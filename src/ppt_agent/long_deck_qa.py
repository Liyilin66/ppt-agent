"""Deterministic cross-batch QA for merged long decks."""

from __future__ import annotations

import re
import string
from collections import Counter
from itertools import pairwise
from typing import Literal

from pydantic import Field

from ppt_agent.models import Deck, Slide, StrictModel
from ppt_agent.planning import BatchPlan, LongDeckPlan, SectionPlan
from ppt_agent.long_deck import validate_merged_long_deck_ir


TITLE_SIMILARITY_THRESHOLD = 0.92
TEXT_SIMILARITY_THRESHOLD = 0.9
NEARBY_TEXT_SIMILARITY_THRESHOLD = 0.9
SECTION_KEYWORD_COVERAGE_THRESHOLD = 0.34
LONG_DECK_PASS_THRESHOLD = 0.75
OPENING_WORDS = ("背景", "介绍", "概览", "什么是", "overview", "introduction", "background")
TRANSITION_WORDS = ("因此", "接下来", "下一步", "在此基础上", "随后", "then", "next")
CONCLUSION_NEW_TOPIC_WORDS = ("另外", "补充背景", "重新介绍", "什么是", "overview", "background")
TOKEN_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "about",
    "slide",
    "point",
    "产品",
    "我们",
    "你们",
    "他们",
    "这个",
    "那个",
    "以及",
    "然后",
}


class LongDeckQAIssue(StrictModel):
    issue_type: str = Field(..., min_length=1)
    severity: Literal["warning", "critical"]
    slide_ids: list[str] = Field(default_factory=list)
    batch_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    message: str = Field(..., min_length=1)
    suggestion: str = Field(..., min_length=1)


class SectionQAResult(StrictModel):
    section_id: str = Field(..., min_length=1)
    score: float = Field(..., ge=0, le=1)
    issue_count: int = Field(default=0, ge=0)
    covered_key_messages: list[str] = Field(default_factory=list)
    missing_must_include: list[str] = Field(default_factory=list)
    violated_must_avoid: list[str] = Field(default_factory=list)


class BatchQAResult(StrictModel):
    batch_id: str = Field(..., min_length=1)
    score: float = Field(..., ge=0, le=1)
    issue_count: int = Field(default=0, ge=0)
    transition_ok: bool = True
    repeated_slide_ids: list[str] = Field(default_factory=list)


class LongDeckQAReport(StrictModel):
    passed: bool
    score: float = Field(..., ge=0, le=1)
    issues: list[LongDeckQAIssue] = Field(default_factory=list)
    repetition_issues: list[LongDeckQAIssue] = Field(default_factory=list)
    coverage_issues: list[LongDeckQAIssue] = Field(default_factory=list)
    transition_issues: list[LongDeckQAIssue] = Field(default_factory=list)
    section_scores: list[SectionQAResult] = Field(default_factory=list)
    batch_scores: list[BatchQAResult] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


def _slide_number(slide_id: str) -> int:
    return int(slide_id.split("_")[-1])


def _section_for_slide(long_deck_plan: LongDeckPlan, slide_number: int) -> SectionPlan:
    for section in long_deck_plan.sections:
        if section.start_slide <= slide_number <= section.end_slide:
            return section
    raise ValueError(f"No section found for slide {slide_number}.")


def _batch_for_slide(long_deck_plan: LongDeckPlan, slide_number: int) -> BatchPlan:
    for batch in long_deck_plan.batches:
        if batch.start_slide <= slide_number <= batch.end_slide:
            return batch
    raise ValueError(f"No batch found for slide {slide_number}.")


def _slide_text(slide: Slide) -> str:
    text_blocks = [slide.title]
    text_blocks.extend(
        element.text.strip()
        for element in slide.elements
        if element.type == "text" and element.text.strip()
    )
    return "\n".join(text_blocks)


def _slide_body_text(slide: Slide) -> str:
    return "\n".join(
        element.text.strip()
        for element in slide.elements
        if element.type == "text" and element.text.strip()
    )


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    table = str.maketrans({char: " " for char in string.punctuation + "，。；：！？、（）【】《》“”‘’/\\-_"})
    cleaned = lowered.translate(table)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _tokenize(text: str) -> set[str]:
    normalized = _normalize_text(text)
    parts = [part for part in normalized.split(" ") if len(part) >= 2 and part not in TOKEN_STOPWORDS]
    if parts:
        return set(parts)
    compact = normalized.replace(" ", "")
    return {
        compact[index : index + 2]
        for index in range(max(len(compact) - 1, 0))
        if len(compact[index : index + 2]) == 2
    }


def _jaccard_similarity(first: str, second: str) -> float:
    first_tokens = _tokenize(first)
    second_tokens = _tokenize(second)
    if not first_tokens or not second_tokens:
        return 0.0
    union = first_tokens | second_tokens
    if not union:
        return 0.0
    return len(first_tokens & second_tokens) / len(union)


def _substring_overlap(first: str, second: str) -> float:
    left = _normalize_text(first).replace(" ", "")
    right = _normalize_text(second).replace(" ", "")
    if not left or not right:
        return 0.0
    shorter, longer = sorted([left, right], key=len)
    if shorter in longer:
        return len(shorter) / max(len(longer), 1)
    return 0.0


def _text_similarity(first: str, second: str) -> float:
    return max(_jaccard_similarity(first, second), _substring_overlap(first, second))


def _contains_phrase(text: str, phrase: str) -> bool:
    return _normalize_text(phrase) in _normalize_text(text)


def _keywords_from_phrase(text: str) -> list[str]:
    tokens = [token for token in _tokenize(text) if len(token) >= 2]
    if tokens:
        return list(tokens)
    compact = _normalize_text(text).replace(" ", "")
    return [compact[index : index + 2] for index in range(max(len(compact) - 1, 0))]


def _keyword_coverage(section_text: str, phrase: str) -> float:
    keywords = _keywords_from_phrase(phrase)
    if not keywords:
        return 0.0
    matches = sum(1 for keyword in keywords if keyword and keyword in _normalize_text(section_text))
    return matches / len(keywords)


def _score_issues(issues: list[LongDeckQAIssue]) -> float:
    score = 1.0
    for issue in issues:
        if issue.severity == "critical":
            score -= 0.18
        else:
            score -= 0.06
    return max(0.0, min(1.0, score))


def _issue(
    *,
    issue_type: str,
    severity: Literal["warning", "critical"],
    message: str,
    suggestion: str,
    slide_ids: list[str] | None = None,
    batch_ids: list[str] | None = None,
    section_ids: list[str] | None = None,
) -> LongDeckQAIssue:
    return LongDeckQAIssue(
        issue_type=issue_type,
        severity=severity,
        slide_ids=slide_ids or [],
        batch_ids=batch_ids or [],
        section_ids=section_ids or [],
        message=message,
        suggestion=suggestion,
    )


def _repetition_issues(deck_ir: Deck, long_deck_plan: LongDeckPlan) -> list[LongDeckQAIssue]:
    issues: list[LongDeckQAIssue] = []
    slides = deck_ir.slides

    title_groups: dict[str, list[Slide]] = {}
    for slide in slides:
        title_groups.setdefault(_normalize_text(slide.title), []).append(slide)
    for normalized_title, group in title_groups.items():
        if normalized_title and len(group) > 1:
            issues.append(
                _issue(
                    issue_type="duplicate_title",
                    severity="warning",
                    slide_ids=[slide.slide_id for slide in group],
                    batch_ids=sorted(
                        {
                            _batch_for_slide(long_deck_plan, _slide_number(slide.slide_id)).batch_id
                            for slide in group
                        }
                    ),
                    section_ids=sorted(
                        {
                            _section_for_slide(long_deck_plan, _slide_number(slide.slide_id)).section_id
                            for slide in group
                        }
                    ),
                    message=f"Repeated slide title detected: '{group[0].title}'.",
                    suggestion="Rename repeated slides so each title reflects a distinct judgment.",
                )
            )

    for left, right in pairwise(slides):
        title_is_duplicate = _normalize_text(left.title) == _normalize_text(right.title)
        body_similarity = _text_similarity(_slide_body_text(left), _slide_body_text(right))
        if title_is_duplicate or body_similarity >= NEARBY_TEXT_SIMILARITY_THRESHOLD:
            issues.append(
                _issue(
                    issue_type="adjacent_slide_repetition",
                    severity="warning",
                    slide_ids=[left.slide_id, right.slide_id],
                    batch_ids=sorted(
                        {
                            _batch_for_slide(long_deck_plan, _slide_number(left.slide_id)).batch_id,
                            _batch_for_slide(long_deck_plan, _slide_number(right.slide_id)).batch_id,
                        }
                    ),
                    section_ids=sorted(
                        {
                            _section_for_slide(long_deck_plan, _slide_number(left.slide_id)).section_id,
                            _section_for_slide(long_deck_plan, _slide_number(right.slide_id)).section_id,
                        }
                    ),
                    message="Adjacent slides are too similar in title or body text.",
                    suggestion="Differentiate neighboring slides so each one advances the narrative.",
                )
            )

    for index, left in enumerate(slides):
        for right in slides[index + 1 :]:
            similarity = _text_similarity(_slide_body_text(left), _slide_body_text(right))
            if similarity < TEXT_SIMILARITY_THRESHOLD:
                continue
            left_batch = _batch_for_slide(long_deck_plan, _slide_number(left.slide_id))
            right_batch = _batch_for_slide(long_deck_plan, _slide_number(right.slide_id))
            left_section = _section_for_slide(long_deck_plan, _slide_number(left.slide_id))
            right_section = _section_for_slide(long_deck_plan, _slide_number(right.slide_id))
            if left_section.section_id == right_section.section_id:
                issue_type = "section_repetition"
            elif left_batch.batch_id != right_batch.batch_id:
                issue_type = "cross_batch_repetition"
            else:
                issue_type = "duplicate_slide_text"
            issues.append(
                _issue(
                    issue_type=issue_type,
                    severity="warning",
                    slide_ids=[left.slide_id, right.slide_id],
                    batch_ids=sorted({left_batch.batch_id, right_batch.batch_id}),
                    section_ids=sorted({left_section.section_id, right_section.section_id}),
                    message="Slides repeat highly similar body text across the merged long deck.",
                    suggestion="Rewrite repeated slides so later batches extend the story instead of restating prior text.",
                )
            )
    return issues


def _section_coverage_issues(
    deck_ir: Deck,
    long_deck_plan: LongDeckPlan,
) -> tuple[list[LongDeckQAIssue], list[SectionQAResult]]:
    issues: list[LongDeckQAIssue] = []
    results: list[SectionQAResult] = []

    for section in long_deck_plan.sections:
        section_slides = [
            slide
            for slide in deck_ir.slides
            if section.start_slide <= _slide_number(slide.slide_id) <= section.end_slide
        ]
        if not section_slides:
            issues.append(
                _issue(
                    issue_type="section_missing",
                    severity="critical",
                    slide_ids=[],
                    section_ids=[section.section_id],
                    message=f"Section '{section.section_id}' has no slides in the merged deck.",
                    suggestion="Restore the missing section slides before stitching the long deck.",
                )
            )
            results.append(
                SectionQAResult(
                    section_id=section.section_id,
                    score=0.0,
                    issue_count=1,
                )
            )
            continue

        section_text = "\n".join(_slide_text(slide) for slide in section_slides)
        covered_key_messages = [
            message
            for message in section.key_messages
            if _keyword_coverage(section_text, message) >= SECTION_KEYWORD_COVERAGE_THRESHOLD
        ]
        missing_key_messages = [
            message for message in section.key_messages if message not in covered_key_messages
        ]
        for message in missing_key_messages:
            issues.append(
                _issue(
                    issue_type="section_key_message_uncovered",
                    severity="warning",
                    slide_ids=[slide.slide_id for slide in section_slides],
                    section_ids=[section.section_id],
                    message=f"Section '{section.section_id}' does not cover planned key message '{message}'.",
                    suggestion="Add slide text that makes the section's planned judgment explicit.",
                )
            )

        missing_must_include = [
            phrase
            for phrase in section.must_include
            if not _contains_phrase(section_text, phrase)
        ]
        for phrase in missing_must_include:
            issues.append(
                _issue(
                    issue_type="section_must_include_missing",
                    severity="critical",
                    slide_ids=[slide.slide_id for slide in section_slides],
                    section_ids=[section.section_id],
                    message=f"Section '{section.section_id}' is missing required phrase '{phrase}'.",
                    suggestion="Restore the required content somewhere inside the section slides.",
                )
            )

        violated_must_avoid = [
            phrase
            for phrase in section.must_avoid
            if phrase and _contains_phrase(section_text, phrase)
        ]
        for phrase in violated_must_avoid:
            issues.append(
                _issue(
                    issue_type="section_must_avoid_violation",
                    severity="critical",
                    slide_ids=[slide.slide_id for slide in section_slides],
                    section_ids=[section.section_id],
                    message=f"Section '{section.section_id}' includes forbidden phrase '{phrase}'.",
                    suggestion="Remove or rewrite the forbidden phrase inside the section slides.",
                )
            )

        if section == long_deck_plan.sections[-1]:
            final_slide = section_slides[-1]
            final_text = _slide_text(final_slide)
            if any(marker in _normalize_text(final_text) for marker in CONCLUSION_NEW_TOPIC_WORDS):
                issues.append(
                    _issue(
                        issue_type="conclusion_reopens_new_topic",
                        severity="warning",
                        slide_ids=[final_slide.slide_id],
                        section_ids=[section.section_id],
                        message="The closing section appears to reopen a new topic instead of finishing the deck.",
                        suggestion="Keep the final slide on action or conclusion instead of reintroducing background.",
                    )
                )

        section_issue_count = sum(1 for issue in issues if section.section_id in issue.section_ids)
        section_score = max(0.0, 1.0 - section_issue_count * 0.12)
        results.append(
            SectionQAResult(
                section_id=section.section_id,
                score=section_score,
                issue_count=section_issue_count,
                covered_key_messages=covered_key_messages,
                missing_must_include=missing_must_include,
                violated_must_avoid=violated_must_avoid,
            )
        )

    return issues, results


def _transition_issues(
    deck_ir: Deck,
    long_deck_plan: LongDeckPlan,
) -> tuple[list[LongDeckQAIssue], list[BatchQAResult]]:
    issues: list[LongDeckQAIssue] = []
    batch_scores: list[BatchQAResult] = []
    slide_by_id = {slide.slide_id: slide for slide in deck_ir.slides}

    for batch in long_deck_plan.batches:
        batch_slide_ids = [
            slide.slide_id
            for slide in deck_ir.slides
            if batch.start_slide <= _slide_number(slide.slide_id) <= batch.end_slide
        ]
        batch_issue_count = 0
        transition_ok = True
        repeated_slide_ids: list[str] = []

        if batch != long_deck_plan.batches[-1]:
            next_batch = long_deck_plan.batches[long_deck_plan.batches.index(batch) + 1]
            current_last = slide_by_id[f"slide_{batch.end_slide:03d}"]
            next_first = slide_by_id[f"slide_{next_batch.start_slide:03d}"]
            title_similarity = _text_similarity(current_last.title, next_first.title)
            text_similarity = _text_similarity(_slide_text(current_last), _slide_text(next_first))

            if title_similarity >= TITLE_SIMILARITY_THRESHOLD or text_similarity >= NEARBY_TEXT_SIMILARITY_THRESHOLD:
                transition_ok = False
                repeated_slide_ids = [current_last.slide_id, next_first.slide_id]
                issues.append(
                    _issue(
                        issue_type="batch_transition_repetition",
                        severity="warning",
                        slide_ids=repeated_slide_ids,
                        batch_ids=[batch.batch_id, next_batch.batch_id],
                        section_ids=sorted(
                            {
                                _section_for_slide(long_deck_plan, batch.end_slide).section_id,
                                _section_for_slide(long_deck_plan, next_batch.start_slide).section_id,
                            }
                        ),
                        message="Batch boundary repeats the previous ending instead of transitioning forward.",
                        suggestion="Rewrite the next batch opening so it extends the previous batch instead of echoing it.",
                    )
                )
                batch_issue_count += 1

            opening_like = any(word in _normalize_text(_slide_text(next_first)) for word in OPENING_WORDS)
            if opening_like:
                transition_ok = False
                issues.append(
                    _issue(
                        issue_type="batch_transition_reopens_opening",
                        severity="warning",
                        slide_ids=[next_first.slide_id],
                        batch_ids=[batch.batch_id, next_batch.batch_id],
                        section_ids=[_section_for_slide(long_deck_plan, next_batch.start_slide).section_id],
                        message="The next batch opens like a fresh introduction instead of continuing the long deck.",
                        suggestion="Replace opening-language with a transition that builds on the previous batch.",
                    )
                )
                batch_issue_count += 1

            has_transition_word = any(word in _normalize_text(_slide_text(next_first)) for word in TRANSITION_WORDS)
            if not has_transition_word:
                issues.append(
                    _issue(
                        issue_type="batch_transition_weak_bridge",
                        severity="warning",
                        slide_ids=[current_last.slide_id, next_first.slide_id],
                        batch_ids=[batch.batch_id, next_batch.batch_id],
                        section_ids=sorted(
                            {
                                _section_for_slide(long_deck_plan, batch.end_slide).section_id,
                                _section_for_slide(long_deck_plan, next_batch.start_slide).section_id,
                            }
                        ),
                        message="The batch boundary lacks an explicit bridge into the next section.",
                        suggestion="Add a transition phrase or framing sentence at the start of the next batch.",
                    )
                )
                batch_issue_count += 1

        batch_scores.append(
            BatchQAResult(
                batch_id=batch.batch_id,
                score=max(0.0, 1.0 - batch_issue_count * 0.15),
                issue_count=batch_issue_count,
                transition_ok=transition_ok,
                repeated_slide_ids=repeated_slide_ids,
            )
        )

    return issues, batch_scores


def evaluate_long_deck_consistency(
    deck_ir: Deck,
    long_deck_plan: LongDeckPlan,
) -> LongDeckQAReport:
    validate_merged_long_deck_ir(deck_ir, long_deck_plan)

    repetition_issues = _repetition_issues(deck_ir, long_deck_plan)
    coverage_issues, section_scores = _section_coverage_issues(deck_ir, long_deck_plan)
    transition_issues, batch_scores = _transition_issues(deck_ir, long_deck_plan)

    issues = [*repetition_issues, *coverage_issues, *transition_issues]
    score = _score_issues(issues)
    has_critical_issue = any(issue.severity == "critical" for issue in issues)
    passed = not has_critical_issue and score >= LONG_DECK_PASS_THRESHOLD

    recommendation_counter = Counter(issue.suggestion for issue in issues)
    recommendations = [
        suggestion
        for suggestion, _count in recommendation_counter.most_common()
    ]

    return LongDeckQAReport(
        passed=passed,
        score=score,
        issues=issues,
        repetition_issues=repetition_issues,
        coverage_issues=coverage_issues,
        transition_issues=transition_issues,
        section_scores=section_scores,
        batch_scores=batch_scores,
        recommendations=recommendations,
    )
