"""Three visual themes that share one HTML vocabulary.

A theme is a stylesheet over the same class names, so a page written once can
be shown in any of them, plus a ``ThemeSpec`` that colours the native charts
and tables and feeds the fallback layout engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal

from ppt_agent.v2.design import ChromeSpec, ThemeFonts, ThemePalette, ThemeSpec

ThemeName = Literal["editorial", "dark", "bright"]
THEME_NAMES: tuple[str, ...] = ("editorial", "dark", "bright")
EXAMPLE_NAMES: tuple[str, ...] = (
    "cover", "section", "hero", "chart", "table", "columns", "steps", "statement",
)


@dataclass(frozen=True)
class HtmlTheme:
    name: str
    label: str
    mood: str
    css: str
    backdrop: str  # elements drawn behind every content page
    spec: ThemeSpec


_NO_CHROME = ChromeSpec(show_page_number=False, show_footer=False, show_section_kicker=False)

_SPECS: dict[str, tuple[str, str, str, ThemeSpec]] = {
    "editorial": (
        "杂志编辑",
        "研究院年报、杂志专题：米白纸感、衬线标题、黑加朱红、细线、大留白",
        "",
        ThemeSpec(
            name="html-editorial",
            mood="editorial, restrained",
            palette=ThemePalette(
                background="#F4EFE6", surface="#F4EFE6", surface_alt="#CDC5B3",
                primary="#1B1A17", primary_soft="#EAE3D4", secondary="#CDC5B3",
                accent="#C8371F", text="#1B1A17", muted="#6A655A", on_primary="#F4EFE6",
            ),
            fonts=ThemeFonts(heading_latin="Georgia", heading_east_asian="SimSun",
                             body_latin="DengXian", body_east_asian="DengXian"),
            motif="none", corner_radius=0, chrome=_NO_CHROME,
        ),
    ),
    "dark": (
        "深色科技",
        "发布会、AI 产品官网：近黑底、蓝青光晕、渐变数字、半透明卡片",
        '<div class="backdrop b1"></div><div class="backdrop b2"></div>',
        ThemeSpec(
            name="html-dark",
            mood="dark, technical",
            palette=ThemePalette(
                background="#07090F", surface="#11151F", surface_alt="#2A3142",
                primary="#6EA8FF", primary_soft="#16223F", secondary="#3A4660",
                accent="#3FF0D0", text="#EEF1F8", muted="#8D97AD", on_primary="#04121A",
            ),
            fonts=ThemeFonts(heading_latin="Microsoft YaHei", heading_east_asian="Microsoft YaHei",
                             body_latin="Microsoft YaHei", body_east_asian="Microsoft YaHei"),
            motif="none", corner_radius=18, chrome=_NO_CHROME,
        ),
    ),
    "bright": (
        "明亮现代",
        "新一代 SaaS 品牌手册：暖白底、墨绿大色块、荧光黄绿点缀、大圆角",
        "",
        ThemeSpec(
            name="html-bright",
            mood="bright, confident",
            palette=ThemePalette(
                background="#FBFAF7", surface="#FFFFFF", surface_alt="#DCD6C6",
                primary="#0F3D2E", primary_soft="#EFEADC", secondary="#C9C3B0",
                accent="#1D7A4C", text="#12261F", muted="#5D6C66", on_primary="#F3F1E7",
            ),
            fonts=ThemeFonts(heading_latin="Microsoft YaHei", heading_east_asian="Microsoft YaHei",
                             body_latin="Microsoft YaHei", body_east_asian="Microsoft YaHei"),
            motif="none", corner_radius=26, chrome=_NO_CHROME,
        ),
    ),
}


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text("utf-8")


@lru_cache(maxsize=None)
def get(name: str) -> HtmlTheme:
    if name not in _SPECS:
        raise ValueError(f"Unknown theme '{name}'. Available: {', '.join(THEME_NAMES)}")
    label, mood, backdrop, spec = _SPECS[name]
    return HtmlTheme(name=name, label=label, mood=mood, css=_read("base.css") + _read(f"{name}.css"),
                     backdrop=backdrop, spec=spec)


@lru_cache(maxsize=None)
def example(name: str) -> str:
    """One of the hand-written sample slides the design prompt shows the model."""

    return _read(f"examples/{name}.html").strip()


@lru_cache(maxsize=None)
def guide() -> str:
    return _read("guide.md")
