"""CustomTkinter scroll frames with touch-style drag scrolling."""

from __future__ import annotations

import sys
import time
import tkinter as tk
import weakref

import customtkinter as ctk

from src.gui.theme import COLORS


class _SwipeGestureManager:
    """Route pointer drags to the nearest scrollable frame."""

    SCROLL_ANIMATION_DURATION_SEC = 0.18

    INTERACTIVE_WIDGET_TYPES = (
        ctk.CTkButton,
        ctk.CTkCheckBox,
        ctk.CTkEntry,
        ctk.CTkOptionMenu,
        ctk.CTkScrollbar,
        ctk.CTkSlider,
        ctk.CTkSwitch,
        ctk.CTkTextbox,
    )

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self._active_frame: SwipeScrollableFrame | None = None
        self._scroll_targets: weakref.WeakKeyDictionary[
            SwipeScrollableFrame, float
        ] = weakref.WeakKeyDictionary()
        self._scroll_after_ids: weakref.WeakKeyDictionary[
            SwipeScrollableFrame, str
        ] = weakref.WeakKeyDictionary()
        self._scroll_animation_origins: weakref.WeakKeyDictionary[
            SwipeScrollableFrame, tuple[float, float]
        ] = weakref.WeakKeyDictionary()
        root.bind_all("<ButtonPress-1>", self._on_press, add="+")
        root.bind_all("<B1-Motion>", self._on_drag, add="+")
        root.bind_all("<ButtonRelease-1>", self._on_release, add="+")
        for key in ("Prior", "Next", "Home", "End"):
            root.bind_all(f"<{key}>", self._on_keyboard_scroll, add="+")

    def _on_press(self, event: tk.Event) -> None:
        if self._is_interactive_widget(event.widget):
            return

        frame = self._find_scrollable_frame(event.widget)
        if frame is None:
            return

        first, last = frame._parent_canvas.yview()
        if last - first >= 0.999:
            return

        self._cancel_scroll_animation(frame)
        canvas = frame._parent_canvas
        canvas.scan_mark(
            event.x_root - canvas.winfo_rootx(),
            event.y_root - canvas.winfo_rooty(),
        )
        self._active_frame = frame

    @classmethod
    def _is_interactive_widget(cls, widget: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if isinstance(current, cls.INTERACTIVE_WIDGET_TYPES):
                return True
            if isinstance(current, SwipeScrollableFrame):
                return False
            current = getattr(current, "master", None)
        return False

    def _on_drag(self, event: tk.Event) -> None:
        frame = self._active_frame
        if frame is None:
            return

        canvas = frame._parent_canvas
        canvas.scan_dragto(
            event.x_root - canvas.winfo_rootx(),
            event.y_root - canvas.winfo_rooty(),
            gain=1,
        )

    def _on_release(self, _event: tk.Event) -> None:
        self._active_frame = None

    def _on_keyboard_scroll(self, event: tk.Event) -> str | None:
        current: tk.Misc | None = event.widget
        while current is not None:
            if isinstance(
                current,
                (ctk.CTkEntry, ctk.CTkTextbox, tk.Entry, tk.Text),
            ):
                return None
            current = getattr(current, "master", None)

        for frame in self._find_scrollable_frames(event.widget):
            canvas = frame._parent_canvas
            first, last = canvas.yview()
            target = self._scroll_targets.get(frame, first)
            if event.keysym == "Prior":
                target -= last - first
            elif event.keysym == "Next":
                target += last - first
            elif event.keysym == "Home":
                target = 0.0
            elif event.keysym == "End":
                target = 1.0 - (last - first)
            if self._animate_to(frame, target):
                return "break"
        return None

    def _on_wheel(self, event: tk.Event) -> None:
        if sys.platform.startswith("win"):
            pixel_delta = -event.delta / 120 * 90
        elif sys.platform == "darwin":
            pixel_delta = -event.delta * 12
        else:
            pixel_delta = -90 if event.num == 4 else 90

        for frame in self._find_scrollable_frames(event.widget):
            canvas = frame._parent_canvas
            bounds = canvas.bbox("all")
            if bounds is None:
                continue
            scroll_range = bounds[3] - bounds[1] - canvas.winfo_height()
            if scroll_range <= 0:
                continue
            first, last = canvas.yview()
            target = self._scroll_targets.get(frame, first)
            target += pixel_delta / scroll_range
            if self._animate_to(frame, target):
                return

    def _animate_to(self, frame: SwipeScrollableFrame, target: float) -> bool:
        canvas = frame._parent_canvas
        first, last = canvas.yview()
        maximum = max(0.0, 1.0 - (last - first))
        target = min(maximum, max(0.0, target))
        current_target = self._scroll_targets.get(frame, first)
        if abs(target - current_target) < 0.000001:
            return False

        self._scroll_targets[frame] = target
        if frame not in self._scroll_after_ids:
            self._scroll_animation_origins[frame] = (first, time.monotonic())
            self._scroll_step(frame)
        return True

    def _cancel_scroll_animation(self, frame: SwipeScrollableFrame) -> None:
        after_id = self._scroll_after_ids.pop(frame, None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._scroll_targets.pop(frame, None)
        self._scroll_animation_origins.pop(frame, None)

    def _scroll_step(self, frame: SwipeScrollableFrame) -> None:
        self._scroll_after_ids.pop(frame, None)
        if not frame.winfo_exists():
            self._scroll_targets.pop(frame, None)
            self._scroll_animation_origins.pop(frame, None)
            return

        canvas = frame._parent_canvas
        first, last = canvas.yview()
        target = self._scroll_targets.get(frame, first)
        maximum = max(0.0, 1.0 - (last - first))
        target = min(maximum, max(0.0, target))
        origin = self._scroll_animation_origins.get(frame)
        if origin is None:
            origin = (first, time.monotonic())
            self._scroll_animation_origins[frame] = origin

        start, started_at = origin
        progress = min(
            1.0,
            max(
                0.0,
                (time.monotonic() - started_at)
                / self.SCROLL_ANIMATION_DURATION_SEC,
            ),
        )
        eased_progress = 1.0 - (1.0 - progress) ** 3
        position = start + (target - start) * eased_progress
        canvas.yview_moveto(position)
        if progress >= 1.0:
            canvas.yview_moveto(target)
            self._scroll_targets.pop(frame, None)
            self._scroll_animation_origins.pop(frame, None)
            return

        after_id = self.root.after(16, self._scroll_step, frame)
        self._scroll_after_ids[frame] = after_id

    @staticmethod
    def _find_scrollable_frame(widget: tk.Misc) -> SwipeScrollableFrame | None:
        current: tk.Misc | None = widget
        while current is not None:
            if isinstance(current, SwipeScrollableFrame):
                return current
            current = getattr(current, "master", None)
        return None

    @staticmethod
    def _find_scrollable_frames(widget: tk.Misc) -> list[SwipeScrollableFrame]:
        frames: list[SwipeScrollableFrame] = []
        current: tk.Misc | None = widget
        while current is not None:
            if isinstance(current, SwipeScrollableFrame):
                frames.append(current)
            current = getattr(current, "master", None)
        return frames


class SwipeScrollableFrame(ctk.CTkScrollableFrame):
    """A vertical frame supporting mouse wheel, touchpad, and drag scrolling."""

    _managers: weakref.WeakKeyDictionary[tk.Misc, _SwipeGestureManager] = (
        weakref.WeakKeyDictionary()
    )

    def __init__(self, master: tk.Misc, **kwargs: object) -> None:
        kwargs.setdefault("scrollbar_fg_color", "transparent")
        kwargs.setdefault("scrollbar_button_color", COLORS["control_hover"])
        kwargs.setdefault("scrollbar_button_hover_color", COLORS["accent"])
        super().__init__(master, **kwargs)
        root = self.winfo_toplevel()
        manager = self._managers.get(root)
        if manager is None:
            manager = _SwipeGestureManager(root)
            self._managers[root] = manager
        self._gesture_manager = manager

    def _mouse_wheel_all(self, event: tk.Event) -> None:
        if self._check_if_valid_scroll(event.widget):
            self._gesture_manager._on_wheel(event)
