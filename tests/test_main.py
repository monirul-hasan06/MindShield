import unittest
from unittest.mock import Mock, patch

import main


class ApplicationStartupTests(unittest.TestCase):
    @patch("main.TrayManager")
    @patch("main.MainWindow")
    @patch("main.WindowTracker")
    @patch("main.TimerManager")
    @patch("main.DatabaseHandler")
    def test_startup_does_not_seed_demo_activity(
        self,
        database_handler: Mock,
        _timer_manager: Mock,
        _window_tracker: Mock,
        main_window: Mock,
        tray_manager: Mock,
    ) -> None:
        app = main_window.return_value
        database = database_handler.return_value

        main.main()

        database.seed_demo_data.assert_not_called()
        database.init_db.assert_not_called()
        tray_manager.assert_called_once()
        app.mainloop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
