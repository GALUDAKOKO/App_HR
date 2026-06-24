"""
Employee Document Management — link to Google Drive files
Admin / SUP: CRUD on documents
All roles: read (for their own employee record)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from database import get_db
import models
from auth import get_current_user, require_admin, require_admin_or_sup, log_action

router = APIRouter(prefix="/api/v1/employee-docs", tags=["employee-docs"])

# ── Document type labels ───────────────────────────────
DOC_TYPES = {
    "id_card":    "สำเนาบัตรประชาชน",
    "house_reg":  "สำเนาทะเบียนบ้าน",
    "job_app":    "ใบสมัครงาน",
    "resume":     "Resume / CV",
    "edu_cert":   "วุฒิการศึกษา",
    "medical":    "ใบตรวจสุขภาพ",
    "other":      "อื่นๆ",
}


def _doc_out(doc: models.EmployeeDocument) -> dict:
    return {
        "id":           doc.id,
        "employee_id":  doc.employee_id,
        "doc_type":     doc.doc_type,
        "doc_name":     doc.doc_name,
        "gdrive_url":   doc.gdrive_url,
        "is_uploaded":  doc.is_uploaded,
        "note":         doc.note,
        "uploaded_by":  doc.uploaded_by,
        "uploaded_at":  doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        "created_at":   doc.created_at.isoformat() if doc.created_at else None,
    }


# ── GET: list doc types (for dropdown) ────────────────
@router.get("/types")
def get_doc_types():
    return [{"value": k, "label": v} for k, v in DOC_TYPES.items()]


# ── GET: employee folder URL + documents ──────────────
@router.get("/{emp_id}")
def list_employee_docs(
    emp_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")

    # Employee เห็นเฉพาะของตัวเอง
    if current_user.role == "employee":
        if current_user.employee_id != emp_id:
            raise HTTPException(status_code=403, detail="forbidden")

    docs = db.query(models.EmployeeDocument).filter(
        models.EmployeeDocument.employee_id == emp_id
    ).order_by(models.EmployeeDocument.doc_type).all()

    return {
        "gdrive_folder_url": emp.gdrive_folder_url,
        "documents": [_doc_out(d) for d in docs],
        "total": len(docs),
        "uploaded_count": sum(1 for d in docs if d.is_uploaded),
    }


# ── PATCH: set Google Drive folder URL ─────────────────
@router.patch("/{emp_id}/folder")
def set_folder_url(
    emp_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup),
):
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")
    emp.gdrive_folder_url = body.get("gdrive_folder_url", "").strip() or None
    emp.updated_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user, "UPDATE", "employees", emp_id,
               f"set gdrive folder: {emp.first_name} {emp.last_name}")
    return {"success": True, "gdrive_folder_url": emp.gdrive_folder_url}


# ── POST: add document record ──────────────────────────
@router.post("/{emp_id}")
def add_document(
    emp_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup),
):
    emp = db.query(models.Employee).filter(models.Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")

    doc_type = body.get("doc_type", "other")
    doc_name = body.get("doc_name") or DOC_TYPES.get(doc_type, "เอกสาร")
    gdrive_url = body.get("gdrive_url", "").strip() or None
    is_uploaded = bool(gdrive_url)

    doc = models.EmployeeDocument(
        employee_id=emp_id,
        doc_type=doc_type,
        doc_name=doc_name,
        gdrive_url=gdrive_url,
        is_uploaded=is_uploaded,
        note=body.get("note"),
        uploaded_by=current_user.id if is_uploaded else None,
        uploaded_at=datetime.utcnow() if is_uploaded else None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    log_action(db, current_user, "CREATE", "employee_documents", doc.id,
               f"add doc [{doc_type}] for {emp.first_name} {emp.last_name}")
    return _doc_out(doc)


# ── PATCH: update document (link / status / note) ─────
@router.patch("/{emp_id}/doc/{doc_id}")
def update_document(
    emp_id: int,
    doc_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin_or_sup),
):
    doc = db.query(models.EmployeeDocument).filter(
        models.EmployeeDocument.id == doc_id,
        models.EmployeeDocument.employee_id == emp_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    if "gdrive_url" in body:
        doc.gdrive_url = body["gdrive_url"].strip() or None
        if doc.gdrive_url and not doc.is_uploaded:
            doc.is_uploaded = True
            doc.uploaded_by = current_user.id
            doc.uploaded_at = datetime.utcnow()
    if "is_uploaded" in body:
        doc.is_uploaded = bool(body["is_uploaded"])
        if doc.is_uploaded and not doc.uploaded_at:
            doc.uploaded_by = current_user.id
            doc.uploaded_at = datetime.utcnow()
    if "note" in body:
        doc.note = body["note"]
    if "doc_name" in body:
        doc.doc_name = body["doc_name"]

    doc.updated_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user, "UPDATE", "employee_documents", doc_id,
               f"update doc [{doc.doc_type}] emp_id={emp_id}")
    return _doc_out(doc)


# ── DELETE: remove document record ────────────────────
@router.delete("/{emp_id}/doc/{doc_id}")
def delete_document(
    emp_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    doc = db.query(models.EmployeeDocument).filter(
        models.EmployeeDocument.id == doc_id,
        models.EmployeeDocument.employee_id == emp_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    db.delete(doc)
    db.commit()
    log_action(db, current_user, "DELETE", "employee_documents", doc_id,
               f"delete doc [{doc.doc_type}] emp_id={emp_id}")
    return {"success": True}
