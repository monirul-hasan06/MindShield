"""Launch and foreground the user's selected work application."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import psutil
import pywinctl

logger = logging.getLogger(__name__)


class FocusAppController:
    """Find, activate, or launch a selected Windows executable."""

    LAUNCH_COOLDOWN_SECONDS = 8.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_launch_at: dict[str, float] = {}

    @staticmethod
    def validate_executable(executable_path: str | os.PathLike[str]) -> Path:
        """Return a normalized, existing executable path or raise a clear error."""
        if not isinstance(executable_path, (str, os.PathLike)):
            raise TypeError("The work application path must be text")
        path = Path(executable_path).expanduser()
        if path.suffix.casefold() != ".exe":
            raise ValueError("Select a Windows .exe application")
        try:
            path = path.resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError(f"Work application not found: {path}") from error
        if not path.is_file():
            raise FileNotFoundError(f"Work application not found: {path}")
        return path

    @staticmethod
    def _normalized_path(path: str | os.PathLike[str]) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def focus_or_launch(
        self,
        executable_path: str | os.PathLike[str],
        *,
        exclude_handle: int | None = None,
        launch_if_missing: bool = True,
    ) -> str:
        """Activate a matching window, or launch the executable when permitted."""
        path = self.validate_executable(executable_path)
        target_path = self._normalized_path(path)
        for window in pywinctl.getAllWindows():
            try:
                if exclude_handle is not None and window.getHandle() == exclude_handle:
                    continue
                pid = window.getPID()
                if not pid or not psutil.pid_exists(pid):
                    continue
                process_path = psutil.Process(pid).exe()
                if self._normalized_path(process_path) != target_path:
                    continue
                if window.isMinimized:
                    window.restore()
                if window.activate(wait=True):
                    return "activated"
                logger.warning("Windows did not foreground the selected work app")
            except (OSError, RuntimeError, ValueError, psutil.Error) as error:
                logger.debug("Could not inspect a candidate work-app window: %s", error)

        if not launch_if_missing:
            raise RuntimeError("The selected work application is not running")

        now = time.monotonic()
        with self._lock:
            previous_launch = self._last_launch_at.get(target_path, 0.0)
            if now - previous_launch < self.LAUNCH_COOLDOWN_SECONDS:
                return "launch_pending"
            self._last_launch_at[target_path] = now
        subprocess.Popen([str(path)], cwd=str(path.parent), close_fds=True)
        return "launched"
