"""자동배차(PLT·CBM 적재 기준)와 차량별 적재율(VFR).

자동배차 규칙(1차, 단순·설명 가능):
  1. 대상 = 납품일의 미배차 운송 건(기본 직송만, include_hub=True면 RDC 간선 포함), 출고센터로 묶음
  2. 같은 센터 안에서 권역(region) → 거점 → 좌표(경도·위도) 순으로 정렬해 가까운 착지가 이어지게 함
  3. 차량은 적재 PLT 큰 순. 각 차량의 남은 용량 = 적재량 × 상한비율(auto_fill_rate) − 그날 이미 배정된 양
  4. 정렬된 건을 앞에서부터 현재 차량에 담고, PLT 또는 CBM이 넘치면 다음 차량으로 (first-fit, 한 건은 쪼개지 않음)
  5. 차량이 모자라면 남은 건은 미배차로 두고 보고
VFR = 배정 PLT ÷ 적재 PLT (CBM도 같은 식). 둘 중 큰 값을 대표 적재율로 본다.
"""
from .db import q, setting


def _rate():
    try:
        return float(setting("auto_fill_rate") or 0.95)
    except ValueError:
        return 0.95


def vehicle_loads(date):
    """{vehicle_id: {cnt, plt, cbm, cap_plt, cap_cbm, vfr_plt, vfr_cbm, vfr}}"""
    rows = q("SELECT s.vehicle_id, COUNT(*) cnt, SUM(o.plt) plt, SUM(o.cbm) cbm, v.capacity_plt, v.capacity_cbm, v.plate, v.ton, v.carrier "
             "FROM shipments s JOIN orders o ON o.id=s.order_id JOIN vehicles v ON v.id=s.vehicle_id "
             "WHERE s.planned_date=? AND s.status!='cancelled' AND o.status!='cancelled' GROUP BY s.vehicle_id", (date,))
    out = {}
    for r in rows:
        vp = (r["plt"] or 0) / r["capacity_plt"] if r["capacity_plt"] else None
        vc = (r["cbm"] or 0) / r["capacity_cbm"] if r["capacity_cbm"] else None
        out[r["vehicle_id"]] = {"cnt": r["cnt"], "plt": r["plt"] or 0, "cbm": r["cbm"] or 0, "cap_plt": r["capacity_plt"], "cap_cbm": r["capacity_cbm"],
                                "vfr_plt": vp, "vfr_cbm": vc, "vfr": max([x for x in (vp, vc) if x is not None] or [None]) if (vp is not None or vc is not None) else None,
                                "plate": r["plate"], "ton": r["ton"], "carrier": r["carrier"]}
    return out


def plan(date, center=None, include_hub=False, vehicle_ids=None):
    """배차안 계산(저장 안 함). 반환 {'assign': [(shipment_id, vehicle_id)], 'left': [shipment rows], 'summary': [...]}"""
    rate = _rate()
    where = ["s.planned_date=?", "s.status='planned'", "o.status NOT IN ('cancelled','error')", "o.delivery_type='direct'"]  # 배차는 직송만
    args = [date]
    if center:
        where.append("COALESCE(o.center_code,'')=?"); args.append(center)
    cands = q("SELECT s.id sid, s.to_name, o.center_code, o.delivery_type, o.plt, o.cbm, o.box, o.store_name, h.name hub_name, h.region hub_region, "
              "st.region, st.lat, st.lng FROM shipments s JOIN orders o ON o.id=s.order_id LEFT JOIN stores st ON st.id=o.store_id LEFT JOIN hubs h ON h.id=o.hub_id "
              "WHERE %s" % " AND ".join(where), args)
    if not cands:
        return {"assign": [], "left": [], "summary": [], "msg": "미배차 운송 건이 없습니다."}
    # 정렬: 센터 → 유형(직송 먼저) → 권역 → 거점 → 경도 → 위도
    cands = sorted(cands, key=lambda r: (r["center_code"] or "", 0 if r["delivery_type"] == "direct" else 1, r["region"] or r["hub_region"] or "~", r["hub_name"] or "",
                                         round(r["lng"] or 0, 1), round(r["lat"] or 0, 1), r["to_name"] or ""))
    vwhere, vargs = ["active=1", "capacity_plt IS NOT NULL", "capacity_plt>0"], []
    if vehicle_ids:
        vwhere.append("id IN (%s)" % ",".join("?" * len(vehicle_ids))); vargs += list(vehicle_ids)
    vehicles = q("SELECT * FROM vehicles WHERE %s ORDER BY capacity_plt DESC, plate" % " AND ".join(vwhere), vargs)
    used = vehicle_loads(date)
    cap = []
    for v in vehicles:
        u = used.get(v["id"], {"plt": 0, "cbm": 0})
        cap.append({"v": v, "plt_left": v["capacity_plt"] * rate - u["plt"], "cbm_left": (v["capacity_cbm"] * rate - u["cbm"]) if v["capacity_cbm"] else None, "items": []})
    assign, left = [], []
    vi = 0
    for r in cands:
        plt, cbm = r["plt"] or 0, r["cbm"] or 0
        placed = False
        # 현재 차량부터 순서대로, 들어가는 첫 차량에
        for j in range(vi, len(cap)):
            c = cap[j]
            if plt <= c["plt_left"] + 1e-9 and (c["cbm_left"] is None or cbm <= c["cbm_left"] + 1e-9):
                c["plt_left"] -= plt
                if c["cbm_left"] is not None:
                    c["cbm_left"] -= cbm
                c["items"].append(r)
                assign.append((r["sid"], c["v"]["id"]))
                placed = True
                # 다음 건도 같은 차량부터 시도하되, 꽉 찬 차량은 건너뜀
                vi = j if c["plt_left"] > 0.05 else j + 1
                break
        if not placed:
            left.append(r)
    summary = [{"vehicle": c["v"], "cnt": len(c["items"]), "plt": sum(x["plt"] or 0 for x in c["items"]), "cbm": sum(x["cbm"] or 0 for x in c["items"]),
                "stops": [x["to_name"] for x in c["items"]],
                "vfr": (sum(x["plt"] or 0 for x in c["items"]) + used.get(c["v"]["id"], {"plt": 0})["plt"]) / c["v"]["capacity_plt"]} for c in cap if c["items"]]
    return {"assign": assign, "left": left, "summary": summary, "rate": rate,
            "msg": "%d건 중 %d건 배정, %d건 미배정(차량 부족 또는 1건이 차량 용량 초과)" % (len(cands), len(assign), len(left))}
