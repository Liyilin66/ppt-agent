"""Regression checks for content-first deck structure and editable round trips."""

import pytest

from ppt_agent.v2.planning import (
    DeckOutline,
    EditableDeckPlan,
    EditablePage,
    EditableSection,
    PageBrief,
    SectionOutline,
    build_skeleton,
    editable_plan_from_skeleton,
    section_start_pages,
    skeleton_from_editable_plan,
)


@pytest.mark.parametrize(
    "total,sections,structural,dividers", [(10, 6, 3, 0), (20, 6, 3, 0), (30, 6, 3, 0), (100, 8, 11, 8)]
)
def test_structure_budget_preserves_content_and_section_targets(total, sections, structural, dividers):
    outline = DeckOutline(
        deck_title="培训汇报",
        sections=[SectionOutline(title=f"章节 {i}", content_pages=3) for i in range(sections)],
    )
    skeleton = build_skeleton(outline, total_pages=total, language="zh-CN")

    assert [slot.page_number for slot in skeleton.slots] == list(range(1, total + 1))
    assert len(skeleton.content_slots()) == total - structural
    assert sum(slot.kind == "section_divider" for slot in skeleton.slots) == dividers
    assert sum(slot.kind != "content" for slot in skeleton.slots) == structural
    assert structural / total <= 0.25 or total < 12  # Three fixed pages cannot fit 10 pages at 25%.
    assert [slot.kind for slot in skeleton.slots[:2]] == ["cover", "toc"]
    assert skeleton.slots[-1].kind == "closing"
    assert len(skeleton.outline.sections) == sections

    expected_starts = []
    for index, section in enumerate(skeleton.outline.sections, 1):
        members = [slot for slot in skeleton.slots if slot.section_index == index]
        content = [slot for slot in members if slot.kind == "content"]
        assert content
        assert all(slot.section_title == section.title for slot in content)
        expected_starts.append((section.title, members[0].page_number))
    assert section_start_pages(skeleton) == expected_starts

    for slot in skeleton.content_slots():
        slot.brief = PageBrief(title=f"页面 {slot.page_number}", points=["真实内容"])
    editable = editable_plan_from_skeleton(skeleton)
    assert editable.total_pages() == total
    rebuilt = skeleton_from_editable_plan(editable)
    assert [slot.kind for slot in rebuilt.slots] == [slot.kind for slot in skeleton.slots]
    assert [slot.brief for slot in rebuilt.content_slots()] == [slot.brief for slot in skeleton.content_slots()]


def test_tiny_deck_omits_dividers_before_merging_sections():
    outline = DeckOutline(
        deck_title="小型汇报",
        sections=[SectionOutline(title=f"章节 {i}", content_pages=2) for i in range(8)],
    )
    skeleton = build_skeleton(outline, total_pages=8, language="zh-CN")
    assert len(skeleton.outline.sections) == 5
    assert len(skeleton.content_slots()) == 5
    assert not any(slot.kind == "section_divider" for slot in skeleton.slots)
    assert all(section.content_pages >= 1 for section in skeleton.outline.sections)


@pytest.mark.parametrize("total", [12, 20])
def test_many_sections_keep_toc_capacity_and_fixed_structure_budget(total):
    outline = DeckOutline(
        deck_title="完整目录",
        sections=[SectionOutline(title=f"章节 {i}", content_pages=1) for i in range(16)],
    )
    skeleton = build_skeleton(outline, total_pages=total, language="zh-CN")
    structural = sum(slot.kind != "content" for slot in skeleton.slots)
    assert structural * 4 <= total
    assert len(skeleton.slots) == total
    assert len(skeleton.outline.sections) <= 8 * sum(slot.kind == "toc" for slot in skeleton.slots)
    assert len(section_start_pages(skeleton)) == len(skeleton.outline.sections)
    assert all(section.content_pages >= 1 for section in skeleton.outline.sections)


@pytest.mark.parametrize("total", range(4, 13))
def test_fixed_cover_toc_closing_are_preserved_in_short_decks(total):
    outline = DeckOutline(deck_title="短稿", sections=[SectionOutline(title="正文", content_pages=2)])
    skeleton = build_skeleton(outline, total_pages=total, language="zh-CN")
    assert [slot.kind for slot in skeleton.slots[:2]] == ["cover", "toc"]
    assert skeleton.slots[-1].kind == "closing"
    assert len(skeleton.slots) == total
    assert len(skeleton.content_slots()) == total - 3


def test_explicit_divider_preference_cannot_exceed_allowance():
    outline = DeckOutline(
        deck_title="预算优先",
        sections=[SectionOutline(title=f"章节 {i}", content_pages=2) for i in range(6)],
    )
    skeleton = build_skeleton(
        outline, total_pages=20, language="zh-CN", include_section_dividers=True
    )
    assert sum(slot.kind != "content" for slot in skeleton.slots) == 3


def test_edited_plan_rejects_implicit_section_merging_without_losing_content():
    plan = EditableDeckPlan(
        deck_title="保留手工正文",
        sections=[
            EditableSection(title=f"章节 {i}", pages=[EditablePage(title=f"原始正文 {i}")])
            for i in range(9)
        ],
    )
    with pytest.raises(ValueError, match="merge sections or add content pages"):
        skeleton_from_editable_plan(plan)
    assert [section.pages[0].title for section in plan.sections] == [f"原始正文 {i}" for i in range(9)]
