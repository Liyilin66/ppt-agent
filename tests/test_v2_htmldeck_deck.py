import asyncio
import json
from pathlib import Path

import pytest

from ppt_agent.v2.htmldeck import browser, deck, sanitize, structural, themes
from ppt_agent.v2.htmldeck.checks import AESTHETIC, find_defects
from ppt_agent.v2.htmldeck.document import wrap
from ppt_agent.v2.htmldeck.fonts import office_font_css
from ppt_agent.v2.htmldeck.pipeline import DesignJob

needs_browser = pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")
SOURCE = "资料来源：《人工智能发展报告（2024年）》第 11 页"
TITLE = "2024年全球AI产业将达6233亿美元"
CONTENT = {
    "title": TITLE, "archetype": "rich", "relation": "cases", "tier": "standard",
    "takeaway": "管理层应将AI视为战略投入，优先配置资源到可规模化落地的业务场景。",
    "highlight": {"value": "6233亿美元", "text": "IDC预测的2024年全球AI产业规模"},
    "items": [
        {"heading": "产业规模增速", "body": "IDC预测，2024年全球人工智能产业规模将同比增长21.5%。", "value": "21.5%", "date": "2024年"},
        {"heading": "大模型驱动增长", "body": "自2023年起全球基础模型数量快速增加。", "date": "2023年起"},
        {"heading": "模型迭代活跃", "body": "2024年以来，全球基础模型新增或迭代近百个。", "value": "近百个"},
        {"heading": "生成式AI加速产业化", "body": "生成式人工智能技术正加速产业化进程。"},
    ],
}


def job(page=4):
    return DesignJob(page_number=page, archetype="rich", title=TITLE, kicker="01 · AI投入窗口已开启",
                     source=SOURCE, content=json.loads(json.dumps(CONTENT)))


def page_html(page, body):
    return (f'<div class="page"><div class="r" style="grid-column:1/9;grid-row:1/2"><div class="kicker">01 · AI投入窗口已开启</div></div>'
            f'<div class="r" style="grid-column:1/8;grid-row:2/4"><div class="t2">{TITLE}</div></div>'
            f'<div class="r" style="grid-column:1/13;grid-row:4/9">{body}</div>'
            f'<div class="source">{SOURCE}</div><div class="pageno">{page:02d}</div></div>')


def fits(page):
    return page_html(page, "".join(
        f'<div class="t3">{item["heading"]}</div><div class="b">{item["body"]}</div>' for item in CONTENT["items"]))


def too_tall(page):
    return page_html(page, '<div class="b">' + "放不下的一段话。" * 400 + "</div>")


class ByForm:
    """Writes a page that fits only for the forms in ``good``."""

    def __init__(self, good=()):
        self.good, self.forms = set(good), []

    async def complete_text(self, *, task, system, user, max_output_tokens=None, context=None, images=None):
        the_job = context["job"]
        self.forms.append(the_job.form)
        return fits(the_job.page_number) if the_job.form in self.good else too_tall(the_job.page_number)

    async def complete_json(self, *, task, system, user, max_output_tokens=None, context=None, images=None):
        return {}


def design(client, jobs):
    return asyncio.run(deck.design_deck(client, "studio", jobs, [], look=False))


@needs_browser
def test_a_page_that_does_not_fit_its_form_is_composed_another_way():
    the_job = job()
    client = ByForm(good={"margin"})
    results, summary = design(client, [the_job])
    assert client.forms[0] == "ledger" and the_job.form == "margin"
    assert results[4].ok and not results[4].templated and summary["templated_pages"] == []


@needs_browser
def test_plain_pages_near_each_other_are_arranged_differently():
    jobs = [job(4), job(5)]
    results, summary = design(ByForm(), jobs)
    assert summary["templated_pages"] == [4, 5] and all(results[number].ok for number in (4, 5))
    assert len(set(summary["plain_variants"].values())) == 2


def test_a_date_is_never_shown_as_a_figure():
    rows = deck._plain_rows(job())
    assert [figure for figure, _, _ in rows] == ["21.5%", "", "近百个", ""]
    assert rows[1][1] == "大模型驱动增长"  # its date is already in the body
    html = deck._plain_html(job(), "ledger")
    assert '<div class="fig n">02</div>' in html and 'class="fig">2023' not in html


def test_the_plain_page_follows_the_content():
    assert deck._plain_variant(job(), set()) == "poster"
    assert deck._plain_variant(job(), {"poster"}) == "columns"
    assert deck._plain_variant(job(), {"poster", "columns"}) == "ledger"
    many = job()
    many.content["items"] = CONTENT["items"] + CONTENT["items"][:2]
    many.content["highlight"] = None
    assert deck._plain_variant(many, set()) == "ledger"


@needs_browser
@pytest.mark.parametrize("variant, count", [("ledger", 4), ("ledger", 6), ("poster", 3),
                                            ("poster", 4), ("columns", 2), ("columns", 3), ("columns", 4)])
def test_every_plain_page_fits_and_looks_finished(variant, count):
    the_job = job()
    the_job.content["items"] = (CONTENT["items"] + CONTENT["items"])[:count]

    async def measure(compact):
        theme = themes.get("studio")
        async with browser.Session() as session:
            body, rejected = sanitize.clean(deck._plain_html(the_job, variant))
            assert rejected == []
            if compact:
                body = body.replace('class="page', 'class="page compact', 1)
            return await session.measure(wrap(body, theme.css, office_font_css()[0]), screenshot=False)

    found = find_defects(asyncio.run(measure(False)), None, tier="standard")
    if any(defect.kind not in AESTHETIC for defect in found):  # as the deck does: the same page with less air
        found = find_defects(asyncio.run(measure(True)), None, tier="standard")
    assert [defect.message for defect in found] == []


class Store:
    def __init__(self):
        self.saved = {}

    def load(self, name):
        return self.saved.get(name)

    def save(self, name, value):
        self.saved[name] = json.loads(json.dumps(value))


class Pages:
    """Replies with the page registered for each page number, in order."""

    def __init__(self, pages):
        self.pages, self.prompts = {number: list(bodies) for number, bodies in pages.items()}, []

    async def complete_text(self, *, task, system, user, max_output_tokens=None, context=None, images=None):
        self.prompts.append(user)
        bodies = self.pages[context["job"].page_number]
        return bodies.pop(0) if len(bodies) > 1 else bodies[0]

    async def complete_json(self, *, task, system, user, max_output_tokens=None, context=None, images=None):
        return {}


@needs_browser
def test_saved_pages_are_rechecked_and_only_the_flawed_one_is_repaired():
    loud = page_html(5, '<div class="q1">管理层应将AI视为战略投入，优先配置资源到可规模化落地的业务场景。</div>'
                        '<div class="b">IDC预测，2024年全球人工智能产业规模将同比增长21.5%。</div>'
                        '<div class="t3">模型迭代活跃</div><div class="b">2024年以来新增或迭代近百个。</div>')
    store = Store()
    first = Pages({4: [fits(4)], 5: [loud]})
    asyncio.run(deck.design_deck(first, "studio", [job(4), job(5)], [], checkpoints=store, look=False))
    assert 'class="q1"' in store.saved["html/page_005.json"]["html"]

    second = Pages({4: [too_tall(4)], 5: [fits(5)]})
    results, summary = asyncio.run(
        deck.design_deck(second, "studio", [job(4), job(5)], [], checkpoints=store, look=False))
    assert len(second.prompts) == 1 and "你上一次写的 HTML" in second.prompts[0] and 'class="q1"' in second.prompts[0]
    assert results[4].ok and results[5].ok and summary["aesthetic_left"] == {}
    assert 'class="q1"' not in store.saved["html/page_005.json"]["html"]


@needs_browser
def test_a_signed_off_page_is_replayed_untouched_until_its_content_changes():
    loud = page_html(5, '<div class="q1">管理层应将AI视为战略投入，优先配置资源到可规模化落地的业务场景。</div>'
                        '<div class="b">IDC预测，2024年全球人工智能产业规模将同比增长21.5%。</div>'
                        '<div class="t3">模型迭代活跃</div><div class="b">2024年以来新增或迭代近百个。</div>')
    store = Store()
    asyncio.run(deck.design_deck(Pages({5: [loud]}), "studio", [job(5)], [], checkpoints=store, look=False))
    assert deck.accept(store, [5], note="P0") == [5]

    again = Pages({5: [fits(5)]})
    results, summary = asyncio.run(deck.design_deck(again, "studio", [job(5)], [], checkpoints=store, look=False))
    assert again.prompts == [] and results[5].ok and summary["accepted_pages"] == [5]
    assert 'class="q1"' in store.saved["html/page_005.json"]["html"]

    changed = job(5)
    changed.content["takeaway"] = "另一句结论。"
    results, summary = asyncio.run(deck.design_deck(again, "studio", [changed], [], checkpoints=store, look=False))
    assert len(again.prompts) >= 1 and summary["accepted_pages"] == []


def test_a_page_without_series_keeps_the_content_it_had():
    from ppt_agent.v2.visual.content import RichContent

    content = RichContent.model_validate({"title": TITLE, "items": CONTENT["items"]})
    made = deck.job_for(content, page_number=4, section_index=1, section_title="AI投入窗口已开启")
    assert "series" not in made.content and "figure" not in made.content
