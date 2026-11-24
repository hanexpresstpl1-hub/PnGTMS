"""세션 로그인과 역할 권한.

역할: admin(TPL2팀 관리자) / center(센터) / driver(기사) / shipper(P&G 담당자).
외부(P&G) 접속을 전제로 하므로 모든 화면은 로그인 필수, 역할별로 메뉴와 라우트를 막는다.
"""
from functools import wraps

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash

from .db import q

HOME = {"admin": "main.dashboard", "center": "main.dashboard", "carrier": "dispatch.board", "driver": "driver.today", "shipper": "portal.orders"}


def load_user():
    g.user = None
    g.link = None
    uid = session.get("uid")
    if uid:
        g.user = q("SELECT * FROM users WHERE id=? AND active=1", (uid,), one=True)
        if g.user is None:
            session.clear()
    lid = session.get("link_id")
    if lid:
        from .links import current_link
        g.link = current_link(lid)
        if g.link is None:
            session.pop("link_id", None)


def authenticate(login_id, password):
    u = q("SELECT * FROM users WHERE login_id=? AND active=1", (login_id,), one=True)
    if u and check_password_hash(u["pw_hash"], password):
        return u
    return None


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.path))
        return f(*a, **k)
    return w


def role_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if g.get("user") is None:
                return redirect(url_for("auth.login", next=request.path))
            if g.user["role"] not in roles:
                abort(403)
            return f(*a, **k)
        return w
    return deco


def home_for(user):
    return url_for(HOME.get(user["role"], "main.dashboard"))
