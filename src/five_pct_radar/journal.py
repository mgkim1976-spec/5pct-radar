"""결정 일지 + 사후 회고.

  radar journal review              # 청산된 포지션 회고 (가설 vs 실제)
  radar journal stats               # 운용사별 / 진입 패턴별 히트율

저장:
  data/positions_closed.json — 청산 기록 (position.py 에서 작성)

회고 형식:
  - 진입 시점 §13 답변 (note 필드)
  - 실제 결과 (return_pct, holding_days)
  - hit15 (>+15%) / loss5 (<-5%) / neutral 분류
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR

CLOSED_FILE = DATA_DIR / "positions_closed.json"


def _load_closed() -> list[dict]:
    if not CLOSED_FILE.exists():
        return []
    try:
        return json.loads(CLOSED_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def render_review() -> str:
    closed = _load_closed()
    if not closed:
        return "(청산된 포지션 없음 — `radar position close <ticker> --price <P>` 으로 청산 기록 시작)"

    out = []
    out.append(f"# 📓 사후 회고 — 청산 {len(closed)}건")
    out.append("")

    # 통계
    n = len(closed)
    wins = [c for c in closed if (c.get("return_pct") or 0) > 0]
    hit15 = [c for c in closed if (c.get("return_pct") or 0) > 15]
    losses5 = [c for c in closed if (c.get("return_pct") or 0) < -5]
    avg_ret = sum((c.get("return_pct") or 0) for c in closed) / n
    avg_hold = sum((c.get("holding_days") or 0) for c in closed) / n

    out.append("## 통계")
    out.append("")
    out.append(f"- 청산 총 {n}건")
    out.append(f"- 승률: **{len(wins)/n*100:.0f}%** ({len(wins)}/{n})")
    out.append(f"- hit15 (>+15%): **{len(hit15)/n*100:.0f}%** ({len(hit15)}/{n})")
    out.append(f"- 손실 5%+ : {len(losses5)/n*100:.0f}% ({len(losses5)}/{n})")
    out.append(f"- 평균 수익률: **{avg_ret:+.1f}%**")
    out.append(f"- 평균 보유: {avg_hold:.0f}일")
    out.append("")

    # follow 한 운용사별 통계
    by_actor: dict[str, list[dict]] = {}
    for c in closed:
        a = c.get("actor_followed") or "(미기록)"
        by_actor.setdefault(a, []).append(c)
    if by_actor:
        out.append("## 운용사 follow 별 성과")
        out.append("")
        out.append("| 운용사 | n | 승률 | hit15 | mean | 평균 hold |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for actor, rows in sorted(by_actor.items(), key=lambda kv: -len(kv[1])):
            nn = len(rows)
            w = sum(1 for r in rows if (r.get("return_pct") or 0) > 0) / nn * 100
            h15 = sum(1 for r in rows if (r.get("return_pct") or 0) > 15) / nn * 100
            mn = sum((r.get("return_pct") or 0) for r in rows) / nn
            ah = sum((r.get("holding_days") or 0) for r in rows) / nn
            out.append(f"| {actor[:25]} | {nn} | {w:.0f}% | {h15:.0f}% | {mn:+.1f}% | {ah:.0f}일 |")
        out.append("")

    # 각 청산 상세
    out.append("## 청산 상세 (최근 → 과거)")
    out.append("")
    for c in sorted(closed, key=lambda x: x.get("exit_date", ""), reverse=True):
        rp = c.get("return_pct") or 0
        emoji = "🟢" if rp > 15 else ("🟡" if rp > 0 else "🔴")
        out.append(f"### {emoji} {c.get('corp_name','?')} ({c.get('stock_code','')}) — {rp:+.1f}% ({c.get('holding_days',0)}일)")
        out.append("")
        out.append(f"- 진입: {c.get('entry_date','')} @ {c.get('entry_price',0):,.0f}원")
        out.append(f"- 청산: {c.get('exit_date','')} @ {c.get('exit_price',0):,.0f}원")
        if c.get("actor_followed"):
            out.append(f"- follow: {c['actor_followed']}")
        if c.get("note"):
            out.append(f"- 진입 시점 가설: {c['note']}")
        if c.get("exit_note"):
            out.append(f"- 청산 시점 회고: {c['exit_note']}")
        out.append("")

    return "\n".join(out)


def save_review() -> Path:
    md = render_review()
    path = DATA_DIR / "journal_review.md"
    path.write_text(md, encoding="utf-8")
    return path
