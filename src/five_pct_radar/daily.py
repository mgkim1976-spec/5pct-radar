"""Daily 통합 리포트 — *오늘 한 번* 모든 것 통합.

  radar daily              # 모든 섹션 통합
  radar daily --days 3     # 최근 3일 신고 기준

섹션:
  §1. EXIT 알림 (내 포지션 A1 트리거)
  §2. 우선순위 ranking (rank.py 매트릭스)
  §3. 신규 시그널 dashboard (today.py 분류)
  §4. 내 포지션 현황 (position.py)
  §5. 통계 + 다음 단계

저장: data/daily/daily_<YYYYMMDD>.md
Cron: 매일 09:30 / 15:30 권장
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import DATA_DIR
from .position import list_positions, get_trigger_alerts
from .rank import build_rank
from .today import fetch_recent_5pct, score_filing

DAILY_DIR = DATA_DIR / "daily"


def build_daily(days: int = 1, min_score: int = 30, max_dives: int = 10) -> str:
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    o: list[str] = []
    o.append(f"# 🎯 5pct-radar Daily Report — {today_str}")
    o.append("")
    o.append("> *오늘 한 번에* 모든 시그널·포지션·우선순위 통합.")
    o.append("")

    # §1. EXIT 알림
    o.append("## §1. 🚨 EXIT 알림 — 내 포지션 A1 트리거")
    o.append("")
    alerts = get_trigger_alerts()
    if not alerts:
        o.append("*(트리거 도달 포지션 없음)*")
    else:
        for p in alerts:
            o.append(f"- **{p['corp_name']}** ({p['stock_code']}) — {p['trigger']}")
            o.append(f"  - 진입 {p['entry_price']:,.0f}원 → 현재 {p['current_price']:,.0f}원 ({p['unrealized_pct']:+.1f}%)")
    o.append("")

    # §2. 우선순위 ranking (가장 중요)
    o.append("## §2. 🏆 진입 우선순위 (자동 dive + 정량 점수)")
    o.append("")
    rank_md, ranked = build_rank(days=days, min_score=min_score, max_dives=max_dives)
    # rank 헤더 제외, 본문만 + 헤더 한 단계 낮춤
    rank_body_lines = rank_md.split("\n")
    start_idx = 0
    for i, line in enumerate(rank_body_lines):
        if line.startswith("## §1."):
            start_idx = i
            break
    end_idx = len(rank_body_lines)
    for i, line in enumerate(rank_body_lines):
        if line.startswith("*ranking 은"):
            end_idx = i
            break
    # 헤더 한 단계 낮춤 (## §1. → ### §2.1, ## §2. → ### §2.2)
    for line in rank_body_lines[start_idx:end_idx]:
        if line.startswith("## §1."):
            line = "### §2.1. " + line[len("## §1."):].lstrip()
        elif line.startswith("## §2."):
            line = "### §2.2. " + line[len("## §2."):].lstrip()
        elif line.startswith("### "):
            line = "#### " + line[len("### "):]
        o.append(line)
    o.append("")

    # §3. 신규 시그널 dashboard
    o.append("## §3. 📡 신규 시그널 dashboard")
    o.append("")
    filings = fetch_recent_5pct(days=days)
    groups: dict[str, list] = {"🟢 STRONG": [], "🟡 MEDIUM": [], "🔴 AVOID": [], "⚪ IGNORE": []}
    for f in filings:
        s = score_filing(f)
        groups[s["priority"]].append({**f, **s})
    for label, items in groups.items():
        if not items:
            continue
        o.append(f"### {label} ({len(items)}건)")
        o.append("")
        # 그룹당 최대 5
        for it in items[:5]:
            corp_name = it.get("corp_name", "?")
            stock_code = it.get("stock_code", "")
            flr = it.get("flr_nm", "?")[:25]
            flags_str = " · ".join(it["flags"]) if it["flags"] else ""
            o.append(f"- **{corp_name}** ({stock_code}) — {flr} · {flags_str}")
        if len(items) > 5:
            o.append(f"- *... {len(items)-5}건 더 (생략)*")
        o.append("")

    # §4. 내 포지션 현황
    o.append("## §4. 📊 내 포지션 현황")
    o.append("")
    positions = list_positions()
    if not positions:
        o.append("*(OPEN 포지션 없음)*")
    else:
        o.append("| 종목 | 진입가 | 현재가 | unrealized | A1 트리거 | follow |")
        o.append("|---|---:|---:|---:|---|---|")
        for p in positions:
            o.append(f"| {p['corp_name']}({p['stock_code']}) | {p['entry_price']:,.0f} | "
                     f"{p['current_price']:,.0f} | {p['unrealized_pct']:+.1f}% | "
                     f"{p['trigger']} | {p.get('actor_followed','-')[:20]} |")
    o.append("")

    # §5. 통계 + 다음 단계
    o.append("## §5. 📈 통계 + 다음 단계")
    o.append("")
    o.append(f"- 진행 포지션: **{len(positions)}건**")
    o.append(f"- 오늘 신고: {sum(len(v) for v in groups.values())}건 "
             f"(🟢 {len(groups['🟢 STRONG'])} / 🟡 {len(groups['🟡 MEDIUM'])} / "
             f"🔴 {len(groups['🔴 AVOID'])} / ⚪ {len(groups['⚪ IGNORE'])})")
    o.append(f"- ranking shortlist: **{len(ranked)}건** dive 완료")
    o.append(f"- EXIT 트리거: {len(alerts)}건")
    o.append("")
    if ranked:
        top = ranked[0]
        o.append(f"### 🎯 오늘의 1순위: **{top['corp_name']} ({top['stock_code']})** — {top['total_score']}/100점")
        o.append("")
        o.append("```bash")
        o.append(f"# 상세 보고서")
        o.append(f"python -m five_pct_radar dive {top['stock_code']}")
        o.append(f"")
        o.append(f"# 사이즈 추천 (실제 자본 입력)")
        actor = top.get('matched_actor') or top['filer']
        o.append(f"python -m five_pct_radar size --actor \"{actor}\" "
                 f"--capital <원> --price {top['cur_price']:.0f}")
        o.append("```")
    o.append("")
    o.append("---")
    o.append("*매일 한 번 cron 권장: `30 9,15 * * 1-5 cd ~/5pct-radar && python -m five_pct_radar daily`*")
    o.append("")
    o.append("*ranking 은 *과거 backtest + 재무* 기반 정량 점수.* *§13 사람 검증 후 진입.*")

    return "\n".join(o)


def save_daily(days: int = 1, min_score: int = 30, max_dives: int = 10) -> Path:
    md = build_daily(days=days, min_score=min_score, max_dives=max_dives)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    path = DAILY_DIR / f"daily_{today_str}.md"
    path.write_text(md, encoding="utf-8")
    return path
