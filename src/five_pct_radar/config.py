"""환경 설정·자격 증명·경로.

`.env` 파일 또는 환경 변수에서 다음 키들을 읽는다:
  - DART_API_KEY   (https://opendart.fss.or.kr)
  - GEMINI_API_KEY (https://aistudio.google.com)
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# .env 자동 로드 (있으면)
_load_env(ROOT_DIR / ".env")

DART_API_KEY = os.environ.get("DART_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

CORP_MAP_FILE = DATA_DIR / "corp_code_map.json"
FILING_INTEL_DIR = DATA_DIR / "filing_intel"


def require(key: str) -> str:
    """필수 env 키가 있는지 확인. 없으면 명확한 에러."""
    v = os.environ.get(key, "")
    if not v:
        raise RuntimeError(
            f"{key} 미설정. .env 파일에 {key}=... 추가 또는 환경변수로 export 하세요."
        )
    return v
