"""DART OpenAPI 공용 클라이언트.

  dart_get(path, params)     - JSON API GET (crtfc_key 자동 주입, 3회 재시도)
  dart_fetch_zip(url, params) - ZIP 응답 다운로드 (document.xml, corpCode.xml 등)
"""
from __future__ import annotations

import io
import time
import zipfile
from typing import Any

import requests

from .config import DART_API_KEY


_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


def dart_get(path: str, params: dict | None = None, retries: int = 3, timeout: int = 20) -> Any:
    """DART OpenAPI JSON GET. rate-limit aware retry.

    DART 응답 status:
      "000" = 정상
      "010" = 등록되지 않은 키
      "020" = 사용 한도 초과 → 60초 backoff
      "100" = 필수값 누락
      "800" = 시스템 점검
    """
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY 미설정")
    p = dict(params or {})
    p["crtfc_key"] = DART_API_KEY
    url = path if path.startswith("http") else f"https://opendart.fss.or.kr/api/{path.lstrip('/')}"
    for i in range(retries):
        try:
            r = _SESSION.get(url, params=p, timeout=timeout)
            # ZIP/binary 응답 (corpCode.xml 등) 은 json() 호출 안 함 — dart_fetch_zip 별도
            ctype = r.headers.get("content-type", "")
            if "json" not in ctype.lower():
                return None
            j = r.json()
            status = j.get("status", "")
            if status == "020":
                # 사용 한도 초과 — 길게 backoff
                time.sleep(60)
                continue
            if status == "800":
                # 시스템 점검 — 짧게 backoff
                time.sleep(10)
                continue
            return j
        except Exception:
            time.sleep(1.0 * (i + 1))
    return None


def dart_fetch_zip(url: str, params: dict | None = None, timeout: int = 60) -> zipfile.ZipFile | None:
    """DART API binary ZIP 응답 → ZipFile 객체. 실패 시 None."""
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY 미설정")
    p = dict(params or {})
    p["crtfc_key"] = DART_API_KEY
    full_url = url if url.startswith("http") else f"https://opendart.fss.or.kr/api/{url.lstrip('/')}"
    try:
        r = _SESSION.get(full_url, params=p, timeout=timeout)
    except Exception:
        return None
    try:
        return zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        return None
