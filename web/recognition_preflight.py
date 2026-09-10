"""Validate recognition model artifacts and load them before service startup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from model_manifest import validate_model_manifest


def run(manifest_path: Path) -> dict[str, str]:
    models = validate_model_manifest(manifest_path)
    try:
        import cv2
        for role, path in models.items():
            network = cv2.dnn.readNet(str(path))
            if network.empty():
                raise ValueError(f"The {role} model loaded empty: {path}")
    except ImportError as error:
        raise ValueError("OpenCV is not installed for recognition.") from error
    return {role: str(path) for role, path in models.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Plate Program recognition models.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "models" / "manifest.json",
    )
    arguments = parser.parse_args()
    try:
        print(json.dumps({"status": "ok", "models": run(arguments.manifest)}))
    except ValueError as error:
        print(json.dumps({"status": "unavailable", "error": str(error)}))
        raise SystemExit(1)
