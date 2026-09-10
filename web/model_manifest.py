"""Integrity checks for the server-owned detector and OCR model artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_manifest(manifest_path: Path) -> dict[str, Path]:
    """Validate every required model and return its resolved path."""
    try:
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Recognition model manifest could not be read: {error}") from error

    models: dict[str, Path] = {}
    for role in ("detector", "recognizer", "dictionary"):
        entry = manifest.get(role)
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError(f"Recognition model manifest has no {role} model entry.")
        expected = entry.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"Recognition model manifest has no valid {role} checksum.")
        path = (manifest_path.parent / entry["path"]).resolve()
        if not path.is_file():
            raise ValueError(f"The {role} model is missing: {path}")
        actual = _sha256(path)
        if actual != expected.lower():
            raise ValueError(f"The {role} model checksum mismatch: {path}")
        models[role] = path
    return models
