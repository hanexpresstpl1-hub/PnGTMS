#!/usr/bin/env bash
# 자동 갱신: GitHub origin/main 에 새 커밋이 있으면 update.sh 실행. cron(/etc/cron.d/pg-portal-autopull)이 2분마다 호출.
# 로그: ~/pg_portal/data/autopull.log
set -uo pipefail
export TZ=Asia/Seoul
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="/tmp/pg-portal-autopull.lock"
LOG="$APP_DIR/data/autopull.log"
exec 9>"$LOCK"
flock -n 9 || exit 0
cd "$APP_DIR" || exit 0
git fetch -q origin main 2>>"$LOG" || exit 0
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
[ "$LOCAL" = "$REMOTE" ] && exit 0
{
  echo "== $(date '+%F %T') 새 커밋 감지 $LOCAL -> $REMOTE"
  bash "$APP_DIR/deploy/update.sh"
} >>"$LOG" 2>&1
# 로그 크기 제한
[ "$(wc -c <"$LOG")" -gt 500000 ] && tail -n 300 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit 0
