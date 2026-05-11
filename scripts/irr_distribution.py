"""Signal 별 IRR 분포 분석 — 재현 가능 스크립트.

사용:
    python scripts/irr_distribution.py [lifecycle.json]

출력: stdout — signal × (P10/P25/median/P75/P90/win/hit15/mean_capped) 표.

10년 lifecycle backtest 의 *대규모 매수 공시 follow* 전략 평가 도구.
docs/STRATEGY_FINDINGS.md 에 정리된 결과의 *raw 통계 재산출* 용.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def irr(raw_pct: float, hold_days: int) -> float | None:
    if not hold_days or hold_days < 30:
        return None
    r = 1 + raw_pct / 100
    if r <= 0:
        return -99.9
    return ((r) ** (365 / hold_days) - 1) * 100


def dist(label: str, items: list[dict]) -> None:
    if not items:
        print(f"{label:<48} 표본 없음")
        return
    irrs = sorted(r["irr"] for r in items)
    n = len(irrs)

    def p(q):
        return irrs[min(int(n * q), n - 1)]

    win = sum(1 for x in irrs if x > 0) / n * 100
    hit15 = sum(1 for x in irrs if x > 15) / n * 100
    cap = [max(min(x, 100), -50) for x in irrs]  # outlier cap [-50, +100]
    mean_cap = sum(cap) / len(cap)
    print(
        f"{label:<48} n={n:>4} "
        f"P10={p(0.10):>+6.1f}% P25={p(0.25):>+6.1f}% "
        f"**med={p(0.50):>+6.1f}%** "
        f"P75={p(0.75):>+6.1f}% P90={p(0.90):>+6.1f}% | "
        f"win={win:>4.0f}% hit15={hit15:>4.0f}% mean(cap)={mean_cap:>+5.1f}%"
    )


def main():
    default = Path(__file__).resolve().parents[1] / "data" / "filing_intel" / "lifecycle_20260512_3650d.json"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    if not path.exists():
        print(f"⚠️ lifecycle JSON 없음: {path}")
        print(f"   → 먼저 `python -m five_pct_radar --lifecycle 3650` 실행")
        sys.exit(1)

    d = json.loads(path.read_text(encoding="utf-8"))
    closed = [
        c for c in d
        if c.get("status") == "CLOSED"
        and c.get("return_pct") is not None
        and c.get("holding_days")
    ]
    rows = []
    for c in closed:
        i = irr(c["return_pct"], c["holding_days"])
        if i is None:
            continue
        rows.append({
            "irr": i,
            "hd": c["holding_days"],
            "nb": c["n_buys"],
            "q": (int(c["entry_date"][4:6]) - 1) // 3 + 1,
            "fp": c["first_pct"],
            "mp": c["max_pct"],
        })

    print(f"\n# Signal × IRR 분포 ({path.name}, n_closed={len(rows)})\n")
    dist("전체 baseline", rows)
    print()
    print("=== 🟢 매수 권장 시그널 ===")
    dist("hd<181 + Q4 진입 (가장 강력)", [r for r in rows if r["hd"] < 181 and r["q"] == 4])
    dist("hd<181 + nb<=2 + fp 5.6~8.8%", [r for r in rows if r["hd"] < 181 and r["nb"] <= 2 and 5.6 <= r["fp"] <= 8.8])
    dist("hd<181 + nb<=2", [r for r in rows if r["hd"] < 181 and r["nb"] <= 2])
    dist("hd<181 (단순 짧은 hold)", [r for r in rows if r["hd"] < 181])
    dist("nb<=2 (단발 매수)", [r for r in rows if r["nb"] <= 2])
    dist("Q4 진입 (10-12월)", [r for r in rows if r["q"] == 4])
    print()
    print("=== 🔴 회피 시그널 ===")
    dist("hd>=724 (장기 hold)", [r for r in rows if r["hd"] >= 724])
    dist("nb>=3 (체계 매집)", [r for r in rows if r["nb"] >= 3])
    dist("hd>=724 AND nb>=3 (행동주의 캠페인 패턴)", [r for r in rows if r["hd"] >= 724 and r["nb"] >= 3])
    dist("Q2 진입 (4-6월)", [r for r in rows if r["q"] == 2])
    print()
    print("# 운영 룰: hit15 = IRR > 15% 적중률. win = 양수 수익률 비율.")
    print("# outlier cap [-50, +100] — heavy-tail 분포라 mean 폭발 방지.")
    print("# 자세한 해석: docs/STRATEGY_FINDINGS.md")


if __name__ == "__main__":
    main()
