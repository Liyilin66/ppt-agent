from __future__ import annotations

from types import SimpleNamespace

import pytest

from ppt_agent.generation import DeckBrief
from ppt_agent.planning import (
    LongDeckPlanningRequest,
    build_deterministic_deck_plan,
    build_deterministic_long_deck_plan,
    get_batch_context,
    split_long_deck_into_batches,
)


def _long_request(slide_count: int) -> LongDeckPlanningRequest:
    return LongDeckPlanningRequest(
        topic="AI Agent 产品经理",
        audience="IT 硕士学生",
        slide_count=slide_count,
        language="zh-CN",
        purpose="技术产品分享",
        content_focus="责任边界、工作流、指标、风险",
        must_include=["强调可编辑 PPTX 只是后续产物，不是本阶段重点"],
        must_avoid=["营销口号"],
        user_requirements_raw="做一份长篇技术分享，覆盖责任边界、技术边界、工作流、指标和风险治理。",
    )


def _assert_continuous_ranges(items, slide_count: int) -> None:
    expected_start = 1
    for item in items:
        assert item.start_slide == expected_start
        assert item.end_slide >= item.start_slide
        expected_start = item.end_slide + 1
    assert expected_start == slide_count + 1


def test_long_deck_plan_builds_three_batches_for_30_slides() -> None:
    plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=10)

    assert [(batch.start_slide, batch.end_slide) for batch in plan.batches] == [
        (1, 10),
        (11, 20),
        (21, 30),
    ]
    assert len(split_long_deck_into_batches(plan)) == 3


def test_long_deck_plan_builds_six_batches_for_30_slides_with_batch_size_5() -> None:
    plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=5)

    assert [(batch.start_slide, batch.end_slide) for batch in plan.batches] == [
        (1, 5),
        (6, 10),
        (11, 15),
        (16, 20),
        (21, 25),
        (26, 30),
    ]
    assert len(split_long_deck_into_batches(plan)) == 6


def test_long_deck_plan_builds_fifteen_batches_for_30_slides_with_batch_size_2() -> None:
    plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=2)

    assert len(plan.batches) == 15
    assert (plan.batches[0].start_slide, plan.batches[0].end_slide) == (1, 2)
    assert (plan.batches[-1].start_slide, plan.batches[-1].end_slide) == (29, 30)
    assert len(split_long_deck_into_batches(plan)) == 15


def test_long_deck_plan_builds_ten_batches_for_100_slides() -> None:
    plan = build_deterministic_long_deck_plan(_long_request(100), batch_size=10)

    assert len(plan.batches) == 10
    assert (plan.batches[0].start_slide, plan.batches[0].end_slide) == (1, 10)
    assert (plan.batches[-1].start_slide, plan.batches[-1].end_slide) == (91, 100)


def test_long_deck_plan_ranges_are_continuous_without_overlap_or_gaps() -> None:
    plan = build_deterministic_long_deck_plan(_long_request(50), batch_size=10)

    _assert_continuous_ranges(plan.sections, 50)
    _assert_continuous_ranges(plan.batches, 50)


def test_long_deck_plan_conclusion_section_stays_last() -> None:
    plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=10)

    assert plan.sections[-1].section_id.endswith("conclusion_action")
    assert plan.sections[-1].title == "Conclusion and Action"
    assert all("Context" not in section.title for section in plan.sections[-1:])
    assert all("Context" not in section.title for section in plan.sections[1:-1] if section.section_id.endswith("conclusion_action"))


def test_long_deck_plan_uses_existing_deck_plan_to_seed_section_messages() -> None:
    short_brief = DeckBrief(
        topic="AI Agent 产品经理",
        audience="IT 硕士学生",
        slide_count=8,
        user_requirements_raw="讲技术边界、工作流、指标和风险。",
    )
    deck_plan = build_deterministic_deck_plan(short_brief)

    plan = build_deterministic_long_deck_plan(_long_request(30), deck_plan=deck_plan, batch_size=10)

    flattened_messages = [message for section in plan.sections for message in section.key_messages]
    assert any(message in flattened_messages for message in [slide.key_message for slide in deck_plan.slides[1:-1]])


def test_get_batch_context_includes_adjacent_section_summaries() -> None:
    plan = build_deterministic_long_deck_plan(_long_request(30), batch_size=10)

    context = get_batch_context(plan, "batch_02")

    assert context.start_slide == 11
    assert context.end_slide == 20
    assert context.section_ids
    assert context.sections
    assert context.previous_section_summary is not None
    assert context.next_section_summary is not None


def test_long_deck_plan_rejects_slide_count_at_or_below_20() -> None:
    short_like_request = SimpleNamespace(
        topic="AI Agent",
        audience="PMs",
        slide_count=20,
        language="zh-CN",
        must_include=[],
        must_avoid=[],
        purpose="",
        tone="",
        visual_style="",
        content_focus="",
        user_requirements_raw="",
    )

    with pytest.raises(ValueError, match="slide_count > 20"):
        build_deterministic_long_deck_plan(short_like_request)
