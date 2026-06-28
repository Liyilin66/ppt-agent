"""Controlled slide layouts for template-guided rendering."""

from __future__ import annotations

from typing import Literal


TemplateLayout = Literal[
    "title_slide",
    "section_divider",
    "two_column",
    "three_column",
    "four_cards",
    "metric_cards",
    "closing_slide",
    "comparison_matrix",
    "process_flow",
    "risk_matrix",
    "key_takeaway",
]


TEMPLATE_LAYOUTS: tuple[TemplateLayout, ...] = (
    "title_slide",
    "section_divider",
    "two_column",
    "three_column",
    "four_cards",
    "metric_cards",
    "closing_slide",
    "comparison_matrix",
    "process_flow",
    "risk_matrix",
    "key_takeaway",
)


def is_template_layout(layout: str) -> bool:
    return layout in TEMPLATE_LAYOUTS
