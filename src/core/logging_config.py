"""User-level rotating log for packaged and source MindShield runs."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_directory() -> Path:
    """Return the per-user folder that stores MindShield's log."""
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    return local_app_data / "MindShield"


def configure_logging(directory: Path | None = None) -> Path:
    """Write warnings and errors to a rotating log file and return its path."""
    folder = directory or log_directory()
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / "mindshield.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(
        isinstance(existing, RotatingFileHandler)
        and Path(getattr(existing, "baseFilename", "")) == log_path
        for existing in root.handlers
    ):
        root.addHandler(handler)
    return log_path
