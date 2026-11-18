import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.gui.main_window import MainWindow
from src.gui.theme import COLORS


class BreakControlsTests(unittest.TestCase):
    def test_paused_break_displays_resume_and_back_to_work_actions(self) -> None:
        window = SimpleNamespace(
            _last_displayed_timer_state=None,
            phase_label=Mock(),
            timer_progress=Mock(),
            start_button=Mock(),
            pause_button=Mock(),
            stop_button=Mock(),
            active_work_zone="Study",
            status_label=Mock(),
            _overlay=None,
            _update_countdown=Mock(),
            _stop_work=Mock(),
            _return_to_work_early=Mock(),
        )

        MainWindow._display_timer_state(window, "break_paused", 42)

        window.phase_label.configure.assert_called_once_with(
            text="BREAK PAUSED",
            text_color=COLORS["success"],
        )
        window.pause_button.configure.assert_called_once_with(
            state="normal",
            text="Resume Break",
        )
        window.stop_button.configure.assert_called_once_with(
            state="normal",
            text="Back to Work",
            command=window._return_to_work_early,
        )
        window._update_countdown.assert_called_once_with(42)

    def test_ending_a_break_early_does_not_mark_it_complete(self) -> None:
        window = SimpleNamespace(
            window_tracker=Mock(),
            _closing=False,
            _work_started=True,
            _window_tracking_started=True,
            _manual_break_active=True,
            _activity_data_lock=threading.Lock(),
            _scheduled_break_id=17,
            database=Mock(),
            after=Mock(),
            _refresh_analytics=Mock(),
            _close_break_overlay=Mock(),
            _return_to_work_app=Mock(),
        )

        MainWindow._finish_break(window, completed=False)

        window.database.complete_scheduled_break.assert_not_called()
        self.assertIsNone(window._scheduled_break_id)
        self.assertFalse(window._manual_break_active)
        window.window_tracker.set_manual_break.assert_called_once_with(False)
        window.after.assert_any_call(0, window._close_break_overlay)
        window.after.assert_any_call(0, window._return_to_work_app)


if __name__ == "__main__":
    unittest.main()
