"""포지션 사이즈 추천 — *내가 얼마 사야 돼?* 답.

Fractional Kelly 변형:
  - 표준 Kelly: f* = (p×b - q) / b  (p=승률, q=패율, b=평균 odds)
  - 우리는 backtest hit15/loss5 + 평균 수익률로 추정
  - 보수적: 0.25 × Kelly (full Kelly 는 *과대 베팅* — 표본 작아서)
  - 상한: 단일 종목 *총 자본의 5%* (포트폴리오 분산)
  - 표본 작으면 (n < 30) 추가 80% 할인

사용:
  from ..workflow.sizing import recommend_size
  rec = recommend_size("브이아이피자산운용", capital_won=100_000_000, price=17390)
  print(rec)  # {kelly_fraction, recommended_pct, recommended_won, shares, ...}
"""
from __future__ import annotations

from ..workflow.dive import ACTOR_BACKTEST, match_actor

# 안전 한도
MAX_SINGLE_POSITION_PCT = 5.0  # 단일 종목 최대 5%
KELLY_FRACTION = 0.25  # full Kelly 의 1/4 (보수적)
SMALL_SAMPLE_THRESHOLD = 30
SMALL_SAMPLE_DISCOUNT = 0.2  # n<30 시 추가 80% 할인


def kelly_fraction(p_win: float, b_avg_win: float, b_avg_loss: float) -> float:
    """Kelly criterion: f* = (p×b - q) / b
       p = 승률, q = 1-p, b = 평균 승리 / 평균 패배 (odds)
    """
    if b_avg_loss <= 0 or p_win <= 0:
        return 0
    q = 1 - p_win
    b = b_avg_win / b_avg_loss
    f = (p_win * b - q) / b
    return max(0, f)


def recommend_size(actor: str, capital_won: float, price: float,
                   *, override_per_share_risk_pct: float = 10.0) -> dict:
    """단일 종목 follow 진입 시 권장 사이즈.

    Args:
        actor: backtest 매칭할 운용사 이름
        capital_won: 총 운용 자본 (원)
        price: 진입가 (원)
        override_per_share_risk_pct: A1 손절 -10% 가정 (변경 가능)

    Returns:
        {actor, hit15, n, p_win, kelly_full, kelly_safe, capped_pct,
         recommended_pct, recommended_won, shares}
    """
    matched_actor, bt = match_actor(actor)
    if not bt:
        return {
            "actor": actor, "matched": False,
            "reason": "backtest 매칭 실패 — 검증된 운용사 아님",
            "recommended_pct": 0, "recommended_won": 0, "shares": 0,
        }

    # 회피 운용사
    if "🔴" in bt["signal"]:
        return {
            "actor": matched_actor, "matched": True, "backtest": bt,
            "reason": f"🔴 회피 운용사 (hit15 {bt['hit15']}%, mean {bt['mean']:+.1f}%)",
            "recommended_pct": 0, "recommended_won": 0, "shares": 0,
        }

    # 승률 p ≈ hit15 (보수적 — IRR>+15% 만 승으로 간주)
    # 평균 승리 = +15% 가정 (hit15 컷오프)
    # 평균 패배 = -10% (A1 손절)
    p_win = bt["hit15"] / 100
    avg_win = 0.15
    avg_loss = override_per_share_risk_pct / 100  # default 0.10
    f_full = kelly_fraction(p_win, avg_win, avg_loss)

    # 보수적: 1/4 Kelly
    f_safe = f_full * KELLY_FRACTION

    # 표본 작으면 추가 할인
    if bt["n"] < SMALL_SAMPLE_THRESHOLD:
        f_safe *= SMALL_SAMPLE_DISCOUNT

    # 상한 적용
    pct = min(f_safe * 100, MAX_SINGLE_POSITION_PCT)

    recommended_won = capital_won * pct / 100
    shares = int(recommended_won / price) if price > 0 else 0

    return {
        "actor": matched_actor, "matched": True, "backtest": bt,
        "p_win": p_win, "avg_win": avg_win, "avg_loss": avg_loss,
        "kelly_full_pct": f_full * 100,
        "kelly_safe_pct": f_safe * 100,
        "capped_pct": pct,
        "recommended_pct": pct,
        "recommended_won": recommended_won,
        "shares": shares,
        "max_loss_won": shares * price * avg_loss,
    }


def render_sizing(rec: dict) -> str:
    """포지션 사이즈 추천 Markdown."""
    o = []
    if not rec.get("matched"):
        o.append(f"⚠️ {rec['reason']}")
        return "\n".join(o)
    bt = rec["backtest"]
    o.append(f"## 📐 포지션 사이즈 추천 — {rec['actor']}")
    o.append("")
    if rec["recommended_pct"] == 0:
        o.append(f"❌ **진입 비추천** — {rec.get('reason','')}")
        return "\n".join(o)
    o.append(f"### Backtest 입력")
    o.append("")
    o.append(f"- 운용사: **{rec['actor']}**")
    o.append(f"- 시그널: {bt['signal']}")
    o.append(f"- hit15 (승률 추정): **{bt['hit15']}%**, n = {bt['n']}")
    o.append(f"- 평균 수익률: {bt['mean']:+.1f}%")
    o.append("")
    o.append(f"### Kelly 계산")
    o.append("")
    o.append(f"- p (승률) = {rec['p_win']*100:.0f}%, avg_win = +{rec['avg_win']*100:.0f}%, avg_loss = -{rec['avg_loss']*100:.0f}%")
    o.append(f"- Full Kelly: **{rec['kelly_full_pct']:.1f}%** (이론치)")
    o.append(f"- 1/4 Kelly (보수적): **{rec['kelly_safe_pct']:.2f}%**")
    if bt["n"] < SMALL_SAMPLE_THRESHOLD:
        o.append(f"  - 표본 작음 (n<{SMALL_SAMPLE_THRESHOLD}) 80% 추가 할인 반영")
    o.append(f"- 단일 종목 상한: **{MAX_SINGLE_POSITION_PCT:.0f}%**")
    o.append("")
    o.append(f"### ✅ 권장 진입")
    o.append("")
    o.append(f"| 항목 | 값 |")
    o.append(f"|---|---:|")
    o.append(f"| 자본 대비 비중 | **{rec['recommended_pct']:.2f}%** |")
    o.append(f"| 진입 금액 | **{rec['recommended_won']:,.0f}원** |")
    o.append(f"| 매수 주수 | **{rec['shares']:,}주** |")
    o.append(f"| 최대 손실 (A1 -10%) | {rec['max_loss_won']:,.0f}원 |")
    o.append("")
    o.append(f"⚠️ Kelly 는 *통계 추정* — 실제 매매는 *§13 사람 검증* 후 결정.")
    return "\n".join(o)
