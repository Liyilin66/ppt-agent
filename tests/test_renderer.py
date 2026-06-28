from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.load import load_deck, load_theme
from ppt_agent.models import Deck
from ppt_agent.renderer import render_deck_to_pptx


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
INTERNAL_SURFACE_TERMS = {"TEMPLATE-GUIDED", "Editable PPTX", "COLUMN", "CARD"}


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
        "four_cards": [
            "Discover\nMap user needs",
            "Prioritize\nChoose high-value work",
            "Prototype\nBuild the first flow",
            "Measure\nTrack quality signals",
        ],
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


def _shape_with_text(slide, text: str):
    for shape in slide.shapes:
        if getattr(shape, "has_text_frame", False) and shape.text.strip() == text:
            return shape
    raise AssertionError(f"Could not find editable text shape: {text}")


def _visible_texts(presentation: Presentation) -> list[str]:
    return [
        shape.text.strip()
        for slide in presentation.slides
        for shape in slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]


def test_title_slide_long_title_keeps_subtitle_below_title_bbox(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    long_title = "Template Guided Presentations Improve Visual Quality for Complex Business Audiences"
    subtitle = "A short subtitle stays below the wrapped title"
    deck = Deck.model_validate(
        {
            "deck_id": "title_spacing_demo",
            "title": "Title Spacing Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Title Spacing Demo",
                    "layout": "title_slide",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
                            "text": long_title,
                        },
                        {
                            "element_id": "subtitle",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 1.3, "width": 6.0, "height": 0.5},
                            "text": subtitle,
                        },
                    ],
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "title_spacing.pptx")
    rendered_slide = Presentation(output_path).slides[0]
    title_shape = _shape_with_text(rendered_slide, long_title)
    subtitle_shape = _shape_with_text(rendered_slide, subtitle)
    visible_texts = [
        shape.text.strip()
        for shape in rendered_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]

    assert subtitle_shape.top >= title_shape.top + title_shape.height + Inches(0.25)
    assert any(text == "Template\nGuided\nPresentations" for text in visible_texts)
    assert all(len(line) > 1 for text in visible_texts for line in text.splitlines() if line.strip())


def test_four_cards_renders_heading_and_body_as_editable_text(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "four_cards_demo",
            "title": "Four Cards Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [_template_slide_payload("four_cards", 1)],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "four_cards.pptx")
    rendered_slide = Presentation(output_path).slides[0]
    editable_texts = [
        shape.text.strip()
        for shape in rendered_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]

    assert "Discover" in editable_texts
    assert "Map user needs" in editable_texts
    assert "01" in editable_texts
    assert "Action" in editable_texts
    assert not (INTERNAL_SURFACE_TERMS & set(editable_texts))
    assert "Prioritize" in editable_texts
    assert "Choose high-value work" in editable_texts


def test_template_rendering_does_not_expose_internal_surface_terms(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "surface_cleanup_demo",
            "title": "Surface Cleanup Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                _template_slide_payload(layout, index)
                for index, layout in enumerate(TEMPLATE_LAYOUTS, start=1)
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "surface_cleanup.pptx")
    visible_text = "\n".join(_visible_texts(Presentation(output_path)))

    for term in INTERNAL_SURFACE_TERMS:
        assert term not in visible_text
