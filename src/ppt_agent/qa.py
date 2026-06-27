"""Rule-based quality checks for validated Slide IR decks."""

from __future__ import annotations

from itertools import combinations
from typing import Literal

from pydantic import Field

from ppt_agent.models import BBox, Deck, StrictModel, TextElement
from ppt_agent.theme import Theme


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
    penalty_by_severity = {
        "info": 1,
        "warning": 8,
        "error": 25,
    }
    penalty = sum(penalty_by_severity[issue.severity] for issue in issues)
    return max(0, 100 - penalty)


def analyze_deck(deck: Deck, theme: Theme | None = None) -> QAReport:
    """Analyze a validated deck with deterministic QA rules."""

    issues: list[QAIssue] = []
    slide_area = deck.canvas_width_in * deck.canvas_height_in

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
