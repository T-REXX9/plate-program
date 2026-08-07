from __future__ import annotations

import re


def normalize_rfid(value: str) -> str:
    hexadecimal_bytes = re.findall(r"0[xX]([0-9A-Fa-f]{2})", value)
    if hexadecimal_bytes:
        return "".join(hexadecimal_bytes).upper()
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def authorize_plate_and_rfid(
    plate_vehicle_id: int | None,
    rfid_required: bool,
    rfid_number: str,
    rfid_vehicle_id: int | None,
) -> tuple[bool, bool, str]:
    plate_authorized = plate_vehicle_id is not None
    rfid_authorized = bool(rfid_required and rfid_number and rfid_vehicle_id is not None)
    if plate_authorized and rfid_authorized:
        return True, True, "plate_and_rfid_authorized"
    if plate_authorized:
        return True, False, "plate_authorized"
    if rfid_authorized:
        return True, True, "rfid_authorized"
    if not rfid_required:
        return False, False, "plate_not_registered"
    if not rfid_number:
        return False, False, "neither_authorized_rfid_not_read"
    return False, False, "neither_authorized_rfid_not_registered"
