#!/usr/bin/env bash
# 서버 갱신: git pull → pip → 서비스 재시작 (데이터 유지)
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin && git reset -q --hard origin/main
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart pg-portal
sleep 1
curl -s -o /dev/null -w "로컬 응답: %{http_code}\n" http://127.0.0.1:5070/login
echo "갱신 완료 $(git log --oneline -1)"
