"""First-run name prompt and feature walkthrough."""

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

class OnboardingMixin:
    """First-run name prompt and feature walkthrough."""

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
            reduced_motion=self._reduced_motion,
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


    def _on_greeting_boundary(self) -> None:
        self._greeting_refresh_after_id = None
        self._refresh_dashboard_greeting()


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


    def _start_onboarding_transition(self) -> None:
        dialog = self._onboarding_window
        if dialog is None or not dialog.winfo_exists():
            return
        if self._onboarding_animation_after_id is not None:
            self.after_cancel(self._onboarding_animation_after_id)
            self._onboarding_animation_after_id = None

        if self._reduced_motion:
            dialog.attributes("-alpha", 1.0)
            return

        dialog.attributes("-alpha", 0.9)
        self._onboarding_animation_step = 0
        self._animate_onboarding_transition()

