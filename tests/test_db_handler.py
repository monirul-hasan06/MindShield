import csv
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from src.database.db_handler import DatabaseHandler


class DatabaseHandlerSettingsTests(unittest.TestCase):
    def test_profile_name_is_saved_in_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            database.set_settings({"user_name": "Monirul Hasan Mithu"})

            self.assertEqual(
                database.get_setting("user_name"),
                "Monirul Hasan Mithu",
            )

    def test_settings_can_be_saved_and_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            self.assertEqual(database.get_setting("active_work_zone"), "Focus")
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

    def test_legacy_work_zone_migrates_to_an_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mindshield.db"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE work_logs "
                    "(id INTEGER PRIMARY KEY, date TEXT, start_time TEXT, "
                    "duration_minutes INTEGER, work_zone TEXT)"
                )
                connection.execute(
                    "CREATE TABLE settings (key TEXT UNIQUE, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO settings VALUES ('active_work_zone', 'Study')"
                )
            database = DatabaseHandler(path)

            profile = database.get_active_work_zone_profile()
            self.assertEqual(profile.name, "Study")
            self.assertEqual(
                [(rule.match_type, rule.pattern, rule.enabled) for rule in profile.rules],
                [("any", "Study", True)],
            )

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
                connection.execute(
                    "CREATE TABLE break_logs "
                    "(id INTEGER PRIMARY KEY, date TEXT NOT NULL, "
                    "started_at TEXT NOT NULL, duration_seconds INTEGER NOT NULL, "
                    "work_zone TEXT NOT NULL, completed_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO break_logs VALUES "
                    "(1, ?, '2026-01-01T09:00:00', 15, 'Study', "
                    "'2026-01-01T09:00:15')",
                    (date.today().isoformat(),),
                )

            database = DatabaseHandler(path)

            self.assertEqual(database.get_setting("active_work_zone"), "Study")
            self.assertEqual(database.get_todays_total_minutes(), 35)
            self.assertEqual(
                database.get_productivity_summary().work_seconds,
                35 * 60,
            )
            self.assertEqual(
                database.get_productivity_summary().break_seconds,
                15,
            )
            self.assertEqual(database.get_monthly_break_compliance(), (1, 1))
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

    def test_new_database_has_no_demo_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            self.assertEqual(database.get_todays_total_minutes(), 0)
            self.assertEqual(sum(total for _, total in database.get_weekly_logs()), 0)

    def test_work_zone_profiles_save_activate_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            default_profile = database.get_active_work_zone_profile()

            profile_id = database.save_work_zone_profile(
                "Study",
                [
                    ("title", "Lecture Notes", True),
                    ("process", "browser.exe", False),
                ],
            )
            profile = database.set_active_work_zone_profile(profile_id)
            self.assertEqual(profile.name, "Study")
            self.assertEqual(len(profile.rules), 2)
            self.assertEqual(database.get_setting("active_work_zone"), "Study")

            database.save_work_zone_profile(
                "Study",
                [("path", r"C:\School", True)],
                profile_id,
            )
            updated_profile = database.get_active_work_zone_profile()
            self.assertEqual(
                [(rule.match_type, rule.pattern) for rule in updated_profile.rules],
                [("path", r"C:\School")],
            )

            database.delete_work_zone_profile(profile_id)
            self.assertEqual(
                database.get_active_work_zone_profile().id,
                default_profile.id,
            )
            with self.assertRaises(ValueError):
                database.delete_work_zone_profile(default_profile.id)

    def test_work_zone_profile_rejects_disabled_or_invalid_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")

            with self.assertRaises(ValueError):
                database.save_work_zone_profile(
                    "Empty",
                    [("title", "Notes", False)],
                )
            with self.assertRaises(ValueError):
                database.save_work_zone_profile(
                    "Invalid",
                    [("unknown", "Notes", True)],
                )

    def test_focus_guard_rules_are_persisted_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            rules = [
                ("accessible", "youtube.com", True),
                ("process", "games.exe", False),
            ]

            database.save_focus_guard_rules(rules)

            self.assertEqual(
                [
                    (rule.match_type, rule.pattern, rule.enabled)
                    for rule in database.get_focus_guard_rules()
                ],
                rules,
            )
            with self.assertRaises(ValueError):
                database.save_focus_guard_rules(
                    [("unknown", "blocked", True)]
                )

    def test_productivity_summary_counts_work_and_completed_manual_breaks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.log_session(20, "Study")
            database.log_work_segment(95, "Study")

            manual_break = database.log_scheduled_break_start(
                300,
                "Study",
                break_type="manual",
            )
            database.complete_scheduled_break(manual_break)
            automatic_break = database.log_scheduled_break_start(20, "Study")
            database.complete_scheduled_break(automatic_break)
            database.log_scheduled_break_start(600, "Study")

            summary = database.get_productivity_summary()
            self.assertEqual(summary.work_seconds, 20 * 60 + 95)
            self.assertEqual(summary.break_seconds, 320)
            self.assertEqual(database.get_monthly_break_compliance(), (1, 2))

    def test_reset_analytics_clears_history_and_preserves_settings_and_active_break(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.set_settings({"user_name": "Monirul"})
            database.log_session(25, "Study")
            completed_break = database.log_scheduled_break_start(300, "Study")
            database.complete_scheduled_break(completed_break)
            active_break = database.log_scheduled_break_start(900, "Study")

            database.reset_analytics(preserve_break_id=active_break)

            summary = database.get_productivity_summary()
            self.assertEqual(summary.work_seconds, 0)
            self.assertEqual(summary.break_seconds, 0)
            self.assertEqual(database.get_session_history(), [])
            self.assertEqual(database.get_setting("user_name"), "Monirul")
            self.assertEqual(database.get_monthly_break_compliance(), (0, 1))

            database.complete_scheduled_break(active_break)
            self.assertEqual(
                database.get_productivity_summary().break_seconds,
                900,
            )

    def test_reset_analytics_removes_activity_when_no_break_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.log_session(25, "Study")
            break_id = database.log_scheduled_break_start(300, "Study")
            database.complete_scheduled_break(break_id)

            database.reset_analytics()

            self.assertEqual(database.get_session_history(), [])
            self.assertEqual(database.get_productivity_summary().work_seconds, 0)
            self.assertEqual(database.get_productivity_summary().break_seconds, 0)
            self.assertEqual(database.get_monthly_break_compliance(), (0, 0))

    def test_session_history_filters_and_csv_export_share_the_same_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            today = date.today()
            old_date = today - timedelta(days=40)
            with closing(database._connect()) as connection, connection:
                connection.executemany(
                    """
                    INSERT INTO work_logs
                        (date, start_time, duration_minutes, work_zone)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (today.isoformat(), "09:15:00", 25, "Study"),
                        (today.isoformat(), "10:00:00", 40, "Writing"),
                        (old_date.isoformat(), "08:00:00", 50, "Study"),
                    ],
                )

            filtered = database.get_session_history(
                start_date=today - timedelta(days=6),
                end_date=today,
                search="study",
            )
            self.assertEqual(
                [(record.work_zone, record.duration_minutes) for record in filtered],
                [("Study", 25)],
            )

            export_path = Path(temp_dir) / "history.csv"
            count = database.export_sessions_csv(
                export_path,
                start_date=today - timedelta(days=6),
                end_date=today,
                search="study",
            )
            with export_path.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.reader(csv_file))
            self.assertEqual(count, 1)
            self.assertEqual(
                rows,
                [
                    ["Date", "Start time", "Duration (minutes)", "Work zone"],
                    [today.isoformat(), "09:15:00", "25", "Study"],
                ],
            )

    def test_encrypted_backup_can_be_restored_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "mindshield.db"
            backup_path = Path(temp_dir) / "mindshield.msbackup"
            database = DatabaseHandler(database_path)
            database.set_settings({"active_work_zone": "Study"})
            database.log_session(35, "Study")

            database.backup_to(backup_path)

            with ZipFile(backup_path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"mindshield.db", "database.key.dpapi"},
                )
                self.assertNotEqual(
                    archive.read("mindshield.db")[:16],
                    b"SQLite format 3\x00",
                )
                self.assertNotEqual(
                    archive.read("database.key.dpapi"),
                    database._encryption_key,
                )

            database.set_settings({"active_work_zone": "Writing"})
            database.log_session(10, "Writing")
            database.restore_from(backup_path)

            self.assertEqual(database.get_setting("active_work_zone"), "Study")
            self.assertEqual(database.get_todays_total_minutes(), 35)

    def test_invalid_backup_does_not_replace_current_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            backup_path = Path(temp_dir) / "invalid.msbackup"
            database.set_settings({"active_work_zone": "Study"})
            with ZipFile(backup_path, "w") as archive:
                archive.writestr("not-a-backup.txt", "invalid")

            with self.assertRaises(ValueError):
                database.restore_from(backup_path)

            self.assertEqual(database.get_setting("active_work_zone"), "Study")


if __name__ == "__main__":
    unittest.main()
