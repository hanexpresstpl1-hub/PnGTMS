"""시연용 샘플 데이터: 거점 2, 차량 3, 기사 3, 납품처 6(직송 3·거점경유 3), 상품 5, 기사/화주 계정 + 샘플 오더 엑셀 생성.

사용: py tools/seed_demo.py   (서버가 꺼진 상태에서, 또는 켜진 상태라도 무방)
비밀번호는 모두 `demo1234` (기사 driver1, 화주 pg_user). 운영 DB에는 쓰지 말 것.
"""
import datetime as dt
import os
import sqlite3
import sys

import openpyxl
from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DATA = os.environ.get("PGPORTAL_DATA") or os.path.join(BASE, "data")
DB = os.path.join(DATA, "portal.db")
SAMPLE_DIR = os.path.join(BASE, "예시파일")

if not os.path.exists(DB):
    from app import app  # noqa: F401  (init_db 실행)

con = sqlite3.connect(DB)
cur = con.cursor()


def ins(table, rows, key):
    for r in rows:
        if cur.execute("SELECT 1 FROM %s WHERE %s=?" % (table, key), (r[key],)).fetchone():
            continue
        cur.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(r), ",".join("?" * len(r))), list(r.values()))


ins("centers", [{"code": "CA", "name": "천안센터", "address": "충남 천안시", "lat": 36.815, "lng": 127.114},
                {"code": "AS", "name": "안성센터", "address": "경기 안성시", "lat": 37.008, "lng": 127.280}], "code")
ins("hubs", [{"code": "H01", "name": "대전 거점", "area_code": "A20", "address": "대전 유성구", "region": "충청", "email": "rdc-daejeon@example.com"},
             {"code": "H02", "name": "부산 거점", "area_code": "A30", "address": "부산 강서구", "region": "영남", "email": "rdc-busan@example.com"}], "code")
ins("drivers", [{"name": "김기사", "phone": "010-1111-2222", "company": "자차"},
                {"name": "이기사", "phone": "010-3333-4444", "company": "용차"},
                {"name": "박기사", "phone": "010-5555-6666", "company": "용차"}], "name")
con.commit()
drv = {r[1]: r[0] for r in cur.execute("SELECT id,name FROM drivers")}
hub = {r[1]: r[0] for r in cur.execute("SELECT id,code FROM hubs")}
ins("vehicles", [{"plate": "12가3456", "ton": "5톤", "capacity_plt": 12, "capacity_cbm": 28, "owner": "자차", "carrier": "한익", "driver_id": drv["김기사"]},
                 {"plate": "34나5678", "ton": "11톤", "capacity_plt": 20, "capacity_cbm": 55, "owner": "용차", "carrier": "현대글로비스", "driver_id": drv["이기사"]},
                 {"plate": "56다7890", "ton": "2.5톤", "capacity_plt": 6, "capacity_cbm": 14, "owner": "용차", "carrier": "SAMMI", "driver_id": drv["박기사"]}], "plate")
ins("stores", [
    {"pg_code": "S1001", "name": "이마트 성수", "gaon_code": "0001001", "address": "서울 성동구", "phone": "02-1111-2222", "area_code": "A10", "region": "수도권", "center_code": "AS", "delivery_type": "direct", "lat": 37.5445, "lng": 127.0560},
    {"pg_code": "S1002", "name": "홈플러스 안양", "gaon_code": "0001002", "address": "경기 안양시", "phone": "031-111-2222", "area_code": "A10", "region": "수도권", "center_code": "AS", "delivery_type": "direct", "lat": 37.3899, "lng": 126.9507},
    {"pg_code": "S1003", "name": "코스트코 광명", "gaon_code": "0001003", "address": "경기 광명시", "phone": "02-3333-4444", "area_code": "A10", "region": "수도권", "center_code": "AS", "delivery_type": "direct", "lat": 37.4229, "lng": 126.8808},
    {"pg_code": "S2001", "name": "롯데마트 대전", "gaon_code": "0002001", "address": "대전 서구", "phone": "042-111-2222", "area_code": "A20", "region": "충청", "center_code": "CA", "delivery_type": "hub", "hub_id": hub["H01"]},
    {"pg_code": "S2002", "name": "이마트 천안", "gaon_code": "0002002", "address": "충남 천안시", "phone": "041-111-2222", "area_code": "A20", "region": "충청", "center_code": "CA", "delivery_type": "hub", "hub_id": hub["H01"]},
    {"pg_code": "S3001", "name": "홈플러스 해운대", "gaon_code": "0003001", "address": "부산 해운대구", "phone": "051-111-2222", "area_code": "A30", "region": "영남", "center_code": "CA", "delivery_type": "hub", "hub_id": hub["H02"]},
], "pg_code")
ins("items", [
    {"pg_code": "80001", "name": "다우니 1.8L", "gaon_code": "PG0001", "box_qty": 6, "plt_qty": 288, "cbm": 0.028, "weight_kg": 11.5},
    {"pg_code": "80002", "name": "페브리즈 370ml", "gaon_code": "PG0002", "box_qty": 12, "plt_qty": 720, "cbm": 0.018, "weight_kg": 5.2},
    {"pg_code": "80003", "name": "질레트 퓨전 4입", "gaon_code": "PG0003", "box_qty": 24, "plt_qty": 2400, "cbm": 0.006, "weight_kg": 1.4},
    {"pg_code": "80004", "name": "팬틴 샴푸 680ml", "gaon_code": "PG0004", "box_qty": 12, "plt_qty": 480, "cbm": 0.024, "weight_kg": 9.0},
    {"pg_code": "80005", "name": "오랄비 칫솔 3입", "gaon_code": "PG0005", "box_qty": 36, "plt_qty": 3600, "cbm": 0.005, "weight_kg": 1.1},
], "pg_code")
pw = generate_password_hash("demo1234")
now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
ins("users", [{"login_id": "driver1", "name": "김기사", "pw_hash": pw, "role": "driver", "driver_id": drv["김기사"], "created_at": now},
              {"login_id": "pg_user", "name": "P&G 담당자", "pw_hash": pw, "role": "shipper", "created_at": now},
              {"login_id": "center1", "name": "센터 담당", "pw_hash": pw, "role": "center", "created_at": now}], "login_id")
cur.execute("UPDATE drivers SET user_id=(SELECT id FROM users WHERE login_id='driver1') WHERE name='김기사'")
con.commit()
con.close()

# 샘플 오더 엑셀 (납품일 = 내일, 미등록 1건 포함)
os.makedirs(SAMPLE_DIR, exist_ok=True)
d = (dt.date.today() + dt.timedelta(days=1)).strftime("%Y-%m-%d")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Order"
ws.append(["P&G 오더 (샘플)"])
ws.append([])
ws.append(["PO번호", "Ship-to", "Ship-to Name", "납품일", "Material", "Description", "Qty", "UOM"])
rows = [
    ("PO2611001", "S1001", "이마트 성수", d, "80001", "다우니 1.8L", 120, "EA"),
    ("PO2611001", "S1001", "이마트 성수", d, "80002", "페브리즈 370ml", 240, "EA"),
    ("PO2611002", "S1002", "홈플러스 안양", d, "80003", "질레트 퓨전 4입", 48, "EA"),
    ("PO2611003", "S1003", "코스트코 광명", d, "80001", "다우니 1.8L", 576, "EA"),
    ("PO2611004", "S2001", "롯데마트 대전", d, "80004", "팬틴 샴푸 680ml", 96, "EA"),
    ("PO2611004", "S2001", "롯데마트 대전", d, "80005", "오랄비 칫솔 3입", 72, "EA"),
    ("PO2611005", "S2002", "이마트 천안", d, "80002", "페브리즈 370ml", 360, "EA"),
    ("PO2611006", "S3001", "홈플러스 해운대", d, "80001", "다우니 1.8L", 288, "EA"),
    ("PO2611007", "S9999", "미등록 점포", d, "80001", "다우니 1.8L", 12, "EA"),
    ("PO2611008", "S1002", "홈플러스 안양", d, "89999", "미등록 상품", 10, "EA"),
]
for r in rows:
    ws.append(list(r))
path = os.path.join(SAMPLE_DIR, "PG_오더_샘플.xlsx")
wb.save(path)
print("샘플 데이터 생성 완료. 샘플 엑셀:", path)
