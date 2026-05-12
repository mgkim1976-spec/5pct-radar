# 5pct-radar

**한국 거래소 5% 대량보유 신고를 매일 자동 추적·분류·우선순위화하는 도구.**

> *행동주의 펀드 / 가치투자 운용사가 5%+ 지분을 신고하면, 본문을 자동으로 읽고
> 시나리오 분류 + 정량 점수 + 매수 단가 비교 + A1 진입 권장가 까지 산출.*

---

## 📘 문서 구조 (MGPrj 3파일 표준)

| 문서 | 내용 |
|---|---|
| **[SPECIFICATIONS.md](SPECIFICATIONS.md)** | *무엇을 만드는가* — 산출물·데이터·파이프라인·품질 |
| **[OPERATIONS.md](OPERATIONS.md)** | *어떻게 돌리는가* — Quick Start·launchd·모니터링·장애 대응 |
| **[CHANGELOG.md](CHANGELOG.md)** | *무엇이 바뀌었는가* — 버전별 변경 |
| [STORY.md](STORY.md) | 9일간의 진화 narrative (참고) |
| [docs/STRATEGY_FINDINGS.md](docs/STRATEGY_FINDINGS.md) | backtest 분석 + 운용사 검증 결과 |

---

## ⚡ 30초 사용법

```bash
# 1. 설치
pip install -e .
cp .env.example .env       # DART_API_KEY + GEMINI_API_KEY 입력

# 2. 자동화 등록 (macOS launchd)
./scheduler/install.sh install

# 3. 매일 자동 실행 결과 확인 (다음 평일 09:30 / 16:30 부터)
ls data/daily/             # 오늘 신고 + 우선순위 ranking
ls data/holdings/$(date +%Y-%m-%d)/  # 운용사 보유 변동
```

Obsidian Vault 도 자동 미러:
```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/theme_radar/5pct_radar/
```

---

## 🎯 핵심 명령어 (11개)

```bash
radar daily              # 통합 데일리 리포트 (오늘 신고 + 우선순위)
radar holdings           # 운용사 보유 + 변동 + 자동 dive
radar dive <code>        # 단일 종목 deep dive (재무·잠정·매수단가·A1)
radar rank               # screen → 자동 dive → 정량 점수 매트릭스
radar today              # 오늘 5%+ 신고 분류 dashboard
radar position {add|list|close|note}  # 내 포지션 tracker + A1 알림
radar size --actor X --capital N --price P  # Kelly 사이즈 추천
radar journal review     # 청산 사후 회고
radar notify             # 텔레그램 알림
radar <rcept_no>         # 단일 신고 LLM 분석 (Gemini)
radar --lifecycle 3650   # 10년 backtest 재실행
```

전체 옵션은 [OPERATIONS.md §1.2](OPERATIONS.md) 참조.

---

## 🏗️ 무엇이 다른가

다른 5%+ 추적 도구와 차이:

1. **운용사 backtest 검증** — 10년 lifecycle 로 운용사별 hit15 측정.
   *베어링 49% / VIP 45% / 에이티넘 5%* — 검증된 follow 신호만 강조
2. **정량 점수 매트릭스** — actor·잠정실적·PBR·자기주식·부채·가격 6항목 100점
3. **매수 단가 자동 추출** — 신고 본문 document.xml 파싱 → 가중평균 매수가
4. **자기주식 catalyst 자동 감지** — DART `tesstkAcqsDspsSttus.json` 보유·매입 추적
5. **운용사 변동 자동 추적** — 어제 vs 오늘 신규/철수/비중변동
6. **변동 종목 자동 dive** — 신규 진입·증가 종목에 대해 자동 30초 분석
7. **Obsidian 미러** — 모든 리포트가 iCloud Vault 에 자동 동기화
8. **§13 사람 검증 의무** — 모든 보고서 마지막. *자동화는 발견·점수화만*

---

## 💡 발견 사례 (오로라 / LF)

도구가 *수동 분석으로는 못 본* 결정적 패턴 자동 발견:

- **오로라 (039830)**: VIP 가 *1Q 잠정실적 발표 다음날* 81,693주 폭매수 (전체 매수의 71%) — *실적 confirm 후 conviction*
- **LF (093050)**: PBR 0.39 + **자기주식 8.8% 보유 + 직전 156만주 매입** — *deep value + 주주환원 catalyst* 의 교차점. VIP 가 한 달간 33회 매수한 진짜 이유

자세한 케이스는 [STORY.md](STORY.md) 참조.

---

## 📊 backtest 검증 (10년 lifecycle)

A1 exit (+20% 익절 / -10% 손절) 적용 시 운용사별 hit15:

| 운용사 | hit15 | 시그널 |
|---|---:|---|
| 베어링자산운용 | **49%** | 🟢 강한 매수 |
| 브이아이피자산운용 (VIP) | **45%** | 🟢 매수 (n=192, 최대 표본) |
| 신영 (최초 진입만) | 44% | 🟡 |
| 한국투자밸류 | 44% | 🟡 |
| 트러스톤 | 35% | 🟡 |
| **에이티넘인베스트** | **5%** | 🔴 회피 |

baseline (filing follow 무차별) 25% 대비 *베어링 +24%p, VIP +20%p*.

자세한 결과: [docs/STRATEGY_FINDINGS.md](docs/STRATEGY_FINDINGS.md)

---

## ⚠️ 정직한 한계

- **과거 데이터 기반 backtest** — 한국 상법 개정 regime change 로 미래 보장 없음
- **5% 미만 축소 후** 추가 매도는 신고 의무 없음 → 추정 ≤ 실제
- **§13 사람 검증 의무** — 자동화는 발견·점수화. *결정은 사람*
- **확률·calibration 주장 안 함** — 어떤 종목 *권유* 아님

---

## 🏗️ 코드 구조

```
src/five_pct_radar/
├── __init__.py / __main__.py / config.py    # CLI + 설정
│
├── core/         # 데이터 fetcher 공용
│   ├── dart_client.py        # DART OpenAPI client
│   ├── corp_code.py          # corp_code 매핑
│   └── fetch_filing.py       # document.xml 파싱
│
├── analysis/     # 시그널 분석·LLM
│   ├── classify.py           # 시나리오 분류
│   ├── extract_llm.py        # Gemini structured
│   ├── grounding.py          # Google grounding
│   ├── resolve_filer.py      # 보고자 그룹 매핑
│   ├── scan.py               # 배치 모드
│   ├── catalyst_chain.py     # 후속 공시 추적
│   ├── report.py             # 단일 신고 보고서
│   └── score_filing.py       # 단일 신고 점수
│
├── backtest/     # backtest·통계
│   ├── lifecycle_monitor.py  # 10년 lifecycle backtest
│   ├── backtest_actor.py     # 운용사 backtest
│   ├── backtest_phase0.py    # Phase 0 검증
│   ├── actor_stats.py        # 운용사 ranking
│   └── score_model.py        # 5-component 점수
│
└── workflow/     # 매일 명령어 (CLI subcommands)
    ├── daily.py              # 통합 데일리
    ├── today.py              # 오늘 신고 dashboard
    ├── dive.py               # 단일 종목 deep dive
    ├── rank.py               # 우선순위 매트릭스
    ├── holdings.py           # 운용사 보유 모니터링
    ├── movements.py          # 어제 vs 오늘 변동
    ├── position.py           # 포지션 tracker
    ├── sizing.py             # Kelly 사이즈
    ├── journal.py            # 사후 회고
    └── notify.py             # 텔레그램
```

---

## 📄 라이선스

MIT — [LICENSE](LICENSE)

면책 — [DISCLAIMER.md](DISCLAIMER.md)

기여 — [CONTRIBUTING.md](CONTRIBUTING.md)

---

*v0.1.0 · 2026-05-09 public release · activist-scout 의 한 catalyst 분석 세션에서 파생*
