"""Background work and break timer for MindShield."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import datetime

logger = logging.getLogger(__name__)


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
        on_break_end_early: Callable[[], None] | None = None,
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
        self.on_break_end_early = on_break_end_early

        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop_requested = False
        self._demo_mode = False
        self._mode = "idle"
        self._deadline: float | None = None
        self._remaining_when_paused = self._work_interval_sec
        self._break_remaining_when_paused = self._break_duration_sec
        self._paused_break_mode: str | None = None
        self._work_elapsed_sec = 0.0
        self._work_phase_started_at: float | None = None
        self._work_phase_started_wall: datetime | None = None
        self._last_completed_work_sec = 0.0
        self._last_completed_work_started_at: datetime | None = None
        self._pending_events: list[str] = []

    @staticmethod
    def _validate_interval(name: str, value: int | float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be greater than zero and finite")

    @property
    def state(self) -> str:
        """Current phase, including paused work and paused break states."""
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
    def last_completed_work_sec(self) -> float:
        """Active work seconds in the most recently completed work segment."""
        with self._condition:
            return self._last_completed_work_sec

    @property
    def last_completed_work_started_at(self) -> datetime | None:
        """Wall-clock start of the most recently completed work segment."""
        with self._condition:
            return self._last_completed_work_started_at

    @property
    def current_work_elapsed_sec(self) -> float:
        """Active seconds in the current work segment, excluding pauses."""
        with self._condition:
            if self._mode != "work":
                return 0.0
            return self._get_work_elapsed_locked(time.monotonic())

    @property
    def is_running(self) -> bool:
        """Whether the timer is actively counting down."""
        with self._condition:
            return self._mode in ("work", "break", "manual_break")

    @property
    def is_on_manual_break(self) -> bool:
        """Whether a timed manual break is currently in progress."""
        with self._condition:
            return self._mode == "manual_break" or (
                self._mode == "break_paused"
                and self._paused_break_mode == "manual_break"
            )

    def start_work(self, duration_sec: int | float | None = None) -> None:
        """Start or resume work, optionally setting the preferred focus duration."""
        if duration_sec is not None:
            self._validate_interval("duration_sec", duration_sec)
        with self._condition:
            if self._mode not in ("idle", "paused"):
                if self._mode == "work":
                    return
                raise RuntimeError(f"Cannot start work while timer is in {self._mode!r} state")

            now = time.monotonic()
            if duration_sec is not None:
                self._normal_work_interval_sec = float(duration_sec)
                if not self._demo_mode:
                    self._work_interval_sec = float(duration_sec)
            if self._mode == "idle":
                self._remaining_when_paused = self._work_interval_sec
                self._work_elapsed_sec = 0.0
            self._mode = "work"
            self._deadline = now + self._remaining_when_paused
            self._mark_work_phase_started_locked(now)
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
            now = time.monotonic()
            self._remaining_when_paused = max(0.0, self._deadline - now)
            self._commit_work_phase_locked(now)
            self._deadline = None
            self._mode = "paused"
            self._pending_events.append("state")
            self._condition.notify_all()

    def pause_break(self) -> None:
        """Pause a timed break while preserving its type and remaining time."""
        with self._condition:
            if self._mode not in ("break", "manual_break") or self._deadline is None:
                return
            now = time.monotonic()
            self._break_remaining_when_paused = max(0.0, self._deadline - now)
            self._paused_break_mode = self._mode
            self._deadline = None
            self._mode = "break_paused"
            self._pending_events.append("state")
            self._condition.notify_all()

    def resume_break(self) -> None:
        """Resume a paused break with its saved remaining duration."""
        with self._condition:
            if self._mode != "break_paused" or self._paused_break_mode is None:
                return
            self._mode = self._paused_break_mode
            self._paused_break_mode = None
            self._deadline = time.monotonic() + self._break_remaining_when_paused
            self._pending_events.append("state")
            self._condition.notify_all()

    def return_to_work(self) -> bool:
        """End a break early and resume the appropriate focus countdown."""
        with self._condition:
            if self._mode in ("break", "manual_break"):
                break_mode = self._mode
            elif self._mode == "break_paused":
                break_mode = self._paused_break_mode
            else:
                return False
            if break_mode not in ("break", "manual_break"):
                return False

            now = time.monotonic()
            self._remaining_when_paused = (
                self._remaining_when_paused
                if break_mode == "manual_break"
                else self._work_interval_sec
            )
            self._work_elapsed_sec = 0.0
            self._mark_work_phase_started_locked(now)
            self._deadline = now + self._remaining_when_paused
            self._mode = "work"
            self._paused_break_mode = None
            self._pending_events.append("break_end_early")
            self._condition.notify_all()
            return True

    def reset_timer(self) -> None:
        """Stop the current phase and reset the work countdown to its interval."""
        with self._condition:
            self._mode = "idle"
            self._deadline = None
            self._remaining_when_paused = self._work_interval_sec
            self._break_remaining_when_paused = self._break_duration_sec
            self._paused_break_mode = None
            self._work_elapsed_sec = 0.0
            self._work_phase_started_at = None
            self._work_phase_started_wall = None
            self._last_completed_work_sec = 0.0
            self._last_completed_work_started_at = None
            self._pending_events.clear()
            self._pending_events.append("state")
            self._condition.notify_all()

    def reset_productivity_tracking(self) -> None:
        """Reset work-time accounting without changing the active countdown."""
        with self._condition:
            self._work_elapsed_sec = 0.0
            self._last_completed_work_sec = 0.0
            self._last_completed_work_started_at = None
            if self._mode == "work":
                self._mark_work_phase_started_locked(time.monotonic())
            else:
                self._work_phase_started_at = None
                self._work_phase_started_wall = None

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
            if self._mode in ("break", "manual_break", "break_paused"):
                raise RuntimeError("A break is already in progress")

            now = time.monotonic()
            if self._mode == "work" and self._deadline is not None:
                self._remaining_when_paused = max(0.0, self._deadline - now)
                self._commit_work_phase_locked(now)
            elif self._mode == "paused":
                self._last_completed_work_sec = 0.0
                self._last_completed_work_started_at = None
                self._work_elapsed_sec = 0.0
            else:
                self._remaining_when_paused = self._work_interval_sec
                self._work_elapsed_sec = 0.0
                self._last_completed_work_started_at = None

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
                self._work_elapsed_sec = 0.0
                self._mark_work_phase_started_locked(now)
            elif self._mode == "break":
                self._deadline = now + self._break_duration_sec
            elif self._mode in ("idle", "paused", "manual_break", "break_paused"):
                self._remaining_when_paused = self._work_interval_sec
                self._work_elapsed_sec = 0.0
                self._work_phase_started_at = None
                self._work_phase_started_wall = None
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
                self._work_elapsed_sec = 0.0
                self._mark_work_phase_started_locked(now)
            elif self._mode == "break":
                self._deadline = now + self._break_duration_sec
            elif self._mode in ("idle", "paused", "manual_break", "break_paused"):
                self._remaining_when_paused = self._work_interval_sec
                self._work_elapsed_sec = 0.0
                self._work_phase_started_at = None
                self._work_phase_started_wall = None
            self._condition.notify_all()

    def _mark_work_phase_started_locked(self, now: float) -> None:
        self._work_phase_started_at = now
        self._work_phase_started_wall = datetime.now()

    def _commit_work_phase_locked(self, now: float) -> None:
        """Close the active work segment and remember when it started."""
        if self._work_phase_started_at is not None:
            self._work_elapsed_sec += max(0.0, now - self._work_phase_started_at)
        self._last_completed_work_sec = self._work_elapsed_sec
        self._last_completed_work_started_at = self._work_phase_started_wall
        self._work_elapsed_sec = 0.0
        self._work_phase_started_at = None
        self._work_phase_started_wall = None

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

                        if self._mode in ("idle", "paused", "break_paused") or self._deadline is None:
                            self._condition.wait()
                            continue

                        now = time.monotonic()
                        time_left = self._deadline - now
                        if time_left <= 0:
                            if self._mode == "work":
                                self._commit_work_phase_locked(now)
                                self._mode = "break"
                                self._deadline = now + self._break_duration_sec
                                event = "break_start"
                            elif self._mode == "break":
                                self._mode = "work"
                                self._remaining_when_paused = self._work_interval_sec
                                self._work_elapsed_sec = 0.0
                                self._mark_work_phase_started_locked(now)
                                self._deadline = now + self._work_interval_sec
                                event = "break_end"
                            else:
                                self._mode = "work"
                                self._mark_work_phase_started_locked(now)
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

                self._notify(self.on_state_update, "state update", state, remaining)
                if event == "tick":
                    self._notify(self.on_tick, "tick", remaining)
                elif event == "break_start":
                    self._notify(self.on_break_start, "break start")
                elif event == "break_end":
                    self._notify(self.on_break_end, "break end")
                elif event == "break_end_early":
                    self._notify(
                        self.on_break_end_early,
                        "early break end",
                    )
        finally:
            with self._condition:
                self._condition.notify_all()

    @staticmethod
    def _notify(
        callback: Callable[..., None] | None,
        callback_name: str,
        *args: object,
    ) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            logger.exception("MindShield timer %s callback failed", callback_name)

    def _get_remaining_locked(self, now: float) -> int:
        if self._deadline is not None:
            return max(0, math.ceil(self._deadline - now))
        if self._mode == "break_paused":
            return max(0, math.ceil(self._break_remaining_when_paused))
        if self._mode in ("idle", "paused"):
            return max(0, math.ceil(self._remaining_when_paused))
        return 0

    def _get_work_elapsed_locked(self, now: float) -> float:
        elapsed = self._work_elapsed_sec
        if self._work_phase_started_at is not None:
            elapsed += max(0.0, now - self._work_phase_started_at)
        return elapsed
