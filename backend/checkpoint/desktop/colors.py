"""QColors and stylesheet colours for the native windows, from the current theme (theme.py).

Read them when a window is shown or painted, never at import time, so switching the theme in
Settings applies the next time a window opens or repaints.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QColor

from .. import theme


def qcolor(key: str, name: Optional[str] = None) -> QColor:
    """A theme colour as a QColor. Goes through theme.rgba because Qt reads '#xxxxxxxx' as #aarrggbb."""
    return QColor(*theme.rgba(theme.palette(name)[key]))


def alpha(c: QColor, a: int) -> QColor:
    out = QColor(c)
    out.setAlpha(a)
    return out


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def css(c: QColor) -> str:
    """A colour for a Qt stylesheet."""
    if c.alpha() == 255:
        return c.name()
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha() / 255:.3f})"


def arrow_icon(c: QColor) -> str:
    """A small down arrow in colour c, as a file a stylesheet can point at (stylesheets can't draw one)."""
    path = Path(tempfile.gettempdir()) / f"checkpoint-arrow-{c.name()[1:]}.svg"
    if not path.exists():
        path.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'>"
                        f"<path d='M1 1l4 4 4-4' fill='none' stroke='{c.name()}' stroke-width='1.6' stroke-linecap='round'"
                        f" stroke-linejoin='round'/></svg>", encoding="utf-8")
    return path.as_posix()


class Colors:
    """One theme's colours as QColors: every palette key (c.text, c.card, ...) plus a few derived shades."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.dark = theme.is_dark(name)
        for key, value in theme.palette(name).items():
            setattr(self, key, QColor(*theme.rgba(value)))
        white = QColor(255, 255, 255)
        ink = white if self.dark else QColor(20, 24, 33)  # what quiet lines and fills are made of
        self.line = alpha(ink, 22)  # hairlines on a card
        self.soft = alpha(ink, 16 if self.dark else 14)  # quiet fills: the empty counter, unpicked buttons
        self.softer = alpha(ink, 12 if self.dark else 13)  # the search box
        self.rim = alpha(white, 24 if self.dark else 170)  # the card's edge
        self.lift = QColor(0, 0, 0, 34) if self.dark else QColor(20, 24, 33, 10)  # one layer of a pill's shadow
        self.chip = alpha(self.surface_3 if self.dark else white, 235)  # the type chip over a screenshot
        self.field = self.bg if self.dark else self.surface_2  # text boxes
        self.button = self.surface_2 if self.dark else self.surface_3
        self.on_accent = white
        self.focus = mix(self.accent, white, 0.7)  # focus ring on an accent button
        self.idle = mix(self.accent, self.card, 0.55)  # the "new project" avatar when not picked
        self.glow = mix(self.accent, self.faint, 0.75)  # neutral glow under the list and sheet cards
        self.wash = 0.2 if self.dark else 0.12  # strength of the green/red swipe wash


_cache: dict[str, Colors] = {}


def current() -> Colors:
    try:
        name = theme.current()
    except Exception:  # noqa: BLE001 - settings unreadable: still draw, in the default theme
        name = "dark"
    if name not in _cache:
        _cache[name] = Colors(name)
    return _cache[name]
