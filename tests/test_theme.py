from pathlib import Path

import pytest
from pydantic import ValidationError

from ppt_agent.load import load_theme
from ppt_agent.theme import Theme


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def test_sample_theme_loads_from_json() -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")

    assert theme.name == "clean_business"
    assert theme.slide_size.width_in == pytest.approx(13.333)
    assert theme.slide_size.height_in == pytest.approx(7.5)
    assert theme.colors.primary == "#2563EB"
    assert theme.fonts.heading == "Aptos Display"
    assert theme.default_text_style.font_size_pt == 18


def test_theme_rejects_invalid_color() -> None:
    payload = {
        "name": "bad_theme",
        "colors": {
            "background": "white",
            "surface": "#F9FAFB",
            "text": "#111827",
            "muted_text": "#4B5563",
            "primary": "#2563EB",
            "secondary": "#0F766E",
            "accent": "#F59E0B",
        },
        "fonts": {
            "heading": "Aptos Display",
            "body": "Aptos",
        },
    }

    with pytest.raises(ValidationError):
        Theme.model_validate(payload)
