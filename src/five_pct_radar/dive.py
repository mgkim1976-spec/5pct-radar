"""Standalone deep dive — screening 의존성 없이 종목 코드 1개로 즉시 분석.

  python -m five_pct_radar dive 039830

수집 데이터 (DART + yfinance):
  - 회사 개요 (company.json)
  - 최신 재무 (사업보고서 + 분기보고서 자동 탐색)
  - 잠정실적 공시 (최근 1년)
  - 5%+ 신고 전체 + document.xml 본문 매매 내역 파싱
  - 운용사별 가중평균 매수가 + 순매집 실효단가
  - A1 권장 진입가 (운용사 평균 +5% 이내) / 익절 +20% / 손절 -10%
  - backtest 운용사 라벨 (베어링/VIP/신영 등 follow 시그널 강도)

⚠️ activist-scout 의 enriched/scores 의존성 *완전 제거*. DART API 만 사용.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf

from .config import CORP_MAP_FILE, DATA_DIR, FILING_INTEL_DIR
from .dart_client import dart_get


# 운용사 backtest 결과 (lifecycle 10년 + A1 exit 기준)
# 출처: docs/STRATEGY_FINDINGS.md
ACTOR_BACKTEST: dict[str, dict[str, Any]] = {
    "베어링자산운용": {"signal": "🟢 강한 매수", "hit15": 49, "mean": 5.1, "n": 70,
                  "note": "누적 매수 시 안정, A1 exit 적합"},
    "브이아이피자산운용": {"signal": "🟢 매수", "hit15": 45, "mean": 5.2, "n": 192,
                     "note": "최대 표본, 안정 양수"},
    "신영자산운용": {"signal": "🟡 약한 매수", "hit15": 44, "mean": 4.2, "n": 32,
                "note": "최초 진입만 양수, 누적은 약함"},
    "한국투자밸류자산운용": {"signal": "🟡 약한 매수", "hit15": 44, "mean": 5.6, "n": 27,
                      "note": "최초 진입 + A1"},
    "라이프자산운용": {"signal": "🟢 강한 매수 (소표본)", "hit15": 67, "mean": 12.6, "n": 12,
                  "note": "표본 작음, 통계 의의 약함"},
    "안다자산운용": {"signal": "🟢 매수 (소표본)", "hit15": 62, "mean": 12.6, "n": 8,
                "note": "최초 매수만 강함"},
    "트러스톤자산운용": {"signal": "🟡 보통", "hit15": 35, "mean": 1.8, "n": 48,
                  "note": "누적 매수는 averaging down"},
    "에이티넘인베스트": {"signal": "🔴 회피", "hit15": 5, "mean": -10.7, "n": 20,
                   "note": "A1 적용해도 -11%"},
}

# document.xml 매매내역 패턴
_TRADE_RE = re.compile(
    r"(20\d{2}\.\d{2}\.\d{2})\s+(장내매수\(\+\)|장내매도\(-\))\s+의결권있는 주식\s+"
    r"([\d,]+)\s+(-?[\d,]+)\s+([\d,]+)\s+([\d,]+)"
)


def _load_corp_map() -> dict:
    if not CORP_MAP_FILE.exists():
        raise SystemExit(f"⚠️ {CORP_MAP_FILE} 없음 — `python -m five_pct_radar --build-corp-map` 먼저")
    return json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))


def fetch_company(corp_code: str) -> dict:
    j = dart_get("company.json", {"corp_code": corp_code})
    return j if j and j.get("status") == "000" else {}


def fetch_financials(corp_code: str, bsns_year: int, reprt_code: str) -> list[dict]:
    """fnlttSinglAcnt — 단일회사 주요 재무.

    reprt_code: 11011=사업보고서, 11012=반기, 11013=1Q, 11014=3Q
    """
    j = dart_get("fnlttSinglAcnt.json", {
        "corp_code": corp_code, "bsns_year": str(bsns_year), "reprt_code": reprt_code,
    })
    return list(j.get("list", [])) if j and j.get("status") == "000" else []


def latest_annual_financials(corp_code: str) -> tuple[int | None, list[dict]]:
    """가장 최근 사업보고서 (연간) 자동 탐색. 올해 → 작년 → 재작년."""
    now_year = datetime.now().year
    for y in (now_year, now_year - 1, now_year - 2):
        rows = fetch_financials(corp_code, y, "11011")
        if rows:
            return y, rows
    return None, []


def latest_quarterly_financials(corp_code: str) -> tuple[int | None, str | None, list[dict]]:
    """가장 최근 분기보고서 자동 탐색 (최근 12개월)."""
    now = datetime.now()
    for offset_months in range(0, 12):
        d = now - timedelta(days=offset_months * 30)
        y = d.year
        for reprt in ("11014", "11012", "11013"):  # 3Q, 반기, 1Q
            rows = fetch_financials(corp_code, y, reprt)
            if rows:
                return y, reprt, rows
    return None, None, []


def parse_financials(rows: list[dict]) -> dict[str, dict[str, float]]:
    """{account_nm: {thstrm, frmtrm}} (CFS 우선, OFS fallback).

    fnlttSinglAcnt 는 *주요 5계정* (매출/영업이익/순이익/자산총계/자본총계) 만 반환.
    부채총계는 자동으로 자산 - 자본 으로 계산해서 추가.
    """
    out: dict[str, dict[str, float]] = {}
    targets = {"매출액", "영업이익", "당기순이익", "자산총계", "자본총계", "부채총계"}
    # CFS 우선
    for fs in ("CFS", "OFS"):
        for it in rows:
            nm = it.get("account_nm", "")
            if nm not in targets or it.get("fs_div", "") != fs:
                continue
            if nm in out:
                continue
            try:
                th = int(it.get("thstrm_amount", "0").replace(",", "")) / 1e8
                fr = it.get("frmtrm_amount", "0").replace(",", "")
                fr = int(fr) / 1e8 if fr and fr != "-" else 0
                out[nm] = {"thstrm": th, "frmtrm": fr}
            except (ValueError, TypeError):
                pass
    # 부채총계 자동 계산
    if "부채총계" not in out and "자산총계" in out and "자본총계" in out:
        a = out["자산총계"]; c = out["자본총계"]
        out["부채총계"] = {
            "thstrm": a["thstrm"] - c["thstrm"],
            "frmtrm": a["frmtrm"] - c["frmtrm"] if a["frmtrm"] and c["frmtrm"] else 0,
        }
    return out


def fetch_prelim_disclosures(corp_code: str, days: int = 365) -> list[dict]:
    """잠정실적 공시 — 전체 공시 중 '잠정' 키워드 포함."""
    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    j = dart_get("list.json", {
        "corp_code": corp_code, "bgn_de": bgn, "end_de": end,
        "page_count": 100,
    })
    if not j or j.get("status") != "000":
        return []
    return [it for it in j.get("list", []) if "잠정" in it.get("report_nm", "")]


def fetch_majorstock(corp_code: str) -> list[dict]:
    j = dart_get("majorstock.json", {"corp_code": corp_code})
    return list(j.get("list", [])) if j and j.get("status") == "000" else []


def fetch_document_text(rcept_no: str) -> str:
    """document.xml ZIP 다운로드 + 본문 텍스트 추출."""
    from .dart_client import dart_fetch_zip
    zf = dart_fetch_zip("document.xml", {"rcept_no": rcept_no})
    if not zf:
        return ""
    try:
        biggest = max(zf.namelist(), key=lambda n: zf.getinfo(n).file_size)
        raw = zf.read(biggest)
    finally:
        zf.close()
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            if "주식등의" in text or "취득" in text or "매수" in text:
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def parse_trades(text: str) -> list[dict]:
    """document.xml 본문에서 매매 내역 추출. → [{date, kind, qty, price}, ...]"""
    if not text:
        return []
    flat = re.sub(r"<[^>]+>", " ", text)
    flat = re.sub(r"\s+", " ", flat)
    trades = []
    for m in _TRADE_RE.finditer(flat):
        date, kind, _before, change, _after, price = m.groups()
        try:
            qty = int(change.replace(",", "").lstrip("-"))
            price_n = int(price.replace(",", ""))
            trades.append({
                "date": date, "kind": "buy" if "매수" in kind else "sell",
                "qty": qty, "price": price_n,
            })
        except ValueError:
            continue
    return trades


def analyze_actor_trades(majorstock: list[dict]) -> dict[str, dict]:
    """보고자별 모든 신고에서 매매 단가 추출 + 가중평균 계산.

    Returns: {actor_name: {trades, buy_avg, sell_avg, net_avg, total_buy_qty, ...}}
    """
    actor_trades: dict[str, list[dict]] = defaultdict(list)
    for ms in majorstock:
        repror = (ms.get("repror") or "").strip()
        if not repror:
            continue
        rcept_no = ms.get("rcept_no", "")
        if not rcept_no:
            continue
        text = fetch_document_text(rcept_no)
        trades = parse_trades(text)
        actor_trades[repror].extend(trades)

    out = {}
    for actor, trades in actor_trades.items():
        if not trades:
            continue
        buy = [t for t in trades if t["kind"] == "buy"]
        sell = [t for t in trades if t["kind"] == "sell"]
        buy_qty = sum(t["qty"] for t in buy)
        buy_amt = sum(t["qty"] * t["price"] for t in buy)
        sell_qty = sum(t["qty"] for t in sell)
        sell_amt = sum(t["qty"] * t["price"] for t in sell)
        net_qty = buy_qty - sell_qty
        net_amt = buy_amt - sell_amt
        out[actor] = {
            "n_buys": len(buy), "n_sells": len(sell),
            "buy_qty": buy_qty, "buy_avg": buy_amt / buy_qty if buy_qty else 0,
            "sell_qty": sell_qty, "sell_avg": sell_amt / sell_qty if sell_qty else 0,
            "net_qty": net_qty, "net_avg": net_amt / net_qty if net_qty else 0,
            "last_trade_date": max((t["date"] for t in trades), default=""),
            "trades": sorted(trades, key=lambda t: t["date"]),
        }
    return out


def yfinance_price(stock_code: str) -> tuple[float, str, dict]:
    """현재가 + suffix (.KS/.KQ) + 6개월 가격 흐름."""
    for suffix in (".KS", ".KQ"):
        try:
            t = yf.Ticker(stock_code + suffix)
            h = t.history(period="6mo")
            if len(h) > 0:
                cur = float(h["Close"].iloc[-1])
                first = float(h["Close"].iloc[0])
                return cur, suffix, {
                    "current": cur, "6mo_ago": first,
                    "6mo_return_pct": (cur / first - 1) * 100,
                    "52w_high": float(h["Close"].max()),
                    "52w_low": float(h["Close"].min()),
                }
        except Exception:
            continue
    return 0.0, "", {}


def estimate_shares_outstanding(majorstock: list[dict]) -> int:
    """majorstock 의 stkqy/stkrt 로 발행주식수 역산 (가장 최근 신고)."""
    for ms in sorted(majorstock, key=lambda x: x.get("rcept_dt", ""), reverse=True):
        try:
            qty = int(ms.get("stkqy", "0").replace(",", ""))
            rt = float(ms.get("stkrt", "0"))
            if qty and rt > 0:
                return int(qty / (rt / 100))
        except (ValueError, TypeError):
            continue
    return 0


def render_dive(stock_code: str) -> str:
    """전체 dive 보고서 Markdown 렌더."""
    cm = _load_corp_map()
    info = cm.get(stock_code)
    if not info:
        return f"⚠️ stock_code {stock_code} 매핑 실패 — `--build-corp-map` 먼저"
    corp_code = info["corp_code"]
    corp_name = info["corp_name"]

    print(f"[1/6] DART 회사 개요 ...")
    company = fetch_company(corp_code)
    print(f"  ✓ {company.get('corp_name','?')} (CEO {company.get('ceo_nm','?')})")

    print(f"[2/6] 최신 재무 (사업보고서 + 분기) ...")
    annual_year, annual_rows = latest_annual_financials(corp_code)
    annual = parse_financials(annual_rows) if annual_rows else {}
    print(f"  ✓ 사업보고서 {annual_year}: {len(annual)} 계정")

    q_year, q_reprt, q_rows = latest_quarterly_financials(corp_code)
    quarterly = parse_financials(q_rows) if q_rows else {}
    q_label = {"11013": "1Q", "11012": "반기", "11014": "3Q"}.get(q_reprt or "", "?")
    print(f"  ✓ 분기보고서 {q_year} {q_label}: {len(quarterly)} 계정")

    print(f"[3/6] 잠정실적 공시 (최근 1년) ...")
    prelims = fetch_prelim_disclosures(corp_code, 365)
    print(f"  ✓ {len(prelims)} 건")

    print(f"[4/6] 5%+ 신고 + 매매내역 파싱 ...")
    majorstock = fetch_majorstock(corp_code)
    print(f"  · 총 {len(majorstock)} 건. 본문 파싱 중...")
    actor_data = analyze_actor_trades(majorstock)
    print(f"  ✓ 보고자 {len(actor_data)} 명, 매매내역 추출 완료")

    print(f"[5/6] yfinance 가격 + 시총 ...")
    cur_price, suffix, price_info = yfinance_price(stock_code)
    shares = estimate_shares_outstanding(majorstock)
    market_cap = cur_price * shares / 1e8 if cur_price and shares else 0
    print(f"  ✓ 현재가 {cur_price:,.0f}원, 시총 {market_cap:,.0f}억원")

    print(f"[6/6] 보고서 생성 ...")
    md = _build_markdown(
        stock_code=stock_code, corp_name=corp_name, corp_code=corp_code,
        company=company, annual_year=annual_year, annual=annual,
        q_year=q_year, q_label=q_label, quarterly=quarterly,
        prelims=prelims, majorstock=majorstock, actor_data=actor_data,
        cur_price=cur_price, suffix=suffix, price_info=price_info,
        shares=shares, market_cap=market_cap,
    )
    return md


def _build_markdown(*, stock_code, corp_name, corp_code, company, annual_year, annual,
                    q_year, q_label, quarterly, prelims, majorstock, actor_data,
                    cur_price, suffix, price_info, shares, market_cap) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    o: list[str] = []
    o.append(f"# 📋 {corp_name} ({stock_code}) — Dive 보고서")
    o.append("")
    o.append(f"*Generated: {now} · 5pct-radar dive · DART + yfinance*")
    o.append("")
    o.append("> ⚠️ **자동 분석.** §13 사람 검증 필수. 과거 데이터 기반 — 미래 보장 없음.")
    o.append("")

    # §1 회사 개요
    o.append("## §1. 회사 개요")
    o.append("")
    o.append("| 항목 | 값 |")
    o.append("|---|---|")
    o.append(f"| 회사명 | {company.get('corp_name', corp_name)} |")
    o.append(f"| CEO | {company.get('ceo_nm','?')} |")
    o.append(f"| 설립일 | {company.get('est_dt','?')} |")
    o.append(f"| 시장 | {company.get('corp_cls','?')} {'(KOSDAQ)' if company.get('corp_cls')=='K' else '(KOSPI)' if company.get('corp_cls')=='Y' else ''} |")
    o.append(f"| 업종코드 | {company.get('induty_code','?')} |")
    o.append(f"| 종목코드 | {stock_code}{suffix} |")
    o.append(f"| DART corp_code | {corp_code} |")
    if shares:
        o.append(f"| 발행주식수 (추정) | {shares:,}주 |")
    o.append("")

    # §2 가격 + 시총
    o.append("## §2. 가격 + 시가총액")
    o.append("")
    if cur_price:
        o.append("| 항목 | 값 |")
        o.append("|---|---:|")
        o.append(f"| 현재가 | **{cur_price:,.0f}원** |")
        o.append(f"| 6개월 전 | {price_info.get('6mo_ago',0):,.0f}원 |")
        o.append(f"| 6개월 수익률 | **{price_info.get('6mo_return_pct',0):+.1f}%** |")
        o.append(f"| 52주 high / low | {price_info.get('52w_high',0):,.0f} / {price_info.get('52w_low',0):,.0f}원 |")
        o.append(f"| 시가총액 | **{market_cap:,.0f}억원** |")
    else:
        o.append("*(yfinance 가격 조회 실패)*")
    o.append("")

    # §3 최신 재무
    o.append(f"## §3. 최신 재무")
    o.append("")
    if annual:
        o.append(f"### 연간 ({annual_year} 사업보고서)")
        o.append("")
        o.append("| 항목 | 당기 (억원) | 전기 (억원) | YoY |")
        o.append("|---|---:|---:|---:|")
        for nm in ["매출액", "영업이익", "당기순이익", "자산총계", "자본총계", "부채총계"]:
            if nm in annual:
                v = annual[nm]
                yoy = f"{(v['thstrm']/v['frmtrm']-1)*100:+.1f}%" if v['frmtrm'] else "-"
                o.append(f"| {nm} | {v['thstrm']:,.0f} | {v['frmtrm']:,.0f} | {yoy} |")
        if "자본총계" in annual and "부채총계" in annual:
            dr = annual["부채총계"]["thstrm"] / annual["자본총계"]["thstrm"] * 100 if annual["자본총계"]["thstrm"] else 0
            o.append(f"| **부채비율** | **{dr:.0f}%** | | |")
    o.append("")
    if quarterly:
        o.append(f"### 분기 ({q_year} {q_label} 보고서)")
        o.append("")
        o.append("| 항목 | 당기 (억원) | 전기 (억원) | YoY |")
        o.append("|---|---:|---:|---:|")
        for nm in ["매출액", "영업이익", "당기순이익", "자산총계", "자본총계"]:
            if nm in quarterly:
                v = quarterly[nm]
                yoy = f"{(v['thstrm']/v['frmtrm']-1)*100:+.1f}%" if v['frmtrm'] else "-"
                o.append(f"| {nm} | {v['thstrm']:,.0f} | {v['frmtrm']:,.0f} | {yoy} |")
    o.append("")

    # §4 잠정실적 공시
    if prelims:
        o.append("## §4. 잠정실적 공시 (최근 1년)")
        o.append("")
        o.append("| 공시일 | 보고서명 | rcept_no |")
        o.append("|---|---|---|")
        for p in prelims[:5]:
            nm = p.get("report_nm", "")[:50]
            o.append(f"| {p.get('rcept_dt','')} | {nm} | {p.get('rcept_no','')} |")
        o.append("")

    # §5 밸류에이션
    o.append("## §5. 밸류에이션")
    o.append("")
    if annual and "자본총계" in annual and market_cap:
        cap_won = annual["자본총계"]["thstrm"]  # 억원
        pbr = market_cap / cap_won if cap_won else 0
        o.append(f"- **PBR** = 시총 {market_cap:,.0f}억 / 자본 {cap_won:,.0f}억 = **{pbr:.2f}배**")
    if annual and "당기순이익" in annual and shares and cur_price:
        ni = annual["당기순이익"]["thstrm"] * 1e8  # 원
        eps = ni / shares
        per = cur_price / eps if eps else 0
        o.append(f"- **PER (연간 기준)** = {cur_price:,.0f}원 / EPS {eps:,.0f}원 = **{per:.1f}배**")
    if quarterly and "당기순이익" in quarterly and shares and cur_price:
        # 분기 연환산
        ni_q = quarterly["당기순이익"]["thstrm"] * 1e8
        annualized = ni_q * 4  # 단순 ×4 (분기 가중치 무시)
        eps_q = annualized / shares
        per_q = cur_price / eps_q if eps_q else 0
        o.append(f"- **PER (분기 연환산)** = {cur_price:,.0f}원 / EPS {eps_q:,.0f}원 = **{per_q:.1f}배** *(분기 ×4 단순 연환산)*")
    o.append("")

    # §6 운용사 매매 내역 + 가중평균 단가
    o.append("## §6. 5%+ 신고 운용사 매매 분석")
    o.append("")
    o.append(f"총 {len(majorstock)}건 신고, {len(actor_data)}명 보고자.")
    o.append("")
    if actor_data:
        # 매매내역 있는 actor 만 (전체 매매내역 수 ≥ 1)
        rows = [(a, d) for a, d in actor_data.items() if d["n_buys"] + d["n_sells"] > 0]
        rows.sort(key=lambda r: -r[1]["buy_qty"])
        o.append("| 보고자 | n_buys | 매수평균 | n_sells | 매도평균 | 순매집 단가 | backtest 시그널 |")
        o.append("|---|---:|---:|---:|---:|---:|---|")
        for actor, d in rows[:10]:
            bt = ACTOR_BACKTEST.get(actor, {})
            sig = bt.get("signal", "—")
            o.append(f"| {actor[:25]} | {d['n_buys']} | {d['buy_avg']:,.0f}원 | "
                     f"{d['n_sells']} | {d['sell_avg']:,.0f}원 | {d['net_avg']:,.0f}원 | {sig} |")
        o.append("")

        # 가장 큰 buyer 의 매매 흐름 timeline
        if rows:
            top_actor, top_d = rows[0]
            o.append(f"### ⭐ Top buyer: **{top_actor}** 매매 timeline")
            o.append("")
            o.append("| 일자 | 종류 | 수량 | 단가 |")
            o.append("|---|---|---:|---:|")
            for t in top_d["trades"][-20:]:
                kind = "🟢 매수" if t["kind"] == "buy" else "🔴 매도"
                o.append(f"| {t['date']} | {kind} | {t['qty']:,}주 | {t['price']:,}원 |")
            o.append("")

            # backtest 코멘트
            bt = ACTOR_BACKTEST.get(top_actor, {})
            if bt:
                o.append(f"#### 🎯 {top_actor} backtest (10년 lifecycle + A1 exit)")
                o.append("")
                o.append(f"- 시그널: **{bt['signal']}**")
                o.append(f"- hit15 (IRR > 15% 적중): **{bt['hit15']}%** (baseline 25%)")
                o.append(f"- mean: **{bt['mean']:+.1f}%**, n = {bt['n']}")
                o.append(f"- 코멘트: {bt['note']}")
                o.append("")

    # §7 A1 권장 진입가
    o.append("## §7. A1 권장 진입가")
    o.append("")
    if cur_price and actor_data:
        rows = [(a, d) for a, d in actor_data.items() if d["net_qty"] > 0]
        rows.sort(key=lambda r: -r[1]["buy_qty"])
        if rows:
            top_actor, top_d = rows[0]
            net_avg = top_d["net_avg"]
            buy_avg = top_d["buy_avg"]
            recent_price = top_d["trades"][-1]["price"] if top_d["trades"] else 0
            o.append(f"기준: **{top_actor}** 매매 분석")
            o.append("")
            o.append("| 기준 | 가격 | 현재가 대비 |")
            o.append("|---|---:|---:|")
            o.append(f"| 매수 가중평균 | {buy_avg:,.0f}원 | {(cur_price/buy_avg-1)*100:+.1f}% |")
            o.append(f"| 순매집 실효단가 | {net_avg:,.0f}원 | {(cur_price/net_avg-1)*100:+.1f}% |")
            if recent_price:
                o.append(f"| 가장 최근 매수 단가 | {recent_price:,}원 | {(cur_price/recent_price-1)*100:+.1f}% |")
            o.append(f"| **현재가** | **{cur_price:,.0f}원** | — |")
            o.append("")
            # 진입 추천
            if cur_price <= net_avg * 1.05:
                o.append(f"✅ **현재가가 운용사 실효단가 +5% 이내** — *follow 진입 합리적*")
            elif cur_price <= net_avg * 1.15:
                o.append(f"🟡 **현재가가 실효단가 +5~15% 위** — *진입 가능, 단 신중*")
            else:
                o.append(f"🔴 **현재가가 실효단가 +15% 위** — *follow 진입 늦음, breakout 대기 권장*")
            o.append("")
            # A1 익절/손절
            o.append(f"**A1 exit 룰 (현재가 {cur_price:,.0f}원 기준):**")
            o.append(f"- 익절 +20%: **{cur_price*1.2:,.0f}원**")
            o.append(f"- 손절 -10%: **{cur_price*0.9:,.0f}원**")
    o.append("")

    # §13 사람 검증
    o.append("## §13. ⚠️ 사람 검증 필수")
    o.append("")
    o.append("자동 분석의 한계. 진입 결정 전 반드시 사람이 확인:")
    o.append("")
    o.append("1. **운용사 신고 *진짜 의도*** — 보유 목적 (단순투자 / 경영참여 / 일반투자)")
    o.append("2. **최대주주 *특수관계인 합산* 지분** — 행동주의 영향력 평가")
    o.append("3. **부채 구조** — 단기/장기, 이자비용, 만기 분포")
    o.append("4. **잠정실적의 *지속 가능성*** — 일시적 효과 vs 구조적 개선")
    o.append("5. **운용사 *4/29 폭매수* 같은 핵심 트레이드** — 실적 발표 timing 일치 여부")
    o.append("6. **해외법인·환율 노출**")
    o.append("7. **자기주식 매입 결정 / 배당 정책** — 주주환원 catalyst")
    o.append("")
    o.append("---")
    o.append("")
    o.append("*본 보고서는 자동 데이터 분석. 한국 상법 개정 regime change — 어떤 확률·calibration 도 주장하지 않음. 투자 권유 아님. DISCLAIMER.md 참조.*")

    return "\n".join(o)


def save_dive(stock_code: str) -> Path:
    md = render_dive(stock_code)
    FILING_INTEL_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    path = FILING_INTEL_DIR / f"dive_{stock_code}_{today_str}.md"
    path.write_text(md, encoding="utf-8")
    return path
