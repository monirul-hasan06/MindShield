"""Semantic color tokens for light- and dark-appearance MindShield views."""

from __future__ import annotations

from typing import Final

ColorPair = tuple[str, str]

COLORS: Final[dict[str, ColorPair]] = {
    "app_background": ("#f4f7fb", "#0b1220"),
    "tab_background": ("#e9eff7", "#111b2e"),
    "card_background": ("#ffffff", "#17243a"),
    "overlay_background": ("#e0f2fe", "#14213d"),
    "segment_background": ("#dbe5f1", "#1e293b"),
    "segment_selected": ("#bfdbfe", "#0369a1"),
    "segment_selected_hover": ("#93c5fd", "#075985"),
    "accent": ("#0369a1", "#0369a1"),
    "accent_hover": ("#075985", "#075985"),
    "control": ("#e2e8f0", "#334155"),
    "control_hover": ("#cbd5e1", "#475569"),
    "highlight": ("#0369a1", "#38bdf8"),
    "success": ("#047857", "#34d399"),
    "text_on_accent": ("#ffffff", "#ffffff"),
    "text_primary": ("#0f172a", "#e2e8f0"),
    "text_emphasis": ("#0b1220", "#f8fafc"),
    "text_secondary": ("#334155", "#cbd5e1"),
    "text_muted": ("#475569", "#94a3b8"),
}

APPEARANCE_MODES: Final[dict[str, str]] = {
    "Dark": "dark",
    "Light": "light",
    "System": "system",
}

TEXT_SIZE_SCALING: Final[dict[str, float]] = {
    "Small": 0.9,
    "Default": 1.0,
    "Large": 1.15,
}
