"""Primary MindShield desktop application window."""

from __future__ import annotations

import math
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
from zipfile import BadZipFile

import customtkinter as ctk
import sqlcipher3.dbapi2 as sqlite3

from src.core.timer_manager import TimerManager
from src.core.focus_app import FocusAppController
from src.core.tray_manager import TrayManager
from src.core.window_tracker import WindowTracker
from src.database.db_handler import (
    DatabaseHandler,
    FocusGuardRule,
    WorkZoneProfile,
)
from src.gui.overlay_window import OverlayWindow
from src.gui.swipe_scroll_frame import SwipeScrollableFrame
from src.gui.themed_controls import themed_button, themed_option_menu
from src.gui.theme import APPEARANCE_MODES, COLORS, TEXT_SIZE_SCALING


def get_time_of_day_greeting(hour: int) -> str:
    """Return a greeting for a local 24-hour clock value."""
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("hour must be an integer from 0 to 23")
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def get_next_greeting_change(now: datetime) -> datetime:
    """Return the next local midnight, noon, or 17:00 greeting boundary."""
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.hour < 12:
        return now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now.hour < 17:
        return now.replace(hour=17, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def normalize_user_name(value: str) -> str:
    """Trim and require the locally stored profile name."""
    if not isinstance(value, str):
        raise TypeError("name must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Enter your name to continue.")
    return normalized


class MainWindow(ctk.CTk):
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
        self.iconbitmap(default=str(resource_root / "assets" / "mindshield.ico"))

        self.database = database or DatabaseHandler(db_path)
        self._work_zone_profiles = self.database.list_work_zone_profiles()
        self._active_work_zone_profile = (
            self.database.get_active_work_zone_profile()
        )
        self.active_work_zone = self._active_work_zone_profile.name
        self._editing_profile_id: int | None = None
        self._profile_rules: list[tuple[str, str, bool]] = []
        self._work_interval_minutes = self._load_positive_integer_setting(
            "work_interval_minutes",
            default=20,
        )
        self._break_duration_seconds = self._load_positive_integer_setting(
            "break_duration_seconds",
            default=20,
        )
        self._daily_goal_minutes = self._load_positive_integer_setting(
            "daily_goal_minutes",
            default=120,
        )
        self._focus_app_path = self.database.get_setting("focus_app_path") or ""
        saved_focus_guard = self.database.get_setting("focus_guard_enabled")
        if saved_focus_guard not in (None, "true", "false"):
            raise ValueError("Saved focus guard setting must be 'true' or 'false'")
        self._focus_guard_enabled = saved_focus_guard == "true"
        self._focus_guard_rules = [
            (rule.match_type, rule.pattern, rule.enabled)
            for rule in self.database.get_focus_guard_rules()
        ]
        self._focus_guard_rules_dirty = False
        self._meeting_mode_minutes = self._load_positive_integer_setting(
            "meeting_mode_minutes",
            default=60,
        )
        if self._meeting_mode_minutes not in (30, 60, 120):
            raise ValueError("Saved meeting mode duration must be 30, 60, or 120 minutes")
        saved_overlay_mode = self.database.get_setting("break_overlay_mode")
        if saved_overlay_mode not in (None, "overlay", "toast"):
            raise ValueError("Saved break overlay mode must be 'overlay' or 'toast'")
        self._break_overlay_mode = (
            "overlay" if saved_overlay_mode is None else saved_overlay_mode
        )
        saved_break_prompt = self.database.get_setting("break_prompt")
        if saved_break_prompt not in (None, *OverlayWindow.PROMPTS):
            raise ValueError("Saved break prompt is not supported")
        self._break_prompt_mode = saved_break_prompt or "eye_care"
        saved_onboarding = self.database.get_setting("onboarding_completed")
        if saved_onboarding not in (None, "true", "false"):
            raise ValueError("Saved onboarding setting must be 'true' or 'false'")
        self._user_name = (
            self.database.get_setting("user_name") or ""
        ).strip()
        self._show_first_run_onboarding = (
            saved_onboarding != "true" or not self._user_name
        )
        saved_demo_mode = self.database.get_setting("demo_mode")
        if saved_demo_mode not in (None, "true", "false"):
            raise ValueError("Saved demo mode setting must be 'true' or 'false'")
        self._demo_mode_enabled_at_startup = saved_demo_mode == "true"
        saved_suppression = self.database.get_setting("suppress_distractions")
        if saved_suppression not in (None, "true", "false"):
            raise ValueError("Saved distraction suppression setting must be 'true' or 'false'")
        self._suppress_distractions_enabled = saved_suppression == "true"
        saved_appearance_mode = self.database.get_setting("appearance_mode")
        if saved_appearance_mode not in (None, *APPEARANCE_MODES.values()):
            raise ValueError("Saved appearance mode must be dark, light, or system")
        self._appearance_mode = saved_appearance_mode or "dark"
        saved_text_size = self.database.get_setting("text_size")
        if saved_text_size not in (None, *TEXT_SIZE_SCALING.keys()):
            raise ValueError("Saved text size must be Small, Default, or Large")
        self._text_size = saved_text_size or "Default"
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
                (rule.match_type, rule.pattern, rule.enabled)
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
        self._refresh_dashboard_greeting()
        self._refresh_analytics()
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

    def _load_positive_integer_setting(self, key: str, default: int) -> int:
        saved_value = self.database.get_setting(key)
        if saved_value is None:
            return default
        try:
            value = int(saved_value)
        except ValueError as error:
            raise ValueError(f"Saved setting {key!r} must be a positive integer") from error
        if value <= 0:
            raise ValueError(f"Saved setting {key!r} must be a positive integer")
        return value

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

    def _appearance_label(self) -> str:
        return next(
            (
                label
                for label, mode in APPEARANCE_MODES.items()
                if mode == self._appearance_mode
            ),
            "Dark",
        )

    def _build_dashboard(self) -> None:
        tab = SwipeScrollableFrame(
            self.dashboard_tab,
            fg_color="transparent",
        )
        tab.grid(row=0, column=0, sticky="nsew")
        self.dashboard_tab.grid_columnconfigure(0, weight=1)
        self.dashboard_tab.grid_rowconfigure(0, weight=1)
        self.dashboard_scroll_frame = tab
        tab.grid_columnconfigure(0, weight=1)

        focus_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=18)
        focus_card.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 14))
        self.greeting_label = ctk.CTkLabel(
            focus_card,
            text="Welcome",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.greeting_label.pack(pady=(18, 8))
        self.dashboard_appearance_menu = themed_option_menu(
            focus_card,
            values=list(APPEARANCE_MODES),
            command=self._save_appearance_mode,
            width=120,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.dashboard_appearance_menu.set(self._appearance_label())
        self.dashboard_appearance_menu.pack(
            anchor="e",
            padx=18,
            pady=(0, 8),
        )
        self.phase_label = ctk.CTkLabel(
            focus_card,
            text="FOCUS SESSION",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["highlight"],
        )
        self.phase_label.pack(pady=(4, 4))
        initial_minutes, initial_seconds = divmod(
            math.ceil(self.timer_manager.work_interval_sec), 60
        )
        self.countdown_label = ctk.CTkLabel(
            focus_card,
            text=f"{initial_minutes:02d}:{initial_seconds:02d}",
            font=ctk.CTkFont(size=52, weight="bold"),
            text_color=COLORS["text_emphasis"],
        )
        self.countdown_label.pack(pady=(0, 2))
        self.timer_progress = ctk.CTkProgressBar(
            focus_card,
            height=8,
            progress_color=COLORS["accent"],
            fg_color=COLORS["control"],
        )
        self.timer_progress.set(1)
        self.timer_progress.pack(fill="x", padx=80, pady=(4, 12))
        focus_duration_row = ctk.CTkFrame(focus_card, fg_color="transparent")
        focus_duration_row.pack(pady=(0, 10))
        ctk.CTkLabel(
            focus_duration_row,
            text="Focus / eye-care interval",
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 8))
        self.focus_duration_entry = ctk.CTkEntry(
            focus_duration_row,
            width=72,
            justify="center",
        )
        self.focus_duration_entry.insert(0, str(self._work_interval_minutes))
        self.focus_duration_entry.pack(side="left", padx=4)
        ctk.CTkLabel(
            focus_duration_row,
            text="minutes (1-240)",
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(4, 0))
        ctk.CTkLabel(
            focus_card,
            text="An automatic eye-care break follows each completed interval.",
            text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=12),
        ).pack(pady=(0, 10))
        self.work_zone_label = ctk.CTkLabel(
            focus_card,
            text=f"Active Work Zone: {self.active_work_zone}",
            font=ctk.CTkFont(size=15),
            text_color=COLORS["text_secondary"],
        )
        self.work_zone_label.pack(pady=(0, 20))

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=24, pady=10)
        controls.grid_columnconfigure((0, 1), weight=1)
        self.start_button = themed_button(
            controls,
            text="Start Work",
            height=42,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._start_work,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.pause_button = themed_button(
            controls,
            text="Pause Work",
            height=38,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            state="disabled",
            command=self._toggle_work_pause,
        )
        self.pause_button.grid(
            row=1, column=0, sticky="ew", padx=(0, 8), pady=(10, 0)
        )
        self.stop_button = themed_button(
            controls,
            text="Stop Work",
            height=38,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            state="disabled",
            command=self._stop_work,
        )
        self.stop_button.grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(10, 0)
        )

        break_controls = ctk.CTkFrame(controls, fg_color="transparent")
        break_controls.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        break_controls.grid_columnconfigure(0, weight=1)
        self.break_length_menu = themed_option_menu(
            break_controls,
            values=["5", "10", "15"],
            width=90,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.break_length_menu.set("5")
        self.break_length_menu.grid(row=0, column=0, sticky="e", padx=(0, 8))
        self.break_button = themed_button(
            break_controls,
            text="Take a Break",
            height=42,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
            command=self._start_selected_break,
        )
        self.break_button.grid(row=0, column=1, sticky="ew")

        status_frame = ctk.CTkFrame(
            controls,
            fg_color=COLORS["segment_background"],
            corner_radius=10,
        )
        status_frame.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0)
        )
        self.status_label = ctk.CTkLabel(
            status_frame,
            text="Ready when you are.",
            anchor="w",
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=13),
        )
        self.status_label.pack(fill="x", padx=14, pady=10)

        zone_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        zone_card.grid(row=2, column=0, sticky="ew", padx=24, pady=14)
        ctk.CTkLabel(
            zone_card,
            text=(
                "Work zone — match a title, app, executable path, "
                "Explorer folder, or browser URL"
            ),
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 10))
        self.work_zone_selector = themed_option_menu(
            zone_card,
            values=[profile.name for profile in self._work_zone_profiles],
            height=38,
            command=self._activate_work_zone_profile,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.work_zone_selector.set(self.active_work_zone)
        self.work_zone_selector.grid(
            row=1, column=0, sticky="ew", padx=(18, 10), pady=(0, 16)
        )
        themed_button(
            zone_card,
            text="Edit Profiles",
            command=lambda: self.tabs.set("Settings"),
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=1, column=1, padx=(0, 18), pady=(0, 16))
        zone_card.grid_columnconfigure(0, weight=1)

    def _build_analytics(self) -> None:
        tab = SwipeScrollableFrame(
            self.analytics_tab,
            fg_color="transparent",
        )
        tab.grid(row=0, column=0, sticky="nsew")
        self.analytics_tab.grid_columnconfigure(0, weight=1)
        self.analytics_tab.grid_rowconfigure(0, weight=1)
        self.analytics_scroll_frame = tab
        tab.grid_columnconfigure((0, 1), weight=1)

        self.today_minutes_label = self._analytics_card(
            tab, "TODAY'S FOCUS", "0 min", 0, 0
        )
        self.streak_label = self._analytics_card(
            tab, "CURRENT STREAK", "0 days", 0, 1
        )

        daily_goal_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        daily_goal_card.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=24, pady=(8, 16)
        )
        ctk.CTkLabel(
            daily_goal_card,
            text="Daily focus goal",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=20, pady=(18, 8))
        self.daily_goal_progress = ctk.CTkProgressBar(
            daily_goal_card,
            height=12,
            progress_color=COLORS["success"],
            fg_color=COLORS["control"],
        )
        self.daily_goal_progress.pack(fill="x", padx=20, pady=8)
        self.daily_goal_summary_label = ctk.CTkLabel(
            daily_goal_card,
            text=f"0 / {self._daily_goal_minutes} minutes",
            text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=13),
        )
        self.daily_goal_summary_label.pack(anchor="e", padx=20, pady=(4, 18))

        self.monthly_focus_label = self._analytics_card(
            tab, "THIS MONTH'S FOCUS", "0 min", 2, 0
        )
        self.break_compliance_label = self._analytics_card(
            tab, "SCHEDULED BREAKS THIS MONTH", "No breaks yet", 2, 1
        )

        weekly_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        weekly_card.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=24, pady=(8, 16)
        )
        ctk.CTkLabel(
            weekly_card,
            text="Weekly focus summary",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=20, pady=(18, 8))
        self.weekly_progress = ctk.CTkProgressBar(
            weekly_card,
            height=12,
            progress_color=COLORS["highlight"],
            fg_color=COLORS["control"],
        )
        self.weekly_progress.pack(fill="x", padx=20, pady=8)
        self.weekly_summary_label = ctk.CTkLabel(
            weekly_card,
            text="0 / 840 minutes",
            text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=13),
        )
        self.weekly_summary_label.pack(anchor="e", padx=20, pady=(4, 18))

        history_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        history_card.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=24, pady=(8, 24)
        )
        history_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            history_card,
            text="Session history",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(18, 8))
        self.history_range_menu = themed_option_menu(
            history_card,
            values=["Last 7 days", "Last 30 days", "All history"],
            command=lambda _value: self._refresh_session_history(),
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.history_range_menu.set("Last 7 days")
        self.history_range_menu.grid(
            row=1, column=0, sticky="w", padx=(20, 8), pady=8
        )
        self.history_search_entry = ctk.CTkEntry(
            history_card,
            placeholder_text="Search work zones",
        )
        self.history_search_entry.grid(
            row=1, column=1, sticky="ew", padx=8, pady=8
        )
        self.history_search_entry.bind(
            "<Return>", lambda _event: self._refresh_session_history()
        )
        themed_button(
            history_card,
            text="Search",
            command=self._refresh_session_history,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=1, column=2, padx=(8, 20), pady=8)
        self.session_history_text = ctk.CTkTextbox(
            history_card,
            height=180,
            fg_color=COLORS["app_background"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
        )
        self.session_history_text.grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=8
        )
        themed_button(
            history_card,
            text="Export Filtered History to CSV",
            command=self._export_session_history,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=20, pady=(8, 18)
        )

        productivity_card = ctk.CTkFrame(
            tab,
            fg_color=COLORS["card_background"],
            corner_radius=16,
        )
        productivity_card.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 24)
        )
        productivity_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            productivity_card,
            text="Productivity profile",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))
        self.productivity_range_menu = themed_option_menu(
            productivity_card,
            values=["Last 7 days", "Last 30 days", "All history"],
            command=lambda _value: self._refresh_productivity_profile(),
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.productivity_range_menu.set("Last 7 days")
        self.productivity_range_menu.grid(
            row=0, column=1, sticky="e", padx=20, pady=(18, 8)
        )
        self.productivity_progress = ctk.CTkProgressBar(
            productivity_card,
            height=12,
            progress_color=COLORS["success"],
            fg_color=COLORS["control"],
        )
        self.productivity_progress.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=8
        )
        self.productivity_work_label = ctk.CTkLabel(
            productivity_card,
            text="Work: 0 min",
            text_color=COLORS["text_secondary"],
        )
        self.productivity_work_label.grid(
            row=2, column=0, sticky="w", padx=20, pady=(8, 4)
        )
        self.productivity_break_label = ctk.CTkLabel(
            productivity_card,
            text="Breaks: 0 min",
            text_color=COLORS["text_secondary"],
        )
        self.productivity_break_label.grid(
            row=2, column=1, sticky="e", padx=20, pady=(8, 4)
        )
        self.productivity_ratio_label = ctk.CTkLabel(
            productivity_card,
            text="Work/break ratio: no tracked time",
            text_color=COLORS["text_muted"],
        )
        self.productivity_ratio_label.grid(
            row=3, column=0, columnspan=2, sticky="w", padx=20, pady=(4, 18)
        )

        reset_card = ctk.CTkFrame(
            tab,
            fg_color=COLORS["card_background"],
            corner_radius=16,
        )
        reset_card.grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 24)
        )
        reset_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            reset_card,
            text="Reset saved analytics",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 6))
        ctk.CTkLabel(
            reset_card,
            text=(
                "Clear saved focus totals, break history, and session history. "
                "Settings and work-zone profiles are kept."
            ),
            justify="left",
            wraplength=560,
            text_color=COLORS["text_muted"],
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 8))
        themed_button(
            reset_card,
            text="Reset Analytics",
            command=self._reset_analytics,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 16))

    @staticmethod
    def _analytics_card(
        parent: ctk.CTkFrame | ctk.CTkScrollableFrame,
        title: str,
        value: str,
        row: int,
        column: int,
    ) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_background"], corner_radius=16)
        card.grid(row=row, column=column, sticky="ew", padx=24, pady=24)
        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", padx=18, pady=(18, 4))
        value_label = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=COLORS["text_emphasis"],
        )
        value_label.pack(anchor="w", padx=18, pady=(0, 18))
        return value_label

    def _build_settings(self) -> None:
        tab = SwipeScrollableFrame(
            self.settings_tab,
            fg_color="transparent",
        )
        tab.grid(row=0, column=0, sticky="nsew")
        self.settings_tab.grid_columnconfigure(0, weight=1)
        self.settings_tab.grid_rowconfigure(0, weight=1)
        self.settings_scroll_frame = tab
        tab.grid_columnconfigure(0, weight=1)

        interval_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        interval_card.grid(row=0, column=0, sticky="ew", padx=24, pady=24)
        ctk.CTkLabel(
            interval_card,
            text="Focus & eye-care schedule",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 14))

        ctk.CTkLabel(
            interval_card,
            text=(
                "The focus interval also sets when the automatic eye-care break "
                "starts. Set the break length separately."
            ),
            justify="left",
            wraplength=560,
            text_color=COLORS["text_muted"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 8))

        ctk.CTkLabel(
            interval_card,
            text="Focus / eye-care interval (minutes)",
            text_color=COLORS["text_secondary"],
        ).grid(row=2, column=0, sticky="w", padx=18, pady=8)
        self.work_interval_entry = ctk.CTkEntry(interval_card, width=140)
        self.work_interval_entry.insert(0, str(self._work_interval_minutes))
        self.work_interval_entry.grid(row=2, column=1, sticky="e", padx=18, pady=8)

        ctk.CTkLabel(
            interval_card,
            text="Eye-care break length (seconds)",
            text_color=COLORS["text_secondary"],
        ).grid(row=3, column=0, sticky="w", padx=18, pady=8)
        self.break_duration_entry = ctk.CTkEntry(interval_card, width=140)
        self.break_duration_entry.insert(0, str(self._break_duration_seconds))
        self.break_duration_entry.grid(row=3, column=1, sticky="e", padx=18, pady=8)

        themed_button(
            interval_card,
            text="Apply Focus & Eye-care Settings",
            command=self._apply_timer_settings,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=(12, 18))
        interval_card.grid_columnconfigure(0, weight=1)

        goal_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        goal_card.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        ctk.CTkLabel(
            goal_card,
            text="Daily focus goal",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            goal_card,
            text="Daily target shown in Profile & Analytics (minutes)",
            text_color=COLORS["text_secondary"],
        ).grid(row=1, column=0, sticky="w", padx=18, pady=8)
        self.daily_goal_entry = ctk.CTkEntry(goal_card, width=140)
        self.daily_goal_entry.insert(0, str(self._daily_goal_minutes))
        self.daily_goal_entry.grid(row=1, column=1, sticky="e", padx=18, pady=8)
        themed_button(
            goal_card,
            text="Save Daily Goal",
            command=self._apply_daily_goal,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=(6, 16))
        goal_card.grid_columnconfigure(0, weight=1)

        demo_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        demo_card.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.demo_switch = ctk.CTkSwitch(
            demo_card,
            text="Demo Mode (10s/5s)",
            command=self._toggle_demo_mode,
        )
        if self._demo_mode_enabled_at_startup:
            self.demo_switch.select()
        self.demo_switch.pack(anchor="w", padx=18, pady=18)

        meeting_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        meeting_card.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 12))
        ctk.CTkLabel(
            meeting_card,
            text="Pause / meeting mode duration",
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=0, sticky="w", padx=18, pady=12)
        self.meeting_duration_menu = themed_option_menu(
            meeting_card,
            values=["30 minutes", "1 hour", "2 hours"],
            command=self._save_meeting_mode_duration,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.meeting_duration_menu.set(self._meeting_duration_label())
        self.meeting_duration_menu.grid(row=0, column=1, sticky="e", padx=18, pady=12)
        themed_button(
            meeting_card,
            text="Start Meeting Mode",
            height=42,
            command=self._start_meeting_break,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 16))
        meeting_card.grid_columnconfigure(0, weight=1)

        self.suppression_switch = ctk.CTkSwitch(
            tab,
            text="Automatically minimize windows outside my work zone",
            command=self._toggle_distraction_suppression,
        )
        if self._suppress_distractions_enabled:
            self.suppression_switch.select()
        self.suppression_switch.grid(row=4, column=0, sticky="w", padx=24, pady=12)

        overlay_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        overlay_card.grid(row=5, column=0, sticky="ew", padx=24, pady=(0, 16))
        ctk.CTkLabel(
            overlay_card,
            text="Break reminder style",
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))
        self.overlay_mode_menu = themed_option_menu(
            overlay_card,
            values=["Full-screen overlay", "Corner toast"],
            command=self._save_break_overlay_mode,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.overlay_mode_menu.set(
            "Corner toast" if self._break_overlay_mode == "toast" else "Full-screen overlay"
        )
        self.overlay_mode_menu.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 8))
        ctk.CTkLabel(
            overlay_card,
            text="Choose Corner toast during presentations or video playback.",
            text_color=COLORS["text_muted"],
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            overlay_card,
            text="Break prompt",
            text_color=COLORS["text_secondary"],
        ).grid(row=3, column=0, sticky="w", padx=18, pady=(6, 4))
        self.break_prompt_menu = themed_option_menu(
            overlay_card,
            values=[
                "Eye-care (20-20-20)",
                "Stretch",
                "Hydrate",
                "No prompt",
            ],
            command=self._save_break_prompt,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.break_prompt_menu.set(
            {
                "eye_care": "Eye-care (20-20-20)",
                "stretch": "Stretch",
                "hydrate": "Hydrate",
                "none": "No prompt",
            }[self._break_prompt_mode]
        )
        self.break_prompt_menu.grid(
            row=4, column=0, sticky="ew", padx=18, pady=(4, 14)
        )
        overlay_card.grid_columnconfigure(0, weight=1)

        appearance_card = ctk.CTkFrame(
            tab,
            fg_color=COLORS["card_background"],
            corner_radius=16,
        )
        appearance_card.grid(row=6, column=0, sticky="ew", padx=24, pady=(0, 16))
        ctk.CTkLabel(
            appearance_card,
            text="Appearance & text size",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            appearance_card,
            text="Theme",
            text_color=COLORS["text_secondary"],
        ).grid(row=1, column=0, sticky="w", padx=18, pady=8)
        self.appearance_menu = themed_option_menu(
            appearance_card,
            values=list(APPEARANCE_MODES),
            command=self._save_appearance_mode,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.appearance_menu.set(self._appearance_label())
        self.appearance_menu.grid(row=1, column=1, sticky="e", padx=18, pady=8)
        ctk.CTkLabel(
            appearance_card,
            text="Text size",
            text_color=COLORS["text_secondary"],
        ).grid(row=2, column=0, sticky="w", padx=18, pady=8)
        self.text_size_menu = themed_option_menu(
            appearance_card,
            values=list(TEXT_SIZE_SCALING),
            command=self._save_text_size,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.text_size_menu.set(self._text_size)
        self.text_size_menu.grid(row=2, column=1, sticky="e", padx=18, pady=(8, 16))
        appearance_card.grid_columnconfigure(0, weight=1)

        privacy_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        privacy_card.grid(row=8, column=0, sticky="ew", padx=24, pady=(0, 12))
        ctk.CTkLabel(
            privacy_card,
            text="Privacy & local data",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            privacy_card,
            text=(
                "During work sessions, MindShield checks the active window title "
                "and app details; browser URLs or folder paths may be read to match "
                "your work zone. These details are not saved. Activity and settings "
                "stay encrypted on this PC; MindShield does not use accounts or "
                "cloud sync."
            ),
            justify="left",
            wraplength=560,
            text_color=COLORS["text_muted"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 12))
        themed_button(
            privacy_card,
            text="Create Encrypted Backup",
            command=self._export_data_backup,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=2, column=0, sticky="ew", padx=(18, 8), pady=(0, 16))
        themed_button(
            privacy_card,
            text="Restore Backup",
            command=self._restore_data_backup,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=2, column=1, sticky="ew", padx=(8, 18), pady=(0, 16))
        themed_button(
            privacy_card,
            text="Replay Walkthrough",
            command=self._show_onboarding,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=3, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 16))
        name_row = ctk.CTkFrame(privacy_card, fg_color="transparent")
        name_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 16))
        name_row.grid_columnconfigure(0, weight=1)
        self.user_name_entry = ctk.CTkEntry(
            name_row,
            placeholder_text="Your name",
        )
        self.user_name_entry.insert(0, self._user_name)
        self.user_name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        themed_button(
            name_row,
            text="Save Name",
            command=self._save_user_name,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=0, column=1, sticky="e")
        privacy_card.grid_columnconfigure((0, 1), weight=1)

        profile_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        profile_card.grid(row=7, column=0, sticky="ew", padx=24, pady=(0, 12))
        profile_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            profile_card,
            text="Work-zone profiles",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        profile_actions = ctk.CTkFrame(profile_card, fg_color="transparent")
        profile_actions.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=4)
        themed_button(
            profile_actions,
            text="New Profile",
            command=self._new_work_zone_profile,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).pack(side="left", padx=(0, 8))
        themed_button(
            profile_actions,
            text="Delete Profile",
            command=self._delete_work_zone_profile,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).pack(side="left")
        ctk.CTkLabel(
            profile_card,
            text="Profile name",
            text_color=COLORS["text_secondary"],
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(8, 4))
        self.profile_name_entry = ctk.CTkEntry(profile_card)
        self.profile_name_entry.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 8)
        )
        ctk.CTkLabel(
            profile_card,
            text="Enabled rules use OR matching: any matching rule activates the profile.",
            text_color=COLORS["text_muted"],
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 6))
        self.profile_rules_frame = SwipeScrollableFrame(
            profile_card,
            height=130,
            fg_color=COLORS["app_background"],
        )
        self.profile_rules_frame.grid_columnconfigure(0, weight=1)
        self.profile_rules_frame.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=18, pady=6
        )
        rule_editor = ctk.CTkFrame(profile_card, fg_color="transparent")
        rule_editor.grid(row=6, column=0, columnspan=2, sticky="ew", padx=18, pady=6)
        self.profile_rule_type_menu = themed_option_menu(
            rule_editor,
            values=list(self.PROFILE_MATCH_TYPES),
            width=175,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.profile_rule_type_menu.set("Window title")
        self.profile_rule_type_menu.pack(side="left", padx=(0, 8))
        self.profile_rule_entry = ctk.CTkEntry(
            rule_editor,
            placeholder_text="Text to match",
        )
        self.profile_rule_entry.pack(side="left", fill="x", expand=True, padx=8)
        themed_button(
            rule_editor,
            text="Add Rule",
            command=self._add_work_zone_rule,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).pack(side="left", padx=(8, 0))
        self.profile_preview_label = ctk.CTkLabel(
            profile_card,
            text="",
            justify="left",
            wraplength=560,
            text_color=COLORS["text_muted"],
        )
        self.profile_preview_label.grid(
            row=7, column=0, columnspan=2, sticky="w", padx=18, pady=6
        )
        themed_button(
            profile_card,
            text="Save and Activate Profile",
            command=self._save_work_zone_profile,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        ).grid(row=8, column=0, columnspan=2, sticky="ew", padx=18, pady=(6, 18))
        self._load_profile_editor(self._active_work_zone_profile)

        guard_card = ctk.CTkFrame(tab, fg_color=COLORS["card_background"], corner_radius=16)
        guard_card.grid(row=9, column=0, sticky="ew", padx=24, pady=(0, 24))
        guard_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            guard_card,
            text="Reversible work-app guard",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        self.focus_guard_switch = ctk.CTkSwitch(
            guard_card,
            text="Keep the selected work app in focus during work",
            command=self._toggle_focus_guard,
            progress_color=COLORS["accent"],
        )
        if self._focus_guard_enabled:
            self.focus_guard_switch.select()
        self.focus_guard_switch.grid(
            row=1, column=0, columnspan=2, sticky="w", padx=18, pady=8
        )
        self.focus_app_path_label = ctk.CTkLabel(
            guard_card,
            text=self._focus_app_path or "No work application selected",
            anchor="w",
            justify="left",
            wraplength=460,
            text_color=COLORS["text_muted"],
        )
        self.focus_app_path_label.grid(
            row=2, column=0, sticky="ew", padx=(18, 8), pady=8
        )
        themed_button(
            guard_card,
            text="Choose .exe",
            command=self._choose_focus_app,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(row=2, column=1, padx=(8, 18), pady=8)
        ctk.CTkLabel(
            guard_card,
            text=(
                "Disallowed windows are minimized while work is active. The guard "
                "pauses for breaks and never blocks Windows system shortcuts. "
                "Browser URL checks only inspect the active window when Windows "
                "exposes it; background tabs are not guaranteed to be blocked."
            ),
            justify="left",
            wraplength=560,
            text_color=COLORS["text_muted"],
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 10))
        self.focus_guard_rules_frame = SwipeScrollableFrame(
            guard_card,
            height=110,
            fg_color=COLORS["app_background"],
        )
        self.focus_guard_rules_frame.grid_columnconfigure(0, weight=1)
        self.focus_guard_rules_frame.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=6
        )
        guard_rule_editor = ctk.CTkFrame(guard_card, fg_color="transparent")
        guard_rule_editor.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=18, pady=6
        )
        self.focus_guard_rule_type_menu = themed_option_menu(
            guard_rule_editor,
            values=list(self.FOCUS_GUARD_MATCH_TYPES),
            width=165,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.focus_guard_rule_type_menu.set("Browser URL")
        self.focus_guard_rule_type_menu.pack(side="left", padx=(0, 8))
        self.focus_guard_rule_entry = ctk.CTkEntry(
            guard_rule_editor,
            placeholder_text="Blocked app, title, path, or URL text",
        )
        self.focus_guard_rule_entry.pack(side="left", fill="x", expand=True, padx=8)
        themed_button(
            guard_rule_editor,
            text="Add Rule",
            command=self._add_focus_guard_rule,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).pack(side="left", padx=(8, 0))
        themed_button(
            guard_card,
            text="Save Block Rules",
            command=self._save_focus_guard_rules,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=18, pady=(6, 18)
        )
        self._render_focus_guard_rules()

    def _export_data_backup(self) -> None:
        backup_path = filedialog.asksaveasfilename(
            parent=self,
            title="Create MindShield backup",
            defaultextension=".msbackup",
            filetypes=[("MindShield backup", "*.msbackup"), ("All files", "*.*")],
        )
        if not backup_path:
            return

        try:
            self.database.backup_to(backup_path)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not create backup: {error}")
            return
        self.status_label.configure(text=f"Encrypted backup created: {backup_path}")

    def _restore_data_backup(self) -> None:
        if self.timer_manager.state not in ("idle", "paused"):
            self.status_label.configure(
                text="Pause or finish the current work/break timer before restoring."
            )
            return

        backup_path = filedialog.askopenfilename(
            parent=self,
            title="Restore MindShield backup",
            filetypes=[("MindShield backup", "*.msbackup"), ("All files", "*.*")],
        )
        if not backup_path:
            return
        if not messagebox.askyesno(
            "Replace MindShield data?",
            "Restoring replaces the current activity history and settings. "
            "Create an encrypted backup of the current data first if you may "
            "need it. Continue?",
            parent=self,
        ):
            return

        try:
            self.database.restore_from(backup_path)
        except (
            BadZipFile,
            OSError,
            RuntimeError,
            ValueError,
            sqlite3.Error,
        ) as error:
            self.status_label.configure(text=f"Could not restore backup: {error}")
            return

        messagebox.showinfo(
            "Backup restored",
            "MindShield will close so the restored settings can be loaded. "
            "Launch it again to continue.",
            parent=self,
        )
        self.destroy()

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
                    self.database.log_work_segment(
                        elapsed_seconds,
                        self.active_work_zone,
                    )
                    self._refresh_analytics()
                except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                    status_message = (
                        f"Focus session stopped, but work time was not saved: {error}"
                    )

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
                    self.database.log_work_segment(
                        elapsed_seconds,
                        self.active_work_zone,
                    )
                    self._refresh_analytics()
                except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                    self.status_label.configure(
                        text=f"Work paused, but its duration could not be saved: {error}"
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

    def _start_selected_break(self) -> None:
        try:
            minutes = int(self.break_length_menu.get())
            self._start_manual_break(minutes)
        except (ValueError, RuntimeError) as error:
            self.status_label.configure(text=str(error))

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

    def _return_to_work_early(self) -> None:
        if not self.timer_manager.return_to_work():
            self.status_label.configure(text="There is no active break to end.")

    def _activate_work_zone_profile(self, profile_name: str) -> None:
        profile = next(
            (item for item in self._work_zone_profiles if item.name == profile_name),
            None,
        )
        if profile is None:
            self.work_zone_selector.set(self.active_work_zone)
            self.status_label.configure(text="Choose an existing work-zone profile.")
            return
        try:
            active_profile = self.database.set_active_work_zone_profile(profile.id)
        except (sqlite3.Error, ValueError, RuntimeError) as error:
            self.work_zone_selector.set(self.active_work_zone)
            self.status_label.configure(text=f"Could not activate profile: {error}")
            return

        self._active_work_zone_profile = active_profile
        self.active_work_zone = active_profile.name
        self.window_tracker.set_work_zone_profile(
            active_profile.name,
            [
                (rule.match_type, rule.pattern, rule.enabled)
                for rule in active_profile.rules
            ],
        )
        self.work_zone_label.configure(text=f"Active Work Zone: {active_profile.name}")
        self._load_profile_editor(active_profile)
        self.status_label.configure(text=f"Active profile: {active_profile.name}.")

    def _new_work_zone_profile(self) -> None:
        self._editing_profile_id = None
        self.profile_name_entry.delete(0, "end")
        self._profile_rules = []
        self._render_profile_rules()
        self.status_label.configure(
            text="Create a profile name and add at least one enabled rule."
        )

    def _load_profile_editor(self, profile: WorkZoneProfile) -> None:
        self._editing_profile_id = profile.id
        self.profile_name_entry.delete(0, "end")
        self.profile_name_entry.insert(0, profile.name)
        self._profile_rules = [
            (rule.match_type, rule.pattern, rule.enabled)
            for rule in profile.rules
        ]
        self._render_profile_rules()

    def _add_work_zone_rule(self) -> None:
        match_type = self.PROFILE_MATCH_TYPES.get(self.profile_rule_type_menu.get())
        pattern = self.profile_rule_entry.get().strip()
        if match_type is None or not pattern:
            self.status_label.configure(text="Choose a rule type and enter match text.")
            return
        self._profile_rules.append((match_type, pattern, True))
        self.profile_rule_entry.delete(0, "end")
        self._render_profile_rules()

    def _toggle_work_zone_rule(self, index: int) -> None:
        match_type, pattern, enabled = self._profile_rules[index]
        self._profile_rules[index] = (match_type, pattern, not enabled)
        self._render_profile_rules()

    def _remove_work_zone_rule(self, index: int) -> None:
        del self._profile_rules[index]
        self._render_profile_rules()

    def _render_profile_rules(self) -> None:
        for child in self.profile_rules_frame.winfo_children():
            child.destroy()

        labels_by_type = {
            value: label for label, value in self.PROFILE_MATCH_TYPES.items()
        }
        for index, (match_type, pattern, enabled) in enumerate(self._profile_rules):
            row = ctk.CTkFrame(self.profile_rules_frame, fg_color="transparent")
            row.grid(row=index, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(1, weight=1)
            enabled_checkbox = ctk.CTkCheckBox(
                row,
                text="Enabled",
                width=88,
                command=lambda rule_index=index: self._toggle_work_zone_rule(
                    rule_index
                ),
                text_color=COLORS["text_secondary"],
            )
            if enabled:
                enabled_checkbox.select()
            else:
                enabled_checkbox.deselect()
            enabled_checkbox.grid(row=0, column=0, padx=(4, 8))
            ctk.CTkLabel(
                row,
                text=f"{labels_by_type[match_type]} contains: {pattern}",
                anchor="w",
                justify="left",
                wraplength=380,
                text_color=COLORS["text_secondary"],
            ).grid(row=0, column=1, sticky="ew", padx=4)
            themed_button(
                row,
                text="Remove",
                width=72,
                command=lambda rule_index=index: self._remove_work_zone_rule(
                    rule_index
                ),
                fg_color=COLORS["control"],
                hover_color=COLORS["control_hover"],
            ).grid(row=0, column=2, padx=(8, 4))

        active_rules = [
            f"{labels_by_type[match_type]}: {pattern}"
            for match_type, pattern, enabled in self._profile_rules
            if enabled
        ]
        if active_rules:
            preview = "Matches when any enabled rule matches: " + "; ".join(active_rules)
        else:
            preview = "Enable at least one rule before saving this profile."
        self.profile_preview_label.configure(text=preview)

    def _save_work_zone_profile(self) -> None:
        name = self.profile_name_entry.get().strip()
        if not name or not any(enabled for _, _, enabled in self._profile_rules):
            self.status_label.configure(
                text="Enter a profile name and enable at least one rule."
            )
            return

        try:
            profile_id = self.database.save_work_zone_profile(
                name,
                self._profile_rules,
                self._editing_profile_id,
            )
            active_profile = self.database.set_active_work_zone_profile(profile_id)
        except (sqlite3.Error, ValueError, TypeError, RuntimeError) as error:
            self.status_label.configure(text=f"Could not save profile: {error}")
            return

        self._work_zone_profiles = self.database.list_work_zone_profiles()
        self._active_work_zone_profile = active_profile
        self.active_work_zone = active_profile.name
        self.window_tracker.set_work_zone_profile(
            active_profile.name,
            [
                (rule.match_type, rule.pattern, rule.enabled)
                for rule in active_profile.rules
            ],
        )
        self.work_zone_selector.configure(
            values=[profile.name for profile in self._work_zone_profiles]
        )
        self.work_zone_selector.set(active_profile.name)
        self.work_zone_label.configure(text=f"Active Work Zone: {active_profile.name}")
        self._load_profile_editor(active_profile)
        self.status_label.configure(text=f"Profile saved and activated: {name}.")

    def _delete_work_zone_profile(self) -> None:
        if self._editing_profile_id is None:
            self.status_label.configure(text="Save or select a profile before deleting it.")
            return
        try:
            self.database.delete_work_zone_profile(self._editing_profile_id)
            self._work_zone_profiles = self.database.list_work_zone_profiles()
            active_profile = self.database.get_active_work_zone_profile()
        except (sqlite3.Error, ValueError, RuntimeError) as error:
            self.status_label.configure(text=f"Could not delete profile: {error}")
            return

        self._active_work_zone_profile = active_profile
        self.active_work_zone = active_profile.name
        self.window_tracker.set_work_zone_profile(
            active_profile.name,
            [
                (rule.match_type, rule.pattern, rule.enabled)
                for rule in active_profile.rules
            ],
        )
        self.work_zone_selector.configure(
            values=[profile.name for profile in self._work_zone_profiles]
        )
        self.work_zone_selector.set(active_profile.name)
        self.work_zone_label.configure(text=f"Active Work Zone: {active_profile.name}")
        self._load_profile_editor(active_profile)
        self.status_label.configure(text="Work-zone profile deleted.")

    def _configure_focus_guard(self) -> None:
        self.window_tracker.set_focus_guard(
            self._focus_guard_enabled,
            self._focus_app_path,
            self._focus_guard_rules,
        )

    def _choose_focus_app(self) -> None:
        if self.timer_manager.state != "idle":
            self.status_label.configure(
                text="Stop the current timer before changing the work application."
            )
            return
        selected_path = filedialog.askopenfilename(
            parent=self,
            title="Choose the work application",
            filetypes=[("Windows applications", "*.exe")],
        )
        if not selected_path:
            return
        try:
            executable_path = self.focus_app_controller.validate_executable(
                selected_path
            )
            self.database.set_settings({"focus_app_path": str(executable_path)})
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not select work app: {error}")
            return

        self._focus_app_path = str(executable_path)
        self.focus_app_path_label.configure(text=self._focus_app_path)
        self._configure_focus_guard()
        self.status_label.configure(text="Work application saved.")

    def _toggle_focus_guard(self) -> None:
        enabled = self.focus_guard_switch.get() in (True, 1, "on")
        if enabled and not self._focus_app_path:
            self.focus_guard_switch.deselect()
            self.status_label.configure(
                text="Choose a work application before enabling the focus guard."
            )
            return
        if enabled and self._focus_guard_rules_dirty:
            self.focus_guard_switch.deselect()
            self.status_label.configure(
                text="Save the edited block rules before enabling the focus guard."
            )
            return
        try:
            self.database.set_settings(
                {"focus_guard_enabled": "true" if enabled else "false"}
            )
            self.window_tracker.set_focus_guard(
                enabled,
                self._focus_app_path,
                self._focus_guard_rules,
            )
        except (sqlite3.Error, TypeError, ValueError) as error:
            if self._focus_guard_enabled:
                self.focus_guard_switch.select()
            else:
                self.focus_guard_switch.deselect()
            self.status_label.configure(text=f"Could not update focus guard: {error}")
            return

        self._focus_guard_enabled = enabled
        self.status_label.configure(
            text=(
                "Focus guard enabled; pause or stop work to release it."
                if enabled
                else "Focus guard disabled."
            )
        )

    def _add_focus_guard_rule(self) -> None:
        match_type = self.FOCUS_GUARD_MATCH_TYPES.get(
            self.focus_guard_rule_type_menu.get()
        )
        pattern = self.focus_guard_rule_entry.get().strip()
        if match_type is None or not pattern:
            self.status_label.configure(
                text="Choose a block-rule type and enter text to match."
            )
            return
        self._focus_guard_rules.append((match_type, pattern, True))
        self._focus_guard_rules_dirty = True
        self.focus_guard_rule_entry.delete(0, "end")
        self._render_focus_guard_rules()

    def _toggle_focus_guard_rule(self, index: int) -> None:
        match_type, pattern, enabled = self._focus_guard_rules[index]
        self._focus_guard_rules[index] = (match_type, pattern, not enabled)
        self._focus_guard_rules_dirty = True
        self._render_focus_guard_rules()

    def _remove_focus_guard_rule(self, index: int) -> None:
        del self._focus_guard_rules[index]
        self._focus_guard_rules_dirty = True
        self._render_focus_guard_rules()

    def _render_focus_guard_rules(self) -> None:
        for child in self.focus_guard_rules_frame.winfo_children():
            child.destroy()

        labels_by_type = {
            value: label for label, value in self.FOCUS_GUARD_MATCH_TYPES.items()
        }
        for index, (match_type, pattern, enabled) in enumerate(
            self._focus_guard_rules
        ):
            row = ctk.CTkFrame(self.focus_guard_rules_frame, fg_color="transparent")
            row.grid(row=index, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(1, weight=1)
            enabled_checkbox = ctk.CTkCheckBox(
                row,
                text="Enabled",
                width=88,
                command=lambda rule_index=index: self._toggle_focus_guard_rule(
                    rule_index
                ),
                text_color=COLORS["text_secondary"],
                progress_color=COLORS["accent"],
            )
            if enabled:
                enabled_checkbox.select()
            else:
                enabled_checkbox.deselect()
            enabled_checkbox.grid(row=0, column=0, padx=(4, 8))
            ctk.CTkLabel(
                row,
                text=f"{labels_by_type[match_type]} contains: {pattern}",
                anchor="w",
                justify="left",
                wraplength=380,
                text_color=COLORS["text_secondary"],
            ).grid(row=0, column=1, sticky="ew", padx=4)
            themed_button(
                row,
                text="Remove",
                width=72,
                command=lambda rule_index=index: self._remove_focus_guard_rule(
                    rule_index
                ),
                fg_color=COLORS["control"],
                hover_color=COLORS["control_hover"],
            ).grid(row=0, column=2, padx=(8, 4))

    def _save_focus_guard_rules(self) -> None:
        try:
            self.database.save_focus_guard_rules(self._focus_guard_rules)
            stored_rules = self.database.get_focus_guard_rules()
            self._focus_guard_rules = [
                (rule.match_type, rule.pattern, rule.enabled)
                for rule in stored_rules
            ]
            self._focus_guard_rules_dirty = False
            self._configure_focus_guard()
        except (sqlite3.Error, TypeError, ValueError) as error:
            self.status_label.configure(
                text=f"Could not save focus-guard rules: {error}"
            )
            return
        self._render_focus_guard_rules()
        self.status_label.configure(text="Focus-guard rules saved.")

    def _ensure_focus_app_from_tracker(self, exclude_handle: int | None) -> None:
        if self._closing or not self._focus_app_path:
            return
        try:
            result = self.focus_app_controller.focus_or_launch(
                self._focus_app_path,
                exclude_handle=exclude_handle,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._report_background_error(f"Could not restore the work app: {error}")
            return
        if result == "launched":
            self._report_background_error("Work app closed; reopening it to restore focus.")

    def _return_to_work_app(self) -> None:
        if not self._focus_app_path:
            self.status_label.configure(text="Work session resumed.")
            return
        try:
            result = self.focus_app_controller.focus_or_launch(self._focus_app_path)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.status_label.configure(text=f"Could not return to the work app: {error}")
            return
        if result == "launched":
            self.status_label.configure(text="Work app closed; reopening it now.")
        elif result == "launch_pending":
            self.status_label.configure(text="Work app is starting.")
        else:
            self.status_label.configure(text="Work session resumed in the selected app.")

    def _save_user_name(self) -> None:
        try:
            user_name = normalize_user_name(self.user_name_entry.get())
            self.database.set_settings({"user_name": user_name})
        except (sqlite3.Error, TypeError, ValueError) as error:
            self.status_label.configure(text=f"Could not save your name: {error}")
            return

        self._user_name = user_name
        self._refresh_dashboard_greeting()
        self.status_label.configure(text="Your name was saved.")

    def _refresh_dashboard_greeting(self) -> None:
        if self._closing:
            return
        if self._greeting_refresh_after_id is not None:
            self.after_cancel(self._greeting_refresh_after_id)
            self._greeting_refresh_after_id = None

        now = datetime.now()
        greeting = get_time_of_day_greeting(now.hour)
        name_suffix = f", {self._user_name}" if self._user_name else ""
        self.greeting_label.configure(text=f"{greeting}{name_suffix}!")
        next_change = get_next_greeting_change(now)
        delay_ms = max(1, math.ceil((next_change - now).total_seconds() * 1000))
        self._greeting_refresh_after_id = self.after(
            delay_ms,
            self._on_greeting_boundary,
        )

    def _on_greeting_boundary(self) -> None:
        self._greeting_refresh_after_id = None
        self._refresh_dashboard_greeting()

    def _show_onboarding(self) -> None:
        if (
            self._onboarding_window is not None
            and self._onboarding_window.winfo_exists()
        ):
            self._onboarding_window.lift()
            return

        dialog = ctk.CTkToplevel(self)
        self._onboarding_window = dialog
        dialog.title("Welcome to MindShield")
        dialog.geometry("600x460")
        dialog.minsize(520, 400)
        dialog.resizable(True, True)
        dialog.transient(self)
        dialog.protocol("WM_DELETE_WINDOW", self._finish_onboarding)
        self._onboarding_content = SwipeScrollableFrame(
            dialog,
            width=464,
            height=352,
            fg_color="transparent",
        )
        self._onboarding_content.pack(
            fill="both",
            expand=True,
            padx=28,
            pady=24,
        )
        dialog.grab_set()
        if self._user_name:
            self._render_walkthrough_step(0)
        else:
            self._render_onboarding_name_step()

    def _render_onboarding_name_step(self) -> None:
        content = self._onboarding_content
        for child in content.winfo_children():
            child.destroy()

        ctk.CTkLabel(
            content,
            text="Welcome to MindShield",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(8, 12))
        ctk.CTkLabel(
            content,
            text=(
                "What should we call you? Your name is stored in your encrypted "
                "local settings and used for your Dashboard greeting."
            ),
            justify="left",
            wraplength=440,
            text_color=COLORS["text_secondary"],
        ).pack(fill="x", pady=8)
        self._onboarding_name_entry = ctk.CTkEntry(
            content,
            placeholder_text="Your name",
            height=38,
        )
        self._onboarding_name_entry.pack(fill="x", pady=(12, 4))
        self._onboarding_feedback_label = ctk.CTkLabel(
            content,
            text="",
            anchor="w",
            text_color=COLORS["text_muted"],
        )
        self._onboarding_feedback_label.pack(fill="x", pady=(0, 8))
        actions = ctk.CTkFrame(content, fg_color="transparent")
        actions.pack(fill="x", pady=(20, 8))
        themed_button(
            actions,
            text="Skip walkthrough",
            command=lambda: self._save_onboarding_name(start_walkthrough=False),
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).pack(side="right")
        themed_button(
            actions,
            text="Continue",
            command=lambda: self._save_onboarding_name(start_walkthrough=True),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        ).pack(side="right", padx=(0, 8))

    def _save_onboarding_name(self, start_walkthrough: bool) -> None:
        if self._onboarding_name_entry is None:
            raise RuntimeError("The onboarding name field is unavailable")
        try:
            user_name = normalize_user_name(self._onboarding_name_entry.get())
            self.database.set_settings({"user_name": user_name})
        except (sqlite3.Error, TypeError, ValueError) as error:
            if self._onboarding_feedback_label is not None:
                self._onboarding_feedback_label.configure(text=str(error))
            return

        self._user_name = user_name
        self.user_name_entry.delete(0, "end")
        self.user_name_entry.insert(0, user_name)
        self._refresh_dashboard_greeting()
        if start_walkthrough:
            self._render_walkthrough_step(0)
        else:
            self._finish_onboarding()

    def _render_walkthrough_step(self, step: int) -> None:
        steps = (
            (
                "Set up your work zone",
                "Choose a work-zone profile that matches the app or document you "
                "use. Settings also lets you enable the optional, best-effort app "
                "focus guard.",
            ),
            (
                "Focus with your timer",
                "Set a custom focus duration on the Dashboard, then start work. "
                "That interval also sets when the automatic eye-care break starts. "
                "Adjust it and the eye-care break length in Settings. Paused time "
                "is not counted as work.",
            ),
            (
                "Take timed breaks",
                "Choose a 5-, 10-, or 15-minute manual break. MindShield also "
                "keeps its short automatic eye-care break. After a break, it "
                "returns to your selected work app when one is configured.",
            ),
            (
                "Review your productivity",
                "Profile & Analytics shows work, break totals, and session "
                "history that you can search and export.",
            ),
        )
        if not 0 <= step < len(steps):
            raise ValueError("Walkthrough step is out of range")

        content = self._onboarding_content
        for child in content.winfo_children():
            child.destroy()
        title, description = steps[step]
        ctk.CTkLabel(
            content,
            text=f"Step {step + 1} of {len(steps)}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["highlight"],
        ).pack(anchor="w", pady=(8, 4))
        ctk.CTkLabel(
            content,
            text=title,
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", pady=(0, 12))
        ctk.CTkLabel(
            content,
            text=description,
            justify="left",
            wraplength=440,
            text_color=COLORS["text_secondary"],
        ).pack(fill="x", pady=8)
        actions = ctk.CTkFrame(content, fg_color="transparent")
        actions.pack(fill="x", pady=(34, 8))
        themed_button(
            actions,
            text="Skip walkthrough",
            command=self._finish_onboarding,
            fg_color=COLORS["control"],
            hover_color=COLORS["control_hover"],
        ).pack(side="left")
        if step > 0:
            themed_button(
                actions,
                text="Back",
                command=lambda: self._render_walkthrough_step(step - 1),
                fg_color=COLORS["control"],
                hover_color=COLORS["control_hover"],
            ).pack(side="right", padx=(8, 0))
        themed_button(
            actions,
            text="Finish" if step == len(steps) - 1 else "Next",
            command=(
                self._finish_onboarding
                if step == len(steps) - 1
                else lambda: self._render_walkthrough_step(step + 1)
            ),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        ).pack(side="right")
        self._start_onboarding_transition()

    def _start_onboarding_transition(self) -> None:
        dialog = self._onboarding_window
        if dialog is None or not dialog.winfo_exists():
            return
        if self._onboarding_animation_after_id is not None:
            self.after_cancel(self._onboarding_animation_after_id)
            self._onboarding_animation_after_id = None

        dialog.attributes("-alpha", 0.9)
        self._onboarding_animation_step = 0
        self._animate_onboarding_transition()

    def _animate_onboarding_transition(self) -> None:
        dialog = self._onboarding_window
        if dialog is None or not dialog.winfo_exists() or self._closing:
            self._onboarding_animation_after_id = None
            return

        self._onboarding_animation_step += 1
        position = min(1.0, self._onboarding_animation_step / 6)
        eased_position = position * position * (3 - 2 * position)
        dialog.attributes("-alpha", 0.9 + 0.1 * eased_position)
        if self._onboarding_animation_step >= 6:
            self._onboarding_animation_after_id = None
            return
        self._onboarding_animation_after_id = self.after(
            16,
            self._animate_onboarding_transition,
        )

    def _finish_onboarding(self) -> None:
        if not self._user_name:
            self._render_onboarding_name_step()
            if self._onboarding_feedback_label is not None:
                self._onboarding_feedback_label.configure(
                    text="Enter your name before finishing setup."
                )
            return
        try:
            self.database.set_settings({"onboarding_completed": "true"})
        except sqlite3.Error as error:
            self.status_label.configure(
                text=f"Could not save onboarding preference: {error}"
            )
            return
        self._show_first_run_onboarding = False
        dialog = self._onboarding_window
        self._onboarding_window = None
        if self._onboarding_animation_after_id is not None:
            self.after_cancel(self._onboarding_animation_after_id)
            self._onboarding_animation_after_id = None
        if dialog is not None and dialog.winfo_exists():
            dialog.grab_release()
            dialog.destroy()
        self._onboarding_name_entry = None
        self._onboarding_feedback_label = None

    def _save_break_prompt(self, label: str) -> None:
        mode = {
            "Eye-care (20-20-20)": "eye_care",
            "Stretch": "stretch",
            "Hydrate": "hydrate",
            "No prompt": "none",
        }.get(label)
        if mode is None:
            self.break_prompt_menu.set(
                {
                    "eye_care": "Eye-care (20-20-20)",
                    "stretch": "Stretch",
                    "hydrate": "Hydrate",
                    "none": "No prompt",
                }[self._break_prompt_mode]
            )
            self.status_label.configure(text="Choose a supported break prompt.")
            return

        try:
            self.database.set_settings({"break_prompt": mode})
        except sqlite3.Error as error:
            self.status_label.configure(text=f"Could not save break prompt: {error}")
            return
        self._break_prompt_mode = mode
        self.status_label.configure(text="Break prompt preference saved.")

    def _toggle_distraction_suppression(self) -> None:
        enabled = self.suppression_switch.get() in (True, 1, "on")
        try:
            self.database.set_settings(
                {"suppress_distractions": "true" if enabled else "false"}
            )
        except sqlite3.Error as error:
            if enabled:
                self.suppression_switch.deselect()
            else:
                self.suppression_switch.select()
            self.status_label.configure(
                text=f"Could not save distraction suppression: {error}"
            )
            return

        self._suppress_distractions_enabled = enabled
        self.window_tracker.set_suppress_distractions(enabled)
        if enabled:
            self.status_label.configure(
                text="Distraction suppression enabled. "
                "Choose a specific work-zone match to avoid minimizing other apps."
            )
        else:
            self.status_label.configure(text="Distraction suppression disabled.")

    def _apply_timer_settings(self) -> None:
        try:
            work_minutes = int(self.work_interval_entry.get())
            break_seconds = int(self.break_duration_entry.get())
            if not 1 <= work_minutes <= 240 or break_seconds <= 0:
                raise ValueError
        except ValueError:
            self.status_label.configure(
                text=(
                    "Enter a focus/eye-care interval from 1 to 240 minutes and "
                    "a positive eye-care break length in seconds."
                )
            )
            return

        try:
            self.database.set_settings(
                {
                    "work_interval_minutes": str(work_minutes),
                    "break_duration_seconds": str(break_seconds),
                }
            )
        except sqlite3.Error as error:
            self.status_label.configure(text=f"Could not save timer settings: {error}")
            return

        self.timer_manager.configure_intervals(work_minutes * 60, break_seconds)
        self._work_interval_minutes = work_minutes
        self._break_duration_seconds = break_seconds
        self.focus_duration_entry.delete(0, "end")
        self.focus_duration_entry.insert(0, str(work_minutes))
        self.status_label.configure(text="Focus and eye-care settings applied.")
        if not self._demo_mode_enabled():
            self._set_countdown_display(work_minutes * 60)

    def _apply_daily_goal(self) -> None:
        try:
            daily_goal_minutes = int(self.daily_goal_entry.get())
            if daily_goal_minutes <= 0:
                raise ValueError
        except ValueError:
            self.status_label.configure(
                text="Enter a positive whole number for the daily focus goal."
            )
            return

        try:
            self.database.set_settings(
                {"daily_goal_minutes": str(daily_goal_minutes)}
            )
        except sqlite3.Error as error:
            self.status_label.configure(text=f"Could not save daily goal: {error}")
            return

        self._daily_goal_minutes = daily_goal_minutes
        self._refresh_analytics()
        self.status_label.configure(text="Daily focus goal saved.")

    def _meeting_duration_label(self) -> str:
        return {
            30: "30 minutes",
            60: "1 hour",
            120: "2 hours",
        }[self._meeting_mode_minutes]

    def _save_meeting_mode_duration(self, label: str) -> None:
        duration_minutes = {
            "30 minutes": 30,
            "1 hour": 60,
            "2 hours": 120,
        }.get(label)
        if duration_minutes is None:
            self.meeting_duration_menu.set(self._meeting_duration_label())
            self.status_label.configure(text="Choose a supported meeting mode duration.")
            return

        try:
            self.database.set_settings(
                {"meeting_mode_minutes": str(duration_minutes)}
            )
        except sqlite3.Error as error:
            self.meeting_duration_menu.set(self._meeting_duration_label())
            self.status_label.configure(
                text=f"Could not save meeting mode duration: {error}"
            )
            return

        self._meeting_mode_minutes = duration_minutes
        self.status_label.configure(
            text=f"Meeting mode duration set to {label}."
        )

    def _save_break_overlay_mode(self, label: str) -> None:
        mode = {
            "Full-screen overlay": "overlay",
            "Corner toast": "toast",
        }.get(label)
        if mode is None:
            self.overlay_mode_menu.set(
                "Corner toast"
                if self._break_overlay_mode == "toast"
                else "Full-screen overlay"
            )
            self.status_label.configure(text="Choose a supported break reminder style.")
            return

        try:
            self.database.set_settings({"break_overlay_mode": mode})
        except sqlite3.Error as error:
            self.overlay_mode_menu.set(
                "Corner toast"
                if self._break_overlay_mode == "toast"
                else "Full-screen overlay"
            )
            self.status_label.configure(
                text=f"Could not save break reminder style: {error}"
            )
            return

        self._break_overlay_mode = mode
        self.status_label.configure(text=f"Break reminder style set to {label}.")

    def _save_appearance_mode(self, label: str) -> None:
        mode = APPEARANCE_MODES.get(label)
        if mode is None:
            self.appearance_menu.set(self._appearance_label())
            self.dashboard_appearance_menu.set(self._appearance_label())
            self.status_label.configure(text="Choose a supported appearance mode.")
            return

        try:
            self.database.set_settings({"appearance_mode": mode})
        except sqlite3.Error as error:
            self.appearance_menu.set(self._appearance_label())
            self.dashboard_appearance_menu.set(self._appearance_label())
            self.status_label.configure(text=f"Could not save appearance mode: {error}")
            return

        self._appearance_mode = mode
        self.appearance_menu.set(label)
        self.dashboard_appearance_menu.set(label)
        ctk.set_appearance_mode(mode)
        self.status_label.configure(text=f"Appearance set to {label}.")

    def _save_text_size(self, label: str) -> None:
        scaling = TEXT_SIZE_SCALING.get(label)
        if scaling is None:
            self.text_size_menu.set(self._text_size)
            self.status_label.configure(text="Choose a supported text size.")
            return

        try:
            self.database.set_settings({"text_size": label})
        except sqlite3.Error as error:
            self.text_size_menu.set(self._text_size)
            self.status_label.configure(text=f"Could not save text size: {error}")
            return

        self._text_size = label
        ctk.set_widget_scaling(scaling)
        self.status_label.configure(text=f"Text size set to {label}.")

    def _toggle_demo_mode(self) -> None:
        enabled = self._demo_mode_enabled()
        try:
            self.database.set_settings(
                {"demo_mode": "true" if enabled else "false"}
            )
        except sqlite3.Error as error:
            if enabled:
                self.demo_switch.deselect()
            else:
                self.demo_switch.select()
            self.status_label.configure(text=f"Could not save demo mode: {error}")
            return

        self.timer_manager.toggle_demo_mode(enabled)
        if enabled:
            self._set_countdown_display(self.timer_manager.work_interval_sec)
            self.status_label.configure(text="Demo mode enabled: 10 seconds work / 5 seconds break.")
        else:
            self._set_countdown_display(self.timer_manager.work_interval_sec)
            self.status_label.configure(text="Demo mode disabled.")

    def _demo_mode_enabled(self) -> bool:
        value = self.demo_switch.get()
        return value is True or value == 1 or value == "on"

    def _on_timer_tick(self, remaining_seconds: int) -> None:
        if self._closing:
            return
        self.after(0, self._update_countdown, remaining_seconds)

    def _on_timer_state_update(self, state: str, _remaining_seconds: int) -> None:
        if self._closing:
            return
        self.after(0, self._display_timer_state, state, _remaining_seconds)

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
        self.status_label.configure(text=messages.get(state, f"Timer state: {state}."))
        if self._overlay is not None and self._overlay.winfo_exists():
            self._overlay.set_paused(state == "break_paused")
        self._update_countdown(remaining_seconds)

    def _update_countdown(self, remaining_seconds: int) -> None:
        self._set_countdown_display(remaining_seconds)

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

    def _animate_timer_progress(self, target: float) -> None:
        if self._progress_animation_after_id is not None:
            self.after_cancel(self._progress_animation_after_id)
            self._progress_animation_after_id = None
        self._progress_animation_start = self._timer_progress_value
        self._progress_animation_target = target
        self._progress_animation_step = 0
        self._advance_timer_progress()

    def _advance_timer_progress(self) -> None:
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
                    self.database.log_work_segment(
                        work_seconds,
                        self.active_work_zone,
                    )
                self._scheduled_break_id = self.database.log_scheduled_break_start(
                    max(1, break_seconds),
                    self.active_work_zone,
                    break_type=break_type,
                )
            if not self._closing:
                self.after(0, self._refresh_analytics)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self._report_background_error(f"Could not save break/work time: {error}")
        if not self._manual_break_active and not self._closing:
            self.after(0, self._show_break_overlay)
        if not self._closing:
            self.after(
                0,
                lambda: self.status_label.configure(text="Break time."),
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
            on_pause=self._toggle_work_pause,
            on_return_to_work=self._return_to_work_early,
        )

    def _on_break_end(self) -> None:
        self._finish_break(completed=True)

    def _on_break_end_early(self) -> None:
        self._finish_break(completed=False)

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

    def _close_break_overlay(self) -> None:
        if self._overlay is not None:
            if self._overlay.winfo_exists():
                self._overlay.destroy()
            self._overlay = None

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

    def _report_background_error(self, message: str) -> None:
        if not self._closing:
            self.after(0, lambda: self.status_label.configure(text=message))

    def _session_history_dates(self) -> tuple[date | None, date | None]:
        selected_range = self.history_range_menu.get()
        if selected_range == "All history":
            return None, None
        range_days = {
            "Last 7 days": 7,
            "Last 30 days": 30,
        }.get(selected_range)
        if range_days is None:
            raise ValueError(f"Unsupported session history range: {selected_range}")
        today = date.today()
        return today - timedelta(days=range_days - 1), today

    def _refresh_session_history(self) -> None:
        try:
            start_date, end_date = self._session_history_dates()
            records = self.database.get_session_history(
                start_date=start_date,
                end_date=end_date,
                search=self.history_search_entry.get(),
            )
        except (sqlite3.Error, TypeError, ValueError) as error:
            self.status_label.configure(text=f"Could not load session history: {error}")
            return

        self.session_history_text.configure(state="normal")
        self.session_history_text.delete("1.0", "end")
        if records:
            self.session_history_text.insert(
                "end",
                "Date        Start      Minutes  Work zone\n"
                "------------------------------------------------------------\n",
            )
            for record in records:
                self.session_history_text.insert(
                    "end",
                    f"{record.date:<12}{record.start_time:<11}"
                    f"{record.duration_minutes:<9}{record.work_zone}\n",
                )
        else:
            self.session_history_text.insert(
                "end",
                "No sessions match these filters.",
            )
        self.session_history_text.configure(state="disabled")

    def _export_session_history(self) -> None:
        export_path = filedialog.asksaveasfilename(
            parent=self,
            title="Export MindShield session history",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not export_path:
            return
        try:
            start_date, end_date = self._session_history_dates()
            exported_count = self.database.export_sessions_csv(
                export_path,
                start_date=start_date,
                end_date=end_date,
                search=self.history_search_entry.get(),
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            self.status_label.configure(text=f"Could not export session history: {error}")
            return
        self.status_label.configure(
            text=f"Exported {exported_count} session(s) to {export_path}."
        )

    def _productivity_summary_dates(self) -> tuple[date | None, date | None]:
        selected_range = self.productivity_range_menu.get()
        if selected_range == "All history":
            return None, None
        range_days = {
            "Last 7 days": 7,
            "Last 30 days": 30,
        }.get(selected_range)
        if range_days is None:
            raise ValueError(f"Unsupported productivity range: {selected_range}")
        today = date.today()
        return today - timedelta(days=range_days - 1), today

    @staticmethod
    def _format_tracked_duration(duration_seconds: int) -> str:
        if duration_seconds < 60:
            return f"{duration_seconds} sec"
        hours, remainder = divmod(duration_seconds // 60, 60)
        minutes = remainder
        if hours:
            return f"{hours} hr {minutes} min"
        return f"{minutes} min"

    def _refresh_productivity_profile(self) -> None:
        try:
            start_date, end_date = self._productivity_summary_dates()
            summary = self.database.get_productivity_summary(
                start_date=start_date,
                end_date=end_date,
            )
        except (sqlite3.Error, TypeError, ValueError) as error:
            self.status_label.configure(
                text=f"Could not load productivity profile: {error}"
            )
            return

        work_seconds = summary.work_seconds
        break_seconds = summary.break_seconds
        total_seconds = work_seconds + break_seconds
        self.productivity_work_label.configure(
            text=f"Work: {self._format_tracked_duration(work_seconds)}"
        )
        self.productivity_break_label.configure(
            text=f"Breaks: {self._format_tracked_duration(break_seconds)}"
        )
        self.productivity_progress.set(
            work_seconds / total_seconds if total_seconds else 0
        )
        if not total_seconds:
            ratio = "Work/break ratio: no tracked time"
        elif not break_seconds:
            ratio = "Work/break ratio: no completed breaks yet"
        else:
            ratio = (
                f"Work/break ratio: {work_seconds / break_seconds:.1f}:1 "
                f"({work_seconds / total_seconds:.0%} work)"
            )
        self.productivity_ratio_label.configure(text=ratio)

    def _refresh_analytics(self) -> None:
        logs = self.database.get_weekly_logs()
        today_minutes = self.database.get_todays_total_minutes()
        weekly_minutes = sum(total_minutes for _, total_minutes in logs)
        streak = self.database.get_current_streak_days()
        monthly_minutes = self.database.get_monthly_total_minutes()
        completed_breaks, total_breaks = self.database.get_monthly_break_compliance()
        weekly_goal_minutes = self._daily_goal_minutes * self.WEEKLY_GOAL_DAYS

        self.today_minutes_label.configure(text=f"{today_minutes} min")
        self.streak_label.configure(text=f"{streak} day{'s' if streak != 1 else ''}")
        self.daily_goal_progress.set(
            min(1.0, today_minutes / self._daily_goal_minutes)
        )
        self.daily_goal_summary_label.configure(
            text=f"{today_minutes} / {self._daily_goal_minutes} minutes"
        )
        self.monthly_focus_label.configure(text=f"{monthly_minutes} min")
        if total_breaks:
            compliance = round(completed_breaks / total_breaks * 100)
            self.break_compliance_label.configure(
                text=f"{compliance}% ({completed_breaks}/{total_breaks})"
            )
        else:
            self.break_compliance_label.configure(text="No breaks yet")
        self.weekly_progress.set(
            min(1.0, weekly_minutes / weekly_goal_minutes)
        )
        self.weekly_summary_label.configure(
            text=f"{weekly_minutes} / {weekly_goal_minutes} minutes"
        )
        self._refresh_session_history()
        self._refresh_productivity_profile()

    def _reset_analytics(self) -> None:
        message = (
            "This permanently clears saved focus totals, manual and scheduled "
            "break history, and session history. Settings and work-zone profiles "
            "will remain. If work is active, its tracked duration will restart "
            "from zero; a break already in progress will remain tracked."
        )
        if not messagebox.askyesno(
            "Reset analytics?",
            message,
            parent=self,
        ):
            return

        try:
            with self._activity_data_lock:
                self.database.reset_analytics(
                    preserve_break_id=self._scheduled_break_id
                )
                self.timer_manager.reset_productivity_tracking()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            messagebox.showerror(
                "Could not reset analytics",
                str(error),
                parent=self,
            )
            self.status_label.configure(text=f"Could not reset analytics: {error}")
            return

        self._refresh_analytics()
        messagebox.showinfo(
            "Analytics reset",
            "Saved focus totals and session history were cleared.",
            parent=self,
        )

    def destroy(self) -> None:
        """Stop background workers and owned windows before closing the app."""
        if not self._closing:
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
            if self.tray_manager is not None:
                self.tray_manager.stop()
            if self._overlay is not None:
                self._close_break_overlay()
        super().destroy()
