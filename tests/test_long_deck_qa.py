from __future__ import annotations

import copy

import pytest

from ppt_agent.generation import BatchGenerationArtifact
from ppt_agent.long_deck import merge_batch_deck_irs
from ppt_agent.long_deck_qa import evaluate_long_deck_consistency
from ppt_agent.models import Deck
from ppt_agent.planning import LongDeckPlanningRequest, build_deterministic_long_deck_plan


def _long_request() -> LongDeckPlanningRequest:
    return LongDeckPlanningRequest(
        topic="AI Agent 产品经理",
        audience="IT 硕士学生",
        slide_count=30,
        language="zh-CN",
        purpose="技术产品分享",
        content_focus="责任边界、工作流、指标和风险治理",
        must_include=["closing section must stay actionable"],
        must_avoid=["marketing slogans"],
        user_requirements_raw="做一份 30 页长 deck，按 batch 生成后再 stitch。",
    )


def _build_long_deck(long_plan) -> Deck:
    slides: list[dict] = []
    for section in long_plan.sections:
        section_slides = list(range(section.start_slide, section.end_slide + 1))
        for local_index, slide_number in enumerate(section_slides):
            batch = next(
                batch
                for batch in long_plan.batches
                if batch.start_slide <= slide_number <= batch.end_slide
            )
            layout = section.preferred_layouts[min(local_index, len(section.preferred_layouts) - 1)]
            if slide_number == 1:
                layout = "title_slide"
            elif slide_number == long_plan.slide_count:
                layout = "closing_slide"

            title_prefix = ""
            body_prefix = ""
            if slide_number == batch.start_slide and slide_number != 1:
                title_prefix = "接下来："
                body_prefix = "接下来承接上一批的判断。 "

            text_segments = []
            if local_index < len(section.key_messages):
                text_segments.append(section.key_messages[local_index])
            else:
                question_index = (local_index - len(section.key_messages)) % max(len(section.key_questions), 1)
                text_segments.append(section.key_questions[question_index])
            if local_index == 0 and section.must_include:
                text_segments.extend(section.must_include)
            if slide_number == long_plan.slide_count:
                text_segments.append("下一步：列出禁止清单，设计失败回退路径，并标注人工接管点。")
            text_segments.append(f"Slide {slide_number} keeps the section on {section.title}.")

            slides.append(
                {
                    "slide_id": f"slide_{slide_number:03d}",
                    "title": f"{title_prefix}{section.title} {local_index + 1}",
                    "layout": layout,
                    "elements": [
                        {
                            "element_id": f"s{slide_number:03d}_e01",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.8, "width": 8.0, "height": 1.0},
                            "text": body_prefix + " ".join(text_segments),
                        }
                    ],
                }
            )

    return Deck.model_validate(
        {
            "deck_id": "generated_long_deck",
            "title": long_plan.topic,
            "theme_name": "clean_business",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": slides,
        }
    )


def _batch_artifacts(long_plan) -> list[BatchGenerationArtifact]:
    full_deck = _build_long_deck(long_plan)
    artifacts: list[BatchGenerationArtifact] = []
    for batch in long_plan.batches:
        batch_slides = [
            slide
            for slide in full_deck.slides
            if batch.start_slide <= int(slide.slide_id.split("_")[-1]) <= batch.end_slide
        ]
        batch_deck = Deck.model_validate(
            {
                "deck_id": full_deck.deck_id,
                "title": full_deck.title,
                "theme_name": full_deck.theme_name,
                "canvas_width_in": full_deck.canvas_width_in,
                "canvas_height_in": full_deck.canvas_height_in,
                "slides": [slide.model_dump(mode="json") for slide in batch_slides],
            }
        )
        artifacts.append(BatchGenerationArtifact(batch_id=batch.batch_id, deck_ir=batch_deck))
    return artifacts


def _merged_long_deck(long_plan) -> Deck:
    return merge_batch_deck_irs(long_plan, _batch_artifacts(long_plan))


def test_evaluate_long_deck_consistency_accepts_valid_merged_deck() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)

    report = evaluate_long_deck_consistency(merged_deck, long_plan)

    assert report.passed is True
    assert report.score >= 0.75
    assert report.issues == []


def test_evaluate_long_deck_consistency_detects_duplicate_slide_title() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[6].title = corrupted.slides[5].title

    report = evaluate_long_deck_consistency(corrupted, long_plan)

    assert any(issue.issue_type == "duplicate_title" for issue in report.repetition_issues)
    assert report.score < 1.0


def test_evaluate_long_deck_consistency_detects_duplicate_slide_text() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[7].elements[0].text = corrupted.slides[3].elements[0].text

    report = evaluate_long_deck_consistency(corrupted, long_plan)

    assert any(issue.issue_type in {"section_repetition", "cross_batch_repetition", "duplicate_slide_text"} for issue in report.repetition_issues)


def test_evaluate_long_deck_consistency_detects_cross_batch_repetition() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[20].elements[0].text = corrupted.slides[8].elements[0].text

    report = evaluate_long_deck_consistency(corrupted, long_plan)

    assert any(issue.issue_type == "cross_batch_repetition" for issue in report.repetition_issues)


def test_evaluate_long_deck_consistency_detects_transition_repetition() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[10].title = corrupted.slides[9].title
    corrupted.slides[10].elements[0].text = corrupted.slides[9].elements[0].text

    report = evaluate_long_deck_consistency(corrupted, long_plan)

    assert any(issue.issue_type == "batch_transition_repetition" for issue in report.transition_issues)


def test_evaluate_long_deck_consistency_detects_missing_section_must_include() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[20].elements[0].text = corrupted.slides[20].elements[0].text.replace("说明指标如何衡量", "")

    report = evaluate_long_deck_consistency(corrupted, long_plan)

    assert any(issue.issue_type == "section_must_include_missing" for issue in report.coverage_issues)
    assert report.passed is False


def test_evaluate_long_deck_consistency_detects_section_must_avoid_violation() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[24].elements[0].text += " marketing slogans"

    report = evaluate_long_deck_consistency(corrupted, long_plan)

    assert any(issue.issue_type == "section_must_avoid_violation" for issue in report.coverage_issues)
    assert report.passed is False


def test_evaluate_long_deck_consistency_rejects_invalid_merged_ir_first() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[10].slide_id = "slide_010"

    with pytest.raises(ValueError, match="duplicate slide_id"):
        evaluate_long_deck_consistency(corrupted, long_plan)


def test_evaluate_long_deck_consistency_score_drops_when_issues_increase() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(), batch_size=10)
    merged_deck = _merged_long_deck(long_plan)
    clean_report = evaluate_long_deck_consistency(merged_deck, long_plan)

    corrupted = merged_deck.model_copy(deep=True)
    corrupted.slides[6].title = corrupted.slides[5].title
    corrupted.slides[20].elements[0].text = corrupted.slides[8].elements[0].text
    issue_report = evaluate_long_deck_consistency(corrupted, long_plan)

    assert issue_report.score < clean_report.score
