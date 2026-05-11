"""5pct-radar self-test wrapper.

⚠️ 이 테스트는 *실제 DART API + Gemini API* 를 호출합니다.
  - 비용: 약 $0.07 (Gemini structured output 2회 + Google grounding 1회)
  - 시간: 약 60~120초
  - 자격 증명: .env 에 DART_API_KEY + GEMINI_API_KEY 필요

평시 CI 에서는 skip 되도록 `RUN_INTEGRATION_TESTS=1` 환경변수로 게이트.
"""
import os

import pytest

from five_pct_radar.__main__ import self_test


@pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 to run live DART + Gemini self-test"
)
def test_self_test_two_cases_pass():
    """두올 케이스 두 건 (프리미어 PE + 모트렉스이에프엠) 자동 검증."""
    assert self_test() is True
