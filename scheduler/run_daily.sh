#!/usr/bin/env bash
# 5pct-radar daily — 평일 09:30 launchd 실행
# 산출물: data/daily/daily_<YYYYMMDD>.md + Obsidian 미러

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

TODAY=$(date +%Y-%m-%d)

# 가드: 주말 스킵
DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
  echo "$(date) 주말 스킵" >> "$LOG_DIR/skipped.log"
  exit 0
fi

# 가드: 오늘 이미 성공
if [ -f "$LOG_DIR/daily_${TODAY}.log" ] && grep -q "✅ 저장" "$LOG_DIR/daily_${TODAY}.log" 2>/dev/null; then
  echo "$(date) 오늘 이미 완료 — 스킵" >> "$LOG_DIR/skipped.log"
  exit 0
fi

LOG="$LOG_DIR/daily_${TODAY}.log"
echo "=== $(date) 5pct-radar daily start ===" | tee -a "$LOG"

python -m five_pct_radar daily >> "$LOG" 2>&1
rc=$?

if [ $rc -ne 0 ]; then
  err=$(tail -3 "$LOG" | tr '\n' ' ' | cut -c1-160)
  echo "  ❌ 실패 (exit=$rc): $err" | tee -a "$LOG"
  osascript -e "display notification \"${err}\" with title \"[5pct-radar daily] 실패\" sound name \"Basso\"" 2>/dev/null || true
else
  echo "=== $(date) 5pct-radar daily done ===" | tee -a "$LOG"
fi

# 로그 30개 보관
ls -1t "$LOG_DIR"/daily_*.log 2>/dev/null | tail -n +31 | xargs -I{} rm -f {}
