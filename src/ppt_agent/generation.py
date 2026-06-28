"""LangChain structured-output deck generation."""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import Field

from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.models import Deck, StrictModel
from ppt_agent.planning import DeckPlan, generate_deck_plan_with_model
from ppt_agent.qa import QAReport, analyze_deck
from ppt_agent.theme import Theme


DEFAULT_LANGUAGE = "zh-CN"
MAX_SINGLE_GENERATION_SLIDES = 4
MAX_QA_FEEDBACK_ISSUES = 8


QA_FEEDBACK_FIX_INSTRUCTIONS = {
    "layout_diversity_low": (
        "Use at least 3 different content layouts across long decks; avoid relying only on card layouts."
    ),
    "layout_repetition_run": "Do not use the same content layout for 3 consecutive slides.",
    "adjacent_title_similarity": "Make adjacent slide titles and key messages clearly distinct.",
}


class DeckBrief(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=1, le=10)
    language: str = Field(default=DEFAULT_LANGUAGE, min_length=1)
    purpose: str = ""
    tone: str = ""
    visual_style: str = ""
    content_focus: str = ""
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    user_requirements_raw: str | None = Field(default=None, min_length=1)


BRIEF_STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "title": "DeckBrief",
    "description": "Structured brief extracted from detailed presentation requirements.",
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "audience": {"type": "string"},
        "slide_count": {"type": "integer"},
        "language": {"type": "string"},
        "purpose": {"type": "string"},
        "tone": {"type": "string"},
        "visual_style": {"type": "string"},
        "content_focus": {"type": "string"},
        "must_include": {"type": "array", "items": {"type": "string"}},
        "must_avoid": {"type": "array", "items": {"type": "string"}},
        "user_requirements_raw": {"type": "string"},
    },
    "required": ["topic", "audience", "slide_count"],
    "additionalProperties": True,
}


class DeckGenerationRequest(StrictModel):
    topic: str = Field(..., min_length=1)
    audience: str = Field(..., min_length=1)
    slide_count: int = Field(..., ge=1, le=10)
    style: str | None = Field(default=None, min_length=1)
    language: str = Field(default=DEFAULT_LANGUAGE, min_length=1)
    key_points: list[str] = Field(default_factory=list)
    user_requirements: str | None = Field(default=None, min_length=1)
    brief: DeckBrief | None = None


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
    deck_plan: DeckPlan | None = None


def format_qa_feedback_for_generation(qa_report: QAReport) -> str:
    issue_lines: list[str] = []
    limited_issues = qa_report.issues[:MAX_QA_FEEDBACK_ISSUES]
    for issue in limited_issues:
        location = f"slide={issue.slide_id}"
        if issue.element_id is not None:
            location = f"{location}, element={issue.element_id}"
        issue_lines.append(
            f"- [{issue.severity}] {issue.code} ({location}): {issue.message}"
        )
        fix_instruction = QA_FEEDBACK_FIX_INSTRUCTIONS.get(issue.code)
        if fix_instruction is not None:
            issue_lines.append(f"  Fix: {fix_instruction}")

    if len(qa_report.issues) > MAX_QA_FEEDBACK_ISSUES:
        issue_lines.append(
            f"- Showing first {MAX_QA_FEEDBACK_ISSUES} of {len(qa_report.issues)} QA issues. "
            "Fix these first before making cosmetic changes."
        )

    issues = "\n".join(issue_lines) or "- No specific issues were reported, but improve the deck quality."

    return f"""- Previous QA score: {qa_report.score}
- Issues:
{issues}"""


def _format_qa_feedback(qa_feedback: QAReport | None) -> str:
    if qa_feedback is None:
        return ""

    return f"""

QA feedback from the previous attempt:
{format_qa_feedback_for_generation(qa_feedback)}

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


def _format_deck_plan(deck_plan: DeckPlan | None) -> str:
    if deck_plan is None:
        return ""

    slide_lines: list[str] = []
    for slide in deck_plan.slides:
        must_not_repeat = ", ".join(slide.must_not_repeat) or "None"
        slide_lines.append(
            "\n".join(
                [
                    f"- Slide {slide.slide_index}:",
                    f"  role: {slide.slide_role}",
                    f"  key_message: {slide.key_message}",
                    f"  content_goal: {slide.content_goal}",
                    f"  recommended_layout: {slide.recommended_layout}",
                    f"  must_not_repeat: {must_not_repeat}",
                ]
            )
        )

    slides_text = "\n".join(slide_lines)

    return f"""

DeckPlan guidance:
- Follow this deck-level plan when generating Deck IR.
- slide.title, slide.layout, and slide content must align with each slide's key_message and content_goal.
- Use each slide's recommended_layout as the slide.layout unless schema repair is absolutely required.
- Do not repeat any topic listed in must_not_repeat for that slide.
- Preserve distinct slide roles so the deck does not repeat the same point.
{slides_text}
"""


def _brief_from_request(request: DeckGenerationRequest) -> DeckBrief:
    return request.brief or DeckBrief(
        topic=request.topic,
        audience=request.audience,
        slide_count=request.slide_count,
        language=request.language,
        visual_style=request.style or "",
        content_focus="\n".join(request.key_points),
        must_include=list(request.key_points),
        user_requirements_raw=request.user_requirements,
    )


def _format_brief_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- None provided"


def _stringify_brief_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "；".join(str(item) for item in value if item is not None)
    return str(value)


def _string_list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(value)]


def _language_instruction(language: str) -> str:
    normalized = language.strip().lower()
    if normalized.startswith("en") or "english" in normalized:
        return (
            "The user explicitly requested English. Generate all user-visible slide text "
            "in concise English."
        )

    return (
        "Default to Simplified Chinese. Unless the user explicitly requested English, "
        "generate all user-visible slide text in natural Chinese, including deck title, "
        "slide titles, body text, card headings, metric labels, and closing slide. "
        "Do not mix meaningless English template words into the deck."
    )


def build_generation_prompt(
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
    generation_feedback: str | None = None,
    segment_instruction: str | None = None,
    deck_plan: DeckPlan | None = None,
) -> str:
    key_points = "\n".join(f"- {point}" for point in request.key_points) or "- None provided"
    style = request.style or "clean_business"
    layouts = ", ".join(TEMPLATE_LAYOUTS)
    brief = _brief_from_request(request)

    return f"""Generate a Slide IR deck as structured data that exactly matches the Deck Pydantic schema.

Request:
- Topic: {request.topic}
- Audience: {request.audience}
- Slide count: {request.slide_count} exactly
- Style: {style}
- Language: {request.language}
- Key points:
{key_points}

DeckBrief:
- Topic: {brief.topic}
- Audience: {brief.audience}
- Slide count: {brief.slide_count} exactly
- Language: {brief.language}
- Purpose: {brief.purpose or "Not specified"}
- Tone: {brief.tone or "Not specified"}
- Visual style: {brief.visual_style or style}
- Content focus: {brief.content_focus or "Not specified"}
- Must include:
{_format_brief_items(brief.must_include)}
- Must avoid:
{_format_brief_items(brief.must_avoid)}
- Raw user requirements: {brief.user_requirements_raw or "None provided"}
{_format_deck_plan(deck_plan)}

Hard schema and layout rules:
- Return only structured data that can be validated as Deck.
- Do not generate Markdown, prose, speaker notes, PPTX, HTML, SVG, or images.
- {_language_instruction(brief.language)}
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
{segment_instruction or ""}
{_format_qa_feedback(qa_feedback)}
{_format_generation_feedback(generation_feedback)}
"""


def _unwrap_structured_response(response: Any) -> Any:
    if isinstance(response, dict) and "structured_response" in response:
        return response["structured_response"]
    return response


def _normalize_brief_payload(
    response: Any,
    *,
    topic: str,
    audience: str,
    slide_count: int,
    language: str,
    user_requirements: str,
) -> Any:
    if isinstance(response, DeckBrief):
        response = response.model_dump(mode="json")
    if not isinstance(response, dict):
        return response

    allowed_fields = set(DeckBrief.model_fields)
    normalized = {key: value for key, value in response.items() if key in allowed_fields}

    normalized.setdefault("topic", topic)
    normalized.setdefault("audience", audience)
    normalized.setdefault("language", language)
    normalized["slide_count"] = slide_count
    normalized["user_requirements_raw"] = user_requirements

    for field in ["topic", "audience", "language", "purpose", "tone", "visual_style", "content_focus"]:
        if field in normalized:
            normalized[field] = _stringify_brief_value(normalized[field])

    for field in ["must_include", "must_avoid"]:
        normalized[field] = _string_list_value(normalized.get(field))

    return normalized


def build_brief_from_user_prompt(
    model: Any,
    user_requirements: str,
    *,
    topic: str,
    audience: str,
    slide_count: int,
    style: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    key_points: list[str] | None = None,
) -> DeckBrief:
    """Extract a structured DeckBrief from detailed user requirements."""

    key_points_text = _format_brief_items(key_points or [])
    prompt = f"""Extract a DeckBrief for an AI presentation generation request.

Base fields:
- topic: {topic}
- audience: {audience}
- slide_count: {slide_count}
- style: {style or "Not specified"}
- requested_language: {language}
- key_points:
{key_points_text}

Detailed user requirements:
{user_requirements}

Rules:
- Return only structured data matching DeckBrief.
- Keep slide_count exactly {slide_count}; it is the product request value.
- Default language to zh-CN unless the detailed requirements explicitly ask for English.
- If the detailed requirements ask for English, set language to en.
- Extract purpose, tone, visual_style, content_focus, must_include, and must_avoid when present.
- Preserve the raw detailed request in user_requirements_raw.
"""
    structured_model = model.with_structured_output(BRIEF_STRUCTURED_OUTPUT_SCHEMA)
    response = _unwrap_structured_response(structured_model.invoke(prompt))
    normalized = _normalize_brief_payload(
        response,
        topic=topic,
        audience=audience,
        slide_count=slide_count,
        language=language,
        user_requirements=user_requirements,
    )
    brief = DeckBrief.model_validate(normalized)
    return brief.model_copy(
        update={
            "slide_count": slide_count,
            "user_requirements_raw": user_requirements,
        }
    )


def _request_with_brief(model: Any, request: DeckGenerationRequest) -> DeckGenerationRequest:
    if request.brief is not None or not request.user_requirements:
        return request

    brief = build_brief_from_user_prompt(
        model,
        request.user_requirements,
        topic=request.topic,
        audience=request.audience,
        slide_count=request.slide_count,
        style=request.style,
        language=request.language,
        key_points=request.key_points,
    )
    return request.model_copy(
        update={
            "brief": brief,
            "topic": brief.topic,
            "audience": brief.audience,
            "language": brief.language,
        }
    )


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


def _normalize_generated_layout(layout: Any, slide_index: int, slide_count: int) -> str:
    normalized = _normalize_layout_alias(layout, slide_index, slide_count)
    if normalized == "title_slide" and slide_index != 1:
        return "two_column"
    if normalized == "closing_slide" and slide_index != slide_count:
        return "two_column"
    return normalized


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


def _normalize_deck_payload(
    response: Any,
    request: DeckGenerationRequest,
    *,
    slide_index_offset: int = 0,
    total_slide_count: int | None = None,
    force_slide_ids: bool = False,
) -> Any:
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

    layout_slide_count = total_slide_count or len(slides)
    for slide_index, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue

        global_slide_index = slide_index_offset + slide_index
        if force_slide_ids:
            slide["slide_id"] = f"slide_{global_slide_index:03d}"
        else:
            slide.setdefault("slide_id", f"slide_{global_slide_index:03d}")
        slide.setdefault("title", f"{request.topic} {global_slide_index}")
        slide["layout"] = _normalize_generated_layout(
            slide.get("layout"),
            global_slide_index,
            layout_slide_count,
        )

        elements = slide.get("elements")
        if not isinstance(elements, list):
            continue

        for element_index, element in enumerate(elements, start=1):
            if not isinstance(element, dict):
                continue

            if force_slide_ids:
                element["element_id"] = f"s{global_slide_index:03d}_e{element_index:02d}"
            else:
                element.setdefault("element_id", f"s{global_slide_index:03d}_e{element_index:02d}")
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


def _generate_deck_once(
    model: Any,
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
    generation_feedback: str | None = None,
    segment_instruction: str | None = None,
    deck_plan: DeckPlan | None = None,
    slide_index_offset: int = 0,
    total_slide_count: int | None = None,
    force_slide_ids: bool = False,
) -> Deck:
    prompt = build_generation_prompt(
        request,
        qa_feedback=qa_feedback,
        generation_feedback=generation_feedback,
        segment_instruction=segment_instruction,
        deck_plan=deck_plan,
    )
    structured_model = model.with_structured_output(Deck)
    response = _unwrap_structured_response(structured_model.invoke(prompt))

    deck = Deck.model_validate(
        _normalize_deck_payload(
            response,
            request,
            slide_index_offset=slide_index_offset,
            total_slide_count=total_slide_count,
            force_slide_ids=force_slide_ids,
        )
    )
    return _ensure_slide_count(deck, request)


def _segment_instruction(start: int, count: int, total: int) -> str:
    end = start + count - 1
    first_layout = "title_slide" if start == 1 else "two_column, three_column, four_cards, or metric_cards"
    last_layout = "closing_slide" if end == total else "two_column, three_column, four_cards, or metric_cards"
    return f"""

Segmented generation rules:
- This response is one segment of a larger {total}-slide deck.
- Generate only global slides {start} through {end}; do not generate slides outside this range.
- This response must contain exactly {count} slides.
- The first slide in this segment should use one of: {first_layout}.
- The last slide in this segment should use one of: {last_layout}.
- Keep slide_id values aligned to the global deck order when possible, such as slide_{start:03d}.
"""


def _chunked_request(request: DeckGenerationRequest, start: int, count: int) -> DeckGenerationRequest:
    brief = _brief_from_request(request)
    segment_focus = (
        f"{brief.content_focus}\n"
        f"Segment: generate global slides {start}-{start + count - 1} of {request.slide_count}."
    ).strip()
    return request.model_copy(
        update={
            "slide_count": count,
            "brief": brief.model_copy(
                update={
                    "slide_count": count,
                    "content_focus": segment_focus,
                }
            ),
        }
    )


def _merge_deck_chunks(chunks: list[Deck], request: DeckGenerationRequest) -> Deck:
    if not chunks:
        raise ValueError("No deck chunks were generated.")

    first = chunks[0]
    payload = first.model_dump(mode="json")
    payload["deck_id"] = _identifier_from_text(request.topic, "generated")
    payload["title"] = request.topic
    if request.style is not None:
        payload["theme_name"] = request.style
    payload["slides"] = [
        slide.model_dump(mode="json")
        for chunk in chunks
        for slide in chunk.slides
    ]
    return _ensure_slide_count(Deck.model_validate(payload), request)


def _generate_deck_in_chunks(
    model: Any,
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
    generation_feedback: str | None = None,
    deck_plan: DeckPlan | None = None,
) -> Deck:
    chunks: list[Deck] = []
    start = 1
    while start <= request.slide_count:
        count = min(MAX_SINGLE_GENERATION_SLIDES, request.slide_count - start + 1)
        chunk_request = _chunked_request(request, start, count)
        chunk = _generate_deck_once(
            model,
            chunk_request,
            qa_feedback=qa_feedback,
            generation_feedback=generation_feedback,
            segment_instruction=_segment_instruction(start, count, request.slide_count),
            deck_plan=deck_plan,
            slide_index_offset=start - 1,
            total_slide_count=request.slide_count,
            force_slide_ids=True,
        )
        chunks.append(chunk)
        start += count

    return _merge_deck_chunks(chunks, request)


def generate_deck_with_model(
    model: Any,
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
    generation_feedback: str | None = None,
    deck_plan: DeckPlan | None = None,
) -> Deck:
    """Generate a Deck using a LangChain chat model with structured output."""

    request = _request_with_brief(model, request)
    if request.slide_count > MAX_SINGLE_GENERATION_SLIDES:
        return _generate_deck_in_chunks(
            model,
            request,
            qa_feedback=qa_feedback,
            generation_feedback=generation_feedback,
            deck_plan=deck_plan,
        )

    return _generate_deck_once(
        model,
        request,
        qa_feedback=qa_feedback,
        generation_feedback=generation_feedback,
        deck_plan=deck_plan,
    )


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
    request = _request_with_brief(model, request)
    try:
        deck_plan = generate_deck_plan_with_model(model, _brief_from_request(request))
    except Exception as exc:
        raise ValueError(f"DeckPlan generation failed: {exc}") from exc

    for attempt_index in range(1, max_attempts + 1):
        try:
            deck = generate_deck_with_model(
                model,
                request,
                qa_feedback=qa_feedback,
                generation_feedback=generation_feedback,
                deck_plan=deck_plan,
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
                deck_plan=deck_plan,
            )

        qa_feedback = qa_report
        generation_feedback = None

    last_attempt = attempts[-1]
    return GenerationResult(
        deck=last_attempt.deck,
        qa_report=last_attempt.qa_report,
        attempts=attempts,
        accepted=False,
        deck_plan=deck_plan,
    )
