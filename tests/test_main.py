import unittest
from unittest.mock import patch

import main


class MainStartupTests(unittest.TestCase):
    def test_startup_seeds_demo_data_and_wires_services(self) -> None:
        with (
            patch("main.DatabaseHandler") as database_handler,
            patch("main.TimerManager") as timer_manager,
            patch("main.WindowTracker") as window_tracker,
            patch("main.MainWindow") as main_window,
            patch("main.TrayManager") as tray_manager,
        ):
            database = database_handler.return_value
            app = main_window.return_value

            main.main()

            database.init_db.assert_called_once_with()
            database.seed_demo_data.assert_called_once_with()
            main_window.assert_called_once_with(
                database=database,
                timer_manager=timer_manager.return_value,
                window_tracker=window_tracker.return_value,
                start_tray=False,
            )
            self.assertEqual(
                timer_manager.return_value.on_break_start,
                app._on_break_start,
            )
            tray_manager.assert_called_once_with(
                app_instance=app,
                on_meeting_mode=app._start_one_hour_meeting_break,
                on_exit=app.destroy,
            )
            app.attach_tray_manager.assert_called_once_with(
                tray_manager.return_value
            )
            app.mainloop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
