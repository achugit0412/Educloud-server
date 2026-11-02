from typing import Any, cast
from functools import wraps
from datetime import date, datetime, timedelta,time

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)

from werkzeug.security import check_password_hash,generate_password_hash
from db import get_db_connection


app = Flask(__name__)
app.secret_key = "educloud-development-secret"


def format_time(value: Any) -> str:
    """Format database time values safely for display."""

    if value is None:
        return ""

    # MySQL TIME values are returned as timedelta
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())

        hours = (total_seconds // 3600) % 24
        minutes = (total_seconds % 3600) // 60

        hour = hours % 12

        if hour == 0:
            hour = 12

        period = "AM" if hours < 12 else "PM"

        return f"{hour:02d}:{minutes:02d} {period}"

    # datetime.time or datetime.datetime
    if hasattr(value, "hour") and hasattr(value, "minute"):
        hours = int(value.hour)
        minutes = int(value.minute)

        hour = hours % 12

        if hour == 0:
            hour = 12

        period = "AM" if hours < 12 else "PM"

        return f"{hour:02d}:{minutes:02d} {period}"

    # Fallback for string values
    text = str(value)

    for time_format in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, time_format)
            return parsed.strftime("%I:%M %p")
        except ValueError:
            pass

    return text


def format_time_input(value: Any) -> str:
    """Format database time values as the 24-hour value needed by time inputs."""

    if value is None:
        return ""

    if isinstance(value, timedelta):
        total_minutes = int(value.total_seconds() // 60) % (24 * 60)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours:02d}:{minutes:02d}"

    if hasattr(value, "hour") and hasattr(value, "minute"):
        return f"{int(value.hour):02d}:{int(value.minute):02d}"

    text = str(value)
    for time_format in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, time_format)
            return parsed.strftime("%H:%M")
        except ValueError:
            pass

    return ""

# ==========================================
# AUTHENTICATION
# ==========================================

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


def student_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        if session.get("role") != "student":
            return redirect(url_for("staff_dashboard"))

        return view(*args, **kwargs)

    return wrapped_view


def staff_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        if session.get("role") != "staff":
            return redirect(url_for("student_dashboard"))

        return view(*args, **kwargs)

    return wrapped_view


# ==========================================
# LOGIN
# ==========================================


@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():

    if "user_id" in session:
        if session.get("role") == "student":
            return redirect(url_for("student_dashboard"))
        elif session.get("role") == "staff":
            return redirect(url_for("staff_dashboard"))

        session.clear()

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        db = None
        cursor = None

        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)

            cursor.execute("""
                SELECT id, name, email, password, role
                FROM users
                WHERE LOWER(TRIM(email)) = %s
            """, (email,))

            user = cast(dict[str, Any] | None, cursor.fetchone())

            if not user or not check_password_hash(
                str(user["password"]), password
            ):
                return render_template(
                    "login.html",
                    error="Invalid email or password"
                )

            # Remove any previous account's session data.
            session.clear()

            # Store the actual authenticated user's details.
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["email"] = user["email"]
            session["role"] = user["role"].strip().lower()

            if session["role"] == "student":
                return redirect(url_for("student_dashboard"))

            elif session["role"] == "staff":
                return redirect(url_for("staff_dashboard"))

            session.clear()
            return render_template(
                "login.html",
                error="This account has an invalid role."
            )

        except Exception:
            app.logger.exception("Login error")
            return render_template(
                "login.html",
                error="Unable to log in. Please try again."
            )

        finally:
            if cursor:
                cursor.close()
            if db:
                db.close()

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ==========================================
# STUDENT DASHBOARD
# ==========================================

from datetime import datetime, date, timedelta
from typing import Any, cast

def format_exam_time(value):
    if value is None:
        return "Time not announced"

    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds()) % 86400
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60

        return datetime(
            2000, 1, 1, hours, minutes
        ).strftime("%I:%M %p")

    if hasattr(value, "strftime"):
        return value.strftime("%I:%M %p")

    return str(value)

@app.route("/student/dashboard")
@student_required
def student_dashboard():

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        user_id = session.get("user_id")

        print("SESSION USER ID:", user_id)

        # ==========================================
        # 1. STUDENT DETAILS
        # ==========================================

        cursor.execute("""
            SELECT
                s.*,
                u.name,
                u.email
            FROM students s
            JOIN users u ON u.id = s.user_id
            WHERE u.id = %s
        """, (user_id,))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if not student:
            return "Student record not found", 404

        student = cast(dict[str, Any], student)
        student_id = student["id"]

        print("STUDENT:", student["name"])
        print("REGISTER NUMBER:", student.get("register_no"))

        # ==========================================
        # 2. CGPA CALCULATION
        # ==========================================

        cursor.execute("""
            SELECT
                SUM(grade_point * credits) AS total_points,
                SUM(credits) AS total_credits
            FROM marks
            WHERE student_id = %s
              AND semester IS NOT NULL
              AND grade_point IS NOT NULL
              AND credits IS NOT NULL
        """, (student_id,))

        cgpa_row = cast(
            dict[str, Any],
            cursor.fetchone() or {}
        )

        total_points = float(
            cgpa_row.get("total_points") or 0
        )

        total_credits = float(
            cgpa_row.get("total_credits") or 0
        )

        cgpa = calculate_student_cgpa(cursor, student_id)

        # ==========================================
        # 3. OVERALL ATTENDANCE
        # ==========================================

        cursor.execute("""
            SELECT
                COUNT(*) AS total_classes,
                SUM(
                    CASE
                        WHEN LOWER(TRIM(status)) = 'present'
                        THEN 1
                        ELSE 0
                    END
                ) AS present_classes
            FROM attendance
            WHERE student_id = %s
        """, (student_id,))

        attendance_row = cast(
            dict[str, Any],
            cursor.fetchone() or {}
        )

        total_classes = int(
            attendance_row.get("total_classes") or 0
        )

        present_classes = int(
            attendance_row.get("present_classes") or 0
        )

        attendance_percentage = (
            (present_classes / total_classes) * 100
            if total_classes > 0
            else 0.0
        )

        # ==========================================
        # 4. SUBJECT-WISE ATTENDANCE
        # ==========================================

        cursor.execute("""
            SELECT
                subject,
                COUNT(*) AS total_classes,
                SUM(
                    CASE
                        WHEN LOWER(TRIM(status)) = 'present'
                        THEN 1
                        ELSE 0
                    END
                ) AS present_classes
            FROM attendance
            WHERE student_id = %s
            GROUP BY subject
            ORDER BY subject ASC
        """, (student_id,))

        attendance_rows = cursor.fetchall() or []

        attendance_by_subject = []

        for row in attendance_rows:

            row_data = cast(dict[str, Any], row)
            item = dict(row_data)

            subject_total = int(
                item.get("total_classes") or 0
            )

            subject_present = int(
                item.get("present_classes") or 0
            )

            item["percentage"] = (
                round(
                    (subject_present / subject_total) * 100,
                    1
                )
                if subject_total > 0
                else 0.0
            )

            attendance_by_subject.append(item)

        # ==========================================
        # 5. NOTIFICATIONS
        # ==========================================

        audiences = _student_notification_audiences(student)

        notification_count = 0
        recent_notifications = []

        if audiences:

            placeholders = ", ".join(
                ["%s"] * len(audiences)
            )

            # Total notification count
            cursor.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM notifications
                WHERE status = 'Published'
                  AND LOWER(TRIM(target_audience))
                      IN ({placeholders})
                """,
                audiences
            )

            notification_row = cast(dict[str, Any], cursor.fetchone() or {})

            notification_count = int(
                notification_row.get("total") or 0
            )

            # Latest 5 notifications
            cursor.execute(
                f"""
                SELECT
                    id,
                    title,
                    message,
                    category,
                    target_audience,
                    created_at
                FROM notifications
                WHERE status = 'Published'
                  AND LOWER(TRIM(target_audience))
                      IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                LIMIT 5
                """,
                audiences
            )

            recent_notifications = (
                cursor.fetchall() or []
            )

        # ==========================================
        # 6. DATE AND GREETING
        # ==========================================

        today = date.today()
        today_date = today.strftime("%d %B %Y")

        current_hour = datetime.now().hour

        if current_hour < 12:
            greeting = "Good morning"
        elif current_hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"

        # ==========================================
        # 7. TODAY'S TIMETABLE
        # ==========================================

        today_timetable = []

        today_day = datetime.now().strftime("%A")

        cursor.execute("""
            SELECT
                subject,
                course_code,
                start_time,
                end_time,
                faculty,
                venue
            FROM timetable
            WHERE LOWER(TRIM(day)) = LOWER(%s)
              AND LOWER(TRIM(department)) = LOWER(%s)
              AND year = %s
              AND semester = %s
              AND (
                    section IS NULL
                    OR TRIM(section) = ''
                    OR LOWER(TRIM(section)) =
                       LOWER(TRIM(%s))
              )
            ORDER BY start_time ASC
        """, (
            today_day,
            student.get("department"),
            student.get("year"),
            student.get("semester"),
            student.get("section") or ""
        ))

        timetable_rows = cursor.fetchall() or []

        for row in timetable_rows:

            item = cast(dict[str, Any], row)

            item["start_time_display"] = format_exam_time(
                item.get("start_time")
            )

            item["end_time_display"] = format_exam_time(
                item.get("end_time")
            )

            today_timetable.append(item)

        # ==========================================
        # 8. UPCOMING EXAMS
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                subject,
                course_code,
                exam_date,
                exam_time,
                exam_type,
                class_name,
                venue,
                status
            FROM exams
            WHERE exam_date >= CURDATE()
              AND status = 'Upcoming'
            ORDER BY exam_date ASC, exam_time ASC
        """)

        exam_rows = cursor.fetchall() or []

        upcoming_exams = []

        for exam in exam_rows:

            exam_item = cast(dict[str, Any], exam)

            exam_date = exam_item.get("exam_date")

            if exam_date is None:
                continue

            exam_item["day"] = exam_date.strftime("%d")
            exam_item["month"] = exam_date.strftime("%b")

            exam_item["time"] = format_exam_time(
                exam_item.get("exam_time")
            )

            exam_item["label"] = (
                exam_item.get("exam_type") or "Upcoming"
            )

            upcoming_exams.append(exam_item)

        upcoming_exam_count = len(upcoming_exams)

        next_exam = (
            upcoming_exams[0]
            if upcoming_exams
            else None
        )

        if next_exam:

            next_exam_label = (
                f"{next_exam['exam_date'].strftime('%d %b %Y')} "
                f"at {next_exam['time']}"
            )

        else:
            next_exam_label = "No upcoming exams scheduled"

        # ==========================================
        # 9. OTHER VALUES
        # ==========================================

        attendance_change = ""

        # ==========================================
        # 10. DEBUG OUTPUT
        # ==========================================

        print("\n========== DASHBOARD DATA ==========")

        print("Student:", student["name"])
        print("CGPA:", cgpa)

        print("Total CGPA Points:", total_points)
        print("Total CGPA Credits:", total_credits)

        print("Attendance:", attendance_percentage)

        print("Subject Attendance:", attendance_by_subject)

        print("Today:", today_day)
        print("Today's Timetable:", today_timetable)

        print("Upcoming Exams:", upcoming_exam_count)
        print("Next Exam:", next_exam)

        print("Notification Count:", notification_count)
        print("Recent Notifications:", recent_notifications)

        print("====================================\n")

        # ==========================================
        # 11. RENDER TEMPLATE
        # ==========================================

        return render_template(
            "student/dashboard.html",

            student=student,

            cgpa=cgpa,

            attendance_percentage=round(
                attendance_percentage, 1
            ),

            attendance_by_subject=attendance_by_subject,

            today_timetable=today_timetable,

            upcoming_exams=upcoming_exams,

            upcoming_exam_count=upcoming_exam_count,

            next_exam=next_exam,

            next_exam_label=next_exam_label,

            recent_notifications=recent_notifications,

            notification_count=notification_count,

            today_date=today_date,

            greeting=greeting,

            attendance_change=attendance_change
        )

    except Exception as e:

        import traceback

        print("STUDENT DASHBOARD ERROR:", repr(e))
        traceback.print_exc()

        return "Unable to load the student dashboard.", 500

    finally:

        if cursor:
            cursor.close()

        if db:
            db.close()

def _student_notification_audiences(
    student: dict[str, Any]
) -> tuple[str, ...]:
    department = str(student.get("department") or "").strip().casefold()
    section = str(student.get("section") or "").strip().casefold()
    year = int(student.get("year") or 0)
    year_label = {
        1: "I",
        2: "II",
        3: "III",
        4: "IV"
    }.get(year, str(year))
    class_name = f"{year_label} {section}".strip().casefold()

    return (
        "student",
        "all",
        "all students",
        department,
        section,
        class_name
    )


# ==========================================
# STUDENT PROFILE
# ==========================================

def calculate_student_cgpa(cursor, student_id):

    cursor.execute("""
        SELECT
            semester,
            SUM(grade_point * credits) AS total_points,
            SUM(credits) AS total_credits
        FROM marks
        WHERE student_id = %s
          AND semester IS NOT NULL
          AND LOWER(TRIM(exam_type)) = 'end semester examination'
          AND grade_point IS NOT NULL
          AND credits > 0
        GROUP BY semester
        ORDER BY semester
    """, (student_id,))

    semester_records = cursor.fetchall()

    total_weighted_points = 0
    total_credits = 0

    for record in semester_records:

        semester_points = float(record["total_points"] or 0)
        semester_credits = int(record["total_credits"] or 0)

        if semester_credits > 0:

            # Calculate semester SGPA
            semester_sgpa = semester_points / semester_credits

            # Add weighted SGPA to cumulative calculation
            total_weighted_points += semester_sgpa * semester_credits
            total_credits += semester_credits

    if total_credits == 0:
        return 0.0

    cgpa = total_weighted_points / total_credits

    return round(cgpa, 2)

@app.route("/student/profile")
@student_required
def student_profile():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Fetch student details
        cursor.execute("""
            SELECT
                u.name,
                u.email,
                s.id AS student_id,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.section,
                s.cgpa,
                s.phone,
                s.address,
                s.date_of_birth,
                s.gender
            FROM users u
            LEFT JOIN students s ON s.user_id = u.id
            WHERE u.id = %s
            LIMIT 1
        """, (session["user_id"],))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if not student:
            return "Student account not found", 404

        # Calculate attendance
        cursor.execute("""
            SELECT
                COUNT(*) AS total_classes,
                COALESCE(
                    SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END),
                    0
                ) AS present_classes
            FROM attendance
            WHERE student_id = %s
        """, (student["student_id"],))

        attendance_data = cast(dict[str, Any] | None, cursor.fetchone()) or {}

        total_classes = int(attendance_data.get("total_classes") or 0)
        present_classes = int(attendance_data.get("present_classes") or 0)

        attendance_percentage = (
            (present_classes / total_classes) * 100
            if total_classes > 0 else 0
        )

        # --------------------------------
        # 2. CALCULATE CGPA
        # --------------------------------
        cursor.execute("""
            SELECT
                COALESCE(
                    SUM(grade_point * credits) / NULLIF(SUM(credits), 0),
                    0
                ) AS calculated_cgpa
            FROM marks
            WHERE student_id = %s
              AND LOWER(TRIM(exam_type)) = 'end semester examination'
              AND grade_point IS NOT NULL
              AND credits > 0
        """, (student["student_id"],))

        cgpa_data = cast(dict[str, Any] | None, cursor.fetchone()) or {}
        cgpa = calculate_student_cgpa(cursor, student["student_id"])
        # --------------------------------
        # 3. CALCULATE CREDITS EARNED
        # --------------------------------
        cursor.execute("""
            SELECT
                COALESCE(SUM(credits), 0) AS credits_earned
            FROM marks
            WHERE student_id = %s
              AND LOWER(TRIM(exam_type)) = 'end semester examination'
              AND grade_point > 0
              AND LOWER(TRIM(grade)) NOT IN ('f', 'fail')
              AND credits > 0
        """, (student["student_id"],))

        credit_data = cast(dict[str, Any] | None, cursor.fetchone()) or {}
        credits_earned = int(credit_data.get("credits_earned") or 0)

        # --------------------------------
        # 4. CALCULATE ACTIVE BACKLOGS
        # --------------------------------
        cursor.execute("""
            SELECT
                COUNT(DISTINCT subject) AS active_backlogs
            FROM marks
            WHERE student_id = %s
              AND LOWER(TRIM(exam_type)) = 'end semester examination'
              AND (
                  grade_point = 0
                  OR LOWER(TRIM(grade)) IN ('f', 'fail')
              )
        """, (student["student_id"],))

        backlog_data = cast(dict[str, Any] | None, cursor.fetchone()) or {}
        active_backlogs = int(backlog_data.get("active_backlogs") or 0)

        # Pass values to the template
        return render_template(
            "student/profile.html",
            student=student,
            cgpa=cgpa,
            attendance_percentage=attendance_percentage,
            credits_earned=credits_earned,
            total_credits=120,
            active_backlogs=active_backlogs,
        )

    except Exception as e:
        print("Student profile error:", repr(e))
        return "Unable to load student profile.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

# ==========================================
# CHANGE PASSWORD
# ==========================================


@app.route("/student/change-password", methods=["GET", "POST"])
@student_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("change_password"))

        if len(new_password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return redirect(url_for("change_password"))

        db = None
        cursor = None

        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)

            cursor.execute(
                "SELECT password FROM users WHERE id = %s",
                (session["user_id"],)
            )
            user = cast(dict[str, Any] | None, cursor.fetchone())

            if not user or not check_password_hash(
                str(user["password"]), current_password
            ):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("change_password"))

            hashed_password = generate_password_hash(new_password)

            cursor.execute(
                "UPDATE users SET password = %s WHERE id = %s",
                (hashed_password, session["user_id"])
            )
            db.commit()

            flash("Password changed successfully!", "success")
            return redirect(url_for("change_password"))

        except Exception as e:
            if db:
                db.rollback()
            print("Change password error:", repr(e))
            flash("Unable to change password. Please try again.", "error")
            return redirect(url_for("change_password"))

        finally:
            if cursor:
                cursor.close()
            if db:
                db.close()

    return render_template("student/change_password.html")

# ==========================================
# STUDENT ATTENDANCE
# ==========================================

@app.route("/student/attendance")
@student_required
def student_attendance():

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.id,
                u.name,
                s.register_no,
                s.department,
                s.year,
                s.semester
            FROM students s
            JOIN users u
                ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student: Any = cursor.fetchone()

        if student is None:
            return "Student record not found", 404

        student_id = int(cast(int, cast(dict[str, Any], student)["id"]))

        cursor.execute("""
            SELECT
                subject,
                SUM(
                    CASE
                        WHEN status = 'Present'
                        THEN 1
                        ELSE 0
                    END
                ) AS present,
                COUNT(*) AS total
            FROM attendance
            WHERE student_id = %s
            GROUP BY subject
            ORDER BY subject
        """, (student_id,))

        rows: Any = cursor.fetchall()

        attendance_records = []

        for row in rows:

            present = int(row["present"] or 0)
            total = int(row["total"] or 0)

            if total > 0:
                percentage = round(
                    (present / total) * 100,
                    1
                )
            else:
                percentage = 0.0

            if percentage >= 85:
                status = "Excellent"
            elif percentage >= 75:
                status = "Good"
            else:
                status = "Warning"

            attendance_records.append({
                "subject": str(row["subject"]),
                "present": present,
                "total": total,
                "percentage": percentage,
                "status": status
            })

        cursor.execute("""
            SELECT
                SUM(
                    CASE
                        WHEN status = 'Present'
                        THEN 1
                        ELSE 0
                    END
                ) AS present,
                COUNT(*) AS total
            FROM attendance
            WHERE student_id = %s
        """, (student_id,))

        overall: Any = cursor.fetchone()

        if overall is None:

            present = 0
            total = 0

        else:

            present = int(
                overall["present"] or 0
            )

            total = int(
                overall["total"] or 0
            )

        absent = total - present

        if total > 0:

            attendance_percentage = round(
                (present / total) * 100,
                1
            )

        else:

            attendance_percentage = 0.0

        return render_template(
            "student/attendance.html",
            student=student,
            attendance_records=attendance_records,
            present=present,
            absent=absent,
            total=total,
            attendance_percentage=attendance_percentage
        )

    except Exception as e:

        return f"Database error: {e}", 500

    finally:

        if cursor:
            cursor.close()

        if db:
            db.close()


# ==========================================
# STUDENT TIMETABLE
# ==========================================
@app.route("/student/timetable")
@student_required
def student_timetable():

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # 1. Fetch the logged-in student's details
        cursor.execute("""
            SELECT
                s.id,
                u.name,
                u.email,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.section
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student_row = cursor.fetchone()
        student_row_dict = (
            dict(student_row.items())
            if isinstance(student_row, dict)
            else {}
        )
        student = {
            str(key): value for key, value in student_row_dict.items()
        }

        if not student:
            return "Student record not found", 404

        department = str(
            student.get("department") or ""
        ).strip()

        try:
            year = int(str(student.get("year") or 0))
        except (TypeError, ValueError):
            year = 0

        try:
            semester = int(str(student.get("semester") or 0))
        except (TypeError, ValueError):
            semester = 0

        section = str(
            student.get("section") or ""
        ).strip()

        print("TIMETABLE STUDENT:", student.get("name") or "")
        print("DEPARTMENT:", department)
        print("YEAR:", year)
        print("SEMESTER:", semester)
        print("SECTION:", section)

        # 2. Fetch matching timetable records
        cursor.execute("""
            SELECT
                id,
                day,
                start_time,
                end_time,
                subject,
                course_code,
                faculty,
                venue
            FROM timetable
            WHERE LOWER(TRIM(department)) = LOWER(%s)
              AND year = %s
              AND semester = %s
              AND (
                    LOWER(TRIM(COALESCE(section, ''))) = LOWER(%s)
                    OR TRIM(COALESCE(section, '')) = ''
              )
            ORDER BY
                FIELD(
                    LOWER(TRIM(day)),
                    'monday',
                    'tuesday',
                    'wednesday',
                    'thursday',
                    'friday',
                    'saturday'
                ),
                start_time
        """, (
            department,
            year,
            semester,
            section
        ))

        timetable_records = [
            {
                str(key): value
                for key, value in (
                    record.items() if isinstance(record, dict) else {}
                )
            }
            for record in cursor.fetchall()
        ]

        print(
            "MATCHING TIMETABLE RECORDS:",
            len(timetable_records)
        )

        # 3. Normalize day and time values
        timetable_by_day = {
            "Monday": [],
            "Tuesday": [],
            "Wednesday": [],
            "Thursday": [],
            "Friday": [],
            "Saturday": []
        }

        day_names = {
            "monday": "Monday",
            "tuesday": "Tuesday",
            "wednesday": "Wednesday",
            "thursday": "Thursday",
            "friday": "Friday",
            "saturday": "Saturday"
        }

        for record in timetable_records:

            raw_day = str(
                record.get("day") or ""
            ).strip().lower()

            normalized_day = day_names.get(raw_day)

            record["day"] = normalized_day or record.get("day")

            record["start_time_display"] = format_time(
                record.get("start_time")
            )

            record["end_time_display"] = format_time(
                record.get("end_time")
            )

            if normalized_day:
                timetable_by_day[normalized_day].append(record)

        # 4. Weekly summary
        day_order = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday"
        ]

        total_classes = len(timetable_records)

        class_hours = 0.0
        lab_count = 0

        for record in timetable_records:

            start_time = record.get("start_time")
            end_time = record.get("end_time")

            duration = 0

            if (
                isinstance(start_time, timedelta)
                and isinstance(end_time, timedelta)
            ):
                duration = (
                    end_time.total_seconds()
                    - start_time.total_seconds()
                ) / 3600

            elif (
                isinstance(start_time, (datetime, time))
                and isinstance(end_time, (datetime, time))
            ):
                start_minutes = (
                    start_time.hour * 60
                    + start_time.minute
                )

                end_minutes = (
                    end_time.hour * 60
                    + end_time.minute
                )

                duration = (
                    end_minutes - start_minutes
                ) / 60

            if duration > 0:
                class_hours += duration

            subject = str(
                record.get("subject") or ""
            ).lower()

            if "lab" in subject or "practical" in subject:
                lab_count += 1

        working_days = sum(
            1 for day in day_order
            if timetable_by_day[day]
        )

        # 5. Get today's timetable
        current_day = datetime.now().strftime("%A")

        if current_day not in timetable_by_day:
            current_day = "Monday"

        today_classes = timetable_by_day.get(
            current_day, []
        )

        print("CURRENT DAY:", current_day)
        print("TODAY'S CLASSES:", len(today_classes))

        # 6. Render timetable page
        return render_template(
            "student/timetable.html",

            student=student,

            timetable_records=timetable_records,
            timetable_by_day=timetable_by_day,
            day_order=day_order,

            current_day=current_day,
            today_classes=today_classes,

            total_classes=total_classes,
            class_hours=round(class_hours, 1),
            lab_count=lab_count,
            working_days=working_days
        )

    except Exception as e:

        print("Student timetable error:", repr(e))

        return f"Database error: {e}", 500

    finally:

        if cursor:
            cursor.close()

        if db:
            db.close()


# ==========================================
# STUDENT MARKS
# ==========================================


@app.route("/student/marks")
@student_required
def student_marks():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # 1. Get student details
        cursor.execute("""
            SELECT
                s.id,
                u.name,
                u.email,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.section,
                s.cgpa
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if student is None:
            return "Student record not found", 404

        student_row = cast(dict[str, Any], student)
        student_id = int(student_row["id"])

        # 2. Fetch marks for all semesters and assessments
        cursor.execute("""
            SELECT
                id,
                semester,
                subject,
                exam_type,
                internal_marks,
                external_marks,
                total_marks,
                grade,
                grade_point,
                credits
            FROM marks
            WHERE student_id = %s
            ORDER BY semester, subject, exam_type
        """, (student_id,))

        rows = cast(list[dict[str, Any]], cursor.fetchall())

        print("Student ID:", student_id)
        print("Marks records fetched:", len(rows))

        marks_records = []

        course_codes = {
            "Data Structures": "CS3501",
            "Database Management Systems": "CS3502",
            "Computer Networks": "CS3503",
            "Operating Systems": "CS3504",
            "Artificial Intelligence": "CS3505",
            "Programming Lab": "CS3511"
        }

        # 3. Calculate semester-wise and overall academic performance

        semester_credit_points = {}
        semester_credits = {}

        credits_earned = 0
        backlogs = 0

        course_codes = {
            "Data Structures": "CS3501",
            "Database Management Systems": "CS3502",
            "Computer Networks": "CS3503",
            "Operating Systems": "CS3504",
            "Artificial Intelligence": "CS3505",
            "Programming Lab": "CS3511"
        }

        marks_records = []

        for row in rows:

            internal_marks = float(row["internal_marks"] or 0)
            external_marks = float(row["external_marks"] or 0)
            total_marks = float(row["total_marks"] or 0)
            grade_point = float(row["grade_point"] or 0)
            credits = int(row["credits"] or 0)

            grade = str(row["grade"] or "N/A")
            subject = str(row["subject"] or "").strip()
            exam_type = str(row.get("exam_type") or "Internal").strip()

            semester = row.get("semester")

            # Include only semester-end results in CGPA/SGPA.
            # Internal assessments should not be counted as separate subjects.
            # Include ONLY End Semester Examination records
            if (
                semester is not None
                and exam_type.lower() == "end semester examination"
            ):
                sem = int(semester)

                semester_credit_points[sem] = (
                    semester_credit_points.get(sem, 0)
                    + grade_point * credits
                )

                semester_credits[sem] = (
                    semester_credits.get(sem, 0) + credits
                )

                if grade.upper() not in ("F", "RA", "U", "AB", "ABSENT"):
                    credits_earned += credits
                else:
                    backlogs += 1
            marks_records.append({
                "id": row["id"],
                "semester": int(semester) if semester is not None else None,
                "subject": subject,
                "course_code": course_codes.get(subject, "-"),
                "exam_type": exam_type,
                "internal_marks": internal_marks,
                "external_marks": external_marks,
                "total_marks": total_marks,
                "grade": grade,
                "grade_point": grade_point,
                "credits": credits
            })


        # Calculate current semester SGPA
        current_semester = int(student["semester"] or 1)

        current_points = semester_credit_points.get(current_semester, 0)
        current_credits = semester_credits.get(current_semester, 0)

        sgpa = (
            round(current_points / current_credits, 2)
            if current_credits > 0 else 0.0
        )


        # Calculate overall CGPA using all completed semester results
        overall_points = sum(semester_credit_points.values())
        overall_credits = sum(semester_credits.values())

        cgpa = calculate_student_cgpa(cursor, student_id)

        difference = round(sgpa - cgpa, 2)

        sgpa_change = (
            f"+{difference:.2f}"
            if difference > 0
            else f"{difference:.2f}"
        )

        credits_total = overall_credits

        credit_percentage = (
            round((credits_earned / credits_total) * 100, 1)
            if credits_total > 0 else 0.0
        )

        print("Semester-wise credits:", semester_credits)
        print("Semester-wise grade points:", semester_credit_points)
        print("Calculated SGPA:", sgpa)
        print("Calculated CGPA:", cgpa)
        print("========== CGPA DEBUG ==========")
        print("Calculated CGPA:", cgpa)
        print("Student table CGPA:", student["cgpa"])
        print("Total credits:", credits_total)
        print("================================")
        return render_template(
            "student/marks.html",
            student=student,
            marks_records=marks_records,
            sgpa=sgpa,
            cgpa=cgpa,
            difference=difference,
            sgpa_change=sgpa_change,
            credits_earned=credits_earned,
            credits_total=credits_total,
            credit_percentage=credit_percentage,
            backlogs=backlogs,
        )
    except Exception as e:
        print("Student marks error:", e)
        return f"Database error: {e}", 500

    finally:
        if cursor:
            cursor.close()

        if db:
            db.close()



# ==========================================
# STUDENT EXAMS
# ==========================================


@app.route("/student/exams")
@student_required
def student_exams():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.id, u.name, u.email,
                s.register_no, s.department,
                s.year, s.semester, s.section, s.cgpa
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if student is None:
            return "Student record not found", 404

        student_id = int(student["id"])

        # Fetch all demo exams without class filtering
        cursor.execute("""
            SELECT
                id, subject, course_code, exam_date,
                exam_time, exam_type, class_name,
                venue, status
            FROM exams
            ORDER BY exam_date ASC, exam_time ASC
        """)

        exams = cast(list[dict[str, Any]], cursor.fetchall())

        today = date.today()
        upcoming_exams = []
        completed_exams = []

        month_names = [
            "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
            "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
        ]

        full_month_names = [
            "January", "February", "March", "April",
            "May", "June", "July", "August",
            "September", "October", "November", "December"
        ]

        weekday_names = [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"
        ]

        def format_exam_time(value):
            if value is None:
                return "-"

            if isinstance(value, timedelta):
                seconds = int(value.total_seconds())
                hours = (seconds // 3600) % 24
                minutes = (seconds % 3600) // 60
            elif hasattr(value, "hour") and hasattr(value, "minute"):
                hours = int(value.hour)
                minutes = int(value.minute)
            else:
                return str(value)

            display_hour = hours % 12 or 12
            period = "AM" if hours < 12 else "PM"

            return f"{display_hour:02d}:{minutes:02d} {period}"

        for exam in exams:
            exam_date_value = exam["exam_date"]

            if exam_date_value:
                exam_date = exam_date_value

                exam["day"] = f"{exam_date.day:02d}"
                exam["month"] = month_names[exam_date.month - 1]
                exam["full_date"] = (
                    f"{exam_date.day:02d} "
                    f"{full_month_names[exam_date.month - 1]} "
                    f"{exam_date.year}"
                )
                exam["weekday"] = weekday_names[exam_date.weekday()]

                days_left = (exam_date - today).days

                if days_left == 0:
                    exam["countdown"] = "Today"
                elif days_left == 1:
                    exam["countdown"] = "Tomorrow"
                elif days_left > 1:
                    exam["countdown"] = f"{days_left} Days"
                else:
                    exam["countdown"] = "Date Passed"
            else:
                exam["day"] = "-"
                exam["month"] = "-"
                exam["full_date"] = "-"
                exam["weekday"] = "-"
                exam["countdown"] = "-"

            exam["formatted_time"] = format_exam_time(
                exam["exam_time"]
            )

            status = str(exam["status"] or "").strip().lower()

            # Past dates should not remain in the upcoming list.
            if status == "completed" or (
                exam_date_value and exam_date_value < today
            ):
                completed_exams.append(exam)
            else:
                upcoming_exams.append(exam)

        next_exam = upcoming_exams[0] if upcoming_exams else None

        venues = {
            str(exam["venue"])
            for exam in upcoming_exams
            if exam.get("venue")
        }

        cursor.execute("""
            SELECT
                SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present,
                COUNT(*) AS total
            FROM attendance
            WHERE student_id = %s
        """, (student_id,))

        attendance_data = cast(
            dict[str, Any] | None, cursor.fetchone()
        ) or {"present": 0, "total": 0}
        present = int(str(attendance_data.get("present") or 0))
        total = int(str(attendance_data.get("total") or 0))

        attendance_percentage = (
            round((present / total) * 100, 1)
            if total > 0 else 0.0
        )

        required_attendance = 75.0
        eligible = attendance_percentage >= required_attendance

        last_updated = datetime.now().strftime("%d %B %Y, %I:%M %p")

        print("Student-side exams fetched:", len(exams))

        return render_template(
            "student/exams.html",
            student=student,
            exams=exams,
            upcoming_exams=upcoming_exams,
            completed_exams=completed_exams,
            upcoming_count=len(upcoming_exams),
            completed_count=len(completed_exams),
            next_exam=next_exam,
            venue_count=len(venues),
            attendance_percentage=attendance_percentage,
            required_attendance=required_attendance,
            eligible=eligible,
            last_updated=last_updated
        )

    except Exception as e:
        app.logger.exception("Error loading student exams")
        return f"Database error: {e}", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()



# ==========================================
# HALL TICKET
# ==========================================

@app.route("/student/hall-ticket")
@student_required
def student_hall_ticket():

    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        # Return database rows as dictionaries
        cursor = connection.cursor(dictionary=True)

        # Get student details
        cursor.execute("""
            SELECT
                s.id AS student_id,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.section,
                u.name
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if student is None:
            return redirect(url_for("login"))

        # Get upcoming examinations
        cursor.execute("""
            SELECT
                id,
                subject,
                course_code,
                exam_date,
                TIME_FORMAT(
                    exam_time, '%h:%i %p'
                ) AS exam_time_display,
                exam_type,
                class_name,
                venue,
                status
            FROM exams
            WHERE exam_date >= CURDATE()
              AND status = 'Upcoming'
            ORDER BY exam_date ASC, exam_time ASC
        """)

        raw_exams = cursor.fetchall()

        # Handle tuple results if the cursor returns them
        if raw_exams and not isinstance(raw_exams[0], dict):
            column_names = [
                column[0] for column in (cursor.description or [])
            ]

            exams = [
                dict(zip(column_names, exam_row))
                for exam_row in raw_exams
            ]
        else:
            exams = raw_exams

        # Format exam dates safely
        for exam in exams:
            if not isinstance(exam, dict):
                continue

            exam_date = exam["exam_date"]

            if isinstance(exam_date, (date, datetime)):
                exam["date_display"] = exam_date.strftime("%d %B %Y")
            elif isinstance(exam_date, str):
                try:
                    parsed_exam_date = date.fromisoformat(exam_date)
                    exam["date_display"] = parsed_exam_date.strftime(
                        "%d %B %Y"
                    )
                except ValueError:
                    exam["date_display"] = "Date not available"
            else:
                exam["date_display"] = "Date not available"

        # Display hall ticket page
        return render_template(
            "student/hall_ticket.html",
            student=student,
            exams=exams
        )

    except Exception as e:
        import traceback

        print("\n--- HALL TICKET ERROR ---")
        traceback.print_exc()

        return f"Database error: {e}", 500

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()



# ==========================================
# EVENTS
# ==========================================


@app.route("/student/events")
@student_required
def student_events():

    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        # Return database rows as dictionaries
        cursor = connection.cursor(dictionary=True)

        # 1. Get student details
        cursor.execute("""
            SELECT
                s.id AS student_id,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                u.name,
                u.email
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if student is None:
            return redirect(url_for("login"))

        student_id = student["student_id"]

        # 2. Get all events
        cursor.execute("""
            SELECT
                id,
                title,
                description,
                event_date,
                TIME_FORMAT(
                    start_time, '%h:%i %p'
                ) AS time_display,
                venue,
                category,
                organizer
            FROM events
            ORDER BY event_date ASC, start_time ASC
        """)

        events = cast(
            list[dict[str, Any]], cursor.fetchall()
        )

        # 3. Get the student's event registrations
        cursor.execute("""
            SELECT
                er.id AS registration_id,
                er.event_id,
                er.registered_at,
                e.title,
                e.description,
                e.event_date,
                TIME_FORMAT(
                    e.start_time, '%h:%i %p'
                ) AS time_display,
                e.venue,
                e.category,
                e.organizer
            FROM event_registrations er
            JOIN events e ON er.event_id = e.id
            WHERE er.student_id = %s
            ORDER BY er.registered_at DESC
        """, (student_id,))

        registrations = cast(
            list[dict[str, Any]], cursor.fetchall()
        )

        # 4. Registered event IDs
        registered_ids = {
            registration["event_id"]
            for registration in registrations
        }

        today = date.today()

        # 5. Initialize statistics
        upcoming_count = 0
        today_count = 0
        workshop_count = 0
        this_month_count = 0

        # 6. Prepare event data
        for event in events:

            event_date = event.get("event_date")
            category = event.get("category") or ""

            # Upcoming events
            if event_date and event_date >= today:
                upcoming_count += 1

            # Today's events
            if event_date and event_date == today:
                today_count += 1

            # Workshop count
            if category.strip().lower() == "workshop":
                workshop_count += 1

            # Events this month
            if (
                event_date
                and event_date.year == today.year
                and event_date.month == today.month
            ):
                this_month_count += 1

            # Date display
            event["date_display"] = (
                event_date.strftime("%d %b %Y")
                if event_date
                else "Date TBA"
            )

            # Time display
            if not event.get("time_display"):
                event["time_display"] = "Time TBA"

            # Registration status
            event["is_registered"] = (
                event["id"] in registered_ids
            )

            # Category display
            event["category_display"] = (
                category if category.strip() else "General"
            )

        registered_count = len(registrations)

        # 7. Find the next featured event
        featured = None

        for event in events:
            event_date = event.get("event_date")

            if event_date and event_date >= today:
                featured = event
                break

        # 8. Render events page
        return render_template(
            "student/events.html",
            student=student,
            events=events,
            registrations=registrations,
            registered_ids=registered_ids,
            upcoming_count=upcoming_count,
            today_count=today_count,
            workshop_count=workshop_count,
            this_month_count=this_month_count,
            registered_count=registered_count,
            featured=featured,
            today=today
        )

    except Exception as e:
        import traceback

        print("\n--- EVENTS ERROR ---")
        traceback.print_exc()

        return "Database error: " + str(e), 500

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ==========================================
# PLACEMENTS
# ==========================================

# ==========================================
# STUDENT PLACEMENTS
# ==========================================

@app.route("/student/placements")
@student_required
def student_placements():

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # ==========================================
        # GET STUDENT DETAILS
        # ==========================================

        cursor.execute("""
            SELECT
                s.id AS student_id,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.cgpa,
                u.name,
                u.email
            FROM students s
            JOIN users u
                ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (
            session["user_id"],
        ))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if student is None:
            return "Student record not found", 404

        student = cast(dict[str, Any], student)
        student_id = int(student["student_id"])

        # Use the student's stored CGPA for placement eligibility checks.
        student_cgpa = float(student.get("cgpa") or 0)
        # ==========================================
        # GET ALL PLACEMENT DRIVES
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                company_name,
                job_role,
                package_lpa,
                drive_date,
                minimum_cgpa,
                job_type,
                status
            FROM placements
            ORDER BY
                CASE
                    WHEN status = 'Open' THEN 1
                    WHEN status = 'Upcoming' THEN 2
                    ELSE 3
                END,
                drive_date ASC
        """)

        placements = cast(
            list[dict[str, Any]],
            cursor.fetchall()
        )

        # ==========================================
        # GET STUDENT APPLICATIONS
        # ==========================================

        cursor.execute("""
            SELECT
                pa.id AS application_id,
                pa.placement_id,
                pa.application_status,
                pa.applied_at,
                p.company_name,
                p.job_role,
                p.package_lpa,
                p.drive_date,
                p.minimum_cgpa,
                p.job_type
            FROM placement_applications pa
            JOIN placements p
                ON pa.placement_id = p.id
            WHERE pa.student_id = %s
            ORDER BY pa.applied_at DESC
        """, (
            student_id,
        ))

        applications = cast(
            list[dict[str, Any]],
            cursor.fetchall()
        )

        # ==========================================
        # CREATE APPLICATION LOOKUP
        # ==========================================

        application_map = {}

        for application in applications:

            placement_id = int(
                application["placement_id"]
            )

            application_map[placement_id] = (
                application["application_status"]
            )

            # Format application date

            applied_at = application.get(
                "applied_at"
            )

            if applied_at:
                application["applied_at_display"] = (
                    applied_at.strftime(
                        "%d %b %Y"
                    )
                )
            else:
                application["applied_at_display"] = ""

            # Format package

            package = application.get(
                "package_lpa"
            )

            if package is not None:
                application["package_display"] = (
                    f"₹{float(package):.2f} LPA"
                )
            else:
                application["package_display"] = (
                    "Not specified"
                )

            # Format drive date

            drive_date = application.get(
                "drive_date"
            )

            if drive_date:
                application["drive_date_display"] = (
                    drive_date.strftime(
                        "%d %b %Y"
                    )
                )
            else:
                application["drive_date_display"] = ""

        # ==========================================
        # PROCESS PLACEMENTS
        # ==========================================

        open_drives = 0
        eligible_drives = 0

        for placement in placements:

            placement_id = int(
                placement["id"]
            )

            # --------------------------------------
            # PACKAGE
            # --------------------------------------

            package = placement.get(
                "package_lpa"
            )

            if package is not None:

                placement["package_display"] = (
                    f"₹{float(package):.2f} LPA"
                )

            else:

                placement["package_display"] = (
                    "Not specified"
                )

            # --------------------------------------
            # DRIVE DATE
            # --------------------------------------

            drive_date = placement.get(
                "drive_date"
            )

            if drive_date:

                placement["drive_date_display"] = (
                    drive_date.strftime(
                        "%d %b %Y"
                    )
                )

                # Deadline = 4 days before drive

                deadline = (
                    drive_date - timedelta(days=4)
                )

                placement["deadline_display"] = (
                    deadline.strftime(
                        "%d %b"
                    )
                )

            else:

                placement["drive_date_display"] = ""
                placement["deadline_display"] = ""

            # --------------------------------------
            # ELIGIBILITY
            # --------------------------------------

            minimum_cgpa = placement.get(
                "minimum_cgpa"
            )

            if minimum_cgpa is not None:

                minimum_cgpa = float(
                    minimum_cgpa
                )

                placement["eligible"] = (
                    student_cgpa >= minimum_cgpa
                )

                placement["eligibility_display"] = (
                    f"{minimum_cgpa:.1f} CGPA"
                )

                if placement["eligible"]:
                    eligible_drives += 1

            else:

                placement["eligible"] = True
                placement["eligibility_display"] = (
                    "No minimum CGPA"
                )

                eligible_drives += 1

            # --------------------------------------
            # APPLICATION STATUS
            # --------------------------------------

            placement["application_status"] = (
                application_map.get(
                    placement_id
                )
            )

            placement["is_applied"] = (
                placement["application_status"]
                is not None
            )

            # --------------------------------------
            # OPEN DRIVE COUNT
            # --------------------------------------

            if placement["status"] == "Open":
                open_drives += 1

        # ==========================================
        # APPLICATION STATISTICS
        # ==========================================

        applied_count = len(
            applications
        )

        selected_count = 0

        for application in applications:

            if (
                application["application_status"]
                == "Selected"
            ):
                selected_count += 1

        # ==========================================
        # COMPANY COUNT
        # ==========================================

        company_names = set()

        for placement in placements:

            company = str(
                placement.get(
                    "company_name"
                ) or ""
            ).strip()

            if company:
                company_names.add(
                    company.lower()
                )

        company_count = len(
            company_names
        )

        # ==========================================
        # STUDENT DATA FOR TEMPLATE
        # ==========================================

        student["cgpa"] = student_cgpa

        # ==========================================
        # RENDER
        # ==========================================

        return render_template(
            "student/placements.html",

            student=student,

            placements=placements,

            applications=applications,

            company_count=company_count,

            open_drives=open_drives,

            applied_count=applied_count,

            selected_count=selected_count,

            eligible_drives=eligible_drives
        )

    except Exception as e:

        import traceback
        traceback.print_exc()

        return (
            f"Database error: {e}",
            500
        )

    finally:

        if cursor:
            cursor.close()

        if db:
            db.close()


# ==========================================
# APPLY FOR PLACEMENT
# ==========================================

@app.route(
    "/student/placements/apply/<int:placement_id>",
    methods=["POST"]
)
@student_required
def apply_placement(placement_id):

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # ==========================================
        # GET STUDENT
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                cgpa
            FROM students
            WHERE user_id = %s
        """, (
            session["user_id"],
        ))

        student = cast(dict[str, Any], cursor.fetchone())

        if student is None:
            return "Student record not found", 404

        student_id_value = student.get("id")

        if student_id_value is None:
            return "Student record not found", 404

        student_id = int(student_id_value)

        student_cgpa = float(
            student.get("cgpa") or 0
        )

        # ==========================================
        # GET PLACEMENT
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                company_name,
                minimum_cgpa,
                status
            FROM placements
            WHERE id = %s
        """, (
            placement_id,
        ))

        placement = cast(dict[str, Any], cursor.fetchone())

        if placement is None:
            return "Placement drive not found", 404

        # ==========================================
        # CHECK OPEN STATUS
        # ==========================================

        if placement.get("status") != "Open":

            return (
                "This placement drive is not open "
                "for applications.",
                400
            )

        # ==========================================
        # CHECK ELIGIBILITY
        # ==========================================

        minimum_cgpa = placement.get(
            "minimum_cgpa"
        )

        if (
            minimum_cgpa is not None
            and student_cgpa < float(minimum_cgpa)
        ):

            return (
                "You are not eligible for this "
                "placement drive.",
                403
            )

        # ==========================================
        # CHECK DUPLICATE APPLICATION
        # ==========================================

        cursor.execute("""
            SELECT id
            FROM placement_applications
            WHERE student_id = %s
              AND placement_id = %s
        """, (
            student_id,
            placement_id
        ))

        existing_application = (
            cursor.fetchone()
        )

        if existing_application:

            return (
                "You have already applied for "
                "this placement drive.",
                400
            )

        # ==========================================
        # INSERT APPLICATION
        # ==========================================

        cursor.execute("""
            INSERT INTO placement_applications
            (
                student_id,
                placement_id,
                application_status
            )
            VALUES
            (
                %s,
                %s,
                'Applied'
            )
        """, (
            student_id,
            placement_id
        ))

        db.commit()

        return redirect(
            url_for("student_placements")
        )

    except Exception as e:

        if db:
            db.rollback()

        import traceback
        traceback.print_exc()

        return (
            f"Database error: {e}",
            500
        )

    finally:

        if cursor:
            cursor.close()

        if db:
            db.close()


# ==========================================
# HACKATHONS
# ==========================================

@app.route("/student/hackathons")
@student_required
def student_hackathons():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Get student details
        cursor.execute("""
            SELECT
                s.id AS student_id,
                s.register_no,
                s.department,
                s.year,
                u.name,
                u.email
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if not student:
            return redirect(url_for("login"))

        student_id = student["student_id"]

        # Get all hackathons
        cursor.execute("""
            SELECT
                id,
                title,
                description,
                start_date,
                end_date,
                venue,
                team_size,
                prize_pool,
                status
            FROM hackathons
            ORDER BY
                CASE
                    WHEN status = 'Ongoing' THEN 1
                    WHEN status = 'Upcoming' THEN 2
                    ELSE 3
                END,
                start_date ASC
        """)

        hackathons = cast(list[dict[str, Any]], cursor.fetchall())

        # Get student's registrations
        cursor.execute("""
            SELECT
                hr.id AS registration_id,
                hr.hackathon_id,
                hr.team_name,
                hr.registered_at,
                h.title,
                h.start_date,
                h.end_date,
                h.venue,
                h.team_size,
                h.prize_pool,
                h.status
            FROM hackathon_registrations hr
            JOIN hackathons h
                ON hr.hackathon_id = h.id
            WHERE hr.student_id = %s
            ORDER BY hr.registered_at DESC
        """, (student_id,))

        registrations = cast(list[dict[str, Any]], cursor.fetchall())

        # Create registration lookup
        registered_ids = {
            registration["hackathon_id"]
            for registration in registrations
        }

        # Statistics
        upcoming_count = sum(
            1 for h in hackathons
            if h["status"] == "Upcoming"
        )

        team_events = sum(
            1 for h in hackathons
            if h["team_size"]
        )

        registered_count = len(registrations)

        total_prize = sum(
            float(h["prize_pool"] or 0)
            for h in hackathons
            if h["status"] != "Completed"
        )

        # Format dates and values for display
        for h in hackathons:
            if h["start_date"] == h["end_date"]:
                h["date_display"] = h["start_date"].strftime("%d %b %Y")
            else:
                h["date_display"] = (
                    f'{h["start_date"].strftime("%d")} – '
                    f'{h["end_date"].strftime("%d %b %Y")}'
                )

            h["prize_display"] = (
                f'₹{float(h["prize_pool"] or 0):,.0f}'
            )

            h["is_registered"] = h["id"] in registered_ids

            # Online / Campus display
            venue = (h["venue"] or "").strip()

            if venue.lower() == "online":
                h["location_type"] = "Online"
            elif venue:
                h["location_type"] = "Campus"
            else:
                h["location_type"] = "Campus"

        # Featured hackathon
        featured = None

        for h in hackathons:
            if h["status"] in ("Ongoing", "Upcoming"):
                featured = h
                break

        return render_template(
            "student/hackathons.html",
            student=student,
            hackathons=hackathons,
            registrations=registrations,
            registered_ids=registered_ids,
            upcoming_count=upcoming_count,
            team_events=team_events,
            registered_count=registered_count,
            total_prize=total_prize,
            featured=featured
        )

    except Exception as e:
        print("Hackathons error:", e)
        return "Database error: " + str(e), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/student/hackathons/register/<int:hackathon_id>", methods=["POST"])
@student_required
def register_hackathon(hackathon_id):

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Get student ID
        cursor.execute("""
            SELECT id
            FROM students
            WHERE user_id = %s
        """, (session["user_id"],))

        student = cast(dict[str, Any] | None, cursor.fetchone())

        if not student:
            return redirect(url_for("login"))

        student_id = cast(int, student["id"])

        # Check hackathon
        cursor.execute("""
            SELECT id, title, status
            FROM hackathons
            WHERE id = %s
        """, (hackathon_id,))

        hackathon = cast(dict[str, Any] | None, cursor.fetchone())

        if not hackathon:
            return "Hackathon not found", 404

        # Completed hackathons cannot be registered
        if hackathon["status"] == "Completed":
            return redirect(url_for("student_hackathons"))

        # Prevent duplicate registration
        cursor.execute("""
            SELECT id
            FROM hackathon_registrations
            WHERE student_id = %s
              AND hackathon_id = %s
        """, (student_id, hackathon_id))

        existing = cursor.fetchone()

        if existing:
            return redirect(url_for("student_hackathons"))

        team_name = request.form.get("team_name", "").strip()

        if not team_name:
            team_name = "Individual"

        # Register
        cursor.execute("""
            INSERT INTO hackathon_registrations
            (student_id, hackathon_id, team_name)
            VALUES (%s, %s, %s)
        """, (
            student_id,
            hackathon_id,
            team_name
        ))

        connection.commit()

        return redirect(url_for("student_hackathons"))

    except Exception as e:
        if connection:
            connection.rollback()

        print("Hackathon registration error:", e)
        return "Database error: " + str(e), 500

    finally:
        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ==========================================
# STUDENT VENUES
# ==========================================

@app.route("/student/venues")
@app.route("/student/venue-updates")
@student_required
def student_venues():

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # ==========================================
        # STUDENT DETAILS
        # ==========================================

        cursor.execute("""
            SELECT
                u.name,
                u.email,
                s.id AS student_id,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.section
            FROM users u
            JOIN students s
                ON u.id = s.user_id
            WHERE u.id = %s
        """, (session["user_id"],))

        student_data = cast(dict[str, Any] | None, cursor.fetchone())

        if student_data is None:
            return "Student record not found", 404

        student = {
            "student_id": int(
                student_data["student_id"] or 0
            ),
            "name": str(
                student_data["name"] or ""
            ),
            "email": str(
                student_data["email"] or ""
            ),
            "register_no": str(
                student_data["register_no"] or ""
            ),
            "department": str(
                student_data["department"] or ""
            ),
            "year": int(
                student_data["year"] or 0
            ),
            "semester": int(
                student_data["semester"] or 0
            ),
            "section": str(
                student_data["section"] or ""
            )
        }

        department = student["department"]
        year = student["year"]
        semester = student["semester"]
        section = student["section"]

        # ==========================================
        # TODAY
        # ==========================================

        today = date.today()

        month_names = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December"
        ]

        day_names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday"
        ]

        day_name = day_names[today.weekday()]

        today_display = (
            f"{today.day:02d} "
            f"{month_names[today.month - 1]} "
            f"{today.year}"
        )

        today_string = today.isoformat()

        # ==========================================
        # TODAY'S TIMETABLE
        # TIME IS FORMATTED DIRECTLY BY MYSQL
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                day,
                TIME_FORMAT(start_time, '%h:%i %p') AS start_time_display,
                TIME_FORMAT(end_time, '%h:%i %p') AS end_time_display,
                subject,
                course_code,
                faculty,
                venue
            FROM timetable
            WHERE department = %s
              AND year = %s
              AND semester = %s
              AND (
                    section = %s
                    OR section IS NULL
                    OR section = ''
              )
              AND day = %s
            ORDER BY start_time
        """, (
            department,
            year,
            semester,
            section,
            day_name
        ))

        today_schedule = [
            cast(dict[str, Any], dict(cast(Any, schedule)))
            for schedule in cursor.fetchall()
        ]

        # ==========================================
        # PREPARE TIMETABLE DATA
        # ==========================================

        for schedule in today_schedule:

            schedule["start_time_display"] = str(
                schedule.get(
                    "start_time_display"
                ) or ""
            )

            schedule["end_time_display"] = str(
                schedule.get(
                    "end_time_display"
                ) or ""
            )

            schedule["display_venue"] = str(
                schedule.get("venue")
                or "Not Assigned"
            )

            schedule["venue_changed"] = False

        # ==========================================
        # TODAY'S VENUE CHANGES
        # TIME ALSO FORMATTED BY MYSQL
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                subject_event,
                update_type,
                DATE_FORMAT(
                    update_date,
                    '%d %b %Y'
                ) AS update_date_display,
                TIME_FORMAT(
                    update_time,
                    '%h:%i %p'
                ) AS time_display,
                old_venue,
                new_venue,
                target_audience,
                created_at
            FROM venue_updates
            WHERE update_date = %s
              AND (
                    target_audience = 'student'
                    OR target_audience = 'all'
                    OR target_audience = %s
                    OR target_audience = %s
              )
            ORDER BY update_time
        """, (
            today_string,
            department,
            section
        ))

        today_changes = [
            cast(dict[str, Any], dict(cast(Any, change)))
            for change in cursor.fetchall()
        ]

        for change in today_changes:

            change["time_display"] = str(
                change.get(
                    "time_display"
                ) or ""
            )

        # ==========================================
        # APPLY VENUE CHANGES
        # ==========================================

        for schedule in today_schedule:

            subject = str(
                schedule.get("subject") or ""
            ).strip().lower()

            for change in today_changes:

                changed_subject = str(
                    change.get(
                        "subject_event"
                    ) or ""
                ).strip().lower()

                if (
                    subject
                    and changed_subject
                    and (
                        subject in changed_subject
                        or changed_subject in subject
                    )
                ):

                    schedule["display_venue"] = str(
                        change.get("new_venue")
                        or schedule.get(
                            "display_venue"
                        )
                        or "Not Assigned"
                    )

                    schedule["venue_changed"] = True

                    break

        # ==========================================
        # RECENT VENUE UPDATES
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                subject_event,
                update_type,
                DATE_FORMAT(
                    update_date,
                    '%d %b %Y'
                ) AS date_display,
                TIME_FORMAT(
                    update_time,
                    '%h:%i %p'
                ) AS time_display,
                old_venue,
                new_venue,
                target_audience,
                created_at
            FROM venue_updates
            WHERE
                target_audience = 'student'
                OR target_audience = 'all'
                OR target_audience = %s
                OR target_audience = %s
            ORDER BY update_date DESC, id DESC
            LIMIT 10
        """, (
            department,
            section
        ))

        venue_updates = [
            cast(dict[str, Any], dict(cast(Any, update)))
            for update in cursor.fetchall()
        ]

        # ==========================================
        # FORMAT SIMPLE VALUES
        # ==========================================

        for update in venue_updates:

            update["date_display"] = str(
                update.get(
                    "date_display"
                ) or ""
            )

            update["time_display"] = str(
                update.get(
                    "time_display"
                ) or ""
            )

            update_type = str(
                update.get(
                    "update_type"
                ) or "Class"
            ).lower()

            if update_type == "exam":

                update["icon"] = (
                    "bi-journal-text"
                )

                update["icon_class"] = "purple"

            elif update_type == "event":

                update["icon"] = (
                    "bi-calendar-event"
                )

                update["icon_class"] = "blue"

            elif update_type == "lab":

                update["icon"] = "bi-laptop"

                update["icon_class"] = "green"

            else:

                update["icon"] = (
                    "bi-arrow-left-right"
                )

                update["icon_class"] = "orange"

        # ==========================================
        # IMPORTANT CHANGE
        # ==========================================

        important_change = None

        if today_changes:

            important_change = dict(
                today_changes[0]
            )

            important_change["time_display"] = str(
                important_change.get(
                    "time_display"
                ) or ""
            )

        # ==========================================
        # TODAY'S VENUES
        # ==========================================

        venue_set = set()

        for schedule in today_schedule:

            venue = str(
                schedule.get(
                    "display_venue"
                ) or ""
            )

            if (
                venue
                and venue != "Not Assigned"
            ):

                venue_set.add(venue)

        todays_venues = len(
            venue_set
        )

        # ==========================================
        # CHANGES TODAY
        # ==========================================

        changes_today = len(
            today_changes
        )

        # ==========================================
        # BUILDINGS
        # ==========================================

        building_set = set()

        for venue in venue_set:

            venue_upper = venue.upper()

            if "-" in venue_upper:

                building = venue_upper.split(
                    "-"
                )[0].strip()

                if building:
                    building_set.add(
                        building
                    )

            elif "AUDITORIUM" in venue_upper:

                building_set.add(
                    "AUDITORIUM"
                )

            elif "HALL" in venue_upper:

                building_set.add(
                    "HALL"
                )

            elif "LAB" in venue_upper:

                building_set.add(
                    "LAB"
                )

            else:

                building_set.add(
                    venue_upper
                )

        building_count = len(
            building_set
        )

        # ==========================================
        # CONFIRMED
        # ==========================================

        confirmed_count = 0

        for schedule in today_schedule:

            venue = str(
                schedule.get(
                    "display_venue"
                ) or ""
            )

            if (
                venue
                and venue != "Not Assigned"
                and not schedule.get(
                    "venue_changed"
                )
            ):

                confirmed_count += 1

        # ==========================================
        # RENDER
        # ==========================================

        return render_template(
            "student/venues.html",
            student=student,
            today_display=today_display,
            today_schedule=today_schedule,
            today_changes=today_changes,
            venue_updates=venue_updates,
            important_change=important_change,
            todays_venues=todays_venues,
            changes_today=changes_today,
            building_count=building_count,
            confirmed_count=confirmed_count
        )

    except Exception as e:

        import traceback
        traceback.print_exc()

        return (
            f"Database error: {e}",
            500
        )

    finally:

        if cursor:
            cursor.close()

        if db:
            db.close()


# ==========================================
# STUDENT NOTIFICATIONS
# ==========================================

@app.route("/student/notifications")
@student_required
def student_notifications():

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.id,
                u.name,
                u.email,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.section
            FROM students s
            JOIN users u
                ON s.user_id = u.id
            WHERE s.user_id = %s
        """, (session["user_id"],))

        student: Any = cursor.fetchone()

        if student is None:
            return "Student record not found", 404

        audience_values = _student_notification_audiences(student)
        placeholders = ", ".join(
            ["%s"] * len(audience_values)
        )
        cursor.execute(f"""
            SELECT
                n.id,
                n.title,
                n.message,
                n.category,
                n.target_audience,
                n.status,
                n.created_at
            FROM notifications n
            WHERE n.status = 'Published'
              AND LOWER(TRIM(n.target_audience)) IN (
                    {placeholders}
              )
            ORDER BY n.created_at DESC
        """, audience_values)

        notifications: Any = cursor.fetchall()

        total_notifications = len(
            notifications
        )

        unread_notifications = 0
        read_notifications = 0
        important_notifications = 0

        for notification in notifications:

            notification["is_read"] = False

            category = str(
                notification.get(
                    "category"
                ) or "General"
            )

            if category in (
                "Exam",
                "Placement"
            ):

                important_notifications += 1

            notification["filter_category"] = (
                "exam"
                if category == "Exam"
                else
                "academic"
                if category == "Academic"
                else
                "placement"
                if category == "Placement"
                else
                "event"
                if category == "Event"
                else
                "venue"
                if "venue" in str(
                    notification.get(
                        "title"
                    ) or ""
                ).lower()
                or "venue" in str(
                    notification.get(
                        "message"
                    ) or ""
                ).lower()
                else
                "attendance"
                if "attendance" in str(
                    notification.get(
                        "title"
                    ) or ""
                ).lower()
                or "attendance" in str(
                    notification.get(
                        "message"
                    ) or ""
                ).lower()
                else
                "system"
            )

            notification["category_class"] = (
                notification["filter_category"]
            )

            created_at = notification.get(
                "created_at"
            )

            notification["time_display"] = (
                str(created_at)
                if created_at is not None
                else ""
            )

            unread_notifications += 1

        return render_template(
            "student/notifications.html",
            student=student,
            notifications=notifications,
            total_notifications=total_notifications,
            unread_notifications=unread_notifications,
            read_notifications=read_notifications,
            important_notifications=important_notifications
        )

    except Exception as e:

        return (
            f"Database error: {e}",
            500
        )

    finally:

        if cursor:
            cursor.close()

        if db:
            db.close()


# ==========================================
# STAFF DASHBOARD
# ==========================================

@app.route("/staff/dashboard")
@staff_required
def staff_dashboard():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("SELECT COUNT(*) AS total FROM notifications")
        row = cast(dict[str, Any] | None, cursor.fetchone())

        staff_name = session.get("name", "Faculty")

        return render_template(
            "staff/dashboard.html",
            notification_count=row["total"] if row else 0,
            staff_name=staff_name
        )

    except Exception as e:
        app.logger.exception("Staff dashboard error")
        return f"Unable to load dashboard: {e}", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

# ==========================================
# STAFF MODULES
# ==========================================

@app.route("/staff/students")
@staff_required
def staff_students():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.id,
                u.name,
                u.email,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                s.section,
                s.cgpa,
                COALESCE(a.total_classes, 0) AS total_classes,
                COALESCE(a.present_classes, 0) AS present_classes
            FROM students s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN (
                SELECT
                    student_id,
                    COUNT(*) AS total_classes,
                    SUM(
                        CASE WHEN status = 'Present'
                        THEN 1 ELSE 0 END
                    ) AS present_classes
                FROM attendance
                GROUP BY student_id
            ) a ON a.student_id = s.id
            ORDER BY s.register_no
        """)

        students = cast(list[dict[str, Any]], cursor.fetchall())

        for student in students:
            total = int(student["total_classes"] or 0)
            present = int(student["present_classes"] or 0)

            student["attendance_percentage"] = (
                round(present * 100 / total, 1)
                if total > 0 else None
            )

            student["cgpa"] = float(student["cgpa"] or 0)

            # Department filter values
            department = str(student["department"] or "").strip().upper()

            if "COMPUTER SCIENCE" in department or department == "CSE":
                student["department_short"] = "CSE"
            elif "ELECTRONICS" in department or department == "ECE":
                student["department_short"] = "ECE"
            elif department == "IT" or "INFORMATION TECHNOLOGY" in department:
                student["department_short"] = "IT"
            else:
                student["department_short"] = department

            # Convert year number to Roman numeral
            year_number = int(student["year"] or 0)
            student["year_label"] = {
                1: "I",
                2: "II",
                3: "III",
                4: "IV"
            }.get(year_number, str(year_number))

            student["status_label"] = (
                "Attention"
                if student["attendance_percentage"] is not None
                and student["attendance_percentage"] < 75
                else "Active"
            )

            student["initials"] = "".join(
                part[0] for part in str(student["name"]).split()
                if part
            )[:2].upper()

        total_students = len(students)
        active_students = total_students
        above_8_cgpa = sum(
            1 for s in students if s["cgpa"] > 8
        )
        need_attention = sum(
            1 for s in students if s["status_label"] == "Attention"
        )

        return render_template(
            "staff/students.html",
            students=students,
            total_students=total_students,
            active_students=active_students,
            above_8_cgpa=above_8_cgpa,
            need_attention=need_attention
        )

    except Exception as e:
        print("Staff students error:", e)
        return f"Database error: {e}", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/students/add", methods=["GET", "POST"])
@staff_required
def add_student():
    db = None
    cursor = None

    try:
        if request.method == "GET":
            return render_template("staff/add_student.html")

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        register_no = request.form.get("register_no", "").strip()
        department = request.form.get("department", "").strip()
        year = request.form.get("year", "").strip()
        semester = request.form.get("semester", "").strip()
        section = request.form.get("section", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()

        if not all([
            name, email, password, register_no,
            department, year, semester, section
        ]):
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("add_student"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return redirect(url_for("add_student"))

        try:
            year = int(year)
            semester = int(semester)
        except ValueError:
            flash("Year and semester must be valid numbers.", "error")
            return redirect(url_for("add_student"))

        if year not in (1, 2, 3, 4) or semester < 1:
            flash("Enter a valid year and semester.", "error")
            return redirect(url_for("add_student"))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT id FROM users WHERE email = %s
        """, (email,))

        if cursor.fetchone():
            flash("This email is already registered.", "error")
            return redirect(url_for("add_student"))

        cursor.execute("""
            SELECT id FROM students WHERE register_no = %s
        """, (register_no,))

        if cursor.fetchone():
            flash("This register number already exists.", "error")
            return redirect(url_for("add_student"))

        cursor.execute("""
            INSERT INTO users (name, email, password, role)
            VALUES (%s, %s, %s, 'student')
        """, (
            name,
            email,
            generate_password_hash(password)
        ))

        user_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO students (
                user_id, register_no, department,
                year, semester, section, cgpa, phone, address
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            register_no,
            department,
            year,
            semester,
            section,
            0.0,
            phone or None,
            address or None
        ))

        db.commit()
        flash("Student account created successfully.", "success")
        return redirect(url_for("staff_students"))

    except Exception:
        if db:
            db.rollback()
        app.logger.exception("Add student error")
        flash("Unable to create student. Check the Flask terminal.", "error")
        return redirect(url_for("add_student"))

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

@app.route("/staff/attendance")
@staff_required
def staff_attendance():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.id AS student_id,
                u.name,
                u.email,
                s.register_no,
                s.department,
                s.year,
                s.section,
                COUNT(a.id) AS total_classes,
                COALESCE(
                    SUM(CASE WHEN a.status = 'Present'
                    THEN 1 ELSE 0 END), 0
                ) AS present_classes
            FROM students s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN attendance a ON a.student_id = s.id
            WHERE u.role = 'student'
            GROUP BY
                s.id, u.name, u.email, s.register_no,
                s.department, s.year, s.section
            ORDER BY s.register_no
        """)

        students = cast(list[dict[str, Any]], cursor.fetchall())

        total_students = len(students)

        for student in students:
            total = int(student["total_classes"] or 0)
            present = int(student["present_classes"] or 0)

            student["attendance_percentage"] = (
                round(present * 100 / total, 1)
                if total > 0 else None
            )

            student["initials"] = "".join(
                part[0]
                for part in student["name"].split()
                if part
            )[:2].upper()

        today = date.today().isoformat()

        cursor.execute("""
            SELECT status, COUNT(*) AS count
            FROM attendance
            WHERE attendance_date = %s
            GROUP BY status
        """, (today,))

        today_counts = {
            row["status"]: row["count"]
            for row in cast(list[dict[str, Any]], cursor.fetchall())
        }

        present_count = int(today_counts.get("Present", 0))
        absent_count = int(today_counts.get("Absent", 0))
        marked_count = present_count + absent_count

        attendance_rate = (
            round(present_count * 100 / marked_count, 1)
            if marked_count else 0
        )

        return render_template(
            "staff/attendance.html",
            students=students,
            total_students=total_students,
            present_count=present_count,
            absent_count=absent_count,
            attendance_rate=attendance_rate,
            today=today
        )

    except Exception as e:
        print("Staff attendance error:", e)
        return f"Database error: {e}", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()
@app.route("/staff/attendance/save", methods=["POST"])
@staff_required
def save_attendance():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or {}

        subject = str(data.get("subject", "")).strip()
        attendance_date = str(data.get("attendance_date", "")).strip()
        records = data.get("records", [])

        if not subject:
            return jsonify({
                "message": "Please select a subject."
            }), 400

        try:
            datetime.strptime(attendance_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({
                "message": "Invalid attendance date."
            }), 400

        if not isinstance(records, list) or not records:
            return jsonify({
                "message": "No attendance records provided."
            }), 400

        # Validate every record before changing the database
        validated_records = []
        seen_students = set()

        for record in records:
            if not isinstance(record, dict):
                return jsonify({
                    "message": "Invalid attendance record."
                }), 400

            try:
                student_id = int(str(record.get("student_id")))
            except (TypeError, ValueError):
                return jsonify({
                    "message": "Invalid student ID."
                }), 400

            status = record.get("status")

            if student_id <= 0 or status not in ("Present", "Absent"):
                return jsonify({
                    "message": "Invalid student ID or attendance status."
                }), 400

            if student_id in seen_students:
                return jsonify({
                    "message": "Duplicate student in the submitted records."
                }), 400

            seen_students.add(student_id)
            validated_records.append((student_id, status))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Confirm that all submitted IDs belong to registered students
        student_ids = [item[0] for item in validated_records]
        placeholders = ", ".join(["%s"] * len(student_ids))

        cursor.execute(
            f"""
            SELECT s.id
            FROM students s
            JOIN users u ON u.id = s.user_id
            WHERE u.role = 'student'
              AND s.id IN ({placeholders})
            """,
            tuple(student_ids)
        )

        rows = cast(list[dict[str, Any]], cursor.fetchall())
        valid_ids = {row["id"] for row in rows}

        if valid_ids != set(student_ids):
            return jsonify({
                "message": "One or more students were not found."
            }), 400

        # Insert new records or update existing ones
        for student_id, status in validated_records:
            cursor.execute(
                """
                SELECT id
                FROM attendance
                WHERE student_id = %s
                  AND subject = %s
                  AND attendance_date = %s
                LIMIT 1
                """,
                (student_id, subject, attendance_date)
            )

            existing = cast(dict[str, Any] | None, cursor.fetchone())

            if existing:
                cursor.execute(
                    """
                    UPDATE attendance
                    SET status = %s
                    WHERE student_id = %s
                      AND subject = %s
                      AND attendance_date = %s
                    """,
                    (status, student_id, subject, attendance_date)
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO attendance
                        (student_id, subject, attendance_date, status)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (student_id, subject, attendance_date, status)
                )

        db.commit()

        return jsonify({
            "message": "Attendance saved successfully!",
            "saved_count": len(validated_records)
        }), 200

    except Exception as e:
        if db:
            db.rollback()

        print("Save attendance error:", e)

        return jsonify({
            "message": "Unable to save attendance. Check the Flask terminal."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()




@app.route("/staff/marks")
@staff_required
def staff_marks():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # 1. Get all students
        cursor.execute("""
            SELECT
                s.id,
                s.register_no,
                s.department,
                s.year,
                s.semester,
                u.name
            FROM students s
            JOIN users u ON s.user_id = u.id
            WHERE u.role = 'student'
            ORDER BY s.register_no
        """)
        students = cast(list[dict[str, Any]], cursor.fetchall())

        # 2. Get subjects for each semester from MARKS table
        # 2. Get Semester-wise subjects from marks table
        cursor.execute("""
            SELECT DISTINCT
                m.semester,
                TRIM(m.subject) AS subject
            FROM marks m
            JOIN students s ON s.id = m.student_id
            WHERE m.semester IS NOT NULL
              AND m.subject IS NOT NULL
              AND TRIM(m.subject) <> ''
              AND TRIM(s.department) = 'Computer Science and Engineering'
            ORDER BY m.semester, subject
        """)

        subject_rows = cast(list[dict[str, Any]], cursor.fetchall())

        subjects_by_semester = [
            {
                "semester": str(row["semester"]),
                "subject": row["subject"]
            }
            for row in subject_rows
        ]

        print("SUBJECTS SENT TO STAFF PAGE:")
        print(subjects_by_semester)
        # 3. Load saved marks, including semester and exam category
        cursor.execute("""
            SELECT
                student_id,
                semester,
                subject,
                exam_type,
                internal_marks,
                external_marks,
                total_marks,
                grade,
                grade_point,
                credits
            FROM marks
            ORDER BY semester, subject, exam_type
        """)
        mark_records = cast(list[dict[str, Any]], cursor.fetchall())

        # 4. Group marks by student
        marks_by_student = {}

        for mark in mark_records:
            student_id = mark["student_id"]

            marks_by_student.setdefault(student_id, []).append({
                "semester": mark["semester"],
                "subject": mark["subject"],
                "exam_type": mark["exam_type"],
                "internal_marks": mark["internal_marks"],
                "external_marks": mark["external_marks"],
                "total_marks": mark["total_marks"],
                "grade": mark["grade"],
                "grade_point": mark["grade_point"],
                "credits": mark["credits"]
            })

        # 5. Attach marks to each student
        for student in students:
            student["marks"] = marks_by_student.get(
                student["id"], []
            )

        # 6. Calculate class statistics
        student_count = len(students)

        all_totals = [
            float(mark["total_marks"])
            for mark in mark_records
            if mark["total_marks"] is not None
        ]

        class_average = (
            round(sum(all_totals) / len(all_totals), 1)
            if all_totals else 0
        )

        highest_mark = round(max(all_totals), 1) if all_totals else 0
        below_pass = sum(1 for total in all_totals if total < 50)

        return render_template(
            "staff/marks.html",
            students=students,
            subjects_by_semester=subjects_by_semester,
            student_count=student_count,
            class_average=class_average,
            highest_mark=highest_mark,
            below_pass=below_pass
        )

    finally:
        cursor.close()
        connection.close()




@app.route("/staff/marks/save", methods=["POST"])
@staff_required
def save_marks():
    connection = None
    cursor = None

    try:
        data = request.get_json(silent=True)

        if not data:
            return jsonify({
                "success": False,
                "message": "No valid JSON data received."
            }), 400

        subject = str(data.get("subject", "")).strip()
        exam_type = str(data.get("exam_type", "Internal")).strip()
        records = data.get("records", [])

        try:
            semester = int(data.get("semester"))
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "message": "Please select a semester."
            }), 400

        if semester < 1 or semester > 8:
            return jsonify({
                "success": False,
                "message": "Invalid semester."
            }), 400

        if not subject:
            return jsonify({
                "success": False,
                "message": "Please select a subject."
            }), 400

        if not isinstance(records, list) or not records:
            return jsonify({
                "success": False,
                "message": "No student marks were submitted."
            }), 400

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        for record in records:
            try:
                student_id = int(record["student_id"])
                internal = float(record["internal_marks"])
                external = float(record["external_marks"])
                credits = int(record.get("credits", 3))
            except (KeyError, TypeError, ValueError):
                connection.rollback()
                return jsonify({
                    "success": False,
                    "message": "Invalid student marks data."
                }), 400

            if not (0 <= internal <= 30 and 0 <= external <= 70):
                connection.rollback()
                return jsonify({
                    "success": False,
                    "message": "Internal marks must be 0–30 and external marks 0–70."
                }), 400

            if not (0 <= credits <= 30):
                connection.rollback()
                return jsonify({
                    "success": False,
                    "message": "Credits must be between 0 and 30."
                }), 400

            cursor.execute(
                "SELECT id FROM students WHERE id = %s",
                (student_id,)
            )

            if not cursor.fetchone():
                connection.rollback()
                return jsonify({
                    "success": False,
                    "message": f"Student ID {student_id} was not found."
                }), 400

            total = internal + external

            if total >= 90:
                grade, grade_point = "O", 10.0
            elif total >= 80:
                grade, grade_point = "A+", 9.0
            elif total >= 70:
                grade, grade_point = "A", 8.0
            elif total >= 60:
                grade, grade_point = "B+", 7.0
            elif total >= 50:
                grade, grade_point = "B", 6.0
            elif total >= 40:
                grade, grade_point = "C", 5.0
            else:
                grade, grade_point = "F", 0.0

            cursor.execute("""
    SELECT id
    FROM marks
    WHERE student_id = %s
      AND semester = %s
      AND subject = %s
      AND exam_type = %s
    LIMIT 1
""", (student_id, semester, subject, exam_type))

            existing = cast(dict[str, Any] | None, cursor.fetchone())

            if existing:
                cursor.execute("""
                    UPDATE marks
                    SET internal_marks = %s,
                        external_marks = %s,
                        total_marks = %s,
                        grade = %s,
                        grade_point = %s,
                        credits = %s
                    WHERE id = %s
                """, (
                    internal, external, total,
                    grade, grade_point, credits,
                    existing["id"]
                ))
            else:
               cursor.execute("""
    INSERT INTO marks (
        student_id,
        semester,
        subject,
        internal_marks,
        external_marks,
        total_marks,
        grade,
        grade_point,
        exam_type,
        credits
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
""", (
    student_id,
    semester,
    subject,
    internal,
    external,
    total,
    grade,
    grade_point,
    exam_type,
    credits
))

        connection.commit()

        return jsonify({
            "success": True,
            "message": "Marks saved successfully."
        }), 200

    except Exception as e:
        if connection:
            connection.rollback()

        app.logger.exception("Failed to save student marks")

        return jsonify({
            "success": False,
            "message": "Unable to save marks. Check the Flask terminal."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/staff/exams", methods=["GET"])
@staff_required
def staff_exams():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                subject,
                course_code,
                exam_date,
                TIME_FORMAT(
                    exam_time, '%h:%i %p'
                ) AS exam_time_display,
                exam_type,
                class_name,
                venue,
                status
            FROM exams
            ORDER BY exam_date ASC, exam_time ASC
        """)

        exams = cast(list[dict[str, Any]], cursor.fetchall())

        today = date.today()

        for exam in exams:
            exam_data = cast(dict[str, Any], exam)
            exam_date = exam_data.get("exam_date")

            exam_data["date_display"] = (
                exam_date.strftime("%d %B %Y")
                if exam_date else "Date TBA"
            )

            exam_data["time_display"] = (
                exam_data.get("exam_time_display") or "Time TBA"
            )

            if exam_date is None:
                exam_data["countdown"] = "-"
            elif exam_date == today:
                exam_data["countdown"] = "Today"
            elif exam_date > today:
                exam_data["countdown"] = (
                    f"{(exam_date - today).days} days"
                )
            else:
                exam_data["countdown"] = "Completed"

        return render_template(
            "staff/exams.html",
            exams=exams
        )

    except Exception as e:
        import traceback
        print("Staff exams page error:")
        traceback.print_exc()
        return f"Database error: {e}", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

@app.route("/staff/exams/add", methods=["POST"])
@staff_required
def add_exam():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        subject = data.get("subject", "").strip()
        course_code = data.get("course_code", "").strip() or None
        exam_date = data.get("exam_date", "").strip()
        exam_time = data.get("exam_time", "").strip()
        exam_type = data.get("exam_type", "Theory").strip()
        class_name = data.get("class_name", "").strip() or None
        venue = data.get("venue", "").strip() or None

        if not subject or not exam_date or not exam_time:
            return jsonify({
                "success": False,
                "message": "Subject, exam date and exam time are required."
            }), 400

        if exam_type not in ("Theory", "Practical"):
            return jsonify({
                "success": False,
                "message": "Invalid examination type."
            }), 400

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO exams
                (subject, course_code, exam_date, exam_time,
                 exam_type, class_name, venue, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Upcoming')
        """, (
            subject, course_code, exam_date, exam_time,
            exam_type, class_name, venue
        ))

        db.commit()

        return jsonify({
            "success": True,
            "message": "Exam scheduled successfully.",
            "exam_id": cursor.lastrowid
        })

    except Exception as e:
        if db:
            db.rollback()

        print("Add exam error:", e)
        return jsonify({
            "success": False,
            "message": "Unable to schedule exam. Check the server logs."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/exams/delete/<int:exam_id>", methods=["POST"])
@staff_required
def delete_exam(exam_id):
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            "DELETE FROM exams WHERE id = %s",
            (exam_id,)
        )

        if cursor.rowcount == 0:
            db.rollback()
            return jsonify({
                "success": False,
                "message": "Exam not found."
            }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Exam deleted successfully."
        })

    except Exception as e:
        if db:
            db.rollback()

        print("Delete exam error:", e)
        return jsonify({
            "success": False,
            "message": "Unable to delete exam."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()
@app.route("/staff/exams/update/<int:exam_id>", methods=["POST"])
@staff_required
def update_exam(exam_id):
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True)

        if not data:
            return jsonify({
                "success": False,
                "message": "Invalid request data."
            }), 400

        subject = data.get("subject", "").strip()
        course_code = data.get("course_code", "").strip() or None
        exam_date = data.get("exam_date", "").strip()
        exam_time = data.get("exam_time", "").strip()
        exam_type = data.get("exam_type", "Theory").strip()
        class_name = data.get("class_name", "").strip() or None
        venue = data.get("venue", "").strip() or None

        if not subject or not exam_date or not exam_time:
            return jsonify({
                "success": False,
                "message": "Subject, date and time are required."
            }), 400

        if exam_type not in ("Theory", "Practical"):
            return jsonify({
                "success": False,
                "message": "Invalid exam type."
            }), 400

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            UPDATE exams
            SET subject = %s,
                course_code = %s,
                exam_date = %s,
                exam_time = %s,
                exam_type = %s,
                class_name = %s,
                venue = %s
            WHERE id = %s
        """, (
            subject,
            course_code,
            exam_date,
            exam_time,
            exam_type,
            class_name,
            venue,
            exam_id
        ))

        cursor.execute(
            "SELECT id FROM exams WHERE id = %s",
            (exam_id,)
        )

        if not cursor.fetchone():
            db.rollback()
            return jsonify({
                "success": False,
                "message": "Exam not found."
            }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Exam updated successfully."
        })

    except Exception as e:
        if db:
            db.rollback()

        print("UPDATE EXAM ERROR:", repr(e))

        return jsonify({
            "success": False,
            "message": "Database error while updating exam. Check Flask terminal."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/timetable")
@staff_required
def staff_timetable():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, department, year, semester, section, day,
                   start_time, end_time, subject, course_code,
                   faculty, venue
            FROM timetable
            ORDER BY
                FIELD(day, 'Monday', 'Tuesday', 'Wednesday',
                      'Thursday', 'Friday', 'Saturday', 'Sunday'),
                start_time
        """)

        timetable = cursor.fetchall()

        return render_template(
            "staff/timetable.html",
            timetable=timetable
        )

    except Exception as e:
        print("Timetable error:", repr(e))
        return "Unable to load timetable. Check the Flask terminal.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/timetable/add", methods=["POST"])
@staff_required
def add_timetable():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        department = data.get("department", "").strip()
        year = data.get("year", "").strip()
        semester = data.get("semester", "").strip()
        section = data.get("section", "").strip() or None
        day = data.get("day", "").strip()
        start_time = data.get("start_time", "").strip()
        end_time = data.get("end_time", "").strip()
        subject = data.get("subject", "").strip()
        course_code = data.get("course_code", "").strip() or None
        faculty = data.get("faculty", "").strip() or None
        venue = data.get("venue", "").strip() or None

        if not all([department, year, semester, day,
                    start_time, end_time, subject]):
            return jsonify({
                "success": False,
                "message": "Fill in all required fields."
            }), 400

        if day not in [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"
        ]:
            return jsonify({
                "success": False,
                "message": "Invalid day."
            }), 400

        try:
            year = int(year)
            semester = int(semester)
            if year < 1 or semester < 1:
                raise ValueError
        except ValueError:
            return jsonify({
                "success": False,
                "message": "Year and semester must be positive numbers."
            }), 400

        if start_time >= end_time:
            return jsonify({
                "success": False,
                "message": "End time must be after start time."
            }), 400

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO timetable (
                department, year, semester, section, day,
                start_time, end_time, subject, course_code,
                faculty, venue
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            department, year, semester, section, day,
            start_time, end_time, subject, course_code,
            faculty, venue
        ))

        db.commit()

        return jsonify({
            "success": True,
            "message": "Timetable entry added successfully."
        })

    except Exception as e:
        if db:
            db.rollback()
        print("Add timetable error:", repr(e))
        return jsonify({
            "success": False,
            "message": "Unable to add timetable entry."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/timetable/edit/<int:entry_id>", methods=["POST"])
@staff_required
def edit_timetable(entry_id):
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        department = data.get("department", "").strip()
        year = data.get("year", "").strip()
        semester = data.get("semester", "").strip()
        section = data.get("section", "").strip() or None
        day = data.get("day", "").strip()
        start_time = data.get("start_time", "").strip()
        end_time = data.get("end_time", "").strip()
        subject = data.get("subject", "").strip()
        course_code = data.get("course_code", "").strip() or None
        faculty = data.get("faculty", "").strip() or None
        venue = data.get("venue", "").strip() or None

        if not all([
            department, year, semester, day,
            start_time, end_time, subject
        ]):
            return jsonify({
                "success": False,
                "message": "Fill in all required fields."
            }), 400

        if day not in [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"
        ]:
            return jsonify({
                "success": False,
                "message": "Invalid day."
            }), 400

        try:
            year = int(year)
            semester = int(semester)

            if year < 1 or semester < 1:
                raise ValueError
        except ValueError:
            return jsonify({
                "success": False,
                "message": "Year and semester must be positive numbers."
            }), 400

        if start_time >= end_time:
            return jsonify({
                "success": False,
                "message": "End time must be after start time."
            }), 400

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            UPDATE timetable
            SET department = %s,
                year = %s,
                semester = %s,
                section = %s,
                day = %s,
                start_time = %s,
                end_time = %s,
                subject = %s,
                course_code = %s,
                faculty = %s,
                venue = %s
            WHERE id = %s
        """, (
            department, year, semester, section, day,
            start_time, end_time, subject, course_code,
            faculty, venue, entry_id
        ))

        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT id FROM timetable WHERE id = %s",
                (entry_id,)
            )

            if not cursor.fetchone():
                db.rollback()
                return jsonify({
                    "success": False,
                    "message": "Timetable entry not found."
                }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Timetable entry updated successfully."
        })

    except Exception as e:
        if db:
            db.rollback()

        print("Edit timetable error:", repr(e))

        return jsonify({
            "success": False,
            "message": "Unable to update timetable entry."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

@app.route("/staff/timetable/delete/<int:entry_id>", methods=["POST"])
@staff_required
def delete_timetable(entry_id):
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            "DELETE FROM timetable WHERE id = %s",
            (entry_id,)
        )

        if cursor.rowcount == 0:
            db.rollback()
            return jsonify({
                "success": False,
                "message": "Timetable entry not found."
            }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Timetable entry deleted successfully."
        })

    except Exception as e:
        if db:
            db.rollback()
        print("Delete timetable error:", repr(e))
        return jsonify({
            "success": False,
            "message": "Unable to delete timetable entry."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/notifications")
@staff_required
def staff_notifications():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, title, message, category,
                   target_audience, status, created_at
            FROM notifications
            ORDER BY id DESC
        """)

        notifications = cursor.fetchall()

        return render_template(
            "staff/notifications.html",
            notifications=notifications
        )

    except Exception as e:
        print("Staff notifications error:", repr(e))
        return "Unable to load notifications.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/notifications/add", methods=["POST"])
@staff_required
def add_notification():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "message": "Invalid notification data."
            }), 400

        title = str(data.get("title") or "").strip()
        message = str(data.get("message") or "").strip()
        category = str(data.get("category") or "").strip()
        target_audience = str(
            data.get("target_audience") or ""
        ).strip()
        status = str(data.get("status") or "").strip()

        if not title or not message:
            return jsonify({
                "success": False,
                "message": "Title and message are required."
            }), 400

        if category not in {
            "Academic",
            "Exam",
            "Event",
            "Placement",
            "General"
        }:
            return jsonify({
                "success": False,
                "message": "Select a valid notification category."
            }), 400

        if target_audience not in {
            "student",
            "III CSE-A",
            "III CSE-B",
            "III CSE-C",
            "Faculty"
        }:
            return jsonify({
                "success": False,
                "message": "Select a valid target audience."
            }), 400

        if status not in {"Published", "Draft"}:
            return jsonify({
                "success": False,
                "message": "Select a valid notification status."
            }), 400

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO notifications
                (title, message, category, target_audience, status)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            title,
            message,
            category,
            target_audience,
            status
        ))
        db.commit()

        return jsonify({
            "success": True,
            "message": "Notification saved successfully."
        }), 201

    except Exception as e:
        if db:
            db.rollback()
        print("Create notification error:", repr(e))
        return jsonify({
            "success": False,
            "message": "Unable to save notification."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route(
    "/staff/notifications/delete/<int:notification_id>",
    methods=["POST"]
)
@staff_required
def delete_notification(notification_id):
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM notifications WHERE id = %s",
            (notification_id,)
        )

        if cursor.rowcount == 0:
            return jsonify({
                "success": False,
                "message": "Notification not found."
            }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Notification deleted successfully."
        })

    except Exception as e:
        if db:
            db.rollback()
        print("Delete notification error:", repr(e))
        return jsonify({
            "success": False,
            "message": "Unable to delete notification."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()
@app.route("/test-notifications")
def test_notifications():
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM notifications ORDER BY id DESC")
        data = cursor.fetchall()
        return jsonify(data)
    finally:
        cursor.close()
        db.close()

@app.route("/staff/venue-updates/add", methods=["POST"])
@staff_required
def add_venue_update():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        subject_event = str(data.get("subject_event", "")).strip()
        update_type = str(data.get("update_type", "")).strip()
        update_date = str(data.get("update_date", "")).strip()
        update_time = str(data.get("update_time", "")).strip() or None
        old_venue = str(data.get("old_venue", "")).strip() or None
        new_venue = str(data.get("new_venue", "")).strip()
        target_audience = str(
            data.get("target_audience", "All Students")
        ).strip()

        if not subject_event or not update_date or not new_venue:
            return jsonify({
                "success": False,
                "message": "Subject, date and new venue are required."
            }), 400

        if update_type not in ("Class", "Exam", "Event", "Lab"):
            return jsonify({
                "success": False,
                "message": "Invalid update type."
            }), 400

        from datetime import date, time
        date.fromisoformat(update_date)

        if update_time:
            time.fromisoformat(update_time)

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO venue_updates (
                subject_event, update_type, update_date, update_time,
                old_venue, new_venue, target_audience
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            subject_event,
            update_type,
            update_date,
            update_time,
            old_venue,
            new_venue,
            target_audience
        ))

        db.commit()

        return jsonify({
            "success": True,
            "message": "Venue update added successfully."
        }), 201

    except ValueError:
        return jsonify({
            "success": False,
            "message": "Enter a valid date and time."
        }), 400

    except Exception as e:
        if db:
            db.rollback()

        print("ADD VENUE UPDATE ERROR:", repr(e))

        return jsonify({
            "success": False,
            "message": "Unable to add venue update."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

@app.route("/staff/venues")
@app.route("/staff/venue-updates")
@staff_required
def staff_venue_updates():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, subject_event, update_type, update_date,
                   update_time, old_venue, new_venue,
                   target_audience, created_at
            FROM venue_updates
            ORDER BY id DESC
        """)

        updates = cast(
            list[dict[str, Any]],
            cursor.fetchall()
        )

        for update in updates:
            update_date = update.get("update_date")
            if isinstance(update_date, (date, datetime)):
                update["update_date_input"] = update_date.isoformat()
                update["update_date_display"] = update_date.strftime(
                    "%d %b %Y"
                )
            else:
                update["update_date_input"] = str(
                    update_date or ""
                )[:10]
                update["update_date_display"] = str(
                    update_date or "Date not specified"
                )

            update_time = update.get("update_time")
            update["update_time_input"] = format_time_input(
                update_time
            )
            update["update_time_display"] = (
                format_time(update_time)
                if update_time is not None
                else "Time not specified"
            )

        return render_template(
            "staff/venues.html",
            updates=updates
        )

    except Exception as e:
        print("Venue updates error:", repr(e))
        return "Unable to load venue updates. Check the terminal.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

@app.route(
    "/student/events/register/<int:event_id>",
    methods=["POST"]
)
@student_required
def register_event(event_id):

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Get student ID
        cursor.execute("""
            SELECT id
            FROM students
            WHERE user_id = %s
        """, (session["user_id"],))

        student = cast(
            dict[str, Any] | None,
            cursor.fetchone()
        )

        if not student:
            return redirect(url_for("login"))

        student_id = student["id"]

        # Check event
        cursor.execute("""
            SELECT
                id,
                title,
                event_date
            FROM events
            WHERE id = %s
        """, (event_id,))

        event = cast(
            dict[str, Any] | None,
            cursor.fetchone()
        )

        if not event:
            return "Event not found", 404

        # Prevent registration for past events
        if event["event_date"] < date.today():
            return redirect(url_for("student_events"))

        # Check duplicate registration
        cursor.execute("""
            SELECT id
            FROM event_registrations
            WHERE student_id = %s
              AND event_id = %s
        """, (
            student_id,
            event_id
        ))

        existing = cursor.fetchone()

        if existing:
            return redirect(url_for("student_events"))

        # Register student
        cursor.execute("""
            INSERT INTO event_registrations
            (student_id, event_id)
            VALUES (%s, %s)
        """, (
            student_id,
            event_id
        ))

        connection.commit()

        return redirect(url_for("student_events"))

    except Exception as e:

        if connection:
            connection.rollback()

        print("Event registration error:", e)

        return "Database error: " + str(e), 500

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()

@app.route("/staff/venue-updates/edit/<int:update_id>", methods=["POST"])
@staff_required
def edit_venue_update(update_id):
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        subject_event = str(data.get("subject_event", "")).strip()
        update_type = str(data.get("update_type", "")).strip()
        update_date = str(data.get("update_date", "")).strip()
        update_time = str(data.get("update_time", "")).strip() or None
        old_venue = str(data.get("old_venue", "")).strip() or None
        new_venue = str(data.get("new_venue", "")).strip()
        target_audience = str(data.get("target_audience", "student")).strip()

        if not subject_event or not update_date or not new_venue:
            return jsonify({
                "success": False,
                "message": "Subject, date and new venue are required."
            }), 400

        if update_type not in ("Class", "Exam", "Event", "Lab"):
            return jsonify({
                "success": False,
                "message": "Invalid update type."
            }), 400

        from datetime import date, time
        date.fromisoformat(update_date)

        if update_time:
            time.fromisoformat(update_time)

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            UPDATE venue_updates
            SET subject_event = %s,
                update_type = %s,
                update_date = %s,
                update_time = %s,
                old_venue = %s,
                new_venue = %s,
                target_audience = %s
            WHERE id = %s
        """, (
            subject_event,
            update_type,
            update_date,
            update_time,
            old_venue,
            new_venue,
            target_audience,
            update_id
        ))

        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT id FROM venue_updates WHERE id = %s",
                (update_id,)
            )
            if not cursor.fetchone():
                return jsonify({
                    "success": False,
                    "message": "Venue update not found."
                }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Venue update updated successfully."
        })

    except ValueError:
        return jsonify({
            "success": False,
            "message": "Enter a valid date and time."
        }), 400

    except Exception as e:
        if db:
            db.rollback()

        print("EDIT VENUE UPDATE ERROR:", repr(e))

        return jsonify({
            "success": False,
            "message": "Unable to edit venue update."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/venue-updates/delete/<int:update_id>", methods=["POST"])
@staff_required
def delete_venue_update(update_id):
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            "DELETE FROM venue_updates WHERE id = %s",
            (update_id,)
        )

        if cursor.rowcount == 0:
            return jsonify({
                "success": False,
                "message": "Venue update not found."
            }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Venue update deleted successfully."
        })

    except Exception as e:
        if db:
            db.rollback()

        print("DELETE VENUE UPDATE ERROR:", repr(e))

        return jsonify({
            "success": False,
            "message": "Unable to delete venue update."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

# ==========================================
# STAFF EVENTS MANAGEMENT
# ==========================================

@app.route("/staff/events")
@staff_required
def staff_events():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                title,
                description,
                event_date,
                start_time,
                TIME_FORMAT(start_time, '%h:%i %p') AS time_display,
                venue,
                category,
                organizer,
                created_at
            FROM events
            ORDER BY event_date ASC, start_time ASC
        """)

        events = [event for event in cursor.fetchall() if isinstance(event, dict)]

        for event in events:
            event["start_time_input"] = format_time_input(
                event.get("start_time")
            )

        return render_template(
            "staff/events.html",
            events=events,
            current_date=date.today()
        )

    except Exception as e:
        print("LOAD EVENTS ERROR:", repr(e))
        return "Unable to load events.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/events/add", methods=["POST"])
@staff_required
def add_event():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip() or None
        event_date = str(data.get("event_date", "")).strip()
        start_time = str(data.get("start_time", "")).strip() or None
        venue = str(data.get("venue", "")).strip() or None
        category = str(data.get("category", "")).strip() or None
        organizer = str(data.get("organizer", "")).strip() or None

        if not title or not event_date:
            return jsonify({
                "success": False,
                "message": "Event title and date are required."
            }), 400

        if len(title) > 150:
            return jsonify({
                "success": False,
                "message": "Title must not exceed 150 characters."
            }), 400

        if venue and len(venue) > 100:
            return jsonify({
                "success": False,
                "message": "Venue must not exceed 100 characters."
            }), 400

        if category and len(category) > 50:
            return jsonify({
                "success": False,
                "message": "Category must not exceed 50 characters."
            }), 400

        if organizer and len(organizer) > 100:
            return jsonify({
                "success": False,
                "message": "Organizer must not exceed 100 characters."
            }), 400

        date.fromisoformat(event_date)

        if start_time:
            datetime.strptime(
                start_time,
                "%H:%M" if len(start_time) == 5 else "%H:%M:%S"
            )

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO events (
                title, description, event_date, start_time,
                venue, category, organizer
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            title, description, event_date, start_time,
            venue, category, organizer
        ))

        db.commit()

        return jsonify({
            "success": True,
            "message": "Event added successfully.",
            "event_id": cursor.lastrowid
        }), 201

    except ValueError:
        return jsonify({
            "success": False,
            "message": "Enter a valid date and time."
        }), 400

    except Exception as e:
        if db:
            db.rollback()
        print("ADD EVENT ERROR:", repr(e))
        return jsonify({
            "success": False,
            "message": "Unable to add event."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/events/edit/<int:event_id>", methods=["POST"])
@staff_required
def edit_event(event_id):
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip() or None
        event_date = str(data.get("event_date", "")).strip()
        start_time = str(data.get("start_time", "")).strip() or None
        venue = str(data.get("venue", "")).strip() or None
        category = str(data.get("category", "")).strip() or None
        organizer = str(data.get("organizer", "")).strip() or None

        if not title or not event_date:
            return jsonify({
                "success": False,
                "message": "Event title and date are required."
            }), 400

        if len(title) > 150:
            return jsonify({
                "success": False,
                "message": "Title must not exceed 150 characters."
            }), 400

        if venue and len(venue) > 100:
            return jsonify({
                "success": False,
                "message": "Venue must not exceed 100 characters."
            }), 400

        if category and len(category) > 50:
            return jsonify({
                "success": False,
                "message": "Category must not exceed 50 characters."
            }), 400

        if organizer and len(organizer) > 100:
            return jsonify({
                "success": False,
                "message": "Organizer must not exceed 100 characters."
            }), 400

        date.fromisoformat(event_date)

        if start_time:
            datetime.strptime(
                start_time,
                "%H:%M" if len(start_time) == 5 else "%H:%M:%S"
            )

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            UPDATE events
            SET title = %s,
                description = %s,
                event_date = %s,
                start_time = %s,
                venue = %s,
                category = %s,
                organizer = %s
            WHERE id = %s
        """, (
            title, description, event_date, start_time,
            venue, category, organizer, event_id
        ))

        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT id FROM events WHERE id = %s",
                (event_id,)
            )
            if not cursor.fetchone():
                return jsonify({
                    "success": False,
                    "message": "Event not found."
                }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Event updated successfully."
        })

    except ValueError:
        return jsonify({
            "success": False,
            "message": "Enter a valid date and time."
        }), 400

    except Exception as e:
        if db:
            db.rollback()
        print("EDIT EVENT ERROR:", repr(e))
        return jsonify({
            "success": False,
            "message": "Unable to update event."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/events/delete/<int:event_id>", methods=["POST"])
@staff_required
def delete_event(event_id):
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            "SELECT id FROM events WHERE id = %s",
            (event_id,)
        )

        if not cursor.fetchone():
            return jsonify({
                "success": False,
                "message": "Event not found."
            }), 404

        # Delete registrations before deleting the event.
        cursor.execute("""
            DELETE FROM event_registrations
            WHERE event_id = %s
        """, (event_id,))

        cursor.execute(
            "DELETE FROM events WHERE id = %s",
            (event_id,)
        )

        db.commit()

        return jsonify({
            "success": True,
            "message": "Event and its registrations deleted successfully."
        })

    except Exception as e:
        if db:
            db.rollback()
        print("DELETE EVENT ERROR:", repr(e))
        return jsonify({
            "success": False,
            "message": "Unable to delete event."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

@app.route("/staff/placements")
@staff_required
def staff_placements():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM placements
            ORDER BY created_at DESC
        """)
        placements = cursor.fetchall()

        return render_template(
            "staff/placements.html",
            placements=placements
        )

    except Exception as e:
        print("LOAD PLACEMENTS ERROR:", repr(e))
        return "Unable to load placements.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/placements/add", methods=["POST"])
@staff_required
def add_placement():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        company_name = str(data.get("company_name", "")).strip()
        job_role = str(data.get("job_role", "")).strip()
        package_lpa = data.get("package_lpa")
        drive_date = str(data.get("drive_date", "")).strip()
        minimum_cgpa = data.get("minimum_cgpa")
        job_type = str(data.get("job_type", "")).strip()
        status = str(data.get("status", "Upcoming")).strip()

        if package_lpa is None or minimum_cgpa is None or not all([
            company_name, job_role, package_lpa,
            drive_date, minimum_cgpa, job_type
        ]):
            return jsonify({
                "success": False,
                "message": "Please fill in all required fields."
            }), 400

        if status not in ("Upcoming", "Open", "Closed", "Completed"):
            return jsonify({
                "success": False,
                "message": "Invalid placement status."
            }), 400

        package_lpa = float(package_lpa)
        minimum_cgpa = float(minimum_cgpa)

        if package_lpa < 0 or not 0 <= minimum_cgpa <= 10:
            return jsonify({
                "success": False,
                "message": "Enter a valid package and CGPA."
            }), 400

        from datetime import date
        date.fromisoformat(drive_date)

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO placements
            (company_name, job_role, package_lpa, drive_date,
             minimum_cgpa, job_type, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            company_name, job_role, package_lpa, drive_date,
            minimum_cgpa, job_type, status
        ))

        db.commit()

        return jsonify({
            "success": True,
            "message": "Placement drive added successfully."
        }), 201

    except ValueError:
        return jsonify({
            "success": False,
            "message": "Enter valid numeric values and a valid date."
        }), 400

    except Exception as e:
        if db:
            db.rollback()

        print("ADD PLACEMENT ERROR:", repr(e))

        return jsonify({
            "success": False,
            "message": "Unable to add placement drive."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/placements/edit/<int:placement_id>", methods=["POST"])
@staff_required
def edit_placement(placement_id):
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or request.form

        company_name = str(data.get("company_name", "")).strip()
        job_role = str(data.get("job_role", "")).strip()
        package_value = data.get("package_lpa")
        minimum_cgpa_value = data.get("minimum_cgpa")
        if package_value is None or minimum_cgpa_value is None:
            raise ValueError("Package and CGPA are required")

        package_lpa = float(package_value)
        drive_date = str(data.get("drive_date", "")).strip()
        minimum_cgpa = float(minimum_cgpa_value)
        job_type = str(data.get("job_type", "")).strip()
        status = str(data.get("status", "Upcoming")).strip()

        if not company_name or not job_role or not drive_date or not job_type:
            return jsonify({
                "success": False,
                "message": "Please fill in all required fields."
            }), 400

        if package_lpa < 0 or not 0 <= minimum_cgpa <= 10:
            return jsonify({
                "success": False,
                "message": "Enter a valid package and CGPA."
            }), 400

        if status not in ("Open", "Upcoming", "Closed"):
            return jsonify({
                "success": False,
                "message": "Invalid placement status."
            }), 400

        from datetime import date
        date.fromisoformat(drive_date)

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("""
            UPDATE placements
            SET company_name = %s,
                job_role = %s,
                package_lpa = %s,
                drive_date = %s,
                minimum_cgpa = %s,
                job_type = %s,
                status = %s
            WHERE id = %s
        """, (
            company_name, job_role, package_lpa, drive_date,
            minimum_cgpa, job_type, status, placement_id
        ))

        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT id FROM placements WHERE id = %s",
                (placement_id,)
            )
            if not cursor.fetchone():
                return jsonify({
                    "success": False,
                    "message": "Placement drive not found."
                }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Placement drive updated successfully."
        })

    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Enter valid package, CGPA and date values."
        }), 400

    except Exception as e:
        if db:
            db.rollback()

        print("EDIT PLACEMENT ERROR:", repr(e))

        return jsonify({
            "success": False,
            "message": "Unable to update placement drive."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/placements/delete/<int:placement_id>", methods=["POST"])
@staff_required
def delete_placement(placement_id):
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            "DELETE FROM placements WHERE id = %s",
            (placement_id,)
        )

        if cursor.rowcount == 0:
            return jsonify({
                "success": False,
                "message": "Placement drive not found."
            }), 404

        db.commit()

        return jsonify({
            "success": True,
            "message": "Placement drive deleted successfully."
        })

    except Exception as e:
        if db:
            db.rollback()

        print("DELETE PLACEMENT ERROR:", repr(e))

        return jsonify({
            "success": False,
            "message": "Unable to delete placement drive."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/staff/profile", methods=["GET", "POST"])
@staff_required
def staff_profile():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        user_id = session["user_id"]

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()

            if not name or not email:
                flash("Name and email are required.", "error")
                return redirect(url_for("staff_profile"))

            cursor.execute(
                """
                SELECT id FROM users
                WHERE email = %s AND id != %s
                """,
                (email, user_id)
            )

            if cursor.fetchone():
                flash("This email is already registered.", "error")
                return redirect(url_for("staff_profile"))

            cursor.execute(
                """
                UPDATE users
                SET name = %s, email = %s
                WHERE id = %s AND role = 'staff'
                """,
                (name, email, user_id)
            )

            if cursor.rowcount == 0:
                db.rollback()
                flash("Staff account was not found.", "error")
                return redirect(url_for("staff_profile"))

            db.commit()

            # Keep the updated name in the current session if used.
            session["name"] = name

            flash("Profile updated successfully.", "success")
            return redirect(url_for("staff_profile"))

        cursor.execute(
            """
            SELECT id, name, email, role, created_at
            FROM users
            WHERE id = %s AND LOWER(TRIM(role)) = 'staff'
            """,
            (user_id,)
        )

        staff = cursor.fetchone()

        if not staff:
            return "Staff account not found.", 404

        return render_template(
            "staff/profile.html",
            staff=staff
        )

    except Exception as e:
        if db:
            db.rollback()
        app.logger.exception("Staff profile error")
        return "Unable to load or update staff profile. Check the Flask terminal.", 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


# ==========================================
# STAFF CHANGE PASSWORD
# ==========================================

@app.route("/staff/change-password", methods=["GET", "POST"])
@staff_required
def staff_change_password():

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            return render_template(
                "staff/change_password.html",
                error="All fields are required."
            )

        if len(new_password) < 8:
            return render_template(
                "staff/change_password.html",
                error="Password must contain at least 8 characters."
            )

        if new_password != confirm_password:
            return render_template(
                "staff/change_password.html",
                error="New passwords do not match."
            )

        db = None
        cursor = None

        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)

            cursor.execute(
                "SELECT password FROM users WHERE id = %s",
                (session["user_id"],)
            )
            user = cursor.fetchone()
            user_password = (
                user.get("password") if isinstance(user, dict) else None
            )

            if not isinstance(user_password, str) or not check_password_hash(
                user_password, current_password
            ):
                return render_template(
                    "staff/change_password.html",
                    error="Current password is incorrect."
                )

            if check_password_hash(user_password, new_password):
                return render_template(
                    "staff/change_password.html",
                    error="New password must differ from the current password."
                )

            hashed_password = generate_password_hash(new_password)

            cursor.execute(
                "UPDATE users SET password = %s WHERE id = %s",
                (hashed_password, session["user_id"])
            )
            db.commit()

            return render_template(
                "staff/change_password.html",
                success="Password changed successfully."
            )

        except Exception:
            if db:
                db.rollback()

            return render_template(
                "staff/change_password.html",
                error="Unable to change password. Please try again."
            )

        finally:
            if cursor:
                cursor.close()
            if db:
                db.close()

    return render_template("staff/change_password.html")

# ==========================================
# RUN
# ==========================================

if __name__ == "__main__":
    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )