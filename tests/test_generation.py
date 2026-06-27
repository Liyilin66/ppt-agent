import pytest
from pydantic import ValidationError

from ppt_agent.generation import (
    DeckGenerationRequest,
    build_generation_prompt,
    generate_deck_with_model,
    generate_deck_with_quality_gate,
)
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


def test_deck_generation_request_validates_slide_count() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=3)

    assert request.language == "en"
    assert request.key_points == []


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


def test_generate_deck_with_fake_model_returns_deck() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    model = FakeModel(_valid_deck_payload())

    deck = generate_deck_with_model(model, request)

    assert isinstance(deck, Deck)
    assert model.schema is Deck
    assert "AI roadmap" in model.structured_model.prompts[0]
    assert len(deck.slides) == 2


def test_generate_deck_with_fake_model_accepts_deck_instance() -> None:
    request = DeckGenerationRequest(topic="AI roadmap", audience="executives", slide_count=2)
    deck_response = Deck.model_validate(_valid_deck_payload())
    model = FakeModel(deck_response)

    deck = generate_deck_with_model(model, request)

    assert isinstance(deck, Deck)
    assert deck.deck_id == "generated_demo_deck"


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
