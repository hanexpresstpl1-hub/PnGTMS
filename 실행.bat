@echo off
chcp 65001 >nul
cd /d "%~dp0"
title P^&G 포털
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -c "import flask, openpyxl, requests, segno" 2>nul || %PY% -m pip install -q flask openpyxl requests segno
echo P^&G 수주·배송 포털 실행 중... (이 창을 닫으면 종료됩니다)
echo 브라우저: http://127.0.0.1:5070
%PY% app.py
pause
