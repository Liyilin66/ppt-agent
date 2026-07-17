"""Anchor pages: cover, TOC, section dividers, closing.

Cover, dividers and closing are normally designed by the model (see the
orchestrator); the builders here are the deterministic fallback library.
Each builder ships several visual variants so even fallback decks do not
all share one template. The variant is chosen per deck from the deck title,
keeping one deck internally consistent while different decks diverge.
"""

from __future__ import annotations

import zlib

from ppt_agent.v2.design import ThemeSpec
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    Frame,
    Gradient,
    LineItem,
    PageDesign,
    PageElement,
    ShapeItem,
    TextItem,
)


COVER_VARIANTS = 4
DIVIDER_VARIANTS = 4
CLOSING_VARIANTS = 3


def _is_english(language: str) -> bool:
    return language.lower().startswith("en")


def anchor_variant_seed(deck_title: str) -> int:
    """Stable per-deck seed so all fallback anchors share one variant family."""

    return zlib.crc32(deck_title.encode("utf-8"))


def build_cover_page(
    *,
    page_number: int,
    deck_title: str,
    subtitle: str | None,
    language: str,
    theme: ThemeSpec,
    variant: int | None = None,
) -> PageDesign:
    chosen = (anchor_variant_seed(deck_title) if variant is None else variant) % COVER_VARIANTS
    builder = (_cover_orbs, _cover_side_band, _cover_diagonal, _cover_editorial)[chosen]
    return builder(page_number, deck_title, subtitle, language, theme)


def _cover_orbs(
    page_number: int, deck_title: str, subtitle: str | None, language: str, theme: ThemeSpec
) -> PageDesign:
    elements: list[PageElement] = [
        ShapeItem(
            id="cover_orb",
            frame=Frame(x=CANVAS_WIDTH - 360, y=CANVAS_HEIGHT - 380, w=520, h=520),
            shape="ellipse",
            fill="accent",
            fill_alpha=0.18,
        ),
        ShapeItem(
            id="cover_orb_2",
            frame=Frame(x=CANVAS_WIDTH - 200, y=-140, w=340, h=340),
            shape="ellipse",
            fill="on_primary",
            fill_alpha=0.08,
        ),
        ShapeItem(
            id="cover_tag",
            frame=Frame(x=96, y=200, w=120, h=8),
            shape="pill",
            fill="accent",
        ),
        TextItem(
            id="cover_title",
            frame=Frame(x=96, y=248, w=860, h=180),
            text=deck_title,
            role="display",
            color="on_primary",
            valign="middle",
        ),
    ]
    if subtitle:
        elements.append(
            TextItem(
                id="cover_subtitle",
                frame=Frame(x=96, y=444, w=760, h=80),
                text=subtitle,
                role="subtitle",
                color="on_primary",
            )
        )
    return PageDesign(
        page_number=page_number,
        role="cover",
        title=deck_title,
        background="primary",
        background_gradient=Gradient(start="primary", end="secondary", angle_deg=115),
        show_chrome=False,
        elements=elements,
    )


def _cover_side_band(
    page_number: int, deck_title: str, subtitle: str | None, language: str, theme: ThemeSpec
) -> PageDesign:
    elements: list[PageElement] = [
        ShapeItem(
            id="cover_edge_band",
            frame=Frame(x=0, y=0, w=18, h=CANVAS_HEIGHT),
            shape="rectangle",
            fill="accent",
        ),
        ShapeItem(
            id="cover_panel",
            frame=Frame(x=860, y=96, w=356, h=528),
            shape="rounded_rectangle",
            fill="on_primary",
            fill_alpha=0.08,
        ),
        ShapeItem(
            id="cover_bar",
            frame=Frame(x=104, y=228, w=10, h=250),
            shape="rectangle",
            fill="accent",
        ),
        TextItem(
            id="cover_kicker",
            frame=Frame(x=150, y=232, w=600, h=30),
            text="PRESENTATION" if _is_english(language) else "专题演示",
            role="kicker",
            color="on_primary",
        ),
        TextItem(
            id="cover_title",
            frame=Frame(x=150, y=272, w=820, h=180),
            text=deck_title,
            role="display",
            color="on_primary",
        ),
        ShapeItem(
            id="cover_sq_1",
            frame=Frame(x=940, y=560, w=26, h=26),
            shape="rectangle",
            fill="accent",
        ),
        ShapeItem(
            id="cover_sq_2",
            frame=Frame(x=980, y=560, w=26, h=26),
            shape="rectangle",
            fill="on_primary",
            fill_alpha=0.4,
        ),
        ShapeItem(
            id="cover_sq_3",
            frame=Frame(x=1020, y=560, w=26, h=26),
            shape="rectangle",
            fill="on_primary",
            fill_alpha=0.16,
        ),
    ]
    if subtitle:
        elements.append(
            TextItem(
                id="cover_subtitle",
                frame=Frame(x=150, y=470, w=700, h=70),
                text=subtitle,
                role="subtitle",
                color="on_primary",
            )
        )
    return PageDesign(
        page_number=page_number,
        role="cover",
        title=deck_title,
        background="primary",
        background_gradient=Gradient(start="primary", end="secondary", angle_deg=100),
        show_chrome=False,
        elements=elements,
    )


def _cover_diagonal(
    page_number: int, deck_title: str, subtitle: str | None, language: str, theme: ThemeSpec
) -> PageDesign:
    elements: list[PageElement] = [
        ShapeItem(
            id="cover_diag_1",
            frame=Frame(x=740, y=0, w=760, h=720),
            shape="parallelogram",
            fill="on_primary",
            fill_alpha=0.07,
        ),
        ShapeItem(
            id="cover_diag_2",
            frame=Frame(x=990, y=0, w=430, h=720),
            shape="parallelogram",
            fill="accent",
            fill_alpha=0.15,
        ),
        LineItem(
            id="cover_rule",
            x1=96,
            y1=346,
            x2=430,
            y2=346,
            color="accent",
            width=3,
        ),
        TextItem(
            id="cover_title",
            frame=Frame(x=96, y=372, w=820, h=170),
            text=deck_title,
            role="display",
            color="on_primary",
        ),
    ]
    if subtitle:
        elements.append(
            TextItem(
                id="cover_subtitle",
                frame=Frame(x=96, y=556, w=720, h=70),
                text=subtitle,
                role="subtitle",
                color="on_primary",
            )
        )
    return PageDesign(
        page_number=page_number,
        role="cover",
        title=deck_title,
        background="primary",
        background_gradient=Gradient(start="secondary", end="primary", angle_deg=160),
        show_chrome=False,
        elements=elements,
    )


def _cover_editorial(
    page_number: int, deck_title: str, subtitle: str | None, language: str, theme: ThemeSpec
) -> PageDesign:
    elements: list[PageElement] = [
        ShapeItem(
            id="cover_top_rule",
            frame=Frame(x=0, y=0, w=CANVAS_WIDTH, h=12),
            shape="rectangle",
            fill="primary",
        ),
        ShapeItem(
            id="cover_accent_sq",
            frame=Frame(x=96, y=132, w=52, h=52),
            shape="rectangle",
            fill="accent",
        ),
        TextItem(
            id="cover_title",
            frame=Frame(x=96, y=236, w=1030, h=220),
            text=deck_title,
            role="display",
            color="text",
        ),
        LineItem(
            id="cover_base_rule",
            x1=96,
            y1=600,
            x2=1184,
            y2=600,
            color="primary",
            width=1.5,
        ),
        TextItem(
            id="cover_footer",
            frame=Frame(x=96, y=616, w=500, h=32),
            text="Presentation" if _is_english(language) else "专题演示",
            role="caption",
        ),
    ]
    if subtitle:
        elements.append(
            TextItem(
                id="cover_subtitle",
                frame=Frame(x=96, y=478, w=860, h=70),
                text=subtitle,
                role="subtitle",
            )
        )
    return PageDesign(
        page_number=page_number,
        role="cover",
        title=deck_title,
        background="background",
        show_chrome=False,
        elements=elements,
    )


def build_toc_pages(
    *,
    start_page_number: int,
    sections: list[tuple[str, int]],
    language: str,
    theme: ThemeSpec,
) -> list[PageDesign]:
    """One or more TOC pages listing (section_title, start_page) entries."""

    heading = "Contents" if _is_english(language) else "目录"
    per_page = 8
    chunks = [sections[i : i + per_page] for i in range(0, len(sections), per_page)] or [[]]
    pages: list[PageDesign] = []
    for chunk_index, chunk in enumerate(chunks):
        elements: list[PageElement] = [
            TextItem(
                id="toc_heading",
                frame=Frame(x=64, y=64, w=500, h=56),
                text=heading if chunk_index == 0 else f"{heading} · {chunk_index + 1}",
                role="title",
            ),
            LineItem(id="toc_rule", x1=64, y1=132, x2=240, y2=132, color="accent", width=3),
        ]
        columns = 2 if len(chunk) > 4 else 1
        rows = (len(chunk) + columns - 1) // columns if chunk else 1
        column_width = (CANVAS_WIDTH - 128 - (columns - 1) * 48) / columns
        row_height = min(96.0, (CANVAS_HEIGHT - 220) / max(rows, 1))
        for index, (section_title, start_page) in enumerate(chunk):
            column, row = divmod(index, rows)
            x = 64 + column * (column_width + 48)
            y = 176 + row * row_height
            ordinal = chunk_index * per_page + index + 1
            elements.extend(
                [
                    ShapeItem(
                        id=f"toc_num_bg_{index}",
                        frame=Frame(x=x, y=y, w=52, h=52),
                        shape="rounded_rectangle",
                        fill="primary_soft",
                    ),
                    TextItem(
                        id=f"toc_num_{index}",
                        frame=Frame(x=x, y=y, w=52, h=52),
                        text=f"{ordinal:02d}",
                        role="h3",
                        color="primary",
                        align="center",
                        valign="middle",
                    ),
                    TextItem(
                        id=f"toc_title_{index}",
                        frame=Frame(x=x + 68, y=y + 2, w=column_width - 130, h=48),
                        text=section_title,
                        role="h3",
                        valign="middle",
                    ),
                    TextItem(
                        id=f"toc_page_{index}",
                        frame=Frame(x=x + column_width - 56, y=y + 2, w=56, h=48),
                        text=f"P{start_page:02d}",
                        role="body_small",
                        align="right",
                        valign="middle",
                    ),
                ]
            )
        pages.append(
            PageDesign(
                page_number=start_page_number + chunk_index,
                role="toc",
                title=heading,
                elements=elements,
            )
        )
    return pages


def build_section_divider(
    *,
    page_number: int,
    section_index: int,
    section_count: int,
    section_title: str,
    section_goal: str | None,
    language: str,
    theme: ThemeSpec,
    variant: int | None = None,
) -> PageDesign:
    chosen = (
        anchor_variant_seed(section_title) if variant is None else variant
    ) % DIVIDER_VARIANTS
    builder = (
        _divider_numbered,
        _divider_band,
        _divider_split,
        _divider_minimal,
    )[chosen]
    return builder(
        page_number, section_index, section_count, section_title, section_goal, language, theme
    )


def _divider_progress_dots(
    section_index: int, section_count: int, *, color: str = "on_primary", y: float | None = None
) -> list[PageElement]:
    dot_size = 12.0
    y = CANVAS_HEIGHT - 80 if y is None else y
    return [
        ShapeItem(
            id=f"divider_dot_{index}",
            frame=Frame(x=96 + index * (dot_size + 12), y=y, w=dot_size, h=dot_size),
            shape="ellipse",
            fill=color,
            fill_alpha=1.0 if index < section_index else 0.3,
        )
        for index in range(section_count)
    ]


def _divider_numbered(
    page_number: int,
    section_index: int,
    section_count: int,
    section_title: str,
    section_goal: str | None,
    language: str,
    theme: ThemeSpec,
) -> PageDesign:
    elements: list[PageElement] = [
        ShapeItem(
            id="divider_orb",
            frame=Frame(x=-160, y=CANVAS_HEIGHT - 300, w=460, h=460),
            shape="ellipse",
            fill="on_primary",
            fill_alpha=0.07,
        ),
        TextItem(
            id="divider_number",
            frame=Frame(x=96, y=150, w=400, h=120),
            text=f"{section_index:02d}",
            role="display",
            color="accent",
            size_pt=72,
        ),
        TextItem(
            id="divider_title",
            frame=Frame(x=96, y=300, w=900, h=110),
            text=section_title,
            role="section",
            color="on_primary",
        ),
    ]
    if section_goal:
        elements.append(
            TextItem(
                id="divider_goal",
                frame=Frame(x=96, y=430, w=780, h=90),
                text=section_goal,
                role="subtitle",
                color="on_primary",
            )
        )
    elements.extend(_divider_progress_dots(section_index, section_count))
    return PageDesign(
        page_number=page_number,
        role="section_divider",
        section=section_title,
        title=section_title,
        background="primary",
        background_gradient=Gradient(start="secondary", end="primary", angle_deg=65),
        show_chrome=False,
        elements=elements,
    )


def _divider_band(
    page_number: int,
    section_index: int,
    section_count: int,
    section_title: str,
    section_goal: str | None,
    language: str,
    theme: ThemeSpec,
) -> PageDesign:
    elements: list[PageElement] = [
        TextItem(
            id="divider_number",
            frame=Frame(x=830, y=140, w=370, h=300),
            text=f"{section_index:02d}",
            role="display",
            color="on_primary",
            size_pt=120,
            align="right",
        ),
        ShapeItem(
            id="divider_band",
            frame=Frame(x=0, y=300, w=820, h=110),
            shape="rectangle",
            fill="on_primary",
            fill_alpha=0.10,
        ),
        ShapeItem(
            id="divider_band_tip",
            frame=Frame(x=0, y=300, w=14, h=110),
            shape="rectangle",
            fill="accent",
        ),
        TextItem(
            id="divider_title",
            frame=Frame(x=96, y=312, w=700, h=86),
            text=section_title,
            role="section",
            color="on_primary",
            valign="middle",
        ),
    ]
    if section_goal:
        elements.append(
            TextItem(
                id="divider_goal",
                frame=Frame(x=96, y=440, w=760, h=90),
                text=section_goal,
                role="subtitle",
                color="on_primary",
            )
        )
    elements.extend(_divider_progress_dots(section_index, section_count))
    return PageDesign(
        page_number=page_number,
        role="section_divider",
        section=section_title,
        title=section_title,
        background="primary",
        background_gradient=Gradient(start="primary", end="secondary", angle_deg=25),
        show_chrome=False,
        elements=elements,
    )


def _divider_split(
    page_number: int,
    section_index: int,
    section_count: int,
    section_title: str,
    section_goal: str | None,
    language: str,
    theme: ThemeSpec,
) -> PageDesign:
    elements: list[PageElement] = [
        ShapeItem(
            id="divider_panel",
            frame=Frame(x=0, y=0, w=440, h=CANVAS_HEIGHT),
            shape="rectangle",
            fill="primary",
            gradient=Gradient(start="primary", end="secondary", angle_deg=140),
        ),
        TextItem(
            id="divider_kicker",
            frame=Frame(x=72, y=200, w=300, h=30),
            text=f"SECTION {section_index:02d}" if _is_english(language) else f"第 {section_index:02d} 章",
            role="kicker",
            color="on_primary",
        ),
        TextItem(
            id="divider_number",
            frame=Frame(x=72, y=250, w=300, h=200),
            text=f"{section_index:02d}",
            role="display",
            color="on_primary",
            size_pt=110,
        ),
        TextItem(
            id="divider_title",
            frame=Frame(x=520, y=280, w=660, h=120),
            text=section_title,
            role="section",
            color="text",
        ),
    ]
    if section_goal:
        elements.append(
            TextItem(
                id="divider_goal",
                frame=Frame(x=520, y=420, w=620, h=100),
                text=section_goal,
                role="subtitle",
            )
        )
    elements.extend(
        _divider_progress_dots(section_index, section_count, color="primary", y=CANVAS_HEIGHT - 88)
    )
    # Shift dots to the right column for this layout.
    for element in elements:
        if element.id.startswith("divider_dot_"):
            element.frame.x += 424
    return PageDesign(
        page_number=page_number,
        role="section_divider",
        section=section_title,
        title=section_title,
        background="background",
        show_chrome=False,
        elements=elements,
    )


def _divider_minimal(
    page_number: int,
    section_index: int,
    section_count: int,
    section_title: str,
    section_goal: str | None,
    language: str,
    theme: ThemeSpec,
) -> PageDesign:
    progress_width = (CANVAS_WIDTH - 192) * (section_index / max(section_count, 1))
    elements: list[PageElement] = [
        ShapeItem(
            id="divider_top_rule",
            frame=Frame(x=0, y=0, w=CANVAS_WIDTH, h=10),
            shape="rectangle",
            fill="accent",
        ),
        TextItem(
            id="divider_kicker",
            frame=Frame(x=96, y=200, w=500, h=30),
            text=(
                f"SECTION {section_index:02d} / {section_count:02d}"
                if _is_english(language)
                else f"第 {section_index:02d} 章 · 共 {section_count:02d} 章"
            ),
            role="kicker",
        ),
        TextItem(
            id="divider_title",
            frame=Frame(x=96, y=250, w=1000, h=120),
            text=section_title,
            role="section",
            color="text",
        ),
        ShapeItem(
            id="divider_progress_track",
            frame=Frame(x=96, y=600, w=CANVAS_WIDTH - 192, h=6),
            shape="pill",
            fill="surface_alt",
        ),
        ShapeItem(
            id="divider_progress_fill",
            frame=Frame(x=96, y=600, w=max(progress_width, 24), h=6),
            shape="pill",
            fill="primary",
        ),
    ]
    if section_goal:
        elements.append(
            TextItem(
                id="divider_goal",
                frame=Frame(x=96, y=396, w=860, h=100),
                text=section_goal,
                role="subtitle",
            )
        )
    return PageDesign(
        page_number=page_number,
        role="section_divider",
        section=section_title,
        title=section_title,
        background="background",
        show_chrome=False,
        elements=elements,
    )


def build_closing_page(
    *,
    page_number: int,
    deck_title: str,
    language: str,
    theme: ThemeSpec,
    closing_note: str | None = None,
    variant: int | None = None,
) -> PageDesign:
    chosen = (anchor_variant_seed(deck_title) if variant is None else variant) % CLOSING_VARIANTS
    builder = (_closing_orb, _closing_editorial, _closing_diagonal)[chosen]
    return builder(page_number, deck_title, language, theme, closing_note)


def _closing_orb(
    page_number: int,
    deck_title: str,
    language: str,
    theme: ThemeSpec,
    closing_note: str | None,
) -> PageDesign:
    thanks = "Thank You" if _is_english(language) else "谢谢观看"
    note = closing_note or deck_title
    return PageDesign(
        page_number=page_number,
        role="closing",
        title=thanks,
        background="primary",
        background_gradient=Gradient(start="primary", end="secondary", angle_deg=245),
        show_chrome=False,
        elements=[
            ShapeItem(
                id="closing_orb",
                frame=Frame(x=CANVAS_WIDTH - 320, y=-160, w=480, h=480),
                shape="ellipse",
                fill="accent",
                fill_alpha=0.16,
            ),
            ShapeItem(
                id="closing_tag",
                frame=Frame(x=(CANVAS_WIDTH - 120) / 2, y=250, w=120, h=8),
                shape="pill",
                fill="accent",
            ),
            TextItem(
                id="closing_title",
                frame=Frame(x=190, y=290, w=900, h=110),
                text=thanks,
                role="display",
                color="on_primary",
                align="center",
                valign="middle",
            ),
            TextItem(
                id="closing_note",
                frame=Frame(x=240, y=420, w=800, h=60),
                text=note,
                role="subtitle",
                color="on_primary",
                align="center",
            ),
        ],
    )


def _closing_editorial(
    page_number: int,
    deck_title: str,
    language: str,
    theme: ThemeSpec,
    closing_note: str | None,
) -> PageDesign:
    thanks = "Thank You" if _is_english(language) else "谢谢观看"
    note = closing_note or deck_title
    return PageDesign(
        page_number=page_number,
        role="closing",
        title=thanks,
        background="background",
        show_chrome=False,
        elements=[
            ShapeItem(
                id="closing_accent_sq",
                frame=Frame(x=96, y=200, w=52, h=52),
                shape="rectangle",
                fill="accent",
            ),
            TextItem(
                id="closing_title",
                frame=Frame(x=96, y=290, w=1000, h=140),
                text=thanks,
                role="display",
                color="text",
            ),
            LineItem(
                id="closing_rule",
                x1=96,
                y1=470,
                x2=520,
                y2=470,
                color="primary",
                width=2,
            ),
            TextItem(
                id="closing_note",
                frame=Frame(x=96, y=492, w=860, h=60),
                text=note,
                role="subtitle",
            ),
        ],
    )


def _closing_diagonal(
    page_number: int,
    deck_title: str,
    language: str,
    theme: ThemeSpec,
    closing_note: str | None,
) -> PageDesign:
    thanks = "Thank You" if _is_english(language) else "谢谢观看"
    note = closing_note or deck_title
    return PageDesign(
        page_number=page_number,
        role="closing",
        title=thanks,
        background="primary",
        background_gradient=Gradient(start="secondary", end="primary", angle_deg=205),
        show_chrome=False,
        elements=[
            ShapeItem(
                id="closing_diag",
                frame=Frame(x=-220, y=0, w=640, h=720),
                shape="parallelogram",
                fill="on_primary",
                fill_alpha=0.07,
            ),
            ShapeItem(
                id="closing_diag_2",
                frame=Frame(x=-380, y=0, w=520, h=720),
                shape="parallelogram",
                fill="accent",
                fill_alpha=0.13,
            ),
            TextItem(
                id="closing_title",
                frame=Frame(x=430, y=300, w=750, h=120),
                text=thanks,
                role="display",
                color="on_primary",
                align="right",
            ),
            LineItem(
                id="closing_rule",
                x1=860,
                y1=440,
                x2=1180,
                y2=440,
                color="accent",
                width=3,
            ),
            TextItem(
                id="closing_note",
                frame=Frame(x=480, y=462, w=700, h=60),
                text=note,
                role="subtitle",
                color="on_primary",
                align="right",
            ),
        ],
    )
