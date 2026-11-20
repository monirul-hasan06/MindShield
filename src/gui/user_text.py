"""Greeting text and text-entry detection shared by MindShield views."""

from __future__ import annotations

from datetime import datetime, timedelta
import tkinter as tk

import customtkinter as ctk

def get_time_of_day_greeting(hour: int) -> str:
    """Return a greeting for a local 24-hour clock value."""
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("hour must be an integer from 0 to 23")
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def get_next_greeting_change(now: datetime) -> datetime:
    """Return the next local midnight, noon, or 17:00 greeting boundary."""
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.hour < 12:
        return now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now.hour < 17:
        return now.replace(hour=17, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def widget_is_text_entry(widget: tk.Misc | None) -> bool:
    """Return whether a widget or one of its parents accepts typed text."""
    current = widget
    while current is not None:
        if isinstance(
            current,
            (ctk.CTkEntry, ctk.CTkTextbox, tk.Entry, tk.Text),
        ):
            return True
        current = getattr(current, "master", None)
    return False


def normalize_user_name(value: str) -> str:
    """Trim and require the locally stored profile name."""
    if not isinstance(value, str):
        raise TypeError("name must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Enter your name to continue.")
    return normalized

