# 5pct-radar — Claude 작업 가이드

이 파일은 Claude Code 가 새 세션을 시작할 때 자동으로 로드됩니다.
프로젝트 컨텍스트를 즉시 회복하는 용도입니다.

---

## 1. 프로젝트 한 줄 요약

한국 거래소 *모든 5% 대량보유 신고* 본문을 자동 분석해 *시나리오 분류 + 12개월
EV 분포 초안* 을 만드는 도구. activist-scout 의 한 catalyst 발견 세션에서
파생된 별도 도구.

**현재 상태**: v0.1.0
**저장소**: (GitHub 공개는 사용자 결정 대기)

---

## 2. 새 세션 빠른 컨텍스트 회복

작업을 이어 받기 전에 이 순서로 읽어 주세요:

1. **[STORY.md](STORY.md)** — 두올 케이스 수동 분석 → 자동화 발견 → 분리.
   필독.
2. **[README.md](README.md)** — 사용자용 가이드 (초보자 친화)
3. **[DISCLAIMER.md](DISCLAIMER.md)** — 면책 조항

---

## 3. 사용자 선호 (절대 잊지 말 것)

### 응답 언어
- **한국어 전용.** 영어 응답 금지.
- 한국어 안에 영어 약자가 자연스러우면 사용 가능 (예: `pip install`, 변수명).

### 외부 노출 문서의 약자/전문용어
- README/STORY/CHANGELOG 같은 **GitHub 에 보이는 문서** 에서는 약자 풀어쓰기:
  - DART → "금융감독원 전자공시(DART)"
  - LLM → "인공지능"
  - PE → "사모펀드"
  - M&A → "인수합병"
- *코드 내부 변수명·함수명*은 영어 그대로 유지 (기술적 정체성).

### 인공지능 출력 검증
- 인공지능이 생성한 내용은 *반드시 원본 공시로 검증* 후 보고.
- 환각 가능성 명시.

---

## 4. 절대 받지 않는 변경

1. **§7 "사람 검증 필수" 섹션 제거** — 자동화 정직성 핵심
2. **미래 수익률 *예측* 표현** — *예측* 이 아니라 *시나리오 가중 EV* 임을 분명히
3. **투자 권유성 텍스트** — "이 종목 사세요" 류 절대 금지
4. **`.env` 또는 비밀 정보 commit** — `.gitignore` 자동 제외 중
5. **evidence 필드 제거** — 본문 인용은 환각 검증 핵심

---

## 5. 명령어 빠른 참조

```bash
# 분기 1회: corp_code 매핑 갱신
python -m five_pct_radar --build-corp-map

# 신고 한 건 분석
python -m five_pct_radar 20260430001599

# 두올 케이스 자동 검증
python -m five_pct_radar --self-test

# Google grounding 생략 (빠르게)
python -m five_pct_radar 20260430001599 --no-grounding
```

---

## 6. 아키텍처 핵심

```
6단계 파이프라인:
  fetch_filing → extract_llm → resolve_filer → grounding → classify → report

7 시나리오 카테고리:
  행동주의캠페인 / 산업통합M&A / PE_buyout / 그룹지배강화 /
  특수관계자_변동 / 단순투자 / 기타

12M EV 분포:
  5개 시나리오 × (확률 × 가격 영향) → 가중 평균 mean

사람 검증 필수 항목:
  모든 보고서 §7 에 의무 포함 — 시스템 정직성 핵심
```

---

## 7. 핵심 코드 위치

| 모듈 | 역할 |
|---|---|
| `src/five_pct_radar/config.py` | 환경 변수·경로 |
| `src/five_pct_radar/dart_client.py` | DART OpenAPI 공용 클라이언트 |
| `src/five_pct_radar/corp_code.py` | corp_code.xml 매핑 빌드/로드 |
| `src/five_pct_radar/fetch_filing.py` | document.xml 다운로드 + 키워드 슬라이스 |
| `src/five_pct_radar/extract_llm.py` | Gemini structured output (본문 → JSON) |
| `src/five_pct_radar/resolve_filer.py` | 보고자 → 상장 모회사 자회사 역참조 |
| `src/five_pct_radar/grounding.py` | Gemini Google search grounding |
| `src/five_pct_radar/classify.py` | 시나리오 분류 + EV 분포 |
| `src/five_pct_radar/report.py` | Markdown 보고서 생성 |
| `src/five_pct_radar/__main__.py` | CLI 진입점 + self-test |

---

## 8. 데이터 디렉토리

`data/` 는 `.gitignore` 에서 자동 제외 — clone 시 비어 있음.

- `data/corp_code_map.json` — 상장사 ↔ DART corp_code 매핑 (분기 1회 빌드)
- `data/filing_intel/filing_intel_<rcept_no>.md` — 신고별 보고서
- `data/filing_intel/filing_intel_index.json` — 전체 분석 카탈로그

---

## 9. 자격 증명 (`.env`)

```
DART_API_KEY=...    # https://opendart.fss.or.kr
GEMINI_API_KEY=...  # https://aistudio.google.com
```

`.env.example` 템플릿 제공. `.env` 는 절대 commit 금지.

---

## 10. activist-scout 와의 관계

5pct-radar 는 activist-scout 의 *수동 검증 작업* 에서 발견된 자동화 도구입니다.
두 도구는 *대체재가 아니라 보완재* — activist-scout 가 *upstream universe 공급*,
5pct-radar 가 *downstream catalyst 추적* 합니다.

`activist-scout/tools/filing_intel/` 에 Phase 1 prototype 이 그대로 남아 있으니
*어떻게 시작됐는지* 비교 가능합니다.
