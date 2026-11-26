#!/usr/bin/env bash
# 서버 갱신: git pull → pip → 서비스 재시작 (데이터 유지). 자동 갱신 cron(2분마다 autopull.sh)도 함께 설치한다.
set -euo pipefail
export TZ=Asia/Seoul
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
git fetch -q origin && git reset -q --hard origin/main
.venv/bin/pip install -q -r requirements.txt
git log --format="%h %ci %s" -1 > data/version.txt
# 시연 데이터 재생성 트리거: deploy/RESEED (첫 단어=납품일) 내용이 지난번과 다르면 재생성
if [ -f deploy/RESEED ]; then
  WANT="$(tr -d '\r' < deploy/RESEED | head -1)"
  HAVE="$(cat data/last_reseed_token.txt 2>/dev/null || true)"
  if [ -n "$WANT" ] && [ "$WANT" != "$HAVE" ]; then
    DATE_ARG="$(echo "$WANT" | awk '{print $1}')"
    echo "시연 데이터 재생성: $WANT"
    sudo systemctl stop pg-portal
    TZ=Asia/Seoul PGPORTAL_DATA=$APP_DIR/data .venv/bin/python tools/seed_demo_1126.py "$DATE_ARG" | tail -2
    .venv/bin/python - <<PY
import sqlite3; db=sqlite3.connect("$APP_DIR/data/portal.db")
db.execute("INSERT INTO settings(key,value) VALUES('public_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("https://" + open("/etc/nginx/sites-available/pg-portal").read().split("server_name ")[1].split(";")[0].strip(),)); db.commit()
PY
    echo "$WANT" > data/last_reseed_token.txt
  fi
fi
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
