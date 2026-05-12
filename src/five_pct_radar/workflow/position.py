"""포지션 tracker — *내가 실제로 진입한* 종목 관리 + A1 자동 트리거.

  radar position add 039830 --price 17390 --shares 100
  radar position list
  radar position close 039830 --price 21000
  radar position note 039830 "VIP 4/30 폭매수 follow"

저장:
  data/positions.json — 진행 포지션
  data/positions_closed.json — 청산 기록 (회고용)

A1 룰:
  익절 = 진입가 × 1.20
  손절 = 진입가 × 0.90
  → list 명령에서 현재가 fetch 후 자동 트리거 표시
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yfinance as yf

from ..config import CORP_MAP_FILE, DATA_DIR

POSITIONS_FILE = DATA_DIR / "positions.json"
CLOSED_FILE = DATA_DIR / "positions_closed.json"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _corp_name(stock_code: str) -> str:
    if not CORP_MAP_FILE.exists():
        return ""
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
    return (cm.get(stock_code) or {}).get("corp_name", "")


def _current_price(stock_code: str) -> float:
    for suffix in (".KS", ".KQ"):
        try:
            t = yf.Ticker(stock_code + suffix)
            h = t.history(period="5d")
            if len(h) > 0:
                return float(h["Close"].iloc[-1])
        except Exception:
            continue
    return 0.0


def add_position(stock_code: str, price: float, shares: int, *,
                 actor_followed: str = "", note: str = "") -> dict:
    positions = _load(POSITIONS_FILE)
    if any(p["stock_code"] == stock_code and p["status"] == "OPEN" for p in positions):
        raise SystemExit(f"⚠️ {stock_code} 이미 OPEN 포지션 — close 먼저")
    pos = {
        "stock_code": stock_code,
        "corp_name": _corp_name(stock_code),
        "entry_date": datetime.now().strftime("%Y-%m-%d"),
        "entry_price": price,
        "shares": shares,
        "actor_followed": actor_followed,
        "note": note,
        "status": "OPEN",
        "stop_loss": round(price * 0.9),
        "take_profit": round(price * 1.2),
    }
    positions.append(pos)
    _save(POSITIONS_FILE, positions)
    return pos


def close_position(stock_code: str, exit_price: float, *, note: str = "") -> dict:
    positions = _load(POSITIONS_FILE)
    pos = next((p for p in positions if p["stock_code"] == stock_code and p["status"] == "OPEN"), None)
    if not pos:
        raise SystemExit(f"⚠️ {stock_code} OPEN 포지션 없음")

    positions = [p for p in positions if not (p["stock_code"] == stock_code and p["status"] == "OPEN")]
    _save(POSITIONS_FILE, positions)

    closed_record = {
        **pos,
        "status": "CLOSED",
        "exit_date": datetime.now().strftime("%Y-%m-%d"),
        "exit_price": exit_price,
        "return_pct": (exit_price / pos["entry_price"] - 1) * 100,
        "exit_note": note,
    }
    # 보유일수
    try:
        d0 = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
        d1 = datetime.strptime(closed_record["exit_date"], "%Y-%m-%d")
        closed_record["holding_days"] = (d1 - d0).days
    except Exception:
        closed_record["holding_days"] = 0

    closed_list = _load(CLOSED_FILE)
    closed_list.append(closed_record)
    _save(CLOSED_FILE, closed_list)
    return closed_record


def annotate_position(stock_code: str, note: str) -> dict:
    positions = _load(POSITIONS_FILE)
    pos = next((p for p in positions if p["stock_code"] == stock_code and p["status"] == "OPEN"), None)
    if not pos:
        raise SystemExit(f"⚠️ {stock_code} OPEN 포지션 없음")
    pos["note"] = (pos.get("note", "") + "\n" + note).strip()
    _save(POSITIONS_FILE, positions)
    return pos


def list_positions(*, with_current_price: bool = True) -> list[dict]:
    """OPEN 포지션 + 현재가 + A1 트리거 상태."""
    positions = _load(POSITIONS_FILE)
    out = []
    for p in positions:
        if p["status"] != "OPEN":
            continue
        cur = _current_price(p["stock_code"]) if with_current_price else 0
        rp = (cur / p["entry_price"] - 1) * 100 if cur else 0
        days = 0
        try:
            d0 = datetime.strptime(p["entry_date"], "%Y-%m-%d")
            days = (datetime.now() - d0).days
        except Exception:
            pass
        if not cur:
            trigger = "❔ 가격 N/A"
        elif rp >= 20:
            trigger = "🟢 +20% 익절 도달 — *지금 매도 권장*"
        elif rp <= -10:
            trigger = "🔴 -10% 손절 도달 — *지금 매도 권장*"
        else:
            trigger = "⚪ 보유 유지"
        out.append({
            **p,
            "current_price": cur,
            "unrealized_pct": rp,
            "holding_days": days,
            "trigger": trigger,
        })
    return out


def render_position_list() -> str:
    rows = list_positions(with_current_price=True)
    if not rows:
        return "(OPEN 포지션 없음 — `radar position add <ticker> --price <P> --shares <N>` 으로 추가)"
    lines = []
    lines.append(f"# 📊 내 포지션 ({len(rows)}건) — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    for p in rows:
        lines.append(f"## {p['corp_name']} ({p['stock_code']}) — {p['trigger']}")
        lines.append("")
        lines.append("| 항목 | 값 |")
        lines.append("|---|---:|")
        lines.append(f"| 진입일 | {p['entry_date']} ({p['holding_days']}일 보유) |")
        lines.append(f"| 진입가 | {p['entry_price']:,.0f}원 × {p['shares']:,}주 |")
        lines.append(f"| 현재가 | **{p['current_price']:,.0f}원** ({p['unrealized_pct']:+.1f}%) |")
        lines.append(f"| 익절 (+20%) | {p['take_profit']:,}원 |")
        lines.append(f"| 손절 (-10%) | {p['stop_loss']:,}원 |")
        if p.get("actor_followed"):
            lines.append(f"| follow 한 운용사 | {p['actor_followed']} |")
        lines.append("")
        if p.get("note"):
            lines.append(f"**§13 메모**: {p['note']}")
            lines.append("")
    return "\n".join(lines)


def get_trigger_alerts() -> list[dict]:
    """A1 트리거 도달한 포지션만 (radar today / webhook 알림용)."""
    return [p for p in list_positions() if "도달" in p["trigger"]]
