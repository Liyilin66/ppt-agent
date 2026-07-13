import pytest
from pydantic import ValidationError

from ppt_agent.requirements_interview import (
    InterviewMessage,
    PresentationInterviewDecision,
    run_requirements_interview_turn,
)


class FakeStructuredModel:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        return self.response


class FakeModel:
    def __init__(self, response: dict) -> None:
        self.schema = None
        self.structured_model = FakeStructuredModel(response)

    def with_structured_output(self, schema):
        self.schema = schema
        return self.structured_model


def _clarifying_response() -> dict:
    return {
        "status": "clarifying",
        "assistant_message": "我先确认这份演示最重要的用途。",
        "question": "你希望观众看完后主要获得什么？",
        "options": [
            {"option_id": "learn", "label": "理解基础知识", "description": "建立完整认知框架"},
            {"option_id": "decide", "label": "支持决策", "description": "比较方案并给出建议"},
            {"option_id": "act", "label": "推动行动", "description": "形成明确执行计划"},
        ],
        "brief": {
            "topic": "生态环境保护",
            "language": "zh-CN",
            "content_focus": [],
            "constraints": [],
        },
        "missing_fields": ["purpose", "audience", "slide_count", "visual_direction"],
        "confidence": 0.45,
        "auto_start": False,
    }


def _ready_response(*, auto_start: bool = False) -> dict:
    return {
        "status": "ready",
        "assistant_message": "需求已经足够具体，我已整理成可执行 Brief。",
        "question": None,
        "options": [],
        "brief": {
            "topic": "生态城市更新路线图",
            "audience": "城市规划与环境工程专业学生",
            "slide_count": 36,
            "language": "zh-CN",
            "purpose": "完成一场专业课程分享",
            "tone": "专业、清晰、证据驱动",
            "visual_direction": "浅色背景，使用地图、时间轴和指标图表",
            "content_focus": ["问题诊断", "技术方案", "实施路线图"],
            "constraints": ["16:9", "所有页面保持可编辑"],
            "user_requirements": "生成 36 页中文可编辑 PPTX，面向城市规划与环境工程专业学生。",
        },
        "missing_fields": [],
        "confidence": 0.95,
        "auto_start": auto_start,
    }


def test_interview_turn_returns_one_question_with_options() -> None:
    model = FakeModel(_clarifying_response())

    decision = run_requirements_interview_turn(
        model,
        [InterviewMessage(role="user", content="我想做一个生态环境保护 PPT，但还没想清楚。")],
    )

    assert model.schema is PresentationInterviewDecision
    assert decision.status == "clarifying"
    assert decision.question == "你希望观众看完后主要获得什么？"
    assert len(decision.options) == 3
    assert "Ask exactly one question" in model.structured_model.prompts[0]


def test_interview_ready_does_not_auto_start_without_explicit_request() -> None:
    decision = run_requirements_interview_turn(
        FakeModel(_ready_response(auto_start=True)),
        [InterviewMessage(role="user", content="这是完整需求，请帮我整理。")],
    )

    assert decision.status == "ready"
    assert decision.auto_start is False


def test_interview_ready_can_auto_start_after_explicit_request() -> None:
    decision = run_requirements_interview_turn(
        FakeModel(_ready_response(auto_start=True)),
        [InterviewMessage(role="user", content="需求都在这里，不用再问，直接生成。")],
    )

    assert decision.auto_start is True


def test_ready_decision_rejects_incomplete_brief() -> None:
    payload = _ready_response()
    payload["brief"]["audience"] = None

    with pytest.raises(ValidationError, match="complete presentation brief"):
        PresentationInterviewDecision.model_validate(payload)
