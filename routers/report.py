"""
Report Router — Leave & OT Summary
- Admin: เห็นทั้งหมด
- SUP: เห็นเฉพาะทีมตัวเอง (SupTeamMember)
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io, pandas as pd
from collections import defaultdict

from database import get_db
import models
from auth import get_current_user, require_admin_or_sup

router = APIRouter(prefix="/api/v1/report", tags=["report"])


def _sup_employee_ids(db: Session, sup_user_id: int) -> set:
    """คืน set ของ employee_id ที่ SUP คนนี้ดูแล"""
    rows = db.query(models.SupTeamMember.employee_id).filter(
        models.SupTeamMember.sup_user_id == sup_user_id
    ).all()
    return {r[0] for r in rows}


@router.get("/leave-ot-summary")
def leave_ot_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: Optional[str] = None,         # ค้นหาชื่อ
    project_id: Optional[int] = None,
    department: Optional[str] = None,
    status: str = "approved",             # approved | all
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    # ── กำหนด employee ที่มองเห็นได้ ──────────────
    if current_user.role == "sup":
        allowed_emp_ids = _sup_employee_ids(db, current_user.id)
    else:
        allowed_emp_ids = None  # admin เห็นทั้งหมด

    # ── ดึงพนักงาน ────────────────────────────────
    emp_q = db.query(models.Employee).filter(models.Employee.is_active == True)
    if allowed_emp_ids is not None:
        emp_q = emp_q.filter(models.Employee.id.in_(allowed_emp_ids))
    if search:
        like = f"%{search}%"
        emp_q = emp_q.filter(
            (models.Employee.first_name + " " + models.Employee.last_name).ilike(like) |
            models.Employee.employee_code.ilike(like)
        )
    if department:
        emp_q = emp_q.filter(models.Employee.department == department)

    employees = emp_q.all()
    emp_ids = [e.id for e in employees]

    if not emp_ids:
        return {"kpi": {}, "rows": []}

    # ── filter โครงการ (ผ่าน assignment) ──────────
    if project_id:
        active_in_project = {
            a.employee_id for a in db.query(models.Assignment).filter(
                models.Assignment.project_id == project_id,
                models.Assignment.employee_id.in_(emp_ids)
            ).all()
        }
        emp_ids = [e for e in emp_ids if e in active_in_project]
        employees = [e for e in employees if e.id in active_in_project]

    if not emp_ids:
        return {"kpi": {}, "rows": []}

    # ── user role per employee ────────────────────
    users = db.query(models.User).filter(
        models.User.employee_id.in_(emp_ids),
        models.User.is_active == True,
    ).all()
    role_map = {}
    for u in users:
        if u.employee_id and u.employee_id not in role_map:
            role_map[u.employee_id] = u.role

    # SUP เห็นแค่พนักงาน (employee) ในสังกัด — ไม่เห็น SUP คนอื่น
    if current_user.role == "sup":
        employees = [e for e in employees if role_map.get(e.id, "employee") != "sup"]
        emp_ids = [e.id for e in employees]
        if not emp_ids:
            return {"kpi": {}, "rows": []}

    # ── project per employee (query ตรง ไม่พึ่ง lazy load) ──
    from sqlalchemy.orm import joinedload
    assigns = db.query(models.Assignment).options(
        joinedload(models.Assignment.project)
    ).filter(
        models.Assignment.employee_id.in_(emp_ids),
    ).order_by(models.Assignment.assigned_at.desc()).all()
    project_map = {}
    # รอบแรก: active assignment
    for a in assigns:
        if a.is_active and a.employee_id not in project_map and a.project:
            project_map[a.employee_id] = a.project.name
    # รอบสอง: fallback → assignment ล่าสุด
    for a in assigns:
        if a.employee_id not in project_map and a.project:
            project_map[a.employee_id] = a.project.name

    # ── Leave requests ─────────────────────────────
    lq = db.query(models.LeaveRequest).filter(
        models.LeaveRequest.employee_id.in_(emp_ids)
    )
    if start_date:
        lq = lq.filter(models.LeaveRequest.start_date >= start_date)
    if end_date:
        lq = lq.filter(models.LeaveRequest.end_date <= end_date)
    if status == "approved":
        lq = lq.filter(models.LeaveRequest.status == "approved")
    leaves = lq.all()

    # ── OT requests ───────────────────────────────
    oq = db.query(models.OTRequest).filter(
        models.OTRequest.employee_id.in_(emp_ids)
    )
    if start_date:
        oq = oq.filter(models.OTRequest.ot_date >= start_date)
    if end_date:
        oq = oq.filter(models.OTRequest.ot_date <= end_date)
    if status == "approved":
        oq = oq.filter(models.OTRequest.status == "approved")
    ots = oq.all()

    # ── Aggregate per employee ─────────────────────
    leave_map = defaultdict(lambda: {"ลาป่วย": 0.0, "ลากิจ": 0.0, "ลาพักร้อน": 0.0, "other": 0.0, "items": []})
    for r in leaves:
        d = leave_map[r.employee_id]
        t = r.leave_type if r.leave_type in ("ลาป่วย", "ลากิจ", "ลาพักร้อน") else "other"
        d[t] += r.days or 0
        d["items"].append({
            "id": r.id, "leave_type": r.leave_type,
            "start_date": r.start_date, "end_date": r.end_date,
            "days": r.days, "status": r.status, "reason": r.reason or "",
            "approver_name": r.approver.username if r.approver else "",
            "approved_at": r.approved_at.strftime("%d/%m/%Y") if r.approved_at else "",
        })

    ot_map = defaultdict(lambda: {"count": 0, "hours": 0.0, "holiday_hours": 0.0, "items": []})
    for r in ots:
        d = ot_map[r.employee_id]
        d["count"] += 1
        d["hours"] += r.hours or 0
        if r.is_holiday_work:
            d["holiday_hours"] += r.hours or 0
        d["items"].append({
            "id": r.id, "ot_date": r.ot_date,
            "start_time": r.start_time or "", "end_time": r.end_time or "",
            "hours": r.hours, "status": r.status,
            "is_holiday_work": bool(r.is_holiday_work), "reason": r.reason or "",
            "approver_name": r.approver.username if r.approver else "",
            "approved_at": r.approved_at.strftime("%d/%m/%Y") if r.approved_at else "",
        })

    # ── Build rows ─────────────────────────────────
    rows = []
    for emp in employees:
        lv = leave_map[emp.id]
        ot = ot_map[emp.id]
        total_leave = lv["ลาป่วย"] + lv["ลากิจ"] + lv["ลาพักร้อน"] + lv["other"]

        project_name = project_map.get(emp.id, "HO")

        rows.append({
            "employee_id": emp.id,
            "employee_code": emp.employee_code,
            "employee_name": f"{emp.first_name} {emp.last_name}",
            "photo_url": emp.photo_url or "",
            "user_role": role_map.get(emp.id, "employee"),
            "department": emp.department or "-",
            "project_name": project_name,
            "leave_sick": lv["ลาป่วย"],
            "leave_personal": lv["ลากิจ"],
            "leave_vacation": lv["ลาพักร้อน"],
            "leave_other": lv["other"],
            "leave_total": total_leave,
            "ot_count": ot["count"],
            "ot_hours": round(ot["hours"], 2),
            "ot_holiday_hours": round(ot["holiday_hours"], 2),
            "leave_items": lv["items"],
            "ot_items": ot["items"],
        })

    # sort: คนที่ลามากสุดก่อน
    rows.sort(key=lambda x: x["leave_total"], reverse=True)

    # ── KPI totals ─────────────────────────────────
    kpi = {
        "total_employees": len(rows),
        "employees_with_leave": sum(1 for r in rows if r["leave_total"] > 0),
        "total_leave_days": round(sum(r["leave_total"] for r in rows), 1),
        "total_ot_hours": round(sum(r["ot_hours"] for r in rows), 1),
        "total_ot_count": sum(r["ot_count"] for r in rows),
        "total_holiday_ot_hours": round(sum(r["ot_holiday_hours"] for r in rows), 1),
    }

    return {"kpi": kpi, "rows": rows}


@router.get("/leave-ot-summary/export")
def export_leave_ot_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: Optional[str] = None,
    project_id: Optional[int] = None,
    department: Optional[str] = None,
    status: str = "approved",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    """Export Excel 3 sheets: Summary, Leave Detail, OT Detail"""
    # reuse summary logic
    result = leave_ot_summary(
        start_date=start_date, end_date=end_date,
        search=search, project_id=project_id,
        department=department, status=status,
        db=db, current_user=current_user
    )
    rows = result["rows"]

    # Sheet 1: Summary
    summary_rows = []
    for r in rows:
        summary_rows.append({
            "รหัสพนักงาน": r["employee_code"],
            "ชื่อ-นามสกุล": r["employee_name"],
            "แผนก": r["department"],
            "โครงการ": r["project_name"],
            "ลาป่วย (วัน)": r["leave_sick"],
            "ลากิจ (วัน)": r["leave_personal"],
            "ลาพักร้อน (วัน)": r["leave_vacation"],
            "ลาอื่นๆ (วัน)": r["leave_other"],
            "รวมวันลา": r["leave_total"],
            "OT (ครั้ง)": r["ot_count"],
            "OT รวม (ชม.)": r["ot_hours"],
            "OT วันหยุด (ชม.)": r["ot_holiday_hours"],
        })

    # Sheet 2: Leave Detail
    leave_rows = []
    for r in rows:
        for lv in r["leave_items"]:
            leave_rows.append({
                "รหัสพนักงาน": r["employee_code"],
                "ชื่อ-นามสกุล": r["employee_name"],
                "แผนก": r["department"],
                "ประเภทการลา": lv["leave_type"],
                "วันเริ่ม": lv["start_date"],
                "วันสิ้นสุด": lv["end_date"],
                "จำนวนวัน": lv["days"],
                "สถานะ": lv["status"],
                "เหตุผล": lv["reason"],
            })

    # Sheet 3: OT Detail
    ot_rows = []
    for r in rows:
        for ot in r["ot_items"]:
            ot_rows.append({
                "รหัสพนักงาน": r["employee_code"],
                "ชื่อ-นามสกุล": r["employee_name"],
                "แผนก": r["department"],
                "วันที่ OT": ot["ot_date"],
                "เวลาเริ่ม": ot["start_time"],
                "เวลาสิ้นสุด": ot["end_time"],
                "ชั่วโมง OT": ot["hours"] or "",
                "วันหยุด": "ใช่" if ot["is_holiday_work"] else "ไม่",
                "สถานะ": ot["status"],
                "เหตุผล": ot["reason"],
            })

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, index=False, sheet_name="สรุปรวม")
        pd.DataFrame(leave_rows if leave_rows else [{"หมายเหตุ": "ไม่มีข้อมูล"}]).to_excel(
            writer, index=False, sheet_name="รายละเอียดการลา")
        pd.DataFrame(ot_rows if ot_rows else [{"หมายเหตุ": "ไม่มีข้อมูล"}]).to_excel(
            writer, index=False, sheet_name="รายละเอียด OT")
