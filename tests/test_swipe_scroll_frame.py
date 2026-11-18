from collections.abc import Callable
import unittest
import weakref
from unittest.mock import patch

from src.gui.swipe_scroll_frame import _SwipeGestureManager


class _FakeCanvas:
    def __init__(self) -> None:
        self.position = 0.0
        self.visible = 0.25

    def yview(self) -> tuple[float, float]:
        return self.position, self.position + self.visible

    def yview_moveto(self, position: float) -> None:
        self.position = min(
            max(0.0, position),
            1.0 - self.visible,
        )


class _FakeFrame:
    def __init__(self) -> None:
        self._parent_canvas = _FakeCanvas()

    def winfo_exists(self) -> bool:
        return True


class _FakeRoot:
    def __init__(self) -> None:
        self.callbacks: list[
            tuple[Callable[..., None], tuple[object, ...]]
        ] = []

    def after(
        self,
        _delay: int,
        callback: Callable[..., None],
        *args: object,
    ) -> str:
        self.callbacks.append((callback, args))
        return f"after-{len(self.callbacks)}"

    def run_next(self) -> None:
        callback, args = self.callbacks.pop(0)
        callback(*args)


class SwipeScrollAnimationTests(unittest.TestCase):
    def _manager(self) -> _SwipeGestureManager:
        manager = object.__new__(_SwipeGestureManager)
        manager.root = _FakeRoot()
        manager._scroll_targets = weakref.WeakKeyDictionary()
        manager._scroll_after_ids = weakref.WeakKeyDictionary()
        manager._scroll_animation_origins = weakref.WeakKeyDictionary()
        return manager

    def test_scroll_animation_eases_to_a_clamped_target_without_a_final_jump(self) -> None:
        manager = self._manager()
        frame = _FakeFrame()
        now = [0.0]

        with patch("src.gui.swipe_scroll_frame.time.monotonic", side_effect=lambda: now[0]):
            self.assertTrue(manager._animate_to(frame, 2.0))
            self.assertEqual(manager._scroll_targets[frame], 0.75)
            self.assertEqual(frame._parent_canvas.position, 0.0)

            now[0] = manager.SCROLL_ANIMATION_DURATION_SEC / 2
            manager.root.run_next()
            middle_position = frame._parent_canvas.position
            self.assertGreater(middle_position, 0.0)
            self.assertLess(middle_position, 0.75)

            now[0] = manager.SCROLL_ANIMATION_DURATION_SEC
            manager.root.run_next()

        self.assertAlmostEqual(frame._parent_canvas.position, 0.75)
        self.assertNotIn(frame, manager._scroll_targets)
        self.assertNotIn(frame, manager._scroll_animation_origins)
        self.assertEqual(manager.root.callbacks, [])


if __name__ == "__main__":
    unittest.main()
