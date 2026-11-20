"""Load saved preferences, falling back when a stored value is invalid."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.database.db_handler import DatabaseHandler
from src.gui.overlay_window import OverlayWindow
from src.gui.theme import APPEARANCE_MODES, TEXT_SIZE_SCALING


@dataclass
class AppSettings:
    """Preferences used to open the main window."""

    work_interval_minutes: int = 20
    break_duration_seconds: int = 20
    daily_goal_minutes: int = 120
    focus_app_path: str = ""
    focus_guard_enabled: bool = False
    meeting_mode_minutes: int = 60
    break_overlay_mode: str = "overlay"
    break_prompt_mode: str = "eye_care"
    user_name: str = ""
    show_first_run_onboarding: bool = True
    demo_mode: bool = False
    suppress_distractions: bool = False
    appearance_mode: str = "dark"
    text_size: str = "Default"
    reduced_motion: bool = False
    warnings: list[str] = field(default_factory=list)


def load_app_settings(database: DatabaseHandler) -> AppSettings:
    """Read settings and replace invalid values with the documented defaults."""
    settings = AppSettings()
    warnings: list[str] = []

    settings.work_interval_minutes = _positive_int(
        database, "work_interval_minutes", 20, warnings
    )
    settings.break_duration_seconds = _positive_int(
        database, "break_duration_seconds", 20, warnings
    )
    settings.daily_goal_minutes = _positive_int(
        database, "daily_goal_minutes", 120, warnings
    )
    settings.meeting_mode_minutes = _positive_int(
        database,
        "meeting_mode_minutes",
        60,
        warnings,
        allowed=frozenset({30, 60, 120}),
    )
    settings.focus_app_path = _text(database, "focus_app_path", "", warnings)
    settings.focus_guard_enabled = _bool_setting(
        database, "focus_guard_enabled", False, warnings
    )
    settings.break_overlay_mode = _choice(
        database,
        "break_overlay_mode",
        frozenset({"overlay", "toast"}),
        "overlay",
        warnings,
    )
    settings.break_prompt_mode = _choice(
        database,
        "break_prompt",
        frozenset(OverlayWindow.PROMPTS),
        "eye_care",
        warnings,
    )
    settings.user_name = _text(database, "user_name", "", warnings).strip()
    onboarding_completed = _choice(
        database,
        "onboarding_completed",
        frozenset({"true", "false"}),
        "false",
        warnings,
    )
    settings.show_first_run_onboarding = (
        onboarding_completed != "true" or not settings.user_name
    )
    settings.demo_mode = _bool_setting(database, "demo_mode", False, warnings)
    settings.suppress_distractions = _bool_setting(
        database, "suppress_distractions", False, warnings
    )
    settings.appearance_mode = _choice(
        database,
        "appearance_mode",
        frozenset(APPEARANCE_MODES.values()),
        "dark",
        warnings,
    )
    settings.text_size = _choice(
        database,
        "text_size",
        frozenset(TEXT_SIZE_SCALING),
        "Default",
        warnings,
    )
    settings.reduced_motion = _bool_setting(
        database, "reduced_motion", False, warnings
    )
    settings.warnings = warnings
    return settings


def _read(database: DatabaseHandler, key: str, warnings: list[str]) -> str | None:
    try:
        return database.get_setting(key)
    except (TypeError, ValueError) as error:
        warnings.append(f"Saved setting {key!r} could not be read and was reset: {error}")
        return None


def _positive_int(
    database: DatabaseHandler,
    key: str,
    default: int,
    warnings: list[str],
    allowed: frozenset[int] | None = None,
) -> int:
    saved = _read(database, key, warnings)
    if saved is None:
        return default
    try:
        value = int(saved)
    except ValueError:
        warnings.append(
            f"Saved setting {key!r} was not a whole number and was reset to {default}."
        )
        return default
    if value <= 0 or (allowed is not None and value not in allowed):
        warnings.append(
            f"Saved setting {key!r} was invalid and was reset to {default}."
        )
        return default
    return value


def _choice(
    database: DatabaseHandler,
    key: str,
    allowed: frozenset[str],
    default: str,
    warnings: list[str],
) -> str:
    saved = _read(database, key, warnings)
    if saved is None:
        return default
    if saved not in allowed:
        warnings.append(
            f"Saved setting {key!r} was invalid and was reset to {default}."
        )
        return default
    return saved


def _text(
    database: DatabaseHandler,
    key: str,
    default: str,
    warnings: list[str],
) -> str:
    saved = _read(database, key, warnings)
    if saved is None:
        return default
    return saved


def _bool_setting(
    database: DatabaseHandler,
    key: str,
    default: bool,
    warnings: list[str],
) -> bool:
    saved = _choice(
        database,
        key,
        frozenset({"true", "false"}),
        "true" if default else "false",
        warnings,
    )
    return saved == "true"
