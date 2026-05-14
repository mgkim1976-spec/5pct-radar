"""스크리닝 확장 — 검증 운용사 OPEN 종목 + 오늘 신고 통합 ranking.

  radar opportunities                  # 매일 권장 — 경량 ranking
  radar opportunities --deep           # 상위 후보 dive 까지 (~10분)
  radar opportunities --top 20         # 상위 N (기본 15)

점수 (총 145점):
  - actor backtest    (40): 검증 운용사 hit15
  - 매매 신선도        (10): 최근 매수 ≤ 30일 / 차익거래 -15
  - **follow 적기**   (10): 현재가 vs 운용사 매수 가중평균
  - 잠정 영업 YoY     (15): +50% 15 / +20% 10 / +0% 5
  - 분기 가속          (15): 연간 vs 1Q 가속도
  - 매출 YoY          (10): +30% 10 / +10% 5
  - NAV 조정 PBR      (15): ≤0.4 15 / ≤0.6 10 / ≤0.8 5
  - 자기주식          (15): 보유% + 매입 진행 + 소각 결정
  - consensus         (10): 검증 운용사 2+ 동시 보유 = 10
  - 부채비율          (3) / 가격 위치 (2)
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yfinance as yf

from ..config import CORP_MAP_FILE, DATA_DIR, OBSIDIAN_DIR
from ..core.dart_client import dart_get
from ..workflow.dive import (
    ACTOR_BACKTEST, match_actor, fetch_majorstock,
    estimate_shares_outstanding, fetch_treasury_shares,
    fetch_prelim_with_data, latest_annual_financials, parse_financials,
    latest_quarterly_financials,
)

OPP_DIR = DATA_DIR / "opportunities"


def _load_holdings_latest() -> list[dict]:
    """가장 최근 holdings JSON."""
    hd = DATA_DIR / "holdings"
    if not hd.exists():
        return []
    candidates = sorted(hd.glob("holdings_*.json"), reverse=True)
    if not candidates:
        return []
    return json.loads(candidates[0].read_text(encoding="utf-8"))


def gather_universe(include_today: bool = True) -> dict[str, dict]:
    """검증 운용사 OPEN 보유 종목 + 오늘 신고 통합 universe.

    Returns: {stock_code: {actors:[...], today_filing:{...} or None}}
    """
    universe: dict[str, dict] = {}
    # 1) holdings (운용사 OPEN)
    for h in _load_holdings_latest():
        code = h["stock_code"]
        if code not in universe:
            universe[code] = {"actors": [], "today_filing": None,
                              "holding_dates": [], "buy_avgs": [], "held_values": []}
        universe[code]["actors"].append(h["actor"])
        if h.get("entry_date"):
            universe[code]["holding_dates"].append(h["entry_date"])
        if h.get("buy_avg"):
            universe[code]["buy_avgs"].append(h["buy_avg"])
            universe[code]["held_values"].append(h.get("held_value", 0))

    # 2) 오늘 신고
    if include_today:
        from ..workflow.today import fetch_recent_5pct
        for f in fetch_recent_5pct(days=1):
            code = f.get("stock_code")
            if not code:
                continue
            if code not in universe:
                universe[code] = {"actors": [], "today_filing": f,
                                  "holding_dates": [], "buy_avgs": []}
            else:
                universe[code]["today_filing"] = f

    return universe


def _yfinance_6m(stock_code: str, cache: dict) -> dict | None:
    if stock_code in cache:
        return cache[stock_code]
    for suffix in (".KS", ".KQ"):
        try:
            t = yf.Ticker(stock_code + suffix)
            h = t.history(period="6mo")
            if len(h) > 0:
                cur = float(h["Close"].iloc[-1])
                first = float(h["Close"].iloc[0])
                info = {
                    "current": cur,
                    "6mo_ago": first,
                    "6mo_return_pct": (cur / first - 1) * 100,
                    "52w_high": float(h["Close"].max()),
                    "52w_low": float(h["Close"].min()),
                }
                cache[stock_code] = info
                return info
        except Exception:
            continue
    cache[stock_code] = None
    return None


def score_opportunity(code: str, u_data: dict, fin_cache: dict,
                       price_cache: dict, cm: dict) -> dict | None:
    """단일 종목 정량 점수 (총 135점)."""
    info = cm.get(code, {})
    corp_code = info.get("corp_code", "")
    if not corp_code:
        return None

    breakdown: dict[str, int] = {}
    flags: list[str] = []

    # 가격
    price_info = _yfinance_6m(code, price_cache)
    if not price_info:
        return None
    cur_price = price_info["current"]

    # 재무 캐시
    if corp_code not in fin_cache:
        annual_year, annual_rows = latest_annual_financials(corp_code)
        annual = parse_financials(annual_rows) if annual_rows else {}
        q_year, q_reprt, q_rows = latest_quarterly_financials(corp_code)
        quarterly = parse_financials(q_rows) if q_rows else {}
        prelim_meta, prelim_body = fetch_prelim_with_data(corp_code, 180)
        # 발행주식 추정 (majorstock)
        ms = fetch_majorstock(corp_code)
        shares = estimate_shares_outstanding(ms)
        # 자기주식
        tes_qty, tes_bsis, tes_acqs, tes_dsps, tes_pct, tes_label = \
            fetch_treasury_shares(corp_code, shares)
        # 매매 신선도 — 가장 최근 매수
        latest_buy_date = ""
        try:
            for m in sorted(ms, key=lambda x: x.get("rcept_dt", ""), reverse=True):
                if (m.get("stkrt_irds") or "0").lstrip("+-").replace(".","").isdigit():
                    rt = float(m.get("stkrt_irds") or 0)
                    if rt > 0:
                        latest_buy_date = m.get("rcept_dt", "")
                        break
        except Exception:
            pass
        fin_cache[corp_code] = {
            "annual": annual, "annual_year": annual_year,
            "quarterly": quarterly, "q_year": q_year, "q_label": q_reprt,
            "prelim_body": prelim_body, "prelim_meta": prelim_meta,
            "shares": shares, "tes_qty": tes_qty, "tes_pct": tes_pct,
            "tes_acqs": tes_acqs, "tes_dsps": tes_dsps,
            "latest_buy_date": latest_buy_date,
        }
    f = fin_cache[corp_code]
    annual = f["annual"]
    quarterly = f["quarterly"]
    prelim = f["prelim_body"]
    shares = f["shares"]
    tes_pct = f["tes_pct"]
    tes_acqs = f["tes_acqs"]
    tes_dsps = f["tes_dsps"]
    tes_qty = f["tes_qty"]
    latest_buy = f["latest_buy_date"]

    # 1) actor backtest (40)
    matched_actor = None
    actor_score = 0
    for a in u_data["actors"]:
        canonical, bt = match_actor(a)
        if bt:
            matched_actor = canonical
            actor_score = int(bt["hit15"] * 0.8)  # max 40 (49% × 0.8 = 39.2)
            if "🔴" in bt["signal"]:
                actor_score = -25
            flags.append(f"{bt['signal']} {canonical}")
            break
    # 오늘 신고 actor 매칭
    if not matched_actor and u_data.get("today_filing"):
        flr = u_data["today_filing"].get("flr_nm", "")
        canonical, bt = match_actor(flr)
        if bt:
            matched_actor = canonical
            actor_score = int(bt["hit15"] * 0.8)
            flags.append(f"🆕 오늘 신고 {canonical}")
    breakdown["actor"] = actor_score

    # 2) 매매 신선도 (10) — 가장 최근 매수 ≤ 90일
    fresh_score = 0
    if latest_buy:
        try:
            buy_dt = datetime.strptime(latest_buy, "%Y-%m-%d") if "-" in latest_buy \
                else datetime.strptime(latest_buy, "%Y%m%d")
            days_ago = (datetime.now() - buy_dt).days
            if days_ago <= 30:
                fresh_score = 10
                flags.append(f"🔥 최근 매수 {days_ago}일 전")
            elif days_ago <= 90:
                fresh_score = 5
                flags.append(f"🟢 최근 매수 {days_ago}일 전")
            elif days_ago <= 180:
                fresh_score = 2
        except Exception:
            pass
    breakdown["freshness"] = fresh_score

    # 3) 잠정 영업 YoY (15)
    prelim_score = 0
    op_yoy = None
    if prelim and prelim.get("rows", {}).get("영업이익"):
        op_yoy = prelim["rows"]["영업이익"].get("yoy_pct")
        if op_yoy is not None:
            if op_yoy >= 50:
                prelim_score = 15; flags.append(f"🔥 잠정 영업 {op_yoy:+.0f}%")
            elif op_yoy >= 20:
                prelim_score = 10; flags.append(f"🟢 잠정 영업 {op_yoy:+.0f}%")
            elif op_yoy >= 0:
                prelim_score = 5
            else:
                prelim_score = -5
    breakdown["prelim"] = prelim_score

    # 4) 분기 가속 (15) — 연간 vs 3Q 가속도
    accel_score = 0
    quarterly_op_yoy = None
    if quarterly.get("영업이익"):
        qv = quarterly["영업이익"]
        if qv["frmtrm"]:
            quarterly_op_yoy = (qv["thstrm"] / qv["frmtrm"] - 1) * 100
    annual_op_yoy = None
    if annual.get("영업이익"):
        av = annual["영업이익"]
        if av["frmtrm"]:
            annual_op_yoy = (av["thstrm"] / av["frmtrm"] - 1) * 100
    if quarterly_op_yoy is not None and annual_op_yoy is not None:
        if quarterly_op_yoy > annual_op_yoy + 30:
            accel_score = 15
            flags.append(f"🚀 가속 (분기 {quarterly_op_yoy:+.0f}% vs 연간 {annual_op_yoy:+.0f}%)")
        elif quarterly_op_yoy > annual_op_yoy + 10:
            accel_score = 10
        elif quarterly_op_yoy < annual_op_yoy - 30:
            accel_score = -10
            flags.append(f"📉 둔화 (분기 {quarterly_op_yoy:+.0f}% vs 연간 {annual_op_yoy:+.0f}%)")
    breakdown["accel"] = accel_score

    # 5) 매출 YoY (10)
    rev_score = 0
    rev_yoy = None
    if annual.get("매출액") and annual["매출액"]["frmtrm"]:
        rev_yoy = (annual["매출액"]["thstrm"] / annual["매출액"]["frmtrm"] - 1) * 100
        if rev_yoy >= 30:
            rev_score = 10; flags.append(f"🚀 매출 +{rev_yoy:.0f}%")
        elif rev_yoy >= 10:
            rev_score = 5
        elif rev_yoy <= -20:
            rev_score = -5
    breakdown["revenue"] = rev_score

    # 6) NAV 조정 PBR (15)
    pbr_score = 0
    nav_pbr = None
    cap_won = (annual.get("자본총계") or {}).get("thstrm", 0)
    if cap_won and shares and cur_price:
        market_cap_eok = cur_price * shares / 1e8
        tes_value_eok = (tes_qty or 0) * cur_price / 1e8
        adjusted_cap = cap_won - tes_value_eok
        float_shares = shares - (tes_qty or 0)
        float_mc = cur_price * float_shares / 1e8
        nav_pbr = float_mc / adjusted_cap if adjusted_cap > 0 else 999
        if nav_pbr <= 0.4:
            pbr_score = 15; flags.append(f"💎 NAV PBR {nav_pbr:.2f}")
        elif nav_pbr <= 0.6:
            pbr_score = 10; flags.append(f"🟢 NAV PBR {nav_pbr:.2f}")
        elif nav_pbr <= 0.8:
            pbr_score = 5
    breakdown["nav_pbr"] = pbr_score

    # 7) 자기주식 (15)
    tes_score = 0
    if tes_pct is not None:
        if tes_pct >= 8:
            tes_score = 10
            flags.append(f"💼 자기주식 {tes_pct:.1f}%")
        elif tes_pct >= 5:
            tes_score = 7
        elif tes_pct >= 2:
            tes_score = 3
    if tes_acqs and shares:
        acq_pct = tes_acqs / shares * 100
        if acq_pct >= 2:
            tes_score += 5
            flags.append(f"🟢 자기주식 +{acq_pct:.1f}%p 매입")
    if tes_dsps and shares:
        dsps_pct = tes_dsps / shares * 100
        if dsps_pct >= 1:
            tes_score -= 5
            flags.append(f"🔴 자기주식 -{dsps_pct:.1f}%p 처분")
    breakdown["treasury"] = min(15, max(-5, tes_score))

    # 8) consensus (10) — 검증 운용사 2+ 동시 보유
    cons_count = 0
    for a in u_data["actors"]:
        _, bt = match_actor(a)
        if bt and "🔴" not in bt["signal"]:
            cons_count += 1
    cons_score = 0
    if cons_count >= 3:
        cons_score = 10; flags.append(f"🤝 운용사 {cons_count}명 동시 보유")
    elif cons_count == 2:
        cons_score = 5; flags.append(f"🤝 운용사 2명 동시 보유")
    breakdown["consensus"] = cons_score

    # 8.5) follow 적기 (10) — 운용사 매수 가중평균 대비 현재가
    timing_score = 0
    avg_buy_price = None
    if u_data.get("buy_avgs") and u_data.get("held_values"):
        # 운용사별 보유금액 가중 매수 가중평균
        buys = u_data["buy_avgs"]
        vals = u_data["held_values"]
        total_val = sum(vals)
        if total_val > 0 and len(buys) == len(vals):
            avg_buy_price = sum(b * v for b, v in zip(buys, vals)) / total_val
            if avg_buy_price > 0:
                ratio = cur_price / avg_buy_price
                if ratio <= 0.95:
                    timing_score = 10
                    flags.append(f"✅ follow 적기 (현재가 < 운용사 단가 {(ratio-1)*100:+.0f}%)")
                elif ratio <= 1.05:
                    timing_score = 8
                    flags.append(f"✅ follow 적기 (단가 ±{(ratio-1)*100:+.0f}%)")
                elif ratio <= 1.15:
                    timing_score = 5
                elif ratio <= 1.30:
                    timing_score = 0
                elif ratio <= 1.50:
                    timing_score = -5
                    flags.append(f"🟡 follow 약간 늦음 ({(ratio-1)*100:+.0f}%)")
                else:
                    timing_score = -15
                    flags.append(f"🔴 follow 매우 늦음 (단가 {(ratio-1)*100:+.0f}%)")
    breakdown["timing"] = timing_score

    # 9) 부채비율 (3)
    debt_score = 0
    debt_ratio = None
    if annual.get("부채총계") and annual.get("자본총계") and annual["자본총계"]["thstrm"]:
        debt_ratio = annual["부채총계"]["thstrm"] / annual["자본총계"]["thstrm"] * 100
        if debt_ratio <= 50: debt_score = 3
        elif debt_ratio <= 100: debt_score = 2
        elif debt_ratio > 200: debt_score = -2
    breakdown["debt"] = debt_score

    # 10) 가격 위치 (2)
    price_score = 0
    high52 = price_info["52w_high"]
    low52 = price_info["52w_low"]
    ret6m = price_info["6mo_return_pct"]
    if high52 > low52:
        position = (cur_price - low52) / (high52 - low52) * 100
        if position <= 20:
            price_score = 2; flags.append(f"🟢 52주 low 근처")
        elif position >= 95:
            price_score = -1
    breakdown["price"] = price_score

    total = sum(breakdown.values())
    return {
        "stock_code": code, "corp_name": info.get("corp_name", code),
        "total": total, "breakdown": breakdown, "flags": flags,
        "matched_actor": matched_actor, "n_actors": cons_count,
        "cur_price": cur_price,
        "avg_buy_price": round(avg_buy_price, 0) if avg_buy_price else None,
        "timing_pct": round((cur_price/avg_buy_price-1)*100, 1) if avg_buy_price else None,
        "nav_pbr": round(nav_pbr, 2) if nav_pbr and nav_pbr < 99 else None,
        "op_yoy_annual": round(annual_op_yoy, 1) if annual_op_yoy is not None else None,
        "op_yoy_quarter": round(quarterly_op_yoy, 1) if quarterly_op_yoy is not None else None,
        "op_yoy_prelim": round(op_yoy, 1) if op_yoy is not None else None,
        "rev_yoy": round(rev_yoy, 1) if rev_yoy is not None else None,
        "tes_pct": round(tes_pct, 1) if tes_pct is not None else None,
        "debt_ratio": round(debt_ratio, 0) if debt_ratio is not None else None,
        "ret6m": round(ret6m, 1),
        "latest_buy_date": latest_buy,
    }


def build_opportunities(top_n: int = 15) -> tuple[str, list[dict]]:
    """모든 universe 종목 점수화 → ranking + 보고서."""
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
    universe = gather_universe(include_today=True)
    print(f"  · universe: {len(universe)} 종목 (holdings + 오늘 신고)")

    fin_cache: dict = {}
    price_cache: dict = {}
    scored = []
    for i, (code, u) in enumerate(universe.items(), 1):
        if i % 10 == 0:
            print(f"    진행 {i}/{len(universe)}")
        try:
            r = score_opportunity(code, u, fin_cache, price_cache, cm)
            if r:
                scored.append(r)
        except Exception as e:
            pass

    scored.sort(key=lambda r: -r["total"])
    ranked = scored[:top_n]

    today_iso = datetime.now().strftime("%Y-%m-%d")
    o: list[str] = []
    o.append(f"# 🎯 5pct-radar Opportunities — {today_iso}")
    o.append("")
    o.append(f"*검증 운용사 OPEN 종목 + 오늘 신고 통합 ranking (총 {len(universe)} 종목 검색 → 상위 {len(ranked)}개)*")
    o.append("")
    o.append("**점수 (총 135점)**: actor(40) + 신선도(10) + 잠정(15) + 분기가속(15) + 매출(10) + NAV PBR(15) + 자기주식(15) + consensus(10) + 부채(3) + 가격(2)")
    o.append("")

    # 종합 표
    o.append("## §1. 종합 ranking")
    o.append("")
    o.append("| # | 종목 | **총점** | NAV PBR | 분기 영업 | 매출 | 자기주식 | 부채 | **단가 vs 현재** | actor |")
    o.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for i, r in enumerate(ranked, 1):
        emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i:>2}"))
        pbr = f"{r['nav_pbr']:.2f}" if r['nav_pbr'] else "—"
        op_q = f"{r['op_yoy_quarter']:+.0f}%" if r['op_yoy_quarter'] is not None else "—"
        rev = f"{r['rev_yoy']:+.0f}%" if r['rev_yoy'] is not None else "—"
        tes = f"{r['tes_pct']:.1f}%" if r['tes_pct'] is not None else "—"
        debt = f"{r['debt_ratio']:.0f}%" if r['debt_ratio'] is not None else "—"
        timing = f"{r['timing_pct']:+.0f}%" if r['timing_pct'] is not None else "—"
        # timing 색깔
        if r['timing_pct'] is not None:
            if r['timing_pct'] <= 5: timing = f"🟢 {timing}"
            elif r['timing_pct'] <= 30: timing = f"🟡 {timing}"
            else: timing = f"🔴 {timing}"
        actor = r['matched_actor'][:10] if r['matched_actor'] else "—"
        o.append(f"| {emoji} | **{r['corp_name']}**({r['stock_code']}) | **{r['total']}** | {pbr} | {op_q} | {rev} | {tes} | {debt} | {timing} | {actor} |")
    o.append("")

    # 항목별 매트릭스
    o.append("## §2. 항목별 점수 매트릭스 (145점 만점)")
    o.append("")
    o.append("| # | 종목 | actor (40) | 신선 (10) | **timing (10)** | 잠정 (15) | 가속 (15) | 매출 (10) | NAV (15) | 자기주식 (15) | cons (10) | 부채 (3) | 가격 (2) | **총** |")
    o.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i, r in enumerate(ranked, 1):
        emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i:>2}"))
        b = r["breakdown"]
        o.append(f"| {emoji} | {r['corp_name']}({r['stock_code']}) | "
                 f"{b.get('actor',0):>3} | {b.get('freshness',0):>3} | "
                 f"**{b.get('timing',0):>3}** | "
                 f"{b.get('prelim',0):>3} | {b.get('accel',0):>3} | "
                 f"{b.get('revenue',0):>3} | {b.get('nav_pbr',0):>3} | "
                 f"{b.get('treasury',0):>3} | {b.get('consensus',0):>3} | "
                 f"{b.get('debt',0):>3} | {b.get('price',0):>3} | **{r['total']}** |")
    o.append("")

    # 시그널 flags
    o.append("## §3. 시그널 요약")
    o.append("")
    for i, r in enumerate(ranked, 1):
        emoji = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"  {i}"))
        thesis = " · ".join(r["flags"][:6]) or "—"
        o.append(f"- {emoji} **{r['corp_name']}** ({r['stock_code']}) — {thesis}")
    o.append("")

    o.append("---")
    o.append("")
    o.append(f"*Universe: {len(universe)} 종목 검색 (검증 운용사 OPEN 보유 + 오늘 5%+ 신고). 점수 ≥ 30 인 후보만 ranking.*")
    o.append("")
    o.append("*과거 backtest + 재무 기반. 미래 보장 없음. 진입 결정은 §13 사람 검증 후.*")
    return "\n".join(o), ranked


def save_opportunities(top_n: int = 15, *, mirror_obsidian: bool = True) -> Path:
    print(f"[1/3] universe 수집 ...")
    md, ranked = build_opportunities(top_n=top_n)
    print(f"[2/3] 보고서 생성 ...")
    OPP_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    today_iso = datetime.now().strftime("%Y-%m-%d")
    path = OPP_DIR / f"opportunities_{today_str}.md"
    path.write_text(md, encoding="utf-8")
    (OPP_DIR / f"opportunities_{today_str}.json").write_text(
        json.dumps(ranked, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if mirror_obsidian:
        print(f"[3/3] Obsidian 미러 ...")
        obs_opp = OBSIDIAN_DIR / "opportunities"
        obs_opp.mkdir(parents=True, exist_ok=True)
        (obs_opp / f"{today_iso}.md").write_text(md, encoding="utf-8")
    print(f"✅ 저장: {path}")
    return path
