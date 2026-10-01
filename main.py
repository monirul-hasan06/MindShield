"""Launch the MindShield desktop application."""

from src.core.timer_manager import TimerManager
from src.core.tray_manager import TrayManager
from src.core.window_tracker import WindowTracker
from src.database.db_handler import DatabaseHandler
from src.gui.main_window import MainWindow


def main() -> None:
    """Initialize persistence and launch the GUI."""
    database = DatabaseHandler()
    database.init_db()
    database.seed_demo_data()

    timer_manager = TimerManager()
    window_tracker = WindowTracker(active_work_zone="Focus")
    app = MainWindow(
        database=database,
        timer_manager=timer_manager,
        window_tracker=window_tracker,
        start_tray=False,
    )
    timer_manager.on_break_start = app._on_break_start
    tray_manager = TrayManager(
        app_instance=app,
        on_meeting_mode=app._start_one_hour_meeting_break,
        on_exit=app.destroy,
    )
    app.attach_tray_manager(tray_manager)
    app.mainloop()


if __name__ == "__main__":
    main()
