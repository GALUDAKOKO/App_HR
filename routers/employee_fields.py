"""
Employee Custom Fields Router
- Admin จัดการ field definitions (CRUD)
- Admin/SUP/Employee บันทึกค่า field ของพนักงาน
- is_sensitive=True → เฉพาะ Admin เห็น
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
import json, os, uuid, io
from datetime import datetime
from PIL import Image

from database import get_db
import models
from auth import get_current_user, require_admin, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/employee-fields", tags=["employee-fields"])


# ── Field Definitions ────────────────────────────────────────

@router.get("/definitions")
def list_definitions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    """ดึง field definitions ทั้งหมด (sensitive แสดงเฉพาะ Admin)"""
    q = db.query(models.EmployeeCustomField).filter(
        models.EmployeeCustomField.is_active == True
    ).order_by(models.EmployeeCustomField.sort_order)

    fields = q.all()
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
    """สร้าง field ใหม่ (Admin only)"""
    options_raw = body.get("options", [])
    f = models.EmployeeCustomField(
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
    log_action(db, current_user, "CREATE", "employee_custom_fields", f.id,
               f"สร้าง field พนักงาน: {f.name}")
    return {"id": f.id, "name": f.name, "field_type": f.field_type,
            "is_sensitive": f.is_sensitive, "sort_order": f.sort_order}


@router.put("/definitions/{field_id}")
def update_definition(
    field_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    f = db.query(models.EmployeeCustomField).filter(
        models.EmployeeCustomField.id == field_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="ไม่พบ field")
    if "name" in body:
        f.name = body["name"]
    if "field_type" in body:
        f.field_type = body["field_type"]
    if "options" in body:
        f.options = json.dumps(body["options"], ensure_ascii=False) if body["options"] else None
    if "is_sensitive" in body:
        f.is_sensitive = body["is_sensitive"]
    if "is_required" in body:
        f.is_required = body["is_required"]
    if "sort_order" in body:
        f.sort_order = body["sort_order"]
    db.commit()
    log_action(db, current_user, "UPDATE", "employee_custom_fields", field_id,
               f"แก้ไข field: {f.name}")
    return {"success": True}


@router.delete("/definitions/{field_id}")
def delete_definition(
    field_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    f = db.query(models.EmployeeCustomField).filter(
        models.EmployeeCustomField.id == field_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="ไม่พบ field")
    f.is_active = False
    db.commit()
    log_action(db, current_user, "DELETE", "employee_custom_fields", field_id,
               f"ลบ field: {f.name}")
    return {"success": True}


# ── Field Values ─────────────────────────────────────────────

@router.get("/{emp_id}/values")
def get_employee_values(
    emp_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    """ดึงค่า custom fields ของพนักงาน (sensitive เฉพาะ Admin)"""
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")

    fields = db.query(models.EmployeeCustomField).filter(
        models.EmployeeCustomField.is_active == True
    ).order_by(models.EmployeeCustomField.sort_order).all()

    # map existing values
    existing = {v.field_id: v.value for v in emp.field_values}

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
            "is_required": f.is_required,
            "value": existing.get(f.id, ""),
        })
    return result


@router.put("/{emp_id}/values")
def save_employee_values(
    emp_id: int,
    body: dict,   # {"values": [{"field_id": 1, "value": "..."}, ...]}
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """บันทึกค่า custom fields ของพนักงาน (Admin only — upsert)"""
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")

    for item in body.get("values", []):
        fid = item.get("field_id")
        val = item.get("value", "")
        existing = db.query(models.EmployeeFieldValue).filter(
            models.EmployeeFieldValue.employee_id == emp_id,
            models.EmployeeFieldValue.field_id == fid
        ).first()
        if existing:
            existing.value = val
            existing.updated_at = datetime.utcnow()
        else:
            db.add(models.EmployeeFieldValue(
                employee_id=emp_id, field_id=fid, value=val))

    # บันทึก notes ด้วยถ้ามี
    if "notes" in body:
        emp.notes = body["notes"]
        emp.updated_at = datetime.utcnow()

    db.commit()
    log_action(db, current_user, "UPDATE", "employee_field_values", emp_id,
               f"บันทึก custom fields พนักงาน id={emp_id}")
    return {"success": True}


@router.post("/{emp_id}/field-image/{field_id}")
def upload_field_image(
    emp_id: int, field_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """Upload รูปภาพสำหรับ custom field ประเภท image (เช่น บัตรประชาชน)"""
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")

    fd = db.query(models.EmployeeCustomField).filter(
        models.EmployeeCustomField.id == field_id,
        models.EmployeeCustomField.field_type == "image"
    ).first()
    if not fd:
        raise HTTPException(status_code=404, detail="ไม่พบ field หรือ field นี้ไม่ใช่ประเภทรูปภาพ")

    contents = file.file.read()
    img = Image.open(io.BytesIO(contents))
    img.thumbnail((800, 600), Image.LANCZOS)
    if img.mode in ("RGBA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    os.makedirs("uploads", exist_ok=True)
    filename = f"field_emp{emp_id}_f{field_id}_{uuid.uuid4().hex[:6]}.jpg"
    filepath = os.path.join("uploads", filename)
    img.save(filepath, format="JPEG", quality=85)
    url = f"/uploads/{filename}"

    # upsert field value
    fv = db.query(models.EmployeeFieldValue).filter(
        models.EmployeeFieldValue.employee_id == emp_id,
        models.EmployeeFieldValue.field_id == field_id
    ).first()
    if fv:
        # ลบรูปเก่า
        if fv.value and os.path.exists(fv.value.lstrip("/")):
            os.remove(fv.value.lstrip("/"))
        fv.value = url
        fv.updated_at = datetime.utcnow()
    else:
        db.add(models.EmployeeFieldValue(employee_id=emp_id, field_id=field_id, value=url))

    db.commit()
    log_action(db, current_user, "UPDATE", "employee_field_values", emp_id,
               f"อัพโหลดรูป field '{fd.name}' พนักงาน id={emp_id}")
    return {"success": True, "url": url}
