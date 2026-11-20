"""Work-zone profile editor."""

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

class WorkZoneEditorMixin:
    """Work-zone profile editor."""

    def _new_work_zone_profile(self) -> None:
        self._editing_profile_id = None
        self.profile_name_entry.delete(0, "end")
        self._profile_rules = []
        self._render_profile_rules()
        self.status_label.configure(
            text="Create a profile name and add at least one enabled rule."
        )


    def _add_work_zone_rule(self) -> None:
        match_type = self.PROFILE_MATCH_TYPES.get(self.profile_rule_type_menu.get())
        match_mode = self.MATCH_MODE_LABELS.get(self.profile_rule_mode_menu.get(), "contains")
        pattern = self.profile_rule_entry.get().strip()
        if match_type is None or not pattern:
            self.status_label.configure(text="Choose a rule type and enter match text.")
            return
        self._profile_rules.append((match_type, pattern, True, match_mode))
        self.profile_rule_entry.delete(0, "end")
        self._render_profile_rules()


    def _remove_work_zone_rule(self, index: int) -> None:
        del self._profile_rules[index]
        self._render_profile_rules()


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
                (rule.match_type, rule.pattern, rule.enabled, rule.match_mode)
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


    @staticmethod
    def _match_mode_text(match_mode: str) -> str:
        return "whole word" if match_mode == "token" else "contains"


    def _toggle_work_zone_rule(self, index: int) -> None:
        match_type, pattern, enabled, match_mode = self._profile_rules[index]
        self._profile_rules[index] = (match_type, pattern, not enabled, match_mode)
        self._render_profile_rules()


    def _load_profile_editor(self, profile: WorkZoneProfile) -> None:
        self._editing_profile_id = profile.id
        self.profile_name_entry.delete(0, "end")
        self.profile_name_entry.insert(0, profile.name)
        self._profile_rules = [
            (rule.match_type, rule.pattern, rule.enabled, rule.match_mode)
            for rule in profile.rules
        ]
        self._render_profile_rules()


    def _save_work_zone_profile(self) -> None:
        name = self.profile_name_entry.get().strip()
        if not name or not any(enabled for _, _, enabled, _mode in self._profile_rules):
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
                (rule.match_type, rule.pattern, rule.enabled, rule.match_mode)
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
                (rule.match_type, rule.pattern, rule.enabled, rule.match_mode)
                for rule in active_profile.rules
            ],
        )
        self.work_zone_label.configure(text=f"Active Work Zone: {active_profile.name}")
        self._load_profile_editor(active_profile)
        self.status_label.configure(text=f"Active profile: {active_profile.name}.")


    def _render_profile_rules(self) -> None:
        for child in self.profile_rules_frame.winfo_children():
            child.destroy()

        labels_by_type = {
            value: label for label, value in self.PROFILE_MATCH_TYPES.items()
        }
        for index, (match_type, pattern, enabled, match_mode) in enumerate(self._profile_rules):
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
                text=(
                    f"{labels_by_type[match_type]} "
                    f"{self._match_mode_text(match_mode)}: {pattern}"
                ),
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
            for match_type, pattern, enabled, _match_mode in self._profile_rules
            if enabled
        ]
        if active_rules:
            preview = "Matches when any enabled rule matches: " + "; ".join(active_rules)
        else:
            preview = "Enable at least one rule before saving this profile."
        self.profile_preview_label.configure(text=preview)

