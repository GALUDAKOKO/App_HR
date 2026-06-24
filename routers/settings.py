from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session
from datetime import datetime
import os, uuid
from PIL import Image
import io

from database import get_db
import models
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

DEFAULT_SETTINGS = {
    "company_name": "HEAD OFFICE ZL",
    "logo_url": "",
    "company_address": "",
    "company_phone": "",
    "company_tax_id": "",
    "work_start_time": "08:00",
    "work_end_time": "17:00",
    "quota_late_per_month": "3",
    "quota_absent_per_year": "3",
    "quota_leave_per_year": "10",       # legacy — ยังคงไว้เพื่อ backward compat
    "quota_leave_personal": "6",        # ลากิจ (วัน/ปี)
    "quota_leave_sick": "30",           # ลาป่วย (วัน/ปี) — กฎหมายแรงงานไทย max 30
    "quota_leave_vacation": "10",       # ลาพักร้อน (วัน/ปี)
    "quota_leave_maternity": "98",      # ลาคลอด (วัน) — กฎหมายกำหนด 98 วัน (แยกจาก quota ปกติ)
    # SMTP
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from_name": "Head Office ZL",
    "smtp_from_email": "",
    "smtp_use_tls": "true",
    "complaint_notify_email": "",
}


def get_setting(db: Session, key: str) -> str:
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    return row.value if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(db: Session, key: str, value: str):
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(models.Setting(key=key, value=value))
    db.commit()


@router.get("")
def get_all_settings(db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    result = dict(DEFAULT_SETTINGS)
    rows = db.query(models.Setting).all()
    for r in rows:
        result[r.key] = r.value or ""
    return result


@router.put("")
def update_settings(body: dict, db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_admin)):
    allowed = {"company_name", "company_address", "company_phone", "company_tax_id",
               "work_start_time", "quota_late_per_month", "quota_absent_per_year", "quota_leave_per_year",
               "quota_leave_personal", "quota_leave_sick", "quota_leave_vacation", "quota_leave_maternity",
               "smtp_host", "smtp_port", "smtp_user", "smtp_password",
               "smtp_from_name", "smtp_from_email", "smtp_use_tls", "complaint_notify_email"}
    for key, value in body.items():
        if key in allowed:
            set_setting(db, key, str(value))
    return {"success": True, "message": "บันทึกการตั้งค่าแล้ว"}


@router.post("/logo")
def upload_logo(file: UploadFile = File(...),
                db: Session = Depends(get_db),
                current_user: models.User = Depends(require_admin)):
    """Upload + resize logo (max 300x300, JPEG 85%)"""
    contents = file.file.read()
    img = Image.open(io.BytesIO(contents))
    img.thumbnail((300, 300), Image.LANCZOS)
    if img.mode in ("RGBA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    os.makedirs("uploads", exist_ok=True)
    filename = f"logo_{uuid.uuid4().hex[:8]}.jpg"
    filepath = os.path.join("uploads", filename)
    img.save(filepath, format="JPEG", quality=85)

    logo_url = f"/uploads/{filename}"
    set_setting(db, "logo_url", logo_url)
    return {"success": True, "logo_url": logo_url}

@router.post("/smtp/test")
def test_smtp(body: dict, db: Session = Depends(get_db),
              current_user: models.User = Depends(require_admin)):
    """ทดสอบส่ง email ผ่าน SMTP config ปัจจุบัน"""
    from routers.email_utils import send_email
    to_email = body.get("to_email", "").strip()
    if not to_email:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="กรุณาระบุ to_email")
    result = send_email(
        db, to_email,
        "🧪 ทดสอบ SMTP — Head Office ZL",
        "<h2>✅ SMTP ทำงานปกติ!</h2><p>ถ้าคุณเห็นอีเมลนี้ แสดงว่าตั้งค่า SMTP สำเร็จแล้ว</p>"
    )
    return result
