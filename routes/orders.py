"""오더: 엑셀 업로드(미리보기→확정), 목록, 상세, 가온 등록 현황."""
import json
import os
import uuid

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from core import service
from core.auth import role_required
from core.db import ORDER_STATUS, execute, q, setting
from core.excel import FIELD_LABEL, parse_order_excel
from core.gaon import GaonError, NotImplementedYet, gaon

bp = Blueprint("orders", __name__, url_prefix="/orders")

PREVIEW_DIR = "preview"


def _preview_path(token):
    d = os.path.join(current_app.config["UPLOAD_DIR"], PREVIEW_DIR)
    os.makedirs(d, exist_ok=True)
    if not token or not all(c in "0123456789abcdef" for c in token):
        abort(400)
    return os.path.join(d, token + ".json")


def _filters():
    f = {k: request.args.get(k, "").strip() for k in ("date_from", "date_to", "status", "dtype", "keyword", "gaon", "batch")}
    where, args = ["1=1"], []
    if f["date_from"]:
        where.append("o.delivery_date>=?"); args.append(f["date_from"])
    if f["date_to"]:
        where.append("o.delivery_date<=?"); args.append(f["date_to"])
    if f["status"]:
        where.append("o.status=?"); args.append(f["status"])
    if f["dtype"]:
        where.append("o.delivery_type=?"); args.append(f["dtype"])
    if f["gaon"]:
        where.append("o.gaon_status=?"); args.append(f["gaon"])
    if f["batch"]:
        where.append("o.batch_id=?"); args.append(f["batch"])
    if f["keyword"]:
        where.append("(o.order_no LIKE ? OR o.store_name LIKE ? OR o.store_pg_code LIKE ? OR o.gaon_slip_no LIKE ?)")
        args += ["%" + f["keyword"] + "%"] * 4
    if g.user["role"] == "center" and g.user["center_code"]:
        where.append("o.center_code=?"); args.append(g.user["center_code"])
    return f, " AND ".join(where), args


@bp.route("/")
@role_required("admin", "center")
def list_orders():
    f, where, args = _filters()
    rows = q("SELECT o.*, h.name hub_name FROM orders o LEFT JOIN hubs h ON h.id=o.hub_id WHERE %s ORDER BY o.delivery_date DESC, o.id DESC LIMIT 500" % where, args)
    return render_template("orders/list.html", rows=rows, f=f, statuses=ORDER_STATUS)


@bp.route("/<int:oid>")
@role_required("admin", "center", "shipper")
def detail(oid):
    o = q("SELECT o.*, h.name hub_name, b.filename FROM orders o LEFT JOIN hubs h ON h.id=o.hub_id LEFT JOIN upload_batches b ON b.id=o.batch_id WHERE o.id=?", (oid,), one=True)
    if not o:
        abort(404)
    items = q("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,))
    legs = q("SELECT s.*, v.plate, v.ton, d.name driver_name, d.phone driver_phone FROM shipments s LEFT JOIN vehicles v ON v.id=s.vehicle_id "
             "LEFT JOIN drivers d ON d.id=s.driver_id WHERE s.order_id=? ORDER BY s.leg", (oid,))
    events = q("SELECT e.*, u.name user_name FROM order_events e LEFT JOIN users u ON u.id=e.user_id WHERE e.order_id=? ORDER BY e.id", (oid,))
    jobs = q("SELECT * FROM gaon_jobs WHERE order_id=? ORDER BY id DESC", (oid,))
    pods = q("SELECT p.*, s.leg FROM pods p JOIN shipments s ON s.id=p.shipment_id WHERE s.order_id=? ORDER BY p.id", (oid,))
    back = url_for("portal.orders") if g.user["role"] == "shipper" else url_for("orders.list_orders")
    return render_template("orders/detail.html", o=o, items=items, legs=legs, events=events, jobs=jobs, pods=pods, back=back)


@bp.route("/<int:oid>/status", methods=["POST"])
@role_required("admin")
def change_status(oid):
    status = request.form.get("status")
    note = request.form.get("note", "")
    if status not in dict(ORDER_STATUS):
        abort(400)
    service.set_status(oid, status, note or "수동 변경")
    if status == "cancelled":
        execute("UPDATE shipments SET status='cancelled' WHERE order_id=? AND status IN ('planned','assigned')", (oid,))
    flash("상태를 변경했습니다.", "ok")
    return redirect(url_for("orders.detail", oid=oid))


@bp.route("/<int:oid>/memo", methods=["POST"])
@role_required("admin", "center")
def memo(oid):
    execute("UPDATE orders SET memo=? WHERE id=?", (request.form.get("memo", "").strip(), oid))
    return redirect(url_for("orders.detail", oid=oid))


# ---- 업로드 ----
@bp.route("/upload", methods=["GET", "POST"])
@role_required("admin", "center", "shipper")
def upload():
    if g.user["role"] == "shipper" and setting("shipper_can_upload") != "1":
        abort(403)
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("xlsx 파일을 올려 주세요.", "error")
            return redirect(url_for("orders.upload"))
        data = f.read()
        parsed = parse_order_excel(data, setting("excel_map"))
        rows, summary = service.validate_rows(parsed["rows"])
        token = uuid.uuid4().hex
        with open(_preview_path(token), "w", encoding="utf-8") as fp:
            json.dump({"filename": f.filename, "rows": [{k: v for k, v in r.items() if k not in ("store", "item")} for r in rows]}, fp, ensure_ascii=False)
        return render_template("orders/preview.html", parsed=parsed, rows=rows, summary=summary, token=token, filename=f.filename, labels=FIELD_LABEL)
    batches = q("SELECT b.*, u.name user_name FROM upload_batches b LEFT JOIN users u ON u.id=b.uploaded_by ORDER BY b.id DESC LIMIT 20")
    return render_template("orders/upload.html", batches=batches, labels=FIELD_LABEL, excel_map=json.loads(setting("excel_map") or "{}"))


@bp.route("/upload/confirm", methods=["POST"])
@role_required("admin", "center", "shipper")
def upload_confirm():
    token = request.form.get("token", "")
    path = _preview_path(token)
    if not os.path.exists(path):
        flash("미리보기가 만료되었습니다. 다시 업로드해 주세요.", "error")
        return redirect(url_for("orders.upload"))
    with open(path, encoding="utf-8") as fp:
        saved = json.load(fp)
    thr_raw = request.form.get("thr", "").strip()
    if thr_raw:
        try:
            thr = float(thr_raw)
            for d in sorted({r["delivery_date"] for r in saved["rows"] if r.get("delivery_date")}):
                service.set_day_threshold(d, thr, "업로드 %s" % saved["filename"])
        except ValueError:
            flash("기준 PLT는 숫자여야 합니다. 기본값으로 판정했습니다.", "warn")
    rows, _ = service.validate_rows(saved["rows"])  # 마스터가 그 사이 바뀌었을 수 있어 다시 검증
    skip = request.form.get("mode", "skip") == "skip"
    batch_id, created, errored = service.import_rows(rows, saved["filename"], skip_errors=skip)
    os.remove(path)
    flash("오더 %d건을 등록했습니다.%s" % (created, (" 오류 오더 %d건은 '오류' 상태로 저장했습니다." % errored) if errored else ""), "ok")
    if g.user["role"] == "shipper":
        return redirect(url_for("portal.orders"))
    return redirect(url_for("orders.list_orders", batch=batch_id))


# ---- 가온 등록 ----
@bp.route("/gaon")
@role_required("admin")
def gaon_status():
    pending = q("SELECT o.* FROM orders o WHERE o.status='received' AND o.gaon_status IN ('pending','failed') ORDER BY o.delivery_date, o.id LIMIT 300")
    jobs = q("SELECT j.*, o.order_no, o.store_name, u.name user_name FROM gaon_jobs j LEFT JOIN orders o ON o.id=j.order_id LEFT JOIN users u ON u.id=j.user_id ORDER BY j.id DESC LIMIT 50")
    return render_template("orders/gaon.html", pending=pending, jobs=jobs, gaon=gaon.info(), market=setting("gaon_market_code"))


@bp.route("/gaon/connect", methods=["POST"])
@role_required("admin")
def gaon_connect():
    if request.form.get("action") == "disconnect":
        gaon.disconnect()
        flash("가온 연결을 끊었습니다.", "ok")
        return redirect(request.referrer or url_for("orders.gaon_status"))
    uid = request.form.get("gaon_id", "").strip() or setting("gaon_user_id", "")
    try:
        c = gaon.connect(uid, request.form.get("gaon_pw", ""))
        flash("가온 연결됨: %s" % (c.user_nm or uid), "ok")
    except GaonError as e:
        flash(str(e), "error")
    return redirect(request.referrer or url_for("orders.gaon_status"))


@bp.route("/gaon/register", methods=["POST"])
@role_required("admin")
def gaon_register():
    ids = [int(x) for x in request.form.getlist("oid") if x.isdigit()]
    if not ids:
        flash("등록할 오더를 선택하세요.", "error")
        return redirect(url_for("orders.gaon_status"))
    if not gaon.connected:
        flash("먼저 가온에 연결하세요.", "error")
        return redirect(url_for("orders.gaon_status"))
    market = setting("gaon_market_code", "")
    payload = []
    for oid in ids:
        o = q("SELECT o.*, s.gaon_code store_gaon FROM orders o LEFT JOIN stores s ON s.id=o.store_id WHERE o.id=?", (oid,), one=True)
        items = q("SELECT oi.qty, i.gaon_code FROM order_items oi LEFT JOIN items i ON i.id=oi.item_id WHERE oi.order_id=?", (oid,))
        payload.append({"order_id": oid, "order_no": o["order_no"], "store_gaon_code": o["store_gaon"], "delivery_date": o["delivery_date"],
                        "items": [{"gaon_code": i["gaon_code"], "qty": i["qty"]} for i in items]})
    try:
        results = gaon.client.register_orders(market, payload)
        for r in results:
            service.mark_gaon(r["order_id"], r["ok"], r.get("slip_no"), r.get("msg"))
        flash("가온 등록 %d건 처리." % len(results), "ok")
    except NotImplementedYet as e:
        for p in payload:
            service.mark_gaon(p["order_id"], False, None, str(e), p)
        flash(str(e), "error")
    except GaonError as e:
        flash("가온 오류: %s" % e, "error")
    return redirect(url_for("orders.gaon_status"))
