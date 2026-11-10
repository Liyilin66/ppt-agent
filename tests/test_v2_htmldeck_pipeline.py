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
