from ppt_agent.v2.htmldeck import signature
from ppt_agent.v2.htmldeck.ledger import FactLedger, fact_keys, page_facts
from ppt_agent.v2.ir import BleedFrame, BoxItem, ChartItem, ChartSeries, Frame, PageDesign, Paint, RichTextItem, TextRun


def box(x, y, w, h):
    return BoxItem(id=f"b{x}-{y}-{w}", frame=BleedFrame(x=x, y=y, w=w, h=h), fill=Paint(hex="112233"))


def page(*elements):
    text = RichTextItem(id="t", frame=BleedFrame(x=0, y=0, w=10, h=10), runs=[TextRun(text="x")])
    return PageDesign(page_number=1, elements=[*elements, text])


def test_signature_ignores_detail_and_backdrops_and_snaps_to_a_grid():
    a = page(box(0, 0, 1280, 720), box(64, 180, 560, 460), box(660, 180, 556, 460), box(70, 190, 40, 20))
    b = page(box(60, 176, 566, 470), box(655, 185, 560, 455))
    assert signature.of(a) == signature.of(b) == "box:0-3x1-4|box:3-6x1-4"
    chart = ChartItem(id="c", frame=Frame(x=64, y=180, w=700, h=460), chart="bar",
                      categories=["a", "b"], series=[ChartSeries(name="s", values=[1, 2])])
    assert signature.of(page(chart)).startswith("chart:0-")
    assert signature.of(page()) == "text"


def test_repetition_is_measured_within_a_window_and_overall():
    report = signature.conflicts(["a", "b", "a", "c", "d", "e", "f", "a"] + ["z"] * 2, window=5, max_share=0.25)
    assert report["repeats_within_window"] == [2, 9]
    assert report["overused"] == {"a": 3} and report["largest_share"] == 0.3
    assert report["distinct"] == 7


def test_facts_keep_their_unit_and_skip_years_and_small_counts():
    assert fact_keys("2024 年规模 6,233 亿美元，同比 +21.5%，分 3 步，另有 95 %") == ["6233亿", "21.5%", "95%"]


def test_a_page_leads_with_its_title_and_headline_figure():
    lead, support = page_facts({"title": "采购自动化率超 85%", "source": "第 43 页",
                                "metrics": [{"value": "95%", "label": "现货率"}, {"value": "30天", "label": "周转"}],
                                "takeaway": "对比 2022 年的 70%"})
    assert lead == {"85%", "95%"} and support == {"70%"}


def test_the_ledger_reports_overuse():
    ledger = FactLedger()
    assert ledger.register(3, {"title": "现货率 95%"}) == []
    assert ledger.register(7, {"title": "现货率达到 95%", "items": [{"body": "成本降 70%"}]}) == ["95%"]
    for number in (8, 9, 10):
        ledger.register(number, {"title": "其他", "items": [{"body": "成本降 70%"}]})
    audit = ledger.audit()
    assert audit["led_more_than_once"] == {"95%": [3, 7]}
    assert audit["supported_more_than_twice"] == {"70%": [7, 8, 9, 10]}
    assert audit["most_used"] == {"fact": "70%", "pages": [7, 8, 9, 10]}


def test_capacity_matches_the_one_report_it_was_calibrated_on():
    from ppt_agent.v2.htmldeck.capacity import content_slides, suggested_slides

    assert content_slides(39744, 93) == 50  # sound at 20 pages, repetitive at 100
    assert suggested_slides(39744, 93, chapters=5) == 58
    assert content_slides(30000, 5) == 20  # a qualitative source is bounded by its text
    assert suggested_slides(900, 0) == 12  # never objects to a small deck


def test_materials_only_brief_objects_when_asked_for_more_than_the_source_carries(tmp_path):
    from ppt_agent.mcp_brief import ClarifyBrief, SourceSummary, apply_updates, evaluate

    source = SourceSummary(name="r.pdf", path=str(tmp_path / "r.pdf"), kind="document", readable=True,
                           sha256="0" * 64, pages=64, characters=39744, figures=93, chapters=["一"] * 5)

    def status(pages):
        brief = apply_updates(ClarifyBrief(), {name: {"value": value, "origin": "user"} for name, value in {
            "topic": "AI", "purpose": "汇报", "audience": "管理层", "slide_count": pages,
            "user_requirements": "讲清机会", "source_policy": "materials_only"}.items()}).brief
        return evaluate(brief, [source])

    assert status(50).status == "ready"
    over = status(100)
    assert over.status == "clarifying" and "58 页以内" in over.conflicts[0].message


def test_density_tiers_follow_the_planned_mix_and_need_a_figure_to_be_visual():
    from types import SimpleNamespace

    from ppt_agent.v2.typeset_pipeline import plan_tiers

    slots = [SimpleNamespace(page_number=n) for n in range(1, 41)]
    tiers = plan_tiers(slots, {n: True for n in range(1, 41)})
    counts = {tier: list(tiers.values()).count(tier) for tier in ("visual", "standard", "deep")}
    assert counts == {"visual": 8, "standard": 22, "deep": 10}
    assert "visual" not in plan_tiers(slots, {}).values()


def test_rich_content_checks_its_data_and_degrades_to_points():
    import pytest
    from pydantic import ValidationError

    from ppt_agent.v2.visual.content import PointsContent, RichContent

    page = RichContent(title="采购自动化率超 85%", relation="cases", tier="standard",
                       highlight={"value": "85%", "text": "采购自动化率"},
                       items=[{"heading": "京东物流", "value": "85%", "body": "采购自动化率"}])
    points = page.to_points()
    assert isinstance(points, PointsContent) and len(points.items) == 2
    assert points.items[0].body == "85% 采购自动化率"
    with pytest.raises(ValidationError):
        RichContent(title="t", items=[{"heading": "a"}], categories=["a", "b"], values=[1.0])
    with pytest.raises(ValidationError):
        RichContent(title="t", items=[{"heading": "a"}], headers=["a", "b"], rows=[["only one"]])


def test_an_unsupported_focal_figure_is_dropped_not_blanked():
    from ppt_agent.v2.evidence_check import check_content_numbers, sanitize_content_numbers
    from ppt_agent.v2.visual.content import RichContent

    page = RichContent(title="准确率很高", highlight={"value": "93%", "text": "准确率"},
                       items=[{"heading": "检测", "value": "90%", "body": "准确率超 90%"},
                              {"heading": "编造", "value": "77%", "body": "没有出处"}])
    issues = check_content_numbers(page, "TCL 面板缺陷检测准确率超 90%。")
    cleaned, _ = sanitize_content_numbers(page, issues)
    assert cleaned.highlight is None and [item.heading for item in cleaned.items] == ["检测"]


def _rich(relation, tier="standard", items=3, highlight=None, **extra):
    return {"archetype": "rich", "title": "t", "relation": relation, "tier": tier,
            "highlight": {"value": highlight, "text": "x"} if highlight else None,
            "items": [{"heading": f"h{n}", "date": extra.get("date")} for n in range(items)],
            **{key: value for key, value in extra.items() if key != "date"}}


def test_a_form_is_offered_only_when_the_content_can_fill_it():
    from ppt_agent.v2.htmldeck.layout_plan import candidates

    assert candidates(_rich("single", "visual", 2, "90%"))[:2] == ["poster", "units"]
    assert "units" not in candidates(_rich("single", "visual", 2, "316亿美元"))
    assert "chart" not in candidates(_rich("ranking"))
    ranked = [{"metric": "m", "unit": "%", "kind": "category", "points": [
        {"label": "a", "value": 1, "display": "1%", "ref": "r"}, {"label": "b", "value": 2, "display": "2%", "ref": "r"}]}]
    assert candidates(_rich("ranking", series=ranked))[0] == "chart"
    assert "chart" not in candidates(_rich("ranking", categories=["a", "b"], values=[1, 2]))  # unchecked numbers are not drawn
    assert candidates(_rich("timeline", items=4, date="2024年"))[0] == "timeline"
    assert candidates(_rich("timeline", items=4))[0] == "ledger"  # no dates, no axis
    assert candidates(_rich("structure", items=4))[0] == "matrix"
    assert candidates({"archetype": "summary"})[0] == "ledger"


def test_no_page_repeats_the_structure_of_any_earlier_page_too_soon():
    from ppt_agent.v2.htmldeck.layout_plan import SAME_FORM_GAP, SAME_STRUCTURE_GAP, audit, resolve

    relations = ["cases", "comparison", "single", "sequence", "structure", "cause", "trend", "cases"]
    contents = {page: _rich(relations[page % 8], items=4 if relations[page % 8] == "structure" else 3,
                            highlight="12.1%" if page % 3 == 0 else None)
                for page in range(1, 41)}
    lazy = {page: {"form": "ledger", "focus": "left"} for page in contents}  # a model that always says the same
    plan = resolve(lazy, contents)
    assert [item.page for item in plan] == list(range(1, 41))
    close_forms = close_structures = 0
    for index, item in enumerate(plan):
        for other in plan[:index]:
            gap = item.page - other.page
            # The firm guarantee: two pages in a row never share a form.
            assert not (other.form == item.form and gap == 1), (other, item)
            close_forms += other.form == item.form and gap <= SAME_FORM_GAP
            close_structures += other.structure == item.structure and gap <= SAME_STRUCTURE_GAP
    # The softer rules give way only when the content allows little else; it must stay rare.
    # One more close structure than before split was limited to two-way comparisons: a meaningless dark
    # half is worse than a structure seen again a few pages later.
    assert close_forms <= 3 and close_structures <= 6, (close_forms, close_structures)
    report = audit(plan)
    assert report["forms_used"] >= 7 and report["largest_form_share"] <= 0.25
    assert report["distinct_structures"] >= 15  # split now takes only two-way comparisons (see above)
