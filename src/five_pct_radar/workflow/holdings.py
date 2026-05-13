"""주요 운용사 보유 종목 모니터링.

  radar holdings

8개 검증 운용사 (ACTOR_BACKTEST) 의 OPEN/TRADING cycle 추출:
  - 종목별 평균 매입가 (lifecycle buy_avg_won)
  - 발행주식 × 보유 비율% = 추정 보유 주수
  - 현재가 (yfinance) × 보유 주수 = 보유 금액
  - 운용사 총 보유 금액 대비 비중
  - 평균 매입가 vs 현재가 → unrealized %

리포트 구성:
  §1. 운용사별 총 보유 금액 ranking
  §2. 운용사 × 종목 매트릭스 (각 운용사 holdings)
  §3. 공통 종목 (복수 운용사 보유)
  §4. unrealized 상위 / 하위 종목
  §5. 통계

저장: data/holdings/holdings_<YYYYMMDD>.md
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yfinance as yf

from ..config import CORP_MAP_FILE, DATA_DIR, OBSIDIAN_DIR
from ..workflow.dive import ACTOR_BACKTEST, match_actor, fetch_majorstock, estimate_shares_outstanding

HOLDINGS_DIR = DATA_DIR / "holdings"


def _load_lifecycle(path: Path | None = None) -> list[dict]:
    """가장 최근 lifecycle JSON 로드."""
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    # 자동 탐색
    lc_dir = DATA_DIR / "filing_intel"
    candidates = sorted(lc_dir.glob("lifecycle_*_3650d.json"), reverse=True)
    if not candidates:
        raise SystemExit("⚠️ lifecycle JSON 없음. `python -m five_pct_radar --lifecycle 3650` 먼저")
    return json.loads(candidates[0].read_text(encoding="utf-8"))


_PRICE_CACHE: dict[str, float] = {}
_SHARES_CACHE: dict[str, int] = {}


def _current_price(stock_code: str) -> float:
    if stock_code in _PRICE_CACHE:
        return _PRICE_CACHE[stock_code]
    for suffix in (".KS", ".KQ"):
        try:
            t = yf.Ticker(stock_code + suffix)
            h = t.history(period="5d")
            if len(h) > 0:
                p = float(h["Close"].iloc[-1])
                _PRICE_CACHE[stock_code] = p
                return p
        except Exception:
            continue
    _PRICE_CACHE[stock_code] = 0
    return 0


def _shares_outstanding(corp_code: str) -> int:
    if corp_code in _SHARES_CACHE:
        return _SHARES_CACHE[corp_code]
    ms = fetch_majorstock(corp_code)
    s = estimate_shares_outstanding(ms)
    _SHARES_CACHE[corp_code] = s
    return s


def gather_holdings(lifecycle_path: Path | None = None) -> dict:
    """8개 검증 운용사의 OPEN/TRADING 보유 종목 추출.

    Returns: {
      "by_actor": {actor: [holding_dict, ...]},
      "all": [holding_dict, ...],
    }
    """
    lc = _load_lifecycle(lifecycle_path)
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))

    # OPEN + TRADING 만 + 검증 actor
    active = []
    for c in lc:
        if c.get("status") not in ("OPEN", "TRADING"):
            continue
        actor = c.get("actor", "")
        canonical, bt = match_actor(actor)
        if not bt:
            continue
        # last_pct 가 5% 미만이면 신고 의무 종료 (실제 보유 0이거나 미상)
        if (c.get("last_pct") or 0) < 5.0:
            continue
        # buy_avg_won 유효
        ba = c.get("buy_avg_won")
        if not ba or (isinstance(ba, float) and math.isnan(ba)):
            continue
        active.append({**c, "canonical_actor": canonical})

    print(f"  ✓ active cycles: {len(active)}건 (8개 검증 운용사)")

    # 종목별 발행주식 + 현재가 (캐시)
    unique_codes = list({c["stock_code"] for c in active})
    print(f"  · 발행주식 + 현재가 수집 ({len(unique_codes)} 종목, 캐시) ...")
    for i, code in enumerate(unique_codes, 1):
        info = cm.get(code, {})
        corp_code = info.get("corp_code", "")
        if corp_code:
            _shares_outstanding(corp_code)
        _current_price(code)
        if i % 10 == 0:
            print(f"    {i}/{len(unique_codes)}")

    # 각 cycle 에 금액·수익률 계산
    holdings = []
    for c in active:
        stock_code = c["stock_code"]
        info = cm.get(stock_code, {})
        corp_code = info.get("corp_code", "")
        corp_name = info.get("corp_name", stock_code)
        shares_total = _shares_outstanding(corp_code) if corp_code else 0
        cur_price = _current_price(stock_code)
        last_pct = c["last_pct"]
        held_shares = int(shares_total * last_pct / 100) if shares_total else 0
        held_value = held_shares * cur_price  # 원
        buy_avg = c["buy_avg_won"]
        unrealized_pct = (cur_price / buy_avg - 1) * 100 if buy_avg else 0
        holding_days = c.get("holding_days", 0) or 0
        # 연평균 수익률 (CAGR) — 30일 이상 보유만
        cagr_pct = 0
        if holding_days >= 30 and buy_avg > 0 and cur_price > 0:
            r = cur_price / buy_avg
            if r > 0:
                cagr_pct = (r ** (365 / holding_days) - 1) * 100
            else:
                cagr_pct = -99.9
        holdings.append({
            "actor": c["canonical_actor"],
            "stock_code": stock_code,
            "corp_name": corp_name,
            "buy_avg": buy_avg,
            "cur_price": cur_price,
            "last_pct": last_pct,
            "shares_total": shares_total,
            "held_shares": held_shares,
            "held_value": held_value,
            "unrealized_pct": unrealized_pct,
            "cagr_pct": cagr_pct,
            "entry_date": c.get("entry_date", ""),
            "holding_days": holding_days,
            "status": c.get("status", ""),
            "n_buys": c.get("n_buys", 0),
        })

    by_actor = defaultdict(list)
    for h in holdings:
        by_actor[h["actor"]].append(h)
    return {"by_actor": dict(by_actor), "all": holdings}


def render_holdings(data: dict) -> str:
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    o: list[str] = []
    o.append(f"# 📊 주요 운용사 보유 종목 모니터링 — {today_str}")
    o.append("")
    o.append("> 8개 검증 운용사 (VIP/베어링/신영/한투밸류/라이프/안다/트러스톤/에이티넘) 의")
    o.append("> 현재 *5%+ 신고 진행 중* (OPEN/TRADING) 종목 통합 모니터링.")
    o.append("")
    o.append("**계산 방식:**")
    o.append("- 보유 주수 = 발행주식 × 최근 신고 비율%")
    o.append("- 보유 금액 = 보유 주수 × yfinance 현재가")
    o.append("- unrealized = (현재가 - 가중평균 매입가) / 매입가")
    o.append("")

    by_actor = data["by_actor"]
    all_holdings = data["all"]
    if not all_holdings:
        o.append("*(보유 종목 없음 — 5%+ 신고 진행 중인 cycle 없음)*")
        return "\n".join(o)

    # 1. 운용사별 총 보유 금액 ranking
    o.append("## §1. 운용사별 총 보유 금액 ranking")
    o.append("")
    actor_totals = []
    for actor, holdings in by_actor.items():
        total_value = sum(h["held_value"] for h in holdings)
        if total_value > 0:
            weighted_ur = sum(h["unrealized_pct"] * h["held_value"] for h in holdings) / total_value
            weighted_cagr = sum(h["cagr_pct"] * h["held_value"] for h in holdings) / total_value
            avg_hold = sum(h["holding_days"] * h["held_value"] for h in holdings) / total_value
        else:
            weighted_ur = 0; weighted_cagr = 0; avg_hold = 0
        actor_totals.append({
            "actor": actor,
            "n_holdings": len(holdings),
            "total_value_won": total_value,
            "weighted_unrealized_pct": weighted_ur,
            "weighted_cagr_pct": weighted_cagr,
            "avg_holding_days": avg_hold,
        })
    actor_totals.sort(key=lambda a: -a["total_value_won"])

    o.append("| 운용사 | 보유 종목 | 총 보유금액 (억) | 절대 unrealized | **연평균 CAGR** | 평균 보유일 |")
    o.append("|---|---:|---:|---:|---:|---:|")
    for a in actor_totals:
        o.append(f"| **{a['actor']}** | {a['n_holdings']} | "
                 f"{a['total_value_won']/1e8:,.0f} | "
                 f"{a['weighted_unrealized_pct']:+.1f}% | "
                 f"**{a['weighted_cagr_pct']:+.1f}%** | "
                 f"{a['avg_holding_days']:,.0f}일 |")
    o.append("")

    # 2. 각 운용사 holdings 상세
    o.append("## §2. 운용사별 보유 종목 상세")
    o.append("")
    for a in actor_totals:
        actor = a["actor"]
        holdings = sorted(by_actor[actor], key=lambda h: -h["held_value"])
        total = a["total_value_won"]
        bt = ACTOR_BACKTEST.get(actor, {})
        o.append(f"### {actor} {bt.get('signal','')}")
        o.append("")
        o.append(f"총 보유 {a['n_holdings']} 종목 · 보유금액 **{total/1e8:,.0f}억** · "
                 f"절대 unrealized **{a['weighted_unrealized_pct']:+.1f}%** · "
                 f"**연평균 CAGR {a['weighted_cagr_pct']:+.1f}%** · "
                 f"backtest hit15 {bt.get('hit15','?')}%")
        o.append("")
        if not holdings:
            o.append("*(없음)*")
            continue
        o.append("| 종목 | 평균매입 | 현재가 | 보유금액(억) | 비중% | 절대% | **CAGR** | 보유일 |")
        o.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for h in holdings:
            weight = (h["held_value"] / total * 100) if total > 0 else 0
            ur_emoji = "🟢" if h["unrealized_pct"] > 20 else ("🔴" if h["unrealized_pct"] < -10 else "⚪")
            ed = h["entry_date"]
            ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}" if len(ed) == 8 else ed
            cagr = h.get("cagr_pct", 0)
            o.append(f"| {h['corp_name']}({h['stock_code']}) | "
                     f"{h['buy_avg']:,.0f} | {h['cur_price']:,.0f} | "
                     f"{h['held_value']/1e8:,.0f} | "
                     f"**{weight:.1f}%** | {ur_emoji} **{h['unrealized_pct']:+.1f}%** | "
                     f"**{cagr:+.1f}%** | {h['holding_days']}d |")
        o.append("")

    # 3. 공통 종목 (복수 운용사 보유)
    o.append("## §3. 공통 종목 (복수 운용사 보유)")
    o.append("")
    by_stock = defaultdict(list)
    for h in all_holdings:
        by_stock[h["stock_code"]].append(h)
    common = [(code, hs) for code, hs in by_stock.items() if len(hs) >= 2]
    common.sort(key=lambda kv: -len(kv[1]))
    if not common:
        o.append("*(2개 이상 운용사가 동시 보유한 종목 없음)*")
    else:
        o.append("| 종목 | 보유 운용사 | 종합 보유금액 (억) |")
        o.append("|---|---|---:|")
        for code, hs in common[:15]:
            actors = ", ".join(f"{h['actor']} ({h['unrealized_pct']:+.0f}%)" for h in hs)
            total_v = sum(h["held_value"] for h in hs)
            o.append(f"| {hs[0]['corp_name']}({code}) | {actors} | {total_v/1e8:,.0f} |")
    o.append("")

    # 4. 통계 (winners/losers 섹션 제거)
    o.append("## §4. 통계")
    o.append("")
    total_value = sum(h["held_value"] for h in all_holdings)
    avg_ur = sum(h["unrealized_pct"] * h["held_value"] for h in all_holdings) / total_value if total_value else 0
    avg_cagr = sum(h["cagr_pct"] * h["held_value"] for h in all_holdings) / total_value if total_value else 0
    winners = sum(1 for h in all_holdings if h["unrealized_pct"] > 0)
    o.append(f"- 8개 운용사 총 보유: **{total_value/1e8:,.0f}억원** ({len(all_holdings)}건)")
    o.append(f"- 절대 unrealized (가중평균): **{avg_ur:+.1f}%**")
    o.append(f"- **연평균 CAGR (가중평균): {avg_cagr:+.1f}%** ⭐")
    o.append(f"- 종목 단위 승률: **{winners/len(all_holdings)*100:.0f}%** ({winners}/{len(all_holdings)})")
    o.append(f"- 공통 보유 종목: {len(common)}개")
    o.append("")
    o.append("---")
    o.append("")
    o.append("*보유 주수는 발행주식 × 신고 비율% 추정. 5% 미만 줄인 후 추가 매도는 신고 의무 없음 → 실제 보유 ≤ 추정.*")
    o.append("")
    o.append("*과거 데이터 기반. 미래 보장 없음. 진입·청산 결정은 §13 사람 검증 후.*")

    return "\n".join(o)


def render_daily_combined(data: dict, movements: dict,
                           today_iso: str, y_date: str) -> str:
    """변동 + 보유 통합 데일리 보고서 (단일 파일).

    구성:
      §1-5. 변동 (어제 → 오늘) — 가장 중요
      §6-9. 보유 현황 — 참조용
    """
    # movements md 본문 (헤더 + > quote + --- 모두 제외)
    from ..workflow.movements import render_movements
    movements_md = render_movements(movements, today_iso, y_date)
    movements_lines = movements_md.split("\n")
    m_start = 0
    for i, line in enumerate(movements_lines):
        # 본문은 "## §" 또는 "*(" 부터 시작
        if line.startswith("## §") or line.startswith("*("):
            m_start = i
            break
    # 첫 실행 (변동 없음) 시 quote 도 포함
    if m_start == 0:
        for i, line in enumerate(movements_lines):
            if line.startswith("> ⚠️") or line.startswith("> 어제"):
                m_start = i
                break
    movements_body = "\n".join(movements_lines[m_start:])
    # ## §N → ## §N (그대로 유지, *변동* 섹션이 먼저)

    # holdings md 본문 (헤더 제외) — 번호 §1→§6, §2→§7, §3→§8, §4→§9
    holdings_md = render_holdings(data)
    holdings_lines = holdings_md.split("\n")
    h_start = 0
    for i, line in enumerate(holdings_lines):
        if line.startswith("## §"):
            h_start = i
            break
    holdings_body_lines = holdings_lines[h_start:]
    # §1 → §6, §2 → §7, §3 → §8, §4 → §9
    renumber = {"## §1.": "## §6.", "## §2.": "## §7.",
                "## §3.": "## §8.", "## §4.": "## §9."}
    renumbered = []
    for line in holdings_body_lines:
        for old, new in renumber.items():
            if line.startswith(old):
                line = new + line[len(old):]
                break
        renumbered.append(line)
    holdings_body = "\n".join(renumbered)

    # 헤더
    o = []
    o.append(f"# 📊 운용사 Daily 보고서 — {today_iso}")
    o.append("")
    o.append("> 8개 검증 운용사 (VIP/베어링/신영/한투밸류/라이프/안다/트러스톤/에이티넘) 의")
    o.append("> *어제 vs 오늘 변동* + *현재 보유 종목 + 금액 + 비중 + CAGR* 통합.")
    o.append("")
    if y_date:
        o.append(f"**비교 기준**: 어제 ({y_date}) → 오늘 ({today_iso})")
    else:
        o.append(f"**비교 기준**: 첫 실행 — 변동 추적 내일부터")
    o.append("")
    o.append("**계산 방식:**")
    o.append("- 보유 주수 = 발행주식 × 최근 신고 비율%")
    o.append("- 보유 금액 = 보유 주수 × yfinance 현재가")
    o.append("- unrealized = (현재가 - 가중평균 매입가) / 매입가")
    o.append("- CAGR = ((1 + return) ^ (365/holding_days) - 1) × 100")
    o.append("")
    o.append("---")
    o.append("")
    o.append("# Part 1. 🔄 어제 → 오늘 변동")
    o.append("")
    o.append(movements_body)
    o.append("")
    o.append("---")
    o.append("")
    o.append("# Part 2. 📊 현재 보유 현황")
    o.append("")
    o.append(holdings_body)

    return "\n".join(o)


def save_holdings(lifecycle_path: Path | None = None, *,
                  auto_dive: bool = True, mirror_obsidian: bool = True) -> Path:
    """holdings + movements + 자동 dive 통합 워크플로.

    Args:
        lifecycle_path: lifecycle JSON 경로 (None 시 자동 탐색)
        auto_dive: 변동 종목 (신규/증가) 자동 dive 실행 여부
        mirror_obsidian: Obsidian iCloud 폴더에도 미러 저장
    """
    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y%m%d")
    today_iso = today_dt.strftime("%Y-%m-%d")

    print(f"[1/5] 운용사 보유 데이터 수집 ...")
    data = gather_holdings(lifecycle_path)

    print(f"[2/5] daily 변동 (어제 vs 오늘) ...")
    from ..workflow.movements import detect_movements_from_today
    movements, _, y_date = detect_movements_from_today(data["all"])
    if y_date:
        n_new = sum(len(b.get("new", [])) for b in movements["by_actor"].values())
        n_removed = sum(len(b.get("removed", [])) for b in movements["by_actor"].values())
        n_inc = sum(len(b.get("increased", [])) for b in movements["by_actor"].values())
        n_dec = sum(len(b.get("decreased", [])) for b in movements["by_actor"].values())
        print(f"  ✓ 변동: 신규 {n_new}, 철수 {n_removed}, 증가 {n_inc}, 감소 {n_dec}")
    else:
        print(f"  · 어제 데이터 없음 — 첫 실행, 내일부터 변동 추적")

    print(f"[3/5] 통합 데일리 보고서 (변동 + 보유) ...")
    HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    daily_md = render_daily_combined(data, movements, today_iso, y_date)
    daily_path = HOLDINGS_DIR / f"holdings_{today_str}.md"
    daily_path.write_text(daily_md, encoding="utf-8")
    # JSON 백업 (회고용)
    json_path = HOLDINGS_DIR / f"holdings_{today_str}.json"
    json_path.write_text(json.dumps(data["all"], ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")

    # [4/5] 변동 종목 자동 dive
    auto_dive_results = []
    if auto_dive and y_date:
        print(f"[4/5] 변동 종목 자동 dive ...")
        from ..workflow.dive import save_dive
        # 우선순위: 신규 > 비중 증가
        priority_codes: list[tuple[str, str, str]] = []  # (code, reason, actor)
        seen = set()
        for actor, b in movements["by_actor"].items():
            for h in b.get("new", []):
                if h["stock_code"] not in seen:
                    priority_codes.append((h["stock_code"], "🆕 신규 진입", actor))
                    seen.add(h["stock_code"])
            for h in b.get("increased", []):
                if h["stock_code"] not in seen:
                    qty_pct = h.get("qty_change_pct", 0)
                    priority_codes.append((h["stock_code"],
                                           f"⬆️ {qty_pct:+.0f}% 추가", actor))
                    seen.add(h["stock_code"])
        # 최대 10건
        priority_codes = priority_codes[:10]
        for i, (code, reason, actor) in enumerate(priority_codes, 1):
            print(f"  [{i}/{len(priority_codes)}] dive {code} ({reason}, {actor})")
            try:
                dp = save_dive(code)
                auto_dive_results.append({"code": code, "reason": reason,
                                          "actor": actor, "path": dp})
            except Exception as e:
                print(f"    ✗ 오류: {e}")
        if not priority_codes:
            print(f"  · 변동 종목 없음 — dive skip")
    else:
        if not y_date:
            print(f"[4/5] 첫 실행 — auto-dive skip")
        else:
            print(f"[4/5] auto-dive 비활성")

    # [5/5] 날짜별 미러 폴더 (data/holdings/<date>/ + Obsidian)
    # 두 곳에 동일한 구조로 저장
    print(f"[5/5] 날짜별 폴더 미러 ...")
    idx_md = _build_obsidian_index(today_iso, data, movements, auto_dive_results)

    def _mirror_to(base: Path) -> None:
        base.mkdir(parents=True, exist_ok=True)
        day_dir = base / today_iso
        day_dir.mkdir(parents=True, exist_ok=True)
        # 통합 데일리 (movements + holdings 결합)
        (day_dir / "holdings.md").write_text(daily_md, encoding="utf-8")
        # 자동 dive 는 dives/ 에 이미 저장됨 — 여기서는 복사 안 함
        # (holdings.md 안에 dive 링크 표시)
        (day_dir / "_index.md").write_text(idx_md, encoding="utf-8")
        _update_master_index(base)
        print(f"  ✓ {day_dir}")

    # data/holdings/<date>/ — 로컬 미러 (항상)
    _mirror_to(HOLDINGS_DIR)

    # Obsidian iCloud — 옵션
    if mirror_obsidian:
        _mirror_to(OBSIDIAN_DIR)

    return daily_path


def _build_obsidian_index(today_iso: str, data: dict, movements: dict,
                          auto_dive_results: list) -> str:
    """Obsidian 일별 인덱스 — 모든 보고서 링크."""
    o: list[str] = []
    o.append(f"# 📡 5pct-radar — {today_iso}")
    o.append("")
    o.append(f"*Obsidian Vault 자동 생성. 출처: 5pct-radar v0.1.0*")
    o.append("")
    o.append("## 📄 오늘의 보고서")
    o.append("")
    o.append(f"- [[holdings|📊 운용사 Daily 보고서 (변동 + 보유)]]")
    o.append(f"- [[../dives/_index|🔍 종목 deep dive 인덱스]]")
    if auto_dive_results:
        o.append("")
        o.append("### 🔍 자동 dive (변동 종목)")
        o.append("")
        for r in auto_dive_results:
            # 종목명 포함된 새 파일명 (예: 039830_오로라)
            stem = r["path"].stem
            o.append(f"- [[../dives/{today_iso}/{stem}|{r['reason']} {stem}]] — {r['actor']}")
    o.append("")
    o.append("## 📈 빠른 통계")
    o.append("")
    all_h = data["all"]
    if all_h:
        total = sum(h["held_value"] for h in all_h)
        avg_ur = sum(h["unrealized_pct"] * h["held_value"] for h in all_h) / total if total else 0
        o.append(f"- 8개 운용사 총 보유: **{total/1e8:,.0f}억원** ({len(all_h)} cycle)")
        o.append(f"- 가중평균 unrealized: **{avg_ur:+.1f}%**")
    if movements.get("by_actor"):
        n_new = sum(len(b.get("new", [])) for b in movements["by_actor"].values())
        n_removed = sum(len(b.get("removed", [])) for b in movements["by_actor"].values())
        n_inc = sum(len(b.get("increased", [])) for b in movements["by_actor"].values())
        o.append(f"- 어제→오늘 변동: 🆕 {n_new} / 🚪 {n_removed} / ⬆️ {n_inc}")
    o.append("")
    o.append("---")
    o.append("")
    o.append(f"*자동 cron: `30 16 * * 1-5 cd ~/5pct-radar && python -m five_pct_radar holdings`*")
    return "\n".join(o)


def _update_master_index(obs_dir: Path) -> None:
    """OBSIDIAN_DIR/index.md — 모든 일별 폴더 링크 (최신 → 과거)."""
    days = sorted([d.name for d in obs_dir.iterdir()
                   if d.is_dir() and not d.name.startswith(".")],
                  reverse=True)
    lines = ["# 📡 5pct-radar — Obsidian Index", "",
             f"*Auto-generated. 총 {len(days)}일 누적.*", "",
             "## 일별 리포트"]
    for d in days[:90]:  # 최대 90일
        lines.append(f"- [[{d}/_index|{d}]]")
    (obs_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
