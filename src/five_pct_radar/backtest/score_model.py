"""Follow-Trade Score — *같이 매매할 가치 있는 종목* 점수.

각 5%+ 신고 (또는 lifecycle cycle) 의 *진입 시점 정보* 에 기반해 0~100 점.
5 component (Phase 1):

  A. 운용사 신뢰도         (가중 20)
  B. 신고 강도             (가중 30)
  C. 종목 fundamentals     (가중 25)  ← Phase 1 추가
  D. 진입가 매력도          (가중 15)
  E. Cross-signal          (가중 10)

⚠️ Phase 1 의 *C fundamentals* 는 yfinance .info (현재 값) 사용 — *look-ahead*
일부 포함. 진정한 point-in-time 은 Phase 2 (DART historical financials).
"""
from __future__ import annotations

from typing import Any


def score_actor(actor_category: str, actor_track: dict[str, Any] | None) -> tuple[float, str]:
    """A. 운용사 신뢰도 (0~20). actor_track = 그 시점 *이전* lifecycle 결과."""
    # 카테고리 기본 점수 (max 12)
    cat_base = {
        "activist": 12.0,
        "semi_activist": 10.0,
        "pe_fund": 8.0,
        "passive": 4.0,
        "corporate": 4.0,
        "individual": 4.0,
        "unknown": 4.0,
    }.get(actor_category, 4.0)

    # track record bonus (표본 ≥ 3 인 경우만), max ±8
    track_bonus = 0.0
    note = f"cat={actor_category}"
    if actor_track and actor_track.get("n_closed", 0) >= 3:
        raw_med = actor_track.get("raw_median", 0)
        if raw_med >= 30:
            track_bonus = 8.0
        elif raw_med >= 10:
            track_bonus = 4.0
        elif raw_med <= -20:
            track_bonus = -8.0
        elif raw_med < 0:
            track_bonus = -4.0
        note += f" track_n={actor_track['n_closed']} med={raw_med:+.0f}%"
    else:
        note += " track=neutral(n<3)"

    score = max(0.0, min(20.0, cat_base + track_bonus))
    return score, note


def score_filing(
    holding_purpose: str,
    stkrt_pct: float,
    stkrt_irds: float,
    n_buys_so_far: int,
    n_sells_so_far: int,
) -> tuple[float, str]:
    """B. 신고 강도 (0~30)."""
    s = 0.0
    notes = []

    # 보유 목적 (0~13)
    if holding_purpose == "경영권 영향":
        s += 13.0
        notes.append("purpose=경영권")
    elif holding_purpose == "일반투자":
        s += 7.0
        notes.append("purpose=일반")
    elif holding_purpose == "단순투자":
        s += 3.0
        notes.append("purpose=단순")
    else:
        s += 3.0
        notes.append("purpose=기타")

    # 지분 % (0~8)
    if stkrt_pct >= 15:
        s += 8.0
        notes.append(f"stk={stkrt_pct:.1f}%(대)")
    elif stkrt_pct >= 10:
        s += 6.0
        notes.append(f"stk={stkrt_pct:.1f}%(중)")
    elif stkrt_pct >= 5:
        s += 3.0
        notes.append(f"stk={stkrt_pct:.1f}%(소)")

    # 증감 강도 (0~5)
    if stkrt_irds >= 2:
        s += 5.0
        notes.append(f"Δ={stkrt_irds:+.1f}%p(강매수)")
    elif stkrt_irds >= 1:
        s += 3.0
        notes.append(f"Δ={stkrt_irds:+.1f}%p")
    elif stkrt_irds <= -1:
        s -= 5.0
        notes.append(f"Δ={stkrt_irds:+.1f}%p(매도)")

    # 체계적 매집 history (0~4)
    if n_buys_so_far >= 3 and n_sells_so_far == 0:
        s += 4.0
        notes.append(f"buys={n_buys_so_far}/sells=0(체계)")
    elif n_sells_so_far >= 1:
        s -= 3.0
        notes.append(f"buys={n_buys_so_far}/sells={n_sells_so_far}(trading)")

    return max(0.0, min(30.0, s)), " ".join(notes)


def score_fundamentals(
    pbr: float | None,
    roe_pct: float | None,
    debt_to_equity: float | None = None,
    current_ratio: float | None = None,
) -> tuple[float, str]:
    """C. 종목 fundamentals (0~25).

    ⚠️ Phase 1: yfinance .info 의 *현재* 값 사용 (look-ahead 일부 포함).
    *진정한 point-in-time* 은 DART historical 사업보고서로 Phase 2 에서 fix.

    가치투자 + 행동주의 관점 점수:
      - PBR 낮을수록 (저PBR = activist target)
      - ROE 적당히 양수 (너무 낮으면 trap, 너무 높으면 이미 reprice)
      - 부채 적정 (debt/equity < 1)
      - 유동성 충분 (current ratio > 1)
    """
    s = 0.0
    notes = []

    # PBR (0~12)
    if pbr is None or pbr <= 0:
        s += 4.0
        notes.append("pbr=?")
    elif pbr < 0.5:
        s += 12.0
        notes.append(f"pbr={pbr:.2f}(극저)")
    elif pbr < 0.8:
        s += 10.0
        notes.append(f"pbr={pbr:.2f}(저)")
    elif pbr < 1.2:
        s += 6.0
        notes.append(f"pbr={pbr:.2f}(중)")
    elif pbr < 2.0:
        s += 3.0
        notes.append(f"pbr={pbr:.2f}(고)")
    else:
        s += 0.0
        notes.append(f"pbr={pbr:.2f}(극고)")

    # ROE (0~9)
    if roe_pct is None:
        s += 3.0
        notes.append("roe=?")
    elif roe_pct >= 15:
        s += 9.0
        notes.append(f"roe={roe_pct:.1f}%(우수)")
    elif roe_pct >= 8:
        s += 7.0
        notes.append(f"roe={roe_pct:.1f}%(양호)")
    elif roe_pct >= 0:
        s += 4.0
        notes.append(f"roe={roe_pct:.1f}%(저)")
    else:
        s -= 3.0
        notes.append(f"roe={roe_pct:.1f}%(적자)")

    # 부채비율 (0~2)
    if debt_to_equity is not None:
        if debt_to_equity < 0.5:
            s += 2.0
            notes.append(f"d/e={debt_to_equity:.1f}(저부채)")
        elif debt_to_equity < 1.0:
            s += 1.0
            notes.append(f"d/e={debt_to_equity:.1f}(적정)")
        elif debt_to_equity > 2.0:
            s -= 2.0
            notes.append(f"d/e={debt_to_equity:.1f}(과부채)")

    # 유동성 (0~2)
    if current_ratio is not None:
        if current_ratio > 2.0:
            s += 2.0
            notes.append(f"cr={current_ratio:.1f}(우수)")
        elif current_ratio > 1.0:
            s += 1.0
            notes.append(f"cr={current_ratio:.1f}(양호)")
        elif current_ratio < 0.8:
            s -= 2.0
            notes.append(f"cr={current_ratio:.1f}(위험)")

    return max(0.0, min(25.0, s)), " ".join(notes)


def score_entry_price(
    current_price: float, buy_avg_so_far: float | None
) -> tuple[float, str]:
    """D. 진입가 매력도 (0~15).

    buy_avg_so_far: 운용사의 *지금까지* 매수 가중평균. None 이면 첫 진입 → neutral.
    """
    if buy_avg_so_far is None or buy_avg_so_far <= 0:
        return 8.0, "first-entry(neutral)"

    diff_pct = (current_price - buy_avg_so_far) / buy_avg_so_far * 100

    if diff_pct <= -5:
        return 15.0, f"gap={diff_pct:+.1f}%(할인)"
    elif diff_pct <= 2:
        return 13.0, f"gap={diff_pct:+.1f}%(동등)"
    elif diff_pct <= 10:
        return 9.0, f"gap={diff_pct:+.1f}%(추격소)"
    elif diff_pct <= 25:
        return 4.0, f"gap={diff_pct:+.1f}%(추격중)"
    else:
        return 0.0, f"gap={diff_pct:+.1f}%(추격대—reprice 위험)"


def score_cross(
    n_other_actors_in_stock: int,
    parent_action: str,
    prior_actor_exits: int,
) -> tuple[float, str]:
    """E. Cross-signal (0~10)."""
    s = 0.0
    notes = []

    # 동시 진입 운용사 (0~4)
    if n_other_actors_in_stock >= 2:
        s += 4.0
        notes.append(f"peer_in={n_other_actors_in_stock}(강합의)")
    elif n_other_actors_in_stock == 1:
        s += 2.5
        notes.append("peer_in=1")
    else:
        s += 1.0
        notes.append("peer_in=0")

    # 대주주 방어 (0~3)
    if parent_action == "defend":
        s -= 2.0
        notes.append("parent=defend(thesis위험)")
    else:
        s += 3.0
        notes.append(f"parent={parent_action}")

    # 다른 운용사 *철수* (0~3)
    if prior_actor_exits >= 1:
        s -= 2.0
        notes.append(f"prior_exits={prior_actor_exits}(교대의문)")
    else:
        s += 3.0
        notes.append("prior_exits=0")

    return max(0.0, min(10.0, s)), " ".join(notes)


def compute_follow_trade_score(
    *,
    actor_category: str,
    actor_track: dict[str, Any] | None,
    holding_purpose: str,
    stkrt_pct: float,
    stkrt_irds: float,
    n_buys_so_far: int,
    n_sells_so_far: int,
    pbr: float | None,
    roe_pct: float | None,
    debt_to_equity: float | None = None,
    current_ratio: float | None = None,
    current_price: float = 0.0,
    buy_avg_so_far: float | None = None,
    n_other_actors_in_stock: int = 0,
    parent_action: str = "neutral",
    prior_actor_exits: int = 0,
) -> dict[str, Any]:
    """5 component 합산 → 총점 0~100 + 라벨."""
    a, a_note = score_actor(actor_category, actor_track)
    b, b_note = score_filing(holding_purpose, stkrt_pct, stkrt_irds, n_buys_so_far, n_sells_so_far)
    c, c_note = score_fundamentals(pbr, roe_pct, debt_to_equity, current_ratio)
    d, d_note = score_entry_price(current_price, buy_avg_so_far)
    e, e_note = score_cross(n_other_actors_in_stock, parent_action, prior_actor_exits)

    total = a + b + c + d + e

    if total >= 75:
        label = "STRONG_BUY"
    elif total >= 55:
        label = "CONSIDER"
    elif total >= 35:
        label = "WATCH"
    else:
        label = "PASS"

    return {
        "total": round(total, 1),
        "label": label,
        "A_actor": round(a, 1),
        "B_filing": round(b, 1),
        "C_fundamentals": round(c, 1),
        "D_entry": round(d, 1),
        "E_cross": round(e, 1),
        "notes": {
            "A": a_note, "B": b_note, "C": c_note, "D": d_note, "E": e_note,
        },
    }
