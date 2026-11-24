"""관리자: 사용자·역할 관리, 설정(화주코드, 가온 계정, 엑셀 컬럼 매핑, 화주 업로드 허용)."""
import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from core.auth import role_required
from core.db import ROLE_MAP, execute, now, q, set_setting, setting
from core.excel import FIELD_LABEL

bp = Blueprint("admin", __name__, url_prefix="/admin")

ROUTE_FIELDS = [("route_mode", "직송/거점 판정 방식", "select", [("plt", "일자+점포 PLT 합계 기준"), ("store", "납품처 마스터 유형 고정")]),
                ("direct_plt_threshold", "직송 기준 PLT (합계가 이 값 이상이면 직송)", "number", None),
                ("route_hub_missing", "거점경유 판정인데 납품처에 거점이 없으면", "select", [("direct", "직송으로 처리"), ("error", "오류 오더로 보류")]),
                ("default_order_unit", "오더 수량 단위가 비어 있을 때", "select", [("EA", "EA(낱개)"), ("BOX", "BOX(박스)")])]
SETTING_FIELDS = [("shipper_name", "화주명"), ("gaon_market_code", "가온 화주코드"), ("gaon_user_id", "가온 로그인 ID(기본값)"),
                  ("default_center_code", "기본 출고센터 코드"), ("default_center_name", "기본 출고센터 이름"),
                  ("osrm_url", "경로 API(OSRM) 주소 — 비우면 직선거리 추정"), ("avg_speed_kmh", "추정 평균속도(km/h)"),
                  ("road_factor", "직선거리 도로계수"), ("arrive_radius_m", "점포 도착 검증 반경(m)"),
                  ("kakao_rest_key", "카카오 REST API 키 (주소→좌표 지오코딩)"), ("public_url", "외부 접속 주소(기사 링크용, https://…)"),
                  ("auto_fill_rate", "자동배차 적재 상한(비율, 예 0.95)"),
                  ("sms_enabled", "점포 SMS 알림 사용(1/0) ※ 단가·비용 확인 전 0"), ("sms_provider", "SMS 대행사(aligo 등)"), ("sms_api_key", "SMS API 키"), ("sms_sender", "SMS 발신번호"),
                  ("sms_depart_tpl", "SMS 출발 문구 ({store} {eta} 치환)"), ("sms_arrive_tpl", "SMS 도착 문구"),
                  ("smtp_host", "메일 SMTP 호스트(거점 반품 안내)"), ("smtp_port", "SMTP 포트"), ("smtp_user", "SMTP 사용자"), ("smtp_from", "보내는 메일 주소")]
JSON_FIELDS = [("gaon_upload_cols", "GAON 업로드 양식 컬럼 — [헤더, 필드] 목록. 필드: delivery_date store_gaon store_code store_name item_gaon item_code item_name qty box plt unit order_no center center_code dtype hub kind")]


@bp.route("/users")
@role_required("admin")
def users():
    rows = q("SELECT u.*, d.name driver_name FROM users u LEFT JOIN drivers d ON d.id=u.driver_id ORDER BY u.active DESC, u.role, u.id")
    drivers = q("SELECT * FROM drivers WHERE active=1 ORDER BY name")
    # 기사는 계정 없이 배차 링크(또는 차량번호+뒷자리)로 들어오므로 새 계정 역할에서 제외
    roles_select = {k: v for k, v in ROLE_MAP.items() if k != "driver"}
    return render_template("admin/users.html", rows=rows, drivers=drivers, roles=ROLE_MAP, roles_select=roles_select)


@bp.route("/users/save", methods=["POST"])
@role_required("admin")
def users_save():
    uid = request.form.get("id")
    login_id = request.form.get("login_id", "").strip()
    name = request.form.get("name", "").strip()
    role = request.form.get("role")
    pw = request.form.get("password", "")
    center = request.form.get("center_code", "").strip() or None
    driver_id = request.form.get("driver_id") or None
    if role not in ROLE_MAP or not login_id or not name:
        flash("아이디·이름·역할은 필수입니다.", "error")
        return redirect(url_for("admin.users"))
    try:
        if uid:
            execute("UPDATE users SET login_id=?, name=?, role=?, center_code=?, driver_id=? WHERE id=?", (login_id, name, role, center, driver_id, uid))
            if pw:
                execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(pw), uid))
        else:
            if len(pw) < 6:
                flash("비밀번호는 6자 이상이어야 합니다.", "error")
                return redirect(url_for("admin.users"))
            uid = execute("INSERT INTO users(login_id,name,pw_hash,role,center_code,driver_id,created_at) VALUES(?,?,?,?,?,?,?)",
                          (login_id, name, generate_password_hash(pw), role, center, driver_id, now()))
        if role == "driver" and driver_id:
            execute("UPDATE drivers SET user_id=? WHERE id=?", (uid, driver_id))
        flash("저장했습니다.", "ok")
    except Exception as e:
        flash("저장 실패: %s" % e, "error")
    return redirect(url_for("admin.users"))


@bp.route("/users/toggle/<int:uid>", methods=["POST"])
@role_required("admin")
def users_toggle(uid):
    execute("UPDATE users SET active=1-active WHERE id=?", (uid,))
    return redirect(url_for("admin.users"))


@bp.route("/settings", methods=["GET", "POST"])
@role_required("admin")
def settings():
    if request.method == "POST":
        for k, _ in SETTING_FIELDS:
            set_setting(k, request.form.get(k, "").strip())
        for k, _, _, _ in ROUTE_FIELDS:
            set_setting(k, request.form.get(k, "").strip())
        for k, _ in JSON_FIELDS:
            raw = request.form.get(k, "").strip()
            if raw:
                try:
                    set_setting(k, json.dumps(json.loads(raw), ensure_ascii=False))
                except ValueError:
                    flash("%s: JSON 형식이 잘못되어 저장하지 않았습니다." % k, "error")
        set_setting("shipper_can_upload", "1" if request.form.get("shipper_can_upload") else "0")
        emap = {}
        for f in FIELD_LABEL:
            names = [x.strip() for x in request.form.get("map_" + f, "").replace("\n", ",").split(",") if x.strip()]
            emap[f] = names
        set_setting("excel_map", json.dumps(emap, ensure_ascii=False))
        flash("설정을 저장했습니다.", "ok")
        return redirect(url_for("admin.settings"))
    emap = json.loads(setting("excel_map") or "{}")
    pending = q("SELECT COUNT(*) c FROM orders WHERE status IN ('received','gaon') AND store_id IS NOT NULL", one=True)["c"]
    return render_template("admin/settings.html", fields=SETTING_FIELDS, route_fields=ROUTE_FIELDS, json_fields=JSON_FIELDS, emap=emap, labels=FIELD_LABEL, pending=pending)


@bp.route("/notifications")
@role_required("admin")
def notifications():
    """SMS·메일 발송 큐. 비용 확인 전에는 대기(queued)로만 쌓인다."""
    rows = q("SELECT * FROM notifications ORDER BY id DESC LIMIT 300")
    counts = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM notifications GROUP BY status")}
    return render_template("admin/notifications.html", rows=rows, counts=counts)


@bp.route("/reroute", methods=["POST"])
@role_required("admin")
def reroute():
    """기준을 바꾼 뒤 배차 전 오더를 현재 규칙으로 다시 판정."""
    from core.service import reroute_all
    changed, groups = reroute_all(request.form.get("date_from") or None, request.form.get("date_to") or None)
    flash("일자+점포 %d묶음을 다시 판정해 %d건의 유형·운송구간을 바꿨습니다." % (groups, changed), "ok")
    return redirect(url_for("admin.settings"))
