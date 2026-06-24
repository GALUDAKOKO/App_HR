from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# ── Auth ──────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    user_id: int

class RegisterRequest(BaseModel):
    token: str
    password: str

class UserMe(BaseModel):
    id: int
    username: str
    role: str
    employee_id: Optional[int] = None
    class Config: from_attributes = True


# ── Project ───────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    geofence_radius_km: float = 3.0
    sup_user_id: Optional[int] = None   # FK → User (role=sup)
    sup_name: Optional[str] = None      # legacy text (import/export)
    require_gps: Optional[bool] = True  # True=ต้อง GPS, False=กดเฉยได้

class ProjectUpdate(ProjectCreate):
    is_active: Optional[bool] = None

class ProjectOut(BaseModel):
    id: int
    name: str
    lat: Optional[float]
    lng: Optional[float]
    geofence_radius_km: float
    sup_user_id: Optional[int] = None
    sup_name: Optional[str]
    is_active: bool
    require_gps: Optional[bool] = True
    start_date: Optional[str] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    class Config: from_attributes = True


# ── Employee ──────────────────────────────────────────
class EmployeeCreate(BaseModel):
    employee_code: str
    first_name: str
    last_name: str
    age: Optional[int] = None
    department: Optional[str] = None
    employee_type: str = "รายวัน"

class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    age: Optional[int] = None
    department: Optional[str] = None
    employee_type: Optional[str] = None
    is_active: Optional[bool] = None

class EmployeeOut(BaseModel):
    id: int
    employee_code: str
    first_name: str
    last_name: str
    age: Optional[int]
    department: Optional[str]
    employee_type: str
    photo_url: Optional[str]
    is_active: bool
    active_project: Optional[str] = None
    has_account: bool = False
    created_at: datetime
    class Config: from_attributes = True


# ── Assignment ────────────────────────────────────────
class AssignRequest(BaseModel):
    employee_id: int
    project_id: int


# ── User ──────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str
    role: str = "employee"
    employee_id: Optional[int] = None

class UserUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None

class UserOut(BaseModel):
    id: int
    username: str
    role: str
    employee_id: Optional[int]
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime
    employee_name: Optional[str] = None
    class Config: from_attributes = True

class InviteOut(BaseModel):
    invite_url: str
    expires_in_hours: int = 48


# ── Audit ─────────────────────────────────────────────
class AuditOut(BaseModel):
    id: int
    username: Optional[str]
    action: str
    table_name: Optional[str]
    record_id: Optional[int]
    description: Optional[str]
    timestamp: datetime
    class Config: from_attributes = True


# ── Generic ───────────────────────────────────────────
class Msg(BaseModel):
    success: bool = True
    message: str
    data: Optional[dict] = None
