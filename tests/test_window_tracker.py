import threading
import unittest
from unittest.mock import Mock, patch

from src.core.window_tracker import WindowTracker


class WindowTrackerMatchingTests(unittest.TestCase):
    def test_matches_window_title_case_insensitively(self) -> None:
        self.assertTrue(
            WindowTracker._matches_work_zone(
                "biology lecture",
                "Biology Lecture - Browser",
            )
        )

    def test_matches_process_name_and_executable_path(self) -> None:
        self.assertTrue(
            WindowTracker._matches_work_zone(
                "code.exe",
                "Untitled",
                process_name="Code.exe",
            )
        )
        self.assertTrue(
            WindowTracker._matches_work_zone(
                r"\study\apps",
                "Untitled",
                executable_path=r"C:\Study\Apps\reader.exe",
            )
        )

    def test_matches_browser_url_and_explorer_folder_accessibility_values(self) -> None:
        self.assertTrue(
            WindowTracker._matches_work_zone(
                "https://example.com/course/lesson",
                "Lesson - Browser",
                accessible_values=("https://example.com/course/lesson",),
            )
        )
        self.assertTrue(
            WindowTracker._matches_work_zone(
                r"C:\Users\Monir\Documents\Research",
                "Research - File Explorer",
                accessible_values=("C:\\Users\\Monir\\Documents\\Research",),
            )
        )

    def test_accessibility_is_used_for_urls_and_absolute_paths(self) -> None:
        self.assertTrue(
            WindowTracker._needs_accessible_match("https://example.com/lesson")
        )
        self.assertTrue(
            WindowTracker._needs_accessible_match(
                r"C:\Users\Monir\Documents\Research"
            )
        )
        self.assertFalse(WindowTracker._needs_accessible_match("Visual Studio Code"))

    def test_rejects_nonmatching_process_and_title(self) -> None:
        self.assertFalse(
            WindowTracker._matches_work_zone(
                "visual studio code",
                "Inbox - Browser",
                process_name="browser.exe",
                executable_path=r"C:\Program Files\Browser\browser.exe",
            )
        )

    def test_profile_rules_use_enabled_typed_or_matching(self) -> None:
        rules = (
            ("title", "lecture",),
            ("process", "code.exe",),
        )
        self.assertTrue(
            WindowTracker._matches_work_zone_rules(
                rules,
                "Lecture notes",
                process_name="browser.exe",
            )
        )
        self.assertTrue(
            WindowTracker._matches_work_zone_rules(
                rules,
                "Untitled",
                process_name="Code.exe",
            )
        )
        self.assertFalse(
            WindowTracker._matches_work_zone_rules(
                (("title", "lecture"),),
                "Untitled",
                process_name="lecture.exe",
            )
        )

    def test_whole_token_mode_does_not_match_a_longer_word(self) -> None:
        self.assertFalse(
            WindowTracker._matches_work_zone_rules(
                (("title", "code", "token"),),
                "Encode notes",
            )
        )
        self.assertTrue(
            WindowTracker._matches_work_zone_rules(
                (("title", "code", "token"),),
                "Visual Studio Code - notes",
            )
        )
        self.assertTrue(
            WindowTracker._matches_work_zone_rules(
                (("title", "code", "contains"),),
                "Encode notes",
            )
        )

    def test_accessible_values_are_reused_for_the_same_foreground_window(self) -> None:
        class FakeWindow:
            title = "Lesson - Browser"

            def getPID(self) -> int:
                return 44

            def getHandle(self) -> int:
                return 90

        tracker = WindowTracker("Study")
        tracker.set_work_zone_profile(
            "Study",
            [("accessible", "https://example.com/lesson", True)],
        )
        with (
            patch("src.core.window_tracker.psutil.pid_exists", return_value=True),
            patch("src.core.window_tracker.psutil.Process") as process_factory,
            patch.object(
                WindowTracker,
                "_get_accessible_window_values",
                return_value=("https://example.com/lesson",),
            ) as read_accessible,
        ):
            tracker._inspect_window(FakeWindow())
            tracker._inspect_window(FakeWindow())

        self.assertEqual(read_accessible.call_count, 1)
        process_factory.assert_not_called()

    def test_profile_accessible_rule_matches_browser_url_or_folder(self) -> None:
        self.assertTrue(
            WindowTracker._matches_work_zone_rules(
                (("accessible", "example.com/course"),),
                "Lesson - Browser",
                accessible_values=("https://example.com/course/lesson",),
            )
        )
        self.assertFalse(
            WindowTracker._matches_work_zone_rules(
                (("accessible", "example.com/course"),),
                "Lesson - Browser",
            )
        )

    def test_title_only_profile_does_not_read_process_details(self) -> None:
        class FakeWindow:
            title = "Study notes"

            def getPID(self) -> int:
                return 1234

        tracker = WindowTracker("Study")
        tracker.set_work_zone_profile(
            "Study",
            [("title", "Study", True)],
        )
        with (
            patch("src.core.window_tracker.psutil.pid_exists", return_value=True),
            patch("src.core.window_tracker.psutil.Process") as process_factory,
        ):
            tracker._inspect_window(FakeWindow())

        process_factory.assert_not_called()

    def test_focus_guard_minimizes_blocked_url_and_requests_work_app(self) -> None:
        class FakeWindow:
            title = "YouTube - Chrome"
            minimized = False

            def getPID(self) -> int:
                return 1234

            def getHandle(self) -> int:
                return 5678

            def minimize(self) -> None:
                self.minimized = True

        process = Mock()
        process.name.return_value = "chrome.exe"
        process.exe.return_value = r"C:\Apps\chrome.exe"
        window = FakeWindow()
        focus_app_required = Mock()
        tracker = WindowTracker("Study")
        tracker.set_focus_guard(
            True,
            r"C:\Apps\chrome.exe",
            [("accessible", "youtube.com", True)],
        )
        tracker.on_focus_app_required = focus_app_required

        with (
            patch("src.core.window_tracker.psutil.pid_exists", return_value=True),
            patch("src.core.window_tracker.psutil.Process", return_value=process),
            patch.object(
                WindowTracker,
                "_get_accessible_window_values",
                return_value=("https://www.youtube.com/watch",),
            ),
        ):
            tracker._inspect_window(window)

        self.assertTrue(window.minimized)
        focus_app_required.assert_called_once_with(5678)

    def test_enabled_suppression_minimizes_and_reports_distractions(self) -> None:
        tracker_holder: list[WindowTracker] = []
        reported_titles: list[str] = []
        reported_event = threading.Event()

        class FakeWindow:
            title = "Inbox - Browser"
            minimized = False

            def getPID(self) -> int:
                return 1234

            def minimize(self) -> None:
                self.minimized = True
                tracker_holder[0].stop()

        window = FakeWindow()
        process = Mock()
        process.name.return_value = "browser.exe"
        process.exe.return_value = r"C:\Browser\browser.exe"

        def report(title: str) -> None:
            reported_titles.append(title)
            reported_event.set()

        tracker = WindowTracker(
            "Visual Studio Code",
            on_distraction_detected=report,
            check_interval_sec=0.01,
            suppress_distractions=True,
        )
        tracker_holder.append(tracker)

        with (
            patch("src.core.window_tracker.pywinctl.getActiveWindow", return_value=window),
            patch("src.core.window_tracker.psutil.pid_exists", return_value=True),
            patch("src.core.window_tracker.psutil.Process", return_value=process),
        ):
            tracker.start()
            self.assertTrue(reported_event.wait(2))
            tracker.stop(timeout=2)

        self.assertTrue(window.minimized)
        self.assertEqual(reported_titles, ["Inbox - Browser"])

    def test_active_window_read_failure_does_not_stop_tracker(self) -> None:
        reported_event = threading.Event()

        class FakeWindow:
            title = "Inbox - Browser"

            def getPID(self) -> int:
                return 1234

            def minimize(self) -> None:
                return None

        process = Mock()
        process.name.return_value = "browser.exe"
        process.exe.return_value = r"C:\Browser\browser.exe"
        tracker = WindowTracker(
            "Visual Studio Code",
            on_distraction_detected=lambda _title: reported_event.set(),
            check_interval_sec=0.01,
        )

        with (
            patch(
                "src.core.window_tracker.pywinctl.getActiveWindow",
                side_effect=[OSError("window API unavailable"), FakeWindow()],
            ),
            patch("src.core.window_tracker.psutil.pid_exists", return_value=True),
            patch("src.core.window_tracker.psutil.Process", return_value=process),
            self.assertLogs("src.core.window_tracker", level="WARNING"),
        ):
            tracker.start()
            self.assertTrue(reported_event.wait(2))
            tracker.stop(timeout=2)

    def test_tracker_does_not_read_windows_during_manual_break(self) -> None:
        tracker = WindowTracker("Study", check_interval_sec=0.01)
        tracker.set_manual_break(True)
        with patch("src.core.window_tracker.pywinctl.getActiveWindow") as get_active_window:
            tracker.start()
            threading.Event().wait(0.05)
            tracker.stop(timeout=2)

        get_active_window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
