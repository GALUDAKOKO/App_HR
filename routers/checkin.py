"""
M1 — Check-in / Check-out Router
- พนักงาน check-in ครั้งเดียวต่อวันต่อโครงการ
- ถ้าโครงการมีพิกัด → ตรวจ Haversine geofence (รัศมี geofence_radius_km)
- ถ้าโครงการไม่มีพิกัด → บันทึกตำแหน่งไว้ โดยไม่ block
- Admin/SUP ดู history ทั้งหมดได้, Employee เห็นของตัวเอง
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date, timezone, timedelta
import math, io

TZ_TH = timezone(timedelta(hours=7))

def now_th() -> datetime:
    """เวลาปัจจุบัน UTC+7 (ไม่มี tzinfo — เก็บเป็น naive datetime ใน DB)"""
    return datetime.now(TZ_TH).replace(tzinfo=None)

def today_th() -> str:
    """วันที่วันนี้ตามเวลาไทย YYYY-MM-DD"""
    return datetime.now(TZ_TH).strftime("%Y-%m-%d")

from database import get_db
import models
from auth import get_current_user, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/checkin", tags=["checkin"])


# ── Haversine ───────────────────────────────────
def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _out(c: models.CheckIn) -> dict:
    emp = c.employee
    return {
        "id": c.id,
        "employee_id": c.employee_id,
        "employee_code": emp.employee_code if emp else "",
        "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "",
        "project_id": c.project_id,
        "project_name": c.project.name if c.project else "",
        "work_date": c.work_date,
        "check_in_time": c.check_in_time.isoformat() if c.check_in_time else None,
        "check_in_lat": c.check_in_lat,
        "check_in_lng": c.check_in_lng,
        "check_in_dist": round(c.check_in_dist, 3) if c.check_in_dist is not None else None,
        "check_in_ok": c.check_in_ok,
        "check_out_time": c.check_out_time.isoformat() if c.check_out_time else None,
        "check_out_lat": c.check_out_lat,
        "check_out_lng": c.check_out_lng,
        "check_out_dist": round(c.check_out_dist, 3) if c.check_out_dist is not None else None,
        "check_out_ok": c.check_out_ok,
        "note": c.note or "",
        "work_hours": _work_hours(c),
    }


def _work_hours(c: models.CheckIn) -> Optional[float]:
    if c.check_in_time and c.check_out_time:
        delta = c.check_out_time - c.check_in_time
        return round(delta.seconds / 3600, 2)
    return None


# ── Check-in ────────────────────────────────────

@router.post("/in")
def do_checkin(body: dict,
               db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    """
    body: { lat, lng, project_id (optional), note (optional) }
    Employee check-in ได้เฉพาะของตัวเอง, Admin check-in แทนได้
    """
    emp_id = body.get("employee_id")
    if current_user.role == "employee":
        if not current_user.employee_id:
            raise HTTPException(400, "บัญชีนี้ยังไม่เชื่อมกับพนักงาน")
        emp_id = current_user.employee_id
    elif not emp_id:
        # SUP / Admin ที่ไม่ได้ระบุ employee_id → check-in ของตัวเองถ้ามีบัญชีผูกอยู่
        if not current_user.employee_id:
            raise HTTPException(400, "ระบุ employee_id หรือเชื่อมบัญชีกับพนักงานก่อน")
        emp_id = current_user.employee_id
    if not emp_id:
        raise HTTPException(400, "ระบุ employee_id")

    today = today_th()

    # หา active project ถ้าไม่ได้ส่งมา
    project_id = body.get("project_id")
    if not project_id:
        assign = db.query(models.Assignment).filter(
            models.Assignment.employee_id == emp_id,
            models.Assignment.is_active == True
        ).first()
        if assign:
            project_id = assign.project_id

    # ดึง project config (require_gps)
    proj = None
    if project_id:
        proj = db.query(models.Project).filter(models.Project.id == project_id).first()
    project_require_gps = bool(proj.require_gps) if proj and proj.require_gps is not None else False

    lat = body.get("lat")
    lng = body.get("lng")
    if project_require_gps:
        if lat is None or lng is None:
            raise HTTPException(400, "ต้องส่งพิกัด lat, lng")
    else:
        # No GPS mode — ใช้ None แทน ไม่ block
        lat = lat if lat is not None else None
        lng = lng if lng is not None else None

    # ตรวจ check-in ซ้ำในวันเดียว
    existing = db.query(models.CheckIn).filter(
        models.CheckIn.employee_id == emp_id,
        models.CheckIn.work_date == today,
        models.CheckIn.project_id == project_id,
    ).first()
    if existing and existing.check_in_time:
        raise HTTPException(400, f"Check-in วันนี้แล้ว เวลา {existing.check_in_time.strftime('%H:%M')}")

    # Geofence check (เฉพาะเมื่อ require_gps=True และมีพิกัดโครงการ)
    dist_km = None
    in_fence = None
    if project_require_gps and lat is not None and lng is not None and proj and proj.lat and proj.lng:
        dist_km = haversine_km(lat, lng, proj.lat, proj.lng)
        radius = proj.geofence_radius_km or 3.0
        in_fence = dist_km <= radius

    checkin = models.CheckIn(
        employee_id=emp_id,
        project_id=project_id,
        work_date=today,
        check_in_time=now_th(),
        check_in_lat=lat,
        check_in_lng=lng,
        check_in_dist=dist_km,
        check_in_ok=in_fence,
        note=body.get("note", ""),
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)

    log_msg = (
        f"Check-in emp_id={emp_id} (no GPS mode)" if not project_require_gps else
        f"Check-in emp_id={emp_id} dist={dist_km:.3f}km" if dist_km else
        f"Check-in emp_id={emp_id} (no geofence)"
    )
    log_action(db, current_user, "CREATE", "checkins", checkin.id, log_msg)

    result = _out(checkin)
    result["geofence_status"] = (
        "no_gps_mode" if not project_require_gps else
        "in" if in_fence else
        "out" if in_fence is False else
        "no_coords"
    )
    return result


# ── Check-out ───────────────────────────────────

@router.post("/out")
def do_checkout(body: dict,
                db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    emp_id = body.get("employee_id")
    if current_user.role == "employee":
        if not current_user.employee_id:
            raise HTTPException(400, "บัญชีนี้ยังไม่เชื่อมกับพนักงาน")
        emp_id = current_user.employee_id
    elif not emp_id:
        # SUP / Admin ที่ไม่ได้ระบุ employee_id → check-in ของตัวเองถ้ามีบัญชีผูกอยู่
        if not current_user.employee_id:
            raise HTTPException(400, "ระบุ employee_id หรือเชื่อมบัญชีกับพนักงานก่อน")
        emp_id = current_user.employee_id
    if not emp_id:
        raise HTTPException(400, "ระบุ employee_id")

    today = today_th()

    checkin = db.query(models.CheckIn).filter(
        models.CheckIn.employee_id == emp_id,
        models.CheckIn.work_date == today,
        models.CheckIn.check_in_time != None,
    ).order_by(models.CheckIn.check_in_time.desc()).first()

    if not checkin:
        raise HTTPException(400, "ยังไม่ได้ Check-in วันนี้")
    if checkin.check_out_time:
        raise HTTPException(400, f"Check-out วันนี้แล้ว เวลา {checkin.check_out_time.strftime('%H:%M')}")

    # ดึง project config (require_gps)
    proj = None
    if checkin.project_id:
        proj = db.query(models.Project).filter(models.Project.id == checkin.project_id).first()
    project_require_gps = bool(proj.require_gps) if proj and proj.require_gps is not None else False

    lat = body.get("lat")
    lng = body.get("lng")
    if project_require_gps:
        if lat is None or lng is None:
            raise HTTPException(400, "ต้องส่งพิกัด lat, lng")

    dist_km = None
    in_fence = None
    if project_require_gps and lat is not None and lng is not None and proj and proj.lat and proj.lng:
        dist_km = haversine_km(lat, lng, proj.lat, proj.lng)
        in_fence = dist_km <= (proj.geofence_radius_km or 3.0)

    checkin.check_out_time = now_th()
    checkin.check_out_lat = lat
    checkin.check_out_lng = lng
    checkin.check_out_dist = dist_km
    checkin.check_out_ok = in_fence
    checkin.updated_at = now_th()
    db.commit()

    log_action(db, current_user, "UPDATE", "checkins", checkin.id,
               f"Check-out emp_id={emp_id}")
    return _out(checkin)


# ── Today status ────────────────────────────────

@router.get("/today")
def today_status(db: Session = Depends(get_db),
                 current_user: models.User = Depends(get_current_user)):
    """สถานะ check-in วันนี้ของตัวเอง"""
    emp_id = current_user.employee_id
    # ดึง require_gps จากโครงการที่ active assignment
    require_gps = False  # default: ไม่บังคับ GPS เพื่อความ safe
    if emp_id and current_user.employee:
        for a in current_user.employee.assignments:
            if a.is_active and a.project:
                rg = a.project.require_gps
                require_gps = bool(rg) if rg is not None else False
                break

    if not emp_id:
        return {"checked_in": False, "checked_out": False, "require_gps": False}

    today = today_th()
    c = db.query(models.CheckIn).filter(
        models.CheckIn.employee_id == emp_id,
        models.CheckIn.work_date == today,
    ).order_by(models.CheckIn.check_in_time.desc()).first()

    if not c:
        return {"checked_in": False, "checked_out": False, "require_gps": require_gps}
    return {**_out(c), "checked_in": bool(c.check_in_time), "checked_out": bool(c.check_out_time), "require_gps": require_gps}


# ── History list ────────────────────────────────

@router.get("")
def list_checkin(
    employee_id: Optional[int] = None,
    work_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    q = db.query(models.CheckIn)
    if current_user.role == "employee":
        if not current_user.employee_id:
            return []
        q = q.filter(models.CheckIn.employee_id == current_user.employee_id)
    elif employee_id:
        q = q.filter(models.CheckIn.employee_id == employee_id)
    if work_date:
        q = q.filter(models.CheckIn.work_date == work_date)
    rows = q.order_by(models.CheckIn.work_date.desc(), models.CheckIn.check_in_time.desc()).all()
    return [_out(r) for r in rows]


# ── Export ──────────────────────────────────────

@router.get("/export/excel")
def export_checkin(db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_admin_or_sup)):
    rows = db.query(models.CheckIn).order_by(models.CheckIn.work_date.desc()).all()
    data = []
    for r in rows:
        emp = r.employee
        data.append({
            "รหัสพนักงาน": emp.employee_code if emp else "",
            "ชื่อ-นามสกุล": f"{emp.first_name} {emp.last_name}" if emp else "",
            "โครงการ": r.project.name if r.project else "",
            "วันที่": r.work_date,
            "เวลา Check-in": r.check_in_time.strftime("%H:%M") if r.check_in_time else "",
            "ระยะ In (km)": round(r.check_in_dist, 3) if r.check_in_dist is not None else "",
            "In Geofence": "✓" if r.check_in_ok else ("✗" if r.check_in_ok is False else "—"),
            "เวลา Check-out": r.check_out_time.strftime("%H:%M") if r.check_out_time else "",
            "ระยะ Out (km)": round(r.check_out_dist, 3) if r.check_out_dist is not None else "",
            "Out Geofence": "✓" if r.check_out_ok else ("✗" if r.check_out_ok is False else "—"),
            "ชั่วโมงทำงาน": _work_hours(r) or "",
        })
    buf = io.BytesIO()
    write_excel(buf, data, sheet_name="Check-in")
    buf.seek(0)
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=checkin.xlsx"})


@router.get("/today-summary")
def today_summary(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Admin/SUP: สรุปจำนวน check-in วันนี้ แยกตามโครงการ"""
    if current_user.role not in ("admin", "sup"):
        raise HTTPException(403, "Admin/SUP only")
    today = today_th()
    checkins = db.query(models.CheckIn).filter(models.CheckIn.work_date == today).all()
    projects = db.query(models.Project).all()
    proj_map = {p.id: p.name for p in projects}  # ใช้ p.name (ไม่ใช่ p.project_name)

    # group by project_id ที่บันทึกใน CheckIn โดยตรง
    summary = {}  # proj_id -> {name, checked_in, checked_out, total}
    unassigned = {"name": "ไม่ระบุโครงการ", "checked_in": 0, "checked_out": 0, "total": 0}

    for c in checkins:
        pid = c.project_id
        if pid and pid in proj_map:
            if pid not in summary:
                summary[pid] = {"name": proj_map[pid], "checked_in": 0, "checked_out": 0, "total": 0}
            summary[pid]["total"] += 1
            if c.check_in_time:
                summary[pid]["checked_in"] += 1
            if c.check_out_time:
                summary[pid]["checked_out"] += 1
        else:
            unassigned["total"] += 1
            if c.check_in_time:
                unassigned["checked_in"] += 1
            if c.check_out_time:
                unassigned["checked_out"] += 1

    result = [{"project_id": pid, **data} for pid, data in summary.items()]
    result.sort(key=lambda x: -x["checked_in"])
    if unassigned["total"] > 0:
        result.a