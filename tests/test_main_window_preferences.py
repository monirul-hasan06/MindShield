import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.core.timer_manager import TimerManager
from src.gui.main_window import (
    AnimatedActionButton,
    AnimatedProgressBar,
    AnimatedSwitch,
    MainWindow,
)


class MainWindowTimerPreferenceTests(unittest.TestCase):
    def test_navigation_has_distinct_icons_for_each_page(self) -> None:
        icons = MainWindow.NAVIGATION_ICONS

        self.assertEqual(set(icons), {"overview", "analytics", "settings"})
        self.assertEqual(len(set(icons.values())), 3)

    def test_animated_switch_draws_and_schedules_next_frame(self) -> None:
        switch = Mock()
        switch.ANIMATION_FRAMES = AnimatedSwitch.ANIMATION_FRAMES
        switch.ANIMATION_INTERVAL_MS = AnimatedSwitch.ANIMATION_INTERVAL_MS
        switch._draw_engine = Mock()
        switch._apply_widget_scaling.side_effect = lambda value: value
        switch._switch_width = 36
        switch._switch_height = 20
        switch._corner_radius = 10
        switch._border_width = 2
        switch._button_length = 18
        switch.after.return_value = "switch-frame"

        AnimatedSwitch._animate_slider(switch, 0.0, 1.0, 1)

        draw_args = (
            switch._draw_engine
            .draw_rounded_slider_with_border_and_button.call_args.args
        )
        self.assertAlmostEqual(draw_args[6], 0.04296875)
        switch.after.assert_called_once_with(
            AnimatedSwitch.ANIMATION_INTERVAL_MS,
            switch._animate_slider,
            0.0,
            1.0,
            2,
        )
        self.assertEqual(switch._slider_animation_job, "switch-frame")

    def test_progress_bar_easing_is_smooth_and_bounded(self) -> None:
        first = AnimatedProgressBar._eased_value(0.0, 1.0, 1)
        middle = AnimatedProgressBar._eased_value(0.0, 1.0, 4)
        final = AnimatedProgressBar._eased_value(0.0, 1.0, 8)

        self.assertGreater(first, 0.0)
        self.assertLess(first, middle)
        self.assertLess(middle, final)
        self.assertEqual(final, 1.0)
        self.assertAlmostEqual(
            AnimatedProgressBar._eased_value(0.8, 0.2, 8),
            0.2,
        )

    def test_action_button_color_animation_interpolates_and_schedules(self) -> None:
        button = Mock()
        button.ANIMATION_FRAMES = AnimatedActionButton.ANIMATION_FRAMES
        button.ANIMATION_INTERVAL_MS = AnimatedActionButton.ANIMATION_INTERVAL_MS
        button._color_animation_job = None
        button._displayed_color = "#000000"
        button._color_animation_start = "#000000"
        button._color_animation_target = "#ffffff"
        button.after.return_value = "button-frame"

        AnimatedActionButton._animate_color(button, "#ffffff", 2)

        self.assertEqual(button.configure.call_args.kwargs["fg_color"], "#5a5a5a")
        button.after.assert_called_once_with(
            AnimatedActionButton.ANIMATION_INTERVAL_MS,
            button._animate_color,
            "#ffffff",
            3,
        )
        self.assertEqual(button._color_animation_job, "button-frame")

    def test_navigation_icon_animation_changes_its_size(self) -> None:
        window = MainWindow.__new__(MainWindow)
        icon = Mock()
        window._page_icon_labels = {"overview": icon}
        window._page_icon_sizes = {"overview": 16.0}
        window._page_icon_jobs = {"overview": None}
        window.after = Mock(return_value="icon-frame")

        with patch("src.gui.main_window.ctk.CTkFont") as font:
            window._animate_navigation_icon("overview", 18, frame=3)

        self.assertAlmostEqual(window._page_icon_sizes["overview"], 17.296)
        icon.configure.assert_called_once_with(
            font=font.return_value,
        )
        window.after.assert_called_once_with(
            14,
            window._animate_navigation_icon,
            "overview",
            18,
            4,
            16.0,
        )
        self.assertEqual(window._page_icon_jobs["overview"], "icon-frame")

    def test_tracker_activity_indicator_spins_during_background_tracking(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._closing = False
        window.timer_manager = Mock()
        window.timer_manager.state = "work"
        window.tracker_activity_label = Mock()
        window._tracker_spinner_frame = 0
        window._tracker_spinner_job = None
        window.after = Mock(return_value="spinner-frame")

        window._animate_tracker_activity()

        window.tracker_activity_label.configure.assert_called_once_with(
            text="LIVE ◴"
        )
        window.after.assert_called_once_with(
            120,
            window._animate_tracker_activity,
            1,
        )
        self.assertEqual(window._tracker_spinner_job, "spinner-frame")

    def test_theme_transition_interpolates_palette_and_completes(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._appearance_mode = "light"
        window._theme_transition_job = None
        window._theme_transition_position = 0.0
        window._theme_transition_start_position = 0.0
        window._theme_transition_target_position = 0.0
        window._recolor_workspace = Mock()
        window._set_focus_status = Mock()
        window.timer_manager = Mock()
        window.timer_manager.state = "idle"
        window.after = Mock(return_value="theme-frame")

        with patch("src.gui.main_window.ctk.set_appearance_mode"):
            window._animate_theme_mode("dark")

        color_map = window._recolor_workspace.call_args.args[0]
        self.assertEqual(color_map["#f4f7fb"], "#f4f7fb")
        self.assertEqual(
            window._recolor_workspace.call_args.args[1],
            "#f4f7fb",
        )
        window.after.assert_called_once_with(
            MainWindow.THEME_TRANSITION_INTERVAL_MS,
            window._animate_theme_mode,
            "dark",
            1,
        )

        window.after.reset_mock()
        window._animate_theme_mode("dark", MainWindow.THEME_TRANSITION_FRAMES)

        color_map = window._recolor_workspace.call_args.args[0]
        self.assertIn("#0e1520", color_map.values())
        self.assertEqual(
            window._recolor_workspace.call_args.args[1],
            "#0e1520",
        )
        window._set_focus_status.assert_called_once_with("idle")
        window.after.assert_not_called()
        self.assertIsNone(window._theme_transition_job)

    def test_theme_color_interpolation_blends_channels(self) -> None:
        self.assertEqual(
            MainWindow._interpolate_color("#000000", "#ffffff", 0.5),
            "#808080",
        )

    def test_light_and_dark_theme_palette_is_reversible(self) -> None:
        light_to_dark = dict(MainWindow.THEME_COLOR_PAIRS)
        dark_colors = [dark for _, dark in MainWindow.THEME_COLOR_PAIRS]

        self.assertEqual(len(dark_colors), len(set(dark_colors)))
        self.assertEqual(light_to_dark["#f4f7fb"], "#0e1520")
        self.assertEqual(light_to_dark["#ffffff"], "#182231")
        self.assertEqual(light_to_dark["#fbfdff"], "#121a26")

    def test_appearance_preference_is_saved_and_applied(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._appearance_mode = "light"
        window._theme_transition_job = None
        window.appearance_menu = Mock()
        window.database = Mock()
        window.status_label = Mock()
        window.timer_manager = Mock()
        window.timer_manager.state = "idle"
        window._animate_theme_mode = Mock()
        window._set_focus_status = Mock()

        window._save_appearance_mode("Dark")

        window.database.set_settings.assert_called_once_with(
            {"appearance_mode": "dark"}
        )
        window._animate_theme_mode.assert_called_once_with("dark")
        window._set_focus_status.assert_not_called()
        window.status_label.configure.assert_called_once_with(
            text="Dark appearance enabled."
        )

    def test_appearance_preference_reverts_when_persistence_fails(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._appearance_mode = "light"
        window._theme_transition_job = None
        window.appearance_menu = Mock()
        window.database = Mock()
        window.database.set_settings.side_effect = sqlite3.Error("database unavailable")
        window.status_label = Mock()
        window._animate_theme_mode = Mock()

        window._save_appearance_mode("Dark")

        window.appearance_menu.set.assert_called_once_with("Light")
        window._animate_theme_mode.assert_not_called()
        window.status_label.configure.assert_called_once_with(
            text="Could not save appearance preference: database unavailable"
        )

    def test_focus_progress_tracks_elapsed_work_time(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.countdown_label = Mock()
        window.session_progress = Mock()
        window.timer_manager = Mock()
        window.timer_manager.state = "work"
        window.timer_manager.work_interval_sec = 1200

        window._set_countdown_display(600)

        window.session_progress.animate_to.assert_called_once_with(0.5)

    def test_focus_badge_animation_schedules_next_pulse(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._closing = False
        window._appearance_mode = "light"
        window._focus_pulse_on = False
        window._focus_pulse_job = None
        window.header_status_badge = Mock()
        window.timer_manager = Mock()
        window.timer_manager.state = "work"
        window.after = Mock(return_value="pulse-job")

        window._animate_focus_badge()

        window.header_status_badge.configure.assert_called_once_with(
            fg_color="#d3f1e2",
            text_color="#116149",
        )
        window.after.assert_called_once_with(720, window._animate_focus_badge)
        self.assertEqual(window._focus_pulse_job, "pulse-job")

    def test_window_intro_fades_from_transparent_to_opaque(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._window_intro_job = None
        window.attributes = Mock()
        window.after = Mock(return_value="fade-job")

        window._start_window_intro()

        window.attributes.assert_called_once_with("-alpha", 0.0)
        window.after.assert_called_once_with(20, window._animate_window_intro, 1)
        self.assertEqual(window._window_intro_job, "fade-job")

        window.after.reset_mock()
        window.attributes.reset_mock()
        window._animate_window_intro(10)

        window.attributes.assert_called_with("-alpha", 1.0)
        window.after.assert_not_called()
        self.assertIsNone(window._window_intro_job)

    def test_sidebar_navigation_shows_requested_page(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._appearance_mode = "light"
        window.timer_manager = Mock()
        window.timer_manager.state = "idle"
        window.page_title_label = Mock()
        window.page_description_label = Mock()
        window._page_buttons = {
            page: Mock()
            for page in MainWindow.PAGE_DETAILS
        }
        window.dashboard_tab = Mock()
        window.analytics_tab = Mock()
        window.settings_tab = Mock()

        with (
            patch("src.gui.main_window.ctk.CTkFont"),
            patch.object(window, "_apply_theme_mode"),
        ):
            window._show_page("analytics")

        window.page_title_label.configure.assert_called_once_with(
            text="Profile & Analytics"
        )
        window.dashboard_tab.grid_forget.assert_called_once_with()
        window.analytics_tab.grid.assert_called_once_with(
            row=0,
            column=0,
            sticky="nsew",
        )
        window.settings_tab.grid_forget.assert_called_once_with()

    def test_sidebar_navigation_rejects_unknown_page(self) -> None:
        window = MainWindow.__new__(MainWindow)

        with self.assertRaises(ValueError):
            window._show_page("unknown")

    def test_active_work_locks_navigation_to_current_page(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.timer_manager = Mock()
        window.timer_manager.state = "work"
        window._current_page = "overview"
        window.status_label = Mock()
        window.page_title_label = Mock()
        window.page_description_label = Mock()
        window._page_buttons = {page: Mock() for page in MainWindow.PAGE_DETAILS}
        window.dashboard_tab = Mock()
        window.analytics_tab = Mock()
        window.settings_tab = Mock()

        with patch("src.gui.main_window.ctk.CTkFont"):
            window._show_page("analytics")

        window.status_label.configure.assert_called_once_with(
            text="Pause work or take a break before changing workspace."
        )
        window.page_title_label.configure.assert_not_called()
        window.analytics_tab.grid.assert_not_called()

    def test_focus_restrictions_pin_window_and_lock_other_pages(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.attributes = Mock()
        window._current_page = "overview"
        window.status_label = Mock()
        buttons = {page: Mock() for page in MainWindow.PAGE_DETAILS}
        setattr(window, "_page_buttons", buttons)

        self.assertTrue(window._set_work_session_restrictions(True))

        window.attributes.assert_called_once_with("-topmost", True)
        buttons["overview"].configure.assert_called_once_with(state="normal")
        buttons["analytics"].configure.assert_called_once_with(state="disabled")
        buttons["settings"].configure.assert_called_once_with(state="disabled")

        window.attributes.reset_mock()
        for button in buttons.values():
            button.configure.reset_mock()

        self.assertTrue(window._set_work_session_restrictions(False))

        window.attributes.assert_called_once_with("-topmost", False)
        for button in buttons.values():
            button.configure.assert_called_once_with(state="normal")

    def test_injected_timer_uses_saved_work_and_break_intervals(self) -> None:
        timer = TimerManager()
        self.addCleanup(timer.stop, 1)
        window = MainWindow.__new__(MainWindow)
        window.timer_manager = timer
        window._work_interval_minutes = 35
        window._break_duration_seconds = 25
        window._demo_mode_enabled_at_startup = False

        window._configure_timer_from_preferences()

        self.assertEqual(timer.work_interval_sec, 35 * 60)
        self.assertEqual(timer.break_duration_sec, 25)

    def test_dashboard_control_starts_pauses_and_resumes_focus(self) -> None:
        timer = TimerManager(work_interval_sec=60)
        self.addCleanup(timer.stop, 1)
        tracker = Mock()
        tracker.manual_break = False
        window = MainWindow.__new__(MainWindow)
        window.timer_manager = timer
        window.window_tracker = tracker
        window._application_enabled = True
        window._work_started = False
        window.start_button = Mock()
        window.status_label = Mock()
        window.active_work_zone = "Study"
        window._set_work_session_restrictions = Mock(return_value=True)

        window._toggle_work()
        self.assertEqual(timer.state, "work")
        tracker.start.assert_called_once_with()
        window._set_work_session_restrictions.assert_called_once_with(True)

        window._toggle_work()
        self.assertEqual(timer.state, "paused")
        tracker.set_manual_break.assert_called_once_with(True)
        window._set_work_session_restrictions.assert_called_with(False)

        window._toggle_work()
        self.assertEqual(timer.state, "work")
        tracker.set_manual_break.assert_called_with(False)
        tracker.start.assert_called_once_with()

    def test_disabling_application_stops_all_active_focus_features(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.application_state_switch = Mock()
        window.application_state_switch.get.return_value = "off"
        window.database = Mock()
        window.timer_manager = Mock()
        window.timer_manager.state = "idle"
        window.window_tracker = Mock()
        application_controls = [Mock(), Mock()]
        setattr(window, "_application_controls", application_controls)
        window._work_started = True
        window._manual_break_active = True
        window._scheduled_break_id = 42
        window._appearance_mode = "light"
        start_button = Mock()
        window.start_button = start_button
        window.status_label = Mock()
        window.application_state_label = Mock()
        window.application_toggle_frame = Mock()
        set_restrictions = Mock(return_value=True)
        window._set_work_session_restrictions = set_restrictions
        window._set_focus_status = Mock()
        close_overlay = Mock()
        window._close_break_overlay = close_overlay

        window._toggle_application_enabled()

        self.assertFalse(window._application_enabled)
        self.assertFalse(window._work_started)
        self.assertFalse(window._manual_break_active)
        self.assertIsNone(window._scheduled_break_id)
        window.database.set_settings.assert_called_once_with(
            {"application_enabled": "false"}
        )
        window.timer_manager.reset_timer.assert_called_once_with()
        window.window_tracker.set_manual_break.assert_called_once_with(False)
        window.window_tracker.stop.assert_called_once_with(timeout=1)
        set_restrictions.assert_called_once_with(False)
        close_overlay.assert_called_once_with()
        for control in application_controls:
            control.configure.assert_called_once_with(state="disabled")
        start_button.configure.assert_called_once_with(
            text="Application Inactive",
            state="disabled",
        )
        window.application_state_label.configure.assert_called_once_with(
            text="MindShield inactive",
            text_color="#64748b",
        )
        window.application_toggle_frame.configure.assert_called_once_with(
            fg_color="#e7eef7"
        )

    def test_enabling_application_does_not_start_tracking_automatically(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.application_state_switch = Mock()
        window.application_state_switch.get.return_value = "on"
        window.database = Mock()
        timer_manager = Mock()
        timer_manager.state = "idle"
        window.timer_manager = timer_manager
        application_controls = [Mock()]
        setattr(window, "_application_controls", application_controls)
        window._appearance_mode = "dark"
        start_button = Mock()
        window.start_button = start_button
        window.status_label = Mock()
        window.application_state_label = Mock()
        window.application_toggle_frame = Mock()
        window._set_focus_status = Mock()

        window._toggle_application_enabled()

        self.assertTrue(window._application_enabled)
        timer_manager.start_work.assert_not_called()
        application_controls[0].configure.assert_called_once_with(
            state="normal"
        )
        start_button.configure.assert_called_once_with(
            text="Start Work",
            state="normal",
        )
        window.application_state_label.configure.assert_called_once_with(
            text="MindShield active",
            text_color="#6ee7b7",
        )
        window.application_toggle_frame.configure.assert_called_once_with(
            fg_color="#163b30"
        )

    def test_failed_application_state_save_keeps_current_state(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.application_state_switch = Mock()
        window.application_state_switch.get.return_value = "off"
        window.database = Mock()
        window.database.set_settings.side_effect = sqlite3.Error("disk full")
        window._application_enabled = True
        window.status_label = Mock()
        window.timer_manager = Mock()
        window.window_tracker = Mock()

        window._toggle_application_enabled()

        self.assertTrue(window._application_enabled)
        window.application_state_switch.select.assert_called_once_with()
        window.timer_manager.reset_timer.assert_not_called()
        window.window_tracker.stop.assert_not_called()
        window.status_label.configure.assert_called_once_with(
            text="Could not save application state: disk full"
        )

    def test_inactive_application_ignores_queued_core_callbacks(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._closing = False
        window._application_enabled = False
        window.after = Mock()

        window._on_break_start()
        window._show_break_overlay()
        window._on_distraction_detected("YouTube")

        window.after.assert_not_called()

    def test_inactive_application_rejects_manual_breaks(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._application_enabled = False

        with self.assertRaisesRegex(RuntimeError, "Turn on MindShield"):
            window._start_manual_break(5)

    def test_manual_break_starts_tracking_for_automatic_resume(self) -> None:
        timer = TimerManager()
        self.addCleanup(timer.stop, 1)
        tracker = Mock()
        tracker.manual_break = False
        window = MainWindow.__new__(MainWindow)
        window.timer_manager = timer
        window.window_tracker = tracker
        window._application_enabled = True
        window._work_started = False
        window._manual_break_active = False
        window.status_label = Mock()
        window._set_work_session_restrictions = Mock(return_value=True)

        window._start_manual_break(5)

        self.assertTrue(window._work_started)
        self.assertEqual(timer.state, "manual_break")
        tracker.start.assert_called_once_with()
        tracker.set_manual_break.assert_called_with(True)
        window._set_work_session_restrictions.assert_called_once_with(False)

    def test_injected_timer_restores_saved_demo_mode(self) -> None:
        timer = TimerManager()
        self.addCleanup(timer.stop, 1)
        window = MainWindow.__new__(MainWindow)
        window.timer_manager = timer
        window._work_interval_minutes = 35
        window._break_duration_seconds = 25
        window._demo_mode_enabled_at_startup = True

        window._configure_timer_from_preferences()

        self.assertEqual(timer.work_interval_sec, 10)
        self.assertEqual(timer.break_duration_sec, 5)
        timer.toggle_demo_mode(False)
        self.assertEqual(timer.work_interval_sec, 35 * 60)
        self.assertEqual(timer.break_duration_sec, 25)


if __name__ == "__main__":
    unittest.main()
