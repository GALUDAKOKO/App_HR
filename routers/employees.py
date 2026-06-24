from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import json, io, os, uuid, pandas as pd
from datetime import datetime
from PIL import Image

from database import get_db
import models, schemas
from auth import get_current_user, require_admin, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/employees", tags=["employees"])


def get_employee_with_project(emp: models.Employee, db: Session) -> schemas.EmployeeOut:
    project_names = []
    seen_ids = set()

    # 0) SUP role — หาจาก Project.sup_user_id ผ่าน User ที่ผูกกับ employee นี้
    linked_user = db.query(models.User).filter(
        models.User.employee_id == emp.id,
        models.User.role == "sup"
    ).first()
    if linked_user:
        sup_projs = db.query(models.Project).filter(
            models.Project.sup_user_id == linked_user.id
        ).all()
        for p in sup_projs:
            if p.id not in seen_ids and p.name:
                project_names.append(p.name)
                seen_ids.add(p.id)

    # 1) จาก Assignment (Admin assign) — ดึงทั้งหมดแล้วกรอง is_active ใน Python
    assigns = db.query(models.Assignment).filter(
        models.Assignment.employee_id == emp.id,
    ).order_by(models.Assignment.assigned_at.desc()).all()
    for a in assigns:
        if a.is_active and a.project_id and a.project_id not in seen_ids:
            proj = db.query(models.Project).filter(models.Project.id == a.project_id).first()
            if proj and proj.name and proj.name not in project_names:
                project_names.append(proj.name)
                seen_ids.add(proj.id)

    # 2) จาก SupTeamMember (SUP เพิ่มในทีม)
    team_rows = db.query(models.SupTeamMember).filter(
        models.SupTeamMember.employee_id == emp.id
    ).all()
    for t in team_rows:
        if t.project_id not in seen_ids:
            proj = db.query(models.Project).filter(models.Project.id == t.project_id).first()
            if proj and proj.name not in project_names:
                project_names.append(proj.name)
                seen_ids.add(proj.id)

    active_project = ", ".join(project_names) if project_names else "HO"

    has_account = db.query(models.User).filter(
        models.User.employee_id == emp.id
    ).first() is not None

    out = schemas.EmployeeOut.model_validate(emp)
    out.active_project = active_project
    out.has_account = has_account
    return out


@router.get("", response_model=List[schemas.EmployeeOut])
def list_employees(
    search: Optional[str] = None,
    department: Optional[str] = None,
    employee_type: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup)
):
    q = db.query(models.Employee)
    if is_active is not None:
        q = q.filter(models.Employee.is_active == is_active)
    if search:
        q = q.filter(
            (models.Employee.first_name.contains(search)) |
            (models.Employee.last_name.contains(search)) |
            (models.Employee.employee_code.contains(search))
        )
    if department:
        q = q.filter(models.Employee.department == department)
    if employee_type:
        q = q.filter(models.Employee.employee_type == employee_type)

    # SUP sees only their own team (SupTeamMember scope)
    if current_user.role == "sup":
        team_emp_ids = [
            stm.employee_id
            for stm in db.query(models.SupTeamMember).filter(
                models.SupTeamMember.sup_user_id == current_user.id
            ).all()
        ]
        if not team_emp_ids:
            return []
        q = q.filter(models.Employee.id.in_(team_emp_ids))

    employees = q.order_by(models.Employee.created_at.desc()).all()
    return [get_employee_with_project(e, db) for e in employees]


@router.post("", response_model=schemas.EmployeeOut, status_code=201)
def create_employee(
    body: schemas.EmployeeCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    existing = db.query(models.Employee).filter(
        models.Employee.employee_code == body.employee_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"รหัสพนักงาน {body.employee_code} มีอยู่แล้ว")

    emp = models.Employee(**body.model_dump())
    db.add(emp)
    db.commit()
    db.refresh(emp)

    log_action(db, current_user, "CREATE", "employees", emp.id,
               f"เพิ่มพนักงาน {emp.first_name} {emp.last_name} ({emp.employee_code})",
               new_value=json.dumps(body.model_dump(), ensure_ascii=False))
    return get_employee_with_project(emp, db)


@router.get("/{emp_id}", response_model=schemas.EmployeeOut)
def get_employee(emp_id: int, db: Session = Depends(get_db),
                 current_user: models.User = Depends(require_admin_or_sup)):
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")
    return get_employee_with_project(emp, db)


@router.put("/{emp_id}", response_model=schemas.EmployeeOut)
def update_employee(emp_id: int, body: schemas.EmployeeUpdate,
                    db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_admin)):
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")

    old = json.dumps({
        "first_name": emp.first_name, "last_name": emp.last_name,
        "department": emp.department, "employee_type": emp.employee_type
    }, ensure_ascii=False)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(emp, field, value)
    emp.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(emp)

    log_action(db, current_user, "UPDATE", "employees", emp_id,
               f"แก้ไขข้อมูล {emp.first_name} {emp.last_name}",
               old_value=old,
               new_value=json.dumps(body.model_dump(exclude_unset=True), ensure_ascii=False))
    return get_employee_with_project(emp, db)


@router.delete("/{emp_id}")
def delete_employee(emp_id: int, db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_admin)):
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")

    emp.is_active = False
    emp.updated_at = datetime.utcnow()
    db.commit()

    log_action(db, current_user, "DELETE", "employees", emp_id,
               f"ปิดการใช้งานพนักงาน {emp.first_name} {emp.last_name} ({emp.employee_code})")
    return {"success": True, "message": "ปิดการใช้งานพนักงานแล้ว"}


@router.get("/export/excel")
def export_employees(db: Session = Depends(get_db),
                     current_user: models.User = Depends(require_admin)):
    """Export พนักงาน + custom field values ทั้งหมดเป็น Excel"""
    employees = db.query(models.Employee).filter(models.Employee.is_active == True).all()

    # โหลด custom field definitions (admin เห็นทั้งหมด)
    field_defs = db.query(models.EmployeeCustomField).filter(
        models.EmployeeCustomField.is_active == True
    ).order_by(models.EmployeeCustomField.sort_order).all()

    data = []
    for e in employees:
        assign = db.query(models.Assignment).filter(
            models.Assignment.employee_id == e.id,
            models.Assignment.is_active == True
        ).first()
        project_name = ""
        if assign:
            p = db.query(models.Project).filter(models.Project.id == assign.project_id).first()
            project_name = p.name if p else ""

        row_data = {
            "รหัสพนักงาน": e.employee_code,
            "ชื่อ": e.first_name,
            "นามสกุล": e.last_name,
            "อายุ": e.age or "",
            "แผนก": e.department or "",
            "ประเภท": e.employee_type,
            "โครงการปัจจุบัน": project_name,
        }

        # เพิ่ม custom field values
        field_val_map = {fv.field_id: fv.value for fv in e.field_values}
        for fd in field_defs:
            if fd.field_type != "image":  # ไม่ export รูปภาพเป็น column
                row_data[fd.name] = field_val_map.get(fd.id, "")

        data.append(row_data)

    df = pd.DataFrame(data) if data else pd.DataFrame(
        columns=["รหัสพนักงาน", "ชื่อ", "นามสกุล", "อายุ", "แผนก", "ประเภท", "โครงการปัจจุบัน"]
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="พนักงาน")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=employees.xlsx"}
    )


@router.post("/{emp_id}/photo")
def upload_photo(emp_id: int, file: UploadFile = File(...),
                 db: Session = Depends(get_db),
                 current_user: models.User = Depends(require_admin)):
    """Upload + auto-resize รูปพนักงาน (max 200x200, JPEG 80%)"""
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")

    contents = file.file.read()
    img = Image.open(io.BytesIO(contents))
    img.thumbnail((200, 200), Image.LANCZOS)
    if img.mode in ("RGBA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    os.makedirs("uploads", exist_ok=True)
    # ลบรูปเก่า
    if emp.photo_url:
        old_path = emp.photo_url.lstrip("/")
        if os.path.exists(old_path):
            os.remove(old_path)

    filename = f"emp_{emp_id}_{uuid.uuid4().hex[:6]}.jpg"
    filepath = os.path.join("uploads", filename)
    img.save(filepath, format="JPEG", quality=80)

    emp.photo_url = f"/uploads/{filename}"
    emp.updated_at = datetime.utcnow()
    db.commit()

    log_action(db, current_user, "UPDATE", "employees", emp_id,
               f"อัพโหลดรูปพนักงาน {emp.first_name} {emp.last_name}")
    return {"success": True, "photo_url": emp.photo_url}


@router.post("/import/excel")
def import_employees(file: UploadFile = File(...),
                     db: Session = Depends(get_db),
                     current_user: models.User = Depends(require_admin)):
    """
    Flexible import — header-aware + upsert by employee_code
    - match คอลัมน์ตามชื่อหัว ไม่ใช่ position
    - ถ้า employee_code มีอยู่แล้ว → update (upsert)
    - custom field columns ที่ตรงกับ field def → import เข้า field values อัตโนมัติ
    - คอลัมน์ในไฟล์ที่ไม่รู้จัก → ข้ามไม่ error
    """
    try:
        contents = file.file.read()
        df = pd.read_excel(io.BytesIO(contents))
    except Exception:
        raise HTTPException(status_code=400, detail="ไม่สามารถอ่านไฟล์ Excel ได้")

    df.columns = [str(c).strip() for c in df.columns]

    if "รหัสพนักงาน" not in df.columns:
        raise HTTPException(status_code=400, detail="ไม่พบคอลัมน์ 'รหัสพนักงาน' ในไฟล์")

    # โหลด custom field definitions (ถ้ายังไม่มีตาราง → ข้ามไม่ error)
    field_name_map = {}
    try:
        field_defs = db.query(models.EmployeeCustomField).filter(
            models.EmployeeCustomField.is_active == True
        ).all()
        field_name_map = {fd.name: fd for fd in field_defs}
    except Exception:
        db.rollback()  # reset session ถ้า table ยังไม่มี

    created, updated, errors = 0, 0, []
    for idx, row in df.iterrows():
        code = str(row.get("รหัสพนักงาน", "")).strip()
        if not code or code == "nan":
            continue

        sp = db.begin_nested()  # savepoint — rollback เฉพาะแถวนี้ ไม่กระทบแถวก่อนหน้า
        try:
            def safe(col, default=""):
                val = row.get(col)
                return str(val).strip() if pd.notna(val) else default

            first_name = safe("ชื่อ") or safe("first_name")
            last_name = safe("นามสกุล") or safe("last_name")
            department = safe("แผนก") or safe("department") or None
            employee_type = safe("ประเภท") or safe("employee_type") or "รายวัน"
            age_raw = row.get("อายุ") or row.get("age")
            age = int(float(str(age_raw))) if pd.notna(age_raw) and str(age_raw).strip() not in ("", "nan") else None

            existing = db.query(models.Employee).filter(
                models.Employee.employee_code == code
            ).first()

            if existing:
                if first_name: existing.first_name = first_name
                if last_name: existing.last_name = last_name
                if department is not None: existing.department = department
                if employee_type: existing.employee_type = employee_type
                if age is not None: existing.age = age
                existing.updated_at = datetime.utcnow()
                emp = existing
                updated += 1
            else:
                valid_types = ("รายวัน", "รายเดือน", "สัญญาจ้าง")
                emp = models.Employee(
                    employee_code=code,
                    first_name=first_name or code,
                    last_name=last_name or "",
                    age=age,
                    department=department,
                    employee_type=employee_type if employee_type in valid_types else "รายวัน",
                )
                db.add(emp)
                db.flush()
                created += 1

            # Custom fields
            for col_name, fd in field_name_map.items():
                if col_name in df.columns:
                    val = row.get(col_name)
                    if pd.notna(val):
                        str_val = str(val).strip()
                        fv = db.query(models.EmployeeFieldValue).filter(
                            models.EmployeeFieldValue.employee_id == emp.id,
                            models.EmployeeFieldValue.field_id == fd.id
                        ).first()
                        if fv:
                            fv.value = str_val
                            fv.updated_at = datetime.utcnow()
                        else:
                            db.add(models.EmployeeFieldValue(
                                employee_id=emp.id, field_id=fd.id, value=str_val
                            ))
            sp.commit()

        except Exception as e:
            sp.rollback()  # rollback เฉพาะ savepoint นี้ — แถวก่อนหน้าไม่หาย
            errors.append(f"แถว {idx+2} (รหัส {code}): {str(e)[:120]}")
            continue

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"บันทึกฐานข้อมูลล้มเหลว: {str(e)}")
    msg = f"นำเข้าสำเร็จ: เพิ่มใหม่ {created} คน, อัพเดต {updated} คน"
    if errors:
        msg += f" | ข้ามด้วยข้อผิดพลาด {len(errors)} แถว"
    log_action(db, current_user, "IMPORT", "employees", description=msg)
    return {"success": True, "message": msg, "errors": errors}
