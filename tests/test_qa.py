import json
from pathlib import Path

import pytest

from ppt_agent.long_deck_quality import evaluate_long_deck_quality_gate
from ppt_agent.load import load_deck, load_theme
from ppt_agent.models import Deck
from ppt_agent.qa import QAReport, analyze_deck


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _sample_deck_payload() -> dict:
    return json.loads((EXAMPLES_DIR / "sample_slide_ir.json").read_text(encoding="utf-8"))


def _issue_codes(report: QAReport) -> list[str]:
    return [issue.code for issue in report.issues]


def _deck_with_layouts(layouts: list[str], titles: list[str] | None = None) -> Deck:
    slide_titles = titles or [f"Slide {index}" for index in range(1, len(layouts) + 1)]
    return Deck.model_validate(
        {
            "deck_id": "qa_layout_test_deck",
            "title": "QA Layout Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": f"slide_{index:03d}",
                    "title": slide_titles[index - 1],
                    "layout": layout,
                    "elements": [
                        {
                            "element_id": f"s{index:03d}_title",
                            "type": "text",
                            "bbox": {
                                "x": 0.8,
                                "y": 1.0,
                                "width": 7.2,
                                "height": 1.2,
                            },
                            "text": slide_titles[index - 1],
                        }
                    ],
                }
                for index, layout in enumerate(layouts, start=1)
            ],
        }
    )


def _deck_with_text_slide(layout: str, title: str, body_texts: list[str]) -> Deck:
    elements = [
        {
            "element_id": "title",
            "type": "text",
            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
            "text": title,
        }
    ]
    for index, text in enumerate(body_texts, start=1):
        elements.append(
            {
                "element_id": f"body_{index}",
                "type": "text",
                "bbox": {"x": 0.8, "y": 1.0 + index * 0.85, "width": 7.2, "height": 0.6},
                "text": text,
            }
        )

    return Deck.model_validate(
        {
            "deck_id": "qa_content_style_test_deck",
            "title": "QA Content Style Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": title,
                    "layout": layout,
                    "elements": elements,
                }
            ],
        }
    )


def test_sample_deck_generates_qa_report() -> None:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    theme = load_theme(EXAMPLES_DIR / "theme.json")

    report = analyze_deck(deck, theme)

    assert report.deck_id == deck.deck_id
    assert 0 <= report.score <= 100
    assert isinstance(report.issues, list)
    assert "layout_diversity_low" not in _issue_codes(report)


def test_analyze_deck_warns_on_obvious_bbox_overlap() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"][1]["bbox"] = {
        "x": 1.0,
        "y": 1.25,
        "width": 4.0,
        "height": 0.7,
    }
    deck = Deck.model_validate(payload)

    report = analyze_deck(deck)

    overlap_issues = [issue for issue in report.issues if issue.code == "BBOX_OVERLAP"]
    assert overlap_issues
    assert overlap_issues[0].severity == "warning"
    assert "s1_title" in overlap_issues[0].message
    assert "s1_subtitle" in overlap_issues[0].message


def test_analyze_deck_marks_very_sparse_slide_as_info() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"] = [
        {
            "element_id": "tiny_note",
            "type": "text",
            "bbox": {
                "x": 0.5,
                "y": 0.5,
                "width": 1.0,
                "height": 0.5,
            },
            "text": "Hi",
        }
    ]
    deck = Deck.model_validate(payload)

    report = analyze_deck(deck)

    empty_issues = [issue for issue in report.issues if issue.code == "SLIDE_TOO_EMPTY"]
    assert empty_issues
    assert empty_issues[0].severity == "info"
    assert empty_issues[0].slide_id == "slide_001"


def test_analyze_deck_warns_on_text_too_long_for_bbox() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"] = [
        {
            "element_id": "long_text",
            "type": "text",
            "bbox": {
                "x": 0.5,
                "y": 0.5,
                "width": 1.2,
                "height": 1.0,
            },
            "text": "This is a very long sentence for a tiny text box. " * 8,
        }
    ]
    deck = Deck.model_validate(payload)

    report = analyze_deck(deck)

    text_issues = [issue for issue in report.issues if issue.code == "TEXT_TOO_LONG"]
    assert text_issues
    assert text_issues[0].severity == "warning"
    assert text_issues[0].element_id == "long_text"


def test_analyze_deck_marks_very_dense_slide_as_warning() -> None:
    payload = _sample_deck_payload()
    payload["slides"][0]["elements"] = [
        {
            "element_id": "large_shape",
            "type": "shape",
            "bbox": {
                "x": 0.1,
                "y": 0.1,
                "width": 12.8,
                "height": 6.2,
            },
            "shape": "rectangle",
            "style": {
                "fill_color": "#F9FAFB"
            },
        }
    ]
    deck = Deck.model_validate(payload)

    report = analyze_deck(deck)

    assert "SLIDE_TOO_DENSE" in _issue_codes(report)


def test_analyze_deck_warns_when_long_deck_layout_diversity_is_low() -> None:
    deck = _deck_with_layouts(
        [
            "title_slide",
            "two_column",
            "two_column",
            "two_column",
            "two_column",
            "two_column",
            "two_column",
            "closing_slide",
        ],
        [
            "Opening",
            "Market context",
            "User needs",
            "Workflow design",
            "Evaluation metrics",
            "Launch risks",
            "Roadmap",
            "Next steps",
        ],
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "layout_diversity_low"]
    assert issues
    assert issues[0].severity == "warning"
    assert "two_column" in issues[0].message
    assert "at least three content layouts" in issues[0].message
    assert report.score < 100


def test_analyze_deck_warns_on_three_consecutive_content_layouts() -> None:
    deck = _deck_with_layouts(
        ["title_slide", "four_cards", "four_cards", "four_cards", "closing_slide"],
        ["Opening", "Capability map", "Risk map", "Action map", "Close"],
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "layout_repetition_run"]
    assert issues
    assert issues[0].severity == "warning"
    assert "four_cards" in issues[0].message
    assert "consecutive content slides" in issues[0].message


def test_analyze_deck_warns_on_visual_pattern_repetition() -> None:
    deck = _deck_with_layouts(
        ["title_slide", "two_column", "three_column", "metric_cards", "risk_matrix", "closing_slide"],
        ["Opening", "Context", "Framework", "Metrics", "Risk", "Close"],
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "visual_pattern_repetition"]
    assert issues
    assert issues[0].severity == "warning"
    assert "card_grid" in issues[0].message
    assert report.score >= 60


def test_analyze_deck_warns_when_card_grid_pattern_dominates_long_deck() -> None:
    deck = _deck_with_layouts(
        [
            "title_slide",
            "two_column",
            "three_column",
            "four_cards",
            "metric_cards",
            "two_column",
            "four_cards",
            "closing_slide",
        ],
        ["Opening", "One", "Two", "Three", "Four", "Five", "Six", "Close"],
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "visual_pattern_repetition"]
    assert issues
    assert any("card-grid visual patterns on 6 slides" in issue.message for issue in issues)
    assert all(issue.severity == "warning" for issue in issues)
    assert report.score >= 60


def test_analyze_deck_warns_on_adjacent_similar_titles() -> None:
    deck = _deck_with_layouts(
        ["title_slide", "two_column", "three_column", "closing_slide"],
        ["AI 学习路线", "AI 学习效率提升", "AI 学习效率提升方法", "下一步"],
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "adjacent_title_similarity"]
    assert issues
    assert issues[0].severity == "warning"
    assert "AI 学习效率提升" in issues[0].message


def test_analyze_deck_warns_on_layout_contract_capacity_violation() -> None:
    deck = Deck.model_validate(
        {
            "deck_id": "qa_layout_contract_test_deck",
            "title": "QA Layout Contract Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Too many comparison items",
                    "layout": "two_column",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
                            "text": "Too many comparison items",
                        },
                        {
                            "element_id": "item_1",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.4, "width": 3.0, "height": 0.5},
                            "text": "First item",
                        },
                        {
                            "element_id": "item_2",
                            "type": "text",
                            "bbox": {"x": 4.2, "y": 1.4, "width": 3.0, "height": 0.5},
                            "text": "Second item",
                        },
                        {
                            "element_id": "item_3",
                            "type": "text",
                            "bbox": {"x": 7.6, "y": 1.4, "width": 3.0, "height": 0.5},
                            "text": "Third item",
                        },
                    ],
                }
            ],
        }
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "layout_contract_violation"]
    assert issues
    assert issues[0].severity == "warning"
    assert issues[0].slide_id == "slide_001"
    assert "layout 'two_column'" in issues[0].message
    assert "estimated_items=3" in issues[0].message
    assert "max_items=2" in issues[0].message


def test_analyze_deck_warns_on_new_layout_capacity_violation() -> None:
    elements = [
        {
            "element_id": "title",
            "type": "text",
            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
            "text": "Too many process steps",
        }
    ]
    for index in range(1, 7):
        elements.append(
            {
                "element_id": f"step_{index}",
                "type": "text",
                "bbox": {"x": 0.8, "y": 0.7 + index * 0.6, "width": 4.0, "height": 0.4},
                "text": f"Step {index}\nDo the work",
            }
        )
    deck = Deck.model_validate(
        {
            "deck_id": "qa_new_layout_contract_test_deck",
            "title": "QA New Layout Contract Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Too many process steps",
                    "layout": "process_flow",
                    "elements": elements,
                }
            ],
        }
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "layout_contract_violation"]
    assert issues
    assert issues[0].slide_id == "slide_001"
    assert "layout 'process_flow'" in issues[0].message
    assert "estimated_items=6" in issues[0].message
    assert "max_items=5" in issues[0].message


def test_visual_preflight_warns_on_low_density_content_slide() -> None:
    deck = Deck.model_validate(
        {
            "deck_id": "qa_low_density_visual_test_deck",
            "title": "QA Low Density Visual Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Sparse Comparison",
                    "layout": "comparison_matrix",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
                            "text": "Sparse Comparison",
                        },
                        {
                            "element_id": "body",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 5.0, "height": 0.5},
                            "text": "Too little",
                        },
                    ],
                }
            ],
        }
    )

    report = analyze_deck(deck)

    assert "visual_density_too_low" in _issue_codes(report)


def test_visual_preflight_warns_on_high_density_slide() -> None:
    elements = [
        {
            "element_id": "title",
            "type": "text",
            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
            "text": "Dense Process",
        }
    ]
    for index in range(1, 8):
        elements.append(
            {
                "element_id": f"body_{index}",
                "type": "text",
                "bbox": {"x": 0.8, "y": 0.8 + index * 0.35, "width": 6.0, "height": 0.3},
                "text": "- first bullet\n- second bullet\n- third bullet",
            }
        )
    deck = Deck.model_validate(
        {
            "deck_id": "qa_high_density_visual_test_deck",
            "title": "QA High Density Visual Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Dense Process",
                    "layout": "process_flow",
                    "elements": elements,
                }
            ],
        }
    )

    report = analyze_deck(deck)

    assert "visual_density_too_high" in _issue_codes(report)


def test_visual_preflight_warns_on_text_overflow_risk() -> None:
    deck = Deck.model_validate(
        {
            "deck_id": "qa_text_overflow_visual_test_deck",
            "title": "QA Text Overflow Visual Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Risk Matrix",
                    "layout": "risk_matrix",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
                            "text": "Risk Matrix",
                        },
                        {
                            "element_id": "risk_1",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 8.0, "height": 0.5},
                            "text": (
                                "模型输出可能在复杂场景中持续产生不可靠结论并误导用户判断，"
                                "需要额外校验、人工复核、权限控制、日志追踪和异常回退机制共同保障，"
                                "否则会在课堂展示、业务汇报和真实产品决策中造成连续误导。"
                            ),
                        },
                    ],
                }
            ],
        }
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "text_overflow_risk"]
    assert issues
    assert issues[0].element_id == "risk_1"


def test_visual_preflight_warns_on_title_wrapping_risk() -> None:
    deck = Deck.model_validate(
        {
            "deck_id": "qa_title_wrap_visual_test_deck",
            "title": "QA Title Wrap Visual Test Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "AI 产品经理如何在复杂组织场景中设计可验证可治理的 Agent 工作流体验",
                    "layout": "title_slide",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
                            "text": "AI 产品经理如何在复杂组织场景中设计可验证可治理的 Agent 工作流体验",
                        },
                        {
                            "element_id": "subtitle",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 8.0, "height": 0.5},
                            "text": "课程汇报",
                        },
                    ],
                }
            ],
        }
    )

    report = analyze_deck(deck)

    assert "title_wrapping_risk" in _issue_codes(report)
    assert "slide_title_too_long" in _issue_codes(report)


def test_content_style_warns_on_slide_text_too_dense() -> None:
    dense_body = (
        "Agent 产品经理需要同时说明技术边界、用户需求、任务拆解、工具调用、"
        "权限控制、上下文限制、评估指标、错误成本、人工接管、灰度发布、"
        "治理机制和落地风险。" * 3
    )
    deck = _deck_with_text_slide("three_column", "密度过高", [dense_body])

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "slide_text_too_dense"]

    assert issues
    assert issues[0].severity == "warning"
    assert "slide_total_text_too_dense" in _issue_codes(report)
    assert report.score >= 60


def test_text_density_guard_warns_on_single_text_element_too_long() -> None:
    deck = _deck_with_text_slide(
        "two_column",
        "边界判断",
        [
            (
                "Agent 产品经理需要把模型不确定性、工具调用可靠性、权限范围、上下文限制、"
                "失败回退、人工接管、日志审计、指标复盘和上线门槛全部解释清楚，"
                "否则一页会像长文档而不是可讲的 PPT。"
            )
        ],
    )

    report = analyze_deck(deck)

    assert "text_element_too_long" in _issue_codes(report)
    assert report.score >= 60


def test_content_style_warns_on_card_body_too_long() -> None:
    deck = _deck_with_text_slide(
        "four_cards",
        "边界判断",
        [
            (
                "先定边界\n"
                "这张卡片把模型能力、工具调用、权限控制、失败回退和人工接管都写进一句话里，"
                "演讲时会变成报告摘要。"
            )
        ],
    )

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "card_body_too_long"]

    assert issues
    assert issues[0].severity == "warning"
    assert issues[0].element_id == "body_1"


def test_qa_warns_on_card_content_imbalance() -> None:
    deck = _deck_with_text_slide(
        "four_cards",
        "边界判断",
        [
            (
                "边界清单\n"
                "把模型能力、工具调用、权限控制、失败回退、人工接管、日志审计和上线指标都塞进一张卡，"
                "会让其他卡片空着，演讲时也没有清晰层次。"
            ),
            "试点",
            "回退",
            "复盘",
        ],
    )

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "card_content_imbalance"]

    assert issues
    assert issues[0].severity == "warning"
    assert issues[0].element_id == "body_1"


def test_content_style_warns_on_paragraph_like_slide() -> None:
    deck = _deck_with_text_slide(
        "two_column",
        "需求判断",
        [
            (
                "产品经理需要理解用户任务背后的真实目标，并且把模糊需求转化为可验证的输入、"
                "执行、校验和交付闭环，否则 Agent 很容易只是在界面上包装一层自动化能力。"
            )
        ],
    )

    report = analyze_deck(deck)

    assert "paragraph_like_slide" in _issue_codes(report)


def test_content_style_warns_on_long_enumeration() -> None:
    deck = _deck_with_text_slide(
        "two_column",
        "技术边界",
        ["技术边界包括模型能力、工具调用、权限控制、上下文限制、失败回退、人工接管、日志审计。"],
    )

    report = analyze_deck(deck)

    assert "long_enumeration" in _issue_codes(report)


def test_content_style_warns_on_weak_slide_message() -> None:
    deck = _deck_with_text_slide("two_column", "概述", ["介绍 Agent 产品经理需要关注的内容。"])

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "weak_slide_message"]

    assert issues
    assert issues[0].severity == "warning"
    assert "specific judgment" in issues[0].message


def test_anti_generic_qa_warns_on_generic_content() -> None:
    deck = _deck_with_text_slide(
        "three_column",
        "治理原则",
        ["提升效率、降低风险、前置治理、建立闭环。"],
    )

    report = analyze_deck(deck)

    assert "generic_content" in _issue_codes(report)
    assert report.score > 0


def test_anti_generic_qa_warns_on_missing_product_judgment() -> None:
    deck = _deck_with_text_slide(
        "comparison_matrix",
        "责任边界",
        ["技术边界\n产品经理判断\n评估指标"],
    )

    report = analyze_deck(deck)

    assert "missing_product_judgment" in _issue_codes(report)
    assert report.score > 0


def test_anti_generic_qa_warns_on_vague_action() -> None:
    deck = _deck_with_text_slide(
        "closing_slide",
        "下一步",
        ["关注风险", "理解边界", "提升能力"],
    )

    report = analyze_deck(deck)

    assert "vague_action" in _issue_codes(report)
    assert report.score > 0


def test_anti_generic_qa_warns_on_prompt_keyword_repetition() -> None:
    deck = _deck_with_text_slide(
        "four_cards",
        "核心框架",
        ["技术边界、用户需求分析、工作流设计、评估指标、落地风险。"],
    )

    report = analyze_deck(deck)

    assert "prompt_keyword_repetition" in _issue_codes(report)
    assert report.score > 0


def test_anti_generic_qa_warns_on_weak_takeaway() -> None:
    deck = _deck_with_text_slide(
        "key_takeaway",
        "总结",
        ["综合来看，要理解技术边界、工作流设计和落地风险。"],
    )

    report = analyze_deck(deck)

    assert "weak_takeaway" in _issue_codes(report)
    assert report.score > 0


def test_qa_warns_on_instruction_leakage() -> None:
    deck = _deck_with_text_slide(
        "closing_slide",
        "下一步",
        ["把这一点转化为明确的下一步行动。"],
    )

    report = analyze_deck(deck)

    assert "instruction_leakage" in _issue_codes(report)
    assert any(issue.code == "instruction_leakage" and issue.severity == "error" for issue in report.issues)
    assert report.score > 0


def test_qa_detects_new_instruction_leakage_phrase_as_error() -> None:
    deck = _deck_with_text_slide(
        "closing_slide",
        "下一步",
        ["先列出 Agent 不允许自动执行的动作"],
    )

    report = analyze_deck(deck)

    issues = [issue for issue in report.issues if issue.code == "instruction_leakage"]
    assert issues
    assert all(issue.severity == "error" for issue in issues)


def test_qa_detects_risk_matrix_placeholders_as_errors() -> None:
    deck = _deck_with_text_slide(
        "risk_matrix",
        "风险矩阵",
        ["risk\nimpact\nmitigation"],
    )

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "risk_matrix_placeholder"]

    assert issues
    assert issues[0].severity == "error"
    assert report.score > 0


def test_qa_detects_uppercase_risk_matrix_placeholders_as_errors() -> None:
    deck = _deck_with_text_slide(
        "risk_matrix",
        "风险矩阵",
        ["Risk\nImpact\nMitigation"],
    )

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "risk_matrix_placeholder"]

    assert issues
    assert issues[0].severity == "error"


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("risk：权限越界", "risk"),
        ("impact：误操作影响用户数据", "impact"),
        ("mitigation：设置人工确认", "mitigation"),
        ("Risk: tool execution exceeds permission", "risk"),
    ],
)
def test_long_deck_quality_gate_detects_risk_label_prefix_leakage(text: str, label: str) -> None:
    deck = _deck_with_text_slide("risk_matrix", "风险治理", [text])

    report = evaluate_long_deck_quality_gate(deck)

    assert report.status == "failed_quality_gate"
    assert report.issues
    assert "risk_label_prefix_leakage" in report.blocked_codes
    assert "slide_001" in report.blocked_slide_ids
    assert "body_1" in report.blocked_element_ids
    assert any(label in issue.message for issue in report.issues)


def test_long_deck_quality_gate_does_not_flag_mid_sentence_risk_mitigation() -> None:
    deck = _deck_with_text_slide(
        "two_column",
        "Risk wording in natural language",
        ["The launch plan includes a risk mitigation strategy for provider timeout."],
    )

    report = evaluate_long_deck_quality_gate(deck)

    assert report.status == "passed"
    assert report.issues == []
    assert report.blocked_codes == []
    assert report.score == 100


def test_qa_detects_comparison_matrix_placeholders_as_errors() -> None:
    deck = _deck_with_text_slide(
        "comparison_matrix",
        "责任对比",
        ["方案 A\n输入输出", "方案 B\n状态管理"],
    )

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "comparison_matrix_placeholder"]

    assert issues
    assert all(issue.severity == "error" for issue in issues)
    assert report.score > 0


def test_qa_detects_comparison_matrix_judgment_placeholders_as_errors() -> None:
    deck = _deck_with_text_slide(
        "comparison_matrix",
        "责任对比",
        ["基准侧\n判断点 1\n判断点 2", "Agent 侧\n判断点 3"],
    )

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "comparison_matrix_placeholder"]

    assert issues
    assert all(issue.severity == "error" for issue in issues)


def test_qa_warns_on_risk_matrix_malformed_row() -> None:
    deck = Deck.model_validate(
        {
            "deck_id": "qa_risk_matrix_malformed_deck",
            "title": "QA Risk Matrix Malformed Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Risk Matrix",
                    "layout": "risk_matrix",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
                            "text": "Risk Matrix",
                        },
                        {
                            "element_id": "risk_1",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 8.0, "height": 0.8},
                            "text": "AI Agent 的落地风险要在设计阶段被约束\n影响大\n加强治理",
                        },
                    ],
                }
            ],
        }
    )

    report = analyze_deck(deck)

    assert "risk_matrix_malformed_row" in _issue_codes(report)
    assert report.score > 0


def test_qa_warns_on_card_body_contains_subheadings() -> None:
    deck = _deck_with_text_slide(
        "four_cards",
        "边界设计",
        ["工具与权限 / 接管与追溯 / 落地风险"],
    )

    report = analyze_deck(deck)

    assert "card_body_contains_subheadings" in _issue_codes(report)
    assert report.score > 0


def test_qa_warns_on_metric_explanation_contains_risk_governance() -> None:
    deck = _deck_with_text_slide(
        "metric_cards",
        "指标判断",
        ["越权操作、模型幻觉、不可追溯、用户过度信任不能被指标掩盖。"],
    )

    report = analyze_deck(deck)

    assert "metric_explanation_contains_risk_governance" in _issue_codes(report)
    assert report.score > 0


def test_qa_warns_on_closing_action_not_executable() -> None:
    deck = _deck_with_text_slide(
        "closing_slide",
        "下一步",
        ["明确下一步行动。"],
    )

    report = analyze_deck(deck)

    assert "closing_action_not_executable" in _issue_codes(report)
    assert report.score > 0


def test_content_style_warnings_do_not_zero_qa_score() -> None:
    deck = _deck_with_text_slide(
        "four_cards",
        "概述",
        [
            (
                "技术边界包括模型能力、工具调用、权限控制、上下文限制、失败回退、人工接管、"
                "日志审计、评估指标，而且这些内容需要在同一页中完整说明。"
            )
        ],
    )

    report = analyze_deck(deck)
    content_codes = {
        "slide_text_too_dense",
        "card_body_too_long",
        "paragraph_like_slide",
        "long_enumeration",
        "weak_slide_message",
    }

    assert content_codes & set(_issue_codes(report))
    assert all(issue.severity != "error" for issue in report.issues)
    assert report.score >= 60


def test_warning_only_issues_do_not_zero_qa_score() -> None:
    layouts = ["title_slide", *["four_cards"] * 6, "closing_slide"]
    deck = _deck_with_layouts(layouts, titles=["同一主题"] * len(layouts))

    report = analyze_deck(deck)

    assert report.issues
    assert all(issue.severity != "error" for issue in report.issues)
    assert report.score >= 60


def test_risk_matrix_missing_mitigation_warns_without_hard_failure() -> None:
    deck = Deck.model_validate(
        {
            "deck_id": "qa_missing_mitigation_deck",
            "title": "QA Missing Mitigation Deck",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Risk Matrix",
                    "layout": "risk_matrix",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 0.5, "width": 8.0, "height": 0.5},
                            "text": "Risk Matrix",
                        },
                        {
                            "element_id": "risk_1",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 8.0, "height": 0.8},
                            "text": "权限过大\n误操作影响用户数据",
                        },
                        {
                            "element_id": "risk_2",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 2.4, "width": 8.0, "height": 0.8},
                            "text": "错误输出\n影响用户判断\n设置人工确认和操作日志",
                        },
                        {
                            "element_id": "risk_3",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 3.3, "width": 8.0, "height": 0.8},
                            "text": "过度自动化\n降低可控性\n限制高风险自动执行",
                        },
                    ],
                }
            ],
        }
    )

    report = analyze_deck(deck)
    issues = [issue for issue in report.issues if issue.code == "risk_matrix_missing_mitigation"]

    assert issues
    assert issues[0].severity == "warning"
    assert issues[0].element_id == "risk_1"
    assert report.score > 0


def test_analyze_deck_does_not_warn_short_deck_for_low_layout_diversity() -> None:
    deck = _deck_with_layouts(
        ["title_slide", "two_column", "closing_slide"],
        ["Opening", "Main idea", "Close"],
    )

    report = analyze_deck(deck)

    assert "layout_diversity_low" not in _issue_codes(report)
