from types import SimpleNamespace

from ppt_agent.v2 import page_budget
from ppt_agent.v2.planning import DeckOutline, DeckSkeleton, PageBrief, PageSlot

FACTS = ("截至2025年6月，我国生成式人工智能用户规模达5.15亿人，较2024年12月增长2.66亿人；"
         "普及率达到36.5%，较2024年12月提升18.8个百分点。")


def _packet(text, chunks):
    return SimpleNamespace(text=text, chunk_ids=chunks)


def test_pages_sharing_too_few_facts_are_over_budget():
    packets = {3: _packet(FACTS, ["c1", "c2"]), 4: _packet(FACTS, ["c1", "c2"]), 5: _packet(FACTS, ["c1", "c2"]),
               6: _packet(FACTS, ["c2", "c1"]), 7: _packet("豆包首选占比为47.1%，DeepSeek为34.0%，其他产品18.8%。", ["c9"])}
    grouped = page_budget.groups([3, 4, 5, 6, 7], packets)
    assert grouped == [[3, 4, 5, 6], [7]]
    found = page_budget.over_budget(grouped[0], packets)
    assert found["facts"] == 4 and found["supports"] == 2
    assert page_budget.over_budget(grouped[1], packets) is None


def test_decisions_keep_a_page_per_group_and_a_chunk_per_page():
    over = [{"pages": [3, 4, 5]}]
    spare = [{"chunk_id": "c7"}, {"chunk_id": "c8"}]
    reply = {"pages": [{"page": 3, "decision": "drop"}, {"page": 4, "decision": "drop"}, {"page": 5, "decision": "drop"}]}
    assert "3" not in page_budget.validate(reply, over, spare)
    reply = {"pages": [{"page": 4, "decision": "retarget", "chunks": ["c7"], "title": "用户年龄结构"},
                       {"page": 5, "decision": "retarget", "chunks": ["c7", "x"], "title": "又一页"}]}
    decided = page_budget.validate(reply, over, spare)
    assert decided["4"]["chunks"] == ["c7"] and "5" not in decided


def test_dropping_a_page_renumbers_and_removes_an_empty_section_divider():
    slots = [PageSlot(page_number=1, kind="cover"), PageSlot(page_number=2, kind="section_divider", section_index=1),
             PageSlot(page_number=3, kind="content", section_index=1, brief=PageBrief(title="a")),
             PageSlot(page_number=4, kind="section_divider", section_index=2),
             PageSlot(page_number=5, kind="content", section_index=2, brief=PageBrief(title="b")),
             PageSlot(page_number=6, kind="closing")]
    skeleton = DeckSkeleton.model_construct(deck_title="t", language="zh-CN", total_pages=6, outline=None, slots=slots)
    new, packets = page_budget.apply(skeleton, {3: "p3", 5: "p5"}, {"decisions": {"5": {"decision": "drop"}}}, None)
    assert [(slot.page_number, slot.kind) for slot in new.slots] == [(1, "cover"), (2, "section_divider"), (3, "content"), (4, "closing")]
    assert packets == {3: "p3"} and new.total_pages == 4
