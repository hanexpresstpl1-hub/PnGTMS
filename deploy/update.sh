#!/usr/bin/env bash
# 서버 갱신: git pull → pip → 서비스 재시작 (데이터 유지). 자동 갱신 cron(2분마다 autopull.sh)도 함께 설치한다.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
git fetch -q origin && git reset -q --hard origin/main
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart pg-portal
# 자동 갱신 cron (없으면 설치)
CRON_FILE=/etc/cron.d/pg-portal-autopull
if [ ! -f "$CRON_FILE" ]; then
  echo "*/2 * * * * $(whoami) /bin/bash $APP_DIR/deploy/autopull.sh" | sudo tee "$CRON_FILE" >/dev/null
  sudo chmod 644 "$CRON_FILE"
  echo "자동 갱신 cron 설치: 2분마다 GitHub main 확인 → 새 커밋이면 자동 갱신·재시작 (로그 data/autopull.log)"
fi
sleep 1
curl -s -o /dev/null -w "로컬 응답: %{http_code}\n" http://127.0.0.1:5070/login
echo "갱신 완료 $(git log --oneline -1)"
