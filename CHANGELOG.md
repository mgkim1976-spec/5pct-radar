# 변경 이력

본 프로젝트의 모든 주요 변경 사항을 기록.
자세한 *진화 이야기* 는 [STORY.md](STORY.md) 참조.

---

## [0.2.0] — 2026-05-12

### Daily Ops 통합 — 11개 명령어 + macOS launchd 자동화

`5pct-radar` 가 *단일 신고 분석 도구* 에서 *펀드 매니저급 daily workflow* 로 진화.

#### 신규 명령어 (10개)

| 명령 | 기능 |
|---|---|
| `daily` | 통합 데일리 (오늘 신고 + 우선순위 ranking + 포지션) |
| `holdings` | 운용사 보유 + 변동 + 자동 dive + Obsidian 미러 |
| `dive <code>` | 단일 종목 deep dive (screening 의존성 X) |
| `rank` | screen → 자동 dive → 정량 점수 매트릭스 |
| `today` | 오늘 5%+ 신고 분류 dashboard |
| `position {add\|list\|close\|note}` | 내 포지션 tracker + A1 알림 |
| `size --actor X --capital N --price P` | Kelly 변형 사이즈 추천 |
| `journal review` | 청산 사후 회고 |
| `notify` | 텔레그램 알림 |

#### 핵심 기능 추가

**1. 정량 점수 매트릭스 (100점 만점)**
- actor backtest hit15 (50)
- 잠정실적 영업 YoY (20)
- PBR (15)
- 자기주식 보유·매입 (15)
- 부채비율 (3)
- 52주 가격 위치 (2)

**2. 운용사 모니터링**
- 8개 검증 운용사 OPEN/TRADING cycle 통합
- 보유 주수 × 현재가 = 보유 금액
- 절대 unrealized + **연평균 CAGR** + 평균 보유일
- 어제 vs 오늘 변동 자동 추적 (신규/철수/비중변동)
- 변동 종목 *자동 dive* (최대 10건)

**3. 단일 종목 dive**
- 최신 재무 (사업보고서 + 분기보고서 자동 탐색)
- 잠정실적 본문 자동 파싱 (매출/영업이익/순이익 + 기간)
- PER 누계실적 기반 연환산
- 자기주식 보유·매입·처분 (`tesstkAcqsDspsSttus`)
- NAV 조정 PBR (자기주식 차감)
- 매수 가중평균 단가 + 매매 timeline
- A1 권장 진입가 + 익절/손절

**4. 자동화 (macOS launchd)**
- `scheduler/com.mgprj.5pct_radar.daily.plist` (평일 09:30)
- `scheduler/com.mgprj.5pct_radar.holdings.plist` (평일 16:30)
- `scheduler/com.mgprj.5pct_radar.weekly.plist` (일요일 03:00)
- `scheduler/install.sh install` 일괄 등록

**5. Obsidian 미러**
- `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/theme_radar/5pct_radar/`
- `data/holdings/<date>/` 로컬 + Obsidian 미러 자동
- 일별 인덱스 + 마스터 인덱스 자동 생성

#### 발견 사례 (도구가 *수동 분석 못 본* 패턴)

- **오로라 (039830)**: VIP 가 1Q 잠정실적 발표 다음날 81,693주 폭매수 (전체의 71%) — *실적 confirm 후 conviction* 패턴 자동 발견
- **LF (093050)**: PBR 0.39 + 자기주식 8.8% + 직전 156만주 매입 — *deep value + 주주환원 catalyst* 의 결정적 조합 자동 발견

#### MGPrj 3파일 표준 도입

- ✅ `SPECIFICATIONS.md` — 산출물·데이터·파이프라인·품질
- ✅ `OPERATIONS.md` — Quick Start·launchd·모니터링·장애 대응
- ✅ `CHANGELOG.md` — 본 문서

#### Backtest 검증 (10년 lifecycle + A1 exit)

- 베어링자산운용 hit15 **49%** (n=70)
- VIP hit15 **45%** (n=192)
- 에이티넘인베스트 hit15 **5%** — 회피 신호 검증
- *baseline 25% 대비 +20~24%p*

#### 리팩토링

- 🗑️ `src/.../deep_dive_lite.py` 삭제 (새 `dive.py` 가 완전 대체)
- 🗑️ `scripts/refresh_weekly.sh` → `scheduler/run_weekly.sh`
- 📁 `scheduler/` 폴더 신설 (launchd plist + run scripts)
- 📁 `logs/` 폴더 신설
- 📄 `README.md` 단순화 (580줄 → ~200줄)
- 🏗️ **모듈 그룹화** — 26개 평탄 모듈 → 4 하위 패키지:
  - `core/` (3): dart_client, corp_code, fetch_filing
  - `analysis/` (8): classify, extract_llm, grounding, resolve_filer, scan, catalyst_chain, report, score_filing
  - `backtest/` (5): lifecycle_monitor, backtest_actor, backtest_phase0, actor_stats, score_model
  - `workflow/` (10): daily, today, dive, rank, holdings, movements, position, sizing, journal, notify

---

## [0.1.0] — 2026-05-11

### 첫 공개 버전

activist-scout 의 한 catalyst 발견 세션에서 *수동 prototype* 으로 시작,
같은 날 별도 도구로 분리되어 공개.

#### 핵심 기능

- DART 5% 대량보유 보고서 본문 자동 분석
- 6단계 파이프라인: 본문 다운로드 → 키워드 슬라이스 → Gemini structured output →
  그룹 구조 역참조 → Google search grounding → 시나리오 분류
- 7개 시나리오 카테고리:
  행동주의캠페인 / 산업통합M&A / PE_buyout / 그룹지배강화 /
  특수관계자_변동 / 단순투자 / 기타
- 12개월 EV 분포 초안 (확률 가중 시나리오 5개)
- 모든 보고서에 *사람 검증 필수 항목* 의무 포함

#### 검증

- self-test 두 케이스 (프리미어 PE 14.65% + 모트렉스이에프엠 62.23%) 10/10 PASS
- activist-scout 두올 케이스 수동 분석 결과를 재현

#### 거버넌스

- MIT 라이선스
- 면책 조항 명시 (투자 권유 아님)
- 보고서 §7 *사람 검증 필수* 정신 유지
