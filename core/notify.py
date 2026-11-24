"""점포 SMS 알림(출발·도착). 단가·비용 확인 전에는 sms_enabled=0 으로 두고 큐에만 쌓는다.

sms_provider = aligo 이고 키가 있으면 실제 발송(https://apis.aligo.in/send/). 그 외는 queued 상태로 보관.
"""
import re

import requests

from .db import execute, now, q, setting


def _digits(p):
    return re.sub(r"\D", "", p or "")


def queue_sms(to_phone, to_name, body, ref_type=None, ref_id=None, kind="sms"):
    nid = execute("INSERT INTO notifications(kind,channel,to_addr,to_name,body,ref_type,ref_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (kind, "sms", _digits(to_phone), to_name, body, ref_type, ref_id, "queued", now()))
    if setting("sms_provider") == "aligo" and setting("sms_api_key") and setting("sms_user_id") and setting("sms_sender"):
        try:
            r = requests.post("https://apis.aligo.in/send/", data={"key": setting("sms_api_key"), "user_id": setting("sms_user_id"), "sender": _digits(setting("sms_sender")),
                                                                  "receiver": _digits(to_phone), "msg": body, "msg_type": "SMS" if len(body.encode("utf-8")) <= 90 else "LMS"}, timeout=8)
            j = r.json()
            if str(j.get("result_code")) == "1":
                execute("UPDATE notifications SET status='sent', sent_at=? WHERE id=?", (now(), nid))
                return True
            execute("UPDATE notifications SET status='failed', error=? WHERE id=?", (str(j.get("message"))[:200], nid))
        except Exception as e:
            execute("UPDATE notifications SET status='failed', error=? WHERE id=?", (str(e)[:200], nid))
    return False


def notify_store(shipment_id, action, eta_text=""):
    """기사 출발/도착 때 점포에 SMS. sms_enabled=1, 점포 연락처 있을 때만. 반환 안내문(없으면 '')"""
    if (setting("sms_enabled") or "0") != "1" or action not in ("depart", "arrive"):
        return ""
    s = q("SELECT s.id, o.store_name, st.phone, st.sms_opt FROM shipments s JOIN orders o ON o.id=s.order_id LEFT JOIN stores st ON st.id=o.store_id WHERE s.id=?", (shipment_id,), one=True)
    if not s or not s["phone"] or (s["sms_opt"] is not None and int(s["sms_opt"]) == 0):
        return ""
    tpl = setting("sms_depart_tpl" if action == "depart" else "sms_arrive_tpl") or ""
    body = tpl.replace("{store}", s["store_name"] or "").replace("{eta}", eta_text or "")
    sent = queue_sms(s["phone"], s["store_name"], body, "shipment", shipment_id, "sms_" + action)
    return "점포 SMS %s" % ("발송" if sent else "대기열")
