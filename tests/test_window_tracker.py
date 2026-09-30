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


if __name__ == "__main__":
    unittest.main()
