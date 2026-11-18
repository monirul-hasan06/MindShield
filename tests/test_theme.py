import unittest

from src.gui.theme import COLORS


def _relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    first, second = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (first + 0.05) / (second + 0.05)


class LightThemeTests(unittest.TestCase):
    def test_primary_text_and_status_colors_are_readable_on_light_surfaces(self) -> None:
        combinations = (
            ("text_primary", "app_background"),
            ("text_secondary", "app_background"),
            ("text_muted", "app_background"),
            ("text_primary", "card_background"),
            ("text_secondary", "card_background"),
            ("text_muted", "card_background"),
            ("highlight", "card_background"),
            ("success", "card_background"),
            ("text_primary", "overlay_background"),
            ("text_secondary", "overlay_background"),
            ("highlight", "overlay_background"),
        )

        for foreground_key, background_key in combinations:
            with self.subTest(foreground=foreground_key, background=background_key):
                foreground = COLORS[foreground_key][0]
                background = COLORS[background_key][0]
                self.assertGreaterEqual(
                    _contrast_ratio(foreground, background),
                    4.5,
                )

    def test_interactive_control_text_has_accessible_contrast_in_both_themes(self) -> None:
        for appearance_index in range(2):
            with self.subTest(appearance_index=appearance_index):
                for foreground_key, background_key in (
                    ("text_primary", "control"),
                    ("text_primary", "control_hover"),
                    ("text_secondary", "control"),
                    ("text_primary", "segment_background"),
                    ("text_primary", "segment_selected"),
                    ("text_primary", "segment_selected_hover"),
                    ("text_on_accent", "accent"),
                    ("text_on_accent", "accent_hover"),
                ):
                    self.assertGreaterEqual(
                        _contrast_ratio(
                            COLORS[foreground_key][appearance_index],
                            COLORS[background_key][appearance_index],
                        ),
                        4.5,
                    )


if __name__ == "__main__":
    unittest.main()
