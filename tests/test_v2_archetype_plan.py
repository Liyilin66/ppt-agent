"""Up-front archetype planning and concurrent content generation."""

import asyncio
import json
from pathlib import Path

import pytest

from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest, build_deck
from ppt_agent.v2.planning import PageBrief, PageSlot
from ppt_agent.v2.typeset_pipeline import has_numeric_evidence, plan_archetypes


def _slots(hints, sections=None):
    sections = sections or [1] * len(hints)
    return [PageSlot(page_number=i + 3, kind="content", section_index=s,
                     brief=PageBrief(title=f"页{i}", layout_hint=h))
            for i, (h, s) in enumerate(zip(hints, sections))]


@pytest.mark.parametrize("count", [5, 10, 17, 40, 89])
def test_plan_respects_mix_rules(count):
    hints = ["two_column", "comparison", "cards", "timeline", "stats", "chart", "quote", "list"]
    sections = [1 + i // max(1, count // 6) for i in range(count)]
    slots = _slots([hints[i % len(hints)] for i in range(count)], sections)
    numeric = {slot.page_number: slot.page_number % 3 == 0 for slot in slots}
    plan = plan_archetypes(slots, numeric)
    kinds = [plan[s.page_number] for s in slots]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    caps = {"points": 0.4, "process": 0.3, "statement": 0.15}
    for kind, share in caps.items():
        assert kinds.count(kind) <= max(1, int(count * share)) or kind == "points"
    for slot, kind in zip(slots, kinds):
        if kind in ("metrics", "chart"):
            assert numeric[slot.page_number], "metrics/chart need numbers in evidence"
        if kind == "timeline":
            assert slot.brief.layout_hint == "timeline"
    for index, (slot, kind) in enumerate(zip(slots, kinds)):
        first = index == 0 or slots[index - 1].section_index != slot.section_index
        assert not (first and kind == "statement")
    for section in set(sections):
        assert any(k != "points" for s, k in zip(slots, kinds) if s.section_index == section)


def test_numeric_gate_needs_several_quantified_facts():
    assert has_numeric_evidence("用户规模 5.15 亿，普及率 36.5%，半年提升 18.8 个百分点")
    # Counting words in prose ("3 项", "5 个") are not statistics.
    assert not has_numeric_evidence("产品经理需要 3 项能力、5 个步骤和 2 类角色，2025 年趋势明显")
    assert not has_numeric_evidence(None)


def test_timeline_needs_real_dates_in_evidence():
    from ppt_agent.v2.typeset_pipeline import has_dated_evidence
    slots = _slots(["two_column", "timeline", "cards", "timeline"])
    dated = {slots[1].page_number: False, slots[3].page_number: True}
    plan = plan_archetypes(slots, {}, dated)
    assert plan[slots[1].page_number] != "timeline"
    assert plan[slots[3].page_number] == "timeline"
    assert has_dated_evidence("2024年12月 Sora 开放；2025.01 DeepSeek-R1 发布")
    assert not has_dated_evidence("第一阶段、第二阶段、第三阶段")


def test_overlong_lists_are_trimmed_not_discarded():
    from ppt_agent.v2.typeset_pipeline import trim_overlong_lists
    payload = {"archetype": "points", "title": "t",
               "items": [{"heading": f"h{i}", "body": "b"} for i in range(6)]}
    trimmed, notes = trim_overlong_lists(payload)
    assert len(trimmed["items"]) == 4 and notes == [{"field": "items", "from": 6, "to": 4}]
    assert trim_overlong_lists({"archetype": "statement", "title": "t", "statement": "s"})[1] == []


def test_long_real_urls_fit_the_source_field():
    from ppt_agent.v2.visual.content import StatementContent
    url = "https://github.com/archlinux-pm/awesome-ai-agent-product-management/blob/main/docs/ai-pm-capability-model.md"
    assert StatementContent(title="t", statement="s", source=url).source == url


def test_content_pages_are_generated_concurrently(tmp_path):
    class Slow(MockLLMClient):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.peak = 0

        async def complete_json(self, **kw):
            if kw["task"] != "page_content":
                return await super().complete_json(**kw)
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            return await super().complete_json(**kw)

    client = Slow()
    result = build_deck(BuildRequest(prompt="并发验证", page_count=20, output_dir=str(tmp_path),
                                     concurrency=4), client, progress=lambda _: None)
    assert client.peak == 4
    report = json.loads(Path(result.run_report_path).read_text())
    plan = json.loads((tmp_path / "checkpoints/typeset/archetype_plan.json").read_text())
    assert [p["assigned_archetype"] for p in report["typeset_pages"]] == [plan[k] for k in sorted(plan, key=int)]
    assert report["content_statistics"]["diversity_violations"] == []


def test_web_search_runs_per_section_within_the_cap(tmp_path):
    from ppt_agent.v2.search import SearchResult

    class FakeSearch:
        def __init__(self):
            self.queries = []

        async def search(self, query, *, max_results=5):
            self.queries.append(query)
            n = len(self.queries)
            return [SearchResult(title=f"结果{n}", url=f"https://example{n}.com/a",
                                 snippet=f"{query} 相关资料，第 {n} 条，包含 3 项事实。")]

    search = FakeSearch()
    build_deck(BuildRequest(prompt="联网分章节", page_count=30, output_dir=str(tmp_path),
                            enable_search=True), MockLLMClient(), search_provider=search,
               progress=lambda _: None)
    from ppt_agent.v2.orchestrator import MAX_SEARCH_CALLS
    assert 2 <= len(search.queries) <= MAX_SEARCH_CALLS
    store = json.loads((tmp_path / "checkpoints/evidence_store.json").read_text())
    web_docs = [d for d in store["documents"] if d.get("page_kind") == "web"]
    assert len(web_docs) == len(search.queries)
    sections = json.loads((tmp_path / "checkpoints/section_search_results.json").read_text())
    assert len(sections) == len(search.queries) - 1  # first call is the topic search


def test_large_sections_are_planned_in_batches_and_survive_a_timeout(tmp_path):
    from ppt_agent.v2.orchestrator import SECTION_PAGES_BATCH
    from ppt_agent.v2.providers import ProviderError

    class OneTimeout(MockLLMClient):
        def __init__(self):
            super().__init__()
            self.section_calls = []

        async def complete_json(self, **kw):
            if kw["task"] == "section_pages":
                self.section_calls.append(kw["context"]["page_count"])
                if len(self.section_calls) == 2:
                    raise ProviderError("[section_pages] provider call failed after 4 attempts: ")
            return await super().complete_json(**kw)

    client = OneTimeout()
    result = build_deck(BuildRequest(prompt="大章节分批", page_count=60, output_dir=str(tmp_path)),
                        client, progress=lambda _: None)
    assert max(client.section_calls) <= SECTION_PAGES_BATCH
    report = json.loads(Path(result.run_report_path).read_text())
    fallbacks = [e for e in report["planning_events"] if e["action"] == "fallback"]
    assert 0 < len(fallbacks) <= SECTION_PAGES_BATCH  # only the failed batch degraded
    assert result.pptx_path


def test_structural_titles_keep_years_but_drop_statistics():
    from ppt_agent.v2.typeset_pipeline import qualitative_structural_text
    removed = []
    assert qualitative_structural_text('人工智能发展报告（2024年）', 'deck_title', 1, removed) == '人工智能发展报告（2024年）'
    assert qualitative_structural_text('用户规模达5.15亿', 'subtitle', 1, removed) == '用户规模达'
    assert [r['value'] for r in removed] == ['5.15亿']
