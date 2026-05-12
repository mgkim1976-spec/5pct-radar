"""운용사별 5% 신고 follow-alpha backtest.

핵심 질문: *"이 운용사의 5%+ 신고를 따라 사면 12개월 후 얼마나 수익이 나나?"*

방법:
  1. 지난 N년 KOSPI+KOSDAQ 5%+ 신고 전체 수집 (DART list.json)
  2. filter: activist / semi_activist / pe_fund 운용사만
  3. *신규 진입* 또는 *유의미한 변경* 신고만 (변경 0% 또는 사소한 정정은 제외)
  4. 각 신고에 대해:
     - 신고일 종가 (pykrx)
     - +30/+90/+180/+365일 종가
     - KOSPI 동기간 변화율 → alpha 계산
  5. 운용사별 집계: N / 평균 alpha / 중앙값 / 승률 / best/worst case
  6. *regime 분리*: 상법개정 2024 전후 (pre/post)

⚠️ 한계:
  - survivorship bias: 상장폐지된 종목은 pykrx 미커버 가능
  - 사소한 변경 신고 (Δ < 1%p) 제외 — *행동주의 의지* 만 포함
  - yfinance 미사용, pykrx 만 사용
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..backtest.actor_stats import classify_actor, fetch_filings_window, normalize_actor_name
from ..config import FILING_INTEL_DIR


# KOSPI 추적용 ETF — KODEX 200 (069500). KOSPI 종합지수 자체는
# pykrx get_index_ohlcv 가 KRX 로그인 필요해서 ETF 로 대체 (corr > 0.99).
KOSPI_INDEX = "069500"


def _import_pykrx():
    """pykrx 지연 import (optional dependency)."""
    try:
        from pykrx import stock  # type: ignore
        return stock
    except ImportError as e:
        raise RuntimeError(
            "pykrx 미설치. `pip install -e .[backtest]` 또는 `pip install pykrx pandas`"
        ) from e


def _date_str(d: str) -> str:
    """rcept_dt 정규화 → 'YYYYMMDD'."""
    return d.replace("-", "")[:8]


def _add_days(d: str, days: int) -> str:
    """YYYYMMDD + N일."""
    dt = datetime.strptime(_date_str(d), "%Y%m%d") + timedelta(days=days)
    return dt.strftime("%Y%m%d")


def filter_meaningful_filings(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """activist/semi/pe 신고만 + *유의미한* 변경 (신규 / Δ ≥ 1%p)."""
    out = []
    for f in filings:
        flr = f.get("flr_nm", "")
        cat = classify_actor(flr)
        if cat not in ("activist", "semi_activist", "pe_fund"):
            continue
        # 보고서명에서 신규/변동/변경 추정
        report_nm = f.get("report_nm", "") or ""
        # 정정 신고는 제외 (중복 신호)
        if "[정정]" in report_nm or "[기재정정]" in report_nm:
            continue
        # stock_code 없으면 비상장 — 제외
        if not f.get("stock_code"):
            continue
        out.append({**f, "_actor_category": cat})
    return out


def fetch_price_series(stock_code: str, bgn: str, end: str, stock) -> dict[str, float] | None:
    """pykrx OHLCV → {date: close} dict.

    실패 시 None.
    """
    try:
        df = stock.get_market_ohlcv(bgn, end, stock_code)
        if df is None or df.empty:
            return None
        # df.index 는 datetime, '종가' 컬럼
        return {idx.strftime("%Y%m%d"): float(row["종가"]) for idx, row in df.iterrows()}
    except Exception:
        return None


def _nearest_price(series: dict[str, float], target: str) -> float | None:
    """target 이전 가장 가까운 영업일 가격."""
    if not series:
        return None
    candidates = sorted(d for d in series if d <= target)
    if not candidates:
        # target 이전 데이터 없으면 이후 첫 영업일 사용 (신고일에 거래 없는 케이스)
        candidates = sorted(d for d in series if d >= target)
        return series[candidates[0]] if candidates else None
    return series[candidates[-1]]


def compute_alpha_for_filing(
    filing: dict[str, Any],
    stock,
    kospi_cache: dict[str, dict[str, float]],
) -> dict[str, Any] | None:
    """단일 신고의 +30/+90/+180/+365 KOSPI alpha 계산."""
    stock_code = filing.get("stock_code", "")
    rcept_dt = _date_str(filing.get("rcept_dt", ""))
    if not stock_code or not rcept_dt:
        return None

    end_horizon = _add_days(rcept_dt, 380)  # +365 + buffer
    end_horizon = min(end_horizon, datetime.now().strftime("%Y%m%d"))
    if end_horizon < rcept_dt:
        return None

    series = fetch_price_series(stock_code, rcept_dt, end_horizon, stock)
    if not series or len(series) < 5:
        return None

    base_price = _nearest_price(series, rcept_dt)
    if base_price is None or base_price <= 0:
        return None

    # KOSPI cache 활용
    if rcept_dt not in kospi_cache:
        kospi_series = fetch_price_series(KOSPI_INDEX, rcept_dt, end_horizon, stock)
        kospi_cache[rcept_dt] = kospi_series or {}
    kospi_series = kospi_cache[rcept_dt]
    kospi_base = _nearest_price(kospi_series, rcept_dt) if kospi_series else None

    result = {
        "stock_code": stock_code,
        "corp_name": filing.get("corp_name", ""),
        "rcept_dt": rcept_dt,
        "actor": normalize_actor_name(filing.get("flr_nm", "")),
        "actor_display": filing.get("flr_nm", ""),
        "actor_category": filing.get("_actor_category", "?"),
        "base_price": base_price,
    }
    for label, days in [("d30", 30), ("d90", 90), ("d180", 180), ("d365", 365)]:
        target = _add_days(rcept_dt, days)
        if target > datetime.now().strftime("%Y%m%d"):
            result[f"return_{label}_pct"] = None
            result[f"alpha_{label}_pct"] = None
            continue
        future_price = _nearest_price(series, target)
        if future_price is None:
            result[f"return_{label}_pct"] = None
            result[f"alpha_{label}_pct"] = None
            continue
        raw_return = (future_price - base_price) / base_price * 100
        result[f"return_{label}_pct"] = round(raw_return, 2)
        if kospi_base and kospi_base > 0 and kospi_series:
            kospi_future = _nearest_price(kospi_series, target)
            if kospi_future:
                kospi_return = (kospi_future - kospi_base) / kospi_base * 100
                result[f"alpha_{label}_pct"] = round(raw_return - kospi_return, 2)
            else:
                result[f"alpha_{label}_pct"] = None
        else:
            result[f"alpha_{label}_pct"] = None
    return result


def aggregate_by_actor(results: list[dict[str, Any]], *, horizon: str = "d365") -> dict[str, dict[str, Any]]:
    """운용사별 alpha 통계."""
    by_actor: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_actor.setdefault(r["actor"], []).append(r)

    out = {}
    for actor, rows in by_actor.items():
        alphas = [r[f"alpha_{horizon}_pct"] for r in rows if r.get(f"alpha_{horizon}_pct") is not None]
        returns = [r[f"return_{horizon}_pct"] for r in rows if r.get(f"return_{horizon}_pct") is not None]
        if not alphas:
            out[actor] = {
                "actor_display": rows[0]["actor_display"],
                "category": rows[0]["actor_category"],
                "n_filings": len(rows),
                "n_with_alpha": 0,
                "alpha_mean": None,
                "alpha_median": None,
                "win_rate": None,
                "best_alpha": None,
                "worst_alpha": None,
                "return_mean": None,
            }
            continue
        alphas_sorted = sorted(alphas)
        median_a = alphas_sorted[len(alphas_sorted) // 2]
        out[actor] = {
            "actor_display": rows[0]["actor_display"],
            "category": rows[0]["actor_category"],
            "n_filings": len(rows),
            "n_with_alpha": len(alphas),
            "alpha_mean": round(sum(alphas) / len(alphas), 2),
            "alpha_median": round(median_a, 2),
            "win_rate": round(sum(1 for a in alphas if a > 0) / len(alphas) * 100, 1),
            "best_alpha": max(alphas),
            "worst_alpha": min(alphas),
            "return_mean": round(sum(returns) / len(returns), 2) if returns else None,
            "stocks": sorted({r["stock_code"] for r in rows}),
        }
    return out


def split_pre_post_regime(results: list[dict[str, Any]], split_date: str = "20240701") -> tuple[list, list]:
    """상법개정 2024-07 전후로 분리."""
    pre = [r for r in results if r["rcept_dt"] < split_date]
    post = [r for r in results if r["rcept_dt"] >= split_date]
    return pre, post


def render_actor_backtest_markdown(
    results: list[dict[str, Any]],
    days: int,
    horizon: str = "d365",
) -> str:
    out: list[str] = []
    end_d = datetime.now().strftime("%Y-%m-%d")
    bgn_d = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out.append(f"# 5pct-radar — 운용사 follow-alpha backtest ({bgn_d} ~ {end_d}, {horizon})")
    out.append("")
    n_total = len(results)
    n_with_alpha = sum(1 for r in results if r.get(f"alpha_{horizon}_pct") is not None)
    out.append(f"*분석 대상: {n_total} 신고 (activist/semi/pe 만), alpha 측정 완료: {n_with_alpha}*")
    out.append("")
    out.append(f"> ⚠️ **한계**: ")
    out.append(f"> - survivorship bias (상장폐지 종목 가격 데이터 누락 가능)")
    out.append(f"> - 같은 종목 반복 신고는 *각각 독립* 으로 카운트")
    out.append(f"> - 상법개정 2024-07-01 전후 *regime change* 가 있어 §3 에 분리 분석")
    out.append("")

    # §1 운용사별 종합 ranking
    agg = aggregate_by_actor(results, horizon=horizon)
    # n_with_alpha >= 3 만 의미 있는 통계
    sig = [(a, s) for a, s in agg.items() if (s.get("n_with_alpha") or 0) >= 3]
    sig.sort(key=lambda kv: -(kv[1]["alpha_mean"] or -999))

    out.append(f"## §1. 운용사 ranking by 12M KOSPI alpha (n ≥ 3)")
    out.append("")
    out.append("| Rank | 운용사 | cat | N | alpha mean | median | 승률 | best | worst | return mean |")
    out.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for i, (actor, s) in enumerate(sig, 1):
        out.append(f"| {i} | {s['actor_display'][:25]} | {s['category']} | "
                   f"{s['n_with_alpha']}/{s['n_filings']} | "
                   f"{(s['alpha_mean'] or 0):+.1f}% | "
                   f"{(s['alpha_median'] or 0):+.1f}% | "
                   f"{(s['win_rate'] or 0):.0f}% | "
                   f"{(s['best_alpha'] or 0):+.0f}% | "
                   f"{(s['worst_alpha'] or 0):+.0f}% | "
                   f"{(s['return_mean'] or 0):+.1f}% |")
    out.append("")

    # §2 카테고리 평균
    out.append("## §2. 카테고리별 평균")
    out.append("")
    cat_stats: dict[str, list[float]] = {}
    for r in results:
        a = r.get(f"alpha_{horizon}_pct")
        if a is not None:
            cat_stats.setdefault(r["actor_category"], []).append(a)
    out.append("| 카테고리 | N | alpha mean | 승률 |")
    out.append("|---|---:|---:|---:|")
    for cat in ["activist", "semi_activist", "pe_fund"]:
        alphas = cat_stats.get(cat, [])
        if not alphas:
            continue
        m = sum(alphas) / len(alphas)
        wr = sum(1 for a in alphas if a > 0) / len(alphas) * 100
        out.append(f"| {cat} | {len(alphas)} | {m:+.1f}% | {wr:.0f}% |")
    out.append("")

    # §3 regime 분리 (상법개정 전후)
    pre, post = split_pre_post_regime(results)
    out.append("## §3. 상법개정 2024-07-01 전후 분리")
    out.append("")
    out.append(f"- **Pre**  (n={len(pre)}): {bgn_d} ~ 2024-06-30")
    out.append(f"- **Post** (n={len(post)}): 2024-07-01 ~ {end_d}")
    out.append("")
    for label, subset in [("Pre (구체제)", pre), ("Post (신체제)", post)]:
        alphas = [r[f"alpha_{horizon}_pct"] for r in subset if r.get(f"alpha_{horizon}_pct") is not None]
        if not alphas:
            out.append(f"### {label}: 데이터 부족")
            out.append("")
            continue
        m = sum(alphas) / len(alphas)
        wr = sum(1 for a in alphas if a > 0) / len(alphas) * 100
        out.append(f"### {label} (n={len(alphas)})")
        out.append(f"- 평균 alpha {m:+.1f}%, 승률 {wr:.0f}%")
        out.append("")

    # §4 *진입 권장* vs *회피* 운용사
    out.append("## §4. 🎯 매수 가치 운용사 vs 회피 운용사")
    out.append("")
    out.append("**조건: n ≥ 3 & 승률 ≥ 50% & alpha mean > 0**")
    out.append("")
    buy = [(a, s) for a, s in sig if (s["win_rate"] or 0) >= 50 and (s["alpha_mean"] or 0) > 0]
    avoid = [(a, s) for a, s in sig if (s["alpha_mean"] or 0) < 0]

    out.append("### 🟢 매수 가치 (follow trade 후보)")
    out.append("")
    if buy:
        for actor, s in buy:
            out.append(f"- **{s['actor_display']}** (n={s['n_with_alpha']}, "
                       f"alpha {(s['alpha_mean'] or 0):+.1f}%, 승률 {(s['win_rate'] or 0):.0f}%)")
    else:
        out.append("*(조건 충족 운용사 없음)*")
    out.append("")

    out.append("### 🔴 회피 (alpha 음수)")
    out.append("")
    if avoid:
        for actor, s in avoid:
            out.append(f"- **{s['actor_display']}** (n={s['n_with_alpha']}, "
                       f"alpha {(s['alpha_mean'] or 0):+.1f}%, 승률 {(s['win_rate'] or 0):.0f}%)")
    else:
        out.append("*(조건 충족 운용사 없음)*")
    out.append("")

    out.append("---")
    out.append("")
    out.append("*본 backtest 는 과거 데이터 기반 통계이며 *미래 수익률 보장 아님*. ")
    out.append("개별 종목 신고는 사람 검증 필수. DISCLAIMER.md.*")
    return "\n".join(out)


def run_actor_backtest(days: int = 1095, *, horizon: str = "d365") -> tuple[Path | None, list[dict[str, Any]]]:
    """전체 흐름: fetch → filter → price/alpha → save."""
    stock = _import_pykrx()
    print(f"\n[1/3] 지난 {days} 일 5%+ 신고 수집...")
    filings_raw = fetch_filings_window(days)
    print(f"   총 {len(filings_raw)} 신고")

    filings = filter_meaningful_filings(filings_raw)
    print(f"\n[2/3] activist/semi/pe filter: {len(filings)} 신고 통과")

    print(f"\n[3/3] 각 신고 KOSPI alpha 계산 (horizon {horizon}) ...")
    kospi_cache: dict[str, dict[str, float]] = {}
    results: list[dict[str, Any]] = []
    fail = 0
    for i, f in enumerate(filings, 1):
        if i % 20 == 0:
            print(f"   {i}/{len(filings)} 처리 (성공 {len(results)}, 실패 {fail})")
        r = compute_alpha_for_filing(f, stock, kospi_cache)
        if r is None:
            fail += 1
            continue
        results.append(r)
        # pykrx rate-limit 대비 약간의 sleep
        time.sleep(0.05)
    print(f"\n  · 완료: {len(results)} 신고 alpha 계산, {fail} 실패")

    md = render_actor_backtest_markdown(results, days=days, horizon=horizon)
    FILING_INTEL_DIR.mkdir(parents=True, exist_ok=True)
    end = datetime.now().strftime("%Y%m%d")
    md_path = FILING_INTEL_DIR / f"backtest_actor_{end}_{days}d_{horizon}.md"
    json_path = FILING_INTEL_DIR / f"backtest_actor_{end}_{days}d_{horizon}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 저장: {md_path}")
    return md_path, results
