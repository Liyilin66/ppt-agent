import pytest

from ppt_agent.mcp_brief import (
    ClarifyBrief, apply_updates, brief_hash, evaluate, summarize_sources, to_job_request,
)


def user(value):
    return {"value": value, "origin": "user"}


def filled(**fields):
    return apply_updates(ClarifyBrief(), fields).brief


def two_section_md(tmp_path):
    path = tmp_path / "季度经营简报.md"
    path.write_text("# 收入\n三季度收入 1.2 亿元，同比增长 18%。\n\n# 成本\n毛利率 41%，较上季度提升 2 个百分点。\n",
                    encoding="utf-8")
    return path


DETAILED = dict(
    purpose=user("向管理层汇报三季度经营情况并争取四季度预算"),
    audience=user("公司管理层"),
    topic=user("三季度经营复盘"),
    slide_count=user(6),
    source_policy=user("materials_only"),
    content_focus=user(["收入", "成本"]),
)


def test_one_sentence_request_asks_purpose_first():
    result = evaluate(filled(topic=user("做一个关于 AI Agent 的 PPT")))
    assert result.status == "clarifying"
    assert result.missing == ["purpose", "audience", "slide_count", "source_policy"]
    assert result.next_question.field in ("purpose", "audience")
    assert result.next_question.kind == "missing"


def test_detailed_request_with_small_source_is_ready_at_once(tmp_path):
    sources = summarize_sources([two_section_md(tmp_path)])
    assert sources[0].readable and sources[0].pages == 2 and sources[0].page_kind == "logical"
    assert sources[0].chapters == ["收入", "成本"]
    result = evaluate(filled(**DETAILED), sources)
    assert result.status == "ready"
    assert result.missing == [] and result.conflicts == [] and result.next_question is None


def test_thin_source_without_web_conflicts_until_web_is_allowed(tmp_path):
    sources = summarize_sources([two_section_md(tmp_path)])
    brief = filled(**{**DETAILED, "slide_count": user(40)})
    result = evaluate(brief, sources)
    assert result.status == "clarifying"
    assert [c.field for c in result.conflicts] == ["slide_count"]
    assert result.next_question.kind == "conflict"
    with_web = apply_updates(brief, {"source_policy": user("materials_and_web")}).brief
    assert evaluate(with_web, sources).status == "ready"


def test_delegated_purpose_counts_as_settled():
    brief = filled(**{**DETAILED, "source_policy": user("web_only"),
                      "purpose": {"value": "让新同事快速了解业务全貌", "origin": "delegated"}})
    result = evaluate(brief)
    assert "purpose" not in result.missing
    assert result.status == "ready"


def test_source_value_does_not_override_user_value():
    brief = filled(topic=user("三季度经营复盘"))
    result = apply_updates(brief, {"topic": {"value": "公司年报摘要", "origin": "source"},
                                   "content_focus": {"value": ["现金流"], "origin": "source"}})
    assert result.brief.values.topic == "三季度经营复盘"
    assert result.brief.origin("topic") == "user"
    assert [s.field for s in result.skipped] == ["topic"]
    assert result.brief.values.content_focus == ["现金流"]
    assert result.brief.origin("content_focus") == "source"


def test_source_origin_is_limited_and_bad_updates_change_nothing():
    brief = filled(topic=user("三季度经营复盘"))
    with pytest.raises(ValueError, match="audience"):
        apply_updates(brief, {"audience": {"value": "管理层", "origin": "source"}})
    with pytest.raises(ValueError, match="来源"):
        apply_updates(brief, {"purpose": {"value": "汇报", "origin": "default"}})
    with pytest.raises(ValueError):
        apply_updates(brief, {"purpose": user("汇报"), "slide_count": user(0)})
    assert brief.values.purpose is None


def test_hash_changes_with_required_fields():
    brief = filled(**DETAILED)
    changed = apply_updates(brief, {"slide_count": user(8)}).brief
    assert brief_hash(brief) != brief_hash(changed)
    assert evaluate(brief).brief_hash == brief_hash(brief)
    assert brief_hash(filled(**DETAILED)) == brief_hash(brief)


def test_unreadable_source_is_not_treated_as_read(tmp_path):
    missing = tmp_path / "不存在.pdf"
    sources = summarize_sources([missing])
    assert sources[0].readable is False and sources[0].warnings
    result = evaluate(filled(**DETAILED), sources)
    assert result.status == "clarifying"
    assert {c.field for c in result.conflicts} == {"sources", "source_policy"}


def test_job_request_states_audience_and_delegated_choices():
    brief = filled(**{**DETAILED, "audience": {"value": "公司管理层", "origin": "delegated"},
                      "constraints": user(["不出现客户名称"])})
    request = to_job_request(brief, attachment_ids=["a" * 32])
    assert request.audience == "公司管理层" != "未指定"
    assert request.slide_count == 6 and request.deck_type == "visual_design_v2"
    assert request.enable_search is False and request.attachment_ids == ["a" * 32]
    text = request.user_requirements
    assert text.startswith("目的：向管理层汇报三季度经营情况并争取四季度预算。")
    assert "重点内容：收入、成本。" in text and "限制条件：不出现客户名称。" in text
    assert "以下各项由 Agent 代为决定：受众=公司管理层。" in text


def test_job_request_refuses_an_unsettled_brief():
    with pytest.raises(ValueError, match="目的"):
        to_job_request(filled(topic=user("AI Agent")))


def test_hash_covers_the_exact_source_files(tmp_path):
    brief = filled(**DETAILED)
    first = summarize_sources([two_section_md(tmp_path)])
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = other_dir / "季度经营简报.md"
    other.write_text("# 收入\n三季度收入 1.3 亿元。\n\n# 成本\n毛利率 39%。\n", encoding="utf-8")
    picture = tmp_path / "趋势图.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    assert brief_hash(brief, first) == brief_hash(brief, summarize_sources([two_section_md(tmp_path)]))
    assert brief_hash(brief, first) != brief_hash(brief, summarize_sources([other]))
    assert brief_hash(brief, first) != brief_hash(brief, first + summarize_sources([picture]))
    assert brief_hash(brief, first) != brief_hash(brief)
    assert evaluate(brief, first).brief_hash == brief_hash(brief, first)


def test_image_is_a_usable_asset_not_a_document(tmp_path):
    picture = tmp_path / "趋势图.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    sources = summarize_sources([two_section_md(tmp_path), picture])
    image = sources[1]
    assert (image.kind, image.readable, image.name, image.pages) == ("image", True, "趋势图.png", 0)
    assert image.sha256 and not image.warnings
    result = evaluate(filled(**DETAILED), sources)
    assert result.status == "ready" and result.conflicts == []
