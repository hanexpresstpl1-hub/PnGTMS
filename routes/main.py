import datetime as dt

from flask import Blueprint, render_template, request

from core.auth import role_required
from core.db import ORDER_STATUS, q
from core.gaon import gaon

bp = Blueprint("main", __name__)


@bp.route("/dashboard")
@role_required("admin", "center")
def dashboard():
    today = dt.date.today().strftime("%Y-%m-%d")
    counts = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM orders GROUP BY status")}
    status_cards = [(code, label, counts.get(code, 0)) for code, label in ORDER_STATUS]
    today_orders = q("SELECT delivery_type, COUNT(*) c, SUM(qty) qty, SUM(plt) plt FROM orders WHERE delivery_date=? AND status NOT IN ('cancelled') GROUP BY delivery_type", (today,))
    unassigned = q("SELECT s.planned_date, COUNT(*) c FROM shipments s WHERE s.status='planned' GROUP BY s.planned_date ORDER BY s.planned_date LIMIT 7")
    gaon_pending = q("SELECT COUNT(*) c FROM orders WHERE status='received' AND gaon_status!='done'", one=True)["c"]
    gaon_failed = q("SELECT COUNT(*) c FROM orders WHERE gaon_status='failed'", one=True)["c"]
    recent = q("SELECT e.*, o.order_no, o.store_name, u.name user_name FROM order_events e JOIN orders o ON o.id=e.order_id "
               "LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 12")
    batches = q("SELECT b.*, u.name user_name FROM upload_batches b LEFT JOIN users u ON u.id=b.uploaded_by ORDER BY b.id DESC LIMIT 5")
    date = request.args.get("date") or today
    progress = q("SELECT s.*, o.order_no, o.store_name, v.plate, d.name driver_name, d.phone, "
                 "(SELECT first_open_at FROM driver_links l WHERE l.driver_id=s.driver_id AND l.vehicle_id=s.vehicle_id AND l.date=s.planned_date AND l.revoked=0 ORDER BY l.id DESC LIMIT 1) link_open "
                 "FROM shipments s JOIN orders o ON o.id=s.order_id "
                 "LEFT JOIN vehicles v ON v.id=s.vehicle_id LEFT JOIN drivers d ON d.id=s.driver_id "
                 "WHERE s.planned_date=? AND o.delivery_type='direct' AND s.status NOT IN ('planned','cancelled') AND o.status!='cancelled' "
                 "ORDER BY v.plate, s.id", (date,))
    from core.autodispatch import vehicle_loads
    returns_open = q("SELECT COUNT(*) c FROM returns WHERE status IN ('registered','mailed','assigned')", one=True)["c"]
    return render_template("dashboard.html", today=today, date=date, status_cards=status_cards, today_orders=today_orders, unassigned=unassigned,
                           gaon_pending=gaon_pending, gaon_failed=gaon_failed, recent=recent, batches=batches, gaon=gaon.info(), progress=progress,
                           vfr=vehicle_loads(date), returns_open=returns_open)
