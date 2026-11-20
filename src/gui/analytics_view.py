"""Profile, history, and analytics tab."""

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

class AnalyticsMixin:
    """Profile, history, and analytics tab."""

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
                "Date        Start      Duration   Work zone\n"
                "------------------------------------------------------------\n",
            )
            for record in records:
                self.session_history_text.insert(
                    "end",
                    f"{record.date:<12}{record.start_time:<11}"
                    f"{self._format_tracked_duration(record.duration_seconds):<12}"
                    f"{record.work_zone}\n",
                )
        else:
            self.session_history_text.insert(
                "end",
                "No sessions match these filters.",
            )
        self.session_history_text.configure(state="disabled")


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
        today = date.today()
        today_seconds = self.database.get_focus_seconds(today, today)
        weekly_seconds = sum(total_seconds for _, total_seconds in logs)
        streak = self.database.get_current_streak_days()
        month_start = today.replace(day=1)
        monthly_seconds = self.database.get_focus_seconds(month_start, today)
        completed_breaks, total_breaks = self.database.get_monthly_break_compliance()
        weekly_goal_seconds = self._daily_goal_minutes * self.WEEKLY_GOAL_DAYS * 60
        daily_goal_seconds = self._daily_goal_minutes * 60

        self.today_minutes_label.configure(
            text=self._format_tracked_duration(today_seconds)
        )
        self.streak_label.configure(text=f"{streak} day{'s' if streak != 1 else ''}")
        self.daily_goal_progress.set(
            min(1.0, today_seconds / daily_goal_seconds) if daily_goal_seconds else 0
        )
        self.daily_goal_summary_label.configure(
            text=(
                f"{self._format_tracked_duration(today_seconds)} / "
                f"{self._daily_goal_minutes} minutes"
            )
        )
        self.monthly_focus_label.configure(
            text=self._format_tracked_duration(monthly_seconds)
        )
        if total_breaks:
            compliance = round(completed_breaks / total_breaks * 100)
            self.break_compliance_label.configure(
                text=f"{compliance}% ({completed_breaks}/{total_breaks})"
            )
        else:
            self.break_compliance_label.configure(text="No breaks yet")
        self.weekly_progress.set(
            min(1.0, weekly_seconds / weekly_goal_seconds) if weekly_goal_seconds else 0
        )
        self.weekly_summary_label.configure(
            text=(
                f"{self._format_tracked_duration(weekly_seconds)} / "
                f"{self._daily_goal_minutes * self.WEEKLY_GOAL_DAYS} minutes"
            )
        )
        self._refresh_session_history()
        self._refresh_productivity_profile()


    @staticmethod
    def _format_tracked_duration(duration_seconds: int) -> str:
        if duration_seconds <= 0:
            return "0 min"
        if duration_seconds < 60:
            return f"{duration_seconds} sec"
        hours, remainder = divmod(duration_seconds // 60, 60)
        minutes = remainder
        if hours:
            return f"{hours} hr {minutes} min"
        return f"{minutes} min"


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


    def _build_analytics(self) -> None:
        tab = SwipeScrollableFrame(
            self.analytics_tab,
            reduced_motion=self._reduced_motion,
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

