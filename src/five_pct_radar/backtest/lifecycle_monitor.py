"""운용사 × 종목 *full cycle* (매집 → 보유 → 매도 → 철수) 추적 + realized alpha.

핵심 질문:
  *"이 운용사 따라서 매집 시작 시점에 사고, 철수 시점에 팔면 실제로 얼마 벌었나?"*

가격 데이터: **yfinance** (.KS / .KQ 자동 폴백). KRX 로그인 불필요.
벤치마크: KODEX 200 ETF (069500.KS) — KOSPI 종합 추적, corr > 0.99.

⚠️ 한계:
  - 신고일 종가 = 매도/매수 단가 proxy
  - 5% 미만 도달 후 추가 매도는 신고 의무 없음 → CLOSED 의 실현 단가는 상한 추정
  - yfinance 일부 한국 종목 (특히 상장폐지) 데이터 누락
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import re
import time as _time

from ..backtest.actor_stats import classify_actor, fetch_filings_window, normalize_actor_name
from ..config import CORP_MAP_FILE, FILING_INTEL_DIR
from ..core.fetch_filing import fetch_document_text, list_majorstock


# KOSPI 추적 ETF — KODEX 200. yfinance ticker.
KOSPI_INDEX_YF = "069500.KS"


def _import_yfinance():
    import yfinance as yf  # type: ignore
    return yf


def _i(s: Any) -> int:
    if s is None:
        return 0
    try:
        return int(str(s).replace(",", "").strip())
    except Exception:
        return 0


def _f(s: Any) -> float:
    if s is None:
        return 0.0
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return 0.0


def _date(s: str) -> str:
    return s.replace("-", "")[:8] if s else ""


def filter_meaningful(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """activist / semi / pe + stock_code 있음 + *대량보유* 보고서만.

    "임원ㆍ주요주주특정증권등소유상황보고서" 같은 다른 유형은 제외.
    """
    out = []
    for f in filings:
        flr = f.get("flr_nm", "")
        cat = classify_actor(flr)
        if cat not in ("activist", "semi_activist", "pe_fund"):
            continue
        if not f.get("stock_code"):
            continue
        report_nm = f.get("report_nm", "") or ""
        if "대량보유" not in report_nm:
            continue
        out.append({**f, "_cat": cat})
    return out


def group_by_pair(filings: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """{(actor_norm, stock_code): [filing, filing, ...]} 시간순."""
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in filings:
        actor = normalize_actor_name(f.get("flr_nm", ""))
        sc = f.get("stock_code", "")
        if not actor or not sc:
            continue
        key = (actor, sc)
        by_pair.setdefault(key, []).append(f)
    for k in by_pair:
        by_pair[k].sort(key=lambda x: x.get("rcept_dt", ""))
    return by_pair


_HOLDINGS_RE = re.compile(
    r"보유주식등의 수 및 보유비율 보유주식등의 수 보유비율\s*"
    r"직전 보고서\s*([\d,\-]+)\s*([\d.\-]+)\s*"
    r"이번 보고서\s*([\d,\-]+)\s*([\d.\-]+)"
)


def _parse_count(s: str) -> int:
    if not s or s.strip() == "-":
        return 0
    try:
        return int(s.replace(",", "").strip())
    except Exception:
        return 0


def _parse_pct(s: str) -> float:
    if not s or s.strip() == "-":
        return 0.0
    try:
        return float(s.strip())
    except Exception:
        return 0.0


def parse_holdings_from_doc(text: str) -> dict[str, Any] | None:
    """document.xml 본문에서 직전/이번 보유주식수·비율 추출."""
    m = _HOLDINGS_RE.search(text)
    if not m:
        return None
    return {
        "prior_qty": _parse_count(m.group(1)),
        "prior_pct": _parse_pct(m.group(2)),
        "this_qty": _parse_count(m.group(3)),
        "this_pct": _parse_pct(m.group(4)),
    }


def enrich_with_document_xml(
    filings: list[dict[str, Any]],
    *,
    sleep_sec: float = 0.2,
    checkpoint_path: Path | None = None,
) -> list[dict[str, Any]]:
    """list.json 의 모든 신고에 document.xml fetch + 본문 파싱.

    DART majorstock.json 의 회사당 ~40건 한도 우회.
    각 신고의 *직전/이번 보유주식수* 본문에서 직접 추출.

    파싱 실패 시 (암호화 PDF / 표 형식 다름) 해당 신고 skip.

    checkpoint_path: 매 100건마다 부분 결과 JSON 저장 (hang 시 복원 가능)
    """
    enriched = []
    fail = 0
    # 기존 checkpoint 있으면 resume
    processed_rcept = set()
    if checkpoint_path and checkpoint_path.exists():
        try:
            prev = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            enriched = prev
            processed_rcept = {f.get("rcept_no") for f in prev}
            print(f"   checkpoint resume: {len(enriched)} 이미 처리됨")
        except Exception:
            pass

    for i, f in enumerate(filings, 1):
        rcept_no = f.get("rcept_no", "")
        if rcept_no in processed_rcept:
            continue
        if i % 100 == 0:
            print(f"   document.xml {i}/{len(filings)} (succ {len(enriched)} fail {fail})")
            if checkpoint_path:
                try:
                    checkpoint_path.write_text(
                        json.dumps(enriched, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                except Exception:
                    pass
        if not rcept_no:
            continue
        try:
            text = fetch_document_text(rcept_no)
        except Exception:
            fail += 1
            continue
        if not text:
            fail += 1
            continue
        parsed = parse_holdings_from_doc(text)
        if parsed is None:
            fail += 1
            continue
        f["stkqy"] = parsed["this_qty"]
        f["stkqy_irds"] = parsed["this_qty"] - parsed["prior_qty"]
        f["stkrt"] = round(parsed["this_pct"], 2)
        f["stkrt_irds"] = round(parsed["this_pct"] - parsed["prior_pct"], 2)
        f["repror"] = f.get("flr_nm", "")
        enriched.append(f)
        if sleep_sec:
            _time.sleep(sleep_sec)
    print(f"   document.xml 파싱 완료: {len(enriched)}/{len(filings)} 성공, {fail} 실패")
    # 최종 checkpoint
    if checkpoint_path:
        try:
            checkpoint_path.write_text(
                json.dumps(enriched, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass
    return enriched


def enrich_with_majorstock(
    pairs: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """[LEGACY] majorstock.json 의 회사당 ~40건 한도 때문에 옛 데이터 누락.
    대안: enrich_with_document_xml (5%+ 신고 본문 파싱)."""
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
    stock_cache: dict[str, list[dict[str, Any]]] = {}
    enriched: dict[tuple[str, str], list[dict[str, Any]]] = {}

    unique_stocks = {sc for (_, sc) in pairs}
    print(f"   majorstock fetch — unique stocks: {len(unique_stocks)}")

    for i, sc in enumerate(unique_stocks, 1):
        if i % 100 == 0:
            print(f"   majorstock {i}/{len(unique_stocks)}")
        info = cm.get(sc)
        if not info:
            continue
        stock_cache[sc] = list_majorstock(info["corp_code"])

    for (actor, sc), _list_filings in pairs.items():
        ms = stock_cache.get(sc, [])
        if not ms:
            continue
        matched = [m for m in ms if actor == normalize_actor_name(m.get("repror", ""))]
        if not matched:
            continue
        matched.sort(key=lambda x: x.get("rcept_dt", ""))
        # actor_category 보존 — list.json 결과의 첫 항목에서
        cat = (_list_filings[0] if _list_filings else {}).get("_cat", "?")
        for m in matched:
            m["_cat"] = cat
        enriched[(actor, sc)] = matched

    return enriched


def classify_cycle(pair_filings: list[dict[str, Any]]) -> dict[str, Any]:
    """단일 (actor, stock) lifecycle 분류 + 매수/매도 분리."""
    if not pair_filings:
        return {}
    buys: list[dict[str, Any]] = []
    sells: list[dict[str, Any]] = []
    for f in pair_filings:
        irds = _i(f.get("stkqy_irds"))
        if irds > 0:
            buys.append({**f, "_qty": irds})
        elif irds < 0:
            sells.append({**f, "_qty": abs(irds)})
        # 0 = 정정 — 무시

    last_pct = _f(pair_filings[-1].get("stkrt"))
    has_sell = len(sells) > 0
    if last_pct < 5.0 and last_pct > 0:
        status = "CLOSED"  # 철수 완료 (5% 미만)
    elif has_sell:
        status = "TRADING"  # 매수/매도 혼합, 현재 보유 중
    elif last_pct >= 5.0:
        status = "OPEN"  # 매수만, 보유 중
    else:
        status = "UNKNOWN"

    return {
        "n_filings": len(pair_filings),
        "n_buys": len(buys),
        "n_sells": len(sells),
        "buy_qty": sum(b["_qty"] for b in buys),
        "sell_qty": sum(s["_qty"] for s in sells),
        "first_date": pair_filings[0].get("rcept_dt"),
        "last_date": pair_filings[-1].get("rcept_dt"),
        "first_pct": _f(pair_filings[0].get("stkrt")),
        "last_pct": last_pct,
        "max_pct": max(_f(f.get("stkrt")) for f in pair_filings),
        "status": status,
        "buys": buys,
        "sells": sells,
    }


def fetch_price_series(stock_code_or_ticker: str, bgn: str, end: str, yf) -> dict[str, float]:
    """yfinance 가격 series. stock_code 6자리는 .KS → .KQ 폴백.
    이미 ticker 형태 ('069500.KS') 면 그대로 사용.
    bgn/end: YYYYMMDD 또는 YYYY-MM-DD.
    """
    bgn_iso = f"{bgn[:4]}-{bgn[4:6]}-{bgn[6:8]}" if len(bgn) == 8 and bgn.isdigit() else bgn
    end_iso = f"{end[:4]}-{end[4:6]}-{end[6:8]}" if len(end) == 8 and end.isdigit() else end

    candidates = [stock_code_or_ticker]
    if "." not in stock_code_or_ticker and len(stock_code_or_ticker) == 6:
        candidates = [f"{stock_code_or_ticker}.KS", f"{stock_code_or_ticker}.KQ"]

    for ticker in candidates:
        try:
            df = yf.download(ticker, start=bgn_iso, end=end_iso,
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                continue
            df.index = df.index.strftime("%Y%m%d")
            out = {}
            for d in df.index:
                v = df.loc[d, "Close"]
                if hasattr(v, "iloc"):
                    v = v.iloc[0]
                out[d] = float(v)
            return out
        except Exception:
            continue
    return {}


def _nearest(series: dict[str, float], target: str) -> float | None:
    if not series:
        return None
    pre = sorted(d for d in series if d <= target)
    if pre:
        return series[pre[-1]]
    post = sorted(d for d in series if d >= target)
    return series[post[0]] if post else None


def compute_cycle_alpha(
    pair: tuple[str, str],
    cycle: dict[str, Any],
    yf,
    kospi_cache: dict[str, dict[str, float]],
) -> dict[str, Any] | None:
    """단일 cycle 의 realized (CLOSED) 또는 unrealized (OPEN) alpha."""
    actor, sc = pair
    if cycle["n_buys"] == 0:
        return None

    bgn = _date(cycle["first_date"])
    today = datetime.now().strftime("%Y%m%d")
    end_horizon = today
    series = fetch_price_series(sc, bgn, end_horizon, yf)
    if not series:
        return None

    # 매수 가중 평균
    buy_total_value = 0.0
    buy_total_qty = 0
    for b in cycle["buys"]:
        d = _date(b.get("rcept_dt", ""))
        p = _nearest(series, d)
        if p is None or p <= 0:
            continue
        buy_total_value += b["_qty"] * p
        buy_total_qty += b["_qty"]
    if buy_total_qty == 0:
        return None
    buy_avg = buy_total_value / buy_total_qty

    # 매도 가중 평균
    sell_avg = None
    if cycle["n_sells"] > 0:
        sell_total_value = 0.0
        sell_total_qty = 0
        for s in cycle["sells"]:
            d = _date(s.get("rcept_dt", ""))
            p = _nearest(series, d)
            if p is None or p <= 0:
                continue
            sell_total_value += s["_qty"] * p
            sell_total_qty += s["_qty"]
        if sell_total_qty > 0:
            sell_avg = sell_total_value / sell_total_qty

    # 데이터 클린: CLOSED 인데 sell_avg=None (매도일 모두 가격 데이터 없음)
    # → 상장폐지 후 신고 또는 KRX 미커버. realized alpha 신뢰 못 함 → skip.
    if cycle["status"] == "CLOSED" and sell_avg is None:
        return None

    # exit price: CLOSED 의 경우 마지막 매도일 가격, 그 외 현재가
    if cycle["status"] == "CLOSED" and sell_avg is not None:
        exit_price = sell_avg
        exit_date = _date(cycle["sells"][-1].get("rcept_dt", ""))
        return_pct = (exit_price - buy_avg) / buy_avg * 100
        return_type = "realized"
    else:
        # OPEN / TRADING — 현재가 기준 unrealized
        exit_price = list(series.values())[-1]
        exit_date = sorted(series.keys())[-1]
        return_pct = (exit_price - buy_avg) / buy_avg * 100
        return_type = "unrealized"

    # KOSPI alpha
    entry_date = _date(cycle["buys"][0].get("rcept_dt", ""))
    if entry_date not in kospi_cache:
        kospi_cache[entry_date] = fetch_price_series(KOSPI_INDEX_YF, entry_date, today, yf)
    kospi_series = kospi_cache.get(entry_date, {})
    kospi_entry = _nearest(kospi_series, entry_date)
    kospi_exit = _nearest(kospi_series, exit_date) if kospi_series else None
    alpha_pct = None
    if kospi_entry and kospi_exit and kospi_entry > 0:
        kospi_return = (kospi_exit - kospi_entry) / kospi_entry * 100
        alpha_pct = return_pct - kospi_return

    holding_days = None
    try:
        holding_days = (datetime.strptime(exit_date, "%Y%m%d") - datetime.strptime(entry_date, "%Y%m%d")).days
    except Exception:
        pass

    return {
        "actor": actor,
        "stock_code": sc,
        "status": cycle["status"],
        "return_type": return_type,
        "buy_avg_won": round(buy_avg, 0),
        "sell_avg_won": round(sell_avg, 0) if sell_avg else None,
        "exit_price_won": round(exit_price, 0),
        "return_pct": round(return_pct, 2),
        "alpha_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "holding_days": holding_days,
        "n_buys": cycle["n_buys"],
        "n_sells": cycle["n_sells"],
        "first_pct": cycle["first_pct"],
        "max_pct": cycle["max_pct"],
        "last_pct": cycle["last_pct"],
    }


def aggregate_actor_realized(
    cycles: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """운용사별 *CLOSED* 와 *OPEN* 분리 통계."""
    by_actor: dict[str, dict[str, Any]] = {}
    for c in cycles:
        a = c["actor"]
        if a not in by_actor:
            by_actor[a] = {
                "closed": [],
                "open": [],
                "trading": [],
            }
        if c["status"] == "CLOSED":
            by_actor[a]["closed"].append(c)
        elif c["status"] == "TRADING":
            by_actor[a]["trading"].append(c)
        else:
            by_actor[a]["open"].append(c)
    return by_actor


def render_lifecycle_markdown(
    cycles: list[dict[str, Any]],
    *,
    days: int,
) -> str:
    out: list[str] = []
    bgn_d = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_d = datetime.now().strftime("%Y-%m-%d")
    out.append(f"# 5pct-radar — 운용사 *full cycle* realized alpha backtest")
    out.append(f"")
    out.append(f"*기간 {bgn_d} ~ {end_d} ({days}일), n_cycles={len(cycles)}*")
    out.append("")
    out.append("> ⚠️ **lifecycle 정의:**")
    out.append("> - **CLOSED**: 마지막 신고 지분 *< 5%* (신고 의무 종료 = 철수 완료)")
    out.append("> - **OPEN**: 매수만, 현재 보유 중 (≥ 5%)")
    out.append("> - **TRADING**: 매수+매도 혼합, 현재 보유 중")
    out.append(">")
    out.append("> **realized** = 매수 가중평균 대비 매도 가중평균 (CLOSED 만)")
    out.append("> **unrealized** = 매수 가중평균 대비 현재가 (OPEN/TRADING)")
    out.append("> *5% 미만 도달 후 추가 매도는 신고 의무 없어 *realized 가격이 상한 추정* 임*")
    out.append("")

    by_actor = aggregate_actor_realized(cycles)
    # 활동주의 풀 (closed >= 2 만)
    significant = []
    for a, d in by_actor.items():
        closed = d["closed"]
        opn = d["open"] + d["trading"]
        if len(closed) >= 1 or len(opn) >= 2:
            significant.append((a, d))

    # §1 — 매집·철수 완료한 운용사 ranking (CLOSED realized alpha)
    out.append("## §1. 🎯 *철수 완료* 사이클 realized alpha — 매집부터 매도까지 따라간 성과")
    out.append("")
    out.append("운용사별 CLOSED 사이클 평균. n_closed ≥ 1 만 표시.")
    out.append("")
    closed_summary: list[tuple[str, dict[str, Any]]] = []
    for a, d in by_actor.items():
        closed = d["closed"]
        if not closed:
            continue
        alphas = [c["alpha_pct"] for c in closed if c.get("alpha_pct") is not None]
        returns = [c["return_pct"] for c in closed if c.get("return_pct") is not None]
        if not returns:
            continue
        holding_days = [c["holding_days"] for c in closed if c.get("holding_days")]
        closed_summary.append((a, {
            "display_name": closed[0]["actor"],
            "n_closed": len(closed),
            "return_mean": sum(returns) / len(returns),
            "alpha_mean": sum(alphas) / len(alphas) if alphas else None,
            "win_rate": sum(1 for r in returns if r > 0) / len(returns) * 100,
            "avg_hold_days": sum(holding_days) / len(holding_days) if holding_days else None,
            "best": max(returns),
            "worst": min(returns),
            "stocks": [c["stock_code"] for c in closed],
        }))
    closed_summary.sort(key=lambda kv: -(kv[1].get("alpha_mean") or -999))

    if closed_summary:
        out.append("| Rank | 운용사 | n_closed | realized return mean | KOSPI alpha mean | 승률 | best | worst | 평균 hold |")
        out.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
        for i, (a, s) in enumerate(closed_summary, 1):
            out.append(f"| {i} | {s['display_name'][:25]} | {s['n_closed']} | "
                       f"{s['return_mean']:+.1f}% | "
                       f"{(s['alpha_mean'] or 0):+.1f}% | "
                       f"{s['win_rate']:.0f}% | "
                       f"{s['best']:+.0f}% | {s['worst']:+.0f}% | "
                       f"{(s['avg_hold_days'] or 0):.0f}일 |")
    else:
        out.append("*(closed 사이클 데이터 부족 — 기간 더 길게 필요)*")
    out.append("")

    # §2 — 진행 중 (OPEN/TRADING) unrealized
    out.append("## §2. *진행 중* 사이클 unrealized alpha")
    out.append("")
    out.append("운용사별 OPEN + TRADING 사이클 평균. n ≥ 2 만 표시.")
    out.append("")
    open_summary: list[tuple[str, dict[str, Any]]] = []
    for a, d in by_actor.items():
        opn = d["open"] + d["trading"]
        if len(opn) < 2:
            continue
        alphas = [c["alpha_pct"] for c in opn if c.get("alpha_pct") is not None]
        returns = [c["return_pct"] for c in opn if c.get("return_pct") is not None]
        if not returns:
            continue
        open_summary.append((a, {
            "display_name": opn[0]["actor"],
            "n_open": len(opn),
            "return_mean": sum(returns) / len(returns),
            "alpha_mean": sum(alphas) / len(alphas) if alphas else None,
            "win_rate": sum(1 for r in returns if r > 0) / len(returns) * 100,
        }))
    open_summary.sort(key=lambda kv: -(kv[1].get("alpha_mean") or -999))
    if open_summary:
        out.append("| Rank | 운용사 | n_open | unrealized mean | KOSPI alpha | 승률 |")
        out.append("|---:|---|---:|---:|---:|---:|")
        for i, (a, s) in enumerate(open_summary[:30], 1):
            out.append(f"| {i} | {s['display_name'][:25]} | {s['n_open']} | "
                       f"{s['return_mean']:+.1f}% | "
                       f"{(s['alpha_mean'] or 0):+.1f}% | "
                       f"{s['win_rate']:.0f}% |")
    out.append("")

    # §3 — CLOSED 사이클 list (상세)
    closed_cycles = [c for c in cycles if c["status"] == "CLOSED"]
    closed_cycles.sort(key=lambda c: -(c.get("alpha_pct") or -999))
    out.append("## §3. CLOSED 사이클 상세 (alpha 내림차순)")
    out.append("")
    if closed_cycles:
        out.append("| 운용사 | 종목 | 진입일 | 철수일 | 매수가 | 매도가 | return | alpha | hold |")
        out.append("|---|---|---|---|---:|---:|---:|---:|---:|")
        for c in closed_cycles[:40]:
            out.append(f"| {c['actor'][:18]} | {c['stock_code']} | {c['entry_date']} | "
                       f"{c['exit_date']} | "
                       f"{(c.get('buy_avg_won') or 0):,.0f} | "
                       f"{(c.get('sell_avg_won') or 0):,.0f} | "
                       f"{c['return_pct']:+.1f}% | "
                       f"{(c.get('alpha_pct') or 0):+.1f}% | "
                       f"{(c.get('holding_days') or 0)}일 |")
    out.append("")

    out.append("---")
    out.append("")
    out.append("*본 backtest 는 과거 데이터 통계 — *미래 수익률 보장 아님*. "
               "투자 결정 전 §6/§7 사람 검증 필수. DISCLAIMER.md*")
    return "\n".join(out)


def run_lifecycle_backtest(
    days: int = 1825,
    *,
    max_filings: int | None = None,
) -> tuple[Path | None, list[dict[str, Any]]]:
    """기본 5년. 1) fetch 5%+ 2) filter activist/semi/pe 3) pair lifecycle 4) realized/unrealized alpha (yfinance).

    max_filings: filter 통과 신고 중 최초 N건만 처리 (검증·sample 용)
    """
    yf = _import_yfinance()
    print(f"\n[1/4] 지난 {days}일 5%+ 신고 fetch...")
    raw = fetch_filings_window(days)
    flt = filter_meaningful(raw)
    print(f"   activist/semi/pe filter: {len(flt)} / {len(raw)}")

    if max_filings is not None and len(flt) > max_filings:
        print(f"   max_filings={max_filings} 적용 — 처음 {max_filings} 건만 처리")
        flt = flt[:max_filings]

    print(f"\n[2/4] document.xml 본문 파싱 → stkqy_irds 추출 ...")
    print(f"   각 신고 본문 fetch (sleep 0.2s, checkpoint 매 100건)")
    FILING_INTEL_DIR.mkdir(parents=True, exist_ok=True)
    end_label = datetime.now().strftime("%Y%m%d")
    enrich_ckpt = FILING_INTEL_DIR / f"lifecycle_{end_label}_{days}d_enrich.json"
    enriched_filings = enrich_with_document_xml(flt, checkpoint_path=enrich_ckpt)

    pairs = group_by_pair(enriched_filings)
    print(f"   pair 수: {len(pairs)}")

    print(f"\n[3/4] 각 pair lifecycle classify + alpha 계산 (yfinance)...")
    print(f"   sleep 0.2s/pair, checkpoint 매 50건")
    FILING_INTEL_DIR.mkdir(parents=True, exist_ok=True)
    end_label = datetime.now().strftime("%Y%m%d")
    checkpoint_path = FILING_INTEL_DIR / f"lifecycle_{end_label}_{days}d_partial.json"

    kospi_cache: dict[str, dict[str, float]] = {}
    cycles: list[dict[str, Any]] = []
    fail = 0
    for i, (pair, filings) in enumerate(pairs.items(), 1):
        if i % 50 == 0:
            print(f"   {i}/{len(pairs)} (succ {len(cycles)} fail {fail})")
            # checkpoint partial save
            try:
                checkpoint_path.write_text(
                    json.dumps(cycles, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
        cyc = classify_cycle(filings)
        if not cyc or cyc.get("n_buys", 0) == 0:
            continue
        try:
            result = compute_cycle_alpha(pair, cyc, yf, kospi_cache)
        except Exception as e:
            print(f"   ! pair {pair} 에러: {e}")
            fail += 1
            continue
        if result is None:
            fail += 1
            continue
        cycles.append(result)
        time.sleep(0.2)  # yfinance 안전 sleep
    print(f"   완료: {len(cycles)} cycle 계산, {fail} 실패")

    print(f"\n[4/4] 보고서 작성 ...")
    md = render_lifecycle_markdown(cycles, days=days)
    FILING_INTEL_DIR.mkdir(parents=True, exist_ok=True)
    end = datetime.now().strftime("%Y%m%d")
    md_path = FILING_INTEL_DIR / f"lifecycle_{end}_{days}d.md"
    json_path = FILING_INTEL_DIR / f"lifecycle_{end}_{days}d.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(cycles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 저장: {md_path}")
    return md_path, cycles
