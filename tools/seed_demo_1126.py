"""시연용 전체 데이터 생성 (납품일 2026-11-26 기준, 날짜는 인자로 변경 가능).

사용: 서버를 끈 상태에서  py tools/seed_demo_1126.py [납품일 YYYY-MM-DD]
  1) 기존 data/portal.db 를 data/backups/ 에 백업하고 새로 만든다
  2) 마스터: 출고센터 2(천안 CA, 안성 AS), RDC 5, 납품처 20, 상품 12, 차량 6, 기사 6, 계정 4
  3) P&G SAP 형식 오더 엑셀(예시파일/시연_PG오더_<날짜>.xlsx)을 만들어 포털 로직으로 업로드·판정
  4) 가온 등록(시뮬레이션), 자동배차, 기사 진행(완료·이동 중·센터 도착), RDC 출고, 반품 4건, SMS 큐까지 채운다
비밀번호: 관리자 1111/1111, center1·sammi·pg_user = demo1234 (운영 DB에는 쓰지 말 것)
"""
import datetime as dt
import io
import os
import shutil
import sqlite3
import sys

import openpyxl
from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-26"
DATA = os.environ.get("PGPORTAL_DATA") or os.path.join(BASE, "data")
DB = os.path.join(DATA, "portal.db")
SAMPLE_DIR = os.path.join(BASE, "예시파일")
os.makedirs(os.path.join(DATA, "backups"), exist_ok=True)
os.makedirs(SAMPLE_DIR, exist_ok=True)

# ---- 1) 백업 후 새 DB ----
if os.path.exists(DB):
    bk = os.path.join(DATA, "backups", "portal_%s.db" % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(DB, bk)
    for f in (DB, DB + "-wal", DB + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    print("백업:", bk)
from app import app  # noqa: E402  (init_db 실행)
from core import service  # noqa: E402
from core.autodispatch import plan as auto_plan  # noqa: E402
from core.db import execute, now, q, set_setting  # noqa: E402
from core.excel import parse_order_excel  # noqa: E402
from core.links import issue_link  # noqa: E402
from flask import g  # noqa: E402

con = sqlite3.connect(DB)
cur = con.cursor()
PW = generate_password_hash("demo1234")
T = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ins(table, rows):
    for r in rows:
        cur.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(r), ",".join("?" * len(r))), list(r.values()))


# ---- 2) 마스터 ----
cur.execute("UPDATE users SET login_id='1111', pw_hash=? WHERE role='admin'", (generate_password_hash("1111"),))
ins("users", [{"login_id": "center1", "name": "천안센터 담당", "pw_hash": PW, "role": "center", "center_code": "CA", "created_at": T},
              {"login_id": "sammi", "name": "SAMMI 배차담당", "pw_hash": PW, "role": "carrier", "created_at": T},
              {"login_id": "pg_user", "name": "P&G 물류담당", "pw_hash": PW, "role": "shipper", "created_at": T}])
ins("centers", [{"code": "CA", "name": "천안센터", "address": "충남 천안시 서북구 성환읍", "lat": 36.9150, "lng": 127.1280, "email": "cheonan@hanex.example"},
                {"code": "AS", "name": "안성센터", "address": "경기 안성시 원곡면", "lat": 37.0320, "lng": 127.1590, "email": "anseong@hanex.example"}])
ins("hubs", [{"code": "RDC-DJ", "name": "대전 RDC", "area_code": "A20", "address": "대전 유성구 대덕대로", "region": "충청", "contact": "042-000-1001", "email": "rdc.daejeon@partner.example", "lat": 36.3700, "lng": 127.3800},
             {"code": "RDC-GJ", "name": "광주 RDC", "area_code": "A40", "address": "광주 광산구 하남산단", "region": "호남", "contact": "062-000-1002", "email": "rdc.gwangju@partner.example", "lat": 35.1900, "lng": 126.8000},
             {"code": "RDC-BS", "name": "부산 RDC", "area_code": "A30", "address": "부산 강서구 미음산단", "region": "영남", "contact": "051-000-1003", "email": "rdc.busan@partner.example", "lat": 35.1800, "lng": 128.8700},
             {"code": "RDC-DG", "name": "대구 RDC", "area_code": "A50", "address": "대구 달성군 논공읍", "region": "영남", "contact": "053-000-1004", "email": "rdc.daegu@partner.example", "lat": 35.7000, "lng": 128.4500},
             {"code": "RDC-WJ", "name": "원주 RDC", "area_code": "A60", "address": "강원 원주시 문막읍", "region": "강원", "contact": "033-000-1005", "email": "rdc.wonju@partner.example", "lat": 37.3200, "lng": 127.8300}])
con.commit()
hub = {r[1]: r[0] for r in cur.execute("SELECT id, code FROM hubs")}
ins("drivers", [{"name": "김민수", "phone": "010-2001-1001", "company": "한익(자차)"}, {"name": "이정호", "phone": "010-2002-1002", "company": "한익(자차)"},
                {"name": "박성진", "phone": "010-2003-1003", "company": "현대글로비스"}, {"name": "최우람", "phone": "010-2004-1004", "company": "현대글로비스"},
                {"name": "정다운", "phone": "010-2005-1005", "company": "SAMMI"}, {"name": "한지훈", "phone": "010-2006-1006", "company": "용차"},
                {"name": "오세훈", "phone": "010-2007-1007", "company": "현대글로비스"}, {"name": "윤재호", "phone": "010-2008-1008", "company": "현대글로비스"}])
con.commit()
drv = {r[1]: r[0] for r in cur.execute("SELECT id, name FROM drivers")}
ins("vehicles", [{"plate": "80가1001", "ton": "5톤", "capacity_plt": 12, "capacity_cbm": 28, "owner": "자차", "carrier": "한익", "driver_id": drv["김민수"]},
                 {"plate": "80가1002", "ton": "5톤", "capacity_plt": 12, "capacity_cbm": 28, "owner": "자차", "carrier": "한익", "driver_id": drv["이정호"]},
                 {"plate": "81나2001", "ton": "11톤", "capacity_plt": 20, "capacity_cbm": 55, "owner": "용차", "carrier": "현대글로비스", "driver_id": drv["박성진"]},
                 {"plate": "81나2002", "ton": "11톤", "capacity_plt": 20, "capacity_cbm": 55, "owner": "용차", "carrier": "현대글로비스", "driver_id": drv["최우람"]},
                 {"plate": "81나2003", "ton": "11톤", "capacity_plt": 20, "capacity_cbm": 55, "owner": "용차", "carrier": "현대글로비스", "driver_id": drv["오세훈"]},
                 {"plate": "81나2004", "ton": "11톤", "capacity_plt": 20, "capacity_cbm": 55, "owner": "용차", "carrier": "현대글로비스", "driver_id": drv["윤재호"]},
                 {"plate": "82다3001", "ton": "2.5톤", "capacity_plt": 6, "capacity_cbm": 14, "owner": "용차", "carrier": "SAMMI", "driver_id": drv["정다운"]},
                 {"plate": "83라4001", "ton": "3.5톤", "capacity_plt": 8, "capacity_cbm": 18, "owner": "용차", "carrier": "용차", "driver_id": drv["한지훈"]}])
# 납품처 20 (수도권=안성센터 직송권 A10/A11, 중부·남부=천안센터 RDC권)
STORES = [
    ("S1001", "이마트 성수점", "0101001", "서울 성동구 뚝섬로 379", "02-460-1001", "A10", "수도권", "AS", None, 37.5445, 127.0560),
    ("S1002", "홈플러스 안양점", "0101002", "경기 안양시 만안구 안양로 180", "031-460-1002", "A10", "수도권", "AS", None, 37.3899, 126.9507),
    ("S1003", "코스트코 광명점", "0101003", "경기 광명시 일직로 17", "02-460-1003", "A10", "수도권", "AS", None, 37.4229, 126.8808),
    ("S1004", "롯데마트 잠실점", "0101004", "서울 송파구 올림픽로 240", "02-460-1004", "A10", "수도권", "AS", None, 37.5113, 127.0980),
    ("S1005", "이마트 수원점", "0101005", "경기 수원시 권선구 경수대로 270", "031-460-1005", "A11", "수도권", "AS", None, 37.2610, 127.0300),
    ("S1006", "홈플러스 인천송도점", "0101006", "인천 연수구 송도국제대로 123", "032-460-1006", "A11", "수도권", "AS", None, 37.3900, 126.6400),
    ("S1007", "이마트 일산점", "0101007", "경기 고양시 일산서구 중앙로 1393", "031-460-1007", "A10", "수도권", "AS", None, 37.6700, 126.7600),
    ("S1008", "코스트코 하남점", "0101008", "경기 하남시 미사대로 540", "031-460-1008", "A10", "수도권", "AS", None, 37.5600, 127.1900),
    ("S1009", "롯데마트 구리점", "0101009", "경기 구리시 경춘로 184", "031-460-1009", "A10", "수도권", "AS", None, 37.5990, 127.1400),
    ("S1010", "이마트 용인흥덕점", "0101010", "경기 용인시 기흥구 흥덕1로 13", "031-460-1010", "A11", "수도권", "AS", None, 37.2800, 127.0800),
    ("S2001", "롯데마트 대전노은점", "0102001", "대전 유성구 노은동로 33", "042-460-2001", "A20", "충청", "CA", "RDC-DJ", 36.3730, 127.3160),
    ("S2002", "이마트 천안점", "0102002", "충남 천안시 동남구 만남로 43", "041-460-2002", "A20", "충청", "CA", "RDC-DJ", 36.8060, 127.1560),
    ("S2003", "홈플러스 청주점", "0102003", "충북 청주시 흥덕구 2순환로 1215", "043-460-2003", "A20", "충청", "CA", "RDC-DJ", 36.6280, 127.4290),
    ("S4001", "이마트 광주점", "0104001", "광주 서구 상무중앙로 7", "062-460-4001", "A40", "호남", "CA", "RDC-GJ", 35.1520, 126.8470),
    ("S4002", "롯데마트 전주점", "0104002", "전북 전주시 완산구 온고을로 2", "063-460-4002", "A40", "호남", "CA", "RDC-GJ", 35.8120, 127.1120),
    ("S3001", "홈플러스 해운대점", "0103001", "부산 해운대구 해운대로 813", "051-460-3001", "A30", "영남", "CA", "RDC-BS", 35.1700, 129.1750),
    ("S3002", "코스트코 울산점", "0103002", "울산 북구 진장유통로 78", "052-460-3002", "A30", "영남", "CA", "RDC-BS", 35.5730, 129.3600),
    ("S5001", "이마트 대구만촌점", "0105001", "대구 수성구 동원로 136", "053-460-5001", "A50", "영남", "CA", "RDC-DG", 35.8640, 128.6460),
    ("S6001", "이마트 강릉점", "0106001", "강원 강릉시 경강로 2120", "033-460-6001", "A60", "강원", "CA", "RDC-WJ", 37.7640, 128.8990),
    ("S6002", "롯데마트 원주점", "0106002", "강원 원주시 북원로 2630", "033-460-6002", "A60", "강원", "CA", "RDC-WJ", 37.3610, 127.9270),
]
ins("stores", [{"pg_code": c, "name": n, "gaon_code": gc, "address": ad, "phone": ph, "area_code": ac, "region": rg, "center_code": cc, "hub_id": hub.get(hb),
                "delivery_type": "direct" if cc == "AS" else "hub", "lat": la, "lng": lo, "sms_opt": 1} for c, n, gc, ad, ph, ac, rg, cc, hb, la, lo in STORES])
ITEMS = [("80001", "다우니 섬유유연제 1.8L", "PG0001", 6, 48, 0.028, 11.5), ("80002", "페브리즈 상쾌한향 370ml", "PG0002", 12, 60, 0.018, 5.2),
         ("80003", "질레트 퓨전 프로글라이드 4입", "PG0003", 24, 100, 0.006, 1.4), ("80004", "팬틴 데일리 샴푸 680ml", "PG0004", 12, 40, 0.024, 9.0),
         ("80005", "오랄비 클린 칫솔 3입", "PG0005", 36, 100, 0.005, 1.1), ("80006", "헤드앤숄더 샴푸 850ml", "PG0006", 12, 40, 0.026, 11.0),
         ("80007", "타이드 세탁세제 2.5kg", "PG0007", 4, 36, 0.030, 10.5), ("80008", "브라운 면도기 리필 2입", "PG0008", 48, 120, 0.004, 0.9),
         ("80009", "SK-II 에센스 230ml", "PG0009", 12, 80, 0.008, 4.2), ("80010", "위스퍼 생리대 중형 18P", "PG0010", 8, 40, 0.022, 3.0),
         ("80011", "팸퍼스 기저귀 대형 56P", "PG0011", 3, 24, 0.045, 5.6), ("80012", "오랄비 치실 50m", "PG0012", 60, 120, 0.003, 0.6)]
ins("items", [{"pg_code": c, "name": n, "gaon_code": gc, "box_qty": bq, "plt_box": pb, "plt_qty": bq * pb, "cbm": cbm, "weight_kg": kg} for c, n, gc, bq, pb, cbm, kg in ITEMS])
con.commit()
con.close()

# ---- 3) 오더 엑셀 (SAP 형식) ----
def rows_for(date):
    R = []
    def add(po, st, it, qty, uom="EA"):
        srow = next(r for r in STORES if r[0] == st)
        name, area, cen = srow[1], srow[5], srow[7]
        R.append((po, st, name, date, it, next(n for c, n, *_ in ITEMS if c == it), qty, uom, area, "안성" if cen == "AS" else "천안"))
    # 수도권 직송권 (안성센터) — 대형마트 대량(직송), 소량 점포(기준 미만이지만 거점 없음 → 직송)
    add("PO26112601", "S1001", "80001", 288 * 6); add("PO26112601", "S1001", "80004", 480 * 2)          # 성수 7 PLT
    add("PO26112602", "S1004", "80007", 144 * 5); add("PO26112602", "S1004", "80002", 720 * 2)           # 잠실 7 PLT
    add("PO26112603", "S1003", "80006", 480 * 4); add("PO26112603", "S1003", "80011", 72 * 3)            # 광명 7 PLT
    add("PO26112604", "S1002", "80003", 2400); add("PO26112604", "S1002", "80005", 3600)                   # 안양 2 PLT
    add("PO26112605", "S1005", "80001", 288 * 3); add("PO26112605", "S1005", "80010", 320 * 2)           # 수원 5 PLT
    add("PO26112606", "S1006", "80008", 5760); add("PO26112606", "S1006", "80012", 7200)                   # 송도 2 PLT
    add("PO26112607", "S1007", "80004", 480 * 3); add("PO26112607", "S1007", "80009", 960)               # 일산 4 PLT
    add("PO26112608", "S1008", "80007", 144 * 8)                                                          # 하남 8 PLT
    add("PO26112609", "S1009", "80002", 720)                                                              # 구리 1 PLT
    add("PO26112610", "S1010", "80006", 480 * 2); add("PO26112610", "S1010", "80011", 72 * 2)            # 용인 4 PLT
    # 중부·남부 (천안센터) — 대형은 직송, 소량은 RDC
    add("PO26112611", "S2001", "80001", 288 * 4); add("PO26112611", "S2001", "80007", 144 * 2)           # 대전 6 PLT 직송
    add("PO26112612", "S2002", "80002", 720); add("PO26112612", "S2002", "80005", 3600)                    # 천안 2 PLT RDC
    add("PO26112613", "S2003", "80004", 480 * 2)                                                          # 청주 2 PLT RDC
    add("PO26112614", "S4001", "80006", 480 * 5)                                                          # 광주 5 PLT 직송
    add("PO26112615", "S4002", "80003", 2400); add("PO26112615", "S4002", "80010", 320)                    # 전주 2 PLT RDC
    add("PO26112616", "S3001", "80001", 288 * 2); add("PO26112616", "S3001", "80011", 72)                 # 해운대 3 PLT 직송(기준 3)
    add("PO26112617", "S3002", "80007", 144); add("PO26112617", "S3002", "80012", 7200)                    # 울산 2 PLT RDC
    add("PO26112618", "S5001", "80004", 480); add("PO26112618", "S5001", "80008", 5760)                    # 대구 2 PLT RDC
    add("PO26112619", "S6001", "80002", 720 * 2)                                                          # 강릉 2 PLT RDC
    add("PO26112620", "S6002", "80006", 480)                                                              # 원주 1 PLT RDC
    # 오류 시연: 미등록 점포 1건, 미등록 상품 1건
    R.append(("PO26112621", "S9999", "신규 점포(미등록)", date, "80001", "다우니 섬유유연제 1.8L", 288, "EA", "A10", "안성"))
    R.append(("PO26112622", "S1001", "이마트 성수점", date, "89999", "신제품(미등록)", 120, "EA", "A10", "안성"))
    return R


wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Order"
ws.append(["PO번호", "Ship-to", "Ship-to Name", "납품일", "Material", "Description", "Qty", "UOM", "AREA CODE", "출고센터"])
for r in rows_for(DATE):
    ws.append(list(r))
xpath = os.path.join(SAMPLE_DIR, "시연_PG오더_%s.xlsx" % DATE.replace("-", ""))
wb.save(xpath)
buf = io.BytesIO(); wb.save(buf)
# 11/27 추가 오더(다음날 시연용) 작은 파일
wb2 = openpyxl.Workbook(); ws2 = wb2.active; ws2.append(["PO번호", "Ship-to", "Ship-to Name", "납품일", "Material", "Description", "Qty", "UOM", "AREA CODE", "출고센터"])
nd = (dt.date.fromisoformat(DATE) + dt.timedelta(days=1)).isoformat()
for r in [("PO26112701", "S1005", "이마트 수원점", nd, "80001", "다우니 섬유유연제 1.8L", 1728, "EA", "A11", "안성"), ("PO26112702", "S2002", "이마트 천안점", nd, "80007", "타이드 세탁세제 2.5kg", 288, "EA", "A20", "천안"),
          ("PO26112703", "S3001", "홈플러스 해운대점", nd, "80004", "팬틴 데일리 샴푸 680ml", 960, "EA", "A30", "천안")]:
    ws2.append(list(r))
wb2.save(os.path.join(SAMPLE_DIR, "시연_PG오더_%s_추가.xlsx" % nd.replace("-", "")))

# ---- 4) 포털 로직으로 업로드·판정·가온·배차·기사·RDC·반품 ----
with app.app_context():
    with app.test_request_context():
        g.user = q("SELECT * FROM users WHERE role='admin'", one=True)
        g.link = None
        set_setting("sms_enabled", "1")
        set_setting("default_center_code", "AS"); set_setting("default_center_name", "안성센터")
        set_setting("gaon_market_code", "2200"); set_setting("gaon_user_id", "2508038")
        service.set_day_threshold(DATE, 3, "시연 기준 3 PLT")
        parsed = parse_order_excel(buf.getvalue(), q("SELECT value FROM settings WHERE key='excel_map'", one=True)["value"])
        rows, summary = service.validate_rows(parsed["rows"])
        batch_id, created, errored = service.import_rows(rows, os.path.basename(xpath), skip_errors=False)
        print("업로드: 오더 %d건, 오류 %d건" % (created, errored))
        # 가온 등록 시뮬레이션 (2건은 대기로 남김)
        orders = q("SELECT id, order_no, delivery_type FROM orders WHERE delivery_date=? AND status='received' ORDER BY id", (DATE,))
        for i, o in enumerate(orders):
            if o["order_no"] in ("PO26112619", "PO26112620"):
                continue
            service.mark_gaon(o["id"], True, "26112600%02d" % (i + 1), "OK(시연)", {"demo": True}, batch_id)
        # 자동배차: 안성센터 직송 → 11톤·5톤
        res = auto_plan(DATE, center="AS", include_hub=False)
        byv = {}
        for sid, vid in res["assign"]:
            byv.setdefault(vid, []).append(sid)
        for vid, sids in byv.items():
            v = q("SELECT driver_id FROM vehicles WHERE id=?", (vid,), one=True)
            service.assign(sids, vid, v["driver_id"], "자동배차")
            issue_link(v["driver_id"], vid, DATE)
        print("자동배차(안성 직송):", res["msg"])
        # 시연용: 용인흥덕점 1건은 미배차로 되돌려 둔다(자동배차·수동배차 시연)
        left = q("SELECT s.id, s.driver_id, s.vehicle_id FROM shipments s JOIN orders o ON o.id=s.order_id WHERE o.order_no='PO26112610' AND s.planned_date=?", (DATE,), one=True)
        if left:
            service.unassign([left["id"]])
        # 천안센터: 직송 3건(대전·광주·해운대)은 수동 배차 → 글로비스 11톤, RDC 간선은 SAMMI 2.5톤/용차 3.5톤에 배정 후 출고 완료
        def ship_ids(where, args):
            return [r["id"] for r in q("SELECT s.id FROM shipments s JOIN orders o ON o.id=s.order_id WHERE s.planned_date=? AND " + where, [DATE] + args)]
        v = {r["plate"]: r for r in q("SELECT * FROM vehicles")}
        # 천안센터 직송(대전·광주·해운대)도 자동배차 — 남은 용량이 있는 차량부터 채움
        res2 = auto_plan(DATE, center="CA", include_hub=False)
        byv2 = {}
        for sid, vid in res2["assign"]:
            byv2.setdefault(vid, []).append(sid)
        for vid, sids in byv2.items():
            vv = q("SELECT driver_id FROM vehicles WHERE id=?", (vid,), one=True)
            service.assign(sids, vid, vv["driver_id"], "자동배차")
            issue_link(vv["driver_id"], vid, DATE)
        print("자동배차(천안 직송):", res2["msg"])
        # RDC 오더는 분리까지만(운송 건 없음)
        # 기사 진행: 80가1001 첫 두 건 → 1건 배송완료(인수증), 1건 점포 도착 / 81나2001 → 센터 출발(이동 중, ETA) / 80가1002 → 센터 도착
        def legs_of(plate):
            return q("SELECT s.id, o.store_id, st.lat, st.lng, o.store_name FROM shipments s JOIN orders o ON o.id=s.order_id JOIN stores st ON st.id=o.store_id "
                     "JOIN vehicles v ON v.id=s.vehicle_id WHERE v.plate=? AND s.planned_date=? AND o.delivery_type='direct' ORDER BY s.id", (plate, DATE))
        AS_LAT, AS_LNG = 37.0320, 127.1590
        busy = [r["plate"] for r in q("SELECT v.plate FROM shipments s JOIN orders o ON o.id=s.order_id JOIN vehicles v ON v.id=s.vehicle_id "
                                      "WHERE s.planned_date=? AND o.delivery_type='direct' AND o.center_code='AS' GROUP BY v.plate ORDER BY COUNT(*) DESC, v.plate", (DATE,))]
        for plate, mode in zip(busy[:3], ("done", "moving", "center")):
            legs = legs_of(plate)
            if not legs:
                continue
            l0 = legs[0]
            service.advance_shipment(l0["id"], "center", AS_LAT, AS_LNG, 8)
            if mode in ("done", "moving"):
                service.advance_shipment(l0["id"], "depart", AS_LAT + 0.002, AS_LNG + 0.001, 10)
            if mode == "done":
                service.advance_shipment(l0["id"], "arrive", l0["lat"] + 0.0012, l0["lng"] + 0.0008, 12)
                service.advance_shipment(l0["id"], "deliver", l0["lat"] + 0.0012, l0["lng"] + 0.0008, 12)
                execute("INSERT INTO pods(shipment_id,receiver,note,signed_at,user_id) VALUES(?,?,?,?,?)", (l0["id"], "김점장", "이상 없음", now(), None))
                if len(legs) > 1:
                    l1 = legs[1]
                    service.advance_shipment(l1["id"], "center", AS_LAT, AS_LNG, 8)
                    service.advance_shipment(l1["id"], "depart", AS_LAT + 0.002, AS_LNG + 0.001, 10)
                    service.advance_shipment(l1["id"], "arrive", l1["lat"] + 0.0010, l1["lng"] + 0.0006, 15)
        # 반품 4건 (등록 / 거점 안내 / SAMMI 배차 / 입고확인) + 지난달 정산 1건
        def ret(no, st, box, reason, status, pickup, **extra):
            s = q("SELECT * FROM stores WHERE pg_code=?", (st,), one=True)
            rid = execute("INSERT INTO returns(return_no,store_id,store_pg_code,store_name,address,hub_id,center_code,request_date,pickup_date,box,qty,reason,memo,status,created_by,created_at,updated_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (no, s["id"], s["pg_code"], s["name"], s["address"], service.resolve_hub(s), s["center_code"],
                                                               extra.get("req", DATE), pickup, box, box * 6, reason, extra.get("memo", ""), status, g.user["id"], now(), now()))
            execute("INSERT INTO return_items(return_id,item_id,item_pg_code,item_name,qty,box,reason) VALUES(?,?,?,?,?,?,?)",
                    (rid, q("SELECT id FROM items WHERE pg_code='80001'", one=True)["id"], "80001", "다우니 섬유유연제 1.8L", box * 6, box, reason))
            execute("INSERT INTO return_events(return_id,status,note,user_id,at) VALUES(?,?,?,?,?)", (rid, "registered", "반품 등록 (P&G 물류담당)", q("SELECT id FROM users WHERE login_id='pg_user'", one=True)["id"], now()))
            for k, val in extra.items():
                if k in ("mailed_at", "mail_to", "vehicle_id", "driver_id", "carrier", "assigned_at", "collected_at", "received_at", "settled_month"):
                    execute("UPDATE returns SET %s=? WHERE id=?" % k, (val, rid))
            if status != "registered":
                execute("INSERT INTO return_events(return_id,status,note,user_id,at) VALUES(?,?,?,?,?)", (rid, status, extra.get("note", ""), g.user["id"], now()))
            return rid
        ret("R261125-001", "S2002", 12, "유통기한", "registered", DATE, req=(dt.date.fromisoformat(DATE) - dt.timedelta(days=1)).isoformat())
        ret("R261125-002", "S2001", 8, "파손", "mailed", DATE, req=(dt.date.fromisoformat(DATE) - dt.timedelta(days=1)).isoformat(), mailed_at=now(), mail_to="rdc.daejeon@partner.example", note="거점 안내 메일 생성 → rdc.daejeon@partner.example")
        execute("INSERT INTO notifications(kind,channel,to_addr,subject,body,ref_type,ref_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("return_mail", "mail", "rdc.daejeon@partner.example", "[한익스프레스] 대전 RDC 반품 회수 안내 (1건)",
                 "대전 RDC 담당자님,\n\n아래 반품 건의 회수를 안내드립니다. (한익스프레스 TPL2팀)\n\n- R261125-002 | 롯데마트 대전노은점(S2001) | 희망 회수일 %s | 8.0 BOX | 파손\n\n회수 일정과 차량은 운송사(SAMMI) 배차 후 다시 안내드립니다.\n\n감사합니다." % DATE,
                 "return", 2, "queued", now()))
        ret("R261124-003", "S3001", 15, "오배송", "assigned", DATE, req=(dt.date.fromisoformat(DATE) - dt.timedelta(days=2)).isoformat(), vehicle_id=v["82다3001"]["id"], driver_id=v["82다3001"]["driver_id"], carrier="SAMMI", assigned_at=now(), note="반품 배차 82다3001 정다운 SAMMI")
        ret("R261120-004", "S4001", 20, "과납", "received", (dt.date.fromisoformat(DATE) - dt.timedelta(days=3)).isoformat(), req=(dt.date.fromisoformat(DATE) - dt.timedelta(days=6)).isoformat(),
            vehicle_id=v["82다3001"]["id"], driver_id=v["82다3001"]["driver_id"], carrier="SAMMI", assigned_at=now(), collected_at=now(), received_at=now(), note="센터 입고확인")
        lm = (dt.date.fromisoformat(DATE).replace(day=1) - dt.timedelta(days=1))
        for i, (st, box, rs) in enumerate([("S2003", 10, "유통기한"), ("S5001", 6, "파손"), ("S6001", 9, "점포 요청")]):
            ret("R%s-%03d" % (lm.strftime("%y%m%d"), i + 5), st, box, rs, "settled", lm.isoformat(), req=lm.replace(day=10 + i).isoformat(), carrier="SAMMI",
                collected_at=now(), received_at=now(), settled_month=lm.strftime("%Y-%m"), note="%s 반품 정산 마감" % lm.strftime("%Y-%m"))
        print("반품 %d건" % q("SELECT COUNT(*) c FROM returns", one=True)["c"])
        print("오더 상태:", [tuple(r) for r in q("SELECT status, COUNT(*) FROM orders WHERE delivery_date=? GROUP BY status", (DATE,))])
        print("유형:", [tuple(r) for r in q("SELECT delivery_type, COUNT(*) FROM orders WHERE delivery_date=? GROUP BY delivery_type", (DATE,))])
        print("기사 링크:", [(r["plate"], r["token"]) for r in q("SELECT v.plate, l.token FROM driver_links l JOIN vehicles v ON v.id=l.vehicle_id WHERE l.revoked=0 AND l.date=?", (DATE,))])
print("완료. 샘플 엑셀:", xpath)
