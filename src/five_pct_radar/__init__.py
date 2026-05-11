"""5pct-radar — 한국 거래소 5% 대량보유 신고 본문 자동 분석.

DART document.xml + Gemini structured output + Google search grounding 으로
대량보유 보고서의 *시나리오 분류 (행동주의 / 산업 통합 M&A / PE buyout 등)*
와 12개월 EV 분포 *초안* 을 자동 생성.

⚠️ 본 도구는 *catalyst trade 후보 발굴* 도구이지 투자 권유가 아니다.
모든 LLM 출력은 §7 "사람 검증 필수" 항목을 통해 원본 공시 본문으로 검증해야 한다.
DISCLAIMER.md 참조.
"""

__version__ = "0.1.0"
