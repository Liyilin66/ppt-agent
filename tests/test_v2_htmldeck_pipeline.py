import asyncio

import pytest

from ppt_agent.v2.htmldeck import browser, themes
from ppt_agent.v2.htmldeck.fonts import office_font_css
from ppt_agent.v2.htmldeck.pipeline import DesignJob, assign_ideas, design_page, structural_page
from ppt_agent.v2.ir import BoxItem, RichTextItem
from ppt_agent.v2.mock import MockLLMClient

pytestmark = pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")

SOURCE = "资料来源：《人工智能发展报告（2024年）》第 40 页"
GOOD = (
    '<div class="page"><div class="head"><div class="kicker">02 · 机会</div>'
    '<h1 class="title">研发成本降低约 70%</h1></div>'
    '<div class="body stretch"><div class="panel f1"><div class="stat l">70%</div>'
    '<div class="p">药物分子大模型使研发成本降低约 70%</div></div>'
    '<div class="card f1"><div class="h">周期缩短</div><div class="p">从数年缩短至数月</div></div></div>'
    f'<div class="foot"><div class="source">{SOURCE}</div><div class="pageno">09</div></div></div>'
)
OVERFLOWING = GOOD.replace("从数年缩短至数月", "很长的说明文字" * 120)
WITH_EXTRA_NUMBER = GOOD.replace("从数年缩短至数月", "准确率 93%")
WITHOUT_SOURCE = GOOD.replace(SOURCE, "")


class Scripted:
    """Replies with the given pages in order; reviews with the given problems."""

    def __init__(self, pages, reviews=()):
        self.pages, self.reviews, self.prompts = list(pages), list(reviews), []

    async def complete_text(self, *, task, system, user, max_output_tokens=None, context=None, images=None):
        self.prompts.append(user)
        return self.pages.pop(0)

    async def complete_json(self, *, task, system, user, max_output_tokens=None, context=None, images=None):
        assert task == "html_review" and images and images[0][0] == "image/png"
        return {"problems": self.reviews.pop(0) if self.reviews else []}


def job():
    return DesignJob(page_number=9, archetype="metrics", title="研发成本降低约 70%", kicker="02 · 机会",
                     source=SOURCE, content={"title": "研发成本降低约 70%", "metrics": [
                         {"value": "70%", "label": "研发成本降低"}, {"value": "数月", "label": "周期"}]},
                     idea="左边 panel，右边 card")


def run(client, look=True, the_job=None):
    async def go():
        async with browser.Session() as session:
            return await design_page(client, session, themes.get("bright"), office_font_css()[0],
                                     the_job or job(), look=look)

    return asyncio.run(go())


def test_a_good_page_passes_first_time_and_becomes_native_elements():
    client = Scripted([GOOD])
    result = run(client)
    assert result.ok and result.attempts == 1 and result.reviewed and result.defects == []
    kinds = {type(element) for element in result.page.elements}
    assert kinds == {BoxItem, RichTextItem}
    assert result.page.title == "研发成本降低约 70%" and result.page.show_chrome is False
    assert "建议的排布：左边 panel，右边 card" in client.prompts[0]


@pytest.mark.parametrize("bad, word", [(OVERFLOWING, "超出"), (WITH_EXTRA_NUMBER, "93%"),
                                       (WITHOUT_SOURCE, "资料来源")], ids=["overflow", "number", "source"])
def test_a_defect_is_sent_back_and_the_rewrite_is_used(bad, word):
    client = Scripted([bad, GOOD])
    result = run(client, look=False)
    assert result.ok and result.attempts == 2 and result.defects == []
    assert word in client.prompts[1] and "你上一次写的 HTML" in client.prompts[1]
    assert "93%" not in result.html


def test_a_page_that_never_passes_is_given_up():
    result = run(Scripted([OVERFLOWING] * 3), look=False)
    assert not result.ok and result.attempts == 3 and result.defects


def test_review_problems_trigger_one_revision_kept_only_if_clean():
    kept = run(Scripted([GOOD, GOOD.replace("周期缩短", "周期更短")], reviews=[["右侧卡片太空"]]))
    assert kept.ok and kept.attempts == 2 and "周期更短" in kept.html
    assert kept.review_problems == ["右侧卡片太空"]
    reverted = run(Scripted([GOOD, OVERFLOWING], reviews=[["右侧卡片太空"]]))
    assert reverted.ok and "周期缩短" in reverted.html


def test_the_mock_client_writes_a_page_that_passes():
    result = run(MockLLMClient())
    assert result.ok and result.attempts == 1


def test_ideas_rotate_within_a_kind():
    jobs = [DesignJob(page_number=n, archetype="points", content={}) for n in (3, 1, 2)]
    assign_ideas(jobs)
    assert len({item.idea for item in jobs}) == 3


def test_structural_pages_need_no_model():
    async def go():
        async with browser.Session() as session:
            return [await structural_page(session, themes.get(name), office_font_css()[0], kind=kind,
                                          page_number=2, deck_title="人工智能企业应用机会与治理风险",
                                          subtitle="基于信通院报告", section_index=2, section_title="企业应用机会",
                                          sections=[("市场态势", 3), ("企业应用机会", 8), ("治理风险", 14)])
                    for name in themes.THEME_NAMES for kind in ("cover", "toc", "section_divider", "closing")]

    results = asyncio.run(go())
    assert all(result.ok for result in results), [result.defects for result in results if not result.ok]
    assert {result.page.role for result in results} == {"cover", "toc", "section_divider", "closing"}


def test_a_page_that_is_slightly_too_tall_is_tightened_without_another_call():
    rows = "".join(f'<div class="card"><div class="h">第 {n} 点</div><div class="p">说明文字说明文字说明文字</div></div>'
                   for n in (1, 2, 3))
    tall = GOOD.replace('<div class="body stretch">', '<div class="body col">').replace(
        '<div class="card f1"><div class="h">周期缩短</div><div class="p">从数年缩短至数月</div></div>', rows)
    client = Scripted([tall])
    result = run(client, look=False)
    assert result.ok and result.attempts == 1 and 'class="page compact' in result.html


# --- studio theme: aesthetic defects are polished, never fatal ---------------

STUDIO_SOURCE = "资料来源：《人工智能发展报告（2024年）》第 42 页"


def studio_page(conclusion):
    return (
        '<div class="page"><div class="r" style="grid-column:1/9;grid-row:1/2"><div class="kicker">02 · 机会</div></div>'
        '<div class="r" style="grid-column:1/8;grid-row:2/4"><div class="t2">北汽福田AI设计使零部件4减至1</div></div>'
        f'<div class="r" style="grid-column:1/8;grid-row:4/9">{conclusion}</div>'
        '<div class="r ll" style="grid-column:8/13;grid-row:2/9"><div class="t3">产品轻量化</div>'
        '<div class="b">通过AI设计路径优化，产品重量减轻70%。</div><div class="t3">结构强度</div>'
        '<div class="b">零部件整合的同时，产品强度增强18.8%。</div><div class="t3">设计迭代</div>'
        '<div class="b">辅助设计软件可根据市场需求快速迭代产品设计，并提升仿真效率。</div>'
        '<div class="t3">仿真验证</div><div class="b">工程约束明确的任务更容易验证效果。</div></div>'
        f'<div class="source">{STUDIO_SOURCE}</div><div class="pageno">11</div></div>'
    )


LOUD = studio_page('<div class="q1">管理层应优先在工程约束明确、可仿真验证的设计任务中投入AI试点。</div>')
CALM = studio_page('<div class="b">管理层应优先在工程约束明确、可仿真验证的设计任务中投入AI试点。</div>'
                   '<div class="b mut">研发设计是AI创造战略价值的重要切口。</div><div class="t3">先试点</div>'
                   '<div class="b">选择验证数据充分的设计任务。</div><div class="t3">再推广</div>'
                   '<div class="b">效果可验证后扩大范围。</div>')


def studio_job():
    return DesignJob(page_number=11, archetype="rich", title="北汽福田AI设计使零部件4减至1", kicker="02 · 机会",
                     source=STUDIO_SOURCE, form="ledger", idea="账本",
                     content={"title": "北汽福田AI设计使零部件4减至1", "tier": "standard", "items": [
                         {"heading": "产品轻量化", "value": "70%"}, {"heading": "结构强度", "value": "18.8%"}]})


def run_studio(client, **kwargs):
    async def go():
        async with browser.Session() as session:
            return await design_page(client, session, themes.get("studio"), office_font_css()[0],
                                     studio_job(), look=False, **kwargs)

    return asyncio.run(go())


def test_an_aesthetic_defect_is_sent_back_and_the_calmer_page_is_used():
    client = Scripted([LOUD, CALM])
    result = run_studio(client)
    assert result.ok and result.attempts == 2 and result.aesthetic == []
    assert "不能用这么大的字" in client.prompts[1] and 'class="q1"' not in result.html


def test_a_page_that_stays_loud_is_still_kept():
    client = Scripted([LOUD] * 3)
    result = run_studio(client)
    assert result.ok and result.attempts == 3 and client.pages == []
    assert any("不能用这么大的字" in message for message in result.aesthetic)


def test_an_existing_page_can_be_repaired_in_place():
    client = Scripted([CALM])
    result = run_studio(client, seed_html=LOUD, seed_problems=["结论的字太大"])
    assert result.ok and result.attempts == 1
    assert "你上一次写的 HTML" in client.prompts[0] and "结论的字太大" in client.prompts[0]
