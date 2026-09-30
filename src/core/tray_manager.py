"""System-tray integration for the MindShield desktop window."""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Protocol
from typing import Any

import pystray
from PIL import Image, ImageDraw


class TrayWindow(Protocol):
    """Tk window operations used by the tray manager."""

    protocol: Any

    def after(
        self,
        ms: int,
        func: Callable[..., Any],
        *args: Any,
    ) -> str: ...

    def withdraw(self) -> None: ...

    def deiconify(self) -> None: ...

    def lift(self) -> None: ...

    def focus_force(self) -> None: ...

    def destroy(self) -> None: ...


class TrayManager:
    """Manage the MindShield tray icon and its CustomTkinter window."""

    def __init__(
        self,
        app_instance: TrayWindow,
        on_meeting_mode: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
    ) -> None:
        self.app_instance = app_instance
        self.window = app_instance
        self.on_meeting_mode = on_meeting_mode or getattr(
            app_instance, "_start_meeting_break", None
        )
        self.on_exit = on_exit or app_instance.destroy
        self._icon = pystray.Icon(
            "MindShield",
            self._create_icon_image(),
            "MindShield",
            menu=pystray.Menu(
                pystray.MenuItem("Open MindShield", self._open_window, default=True),
                pystray.MenuItem(
                    "Pause / Meeting Mode (saved duration)",
                    self._meeting_mode,
                    enabled=self.on_meeting_mode is not None,
                ),
                pystray.MenuItem("Exit", self._exit),
            ),
        )
        self._state_lock = threading.Lock()
        self._started = False
        self._thread: threading.Thread | None = None

    @staticmethod
    def _create_icon_image() -> Image.Image:
        """Create a small app icon without requiring an external asset."""
        image = Image.new("RGB", (64, 64), "#14213d")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((10, 10, 54, 54), radius=12, fill="#38bdf8")
        draw.ellipse((23, 18, 41, 36), fill="#14213d")
        draw.rounded_rectangle((20, 34, 44, 47), radius=6, fill="#14213d")
        return image

    def start(self) -> None:
        """Start the tray event loop on a background daemon thread."""
        self.run_tray()

    def run_tray(self) -> None:
        """Start the tray icon's event loop on its own daemon thread."""
        with self._state_lock:
            if self._started:
                return

        self._set_close_handler()
        thread = threading.Thread(
            target=self._run_icon,
            name="MindShieldTray",
            daemon=True,
        )
        with self._state_lock:
            if self._started:
                return
            self._started = True
            self._thread = thread
            thread.start()

    def _run_icon(self) -> None:
        try:
            self._icon.run()
        finally:
            with self._state_lock:
                self._started = False
                self._thread = None

    def _set_close_handler(self) -> None:
        self.window.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

    def stop(self) -> None:
        """Stop the tray icon's event loop."""
        with self._state_lock:
            if not self._started:
                return
            thread = self._thread

        self._icon.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def hide_to_tray(self) -> None:
        """Hide the main window while keeping the tray icon available."""
        self.window.withdraw()

    def minimize_to_tray(self) -> None:
        """Backward-compatible alias for :meth:`hide_to_tray`."""
        self.hide_to_tray()

    def show_from_tray(self) -> None:
        """Restore and focus the main window from a tray-menu callback."""
        self._dispatch_to_window(self._restore_window)

    def _dispatch_to_window(self, callback: Callable[[], None]) -> None:
        self.window.after(0, callback)

    def _open_window(self, *_args: Any) -> None:
        del _args
        self.show_from_tray()

    def _restore_window(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def _meeting_mode(self, *_args: Any) -> None:
        del _args
        if self.on_meeting_mode is not None:
            self._dispatch_to_window(self.on_meeting_mode)

    def _exit(self, *_args: Any) -> None:
        del _args
        self.stop()
        if self.on_exit is not None:
            self._dispatch_to_window(self.on_exit)
        else:
            self._dispatch_to_window(self.window.destroy)
