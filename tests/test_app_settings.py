import tempfile
import unittest
from pathlib import Path

from src.database.db_handler import DatabaseHandler
from src.gui.app_settings import load_app_settings


class AppSettingsRecoveryTests(unittest.TestCase):
    def test_invalid_saved_settings_fall_back_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DatabaseHandler(Path(temp_dir) / "mindshield.db")
            database.set_settings(
                {
                    "appearance_mode": "blue",
                    "meeting_mode_minutes": "15",
                    "focus_guard_enabled": "yes",
                    "work_interval_minutes": "later",
                }
            )

            settings = load_app_settings(database)

        self.assertEqual(settings.appearance_mode, "dark")
        self.assertEqual(settings.meeting_mode_minutes, 60)
        self.assertFalse(settings.focus_guard_enabled)
        self.assertEqual(settings.work_interval_minutes, 20)
        self.assertGreaterEqual(len(settings.warnings), 4)


if __name__ == "__main__":
    unittest.main()
