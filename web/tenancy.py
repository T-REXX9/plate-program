from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Iterable


def normalize_tenant_uid(value: str, label: str) -> str:
    normalized = value.strip().lower().replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]", normalized):
        raise ValueError(
            f"{label} must be 3 to 64 lowercase letters, numbers, dots, "
            "dashes, or underscores."
        )
    return normalized


def controller_key_digest(controller_key: str) -> str:
    return hashlib.sha256(controller_key.encode("utf-8")).hexdigest()


def generate_controller_key() -> str:
    """Return a cryptographically random, exactly 10-digit controller key."""
    return f"{secrets.randbelow(10**10):010d}"


def matching_credential_id(
    controller_key: str,
    credentials: Iterable[dict[str, object]],
) -> int | None:
    supplied_hash = controller_key_digest(controller_key)
    for credential in credentials:
        stored_hash = str(credential["credential_hash"])
        if hmac.compare_digest(stored_hash, supplied_hash):
            return int(credential["id"])
    return None
