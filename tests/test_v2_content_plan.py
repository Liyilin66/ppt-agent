import asyncio
import json

from ppt_agent.v2 import content_plan as cp


def test_facts_keep_measured_sentences_only():
    text = "截至2025年6月，用户规\n模达5.15亿人。本章介绍方法。\n0% 20% 40% 60%\n豆包 47.1%\n普及率达到36.5%，较去年提升。"
    assert cp.facts(text) == ["截至2025年6月，用户规模达5.15亿人。", "豆包 47.1%普及率达到36.5%，较去年提升。"]


def test_conflicts_two_details_leading_with_one_figure():
    plan = {4: {"lead": "普及率 36.5%", "role": "detail"}, 5: {"lead": "普及率升至36.5%", "role": "detail"},
            6: {"lead": "豆包 47.1%", "role": "detail"}}
    assert cp.conflicts(plan) == [{"pages": [4, 5], "figures": ["36.5%"]}]
    plan[5]["role"] = "summary"
    assert cp.conflicts(plan) == []


def _page(title, value, *items):
    return {"title": title, "highlight": {"value": value, "text": ""},
            "items": [{"heading": "x", "value": v, "body": ""} for v in items]}


def test_suspects_same_focal_figure_nearby_only():
    contents = {4: _page("普及率半年提升18.8个百分点至36.5%", "36.5%", "17.7%"),
                5: _page("普及率半年翻倍至36.5%", "36.5%", "5.15亿人"),
                6: _page("豆包首选占比达47.1%", "47.1%", "34.0%"),
                12: _page("普及率达36.5%", "36.5%")}
    pairs = [pair["pages"] for pair in cp.suspects(contents)]
    assert [4, 5] in pairs and [4, 6] not in pairs
    assert [4, 12] in pairs and [6, 12] not in pairs  # far apart: only the plain signs count


def test_rewrite_the_page_straying_from_its_plan_and_never_a_signed_page():
    contents = {4: _page("a", "36.5%"), 5: _page("b", "36.5%")}
    plan = {4: {"lead": "普及率 36.5%"}, 5: {"lead": "半年新增2.49亿人"}}
    assert cp.to_rewrite([4, 5], contents, plan, set()) == 5
    plan[5]["lead"] = "36.5%"
    plan[4]["lead"] = "17.7%"
    assert cp.to_rewrite([4, 5], contents, plan, set()) == 4
    assert cp.to_rewrite([4, 5], contents, plan, {4}) == 5
    assert cp.to_rewrite([4, 5], contents, plan, {4, 5}) is None


class _Checkpoints:
    def __init__(self):
        self.saved = {}

    def load(self, name):
        return self.saved.get(name)

    def save(self, name, value):
        self.saved[name] = value


class _Judge:
    calls = 0

    async def complete_json(self, **kwargs):
        _Judge.calls += 1
        return {"same": True, "why": "都讲普及率升到36.5%"}


def test_review_rewrites_once_and_caches_judgements():
    contents = {4: _page("普及率至36.5%", "36.5%"), 5: _page("普及率翻倍至36.5%", "36.5%")}
    plan = {4: {"lead": "36.5%", "claim": "普及率翻倍"}, 5: {"lead": "2.49亿", "claim": "新增用户来自..."}}
    checkpoints = _Checkpoints()
    first = asyncio.run(cp.review(_Judge(), checkpoints, contents, plan, set()))
    assert first["repeated"] == [[4, 5]] and list(first["rewrite"]) == [5]
    assert "2.49亿" in first["rewrite"][5]
    calls = _Judge.calls
    asyncio.run(cp.review(_Judge(), checkpoints, contents, plan, set()))
    assert _Judge.calls == calls


def test_a_page_restating_earlier_figures_is_a_suspect():
    contents = {3: _page("用户达5.15亿", "5.15亿人", "2.66亿人"), 4: _page("普及率36.5%", "36.5%", "18.8个百分点"),
                5: _page("AI进入规模化采用", "2.66亿人", "5.15亿人", "36.5%", "18.8个百分点")}
    pairs = {tuple(pair["pages"]): pair for pair in cp.suspects(contents)}
    assert pairs[(4, 5)]["nothing_new"] and pairs[(3, 5)]["nothing_new"]


def test_same_focal_figure_on_two_detail_pages_is_repeated_without_asking():
    class Never:
        async def complete_json(self, **kwargs):
            raise AssertionError("no judgement needed")

    contents = {4: _page("覆盖36.5%人口", "36.5%"), 5: _page("普及率半年升至36.5%", "36.5%", "18.8个百分点")}
    plan = {4: {"lead": "36.5%", "role": "detail"}, 5: {"lead": "18.8个百分点", "role": "detail"}}
    result = asyncio.run(cp.review(Never(), _Checkpoints(), contents, plan, set()))
    assert result["repeated"] == [[4, 5]] and list(result["rewrite"]) == [5]


def test_one_figure_pages_retelling_their_neighbours_are_suspects():
    contents = {3: _page("半年新增2.66亿用户", "2.66亿人", "5.15亿人"), 4: _page("普及率半年提升18.8个百分点", "18.8个百分点", "36.5%"),
                5: _page("5.15亿用户奠定竞争基础", "5.15亿人"), 6: _page("36.5%普及率表明覆盖已广泛", "36.5%")}
    pairs = {tuple(pair["pages"]): pair for pair in cp.suspects(contents)}
    assert pairs[(3, 5)]["nothing_new"] and pairs[(4, 6)]["nothing_new"]


def test_a_repeat_far_down_the_deck_is_still_a_suspect():
    contents = {17: _page("外卖以3748万人增量领跑典型应用", "3748万人"), 22: _page("外卖以3748万人新增领跑应用增长", "3748万人"),
                30: _page("短视频覆盖率升至95.4%", "95.4%")}
    pairs = {tuple(pair["pages"]): pair for pair in cp.suspects(contents)}
    assert pairs[(17, 22)]["why"] == "焦点数字相同" and (17, 30) not in pairs


def test_a_long_deck_is_planned_in_batches_that_see_each_other():
    from types import SimpleNamespace

    calls = []

    class Planner:
        async def complete_json(self, **kwargs):
            asked = json.loads(kwargs["user"])
            calls.append(len(asked["already_planned"]))
            return {"pages": [{"page": entry["page"], "claim": f"c{entry['page']}", "lead": "", "owns": []}
                              for entry in asked["pages"]]}

    slots = [SimpleNamespace(page_number=n, brief=None, section_title="s") for n in range(3, 40)]
    packets = {n: SimpleNamespace(text="", chunk_ids=[f"c{n}"]) for n in range(3, 40)}
    planned = asyncio.run(cp.plan(Planner(), _Checkpoints(), slots, packets))
    assert len(planned) == 37 and calls == [0, 12, 24, 36]


def test_a_truncated_batch_is_retried_then_stops_the_run_with_its_error():
    from types import SimpleNamespace

    class Truncating:
        async def complete_json(self, **kwargs):
            asked = json.loads(kwargs["user"])
            if asked["pages"][0]["page"] == 15:  # the second batch: its reply never parses
                raise ValueError("reply truncated at max_output_tokens")
            return {"pages": [{"page": entry["page"], "claim": f"c{entry['page']}", "owns": []} for entry in asked["pages"]]}

    slots = [SimpleNamespace(page_number=n, brief=None, section_title="s") for n in range(3, 40)]
    packets = {n: SimpleNamespace(text="", chunk_ids=[f"c{n}"]) for n in range(3, 40)}
    checkpoints = _Checkpoints()
    planned = asyncio.run(cp.plan(Truncating(), checkpoints, slots, packets))
    saved = checkpoints.saved["typeset/content_plan.json"]
    assert saved["unplanned_pages"] == list(range(15, 27)) and len(saved["failures"]) == 3
    try:
        cp.require(planned, list(range(3, 40)), checkpoints)
    except cp.PlanningFailed as error:
        assert "15" in str(error) and "truncated" in str(error)
    else:
        raise AssertionError("an unplanned deck must not go on")
