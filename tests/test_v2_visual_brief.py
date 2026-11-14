import asyncio

from ppt_agent.v2 import figures, visual_brief
from ppt_agent.v2.htmldeck import layout_plan
from ppt_agent.v2.htmldeck.prompts import diagram_words


class Client:
    def __init__(self, replies):
        self.replies, self.tasks = dict(replies), []

    async def complete_json(self, *, task, **kwargs):
        self.tasks.append(task)
        reply = self.replies[task]
        return reply.pop(0) if isinstance(reply, list) else reply


PAGE = {"title": "2024年上半年AI融资达316亿美元", "items": [{"heading": "占比", "body": "升至12.1%"}]}
OFFER = [{"id": "series:abc", "type": "核对过的数据序列", "metric": "融资占全行业融资比例", "payload": object()}]


def decide(reply, candidates=OFFER):
    return asyncio.run(visual_brief.decide(Client({"visual_brief": reply}), None, 5, PAGE, candidates))


def test_data_being_available_is_not_a_reason_to_draw_it():
    said_no = decide({"claim": "融资达316亿美元", "core_metric": "融资金额", "use": "none", "reason": "占比不是金额"})
    assert said_no["use"] == "none" and said_no["core_metric"] == "融资金额"
    assert decide({"use": "series:abc", "role": "supporting"})["role"] == "supporting"
    assert decide({"use": "series:zzz"})["use"] == "none"          # only what was offered
    client = Client({})
    assert asyncio.run(visual_brief.decide(client, None, 5, PAGE, []))["use"] == "none" and client.tasks == []


def test_each_thing_is_shown_once_where_it_leads():
    briefs = {4: {"use": "figure:a", "role": "supporting"}, 9: {"use": "figure:a", "role": "primary"},
              12: {"use": "none", "role": "primary"}}
    settled = visual_brief.settle(briefs)
    assert settled[9]["use"] == "figure:a" and settled[4]["use"] == "none" and settled[12]["use"] == "none"


SERIES = {"metric": "m", "unit": "%", "kind": "time", "points": [
    {"label": "2022年", "value": 4.5, "display": "4.5%", "ref": "r 第1页"},
    {"label": "2024年", "value": 12.1, "display": "12.1%", "ref": "r 第1页"}]}
RICH = {"archetype": "rich", "relation": "single", "tier": "visual", "highlight": {"value": "316亿美元", "text": "融资"},
        "items": [{"heading": "占比"}], "series": [SERIES]}


def test_a_supporting_chart_is_drawn_small_beside_the_pages_own_figure():
    from ppt_agent.v2.htmldeck import prompts

    saved = [layout_plan.Fingerprint(page=5, form="ledger")]
    lead = layout_plan.repair(saved, {5: {**RICH, "visual": {"role": "primary"}}})[0]
    second = layout_plan.repair(saved, {5: {**RICH, "visual": {"role": "supporting"}}})[0]
    assert (lead.form, lead.ratio) == ("chart", "wide") and (second.form, second.ratio) == ("chart", "narrow")
    brief = prompts._chart_brief({**RICH, "visual": {"role": "supporting"}}, "chart——…")[0]
    assert "不是主角" in brief and "data-chart" in brief


def levels(extra=None, groups=None):
    return {"kind": "levels", "complete": True, "levels": [
        {"name": "可控 可持续", "items": []}, {"name": "政治 文化", "items": []}, {"name": "数据 框架", "items": extra or []}],
        "groups": groups or [{"name": "全人类", "covers": [1]}, {"name": "国家社会", "covers": [2]},
                             {"name": "技术系统", "covers": [3]}, {"name": "衍生安全", "covers": [1, 2]}]}


def test_two_readings_agree_on_words_not_on_how_far_a_bracket_reaches():
    first, second = figures._structure(levels()), figures._structure(levels())
    next(group for group in second["groups"] if group["name"] == "衍生安全")["covers"] = [1, 2, 3]  # the bracket read longer
    assert figures._same_reading(first, second)
    other = figures._structure(levels(extra=["芯片"]))
    assert not figures._same_reading(first, other)
    named = figures._named(first)
    assert [level["name"] for level in named["levels"]] == ["全人类", "国家社会", "技术系统"]
    assert named["levels"][0]["items"] == ["可控", "可持续"] and [g["name"] for g in named["groups"]] == ["衍生安全"]
    assert figures._structure({**levels(), "complete": False}) is None


def test_which_levels_a_group_spans_is_decided_by_the_reports_own_words():
    text = "人工智能的衍生安全问题是对个人组织、国家社会，乃至全人类造成的安全问题。"
    redraw = {"kind": "levels", "levels": [{"name": "全人类", "items": []}, {"name": "国家社会", "items": []},
                                           {"name": "个人组织", "items": []}, {"name": "技术系统", "items": []}],
              "groups": [{"name": "衍生安全", "covers": [1, 2]}]}
    quoted = {"groups": [{"name": "衍生安全", "levels": ["个人组织", "国家社会", "全人类"],
                          "quote": "衍生安全问题是对个人组织、国家社会，乃至全人类造成的安全问题"}]}
    made, trouble = asyncio.run(figures._ground(Client({"figure_ground": quoted}), redraw, text))
    assert trouble is None and made["groups"][0]["covers"] == [1, 2, 3] and made["groups"][0]["read_from_picture"] == [1, 2]
    invented = {"groups": [{"name": "衍生安全", "levels": ["全人类"], "quote": "衍生安全只涉及全人类"}]}
    assert asyncio.run(figures._ground(Client({"figure_ground": invented}), redraw, text))[1]


def test_a_titled_box_keeps_what_it_holds():
    nested = figures._item({"name": "模型训练工具", "children": ["分布式策略", "训练加速"]})
    assert figures.item_text(nested) == "模型训练工具（分布式策略、训练加速）" and figures._item("RAG") == "RAG"
    diagram = {"levels": [{"name": "大模型开发平台", "items": [nested, "RAG"]}], "groups": [{"name": "衍生安全", "covers": [1]}]}
    assert diagram_words(diagram) == ["大模型开发平台", "模型训练工具（分布式策略、训练加速）", "RAG", "衍生安全"]
