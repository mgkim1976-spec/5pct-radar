"""Daily 마스터 보고서 — *오늘 한 번 모든 것*.

  radar daily

구성 (단일 파일):
  §1. 🚨 EXIT 알림 (내 포지션 A1 트리거)
  §2. 🏆 진입 우선순위 (opportunities 통합 ranking) ⭐ 핵심
  §3. 🔄 운용사 변동 (어제 → 오늘)
  §4. 📊 운용사 보유 현황 (요약)
  §5. 📡 오늘 신규 5%+ 신고
  §6. 📊 내 포지션 현황
  §7. 📈 통계 + 오늘의 1순위

저장:
  data/daily/daily_<YYYYMMDD>.md         # 마스터
  data/dives/<date>/<code>_<name>.md     # 부속 (자동 dive)
  Obsidian 미러
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR, OBSIDIAN_DIR
from ..workflow.position import list_positions, get_trigger_alerts
from ..workflow.today import fetch_recent_5pct, score_filing
from ..workflow.opportunities import build_opportunities
from ..workflow.holdings import gather_holdings
from ..workflow.movements import detect_movements_from_today

DAILY_DIR = DATA_DIR / "daily"


def build_daily(top_n: int = 15, *, auto_dive: bool = True) -> str:
    today_iso = datetime.now().strftime("%Y-%m-%d %H:%M")
    o: list[str] = []
    o.append(f"# 🎯 5pct-radar Daily — {today_iso}")
    o.append("")
    o.append("> **하루에 한 번 — 모든 것 통합**. EXIT 알림 · 진입 우선순위 · 운용사 변동 · 보유 · 오늘 신고 · 내 포지션.")
    o.append("")
    o.append("---")
    o.append("")

    # §1. EXIT 알림 (가장 시급)
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

    # §2. 진입 우선순위 (opportunities) — 핵심
    o.append("## §2. 🏆 진입 우선순위 (검증 운용사 universe 통합)")
    o.append("")
    print(f"[1/4] opportunities 우선순위 ...")
    opp_md, ranked = build_opportunities(top_n=top_n)
    # opp 본문에서 §1, §2, §3 만 추출 (헤더 한 단계 낮춤)
    opp_lines = opp_md.split("\n")
    start_idx = 0
    for i, line in enumerate(opp_lines):
        if line.startswith("## §1."):
            start_idx = i
            break
    end_idx = len(opp_lines)
    for i, line in enumerate(opp_lines):
        if line.startswith("*Universe:") or line.startswith("*과거"):
            end_idx = i
            break
    for line in opp_lines[start_idx:end_idx]:
        if line.startswith("## §1."):
            line = "### §2.1." + line[6:]
        elif line.startswith("## §2."):
            line = "### §2.2." + line[6:]
        elif line.startswith("## §3."):
            line = "### §2.3." + line[6:]
        elif line.startswith("### "):
            line = "#### " + line[len("### "):]
        o.append(line)
    o.append("")

    # §3. 운용사 변동 + §4. 보유 현황 + 자동 dive
    print(f"[2/4] 운용사 변동 + 보유 + 자동 dive ...")
    holdings_data = gather_holdings()
    movements, _, y_date = detect_movements_from_today(holdings_data["all"])

    # 변동 종목 자동 dive (신규 + 비중 증가)
    auto_dive_count = 0
    if auto_dive and y_date:
        from ..workflow.dive import save_dive
        priority_codes: list[tuple[str, str, str]] = []
        seen = set()
        for actor, b in movements["by_actor"].items():
            for h in b.get("new", []):
                if h["stock_code"] not in seen:
                    priority_codes.append((h["stock_code"], "🆕 신규", actor))
                    seen.add(h["stock_code"])
            for h in b.get("increased", []):
                if h["stock_code"] not in seen:
                    priority_codes.append((h["stock_code"], "⬆️ 증가", actor))
                    seen.add(h["stock_code"])
        # 우선순위 ranking 1~3위도 자동 dive (이미 있으면 skip)
        for r in ranked[:3]:
            if r["stock_code"] not in seen:
                priority_codes.append((r["stock_code"], "🏆 우선순위", r.get("matched_actor") or "—"))
                seen.add(r["stock_code"])
        priority_codes = priority_codes[:10]
        for i, (code, reason, actor) in enumerate(priority_codes, 1):
            print(f"    [{i}/{len(priority_codes)}] auto-dive {code} ({reason})")
            try:
                save_dive(code)
                auto_dive_count += 1
            except Exception as e:
                print(f"      ✗ {e}")

    o.append("## §3. 🔄 운용사 변동 (어제 → 오늘)")
    o.append("")
    if not y_date:
        o.append("*첫 실행 — 변동 추적 내일부터*")
    else:
        o.append(f"비교 기준: 어제 ({y_date}) → 오늘")
        o.append("")
        # summary
        summary = movements.get("summary", {})
        if summary:
            o.append("| 운용사 | 🆕 신규 | 🚪 철수 | ⬆️ 증가 | ⬇️ 감소 |")
            o.append("|---|---:|---:|---:|---:|")
            for actor, s in sorted(summary.items(), key=lambda kv: -kv[1]["n_new"] - kv[1]["n_increased"]):
                if s["n_new"] + s["n_removed"] + s["n_increased"] + s["n_decreased"] == 0:
                    continue
                o.append(f"| {actor} | {s['n_new']} | {s['n_removed']} | {s['n_increased']} | {s['n_decreased']} |")
            o.append("")
        # 신규 진입
        all_new = []
        for actor, b in movements["by_actor"].items():
            for h in b.get("new", []):
                all_new.append({**h, "_actor": actor})
        if all_new:
            o.append("### 🆕 신규 진입 (Top 10)")
            o.append("")
            o.append("| 운용사 | 종목 | 평균매입 | 현재가 | unrealized |")
            o.append("|---|---|---:|---:|---:|")
            for h in sorted(all_new, key=lambda x: -x.get("held_value", 0))[:10]:
                o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                         f"{h['buy_avg']:,.0f} | {h['cur_price']:,.0f} | {h['unrealized_pct']:+.1f}% |")
            o.append("")
        # 비중 증가
        all_inc = []
        for actor, b in movements["by_actor"].items():
            for h in b.get("increased", []):
                all_inc.append({**h, "_actor": actor})
        if all_inc:
            o.append("### ⬆️ 비중 증가 (Top 10)")
            o.append("")
            o.append("| 운용사 | 종목 | 변동% | 현재가 | unrealized |")
            o.append("|---|---|---:|---:|---:|")
            for h in sorted(all_inc, key=lambda x: -x.get("qty_change_pct", 0))[:10]:
                o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                         f"+{h.get('qty_change_pct',0):.1f}% | {h['cur_price']:,.0f} | {h['unrealized_pct']:+.1f}% |")
            o.append("")

    # §4. 운용사 보유 현황
    o.append("## §4. 📊 운용사 보유 현황")
    o.append("")
    by_actor = holdings_data["by_actor"]
    actor_totals = []
    for actor, holdings in by_actor.items():
        total_value = sum(h["held_value"] for h in holdings)
        if total_value > 0:
            weighted_ur = sum(h["unrealized_pct"] * h["held_value"] for h in holdings) / total_value
            weighted_cagr = sum(h["cagr_pct"] * h["held_value"] for h in holdings) / total_value
        else:
            weighted_ur = 0; weighted_cagr = 0
        actor_totals.append({
            "actor": actor, "n": len(holdings),
            "total_value_won": total_value,
            "ur": weighted_ur, "cagr": weighted_cagr,
        })
    actor_totals.sort(key=lambda a: -a["total_value_won"])
    o.append("| 운용사 | 종목 | 총 보유 (억) | 절대 unrealized | **연평균 CAGR** |")
    o.append("|---|---:|---:|---:|---:|")
    for a in actor_totals:
        o.append(f"| **{a['actor']}** | {a['n']} | {a['total_value_won']/1e8:,.0f} | "
                 f"{a['ur']:+.1f}% | **{a['cagr']:+.1f}%** |")
    o.append("")
    o.append(f"*상세*: 보유 종목별 비중·CAGR·매수가는 `data/holdings/<date>/holdings.md` (또는 Obsidian)*")
    o.append("")

    # §5. 오늘 신규 5%+ 신고
    o.append("## §5. 📡 오늘 신규 5%+ 신고")
    o.append("")
    print(f"[3/4] 오늘 신고 ...")
    filings = fetch_recent_5pct(days=1)
    groups: dict[str, list] = {"🟢 STRONG": [], "🟡 MEDIUM": [], "🔴 AVOID": [], "⚪ IGNORE": []}
    for f in filings:
        s = score_filing(f)
        groups[s["priority"]].append({**f, **s})
    for label, items in groups.items():
        if not items or label == "⚪ IGNORE":
            continue
        o.append(f"### {label} ({len(items)}건)")
        o.append("")
        for it in items[:5]:
            corp_name = it.get("corp_name", "?")
            stock_code = it.get("stock_code", "")
            flr = it.get("flr_nm", "?")[:25]
            flags_str = " · ".join(it.get("flags", [])) if it.get("flags") else ""
            o.append(f"- **{corp_name}** ({stock_code}) — {flr} · {flags_str}")
        o.append("")
    n_ignore = len(groups.get("⚪ IGNORE", []))
    if n_ignore:
        o.append(f"*+ ⚪ IGNORE {n_ignore}건 (검증 안 된 actor)*")
        o.append("")

    # §6. 내 포지션
    o.append("## §6. 📊 내 포지션 현황")
    o.append("")
    positions = list_positions()
    if not positions:
        o.append("*(OPEN 포지션 없음 — `radar position add ...` 으로 추가)*")
    else:
        o.append("| 종목 | 진입가 | 현재가 | unrealized | A1 트리거 | follow |")
        o.append("|---|---:|---:|---:|---|---|")
        for p in positions:
            o.append(f"| {p['corp_name']}({p['stock_code']}) | {p['entry_price']:,.0f} | "
                     f"{p['current_price']:,.0f} | {p['unrealized_pct']:+.1f}% | "
                     f"{p['trigger']} | {p.get('actor_followed','-')[:20]} |")
    o.append("")

    # §7. 통계 + 오늘의 1순위
    print(f"[4/4] 통계 + 1순위 ...")
    o.append("## §7. 📈 통계 + 오늘의 1순위")
    o.append("")
    n_strong = len(groups['🟢 STRONG']); n_medium = len(groups['🟡 MEDIUM'])
    n_avoid = len(groups['🔴 AVOID']); n_ig = len(groups['⚪ IGNORE'])
    o.append(f"- 진행 포지션: **{len(positions)}건** · EXIT 알림: {len(alerts)}건")
    o.append(f"- 오늘 신고: {len(filings)}건 (🟢 {n_strong} / 🟡 {n_medium} / 🔴 {n_avoid} / ⚪ {n_ig})")
    o.append(f"- 운용사 universe: **{len(holdings_data['all'])} 보유 종목** + 오늘 신고")
    o.append(f"- 우선순위 shortlist: **{len(ranked)}건** (점수 ≥ 30)")
    if auto_dive_count:
        o.append(f"- 자동 dive 실행: **{auto_dive_count}건** (변동 종목 + 우선순위 Top 3)")
    o.append("")
    if ranked:
        top = ranked[0]
        o.append(f"### 🎯 오늘의 1순위: **{top['corp_name']} ({top['stock_code']})** — {top['total']}/135점")
        o.append("")
        o.append("```bash")
        o.append(f"python -m five_pct_radar dive {top['stock_code']}                  # 전체 보고서")
        actor = top.get('matched_actor', '') or '<actor>'
        o.append(f"python -m five_pct_radar size --actor \"{actor}\" --capital <원> --price {top['cur_price']:.0f}")
        o.append("```")
    o.append("")
    o.append("---")
    o.append("")
    o.append("**부속 데이터 (필요 시):**")
    o.append("- `data/holdings/<date>/holdings.md` — 운용사 변동·보유 상세")
    o.append("- `data/opportunities/opportunities_<YYYYMMDD>.md` — 우선순위 상세")
    o.append("- `data/dives/<date>/<code>_<name>.md` — 종목별 deep dive")
    o.append("- Obsidian Vault: `theme_radar/5pct_radar/`")
    o.append("")
    o.append("*launchd 자동: `평일 09:30 daily / 16:30 holdings / 일요일 03:00 weekly` (`./scheduler/install.sh install`)*")
    o.append("")
    o.append("*과거 backtest + 재무 기반. 미래 보장 없음. 진입 결정은 §13 사람 검증 후.*")

    return "\n".join(o)


def save_daily(top_n: int = 15, *, auto_dive: bool = True, mirror_obsidian: bool = True) -> Path:
    md = build_daily(top_n=top_n, auto_dive=auto_dive)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    today_iso = datetime.now().strftime("%Y-%m-%d")
    path = DAILY_DIR / f"daily_{today_str}.md"
    path.write_text(md, encoding="utf-8")

    if mirror_obsidian:
        obs_daily = OBSIDIAN_DIR / "daily"
        obs_daily.mkdir(parents=True, exist_ok=True)
        (obs_daily / f"{today_iso}.md").write_text(md, encoding="utf-8")
        # 마스터 인덱스
        days = sorted([f.stem for f in obs_daily.glob("*.md")
                       if f.name != "index.md"], reverse=True)
        idx = ["# 📡 5pct-radar Daily — 마스터 인덱스", "",
               f"*총 {len(days)}일 누적.*", "", "## 일별 보고서 (최신 → 과거)"]
        for d in days[:90]:
            idx.append(f"- [[{d}|{d}]]")
        (obs_daily / "index.md").write_text("\n".join(idx) + "\n", encoding="utf-8")

    return path
