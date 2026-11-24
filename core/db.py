"""SQLite 연결·스키마·초기 데이터.

테이블 개요
  users            사용자(역할: admin/center/driver/shipper)
  settings         키-값 설정(엑셀 컬럼 매핑, 가온 계정 등)
  hubs             거점센터
  vehicles/drivers 차량·기사
  stores/items     납품처·상품 마스터 (P&G 코드 ↔ 가온 코드 매핑, 직송/거점 구분)
  upload_batches   엑셀 업로드 단위
  orders/order_items/order_events  오더 헤더·상품·상태 이력
  gaon_jobs        가온 수주등록 요청·응답 로그
  shipments        운송 구간(직송=1구간, 거점경유=간선+거점배송 2구간)
  pods             인수증
"""
import datetime as dt
import json
import sqlite3

from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, login_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','center','driver','shipper')),
  center_code TEXT, driver_id INTEGER, active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS hubs (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT NOT NULL, address TEXT, region TEXT, contact TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS drivers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, company TEXT, user_id INTEGER, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS vehicles (
  id INTEGER PRIMARY KEY, plate TEXT UNIQUE NOT NULL, ton TEXT, capacity_plt REAL, owner TEXT, driver_id INTEGER, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY, pg_code TEXT UNIQUE, name TEXT NOT NULL, gaon_code TEXT, address TEXT, region TEXT,
  delivery_type TEXT DEFAULT 'direct' CHECK(delivery_type IN ('direct','hub')), hub_id INTEGER, center_code TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, pg_code TEXT UNIQUE, name TEXT NOT NULL, gaon_code TEXT, box_qty REAL, plt_qty REAL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS upload_batches (
  id INTEGER PRIMARY KEY, filename TEXT, uploaded_by INTEGER, uploaded_at TEXT, total INTEGER DEFAULT 0, ok INTEGER DEFAULT 0,
  error INTEGER DEFAULT 0, status TEXT DEFAULT 'confirmed', note TEXT);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, order_no TEXT, batch_id INTEGER, store_id INTEGER, store_pg_code TEXT, store_name TEXT,
  address TEXT, delivery_date TEXT, delivery_type TEXT DEFAULT 'direct', hub_id INTEGER, center_code TEXT,
  status TEXT DEFAULT 'received', gaon_slip_no TEXT, gaon_status TEXT DEFAULT 'pending', gaon_msg TEXT,
  error_msg TEXT, memo TEXT, line_cnt INTEGER DEFAULT 0, qty REAL DEFAULT 0, box REAL DEFAULT 0, plt REAL DEFAULT 0,
  created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_orders_date ON orders(delivery_date);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, item_id INTEGER, item_pg_code TEXT, item_name TEXT,
  qty REAL DEFAULT 0, unit TEXT, box REAL, plt REAL, error_msg TEXT);
CREATE INDEX IF NOT EXISTS ix_oi_order ON order_items(order_id);
CREATE TABLE IF NOT EXISTS order_events (
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, status TEXT, note TEXT, user_id INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS gaon_jobs (
  id INTEGER PRIMARY KEY, batch_id INTEGER, order_id INTEGER, kind TEXT, requested_at TEXT, finished_at TEXT,
  ok INTEGER, message TEXT, payload TEXT, user_id INTEGER);
CREATE TABLE IF NOT EXISTS shipments (
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, leg INTEGER DEFAULT 1, from_name TEXT, to_name TEXT, hub_id INTEGER,
  planned_date TEXT, vehicle_id INTEGER, driver_id INTEGER, seq INTEGER,
  status TEXT DEFAULT 'planned' CHECK(status IN ('planned','assigned','at_center','departed','arrived','delivered','cancelled')),
  assigned_at TEXT, center_arrived_at TEXT, departed_at TEXT, arrived_at TEXT, delivered_at TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_ship_order ON shipments(order_id);
CREATE INDEX IF NOT EXISTS ix_ship_date ON shipments(planned_date);
CREATE TABLE IF NOT EXISTS centers (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, address TEXT, lat REAL, lng REAL, email TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS returns (
  id INTEGER PRIMARY KEY, return_no TEXT, store_id INTEGER, store_pg_code TEXT, store_name TEXT, address TEXT, hub_id INTEGER, center_code TEXT,
  request_date TEXT, pickup_date TEXT, box REAL DEFAULT 0, qty REAL DEFAULT 0, reason TEXT, memo TEXT,
  status TEXT DEFAULT 'registered', mailed_at TEXT, mail_to TEXT, vehicle_id INTEGER, driver_id INTEGER, carrier TEXT, assigned_at TEXT,
  collected_at TEXT, received_at TEXT, settled_month TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_returns_date ON returns(request_date);
CREATE TABLE IF NOT EXISTS return_items (
  id INTEGER PRIMARY KEY, return_id INTEGER NOT NULL, item_id INTEGER, item_pg_code TEXT, item_name TEXT, qty REAL DEFAULT 0, box REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS return_events (
  id INTEGER PRIMARY KEY, return_id INTEGER NOT NULL, status TEXT, note TEXT, user_id INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY, kind TEXT, channel TEXT, to_addr TEXT, to_name TEXT, subject TEXT, body TEXT, ref_type TEXT, ref_id INTEGER,
  status TEXT DEFAULT 'queued', created_at TEXT, sent_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS pods (
  id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL, receiver TEXT, photo_path TEXT, note TEXT, signed_at TEXT, user_id INTEGER);
"""

DEFAULT_SETTINGS = {
    "shipper_name": "P&G",
    "company": "한익스프레스",
    "team": "TPL2팀",
    "gaon_market_code": "",          # P&G 가온 화주코드 (미확정)
    "gaon_user_id": "",
    "default_center_code": "",
    "default_center_name": "한익 센터",
    "shipper_can_upload": "0",       # P&G 담당자가 포털에서 직접 업로드 허용 여부
    # 기사 GPS·예상도착: OSRM 경로 API(없거나 실패하면 직선거리×도로계수/평균속도로 추정)
    "osrm_url": "https://router.project-osrm.org",
    "avg_speed_kmh": "40",
    "road_factor": "1.3",
    "arrive_radius_m": "500",        # 점포 도착 검증 반경
    "kakao_rest_key": "",            # 납품처·거점 주소 → 좌표 (카카오 로컬 REST API 키, 관리 > 설정에서 입력)
    # 직송/거점 판정: route_mode = plt(일자+점포 PLT 합계 기준) | store(납품처 마스터 유형 고정)
    "route_mode": "plt",
    "direct_plt_threshold": "3",     # 일자+점포 합계 PLT가 이 값 이상이면 직송
    "route_hub_missing": "direct",   # 거점경유 판정인데 납품처에 거점이 없을 때: direct(직송) | error(오류 오더)
    "default_order_unit": "EA",      # 오더 수량 단위가 비어 있을 때: EA | BOX
    "public_url": "",                # 기사 링크에 쓸 외부 주소(https://...). 비우면 현재 접속 주소 사용
    "link_valid_days": "1",          # 기사 링크 유효기간: 납품일 + N일
    # 엑셀 헤더 → 필드 매핑. 샘플 확정 전까지는 동의어로 자동 추정하고, 설정 화면에서 고정할 수 있음.
    "excel_map": json.dumps({
        "order_no": ["오더번호", "주문번호", "PO번호", "PO", "Order No", "주문No"],
        "store_code": ["납품처코드", "점포코드", "거래처코드", "Ship-to", "Ship To", "ShipTo", "고객코드"],
        "store_name": ["납품처명", "점포명", "거래처명", "Ship-to Name", "고객명"],
        "address": ["주소", "납품처주소", "배송지"],
        "delivery_date": ["납품일", "납기", "납품일자", "배송일", "Delivery Date", "RDD"],
        "item_code": ["상품코드", "품목코드", "SKU", "Material", "제품코드", "GTIN"],
        "item_name": ["상품명", "품목명", "제품명", "Description"],
        "qty": ["수량", "주문수량", "Qty", "Quantity", "출고수량"],
        "unit": ["단위", "Unit", "UOM"],
        "area_code": ["AREA CODE", "AREA", "지역코드", "AreaCode", "권역코드"],
        "plant": ["출고센터", "센터", "Plant", "플랜트", "출고창고", "창고"],
    }, ensure_ascii=False),
    # 출고센터: 오더에 센터 정보가 없을 때 납품처 마스터 → AREA CODE 매핑 → 기본값 순
    "centers_note": "천안·안성 2개 센터 (마스터 > 출고센터)",
    # 점포 SMS 알림(출발·도착): 단가·비용 확인 전까지 대기 큐에만 쌓음
    "auto_fill_rate": "0.95",         # 자동배차 적재 상한
    "sms_enabled": "0",
    "sms_provider": "",              # aligo 등 (키 입력 후 연결)
    "sms_api_key": "",
    "sms_user_id": "",
    "sms_sender": "",
    "sms_depart_tpl": "[한익스프레스] {store} P&G 배송 차량이 출발했습니다. 예상 도착 {eta}",
    "sms_arrive_tpl": "[한익스프레스] {store} P&G 배송 차량이 도착했습니다.",
    # 메일(거점 반품 안내): SMTP 미설정이면 본문만 생성해 복사
    "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_from": "",
    # GAON 업로드 양식 컬럼(HAR·양식 확정 전 기본값). JSON [[헤더, 필드], ...]
    "gaon_upload_cols": json.dumps([["납품일자", "delivery_date"], ["점포코드", "store_gaon"], ["점포명", "store_name"], ["상품코드", "item_gaon"],
                                    ["상품명", "item_name"], ["수량", "qty"], ["단위", "unit"], ["오더번호", "order_no"], ["출고센터", "center"], ["구분", "dtype"]], ensure_ascii=False),
}

ORDER_STATUS = [
    ("received", "접수"), ("error", "오류"), ("gaon", "가온등록"), ("dispatched", "배차"), ("center_arrived", "센터도착"),
    ("shipped", "센터출발"), ("store_arrived", "점포도착"), ("delivered", "배송완료"), ("cancelled", "취소"),
]
ORDER_STATUS_MAP = dict(ORDER_STATUS)
ORDER_STATUS_MAP["hub_arrived"] = "거점도착"  # 구버전 데이터 호환
# 운송 구간 상태: 기사 TMS 단계 (배차완료 → 센터도착 → 센터출발 → 점포도착 → 배송완료)
SHIP_STATUS_MAP = {"planned": "미배차", "assigned": "배차완료", "at_center": "센터도착", "departed": "센터출발", "arrived": "점포도착",
                   "delivered": "배송완료", "cancelled": "취소"}
SHIP_TIME_COL = {"at_center": "center_arrived_at", "departed": "departed_at", "arrived": "arrived_at", "delivered": "delivered_at"}
# 기사 화면 동작 → (다음 구간 상태, 버튼 이름)
DRIVER_ACTIONS = {"center": ("at_center", "센터 도착"), "depart": ("departed", "센터 출발"), "arrive": ("arrived", "점포 도착"), "deliver": ("delivered", "배송 완료")}
NEXT_ACTION = {"assigned": "center", "at_center": "depart", "departed": "arrive", "arrived": "deliver"}
ROLE_MAP = {"admin": "관리자(TPL2)", "center": "센터", "carrier": "운송사(SAMMI·글로비스)", "driver": "기사", "shipper": "화주(P&G)"}
DTYPE_LABEL = {"direct": "직송", "hub": "RDC(거점)"}
RETURN_STATUS = [("registered", "등록"), ("mailed", "거점 안내"), ("assigned", "배차"), ("collected", "회수완료"), ("received", "입고확인"), ("settled", "정산"), ("cancelled", "취소")]
RETURN_STATUS_MAP = dict(RETURN_STATUS)
DTYPE_MAP = {"direct": "직송", "hub": "RDC(거점)"}


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    if "db" not in g:
        con = sqlite3.connect(current_app.config["DB_PATH"])
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        g.db = con
    return g.db


def close_db(_e=None):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def q(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    con = get_db()
    cur = con.execute(sql, args)
    con.commit()
    return cur.lastrowid


def setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def set_setting(key, value):
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _migrate(con):
    """기존 DB 보정: shipments에 센터도착 상태·시각 추가(CHECK 제약 때문에 테이블 재생성), 구버전 2구간 데이터 정리."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(shipments)")]
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='shipments'").fetchone()[0]
    if "center_arrived_at" not in cols or "'at_center'" not in sql:
        con.executescript("""
        ALTER TABLE shipments RENAME TO shipments_old;
        CREATE TABLE shipments (
          id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, leg INTEGER DEFAULT 1, from_name TEXT, to_name TEXT, hub_id INTEGER,
          planned_date TEXT, vehicle_id INTEGER, driver_id INTEGER, seq INTEGER,
          status TEXT DEFAULT 'planned' CHECK(status IN ('planned','assigned','at_center','departed','arrived','delivered','cancelled')),
          assigned_at TEXT, center_arrived_at TEXT, departed_at TEXT, arrived_at TEXT, delivered_at TEXT, note TEXT);
        INSERT INTO shipments(id,order_id,leg,from_name,to_name,hub_id,planned_date,vehicle_id,driver_id,seq,status,assigned_at,departed_at,arrived_at,delivered_at,note)
          SELECT id,order_id,leg,from_name,to_name,hub_id,planned_date,vehicle_id,driver_id,seq,status,assigned_at,departed_at,arrived_at,delivered_at,note FROM shipments_old;
        DROP TABLE shipments_old;
        CREATE INDEX IF NOT EXISTS ix_ship_order ON shipments(order_id);
        CREATE INDEX IF NOT EXISTS ix_ship_date ON shipments(planned_date);
        """)
    con.execute("UPDATE orders SET status='delivered' WHERE status='hub_arrived'")
    usql = con.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
    if "'carrier'" not in usql:
        con.executescript("""
        ALTER TABLE users RENAME TO users_old;
        CREATE TABLE users (
          id INTEGER PRIMARY KEY, login_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','center','carrier','driver','shipper')),
          center_code TEXT, driver_id INTEGER, active INTEGER DEFAULT 1, created_at TEXT);
        INSERT INTO users SELECT id,login_id,name,pw_hash,role,center_code,driver_id,active,created_at FROM users_old;
        DROP TABLE users_old;""")
    # GPS·예상도착 컬럼
    for table, col, typ in (("stores", "lat", "REAL"), ("stores", "lng", "REAL"), ("hubs", "lat", "REAL"), ("hubs", "lng", "REAL"),
                            ("shipments", "eta_at", "TEXT"), ("shipments", "eta_min", "REAL"), ("shipments", "eta_km", "REAL"),
                            ("shipments", "last_lat", "REAL"), ("shipments", "last_lng", "REAL"), ("shipments", "last_at", "TEXT"),
                            ("shipments", "arrive_dist_m", "REAL"),
                            ("stores", "area_code", "TEXT"), ("stores", "phone", "TEXT"), ("stores", "sms_opt", "INTEGER"),
                            ("hubs", "area_code", "TEXT"), ("hubs", "email", "TEXT"),
                            ("items", "cbm", "REAL"), ("items", "weight_kg", "REAL"),
                            ("vehicles", "capacity_cbm", "REAL"), ("vehicles", "carrier", "TEXT"),
                            ("orders", "cbm", "REAL"), ("order_items", "cbm", "REAL"), ("shipments", "seq_auto", "INTEGER"),
                            ("stores", "route_fixed", "TEXT"),      # 강제 유형: direct/hub/NULL(규칙 적용)
                            ("items", "plt_box", "REAL"),           # BOX/PLT (EA/PLT가 없을 때 box_qty×plt_box)
                            ("orders", "route_note", "TEXT"),       # 판정 근거
                            ("orders", "route_manual", "TEXT")):    # 담당자 지정: direct/hub/NULL
        if col not in [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, typ))
    con.execute("""CREATE TABLE IF NOT EXISTS ship_logs (
      id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL, action TEXT, at TEXT, lat REAL, lng REAL, accuracy REAL,
      dist_km REAL, eta_min REAL, eta_source TEXT, user_id INTEGER)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_shiplog_ship ON ship_logs(shipment_id)")
    con.execute("CREATE TABLE IF NOT EXISTS geocode_cache (address TEXT PRIMARY KEY, lat REAL, lng REAL, matched TEXT, method TEXT, at TEXT)")
    # 기사 접속 링크(기사·차량·납품일 단위, 첫 기기에 묶임) + 예비 로그인 실패 잠금
    con.execute("""CREATE TABLE IF NOT EXISTS driver_links (
      id INTEGER PRIMARY KEY, token TEXT UNIQUE NOT NULL, driver_id INTEGER NOT NULL, vehicle_id INTEGER NOT NULL, date TEXT NOT NULL,
      created_at TEXT, expires_at TEXT, device_id TEXT, first_open_at TEXT, last_open_at TEXT, open_count INTEGER DEFAULT 0,
      revoked INTEGER DEFAULT 0, created_by INTEGER, via TEXT DEFAULT 'link')""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_links_dvd ON driver_links(driver_id, vehicle_id, date)")
    con.execute("CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, count INTEGER DEFAULT 0, locked_until TEXT, updated_at TEXT)")
    # 납품일별 직송 기준 PLT (담당자가 그날그날 조정). 없으면 설정 direct_plt_threshold 사용
    con.execute("CREATE TABLE IF NOT EXISTS route_day (date TEXT PRIMARY KEY, threshold REAL NOT NULL, note TEXT, updated_by INTEGER, updated_at TEXT)")


def init_db(app):
    """스키마 생성 + 기본 설정 + 초기 관리자(없을 때만). 초기 계정은 README 참고."""
    con = sqlite3.connect(app.config["DB_PATH"])
    con.executescript(SCHEMA)
    _migrate(con)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    if not con.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        con.execute("INSERT INTO users(login_id,name,pw_hash,role,created_at) VALUES(?,?,?,?,?)",
                    ("admin", "관리자", generate_password_hash("hanex1234"), "admin", now()))
    con.commit()
    con.close()
