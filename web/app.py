from __future__ import annotations

import csv
import io
import math
import os
import re
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor
from pymysql.err import IntegrityError

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


PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")
DATABASE_DIR = PROJECT_DIR / "database"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
SECRET_PATH = DATABASE_DIR / "web_secret.key"
OUTPUT_DIR = PROJECT_DIR / "Output"
LATEST_CAPTURE_PATH = OUTPUT_DIR / "latest-plate-crop.jpg"


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

MAX_CROP_BYTES = 4 * 1024 * 1024
MAX_FRAME_BYTES = 10 * 1024 * 1024


def mysql_options() -> dict[str, Any]:
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
        self.connection = pymysql.connect(**mysql_options())

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
        for statement in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
            statement = statement.strip()
            if statement:
                connection.execute(statement)
        # CREATE TABLE IF NOT EXISTS does not add columns to an installation
        # upgraded from the original single-controller schema.
        for table, column, definition in (
            ("access_events", "controller_uid", "VARCHAR(64) NULL AFTER id"),
            ("reader_commands", "controller_uid", "VARCHAR(64) NULL AFTER id"),
            ("controllers", "rfid_connected", "TINYINT(1) NOT NULL DEFAULT 0 AFTER camera_connected"),
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
        # Preserve the original singleton controller and its history on the
        # first upgrade. New controller software subsequently reports a stable
        # per-device ID and appears as its own selector entry.
        controller_count = connection.execute(
            "SELECT COUNT(*) AS count FROM controllers"
        ).fetchone()["count"]
        if not controller_count:
            legacy = connection.execute(
                "SELECT * FROM system_status WHERE id = 1"
            ).fetchone()
            if legacy is not None:
                controller_type = "rfid" if legacy.get("controller_type") == "rfid" else "plate"
                legacy_uid = f"legacy-{controller_type}-controller"
                connection.execute(
                    """
                    INSERT INTO controllers (
                        controller_uid, display_name, controller_type,
                        camera_state, detector_state, gate_state,
                        camera_connected, rfid_connected, loop_active, ir_blocked, barrier_open,
                        traffic_green, plate_unrecognized, last_plate, last_rfid,
                        controller_seen_at, last_heartbeat
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        legacy_uid,
                        "Legacy RFID Controller" if controller_type == "rfid" else "Legacy Plate + RFID Controller",
                        controller_type,
                        legacy["camera_state"], legacy["detector_state"], legacy["gate_state"],
                        legacy["camera_connected"], legacy.get("rfid_connected", controller_type == "rfid"),
                        legacy["loop_active"], legacy["ir_blocked"],
                        legacy["barrier_open"], legacy["traffic_green"],
                        legacy["plate_unrecognized"], legacy["last_plate"], legacy["last_rfid"],
                        legacy["controller_seen_at"], legacy["last_heartbeat"],
                    ),
                )
                connection.execute(
                    "UPDATE access_events SET controller_uid = ? WHERE controller_uid IS NULL",
                    (legacy_uid,),
                )
                connection.execute(
                    "UPDATE reader_commands SET controller_uid = ? WHERE controller_uid IS NULL",
                    (legacy_uid,),
                )
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
            if session.get("role") not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def record_audit(action: str, entity_type: str | None = None, entity_id: int | None = None, details: str | None = None) -> None:
    get_db().execute(
        """
        INSERT INTO audit_log (user_id, action, entity_type, entity_id, details)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session.get("user_id"), action, entity_type, entity_id, details),
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
        controllers = controller_records(connection)
        selected_uid = selected_controller_uid(connection)
        context.update(
            controllers=controllers,
            selected_controller_uid=selected_uid,
            selected_controller=next(
                (row for row in controllers if row["controller_uid"] == selected_uid),
                None,
            ),
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


def request_controller_uid(fallback: str) -> str:
    return normalize_controller_uid(request.form.get("controller_id", ""), fallback)


def default_controller_name(controller_uid: str, controller_type: str) -> str:
    label = "RFID Controller" if controller_type == "rfid" else "Plate + RFID Controller"
    suffix = controller_uid[-8:] if len(controller_uid) > 8 else controller_uid
    return f"{label} {suffix}"


def ensure_controller(
    connection: DatabaseConnection,
    controller_uid: str,
    controller_type: str,
) -> None:
    connection.execute(
        """
        INSERT INTO controllers (controller_uid, display_name, controller_type)
        VALUES (?, ?, ?)
        ON DUPLICATE KEY UPDATE controller_type = VALUES(controller_type)
        """,
        (
            controller_uid,
            default_controller_name(controller_uid, controller_type),
            controller_type,
        ),
    )


def controller_records(connection: DatabaseConnection) -> list[dict[str, Any]]:
    return connection.execute(
        """
        SELECT controller_uid, display_name, controller_type,
               controller_seen_at IS NOT NULL AND
               controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP)
                   AS controller_online,
               DATE_FORMAT(controller_seen_at, '%Y-%m-%d %H:%i:%s') AS controller_seen_at
        FROM controllers
        ORDER BY controller_online DESC, display_name, controller_uid
        """
    ).fetchall()


def selected_controller_uid(connection: DatabaseConnection) -> str | None:
    controllers = controller_records(connection)
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
        "SELECT 1 FROM controllers WHERE controller_uid = ?", (controller_uid,)
    ).fetchone()
    if exists is None:
        return {"success": False, "message": "Controller not found."}, 404
    session["active_controller_uid"] = controller_uid
    return {"success": True, "controller_id": controller_uid}


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
    gate_state = request.form.get("gate_state", "idle_closed").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{1,40}", gate_state):
        return {"error": "Invalid gate state."}, 400
    try:
        controller_uid = request_controller_uid("legacy-rfid-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    ensure_controller(connection, controller_uid, "rfid")
    connection.execute(
        """
        UPDATE controllers
        SET camera_state = 'unavailable',
            detector_state = 'idle', gate_state = ?, camera_connected = 0,
            rfid_connected = ?,
            loop_active = ?, ir_blocked = ?, barrier_open = ?,
            traffic_green = ?, plate_unrecognized = ?,
            controller_seen_at = CURRENT_TIMESTAMP,
            last_heartbeat = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE controller_uid = ?
        """,
        (
            gate_state,
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
    return {"accepted": True, "controller_id": controller_uid, "controller_type": "rfid", "camera": False}


@app.post("/api/rfid-controller/recognitions")
def rfid_controller_recognition():
    """Authorize an RFID scan and create a normal camera-less access event."""
    rfid_number = normalize_rfid(request.form.get("rfid", ""))
    if len(rfid_number) < 4 or len(rfid_number) > 64:
        return {"error": "A valid RFID value containing 4 to 64 characters is required."}, 400

    try:
        controller_uid = request_controller_uid("legacy-rfid-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    ensure_controller(connection, controller_uid, "rfid")
    vehicle = connection.execute(
        """
        SELECT v.id, v.plate_number, v.owner_name, v.vehicle_type, v.make, v.model
        FROM rfid_stickers r
        JOIN vehicles v ON v.id = r.vehicle_id
        WHERE r.sticker_value = ? AND r.is_active = 1
          AND v.is_active = 1
          AND (v.registration_expires_on IS NULL
               OR v.registration_expires_on >= CURRENT_DATE)
        LIMIT 1
        """,
        (rfid_number,),
    ).fetchone()
    authorized = vehicle is not None
    decision = "authorized" if authorized else "denied"
    plate = vehicle["plate_number"] if vehicle else "RFID"
    reason = "rfid_authorized" if authorized else "rfid_not_registered_or_expired"

    duplicate = connection.execute(
        """
        SELECT id FROM access_events
        WHERE controller_uid = ? AND rfid_number = ?
          AND detected_at >= TIMESTAMPADD(
            SECOND,
            -CAST(COALESCE((SELECT `value` FROM settings
                WHERE `key` = 'duplicate_event_seconds'), '30') AS SIGNED),
            NOW()
        )
        ORDER BY detected_at DESC LIMIT 1
        """,
        (controller_uid, rfid_number),
    ).fetchone()
    event_id = duplicate["id"] if duplicate else None
    if duplicate is None:
        cursor = connection.execute(
            """
            INSERT INTO access_events (
                controller_uid, vehicle_id, plate_number, rfid_number, rfid_required,
                rfid_authorized, decision, gate_action, detector_confidence,
                notes
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, NULL, ?)
            """,
            (
                controller_uid,
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

    # Do not modify camera or detector fields. The Raspberry Pi plate
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
        controller_uid = request_controller_uid("legacy-plate-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    ensure_controller(connection, controller_uid, "plate")
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


@app.post("/api/reader/commands/next")
def reader_next_command():
    try:
        controller_uid = request_controller_uid("legacy-plate-controller")
    except ValueError as error:
        return {"error": str(error)}, 400
    connection = get_db()
    ensure_controller(connection, controller_uid, "plate")
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
        controller_uid = request_controller_uid("legacy-plate-controller")
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
        controller_uid = request_controller_uid("legacy-plate-controller")
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
    ensure_controller(connection, controller_uid, "plate")
    vehicle = connection.execute(
        """
        SELECT id, owner_name FROM vehicles
        WHERE plate_number = ? AND is_active = 1
          AND (registration_expires_on IS NULL
               OR registration_expires_on >= CURRENT_DATE)
        LIMIT 1
        """,
        (plate,),
    ).fetchone()
    rfid_vehicle = None
    if rfid_required and rfid_number:
        rfid_vehicle = connection.execute(
            """
            SELECT v.id, v.owner_name
            FROM rfid_stickers r
            JOIN vehicles v ON v.id = r.vehicle_id
            WHERE r.sticker_value = ? AND r.is_active = 1
              AND v.is_active = 1
              AND (v.registration_expires_on IS NULL
                   OR v.registration_expires_on >= CURRENT_DATE)
            LIMIT 1
            """,
            (rfid_number,),
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

    duplicate = None if is_no_plate_capture else connection.execute(
        """
        SELECT id FROM access_events
        WHERE controller_uid = ? AND plate_number = ? AND COALESCE(rfid_number, '') = ?
          AND detected_at >= TIMESTAMPADD(
            SECOND,
            -CAST(COALESCE((SELECT `value` FROM settings
                WHERE `key` = 'duplicate_event_seconds'), '30') AS SIGNED),
            NOW()
        )
        ORDER BY detected_at DESC LIMIT 1
        """,
        (controller_uid, plate, rfid_number),
    ).fetchone()

    event_id = duplicate["id"] if duplicate else None
    if duplicate is None:
        relative_crop_path = None
        relative_raw_frame_path = None
        relative_annotated_frame_path = None
        if image_bytes or raw_frame_bytes or annotated_frame_bytes:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            filename = f"{timestamp}-{plate}.jpg"
            relative_crop_path = store_event_image(
                "Plate-Crops", filename, image_bytes
            )
            relative_raw_frame_path = store_event_image(
                "Raw-Frames", filename, raw_frame_bytes
            )
            relative_annotated_frame_path = store_event_image(
                "Annotated-Frames", filename, annotated_frame_bytes
            )
        if image_bytes:
            temporary_latest = LATEST_CAPTURE_PATH.with_suffix(".tmp.jpg")
            temporary_latest.write_bytes(image_bytes)
            temporary_latest.replace(LATEST_CAPTURE_PATH)
        cursor = connection.execute(
            """
            INSERT INTO access_events (
                controller_uid, vehicle_id, plate_number, rfid_number, rfid_required,
                rfid_authorized, decision, gate_action,
                detector_confidence, image_path, raw_image_path,
                annotated_image_path, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'not_requested', ?, ?, ?, ?, ?)
            """,
            (
                controller_uid,
                authorized_vehicle["id"] if authorized_vehicle else None,
                plate,
                rfid_number or None,
                int(rfid_required),
                int(rfid_authorized),
                decision,
                detector_confidence,
                relative_crop_path,
                relative_raw_frame_path,
                relative_annotated_frame_path,
                authorization_reason,
            ),
        )
        event_id = cursor.lastrowid

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
    connection.commit()
    return {
        "accepted": True,
        "controller_id": controller_uid,
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
                VALUES (?, ?, 'administrator')
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
    controller_uid = selected_controller_uid(connection)
    scoped_uid = controller_uid or "__no_controller__"
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
            (SELECT count(*) FROM vehicles WHERE is_active = 1) AS active_vehicles,
            (SELECT count(*) FROM access_events WHERE controller_uid = ? AND DATE(detected_at) = CURRENT_DATE) AS events_today,
            (SELECT count(*) FROM access_events WHERE controller_uid = ? AND decision = 'authorized' AND DATE(detected_at) = CURRENT_DATE) AS authorized_today,
            (SELECT count(*) FROM access_events WHERE controller_uid = ? AND decision = 'denied' AND DATE(detected_at) = CURRENT_DATE) AS denied_today
        """,
        (scoped_uid, scoped_uid, scoped_uid),
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
        SELECT controller_uid, display_name, controller_type,
               camera_state, detector_state, gate_state,
               camera_connected, rfid_connected, loop_active, ir_blocked,
               barrier_open, traffic_green, plate_unrecognized,
               (controller_seen_at IS NOT NULL AND
                controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP))
                    AS controller_online,
               last_plate, last_rfid,
               DATE_FORMAT(controller_seen_at, '%Y-%m-%d %H:%i:%s') AS controller_seen_at,
               DATE_FORMAT(last_heartbeat, '%Y-%m-%d %H:%i:%s') AS last_heartbeat,
               DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') AS updated_at
        FROM controllers WHERE controller_uid = ?
        """,
        (scoped_uid,),
    ).fetchone()
    if system is None:
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
        }
    return {
        "summary": summary,
        "recent_events": recent_events,
        "latest_event": latest_event,
        "latest_has_image": latest_has_image,
        "latest_timing": latest_timing,
        "daily": daily,
        "system": system,
        "latest_capture_version": latest_event["id"] if latest_event else None,
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
            "camera_running": bool(system["controller_online"] and system["camera_connected"]),
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
@role_required("administrator")
def camera_capture():
    connection = get_db()
    controller_uid = selected_controller_uid(connection)
    controller = connection.execute(
        """
        SELECT controller_type, controller_seen_at IS NOT NULL AND
               controller_seen_at >= TIMESTAMPADD(SECOND, -12, CURRENT_TIMESTAMP) AS online
        FROM controllers WHERE controller_uid = ?
        """,
        (controller_uid or "",),
    ).fetchone()
    if (
        controller is None
        or controller["controller_type"] != "plate"
        or not controller["online"]
    ):
        return {"success": False, "message": "Select an online Plate + RFID controller first."}, 409
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
        SELECT id, status FROM reader_commands
        WHERE controller_uid = ? AND status IN ('pending', 'active')
        ORDER BY created_at LIMIT 1
        """,
        (controller_uid,),
    ).fetchone()
    if existing is not None:
        connection.commit()
        success = False
        message = "A plate capture is already queued or running."
    else:
        cursor = connection.execute(
            """
            INSERT INTO reader_commands (controller_uid, command_type, status, requested_by)
            VALUES (?, 'capture', 'pending', ?)
            """,
            (controller_uid, session["user_id"]),
        )
        connection.execute(
            """
            UPDATE controllers
            SET detector_state = 'queued', updated_at = CURRENT_TIMESTAMP
            WHERE controller_uid = ?
            """,
            (controller_uid,),
        )
        record_audit("queue_capture", "reader_command", cursor.lastrowid, "Remote plate capture")
        connection.commit()
        success = True
        message = "Plate capture queued for the Raspberry Pi."
    if request.accept_mimetypes.best == "application/json":
        return {"success": success, "message": message}, 202 if success else 409
    flash(message, "success" if success else "error")
    return redirect(url_for("dashboard"))


@app.post("/hardware/command")
@role_required("administrator")
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
            "message": "The Raspberry Pi controller is offline. No hardware command was queued.",
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
        INSERT INTO reader_commands (controller_uid, command_type, status, requested_by)
        VALUES (?, ?, 'pending', ?)
        """,
        (controller_uid, command, session["user_id"]),
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
@role_required("administrator")
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
        return {"success": False, "message": "The Raspberry Pi controller is offline."}, 409
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
            controller_uid, command_type, status, requested_by, serial_tx_hex, serial_baud,
            serial_data_bits, serial_parity, serial_stop_bits, serial_timeout_ms
        ) VALUES (?, 'rfid_serial', 'pending', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            controller_uid, session["user_id"], tx_hex, baud, data_bits,
            parity, stop_bits, timeout_ms,
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
@role_required("administrator")
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
@role_required("administrator")
def vehicles():
    query = request.args.get("q", "").strip()
    if query:
        wildcard = f"%{query}%"
        normalized_plate_query = normalize_plate(query)
        plate_wildcard = (
            f"%{normalized_plate_query}%"
            if normalized_plate_query
            else "__NO_PLATE_MATCH__"
        )
        records = get_db().execute(
            """
            SELECT v.*,
                   (SELECT r.sticker_value FROM rfid_stickers r
                    WHERE r.vehicle_id = v.id AND r.is_active = 1
                    ORDER BY r.id LIMIT 1) AS rfid_number
            FROM vehicles v
            WHERE v.plate_number LIKE ? OR v.owner_name LIKE ?
               OR v.make LIKE ? OR v.model LIKE ?
               OR EXISTS (
                   SELECT 1 FROM rfid_stickers r
                   WHERE r.vehicle_id = v.id AND r.sticker_value LIKE ?
               )
            ORDER BY v.is_active DESC, v.plate_number
            """,
            (plate_wildcard, wildcard, wildcard, wildcard, wildcard),
        ).fetchall()
    else:
        records = get_db().execute(
            """
            SELECT v.*,
                   (SELECT r.sticker_value FROM rfid_stickers r
                    WHERE r.vehicle_id = v.id AND r.is_active = 1
                    ORDER BY r.id LIMIT 1) AS rfid_number
            FROM vehicles v
            ORDER BY v.is_active DESC, v.plate_number
            """
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
@role_required("administrator")
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
                cursor = connection.execute(
                    """
                    INSERT INTO vehicles (
                        plate_number, owner_name, vehicle_type, make, model, color,
                        contact_number, email, registration_expires_on, photo_path, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(values[key] or None for key in (
                        "plate_number", "owner_name", "vehicle_type", "make", "model", "color",
                        "contact_number", "email", "registration_expires_on", "photo_path", "notes"
                    )),
                )
                if values["rfid_number"]:
                    connection.execute(
                        "INSERT INTO rfid_stickers (vehicle_id, sticker_value) VALUES (?, ?)",
                        (cursor.lastrowid, values["rfid_number"]),
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
@role_required("administrator")
def vehicle_edit(vehicle_id: int):
    connection = get_db()
    existing = connection.execute(
        """
        SELECT v.*,
               (SELECT r.sticker_value FROM rfid_stickers r
                WHERE r.vehicle_id = v.id AND r.is_active = 1
                ORDER BY r.id LIMIT 1) AS rfid_number
        FROM vehicles v WHERE v.id = ?
        """,
        (vehicle_id,),
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
                WHERE id = ?
                """,
                tuple(values[key] or None for key in (
                    "plate_number", "owner_name", "vehicle_type", "make", "model", "color",
                    "contact_number", "email", "registration_expires_on", "photo_path", "notes"
                )) + (vehicle_id,),
            )
            connection.execute("DELETE FROM rfid_stickers WHERE vehicle_id = ?", (vehicle_id,))
            if values["rfid_number"]:
                connection.execute(
                    "INSERT INTO rfid_stickers (vehicle_id, sticker_value) VALUES (?, ?)",
                    (vehicle_id, values["rfid_number"]),
                )
            record_audit("update_vehicle", "vehicle", vehicle_id, values["plate_number"])
            connection.commit()
            flash("Vehicle details updated.", "success")
            return redirect(url_for("vehicles"))
        except IntegrityError:
            flash("That plate or RFID sticker is already registered or is invalid.", "error")
    return render_template("vehicle_form.html", vehicle=values, editing=True)


@app.post("/vehicles/<int:vehicle_id>/toggle")
@role_required("administrator")
def vehicle_toggle(vehicle_id: int):
    connection = get_db()
    vehicle = connection.execute(
        "SELECT plate_number, is_active FROM vehicles WHERE id = ?", (vehicle_id,)
    ).fetchone()
    if vehicle is None:
        abort(404)
    new_state = 0 if vehicle["is_active"] else 1
    connection.execute("UPDATE vehicles SET is_active = ? WHERE id = ?", (new_state, vehicle_id))
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
@role_required("administrator")
def vehicle_delete(vehicle_id: int):
    connection = get_db()
    vehicle = connection.execute(
        "SELECT plate_number, owner_name FROM vehicles WHERE id = ?", (vehicle_id,)
    ).fetchone()
    if vehicle is None:
        abort(404)
    record_audit(
        "delete_vehicle",
        "vehicle",
        vehicle_id,
        f'{vehicle["plate_number"]} / {vehicle["owner_name"]}',
    )
    connection.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    connection.commit()
    flash(f'{vehicle["plate_number"]} was permanently deleted.', "success")
    return redirect(url_for("vehicles", q=request.form.get("return_query", "").strip()))


@app.route("/users")
@role_required("administrator")
def users():
    guard_users = get_db().execute(
        """
        SELECT id, username, is_active, created_at, last_login_at
        FROM users
        WHERE role = 'viewer'
        ORDER BY is_active DESC, username
        """
    ).fetchall()
    return render_template("users.html", users=guard_users)


@app.route("/users/new", methods=["GET", "POST"])
@role_required("administrator")
def user_new():
    username = ""
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
            try:
                connection = get_db()
                cursor = connection.execute(
                    """
                    INSERT INTO users (username, password_hash, role)
                    VALUES (?, ?, 'viewer')
                    """,
                    (username, generate_password_hash(password)),
                )
                record_audit("create_guard", "user", cursor.lastrowid, username)
                connection.commit()
                flash(f"Read-only guard account {username} was created.", "success")
                return redirect(url_for("users"))
            except IntegrityError:
                flash("That username is already in use.", "error")
    return render_template("user_form.html", username=username)


@app.post("/users/<int:user_id>/toggle")
@role_required("administrator")
def user_toggle(user_id: int):
    connection = get_db()
    guard = connection.execute(
        "SELECT username, is_active FROM users WHERE id = ? AND role = 'viewer'",
        (user_id,),
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
@role_required("administrator")
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
    event = get_db().execute(
        "SELECT image_path FROM access_events WHERE id = ?", (event_id,)
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
    event = get_db().execute(
        """
        SELECT image_path, raw_image_path, annotated_image_path
        FROM access_events WHERE id = ?
        """,
        (event_id,),
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
    if not LATEST_CAPTURE_PATH.is_file():
        abort(404)
    response = send_file(LATEST_CAPTURE_PATH, max_age=0)
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
    site_id = os.environ.get("MOBILE_ACCOUNT_SITE_ID", "").strip()
    secret_configured = bool(
        os.environ.get("MOBILE_ACCOUNT_SYNC_SECRET", "").strip()
    )
    return {
        "service": "plate-program",
        "account_sync_api": "v1",
        "schema_ready": True,
        "enabled": enabled,
        "configured": bool(service_url and site_id and secret_configured),
        "authorization_enforcement": False,
        "state": "disabled" if not enabled else "prepared",
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
