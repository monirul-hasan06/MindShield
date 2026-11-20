"""Reversible work-app guard controls."""

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

class FocusGuardMixin:
    """Reversible work-app guard controls."""

    def _ensure_focus_app_from_tracker(self, exclude_handle: int | None) -> None:
        """Schedule work-app activation on the Tk thread."""
        if self._closing or not self._focus_app_path:
            return
        self.after(0, self._restore_focus_app, exclude_handle)


    def _remove_focus_guard_rule(self, index: int) -> None:
        del self._focus_guard_rules[index]
        self._focus_guard_rules_dirty = True
        self._render_focus_guard_rules()


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


    def _restore_focus_app(self, exclude_handle: int | None) -> None:
        if self._closing or not self._focus_app_path:
            return
        try:
            result = self.focus_app_controller.focus_or_launch(
                self._focus_app_path,
                exclude_handle=exclude_handle,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._set_status(f"Could not restore the work app: {error}", error=True)
            return
        if result == "launched":
            self._set_status(
                "Work app closed; reopening it to restore focus.",
                error=True,
            )


    def _add_focus_guard_rule(self) -> None:
        match_type = self.FOCUS_GUARD_MATCH_TYPES.get(
            self.focus_guard_rule_type_menu.get()
        )
        match_mode = self.MATCH_MODE_LABELS.get(
            self.focus_guard_rule_mode_menu.get(),
            "contains",
        )
        pattern = self.focus_guard_rule_entry.get().strip()
        if match_type is None or not pattern:
            self.status_label.configure(
                text="Choose a block-rule type and enter text to match."
            )
            return
        self._focus_guard_rules.append((match_type, pattern, True, match_mode))
        self._focus_guard_rules_dirty = True
        self.focus_guard_rule_entry.delete(0, "end")
        self._render_focus_guard_rules()


    def _configure_focus_guard(self) -> None:
        self.window_tracker.set_focus_guard(
            self._focus_guard_enabled,
            self._focus_app_path,
            self._focus_guard_rules,
        )


    def _toggle_focus_guard_rule(self, index: int) -> None:
        match_type, pattern, enabled, match_mode = self._focus_guard_rules[index]
        self._focus_guard_rules[index] = (match_type, pattern, not enabled, match_mode)
        self._focus_guard_rules_dirty = True
        self._render_focus_guard_rules()


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


    def _save_focus_guard_rules(self) -> None:
        try:
            self.database.save_focus_guard_rules(self._focus_guard_rules)
            stored_rules = self.database.get_focus_guard_rules()
            self._focus_guard_rules = [
                (rule.match_type, rule.pattern, rule.enabled, rule.match_mode)
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


    def _render_focus_guard_rules(self) -> None:
        for child in self.focus_guard_rules_frame.winfo_children():
            child.destroy()

        labels_by_type = {
            value: label for label, value in self.FOCUS_GUARD_MATCH_TYPES.items()
        }
        for index, (match_type, pattern, enabled, match_mode) in enumerate(
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
                command=lambda rule_index=index: self._remove_focus_guard_rule(
                    rule_index
                ),
                fg_color=COLORS["control"],
                hover_color=COLORS["control_hover"],
            ).grid(row=0, column=2, padx=(8, 4))

