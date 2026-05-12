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

from .config import CORP_MAP_FILE, DATA_DIR
from .dive import ACTOR_BACKTEST, match_actor, fetch_majorstock, estimate_shares_outstanding

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
            "entry_date": c.get("entry_date", ""),
            "holding_days": c.get("holding_days", 0),
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
        # 금액 가중평균 unrealized
        if total_value > 0:
            weighted_ur = sum(h["unrealized_pct"] * h["held_value"] for h in holdings) / total_value
        else:
            weighted_ur = 0
        actor_totals.append({
            "actor": actor,
            "n_holdings": len(holdings),
            "total_value_won": total_value,
            "weighted_unrealized_pct": weighted_ur,
            "best_stock": max(holdings, key=lambda h: h["unrealized_pct"]) if holdings else None,
            "worst_stock": min(holdings, key=lambda h: h["unrealized_pct"]) if holdings else None,
        })
    actor_totals.sort(key=lambda a: -a["total_value_won"])

    o.append("| 운용사 | 보유 종목 수 | 총 보유금액 (억) | 가중평균 unrealized | 최고 종목 | 최악 종목 |")
    o.append("|---|---:|---:|---:|---|---|")
    for a in actor_totals:
        bs = a["best_stock"]
        ws = a["worst_stock"]
        bs_s = f"{bs['corp_name']} {bs['unrealized_pct']:+.0f}%" if bs else "—"
        ws_s = f"{ws['corp_name']} {ws['unrealized_pct']:+.0f}%" if ws else "—"
        o.append(f"| **{a['actor']}** | {a['n_holdings']} | "
                 f"{a['total_value_won']/1e8:,.0f} | "
                 f"**{a['weighted_unrealized_pct']:+.1f}%** | {bs_s} | {ws_s} |")
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
                 f"가중평균 unrealized **{a['weighted_unrealized_pct']:+.1f}%** · "
                 f"backtest hit15 {bt.get('hit15','?')}%")
        o.append("")
        if not holdings:
            o.append("*(없음)*")
            continue
        o.append("| 종목 | 평균매입 | 현재가 | 보유주수 | 보유금액(억) | 비중% | unrealized | 진입일 | 경로 |")
        o.append("|---|---:|---:|---:|---:|---:|---:|---|---|")
        for h in holdings:
            weight = (h["held_value"] / total * 100) if total > 0 else 0
            ur_emoji = "🟢" if h["unrealized_pct"] > 20 else ("🔴" if h["unrealized_pct"] < -10 else "⚪")
            ed = h["entry_date"]
            ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}" if len(ed) == 8 else ed
            o.append(f"| {h['corp_name']}({h['stock_code']}) | "
                     f"{h['buy_avg']:,.0f} | {h['cur_price']:,.0f} | "
                     f"{h['held_shares']:,} | {h['held_value']/1e8:,.0f} | "
                     f"**{weight:.1f}%** | {ur_emoji} **{h['unrealized_pct']:+.1f}%** | "
                     f"{ed_fmt} ({h['holding_days']}d) | {h['n_buys']}buys |")
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

    # 4. unrealized 상위 / 하위
    o.append("## §4. unrealized 상위 / 하위")
    o.append("")
    top_winners = sorted(all_holdings, key=lambda h: -h["unrealized_pct"])[:10]
    top_losers = sorted(all_holdings, key=lambda h: h["unrealized_pct"])[:10]

    o.append("### 🟢 Top 10 winners")
    o.append("")
    o.append("| 운용사 | 종목 | 평균매입 | 현재가 | **unrealized** | 보유금액(억) |")
    o.append("|---|---|---:|---:|---:|---:|")
    for h in top_winners:
        o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                 f"{h['buy_avg']:,.0f} | {h['cur_price']:,.0f} | "
                 f"**{h['unrealized_pct']:+.1f}%** | {h['held_value']/1e8:,.0f} |")
    o.append("")
    o.append("### 🔴 Top 10 losers")
    o.append("")
    o.append("| 운용사 | 종목 | 평균매입 | 현재가 | **unrealized** | 보유금액(억) |")
    o.append("|---|---|---:|---:|---:|---:|")
    for h in top_losers:
        o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                 f"{h['buy_avg']:,.0f} | {h['cur_price']:,.0f} | "
                 f"**{h['unrealized_pct']:+.1f}%** | {h['held_value']/1e8:,.0f} |")
    o.append("")

    # 5. 통계
    o.append("## §5. 통계")
    o.append("")
    total_value = sum(h["held_value"] for h in all_holdings)
    avg_ur = sum(h["unrealized_pct"] * h["held_value"] for h in all_holdings) / total_value if total_value else 0
    winners = sum(1 for h in all_holdings if h["unrealized_pct"] > 0)
    o.append(f"- 8개 운용사 총 보유: **{total_value/1e8:,.0f}억원** ({len(all_holdings)}건)")
    o.append(f"- 금액 가중 평균 unrealized: **{avg_ur:+.1f}%**")
    o.append(f"- 종목 단위 승률: **{winners/len(all_holdings)*100:.0f}%** ({winners}/{len(all_holdings)})")
    o.append(f"- 공통 보유 종목: {len(common)}개")
    o.append("")
    o.append("---")
    o.append("")
    o.append("*보유 주수는 발행주식 × 신고 비율% 추정. 5% 미만 줄인 후 추가 매도는 신고 의무 없음 → 실제 보유 ≤ 추정.*")
    o.append("")
    o.append("*과거 데이터 기반. 미래 보장 없음. 진입·청산 결정은 §13 사람 검증 후.*")

    return "\n".join(o)


def save_holdings(lifecycle_path: Path | None = None) -> Path:
    print("[1/2] 운용사 보유 데이터 수집 ...")
    data = gather_holdings(lifecycle_path)
    print("[2/2] 보고서 생성 ...")
    md = render_holdings(data)
    HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    path = HOLDINGS_DIR / f"holdings_{today_str}.md"
    path.write_text(md, encoding="utf-8")
    # JSON 도 저장 (회고용)
    json_path = HOLDINGS_DIR / f"holdings_{today_str}.json"
    json_path.write_text(json.dumps(data["all"], ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")
    return path
