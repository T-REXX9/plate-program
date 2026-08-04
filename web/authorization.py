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
    if not rfid_required:
        if plate_vehicle_id is None:
            return False, False, "plate_not_registered"
        return True, False, "plate_authorized_rfid_disabled"
    if not rfid_number:
        return False, False, "rfid_not_read"
    if rfid_vehicle_id is None:
        return False, False, "rfid_not_registered"
    return True, True, "rfid_authorized"
