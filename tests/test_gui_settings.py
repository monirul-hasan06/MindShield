import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.gui.main_window import MainWindow, sqlite3 as database_sqlite3


class EyeCareSettingsTests(unittest.TestCase):
    def _window(self, interval: str, break_length: str) -> SimpleNamespace:
        return SimpleNamespace(
            work_interval_entry=Mock(get=Mock(return_value=interval)),
            break_duration_entry=Mock(get=Mock(return_value=break_length)),
            database=Mock(),
            timer_manager=Mock(),
            _work_interval_minutes=20,
            _break_duration_seconds=20,
            _demo_mode_enabled=Mock(return_value=False),
            focus_duration_entry=Mock(),
            status_label=Mock(),
            _set_countdown_display=Mock(),
        )

    def test_saves_focus_and_linked_eye_care_timing(self) -> None:
        window = self._window("45", "30")

        MainWindow._apply_timer_settings(window)

        window.database.set_settings.assert_called_once_with(
            {
                "work_interval_minutes": "45",
                "break_duration_seconds": "30",
            }
        )
        window.timer_manager.configure_intervals.assert_called_once_with(
            45 * 60,
            30,
        )
        self.assertEqual(window._work_interval_minutes, 45)
        self.assertEqual(window._break_duration_seconds, 30)
        window._set_countdown_display.assert_called_once_with(45 * 60)

    def test_rejects_out_of_range_interval_without_saving(self) -> None:
        window = self._window("241", "30")

        MainWindow._apply_timer_settings(window)

        window.database.set_settings.assert_not_called()
        window.timer_manager.configure_intervals.assert_not_called()
        window.status_label.configure.assert_called_once()

    def test_rejects_nonpositive_eye_care_break_length(self) -> None:
        window = self._window("20", "0")

        MainWindow._apply_timer_settings(window)

        window.database.set_settings.assert_not_called()
        window.timer_manager.configure_intervals.assert_not_called()
        window.status_label.configure.assert_called_once()


class AppearanceSettingsTests(unittest.TestCase):
    def _window(self) -> SimpleNamespace:
        return SimpleNamespace(
            database=Mock(),
            appearance_menu=Mock(),
            dashboard_appearance_menu=Mock(),
            _appearance_mode="dark",
            _appearance_label=Mock(return_value="Dark"),
            status_label=Mock(),
        )

    def test_saves_and_synchronizes_dashboard_and_settings_themes(self) -> None:
        window = self._window()

        with patch("src.gui.settings_view.ctk.set_appearance_mode") as set_appearance:
            MainWindow._save_appearance_mode(window, "Light")

        window.database.set_settings.assert_called_once_with(
            {"appearance_mode": "light"}
        )
        self.assertEqual(window._appearance_mode, "light")
        window.appearance_menu.set.assert_called_once_with("Light")
        window.dashboard_appearance_menu.set.assert_called_once_with("Light")
        set_appearance.assert_called_once_with("light")

    def test_failed_theme_save_restores_both_menus(self) -> None:
        window = self._window()
        window.database.set_settings.side_effect = database_sqlite3.Error(
            "database unavailable"
        )

        with patch("src.gui.settings_view.ctk.set_appearance_mode") as set_appearance:
            MainWindow._save_appearance_mode(window, "Light")

        self.assertEqual(window._appearance_mode, "dark")
        window.appearance_menu.set.assert_called_once_with("Dark")
        window.dashboard_appearance_menu.set.assert_called_once_with("Dark")
        set_appearance.assert_not_called()
        window.status_label.configure.assert_called_once()


class ReducedMotionSettingsTests(unittest.TestCase):
    def _window(self, current: bool, selected: int) -> SimpleNamespace:
        return SimpleNamespace(
            database=Mock(),
            reduced_motion_switch=Mock(get=Mock(return_value=selected)),
            _reduced_motion=current,
            dashboard_scroll_frame=Mock(),
            status_label=Mock(),
        )

    def test_saves_and_applies_reduced_motion(self) -> None:
        window = self._window(current=False, selected=1)

        MainWindow._save_reduced_motion(window)

        window.database.set_settings.assert_called_once_with(
            {"reduced_motion": "true"}
        )
        self.assertTrue(window._reduced_motion)
        window.dashboard_scroll_frame.set_reduced_motion.assert_called_once_with(
            True
        )

    def test_failed_save_restores_previous_toggle_state(self) -> None:
        window = self._window(current=False, selected=1)
        window.database.set_settings.side_effect = database_sqlite3.Error(
            "database unavailable"
        )

        MainWindow._save_reduced_motion(window)

        self.assertFalse(window._reduced_motion)
        window.reduced_motion_switch.deselect.assert_called_once()
        window.dashboard_scroll_frame.set_reduced_motion.assert_not_called()
        window.status_label.configure.assert_called_once()


class KeyboardShortcutTests(unittest.TestCase):
    def test_shortcut_toggles_active_work(self) -> None:
        window = SimpleNamespace(
            timer_manager=SimpleNamespace(state="work"),
            _toggle_work_pause=Mock(),
        )
        event = SimpleNamespace(widget=SimpleNamespace(master=None))

        result = MainWindow._toggle_work_pause_shortcut(window, event)

        self.assertEqual(result, "break")
        window._toggle_work_pause.assert_called_once()

    def test_global_hotkey_toggles_work_while_the_window_is_withdrawn(self) -> None:
        window = SimpleNamespace(
            timer_manager=SimpleNamespace(state="work"),
            _toggle_work_pause=Mock(),
            focus_get=Mock(return_value=None),
        )

        MainWindow._handle_global_hotkey(window)

        window._toggle_work_pause.assert_called_once()

    def test_shortcut_does_not_interfere_with_text_entry(self) -> None:
        class FakeEntry:
            master = None

        window = SimpleNamespace(
            timer_manager=SimpleNamespace(state="work"),
            _toggle_work_pause=Mock(),
        )
        event = SimpleNamespace(widget=FakeEntry())

        with patch("src.gui.user_text.ctk.CTkEntry", FakeEntry):
            result = MainWindow._toggle_work_pause_shortcut(window, event)

        self.assertIsNone(result)
        window._toggle_work_pause.assert_not_called()


if __name__ == "__main__":
    unittest.main()
