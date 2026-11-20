"""Keep one MindShield process per Windows user session."""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002
MUTEX_NAME = "Local\\MindShield.SingleInstance"
EVENT_NAME = "Local\\MindShield.ShowWindow"


class SingleInstance:
    """A named mutex plus an event the second launch uses to reveal the first."""

    def __init__(self) -> None:
        kernel32 = ctypes.windll.kernel32
        self._kernel32 = kernel32
        self._mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        self.acquired = bool(self._mutex) and kernel32.GetLastError() != ERROR_ALREADY_EXISTS
        self._event = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self.acquired:
            self._event = kernel32.CreateEventW(None, False, False, EVENT_NAME)
        else:
            self.notify_existing()

    def notify_existing(self) -> None:
        """Ask the already running MindShield window to show itself."""
        kernel32 = self._kernel32
        event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
        if not event:
            logger.warning("MindShield is already running, but its window could not be signaled")
            return
        try:
            kernel32.SetEvent(event)
        finally:
            kernel32.CloseHandle(event)

    def listen(self, on_show: Callable[[], None]) -> None:
        """Call ``on_show`` from a background thread when another launch asks."""
        if not self.acquired or self._event is None:
            return
        if self._thread is not None and self._thread.is_alive():
            return

        def _wait() -> None:
            while not self._stop.is_set():
                result = self._kernel32.WaitForSingleObject(self._event, 500)
                if result == WAIT_OBJECT_0 and not self._stop.is_set():
                    try:
                        on_show()
                    except Exception:
                        logger.exception("Could not show the existing MindShield window")

        self._thread = threading.Thread(
            target=_wait,
            name="MindShieldSingleInstance",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Stop waiting and release the instance handles."""
        self._stop.set()
        if self._event:
            self._kernel32.SetEvent(self._event)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        if self._event:
            self._kernel32.CloseHandle(self._event)
            self._event = None
        if self._mutex:
            self._kernel32.CloseHandle(self._mutex)
            self._mutex = None
