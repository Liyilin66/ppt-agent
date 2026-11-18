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
    assert claim_audit.normalise(clean) == {"claim": "", "facts": clean["facts"], "unsupported": [], "verdict": "ok",
                                            "instruction": ""}


def test_instruction_names_each_finding_and_verdicts_are_cached():
    verdict = claim_audit.normalise({"facts": [{"text": "回答问题 76.0%", "relation": "unrelated", "why": "别的对象"}],
                                     "unsupported": [{"text": "明显提速", "why": "没有上一年的新增数"}]})
    text = claim_audit.instruction(verdict)
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
