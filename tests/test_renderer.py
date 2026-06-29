from pathlib import Path

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.load import load_deck, load_theme
from ppt_agent.models import Deck
from ppt_agent.renderer import render_deck_to_pptx


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
INTERNAL_SURFACE_TERMS = {"TEMPLATE-GUIDED", "Editable PPTX", "COLUMN", "CARD"}


def test_render_deck_to_pptx_generates_editable_powerpoint(tmp_path: Path) -> None:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    output_path = tmp_path / "sample_deck.pptx"

    rendered_path = render_deck_to_pptx(deck, theme, output_path, assets_dir=EXAMPLES_DIR)

    assert rendered_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    presentation = Presentation(output_path)
    assert len(presentation.slides) == len(deck.slides)
    assert presentation.slide_width == Inches(deck.canvas_width_in)
    assert presentation.slide_height == Inches(deck.canvas_height_in)

    first_slide = presentation.slides[0]
    editable_text_shapes = [
        shape
        for shape in first_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]

    assert editable_text_shapes
    assert any("Q3 Operating Review" in shape.text for shape in editable_text_shapes)


def test_render_deck_to_pptx_creates_missing_image_placeholder(tmp_path: Path) -> None:
    deck = load_deck(EXAMPLES_DIR / "sample_slide_ir.json")
    theme = load_theme(EXAMPLES_DIR / "theme.json")

    output_path = render_deck_to_pptx(
        deck,
        theme,
        tmp_path / "sample_deck.pptx",
        assets_dir=EXAMPLES_DIR,
    )

    presentation = Presentation(output_path)
    third_slide = presentation.slides[2]

    assert any(
        shape.has_text_frame and "Placeholder" in shape.text
        for shape in third_slide.shapes
    )


def _template_slide_payload(layout: str, index: int) -> dict:
    body_texts = {
        "title_slide": ["A concise subtitle for the title slide"],
        "section_divider": ["This section frames the next part of the story."],
        "two_column": ["Left column content\n- Point A\n- Point B", "Right column content\n- Point C\n- Point D"],
        "three_column": ["First pillar", "Second pillar", "Third pillar"],
        "four_cards": [
            "Discover\nMap user needs",
            "Prioritize\nChoose high-value work",
            "Prototype\nBuild the first flow",
            "Measure\nTrack quality signals",
        ],
        "metric_cards": ["Revenue\n$4.2M", "Growth\n18%", "Retention\n91%"],
        "closing_slide": ["Thank you. Questions and next steps."],
        "comparison_matrix": [
            "Normal AI\nResponds to direct prompts\nNeeds manual handoff",
            "AI Agent\nPlans multi-step work\nUses tools with guardrails",
            "Choose Agent when workflow ownership matters",
        ],
        "process_flow": [
            "Discover\nMap user needs",
            "Design\nDefine workflow",
            "Build\nCreate prototype",
            "Measure\nTrack quality",
        ],
        "risk_matrix": [
            "Hallucination\nHigh user trust impact\nUse retrieval checks",
            "Data leakage\nCompliance impact\nLimit sensitive inputs",
            "Over-automation\nOperational impact\nKeep human review",
        ],
        "key_takeaway": [
            "AI agents need product judgment\nStart from owned workflows, not model demos",
            "Pick one high-value user journey",
            "Add evals before scaling",
            "Keep human checkpoints",
        ],
    }[layout]

    elements = [
        {
            "element_id": f"s{index}_title",
            "type": "text",
            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
            "text": f"{layout} title",
        }
    ]
    for body_index, text in enumerate(body_texts, start=1):
        elements.append(
            {
                "element_id": f"s{index}_body_{body_index}",
                "type": "text",
                "bbox": {"x": 0.8 + body_index, "y": 1.5, "width": 3.0, "height": 1.0},
                "text": text,
            }
        )

    return {
        "slide_id": f"template_{index:03d}",
        "title": f"{layout} title",
        "layout": layout,
        "elements": elements,
    }


def test_template_layouts_render_to_editable_powerpoint(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "template_layout_demo",
            "title": "Template Layout Demo",
            "theme_name": "clean_business",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                _template_slide_payload(layout, index)
                for index, layout in enumerate(TEMPLATE_LAYOUTS, start=1)
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "template_layouts.pptx")

    presentation = Presentation(output_path)
    assert len(presentation.slides) == len(TEMPLATE_LAYOUTS)

    for layout, rendered_slide in zip(TEMPLATE_LAYOUTS, presentation.slides):
        editable_text_shapes = [
            shape
            for shape in rendered_slide.shapes
            if getattr(shape, "has_text_frame", False) and shape.text.strip()
        ]
        assert editable_text_shapes
        assert any(f"{layout} title" in shape.text for shape in editable_text_shapes)


def _shape_with_text(slide, text: str):
    for shape in slide.shapes:
        if getattr(shape, "has_text_frame", False) and shape.text.strip() == text:
            return shape
    raise AssertionError(f"Could not find editable text shape: {text}")


def _visible_texts(presentation: Presentation) -> list[str]:
    return [
        shape.text.strip()
        for slide in presentation.slides
        for shape in slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]


def _overlap_area(shape_a, shape_b) -> int:
    left = max(shape_a.left, shape_b.left)
    right = min(shape_a.left + shape_a.width, shape_b.left + shape_b.width)
    top = max(shape_a.top, shape_b.top)
    bottom = min(shape_a.top + shape_a.height, shape_b.top + shape_b.height)
    if right <= left or bottom <= top:
        return 0
    return int((right - left) * (bottom - top))


def test_title_slide_long_title_keeps_subtitle_below_title_bbox(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    long_title = "AI 产品经理如何设计可落地的 Agent 工作流"
    subtitle = "从用户需求、工具调用、状态管理到风险控制的技术产品分享"
    deck = Deck.model_validate(
        {
            "deck_id": "title_spacing_demo",
            "title": "Title Spacing Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Title Spacing Demo",
                    "layout": "title_slide",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
                            "text": long_title,
                        },
                        {
                            "element_id": "subtitle",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 1.3, "width": 6.0, "height": 0.5},
                            "text": subtitle,
                        },
                    ],
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "title_spacing.pptx")
    rendered_slide = Presentation(output_path).slides[0]
    title_shape = _shape_with_text(rendered_slide, long_title)
    subtitle_shape = _shape_with_text(rendered_slide, subtitle)
    visible_texts = [
        shape.text.strip()
        for shape in rendered_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]

    assert subtitle_shape.top >= title_shape.top + title_shape.height + Inches(0.25)
    assert title_shape.left < Inches(1.0)
    assert title_shape.width >= Inches(7.5)
    assert visible_texts.count(long_title) == 1
    assert visible_texts.count(subtitle) == 1
    assert "Focus" not in visible_texts
    assert "Editable PPTX" not in visible_texts
    assert "TEMPLATE-GUIDED" not in visible_texts


def test_title_slide_does_not_duplicate_subtitle_in_side_panel(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    subtitle = "Building practical fluency, ethical judgment, and responsible AI habits"
    deck = Deck.model_validate(
        {
            "deck_id": "title_side_summary_demo",
            "title": "Title Side Summary Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Title Side Summary Demo",
                    "layout": "title_slide",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
                            "text": "AI Education for Practical Student Readiness",
                        },
                        {
                            "element_id": "subtitle",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 1.3, "width": 6.0, "height": 0.5},
                            "text": subtitle,
                        },
                    ],
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "title_side_summary.pptx")
    rendered_slide = Presentation(output_path).slides[0]
    subtitle_shapes = [
        shape
        for shape in rendered_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip() == subtitle
    ]

    assert len(subtitle_shapes) == 1
    assert subtitle_shapes[0].left < Inches(8.0)


def test_four_cards_renders_heading_and_body_as_editable_text(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "four_cards_demo",
            "title": "Four Cards Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [_template_slide_payload("four_cards", 1)],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "four_cards.pptx")
    rendered_slide = Presentation(output_path).slides[0]
    editable_texts = [
        shape.text.strip()
        for shape in rendered_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]

    assert "Discover" in editable_texts
    assert "Map user needs" in editable_texts
    assert "01" in editable_texts
    assert "Action" in editable_texts
    assert not (INTERNAL_SURFACE_TERMS & set(editable_texts))
    assert "Prioritize" in editable_texts
    assert "Choose high-value work" in editable_texts


def test_chinese_deck_uses_chinese_card_labels(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "chinese_labels_demo",
            "title": "AI 学习应用",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "AI 学习应用",
                    "layout": "four_cards",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
                            "text": "AI 学习应用",
                        },
                        {
                            "element_id": "card_1",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 3.0, "height": 1.0},
                            "text": "预习\n快速理解背景",
                        },
                        {
                            "element_id": "card_2",
                            "type": "text",
                            "bbox": {"x": 4.0, "y": 1.5, "width": 3.0, "height": 1.0},
                            "text": "练习\n获得即时反馈",
                        },
                    ],
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "chinese_labels.pptx")
    visible_texts = _visible_texts(Presentation(output_path))

    assert "行动" in visible_texts
    assert "Action" not in visible_texts
    assert "Insight" not in visible_texts
    assert "Priority" not in visible_texts
    assert "Step" not in visible_texts


def test_sparse_two_column_layout_uses_compact_cards(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "sparse_two_column_demo",
            "title": "Sparse Two Column Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Sparse Two Column Demo",
                    "layout": "two_column",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
                            "text": "Two Focus Areas",
                        },
                        {
                            "element_id": "left",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 3.0, "height": 1.0},
                            "text": "Practice\nBuild fluency",
                        },
                        {
                            "element_id": "right",
                            "type": "text",
                            "bbox": {"x": 4.0, "y": 1.5, "width": 3.0, "height": 1.0},
                            "text": "Reflect\nUse judgment",
                        },
                    ],
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "sparse_two_column.pptx")
    rendered_slide = Presentation(output_path).slides[0]
    card_backgrounds = [
        shape
        for shape in rendered_slide.shapes
        if not getattr(shape, "text", "").strip()
        and shape.width > Inches(5)
        and shape.height > Inches(1)
    ]

    assert len(card_backgrounds) == 2
    assert max(shape.height for shape in card_backgrounds) <= Inches(2.5)


def test_closing_slide_renders_multiple_next_steps_as_editable_text(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "closing_actions_demo",
            "title": "Closing Actions Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Closing Actions Demo",
                    "layout": "closing_slide",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
                            "text": "Next Steps",
                        },
                        {
                            "element_id": "actions",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.5, "width": 5.0, "height": 1.0},
                            "text": "Pilot the workflow\nGather feedback\nScale the pattern",
                        },
                    ],
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "closing_actions.pptx")
    rendered_slide = Presentation(output_path).slides[0]
    editable_texts = [
        shape.text.strip()
        for shape in rendered_slide.shapes
        if getattr(shape, "has_text_frame", False) and shape.text.strip()
    ]

    assert "01" in editable_texts
    assert "02" in editable_texts
    assert "03" in editable_texts
    for number in ["01", "02", "03"]:
        number_shape = _shape_with_text(rendered_slide, number)
        assert number_shape.width >= Inches(0.4)
    assert "Pilot the workflow" in editable_texts
    assert "Gather feedback" in editable_texts
    assert "Scale the pattern" in editable_texts


def test_template_rendering_does_not_expose_internal_surface_terms(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "surface_cleanup_demo",
            "title": "Surface Cleanup Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                _template_slide_payload(layout, index)
                for index, layout in enumerate(TEMPLATE_LAYOUTS, start=1)
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "surface_cleanup.pptx")
    visible_text = "\n".join(_visible_texts(Presentation(output_path)))

    for term in INTERNAL_SURFACE_TERMS:
        assert term not in visible_text


def test_professional_layouts_render_distinct_editable_templates(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    professional_layouts = [
        "comparison_matrix",
        "process_flow",
        "risk_matrix",
        "key_takeaway",
    ]
    deck = Deck.model_validate(
        {
            "deck_id": "professional_layout_demo",
            "title": "Professional Layout Demo",
            "theme_name": "clean_business",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                _template_slide_payload(layout, index)
                for index, layout in enumerate(professional_layouts, start=1)
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "professional_layouts.pptx")
    presentation = Presentation(output_path)

    assert len(presentation.slides) == 4
    visible_texts = _visible_texts(presentation)
    assert "Option A" in visible_texts
    assert "Option B" in visible_texts
    assert "01" in visible_texts
    assert "Risk" in visible_texts
    assert "Mitigation" in visible_texts
    assert "Key Takeaway" in visible_texts
    for layout in professional_layouts:
        assert any(f"{layout} title" in text for text in visible_texts)


@pytest.mark.parametrize("step_count", [3, 4, 5])
def test_process_flow_renders_readable_step_counts(tmp_path: Path, step_count: int) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    elements = [
        {
            "element_id": "title",
            "type": "text",
            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
            "text": f"{step_count} Step Workflow",
        }
    ]
    for index in range(1, step_count + 1):
        elements.append(
            {
                "element_id": f"step_{index}",
                "type": "text",
                "bbox": {"x": 0.8, "y": 1.2 + index * 0.4, "width": 3.0, "height": 0.6},
                "text": f"Step {index}\nComplete focused action {index} without excessive detail",
            }
        )
    deck = Deck.model_validate(
        {
            "deck_id": f"process_flow_{step_count}_demo",
            "title": "Process Flow Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Process Flow Demo",
                    "layout": "process_flow",
                    "elements": elements,
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / f"process_{step_count}.pptx")
    presentation = Presentation(output_path)
    rendered_slide = presentation.slides[0]
    visible_texts = _visible_texts(presentation)
    step_numbers = {f"{index:02d}" for index in range(1, step_count + 1)}
    card_backgrounds = [
        shape
        for shape in rendered_slide.shapes
        if not getattr(shape, "text", "").strip()
        and shape.width >= Inches(3.5 if step_count >= 4 else 3.0)
        and shape.height >= Inches(1.4)
    ]

    assert step_numbers <= set(visible_texts)
    assert len(card_backgrounds) >= step_count
    if step_count == 5:
        for card in card_backgrounds:
            assert card.left >= 0
            assert card.top >= 0
            assert card.left + card.width <= presentation.slide_width
            assert card.top + card.height <= presentation.slide_height

        connectors = [
            shape
            for shape in rendered_slide.shapes
            if shape.shape_type == MSO_SHAPE_TYPE.LINE
        ]
        core_text_shapes = [
            shape
            for shape in rendered_slide.shapes
            if getattr(shape, "has_text_frame", False)
            and "Complete focused action" in shape.text
        ]

        assert connectors
        assert core_text_shapes
        assert all(
            _overlap_area(connector, text_shape) == 0
            for connector in connectors
            for text_shape in core_text_shapes
        )


def test_comparison_matrix_renders_aligned_rows(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "comparison_matrix_rows_demo",
            "title": "Comparison Matrix Rows Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [_template_slide_payload("comparison_matrix", 1)],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "comparison_rows.pptx")
    visible_texts = _visible_texts(Presentation(output_path))

    assert "Dimension" in visible_texts
    assert "Input / Output" in visible_texts
    assert "State" in visible_texts
    assert "Option A" in visible_texts
    assert "Option B" in visible_texts
    assert any("workflow ownership" in text for text in visible_texts)


@pytest.mark.parametrize("risk_count", [3, 4])
def test_risk_matrix_renders_three_to_four_risks(tmp_path: Path, risk_count: int) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    slide_payload = _template_slide_payload("risk_matrix", 1)
    slide_payload["elements"] = slide_payload["elements"][:1] + slide_payload["elements"][1 : risk_count + 1]
    deck = Deck.model_validate(
        {
            "deck_id": f"risk_matrix_{risk_count}_demo",
            "title": "Risk Matrix Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [slide_payload],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / f"risk_{risk_count}.pptx")
    visible_texts = _visible_texts(Presentation(output_path))

    assert "Risk" in visible_texts
    assert "Impact" in visible_texts
    assert "Mitigation" in visible_texts
    assert any("Hallucination" in text for text in visible_texts)


def test_key_takeaway_renders_fallback_explanations(tmp_path: Path) -> None:
    theme = load_theme(EXAMPLES_DIR / "theme.json")
    deck = Deck.model_validate(
        {
            "deck_id": "key_takeaway_fallback_demo",
            "title": "Key Takeaway Fallback Demo",
            "canvas_width_in": 13.333,
            "canvas_height_in": 7.5,
            "slides": [
                {
                    "slide_id": "slide_001",
                    "title": "Key Takeaway Fallback Demo",
                    "layout": "key_takeaway",
                    "elements": [
                        {
                            "element_id": "title",
                            "type": "text",
                            "bbox": {"x": 0.5, "y": 0.5, "width": 6.0, "height": 0.6},
                            "text": "Focus on workflow ownership",
                        },
                        {
                            "element_id": "takeaway_1",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 1.4, "width": 5.0, "height": 0.6},
                            "text": "Start with one journey\nUse evaluation before scaling",
                        },
                        {
                            "element_id": "takeaway_2",
                            "type": "text",
                            "bbox": {"x": 0.8, "y": 2.0, "width": 5.0, "height": 0.6},
                            "text": "Keep human checkpoints",
                        },
                    ],
                }
            ],
        }
    )

    output_path = render_deck_to_pptx(deck, theme, tmp_path / "key_takeaway_fallback.pptx")
    visible_texts = _visible_texts(Presentation(output_path))

    assert "Keep human checkpoints" in visible_texts
    assert "Turn this point into a concrete next action." in visible_texts
