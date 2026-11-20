"""Dashboard tab for the MindShield window."""

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

class DashboardMixin:
    """Dashboard tab for the MindShield window."""

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
            reduced_motion=self._reduced_motion,
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

