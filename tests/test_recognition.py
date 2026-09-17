from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from recognition import _crop_plate, _fast_plate_config_path  # noqa: E402


class PlateCropTests(unittest.TestCase):
    def test_crop_adds_a_right_edge_margin_only(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        crop = _crop_plate(frame, [40, 20, 100, 30])

        self.assertEqual(crop.shape, (30, 113, 3))

    def test_crop_does_not_extend_past_the_frame(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        crop = _crop_plate(frame, [150, 20, 50, 30])

        self.assertEqual(crop.shape, (30, 50, 3))


class PlateSpecificModelTests(unittest.TestCase):
    def test_fast_plate_config_is_discovered_next_to_its_model(self) -> None:
        models = Path(__file__).resolve().parents[1] / "models"

        with self.subTest("existing configuration"):
            self.assertEqual(
                _fast_plate_config_path(str(models / "cct_s_v2_global.onnx")).name,
                "cct_s_v2_global_plate_config.yaml",
            )

        with self.subTest("legacy recognizer has no plate-specific configuration"):
            self.assertIsNone(_fast_plate_config_path(str(models / "en_PP-OCRv5_rec_mobile.onnx")))


if __name__ == "__main__":
    unittest.main()
