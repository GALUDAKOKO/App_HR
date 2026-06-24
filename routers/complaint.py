from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
import os

from database import get_db
import models
from auth import get_current_user, require_admin, log_action

router = APIRouter(prefix="/api/v1/complaints", tags=["complaints"])


# ── Schemas ────────────────────────────────────────────

class ComplaintCreate(BaseModel):
    project_id: Optional[int] = None
    comp_type: str = "complaint"   # complaint | suggestion
    subject: str
    detail: str
    is_anonymous: bool = False


class ComplaintStatusUpdate(BaseModel):
    status: str          # pending | reviewed | closed
    admin_note: Optional[str] = None


# ── Email helper ───────────────────────────────────────

def _send_complaint_email(comp: models.Complaint, emp: models.Employee,
                          project_name: str, db: Session):
    from routers.email_utils import send_email, get_smtp_config
    from routers.settings import get_setting

    # notify_to: ใช้ค่าจาก DB (complaint_notify_email) หรือ from_email
    notify_to = get_setting(db, "complaint_notify_email") or get_setting(db, "smtp_from_email")
    if not notify_to:
        return

    cfg = get_smtp_config(db)
    if not cfg["smtp_host"] or not cfg["smtp_user"]:
        return

    type_label  = "ข้อเสนอแนะ" if comp.comp_type == "suggestion" else "ข้อร้องเรียน"
    sender_name = "ไม่ระบุตัวตน" if comp.is_anonymous else                   f"{emp.first_name} {emp.last_name} ({emp.employee_code})"

    html = f"""
<div style="font-family:sans-serif;max-width:560px;margin:auto;background:#f8fafc;padding:32px;border-radius:16px">
  <div style="background:#4F46E5;border-radius:12px;padding:20px 24px;margin-bottom:24px">
    <h2 style="color:#fff;margin:0;font-size:18px">🏢 Head Office ZL — {type_label}ใหม่</h2>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:14px;color:#334155">
    <tr><td style="padding:6px 0;font-weight:600;width:100px">ประเภท</td><td>{type_label}</td></tr>
    <tr><td style="padding:6px 0;font-weight:600">ผู้ส่ง</td><td>{sender_name}</td></tr>
    <tr><td style="padding:6px 0;font-weight:600">โครงการ</td><td>{project_name}</td></tr>
    <tr><td style="padding:6px 0;font-weight:600">เรื่อง</td><td>{comp.subject}</td></tr>
    <tr><td style="padding:6px 0;font-weight:600">วันเวลา</td><td>{comp.created_at.strftime('%d/%m/%Y %H:%M')}</td></tr>
  </table>
  <div style="margin-top:16px;padding:16px;background:#fff;border-radius:10px;border:1px solid #e2e8f0;font-size:14px;color:#475569;white-space:pre-wrap">{comp.detail}</div>
  <p style="margin-top:20px;font-size:12px;color:#94a3b8;text-align:center">กรุณาเข้าระบบเพื่อดำเนินการ</p>
</div>"""

    try:
        send_email(db, notify_to, f"[HR-ZL] {type_label} — {comp.subject}", html)
    except Exception:
        pass  # email fail ไม่ block การบันทึก


# ── Endpoints ──────────────────────────────────────────

@router.post("", status_code=201)
def submit_complaint(
    body: ComplaintCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Employee ส่งข้อร้องเรียน/ข้อเสนอแนะ"""
    # SUP ส่ง complaint ได้ (ร้องเรียนต่อ Admin ในฐานะพนักงานขององค์กร)
    emp = db.query(models.Employee).filter(
        models.Employee.id == current_user.employee_id
    ).first()
    if not emp:
        raise HTTPException(status_code=400, detail="employee profile not found")

    comp = models.Complaint(
        employee_id  = emp.id,
        project_id   = body.project_id,
        comp_type    = body.comp_type,
        subject      = body.subject,
        detail       = body.detail,
        is_anonymous = body.is_anonymous,
    )
    db.add(comp)
    db.commit()
    db.refresh(comp)

    # resolve project name
    proj_name = "ไม่ระบุ"
    if body.project_id:
        proj = db.query(models.Project).filter(models.Project.id == body.project_id).first()
        if proj:
            proj_name = proj.name

    _send_complaint_email(comp, emp, proj_name, db)
    log_action(db, current_user, "CREATE", "complaints", comp.id, f"submit {comp.comp_type}: {comp.subject}")
    return {"success": True, "id": comp.id, "message": "บันทึกและแจ้งเรื่องแล้ว"}


@router.get("")
def list_complaints(
    status: Optional[str] = None,
    comp_type: Optional[str] = None,
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)   # Admin ONLY
):
    """Admin ดูรายการข้อร้องเรียน"""
    q = db.query(models.Complaint)
    if status == "open":
        q = q.filter(models.Complaint.status.in_(["pending", "reviewed"]))
    elif status:
        q = q.filter(models.Complaint.status == status)
    if comp_type:
        q = q.filter(models.Complaint.comp_type == comp_type)
    if project_id:
        q = q.filter(models.Complaint.project_id == project_id)
    q = q.order_by(models.Complaint.created_at.desc())

    result = []
    for c in q.all():
        emp = c.employee
        proj = c.project
        result.append({
            "id":           c.id,
            "comp_type":    c.comp_type,
            "subject":      c.subject,
            "detail":       c.detail,
            "is_anonymous": c.is_anonymous,
            "sender":       "ไม่ระบุตัวตน" if c.is_anonymous else (
                f"{emp.first_name} {emp.last_name}" if emp else "-"
            ),
            "employee_code": "" if c.is_anonymous else (emp.employee_code if emp else ""),
            "project_name": proj.name if proj else "-",
            "status":       c.status,
            "admin_note":   c.admin_note,
            "created_at":   c.created_at.isoformat(),
            "reviewed_at":  c.reviewed_at.isoformat() if c.reviewed_at else None,
        })
    return result


@router.patch("/{comp_id}")
def update_complaint_status(
    comp_id: int,
    body: ComplaintStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """Admin อัปเดตสถานะ"""
    comp = db.query(models.Complaint).filter(models.Complaint.id == comp_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="not found")

    comp.status      = body.status
    comp.admin_note  = body.admin_note
    comp.reviewed_by = current_user.id
    comp.reviewed_at = datetime.utcnow()
    comp.updated_at  = datetime.utcnow()
    db.commit()
    log_action(db, current_user, "UPDATE", "complaints", comp_id,
               f"update status -> {body.status}")
    return {"success": True}
