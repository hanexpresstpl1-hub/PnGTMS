"""메일 발송(거점 반품 안내 등). SMTP 설정(smtp_host 등)이 있으면 보내고, 없으면 본문만 만들어 notifications 큐에 남긴다."""
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

from .db import execute, now, setting


class MailError(Exception):
    pass


def configured():
    return bool((setting("smtp_host") or "").strip() and (setting("smtp_from") or "").strip())


def send_mail(to_addrs, subject, body, ref_type=None, ref_id=None, kind="mail"):
    """반환 (sent: bool, message). 실패·미설정이면 큐(status queued)에 저장."""
    to_list = [a.strip() for a in (to_addrs if isinstance(to_addrs, (list, tuple)) else str(to_addrs or "").replace(";", ",").split(",")) if a and a.strip()]
    nid = execute("INSERT INTO notifications(kind,channel,to_addr,subject,body,ref_type,ref_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (kind, "mail", ", ".join(to_list), subject, body, ref_type, ref_id, "queued", now()))
    if not to_list:
        execute("UPDATE notifications SET status='failed', error='받는 주소 없음' WHERE id=?", (nid,))
        return False, "받는 메일 주소가 없습니다(거점 마스터의 반품 안내 메일을 채우세요)."
    if not configured():
        return False, "SMTP가 설정되지 않아 본문만 만들었습니다(복사해서 보내세요)."
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((setting("company") or "한익스프레스", setting("smtp_from")))
        msg["To"] = ", ".join(to_list)
        port = int(setting("smtp_port") or 587)
        with smtplib.SMTP(setting("smtp_host"), port, timeout=15) as sv:
            sv.ehlo()
            if port != 25:
                sv.starttls()
            user, pw = setting("smtp_user") or "", os.environ.get("PGPORTAL_SMTP_PW", "")
            if user and pw:
                sv.login(user, pw)
            sv.sendmail(setting("smtp_from"), to_list, msg.as_string())
        execute("UPDATE notifications SET status='sent', sent_at=? WHERE id=?", (now(), nid))
        return True, "메일을 보냈습니다: %s" % ", ".join(to_list)
    except Exception as e:
        execute("UPDATE notifications SET status='failed', error=? WHERE id=?", (str(e)[:300], nid))
        return False, "메일 발송 실패: %s" % e
