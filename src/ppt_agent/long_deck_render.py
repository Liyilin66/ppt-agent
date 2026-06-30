"""Offline rendering helpers for stitched long-deck Deck IR artifacts."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import Field

from ppt_agent.export import write_model_json
from ppt_agent.load import load_deck, load_theme
from ppt_agent.models import Deck, StrictModel
from ppt_agent.qa import (
    COMPARISON_MATRIX_PLACEHOLDERS,
    GENERIC_MATRIX_PLACEHOLDERS,
    INSTRUCTION_LEAKAGE_PHRASES,
    RISK_MATRIX_PLACEHOLDERS,
)
from ppt_agent.renderer import render_deck_to_pptx
from ppt_agent.runtime import sanitize_error_message, utc_now_iso


class LongDeckRenderReport(StrictModel):
    status: Literal["succeeded", "failed"]
    input_deck_ir_path: Path
    output_pptx_path: Path
    slide_count: int | None = Field(default=None, ge=0)
    error_message: str | None = None
    generated_at: str
    warnings: list[str] = Field(default_factory=list)


DEFAULT_TITLE_BBOX = {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.6}
DEFAULT_BODY_BBOXES = [
    {"x": 0.8, "y": 1.55, "width": 5.5, "height": 1.0},
    {"x": 6.9, "y": 1.55, "width": 5.5, "height": 1.0},
    {"x": 0.8, "y": 3.25, "width": 5.5, "height": 1.0},
]
SAFE_ACTIONS_ZH = ["明确边界", "设计确认点", "记录失败样本", "建立评估指标"]
SAFE_ACTIONS_EN = [
    "Define the boundary",
    "Design a confirmation point",
    "Record failure samples",
    "Set one evaluation metric",
]


def _normalized_segment(text: str) -> str:
    return re.sub(r"[\s_/\-／|]+", "", text.strip(" \t-•:：,，.。;；").lower())


def _normalized_placeholders(placeholders: set[str]) -> set[str]:
    return {_normalized_segment(placeholder) for placeholder in placeholders}


def _text_elements(slide_payload: dict) -> list[dict]:
    return [element for element in slide_payload.get("elements", []) if element.get("type") == "text"]


def _body_text_elements(slide_payload: dict) -> list[dict]:
    text_elements = _text_elements(slide_payload)
    return text_elements[1:] if text_elements else []


def _semantic_lines(text: str) -> list[str]:
    lines = [
        line.strip(" -•\t")
        for line in text.splitlines()
        if line.strip(" -•\t")
    ]
    return lines or ([text.strip()] if text.strip() else [])


def _cell_segments(text: str) -> list[str]:
    segments: list[str] = []
    for line in _semantic_lines(text):
        parts = line.split("|") if "|" in line else [line]
        for part in parts:
            cleaned = part.strip(" \t-•:：,，.。;；")
            if cleaned:
                segments.append(cleaned)
    return segments


def _contains_instruction_leakage(text: str) -> bool:
    lowered = text.lower()
    return any(phrase.lower() in lowered for phrase in INSTRUCTION_LEAKAGE_PHRASES)


def _strip_instruction_leakage_lines(text: str) -> tuple[str, bool]:
    kept_lines: list[str] = []
    removed = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _contains_instruction_leakage(stripped):
            removed = True
            continue
        kept_lines.append(stripped)
    if kept_lines:
        return "\n".join(kept_lines), removed
    if text.strip() and _contains_instruction_leakage(text.strip()):
        return "", True
    return text.strip(), removed


def _is_placeholder_segment(text: str, placeholders: set[str]) -> bool:
    normalized = _normalized_segment(text)
    return bool(normalized) and normalized in _normalized_placeholders(placeholders)


def _clean_placeholder_lines(text: str, placeholders: set[str]) -> tuple[str, bool]:
    cleaned_lines: list[str] = []
    removed = False
    for line in _semantic_lines(text):
        if _is_placeholder_segment(line, placeholders):
            removed = True
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines), removed


def _safe_action_from_title(title: str, index: int = 0) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in title):
        if "边界" in title:
            return "明确边界"
        if "工作流" in title or "流程" in title or "闭环" in title:
            return "设计确认点"
        if "风险" in title:
            return "记录失败样本"
        if "指标" in title or "评估" in title:
            return "建立评估指标"
        return SAFE_ACTIONS_ZH[index % len(SAFE_ACTIONS_ZH)]
    lowered = title.lower()
    if "boundary" in lowered:
        return "Define the boundary"
    if "workflow" in lowered or "process" in lowered:
        return "Design a confirmation point"
    if "risk" in lowered:
        return "Record failure samples"
    if "metric" in lowered or "evaluation" in lowered:
        return "Set one evaluation metric"
    return SAFE_ACTIONS_EN[index % len(SAFE_ACTIONS_EN)]


def _replace_slide_as_text_layout(
    slide_payload: dict,
    *,
    layout: str,
    title_text: str,
    body_texts: list[str],
) -> None:
    original_text_elements = _text_elements(slide_payload)
    title_bbox = deepcopy(original_text_elements[0]["bbox"]) if original_text_elements else deepcopy(DEFAULT_TITLE_BBOX)
    elements = [
        {
            "element_id": f"{slide_payload['slide_id']}_title",
            "type": "text",
            "bbox": title_bbox,
            "text": title_text,
        }
    ]
    usable_body_texts = [text.strip() for text in body_texts if text and text.strip()]
    if not usable_body_texts:
        usable_body_texts = [_safe_action_from_title(title_text)]
    for index, text in enumerate(usable_body_texts[:3], start=1):
        if index < len(original_text_elements):
            bbox = deepcopy(original_text_elements[index]["bbox"])
        else:
            bbox = deepcopy(DEFAULT_BODY_BBOXES[min(index - 1, len(DEFAULT_BODY_BBOXES) - 1)])
        elements.append(
            {
                "element_id": f"{slide_payload['slide_id']}_body_{index:02d}",
                "type": "text",
                "bbox": bbox,
                "text": text,
            }
        )
    slide_payload["title"] = title_text
    slide_payload["layout"] = layout
    slide_payload["elements"] = elements


def _risk_row_parts(text: str) -> tuple[str, str, str]:
    lines = _semantic_lines(text)
    if len(lines) == 1 and "|" in lines[0]:
        lines = [part.strip() for part in lines[0].split("|") if part.strip()]
    cleaned: list[str] = []
    for line in lines[:3]:
        cleaned.append(
            re.sub(r"^(risk|impact|mitigation|风险|影响|缓解措施|缓解)[:：]\s*", "", line, flags=re.I).strip()
        )
    while len(cleaned) < 3:
        cleaned.append("")
    return cleaned[0], cleaned[1], cleaned[2]


def _sanitize_risk_matrix_slide(slide_payload: dict, warnings: list[str]) -> None:
    title_text = slide_payload.get("title", "").strip() or "Risk Review"
    placeholder_terms = RISK_MATRIX_PLACEHOLDERS | GENERIC_MATRIX_PLACEHOLDERS
    cleaned_rows: list[str] = []

    for element in _body_text_elements(slide_payload):
        row_text, removed_instruction = _strip_instruction_leakage_lines(str(element.get("text", "")))
        if removed_instruction:
            warnings.append(f"{slide_payload['slide_id']}: removed instruction leakage from risk_matrix row.")
        risk, impact, mitigation = _risk_row_parts(row_text)
        cells = [risk, impact, mitigation]
        normalized_cells = [
            ""
            if not cell.strip() or _is_placeholder_segment(cell, placeholder_terms)
            else cell.strip()
            for cell in cells
        ]
        risk, impact, mitigation = normalized_cells
        if not risk:
            if any(cell.strip() for cell in cells):
                warnings.append(f"{slide_payload['slide_id']}: dropped placeholder-only risk matrix row.")
            continue
        row_parts = [risk]
        if impact:
            row_parts.append(impact)
        if mitigation:
            row_parts.append(mitigation)
        cleaned_rows.append("\n".join(row_parts))

    if len(cleaned_rows) >= 2:
        _replace_slide_as_text_layout(
            slide_payload,
            layout="risk_matrix",
            title_text=title_text,
            body_texts=cleaned_rows[:4],
        )
        return

    warnings.append(
        f"{slide_payload['slide_id']}: risk_matrix had fewer than two real risk rows after sanitization; rendered as text fallback."
    )
    fallback_rows = cleaned_rows[:]
    if not fallback_rows:
        fallback_rows = [
            f"{_safe_action_from_title(title_text, 0)}\n{_safe_action_from_title(title_text, 1)}",
            f"{_safe_action_from_title(title_text, 2)}\n{_safe_action_from_title(title_text, 3)}",
        ]
    _replace_slide_as_text_layout(
        slide_payload,
        layout="two_column",
        title_text=title_text,
        body_texts=fallback_rows[:2],
    )


def _split_comparison_side(text: str, placeholders: set[str]) -> tuple[str, list[str], bool]:
    cleaned_text, removed_instruction = _strip_instruction_leakage_lines(text)
    cleaned_text, removed_placeholder = _clean_placeholder_lines(cleaned_text, placeholders)
    lines = _semantic_lines(cleaned_text)
    if not lines:
        return "", [], removed_instruction or removed_placeholder
    if len(lines) == 1:
        return "", lines, removed_instruction or removed_placeholder
    return lines[0], lines[1:], removed_instruction or removed_placeholder


def _sanitize_comparison_matrix_slide(slide_payload: dict, warnings: list[str]) -> None:
    title_text = slide_payload.get("title", "").strip() or "Comparison"
    placeholders = COMPARISON_MATRIX_PLACEHOLDERS | GENERIC_MATRIX_PLACEHOLDERS
    body_elements = _body_text_elements(slide_payload)
    left_text = str(body_elements[0].get("text", "")) if len(body_elements) >= 1 else ""
    right_text = str(body_elements[1].get("text", "")) if len(body_elements) >= 2 else ""
    decision_text = str(body_elements[2].get("text", "")) if len(body_elements) >= 3 else ""

    left_heading, left_points, left_removed = _split_comparison_side(left_text, placeholders)
    right_heading, right_points, right_removed = _split_comparison_side(right_text, placeholders)
    decision_clean, decision_removed_instruction = _strip_instruction_leakage_lines(decision_text)
    decision_clean, decision_removed_placeholder = _clean_placeholder_lines(decision_clean, placeholders)
    if left_removed or right_removed or decision_removed_instruction or decision_removed_placeholder:
        warnings.append(f"{slide_payload['slide_id']}: removed comparison matrix placeholder or instruction text.")

    real_row_count = max(len(left_points), len(right_points))
    if real_row_count >= 2:
        left_block = "\n".join(([left_heading] if left_heading else []) + left_points[:5])
        right_block = "\n".join(([right_heading] if right_heading else []) + right_points[:5])
        body_texts = [left_block, right_block]
        if decision_clean.strip():
            body_texts.append(decision_clean.strip())
        _replace_slide_as_text_layout(
            slide_payload,
            layout="comparison_matrix",
            title_text=title_text,
            body_texts=body_texts,
        )
        return

    warnings.append(
        f"{slide_payload['slide_id']}: comparison_matrix had fewer than two real comparison rows after sanitization; rendered as text fallback."
    )
    fallback_left = "\n".join(([left_heading] if left_heading else []) + left_points[:3]).strip()
    fallback_right = "\n".join(([right_heading] if right_heading else []) + right_points[:3]).strip()
    fallback_body_texts = [text for text in [fallback_left, fallback_right, decision_clean.strip()] if text]
    if not fallback_body_texts:
        fallback_body_texts = [
            _safe_action_from_title(title_text, 0),
            _safe_action_from_title(title_text, 1),
        ]
    _replace_slide_as_text_layout(
        slide_payload,
        layout="two_column",
        title_text=title_text,
        body_texts=fallback_body_texts[:2],
    )


def sanitize_deck_ir_for_render(deck_ir: Deck) -> tuple[Deck, list[str]]:
    warnings: list[str] = []
    payload = deck_ir.model_dump(mode="json")

    for slide_payload in payload["slides"]:
        text_elements = _text_elements(slide_payload)
        for element in text_elements[1:]:
            cleaned_text, removed = _strip_instruction_leakage_lines(str(element.get("text", "")))
            if removed:
                warnings.append(f"{slide_payload['slide_id']}: removed instruction leakage from text content.")
            element["text"] = cleaned_text

        if slide_payload["layout"] == "risk_matrix":
            _sanitize_risk_matrix_slide(slide_payload, warnings)
        elif slide_payload["layout"] == "comparison_matrix":
            _sanitize_comparison_matrix_slide(slide_payload, warnings)
        elif slide_payload["layout"] in {"closing_slide", "key_takeaway"}:
            body_elements = _body_text_elements(slide_payload)
            if not any(str(element.get("text", "")).strip() for element in body_elements):
                _replace_slide_as_text_layout(
                    slide_payload,
                    layout=slide_payload["layout"],
                    title_text=slide_payload.get("title", "").strip() or "Next steps",
                    body_texts=[_safe_action_from_title(slide_payload.get("title", ""), index) for index in range(3)],
                )
                warnings.append(f"{slide_payload['slide_id']}: inserted safe fallback actions after removing empty action text.")

    return Deck.model_validate(payload), warnings


def _write_render_report(report: LongDeckRenderReport, report_path: str | Path) -> LongDeckRenderReport:
    write_model_json(report, report_path)
    return report


def _failure_report(
    *,
    input_deck_ir_path: Path,
    output_pptx_path: Path,
    report_path: Path,
    error: object,
    slide_count: int | None = None,
) -> LongDeckRenderReport:
    report = LongDeckRenderReport(
        status="failed",
        input_deck_ir_path=input_deck_ir_path,
        output_pptx_path=output_pptx_path,
        slide_count=slide_count,
        error_message=sanitize_error_message(error),
        generated_at=utc_now_iso(),
    )
    return _write_render_report(report, report_path)


def render_long_deck_ir_to_pptx(
    input_deck_ir_path: str | Path,
    output_pptx_path: str | Path,
    report_path: str | Path,
    *,
    theme_path: str | Path,
    assets_dir: str | Path | None = None,
) -> LongDeckRenderReport:
    """Render an already-generated long Deck IR to PPTX without calling an LLM."""

    resolved_input = Path(input_deck_ir_path)
    resolved_output = Path(output_pptx_path)
    resolved_report = Path(report_path)
    resolved_theme = Path(theme_path)
    resolved_assets = Path(assets_dir) if assets_dir is not None else None

    if not resolved_input.exists():
        return _failure_report(
            input_deck_ir_path=resolved_input,
            output_pptx_path=resolved_output,
            report_path=resolved_report,
            error=f"Input Deck IR not found: {resolved_input}",
        )

    deck = None
    try:
        deck = load_deck(resolved_input)
        deck, render_warnings = sanitize_deck_ir_for_render(deck)
        theme = load_theme(resolved_theme)
        render_deck_to_pptx(
            deck,
            theme,
            resolved_output,
            assets_dir=resolved_assets,
        )
    except Exception as exc:
        return _failure_report(
            input_deck_ir_path=resolved_input,
            output_pptx_path=resolved_output,
            report_path=resolved_report,
            error=exc,
            slide_count=len(deck.slides) if deck is not None else None,
        )

    report = LongDeckRenderReport(
        status="succeeded",
        input_deck_ir_path=resolved_input,
        output_pptx_path=resolved_output,
        slide_count=len(deck.slides),
        error_message=None,
        generated_at=utc_now_iso(),
        warnings=render_warnings,
    )
    return _write_render_report(report, resolved_report)
