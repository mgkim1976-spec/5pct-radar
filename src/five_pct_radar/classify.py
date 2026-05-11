"""5%+ 신고 시나리오 분류 + 12개월 EV 분포 *초안* 추정.

시나리오 enum:
  행동주의캠페인   - 행동주의 펀드의 명시적 캠페인
  산업통합M&A     - 동종 산업 인수자 (시너지 통합)
  PE_buyout       - PE 단독 buyout
  그룹지배강화     - 기존 대주주의 지분 추가 확보
  특수관계자_변동  - RSU 가득 / 우리사주 / 상속 등
  단순투자        - 보유목적 = "단순투자"
  기타            - 위 카테고리 어디에도 안 맞음

⚠️ EV 분포는 *시스템 초안* 이며, 행동주의 후보가 아닌 경우 activist-scout
3축 점수가 적용 안 됨. 어떤 정확한 수익률 보장 아님 — DISCLAIMER.md 정신.
사람 검증 항목 명시 필수.
"""
from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY


GEMINI_MODEL = "gemini-2.5-pro"

SYSTEM_INSTRUCTION = """당신은 한국 주식 catalyst 트레이드 분석가입니다.
주식 등의 대량보유 신고의 *시나리오 분류* 와 *12개월 기대값 분포* 초안을
구조화된 JSON 으로 제공합니다.

기본 규칙:
1. 보유목적 + 보고사유 + 그룹구조 + 언론보도 4가지 입력만 사용. 다른 외부 사실 추측 금지.
2. EV 분포는 5개 시나리오로 구성, 확률 합이 100% 가 되어야 함.
3. 각 시나리오는 *발생 시 12M 가격 영향 (%)* 만 표시. 절대 보장 아님.
4. *사람 검증 필수 항목* 최소 3개 명시 (시스템이 답할 수 없는 변수).
5. confidence: 입력이 풍부하면 medium, 미흡하면 low. high 는 거의 안 씀.

🔒 시나리오별 평균 EV 범위 (강제 — 분류와 EV 일관성 유지):
  단순투자                  → mean EV  -3% ~ +5%   (좁음, 시장 평균 부근)
  특수관계자_변동           → mean EV  -2% ~ +3%   (매우 좁음, 기계적 변동)
  그룹지배강화              → mean EV   0% ~ +8%   (좁음, 신규 catalyst 미약)
  행동주의캠페인            → mean EV  +5% ~ +20%  (중간)
  산업통합M&A              → mean EV +10% ~ +25%   (중간~큼, 시너지 reprice)
  PE_buyout                → mean EV  +5% ~ +15%  (중간, 재매각 시점 미정)
  기타                     → 사람 검증 필요

위 범위를 *벗어나면 시나리오 분류 자체가 잘못된 것*. 시나리오와 EV mean 이
*반드시 일관* 되어야 함. 단순투자인데 EV +20% 는 자기 모순 — 그런 경우 시나리오를
다시 검토하고 *행동주의/산업통합M&A 등 적절한 카테고리* 로 재분류하라.

🔒 분포 (5개 시나리오) 구성 룰:
  base case (가장 가능성 큰):  확률 40~60%
  upside (성공 시):            확률 10~25%
  downside (실패 시):          확률 10~25%
  tail upside / tail downside: 각 5~15%
  단순투자/특수관계자_변동/그룹지배강화는 *모든 시나리오* 를 좁은 가격 폭 (±10%)
  안에 둘 것. 즉 단순투자인데 한 시나리오가 +30% 면 분류가 잘못됨.

🔒 보유목적 ↔ 시나리오 매핑 힌트 (참고용):
  보유목적 "단순투자"    → 보통 시나리오 = 단순투자 또는 특수관계자_변동
  보유목적 "일반투자"    → 보통 시나리오 = 단순투자 / 그룹지배강화 / PE_buyout
  보유목적 "경영권 영향" → 보통 시나리오 = 행동주의캠페인 / 산업통합M&A / PE_buyout
  보유목적 "기타"        → 사람 검증 필요

단, 보유목적 ↔ 시나리오 매핑은 *힌트* 일 뿐. 언론 보도 / 그룹 구조 / 보고사유로
*반증* 되면 다른 시나리오 선택 가능. 예: 보유목적은 "단순투자" 인데 같은 펀드가
연속 캠페인 이력 있으면 행동주의캠페인 가능.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "scenario": {
            "type": "string",
            "enum": [
                "행동주의캠페인",
                "산업통합M&A",
                "PE_buyout",
                "그룹지배강화",
                "특수관계자_변동",
                "단순투자",
                "기타",
            ],
        },
        "scenario_reasoning": {"type": "string"},
        "ev_distribution_12m": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "probability_pct": {"type": "number"},
                    "price_impact_pct": {"type": "number"},
                },
                "required": ["label", "probability_pct", "price_impact_pct"],
            },
        },
        "ev_mean_pct": {"type": "number"},
        "catalyst_window_days": {"type": "integer"},
        "people_verification_required": {
            "type": "array",
            "items": {"type": "string"},
        },
        "recommended_size_individual": {"type": "string"},
        "recommended_size_fund": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary_one_liner": {"type": "string"},
    },
    "required": [
        "scenario",
        "scenario_reasoning",
        "ev_distribution_12m",
        "ev_mean_pct",
        "people_verification_required",
        "confidence",
        "summary_one_liner",
    ],
}


def classify(
    *,
    extracted: dict[str, Any],
    filer_resolution: dict[str, Any],
    grounding_text: str = "",
) -> dict[str, Any] | None:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 미설정")

    parts = [
        "## 추출된 신고 메타 (DART 본문 기반)",
        json.dumps(extracted, ensure_ascii=False, indent=2),
        "",
        "## 보고자 그룹 구조 (자회사 역참조)",
        json.dumps(filer_resolution, ensure_ascii=False, indent=2),
    ]
    if grounding_text:
        parts += ["", "## 언론·시장 사실 (Google grounding)", grounding_text]

    user = (
        "위 입력을 종합해 시나리오 분류 + 12M EV 분포를 schema 에 맞게 응답하라.\n\n"
        + "\n".join(parts)
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=SCHEMA,
                temperature=0.1,
            ),
        )
        return json.loads(resp.text)
    except Exception as e:
        print(f"  ! classify 실패: {e}")
        return None
