"""오더·운송 업무 로직.

- validate_rows: 파싱된 엑셀 행 검증(납품처·상품 매핑, 납품일, 수량) + EA/BOX/PLT 환산 + 일자·점포별 PLT 합계와 직송/거점 예상 판정
- import_rows : 오더/상품 저장 → 일자+점포 단위로 직송/거점 판정(기존 오더 포함 재판정) → 운송 구간 생성
- 운송 구간   : 직송 = 센터→납품처 1구간(기사 TMS 추적), 거점경유 = 센터→거점 1구간(출고 처리 시 완료 간주)
- 오더 상태는 운송 구간 상태에서 다시 계산한다(recompute_status)
"""
import json

from flask import g

from .db import execute, get_db, now, q, setting

BOX_UNITS = {"BOX", "BX", "CS", "CT", "CTN", "CASE", "C/S", "박스", "케이스", "상자"}


def _uid():
    u = g.get("user")
    return u["id"] if u else None


def _num(key, default):
    try:
        return float(setting(key) or default)
    except ValueError:
        return default


def add_event(order_id, status, note=""):
    execute("INSERT INTO order_events(order_id,status,note,user_id,at) VALUES(?,?,?,?,?)", (order_id, status, note, _uid(), now()))


def set_status(order_id, status, note=""):
    execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (status, now(), order_id))
    add_event(order_id, status, note)


# ---- 수량 환산 ----
def measure(qty, unit, item):
    """(EA, BOX, PLT). 단위가 BOX류면 qty는 박스 수. 상품 입수: box_qty=EA/BOX, plt_box=BOX/PLT, plt_qty=EA/PLT(없으면 box_qty×plt_box)."""
    if qty is None:
        return None, None, None
    return _measure(qty, unit, item)[:3]


def measure_cbm(qty, unit, item):
    """CBM = BOX수 × 상품 cbm(박스당). cbm이 없으면 None."""
    if qty is None:
        return None
    return _measure(qty, unit, item)[3]


def _measure(qty, unit, item):
    unit = (unit or setting("default_order_unit", "EA") or "EA").strip().upper()
    box_qty = (item["box_qty"] if item else None) or None
    plt_box = (item["plt_box"] if item else None) or None
    plt_qty = (item["plt_qty"] if item else None) or None
    if not plt_qty and box_qty and plt_box:
        plt_qty = box_qty * plt_box
    if unit in BOX_UNITS:
        box, ea = qty, (qty * box_qty if box_qty else None)
    else:
        ea, box = qty, (qty / box_qty if box_qty else None)
    plt = None
    if box is not None and plt_box:
        plt = box / plt_box
    elif ea is not None and plt_qty:
        plt = ea / plt_qty
    cbm = (box * item["cbm"]) if (box is not None and item and item["cbm"]) else None
    return ea, box, plt, cbm


def resolve_center(store, plant_text=None):
    """출고센터 코드: 납품처 마스터 → 오더의 출고센터 열(코드/이름 매칭) → 기본값."""
    if store and store["center_code"]:
        return store["center_code"]
    if plant_text:
        t = str(plant_text).strip()
        for c in q("SELECT code, name FROM centers WHERE active=1"):
            if t and (t.upper() == c["code"].upper() or t in c["name"] or c["name"] in t):
                return c["code"]
    return setting("default_center_code", "") or ""


def resolve_hub(store, area_code=None):
    """거점: 납품처 마스터의 거점 → (없으면) AREA CODE가 같은 거점. 반환 hub_id 또는 None."""
    if store and store["hub_id"]:
        return store["hub_id"]
    ac = (area_code or (store["area_code"] if store else None) or "").strip()
    if ac:
        h = q("SELECT id FROM hubs WHERE active=1 AND area_code=?", (ac,), one=True)
        if h:
            return h["id"]
    return None


# ---- 직송/거점 판정 ----
def day_threshold(date):
    """납품일별 기준 PLT. route_day에 있으면 그 값, 없으면 설정 기본값. 반환 (값, 출처)"""
    r = q("SELECT threshold FROM route_day WHERE date=?", (date,), one=True) if date else None
    if r:
        return float(r["threshold"]), "day"
    return _num("direct_plt_threshold", 3), "default"


def set_day_threshold(date, thr, note=""):
    execute("INSERT INTO route_day(date,threshold,note,updated_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET threshold=excluded.threshold, note=excluded.note, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (date, float(thr), note, _uid(), now()))


def route_rule(date=None):
    thr, src = day_threshold(date)
    return {"mode": setting("route_mode", "plt") or "plt", "thr": thr, "thr_src": src,
            "hub_missing": setting("route_hub_missing", "direct") or "direct"}


def decide_route(store, total_plt, rule=None, manual=None):
    """반환 (dtype, hub_id, note). dtype ∈ direct | hub | error. manual(담당자 지정)이 있으면 최우선."""
    rule = rule or route_rule()
    hub_id = resolve_hub(store)
    if manual in ("direct", "hub"):
        dtype, note = manual, "담당자 지정 %s" % ("직송" if manual == "direct" else "RDC")
    elif store and store["route_fixed"] in ("direct", "hub"):
        dtype, note = store["route_fixed"], "납품처 강제 %s" % ("직송" if store["route_fixed"] == "direct" else "RDC")
    elif rule["mode"] == "store":
        dtype, note = (store["delivery_type"] if store else "direct"), "납품처 마스터 유형"
    else:
        dtype = "direct" if (total_plt or 0) >= rule["thr"] else "hub"
        note = "일자+점포 합계 %.2f PLT %s 기준 %g PLT(%s) → %s" % (total_plt or 0, "이상" if dtype == "direct" else "미만", rule["thr"], "당일 기준" if rule.get("thr_src") == "day" else "기본값", "직송" if dtype == "direct" else "RDC")
    if dtype == "hub" and not hub_id:
        if rule["hub_missing"] == "error":
            return "error", None, note + " · 납품처에 거점 미지정"
        dtype, note = "direct", note + " · 거점 미지정→직송"
    return dtype, (hub_id if dtype == "hub" else None), note


def _existing_plt(store_id, date, exclude_batch=None):
    r = q("SELECT COALESCE(SUM(plt),0) p, COUNT(*) c FROM orders WHERE store_id=? AND delivery_date=? AND status NOT IN ('cancelled','error')" +
          (" AND batch_id!=?" if exclude_batch else ""), (store_id, date) + ((exclude_batch,) if exclude_batch else ()), one=True)
    return r["p"] or 0, r["c"]


# ---- 검증 ----
def validate_rows(rows):
    """행마다 납품처·상품 매핑·환산·오류를 붙이고, 일자+점포 그룹별 PLT 합계와 예상 판정을 만든다. 반환: (행 목록, 요약)"""
    stores = {s["pg_code"]: s for s in q("SELECT * FROM stores WHERE active=1") if s["pg_code"]}
    items = {i["pg_code"]: i for i in q("SELECT * FROM items WHERE active=1") if i["pg_code"]}
    rule = route_rule()
    summary = {"total": 0, "ok": 0, "error": 0, "orders": set(), "unknown_store": set(), "unknown_item": set(), "groups": {}, "rule": rule}
    for r in rows:
        errs = []
        s = stores.get(r["store_code"])
        it = items.get(r["item_code"])
        r["store"], r["item"] = s, it
        if not r["store_code"]:
            errs.append("납품처코드 없음")
        elif not s:
            errs.append("납품처 미등록")
            summary["unknown_store"].add(r["store_code"])
        if not r["item_code"]:
            errs.append("상품코드 없음")
        elif not it:
            errs.append("상품 미등록")
            summary["unknown_item"].add(r["item_code"])
        if not r["delivery_date"]:
            errs.append("납품일 형식 오류(%s)" % (r.get("delivery_date_raw") or "빈칸"))
        if r["qty"] is None:
            errs.append("수량 형식 오류(%s)" % r.get("qty_raw"))
        elif r["qty"] <= 0:
            errs.append("수량 0")
        r["ea"], r["box"], r["plt"] = measure(r["qty"] if r["qty"] and r["qty"] > 0 else None, r.get("unit"), it)
        r["cbm"] = measure_cbm(r["qty"] if r["qty"] and r["qty"] > 0 else None, r.get("unit"), it)
        if s and not s["area_code"] and r.get("area_code"):
            execute("UPDATE stores SET area_code=? WHERE id=?", (r["area_code"], s["id"]))  # 오더의 AREA CODE를 마스터에 채움
        if not errs and r["plt"] is None and rule["mode"] == "plt":
            errs.append("PLT 환산 불가(상품 입수 없음)")
        r["errors"] = errs
        summary["total"] += 1
        summary["ok" if not errs else "error"] += 1
        summary["orders"].add(order_key(r))
        if s and r["delivery_date"]:
            gk = (s["id"], r["delivery_date"])
            gr = summary["groups"].setdefault(gk, {"store": s, "store_name": s["name"], "store_code": s["pg_code"], "date": r["delivery_date"],
                                                   "plt": 0.0, "lines": 0, "orders": set(), "errors": 0})
            gr["plt"] += r["plt"] or 0
            gr["lines"] += 1
            gr["orders"].add(order_key(r))
            gr["errors"] += 1 if errs else 0
    for gk, gr in summary["groups"].items():
        gr["existing_plt"], gr["existing_cnt"] = _existing_plt(gk[0], gk[1])
        gr["total_plt"] = gr["plt"] + gr["existing_plt"]
        drule = route_rule(gk[1])
        gr["thr"], gr["thr_src"] = drule["thr"], drule["thr_src"]
        gr["dtype"], gr["hub_id"], gr["note"] = decide_route(gr["store"], gr["total_plt"], drule)
        gr["orders"] = len(gr["orders"])
        gr["hub_name"] = (q("SELECT name FROM hubs WHERE id=?", (gr["hub_id"],), one=True) or {"name": ""})["name"] if gr["hub_id"] else ""
    summary["groups"] = sorted(summary["groups"].values(), key=lambda x: (x["date"], x["store_name"]))
    summary["orders"] = len(summary["orders"])
    summary["unknown_store"] = sorted(summary["unknown_store"])
    summary["unknown_item"] = sorted(summary["unknown_item"])
    return rows, summary


def order_key(r):
    """오더 묶음 키: 오더번호가 있으면 그것, 없으면 납품처+납품일."""
    return r["order_no"] or "%s|%s" % (r["store_code"], r["delivery_date"])


# ---- 저장 ----
def import_rows(rows, filename, skip_errors=True):
    """검증된 행을 오더로 저장. 오류 행은 skip_errors=True면 제외, 아니면 오류 오더로 저장. 저장 후 일자+점포 단위로 유형 판정."""
    con = get_db()
    batch_id = execute("INSERT INTO upload_batches(filename,uploaded_by,uploaded_at) VALUES(?,?,?)", (filename, _uid(), now()))
    groups = {}
    for r in rows:
        if r["errors"] and skip_errors:
            continue
        groups.setdefault(order_key(r), []).append(r)
    center_code = setting("default_center_code", "")
    created, errored, touched = 0, 0, set()
    for key, lines in groups.items():
        first = lines[0]
        s = first["store"]
        has_err = any(l["errors"] for l in lines)
        qty = sum((l["ea"] if l["ea"] is not None else (l["qty"] or 0)) for l in lines)
        box = sum(l["box"] or 0 for l in lines)
        plt = sum(l["plt"] or 0 for l in lines)
        cbm = sum(l.get("cbm") or 0 for l in lines)
        oid = execute(
            "INSERT INTO orders(order_no,batch_id,store_id,store_pg_code,store_name,address,delivery_date,delivery_type,hub_id,center_code,"
            "status,error_msg,line_cnt,qty,box,plt,cbm,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (first["order_no"], batch_id, s["id"] if s else None, first["store_code"], (s["name"] if s else first["store_name"]),
             (s["address"] if s else first["address"]), first["delivery_date"], "direct", None, resolve_center(s, first.get("plant")) or center_code,
             "error" if has_err else "received", "; ".join(sorted({e for l in lines for e in l["errors"]})) or None,
             len(lines), qty, round(box, 2), round(plt, 3), round(cbm, 3), now(), now()))
        for l in lines:
            it = l["item"]
            con.execute("INSERT INTO order_items(order_id,item_id,item_pg_code,item_name,qty,unit,box,plt,cbm,error_msg) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (oid, it["id"] if it else None, l["item_code"], it["name"] if it else l["item_name"],
                         l["ea"] if l["ea"] is not None else (l["qty"] or 0), l.get("unit") or None,
                         round(l["box"], 2) if l["box"] is not None else None, round(l["plt"], 3) if l["plt"] is not None else None,
                         round(l["cbm"], 3) if l.get("cbm") is not None else None, "; ".join(l["errors"]) or None))
        add_event(oid, "error" if has_err else "received", "엑셀 업로드: %s" % filename)
        if has_err:
            errored += 1
        else:
            created += 1
            touched.add((s["id"], first["delivery_date"]))
    con.execute("UPDATE upload_batches SET total=?, ok=?, error=? WHERE id=?", (created + errored, created, errored, batch_id))
    con.commit()
    apply_route(touched)
    _fill_store_coords(groups)
    return batch_id, created, errored


def apply_route(pairs):
    """(store_id, date) 묶음마다 활성 오더의 PLT 합계로 직송/거점을 판정하고, 아직 배차 전인 오더의 유형·운송구간을 맞춘다. 반환 변경 수."""
    cnames = {c["code"]: c["name"] for c in q("SELECT code, name FROM centers")}
    changed = 0
    for store_id, date in pairs:
        rule = route_rule(date)
        store = q("SELECT * FROM stores WHERE id=?", (store_id,), one=True)
        # 거점 미지정으로 보류(error)된 오더도 함께 재판정해 조건이 맞으면 살린다
        orders = q("SELECT * FROM orders WHERE store_id=? AND delivery_date=? AND status!='cancelled' AND (status!='error' OR error_msg LIKE '거점 미지정%')", (store_id, date))
        if not store or not orders:
            continue
        total = sum(o["plt"] or 0 for o in orders)
        manual = next((o["route_manual"] for o in orders if o["route_manual"] in ("direct", "hub")), None)
        dtype, hub_id, note = decide_route(store, total, rule, manual)
        for o in orders:
            if o["status"] not in ("received", "gaon", "error", "rdc"):
                continue  # 배차 이후 단계는 건드리지 않음
            if o["status"] == "error" and dtype != "error":
                execute("UPDATE orders SET status='received', error_msg=NULL WHERE id=?", (o["id"],))
                add_event(o["id"], "received", "보류 해제: " + note)
                o = q("SELECT * FROM orders WHERE id=?", (o["id"],), one=True)
            legs = q("SELECT * FROM shipments WHERE order_id=? AND status!='cancelled'", (o["id"],))
            if any(l["status"] != "planned" for l in legs):
                continue
            if dtype == "error":
                execute("UPDATE orders SET status='error', error_msg=?, route_note=?, updated_at=? WHERE id=?", ("거점 미지정(거점경유 판정)", note, now(), o["id"]))
                execute("DELETE FROM shipments WHERE order_id=? AND status='planned'", (o["id"],))
                add_event(o["id"], "error", "유형 판정: " + note)
                changed += 1
                continue
            same = o["delivery_type"] == dtype and (o["hub_id"] or None) == (hub_id or None) and legs
            execute("UPDATE orders SET delivery_type=?, hub_id=?, route_note=?, updated_at=? WHERE id=?", (dtype, hub_id, note, now(), o["id"]))
            if not same:
                execute("DELETE FROM shipments WHERE order_id=? AND status='planned'", (o["id"],))
                create_shipments(o["id"], dtype, hub_id, cnames.get(o["center_code"]) or setting("default_center_name", "한익 센터"), date, o["store_name"])
                if not recompute_status(o["id"], "유형 판정: " + note):
                    add_event(o["id"], o["status"], "유형 판정: " + note)
                changed += 1
    return changed


def reroute_all(date_from=None, date_to=None):
    """설정(기준 PLT 등)을 바꾼 뒤 배차 전 오더를 다시 판정."""
    where, args = ["(status IN ('received','gaon','rdc') OR (status='error' AND error_msg LIKE '거점 미지정%'))", "store_id IS NOT NULL"], []
    if date_from:
        where.append("delivery_date>=?"); args.append(date_from)
    if date_to:
        where.append("delivery_date<=?"); args.append(date_to)
    pairs = {(r["store_id"], r["delivery_date"]) for r in q("SELECT DISTINCT store_id, delivery_date FROM orders WHERE %s" % " AND ".join(where), args)}
    return apply_route(pairs), len(pairs)


def create_shipments(order_id, dtype, hub_id, center_name, date, store_name):
    """직송만 운송 건(센터→납품처 1구간, 기사 TMS 추적)을 만든다. RDC(거점)는 분리까지만 하고 운송 건을 만들지 않는다."""
    if dtype == "hub":
        return
    con = get_db()
    con.execute("INSERT INTO shipments(order_id,leg,from_name,to_name,planned_date) VALUES(?,?,?,?,?)",
                (order_id, 1, center_name, store_name, date))
    con.commit()


# ---- 상태 ----
def recompute_status(order_id, note=""):
    """운송 구간 상태 → 오더 상태. 취소/오류 오더는 건드리지 않는다. 상태가 바뀌면 True."""
    o = q("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not o or o["status"] in ("cancelled", "error"):
        return False
    if o["delivery_type"] == "hub":  # RDC는 분리가 끝. 운송 추적 없음
        if o["status"] != "rdc":
            set_status(order_id, "rdc", note)
            return True
        return False
    legs = q("SELECT * FROM shipments WHERE order_id=? AND status!='cancelled' ORDER BY leg", (order_id,))
    if not legs:
        if o["status"] not in ("received", "gaon"):
            set_status(order_id, "gaon" if o["gaon_status"] == "done" else "received", note)
            return True
        return False
    last = legs[-1]
    first = legs[0]
    if last["status"] == "delivered":
        new = "delivered"
    elif first["status"] == "arrived":
        new = "store_arrived"
    elif first["status"] == "departed":
        new = "shipped"
    elif first["status"] == "at_center":
        new = "center_arrived"
    elif any(l["vehicle_id"] or l["driver_id"] for l in legs):
        new = "dispatched"
    else:
        new = "gaon" if o["gaon_status"] == "done" else "received"
    if new != o["status"]:
        set_status(order_id, new, note)
        return True
    return False


def assign(shipment_ids, vehicle_id, driver_id, note=None):
    con = get_db()
    for sid in shipment_ids:
        con.execute("UPDATE shipments SET vehicle_id=?, driver_id=?, status=CASE WHEN status='planned' THEN 'assigned' ELSE status END,"
                    " assigned_at=?, note=COALESCE(?,note) WHERE id=?", (vehicle_id or None, driver_id or None, now(), note, sid))
    con.commit()
    v = q("SELECT plate FROM vehicles WHERE id=?", (vehicle_id,), one=True) if vehicle_id else None
    for sid in shipment_ids:
        s = q("SELECT order_id FROM shipments WHERE id=?", (sid,), one=True)
        if s:
            recompute_status(s["order_id"], "배차 %s" % (v["plate"] if v else ""))


def unassign(shipment_ids):
    """배차 해제. 이미 진행 중(센터도착 이후)인 건은 건너뛴다. 반환 (해제 수, 건너뜀 수)."""
    con = get_db()
    done = 0
    for sid in shipment_ids:
        done += con.execute("UPDATE shipments SET vehicle_id=NULL, driver_id=NULL, status='planned', assigned_at=NULL WHERE id=? AND status IN ('planned','assigned')", (sid,)).rowcount
    con.commit()
    for sid in shipment_ids:
        s = q("SELECT order_id FROM shipments WHERE id=?", (sid,), one=True)
        if s:
            recompute_status(s["order_id"], "배차 해제")
    return done, len(shipment_ids) - done


def advance_shipment(sid, action, lat=None, lng=None, accuracy=None):
    """기사 화면: center(센터 도착) → depart(센터 출발) → arrive(점포 도착) → deliver(배송 완료).

    GPS가 오면 ship_logs에 남기고, 센터 도착·출발 때는 납품처까지 예상도착을 계산해 shipments.eta_*에 저장,
    점포 도착 때는 납품처 좌표와의 거리(arrive_dist_m)로 실제 도착을 검증한다. 반환 (shipment, 안내문).
    """
    from .db import DRIVER_ACTIONS, SHIP_TIME_COL
    from .geo import eta, haversine_km
    new_status, label = DRIVER_ACTIONS[action]
    t = now()
    execute("UPDATE shipments SET status=?, %s=? WHERE id=?" % SHIP_TIME_COL[new_status], (new_status, t, sid))
    s = q("SELECT s.*, st.lat store_lat, st.lng store_lng FROM shipments s JOIN orders o ON o.id=s.order_id LEFT JOIN stores st ON st.id=o.store_id WHERE s.id=?", (sid,), one=True)
    info, dist_km, eta_min, src = "", None, None, None
    if lat is not None and lng is not None:
        execute("UPDATE shipments SET last_lat=?, last_lng=?, last_at=? WHERE id=?", (lat, lng, t, sid))
        if s["store_lat"] is not None and s["store_lng"] is not None:
            if action in ("center", "depart"):
                e = eta(lat, lng, s["store_lat"], s["store_lng"])
                if e:
                    dist_km, eta_min, src = e["dist_km"], e["min"], e["source"]
                    execute("UPDATE shipments SET eta_at=?, eta_min=?, eta_km=? WHERE id=?", (e["eta_at"], e["min"], e["dist_km"], sid))
                    info = "예상 도착 %s (약 %skm, %s분%s)" % (e["eta_at"][11:], e["dist_km"], e["min"], "" if src == "osrm" else " 추정")
            elif action == "arrive":
                dist_m = round(haversine_km(lat, lng, s["store_lat"], s["store_lng"]) * 1000)
                dist_km = round(dist_m / 1000, 2)
                execute("UPDATE shipments SET arrive_dist_m=? WHERE id=?", (dist_m, sid))
                radius = _num("arrive_radius_m", 500)
                info = "납품처에서 %sm (%s)" % (dist_m, "반경 내 확인" if dist_m <= radius else "반경 %dm 밖 — 위치 확인 필요" % radius)
        else:
            info = "납품처 좌표 미등록 — 예상도착 계산 불가"
    else:
        info = "위치 정보 없음"
    execute("INSERT INTO ship_logs(shipment_id,action,at,lat,lng,accuracy,dist_km,eta_min,eta_source,user_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, action, t, lat, lng, accuracy, dist_km, eta_min, src, _uid()))
    # 점포 SMS(출발·도착) — 설정 sms_enabled=1 일 때만
    from .notify import notify_store
    sms = notify_store(sid, action, (q("SELECT eta_at FROM shipments WHERE id=?", (sid,), one=True)["eta_at"] or "")[11:16])
    if sms:
        info = (info + " · " if info else "") + sms
    note = "%s → %s %s%s" % (s["from_name"], s["to_name"], label, (" · " + info) if info else "")
    if not recompute_status(s["order_id"], note):
        o = q("SELECT status FROM orders WHERE id=?", (s["order_id"],), one=True)
        add_event(s["order_id"], o["status"], note)
    return s, info


def mark_gaon(order_id, ok, slip_no=None, msg=None, payload=None, batch_id=None, kind="register"):
    execute("INSERT INTO gaon_jobs(batch_id,order_id,kind,requested_at,finished_at,ok,message,payload,user_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (batch_id, order_id, kind, now(), now(), 1 if ok else 0, msg, json.dumps(payload, ensure_ascii=False) if payload else None, _uid()))
    execute("UPDATE orders SET gaon_status=?, gaon_slip_no=COALESCE(?,gaon_slip_no), gaon_msg=?, updated_at=? WHERE id=?",
            ("done" if ok else "failed", slip_no, msg, now(), order_id))
    if ok:
        add_event(order_id, "gaon", "가온 수주등록 %s" % (slip_no or ""))
        recompute_status(order_id)


def _fill_store_coords(groups):
    """업로드된 오더의 납품처 중 좌표가 없는 곳을 주소로 지오코딩(마스터 주소 없으면 오더 주소를 마스터에 채움). 키 없으면 건너뜀."""
    from .geocode import GeocodeError, geocode
    if not setting("kakao_rest_key"):
        return
    seen = set()
    for lines in groups.values():
        s = lines[0]["store"]
        if not s or s["id"] in seen or (s["lat"] is not None and s["lng"] is not None):
            continue
        seen.add(s["id"])
        addr = s["address"] or lines[0]["address"]
        if not addr:
            continue
        try:
            g_ = geocode(addr)
        except GeocodeError:
            return
        if g_:
            execute("UPDATE stores SET lat=?, lng=?, address=COALESCE(address,?) WHERE id=?", (g_["lat"], g_["lng"], addr, s["id"]))
