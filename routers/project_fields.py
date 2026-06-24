"""
Project Custom Fields Router
- Admin จัดการ field definitions (CRUD)
- Admin บันทึกค่า field ของโครงการ
- is_sensitive=True → เฉพาะ Admin เห็น
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json
from datetime import datetime

from database import get_db
import models
from auth import require_admin, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/project-fields", tags=["project-fields"])


# ── Field Definitions ─────────────────────────────────────────

@router.get("/definitions")
def list_definitions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    fields = db.query(models.ProjectCustomField).filter(
        models.ProjectCustomField.is_active == True
    ).order_by(models.ProjectCustomField.sort_order).all()

    result = []
    for f in fields:
        if f.is_sensitive and current_user.role != "admin":
            continue
        result.append({
            "id": f.id,
            "name": f.name,
            "field_type": f.field_type,
            "options": json.loads(f.options) if f.options else [],
            "is_sensitive": f.is_sensitive,
            "is_required": f.is_required,
            "sort_order": f.sort_order,
        })
    return result


@router.post("/definitions")
def create_definition(
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    options_raw = body.get("options", [])
    f = models.ProjectCustomField(
        name=body["name"],
        field_type=body.get("field_type", "text"),
        options=json.dumps(options_raw, ensure_ascii=False) if options_raw else None,
        is_sensitive=body.get("is_sensitive", False),
        is_required=body.get("is_required", False),
        sort_order=body.get("sort_order", 0),
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    log_action(db, current_user, "CREATE", "project_custom_fields", f.id,
               f"สร้าง field โครงการ: {f.name}")
    return {"id": f.id, "name": f.name, "field_type": f.field_type,
            "is_sensitive": f.is_sensitive}


@router.put("/definitions/{field_id}")
def update_definition(
    field_id: int, body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    f = db.query(models.ProjectCustomField).filter(
        models.ProjectCustomField.id == field_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="ไม่พบ field")
    for attr in ("name", "field_type", "is_sensitive", "is_required", "sort_order"):
        if attr in body:
            setattr(f, attr, body[attr])
    if "options" in body:
        f.options = json.dumps(body["options"], ensure_ascii=False) if body["options"] else None
    db.commit()
    return {"success": True}


@router.delete("/definitions/{field_id}")
def delete_definition(
    field_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    f = db.query(models.ProjectCustomField).filter(
        models.ProjectCustomField.id == field_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="ไม่พบ field")
    f.is_active = False
    db.commit()
    log_action(db, current_user, "DELETE", "project_custom_fields", field_id,
               f"ลบ field โครงการ: {f.name}")
    return {"success": True}


# ── Field Values ─────────────────────────────────────────────

@router.get("/{project_id}/values")
def get_project_values(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    proj = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="ไม่พบโครงการ")

    fields = db.query(models.ProjectCustomField).filter(
        models.ProjectCustomField.is_active == True
    ).order_by(models.ProjectCustomField.sort_order).all()

    existing = {v.field_id: v.value for v in proj.field_values}

    result = []
    for f in fields:
        if f.is_sensitive and current_user.role != "admin":
            continue
        result.append({
            "field_id": f.id,
            "name": f.name,
            "field_type": f.field_type,
            "options": json.loads(f.options) if f.options else [],
            "is_sensitive": f.is_sensitive,
            "value": existing.get(f.id, ""),
        })
    return result


@router.put("/{project_id}/values")
def save_project_values(
    project_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    proj = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="ไม่พบโครงการ")

    for item in body.get("values", []):
        fid = item.get("field_id")
        val = item.get("value", "")
        existing = db.query(models.ProjectFieldValue).filter(
            models.ProjectFieldValue.project_id == project_id,
            models.ProjectFieldValue.field_id == fid
        ).first()
        if existing:
            existing.value = val
            existing.updated_at = datetime.utcnow()
        else:
            db.add(models.ProjectFieldValue(
                project_id=project_id, field_id=fid, value=val))

    if "notes" in body:
        proj.notes = body["notes"]
        proj.updated_at = datetime.utcnow()

    db.commit()
    log_action(db, current_user, "UPDATE", "project_field_values", project_id,
               f"บันทึก custom fields โครงการ id={project_id}")
    return {"success": True}
