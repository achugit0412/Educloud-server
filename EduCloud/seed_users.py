from db import get_db_connection
from werkzeug.security import generate_password_hash


def create_users():

    db = get_db_connection()
    cursor = db.cursor()

    # Student account
    student_password = generate_password_hash("Student@123")

    cursor.execute("""
        INSERT INTO users
        (name, email, password, role)
        VALUES (%s, %s, %s, %s)
    """, (
        "Joshega K",
        "joshega@educloud.com",
        student_password,
        "student"
    ))

    student_user_id = cursor.lastrowid

    # Student academic details
    cursor.execute("""
        INSERT INTO students
        (
            user_id,
            register_no,
            department,
            year,
            semester,
            section,
            cgpa,
            phone,
            address
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        student_user_id,
        "24BCS115",
        "Computer Science and Engineering",
        3,
        5,
        "CSE-A",
        8.45,
        "9876543210",
        "Tamil Nadu"
    ))


    # Staff account
    staff_password = generate_password_hash("Staff@123")

    cursor.execute("""
        INSERT INTO users
        (name, email, password, role)
        VALUES (%s, %s, %s, %s)
    """, (
        "EduCloud Staff",
        "staff@educloud.com",
        staff_password,
        "staff"
    ))


    db.commit()

    cursor.close()
    db.close()

    print("Users created successfully!")


if __name__ == "__main__":
    create_users()