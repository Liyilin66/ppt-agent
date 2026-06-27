"""JSON loading helpers for Slide IR and theme files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ppt_agent.models import Deck
from ppt_agent.patch import SlidePatch
from ppt_agent.theme import Theme


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def load_deck(path: str | Path) -> Deck:
    """Load and validate a deck JSON file."""

    return Deck.model_validate(_load_json(path))


def load_theme(path: str | Path) -> Theme:
    """Load and validate a theme JSON file."""

    return Theme.model_validate(_load_json(path))


def load_patch(path: str | Path) -> SlidePatch:
    """Load and validate a structured patch JSON file."""

    return SlidePatch.model_validate(_load_json(path))
