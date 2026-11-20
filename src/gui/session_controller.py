"""Focus timer, breaks, and activity recording."""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from zipfile import BadZipFile

import customtkinter as ctk
import sqlcipher3.dbapi2 as sqlite3

from src.core.focus_app import FocusAppController
from src.core.timer_manager import TimerManager
from src.core.tray_manager import TrayManager
from src.core.window_tracker import WindowTracker
from src.database.db_handler import DatabaseHandler, WorkZoneProfile
from src.gui.overlay_window import OverlayWindow
from src.gui.swipe_scroll_frame import SwipeScrollableFrame
from src.gui.theme import APPEARANCE_MODES, COLORS, TEXT_SIZE_SCALING
from src.gui.themed_controls import themed_button, themed_option_menu
from src.gui.user_text import (
    get_next_greeting_change,
    get_time_of_day_greeting,
    normalize_user_name,
    widget_is_text_entry,
)

logger = logging.getLogger(__name__)

class SessionControllerMixin:
    """Focus timer, breaks, and activity recording."""

    def _on_distraction_detected(self, window_title: str) -> None:
        if not self._closing:
            prefix = (
                "Distraction minimized"
                if self._suppress_distractions_enabled or self._focus_guard_enabled
                else "Distraction detected"
            )
            self.after(
                0,
                lambda: self.status_label.configure(
                    text=f"{prefix}: {window_title}"
                ),
            )


    def _set_countdown_display(self, remaining_seconds: int | float) -> None:
        total_seconds = max(0, math.ceil(remaining_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        self.countdown_label.configure(text=f"{minutes:02d}:{seconds:02d}")
        state = self.timer_manager.state
        phase_duration = {
            "idle": self.timer_manager.work_interval_sec,
            "work": self.timer_manager.work_interval_sec,
            "paused": self.timer_manager.work_interval_sec,
            "break": self.timer_manager.break_duration_sec,
            "manual_break": self._manual_break_duration_seconds,
            "break_paused": (
                self._manual_break_duration_seconds
                if self.timer_manager.is_on_manual_break
                else self.timer_manager.break_duration_sec
            ),
        }.get(state, 0)
        progress = (
            min(1.0, total_seconds / phase_duration)
            if phase_duration > 0
            else 0.0
        )
        self._animate_timer_progress(progress)


    def _handle_global_hotkey(self) -> None:
        """Pause or resume from the global shortcut, including while withdrawn."""
        try:
            focused = self.focus_get()
        except tk.TclError:
            focused = None
        if widget_is_text_entry(focused):
            return
        if self.timer_manager.state not in (
            "work",
            "paused",
            "break",
            "manual_break",
            "break_paused",
        ):
            return
        self._toggle_work_pause()


    def _report_background_error(self, message: str) -> None:
        if not self._closing:
            self.after(0, lambda: self.status_label.configure(text=message))


    def _save_work_before_exit(self) -> bool:
        elapsed_seconds = 0
        started_at = None
        if self.timer_manager.state == "work":
            self.timer_manager.pause_work()
            if self.timer_manager.state == "paused":
                elapsed_seconds = int(
                    round(self.timer_manager.last_completed_work_sec)
                )
                started_at = self.timer_manager.last_completed_work_started_at
        try:
            with self._activity_data_lock:
                self._record_work_segment_locked(
                    elapsed_seconds,
                    self.active_work_zone,
                    started_at,
                )
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            messagebox.showerror(
                "Could not save focus time",
                "MindShield could not save the active work segment. "
                f"The app will stay open so you can retry: {error}",
                parent=self,
            )
            return False
        return True


    def _close_break_overlay(self) -> None:
        if self._overlay is not None:
            if self._overlay.winfo_exists():
                self._overlay.destroy()
            self._overlay = None


    def _on_break_end(self) -> None:
        self._finish_break(completed=True)


    def _update_countdown(self, remaining_seconds: int) -> None:
        self._set_countdown_display(remaining_seconds)


    def _on_break_end_early(self) -> None:
        self._finish_break(completed=False)


    def _on_timer_tick(self, remaining_seconds: int) -> None:
        if self._closing:
            return
        self.after(0, self._update_countdown, remaining_seconds)


    def _start_work(self) -> None:
        if self._work_started:
            self.status_label.configure(text="A work session is already running.")
            return

        try:
            focus_minutes = int(self.focus_duration_entry.get())
            if not 1 <= focus_minutes <= 240:
                raise ValueError
        except ValueError:
            self.status_label.configure(
                text="Enter a whole focus/eye-care interval from 1 to 240 minutes."
            )
            return

        if self._focus_guard_enabled:
            try:
                self.focus_app_controller.validate_executable(self._focus_app_path)
                self.focus_app_controller.focus_or_launch(self._focus_app_path)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                self.status_label.configure(
                    text=f"Could not start the work application: {error}"
                )
                return

        try:
            self.database.set_settings(
                {"work_interval_minutes": str(focus_minutes)}
            )
        except sqlite3.Error as error:
            self.status_label.configure(
                text=f"Could not save focus duration: {error}"
            )
            return

        self._work_interval_minutes = focus_minutes
        self.work_interval_entry.delete(0, "end")
        self.work_interval_entry.insert(0, str(focus_minutes))
        timer_started = False
        try:
            self.timer_manager.start_work(focus_minutes * 60)
            timer_started = True
            self.window_tracker.start()
            self._window_tracking_started = True
        except RuntimeError as error:
            if timer_started:
                self.timer_manager.pause_work()
            self.status_label.configure(text=str(error))
            return

        self._work_started = True
        self.start_button.configure(state="disabled", text="Work in Progress")
        self.status_label.configure(text=f"Focusing in {self.active_work_zone}.")


    def _on_timer_state_update(self, state: str, _remaining_seconds: int) -> None:
        if self._closing:
            return
        self.after(0, self._display_timer_state, state, _remaining_seconds)


    def _finish_break(self, completed: bool) -> None:
        self.window_tracker.set_manual_break(False)
        if (
            not self._closing
            and self._work_started
            and not self._window_tracking_started
        ):
            try:
                self.window_tracker.start()
                self._window_tracking_started = True
            except RuntimeError as error:
                self._report_background_error(
                    f"Could not resume window monitoring: {error}"
                )
        self._manual_break_active = False
        try:
            with self._activity_data_lock:
                break_id = self._scheduled_break_id
                self._scheduled_break_id = None
                if completed and break_id is not None:
                    self.database.complete_scheduled_break(break_id)
            if break_id is not None and not self._closing:
                self.after(0, self._refresh_analytics)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self._report_background_error(f"Could not save break status: {error}")
        if not self._closing:
            self.after(0, self._close_break_overlay)
            self.after(0, self._return_to_work_app)


    def _return_to_work_early(self) -> None:
        if not self.timer_manager.return_to_work():
            self.status_label.configure(text="There is no active break to end.")


    def _on_break_start(self) -> None:
        self.window_tracker.set_manual_break(True)
        break_type = "manual" if self._manual_break_active else "scheduled"
        break_seconds = (
            self.timer_manager.remaining_sec
            if self._manual_break_active
            else math.ceil(self.timer_manager.break_duration_sec)
        )
        try:
            with self._activity_data_lock:
                work_seconds = int(
                    round(self.timer_manager.last_completed_work_sec)
                )
                self._scheduled_break_id = None
                if work_seconds > 0:
                    self._record_work_segment_locked(
                        work_seconds,
                        self.active_work_zone,
                        self.timer_manager.last_completed_work_started_at,
                    )
                self._scheduled_break_id = self.database.log_scheduled_break_start(
                    max(1, break_seconds),
                    self.active_work_zone,
                    break_type=break_type,
                )
            if not self._closing:
                self.after(0, self._refresh_analytics)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self._report_data_loss(f"Could not save break/work time: {error}")
        if not self._manual_break_active and not self._closing:
            self.after(0, self._show_break_overlay)
        if not self._closing:
            self.after(
                0,
                lambda: self.status_label.configure(text="Break time."),
            )


    def _toggle_work_pause(self) -> None:
        state = self.timer_manager.state
        if state in ("break", "manual_break"):
            self.timer_manager.pause_break()
            return
        if state == "break_paused":
            self.timer_manager.resume_break()
            return
        if state == "work":
            self.timer_manager.pause_work()
            self.window_tracker.set_manual_break(True)
            elapsed_seconds = int(round(self.timer_manager.last_completed_work_sec))
            if elapsed_seconds > 0:
                try:
                    self._record_work_segment(
                        elapsed_seconds,
                        self.active_work_zone,
                        self.timer_manager.last_completed_work_started_at,
                    )
                    self._refresh_analytics()
                except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                    self._report_data_loss(
                        f"Work paused, but its duration could not be saved: {error}"
                    )
                    return
            self.status_label.configure(text="Work paused; window monitoring is paused.")
        elif self.timer_manager.state == "paused":
            timer_started = False
            try:
                self.timer_manager.start_work()
                timer_started = True
                if not self._window_tracking_started:
                    self.window_tracker.start()
                    self._window_tracking_started = True
            except RuntimeError as error:
                if timer_started:
                    self.timer_manager.pause_work()
                self.status_label.configure(text=str(error))
                return
            self.window_tracker.set_manual_break(False)
            if self._focus_guard_enabled:
                self._return_to_work_app()
            self.status_label.configure(text="Work resumed.")
        else:
            self.status_label.configure(
                text="Work can only be paused or resumed during a work interval."
            )


    def _show_break_overlay(self) -> None:
        message = OverlayWindow.PROMPTS[self._break_prompt_mode]
        if self._closing or not message:
            return
        duration = max(1, math.ceil(self.timer_manager.break_duration_sec))
        self._overlay = OverlayWindow(
            self,
            duration,
            mode=self._break_overlay_mode,
            message=message,
            reduced_motion=self._reduced_motion,
            on_pause=self._toggle_work_pause,
            on_return_to_work=self._return_to_work_early,
        )


    def _start_meeting_break(self) -> None:
        try:
            self._start_manual_break(self._meeting_mode_minutes)
        except RuntimeError as error:
            self.status_label.configure(text=str(error))


    def _start_manual_break(self, minutes: int) -> None:
        previous_manual_state = self.window_tracker.manual_break
        was_manual_break = self._manual_break_active
        self._manual_break_active = True
        self.window_tracker.set_manual_break(True)
        try:
            self.timer_manager.start_manual_break(minutes)
        except RuntimeError:
            self._manual_break_active = was_manual_break
            self.window_tracker.set_manual_break(previous_manual_state)
            raise
        self._manual_break_duration_seconds = minutes * 60
        self._work_started = True
        self.status_label.configure(text=f"Break started for {minutes} minutes.")


    def _stop_work(self) -> None:
        state = self.timer_manager.state
        if state not in ("work", "paused"):
            return

        status_message = "Focus session stopped."
        if state == "work":
            self.timer_manager.pause_work()
            if self.timer_manager.state != "paused":
                self.status_label.configure(
                    text="A break just started; stop work after it ends."
                )
                return
            elapsed_seconds = int(round(self.timer_manager.last_completed_work_sec))
            if elapsed_seconds > 0:
                try:
                    self._record_work_segment(
                        elapsed_seconds,
                        self.active_work_zone,
                        self.timer_manager.last_completed_work_started_at,
                    )
                    self._refresh_analytics()
                except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                    status_message = (
                        f"Focus session stopped, but work time was not saved: {error}"
                    )
                    self._report_data_loss(status_message)

        self.timer_manager.reset_timer()
        self.window_tracker.set_manual_break(False)
        if self._window_tracking_started:
            self.window_tracker.stop(timeout=1)
            self._window_tracking_started = False
        self._work_started = False
        self._manual_break_active = False
        self._scheduled_break_id = None
        self._close_break_overlay()
        self.status_label.configure(text=status_message)


    def _toggle_work_pause_shortcut(self, event: tk.Event) -> str | None:
        if widget_is_text_entry(event.widget):
            return None

        if self.timer_manager.state not in (
            "work",
            "paused",
            "break",
            "manual_break",
            "break_paused",
        ):
            return None

        self._toggle_work_pause()
        return "break"


    def _animate_timer_progress(self, target: float) -> None:
        if self._progress_animation_after_id is not None:
            self.after_cancel(self._progress_animation_after_id)
            self._progress_animation_after_id = None
        if self._reduced_motion:
            self._timer_progress_value = target
            self.timer_progress.set(target)
            return
        self._progress_animation_start = self._timer_progress_value
        self._progress_animation_target = target
        self._progress_animation_step = 0
        self._advance_timer_progress()


    def _record_work_segment(
        self,
        elapsed_seconds: int,
        work_zone: str,
        started_at: datetime | None = None,
    ) -> None:
        with self._activity_data_lock:
            self._record_work_segment_locked(
                elapsed_seconds,
                work_zone,
                started_at,
            )


    def _record_work_segment_locked(
        self,
        elapsed_seconds: int,
        work_zone: str,
        started_at: datetime | None = None,
    ) -> None:
        if elapsed_seconds > 0:
            self._pending_work_segments.append(
                (elapsed_seconds, work_zone, started_at)
            )
        while self._pending_work_segments:
            pending_seconds, pending_zone, pending_started = (
                self._pending_work_segments[0]
            )
            self.database.log_work_segment(
                pending_seconds,
                pending_zone,
                pending_started,
            )
            self._pending_work_segments.pop(0)


    def _display_timer_state(self, state: str, remaining_seconds: int) -> None:
        if state == self._last_displayed_timer_state:
            return
        self._last_displayed_timer_state = state
        phase_labels = {
            "idle": "FOCUS SESSION",
            "work": "FOCUS SESSION",
            "paused": "WORK PAUSED",
            "break": "SHORT BREAK",
            "manual_break": "MANUAL BREAK",
            "break_paused": "BREAK PAUSED",
        }
        phase_colors = {
            "idle": COLORS["highlight"],
            "work": COLORS["highlight"],
            "paused": COLORS["text_muted"],
            "break": COLORS["success"],
            "manual_break": COLORS["success"],
            "break_paused": COLORS["success"],
        }
        self.phase_label.configure(
            text=phase_labels.get(state, "FOCUS SESSION"),
            text_color=phase_colors.get(state, COLORS["highlight"]),
        )
        self.timer_progress.configure(
            progress_color=(
                COLORS["success"]
                if state in ("break", "manual_break", "break_paused")
                else COLORS["accent"]
            )
        )
        start_text = {
            "idle": "Start Work",
            "work": "Work in Progress",
            "paused": "Work Paused",
            "break": "Break in Progress",
            "manual_break": "Break in Progress",
            "break_paused": "Break Paused",
        }
        self.start_button.configure(
            state="normal" if state == "idle" else "disabled",
            text=start_text.get(state, "Start Work"),
        )
        self.pause_button.configure(
            state=(
                "normal"
                if state in ("work", "paused", "break", "manual_break", "break_paused")
                else "disabled"
            ),
            text={
                "paused": "Resume Work",
                "break": "Pause Break",
                "manual_break": "Pause Break",
                "break_paused": "Resume Break",
            }.get(state, "Pause Work"),
        )
        self.stop_button.configure(
            state=(
                "normal"
                if state in ("work", "paused", "break", "manual_break", "break_paused")
                else "disabled"
            ),
            text=(
                "Back to Work"
                if state in ("break", "manual_break", "break_paused")
                else "Stop Work"
            ),
            command=(
                self._return_to_work_early
                if state in ("break", "manual_break", "break_paused")
                else self._stop_work
            ),
        )
        messages = {
            "idle": "Ready when you are.",
            "work": f"Focusing in {self.active_work_zone}.",
            "paused": "Work paused.",
            "break": "Break time.",
            "manual_break": "Manual break in progress.",
            "break_paused": "Break paused.",
        }
        if time.monotonic() >= getattr(self, "_status_error_until", 0):
            self.status_label.configure(text=messages.get(state, f"Timer state: {state}."))
        if self._overlay is not None and self._overlay.winfo_exists():
            self._overlay.set_paused(state == "break_paused")
        self._update_countdown(remaining_seconds)


    def _advance_timer_progress(self) -> None:
        if self._reduced_motion:
            self._timer_progress_value = self._progress_animation_target
            self.timer_progress.set(self._timer_progress_value)
            self._progress_animation_after_id = None
            return
        self._progress_animation_step += 1
        position = min(1.0, self._progress_animation_step / 8)
        eased_position = position * position * (3 - 2 * position)
        self._timer_progress_value = (
            self._progress_animation_start
            + (self._progress_animation_target - self._progress_animation_start)
            * eased_position
        )
        self.timer_progress.set(self._timer_progress_value)
        if self._progress_animation_step >= 8 or self._closing:
            self._progress_animation_after_id = None
            return
        self._progress_animation_after_id = self.after(
            20,
            self._advance_timer_progress,
        )


    def _start_selected_break(self) -> None:
        try:
            minutes = int(self.break_length_menu.get())
            self._start_manual_break(minutes)
        except (ValueError, RuntimeError) as error:
            self.status_label.configure(text=str(error))

