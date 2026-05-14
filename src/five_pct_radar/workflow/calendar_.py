"""실적 calendar — 잠정실적 발표 예정일 자동 예측.

  radar calendar          # 향후 30일 발표 예정
  radar calendar --days 14

검증 운용사 보유 + 오늘 ranking top 종목 → 직전 분기 발표일 기준 *다음 발표일 추정*.

전형적 패턴 (KOSPI/KOSDAQ):
  - 1Q: 4월 중순 ~ 5월 초
  - 반기: 7월 중순 ~ 8월 중순
  - 3Q: 10월 중순 ~ 11월 초
  - 연간: 2월 중순 ~ 3월 중순
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from ..config import CORP_MAP_FILE, DATA_DIR
from ..core.dart_client import dart_get

CALENDAR_DIR = DATA_DIR / "calendar"


def fetch_recent_prelim(corp_code: str, days: int = 365) -> list[dict]:
    """잠정실적 공시 최근 N일."""
    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    j = dart_get("list.json", {
        "corp_code": corp_code, "bgn_de": bgn, "end_de": end,
        "page_count": 100,
    })
    if not j or j.get("status") != "000":
        return []
    return [it for it in j.get("list", []) if "잠정" in it.get("report_nm", "")]


def predict_next_prelim(prelims: list[dict]) -> tuple[datetime | None, str]:
    """직전 잠정실적 발표 일자 기준으로 다음 예측.

    Returns: (예상 일자, 분기 라벨)
    """
    if not prelims:
        return None, ""
    latest = max(prelims, key=lambda x: x.get("rcept_dt", ""))
    rcept_dt = latest.get("rcept_dt", "")
    if not rcept_dt:
        return None, ""
    try:
        last_date = datetime.strptime(rcept_dt, "%Y-%m-%d")
    except ValueError:
        try:
            last_date = datetime.strptime(rcept_dt, "%Y%m%d")
        except ValueError:
            return None, ""

    # 직전 발표 후 +90일 (분기 주기)
    next_date = last_date + timedelta(days=90)

    # 분기 라벨
    next_month = next_date.month
    if 4 <= next_month <= 5:
        quarter = "1Q"
    elif 7 <= next_month <= 8:
        quarter = "반기"
    elif 10 <= next_month <= 11:
        quarter = "3Q"
    elif 2 <= next_month <= 3:
        quarter = "연간"
    else:
        quarter = "?"
    return next_date, quarter


def gather_universe_codes() -> set[str]:
    """검증 운용사 보유 종목 + 오늘 ranking 후보."""
    codes = set()
    # holdings
    hd = DATA_DIR / "holdings"
    if hd.exists():
        latest_h = sorted(hd.glob("holdings_*.json"), reverse=True)
        if latest_h:
            try:
                for h in json.loads(latest_h[0].read_text(encoding="utf-8")):
                    codes.add(h["stock_code"])
            except Exception:
                pass
    # opportunities top
    opp_dir = DATA_DIR / "opportunities"
    if opp_dir.exists():
        latest_o = sorted(opp_dir.glob("opportunities_*.json"), reverse=True)
        if latest_o:
            try:
                for h in json.loads(latest_o[0].read_text(encoding="utf-8")):
                    codes.add(h["stock_code"])
            except Exception:
                pass
    return codes


def build_calendar(days_ahead: int = 30) -> tuple[str, list[dict]]:
    today_iso = datetime.now().strftime("%Y-%m-%d")
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
    codes = gather_universe_codes()
    print(f"  · universe {len(codes)} 종목 검색 ...")

    # 각 종목 직전 잠정실적 → 다음 예측
    calendar_items = []
    for i, code in enumerate(codes, 1):
        if i % 10 == 0:
            print(f"    {i}/{len(codes)}")
        info = cm.get(code, {})
        corp_code = info.get("corp_code", "")
        if not corp_code:
            continue
        prelims = fetch_recent_prelim(corp_code, 365)
        if not prelims:
            continue
        next_date, quarter = predict_next_prelim(prelims)
        if not next_date:
            continue
        days_until = (next_date - datetime.now()).days
        if days_until < -5 or days_until > days_ahead:  # 5일 지난 것까지 (혹시 늦은 발표)
            continue
        calendar_items.append({
            "stock_code": code, "corp_name": info.get("corp_name", code),
            "predicted_date": next_date.strftime("%Y-%m-%d"),
            "days_until": days_until, "quarter": quarter,
            "last_prelim_date": max(prelims, key=lambda x: x.get("rcept_dt", "")).get("rcept_dt", ""),
        })

    calendar_items.sort(key=lambda x: x["days_until"])

    # 보고서
    o: list[str] = []
    o.append(f"# 📅 잠정실적 발표 예정 calendar — {today_iso}")
    o.append("")
    o.append(f"*검증 운용사 보유 + opportunities top 종목 중 향후 {days_ahead}일 내 발표 예상.*")
    o.append("")
    o.append(f"**기준**: 직전 잠정실적 발표일 + 90일 (분기 주기)")
    o.append("")

    if not calendar_items:
        o.append("*(향후 {days_ahead}일 내 발표 예정 종목 없음)*")
        return "\n".join(o), []

    # 14일 이내 (긴급)
    urgent = [c for c in calendar_items if -5 <= c["days_until"] <= 14]
    if urgent:
        o.append(f"## 🔥 14일 이내 발표 예정 ({len(urgent)}건)")
        o.append("")
        o.append("| 예상일 | D-day | 종목 | 분기 | 직전 발표일 |")
        o.append("|---|---:|---|---|---|")
        for c in urgent:
            d_label = f"D-{c['days_until']}" if c['days_until'] >= 0 else f"D+{-c['days_until']}"
            d_label = "**오늘**" if c['days_until'] == 0 else d_label
            o.append(f"| {c['predicted_date']} | {d_label} | "
                     f"**{c['corp_name']}**({c['stock_code']}) | {c['quarter']} | {c['last_prelim_date']} |")
        o.append("")

    # 15~30일
    later = [c for c in calendar_items if c["days_until"] > 14]
    if later:
        o.append(f"## 📆 15~{days_ahead}일 후 예상 ({len(later)}건)")
        o.append("")
        o.append("| 예상일 | D-day | 종목 | 분기 |")
        o.append("|---|---:|---|---|")
        for c in later:
            o.append(f"| {c['predicted_date']} | D-{c['days_until']} | "
                     f"{c['corp_name']}({c['stock_code']}) | {c['quarter']} |")
        o.append("")

    o.append("---")
    o.append("")
    o.append("*예측은 *직전 발표 + 90일* 단순 룰. 실제 발표일은 회사마다 ±7일.*")
    o.append("*잠정실적 발표 후 30일 내 매수 = Fresh Polarity 시그널 (점수 +20)*")
    return "\n".join(o), calendar_items


def save_calendar(days_ahead: int = 30) -> Path:
    print(f"[1/2] universe 수집 ...")
    md, items = build_calendar(days_ahead=days_ahead)
    print(f"[2/2] 저장 ...")
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    path = CALENDAR_DIR / f"calendar_{today_str}.md"
    path.write_text(md, encoding="utf-8")
    json_path = CALENDAR_DIR / f"calendar_{today_str}.json"
    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")
    return path
