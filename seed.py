"""
seed.py — Auto-seed demo data สำหรับ HEAD OFFICE ZL staging
รันครั้งเดียวตอน startup ถ้า DB ว่าง (ไม่มี admin user)
"""
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
import models
from auth import hash_password

PW = hash_password("123456")          # password ร่วมทุกคน
TODAY = date.today()


def _d(delta_days: int) -> str:
    return (TODAY + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def run_seed(db: Session):
    """รัน seed — จะ skip ถ้ามี admin อยู่แล้ว"""
    if db.query(models.User).filter(models.User.role == "admin").first():
        return  # มีข้อมูลแล้ว ไม่ต้อง seed

    print("[SEED] เริ่ม seed demo data …")

    # ─────────────────────────────────────────────
    # 1. ADMIN USER
    # ─────────────────────────────────────────────
    admin = models.User(username="admin", hashed_password=PW, role="admin", is_active=True)
    db.add(admin)
    db.flush()

    # ─────────────────────────────────────────────
    # 2. EMPLOYEES (กำหนดตัวละครให้ครบ)
    # ─────────────────────────────────────────────
    emp_data = [
        # code,  first,     last,       age, dept,           type,     notes
        ("EMP001", "วิชัย",   "มงคลทอง",  32,  "ช่างไฟฟ้า",   "daily",  "ช่างไฟฟ้าประสบการณ์ 8 ปี"),
        ("EMP002", "สายฝน",  "เพ็งจันทร์", 27, "ช่างกลโรงงาน","daily",  "เชี่ยวชาญซ่อม CNC"),
        ("EMP003", "นิพนธ์",  "ดีงาม",     35,  "ช่างเชื่อม",  "daily",  "ผ่านการอบรม WPS/WPQR"),
        # SUP เป็น employee ด้วย (ใช้ link user)
        ("SUP001", "สมหญิง", "ชมภู",      38,  "หัวหน้างาน",  "monthly","SUP โครงการ A และ B"),
        ("SUP002", "ประภา",  "สุขใจ",     41,  "หัวหน้างาน",  "monthly","SUP โครงการ C"),
    ]
    emps = {}
    for code, fn, ln, age, dept, etype, notes in emp_data:
        e = models.Employee(
            employee_code=code, first_name=fn, last_name=ln,
            age=age, department=dept, employee_type=etype,
            notes=notes, is_active=True
        )
        db.add(e)
        db.flush()
        emps[code] = e

    # ─────────────────────────────────────────────
    # 3. USERS (SUP + EMPLOYEE บัญชี)
    # ─────────────────────────────────────────────
    sup1_user = models.User(
        username="SUP_1", hashed_password=PW, role="sup",
        employee_id=emps["SUP001"].id, is_active=True
    )
    sup2_user = models.User(
        username="SUP_2", hashed_password=PW, role="sup",
        employee_id=emps["SUP002"].id, is_active=True
    )
    emp1_user = models.User(
        username="EMP001", hashed_password=PW, role="employee",
        employee_id=emps["EMP001"].id, is_active=True
    )
    emp2_user = models.User(
        username="EMP002", hashed_password=PW, role="employee",
        employee_id=emps["EMP002"].id, is_active=True
    )
    emp3_user = models.User(
        username="EMP003", hashed_password=PW, role="employee",
        employee_id=emps["EMP003"].id, is_active=True
    )
    db.add_all([sup1_user, sup2_user, emp1_user, emp2_user, emp3_user])
    db.flush()

    # ─────────────────────────────────────────────
    # 4. PROJECTS (3 โครงการ)
    # ─────────────────────────────────────────────
    proj_data = [
        # name,                               lat,      lng,      sup_user, sup_name
        ("โครงการ A — ปรับปรุงระบบไฟฟ้า",  13.7563,  100.5018, sup1_user, "สมหญิง ชมภู"),
        ("โครงการ B — ติดตั้งระบบ CCTV",    13.8621,  100.4930, sup1_user, "สมหญิง ชมภู"),
        ("โครงการ C — ซ่อมบำรุงเครื่องจักร", 14.0000, 100.6000, sup2_user, "ประภา สุขใจ"),
    ]
    projs = []
    for name, lat, lng, sup_user, sup_name in proj_data:
        p = models.Project(
            name=name, lat=lat, lng=lng,
            sup_user_id=sup_user.id, sup_name=sup_name,
            require_gps=False,           # staging: ปิด GPS บังคับ ทดสอบง่าย
            is_active=True,
            start_date=_d(-30)
        )
        db.add(p)
        db.flush()
        projs.append(p)

    proj_a, proj_b, proj_c = projs

    # ─────────────────────────────────────────────
    # 5. ASSIGNMENTS (พนักงาน → โครงการ)
    # ─────────────────────────────────────────────
    assignments = [
        (emps["EMP001"], proj_a),   # วิชัย → A
        (emps["EMP002"], proj_a),   # สายฝน → A
        (emps["EMP003"], proj_c),   # นิพนธ์ → C
        (emps["SUP001"], proj_a),   # สมหญิง → A (primary)
        (emps["SUP002"], proj_c),   # ประภา → C
    ]
    for emp, proj in assignments:
        a = models.Assignment(employee_id=emp.id, project_id=proj.id, is_active=True)
        db.add(a)
    db.flush()

    # SupTeamMember sync (โครงการ A)
    for emp in [emps["EMP001"], emps["EMP002"]]:
        stm = models.SupTeamMember(
            sup_user_id=sup1_user.id,
            project_id=proj_a.id,
            employee_id=emp.id
        )
        db.add(stm)
    # โครงการ C
    stm_c = models.SupTeamMember(
        sup_user_id=sup2_user.id,
        project_id=proj_c.id,
        employee_id=emps["EMP003"].id
    )
    db.add(stm_c)
    db.flush()

    # ─────────────────────────────────────────────
    # 6. CUSTOM FIELDS (employee)
    # ─────────────────────────────────────────────
    import json
    cf_gender = models.EmployeeCustomField(
        name="เพศ", field_type="select",
        options=json.dumps(["ชาย", "หญิง", "ไม่ระบุ"], ensure_ascii=False),
        is_sensitive=False, sort_order=1
    )
    cf_phone = models.EmployeeCustomField(
        name="เบอร์โทรศัพท์", field_type="text",
        is_sensitive=False, sort_order=2
    )
    cf_blood = models.EmployeeCustomField(
        name="กรุ๊ปเลือด", field_type="select",
        options=json.dumps(["A", "B", "AB", "O"], ensure_ascii=False),
        is_sensitive=False, sort_order=3
    )
    cf_emerg = models.EmployeeCustomField(
        name="เบอร์ฉุกเฉิน", field_type="text",
        is_sensitive=False, sort_order=4
    )
    db.add_all([cf_gender, cf_phone, cf_blood, cf_emerg])
    db.flush()

    # ค่า field สำหรับแต่ละพนักงาน
    field_values = [
        (emps["EMP001"].id, cf_gender.id, "ชาย"),
        (emps["EMP001"].id, cf_phone.id,  "081-234-5678"),
        (emps["EMP001"].id, cf_blood.id,  "O"),
        (emps["EMP002"].id, cf_gender.id, "หญิง"),
        (emps["EMP002"].id, cf_phone.id,  "082-345-6789"),
        (emps["EMP002"].id, cf_blood.id,  "B"),
        (emps["EMP003"].id, cf_gender.id, "ชาย"),
        (emps["EMP003"].id, cf_phone.id,  "083-456-7890"),
    ]
    for emp_id, field_id, val in field_values:
        db.add(models.EmployeeFieldValue(employee_id=emp_id, field_id=field_id, value=val))
    db.flush()

    # ─────────────────────────────────────────────
    # 7. LEAVE REQUESTS (2 ใบ)
    # ─────────────────────────────────────────────
    leave1 = models.LeaveRequest(
        employee_id=emps["EMP001"].id,
        project_id=proj_a.id,
        leave_type="ลาป่วย",
        start_date=_d(-10), end_date=_d(-9),
        days=2.0,
        reason="มีไข้สูง ไปพบแพทย์",
        status="approved",
        approved_by=admin.id,
        approved_at=datetime.utcnow() - timedelta(days=9),
    )
    leave2 = models.LeaveRequest(
        employee_id=emps["EMP002"].id,
        project_id=proj_a.id,
        leave_type="ลากิจ",
        start_date=_d(3), end_date=_d(3),
        days=1.0,
        reason="ธุระส่วนตัว",
        status="pending",
    )
    db.add_all([leave1, leave2])
    db.flush()

    # ─────────────────────────────────────────────
    # 8. OT REQUESTS (2 ใบ)
    # ─────────────────────────────────────────────
    ot1 = models.OTRequest(
        employee_id=emps["EMP001"].id,
        project_id=proj_a.id,
        ot_date=_d(-5),
        start_time="17:00", end_time="20:00", hours=3.0,
        reason="งานไฟฟ้าฉุกเฉิน — ต้องเสร็จก่อนเปิดสาย",
        status="approved",
        approved_by=admin.id,
        approved_at=datetime.utcnow() - timedelta(days=5),
        ot_rate=1.5,
    )
    ot2 = models.OTRequest(
        employee_id=emps["EMP003"].id,
        project_id=proj_c.id,
        ot_date=_d(-2),
        start_time="17:00", end_time="21:00", hours=4.0,
        reason="ซ่อมเครื่องปั๊มน้ำ — ลูกค้าต้องการเร่ง",
        status="pending",
        ot_rate=1.5,
    )
    db.add_all([ot1, ot2])
    db.flush()

    # ─────────────────────────────────────────────
    # 9. CHECK-IN HISTORY (5 รายการ — ทดสอบ report)
    # ─────────────────────────────────────────────
    checkins = [
        # (emp, proj, day_offset, in_hr, out_hr)
        (emps["EMP001"], proj_a, -7, 8, 17),
        (emps["EMP001"], proj_a, -6, 8, 17),
        (emps["EMP001"], proj_a, -5, 8, 20),   # วัน OT
        (emps["EMP002"], proj_a, -7, 8, 17),
        (emps["EMP002"], proj_a, -6, 9, 17),   # มาสาย
        (emps["EMP003"], proj_c, -7, 7, 16),
    ]
    for emp, proj, delta, in_h, out_h in checkins:
        work_dt = TODAY + timedelta(days=delta)
        ci_time = datetime(work_dt.year, work_dt.month, work_dt.day, in_h, 0, 0)
        co_time = datetime(work_dt.year, work_dt.month, work_dt.day, out_h, 0, 0)
        db.add(models.CheckIn(
            employee_id=emp.id, project_id=proj.id,
            work_date=work_dt.strftime("%Y-%m-%d"),
            check_in_time=ci_time,
            check_in_lat=proj.lat, check_in_lng=proj.lng,
            check_in_dist=0.0, check_in_ok=True,
            check_out_time=co_time,
            check_out_lat=proj.lat, check_out_lng=proj.lng,
            check_out_dist=0.0, check_out_ok=True,
        ))
    db.flush()

    # ─────────────────────────────────────────────
    # 10. ELEARNING CONTENT (2 คอร์ส)
    # ─────────────────────────────────────────────
    el1 = models.ElearningContent(
        title="ความปลอดภัยในโรงงาน",
        category="ความปลอดภัย",
        content_type="video",
        url="https://www.youtube.com/watch?v=8DXDmI85VUw",
        thumbnail_url="https://img.youtube.com/vi/8DXDmI85VUw/hqdefault.jpg",
        duration_min=15,
        description="บทเรียนพื้นฐานความปลอดภัยในพื้นที่โรงงานอุตสาหกรรม PPE, Lockout/Tagout, สัญลักษณ์เตือน",
        allowed_roles='["admin","sup","employee"]',
        is_active=True,
        created_by=admin.id,
    )
    el2 = models.ElearningContent(
        title="ไฟฟ้าเบื้องต้น",
        category="วิศวกรรมไฟฟ้า",
        content_type="video",
        url="https://www.youtube.com/watch?v=D3nVECGPMcg&list=PLpft6HIXr4FEgiKGAIoZXZu4t_oFtXOQ-",
        thumbnail_url="https://img.youtube.com/vi/D3nVECGPMcg/hqdefault.jpg",
        duration_min=30,
        description="ความรู้ไฟฟ้าเบื้องต้น วงจร กระแส แรงดัน กฎของโอห์ม เหมาะสำหรับช่างไฟฟ้าใหม่",
        allowed_roles='["admin","sup","employee"]',
        is_active=True,
        created_by=admin.id,
    )
    db.add_all([el1, el2])
    db.flush()

    # elearning log — EMP001 ดู el1 ไปแล้ว
    db.add(models.ElearningLog(
        content_id=el1.id, user_id=emp1_user.id,
        watched_at=datetime.utcnow() - timedelta(days=3),
        duration_sec=900, completed=True,
        completed_at=datetime.utcnow() - timedelta(days=3),
    ))

    # ─────────────────────────────────────────────
    # 11. COMPLAINT (1 รายการ)
    # ─────────────────────────────────────────────
    db.add(models.Complaint(
        employee_id=emps["EMP003"].id,
        project_id=proj_c.id,
        comp_type="suggestion",
        subject="ขอปรับปรุงอุปกรณ์ป้องกันส่วนบุคคล",
        detail="ถุงมือที่ให้มาบางเกินไปสำหรับงานเชื่อม ขอให้เพิ่มถุงมือหนัง ป้องกันประกายไฟได้ดีกว่า",
        is_anonymous=False,
        status="pending",
    ))

    # ─────────────────────────────────────────────
    # 12. SETTINGS (quota + complaint email)
    # ─────────────────────────────────────────────
    from sqlalchemy import text
    settings_data = [
        ("leave_quota_sick",        "30"),
        ("leave_quota_personal",    "5"),
        ("leave_quota_annual",      "10"),
        ("leave_quota_maternity",   "98"),
        ("complaint_email",         ""),   # Admin ตั้งเองได้ใน settings
        ("company_name",            "บริษัท เซโรลอส จำกัด"),
        ("ot_rate_normal",          "1.5"),
        ("ot_rate_holiday",         "2.0"),
    ]
    for k, v in settings_data:
        db.execute(
            text("INSERT OR IGNORE INTO settings (key, value) VALUES (:k, :v)"),
            {"k": k, "v": v}
        )

    db.commit()
    print("[SEED] ✅ seed สำเร็จ — admin/SUP_1/SUP_2/EMP001/EMP002/EMP003 password: 123456")
