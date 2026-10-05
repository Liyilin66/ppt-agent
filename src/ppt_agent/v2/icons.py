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
    "play": ("▶", True),
    "pause": ("❙❙", True),
    "video": ("🎬", False),
    "camera": ("📷", False),
    "tag": ("🏷", False),
    "box": ("📦", False),
    "truck": ("🚚", False),
    "pill": ("💊", False),
    "flask": ("🧪", False),
    "ruler": ("📐", False),
    "list": ("☰", True),
    "grid": ("▦", True),
    "eye": ("👁", False),
    "hand": ("✋", False),
    "sun": ("☀", True),
    "moon": ("☾", True),
    "food": ("🍽", False),
    "drop": ("💧", False),
    "fish": ("🐟", False),
}

FALLBACK_GLYPH = ("●", True)

# Near-miss names the model reaches for, mapped onto a real glyph. A wrong icon
# name should degrade to something meaningful, not to an unexplained dot.
ICON_ALIASES: dict[str, str] = {
    "arrow": "arrow_right",
    "attention": "warning",
    "alert": "warning",
    "caution": "warning",
    "bell": "warning",
    "bag": "box",
    "cart": "box",
    "package": "box",
    "shipping": "truck",
    "delivery": "truck",
    "price": "tag",
    "label": "tag",
    "discount": "tag",
    "capsule": "pill",
    "medicine": "pill",
    "drug": "pill",
    "health": "heart",
    "medical": "heart",
    "lab": "flask",
    "science": "flask",
    "test": "flask",
    "measure": "ruler",
    "size": "ruler",
    "spec": "ruler",
    "livestream": "video",
    "stream": "video",
    "movie": "video",
    "film": "video",
    "photo": "camera",
    "image": "camera",
    "picture": "camera",
    "bullet": "dot",
    "circle": "dot",
    "square": "grid",
    "table": "grid",
    "menu": "list",
    "steps": "list",
    "note": "doc",
    "file": "doc",
    "page": "doc",
    "team": "users",
    "people": "users",
    "person": "user",
    "customer": "user",
    "time": "clock",
    "date": "calendar",
    "schedule": "calendar",
    "idea": "bulb",
    "tip": "bulb",
    "insight": "bulb",
    "goal": "target",
    "aim": "target",
    "focus": "target",
    "safety": "shield",
    "secure": "shield",
    "trust": "shield",
    "quality": "trophy",
    "award": "trophy",
    "certified": "check",
    "verified": "check",
    "ok": "check",
    "no": "cross",
    "bad": "cross",
    "up": "arrow_up",
    "down": "arrow_down",
    "increase": "growth",
    "decrease": "decline",
    "water": "drop",
    "liquid": "drop",
    "oil": "drop",
    "energy": "bolt",
    "power": "bolt",
    "nature": "leaf",
    "plant": "leaf",
    "eat": "food",
    "dose": "food",
    "dosage": "pill",
}


def resolve_icon(name: str) -> tuple[str, bool]:
    key = canonical_icon_name(name)
    return ICON_GLYPHS.get(key, FALLBACK_GLYPH)


def canonical_icon_name(name: str) -> str:
    """Map a model-supplied icon name onto a catalog name when we can.

    Exact catalog names win, then the alias table, then a singular/plural
    nudge. Anything still unknown is returned as-is so QA can report it.
    """

    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    if key in ICON_GLYPHS:
        return key
    if key in ICON_ALIASES:
        return ICON_ALIASES[key]
    for variant in (key.rstrip("s"), f"{key}s"):
        if variant in ICON_GLYPHS:
            return variant
        if variant in ICON_ALIASES:
            return ICON_ALIASES[variant]
    return key


def icon_catalog_for_prompt() -> str:
    """Comma-separated icon names for the page-design prompt."""

    return ", ".join(sorted(ICON_GLYPHS))
