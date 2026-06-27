"""LangChain structured-output deck generation."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ppt_agent.models import Deck, StrictModel
from ppt_agent.qa import QAReport, analyze_deck
from ppt_agent.theme import Theme


class DeckGenerationRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=1, le=10)
    style: str | None = Field(default=None, min_length=1)
    language: str = Field(default="en", min_length=1)
    key_points: list[str] = Field(default_factory=list)


class GenerationAttempt(StrictModel):
    attempt_index: int = Field(..., ge=1)
    deck: Deck
    qa_report: QAReport
    accepted: bool


class GenerationResult(StrictModel):
    deck: Deck
    qa_report: QAReport
    attempts: list[GenerationAttempt] = Field(..., min_length=1)
    accepted: bool


def _format_qa_feedback(qa_feedback: QAReport | None) -> str:
    if qa_feedback is None:
        return ""

    issue_lines = [
        f"- {issue.code}: {issue.message}"
        for issue in qa_feedback.issues
    ]
    issues = "\n".join(issue_lines) or "- No specific issues were reported, but improve the deck quality."

    return f"""

QA feedback from the previous attempt:
- Previous QA score: {qa_feedback.score}
- Issues:
{issues}

Avoid repeating these QA problems in the next Deck IR. Improve layout quality while keeping all schema and bbox rules valid.
"""


def build_generation_prompt(request: DeckGenerationRequest, qa_feedback: QAReport | None = None) -> str:
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
{_format_qa_feedback(qa_feedback)}
"""


def _unwrap_structured_response(response: Any) -> Any:
    if isinstance(response, dict) and "structured_response" in response:
        return response["structured_response"]
    return response


def generate_deck_with_model(
    model: Any,
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
) -> Deck:
    """Generate a Deck using a LangChain chat model with structured output."""

    prompt = build_generation_prompt(request, qa_feedback=qa_feedback)
    structured_model = model.with_structured_output(Deck)
    response = _unwrap_structured_response(structured_model.invoke(prompt))

    if isinstance(response, Deck):
        return Deck.model_validate(response.model_dump(mode="json"))

    return Deck.model_validate(response)


def generate_deck_with_quality_gate(
    model: Any,
    request: DeckGenerationRequest,
    theme: Theme | None = None,
    min_score: int = 80,
    max_attempts: int = 2,
) -> GenerationResult:
    """Generate Deck IR and retry when deterministic QA does not meet the score gate."""

    if not 0 <= min_score <= 100:
        raise ValueError("min_score must be between 0 and 100.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    attempts: list[GenerationAttempt] = []
    qa_feedback: QAReport | None = None

    for attempt_index in range(1, max_attempts + 1):
        deck = generate_deck_with_model(model, request, qa_feedback=qa_feedback)
        qa_report = analyze_deck(deck, theme)
        accepted = qa_report.score >= min_score
        attempts.append(
            GenerationAttempt(
                attempt_index=attempt_index,
                deck=deck,
                qa_report=qa_report,
                accepted=accepted,
            )
        )

        if accepted:
            return GenerationResult(
                deck=deck,
                qa_report=qa_report,
                attempts=attempts,
                accepted=True,
            )

        qa_feedback = qa_report

    last_attempt = attempts[-1]
    return GenerationResult(
        deck=last_attempt.deck,
        qa_report=last_attempt.qa_report,
        attempts=attempts,
        accepted=False,
    )
