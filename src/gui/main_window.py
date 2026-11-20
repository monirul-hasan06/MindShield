"""Primary MindShield desktop application window."""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import sqlcipher3.dbapi2 as sqlite3

from src.core.focus_app import FocusAppController
from src.core.hotkey_listener import GlobalHotkeyListener
from src.core.timer_manager import TimerManager
from src.core.tray_manager import TrayManager
from src.core.window_tracker import WindowTracker
from src.database.db_handler import DatabaseHandler
from src.gui.analytics_view import AnalyticsMixin
from src.gui.app_settings import load_app_settings
from src.gui.dashboard_view import DashboardMixin
from src.gui.focus_guard_view import FocusGuardMixin
from src.gui.onboarding_view import OnboardingMixin
from src.gui.overlay_window import OverlayWindow
from src.gui.session_controller import SessionControllerMixin
from src.gui.settings_view import SettingsMixin
from src.gui.theme import COLORS, TEXT_SIZE_SCALING
from src.gui.user_text import (
    get_next_greeting_change,
    get_time_of_day_greeting,
    normalize_user_name,
    widget_is_text_entry,
)
from src.gui.work_zone_editor import WorkZoneEditorMixin

logger = logging.getLogger(__name__)


class MainWindow(
    DashboardMixin,
    AnalyticsMixin,
    SettingsMixin,
    WorkZoneEditorMixin,
    FocusGuardMixin,
    OnboardingMixin,
    SessionControllerMixin,
    ctk.CTk,
):
    """MindShield dashboard, analytics, and settings interface."""

    WEEKLY_GOAL_DAYS = 7
    PROFILE_MATCH_TYPES = {
        "Any window details": "any",
        "Window title": "title",
        "App / process": "process",
        "Executable path": "path",
        "Browser URL / folder": "accessible",
    }
    FOCUS_GUARD_MATCH_TYPES = {
        "Window title": "title",
        "App / process": "process",
        "Executable path": "path",
        "Browser URL": "accessible",
    }
    MATCH_MODE_LABELS = {
        "Contains": "contains",
        "Whole word": "token",
    }

    def __init__(
        self,
        database: DatabaseHandler | None = None,
        db_path: str | Path | None = None,
        timer_manager: TimerManager | None = None,
        window_tracker: WindowTracker | None = None,
        start_tray: bool = True,
    ) -> None:
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=COLORS["app_background"])

        self.title("MindShield")
        self.geometry("1024x760")
        self.minsize(820, 640)
        resource_root = Path(
            getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
        )
        icon_path = resource_root / "assets" / "mindshield.ico"
        if icon_path.is_file():
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                logger.warning("Could not load the MindShield icon at %s", icon_path)

        self.database = database or DatabaseHandler(db_path)
        loaded_settings = load_app_settings(self.database)
        self._startup_warnings = loaded_settings.warnings
        self._work_zone_profiles = self.database.list_work_zone_profiles()
        self._active_work_zone_profile = (
            self.database.get_active_work_zone_profile()
        )
        self.active_work_zone = self._active_work_zone_profile.name
        self._editing_profile_id: int | None = None
        self._profile_rules: list[tuple[str, str, bool, str]] = []
        self._work_interval_minutes = loaded_settings.work_interval_minutes
        self._break_duration_seconds = loaded_settings.break_duration_seconds
        self._daily_goal_minutes = loaded_settings.daily_goal_minutes
        self._focus_app_path = loaded_settings.focus_app_path
        self._focus_guard_enabled = loaded_settings.focus_guard_enabled
        self._focus_guard_rules = [
            (rule.match_type, rule.pattern, rule.enabled, rule.match_mode)
            for rule in self.database.get_focus_guard_rules()
        ]
        self._focus_guard_rules_dirty = False
        self._meeting_mode_minutes = loaded_settings.meeting_mode_minutes
        self._break_overlay_mode = loaded_settings.break_overlay_mode
        self._break_prompt_mode = loaded_settings.break_prompt_mode
        self._user_name = loaded_settings.user_name
        self._show_first_run_onboarding = loaded_settings.show_first_run_onboarding
        self._demo_mode_enabled_at_startup = loaded_settings.demo_mode
        self._suppress_distractions_enabled = loaded_settings.suppress_distractions
        self._appearance_mode = loaded_settings.appearance_mode
        self._text_size = loaded_settings.text_size
        self._reduced_motion = loaded_settings.reduced_motion
        ctk.set_appearance_mode(self._appearance_mode)
        ctk.set_widget_scaling(TEXT_SIZE_SCALING[self._text_size])
        self._work_started = False
        self._manual_break_active = False
        self._manual_break_duration_seconds = 0
        self._window_tracking_started = False
        self._scheduled_break_id: int | None = None
        self._activity_data_lock = threading.Lock()
        self._closing = False
        self._last_displayed_timer_state: str | None = None
        self._overlay: OverlayWindow | None = None
        self._onboarding_window: ctk.CTkToplevel | None = None
        self._onboarding_name_entry: ctk.CTkEntry | None = None
        self._onboarding_feedback_label: ctk.CTkLabel | None = None
        self._onboarding_animation_after_id: str | None = None
        self._onboarding_animation_step = 0
        self._greeting_refresh_after_id: str | None = None
        self._progress_animation_after_id: str | None = None
        self._progress_animation_start = 1.0
        self._progress_animation_target = 1.0
        self._progress_animation_step = 0
        self._timer_progress_value = 1.0
        self._pending_work_segments: list[tuple[int, str, datetime | None]] = []
        self._status_error_until = 0.0
        self._hotkey_listener: GlobalHotkeyListener | None = None

        self.timer_manager = timer_manager or TimerManager(
            work_interval_sec=self._work_interval_minutes * 60,
            break_duration_sec=self._break_duration_seconds,
        )
        if timer_manager is None and self._demo_mode_enabled_at_startup:
            self.timer_manager.toggle_demo_mode(True)
        self.timer_manager.on_tick = self._on_timer_tick
        self.timer_manager.on_break_start = self._on_break_start
        self.timer_manager.on_break_end = self._on_break_end
        self.timer_manager.on_break_end_early = self._on_break_end_early
        self.timer_manager.on_state_update = self._on_timer_state_update

        self.focus_app_controller = FocusAppController()
        self.window_tracker = window_tracker or WindowTracker(self.active_work_zone)
        self.window_tracker.set_work_zone_profile(
            self._active_work_zone_profile.name,
            [
                (
                    rule.match_type,
                    rule.pattern,
                    rule.enabled,
                    rule.match_mode,
                )
                for rule in self._active_work_zone_profile.rules
            ],
        )
        self.window_tracker.set_suppress_distractions(
            self._suppress_distractions_enabled
        )
        self.window_tracker.set_focus_guard(
            self._focus_guard_enabled,
            self._focus_app_path,
            self._focus_guard_rules,
        )
        self.window_tracker.on_focus_app_required = self._ensure_focus_app_from_tracker
        self.window_tracker.on_distraction_detected = self._on_distraction_detected
        self.tray_manager: TrayManager | None = None

        self._build_interface()
        self._hotkey_listener = GlobalHotkeyListener(
            lambda: self.after(0, self._handle_global_hotkey)
        )
        self._hotkey_listener.start()
        self._refresh_dashboard_greeting()
        self._refresh_analytics()
        if self._startup_warnings:
            self._set_status(self._startup_warnings[0], error=True)
        if self._show_first_run_onboarding:
            self.after(100, self._show_onboarding)

        if start_tray:
            self.attach_tray_manager(
                TrayManager(
                    app_instance=self,
                    on_meeting_mode=self._start_meeting_break,
                    on_exit=self.destroy,
                )
            )

    def attach_tray_manager(self, tray_manager: TrayManager) -> None:
        """Attach and start a tray manager created for this window."""
        if self.tray_manager is not None:
            self.tray_manager.stop()
        self.tray_manager = tray_manager
        self.tray_manager.start()

    def present_window(self) -> None:
        """Show the main window after another launch asks for it."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        """Show a status line. Errors stay up when the timer phase changes."""
        self.status_label.configure(text=text)
        self._status_error_until = time.monotonic() + 8 if error else 0.0

    def _report_data_loss(self, message: str) -> None:
        """Keep a save failure visible and explain it in a dialog."""
        def show() -> None:
            if self._closing:
                return
            self._set_status(message, error=True)
            messagebox.showerror("Could not save focus time", message, parent=self)

        if threading.current_thread() is threading.main_thread():
            show()
        elif not self._closing:
            self.after(0, show)

    def _build_interface(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 10))
        ctk.CTkLabel(
            header,
            text="MindShield",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Protect your focus. Make space to recharge.",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(2, 0))

        self.tabs = ctk.CTkTabview(
            self,
            fg_color=COLORS["tab_background"],
            segmented_button_fg_color=COLORS["segment_background"],
            segmented_button_selected_color=COLORS["segment_selected"],
            segmented_button_selected_hover_color=COLORS["segment_selected_hover"],
            segmented_button_unselected_color=COLORS["segment_background"],
            segmented_button_unselected_hover_color=COLORS["control_hover"],
            text_color=COLORS["text_primary"],
            text_color_disabled=COLORS["text_primary"],
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=24, pady=(8, 24))
        self.dashboard_tab = self.tabs.add("Dashboard")
        self.analytics_tab = self.tabs.add("Profile & Analytics")
        self.settings_tab = self.tabs.add("Settings")

        self._build_dashboard()
        self._build_analytics()
        self._build_settings()

    def destroy(self) -> None:
        """Stop background workers and owned windows before closing the app."""
        if not self._closing:
            if not self._save_work_before_exit():
                return
            self._closing = True
            if self._greeting_refresh_after_id is not None:
                self.after_cancel(self._greeting_refresh_after_id)
                self._greeting_refresh_after_id = None
            if self._onboarding_animation_after_id is not None:
                try:
                    self.after_cancel(self._onboarding_animation_after_id)
                except tk.TclError:
                    pass
                self._onboarding_animation_after_id = None
            if self._progress_animation_after_id is not None:
                try:
                    self.after_cancel(self._progress_animation_after_id)
                except tk.TclError:
                    pass
                self._progress_animation_after_id = None
            self.timer_manager.stop(timeout=1)
            self.window_tracker.stop(timeout=1)
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()
            if self.tray_manager is not None:
                self.tray_manager.stop()
            if self._overlay is not None:
                self._close_break_overlay()
        super().destroy()
