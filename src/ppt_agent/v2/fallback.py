"""Deterministic archetype designer.

Two jobs: (1) resilience — when a page's model call fails past all retries,
the deck still ships with a clean templated page instead of a hole; (2) the
offline demo/test fixture — the mock client routes page-design tasks here so
the full 100-page pipeline runs without any API key.
"""

from __future__ import annotations

from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ChartItem,
    ChartSeries,
    Frame,
    IconItem,
    LineItem,
    PageDesign,
    PageElement,
    ShapeItem,
    TableItem,
    TextItem,
)
from ppt_agent.v2.planning import PageBrief


MARGIN = 64.0
CONTENT_TOP = 162.0
CONTENT_BOTTOM = CANVAS_HEIGHT - 64.0
CONTENT_WIDTH = CANVAS_WIDTH - 2 * MARGIN

_CYCLE_ICONS = [
    "target", "rocket", "bulb", "gear", "users", "chart", "shield", "compass",
    "spark", "layers", "globe", "key",
]


def _title_block(title: str) -> list[PageElement]:
    return [
        TextItem(
            id="fb_title",
            frame=Frame(x=MARGIN, y=56, w=CONTENT_WIDTH - 200, h=76),
            text=title,
            role="title",
            valign="middle",
        ),
        LineItem(id="fb_rule", x1=MARGIN, y1=142, x2=MARGIN + 130, y2=142, color="accent", width=3),
    ]


def _icon_for(index: int, page_number: int) -> str:
    return _CYCLE_ICONS[(page_number + index) % len(_CYCLE_ICONS)]


def _cards_layout(brief: PageBrief, page_number: int) -> list[PageElement]:
    points = brief.points[:4] or [brief.summary or brief.title]
    count = max(2, min(4, len(points)))
    gap = 28.0
    card_width = (CONTENT_WIDTH - gap * (count - 1)) / count
    card_top, card_height = CONTENT_TOP + 20, CONTENT_BOTTOM - CONTENT_TOP - 40
    elements: list[PageElement] = _title_block(brief.title)
    for index in range(count):
        x = MARGIN + index * (card_width + gap)
        text = points[index] if index < len(points) else ""
        elements.extend(
            [
                ShapeItem(
                    id=f"fb_card_{index}",
                    frame=Frame(x=x, y=card_top, w=card_width, h=card_height),
                    shape="rounded_rectangle",
                    fill="surface" if index % 2 == 0 else "surface_alt",
                ),
                IconItem(
                    id=f"fb_icon_{index}",
                    frame=Frame(x=x + 24, y=card_top + 28, w=52, h=52),
                    name=_icon_for(index, page_number),
                ),
                TextItem(
                    id=f"fb_card_head_{index}",
                    frame=Frame(x=x + 24, y=card_top + 100, w=card_width - 48, h=54),
                    text=f"{index + 1:02d}",
                    role="stat",
                ),
                TextItem(
                    id=f"fb_card_text_{index}",
                    frame=Frame(
                        x=x + 24,
                        y=card_top + 164,
                        w=card_width - 48,
                        h=card_height - 190,
                    ),
                    text=text,
                    role="body",
                ),
            ]
        )
    return elements


def _stats_layout(brief: PageBrief, page_number: int) -> list[PageElement]:
    points = brief.points[:3] or [brief.summary or brief.title]
    count = max(2, min(3, len(points)))
    gap = 32.0
    card_width = (CONTENT_WIDTH - gap * (count - 1)) / count
    elements: list[PageElement] = _title_block(brief.title)
    stats = ["01", "02", "03"]
    for index in range(count):
        x = MARGIN + index * (card_width + gap)
        elements.extend(
            [
                ShapeItem(
                    id=f"fb_stat_card_{index}",
                    frame=Frame(x=x, y=CONTENT_TOP + 40, w=card_width, h=320),
                    shape="rounded_rectangle",
                    fill="primary_soft" if index == 0 else "surface",
                ),
                TextItem(
                    id=f"fb_stat_{index}",
                    frame=Frame(x=x + 28, y=CONTENT_TOP + 90, w=card_width - 56, h=70),
                    text=stats[index],
                    role="stat",
                ),
                TextItem(
                    id=f"fb_stat_text_{index}",
                    frame=Frame(x=x + 28, y=CONTENT_TOP + 180, w=card_width - 56, h=150),
                    text=points[index] if index < len(points) else "",
                    role="body",
                ),
            ]
        )
    return elements


def _timeline_layout(brief: PageBrief, page_number: int) -> list[PageElement]:
    points = brief.points[:5] or [brief.summary or brief.title]
    count = max(3, min(5, len(points)))
    axis_y = (CONTENT_TOP + CONTENT_BOTTOM) / 2
    step = CONTENT_WIDTH / count
    elements: list[PageElement] = _title_block(brief.title)
    elements.append(
        LineItem(
            id="fb_axis",
            x1=MARGIN + 30,
            y1=axis_y,
            x2=CANVAS_WIDTH - MARGIN - 30,
            y2=axis_y,
            color="primary_soft",
            width=3,
        )
    )
    for index in range(count):
        x = MARGIN + index * step
        text = points[index] if index < len(points) else ""
        above = index % 2 == 0
        text_y = axis_y - 150 if above else axis_y + 46
        elements.extend(
            [
                ShapeItem(
                    id=f"fb_node_{index}",
                    frame=Frame(x=x + step / 2 - 14, y=axis_y - 14, w=28, h=28),
                    shape="ellipse",
                    fill="primary" if index == 0 else "surface",
                    stroke="primary",
                    stroke_width=2,
                ),
                TextItem(
                    id=f"fb_step_no_{index}",
                    frame=Frame(x=x + 12, y=text_y, w=step - 24, h=28),
                    text=f"{index + 1:02d}",
                    role="kicker",
                    align="center",
                ),
                TextItem(
                    id=f"fb_step_text_{index}",
                    frame=Frame(x=x + 12, y=text_y + 32, w=step - 24, h=96),
                    text=text,
                    role="body_small",
                    align="center",
                ),
            ]
        )
    return elements


def _two_column_layout(brief: PageBrief, page_number: int) -> list[PageElement]:
    points = brief.points or [brief.summary or brief.title]
    middle = (len(points) + 1) // 2
    left, right = points[:middle], points[middle:] or [""]
    column_width = (CONTENT_WIDTH - 40) / 2
    elements: list[PageElement] = _title_block(brief.title)
    for column, (label, chunk) in enumerate(
        ((f"{brief.title[:12]} A", left), (f"{brief.title[:12]} B", right))
    ):
        x = MARGIN + column * (column_width + 40)
        elements.extend(
            [
                ShapeItem(
                    id=f"fb_col_{column}",
                    frame=Frame(x=x, y=CONTENT_TOP, w=column_width, h=CONTENT_BOTTOM - CONTENT_TOP),
                    shape="rounded_rectangle",
                    fill="surface" if column == 0 else "primary_soft",
                ),
                TextItem(
                    id=f"fb_col_text_{column}",
                    frame=Frame(
                        x=x + 28,
                        y=CONTENT_TOP + 32,
                        w=column_width - 56,
                        h=CONTENT_BOTTOM - CONTENT_TOP - 64,
                    ),
                    text="\n".join(chunk),
                    role="body",
                    bullet="dot",
                ),
            ]
        )
    return elements


def _quote_layout(brief: PageBrief, page_number: int) -> list[PageElement]:
    quote = brief.summary or (brief.points[0] if brief.points else brief.title)
    return [
        ShapeItem(
            id="fb_quote_mark_bg",
            frame=Frame(x=MARGIN, y=170, w=90, h=90),
            shape="ellipse",
            fill="primary_soft",
        ),
        TextItem(
            id="fb_quote_mark",
            frame=Frame(x=MARGIN, y=170, w=90, h=90),
            text="“",
            role="display",
            color="primary",
            align="center",
            valign="middle",
        ),
        TextItem(
            id="fb_quote",
            frame=Frame(x=MARGIN + 40, y=290, w=CONTENT_WIDTH - 80, h=200),
            text=quote,
            role="quote",
            align="center",
            valign="middle",
        ),
        TextItem(
            id="fb_quote_source",
            frame=Frame(x=MARGIN + 40, y=520, w=CONTENT_WIDTH - 80, h=36),
            text=f"—— {brief.title}",
            role="body_small",
            align="center",
        ),
    ]


def _chart_layout(brief: PageBrief, page_number: int) -> list[PageElement]:
    categories = ["阶段一", "阶段二", "阶段三", "阶段四"]
    base = (page_number % 5) + 2
    values = [base + offset * 1.7 for offset in range(4)]
    elements: list[PageElement] = _title_block(brief.title)
    elements.extend(
        [
            ChartItem(
                id="fb_chart",
                frame=Frame(x=MARGIN, y=CONTENT_TOP, w=640, h=CONTENT_BOTTOM - CONTENT_TOP),
                chart="column" if page_number % 2 == 0 else "bar",
                categories=categories,
                series=[ChartSeries(name=brief.title[:12], values=values)],
                show_legend=False,
                show_data_labels=True,
            ),
            ShapeItem(
                id="fb_chart_panel",
                frame=Frame(x=MARGIN + 680, y=CONTENT_TOP, w=CONTENT_WIDTH - 680, h=CONTENT_BOTTOM - CONTENT_TOP),
                shape="rounded_rectangle",
                fill="surface",
            ),
            TextItem(
                id="fb_chart_notes",
                frame=Frame(
                    x=MARGIN + 708,
                    y=CONTENT_TOP + 32,
                    w=CONTENT_WIDTH - 736,
                    h=CONTENT_BOTTOM - CONTENT_TOP - 64,
                ),
                text="\n".join(brief.points[:4]) or brief.summary or brief.title,
                role="body",
                bullet="dot",
            ),
        ]
    )
    return elements


def _table_layout(brief: PageBrief, page_number: int) -> list[PageElement]:
    points = brief.points[:5] or [brief.summary or brief.title]
    rows = [[f"{index + 1:02d}", point, "✓"] for index, point in enumerate(points)]
    elements: list[PageElement] = _title_block(brief.title)
    elements.append(
        TableItem(
            id="fb_table",
            frame=Frame(
                x=MARGIN,
                y=CONTENT_TOP,
                w=CONTENT_WIDTH,
                h=min(80.0 + 56.0 * len(rows), CONTENT_BOTTOM - CONTENT_TOP),
            ),
            headers=["序号", "要点", "状态"],
            rows=rows,
        )
    )
    return elements


_LAYOUTS = {
    "cards": _cards_layout,
    "stats": _stats_layout,
    "timeline": _timeline_layout,
    "two_column": _two_column_layout,
    "comparison": _two_column_layout,
    "quote": _quote_layout,
    "chart": _chart_layout,
    "table": _table_layout,
    "list": _two_column_layout,
}

_AUTO_CYCLE = ["cards", "two_column", "stats", "timeline", "chart", "cards", "table", "two_column"]


def design_fallback_page(
    brief: PageBrief,
    *,
    page_number: int,
    section_title: str | None,
    language: str,
) -> PageDesign:
    """Build a clean templated page for one brief, varying layout by position."""

    hint = brief.layout_hint
    if hint == "auto" or hint not in _LAYOUTS:
        hint = _AUTO_CYCLE[page_number % len(_AUTO_CYCLE)]
    elements = _LAYOUTS[hint](brief, page_number)
    role = "quote" if hint == "quote" else ("stats" if hint == "stats" else "content")
    return PageDesign(
        page_number=page_number,
        role=role,  # type: ignore[arg-type]
        section=section_title,
        title=brief.title,
        elements=elements,
        speaker_notes=brief.summary or brief.title,
    )
