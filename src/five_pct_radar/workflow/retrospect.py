"""회고 학습 — N일 전 ranking vs 현재 가격으로 *우리 점수 시스템의 alpha* 검증.

  radar retrospect --days 7    # 7일 전 ranking 검증
  radar retrospect --days 30   # 1개월 전

검증:
  - 점수 80+ vs 70~79 vs 60~69 vs 60- 의 *실제 평균 수익률*
  - hit15 (수익 +15% 이상 비율)
  - 시스템 alpha = (포트폴리오 평균) - (KOSPI 평균)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

from ..config import DATA_DIR

RETRO_DIR = DATA_DIR / "retrospect"
OPP_DIR = DATA_DIR / "opportunities"


def _load_past_opp(days_back: int) -> tuple[list[dict] | None, str]:
    """N일 전 (또는 가장 가까운 과거) opportunities JSON."""
    now = datetime.now()
    for d in range(days_back, days_back + 7):
        prev = now - timedelta(days=d)
        p = OPP_DIR / f"opportunities_{prev.strftime('%Y%m%d')}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")), prev.strftime("%Y-%m-%d")
            except (json.JSONDecodeError, OSError):
                continue
    return None, ""


def _get_price_now(stock_code: str, cache: dict) -> float | None:
    if stock_code in cache:
        return cache[stock_code]
    for suffix in (".KS", ".KQ"):
        try:
            t = yf.Ticker(stock_code + suffix)
            h = t.history(period="5d")
            if len(h) > 0:
                price = float(h["Close"].iloc[-1])
                cache[stock_code] = price
                return price
        except Exception:
            continue
    cache[stock_code] = None
    return None


def _get_price_history(stock_code: str, days_back: int, cache: dict) -> tuple[float | None, float | None]:
    """N일 전 종가 + 현재 종가."""
    key = (stock_code, days_back)
    if key in cache:
        return cache[key]
    for suffix in (".KS", ".KQ"):
        try:
            t = yf.Ticker(stock_code + suffix)
            period_days = days_back + 7
            h = t.history(period=f"{period_days}d")
            if len(h) > days_back:
                past = float(h["Close"].iloc[-(days_back+1)]) if len(h) > days_back else float(h["Close"].iloc[0])
                now = float(h["Close"].iloc[-1])
                cache[key] = (past, now)
                return past, now
        except Exception:
            continue
    cache[key] = (None, None)
    return None, None


def compute_retrospect(past: list[dict], days_back: int) -> dict:
    """N일 전 ranking 종목들의 현재 가격으로 수익률 측정."""
    price_cache: dict = {}
    results = []
    print(f"  · {len(past)} 종목 가격 fetch ...")
    for i, h in enumerate(past, 1):
        if i % 10 == 0:
            print(f"    {i}/{len(past)}")
        code = h["stock_code"]
        past_price, now_price = _get_price_history(code, days_back, price_cache)
        if past_price is None or now_price is None:
            continue
        ret = (now_price / past_price - 1) * 100
        results.append({
            "stock_code": code, "corp_name": h["corp_name"],
            "past_score": h["total"], "past_rank": past.index(h) + 1,
            "past_price": past_price, "now_price": now_price,
            "return_pct": ret,
        })

    # KOSPI (KODEX 200) benchmark
    bench_past, bench_now = _get_price_history("069500", days_back, price_cache)
    bench_ret = (bench_now / bench_past - 1) * 100 if bench_past and bench_now else 0

    # 점수 구간별 통계
    def stats(items):
        if not items: return {"n": 0, "mean": 0, "hit15": 0, "win": 0, "alpha": 0}
        n = len(items)
        mean = sum(x["return_pct"] for x in items) / n
        hit15 = sum(1 for x in items if x["return_pct"] >= 15) / n * 100
        win = sum(1 for x in items if x["return_pct"] > 0) / n * 100
        alpha = mean - bench_ret
        return {"n": n, "mean": mean, "hit15": hit15, "win": win, "alpha": alpha}

    buckets = {
        "80+": stats([r for r in results if r["past_score"] >= 80]),
        "70~79": stats([r for r in results if 70 <= r["past_score"] < 80]),
        "60~69": stats([r for r in results if 60 <= r["past_score"] < 70]),
        "<60": stats([r for r in results if r["past_score"] < 60]),
        "전체": stats(results),
    }
    return {
        "results": results, "buckets": buckets,
        "bench_ret": bench_ret, "days_back": days_back,
    }


def render_retrospect(retro: dict, today_iso: str, y_date: str) -> str:
    o: list[str] = []
    o.append(f"# 📈 회고 학습 — {today_iso} (vs {y_date})")
    o.append("")
    days = retro["days_back"]
    o.append(f"*{days}일 전 ranking 의 종목들 → 현재 가격까지 수익률 측정. 시스템 alpha 검증.*")
    o.append("")

    o.append(f"## §1. 점수 구간별 성과 ({days}일)")
    o.append("")
    o.append(f"**KOSPI (KODEX 200) {days}일 수익률**: {retro['bench_ret']:+.1f}%")
    o.append("")
    o.append("| 점수 구간 | n | 평균 수익률 | hit15 (+15%↑) | 승률 (>0) | **알파** (vs KOSPI) |")
    o.append("|---|---:|---:|---:|---:|---:|")
    for label, s in retro["buckets"].items():
        emoji = "🟢" if label == "80+" else ("🟡" if label == "70~79" else ("⚪" if label == "60~69" else "🔴"))
        if label == "전체":
            emoji = "📊"
        if s["n"] == 0:
            o.append(f"| {emoji} {label} | 0 | — | — | — | — |")
            continue
        o.append(f"| {emoji} {label} | {s['n']} | "
                 f"**{s['mean']:+.1f}%** | {s['hit15']:.0f}% | "
                 f"{s['win']:.0f}% | **{s['alpha']:+.1f}%p** |")
    o.append("")

    # 80+ 종목 상세
    high = [r for r in retro["results"] if r["past_score"] >= 80]
    if high:
        o.append(f"## §2. 80+ 점수 종목 상세 ({len(high)}개)")
        o.append("")
        o.append("| 종목 | 점수 | 과거 가격 | 현재 가격 | **수익률** |")
        o.append("|---|---:|---:|---:|---:|")
        for r in sorted(high, key=lambda x: -x["return_pct"]):
            emoji = "🟢" if r["return_pct"] > 15 else ("🔴" if r["return_pct"] < -5 else "⚪")
            o.append(f"| {emoji} {r['corp_name']}({r['stock_code']}) | {r['past_score']} | "
                     f"{r['past_price']:,.0f} | {r['now_price']:,.0f} | **{r['return_pct']:+.1f}%** |")
        o.append("")

    # 결론
    o.append("## §3. 시스템 alpha 결론")
    o.append("")
    high_stats = retro["buckets"].get("80+", {})
    if high_stats.get("n", 0) > 0:
        alpha = high_stats["alpha"]
        if alpha >= 5:
            o.append(f"✅ **시스템 정상** — 80+ 점수 종목이 KOSPI 보다 **{alpha:+.1f}%p** 초과수익")
        elif alpha >= 0:
            o.append(f"🟡 **약한 alpha** — 80+ 점수 KOSPI 대비 **{alpha:+.1f}%p** (시장 유사)")
        else:
            o.append(f"🔴 **alpha 부정** — 80+ 점수 KOSPI 대비 **{alpha:+.1f}%p** *시스템 재검토 필요*")
    else:
        o.append("*표본 부족*")
    o.append("")
    o.append("*과거 backtest 보다 *실제 검증*이 더 강력. 최소 4주 누적 후 의미 있음.*")
    return "\n".join(o)


def save_retrospect(days_back: int = 7) -> Path | None:
    today_iso = datetime.now().strftime("%Y-%m-%d")
    print(f"[1/2] {days_back}일 전 opportunities 로드 ...")
    past, y_date = _load_past_opp(days_back)
    if not past:
        print(f"⚠️ {days_back}일 전 opportunities JSON 없음")
        return None
    print(f"  ✓ {y_date} (n={len(past)})")
    print(f"[2/2] 현재 가격 fetch + 수익률 계산 ...")
    retro = compute_retrospect(past, days_back)
    md = render_retrospect(retro, today_iso, y_date)

    RETRO_DIR.mkdir(parents=True, exist_ok=True)
    path = RETRO_DIR / f"retrospect_{datetime.now().strftime('%Y%m%d')}_{days_back}d.md"
    path.write_text(md, encoding="utf-8")
    json_path = path.with_suffix(".json")
    json_path.write_text(json.dumps(retro, ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")
    return path
