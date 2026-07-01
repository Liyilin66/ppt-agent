"""Export Deck IR as a source Markdown brief for ppt-master experiments."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ppt_agent.models import Deck, Slide


DEFAULT_STYLE_DIRECTION = (
    "技术产品分享",
    "不像营销材料",
    "极淡蓝绿色背景",
    "每页一个明确观点",
    "页面要有设计变化，不要全是卡片",
    "尽量使用可编辑 PowerPoint 原生元素",
    "避免大段文字",
    "避免模板占位词",
    "避免把生成指令写进正文",
)

NOISE_PREFIX_RE = re.compile(
    r"^\s*(?:risk|impact|mitigation|instruction\s+leakage|风险|影响|缓解措施|应对措施)\s*[：:]\s*",
    re.IGNORECASE,
)
PLACEHOLDER_PREFIX_RE = re.compile(
    r"^\s*(?:判断点\s*[一二三123]|方案\s*[ABＡＢ]|option\s*[AB])\s*(?:[：:.)、-]\s*)?",
    re.IGNORECASE,
)
STANDALONE_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:"
    r"risk|impact|mitigation|"
    r"risk\s*/\s*impact\s*/\s*mitigation|"
    r"判断点\s*[一二三123]|"
    r"方案\s*[ABＡＢ]|"
    r"方案\s*A\s*/\s*方案\s*B|"
    r"option\s*[AB]|"
    r"option\s*A\s*/\s*option\s*B"
    r")\s*$",
    re.IGNORECASE,
)
INSTRUCTION_LEAKAGE_RE = re.compile(
    r"\b(?:instruction\s+leakage|instruction[-_ ]?leak|prompt\s+leakage|risk_label_prefix_leakage)\b",
    re.IGNORECASE,
)
BULLET_MARKER_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)、]\s*|[A-Za-z][.)]\s*)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。.!?！？])\s+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def export_deck_ir_to_ppt_master_markdown(
    deck_ir: Deck | Mapping[str, Any],
    output_path: str | Path,
    *,
    style_notes: str | Iterable[str] | None = None,
) -> Path:
    """Write a human-readable ppt-master source Markdown file from Deck IR."""

    deck = deck_ir if isinstance(deck_ir, Deck) else Deck.model_validate(deck_ir)
    markdown = _build_markdown(deck, style_notes=style_notes)
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(markdown, encoding="utf-8")
    return resolved_output_path


def _build_markdown(deck: Deck, *, style_notes: str | Iterable[str] | None) -> str:
    language = "zh-CN" if _deck_uses_cjk(deck) else "en-US"
    audience = "沿用原始 Deck IR 生成请求中的目标观众"

    lines = [
        "# Presentation Request",
        "",
        "## Topic",
        _clean_text(deck.title) or "Untitled presentation",
        "",
        "## Audience",
        audience,
        "",
        "## Language",
        language,
        "",
        "## Style Direction",
        *_format_bullets([*DEFAULT_STYLE_DIRECTION, *_style_note_items(style_notes)]),
        "",
        "## Global Requirements",
        *_format_bullets(_global_requirements(len(deck.slides))),
        "",
        "## Slide-by-slide Outline",
        "",
    ]

    for index, slide in enumerate(deck.slides, start=1):
        title = _clean_text(slide.title) or f"Slide {index}"
        key_message = _key_message_for_slide(slide)
        purpose = _purpose_for_slide(slide, key_message=key_message)
        content_bullets = _content_bullets_for_slide(slide, key_message=key_message)
        visual_bullets = _visual_direction_for_slide(slide)

        lines.extend(
            [
                f"### Slide {index}: {title}",
                "Purpose:",
                purpose,
                "",
                "Key message:",
                key_message,
                "",
                "Suggested content:",
                *_format_bullets(content_bullets),
                "",
                "Visual direction:",
                *_format_bullets(visual_bullets),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _global_requirements(slide_count: int) -> list[str]:
    return [
        "生成 16:9 editable PPTX",
        f"保留 {slide_count} 页结构",
        "保留章节推进",
        "不要每 10 页重复开场",
        "不要把风险、影响、缓解措施当作固定正文标签",
        "不要使用编号判断点占位标题",
        "不要使用 A/B 方案或选项占位标题",
        "输出应面向观众，不暴露生成过程、内部字段或模板指令",
    ]


def _style_note_items(style_notes: str | Iterable[str] | None) -> list[str]:
    if style_notes is None:
        return []
    if isinstance(style_notes, str):
        return [style_notes]
    return [str(note) for note in style_notes]


def _deck_uses_cjk(deck: Deck) -> bool:
    if CJK_RE.search(deck.title):
        return True
    return any(
        CJK_RE.search(element.text)
        for slide in deck.slides
        for element in slide.elements
        if element.type == "text"
    )


def _purpose_for_slide(slide: Slide, *, key_message: str) -> str:
    title = _clean_text(slide.title) or "this slide"
    if _slide_uses_cjk(slide):
        return _truncate_sentence(f"说明“{title}”在整体叙事中的作用，并让观众记住：{key_message}", limit=110)
    return _truncate_sentence(f"Clarify the role of {title} in the story and land the point: {key_message}", limit=110)


def _key_message_for_slide(slide: Slide) -> str:
    title = _clean_text(slide.title)
    for candidate in _text_candidates(slide):
        if title and candidate.casefold() == title.casefold():
            continue
        return _truncate_sentence(candidate, limit=96)
    return _truncate_sentence(title or "Clarify the slide's central point.", limit=96)


def _content_bullets_for_slide(slide: Slide, *, key_message: str) -> list[str]:
    title = _clean_text(slide.title)
    bullets: list[str] = []
    seen: set[str] = set()

    for candidate in _text_candidates(slide):
        if title and candidate.casefold() == title.casefold():
            continue
        compact = _truncate_sentence(candidate, limit=86)
        normalized = compact.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        bullets.append(compact)
        if len(bullets) >= 5:
            break

    return bullets or [key_message]


def _visual_direction_for_slide(slide: Slide) -> list[str]:
    layout = slide.layout.strip().lower()
    has_image = any(element.type == "image" for element in slide.elements)

    if layout == "risk_matrix":
        return [
            "Use a two-axis decision map with short axis labels and concise quadrant language.",
            "Keep labels business-readable; avoid template taxonomy as audience-visible text.",
        ]
    if layout == "comparison_matrix":
        return [
            "Use a side-by-side comparison grid with meaningful column names and 2-4 criteria.",
            "Highlight the preferred direction with emphasis, not placeholder option labels.",
        ]
    if layout == "process_flow":
        return [
            "Use a left-to-right flow or swimlane with 3-5 steps and one clear transition per step.",
            "Add small icons or connectors only where they clarify the workflow.",
        ]
    if layout == "metric_cards":
        return [
            "Use compact metric tiles with one headline number or signal per tile.",
            "Vary tile scale and grouping so the slide does not read as a generic card wall.",
        ]
    if layout in {"title", "cover"}:
        return [
            "Use a strong cover composition with restrained type and one visual anchor.",
            "Keep the first slide polished and sparse.",
        ]
    if "section" in layout:
        return [
            "Use a section-divider rhythm with one dominant statement and subtle progress cues.",
            "Make the transition feel different from normal content pages.",
        ]
    if has_image:
        return [
            "Use the visual as the main evidence area with concise annotation text.",
            "Keep the supporting text short enough to scan while presenting.",
        ]
    return [
        "Use a distinct layout rhythm that supports the slide's single key message.",
        "Prefer editable native shapes, diagrams, callouts, or light annotation over repeated cards.",
    ]


def _slide_uses_cjk(slide: Slide) -> bool:
    if CJK_RE.search(slide.title):
        return True
    return any(
        CJK_RE.search(element.text)
        for element in slide.elements
        if element.type == "text"
    )


def _text_candidates(slide: Slide) -> list[str]:
    candidates: list[str] = []
    for element in slide.elements:
        if element.type != "text":
            continue
        for raw_line in element.text.splitlines():
            candidate = _clean_text(raw_line)
            if candidate:
                candidates.append(candidate)
    return candidates


def _clean_text(value: str) -> str:
    text = value.replace("\r", "\n").strip()
    text = BULLET_MARKER_RE.sub("", text).strip()
    previous = None
    while previous != text:
        previous = text
        text = NOISE_PREFIX_RE.sub("", text).strip()
        text = PLACEHOLDER_PREFIX_RE.sub("", text).strip()
    text = INSTRUCTION_LEAKAGE_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text).strip(" -:：/|")
    if not text or STANDALONE_PLACEHOLDER_RE.fullmatch(text):
        return ""
    return text


def _truncate_sentence(value: str, *, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text

    sentence = SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    if sentence and len(sentence) <= limit:
        return sentence

    for separator in ("。", "；", ";", "，", ",", ":"):
        head = text.split(separator, 1)[0].strip()
        if 18 <= len(head) <= limit:
            return head

    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_bullets(values: Iterable[str]) -> list[str]:
    bullets: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            bullets.append(f"- {cleaned}")
    return bullets
