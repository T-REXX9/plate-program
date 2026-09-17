from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from recognition import _crop_plate  # noqa: E402


class PlateCropTests(unittest.TestCase):
    def test_crop_adds_a_right_edge_margin_only(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        crop = _crop_plate(frame, [40, 20, 100, 30])

        self.assertEqual(crop.shape, (30, 113, 3))

    def test_crop_does_not_extend_past_the_frame(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        crop = _crop_plate(frame, [150, 20, 50, 30])

        self.assertEqual(crop.shape, (30, 50, 3))


if __name__ == "__main__":
    unittest.main()
