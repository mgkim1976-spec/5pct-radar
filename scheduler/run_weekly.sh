#!/usr/bin/env bash
# 5pct-radar weekly — 일요일 03:00 launchd 실행
# 작업: corp_code 매핑 + 10년 lifecycle backtest 갱신 (~30분)

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

TODAY=$(date +%Y-%m-%d)
LOG="$LOG_DIR/weekly_${TODAY}.log"
echo "=== $(date) 5pct-radar weekly start ===" | tee -a "$LOG"

# 1) corp_code 매핑
echo "[1/3] corp_code 매핑 갱신" >> "$LOG"
python -m five_pct_radar --build-corp-map >> "$LOG" 2>&1

# 2) 10년 lifecycle backtest
echo "[2/3] 10년 lifecycle backtest" >> "$LOG"
python -m five_pct_radar --lifecycle 3650 >> "$LOG" 2>&1
rc=$?

# 3) retrospect — 알파 검증 (1주 + 1개월)
echo "[3/4] retrospect 알파 검증" >> "$LOG"
python -m five_pct_radar retrospect --days 7 >> "$LOG" 2>&1
python -m five_pct_radar retrospect --days 30 >> "$LOG" 2>&1

# 4) today 1회 (확인용)
echo "[4/4] today 검증" >> "$LOG"
python -m five_pct_radar today >> "$LOG" 2>&1

if [ $rc -ne 0 ]; then
  err=$(tail -3 "$LOG" | tr '\n' ' ' | cut -c1-160)
  echo "  ❌ 실패 (exit=$rc): $err" | tee -a "$LOG"
  osascript -e "display notification \"${err}\" with title \"[5pct-radar weekly] 실패\" sound name \"Basso\"" 2>/dev/null || true
else
  echo "=== $(date) 5pct-radar weekly done ===" | tee -a "$LOG"
fi

# 주간 로그 12주 보관
ls -1t "$LOG_DIR"/weekly_*.log 2>/dev/null | tail -n +13 | xargs -I{} rm -f {}
