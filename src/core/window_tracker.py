"""Monitor the active desktop window for work-zone distractions."""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import PureWindowsPath
from urllib.parse import unquote

import pywintypes
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

    MATCH_TYPES = frozenset({"any", "title", "process", "path", "accessible"})

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
        self._work_zone_rules: tuple[tuple[str, str], ...] = (
            ("any", self._active_work_zone),
        )
        self._focus_guard_enabled = False
        self._focus_app_path = ""
        self._focus_guard_rules: tuple[tuple[str, str], ...] = ()
        self._suppress_distractions = suppress_distractions
        self.on_distraction_detected = on_distraction_detected
        self.on_focus_app_required: Callable[[int | None], None] | None = None
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
            self._work_zone_rules = (("any", self._active_work_zone),)
            self._last_reported_title = None

    def set_work_zone_profile(
        self,
        profile_name: str,
        rules: Sequence[tuple[str, str, bool]],
    ) -> None:
        """Apply a named profile with enabled typed matching rules."""
        self._validate_zone(profile_name)
        normalized_rules: list[tuple[str, str]] = []
        for rule in rules:
            if not isinstance(rule, tuple) or len(rule) != 3:
                raise ValueError("Each work-zone rule must have three values")
            match_type, pattern, enabled = rule
            if not isinstance(match_type, str) or match_type not in self.MATCH_TYPES:
                raise ValueError(f"Unsupported work-zone match type: {match_type!r}")
            if not isinstance(pattern, str):
                raise TypeError("Rule patterns must be strings")
            self._validate_zone(pattern)
            if not isinstance(enabled, bool):
                raise TypeError("Rule enabled flags must be bool values")
            if enabled:
                normalized_rules.append((match_type, pattern.strip()))
        if not normalized_rules:
            raise ValueError("A work-zone profile must have an enabled rule")

        with self._lock:
            self._active_work_zone = profile_name.strip()
            self._work_zone_rules = tuple(normalized_rules)
            self._last_reported_title = None

    def set_suppress_distractions(self, enabled: bool) -> None:
        """Enable or disable minimizing windows outside the active work zone."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        with self._lock:
            self._suppress_distractions = enabled

    def set_focus_guard(
        self,
        enabled: bool,
        executable_path: str,
        blocked_rules: Sequence[tuple[str, str, bool]],
    ) -> None:
        """Configure a reversible guard for one app and optional block rules."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        if not isinstance(executable_path, str):
            raise TypeError("executable_path must be a string")
        if enabled and not executable_path.strip():
            raise ValueError("Choose a work application before enabling the guard")

        normalized_rules: list[tuple[str, str]] = []
        for rule in blocked_rules:
            if not isinstance(rule, tuple) or len(rule) != 3:
                raise ValueError("Each focus-guard rule must have three values")
            match_type, pattern, rule_enabled = rule
            if (
                not isinstance(match_type, str)
                or match_type not in {"title", "process", "path", "accessible"}
            ):
                raise ValueError(f"Unsupported focus-guard match type: {match_type!r}")
            self._validate_zone(pattern)
            if not isinstance(rule_enabled, bool):
                raise TypeError("Rule enabled flags must be bool values")
            if rule_enabled:
                normalized_rules.append((match_type, pattern.strip()))

        with self._lock:
            self._focus_guard_enabled = enabled
            self._focus_app_path = executable_path.strip()
            self._focus_guard_rules = tuple(normalized_rules)
            self._last_reported_title = None

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
    def _same_executable_path(first: str, second: str) -> bool:
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
            os.path.abspath(second)
        )

    @staticmethod
    def _matches_work_zone_rules(
        rules: Sequence[tuple[str, str]],
        title: str,
        process_name: str = "",
        executable_path: str = "",
        accessible_values: tuple[str, ...] = (),
    ) -> bool:
        values_by_type = {
            "title": (title,),
            "process": (process_name,),
            "path": (executable_path,),
            "accessible": accessible_values,
            "any": (title, process_name, executable_path, *accessible_values),
        }
        for match_type, pattern in rules:
            fragment = unquote(pattern).replace("/", "\\").casefold()
            if any(
                fragment in unquote(value).replace("/", "\\").casefold()
                for value in values_by_type[match_type]
                if value
            ):
                return True
        return False

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
            with self._lock:
                manual_break = self._manual_break
                if manual_break:
                    self._last_reported_title = None
            if manual_break:
                self._stop_event.wait(self.check_interval_sec)
                continue

            try:
                window = pywinctl.getActiveWindow()
                if window is not None:
                    self._inspect_window(window)
            except (OSError, RuntimeError, ValueError, pywintypes.error) as error:
                logger.warning("Could not inspect the active window: %s", error)

            self._stop_event.wait(self.check_interval_sec)

    def _inspect_window(self, window: pywinctl.Window) -> None:
        title = window.title or ""
        with self._lock:
            work_zone_rules = self._work_zone_rules
            focus_guard_enabled = self._focus_guard_enabled
            focus_app_path = self._focus_app_path
            focus_guard_rules = self._focus_guard_rules
            manual_break = self._manual_break
        if manual_break:
            return

        pid = window.getPID()

        # Ignore a stale window handle rather than reporting its title.
        if not pid or not psutil.pid_exists(pid):
            return

        process_name = ""
        executable_path = ""
        matching_rules = (
            (*work_zone_rules, *focus_guard_rules)
            if focus_guard_enabled
            else work_zone_rules
        )
        needs_process_name = focus_guard_enabled or any(
            match_type in ("any", "process")
            for match_type, _pattern in matching_rules
        )
        needs_executable_path = focus_guard_enabled or any(
            match_type in ("any", "path")
            for match_type, _pattern in matching_rules
        )
        if needs_process_name or needs_executable_path:
            try:
                process = psutil.Process(pid)
                if needs_process_name:
                    process_name = process.name()
                if needs_executable_path:
                    executable_path = process.exe()
            except psutil.Error as error:
                logger.warning("Could not inspect active process %s: %s", pid, error)

        accessible_values = ()
        if any(
            match_type == "accessible"
            or (
                match_type == "any"
                and self._needs_accessible_match(pattern)
            )
            for match_type, pattern in matching_rules
        ):
            accessible_values = self._get_accessible_window_values(window.getHandle())

        zone_matches = self._matches_work_zone_rules(
            work_zone_rules,
            title,
            process_name,
            executable_path,
            accessible_values,
        )
        guard_matches = (
            self._same_executable_path(executable_path, focus_app_path)
            and not self._matches_work_zone_rules(
                focus_guard_rules,
                title,
                process_name,
                executable_path,
                accessible_values,
            )
        ) if focus_guard_enabled else True
        callback: Callable[[str], None] | None = None
        suppress = False
        on_focus_app_required: Callable[[int | None], None] | None = None
        window_handle: int | None = None
        with self._lock:
            if self._manual_break:
                self._last_reported_title = None
            elif focus_guard_enabled and guard_matches:
                self._last_reported_title = None
            elif not focus_guard_enabled and zone_matches:
                self._last_reported_title = None
            else:
                suppress = focus_guard_enabled or self._suppress_distractions
                if title != self._last_reported_title:
                    self._last_reported_title = title
                    callback = self.on_distraction_detected
                if focus_guard_enabled:
                    on_focus_app_required = self.on_focus_app_required
                    try:
                        window_handle = window.getHandle()
                    except (OSError, RuntimeError, ValueError, pywintypes.error):
                        window_handle = None

        if suppress:
            try:
                window.minimize()
            except (OSError, RuntimeError, ValueError, pywintypes.error) as error:
                logger.warning("Could not minimize active window %r: %s", title, error)
        if on_focus_app_required is not None:
            try:
                on_focus_app_required(window_handle)
            except Exception:
                logger.exception("MindShield could not restore the work application")
        if callback is not None:
            try:
                callback(title)
            except Exception:
                logger.exception("MindShield distraction callback failed")
