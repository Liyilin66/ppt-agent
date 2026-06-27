from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from ppt_agent.load import load_deck, load_theme
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
