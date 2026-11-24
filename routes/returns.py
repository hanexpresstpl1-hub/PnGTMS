"""반품 관리 (TF 6-1~6-4).

  6-1 P&G 담당자가 포털에 반품 오더 등록 (관리자도 대신 등록 가능)
  6-2 HanEx가 거점(RDC)별로 반품 안내 메일 발송 (SMTP 미설정이면 본문 생성 → 복사)
  6-3 SAMMI(운송사)가 반품 오더에 차량·기사 배차 → 회수 완료 → 센터 입고 확인
  6-4 월별 반품 BOX 대시보드 · 정산 마감
"""
import datetime as dt
import io

import openpyxl
from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for

from core.auth import role_required
from core.db import RETURN_STATUS, RETURN_STATUS_MAP, execute, now, q, setting
from core.links import get_or_create_driver, get_or_create_vehicle
from core.mail import configured as mail_configured, send_mail
from core.service import resolve_hub

bp = Blueprint("returns", __name__, url_prefix="/returns")
ALL = ("admin", "center", "carrier", "shipper")


def _event(rid, status, note=""):
    execute("INSERT INTO return_events(return_id,status,note,user_id,at) VALUES(?,?,?,?,?)", (rid, status, note, g.user["id"] if g.get("user") else None, now()))


def _set(rid, status, note="", **cols):
    sets = ", ".join("%s=?" % k for k in cols)
    execute("UPDATE returns SET status=?, updated_at=?%s WHERE id=?" % ((", " + sets) if sets else ""), [status, now()] + list(cols.values()) + [rid])
    _event(rid, status, note)


def _next_no():
    today = dt.date.today().strftime("%y%m%d")
    n = q("SELECT COUNT(*) c FROM returns WHERE return_no LIKE ?", ("R%s%%" % today,), one=True)["c"] + 1
    return "R%s-%03d" % (today, n)


@bp.route("/")
@role_required(*ALL)
def index():
    f = {k: request.args.get(k, "").strip() for k in ("month", "status", "hub", "keyword")}
    where, args = ["1=1"], []
    if f["month"]:
        where.append("substr(r.request_date,1,7)=?"); args.append(f["month"])
    if f["status"]:
        where.append("r.status=?"); args.append(f["status"])
    if f["hub"]:
        where.append("r.hub_id=?"); args.append(f["hub"])
    if f["keyword"]:
        where.append("(r.return_no LIKE ? OR r.store_name LIKE ? OR r.store_pg_code LIKE ?)"); args += ["%" + f["keyword"] + "%"] * 3
    if g.user["role"] == "shipper":
        pass  # P&G는 전체 반품 조회 가능(자기 화주 전용 포털)
    rows = q("SELECT r.*, h.name hub_name, v.plate, d.name driver_name FROM returns r LEFT JOIN hubs h ON h.id=r.hub_id LEFT JOIN vehicles v ON v.id=r.vehicle_id "
             "LEFT JOIN drivers d ON d.id=r.driver_id WHERE %s ORDER BY r.id DESC LIMIT 500" % " AND ".join(where), args)
    hubs = q("SELECT * FROM hubs WHERE active=1 ORDER BY name")
    counts = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM returns GROUP BY status")}
    return render_template("returns/index.html", rows=rows, f=f, statuses=RETURN_STATUS, hubs=hubs, counts=counts,
                           vehicles=q("SELECT v.*, d.name driver_name, d.phone driver_phone FROM vehicles v LEFT JOIN drivers d ON d.id=v.driver_id WHERE v.active=1 ORDER BY v.plate"),
                           mail_ok=mail_configured())


@bp.route("/new", methods=["GET", "POST"])
@role_required("admin", "center", "shipper")
def new():
    stores = q("SELECT id, pg_code, name, address FROM stores WHERE active=1 ORDER BY name")
    items = q("SELECT id, pg_code, name, box_qty FROM items WHERE active=1 ORDER BY name")
    if request.method == "POST":
        code = request.form.get("store_code", "").strip()
        s = q("SELECT * FROM stores WHERE pg_code=? OR name=?", (code, code), one=True)
        if not s:
            flash("납품처를 찾지 못했습니다: %s" % code, "error")
            return render_template("returns/form.html", stores=stores, items=items, form=request.form)
        pickup = request.form.get("pickup_date") or None
        lines, box_sum, qty_sum = [], 0.0, 0.0
        for i in range(1, 11):
            ic, iq = request.form.get("item_code_%d" % i, "").strip(), request.form.get("item_qty_%d" % i, "").strip()
            if not ic or not iq:
                continue
            it = q("SELECT * FROM items WHERE pg_code=? OR name=?", (ic, ic), one=True)
            try:
                qn = float(iq)
            except ValueError:
                continue
            unit = (request.form.get("item_unit_%d" % i) or "BOX").upper()
            box = qn if unit == "BOX" else (qn / it["box_qty"] if it and it["box_qty"] else 0)
            qty = qn * it["box_qty"] if (unit == "BOX" and it and it["box_qty"]) else (qn if unit != "BOX" else 0)
            lines.append((it["id"] if it else None, ic, it["name"] if it else ic, qty, box, request.form.get("item_reason_%d" % i, "").strip()))
            box_sum += box; qty_sum += qty
        try:
            box_total = float(request.form.get("box") or box_sum or 0)
        except ValueError:
            box_total = box_sum
        rid = execute("INSERT INTO returns(return_no,store_id,store_pg_code,store_name,address,hub_id,center_code,request_date,pickup_date,box,qty,reason,memo,status,created_by,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (_next_no(), s["id"], s["pg_code"], s["name"], s["address"], resolve_hub(s), s["center_code"] or setting("default_center_code", ""),
                       dt.date.today().isoformat(), pickup, box_total, qty_sum, request.form.get("reason", "").strip(), request.form.get("memo", "").strip(),
                       "registered", g.user["id"], now(), now()))
        for l in lines:
            execute("INSERT INTO return_items(return_id,item_id,item_pg_code,item_name,qty,box,reason) VALUES(?,?,?,?,?,?,?)", (rid,) + l)
        _event(rid, "registered", "반품 등록 (%s)" % g.user["name"])
        flash("반품 오더를 등록했습니다.", "ok")
        return redirect(url_for("returns.index"))
    return render_template("returns/form.html", stores=stores, items=items, form={})


@bp.route("/<int:rid>")
@role_required(*ALL)
def detail(rid):
    r = q("SELECT r.*, h.name hub_name, h.email hub_email, v.plate, d.name driver_name, d.phone driver_phone FROM returns r LEFT JOIN hubs h ON h.id=r.hub_id "
          "LEFT JOIN vehicles v ON v.id=r.vehicle_id LEFT JOIN drivers d ON d.id=r.driver_id WHERE r.id=?", (rid,), one=True)
    if not r:
        abort(404)
    items = q("SELECT * FROM return_items WHERE return_id=? ORDER BY id", (rid,))
    events = q("SELECT e.*, u.name user_name FROM return_events e LEFT JOIN users u ON u.id=e.user_id WHERE e.return_id=? ORDER BY e.id", (rid,))
    mails = q("SELECT * FROM notifications WHERE ref_type='return' AND ref_id=? ORDER BY id DESC", (rid,))
    return render_template("returns/detail.html", r=r, items=items, events=events, mails=mails)


def _mail_body(hub, rows):
    lines = ["%s 담당자님," % hub["name"], "", "아래 반품 건의 회수를 안내드립니다. (한익스프레스 %s)" % (setting("team") or "TPL2팀"), ""]
    for r in rows:
        lines.append("- %s | %s(%s) | 희망 회수일 %s | %.1f BOX | %s" % (r["return_no"], r["store_name"], r["store_pg_code"], r["pickup_date"] or "미정", r["box"] or 0, r["reason"] or ""))
    lines += ["", "회수 일정과 차량은 운송사(SAMMI) 배차 후 다시 안내드립니다.", "", "감사합니다."]
    return "\n".join(lines)


@bp.route("/mail", methods=["POST"])
@role_required("admin", "center")
def mail():
    """선택한 반품 건을 거점별로 묶어 안내 메일. ids 없으면 등록 상태 전체."""
    ids = [int(x) for x in request.form.getlist("rid") if x.isdigit()]
    rows = q("SELECT r.*, h.name hub_name, h.email hub_email FROM returns r LEFT JOIN hubs h ON h.id=r.hub_id WHERE %s ORDER BY r.hub_id, r.id"
             % ("r.id IN (%s)" % ",".join("?" * len(ids)) if ids else "r.status='registered'"), ids)
    if not rows:
        flash("안내할 반품 건이 없습니다.", "error")
        return redirect(url_for("returns.index"))
    by_hub = {}
    for r in rows:
        by_hub.setdefault(r["hub_id"], []).append(r)
    msgs = []
    for hid, rs in by_hub.items():
        hub = q("SELECT * FROM hubs WHERE id=?", (hid,), one=True) if hid else None
        if not hub:
            msgs.append("거점 미지정 %d건은 보내지 않음" % len(rs))
            continue
        body = _mail_body(hub, rs)
        sent, m = send_mail(hub["email"], "[한익스프레스] %s 반품 회수 안내 (%d건)" % (hub["name"], len(rs)), body, "return", rs[0]["id"], "return_mail")
        for r in rs:
            _set(r["id"], "mailed", "거점 안내 메일 %s → %s" % ("발송" if sent else "생성", hub["email"] or "(주소 없음)"), mailed_at=now(), mail_to=hub["email"] or "")
        msgs.append("%s: %s" % (hub["name"], m))
    flash(" / ".join(msgs), "ok")
    return redirect(url_for("returns.index"))


@bp.route("/<int:rid>/assign", methods=["POST"])
@role_required("admin", "center", "carrier")
def assign(rid):
    plate, dname, dphone = request.form.get("plate", "").strip(), request.form.get("driver_name", "").strip(), request.form.get("driver_phone", "").strip()
    vid = get_or_create_vehicle(plate) if plate else None
    did = get_or_create_driver(dname, dphone) if (dname or dphone) else None
    if vid and not did:
        v = q("SELECT driver_id FROM vehicles WHERE id=?", (vid,), one=True)
        did = v["driver_id"] if v else None
    if not vid:
        flash("차량번호를 입력하세요.", "error")
        return redirect(request.referrer or url_for("returns.index"))
    carrier = request.form.get("carrier", "").strip() or (q("SELECT carrier FROM vehicles WHERE id=?", (vid,), one=True) or {"carrier": ""})["carrier"] or ""
    _set(rid, "assigned", "반품 배차 %s %s %s" % (plate, dname, carrier), vehicle_id=vid, driver_id=did, carrier=carrier, assigned_at=now(),
         pickup_date=request.form.get("pickup_date") or q("SELECT pickup_date FROM returns WHERE id=?", (rid,), one=True)["pickup_date"])
    flash("반품 배차를 저장했습니다.", "ok")
    return redirect(request.referrer or url_for("returns.detail", rid=rid))


@bp.route("/<int:rid>/status", methods=["POST"])
@role_required("admin", "center", "carrier")
def status(rid):
    st = request.form.get("status")
    note = request.form.get("note", "").strip()
    if st not in RETURN_STATUS_MAP:
        abort(400)
    extra = {}
    if st == "collected":
        extra["collected_at"] = now()
        try:
            if request.form.get("box"):
                extra["box"] = float(request.form.get("box"))
        except ValueError:
            pass
    if st == "received":
        extra["received_at"] = now()
    _set(rid, st, note or RETURN_STATUS_MAP[st], **extra)
    flash("%s 처리했습니다." % RETURN_STATUS_MAP[st], "ok")
    return redirect(request.referrer or url_for("returns.detail", rid=rid))


@bp.route("/dashboard")
@role_required(*ALL)
def dashboard():
    month = request.args.get("month") or dt.date.today().strftime("%Y-%m")
    by_hub = q("SELECT COALESCE(h.name,'거점 미지정') hub, COUNT(*) cnt, SUM(r.box) box, SUM(CASE WHEN r.status IN ('received','settled') THEN r.box ELSE 0 END) box_done, "
               "SUM(CASE WHEN r.status='settled' THEN 1 ELSE 0 END) settled FROM returns r LEFT JOIN hubs h ON h.id=r.hub_id "
               "WHERE substr(r.request_date,1,7)=? AND r.status!='cancelled' GROUP BY h.name ORDER BY box DESC", (month,))
    by_store = q("SELECT r.store_name, r.store_pg_code, COUNT(*) cnt, SUM(r.box) box FROM returns r WHERE substr(r.request_date,1,7)=? AND r.status!='cancelled' "
                 "GROUP BY r.store_pg_code ORDER BY box DESC LIMIT 30", (month,))
    by_status = {r["status"]: (r["c"], r["b"] or 0) for r in q("SELECT status, COUNT(*) c, SUM(box) b FROM returns WHERE substr(request_date,1,7)=? GROUP BY status", (month,))}
    by_reason = q("SELECT COALESCE(NULLIF(reason,''),'(사유 없음)') reason, COUNT(*) cnt, SUM(box) box FROM returns WHERE substr(request_date,1,7)=? AND status!='cancelled' GROUP BY reason ORDER BY box DESC", (month,))
    trend = q("SELECT substr(request_date,1,7) m, COUNT(*) cnt, SUM(box) box FROM returns WHERE status!='cancelled' GROUP BY m ORDER BY m DESC LIMIT 12")
    tot = {"cnt": sum(r["cnt"] for r in by_hub), "box": sum(r["box"] or 0 for r in by_hub), "box_done": sum(r["box_done"] or 0 for r in by_hub)}
    return render_template("returns/dashboard.html", month=month, by_hub=by_hub, by_store=by_store, by_status=by_status, by_reason=by_reason, trend=trend, tot=tot,
                           statuses=RETURN_STATUS_MAP)


@bp.route("/settle", methods=["POST"])
@role_required("admin")
def settle():
    month = request.form.get("month")
    n = 0
    for r in q("SELECT id FROM returns WHERE substr(request_date,1,7)=? AND status='received'", (month,)):
        _set(r["id"], "settled", "%s 반품 정산 마감" % month, settled_month=month)
        n += 1
    flash("%s 입고확인 반품 %d건을 정산 마감했습니다." % (month, n), "ok")
    return redirect(url_for("returns.dashboard", month=month))


@bp.route("/export.xlsx")
@role_required(*ALL)
def export():
    month = request.args.get("month") or dt.date.today().strftime("%Y-%m")
    rows = q("SELECT r.*, h.name hub_name, v.plate, d.name driver_name FROM returns r LEFT JOIN hubs h ON h.id=r.hub_id LEFT JOIN vehicles v ON v.id=r.vehicle_id "
             "LEFT JOIN drivers d ON d.id=r.driver_id WHERE substr(r.request_date,1,7)=? ORDER BY r.id", (month,))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "반품 %s" % month
    ws.append(["반품번호", "등록일", "희망회수일", "거점", "납품처코드", "납품처", "BOX", "수량(EA)", "사유", "상태", "차량", "기사", "운송사", "회수일시", "입고확인", "정산월", "메모"])
    for r in rows:
        ws.append([r["return_no"], r["request_date"], r["pickup_date"], r["hub_name"], r["store_pg_code"], r["store_name"], r["box"], r["qty"], r["reason"],
                   RETURN_STATUS_MAP.get(r["status"], r["status"]), r["plate"], r["driver_name"], r["carrier"], r["collected_at"], r["received_at"], r["settled_month"], r["memo"]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="반품_%s.xlsx" % month, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
