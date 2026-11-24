"""P&G 오더 엑셀 파서.

샘플 양식이 확정되기 전이라 헤더 동의어(settings.excel_map)로 컬럼을 추정한다.
헤더 행은 첫 20행 안에서 매핑 필드가 가장 많이 맞는 행으로 잡는다.
결과: 행 단위 dict 목록 + 컬럼 매핑 결과 + 누락 필드.
"""
import datetime as dt
import io
import json
import re

import openpyxl

REQUIRED = ["store_code", "delivery_date", "item_code", "qty"]
FIELD_LABEL = {
    "order_no": "오더번호", "store_code": "납품처코드", "store_name": "납품처명", "address": "주소",
    "delivery_date": "납품일", "item_code": "상품코드", "item_name": "상품명", "qty": "수량", "unit": "단위",
    "area_code": "AREA CODE", "plant": "출고센터",
}


def _norm(s):
    return re.sub(r"[\s_\-\.\(\)\[\]/]+", "", str(s or "")).lower()


def _cell_str(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def parse_date(v):
    """엑셀 날짜·문자열(20261115, 2026-11-15, 11/15, 2026.11.15) → YYYY-MM-DD. 실패 시 None."""
    if v is None or v == "":
        return None
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.match(r"^(\d{4})[.\-/]?(\d{1,2})[.\-/]?(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"^(\d{1,2})[.\-/](\d{1,2})$", s)
        if not m:
            return None
        y, mo, d = dt.date.today().year, int(m.group(1)), int(m.group(2))
    try:
        return dt.date(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_num(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def build_colmap(header_cells, excel_map):
    """헤더 셀 목록 → {field: col_index}. 동의어 완전일치 우선, 없으면 부분일치."""
    norm_hdr = [_norm(h) for h in header_cells]
    colmap = {}
    for field, names in excel_map.items():
        cands = [_norm(n) for n in names]
        hit = next((i for i, h in enumerate(norm_hdr) if h and h in cands), None)
        if hit is None:
            hit = next((i for i, h in enumerate(norm_hdr) if h and any(c and (c in h or h in c) for c in cands)), None)
        if hit is not None and hit not in colmap.values():
            colmap[field] = hit
    return colmap


def parse_order_excel(data, excel_map, sheet=None):
    """data: bytes. 반환 {'rows': [...], 'colmap': {field: 헤더명}, 'missing': [field], 'header_row': n, 'sheet': name}"""
    if isinstance(excel_map, str):
        excel_map = json.loads(excel_map)
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    buf = []
    header_idx, best, best_map = None, -1, {}
    for i, r in enumerate(rows_iter):
        buf.append(r)
        if i >= 20:
            break
        cm = build_colmap(r, excel_map)
        score = sum(1 for f in cm if f in REQUIRED) * 2 + len(cm)
        if score > best and any(f in cm for f in REQUIRED):
            header_idx, best, best_map = i, score, cm
    if header_idx is None:
        return {"rows": [], "colmap": {}, "missing": REQUIRED[:], "header_row": None, "sheet": ws.title,
                "error": "헤더 행을 찾지 못했습니다. 설정에서 엑셀 컬럼명을 지정해 주세요."}
    header = buf[header_idx]
    colmap = best_map
    missing = [f for f in REQUIRED if f not in colmap]
    out = []

    def take(r, field):
        i = colmap.get(field)
        return r[i] if i is not None and i < len(r) else None

    data_rows = buf[header_idx + 1:]
    for r in data_rows:
        _collect(out, r, take, header_idx + 2 + len(out) + 0)
    for r in rows_iter:
        _collect(out, r, take, None)
    # 엑셀 행번호 다시 매김
    for n, row in enumerate(out):
        row["row"] = n + header_idx + 2
    wb.close()
    return {"rows": out, "colmap": {f: _cell_str(header[i]) for f, i in colmap.items()}, "missing": missing,
            "header_row": header_idx + 1, "sheet": ws.title}


def _collect(out, r, take, _n):
    if r is None or all(v is None or str(v).strip() == "" for v in r):
        return
    row = {f: _cell_str(take(r, f)) for f in FIELD_LABEL}
    row["delivery_date_raw"] = row["delivery_date"]
    row["delivery_date"] = parse_date(take(r, "delivery_date"))
    row["qty_raw"] = row["qty"]
    row["qty"] = parse_num(take(r, "qty"))
    out.append(row)
