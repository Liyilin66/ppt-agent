"""Rule-based quality checks for validated Slide IR decks."""

from __future__ import annotations

from itertools import combinations
import re
from typing import Literal

from pydantic import Field

from ppt_agent.design import get_layout_contract
from ppt_agent.models import BBox, Deck, StrictModel, TextElement
from ppt_agent.theme import Theme


CONTENT_LAYOUT_EXCLUSIONS = {"title_slide", "closing_slide"}
LOW_DENSITY_EXCLUSIONS = {"title_slide", "section_divider", "closing_slide"}
CARD_GRID_PATTERN_LAYOUTS = {"two_column", "three_column", "four_cards", "metric_cards"}
VISUAL_PATTERN_LAYOUTS = {
    "two_column": "card_grid",
    "three_column": "card_grid",
    "four_cards": "card_grid",
    "metric_cards": "card_grid",
    "process_flow": "process_flow",
    "risk_matrix": "matrix",
    "comparison_matrix": "matrix",
    "key_takeaway": "takeaway",
    "section_divider": "divider",
}
CONTENT_STYLE_WARNING_CODES = {
    "slide_text_too_dense",
    "card_body_too_long",
    "paragraph_like_slide",
    "long_enumeration",
    "weak_slide_message",
}
LAYOUT_TEXT_LIMITS = {
    "title_slide": 125,
    "comparison_matrix": 95,
    "process_flow": 72,
    "risk_matrix": 78,
    "key_takeaway": 110,
}
SLIDE_TEXT_BUDGETS = {
    "title_slide": 150,
    "two_column": 210,
    "three_column": 230,
    "four_cards": 250,
    "metric_cards": 190,
    "comparison_matrix": 220,
    "process_flow": 200,
    "risk_matrix": 230,
    "key_takeaway": 190,
    "closing_slide": 190,
}
DEFAULT_SLIDE_TEXT_BUDGET = 300
BODY_TEXT_BUDGETS = {
    "comparison_matrix": 42,
    "process_flow": 36,
    "risk_matrix": 34,
    "metric_cards": 36,
    "key_takeaway": 42,
    "closing_slide": 42,
}
DEFAULT_BODY_TEXT_BUDGET = 44
WEAK_MESSAGE_TITLES = {
    "overview",
    "summary",
    "background",
    "context",
    "introduction",
    "agenda",
    "核心内容",
    "内容概述",
    "概述",
    "介绍",
    "背景",
    "总结",
    "价值",
    "方法",
    "流程",
    "指标",
    "风险",
}


class QAIssue(StrictModel):
    severity: Literal["info", "warning", "error"]
    slide_id: str = Field(..., min_length=1)
    element_id: str | None = None
    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class QAReport(StrictModel):
    deck_id: str = Field(..., min_length=1)
    score: int = Field(..., ge=0, le=100)
    issues: list[QAIssue] = Field(default_factory=list)


def _bbox_area(bbox: BBox) -> float:
    return bbox.width * bbox.height


def _overlap_area(first: BBox, second: BBox) -> float:
    left = max(first.x, second.x)
    right = min(first.x + first.width, second.x + second.width)
    top = max(first.y, second.y)
    bottom = min(first.y + first.height, second.y + second.height)

    if right <= left or bottom <= top:
        return 0.0

    return (right - left) * (bottom - top)


def _score_for_issues(issues: list[QAIssue]) -> int:
    error_penalty = sum(25 for issue in issues if issue.severity == "error")
    soft_penalty = 0
    light_feedback_codes = {
        "visual_density_too_low",
        "visual_density_too_high",
        "text_overflow_risk",
        "title_wrapping_risk",
        "visual_pattern_repetition",
        "SLIDE_TOO_EMPTY",
    } | CONTENT_STYLE_WARNING_CODES
    for issue in issues:
        if issue.severity == "error":
            continue
        if issue.severity == "info":
            soft_penalty += 0
        elif issue.code in light_feedback_codes:
            soft_penalty += 1
        else:
            soft_penalty += 2

    # Warnings guide regeneration and polish, but should not make a rendered PPTX
    # look like a runtime failure. Hard errors remain the only path to severe scores.
    soft_penalty = min(soft_penalty, 40)
    return max(0, 100 - error_penalty - soft_penalty)


def _content_layout(slide_layout: str) -> str | None:
    if slide_layout in CONTENT_LAYOUT_EXCLUSIONS:
        return None
    return slide_layout


def _append_layout_diversity_issue(deck: Deck, issues: list[QAIssue]) -> None:
    if len(deck.slides) < 6:
        return

    content_layouts = [
        layout
        for slide in deck.slides
        if (layout := _content_layout(slide.layout)) is not None
    ]
    unique_layouts = sorted(set(content_layouts))
    if content_layouts and len(unique_layouts) < 3:
        layout_summary = ", ".join(unique_layouts)
        issues.append(
            QAIssue(
                severity="warning",
                slide_id=deck.deck_id,
                code="layout_diversity_low",
                message=(
                    f"Deck uses only {len(unique_layouts)} unique content layout(s): "
                    f"{layout_summary}. Use at least three content layouts across longer decks "
                    "to create clearer structure and visual rhythm."
                ),
            )
        )


def _append_layout_repetition_issues(deck: Deck, issues: list[QAIssue]) -> None:
    run_layout: str | None = None
    run_slide_ids: list[str] = []

    def flush_run() -> None:
        if run_layout is None or len(run_slide_ids) < 3:
            return
        issues.append(
            QAIssue(
                severity="warning",
                slide_id=run_slide_ids[0],
                code="layout_repetition_run",
                message=(
                    f"Slides {', '.join(run_slide_ids)} repeat the '{run_layout}' layout "
                    f"for {len(run_slide_ids)} consecutive content slides. Vary the layout "
                    "to avoid a monotonous deck rhythm."
                ),
            )
        )

    for slide in deck.slides:
        layout = _content_layout(slide.layout)
        if layout is None:
            flush_run()
            run_layout = None
            run_slide_ids = []
            continue

        if layout == run_layout:
            run_slide_ids.append(slide.slide_id)
        else:
            flush_run()
            run_layout = layout
            run_slide_ids = [slide.slide_id]

    flush_run()


def _visual_pattern(slide_layout: str) -> str | None:
    if slide_layout in CONTENT_LAYOUT_EXCLUSIONS:
        return None
    if slide_layout in CARD_GRID_PATTERN_LAYOUTS:
        return "card_grid"
    return VISUAL_PATTERN_LAYOUTS.get(slide_layout)


def _append_visual_pattern_repetition_issues(deck: Deck, issues: list[QAIssue]) -> None:
    pattern_run: str | None = None
    pattern_slide_ids: list[str] = []

    def flush_pattern_run() -> None:
        if pattern_run is None or len(pattern_slide_ids) < 3:
            return
        issues.append(
            QAIssue(
                severity="warning",
                slide_id=pattern_slide_ids[0],
                code="visual_pattern_repetition",
                message=(
                    f"Slides {', '.join(pattern_slide_ids)} repeat the '{pattern_run}' visual "
                    "pattern for 3 or more consecutive content slides. Use more varied "
                    "information architecture and visual rhythm."
                ),
            )
        )

    content_patterns: list[str] = []
    for slide in deck.slides:
        pattern = _visual_pattern(slide.layout)
        if pattern is None:
            flush_pattern_run()
            pattern_run = None
            pattern_slide_ids = []
            continue

        content_patterns.append(pattern)
        if pattern == pattern_run:
            pattern_slide_ids.append(slide.slide_id)
        else:
            flush_pattern_run()
            pattern_run = pattern
            pattern_slide_ids = [slide.slide_id]

    flush_pattern_run()

    card_grid_count = sum(1 for pattern in content_patterns if pattern == "card_grid")
    if len(deck.slides) >= 8 and card_grid_count > 5:
        issues.append(
            QAIssue(
                severity="warning",
                slide_id=deck.deck_id,
                code="visual_pattern_repetition",
                message=(
                    f"Deck uses card-grid visual patterns on {card_grid_count} slides. "
                    "For an 8-slide-style deck, use process, matrix, takeaway, or split-view "
                    "patterns to avoid a copied-template feel."
                ),
            )
        )


def _title_ngrams(title: str) -> set[str]:
    normalized = re.sub(r"[\W_]+", "", title.lower(), flags=re.UNICODE)
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _title_similarity(first: str, second: str) -> float:
    first_ngrams = _title_ngrams(first)
    second_ngrams = _title_ngrams(second)
    if not first_ngrams or not second_ngrams:
        return 0.0

    return len(first_ngrams & second_ngrams) / len(first_ngrams | second_ngrams)


def _append_adjacent_title_similarity_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for first, second in zip(deck.slides, deck.slides[1:]):
        similarity = _title_similarity(first.title, second.title)
        if similarity < 0.72:
            continue

        issues.append(
            QAIssue(
                severity="warning",
                slide_id=second.slide_id,
                code="adjacent_title_similarity",
                message=(
                    f"Adjacent slide titles are too similar ({similarity:.0%} overlap): "
                    f"'{first.title}' and '{second.title}'. Give neighboring slides distinct "
                    "titles and key messages."
                ),
            )
        )


def _estimate_slide_content_items(slide) -> int:
    text_elements = [
        element
        for element in slide.elements
        if isinstance(element, TextElement) and element.text.strip()
    ]
    image_count = sum(1 for element in slide.elements if element.type == "image")

    if text_elements:
        body_texts = text_elements[1:]
        estimate = len(body_texts) + image_count
        if slide.layout == "comparison_matrix" and len(body_texts) >= 3:
            estimate -= 1
    else:
        estimate = image_count

    if estimate == 0:
        estimate = max(0, len(slide.elements) - 1)

    return estimate


def _append_layout_contract_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for slide in deck.slides:
        try:
            contract = get_layout_contract(slide.layout)
        except ValueError:
            continue

        estimated_items = _estimate_slide_content_items(slide)
        if estimated_items <= contract.max_items:
            continue

        issues.append(
            QAIssue(
                severity="warning",
                slide_id=slide.slide_id,
                code="layout_contract_violation",
                message=(
                    f"Slide '{slide.slide_id}' uses layout '{contract.layout_name}' "
                    f"with estimated_items={estimated_items}, above max_items="
                    f"{contract.max_items}."
                ),
            )
        )


def _text_elements(slide) -> list[TextElement]:
    return [
        element
        for element in slide.elements
        if isinstance(element, TextElement) and element.text.strip()
    ]


def _text_length_score(text: str) -> int:
    cjk_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    non_cjk_chars = sum(1 for char in text if not char.isspace()) - cjk_chars
    return cjk_chars + non_cjk_chars


def _bullet_line_count(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.strip().startswith(("-", "*", "•")) or re.match(r"^\s*\d+[\).]", line)
    )


def _semantic_lines(text: str) -> list[str]:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        line = re.sub(r"^\s*(?:[-*•]|\d+[\).]|[A-Z]\.)\s*", "", line)
        line = re.sub(
            r"^(?:heading|title|body|risk|impact|mitigation|风险|影响|缓解措施|缓解|说明|解释)[:：]\s*",
            "",
            line,
            flags=re.I,
        ).strip()
        if line:
            lines.append(line)
    if lines:
        return lines
    stripped = text.strip()
    return [stripped] if stripped else []


def _body_segments_for_budget(layout: str, text: str) -> list[str]:
    if layout == "risk_matrix":
        return [part for part in _risk_matrix_parts(text) if part]

    lines = _semantic_lines(text)
    if len(lines) >= 2:
        return [" ".join(lines[1:])]
    return lines


def _punctuation_count(text: str) -> int:
    return sum(text.count(marker) for marker in ("，", "、", "；", "。", ",", ";", "."))


def _looks_like_paragraph(text: str) -> bool:
    length_score = _text_length_score(text)
    if length_score < 70:
        return False
    return _punctuation_count(text) >= 3


def _looks_like_long_enumeration(text: str) -> bool:
    if _text_length_score(text) < 42:
        return False

    separator_count = sum(text.count(marker) for marker in ("、", "，", ",", "；", ";"))
    if separator_count < 5:
        return False

    segments = [
        segment.strip(" ：:。.;；")
        for segment in re.split(r"[、，,；;]", text)
        if segment.strip(" ：:。.;；")
    ]
    compact_terms = [segment for segment in segments if 1 <= _text_length_score(segment) <= 18]
    return len(compact_terms) >= 6


def _normalized_message_text(text: str) -> str:
    return re.sub(r"[\s:：。.!?？！（）()\[\]【】_-]+", "", text.strip().lower())


def _looks_like_weak_slide_message(slide) -> bool:
    if slide.layout in {"title_slide", "section_divider", "closing_slide"}:
        return False

    normalized_title = _normalized_message_text(slide.title)
    if normalized_title in WEAK_MESSAGE_TITLES:
        return True

    first_body = _body_texts(slide)[0].text if _body_texts(slide) else ""
    normalized_body = _normalized_message_text(first_body)
    if normalized_title and normalized_title == normalized_body:
        return True

    return False


def _append_content_style_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for slide in deck.slides:
        text_elements = _text_elements(slide)
        total_chars = sum(_text_length_score(element.text) for element in text_elements)
        slide_budget = SLIDE_TEXT_BUDGETS.get(slide.layout, DEFAULT_SLIDE_TEXT_BUDGET)
        if total_chars > slide_budget:
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="slide_text_too_dense",
                    message=(
                        f"Slide '{slide.slide_id}' has text length score {total_chars}, "
                        f"above the presentation budget {slide_budget} for layout "
                        f"'{slide.layout}'."
                    ),
                )
            )

        body_budget = BODY_TEXT_BUDGETS.get(slide.layout, DEFAULT_BODY_TEXT_BUDGET)
        for element in _body_texts(slide):
            body_segments = _body_segments_for_budget(slide.layout, element.text)
            longest_segment = max((_text_length_score(segment) for segment in body_segments), default=0)
            if longest_segment > body_budget:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        element_id=element.element_id,
                        code="card_body_too_long",
                        message=(
                            f"Text element '{element.element_id}' on slide '{slide.slide_id}' "
                            f"has body length score {longest_segment}, above the layout body "
                            f"budget {body_budget}."
                        ),
                    )
                )

            if _looks_like_paragraph(element.text):
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        element_id=element.element_id,
                        code="paragraph_like_slide",
                        message=(
                            f"Text element '{element.element_id}' on slide '{slide.slide_id}' "
                            "reads like a report paragraph. Use shorter presentation-style "
                            "phrases or one judgment sentence."
                        ),
                    )
                )

            if _looks_like_long_enumeration(element.text):
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        element_id=element.element_id,
                        code="long_enumeration",
                        message=(
                            f"Text element '{element.element_id}' on slide '{slide.slide_id}' "
                            "contains a long enumeration. Keep only the most important items "
                            "or split the concepts across slides."
                        ),
                    )
                )

        if _looks_like_weak_slide_message(slide):
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="weak_slide_message",
                    message=(
                        f"Slide '{slide.slide_id}' has a generic message title "
                        f"'{slide.title}'. Use a specific judgment or action-oriented "
                        "key message."
                    ),
                )
            )


def _risk_matrix_parts(text: str) -> tuple[str, str, str]:
    lines = [line.strip(" -•\t") for line in text.splitlines() if line.strip(" -•\t")]
    if len(lines) == 1 and "|" in lines[0]:
        lines = [part.strip() for part in lines[0].split("|") if part.strip()]
    cleaned = []
    for line in lines[:3]:
        cleaned.append(re.sub(r"^(risk|impact|mitigation|风险|影响|缓解措施|缓解)[:：]\s*", "", line, flags=re.I))
    while len(cleaned) < 3:
        cleaned.append("")
    return cleaned[0], cleaned[1], cleaned[2]


def _append_risk_matrix_semantic_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for slide in deck.slides:
        if slide.layout != "risk_matrix":
            continue
        for element in _body_texts(slide)[:4]:
            _risk, _impact, mitigation = _risk_matrix_parts(element.text)
            if mitigation:
                continue
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    element_id=element.element_id,
                    code="risk_matrix_missing_mitigation",
                    message=(
                        f"Risk matrix row '{element.element_id}' on slide '{slide.slide_id}' "
                        "is missing a clear mitigation. Each risk row should include risk, "
                        "impact, and mitigation."
                    ),
                )
            )


def _body_texts(slide) -> list[TextElement]:
    text_elements = _text_elements(slide)
    return text_elements[1:] if text_elements else []


def _append_visual_preflight_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for slide in deck.slides:
        body_texts = _body_texts(slide)
        estimated_items = _estimate_slide_content_items(slide)
        total_chars = sum(_text_length_score(element.text) for element in body_texts)
        total_bullets = sum(_bullet_line_count(element.text) for element in body_texts)

        if slide.layout not in LOW_DENSITY_EXCLUSIONS and estimated_items <= 1 and total_chars < 45:
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="visual_density_too_low",
                    message=(
                        f"Slide '{slide.slide_id}' uses layout '{slide.layout}' but has only "
                        f"{estimated_items} estimated content item(s) and {total_chars} body "
                        "characters, so it may look empty."
                    ),
                )
            )

        if len(body_texts) > 6 or total_bullets > 12 or total_chars > 560:
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="visual_density_too_high",
                    message=(
                        f"Slide '{slide.slide_id}' may be too dense: {len(body_texts)} body "
                        f"text blocks, {total_bullets} bullet-like lines, {total_chars} "
                        "body characters."
                    ),
                )
            )

        limit = LAYOUT_TEXT_LIMITS.get(slide.layout, 140)
        for element in body_texts:
            length_score = _text_length_score(element.text)
            if length_score <= limit:
                continue
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    element_id=element.element_id,
                    code="text_overflow_risk",
                    message=(
                        f"Text element '{element.element_id}' on slide '{slide.slide_id}' "
                        f"has length score {length_score}, above safe limit {limit} for "
                        f"layout '{slide.layout}'."
                    ),
                )
            )

        title_length = _text_length_score(slide.title)
        if slide.layout == "title_slide":
            text_elements = _text_elements(slide)
            rendered_title = text_elements[0].text if text_elements else slide.title
            rendered_title_length = _text_length_score(rendered_title)
            if title_length > 34 or rendered_title_length > 40:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="title_wrapping_risk",
                        message=(
                            f"Slide '{slide.slide_id}' title may wrap awkwardly on a cover: "
                            f"title length score {max(title_length, rendered_title_length)}."
                        ),
                    )
                )
        elif title_length > 72 and slide.layout in {"comparison_matrix", "process_flow", "risk_matrix", "key_takeaway"}:
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="title_wrapping_risk",
                    message=(
                        f"Slide '{slide.slide_id}' title may wrap awkwardly in layout "
                        f"'{slide.layout}': title length score {title_length}."
                    ),
                )
            )


def analyze_deck(deck: Deck, theme: Theme | None = None) -> QAReport:
    """Analyze a validated deck with deterministic QA rules."""

    issues: list[QAIssue] = []
    slide_area = deck.canvas_width_in * deck.canvas_height_in

    _append_layout_diversity_issue(deck, issues)
    _append_layout_repetition_issues(deck, issues)
    _append_visual_pattern_repetition_issues(deck, issues)
    _append_adjacent_title_similarity_issues(deck, issues)
    _append_layout_contract_issues(deck, issues)
    _append_risk_matrix_semantic_issues(deck, issues)
    _append_visual_preflight_issues(deck, issues)
    _append_content_style_issues(deck, issues)

    for slide in deck.slides:
        total_element_area = sum(_bbox_area(element.bbox) for element in slide.elements)
        density = total_element_area / slide_area

        if density > 0.75:
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="SLIDE_TOO_DENSE",
                    message=(
                        f"Slide '{slide.slide_id}' is very dense: element bbox area "
                        f"is {density:.2%} of the slide area."
                    ),
                )
            )
        elif density < 0.08:
            issues.append(
                QAIssue(
                    severity="info",
                    slide_id=slide.slide_id,
                    code="SLIDE_TOO_EMPTY",
                    message=(
                        f"Slide '{slide.slide_id}' is sparse: element bbox area "
                        f"is only {density:.2%} of the slide area."
                    ),
                )
            )

        for first, second in combinations(slide.elements, 2):
            overlap = _overlap_area(first.bbox, second.bbox)
            if overlap <= 0:
                continue

            smaller_area = min(_bbox_area(first.bbox), _bbox_area(second.bbox))
            overlap_ratio = overlap / smaller_area
            if overlap >= 0.10 and overlap_ratio >= 0.20:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="BBOX_OVERLAP",
                        message=(
                            f"Elements '{first.element_id}' and '{second.element_id}' "
                            f"overlap by {overlap:.2f} square inches "
                            f"({overlap_ratio:.0%} of the smaller bbox)."
                        ),
                    )
                )

        for element in slide.elements:
            if not isinstance(element, TextElement):
                continue

            text_area = _bbox_area(element.bbox)
            char_density = len(element.text) / text_area
            if char_density > 35:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        element_id=element.element_id,
                        code="TEXT_TOO_LONG",
                        message=(
                            f"Text element '{element.element_id}' may be too long for its bbox: "
                            f"{len(element.text)} characters across {text_area:.2f} square inches."
                        ),
                    )
                )

    return QAReport(deck_id=deck.deck_id, score=_score_for_issues(issues), issues=issues)
