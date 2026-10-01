"""Typography defaults shared by the MindShield desktop UI."""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import sys
from typing import Any

import customtkinter as ctk

FONT_FAMILY = "Segoe UI"
MONOSPACED_FONT_FAMILY = "Cascadia Mono"
DISPLAY_FONT_FAMILY = "Orbitron"
_DISPLAY_FONT_PATH = Path("assets") / "fonts" / "Orbitron-Bold.ttf"
_registered_display_font: str | None = None
logger = logging.getLogger(__name__)


def get_display_font_path() -> Path:
    """Resolve the bundled Orbitron face in source and frozen builds."""
    resource_root = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
    )
    return resource_root / _DISPLAY_FONT_PATH


def register_display_font() -> bool:
    """Register bundled Orbitron privately for this Windows process."""
    global _registered_display_font
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return False

    font_path = get_display_font_path()
    if not font_path.is_file():
        logger.warning("Bundled Orbitron font is missing: %s", font_path)
        return False
    resolved_path = str(font_path.resolve())
    if _registered_display_font == resolved_path:
        return True

    try:
        gdi32: Any = win_dll("gdi32", use_last_error=True)
        add_font_resource = gdi32.AddFontResourceExW
        add_font_resource.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_void_p,
        )
        add_font_resource.restype = ctypes.c_int
        if add_font_resource(resolved_path, 0x10, None) == 0:
            error_code = ctypes.get_last_error()
            logger.warning(
                "Could not register bundled Orbitron font (Windows error %s).",
                error_code,
            )
            return False
    except (AttributeError, OSError) as error:
        logger.warning("Could not register bundled Orbitron font: %s", error)
        return False

    _registered_display_font = resolved_path
    return True


def get_display_font_family() -> str:
    """Use Orbitron when registered, otherwise retain the native UI font."""
    if _registered_display_font is not None:
        return DISPLAY_FONT_FAMILY
    return FONT_FAMILY


def unregister_display_font() -> None:
    """Release the process-private Orbitron font after the UI is destroyed."""
    global _registered_display_font
    if _registered_display_font is None:
        return

    try:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            return
        gdi32: Any = win_dll("gdi32", use_last_error=True)
        remove_font_resource = gdi32.RemoveFontResourceExW
        remove_font_resource.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_void_p,
        )
        remove_font_resource.restype = ctypes.c_bool
        if not remove_font_resource(_registered_display_font, 0x10, None):
            logger.warning(
                "Could not release bundled Orbitron font (Windows error %s).",
                ctypes.get_last_error(),
            )
    except (AttributeError, OSError) as error:
        logger.warning("Could not release bundled Orbitron font: %s", error)
    finally:
        _registered_display_font = None


def configure_default_font() -> None:
    """Set the default family used by new CustomTkinter widgets."""
    ctk.ThemeManager.theme["CTkFont"]["family"] = FONT_FAMILY
