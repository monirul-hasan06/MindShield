"""Eye-care break overlay window."""

from __future__ import annotations

import math
import time
import tkinter as tk

import customtkinter as ctk
from PIL import Image

from src.core.app_icon import get_app_logo_path
from src.gui.typography import (
    FONT_FAMILY,
    get_display_font_family,
)


class OverlayWindow(ctk.CTkToplevel):
    """Frameless, translucent reminder shown during an eye-care break."""

    MESSAGE = (
        "Eye-Care Break! Look at an object 20 feet away for 20 seconds."
    )

    def __init__(
        self,
        master: tk.Misc,
        break_duration_sec: int = 20,
        mode: str = "overlay",
    ) -> None:
        if isinstance(break_duration_sec, bool) or not isinstance(break_duration_sec, int):
            raise TypeError("break_duration_sec must be an integer")
        if break_duration_sec <= 0:
            raise ValueError("break_duration_sec must be greater than zero")
        if mode not in ("overlay", "toast"):
            raise ValueError("mode must be 'overlay' or 'toast'")

        super().__init__(master)
        self._remaining_seconds = break_duration_sec
        self._deadline = time.monotonic() + break_duration_sec
        self._after_id: str | None = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.84 if mode == "overlay" else 0.94)
        self.configure(fg_color="#14213d")

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        if mode == "overlay":
            width, height = screen_width, screen_height
            x = y = 0
        else:
            width, height = min(420, screen_width), min(190, screen_height)
            x = max(0, screen_width - width - 24)
            y = max(0, screen_height - height - 48)
        self.geometry(f"{width}x{height}+{x}+{y}")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.place(relx=0.5, rely=0.5, anchor="center")
        with Image.open(get_app_logo_path()) as logo_file:
            logo = logo_file.convert("RGBA")
        self._logo_image = ctk.CTkImage(
            light_image=logo,
            dark_image=logo,
            size=logo.size,
        )
        ctk.CTkLabel(
            content,
            image=self._logo_image,
            text="",
        ).pack(pady=(0, 12))
        self.message_label = ctk.CTkLabel(
            content,
            text=self.MESSAGE,
            font=ctk.CTkFont(
                family=FONT_FAMILY,
                size=20,
                weight="bold",
            ),
            text_color="#ffffff",
            wraplength=420,
        )
        self.message_label.pack(padx=24, pady=(24, 12))

        self.countdown_label = ctk.CTkLabel(
            content,
            text="",
            font=ctk.CTkFont(
                family=get_display_font_family(),
                size=32,
                weight="bold",
            ),
            text_color="#38bdf8",
        )
        self.countdown_label.pack(pady=(0, 24))

        self._update_countdown()

    def _update_countdown(self) -> None:
        remaining = max(0, math.ceil(self._deadline - time.monotonic()))
        self._remaining_seconds = remaining
        unit = "second" if remaining == 1 else "seconds"
        self.countdown_label.configure(text=f"{remaining} {unit} remaining")

        if remaining == 0:
            self.destroy()
            return

        self._after_id = self.after(200, self._update_countdown)

    def destroy(self) -> None:
        """Cancel the pending countdown callback before destroying the window."""
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        super().destroy()
