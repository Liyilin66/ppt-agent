"""Hard quality gate helpers for long-deck merged Deck IR artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ppt_agent.models import Deck, StrictModel
from ppt_agent.qa import QAReport, QAIssue, analyze_deck, hard_quality_gate_issues


LongDeckQualityStatus = Literal["passed", "failed_quality_gate"]


class LongDeckQualityGateReport(StrictModel):
    status: LongDeckQualityStatus
    score: int = Field(..., ge=0, le=100)
    issues: list[QAIssue] = Field(default_factory=list)
    blocked_codes: list[str] = Field(default_factory=list)
    blocked_slide_ids: list[str] = Field(default_factory=list)
    blocked_element_ids: list[str] = Field(default_factory=list)
    message: str = Field(..., min_length=1)


def evaluate_long_deck_quality_gate(deck: Deck) -> LongDeckQualityGateReport:
    qa_report: QAReport = analyze_deck(deck)
    blocking_issues = hard_quality_gate_issues(qa_report)
    if not blocking_issues:
        return LongDeckQualityGateReport(
            status="passed",
            score=100,
            issues=[],
            blocked_codes=[],
            blocked_slide_ids=[],
            blocked_element_ids=[],
            message="Long-deck hard quality gate passed.",
        )

    blocked_codes = sorted({issue.code for issue in blocking_issues})
    blocked_slide_ids = sorted({issue.slide_id for issue in blocking_issues})
    blocked_element_ids = sorted(
        {
            issue.element_id
            for issue in blocking_issues
            if issue.element_id
        }
    )
    gate_score = max(0, 100 - 35 * len(blocking_issues))
    return LongDeckQualityGateReport(
        status="failed_quality_gate",
        score=gate_score,
        issues=blocking_issues,
        blocked_codes=blocked_codes,
        blocked_slide_ids=blocked_slide_ids,
        blocked_element_ids=blocked_element_ids,
        message=(
            "Long-deck hard quality gate failed because audience-visible instruction leakage "
            "or matrix placeholder content was detected."
        ),
    )
