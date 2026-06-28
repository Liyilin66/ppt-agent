"""Deck-level planning primitives for generation prompts."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from ppt_agent.design import DesignSpec, SlideRole, get_layout_contract, list_layout_contracts
from ppt_agent.models import StrictModel


SLIDE_ROLES: tuple[str, ...] = (
    "cover",
    "context",
    "comparison",
    "framework",
    "process",
    "metrics",
    "risk",
    "summary",
)


class SlidePlan(StrictModel):
    slide_index: int = Field(..., ge=1)
    slide_role: SlideRole
    key_message: str = Field(..., min_length=1)
    content_goal: str = Field(..., min_length=1)
    recommended_layout: str = Field(..., min_length=1)
    content_items: int | None = Field(default=None, ge=0)
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

        for slide in self.slides:
            try:
                contract = get_layout_contract(slide.recommended_layout)
            except ValueError as exc:
                raise ValueError(
                    f"Slide {slide.slide_index} uses unsupported recommended_layout "
                    f"'{slide.recommended_layout}'. {exc}"
                ) from exc

            if slide.content_items is None:
                continue

            if not contract.min_items <= slide.content_items <= contract.max_items:
                raise ValueError(
                    f"Slide {slide.slide_index} content_items={slide.content_items} "
                    f"does not fit layout '{contract.layout_name}' capacity "
                    f"{contract.min_items}-{contract.max_items}."
                )

        return self


DECK_PLAN_STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "title": "DeckPlan",
    "description": "Deck-level plan generated before Slide IR. Extra provider fields are normalized locally.",
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "audience": {"type": "string"},
        "slide_count": {"type": "integer"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slide_index": {"type": "integer"},
                    "slide_number": {"type": "integer"},
                    "slide_role": {"type": "string"},
                    "key_message": {"type": "string"},
                    "content_goal": {"type": "string"},
                    "recommended_layout": {"type": "string"},
                    "content_items": {"type": "integer"},
                    "must_not_repeat": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "deck_plan": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": True,
}


def _normalize_deck_plan_payload(response: Any, brief: Any) -> Any:
    if isinstance(response, DeckPlan):
        return response
    if not isinstance(response, dict):
        return response

    allowed_plan_fields = set(DeckPlan.model_fields)
    payload = {
        field_name: field_value
        for field_name, field_value in response.items()
        if field_name in allowed_plan_fields
    }
    payload.setdefault("topic", _brief_value(brief, "topic"))
    payload.setdefault("audience", _brief_value(brief, "audience"))
    payload.setdefault("slide_count", _brief_value(brief, "slide_count"))

    slides = payload.get("slides")
    if isinstance(slides, list):
        allowed_slide_fields = set(SlidePlan.model_fields)
        normalized_slides: list[Any] = []
        for index, slide in enumerate(slides, start=1):
            if not isinstance(slide, dict):
                normalized_slides.append(slide)
                continue

            normalized_slide = {
                field_name: field_value
                for field_name, field_value in slide.items()
                if field_name in allowed_slide_fields
            }
            if "slide_index" not in normalized_slide:
                normalized_slide["slide_index"] = slide.get("slide_number", index)
            normalized_slides.append(normalized_slide)

        payload["slides"] = normalized_slides

    return payload


def _brief_value(brief: Any, field_name: str, fallback: str = "") -> Any:
    return getattr(brief, field_name, fallback)


def _format_brief_list(value: Any) -> str:
    if not value:
        return "- None provided"
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return f"- {value}"


def _format_design_spec(spec: DesignSpec) -> str:
    accent_color = spec.accent_color or "None"
    return "\n".join(
        [
            f"- theme_name: {spec.theme_name}",
            f"- visual_tone: {spec.visual_tone}",
            f"- density_level: {spec.density_level}",
            f"- font_scale: {spec.font_scale}",
            f"- accent_color: {accent_color}",
            f"- background_style: {spec.background_style}",
        ]
    )


def _format_layout_contracts() -> str:
    lines: list[str] = []
    for contract in list_layout_contracts():
        best_for = ", ".join(contract.best_for)
        required = ", ".join(contract.required_slots)
        optional = ", ".join(contract.optional_slots) or "none"
        avoid = ", ".join(contract.avoid_when) or "none"
        lines.append(
            "- "
            f"{contract.layout_name}: best_for={best_for}; "
            f"min_items={contract.min_items}; max_items={contract.max_items}; "
            f"required_slots={required}; optional_slots={optional}; avoid_when={avoid}"
        )
    return "\n".join(lines)


def _format_role_layout_guidance() -> str:
    return "\n".join(
        [
            "- comparison: prefer comparison_matrix for two-option, before/after, or normal AI vs Agent comparisons.",
            "- process: prefer process_flow for workflows, pipelines, or step-by-step sequences with 3-5 steps.",
            "- risk: prefer risk_matrix for risk / impact / mitigation or governance content with 3-4 risks.",
            "- summary: prefer key_takeaway for strong conclusions, action checklists, or pre-closing summary pages.",
            "- cover still uses title_slide; final thank-you pages can still use closing_slide.",
        ]
    )


def build_deck_plan_prompt(brief: Any) -> str:
    """Build the planning prompt from a DeckBrief-like object."""

    slide_count = _brief_value(brief, "slide_count")
    design_spec = DesignSpec()
    layout_names = ", ".join(contract.layout_name for contract in list_layout_contracts())
    slide_roles = ", ".join(SLIDE_ROLES)
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

Default DesignSpec guidance:
{_format_design_spec(design_spec)}

LayoutContract registry:
{_format_layout_contracts()}

SlideRole to layout guidance:
{_format_role_layout_guidance()}

Planning rules:
- Return only structured data that validates as DeckPlan.
- Plan exactly {slide_count} slides.
- Each slide must have one unique key_message.
- Avoid repeated key_message values across slides.
- Every slide must set slide_role to one of: {slide_roles}.
- Every slide needs a distinct slide_role, content_goal, and key_message.
- recommended_layout must be one of the LayoutContract registry layout_name values only: {layout_names}.
- Choose a recommended_layout that naturally matches the slide_role and content_goal.
- If slide_role is comparison, process, risk, or summary, prefer the matching professional layout when the content fits.
- Set content_items to the estimated number of major content blocks, excluding the slide title.
- Do not let content_items exceed the selected layout max_items.
- For 3-slide short decks, do not prioritize section_divider.
- Use section_divider only when it creates real story structure, especially in decks with 5 or more slides.
- For long decks, keep layout diversity; for 8-slide decks, mix at least three useful layouts when the content allows it.
- Avoid repeating content listed in each slide's must_not_repeat.
- Keep the plan concise enough that the generator can follow it exactly.
"""


def generate_deck_plan_with_model(model: Any, brief: Any) -> DeckPlan:
    """Generate a DeckPlan using a LangChain-compatible structured-output model."""

    prompt = build_deck_plan_prompt(brief)
    structured_model = model.with_structured_output(DECK_PLAN_STRUCTURED_OUTPUT_SCHEMA)
    response = structured_model.invoke(prompt)
    if isinstance(response, dict) and "structured_response" in response:
        response = response["structured_response"]
    if isinstance(response, dict) and "deck_plan" in response:
        response = response["deck_plan"]
    response = _normalize_deck_plan_payload(response, brief)
    return DeckPlan.model_validate(response)
