from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path


from web.model_manifest import validate_model_manifest


class ModelManifestTests(unittest.TestCase):
    def test_valid_manifest_accepts_matching_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            detector = root / "detector.onnx"
            recognizer = root / "recognizer.onnx"
            detector.write_bytes(b"detector")
            recognizer.write_bytes(b"recognizer")
            manifest = {
                "detector": {"path": "detector.onnx", "sha256": hashlib.sha256(b"detector").hexdigest()},
                "recognizer": {"path": "recognizer.onnx", "sha256": hashlib.sha256(b"recognizer").hexdigest()},
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = validate_model_manifest(manifest_path)

            self.assertEqual(
                result,
                {"detector": detector.resolve(), "recognizer": recognizer.resolve()},
            )

    def test_missing_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps({"detector": {"path": "missing.onnx", "sha256": "0" * 64}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "detector model is missing"):
                validate_model_manifest(manifest_path)

    def test_checksum_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            detector = root / "detector.onnx"
            detector.write_bytes(b"detector")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps({"detector": {"path": "detector.onnx", "sha256": "0" * 64}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "detector model checksum mismatch"):
                validate_model_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
