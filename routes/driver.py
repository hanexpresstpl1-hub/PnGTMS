"""기사 화면.

접속 경로 두 가지(계정 없음):
  /d/<token>   배차 때 발급된 링크. 처음 연 기기에 묶이고 납품일+N일까지 유효 → 세션에 link_id
  /m/login     예비: 차량번호 + 휴대폰 뒷 4자리 → 전후 1일 배차가 있으면 같은 링크 발급
관리자·센터는 /m/?driver_id=..&date=.. 로 미리보기.
"""
import datetime as dt
import os
import uuid
from functools import wraps

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, send_from_directory, session, url_for

from core import service
from core.db import DRIVER_ACTIONS, execute, now, q
from core.links import fallback_login, open_link

bp = Blueprint("driver", __name__)
DEV_COOKIE = "pgd"


def _staff():
    return g.get("user") is not None and g.user["role"] in ("admin", "center", "carrier")


def driver_access(f):
    @wraps(f)
    def w(*a, **k):
        if g.get("link") is None and not _staff():
            return redirect(url_for("driver.login", next=request.path))
        return f(*a, **k)
    return w


def _enter(link):
    session["link_id"] = link["id"]
    session.permanent = True
    return redirect(url_for("driver.today"))


@bp.route("/d/<token>")
def open_token(token):
    dev = request.cookies.get(DEV_COOKIE) or uuid.uuid4().hex
    link, err = open_link(token, dev)
    if err:
        msgs = {"notfound": "없는 링크입니다.", "revoked": "배차가 변경되어 더 이상 쓸 수 없는 링크입니다. 담당자에게 새 링크를 요청하세요.",
                "expired": "유효기간이 지난 링크입니다.", "device": "이 링크는 다른 기기에서 이미 열렸습니다. 담당자에게 재발급을 요청하거나 차량번호로 들어오세요."}
        resp = make_response(render_template("driver/link_error.html", msg=msgs[err]), 403)
    else:
        resp = _enter(link)
    resp.set_cookie(DEV_COOKIE, dev, max_age=400 * 86400, httponly=True, samesite="Lax")
    return resp


@bp.route("/m/login", methods=["GET", "POST"])
def login():
    if g.get("link"):
        return redirect(url_for("driver.today"))
    if request.method == "POST":
        link, err = fallback_login(request.form.get("plate", ""), request.form.get("last4", ""))
        if err:
            flash(err, "error")
        else:
            dev = request.cookies.get(DEV_COOKIE) or uuid.uuid4().hex
            execute("UPDATE driver_links SET device_id=COALESCE(device_id,?), first_open_at=COALESCE(first_open_at,?), last_open_at=?, open_count=open_count+1 WHERE id=?",
                    (dev, now(), now(), link["id"]))
            resp = _enter(link)
            resp.set_cookie(DEV_COOKIE, dev, max_age=400 * 86400, httponly=True, samesite="Lax")
            return resp
    return render_template("driver/login.html")


@bp.route("/m/logout")
def logout():
    session.pop("link_id", None)
    return redirect(url_for("driver.login"))


@bp.route("/m/")
@driver_access
def today():
    link = g.get("link")
    if link:
        did, vid, date = link["driver_id"], link["vehicle_id"], link["date"]
        drivers = []
    else:  # 관리자 미리보기
        did, vid = request.args.get("driver_id", type=int), None
        date = request.args.get("date") or dt.date.today().strftime("%Y-%m-%d")
        drivers = q("SELECT * FROM drivers WHERE active=1 ORDER BY name")
    where, args = ["s.driver_id=?", "s.planned_date=?", "s.status!='cancelled'", "o.delivery_type='direct'"], [did, date]
    if vid:
        where.append("s.vehicle_id=?"); args.append(vid)
    rows = q("SELECT s.*, o.order_no, o.store_name, o.address, o.delivery_type, o.qty, o.box, o.plt, o.memo, v.plate FROM shipments s "
             "JOIN orders o ON o.id=s.order_id LEFT JOIN vehicles v ON v.id=s.vehicle_id WHERE %s "
             "ORDER BY CASE s.status WHEN 'delivered' THEN 1 ELSE 0 END, s.seq, s.id" % " AND ".join(where), args) if did else []
    done = sum(1 for r in rows if r["status"] == "delivered")
    return render_template("driver/today.html", rows=rows, date=date, did=did, drivers=drivers, done=done)


@bp.route("/m/<int:sid>/<act>", methods=["POST"])
@driver_access
def action(sid, act):
    s = q("SELECT s.*, o.delivery_type FROM shipments s JOIN orders o ON o.id=s.order_id WHERE s.id=?", (sid,), one=True)
    if not s or act not in DRIVER_ACTIONS:
        abort(404)
    if s["delivery_type"] != "direct":
        abort(403, "거점경유 건은 기사 앱에서 처리하지 않습니다.")
    link = g.get("link")
    if link and not (s["driver_id"] == link["driver_id"] and s["vehicle_id"] == link["vehicle_id"] and s["planned_date"] == link["date"]):
        abort(403)

    def _f(k):
        try:
            return float(request.form.get(k)) if request.form.get(k) else None
        except ValueError:
            return None
    _, info = service.advance_shipment(sid, act, _f("lat"), _f("lng"), _f("acc"))
    if act == "deliver":
        receiver = request.form.get("receiver", "").strip()
        note = request.form.get("note", "").strip()
        photo = request.files.get("photo")
        path = None
        if photo and photo.filename:
            ext = os.path.splitext(photo.filename)[1].lower() or ".jpg"
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"
            path = "%s_%s%s" % (sid, uuid.uuid4().hex[:8], ext)
            photo.save(os.path.join(current_app.config["POD_DIR"], path))
        execute("INSERT INTO pods(shipment_id,receiver,photo_path,note,signed_at,user_id) VALUES(?,?,?,?,?,?)",
                (sid, receiver, path, note, now(), g.user["id"] if g.get("user") else None))
    flash("%s 처리했습니다.%s" % (DRIVER_ACTIONS[act][1], (" " + info) if info else ""), "ok" if "확인 필요" not in info and "없음" not in info else "warn")
    if link:
        return redirect(url_for("driver.today"))
    return redirect(url_for("driver.today", date=s["planned_date"], driver_id=request.args.get("driver_id")))


@bp.route("/m/pod/<path:name>")
def pod_file(name):
    if g.get("link") is None and g.get("user") is None:
        abort(403)
    return send_from_directory(current_app.config["POD_DIR"], name)
