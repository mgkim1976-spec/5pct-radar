#!/usr/bin/env bash
# 5pct-radar launchd installer — 3 plist 일괄 관리
#
# 사용:
#   ./scheduler/install.sh install    # 3 plist 등록
#   ./scheduler/install.sh uninstall  # 모두 제거
#   ./scheduler/install.sh status     # 상태 확인
#   ./scheduler/install.sh test-daily # 즉시 1회 daily 실행 (검증용)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHAGENTS="$HOME/Library/LaunchAgents"
PLISTS=(
  "com.mgprj.5pct_radar.daily"
  "com.mgprj.5pct_radar.holdings"
  "com.mgprj.5pct_radar.weekly"
)

cmd="${1:-status}"

case "$cmd" in
  install)
    mkdir -p "$LAUNCHAGENTS"
    chmod +x "$SCRIPT_DIR"/run_*.sh
    for label in "${PLISTS[@]}"; do
      src="$SCRIPT_DIR/$label.plist"
      dst="$LAUNCHAGENTS/$label.plist"
      cp "$src" "$dst"
      launchctl unload "$dst" 2>/dev/null || true
      launchctl load "$dst"
      echo "✅ $label 등록"
    done
    echo ""
    echo "스케줄:"
    echo "  - daily    : 평일 09:30 KST"
    echo "  - holdings : 평일 16:30 KST"
    echo "  - weekly   : 일요일 03:00 KST"
    echo ""
    echo "즉시 테스트: ./scheduler/install.sh test-daily"
    ;;
  uninstall)
    for label in "${PLISTS[@]}"; do
      dst="$LAUNCHAGENTS/$label.plist"
      launchctl unload "$dst" 2>/dev/null || true
      rm -f "$dst"
      echo "✅ $label 제거"
    done
    ;;
  status)
    for label in "${PLISTS[@]}"; do
      if launchctl list | grep -q "$label"; then
        echo "✅ $label : 등록됨"
      else
        echo "❌ $label : 미등록"
      fi
    done
    ;;
  test-daily)
    echo "즉시 daily 실행 (logs/daily_$(date +%Y-%m-%d).log 확인)..."
    launchctl start com.mgprj.5pct_radar.daily
    sleep 2
    tail -5 "$SCRIPT_DIR/../logs/daily_$(date +%Y-%m-%d).log" 2>/dev/null || echo "(아직 로그 없음 — 잠시 후 다시)"
    ;;
  test-holdings)
    echo "즉시 holdings 실행..."
    launchctl start com.mgprj.5pct_radar.holdings
    sleep 2
    tail -5 "$SCRIPT_DIR/../logs/holdings_$(date +%Y-%m-%d).log" 2>/dev/null || echo "(아직 로그 없음)"
    ;;
  *)
    echo "Usage: $0 {install|uninstall|status|test-daily|test-holdings}"
    exit 1
    ;;
esac
