"""기사 접속 링크·예비 로그인.

- 배차 때 (기사, 차량, 납품일) 단위로 토큰 링크를 발급한다. 같은 조합이면 기존 유효 링크를 재사용.
- 링크는 납품일 + link_valid_days 까지 유효, 처음 연 기기(쿠키 pgd)에 묶인다. 재발급하면 이전 링크는 무효.
- 예비 로그인: 차량번호 + 휴대폰 뒷 4자리 → 전후 1일 안에 배차가 있으면 같은 링크를 만들어 세션에 넣는다. 5회 실패 시 10분 잠금.
"""
import datetime as dt
import re
import secrets

from flask import g, request

from .db import execute, now, q, setting

LOCK_AFTER, LOCK_MIN = 5, 10


def norm_plate(p):
    return re.sub(r"[\s\-·.]", "", p or "").upper()


def digits(p):
    return re.sub(r"\D", "", p or "")


def get_or_create_vehicle(plate):
    np_ = norm_plate(plate)
    if not np_:
        return None
    for v in q("SELECT * FROM vehicles"):
        if norm_plate(v["plate"]) == np_:
            if not v["active"]:
                execute("UPDATE vehicles SET active=1 WHERE id=?", (v["id"],))
            return v["id"]
    return execute("INSERT INTO vehicles(plate, owner, active) VALUES(?,?,1)", (plate.strip(), "용차"))


def get_or_create_driver(name, phone):
    name, ph = (name or "").strip(), digits(phone)
    if not name and not ph:
        return None
    if ph:
        for d in q("SELECT * FROM drivers"):
            if digits(d["phone"]) == ph:
                if name and d["name"] != name:
                    execute("UPDATE drivers SET name=? WHERE id=?", (name, d["id"]))
                if not d["active"]:
                    execute("UPDATE drivers SET active=1 WHERE id=?", (d["id"],))
                return d["id"]
    elif name:
        d = q("SELECT * FROM drivers WHERE name=? AND active=1 ORDER BY id DESC", (name,), one=True)
        if d:
            return d["id"]
    return execute("INSERT INTO drivers(name, phone, company, active) VALUES(?,?,?,1)", (name or ("기사 " + ph[-4:]), phone.strip() if phone else None, "용차"))


def _expires(date):
    try:
        days = int(setting("link_valid_days") or 1)
    except ValueError:
        days = 1
    d = dt.datetime.strptime(date, "%Y-%m-%d") + dt.timedelta(days=days)
    return d.strftime("%Y-%m-%d 23:59:59")


def active_link(driver_id, vehicle_id, date):
    return q("SELECT * FROM driver_links WHERE driver_id=? AND vehicle_id=? AND date=? AND revoked=0 AND expires_at>=? ORDER BY id DESC",
             (driver_id, vehicle_id, date, now()), one=True)


def issue_link(driver_id, vehicle_id, date, renew=False, via="link"):
    """유효 링크가 있으면 그것을, 없거나 renew면 새로 발급."""
    cur = active_link(driver_id, vehicle_id, date)
    if cur and not renew:
        return cur
    if cur:
        execute("UPDATE driver_links SET revoked=1 WHERE id=?", (cur["id"],))
    token = secrets.token_urlsafe(12)
    uid = g.user["id"] if g.get("user") else None
    lid = execute("INSERT INTO driver_links(token,driver_id,vehicle_id,date,created_at,expires_at,created_by,via) VALUES(?,?,?,?,?,?,?,?)",
                  (token, driver_id, vehicle_id, date, now(), _expires(date), uid, via))
    return q("SELECT * FROM driver_links WHERE id=?", (lid,), one=True)


def revoke_if_unused(driver_id, vehicle_id, date):
    """배차 해제 후 그 조합에 남은 배차가 없으면 링크 무효화."""
    left = q("SELECT 1 FROM shipments WHERE driver_id=? AND vehicle_id=? AND planned_date=? AND status!='cancelled' LIMIT 1", (driver_id, vehicle_id, date), one=True)
    if not left:
        execute("UPDATE driver_links SET revoked=1 WHERE driver_id=? AND vehicle_id=? AND date=? AND revoked=0", (driver_id, vehicle_id, date))


def link_url(token):
    base = (setting("public_url") or "").strip().rstrip("/") or request.host_url.rstrip("/")
    return "%s/d/%s" % (base, token)


def open_link(token, device_id):
    """토큰 검증 + 기기 묶기. 반환 (row, error) error ∈ None|'notfound'|'revoked'|'expired'|'device'"""
    row = q("SELECT * FROM driver_links WHERE token=?", (token,), one=True)
    if not row:
        return None, "notfound"
    if row["revoked"]:
        return None, "revoked"
    if row["expires_at"] < now():
        return None, "expired"
    if row["device_id"] and device_id and row["device_id"] != device_id:
        return None, "device"
    execute("UPDATE driver_links SET device_id=COALESCE(device_id,?), first_open_at=COALESCE(first_open_at,?), last_open_at=?, open_count=open_count+1 WHERE id=?",
            (device_id, now(), now(), row["id"]))
    return q("SELECT * FROM driver_links WHERE id=?", (row["id"],), one=True), None


def current_link(link_id):
    """세션의 링크가 아직 유효한지. 기사·차량 정보 포함."""
    return q("SELECT l.*, d.name driver_name, d.phone driver_phone, v.plate FROM driver_links l JOIN drivers d ON d.id=l.driver_id JOIN vehicles v ON v.id=l.vehicle_id "
             "WHERE l.id=? AND l.revoked=0 AND l.expires_at>=?", (link_id, now()), one=True)


# ---- 예비 로그인(차량번호 + 휴대폰 뒷자리) ----
def lock_state(key):
    r = q("SELECT * FROM login_attempts WHERE key=?", (key,), one=True)
    if r and r["locked_until"] and r["locked_until"] > now():
        return r["locked_until"]
    return None


def record_fail(key):
    r = q("SELECT * FROM login_attempts WHERE key=?", (key,), one=True)
    cnt = (r["count"] if r and not (r["locked_until"] and r["locked_until"] < now()) else 0) + 1
    locked = (dt.datetime.now() + dt.timedelta(minutes=LOCK_MIN)).strftime("%Y-%m-%d %H:%M:%S") if cnt >= LOCK_AFTER else None
    execute("INSERT INTO login_attempts(key,count,locked_until,updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET count=excluded.count, locked_until=excluded.locked_until, updated_at=excluded.updated_at",
            (key, 0 if locked else cnt, locked, now()))
    return LOCK_AFTER - cnt if not locked else 0


def clear_fail(key):
    execute("DELETE FROM login_attempts WHERE key=?", (key,))


def fallback_login(plate, last4):
    """반환 (link_row, error). 전후 1일 배차 중 오늘에 가장 가까운 날짜를 고른다."""
    np_, l4 = norm_plate(plate), digits(last4)[-4:]
    if not np_ or len(l4) != 4:
        return None, "차량번호와 휴대폰 뒷 4자리를 입력하세요."
    key = "plate:" + np_
    lu = lock_state(key)
    if lu:
        return None, "실패가 잦아 %s까지 잠겼습니다." % lu[11:16]
    today = dt.date.today()
    d0, d1 = (today - dt.timedelta(days=1)).isoformat(), (today + dt.timedelta(days=1)).isoformat()
    rows = q("SELECT s.planned_date, s.driver_id, s.vehicle_id, v.plate, d.phone FROM shipments s JOIN vehicles v ON v.id=s.vehicle_id JOIN drivers d ON d.id=s.driver_id "
             "JOIN orders o ON o.id=s.order_id WHERE s.planned_date BETWEEN ? AND ? AND s.status!='cancelled' AND o.delivery_type='direct'", (d0, d1))
    cands = [r for r in rows if norm_plate(r["plate"]) == np_ and digits(r["phone"]).endswith(l4)]
    if not cands:
        left = record_fail(key)
        return None, "해당 차량의 배차를 찾지 못했습니다. 차량번호·뒷자리를 확인하세요." + (" (남은 시도 %d회)" % left if left else " 10분간 잠깁니다.")
    cands.sort(key=lambda r: abs((dt.date.fromisoformat(r["planned_date"]) - today).days))
    c = cands[0]
    clear_fail(key)
    return issue_link(c["driver_id"], c["vehicle_id"], c["planned_date"], via="plate"), None
