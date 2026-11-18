import asyncio

from ppt_agent.v2 import claim_audit


class _Checkpoints:
    def __init__(self):
        self.saved = {}

    def load(self, name):
        return self.saved.get(name)

    def save(self, name, data):
        self.saved[name] = data


def test_the_rule_decides_not_the_reviewers_word():
    said_ok = {"claim": "短视频领跑", "verdict": "ok",
               "facts": [{"text": "回答问题 76.0%", "relation": "unrelated", "why": "AI 使用目的"}], "unsupported": []}
    assert claim_audit.normalise(said_ok)["verdict"] == "fix"
    clean = {"facts": [{"text": "94.3亿小时", "relation": "direct"}, {"text": "14.1%", "relation": "independent"}],
             "verdict": "fix", "instruction": "x"}
    assert claim_audit.normalise(clean) == {"claim": "", "facts": clean["facts"], "unsupported": [], "mixed": [], "verdict": "ok",
                                            "instruction": ""}


def test_instruction_names_each_finding_and_verdicts_are_cached():
    verdict = claim_audit.normalise({"facts": [{"text": "回答问题 76.0%", "relation": "unrelated", "why": "别的对象"}],
                                     "unsupported": [{"text": "明显提速", "why": "没有上一年的新增数"}]})
    page = {"title": "备案登记明显提速", "lead": "AI 用途：回答问题 76.0%", "items": [{"heading": "a"}]}
    text = claim_audit.instruction(claim_audit.decide(verdict, page))
    assert "回答问题 76.0%" in text and "明显提速" in text

    calls = []

    class Reviewer:
        async def complete_json(self, **kwargs):
            calls.append(kwargs["task"])
            return {"facts": [], "unsupported": [], "verdict": "ok"}

    checkpoints = _Checkpoints()
    page = {"title": "t", "items": [{"heading": "a", "value": "1%"}], "source": "x"}
    for _ in range(2):
        assert asyncio.run(claim_audit.audit_page(Reviewer(), checkpoints, 3, page, "evidence"))["verdict"] == "ok"
    assert calls == ["claim_audit"]


def test_a_reviewer_failure_is_an_error_never_a_pass():
    class Broken:
        async def complete_json(self, **kwargs):
            raise RuntimeError("down")

    assert asyncio.run(claim_audit.audit_page(Broken(), None, 3, {"title": "t"}, ""))["verdict"] == "error"


def test_only_the_claim_is_rewritten_items_move_and_wording_is_reported():
    page = {"title": "短视频以94.3亿小时净增领跑", "lead": "短视频贡献最大时长净增量",
            "items": [{"heading": "短视频净增", "value": "94.3亿小时", "body": "居首位"},
                      {"heading": "同比增速", "value": "14.1%", "body": "同比增长"},
                      {"heading": "AI使用目的", "value": "76.0%", "body": "回答问题"}]}
    verdict = claim_audit.normalise({"facts": [{"text": "AI使用目的 76.0% 回答问题", "relation": "unrelated", "why": "别的对象"}],
                                     "unsupported": [{"text": "市场规模较大", "why": "无比较"}]})
    decision = claim_audit.decide(verdict, page)
    assert decision["rewrite"] == [] and decision["move_items"] == [2] and len(decision["minor"]) == 1
    verdict = claim_audit.normalise({"unsupported": [{"text": "净增领跑", "why": "无同类比较"}]})
    assert claim_audit.decide(verdict, page)["rewrite"]
    # at least two items stay on the page
    two = {**page, "items": page["items"][1:]}
    assert claim_audit.decide(claim_audit.normalise(
        {"facts": [{"text": "AI使用目的 76.0% 回答问题", "relation": "unrelated"}]}), two)["move_items"] == []


def test_rephrasing_may_not_add_a_figure_and_is_cached():
    calls = []

    class Writer:
        def __init__(self, reply):
            self.reply = reply

        async def complete_json(self, **kwargs):
            calls.append(kwargs["task"])
            return self.reply

    page = {"title": "短视频以94.3亿小时净增领跑，AI需求以问答为主", "lead": "x",
            "items": [{"heading": "短视频净增", "value": "94.3亿小时"}, {"heading": "同比", "value": "14.1%"}]}
    decision = {"rewrite": [{"mixed": {"text": "短视频以94.3亿小时净增领跑，AI需求以问答为主", "why": "两个对象"}}]}
    ok = asyncio.run(claim_audit.rephrase(Writer({"title": "短视频净增94.3亿小时居首", "lead": "同比增长14.1%"}),
                                          _Checkpoints(), 22, page, decision))
    assert ok == {"title": "短视频净增94.3亿小时居首", "lead": "同比增长14.1%"}
    added = asyncio.run(claim_audit.rephrase(Writer({"title": "短视频净增94.3亿小时，用户10.74亿", "lead": ""}),
                                             _Checkpoints(), 22, page, decision))
    assert added == {}
    store = _Checkpoints()
    for _ in range(2):
        asyncio.run(claim_audit.rephrase(Writer({"title": "短视频净增居首", "lead": ""}), store, 22, page, decision))
    assert calls.count("claim_rephrase") == 3  # two fresh pages above, then one call for the cached pair
