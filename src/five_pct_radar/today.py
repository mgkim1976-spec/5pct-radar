"""Daily ops dashboard — *오늘 뭐해야 돼?* 1줄 답.

  radar today

표시:
  1. NEW 시그널 — 최근 1일 신규 5%+ 신고 + backtest 운용사 매칭 + 강도 점수
  2. FRESH polarity — 잠정실적 후 30일 내 매수 폭증 (자동 탐지)
  3. EXIT 알림 — 내 OPEN 포지션 A1 트리거 도달
  4. 통계 — 진행 / NEW / EXIT / IGNORE 카운트

저장: data/today/today_<YYYYMMDD>.md
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import CORP_MAP_FILE, DATA_DIR
from .dart_client import dart_get
from .dive import ACTOR_BACKTEST, match_actor
from .position import list_positions, get_trigger_alerts

TODAY_DIR = DATA_DIR / "today"


def fetch_recent_5pct(days: int = 1) -> list[dict]:
    """최근 N일 *모든* 5%+ 대량보유 신고 (KOSPI+KOSDAQ+KONEX)."""
    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    out = []
    page = 1
    while page <= 10:
        j = dart_get("list.json", {
            "bgn_de": bgn, "end_de": end, "pblntf_ty": "D",
            "page_no": page, "page_count": 100,
        })
        if not j or j.get("status") != "000":
            break
        out.extend(j.get("list", []))
        if int(j.get("page_no", 1)) >= int(j.get("total_page", 1)):
            break
        page += 1
    # 5%+ 본 보고서만 (변동 / 임원·주요주주 제외)
    return [f for f in out if "주식등의대량보유" in f.get("report_nm", "")]


def fetch_prelim_history(corp_code: str, days: int = 30) -> list[dict]:
    """잠정실적 공시 (최근 N일)."""
    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    j = dart_get("list.json", {
        "corp_code": corp_code, "bgn_de": bgn, "end_de": end,
        "pblntf_detail_ty": "I001", "page_count": 50,
    })
    if not j or j.get("status") != "000":
        return []
    return [it for it in j.get("list", []) if "잠정" in it.get("report_nm", "")]


def score_filing(filing: dict) -> dict[str, Any]:
    """단일 신고에 시그널 점수 부여.

    +50: 검증된 운용사 (베어링/VIP/신영 최초/한투밸류 최초)
    +30: hit15 ≥ 35% 운용사
    +20: Fresh polarity (잠정실적 발표 후 30일 내 신고)
    -50: 회피 운용사 (에이티넘)
    """
    flr = filing.get("flr_nm", "") or ""
    corp_code = filing.get("corp_code", "")
    score = 0
    flags: list[str] = []

    # 운용사 매칭 (별칭 지원)
    _, backtest = match_actor(flr)

    # 추가 시그널 — backtest unknown actor 라도 패턴으로 잡기
    if not backtest:
        # 외국계 패턴 (Capital, LLC, Fund, Investment, Management)
        foreign_keywords = ["Capital", "LLC", "Fund", "Investment", "Management",
                            "Partners", "Holdings", "Asset", "Hedge"]
        is_foreign = any(kw in flr for kw in foreign_keywords)
        if is_foreign:
            score += 25
            flags.append("🟡 외국계 패턴 (unknown backtest)")
        # 신규 5%+ 신고 (일반) — 처음 진입 또는 ±1%p 변동
        if "(일반)" in (filing.get("report_nm", "") or ""):
            score += 10
            flags.append("📋 일반보고 (신규 또는 변동)")

    if backtest:
        if "🟢 강한 매수" in backtest["signal"]:
            score += 50
            flags.append(f"🟢 강한 매수 운용사 ({backtest['hit15']}%)")
        elif "🟢 매수" in backtest["signal"]:
            score += 40
            flags.append(f"🟢 매수 운용사 ({backtest['hit15']}%)")
        elif "🟡 약한" in backtest["signal"]:
            score += 20
            flags.append(f"🟡 약한 매수 ({backtest['hit15']}%)")
        elif "🔴 회피" in backtest["signal"]:
            score -= 50
            flags.append(f"🔴 회피 운용사 ({backtest['hit15']}%)")

    # Fresh polarity: 잠정실적 발표 후 30일 내 신고
    if corp_code:
        prelims = fetch_prelim_history(corp_code, 30)
        if prelims:
            score += 20
            flags.append(f"🔥 잠정실적 발표 후 30일 내 매수 ({len(prelims)}건)")

    # 시그널 강도
    if score >= 60:
        priority = "🟢 STRONG"
    elif score >= 30:
        priority = "🟡 MEDIUM"
    elif score < 0:
        priority = "🔴 AVOID"
    else:
        priority = "⚪ IGNORE"

    return {
        "score": score,
        "priority": priority,
        "flags": flags,
        "backtest": backtest,
    }


def build_today() -> str:
    """오늘의 dashboard 렌더."""
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    o: list[str] = []
    o.append(f"# 🎯 5pct-radar Today — {today_str}")
    o.append("")
    o.append("## §1. EXIT 알림 — 내 포지션 A1 트리거")
    o.append("")
    alerts = get_trigger_alerts()
    if not alerts:
        o.append("*(트리거 도달 포지션 없음)*")
    else:
        for p in alerts:
            o.append(f"- **{p['corp_name']}** ({p['stock_code']}) — {p['trigger']}")
            o.append(f"  - 진입 {p['entry_price']:,.0f}원 → 현재 {p['current_price']:,.0f}원 ({p['unrealized_pct']:+.1f}%)")
            o.append(f"  - 보유 {p['holding_days']}일, follow {p.get('actor_followed','-')}")
    o.append("")

    # NEW 시그널
    o.append("## §2. NEW 시그널 — 최근 1일 5%+ 신고")
    o.append("")
    print("[1/3] 최근 1일 DART 5%+ 신고 조회 ...")
    filings = fetch_recent_5pct(days=1)
    print(f"  ✓ {len(filings)}건")

    print("[2/3] 시그널 점수화 + 잠정실적 매칭 ...")
    scored = []
    for f in filings:
        s = score_filing(f)
        scored.append({**f, **s})

    # 점수 내림차순
    scored.sort(key=lambda x: -x["score"])

    # priority별 그룹
    groups: dict[str, list] = {"🟢 STRONG": [], "🟡 MEDIUM": [], "🔴 AVOID": [], "⚪ IGNORE": []}
    for s in scored:
        groups[s["priority"]].append(s)

    for label, items in groups.items():
        if not items:
            continue
        o.append(f"### {label} ({len(items)}건)")
        o.append("")
        for it in items[:10]:  # 그룹당 최대 10
            corp_name = it.get("corp_name", "?")
            stock_code = it.get("stock_code", "")
            flr = it.get("flr_nm", "?")[:30]
            report_nm = it.get("report_nm", "")[:40]
            flags_str = " · ".join(it["flags"]) if it["flags"] else ""
            o.append(f"- **{corp_name}** ({stock_code}) — {flr}")
            o.append(f"  - {report_nm}")
            if flags_str:
                o.append(f"  - {flags_str}")
            if it["score"] >= 30 and stock_code:
                o.append(f"  - deep dive: `radar dive {stock_code}`")
        o.append("")

    # 내 포지션 요약
    o.append("## §3. 내 포지션 요약")
    o.append("")
    positions = list_positions()
    if not positions:
        o.append("*(OPEN 포지션 없음)*")
    else:
        o.append("| 종목 | 진입가 | 현재가 | unrealized | 트리거 |")
        o.append("|---|---:|---:|---:|---|")
        for p in positions:
            o.append(f"| {p['corp_name']}({p['stock_code']}) | "
                     f"{p['entry_price']:,.0f} | {p['current_price']:,.0f} | "
                     f"{p['unrealized_pct']:+.1f}% | {p['trigger']} |")
    o.append("")

    # 통계
    print("[3/3] 통계 + 저장 ...")
    o.append("## §4. 통계")
    o.append("")
    o.append(f"- 진행 포지션: {len(positions)}")
    o.append(f"- NEW STRONG: {len(groups['🟢 STRONG'])}")
    o.append(f"- NEW MEDIUM: {len(groups['🟡 MEDIUM'])}")
    o.append(f"- NEW AVOID: {len(groups['🔴 AVOID'])}")
    o.append(f"- NEW IGNORE: {len(groups['⚪ IGNORE'])}")
    o.append(f"- EXIT 트리거: {len(alerts)}")
    o.append("")
    o.append("---")
    o.append("*과거 backtest 기반 시그널 — 미래 보장 없음. 진입 결정은 §13 사람 검증 후.*")

    return "\n".join(o)


def save_today() -> Path:
    md = build_today()
    TODAY_DIR.mkdir(parents=True, exist_ok=True)
    path = TODAY_DIR / f"today_{datetime.now().strftime('%Y%m%d')}.md"
    path.write_text(md, encoding="utf-8")
    return path


def get_alerts_summary() -> str:
    """텔레그램 webhook 용 짧은 요약."""
    alerts = get_trigger_alerts()
    filings = fetch_recent_5pct(days=1)
    strong = [f for f in filings if score_filing(f)["score"] >= 60]

    lines = [f"📡 5pct-radar {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    if strong:
        lines.append(f"\n🟢 STRONG 시그널 ({len(strong)}):")
        for f in strong[:5]:
            lines.append(f"  · {f.get('corp_name','?')} ({f.get('stock_code','')}) — {f.get('flr_nm','?')[:20]}")
    if alerts:
        lines.append(f"\n🚨 EXIT 알림 ({len(alerts)}):")
        for p in alerts[:5]:
            lines.append(f"  · {p['corp_name']} ({p['stock_code']}) — {p['trigger']}")
    if not strong and not alerts:
        lines.append("\n⚪ NEW 시그널 / EXIT 알림 없음")
    return "\n".join(lines)
