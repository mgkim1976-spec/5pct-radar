"""우선순위 결정 통합 — Screen → Auto Dive → Rank.

  radar rank
  radar rank --days 3              # 최근 3일 신고
  radar rank --min-score 30        # MEDIUM 이상만
  radar rank --max-dives 10        # 최대 10건만 dive (비용 통제)

흐름:
  1. 최근 N일 5%+ 신고 수집 (today 와 동일)
  2. score_filing → STRONG/MEDIUM/AVOID/IGNORE 분류
  3. STRONG + MEDIUM 자동 dive (gather_dive_data)
  4. 각 후보 정량 점수 계산 (0~100)
  5. 우선순위 표 + 각 1줄 thesis
  6. 저장: data/rank/rank_<YYYYMMDD>.md
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR
from .dive import (
    ACTOR_BACKTEST, gather_dive_data, match_actor, _load_corp_map,
)
from .today import fetch_recent_5pct, score_filing

RANK_DIR = DATA_DIR / "rank"


def score_candidate(dive_data: dict, filing_score: dict) -> dict:
    """단일 종목 정량 점수 (0~100).

    구성:
      - backtest actor (50): hit15%
      - 잠정실적 영업이익 YoY (20)
      - PBR (15)
      - 자기주식 (10)
      - 부채비율 + 가격위치 (5)
    """
    score = 0
    breakdown = {}
    flags: list[str] = []

    # 1. Backtest actor (50점)
    bt_score = 0
    actor_data = dive_data.get("actor_data", {})
    # 가장 큰 매수자 매칭
    matched_actor_name = None
    for actor in actor_data.keys():
        canonical, bt = match_actor(actor)
        if bt:
            matched_actor_name = canonical
            bt_score = bt["hit15"]  # 최대 50 (hit15 가 백분율)
            if "🔴" in bt["signal"]:
                bt_score = -30  # 회피 운용사 페널티
            flags.append(f"{bt['signal']} {canonical} (hit15 {bt['hit15']}%)")
            break
    # filing 자체의 점수도 반영
    if filing_score and filing_score.get("score", 0) >= 60:
        bt_score = max(bt_score, 40)
    # backtest unknown actor 라도 *대안 시그널* 반영
    if bt_score == 0 and filing_score:
        flags_str = " ".join(filing_score.get("flags", []) or [])
        if "외국계 패턴" in flags_str:
            bt_score = 15
            flags.append("🟡 외국계 actor (backtest 없음, +15)")
        if "잠정실적 발표 후" in flags_str:
            bt_score = max(bt_score, 12)
        if "일반보고" in flags_str:
            bt_score = max(bt_score, 5)
        # 본문 파싱한 actor_data 있으면 (검증 안 됨) 약한 보너스
        if actor_data:
            bt_score = max(bt_score, 8)
    breakdown["actor"] = min(50, bt_score)
    score += breakdown["actor"]

    # 2. 잠정실적 영업이익 YoY (20점)
    prelim_score = 0
    prelim_body = dive_data.get("prelim_body", {})
    rows = prelim_body.get("rows", {}) if prelim_body else {}
    op_yoy = None
    if "영업이익" in rows:
        op_yoy = rows["영업이익"].get("yoy_pct", 0)
        if op_yoy >= 50:
            prelim_score = 20
            flags.append(f"🔥 잠정 영업 {op_yoy:+.0f}%")
        elif op_yoy >= 20:
            prelim_score = 15
            flags.append(f"🟢 잠정 영업 {op_yoy:+.0f}%")
        elif op_yoy >= 0:
            prelim_score = 8
            flags.append(f"🟡 잠정 영업 {op_yoy:+.0f}%")
        else:
            prelim_score = 0
            flags.append(f"🔴 잠정 영업 {op_yoy:+.0f}%")
    breakdown["prelim"] = prelim_score
    score += prelim_score

    # 3. PBR (15점)
    pbr_score = 0
    market_cap = dive_data.get("market_cap", 0)
    annual = dive_data.get("annual", {})
    cap_won = annual.get("자본총계", {}).get("thstrm", 0) if annual else 0
    pbr = market_cap / cap_won if cap_won else 999
    if pbr <= 0.5:
        pbr_score = 15
        flags.append(f"💎 PBR {pbr:.2f}")
    elif pbr <= 0.7:
        pbr_score = 10
        flags.append(f"🟢 PBR {pbr:.2f}")
    elif pbr <= 1.0:
        pbr_score = 5
        flags.append(f"🟡 PBR {pbr:.2f}")
    else:
        pbr_score = 0
    breakdown["pbr"] = pbr_score
    score += pbr_score

    # 4. 자기주식 (10점 + bonus)
    tes_score = 0
    tes_pct = dive_data.get("tes_pct")
    tes_acqs = dive_data.get("tes_acqs", 0)
    if tes_pct is not None:
        if tes_pct >= 5:
            tes_score = 10
            flags.append(f"💼 자기주식 {tes_pct:.1f}%")
        elif tes_pct >= 2:
            tes_score = 6
        elif tes_pct >= 1:
            tes_score = 3
    if tes_acqs and dive_data.get("shares"):
        acq_pct = tes_acqs / dive_data["shares"] * 100
        if acq_pct >= 2:
            tes_score += 5
            flags.append(f"🟢 자기주식 +{acq_pct:.1f}%p 매입 진행")
    breakdown["treasury"] = min(15, tes_score)
    score += breakdown["treasury"]

    # 5. 부채비율 (3점)
    debt_score = 0
    debt_total = annual.get("부채총계", {}).get("thstrm", 0) if annual else 0
    cap_total = annual.get("자본총계", {}).get("thstrm", 0) if annual else 0
    debt_ratio = debt_total / cap_total * 100 if cap_total else 999
    if debt_ratio <= 50:
        debt_score = 3
        flags.append(f"🟢 부채 {debt_ratio:.0f}%")
    elif debt_ratio <= 100:
        debt_score = 2
    elif debt_ratio <= 200:
        debt_score = 1
    else:
        debt_score = -2
        flags.append(f"🔴 부채 {debt_ratio:.0f}%")
    breakdown["debt"] = debt_score
    score += debt_score

    # 6. 가격 위치 (2점)
    price_score = 0
    price_info = dive_data.get("price_info", {})
    cur = price_info.get("current", 0)
    low52 = price_info.get("52w_low", 0)
    high52 = price_info.get("52w_high", 0)
    ret6m = price_info.get("6mo_return_pct", 0)
    if cur and low52 and high52:
        position_pct = (cur - low52) / (high52 - low52) * 100 if high52 > low52 else 50
        if position_pct <= 20:
            price_score = 2
            flags.append(f"🟢 52주 low {position_pct:.0f}% 위치")
        elif position_pct <= 50:
            price_score = 1
        elif position_pct >= 95:
            price_score = -1
            flags.append(f"🟡 52주 high {position_pct:.0f}% 위치")
    breakdown["price"] = price_score
    score += price_score

    return {
        "total_score": round(score),
        "breakdown": breakdown,
        "flags": flags,
        "matched_actor": matched_actor_name,
        "pbr": round(pbr, 2) if pbr < 99 else None,
        "op_yoy": round(op_yoy, 1) if op_yoy is not None else None,
        "tes_pct": round(tes_pct, 1) if tes_pct is not None else None,
        "debt_ratio": round(debt_ratio, 0) if debt_ratio < 999 else None,
        "market_cap": round(market_cap, 0),
        "cur_price": cur,
        "6mo_return": round(ret6m, 1),
    }


def build_rank(days: int = 1, min_score: int = 30, max_dives: int = 10,
               include: list[str] | None = None) -> tuple[str, list[dict]]:
    """screen → dive → rank 통합 실행. (markdown, ranked_list)"""
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    o: list[str] = []
    o.append(f"# 🏆 5pct-radar Rank — {today_str}")
    o.append("")
    o.append(f"*최근 {days}일 신고 → 시그널 분류 → 자동 dive → 정량 점수 → 우선순위*")
    o.append("")

    # 1. 신고 수집 + 점수
    print(f"[1/3] 최근 {days}일 5%+ 신고 수집 ...")
    filings = fetch_recent_5pct(days=days)
    print(f"  ✓ {len(filings)}건")

    print(f"[2/3] 시그널 점수화 ...")
    scored = []
    for f in filings:
        s = score_filing(f)
        scored.append({**f, **s})

    # shortlist: score ≥ min_score
    shortlist_codes = set()
    by_code: dict[str, dict] = {}
    for s in scored:
        code = s.get("stock_code", "")
        if not code:
            continue
        if code not in by_code or s["score"] > by_code[code]["score"]:
            by_code[code] = s
    # 자동 shortlist
    for code, s in by_code.items():
        if s["score"] >= min_score:
            shortlist_codes.add(code)
    # --include 강제 포함
    cm = _load_corp_map()
    for code in include or []:
        code = code.strip()
        if not code:
            continue
        shortlist_codes.add(code)
        if code not in by_code:
            # 오늘 신고 없는 종목 — placeholder
            info = cm.get(code, {})
            by_code[code] = {
                "stock_code": code,
                "corp_name": info.get("corp_name", code),
                "flr_nm": "(--include)",
                "score": 0, "priority": "—", "flags": ["📌 사용자 지정"],
                "backtest": None,
            }
    shortlist = [by_code[c] for c in shortlist_codes]
    shortlist.sort(key=lambda s: -s["score"])
    shortlist = shortlist[:max_dives]
    print(f"  ✓ shortlist {len(shortlist)}건 (score ≥ {min_score}, +include {len(include or [])})")

    if not shortlist:
        o.append("*(shortlist 없음 — 검증된 운용사 시그널 0건)*")
        return "\n".join(o), []

    # 3. 각 후보 dive + 정량 점수
    print(f"[3/3] 자동 dive + 정량 점수 (예상 {len(shortlist)*30}~{len(shortlist)*60}초) ...")
    ranked = []
    for i, s in enumerate(shortlist, 1):
        code = s["stock_code"]
        nm = s.get("corp_name", code)
        flr = s.get("flr_nm", "?")
        print(f"\n--- [{i}/{len(shortlist)}] {nm} ({code}) — {flr} ---")
        try:
            d = gather_dive_data(code, verbose=False)
            if "error" in d:
                print(f"  ⚠️ {d['error']}")
                continue
            qs = score_candidate(d, s)
            ranked.append({
                "stock_code": code,
                "corp_name": nm,
                "filer": flr,
                "filing_score": s["score"],
                **qs,
            })
            print(f"  ✓ 정량 점수 {qs['total_score']}/100")
        except Exception as e:
            print(f"  ✗ 오류: {e}")
            continue

    ranked.sort(key=lambda r: -r["total_score"])

    # 4. 보고서 - 매트릭스 형태 (항목별 점수 비교)
    o.append(f"## §1. 우선순위 ranking ({len(ranked)}건)")
    o.append("")
    o.append("### 종합 점수 + 항목별 정량 값")
    o.append("")
    o.append("| # | 종목 | **총점** | PBR | 영업 YoY | 자기주식% | 부채% | 시총(억) | 6M% |")
    o.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for i, r in enumerate(ranked, 1):
        emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else "  "))
        pbr_s = f"{r['pbr']:.2f}" if r["pbr"] is not None else "—"
        op_s = f"{r['op_yoy']:+.0f}%" if r["op_yoy"] is not None else "—"
        tes_s = f"{r['tes_pct']:.1f}%" if r["tes_pct"] is not None else "—"
        debt_s = f"{r['debt_ratio']:.0f}%" if r["debt_ratio"] is not None else "—"
        mc_s = f"{r['market_cap']:,.0f}" if r["market_cap"] else "—"
        ret6m_s = f"{r['6mo_return']:+.0f}%" if r["6mo_return"] is not None else "—"
        o.append(f"| {emoji} {i} | **{r['corp_name']}**({r['stock_code']}) | "
                 f"**{r['total_score']}** | {pbr_s} | {op_s} | {tes_s} | "
                 f"{debt_s} | {mc_s} | {ret6m_s} |")
    o.append("")

    # 항목별 점수 매트릭스
    o.append("### 항목별 점수 매트릭스 (총 100점 만점)")
    o.append("")
    o.append("| # | 종목 | actor (50) | prelim (20) | PBR (15) | 자기주식 (15) | 부채 (3) | 가격 (2) | **총점** |")
    o.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for i, r in enumerate(ranked, 1):
        emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else "  "))
        b = r["breakdown"]
        o.append(f"| {emoji} {i} | {r['corp_name']}({r['stock_code']}) | "
                 f"{b.get('actor',0):>3} | {b.get('prelim',0):>3} | {b.get('pbr',0):>3} | "
                 f"{b.get('treasury',0):>3} | {b.get('debt',0):>3} | {b.get('price',0):>3} | "
                 f"**{r['total_score']}** |")
    o.append("")

    # 시그널 flags 표
    o.append("### 시그널 요약")
    o.append("")
    o.append("| # | 종목 | thesis (flags) |")
    o.append("|---:|---|---|")
    for i, r in enumerate(ranked, 1):
        emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else "  "))
        thesis = " · ".join(r["flags"][:5]) or "—"
        o.append(f"| {emoji} {i} | {r['corp_name']}({r['stock_code']}) | {thesis} |")
    o.append("")

    # 5. 각 종목 상세
    o.append("## §2. 상세 ranking")
    o.append("")
    for i, r in enumerate(ranked, 1):
        o.append(f"### {i}. {r['corp_name']} ({r['stock_code']}) — **{r['total_score']}/100점**")
        o.append("")
        o.append(f"- 보고자: {r['filer']}")
        if r.get("matched_actor"):
            bt = ACTOR_BACKTEST.get(r["matched_actor"], {})
            o.append(f"- backtest: {bt.get('signal','?')} ({r['matched_actor']}, hit15 {bt.get('hit15')}%, n={bt.get('n')})")
        else:
            o.append(f"- backtest: ⚠️ unknown actor (검증 안 됨)")
        o.append(f"- 현재가 {r['cur_price']:,.0f}원, 시총 {r['market_cap']:,.0f}억")
        o.append("")
        o.append(f"**점수 분해:**")
        for k, v in r["breakdown"].items():
            o.append(f"  - {k}: {v}")
        o.append("")
        o.append(f"**시그널:**")
        for f in r["flags"]:
            o.append(f"  - {f}")
        o.append("")
        o.append(f"**다음 단계:**")
        o.append(f"```bash")
        o.append(f"radar dive {r['stock_code']}      # 전체 보고서")
        o.append(f"radar size --actor \"{r.get('matched_actor') or r['filer']}\" --capital 1억 --price {r['cur_price']:.0f}")
        o.append(f"```")
        o.append("")

    o.append("---")
    o.append("")
    o.append("*ranking 은 *과거 backtest + 재무* 기반 정량 점수.* *§13 사람 검증 후 진입.*")

    return "\n".join(o), ranked


def save_rank(days: int = 1, min_score: int = 30, max_dives: int = 10,
              include: list[str] | None = None) -> Path:
    md, ranked = build_rank(days=days, min_score=min_score, max_dives=max_dives, include=include)
    RANK_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    path = RANK_DIR / f"rank_{today_str}.md"
    path.write_text(md, encoding="utf-8")
    # JSON 도 저장 (회고용)
    json_path = RANK_DIR / f"rank_{today_str}.json"
    json_path.write_text(json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
