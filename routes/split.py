"""오더 분리·양식 (TF 프로세스 2~5단계).

납품일 기준으로 오더를 출고센터(천안·안성) × 유형(직송·RDC) 4개로 나눠 보여주고,
  - GAON 입고예정·수주 업로드 양식(xlsx, 그룹별 / 전체) — 컬럼은 설정 gaon_upload_cols
  - 현장 작업용 피킹리스트(센터별 인쇄 화면 + xlsx)
  - 현대글로비스 작업지시(배차·착지, xlsx)
를 내보낸다. 실제 GAON·글로비스 양식이 확정되면 컬럼 정의만 바꾸면 된다.
"""
import datetime as dt
import io
import json

import openpyxl
from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from core import service
from core.auth import role_required
from core.db import DTYPE_MAP, execute, q, setting

bp = Blueprint("split", __name__, url_prefix="/split")


def _centers():
    rows = q("SELECT * FROM centers WHERE active=1 ORDER BY code")
    return rows if rows else [{"code": setting("default_center_code", "") or "", "name": setting("default_center_name", "한익 센터")}]


def _orders(date, center=None, dtype=None, include_error=False):
    where, args = ["o.delivery_date=?", "o.status!='cancelled'"], [date]
    if not include_error:
        where.append("o.status!='error'")
    if center is not None:
        where.append("COALESCE(o.center_code,'')=?"); args.append(center)
    if dtype:
        where.append("o.delivery_type=?"); args.append(dtype)
    return q("SELECT o.*, h.name hub_name, h.code hub_code, s.gaon_code store_gaon, s.area_code, s.region FROM orders o LEFT JOIN hubs h ON h.id=o.hub_id "
             "LEFT JOIN stores s ON s.id=o.store_id WHERE %s ORDER BY o.center_code, o.delivery_type, h.name, o.store_name, o.id" % " AND ".join(where), args)


def _lines(order_ids):
    if not order_ids:
        return []
    return q("SELECT oi.*, o.order_no, o.store_name, o.store_pg_code, o.delivery_date, o.delivery_type, o.center_code, o.hub_id, s.gaon_code store_gaon, "
             "i.gaon_code item_gaon, h.name hub_name FROM order_items oi JOIN orders o ON o.id=oi.order_id LEFT JOIN stores s ON s.id=o.store_id "
             "LEFT JOIN items i ON i.id=oi.item_id LEFT JOIN hubs h ON h.id=o.hub_id WHERE oi.order_id IN (%s) ORDER BY o.center_code, o.delivery_type, o.store_name, oi.id"
             % ",".join("?" * len(order_ids)), order_ids)


@bp.route("/")
@role_required("admin", "center")
def index():
    date = request.args.get("date") or dt.date.today().strftime("%Y-%m-%d")
    centers = _centers()
    orders = _orders(date)
    groups = []
    for c in centers:
        for dtype in ("direct", "hub"):
            os_ = [o for o in orders if (o["center_code"] or "") == c["code"] and o["delivery_type"] == dtype]
            groups.append({"center": c, "dtype": dtype, "orders": os_, "cnt": len(os_), "stores": len({o["store_id"] for o in os_}),
                           "qty": sum(o["qty"] or 0 for o in os_), "box": sum(o["box"] or 0 for o in os_), "plt": sum(o["plt"] or 0 for o in os_),
                           "cbm": sum(o["cbm"] or 0 for o in os_), "hubs": sorted({o["hub_name"] for o in os_ if o["hub_name"]})})
    known = {c["code"] for c in centers}
    orphan = [o for o in orders if (o["center_code"] or "") not in known]
    errors = q("SELECT COUNT(*) c FROM orders WHERE delivery_date=? AND status='error'", (date,), one=True)["c"]
    cols = json.loads(setting("gaon_upload_cols") or "[]")
    # 납품처별 판정 현황(담당자 조정용): 배차 전 오더만 전환 가능
    thr, thr_src = service.day_threshold(date)
    stores = {}
    for o in orders:
        g_ = stores.setdefault(o["store_id"], {"store_id": o["store_id"], "name": o["store_name"], "code": o["store_pg_code"], "center": o["center_code"], "hub": o["hub_name"],
                                               "area": o["area_code"], "plt": 0.0, "cnt": 0, "dtype": o["delivery_type"], "manual": o["route_manual"], "note": o["route_note"],
                                               "locked": False, "ids": []})
        g_["plt"] += o["plt"] or 0; g_["cnt"] += 1; g_["ids"].append(o["id"])
        if o["status"] not in ("received", "gaon"):
            g_["locked"] = True
    store_rows = sorted(stores.values(), key=lambda x: (-x["plt"], x["name"] or ""))
    return render_template("split/index.html", date=date, groups=groups, orphan=orphan, errors=errors, centers=centers, cols=cols, total=len(orders),
                           thr=thr, thr_src=thr_src, default_thr=float(setting("direct_plt_threshold") or 3), store_rows=store_rows)


@bp.route("/threshold", methods=["POST"])
@role_required("admin", "center")
def threshold():
    """그 납품일의 직송 기준 PLT를 저장하고 배차 전 오더를 다시 판정."""
    date = request.form.get("date")
    try:
        thr = float(request.form.get("thr", ""))
    except ValueError:
        flash("기준 PLT를 숫자로 입력하세요.", "error")
        return redirect(url_for("split.index", date=date))
    if request.form.get("action") == "clear":
        execute("DELETE FROM route_day WHERE date=?", (date,))
        msg = "%s 기준을 기본값으로 되돌렸습니다." % date
    else:
        service.set_day_threshold(date, thr, request.form.get("note", ""))
        msg = "%s 직송 기준을 %g PLT로 저장했습니다." % (date, thr)
    changed, groups = service.reroute_all(date, date)
    flash("%s 납품처 %d곳을 다시 판정해 %d건이 바뀌었습니다(배차 전 오더만)." % (msg, groups, changed), "ok")
    return redirect(url_for("split.index", date=date))


@bp.route("/manual", methods=["POST"])
@role_required("admin", "center")
def manual():
    """납품처(일자) 단위로 담당자가 직송/RDC를 직접 지정하거나(규칙 무시) 규칙으로 되돌린다."""
    date, store_id, dtype = request.form.get("date"), request.form.get("store_id", type=int), request.form.get("dtype", "")
    if dtype not in ("direct", "hub", ""):
        abort(400)
    execute("UPDATE orders SET route_manual=? WHERE store_id=? AND delivery_date=? AND status IN ('received','gaon','error')", (dtype or None, store_id, date))
    changed = service.apply_route({(store_id, date)})
    name = (q("SELECT name FROM stores WHERE id=?", (store_id,), one=True) or {"name": ""})["name"]
    flash("%s: %s → %d건 반영." % (name, {"direct": "직송 지정", "hub": "RDC 지정", "": "규칙으로 되돌림"}[dtype], changed), "ok")
    return redirect(url_for("split.index", date=date))


def _xlsx(headers, rows, sheet="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet[:31]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(10, min(40, max(len(str(c.value or "")) for c in col) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _dl(buf, name):
    return send_file(buf, as_attachment=True, download_name=name, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/gaon.xlsx")
@role_required("admin", "center")
def gaon_xlsx():
    """GAON 업로드 양식. kind=inbound(입고예정) | order(수주). center·dtype로 그룹 지정, 없으면 전체."""
    date = request.args.get("date") or dt.date.today().strftime("%Y-%m-%d")
    center, dtype, kind = request.args.get("center"), request.args.get("dtype"), request.args.get("kind", "order")
    orders = _orders(date, center if center is not None else None, dtype or None)
    lines = _lines([o["id"] for o in orders])
    cols = json.loads(setting("gaon_upload_cols") or "[]")
    cnames = {c["code"]: c["name"] for c in _centers()}
    def val(l, f):
        return {"delivery_date": l["delivery_date"], "store_gaon": l["store_gaon"] or l["store_pg_code"], "store_code": l["store_pg_code"], "store_name": l["store_name"],
                "item_gaon": l["item_gaon"] or l["item_pg_code"], "item_code": l["item_pg_code"], "item_name": l["item_name"], "qty": l["qty"], "box": l["box"], "plt": l["plt"],
                "unit": l["unit"] or "EA", "order_no": l["order_no"], "center": cnames.get(l["center_code"], l["center_code"]), "center_code": l["center_code"],
                "dtype": DTYPE_MAP.get(l["delivery_type"], ""), "hub": l["hub_name"] or "", "kind": "입고예정" if kind == "inbound" else "수주"}.get(f, "")
    rows = [[val(l, f) for _, f in cols] for l in lines]
    tag = "%s%s" % (cnames.get(center, center) if center else "전체", ("_" + DTYPE_MAP.get(dtype, dtype)) if dtype else "")
    return _dl(_xlsx([h for h, _ in cols], rows, "GAON"), "GAON_%s_%s_%s.xlsx" % ("입고예정" if kind == "inbound" else "수주", tag, date))


@bp.route("/picking")
@role_required("admin", "center")
def picking():
    """현장 피킹리스트: 센터별 · 유형별 · 납품처별 상품 수량. print=1 인쇄 화면, xlsx=1 파일."""
    date = request.args.get("date") or dt.date.today().strftime("%Y-%m-%d")
    center = request.args.get("center")
    if center is None:
        abort(400)
    cname = {c["code"]: c["name"] for c in _centers()}.get(center, center)
    orders = _orders(date, center)
    lines = _lines([o["id"] for o in orders])
    if request.args.get("xlsx"):
        rows = [[l["delivery_date"], cname, DTYPE_MAP.get(l["delivery_type"], ""), l["hub_name"] or "", l["store_pg_code"], l["store_name"], l["order_no"],
                 l["item_pg_code"], l["item_gaon"] or "", l["item_name"], l["qty"], l["box"], l["plt"], ""] for l in lines]
        return _dl(_xlsx(["납품일", "출고센터", "구분", "거점", "납품처코드", "납품처", "오더번호", "상품코드", "가온코드", "상품명", "수량(EA)", "BOX", "PLT", "피킹확인"], rows, "피킹리스트"),
                   "피킹리스트_%s_%s.xlsx" % (cname, date))
    # 인쇄: 유형 → 납품처(거점) → 상품
    tree = {}
    for l in lines:
        key = (l["delivery_type"], l["hub_name"] or "", l["store_name"], l["store_pg_code"])
        tree.setdefault(key, []).append(l)
    # 상품 합계(센터 전체 총량 = 피킹 총량)
    tot = {}
    for l in lines:
        t = tot.setdefault(l["item_pg_code"], {"code": l["item_pg_code"], "gaon": l["item_gaon"] or "", "name": l["item_name"], "qty": 0, "box": 0.0, "plt": 0.0})
        t["qty"] += l["qty"] or 0; t["box"] += l["box"] or 0; t["plt"] += l["plt"] or 0
    return render_template("split/picking.html", date=date, cname=cname, tree=tree, tot=sorted(tot.values(), key=lambda x: x["name"] or ""),
                           n_orders=len(orders), n_lines=len(lines))


@bp.route("/glovis.xlsx")
@role_required("admin", "center", "carrier")
def glovis_xlsx():
    """현대글로비스 작업지시(배차·착지) 양식: 납품일의 배차된 운송 건을 차량 → 순서로."""
    date = request.args.get("date") or dt.date.today().strftime("%Y-%m-%d")
    rows = q("SELECT s.*, o.order_no, o.store_pg_code, o.store_name, o.address, o.delivery_type, o.qty, o.box, o.plt, o.cbm, o.center_code, o.memo, "
             "v.plate, v.ton, v.carrier, d.name driver_name, d.phone, st.phone store_phone, st.lat, st.lng, h.name hub_name FROM shipments s JOIN orders o ON o.id=s.order_id "
             "LEFT JOIN vehicles v ON v.id=s.vehicle_id LEFT JOIN drivers d ON d.id=s.driver_id LEFT JOIN stores st ON st.id=o.store_id LEFT JOIN hubs h ON h.id=o.hub_id "
             "WHERE s.planned_date=? AND s.status NOT IN ('planned','cancelled') ORDER BY v.plate, s.seq, s.id", (date,))
    cnames = {c["code"]: c["name"] for c in _centers()}
    seq, last = 0, None
    out = []
    for r in rows:
        seq = seq + 1 if r["plate"] == last else 1
        last = r["plate"]
        out.append([date, cnames.get(r["center_code"], r["center_code"]), r["plate"] or "", r["ton"] or "", r["carrier"] or "", r["driver_name"] or "", r["phone"] or "", seq,
                    DTYPE_MAP.get(r["delivery_type"], ""), r["to_name"], r["store_pg_code"] or "", r["address"] or "", r["store_phone"] or "", r["order_no"] or "",
                    r["qty"], r["box"], r["plt"], r["cbm"], r["lat"], r["lng"], r["note"] or r["memo"] or ""])
    return _dl(_xlsx(["납품일", "출고센터", "차량번호", "톤수", "운송사", "기사", "기사연락처", "순서", "구분", "착지", "착지코드", "착지주소", "착지연락처", "오더번호",
                      "수량(EA)", "BOX", "PLT", "CBM", "위도", "경도", "비고"], out, "작업지시"), "글로비스_작업지시_%s.xlsx" % date)
