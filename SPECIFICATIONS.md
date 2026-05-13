# 5pct-radar — Specifications

> *무엇을 만드는가* 의 정의. 운영은 [OPERATIONS.md](OPERATIONS.md), 변경 이력은 [CHANGELOG.md](CHANGELOG.md), 개요는 [README.md](README.md).

---

## 1. 최종 산출물

### 1.1 일별 (launchd 자동 생성)

| 산출물 | 경로 | 발행 주기 | 소비자 |
|---|---|---|---|
| **통합 데일리 리포트** | `data/daily/daily_<YYYYMMDD>.md` | 평일 09:30 (launchd) | 본인 |
| **운용사 보유 모니터링** | `data/holdings/<YYYY-MM-DD>/holdings.md` | 평일 16:30 (launchd) | 본인 |
| **운용사 변동 추적** | `data/holdings/<YYYY-MM-DD>/movements.md` | 평일 16:30 (launchd) | 본인 |
| **변동 종목 자동 dive** | `data/holdings/<YYYY-MM-DD>/dive_<code>.md` | 변동 발생 시 | 본인 |
| **우선순위 ranking** | `data/rank/rank_<YYYYMMDD>.{md,json}` | 평일 09:30 (launchd) | 본인 |
| **종목 deep dive** | `data/dives/<YYYY-MM-DD>/<code>_<종목명>.md` | on-demand | 본인 |
| **dive 마스터 인덱스** | `data/dives/_index.md` | 자동 갱신 | 본인 |

### 1.2 Obsidian 미러 (선택)

```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/theme_radar/5pct_radar/
├── index.md                    # 마스터 인덱스
└── <YYYY-MM-DD>/               # 일별 폴더
    ├── _index.md               # Obsidian 링크
    ├── holdings.md
    ├── movements.md
    └── dive_<code>.md
```

### 1.3 단발 산출물 (수동/배치)

| 산출물 | 경로 | 명령어 |
|---|---|---|
| 5%+ 신고 단건 분석 | `data/filing_intel/filing_intel_<rcept_no>.md` | `radar <rcept_no>` |
| 10년 backtest | `data/filing_intel/lifecycle_<date>_3650d.{json,md}` | `radar --lifecycle 3650` |
| corp_code 매핑 | `data/corp_code_map.json` | `radar --build-corp-map` |
| 포지션 tracker | `data/positions.json` | `radar position` |
| 결정 일지 | `data/positions_closed.json` | `radar journal` |

---

## 2. 필수 입력 데이터 (Requested Fields)

| 필드 | 소스 | 빈도 | 필수 | 누락 시 |
|---|---|---|---|---|
| `5pct 신고 본문` | DART `list.json` + `document.xml` | 매일 | 필수 | shortlist 비어 있음 |
| `corp_code 매핑` | DART `corpCode.xml` | 분기 1회 | 필수 | 종목명 매핑 실패 |
| `정기보고서 재무` | DART `fnlttSinglAcnt.json` | 분기 (4회/년) | 필수 | PBR/부채비율 계산 불가 |
| `잠정실적` | DART `list.json` + `document.xml` (잠정 키워드) | 이벤트 | 필수 | 신선도 ↓ |
| `회사 개요` | DART `company.json` | 변경 시 | 필수 | §1 회사 정보 누락 |
| `자기주식 보유` | DART `tesstkAcqsDspsSttus.json` | 분기 | 필수 | catalyst 점수 -15 |
| `대량보유 신고` | DART `majorstock.json` | 매일 | 필수 | 매수 단가 추출 불가 |
| `주식 가격` | yfinance `<code>.KS`/`.KQ` | 일간 | 필수 | 현재가/시총 계산 불가 |
| `벤치마크 가격` | yfinance `069500.KS` (KODEX 200) | 일간 | 필수 | alpha 계산 불가 |

### 외부 API 요구사항
- **DART OpenAPI**: 회원가입 후 발급 (무료). `DART_API_KEY` 환경변수
- **Google AI Studio**: Gemini API 키 (무료 티어 충분). `GEMINI_API_KEY` (단일 신고 분석용)
- **Telegram Bot** (옵션): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (알림용)

---

## 3. 처리 파이프라인

### 3.1 매일 자동 (launchd 1 plist — 통합)

```
[16:30 평일] com.mgprj.5pct_radar.daily   →   radar daily (통합 마스터)
                                                 ├─ EXIT 알림 (포지션 A1)
                                                 ├─ 진입 우선순위 (opportunities, 135점)
                                                 ├─ 운용사 변동 + 자동 dive (변동 종목 + Top 3)
                                                 ├─ 운용사 보유 현황 (CAGR 포함)
                                                 ├─ 오늘 신규 5%+ 신고
                                                 ├─ 내 포지션 현황
                                                 └─ data/daily/ + Obsidian 미러
```

### 3.2 매주 일요일 03:00 (launchd)

```
[일요일 03:00] com.mgprj.5pct_radar.weekly  →  scheduler/run_weekly.sh
                                                  ├─ corp_code 매핑 갱신
                                                  ├─ 10년 lifecycle backtest (~30분)
                                                  └─ today dashboard 저장
```

### 3.3 launchd 등록

```bash
./scheduler/install.sh install   # 3 plist 모두 등록
./scheduler/install.sh status    # 등록 상태 확인
./scheduler/install.sh uninstall # 모두 제거
```

### 3.3 정량 점수 계산 (rank)

```
종합 점수 (100점):
  - actor       (50): 매칭된 검증 운용사 hit15%
                       unknown actor → 외국계/일반보고 대안 시그널 5~15
  - prelim      (20): 잠정실적 영업이익 YoY
                       +50%↑ 20 / +20% 15 / +0% 8 / -0 0
  - PBR         (15): ≤0.5 15 / ≤0.7 10 / ≤1.0 5
  - 자기주식    (15): 보유% (5%↑ 10) + 직전 매입 (2%↑ +5)
  - 부채비율     (3): ≤50% 3 / ≤100% 2 / >200% -2
  - 52주 위치    (2): low 근처 +2 / high 근처 -1
```

---

## 4. 품질 기준

### 4.1 검증된 actor 매칭 (backtest hit15)

10년 lifecycle × A1 exit (+20%익절/-10%손절) 기준:

| 운용사 | hit15 | n | signal |
|---|---:|---:|---|
| 베어링자산운용 (Barings) | 49% | 70 | 🟢 강한 매수 |
| 라이프자산운용 | 67% | 12 | 🟢 (소표본) |
| 안다자산운용 | 62% | 8 | 🟢 (소표본) |
| 브이아이피자산운용 (VIP) | 45% | 192 | 🟢 매수 |
| 신영자산운용 | 44% | 32 | 🟡 약한 매수 (최초 진입만) |
| 한국투자밸류자산운용 | 44% | 27 | 🟡 약한 매수 |
| 트러스톤자산운용 | 35% | 48 | 🟡 보통 |
| 에이티넘인베스트 | 5% | 20 | 🔴 회피 |

### 4.2 데이터 freshness

- 잠정실적: 매일 list.json 폴링
- 운용사 신고: 매일 majorstock.json + document.xml
- 가격: yfinance 5일 history (장 마감 후)
- 발행주식수: majorstock stkqy/stkrt 역산 (캐시 1일)

### 4.3 한계 (절대 신뢰 금지)

- 5% 미만으로 줄인 후 추가 매도는 *신고 의무 없음* → 보유 ≤ 추정
- 매수 단가 = 신고된 거래만 (5% 도달 후 변동만 의무)
- backtest hit15 는 *과거 패턴*, regime change (한국 상법 개정) 후 보장 없음
- 모든 보고서는 §13 사람 검증 의무

---

## 5. 의존성

### 5.1 외부 API / 패키지

```toml
# pyproject.toml
dependencies = [
  "requests",
  "yfinance",
  "pandas",
  "openai",        # 단일 신고 분석용
  "google-genai",  # Gemini grounding
]
```

### 5.2 환경변수 (`.env`)

```
DART_API_KEY=...        # 필수
GEMINI_API_KEY=...      # 필수 (단일 신고 분석)
OPENAI_API_KEY=...      # 옵션 (대체 LLM)
TELEGRAM_BOT_TOKEN=...  # 옵션 (알림)
TELEGRAM_CHAT_ID=...    # 옵션
OBSIDIAN_DIR=...        # 옵션 (default: ~/Library/.../theme_radar/5pct_radar)
```

### 5.3 MGPrj 내부 참조

- *없음* — 5pct-radar 는 *독립 실행 가능*
- (참고) activist-scout 와 *철학 공유*: §13 사람 검증 의무, regime change 인정

---

## 6. 알려진 Gap

- [ ] **`specs/requested_fields.json`** — JSON schema 형식 (현재 SPECIFICATIONS 텍스트만)
- [ ] **history tracking** — 어제 vs 오늘 ranking 점수 변화 추적
- [ ] **`radar daily` 도 Obsidian 미러** — 현재 `holdings` 만
- [ ] **5%+ 미만 보유 추정** — 신고 의무 종료 후 실제 매도 불명
- [ ] **외국계 펀드 패턴 강화** — 현재 `Capital/LLC/Fund` 키워드만 (정확도 ↓)
- [ ] **자기주식 처분 결정 자동 감지** — 현재는 보유 현황만 (catalyst 부재)

> 수정 완료 시 `CHANGELOG.md` 로 이동.

---

## 7. 버전

- **현재**: v0.1.0 (2026-05-09 public release)
- **다음 목표**: v0.2.0 — 모듈 그룹화 + history tracking + launchd
- **장기**: v1.0 — activist-scout 통합 + Telegram bot wizard

---

## 8. 비범위 (Out of Scope)

- ❌ **실시간 거래 자동화** — 분석만, 매매는 수동
- ❌ **확률/calibration 주장** — regime change 로 인해
- ❌ **개별 종목 추천** — 발견·점수화만, 진입 결정은 §13 사람
- ❌ **실시간 가격 스트리밍** — 일간 종가 기준
- ❌ **외국 주식** — 한국 KOSPI/KOSDAQ 만
