"""Settings tab and preference actions."""

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

class SettingsMixin:
    """Settings tab and preference actions."""

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


    def _save_reduced_motion(self) -> None:
        enabled = self.reduced_motion_switch.get() in (True, 1, "on")
        try:
            self.database.set_settings(
                {"reduced_motion": "true" if enabled else "false"}
            )
        except sqlite3.Error as error:
            if self._reduced_motion:
                self.reduced_motion_switch.select()
            else:
                self.reduced_motion_switch.deselect()
            self.status_label.configure(
                text=f"Could not save reduced-motion preference: {error}"
            )
            return

        self._reduced_motion = enabled
        self.dashboard_scroll_frame.set_reduced_motion(enabled)
        self.status_label.configure(
            text=(
                "Reduced motion enabled."
                if enabled
                else "Reduced motion disabled."
            )
        )


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


    def _build_settings(self) -> None:
        tab = SwipeScrollableFrame(
            self.settings_tab,
            reduced_motion=self._reduced_motion,
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
        self.reduced_motion_switch = ctk.CTkSwitch(
            appearance_card,
            text="Reduce motion",
            command=self._save_reduced_motion,
            progress_color=COLORS["accent"],
        )
        if self._reduced_motion:
            self.reduced_motion_switch.select()
        self.reduced_motion_switch.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            padx=18,
            pady=(8, 16),
        )
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
            reduced_motion=self._reduced_motion,
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
        self.profile_rule_mode_menu = themed_option_menu(
            rule_editor,
            values=list(self.MATCH_MODE_LABELS),
            width=120,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.profile_rule_mode_menu.set("Contains")
        self.profile_rule_mode_menu.pack(side="left", padx=(0, 8))
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
            reduced_motion=self._reduced_motion,
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
        self.focus_guard_rule_mode_menu = themed_option_menu(
            guard_rule_editor,
            values=list(self.MATCH_MODE_LABELS),
            width=120,
            fg_color=COLORS["control"],
            button_color=COLORS["control_hover"],
        )
        self.focus_guard_rule_mode_menu.set("Contains")
        self.focus_guard_rule_mode_menu.pack(side="left", padx=(0, 8))
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


    def _meeting_duration_label(self) -> str:
        return {
            30: "30 minutes",
            60: "1 hour",
            120: "2 hours",
        }[self._meeting_mode_minutes]

