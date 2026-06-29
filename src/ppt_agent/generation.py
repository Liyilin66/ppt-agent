"""LangChain structured-output deck generation."""

from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import Field

from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.models import Deck, StrictModel
from ppt_agent.planning import (
    DeckPlan,
    PlanSource,
    build_deterministic_deck_plan,
    generate_deck_plan_with_model,
)
from ppt_agent.qa import QAReport, analyze_deck
from ppt_agent.runtime import StageObserver, invoke_with_timeout, observed_stage, sanitize_error_message
from ppt_agent.theme import Theme


DEFAULT_LANGUAGE = "zh-CN"
MAX_SINGLE_GENERATION_SLIDES = 3
LONG_DECK_CHUNK_SLIDES = 2
LONG_DECK_SLIDE_THRESHOLD = 6
MAX_QA_FEEDBACK_ISSUES = 5
# These codes share one retry block that pushes the next generation toward
# audience-facing, layout-specific judgments without widening the core schema.
ANTI_GENERIC_FEEDBACK_CODES = {
    "generic_content",
    "missing_product_judgment",
    "vague_action",
    "prompt_keyword_repetition",
    "weak_takeaway",
    "instruction_leakage",
    "risk_matrix_malformed_row",
    "card_body_contains_subheadings",
    "metric_explanation_contains_risk_governance",
    "closing_action_not_executable",
}

BriefSource = Literal["llm", "deterministic", "fallback", "provided", "none"]


QA_FEEDBACK_FIX_INSTRUCTIONS = {
    "layout_diversity_low": (
        "Use at least 3 different content layouts across long decks; avoid relying only on card layouts."
    ),
    "layout_repetition_run": "Do not use the same content layout for 3 consecutive slides.",
    "adjacent_title_similarity": "Make adjacent slide titles and key messages clearly distinct.",
    "layout_contract_violation": (
        "Use a layout whose capacity matches the number of content blocks, or reduce "
        "the number of major content items."
    ),
    "visual_density_too_low": (
        "Add enough meaningful content or choose a layout that uses whitespace intentionally; "
        "avoid pages that look empty."
    ),
    "visual_density_too_high": (
        "Reduce text density, shorten bullets, or split content into a more suitable layout."
    ),
    "text_overflow_risk": (
        "Shorten long text blocks and keep each card/table cell within safe reading length."
    ),
    "title_wrapping_risk": (
        "Keep slide titles concise and avoid layouts that force titles into narrow vertical text areas."
    ),
    "visual_pattern_repetition": (
        "Increase visual variety across the deck; alternate card grids with process, matrix, "
        "takeaway, split-view, or checklist-style layouts."
    ),
    "slide_text_too_dense": (
        "Compress the slide to one core judgment, remove secondary concepts, and keep body text "
        "within the layout content budget."
    ),
    "card_body_too_long": (
        "Shorten card, metric, step, table-cell, or action-item body text to one compact sentence."
    ),
    "paragraph_like_slide": (
        "Rewrite report-style paragraphs into short presentation phrases; one sentence should "
        "express one judgment."
    ),
    "long_enumeration": (
        "Reduce long enumerations; keep only the most important items or split them across slides."
    ),
    "weak_slide_message": (
        "Give each slide a specific judgment or action, not a generic topic label."
    ),
    "generic_content": (
        "Replace generic slogans with concrete product judgments, control points, or operating actions."
    ),
    "missing_product_judgment": (
        "Turn concept explanation into a product decision: say when to do it, when not to do it, what to define, or how to recover."
    ),
    "vague_action": (
        "Rewrite vague action items into concrete next steps with an owner action, control point, or measurable check."
    ),
    "prompt_keyword_repetition": (
        "Reduce direct repetition of user prompt keywords and expand them into one specific judgment, scenario, or operating rule."
    ),
    "weak_takeaway": (
        "Give the slide one strong takeaway or tradeoff principle instead of summarizing earlier slides."
    ),
    "instruction_leakage": (
        "Remove prompt-like meta language and rewrite it as audience-facing slide content."
    ),
    "risk_matrix_malformed_row": (
        "Rewrite each risk-matrix row as risk, impact, and mitigation; use a specific risk event and a concrete mitigation action."
    ),
    "card_body_contains_subheadings": (
        "Keep each card to one heading and one body sentence; split stacked mini-headings into separate cards or remove them."
    ),
    "metric_explanation_contains_risk_governance": (
        "Keep metric cards focused on how the metric is measured; move governance or risk lists to risk_matrix."
    ),
    "closing_action_not_executable": (
        "Rewrite closing-slide actions as concrete verb-plus-object next steps and remove meta instruction wording."
    ),
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


class DeckBriefArtifact(StrictModel):
    brief: DeckBrief
    brief_source: BriefSource
    brief_fallback_used: bool = False
    brief_error_message: str | None = None


class DeckPlanArtifact(StrictModel):
    deck_plan: DeckPlan
    plan_source: PlanSource
    plan_fallback_used: bool = False
    plan_error_message: str | None = None


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
    brief_source: BriefSource = "none"
    brief_fallback_used: bool = False
    brief_error_message: str | None = None
    use_llm_brief: bool = False
    use_llm_plan: bool = False


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
    brief: DeckBrief | None = None
    brief_source: BriefSource = "none"
    brief_fallback_used: bool = False
    brief_error_message: str | None = None
    plan_source: PlanSource = "none"
    plan_fallback_used: bool = False
    plan_error_message: str | None = None


def format_qa_feedback_for_generation(qa_report: QAReport) -> str:
    issue_lines: list[str] = []
    emitted_fix_codes: set[str] = set()
    limited_issues = qa_report.issues[:MAX_QA_FEEDBACK_ISSUES]
    seen_codes = {issue.code for issue in qa_report.issues}
    for issue in limited_issues:
        location = f"slide={issue.slide_id}"
        if issue.element_id is not None:
            location = f"{location}, element={issue.element_id}"
        issue_lines.append(
            f"- [{issue.severity}] {issue.code} ({location}): {issue.message}"
        )
        fix_instruction = QA_FEEDBACK_FIX_INSTRUCTIONS.get(issue.code)
        if fix_instruction is not None and issue.code not in emitted_fix_codes:
            issue_lines.append(f"  Fix: {fix_instruction}")
            emitted_fix_codes.add(issue.code)

    if len(qa_report.issues) > MAX_QA_FEEDBACK_ISSUES:
        issue_lines.append(
            f"- Showing first {MAX_QA_FEEDBACK_ISSUES} of {len(qa_report.issues)} QA issues. "
            "Fix these first before making cosmetic changes."
        )

    if seen_codes & ANTI_GENERIC_FEEDBACK_CODES:
        issue_lines.extend(
            [
                "- Anti-generic retry guidance:",
                "  Fix: Replace vague concept words with concrete product judgments.",
                "  Fix: Keep one strong viewpoint per slide.",
                "  Fix: Add a specific scenario, control point, or fallback rule where relevant.",
                "  Fix: Do not mechanically repeat the user's prompt keywords.",
                "  Fix: Make closing-slide actions executable for the target audience.",
                "  Fix: Remove prompt-like meta language and rewrite it as audience-facing content.",
            ]
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


def _slide_content_contract_rules() -> str:
    # Keep these constraints prompt-only. QA mirrors them as warnings, but the
    # final Deck IR should read like slide copy, not like prompt instructions.
    return """
- Final slide content must be audience-facing content only.
- Do not copy prompt instructions, planning rules, QA rules, or content contract language into slide text.
- Never write slide text such as "把这一点转化为明确的下一步行动", "明确下一步行动", "本页必须", "该页需要", "内容合同", "核心判断必须", "可执行建议应该", "根据用户要求", or "将用户要求转化为".
- Slide Content Contract:
  - comparison_matrix:
    - Use real comparison dimensions, not abstract labels.
    - Each cell must be a short judgment, not a concept noun.
    - Make clear why Agent products carry different product responsibility than ordinary AI features.
    - Do not fill cells with concept-only labels such as "技术边界", "产品经理判断", or "评估指标".
  - framework slides using three_column or four_cards:
    - Each card must answer one product decision such as when it is safe to do, when it is not safe, what the product manager must define, or how the system falls back on failure.
    - Do not use cards only to explain concepts.
  - process_flow:
    - Show a real execution order.
    - Include at least one concrete control point such as permission confirmation, human handoff, failure rollback, or output validation.
    - Do not use an empty flow like input -> plan -> call -> deliver without control logic.
  - metric_cards:
    - Each metric must state how it is measured.
    - Keep risk governance content out of metric explanations.
    - Metrics should help decide whether the workflow is ready to launch.
  - risk_matrix:
    - The first row cannot be the page takeaway or slide-level summary.
    - Every row must include risk, impact, and mitigation.
    - Risk must be a specific risk event or failure mode.
    - Impact must describe the consequence.
    - Mitigation must be an action, not a slogan.
    - Prefer actions like "record input, tool calls, and output version" over slogans like "strengthen governance".
  - key_takeaway:
    - Give one strong judgment.
    - Do not only repeat earlier slides.
    - Answer the most important product tradeoff or operating principle of the topic.
  - closing_slide:
    - Give concrete next-step advice for the target audience.
    - Each action must be executable.
    - Do not use vague items like "learn boundaries", "understand workflow", or "pay attention to risk" without a next action.
    - Do not use meta expressions like "把这一点转化为明确的下一步行动".
"""


def _compact_prompt_text(text: str | None, max_chars: int) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _format_deck_plan(
    deck_plan: DeckPlan | None,
    *,
    focus_start: int | None = None,
    focus_count: int | None = None,
) -> str:
    if deck_plan is None:
        return ""

    focus_end = None if focus_start is None or focus_count is None else focus_start + focus_count - 1
    focus_slides = [
        slide
        for slide in deck_plan.slides
        if focus_start is None or focus_end is None or focus_start <= slide.slide_index <= focus_end
    ]
    if not focus_slides:
        focus_slides = deck_plan.slides

    outline_lines = [
        f"- Slide {slide.slide_index}: planning idea only: {slide.key_message}; layout: {slide.recommended_layout}"
        for slide in deck_plan.slides
    ]
    slide_lines: list[str] = []
    for slide in focus_slides:
        must_not_repeat = ", ".join(slide.must_not_repeat) or "None"
        slide_lines.append(
            "\n".join(
                [
                    f"- Slide {slide.slide_index} planning guidance:",
                    f"  Express this planning idea through slide.title or text elements: {slide.key_message}",
                    f"  Use this controlled layout as slide.layout: {slide.recommended_layout}",
                    f"  Avoid repeating these topics: {must_not_repeat}",
                ]
            )
        )

    slides_text = "\n".join(slide_lines)
    outline_text = "\n".join(outline_lines)
    segment_note = ""
    if focus_start is not None and focus_end is not None:
        segment_note = f"- Current chunk must follow only SlidePlan entries {focus_start}-{focus_end}.\n"

    return f"""

DeckPlan guidance:
- Follow this deck-level plan when generating Deck IR.
- Use the global outline only for story continuity; do not generate slides outside the requested segment.
- DeckPlan key_message is planning guidance only. Do not copy it as a key_message field into the final Deck IR slide.
- slide.title, slide.layout, and slide content must align with each slide's planning idea.
- Use each slide's recommended_layout as the slide.layout unless schema repair is absolutely required.
- Do not repeat any topic listed in must_not_repeat for that slide.
- Preserve distinct slide roles so the deck does not repeat the same point.
{segment_note}Global slide planning ideas:
{outline_text}

Current segment SlidePlan:
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


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(value.split()).strip(" ，。,:;!?.")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _fallback_language(user_requirements: str, language: str) -> str:
    normalized_language = language.strip().lower()
    normalized_requirements = user_requirements.lower()
    if normalized_language.startswith("en") or "english" in normalized_language:
        return "en"
    if "英文" in user_requirements or "英语" in user_requirements or " in english" in normalized_requirements:
        return "en"
    return DEFAULT_LANGUAGE


def _combined_request_text(*values: str | None) -> str:
    return " ".join(value for value in values if value).lower()


def _fallback_purpose(topic: str, audience: str, user_requirements: str) -> str:
    text = _combined_request_text(topic, audience, user_requirements)
    if any(marker in text for marker in ["pitch", "商业计划", "融资", "投资", "市场机会", "商业模式"]):
        return "business_pitch"
    if any(marker in text for marker in ["项目总结", "作业汇报", "实习", "开发项目", "project report"]):
        return "project_report"
    if any(marker in text for marker in ["风险评估", "安全", "合规", "失败处理", "risk analysis"]):
        if not any(marker in text for marker in ["产品经理", "agent 产品", "技术产品"]):
            return "risk_analysis"
    if any(marker in text for marker in ["课堂", "教学", "学生", "学习", "classroom", "teaching"]):
        if not any(marker in text for marker in ["产品经理", "agent 产品", "技术产品"]):
            return "classroom_teaching"
    if any(marker in text for marker in ["产品", "产品经理", "agent", "技术产品", "产品方法论"]):
        return "technical_product_share"
    return "general_knowledge_share"


def _fallback_visual_style(user_requirements: str, style: str | None) -> str:
    base = style or "clean modern"
    if any(marker in user_requirements for marker in ["蓝绿", "蓝绿色", "blue-green", "blue green"]):
        return f"{base}, light blue-green background"
    if any(marker in user_requirements for marker in ["淡蓝绿", "科技风", "技术风"]):
        return f"{base}, modern technology style"
    return base


def _extract_requirement_phrases(user_requirements: str) -> list[str]:
    matches = re.findall(
        r"(?:重点讲|必须包含|包括|需要讲)([^。；;\n]{1,96})",
        user_requirements,
    )
    values: list[str] = []
    for match in matches:
        for part in re.split(r"[、,，/]|以及|和", match):
            value = part.strip(" ：:的内容重点")
            if 1 <= len(value) <= 32:
                values.append(value)
    return values


def _fallback_must_include(user_requirements: str, key_points: list[str] | None) -> list[str]:
    known_topics = [
        "AI Agent",
        "Agent",
        "产品经理",
        "技术边界",
        "用户需求分析",
        "工作流设计",
        "评估指标",
        "落地风险",
        "学术诚信",
        "风险控制",
    ]
    values = list(key_points or [])
    values.extend(_extract_requirement_phrases(user_requirements))
    values.extend(topic for topic in known_topics if topic in user_requirements)
    return _dedupe_preserve_order(values)


def _fallback_must_avoid(user_requirements: str) -> list[str]:
    matches = re.findall(r"(?:不要|避免|不能|不应)([^。；;，,\n]{1,32})", user_requirements)
    cleaned = []
    for match in matches:
        value = match.strip(" 像是做成变得")
        if value:
            cleaned.append(value)
    return _dedupe_preserve_order(cleaned)


def build_deterministic_deck_brief(
    *,
    topic: str,
    audience: str,
    slide_count: int,
    user_requirements: str | None = None,
    style: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    key_points: list[str] | None = None,
) -> DeckBrief:
    """Build a deterministic DeckBrief without calling an LLM."""

    raw_requirements = user_requirements or ""
    content_focus = _compact_prompt_text(raw_requirements, 520) or _compact_prompt_text("\n".join(key_points or []), 360)
    return DeckBrief(
        topic=topic,
        audience=audience,
        slide_count=slide_count,
        language=_fallback_language(raw_requirements, language),
        purpose=_fallback_purpose(topic, audience, raw_requirements),
        tone="professional / educational",
        visual_style=_fallback_visual_style(raw_requirements, style),
        content_focus=content_focus or topic,
        must_include=_fallback_must_include(raw_requirements, key_points),
        must_avoid=_fallback_must_avoid(raw_requirements),
        user_requirements_raw=raw_requirements or None,
    )


def build_fallback_deck_brief(
    user_requirements: str,
    *,
    topic: str,
    audience: str,
    slide_count: int,
    style: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    key_points: list[str] | None = None,
) -> DeckBrief:
    """Build a deterministic DeckBrief when LLM brief extraction is unavailable."""

    return build_deterministic_deck_brief(
        topic=topic,
        audience=audience,
        slide_count=slide_count,
        user_requirements=user_requirements,
        style=style,
        language=language,
        key_points=key_points,
    )


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
    deck_plan_focus_start: int | None = None,
    deck_plan_focus_count: int | None = None,
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
{_format_deck_plan(deck_plan, focus_start=deck_plan_focus_start, focus_count=deck_plan_focus_count)}

Hard schema and layout rules:
- Return only structured data that can be validated as Deck.
- Do not generate Markdown, prose, speaker notes, PPTX, HTML, SVG, or images.
- {_language_instruction(brief.language)}
- Final Deck IR must exactly match the provided Pydantic schema.
- Output only fields defined by the Deck / Slide / Element schema. Do not add extra fields.
- Do not add key_message, rationale, notes, speaker_notes, layout_reason, title_hint, recommended_layout, must_not_repeat, or any planning-only fields to final Deck IR objects.
- The slide-level idea should be expressed through existing slide.title, subtitle/body text elements, card headings, or card bodies, not as a new field.
- Final slide content must be audience-facing content only.
- Do not copy prompt instructions, planning rules, QA rules, or content contract language into final slide text.
- Required root fields: deck_id, title, canvas_width_in, canvas_height_in, slides.
- The slides array length must be exactly {request.slide_count}. Do not generate more or fewer slides.
- Set deck.canvas_width_in to 13.333 and deck.canvas_height_in to 7.5 unless there is a strong reason not to.
- Use bbox coordinates and sizes in PowerPoint-style inches, not pixels.
- Choose each slide.layout from these controlled layouts only: {layouts}.
- Prefer this general sequence when it fits the requested deck: title_slide, two_column/three_column/four_cards/metric_cards, closing_slide.
- For a 3-slide deck, prefer:
  slide 1: title_slide.
  slide 2: comparison_matrix, process_flow, risk_matrix, key_takeaway, two_column, three_column, four_cards, or metric_cards.
  slide 3: closing_slide, key_takeaway, two_column, or four_cards.
- Do not use section_divider by default in a short 3-slide deck.
- Use section_divider only for true chapter transition or section break pages, never for ordinary content explanation pages.
- For decks with 8 slides or fewer, do not use section_divider unless the user explicitly asks for divider, transition, or section break pages.
- If a slide has only one core idea plus one explanation sentence, use key_takeaway, two_column, or three_column instead of section_divider.
- Use four_cards for four parallel concepts, four steps, four capabilities, or four recommendations.
- Use comparison_matrix for two-option comparisons, before/after views, or normal AI vs Agent; provide two major body text elements, one per side, and optionally one short decision_rule.
- Use process_flow for workflows, pipelines, or step-by-step processes; provide 3-5 step text elements in order.
- Use metric_cards for 2-4 metrics. If there are 4 metrics, keep each as its own metric item; do not merge the fourth metric into another card.
- Use risk_matrix for risk governance pages; provide 3-4 risk text elements where each item has exactly three concise lines: Risk, Impact, Mitigation.
- Use key_takeaway for strong conclusion or pre-closing summary pages; provide 2-4 takeaways or next actions.
- Prefer these professional layouts over card variants when the slide role is comparison, process, risk, or summary and the content fits.
- Avoid making 3 consecutive content slides use the same card-grid visual pattern; alternate card grids with process, matrix, takeaway, split-view, or checklist-style structures.
- Follow narrative order: background/context/why-now/value/problem framing must appear in the first half before conclusions; metrics and risk should precede conclusion; closing_slide and next-action content belong at the end.
- Do not introduce new background framing after a core conclusion. Each slide should advance the narrative, not restart the topic late.
- 背景 / 价值 / 为什么重要必须放在前半段；核心结论 / 下一步行动必须放在后半段，通常最后两页。
- Professional layouts must keep text short enough for the chosen layout; do not rely on the renderer to hide long prose.
- Do not squeeze 5 process steps into one narrow row; keep each process_flow step to a concise title plus one short description sentence.
- For key_takeaway, every takeaway must include both a concise title and a one-sentence explanation.
- For comparison_matrix, prefer aligned comparison rows over two sparse cards; put matching points in the same order on both sides.
- For comparison_matrix, every comparison row must express a concrete product judgment or responsibility difference, not a category word.
- For risk_matrix, every row must include a concrete mitigation; never leave mitigation implicit or mixed into the impact text.
- For risk_matrix, do not place the page takeaway or overall slide judgment inside any table row.
- For risk_matrix, keep each risk, impact, and mitigation cell concise while preserving all three cells.
- For closing_slide, each action item must include a concise heading plus one explanatory sentence.
- Do not rely on freeform bbox placement for visual design. The renderer will apply deterministic template positions and styles.
- Focus on semantic content: slide titles, concise section text, column content, metric labels/values, and closing message.
- Match each slide's content to its chosen layout. Do not create empty cards or placeholder-only cards.
- Card text should be short phrases or compact sentences, not long paragraphs.
- Still include valid bbox values for schema compatibility, but keep them simple and inside the canvas; template rendering may ignore the exact bbox.
- Content Budget:
  - Each slide can express only 1 core judgment.
  - Express the core judgment through slide.title, subtitle/body text, card headings, or card bodies.
  - Do not create a key_message field in Deck IR slides.
  - Chinese slide title <= 18 characters; English slide title <= 8 words.
  - Subtitle or key takeaway <= 32 Chinese characters.
  - Card heading <= 8 Chinese characters; English card heading <= 4 words.
  - Card body <= 32 Chinese characters; English card body <= 14 words.
  - Process step body <= 28 Chinese characters.
  - Risk matrix cells <= 24 Chinese characters each.
  - Metric card explanation <= 28 Chinese characters.
  - Closing slide action item explanation <= 32 Chinese characters.
  - Do not put 4 or more concepts into one card body.
  - Do not compress the user's full detailed requirements into a single slide.
- Layout-specific content budget:
  - title_slide: one clear title, one subtitle, and very little auxiliary text.
  - comparison_matrix: each cell contains one short judgment; align rows instead of writing paragraphs; compare responsibility, promise, validation, or rollback logic.
  - three_column/four_cards: each card has a short heading plus one short body sentence that states a product decision, boundary, or fallback rule; card bodies must not contain new subheadings or stacked mini-titles.
  - process_flow: each step has a short heading plus one-line body, and at least one step must act as a control point.
  - metric_cards: metric name plus one-line explanation of how to measure it; do not use metric cards to list governance or risk concerns.
  - risk_matrix: risk, impact, and mitigation cells must all be short.
  - key_takeaway: use a strong judgment or tradeoff principle, not a long summary.
  - closing_slide: action checklist; each item is a heading plus one short explanation with a concrete next step and no meta-instruction wording.
- Presentation Style Guard:
  - Write like a spoken technical product-sharing deck, not a report summary or course handout.
  - Do not write paper-like explanatory paragraphs.
  - Do not write long enumeration sentences such as "包括 A、B、C、D、E".
  - Each bullet or body should be a judgment sentence or action sentence.
  - One sentence expresses one judgment only.
  - Prefer Chinese patterns like "先判断……", "只承诺……", "把……拆成……", "用……验证……", "高风险动作必须……".
  - Avoid Chinese patterns like "需要理解的技术边界包括……", "覆盖……以及……并且……", "从……、……、……、……等方面……".
  - Avoid generic claims like "提升效率", "降低风险", "前置治理", "完善机制", "加强监控", "优化体验", or "建立闭环" unless the slide also states a specific operating action or product judgment.
  - Do not mechanically repeat user prompt keywords such as "技术边界", "用户需求分析", "工作流设计", "评估指标", or "落地风险" without concrete expansion.
  - Never use meta prompt language like "本页必须", "该页需要", "明确下一步行动", "把这一点转化为", "内容合同", "核心判断必须", "可执行建议应该", "根据用户要求", or "将用户要求转化为" in final slide text.
  - 中文输出要像技术产品分享，不像营销文案，也不像课程讲义摘要。
{_slide_content_contract_rules()}
- Every slide must include slide_id, title, layout, and at least one element.
- slide_id values must be unique across the deck.
- Every element must include element_id, type, bbox, and type-specific fields.
- element_id values must be unique within each slide.
- Supported element types are text, shape, and image.
- For template-guided slides, make the first text element the primary slide title and subsequent text elements the body/columns/cards in reading order.
- Keep generated text compact and below the Content Budget.
- Avoid paragraph-style body text. Prefer phrases, short judgment sentences, and short action bullets.
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
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
) -> DeckBrief:
    """Extract a structured DeckBrief from detailed user requirements."""

    key_points_text = _format_brief_items(key_points or [])
    compact_requirements = _compact_prompt_text(user_requirements, 1200)
    prompt = f"""Extract a DeckBrief.

Fixed request fields:
- topic: {topic}
- audience: {audience}
- slide_count: {slide_count}
- style: {style or "Not specified"}
- requested_language: {language or DEFAULT_LANGUAGE}
- key_points:
{key_points_text}

User requirements:
{compact_requirements}

Rules:
- Return only structured data matching DeckBrief.
- Keep slide_count exactly {slide_count}; it is the product request value.
- Default language to zh-CN unless the user explicitly asks for English.
- Extract only these fields: topic, audience, slide_count, language, purpose, tone, visual_style, content_focus, must_include, must_avoid, user_requirements_raw.
- Preserve the raw detailed request in user_requirements_raw.
"""
    structured_model = model.with_structured_output(BRIEF_STRUCTURED_OUTPUT_SCHEMA)
    with observed_stage(stage_observer, "build_brief", slide_count=slide_count, use_deck_plan=False):
        response = _unwrap_structured_response(
            invoke_with_timeout(
                lambda: structured_model.invoke(prompt),
                timeout_seconds=timeout_seconds,
                stage_name="build_brief",
            )
        )
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


def _request_with_brief(
    model: Any,
    request: DeckGenerationRequest,
    *,
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
) -> DeckGenerationRequest:
    if request.brief is not None:
        if request.brief_source == "none":
            return request.model_copy(update={"brief_source": "provided"})
        return request

    brief_source: BriefSource = "deterministic"
    brief_error_message: str | None = None
    brief_fallback_used = False
    raw_requirements = request.user_requirements or ""

    if request.use_llm_brief and request.user_requirements:
        brief_source = "llm"
        try:
            brief = build_brief_from_user_prompt(
                model,
                request.user_requirements,
                topic=request.topic,
                audience=request.audience,
                slide_count=request.slide_count,
                style=request.style,
                language=request.language,
                key_points=request.key_points,
                timeout_seconds=timeout_seconds,
                stage_observer=stage_observer,
            )
        except Exception as exc:
            brief_error_message = sanitize_error_message(exc)
            brief_source = "fallback"
            brief_fallback_used = True
            with observed_stage(
                stage_observer,
                "build_brief_fallback",
                slide_count=request.slide_count,
                use_deck_plan=False,
                error_message=brief_error_message,
                brief_source=brief_source,
            ):
                brief = build_fallback_deck_brief(
                    request.user_requirements,
                    topic=request.topic,
                    audience=request.audience,
                    slide_count=request.slide_count,
                    style=request.style,
                    language=request.language,
                    key_points=request.key_points,
                )
    else:
        with observed_stage(
            stage_observer,
            "build_brief_fast_path",
            slide_count=request.slide_count,
            use_deck_plan=False,
            brief_source=brief_source,
        ):
            brief = build_deterministic_deck_brief(
                topic=request.topic,
                audience=request.audience,
                slide_count=request.slide_count,
                user_requirements=raw_requirements,
                style=request.style,
                language=request.language,
                key_points=request.key_points,
            )

    return request.model_copy(
        update={
            "brief": brief,
            "brief_source": brief_source,
            "brief_fallback_used": brief_fallback_used,
            "brief_error_message": brief_error_message,
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
            "comparison": "comparison_matrix",
            "comparison_table": "comparison_matrix",
            "matrix": "comparison_matrix",
            "before_after": "comparison_matrix",
            "ai_vs_agent": "comparison_matrix",
            "process": "process_flow",
            "workflow": "process_flow",
            "pipeline": "process_flow",
            "step_flow": "process_flow",
            "steps": "process_flow",
            "risk": "risk_matrix",
            "risks": "risk_matrix",
            "risk_table": "risk_matrix",
            "governance": "risk_matrix",
            "takeaway": "key_takeaway",
            "key_takeaways": "key_takeaway",
            "conclusion": "key_takeaway",
            "action_checklist": "key_takeaway",
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
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
    attempt_index: int | None = None,
    chunk_index: int | None = None,
    total_chunks: int | None = None,
    deck_plan_focus_start: int | None = None,
    deck_plan_focus_count: int | None = None,
) -> Deck:
    prompt = build_generation_prompt(
        request,
        qa_feedback=qa_feedback,
        generation_feedback=generation_feedback,
        segment_instruction=segment_instruction,
        deck_plan=deck_plan,
        deck_plan_focus_start=deck_plan_focus_start,
        deck_plan_focus_count=deck_plan_focus_count,
    )
    structured_model = model.with_structured_output(Deck)
    timeout_detail = (
        f"chunk {chunk_index}/{total_chunks}"
        if chunk_index is not None and total_chunks is not None
        else None
    )
    with observed_stage(
        stage_observer,
        "generate_deck",
        slide_count=request.slide_count,
        use_deck_plan=deck_plan is not None,
        attempt_index=attempt_index,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
    ):
        response = _unwrap_structured_response(
            invoke_with_timeout(
                lambda: structured_model.invoke(prompt),
                timeout_seconds=timeout_seconds,
                stage_name="generate_deck",
                timeout_detail=timeout_detail,
            )
        )

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


def _segment_instruction(start: int, count: int, total: int, chunk_index: int, total_chunks: int) -> str:
    end = start + count - 1
    content_layouts = "comparison_matrix, process_flow, risk_matrix, key_takeaway, two_column, three_column, four_cards, or metric_cards"
    first_layout = "title_slide" if start == 1 else content_layouts
    last_layout = "closing_slide or key_takeaway" if end == total else content_layouts
    return f"""

Segmented generation rules:
- This response is chunk {chunk_index}/{total_chunks} of a larger {total}-slide deck.
- Generate only global slides {start} through {end}; do not generate slides outside this range.
- This response must contain exactly {count} slides.
- The first slide in this segment should use one of: {first_layout}.
- The last slide in this segment should use one of: {last_layout}.
- Keep slide_id values aligned to the global deck order when possible, such as slide_{start:03d}.
"""


def _chunk_size_for_slide_count(slide_count: int) -> int:
    if slide_count >= LONG_DECK_SLIDE_THRESHOLD:
        return LONG_DECK_CHUNK_SLIDES
    return min(MAX_SINGLE_GENERATION_SLIDES, slide_count)


def _chunked_request(request: DeckGenerationRequest, start: int, count: int) -> DeckGenerationRequest:
    brief = _brief_from_request(request)
    segment_focus = (
        f"{_compact_prompt_text(brief.content_focus, 360)}\n"
        f"Segment: generate global slides {start}-{start + count - 1} of {request.slide_count}."
    ).strip()
    raw_requirements = _compact_prompt_text(brief.user_requirements_raw, 520)
    return request.model_copy(
        update={
            "slide_count": count,
            "brief": brief.model_copy(
                update={
                    "slide_count": count,
                    "content_focus": segment_focus,
                    "user_requirements_raw": raw_requirements or None,
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
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
    attempt_index: int | None = None,
) -> Deck:
    chunks: list[Deck] = []
    chunk_size = _chunk_size_for_slide_count(request.slide_count)
    total_chunks = (request.slide_count + chunk_size - 1) // chunk_size
    start = 1
    chunk_index = 1
    while start <= request.slide_count:
        count = min(chunk_size, request.slide_count - start + 1)
        chunk_request = _chunked_request(request, start, count)
        chunk = _generate_deck_once(
            model,
            chunk_request,
            qa_feedback=qa_feedback,
            generation_feedback=generation_feedback,
            segment_instruction=_segment_instruction(start, count, request.slide_count, chunk_index, total_chunks),
            deck_plan=deck_plan,
            slide_index_offset=start - 1,
            total_slide_count=request.slide_count,
            force_slide_ids=True,
            timeout_seconds=timeout_seconds,
            stage_observer=stage_observer,
            attempt_index=attempt_index,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            deck_plan_focus_start=start,
            deck_plan_focus_count=count,
        )
        chunks.append(chunk)
        start += count
        chunk_index += 1

    return _merge_deck_chunks(chunks, request)


def generate_deck_with_model(
    model: Any,
    request: DeckGenerationRequest,
    qa_feedback: QAReport | None = None,
    generation_feedback: str | None = None,
    deck_plan: DeckPlan | None = None,
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
    attempt_index: int | None = None,
) -> Deck:
    """Generate a Deck using a LangChain chat model with structured output."""

    request = _request_with_brief(
        model,
        request,
        timeout_seconds=timeout_seconds,
        stage_observer=stage_observer,
    )
    chunk_size = _chunk_size_for_slide_count(request.slide_count)
    if request.slide_count > chunk_size:
        return _generate_deck_in_chunks(
            model,
            request,
            qa_feedback=qa_feedback,
            generation_feedback=generation_feedback,
            deck_plan=deck_plan,
            timeout_seconds=timeout_seconds,
            stage_observer=stage_observer,
            attempt_index=attempt_index,
        )

    return _generate_deck_once(
        model,
        request,
        qa_feedback=qa_feedback,
        generation_feedback=generation_feedback,
        deck_plan=deck_plan,
        timeout_seconds=timeout_seconds,
        stage_observer=stage_observer,
        attempt_index=attempt_index,
        chunk_index=1,
        total_chunks=1,
        deck_plan_focus_start=1,
        deck_plan_focus_count=request.slide_count,
    )


def _resolve_deck_plan(
    model: Any,
    request: DeckGenerationRequest,
    *,
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
) -> tuple[DeckPlan, PlanSource, bool, str | None]:
    brief = _brief_from_request(request)
    if request.use_llm_plan:
        try:
            deck_plan = generate_deck_plan_with_model(
                model,
                brief,
                timeout_seconds=timeout_seconds,
                stage_observer=stage_observer,
            ).model_copy(update={"plan_source": "llm"})
            return deck_plan, "llm", False, None
        except Exception as exc:
            plan_error_message = sanitize_error_message(exc)
            with observed_stage(
                stage_observer,
                "generate_deck_plan_fallback",
                slide_count=request.slide_count,
                use_deck_plan=True,
                plan_source="fallback",
                error_message=plan_error_message,
            ):
                deck_plan = build_deterministic_deck_plan(brief).model_copy(update={"plan_source": "fallback"})
            return deck_plan, "fallback", True, plan_error_message

    with observed_stage(
        stage_observer,
        "generate_deck_plan_fast_path",
        slide_count=request.slide_count,
        use_deck_plan=True,
        plan_source="deterministic",
    ):
        deck_plan = build_deterministic_deck_plan(brief)
    return deck_plan, "deterministic", False, None


def generate_deck_with_quality_gate(
    model: Any,
    request: DeckGenerationRequest,
    theme: Theme | None = None,
    min_score: int = 80,
    max_attempts: int = 2,
    timeout_seconds: float | None = None,
    stage_observer: StageObserver | None = None,
) -> GenerationResult:
    """Generate Deck IR and retry when deterministic QA does not meet the score gate."""

    if not 0 <= min_score <= 100:
        raise ValueError("min_score must be between 0 and 100.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    attempts: list[GenerationAttempt] = []
    qa_feedback: QAReport | None = None
    generation_feedback: str | None = None
    request = _request_with_brief(
        model,
        request,
        timeout_seconds=timeout_seconds,
        stage_observer=stage_observer,
    )
    deck_plan, plan_source, plan_fallback_used, plan_error_message = _resolve_deck_plan(
        model,
        request,
        timeout_seconds=timeout_seconds,
        stage_observer=stage_observer,
    )

    for attempt_index in range(1, max_attempts + 1):
        with observed_stage(
            stage_observer,
            "qa_attempt",
            attempt_index=attempt_index,
            slide_count=request.slide_count,
            use_deck_plan=True,
        ):
            try:
                deck = generate_deck_with_model(
                    model,
                    request,
                    qa_feedback=qa_feedback,
                    generation_feedback=generation_feedback,
                    deck_plan=deck_plan,
                    timeout_seconds=timeout_seconds,
                    stage_observer=stage_observer,
                    attempt_index=attempt_index,
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
                    brief=request.brief,
                    brief_source=request.brief_source,
                    brief_fallback_used=request.brief_fallback_used,
                    brief_error_message=request.brief_error_message,
                    plan_source=plan_source,
                    plan_fallback_used=plan_fallback_used,
                    plan_error_message=plan_error_message,
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
        brief=request.brief,
        brief_source=request.brief_source,
        brief_fallback_used=request.brief_fallback_used,
        brief_error_message=request.brief_error_message,
        plan_source=plan_source,
        plan_fallback_used=plan_fallback_used,
        plan_error_message=plan_error_message,
    )
