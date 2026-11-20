import threading
import time
import unittest

from src.core.timer_manager import TimerManager


class TimerManagerTests(unittest.TestCase):
    def test_custom_focus_duration_sets_the_next_work_interval(self) -> None:
        timer = TimerManager(work_interval_sec=30, break_duration_sec=5)
        try:
            timer.start_work(duration_sec=60)
            self.assertEqual(timer.work_interval_sec, 60)
            self.assertGreaterEqual(timer.remaining_sec, 59)

            timer.start_work(duration_sec=120)
            self.assertEqual(timer.work_interval_sec, 60)
        finally:
            timer.stop(timeout=2)

    def test_pause_completes_a_work_segment_without_counting_paused_time(self) -> None:
        timer = TimerManager(work_interval_sec=30, break_duration_sec=5)
        try:
            timer.start_work()
            time.sleep(0.03)
            timer.pause_work()
            completed_segment = timer.last_completed_work_sec
            time.sleep(0.05)

            self.assertGreater(completed_segment, 0)
            self.assertIsNotNone(timer.last_completed_work_started_at)
            self.assertEqual(timer.current_work_elapsed_sec, 0)
            self.assertEqual(timer.last_completed_work_sec, completed_segment)

            timer.start_work()
            self.assertEqual(timer.state, "work")
        finally:
            timer.stop(timeout=2)

    def test_reset_productivity_tracking_keeps_the_work_countdown_running(self) -> None:
        timer = TimerManager(work_interval_sec=30, break_duration_sec=5)
        try:
            timer.start_work()
            time.sleep(0.03)
            remaining_before_reset = timer.remaining_sec

            timer.reset_productivity_tracking()
            time.sleep(0.03)
            timer.pause_work()

            self.assertEqual(timer.state, "paused")
            self.assertLess(timer.last_completed_work_sec, 0.2)
            self.assertGreater(timer.remaining_sec, 0)
            self.assertLessEqual(timer.remaining_sec, remaining_before_reset)
        finally:
            timer.stop(timeout=2)

    def test_pause_and_resume_preserve_work_state(self) -> None:
        timer = TimerManager(work_interval_sec=30, break_duration_sec=5)
        try:
            timer.start_work()
            timer.pause_work()
            self.assertEqual(timer.state, "paused")

            timer.start_work()
            self.assertEqual(timer.state, "work")
        finally:
            timer.stop(timeout=2)

    def test_pause_and_resume_preserve_manual_break_countdown(self) -> None:
        break_started = threading.Event()
        timer = TimerManager(
            work_interval_sec=30,
            break_duration_sec=5,
            on_break_start=break_started.set,
        )
        try:
            timer.start_manual_break(1)
            self.assertTrue(break_started.wait(2))
            timer.pause_break()
            paused_remaining = timer.remaining_sec
            time.sleep(0.03)

            self.assertEqual(timer.state, "break_paused")
            self.assertTrue(timer.is_on_manual_break)
            self.assertEqual(timer.remaining_sec, paused_remaining)

            timer.resume_break()
            self.assertEqual(timer.state, "manual_break")
            self.assertLessEqual(timer.remaining_sec, paused_remaining)
        finally:
            timer.stop(timeout=2)

    def test_pause_and_resume_preserve_scheduled_break_countdown(self) -> None:
        break_started = threading.Event()
        timer = TimerManager(
            work_interval_sec=0.05,
            break_duration_sec=5,
            on_break_start=break_started.set,
        )
        try:
            timer.start_work()
            self.assertTrue(break_started.wait(2))
            timer.pause_break()
            paused_remaining = timer.remaining_sec
            time.sleep(0.03)

            self.assertEqual(timer.state, "break_paused")
            self.assertFalse(timer.is_on_manual_break)
            self.assertEqual(timer.remaining_sec, paused_remaining)

            timer.resume_break()
            self.assertEqual(timer.state, "break")
            self.assertLessEqual(timer.remaining_sec, paused_remaining)
        finally:
            timer.stop(timeout=2)

    def test_ending_manual_break_early_resumes_saved_focus_time(self) -> None:
        early_end = threading.Event()
        completed = threading.Event()
        break_started = threading.Event()
        timer = TimerManager(
            work_interval_sec=30,
            break_duration_sec=5,
            on_break_start=break_started.set,
            on_break_end=completed.set,
            on_break_end_early=early_end.set,
        )
        try:
            timer.start_work()
            time.sleep(0.03)
            saved_remaining = timer.remaining_sec
            timer.start_manual_break(1)
            self.assertTrue(break_started.wait(2))

            self.assertTrue(timer.return_to_work())
            self.assertTrue(early_end.wait(2))
            self.assertEqual(timer.state, "work")
            self.assertLessEqual(timer.remaining_sec, saved_remaining)
            self.assertGreaterEqual(timer.remaining_sec, saved_remaining - 1)
            self.assertFalse(completed.is_set())
        finally:
            timer.stop(timeout=2)

    def test_ending_scheduled_break_early_starts_a_fresh_focus_interval(self) -> None:
        early_end = threading.Event()
        break_started = threading.Event()
        timer = TimerManager(
            work_interval_sec=1,
            break_duration_sec=5,
            on_break_start=break_started.set,
            on_break_end_early=early_end.set,
        )
        try:
            timer.start_work()
            self.assertTrue(break_started.wait(2))

            self.assertTrue(timer.return_to_work())
            self.assertTrue(early_end.wait(2))
            self.assertEqual(timer.state, "work")
            self.assertGreaterEqual(timer.remaining_sec, 1)
        finally:
            timer.stop(timeout=2)

    def test_work_and_break_phases_repeat(self) -> None:
        break_started = threading.Event()
        break_ended = threading.Event()
        timer = TimerManager(
            work_interval_sec=0.1,
            break_duration_sec=0.1,
            on_break_start=break_started.set,
            on_break_end=break_ended.set,
        )
        try:
            timer.start_work()
            self.assertTrue(break_started.wait(2))
            self.assertTrue(break_ended.wait(2))
            self.assertEqual(timer.state, "work")
        finally:
            timer.stop(timeout=2)

    def test_paused_time_is_not_counted_in_completed_work_duration(self) -> None:
        break_started = threading.Event()
        timer = TimerManager(
            work_interval_sec=0.3,
            break_duration_sec=5,
            on_break_start=break_started.set,
        )
        try:
            timer.start_work()
            time.sleep(0.05)
            timer.pause_work()
            time.sleep(0.25)
            timer.start_work()

            self.assertTrue(break_started.wait(2))
            self.assertLess(timer.last_completed_work_sec, 0.5)
        finally:
            timer.stop(timeout=2)

    def test_callback_failure_is_logged_without_stopping_timer(self) -> None:
        recovered = threading.Event()
        should_fail = True

        def update_state(_state: str, _remaining: int) -> None:
            nonlocal should_fail
            if should_fail:
                should_fail = False
                raise RuntimeError("callback failure")
            recovered.set()

        timer = TimerManager(
            work_interval_sec=10,
            break_duration_sec=5,
            on_state_update=update_state,
        )
        try:
            with self.assertLogs("src.core.timer_manager", level="ERROR"):
                timer.start_work()
                self.assertTrue(recovered.wait(2))
            self.assertEqual(timer.state, "work")
        finally:
            timer.stop(timeout=2)


if __name__ == "__main__":
    unittest.main()
