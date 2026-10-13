"""Colour themes shared by every Checkpoint surface: the web app, the native note window and
toasts, the review overlay (Ctrl+F9) and the project switcher (Shift+F9).

"light" is the default Pearl Glass theme; "dark" is the night-blue alternative. The web app mirrors these
values as CSS variables in frontend/src/styles.css, so keep the two in step when changing one.
"""

from __future__ import annotations

from typing import Literal

ThemeName = Literal["dark", "light"]

# Keys match the web app's CSS variables (--bg, --surface, --surface-2, ...), with "_" for "-".
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0b1220",
        "surface": "#111a2e",
        "surface_2": "#17223b",
        "surface_3": "#1e2b48",
        "border": "#24324f",
        "border_strong": "#33456b",
        "text": "#e6ebf5",
        "text_2": "#aab4ca",
        "muted": "#8390ab",
        "faint": "#5f6c88",
        "accent": "#5b8cff",
        "accent_strong": "#7aa2ff",
        "success": "#3ecf8e",
        "warn": "#e8b05a",
        "danger": "#f07171",
        "card": "#121c33",  # overlay and switcher cards
        "pill": "#121c33f0",  # floating pills (header, hints), with alpha
        "selected": "#1f2d52",  # picked row in overlay lists
        "shadow": "#000000",
    },
    "light": {
        "bg": "#e9edf2",
        "surface": "#f7f8fa",
        "surface_2": "#ffffff",
        "surface_3": "#eef1f5",
        "border": "#d7dce5",
        "border_strong": "#bdc5d2",
        "text": "#171d2b",
        "text_2": "#343c4b",
        "muted": "#5f6878",
        "faint": "#8992a0",
        "accent": "#2582ff",
        "accent_strong": "#1468e8",
        "success": "#16a05a",
        "warn": "#c27c0e",
        "danger": "#e5484d",
        "card": "#f7f9fc",
        "pill": "#ffffffed",
        "selected": "#dde9ff",
        "shadow": "#141821",
    },
}


def current() -> str:
    from .settings_store import get_settings

    name = get_settings().appearance.theme
    return name if name in PALETTES else "light"


def palette(name: str | None = None) -> dict[str, str]:
    """Colour hex strings for a theme (the current one by default). #rrggbbaa carries alpha."""
    return PALETTES.get(name or current(), PALETTES["light"])


def is_dark(name: str | None = None) -> bool:
    return (name or current()) == "dark"


def rgba(value: str) -> tuple[int, int, int, int]:
    """'#rrggbb' or '#rrggbbaa' to (r, g, b, a). Note Qt reads 8-digit hex as #aarrggbb, so convert with this."""
    h = value.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return r, g, b, a
