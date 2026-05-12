"""텔레그램 webhook 알림.

.env:
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...

사용:
  from .notify import send_telegram
  send_telegram("EXIT 알림: 037460 +62.8%")

토큰 미설정 시 stdout 으로 fallback (warn).
"""
from __future__ import annotations

import os
import sys

import requests

from .config import _load_env, ROOT_DIR

_load_env(ROOT_DIR / ".env")


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — stdout 으로 출력", file=sys.stderr)
        print(text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        if r.status_code == 200:
            return True
        print(f"⚠️ 텔레그램 전송 실패 (HTTP {r.status_code}): {r.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠️ 텔레그램 전송 예외: {e}", file=sys.stderr)
        return False


def send_today_summary() -> bool:
    """오늘 dashboard 요약을 텔레그램으로."""
    from .today import get_alerts_summary
    return send_telegram(get_alerts_summary())
