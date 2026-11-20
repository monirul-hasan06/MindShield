import unittest

from src.gui.monitor_bounds import overlay_geometry


class OverlayPlacementTests(unittest.TestCase):
    def test_overlay_and_toast_use_the_foreground_monitor(self) -> None:
        bounds = (1920, 0, 1600, 900)

        self.assertEqual(overlay_geometry("overlay", bounds), (1920, 0, 1600, 900))
        self.assertEqual(
            overlay_geometry("toast", bounds),
            (1920 + 1600 - 420 - 24, 900 - 280 - 48, 420, 280),
        )


if __name__ == "__main__":
    unittest.main()
