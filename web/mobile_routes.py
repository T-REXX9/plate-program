from __future__ import annotations

from functools import wraps
from typing import Any

from flask import Blueprint, abort, request, session, url_for
from werkzeug.security import generate_password_hash

mobile_api = Blueprint('mobile_api', __name__, url_prefix='/api/mobile')


def _helpers():
    from app import current_village_role, get_db, require_selected_village, selected_village_id, village_records
    return current_village_role, get_db, require_selected_village, selected_village_id, village_records


def _login_required():
    if 'user_id' not in session:
        abort(401)


def _roles(*allowed: str):
    _login_required()
    current_village_role, get_db, _, _, _ = _helpers()
    role = current_village_role(get_db())
    if role not in allowed:
        abort(403)
    return role


def _json_rows(rows: list[Any], date_fields=()):
    result = []
    for row in rows:
        item = dict(row)
        for field in date_fields:
            if item.get(field) is not None and hasattr(item[field], 'isoformat'):
                item[field] = item[field].isoformat()
        result.append(item)
    return result


@mobile_api.get('/session')
def session_info():
    _login_required()
    current_village_role, get_db, _, selected_village_id, _ = _helpers()
    connection = get_db()
    role = current_village_role(connection)
    return {'username': session.get('username'), 'role': role, 'village_id': selected_village_id(connection), 'can_manage_tenant': role in {'system_owner', 'village_admin'}, 'can_operate_gate': role in {'system_owner', 'village_admin', 'security_guard'}, 'is_system_owner': role == 'system_owner'}


@mobile_api.get('/dashboard')
def dashboard():
    _login_required()
    from app import api_dashboard
    response = api_dashboard()
    return response[0] if isinstance(response, tuple) else response


@mobile_api.get('/overview')
def overview():
    return dashboard()


@mobile_api.get('/gates')
def gates():
    _login_required()
    from app import api_gates
    response = api_gates()
    return response[0] if isinstance(response, tuple) else response


@mobile_api.get('/logs')
def logs():
    _login_required()
    from app import api_logs
    response = api_logs()
    return response[0] if isinstance(response, tuple) else response


@mobile_api.get('/access-log')
def access_log():
    return logs()


@mobile_api.get('/vehicles')
def vehicles():
    _roles('system_owner', 'village_admin')
    _, get_db, require_selected_village, _, _ = _helpers()
    connection = get_db()
    rows = connection.execute('SELECT id, owner_name, plate_number, rfid_number, vehicle_type, make, model, color, is_active, registration_expires_on FROM vehicles WHERE village_id = ? ORDER BY owner_name, plate_number', (require_selected_village(connection),)).fetchall()
    return _json_rows(rows, ('registration_expires_on',))


@mobile_api.get('/users')
def users():
    _roles('system_owner', 'village_admin')
    _, get_db, require_selected_village, _, _ = _helpers()
    connection = get_db()
    rows = connection.execute('SELECT u.id, u.username, u.is_active, u.created_at, u.last_login_at, r.role FROM users u JOIN user_village_roles r ON r.user_id = u.id WHERE r.village_id = ? ORDER BY u.username', (require_selected_village(connection),)).fetchall()
    return _json_rows(rows, ('created_at', 'last_login_at'))


@mobile_api.get('/sites')
def sites():
    _roles('system_owner')
    _, get_db, _, _, village_records = _helpers()
    connection = get_db()
    return {'villages': village_records(connection), 'gates': _json_rows(connection.execute('SELECT g.id, g.village_id, g.gate_uid, g.name, g.is_active, v.name AS village_name FROM gates g JOIN villages v ON v.id = g.village_id ORDER BY v.name, g.name').fetchall()), 'controllers': _json_rows(connection.execute('SELECT c.controller_uid, c.display_name, c.controller_type, c.gate_id, c.is_active, c.lifecycle_status, g.name AS gate_name, v.name AS village_name FROM controllers c LEFT JOIN gates g ON g.id = c.gate_id LEFT JOIN villages v ON v.id = g.village_id ORDER BY c.display_name').fetchall())}


@mobile_api.post('/vehicles')
def vehicle_create():
    _roles('system_owner', 'village_admin')
    _, get_db, require_selected_village, _, _ = _helpers()
    payload = request.get_json(silent=True) or {}
    owner = str(payload.get('owner_name', '')).strip()
    plate = str(payload.get('plate_number', '')).strip().upper()
    if not owner or not plate:
        return {'error': 'Owner name and plate number are required.'}, 400
    connection = get_db()
    cursor = connection.execute('INSERT INTO vehicles (village_id, owner_name, plate_number, rfid_number, vehicle_type, make, model, color, registration_expires_on, notes, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)', (require_selected_village(connection), owner, plate, payload.get('rfid_number') or None, payload.get('vehicle_type') or None, payload.get('make') or None, payload.get('model') or None, payload.get('color') or None, payload.get('registration_expires_on') or None, payload.get('notes') or None))
    connection.commit()
    return {'id': cursor.lastrowid, 'created': True}, 201


@mobile_api.post('/vehicles/<int:vehicle_id>/toggle')
def vehicle_toggle(vehicle_id: int):
    _roles('system_owner', 'village_admin')
    _, get_db, require_selected_village, _, _ = _helpers()
    connection = get_db()
    village_id = require_selected_village(connection)
    row = connection.execute('SELECT is_active FROM vehicles WHERE id = ? AND village_id = ?', (vehicle_id, village_id)).fetchone()
    if row is None:
        abort(404)
    state = 0 if row['is_active'] else 1
    connection.execute('UPDATE vehicles SET is_active = ? WHERE id = ? AND village_id = ?', (state, vehicle_id, village_id))
    connection.commit()
    return {'id': vehicle_id, 'is_active': bool(state)}


@mobile_api.post('/users')
def user_create():
    _roles('system_owner', 'village_admin')
    _, get_db, require_selected_village, _, _ = _helpers()
    payload = request.get_json(silent=True) or {}
    username = str(payload.get('username', '')).strip()
    password = str(payload.get('password', ''))
    role = str(payload.get('role', 'homeowner')).strip()
    if len(username) < 3 or len(password) < 10 or role not in {'village_admin', 'security_guard', 'homeowner'}:
        return {'error': 'Invalid username, password, or role.'}, 400
    connection = get_db()
    try:
        cursor = connection.execute('INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)', (username, generate_password_hash(password), 'village_user'))
        connection.execute('INSERT INTO user_village_roles (user_id, village_id, role) VALUES (?, ?, ?)', (cursor.lastrowid, require_selected_village(connection), role))
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if 'unique' in str(exc).lower() or 'duplicate' in str(exc).lower():
            return {'error': 'That username is already in use.'}, 409
        raise
    return {'id': cursor.lastrowid, 'created': True}, 201


@mobile_api.post('/users/<int:user_id>/toggle')
def user_toggle(user_id: int):
    _roles('system_owner', 'village_admin')
    _, get_db, require_selected_village, _, _ = _helpers()
    connection = get_db()
    row = connection.execute("SELECT u.is_active FROM users u JOIN user_village_roles r ON r.user_id = u.id WHERE u.id = ? AND r.village_id = ? AND u.role != 'system_owner'", (user_id, require_selected_village(connection))).fetchone()
    if row is None:
        abort(404)
    state = 0 if row['is_active'] else 1
    connection.execute('UPDATE users SET is_active = ? WHERE id = ?', (state, user_id))
    connection.commit()
    return {'id': user_id, 'is_active': bool(state)}


@mobile_api.post('/gates/<int:gate_id>/toggle')
def gate_toggle(gate_id: int):
    _roles('system_owner', 'village_admin')
    _, get_db, require_selected_village, _, _ = _helpers()
    connection = get_db()
    village_id = require_selected_village(connection)
    row = connection.execute('SELECT is_active FROM gates WHERE id = ? AND village_id = ?', (gate_id, village_id)).fetchone()
    if row is None:
        abort(404)
    state = 0 if row['is_active'] else 1
    connection.execute('UPDATE gates SET is_active = ? WHERE id = ? AND village_id = ?', (state, gate_id, village_id))
    connection.commit()
    return {'id': gate_id, 'is_active': bool(state)}


@mobile_api.post('/hardware/command')
def hardware_command():
    _roles('system_owner', 'village_admin', 'security_guard')
    _, get_db, require_selected_village, _, _ = _helpers()
    command_type = str((request.get_json(silent=True) or {}).get('command_type', '')).strip()
    if command_type not in {'barrier_open', 'barrier_close', 'traffic_green', 'traffic_red', 'capture'}:
        return {'error': 'Unsupported hardware command.'}, 400
    connection = get_db()
    row = connection.execute('SELECT c.controller_uid FROM controllers c JOIN gates g ON g.id = c.gate_id WHERE g.village_id = ? AND c.is_active = 1 ORDER BY c.display_name LIMIT 1', (require_selected_village(connection),)).fetchone()
    if row is None:
        return {'error': 'No active controller is available.'}, 409
    cursor = connection.execute("INSERT INTO reader_commands (controller_uid, command_type, status) VALUES (?, ?, 'pending')", (row['controller_uid'], command_type))
    connection.commit()
    return {'command_id': cursor.lastrowid, 'queued': True}, 202


@mobile_api.post('/logout')
def logout():
    session.clear()
    return {'logged_out': True}


@mobile_api.get('/roles')
def roles():
    return {'roles': ['system_owner', 'village_admin', 'security_guard', 'homeowner']}


@mobile_api.get('/health')
def health():
    return {'service': 'plate-program', 'status': 'ok'}

@mobile_api.post('/villages')
def village_create():
    _roles('system_owner')
    _, get_db, _, _, _ = _helpers()
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name', '')).strip()
    village_uid = str(payload.get('village_uid', '')).strip()
    if not name or not village_uid:
        return {'error': 'Village name and ID are required.'}, 400
    connection = get_db()
    try:
        cursor = connection.execute('INSERT INTO villages (village_uid, name, timezone, is_active) VALUES (?, ?, ?, 1)', (village_uid, name, payload.get('timezone', 'Asia/Manila')))
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if 'unique' in str(exc).lower() or 'duplicate' in str(exc).lower():
            return {'error': 'Village ID or name is already in use.'}, 409
        raise
    return {'id': cursor.lastrowid, 'created': True}, 201


@mobile_api.post('/gates')
def gate_create():
    _roles('system_owner')
    _, get_db, _, _, _ = _helpers()
    payload = request.get_json(silent=True) or {}
    try:
        village_id = int(payload.get('village_id', 0))
    except (TypeError, ValueError):
        village_id = 0
    gate_uid = str(payload.get('gate_uid', '')).strip()
    name = str(payload.get('name', '')).strip()
    if not village_id or not gate_uid or not name:
        return {'error': 'Village, gate ID, and gate name are required.'}, 400
    connection = get_db()
    if connection.execute('SELECT id FROM villages WHERE id = ?', (village_id,)).fetchone() is None:
        abort(404)
    try:
        cursor = connection.execute('INSERT INTO gates (village_id, gate_uid, name, direction, is_active) VALUES (?, ?, ?, ?, 1)', (village_id, gate_uid, name, payload.get('direction', 'entry')))
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if 'unique' in str(exc).lower() or 'duplicate' in str(exc).lower():
            return {'error': 'Gate ID or name is already in use.'}, 409
        raise
    return {'id': cursor.lastrowid, 'created': True}, 201
