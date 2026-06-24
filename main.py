from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
import os
from dotenv import load_dotenv

from database import engine, SessionLocal
import models
from auth import hash_password
from routers import auth_router, employees, projects, users, audit, settings, employee_fields, project_fields, leave, ot, checkin, elearning, chat, dashboard, complaint, holiday, kpi, employee_docs, report, payroll

load_dotenv()

# -- Create new tables
models.Base.metadata.create_all(bind=engine)

# -- Migrate existing tables
def _safe_alter(conn, sql: str):
    try:
        conn.execute(text(sql))
        conn.commit()
    except Exception:
        pass

with engine.connect() as _conn:
    _safe_alter(_conn, "ALTER TABLE employees ADD COLUMN notes TEXT")
    _safe_alter(_conn, "ALTER TABLE employees ADD COLUMN photo_url TEXT")
    _safe_alter(_conn, "ALTER TABLE projects ADD COLUMN notes TEXT")
    _safe_alter(_conn, "ALTER TABLE projects ADD COLUMN photo_url TEXT")
    _safe_alter(_conn, "ALTER TABLE projects ADD COLUMN sup_name TEXT")
    _safe_alter(_conn, "ALTER TABLE elearning_contents ADD COLUMN content_type TEXT DEFAULT 'video'")
    _safe_alter(_conn, "ALTER TABLE elearning_logs ADD COLUMN completed INTEGER DEFAULT 0")
    _safe_alter(_conn, "ALTER TABLE elearning_logs ADD COLUMN completed_at TEXT")
    _safe_alter(_conn, "ALTER TABLE projects ADD COLUMN sup_user_id INTEGER REFERENCES users(id)")
    _safe_alter(_conn, "ALTER TABLE sup_team_members ADD COLUMN project_id INTEGER REFERENCES projects(id)")
    # complaints extra columns (for existing DBs)
    _safe_alter(_conn, "ALTER TABLE complaints ADD COLUMN admin_note TEXT")
    _safe_alter(_conn, "ALTER TABLE complaints ADD COLUMN reviewed_by INTEGER REFERENCES users(id)")
    _safe_alter(_conn, "ALTER TABLE complaints ADD COLUMN reviewed_at TEXT")
    _safe_alter(_conn, "ALTER TABLE ot_requests ADD COLUMN is_holiday_work INTEGER DEFAULT 0")
    _safe_alter(_conn, "ALTER TABLE ot_requests ADD COLUMN ot_rate REAL DEFAULT 1.5")
    # Project Closure fields
    _safe_alter(_conn, "ALTER TABLE projects ADD COLUMN start_date TEXT")
    _safe_alter(_conn, "ALTER TABLE projects ADD COLUMN closed_at TEXT")
    # Employee Documents
    _safe_alter(_conn, "ALTER TABLE employees ADD COLUMN gdrive_folder_url TEXT")
    # GPS check-in mode per project (1=ต้องใช้ GPS, 0=กดเฉยได้)
    _safe_alter(_conn, "ALTER TABLE projects ADD COLUMN require_gps INTEGER DEFAULT 1")

    # SMTP settings defaults
    smtp_defaults = [
        ("smtp_host", ""), ("smtp_port", "587"), ("smtp_user", ""),
        ("smtp_password", ""), ("smtp_from_name", "Head Office ZL"),
        ("smtp_from_email", ""), ("smtp_use_tls", "true"),
    ]
    for key, val in smtp_defaults:
        _conn.execute(
            text("INSERT OR IGNORE INTO settings (key, value) VALUES (:k, :v)"),
            {"k": key, "v": val}
        )
    # Holiday table
    from sqlalchemy import text as _text
    try:
        _conn.execute(_text("""CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            holiday_type TEXT DEFAULT 'public',
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )"""))
        _conn.commit()
    except Exception:
        pass

app = FastAPI(title="Head Office ZL -- HR System", version="1.0.0")

app.include_router(auth_router.router)
app.include_router(employees.router)
app.include_router(projects.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(settings.router)
app.include_router(employee_fields.router)
app.include_router(project_fields.router)
app.include_router(leave.router)
app.include_router(ot.router)
app.include_router(checkin.router)
app.include_router(elearning.router)
app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(complaint.router)
app.include_router(holiday.router)
app.include_router(kpi.router)
app.include_router(report.router)
app.include_router(payroll.router)
app.include_router(employee_docs.router)

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
@app.get("/register")
async def serve_spa():
    return FileResponse("static/index.html")


@app.on_event("startup")
def auto_seed():
    """Seed demo data ถ้า DB ว่าง (ไม่มี admin) — รัน safe ทุก startup"""
    from seed import run_seed
    db: Session = SessionLocal()
    try:
        run_seed(db)
    except Exception as e:
        print(f"[SEED] warning: {e}")
    finally:
        db.close()


@app.on_event("startup")
def sync_sup_team_members():
    """Repair SupTeamMember ให้ตรงกับ active Assignment — รัน safe ทุก startup"""
    db: Session = SessionLocal()
    try:
        assigns = (
            db.query(models.Assignment, models.Project)
            .join(models.Project, models.Project.id == models.Assignment.project_id)
            .filter(
                models.Assignment.is_active == True,
                models.Project.sup_user_id.isnot(None),
            )
            .all()
        )
        created = 0
        for a, p in assigns:
            existing = db.query(models.SupTeamMember).filter(
                models.SupTeamMember.employee_id == a.employee_id,
                models.SupTeamMember.project_id == a.project_id,
            ).first()
            if not existing:
                db.add(models.SupTeamMember(
                    sup_user_id=p.sup_user_id,
                    project_id=a.project_id,
                    employee_id=a.employee_id,
                ))
                created += 1
        # Remove stale SupTeamMember (no active assignment)
        all_stm = db.query(models.SupTeamMember).all()
        deleted = 0
        for stm in all_stm:
            active_assign = db.query(models.Assignment).filter(
                models.Assignment.employee_id == stm.employee_id,
                models.Assignment.project_id == stm.project_id,
                models.Assignment.is_active == True,
            ).first()
            if not active_assign:
                db.delete(stm)
                deleted += 1
        if created or deleted:
            db.commit()
            print(f"[Startup] SupTeamMember sync: created={created}, deleted_stale={deleted}")
    except Exception as e:
        print(f"[Startup] SupTeamMember sync error: {e}")
        db.rollback()
    finally:
        db.close()


@app.on_event("startup")
def seed_admin():
    db: Session = SessionLocal()
    try:
        admin = db.query(models.User).filter(models.User.username == "admin").first()
        if not admin:
            admin_password = os.getenv("ADMIN_PASSWORD", "admin1234")
            admin = models.User(
                username=os.getenv("ADMIN_USERNAME", "admin"),
                hashed_password=hash_password(admin_password),
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print("\n" + "="*50)
            print("  HEAD OFFICE ZL -- HR SYSTEM")
            print(f"  Admin created | user: admin | pass: {admin_password}")
            print(f"  URL: http://localhost:8000")
            print("="*50 + "\n")
        else:
            print("\nHR System started -> http://localhost:8000\n")
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
