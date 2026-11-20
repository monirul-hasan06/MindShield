"""Place a window on the monitor that owns the foreground window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

MONITOR_DEFAULTTONEAREST = 2


class _Rect(ctypes.Structure):
    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


class _MonitorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", wintypes.DWORD),
    )


def foreground_monitor_bounds() -> tuple[int, int, int, int]:
    """Return ``(x, y, width, height)`` for the monitor of the active window."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        rect = info.rcMonitor
        return (
            int(rect.left),
            int(rect.top),
            int(rect.right - rect.left),
            int(rect.bottom - rect.top),
        )
    return (0, 0, int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))


def overlay_geometry(
    mode: str,
    bounds: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Return ``(x, y, width, height)`` for a full-screen overlay or corner toast."""
    origin_x, origin_y, screen_width, screen_height = bounds
    if mode == "overlay":
        return origin_x, origin_y, screen_width, screen_height
    width = min(420, screen_width)
    height = min(280, screen_height)
    x = origin_x + max(0, screen_width - width - 24)
    y = origin_y + max(0, screen_height - height - 48)
    return x, y, width, height
