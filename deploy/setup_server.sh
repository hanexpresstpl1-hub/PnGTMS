#!/usr/bin/env bash
# P&G 수주·배송 포털 — Lightsail(Ubuntu 22.04) 설치/갱신 스크립트
#
# 사용 (서버에서, 한 줄):
#   curl -fsSL https://raw.githubusercontent.com/hanexpresstpl1-hub/PnGTMS/main/deploy/setup_server.sh | bash -s -- <DOMAIN> [demo|prod] [DEMO_DATE]
#   예) ... | bash -s -- pg.3-39-129-208.sslip.io demo 2026-11-26
#
# 하는 일: apt(nginx, certbot, python3-venv, git) → ~/pg_portal 에 git clone/pull → venv + pip →
#          systemd pg-portal(gunicorn 127.0.0.1:5070) → nginx vhost(DOMAIN) → certbot HTTPS →
#          demo 모드면 시연 데이터 생성 + public_url 설정. 다시 실행하면 갱신만 한다(데이터 유지, demo면 재생성).
set -euo pipefail
DOMAIN="${1:?도메인을 지정하세요 (예: pg.3-39-129-208.sslip.io)}"
MODE="${2:-demo}"
DEMO_DATE="${3:-$(date +%F)}"
REPO="https://github.com/hanexpresstpl1-hub/PnGTMS.git"
APP_DIR="$HOME/pg_portal"
SVC="pg-portal"
PORT=5070
USER_NAME="$(whoami)"

echo "== [1/6] 패키지"
sudo timedatectl set-timezone Asia/Seoul 2>/dev/null || true
export TZ=Asia/Seoul
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot python3-certbot-nginx python3-venv python3-pip git >/dev/null

echo "== [2/6] 소스 ($APP_DIR)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin && git -C "$APP_DIR" reset -q --hard origin/main
else
  git clone -q "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p data

echo "== [3/6] systemd $SVC"
sudo tee /etc/systemd/system/$SVC.service >/dev/null <<UNIT
[Unit]
Description=P&G 수주·배송 포털 (gunicorn)
After=network.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=TZ=Asia/Seoul
Environment=PGPORTAL_DATA=$APP_DIR/data
ExecStart=$APP_DIR/.venv/bin/gunicorn -w 2 --threads 4 --timeout 120 -b 127.0.0.1:$PORT app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable -q $SVC
sudo systemctl restart $SVC

echo "== [4/6] nginx $DOMAIN"
sudo tee /etc/nginx/sites-available/$SVC >/dev/null <<NGX
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 40m;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 180;
    }
}
NGX
sudo ln -sf /etc/nginx/sites-available/$SVC /etc/nginx/sites-enabled/$SVC
sudo nginx -t -q && sudo systemctl reload nginx

echo "== [5/6] HTTPS (certbot)"
if sudo certbot certificates 2>/dev/null | grep -q "Domains: .*$DOMAIN"; then
  echo "  인증서 있음"
else
  sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || echo "  certbot 실패: 80/443 포트 개방과 도메인 DNS를 확인하세요 (HTTP로는 동작함)"
fi

echo "== [6/6] 앱 설정"
if [ "$MODE" = "demo" ]; then
  sudo systemctl stop $SVC
  TZ=Asia/Seoul PGPORTAL_DATA=$APP_DIR/data .venv/bin/python tools/seed_demo_1126.py "$DEMO_DATE" | tail -3
  echo "$DEMO_DATE $(date +%s)" > "$APP_DIR/data/last_reseed.txt"
  sudo systemctl start $SVC
fi
# 기사 링크·QR에 쓸 외부 주소
.venv/bin/python - <<PY
import sqlite3; db=sqlite3.connect("$APP_DIR/data/portal.db")
db.execute("INSERT INTO settings(key,value) VALUES('public_url','https://$DOMAIN') ON CONFLICT(key) DO UPDATE SET value=excluded.value"); db.commit(); print("public_url = https://$DOMAIN")
PY
# 자동 갱신 cron: 2분마다 GitHub main 확인 → 새 커밋이면 update.sh
echo "*/2 * * * * $USER_NAME /bin/bash $APP_DIR/deploy/autopull.sh" | sudo tee /etc/cron.d/pg-portal-autopull >/dev/null
sudo chmod 644 /etc/cron.d/pg-portal-autopull
sleep 1
curl -s -o /dev/null -w "로컬 응답: %{http_code}\n" http://127.0.0.1:$PORT/login
echo
echo "완료: https://$DOMAIN  (관리자 1111/1111 — 외부 공유 전 관리 > 사용자에서 비밀번호 변경)"
echo "자동 갱신: GitHub main 에 푸시하면 2분 안에 서버가 스스로 갱신·재시작합니다 (로그 $APP_DIR/data/autopull.log)"
echo "수동 갱신: cd $APP_DIR && bash deploy/update.sh"
