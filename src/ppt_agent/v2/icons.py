"""Curated icon registry: names the model may use, rendered as text glyphs.

Glyphs marked monochrome tint with the theme color; emoji glyphs keep their
own colors but still read well on cards. Unknown names degrade to a bullet
dot so a bad icon name can never fail a page.
"""

from __future__ import annotations


# name -> (glyph, monochrome)
ICON_GLYPHS: dict[str, tuple[str, bool]] = {
    "check": ("✓", True),
    "cross": ("✕", True),
    "plus": ("+", True),
    "arrow_right": ("→", True),
    "arrow_up": ("↗", True),
    "arrow_down": ("↘", True),
    "target": ("◎", True),
    "star": ("★", True),
    "spark": ("✦", True),
    "dot": ("●", True),
    "diamond": ("◆", True),
    "warning": ("⚠", True),
    "bolt": ("⚡", False),
    "bulb": ("💡", False),
    "gear": ("⚙", True),
    "chart": ("📊", False),
    "growth": ("📈", False),
    "decline": ("📉", False),
    "users": ("👥", False),
    "user": ("👤", False),
    "shield": ("🛡", False),
    "clock": ("🕐", False),
    "calendar": ("📅", False),
    "flag": ("🚩", False),
    "book": ("📖", False),
    "globe": ("🌐", False),
    "chat": ("💬", False),
    "money": ("💰", False),
    "link": ("🔗", False),
    "search": ("🔍", False),
    "heart": ("♥", True),
    "trophy": ("🏆", False),
    "key": ("🔑", False),
    "lock": ("🔒", False),
    "mail": ("✉", True),
    "phone": ("📞", False),
    "pin": ("📍", False),
    "folder": ("📁", False),
    "doc": ("📄", False),
    "code": ("⌨", True),
    "cloud": ("☁", True),
    "database": ("🗄", False),
    "brain": ("🧠", False),
    "robot": ("🤖", False),
    "rocket": ("🚀", False),
    "puzzle": ("🧩", False),
    "handshake": ("🤝", False),
    "scale": ("⚖", True),
    "compass": ("🧭", False),
    "layers": ("📚", False),
    "wrench": ("🔧", False),
    "fire": ("🔥", False),
    "leaf": ("🌿", False),
    "question": ("?", True),
}

FALLBACK_GLYPH = ("●", True)


def resolve_icon(name: str) -> tuple[str, bool]:
    return ICON_GLYPHS.get(name.strip().lower(), FALLBACK_GLYPH)


def icon_catalog_for_prompt() -> str:
    """Comma-separated icon names for the page-design prompt."""

    return ", ".join(sorted(ICON_GLYPHS))
