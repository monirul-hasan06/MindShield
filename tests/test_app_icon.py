import unittest

from PIL import Image, ImageChops, ImageStat
from PIL.IcoImagePlugin import IcoImageFile

from src.core.app_icon import get_app_icon_image, get_app_icon_path, get_app_logo_path
from src.core.tray_manager import TrayManager


class AppIconTests(unittest.TestCase):
    def test_tray_icon_uses_the_shared_square_application_icon(self) -> None:
        expected = get_app_icon_image((64, 64))

        actual = TrayManager._create_icon_image()

        self.assertEqual(actual.size, (64, 64))
        self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_windows_icon_uses_the_wordmark_m_mark(self) -> None:
        with Image.open(get_app_logo_path()) as logo:
            expected = logo.crop((4, 3, 36, 35)).convert("RGBA").resize(
                (64, 64),
                Image.Resampling.LANCZOS,
            )
        with Image.open(get_app_icon_path()) as icon:
            assert isinstance(icon, IcoImageFile)
            actual = icon.ico.getimage((64, 64)).copy()

        background = Image.new("RGBA", (64, 64), (11, 18, 32, 255))
        channel_ranges = ImageStat.Stat(
            ImageChops.difference(
                Image.alpha_composite(background, actual).convert("RGB"),
                Image.alpha_composite(background, expected).convert("RGB"),
            )
        ).extrema
        self.assertLessEqual(max(maximum for _, maximum in channel_ranges), 5)


if __name__ == "__main__":
    unittest.main()
