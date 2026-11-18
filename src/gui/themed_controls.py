"""Theme-aware constructors for interactive controls."""

from typing import Any

import customtkinter as ctk

from src.gui.theme import COLORS


def themed_button(master: Any, **kwargs: Any) -> ctk.CTkButton:
    foreground = kwargs.get("fg_color", COLORS["accent"])
    text_color = (
        COLORS["text_on_accent"]
        if foreground == COLORS["accent"]
        else COLORS["text_primary"]
    )
    kwargs.setdefault("text_color", text_color)
    kwargs.setdefault("text_color_disabled", COLORS["text_secondary"])
    return ctk.CTkButton(master, **kwargs)


def themed_option_menu(master: Any, **kwargs: Any) -> ctk.CTkOptionMenu:
    kwargs.setdefault("text_color", COLORS["text_primary"])
    kwargs.setdefault("dropdown_fg_color", COLORS["card_background"])
    kwargs.setdefault("dropdown_text_color", COLORS["text_primary"])
    kwargs.setdefault("dropdown_hover_color", COLORS["control_hover"])
    kwargs.setdefault("text_color_disabled", COLORS["text_secondary"])
    return ctk.CTkOptionMenu(master, **kwargs)
