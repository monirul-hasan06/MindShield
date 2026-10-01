import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
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

    def test_database_uses_user_data_directory_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            expected_path = Path(temp_dir) / "MindShield" / "mindshield.db"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, {"LOCALAPPDATA": temp_dir}),
            ):
                database_path = DatabaseHandler._default_db_path()

        self.assertEqual(database_path, expected_path)

    def test_legacy_sqlcipher_database_is_migrated_with_backup(self) -> None:
        try:
            import sqlcipher3.dbapi2 as sqlcipher
            import win32crypt
        except ImportError:
            self.skipTest("Windows SQLCipher migration dependencies are unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mindshield.db"
            key_path = Path(temp_dir) / "database.key.dpapi"
            key = os.urandom(32)
            key_path.write_bytes(
                win32crypt.CryptProtectData(
                    key,
                    "MindShield test key",
                    None,
                    None,
                    None,
                    0,
                )
            )
            with closing(sqlcipher.connect(str(path))) as connection, connection:
                connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
                connection.execute(
                    """
                    CREATE TABLE work_logs (
                        id INTEGER PRIMARY KEY,
                        date TEXT,
                        start_time TEXT,
                        duration_minutes INTEGER,
                        work_zone TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO work_logs
                        (id, date, start_time, duration_minutes, work_zone)
                    VALUES (1, ?, '09:00:00', 35, 'Study')
                    """,
                    (date.today().isoformat(),),
                )
                connection.execute(
                    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO settings VALUES ('active_work_zone', 'Study')"
                )
                connection.execute(
                    """
                    CREATE TABLE break_logs (
                        id INTEGER PRIMARY KEY,
                        date TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        duration_seconds INTEGER NOT NULL,
                        work_zone TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO break_logs
                        (id, date, started_at, duration_seconds, work_zone, completed_at)
                    VALUES (1, ?, ?, 20, 'Study', ?)
                    """,
                    (
                        date.today().isoformat(),
                        "2026-10-01T09:20:00",
                        "2026-10-01T09:20:20",
                    ),
                )
            encrypted_bytes = path.read_bytes()

            database = DatabaseHandler(path, key_path)

            backup = path.with_name("mindshield.db.sqlcipher.bak")
            self.assertEqual(backup.read_bytes(), encrypted_bytes)
            self.assertEqual(path.read_bytes()[:16], b"SQLite format 3\x00")
            self.assertEqual(database.get_todays_total_minutes(), 35)
            self.assertEqual(database.get_setting("active_work_zone"), "Study")
            self.assertEqual(database.get_monthly_break_compliance(), (1, 1))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM work_logs").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM break_logs").fetchone()[0],
                    1,
                )

    def test_new_database_uses_standard_sqlite_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mindshield.db"

            DatabaseHandler(path)

            self.assertEqual(path.read_bytes()[:16], b"SQLite format 3\x00")
            with closing(sqlite3.connect(path)) as connection:
                table_names = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name IN "
                        "('work_logs', 'settings', 'break_logs')"
                    )
                ]
                columns_by_table = {
                    table: {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    }
                    for table in table_names
                }

            self.assertEqual(
                columns_by_table["work_logs"],
                {"id", "date", "start_time", "duration_minutes", "work_zone"},
            )
            self.assertEqual(columns_by_table["settings"], {"key", "value"})
            self.assertEqual(
                columns_by_table["break_logs"],
                {
                    "id",
                    "date",
                    "started_at",
                    "duration_seconds",
                    "work_zone",
                    "completed_at",
                },
            )

    def test_log_session_updates_today_and_weekly_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mindshield.db"
            database = DatabaseHandler(path)

            database.log_session(35, "  Study  ")
            database.log_session(25, "Reading")

            self.assertEqual(database.get_todays_total_minutes(), 60)
            weekly_logs = database.get_weekly_logs()
            self.assertEqual(len(weekly_logs), 7)
            self.assertEqual(weekly_logs[-1], (date.today().isoformat(), 60))
            with closing(sqlite3.connect(path)) as connection:
                work_zones = [
                    row[0]
                    for row in connection.execute(
                        "SELECT work_zone FROM work_logs ORDER BY id"
                    )
                ]
            self.assertEqual(work_zones, ["Study", "Reading"])

    def test_focus_sessions_can_be_added_edited_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.add_work_log("2026-09-30", "09:15", 25, "Study")

            [(log_id, log_date, start_time, duration, zone)] = database.get_work_logs()
            self.assertEqual(
                (log_date, start_time, duration, zone),
                ("2026-09-30", "09:15:00", 25, "Study"),
            )

            database.update_work_log(log_id, date.today().isoformat(), "10:30", 40, "Writing")

            [(updated_id, updated_date, updated_time, updated_duration, updated_zone)] = (
                database.get_work_logs()
            )
            self.assertEqual(
                (
                    updated_id,
                    updated_date,
                    updated_time,
                    updated_duration,
                    updated_zone,
                ),
                (log_id, date.today().isoformat(), "10:30:00", 40, "Writing"),
            )
            self.assertEqual(database.get_todays_total_minutes(), 40)

            database.delete_work_log(log_id)

            self.assertEqual(database.get_work_logs(), [])
            self.assertEqual(database.get_todays_total_minutes(), 0)

    def test_work_log_editor_rejects_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                database.add_work_log("not-a-date", "09:00", 25, "Study")
            with self.assertRaisesRegex(ValueError, "HH:MM"):
                database.add_work_log("2026-09-30", "9am", 25, "Study")
            with self.assertRaisesRegex(ValueError, "positive integer"):
                database.add_work_log("2026-09-30", "09:00", 0, "Study")
            with self.assertRaisesRegex(ValueError, "non-empty string"):
                database.add_work_log("2026-09-30", "09:00", 25, " ")

    def test_reset_analytics_clears_history_and_does_not_reseed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.seed_demo_data()
            database.set_settings({"active_work_zone": "Writing"})
            database.log_scheduled_break_start(20, "Writing")

            database.reset_analytics()
            database.seed_demo_data()

            self.assertEqual(database.get_work_logs(), [])
            self.assertEqual(database.get_todays_total_minutes(), 0)
            self.assertEqual(
                database.get_weekly_logs(),
                [
                    ((date.today() - timedelta(days=6 - offset)).isoformat(), 0)
                    for offset in range(7)
                ],
            )
            self.assertEqual(database.get_monthly_break_compliance(), (0, 0))
            self.assertEqual(database.get_setting("active_work_zone"), "Writing")

    def test_existing_plain_database_keeps_data(self) -> None:
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
            self.assertEqual(path.read_bytes()[:16], b"SQLite format 3\x00")

    def test_encrypted_database_is_rejected_without_modification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mindshield.db"
            encrypted_bytes = b"SQLCipher legacy database data"
            path.write_bytes(encrypted_bytes)

            with self.assertRaisesRegex(RuntimeError, "Back up and migrate"):
                DatabaseHandler(path)

            self.assertEqual(path.read_bytes(), encrypted_bytes)

    def test_monthly_focus_and_break_compliance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.log_session(35, "Study")
            break_id = database.log_scheduled_break_start(20, "Study")

            self.assertEqual(database.get_monthly_total_minutes(), 35)
            self.assertEqual(database.get_monthly_break_compliance(), (0, 1))

            database.complete_scheduled_break(break_id)
            self.assertEqual(database.get_monthly_break_compliance(), (1, 1))

    def test_scheduled_breaks_can_be_edited_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            break_id = database.add_break_log(
                "2026-10-01",
                "09:00",
                20,
                "Study",
                False,
            )

            database.update_break_log(
                break_id,
                date.today().isoformat(),
                "10:15",
                30,
                "Reading",
                True,
            )

            [(updated_id, break_date, started_at, seconds, zone, completed)] = (
                database.get_break_logs()
            )
            self.assertEqual(updated_id, break_id)
            self.assertEqual(break_date, date.today().isoformat())
            self.assertTrue(started_at.endswith("T10:15:00"))
            self.assertEqual(seconds, 30)
            self.assertEqual(zone, "Reading")
            self.assertTrue(completed)
            self.assertEqual(database.get_monthly_break_compliance(), (1, 1))

            database.delete_break_log(break_id)

            self.assertEqual(database.get_break_logs(), [])
            self.assertEqual(database.get_monthly_break_compliance(), (0, 0))

    def test_demo_data_can_be_seeded_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            database.seed_demo_data()
            first_total = sum(minutes for _, minutes in database.get_weekly_logs())
            first_week = database.get_weekly_logs()
            database.seed_demo_data()
            second_total = sum(minutes for _, minutes in database.get_weekly_logs())

            self.assertEqual(first_total, 485)
            self.assertEqual(len(first_week), 7)
            self.assertTrue(all(minutes > 0 for _, minutes in first_week))
            self.assertEqual(second_total, first_total)


if __name__ == "__main__":
    unittest.main()
