import copy

import pytest
from pydantic import ValidationError

from ppt_agent.generation import (
    DeckBrief,
    DeckGenerationRequest,
    build_brief_from_user_prompt,
    build_generation_prompt,
    generate_deck_with_model,
    generate_deck_with_quality_gate,
)
from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.models import Deck
from ppt_agent.qa import QAReport, analyze_deck


class FakeStructuredModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class FakeModel:
    def __init__(self, response):
        self.schema = None
        responses = response if isinstance(response, list) else [response]
        self.structured_model = FakeStructuredModel(responses)

    def with_structured_output(self, schema):
        self.schema = schema
        return self.structured_model


def _valid_deck_payload() -> dict:
    return {
        "deck_id": "generated_demo_deck",
        "title": "AI Readiness Roadmap",
        "theme_name": "clean_business",
        "canvas_width_in": 13.333,
        "canvas_height_in": 7.5,
        "slides": [
            {
                "slide_id": "slide_001",
                "title": "AI Readiness Roadmap",
                "layout": "title",
                "elements": [
                    {
                        "element_id": "title_text",
                        "type": "text",
                        "bbox": {"x": 0.8, "y": 0.8, "width": 8.0, "height": 0.8},
                        "text": "AI Readiness Roadmap",
                    },
                    {
                        "element_id": "accent",
                        "type": "shape",
                        "bbox": {"x": 0.8, "y": 1.8, "width": 3.0, "height": 0.1},
                        "shape": "rectangle",
                        "style": {"fill_color": "#2563EB"},
                    },
                ],
            },
            {
                "slide_id": "slide_002",
                "title": "Priority Moves",
                "layout": "content",
                "elements": [
                    {
                        "element_id": "body_text",
                        "type": "text",
                        "bbox": {"x": 0.9, "y": 1.0, "width": 7.2, "height": 2.0},
                        "text": "Assess data readiness, choose high-value workflows, and define governance.",
                    }
                ],
            },
        ],
    }


def _low_quality_deck_payload() -> dict:
    payload = _valid_deck_payload()
    payload["deck_id"] = "low_quality_deck"
    payload["slides"][0]["elements"] = [
        {
            "element_id": "tiny_text",
            "type": "text",
            "bbox": {"x": 0.5, "y": 0.5, "width": 1.0, "height": 0.5},
            "text": "Hi",
        }
    ]
    payload["slides"][1]["elements"] = [
        {
            "element_id": "tiny_text_2",
            "type": "text",
            "bbox": {"x": 0.5, "y": 0.5, "width": 1.0, "height": 0.5},
            "text": "Hi",
        }
    ]
    return payload


def _deck_payload_with_slide_count(slide_count: int) -> dict:
    payload = _valid_deck_payload()
    base_slide = copy.deepcopy(payload["slides"][1])
    slides = []

    for index in range(1, slide_count + 1):
        slide = copy.deepcopy(payload["slides"][0] if index == 1 else base_slide)
        slide["slide_id"] = f"slide_{index:03d}"
        slide["title"] = f"Slide {index}"
        slide["layout"] = "title_slide" if index == 1 else ("closing_slide" if index == slide_count else "two_column")
        for element_index, element in enumerate(slide["elements"], start=1):
            element["element_id"] = f"s{index}_e{element_index}"
        slides.append(slide)

    payload["slides"] = slides
    return payload


def test_deck_generation_request_validates_slide_count() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=3)

    assert request.language == "zh-CN"
    assert request.key_points == []


def test_deck_brief_defaults_to_chinese() -> None:
    brief = DeckBrief(topic="AI 教育", audience="大学生", slide_count=3)

    assert brief.language == "zh-CN"
    assert brief.must_include == []
    assert brief.must_avoid == []


@pytest.mark.parametrize("slide_count", [0, 11])
def test_deck_generation_request_rejects_out_of_range_slide_count(slide_count: int) -> None:
    with pytest.raises(ValidationError):
        DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=slide_count)


def test_build_generation_prompt_contains_core_constraints() -> None:
    request = DeckGenerationRequest(
        topic="AI roadmap",
        audience="executives",
        slide_count=4,
        style="clean_business",
        key_points=["governance", "90-day plan"],
    )

    prompt = build_generation_prompt(request)

    assert "Deck Pydantic schema" in prompt
    assert "13.333" in prompt
    assert "7.5" in prompt
    assert "PowerPoint-style inches" in prompt
    assert "slide_id values must be unique" in prompt
    assert "element_id values must be unique" in prompt
    assert "Do not create any bbox that extends outside" in prompt
    assert "roughly 2 to 5 elements" in prompt
    assert "Generate exactly 4 slides" in prompt
    assert "Required root fields: deck_id, title" in prompt
    assert "Never use font_size; use font_size_pt" in prompt
    assert "Never use line_color; use stroke_color" in prompt
    assert "stroke_width_pt must be greater than 0; never use 0" in prompt
    assert "Choose each slide.layout from these controlled layouts only" in prompt
    assert "Do not rely on freeform bbox placement for visual design" in prompt
    assert "Do not create empty cards" in prompt
    assert "Card text should be short phrases" in prompt
    assert "Do not use section_divider by default in a short 3-slide deck" in prompt
    assert "Use four_cards for four parallel concepts" in prompt
    assert "title <= 9 words" in prompt
    assert "subtitle <= 16 words" in prompt
    assert "card heading <= 4 words" in prompt
    assert "card body <= 18 words" in prompt
    assert "Heading\n  Short body sentence" in prompt
    assert "Default to Simplified Chinese" in prompt
    assert "generate all user-visible slide text in natural Chinese" in prompt
    for layout in TEMPLATE_LAYOUTS:
        assert layout in prompt


def test_build_generation_prompt_includes_user_brief_constraints() -> None:
    brief = DeckBrief(
        topic="AI 如何帮助学习",
        audience="大学课堂学生",
        slide_count=3,
        purpose="课堂展示",
        tone="清晰、克制",
        visual_style="简洁现代",
        content_focus="学习效率与学术诚信风险",
        must_include=["AI 辅助学习", "学术诚信"],
        must_avoid=["夸大 AI 能力"],
        user_requirements_raw="做一份中文 PPT，提醒风险。",
    )
    request = DeckGenerationRequest(
        topic="AI 教育",
        audience="大学生",
        slide_count=3,
        brief=brief,
    )

    prompt = build_generation_prompt(request)

    assert "DeckBrief" in prompt
    assert "课堂展示" in prompt
    assert "简洁现代" in prompt
    assert "AI 辅助学习" in prompt
    assert "夸大 AI 能力" in prompt
    assert "Default to Simplified Chinese" in prompt


def test_build_generation_prompt_respects_explicit_english() -> None:
    request = DeckGenerationRequest(
        topic="AI education",
        audience="students",
        slide_count=3,
        language="en",
    )

    prompt = build_generation_prompt(request)

    assert "The user explicitly requested English" in prompt
    assert "concise English" in prompt


def test_build_brief_from_user_prompt_with_fake_model() -> None:
    response = {
        "topic": "AI 如何帮助学习",
        "audience": "大学生",
        "slide_count": 3,
        "language": "zh-CN",
        "purpose": "课堂展示",
        "tone": "简洁、可信",
        "visual_style": "现代商务",
        "content_focus": "学习效率与学术诚信",
        "must_include": ["学习场景", "诚信风险"],
        "must_avoid": ["过度承诺"],
        "user_requirements_raw": "placeholder",
    }
    model = FakeModel(response)
    requirements = "我要做一份给大学课堂展示的中文 PPT，重点讲 AI 如何帮助学习。"

    brief = build_brief_from_user_prompt(
        model,
        requirements,
        topic="AI 教育",
        audience="大学生",
        slide_count=3,
    )

    assert model.schema is DeckBrief
    assert brief.topic == "AI 如何帮助学习"
    assert brief.language == "zh-CN"
    assert brief.slide_count == 3
    assert brief.user_requirements_raw == requirements
    assert "Default language to zh-CN" in model.structured_model.prompts[0]


def test_generate_deck_with_fake_model_returns_deck() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel(_valid_deck_payload())

    deck = generate_deck_with_model(model, request)

    assert isinstance(deck, Deck)
    assert model.schema is Deck
    assert "AI roadmap" in model.structured_model.prompts[0]
    assert len(deck.slides) == 2


def test_generate_deck_with_user_requirements_builds_brief_first() -> None:
    brief_response = {
        "topic": "AI 如何帮助学习",
        "audience": "大学生",
        "slide_count": 2,
        "language": "zh-CN",
        "purpose": "课堂展示",
        "tone": "简洁",
        "visual_style": "现代",
        "content_focus": "学习效率",
        "must_include": ["诚信风险"],
        "must_avoid": [],
        "user_requirements_raw": "placeholder",
    }
    request = DeckGenerationRequest(
        topic="AI 教育",
        audience="大学生",
        slide_count=2,
        user_requirements="做中文 PPT，提醒学术诚信风险。",
    )
    model = FakeModel([brief_response, _valid_deck_payload()])

    deck = generate_deck_with_model(model, request)

    assert isinstance(deck, Deck)
    assert len(model.structured_model.prompts) == 2
    assert "Extract a DeckBrief" in model.structured_model.prompts[0]
    assert "诚信风险" in model.structured_model.prompts[1]


def test_generate_deck_with_fake_model_accepts_deck_instance() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    deck_response = Deck.model_validate(_valid_deck_payload())
    model = FakeModel(deck_response)

    deck = generate_deck_with_model(model, request)

    assert isinstance(deck, Deck)
    assert deck.deck_id == "generated_demo_deck"


def test_generate_deck_normalizes_common_provider_schema_drift() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    payload = _valid_deck_payload()
    del payload["deck_id"]
    del payload["title"]
    del payload["slides"][1]["layout"]
    payload["slides"][0]["layout"] = "title"
    payload["slides"][0]["elements"][0]["style"] = {
        "font_size": 32,
        "color": "#111827",
    }
    payload["slides"][0]["elements"][1]["style"] = {
        "fill_color": "#2563EB",
        "line_color": "#111827",
    }

    deck = generate_deck_with_model(FakeModel(payload), request)

    assert deck.deck_id == "generated_ai_roadmap"
    assert deck.title == "AI roadmap"
    assert deck.slides[0].layout == "title_slide"
    assert deck.slides[1].layout == "closing_slide"
    assert deck.slides[0].elements[0].style.font_size_pt == 32
    assert deck.slides[0].elements[1].style.stroke_color == "#111827"


def test_generate_deck_normalizes_four_card_layout_alias() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    payload = _valid_deck_payload()
    payload["slides"][1]["layout"] = "four steps"

    deck = generate_deck_with_model(FakeModel(payload), request)

    assert deck.slides[1].layout == "four_cards"


def test_generate_deck_rejects_slide_count_mismatch() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=3)
    model = FakeModel(_deck_payload_with_slide_count(4))

    with pytest.raises(ValueError, match="Generated Deck has 4 slides"):
        generate_deck_with_model(model, request)


def test_quality_gate_retries_slide_count_mismatch_and_accepts_fixed_deck() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=3)
    model = FakeModel([_deck_payload_with_slide_count(4), _deck_payload_with_slide_count(3)])

    result = generate_deck_with_quality_gate(model, request, min_score=80, max_attempts=2)

    assert result.accepted is True
    assert len(result.deck.slides) == 3
    assert len(model.structured_model.prompts) == 2
    assert "Generated Deck has 4 slides" in model.structured_model.prompts[1]
    assert "Regenerate exactly 3 slides" in model.structured_model.prompts[1]


def test_generate_deck_omits_zero_shape_stroke_width() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    payload = _valid_deck_payload()
    payload["slides"][0]["elements"][1]["style"] = {
        "fill_color": "#2563EB",
        "stroke_color": "#111827",
        "stroke_width_pt": 0,
    }

    deck = generate_deck_with_model(FakeModel(payload), request)

    assert deck.slides[0].elements[1].style.stroke_color == "#111827"
    assert deck.slides[0].elements[1].style.stroke_width_pt is None


def test_generated_deck_can_be_analyzed() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    deck = generate_deck_with_model(FakeModel(_valid_deck_payload()), request)

    report = analyze_deck(deck)

    assert report.deck_id == deck.deck_id
    assert 0 <= report.score <= 100


def test_build_generation_prompt_includes_qa_feedback() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    low_quality_report = analyze_deck(Deck.model_validate(_low_quality_deck_payload()))

    prompt = build_generation_prompt(request, qa_feedback=low_quality_report)

    assert "QA feedback from the previous attempt" in prompt
    assert f"Previous QA score: {low_quality_report.score}" in prompt
    assert "SLIDE_TOO_EMPTY" in prompt
    assert "Avoid repeating these QA problems" in prompt


def test_quality_gate_retries_and_accepts_second_attempt() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel([_low_quality_deck_payload(), _valid_deck_payload()])

    result = generate_deck_with_quality_gate(model, request, min_score=99, max_attempts=2)

    assert result.accepted is True
    assert result.deck.deck_id == "generated_demo_deck"
    assert result.qa_report.score >= 99
    assert len(result.attempts) == 2
    assert result.attempts[0].accepted is False
    assert result.attempts[1].accepted is True
    assert "QA feedback from the previous attempt" in model.structured_model.prompts[1]
    assert "SLIDE_TOO_EMPTY" in model.structured_model.prompts[1]


def test_quality_gate_returns_last_attempt_when_all_attempts_fail() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel([_low_quality_deck_payload(), _low_quality_deck_payload()])

    result = generate_deck_with_quality_gate(model, request, min_score=100, max_attempts=2)

    assert result.accepted is False
    assert result.deck.deck_id == "low_quality_deck"
    assert result.qa_report.score < 100
    assert len(result.attempts) == 2
    assert all(attempt.accepted is False for attempt in result.attempts)
    assert isinstance(result.qa_report, QAReport)
