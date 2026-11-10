"""Make the browser measure text with the fonts PowerPoint will draw it in.

Office ships the same Chinese fonts on Windows and macOS. On Windows they are
installed system-wide; on macOS they live inside the app bundle, so the browser
only sees them through ``@font-face`` rules pointing at those files. Measuring
with any other font moves every line break.
"""

from __future__ import annotations

import sys
from pathlib import Path

# family, weight, file name inside Office's font folder
OFFICE_FACES: tuple[tuple[str, int, str], ...] = (
    ("Microsoft YaHei", 400, "msyh.ttc"),
    ("Microsoft YaHei", 700, "msyhbd.ttc"),
    ("Microsoft YaHei Light", 400, "msyhl.ttc"),
    ("DengXian", 400, "Deng.ttf"),
    ("DengXian", 700, "Dengb.ttf"),
    ("DengXian Light", 400, "Dengl.ttf"),
    ("SimSun", 400, "Simsun.ttc"),
    ("SimHei", 400, "SimHei.ttf"),
)
FAMILIES = tuple(dict.fromkeys(family for family, _, _ in OFFICE_FACES))

# What stands in when Office is absent: close in width, never identical.
_FALLBACKS = {
    "Microsoft YaHei": ("PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC"),
    "Microsoft YaHei Light": ("PingFang SC", "Noto Sans CJK SC"),
    "DengXian": ("PingFang SC", "Noto Sans CJK SC"),
    "DengXian Light": ("PingFang SC", "Noto Sans CJK SC"),
    "SimSun": ("Songti SC", "Noto Serif CJK SC", "Source Han Serif SC"),
    "SimHei": ("Heiti SC", "PingFang SC", "Noto Sans CJK SC"),
}

_MAC_DIRS = tuple(
    Path(f"/Applications/Microsoft {app}.app/Contents/Resources/DFonts")
    for app in ("PowerPoint", "Word", "Excel")
)


def office_font_dir(candidates: tuple[Path, ...] = _MAC_DIRS) -> Path | None:
    for folder in candidates:
        if (folder / "msyh.ttc").is_file():
            return folder
    return None


def office_font_css(
    font_dir: Path | None = None, *, platform: str = sys.platform
) -> tuple[str, bool]:
    """Return ``@font-face`` rules and whether the real Office fonts are in use."""

    if platform.startswith("win"):
        return "", True  # installed with Windows/Office; the browser finds them by name
    folder = font_dir if font_dir is not None else office_font_dir()
    rules = []
    if folder is not None:
        for family, weight, file_name in OFFICE_FACES:
            path = folder / file_name
            if path.is_file():
                rules.append(
                    f"@font-face{{font-family:'{family}';font-weight:{weight};"
                    f"src:url('{path.as_uri()}');}}"
                )
        if rules:
            return "\n".join(rules), True
    for family, stand_ins in _FALLBACKS.items():
        sources = ",".join(f"local('{name}')" for name in stand_ins)
        rules.append(f"@font-face{{font-family:'{family}';src:{sources};}}")
    return "\n".join(rules), False
