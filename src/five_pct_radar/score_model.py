"""Follow-Trade Score — *같이 매매할 가치 있는 종목* 점수.

각 5%+ 신고 (또는 lifecycle cycle) 의 *진입 시점 정보* 에 기반해 0~100 점.
4 component (Phase 0 — fundamentals 제외):

  A. 운용사 신뢰도         (가중 25)
  B. 신고 강도             (가중 35)
  D. 진입가 매력도          (가중 20)
  E. Cross-signal          (가중 20)

Phase 1+ 에서 C. 종목 fundamentals 추가 예정 (현재는 표본 부족 위험).

⚠️ Phase 0 의 가중치는 *직관 기반 초안*. 점수 모델 monotonic 검증 후 캘리브레이션.
"""
from __future__ import annotations

from typing import Any


def score_actor(actor_category: str, actor_track: dict[str, Any] | None) -> tuple[float, str]:
    """A. 운용사 신뢰도 (0~25). actor_track = 그 시점 *이전* lifecycle 결과."""
    # 카테고리 기본 점수
    cat_base = {
        "activist": 15.0,
        "semi_activist": 12.0,
        "pe_fund": 10.0,
        "passive": 5.0,
        "corporate": 5.0,
        "individual": 5.0,
        "unknown": 5.0,
    }.get(actor_category, 5.0)

    # track record bonus (표본 ≥ 3 인 경우만)
    track_bonus = 0.0
    note = f"cat={actor_category}"
    if actor_track and actor_track.get("n_closed", 0) >= 3:
        raw_med = actor_track.get("raw_median", 0)
        if raw_med >= 30:
            track_bonus = 10.0
        elif raw_med >= 10:
            track_bonus = 5.0
        elif raw_med <= -20:
            track_bonus = -10.0
        elif raw_med < 0:
            track_bonus = -5.0
        note += f" track_n={actor_track['n_closed']} med={raw_med:+.0f}%"
    else:
        note += " track=neutral(n<3)"

    score = max(0.0, min(25.0, cat_base + track_bonus))
    return score, note


def score_filing(
    holding_purpose: str,
    stkrt_pct: float,
    stkrt_irds: float,
    n_buys_so_far: int,
    n_sells_so_far: int,
) -> tuple[float, str]:
    """B. 신고 강도 (0~35)."""
    s = 0.0
    notes = []

    # 보유 목적 (0~15)
    if holding_purpose == "경영권 영향":
        s += 15.0
        notes.append("purpose=경영권")
    elif holding_purpose == "일반투자":
        s += 8.0
        notes.append("purpose=일반")
    elif holding_purpose == "단순투자":
        s += 3.0
        notes.append("purpose=단순")
    else:
        s += 3.0
        notes.append("purpose=기타")

    # 지분 % (0~10)
    if stkrt_pct >= 15:
        s += 10.0
        notes.append(f"stk={stkrt_pct:.1f}%(대)")
    elif stkrt_pct >= 10:
        s += 7.0
        notes.append(f"stk={stkrt_pct:.1f}%(중)")
    elif stkrt_pct >= 5:
        s += 4.0
        notes.append(f"stk={stkrt_pct:.1f}%(소)")

    # 증감 강도 (0~5)
    if stkrt_irds >= 2:
        s += 5.0
        notes.append(f"Δ={stkrt_irds:+.1f}%p(강매수)")
    elif stkrt_irds >= 1:
        s += 3.0
        notes.append(f"Δ={stkrt_irds:+.1f}%p")
    elif stkrt_irds <= -1:
        s -= 5.0  # 큰 매도는 부정
        notes.append(f"Δ={stkrt_irds:+.1f}%p(매도)")

    # 체계적 매집 history (0~5)
    if n_buys_so_far >= 3 and n_sells_so_far == 0:
        s += 5.0
        notes.append(f"buys={n_buys_so_far}/sells=0(체계)")
    elif n_sells_so_far >= 1:
        s -= 3.0
        notes.append(f"buys={n_buys_so_far}/sells={n_sells_so_far}(trading)")

    return max(0.0, min(35.0, s)), " ".join(notes)


def score_entry_price(
    current_price: float, buy_avg_so_far: float | None
) -> tuple[float, str]:
    """D. 진입가 매력도 (0~20).

    buy_avg_so_far: 운용사의 *지금까지* 매수 가중평균. None 이면 첫 진입 → neutral.
    """
    if buy_avg_so_far is None or buy_avg_so_far <= 0:
        return 10.0, "first-entry(neutral)"

    diff_pct = (current_price - buy_avg_so_far) / buy_avg_so_far * 100

    # 운용사 평균보다 *싸게* 사면 매력 (역설적 LATE_SKEPTICAL)
    # 운용사 평균보다 *비싸게* 사면 추격 비용
    if diff_pct <= -5:
        return 20.0, f"gap={diff_pct:+.1f}%(할인)"
    elif diff_pct <= 2:
        return 17.0, f"gap={diff_pct:+.1f}%(동등)"
    elif diff_pct <= 10:
        return 12.0, f"gap={diff_pct:+.1f}%(추격소)"
    elif diff_pct <= 25:
        return 5.0, f"gap={diff_pct:+.1f}%(추격중)"
    else:
        return 0.0, f"gap={diff_pct:+.1f}%(추격대—reprice 위험)"


def score_cross(
    n_other_actors_in_stock: int,
    parent_action: str,  # "defend" (대주주 방어 강화), "neutral", "absent"
    prior_actor_exits: int,  # 이 종목에서 *철수한* 다른 운용사 수
) -> tuple[float, str]:
    """E. Cross-signal (0~20)."""
    s = 0.0
    notes = []

    # 동시 진입 운용사
    if n_other_actors_in_stock >= 2:
        s += 8.0
        notes.append(f"peer_in={n_other_actors_in_stock}(강합의)")
    elif n_other_actors_in_stock == 1:
        s += 5.0
        notes.append("peer_in=1")
    else:
        s += 3.0
        notes.append("peer_in=0")

    # 대주주 방어 행동 (행동주의 무력화 위험)
    if parent_action == "defend":
        s -= 5.0
        notes.append("parent=defend(thesis위험)")
    else:
        s += 5.0
        notes.append(f"parent={parent_action}")

    # 다른 운용사 *철수* (thesis 깨졌나)
    if prior_actor_exits >= 1:
        s -= 5.0
        notes.append(f"prior_exits={prior_actor_exits}(교대의문)")
    else:
        s += 7.0
        notes.append("prior_exits=0")

    return max(0.0, min(20.0, s)), " ".join(notes)


def compute_follow_trade_score(
    *,
    actor_category: str,
    actor_track: dict[str, Any] | None,
    holding_purpose: str,
    stkrt_pct: float,
    stkrt_irds: float,
    n_buys_so_far: int,
    n_sells_so_far: int,
    current_price: float,
    buy_avg_so_far: float | None,
    n_other_actors_in_stock: int,
    parent_action: str,
    prior_actor_exits: int,
) -> dict[str, Any]:
    """4 component 합산 → 총점 0~100 + 라벨."""
    a, a_note = score_actor(actor_category, actor_track)
    b, b_note = score_filing(holding_purpose, stkrt_pct, stkrt_irds, n_buys_so_far, n_sells_so_far)
    d, d_note = score_entry_price(current_price, buy_avg_so_far)
    e, e_note = score_cross(n_other_actors_in_stock, parent_action, prior_actor_exits)

    total = a + b + d + e

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
        "D_entry": round(d, 1),
        "E_cross": round(e, 1),
        "notes": {
            "A": a_note, "B": b_note, "D": d_note, "E": e_note,
        },
    }
