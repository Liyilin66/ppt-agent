import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ppt_agent.load import load_deck
from ppt_agent.models import BBox, Deck, ImageElement, ShapeElement, TextElement


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _sample_deck_payload() -> dict:
    return json.loads((EXAMPLES_DIR / "sample_slide_ir.json").read_text(encoding="utf-8"))


def test_sample_deck_loads_from_json() -> None:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")

    assert deck.deck_id == "sample_clean_business_deck"
    assert deck.theme_name == "clean_business"
    assert deck.canvas_width_in == pytest.approx(13.333)
    assert deck.canvas_height_in == pytest.approx(7.5)
    assert len(deck.slides) == 3

    first_slide = deck.slides[0]
    assert first_slide.slide_id == "slide_001"
    assert first_slide.title == "Q3 Operating Review"
    assert first_slide.layout == "title"
    assert len(first_slide.elements) == 3
    assert isinstance(first_slide.elements[0], TextElement)
    assert isinstance(first_slide.elements[2], ShapeElement)

    third_slide = deck.slides[2]
    assert isinstance(third_slide.elements[1], ImageElement)
    assert third_slide.elements[1].bbox.width > 0


@pytest.mark.parametrize(
    "payload",
    [
        {"x": 0, "y": 0, "width": 0, "height": 1},
        {"x": 0, "y": 0, "width": 1, "height": -1},
    ],
)
def test_bbox_rejects_non_positive_size(payload: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        BBox.model_validate(payload)


def test_shape_style_rejects_invalid_fill_color() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"][2]["style"]["fill_color"] = "blue"

    with pytest.raises(ValidationError) as exc_info:
        Deck.model_validate(payload)

    message = str(exc_info.value)
    assert "fill_color" in message
    assert "pattern" in message


def test_slide_requires_at_least_one_element() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"] = []

    with pytest.raises(ValidationError) as exc_info:
        Deck.model_validate(payload)

    message = str(exc_info.value)
    assert "elements" in message
    assert "at least 1" in message


def test_deck_rejects_duplicate_slide_ids() -> None:
    payload = _sample_deck_payload()
    payload["slides"][1]["slide_id"] = payload["slides"][0]["slide_id"]

    with pytest.raises(ValidationError) as exc_info:
        Deck.model_validate(payload)

    message = str(exc_info.value)
    assert "slide_id values must be unique" in message
    assert "'slide_001'" in message


def test_slide_rejects_duplicate_element_ids() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"][1]["element_id"] = "s1_title"

    with pytest.raises(ValidationError) as exc_info:
        Deck.model_validate(payload)

    message = str(exc_info.value)
    assert "Slide 'slide_001' element_id values must be unique" in message
    assert "'s1_title'" in message


def test_deck_rejects_element_bbox_beyond_canvas_width() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"][0]["bbox"] = {
        "x": 12.5,
        "y": 1.2,
        "width": 1.0,
        "height": 0.8,
    }

    with pytest.raises(ValidationError) as exc_info:
        Deck.model_validate(payload)

    message = str(exc_info.value)
    assert "Element 's1_title' on slide 'slide_001' exceeds canvas width" in message
    assert "bbox.x + bbox.width" in message


def test_deck_rejects_element_bbox_beyond_canvas_height() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"][0]["bbox"] = {
        "x": 0.85,
        "y": 7.2,
        "width": 8.8,
        "height": 0.8,
    }

    with pytest.raises(ValidationError) as exc_info:
        Deck.model_validate(payload)

    message = str(exc_info.value)
    assert "Element 's1_title' on slide 'slide_001' exceeds canvas height" in message
    assert "bbox.y + bbox.height" in message
