"""Global Ctrl+Shift+Space hotkey for pausing MindShield work and breaks."""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes

logger = logging.getLogger(__name__)

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 1


class GlobalHotkeyListener:
    """Listen for Ctrl+Shift+Space even when the main window is in the tray."""

    def __init__(self, on_hotkey: Callable[[], None]) -> None:
        self._on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None

    def start(self) -> None:
        """Start the hotkey message loop on a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="MindShieldHotkey",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Ask the hotkey thread to unregister and exit."""
        thread_id = self._thread_id
        if thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = bool(
            user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, VK_SPACE)
        )
        if not registered:
            logger.warning("Could not register the Ctrl+Shift+Space hotkey")
            return
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    try:
                        self._on_hotkey()
                    except Exception:
                        logger.exception("MindShield hotkey callback failed")
                else:
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self._thread_id = None
