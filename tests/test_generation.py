import copy
import time

import pytest
from pydantic import ValidationError

import ppt_agent.generation as generation
from ppt_agent.generation import (
    DeckBrief,
    DeckGenerationRequest,
    build_brief_from_user_prompt,
    build_generation_prompt,
    format_qa_feedback_for_generation,
    generate_deck_with_model,
    generate_deck_with_quality_gate,
)
from ppt_agent.layouts import TEMPLATE_LAYOUTS
from ppt_agent.models import Deck
from ppt_agent.planning import (
    DECK_PLAN_STRUCTURED_OUTPUT_SCHEMA,
    DeckPlan,
    SlidePlan,
    build_deck_plan_prompt,
    generate_deck_plan_with_model,
)
from ppt_agent.qa import QAIssue, QAReport, analyze_deck
from ppt_agent.runtime import LLMCallTimeoutError


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


class SlowStructuredModel:
    def invoke(self, prompt: str):
        time.sleep(0.05)
        return {}


class SlowModel:
    def with_structured_output(self, schema):
        return SlowStructuredModel()


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


def _valid_deck_plan_payload(slide_count: int = 3) -> dict:
    slides = []
    layouts = [
        "four_cards",
        "comparison_matrix",
        "process_flow",
        "risk_matrix",
        "key_takeaway",
        "metric_cards",
    ]
    roles = ["framework", "comparison", "process", "metrics", "risk", "context"]
    content_items_by_layout = {
        "title_slide": 1,
        "section_divider": 1,
        "two_column": 2,
        "three_column": 3,
        "four_cards": 4,
        "metric_cards": 3,
        "closing_slide": 1,
        "comparison_matrix": 2,
        "process_flow": 4,
        "risk_matrix": 3,
        "key_takeaway": 3,
    }
    for index in range(1, slide_count + 1):
        if index == 1:
            layout = "title_slide"
            role = "cover"
        elif index == slide_count:
            layout = "closing_slide"
            role = "summary"
        else:
            layout = layouts[(index - 2) % len(layouts)]
            role = roles[(index - 2) % len(roles)]
        slides.append(
            {
                "slide_index": index,
                "slide_role": role,
                "key_message": f"Unique message {index}",
                "content_goal": f"Explain point {index} without repeating prior slides.",
                "recommended_layout": layout,
                "content_items": content_items_by_layout[layout],
                "must_not_repeat": [f"Unique message {previous}" for previous in range(1, index)],
            }
        )
    return {
        "topic": "AI 教育",
        "audience": "大学生",
        "slide_count": slide_count,
        "slides": slides,
    }


def test_deck_generation_request_validates_slide_count() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=3)

    assert request.language == "zh-CN"
    assert request.key_points == []


def test_deck_brief_defaults_to_chinese() -> None:
    brief = DeckBrief(topic="AI 教育", audience="大学生", slide_count=3)

    assert brief.language == "zh-CN"
    assert brief.must_include == []
    assert brief.must_avoid == []


def test_deck_plan_schema_validates_successfully() -> None:
    plan = DeckPlan.model_validate(_valid_deck_plan_payload(3))

    assert plan.slide_count == 3
    assert isinstance(plan.slides[0], SlidePlan)
    assert [slide.slide_index for slide in plan.slides] == [1, 2, 3]
    assert plan.slides[1].recommended_layout in TEMPLATE_LAYOUTS


def test_deck_plan_rejects_slide_count_mismatch() -> None:
    payload = _valid_deck_plan_payload(3)
    payload["slides"] = payload["slides"][:2]

    with pytest.raises(ValidationError):
        DeckPlan.model_validate(payload)


def test_deck_plan_rejects_non_consecutive_slide_indexes() -> None:
    payload = _valid_deck_plan_payload(3)
    payload["slides"][1]["slide_index"] = 3

    with pytest.raises(ValidationError):
        DeckPlan.model_validate(payload)


def test_deck_plan_rejects_unsupported_layout() -> None:
    payload = _valid_deck_plan_payload(3)
    payload["slides"][1]["recommended_layout"] = "timeline"

    with pytest.raises(ValidationError):
        DeckPlan.model_validate(payload)


def test_deck_plan_rejects_content_items_above_layout_capacity() -> None:
    payload = _valid_deck_plan_payload(3)
    payload["slides"][1]["recommended_layout"] = "title_slide"
    payload["slides"][1]["content_items"] = 4

    with pytest.raises(ValidationError) as exc_info:
        DeckPlan.model_validate(payload)

    message = str(exc_info.value)
    assert "content_items=4" in message
    assert "layout 'title_slide' capacity 1-2" in message


def test_generate_deck_plan_with_fake_model_returns_plan() -> None:
    brief = DeckBrief(topic="AI 教育", audience="大学生", slide_count=3)
    model = FakeModel(_valid_deck_plan_payload(3))

    plan = generate_deck_plan_with_model(model, brief)

    assert isinstance(plan, DeckPlan)
    assert model.schema == DECK_PLAN_STRUCTURED_OUTPUT_SCHEMA
    assert "Create a DeckPlan" in model.structured_model.prompts[0]
    assert len(plan.slides) == 3


def test_generate_deck_plan_unwraps_provider_deck_plan_wrapper() -> None:
    brief = DeckBrief(topic="AI 产品经理", audience="IT 硕士学生", slide_count=3)
    model = FakeModel({"deck_plan": _valid_deck_plan_payload(3)})

    plan = generate_deck_plan_with_model(model, brief)

    assert isinstance(plan, DeckPlan)
    assert plan.slide_count == 3
    assert [slide.slide_index for slide in plan.slides] == [1, 2, 3]


def test_generate_deck_plan_normalizes_provider_extra_fields_and_slide_number() -> None:
    brief = DeckBrief(topic="AI 产品经理", audience="IT 硕士学生", slide_count=3)
    payload = _valid_deck_plan_payload(3)
    payload["language"] = "zh-CN"
    payload["purpose"] = "中文分享 PPT"
    payload["tone"] = "技术产品分享"
    payload["visual_style"] = "clean_business"
    for slide in payload["slides"]:
        slide["slide_number"] = slide.pop("slide_index")
        slide["must_include"] = ["真实用户需求里的额外规划字段"]

    model = FakeModel({"deck_plan": payload})

    plan = generate_deck_plan_with_model(model, brief)

    assert isinstance(plan, DeckPlan)
    assert [slide.slide_index for slide in plan.slides] == [1, 2, 3]
    assert not hasattr(plan, "language")
    assert not hasattr(plan.slides[0], "slide_number")
    assert not hasattr(plan.slides[0], "must_include")


def test_build_deck_plan_prompt_contains_planning_constraints() -> None:
    brief = DeckBrief(topic="AI Agent 产品经理", audience="IT 硕士学生", slide_count=8)

    prompt = build_deck_plan_prompt(brief)

    assert "Plan exactly 8 slides" in prompt
    assert "unique key_message" in prompt
    assert "For 3-slide short decks, do not prioritize section_divider" in prompt
    assert "For long decks, keep layout diversity" in prompt
    assert "recommended_layout must be one of" in prompt
    assert "Default DesignSpec guidance" in prompt
    assert "LayoutContract registry" in prompt
    assert "max_items" in prompt
    assert "slide_role to one of" in prompt
    assert "Do not let content_items exceed the selected layout max_items" in prompt
    assert "prefer comparison_matrix" in prompt
    assert "prefer process_flow" in prompt
    assert "prefer risk_matrix" in prompt
    assert "prefer key_takeaway" in prompt
    for layout in TEMPLATE_LAYOUTS:
        assert layout in prompt


def test_build_generation_prompt_includes_optional_deck_plan() -> None:
    request = DeckGenerationRequest(topic="AI 教育", audience="大学生", slide_count=3)
    deck_plan = DeckPlan.model_validate(_valid_deck_plan_payload(3))

    prompt = build_generation_prompt(request, deck_plan=deck_plan)

    assert "DeckPlan guidance" in prompt
    assert "key_message: Unique message 2" in prompt
    assert "recommended_layout: four_cards" in prompt
    assert "content_items: 4" in prompt
    assert "must_not_repeat: Unique message 1" in prompt
    assert "must align with each slide's key_message" in prompt


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
    assert "Use comparison_matrix for two-option comparisons" in prompt
    assert "Use process_flow for workflows" in prompt
    assert "Use risk_matrix for risk governance pages" in prompt
    assert "Use key_takeaway for strong conclusion" in prompt
    assert "Professional layouts must keep text short enough" in prompt
    assert "Do not squeeze 5 process steps into one narrow row" in prompt
    assert "every takeaway must include both a concise title and a one-sentence explanation" in prompt
    assert "prefer aligned comparison rows over two sparse cards" in prompt
    assert "keep each risk, impact, and mitigation cell concise" in prompt
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

    assert model.schema["title"] == "DeckBrief"
    assert brief.topic == "AI 如何帮助学习"
    assert brief.language == "zh-CN"
    assert brief.slide_count == 3
    assert brief.user_requirements_raw == requirements
    assert "Default language to zh-CN" in model.structured_model.prompts[0]


def test_build_brief_from_user_prompt_times_out_slow_model() -> None:
    with pytest.raises(LLMCallTimeoutError, match="build_brief"):
        build_brief_from_user_prompt(
            SlowModel(),
            "生成一份中文 PPT。",
            topic="AI 教育",
            audience="大学生",
            slide_count=3,
            timeout_seconds=0.001,
        )


def test_build_brief_normalizes_common_provider_schema_drift() -> None:
    response = {
        "topic": "AI Agent 产品经理",
        "audience": "IT 硕士学生",
        "slide_count": 8,
        "language": "zh-CN",
        "purpose": "技术产品分享",
        "tone": "技术产品分享",
        "visual_style": "简洁现代",
        "content_focus": ["技术边界", "用户需求分析", "评估指标", "落地风险"],
        "must_include": "工作流设计",
        "must_avoid": ["营销口号"],
        "user_requirements_raw": "placeholder",
        "style": "clean_business",
        "key_points": [],
    }
    requirements = "我要做一份中文分享 PPT，面向准备进入 AI 产品岗位的 IT 硕士学生。"

    brief = build_brief_from_user_prompt(
        FakeModel(response),
        requirements,
        topic="AI Agent 产品经理",
        audience="IT 硕士学生",
        slide_count=8,
        style="clean_business",
    )

    assert brief.content_focus == "技术边界；用户需求分析；评估指标；落地风险"
    assert brief.must_include == ["工作流设计"]
    assert brief.must_avoid == ["营销口号"]
    assert brief.user_requirements_raw == requirements


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


def test_generate_deck_chunks_long_decks_to_reduce_single_request_size() -> None:
    request = DeckGenerationRequest(topic="AI Agent 产品经理", audience="IT 硕士学生", slide_count=8)
    model = FakeModel([
        _deck_payload_with_slide_count(4),
        _deck_payload_with_slide_count(4),
    ])

    deck = generate_deck_with_model(model, request)

    assert len(deck.slides) == 8
    assert [slide.slide_id for slide in deck.slides] == [f"slide_{index:03d}" for index in range(1, 9)]
    assert deck.slides[0].layout == "title_slide"
    assert deck.slides[-1].layout == "closing_slide"
    assert len(model.structured_model.prompts) == 2
    assert "Generate only global slides 1 through 4" in model.structured_model.prompts[0]
    assert "Generate only global slides 5 through 8" in model.structured_model.prompts[1]


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
    model = FakeModel([
        _valid_deck_plan_payload(3),
        _deck_payload_with_slide_count(4),
        _deck_payload_with_slide_count(3),
    ])

    result = generate_deck_with_quality_gate(model, request, min_score=80, max_attempts=2)

    assert result.accepted is True
    assert len(result.deck.slides) == 3
    assert result.deck_plan is not None
    assert len(model.structured_model.prompts) == 3
    assert "Create a DeckPlan" in model.structured_model.prompts[0]
    assert "Generated Deck has 4 slides" in model.structured_model.prompts[2]
    assert "Regenerate exactly 3 slides" in model.structured_model.prompts[2]


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


def test_format_qa_feedback_for_generation_instructs_layout_diversity_fix() -> None:
    report = QAReport(
        deck_id="deck",
        score=72,
        issues=[
            QAIssue(
                severity="warning",
                slide_id="deck",
                code="layout_diversity_low",
                message="Deck uses only two_column across content slides.",
            )
        ],
    )

    feedback = format_qa_feedback_for_generation(report)

    assert "Previous QA score: 72" in feedback
    assert "[warning] layout_diversity_low (slide=deck)" in feedback
    assert "Use at least 3 different content layouts across long decks" in feedback
    assert "avoid relying only on card layouts" in feedback


def test_format_qa_feedback_for_generation_instructs_layout_repetition_fix() -> None:
    report = QAReport(
        deck_id="deck",
        score=84,
        issues=[
            QAIssue(
                severity="warning",
                slide_id="slide_002",
                code="layout_repetition_run",
                message="Slides repeat four_cards for three consecutive content slides.",
            )
        ],
    )

    feedback = format_qa_feedback_for_generation(report)

    assert "[warning] layout_repetition_run (slide=slide_002)" in feedback
    assert "Do not use the same content layout for 3 consecutive slides" in feedback


def test_format_qa_feedback_for_generation_instructs_title_similarity_fix() -> None:
    report = QAReport(
        deck_id="deck",
        score=84,
        issues=[
            QAIssue(
                severity="warning",
                slide_id="slide_003",
                code="adjacent_title_similarity",
                message="Adjacent slide titles are too similar.",
            )
        ],
    )

    feedback = format_qa_feedback_for_generation(report)

    assert "[warning] adjacent_title_similarity (slide=slide_003)" in feedback
    assert "Make adjacent slide titles and key messages clearly distinct" in feedback


def test_format_qa_feedback_for_generation_instructs_layout_contract_fix() -> None:
    report = QAReport(
        deck_id="deck",
        score=84,
        issues=[
            QAIssue(
                severity="warning",
                slide_id="slide_002",
                code="layout_contract_violation",
                message="Slide exceeds layout capacity.",
            )
        ],
    )

    feedback = format_qa_feedback_for_generation(report)

    assert "[warning] layout_contract_violation (slide=slide_002)" in feedback
    assert "Use a layout whose capacity matches the number of content blocks" in feedback
    assert "reduce the number of major content items" in feedback


def test_format_qa_feedback_for_generation_instructs_visual_preflight_fixes_once() -> None:
    report = QAReport(
        deck_id="deck",
        score=44,
        issues=[
            QAIssue(
                severity="warning",
                slide_id="slide_001",
                code="visual_density_too_low",
                message="Looks empty.",
            ),
            QAIssue(
                severity="warning",
                slide_id="slide_002",
                code="visual_density_too_high",
                message="Looks dense.",
            ),
            QAIssue(
                severity="warning",
                slide_id="slide_003",
                element_id="text_1",
                code="text_overflow_risk",
                message="Text is too long.",
            ),
            QAIssue(
                severity="warning",
                slide_id="slide_004",
                code="title_wrapping_risk",
                message="Title may wrap.",
            ),
            QAIssue(
                severity="warning",
                slide_id="slide_005",
                code="text_overflow_risk",
                message="Another long text block.",
            ),
        ],
    )

    feedback = format_qa_feedback_for_generation(report)

    assert "Add enough meaningful content" in feedback
    assert "Reduce text density" in feedback
    assert "Shorten long text blocks" in feedback
    assert "Keep slide titles concise" in feedback
    assert feedback.count("Shorten long text blocks") == 1


def test_format_qa_feedback_for_generation_limits_issue_count() -> None:
    report = QAReport(
        deck_id="deck",
        score=20,
        issues=[
            QAIssue(
                severity="warning",
                slide_id=f"slide_{index:03d}",
                code=f"issue_{index}",
                message=f"Issue {index}",
            )
            for index in range(1, 11)
        ],
    )

    feedback = format_qa_feedback_for_generation(report)

    assert "issue_8" in feedback
    assert "issue_9" not in feedback
    assert "Showing first 8 of 10 QA issues" in feedback


def test_quality_gate_generates_plan_before_deck_generation() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel([
        _valid_deck_plan_payload(2),
        _valid_deck_payload(),
    ])

    result = generate_deck_with_quality_gate(model, request, min_score=80, max_attempts=1)

    assert result.accepted is True
    assert isinstance(result.deck_plan, DeckPlan)
    assert len(model.structured_model.prompts) == 2
    assert "Create a DeckPlan" in model.structured_model.prompts[0]
    assert "DeckPlan guidance" in model.structured_model.prompts[1]
    assert "key_message: Unique message 2" in model.structured_model.prompts[1]
    assert "recommended_layout: closing_slide" in model.structured_model.prompts[1]
    assert '"deck_plan"' in result.model_dump_json()


def test_quality_gate_reports_deck_plan_generation_failure() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel({"topic": "AI roadmap", "audience": "executives", "slide_count": 2, "slides": []})

    with pytest.raises(ValueError, match="DeckPlan generation failed"):
        generate_deck_with_quality_gate(model, request, min_score=80, max_attempts=1)


def test_quality_gate_retries_and_accepts_second_attempt() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel([
        _valid_deck_plan_payload(2),
        _low_quality_deck_payload(),
        _valid_deck_payload(),
    ])

    result = generate_deck_with_quality_gate(model, request, min_score=99, max_attempts=2)

    assert result.accepted is True
    assert result.deck_plan is not None
    assert result.deck.deck_id == "generated_demo_deck"
    assert result.qa_report.score >= 99
    assert len(result.attempts) == 2
    assert result.attempts[0].accepted is False
    assert result.attempts[1].accepted is True
    assert sum("Create a DeckPlan" in prompt for prompt in model.structured_model.prompts) == 1
    assert "DeckPlan guidance" in model.structured_model.prompts[1]
    assert "DeckPlan guidance" in model.structured_model.prompts[2]
    assert "QA feedback from the previous attempt" in model.structured_model.prompts[2]
    assert "SLIDE_TOO_EMPTY" in model.structured_model.prompts[2]
    assert "key_message: Unique message 2" in model.structured_model.prompts[2]


def test_quality_gate_retry_prompt_includes_actionable_layout_feedback(monkeypatch) -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel([
        _valid_deck_plan_payload(2),
        _valid_deck_payload(),
        _valid_deck_payload(),
    ])
    reports = [
        QAReport(
            deck_id="generated_demo_deck",
            score=50,
            issues=[
                QAIssue(
                    severity="warning",
                    slide_id="generated_demo_deck",
                    code="layout_diversity_low",
                    message="Deck uses only one content layout.",
                ),
                QAIssue(
                    severity="warning",
                    slide_id="slide_002",
                    code="layout_repetition_run",
                    message="Three consecutive content slides use the same layout.",
                ),
                QAIssue(
                    severity="warning",
                    slide_id="slide_003",
                    code="adjacent_title_similarity",
                    message="Adjacent titles are too similar.",
                ),
            ],
        ),
        QAReport(deck_id="generated_demo_deck", score=100, issues=[]),
    ]

    def fake_analyze_deck(deck, theme=None):
        return reports.pop(0)

    monkeypatch.setattr(generation, "analyze_deck", fake_analyze_deck)

    result = generate_deck_with_quality_gate(model, request, min_score=80, max_attempts=2)

    retry_prompt = model.structured_model.prompts[2]
    assert result.accepted is True
    assert "DeckPlan guidance" in retry_prompt
    assert "QA feedback from the previous attempt" in retry_prompt
    assert "Use at least 3 different content layouts across long decks" in retry_prompt
    assert "Do not use the same content layout for 3 consecutive slides" in retry_prompt
    assert "Make adjacent slide titles and key messages clearly distinct" in retry_prompt


def test_quality_gate_returns_last_attempt_when_all_attempts_fail() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel([
        _valid_deck_plan_payload(2),
        _low_quality_deck_payload(),
        _low_quality_deck_payload(),
    ])

    result = generate_deck_with_quality_gate(model, request, min_score=100, max_attempts=2)

    assert result.accepted is False
    assert result.deck_plan is not None
    assert result.deck.deck_id == "low_quality_deck"
    assert result.qa_report.score < 100
    assert len(result.attempts) == 2
    assert all(attempt.accepted is False for attempt in result.attempts)
    assert isinstance(result.qa_report, QAReport)
