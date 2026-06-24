"""
M1 — OT Request Router
- Employee/SUP: ยื่นคำขอ OT, ดูรายการของตัวเอง
- Admin/SUP: ดูทั้งหมด, อนุมัติ/ปฏิเสธ, Export Excel
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import io, pandas as pd

from database import get_db
import models
from routers.holiday import _is_holiday
from auth import get_current_user, require_admin, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/ot", tags=["ot"])


def _ot_out(req: models.OTRequest) -> dict:
    emp = req.employee
    return {
        "id": req.id,
        "employee_id": req.employee_id,
        "employee_code": emp.employee_code if emp else "",
        "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "",
        "project_id": req.project_id,
        "project_name": req.project.name if req.project else "",
        "ot_date": req.ot_date,
        "start_time": req.start_time or "",
        "end_time": req.end_time or "",
        "hours": req.hours,
        "reason": req.reason or "",
        "status": req.status,
        "approved_by": req.approved_by,
        "approver_name": req.approver.username if req.approver else "",
        "approved_at": req.approved_at.isoformat() if req.approved_at else None,
        "admin_note": req.admin_note or "",
        "is_holiday_work": bool(req.is_holiday_work) if req.is_holiday_work is not None else False,
        "ot_rate": req.ot_rate if req.ot_rate is not None else 1.5,
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }


# ── List ────────────────────────────────────────

@router.get("")
def list_ot(
    employee_id: Optional[int] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    q = db.query(models.OTRequest)

    if current_user.role == "employee":
        if not current_user.employee_id:
            return []
        q = q.filter(models.OTRequest.employee_id == current_user.employee_id)
    elif employee_id:
        q = q.filter(models.OTRequest.employee_id == employee_id)

    if status:
        q = q.filter(models.OTRequest.status == status)
    if start_date:
        q = q.filter(models.OTRequest.ot_date >= start_date)
    if end_date:
        q = q.filter(models.OTRequest.ot_date <= end_date)

    reqs = q.order_by(models.OTRequest.ot_date.desc()).all()
    return [_ot_out(r) for r in reqs]


# ── Create ────────────────────────────────────────

@router.post("", status_code=201)
def create_ot(body: dict,
              db: Session = Depends(get_db),
              current_user: models.User = Depends(get_current_user)):
    emp_id = body.get("employee_id")
    if current_user.role in ("employee", "sup"):
        if not current_user.employee_id:
            raise HTTPException(status_code=400, detail="บัญชีนี้ยังไม่เชื่อมกับพนักงาน")
        emp_id = current_user.employee_id

    if not emp_id:
        raise HTTPException(status_code=400, detail="ระบุ employee_id")

    ot_date = body.get("ot_date", "")
    if not ot_date:
        raise HTTPException(status_code=400, detail="ระบุ ot_date")

    start_time = body.get("start_time", "")
    end_time = body.get("end_time", "")

    # คำนวณชั่วโมงอัตโนมัติถ้ามี start/end
    hours = body.get("hours")
    if not hours and start_time and end_time:
        try:
            fmt = "%H:%M"
            t1 = datetime.strptime(start_time, fmt)
            t2 = datetime.strptime(end_time, fmt)
            diff = (t2 - t1).seconds / 3600
            hours = round(diff, 2) if diff > 0 else None
        except ValueError:
            pass

    # Detect holiday — set rate automatically (can be overridden by caller)
    hol_info = _is_holiday(db, ot_date)
    is_holiday_work = body.get("is_holiday_work", hol_info["is_holiday"])
    ot_rate = 2.0 if is_holiday_work else 1.5

    req = models.OTRequest(
        employee_id=emp_id,
        project_id=body.get("project_id"),
        ot_date=ot_date,
        start_time=start_time or None,
        end_time=end_time or None,
        hours=float(hours) if hours else None,
        reason=body.get("reason", ""),
        status="pending",
        is_holiday_work=bool(is_holiday_work),
        ot_rate=ot_rate,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    log_action(db, current_user, "CREATE", "ot_requests", req.id,
               f"ยื่น OT วันที่ {ot_date} {start_time}-{end_time} ({hours} ชม.)")
    return _ot_out(req)


# ── Approve / Reject ────────────────────────────

@router.put("/{req_id}/approve")
def approve_ot(req_id: int, body: dict = {},
               db: Session = Depends(get_db),
               current_user: models.User = Depends(require_admin_or_sup)):
    req = db.query(models.OTRequest).filter(models.OTRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอ OT")

    if current_user.role == "sup":
        # SUP ห้ามอนุมัติคำขอของตัวเอง
        if current_user.employee_id and current_user.employee_id == req.employee_id:
            raise HTTPException(status_code=403, detail="SUP ไม่สามารถอนุมัติคำขอของตัวเองได้")
        in_team = db.query(models.SupTeamMember).filter(
            models.SupTeamMember.sup_user_id == current_user.id,
            models.SupTeamMember.employee_id == req.employee_id
        ).first()
        if not in_team:
            raise HTTPException(status_code=403, detail="SUP สามารถอนุมัติได้เฉพาะพนักงานในโครงการตัวเอง")

    action = body.get("action", "approved")
    if action not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="action ต้องเป็น approved หรือ rejected")

    req.status = action
    req.approved_by = current_user.id
    req.approved_at = datetime.utcnow()
    req.admin_note = body.get("admin_note", "")
    req.updated_at = datetime.utcnow()
    db.commit()

    label = "อนุมัติ" if action == "approved" else "ปฏิเสธ"
    log_action(db, current_user, "UPDATE", "ot_requests", req_id,
               f"{label} OT id={req_id}")
    return _ot_out(req)


# ── Delete ────────────────────────────────────────

@router.delete("/{req_id}")
def delete_ot(req_id: int,
              db: Session = Depends(get_db),
              current_user: models.User = Depends(require_admin)):
    req = db.query(models.OTRequest).filter(models.OTRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอ OT")
    db.delete(req)
    db.commit()
    log_action(db, current_user, "DELETE", "ot_requests", req_id, "ลบคำขอ OT")
    return {"success": True}


# ── Export Excel ────────────────────────────────

@router.get("/export/excel")
def export_ot(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    q = db.query(models.OTRequest)
    if start_date:
        q = q.filter(models.OTRequest.ot_date >= start_date)
    if end_date:
        q = q.filter(models.OTRequest.ot_date <= end_date)
    if status:
        q = q.filter(models.OTRequest.status == status)
    reqs = q.order_by(models.OTRequest.ot_date.desc()).all()
    rows = []
    for r in reqs:
        emp = r.employee
        rows.append({
            "รหัสพนักงาน": emp.employee_code if emp else "",
            "ชื่อ-นามสกุล": f"{emp.first_name} {emp.last_name}" if emp else "",
            "วันที่ OT": r.ot_date,
            "เวลาเริ่ม": r.start_time or "",
            "เวลาสิ้นสุด": r.end_time or "",
            "ชั่วโมง": r.hours or "",
            "เหตุผล": r.reason or "",
            "สถานะ": r.status,
            "ผู้อนุมัติ": r.approver.username if r.approver else "",
            "วันที่อนุมัติ": r.approved_at.strftime("%Y-%m-%d") if r.approved_at else "",
            "หมายเหตุ Admin": r.admin_note or "",
        })
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["รหัสพนักงาน", "ชื่อ-นามสกุล"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="OT")
    buf.seek(0)
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=ot_requests.xlsx"})
