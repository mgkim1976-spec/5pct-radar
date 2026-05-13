# 5pct-radar — Operations Manual

> *어떻게 돌리는가*. 무엇을 만드는지는 [SPECIFICATIONS.md](SPECIFICATIONS.md), 변경 이력은 [CHANGELOG.md](CHANGELOG.md).

---

## 0. Quick Start (최초 5분)

```bash
cd /Users/mg_mac/MGPrj/5pct-radar

# 1) 환경
cp .env.example .env
# .env 편집 — DART_API_KEY, GEMINI_API_KEY 필수
pip install -e .

# 2) 회사코드 매핑 (분기 1회)
python -m five_pct_radar --build-corp-map

# 3) 10년 backtest (최초 1회, ~30분)
python -m five_pct_radar --lifecycle 3650

# 4) 첫 실행 확인
python -m five_pct_radar today        # 오늘 신고 dashboard
python -m five_pct_radar daily        # 통합 데일리
python -m five_pct_radar holdings     # 운용사 보유 + 변동 + 자동 dive
```

---

## 1. Daily Operations

### 1.1 자동 (launchd — 권장)

```bash
./scheduler/install.sh install   # 3 plist 등록
./scheduler/install.sh status    # 등록 확인
```

자동 실행 (2 plist 만):
- **평일 16:30** — `radar daily` 통합 마스터 (EXIT + opportunities + 변동 + 자동 dive + Obsidian)
- **일요일 03:00** — `run_weekly.sh` (corp_code + 10년 backtest)

로그: `logs/launchd_*.log` + `data/cron_*.log`

### 1.2 수동 명령어 (전체)

| 명령 | 용도 | 소요 |
|---|---|---|
| `radar today` | 오늘 5%+ 신고 dashboard + 점수 분류 | ~1분 |
| `radar dive <code>` | 단일 종목 deep dive (재무·잠정·매수단가·자기주식·A1) | ~30초 |
| `radar rank` | screen → 자동 dive → 정량 점수 → ranking | ~3~10분 |
| `radar daily` | today + rank + position 통합 | ~5분 |
| `radar holdings` | 운용사 보유 + 변동 + 자동 dive + Obsidian | ~5분 |
| `radar position {add\|list\|close\|note}` | 포지션 tracker | 즉시 |
| `radar size --actor X --capital N --price P` | Kelly 변형 사이즈 추천 | 즉시 |
| `radar journal review` | 청산 사후 회고 | 즉시 |
| `radar notify` | 텔레그램 푸시 | 즉시 |
| `radar <rcept_no>` | 단일 5%+ 신고 분석 (Gemini LLM 사용) | ~30초 |
| `radar --scan-recent 1` | 최근 1일 모든 신고 일괄 분석 | ~수분 |
| `radar --lifecycle 3650` | 10년 backtest 재실행 | ~30분 |
| `radar --build-corp-map` | DART corp_code 매핑 갱신 | ~30초 |

### 1.3 오전 루틴 (3분)

1. **Obsidian 열기** → `theme_radar/5pct_radar/<오늘>/`
2. `_index.md` 에서 *오늘의 1순위* 확인
3. `holdings.md` 에서 *변동 운용사* 점검
4. `rank.md` 매트릭스 — *어제 vs 오늘 점수 변화*
5. EXIT 알림 (A1 트리거) 있으면 `radar position list`

### 1.4 진입 결정 시

```bash
# 1) 종목 검증
python -m five_pct_radar dive <code>

# 2) 사이즈 추천
python -m five_pct_radar size --actor "VIP" --capital <원> --price <원>

# 3) §13 사람 검증 (운용사 의도, 부채 구조, 환율 등)
#    deep dive 보고서 §13 체크리스트

# 4) 진입 후 tracker 등록
python -m five_pct_radar position add <code> --price <원> --shares <N> \
    --actor "VIP" --note "§13 검증 결과"
```

---

## 2. Weekly / Monthly Operations

| 주기 | 작업 | 명령어 |
|---|---|---|
| 매주 일요일 03:00 (launchd) | backtest + corp_code | `scheduler/run_weekly.sh` |
| 매월 1회 | 청산 사후 회고 통계 | `radar journal review` |
| 분기 1회 | 자기주식 보유 갱신 | `radar holdings` (자동) |

---

## 3. 모니터링 체크리스트

- [ ] `data/daily/daily_<today>.md` 가 09:30 이후 생성됐는가
- [ ] `data/holdings/<today>/` 폴더가 16:30 이후 생성됐는가
- [ ] Obsidian `theme_radar/5pct_radar/<today>/` 에 동기화됐는가 (5~30분 지연 가능)
- [ ] `logs/launchd_stderr.log` 에 ERROR 없는가
- [ ] DART API 호출 일일 한도 (10,000회) 내인가
- [ ] 현재 포지션 A1 트리거 도달 종목 있는가

---

## 4. 장애 대응

### 4.1 DART API 401/403

증상: `status=101` 또는 `403`
원인: `DART_API_KEY` 만료/오류
대응:
```bash
# 1) .env 확인
grep DART_API_KEY .env

# 2) DART 사이트 로그인 후 키 재발급
# https://opendart.fss.or.kr/uss/umt/login/loginPage.do
```

### 4.2 yfinance 가격 N/A

증상: `possibly delisted` 또는 `No data found`
원인: 종목 코드 변경, 상장폐지, 또는 일시적 API 오류
대응:
- *상장폐지* 종목은 dive 결과 *-100%* 표시 — 정상
- 일시 오류는 자동 retry 안 됨 → 다음 실행 시 재시도

### 4.3 lifecycle backtest 시간 초과

증상: `--lifecycle 3650` 실행 중단
원인: KRX 세션 만료 (이전), 현재는 yfinance 사용 — 일반적으로 발생 안 함
대응:
```bash
# 부분 저장 확인 (lifecycle_*_partial.json)
ls data/filing_intel/lifecycle_*_partial.json

# checkpoint 부터 재시작 (lifecycle_monitor 자동 처리)
python -m five_pct_radar --lifecycle 3650
```

### 4.4 launchd plist 미실행

증상: `data/daily/daily_<today>.md` 미생성
대응:
```bash
# 상태 확인
./scheduler/install.sh status

# 로그 확인
tail -50 logs/launchd_stderr.log

# 재설치
./scheduler/install.sh uninstall && ./scheduler/install.sh install

# 수동 즉시 실행 테스트
launchctl start com.mgprj.5pct_radar.daily
```

### 4.5 Obsidian 미러 실패

증상: iCloud 폴더에 파일 없음
원인 1: macOS Full Disk Access 권한 부재
대응 1:
```
시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한
→ /bin/bash 추가 (launchd 실행 주체)
```

원인 2: iCloud 동기화 지연 (5~30분 정상)
대응 2: 기다림 또는 `data/holdings/<today>/` 로컬 폴더 확인

### 4.6 변동 종목 자동 dive 실패

증상: `holdings` 실행 후 `dive_<code>.md` 누락
원인: DART document.xml 가져오기 실패, ZIP 인코딩 오류 등
대응:
- 다음날 자동 재시도
- 수동 dive: `radar dive <code>`

---

## 5. 운영 담당자 노트

### 5.1 자주 바뀌는 것
- **DART API endpoint 명** — 가끔 변경 (`stockTotqyStus.json` → `tesstkAcqsDspsSttus.json` 사례)
- **운용사 backtest hit15** — 매주 weekly refresh 시 갱신
- **잠정실적 본문 형식** — 회사마다 다름, 일부 파싱 실패 가능

### 5.2 건드리면 위험한 것
- `data/positions.json` — 수동 편집 시 tracker 일관성 파괴
- `data/holdings/<date>/` 폴더 — Obsidian 미러 후 수동 수정 시 다음 실행에서 덮어씀
- `data/filing_intel/lifecycle_*_3650d.json` — backtest 결과, 재생성 30분 소요

### 5.3 백업 정책
- `data/positions*.json` — git tracking 아님 (개인 데이터). 별도 수동 백업 권장
- `data/holdings/<date>/` — Obsidian iCloud 자동 동기화
- `data/filing_intel/` — git tracking (보고서·인덱스만, JSON 제외)

### 5.4 비용 (DART + Gemini)
- DART: 무료 (10,000 호출/일 한도)
- Gemini: 무료 티어 충분 (단일 신고 분석 시만 호출)
- yfinance: 무료 (rate limit 자체적용)

---

## 6. 첫 24시간 검증

launchd 등록 후:

```bash
# 1) 다음 평일 09:30 이후
ls data/daily/daily_$(date +%Y%m%d).md
grep "✅ 저장" data/cron_daily.log

# 2) 다음 평일 16:30 이후
ls data/holdings/$(date +%Y-%m-%d)/
grep "변동:" data/cron_holdings.log

# 3) Obsidian 확인
ls "/Users/mg_mac/Library/Mobile Documents/iCloud~md~obsidian/Documents/theme_radar/5pct_radar/$(date +%Y-%m-%d)/"

# 4) 다음 일요일 03:00 이후
ls data/filing_intel/lifecycle_$(date +%Y%m%d)_3650d.json
```

모두 정상이면 운영 안정.
