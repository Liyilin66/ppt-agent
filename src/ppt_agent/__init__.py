"""Core package for ppt-agent milestones."""

from ppt_agent.export import write_model_json
from ppt_agent.load import load_deck, load_patch, load_theme
from ppt_agent.models import (
    BBox,
    Deck,
    Element,
    ImageElement,
    ShapeStyle,
    ShapeElement,
    Slide,
    TextElement,
    TextStyle,
)
from ppt_agent.patch import PatchIssue, PatchOperation, PatchResult, SlidePatch, apply_patch
from ppt_agent.qa import QAIssue, QAReport, analyze_deck
from ppt_agent.renderer import render_deck_to_pptx
from ppt_agent.theme import Theme

__all__ = [
    "BBox",
    "Deck",
    "Element",
    "ImageElement",
    "ShapeStyle",
    "ShapeElement",
    "Slide",
    "TextElement",
    "TextStyle",
    "Theme",
    "SlidePatch",
    "PatchIssue",
    "PatchOperation",
    "PatchResult",
    "apply_patch",
    "write_model_json",
    "QAIssue",
    "QAReport",
    "analyze_deck",
    "load_deck",
    "load_patch",
    "load_theme",
    "render_deck_to_pptx",
]

__version__ = "0.1.0"
