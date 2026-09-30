import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.database.db_handler import DatabaseHandler


class DatabaseHandlerSettingsTests(unittest.TestCase):
    def test_settings_can_be_saved_and_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            self.assertIsNone(database.get_setting("active_work_zone"))
            database.set_settings(
                {
                    "active_work_zone": "Visual Studio Code",
                    "work_interval_minutes": "45",
                }
            )
            database.set_settings({"work_interval_minutes": "60"})

            self.assertEqual(
                database.get_setting("active_work_zone"),
                "Visual Studio Code",
            )
            self.assertEqual(database.get_setting("work_interval_minutes"), "60")

    def test_invalid_setting_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            with self.assertRaises(ValueError):
                database.get_setting(" ")
            with self.assertRaises(ValueError):
                database.set_settings({"": "value"})

    def test_packaged_database_uses_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, {"LOCALAPPDATA": temp_dir}),
            ):
                database = DatabaseHandler()

            self.assertEqual(
                database.db_path,
                Path(temp_dir) / "MindShield" / "mindshield.db",
            )
            self.assertTrue(database.db_path.is_file())

    def test_new_database_is_encrypted_with_user_protected_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mindshield.db"
            database = DatabaseHandler(path)

            self.assertNotEqual(path.read_bytes()[:16], b"SQLite format 3\x00")
            self.assertNotEqual(
                database.key_path.read_bytes(),
                database._encryption_key,
            )
            with self.assertRaises(sqlite3.DatabaseError):
                plain_connection = sqlite3.connect(path)
                try:
                    plain_connection.execute("SELECT * FROM settings").fetchall()
                finally:
                    plain_connection.close()

    def test_existing_plain_database_is_encrypted_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mindshield.db"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE work_logs "
                    "(id INTEGER PRIMARY KEY, date TEXT, start_time TEXT, "
                    "duration_minutes INTEGER, work_zone TEXT)"
                )
                connection.execute(
                    "INSERT INTO work_logs VALUES (1, ?, '09:00:00', 35, 'Study')",
                    (date.today().isoformat(),),
                )
                connection.execute(
                    "CREATE TABLE settings (key TEXT UNIQUE, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO settings VALUES ('active_work_zone', 'Study')"
                )

            database = DatabaseHandler(path)

            self.assertEqual(database.get_setting("active_work_zone"), "Study")
            self.assertEqual(database.get_todays_total_minutes(), 35)
            self.assertNotEqual(path.read_bytes()[:16], b"SQLite format 3\x00")

    def test_monthly_focus_and_break_compliance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.log_session(35, "Study")
            break_id = database.log_scheduled_break_start(20, "Study")

            self.assertEqual(database.get_monthly_total_minutes(), 35)
            self.assertEqual(database.get_monthly_break_compliance(), (0, 1))

            database.complete_scheduled_break(break_id)
            self.assertEqual(database.get_monthly_break_compliance(), (1, 1))


if __name__ == "__main__":
    unittest.main()
