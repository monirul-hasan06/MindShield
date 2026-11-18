import unittest
from unittest.mock import Mock, patch

from src.core.tray_manager import TrayManager


class TrayManagerExitTests(unittest.TestCase):
    def _manager(self, closing: bool) -> tuple[TrayManager, Mock, Mock]:
        window = Mock()
        window._closing = False
        window.after.side_effect = lambda _delay, callback: callback()
        icon = Mock()
        on_exit = Mock(side_effect=lambda: setattr(window, "_closing", closing))
        with patch("src.core.tray_manager.pystray.Icon", return_value=icon):
            manager = TrayManager(window, on_exit=on_exit)
        manager._started = True
        manager._thread = None
        return manager, icon, window

    def test_exit_keeps_tray_available_when_app_defers_exit(self) -> None:
        manager, icon, window = self._manager(closing=False)

        manager._exit()

        icon.stop.assert_not_called()
        window.destroy.assert_not_called()

    def test_exit_stops_tray_after_app_accepts_exit(self) -> None:
        manager, icon, _window = self._manager(closing=True)

        manager._exit()

        icon.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
