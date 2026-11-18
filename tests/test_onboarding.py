import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from src.gui.main_window import (
    MainWindow,
    get_next_greeting_change,
    get_time_of_day_greeting,
    normalize_user_name,
)


class OnboardingHelperTests(unittest.TestCase):
    def test_time_of_day_greetings_follow_local_hour_boundaries(self) -> None:
        self.assertEqual(get_time_of_day_greeting(0), "Good morning")
        self.assertEqual(get_time_of_day_greeting(11), "Good morning")
        self.assertEqual(get_time_of_day_greeting(12), "Good afternoon")
        self.assertEqual(get_time_of_day_greeting(16), "Good afternoon")
        self.assertEqual(get_time_of_day_greeting(17), "Good evening")
        self.assertEqual(get_time_of_day_greeting(23), "Good evening")

    def test_time_of_day_greeting_rejects_invalid_hours(self) -> None:
        for hour in (-1, 24, True, 12.5):
            with self.subTest(hour=hour), self.assertRaises(ValueError):
                get_time_of_day_greeting(hour)

    def test_next_greeting_change_returns_the_next_boundary(self) -> None:
        self.assertEqual(
            get_next_greeting_change(datetime(2026, 6, 1, 8, 30)),
            datetime(2026, 6, 1, 12),
        )
        self.assertEqual(
            get_next_greeting_change(datetime(2026, 6, 1, 12, 30)),
            datetime(2026, 6, 1, 17),
        )
        self.assertEqual(
            get_next_greeting_change(datetime(2026, 6, 1, 17, 30)),
            datetime(2026, 6, 2),
        )

    def test_user_name_is_trimmed_and_must_not_be_empty(self) -> None:
        self.assertEqual(
            normalize_user_name("  Monirul Hasan Mithu  "),
            "Monirul Hasan Mithu",
        )
        with self.assertRaises(ValueError):
            normalize_user_name("   ")
        with self.assertRaises(TypeError):
            normalize_user_name(None)

    def test_onboarding_requires_name_before_skipping_or_continuing(self) -> None:
        window = SimpleNamespace(
            _onboarding_name_entry=Mock(get=Mock(return_value="  ")),
            _onboarding_feedback_label=Mock(),
            database=Mock(),
        )

        MainWindow._save_onboarding_name(window, start_walkthrough=False)

        window.database.set_settings.assert_not_called()
        window._onboarding_feedback_label.configure.assert_called_once_with(
            text="Enter your name to continue."
        )

    def test_saving_onboarding_name_starts_the_walkthrough(self) -> None:
        window = SimpleNamespace(
            _onboarding_name_entry=Mock(get=Mock(return_value="  Monirul  ")),
            database=Mock(),
            user_name_entry=Mock(),
            _refresh_dashboard_greeting=Mock(),
            _render_walkthrough_step=Mock(),
        )

        MainWindow._save_onboarding_name(window, start_walkthrough=True)

        window.database.set_settings.assert_called_once_with(
            {"user_name": "Monirul"}
        )
        self.assertEqual(window._user_name, "Monirul")
        window._refresh_dashboard_greeting.assert_called_once_with()
        window._render_walkthrough_step.assert_called_once_with(0)

    def test_skipping_walkthrough_finishes_onboarding_after_name_save(self) -> None:
        window = SimpleNamespace(
            _onboarding_name_entry=Mock(get=Mock(return_value="Monirul")),
            database=Mock(),
            user_name_entry=Mock(),
            _refresh_dashboard_greeting=Mock(),
            _finish_onboarding=Mock(),
        )

        MainWindow._save_onboarding_name(window, start_walkthrough=False)

        window._finish_onboarding.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
