"""
KPI Module — M5
4 Factors: HR(0.3) | Project(0.3) | Elearning(0.2) | Achievement(0.2)
- Admin: จัดการ period, ตั้ง factor, Confirm, Publish, เห็นทุกคน + Ranking, ให้ Achievement SUP
- SUP:   เห็น KPI ทีม, ให้ Achievement คะแนน employee
- Employee: เห็น KPI ตัวเองเฉพาะเมื่อ Admin Publish
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel

from database import get_db
import models
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/v1/kpi", tags=["kpi"])


# ─── Schemas ───────────────────────────────────────────────

class PeriodCreate(BaseModel):
    name: str
    period_type: str = "monthly"
    start_date: str
    end_date: str
    factor_hr: float = 0.3
    factor_project: float = 0.3
    factor_elearning: float = 0.2
    factor_achievement: float = 0.2

class PeriodUpdate(BaseModel):
    name: Optional[str] = None
    period_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    factor_hr: Optional[float] = None
    factor_project: Optional[float] = None
    factor_elearning: Optional[float] = None
    factor_achievement: Optional[float] = None

class AchievementInput(BaseModel):
    score_achievement: float   # 0-100
    achievement_note: Optional[str] = None

class ScoreOverrideInput(BaseModel):
    score_hr: Optional[float] = None
    score_project: Optional[float] = None
    score_elearning: Optional[float] = None
    score_achievement: Optional[float] = None
    achievement_note: Optional[str] = None


# ─── Helpers ───────────────────────────────────────────────

def _recalc_total(score: models.KPIScore, period: models.KPIPeriod):
    """คำนวณ weighted total จาก 4 factors"""
    vals = [
        (score.score_hr or 0, period.factor_hr),
        (score.score_project or 0, period.factor_project),
        (score.score_elearning or 0, period.factor_elearning),
        (score.score_achievement or 0, period.factor_achievement),
    ]
    score.score_total = sum(v * w for v, w in vals)


def _auto_calc_hr(employee_id: int, start_date: str, end_date: str, db: Session) -> float:
    """
    HR KPI: วัดจาก check-in สม่ำเสมอ
    = (จำนวนวันที่ check-in / จำนวนวันทำงานในช่วง) * 100
    """
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return 0.0

    # นับวันทำงาน (จันทร์-ศุกร์) ในช่วง
    from datetime import timedelta as _td
    total_workdays = sum(
        1 for n in range((ed - sd).days + 1)
        if (sd + _td(days=n)).weekday() < 5
    )
    if total_workdays == 0:
        return 0.0

    # นับ check-in ใน period
    checkins = db.query(func.count(models.CheckIn.id)).filter(
        models.CheckIn.employee_id == employee_id,
        models.CheckIn.check_in_time >= datetime.strptime(start_date, "%Y-%m-%d"),
        models.CheckIn.check_in_time <= datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S"),
    ).scalar() or 0

    return min(round((checkins / total_workdays) * 100, 2), 100.0)


def _auto_calc_elearning(user_id: int, start_date: str, end_date: str, db: Session) -> float:
    """
    E-learning KPI: % เนื้อหาที่ complete ในช่วง
    """
    total = db.query(func.count(models.ElearningContent.id)).filter(
        models.ElearningContent.is_active == True
    ).scalar() or 0
    if total == 0:
        return 100.0

    completed = db.query(func.count(models.ElearningLog.id)).filter(
        models.ElearningLog.user_id == user_id,
        models.ElearningLog.completed == True,
        models.ElearningLog.completed_at >= datetime.strptime(start_date, "%Y-%m-%d"),
        models.ElearningLog.completed_at <= datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S"),
    ).scalar() or 0

    return min(round((completed / total) * 100, 2), 100.0)


def _auto_calc_project(employee_id: int, start_date: str, end_date: str, db: Session) -> float:
    """
    Project KPI: % วันที่ check-in สำเร็จ (check_in_ok=True) ใน period
    """
    sd = datetime.strptime(start_date, "%Y-%m-%d")
    ed = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")

    ok_ci = db.query(func.count(models.CheckIn.id)).filter(
        models.CheckIn.employee_id == employee_id,
        models.CheckIn.check_in_time >= sd,
        models.CheckIn.check_in_time <= ed,
        models.CheckIn.check_in_ok == True,
    ).scalar() or 0

    total_ci = db.query(func.count(models.CheckIn.id)).filter(
        models.CheckIn.employee_id == employee_id,
        models.CheckIn.check_in_time >= sd,
        models.CheckIn.check_in_time <= ed,
    ).scalar() or 0

    if total_ci == 0:
        return 0.0
    return min(round((ok_ci / total_ci) * 100, 2), 100.0)


def _get_or_create_score(period_id: int, employee_id: int, db: Session) -> models.KPIScore:
    score = db.query(models.KPIScore).filter_by(
        period_id=period_id, employee_id=employee_id
    ).first()
    if not score:
        score = models.KPIScore(period_id=period_id, employee_id=employee_id)
        db.add(score)
        db.flush()
    return score


def _rank_scores(result: list) -> list:
    """Sort by score_total desc and assign rank"""
    result.sort(key=lambda x: (x["score_total"] or 0), reverse=True)
    for i, r in enumerate(result):
        r["rank"] = i + 1
    return result


def _get_employee_projects(emp_id: int, db) -> str:
    """ดึงชื่อโครงการที่พนักงานสังกัด (comma-separated)"""
    seen = set()
    names = []
    # 1. SUP role — project.sup_user_id
    user = db.query(models.User).filter_by(employee_id=emp_id, role="sup").first()
    if user:
        for p in db.query(models.Project).filter_by(sup_user_id=user.id, is_active=True).all():
            if p.id not in seen:
                seen.add(p.id); names.append(p.name)
    # 2. Assignment
    for a in db.query(models.Assignment).filter_by(employee_id=emp_id, is_active=True).all():
        proj = db.query(models.Project).get(a.project_id)
        if proj and proj.id not in seen:
            seen.add(proj.id); names.append(proj.name)
    # 3. SupTeamMember
    for m in db.query(models.SupTeamMember).filter_by(employee_id=emp_id).all():
        proj = db.query(models.Project).get(m.project_id)
        if proj and proj.is_active and proj.id not in seen:
            seen.add(proj.id); names.append(proj.name)
    return ", ".join(names) if names else "-"


def _score_to_dict(s: models.KPIScore, period: models.KPIPeriod, db=None) -> dict:
    emp = s.employee
    project_name = _get_employee_projects(emp.id, db) if emp and db else "-"
    # detect SUP role
    is_sup = False
    if emp and db:
        sup_user = db.query(models.User).filter_by(employee_id=emp.id, role="sup").first()
        is_sup = sup_user is not None
    return {
        "id": s.id,
        "period_id": s.period_id,
        "employee_id": s.employee_id,
        "is_sup": is_sup,
        "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "",
        "employee_code": emp.employee_code if emp else "",
        "department": emp.department if emp else "",
        "photo_url": emp.photo_url if emp else None,
        "project_name": project_name,
        "score_hr": s.score_hr,
        "score_project": s.score_project,
        "score_elearning": s.score_elearning,
        "score_achievement": s.score_achievement,
        "score_total": round(s.score_total, 2) if s.score_total is not None else None,
        "achievement_note": s.achievement_note,
        "achievement_at": s.achievement_at.isoformat() if s.achievement_at else None,
        "locked_at": s.locked_at.isoformat() if s.locked_at else None,
        "factors": {
            "hr": period.factor_hr,
            "project": period.factor_project,
            "elearning": period.factor_elearning,
            "achievement": period.factor_achievement,
        }
    }


# ─── Period Endpoints ───────────────────────────────────────

@router.get("/periods")
def list_periods(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    periods = db.query(models.KPIPeriod).order_by(models.KPIPeriod.start_date.desc()).all()
    result = []
    for p in periods:
        result.append({
            "id": p.id,
            "name": p.name,
            "period_type": p.period_type,
            "start_date": p.start_date,
            "end_date": p.end_date,
            "factor_hr": p.factor_hr,
            "factor_project": p.factor_project,
            "factor_elearning": p.factor_elearning,
            "factor_achievement": p.factor_achievement,
            "is_published": p.is_published,
            "is_closed": p.is_closed,
            "score_count": len(p.scores),
        })
    return result


@router.post("/periods")
def create_period(
    body: PeriodCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    total = body.factor_hr + body.factor_project + body.factor_elearning + body.factor_achievement
    if abs(total - 1.0) > 0.01:
        raise HTTPException(400, "Factor weights ต้องรวมกันได้ 1.0")

    p = models.KPIPeriod(
        name=body.name,
        period_type=body.period_type,
        start_date=body.start_date,
        end_date=body.end_date,
        factor_hr=body.factor_hr,
        factor_project=body.factor_project,
        factor_elearning=body.factor_elearning,
        factor_achievement=body.factor_achievement,
        created_by=current_user.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "name": p.name, "message": "สร้าง KPI Period สำเร็จ"}


@router.put("/periods/{period_id}")
def update_period(
    period_id: int,
    body: PeriodUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    if p.is_closed:
        raise HTTPException(400, "Period นี้ปิดแล้ว ไม่สามารถแก้ไขได้")

    for field in ("name", "period_type", "start_date", "end_date",
                  "factor_hr", "factor_project", "factor_elearning", "factor_achievement"):
        val = getattr(body, field)
        if val is not None:
            setattr(p, field, val)

    # validate factors
    total = p.factor_hr + p.factor_project + p.factor_elearning + p.factor_achievement
    if abs(total - 1.0) > 0.01:
        raise HTTPException(400, "Factor weights ต้องรวมกันได้ 1.0")

    db.commit()
    return {"message": "อัปเดต Period สำเร็จ"}


@router.post("/periods/{period_id}/publish")
def toggle_publish(
    period_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    p.is_published = not p.is_published
    db.commit()
    return {"is_published": p.is_published, "message": "ประกาศ KPI" if p.is_published else "ยกเลิกประกาศ KPI"}


@router.post("/periods/{period_id}/close")
def close_period(
    period_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """ปิด Period — ล็อกทุก score"""
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    if p.is_closed:
        raise HTTPException(400, "Period ปิดแล้ว")
    p.is_closed = True
    now = datetime.utcnow()
    for s in p.scores:
        if not s.locked_at:
            s.locked_at = now
    db.commit()
    return {"message": "ปิด Period และล็อก KPI ทั้งหมดแล้ว"}


@router.delete("/periods/{period_id}")
def delete_period(
    period_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """ลบ KPI Period (และ scores ทั้งหมดใน period นั้น)"""
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    db.delete(p)
    db.commit()
    return {"message": f"ลบ Period '{p.name}' เรียบร้อย"}


# ─── Score Endpoints ────────────────────────────────────────

@router.post("/periods/{period_id}/calculate")
def calculate_scores(
    period_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """คำนวณ HR/Project/Elearning KPI อัตโนมัติสำหรับทุกพนักงาน"""
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    if p.is_closed:
        raise HTTPException(400, "Period ปิดแล้ว")

    employees = db.query(models.Employee).filter(models.Employee.is_active == True).all()
    updated = 0

    for emp in employees:
        user = db.query(models.User).filter(
            models.User.employee_id == emp.id,
            models.User.is_active == True
        ).first()

        score = _get_or_create_score(period_id, emp.id, db)
        score.score_hr = _auto_calc_hr(emp.id, p.start_date, p.end_date, db)
        score.score_project = _auto_calc_project(emp.id, p.start_date, p.end_date, db)
        score.score_elearning = _auto_calc_elearning(user.id if user else 0, p.start_date, p.end_date, db)
        _recalc_total(score, p)
        updated += 1

    db.commit()
    return {"message": f"คำนวณ KPI สำเร็จ {updated} คน"}


@router.get("/periods/{period_id}/scores")
def get_scores(
    period_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")

    if current_user.role == "employee":
        # เห็นตัวเองเมื่อ published เท่านั้น
        if not p.is_published:
            raise HTTPException(403, "KPI ยังไม่ประกาศ")
        if not current_user.employee_id:
            raise HTTPException(403, "ไม่มีข้อมูลพนักงาน")
        scores = db.query(models.KPIScore).filter_by(
            period_id=period_id, employee_id=current_user.employee_id
        ).all()
        return _rank_scores([_score_to_dict(s, p, db) for s in scores])

    elif current_user.role == "sup":
        team_ids = [
            m.employee_id for m in db.query(models.SupTeamMember).filter_by(
                sup_user_id=current_user.id
            ).all()
        ]
        proj_ids = [
            pr.id for pr in db.query(models.Project).filter_by(
                sup_user_id=current_user.id
            ).all()
        ]
        assign_ids = [
            a.employee_id for a in db.query(models.Assignment).filter(
                models.Assignment.project_id.in_(proj_ids),
                models.Assignment.is_active == True
            ).all()
        ] if proj_ids else []
        all_ids = list(set(team_ids + assign_ids))

        scores = db.query(models.KPIScore).filter(
            models.KPIScore.period_id == period_id,
            models.KPIScore.employee_id.in_(all_ids)
        ).all()
        return _rank_scores([_score_to_dict(s, p, db) for s in scores])

    else:
        # Admin เห็นทุกคน
        scores = db.query(models.KPIScore).filter_by(period_id=period_id).all()
        return _rank_scores([_score_to_dict(s, p, db) for s in scores])


@router.get("/periods/{period_id}/scores/my")
def get_my_score(
    period_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Employee ดู KPI ตัวเอง"""
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    if current_user.role == "employee" and not p.is_published:
        raise HTTPException(403, "KPI ยังไม่ประกาศ")

    emp_id = current_user.employee_id
    if not emp_id:
        raise HTTPException(400, "ไม่มีข้อมูลพนักงาน")

    score = db.query(models.KPIScore).filter_by(
        period_id=period_id, employee_id=emp_id
    ).first()
    if not score:
        raise HTTPException(404, "ยังไม่มีคะแนน KPI ในช่วงนี้")
    return _score_to_dict(score, p, db)


@router.put("/periods/{period_id}/scores/{employee_id}/achievement")
def give_achievement(
    period_id: int,
    employee_id: int,
    body: AchievementInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """SUP ให้ Achievement score พนักงาน / Admin ให้ Achievement SUP"""
    if current_user.role not in ("sup", "admin"):
        raise HTTPException(403, "ไม่มีสิทธิ์")
    # SUP ห้ามให้คะแนนตัวเอง
    if current_user.role == "sup" and current_user.employee_id == employee_id:
        raise HTTPException(403, "ไม่สามารถให้คะแนน Achievement ตัวเองได้")
    if body.score_achievement < 0 or body.score_achievement > 100:
        raise HTTPException(400, "คะแนนต้องอยู่ระหว่าง 0-100")

    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    if p.is_closed:
        raise HTTPException(400, "Period ปิดแล้ว")

    score = _get_or_create_score(period_id, employee_id, db)
    score.score_achievement = body.score_achievement
    score.achievement_note = body.achievement_note
    score.achievement_by = current_user.id
    score.achievement_at = datetime.utcnow()
    _recalc_total(score, p)
    db.commit()
    return {"message": "บันทึก Achievement KPI สำเร็จ", "score_total": round(score.score_total, 2)}


@router.put("/periods/{period_id}/scores/{employee_id}/override")
def override_score(
    period_id: int,
    employee_id: int,
    body: ScoreOverrideInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """Admin แก้ไขคะแนนแต่ละ factor ได้ (Manual override)"""
    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")
    if p.is_closed:
        raise HTTPException(400, "Period ปิดแล้ว")

    score = _get_or_create_score(period_id, employee_id, db)
    if body.score_hr is not None:
        score.score_hr = body.score_hr
    if body.score_project is not None:
        score.score_project = body.score_project
    if body.score_elearning is not None:
        score.score_elearning = body.score_elearning
    if body.score_achievement is not None:
        score.score_achievement = body.score_achievement
        score.achievement_note = body.achievement_note
        score.achievement_by = current_user.id
        score.achievement_at = datetime.utcnow()
    _recalc_total(score, p)
    db.commit()
    return {"message": "อัปเดตคะแนน KPI สำเร็จ", "score_total": round(score.score_total, 2)}


# ─── KPI Export ──────────────────────────────────────────────

@router.get("/periods/{period_id}/scores/export")
def export_kpi_csv(
    period_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """Export KPI scores ทั้งหมดเป็น CSV"""
    import csv, io
    from fastapi.responses import StreamingResponse

    p = db.query(models.KPIPeriod).get(period_id)
    if not p:
        raise HTTPException(404, "ไม่พบ Period")

    scores = db.query(models.KPIScore).filter_by(period_id=period_id).all()
    result = _rank_scores([_score_to_dict(s, p, db) for s in scores])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "อันดับ", "รหัสพนักงาน", "ชื่อ-นามสกุล", "แผนก", "โครงการ", "ประเภท",
        "HR KPI (%)", "Project KPI (%)", "E-learning KPI (%)", "Achievement KPI (%)", "คะแนนรวม (%)", "Grade"
    ])

    def grade(v):
        if v is None: return "-"
        if v >= 90: return "A"
        if v >= 80: return "B+"
        if v >= 70: return "B"
        if v >= 60: return "C+"
        if v >= 50: return "C"
        return "D"

    # แยก SUP / Employee
    sup_user_ids = {u.employee_id for u in db.query(models.User).filter(
        models.User.role == "sup", models.User.is_active == True
    ).all() if u.employee_id}

    for r in result:
        role_label = "SUP" if r["employee_id"] in sup_user_ids else "พนักงาน"
        writer.writerow([
            r.get("rank", ""),
            r["employee_code"],
            r["employee_name"],
            r["department"] or "",
            r["project_name"] or "",
            role_label,
            f"{r['score_hr']:.1f}" if r["score_hr"] is not None else "-",
            f"{r['score_project']:.1f}" if r["score_project"] is not None else "-",
            f"{r['score_elearning']:.1f}" if r["score_elearning"] is not None else "-",
            f"{r['score_achievement']:.1f}" if r["score_achievement"] is not None else "-",
            f"{r['score_total']:.1f}" if r["score_total"] is not None else "-",
            grade(r["score_total"]),
        ])

    output.seek(0)
    filename = f"kpi_{p.name.replace('/', '-').replace(' ', '_')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )



@router.get("/projects/{project_id}/history")
def get_project_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    records = db.query(models.ProjectHistory).filter_by(project_id=project_id).all()
    return [
        {
            "id": r.id,
            "employee_id": r.employee_id,
            "employee_name": f"{r.employee.first_name} {r.employee.last_name}" if r.employee else "",
            "employee_code": r.employee.employee_code if r.employee else "",
            "role": r.role,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "note": r.note,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in records
    ]


@router.get("/employees/{employee_id}/history")
def get_employee_history(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role == "employee" and current_user.employee_id != employee_id:
        raise HTTPException(403, "ไม่มีสิทธิ์")
    records = db.query(models.ProjectHistory).filter_by(employee_id=employee_id).all()
    return [
        {
            "id": r.id,
            "project_id": r.project_id,
            "project_name": r.project.name if r.project else "",
            "role": r.role,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "note": r.note,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in records
    ]


@router.get("/my-summary")
def my_kpi_summary(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if not current_user.employee_id:
        return {"has_kpi": False}
    period = db.query(models.KPIPeriod).filter(
        models.KPIPeriod.is_published == True
    ).order_by(models.KPIPeriod.end_date.desc()).first()
    if not period:
        return {"has_kpi": False}
    score = db.query(models.KPIScore).filter_by(
        period_id=period.id, employee_id=current_user.employee_id
    ).first()
    if not score:
        return {"has_kpi": False}
    return {
        "has_kpi": True,
        "period_name": period.name,
        "score_hr": score.score_hr,
        "score_project": score.score_project,
        "score_elearning": score.score_elearning,
        "score_achievement": score.score_achievement,
        "score_total": round(score.score_total, 2) if score.score_total is not None else None,
    }


# ─── Project Closure Report ───────────────────────────────────

import json as _json

def _days_between(start: str, end: str) -> int:
    """คำนวณจำนวนวันระหว่าง 2 วันที่ (string YYYY-MM-DD)"""
    try:
        from datetime import datetime as _dt2
        a = _dt2.strptime(start, "%Y-%m-%d")
        b = _dt2.strptime(end, "%Y-%m-%d")
        return max(0, (b - a).days)
    except Exception:
        return 0


def _build_closure_email(report: dict, company_name: str = "Head Office ZL") -> str:
    """สร้าง HTML email สำหรับส่งแจ้งปิดโครงการ"""
    rows = ""
    for m in (report.get("team_snapshot") or []):
        badge = "🔵 SUP" if m["role"] == "sup" else "👤 พนักงาน"
        rows += f"<tr><td style='padding:6px 12px;border-bottom:1px solid #f1f5f9'>{m['name']}</td><td style='padding:6px 12px;border-bottom:1px solid #f1f5f9'>{badge}</td><td style='padding:6px 12px;border-bottom:1px solid #f1f5f9;color:#64748b'>{m.get('department','')}</td></tr>"
    complaints_html = ""
    for c in (report.get("complaint_detail") or []):
        complaints_html += f"<li style='margin-bottom:4px'><strong>{c['category']}</strong>: {c['description'][:120]}{'...' if len(c['description'])>120 else ''}</li>"
    if not complaints_html:
        complaints_html = "<li style='color:#94a3b8'>ไม่มีข้อร้องเรียน</li>"

    return f"""
<div style="font-family:sans-serif;max-width:640px;margin:auto;background:#f8fafc;padding:32px;border-radius:16px">
  <div style="background:linear-gradient(135deg,#1e293b,#334155);border-radius:12px;padding:28px;text-align:center;margin-bottom:28px">
    <div style="font-size:36px;margin-bottom:8px">🏗️</div>
    <h1 style="color:#fff;margin:0;font-size:22px">{company_name}</h1>
    <p style="color:#94a3b8;margin:4px 0 0">รายงานสรุปการปิดโครงการ</p>
  </div>

  <div style="background:#fff;border-radius:12px;padding:24px;margin-bottom:16px;border-left:4px solid #ef4444">
    <h2 style="margin:0 0 4px;color:#1e293b;font-size:20px">📋 {report['project_name']}</h2>
    <p style="color:#64748b;margin:0;font-size:14px">ปิดโครงการเมื่อ {report['end_date']}</p>
  </div>

  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">
    <div style="background:#fff;border-radius:10px;padding:16px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#3b82f6">{report.get('total_days','—')}</div>
      <div style="font-size:12px;color:#64748b">วันทั้งหมด</div>
    </div>
    <div style="background:#fff;border-radius:10px;padding:16px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#10b981">{report.get('employee_count',0)}</div>
      <div style="font-size:12px;color:#64748b">พนักงานทั้งหมด</div>
    </div>
    <div style="background:#fff;border-radius:10px;padding:16px;text-align:center">
      <div style="font-size:28px;font-weight:700;color:#f59e0b">{report.get('complaint_count',0)}</div>
      <div style="font-size:12px;color:#64748b">ข้อร้องเรียน</div>
    </div>
  </div>

  {'<div style="background:#fff;border-radius:12px;padding:20px;margin-bottom:16px"><h3 style="margin:0 0 8px;color:#1e293b;font-size:15px">📝 สรุปผลโครงการ</h3><p style="color:#475569;margin:0;font-size:14px;line-height:1.6">'+report["summary"]+'</p></div>' if report.get("summary") else ''}
  {'<div style="background:#fff5f5;border-radius:12px;padding:20px;margin-bottom:16px;border-left:3px solid #fca5a5"><h3 style="margin:0 0 8px;color:#dc2626;font-size:15px">⚠️ ปัญหา/อุปสรรค</h3><p style="color:#475569;margin:0;font-size:14px;line-height:1.6">'+report["obstacles"]+'</p></div>' if report.get("obstacles") else ''}
  {'<div style="background:#f0fdf4;border-radius:12px;padding:20px;margin-bottom:16px;border-left:3px solid #86efac"><h3 style="margin:0 0 8px;color:#16a34a;font-size:15px">💡 บทเรียนสำหรับโครงการถัดไป</h3><p style="color:#475569;margin:0;font-size:14px;line-height:1.6">'+report["lessons_learned"]+'</p></div>' if report.get("lessons_learned") else ''}

  <div style="background:#fff;border-radius:12px;padding:20px;margin-bottom:16px">
    <h3 style="margin:0 0 12px;color:#1e293b;font-size:15px">👥 ทีมงาน ({report.get('employee_count',0)} คน)</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#f8fafc"><th style="padding:8px 12px;text-align:left;color:#64748b">ชื่อ</th><th style="padding:8px 12px;text-align:left;color:#64748b">บทบาท</th><th style="padding:8px 12px;text-align:left;color:#64748b">แผนก</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <div style="background:#fff;border-radius:12px;padding:20px;margin-bottom:16px">
    <h3 style="margin:0 0 12px;color:#1e293b;font-size:15px">📣 ข้อร้องเรียนในโครงการ</h3>
    <ul style="margin:0;padding-left:20px;font-size:13px;color:#475569">{complaints_html}</ul>
  </div>

  <p style="color:#94a3b8;font-size:11px;text-align:center;margin-top:24px">
    รายงานนี้สร้างโดยระบบ {company_name} โดยอัตโนมัติ
  </p>
</div>"""


@router.post("/projects/{project_id}/close")
def close_project(
    project_id: int,
    note: Optional[str] = None,
    summary: Optional[str] = None,
    obstacles: Optional[str] = None,
    lessons_learned: Optional[str] = None,
    notify_emails: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """
    ปิดโครงการ:
    1. บันทึก ProjectHistory + ProjectClosureReport
    2. ปลด Assignment + SupTeamMember
    3. ตั้ง Project.is_active = False
    4. ส่ง email แจ้ง (ถ้า notify_emails ระบุ)
    """
    from datetime import date
    proj = db.query(models.Project).get(project_id)
    if not proj:
        raise HTTPException(404, "ไม่พบโครงการ")
    if not proj.is_active:
        raise HTTPException(400, "โครงการนี้ปิดแล้ว")

    today = date.today().isoformat()
    recorded = []
    team_snapshot = []

    # ── บันทึก SUP ──
    if proj.sup_user_id:
        sup_user = db.query(models.User).get(proj.sup_user_id)
        if sup_user and sup_user.employee_id:
            emp = db.query(models.Employee).get(sup_user.employee_id)
            db.add(models.ProjectHistory(
                employee_id=sup_user.employee_id,
                project_id=project_id,
                role="sup",
                start_date=proj.start_date,
                end_date=today,
                note=note,
            ))
            recorded.append(f"SUP:{sup_user.employee_id}")
            if emp:
                team_snapshot.append({
                    "id": emp.id, "name": f"{emp.first_name} {emp.last_name}",
                    "employee_code": emp.employee_code, "department": emp.department or "",
                    "role": "sup"
                })

    # ── บันทึก Assignments ──
    assigns = db.query(models.Assignment).filter_by(project_id=project_id, is_active=True).all()
    for a in assigns:
        emp = db.query(models.Employee).get(a.employee_id)
        db.add(models.ProjectHistory(
            employee_id=a.employee_id, project_id=project_id,
            role="employee", start_date=proj.start_date, end_date=today, note=note,
        ))
        recorded.append(f"emp:{a.employee_id}")
        a.is_active = False
        if emp:
            team_snapshot.append({
                "id": emp.id, "name": f"{emp.first_name} {emp.last_name}",
                "employee_code": emp.employee_code, "department": emp.department or "",
                "role": "employee"
            })

    # ── บันทึก SupTeamMember ──
    team = db.query(models.SupTeamMember).filter_by(project_id=project_id).all()
    for m in team:
        if f"emp:{m.employee_id}" not in recorded:
            emp = db.query(models.Employee).get(m.employee_id)
            db.add(models.ProjectHistory(
                employee_id=m.employee_id, project_id=project_id,
                role="employee", start_date=proj.start_date, end_date=today, note=note,
            ))
            if emp:
                team_snapshot.append({
                    "id": emp.id, "name": f"{emp.first_name} {emp.last_name}",
                    "employee_code": emp.employee_code, "department": emp.department or "",
                    "role": "employee"
                })
        db.delete(m)

    # ── รวบรวม stats ──
    sup_count = sum(1 for t in team_snapshot if t["role"] == "sup")
    emp_count = len(team_snapshot)
    total_days = _days_between(proj.start_date, today) if proj.start_date else None

    # ── Complaints ในโครงการนี้ ──
    complaints = db.query(models.Complaint).filter_by(project_id=project_id).all()
    complaint_count = len(complaints)
    complaint_detail = [
        {"id": c.id, "category": c.category or "ทั่วไป",
         "description": c.description or "", "status": c.status or "pending",
         "created_at": c.created_at.isoformat() if c.created_at else ""}
        for c in complaints
    ]

    # ── avg checkin rate (จากทุกคนในทีม) ──
    avg_checkin = None
    if proj.start_date and team_snapshot:
        from datetime import datetime as _dt2
        try:
            sd = _dt2.strptime(proj.start_date, "%Y-%m-%d")
            ed = _dt2.strptime(today, "%Y-%m-%d")
            total_checkins = db.query(models.CheckIn).filter(
                models.CheckIn.project_id == project_id,
                models.CheckIn.check_in_time >= sd,
                models.CheckIn.check_in_time <= ed,
            ).count()
            if total_days and emp_count:
                avg_checkin = round(total_checkins / (emp_count * max(total_days, 1)) * 100, 1)
        except Exception:
            pass

    # ── avg KPI (จาก KPIScore ถ้ามี) ──
    avg_kpi = None
    emp_ids = [t["id"] for t in team_snapshot]
    if emp_ids:
        kpi_scores = db.query(models.KPIScore).filter(
            models.KPIScore.employee_id.in_(emp_ids),
            models.KPIScore.score_total.isnot(None)
        ).all()
        if kpi_scores:
            avg_kpi = round(sum(s.score_total for s in kpi_scores) / len(kpi_scores), 1)

    # ── บันทึก Closure Report ──
    report = models.ProjectClosureReport(
        project_id=project_id,
        project_name=proj.name,
        start_date=proj.start_date,
        end_date=today,
        total_days=total_days,
        employee_count=emp_count,
        sup_count=sup_count,
        complaint_count=complaint_count,
        avg_checkin_rate=avg_checkin,
        avg_kpi_score=avg_kpi,
        summary=summary,
        obstacles=obstacles,
        lessons_learned=lessons_learned,
        complaint_detail=_json.dumps(complaint_detail, ensure_ascii=False),
        team_snapshot=_json.dumps(team_snapshot, ensure_ascii=False),
        notify_emails=notify_emails,
        closed_by=current_user.id,
    )
    db.add(report)

    # ── ปิดโครงการ ──
    from datetime import datetime as _dtnow
    proj.is_active = False
    proj.sup_user_id = None
    proj.sup_name = None
    proj.closed_at = _dtnow.utcnow()

    db.commit()
    db.refresh(report)

    # ── ส่ง Email ──
    email_result = {"sent": False}
    if notify_emails:
        from routers.email_utils import send_email
        setting = db.query(models.Setting).filter_by(key="company_name").first()
        company = setting.value if setting else "Head Office ZL"
        report_dict = {
            "project_name": proj.name,
            "start_date": proj.start_date or "—",
            "end_date": today,
            "total_days": total_days,
            "employee_count": emp_count,
            "complaint_count": complaint_count,
            "summary": summary or "",
            "obstacles": obstacles or "",
            "lessons_learned": lessons_learned or "",
            "team_snapshot": team_snapshot,
            "complaint_detail": complaint_detail,
        }
        html = _build_closure_email(report_dict, company)
        subject = f"[{company}] ปิดโครงการ: {proj.name}"
        sent_ok = []
        for email in notify_emails.split(","):
            email = email.strip()
            if email:
                r = send_email(db, email, subject, html)
                if r.get("success"):
                    sent_ok.append(email)
        report.email_sent = bool(sent_ok)
        report.notify_emails = ",".join(sent_ok)
        db.commit()
        email_result = {"sent": bool(sent_ok), "to": sent_ok}

    return {
        "message": f"ปิดโครงการ '{proj.name}' สำเร็จ บันทึกประวัติ {len(recorded)} คน",
        "report_id": report.id,
        "total_days": total_days,
        "employee_count": emp_count,
        "complaint_count": complaint_count,
        "email": email_result,
    }


@router.get("/projects/{project_id}/closure-report")
def get_closure_report(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    report = db.query(models.ProjectClosureReport).filter_by(
        project_id=project_id
    ).order_by(models.ProjectClosureReport.created_at.desc()).first()
    if not report:
        raise HTTPException(404, "ยังไม่มีรายงานปิดโครงการ")
    return {
        "id": report.id,
        "project_id": report.project_id,
        "project_name": report.project_name,
        "start_date": report.start_date,
        "end_date": report.end_date,
        "total_days": report.total_days,
        "employee_count": report.employee_count,
        "sup_count": report.sup_count,
        "complaint_count": report.complaint_count,
        "avg_checkin_rate": report.avg_checkin_rate,
        "avg_kpi_score": report.avg_kpi_score,
        "summary": report.summary,
        "obstacles": report.obstacles,
        "lessons_learned": report.lessons_learned,
        "complaint_detail": _json.loads(report.complaint_detail or "[]"),
        "team_snapshot": _json.loads(report.team_snapshot or "[]"),
        "notify_emails": report.notify_emails,
        "email_sent": report.email_sent,
        "closed_by": report.closer.username if report.closer else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


@router.get("/projects/{project_id}/closure-report/export")
def export_closure_report(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """Export Closure Report เป็น CSV"""
    import csv, io
    from fastapi.responses import StreamingResponse

    report = db.query(models.ProjectClosureReport).filter_by(
        project_id=project_id
    ).order_by(models.ProjectClosureReport.created_at.desc()).first()
    if not report:
        raise HTTPException(404, "ยังไม่มีรายงานปิดโครงการ")

    team = _json.loads(report.team_snapshot or "[]")
    complaints = _json.loads(report.complaint_detail or "[]")
    setting = db.query(models.Setting).filter_by(key="company_name").first()
    company = setting.value if setting else "Head Office ZL"

    output = io.StringIO()
    w = csv.writer(output)

    # Header section
    w.writerow([f"รายงานสรุปโครงการ — {company}"])
    w.writerow([])
    w.writerow(["โครงการ", report.project_name])
    w.writerow(["วันที่เริ่ม", report.start_date or "—"])
    w.writerow(["วันที่ปิด", report.end_date])
    w.writerow(["ระยะเวลา (วัน)", report.total_days or "—"])
    w.writerow(["จำนวนพนักงานทั้งหมด", report.employee_count])
    w.writerow(["SUP", report.sup_count])
    w.writerow(["ข้อร้องเรียน", report.complaint_count])
    w.writerow(["Check-in เฉลี่ย (%)", report.avg_checkin_rate or "—"])
    w.writerow(["KPI เฉลี่ย (%)", report.avg_kpi_score or "—"])
    w.writerow([])
    w.writerow(["สรุปผลโครงการ", report.summary or "—"])
    w.writerow(["ปัญหา/อุปสรรค", report.obstacles or "—"])
    w.writerow(["บทเรียน", report.lessons_learned or "—"])
    w.writerow([])

    # Team section
    w.writerow(["── รายชื่อทีมงาน ──"])
    w.writerow(["ลำดับ", "รหัสพนักงาน", "ชื่อ", "แผนก", "บทบาท"])
    for i, m in enumerate(team, 1):
        w.writerow([i, m.get("employee_code", ""), m.get("name", ""),
                    m.get("department", ""), "SUP" if m["role"] == "sup" else "พนักงาน"])
    w.writerow([])

    # Complaints section
    w.writerow(["── ข้อร้องเรียน ──"])
    w.writerow(["ลำดับ", "หมวด", "รายละเอียด", "สถานะ", "วันที่"])
    for i, c in enumerate(complaints, 1):
        w.writerow([i, c.get("category", ""), c.get("description", ""),
                    c.get("status", ""), c.get("created_at", "")[:10]])

    output.seek(0)
    safe_name = report.project_name.replace("/", "-").replace(" ", "_")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename=closure_{safe_name}.csv"}
    )
