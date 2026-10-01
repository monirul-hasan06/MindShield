"""Background work and break timer for MindShield."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable


class TimerManager:
    """Manage focus and break periods on a background daemon thread.

    ``on_state_update`` and the phase callbacks run on the timer thread. UI
    callbacks should dispatch their updates to the GUI event loop.
    """

    DEMO_WORK_INTERVAL_SEC = 10
    DEMO_BREAK_DURATION_SEC = 5

    def __init__(
        self,
        work_interval_sec: int | float = 1200,
        break_duration_sec: int | float = 20,
        on_tick: Callable[[int], None] | None = None,
        on_break_start: Callable[[], None] | None = None,
        on_break_end: Callable[[], None] | None = None,
        on_state_update: Callable[[str, int], None] | None = None,
    ) -> None:
        self._validate_interval("work_interval_sec", work_interval_sec)
        self._validate_interval("break_duration_sec", break_duration_sec)

        self._normal_work_interval_sec = float(work_interval_sec)
        self._normal_break_duration_sec = float(break_duration_sec)
        self._work_interval_sec = float(work_interval_sec)
        self._break_duration_sec = float(break_duration_sec)
        self.on_tick = on_tick
        self.on_break_start = on_break_start
        self.on_break_end = on_break_end
        self.on_state_update = on_state_update

        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop_requested = False
        self._demo_mode = False
        self._mode = "idle"
        self._deadline: float | None = None
        self._remaining_when_paused = self._work_interval_sec
        self._pending_events: list[str] = []

    @staticmethod
    def _validate_interval(name: str, value: int | float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be greater than zero and finite")

    @property
    def state(self) -> str:
        """Current timer phase: idle, work, break, manual_break, or paused."""
        with self._condition:
            return self._mode

    @property
    def work_interval_sec(self) -> float:
        """Configured work duration, read under the timer lock."""
        with self._condition:
            return self._work_interval_sec

    @property
    def break_duration_sec(self) -> float:
        """Configured scheduled-break duration, read under the timer lock."""
        with self._condition:
            return self._break_duration_sec

    @property
    def remaining_sec(self) -> int:
        """Current countdown value, rounded up to the next whole second."""
        with self._condition:
            return self._get_remaining_locked(time.monotonic())

    @property
    def is_running(self) -> bool:
        """Whether the timer is actively counting down."""
        with self._condition:
            return self._mode in ("work", "break", "manual_break")

    @property
    def is_on_manual_break(self) -> bool:
        """Whether a timed manual break is currently in progress."""
        with self._condition:
            return self._mode == "manual_break"

    def start_work(self) -> None:
        """Start a work interval or resume the previously paused interval."""
        with self._condition:
            if self._mode not in ("idle", "paused"):
                if self._mode == "work":
                    return
                raise RuntimeError(f"Cannot start work while timer is in {self._mode!r} state")

            if self._mode == "idle":
                self._remaining_when_paused = self._work_interval_sec
            self._mode = "work"
            self._deadline = time.monotonic() + self._remaining_when_paused
            self._start_thread_locked()
            self._pending_events.append("state")
            self._condition.notify_all()

    def start(self) -> None:
        """Backward-compatible alias for :meth:`start_work`."""
        self.start_work()

    def pause_work(self) -> None:
        """Pause work and preserve the remaining time for ``start_work``."""
        with self._condition:
            if self._mode != "work" or self._deadline is None:
                return
            self._remaining_when_paused = max(
                0.0, self._deadline - time.monotonic()
            )
            self._deadline = None
            self._mode = "paused"
            self._pending_events.append("state")
            self._condition.notify_all()

    def reset_timer(self) -> None:
        """Stop the current phase and reset the work countdown to its interval."""
        with self._condition:
            self._mode = "idle"
            self._deadline = None
            self._remaining_when_paused = self._work_interval_sec
            self._pending_events.clear()
            self._pending_events.append("state")
            self._condition.notify_all()

    def stop(self, timeout: float | None = None) -> None:
        """Stop the background worker and wait up to ``timeout`` seconds."""
        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()
            thread = self._thread

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def start_manual_break(self, minutes: int) -> None:
        """Pause focus tracking for whole minutes, then automatically resume.

        The UI offers 5-, 10-, and 15-minute breaks; other positive durations
        are accepted for actions such as the one-hour meeting mode.
        """
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise TypeError("minutes must be an integer")
        if minutes <= 0:
            raise ValueError("minutes must be greater than zero")

        with self._condition:
            if self._mode in ("break", "manual_break"):
                raise RuntimeError("A break is already in progress")

            now = time.monotonic()
            if self._mode == "work" and self._deadline is not None:
                self._remaining_when_paused = max(0.0, self._deadline - now)
            elif self._mode != "paused":
                self._remaining_when_paused = self._work_interval_sec

            self._mode = "manual_break"
            self._deadline = now + minutes * 60
            self._start_thread_locked()
            self._pending_events.append("break_start")
            self._condition.notify_all()

    def configure_intervals(
        self,
        work_interval_sec: int | float,
        break_duration_sec: int | float,
    ) -> None:
        """Update normal intervals, restarting the current matching interval."""
        self._validate_interval("work_interval_sec", work_interval_sec)
        self._validate_interval("break_duration_sec", break_duration_sec)

        with self._condition:
            self._normal_work_interval_sec = float(work_interval_sec)
            self._normal_break_duration_sec = float(break_duration_sec)
            if self._demo_mode:
                return

            self._work_interval_sec = self._normal_work_interval_sec
            self._break_duration_sec = self._normal_break_duration_sec
            now = time.monotonic()
            if self._mode == "work":
                self._deadline = now + self._work_interval_sec
            elif self._mode == "break":
                self._deadline = now + self._break_duration_sec
            elif self._mode in ("idle", "paused", "manual_break"):
                self._remaining_when_paused = self._work_interval_sec
            self._condition.notify_all()

    def toggle_demo_mode(self, enabled: bool) -> None:
        """Use 10-second work and 5-second breaks when demo mode is enabled."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")

        with self._condition:
            self._demo_mode = enabled
            self._work_interval_sec = (
                float(self.DEMO_WORK_INTERVAL_SEC)
                if enabled
                else self._normal_work_interval_sec
            )
            self._break_duration_sec = (
                float(self.DEMO_BREAK_DURATION_SEC)
                if enabled
                else self._normal_break_duration_sec
            )

            now = time.monotonic()
            if self._mode == "work":
                self._deadline = now + self._work_interval_sec
            elif self._mode == "break":
                self._deadline = now + self._break_duration_sec
            elif self._mode in ("idle", "paused", "manual_break"):
                self._remaining_when_paused = self._work_interval_sec
            self._condition.notify_all()

    def _start_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_requested = False
        self._thread = threading.Thread(
            target=self.run_loop,
            name="MindShieldTimer",
            daemon=True,
        )
        self._thread.start()

    def run_loop(self) -> None:
        """Run the timer loop; normally invoked on the daemon worker thread."""
        last_tick: tuple[str, int] | None = None
        try:
            while True:
                event: str | None = None
                state = ""
                remaining = 0

                with self._condition:
                    while event is None:
                        if self._stop_requested:
                            return

                        if self._pending_events:
                            event = self._pending_events.pop(0)
                            state = self._mode
                            remaining = self._get_remaining_locked(time.monotonic())
                            break

                        if self._mode in ("idle", "paused") or self._deadline is None:
                            self._condition.wait()
                            continue

                        now = time.monotonic()
                        time_left = self._deadline - now
                        if time_left <= 0:
                            if self._mode == "work":
                                self._mode = "break"
                                self._deadline = now + self._break_duration_sec
                                event = "break_start"
                            elif self._mode == "break":
                                self._mode = "work"
                                self._remaining_when_paused = self._work_interval_sec
                                self._deadline = now + self._work_interval_sec
                                event = "break_end"
                            else:
                                self._mode = "work"
                                self._deadline = now + self._remaining_when_paused
                                event = "break_end"
                            last_tick = None
                            state = self._mode
                            remaining = self._get_remaining_locked(now)
                            break

                        remaining = math.ceil(time_left)
                        state = self._mode
                        tick = (state, remaining)
                        if tick != last_tick:
                            last_tick = tick
                            event = "tick"
                            break

                        self._condition.wait(timeout=min(1.0, time_left))

                if self.on_state_update is not None:
                    self.on_state_update(state, remaining)
                if event == "tick":
                    if self.on_tick is not None:
                        self.on_tick(remaining)
                elif event == "break_start":
                    if self.on_break_start is not None:
                        self.on_break_start()
                elif event == "break_end":
                    if self.on_break_end is not None:
                        self.on_break_end()
        finally:
            with self._condition:
                self._condition.notify_all()

    def _get_remaining_locked(self, now: float) -> int:
        if self._deadline is not None:
            return max(0, math.ceil(self._deadline - now))
        if self._mode in ("idle", "paused"):
            return max(0, math.ceil(self._remaining_when_paused))
        return 0
