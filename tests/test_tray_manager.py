import threading
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock, patch

from src.core.tray_manager import TrayManager


class FakeWindow:
    def __init__(self) -> None:
        self.protocol = Mock()
        self.withdraw = Mock()
        self.deiconify = Mock()
        self.lift = Mock()
        self.focus_force = Mock()
        self.destroy = Mock()
        self._start_one_hour_meeting_break = Mock()

    def after(
        self,
        ms: int,
        func: Callable[..., Any],
        *args: Any,
    ) -> str:
        del ms
        func(*args)
        return "callback-id"


class FakeIcon:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped = threading.Event()

    def run(self) -> None:
        self.started.set()
        self.stopped.wait(2)

    def stop(self) -> None:
        self.stopped.set()


class TrayManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = FakeWindow()
        self.icon = FakeIcon()
        self.menu_items: list[tuple[object, ...]] = []

        def create_menu_item(*args: object, **kwargs: object) -> Mock:
            del kwargs
            self.menu_items.append(args)
            return Mock()

        self.patches = (
            patch("src.core.tray_manager.pystray.Icon", return_value=self.icon),
            patch("src.core.tray_manager.pystray.Menu", return_value=Mock()),
            patch("src.core.tray_manager.pystray.MenuItem", side_effect=create_menu_item),
        )
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.manager = TrayManager(self.window)

    def test_menu_items_match_tray_actions(self) -> None:
        self.assertEqual(
            [item[0] for item in self.menu_items],
            [
                "Open MindShield",
                "Pause / Meeting Mode (1 Hr)",
                "Exit",
            ],
        )

    def test_open_and_meeting_actions_are_dispatched_to_window(self) -> None:
        self.manager._open_window()
        self.manager._meeting_mode()

        self.window.deiconify.assert_called_once_with()
        self.window.lift.assert_called_once_with()
        self.window.focus_force.assert_called_once_with()
        self.window._start_one_hour_meeting_break.assert_called_once_with()

    def test_hide_to_tray_withdraws_main_window(self) -> None:
        self.manager.hide_to_tray()

        self.window.withdraw.assert_called_once_with()

    def test_run_tray_starts_in_background_and_exit_stops_cleanly(self) -> None:
        self.manager.run_tray()
        self.assertTrue(self.icon.started.wait(2))
        self.window.protocol.assert_called_once()

        self.manager._exit()

        self.assertTrue(self.icon.stopped.is_set())
        self.window.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
