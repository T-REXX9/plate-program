from __future__ import annotations

import csv
import hashlib
import hmac
import io
import math
import os
import re
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

pymysql = None
DictCursor = None
class IntegrityError(Exception):
    pass

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from authorization import authorize_plate_and_rfid, normalize_rfid
from camera import (
    CameraConfig,
    CameraError,
    camera_endpoint_env_name,
    camera_is_configured,
    capture_frame,
    validate_camera_config,
    validate_camera_uid,
)
from tenancy import controller_key_digest, matching_credential_id, normalize_tenant_uid


PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")
DATABASE_DIR = PROJECT_DIR / "database"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
SECRET_PATH = DATABASE_DIR / "web_secret.key"
OUTPUT_DIR = PROJECT_DIR / "Output"


def load_secret_key() -> str:
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
        SECRET_PATH.chmod(0o600)
    return SECRET_PATH.read_text(encoding="utf-8").strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    # One enhanced crop plus two optional 4K JPEG frames from the controller.
    MAX_CONTENT_LENGTH=24 * 1024 * 1024,
)


@app.errorhandler(401)
@app.errorhandler(403)
def authorization_error(error):
    if request.path.startswith("/api/"):
        return {"error": getattr(error, "description", "Access denied.")}, error.code
    return error

MAX_CROP_BYTES = 4 * 1024 * 1024
MAX_FRAME_BYTES = 10 * 1024 * 1024


def mysql_options() -> dict[str, Any]:
    global pymysql, DictCursor, IntegrityError
    if pymysql is None:
        import pymysql as _pymysql
        from pymysql.cursors import DictCursor as _DictCursor
        from pymysql.err import IntegrityError as _IntegrityError
        pymysql = _pymysql
        DictCursor = _DictCursor
        IntegrityError = _IntegrityError
    time_zone = os.environ.get("MYSQL_TIME_ZONE", "+08:00")
    if not re.fullmatch(r"[+-](?:0\d|1[0-4]):[0-5]\d", time_zone):
        raise ValueError("MYSQL_TIME_ZONE must be a numeric offset such as +08:00.")
    return {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "gatekeeper"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE", "plate_access_control"),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "connect_timeout": 5,
        "read_timeout": 10,
        "write_timeout": 10,
        "init_command": f"SET time_zone = '{time_zone}'",
        "autocommit": False,
    }


class DatabaseConnection:
    def __init__(self) -> None:
        options = mysql_options()
        self.connection = pymysql.connect(**options)

    def execute(self, sql: str, parameters: Any = ()):
        cursor = self.connection.cursor()
        if parameters:
            sql = sql.replace("%", "%%").replace("?", "%s")
            cursor.execute(sql, parameters)
        else:
            cursor.execute(sql)
        return cursor

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


def initialize_database() -> None:
    connection = DatabaseConnection()
    try:
        # The schema contains a legacy forward reference from
        # user_village_roles to households. Disable checks while creating the
        # complete schema, then restore them before committing.
        connection.execute("SET FOREIGN_KEY_CHECKS = 0")
        for statement in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
            statement = statement.strip()
            if statement:
                connection.execute(statement)
        # CREATE TABLE IF NOT EXISTS does not add columns to an installation
        # upgraded from the original single-controller schema.
        for table, column, definition in (
            ("access_events", "controller_uid", "VARCHAR(64) NULL AFTER id"),
            ("access_events", "attempt_uid", "VARCHAR(80) NULL AFTER controller_uid"),
            ("reader_commands", "controller_uid", "VARCHAR(64) NULL AFTER id"),
            ("controllers", "rfid_connected", "TINYINT(1) NOT NULL DEFAULT 0 AFTER camera_connected"),
            ("controllers", "is_active", "TINYINT(1) NOT NULL DEFAULT 1 AFTER controller_type"),
            (
                "controllers", "lifecycle_status",
                "ENUM('pending','active','suspended','revoked','retired') "
                "NOT NULL DEFAULT 'pending' AFTER is_active",
            ),
            ("controllers", "software_version", "VARCHAR(80) NULL AFTER lifecycle_status"),
            ("controllers", "hardware_identity", "VARCHAR(160) NULL AFTER software_version"),
            ("audit_log", "village_id", "BIGINT UNSIGNED NULL AFTER user_id"),
            ("vehicles", "village_id", "BIGINT UNSIGNED NULL AFTER id"),
            ("rfid_stickers", "village_id", "BIGINT UNSIGNED NULL AFTER id"),
            ("controllers", "gate_id", "BIGINT UNSIGNED NULL AFTER controller_uid"),
            ("access_events", "village_id", "BIGINT UNSIGNED NULL AFTER id"),
            ("access_events", "gate_id", "BIGINT UNSIGNED NULL AFTER village_id"),
            ("reader_commands", "village_id", "BIGINT UNSIGNED NULL AFTER id"),
            ("reader_commands", "gate_id", "BIGINT UNSIGNED NULL AFTER village_id"),
        ):
            present = connection.execute(
                """
                SELECT COUNT(*) AS count FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?
                """,
                (table, column),
            ).fetchone()["count"]
            if not present:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # Backfill pre-multitenant installations into one explicit tenant.
        # This preserves all existing records while ensuring no controller or
        # recognition remains outside the controller -> gate -> village chain.
        legacy_data = connection.execute(
            """
            SELECT EXISTS(SELECT 1 FROM vehicles WHERE village_id IS NULL)
                OR EXISTS(SELECT 1 FROM rfid_stickers WHERE village_id IS NULL)
                OR EXISTS(SELECT 1 FROM controllers WHERE gate_id IS NULL)
                OR EXISTS(SELECT 1 FROM access_events
                          WHERE village_id IS NULL OR gate_id IS NULL)
                OR EXISTS(SELECT 1 FROM reader_commands
                          WHERE village_id IS NULL OR gate_id IS NULL)
                AS needed
            """
        ).fetchone()["needed"]
        if legacy_data:
            connection.execute(
                """
                INSERT INTO villages (village_uid, name, timezone, is_active)
                VALUES ('legacy-village', 'Legacy Village', 'Asia/Manila', 1)
                ON DUPLICATE KEY UPDATE village_uid = VALUES(village_uid)
                """
            )
            legacy_village_id = connection.execute(
                "SELECT id FROM villages WHERE village_uid = 'legacy-village'"
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO gates (village_id, gate_uid, name, direction, is_active)
                VALUES (?, 'legacy-main-gate', 'Legacy Main Gate', 'entry', 1)
                ON DUPLICATE KEY UPDATE village_id = VALUES(village_id)
                """,
                (legacy_village_id,),
            )
            legacy_gate_id = connection.execute(
                "SELECT id FROM gates WHERE gate_uid = 'legacy-main-gate'"
            ).fetchone()["id"]
            connection.execute(
                "UPDATE vehicles SET village_id = ? WHERE village_id IS NULL",
                (legacy_village_id,),
            )
            connection.execute(
                """
                UPDATE rfid_stickers r
                JOIN vehicles v ON v.id = r.vehicle_id
                SET r.village_id = v.village_id
                WHERE r.village_id IS NULL
                """
            )
            connection.execute(
                "UPDATE controllers SET gate_id = ? WHERE gate_id IS NULL",
                (legacy_gate_id,),
            )
            for table in ("access_events", "reader_commands"):
                connection.execute(
                    f"""
                    UPDATE {table} item
                    JOIN controllers c ON c.controller_uid = item.controller_uid
                    JOIN gates g ON g.id = c.gate_id
                    SET item.village_id = g.village_id, item.gate_id = c.gate_id
                    WHERE item.village_id IS NULL OR item.gate_id IS NULL
                    """
                )
                connection.execute(
                    f"""
                    UPDATE {table}
                    SET village_id = ?, gate_id = ?
                    WHERE village_id IS NULL OR gate_id IS NULL
                    """,
                    (legacy_village_id, legacy_gate_id),
                )

        # Historical rows may reference controller identifiers that no longer
        # exist. Keep the event/command but clear the optional broken link.
        for table in ("access_events", "reader_commands"):
            connection.execute(
                f"""
                UPDATE {table} item
                LEFT JOIN controllers c ON c.controller_uid = item.controller_uid
                SET item.controller_uid = NULL
                WHERE item.controller_uid IS NOT NULL AND c.controller_uid IS NULL
                """
            )

        for table, column, definition in (
            ("vehicles", "village_id", "BIGINT UNSIGNED NOT NULL"),
            ("rfid_stickers", "village_id", "BIGINT UNSIGNED NOT NULL"),
            ("controllers", "gate_id", "BIGINT UNSIGNED NOT NULL"),
            ("access_events", "village_id", "BIGINT UNSIGNED NOT NULL"),
            ("access_events", "gate_id", "BIGINT UNSIGNED NOT NULL"),
            ("reader_commands", "village_id", "BIGINT UNSIGNED NOT NULL"),
            ("reader_commands", "gate_id", "BIGINT UNSIGNED NOT NULL"),
        ):
            null_count = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE {column} IS NULL"
            ).fetchone()["count"]
            if not null_count:
                connection.execute(
                    f"ALTER TABLE {table} MODIFY COLUMN {column} {definition}"
                )

        # The old global unique constraints would prevent two villages from
        # legitimately registering the same plate or RFID value.
        for table, index_name in (
            ("vehicles", "uq_vehicles_plate_number"),
            ("rfid_stickers", "uq_rfid_stickers_value"),
        ):
            present = connection.execute(
                """
                SELECT COUNT(*) AS count FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?
                """,
                (table, index_name),
            ).fetchone()["count"]
            if present:
                connection.execute(f"ALTER TABLE {table} DROP INDEX {index_name}")
        for table, index_name, definition in (
            (
                "access_events",
                "idx_access_events_controller",
                "KEY idx_access_events_controller (controller_uid, detected_at DESC)",
            ),
            (
                "reader_commands",
                "idx_reader_commands_controller_status",
                "KEY idx_reader_commands_controller_status (controller_uid, status, created_at)",
            ),
            (
                "vehicles",
                "uq_vehicles_village_plate",
                "UNIQUE KEY uq_vehicles_village_plate (village_id, plate_number)",
            ),
            (
                "rfid_stickers",
                "uq_rfid_stickers_village_value",
                "UNIQUE KEY uq_rfid_stickers_village_value (village_id, sticker_value)",
            ),
            (
                "controllers",
                "idx_controllers_gate",
                "KEY idx_controllers_gate (gate_id, controller_seen_at)",
            ),
            (
                "gates",
                "uq_gates_village_id",
                "UNIQUE KEY uq_gates_village_id (village_id, id)",
            ),
            (
                "vehicles",
                "uq_vehicles_village_id",
                "UNIQUE KEY uq_vehicles_village_id (village_id, id)",
            ),
            (
                "controllers",
                "uq_controllers_uid_gate",
                "UNIQUE KEY uq_controllers_uid_gate (controller_uid, gate_id)",
            ),
            (
                "access_events",
                "idx_access_events_village_detected",
                "KEY idx_access_events_village_detected (village_id, detected_at)",
            ),
            (
                "access_events",
                "idx_access_events_gate_detected",
                "KEY idx_access_events_gate_detected (gate_id, detected_at)",
            ),
            (
                "reader_commands",
                "idx_reader_commands_village_created",
                "KEY idx_reader_commands_village_created (village_id, created_at)",
            ),
            (
                "reader_commands",
                "idx_reader_commands_gate_created",
                "KEY idx_reader_commands_gate_created (gate_id, created_at)",
            ),
        ):
            present = connection.execute(
                """
                SELECT COUNT(*) AS count FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?
                """,
                (table, index_name),
            ).fetchone()["count"]
            if not present:
                connection.execute(f"ALTER TABLE {table} ADD {definition}")

        # MySQL requires both sides of a text foreign key to use the same
        # collation. Older installations may have created controllers with the
        # server default (for example utf8mb4_0900_ai_ci) while the new table
        # explicitly uses utf8mb4_unicode_ci.
        controller_collation = connection.execute(
            """
            SELECT collation_name AS collation_name FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'controllers'
              AND column_name = 'controller_uid'
            """
        ).fetchone()["collation_name"]
        credential_collation = connection.execute(
            """
            SELECT collation_name AS collation_name FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'controller_credentials'
              AND column_name = 'controller_uid'
            """
        ).fetchone()["collation_name"]
        if controller_collation != credential_collation:
            if not re.fullmatch(r"[A-Za-z0-9_]+", controller_collation or ""):
                raise RuntimeError("Unexpected MySQL controller ID collation.")
            connection.execute(
                "ALTER TABLE controller_credentials MODIFY controller_uid "
                f"VARCHAR(64) COLLATE {controller_collation} NOT NULL"
            )

        # Capture jobs are new, but they join legacy event/controller IDs on
        # upgraded databases. Match each job column to its corresponding
        # source column without changing the existing foreign-key columns.
        for source_table, source_column, target_column, definition in (
            ("controllers", "controller_uid", "controller_uid", "VARCHAR(64) NOT NULL"),
            ("access_events", "attempt_uid", "attempt_uid", "VARCHAR(80) NOT NULL"),
        ):
            source_collation = connection.execute(
                """
                SELECT collation_name AS column_collation FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?
                """,
                (source_table, source_column),
            ).fetchone()["column_collation"]
            target_collation = connection.execute(
                """
                SELECT collation_name AS column_collation FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'camera_capture_jobs'
                  AND column_name = ?
                """,
                (target_column,),
            ).fetchone()["column_collation"]
            if source_collation != target_collation:
                if not re.fullmatch(r"[A-Za-z0-9_]+", source_collation or ""):
                    raise RuntimeError("Unexpected MySQL capture ID collation.")
                connection.execute(
                    f"ALTER TABLE camera_capture_jobs MODIFY {target_column} "
                    f"{definition} COLLATE {source_collation}"
                )

        # Credentials without a matching provisioned controller cannot be
        # used and would prevent the relationship constraint from being added
        # on an upgraded database.
        connection.execute(
            """
            DELETE cc FROM controller_credentials cc
            LEFT JOIN controllers c ON c.controller_uid = cc.controller_uid
            WHERE c.controller_uid IS NULL
            """
        )

        for table, constraint_name, definition in (
            (
                "vehicles", "fk_vehicles_village",
                "FOREIGN KEY (village_id) REFERENCES villages(id) ON DELETE RESTRICT",
            ),
            (
                "rfid_stickers", "fk_rfid_stickers_vehicle_village",
                "FOREIGN KEY (village_id, vehicle_id) "
                "REFERENCES vehicles(village_id, id) ON DELETE CASCADE",
            ),
            (
                "controllers", "fk_controllers_gate",
                "FOREIGN KEY (gate_id) REFERENCES gates(id) ON DELETE RESTRICT",
            ),
            (
                "controller_credentials", "fk_controller_credentials_controller",
                "FOREIGN KEY (controller_uid) REFERENCES controllers(controller_uid) ON DELETE CASCADE",
            ),
            (
                "access_events", "fk_access_events_village_gate",
                "FOREIGN KEY (village_id, gate_id) "
                "REFERENCES gates(village_id, id) ON DELETE RESTRICT",
            ),
            (
                "access_events", "fk_access_events_controller_gate",
                "FOREIGN KEY (controller_uid, gate_id) "
                "REFERENCES controllers(controller_uid, gate_id) ON DELETE RESTRICT",
            ),
            (
                "reader_commands", "fk_reader_commands_village_gate",
                "FOREIGN KEY (village_id, gate_id) "
                "REFERENCES gates(village_id, id) ON DELETE RESTRICT",
            ),
            (
                "reader_commands", "fk_reader_commands_controller_gate",
                "FOREIGN KEY (controller_uid, gate_id) "
                "REFERENCES controllers(controller_uid, gate_id) ON DELETE RESTRICT",
            ),
            (
                "audit_log", "fk_audit_log_village",
                "FOREIGN KEY (village_id) REFERENCES villages(id) ON DELETE SET NULL",
            ),
            (
                "user_gate_assignments",
                "fk_user_gate_assignments_village_gate",
                "FOREIGN KEY (village_id, gate_id) "
                "REFERENCES gates(village_id, id) ON DELETE CASCADE",
            ),
        ):
            present = connection.execute(
                """
                SELECT COUNT(*) AS count FROM information_schema.table_constraints
                WHERE constraint_schema = DATABASE() AND table_name = ?
                  AND constraint_name = ?
                """,
                (table, constraint_name),
            ).fetchone()["count"]
            if not present:
                connection.execute(
                    f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} {definition}"
                )

        connection.execute("SET FOREIGN_KEY_CHECKS = 1")
        connection.commit()
    finally:
        connection.close()


initialize_database()


def get_db() -> DatabaseConnection:
    if "db" not in g:
        g.db = DatabaseConnection()
    return g.db


@app.teardown_appcontext
def close_db(_: BaseException | None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def admin_exists() -> bool:
    row = get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return row is not None


def normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def uploaded_jpeg(field_name: str, label: str, maximum_bytes: int):
    upload = request.files.get(field_name)
    if upload is None:
        return None, b"", None
    contents = upload.read()
    if not contents or len(contents) > maximum_bytes:
        return upload, b"", ({"error": f"The {label} is empty or too large."}, 400)
    if not contents.startswith(b"\xff\xd8\xff"):
        return upload, b"", ({"error": f"The {label} must be a JPEG image."}, 400)
    return upload, contents, None


def store_event_image(directory_name: str, filename: str, contents: bytes) -> str | None:
    if not contents:
        return None
    directory = OUTPUT_DIR / directory_name
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    temporary = destination.with_suffix(".tmp.jpg")
    temporary.write_bytes(contents)
    temporary.replace(destination)
    return destination.relative_to(PROJECT_DIR).as_posix()


def environment_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be 0/1, true/false, yes/no, or on/off.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*allowed_roles: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login", next=request.path))
            effective_roles = set(allowed_roles)
            # Older routes used "administrator" before the multi-village role
            # model renamed the central account to "system_owner".
            if "administrator" in effective_roles:
                effective_roles.add("system_owner")
            if session.get("role") not in effective_roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def current_village_role(connection: DatabaseConnection) -> str | None:
    if session.get("role") in {"system_owner", "administrator"}:
        return "system_owner"
    village_id = selected_village_id(connection)
    if village_id is None:
        return None
    membership = connection.execute(
        """
        SELECT role FROM user_village_roles
        WHERE user_id = ? AND village_id = ?
        """,
        (session.get("user_id", 0), village_id),
    ).fetchone()
    return membership["role"] if membership else None


def tenant_role_required(*allowed_roles: str):
    """Authorize a write from server-resolved village membership."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login", next=request.path))
            role = current_village_role(get_db())
            if role != "system_owner" and role not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def record_audit(
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: str | None = None,
    village_id: int | None = None,
) -> None:
    get_db().execute(
        """
        INSERT INTO audit_log (
            user_id, village_id, action, entity_type, entity_id, details
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session.get("user_id"),
            village_id if village_id is not None else session.get("active_village_id"),
            action,
            entity_type,
            entity_id,
            details,
        ),
    )


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.context_processor
def template_context() -> dict[str, Any]:
    context = {
        "csrf_token": csrf_token,
        "current_user": session.get("username"),
        "current_role": session.get("role"),
    }
    if session.get("user_id"):
        connection = get_db()
        villages = village_records(connection)
        active_village_id = selected_village_id(connection)
        controllers = controller_records(connection)
        selected_uid = selected_controller_uid(connection)
        tenant_role = current_village_role(connection)
        context.update(
            villages=villages,
            selected_village_id=active_village_id,
            controllers=controllers,
            selected_controller_uid=selected_uid,
            selected_controller=next(
                (row for row in controllers if row["controller_uid"] == selected_uid),
                None,
            ),
            tenant_role=tenant_role,
            is_system_owner=tenant_role == "system_owner",
            can_manage_tenant=tenant_role in {"system_owner", "village_admin"},
            can_operate_gate=tenant_role in {
                "system_owner", "village_admin", "security_guard"
            },
        )
    return context


@app.before_request
def validate_authenticated_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    user = get_db().execute(
        "SELECT username, role FROM users WHERE id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    if user is None:
        session.clear()
        if request.endpoint not in {"login", "setup", "static", "health"}:
            return redirect(url_for("login"))
        return None
    session["username"] = user["username"]
    session["role"] = user["role"]
    return None


@app.before_request
def protect_forms() -> None:
    reader_endpoints = {
        "reader_recognition",
        "reader_next_command",
        "reader_complete_command",
        "reader_status",
        "rfid_controller_recognition",
        "rfid_controller_status",
        "controller_capture_request",
        "controller_access_result",
        "camera_agent_next_job",
        "camera_agent_complete_job",
        "camera_job_recognition",
    }
    if request.method == "POST" and request.endpoint not in reader_endpoints:
        supplied = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            abort(400, "Invalid form token. Refresh the page and try again.")


def reader_form_boolean(field: str) -> bool:
    return request.form.get(field, "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


def normalize_controller_uid(value: str, fallback: str) -> str:
    candidate = (value or fallback).strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", candidate):
        raise ValueError("Controller ID must use only letters, numbers, dot, dash, colon, or underscore.")
    return candidate


def request_value(name: str, default: str = "") -> str:
    """Read a scalar from form or JSON requests for hardware compatibility."""
    if name in request.form:
        return request.form.get(name, default)
    payload = request.get_json(silent=True)
    if isinstance(payload, dict) and name in payload:
        value = payload.get(name)
        return default if value is None else str(value)
    return default


def request_controller_uid(fallback: str) -> str:
    return normalize_controller_uid(request_value("controller_id"), fallback)


def request_attempt_uid() -> str | None:
    value = request_value("attempt_uid").strip()
    if not value:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", value):
        raise ValueError("Attempt ID must use only letters, numbers, dot, dash, colon, or underscore.")
    return value


def find_recent_access_event(
    connection: DatabaseConnection,
    village_id: int,
    gate_id: int,
    controller_uid: str,
    attempt_uid: str | None,
    rfid_number: str | None = None,
) -> Any:
    """Find the current gate attempt without crossing tenant or gate boundaries."""
    if attempt_uid:
        event = connection.execute(
            """
            SELECT id FROM access_events
            WHERE village_id = ? AND gate_id = ? AND controller_uid = ?
              AND attempt_uid = ?
            LIMIT 1
            """,
            (village_id, gate_id, controller_uid, attempt_uid),
        ).fetchone()
        if event is not None:
            return event
    if rfid_number:
        return connection.execute(
            """
            SELECT id FROM access_events
            WHERE village_id = ? AND gate_id = ? AND controller_uid = ?
              AND rfid_number = ?
              AND detected_at >= TIMESTAMPADD(SECOND, -10, CURRENT_TIMESTAMP)
            ORDER BY detected_at DESC, id DESC
            LIMIT 1
            """,
            (village_id, gate_id, controller_uid, rfid_number),
        ).fetchone()
    return connection.execute(
        """
        SELECT id FROM access_events
        WHERE village_id = ? AND gate_id = ? AND controller_uid = ?
          AND decision = 'unreadable'
          AND detected_at >= TIMESTAMPADD(SECOND, -10, CURRENT_TIMESTAMP)
        ORDER BY detected_at DESC, id DESC
        LIMIT 1
        """,
        (village_id, gate_id, controller_uid),
    ).fetchone()


def default_controller_name(controller_uid: str, controller_type: str) -> str:
    label = "RFID Controller" if controller_type == "rfid" else "Plate + RFID Controller"
    suffix = controller_uid[-8:] if len(controller_uid) > 8 else controller_uid
    return f"{label} {suffix}"


def ensure_controller(
    connection: DatabaseConnection,
    controller_uid: str,
    controller_type: str | tuple[str, ...],
) -> dict[str, Any]:
    context = connection.execute(
        """
        SELECT c.controller_uid, c.gate_id, g.village_id,
               g.gate_uid, g.name AS gate_name,
               v.village_uid, v.name AS village_name,
               c.controller_type, c.display_name, c.is_active,
               c.lifecycle_status,
               c.is_active = 1 AND c.gate_id IS NOT NULL
                   AND g.is_active = 1 AND v.is_active = 1
                   AS assignment_active
        FROM controllers c
        JOIN gates g ON g.id = c.gate_id
        JOIN villages v ON v.id = g.village_id
        WHERE c.controller_uid = ?
        LIMIT 1
        """,
        (controller_uid,),
    ).fetchone()
    if context is None:
        abort(401, "Controller is not provisioned on this server.")
    allowed_types = (controller_type,) if isinstance(controller_type, str) else controller_type
    if context["controller_type"] not in allowed_types:
        abort(403, "Controller type does not match this endpoint.")
    supplied_key = (
        request.headers.get("X-Controller-Key", "")
        or request.form.get("controller_key", "")
    ).strip()
    if not supplied_key:
        abort(401, "Controller key is required.")
    credential = connection.execute(
        """
        SELECT id, credential_hash FROM controller_credentials
        WHERE controller_uid = ? AND revoked_at IS NULL
        """,
        (controller_uid,),
    ).fetchall()
    credential_id = matching_credential_id(supplied_key, credential)
    if credential_id is None:
        abort(401, "Controller key is invalid or revoked.")
    if not context["assignment_active"]:
        abort(403, "Controller, gate, or village is inactive.")
    if context["lifecycle_status"] == "pending":
        connection.execute(
            """
            UPDATE controllers SET lifecycle_status = 'active'
            WHERE controller_uid = ? AND lifecycle_status = 'pending'
            """,
            (controller_uid,),
        )
        connection.execute(
            """
            INSERT INTO audit_log (
                user_id, village_id, action, entity_type, details
            ) VALUES (NULL, ?, 'controller_activation_confirmed',
                      'controller', ?)
            """,
            (context["village_id"], controller_uid),
        )
        context["lifecycle_status"] = "active"
    elif context["lifecycle_status"] != "active":
        abort(403, "Controller is suspended, revoked, or retired.")
    connection.execute(
        "UPDATE controller_credentials SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
        (credential_id,),
    )
    return context


def village_records(connection: DatabaseConnection) -> list[dict[str, Any]]:
    access_join = ""
    parameters: tuple[Any, ...] = ()
    if session.get("role") not in {"system_owner", "administrator"}:
        access_join = "JOIN user_village_roles uvr ON uvr.village_id = v.id AND uvr.user_id = ?"
        parameters = (session.get("user_id", 0),)
    return connection.execute(
        f"""
        SELECT id, village_uid, name, timezone, is_active,
               (SELECT COUNT(*) FROM gates g WHERE g.village_id = v.id) AS gate_count,
               (SELECT COUNT(*) FROM controllers c JOIN gates g ON g.id = c.gate_id
                WHERE g.village_id = v.id) AS controller_count
        FROM villages v
        {access_join}
        ORDER BY is_active DESC, name
        """,
        parameters,
    ).fetchall()


def selected_village_id(connection: DatabaseConnection) -> int | None:
    villages = village_records(connection)
    if not villages:
        session.pop("active_village_id", None)
        return None
        
    if session.get("role") not in {"system_owner", "administrator"}:
        # Non-owners are strictly bound to their assigned village
        return villages[0]["id"]
        
    known = {row["id"] for row in villages}
    selected = session.get("active_village_id")
    if selected not in known:
        selected = villages[0]["id"]
        session["active_village_id"] = selected
    return selected


def require_selected_village(connection: DatabaseConnection) -> int:
    village_id = selected_village_id(connection)
    if village_id is None:
        abort(409, "Create and select a village before managing village data.")
    return village_id


def controller_records(connection: DatabaseConnection) -> list[dict[str, Any]]:
    accessible = village_records(connection)
    village_ids = [row["id"] for row in accessible]
    if not village_ids:
        return []
    placeholders = ",".join("?" for _ in village_ids)
    gate_filter = ""
    parameters: list[Any] = list(village_ids)
    if session.get("role") not in {"system_owner", "administrator"}:
        assigned_gates = connection.execute(
            "SELECT gate_id FROM user_gate_assignments WHERE user_id = ?",
            (session.get("user_id", 0),),
        ).fetchall()
        gate_ids = [row["gate_id"] for row in assigned_gates]
        if gate_ids:
            gate_placeholders = ",".join("?" for _ in gate_ids)
            gate_filter = f"AND c.gate_id IN ({gate_placeholders})"
            parameters.extend(gate_ids)
    return connection.execute(
        f"""
        SELECT c.controller_uid, c.display_name, c.controller_type, c.is_active,
               c.gate_id, g.gate_uid, g.name AS gate_name,
               v.id AS village_id, v.village_uid, v.name AS village_name,
               controller_seen_at IS NOT NULL AND
               controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP)
                   AS controller_online,
               DATE_FORMAT(controller_seen_at, '%Y-%m-%d %H:%i:%s') AS controller_seen_at
        FROM controllers c
        JOIN gates g ON g.id = c.gate_id
        JOIN villages v ON v.id = g.village_id
        WHERE v.id IN ({placeholders})
        {gate_filter}
        ORDER BY controller_online DESC, display_name, controller_uid
        """,
        parameters,
    ).fetchall()


def selected_controller_uid(connection: DatabaseConnection) -> str | None:
    controllers = controller_records(connection)
    village_id = selected_village_id(connection)
    controllers = [row for row in controllers if row["village_id"] == village_id]
    if not controllers:
        session.pop("active_controller_uid", None)
        return None
    known = {row["controller_uid"] for row in controllers}
    selected = session.get("active_controller_uid")
    if selected not in known:
        selected = controllers[0]["controller_uid"]
        session["active_controller_uid"] = selected
    return selected


@app.post("/controllers/select")
@login_required
def select_controller():
    try:
        controller_uid = normalize_controller_uid(
            request.form.get("controller_uid", ""), ""
        )
    except ValueError as error:
        return {"success": False, "message": str(error)}, 400
    exists = get_db().execute(
        """
        SELECT g.village_id FROM controllers c
        JOIN gates g ON g.id = c.gate_id
        WHERE c.controller_uid = ?
        """,
        (controller_uid,),
    ).fetchone()
    if exists is None:
        return {"success": False, "message": "Controller not found."}, 404
    allowed_villages = {row["id"] for row in village_records(get_db())}
    if exists["village_id"] not in allowed_villages:
        return {"success": False, "message": "You cannot access this controller."}, 403
    session["active_village_id"] = exists["village_id"]
    session["active_controller_uid"] = controller_uid
    return {"success": True, "controller_id": controller_uid}


@app.post("/villages/select")
@role_required("system_owner")
def select_village():
    try:
        village_id = int(request.form.get("village_id", "0"))
    except ValueError:
        return {"success": False, "message": "Invalid village."}, 400
    allowed_villages = {row["id"] for row in village_records(get_db())}
    if village_id not in allowed_villages:
        return {"success": False, "message": "You cannot access this village."}, 403
    exists = get_db().execute(
        "SELECT 1 FROM villages WHERE id = ? AND is_active = 1", (village_id,)
    ).fetchone()
    if exists is None:
        return {"success": False, "message": "Village not found."}, 404
    session["active_village_id"] = village_id
    session.pop("active_controller_uid", None)
    if request.accept_mimetypes.best == "application/json":
        return {"success": True, "village_id": village_id}
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/sites")
@role_required("administrator")
def sites():
    connection = get_db()
    villages = village_records(connection)
    gates = connection.execute(
        """
        SELECT g.*, v.name AS village_name,
               (SELECT COUNT(*) FROM controllers c WHERE c.gate_id = g.id) AS controller_count,
               cam.camera_uid, cam.display_name AS camera_name, cam.transport AS camera_transport,
               cam.status AS camera_status, cam.endpoint_url AS camera_endpoint_url,
               cam.endpoint_url IS NOT NULL AS camera_configured
        FROM gates g JOIN villages v ON v.id = g.village_id
        LEFT JOIN cameras cam ON cam.gate_id = g.id
        ORDER BY v.name, g.name
        """
    ).fetchall()
    gates = [
        dict(gate, camera_configured=camera_is_configured(
            gate["camera_uid"], gate["camera_endpoint_url"]
        ))
        for gate in gates
    ]
    controllers = controller_records(connection)
    unassigned_controllers = connection.execute(
        """
        SELECT c.controller_uid, c.display_name, c.controller_type, c.is_active
        FROM controllers c
        LEFT JOIN gates g ON g.id = c.gate_id
        WHERE g.id IS NULL
        ORDER BY c.display_name, c.controller_uid
        """
    ).fetchall()
    new_credential = session.pop("new_controller_credential", None)
    return render_template(
        "sites.html", villages=villages, gates=gates,
        site_controllers=controllers, unassigned_controllers=unassigned_controllers,
        new_credential=new_credential,
        camera_test_uid=session.get("camera_test_uid"),
        camera_test_version=session.get("camera_test_version"),
    )


def normalize_uid(value: str, label: str) -> str:
    return normalize_tenant_uid(value, label)


@app.post("/sites/villages")
@role_required("administrator")
def village_create():
    try:
        village_uid = normalize_uid(request.form.get("village_uid", ""), "Village ID")
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("sites"))
    name = " ".join(request.form.get("name", "").split())
    timezone = request.form.get("timezone", "Asia/Manila").strip()
    if not 2 <= len(name) <= 160 or not re.fullmatch(r"[A-Za-z0-9_+./-]{1,64}", timezone):
        flash("Enter a valid village name and timezone.", "error")
        return redirect(url_for("sites"))
    try:
        connection = get_db()
        cursor = connection.execute(
            "INSERT INTO villages (village_uid, name, timezone) VALUES (?, ?, ?)",
            (village_uid, name, timezone),
        )
        record_audit("create_village", "village", cursor.lastrowid, name)
        connection.commit()
        session["active_village_id"] = cursor.lastrowid
        flash(f"{name} was created.", "success")
    except IntegrityError:
        flash("That village ID or name is already in use.", "error")
    return redirect(url_for("sites"))


@app.post("/sites/gates")
@role_required("administrator")
def gate_create():
    try:
        village_id = int(request.form.get("village_id", "0"))
        gate_uid = normalize_uid(request.form.get("gate_uid", ""), "Gate ID")
    except (ValueError, TypeError) as error:
        flash(str(error), "error")
        return redirect(url_for("sites"))
    name = " ".join(request.form.get("name", "").split())
    direction = request.form.get("direction", "entry")
    if not 2 <= len(name) <= 160 or direction not in {"entry", "exit", "both"}:
        flash("Enter a valid gate name and direction.", "error")
        return redirect(url_for("sites"))
    try:
        connection = get_db()
        cursor = connection.execute(
            "INSERT INTO gates (village_id, gate_uid, name, direction) VALUES (?, ?, ?, ?)",
            (village_id, gate_uid, name, direction),
        )
        record_audit("create_gate", "gate", cursor.lastrowid, name)
        connection.commit()
        flash(f"{name} was created.", "success")
    except IntegrityError:
        flash("That gate ID/name is already in use or its village is invalid.", "error")
    return redirect(url_for("sites"))


@app.post("/sites/gates/<int:gate_id>/camera")
@role_required("administrator")
def camera_configure(gate_id: int):
    camera_uid = request.form.get("camera_uid", "").strip()
    display_name = " ".join(request.form.get("display_name", "").split())
    transport = request.form.get("transport", "rtsp").strip().lower()
    endpoint_url = request.form.get("endpoint_url", "").strip() or None
    configured_endpoint = endpoint_url or os.environ.get(
        camera_endpoint_env_name(camera_uid), ""
    ).strip() or None
    try:
        validate_camera_config(CameraConfig(camera_uid, transport, configured_endpoint))
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("sites"))
    if not 2 <= len(display_name) <= 100:
        flash("Enter a valid camera name.", "error")
        return redirect(url_for("sites"))
    connection = get_db()
    gate = connection.execute(
        """
        SELECT g.id, g.name, g.village_id, v.name AS village_name
        FROM gates g JOIN villages v ON v.id = g.village_id
        WHERE g.id = ?
        """,
        (gate_id,),
    ).fetchone()
    if gate is None:
        flash("The selected gate does not exist.", "error")
        return redirect(url_for("sites"))
    try:
        connection.execute(
            """
            INSERT INTO cameras (
                camera_uid, gate_id, display_name, transport, endpoint_url,
                status, is_active
            ) VALUES (?, ?, ?, ?, ?, 'unknown', 1)
            ON DUPLICATE KEY UPDATE
                gate_id = VALUES(gate_id), display_name = VALUES(display_name),
                transport = VALUES(transport), endpoint_url = VALUES(endpoint_url),
                is_active = 1, updated_at = CURRENT_TIMESTAMP
            """,
            (camera_uid, gate_id, display_name, transport, endpoint_url),
        )
        record_audit(
            "configure_camera", "camera", None,
            f"{camera_uid} bound to {gate['village_name']} / {gate['name']}",
            village_id=gate["village_id"],
        )
        connection.commit()
        flash(f"{display_name} was bound to {gate['name']}.", "success")
    except IntegrityError:
        flash("That camera ID is already bound to another gate.", "error")
    return redirect(url_for("sites"))


@app.post("/sites/gates/<int:gate_id>/camera/test")
@role_required("administrator")
def camera_test(gate_id: int):
    connection = get_db()
    camera = connection.execute(
        """
        SELECT camera_uid, transport, endpoint_url
        FROM cameras
        WHERE gate_id = ? AND is_active = 1
        """,
        (gate_id,),
    ).fetchone()
    if camera is None:
        flash("Bind a camera to this gate before testing it.", "error")
        return redirect(url_for("sites"))
    endpoint_url = camera["endpoint_url"] or os.environ.get(
        camera_endpoint_env_name(camera["camera_uid"]), ""
    ).strip() or None
    try:
        frame = capture_frame(
            CameraConfig(camera["camera_uid"], camera["transport"], endpoint_url),
            timeout_seconds=10,
        )
        stored_path = store_event_image(
            "camera-tests", f"{camera['camera_uid']}.jpg", frame
        )
        if stored_path is None:
            raise CameraError("The captured frame could not be stored.")
        connection.execute(
            """
            UPDATE cameras
            SET status = 'online', last_seen_at = CURRENT_TIMESTAMP, last_error = NULL
            WHERE camera_uid = ?
            """,
            (camera["camera_uid"],),
        )
        connection.commit()
        session["camera_test_uid"] = camera["camera_uid"]
        session["camera_test_version"] = int(datetime.now().timestamp())
        flash("Camera frame captured successfully.", "success")
    except (CameraError, ValueError) as error:
        connection.execute(
            """
            UPDATE cameras
            SET status = 'degraded', last_seen_at = CURRENT_TIMESTAMP, last_error = ?
            WHERE camera_uid = ?
            """,
            (str(error)[:500], camera["camera_uid"]),
        )
        connection.commit()
        flash(f"Camera test failed: {error}", "error")
    return redirect(url_for("sites"))


@app.get("/sites/cameras/<camera_uid>/test-frame")
@role_required("administrator")
def camera_test_frame(camera_uid: str):
    camera_uid = validate_camera_uid(camera_uid)
    frame_path = OUTPUT_DIR / "camera-tests" / f"{camera_uid}.jpg"
    if not frame_path.is_file():
        abort(404)
    return send_file(frame_path, mimetype="image/jpeg", max_age=0)


@app.post("/sites/controllers")
@role_required("administrator")
def controller_create():
    try:
        gate_id = int(request.form.get("gate_id", "0"))
        controller_uid = normalize_controller_uid(request.form.get("controller_uid", ""), "")
    except (ValueError, TypeError) as error:
        flash(str(error), "error")
        return redirect(url_for("sites"))
    display_name = " ".join(request.form.get("display_name", "").split())
    controller_type = request.form.get("controller_type", "plate")
    if not 2 <= len(display_name) <= 100 or controller_type not in {"plate", "rfid"}:
        flash("Enter a valid controller name and type.", "error")
        return redirect(url_for("sites"))
    controller_key = secrets.token_urlsafe(32)
    credential_hash = controller_key_digest(controller_key)
    try:
        connection = get_db()
        connection.execute(
            """
            INSERT INTO controllers (
                controller_uid, gate_id, display_name, controller_type,
                lifecycle_status
            ) VALUES (?, ?, ?, ?, 'pending')
            """,
            (controller_uid, gate_id, display_name, controller_type),
        )
        cursor = connection.execute(
            """
            INSERT INTO controller_credentials (controller_uid, credential_hash, label)
            VALUES (?, ?, 'Initial credential')
            """,
            (controller_uid, credential_hash),
        )
        record_audit("provision_controller", "controller_credential", cursor.lastrowid, controller_uid)
        connection.commit()
        session["new_controller_credential"] = {
            "controller_id": controller_uid,
            "controller_key": controller_key,
        }
        flash("Controller provisioned. Copy its key now; it will not be shown again.", "success")
    except IntegrityError:
        flash("That controller ID is already used or the gate is invalid.", "error")
    return redirect(url_for("sites"))


@app.post("/sites/controllers/<controller_uid>/assign")
@role_required("administrator")
def controller_assign(controller_uid: str):
    controller_uid = normalize_controller_uid(controller_uid, "")
    try:
        gate_id = int(request.form.get("gate_id", "0"))
    except ValueError:
        flash("Select a valid gate.", "error")
        return redirect(url_for("sites"))
    connection = get_db()
    gate = connection.execute(
        """
        SELECT g.id, g.name, g.village_id, v.name AS village_name
        FROM gates g JOIN villages v ON v.id = g.village_id
        WHERE g.id = ?
        """,
        (gate_id,),
    ).fetchone()
    controller = connection.execute(
        """
        SELECT c.display_name, c.gate_id, g.name AS old_gate_name,
               v.id AS old_village_id, v.name AS old_village_name
        FROM controllers c
        LEFT JOIN gates g ON g.id = c.gate_id
        LEFT JOIN villages v ON v.id = g.village_id
        WHERE c.controller_uid = ?
        """,
        (controller_uid,),
    ).fetchone()
    if gate is None or controller is None:
        flash("The controller or selected gate no longer exists.", "error")
        return redirect(url_for("sites"))
    if controller["gate_id"] == gate_id:
        flash("That controller is already assigned to the selected gate.", "error")
        return redirect(url_for("sites"))
    new_key = secrets.token_urlsafe(32)
    connection.execute("START TRANSACTION")
    connection.execute(
        """
        UPDATE reader_commands
        SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
            result_message = 'Cancelled during controller transfer'
        WHERE controller_uid = ? AND status IN ('pending', 'active')
        """,
        (controller_uid,),
    )
    connection.execute(
        """
        UPDATE controller_credentials SET revoked_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ? AND revoked_at IS NULL
        """,
        (controller_uid,),
    )
    connection.execute(
        """
        UPDATE controllers
        SET gate_id = ?, is_active = 1, lifecycle_status = 'pending',
            updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (gate_id, controller_uid),
    )
    credential = connection.execute(
        """
        INSERT INTO controller_credentials (controller_uid, credential_hash, label)
        VALUES (?, ?, 'Transfer credential')
        """,
        (controller_uid, controller_key_digest(new_key)),
    )
    record_audit(
        "assign_controller",
        "controller",
        None,
        (
            f"{controller_uid}: "
            f"{controller['old_village_name'] or 'Unassigned'} / "
            f"{controller['old_gate_name'] or 'Unassigned'} -> "
            f"{gate['village_name']} / {gate['name']}; "
            "old credential revoked and pending commands cancelled"
        ),
        village_id=gate["village_id"],
    )
    connection.commit()
    session["new_controller_credential"] = {
        "controller_id": controller_uid,
        "controller_key": new_key,
        "credential_id": credential.lastrowid,
    }
    flash(
        (
            f"{controller['display_name']} was transferred to "
            f"{gate['village_name']} · {gate['name']}. Install the new key; "
            "the old key no longer works."
        ),
        "success",
    )
    return redirect(url_for("sites"))


@app.post("/sites/villages/<int:village_id>/toggle")
@role_required("administrator")
def village_toggle(village_id: int):
    connection = get_db()
    row = connection.execute(
        "SELECT name, is_active FROM villages WHERE id = ?", (village_id,)
    ).fetchone()
    if row is None:
        abort(404)
    new_state = 0 if row["is_active"] else 1
    connection.execute(
        "UPDATE villages SET is_active = ? WHERE id = ?", (new_state, village_id)
    )
    record_audit("activate_village" if new_state else "deactivate_village", "village", village_id, row["name"])
    connection.commit()
    flash(f'{row["name"]} is now {"active" if new_state else "inactive"}.', "success")
    return redirect(url_for("sites"))


@app.post("/sites/gates/<int:gate_id>/toggle")
@role_required("administrator")
def gate_toggle(gate_id: int):
    connection = get_db()
    row = connection.execute(
        "SELECT name, is_active FROM gates WHERE id = ?", (gate_id,)
    ).fetchone()
    if row is None:
        abort(404)
    new_state = 0 if row["is_active"] else 1
    connection.execute("UPDATE gates SET is_active = ? WHERE id = ?", (new_state, gate_id))
    record_audit("activate_gate" if new_state else "deactivate_gate", "gate", gate_id, row["name"])
    connection.commit()
    flash(f'{row["name"]} is now {"active" if new_state else "inactive"}.', "success")
    return redirect(url_for("sites"))


@app.post("/sites/controllers/<controller_uid>/toggle")
@role_required("administrator")
def controller_toggle(controller_uid: str):
    controller_uid = normalize_controller_uid(controller_uid, "")
    connection = get_db()
    row = connection.execute(
        "SELECT display_name, is_active FROM controllers WHERE controller_uid = ?",
        (controller_uid,),
    ).fetchone()
    if row is None:
        abort(404)
    new_state = 0 if row["is_active"] else 1
    connection.execute(
        """
        UPDATE controllers
        SET is_active = ?, lifecycle_status = ?
        WHERE controller_uid = ?
        """,
        (new_state, "pending" if new_state else "suspended", controller_uid),
    )
    record_audit("activate_controller" if new_state else "deactivate_controller", "controller", None, controller_uid)
    connection.commit()
    flash(f'{row["display_name"]} is now {"active" if new_state else "inactive"}.', "success")
    return redirect(url_for("sites"))


@app.post("/sites/controllers/<controller_uid>/rotate-key")
@role_required("administrator")
def controller_rotate_key(controller_uid: str):
    controller_uid = normalize_controller_uid(controller_uid, "")
    connection = get_db()
    exists = connection.execute(
        "SELECT 1 FROM controllers WHERE controller_uid = ?", (controller_uid,)
    ).fetchone()
    if exists is None:
        abort(404)
    new_key = secrets.token_urlsafe(32)
    connection.execute(
        """
        UPDATE controller_credentials SET revoked_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ? AND revoked_at IS NULL
        """,
        (controller_uid,),
    )
    cursor = connection.execute(
        """
        INSERT INTO controller_credentials (controller_uid, credential_hash, label)
        VALUES (?, ?, 'Rotated credential')
        """,
        (controller_uid, controller_key_digest(new_key)),
    )
    connection.execute(
        """
        UPDATE controllers
        SET is_active = 1, lifecycle_status = 'pending'
        WHERE controller_uid = ?
        """,
        (controller_uid,),
    )
    record_audit("rotate_controller_key", "controller_credential", cursor.lastrowid, controller_uid)
    connection.commit()
    session["new_controller_credential"] = {
        "controller_id": controller_uid,
        "controller_key": new_key,
    }
    flash("Controller key rotated. The previous key stopped working immediately.", "success")
    return redirect(url_for("sites"))


@app.post("/controllers/name")
@role_required("administrator")
def name_controller():
    try:
        controller_uid = normalize_controller_uid(
            request.form.get("controller_uid", ""), ""
        )
    except ValueError as error:
        return {"success": False, "message": str(error)}, 400
    display_name = " ".join(request.form.get("display_name", "").split())
    if not 2 <= len(display_name) <= 100:
        return {"success": False, "message": "Name must contain 2 to 100 characters."}, 400
    connection = get_db()
    cursor = connection.execute(
        "UPDATE controllers SET display_name = ? WHERE controller_uid = ?",
        (display_name, controller_uid),
    )
    if cursor.rowcount == 0:
        return {"success": False, "message": "Controller not found."}, 404
    connection.commit()
    record_audit("rename_controller", "controller", None, f"{controller_uid} renamed to {display_name}")
    connection.commit()
    return {"success": True, "display_name": display_name}


@app.get("/api/controllers")
@login_required
def controllers_status():
    connection = get_db()
    selected_uid = selected_controller_uid(connection)
    return {
        "selected_controller_id": selected_uid,
        "controllers": [
            {
                "controller_id": row["controller_uid"],
                "display_name": row["display_name"],
                "controller_type": row["controller_type"],
                "controller_online": bool(row["controller_online"]),
                "last_seen": row["controller_seen_at"],
            }
            for row in controller_records(connection)
        ],
    }, 200, {"Cache-Control": "no-store"}


@app.post("/api/rfid-controller/status")
def rfid_controller_status():
    """Record a camera-less RFID controller heartbeat and live I/O state."""
    gate_state = request_value("gate_state", "idle_closed").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{1,40}", gate_state):
        return {"error": "Invalid gate state."}, 400
    try:
        controller_uid = request_controller_uid("unprovisioned-rfid-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    # The current ESP8266 firmware reports the same heartbeat endpoint in both
    # legacy RFID-only mode and the new combined Plate + RFID mode.
    controller = ensure_controller(connection, controller_uid, ("rfid", "plate"))
    if not controller["assignment_active"]:
        return {"error": "Controller gate or village is inactive."}, 403
    connection.execute(
        """
        UPDATE controllers
        SET camera_state = ?,
            detector_state = ?, gate_state = ?, camera_connected = ?,
            rfid_connected = ?,
            loop_active = ?, ir_blocked = ?, barrier_open = ?,
            traffic_green = ?, plate_unrecognized = ?,
            controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (
            "remote" if controller["controller_type"] == "plate" else "unavailable",
            "server" if controller["controller_type"] == "plate" else "idle",
            gate_state,
            int(controller["controller_type"] == "plate"),
            reader_form_boolean("rfid_connected") if "rfid_connected" in request.form else True,
            reader_form_boolean("loop_active"),
            reader_form_boolean("ir_blocked"),
            reader_form_boolean("barrier_open"),
            reader_form_boolean("traffic_green"),
            reader_form_boolean("credential_unrecognized"),
            controller_uid,
        ),
    )
    connection.commit()
    return {
        "accepted": True,
        "controller_id": controller_uid,
        "controller_type": controller["controller_type"],
        "camera": controller["controller_type"] == "plate",
    }


@app.post("/api/rfid-controller/recognitions")
def rfid_controller_recognition():
    """Authorize an RFID scan and create a normal camera-less access event."""
    rfid_number = normalize_rfid(request_value("rfid"))
    if len(rfid_number) < 4 or len(rfid_number) > 64:
        return {"error": "A valid RFID value containing 4 to 64 characters is required."}, 400

    try:
        controller_uid = request_controller_uid("unprovisioned-rfid-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    # A Plate + RFID NodeMCU submits both credentials through this endpoint;
    # retain support for legacy RFID-only controllers as well.
    controller = ensure_controller(connection, controller_uid, ("rfid", "plate"))
    if not controller["assignment_active"]:
        return {"error": "Controller gate or village is inactive."}, 403
    try:
        attempt_uid = request_attempt_uid()
    except ValueError as error:
        return {"error": str(error)}, 400
    vehicle = connection.execute(
        """
        SELECT v.id, v.plate_number, v.owner_name, v.vehicle_type, v.make, v.model
        FROM rfid_stickers r
        JOIN vehicles v ON v.id = r.vehicle_id
        WHERE r.village_id = ? AND v.village_id = ?
          AND r.sticker_value = ? AND r.is_active = 1
          AND v.is_active = 1
          AND (v.registration_expires_on IS NULL
               OR v.registration_expires_on >= CURRENT_DATE)
        LIMIT 1
        """,
        (controller["village_id"], controller["village_id"], rfid_number),
    ).fetchone()
    authorized = vehicle is not None
    decision = "authorized" if authorized else "denied"
    plate = vehicle["plate_number"] if vehicle else "RFID"
    reason = "rfid_authorized" if authorized else "rfid_not_registered_or_expired"

    correlated = find_recent_access_event(
        connection, controller["village_id"], controller["gate_id"],
        controller_uid, attempt_uid, rfid_number,
    )
    duplicate = connection.execute(
        """
        SELECT id FROM access_events
        WHERE village_id = ? AND controller_uid = ? AND rfid_number = ?
          AND detected_at >= TIMESTAMPADD(
            SECOND,
            -CAST(COALESCE((SELECT `value` FROM settings
                WHERE `key` = 'duplicate_event_seconds'), '30') AS SIGNED),
            NOW()
        )
        ORDER BY detected_at DESC LIMIT 1
        """,
        (controller["village_id"], controller_uid, rfid_number),
    ).fetchone()
    event = correlated or duplicate
    event_id = event["id"] if event else None
    if correlated is not None:
        connection.execute(
            """
            UPDATE access_events
            SET rfid_number = ?, rfid_authorized = ?,
                plate_number = CASE
                    WHEN plate_number IN ('UNREADABLE', 'RFID') AND ? IS NOT NULL
                    THEN ? ELSE plate_number END,
                vehicle_id = COALESCE(vehicle_id, ?),
                decision = CASE WHEN decision = 'authorized' OR ? = 1
                                THEN 'authorized' ELSE decision END,
                gate_action = CASE WHEN gate_action = 'opened' OR ? = 1
                                   THEN 'opened' ELSE gate_action END,
                notes = CONCAT_WS('; ', notes, ?)
            WHERE id = ? AND village_id = ?
            """,
            (
                rfid_number,
                int(authorized),
                vehicle["plate_number"] if vehicle else None,
                vehicle["plate_number"] if vehicle else None,
                vehicle["id"] if vehicle else None,
                int(authorized),
                int(authorized),
                reason,
                event_id,
                controller["village_id"],
            ),
        )
    elif duplicate is None:
        cursor = connection.execute(
            """
            INSERT INTO access_events (
                village_id, gate_id, controller_uid, attempt_uid, vehicle_id,
                plate_number, rfid_number, rfid_required,
                rfid_authorized, decision, gate_action, detector_confidence,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, ?)
            """,
            (
                controller["village_id"],
                controller["gate_id"],
                controller_uid,
                attempt_uid,
                vehicle["id"] if vehicle else None,
                plate,
                rfid_number,
                int(authorized),
                decision,
                "opened" if authorized else "kept_closed",
                reason,
            ),
        )
        event_id = cursor.lastrowid

    # Do not modify camera or detector fields. The hardware plate
    # controller may be using those fields at the same time.
    connection.execute(
        """
        UPDATE controllers
        SET last_plate = ?, last_rfid = ?,
            controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (plate, rfid_number, controller_uid),
    )
    connection.commit()
    return {
        "accepted": True,
        "controller_id": controller_uid,
        "village_id": controller["village_uid"],
        "gate_id": controller["gate_uid"],
        "attempt_uid": attempt_uid,
        "authorized": authorized,
        "decision": decision,
        "duplicate": duplicate is not None,
        "event_id": event_id,
        "owner": vehicle["owner_name"] if vehicle else None,
        "plate": plate,
        "rfid": rfid_number,
        "vehicle_type": vehicle["vehicle_type"] if vehicle else None,
        "make": vehicle["make"] if vehicle else None,
        "model": vehicle["model"] if vehicle else None,
        "authorization_reason": reason,
    }


@app.post("/api/controller/access-result")
def controller_access_result():
    """Return the current authorization state for a NodeMCU vehicle attempt."""
    try:
        controller_uid = request_controller_uid("unprovisioned-plate-controller")
        attempt_uid = request_attempt_uid()
    except ValueError as error:
        return {"error": str(error)}, 400
    if attempt_uid is None:
        return {"error": "A capture attempt ID is required."}, 400
    connection = get_db()
    controller = ensure_controller(connection, controller_uid, "plate")
    event = connection.execute(
        """
        SELECT e.id, e.plate_number, e.rfid_number, e.decision, e.gate_action,
               e.vehicle_id, e.annotated_image_path, j.status AS job_status
        FROM access_events e
        LEFT JOIN camera_capture_jobs j ON j.attempt_uid COLLATE utf8mb4_unicode_ci =
            e.attempt_uid COLLATE utf8mb4_unicode_ci
        WHERE e.village_id = ? AND e.gate_id = ?
          AND e.controller_uid = ? AND e.attempt_uid = ?
        LIMIT 1
        """,
        (
            controller["village_id"], controller["gate_id"],
            controller_uid, attempt_uid,
        ),
    ).fetchone()
    connection.commit()
    if event is None:
        return {"accepted": True, "attempt_uid": attempt_uid, "status": "pending"}, 202
    if event["decision"] == "authorized":
        status = "authorized"
    elif event["decision"] == "denied":
        status = "denied"
    elif event["job_status"] in {"recognized", "timed_out", "failed"}:
        status = "denied"
    else:
        status = "pending"
    return {
        "accepted": True,
        "attempt_uid": attempt_uid,
        "event_id": event["id"],
        "status": status,
        "authorized": status == "authorized",
        "plate": event["plate_number"],
        "rfid": event["rfid_number"],
        "gate_action": event["gate_action"],
        "annotated_image_available": bool(event["annotated_image_path"]),
    }


@app.post("/api/internal/camera-jobs/<int:job_id>/recognition")
def camera_job_recognition(job_id: int):
    """Accept a result from the trusted server-side recognition worker."""
    configured_key = os.environ.get("CAMERA_WORKER_KEY", "").strip()
    supplied_key = request.headers.get("X-Camera-Worker-Key", "").strip()
    if not configured_key or not hmac.compare_digest(supplied_key, configured_key):
        abort(401, "Camera worker authentication failed.")
    plate = normalize_plate(request_value("plate"))
    if not plate or len(plate) > 20:
        return {"error": "A valid alphanumeric plate is required."}, 400
    try:
        detector_confidence = min(1.0, max(0.0, float(request_value("detector_confidence", "0"))))
        ocr_confidence = min(1.0, max(0.0, float(request_value("ocr_confidence", "0"))))
    except ValueError:
        return {"error": "Recognition confidence must be numeric."}, 400
    upload, annotated_bytes, image_error = uploaded_jpeg(
        "annotated_image", "annotated camera image", MAX_FRAME_BYTES
    )
    if image_error is not None:
        return image_error
    if upload is None or not annotated_bytes:
        return {"error": "An annotated camera image is required."}, 400
    connection = get_db()
    job = connection.execute(
        """
        SELECT j.id, j.village_id, j.gate_id, j.controller_uid, j.attempt_uid,
               c.village_uid, g.gate_uid
        FROM camera_capture_jobs j
        JOIN controllers ct ON ct.controller_uid = j.controller_uid
        JOIN gates g ON g.id = j.gate_id
        JOIN villages c ON c.id = j.village_id
        WHERE j.id = ? AND j.status IN ('captured', 'capturing', 'pending')
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if job is None:
        return {"error": "Camera recognition job not found or already completed."}, 404
    vehicle = connection.execute(
        """
        SELECT id, owner_name
        FROM vehicles
        WHERE village_id = ? AND plate_number = ? AND is_active = 1
          AND (registration_expires_on IS NULL OR registration_expires_on >= CURRENT_DATE)
        LIMIT 1
        """,
        (job["village_id"], plate),
    ).fetchone()
    authorized = vehicle is not None
    decision = "authorized" if authorized else "denied"
    event_directory = (
        f"captures/{job['village_uid']}/{job['gate_uid']}/"
        f"{datetime.now():%Y}/{datetime.now():%m}/{job_id}"
    )
    stored_path = store_event_image(event_directory, "annotated.jpg", annotated_bytes)
    if stored_path is None:
        return {"error": "The annotated image could not be stored."}, 500
    event = connection.execute(
        """
        SELECT id FROM access_events
        WHERE village_id = ? AND gate_id = ? AND controller_uid = ? AND attempt_uid = ?
        LIMIT 1
        """,
        (job["village_id"], job["gate_id"], job["controller_uid"], job["attempt_uid"]),
    ).fetchone()
    if event is None:
        return {"error": "The capture attempt has no access event."}, 409
    event_id = event["id"]
    connection.execute(
        """
        UPDATE access_events
        SET plate_number = ?, vehicle_id = ?, detector_confidence = ?,
            ocr_confidence = ?, decision = CASE WHEN decision = 'authorized'
                OR ? = 1 THEN 'authorized' ELSE ? END,
            annotated_image_path = ?, notes = CONCAT_WS('; ', notes, 'server_recognition')
        WHERE id = ? AND village_id = ?
        """,
        (
            plate, vehicle["id"] if vehicle else None, detector_confidence,
            ocr_confidence, int(authorized), decision, stored_path,
            event_id, job["village_id"],
        ),
    )
    connection.execute(
        """
        INSERT INTO capture_images (
            village_id, access_event_id, image_type, storage_path, sha256
        ) VALUES (?, ?, 'annotated', ?, ?)
        ON DUPLICATE KEY UPDATE storage_path = VALUES(storage_path), sha256 = VALUES(sha256)
        """,
        (job["village_id"], event_id, stored_path, hashlib.sha256(annotated_bytes).hexdigest()),
    )
    connection.execute(
        """
        UPDATE camera_capture_jobs
        SET status = 'recognized', completed_at = CURRENT_TIMESTAMP, image_path = ?
        WHERE id = ?
        """,
        (stored_path, job_id),
    )
    connection.commit()
    return {
        "accepted": True,
        "job_id": job_id,
        "event_id": event_id,
        "attempt_uid": job["attempt_uid"],
        "plate": plate,
        "authorized": authorized,
        "decision": decision,
    }


@app.post("/api/controller/capture-request")
def controller_capture_request():
    """Create a gate-bound camera job requested by a NodeMCU controller."""
    try:
        controller_uid = request_controller_uid("unprovisioned-plate-controller")
        attempt_uid = request_value("attempt_uid").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", attempt_uid):
            return {"error": "A valid capture attempt ID is required."}, 400
    except ValueError as error:
        return {"error": str(error)}, 400

    connection = get_db()
    controller = ensure_controller(connection, controller_uid, "plate")
    camera = connection.execute(
        """
        SELECT camera_uid, transport, status
        FROM cameras
        WHERE gate_id = ? AND is_active = 1
        LIMIT 1
        """,
        (controller["gate_id"],),
    ).fetchone()
    if camera is None:
        return {"error": "No active camera is bound to this gate."}, 409

    existing = connection.execute(
        """
        SELECT id, camera_uid, status
        FROM camera_capture_jobs
        WHERE attempt_uid = ? AND controller_uid = ?
        LIMIT 1
        """,
        (attempt_uid, controller_uid),
    ).fetchone()
    if existing is not None:
        connection.commit()
        return {
            "accepted": True,
            "job_id": existing["id"],
            "attempt_uid": attempt_uid,
            "camera_id": existing["camera_uid"],
            "status": existing["status"],
            "idempotent": True,
        }, 200

    try:
        validate_camera_uid(camera["camera_uid"])
        cursor = connection.execute(
            """
            INSERT INTO camera_capture_jobs (
                village_id, gate_id, camera_uid, controller_uid, attempt_uid
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                controller["village_id"],
                controller["gate_id"],
                camera["camera_uid"],
                controller_uid,
                attempt_uid,
            ),
        )
        event_cursor = connection.execute(
            """
            INSERT INTO access_events (
                village_id, gate_id, controller_uid, attempt_uid,
                plate_number, decision, gate_action, notes
            ) VALUES (?, ?, ?, ?, 'UNREADABLE', 'unreadable', 'not_requested',
                      'capture_requested')
            """,
            (
                controller["village_id"], controller["gate_id"],
                controller_uid, attempt_uid,
            ),
        )
    except IntegrityError:
        connection.rollback()
        existing = connection.execute(
            """
            SELECT id, camera_uid, status
            FROM camera_capture_jobs
            WHERE attempt_uid = ? AND controller_uid = ?
            LIMIT 1
            """,
            (attempt_uid, controller_uid),
        ).fetchone()
        if existing is None:
            return {"error": "The capture request could not be created."}, 409
        return {
            "accepted": True,
            "job_id": existing["id"],
            "attempt_uid": attempt_uid,
            "camera_id": existing["camera_uid"],
            "status": existing["status"],
            "idempotent": True,
        }, 200

    connection.execute(
        """
        UPDATE controllers
        SET detector_state = 'queued', camera_state = ?, camera_connected = 1,
            controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (camera["status"], controller_uid),
    )
    connection.commit()
    return {
        "accepted": True,
        "job_id": cursor.lastrowid,
        "event_id": event_cursor.lastrowid,
        "attempt_uid": attempt_uid,
        "camera_id": camera["camera_uid"],
        "transport": camera["transport"],
        "status": "pending",
        "capture_timeout_seconds": 10,
        "idempotent": False,
    }, 202


@app.post("/api/reader/status")
def reader_status():
    gate_state = request.form.get("gate_state", "unknown").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{1,40}", gate_state):
        return {"error": "Invalid gate state."}, 400
    camera_connected = reader_form_boolean("camera_connected")
    detector_state = request.form.get("detector_state", "idle").strip().lower()
    if detector_state not in {"idle", "active"}:
        return {"error": "Invalid detector state."}, 400
    try:
        controller_uid = request_controller_uid("unprovisioned-plate-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    controller = ensure_controller(connection, controller_uid, "plate")
    if not controller["assignment_active"]:
        return {"error": "Controller gate or village is inactive."}, 403
    connection.execute(
        """
        UPDATE controllers
        SET camera_state = ?, detector_state = ?, gate_state = ?,
            camera_connected = ?, rfid_connected = ?, loop_active = ?, ir_blocked = ?,
            barrier_open = ?, traffic_green = ?, plate_unrecognized = ?,
            controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (
            "remote" if camera_connected else "unavailable",
            detector_state,
            gate_state,
            camera_connected,
            reader_form_boolean("rfid_connected"),
            reader_form_boolean("loop_active"),
            reader_form_boolean("ir_blocked"),
            reader_form_boolean("barrier_open"),
            reader_form_boolean("traffic_green"),
            reader_form_boolean("plate_unrecognized"),
            controller_uid,
        ),
    )
    connection.commit()
    return {"accepted": True, "controller_id": controller_uid}


def ensure_camera_agent(connection: DatabaseConnection) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate a camera agent through the controller assigned to its gate."""
    try:
        controller_uid = request_controller_uid("unprovisioned-plate-controller")
        camera_uid = validate_camera_uid(request_value("camera_id"))
    except ValueError as error:
        abort(400, str(error))
    controller = ensure_controller(connection, controller_uid, "plate")
    camera = connection.execute(
        """
        SELECT camera_uid, gate_id, transport, status
        FROM cameras
        WHERE camera_uid = ? AND gate_id = ? AND is_active = 1
        LIMIT 1
        """,
        (camera_uid, controller["gate_id"]),
    ).fetchone()
    if camera is None:
        abort(403, "Camera is not bound to the controller's gate.")
    return controller, camera


@app.post("/api/camera-agent/jobs/next")
def camera_agent_next_job():
    """Claim the oldest pending capture job for a gate-bound camera agent."""
    connection = get_db()
    controller, camera = ensure_camera_agent(connection)
    connection.execute("START TRANSACTION")
    job = connection.execute(
        """
        SELECT id, attempt_uid
        FROM camera_capture_jobs
        WHERE camera_uid = ? AND gate_id = ? AND status = 'pending'
        ORDER BY requested_at, id
        LIMIT 1 FOR UPDATE
        """,
        (camera["camera_uid"], controller["gate_id"]),
    ).fetchone()
    if job is None:
        connection.commit()
        return "", 204
    connection.execute(
        """
        UPDATE camera_capture_jobs
        SET status = 'capturing', started_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
        """,
        (job["id"],),
    )
    connection.execute(
        """
        UPDATE cameras
        SET status = 'online', last_seen_at = CURRENT_TIMESTAMP, last_error = NULL
        WHERE camera_uid = ?
        """,
        (camera["camera_uid"],),
    )
    connection.commit()
    return {
        "accepted": True,
        "job_id": job["id"],
        "attempt_uid": job["attempt_uid"],
        "camera_id": camera["camera_uid"],
        "capture_timeout_seconds": 10,
    }


@app.post("/api/camera-agent/jobs/<int:job_id>/complete")
def camera_agent_complete_job(job_id: int):
    """Store an annotated frame delivered by a camera agent."""
    connection = get_db()
    controller, camera = ensure_camera_agent(connection)
    upload, image_bytes, image_error = uploaded_jpeg(
        "annotated_image", "annotated camera image", MAX_FRAME_BYTES
    )
    if image_error is not None:
        return image_error
    if upload is None or not image_bytes:
        return {"error": "An annotated camera image is required."}, 400
    job = connection.execute(
        """
        SELECT id, village_id, gate_id, attempt_uid, status
        FROM camera_capture_jobs
        WHERE id = ? AND camera_uid = ? AND gate_id = ?
        LIMIT 1
        """,
        (job_id, camera["camera_uid"], controller["gate_id"]),
    ).fetchone()
    if job is None:
        return {"error": "Camera capture job not found."}, 404
    if job["status"] not in {"capturing", "pending"}:
        return {"error": "Camera capture job is already complete or failed."}, 409
    directory = (
        f"camera-jobs/{job['village_id']}/{job['gate_id']}/"
        f"{datetime.now():%Y}/{datetime.now():%m}"
    )
    stored_path = store_event_image(directory, f"{job_id}-annotated.jpg", image_bytes)
    if stored_path is None:
        return {"error": "The annotated camera image could not be stored."}, 500
    connection.execute(
        """
        UPDATE camera_capture_jobs
        SET status = 'captured', completed_at = CURRENT_TIMESTAMP, image_path = ?,
            error_message = NULL
        WHERE id = ? AND camera_uid = ?
        """,
        (stored_path, job_id, camera["camera_uid"]),
    )
    connection.execute(
        """
        UPDATE cameras
        SET status = 'online', last_seen_at = CURRENT_TIMESTAMP, last_error = NULL
        WHERE camera_uid = ?
        """,
        (camera["camera_uid"],),
    )
    connection.commit()
    return {
        "accepted": True,
        "job_id": job_id,
        "attempt_uid": job["attempt_uid"],
        "status": "captured",
        "image_path": stored_path,
    }


@app.post("/api/reader/commands/next")
def reader_next_command():
    try:
        controller_uid = request_controller_uid("unprovisioned-plate-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    controller = ensure_controller(connection, controller_uid, "plate")
    if not controller["assignment_active"]:
        return {"error": "Controller gate or village is inactive."}, 403
    connection.execute("START TRANSACTION")
    connection.execute(
        """
        UPDATE reader_commands
        SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
            result_message = 'Hardware command expired before controller pickup'
        WHERE controller_uid = ? AND status = 'pending' AND command_type != 'capture'
          AND created_at < TIMESTAMPADD(SECOND, -10, CURRENT_TIMESTAMP)
        """,
        (controller_uid,),
    )
    command = connection.execute(
        """
        SELECT id, command_type, serial_tx_hex, serial_baud,
               serial_data_bits, serial_parity, serial_stop_bits,
               serial_timeout_ms
        FROM reader_commands
        WHERE controller_uid = ? AND status = 'pending'
        ORDER BY created_at, id
        LIMIT 1 FOR UPDATE
        """,
        (controller_uid,),
    ).fetchone()
    if command is None:
        connection.commit()
        return "", 204
    connection.execute(
        """
        UPDATE reader_commands
        SET status = 'active', started_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'pending'
        """,
        (command["id"],),
    )
    connection.execute(
        """
        UPDATE controllers
        SET camera_state = 'remote', detector_state = 'active',
            camera_connected = 1, controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (controller_uid,),
    )
    connection.commit()
    response = {
        "command": command["command_type"],
        "command_id": command["id"],
    }
    if command["command_type"] == "rfid_serial":
        response["serial"] = {
            "tx_hex": command["serial_tx_hex"],
            "baud": command["serial_baud"],
            "data_bits": command["serial_data_bits"],
            "parity": command["serial_parity"],
            "stop_bits": command["serial_stop_bits"],
            "timeout_ms": command["serial_timeout_ms"],
        }
    return response


@app.post("/api/reader/commands/<int:command_id>/complete")
def reader_complete_command(command_id: int):
    try:
        controller_uid = request_controller_uid("unprovisioned-plate-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    requested_status = request.form.get("status", "completed")
    status = requested_status if requested_status in {"completed", "failed"} else "failed"
    message = request.form.get("message", "")[:500] or None
    response_data = request.form.get("response_data", "")[:16000] or None
    timings = []
    for field in ("frames_ms", "yolo_ms", "ocr_ms", "server_ms", "total_ms"):
        try:
            value = int(request.form.get(field, ""))
            timings.append(min(600_000, max(0, value)))
        except (TypeError, ValueError):
            timings.append(None)
    connection = get_db()
    controller = ensure_controller(connection, controller_uid, "plate")
    if not controller["assignment_active"]:
        return {"error": "Controller gate or village is inactive."}, 403
    cursor = connection.execute(
        """
        UPDATE reader_commands
        SET status = ?, completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
            result_message = ?,
            response_data = COALESCE(?, response_data),
            frames_ms = COALESCE(?, frames_ms), yolo_ms = COALESCE(?, yolo_ms),
            ocr_ms = COALESCE(?, ocr_ms), server_ms = COALESCE(?, server_ms),
            total_ms = COALESCE(?, total_ms)
        WHERE id = ? AND controller_uid = ?
        """,
        (status, message, response_data, *timings, command_id, controller_uid),
    )
    connection.execute(
        """
        UPDATE controllers
        SET camera_state = 'remote', detector_state = 'idle',
            camera_connected = 1, controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (controller_uid,),
    )
    connection.commit()
    return {"accepted": cursor.rowcount > 0, "command_id": command_id, "status": status}


@app.post("/api/reader/recognitions")
def reader_recognition():
    try:
        controller_uid = request_controller_uid("unprovisioned-plate-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    plate = normalize_plate(request.form.get("plate", ""))
    if not plate or len(plate) > 20:
        return {"error": "A valid alphanumeric plate is required."}, 400
    try:
        detector_confidence = float(request.form.get("detector_confidence", "0"))
    except ValueError:
        return {"error": "Detector confidence must be numeric."}, 400
    detector_confidence = min(1.0, max(0.0, detector_confidence))
    rfid_number = normalize_rfid(request.form.get("rfid", ""))
    if len(rfid_number) > 64:
        return {"error": "RFID number cannot exceed 64 alphanumeric characters."}, 400
    rfid_required = request.form.get("rfid_required", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }

    image, image_bytes, image_error = uploaded_jpeg(
        "image", "enhanced plate crop", MAX_CROP_BYTES
    )
    _, raw_frame_bytes, raw_frame_error = uploaded_jpeg(
        "raw_frame", "raw camera frame", MAX_FRAME_BYTES
    )
    _, annotated_frame_bytes, annotated_frame_error = uploaded_jpeg(
        "annotated_frame", "annotated camera frame", MAX_FRAME_BYTES
    )
    for upload_error in (image_error, raw_frame_error, annotated_frame_error):
        if upload_error is not None:
            return upload_error
    has_display_frame = bool(raw_frame_bytes or annotated_frame_bytes)
    is_no_plate_capture = plate == "UNREADABLE" and has_display_frame
    if image is None and not (is_no_plate_capture or (rfid_required and rfid_number)):
        return {"error": "The enhanced plate crop is required."}, 400

    connection = get_db()
    controller = ensure_controller(connection, controller_uid, "plate")
    if not controller["assignment_active"]:
        return {"error": "Controller gate or village is inactive."}, 403
    try:
        attempt_uid = request_attempt_uid()
    except ValueError as error:
        return {"error": str(error)}, 400
    vehicle = connection.execute(
        """
        SELECT id, owner_name FROM vehicles
        WHERE village_id = ? AND plate_number = ? AND is_active = 1
          AND (registration_expires_on IS NULL
               OR registration_expires_on >= CURRENT_DATE)
        LIMIT 1
        """,
        (controller["village_id"], plate),
    ).fetchone()
    rfid_vehicle = None
    if rfid_required and rfid_number:
        rfid_vehicle = connection.execute(
            """
            SELECT v.id, v.owner_name
            FROM rfid_stickers r
            JOIN vehicles v ON v.id = r.vehicle_id
            WHERE r.village_id = ? AND v.village_id = ?
              AND r.sticker_value = ? AND r.is_active = 1
              AND v.is_active = 1
              AND (v.registration_expires_on IS NULL
                   OR v.registration_expires_on >= CURRENT_DATE)
            LIMIT 1
            """,
            (controller["village_id"], controller["village_id"], rfid_number),
        ).fetchone()
    authorized, rfid_authorized, authorization_reason = authorize_plate_and_rfid(
        vehicle["id"] if vehicle else None,
        rfid_required,
        rfid_number,
        rfid_vehicle["id"] if rfid_vehicle else None,
    )
    # Plate and RFID are independent authorization credentials. Prefer the
    # plate-linked vehicle for display when both are valid; otherwise use RFID.
    authorized_vehicle = vehicle or rfid_vehicle
    decision = "authorized" if authorized else "denied"

    correlated = find_recent_access_event(
        connection, controller["village_id"], controller["gate_id"],
        controller_uid, attempt_uid, rfid_number,
    )
    duplicate = None if is_no_plate_capture else connection.execute(
        """
        SELECT id FROM access_events
        WHERE village_id = ? AND controller_uid = ?
          AND plate_number = ? AND COALESCE(rfid_number, '') = ?
          AND detected_at >= TIMESTAMPADD(
            SECOND,
            -CAST(COALESCE((SELECT `value` FROM settings
                WHERE `key` = 'duplicate_event_seconds'), '30') AS SIGNED),
            NOW()
        )
        ORDER BY detected_at DESC LIMIT 1
        """,
        (controller["village_id"], controller_uid, plate, rfid_number),
    ).fetchone()

    event = correlated or duplicate
    event_id = event["id"] if event else None
    if correlated is not None:
        connection.execute(
            """
            UPDATE access_events
            SET plate_number = ?, vehicle_id = COALESCE(?, vehicle_id),
                detector_confidence = ?, rfid_number = COALESCE(?, rfid_number),
                rfid_required = CASE WHEN ? = 1 THEN 1 ELSE rfid_required END,
                rfid_authorized = CASE WHEN ? = 1 THEN 1 ELSE rfid_authorized END,
                decision = CASE WHEN decision = 'authorized' OR ? = 1
                                THEN 'authorized' ELSE ? END,
                notes = CONCAT_WS('; ', notes, ?)
            WHERE id = ? AND village_id = ?
            """,
            (
                plate,
                authorized_vehicle["id"] if authorized_vehicle else None,
                detector_confidence,
                rfid_number or None,
                int(rfid_required),
                int(rfid_authorized),
                int(authorized),
                decision,
                authorization_reason,
                event_id,
                controller["village_id"],
            ),
        )
        if annotated_frame_bytes:
            event_directory = (
                f"captures/{controller['village_uid']}/{controller['gate_uid']}/"
                f"{datetime.now():%Y}/{datetime.now():%m}/{event_id}"
            )
            stored_path = store_event_image(event_directory, "annotated.jpg", annotated_frame_bytes)
            if stored_path:
                connection.execute(
                    """
                    INSERT INTO capture_images (
                        village_id, access_event_id, image_type, storage_path, sha256
                    ) VALUES (?, ?, 'annotated', ?, ?)
                    ON DUPLICATE KEY UPDATE storage_path = VALUES(storage_path), sha256 = VALUES(sha256)
                    """,
                    (
                        controller["village_id"], event_id, stored_path,
                        hashlib.sha256(annotated_frame_bytes).hexdigest(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE access_events SET annotated_image_path = ?
                    WHERE id = ? AND village_id = ?
                    """,
                    (stored_path, event_id, controller["village_id"]),
                )
    elif duplicate is None:
        cursor = connection.execute(
            """
            INSERT INTO access_events (
                village_id, gate_id, controller_uid, attempt_uid, vehicle_id,
                plate_number, rfid_number, rfid_required,
                rfid_authorized, decision, gate_action,
                detector_confidence, image_path, raw_image_path,
                annotated_image_path, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_requested', ?, NULL, NULL, NULL, ?)
            """,
            (
                controller["village_id"],
                controller["gate_id"],
                controller_uid,
                attempt_uid,
                authorized_vehicle["id"] if authorized_vehicle else None,
                plate,
                rfid_number or None,
                int(rfid_required),
                int(rfid_authorized),
                decision,
                detector_confidence,
                authorization_reason,
            ),
        )
        event_id = cursor.lastrowid
        now = datetime.now()
        event_directory = (
            f"captures/{controller['village_uid']}/{controller['gate_uid']}/"
            f"{now:%Y}/{now:%m}/{event_id}"
        )
        image_payloads = (
            ("crop", "crop.jpg", image_bytes, "image_path"),
            ("raw", "raw.jpg", raw_frame_bytes, "raw_image_path"),
            (
                "annotated", "annotated.jpg", annotated_frame_bytes,
                "annotated_image_path",
            ),
        )
        stored_paths: dict[str, str] = {}
        for image_type, filename, contents, event_column in image_payloads:
            stored_path = store_event_image(event_directory, filename, contents)
            if stored_path is None:
                continue
            stored_paths[event_column] = stored_path
            connection.execute(
                """
                INSERT INTO capture_images (
                    village_id, access_event_id, image_type, storage_path, sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    controller["village_id"],
                    event_id,
                    image_type,
                    stored_path,
                    hashlib.sha256(contents).hexdigest(),
                ),
            )
        if stored_paths:
            connection.execute(
                """
                UPDATE access_events
                SET image_path = ?, raw_image_path = ?, annotated_image_path = ?
                WHERE id = ? AND village_id = ?
                """,
                (
                    stored_paths.get("image_path"),
                    stored_paths.get("raw_image_path"),
                    stored_paths.get("annotated_image_path"),
                    event_id,
                    controller["village_id"],
                ),
            )

    connection.execute(
        """
        UPDATE controllers
        SET camera_state = 'remote', detector_state = 'idle', last_plate = ?,
            last_rfid = ?, camera_connected = 1,
            controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (plate, rfid_number or None, controller_uid),
    )
    try:
        command_id = int(request.form.get("command_id", "0"))
    except ValueError:
        command_id = 0
    if command_id > 0:
        connection.execute(
            """
            UPDATE reader_commands
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                result_message = ?
            WHERE id = ? AND controller_uid = ? AND status IN ('pending', 'active')
            """,
            (
                f"Recognized {plate}"
                + (f" / RFID {rfid_number}" if rfid_number else ""),
                command_id,
                controller_uid,
            ),
        )
    if attempt_uid:
        connection.execute(
            """
            UPDATE camera_capture_jobs
            SET status = 'recognized', completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                error_message = NULL
            WHERE attempt_uid = ? AND controller_uid = ?
              AND status IN ('pending', 'capturing', 'captured')
            """,
            (attempt_uid, controller_uid),
        )
    connection.commit()
    return {
        "accepted": True,
        "controller_id": controller_uid,
        "village_id": controller["village_uid"],
        "gate_id": controller["gate_uid"],
        "attempt_uid": attempt_uid,
        "authorized": authorized,
        "decision": decision,
        "duplicate": duplicate is not None,
        "event_id": event_id,
        "owner": authorized_vehicle["owner_name"] if authorized_vehicle else None,
        "plate": plate,
        "rfid": rfid_number or None,
        "rfid_required": rfid_required,
        "rfid_authorized": rfid_authorized,
        "authorization_reason": authorization_reason,
    }


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if admin_exists():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")
        if len(username) < 3:
            flash("Username must contain at least three characters.", "error")
        elif len(password) < 10:
            flash("Password must contain at least ten characters.", "error")
        elif password != confirmation:
            flash("The passwords do not match.", "error")
        else:
            connection = get_db()
            cursor = connection.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, 'system_owner')
                """,
                (username, generate_password_hash(password)),
            )
            connection.execute(
                """
                INSERT INTO audit_log (user_id, action, entity_type, entity_id, details)
                VALUES (?, 'create_admin', 'user', ?, 'Initial administrator created')
                """,
                (cursor.lastrowid, cursor.lastrowid),
            )
            connection.commit()
            flash("Administrator account created. You can now sign in.", "success")
            return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not admin_exists():
        return redirect(url_for("setup"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            """
            SELECT id, username, password_hash, role
            FROM users
            WHERE username = ? AND is_active = 1
            """,
            (username,),
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect username or password.", "error")
        else:
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            connection = get_db()
            connection.execute(
                "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user["id"],),
            )
            connection.execute(
                "INSERT INTO audit_log (user_id, action, details) VALUES (?, 'login', 'Administrator signed in')",
                (user["id"],),
            )
            connection.commit()
            return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    user_id = session.get("user_id")
    connection = get_db()
    connection.execute(
        "INSERT INTO audit_log (user_id, action, details) VALUES (?, 'logout', 'Administrator signed out')",
        (user_id,),
    )
    connection.commit()
    session.clear()
    return redirect(url_for("login"))


def load_dashboard_state() -> dict[str, Any]:
    connection = get_db()
    village_id = selected_village_id(connection)
    controller_uid = selected_controller_uid(connection)
    scoped_uid = controller_uid or "__no_controller__"
    if session.get("role") in {"administrator", "system_owner"}:
        network_summary = connection.execute(
            """
        SELECT
            (SELECT COUNT(*) FROM villages) AS villages,
            (SELECT COUNT(*) FROM gates) AS gates,
            (SELECT COUNT(*) FROM controllers c
             JOIN gates g ON g.id = c.gate_id
             JOIN villages v ON v.id = g.village_id) AS controllers,
            (SELECT COUNT(*) FROM controllers c
             LEFT JOIN gates g ON g.id = c.gate_id
             WHERE g.id IS NULL) AS unassigned_controllers,
            (SELECT COUNT(*) FROM controllers c
             JOIN gates g ON g.id = c.gate_id
             JOIN villages v ON v.id = g.village_id
             WHERE controller_seen_at IS NOT NULL
               AND c.controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP)
               AND c.is_active = 1 AND g.is_active = 1 AND v.is_active = 1) AS controllers_online,
            (SELECT COUNT(*) FROM controllers c JOIN gates g ON g.id = c.gate_id
             WHERE c.controller_type = 'plate') AS plate_controllers,
            (SELECT COUNT(*) FROM controllers c JOIN gates g ON g.id = c.gate_id
             WHERE c.controller_type = 'rfid') AS rfid_controllers
            """
        ).fetchone()
    else:
        network_summary = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM villages WHERE id = ?) AS villages,
                (SELECT COUNT(*) FROM gates WHERE village_id = ?) AS gates,
                (SELECT COUNT(*) FROM controllers c JOIN gates g ON g.id = c.gate_id
                 WHERE g.village_id = ?) AS controllers,
                0 AS unassigned_controllers,
                (SELECT COUNT(*) FROM controllers c JOIN gates g ON g.id = c.gate_id
                 WHERE g.village_id = ? AND c.controller_seen_at IS NOT NULL
                   AND c.controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP)
                   AND c.is_active = 1 AND g.is_active = 1) AS controllers_online,
                (SELECT COUNT(*) FROM controllers c JOIN gates g ON g.id = c.gate_id
                 WHERE g.village_id = ? AND c.controller_type = 'plate') AS plate_controllers,
                (SELECT COUNT(*) FROM controllers c JOIN gates g ON g.id = c.gate_id
                 WHERE g.village_id = ? AND c.controller_type = 'rfid') AS rfid_controllers
            """,
            (village_id or 0,) * 6,
        ).fetchone()
    expired = connection.execute(
        """
        UPDATE reader_commands
        SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
            result_message = 'Command timed out before completion'
        WHERE controller_uid = ? AND status IN ('pending', 'active')
          AND COALESCE(started_at, created_at) < TIMESTAMPADD(MINUTE, -2, CURRENT_TIMESTAMP)
        """,
        (scoped_uid,),
    )
    if expired.rowcount > 0:
        connection.execute(
            """
            UPDATE controllers
            SET detector_state = 'idle', updated_at = CURRENT_TIMESTAMP
            WHERE controller_uid = ?
            """,
            (scoped_uid,),
        )
        connection.commit()
    summary = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM vehicles WHERE village_id = ? AND is_active = 1) AS active_vehicles,
            (SELECT count(*) FROM access_events WHERE controller_uid = ? AND DATE(detected_at) = CURRENT_DATE) AS events_today,
            (SELECT count(*) FROM access_events WHERE controller_uid = ? AND decision = 'authorized' AND DATE(detected_at) = CURRENT_DATE) AS authorized_today,
            (SELECT count(*) FROM access_events WHERE controller_uid = ? AND decision = 'denied' AND DATE(detected_at) = CURRENT_DATE) AS denied_today
        """,
        (village_id or 0, scoped_uid, scoped_uid, scoped_uid),
    ).fetchone()
    recent_events = connection.execute(
        """
        SELECT e.id, e.plate_number, e.rfid_number, e.decision, e.gate_action,
               DATE_FORMAT(e.detected_at, '%Y-%m-%d %H:%i:%s') AS local_time,
               v.owner_name, v.vehicle_type, v.make, v.model
        FROM access_events e
        LEFT JOIN vehicles v ON v.id = e.vehicle_id
        WHERE e.controller_uid = ?
        ORDER BY e.detected_at DESC
        LIMIT 8
        """,
        (scoped_uid,),
    ).fetchall()
    latest_event = connection.execute(
        """
        SELECT e.id, e.plate_number, e.rfid_number, e.rfid_required,
               e.rfid_authorized, e.decision, e.image_path,
               e.raw_image_path, e.annotated_image_path,
               DATE_FORMAT(e.detected_at, '%Y-%m-%d %H:%i:%s') AS local_time,
               v.owner_name
        FROM access_events e
        LEFT JOIN vehicles v ON v.id = e.vehicle_id
        WHERE e.controller_uid = ?
        ORDER BY e.detected_at DESC
        LIMIT 1
        """,
        (scoped_uid,),
    ).fetchone()
    latest_has_image = bool(
        latest_event
        and (
            latest_event["image_path"]
            or latest_event["raw_image_path"]
            or latest_event["annotated_image_path"]
        )
    )
    latest_timing = connection.execute(
        """
        SELECT frames_ms, yolo_ms, ocr_ms, server_ms, total_ms
        FROM reader_commands
        WHERE controller_uid = ? AND total_ms IS NOT NULL
          AND result_message LIKE 'Recognized %'
        ORDER BY completed_at DESC, id DESC
        LIMIT 1
        """,
        (scoped_uid,),
    ).fetchone()
    if not latest_has_image:
        latest_timing = None
    daily = connection.execute(
        """
        SELECT DATE_FORMAT(event_date, '%Y-%m-%d') AS event_date,
               total_events, authorized_count, denied_count, gates_opened
        FROM (
            SELECT DATE(detected_at) AS event_date,
                   COUNT(*) AS total_events,
                   SUM(decision = 'authorized') AS authorized_count,
                   SUM(decision = 'denied') AS denied_count,
                   SUM(gate_action = 'opened') AS gates_opened
            FROM access_events
            WHERE controller_uid = ?
            GROUP BY DATE(detected_at)
        ) AS controller_days
        ORDER BY event_date DESC
        LIMIT 7
        """,
        (scoped_uid,),
    ).fetchall()
    system = connection.execute(
        """
        SELECT controllers.controller_uid, controllers.display_name, controller_type,
               camera_state, detector_state, gate_state,
               camera_connected, rfid_connected, loop_active, ir_blocked,
               barrier_open, traffic_green, plate_unrecognized,
               (controller_seen_at IS NOT NULL AND
                controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP))
                    AS controller_online,
               last_plate, last_rfid,
               DATE_FORMAT(controller_seen_at, '%Y-%m-%d %H:%i:%s') AS controller_seen_at,
               DATE_FORMAT(last_heartbeat, '%Y-%m-%d %H:%i:%s') AS last_heartbeat,
               DATE_FORMAT(controllers.updated_at, '%Y-%m-%d %H:%i:%s') AS updated_at,
               cameras.camera_uid AS camera_uid,
               cameras.transport AS camera_transport,
               cameras.status AS camera_status,
               cameras.endpoint_url IS NOT NULL AS camera_configured,
               DATE_FORMAT(cameras.last_seen_at, '%Y-%m-%d %H:%i:%s') AS camera_last_seen_at,
               cameras.last_error AS camera_last_error
        FROM controllers
        LEFT JOIN cameras ON cameras.gate_id = controllers.gate_id
            AND cameras.is_active = 1
        WHERE controllers.controller_uid = ?
        """,
        (scoped_uid,),
    ).fetchone()
    if system is not None:
        system["camera_configured"] = camera_is_configured(
            system["camera_uid"], system["camera_endpoint_url"]
        )
    if system is None:
        camera = connection.execute(
            """
            SELECT cameras.camera_uid, cameras.transport, cameras.status,
                   cameras.endpoint_url IS NOT NULL AS camera_configured,
                   DATE_FORMAT(cameras.last_seen_at, '%Y-%m-%d %H:%i:%s') AS camera_last_seen_at,
                   cameras.last_error AS camera_last_error
            FROM cameras
            JOIN gates ON gates.id = cameras.gate_id
            WHERE cameras.is_active = 1 AND gates.is_active = 1
              AND gates.village_id = ?
            ORDER BY cameras.gate_id
            LIMIT 1
            """,
            (village_id or 0,),
        ).fetchone()
        system = {
            "controller_uid": None,
            "display_name": "No controller connected",
            "controller_type": "plate",
            "camera_state": "unavailable",
            "detector_state": "idle",
            "gate_state": "offline",
            "camera_connected": 0,
            "rfid_connected": 0,
            "loop_active": 0,
            "ir_blocked": 0,
            "barrier_open": 0,
            "traffic_green": 0,
            "plate_unrecognized": 0,
            "controller_online": 0,
            "last_plate": None,
            "last_rfid": None,
            "controller_seen_at": None,
            "last_heartbeat": None,
            "updated_at": None,
            "camera_uid": camera["camera_uid"] if camera else None,
            "camera_transport": camera["transport"] if camera else None,
            "camera_status": camera["status"] if camera else "unknown",
            "camera_configured": camera["camera_configured"] if camera else 0,
            "camera_last_seen_at": camera["camera_last_seen_at"] if camera else None,
            "camera_last_error": camera["camera_last_error"] if camera else None,
        }
    return {
        "network_summary": network_summary,
        "summary": summary,
        "recent_events": recent_events,
        "latest_event": latest_event,
        "latest_has_image": latest_has_image,
        "latest_timing": latest_timing,
        "daily": daily,
        "system": system,
        "latest_capture_version": latest_event["id"] if latest_event else None,
        "camera_test_uid": session.get("camera_test_uid"),
        "camera_test_version": session.get("camera_test_version"),
    }


@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", **load_dashboard_state())


@app.route("/hardware")
@login_required
def hardware():
    return render_template("hardware.html", **load_dashboard_state())


@app.route("/api/dashboard")
@login_required
def dashboard_sync():
    state = load_dashboard_state()
    summary = dict(state["summary"])
    latest = dict(state["latest_event"]) if state["latest_event"] else None
    if latest is not None:
        latest["has_image"] = state["latest_has_image"]
        if latest["has_image"]:
            latest["frame_urls"] = {
                "raw": url_for("event_frame", event_id=latest["id"], variant="raw"),
                "annotated": url_for(
                    "event_frame", event_id=latest["id"], variant="annotated"
                ),
            }
            latest["image_url"] = latest["frame_urls"]["annotated"]
        latest["image_version"] = latest["id"]
    recent = []
    for row in state["recent_events"]:
        event = dict(row)
        event["vehicle"] = " ".join(
            value for value in (event["make"], event["model"]) if value
        ) or event["vehicle_type"] or "—"
        recent.append(event)
    system = dict(state["system"])
    payload = {
        "network": dict(state["network_summary"]),
        "summary": summary,
        "latest_event": latest,
        "latest_timing": dict(state["latest_timing"]) if state["latest_timing"] else None,
        "recent_events": recent,
        "daily": [dict(row) for row in state["daily"]],
        "system": {
            "controller_id": system["controller_uid"],
            "controller_name": system["display_name"],
            "controller_type": system["controller_type"],
            "controller_online": bool(system["controller_online"]),
            "camera_running": system["camera_status"] == "online",
            "camera_status": system["camera_status"],
            "camera_configured": bool(system["camera_configured"]),
            "camera_last_seen_at": system["camera_last_seen_at"],
            "camera_last_error": system["camera_last_error"],
            "rfid_connected": bool(system["controller_online"] and system["rfid_connected"]),
            "camera_state": system["camera_state"],
            "detector_state": system["detector_state"],
            "gate_state": system["gate_state"],
            "loop_active": bool(system["loop_active"]),
            "ir_blocked": bool(system["ir_blocked"]),
            "barrier_open": bool(system["barrier_open"]),
            "traffic_green": bool(system["traffic_green"]),
            "plate_unrecognized": bool(system["plate_unrecognized"]),
            "last_plate": system["last_plate"],
            "last_rfid": system["last_rfid"],
            "controller_seen_at": system["controller_seen_at"],
            "last_heartbeat": system["last_heartbeat"],
        },
    }
    return payload, 200, {"Cache-Control": "no-store"}


@app.post("/camera/capture")
@tenant_role_required("village_admin", "security_guard")
def camera_capture():
    connection = get_db()
    controller_uid = selected_controller_uid(connection)
    village_id = selected_village_id(connection)
    camera = connection.execute(
        """
        SELECT cameras.camera_uid, cameras.transport, cameras.endpoint_url
        FROM cameras
        JOIN gates ON gates.id = cameras.gate_id
        WHERE cameras.is_active = 1 AND gates.is_active = 1
          AND gates.village_id = ?
          AND (? IS NULL OR cameras.gate_id = (
              SELECT gate_id FROM controllers WHERE controller_uid = ?
          ))
        ORDER BY cameras.gate_id
        LIMIT 1
        """,
        (village_id or 0, controller_uid, controller_uid),
    ).fetchone()
    if camera is None:
        return {"success": False, "message": "Bind a camera to a gate before capturing a frame."}, 409
    endpoint_url = camera["endpoint_url"] or os.environ.get(
        camera_endpoint_env_name(camera["camera_uid"]), ""
    ).strip() or None
    try:
        frame = capture_frame(
            CameraConfig(camera["camera_uid"], camera["transport"], endpoint_url),
            timeout_seconds=10,
        )
        stored_path = store_event_image(
            "camera-tests", f"{camera['camera_uid']}.jpg", frame
        )
        if stored_path is None:
            raise CameraError("The captured frame could not be stored.")
        connection.execute(
            """
            UPDATE cameras
            SET status = 'online', last_seen_at = CURRENT_TIMESTAMP, last_error = NULL
            WHERE camera_uid = ?
            """,
            (camera["camera_uid"],),
        )
        connection.commit()
        session["camera_test_uid"] = camera["camera_uid"]
        session["camera_test_version"] = int(datetime.now().timestamp())
        success = True
        message = "Camera frame captured successfully."
    except (CameraError, ValueError) as error:
        connection.execute(
            """
            UPDATE cameras
            SET status = 'degraded', last_seen_at = CURRENT_TIMESTAMP, last_error = ?
            WHERE camera_uid = ?
            """,
            (str(error)[:500], camera["camera_uid"]),
        )
        connection.commit()
        success = False
        message = f"Camera capture failed: {error}"
    if request.accept_mimetypes.best == "application/json":
        return {
            "success": success,
            "message": message,
            "camera_id": camera["camera_uid"],
        }, 200 if success else 502
    flash(message, "success" if success else "error")
    return redirect(url_for("dashboard"))


@app.post("/hardware/command")
@tenant_role_required("village_admin", "security_guard")
def hardware_command():
    command = request.form.get("command", "").strip().lower()
    labels = {
        "barrier_open": "Boom-barrier OPEN test",
        "barrier_close": "Boom-barrier CLOSE test",
        "traffic_red": "Traffic RED test",
        "traffic_green": "Traffic GREEN test",
    }
    if command not in labels:
        return {"success": False, "message": "Unsupported hardware command."}, 400
    connection = get_db()
    controller_uid = selected_controller_uid(connection)
    controller = connection.execute(
        """
        SELECT controller_type, gate_state, controller_seen_at IS NOT NULL AND
               controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP)
                   AS online
        FROM controllers WHERE controller_uid = ?
        """,
        (controller_uid or "",),
    ).fetchone()
    if controller is None or not controller["online"]:
        return {
            "success": False,
            "message": "The gate equipment is offline. No hardware command was queued.",
        }, 409
    if controller["controller_type"] != "plate":
        return {
            "success": False,
            "message": "Manual relay commands are unavailable for an RFID-only controller.",
        }, 409
    if controller["gate_state"] == "disabled":
        return {
            "success": False,
            "message": "Automatic GPIO gate mode is disabled on the controller.",
        }, 409
    connection.execute(
        """
        UPDATE reader_commands
        SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
            result_message = 'Command timed out before completion'
        WHERE controller_uid = ? AND status IN ('pending', 'active')
          AND COALESCE(started_at, created_at) < TIMESTAMPADD(MINUTE, -2, CURRENT_TIMESTAMP)
        """,
        (controller_uid,),
    )
    existing = connection.execute(
        """
        SELECT id FROM reader_commands
        WHERE controller_uid = ? AND status IN ('pending', 'active')
        ORDER BY created_at LIMIT 1
        """,
        (controller_uid,),
    ).fetchone()
    if existing is not None:
        connection.commit()
        return {
            "success": False,
            "message": "Another controller command is already queued or running.",
        }, 409
    cursor = connection.execute(
        """
        INSERT INTO reader_commands (
            village_id, gate_id, controller_uid, command_type, status, requested_by
        )
        SELECT g.village_id, c.gate_id, c.controller_uid, ?, 'pending', ?
        FROM controllers c JOIN gates g ON g.id = c.gate_id
        WHERE c.controller_uid = ?
        """,
        (command, session["user_id"], controller_uid),
    )
    record_audit(
        "queue_hardware_command",
        "reader_command",
        cursor.lastrowid,
        labels[command],
    )
    connection.commit()
    return {
        "success": True,
        "message": f"{labels[command]} sent to the selected controller.",
    }, 202


@app.post("/hardware/serial")
@tenant_role_required("village_admin", "security_guard")
def hardware_serial():
    mode = request.form.get("mode", "hex").strip().lower()
    command_text = request.form.get("command", "")
    if mode == "hex":
        compact = re.sub(r"0x", "", command_text, flags=re.IGNORECASE)
        compact = re.sub(r"[\s,;:<>{}\[\]()-]+", "", compact)
        if not compact or len(compact) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", compact):
            return {
                "success": False,
                "message": "HEX commands must contain complete byte pairs such as 05 00 01 FE 5F 6A.",
            }, 400
        tx_hex = compact.upper()
    elif mode == "text":
        if not command_text:
            return {"success": False, "message": "Enter a text command."}, 400
        tx_hex = command_text.encode("utf-8").hex().upper()
    else:
        return {"success": False, "message": "Invalid transmit mode."}, 400
    if len(tx_hex) > 1024:
        return {"success": False, "message": "Serial commands are limited to 512 bytes."}, 400
    try:
        baud = int(request.form.get("baud", "9600"))
        data_bits = int(request.form.get("data_bits", "8"))
        stop_bits = int(request.form.get("stop_bits", "1"))
        timeout_ms = int(request.form.get("timeout_ms", "2000"))
    except ValueError:
        return {"success": False, "message": "Invalid numeric serial setting."}, 400
    parity = request.form.get("parity", "N").strip().upper()
    if baud not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
        return {"success": False, "message": "Unsupported baud rate."}, 400
    if data_bits not in {5, 6, 7, 8} or parity not in {"N", "E", "O"} or stop_bits not in {1, 2}:
        return {"success": False, "message": "Invalid data bits, parity, or stop bits."}, 400
    if timeout_ms < 50 or timeout_ms > 10000:
        return {"success": False, "message": "Read timeout must be from 50 to 10000 ms."}, 400

    connection = get_db()
    controller_uid = selected_controller_uid(connection)
    controller = connection.execute(
        """
        SELECT controller_type, gate_state, controller_seen_at IS NOT NULL AND
               controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP)
                   AS online
        FROM controllers WHERE controller_uid = ?
        """,
        (controller_uid or "",),
    ).fetchone()
    if controller is None or not controller["online"]:
        return {"success": False, "message": "The gate equipment is offline."}, 409
    if controller["controller_type"] != "plate":
        return {"success": False, "message": "The serial console requires a Plate + RFID controller."}, 409
    if controller["gate_state"] == "disabled":
        return {"success": False, "message": "GPIO gate mode is disabled on the controller."}, 409
    existing = connection.execute(
        """
        SELECT id FROM reader_commands
        WHERE controller_uid = ? AND status IN ('pending', 'active')
        ORDER BY created_at LIMIT 1
        """,
        (controller_uid,),
    ).fetchone()
    if existing is not None:
        return {"success": False, "message": "Another controller command is already running."}, 409
    cursor = connection.execute(
        """
        INSERT INTO reader_commands (
            village_id, gate_id, controller_uid, command_type, status, requested_by,
            serial_tx_hex, serial_baud,
            serial_data_bits, serial_parity, serial_stop_bits, serial_timeout_ms
        )
        SELECT g.village_id, c.gate_id, c.controller_uid, 'rfid_serial', 'pending',
               ?, ?, ?, ?, ?, ?, ?
        FROM controllers c JOIN gates g ON g.id = c.gate_id
        WHERE c.controller_uid = ?
        """,
        (
            session["user_id"], tx_hex, baud, data_bits,
            parity, stop_bits, timeout_ms, controller_uid,
        ),
    )
    record_audit(
        "queue_rfid_serial",
        "reader_command",
        cursor.lastrowid,
        f"RFID serial debug {baud} {data_bits}{parity}{stop_bits}; TX {tx_hex}",
    )
    connection.commit()
    return {
        "success": True,
        "message": "Serial command queued for the selected controller.",
        "command_id": cursor.lastrowid,
        "tx_hex": tx_hex,
    }, 202


@app.get("/api/hardware/commands/<int:command_id>")
@tenant_role_required("village_admin", "security_guard")
def hardware_command_result(command_id: int):
    controller_uid = selected_controller_uid(get_db())
    command = get_db().execute(
        """
        SELECT id, command_type, status, result_message, response_data,
               DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') AS created_at,
               DATE_FORMAT(completed_at, '%Y-%m-%d %H:%i:%s') AS completed_at
        FROM reader_commands WHERE id = ? AND requested_by = ? AND controller_uid = ?
        """,
        (command_id, session["user_id"], controller_uid),
    ).fetchone()
    if command is None:
        return {"error": "Command not found."}, 404
    return dict(command), 200, {"Cache-Control": "no-store"}


@app.route("/vehicles")
@tenant_role_required("village_admin")
def vehicles():
    connection = get_db()
    village_id = require_selected_village(connection)
    query = request.args.get("q", "").strip()
    if query:
        wildcard = f"%{query}%"
        normalized_plate_query = normalize_plate(query)
        plate_wildcard = (
            f"%{normalized_plate_query}%"
            if normalized_plate_query
            else "__NO_PLATE_MATCH__"
        )
        records = connection.execute(
            """
            SELECT v.*,
                   (SELECT r.sticker_value FROM rfid_stickers r
                    WHERE r.vehicle_id = v.id AND r.is_active = 1
                    ORDER BY r.id LIMIT 1) AS rfid_number
            FROM vehicles v
            WHERE v.village_id = ? AND (
               v.plate_number LIKE ? OR v.owner_name LIKE ?
               OR v.make LIKE ? OR v.model LIKE ?
               OR EXISTS (
                   SELECT 1 FROM rfid_stickers r
                   WHERE r.village_id = ? AND r.vehicle_id = v.id AND r.sticker_value LIKE ?
               ))
            ORDER BY v.is_active DESC, v.plate_number
            """,
            (village_id, plate_wildcard, wildcard, wildcard, wildcard, village_id, wildcard),
        ).fetchall()
    else:
        records = connection.execute(
            """
            SELECT v.*,
                   (SELECT r.sticker_value FROM rfid_stickers r
                    WHERE r.vehicle_id = v.id AND r.is_active = 1
                    ORDER BY r.id LIMIT 1) AS rfid_number
            FROM vehicles v
            WHERE v.village_id = ?
            ORDER BY v.is_active DESC, v.plate_number
            """,
            (village_id,),
        ).fetchall()
    return render_template("vehicles.html", vehicles=records, query=query)


def vehicle_form_values() -> dict[str, str]:
    fields = (
        "owner_name",
        "vehicle_type",
        "make",
        "model",
        "color",
        "contact_number",
        "email",
        "registration_expires_on",
        "photo_path",
        "notes",
        "rfid_number",
    )
    values = {field: request.form.get(field, "").strip() for field in fields}
    values["plate_number"] = normalize_plate(request.form.get("plate_number", ""))
    values["rfid_number"] = normalize_rfid(values["rfid_number"])
    return values


@app.route("/vehicles/new", methods=["GET", "POST"])
@tenant_role_required("village_admin")
def vehicle_new():
    values: dict[str, Any] = {}
    prefilled = False
    if request.method == "GET":
        plate_number = normalize_plate(request.args.get("plate_number", ""))
        if plate_number in {"RFID", "UNREADABLE"}:
            plate_number = ""
        rfid_number = normalize_rfid(request.args.get("rfid_number", ""))
        values = {
            "plate_number": plate_number,
            "rfid_number": rfid_number,
        }
        prefilled = bool(plate_number or rfid_number)
    if request.method == "POST":
        values = vehicle_form_values()
        if not values["plate_number"] or not values["owner_name"]:
            flash("Plate number and owner name are required.", "error")
        elif values["rfid_number"] and not 4 <= len(values["rfid_number"]) <= 64:
            flash("RFID sticker value must contain 4 to 64 letters or digits.", "error")
        else:
            try:
                connection = get_db()
                village_id = require_selected_village(connection)
                cursor = connection.execute(
                    """
                    INSERT INTO vehicles (
                        village_id, plate_number, owner_name, vehicle_type, make, model, color,
                        contact_number, email, registration_expires_on, photo_path, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (village_id,) + tuple(values[key] or None for key in (
                        "plate_number", "owner_name", "vehicle_type", "make", "model", "color",
                        "contact_number", "email", "registration_expires_on", "photo_path", "notes"
                    )),
                )
                if values["rfid_number"]:
                    connection.execute(
                        "INSERT INTO rfid_stickers (village_id, vehicle_id, sticker_value) VALUES (?, ?, ?)",
                        (village_id, cursor.lastrowid, values["rfid_number"]),
                    )
                record_audit("create_vehicle", "vehicle", cursor.lastrowid, values["plate_number"])
                connection.commit()
                flash(f'{values["plate_number"]} was registered successfully.', "success")
                return redirect(url_for("vehicles"))
            except IntegrityError:
                flash("That plate or RFID sticker is already registered or is invalid.", "error")
    return render_template(
        "vehicle_form.html", vehicle=values, editing=False, prefilled=prefilled
    )


@app.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
@tenant_role_required("village_admin")
def vehicle_edit(vehicle_id: int):
    connection = get_db()
    village_id = require_selected_village(connection)
    existing = connection.execute(
        """
        SELECT v.*,
               (SELECT r.sticker_value FROM rfid_stickers r
                WHERE r.vehicle_id = v.id AND r.is_active = 1
                ORDER BY r.id LIMIT 1) AS rfid_number
        FROM vehicles v WHERE v.id = ? AND v.village_id = ?
        """,
        (vehicle_id, village_id),
    ).fetchone()
    if existing is None:
        abort(404)
    values: dict[str, Any] = dict(existing)
    if request.method == "POST":
        values = vehicle_form_values()
        if not values["plate_number"] or not values["owner_name"]:
            flash("Plate number and owner name are required.", "error")
            return render_template("vehicle_form.html", vehicle=values, editing=True)
        if values["rfid_number"] and not 4 <= len(values["rfid_number"]) <= 64:
            flash("RFID sticker value must contain 4 to 64 letters or digits.", "error")
            return render_template("vehicle_form.html", vehicle=values, editing=True)
        try:
            connection.execute(
                """
                UPDATE vehicles SET
                    plate_number = ?, owner_name = ?, vehicle_type = ?, make = ?,
                    model = ?, color = ?, contact_number = ?, email = ?,
                    registration_expires_on = ?, photo_path = ?, notes = ?
                WHERE id = ? AND village_id = ?
                """,
                tuple(values[key] or None for key in (
                    "plate_number", "owner_name", "vehicle_type", "make", "model", "color",
                    "contact_number", "email", "registration_expires_on", "photo_path", "notes"
                )) + (vehicle_id, village_id),
            )
            connection.execute("DELETE FROM rfid_stickers WHERE vehicle_id = ? AND village_id = ?", (vehicle_id, village_id))
            if values["rfid_number"]:
                connection.execute(
                    "INSERT INTO rfid_stickers (village_id, vehicle_id, sticker_value) VALUES (?, ?, ?)",
                    (village_id, vehicle_id, values["rfid_number"]),
                )
            record_audit("update_vehicle", "vehicle", vehicle_id, values["plate_number"])
            connection.commit()
            flash("Vehicle details updated.", "success")
            return redirect(url_for("vehicles"))
        except IntegrityError:
            flash("That plate or RFID sticker is already registered or is invalid.", "error")
    return render_template("vehicle_form.html", vehicle=values, editing=True)


@app.post("/vehicles/<int:vehicle_id>/toggle")
@tenant_role_required("village_admin")
def vehicle_toggle(vehicle_id: int):
    connection = get_db()
    village_id = require_selected_village(connection)
    vehicle = connection.execute(
        "SELECT plate_number, is_active FROM vehicles WHERE id = ? AND village_id = ?", (vehicle_id, village_id)
    ).fetchone()
    if vehicle is None:
        abort(404)
    new_state = 0 if vehicle["is_active"] else 1
    connection.execute("UPDATE vehicles SET is_active = ? WHERE id = ? AND village_id = ?", (new_state, vehicle_id, village_id))
    record_audit(
        "activate_vehicle" if new_state else "deactivate_vehicle",
        "vehicle",
        vehicle_id,
        vehicle["plate_number"],
    )
    connection.commit()
    flash(f'{vehicle["plate_number"]} is now {"active" if new_state else "inactive"}.', "success")
    return redirect(url_for("vehicles", q=request.form.get("return_query", "").strip()))


@app.post("/vehicles/<int:vehicle_id>/delete")
@tenant_role_required("village_admin")
def vehicle_delete(vehicle_id: int):
    connection = get_db()
    village_id = require_selected_village(connection)
    vehicle = connection.execute(
        "SELECT plate_number, owner_name FROM vehicles WHERE id = ? AND village_id = ?", (vehicle_id, village_id)
    ).fetchone()
    if vehicle is None:
        abort(404)
    record_audit(
        "delete_vehicle",
        "vehicle",
        vehicle_id,
        f'{vehicle["plate_number"]} / {vehicle["owner_name"]}',
    )
    connection.execute("DELETE FROM vehicles WHERE id = ? AND village_id = ?", (vehicle_id, village_id))
    connection.commit()
    flash(f'{vehicle["plate_number"]} was permanently deleted.', "success")
    return redirect(url_for("vehicles", q=request.form.get("return_query", "").strip()))


@app.route("/users")
@tenant_role_required("village_admin")
def users():
    connection = get_db()
    village_id = require_selected_village(connection)
    guard_users = connection.execute(
        """
        SELECT u.id, u.username, u.is_active, u.created_at, u.last_login_at,
               v.name AS village_name, uvr.role AS village_role
        FROM users u
        JOIN user_village_roles uvr ON uvr.user_id = u.id
        JOIN villages v ON v.id = uvr.village_id
        WHERE uvr.village_id = ?
        ORDER BY u.is_active DESC, u.username
        """,
        (village_id,),
    ).fetchall()
    return render_template("users.html", users=guard_users)


@app.route("/users/new", methods=["GET", "POST"])
@tenant_role_required("village_admin")
def user_new():
    username = ""
    village_role = "homeowner"
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        village_role = request.form.get("village_role", "homeowner").strip()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")
        if len(username) < 3:
            flash("Username must contain at least three characters.", "error")
        elif len(password) < 10:
            flash("Password must contain at least ten characters.", "error")
        elif password != confirmation:
            flash("The passwords do not match.", "error")
        elif village_role not in {"village_admin", "security_guard", "homeowner"}:
            flash("Select a valid village role.", "error")
        else:
            try:
                connection = get_db()
                cursor = connection.execute(
                    """
                    INSERT INTO users (username, password_hash, role)
                    VALUES (?, ?, ?)
                    """,
                    (
                        username,
                        generate_password_hash(password),
                        "village_user",
                    ),
                )
                village_id = require_selected_village(connection)
                connection.execute(
                    """
                    INSERT INTO user_village_roles (user_id, village_id, role)
                    VALUES (?, ?, ?)
                    """,
                    (cursor.lastrowid, village_id, village_role),
                )
                record_audit(
                    "create_village_user", "user", cursor.lastrowid,
                    f"{username} / {village_role}", village_id=village_id,
                )
                connection.commit()
                flash(f"Village account {username} was created.", "success")
                return redirect(url_for("users"))
            except IntegrityError:
                flash("That username is already in use.", "error")
    return render_template(
        "user_form.html", username=username, village_role=village_role
    )


@app.post("/users/<int:user_id>/toggle")
@tenant_role_required("village_admin")
def user_toggle(user_id: int):
    connection = get_db()
    village_id = require_selected_village(connection)
    guard = connection.execute(
        """
        SELECT u.username, u.is_active FROM users u
        JOIN user_village_roles uvr ON uvr.user_id = u.id
        WHERE u.id = ? AND uvr.village_id = ?
          AND u.role != 'system_owner'
        """,
        (user_id, village_id),
    ).fetchone()
    if guard is None:
        abort(404)
    new_state = 0 if guard["is_active"] else 1
    connection.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, user_id))
    record_audit(
        "activate_guard" if new_state else "deactivate_guard",
        "user",
        user_id,
        guard["username"],
    )
    connection.commit()
    flash(
        f'{guard["username"]} is now {"active" if new_state else "inactive"}.',
        "success",
    )
    return redirect(url_for("users"))


def log_filters() -> tuple[list[str], list[Any]]:
    controller_uid = selected_controller_uid(get_db())
    clauses: list[str] = ["e.controller_uid = ?"]
    parameters: list[Any] = [controller_uid or "__no_controller__"]
    plate = normalize_plate(request.args.get("plate", ""))
    decision = request.args.get("decision", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    if plate:
        clauses.append("e.plate_number LIKE ?")
        parameters.append(f"%{plate}%")
    if decision in {"authorized", "denied", "unreadable", "manual"}:
        clauses.append("e.decision = ?")
        parameters.append(decision)
    if date_from:
        clauses.append("DATE(e.detected_at) >= ?")
        parameters.append(date_from)
    if date_to:
        clauses.append("DATE(e.detected_at) <= ?")
        parameters.append(date_to)
    return clauses, parameters


@app.route("/logs")
@login_required
def logs():
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 50
    clauses, parameters = log_filters()
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = get_db()
    total = connection.execute(
        f"SELECT count(*) AS total FROM access_events e {where}", parameters
    ).fetchone()["total"]
    records = connection.execute(
        f"""
        SELECT e.*, DATE_FORMAT(e.detected_at, '%Y-%m-%d %H:%i:%s') AS local_time,
               v.owner_name, v.vehicle_type, v.make, v.model, v.color
        FROM access_events e
        LEFT JOIN vehicles v ON v.id = e.vehicle_id
        {where}
        ORDER BY e.detected_at DESC
        LIMIT ? OFFSET ?
        """,
        parameters + [per_page, (page - 1) * per_page],
    ).fetchall()
    return render_template(
        "logs.html",
        events=records,
        page=page,
        pages=max(1, math.ceil(total / per_page)),
        total=total,
        filters=request.args,
    )


@app.route("/logs/export.csv")
@tenant_role_required("village_admin")
def logs_export():
    clauses, parameters = log_filters()
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    records = get_db().execute(
        f"""
        SELECT DATE_FORMAT(e.detected_at, '%Y-%m-%d %H:%i:%s') AS local_time,
               e.plate_number, coalesce(e.rfid_number, '') AS rfid_number,
               coalesce(v.owner_name, 'Unknown') AS owner_name,
               coalesce(v.vehicle_type, '') AS vehicle_type,
               coalesce(v.make, '') AS make, coalesce(v.model, '') AS model,
               e.direction, e.decision, e.gate_action,
               e.detector_confidence, e.ocr_confidence, coalesce(e.image_path, '') AS image_path
        FROM access_events e
        LEFT JOIN vehicles v ON v.id = e.vehicle_id
        {where}
        ORDER BY e.detected_at DESC
        """,
        parameters,
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp", "Plate", "RFID", "Owner", "Vehicle type", "Make", "Model",
        "Direction", "Decision", "Gate action", "Detector confidence",
        "OCR confidence", "Image path",
    ])
    writer.writerows(tuple(row.values()) for row in records)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=access-log.csv"},
    )


@app.route("/events/<int:event_id>/image")
@login_required
def event_image(event_id: int):
    connection = get_db()
    village_id = require_selected_village(connection)
    event = connection.execute(
        "SELECT image_path FROM access_events WHERE id = ? AND village_id = ?",
        (event_id, village_id),
    ).fetchone()
    if event is None or not event["image_path"]:
        abort(404)
    image_path = Path(event["image_path"])
    if not image_path.is_absolute():
        image_path = PROJECT_DIR / image_path
    image_path = image_path.resolve()
    try:
        image_path.relative_to(PROJECT_DIR.resolve())
    except ValueError:
        abort(403)
    if not image_path.is_file():
        abort(404)
    return send_file(image_path)


@app.route("/events/<int:event_id>/frame/<variant>")
@login_required
def event_frame(event_id: int, variant: str):
    if variant not in {"raw", "annotated"}:
        abort(404)
    connection = get_db()
    village_id = require_selected_village(connection)
    event = connection.execute(
        """
        SELECT image_path, raw_image_path, annotated_image_path
        FROM access_events WHERE id = ? AND village_id = ?
        """,
        (event_id, village_id),
    ).fetchone()
    if event is None:
        abort(404)
    selected_path = event[f"{variant}_image_path"] or event["image_path"]
    if not selected_path:
        abort(404)
    image_path = Path(selected_path)
    if not image_path.is_absolute():
        image_path = PROJECT_DIR / image_path
    image_path = image_path.resolve()
    try:
        image_path.relative_to(PROJECT_DIR.resolve())
    except ValueError:
        abort(403)
    if not image_path.is_file():
        abort(404)
    response = send_file(image_path, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/latest-plate-crop.jpg")
@login_required
def latest_capture_image():
    connection = get_db()
    village_id = require_selected_village(connection)
    event = connection.execute(
        """
        SELECT image_path FROM access_events
        WHERE village_id = ? AND image_path IS NOT NULL
        ORDER BY detected_at DESC, id DESC LIMIT 1
        """,
        (village_id,),
    ).fetchone()
    if event is None:
        abort(404)
    image_path = (PROJECT_DIR / event["image_path"]).resolve()
    try:
        image_path.relative_to(PROJECT_DIR.resolve())
    except ValueError:
        abort(403)
    if not image_path.is_file():
        abort(404)
    response = send_file(image_path, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/health")
def health():
    get_db().execute("SELECT 1").fetchone()
    return {"status": "ok", "service": "plate-program"}


@app.get("/api/account-sync/v1/capabilities")
def account_sync_capabilities():
    enabled = environment_flag("MOBILE_ACCOUNT_INTEGRATION_ENABLED")
    service_url = os.environ.get("MOBILE_ACCOUNT_SERVICE_URL", "").strip()
    secret_configured = bool(
        os.environ.get("MOBILE_ACCOUNT_SYNC_SECRET", "").strip()
    )
    return {
        "service": "plate-program",
        "account_sync_api": "v1",
        "schema_ready": True,
        "enabled": enabled,
        "configured": bool(service_url and secret_configured),
        "multi_tenant": True,
        "authorization_enforcement": False,
        "state": "disabled" if not enabled else "prepared",
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)

# --- MOBILE API ENDPOINTS ---

@app.post("/api/auth/login")
def api_login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    
    connection = get_db()
    user = connection.execute(
        "SELECT id, username, password_hash, role FROM users WHERE username = ? AND is_active = 1",
        (username,),
    ).fetchone()
    
    if user and check_password_hash(user["password_hash"], password):
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        return {"success": True, "role": user["role"]}
        
    return {"success": False, "message": "Invalid credentials"}, 401

@app.get("/api/dashboard")
@login_required
def api_dashboard():
    connection = get_db()
    village_id = selected_village_id(connection)
    
    if not village_id:
        return {"total_events": 0, "authorized_count": 0, "denied_count": 0}
        
    summary = connection.execute(
        """
        SELECT 
            COUNT(*) as total_events,
            SUM(decision = 'authorized') as authorized_count,
            SUM(decision = 'denied') as denied_count
        FROM access_events 
        WHERE village_id = ? AND DATE(detected_at) = CURRENT_DATE
        """,
        (village_id,)
    ).fetchone()
    
    return {
        "total_events": summary["total_events"] or 0,
        "authorized_count": summary["authorized_count"] or 0,
        "denied_count": summary["denied_count"] or 0,
    }

@app.get("/api/gates")
@login_required
def api_gates():
    connection = get_db()
    village_id = selected_village_id(connection)
    
    if not village_id:
        return []
        
    gates = connection.execute(
        """
        SELECT g.id, g.name, v.name as village_name, g.is_active
        FROM gates g
        JOIN villages v ON v.id = g.village_id
        WHERE g.village_id = ?
        """,
        (village_id,)
    ).fetchall()
    
    return gates

@app.get("/api/logs")
@login_required
def api_logs():
    connection = get_db()
    village_id = selected_village_id(connection)
    
    if not village_id:
        return []
        
    logs = connection.execute(
        """
        SELECT id, plate_number, rfid_number, decision, detected_at
        FROM access_events
        WHERE village_id = ?
        ORDER BY detected_at DESC
        LIMIT 50
        """,
        (village_id,)
    ).fetchall()
    
    # Format datetime objects to strings
    for log in logs:
        if isinstance(log['detected_at'], datetime):
            log['detected_at'] = log['detected_at'].isoformat()
            
    return logs



from mobile_routes import mobile_api
app.register_blueprint(mobile_api)
