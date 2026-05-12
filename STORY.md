# 5pct-radar 가 만들어진 이야기

*activist-scout 의 한 세션에서 발견한 자동화 가능성을, 같은 날 검증하고 별도
도구로 분리한 1일짜리 이야기.*

> **읽기 전 알아 두면 좋은 용어**
>
> - **대량보유 보고서 (5% 신고)** — 한국 자본시장법 §147 에 따라 누군가 한
>   상장사의 지분을 *5% 이상 새로 사거나 1%p 이상 변동* 시 의무적으로 내는 신고.
>   "큰손이 들어왔다" 의 1차 신호.
> - **보유 목적** — 신고서에 필수 기재. "단순투자" / "경영권 영향" / "일반투자" 중
>   하나. 경영권 영향 = 행동주의·M&A·지배 강화 등의 *실질적 권리 행사* 의지.
> - **document.xml** — DART OpenAPI 가 공시 본문 ZIP 으로 제공하는 형식.
>   안에 XML 한 개. 태그 제거하면 텍스트 본문.

---

## 0일차 — 두올 케이스를 수동으로 풀다

이 프로젝트는 사용자(개인 PM 입장의 펀드 매니저급) 가 activist-scout 의 보고서를
보다가 던진 한 질문에서 시작했습니다:

> "두올의 upside 는 어느 정도야?"

activist-scout 의 deep dive 가 *행동주의 캠페인 thesis* 기반으로 EV +11.7% 를
제시했습니다. 사용자는 더 깊이 보자 했고, 우리는:

1. **DART majorstock API** 로 두올의 최근 5% 신고 4건 발견
2. **DART document.xml API** 로 2026-05-04 프리미어 PE 14.65% 신고 본문 직접 다운로드
3. 본문에서 **"보유목적 = 경영권 영향"** 발견 (단순투자가 아니었음)
4. 같은 날 모트렉스이에프엠 62.23% + IHC 48.61% 도 같이 신고된 것 발견
5. **otrCprInvstmntSttus** 로 모트렉스이에프엠 → 모빌리스 → 모트렉스(KOSDAQ 118990)
   그룹 구조 매핑
6. **Gemini Google search grounding** 으로 언론 보도 8건 확인 — 산업 통합 M&A
   (시트·내장재 사업 결합) 로 확정
7. activist-scout deep dive 의 thesis 가 *부분적으로 틀렸음* 확인:
   행동주의 캠페인이 아니라 **모트렉스 그룹의 두올 인수 거래**

이 과정 후 사용자가 던진 결정적인 한 마디:

> "근데 대량보유보고서를 토대로 앞서 한 것과 같이 투자 기회를 발굴하는 것도
> 자동화 가능한가? LLM 도 사용하고."

답은 명백히 "가능". 우리가 방금 한 작업이 *수동 prototype* 이었습니다. 자동화는
직선 확장.

---

## 1일차 — Phase 1: tools/filing_intel/ prototype

activist-scout 의 정체성 (행동주의 후보 발굴) 을 보호하기 위해, 새 도구를 같은
패키지에 넣지 않고 *별도 모듈* 로 시작했습니다. 위치: `activist-scout/tools/filing_intel/`.

6개 모듈 작성:

- **fetch_filing** — DART document.xml ZIP 다운로드, 키워드 주변 600자 슬라이스
- **extract_llm** — Gemini 2.5 Pro structured output 으로 본문 → JSON (보유목적/취득금액/거래종결조건/특별관계자)
- **resolve_filer** — 비상장 보고자 명칭 → 정규화 → 어근 매칭 → 자회사 역참조로 상장 모회사 매핑
- **grounding** — Gemini Google search grounding 으로 언론·시너지 보강 (3회 재시도)
- **classify** — 시나리오 분류 (행동주의·M&A·PE buyout·그룹강화·특수관계자변동·단순투자·기타) + 12M EV 분포
- **report** — 7개 섹션 Markdown 보고서 + 인덱스 JSON

self-test 두 케이스로 검증:

| 케이스 | rcept_no | 보고자 | 검증 항목 |
|---|---|---|---|
| 1 | 20260504000081 | 프리미어 PE | 동반 인수자 시나리오 |
| 2 | 20260430001599 | 모트렉스이에프엠 | 그룹 매핑 |

**10/10 PASS.** 자동화가 수동 분석 결과를 재현했습니다.

흥미로운 발견: resolve_filer 가 "모트렉스이에프엠 → 모트렉스" 1-hop 매핑은
실패했지만 (실제로는 모트렉스 → 모빌리스 → 모트렉스이에프엠 2-hop 구조),
Gemini 가 *자체 world knowledge* 로 "*자동차 부품사인 모트렉스가 설립한
특수목적법인*" 이라고 정확히 추론. 즉 LLM 이 *코드 알고리즘의 한계* 를
*상식* 으로 보완.

---

## 1일차 (이어서) — Phase 2: 5pct-radar 별도 repo 분리

Phase 1 검증 직후, 사용자는 별도 repo 로 분리를 지시:

> "5pct-radar 로 해서 너가 추천한 방식으로 단계별로 진행하자."

분리 이유:

1. **목적 본질이 다름** — activist-scout 는 *행동주의 universe*, 5pct-radar 는
   *모든 5% 신고 universe*
2. **STORY 정신 보호** — activist-scout 의 9일 행동주의 narrative 가 깨지지 않게
3. **사용자 시그널** — GitHub 공개 시 두 narrative 분리

분리 작업:

- 자체 `config.py`, `dart_client.py`, `corp_code.py` 작성 — activist-scout 의존성 0
- 6개 분석 모듈 이전, `from activist_scout.* import` → `from .* import`
- `pyproject.toml`, `.gitignore`, `.env.example` 작성
- README / STORY / DISCLAIMER / CHANGELOG / CONTRIBUTING / CLAUDE.md
- self-test 재실행 → 분리 후도 10/10 PASS

---

## 2일차 (2026-05-12) — Backtest 검증 + Daily Ops 통합

1일차에 *5%+ 신고를 자동 분석* 까지 끝냈다. 그러나 *"그래서 뭐 살까?"* 답은
없었다. 2일차는 **검증과 사용** 의 날이었다.

### 오전 — 10년 lifecycle backtest

10년치 5%+ 신고 (n=682 cycles) 의 *full lifecycle* 추적:
- CLOSED 455 / OPEN 98 / TRADING 129
- 운용사 × A1 exit (+20%/-10%) backtest:
  - 베어링 누적 매수: hit15 **49%** (baseline 28% 대비 +21%p)
  - VIP 모든 매수: hit15 **45%**
  - 신영 *최초 진입만*: hit15 44% (누적은 36%)
  - 에이티넘: hit15 **5%** — 회피 시그널 발견
- Walk-forward CV (3 윈도우): *2021-2023 약세장* 모든 strategy 약함,
  *2024-2026* 강함 — *regime-dependent alpha* 확인

### 오후 — 오로라 ad-hoc dive

VIP가 *4/30 (1Q 잠정실적 발표 다음날)* 에 81,693주를 *16,989원에 폭매수* —
*전체 매수의 71%*. 이 패턴이 ***"실적 confirm 후 conviction 추가매수"*** 라는
명확한 시그널이었다.

오로라 분석을 위해 약 **11단계 ad-hoc 작업** 필요했다:
1. lifecycle JSON 에서 VIP 진입 cycle 찾기
2. 종목명 매핑 (corp_code_map)
3. DART company.json 회사 개요
4. DART fnlttSinglAcnt — 2025 연간 재무
5. DART 잠정실적 공시 검색 (4/29)
6. DART document.xml 본문 다운로드
7. ZIP 압축 풀고 인코딩 fallback (UTF-8 → CP949)
8. 본문 정규식으로 매출/영업이익 추출
9. majorstock 신고 본문에서 매매 단가 파싱
10. 가중평균 매수가 계산
11. A1 권장 진입가 + §13 사람 검증 체크리스트

이 11단계를 *명령어 1줄* 로 만드는 게 다음 도구의 사양이 됐다.

### 저녁 — Daily Ops 5종 + 사이즈 추천

**`radar today` / `dive` / `position` / `journal` / `notify` / `size`** 통합:

- `today`: 71건 신고 → 검증된 운용사 매칭 → STRONG/MEDIUM/AVOID/IGNORE 4분류
- `dive <code>`: 11단계 → 명령어 1줄. 잠정실적 본문 자동 파싱 (PER 5.7배 자동)
- `position`: 진입 기록 + A1 자동 트리거 + yfinance 실시간 가격
- `journal`: 청산 사후 회고 (가설 vs 실제, 운용사별 통계)
- `notify`: 텔레그램 push (없으면 stdout fallback)
- `size`: Kelly 변형 — 1억 자본 × VIP hit15 45% → 2.08% (208만원) 추천

오로라 *수동 분석 90분* 이 *`radar dive 039830` 30초* 가 됐다.

### 발견된 패턴

`radar today` 실행 결과 — **VIP가 *5/11 하루에* 3종목 동시 신고**:
- 오로라 (039830) — 이미 분석 완료
- 토비스 (051360) — VIP unrealized +22%
- **LF (093050) — 신규** — *PBR 0.39, 한 달간 33회 매수*

LF 의 ***PBR 0.39 + VIP 30회 매일 매수*** 는 *수동으로는 못 발견했을* 패턴.
도구가 *사람보다 멀리* 보기 시작했다.

---

## 흥미로운 협업 패턴

이 도구가 1일 만에 만들어진 비결:

1. **수동 prototype 이 명확** — 두올 케이스를 사람이 한 번 풀어 본 것이 자동화 사양
2. **기존 코드 재사용** — activist-scout 의 `dart_get` + Gemini structured output
   패턴이 그대로 작동
3. **LLM 의 *상식* 활용** — 알고리즘 한계 (그룹 구조 2-hop) 를 Gemini world
   knowledge 가 보완
4. **검증 자동화** — self-test 가 두 케이스로 10/10 통과해야 PASS

이건 *기존 도구 (activist-scout) 의 *수동 검증 단계* 가 *다음 도구* 의 *자동화 사양*
이 되는* 패턴입니다. 사람이 도구를 *쓰면서* 다음 도구의 *씨앗* 을 발견합니다.

---

## 솔직히 못 하는 것들

5pct-radar 가 **할 수 없는** 것:

- 공시 본문이 *암호화 PDF* 인 경우 (DART 의 약 5% 비율, ZIP 안에 PDF 만 들어있음)
- *손자회사 → 할아버지회사* 같은 다단 그룹 매핑 (1-hop 만 추적)
- 인수자 *진짜 의도* 검증 (위장 행동주의·청산 목적 등)
- 외국계 펀드 *한국 진입 신호* (블룸버그·헤드헌터 네트워크)
- *비공시 사실* (사적 인수 합의·이면 계약)

모든 보고서는 **§7. 사람 검증 필수 항목** 으로 끝납니다. 자동화의 정직한 경계입니다.

---

## 처음 발견한 분께

이 도구는 activist-scout 의 한 세션 도중 *두올 인수 거래* 를 풀다가 우연히 만들어진
파생 프로젝트입니다. 만약 당신이 *5% 신고 본문* 을 매일 사람이 읽기 부담스러워
이 도구를 찾으셨다면, 잘 오셨습니다.

이 코드를 가져가 개선하실 때는 **§7 "사람 검증 필수" 정신을 유지** 해 주세요.
가장 위험한 함정은 *5% 신고서가 *과도하게 신뢰* 되는 것* 입니다. 공시는 *법적 사실*
만 담고, *진짜 의도* 는 본문에 없습니다.

— *2026년 5월, activist-scout 의 한 catalyst 발견 세션에서*

---

## activist-scout 와의 관계

5pct-radar 는 [activist-scout](https://github.com/mgkim1976-spec/activist-scout)
의 *수동 검증 작업* 에서 발견된 *자동화 후보* 입니다. 두 도구는 각각 *upstream
universe 공급* 과 *downstream catalyst 추적* 역할을 분담합니다.

---

## 감사

- **두올 케이스를 던진 사용자** — 한 종목 분석이 새 도구를 만드는 씨앗이 됨
- **[Claude Code](https://claude.ai/code) (Anthropic)** — 인내심 있는 협업자
- **[DART OpenAPI](https://opendart.fss.or.kr)** — 공시 본문 자동 다운로드
- **Google Gemini 2.5 Pro** — structured output + Google search grounding
