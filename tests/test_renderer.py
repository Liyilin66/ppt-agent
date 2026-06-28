from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.load import load_deck, load_theme
from ppt_agent.models import Deck
from ppt_agent.renderer import render_deck_to_pptx


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def test_render_deck_to_pptx_generates_editable_powerpoint(tmp_path: Path) -> None:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    output_path = tmp_path / "sample_deck.pptx"

    rendered_path = render_deck_to_pptx(deck, theme, output_path, assets_dir=EXAMPLES_DIR)

    assert rendered_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    presentation = Presentation(output_path)
    assert len(presentation.slides) == len(deck.slides)
    assert presentation.slide_width == Inches(deck.canvas_width_in)
    assert presentation.slide_height == Inches(deck.canvas_height_in)

    first_slide = presentation.slides[0]
    editable_text_shapes = [
        shape
        for shape in first_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]

    assert editable_text_shapes
    assert any("Q3 Operating Review" in shape.text for shape in editable_text_shapes)


def test_render_deck_to_pptx_creates_missing_image_placeholder(tmp_path: Path) -> None:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    theme = load_theme(EXAMPLES_DIR / "theme.json")

    output_path = render_deck_to_pptx(
        deck,
        theme,
        tmp_path / "sample_deck.pptx",
        assets_dir=EXAMPLES_DIR,
    )

    presentation = Presentation(output_path)
    third_slide = presentation.slides[2]

    assert any(
        shape.has_text_frame and "Placeholder" in shape.text
        for shape in third_slide.shapes
    )


def _template_slide_payload(layout: str, index: int) -> dict:
    body_texts = {
        "title_slide": ["A concise subtitle for the title slide"],
        "section_divider": ["This section frames the next part of the story."],
        "two_column": ["Left column content\n- Point A\n- Point B", "Right column content\n- Point C\n- Point D"],
        "three_column": ["First pillar", "Second pillar", "Third pillar"],
        "metric_cards": ["Revenue\n$4.2M", "Growth\n18%", "Retention\n91%"],
        "closing_slide": ["Thank you. Questions and next steps."],
    }[layout]

    elements = [
        {
            "element_id": f"s{index}_title",
            "type": "text",
            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
            "text": f"{layout} title",
        }
    ]
    for body_index, text in enumerate(body_texts, start=1):
        elements.append(
            {
                "element_id": f"s{index}_body_{body_index}",
                "type": "text",
                "bbox": {"x": 0.8 + body_index, "y": 1.5, "width": 3.0, "height": 1.0},
                "text": text,
            }
        )

    return {
        "slide_id": f"template_{index:03d}",
        "title": f"{layout} title",
        "layout": layout,
        "elements": elements,
    }


def test_template_layouts_render_to_editable_powerpoint(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "template_layout_demo",
            "title": "Template Layout Demo",
            "theme_name": "clean_business",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                _template_slide_payload(layout, index)
                for index, layout in enumerate(TEMPLATE_LAYOUTS, start=1)
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "template_layouts.pptx")

    presentation = Presentation(output_path)
    assert len(presentation.slides) == len(TEMPLATE_LAYOUTS)

    for layout, rendered_slide in zip(TEMPLATE_LAYOUTS, presentation.slides):
        editable_text_shapes = [
            shape
            for shape in rendered_slide.shapes
            if getattr(shape, "has_text_frame", False) and shape.text.strip()
        ]
        assert editable_text_shapes
        assert any(f"{layout} title" in shape.text for shape in editable_text_shapes)
