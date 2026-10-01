"""Shared application icon asset location."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


_LOGO_RELATIVE_PATH = Path("assets") / "mindshield-logo.png"
_ICON_MARK_BOUNDS = (4, 3, 36, 35)


def _resource_root() -> Path:
    return Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
    )


def get_app_icon_path() -> Path:
    """Return the bundled square MindShield icon path."""
    return _resource_root() / "assets" / "mindshield.ico"


def get_app_logo_path() -> Path:
    """Return the bundled MindShield wordmark path."""
    return _resource_root() / _LOGO_RELATIVE_PATH


def get_app_icon_image(size: tuple[int, int]) -> Image.Image:
    """Return the square M mark from the supplied wordmark at the requested size."""
    with Image.open(get_app_logo_path()) as logo:
        mark = logo.crop(_ICON_MARK_BOUNDS).convert("RGBA")
    return mark.resize(size, Image.Resampling.LANCZOS)
