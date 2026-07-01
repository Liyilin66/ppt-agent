from pathlib import Path

from ppt_agent.models import Deck
from ppt_agent.ppt_master_adapter import export_deck_ir_to_ppt_master_markdown


def _text_element(element_id: str, text: str) -> dict:
    return {
        "element_id": element_id,
        "type": "text",
        "bbox": {"x": 0.5, "y": 0.5, "width": 4.5, "height": 0.6},
        "text": text,
    }


def _deck_payload() -> dict:
    return {
        "deck_id": "adapter_test_deck",
        "title": "AI 产品经理如何设计 Agent 产品",
        "theme_name": "clean_business",
        "canvas_width_in": 13.333,
        "canvas_height_in": 7.5,
        "slides": [
            {
                "slide_id": "slide_001",
                "title": "Agent 产品不是聊天窗口",
                "layout": "title",
                "elements": [
                    _text_element("s1_title", "Agent 产品不是聊天窗口"),
                    _text_element("s1_body", "Risk：instruction leakage\n判断点 1：用户任务要先被拆成可执行工作流"),
                ],
            },
            {
                "slide_id": "slide_002",
                "title": "方案比较",
                "layout": "comparison_matrix",
                "elements": [
                    _text_element("s2_heading", "方案 A：只做聊天助手"),
                    _text_element("s2_body", "Option B: 把目标、工具、状态和验收标准都产品化"),
                ],
            },
            {
                "slide_id": "slide_003",
                "title": "落地流程",
                "layout": "process_flow",
                "elements": [
                    _text_element("s3_heading", "Impact：从场景访谈到任务编排"),
                    _text_element("s3_body", "Mitigation：用评估指标和灰度发布控制质量"),
                ],
            },
            {
                "slide_id": "slide_004",
                "title": "质量指标",
                "layout": "metric_cards",
                "elements": [
                    _text_element("s4_heading", "判断点 3"),
                    _text_element("s4_body", "任务完成率、人工接管率、用户修正次数要一起看"),
                    _text_element("s4_instruction", "把这一点转化为明确的下一步行动"),
                    _text_element("s4_policy", "先列出 Agent 不允许自动执行的动作"),
                    _text_element("s4_schema", "slide_id / element_id / bbox should never leak"),
                ],
            },
        ],
    }


def test_export_deck_ir_to_ppt_master_markdown_writes_expected_structure(tmp_path: Path) -> None:
    output_path = tmp_path / "ppt_master_source.md"

    exported_path = export_deck_ir_to_ppt_master_markdown(
        _deck_payload(),
        output_path,
        style_notes="更像技术产品分享，不要营销口吻",
    )

    markdown = output_path.read_text(encoding="utf-8")

    assert exported_path == output_path
    assert output_path.exists()
    assert "# Presentation Request" in markdown
    assert "## Topic" in markdown
    assert "## Audience" in markdown
    assert "## Style Direction" in markdown
    assert "## Slide-by-slide Outline" in markdown
    assert "更像技术产品分享" in markdown


def test_export_deck_ir_to_ppt_master_markdown_emits_slide_headings(tmp_path: Path) -> None:
    output_path = tmp_path / "ppt_master_source.md"

    export_deck_ir_to_ppt_master_markdown(_deck_payload(), output_path)
    markdown = output_path.read_text(encoding="utf-8")

    for slide_number in range(1, 5):
        assert f"### Slide {slide_number}:" in markdown
    assert "Key message:" in markdown
    assert "Suggested content:" in markdown
    assert "Visual direction:" in markdown


def test_export_deck_ir_to_ppt_master_markdown_hides_ir_fields(tmp_path: Path) -> None:
    output_path = tmp_path / "ppt_master_source.md"

    export_deck_ir_to_ppt_master_markdown(_deck_payload(), output_path)
    markdown = output_path.read_text(encoding="utf-8")

    assert "bbox" not in markdown
    assert "element_id" not in markdown
    assert "slide_id" not in markdown


def test_export_deck_ir_to_ppt_master_markdown_sanitizes_bad_content(tmp_path: Path) -> None:
    output_path = tmp_path / "ppt_master_source.md"
    original_payload = _deck_payload()

    export_deck_ir_to_ppt_master_markdown(original_payload, output_path)
    markdown = output_path.read_text(encoding="utf-8")
    lowered = markdown.lower()

    assert "instruction leakage" not in lowered
    assert "risk：" not in lowered
    assert "risk:" not in lowered
    assert "impact：" not in lowered
    assert "impact:" not in lowered
    assert "mitigation：" not in lowered
    assert "mitigation:" not in lowered
    assert "判断点 1" not in markdown
    assert "判断点 2" not in markdown
    assert "判断点 3" not in markdown
    assert "方案 A" not in markdown
    assert "方案 B" not in markdown
    assert "Option A" not in markdown
    assert "Option B" not in markdown
    assert "把这一点转化为明确的下一步行动" not in markdown
    assert "先列出 Agent 不允许自动执行的动作" not in markdown
    assert "bbox" not in markdown
    assert "element_id" not in markdown
    assert "slide_id" not in markdown
    assert "\n- \n" not in markdown
    assert "Risk：instruction leakage" in original_payload["slides"][0]["elements"][1]["text"]


def test_export_deck_ir_to_ppt_master_markdown_accepts_deck_model(tmp_path: Path) -> None:
    deck = Deck.model_validate(_deck_payload())
    output_path = tmp_path / "ppt_master_source.md"

    export_deck_ir_to_ppt_master_markdown(deck, output_path)
    markdown = output_path.read_text(encoding="utf-8")

    assert "AI 产品经理如何设计 Agent 产品" in markdown
    assert "Use a side-by-side comparison grid" in markdown
    assert "Use a left-to-right flow" in markdown
    assert "Use compact metric tiles" in markdown
