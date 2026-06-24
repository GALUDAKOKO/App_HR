"""
M2 — Elearning Router
- Admin: CRUD content, set allowed_roles, view logs, export
- SUP/Employee: เห็นเฉพาะ content ที่ role ตัวเองได้รับสิทธิ์
- Activity Log: บันทึกทุกครั้งที่เปิดดู (POST /log/{id})
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import json, io, pandas as pd

from database import get_db
import models
from auth import get_current_user, require_admin, log_action

router = APIRouter(prefix="/api/v1/elearning", tags=["elearning"])

CATEGORIES = ("ไฟฟ้า", "เครื่องกล", "โปรแกรม", "บริหาร")
CONTENT_TYPES = ("video", "document", "link")


def detect_content_type(url: str) -> str:
    """Auto-detect content type จาก URL"""
    if not url:
        return "link"
    if any(x in url for x in ("youtube.com", "youtu.be", "vimeo.com")):
        return "video"
    if any(x in url for x in ("drive.google.com", "docs.google.com")):
        return "document"
    return "link"


def _can_access(content: models.ElearningContent, role: str) -> bool:
    """ตรวจสิทธิ์ตาม allowed_roles JSON"""
    if not content.allowed_roles:
        return role == "admin"
    try:
        roles = json.loads(content.allowed_roles)
        return role in roles
    except Exception:
        return role == "admin"


def _out(c: models.ElearningContent, include_logs: bool = False) -> dict:
    roles = []
    try:
        roles = json.loads(c.allowed_roles) if c.allowed_roles else []
    except Exception:
        pass
    d = {
        "id": c.id,
        "title": c.title,
        "category": c.category,
        "content_type": c.content_type or "video",
        "url": c.url,
        "thumbnail_url": c.thumbnail_url or "",
        "duration_min": c.duration_min,
        "description": c.description or "",
        "allowed_roles": roles,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    d["view_count"] = len(c.logs)
    return d


# ── List ────────────────────────────────────────

@router.get("")
def list_content(
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    q = db.query(models.ElearningContent).filter(models.ElearningContent.is_active == True)
    if category:
        q = q.filter(models.ElearningContent.category == category)
    rows = q.order_by(models.ElearningContent.category, models.ElearningContent.title).all()
    # Admin เห็นทั้งหมด, คนอื่น filter ตาม role
    if current_user.role == "admin":
        return [_out(r, include_logs=True) for r in rows]
    return [_out(r) for r in rows if _can_access(r, current_user.role)]


# ── Create (Admin) ──────────────────────────────

@router.post("")
def create_content(
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    title = body.get("title", "").strip()
    url = body.get("url", "").strip()
    if not title or not url:
        raise HTTPException(400, "กรอก title และ url")

    category = body.get("category", "บริหาร")
    if category not in CATEGORIES:
        raise HTTPException(400, f"category ต้องเป็นหนึ่งใน {CATEGORIES}")

    allowed_roles = body.get("allowed_roles", [])
    if not isinstance(allowed_roles, list):
        allowed_roles = []

    content_type = body.get("content_type") or detect_content_type(url)

    content = models.ElearningContent(
        title=title,
        category=category,
        content_type=content_type,
        url=url,
        thumbnail_url=(body.get("thumbnail_url") or "").strip() or None,
        duration_min=body.get("duration_min") or None,
        description=(body.get("description") or "").strip() or None,
        allowed_roles=json.dumps(allowed_roles) if allowed_roles else None,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    log_action(db, current_user, "CREATE", "elearning_contents", content.id, f"สร้าง: {title}")
    return _out(content, include_logs=True)


# ── Update (Admin) ──────────────────────────────

@router.put("/{content_id}")
def update_content(
    content_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    c = db.query(models.ElearningContent).filter(models.ElearningContent.id == content_id).first()
    if not c:
        raise HTTPException(404, "ไม่พบ content")

    if "title" in body:
        c.title = body["title"].strip()
    if "category" in body:
        if body["category"] not in CATEGORIES:
            raise HTTPException(400, f"category ต้องเป็นหนึ่งใน {CATEGORIES}")
        c.category = body["category"]
    if "url" in body:
        c.url = body["url"].strip()
        # auto-detect content_type ถ้าไม่ได้ส่งมา
        if "content_type" not in body:
            c.content_type = detect_content_type(c.url)
    if "content_type" in body:
        c.content_type = body["content_type"] or detect_content_type(c.url)
    if "thumbnail_url" in body:
        c.thumbnail_url = (body["thumbnail_url"] or "").strip() or None
    if "duration_min" in body:
        c.duration_min = body["duration_min"] or None
    if "description" in body:
        c.description = (body["description"] or "").strip() or None
    if "is_active" in body:
        c.is_active = bool(body["is_active"])
    if "allowed_roles" in body:
        roles = body["allowed_roles"]
        if not isinstance(roles, list):
            roles = []
        c.allowed_roles = json.dumps(roles) if roles else None

    c.updated_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user, "UPDATE", "elearning_contents", c.id, f"แก้ไข: {c.title}")
    return _out(c, include_logs=True)


# ── Delete (Admin) ──────────────────────────────

@router.delete("/{content_id}")
def delete_content(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    c = db.query(models.ElearningContent).filter(models.ElearningContent.id == content_id).first()
    if not c:
        raise HTTPException(404, "ไม่พบ content")
    c.is_active = False
    c.updated_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user, "DELETE", "elearning_contents", content_id, f"ซ่อน: {c.title}")
    return {"success": True}


# ── Log view ────────────────────────────────────

@router.post("/log/{content_id}")
def log_view(
    content_id: int,
    body: dict = {},
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """บันทึกเมื่อผู้ใช้เปิดดู content"""
    c = db.query(models.ElearningContent).filter(
        models.ElearningContent.id == content_id,
        models.ElearningContent.is_active == True
    ).first()
    if not c:
        raise HTTPException(404, "ไม่พบ content")
    if current_user.role != "admin" and not _can_access(c, current_user.role):
        raise HTTPException(403, "ไม่มีสิทธิ์เข้าถึง")

    completed = bool(body.get("completed", False))
    # รับทั้ง duration_sec และ duration (compat)
    duration_sec = body.get("duration_sec") or body.get("duration") or None
    if duration_sec is not None:
        duration_sec = int(duration_sec)
    now = datetime.utcnow()
    entry = models.ElearningLog(
        content_id=content_id,
        user_id=current_user.id,
        watched_at=now,
        duration_sec=duration_sec,
        completed=completed,
        completed_at=now if completed else None,
    )
    db.add(entry)
    db.commit()
    return {"success": True, "completed": completed}


@router.post("/{content_id}/complete")
def mark_complete(
    content_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Mark content as completed"""
    c = db.query(models.ElearningContent).filter(
        models.ElearningContent.id == content_id,
        models.ElearningContent.is_active == True
    ).first()
    if not c:
        raise HTTPException(404, "ไม่พบ content")
    if current_user.role != "admin" and not _can_access(c, current_user.role):
        raise HTTPException(403, "ไม่มีสิทธิ์เข้าถึง")
    now = datetime.utcnow()
    entry = models.ElearningLog(
        content_id=content_id,
        user_id=current_user.id,
        watched_at=now,
        duration_sec=None,
        completed=True,
        completed_at=now,
    )
    db.add(entry)
    db.commit()
    return {"success": True, "completed": True}


# ── My progress ──────────────────────────────────────

@router.get("/my-progress")
def my_progress(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Return {content_id: {viewed, completed, completed_at}}"""
    logs = db.query(models.ElearningLog).filter(
        models.ElearningLog.user_id == current_user.id
    ).all()
    result = {}
    for log in logs:
        cid = log.content_id
        if cid not in result:
            result[cid] = {"viewed": False, "completed": False, "completed_at": None}
        result[cid]["viewed"] = True
        if log.completed:
            result[cid]["completed"] = True
            result[cid]["completed_at"] = log.completed_at.isoformat() if log.completed_at else None
    return result


# ── Logs list (Admin/SUP) ───────────────────────

@router.get("/logs")
def list_logs(
    content_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role not in ("admin", "sup"):
        raise HTTPException(403, "เฉพาะ Admin/SUP")
    q = db.query(models.ElearningLog)
    if content_id:
        q = q.filter(models.ElearningLog.content_id == content_id)
    rows = q.order_by(models.ElearningLog.watched_at.desc()).limit(500).all()
    result = []
    for r in rows:
        u = r.user
        c = r.content
        result.append({
            "id": r.id,
            "content_id": r.content_id,
            "content_title": c.title if c else "",
            "category": c.category if c else "",
            "user_id": r.user_id,
            "username": u.username if u else "",
            "watched_at": r.watched_at.isoformat() if r.watched_at else None,
            "duration_sec": r.duration_sec,
            "completed": bool(r.completed),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        })
    return result


# ── Export Excel (Admin) ────────────────────────

@router.get("/logs/export")
def export_logs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    rows = db.query(models.ElearningLog).order_by(models.ElearningLog.watched_at.desc()).all()
    data = []
    for r in rows:
        u = r.user
        c = r.content
        data.append({
            "Username": u.username if u else "",
            "Content": c.title if c else "",
            "หมวดหมู่": c.category if c else "",
            "เวลาที่ดู": r.watched_at.strftime("%Y-%m-%d %H:%M") if r.watched_at else "",
            "ระยะเวลา (วิ)": r.duration_sec or "",
            "เรียนจบ": "✓" if r.completed else "",
            "จบเมื่อ": r.completed_at.strftime("%Y-%m-%d %H:%M") if r.completed_at else "",
        })
    df = pd.DataFrame(data) if data else pd.DataFrame()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Elearning Log")
    buf.seek(0)
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=elearning_logs.xlsx"})


# ── Completion stats per course (Admin/SUP) ─────

@router.get("/stats")
def course_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role not in ("admin", "sup"):
        raise HTTPException(403, "Admin/SUP only")
    from sqlalchemy import func
    courses = db.query(models.ElearningContent).filter(
