from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
import os, uuid, io, json
import pandas as pd
from PIL import Image

from database import get_db
import models, schemas
from auth import get_current_user, require_admin, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ── List SUP users (for dropdown) ──────────────────────

@router.get("/sups")
def list_sup_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    sups = db.query(models.User).filter(
        models.User.role == "sup",
        models.User.is_active == True
    ).all()
    result = []
    for u in sups:
        name = u.username
        if u.employee:
            name = f"{u.employee.first_name} {u.employee.last_name}"
        result.append({"id": u.id, "username": u.username, "display_name": name})
    return result


def _sync_sup_name(db: Session, proj: models.Project):
    if proj.sup_user_id:
        u = db.query(models.User).filter(models.User.id == proj.sup_user_id).first()
        if u:
            proj.sup_name = (
                f"{u.employee.first_name} {u.employee.last_name}"
                if u.employee else u.username
            )
    elif not proj.sup_name:
        proj.sup_name = None


@router.get("", response_model=List[schemas.ProjectOut])
def list_projects(db: Session = Depends(get_db),
                  current_user: models.User = Depends(get_current_user)):
    return db.query(models.Project).order_by(models.Project.created_at.desc()).all()


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(body: schemas.ProjectCreate,
                   db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_admin)):
    proj = models.Project(**body.model_dump())
    _sync_sup_name(db, proj)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    log_action(db, current_user, "CREATE", "projects", proj.id,
               f"create project: {proj.name}")
    return proj


@router.put("/{proj_id}", response_model=schemas.ProjectOut)
def update_project(proj_id: int, body: schemas.ProjectUpdate,
                   db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_admin)):
    proj = db.query(models.Project).filter(models.Project.id == proj_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(proj, field, value)
    _sync_sup_name(db, proj)
    proj.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(proj)
    log_action(db, current_user, "UPDATE", "projects", proj_id, f"update project: {proj.name}")
    return proj


@router.patch("/{proj_id}")
def patch_project(proj_id: int, body: dict, db: Session = Depends(get_db),
                  current_user: models.User = Depends(require_admin)):
    """Partial update — รองรับ start_date และ field อื่นๆ"""
    proj = db.query(models.Project).filter(models.Project.id == proj_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    allowed = {"start_date", "notes", "name"}
    for k, v in body.items():
        if k in allowed:
            setattr(proj, k, v)
    proj.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@router.delete("/{proj_id}")
def delete_project(proj_id: int, db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_admin)):
    proj = db.query(models.Project).filter(models.Project.id == proj_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    name = proj.name
    db.delete(proj)
    db.commit()
    log_action(db, current_user, "DELETE", "projects", proj_id, f"ลบโครงการ: {name}")
    return {"success": True, "message": f"ลบโครงการ {name} สำเร็จ"}


@router.post("/{proj_id}/photo")
def upload_project_photo(proj_id: int, file: UploadFile = File(...),
                         db: Session = Depends(get_db),
                         current_user: models.User = Depends(require_admin)):
    proj = db.query(models.Project).filter(models.Project.id == proj_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    contents = file.file.read()
    img = Image.open(io.BytesIO(contents))
    img.thumbnail((600, 400), Image.LANCZOS)
    if img.mode in ("RGBA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    os.makedirs("uploads", exist_ok=True)
    if proj.photo_url:
        old = proj.photo_url.lstrip("/")
        if os.path.exists(old):
            os.remove(old)

    filename = f"proj_{proj_id}_{uuid.uuid4().hex[:6]}.jpg"
    filepath = os.path.join("uploads", filename)
    img.save(filepath, format="JPEG", quality=85)

    proj.photo_url = f"/uploads/{filename}"
    proj.updated_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user, "UPDATE", "projects", proj_id,
               f"upload photo: {proj.name}")
    return {"success": True, "photo_url": proj.photo_url}


@router.get("/{proj_id}/employees")
def get_project_employees(proj_id: int, db: Session = Depends(get_db),
                          current_user: models.User = Depends(require_admin_or_sup)):
    """รวม employees จาก Assignment + SupTeamMember (deduplicated)"""
    seen = set()
    result = []

    # 1) จาก Assignment (Admin assign)
    assigns = db.query(models.Assignment).filter(
        models.Assignment.project_id == proj_id,
        models.Assignment.is_active == True
    ).all()
    for a in assigns:
        emp = db.query(models.Employee).filter(models.Employee.id == a.employee_id).first()
        if emp and emp.id not in seen:
            seen.add(emp.id)
            result.append({
                "id": emp.id,
                "employee_code": emp.employee_code,
                "name": f"{emp.first_name} {emp.last_name}",
                "department": emp.department or "",
                "employee_type": emp.employee_type,
                "photo_url": emp.photo_url,
                "source": "assignment",
            })

    # 2) จาก SupTeamMember (SUP assign)
    team = db.query(models.SupTeamMember).filter(
        models.SupTeamMember.project_id == proj_id
    ).all()
    for m in team:
        emp = db.query(models.Employee).filter(models.Employee.id == m.employee_id).first()
        if emp and emp.id not in seen:
            seen.add(emp.id)
            result.append({
                "id": emp.id,
                "employee_code": emp.employee_code,
                "name": f"{emp.first_name} {emp.last_name}",
                "department": emp.department or "",
                "employee_type": emp.employee_type,
                "photo_url": emp.photo_url,
                "source": "sup_team",
            })

    # SUP info
    proj = db.query(models.Project).filter(models.Project.id == proj_id).first()
    sup_info = None
    if proj and proj.sup_user_id:
        sup_u = db.query(models.User).filter(models.User.id == proj.sup_user_id).first()
        if sup_u and sup_u.employee:
            sup_emp = sup_u.employee
            sup_info = {
                "name": f"{sup_emp.first_name} {sup_emp.last_name}",
                "photo_url": sup_emp.photo_url,
                "employee_code": sup_emp.employee_code,
            }
    elif proj and proj.sup_name:
        sup_info = {"name": proj.sup_name, "photo_url": None, "employee_code": ""}

    return {"employees": result, "sup": sup_info, "total": len(result)}


@router.post("/{proj_id}/assign")
def assign_employee(proj_id: int, body: schemas.AssignRequest,
                    db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_admin)):
    proj = db.query(models.Project).filter(models.Project.id == proj_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    emp = db.query(models.Employee).filter(models.Employee.id == body.employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")

    old = db.query(models.Assignment).filter(
        models.Assignment.employee_id == body.employee_id,
        models.Assignment.is_active == True
    ).first()
    if old:
        old.is_active = False
        old.unassigned_at = datetime.utcnow()

    assign = models.Assignment(employee_id=body.employee_id, project_id=proj_id)
    db.add(assign)
    db.flush()

    # ── sync SupTeamMember ──────────────────────────────
    if proj.sup_user_id:
        existing_stm = db.query(models.SupTeamMember).filter(
            models.SupTeamMember.project_id == proj_id,
            models.SupTeamMember.employee_id == body.employee_id,
        ).first()
        if not existing_stm:
            db.add(models.SupTeamMember(
                sup_user_id=proj.sup_user_id,
                project_id=proj_id,
                employee_id=body.employee_id,
            ))

    db.commit()
    log_action(db, current_user, "ASSIGN", "assignments", assign.id,
               f"Assign {emp.first_name} {emp.last_name} to {proj.name}")
    return {"success": True, "message": f"Assign {emp.first_name} {emp.last_name} to {proj.name}"}


@router.delete("/{proj_id}/unassign/{emp_id}")
def unassign_employee(proj_id: int, emp_id: int, db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_admin)):
    # deactivate Assignment ถ้ามี (อาจไม่มีถ้าเพิ่มผ่าน SUP team)
    assign = db.query(models.Assignment).filter(
        models.Assignment.project_id == proj_id,
        models.Assignment.employee_id == emp_id,
        models.Assignment.is_active == True
    ).first()
    if assign:
        assign.is_active = False
        assign.unassigned_at = datetime.utcnow()

    # ลบ SupTeamMember เสมอ (source ไหนก็ตาม)
    stm_deleted = db.query(models.SupTeamMember).filter(
        models.SupTeamMember.project_id == proj_id,
        models.SupTeamMember.employee_id == emp_id,
    ).delete()

    if not assign and stm_deleted == 0:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงานในโครงการนี้")

    db.commit()
    return {"success": True, "message": "unassigned"}


# ══ Import / Export Projects ══════════════════════════════

@router.get("/export/excel")
def export_projects(db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_admin)):
    projects = db.query(models.Project).all()

    field_defs = db.query(models.ProjectCustomField).filter(
        models.ProjectCustomField.is_active == True
    ).order_by(models.ProjectCustomField.sort_order).all()

    data = []
    for p in projects:
        row_data = {
            "project_name": p.name,
            "sup_name": p.sup_name or "",
            "latitude": p.lat or "",
            "longitude": p.lng or "",
            "radius_km": p.geofence_radius_km or 3.0,
            "status": "active" if p.is_active else "inactive",
        }
        field_val_map = {fv.field_id: fv.value for fv in p.field_values}
        for fd in field_defs:
            if fd.field_type != "image":
                row_data[fd.name] = field_val_map.get(fd.id, "")
        data.append(row_data)

    df = pd.DataFrame(data) if data else pd.DataFrame(
        columns=["project_name", "sup_name", "latitude", "longitude", "radius_km", "status"]
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Projects")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=projects.xlsx"}
    )


@router.post("/import/excel")
def import_projects(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    try:
        contents = file.file.read()
        df = pd.read_excel(io.BytesIO(contents))
    except Exception:
        raise HTTPException(status_code=400, detail="cannot read excel file")

    df.columns = [str(c).strip() for c in df.columns]

    name_col = None
    for c in ["project_name", "name"]:
        if c in df.columns:
            name_col = c
            break
    if name_col is None:
        raise HTTPException(status_code=400, detail="column 'project_name' not found")

    field_name_map = {}
    try:
        field_defs = db.query(models.ProjectCustomField).filter(
            models.ProjectCustomField.is_active == True
        ).all()
        field_name_map = {fd.name: fd for fd in field_defs}
    except Exception:
        db.rollback()

    created, updated, errors = 0, 0, []
    for idx, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if not name or name == "nan":
            continue

        sp = db.begin_nested()  # savepoint — rollback เฉพาะแถวนี้
        try:
            def safe(col, default=""):
                val = row.get(col)
                return str(val).strip() if pd.notna(val) else default

            sup_name = safe("sup_name") or None
            lat_raw = row.get("latitude") or row.get("lat") or row.get("Latitude")
            lng_raw = row.get("longitude") or row.get("lng") or row.get("Longitude")
            lat = float(lat_raw) if pd.notna(lat_raw) and str(lat_raw).strip() not in ("", "nan") else None
            lng = float(lng_raw) if pd.notna(lng_raw) and str(lng_raw).strip() not in ("", "nan") else None
            radius_raw = row.get("radius_km") or row.get("geofence_radius_km")
            radius = float(radius_raw) if pd.notna(radius_raw) and str(radius_raw).strip() not in ("", "nan") else 3.0
            status_raw = safe("status")
            is_active = status_raw != "inactive"

            existing = db.query(models.Project).filter(models.Project.name == name).first()

            if existing:
                if sup_name is not None:
                    existing.sup_name = sup_name
                if lat is not None:
                    existing.lat = lat
                if lng is not None:
                    existing.lng = lng
                existing.geofence_radius_km = radius
                existing.is_active = is_active
                existing.updated_at = datetime.utcnow()
                proj = existing
                updated += 1
            else:
                proj = models.Project(
                    name=name,
                    sup_name=sup_name,
                    lat=lat,
                    lng=lng,
                    geofence_radius_km=radius,
                    is_active=is_active,
                )
                db.add(proj)
                db.flush()
                created += 1

            for col_name in df.columns:
                if col_name in field_name_map:
                    fd = field_name_map[col_name]
                    val = row.get(col_name)
                    if pd.notna(val):
                        str_val = str(val).strip()
                        fv = db.query(models.ProjectFieldValue).filter(
                            models.ProjectFieldValue.project_id == proj.id,
                            models.ProjectFieldValue.field_id == fd.id
                        ).first()
                        if fv:
                            fv.value = str_val
                            fv.updated_at = datetime.utcnow()
                        else:
                            db.add(models.ProjectFieldValue(
                                project_id=proj.id, field_id=fd.id, value=str_val
                            ))
            sp.commit()

        except Exception as e:
            sp.rollback()  # rollback เฉพาะ savepoint นี้ — แถวก่อนหน้าไม่หาย
            errors.append(f"row {idx+2} ({name}): {str(e)[:120]}")
            continue

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"commit error: {str(e)}")

    imported = created + updated
    return {
        "message": f"นำเข้าสำเร็จ {imported} โครงการ (เพิ่มใหม่ {created}, อัพเดต {updated})",
        "imported": imported,
        "created": created,
        "updated": updated,
        "errors": errors,
    }
