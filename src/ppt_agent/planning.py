"""Deck-level planning primitives for generation prompts."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.models import StrictModel


SupportedPlanLayout = Literal[
    "title_slide",
    "section_divider",
    "two_column",
    "three_column",
    "four_cards",
    "metric_cards",
    "closing_slide",
]


class SlidePlan(StrictModel):
    slide_index: int = Field(..., ge=1)
    slide_role: str = Field(..., min_length=1)
    key_message: str = Field(..., min_length=1)
    content_goal: str = Field(..., min_length=1)
    recommended_layout: SupportedPlanLayout
    must_not_repeat: list[str] = Field(default_factory=list)


class DeckPlan(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=1, le=10)
    slides: list[SlidePlan] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_slide_plan_relationships(self) -> Self:
        actual_count = len(self.slides)
        if actual_count != self.slide_count:
            raise ValueError(
                f"DeckPlan has {actual_count} slides, but slide_count is {self.slide_count}."
            )

        expected_indexes = list(range(1, self.slide_count + 1))
        actual_indexes = [slide.slide_index for slide in self.slides]
        if actual_indexes != expected_indexes:
            raise ValueError(
                "DeckPlan slide_index values must be consecutive from 1 to slide_count; "
                f"got {actual_indexes}."
            )

        return self


def _brief_value(brief: Any, field_name: str, fallback: str = "") -> Any:
    return getattr(brief, field_name, fallback)


def _format_brief_list(value: Any) -> str:
    if not value:
        return "- None provided"
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return f"- {value}"


def build_deck_plan_prompt(brief: Any) -> str:
    """Build the planning prompt from a DeckBrief-like object."""

    slide_count = _brief_value(brief, "slide_count")
    layouts = ", ".join(TEMPLATE_LAYOUTS)
    return f"""Create a DeckPlan as structured data before generating Slide IR.

Brief:
- Topic: {_brief_value(brief, "topic")}
- Audience: {_brief_value(brief, "audience")}
- Slide count: {slide_count}
- Language: {_brief_value(brief, "language")}
- Purpose: {_brief_value(brief, "purpose", "Not specified") or "Not specified"}
- Tone: {_brief_value(brief, "tone", "Not specified") or "Not specified"}
- Visual style: {_brief_value(brief, "visual_style", "Not specified") or "Not specified"}
- Content focus: {_brief_value(brief, "content_focus", "Not specified") or "Not specified"}
- Must include:
{_format_brief_list(_brief_value(brief, "must_include", []))}
- Must avoid:
{_format_brief_list(_brief_value(brief, "must_avoid", []))}
- Raw user requirements: {_brief_value(brief, "user_requirements_raw", None) or "None provided"}

Planning rules:
- Return only structured data that validates as DeckPlan.
- Plan exactly {slide_count} slides.
- Each slide must have one unique key_message.
- Avoid repeated key_message values across slides.
- Every slide needs a distinct slide_role and content_goal.
- recommended_layout must be one of: {layouts}.
- For 3-slide short decks, do not prioritize section_divider.
- Use section_divider only when it creates real story structure, especially in decks with 5 or more slides.
- For 8-slide decks, use layout diversity: mix at least three useful layouts when the content allows it.
- Avoid repeating content listed in each slide's must_not_repeat.
- Keep the plan concise enough that the generator can follow it exactly.
"""


def generate_deck_plan_with_model(model: Any, brief: Any) -> DeckPlan:
    """Generate a DeckPlan using a LangChain-compatible structured-output model."""

    prompt = build_deck_plan_prompt(brief)
    structured_model = model.with_structured_output(DeckPlan)
    response = structured_model.invoke(prompt)
    if isinstance(response, dict) and "structured_response" in response:
        response = response["structured_response"]
    return DeckPlan.model_validate(response)
