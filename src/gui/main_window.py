"""Primary MindShield desktop application window."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import customtkinter as ctk
import sqlcipher3.dbapi2 as sqlite3

from src.core.timer_manager import TimerManager
from src.core.tray_manager import TrayManager
from src.core.window_tracker import WindowTracker
from src.database.db_handler import DatabaseHandler
from src.gui.overlay_window import OverlayWindow


class MainWindow(ctk.CTk):
    """Dark-themed MindShield dashboard, analytics, and settings interface."""

    WEEKLY_GOAL_DAYS = 7

    def __init__(
        self,
        database: DatabaseHandler | None = None,
        db_path: str | Path | None = None,
        timer_manager: TimerManager | None = None,
        window_tracker: WindowTracker | None = None,
        start_tray: bool = True,
    ) -> None:
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color="#0b1220")

        self.title("MindShield")
        self.geometry("900x680")
        self.minsize(760, 580)
        resource_root = Path(
            getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
        )
        self.iconbitmap(default=str(resource_root / "assets" / "mindshield.ico"))

        self.database = database or DatabaseHandler(db_path)
        saved_work_zone = self.database.get_setting("active_work_zone")
        if saved_work_zone is not None and not saved_work_zone.strip():
            raise ValueError("Saved active work zone must not be empty")
        self.active_work_zone = saved_work_zone or "Focus"
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
        saved_demo_mode = self.database.get_setting("demo_mode")
        if saved_demo_mode not in (None, "true", "false"):
            raise ValueError("Saved demo mode setting must be 'true' or 'false'")
        self._demo_mode_enabled_at_startup = saved_demo_mode == "true"
        saved_suppression = self.database.get_setting("suppress_distractions")
        if saved_suppression not in (None, "true", "false"):
            raise ValueError("Saved distraction suppression setting must be 'true' or 'false'")
        self._suppress_distractions_enabled = saved_suppression == "true"
        self._work_started = False
        self._manual_break_active = False
        self._scheduled_break_id: int | None = None
        self._closing = False
        self._last_displayed_timer_state: str | None = None
        self._overlay: OverlayWindow | None = None

        self.timer_manager = timer_manager or TimerManager(
            work_interval_sec=self._work_interval_minutes * 60,
            break_duration_sec=self._break_duration_seconds,
        )
        if timer_manager is None and self._demo_mode_enabled_at_startup:
            self.timer_manager.toggle_demo_mode(True)
        self.timer_manager.on_tick = self._on_timer_tick
        self.timer_manager.on_break_start = self._on_break_start
        self.timer_manager.on_break_end = self._on_break_end
        self.timer_manager.on_state_update = self._on_timer_state_update

        self.window_tracker = window_tracker or WindowTracker(self.active_work_zone)
        self.window_tracker.set_work_zone(self.active_work_zone)
        self.window_tracker.set_suppress_distractions(
            self._suppress_distractions_enabled
        )
        self.window_tracker.on_distraction_detected = self._on_distraction_detected
        self.tray_manager: TrayManager | None = None

        self._build_interface()
        self._refresh_analytics()

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
            text_color="#e2e8f0",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Protect your focus. Make space to recharge.",
            font=ctk.CTkFont(size=13),
            text_color="#94a3b8",
        ).pack(anchor="w", pady=(2, 0))

        self.tabs = ctk.CTkTabview(
            self,
            fg_color="#111b2e",
            segmented_button_fg_color="#1e293b",
            segmented_button_selected_color="#0ea5e9",
            segmented_button_selected_hover_color="#0284c7",
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=24, pady=(8, 24))
        self.dashboard_tab = self.tabs.add("Dashboard")
        self.analytics_tab = self.tabs.add("Profile & Analytics")
        self.settings_tab = self.tabs.add("Settings")

        self._build_dashboard()
        self._build_analytics()
        self._build_settings()

    def _build_dashboard(self) -> None:
        tab = self.dashboard_tab
        tab.grid_columnconfigure(0, weight=1)

        focus_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=18)
        focus_card.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 14))
        ctk.CTkLabel(
            focus_card,
            text="FOCUS SESSION",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
        ).pack(pady=(22, 4))
        initial_minutes, initial_seconds = divmod(
            math.ceil(self.timer_manager.work_interval_sec), 60
        )
        self.countdown_label = ctk.CTkLabel(
            focus_card,
            text=f"{initial_minutes:02d}:{initial_seconds:02d}",
            font=ctk.CTkFont(size=52, weight="bold"),
            text_color="#f8fafc",
        )
        self.countdown_label.pack(pady=(0, 2))
        self.work_zone_label = ctk.CTkLabel(
            focus_card,
            text=f"Active Work Zone: {self.active_work_zone}",
            font=ctk.CTkFont(size=15),
            text_color="#cbd5e1",
        )
        self.work_zone_label.pack(pady=(0, 20))

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=24, pady=10)
        controls.grid_columnconfigure((0, 1), weight=1)
        self.start_button = ctk.CTkButton(
            controls,
            text="Start Work",
            height=42,
            fg_color="#0ea5e9",
            hover_color="#0284c7",
            command=self._start_work,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        break_controls = ctk.CTkFrame(controls, fg_color="transparent")
        break_controls.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        break_controls.grid_columnconfigure(0, weight=1)
        self.break_length_menu = ctk.CTkOptionMenu(
            break_controls,
            values=["5", "10", "15"],
            width=90,
            fg_color="#334155",
            button_color="#475569",
        )
        self.break_length_menu.set("5")
        self.break_length_menu.grid(row=0, column=0, sticky="e", padx=(0, 8))
        self.break_button = ctk.CTkButton(
            break_controls,
            text="Take a Break",
            height=42,
            fg_color="#334155",
            hover_color="#475569",
            command=self._start_selected_break,
        )
        self.break_button.grid(row=0, column=1, sticky="ew")

        zone_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=16)
        zone_card.grid(row=2, column=0, sticky="ew", padx=24, pady=14)
        ctk.CTkLabel(
            zone_card,
            text=(
                "Work zone — match a title, app, executable path, "
                "Explorer folder, or browser URL"
            ),
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#e2e8f0",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 10))
        self.work_zone_selector = ctk.CTkComboBox(
            zone_card,
            values=["Focus", "Visual Studio Code", "Study", "Writing"],
            height=38,
        )
        self.work_zone_selector.set(self.active_work_zone)
        self.work_zone_selector.grid(
            row=1, column=0, sticky="ew", padx=(18, 10), pady=(0, 16)
        )
        ctk.CTkButton(
            zone_card,
            text="Update Work Zone",
            command=self._update_work_zone,
            fg_color="#334155",
            hover_color="#475569",
        ).grid(row=1, column=1, padx=(0, 18), pady=(0, 16))
        zone_card.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            tab,
            text="Ready when you are.",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=13),
        )
        self.status_label.grid(row=3, column=0, sticky="w", padx=28, pady=(10, 0))

    def _build_analytics(self) -> None:
        tab = ctk.CTkScrollableFrame(
            self.analytics_tab,
            fg_color="transparent",
        )
        tab.grid(row=0, column=0, sticky="nsew")
        self.analytics_tab.grid_columnconfigure(0, weight=1)
        self.analytics_tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure((0, 1), weight=1)

        self.today_minutes_label = self._analytics_card(
            tab, "TODAY'S FOCUS", "0 min", 0, 0
        )
        self.streak_label = self._analytics_card(
            tab, "CURRENT STREAK", "0 days", 0, 1
        )

        daily_goal_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=16)
        daily_goal_card.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=24, pady=(8, 16)
        )
        ctk.CTkLabel(
            daily_goal_card,
            text="Daily focus goal",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#e2e8f0",
        ).pack(anchor="w", padx=20, pady=(18, 8))
        self.daily_goal_progress = ctk.CTkProgressBar(
            daily_goal_card,
            height=12,
            progress_color="#34d399",
            fg_color="#334155",
        )
        self.daily_goal_progress.pack(fill="x", padx=20, pady=8)
        self.daily_goal_summary_label = ctk.CTkLabel(
            daily_goal_card,
            text=f"0 / {self._daily_goal_minutes} minutes",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=13),
        )
        self.daily_goal_summary_label.pack(anchor="e", padx=20, pady=(4, 18))

        self.monthly_focus_label = self._analytics_card(
            tab, "THIS MONTH'S FOCUS", "0 min", 2, 0
        )
        self.break_compliance_label = self._analytics_card(
            tab, "SCHEDULED BREAKS THIS MONTH", "No breaks yet", 2, 1
        )

        weekly_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=16)
        weekly_card.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=24, pady=(8, 16)
        )
        ctk.CTkLabel(
            weekly_card,
            text="Weekly focus summary",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#e2e8f0",
        ).pack(anchor="w", padx=20, pady=(18, 8))
        self.weekly_progress = ctk.CTkProgressBar(
            weekly_card,
            height=12,
            progress_color="#38bdf8",
            fg_color="#334155",
        )
        self.weekly_progress.pack(fill="x", padx=20, pady=8)
        self.weekly_summary_label = ctk.CTkLabel(
            weekly_card,
            text="0 / 840 minutes",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=13),
        )
        self.weekly_summary_label.pack(anchor="e", padx=20, pady=(4, 18))

    @staticmethod
    def _analytics_card(
        parent: ctk.CTkFrame | ctk.CTkScrollableFrame,
        title: str,
        value: str,
        row: int,
        column: int,
    ) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color="#17243a", corner_radius=16)
        card.grid(row=row, column=column, sticky="ew", padx=24, pady=24)
        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#94a3b8",
        ).pack(anchor="w", padx=18, pady=(18, 4))
        value_label = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color="#f8fafc",
        )
        value_label.pack(anchor="w", padx=18, pady=(0, 18))
        return value_label

    def _build_settings(self) -> None:
        tab = ctk.CTkScrollableFrame(
            self.settings_tab,
            fg_color="transparent",
        )
        tab.grid(row=0, column=0, sticky="nsew")
        self.settings_tab.grid_columnconfigure(0, weight=1)
        self.settings_tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        interval_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=16)
        interval_card.grid(row=0, column=0, sticky="ew", padx=24, pady=24)
        ctk.CTkLabel(
            interval_card,
            text="Timer intervals",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#e2e8f0",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 14))

        ctk.CTkLabel(
            interval_card,
            text="Work Interval (minutes)",
            text_color="#cbd5e1",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=8)
        self.work_interval_entry = ctk.CTkEntry(interval_card, width=140)
        self.work_interval_entry.insert(0, str(self._work_interval_minutes))
        self.work_interval_entry.grid(row=1, column=1, sticky="e", padx=18, pady=8)

        ctk.CTkLabel(
            interval_card,
            text="Break Duration (seconds)",
            text_color="#cbd5e1",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=8)
        self.break_duration_entry = ctk.CTkEntry(interval_card, width=140)
        self.break_duration_entry.insert(0, str(self._break_duration_seconds))
        self.break_duration_entry.grid(row=2, column=1, sticky="e", padx=18, pady=8)

        ctk.CTkLabel(
            interval_card,
            text="Daily focus goal (minutes)",
            text_color="#cbd5e1",
        ).grid(row=3, column=0, sticky="w", padx=18, pady=8)
        self.daily_goal_entry = ctk.CTkEntry(interval_card, width=140)
        self.daily_goal_entry.insert(0, str(self._daily_goal_minutes))
        self.daily_goal_entry.grid(row=3, column=1, sticky="e", padx=18, pady=8)

        ctk.CTkButton(
            interval_card,
            text="Apply Settings",
            command=self._apply_timer_settings,
            fg_color="#334155",
            hover_color="#475569",
        ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=(12, 6))
        ctk.CTkButton(
            interval_card,
            text="Save Daily Goal",
            command=self._apply_daily_goal,
            fg_color="#334155",
            hover_color="#475569",
        ).grid(row=5, column=0, columnspan=2, sticky="ew", padx=18, pady=(6, 18))
        interval_card.grid_columnconfigure(0, weight=1)

        demo_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=16)
        demo_card.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.demo_switch = ctk.CTkSwitch(
            demo_card,
            text="Demo Mode (10s/5s)",
            command=self._toggle_demo_mode,
            progress_color="#0ea5e9",
        )
        if self._demo_mode_enabled_at_startup:
            self.demo_switch.select()
        self.demo_switch.pack(anchor="w", padx=18, pady=18)

        meeting_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=16)
        meeting_card.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        ctk.CTkLabel(
            meeting_card,
            text="Pause / meeting mode duration",
            text_color="#cbd5e1",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=12)
        self.meeting_duration_menu = ctk.CTkOptionMenu(
            meeting_card,
            values=["30 minutes", "1 hour", "2 hours"],
            command=self._save_meeting_mode_duration,
            fg_color="#334155",
            button_color="#475569",
        )
        self.meeting_duration_menu.set(self._meeting_duration_label())
        self.meeting_duration_menu.grid(row=0, column=1, sticky="e", padx=18, pady=12)
        ctk.CTkButton(
            meeting_card,
            text="Start Meeting Mode",
            height=42,
            command=self._start_meeting_break,
            fg_color="#334155",
            hover_color="#475569",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 16))
        meeting_card.grid_columnconfigure(0, weight=1)

        self.suppression_switch = ctk.CTkSwitch(
            tab,
            text="Automatically minimize windows outside my work zone",
            command=self._toggle_distraction_suppression,
            progress_color="#0ea5e9",
        )
        if self._suppress_distractions_enabled:
            self.suppression_switch.select()
        self.suppression_switch.grid(row=3, column=0, sticky="w", padx=24, pady=12)

        overlay_card = ctk.CTkFrame(tab, fg_color="#17243a", corner_radius=16)
        overlay_card.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 16))
        ctk.CTkLabel(
            overlay_card,
            text="Break reminder style",
            text_color="#cbd5e1",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))
        self.overlay_mode_menu = ctk.CTkOptionMenu(
            overlay_card,
            values=["Full-screen overlay", "Corner toast"],
            command=self._save_break_overlay_mode,
            fg_color="#334155",
            button_color="#475569",
        )
        self.overlay_mode_menu.set(
            "Corner toast" if self._break_overlay_mode == "toast" else "Full-screen overlay"
        )
        self.overlay_mode_menu.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 8))
        ctk.CTkLabel(
            overlay_card,
            text="Choose Corner toast during presentations or video playback.",
            text_color="#94a3b8",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 14))
        overlay_card.grid_columnconfigure(0, weight=1)

    def _start_work(self) -> None:
        if self._work_started:
            self.status_label.configure(text="A work session is already running.")
            return

        try:
            self.timer_manager.start()
            self.window_tracker.start()
        except RuntimeError as error:
            self.status_label.configure(text=str(error))
            return

        self._work_started = True
        self.start_button.configure(state="disabled", text="Work in Progress")
        self.status_label.configure(text=f"Focusing in {self.active_work_zone}.")

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
        self.status_label.configure(text=f"Break started for {minutes} minutes.")

    def _update_work_zone(self) -> None:
        new_zone = self.work_zone_selector.get().strip()
        if not new_zone:
            self.status_label.configure(text="Enter a work-zone name first.")
            return

        try:
            self.database.set_settings({"active_work_zone": new_zone})
        except sqlite3.Error as error:
            self.status_label.configure(text=f"Could not save work zone: {error}")
            return

        self.active_work_zone = new_zone
        self.window_tracker.set_work_zone(new_zone)
        self.work_zone_label.configure(text=f"Active Work Zone: {new_zone}")
        self.status_label.configure(text=f"Work zone updated to {new_zone}.")

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
            if work_minutes <= 0 or break_seconds <= 0:
                raise ValueError
        except ValueError:
            self.status_label.configure(
                text="Enter positive whole numbers for both timer intervals."
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
        self.status_label.configure(text="Timer settings applied.")
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
        messages = {
            "idle": "Ready when you are.",
            "work": f"Focusing in {self.active_work_zone}.",
            "paused": "Work paused.",
            "break": "Break time. Rest your eyes.",
            "manual_break": "Manual break in progress.",
        }
        self.status_label.configure(text=messages.get(state, f"Timer state: {state}."))
        self._update_countdown(remaining_seconds)

    def _update_countdown(self, remaining_seconds: int) -> None:
        self._set_countdown_display(remaining_seconds)

    def _set_countdown_display(self, remaining_seconds: int | float) -> None:
        total_seconds = max(0, math.ceil(remaining_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        self.countdown_label.configure(text=f"{minutes:02d}:{seconds:02d}")

    def _on_break_start(self) -> None:
        self.window_tracker.set_manual_break(True)
        if not self._manual_break_active:
            minutes = int(round(self.timer_manager.work_interval_sec / 60))
            try:
                if minutes > 0:
                    self.database.log_session(minutes, self.active_work_zone)
                self._scheduled_break_id = self.database.log_scheduled_break_start(
                    math.ceil(self.timer_manager.break_duration_sec),
                    self.active_work_zone,
                )
                if not self._closing:
                    self.after(0, self._refresh_analytics)
            except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                self._report_background_error(
                    f"Could not save scheduled break: {error}"
                )
            if not self._closing:
                self.after(0, self._show_break_overlay)
        if not self._closing:
            self.after(
                0,
                lambda: self.status_label.configure(
                    text="Break time. Rest your eyes."
                ),
            )

    def _show_break_overlay(self) -> None:
        if self._closing:
            return
        duration = max(1, math.ceil(self.timer_manager.break_duration_sec))
        self._overlay = OverlayWindow(
            self,
            duration,
            mode=self._break_overlay_mode,
        )

    def _on_break_end(self) -> None:
        self.window_tracker.set_manual_break(False)
        self._manual_break_active = False
        break_id = self._scheduled_break_id
        self._scheduled_break_id = None
        if break_id is not None:
            try:
                self.database.complete_scheduled_break(break_id)
                if not self._closing:
                    self.after(0, self._refresh_analytics)
            except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                self._report_background_error(
                    f"Could not save completed break: {error}"
                )
        if not self._closing:
            self.after(0, self._close_break_overlay)
            self.after(
                0,
                lambda: self.status_label.configure(text="Work session resumed."),
            )

    def _close_break_overlay(self) -> None:
        if self._overlay is not None:
            if self._overlay.winfo_exists():
                self._overlay.destroy()
            self._overlay = None

    def _on_distraction_detected(self, window_title: str) -> None:
        if not self._closing:
            prefix = (
                "Distraction minimized"
                if self._suppress_distractions_enabled
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

    def destroy(self) -> None:
        """Stop background workers and owned windows before closing the app."""
        if not self._closing:
            self._closing = True
            self.timer_manager.stop(timeout=1)
            self.window_tracker.stop(timeout=1)
            if self.tray_manager is not None:
                self.tray_manager.stop()
            if self._overlay is not None:
                self._close_break_overlay()
        super().destroy()
