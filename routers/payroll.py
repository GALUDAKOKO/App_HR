"""
Payroll Export Router
- ดึงข้อมูล Check-in / Leave / OT สำหรับงวดเงินเดือน (เดือน+ปี)
- Admin: เห็นทั้งหมด | SUP: เห็นเฉพาะทีมตัวเอง
- Export Excel 4 sheets: สรุป / check-in / ลา / OT
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io, calendar
from collections import defaultdict
from datetime import datetime

from database import get_db
import models
from .excel_utils import write_excel_multi
from auth import get_current_user, require_admin_or_sup

router = APIRouter(prefix="/api/v1/payroll", tags=["payroll"])


# ────────────────────────────────────────────────────────
# Helper
# ────────────────────────────────────────────────────────

def _sup_employee_ids(db: Session, sup_user_id: int) -> set:
    rows = db.query(models.SupTeamMember.employee_id).filter(
        models.SupTeamMember.sup_user_id == sup_user_id
    ).all()
    return {r[0] for r in rows}


def _month_range(year: int, month: int):
    """คืน (start_str, end_str) เช่น ('2025-06-01', '2025-06-30')"""
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


# ────────────────────────────────────────────────────────
# Core aggregation (shared between summary + export)
# ────────────────────────────────────────────────────────

def _build_payroll(
    db: Session,
    year: int,
    month: int,
    project_id: Optional[int],
    department: Optional[str],
    employee_type: Optional[str],
    allowed_emp_ids: Optional[set],
    caller_role: str = "admin",
):
    start_str, end_str = _month_range(year, month)

    # ── employees ────────────────────────────────────────
    eq = db.query(models.Employee).filter(models.Employee.is_active == True)
    if allowed_emp_ids is not None:
        eq = eq.filter(models.Employee.id.in_(allowed_emp_ids))
    if department:
        eq = eq.filter(models.Employee.department == department)
    if employee_type:
        eq = eq.filter(models.Employee.employee_type == employee_type)
    employees = eq.all()
    emp_ids = [e.id for e in employees]

    if not emp_ids:
        return [], []

    # ── project filter ────────────────────────────────────
    if project_id:
        in_proj = {
            a.employee_id for a in db.query(models.Assignment).filter(
                models.Assignment.project_id == project_id,
                models.Assignment.employee_id.in_(emp_ids)
            ).all()
        }
        employees = [e for e in employees if e.id in in_proj]
        emp_ids = [e.id for e in employees]

    if not emp_ids:
        return [], []

    # ── user role per employee ────────────────────────────
    users = db.query(models.User).filter(
        models.User.employee_id.in_(emp_ids),
        models.User.is_active == True,
    ).all()
    role_map = {}
    for u in users:
        if u.employee_id and u.employee_id not in role_map:
            role_map[u.employee_id] = u.role

    # SUP เห็นแค่พนักงาน (employee) ในสังกัด — ไม่เห็น SUP คนอื่น
    if caller_role == "sup":
        employees = [e for e in employees if role_map.get(e.id, "employee") != "sup"]
        emp_ids = [e.id for e in employees]
        if not emp_ids:
            return [], period if 'period' in dir() else ""

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
    # รอบสอง: fallback → assignment ล่าสุด ถ้ายังไม่มีในแผนที่
    for a in assigns:
        if a.employee_id not in project_map and a.project:
            project_map[a.employee_id] = a.project.name

    # ── check-in ──────────────────────────────────────────
    checkins = db.query(models.CheckIn).filter(
        models.CheckIn.employee_id.in_(emp_ids),
        models.CheckIn.work_date >= start_str,
        models.CheckIn.work_date <= end_str,
    ).all()

    ci_map = defaultdict(list)
    for c in checkins:
        ci_map[c.employee_id].append(c)

    # ── leave (approved only) ────────────────────────────
    leaves = db.query(models.LeaveRequest).filter(
        models.LeaveRequest.employee_id.in_(emp_ids),
        models.LeaveRequest.status == "approved",
        models.LeaveRequest.start_date <= end_str,
        models.LeaveRequest.end_date >= start_str,
    ).all()

    lv_map = defaultdict(lambda: {"ลาป่วย": 0.0, "ลากิจ": 0.0, "ลาพักร้อน": 0.0,
                                   "ลาคลอด": 0.0, "other": 0.0, "items": []})
    for r in leaves:
        d = lv_map[r.employee_id]
        t = r.leave_type if r.leave_type in ("ลาป่วย", "ลากิจ", "ลาพักร้อน", "ลาคลอด") else "other"
        d[t] += r.days or 0
        d["items"].append(r)

    # ── OT (approved only) ───────────────────────────────
    ots = db.query(models.OTRequest).filter(
        models.OTRequest.employee_id.in_(emp_ids),
        models.OTRequest.status == "approved",
        models.OTRequest.ot_date >= start_str,
        models.OTRequest.ot_date <= end_str,
    ).all()

    ot_map = defaultdict(lambda: {"normal_hours": 0.0, "holiday_hours": 0.0,
                                   "count": 0, "items": []})
    for r in ots:
        d = ot_map[r.employee_id]
        d["count"] += 1
        if r.is_holiday_work:
            d["holiday_hours"] += r.hours or 0
        else:
            d["normal_hours"] += r.hours or 0
        d["items"].append(r)

    # ── build rows ────────────────────────────────────────
    rows = []
    for emp in employees:
        cis = ci_map[emp.id]
        lv  = lv_map[emp.id]
        ot  = ot_map[emp.id]

        work_days   = len(cis)
        total_leave = lv["ลาป่วย"] + lv["ลากิจ"] + lv["ลาพักร้อน"] + lv["ลาคลอด"] + lv["other"]
        ot_total    = round(ot["normal_hours"] + ot["holiday_hours"], 2)

        project_name = project_map.get(emp.id, "HO")

        rows.append({
            "employee_id":      emp.id,
            "employee_code":    emp.employee_code,
            "employee_name":    f"{emp.first_name} {emp.last_name}",
            "photo_url":        emp.photo_url or "",
            "department":       emp.department or "-",
            "employee_type":    emp.employee_type or "-",
            "project_name":     project_name,
            "user_role":        role_map.get(emp.id, "employee"),
            "work_days":        work_days,
            "leave_sick":       lv["ลาป่วย"],
            "leave_personal":   lv["ลากิจ"],
            "leave_vacation":   lv["ลาพักร้อน"],
            "leave_maternity":  lv["ลาคลอด"],
            "leave_other":      lv["other"],
            "leave_total":      total_leave,
            "ot_normal_hours":  round(ot["normal_hours"], 2),
            "ot_holiday_hours": round(ot["holiday_hours"], 2),
            "ot_count":         ot["count"],
            "ot_total_hours":   ot_total,
            # raw data for export
            "_ci_items": cis,
            "_lv_items": lv["items"],
            "_ot_items": ot["items"],
        })

    rows.sort(key=lambda x: x["employee_code"])
    return rows, {"year": year, "month": month, "start": start_str, "end": end_str}


# ────────────────────────────────────────────────────────
# API: Summary
# ────────────────────────────────────────────────────────

@router.get("/summary")
def payroll_summary(
    year:          int            = None,
    month:         int            = None,
    project_id:    Optional[int]  = None,
    department:    Optional[str]  = None,
    employee_type: Optional[str]  = None,
    db:            Session        = Depends(get_db),
    current_user:  models.User    = Depends(require_admin_or_sup),
):
    now = datetime.utcnow()
    year  = year  or now.year
    month = month or now.month

    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="month ต้องอยู่ระหว่าง 1–12")

    allowed = _sup_employee_ids(db, current_user.id) if current_user.role == "sup" else None

    rows, period = _build_payroll(db, year, month, project_id, department, employee_type, allowed, caller_role=current_user.role)

    # strip raw items before returning JSON
    clean_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    kpi = {
        "total_employees":  len(clean_rows),
        "total_work_days":  sum(r["work_days"] for r in clean_rows),
        "total_leave_days": round(sum(r["leave_total"] for r in clean_rows), 1),
        "total_ot_hours":   round(sum(r["ot_total_hours"] for r in clean_rows), 1),
    }

    return {"period": period, "kpi": kpi, "rows": clean_rows}


# ────────────────────────────────────────────────────────
# API: Export Excel
# ────────────────────────────────────────────────────────

@router.get("/export")
def payroll_export(
    year:          int            = None,
    month:         int            = None,
    project_id:    Optional[int]  = None,
    department:    Optional[str]  = None,
    employee_type: Optional[str]  = None,
    db:            Session        = Depends(get_db),
    current_user:  models.User    = Depends(require_admin_or_sup),
):
    now   = datetime.utcnow()
    year  = year  or now.year
    month = month or now.month

    allowed = _sup_employee_ids(db, current_user.id) if current_user.role == "sup" else None
    rows, period = _build_payroll(db, year, month, project_id, department, employee_type, allowed, caller_role=current_user.role)

    month_label = f"{year:04d}-{month:02d}"

    # ── Sheet 1: สรุปรายงานค่าจ้าง ────────────────────────
    summary_rows = []
    for r in rows:
        summary_rows.append({
            "รหัสพนักงาน":        r["employee_code"],
            "ชื่อ-นามสกุล":       r["employee_name"],
            "แผนก":               r["department"],
            "ประเภทพนักงาน":      r["employee_type"],
            "โครงการ":             r["project_name"],
            "วันทำงาน (วัน)":      r["work_days"],
            "ลาป่วย (วัน)":        r["leave_sick"],
            "ลากิจ (วัน)":         r["leave_personal"],
            "ลาพักร้อน (วัน)":     r["leave_vacation"],
            "ลาคลอด (วัน)":        r["leave_maternity"],
            "ลาอื่นๆ (วัน)":       r["leave_other"],
            "รวมวันลา (วัน)":      r["leave_total"],
            "OT ปกติ (ชม.)":       r["ot_normal_hours"],
            "OT วันหยุด (ชม.)":    r["ot_holiday_hours"],
            "OT รวม (ชม.)":        r["ot_total_hours"],
            "ครั้ง OT":            r["ot_count"],
        })

    # ── Sheet 2: รายละเอียด Check-in ─────────────────────
    ci_rows = []
    for r in rows:
        for c in r["_ci_items"]:
            ci_rows.append({
                "รหัสพนักงาน":  r["employee_code"],
                "ชื่อ-นามสกุล": r["employee_name"],
                "แผนก":         r["department"],
                "วันที่":        c.work_date,
                "เวลาเข้า":      c.check_in_time.strftime("%H:%M") if c.check_in_time else "",
                "เวลาออก":      c.check_out_time.strftime("%H:%M") if c.check_out_time else "",
                "GPS OK":       "ใช่" if c.check_in_ok else "ไม่",
                "ระยะทาง (กม.)": round(c.check_in_dist, 3) if c.check_in_dist else "",
                "หมายเหตุ":      c.note or "",
            })

    # ── Sheet 3: รายละเอียดการลา ─────────────────────────
    lv_rows = []
    for r in rows:
        for lv in r["_lv_items"]:
            lv_rows.append({
                "รหัสพนักงาน":  r["employee_code"],
                "ชื่อ-นามสกุล": r["employee_name"],
                "แผนก":         r["department"],
                "ประเภทการลา":  lv.leave_type,
                "วันเริ่ม":      lv.start_date,
                "วันสิ้นสุด":    lv.end_date,
                "จำนวนวัน":     lv.days,
                "เหตุผล":       lv.reason or "",
                "อนุมัติโดย":    lv.approver.username if lv.approver else "",
                "วันที่อนุมัติ":  lv.approved_at.strftime("%d/%m/%Y") if lv.approved_at else "",
            })

    # ── Sheet 4: รายละเอียด OT ────────────────────────────
    ot_rows = []
    for r in rows:
        for ot in r["_ot_items"]:
            ot_rows.append({
                "รหัสพนักงาน":  r["employee_code"],
                "ชื่อ-นามสกุล": r["employee_name"],
                "แผนก":         r["department"],
                "วันที่ OT":     ot.ot_date,
                "เวลาเริ่ม":     ot.start_time or "",
                "เวลาสิ้นสุด":   ot.end_time or "",
                "ชั่วโมง":       ot.hours or 0,
                "อัตรา OT":     ot.ot_rate or 1.5,
                "วันหยุด":      "ใช่" if ot.is_holiday_work else "ไม่",
                "เหตุผล":       ot.reason or "",
                "อนุมัติโดย":    ot.approver.username if ot.approver else "",
                "วันที่อนุมัติ":  ot.approved_at.strftime("%d/%m/%Y") if ot.approved_at else "",
            })

    # ── Write Excel ───────────────────────────────────────
    buf = io.BytesIO()
    write_excel_multi(buf, [
        ("สรุปค่าจ้าง", summary_rows if summary_rows else [{"หมายเหตุ": "ไม่มีข้อมูล"}]),
        ("Check-in", ci_rows if ci_rows else [{"หมายเหตุ": "ไม่มีข้อมูล"}]),
        ("การลา", lv_rows if lv_rows else [{"หมายเหตุ": "ไม่มีข้อมูล"}]),
        ("OT", ot_rows if ot_rows else [{"หมายเหตุ": "ไม่มีข้อมูล"}]),
    ])

    buf.seek(0)
    filename = f"payroll_{month_label}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
