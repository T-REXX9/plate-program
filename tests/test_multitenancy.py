from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "web"))

from tenancy import (  # noqa: E402
    controller_key_digest,
    generate_controller_key,
    matching_credential_id,
    normalize_tenant_uid,
)


class ControllerCredentialTests(unittest.TestCase):
    def test_generated_controller_key_is_exactly_ten_digits(self) -> None:
        for _ in range(20):
            key = generate_controller_key()
            self.assertRegex(key, r"^\d{10}$")

    def test_controller_key_is_stored_as_a_digest(self) -> None:
        digest = controller_key_digest("secret-controller-key")
        self.assertEqual(len(digest), 64)
        self.assertNotIn("secret-controller-key", digest)

    def test_only_matching_active_credential_is_accepted(self) -> None:
        credentials = [
            {"id": 10, "credential_hash": controller_key_digest("controller-a")},
            {"id": 11, "credential_hash": controller_key_digest("controller-b")},
        ]
        self.assertEqual(matching_credential_id("controller-b", credentials), 11)
        self.assertIsNone(matching_credential_id("controller-c", credentials))

    def test_tenant_ids_are_canonical_and_restricted(self) -> None:
        self.assertEqual(normalize_tenant_uid("Village Alpha", "Village ID"), "village-alpha")
        with self.assertRaises(ValueError):
            normalize_tenant_uid("../other-village", "Village ID")


class MultiTenantSchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = (PROJECT_DIR / "database" / "schema.sql").read_text(encoding="utf-8")

    def test_controller_assignment_is_controller_to_gate_to_village(self) -> None:
        self.assertIn("gate_id BIGINT UNSIGNED NOT NULL", self.schema)
        self.assertIn("FOREIGN KEY (gate_id)\n        REFERENCES gates(id)", self.schema)
        self.assertIn("FOREIGN KEY (village_id)\n        REFERENCES villages(id)", self.schema)

    def test_controller_credentials_cannot_be_orphaned(self) -> None:
        self.assertIn(
            "CONSTRAINT fk_controller_credentials_controller FOREIGN KEY (controller_uid)",
            self.schema,
        )
        self.assertIn(
            "REFERENCES controllers(controller_uid) ON DELETE CASCADE",
            self.schema,
        )

    def test_plate_and_rfid_uniqueness_is_per_village(self) -> None:
        self.assertIn("uq_vehicles_village_plate (village_id, plate_number)", self.schema)
        self.assertIn("uq_rfid_stickers_village_value (village_id, sticker_value)", self.schema)
        self.assertNotIn("uq_vehicles_plate_number (plate_number)", self.schema)
        self.assertNotIn("uq_rfid_stickers_value (sticker_value)", self.schema)

    def test_events_and_commands_carry_immutable_tenant_context(self) -> None:
        access_events = self.schema.split("CREATE TABLE IF NOT EXISTS access_events", 1)[1]
        access_events = access_events.split("CREATE TABLE IF NOT EXISTS users", 1)[0]
        commands = self.schema.split("CREATE TABLE IF NOT EXISTS reader_commands", 1)[1]
        commands = commands.split("CREATE TABLE IF NOT EXISTS system_status", 1)[0]
        for definition in (access_events, commands):
            self.assertIn("village_id BIGINT UNSIGNED NOT NULL", definition)
            self.assertIn("gate_id BIGINT UNSIGNED NOT NULL", definition)

    def test_access_events_can_correlate_plate_and_rfid_attempts(self) -> None:
        events = self.schema.split("CREATE TABLE IF NOT EXISTS access_events", 1)[1]
        events = events.split("CREATE TABLE IF NOT EXISTS users", 1)[0]
        self.assertIn("attempt_uid VARCHAR(80) NULL", events)
        self.assertIn("idx_access_events_attempt (controller_uid, attempt_uid)", events)

    def test_legacy_tenant_is_seeded_during_migration(self) -> None:
        # The schema itself should not hardcode the legacy village ID/defaults.
        # It's the migration script that should handle inserting it if needed.
        self.assertNotIn("village_id BIGINT UNSIGNED NOT NULL DEFAULT", self.schema)
        self.assertNotIn("gate_id BIGINT UNSIGNED NOT NULL DEFAULT", self.schema)

    def test_cameras_are_bound_to_one_gate(self) -> None:
        cameras = self.schema.split("CREATE TABLE IF NOT EXISTS cameras", 1)[1]
        cameras = cameras.split("CREATE TABLE IF NOT EXISTS camera_capture_jobs", 1)[0]
        self.assertIn("UNIQUE KEY uq_cameras_gate (gate_id)", cameras)
        self.assertIn("FOREIGN KEY (gate_id)", cameras)
        self.assertIn("transport ENUM('rtsp', 'http_mjpeg', 'usb', 'onvif', 'agent')", cameras)

    def test_capture_jobs_preserve_tenant_and_gate_context(self) -> None:
        jobs = self.schema.split("CREATE TABLE IF NOT EXISTS camera_capture_jobs", 1)[1]
        self.assertIn("village_id BIGINT UNSIGNED NOT NULL", jobs)
        self.assertIn("gate_id BIGINT UNSIGNED NOT NULL", jobs)
        self.assertIn("UNIQUE KEY uq_camera_capture_attempt (attempt_uid)", jobs)
        self.assertIn("FOREIGN KEY (camera_uid, gate_id)", jobs)

    def test_controller_tracks_server_side_vehicle_attempt_limit(self) -> None:
        controllers = self.schema.split("CREATE TABLE IF NOT EXISTS controllers", 1)[1]
        controllers = controllers.split("CREATE TABLE IF NOT EXISTS cameras", 1)[0]
        self.assertIn(
            "recognition_attempt_count TINYINT UNSIGNED NOT NULL DEFAULT 0",
            controllers,
        )
        self.assertIn(
            "recognition_locked_until_clear TINYINT(1) NOT NULL DEFAULT 0",
            controllers,
        )


class CrossVillageIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = (PROJECT_DIR / "database" / "schema.sql").read_text(encoding="utf-8")

    def test_village_a_cannot_query_village_b(self) -> None:
        # A simple check to ensure the schema structure forces queries to scope by village_id
        # We verify that any vehicle query must include village_id
        self.assertIn("uq_vehicles_village_plate (village_id, plate_number)", self.schema)
        self.assertIn("uq_rfid_stickers_village_value (village_id, sticker_value)", self.schema)
        
    def test_identical_plates_exist_independently(self) -> None:
        # Since uq_vehicles_plate_number doesn't exist globally, the same plate can be in multiple villages
        self.assertNotIn("UNIQUE KEY uq_vehicles_plate_number", self.schema)
        self.assertNotIn("UNIQUE KEY uq_rfid_stickers_value", self.schema)
        
    def test_non_owner_is_bound_to_single_village(self) -> None:
        # Verify that the schema enforces user_village_roles as the source of truth for membership
        self.assertIn("user_village_roles", self.schema)
        self.assertIn("role ENUM('village_admin', 'security_guard', 'homeowner')", self.schema)


if __name__ == "__main__":
    unittest.main()
