"""P&G 수주·배송 포털 (한익스프레스 TPL2팀)

엑셀 수주 업로드 → 검증 → 가온 자동 수주등록 → 배차(직송/거점경유) → 기사 모바일 추적 → P&G 화주 조회.
실행: 실행.bat 또는 `py app.py` → http://127.0.0.1:5070
"""
import os
import sys
import threading
import webbrowser

from flask import Flask, g, redirect, url_for

from core import db as dbm
from core.auth import load_user
from routes.admin import bp as admin_bp
from routes.auth import bp as auth_bp
from routes.dispatch import bp as dispatch_bp
from routes.driver import bp as driver_bp
from routes.main import bp as main_bp
from routes.masters import bp as masters_bp
from routes.orders import bp as orders_bp
from routes.portal import bp as portal_bp
from routes.returns import bp as returns_bp
from routes.split import bp as split_bp

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    RES = getattr(sys, "_MEIPASS", BASE)
else:
    BASE = RES = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PGPORTAL_DATA") or os.path.join(BASE, "data")
UPLOAD = os.path.join(DATA, "uploads")
POD_DIR = os.path.join(DATA, "pod")
for d in (DATA, UPLOAD, POD_DIR):
    os.makedirs(d, exist_ok=True)
PORT = int(os.environ.get("PGPORTAL_PORT") or 5070)


def create_app():
    app = Flask(__name__, template_folder=os.path.join(RES, "templates"), static_folder=os.path.join(RES, "static"))
    secret_file = os.path.join(DATA, "secret.key")
    if not os.path.exists(secret_file):
        with open(secret_file, "wb") as f:
            f.write(os.urandom(32))
    with open(secret_file, "rb") as f:
        app.secret_key = f.read()
    app.config.update(DB_PATH=os.path.join(DATA, "portal.db"), UPLOAD_DIR=UPLOAD, POD_DIR=POD_DIR,
                      MAX_CONTENT_LENGTH=30 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    dbm.init_db(app)
    app.teardown_appcontext(dbm.close_db)
    app.before_request(load_user)

    @app.context_processor
    def inject():
        return {"ORDER_STATUS_MAP": dbm.ORDER_STATUS_MAP, "SHIP_STATUS_MAP": dbm.SHIP_STATUS_MAP, "ROLE_MAP": dbm.ROLE_MAP,
                "DTYPE_MAP": dbm.DTYPE_MAP, "RETURN_STATUS_MAP": dbm.RETURN_STATUS_MAP, "DRIVER_ACTIONS": dbm.DRIVER_ACTIONS, "NEXT_ACTION": dbm.NEXT_ACTION,
                "setting": dbm.setting, "user": g.get("user"), "link": g.get("link")}

    @app.route("/")
    def index():
        from core.auth import home_for
        if g.get("user") is None:
            return redirect(url_for("driver.today") if g.get("link") else url_for("auth.login"))
        return redirect(home_for(g.user))

    for bp in (auth_bp, main_bp, orders_bp, dispatch_bp, masters_bp, driver_bp, portal_bp, admin_bp, split_bp, returns_bp):
        app.register_blueprint(bp)
    return app


app = create_app()

if __name__ == "__main__":
    if os.environ.get("PGPORTAL_NOBROWSER") != "1" and "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:%d" % PORT)).start()
    # 외부(P&G) 접속은 Lightsail 배포 시 리버스 프록시(HTTPS) 뒤에서 host=0.0.0.0 으로 올린다.
    app.run(host=os.environ.get("PGPORTAL_HOST", "127.0.0.1"), port=PORT, debug=False)
