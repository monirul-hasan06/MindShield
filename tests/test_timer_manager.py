import threading
import time
import unittest
from collections.abc import Callable

from src.core.timer_manager import TimerManager


class TimerManagerTests(unittest.TestCase):
    def make_timer(
        self,
        *,
        work_interval_sec: int | float = 1200,
        break_duration_sec: int | float = 20,
        on_tick: Callable[[int], None] | None = None,
        on_break_start: Callable[[], None] | None = None,
        on_break_end: Callable[[], None] | None = None,
    ) -> TimerManager:
        timer = TimerManager(
            work_interval_sec=work_interval_sec,
            break_duration_sec=break_duration_sec,
            on_tick=on_tick,
            on_break_start=on_break_start,
            on_break_end=on_break_end,
        )
        self.addCleanup(timer.stop, 1)
        return timer

    def test_defaults_and_daemon_worker(self) -> None:
        timer = self.make_timer()

        self.assertEqual(timer.work_interval_sec, 1200)
        self.assertEqual(timer.break_duration_sec, 20)
        self.assertEqual(timer.remaining_sec, 1200)
        self.assertFalse(timer.is_running)
        self.assertFalse(timer.is_on_manual_break)

        timer.start_work()

        self.assertTrue(timer.is_running)
        self.assertIsNotNone(timer._thread)
        assert timer._thread is not None
        self.assertTrue(timer._thread.daemon)

    def test_tick_callback_receives_remaining_seconds(self) -> None:
        tick_received = threading.Event()
        tick_values: list[int] = []

        def on_tick(remaining_sec: int) -> None:
            tick_values.append(remaining_sec)
            tick_received.set()

        timer = self.make_timer(on_tick=on_tick)
        timer.start_work()

        self.assertTrue(tick_received.wait(2))
        self.assertGreater(tick_values[0], 0)
        self.assertLessEqual(tick_values[0], 1200)

    def test_pause_resume_and_reset(self) -> None:
        timer = self.make_timer()

        timer.start_work()
        timer.pause_work()
        paused_remaining = timer.remaining_sec

        self.assertFalse(timer.is_running)
        self.assertEqual(timer.state, "paused")
        self.assertGreater(paused_remaining, 0)
        self.assertLessEqual(paused_remaining, 1200)

        timer.start_work()

        self.assertEqual(timer.state, "work")
        self.assertTrue(timer.is_running)

        timer.reset_timer()

        self.assertEqual(timer.state, "idle")
        self.assertEqual(timer.remaining_sec, 1200)
        self.assertFalse(timer.is_running)

    def test_scheduled_break_callbacks_fire(self) -> None:
        break_started = threading.Event()
        break_ended = threading.Event()
        timer = self.make_timer(
            work_interval_sec=0.05,
            break_duration_sec=0.05,
            on_break_start=break_started.set,
            on_break_end=break_ended.set,
        )

        timer.start_work()

        self.assertTrue(break_started.wait(2))
        self.assertTrue(break_ended.wait(2))
        self.assertEqual(timer.state, "work")

    def test_manual_break_resumes_remaining_work_time(self) -> None:
        break_ended = threading.Event()
        timer = self.make_timer(
            work_interval_sec=1200,
            on_break_end=break_ended.set,
        )
        timer.start_work()
        timer.pause_work()
        with timer._condition:
            timer._remaining_when_paused = 417.25
        paused_remaining = timer.remaining_sec

        timer.start_manual_break(5)

        self.assertEqual(timer.state, "manual_break")
        self.assertTrue(timer.is_running)
        self.assertTrue(timer.is_on_manual_break)
        self.assertGreater(timer.remaining_sec, 295)
        self.assertLessEqual(timer.remaining_sec, 300)

        with timer._condition:
            timer._deadline = time.monotonic() - 1
            timer._condition.notify_all()

        self.assertTrue(break_ended.wait(2))
        self.assertEqual(timer.state, "work")
        self.assertFalse(timer.is_on_manual_break)
        self.assertLessEqual(timer.remaining_sec, paused_remaining)
        self.assertGreaterEqual(timer.remaining_sec, paused_remaining - 1)

    def test_demo_mode_sets_and_restores_intervals(self) -> None:
        timer = self.make_timer()

        timer.toggle_demo_mode(True)

        self.assertEqual(timer.work_interval_sec, 10)
        self.assertEqual(timer.break_duration_sec, 5)
        self.assertEqual(timer.remaining_sec, 10)

        timer.toggle_demo_mode(False)

        self.assertEqual(timer.work_interval_sec, 1200)
        self.assertEqual(timer.break_duration_sec, 20)
        self.assertEqual(timer.remaining_sec, 1200)


if __name__ == "__main__":
    unittest.main()
