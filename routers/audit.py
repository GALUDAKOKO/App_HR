from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import csv, io
from datetime import datetime as _dt, timedelta

from database import get_db
import models, schemas
from auth import require_admin

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=List[schemas.AuditOut])
def get_audit_logs(
    limit: int = 20,
    action: Optional[str] = None,
    table_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    q = db.query(models.AuditLog)
    if action:
        q = q.filter(models.AuditLog.action == action)
    if table_name:
        q = q.filter(models.AuditLog.table_name == table_name)
    if date_from:
        try:
            q = q.filter(models.AuditLog.timestamp >= _dt.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(models.AuditLog.timestamp < _dt.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass
    return q.order_by(models.AuditLog.timestamp.desc()).limit(limit).all()


@router.get("/export/csv")
def export_audit_csv(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    q = db.query(models.AuditLog)
    if date_from:
        try:
            q = q.filter(models.AuditLog.timestamp >= _dt.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(models.AuditLog.timestamp < _dt.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass
    logs = q.order_by(models.AuditLog.timestamp.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp", "username", "action", "table_name",
                     "record_id", "description", "ip_address"])
    for log in logs:
        writer.writerow([
            log.id,
            log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else "",
            log.username or "",
            log.action or "",
            log.table_name or "",
            log.record_id or "",
            log.description or "",
            log.ip_address or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"}
    )
