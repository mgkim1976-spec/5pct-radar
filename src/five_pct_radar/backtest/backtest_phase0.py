"""Phase 0 backtest — score 모델 sanity check.

lifecycle JSON 데이터의 각 cycle 에 *진입 시점 정보* 로 Follow-Trade Score 계산
+ yfinance 가격으로 *forward return* 신선 재계산 → 점수대별 bucket 분석.

monotonic 관계 (점수 ↑ → return ↑) 가 있어야 score 모델 *초안 validity*.

⚠️ Phase 0 의 *look-ahead* 부분 인정:
  - actor track record 는 *시점 후행 정보* 일부 사용 (현재 lifecycle 결과 그대로)
  - 진정한 point-in-time 은 Phase 1 부터 (out-of-sample)
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import CORP_MAP_FILE, FILING_INTEL_DIR
from ..backtest.score_model import compute_follow_trade_score


def _yf_ticker(stock_code: str, corp_cls: str = "Y") -> str:
    """6자리 → yfinance ticker. KOSPI=.KS, KOSDAQ=.KQ."""
    suffix = ".KQ" if corp_cls == "K" else ".KS"
    return f"{stock_code}{suffix}"


_fundamentals_cache: dict[str, dict[str, float | None]] = {}


def fetch_fundamentals_yf(stock_code: str) -> dict[str, Any]:
    """yfinance .info 에서 PBR / ROE / debt_to_equity / current_ratio + sector/industry.

    ⚠️ *현재* 값. Phase 1 의 look-ahead. KOSPI 우선, 실패 시 KOSDAQ.
    """
    if stock_code in _fundamentals_cache:
        return _fundamentals_cache[stock_code]
    import yfinance as yf  # type: ignore

    out: dict[str, Any] = {
        "pbr": None, "roe_pct": None,
        "debt_to_equity": None, "current_ratio": None,
        "sector": None, "industry": None,
    }
    for suffix in (".KS", ".KQ"):
        ticker = f"{stock_code}{suffix}"
        try:
            info = yf.Ticker(ticker).info
            if not info or info.get("regularMarketPrice") is None:
                continue
            pbr = info.get("priceToBook")
            roe = info.get("returnOnEquity")
            de = info.get("debtToEquity")
            cr = info.get("currentRatio")
            out["pbr"] = float(pbr) if pbr else None
            out["roe_pct"] = float(roe) * 100 if roe else None
            out["debt_to_equity"] = float(de) / 100 if de and de > 5 else (float(de) if de else None)
            out["current_ratio"] = float(cr) if cr else None
            out["sector"] = info.get("sector")
            out["industry"] = info.get("industry")
            break
        except Exception:
            continue
    _fundamentals_cache[stock_code] = out
    return out


def fetch_forward_return_yf(
    stock_code: str,
    entry_date: str,  # YYYYMMDD
    exit_date: str,
    corp_cls: str = "Y",
) -> dict[str, Any] | None:
    """yfinance 로 entry→exit return 신선 계산."""
    import yfinance as yf  # type: ignore

    ticker = _yf_ticker(stock_code, corp_cls)
    try:
        # entry 전 5일 ~ exit 후 5일 (영업일 보장)
        start = (datetime.strptime(entry_date, "%Y%m%d") - timedelta(days=10)).strftime("%Y-%m-%d")
        end = (datetime.strptime(exit_date, "%Y%m%d") + timedelta(days=10)).strftime("%Y-%m-%d")
        # 미래 date 면 today 로 clamp
        today = datetime.now().strftime("%Y-%m-%d")
        if end > today:
            end = today
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # entry 일자 이후 첫 영업일
        df.index = df.index.strftime("%Y%m%d")
        post_entry = [d for d in df.index if d >= entry_date]
        if not post_entry:
            return None
        entry_actual = post_entry[0]
        entry_price = float(df.loc[entry_actual, "Close"].iloc[0] if hasattr(df.loc[entry_actual, "Close"], 'iloc') else df.loc[entry_actual, "Close"])
        # exit 일자 이전 마지막 영업일
        pre_exit = [d for d in df.index if d <= exit_date]
        if not pre_exit:
            return None
        exit_actual = pre_exit[-1]
        exit_price = float(df.loc[exit_actual, "Close"].iloc[0] if hasattr(df.loc[exit_actual, "Close"], 'iloc') else df.loc[exit_actual, "Close"])
        if entry_price <= 0:
            return None
        return_pct = (exit_price - entry_price) / entry_price * 100
        return {
            "yf_entry_date": entry_actual,
            "yf_exit_date": exit_actual,
            "yf_entry_price": entry_price,
            "yf_exit_price": exit_price,
            "yf_return_pct": round(return_pct, 2),
        }
    except Exception as e:
        return {"yf_error": str(e)}


def build_actor_track(cycles: list[dict[str, Any]], cutoff_date: str) -> dict[str, dict[str, Any]]:
    """cutoff 이전 *완료된* (CLOSED + 진입~철수 모두 cutoff 이전) cycle 만으로
    운용사별 track record. Point-in-time 흉내."""
    by_actor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cycles:
        if c.get("status") != "CLOSED":
            continue
        ed = (c.get("exit_date") or "")
        if ed and ed < cutoff_date:
            by_actor[c["actor"]].append(c)

    out: dict[str, dict[str, Any]] = {}
    for actor, ccs in by_actor.items():
        rets = [c.get("return_pct") for c in ccs if c.get("return_pct") is not None]
        if not rets:
            continue
        rets_sorted = sorted(rets)
        med = rets_sorted[len(rets_sorted) // 2]
        out[actor] = {
            "n_closed": len(rets),
            "raw_mean": round(sum(rets) / len(rets), 2),
            "raw_median": round(med, 2),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 0),
        }
    return out


def score_cycle_with_lifecycle_data(
    cycle: dict[str, Any],
    all_cycles: list[dict[str, Any]],
) -> dict[str, Any]:
    """단일 cycle 에 *그 시점 이전* 정보 활용 score 계산."""
    entry_date = cycle.get("entry_date", "")

    # actor track — entry_date 이전 closed cycle 만
    actor_tracks = build_actor_track(all_cycles, entry_date)
    actor_track = actor_tracks.get(cycle["actor"])

    # 동시 진입 운용사 — entry_date ± 90일에 *다른 actor* 가 *같은 stock* 에 진입
    same_stock = [c for c in all_cycles if c["stock_code"] == cycle["stock_code"]
                  and c["actor"] != cycle["actor"]]
    n_peer = 0
    prior_exits = 0
    for s in same_stock:
        s_entry = s.get("entry_date", "")
        s_exit = s.get("exit_date", "")
        # 동시 진입 (±90일)
        try:
            dt = (datetime.strptime(entry_date, "%Y%m%d") - datetime.strptime(s_entry, "%Y%m%d")).days
            if abs(dt) <= 90:
                n_peer += 1
        except Exception:
            pass
        # *철수한 사람* (s 의 exit_date 가 entry_date 이전 + status CLOSED)
        if s.get("status") == "CLOSED" and s_exit and s_exit < entry_date:
            prior_exits += 1

    # C. fundamentals (Phase 1 추가) — yfinance .info
    funds = fetch_fundamentals_yf(cycle["stock_code"])

    # 진입가 vs 매수평균 (cycle 자체의 buy_avg) — Phase 0 의 단순화
    score = compute_follow_trade_score(
        actor_category=cycle.get("actor_category", "unknown"),
        actor_track=actor_track,
        holding_purpose="경영권 영향" if cycle.get("actor_category") == "activist" else "단순투자",
        stkrt_pct=cycle.get("max_pct", 5.0),
        stkrt_irds=2.0 if cycle.get("n_buys", 0) >= 3 else 1.0,
        n_buys_so_far=cycle.get("n_buys", 0),
        n_sells_so_far=cycle.get("n_sells", 0),
        pbr=funds.get("pbr"),
        roe_pct=funds.get("roe_pct"),
        debt_to_equity=funds.get("debt_to_equity"),
        current_ratio=funds.get("current_ratio"),
        current_price=cycle.get("buy_avg_won", 0),
        buy_avg_so_far=None,
        n_other_actors_in_stock=n_peer,
        parent_action="neutral",
        prior_actor_exits=prior_exits,
    )
    score["cycle_return"] = cycle.get("return_pct")
    score["cycle_status"] = cycle.get("status")
    score["actor"] = cycle["actor"]
    score["stock_code"] = cycle["stock_code"]
    return score


def add_sector_adjusted_return(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """각 cycle 의 *섹터-중립 return* 계산.

    sector_adj = yf_return - 같은 sector 종목들의 *동일 entry_date ±90일* 평균 return

    sector 정보는 yfinance .info 의 'sector' 필드 (영어 카테고리 — Industrials,
    Financial Services, Healthcare 등). 같은 sector 의 *다른 cycle* 평균과 비교.
    """
    from datetime import datetime, timedelta
    # 각 cycle 에 sector 부여
    for s in scored:
        funds = _fundamentals_cache.get(s["stock_code"], {})
        s["sector"] = funds.get("sector")

    # sector 별 cycle 목록
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for s in scored:
        if s.get("yf_return_pct") is None or not s.get("sector"):
            continue
        by_sector.setdefault(s["sector"], []).append(s)

    # 각 cycle 에 sector_adj_return 추가
    for s in scored:
        if s.get("yf_return_pct") is None or not s.get("sector"):
            s["sector_adj_return"] = None
            continue
        peers = by_sector.get(s["sector"], [])
        try:
            s_entry_dt = datetime.strptime(s.get("yf_entry_date", "")[:8], "%Y%m%d")
        except Exception:
            s["sector_adj_return"] = None
            continue
        # 같은 sector + entry_date ±180일 범위의 동시기 peer
        peer_returns = []
        for p in peers:
            if p is s:
                continue
            try:
                p_entry_dt = datetime.strptime(p.get("yf_entry_date", "")[:8], "%Y%m%d")
            except Exception:
                continue
            if abs((s_entry_dt - p_entry_dt).days) <= 180:
                peer_returns.append(p["yf_return_pct"])
        if peer_returns:
            sector_avg = sum(peer_returns) / len(peer_returns)
            s["sector_adj_return"] = round(s["yf_return_pct"] - sector_avg, 2)
            s["sector_n_peers"] = len(peer_returns)
        else:
            s["sector_adj_return"] = None
    return scored


def add_yf_forward_return(
    scored: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
    horizon_days: int = 365,
) -> list[dict[str, Any]]:
    """각 cycle 에 yfinance 로 *D+1 진입 → +horizon 일 매도* return 추가."""
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
    out = []
    for s, c in zip(scored, cycles):
        sc = s["stock_code"]
        entry = c.get("entry_date", "")
        # +horizon 일 후 (또는 cycle exit_date 중 빠른 쪽)
        try:
            forward_exit = (datetime.strptime(entry, "%Y%m%d") + timedelta(days=horizon_days)).strftime("%Y%m%d")
        except Exception:
            forward_exit = c.get("exit_date", "")
        cycle_exit = c.get("exit_date", forward_exit)
        target_exit = min(forward_exit, cycle_exit) if cycle_exit else forward_exit
        today = datetime.now().strftime("%Y%m%d")
        if target_exit > today:
            target_exit = today

        # corp_cls — corp_code_map 에 없음. 일단 KOSPI 기본.
        corp_cls = "Y"
        result = fetch_forward_return_yf(sc, entry, target_exit, corp_cls=corp_cls)
        if result is None and "K" not in corp_cls:
            # KOSPI 실패 → KOSDAQ 재시도
            result = fetch_forward_return_yf(sc, entry, target_exit, corp_cls="K")
        merged = {**s, "yf_target_exit": target_exit}
        if result:
            merged.update(result)
        out.append(merged)
    return out


def bucket_analysis(scored: list[dict[str, Any]], return_field: str = "yf_return_pct") -> dict[str, Any]:
    """점수 5분위 bucket 별 forward return 분포."""
    valid = [s for s in scored if s.get(return_field) is not None]
    if not valid:
        return {"error": "no valid scores"}

    # 점수 분포
    totals = [s["total"] for s in valid]
    totals_sorted = sorted(totals)
    n = len(totals_sorted)
    cuts = [totals_sorted[n * i // 5] for i in range(1, 5)]

    def bucket_of(t):
        for i, c in enumerate(cuts):
            if t < c:
                return i
        return 4

    buckets: dict[int, list[float]] = defaultdict(list)
    for s in valid:
        buckets[bucket_of(s["total"])].append(s[return_field])

    summary = []
    for i in range(5):
        rets = buckets[i]
        if not rets:
            continue
        rets_sorted = sorted(rets)
        summary.append({
            "bucket": i,
            "score_range": f"~{cuts[i] if i < 4 else 100:.1f}",
            "n": len(rets),
            "mean": round(sum(rets) / len(rets), 1),
            "median": round(rets_sorted[len(rets_sorted) // 2], 1),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 0),
        })

    # monotonic? (mean 이 bucket 순으로 증가하는가)
    means = [b["mean"] for b in summary]
    is_monotonic = all(means[i] <= means[i + 1] for i in range(len(means) - 1))

    return {
        "buckets": summary,
        "score_cuts": cuts,
        "is_monotonic": is_monotonic,
        "n_total": len(valid),
    }


def run_phase0(lifecycle_json_path: Path) -> Path:
    """Phase 0 전체 흐름."""
    print(f"\n[1/4] lifecycle JSON 로드 ...")
    cycles = json.loads(lifecycle_json_path.read_text(encoding="utf-8"))
    print(f"   {len(cycles)} cycles")

    print(f"\n[2/4] 각 cycle 에 score 계산 (yfinance fundamentals 포함)...")
    scored = []
    for i, c in enumerate(cycles, 1):
        if i % 30 == 0:
            print(f"   {i}/{len(cycles)} (fundamentals cache {len(_fundamentals_cache)})")
        scored.append(score_cycle_with_lifecycle_data(c, cycles))

    print(f"\n[3/4] yfinance forward return (+365일) 신선 계산...")
    scored = add_yf_forward_return(scored, cycles, horizon_days=365)
    n_yf_ok = sum(1 for s in scored if s.get("yf_return_pct") is not None)
    print(f"   yfinance 가격 성공: {n_yf_ok}/{len(scored)}")

    print(f"\n[4/4] 섹터-중립 alpha + bucket 분석 ...")
    scored = add_sector_adjusted_return(scored)
    n_sec = sum(1 for s in scored if s.get("sector_adj_return") is not None)
    print(f"   sector-adj 계산 완료: {n_sec}/{len(scored)}")
    yf_analysis = bucket_analysis(scored, return_field="yf_return_pct")
    cycle_analysis = bucket_analysis(scored, return_field="cycle_return")
    sector_analysis = bucket_analysis(scored, return_field="sector_adj_return")

    out_dir = FILING_INTEL_DIR
    out_path = out_dir / "phase0_score_validation.md"
    md = _render_phase0(scored, yf_analysis, cycle_analysis, sector_analysis, lifecycle_json_path)
    out_path.write_text(md, encoding="utf-8")
    (out_dir / "phase0_score_validation.json").write_text(
        json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✅ 저장: {out_path}")
    return out_path


def _render_phase0(scored, yf_analysis, cycle_analysis, sector_analysis, source) -> str:
    out = []
    out.append(f"# Phase 0 — Follow-Trade Score 모델 sanity check")
    out.append("")
    out.append(f"*source: {source.name}, n={len(scored)} cycles*")
    out.append("")
    out.append("> ⚠️ **Phase 0 한계**:")
    out.append("> - 점수 모델 가중치 *직관 기반 초안* (캘리브레이션 안 됨)")
    out.append("> - *point-in-time* 흉내 (actor track 은 entry_date 이전 cycle 만), but 다른 필드는 단순화")
    out.append("> - 표본 부족 — 통계적 유의성 weak")
    out.append("")

    out.append("## §1. 점수 5분위 bucket — yfinance forward return")
    out.append("")
    if "buckets" in yf_analysis:
        out.append("| Bucket | Score range | N | mean return | median | win rate |")
        out.append("|---:|---|---:|---:|---:|---:|")
        for b in yf_analysis["buckets"]:
            out.append(f"| {b['bucket']} | {b['score_range']} | {b['n']} | "
                       f"{b['mean']:+.1f}% | {b['median']:+.1f}% | {b['win_rate']:.0f}% |")
        out.append("")
        if yf_analysis["is_monotonic"]:
            out.append("✅ **Monotonic 관계 확인** — 점수 ↑ → return ↑ — score 모델 *초안 validity*")
        else:
            out.append("❌ **Monotonic 깨짐** — 점수 모델 *재캘리브레이션 필요*")
    out.append("")

    out.append("## §1b. 🎯 점수 5분위 bucket — **섹터-중립 return** (반도체 효과 제거)")
    out.append("")
    if "buckets" in sector_analysis:
        out.append("| Bucket | Score range | N | mean | median | win rate |")
        out.append("|---:|---|---:|---:|---:|---:|")
        for b in sector_analysis["buckets"]:
            out.append(f"| {b['bucket']} | {b['score_range']} | {b['n']} | "
                       f"{b['mean']:+.1f}% | {b['median']:+.1f}% | {b['win_rate']:.0f}% |")
        out.append("")
        if sector_analysis["is_monotonic"]:
            out.append("✅ **섹터-중립 Monotonic 확인** — *반도체 효과 제거* 후 점수 ↑ → 알파 ↑")
        else:
            out.append("❌ 섹터-중립도 monotonic 깨짐 — 모델 신호 자체 부족")
    out.append("")
    out.append("> 섹터-중립 alpha = cycle yf_return − *동일 sector 종목 동시기 ±180일* 평균. "
               "yfinance .info 의 sector 카테고리 (Industrials/Healthcare 등) 사용.")
    out.append("")

    out.append("## §2. 점수 5분위 bucket — lifecycle cycle return (대조군)")
    out.append("")
    if "buckets" in cycle_analysis:
        out.append("| Bucket | Score range | N | mean return | median | win rate |")
        out.append("|---:|---|---:|---:|---:|---:|")
        for b in cycle_analysis["buckets"]:
            out.append(f"| {b['bucket']} | {b['score_range']} | {b['n']} | "
                       f"{b['mean']:+.1f}% | {b['median']:+.1f}% | {b['win_rate']:.0f}% |")
        out.append("")

    out.append("## §3. 상위 10 cycle (점수 내림차순)")
    out.append("")
    valid = [s for s in scored if s.get("yf_return_pct") is not None]
    valid.sort(key=lambda x: -x["total"])
    if valid:
        out.append("| Score | label | 운용사 | 종목 | yf_return | cycle_return | A | B | C | D | E |")
        out.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for s in valid[:10]:
            out.append(f"| {s['total']} | {s['label']} | {s['actor'][:15]} | {s['stock_code']} | "
                       f"{s.get('yf_return_pct', 0):+.1f}% | {(s.get('cycle_return') or 0):+.1f}% | "
                       f"{s['A_actor']} | {s['B_filing']} | {s.get('C_fundamentals',0)} | "
                       f"{s['D_entry']} | {s['E_cross']} |")
    out.append("")

    out.append("## §4. 하위 10 cycle (점수 오름차순)")
    out.append("")
    valid_low = sorted(valid, key=lambda x: x["total"])
    if valid_low:
        out.append("| Score | label | 운용사 | 종목 | yf_return | cycle_return |")
        out.append("|---:|---|---|---|---:|---:|")
        for s in valid_low[:10]:
            out.append(f"| {s['total']} | {s['label']} | {s['actor'][:15]} | {s['stock_code']} | "
                       f"{s.get('yf_return_pct', 0):+.1f}% | {(s.get('cycle_return') or 0):+.1f}% |")
    out.append("")

    out.append("---")
    out.append("*과거 데이터 sanity check. 미래 수익률 보장 아님. DISCLAIMER.md*")
    return "\n".join(out)
