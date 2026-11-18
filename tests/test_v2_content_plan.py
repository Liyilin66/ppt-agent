import asyncio

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
    assert not any(12 in pair for pair in pairs)  # too far apart to read as a repeat


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
