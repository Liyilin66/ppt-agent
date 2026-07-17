"""Tests for hierarchical planning and page-budget reconciliation."""

from __future__ import annotations

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
    parse_page_briefs,
    reconcile_outline,
    reconcile_page_briefs,
    section_start_pages,
    skeleton_from_editable_plan,
)


def _outline(page_counts: list[int]) -> DeckOutline:
    return DeckOutline(
        deck_title="Deck",
        sections=[
            SectionOutline(title=f"S{index}", content_pages=count)
            for index, count in enumerate(page_counts, start=1)
        ],
    )


class TestReconcileOutline:
    def test_exact_budget_for_100_pages(self) -> None:
        outline = _outline([10, 20, 30, 15, 25])
        fitted, toc_pages = reconcile_outline(outline, 100)
        overhead = 1 + toc_pages + len(fitted.sections) + 1
        assert sum(s.content_pages for s in fitted.sections) + overhead == 100
        assert all(s.content_pages >= 1 for s in fitted.sections)

    def test_merges_sections_when_budget_is_tiny(self) -> None:
        outline = _outline([3, 3, 3, 3, 3, 3])
        fitted, toc_pages = reconcile_outline(outline, 8)
        overhead = 1 + toc_pages + len(fitted.sections) + 1
        assert sum(s.content_pages for s in fitted.sections) + overhead == 8
        assert len(fitted.sections) < 6

    def test_rejects_out_of_range_page_count(self) -> None:
        with pytest.raises(ValueError):
            reconcile_outline(_outline([5]), 300)


class TestSkeleton:
    def test_skeleton_layout_for_100_pages(self) -> None:
        skeleton = build_skeleton(
            _outline([10, 20, 30, 15, 25]), total_pages=100, language="zh-CN"
        )
        assert skeleton.total_pages == 100
        assert len(skeleton.slots) == 100
        assert skeleton.slots[0].kind == "cover"
        assert skeleton.slots[1].kind == "toc"
        assert skeleton.slots[-1].kind == "closing"
        page_numbers = [slot.page_number for slot in skeleton.slots]
        assert page_numbers == list(range(1, 101))
        dividers = [slot for slot in skeleton.slots if slot.kind == "section_divider"]
        assert len(dividers) == len(skeleton.outline.sections)

    def test_small_deck_has_no_toc(self) -> None:
        skeleton = build_skeleton(_outline([2, 2]), total_pages=8, language="zh-CN")
        assert all(slot.kind != "toc" for slot in skeleton.slots)

    def test_section_start_pages_match_dividers(self) -> None:
        skeleton = build_skeleton(_outline([5, 5, 5]), total_pages=30, language="zh-CN")
        starts = section_start_pages(skeleton)
        assert len(starts) == 3
        for (_, page_number), slot in zip(
            starts, (s for s in skeleton.slots if s.kind == "section_divider")
        ):
            assert page_number == slot.page_number


class TestPageBriefs:
    def test_reconcile_pads_and_trims(self) -> None:
        briefs = [PageBrief(title="a"), PageBrief(title="b")]
        assert len(reconcile_page_briefs(briefs, 4, section_title="S")) == 4
        assert len(reconcile_page_briefs(briefs * 5, 3, section_title="S")) == 3

    def test_parse_accepts_wrapped_and_bare_lists(self) -> None:
        bare = [{"title": "t1"}, {"title": "t2"}]
        assert len(parse_page_briefs(bare)) == 2
        assert len(parse_page_briefs({"pages": bare})) == 2
        with pytest.raises(ValueError):
            parse_page_briefs({"nope": 1})


class TestEditableDeckPlan:
    def _plan(self) -> "EditableDeckPlan":
        return EditableDeckPlan(
            deck_title="演示",
            sections=[
                EditableSection(
                    title="第一章",
                    goal="讲清现状",
                    pages=[
                        EditablePage(title="p1", points=["a", " ", "b"], speaker_notes="口播一"),
                        EditablePage(title="p2", layout_hint="not_a_hint"),
                    ],
                ),
                EditableSection(title="第二章", pages=[EditablePage(title="p3")]),
            ],
        )

    def test_skeleton_roundtrip_preserves_briefs(self) -> None:
        skeleton = skeleton_from_editable_plan(self._plan())
        assert skeleton.total_pages == 7  # cover + 2 dividers + 3 content + closing = 7 (<10, no TOC)
        content = skeleton.content_slots()
        assert [slot.brief.title for slot in content] == ["p1", "p2", "p3"]
        assert content[0].brief.points == ["a", "b"]
        assert content[0].brief.speaker_notes == "口播一"
        assert content[1].brief.layout_hint == "auto"  # invalid hint falls back
        back = editable_plan_from_skeleton(skeleton)
        assert [len(section.pages) for section in back.sections] == [2, 1]
        assert back.sections[0].pages[0].speaker_notes == "口播一"

    def test_total_pages_adds_toc_for_long_decks(self) -> None:
        plan = EditableDeckPlan(
            deck_title="长演示",
            sections=[
                EditableSection(
                    title="章",
                    pages=[EditablePage(title=f"p{i}") for i in range(8)],
                )
            ],
        )
        # cover + toc + divider + 8 content + closing = 12
        assert plan.total_pages() == 12
        assert skeleton_from_editable_plan(plan).total_pages == 12

    def test_rejects_out_of_range_totals(self) -> None:
        oversized = EditableDeckPlan(
            deck_title="太长",
            sections=[
                EditableSection(
                    title="章",
                    pages=[EditablePage(title=f"p{i}") for i in range(40)],
                ),
                EditableSection(
                    title="章2",
                    pages=[EditablePage(title=f"q{i}") for i in range(40)],
                ),
                EditableSection(
                    title="章3",
                    pages=[EditablePage(title=f"r{i}") for i in range(20)],
                ),
            ],
        )
        with pytest.raises(ValueError, match="4-100"):
            skeleton_from_editable_plan(oversized)
