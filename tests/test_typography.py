from __future__ import annotations

import unittest
from unittest.mock import patch

from src.gui.typography import (
    DISPLAY_FONT_FAMILY,
    FONT_FAMILY,
    get_display_font_family,
    get_display_font_path,
)


class TypographyTests(unittest.TestCase):
    def test_bundled_orbitron_font_and_license_are_present(self) -> None:
        font_path = get_display_font_path()

        self.assertTrue(font_path.is_file())
        self.assertGreater(font_path.stat().st_size, 0)
        self.assertTrue(font_path.with_name("OFL.txt").is_file())

    def test_display_font_uses_orbitron_when_registered(self) -> None:
        with patch("src.gui.typography._registered_display_font", "orbitron.ttf"):
            self.assertEqual(get_display_font_family(), DISPLAY_FONT_FAMILY)

    def test_display_font_falls_back_to_native_font_when_unavailable(self) -> None:
        with patch("src.gui.typography._registered_display_font", None):
            self.assertEqual(get_display_font_family(), FONT_FAMILY)


if __name__ == "__main__":
    unittest.main()
