from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from core.auth import authenticate, home_for

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(home_for(g.user))
    if request.method == "POST":
        u = authenticate(request.form.get("login_id", "").strip(), request.form.get("password", ""))
        if u is None:
            flash("아이디 또는 비밀번호가 맞지 않습니다.", "error")
        else:
            session.clear()
            session["uid"] = u["id"]
            session.permanent = False
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else home_for(u))
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
