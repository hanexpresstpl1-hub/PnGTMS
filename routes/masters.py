"""마스터: 거점센터, 차량, 기사, 납품처(P&G↔가온 매핑, 거점, 강제유형, 좌표), 상품(입수 EA/BOX·BOX/PLT·EA/PLT). 엑셀 일괄 등록(헤더 동의어 인식)."""
import io
import re

import openpyxl
from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from core.auth import role_required
from core.db import execute, get_db, q, setting
from core.geocode import GeocodeError, geocode_table

bp = Blueprint("masters", __name__, url_prefix="/masters")

# 종류별 정의: 테이블, 화면 제목, 컬럼(필드, 라벨, 입력타입)
KINDS = {
    "centers": {"title": "출고센터", "cols": [("code", "코드", "text"), ("name", "센터명", "text"), ("address", "주소", "text"), ("email", "메일", "text"),
                                          ("lat", "위도", "number"), ("lng", "경도", "number")]},
    "hubs": {"title": "거점센터(RDC)", "cols": [("code", "코드", "text"), ("name", "거점명", "text"), ("area_code", "AREA CODE", "text"), ("address", "주소", "text"), ("region", "권역", "text"),
                                        ("contact", "연락처", "text"), ("email", "반품 안내 메일", "text"), ("lat", "위도", "number"), ("lng", "경도", "number")]},
    "vehicles": {"title": "차량", "cols": [("plate", "차량번호", "text"), ("ton", "톤수", "text"), ("capacity_plt", "적재 PLT", "number"), ("capacity_cbm", "적재 CBM", "number"),
                                        ("owner", "자차/용차", "text"), ("carrier", "운송사", "text"), ("driver_id", "기본 기사", "driver")]},
    "drivers": {"title": "기사", "cols": [("name", "이름", "text"), ("phone", "연락처", "text"), ("company", "소속", "text")]},
    "stores": {"title": "납품처", "cols": [("pg_code", "P&G 코드", "text"), ("name", "납품처명", "text"), ("gaon_code", "가온 점포코드", "text"), ("address", "주소", "text"),
                                        ("phone", "연락처(SMS)", "text"), ("area_code", "AREA CODE", "text"), ("region", "권역", "text"), ("center_code", "출고센터", "center"),
                                        ("hub_id", "거점(RDC)", "hub"), ("route_fixed", "강제 유형", "route"), ("delivery_type", "기본 유형(마스터 모드)", "dtype"),
                                        ("lat", "위도", "number"), ("lng", "경도", "number")]},
    "items": {"title": "상품", "cols": [("pg_code", "P&G 코드", "text"), ("name", "상품명", "text"), ("gaon_code", "가온 상품코드", "text"), ("box_qty", "입수 EA/BOX", "number"),
                                      ("plt_box", "입수 BOX/PLT", "number"), ("plt_qty", "입수 EA/PLT", "number"), ("cbm", "CBM/BOX", "number"), ("weight_kg", "KG/BOX", "number")]},
}
# 엑셀 일괄 등록 시 헤더 동의어 (라벨과 같으면 그대로, 아니면 아래에서 찾음)
SYNONYMS = {
    "items": {"pg_code": ["상품코드", "제품코드", "품목코드", "SKU", "Material", "GTIN", "바코드", "P&G코드", "PG코드", "자재번호"],
              "name": ["상품명", "제품명", "품명", "품목명", "Description", "Material Description"],
              "gaon_code": ["가온코드", "가온상품코드", "한익코드", "자사코드", "WMS코드"],
              "box_qty": ["EA/BOX", "박스입수", "BOX입수", "케이스입수", "CS입수", "입수", "입수량", "박스당수량", "EA/CS", "PCS/BOX"],
              "plt_box": ["BOX/PLT", "박스/PLT", "PLT당박스", "팔레트당박스", "PLT입수(BOX)", "CS/PLT", "BOX/PALLET", "적재박스"],
              "plt_qty": ["EA/PLT", "PLT입수", "팔레트입수", "PLT당수량", "EA/PALLET"],
              "cbm": ["CBM", "박스CBM", "CBM/BOX", "부피", "Volume"], "weight_kg": ["KG", "중량", "무게", "KG/BOX", "Weight"]},
    "stores": {"pg_code": ["납품처코드", "점포코드", "거래처코드", "Ship-to", "ShipTo", "고객코드", "P&G코드"],
               "name": ["납품처명", "점포명", "거래처명", "Ship-to Name", "고객명"],
               "gaon_code": ["가온점포코드", "가온코드", "한익점포코드", "자사점포코드"],
               "address": ["주소", "납품처주소", "배송지", "도로명주소"], "region": ["권역", "지역"], "phone": ["연락처", "전화", "전화번호", "점포전화", "휴대폰"],
               "area_code": ["AREA CODE", "AREA", "지역코드", "AreaCode", "권역코드"],
               "hub_id": ["거점", "거점센터", "거점명"], "route_fixed": ["강제유형", "유형고정", "고정유형"], "delivery_type": ["배송유형", "유형", "기본유형"],
               "center_code": ["출고센터", "센터코드", "센터"], "lat": ["위도", "lat", "latitude", "y"], "lng": ["경도", "lng", "lon", "longitude", "x"]},
    "hubs": {"code": ["코드", "거점코드"], "name": ["거점명", "거점", "거점센터명", "센터명", "RDC"], "area_code": ["AREA CODE", "AREA", "지역코드", "권역코드"], "address": ["주소"],
             "region": ["권역", "지역"], "contact": ["연락처", "전화"], "email": ["메일", "이메일", "email", "반품메일"], "lat": ["위도", "lat"], "lng": ["경도", "lng", "lon"]},
    "centers": {"code": ["코드", "센터코드", "Plant"], "name": ["센터명", "센터", "출고센터"], "address": ["주소"], "email": ["메일", "이메일"], "lat": ["위도"], "lng": ["경도"]},
    "vehicles": {"plate": ["차량번호", "차번", "차량"], "ton": ["톤수", "톤"], "capacity_plt": ["적재PLT", "PLT", "적재"], "capacity_cbm": ["적재CBM", "CBM"],
                 "owner": ["자차/용차", "구분"], "carrier": ["운송사", "소속", "협력사"], "driver_id": ["기본기사", "기사", "기사명"]},
    "drivers": {"name": ["이름", "기사명", "성명", "기사"], "phone": ["연락처", "전화", "휴대폰", "전화번호"], "company": ["소속", "회사", "운송사"]},
}


def _norm(s):
    return re.sub(r"[\s_\-\.\(\)\[\]/·]+", "", str(s or "")).lower()


def _kind(kind):
    if kind not in KINDS:
        abort(404)
    return KINDS[kind]


def _refs():
    return {"driver": q("SELECT id, name || COALESCE(' ' || phone, '') AS label FROM drivers WHERE active=1 ORDER BY name"),
            "hub": q("SELECT id, name AS label FROM hubs WHERE active=1 ORDER BY name"),
            "center": q("SELECT code AS id, name AS label FROM centers WHERE active=1 ORDER BY code"),
            "dtype": [{"id": "direct", "label": "직송"}, {"id": "hub", "label": "거점경유"}],
            "route": [{"id": "", "label": "규칙 적용"}, {"id": "direct", "label": "항상 직송"}, {"id": "hub", "label": "항상 거점경유"}]}


@bp.route("/<kind>")
@role_required("admin")
def list_kind(kind):
    k = _kind(kind)
    kw = request.args.get("keyword", "").strip()
    text_cols = [c for c, _, t in k["cols"] if t in ("text",)]
    where, args = "1=1", []
    if kw and text_cols:
        where = "(" + " OR ".join("%s LIKE ?" % c for c in text_cols) + ")"
        args = ["%" + kw + "%"] * len(text_cols)
    rows = q("SELECT * FROM %s WHERE %s ORDER BY active DESC, id DESC LIMIT 1000" % (kind, where), args)
    refs = _refs()
    label = {t: {str(r["id"]): r["label"] for r in refs[t]} for t in refs}
    return render_template("masters/list.html", kind=kind, k=k, rows=rows, refs=refs, label=label, kw=kw)


def _derive(kind, vals):
    if kind == "items" and not vals.get("plt_qty") and vals.get("box_qty") and vals.get("plt_box"):
        vals["plt_qty"] = vals["box_qty"] * vals["plt_box"]
    if kind == "stores":
        vals["delivery_type"] = vals.get("delivery_type") or "direct"
        if vals.get("route_fixed") not in ("direct", "hub"):
            vals["route_fixed"] = None
    return vals


@bp.route("/<kind>/save", methods=["POST"])
@role_required("admin")
def save(kind):
    k = _kind(kind)
    rid = request.form.get("id")
    vals = {}
    for c, _, t in k["cols"]:
        v = request.form.get(c, "").strip()
        vals[c] = (v or None) if t != "number" else (float(v) if v else None)
    vals = _derive(kind, vals)
    vals["active"] = 1 if request.form.get("active", "1") == "1" else 0
    try:
        if rid:
            execute("UPDATE %s SET %s WHERE id=?" % (kind, ", ".join("%s=?" % c for c in vals)), list(vals.values()) + [rid])
        else:
            execute("INSERT INTO %s(%s) VALUES(%s)" % (kind, ",".join(vals), ",".join("?" * len(vals))), list(vals.values()))
        flash("저장했습니다.", "ok")
    except Exception as e:  # UNIQUE 위반 등
        flash("저장 실패: %s" % e, "error")
    return redirect(url_for("masters.list_kind", kind=kind))


@bp.route("/<kind>/toggle/<int:rid>", methods=["POST"])
@role_required("admin")
def toggle(kind, rid):
    _kind(kind)
    execute("UPDATE %s SET active=1-active WHERE id=?" % kind, (rid,))
    return redirect(url_for("masters.list_kind", kind=kind))


@bp.route("/<kind>/geocode", methods=["POST"])
@role_required("admin")
def geocode_kind(kind):
    """납품처·거점 주소로 좌표 채우기. 기본은 좌표 없는 행만, force=1이면 선택/전체 다시."""
    if kind not in ("stores", "hubs", "centers"):
        abort(404)
    ids = [int(x) for x in request.form.getlist("id") if x.isdigit()] or None
    try:
        res = geocode_table(kind, ids=ids, only_missing=request.form.get("force") != "1")
    except GeocodeError as e:
        flash(str(e), "error")
        return redirect(url_for("masters.list_kind", kind=kind))
    msg = "좌표 %d/%d건 채움." % (res["ok"], res["total"])
    if res["fail"]:
        msg += " 실패 %d건: %s" % (len(res["fail"]), ", ".join("%s(%s)" % (n, a) for _, n, a in res["fail"][:8])) + (" …" if len(res["fail"]) > 8 else "")
    if res["total"] == 0:
        msg = "좌표가 비어 있고 주소가 있는 행이 없습니다."
    flash(msg, "ok" if res["ok"] or res["total"] == 0 else "error")
    return redirect(url_for("masters.list_kind", kind=kind))


@bp.route("/<kind>/template")
@role_required("admin")
def template(kind):
    k = _kind(kind)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = k["title"]
    ws.append([lab for _, lab, _ in k["cols"]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="%s_양식.xlsx" % k["title"], mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _find_header(kind, k, rows):
    """첫 15행 안에서 라벨/동의어가 가장 많이 맞는 행을 헤더로. 반환 (행 index, {field: col index})"""
    syn = SYNONYMS.get(kind, {})
    best, best_i, best_map = -1, None, {}
    for i, r in enumerate(rows[:15]):
        hdr = [_norm(h) for h in r]
        m = {}
        for c, lab, _ in k["cols"]:
            cands = [_norm(lab)] + [_norm(x) for x in syn.get(c, [])]
            hit = next((j for j, h in enumerate(hdr) if h and h in cands), None)
            if hit is None:
                hit = next((j for j, h in enumerate(hdr) if h and len(h) >= 2 and any(x and (x == h or x in h) for x in cands)), None)
            if hit is not None and hit not in m.values():
                m[c] = hit
        if len(m) > best:
            best, best_i, best_map = len(m), i, m
    return best_i, best_map


@bp.route("/<kind>/import", methods=["POST"])
@role_required("admin")
def import_xlsx(kind):
    """엑셀 일괄 등록: 헤더는 양식 라벨 또는 동의어(상품코드/SKU/Material, 박스입수/EA/BOX, BOX/PLT …)로 인식. 코드 기준 추가/갱신."""
    k = _kind(kind)
    f = request.files.get("file")
    if not f:
        flash("파일을 선택하세요.", "error")
        return redirect(url_for("masters.list_kind", kind=kind))
    wb = openpyxl.load_workbook(io.BytesIO(f.read()), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        flash("빈 파일입니다.", "error")
        return redirect(url_for("masters.list_kind", kind=kind))
    hi, idx = _find_header(kind, k, rows)
    key = {"hubs": "code", "centers": "code", "vehicles": "plate", "drivers": "name", "stores": "pg_code", "items": "pg_code"}[kind]
    labels = {c: lab for c, lab, _ in k["cols"]}
    if hi is None or key not in idx:
        flash("'%s' 열을 찾지 못했습니다. 인식 가능한 헤더: %s" % (labels[key], ", ".join([labels[key]] + SYNONYMS.get(kind, {}).get(key, []))), "error")
        return redirect(url_for("masters.list_kind", kind=kind))
    refs = _refs()
    name2id = {t: {r["label"]: r["id"] for r in refs[t]} for t in ("driver", "hub", "center")}
    name2id["hub"].update({r["label"].split(" ")[0]: r["id"] for r in refs["hub"]})
    name2id["center"].update({str(r["id"]).upper(): r["id"] for r in refs["center"]})
    types = {c: t for c, _, t in k["cols"]}
    con = get_db()
    n_new = n_upd = 0
    for r in rows[hi + 1:]:
        if r is None or all(v is None or str(v).strip() == "" for v in r):
            continue
        vals = {}
        for c, i in idx.items():
            v = r[i] if i < len(r) else None
            v = None if v is None else (str(v).strip() if not isinstance(v, float) else (str(int(v)) if v.is_integer() else str(v)))
            t = types[c]
            if t == "number":
                try:
                    v = float(str(v).replace(",", "")) if v not in (None, "") else None
                except ValueError:
                    v = None
            elif t == "hub":
                v = name2id["hub"].get(v) if v else None
            elif t == "center":
                v = (name2id["center"].get(v) or name2id["center"].get(str(v).upper()) or next((cid for lab, cid in name2id["center"].items() if v and (v in lab or lab in v)), None)) if v else None
            elif t == "driver":
                v = next((i_ for lab, i_ in name2id["driver"].items() if v and lab.startswith(v)), None)
            elif t == "dtype":
                v = "hub" if v in ("hub", "거점", "거점경유") else "direct"
            elif t == "route":
                v = "hub" if v in ("hub", "거점", "거점경유", "항상 거점경유") else "direct" if v in ("direct", "직송", "항상 직송") else None
            vals[c] = v if v not in ("",) else None
        if not vals.get(key):
            continue
        vals = _derive(kind, vals)
        ex = con.execute("SELECT id FROM %s WHERE %s=?" % (kind, key), (vals[key],)).fetchone()
        if ex:
            upd = {c: v for c, v in vals.items() if v is not None or c in ("route_fixed",)}
            con.execute("UPDATE %s SET %s WHERE id=?" % (kind, ", ".join("%s=?" % c for c in upd)), list(upd.values()) + [ex[0]])
            n_upd += 1
        else:
            con.execute("INSERT INTO %s(%s) VALUES(%s)" % (kind, ",".join(vals), ",".join("?" * len(vals))), list(vals.values()))
            n_new += 1
    con.commit()
    msg = "%s 추가 %d건, 갱신 %d건. 인식한 열: %s" % (k["title"], n_new, n_upd, ", ".join("%s←%s" % (labels[c], str(rows[hi][i]).strip()) for c, i in idx.items()))
    if kind in ("stores", "hubs", "centers") and setting("kakao_rest_key"):
        try:
            res = geocode_table(kind, only_missing=True)
            if res["total"]:
                msg += " 좌표 자동 채움 %d/%d건." % (res["ok"], res["total"]) + (" 실패: %s" % ", ".join(n for _, n, _ in res["fail"][:8]) if res["fail"] else "")
        except GeocodeError as e:
            msg += " (좌표 채움 실패: %s)" % e
    flash(msg, "ok")
    return redirect(url_for("masters.list_kind", kind=kind))
