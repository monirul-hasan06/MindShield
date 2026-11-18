"""Eye-care break overlay window."""

from __future__ import annotations

import math
import time
import tkinter as tk

import customtkinter as ctk

from src.gui.theme import COLORS


class OverlayWindow(ctk.CTkToplevel):
    """Frameless, translucent reminder shown during a break."""

    MESSAGE = (
        "Eye-Care Break! Look at an object 20 feet away for 20 seconds."
    )
    PROMPTS = {
        "eye_care": MESSAGE,
        "stretch": "Stand up, stretch gently, and relax your shoulders.",
        "hydrate": "Take a few sips of water and enjoy your break.",
        "none": "",
    }

    def __init__(
        self,
        master: tk.Misc,
        break_duration_sec: int = 20,
        mode: str = "overlay",
        message: str = MESSAGE,
    ) -> None:
        if isinstance(break_duration_sec, bool) or not isinstance(break_duration_sec, int):
            raise TypeError("break_duration_sec must be an integer")
        if break_duration_sec <= 0:
            raise ValueError("break_duration_sec must be greater than zero")
        if mode not in ("overlay", "toast"):
            raise ValueError("mode must be 'overlay' or 'toast'")
        if not isinstance(message, str):
            raise TypeError("message must be a string")

        super().__init__(master)
        self._remaining_seconds = break_duration_sec
        self._deadline = time.monotonic() + break_duration_sec
        self._after_id: str | None = None
        self._fade_after_id: str | None = None
        self._target_alpha = 0.84 if mode == "overlay" else 0.94
        self._fading_out = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self.configure(fg_color=COLORS["overlay_background"])

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
        self.message_label = ctk.CTkLabel(
            content,
            text=message,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text_primary"],
            wraplength=420,
        )
        self.message_label.pack(padx=24, pady=(24, 12))

        self.countdown_label = ctk.CTkLabel(
            content,
            text="",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color=COLORS["highlight"],
        )
        self.countdown_label.pack(pady=(0, 24))

        self._fade_in()
        self._update_countdown()

    def _fade_in(self, step: int = 0) -> None:
        self.attributes("-alpha", self._target_alpha * step / 8)
        if step >= 8:
            self._fade_after_id = None
            return
        self._fade_after_id = self.after(20, self._fade_in, step + 1)

    def _fade_out(self, step: int = 0) -> None:
        self.attributes("-alpha", self._target_alpha * (1 - step / 8))
        if step >= 8:
            self._fade_after_id = None
            self.destroy()
            return
        self._fade_after_id = self.after(20, self._fade_out, step + 1)

    def _update_countdown(self) -> None:
        remaining = max(0, math.ceil(self._deadline - time.monotonic()))
        self._remaining_seconds = remaining
        unit = "second" if remaining == 1 else "seconds"
        self.countdown_label.configure(text=f"{remaining} {unit} remaining")

        if remaining == 0:
            self._after_id = None
            if not self._fading_out:
                self._fading_out = True
                self._fade_out()
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
        if self._fade_after_id is not None:
            try:
                self.after_cancel(self._fade_after_id)
            except tk.TclError:
                pass
            self._fade_after_id = None
        super().destroy()
