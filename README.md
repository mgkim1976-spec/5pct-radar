# 5pct-radar

**한국 거래소에 *5% 이상 지분 신고* 가 들어올 때마다, 그 신고서 본문을 자동으로
읽고 "어떤 종류의 거래인지" 분류해 주는 도구.**

> 💡 **이 도구가 어떻게 만들어졌는지** → [STORY.md](STORY.md)
>
> activist-scout 사용 도중 *두올(016740) 인수 거래* 를 수동 분석하다가
> "이 작업을 자동화할 수 있겠다" 는 깨달음에서 시작.

---

## 1. 이게 뭔가요?

한국 상장사에 누군가 **지분 5% 이상을 새로 사거나 큰 폭으로 늘리면** 금융감독원에
의무적으로 신고해야 합니다 (자본시장법 §147). 이 신고서에는 *"왜 샀는지"*,
*"누가 샀는지"*, *"어떻게 자금을 조달했는지"* 같은 정보가 들어 있습니다.

문제는:

- 신고서 본문이 길고 (10~50 페이지)
- 형식이 복잡하고
- 매일 5~10건씩 들어옵니다

`5pct-radar` 는 이 신고서를 **자동으로 읽어** 다음을 추출합니다:

1. **보유 목적**: 단순 투자 / 경영권 영향 / 일반 투자 중 어느 것인지
2. **취득 금액과 자금 조달 방식**: 자기자금 / 차입 / 출자전환 등
3. **거래 종결 예정일**: 매매가 실제로 끝나는 날짜
4. **누구와 관련 있나**: 인수자가 어떤 그룹의 일원인지 자동 추적
5. **시장에 어떻게 알려졌나**: Google 검색으로 언론 보도와 거래 동기 보강
6. **시나리오 분류**: 행동주의 캠페인 / 산업 통합 인수 / 사모펀드 단독 인수 /
   대주주 지배 강화 / 단순 투자 중 어느 카테고리인지
7. **앞으로 12개월 가격 영향 분포**: 시나리오별 확률 가중 *초안*

### 왜 이게 가치 있나요?

한국에서는 *5% 신고서* 가 자주 시장 흐름의 *조기 신호* 입니다. 예를 들어:

- 행동주의 펀드가 5% 신고하면 → 자사주 소각 / 배당 확대 요구가 따라옴
- 동종 산업 회사가 60%+ 인수하면 → 산업 통합 M&A. 시너지 가격 상승
- 사모펀드 단독 인수하면 → 일정 기간 후 재매각 (3~5년)
- 대주주 본인이 지분 늘리면 → 지배 강화. 보통 주가 영향 작음

이 도구는 위 신호를 **사람이 한 건 한 건 본문을 읽지 않고도** 매일 자동으로
분류해 줍니다.

---

## 2. 결과물 미리 보기

명령어 한 줄 실행:

```bash
python -m five_pct_radar 20260430001599
```

→ `data/filing_intel/filing_intel_20260430001599.md` 파일 생성.

내용 발췌:

```markdown
# Filing Intel — 두올 (016740)

## §0. 한 줄 요약
> **시나리오: 산업통합M&A** — 모트렉스가 자회사 모트렉스이에프엠을 통해
>   두올 지분 62.23%를 인수, 자동차 부품 사업 통합 시너지 추구
> *12개월 EV 평균 추정: +16.5%*

## §1. 신고 메타 (DART 원문)
| 항목 | 값 |
| 보유목적 | 경영권 영향 |
| 취득금액 | 1,452억 원 (자기자금 1,402억 + 차입 50억) |
| 차입처 | 모빌리스 주식회사 |
| 거래종결 조건 | 2026년 7월 31일 (선행조건 충족 전제) |

## §3. 시나리오 분류
**시나리오 = `산업통합M&A`**

## §4. 12개월 EV 분포
| 시나리오 | 확률 (%) | 가격 영향 (%) | EV 기여 (%) |
| 기대 수준의 시너지 및 안정적 경영 | 50 | +20.0 | +10.00 |
| 성공적 PMI 및 높은 시너지 발현 | 15 | +50.0 | +7.50 |
| 통합 과정 난항, 시너지 발현 미미 | 20 | -10.0 | -2.00 |
| 인수 후유증 및 실적 악화 | 10 | -30.0 | -3.00 |
| 추가 M&A 또는 신사업 진출 성공 | 5 | +80.0 | +4.00 |
| 합계 (가중 평균) | 100 | — | +16.50 |

## §7. ⚠️ 사람 검증 필수 항목
- 인수 주체의 구체적인 PMI 계획 및 시너지 규모
- 피인수 기업의 내부 자산 및 운영 실사 결과
- 인수 주체 경영진의 과거 M&A 실행 경험
```

> ⚠️ **이 도구는 투자 권유가 아닙니다.** 결과만 보고 매매하지 마세요.
> [DISCLAIMER.md](DISCLAIMER.md) 참고.

---

## 3. 누구에게 도움이 되나요?

- 한국 주식 *catalyst 거래* 를 추적하는 개인 투자자
- 펀드 매니저·애널리스트 (5% 신고 1차 정리 자동화)
- M&A 흐름을 *시점에 맞춰* 추적하고 싶은 연구자
- 인공지능 + 공시 데이터 활용에 관심 있는 개발자

---

## 4. 빠른 시작

### (1) 설치

(컴퓨터에 Python 3.11 이상 필요)

```bash
git clone https://github.com/<your-account>/5pct-radar.git
cd 5pct-radar
pip install -e .
```

### (2) 자격 증명 2개 준비

```bash
cp .env.example .env
# .env 파일을 열어 2개 칸을 채웁니다
```

| 키 이름 | 어디서 받나 | 용도 |
|---|---|---|
| `DART_API_KEY` | https://opendart.fss.or.kr 가입 후 발급 (무료) | 공시 본문 다운로드 |
| `GEMINI_API_KEY` | https://aistudio.google.com (무료 티어 충분) | 본문 분석 + Google 검색 보강 |

### (3) 회사 코드 매핑 빌드 (분기 1회)

```bash
python -m five_pct_radar --build-corp-map
```

→ 약 4,000개 상장사 정보를 `data/corp_code_map.json` 에 저장 (약 30초).

### (4) 신고서 한 건 분석

```bash
# DART 접수번호 (rcept_no, 14자리) 가 필요합니다
python -m five_pct_radar 20260430001599
```

자동 검증 실행 (두올 케이스로 동작 확인):

```bash
python -m five_pct_radar --self-test
```

---

## 4.5. 매일 쓰는 명령어 (Daily Ops)

> 펀드 매니저급 워크플로 통합 — *"오늘 뭐해야 돼?"* 1줄 답 도구 모음.
> 모든 명령어는 *과거 10년 backtest 결과 + 검증된 운용사 시그널* 기반.

### `today` — 오늘 dashboard

```bash
python -m five_pct_radar today
```

오늘 들어온 5%+ 신고를 검증된 운용사·점수 기준으로 정렬한 dashboard:
- **🟢 STRONG / 🟡 MEDIUM / 🔴 AVOID / ⚪ IGNORE** 4단계 분류
- 베어링·VIP·신영·한국투자밸류 등 *backtest 검증된 매수 시그널*
- 잠정실적 발표 후 30일 내 매수 = *Fresh polarity* 자동 보너스
- 내 포지션의 A1 익절/손절 트리거 동시 표시
- 저장: `data/today/today_<YYYYMMDD>.md`

### `dive <종목코드>` — 단일 종목 즉시 deep dive

```bash
python -m five_pct_radar dive 039830
```

screening 통과 여부와 *무관하게* 모든 종목에 동작. 약 30초 내:
- DART 회사 개요·최신 재무 (사업보고서 + 분기) 자동 탐색
- 잠정실적 공시 목록 (최근 1년)
- 5%+ 신고 전체에서 *운용사별 매수/매도 timeline + 가중평균 단가*
- `매수 가중평균 / 순매집 실효단가 / 가장 최근 매수가` 3종 비교
- backtest 운용사 라벨 (hit15, mean, n) 자동 매칭
- A1 권장 진입가 + 익절/손절 (`현재가 ±20%/-10%`)
- 저장: `data/filing_intel/dive_<code>_<YYYYMMDD>.md`

### `position` — 내 포지션 tracker

```bash
# 진입 기록 (오로라 17,390원에 100주 매수, VIP follow)
python -m five_pct_radar position add 039830 \
    --price 17390 --shares 100 \
    --actor "VIP" --note "VIP 4/30 폭매수 후 follow. 1Q 영업+45.9%"

# 현재 포지션 + A1 트리거 (yfinance 실시간 가격)
python -m five_pct_radar position list

# 청산 기록 (사후 회고용으로 보관)
python -m five_pct_radar position close 039830 --price 21000 \
    --note "A1 익절 도달, 매도"

# 메모 추가 (§13 사람 검증 답변 등)
python -m five_pct_radar position note 039830 "노씨 일가 합산 지분 확인 필요"
```

저장: `data/positions.json` (OPEN), `data/positions_closed.json` (청산)

### `journal review` — 사후 회고 통계

```bash
python -m five_pct_radar journal review
```

청산된 포지션의 *내 가설 vs 실제 결과* 비교:
- 전체 승률 / hit15 / 평균 수익률 / 평균 보유일
- *follow 한 운용사별* 통계 (실제 성과 vs backtest 기대)
- 각 청산의 진입 메모 + 청산 메모

### `notify` — 텔레그램 알림

```bash
# .env 에 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 추가
python -m five_pct_radar notify
```

오늘의 STRONG 시그널 + EXIT 트리거를 텔레그램으로 push. 토큰 미설정 시 stdout fallback.

### 권장 cron 설정

```bash
# 평일 09:30 (장 개장 직후) 와 15:30 (장 마감 직후)
30 9,15 * * 1-5 cd ~/5pct-radar && python -m five_pct_radar notify
```

---

## 5. 어떤 원리로 동작하나요?

```
DART 신고 1건 (rcept_no)
        │
        ▼
[1] 본문 다운로드  ─── DART document.xml API (ZIP)
        │
        ▼
[2] 본문에서 핵심 키워드 주변만 추출 (토큰 절감)
        │
        ▼
[3] Gemini 인공지능: 구조화된 JSON 추출
    (보유목적/취득금액/거래종결조건/특별관계자)
        │
        ▼
[4] 보고자가 비상장 법인이면 → 상장 모회사 자동 추적
    (자회사 리스트 역참조)
        │
        ▼
[5] Google 검색 보강 (선택)
    언론 보도·거래 동기·시장 반응
        │
        ▼
[6] 인공지능 시나리오 분류 + 12개월 EV 분포 초안
        │
        ▼
data/filing_intel/filing_intel_<rcept_no>.md
data/filing_intel/filing_intel_index.json
```

### 핵심 기술

- **DART OpenAPI** (`majorstock`, `document.xml`, `otrCprInvstmntSttus`)
- **Google Gemini 2.5 Pro**
  - *structured output* 으로 본문 → JSON 추출
  - *Google Search grounding* 으로 언론·시장 사실 보강
- **자체 알고리즘**
  - 보고자 명칭 정규화 (`(주)`, `Co., Ltd.` 등 제거)
  - 어근 매칭으로 후보 상장사 추출
  - 후보의 자회사 리스트에서 역참조 → 그룹 구조 매핑

---

## 6. 시나리오 분류 카테고리

| 카테고리 | 뜻 | 통상 12M EV 분포 |
|---|---|---|
| **행동주의캠페인** | 행동주의 펀드의 명시적 자사주 소각·배당 요구 | +5~15%, 다운사이드 작음 |
| **산업통합M&A** | 동종 산업 회사가 시너지 통합 목적 인수 | +10~25%, 종결 무산 시 −20% |
| **PE_buyout** | 사모펀드 단독 buyout | 시간 지평 3~5년 |
| **그룹지배강화** | 기존 대주주가 추가 지분 매수 | 주가 영향 보통 작음 |
| **특수관계자_변동** | RSU 가득·우리사주 인출 등 | 거의 없음 (기계적 변동) |
| **단순투자** | 보유목적 = "단순투자" 표기 | 시장 평균 |
| **기타** | 위 어디에도 안 맞음 | 사람 검증 필수 |

---

## 6.5. 🚨 정직한 backtest 결과

10년 backtest 의 **결정적 발견** (3가지 한계 명시):

### 운용사 ranking (CLOSED 사이클 ≥ 70 인 운용사만)

```
🟢 확정 양수 (closed_ratio 97%):
   한국투자밸류 (n=71) median raw +4.2%  ← 유일 양수 확정

🔴 확정 음수 (closed_ratio 97%):
   신영자산운용  (n=126) median raw -10.6%  ← 명확 follow 회피

🟡 판단 보류 (closed_ratio < 70%, 미실현 큼):
   VIP자산운용 (전체 70, closed 47%) — OPEN median raw +54%
   * cycle 완성 (5년+) 후 평가 가능. 단기 결론 X
```

### 진입 시점 (look-ahead 없는) screening rule

```
🟢 매수 권장 (lift 1.9x):
   actor_cat = PE_사모 + Q4 (10~12월) 진입
   → hit15 = 47% (n=38, baseline 24.5%)
   
🔴 명확한 회피:
   행동주의 캠페인 패턴 (n=126, hit15 5%)
   Q2 진입 (hit15 12%)
```

→ ***적극적 매수 전략* 은 *작은 우위 + 큰 변동성***. 5pct-radar 의 진정한 활용:
*회피 신호* + *catalyst awareness* (triage 도구).
자세한 결과 + 한계 + closed-ratio bias 는
[docs/STRATEGY_FINDINGS.md](docs/STRATEGY_FINDINGS.md) 필독.

---

## 7. 한계 (정직 공시)

이 도구가 **할 수 없는** 것:

1. **본문 자체가 부정확하면 자동 분석도 부정확** — 공시는 *법적 사실* 만 담고
   *진짜 의도* 는 안 담음. 인수자의 *진짜 의도* 는 사람이 매니지먼트 인터뷰로 확인
2. **인공지능 환각 가능** — Gemini 가 본문에 없는 정보를 그럴듯하게 만들 수 있음.
   모든 보고서 §1 에 *evidence (본문 인용)* 를 강제로 첨부해 검증 가능
3. **그룹 구조 추적은 1-hop 만** — *모회사 → 자회사* 직접 관계만 매핑.
   *손자회사 → 할아버지회사* 같은 다단 그룹은 사람 검증 필요
4. **거래 종결 후 *실제 가격 변동* 예측 안 함** — 12M EV 분포는 *초안* 일 뿐.
   미래 수익률 보장하지 않음 (DISCLAIMER.md)
5. **법조항 해석 가끔 부정확** — "자본시장법 §154 제1항 각 호" 같은 구체적
   조항 인용은 LLM 이 가끔 틀림

**가장 위험한 함정**: 이 도구가 *마치* 의사 결정을 대신해 주는 것처럼 보이는 것.
모든 보고서 §7 "사람 검증 필수 항목" 을 읽지 않고 매매하지 마세요.

---

## 8. activist-scout 와의 관계

5pct-radar 는 [activist-scout](https://github.com/mgkim1976-spec/activist-scout)
사용 도중 발견한 *별도 도구* 입니다.

| | activist-scout | 5pct-radar |
|---|---|---|
| 목적 | *행동주의 펀드 캠페인 후보* 발굴 | *모든 5% 신고 본문* 분석 |
| Universe | KOSPI 행동주의 후보 ~48종 | KOSPI 전체 5% 신고 (연 수백 건) |
| 점수 | 3축 (타깃 매력도/매집/법적) | 시나리오 분류 + EV 분포 |
| Cadence | 주간 | 매일 |
| 출력 | 9등급 종목 리스트 + 심층 보고서 | 신고별 1페이지 보고서 |

**두 도구는 *대체재가 아니라 보완재*** 입니다.
activist-scout 가 *후보 발굴* 하면, 5pct-radar 가 *catalyst 추적* 합니다.

---

## 9. 문서 안내

- [STORY.md](STORY.md) — 이 도구가 어떻게 만들어졌는지
- **[docs/STRATEGY_FINDINGS.md](docs/STRATEGY_FINDINGS.md)** — 10년 backtest 결과
  + IRR 분포 + scenario × hit rate + walk-forward 검증 (필독)
- [DISCLAIMER.md](DISCLAIMER.md) — 면책 사항 (필독)
- [CHANGELOG.md](CHANGELOG.md) — 변경 이력
- [CONTRIBUTING.md](CONTRIBUTING.md) — 기여 방법
- [LICENSE](LICENSE) — MIT

---

## 10. 라이선스

MIT — **무료, 누구나 사용·수정·재배포 가능. 출처만 표기하면 됨.**
