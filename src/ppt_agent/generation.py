"""LangChain structured-output deck generation."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ppt_agent.models import Deck, StrictModel


class DeckGenerationRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=1, le=10)
    style: str | None = Field(default=None, min_length=1)
    language: str = Field(default="en", min_length=1)
    key_points: list[str] = Field(default_factory=list)


def build_generation_prompt(request: DeckGenerationRequest) -> str:
    key_points = "\n".join(f"- {point}" for point in request.key_points) or "- None provided"
    style = request.style or "clean_business"

    return f"""Generate a Slide IR deck as structured data that exactly matches the Deck Pydantic schema.

Request:
- Topic: {request.topic}
- Audience: {request.audience}
- Slide count: {request.slide_count}
- Style: {style}
- Language: {request.language}
- Key points:
{key_points}

Hard schema and layout rules:
- Return only structured data that can be validated as Deck.
- Do not generate Markdown, prose, speaker notes, PPTX, HTML, SVG, or images.
- Set deck.canvas_width_in to 13.333 and deck.canvas_height_in to 7.5 unless there is a strong reason not to.
- Use bbox coordinates and sizes in PowerPoint-style inches, not pixels.
- Every slide must include slide_id, title, layout, and at least one element.
- slide_id values must be unique across the deck.
- Every element must include element_id, type, bbox, and type-specific fields.
- element_id values must be unique within each slide.
- Supported element types are text, shape, and image.
- For text elements, include text and optional TextStyle.
- For shape elements, include shape as rectangle, ellipse, or line, plus optional ShapeStyle.
- For image elements, include a non-empty src and optional alt_text. Use placeholders only as IR image elements.
- Do not create any bbox that extends outside the slide canvas:
  bbox.x + bbox.width must be <= canvas_width_in.
  bbox.y + bbox.height must be <= canvas_height_in.
- bbox.width and bbox.height must be positive.
- Keep each slide simple, with roughly 2 to 5 elements, to avoid dense layouts.
- Prefer readable business-style layouts with clear titles and generous whitespace.
- Generate exactly {request.slide_count} slides.
"""


def _unwrap_structured_response(response: Any) -> Any:
    if isinstance(response, dict) and "structured_response" in response:
        return response["structured_response"]
    return response


def generate_deck_with_model(model: Any, request: DeckGenerationRequest) -> Deck:
    """Generate a Deck using a LangChain chat model with structured output."""

    prompt = build_generation_prompt(request)
    structured_model = model.with_structured_output(Deck)
    response = _unwrap_structured_response(structured_model.invoke(prompt))

    if isinstance(response, Deck):
        return Deck.model_validate(response.model_dump(mode="json"))

    return Deck.model_validate(response)
