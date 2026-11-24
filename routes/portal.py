"""P&G 화주 포털(/portal): 자기 오더 조회·추적, 월 실적 요약. 상세는 orders.detail(shipper 허용)을 같이 쓴다."""
import datetime as dt

from flask import Blueprint, render_template, request

from core.auth import role_required
from core.db import ORDER_STATUS, q, setting

bp = Blueprint("portal", __name__, url_prefix="/portal")


@bp.route("/")
@role_required("shipper", "admin")
def orders():
    today = dt.date.today()
    f = {"date_from": request.args.get("date_from") or (today - dt.timedelta(days=7)).strftime("%Y-%m-%d"),
         "date_to": request.args.get("date_to") or (today + dt.timedelta(days=7)).strftime("%Y-%m-%d"),
         "status": request.args.get("status", ""), "keyword": request.args.get("keyword", "").strip()}
    where, args = ["o.delivery_date BETWEEN ? AND ?"], [f["date_from"], f["date_to"]]
    if f["status"]:
        where.append("o.status=?"); args.append(f["status"])
    if f["keyword"]:
        where.append("(o.order_no LIKE ? OR o.store_name LIKE ? OR o.store_pg_code LIKE ?)"); args += ["%" + f["keyword"] + "%"] * 3
    rows = q("SELECT o.*, h.name hub_name, (SELECT MAX(delivered_at) FROM shipments s WHERE s.order_id=o.id) delivered_at, "
             "(SELECT eta_at FROM shipments s WHERE s.order_id=o.id ORDER BY s.id LIMIT 1) eta_at "
             "FROM orders o LEFT JOIN hubs h ON h.id=o.hub_id WHERE %s ORDER BY o.delivery_date DESC, o.id DESC LIMIT 500" % " AND ".join(where), args)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return render_template("portal/orders.html", rows=rows, f=f, statuses=ORDER_STATUS, counts=counts,
                           can_upload=setting("shipper_can_upload") == "1")


@bp.route("/summary")
@role_required("shipper", "admin")
def summary():
    month = request.args.get("month") or dt.date.today().strftime("%Y-%m")
    rows = q("SELECT delivery_date, delivery_type, COUNT(*) cnt, SUM(qty) qty, SUM(box) box, SUM(plt) plt, "
             "SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) delivered FROM orders WHERE substr(delivery_date,1,7)=? AND status!='cancelled' "
             "GROUP BY delivery_date, delivery_type ORDER BY delivery_date", (month,))
    tot = {"cnt": sum(r["cnt"] for r in rows), "qty": sum(r["qty"] or 0 for r in rows), "box": sum(r["box"] or 0 for r in rows),
           "plt": sum(r["plt"] or 0 for r in rows), "delivered": sum(r["delivered"] for r in rows)}
    return render_template("portal/summary.html", rows=rows, month=month, tot=tot)
