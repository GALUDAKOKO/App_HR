"""
Email utility — ส่ง email ผ่าน SMTP config
[RENDER VERSION] Priority: ENV VAR > DB Setting
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM_EMAIL ตั้งใน Render Dashboard
- ถ้าไม่มี env var → fallback อ่านจาก DB Settings (Admin UI)
"""
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.orm import Session
import models


def get_smtp_config(db: Session) -> dict:
    """โหลด SMTP config — Render env var มี priority สูงกว่า DB"""
    keys = ["smtp_host", "smtp_port", "smtp_user", "smtp_password",
            "smtp_from_name", "smtp_from_email", "smtp_use_tls"]
    result = {}
    rows = db.query(models.Setting).filter(models.Setting.key.in_(keys)).all()
    for r in rows:
        result[r.key] = r.value or ""
    # defaults
    result.setdefault("smtp_host", "")
    result.setdefault("smtp_port", "587")
    result.setdefault("smtp_user", "")
    result.setdefault("smtp_password", "")
    result.setdefault("smtp_from_name", "Head Office ZL")
    result.setdefault("smtp_from_email", "")
    result.setdefault("smtp_use_tls", "true")

    # Render env var override — password ไม่เปิดเผยใน code หรือ DB
    if os.getenv("SMTP_HOST"):
        result["smtp_host"]       = os.getenv("SMTP_HOST", "")
        result["smtp_port"]       = os.getenv("SMTP_PORT", "587")
        result["smtp_user"]       = os.getenv("SMTP_USER", "")
        result["smtp_password"]   = os.getenv("SMTP_PASS", "")
        result["smtp_from_email"] = os.getenv("SMTP_FROM_EMAIL", result["smtp_from_email"])
        result["smtp_from_name"]  = os.getenv("SMTP_FROM_NAME", result["smtp_from_name"])
        result["smtp_use_tls"]    = os.getenv("SMTP_USE_TLS", "true")
    return result


def send_email(db: Session, to_email: str, subject: str, html_body: str) -> dict:
    """
    ส่ง email — returns {"success": True} หรือ {"success": False, "error": "..."}
    """
    cfg = get_smtp_config(db)

    if not cfg["smtp_host"] or not cfg["smtp_user"] or not cfg["smtp_from_email"]:
        return {"success": False, "error": "ยังไม่ได้ตั้งค่า SMTP — กรุณาตั้งค่าใน Settings ก่อน"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{cfg['smtp_from_name']} <{cfg['smtp_from_email']}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        port = int(cfg["smtp_port"] or 587)
        use_tls = cfg["smtp_use_tls"].lower() in ("true", "1", "yes")

        if port == 465:
            # SSL
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["smtp_host"], port, context=context, timeout=10) as server:
                server.login(cfg["smtp_user"], cfg["smtp_password"])
                server.sendmail(cfg["smtp_from_email"], to_email, msg.as_string())
        else:
            # STARTTLS (587) หรือ plain (25)
            with smtplib.SMTP(cfg["smtp_host"], port, timeout=10) as server:
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                server.login(cfg["smtp_user"], cfg["smtp_password"])
                server.sendmail(cfg["smtp_from_email"], to_email, msg.as_string())

        return {"success": True}

    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Authentication ล้มเหลว — ตรวจสอบ username/password"}
    except smtplib.SMTPConnectError:
        return {"success": False, "error": f"ไม่สามารถต่อ SMTP {cfg['smtp_host']}:{port} ได้"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_invite_email(db: Session, to_email: str, username: str,
                      invite_url: str, company_name: str = "Head Office ZL") -> dict:
    html = f"""
<div style="font-family:sans-serif;max-width:520px;margin:auto;background:#f8fafc;padding:32px;border-radius:16px">
  <div style="background:#4F46E5;border-radius:12px;padding:24px;text-align:center;margin-bottom:24px">
    <h1 style="color:#fff;margin:0;font-size:22px">🏢 {company_name}</h1>
  </div>
  <h2 style="color:#1e293b">ยินดีต้อนรับสู่ระบบ HR</h2>
  <p style="color:#475569">สวัสดี <strong>{username}</strong>,<br>
  บัญชีของคุณถูกสร้างแล้ว กรุณาคลิกปุ่มด้านล่างเพื่อตั้งรหัสผ่านของคุณ</p>
  <div style="text-align:center;margin:32px 0">
    <a href="{invite_url}" style="background:#4F46E5;color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:bold;font-size:15px">
      🔑 ตั้งรหัสผ่าน
    </a>
  </div>
  <p style="color:#94a3b8;font-size:12px;text-align:center">
    ลิงก์นี้ใช้ได้ภายใน 24 ชั่วโมง<br>
    หากคุณไม่ได้สร้างบัญชีนี้ กรุณาติดต่อผู้ดูแลระบบ
  </p>
</div>"""
    return send_email(db, to_email, f"[{company_name}] คำเชิญเข้าสู่ระบบ HR", html)


def send_reset_email(db: Session, to_email: str, username: str,
                     reset_url: str, company_name: str = "Head Office ZL") -> dict:
    html = f"""
<div style="font-family:sans-serif;max-width:520px;margin:auto;background:#f8fafc;padding:32px;border-radius:16px">
    <h1 style="color:#fff;margin:0;font-size:22px">🏢 {company_name}</h1>
  </div>
  <h2 style="color:#1e293b">รีเซ็ตรหัสผ่าน</h2>
  <p style="color:#475569">สวัสดี <strong>{username}</strong>,<br>
  มีการร้องขอรีเซ็ตรหัสผ่านสำหรับบัญชีนี้</p>
  <div style="text-align:center;margin:32px 0">
    <a href="{reset_url}" style="background:#dc2626;color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:bold;font-size:15px">
      🔒 รีเซ็ตรหัสผ่าน
    </a>
  </div>
  <p style="color:#94a3b8;font-size:12px;text-align:center">
    ลิงก์นี้ใช้ได้ภายใน 1 ชั่วโมง
  </p>
</div>"""
    return send_email(db, to_email, f"[{company_name}] รีเซ็ตรหัสผ่าน", html)
