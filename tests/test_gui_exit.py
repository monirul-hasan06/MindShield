import threading
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from src.gui.main_window import MainWindow, sqlite3 as database_sqlite3


class SaveWorkOnExitTests(unittest.TestCase):
    def _window(self, state: str, elapsed: float) -> SimpleNamespace:
        timer = Mock()
        timer.state = state
        timer.last_completed_work_sec = elapsed

        def pause_work() -> None:
            timer.state = "paused"

        timer.pause_work.side_effect = pause_work
        window = SimpleNamespace(
            timer_manager=timer,
            active_work_zone="Writing",
            _pending_work_segments=[],
            _activity_data_lock=threading.Lock(),
            database=Mock(),
        )
        window._record_work_segment_locked = MethodType(
            MainWindow._record_work_segment_locked,
            window,
        )
        return window

    def test_exit_saves_active_work_once(self) -> None:
        window = self._window("work", 37.2)

        self.assertTrue(MainWindow._save_work_before_exit(window))
        self.assertTrue(MainWindow._save_work_before_exit(window))

        window.database.log_work_segment.assert_called_once_with(37, "Writing")
        self.assertEqual(window._pending_work_segments, [])

    def test_failed_exit_save_keeps_pending_work_for_retry(self) -> None:
        window = self._window("work", 37)
        window.database.log_work_segment.side_effect = [
            database_sqlite3.Error("database unavailable"),
            None,
        ]

        with patch("src.gui.main_window.messagebox.showerror") as showerror:
            self.assertFalse(MainWindow._save_work_before_exit(window))
            self.assertEqual(window._pending_work_segments, [(37, "Writing")])
            self.assertTrue(MainWindow._save_work_before_exit(window))

        self.assertEqual(window.database.log_work_segment.call_count, 2)
        self.assertEqual(window._pending_work_segments, [])
        showerror.assert_called_once()


if __name__ == "__main__":
    unittest.main()
