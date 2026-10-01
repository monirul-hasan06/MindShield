"""Primary MindShield desktop application window."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from typing import Any

import customtkinter as ctk
from PIL import Image
import sqlite3

from src.core.app_icon import get_app_icon_path, get_app_logo_path
from src.core.timer_manager import TimerManager
from src.core.tray_manager import TrayManager
from src.core.window_tracker import WindowTracker
from src.database.db_handler import DatabaseHandler
from src.gui.overlay_window import OverlayWindow
from src.gui.typography import (
    configure_default_font,
    get_display_font_family,
    register_display_font,
    unregister_display_font,
)


class AnimatedProgressBar(ctk.CTkProgressBar):
    """Ease progress updates instead of snapping between timer/metric values."""

    ANIMATION_FRAMES = 8
    ANIMATION_INTERVAL_MS = 16

    @staticmethod
    def _eased_value(start: float, target: float, frame: int) -> float:
        progress = min(1.0, frame / AnimatedProgressBar.ANIMATION_FRAMES)
        eased_progress = progress * progress * (3 - 2 * progress)
        return start + (target - start) * eased_progress

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._progress_animation_job: str | None = None
        self._displayed_progress = 0.0

    def animate_to(self, value: float) -> None:
        target = min(1.0, max(0.0, value))
        if self._progress_animation_job is not None:
            self.after_cancel(self._progress_animation_job)
            self._progress_animation_job = None
        if not self.winfo_ismapped():
            self._displayed_progress = target
            super().set(target)
            return
        self._animate_progress(self._displayed_progress, target, 1)

    def _animate_progress(
        self,
        start: float,
        target: float,
        frame: int,
    ) -> None:
        self._displayed_progress = self._eased_value(start, target, frame)
        super().set(self._displayed_progress)
        if frame < self.ANIMATION_FRAMES:
            self._progress_animation_job = self.after(
                self.ANIMATION_INTERVAL_MS,
                self._animate_progress,
                start,
                target,
                frame + 1,
            )
        else:
            self._progress_animation_job = None

    def destroy(self) -> None:
        if self._progress_animation_job is not None:
            self.after_cancel(self._progress_animation_job)
            self._progress_animation_job = None
        super().destroy()


class AnimatedActionButton(ctk.CTkButton):
    """Rounded primary action button with brief hover and press color easing."""

    ANIMATION_FRAMES = 5
    ANIMATION_INTERVAL_MS = 16

    def __init__(
        self,
        *args: Any,
        animation_color: str,
        animation_hover_color: str,
        animation_pressed_color: str,
        **kwargs: Any,
    ) -> None:
        self._animation_rest_color = animation_color
        self._animation_hover_color = animation_hover_color
        self._animation_pressed_color = animation_pressed_color
        self._displayed_color = animation_color
        self._color_animation_job: str | None = None
        self._animating_color = False
        super().__init__(*args, **kwargs)
        self.bind("<Enter>", self._animate_on_enter, add="+")
        self.bind("<Leave>", self._animate_on_leave, add="+")
        self.bind("<ButtonPress-1>", self._animate_on_press, add="+")
        self.bind("<ButtonRelease-1>", self._animate_on_release, add="+")

    def _animate_on_enter(self, event: Any) -> None:
        del event
        self._animate_color(
            self._get_color_option(
                "hover_color",
                self._animation_hover_color,
            )
        )

    def _animate_on_leave(self, event: Any) -> None:
        del event
        self._animate_color(self._animation_rest_color)

    def _animate_on_press(self, event: Any) -> None:
        del event
        self.configure(corner_radius=12)
        self._animate_color(self._animation_pressed_color)

    def _animate_on_release(self, event: Any) -> None:
        del event
        self.configure(corner_radius=16)
        self.after(
            80,
            self._animate_color,
            self._get_color_option(
                "hover_color",
                self._animation_hover_color,
            ),
        )

    def _get_color_option(self, option: str, fallback: str | tuple[str, str]) -> str:
        value = self.cget(option)
        if isinstance(value, str) and value.startswith("#"):
            return value
        if isinstance(fallback, str):
            return fallback
        return fallback[0]

    def configure(self, *args: Any, **kwargs: Any) -> None:
        color = kwargs.get("fg_color")
        if (
            not self._animating_color
            and isinstance(color, str)
            and color.startswith("#")
        ):
            self._animation_rest_color = color
            self._displayed_color = color
        super().configure(*args, **kwargs)

    def _animate_color(self, target: str, frame: int = 0) -> None:
        if self._color_animation_job is not None:
            self.after_cancel(self._color_animation_job)
            self._color_animation_job = None
        if frame == 0:
            self._color_animation_start = self._displayed_color
            self._color_animation_target = target
        progress = min(1.0, frame / self.ANIMATION_FRAMES)
        eased_progress = progress * progress * (3 - 2 * progress)
        self._displayed_color = MainWindow._interpolate_color(
            self._color_animation_start,
            self._color_animation_target,
            eased_progress,
        )
        self._animating_color = True
        try:
            self.configure(fg_color=self._displayed_color)
        finally:
            self._animating_color = False
        if frame < self.ANIMATION_FRAMES:
            self._color_animation_job = self.after(
                self.ANIMATION_INTERVAL_MS,
                self._animate_color,
                target,
                frame + 1,
            )
        else:
            self._color_animation_job = None

    def destroy(self) -> None:
        if self._color_animation_job is not None:
            self.after_cancel(self._color_animation_job)
            self._color_animation_job = None
        super().destroy()


class AnimatedSwitch(ctk.CTkSwitch):
    """CustomTkinter switch with an eased sliding-knob transition."""

    ANIMATION_FRAMES = 8
    ANIMATION_INTERVAL_MS = 16
    _slider_animation_job: str | None = None

    def set(self, state: bool, from_variable_callback: bool = False) -> None:
        previous_state = self._check_state
        super().set(state, from_variable_callback)
        if previous_state == bool(state) or not self.winfo_ismapped():
            return
        if self._slider_animation_job is not None:
            self.after_cancel(self._slider_animation_job)
        self._animate_slider(
            float(previous_state),
            float(bool(state)),
            1,
        )

    def _animate_slider(self, start: float, end: float, frame: int) -> None:
        progress = min(1.0, frame / self.ANIMATION_FRAMES)
        eased_progress = progress * progress * (3 - 2 * progress)
        slider_value = start + (end - start) * eased_progress
        self._draw_engine.draw_rounded_slider_with_border_and_button(
            self._apply_widget_scaling(self._switch_width),
            self._apply_widget_scaling(self._switch_height),
            self._apply_widget_scaling(self._corner_radius),
            self._apply_widget_scaling(self._border_width),
            self._apply_widget_scaling(self._button_length),
            self._apply_widget_scaling(self._corner_radius),
            slider_value,
            "w",
        )
        if frame < self.ANIMATION_FRAMES:
            self._slider_animation_job = self.after(
                self.ANIMATION_INTERVAL_MS,
                self._animate_slider,
                start,
                end,
                frame + 1,
            )
        else:
            self._slider_animation_job = None

    def destroy(self) -> None:
        if self._slider_animation_job is not None:
            self.after_cancel(self._slider_animation_job)
            self._slider_animation_job = None
        super().destroy()


class MainWindow(ctk.CTk):
    """MindShield desktop workspace with navigation, overview, and settings pages."""

    WEEKLY_GOAL_DAYS = 7
    THEME_TRANSITION_FRAMES = 10
    THEME_TRANSITION_INTERVAL_MS = 16
    NAVIGATION_ICONS = {
        "overview": "⌂",
        "analytics": "▥",
        "settings": "⚙",
    }
    PAGE_DETAILS = {
        "overview": (
            "Overview",
            "A calm workspace for protecting your focus and taking timely breaks.",
        ),
        "analytics": (
            "Profile & Analytics",
            "Review your progress and manage your focus and break history.",
        ),
        "settings": (
            "Settings",
            "Personalize your focus schedule, reminders, and app behavior.",
        ),
    }
    THEME_COLOR_PAIRS = (
        ("#f4f7fb", "#0e1520"),
        ("#ffffff", "#182231"),
        ("#fbfdff", "#121a26"),
        ("#e4ebf3", "#2a394c"),
        ("#0f172a", "#f5f7fb"),
        ("#172554", "#e8edf5"),
        ("#1e293b", "#dbe5f1"),
        ("#334155", "#c0ccdc"),
        ("#475569", "#aab8ca"),
        ("#64748b", "#8b9bb0"),
        ("#e7eef7", "#263449"),
        ("#d8e3ef", "#34445a"),
        ("#f1f5f9", "#202c3b"),
        ("#e8f4fb", "#12354a"),
        ("#edf4fa", "#1c3447"),
        ("#087cad", "#55c3f3"),
        ("#1788c4", "#62c6f2"),
        ("#1593d0", "#0ea5e9"),
        ("#0284c7", "#0369a1"),
        ("#22a06b", "#34d399"),
        ("#16835d", "#6ee7b7"),
        ("#e8f6ef", "#163b30"),
        ("#d3f1e2", "#1d4b3a"),
        ("#116149", "#a7f3d0"),
        ("#dc4c4c", "#ef6868"),
        ("#bd3434", "#d94b4b"),
    )

    def __init__(
        self,
        database: DatabaseHandler | None = None,
        db_path: str | Path | None = None,
        timer_manager: TimerManager | None = None,
        window_tracker: WindowTracker | None = None,
        start_tray: bool = True,
    ) -> None:
        ctk.set_appearance_mode("light")
        configure_default_font()
        register_display_font()
        super().__init__(fg_color="#f4f7fb")

        self.title("MindShield")
        self.geometry("1180x780")
        self.minsize(1040, 680)
        icon_path = get_app_icon_path()
        self.iconbitmap(default=str(icon_path))
        with Image.open(get_app_logo_path()) as logo_file:
            logo = logo_file.convert("RGBA")
        dark_logo = Image.new("RGBA", logo.size, (235, 242, 250, 0))
        dark_logo.putalpha(logo.getchannel("A"))
        mark = logo.crop((0, 0, 38, logo.height))
        dark_logo.paste(mark, (0, 0), mark)
        self._logo_image = ctk.CTkImage(
            light_image=logo,
            dark_image=dark_logo,
            size=(159, 38),
        )

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
        saved_appearance = self.database.get_setting("appearance_mode")
        if saved_appearance not in (None, "light", "dark"):
            raise ValueError("Saved appearance mode must be 'light' or 'dark'")
        self._appearance_mode = saved_appearance or "light"
        ctk.set_appearance_mode(self._appearance_mode)
        saved_demo_mode = self.database.get_setting("demo_mode")
        if saved_demo_mode not in (None, "true", "false"):
            raise ValueError("Saved demo mode setting must be 'true' or 'false'")
        self._demo_mode_enabled_at_startup = saved_demo_mode == "true"
        saved_suppression = self.database.get_setting("suppress_distractions")
        if saved_suppression not in (None, "true", "false"):
            raise ValueError("Saved distraction suppression setting must be 'true' or 'false'")
        self._suppress_distractions_enabled = saved_suppression == "true"
        saved_application_state = self.database.get_setting("application_enabled")
        if saved_application_state not in (None, "true", "false"):
            raise ValueError("Saved application state must be 'true' or 'false'")
        self._application_enabled = saved_application_state != "false"
        self._work_started = False
        self._manual_break_active = False
        self._scheduled_break_id: int | None = None
        self._application_controls: list[
            ctk.CTkButton
            | ctk.CTkEntry
            | ctk.CTkOptionMenu
            | ctk.CTkComboBox
            | AnimatedSwitch
        ] = []
        self._closing = False
        self._last_displayed_timer_state: str | None = None
        self._focus_pulse_job: str | None = None
        self._focus_pulse_on = False
        self._window_intro_job: str | None = None
        self._tracker_spinner_job: str | None = None
        self._tracker_spinner_frame = 0
        self._current_page = "overview"
        self._page_icon_labels: dict[str, ctk.CTkLabel] = {}
        self._page_icon_rows: dict[str, ctk.CTkFrame] = {}
        self._page_icon_sizes: dict[str, float] = {}
        self._page_icon_jobs: dict[str, str | None] = {}
        self._theme_transition_job: str | None = None
        self._theme_transition_position = (
            1.0 if self._appearance_mode == "dark" else 0.0
        )
        self._theme_transition_start_position = self._theme_transition_position
        self._theme_transition_target_position = self._theme_transition_position
        self._overlay: OverlayWindow | None = None

        self.timer_manager = timer_manager or TimerManager(
            work_interval_sec=self._work_interval_minutes * 60,
            break_duration_sec=self._break_duration_seconds,
        )
        self._configure_timer_from_preferences()
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
        self._set_application_controls_enabled(self._application_enabled)
        self._refresh_analytics()
        self._apply_theme_mode(self._appearance_mode)
        self._set_focus_status("idle")
        self._start_window_intro()

        if start_tray:
            self.attach_tray_manager(
                TrayManager(
                    app_instance=self,
                    on_meeting_mode=self._start_one_hour_meeting_break,
                    on_exit=self.destroy,
                )
            )

    def _configure_timer_from_preferences(self) -> None:
        self.timer_manager.configure_intervals(
            self._work_interval_minutes * 60,
            self._break_duration_seconds,
        )
        self.timer_manager.toggle_demo_mode(self._demo_mode_enabled_at_startup)

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
        self.grid_rowconfigure(0, weight=1)

        workspace = ctk.CTkFrame(self, fg_color="transparent")
        workspace.grid(row=0, column=0, sticky="nsew")
        workspace.grid_columnconfigure(1, weight=1)
        workspace.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(
            workspace,
            width=226,
            fg_color="#fbfdff",
            corner_radius=0,
            border_width=1,
            border_color="#e4ebf3",
        )
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        ctk.CTkLabel(
            sidebar,
            image=self._logo_image,
            text="",
        ).pack(anchor="w", padx=22, pady=(24, 30))
        ctk.CTkLabel(
            sidebar,
            text="WORKSPACE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#64748b",
        ).pack(anchor="w", padx=20, pady=(0, 10))

        self._page_buttons: dict[str, ctk.CTkButton] = {}
        for page, label in (
            ("overview", "Overview"),
            ("analytics", "Profile & Analytics"),
            ("settings", "Settings"),
        ):
            icon = self.NAVIGATION_ICONS[page]
            navigation_row = ctk.CTkFrame(
                sidebar,
                fg_color="#e8f4fb" if page == "overview" else "transparent",
                corner_radius=10,
            )
            navigation_row.pack(fill="x", padx=12, pady=3)
            icon_label = ctk.CTkLabel(
                navigation_row,
                text=icon,
                width=34,
                font=ctk.CTkFont(size=17, weight="bold"),
                text_color="#087cad" if page == "overview" else "#64748b",
            )
            icon_label.pack(side="left", padx=(8, 0), pady=6)
            button = ctk.CTkButton(
                navigation_row,
                text=label,
                anchor="w",
                height=42,
                corner_radius=10,
                border_spacing=8,
                font=ctk.CTkFont(size=14, weight="bold" if page == "overview" else "normal"),
                fg_color="transparent",
                hover_color="#edf4fa",
                text_color="#087cad" if page == "overview" else "#475569",
                command=lambda selected_page=page: self._show_page(selected_page),
            )
            button.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=3)
            for widget in (icon_label, button):
                widget.bind(
                    "<Enter>",
                    lambda event, selected_page=page: self._on_navigation_hover(
                        event,
                        selected_page,
                        True,
                    ),
                    add="+",
                )
                widget.bind(
                    "<Leave>",
                    lambda event, selected_page=page: self._on_navigation_hover(
                        event,
                        selected_page,
                        False,
                    ),
                    add="+",
                )
            self._page_buttons[page] = button
            self._page_icon_labels[page] = icon_label
            self._page_icon_rows[page] = navigation_row
            self._page_icon_sizes[page] = 17.0
            self._page_icon_jobs[page] = None

        sidebar_footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        sidebar_footer.pack(side="bottom", fill="x", padx=18, pady=20)
        ctk.CTkLabel(
            sidebar_footer,
            text="MindShield",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#334155",
        ).pack(anchor="w")
        ctk.CTkLabel(
            sidebar_footer,
            text="Focus well. Rest your eyes.",
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
        ).pack(anchor="w", pady=(3, 0))
        ctk.CTkLabel(
            sidebar_footer,
            text="Appearance",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#64748b",
        ).pack(anchor="w", pady=(18, 7))
        self.appearance_menu = ctk.CTkSegmentedButton(
            sidebar_footer,
            values=["Light", "Dark"],
            command=self._save_appearance_mode,
            selected_color="#1593d0",
            selected_hover_color="#087cad",
            unselected_color="#e7eef7",
            unselected_hover_color="#d8e3ef",
            text_color="#334155",
        )
        self.appearance_menu.set(
            "Dark" if self._appearance_mode == "dark" else "Light"
        )
        self.appearance_menu.pack(fill="x", pady=(0, 2))

        content = ctk.CTkFrame(workspace, fg_color="#f4f7fb", corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)

        page_header = ctk.CTkFrame(content, fg_color="transparent")
        page_header.grid(row=0, column=0, sticky="ew", padx=32, pady=(24, 14))
        page_header.grid_columnconfigure(0, weight=1)
        self.page_title_label = ctk.CTkLabel(
            page_header,
            text="Overview",
            font=ctk.CTkFont(
                family=get_display_font_family(),
                size=22,
                weight="bold",
            ),
            text_color="#172554",
        )
        self.page_title_label.grid(row=0, column=0, sticky="w")
        self.page_description_label = ctk.CTkLabel(
            page_header,
            text=self.PAGE_DETAILS["overview"][1],
            font=ctk.CTkFont(size=13),
            text_color="#64748b",
        )
        self.page_description_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        header_status_group = ctk.CTkFrame(page_header, fg_color="transparent")
        header_status_group.grid(
            row=0, column=1, rowspan=2, sticky="e"
        )
        self.tracker_activity_label = ctk.CTkLabel(
            header_status_group,
            text="LIVE ◴",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#1788c4",
        )
        self.header_status_badge = ctk.CTkLabel(
            header_status_group,
            text="READY",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#16835d",
            fg_color="#e8f6ef",
            corner_radius=14,
            padx=12,
            pady=7,
        )
        self.header_status_badge.pack(side="left")

        self.page_container = ctk.CTkFrame(content, fg_color="transparent")
        self.page_container.grid(row=1, column=0, sticky="nsew", padx=(20, 24), pady=(0, 22))
        self.page_container.grid_columnconfigure(0, weight=1)
        self.page_container.grid_rowconfigure(0, weight=1)

        self.dashboard_tab = ctk.CTkScrollableFrame(
            self.page_container,
            fg_color="transparent",
        )
        self.analytics_tab = ctk.CTkFrame(self.page_container, fg_color="transparent")
        self.settings_tab = ctk.CTkFrame(self.page_container, fg_color="transparent")

        self._build_dashboard()
        self._build_analytics()
        self._build_settings()
        self._show_page("overview")

    def _show_page(self, page: str) -> None:
        """Show a workspace page and update the selected sidebar item."""
        if page not in self.PAGE_DETAILS:
            raise ValueError(f"Unknown MindShield page: {page}")
        if (
            self.timer_manager.state == "work"
            and page != self._current_page
        ):
            self.status_label.configure(
                text="Pause work or take a break before changing workspace."
            )
            return

        title, description = self.PAGE_DETAILS[page]
        self._current_page = page
        self.page_title_label.configure(text=title)
        self.page_description_label.configure(text=description)
        for page_name, button in self._page_buttons.items():
            selected = page_name == page
            button.configure(
                text_color="#087cad" if selected else "#475569",
                font=ctk.CTkFont(
                    size=14,
                    weight="bold" if selected else "normal",
                ),
            )
            instance_attributes = vars(self)
            icon_rows = instance_attributes.get("_page_icon_rows", {})
            icon_labels = instance_attributes.get("_page_icon_labels", {})
            if page_name in icon_rows:
                icon_rows[page_name].configure(
                    fg_color="#e8f4fb" if selected else "transparent"
                )
                icon_labels[page_name].configure(
                    text_color="#087cad" if selected else "#64748b"
                )
                self._animate_navigation_icon(
                    page_name,
                    18 if selected else 16,
                )

        self.dashboard_tab.grid_forget()
        self.analytics_tab.grid_forget()
        self.settings_tab.grid_forget()
        active_page = {
            "overview": self.dashboard_tab,
            "analytics": self.analytics_tab,
            "settings": self.settings_tab,
        }[page]
        active_page.grid(row=0, column=0, sticky="nsew")
        self._apply_theme_mode(self._appearance_mode)

    def _animate_navigation_icon(
        self,
        page: str,
        target_size: int,
        frame: int = 1,
        start_size: float | None = None,
    ) -> None:
        instance_attributes = vars(self)
        icon_labels = instance_attributes.get("_page_icon_labels", {})
        icon_jobs = instance_attributes.get("_page_icon_jobs", {})
        icon_sizes = instance_attributes.get("_page_icon_sizes", {})
        label = icon_labels.get(page)
        if label is None:
            return
        job = icon_jobs.get(page)
        if job is not None:
            self.after_cancel(job)
            icon_jobs[page] = None
        animation_start_size = (
            float(start_size)
            if start_size is not None
            else float(icon_sizes.get(page, target_size))
        )
        progress = min(1.0, frame / 5)
        eased_progress = progress * progress * (3 - 2 * progress)
        current_size = animation_start_size + (
            target_size - animation_start_size
        ) * eased_progress
        icon_sizes[page] = current_size
        label.configure(font=ctk.CTkFont(size=round(current_size), weight="bold"))
        if frame < 5:
            icon_jobs[page] = self.after(
                14,
                self._animate_navigation_icon,
                page,
                target_size,
                frame + 1,
                animation_start_size,
            )
        else:
            icon_jobs[page] = None

    def _on_navigation_hover(
        self,
        event: Any,
        page: str,
        hovered: bool,
    ) -> None:
        del event
        target_size = (
            19 if hovered else 18 if page == self._current_page else 16
        )
        self._animate_navigation_icon(page, target_size)

    def _save_appearance_mode(self, label: str) -> None:
        """Persist the selected appearance and apply it across the workspace."""
        mode = {"Light": "light", "Dark": "dark"}.get(label)
        if mode is None:
            self.appearance_menu.set(
                "Dark" if self._appearance_mode == "dark" else "Light"
            )
            self.status_label.configure(text="Choose Light or Dark appearance.")
            return

        try:
            self.database.set_settings({"appearance_mode": mode})
        except sqlite3.Error as error:
            self.appearance_menu.set(
                "Dark" if self._appearance_mode == "dark" else "Light"
            )
            self.status_label.configure(
                text=f"Could not save appearance preference: {error}"
            )
            return

        self._animate_theme_mode(mode)
        self.status_label.configure(text=f"{label} appearance enabled.")

    def _toggle_application_enabled(self) -> None:
        enabled = self.application_state_switch.get() in (True, 1, "on")
        try:
            self.database.set_settings(
                {"application_enabled": "true" if enabled else "false"}
            )
        except sqlite3.Error as error:
            if enabled:
                self.application_state_switch.deselect()
            else:
                self.application_state_switch.select()
            self.status_label.configure(
                text=f"Could not save application state: {error}"
            )
            return

        self._application_enabled = enabled
        if not enabled:
            self.timer_manager.reset_timer()
            self.window_tracker.set_manual_break(False)
            self.window_tracker.stop(timeout=1)
            self._work_started = False
            self._manual_break_active = False
            self._scheduled_break_id = None
            self._set_work_session_restrictions(False)
            self._close_break_overlay()

        self._set_application_controls_enabled(enabled)

    def _set_application_controls_enabled(self, enabled: bool) -> None:
        """Gate operational controls while leaving navigation and history available."""
        control_state = "normal" if enabled else "disabled"
        for control in self._application_controls:
            control.configure(state=control_state)

        if enabled:
            timer_state = self.timer_manager.state
            text, state = {
                "idle": ("Start Work", "normal"),
                "work": ("Pause Work", "normal"),
                "paused": ("Resume Work", "normal"),
                "break": ("Break in Progress", "disabled"),
                "manual_break": ("Break in Progress", "disabled"),
            }.get(timer_state, ("Start Work", "normal"))
            self.start_button.configure(text=text, state=state)
            self.status_label.configure(text="MindShield is active. Ready when you are.")
            self._set_focus_status(timer_state)
        else:
            self._set_focus_status("inactive")
            self.start_button.configure(
                text="Application Inactive",
                state="disabled",
            )
            self.status_label.configure(
                text="MindShield is inactive. Turn it on to resume focus features."
            )

        if self._appearance_mode == "dark":
            active_text, active_background = "#6ee7b7", "#163b30"
            inactive_text, inactive_background = "#8b9bb0", "#263449"
        else:
            active_text, active_background = "#16835d", "#e8f6ef"
            inactive_text, inactive_background = "#64748b", "#e7eef7"
        self.application_state_label.configure(
            text="MindShield active" if enabled else "MindShield inactive",
            text_color=active_text if enabled else inactive_text,
        )
        self.application_toggle_frame.configure(
            fg_color=active_background if enabled else inactive_background
        )

    def _apply_theme_mode(self, mode: str) -> None:
        """Recolor existing widgets without rebuilding or resetting page contents."""
        if mode not in ("light", "dark"):
            raise ValueError("Appearance mode must be 'light' or 'dark'")

        self._appearance_mode = mode
        ctk.set_appearance_mode(mode)
        light_to_dark = dict(self.THEME_COLOR_PAIRS)
        dark_to_light = {
            dark: light for light, dark in self.THEME_COLOR_PAIRS
        }
        color_map = dark_to_light if mode == "light" else light_to_dark
        background = "#0e1520" if mode == "dark" else "#f4f7fb"
        self._recolor_workspace(color_map, background)

    def _recolor_workspace(
        self,
        color_map: dict[str, str],
        background: str,
    ) -> None:
        self.configure(fg_color=background)
        widget_color_options = {
            ctk.CTkScrollableFrame: (
                "fg_color",
                "border_color",
                "scrollbar_button_color",
                "scrollbar_button_hover_color",
            ),
            ctk.CTkSegmentedButton: (
                "fg_color",
                "selected_color",
                "selected_hover_color",
                "unselected_color",
                "unselected_hover_color",
                "text_color",
                "text_color_disabled",
            ),
            ctk.CTkFrame: ("fg_color", "border_color"),
            ctk.CTkButton: (
                "fg_color",
                "hover_color",
                "text_color",
                "border_color",
            ),
            ctk.CTkLabel: ("fg_color", "text_color"),
            ctk.CTkEntry: (
                "fg_color",
                "border_color",
                "text_color",
                "placeholder_text_color",
            ),
            ctk.CTkComboBox: (
                "fg_color",
                "border_color",
                "text_color",
                "button_color",
                "button_hover_color",
            ),
            ctk.CTkOptionMenu: (
                "fg_color",
                "text_color",
                "button_color",
                "button_hover_color",
            ),
            ctk.CTkProgressBar: ("fg_color", "progress_color"),
            ctk.CTkSwitch: (
                "fg_color",
                "progress_color",
                "button_color",
                "button_hover_color",
                "text_color",
            ),
        }

        def recolor(widget: tk.Misc) -> None:
            for widget_type, options in widget_color_options.items():
                if isinstance(widget, widget_type):
                    updates: dict[str, str] = {}
                    for option in options:
                        value = widget.cget(option)
                        if isinstance(value, str) and value in color_map:
                            updates[option] = color_map[value]
                    if updates:
                        widget.configure(**updates)
                    break
            for child in widget.winfo_children():
                recolor(child)

        recolor(self)

    @staticmethod
    def _interpolate_color(start: str, end: str, progress: float) -> str:
        start_rgb = tuple(
            int(start[index : index + 2], 16)
            for index in (1, 3, 5)
        )
        end_rgb = tuple(
            int(end[index : index + 2], 16)
            for index in (1, 3, 5)
        )
        channels = tuple(
            round(start_value + (end_value - start_value) * progress)
            for start_value, end_value in zip(start_rgb, end_rgb)
        )
        return "#{:02x}{:02x}{:02x}".format(*channels)

    def _animate_theme_mode(self, mode: str, frame: int = 0) -> None:
        """Blend the full palette between modes over a short eased transition."""
        if mode not in ("light", "dark"):
            raise ValueError("Appearance mode must be 'light' or 'dark'")
        if frame == 0:
            if self._theme_transition_job is not None:
                self.after_cancel(self._theme_transition_job)
                self._theme_transition_job = None
            self._theme_transition_start_position = (
                self._theme_transition_position
            )
            self._theme_transition_target_position = (
                1.0 if mode == "dark" else 0.0
            )
            self._appearance_mode = mode
            ctk.set_appearance_mode(mode)

        def eased_position(frame_number: int) -> float:
            progress = min(
                1.0,
                frame_number / self.THEME_TRANSITION_FRAMES,
            )
            eased_progress = progress * progress * (3 - 2 * progress)
            return self._theme_transition_start_position + (
                self._theme_transition_target_position
                - self._theme_transition_start_position
            ) * eased_progress

        previous_position = eased_position(max(0, frame - 1))
        current_position = eased_position(frame)
        color_map: dict[str, str] = {}
        for light_color, dark_color in self.THEME_COLOR_PAIRS:
            color_map[
                self._interpolate_color(
                    light_color,
                    dark_color,
                    previous_position,
                )
            ] = self._interpolate_color(
                light_color,
                dark_color,
                current_position,
            )
        self._theme_transition_position = current_position
        self._recolor_workspace(
            color_map,
            self._interpolate_color(
                self.THEME_COLOR_PAIRS[0][0],
                self.THEME_COLOR_PAIRS[0][1],
                current_position,
            ),
        )
        if frame < self.THEME_TRANSITION_FRAMES:
            self._theme_transition_job = self.after(
                self.THEME_TRANSITION_INTERVAL_MS,
                self._animate_theme_mode,
                mode,
                frame + 1,
            )
        else:
            self._theme_transition_job = None
            self._set_focus_status(self.timer_manager.state)

    def _build_dashboard(self) -> None:
        tab = self.dashboard_tab
        tab.grid_columnconfigure(0, weight=1)

        focus_card = ctk.CTkFrame(
            tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3",
            corner_radius=18
        )
        focus_card.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 14))
        session_header = ctk.CTkFrame(focus_card, fg_color="transparent")
        session_header.pack(fill="x", padx=22, pady=(18, 10))
        ctk.CTkLabel(
            session_header,
            text="FOCUS SESSION",
            font=ctk.CTkFont(
                family=get_display_font_family(),
                size=11,
                weight="bold",
            ),
            text_color="#1788c4",
        ).pack(side="left")
        self.application_toggle_frame = ctk.CTkFrame(
            session_header,
            fg_color="#e8f6ef",
            corner_radius=18,
        )
        self.application_toggle_frame.pack(side="right")
        self.application_state_label = ctk.CTkLabel(
            self.application_toggle_frame,
            text="MindShield active",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#16835d",
        )
        self.application_state_label.pack(side="left", padx=(14, 8), pady=9)
        self.application_state_switch = AnimatedSwitch(
            self.application_toggle_frame,
            text="",
            width=52,
            height=28,
            progress_color="#22a06b",
            command=self._toggle_application_enabled,
        )
        if self._application_enabled:
            self.application_state_switch.select()
        self.application_state_switch.pack(side="left", padx=(0, 12), pady=6)
        initial_minutes, initial_seconds = divmod(
            math.ceil(self.timer_manager.work_interval_sec), 60
        )
        self.countdown_label = ctk.CTkLabel(
            focus_card,
            text=f"{initial_minutes:02d}:{initial_seconds:02d}",
            font=ctk.CTkFont(
                family=get_display_font_family(),
                size=52,
                weight="bold",
            ),
            text_color="#0f172a",
        )
        self.countdown_label.pack(pady=(0, 2))
        self.session_progress = AnimatedProgressBar(
            focus_card,
            height=9,
            progress_color="#1788c4",
            fg_color="#e7eef7",
            corner_radius=8,
        )
        self.session_progress.set(0)
        self.session_progress.pack(fill="x", padx=48, pady=(8, 12))
        self.work_zone_label = ctk.CTkLabel(
            focus_card,
            text=f"Active Work Zone: {self.active_work_zone}",
            font=ctk.CTkFont(size=15),
            text_color="#475569",
        )
        self.work_zone_label.pack(pady=(0, 20))

        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=24, pady=10)
        controls.grid_columnconfigure((0, 1), weight=1)
        self.start_button = AnimatedActionButton(
            controls,
            text="Start Work",
            height=48,
            corner_radius=16,
            fg_color="#1593d0",
            hover_color="#087cad",
            animation_color="#1593d0",
            animation_hover_color="#087cad",
            animation_pressed_color="#0369a1",
            command=self._toggle_work,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._application_controls.append(self.start_button)

        break_controls = ctk.CTkFrame(controls, fg_color="transparent")
        break_controls.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        break_controls.grid_columnconfigure(0, weight=1)
        self.break_length_menu = ctk.CTkOptionMenu(
            break_controls,
            values=["5", "10", "15"],
            width=90,
            fg_color="#e7eef7",
            button_color="#d8e3ef",
            text_color="#334155",
        )
        self.break_length_menu.set("5")
        self.break_length_menu.grid(row=0, column=0, sticky="e", padx=(0, 8))
        self._application_controls.append(self.break_length_menu)
        self.break_button = AnimatedActionButton(
            break_controls,
            text="Take a Break",
            height=48,
            corner_radius=16,
            fg_color="#e7eef7",
            hover_color="#d8e3ef",
            text_color="#334155",
            animation_color="#e7eef7",
            animation_hover_color="#d8e3ef",
            animation_pressed_color="#cbd8e6",
            command=self._start_selected_break,
        )
        self.break_button.grid(row=0, column=1, sticky="ew")
        self._application_controls.append(self.break_button)

        zone_card = ctk.CTkFrame(
            tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3",
            corner_radius=16
        )
        zone_card.grid(row=2, column=0, sticky="ew", padx=24, pady=14)
        ctk.CTkLabel(
            zone_card,
            text=(
                "Work zone — match a title, app, executable path, "
                "Explorer folder, or browser URL"
            ),
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#1e293b",
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
        self._application_controls.append(self.work_zone_selector)
        self.update_work_zone_button = AnimatedActionButton(
            zone_card,
            text="Update Work Zone",
            command=self._update_work_zone,
            fg_color="#e7eef7",
            hover_color="#d8e3ef",
            text_color="#334155",
            animation_color="#e7eef7",
            animation_hover_color="#d8e3ef",
            animation_pressed_color="#cbd8e6",
        )
        self.update_work_zone_button.grid(
            row=1, column=1, padx=(0, 18), pady=(0, 16)
        )
        self._application_controls.append(self.update_work_zone_button)
        zone_card.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            tab,
            text="Ready when you are.",
            text_color="#64748b",
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

        daily_goal_card = ctk.CTkFrame(tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        daily_goal_card.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=24, pady=(8, 16)
        )
        ctk.CTkLabel(
            daily_goal_card,
            text="Daily focus goal",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#1e293b",
        ).pack(anchor="w", padx=20, pady=(18, 8))
        self.daily_goal_progress = AnimatedProgressBar(
            daily_goal_card,
            height=14,
            progress_color="#22a06b",
            fg_color="#e7eef7",
            corner_radius=10,
        )
        self.daily_goal_progress.pack(fill="x", padx=20, pady=8)
        self.daily_goal_summary_label = ctk.CTkLabel(
            daily_goal_card,
            text=f"0 / {self._daily_goal_minutes} minutes",
            text_color="#64748b",
            font=ctk.CTkFont(size=13),
        )
        self.daily_goal_summary_label.pack(anchor="e", padx=20, pady=(4, 18))
        goal_controls = ctk.CTkFrame(daily_goal_card, fg_color="transparent")
        goal_controls.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkLabel(
            goal_controls,
            text="Daily goal (minutes)",
            text_color="#475569",
        ).pack(side="left")
        self.analytics_daily_goal_entry = ctk.CTkEntry(
            goal_controls,
            width=90,
            justify="center",
        )
        self.analytics_daily_goal_entry.insert(0, str(self._daily_goal_minutes))
        self.analytics_daily_goal_entry.pack(side="left", padx=(10, 8))
        ctk.CTkButton(
            goal_controls,
            text="Save",
            width=72,
            command=self._save_analytics_daily_goal,
        ).pack(side="left")
        ctk.CTkButton(
            goal_controls,
            text="Reset goal",
            width=100,
            fg_color="#e7eef7",
            hover_color="#d8e3ef",
            text_color="#334155",
            command=self._reset_analytics_daily_goal,
        ).pack(side="right")

        self.monthly_focus_label = self._analytics_card(
            tab, "THIS MONTH'S FOCUS", "0 min", 2, 0
        )
        self.break_compliance_label = self._analytics_card(
            tab, "SCHEDULED BREAKS THIS MONTH", "No breaks yet", 2, 1
        )

        weekly_card = ctk.CTkFrame(tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        weekly_card.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=24, pady=(8, 16)
        )
        ctk.CTkLabel(
            weekly_card,
            text="Weekly focus summary",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#1e293b",
        ).pack(anchor="w", padx=20, pady=(18, 8))
        self.weekly_progress = AnimatedProgressBar(
            weekly_card,
            height=14,
            progress_color="#1788c4",
            fg_color="#e7eef7",
            corner_radius=10,
        )
        self.weekly_progress.pack(fill="x", padx=20, pady=8)
        self.weekly_summary_label = ctk.CTkLabel(
            weekly_card,
            text="0 / 840 minutes",
            text_color="#64748b",
            font=ctk.CTkFont(size=13),
        )
        self.weekly_summary_label.pack(anchor="e", padx=20, pady=(4, 18))

        history_card = ctk.CTkFrame(tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        history_card.grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=24,
            pady=(8, 20),
        )
        ctk.CTkLabel(
            history_card,
            text="Focus session history",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#1e293b",
        ).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            history_card,
            text="Add, edit, or delete sessions. All totals update from this history.",
            text_color="#64748b",
        ).pack(anchor="w", padx=20, pady=(0, 12))

        add_session = ctk.CTkFrame(history_card, fg_color="#f1f5f9", corner_radius=12)
        add_session.pack(fill="x", padx=16, pady=(0, 12))
        self._add_session_date = self._history_entry(add_session, "YYYY-MM-DD", 0, 0)
        self._add_session_date.insert(0, date.today().isoformat())
        self._add_session_time = self._history_entry(add_session, "HH:MM", 0, 1)
        self._add_session_time.insert(0, "09:00")
        self._add_session_duration = self._history_entry(add_session, "Minutes", 0, 2)
        self._add_session_duration.insert(0, "25")
        self._add_session_zone = self._history_entry(
            add_session,
            "Work zone",
            0,
            3,
            width=180,
        )
        self._add_session_zone.insert(0, self.active_work_zone)
        ctk.CTkButton(
            add_session,
            text="Add session",
            width=105,
            command=self._add_analytics_session,
        ).grid(row=0, column=4, padx=(4, 10), pady=10)
        for column in range(4):
            add_session.grid_columnconfigure(column, weight=1)

        history_heading = ctk.CTkFrame(history_card, fg_color="transparent")
        history_heading.pack(fill="x", padx=16, pady=(0, 4))
        for column, label, weight in (
            (0, "Date", 2),
            (1, "Start", 1),
            (2, "Minutes", 1),
            (3, "Work zone", 3),
        ):
            ctk.CTkLabel(
                history_heading,
                text=label,
                text_color="#64748b",
                anchor="w",
            ).grid(row=0, column=column, sticky="ew", padx=4)
            history_heading.grid_columnconfigure(column, weight=weight)

        self.analytics_history = ctk.CTkScrollableFrame(
            history_card,
            height=250,
            fg_color="transparent",
        )
        self.analytics_history.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(
            history_card,
            text="Scheduled-break history",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#1e293b",
        ).pack(anchor="w", padx=20, pady=(8, 8))
        break_heading = ctk.CTkFrame(history_card, fg_color="transparent")
        break_heading.pack(fill="x", padx=16, pady=(0, 4))
        for column, label, weight in (
            (0, "Date", 2),
            (1, "Start", 1),
            (2, "Seconds", 1),
            (3, "Work zone", 3),
            (4, "Status", 1),
        ):
            ctk.CTkLabel(
                break_heading,
                text=label,
                text_color="#64748b",
                anchor="w",
            ).grid(row=0, column=column, sticky="ew", padx=4)
            break_heading.grid_columnconfigure(column, weight=weight)
        add_break = ctk.CTkFrame(history_card, fg_color="#f1f5f9", corner_radius=12)
        add_break.pack(fill="x", padx=16, pady=(0, 12))
        self._add_break_date = self._history_entry(
            add_break,
            "YYYY-MM-DD",
            0,
            0,
        )
        self._add_break_date.insert(0, date.today().isoformat())
        self._add_break_time = self._history_entry(add_break, "HH:MM", 0, 1)
        self._add_break_time.insert(0, "09:00")
        self._add_break_duration = self._history_entry(
            add_break,
            "Seconds",
            0,
            2,
        )
        self._add_break_duration.insert(0, "20")
        self._add_break_zone = self._history_entry(
            add_break,
            "Work zone",
            0,
            3,
            width=180,
        )
        self._add_break_zone.insert(0, self.active_work_zone)
        self._add_break_status = ctk.CTkOptionMenu(
            add_break,
            values=["Incomplete", "Completed"],
            width=112,
        )
        self._add_break_status.set("Completed")
        self._add_break_status.grid(row=0, column=4, padx=4, pady=8)
        ctk.CTkButton(
            add_break,
            text="Add break",
            width=90,
            command=self._add_analytics_break,
        ).grid(row=0, column=5, padx=(4, 10), pady=8)
        for column in range(5):
            add_break.grid_columnconfigure(column, weight=1)
        self.analytics_break_history = ctk.CTkScrollableFrame(
            history_card,
            height=180,
            fg_color="transparent",
        )
        self.analytics_break_history.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(
            history_card,
            text="Reset all analytics",
            fg_color="#dc4c4c",
            hover_color="#bd3434",
            command=self._reset_analytics,
        ).pack(anchor="e", padx=16, pady=(0, 16))

    @staticmethod
    def _analytics_card(
        parent: ctk.CTkFrame | ctk.CTkScrollableFrame,
        title: str,
        value: str,
        row: int,
        column: int,
    ) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        card.grid(row=row, column=column, sticky="ew", padx=24, pady=24)
        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#64748b",
        ).pack(anchor="w", padx=18, pady=(18, 4))
        value_label = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(
                family=get_display_font_family(),
                size=26,
                weight="bold",
            ),
            text_color="#0f172a",
        )
        value_label.pack(anchor="w", padx=18, pady=(0, 18))
        return value_label

    @staticmethod
    def _history_entry(
        parent: ctk.CTkFrame,
        placeholder: str,
        row: int,
        column: int,
        width: int = 115,
    ) -> ctk.CTkEntry:
        entry = ctk.CTkEntry(
            parent,
            width=width,
            placeholder_text=placeholder,
        )
        entry.grid(row=row, column=column, sticky="ew", padx=4, pady=10)
        return entry

    def _build_settings(self) -> None:
        tab = ctk.CTkScrollableFrame(
            self.settings_tab,
            fg_color="transparent",
        )
        tab.grid(row=0, column=0, sticky="nsew")
        self.settings_tab.grid_columnconfigure(0, weight=1)
        self.settings_tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        interval_card = ctk.CTkFrame(tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        interval_card.grid(row=0, column=0, sticky="ew", padx=24, pady=24)
        ctk.CTkLabel(
            interval_card,
            text="Timer intervals",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#1e293b",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 14))

        ctk.CTkLabel(
            interval_card,
            text="Work Interval (minutes)",
            text_color="#475569",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=8)
        self.work_interval_entry = ctk.CTkEntry(interval_card, width=140)
        self.work_interval_entry.insert(0, str(self._work_interval_minutes))
        self.work_interval_entry.grid(row=1, column=1, sticky="e", padx=18, pady=8)
        self._application_controls.append(self.work_interval_entry)

        ctk.CTkLabel(
            interval_card,
            text="Break Duration (seconds)",
            text_color="#475569",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=8)
        self.break_duration_entry = ctk.CTkEntry(interval_card, width=140)
        self.break_duration_entry.insert(0, str(self._break_duration_seconds))
        self.break_duration_entry.grid(row=2, column=1, sticky="e", padx=18, pady=8)
        self._application_controls.append(self.break_duration_entry)

        ctk.CTkLabel(
            interval_card,
            text="Daily focus goal (minutes)",
            text_color="#475569",
        ).grid(row=3, column=0, sticky="w", padx=18, pady=8)
        self.daily_goal_entry = ctk.CTkEntry(interval_card, width=140)
        self.daily_goal_entry.insert(0, str(self._daily_goal_minutes))
        self.daily_goal_entry.grid(row=3, column=1, sticky="e", padx=18, pady=8)

        self.apply_timer_settings_button = ctk.CTkButton(
            interval_card,
            text="Apply Settings",
            command=self._apply_timer_settings,
            fg_color="#e7eef7",
            hover_color="#d8e3ef",
            text_color="#334155",
        )
        self.apply_timer_settings_button.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=(12, 6)
        )
        self._application_controls.append(self.apply_timer_settings_button)
        ctk.CTkButton(
            interval_card,
            text="Save Daily Goal",
            command=self._apply_daily_goal,
            fg_color="#e7eef7",
            hover_color="#d8e3ef",
            text_color="#334155",
        ).grid(row=5, column=0, columnspan=2, sticky="ew", padx=18, pady=(6, 18))
        interval_card.grid_columnconfigure(0, weight=1)

        demo_card = ctk.CTkFrame(tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        demo_card.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.demo_switch = AnimatedSwitch(
            demo_card,
            text="Demo Mode (10s/5s)",
            command=self._toggle_demo_mode,
            progress_color="#1593d0",
        )
        if self._demo_mode_enabled_at_startup:
            self.demo_switch.select()
        self.demo_switch.pack(anchor="w", padx=18, pady=18)
        self._application_controls.append(self.demo_switch)

        meeting_card = ctk.CTkFrame(tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        meeting_card.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        ctk.CTkLabel(
            meeting_card,
            text="Pause / meeting mode duration",
            text_color="#475569",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=12)
        self.meeting_duration_menu = ctk.CTkOptionMenu(
            meeting_card,
            values=["30 minutes", "1 hour", "2 hours"],
            command=self._save_meeting_mode_duration,
            fg_color="#e7eef7",
            button_color="#d8e3ef",
            text_color="#334155",
        )
        self.meeting_duration_menu.set(self._meeting_duration_label())
        self.meeting_duration_menu.grid(row=0, column=1, sticky="e", padx=18, pady=12)
        self._application_controls.append(self.meeting_duration_menu)
        self.start_meeting_button = AnimatedActionButton(
            meeting_card,
            text="Start Meeting Mode",
            height=42,
            command=self._start_meeting_break,
            fg_color="#e7eef7",
            hover_color="#d8e3ef",
            text_color="#334155",
            animation_color="#e7eef7",
            animation_hover_color="#d8e3ef",
            animation_pressed_color="#cbd8e6",
        )
        self.start_meeting_button.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 16)
        )
        self._application_controls.append(self.start_meeting_button)
        meeting_card.grid_columnconfigure(0, weight=1)

        self.suppression_switch = AnimatedSwitch(
            tab,
            text="Automatically minimize windows outside my work zone",
            command=self._toggle_distraction_suppression,
            progress_color="#1593d0",
        )
        if self._suppress_distractions_enabled:
            self.suppression_switch.select()
        self.suppression_switch.grid(row=3, column=0, sticky="w", padx=24, pady=12)
        self._application_controls.append(self.suppression_switch)

        overlay_card = ctk.CTkFrame(tab, fg_color="#ffffff", border_width=1, border_color="#e4ebf3", corner_radius=16)
        overlay_card.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 16))
        ctk.CTkLabel(
            overlay_card,
            text="Break reminder style",
            text_color="#475569",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))
        self.overlay_mode_menu = ctk.CTkOptionMenu(
            overlay_card,
            values=["Full-screen overlay", "Corner toast"],
            command=self._save_break_overlay_mode,
            fg_color="#e7eef7",
            button_color="#d8e3ef",
            text_color="#334155",
        )
        self.overlay_mode_menu.set(
            "Corner toast" if self._break_overlay_mode == "toast" else "Full-screen overlay"
        )
        self.overlay_mode_menu.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 8))
        self._application_controls.append(self.overlay_mode_menu)
        ctk.CTkLabel(
            overlay_card,
            text="Choose Corner toast during presentations or video playback.",
            text_color="#64748b",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 14))
        overlay_card.grid_columnconfigure(0, weight=1)

    def _start_work(self) -> None:
        if not self._application_enabled:
            self.status_label.configure(text="Turn on MindShield to start a work session.")
            return
        if self._work_started:
            self.status_label.configure(text="A work session is already running.")
            return

        tracker_started = False
        try:
            self.window_tracker.start()
            tracker_started = True
            self.timer_manager.start_work()
        except RuntimeError as error:
            if tracker_started:
                self.window_tracker.stop(timeout=1)
            self.status_label.configure(text=str(error))
            return

        self._work_started = True
        if not self._set_work_session_restrictions(True):
            self.timer_manager.pause_work()
            self.window_tracker.set_manual_break(True)
            return
        self.start_button.configure(state="normal", text="Pause Work")
        self.status_label.configure(text=f"Focusing in {self.active_work_zone}.")

    def _toggle_work(self) -> None:
        if not self._application_enabled:
            self.status_label.configure(text="Turn on MindShield to control the focus timer.")
            return
        if self.timer_manager.state == "work":
            self.timer_manager.pause_work()
            self.window_tracker.set_manual_break(True)
            self._set_work_session_restrictions(False)
            return

        if self.timer_manager.state == "paused":
            try:
                self.timer_manager.start_work()
            except RuntimeError as error:
                self.status_label.configure(text=str(error))
                return
            if not self._set_work_session_restrictions(True):
                self.timer_manager.pause_work()
                self.window_tracker.set_manual_break(True)
                return
            self.window_tracker.set_manual_break(False)
            return

        self._start_work()

    def _start_selected_break(self) -> None:
        if not self._application_enabled:
            self.status_label.configure(text="Turn on MindShield to start a break timer.")
            return
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

    def _start_one_hour_meeting_break(self) -> None:
        try:
            self._start_manual_break(60)
        except RuntimeError as error:
            self.status_label.configure(text=str(error))

    def _start_manual_break(self, minutes: int) -> None:
        if not self._application_enabled:
            raise RuntimeError("Turn on MindShield before starting a timed break.")
        was_tracking = self._work_started
        previous_manual_state = self.window_tracker.manual_break
        was_manual_break = self._manual_break_active
        self._manual_break_active = True
        self.window_tracker.set_manual_break(True)
        try:
            if not was_tracking:
                self.window_tracker.start()
            self.timer_manager.start_manual_break(minutes)
        except RuntimeError:
            self._manual_break_active = was_manual_break
            self.window_tracker.set_manual_break(previous_manual_state)
            if not was_tracking:
                self.window_tracker.stop(timeout=1)
            raise
        self._work_started = True
        self._set_work_session_restrictions(False)
        self.status_label.configure(text=f"Break started for {minutes} minutes.")

    def _set_work_session_restrictions(self, active: bool) -> bool:
        """Keep the app above other windows and lock navigation only during work."""
        try:
            self.attributes("-topmost", active)
        except tk.TclError as error:
            self.status_label.configure(
                text=f"Could not update focus-window behavior: {error}"
            )
            return False

        for page, button in self._page_buttons.items():
            button.configure(
                state=(
                    "disabled"
                    if active and page != self._current_page
                    else "normal"
                )
            )
        return True

    def _update_work_zone(self) -> None:
        if not self._application_enabled:
            self.status_label.configure(text="Turn on MindShield to update the work zone.")
            return
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
        if not self._application_enabled:
            if self._suppress_distractions_enabled:
                self.suppression_switch.select()
            else:
                self.suppression_switch.deselect()
            self.status_label.configure(text="Turn on MindShield to change tracking settings.")
            return
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
        if not self._application_enabled:
            self.status_label.configure(text="Turn on MindShield to change focus timing.")
            return
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

        self._save_daily_goal(daily_goal_minutes)

    def _save_analytics_daily_goal(self) -> None:
        try:
            daily_goal_minutes = int(self.analytics_daily_goal_entry.get())
            if daily_goal_minutes <= 0:
                raise ValueError
        except ValueError:
            self.status_label.configure(
                text="Enter a positive whole number for the daily focus goal."
            )
            return

        self._save_daily_goal(daily_goal_minutes)

    def _save_daily_goal(self, daily_goal_minutes: int) -> None:
        try:
            self.database.set_settings(
                {"daily_goal_minutes": str(daily_goal_minutes)}
            )
        except sqlite3.Error as error:
            self.status_label.configure(text=f"Could not save daily goal: {error}")
            return

        self._daily_goal_minutes = daily_goal_minutes
        self.analytics_daily_goal_entry.delete(0, "end")
        self.analytics_daily_goal_entry.insert(0, str(daily_goal_minutes))
        self.daily_goal_entry.delete(0, "end")
        self.daily_goal_entry.insert(0, str(daily_goal_minutes))
        self._refresh_analytics()
        self.status_label.configure(text="Daily focus goal saved.")

    def _reset_analytics_daily_goal(self) -> None:
        default_goal = 120
        try:
            self.database.set_settings({"daily_goal_minutes": str(default_goal)})
        except sqlite3.Error as error:
            self.status_label.configure(text=f"Could not reset daily goal: {error}")
            return

        self._daily_goal_minutes = default_goal
        for entry in (self.analytics_daily_goal_entry, self.daily_goal_entry):
            entry.delete(0, "end")
            entry.insert(0, str(default_goal))
        self._refresh_analytics()
        self.status_label.configure(text="Daily focus goal reset to 120 minutes.")

    def _add_analytics_session(self) -> None:
        try:
            duration = int(self._add_session_duration.get())
            self.database.add_work_log(
                self._add_session_date.get(),
                self._add_session_time.get(),
                duration,
                self._add_session_zone.get(),
            )
        except (ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not add focus session: {error}")
            return

        self._refresh_analytics()
        self.status_label.configure(text="Focus session added.")

    def _save_analytics_session(
        self,
        log_id: int,
        date_entry: ctk.CTkEntry,
        time_entry: ctk.CTkEntry,
        duration_entry: ctk.CTkEntry,
        zone_entry: ctk.CTkEntry,
    ) -> None:
        try:
            duration = int(duration_entry.get())
            self.database.update_work_log(
                log_id,
                date_entry.get(),
                time_entry.get(),
                duration,
                zone_entry.get(),
            )
        except (ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not update focus session: {error}")
            return

        self._refresh_analytics()
        self.status_label.configure(text="Focus session updated.")

    def _delete_analytics_session(self, log_id: int) -> None:
        if not messagebox.askyesno(
            "Delete focus session",
            "Delete this focus session? Analytics will be recalculated.",
            parent=self,
        ):
            return
        try:
            self.database.delete_work_log(log_id)
        except (ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not delete focus session: {error}")
            return

        self._refresh_analytics()
        self.status_label.configure(text="Focus session deleted.")

    def _save_analytics_break(
        self,
        break_id: int,
        date_entry: ctk.CTkEntry,
        time_entry: ctk.CTkEntry,
        duration_entry: ctk.CTkEntry,
        zone_entry: ctk.CTkEntry,
        status_menu: ctk.CTkOptionMenu,
    ) -> None:
        try:
            self.database.update_break_log(
                break_id,
                date_entry.get(),
                time_entry.get(),
                int(duration_entry.get()),
                zone_entry.get(),
                status_menu.get() == "Completed",
            )
        except (ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not update scheduled break: {error}")
            return

        self._refresh_analytics()
        self.status_label.configure(text="Scheduled break updated.")

    def _add_analytics_break(self) -> None:
        try:
            self.database.add_break_log(
                self._add_break_date.get(),
                self._add_break_time.get(),
                int(self._add_break_duration.get()),
                self._add_break_zone.get(),
                self._add_break_status.get() == "Completed",
            )
        except (ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not add scheduled break: {error}")
            return

        self._refresh_analytics()
        self.status_label.configure(text="Scheduled break added.")

    def _delete_analytics_break(self, break_id: int) -> None:
        if not messagebox.askyesno(
            "Delete scheduled break",
            "Delete this scheduled-break record?",
            parent=self,
        ):
            return
        try:
            self.database.delete_break_log(break_id)
        except (ValueError, sqlite3.Error) as error:
            self.status_label.configure(text=f"Could not delete scheduled break: {error}")
            return

        self._refresh_analytics()
        self.status_label.configure(text="Scheduled break deleted.")

    def _reset_analytics(self) -> None:
        if not messagebox.askyesno(
            "Reset analytics",
            "Permanently clear all focus-session and break history? "
            "This cannot be undone.",
            parent=self,
        ):
            return
        try:
            self.database.reset_analytics()
        except sqlite3.Error as error:
            self.status_label.configure(text=f"Could not reset analytics: {error}")
            return

        self._refresh_analytics()
        self.status_label.configure(text="Focus and break analytics have been reset.")

    def _meeting_duration_label(self) -> str:
        return {
            30: "30 minutes",
            60: "1 hour",
            120: "2 hours",
        }[self._meeting_mode_minutes]

    def _save_meeting_mode_duration(self, label: str) -> None:
        if not self._application_enabled:
            self.meeting_duration_menu.set(self._meeting_duration_label())
            self.status_label.configure(text="Turn on MindShield to change break settings.")
            return
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
        if not self._application_enabled:
            self.overlay_mode_menu.set(
                "Corner toast"
                if self._break_overlay_mode == "toast"
                else "Full-screen overlay"
            )
            self.status_label.configure(text="Turn on MindShield to change break settings.")
            return
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
        if not self._application_enabled:
            if self._demo_mode_enabled_at_startup:
                self.demo_switch.select()
            else:
                self.demo_switch.deselect()
            self.status_label.configure(text="Turn on MindShield to change timer settings.")
            return
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
        if self._closing or not self._application_enabled:
            return
        self.after(0, self._update_countdown, remaining_seconds)

    def _on_timer_state_update(self, state: str, _remaining_seconds: int) -> None:
        if self._closing or not self._application_enabled:
            return
        self.after(0, self._display_timer_state, state, _remaining_seconds)

    def _display_timer_state(self, state: str, remaining_seconds: int) -> None:
        if not self._application_enabled:
            return
        if state == self._last_displayed_timer_state:
            return
        self._last_displayed_timer_state = state
        if not self._set_work_session_restrictions(state == "work") and state == "work":
            self.timer_manager.pause_work()
            self.window_tracker.set_manual_break(True)
            return
        self._set_focus_status(state)
        messages = {
            "idle": "Ready when you are.",
            "work": f"Focusing in {self.active_work_zone}.",
            "paused": "Work paused.",
            "break": "Break time. Rest your eyes.",
            "manual_break": "Manual break in progress.",
        }
        self.status_label.configure(text=messages.get(state, f"Timer state: {state}."))
        button_state = {
            "idle": ("Start Work", "normal"),
            "work": ("Pause Work", "normal"),
            "paused": ("Resume Work", "normal"),
            "break": ("Break in Progress", "disabled"),
            "manual_break": ("Break in Progress", "disabled"),
        }.get(state, ("Start Work", "normal"))
        self.start_button.configure(text=button_state[0], state=button_state[1])
        self._update_countdown(remaining_seconds)

    def _update_countdown(self, remaining_seconds: int) -> None:
        self._set_countdown_display(remaining_seconds)

    def _set_countdown_display(self, remaining_seconds: int | float) -> None:
        total_seconds = max(0, math.ceil(remaining_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        self.countdown_label.configure(text=f"{minutes:02d}:{seconds:02d}")
        state = self.timer_manager.state
        if state in ("work", "paused"):
            elapsed_fraction = 1 - (
                max(0, remaining_seconds) / self.timer_manager.work_interval_sec
            )
            self.session_progress.animate_to(
                min(1.0, max(0.0, elapsed_fraction))
            )
        elif state in ("idle", "break", "manual_break", "inactive"):
            self.session_progress.animate_to(0)

    def _set_focus_status(self, state: str) -> None:
        """Show and gently animate the current focus state in the page header."""
        if self._focus_pulse_job is not None:
            self.after_cancel(self._focus_pulse_job)
            self._focus_pulse_job = None
        self._focus_pulse_on = False

        status = {
            "idle": ("READY", "#16835d", "#e8f6ef"),
            "work": ("FOCUSING", "#16835d", "#e8f6ef"),
            "paused": ("PAUSED", "#64748b", "#e7eef7"),
            "break": ("ON A BREAK", "#087cad", "#e8f4fb"),
            "manual_break": ("ON A BREAK", "#087cad", "#e8f4fb"),
            "inactive": ("INACTIVE", "#64748b", "#e7eef7"),
        }.get(state, ("READY", "#16835d", "#e8f6ef"))
        text, light_text, light_background = status
        if self._appearance_mode == "dark":
            color_map = dict(self.THEME_COLOR_PAIRS)
            text_color = color_map.get(light_text, light_text)
            background_color = color_map.get(light_background, light_background)
        else:
            text_color = light_text
            background_color = light_background
        self.header_status_badge.configure(
            text=text,
            text_color=text_color,
            fg_color=background_color,
        )
        if state == "work":
            self.tracker_activity_label.pack(
                side="left",
                padx=(0, 10),
            )
            self._animate_tracker_activity()
        else:
            self._stop_tracker_activity()
        if state == "work":
            self._animate_focus_badge()

    def _animate_tracker_activity(self, frame: int = 0) -> None:
        if self._closing or self.timer_manager.state != "work":
            self._stop_tracker_activity()
            return
        frames = ("◴", "◷", "◶", "◵")
        activity_label = vars(self).get("tracker_activity_label")
        if activity_label is None:
            return
        activity_label.configure(
            text=f"LIVE {frames[frame % len(frames)]}"
        )
        self._tracker_spinner_frame = (frame + 1) % len(frames)
        self._tracker_spinner_job = self.after(
            120,
            self._animate_tracker_activity,
            self._tracker_spinner_frame,
        )

    def _stop_tracker_activity(self) -> None:
        if self._tracker_spinner_job is not None:
            self.after_cancel(self._tracker_spinner_job)
            self._tracker_spinner_job = None
        self._tracker_spinner_frame = 0
        activity_label = vars(self).get("tracker_activity_label")
        if activity_label is not None:
            activity_label.pack_forget()

    def _animate_focus_badge(self) -> None:
        if self._closing or self.timer_manager.state != "work":
            self._focus_pulse_job = None
            return

        self._focus_pulse_on = not self._focus_pulse_on
        if self._appearance_mode == "dark":
            background = "#1d4b3a" if self._focus_pulse_on else "#163b30"
            text_color = "#a7f3d0" if self._focus_pulse_on else "#6ee7b7"
        else:
            background = "#d3f1e2" if self._focus_pulse_on else "#e8f6ef"
            text_color = "#116149" if self._focus_pulse_on else "#16835d"
        self.header_status_badge.configure(
            fg_color=background,
            text_color=text_color,
        )
        self._focus_pulse_job = self.after(720, self._animate_focus_badge)

    def _start_window_intro(self) -> None:
        """Fade the application window in gently when it first opens."""
        try:
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            return
        self._window_intro_job = self.after(20, self._animate_window_intro, 1)

    def _animate_window_intro(self, frame: int) -> None:
        frame_count = 10
        progress = min(1.0, frame / frame_count)
        eased_progress = 1 - (1 - progress) ** 3
        try:
            self.attributes("-alpha", eased_progress)
        except tk.TclError:
            self._window_intro_job = None
            return

        if frame < frame_count:
            self._window_intro_job = self.after(
                20,
                self._animate_window_intro,
                frame + 1,
            )
        else:
            self._window_intro_job = None

    def _on_break_start(self) -> None:
        if self._closing or not self._application_enabled:
            return
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
                self._set_status_if_application_active,
                "Break time. Rest your eyes.",
            )

    def _show_break_overlay(self) -> None:
        if self._closing or not self._application_enabled:
            return
        duration = max(1, math.ceil(self.timer_manager.break_duration_sec))
        self._overlay = OverlayWindow(
            self,
            duration,
            mode=self._break_overlay_mode,
        )

    def _on_break_end(self) -> None:
        if self._closing or not self._application_enabled:
            return
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
                self._set_status_if_application_active,
                "Work session resumed.",
            )

    def _close_break_overlay(self) -> None:
        if self._overlay is not None:
            if self._overlay.winfo_exists():
                self._overlay.destroy()
            self._overlay = None

    def _on_distraction_detected(self, window_title: str) -> None:
        if not self._closing and self._application_enabled:
            prefix = (
                "Distraction minimized"
                if self._suppress_distractions_enabled
                else "Distraction detected"
            )
            self.after(
                0,
                self._set_status_if_application_active,
                f"{prefix}: {window_title}",
            )

    def _set_status_if_application_active(self, message: str) -> None:
        if not self._closing and self._application_enabled:
            self.status_label.configure(text=message)

    def _report_background_error(self, message: str) -> None:
        if not self._closing:
            self.after(0, lambda: self.status_label.configure(text=message))

    def _refresh_analytics(self) -> None:
        logs = self.database.get_weekly_logs()
        work_logs = self.database.get_work_logs()
        break_logs = self.database.get_break_logs()
        today_minutes = self.database.get_todays_total_minutes()
        weekly_minutes = sum(total_minutes for _, total_minutes in logs)
        streak = self.database.get_current_streak_days()
        monthly_minutes = self.database.get_monthly_total_minutes()
        completed_breaks, total_breaks = self.database.get_monthly_break_compliance()
        weekly_goal_minutes = self._daily_goal_minutes * self.WEEKLY_GOAL_DAYS

        self.today_minutes_label.configure(text=f"{today_minutes} min")
        self.streak_label.configure(text=f"{streak} day{'s' if streak != 1 else ''}")
        self.daily_goal_progress.animate_to(
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
        self.weekly_progress.animate_to(
            min(1.0, weekly_minutes / weekly_goal_minutes)
        )
        self.weekly_summary_label.configure(
            text=f"{weekly_minutes} / {weekly_goal_minutes} minutes"
        )
        for child in self.analytics_history.winfo_children():
            child.destroy()

        if not work_logs:
            ctk.CTkLabel(
                self.analytics_history,
                text="No focus sessions recorded yet.",
                text_color="#64748b",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=12)
        else:
            for row_index, (
                log_id,
                log_date,
                start_time,
                duration,
                work_zone,
            ) in enumerate(work_logs):
                row = ctk.CTkFrame(
                    self.analytics_history,
                    fg_color="#ffffff",
                    border_width=1,
                    border_color="#e4ebf3",
                    corner_radius=10,
                )
                row.grid(row=row_index, column=0, sticky="ew", padx=4, pady=3)
                row.grid_columnconfigure(0, weight=2)
                row.grid_columnconfigure(1, weight=1)
                row.grid_columnconfigure(2, weight=1)
                row.grid_columnconfigure(3, weight=3)

                date_entry = self._history_entry(row, "YYYY-MM-DD", 0, 0)
                date_entry.insert(0, log_date)
                time_entry = self._history_entry(row, "HH:MM", 0, 1)
                time_entry.insert(0, start_time[:5])
                duration_entry = self._history_entry(row, "Minutes", 0, 2)
                duration_entry.insert(0, str(duration))
                zone_entry = self._history_entry(row, "Work zone", 0, 3, width=180)
                zone_entry.insert(0, work_zone)

                ctk.CTkButton(
                    row,
                    text="Save",
                    width=68,
                    command=lambda current_id=log_id,
                    current_date=date_entry,
                    current_time=time_entry,
                    current_duration=duration_entry,
                    current_zone=zone_entry: self._save_analytics_session(
                        current_id,
                        current_date,
                        current_time,
                        current_duration,
                        current_zone,
                    ),
                ).grid(row=0, column=4, padx=4, pady=8)
                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=68,
                    fg_color="#dc4c4c",
                    hover_color="#bd3434",
                    command=lambda current_id=log_id: self._delete_analytics_session(
                        current_id
                    ),
                ).grid(row=0, column=5, padx=(0, 8), pady=8)

        for child in self.analytics_break_history.winfo_children():
            child.destroy()
        if not break_logs:
            ctk.CTkLabel(
                self.analytics_break_history,
                text="No scheduled breaks recorded yet.",
                text_color="#64748b",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=12)
        else:
            for row_index, (
                break_id,
                break_date,
                started_at,
                duration_seconds,
                work_zone,
                completed,
            ) in enumerate(break_logs):
                row = ctk.CTkFrame(
                    self.analytics_break_history,
                    fg_color="#ffffff",
                    border_width=1,
                    border_color="#e4ebf3",
                    corner_radius=10,
                )
                row.grid(row=row_index, column=0, sticky="ew", padx=4, pady=3)
                for column, weight in enumerate((2, 1, 1, 3, 1)):
                    row.grid_columnconfigure(column, weight=weight)

                date_entry = self._history_entry(row, "YYYY-MM-DD", 0, 0)
                date_entry.insert(0, break_date)
                time_entry = self._history_entry(row, "HH:MM", 0, 1)
                time_entry.insert(0, started_at[11:16])
                duration_entry = self._history_entry(row, "Seconds", 0, 2)
                duration_entry.insert(0, str(duration_seconds))
                zone_entry = self._history_entry(row, "Work zone", 0, 3, width=180)
                zone_entry.insert(0, work_zone)
                status_menu = ctk.CTkOptionMenu(
                    row,
                    values=["Completed", "Incomplete"],
                    width=112,
                )
                status_menu.set("Completed" if completed else "Incomplete")
                status_menu.grid(row=0, column=4, padx=4, pady=8)

                ctk.CTkButton(
                    row,
                    text="Save",
                    width=68,
                    command=lambda current_id=break_id,
                    current_date=date_entry,
                    current_time=time_entry,
                    current_duration=duration_entry,
                    current_zone=zone_entry,
                    current_status=status_menu: self._save_analytics_break(
                        current_id,
                        current_date,
                        current_time,
                        current_duration,
                        current_zone,
                        current_status,
                    ),
                ).grid(row=0, column=5, padx=4, pady=8)
                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=68,
                    fg_color="#dc4c4c",
                    hover_color="#bd3434",
                    command=lambda current_id=break_id: self._delete_analytics_break(
                        current_id
                    ),
                ).grid(row=0, column=6, padx=(0, 8), pady=8)

        self._apply_theme_mode(self._appearance_mode)

    def destroy(self) -> None:
        """Stop background workers and owned windows before closing the app."""
        if not self._closing:
            self._closing = True
            if self._focus_pulse_job is not None:
                self.after_cancel(self._focus_pulse_job)
                self._focus_pulse_job = None
            if self._window_intro_job is not None:
                self.after_cancel(self._window_intro_job)
                self._window_intro_job = None
            if self._tracker_spinner_job is not None:
                self.after_cancel(self._tracker_spinner_job)
                self._tracker_spinner_job = None
            for job in self._page_icon_jobs.values():
                if job is not None:
                    self.after_cancel(job)
            self._page_icon_jobs.clear()
            if self._theme_transition_job is not None:
                self.after_cancel(self._theme_transition_job)
                self._theme_transition_job = None
            self.timer_manager.stop(timeout=1)
            self.window_tracker.stop(timeout=1)
            if self.tray_manager is not None:
                self.tray_manager.stop()
            if self._overlay is not None:
                self._close_break_overlay()
        try:
            super().destroy()
        finally:
            unregister_display_font()
