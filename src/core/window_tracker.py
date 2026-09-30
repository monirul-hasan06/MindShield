"""Monitor the active desktop window for work-zone distractions."""

from __future__ import annotations

import logging
import math
import sys
import threading
from collections.abc import Callable
from pathlib import PureWindowsPath
from urllib.parse import unquote

from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
import comtypes
from comtypes import COMError
import psutil
import pywinctl

logger = logging.getLogger(__name__)


class WindowTracker:
    """Monitor the foreground window and report or minimize out-of-zone windows.

    The distraction callback runs on the tracker thread. GUI callers should
    dispatch UI updates to their event loop.
    """

    def __init__(
        self,
        active_work_zone: str,
        on_distraction_detected: Callable[[str], None] | None = None,
        check_interval_sec: int | float = 2,
        suppress_distractions: bool = False,
    ) -> None:
        self._validate_zone(active_work_zone)
        if (
            isinstance(check_interval_sec, bool)
            or not isinstance(check_interval_sec, (int, float))
        ):
            raise TypeError("check_interval_sec must be a number")
        if not math.isfinite(check_interval_sec) or check_interval_sec <= 0:
            raise ValueError("check_interval_sec must be greater than zero and finite")
        if not isinstance(suppress_distractions, bool):
            raise TypeError("suppress_distractions must be a bool")

        self._active_work_zone = active_work_zone.strip()
        self._suppress_distractions = suppress_distractions
        self.on_distraction_detected = on_distraction_detected
        self.check_interval_sec = float(check_interval_sec)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual_break = False
        self._last_reported_title: str | None = None

    @staticmethod
    def _validate_zone(zone_title: str) -> None:
        if not isinstance(zone_title, str) or not zone_title.strip():
            raise ValueError("active_work_zone must be a non-empty string")

    @property
    def manual_break(self) -> bool:
        """Whether distraction checks are currently suspended."""
        with self._lock:
            return self._manual_break

    @property
    def active_work_zone(self) -> str:
        """The title fragment currently being monitored."""
        with self._lock:
            return self._active_work_zone

    def set_manual_break(self, enabled: bool) -> None:
        """Suspend or resume distraction detection during a manual break."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")

        with self._lock:
            self._manual_break = enabled
            if enabled:
                self._last_reported_title = None

    def set_work_zone(self, new_zone: str) -> None:
        """Switch the monitored work-zone title without restarting the tracker."""
        self._validate_zone(new_zone)
        with self._lock:
            self._active_work_zone = new_zone.strip()
            self._last_reported_title = None

    def set_suppress_distractions(self, enabled: bool) -> None:
        """Enable or disable minimizing windows outside the active work zone."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        with self._lock:
            self._suppress_distractions = enabled

    @staticmethod
    def _matches_work_zone(
        work_zone: str,
        title: str,
        process_name: str = "",
        executable_path: str = "",
        accessible_values: tuple[str, ...] = (),
    ) -> bool:
        """Match a zone against titles, process metadata, and UIA edit controls."""
        fragment = unquote(work_zone).replace("/", "\\").casefold()
        return any(
            fragment in unquote(candidate).replace("/", "\\").casefold()
            for candidate in (
                title,
                process_name,
                executable_path,
                *accessible_values,
            )
            if candidate
        )

    @staticmethod
    def _needs_accessible_match(work_zone: str) -> bool:
        normalized = work_zone.strip().casefold()
        return normalized.startswith(("http://", "https://", "file://")) or (
            PureWindowsPath(work_zone).is_absolute()
        )

    @staticmethod
    def _get_accessible_window_values(handle: int) -> tuple[str, ...]:
        com_initialized = False
        try:
            comtypes.CoInitializeEx(getattr(sys, "coinit_flags", 0))
            com_initialized = True
            window = Desktop(backend="uia").window(handle=handle)
            values: list[str] = []
            for control_type in ("Edit", "ComboBox"):
                for control in window.descendants(control_type=control_type):
                    text = control.window_text()
                    name = control.element_info.name
                    if text:
                        values.append(text)
                    if name and name != text:
                        values.append(name)
            return tuple(values)
        except (COMError, ElementNotFoundError, RuntimeError) as error:
            logger.warning(
                "Could not inspect accessible controls for window %s: %s",
                handle,
                error,
            )
            return ()
        finally:
            if com_initialized:
                comtypes.CoUninitialize()

    def start(self) -> None:
        """Start polling the active window on a daemon thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("WindowTracker is already running")
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self.run_loop,
                name="MindShieldWindowTracker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Stop polling and wait for the tracker thread to finish."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def run_loop(self) -> None:
        """Poll the active window until :meth:`stop` requests shutdown."""
        while not self._stop_event.is_set():
            window = pywinctl.getActiveWindow()
            if window is not None:
                title = window.title or ""
                pid = window.getPID()

                # Ignore a stale window handle rather than reporting its title.
                if pid and psutil.pid_exists(pid):
                    process_name = ""
                    executable_path = ""
                    try:
                        process = psutil.Process(pid)
                        process_name = process.name()
                        executable_path = process.exe()
                    except psutil.Error as error:
                        logger.warning(
                            "Could not inspect active process %s: %s",
                            pid,
                            error,
                        )

                    with self._lock:
                        active_work_zone = self._active_work_zone
                        manual_break = self._manual_break
                    accessible_values = ()
                    if (
                        not manual_break
                        and self._needs_accessible_match(active_work_zone)
                    ):
                        accessible_values = self._get_accessible_window_values(
                            window.getHandle()
                        )

                    callback: Callable[[str], None] | None = None
                    suppress = False
                    with self._lock:
                        if self._manual_break:
                            self._last_reported_title = None
                        elif self._matches_work_zone(
                            self._active_work_zone,
                            title,
                            process_name,
                            executable_path,
                            accessible_values,
                        ):
                            self._last_reported_title = None
                        else:
                            suppress = self._suppress_distractions
                            if title != self._last_reported_title:
                                self._last_reported_title = title
                                callback = self.on_distraction_detected

                    if suppress:
                        window.minimize()
                    if callback is not None:
                        callback(title)

            self._stop_event.wait(self.check_interval_sec)
