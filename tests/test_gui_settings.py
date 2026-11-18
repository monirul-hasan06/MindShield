import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.gui.main_window import MainWindow


class EyeCareSettingsTests(unittest.TestCase):
    def _window(self, interval: str, break_length: str) -> SimpleNamespace:
        return SimpleNamespace(
            work_interval_entry=Mock(get=Mock(return_value=interval)),
            break_duration_entry=Mock(get=Mock(return_value=break_length)),
            database=Mock(),
            timer_manager=Mock(),
            _work_interval_minutes=20,
            _break_duration_seconds=20,
            _demo_mode_enabled=Mock(return_value=False),
            focus_duration_entry=Mock(),
            status_label=Mock(),
            _set_countdown_display=Mock(),
        )

    def test_saves_focus_and_linked_eye_care_timing(self) -> None:
        window = self._window("45", "30")

        MainWindow._apply_timer_settings(window)

        window.database.set_settings.assert_called_once_with(
            {
                "work_interval_minutes": "45",
                "break_duration_seconds": "30",
            }
        )
        window.timer_manager.configure_intervals.assert_called_once_with(
            45 * 60,
            30,
        )
        self.assertEqual(window._work_interval_minutes, 45)
        self.assertEqual(window._break_duration_seconds, 30)
        window._set_countdown_display.assert_called_once_with(45 * 60)

    def test_rejects_out_of_range_interval_without_saving(self) -> None:
        window = self._window("241", "30")

        MainWindow._apply_timer_settings(window)

        window.database.set_settings.assert_not_called()
        window.timer_manager.configure_intervals.assert_not_called()
        window.status_label.configure.assert_called_once()

    def test_rejects_nonpositive_eye_care_break_length(self) -> None:
        window = self._window("20", "0")

        MainWindow._apply_timer_settings(window)

        window.database.set_settings.assert_not_called()
        window.timer_manager.configure_intervals.assert_not_called()
        window.status_label.configure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
