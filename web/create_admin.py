#!/usr/bin/env python3
"""Create the first administrator from an interactive terminal."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash


PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


def connection() -> pymysql.Connection:
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "gatekeeper"),
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ.get("MYSQL_DATABASE", "plate_access_control"),
        charset="utf8mb4",
        autocommit=False,
    )


def main() -> None:
    database = connection()
    try:
        with database.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'administrator'")
            if int(cursor.fetchone()[0]) > 0:
                print("An administrator account already exists; it was preserved.")
                return

        while True:
            username = input("Administrator username: ").strip()
            if len(username) >= 3:
                break
            print("Use at least three characters.")

        while True:
            password = getpass.getpass("Administrator password (at least 10 characters): ")
            confirmation = getpass.getpass("Confirm administrator password: ")
            if len(password) < 10:
                print("Use at least ten characters.")
            elif password != confirmation:
                print("The passwords did not match.")
            else:
                break

        with database.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'administrator')",
                (username, generate_password_hash(password)),
            )
            administrator_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO audit_log (user_id, action, entity_type, entity_id, details)
                VALUES (%s, 'create_admin', 'user', %s, 'Initial administrator created by installer')
                """,
                (administrator_id, administrator_id),
            )
        database.commit()
        print(f"Administrator account created: {username}")
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()


if __name__ == "__main__":
    main()
