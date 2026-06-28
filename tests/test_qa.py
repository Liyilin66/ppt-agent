import json
from pathlib import Path

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


def test_analyze_deck_does_not_warn_short_deck_for_low_layout_diversity() -> None:
    deck = _deck_with_layouts(
        ["title_slide", "two_column", "closing_slide"],
        ["Opening", "Main idea", "Close"],
    )

    report = analyze_deck(deck)

    assert "layout_diversity_low" not in _issue_codes(report)
