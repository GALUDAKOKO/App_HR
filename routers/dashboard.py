"""
Dashboard Router — Personal & Team Stats
- GET /api/v1/dashboard/me      → stats ของตัวเอง (employee/sup/admin)
- GET /api/v1/dashboard/team    → SUP/Admin เห็น summary ทีม
- GET /api/v1/dashboard/admin   → Admin เห็น overview ทั้งองค์กร
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, date
import json

from database import get_db
import models
from auth import get_current_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def _get_setting(db: Session, key: str, default=None):
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    return row.value if row else default


def _get_quotas(db: Session) -> dict:
    return {
        "work_start_time": _get_setting(db, "work_start_time", "08:00"),
        "quota_late_per_month": int(_get_setting(db, "quota_late_per_month", 3) or 3),
        "quota_absent_per_year": int(_get_setting(db, "quota_absent_per_year", 3) or 3),
        "quota_leave_per_year": int(_get_setting(db, "quota_leave_per_year", 10) or 10),
    }


def _employee_stats(db: Session, employee_id: int, quotas: dict) -> dict:
    """คำนวณ stats ของพนักงานคนหนึ่งสำหรับเดือนนี้ + ปีนี้"""
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    year_str = str(now.year)

    # ── Late count (month) ──────────────────────────
    work_start = quotas.get("work_start_time", "08:00")
    try:
        wh, wm = [int(x) for x in work_start.split(":")]
    except Exception:
        wh, wm = 8, 0

    checkins = db.query(models.CheckIn).filter(
        models.CheckIn.employee_id == employee_id,
        models.CheckIn.work_date.like(f"{month_str}%"),
        models.CheckIn.check_in_time != None,
    ).all()

    late_month = 0
    for ci in checkins:
        t = ci.check_in_time
        if t and (t.hour > wh or (t.hour == wh and t.minute > wm)):
            late_month += 1

    # ── Absent count (year) — ไม่มี checkin record เลยในวันทำงาน
    # วิธีง่าย: นับ CheckIn ที่ check_in_time IS NULL (มา checkin แต่ไม่มีเวลา = absent)
    absent_year = db.query(models.CheckIn).filter(
        models.CheckIn.employee_id == employee_id,
        models.CheckIn.work_date.like(f"{year_str}%"),
        models.CheckIn.check_in_time == None,
    ).count()

    # ── Leave (year) ────────────────────────────────
    leave_rows = db.query(models.LeaveRequest).filter(
        models.LeaveRequest.employee_id == employee_id,
        models.LeaveRequest.status == "approved",
        models.LeaveRequest.start_date.like(f"{year_str}%"),
    ).all()
    leave_days_year = sum(r.days or 1 for r in leave_rows)

    # Leave month (สำหรับ reference)
    leave_month_rows = db.query(models.LeaveRequest).filter(
        models.LeaveRequest.employee_id == employee_id,
        models.LeaveRequest.status == "approved",
        models.LeaveRequest.start_date.like(f"{month_str}%"),
    ).all()
    leave_days_month = sum(r.days or 1 for r in leave_month_rows)

    # ── OT (month) ─────────────────────────────────
    ot_rows = db.query(models.OTRequest).filter(
        models.OTRequest.employee_id == employee_id,
        models.OTRequest.status == "approved",
        models.OTRequest.ot_date.like(f"{month_str}%"),
    ).all()
    ot_hours_month = sum(r.hours or 0 for r in ot_rows)

    # OT year
    ot_rows_year = db.query(models.OTRequest).filter(
        models.OTRequest.employee_id == employee_id,
        models.OTRequest.status == "approved",
        models.OTRequest.ot_date.like(f"{year_str}%"),
    ).all()
    ot_hours_year = sum(r.hours or 0 for r in ot_rows_year)

    # Pending requests
    pending_leave = db.query(models.LeaveRequest).filter(
        models.LeaveRequest.employee_id == employee_id,
        models.LeaveRequest.status == "pending",
    ).count()
    pending_ot = db.query(models.OTRequest).filter(
        models.OTRequest.employee_id == employee_id,
        models.OTRequest.status == "pending",
    ).count()

    quota_late = quotas.get("quota_late_per_month", 3)
    quota_absent = quotas.get("quota_absent_per_year", 3)
    quota_leave = quotas.get("quota_leave_per_year", 10)

    return {
        "late_month": late_month,
        "late_remaining_month": max(0, quota_late - late_month),
        "quota_late_per_month": quota_late,
        "absent_year": absent_year,
        "absent_remaining_year": max(0, quota_absent - absent_year),
        "quota_absent_per_year": quota_absent,
        "leave_days_year": leave_days_year,
        "leave_remaining_year": max(0, quota_leave - leave_days_year),
        "quota_leave_per_year": quota_leave,
        "leave_days_month": leave_days_month,
        "ot_hours_month": round(ot_hours_month, 1),
        "ot_hours_year": round(ot_hours_year, 1),
        "pending_leave": pending_leave,
        "pending_ot": pending_ot,
        "month": now.strftime("%B %Y"),
        "year": year_str,
        "work_start_time": quotas.get("work_start_time", "08:00"),
    }


# ── GET /me ─────────────────────────────────────────

@router.get("/me")
def my_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Stats ส่วนตัวของผู้ใช้ที่ login"""
    if not current_user.employee_id:
        # Admin ที่ไม่ผูกกับ employee → return admin overview
        quotas = _get_quotas(db)
        return {
            "role": current_user.role,
            "has_employee": False,
            "quotas": quotas,
        }

    quotas = _get_quotas(db)
    stats = _employee_stats(db, current_user.employee_id, quotas)
    stats["role"] = current_user.role
    stats["has_employee"] = True

    # ข้อมูลพนักงาน
    emp = db.query(models.Employee).filter(models.Employee.id == current_user.employee_id).first()
    if emp:
        stats["employee_name"] = f"{emp.first_name} {emp.last_name}"
        stats["employee_code"] = emp.employee_code
        stats["department"] = emp.department or ""

    return stats


# ── Team Members CRUD (project-scoped) ───────────────

@router.get("/team-members")
def get_team_members(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SUP: ดูรายชื่อพนักงานในสังกัด ของโครงการนั้น"""
    if current_user.role not in ("admin", "sup"):
        raise HTTPException(403, "เฉพาะ Admin/SUP")
    q = db.query(models.SupTeamMember).filter(
        models.SupTeamMember.project_id == project_id,
    )
    if current_user.role == "sup":
        q = q.filter(models.SupTeamMember.sup_user_id == current_user.id)
    members = q.all()
    return [
        {
            "id": m.id,
            "employee_id": m.employee_id,
            "project_id": m.project_id,
            "employee_code": m.employee.employee_code if m.employee else "",
            "name": f"{m.employee.first_name} {m.employee.last_name}" if m.employee else "",
            "department": m.employee.department or "" if m.employee else "",
        }
        for m in members
    ]


@router.post("/team-members/{project_id}/{employee_id}")
def add_team_member(
    project_id: int,
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SUP: เพิ่มพนักงานเข้าสังกัดของโครงการ"""
    if current_user.role not in ("admin", "sup"):
        raise HTTPException(403, "เฉพาะ Admin/SUP")
    proj = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not proj:
        raise HTTPException(404, "ไม่พบโครงการ")
    if current_user.role == "sup" and proj.sup_user_id != current_user.id:
        raise HTTPException(403, "คุณไม่ใช่ SUP ของโครงการนี้")
    emp = db.query(models.Employee).filter(
        models.Employee.id == employee_id,
        models.Employee.is_active == True
    ).first()
    if not emp:
        raise HTTPException(404, "ไม่พบพนักงาน")
    try:
        sup_uid = current_user.id
        if current_user.role == "admin" and proj.sup_user_id:
            sup_uid = proj.sup_user_id
        m = models.SupTeamMember(
            sup_user_id=sup_uid,
            project_id=project_id,
            employee_id=employee_id
        )
        db.add(m)
        db.flush()
    except IntegrityError:
        db.rollback()

    # ── sync Assignment ────────────────────────────────
    existing_assign = db.query(models.Assignment).filter(
        models.Assignment.employee_id == employee_id,
        models.Assignment.project_id == project_id,
    ).first()
    if existing_assign:
        if not existing_assign.is_active:
            existing_assign.is_active = True
            existing_assign.assigned_at = datetime.utcnow()
            existing_assign.unassigned_at = None
    else:
        # deactivate assignment ในโครงการอื่นก่อน
        old = db.query(models.Assignment).filter(
            models.Assignment.employee_id == employee_id,
            models.Assignment.is_active == True,
        ).first()
        if old:
            old.is_active = False
            old.unassigned_at = datetime.utcnow()
        db.add(models.Assignment(employee_id=employee_id, project_id=project_id))

    db.commit()
    return {"success": True}


@router.delete("/team-members/{project_id}/{employee_id}")
def remove_team_member(
    project_id: int,
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SUP: ลบพนักงานออกจากสังกัดของโครงการ"""
    if current_user.role not in ("admin", "sup"):
        raise HTTPException(403, "เฉพาะ Admin/SUP")
    q = db.query(models.SupTeamMember).filter(
        models.SupTeamMember.project_id == project_id,
        models.SupTeamMember.employee_id == employee_id,
    )
    if current_user.role == "sup":
        q = q.filter(models.SupTeamMember.sup_user_id == current_user.id)
    q.delete()

    # ── sync Assignment — deactivate ────────────────────
    assign = db.query(models.Assignment).filter(
        models.Assignment.project_id == project_id,
        models.Assignment.employee_id == employee_id,
        models.Assignment.is_active == True,
    ).first()
    if assign:
        assign.is_active = False
        assign.unassigned_at = datetime.utcnow()

    db.commit()
    return {"success": True}


# ── GET /team ─────────────────────────────────────────

@router.get("/team")
def team_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SUP: overview โครงการในสังกัด + สถิติทีมแต่ละโครงการ"""
    if current_user.role not in ("admin", "sup"):
        raise HTTPException(403, "เฉพาะ Admin/SUP")

    quotas = _get_quotas(db)
    now = datetime.now()

    if current_user.role == "sup":
        projects = db.query(models.Project).filter(
            models.Project.sup_user_id == current_user.id,
            models.Project.is_active == True,
        ).all()
    else:
        projects = db.query(models.Project).filter(models.Project.is_active == True).all()

    result_projects = []
    for proj in projects:
        # ดึง member ทั้งหมดของโครงการ (ไม่ filter by sup_user_id เพราะ Admin อาจเป็นคนเพิ่ม)
        member_rows = db.query(models.SupTeamMember).filter(
            models.SupTeamMember.project_id == proj.id,
        ).all()

        employees_out = []
        for m in member_rows:
            emp = m.employee
            if not emp or not emp.is_active:
                continue
            try:
                s = _employee_stats(db, emp.id, quotas)
            except Exception:
                s = {"late_month":0,"absent_year":0,"leave_days_year":0,"leave_remaining_year":0,"ot_hours_month":0,"ot_hours_year":0,"pending_leave":0,"pending_ot":0}
            warnings = []
            if s["late_month"] >= quotas["quota_late_per_month"]:
                warnings.append(f"มาสาย {s['late_month']} ครั้ง")
            if s["absent_year"] >= quotas["quota_absent_per_year"]:
                warnings.append(f"ขาด {s['absent_year']} วัน")
            if s["leave_remaining_year"] <= 0:
                warnings.append("ลาครบโควต้า")

            employees_out.append({
                "employee_id": emp.id,
                "employee_code": emp.employee_code,
                "name": f"{emp.first_name} {emp.last_name}",
                "department": emp.department or "",
                "late_month": s["late_month"],
                "absent_year": s["absent_year"],
                "leave_days_year": s["leave_days_year"],
                "leave_remaining_year": s["leave_remaining_year"],
                "ot_hours_month": s["ot_hours_month"],
                "pending_leave": s["pending_leave"],
                "pending_ot": s["pending_ot"],
                "status": ("⚠️ " + ", ".join(warnings)) if warnings else "ปกติ",
                "warnings": warnings,
            })

        result_projects.append({
            "project_id": proj.id,
            "project_name": proj.name,
            "employee_count": len(employees_out),
            "employees": employees_out,
        })

    return {
        "projects": result_projects,
        "quotas": quotas,
        "month": now.strftime("%B %Y"),
        "year": str(now.year),
    }


# ── GET /admin ───────────────────────────────────────

@router.get("/admin")
def admin_overview(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Admin: ภาพรวมทั้งองค์กร"""
    if current_user.role != "admin":
        raise HTTPException(403, "เฉพาะ Admin")

    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    year_str = str(now.year)

    total_emp = db.query(models.Employee).filter(models.Employee.is_active == True).count()
    total_proj = db.query(models.Project).filter(models.Project.is_active == True).count()
    total_users = db.query(models.User).filter(models.User.is_active == True).count()

    # pending approvals
    pending_leave = db.query(models.LeaveRequest).filter(models.LeaveRequest.status == "pending").count()
    pending_ot = db.query(models.OTRequest).filter(models.OTRequest.status == "pending").count()

    # checkin วันนี้
    today_str = now.strftime("%Y-%m-%d")
    checkin_today = db.query(models.CheckIn).filter(
        models.CheckIn.work_date == today_str,
        models.CheckIn.check_in_time != None,
    ).count()

    # audit logs วันนี้
    logs_today = db.query(models.AuditLog).filter(
        func.date(models.AuditLog.timestamp) == now.date()
    ).count()

    # recent audit
    recent_logs = db.query(models.AuditLog).order_by(
        models.AuditLog.timestamp.desc()
    ).limit(10).all()

    return {
        "employees": total_emp,
        "projects": total_proj,
        "users": total_users,
        "pending_leave": pending_leave,
        "pending_ot": pending_ot,
        "checkin_today": checkin_today,
        "audit_today": logs_today,
        "recent_logs": [
            {
                "id": l.id,
                "username": l.username,
                "action": l.action,
                "description": l.description,
                "timestamp": l.timestamp.isoformat() if l.timestamp else None,
            }
            for l in recent_logs
        ],
    }


# ── Aliases for frontend compatibility ───────────────
@router.get("/my-stats")
def my_stats_alias(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return my_stats(db=db, current_user=current_user)

@router.get("/team-stats")
def team_stats_alias(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return team_stats(db=db, current_user=current_user)