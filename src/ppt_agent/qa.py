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
    "slide_total_text_too_dense",
    "slide_title_too_long",
    "text_element_too_long",
    "card_body_too_long",
    "card_content_imbalance",
    "paragraph_like_slide",
    "long_enumeration",
    "weak_slide_message",
    "generic_content",
    "missing_product_judgment",
    "vague_action",
    "prompt_keyword_repetition",
    "weak_takeaway",
    "instruction_leakage",
    "risk_matrix_malformed_row",
    "risk_matrix_placeholder",
    "comparison_matrix_placeholder",
    "placeholder_content",
    "card_body_contains_subheadings",
    "metric_explanation_contains_risk_governance",
    "closing_action_not_executable",
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
TITLE_TEXT_BUDGETS = {
    "title_slide": 34,
    "section_divider": 42,
    "comparison_matrix": 42,
    "process_flow": 40,
    "risk_matrix": 42,
    "key_takeaway": 46,
    "closing_slide": 44,
}
DEFAULT_TITLE_TEXT_BUDGET = 46
TEXT_ELEMENT_BUDGETS = {
    "title_slide": 110,
    "two_column": 72,
    "three_column": 105,
    "four_cards": 68,
    "metric_cards": 76,
    "comparison_matrix": 90,
    "process_flow": 72,
    "risk_matrix": 78,
    "key_takeaway": 105,
    "closing_slide": 84,
}
DEFAULT_TEXT_ELEMENT_BUDGET = 120
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
GENERIC_CONTENT_PHRASES = {
    "提升效率",
    "降低风险",
    "前置治理",
    "完善机制",
    "加强监控",
    "优化体验",
    "建立闭环",
}
PRODUCT_JUDGMENT_PHRASES = {
    "只承诺",
    "必须人工确认",
    "人工确认",
    "高风险动作必须",
    "失败后回退",
    "回退到人工",
    "低权限任务优先试点",
    "先判断",
    "不能上线",
    "可以上线",
    "不上线",
    "先试点",
    "按角色授权",
    "记录输入",
    "记录工具调用",
    "记录输出版本",
    "权限确认",
    "人工接管",
    "失败回退",
    "校验输出",
    "验证结果",
}
MEASUREMENT_PHRASES = {
    "如何衡量",
    "按周统计",
    "按日统计",
    "按月统计",
    "记录",
    "统计",
    "计算",
    "衡量",
    "观察",
    "复盘",
    "看板",
    "命中率",
    "成功率",
    "转化率",
}
VAGUE_ACTION_PHRASES = {
    "关注风险",
    "理解边界",
    "提升能力",
    "学习技术边界",
    "理解工作流",
    "关注治理",
}
PROMPT_KEYWORD_REPETITION_TERMS = {
    "技术边界",
    "用户需求分析",
    "工作流设计",
    "评估指标",
    "落地风险",
}
WEAK_TAKEAWAY_PHRASES = {
    "总结前文",
    "综合来看",
    "总体来说",
    "以上内容",
    "最后总结",
}
INSTRUCTION_LEAKAGE_PHRASES = {
    "把这一点转化为明确的下一步行动",
    "明确下一步行动",
    "本页必须",
    "该页需要",
    "内容合同",
    "核心判断必须",
    "可执行建议应该",
    "产品经理要清楚ai agent需要理解的技术边界",
    "产品经理要清楚 AI Agent 需要理解的技术边界",
    "将用户要求转化为",
    "根据用户要求",
}
COMPARISON_MATRIX_PLACEHOLDERS = {
    "方案 A",
    "方案 B",
    "Option A",
    "Option B",
}
RISK_MATRIX_PLACEHOLDERS = {
    "risk",
    "impact",
    "mitigation",
}
GENERIC_MATRIX_PLACEHOLDERS = {
    "输入输出",
    "状态管理",
    "工具调用",
    "Input / Output",
    "State",
    "Tool Use",
}
RISK_GOVERNANCE_TERMS = {
    "越权操作",
    "模型幻觉",
    "不可追溯",
    "风险治理",
    "用户过度信任",
    "成本失控",
    "治理建议",
}
WEAK_MITIGATION_PHRASES = {
    "加强治理",
    "完善机制",
    "前置治理",
    "加强监控",
    "关注风险",
}
ACTION_PHRASES = {
    "记录",
    "限制",
    "设置",
    "要求",
    "验证",
    "统计",
    "回退",
    "标注",
    "列出",
    "拆",
    "画",
    "设计",
    "选择",
    "授权",
    "确认",
    "试点",
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


def _cell_like_segments(text: str) -> list[str]:
    segments: list[str] = []
    raw_lines = [line.strip(" -•\t") for line in text.splitlines() if line.strip(" -•\t")]
    if not raw_lines and text.strip():
        raw_lines = [text.strip()]
    for raw_line in raw_lines:
        split_parts = raw_line.split("|") if "|" in raw_line else [raw_line]
        for part in split_parts:
            cleaned = part.strip(" \t-•:：,，.。;；")
            if cleaned:
                segments.append(cleaned)
    return segments


def _normalized_placeholder_segment(text: str) -> str:
    return re.sub(r"[\s_/\-／]+", "", text.strip(" \t-•:：,，.。;；").lower())


def _placeholder_hits(text: str, placeholders: set[str], *, allow_prefix: bool = False) -> list[str]:
    normalized_placeholders = {
        _normalized_placeholder_segment(placeholder): placeholder
        for placeholder in placeholders
    }
    hits: list[str] = []
    for segment in _cell_like_segments(text):
        normalized_segment = _normalized_placeholder_segment(segment)
        if normalized_segment in normalized_placeholders:
            hits.append(normalized_placeholders[normalized_segment])
            continue
        if allow_prefix:
            for normalized_placeholder, placeholder in normalized_placeholders.items():
                if normalized_segment.startswith(normalized_placeholder) and len(normalized_segment) <= (
                    len(normalized_placeholder) + 8
                ):
                    hits.append(placeholder)
    return _dedupe_text(hits)


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _append_text_density_guard_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for slide in deck.slides:
        # Keep this heuristic separate from schema validation: the goal is to
        # warn before renderer overflow, not to reject otherwise valid Deck IR.
        title_length = _text_length_score(slide.title)
        title_budget = TITLE_TEXT_BUDGETS.get(slide.layout, DEFAULT_TITLE_TEXT_BUDGET)
        if title_length > title_budget:
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="slide_title_too_long",
                    message=(
                        f"Slide '{slide.slide_id}' title length score {title_length} exceeds "
                        f"safe title budget {title_budget}. Compress the title before rendering."
                    ),
                )
            )

        text_elements = _text_elements(slide)
        total_chars = sum(_text_length_score(element.text) for element in text_elements)
        total_budget = SLIDE_TEXT_BUDGETS.get(slide.layout, DEFAULT_SLIDE_TEXT_BUDGET)
        if total_chars > total_budget:
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="slide_total_text_too_dense",
                    message=(
                        f"Slide '{slide.slide_id}' has total text length score {total_chars}, "
                        f"above safe budget {total_budget} for layout '{slide.layout}'."
                    ),
                )
            )

        element_budget = TEXT_ELEMENT_BUDGETS.get(slide.layout, DEFAULT_TEXT_ELEMENT_BUDGET)
        for element in _body_texts(slide):
            length_score = _text_length_score(element.text)
            if length_score <= element_budget:
                continue
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    element_id=element.element_id,
                    code="text_element_too_long",
                    message=(
                        f"Text element '{element.element_id}' on slide '{slide.slide_id}' has "
                        f"length score {length_score}, above safe element budget {element_budget}."
                    ),
                )
            )


def _append_card_balance_issues(deck: Deck, issues: list[QAIssue]) -> None:
    expected_slots_by_layout = {
        "two_column": 2,
        "three_column": 3,
        "four_cards": 4,
        "metric_cards": 3,
    }
    for slide in deck.slides:
        if slide.layout not in CARD_GRID_PATTERN_LAYOUTS:
            continue
        body_texts = _body_texts(slide)
        if not body_texts:
            continue
        scores = [(_text_length_score(element.text), element.element_id) for element in body_texts]
        longest_score, longest_element_id = max(scores, key=lambda item: item[0])
        short_scores = [score for score, _element_id in scores if score <= 18]
        sibling_scores = [score for score, element_id in scores if element_id != longest_element_id]
        expected_slots = expected_slots_by_layout.get(slide.layout, len(scores))
        if longest_score < 55:
            continue
        if len(scores) < expected_slots or len(short_scores) >= max(1, len(scores) - 1) or (
            sibling_scores and longest_score >= max(sibling_scores) * 2.5
        ):
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    element_id=longest_element_id,
                    code="card_content_imbalance",
                    message=(
                        f"Slide '{slide.slide_id}' puts most card content into '{longest_element_id}' "
                        "while sibling cards are empty or much shorter. Split the content across cards."
                    ),
                )
            )


def _append_placeholder_guard_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for slide in deck.slides:
        for element in _body_texts(slide):
            generic_hits = _placeholder_hits(element.text, GENERIC_MATRIX_PLACEHOLDERS)

            if slide.layout == "comparison_matrix":
                # Matrix placeholders are harder failures than generic wording
                # because they leak template labels directly into the audience view.
                hits = _dedupe_text(
                    generic_hits
                    + _placeholder_hits(element.text, COMPARISON_MATRIX_PLACEHOLDERS, allow_prefix=True)
                )
                if hits:
                    issues.append(
                        QAIssue(
                            severity="error",
                            slide_id=slide.slide_id,
                            element_id=element.element_id,
                            code="comparison_matrix_placeholder",
                            message=(
                                f"Comparison matrix element '{element.element_id}' uses placeholder "
                                f"label(s): {', '.join(hits[:3])}. Name the actual comparison sides "
                                "and judgment rows."
                            ),
                        )
                    )
                continue

            if slide.layout == "risk_matrix":
                hits = _dedupe_text(generic_hits + _placeholder_hits(element.text, RISK_MATRIX_PLACEHOLDERS))
                if hits:
                    issues.append(
                        QAIssue(
                            severity="error",
                            slide_id=slide.slide_id,
                            element_id=element.element_id,
                            code="risk_matrix_placeholder",
                            message=(
                                f"Risk matrix row '{element.element_id}' contains placeholder "
                                f"cell(s): {', '.join(hits[:3])}. Fill risk, impact, and mitigation "
                                "with concrete content."
                            ),
                        )
                    )
                continue

            if generic_hits:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        element_id=element.element_id,
                        code="placeholder_content",
                        message=(
                            f"Text element '{element.element_id}' on slide '{slide.slide_id}' contains "
                            f"template placeholder term(s): {', '.join(generic_hits[:3])}. "
                            "Replace placeholders with audience-facing content."
                        ),
                    )
                )


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


def _normalized_slide_text(slide) -> str:
    parts = [slide.title]
    parts.extend(element.text for element in _body_texts(slide))
    return "\n".join(part for part in parts if part).strip()


def _contains_any_phrase(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _generic_phrase_hits(text: str) -> list[str]:
    return [phrase for phrase in GENERIC_CONTENT_PHRASES if phrase in text]


def _prompt_keyword_hits(text: str) -> list[str]:
    return [phrase for phrase in PROMPT_KEYWORD_REPETITION_TERMS if phrase in text]


def _looks_like_vague_action(text: str) -> bool:
    lines = _semantic_lines(text)
    return any(line in VAGUE_ACTION_PHRASES for line in lines)


def _has_product_judgment(text: str) -> bool:
    return _contains_any_phrase(text, PRODUCT_JUDGMENT_PHRASES | MEASUREMENT_PHRASES)


def _instruction_leakage_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [phrase for phrase in INSTRUCTION_LEAKAGE_PHRASES if phrase.lower() in lowered]


def _has_action_phrase(text: str) -> bool:
    return _contains_any_phrase(text, ACTION_PHRASES)


def _looks_like_takeaway_risk_cell(text: str) -> bool:
    normalized = text.strip()
    if _text_length_score(normalized) < 12:
        return False
    takeaway_markers = ("要", "必须", "需要", "应该", "被约束", "前置", "设计阶段")
    return "风险" in normalized and any(marker in normalized for marker in takeaway_markers)


def _looks_like_subheading_stack(text: str) -> bool:
    lines = _semantic_lines(text)
    short_title_lines = [
        line for line in lines if _text_length_score(line) <= 10 and not re.search(r"[。.!?；;:：]", line)
    ]
    if len(short_title_lines) >= 2 and len(lines) >= 2:
        return True

    if "/" in text or "／" in text:
        segments = [
            segment.strip(" /／")
            for segment in re.split(r"[／/]", text)
            if segment.strip(" /／")
        ]
        compact_segments = [segment for segment in segments if _text_length_score(segment) <= 10]
        if len(compact_segments) >= 3:
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


def _append_anti_generic_issues(deck: Deck, issues: list[QAIssue]) -> None:
    for slide in deck.slides:
        combined_text = _normalized_slide_text(slide)
        if not combined_text:
            continue

        instruction_hits = _instruction_leakage_hits(combined_text)
        if instruction_hits:
            issues.append(
                QAIssue(
                    severity="error",
                    slide_id=slide.slide_id,
                    code="instruction_leakage",
                    message=(
                        f"Slide '{slide.slide_id}' contains prompt-like meta language such as "
                        f"{', '.join(instruction_hits[:3])}. Rewrite it as audience-facing content."
                    ),
                )
            )

        generic_hits = _generic_phrase_hits(combined_text)
        if generic_hits and not _has_product_judgment(combined_text):
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="generic_content",
                    message=(
                        f"Slide '{slide.slide_id}' uses generic phrases like {', '.join(generic_hits[:3])} "
                        "without a concrete product action or operating judgment."
                    ),
                )
            )

        keyword_hits = _prompt_keyword_hits(combined_text)
        if len(keyword_hits) >= 3 and not _has_product_judgment(combined_text):
            issues.append(
                QAIssue(
                    severity="warning",
                    slide_id=slide.slide_id,
                    code="prompt_keyword_repetition",
                    message=(
                        f"Slide '{slide.slide_id}' repeats prompt keywords such as "
                        f"{', '.join(keyword_hits[:4])} without concrete expansion."
                    ),
                )
            )

        if slide.layout in {"comparison_matrix", "two_column", "three_column", "four_cards", "process_flow", "metric_cards"}:
            if not _has_product_judgment(combined_text):
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="missing_product_judgment",
                        message=(
                            f"Slide '{slide.slide_id}' explains concepts but does not state a clear product decision, "
                            "boundary, control point, fallback rule, or measurement rule."
                        ),
                    )
                )

        if slide.layout in {"two_column", "three_column", "four_cards"}:
            # Card layouts drift easily when the model compresses several mini-topics
            # into one body. Warn early so retries preserve one card = one judgment.
            stacked_elements = [
                element.element_id
                for element in _body_texts(slide)
                if _looks_like_subheading_stack(element.text)
            ]
            if stacked_elements:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="card_body_contains_subheadings",
                        message=(
                            f"Slide '{slide.slide_id}' stacks mini-headings inside body elements "
                            f"{', '.join(stacked_elements)}. Keep each card to one heading and one body."
                        ),
                    )
                )

        if slide.layout == "metric_cards":
            governance_elements = [
                element.element_id
                for element in _body_texts(slide)
                if _contains_any_phrase(element.text, RISK_GOVERNANCE_TERMS)
                and not _contains_any_phrase(element.text, MEASUREMENT_PHRASES)
            ]
            if governance_elements:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="metric_explanation_contains_risk_governance",
                        message=(
                            f"Metric slide '{slide.slide_id}' uses governance or risk-list language in "
                            f"{', '.join(governance_elements)} instead of explaining how the metric is measured."
                        ),
                    )
                )

        if slide.layout == "closing_slide":
            vague_elements = [
                element.element_id
                for element in _body_texts(slide)
                if _looks_like_vague_action(element.text)
            ]
            if vague_elements:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="vague_action",
                        message=(
                            f"Closing slide '{slide.slide_id}' contains vague action items in "
                            f"{', '.join(vague_elements)}. Replace slogans with executable next steps."
                        ),
                    )
                )

            non_executable_elements = [
                element.element_id
                for element in _body_texts(slide)
                if _instruction_leakage_hits(element.text) or not _has_action_phrase(element.text)
            ]
            if non_executable_elements:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="closing_action_not_executable",
                        message=(
                            f"Closing slide '{slide.slide_id}' has non-executable action text in "
                            f"{', '.join(non_executable_elements)}. Use a concrete verb plus object."
                        ),
                    )
                )

        if slide.layout in {"key_takeaway", "closing_slide"}:
            if not _has_product_judgment(combined_text) or _contains_any_phrase(combined_text, WEAK_TAKEAWAY_PHRASES):
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        code="weak_takeaway",
                        message=(
                            f"Slide '{slide.slide_id}' needs a stronger takeaway or tradeoff principle instead of a recap."
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
            # This stays heuristic on purpose: we want actionable warnings for bad
            # row semantics without turning content-quality misses into hard render failures.
            risk, impact, mitigation = _risk_matrix_parts(element.text)
            malformed_reasons: list[str] = []
            if not risk:
                malformed_reasons.append("missing risk")
            elif _looks_like_takeaway_risk_cell(risk):
                malformed_reasons.append("risk cell looks like a slide takeaway instead of a risk item")
            if not impact or _text_length_score(impact) < 6:
                malformed_reasons.append("impact is missing or too short")
            if not mitigation:
                malformed_reasons.append("mitigation is missing")
            elif mitigation in WEAK_MITIGATION_PHRASES or not _has_action_phrase(mitigation):
                malformed_reasons.append("mitigation is not a concrete action")

            if malformed_reasons:
                issues.append(
                    QAIssue(
                        severity="warning",
                        slide_id=slide.slide_id,
                        element_id=element.element_id,
                        code="risk_matrix_malformed_row",
                        message=(
                            f"Risk matrix row '{element.element_id}' on slide '{slide.slide_id}' is malformed: "
                            f"{'; '.join(malformed_reasons)}."
                        ),
                    )
                )
            if not mitigation:
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
    _append_text_density_guard_issues(deck, issues)
    _append_card_balance_issues(deck, issues)
    _append_placeholder_guard_issues(deck, issues)
    _append_content_style_issues(deck, issues)
    _append_anti_generic_issues(deck, issues)

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
