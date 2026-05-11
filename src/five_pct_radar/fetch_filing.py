"""DART majorstock + document.xml 다운로드 + 본문 텍스트 추출."""
from __future__ import annotations

import re
from typing import Any

from .dart_client import dart_get, dart_fetch_zip


def list_majorstock(corp_code: str) -> list[dict[str, Any]]:
    """특정 회사의 5% 대량보유 공시 전체 목록."""
    data = dart_get("majorstock.json", {"corp_code": corp_code})
    if not data or data.get("status") != "000":
        return []
    return list(data.get("list", []))


def fetch_majorstock_meta(rcept_no: str, corp_code: str) -> dict[str, Any] | None:
    """rcept_no 와 corp_code 가 일치하는 majorstock 항목 한 건 반환."""
    for item in list_majorstock(corp_code):
        if item.get("rcept_no") == rcept_no:
            return item
    return None


def fetch_document_text(rcept_no: str, *, timeout: int = 60) -> str:
    """DART document.xml ZIP → 가장 큰 XML → 태그 제거 텍스트.

    파일이 ZIP 이 아닌 경우 (예: 암호화 PDF) 빈 문자열 반환.
    """
    zf = dart_fetch_zip("document.xml", {"rcept_no": rcept_no}, timeout=timeout)
    if zf is None:
        return ""
    biggest = max(zf.namelist(), key=lambda n: zf.getinfo(n).file_size)
    raw_bytes = zf.read(biggest)
    # 인코딩 자동 폴백 (옛 DART 신고는 CP949/EUC-KR, 최신은 UTF-8)
    raw = None
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            candidate = raw_bytes.decode(enc)
            # 한글 키워드 ≥ 1 검증 (mojibake 자동 감지)
            if "보고서" in candidate or "보유" in candidate or "주식" in candidate:
                raw = candidate
                break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raw = raw_bytes.decode("utf-8", errors="replace")
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def slice_around(text: str, keywords: list[str], window: int = 600) -> dict[str, str]:
    """본문에서 각 키워드 주변 ±window 자만 추출 (LLM 토큰 절감용)."""
    out: dict[str, str] = {}
    for kw in keywords:
        m = re.search(kw, text)
        if not m:
            out[kw] = ""
            continue
        s = max(0, m.start() - 50)
        e = min(len(text), m.end() + window)
        out[kw] = text[s:e]
    return out


# 대량보유 보고서에서 핵심 정보가 모여 있는 키워드 (Gemini 토큰 절감용)
DEFAULT_KEYWORDS = [
    "보유목적",
    "보고사유",
    "취득자금등의 개요",
    "취득자금등의 조성경위",
    "보유주식등의 수 및 보유비율",
    "발행회사와의 관계",
    "특별관계자",
    "보고자 개요",
    "차입",
    "거래종결",
    "선행조건",
]
