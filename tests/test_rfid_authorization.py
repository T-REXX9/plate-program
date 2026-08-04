from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from authorization import authorize_plate_and_rfid, normalize_rfid  # noqa: E402


class RfidAuthorizationTests(unittest.TestCase):
    def test_disabled_rfid_preserves_plate_only_authorization(self) -> None:
        self.assertEqual(
            authorize_plate_and_rfid(5, False, "", None),
            (True, False, "plate_authorized_rfid_disabled"),
        )

    def test_known_rfid_authorizes_even_when_plate_is_unknown(self) -> None:
        self.assertEqual(
            authorize_plate_and_rfid(None, True, "TAG1", 5),
            (True, True, "rfid_authorized"),
        )

    def test_missing_rfid_is_denied_when_required(self) -> None:
        self.assertEqual(
            authorize_plate_and_rfid(5, True, "", None)[2],
            "rfid_not_read",
        )

    def test_unregistered_rfid_is_denied(self) -> None:
        self.assertEqual(
            authorize_plate_and_rfid(5, True, "UNKNOWN", None)[2],
            "rfid_not_registered",
        )

    def test_registered_rfid_is_authorized_independently_of_plate(self) -> None:
        self.assertEqual(
            authorize_plate_and_rfid(5, True, "TAG2", 9),
            (True, True, "rfid_authorized"),
        )

    def test_matching_plate_and_rfid_are_authorized(self) -> None:
        self.assertEqual(
            authorize_plate_and_rfid(5, True, "TAG5", 5),
            (True, True, "rfid_authorized"),
        )

    def test_rfid_normalization_removes_formatting(self) -> None:
        self.assertEqual(normalize_rfid(" epc-00 12:ab "), "EPC0012AB")

    def test_binary_hex_notation_is_stored_canonically(self) -> None:
        self.assertEqual(
            normalize_rfid(
                "0x30 0x45 0x67 0x30 0x30 0x55 "
                "0x3F 0x90 0x30 0x55 0x3F 0x90"
            ),
            "3045673030553F9030553F90",
        )


if __name__ == "__main__":
    unittest.main()
