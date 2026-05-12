"""운용사 daily 변동 추적 — 어제 vs 오늘 holdings 비교.

  내부 호출: save_holdings 가 자동으로 movements 도 생성

변동 분류:
  - 🆕 NEW       : 어제 없음 → 오늘 새로 등장
  - 🚪 REMOVED   : 어제 있음 → 오늘 없음 (5% 미만으로 줄임)
  - ⬆️  INCREASED : 보유 주수 +5% 이상 증가
  - ⬇️  DECREASED : 보유 주수 -5% 이상 감소
  - 💰 GAIN      : unrealized +5%p 이상 향상
  - 📉 LOSS      : unrealized -5%p 이상 악화
  - = STABLE    : 변동 거의 없음
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from ..config import DATA_DIR


def _load_yesterday_json(today: datetime, days_back_max: int = 7) -> tuple[list[dict], str]:
    """전날 (또는 가장 가까운 과거) holdings JSON 로드.

    Returns: (holdings_list, yesterday_date_str) or ([], "")
    """
    holdings_dir = DATA_DIR / "holdings"
    if not holdings_dir.exists():
        return [], ""
    for d in range(1, days_back_max + 1):
        prev = today - timedelta(days=d)
        path = holdings_dir / f"holdings_{prev.strftime('%Y%m%d')}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data, prev.strftime("%Y-%m-%d")
            except (json.JSONDecodeError, OSError):
                continue
    return [], ""


def compute_movements(yesterday: list[dict], today: list[dict]) -> dict:
    """(actor, stock_code) 기준 매칭 → 변동 분류.

    Returns:
        {
          "by_actor": {actor: {"new": [], "removed": [], "increased": [], ...}},
          "all_new": [...], "all_removed": [...],
          "summary": {actor: {...}}, "yesterday_date": "..."
        }
    """
    y_map = {(h["actor"], h["stock_code"]): h for h in yesterday}
    t_map = {(h["actor"], h["stock_code"]): h for h in today}

    by_actor: dict[str, dict[str, list]] = defaultdict(
        lambda: {"new": [], "removed": [], "increased": [], "decreased": [],
                 "gain": [], "loss": [], "stable": []}
    )

    # 신규 (오늘만)
    for key, t in t_map.items():
        if key not in y_map:
            by_actor[t["actor"]]["new"].append(t)

    # 철수 (어제만)
    for key, y in y_map.items():
        if key not in t_map:
            by_actor[y["actor"]]["removed"].append(y)

    # 둘 다 → 비교
    for key, t in t_map.items():
        if key not in y_map:
            continue
        y = y_map[key]
        actor = t["actor"]
        # 주수 변동 %
        y_shares = y.get("held_shares", 0) or 0
        t_shares = t.get("held_shares", 0) or 0
        if y_shares > 0:
            qty_change_pct = (t_shares - y_shares) / y_shares * 100
        else:
            qty_change_pct = 0
        # unrealized 변동 %p
        y_ur = y.get("unrealized_pct", 0) or 0
        t_ur = t.get("unrealized_pct", 0) or 0
        ur_change = t_ur - y_ur
        # 가격 변동 %
        y_price = y.get("cur_price", 0) or 0
        t_price = t.get("cur_price", 0) or 0
        price_change = (t_price / y_price - 1) * 100 if y_price else 0

        record = {**t, "qty_change_pct": qty_change_pct,
                  "ur_change_pp": ur_change, "price_change_pct": price_change,
                  "y_shares": y_shares, "y_ur": y_ur, "y_price": y_price}

        if qty_change_pct >= 5:
            by_actor[actor]["increased"].append(record)
        elif qty_change_pct <= -5:
            by_actor[actor]["decreased"].append(record)
        elif ur_change >= 5:
            by_actor[actor]["gain"].append(record)
        elif ur_change <= -5:
            by_actor[actor]["loss"].append(record)
        else:
            by_actor[actor]["stable"].append(record)

    # 운용사별 summary
    summary = {}
    for actor, buckets in by_actor.items():
        n_new = len(buckets["new"])
        n_removed = len(buckets["removed"])
        n_inc = len(buckets["increased"])
        n_dec = len(buckets["decreased"])
        summary[actor] = {
            "n_new": n_new, "n_removed": n_removed,
            "n_increased": n_inc, "n_decreased": n_dec,
            "net_active": n_new - n_removed,
            "net_qty": n_inc - n_dec,
        }

    return {
        "by_actor": dict(by_actor),
        "summary": summary,
    }


def render_movements(movements: dict, today_date: str, yesterday_date: str) -> str:
    o: list[str] = []
    o.append(f"# 🔄 운용사 Daily 변동 — {today_date} (vs {yesterday_date or '첫 실행'})")
    o.append("")
    if not yesterday_date:
        o.append("> ⚠️ 어제 JSON 없음 — 첫 실행. 내일부터 변동 비교 가능.")
        return "\n".join(o)
    o.append(f"> 어제 ({yesterday_date}) → 오늘 ({today_date}) holdings 비교.")
    o.append("> 5% 변동 임계치: 보유 주수 ±5%, unrealized ±5%p.")
    o.append("")

    by_actor = movements["by_actor"]
    summary = movements["summary"]

    if not by_actor:
        o.append("*(변동 없음)*")
        return "\n".join(o)

    # §1. 운용사별 summary
    o.append("## §1. 운용사별 변동 summary")
    o.append("")
    o.append("| 운용사 | 🆕 신규 | 🚪 철수 | ⬆️  증가 | ⬇️  감소 | net active | net qty |")
    o.append("|---|---:|---:|---:|---:|---:|---:|")
    rows = sorted(summary.items(), key=lambda kv: -kv[1]["n_new"] - kv[1]["n_increased"])
    for actor, s in rows:
        o.append(f"| **{actor}** | {s['n_new']} | {s['n_removed']} | "
                 f"{s['n_increased']} | {s['n_decreased']} | "
                 f"{s['net_active']:+d} | {s['net_qty']:+d} |")
    o.append("")

    # §2. 신규 진입 (모든 운용사)
    all_new = []
    for actor, buckets in by_actor.items():
        for h in buckets["new"]:
            all_new.append({**h, "_actor": actor})
    if all_new:
        o.append("## §2. 🆕 신규 진입 (가장 fresh — *오늘 추적 시작*)")
        o.append("")
        o.append("| 운용사 | 종목 | 진입가 | 현재가 | 보유주수 | 보유금액(억) | unrealized |")
        o.append("|---|---|---:|---:|---:|---:|---:|")
        for h in sorted(all_new, key=lambda x: -x.get("held_value", 0))[:20]:
            o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                     f"{h['buy_avg']:,.0f} | {h['cur_price']:,.0f} | "
                     f"{h['held_shares']:,} | {h['held_value']/1e8:,.0f} | "
                     f"{h['unrealized_pct']:+.1f}% |")
        o.append("")

    # §3. 철수 (5% 미만으로 줄임)
    all_removed = []
    for actor, buckets in by_actor.items():
        for h in buckets["removed"]:
            all_removed.append({**h, "_actor": actor})
    if all_removed:
        o.append("## §3. 🚪 철수 (5% 미만으로 축소 — *주의 신호*)")
        o.append("")
        o.append("| 운용사 | 종목 | 어제 보유금액(억) | 어제 unrealized |")
        o.append("|---|---|---:|---:|")
        for h in sorted(all_removed, key=lambda x: -x.get("held_value", 0))[:20]:
            o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                     f"{h.get('held_value', 0)/1e8:,.0f} | "
                     f"{h.get('unrealized_pct', 0):+.1f}% |")
        o.append("")

    # §4. 비중 증가 / 감소
    all_inc = []
    all_dec = []
    for actor, buckets in by_actor.items():
        for h in buckets["increased"]:
            all_inc.append({**h, "_actor": actor})
        for h in buckets["decreased"]:
            all_dec.append({**h, "_actor": actor})
    if all_inc:
        o.append("## §4. ⬆️  비중 증가 (추가 매수)")
        o.append("")
        o.append("| 운용사 | 종목 | 어제 주수 | 오늘 주수 | 변동% | 현재가 | unrealized |")
        o.append("|---|---|---:|---:|---:|---:|---:|")
        for h in sorted(all_inc, key=lambda x: -x.get("qty_change_pct", 0))[:15]:
            o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                     f"{h.get('y_shares',0):,} | {h['held_shares']:,} | "
                     f"**+{h.get('qty_change_pct',0):.1f}%** | "
                     f"{h['cur_price']:,.0f} | {h['unrealized_pct']:+.1f}% |")
        o.append("")
    if all_dec:
        o.append("## §5. ⬇️  비중 감소 (부분 익절·손절)")
        o.append("")
        o.append("| 운용사 | 종목 | 어제 주수 | 오늘 주수 | 변동% | 현재가 | unrealized |")
        o.append("|---|---|---:|---:|---:|---:|---:|")
        for h in sorted(all_dec, key=lambda x: x.get("qty_change_pct", 0))[:15]:
            o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                     f"{h.get('y_shares',0):,} | {h['held_shares']:,} | "
                     f"**{h.get('qty_change_pct',0):.1f}%** | "
                     f"{h['cur_price']:,.0f} | {h['unrealized_pct']:+.1f}% |")
        o.append("")

    # §6. unrealized 변동 (가격만)
    all_gain = []
    all_loss = []
    for actor, buckets in by_actor.items():
        for h in buckets["gain"]:
            all_gain.append({**h, "_actor": actor})
        for h in buckets["loss"]:
            all_loss.append({**h, "_actor": actor})
    if all_gain or all_loss:
        o.append("## §6. 💹 unrealized 변동 (가격 변동만, 매매 없음)")
        o.append("")
        if all_gain:
            o.append("### 💰 unrealized 향상")
            o.append("")
            o.append("| 운용사 | 종목 | 어제 unrealized | 오늘 unrealized | 변동 |")
            o.append("|---|---|---:|---:|---:|")
            for h in sorted(all_gain, key=lambda x: -x.get("ur_change_pp", 0))[:10]:
                o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                         f"{h.get('y_ur',0):+.1f}% | {h['unrealized_pct']:+.1f}% | "
                         f"**+{h.get('ur_change_pp',0):.1f}%p** |")
            o.append("")
        if all_loss:
            o.append("### 📉 unrealized 악화")
            o.append("")
            o.append("| 운용사 | 종목 | 어제 unrealized | 오늘 unrealized | 변동 |")
            o.append("|---|---|---:|---:|---:|")
            for h in sorted(all_loss, key=lambda x: x.get("ur_change_pp", 0))[:10]:
                o.append(f"| {h['actor']} | {h['corp_name']}({h['stock_code']}) | "
                         f"{h.get('y_ur',0):+.1f}% | {h['unrealized_pct']:+.1f}% | "
                         f"**{h.get('ur_change_pp',0):.1f}%p** |")
            o.append("")

    o.append("---")
    o.append("")
    o.append("*5% 미만으로 줄인 후 추가 매매는 신고 의무 없음 → 추정 데이터.*")
    o.append("*'철수' 는 절대적 매도 아닌 *공시 의무 종료* 일 수 있음.*")

    return "\n".join(o)


def detect_movements_from_today(today_holdings: list[dict]) -> tuple[dict, str, str]:
    """오늘 holdings 와 어제 JSON 비교 → movements + 메타.

    Returns: (movements dict, today_date_str, yesterday_date_str)
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    yesterday, y_date = _load_yesterday_json(now)
    if not yesterday:
        return {"by_actor": {}, "summary": {}}, today_str, ""
    return compute_movements(yesterday, today_holdings), today_str, y_date
