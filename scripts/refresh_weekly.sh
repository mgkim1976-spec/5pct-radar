#!/usr/bin/env bash
# 주간 backtest + corp_code 매핑 자동 갱신.
#
# 사용법:
#   chmod +x scripts/refresh_weekly.sh
#   ./scripts/refresh_weekly.sh
#
# Cron 예시 (매주 일요일 새벽 3시):
#   0 3 * * 0 cd ~/MGPrj/5pct-radar && ./scripts/refresh_weekly.sh >> data/refresh.log 2>&1

set -euo pipefail

PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ_DIR"

LOG_TS=$(date '+%Y-%m-%d %H:%M:%S')
echo "===== $LOG_TS 5pct-radar weekly refresh 시작 ====="

# 1. corp_code 매핑 갱신 (분기마다 하면 충분하지만 매주 해도 무방)
echo "[1/3] DART corp_code 매핑 갱신 ..."
python -m five_pct_radar --build-corp-map

# 2. lifecycle backtest 재실행 (최근 10년)
echo "[2/3] 10년 lifecycle backtest 갱신 ..."
python -m five_pct_radar --lifecycle 3650

# 3. 오늘 dashboard 1회 실행 → today_<DATE>.md 저장
echo "[3/3] 오늘 dashboard 저장 ..."
python -m five_pct_radar today > /dev/null

echo "===== $(date '+%Y-%m-%d %H:%M:%S') refresh 완료 ====="
