"""Style profiles: how a deck *looks*, chosen by what kind of deck it is.

A profile is a bundle of typesetting parameters — type sizes, spacing, color
usage, card treatment, decoration — layered over the theme palette. The same
archetype ("three parallel points") typeset under two profiles reads as two
different kinds of presentation, while both stay inside measured rules
(minimum sizes, no overlaps, nothing outside the canvas).

Sizes are points on a 13.333 in x 7.5 in slide (the 1280 x 720 canvas). At that
size 14 pt is the smallest comfortable reading size for running text; the
legacy type scale's 13 / 11 / 10 pt body text is why generated pages read as
cramped.
"""

from __future__ import annotations

from typing import Literal


from ppt_agent.models import StrictModel
from ppt_agent.v2.design import ThemeFonts, ThemePalette, ThemeSpec

ProfileName = Literal["consulting", "launch", "training", "corporate"]
CardStyle = Literal["none", "soft", "outlined"]
MarkerStyle = Literal["rule_number", "big_number", "circle_number", "badge_number"]
TitleDecor = Literal["rule_below", "bar_left", "none"]


class TypeSizes(StrictModel):
    kicker: float
    title: float
    lead: float  # one-line summary under the title / statement support
    heading: float  # item headings inside a page
    body: float
    small: float  # secondary text inside items
    caption: float  # source line, footer, page number
    stat: float  # headline numbers
    marker: float  # step / item numbers


class StyleProfile(StrictModel):
    name: ProfileName
    label: str
    description: str
    palette: ThemePalette
    fonts: ThemeFonts
    sizes: TypeSizes
    margin_x: float = 72.0
    title_top: float = 54.0
    content_gap: float = 28.0  # space between title block and content
    item_gap: float = 24.0
    card_style: CardStyle = "none"
    card_radius: float = 0.0
    card_pad: float = 24.0
    marker: MarkerStyle = "rule_number"
    title_decor: TitleDecor = "rule_below"
    dark: bool = False
    background_gradient: bool = False
    centered_block: bool = False  # vertically centre side columns (chart insights)
    vertical_bias: float = 0.25  # where a sparse block sits: 0 = top, 0.5 = centre
    fill_target: float = 0.7  # how much of the content zone a sparse page grows into
    show_footer_title: bool = False
    kicker_as_pill: bool = False
    chart_font: float = 12.0
    table_style: Literal["filled", "minimal"] = "minimal"
    # Preferred compositions per archetype, best first. The engine falls back
    # to the next one to avoid repeating the previous page's arrangement.
    compositions: dict[str, list[str]] = {}

    def theme(self) -> ThemeSpec:
        """A ThemeSpec the existing renderer understands.

        Decoration is drawn by the typesetter inside each page, so the
        renderer's stamped motif and chrome are switched off.
        """

        return ThemeSpec(
            name=f"profile-{self.name}",
            mood=self.label,
            palette=self.palette,
            fonts=self.fonts,
            motif="none",
            dark_cover=self.dark,
            corner_radius=self.card_radius,
        )


def _fonts(family: str) -> ThemeFonts:
    # Microsoft YaHei's Latin glyphs are drawn from Segoe UI, and both YaHei and
    # DengXian ship with Office on Windows and macOS, so one family for both
    # scripts renders identically everywhere instead of falling back per run.
    return ThemeFonts(
        heading_latin=family,
        heading_east_asian=family,
        body_latin=family,
        body_east_asian=family,
    )


PROFILES: dict[ProfileName, StyleProfile] = {
    "consulting": StyleProfile(
        name="consulting",
        label="咨询汇报",
        description=(
            "结论式标题、克制留白、单一主色加一个强调色；不用卡片和图标，"
            "用细线、编号和图表组织信息；每页底部注明来源。"
        ),
        palette=ThemePalette(
            background="#FFFFFF",
            surface="#FFFFFF",
            surface_alt="#D9DEE5",
            primary="#12355B",
            primary_soft="#E6ECF3",
            secondary="#5B7FA6",
            accent="#D9822B",
            text="#1A1F26",
            muted="#5F6B78",
            on_primary="#FFFFFF",
        ),
        fonts=_fonts("Microsoft YaHei"),
        sizes=TypeSizes(
            kicker=11, title=24, lead=15, heading=17, body=14, small=13,
            caption=10, stat=34, marker=13,
        ),
        vertical_bias=0.22,
        title_top=46,
        content_gap=30,
        item_gap=32,
        card_style="none",
        marker="rule_number",
        title_decor="rule_below",
        fill_target=0.74,
        chart_font=12,
        compositions={
            "points": ["columns", "list2", "rows", "grid", "panel", "feature"],
            "process": ["chevron", "horizontal", "vertical"],
            "chart": ["stacked", "side", "hero"],
            "metrics": ["columns", "cards"],
            "statement": ["left", "center"],
            "compare": ["split", "versus"],
            "timeline": ["axis", "cards"],
        },
    ),
    "launch": StyleProfile(
        name="launch",
        label="产品发布",
        description=(
            "深色背景、超大字号、每页一个重点；数字是视觉主角，文字尽量少；"
            "卡片只用极淡的表面色区分。"
        ),
        palette=ThemePalette(
            background="#0B0E14",
            surface="#151A24",
            surface_alt="#1E2533",
            primary="#7C8CFF",
            primary_soft="#232B45",
            secondary="#3DD6C6",
            accent="#FFB547",
            text="#F4F6FA",
            muted="#A3ACBD",
            on_primary="#0B0E14",
        ),
        fonts=_fonts("DengXian"),
        sizes=TypeSizes(
            kicker=13, title=36, lead=18, heading=22, body=17, small=16,
            caption=11, stat=60, marker=40,
        ),
        margin_x=88,
        title_top=64,
        content_gap=44,
        item_gap=28,
        card_style="soft",
        card_radius=18,
        card_pad=30,
        marker="big_number",
        title_decor="none",
        dark=True,
        centered_block=True,
        vertical_bias=0.5,
        fill_target=0.75,
        chart_font=14,
        compositions={
            "points": ["columns", "feature", "list2", "rows"],
            "process": ["horizontal", "vertical"],
            "chart": ["hero", "side", "stacked"],
            "metrics": ["columns", "cards"],
            "statement": ["center", "left"],
            "compare": ["versus", "split"],
            "timeline": ["axis", "cards"],
        },
    ),
    "training": StyleProfile(
        name="training",
        label="培训课件",
        description=(
            "暖白底、大正文、编号步骤和醒目的提示框；每页先说明本页要记住什么，"
            "适合非专业听众快速跟上。"
        ),
        palette=ThemePalette(
            background="#FBFAF6",
            surface="#FFFFFF",
            surface_alt="#FFF1D6",
            primary="#0F766E",
            primary_soft="#D6EFEC",
            secondary="#2B6CB0",
            accent="#C26B0A",
            text="#1F2A2E",
            muted="#56636A",
            on_primary="#FFFFFF",
        ),
        fonts=_fonts("Microsoft YaHei"),
        sizes=TypeSizes(
            kicker=13, title=28, lead=17, heading=19, body=16, small=15,
            caption=11, stat=40, marker=20,
        ),
        content_gap=30,
        item_gap=22,
        card_style="soft",
        card_radius=14,
        card_pad=26,
        marker="circle_number",
        title_decor="none",
        kicker_as_pill=True,
        vertical_bias=0.3,
        fill_target=0.78,
        chart_font=14,
        compositions={
            "points": ["columns", "panel", "list2", "rows"],
            "process": ["horizontal", "vertical"],
            "chart": ["side", "kpi_top", "hero"],
            "metrics": ["cards", "columns"],
            "statement": ["center", "band"],
            "compare": ["split", "versus"],
            "timeline": ["cards", "axis"],
        },
    ),
    "corporate": StyleProfile(
        name="corporate",
        label="企业汇报",
        description=(
            "仪表盘式：指标卡、2×2 网格和通栏图表组合；沉稳的靛蓝灰配青绿与珊瑚点缀，"
            "白卡片浅描边，像大厂内部业务汇报。"
        ),
        palette=ThemePalette(
            background="#F2F4F7",
            surface="#FFFFFF",
            surface_alt="#E2E6EE",
            primary="#3D4F8F",
            primary_soft="#E7EAF4",
            secondary="#2E9C94",
            accent="#E0694B",
            text="#1E2330",
            muted="#596273",
            on_primary="#FFFFFF",
        ),
        fonts=_fonts("Microsoft YaHei"),
        sizes=TypeSizes(
            kicker=11, title=26, lead=15, heading=18, body=15, small=14,
            caption=10, stat=40, marker=14,
        ),
        vertical_bias=0.3,
        content_gap=28,
        item_gap=20,
        card_style="outlined",
        card_radius=10,
        card_pad=24,
        marker="badge_number",
        title_decor="bar_left",
        show_footer_title=True,
        fill_target=0.72,
        chart_font=12,
        compositions={
            "points": ["grid", "columns", "list2", "feature", "rows"],
            "process": ["horizontal", "vertical"],
            "chart": ["kpi_top", "side", "hero"],
            "metrics": ["cards", "columns"],
            "statement": ["band", "left"],
            "compare": ["versus", "split"],
            "timeline": ["cards", "axis"],
        },
    ),
}


def get_profile(name: str) -> StyleProfile:
    try:
        return PROFILES[name]  # type: ignore[index]
    except KeyError:
        raise ValueError(f"Unknown style profile '{name}'. Available: {', '.join(PROFILES)}") from None


__all__ = ["PROFILES", "ProfileName", "StyleProfile", "TypeSizes", "get_profile"]
