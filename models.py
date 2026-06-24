from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    geofence_radius_km = Column(Float, default=3.0)
    sup_name = Column(String, nullable=True)
    sup_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    photo_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    start_date = Column(String, nullable=True)     # วันที่เริ่มโครงการ (YYYY-MM-DD)
    closed_at = Column(DateTime, nullable=True)    # วันที่ปิดโครงการ
    require_gps = Column(Boolean, default=True)    # True=ต้อง GPS, False=กดเฉยได้ (สำหรับพื้นที่ไม่มีสัญญาณ)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assignments = relationship("Assignment", back_populates="project")
    field_values = relationship("ProjectFieldValue", back_populates="project", cascade="all, delete-orphan")
    sup_user = relationship("User", foreign_keys=[sup_user_id])


class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, index=True)
    employee_code = Column(String, unique=True, index=True, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    age = Column(Integer, nullable=True)
    department = Column(String, nullable=True)
    employee_type = Column(String, nullable=False, default="daily")
    photo_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    gdrive_folder_url = Column(String, nullable=True)  # Google Drive folder link

    user = relationship("User", back_populates="employee", uselist=False)
    assignments = relationship("Assignment", back_populates="employee")
    field_values = relationship("EmployeeFieldValue", back_populates="employee", cascade="all, delete-orphan")
    documents = relationship("EmployeeDocument", back_populates="employee", cascade="all, delete-orphan")


class Assignment(Base):
    __tablename__ = "assignments"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    unassigned_at = Column(DateTime, nullable=True)

    employee = relationship("Employee", back_populates="assignments")
    project = relationship("Project", back_populates="assignments")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)
    role = Column(String, default="employee", nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    invite_token = Column(String, nullable=True, index=True)
    invite_expires = Column(DateTime, nullable=True)
    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee", back_populates="user")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmployeeCustomField(Base):
    __tablename__ = "employee_custom_fields"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    field_type = Column(String, default="text")
    options = Column(Text, nullable=True)
    is_sensitive = Column(Boolean, default=False)
    is_required = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    values = relationship("EmployeeFieldValue", back_populates="field", cascade="all, delete-orphan")


class EmployeeFieldValue(Base):
    __tablename__ = "employee_field_values"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    field_id = Column(Integer, ForeignKey("employee_custom_fields.id"), nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("employee_id", "field_id"),)

    employee = relationship("Employee", back_populates="field_values")
    field = relationship("EmployeeCustomField", back_populates="values")


class ProjectCustomField(Base):
    __tablename__ = "project_custom_fields"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    field_type = Column(String, default="text")
    options = Column(Text, nullable=True)
    is_sensitive = Column(Boolean, default=False)
    is_required = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    values = relationship("ProjectFieldValue", back_populates="field", cascade="all, delete-orphan")


class ProjectFieldValue(Base):
    __tablename__ = "project_field_values"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    field_id = Column(Integer, ForeignKey("project_custom_fields.id"), nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("project_id", "field_id"),)

    project = relationship("Project", back_populates="field_values")
    field = relationship("ProjectCustomField", back_populates="values")


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    leave_type = Column(String, nullable=False, default="leave")
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    days = Column(Float, default=1.0)
    reason = Column(Text, nullable=True)
    status = Column(String, default="pending", nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    admin_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee")
    project = relationship("Project")
    approver = relationship("User", foreign_keys=[approved_by])


class OTRequest(Base):
    __tablename__ = "ot_requests"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    ot_date = Column(String, nullable=False)
    start_time = Column(String, nullable=True)
    end_time = Column(String, nullable=True)
    hours = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(String, default="pending", nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    admin_note = Column(Text, nullable=True)
    is_holiday_work = Column(Boolean, default=False)  # True = holiday OT (2x rate)
    ot_rate = Column(Float, default=1.5)              # 1.5 = normal, 2.0 = holiday
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee")
    project = relationship("Project")
    approver = relationship("User", foreign_keys=[approved_by])


class CheckIn(Base):
    __tablename__ = "checkins"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=True)
    work_date   = Column(String, nullable=False)

    check_in_time  = Column(DateTime, nullable=True)
    check_in_lat   = Column(Float, nullable=True)
    check_in_lng   = Column(Float, nullable=True)
    check_in_dist  = Column(Float, nullable=True)
    check_in_ok    = Column(Boolean, nullable=True)

    check_out_time = Column(DateTime, nullable=True)
    check_out_lat  = Column(Float, nullable=True)
    check_out_lng  = Column(Float, nullable=True)
    check_out_dist = Column(Float, nullable=True)
    check_out_ok   = Column(Boolean, nullable=True)

    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("Employee")
    project  = relationship("Project")


class ElearningContent(Base):
    __tablename__ = "elearning_contents"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    category = Column(String, nullable=False, default="admin")
    content_type = Column(String, nullable=False, default="video")
    url = Column(String, nullable=False)
    thumbnail_url = Column(String, nullable=True)
    duration_min = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    allowed_roles = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", foreign_keys=[created_by])
    logs = relationship("ElearningLog", back_populates="content", cascade="all, delete-orphan")


class ElearningLog(Base):
    __tablename__ = "elearning_logs"
    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("elearning_contents.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    watched_at = Column(DateTime, default=datetime.utcnow)
    duration_sec = Column(Integer, nullable=True)
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)

    content = relationship("ElearningContent", back_populates="logs")
    user = relationship("User")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    sender_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    message     = Column(Text, nullable=False)
    is_read     = Column(Boolean, default=False)
    is_announce = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

    sender   = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])


class SupTeamMember(Base):
    __tablename__ = "sup_team_members"
    id           = Column(Integer, primary_key=True)
    sup_user_id  = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=False)
    employee_id  = Column(Integer, ForeignKey("employees.id"), nullable=False)
    added_at     = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("project_id", "employee_id"),)

    sup      = relationship("User",     foreign_keys=[sup_user_id])
    project  = relationship("Project",  foreign_keys=[project_id])
    employee = relationship("Employee", foreign_keys=[employee_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String, nullable=True)
    action = Column(String, nullable=False)
    table_name = Column(String, nullable=True)
    record_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class Complaint(Base):
    __tablename__ = "complaints"
    id          = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=True)
    comp_type   = Column(String, nullable=False, default="complaint")  # complaint | suggestion
    subject     = Column(String, nullable=False)
    detail      = Column(Text, nullable=False)
    is_anonymous = Column(Boolean, default=False)
    status      = Column(String, default="pending", nullable=False)   # pending | reviewed | closed
    admin_note  = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee    = relationship("Employee")
    project     = relationship("Project")
    reviewer    = relationship("User", foreign_keys=[reviewed_by])


# ─────────── KPI Module ───────────

class KPIPeriod(Base):
    """ช่วงเวลา KPI เช่น รายเดือน รายไตรมาส"""
    __tablename__ = "kpi_periods"
    id           = Column(Integer, primary_key=True)
    name         = Column(String, nullable=False)
    period_type  = Column(String, default="monthly")
    start_date   = Column(String, nullable=False)
    end_date     = Column(String, nullable=False)
    factor_hr          = Column(Float, default=0.3)
    factor_project     = Column(Float, default=0.3)
    factor_elearning   = Column(Float, default=0.2)
    factor_achievement = Column(Float, default=0.2)
    is_published   = Column(Boolean, default=False)
    is_closed      = Column(Boolean, default=False)
    created_by     = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    scores   = relationship("KPIScore", back_populates="period", cascade="all, delete-orphan")
    creator  = relationship("User", foreign_keys=[created_by])


class KPIScore(Base):
    """คะแนน KPI ต่อคน ต่อ period"""
    __tablename__ = "kpi_scores"
    id            = Column(Integer, primary_key=True)
    period_id     = Column(Integer, ForeignKey("kpi_periods.id"), nullable=False)
    employee_id   = Column(Integer, ForeignKey("employees.id"), nullable=False)
    score_hr          = Column(Float, nullable=True)
    score_project     = Column(Float, nullable=True)
    score_elearning   = Column(Float, nullable=True)
    score_achievement = Column(Float, nullable=True)
    score_total   = Column(Float, nullable=True)
    achievement_by    = Column(Integer, ForeignKey("users.id"), nullable=True)
    achievement_note  = Column(Text, nullable=True)
    achievement_at    = Column(DateTime, nullable=True)
    locked_at     = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("period_id", "employee_id"),)

    period   = relationship("KPIPeriod", back_populates="scores")
    employee = relationship("Employee")
    giver    = relationship("User", foreign_keys=[achievement_by])


class ProjectHistory(Base):
    """บันทึกประวัติการทำงานในโครงการ"""
    __tablename__ = "project_history"
    id           = Column(Integer, primary_key=True)
    employee_id  = Column(Integer, ForeignKey("employees.id"), nullable=False)
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=False)
    role         = Column(String, nullable=False, default="employee")
    start_date   = Column(String, nullable=True)
    end_date     = Column(String, nullable=True)
    note         = Column(Text, nullable=True)
    recorded_at  = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee")
    project  = relationship("Project")


class Holiday(Base):
    """วันหยุด — public (ราชการ/นักขัตฤกษ์) หรือ weekly (เสาร์/อาทิตย์)"""
    __tablename__ = "holidays"
    id           = Column(Integer, primary_key=True)
    date         = Column(String, nullable=False)       # YYYY-MM-DD (public) หรือ "0"-"6" (weekly weekday)
    name         = Column(String, nullable=False)
    holiday_type = Column(String, default="public")     # "public" | "weekly"
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)


class EmployeeDocument(Base):
    """เอกสารของพนักงาน — link ไปยัง Google Drive"""
    __tablename__ = "employee_documents"
    id            = Column(Integer, primary_key=True)
    employee_id   = Column(Integer, ForeignKey("employees.id"), nullable=False)
    doc_type      = Column(String, nullable=False)   # id_card / house_reg / job_app / resume / other
    doc_name      = Column(String, nullable=False)   # ชื่อเอกสาร (ไทย)
    gdrive_url    = Column(String, nullable=True)    # ลิงก์ไฟล์ใน Drive
    is_uploaded   = Column(Boolean, default=False)   # ✓ ส่งแล้ว / ✗ ยังไม่มี
    note          = Column(Text, nullable=True)
    uploaded_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at   = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee   = relationship("Employee", back_populates="documents")
    uploader   = relationship("User", foreign_keys=[uploaded_by])
