"""
Live Chat Router — WebSocket + REST
- Employee คุยกับ Admin เท่านั้น
- Admin เห็นทุก conversation
- Broadcast / Announcement จาก Admin
- Unread count สำหรับ badge
"""
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import json

from database import get_db, SessionLocal
import models
from auth import get_current_user, require_admin

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


# ── WebSocket Connection Manager ─────────────────
class ConnectionManager:
    def __init__(self):
        # {user_id: WebSocket}
        self.active: dict[int, WebSocket] = {}

    async def connect(self, user_id: int, ws: WebSocket):
        await ws.accept()
        self.active[user_id] = ws

    def disconnect(self, user_id: int):
        self.active.pop(user_id, None)

    async def send_to(self, user_id: int, data: dict):
        ws = self.active.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(data, ensure_ascii=False, default=str))
            except Exception:
                self.disconnect(user_id)

    async def broadcast(self, data: dict, exclude_id: int = None):
        for uid, ws in list(self.active.items()):
            if uid == exclude_id:
                continue
            try:
                await ws.send_text(json.dumps(data, ensure_ascii=False, default=str))
            except Exception:
                self.disconnect(uid)


manager = ConnectionManager()


def _msg_out(m: models.ChatMessage) -> dict:
    sender = m.sender
    return {
        "id": m.id,
        "sender_id": m.sender_id,
        "sender_name": sender.username if sender else "",
        "sender_role": sender.role if sender else "",
        "receiver_id": m.receiver_id,
        "message": m.message,
        "is_read": m.is_read,
        "is_announce": m.is_announce,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


# ── WebSocket endpoint ───────────────────────────

@router.websocket("/ws/{user_id}")
async def chat_ws(
    ws: WebSocket,
    user_id: int,
    token: str = Query(...),
):
    """
    WS /api/v1/chat/ws/{user_id}?token=JWT
    Client ส่ง: {"to": receiver_id, "message": "...", "is_announce": false}
    Server push: msg object
    """
    # Verify token
    from jose import jwt, JWTError
    import os
    SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        token_user_id = int(sub) if sub else None
    except (JWTError, ValueError):
        await ws.accept()
        await ws.close(code=1008)
        return

    db: Session = SessionLocal()
    try:
        current_user = db.query(models.User).filter(models.User.id == token_user_id).first()
        if not current_user or current_user.id != user_id:
            await ws.accept()
            await ws.close(code=1008)
            return

        await manager.connect(user_id, ws)

        # ส่ง unread count ทันทีที่ connect
        unread = db.query(models.ChatMessage).filter(
            models.ChatMessage.receiver_id == user_id,
            models.ChatMessage.is_read == False
        ).count()
        await manager.send_to(user_id, {"type": "unread", "count": unread})

        try:
            while True:
                raw = await ws.receive_text()
                data = json.loads(raw)
                msg_text = (data.get("message") or "").strip()
                if not msg_text:
                    continue

                is_announce = bool(data.get("is_announce")) and current_user.role == "admin"
                receiver_id = None if is_announce else data.get("to")

                # Employee/SUP ส่งได้แค่ถึง Admin
                if current_user.role in ("employee", "sup"):
                    admin = db.query(models.User).filter(models.User.role == "admin").first()
                    receiver_id = admin.id if admin else None

                # บันทึก DB
                msg = models.ChatMessage(
                    sender_id=current_user.id,
                    receiver_id=receiver_id,
                    message=msg_text,
                    is_announce=is_announce,
                    created_at=datetime.utcnow(),
                )
                db.add(msg)
                db.commit()
                db.refresh(msg)

                out = _msg_out(msg)
                out["type"] = "message"

                if is_announce:
                    # broadcast ทุกคน
                    await manager.broadcast(out)
                else:
                    # ส่งหา receiver
                    await manager.send_to(receiver_id, out)
                    # ส่งกลับ sender ด้วย (echo)
                    await manager.send_to(current_user.id, out)
                    # แจ้ง unread ให้ receiver
                    unread_r = db.query(models.ChatMessage).filter(
                        models.ChatMessage.receiver_id == receiver_id,
                        models.ChatMessage.is_read == False
                    ).count()
                    await manager.send_to(receiver_id, {"type": "unread", "count": unread_r})

        except WebSocketDisconnect:
            manager.disconnect(user_id)
    finally:
        db.close()


# ── REST endpoints ───────────────────────────────

@router.get("/messages")
def get_messages(
    with_user: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """ดึงประวัติ chat"""
    q = db.query(models.ChatMessage)

    if current_user.role == "admin":
        if with_user:
            q = q.filter(
                ((models.ChatMessage.sender_id == with_user) & (models.ChatMessage.receiver_id == current_user.id)) |
                ((models.ChatMessage.sender_id == current_user.id) & (models.ChatMessage.receiver_id == with_user))
            )
    else:
        # Employee เห็นแค่ของตัวเองกับ Admin
        q = q.filter(
            (models.ChatMessage.sender_id == current_user.id) |
            (models.ChatMessage.receiver_id == current_user.id) |
            (models.ChatMessage.is_announce == True)
        )

    msgs = q.order_by(models.ChatMessage.created_at.desc()).limit(limit).all()
    return [_msg_out(m) for m in reversed(msgs)]


@router.post("/read/{sender_id}")
async def mark_read(
    sender_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """mark ข้อความที่ sender_id ส่งมาว่าอ่านแล้ว + notify sender via WebSocket"""
    updated = db.query(models.ChatMessage).filter(
        models.ChatMessage.sender_id == sender_id,
        models.ChatMessage.receiver_id == current_user.id,
        models.ChatMessage.is_read == False
    ).update({"is_read": True})
    db.commit()
    # แจ้ง sender ว่าข้อความถูกอ่านแล้ว (real-time read receipt)
    if updated > 0:
        await manager.send_to(sender_id, {
            "type": "read_receipt",
            "reader_id": current_user.id,
            "reader_name": current_user.username,
        })
    return {"success": True}


@router.get("/unread")
def unread_count(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    count = db.query(models.ChatMessage).filter(
        models.ChatMessage.receiver_id == current_user.id,
        models.ChatMessage.is_read == False
    ).count()
    return {"count": count}


@router.get("/conversations")
def conversations(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    """Admin: รายชื่อ user ที่มีการคุย + unread count"""
    users = db.query(models.User).filter(
        models.User.id != current_user.id,
        models.User.is_active == True
    ).all()

    result = []
    for u in users:
        last_msg = db.query(models.ChatMessage).filter(
            ((models.ChatMessage.sender_id == u.id) & (models.ChatMessage.receiver_id == current_user.id)) |
            ((models.ChatMessage.sender_id == current_user.id) & (models.ChatMessage.receiver_id == u.id))
        ).order_by(models.ChatMessage.created_at.desc()).first()

        unread = db.query(models.ChatMessage).filter(
            models.ChatMessage.sender_id == u.id,
            models.ChatMessage.receiver_id == current_user.id,
            models.ChatMessage.is_read == False
        ).count()

        result.append({
            "user_id": u.id,
            "username": u.username,
            "role": u.role,
            "unread": unread,
            "last_message": last_msg.message if last_msg else None,
            "last_at": last_msg.created_at.isoformat() if last_msg and last_msg.created_at else None,
        })

    # เรียงตาม last message ล่าสุด
    result.sort(key=lambda x: x["last_at"] or "", reverse=True)
    return result


@router.get("/admin-info")
def get_admin_info(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """ทุก role: ดึง admin user id สำหรับ chat"""
    admin = db.query(models.User).filter(models.User.role == "admin", models.User.is_active == True).first()
    if not admin:
        raise HTTPException(status_code=404, detail="No admin found")
    return {"user_id": admin.id, "username": admin.username, "role": admin.role}
