from __future__ import annotations

import pytest

from ppt_agent.generation import BatchGenerationArtifact
from ppt_agent.long_deck import merge_batch_deck_irs, validate_merged_long_deck_ir
from ppt_agent.models import Deck
from ppt_agent.planning import LongDeckPlanningRequest, build_deterministic_long_deck_plan


def _long_request(slide_count: int = 30) -> LongDeckPlanningRequest:
    return LongDeckPlanningRequest(
        topic="AI Agent 产品经理",
        audience="IT 硕士学生",
        slide_count=slide_count,
        language="zh-CN",
        purpose="技术产品分享",
        content_focus="责任边界、工作流、指标、风险",
        must_include=["保持 batch 之间的绝对页码"],
        must_avoid=["营销口号"],
        user_requirements_raw="做一份长 deck，先分 batch 生成，再 deterministic merge。",
    )


def _batch_artifact(long_plan, batch_id: str) -> BatchGenerationArtifact:
    batch = next(batch for batch in long_plan.batches if batch.batch_id == batch_id)
    slides = []
    for slide_number in range(batch.start_slide, batch.end_slide + 1):
        layout = "two_column"
        if slide_number == 1:
            layout = "title_slide"
        elif slide_number == long_plan.slide_count:
            layout = "closing_slide"
        slides.append(
            {
                "slide_id": f"slide_{slide_number:03d}",
                "title": f"Slide {slide_number}",
                "layout": layout,
                "elements": [
                    {
                        "element_id": f"s{slide_number:03d}_e01",
                        "type": "text",
                        "bbox": {"x": 0.8, "y": 0.8, "width": 7.0, "height": 0.8},
                        "text": f"Point for slide {slide_number}",
                    }
                ],
            }
        )

    deck = Deck.model_validate(
        {
            "deck_id": "generated_long_deck",
            "title": long_plan.topic,
            "theme_name": "clean_business",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": slides,
        }
    )
    return BatchGenerationArtifact(batch_id=batch_id, deck_ir=deck)


def _merged_deck(long_plan) -> Deck:
    return merge_batch_deck_irs(
        long_plan,
        [_batch_artifact(long_plan, batch.batch_id) for batch in long_plan.batches],
    )


def test_merge_batch_deck_irs_sorts_batches_by_long_deck_plan_order() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)
    batch_one = _batch_artifact(long_plan, "batch_01")
    batch_two = _batch_artifact(long_plan, "batch_02")

    merged = merge_batch_deck_irs(long_plan, [batch_two, batch_one])

    assert len(merged.slides) == 30
    assert merged.slides[0].slide_id == "slide_001"
    assert merged.slides[14].slide_id == "slide_015"
    assert merged.slides[15].slide_id == "slide_016"
    assert merged.slides[-1].slide_id == "slide_030"


def test_merge_batch_deck_irs_rejects_missing_batch() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)

    with pytest.raises(ValueError, match="Missing batch artifacts"):
        merge_batch_deck_irs(long_plan, [_batch_artifact(long_plan, "batch_01")])


def test_merge_batch_deck_irs_rejects_duplicate_batch_id() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)
    batch_one = _batch_artifact(long_plan, "batch_01")

    with pytest.raises(ValueError, match="Duplicate batch_id"):
        merge_batch_deck_irs(long_plan, [batch_one, batch_one])


def test_merge_batch_deck_irs_rejects_unknown_batch_id() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)
    batch_one = _batch_artifact(long_plan, "batch_01")
    batch_two = _batch_artifact(long_plan, "batch_02").model_copy(update={"batch_id": "batch_99"})

    with pytest.raises(ValueError, match="Unknown batch_id"):
        merge_batch_deck_irs(long_plan, [batch_one, batch_two])


def test_merge_batch_deck_irs_rejects_invalid_batch_slide_range() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)
    batch_one = _batch_artifact(long_plan, "batch_01")
    batch_two = _batch_artifact(long_plan, "batch_02")
    corrupted_slides = [slide.model_copy(deep=True) for slide in batch_two.deck_ir.slides]
    corrupted_slides[0].slide_id = "slide_010"
    corrupted_batch_two = batch_two.model_copy(
        update={"deck_ir": batch_two.deck_ir.model_copy(update={"slides": corrupted_slides})}
    )

    with pytest.raises(ValueError, match="absolute batch range"):
        merge_batch_deck_irs(long_plan, [batch_one, corrupted_batch_two])


def test_validate_merged_long_deck_ir_rejects_wrong_total_slide_count() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)
    merged = _merged_deck(long_plan)
    corrupted = merged.model_copy(update={"slides": merged.slides[:-1]})

    with pytest.raises(ValueError, match="has 29 slides"):
        validate_merged_long_deck_ir(corrupted, long_plan)


def test_validate_merged_long_deck_ir_rejects_duplicate_slide_ids() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)
    merged = _merged_deck(long_plan)
    corrupted_slides = [slide.model_copy(deep=True) for slide in merged.slides]
    corrupted_slides[1].slide_id = "slide_001"
    corrupted = merged.model_copy(update={"slides": corrupted_slides})

    with pytest.raises(ValueError, match="duplicate slide_id"):
        validate_merged_long_deck_ir(corrupted, long_plan)


def test_validate_merged_long_deck_ir_rejects_missing_slide_gap() -> None:
    long_plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=15)
    merged = _merged_deck(long_plan)
    corrupted_slides = [slide.model_copy(deep=True) for slide in merged.slides]
    corrupted_slides[15].slide_id = "slide_031"
    corrupted = merged.model_copy(update={"slides": corrupted_slides})

    with pytest.raises(ValueError, match="slide_016"):
        validate_merged_long_deck_ir(corrupted, long_plan)
