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

from ..config import CORP_MAP_FILE, DATA_DIR, FILING_INTEL_DIR, OBSIDIAN_DIR
from ..core.dart_client import dart_get

DIVES_DIR = DATA_DIR / "dives"


# 운용사 backtest 결과 (lifecycle 10년 + A1 exit 기준)
# 출처: docs/STRATEGY_FINDINGS.md
ACTOR_BACKTEST: dict[str, dict[str, Any]] = {
    "베어링자산운용": {"signal": "🟢 강한 매수", "hit15": 49, "mean": 5.1, "n": 70,
                  "aliases": ["베어링", "Barings"],
                  "note": "누적 매수 시 안정, A1 exit 적합"},
    "브이아이피자산운용": {"signal": "🟢 매수", "hit15": 45, "mean": 5.2, "n": 192,
                     "aliases": ["VIP", "브이아이피", "vip"],
                     "note": "최대 표본, 안정 양수"},
    "신영자산운용": {"signal": "🟡 약한 매수", "hit15": 44, "mean": 4.2, "n": 32,
                "aliases": ["신영"],
                "note": "최초 진입만 양수, 누적은 약함"},
    "한국투자밸류자산운용": {"signal": "🟡 약한 매수", "hit15": 44, "mean": 5.6, "n": 27,
                      "aliases": ["한국투자밸류", "한투밸류"],
                      "note": "최초 진입 + A1"},
    "라이프자산운용": {"signal": "🟢 강한 매수 (소표본)", "hit15": 67, "mean": 12.6, "n": 12,
                  "aliases": ["라이프"],
                  "note": "표본 작음, 통계 의의 약함"},
    "안다자산운용": {"signal": "🟢 매수 (소표본)", "hit15": 62, "mean": 12.6, "n": 8,
                "aliases": ["안다"],
                "note": "최초 매수만 강함"},
    "트러스톤자산운용": {"signal": "🟡 보통", "hit15": 35, "mean": 1.8, "n": 48,
                  "aliases": ["트러스톤"],
                  "note": "누적 매수는 averaging down"},
    "에이티넘인베스트": {"signal": "🔴 회피", "hit15": 5, "mean": -10.7, "n": 20,
                   "aliases": ["에이티넘"],
                   "note": "A1 적용해도 -11%"},
}


def match_actor(query: str) -> tuple[str | None, dict | None]:
    """이름 또는 별칭으로 backtest 매칭. (canonical_name, backtest_dict) or (None, None)."""
    if not query:
        return None, None
    q = query.strip()
    for canonical, bt in ACTOR_BACKTEST.items():
        if canonical in q or q in canonical:
            return canonical, bt
        for alias in bt.get("aliases", []):
            if alias in q or q in alias:
                return canonical, bt
    return None, None

# document.xml 매매내역 패턴
_TRADE_RE = re.compile(
    r"(20\d{2}\.\d{2}\.\d{2})\s+(장내매수\(\+\)|장내매도\(-\))\s+의결권있는 주식\s+"
    r"([\d,]+)\s+(-?[\d,]+)\s+([\d,]+)\s+([\d,]+)"
)

# 잠정실적 공정공시 본문 패턴 — "매출액 당해실적 97,455 83,428 16.8 - 79,747 22.2"
# (당기, 전기, 전기대비%, 전년동기, 전년동기대비%)
_PRELIM_LINE_RE = re.compile(
    r"(매출액|영업이익|법인세비용차감전계속사업이익|당기순이익|지배기업 소유주지분 순이익)\s+"
    r"당해실적\s+([\d,]+)\s+([\d,]+)\s+(-?[\d.]+)\s+-?\s*([\d,]+)\s+(-?[\d.]+)"
)
_PRELIM_PERIOD_RE = re.compile(
    r"당기실적\s+(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})"
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


def fetch_treasury_shares(corp_code: str, shares_outstanding: int = 0) -> tuple[int, int, int, int, float | None, str]:
    """자기주식 취득·처분 현황 (정기보고서 기준).

    DART endpoint: tesstkAcqsDspsSttus.json
    가장 최근 정기보고서 (사업/분기/반기) 의 *보통주 장내직접취득 + 소계* trmend_qy 합산.

    Args:
        corp_code: DART corp_code
        shares_outstanding: 발행주식수 (없으면 0)

    Returns:
        (보유 자기주식 수, 기초, 취득, 처분, 비율%, label)
    """
    now_year = datetime.now().year
    for y in (now_year, now_year - 1, now_year - 2):
        for reprt in ("11011", "11014", "11012", "11013"):
            j = dart_get("tesstkAcqsDspsSttus.json", {
                "corp_code": corp_code, "bsns_year": str(y), "reprt_code": reprt,
            })
            if not j or j.get("status") != "000":
                continue
            tesstk = bsis = acqs = dsps = 0
            for it in j.get("list", []):
                if it.get("stock_knd") != "보통주":
                    continue
                # 장내직접취득 + 소계 + 신탁수탁자보유물량 모두 합산
                mth3 = it.get("acqs_mth3", "")
                if mth3 not in ("장내직접취득", "장외직접취득", "공개매수", "수탁자보유물량", "현물보유량", "소계", "총계"):
                    continue
                # 소계만 가져오면 두 번 카운트 방지
                if mth3 != "소계" and mth3 != "총계":
                    continue
                # 우리는 소계 또는 총계 한 번만
                try:
                    bsis_q = it.get("bsis_qy", "0").replace(",", "")
                    aq_q = it.get("change_qy_acqs", "0").replace(",", "")
                    ds_q = it.get("change_qy_dsps", "0").replace(",", "")
                    tm_q = it.get("trmend_qy", "0").replace(",", "")
                    if bsis_q == "-": bsis_q = "0"
                    if aq_q == "-": aq_q = "0"
                    if ds_q == "-": ds_q = "0"
                    if tm_q == "-": tm_q = "0"
                    bsis += int(bsis_q)
                    acqs += int(aq_q)
                    dsps += int(ds_q)
                    tesstk = max(tesstk, int(tm_q))
                except (ValueError, TypeError):
                    continue
            if tesstk > 0 or acqs > 0 or dsps > 0:
                pct = (tesstk / shares_outstanding * 100) if shares_outstanding else None
                label = {"11011": "사업보고서", "11014": "3Q", "11012": "반기", "11013": "1Q"}.get(reprt, "?")
                return tesstk, bsis, acqs, dsps, pct, f"{y} {label}"
    return 0, 0, 0, 0, None, ""


def fetch_document_text(rcept_no: str) -> str:
    """document.xml ZIP 다운로드 + 본문 텍스트 추출."""
    from ..core.dart_client import dart_fetch_zip
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


def parse_prelim_body(text: str) -> dict:
    """잠정실적 공시 본문 파싱.

    Returns:
        {
          "period_start": "2026-01-01", "period_end": "2026-03-31",
          "rows": {
            "매출액": {"this": 97455e6, "prev_q": 83428e6, "prev_q_pct": 16.8,
                      "yoy": 79747e6, "yoy_pct": 22.2},
            ...
          }
        }
    """
    if not text:
        return {}
    flat = re.sub(r"<[^>]+>", " ", text)
    flat = re.sub(r"\s+", " ", flat)
    out = {"rows": {}, "period_start": "", "period_end": ""}
    m = _PRELIM_PERIOD_RE.search(flat)
    if m:
        out["period_start"], out["period_end"] = m.group(1), m.group(2)
    for m in _PRELIM_LINE_RE.finditer(flat):
        nm, this_v, prev_q, prev_q_pct, yoy, yoy_pct = m.groups()
        try:
            out["rows"][nm] = {
                # 잠정실적 단위는 백만원 — 억원으로 환산
                "this": int(this_v.replace(",", "")) / 100,
                "prev_q": int(prev_q.replace(",", "")) / 100,
                "prev_q_pct": float(prev_q_pct),
                "yoy": int(yoy.replace(",", "")) / 100,
                "yoy_pct": float(yoy_pct),
            }
        except (ValueError, TypeError):
            continue
    return out


def fetch_prelim_with_data(corp_code: str, days: int = 365) -> tuple[dict | None, dict]:
    """가장 최근 잠정실적 공시 + 본문 파싱.

    Returns: (meta dict, parsed body)
    """
    prelims = fetch_prelim_disclosures(corp_code, days)
    if not prelims:
        return None, {}
    # 가장 최근
    latest = max(prelims, key=lambda x: x.get("rcept_dt", ""))
    rcept_no = latest.get("rcept_no", "")
    if not rcept_no:
        return latest, {}
    text = fetch_document_text(rcept_no)
    body = parse_prelim_body(text)
    return latest, body


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


def gather_dive_data(stock_code: str, *, verbose: bool = True) -> dict:
    """dive 모든 데이터 수집 → dict (render 없이).

    rank·다른 자동화에서 *정량 점수 계산* 용.
    """
    cm = _load_corp_map()
    info = cm.get(stock_code)
    if not info:
        return {"error": f"stock_code {stock_code} 매핑 실패"}
    corp_code = info["corp_code"]
    corp_name = info["corp_name"]

    def log(msg):
        if verbose: print(msg)

    log(f"[1/6] DART 회사 개요 ...")
    company = fetch_company(corp_code)
    log(f"  ✓ {company.get('corp_name','?')} (CEO {company.get('ceo_nm','?')})")

    log(f"[2/6] 최신 재무 (사업보고서 + 분기) ...")
    annual_year, annual_rows = latest_annual_financials(corp_code)
    annual = parse_financials(annual_rows) if annual_rows else {}
    log(f"  ✓ 사업보고서 {annual_year}: {len(annual)} 계정")

    q_year, q_reprt, q_rows = latest_quarterly_financials(corp_code)
    quarterly = parse_financials(q_rows) if q_rows else {}
    q_label = {"11013": "1Q", "11012": "반기", "11014": "3Q"}.get(q_reprt or "", "?")
    log(f"  ✓ 분기보고서 {q_year} {q_label}: {len(quarterly)} 계정")

    log(f"[3/6] 잠정실적 공시 + 본문 파싱 ...")
    prelim_meta, prelim_body = fetch_prelim_with_data(corp_code, 365)
    prelims = fetch_prelim_disclosures(corp_code, 365)
    n_prelim_rows = len(prelim_body.get("rows", {}))
    log(f"  ✓ 공시 {len(prelims)} 건, 본문 {n_prelim_rows} 계정 추출")

    log(f"[4/6] 5%+ 신고 + 매매내역 파싱 ...")
    majorstock = fetch_majorstock(corp_code)
    log(f"  · 총 {len(majorstock)} 건. 본문 파싱 중...")
    actor_data = analyze_actor_trades(majorstock)
    log(f"  ✓ 보고자 {len(actor_data)} 명, 매매내역 추출 완료")

    log(f"[5/6] yfinance 가격 + 시총 + 자기주식 ...")
    cur_price, suffix, price_info = yfinance_price(stock_code)
    shares = estimate_shares_outstanding(majorstock)
    market_cap = cur_price * shares / 1e8 if cur_price and shares else 0
    tes_qty, tes_bsis, tes_acqs, tes_dsps, tes_pct, tes_label = fetch_treasury_shares(corp_code, shares)
    log(f"  ✓ 현재가 {cur_price:,.0f}원, 시총 {market_cap:,.0f}억원")
    if tes_qty > 0:
        pct_str = f"{tes_pct:.1f}%" if tes_pct is not None else "?%"
        log(f"  · 자기주식 보유 {tes_qty:,}주 ({pct_str}, {tes_label}) — 직전 취득 {tes_acqs:,}주")

    return {
        "stock_code": stock_code, "corp_name": corp_name, "corp_code": corp_code,
        "company": company,
        "annual_year": annual_year, "annual": annual,
        "q_year": q_year, "q_label": q_label, "quarterly": quarterly,
        "prelim_meta": prelim_meta, "prelim_body": prelim_body, "prelims": prelims,
        "majorstock": majorstock, "actor_data": actor_data,
        "cur_price": cur_price, "suffix": suffix, "price_info": price_info,
        "shares": shares, "market_cap": market_cap,
        "tes_qty": tes_qty, "tes_bsis": tes_bsis, "tes_acqs": tes_acqs,
        "tes_dsps": tes_dsps, "tes_pct": tes_pct, "tes_label": tes_label,
    }


def render_dive(stock_code: str) -> str:
    """전체 dive 보고서 Markdown 렌더."""
    d = gather_dive_data(stock_code)
    if "error" in d:
        return f"⚠️ {d['error']}"

    print(f"[6/6] 보고서 생성 ...")
    return _build_markdown(**d)


def _build_markdown(*, stock_code, corp_name, corp_code, company, annual_year, annual,
                    q_year, q_label, quarterly, prelims, prelim_meta, prelim_body,
                    majorstock, actor_data,
                    cur_price, suffix, price_info, shares, market_cap,
                    tes_qty=0, tes_bsis=0, tes_acqs=0, tes_dsps=0,
                    tes_pct=None, tes_label="") -> str:
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
    # 잠정실적 본문 파싱 결과 우선 (가장 fresh)
    if prelim_body and prelim_body.get("rows"):
        pstart, pend = prelim_body.get("period_start",""), prelim_body.get("period_end","")
        period_label = f"{pstart} ~ {pend}" if pstart else "잠정"
        o.append(f"### 🔥 잠정실적 ({period_label}) — *가장 fresh*")
        o.append("")
        o.append(f"공시일: {prelim_meta.get('rcept_dt','')} · "
                 f"rcept_no `{prelim_meta.get('rcept_no','')}`")
        o.append("")
        o.append("| 항목 | 당기 (억원) | 전년동기 (억원) | YoY |")
        o.append("|---|---:|---:|---:|")
        for nm in ["매출액", "영업이익", "당기순이익", "지배기업 소유주지분 순이익"]:
            if nm in prelim_body["rows"]:
                v = prelim_body["rows"][nm]
                yoy = f"{v['yoy_pct']:+.1f}%"
                o.append(f"| {nm} | {v['this']:,.0f} | {v['yoy']:,.0f} | **{yoy}** |")
        o.append("")
    elif quarterly:
        o.append(f"### 분기 ({q_year} {q_label} 보고서) — *정식 분기 데이터*")
        o.append("")
        o.append("| 항목 | 당기 (억원) | 전기 (억원) | YoY |")
        o.append("|---|---:|---:|---:|")
        for nm in ["매출액", "영업이익", "당기순이익", "자산총계", "자본총계"]:
            if nm in quarterly:
                v = quarterly[nm]
                yoy = f"{(v['thstrm']/v['frmtrm']-1)*100:+.1f}%" if v['frmtrm'] else "-"
                o.append(f"| {nm} | {v['thstrm']:,.0f} | {v['frmtrm']:,.0f} | {yoy} |")
        o.append("")

    # §4 잠정실적 공시 목록 (히스토리)
    if prelims:
        o.append("## §4. 잠정실적 공시 목록 (최근 1년)")
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
        # NAV 할인 PBR — 자기주식 차감 (유통주식 기준)
        if tes_qty and cur_price and shares:
            tes_value = tes_qty * cur_price / 1e8  # 억원
            adjusted_cap = cap_won - tes_value
            float_shares = shares - tes_qty
            float_mc = cur_price * float_shares / 1e8
            adj_pbr = float_mc / adjusted_cap if adjusted_cap > 0 else 0
            pct_str = f"{tes_pct:.1f}%" if tes_pct is not None else "?"
            o.append(f"- **NAV 조정 PBR** = 유통시총 {float_mc:,.0f}억 / (자본 - 자기주식가치 {tes_value:,.0f}억) = **{adj_pbr:.2f}배**")
            o.append(f"  - *자기주식 {tes_qty:,}주 ({pct_str}) 차감 효과*")

    # PER 1: 연간 사업보고서 (LTM)
    if annual and "당기순이익" in annual and shares and cur_price:
        ni = annual["당기순이익"]["thstrm"] * 1e8  # 원
        if ni > 0:
            eps = ni / shares
            per = cur_price / eps if eps else 0
            o.append(f"- **PER (연간 사업보고서 {annual_year})** = {cur_price:,.0f}원 / EPS {eps:,.0f}원 = **{per:.1f}배**")

    # PER 2: 잠정실적 분기 연환산 (가장 fresh)
    if prelim_body and prelim_body.get("rows") and shares and cur_price:
        rows = prelim_body["rows"]
        # 누계 분기 수 추론 (period_end 의 월)
        period_end = prelim_body.get("period_end", "")
        try:
            end_month = int(period_end[5:7]) if period_end else 3
        except (ValueError, TypeError):
            end_month = 3
        n_quarters = max(1, end_month // 3)
        # 지배지분 우선, 없으면 당기순이익
        if "지배기업 소유주지분 순이익" in rows:
            ni_q = rows["지배기업 소유주지분 순이익"]["this"] * 1e8
            label = "지배순이익"
        elif "당기순이익" in rows:
            ni_q = rows["당기순이익"]["this"] * 1e8
            label = "당기순이익"
        else:
            ni_q = 0
            label = ""
        if ni_q > 0:
            # 누계 기반 연환산: cum / n_quarters × 4
            # 1Q (n=1): this × 4 와 동일
            # 반기 (n=2): cum × 2
            # 3Q (n=3): cum × 4/3
            annualized = ni_q * (4 / n_quarters)
            eps_q = annualized / shares
            per_q = cur_price / eps_q if eps_q else 0
            period = period_end[:7]
            quarters_label = {1: "1Q", 2: "반기", 3: "3Q", 4: "연간"}.get(n_quarters, f"{n_quarters}Q")
            o.append(f"- **PER (잠정 {period}, {quarters_label} {label} → 연환산)** = "
                     f"{cur_price:,.0f}원 / EPS {eps_q:,.0f}원 = **{per_q:.1f}배** ⭐ *가장 fresh*")
            if n_quarters > 1:
                o.append(f"  - *누계 {ni_q/1e8:,.0f}억 ÷ {n_quarters}분기 × 4 = 연환산 {annualized/1e8:,.0f}억*")

    # PER 3: 정식 분기 (잠정 없을 때만)
    if (not prelim_body or not prelim_body.get("rows")) and quarterly and "당기순이익" in quarterly and shares and cur_price:
        ni_q = quarterly["당기순이익"]["thstrm"] * 1e8
        if ni_q > 0:
            annualized = ni_q * 4
            eps_q = annualized / shares
            per_q = cur_price / eps_q if eps_q else 0
            o.append(f"- **PER ({q_year} {q_label} ×4 연환산)** = {cur_price:,.0f}원 / EPS {eps_q:,.0f}원 = **{per_q:.1f}배**")
    o.append("")

    # §5.5 자기주식 catalyst
    if tes_qty > 0 or tes_acqs > 0:
        o.append("## §5.5 자기주식 catalyst")
        o.append("")
        pct_str = f"**{tes_pct:.1f}%**" if tes_pct is not None else "?"
        o.append(f"- 자기주식 보유: **{tes_qty:,}주** ({pct_str}) — *{tes_label}* 기준")
        if tes_pct is not None:
            if tes_pct >= 5:
                o.append(f"  - 🟢 *5% 이상* — *소각 / 주주환원* catalyst 잠재력 **강함**")
            elif tes_pct >= 2:
                o.append(f"  - 🟡 *2~5%* — 소각·배당 확대 가능")
            else:
                o.append(f"  - ⚪ 1~2% — 작은 규모")
        if tes_bsis or tes_acqs or tes_dsps:
            o.append("")
            o.append(f"### 직전 회계연도 변동")
            o.append("")
            o.append(f"| 기초 | 취득 | 처분 | 기말 |")
            o.append(f"|---:|---:|---:|---:|")
            o.append(f"| {tes_bsis:,} | **+{tes_acqs:,}** | -{tes_dsps:,} | {tes_qty:,} |")
            if tes_acqs > 0:
                o.append("")
                o.append(f"→ 🟢 *직전 연도 {tes_acqs:,}주 매입* — *주주환원 catalyst* 진행 중")
            if tes_dsps > 0:
                o.append("")
                o.append(f"→ 🔴 *직전 연도 {tes_dsps:,}주 처분* — *EPS 희석 risk*")
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

        # 가장 큰 buyer 의 매매 흐름 — 전체 통계 + 패턴 분석 + 전체 timeline
        if rows:
            top_actor, top_d = rows[0]
            buys = [t for t in top_d["trades"] if t["kind"] == "buy"]
            sells = [t for t in top_d["trades"] if t["kind"] == "sell"]
            buy_qty = sum(t["qty"] for t in buys)
            buy_amt = sum(t["qty"] * t["price"] for t in buys)
            sell_qty = sum(t["qty"] for t in sells)
            sell_amt = sum(t["qty"] * t["price"] for t in sells)
            net_qty = buy_qty - sell_qty
            net_amt = buy_amt - sell_amt
            buy_avg = buy_amt / buy_qty if buy_qty else 0
            sell_avg = sell_amt / sell_qty if sell_qty else 0
            net_avg = net_amt / net_qty if net_qty else 0
            first_date = top_d["trades"][0]["date"] if top_d["trades"] else ""
            last_date = top_d["trades"][-1]["date"] if top_d["trades"] else ""
            recent_price = top_d["trades"][-1]["price"] if top_d["trades"] else 0

            o.append(f"### ⭐ Top buyer: **{top_actor}** 매매 통계")
            o.append("")
            o.append(f"**기간**: {first_date} ~ {last_date} ({len(top_d['trades'])}건)")
            o.append("")
            o.append("| 항목 | 횟수 | 총 수량 | 가중평균 단가 |")
            o.append("|---|---:|---:|---:|")
            o.append(f"| **매수** | {len(buys)} | **{buy_qty:,}주** | **{buy_avg:,.0f}원** |")
            o.append(f"| **매도** | {len(sells)} | {sell_qty:,}주 | {sell_avg:,.0f}원 |")
            o.append(f"| **순매집** | — | **{net_qty:,}주** | **{net_avg:,.0f}원** (실효) |")
            o.append("")
            o.append(f"- 순투자금: **{net_amt:,}원** (약 {net_amt/1e8:.1f}억)")
            if cur_price > 0:
                o.append("")
                o.append("**현재가 (oa) 대비 비교:**")
                o.append("")
                o.append("| 기준 | 가격 | 현재가 차이 |")
                o.append("|---|---:|---:|")
                if buy_avg:
                    o.append(f"| 매수 가중평균 | {buy_avg:,.0f}원 | {(cur_price/buy_avg-1)*100:+.1f}% |")
                if sell_avg:
                    o.append(f"| 매도 가중평균 | {sell_avg:,.0f}원 | {(cur_price/sell_avg-1)*100:+.1f}% |")
                if net_avg:
                    o.append(f"| 순매집 실효단가 | {net_avg:,.0f}원 | {(cur_price/net_avg-1)*100:+.1f}% |")
                if recent_price:
                    o.append(f"| 가장 최근 매수 | {recent_price:,}원 | {(cur_price/recent_price-1)*100:+.1f}% |")
                o.append(f"| **현재가** | **{cur_price:,.0f}원** | — |")
            o.append("")

            o.append(f"### {top_actor} 매매 timeline (전체 {len(top_d['trades'])}건)")
            o.append("")
            o.append("| 일자 | 종류 | 수량 | 단가 |")
            o.append("|---|---|---:|---:|")
            for t in top_d["trades"]:
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


def save_dive(stock_code: str, *, mirror_obsidian: bool = True) -> Path:
    """data/dives/<YYYY-MM-DD>/<code>_<name>.md 저장 + Obsidian 미러 + 인덱스."""
    md = render_dive(stock_code)
    cm = _load_corp_map()
    corp_name = (cm.get(stock_code) or {}).get("corp_name", stock_code)
    name_safe = corp_name.replace(" ", "_").replace("/", "_")
    today_iso = datetime.now().strftime("%Y-%m-%d")
    fname = f"{stock_code}_{name_safe}.md"

    # 1) 로컬 data/dives/
    day_dir = DIVES_DIR / today_iso
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / fname
    path.write_text(md, encoding="utf-8")
    _update_dives_index(DIVES_DIR)

    # 2) Obsidian dives/
    if mirror_obsidian:
        obs_dives = OBSIDIAN_DIR / "dives"
        obs_day = obs_dives / today_iso
        obs_day.mkdir(parents=True, exist_ok=True)
        (obs_day / fname).write_text(md, encoding="utf-8")
        _update_dives_index(obs_dives)

    return path


def _update_dives_index(dives_dir: Path) -> None:
    """data/dives/_index.md — 모든 dive 인덱스 (날짜별 + 종목별 latest)."""
    if not dives_dir.exists():
        return
    all_dives: dict[str, list[tuple[str, Path]]] = {}
    by_code: dict[str, tuple[str, Path]] = {}
    for day_dir in sorted(dives_dir.iterdir(), reverse=True):
        if not day_dir.is_dir() or day_dir.name.startswith("."):
            continue
        date = day_dir.name
        all_dives[date] = []
        for f in sorted(day_dir.glob("*.md")):
            stem = f.stem
            all_dives[date].append((stem, f))
            code = stem.split("_")[0]
            if code not in by_code:
                by_code[code] = (date, f)

    total = sum(len(v) for v in all_dives.values())
    lines = [
        "# 🔍 5pct-radar Dives — 종목 deep dive 인덱스",
        "",
        f"*Auto-generated. 총 {total}건 누적, {len(by_code)}개 종목.*",
        "",
        "## 종목별 최신 dive",
        "",
        "| 종목 | 최신 일자 | 보고서 |",
        "|---|---|---|",
    ]
    for code, (date, f) in sorted(by_code.items()):
        rel = f.relative_to(dives_dir)
        lines.append(f"| {f.stem} | {date} | [{rel}]({rel}) |")
    lines.extend(["", "## 날짜별 (최신 → 과거)", ""])
    for date, dives in all_dives.items():
        if not dives:
            continue
        lines.append(f"### {date}")
        lines.append("")
        for stem, f in dives:
            rel = f.relative_to(dives_dir)
            lines.append(f"- [{stem}]({rel})")
        lines.append("")
    (dives_dir / "_index.md").write_text("\n".join(lines), encoding="utf-8")
