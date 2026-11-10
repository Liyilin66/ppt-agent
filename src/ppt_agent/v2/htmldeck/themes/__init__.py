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
    # 1: blocks and cards (editorial/dark/bright share it). 2: grid, type and diagrams (studio).
    vocabulary: int = 1


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


# The studio theme: pages composed on a grid from type, rules and diagrams.
STUDIO = "studio"
STUDIO_FORMS: dict[str, str] = {
    "poster": "数据海报：一个巨大的数字就是画面，旁边细线分出单位和变化，底部一行三条小字事实",
    "strip": "比例条：左侧标题和一段话，右侧两条等长的条，填色的一小段是占比，大数字跟在条下面",
    "units": "方块阵：左侧论点，右侧一个大百分比，下面十个方块按比例点亮",
    "columns": "柱子即版面：三根贴底的柱子，高度就是数据或快慢，文字写在柱子里",
    "split": "左右对开：两个方案各占一半，一半是深色色域并出血到页边",
    "timeline": "时间轴：一条横贯页面的轴线，事件在轴线上下交错",
    "stairs": "阶梯：每一步向右下退一级，最后一步用红线，左下角一句结论",
    "flow": "因果链：上方标题和右上角的结果数字，下方三到四个节点用箭头连起来",
    "matrix": "四象限：左侧标题，右侧用十字线分出的 2×2，每格一个要点",
    "ledger": "账本：左侧大标题，右侧一行一条「数字 + 它衡量什么」，用细线隔开",
    "chart": "图表加旁注：左侧原生图表，右侧细线后面一个大数字和两句解读",
    "table": "表格：通栏原生表格，下面一句共同结论",
    "margin": "正文加边栏：左侧大标题和一组前后对比的数字，右侧细线后面三条边注",
    "statement": "观点页：左侧红色竖线，一句大字结论，右下角一行依据",
}


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text("utf-8")


@lru_cache(maxsize=None)
def get(name: str) -> HtmlTheme:
    if name not in _SPECS and name != STUDIO:
        raise ValueError(f"Unknown theme '{name}'. Available: {', '.join(THEME_NAMES)}")
    if name == STUDIO:
        return HtmlTheme(
            name=STUDIO, label="编辑式", mood="研究机构年报和杂志专题：纸感底色、墨色、一种朱红；靠字号反差、细线、留白和图解，不靠卡片",
            css=_read("studio.css"), backdrop="", vocabulary=2,
            spec=_SPECS["editorial"][3].model_copy(update={
                "name": "html-studio",
                "fonts": ThemeFonts(heading_latin="Times New Roman", heading_east_asian="SimSun",
                                    body_latin="DengXian", body_east_asian="DengXian")}),
        )
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


@lru_cache(maxsize=None)
def studio_example(form: str) -> str:
    """The hand-written sample for one visual form of the studio theme."""

    return _read(f"studio/{form}.html").strip()
