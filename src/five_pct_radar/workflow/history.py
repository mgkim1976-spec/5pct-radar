"""Ranking history tracking — 어제 vs 오늘 변화 추적.

  radar diff               # 어제 → 오늘 ranking 변화
  radar diff --days 7      # 7일 전 → 오늘

opportunities_<YYYYMMDD>.json 들 비교:
  - 🆕 새로 등장한 종목 (top N 신규 진입)
  - 🚪 사라진 종목 (top N 탈락)
  - ⬆️ 점수 상승 ≥ 10
  - ⬇️ 점수 하락 ≥ 10 (catalyst 발견 신호)
  - 순위 변동 ≥ 5계단
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ..config import DATA_DIR


OPP_DIR = DATA_DIR / "opportunities"


def _load_opp_json(date_str: str) -> list[dict] | None:
    p = OPP_DIR / f"opportunities_{date_str}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _find_past_opp(days_back: int = 1) -> tuple[list[dict] | None, str]:
    """가장 가까운 과거 opportunities JSON 찾기.

    Returns: (data, date_str) or (None, '')
    """
    now = datetime.now()
    # days_back 부터 시작해 7일까지 확장 탐색
    max_search = max(days_back + 7, 14)
    for d in range(days_back, max_search + 1):
        prev = now - timedelta(days=d)
        date_str = prev.strftime("%Y%m%d")
        data = _load_opp_json(date_str)
        if data:
            return data, prev.strftime("%Y-%m-%d")
    return None, ""


def compute_diff(yesterday: list[dict], today: list[dict],
                  *, top_threshold: int = 15, score_delta: int = 10,
                  rank_delta: int = 5) -> dict:
    """ranking 변화 분석.

    Returns: {
      "new_entries": [...],          # 오늘 top N, 어제 없음
      "dropped": [...],              # 어제 top N, 오늘 없음
      "score_up": [...],             # 점수 +N 이상 상승
      "score_down": [...],           # 점수 -N 이상 하락
      "rank_up": [...],              # 순위 +K 이상 상승
      "rank_down": [...],            # 순위 -K 이상 하락
    }
    """
    y_map = {h["stock_code"]: {**h, "rank": i+1} for i, h in enumerate(yesterday)}
    t_map = {h["stock_code"]: {**h, "rank": i+1} for i, h in enumerate(today)}

    y_top = set(c for c, h in y_map.items() if h["rank"] <= top_threshold)
    t_top = set(c for c, h in t_map.items() if h["rank"] <= top_threshold)

    new_entries = []
    for code in t_top - y_top:
        new_entries.append({**t_map[code]})

    dropped = []
    for code in y_top - t_top:
        if code in t_map:
            dropped.append({**y_map[code], "new_rank": t_map[code]["rank"],
                            "new_score": t_map[code]["total"]})
        else:
            dropped.append({**y_map[code], "new_rank": None, "new_score": None})

    score_up = []
    score_down = []
    rank_up = []
    rank_down = []
    for code in (y_top | t_top):
        if code not in y_map or code not in t_map:
            continue
        y = y_map[code]; t = t_map[code]
        ds = t["total"] - y["total"]
        dr = y["rank"] - t["rank"]  # +면 순위 상승 (작은 숫자로)
        if ds >= score_delta:
            score_up.append({**t, "y_score": y["total"], "y_rank": y["rank"],
                              "score_delta": ds, "rank_delta": dr})
        elif ds <= -score_delta:
            score_down.append({**t, "y_score": y["total"], "y_rank": y["rank"],
                                "score_delta": ds, "rank_delta": dr})
        if dr >= rank_delta:
            rank_up.append({**t, "y_score": y["total"], "y_rank": y["rank"],
                             "score_delta": ds, "rank_delta": dr})
        elif dr <= -rank_delta:
            rank_down.append({**t, "y_score": y["total"], "y_rank": y["rank"],
                               "score_delta": ds, "rank_delta": dr})

    # 정렬
    new_entries.sort(key=lambda x: x["rank"])
    dropped.sort(key=lambda x: x["rank"])
    score_up.sort(key=lambda x: -x["score_delta"])
    score_down.sort(key=lambda x: x["score_delta"])
    rank_up.sort(key=lambda x: -x["rank_delta"])
    rank_down.sort(key=lambda x: x["rank_delta"])

    return {
        "new_entries": new_entries, "dropped": dropped,
        "score_up": score_up, "score_down": score_down,
        "rank_up": rank_up, "rank_down": rank_down,
    }


def render_diff(diff: dict, today_iso: str, y_date: str) -> str:
    o: list[str] = []
    o.append(f"# 🔄 Ranking Diff — {today_iso} (vs {y_date or '(과거 데이터 없음)'})")
    o.append("")
    if not y_date:
        o.append("*과거 opportunities JSON 없음. 내일부터 비교 가능.*")
        return "\n".join(o)

    n_total = sum(len(diff[k]) for k in diff)
    if n_total == 0:
        o.append("*(변화 없음 — top 15 동일)*")
        return "\n".join(o)

    if diff["new_entries"]:
        o.append(f"## 🆕 새로 등장 ({len(diff['new_entries'])}건)")
        o.append("")
        o.append("| 순위 | 종목 | 점수 | 시그널 |")
        o.append("|---:|---|---:|---|")
        for h in diff["new_entries"]:
            thesis = " · ".join(h.get("flags", [])[:2]) or "—"
            o.append(f"| {h['rank']} | **{h['corp_name']}**({h['stock_code']}) | {h['total']} | {thesis} |")
        o.append("")

    if diff["dropped"]:
        o.append(f"## 🚪 사라짐 ({len(diff['dropped'])}건)")
        o.append("")
        o.append("| 어제 순위 | 종목 | 어제 점수 | 오늘 |")
        o.append("|---:|---|---:|---|")
        for h in diff["dropped"]:
            new_rank = f"#{h['new_rank']} ({h['new_score']})" if h.get('new_rank') else "—"
            o.append(f"| {h['rank']} | {h['corp_name']}({h['stock_code']}) | {h['total']} | {new_rank} |")
        o.append("")

    if diff["score_up"]:
        o.append(f"## ⬆️ 점수 상승 (Top {len(diff['score_up'])})")
        o.append("")
        o.append("| 종목 | 어제 점수 | 오늘 점수 | Δ | 순위 변화 |")
        o.append("|---|---:|---:|---:|---|")
        for h in diff["score_up"][:10]:
            rank_change = f"#{h['y_rank']} → #{h['rank']} ({h['rank_delta']:+d})"
            o.append(f"| **{h['corp_name']}**({h['stock_code']}) | {h['y_score']} | "
                     f"**{h['total']}** | **+{h['score_delta']}** | {rank_change} |")
        o.append("")

    if diff["score_down"]:
        o.append(f"## ⬇️ 점수 하락 (Top {len(diff['score_down'])})")
        o.append("")
        o.append("| 종목 | 어제 점수 | 오늘 점수 | Δ | 순위 변화 |")
        o.append("|---|---:|---:|---:|---|")
        for h in diff["score_down"][:10]:
            rank_change = f"#{h['y_rank']} → #{h['rank']} ({h['rank_delta']:+d})"
            o.append(f"| {h['corp_name']}({h['stock_code']}) | {h['y_score']} | "
                     f"**{h['total']}** | **{h['score_delta']}** | {rank_change} |")
        o.append("")

    if diff["rank_up"]:
        o.append(f"## ⬆️ 순위 급상승 ({len(diff['rank_up'])}건)")
        o.append("")
        for h in diff["rank_up"][:5]:
            o.append(f"- **{h['corp_name']}** (#{h['y_rank']} → #{h['rank']}, +{h['rank_delta']}계단)")
        o.append("")

    o.append("---")
    o.append("")
    o.append("*점수 변화 = 새 시그널 발견 신호. 큰 하락은 *함정 발견*, 큰 상승은 *catalyst 등장*.*")
    return "\n".join(o)


def get_diff(days_back: int = 1) -> tuple[dict | None, str, str]:
    """오늘 opportunities vs N일 전 비교. (diff, today_iso, y_date)"""
    today_iso = datetime.now().strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y%m%d")
    today = _load_opp_json(today_str)
    if not today:
        return None, today_iso, ""
    yesterday, y_date = _find_past_opp(days_back)
    if not yesterday:
        return None, today_iso, ""
    return compute_diff(yesterday, today), today_iso, y_date
