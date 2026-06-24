from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import secrets

from database import get_db
import models, schemas
from auth import (verify_password, hash_password, create_access_token,
                  get_current_user, require_admin, log_action)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenResponse)
def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(
        models.User.username == body.username,
        models.User.is_active == True
    ).first()

    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

    user.last_login = datetime.utcnow()
    db.commit()

    token = create_access_token({"sub": str(user.id), "role": user.role})
    log_action(db, user, "LOGIN", description=f"User {user.username} logged in")

    return schemas.TokenResponse(
        access_token=token,
        role=user.role,
        username=user.username,
        user_id=user.id
    )


@router.get("/me", response_model=schemas.UserMe)
def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user


@router.post("/register")
def register_via_invite(body: schemas.RegisterRequest, db: Session = Depends(get_db)):
    """พนักงานตั้ง password ผ่าน invite link"""
    user = db.query(models.User).filter(
        models.User.invite_token == body.token,
        models.User.is_active == True
    ).first()

    if not user:
        raise HTTPException(status_code=400, detail="Token ไม่ถูกต้อง")

    if user.invite_expires and user.invite_expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Token หมดอายุแล้ว (48 ชั่วโมง)")

    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password ต้องมีอย่างน้อย 6 ตัวอักษร")

    user.hashed_password = hash_password(body.password)
    user.invite_token = None
    user.invite_expires = None
    db.commit()

    return {"success": True, "message": "ตั้งรหัสผ่านสำเร็จ กรุณาเข้าสู่ระบบ", "username": user.username}


@router.post("/invite/{user_id}", response_model=schemas.InviteOut)
def generate_invite(user_id: int, request: Request, db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_admin)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")

    token = secrets.token_urlsafe(32)
    user.invite_token = token
    user.invite_expires = datetime.utcnow() + timedelta(hours=48)
    db.commit()

    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/register?token={token}"

    log_action(db, current_user, "INVITE", "users", user_id,
               f"Generated invite for {user.username}")

    return schemas.InviteOut(invite_url=invite_url)


@router.post("/reset-password/{user_id}", response_model=schemas.InviteOut)
def reset_password(user_id: int, request: Request, db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_admin)):
    """Admin reset password → generate new invite link"""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")

    token = secrets.token_urlsafe(32)
    user.invite_token = token
    user.invite_expires = datetime.utcnow() + timedelta(hours=48)
    user.hashed_password = None  # ล้าง password เดิม
    db.commit()

    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/register?token={token}"

    log_action(db, current_user, "RESET_PASSWORD", "users", user_id,
               f"Reset password for {user.username}")

    return schemas.InviteOut(invite_url=invite_url)
