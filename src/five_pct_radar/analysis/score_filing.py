"""Single-filing follow-trade score wrapper.

새 5%+ 신고 1건 입력 → 5 component score (Phase 1) 산출.

scan_recent (배치 모드) + run_one (단건) 에서 호출되어 보고서에 자동 첨부.

⚠️ Phase 1 의 한계 그대로 (yfinance fundamentals look-ahead, 모델 미캘리브
   레이션). 운영은 *score < 33 PASS / 33~40 WATCH / 40+ CONSIDER* 룰로.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..backtest.actor_stats import classify_actor, normalize_actor_name
from ..config import CORP_MAP_FILE, FILING_INTEL_DIR
from ..core.fetch_filing import list_majorstock
from ..backtest.score_model import compute_follow_trade_score


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


def _load_actor_track() -> dict[str, dict[str, Any]]:
    """가장 최근 lifecycle 결과에서 운용사 track record 로드.

    *해당 종목의 신고 시점 이전* track 으로 fully filtering 하지 않음 (배치
    triage 용). Phase 2 에서 point-in-time 으로 강화 예정.
    """
    # 최근 lifecycle JSON 파일 찾기
    candidates = sorted(FILING_INTEL_DIR.glob("lifecycle_*.json"), reverse=True)
    if not candidates:
        return {}
    cycles = json.loads(candidates[0].read_text(encoding="utf-8"))
    by_actor: dict[str, list[dict[str, Any]]] = {}
    for c in cycles:
        if c.get("status") != "CLOSED":
            continue
        by_actor.setdefault(c["actor"], []).append(c)

    out: dict[str, dict[str, Any]] = {}
    for actor, ccs in by_actor.items():
        rets = [c.get("return_pct") for c in ccs if c.get("return_pct") is not None]
        if not rets:
            continue
        rets_sorted = sorted(rets)
        out[actor] = {
            "n_closed": len(rets),
            "raw_mean": round(sum(rets) / len(rets), 2),
            "raw_median": round(rets_sorted[len(rets_sorted) // 2], 2),
        }
    return out


def score_single_filing(
    rcept_no: str,
    stock_code: str,
    corp_code: str,
    flr_nm: str,
    rcept_dt: str,
    holding_purpose: str = "",  # extract_llm 결과 (있으면)
) -> dict[str, Any]:
    """단일 5%+ 신고 score 산출.

    Args:
        rcept_no: 신고 번호
        stock_code: 발행회사 종목코드
        corp_code: 발행회사 corp_code
        flr_nm: 보고자명 (raw)
        rcept_dt: 신고일 YYYYMMDD
        holding_purpose: extract_llm 으로 추출된 보유목적

    Returns:
        score dict (compute_follow_trade_score 출력 + 메타데이터)
    """
    actor_norm = normalize_actor_name(flr_nm)
    actor_cat = classify_actor(flr_nm)

    # 운용사 track record (lifecycle 결과 활용)
    all_tracks = _load_actor_track()
    actor_track = all_tracks.get(actor_norm)

    # 보유목적 추정 (없으면 운용사 카테고리로 폴백)
    if not holding_purpose:
        holding_purpose = "경영권 영향" if actor_cat == "activist" else \
                          "일반투자" if actor_cat in ("semi_activist", "pe_fund") else \
                          "단순투자"

    # 해당 종목 majorstock 전체 → 이 운용사의 buy/sell history
    ms = list_majorstock(corp_code) if corp_code else []
    same_actor = [m for m in ms if actor_norm == normalize_actor_name(m.get("repror", ""))]
    same_actor.sort(key=lambda x: x.get("rcept_dt", ""))
    # *이번 신고 이전* 만 — point-in-time
    rcept_dt_norm = rcept_dt.replace("-", "")[:8]
    prior = [m for m in same_actor if (m.get("rcept_dt", "").replace("-", "")[:8] < rcept_dt_norm)]
    n_buys = sum(1 for m in prior if _i(m.get("stkqy_irds")) > 0)
    n_sells = sum(1 for m in prior if _i(m.get("stkqy_irds")) < 0)

    # 현재 신고의 지분 + 변동
    current_filing = next((m for m in same_actor if m.get("rcept_no") == rcept_no), None)
    if current_filing:
        stkrt_pct = _f(current_filing.get("stkrt"))
        stkrt_irds = _f(current_filing.get("stkrt_irds"))
    else:
        stkrt_pct = 5.0
        stkrt_irds = 0.0

    # 진입가 매력도 — 현재가 vs 그동안 매수 가중평균
    buy_avg = None
    current_price = 0.0
    try:
        from ..backtest.backtest_phase0 import _fundamentals_cache, fetch_fundamentals_yf
        # yfinance .history 로 현재가 + 그동안 신고일 가격으로 buy_avg
        import yfinance as yf  # type: ignore

        if prior:
            # 첫 매수일 → 현재까지 가격 fetch
            first_buy = min(prior, key=lambda x: x.get("rcept_dt", ""))
            first_dt = first_buy.get("rcept_dt", "").replace("-", "")[:8]
            from datetime import timedelta
            start = (datetime.strptime(first_dt, "%Y%m%d") - timedelta(days=10)).strftime("%Y-%m-%d")
            today = datetime.now().strftime("%Y-%m-%d")
            for suffix in (".KS", ".KQ"):
                try:
                    df = yf.download(f"{stock_code}{suffix}", start=start, end=today,
                                     progress=False, auto_adjust=True)
                    if df is None or df.empty:
                        continue
                    df.index = df.index.strftime("%Y%m%d")
                    series = {d: float(df.loc[d, "Close"].iloc[0] if hasattr(df.loc[d, "Close"], 'iloc') else df.loc[d, "Close"]) for d in df.index}
                    # 매수 가중평균
                    tot_v, tot_q = 0.0, 0
                    for m in prior:
                        irds = _i(m.get("stkqy_irds"))
                        if irds <= 0:
                            continue
                        d = m.get("rcept_dt", "").replace("-", "")[:8]
                        pre = sorted(x for x in series if x <= d)
                        if not pre:
                            continue
                        p = series[pre[-1]]
                        tot_v += irds * p
                        tot_q += irds
                    if tot_q > 0:
                        buy_avg = tot_v / tot_q
                    current_price = list(series.values())[-1]
                    break
                except Exception:
                    continue
    except Exception:
        pass

    # 종목 fundamentals
    try:
        from ..backtest.backtest_phase0 import fetch_fundamentals_yf
        funds = fetch_fundamentals_yf(stock_code)
    except Exception:
        funds = {"pbr": None, "roe_pct": None, "debt_to_equity": None, "current_ratio": None}

    # cross-signal — 같은 종목 다른 운용사 동시 진입
    other_actors = set()
    prior_exits = 0
    for m in ms:
        other = normalize_actor_name(m.get("repror", ""))
        if other == actor_norm or not other:
            continue
        ocat = classify_actor(m.get("repror", ""))
        if ocat not in ("activist", "semi_activist", "pe_fund"):
            continue
        other_actors.add(other)
        # 마지막 신고 < 5% 이면 철수
        same_other = [x for x in ms if normalize_actor_name(x.get("repror", "")) == other]
        if same_other:
            last_other = max(same_other, key=lambda x: x.get("rcept_dt", ""))
            if _f(last_other.get("stkrt")) < 5.0 and \
               last_other.get("rcept_dt", "").replace("-", "")[:8] < rcept_dt_norm:
                prior_exits += 1

    # 점수 계산
    score = compute_follow_trade_score(
        actor_category=actor_cat,
        actor_track=actor_track,
        holding_purpose=holding_purpose,
        stkrt_pct=stkrt_pct,
        stkrt_irds=stkrt_irds,
        n_buys_so_far=n_buys,
        n_sells_so_far=n_sells,
        pbr=funds.get("pbr"),
        roe_pct=funds.get("roe_pct"),
        debt_to_equity=funds.get("debt_to_equity"),
        current_ratio=funds.get("current_ratio"),
        current_price=current_price,
        buy_avg_so_far=buy_avg,
        n_other_actors_in_stock=len(other_actors),
        parent_action="neutral",  # Phase 1 단순화
        prior_actor_exits=prior_exits,
    )
    score["meta"] = {
        "actor_norm": actor_norm,
        "actor_category": actor_cat,
        "stkrt_pct": stkrt_pct,
        "stkrt_irds": stkrt_irds,
        "n_buys_prior": n_buys,
        "n_sells_prior": n_sells,
        "buy_avg_won": int(buy_avg) if buy_avg else None,
        "current_price_won": int(current_price) if current_price else None,
        "pbr": funds.get("pbr"),
        "roe_pct": funds.get("roe_pct"),
        "n_other_actors": len(other_actors),
        "prior_exits": prior_exits,
    }
    return score


def render_score_card(score: dict[str, Any]) -> str:
    """score 결과 → Markdown 보고서 fragment."""
    out = []
    out.append(f"### Follow-Trade Score: {score['total']}/100 — **{score['label']}**")
    out.append("")
    out.append("| Component | 점수 | 메모 |")
    out.append("|---|---:|---|")
    for k, full in [("A_actor", "A. 운용사"), ("B_filing", "B. 신고강도"),
                    ("C_fundamentals", "C. 펀더멘털"), ("D_entry", "D. 진입가"),
                    ("E_cross", "E. cross")]:
        out.append(f"| {full} | {score.get(k, 0)} | {score.get('notes', {}).get(k[0], '')} |")
    out.append("")
    m = score.get("meta", {})
    if m:
        if m.get("buy_avg_won") and m.get("current_price_won"):
            gap = (m["current_price_won"] - m["buy_avg_won"]) / m["buy_avg_won"] * 100
            out.append(f"- 매수 가중평균 (이 운용사 prior): {m['buy_avg_won']:,}원, "
                       f"현재가 {m['current_price_won']:,}원, **gap {gap:+.1f}%**")
        if m.get("pbr") is not None:
            out.append(f"- PBR {m['pbr']:.2f} · ROE {m.get('roe_pct', 0):.1f}% · "
                       f"prior buys {m['n_buys_prior']} / sells {m['n_sells_prior']}")
        if m.get("n_other_actors", 0) > 0 or m.get("prior_exits", 0) > 0:
            out.append(f"- cross: peer 운용사 {m['n_other_actors']} 명 동시 진입, "
                       f"철수자 {m['prior_exits']} 명")
    out.append("")
    out.append(f"> 라벨 가이드: <33 PASS 회피 (승률 33%, mean −3%) · 33~40 WATCH "
               f"(승률 58%) · 40+ CONSIDER (승률 65%, median +13%). 5년 155 cycle backtest 기준.")
    return "\n".join(out)
