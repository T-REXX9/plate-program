import re
import unittest
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "database" / "schema.sql"


class SchemaOrderTests(unittest.TestCase):
    def test_households_exists_before_user_village_roles_references_it(self):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        households = re.search(
            r"CREATE TABLE IF NOT EXISTS households\s*\(", schema
        )
        user_village_roles = re.search(
            r"CREATE TABLE IF NOT EXISTS user_village_roles\s*\(", schema
        )

        self.assertIsNotNone(households)
        self.assertIsNotNone(user_village_roles)
        self.assertLess(households.start(), user_village_roles.start())


if __name__ == "__main__":
    unittest.main()
