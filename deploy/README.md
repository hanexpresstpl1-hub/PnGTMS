# Lightsail 배포

대상: 기존 UnileverVA 인스턴스(Ubuntu 22.04, nginx·certbot 설치됨)에 별도 서비스로 올린다. 도메인은 sslip.io(IP 기반, DNS 설정 불필요)를 쓴다.

## 최초 설치 (서버 SSH에서 한 줄)

```
curl -fsSL https://raw.githubusercontent.com/hanexpresstpl1-hub/PnGTMS/main/deploy/setup_server.sh | bash -s -- pg.3-39-129-208.sslip.io demo 2026-11-26
```

- 인자: `<도메인> [demo|prod] [시연 납품일]`. demo면 `tools/seed_demo_1126.py`로 시연 데이터를 만들고, prod면 빈 DB로 시작한다.
- 만드는 것: `~/pg_portal`(git clone), `.venv`, systemd `pg-portal`(gunicorn 127.0.0.1:5070), nginx vhost, certbot 인증서(HTTPS 자동 리다이렉트), 설정 `public_url=https://<도메인>`.
- Lightsail 방화벽에서 80·443이 열려 있어야 한다(UnileverVA는 이미 열려 있음).

## 갱신

로컬에서 수정 → `git push` → 서버에서:

```
cd ~/pg_portal && bash deploy/update.sh
```

데이터(`~/pg_portal/data`)는 유지된다. 시연 데이터를 초기화하려면 `sudo systemctl stop pg-portal && PGPORTAL_DATA=~/pg_portal/data .venv/bin/python tools/seed_demo_1126.py 2026-11-26 && sudo systemctl start pg-portal`.

## 확인·로그

```
sudo systemctl status pg-portal
sudo journalctl -u pg-portal -n 50 --no-pager
sudo tail -n 50 /var/log/nginx/error.log
```

## 보안 메모

- 관리자 기본 계정(1111/1111)과 시연 계정(demo1234)은 공개 주소로 공유하기 전에 관리 > 사용자에서 바꾼다.
- 가온 비밀번호는 저장하지 않고 연결할 때마다 입력한다. 카카오 REST 키·SMS 키는 설정 화면에서만 입력(DB에 저장, 저장소에 없음).
- 백업은 `~/pg_portal/data` 폴더를 통째로 복사하면 된다(SQLite + 업로드 + 인수증 사진).
