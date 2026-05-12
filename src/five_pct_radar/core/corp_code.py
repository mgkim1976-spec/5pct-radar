"""DART corpCode.xml → stock_code ↔ corp_code 매핑 빌드/로드.

분기 1회 정도 갱신 권장 (신규 상장사 / 폐지 반영).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from ..config import CORP_MAP_FILE
from ..core.dart_client import dart_fetch_zip


def build_corp_code_map() -> None:
    """DART corpCode.xml 다운로드 → stock_code → {corp_code, corp_name} 매핑."""
    zf = dart_fetch_zip("corpCode.xml", timeout=120)
    if zf is None:
        raise RuntimeError("DART corpCode.xml 다운로드 실패")
    root = ET.fromstring(zf.read("CORPCODE.xml"))
    mapping = {}
    for item in root.findall("list"):
        sc = (item.findtext("stock_code") or "").strip()
        if not sc or len(sc) != 6:
            continue
        mapping[sc] = {
            "corp_code": (item.findtext("corp_code") or "").strip(),
            "corp_name": (item.findtext("corp_name") or "").strip(),
        }
    with open(CORP_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    print(f"saved {CORP_MAP_FILE} ({len(mapping)} listed companies)")


def load_corp_map() -> dict:
    if not CORP_MAP_FILE.exists():
        raise FileNotFoundError(
            f"{CORP_MAP_FILE} 없음. `python -m five_pct_radar --build-corp-map` 먼저 실행하세요."
        )
    with open(CORP_MAP_FILE, encoding="utf-8") as f:
        return json.load(f)
