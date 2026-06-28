"""LangChain structured-output deck generation."""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import Field

from ppt_agent.layouts import TEMPLATE_LAYOUTS
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


def _format_generation_feedback(generation_feedback: str | None) -> str:
    if generation_feedback is None:
        return ""

    return f"""

Generation feedback from the previous attempt:
- {generation_feedback}

Regenerate the Deck IR and fix this issue before optimizing style.
"""


def build_generation_prompt(
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
    generation_feedback: str | None = None,
) -> str:
    key_points = "\n".join(f"- {point}" for point in request.key_points) or "- None provided"
    style = request.style or "clean_business"
    layouts = ", ".join(TEMPLATE_LAYOUTS)

    return f"""Generate a Slide IR deck as structured data that exactly matches the Deck Pydantic schema.

Request:
- Topic: {request.topic}
- Audience: {request.audience}
- Slide count: {request.slide_count} exactly
- Style: {style}
- Language: {request.language}
- Key points:
{key_points}

Hard schema and layout rules:
- Return only structured data that can be validated as Deck.
- Do not generate Markdown, prose, speaker notes, PPTX, HTML, SVG, or images.
- Required root fields: deck_id, title, canvas_width_in, canvas_height_in, slides.
- The slides array length must be exactly {request.slide_count}. Do not generate more or fewer slides.
- Set deck.canvas_width_in to 13.333 and deck.canvas_height_in to 7.5 unless there is a strong reason not to.
- Use bbox coordinates and sizes in PowerPoint-style inches, not pixels.
- Choose each slide.layout from these controlled layouts only: {layouts}.
- Prefer this general sequence when it fits the requested deck: title_slide, two_column/three_column/four_cards/metric_cards, closing_slide.
- For a 3-slide deck, prefer:
  slide 1: title_slide.
  slide 2: two_column, three_column, four_cards, or metric_cards.
  slide 3: closing_slide, two_column, or four_cards.
- Do not use section_divider by default in a short 3-slide deck.
- Use section_divider only for decks with 5 or more slides, or when the user explicitly asks for section divider pages.
- Use four_cards for four parallel concepts, four steps, four capabilities, or four recommendations.
- Do not rely on freeform bbox placement for visual design. The renderer will apply deterministic template positions and styles.
- Focus on semantic content: slide titles, concise section text, column content, metric labels/values, and closing message.
- Match each slide's content to its chosen layout. Do not create empty cards or placeholder-only cards.
- Card text should be short phrases or compact sentences, not long paragraphs.
- Still include valid bbox values for schema compatibility, but keep them simple and inside the canvas; template rendering may ignore the exact bbox.
- Every slide must include slide_id, title, layout, and at least one element.
- slide_id values must be unique across the deck.
- Every element must include element_id, type, bbox, and type-specific fields.
- element_id values must be unique within each slide.
- Supported element types are text, shape, and image.
- For template-guided slides, make the first text element the primary slide title and subsequent text elements the body/columns/cards in reading order.
- Keep generated text compact:
  title <= 9 words.
  subtitle <= 16 words.
  card heading <= 4 words.
  card body <= 18 words.
- Avoid paragraph-style body text. Prefer phrases, short sentences, and short bullets.
- For card content, use this format whenever possible:
  Heading
  Short body sentence.
- For text elements, include text and optional TextStyle with these exact fields only: font_family, font_size_pt, color, bold, italic.
- For shape elements, include shape as rectangle, ellipse, or line, plus optional ShapeStyle with these exact fields only: fill_color, stroke_color, stroke_width_pt.
- For shape stroke_width_pt, omit the field when there is no stroke. If present, stroke_width_pt must be greater than 0; never use 0.
- For image elements, include a non-empty src and optional alt_text. Use placeholders only as IR image elements.
- Never use font_size; use font_size_pt.
- Never use line_color; use stroke_color.
- Do not create any bbox that extends outside the slide canvas:
  bbox.x + bbox.width must be <= canvas_width_in.
  bbox.y + bbox.height must be <= canvas_height_in.
- bbox.width and bbox.height must be positive.
- Keep each slide simple, with roughly 2 to 5 elements, to avoid dense layouts.
- Prefer readable business-style layouts with clear titles and generous whitespace.
- Generate exactly {request.slide_count} slides.
{_format_qa_feedback(qa_feedback)}
{_format_generation_feedback(generation_feedback)}
"""


def _unwrap_structured_response(response: Any) -> Any:
    if isinstance(response, dict) and "structured_response" in response:
        return response["structured_response"]
    return response


def _identifier_from_text(value: str, prefix: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return f"{prefix}_{slug or 'deck'}"


def _normalize_layout_alias(layout: Any, slide_index: int, slide_count: int) -> str:
    if isinstance(layout, str):
        normalized = layout.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "title": "title_slide",
            "cover": "title_slide",
            "cover_slide": "title_slide",
            "section": "section_divider",
            "section_list": "section_divider",
            "section_title": "section_divider",
            "two_columns": "two_column",
            "two_col": "two_column",
            "three_columns": "three_column",
            "three_col": "three_column",
            "four_card": "four_cards",
            "four_cards": "four_cards",
            "four_column": "four_cards",
            "four_columns": "four_cards",
            "four_steps": "four_cards",
            "metrics": "metric_cards",
            "metric_card": "metric_cards",
            "kpi_cards": "metric_cards",
            "summary": "closing_slide",
            "closing": "closing_slide",
            "final": "closing_slide",
        }
        candidate = aliases.get(normalized, normalized)
        if candidate in TEMPLATE_LAYOUTS:
            return candidate

    if slide_index == 1:
        return "title_slide"
    if slide_index == slide_count:
        return "closing_slide"
    return "two_column"


def _normalize_style_aliases(style: Any, element_type: Any) -> Any:
    if not isinstance(style, dict):
        return style

    normalized = dict(style)
    if element_type == "text":
        if "font_size" in normalized and "font_size_pt" not in normalized:
            normalized["font_size_pt"] = normalized["font_size"]
        normalized.pop("font_size", None)

    if element_type == "shape":
        if "line_color" in normalized and "stroke_color" not in normalized:
            normalized["stroke_color"] = normalized["line_color"]
        if "line_width" in normalized and "stroke_width_pt" not in normalized:
            normalized["stroke_width_pt"] = normalized["line_width"]
        normalized.pop("line_color", None)
        normalized.pop("line_width", None)
        if normalized.get("stroke_width_pt") in (0, 0.0, "0", "0.0"):
            normalized.pop("stroke_width_pt", None)

    return normalized


def _normalize_deck_payload(response: Any, request: DeckGenerationRequest) -> Any:
    if isinstance(response, Deck):
        return response.model_dump(mode="json")
    if not isinstance(response, dict):
        return response

    payload = copy.deepcopy(response)
    payload.setdefault("deck_id", _identifier_from_text(request.topic, "generated"))
    payload.setdefault("title", request.topic)
    if request.style is not None:
        payload.setdefault("theme_name", request.style)

    slides = payload.get("slides")
    if not isinstance(slides, list):
        return payload

    for slide_index, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue

        slide.setdefault("slide_id", f"slide_{slide_index:03d}")
        slide.setdefault("title", f"{request.topic} {slide_index}")
        slide["layout"] = _normalize_layout_alias(slide.get("layout"), slide_index, len(slides))

        elements = slide.get("elements")
        if not isinstance(elements, list):
            continue

        for element_index, element in enumerate(elements, start=1):
            if not isinstance(element, dict):
                continue

            element.setdefault("element_id", f"s{slide_index:03d}_e{element_index:02d}")
            element["style"] = _normalize_style_aliases(element.get("style"), element.get("type"))

    return payload


def _ensure_slide_count(deck: Deck, request: DeckGenerationRequest) -> Deck:
    actual_count = len(deck.slides)
    if actual_count != request.slide_count:
        raise ValueError(
            f"Generated Deck has {actual_count} slides, but request.slide_count is {request.slide_count}. "
            f"Regenerate exactly {request.slide_count} slides."
        )
    return deck


def generate_deck_with_model(
    model: Any,
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
    generation_feedback: str | None = None,
) -> Deck:
    """Generate a Deck using a LangChain chat model with structured output."""

    prompt = build_generation_prompt(
        request,
        qa_feedback=qa_feedback,
        generation_feedback=generation_feedback,
    )
    structured_model = model.with_structured_output(Deck)
    response = _unwrap_structured_response(structured_model.invoke(prompt))

    deck = Deck.model_validate(_normalize_deck_payload(response, request))
    return _ensure_slide_count(deck, request)


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
    generation_feedback: str | None = None

    for attempt_index in range(1, max_attempts + 1):
        try:
            deck = generate_deck_with_model(
                model,
                request,
                qa_feedback=qa_feedback,
                generation_feedback=generation_feedback,
            )
        except ValueError as exc:
            generation_feedback = str(exc)
            qa_feedback = None
            if attempt_index == max_attempts:
                raise ValueError(
                    f"Deck generation failed after {max_attempts} attempt(s): {generation_feedback}"
                ) from exc
            continue

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
        generation_feedback = None

    last_attempt = attempts[-1]
    return GenerationResult(
        deck=last_attempt.deck,
        qa_report=last_attempt.qa_report,
        attempts=attempts,
        accepted=False,
    )
