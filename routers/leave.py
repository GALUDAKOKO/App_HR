"""
M1 — Leave Request Router
- Employee/SUP: ยื่นคำขอลา, ดูรายการลาของตัวเอง
- Admin/SUP: ดูรายการทั้งหมด (SUP เห็นเฉพาะโครงการตัวเอง), อนุมัติ/ปฏิเสธ
- Export Excel
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import io

from database import get_db
import models
from routers.holiday import _is_holiday
from .excel_utils import write_excel
from auth import get_current_user, require_admin, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/leave", tags=["leave"])

LEAVE_TYPES = ("ลาป่วย", "ลากิจ", "ลาพักร้อน")
STATUSES = ("pending", "approved", "rejected")


def _leave_out(req: models.LeaveRequest) -> dict:
    emp = req.employee
    return {
        "id": req.id,
        "employee_id": req.employee_id,
        "employee_code": emp.employee_code if emp else "",
        "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "",
        "project_id": req.project_id,
        "project_name": req.project.name if req.project else "",
        "leave_type": req.leave_type,
        "start_date": req.start_date,
        "end_date": req.end_date,
        "days": req.days,
        "reason": req.reason or "",
        "status": req.status,
        "approved_by": req.approved_by,
        "approver_name": req.approver.username if req.approver else "",
        "approved_at": req.approved_at.isoformat() if req.approved_at else None,
        "admin_note": req.admin_note or "",
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }


# ── List ────────────────────────────────────────

@router.get("")
def list_leave(
    employee_id: Optional[int] = None,
    status: Optional[str] = None,
    leave_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    q = db.query(models.LeaveRequest)

    if current_user.role == "employee":
        if not current_user.employee_id:
            return []
        q = q.filter(models.LeaveRequest.employee_id == current_user.employee_id)
    elif employee_id:
        q = q.filter(models.LeaveRequest.employee_id == employee_id)

    if status:
        q = q.filter(models.LeaveRequest.status == status)
    if leave_type:
        q = q.filter(models.LeaveRequest.leave_type == leave_type)
    if start_date:
        q = q.filter(models.LeaveRequest.start_date >= start_date)
    if end_date:
        q = q.filter(models.LeaveRequest.end_date <= end_date)

    reqs = q.order_by(models.LeaveRequest.created_at.desc()).all()
    return [_leave_out(r) for r in reqs]


# ── Create ────────────────────────────────────────

@router.post("", status_code=201)
def create_leave(body: dict,
                 db: Session = Depends(get_db),
                 current_user: models.User = Depends(get_current_user)):
    # Employee ยื่นได้เฉพาะของตัวเอง, Admin ยื่นให้คนอื่นได้
    emp_id = body.get("employee_id")
    if current_user.role in ("employee", "sup"):
        # employee & SUP ยื่นได้เฉพาะของตัวเอง
        if not current_user.employee_id:
            raise HTTPException(status_code=400, detail="บัญชีนี้ยังไม่เชื่อมกับพนักงาน")
        emp_id = current_user.employee_id
    # admin ยื่นแทนได้ (emp_id จาก body)

    if not emp_id:
        raise HTTPException(status_code=400, detail="ระบุ employee_id")

    leave_type = body.get("leave_type", "ลากิจ")
    if leave_type not in LEAVE_TYPES:
        raise HTTPException(status_code=400, detail=f"ประเภทการลาต้องเป็น: {', '.join(LEAVE_TYPES)}")

    start = body.get("start_date", "")
    end = body.get("end_date", "") or start
    if not start:
        raise HTTPException(status_code=400, detail="ระบุ start_date")

    # คำนวณวันลา (ไม่นับวันหยุด)
    try:
        from datetime import timedelta
        d1 = datetime.strptime(start, "%Y-%m-%d")
        d2 = datetime.strptime(end, "%Y-%m-%d")
        if body.get("days"):
            days = float(body["days"])
        else:
            # นับเฉพาะวันทำงาน (ไม่รวมวันหยุด)
            working_days = 0.0
            cur = d1
            while cur <= d2:
                ds = cur.strftime("%Y-%m-%d")
                hol = _is_holiday(db, ds)
                if not hol["is_holiday"]:
                    working_days += 1.0
                cur += timedelta(days=1)
            days = max(1.0, working_days)
    except ValueError:
        raise HTTPException(status_code=400, detail="รูปแบบวันที่ต้องเป็น YYYY-MM-DD")

    req = models.LeaveRequest(
        employee_id=emp_id,
        project_id=body.get("project_id"),
        leave_type=leave_type,
        start_date=start,
        end_date=end,
        days=float(days),
        reason=body.get("reason", ""),
        status="pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    log_action(db, current_user, "CREATE", "leave_requests", req.id,
               f"ยื่นคำขอลา {leave_type} {start}→{end} ({days} วัน)")
    return _leave_out(req)


# ── Approve / Reject ────────────────────────────

@router.put("/{req_id}/approve")
def approve_leave(req_id: int, body: dict = {},
                  db: Session = Depends(get_db),
                  current_user: models.User = Depends(require_admin_or_sup)):
    req = db.query(models.LeaveRequest).filter(models.LeaveRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอลา")

    # SUP อนุมัติได้เฉพาะพนักงานในโครงการตัวเอง และห้ามอนุมัติตัวเอง
    if current_user.role == "sup":
        if current_user.employee_id and current_user.employee_id == req.employee_id:
            raise HTTPException(status_code=403, detail="SUP ไม่สามารถอนุมัติคำขอของตัวเองได้")
        in_team = db.query(models.SupTeamMember).filter(
            models.SupTeamMember.sup_user_id == current_user.id,
            models.SupTeamMember.employee_id == req.employee_id
        ).first()
        if not in_team:
            raise HTTPException(status_code=403, detail="SUP สามารถอนุมัติได้เฉพาะพนักงานในโครงการตัวเอง")

    action = body.get("action", "approved")  # approved / rejected
    if action not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="action ต้องเป็น approved หรือ rejected")

    req.status = action
    req.approved_by = current_user.id
    req.approved_at = datetime.utcnow()
    req.admin_note = body.get("admin_note", "")
    req.updated_at = datetime.utcnow()
    db.commit()

    label = "อนุมัติ" if action == "approved" else "ปฏิเสธ"
    log_action(db, current_user, "UPDATE", "leave_requests", req_id,
               f"{label}คำขอลา id={req_id}")
    return _leave_out(req)


# ── Delete (Admin only, pending เท่านั้น) ────────

@router.delete("/{req_id}")
def delete_leave(req_id: int,
                 db: Session = Depends(get_db),
                 current_user: models.User = Depends(require_admin)):
    req = db.query(models.LeaveRequest).filter(models.LeaveRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอลา")
    db.delete(req)
    db.commit()
    log_action(db, current_user, "DELETE", "leave_requests", req_id, "ลบคำขอลา")
    return {"success": True}


# ── Export Excel ────────────────────────────────

@router.get("/export/excel")
def export_leave(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    q = db.query(models.LeaveRequest)
    if start_date:
        q = q.filter(models.LeaveRequest.start_date >= start_date)
    if end_date:
        q = q.filter(models.LeaveRequest.end_date <= end_date)
    if status:
        q = q.filter(models.LeaveRequest.status == status)
    reqs = q.order_by(models.LeaveRequest.start_date.desc()).all()
    rows = []
    for r in reqs:
        emp = r.employee
        rows.append({
            "รหัสพนักงาน": emp.employee_code if emp else "",
            "ชื่อ-นามสกุล": f"{emp.first_name} {emp.last_name}" if emp else "",
            "ประเภทการลา": r.leave_type,
            "วันที่เริ่ม": r.start_date,
            "วันที่สิ้นสุด": r.end_date,
            "จำนวนวัน": r.days,
            "เหตุผล": r.reason or "",
            "สถานะ": r.status,
            "ผู้อนุมัติ": r.approver.username if r.approver else "",
            "วันที่อนุมัติ": r.approved_at.strftime("%Y-%m-%d") if r.approved_at else "",
            "หมายเหตุ Admin": r.admin_note or "",
        })
    buf = io.BytesIO()
    write_excel(buf, rows, sheet_name="ใบลา")
    buf.seek(0)
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=leave_requests.xlsx"})
