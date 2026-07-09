"""Deterministic anchor pages: cover, TOC, section dividers, closing.

Structural pages are generated in code, not by the model. They are the
visual anchors of a long deck — always consistent, always polished, and
they cost zero tokens. Content pages in between are model-designed.
"""

from __future__ import annotations

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


def _is_english(language: str) -> bool:
    return language.lower().startswith("en")


def build_cover_page(
    *,
    page_number: int,
    deck_title: str,
    subtitle: str | None,
    language: str,
    theme: ThemeSpec,
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
    # Progress dots: filled up to the current section.
    dot_size = 12.0
    for index in range(section_count):
        elements.append(
            ShapeItem(
                id=f"divider_dot_{index}",
                frame=Frame(
                    x=96 + index * (dot_size + 12),
                    y=CANVAS_HEIGHT - 80,
                    w=dot_size,
                    h=dot_size,
                ),
                shape="ellipse",
                fill="on_primary",
                fill_alpha=1.0 if index < section_index else 0.3,
            )
        )
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


def build_closing_page(
    *,
    page_number: int,
    deck_title: str,
    language: str,
    theme: ThemeSpec,
    closing_note: str | None = None,
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
