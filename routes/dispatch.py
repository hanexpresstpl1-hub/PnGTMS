"""배차: 납품일별 운송 목록, 차량·기사 배정(차량번호·기사명·전화 직접 입력 → 자동 등록), 기사 링크 발급·복사·재발급, 거점 출고 완료."""
import base64
import datetime as dt
import io

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from core import service
from core.auth import role_required
from core.autodispatch import plan as auto_plan, vehicle_loads
from core.db import q
from core.links import active_link, get_or_create_driver, get_or_create_vehicle, issue_link, link_url, revoke_if_unused

bp = Blueprint("dispatch", __name__, url_prefix="/dispatch")


def _links_for(date):
    """(driver_id, vehicle_id) → 유효 링크 행 (해당 납품일)."""
    out = {}
    for l in q("SELECT * FROM driver_links WHERE date=? AND revoked=0 AND expires_at>=datetime('now','localtime') ORDER BY id", (date,)):
        out[(l["driver_id"], l["vehicle_id"])] = l
    return out


@bp.route("/")
@role_required("admin", "center", "carrier")
def board():
    date = request.args.get("date") or dt.date.today().strftime("%Y-%m-%d")
    dtype = request.args.get("dtype", "")
    view = request.args.get("view", "all")
    where, args = ["s.planned_date=?", "s.status!='cancelled'", "o.status NOT IN ('cancelled','error')"], [date]
    if dtype:
        where.append("o.delivery_type=?"); args.append(dtype)
    if view == "unassigned":
        where.append("s.status='planned'")
    elif view == "assigned":
        where.append("s.status!='planned'")
    if g.user["role"] == "center" and g.user["center_code"]:
        where.append("o.center_code=?"); args.append(g.user["center_code"])
    rows = q("SELECT s.*, o.order_no, o.store_name, o.address, o.delivery_type, o.qty, o.box, o.plt, o.status order_status, o.gaon_status, "
             "v.plate, v.ton, d.name driver_name, d.phone driver_phone FROM shipments s JOIN orders o ON o.id=s.order_id LEFT JOIN vehicles v ON v.id=s.vehicle_id "
             "LEFT JOIN drivers d ON d.id=s.driver_id WHERE %s ORDER BY s.status, s.to_name, s.id" % " AND ".join(where), args)
    vehicles = q("SELECT v.*, d.name driver_name, d.phone driver_phone FROM vehicles v LEFT JOIN drivers d ON d.id=v.driver_id WHERE v.active=1 ORDER BY v.plate")
    drivers = q("SELECT * FROM drivers WHERE active=1 ORDER BY name")
    links = _links_for(date)
    loads = {}
    for r in rows:
        if r["vehicle_id"]:
            l = loads.setdefault(r["vehicle_id"], {"cnt": 0, "plt": 0.0, "box": 0.0})
            l["cnt"] += 1; l["plt"] += r["plt"] or 0; l["box"] += r["box"] or 0
    summary = {"total": len(rows), "unassigned": sum(1 for r in rows if r["status"] == "planned"),
               "plt": round(sum(r["plt"] or 0 for r in rows), 1), "box": round(sum(r["box"] or 0 for r in rows), 1)}
    centers = q("SELECT code, name FROM centers WHERE active=1 ORDER BY code")
    return render_template("dispatch/board.html", rows=rows, date=date, dtype=dtype, view=view, vehicles=vehicles, drivers=drivers,
                           loads=loads, summary=summary, links=links, link_url=link_url, vfr=vehicle_loads(date), centers=centers)


@bp.route("/assign", methods=["POST"])
@role_required("admin", "center", "carrier")
def assign():
    ids = [int(x) for x in request.form.getlist("sid") if x.isdigit()]
    back = request.referrer or url_for("dispatch.board")
    if not ids:
        flash("운송 건을 선택하세요.", "error")
        return redirect(back)
    action = request.form.get("action")
    if action == "unassign":
        pairs = {(s["driver_id"], s["vehicle_id"], s["planned_date"]) for s in q("SELECT driver_id, vehicle_id, planned_date FROM shipments WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
        done, skipped = service.unassign(ids)
        for did, vid, date in pairs:
            if did and vid:
                revoke_if_unused(did, vid, date)
        flash("%d건 배차를 해제했습니다.%s 남은 배차가 없는 기사 링크는 무효화됐습니다." % (done, (" 이미 진행 중인 %d건은 해제할 수 없어 건너뜀." % skipped) if skipped else ""), "ok" if done else "warn")
        return redirect(back)
    if action == "ship_hub":
        done, skipped = service.ship_hub(ids)
        flash("거점 출고 완료 %d건 처리.%s" % (done, (" 거점경유가 아니거나 이미 완료된 %d건은 건너뜀." % skipped) if skipped else ""), "ok" if done else "error")
        return redirect(back)
    # 배정: 차량번호·기사명·전화 직접 입력(없는 차량·기사는 자동 등록)
    plate = request.form.get("plate", "").strip()
    dname = request.form.get("driver_name", "").strip()
    dphone = request.form.get("driver_phone", "").strip()
    vid = get_or_create_vehicle(plate) if plate else None
    did = get_or_create_driver(dname, dphone) if (dname or dphone) else None
    if vid and not did:  # 차량의 기본 기사
        v = q("SELECT driver_id FROM vehicles WHERE id=?", (vid,), one=True)
        did = v["driver_id"] if v else None
    if not vid and not did:
        flash("차량번호 또는 기사(이름·전화)를 입력하세요.", "error")
        return redirect(back)
    if vid and did:
        from core.db import execute
        execute("UPDATE vehicles SET driver_id=? WHERE id=?", (did, vid))
    service.assign(ids, vid, did, request.form.get("note") or None)
    # 직송 건이 있으면 기사 링크 발급
    issued = []
    if vid and did:
        dates = {s["planned_date"] for s in q("SELECT DISTINCT s.planned_date FROM shipments s JOIN orders o ON o.id=s.order_id WHERE s.id IN (%s) AND o.delivery_type='direct'" % ",".join("?" * len(ids)), ids)}
        for date in dates:
            issued.append(link_url(issue_link(did, vid, date)["token"]))
    msg = "%d건을 배차했습니다." % len(ids)
    if issued:
        msg += " 기사 링크: " + " ".join(issued) + " (복사해서 카톡·문자로 보내세요)"
    elif vid and not did:
        msg += " 기사 정보가 없어 링크는 발급되지 않았습니다."
    flash(msg, "ok")
    return redirect(back)


@bp.route("/auto", methods=["POST"])
@role_required("admin", "center", "carrier")
def auto():
    """자동배차: 미배차 직송 건을 PLT·CBM 적재 기준으로 차량에 채워 배정하고 기사 링크를 발급한다."""
    date = request.form.get("date") or dt.date.today().strftime("%Y-%m-%d")
    center = request.form.get("center") or None
    include_hub = request.form.get("include_hub") == "1"
    res = auto_plan(date, center=center, include_hub=include_hub)
    if not res["assign"]:
        flash(res["msg"], "warn")
        return redirect(url_for("dispatch.board", date=date))
    by_vehicle = {}
    for sid, vid in res["assign"]:
        by_vehicle.setdefault(vid, []).append(sid)
    links = []
    for vid, sids in by_vehicle.items():
        v = q("SELECT driver_id FROM vehicles WHERE id=?", (vid,), one=True)
        did = v["driver_id"] if v else None
        service.assign(sids, vid, did, "자동배차")
        if did and any(q("SELECT 1 FROM shipments s JOIN orders o ON o.id=s.order_id WHERE s.id=? AND o.delivery_type='direct'", (sid,), one=True) for sid in sids):
            links.append(link_url(issue_link(did, vid, date)["token"]))
    parts = ["%s %d건 %.1fPLT(%d%%)" % (s["vehicle"]["plate"], s["cnt"], s["plt"], round(s["vfr"] * 100)) for s in res["summary"]]
    msg = "자동배차: %s — %s" % (res["msg"], ", ".join(parts))
    if res["left"]:
        msg += " · 미배정: " + ", ".join(x["to_name"] for x in res["left"][:6]) + (" …" if len(res["left"]) > 6 else "")
    if links:
        msg += " · 기사 링크 %d건 발급(배차표에서 복사)" % len(links)
    flash(msg, "ok")
    return redirect(url_for("dispatch.board", date=date))


@bp.route("/link/renew", methods=["POST"])
@role_required("admin", "center", "carrier")
def link_renew():
    did, vid, date = request.form.get("driver_id", type=int), request.form.get("vehicle_id", type=int), request.form.get("date")
    if not (did and vid and date):
        flash("재발급 대상이 없습니다.", "error")
    else:
        l = issue_link(did, vid, date, renew=True)
        flash("새 링크를 발급했습니다(이전 링크·기기 묶음 해제): %s" % link_url(l["token"]), "ok")
    return redirect(request.referrer or url_for("dispatch.board", date=date))


def _qr_data_uri(text):
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=4, border=1)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@bp.route("/vehicle-day")
@role_required("admin", "center", "carrier")
def vehicle_day():
    """차량별 당일 배정표(인쇄). 직송 차량은 기사 링크 QR 포함."""
    date = request.args.get("date") or dt.date.today().strftime("%Y-%m-%d")
    rows = q("SELECT s.*, o.order_no, o.store_name, o.address, o.delivery_type, o.qty, o.box, o.plt, v.plate, v.ton, d.name driver_name, d.phone "
             "FROM shipments s JOIN orders o ON o.id=s.order_id LEFT JOIN vehicles v ON v.id=s.vehicle_id LEFT JOIN drivers d ON d.id=s.driver_id "
             "WHERE s.planned_date=? AND s.status NOT IN ('planned','cancelled') ORDER BY v.plate, s.seq, s.id", (date,))
    groups, qrs = {}, {}
    for r in rows:
        key = (r["plate"] or "차량미정", r["driver_name"] or "", r["phone"] or "")
        groups.setdefault(key, []).append(r)
        if key not in qrs and r["driver_id"] and r["vehicle_id"] and r["delivery_type"] == "direct":
            l = active_link(r["driver_id"], r["vehicle_id"], date)
            if l:
                u = link_url(l["token"])
                qrs[key] = (u, _qr_data_uri(u))
    return render_template("dispatch/vehicle_day.html", groups=groups, date=date, qrs=qrs)
