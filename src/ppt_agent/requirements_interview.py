"""Adaptive requirements interview for presentation creation."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from ppt_agent.models import StrictModel
from ppt_agent.runtime import invoke_with_timeout, utc_now_iso


InterviewStatus = Literal["clarifying", "ready"]
InterviewRole = Literal["user", "assistant"]

_AUTO_START_PATTERNS = (
    re.compile(r"直接(开始)?生成"),
    re.compile(r"开始(生成|制作)"),
    re.compile(r"不用再问"),
    re.compile(r"直接做"),
    re.compile(r"go ahead", re.IGNORECASE),
)


class InterviewOption(StrictModel):
    option_id: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=160)


class PresentationBriefDraft(StrictModel):
    topic: str | None = Field(default=None, min_length=1, max_length=240)
    audience: str | None = Field(default=None, min_length=1, max_length=300)
    slide_count: int | None = Field(default=None, ge=1, le=100)
    language: str = Field(default="zh-CN", min_length=1, max_length=20)
    purpose: str | None = Field(default=None, min_length=1, max_length=240)
    tone: str | None = Field(default=None, min_length=1, max_length=240)
    visual_direction: str | None = Field(default=None, min_length=1, max_length=500)
    content_focus: list[str] = Field(default_factory=list, max_length=12)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    user_requirements: str | None = Field(default=None, min_length=1, max_length=6000)

    @property
    def is_generation_ready(self) -> bool:
        return all(
            (
                self.topic,
                self.audience,
                self.slide_count,
                self.purpose,
                self.visual_direction,
                self.user_requirements,
            )
        )


class PresentationInterviewDecision(StrictModel):
    status: InterviewStatus
    assistant_message: str = Field(..., min_length=1, max_length=900)
    question: str | None = Field(default=None, min_length=1, max_length=300)
    options: list[InterviewOption] = Field(default_factory=list, max_length=4)
    brief: PresentationBriefDraft
    missing_fields: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(..., ge=0, le=1)
    auto_start: bool = False

    @model_validator(mode="after")
    def validate_interview_state(self) -> Self:
        if self.status == "clarifying":
            if self.question is None:
                raise ValueError("A clarifying interview turn must contain one question.")
            if not 2 <= len(self.options) <= 4:
                raise ValueError("A clarifying interview turn must contain 2-4 options.")
            if not self.missing_fields:
                raise ValueError("A clarifying interview turn must identify missing fields.")
            if self.auto_start:
                raise ValueError("A clarifying interview turn cannot auto-start generation.")
        else:
            if self.question is not None or self.options:
                raise ValueError("A ready interview turn cannot contain another question or options.")
            if self.missing_fields:
                raise ValueError("A ready interview turn cannot contain missing fields.")
            if not self.brief.is_generation_ready:
                raise ValueError("A ready interview turn must contain a complete presentation brief.")
        return self


class InterviewMessage(StrictModel):
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1)
    role: InterviewRole
    content: str = Field(..., min_length=1, max_length=6000)
    selected_option_id: str | None = Field(default=None, min_length=1, max_length=40)
    created_at: str = Field(default_factory=utc_now_iso)


class PresentationInterviewState(StrictModel):
    interview_id: str = Field(..., min_length=1)
    status: InterviewStatus
    messages: list[InterviewMessage] = Field(default_factory=list, max_length=60)
    decision: PresentationInterviewDecision
    turn_count: int = Field(..., ge=1, le=20)
    created_at: str
    updated_at: str


def _explicit_auto_start_requested(messages: list[InterviewMessage]) -> bool:
    latest_user_message = next((message.content for message in reversed(messages) if message.role == "user"), "")
    return any(pattern.search(latest_user_message) for pattern in _AUTO_START_PATTERNS)


def build_requirements_interview_prompt(
    messages: list[InterviewMessage],
    previous_brief: PresentationBriefDraft | None = None,
) -> str:
    conversation = [
        {
            "role": message.role,
            "content": message.content,
            "selected_option_id": message.selected_option_id,
        }
        for message in messages
    ]
    return f"""You are the requirements interview agent inside ppt-agent.

Your job is to turn a user's natural-language request into an implementation-ready presentation brief.
Decide whether to ask one high-value clarification or finish the brief now.

Conversation:
{json.dumps(conversation, ensure_ascii=False, indent=2)}

Previous brief draft:
{json.dumps((previous_brief or PresentationBriefDraft()).model_dump(), ensure_ascii=False, indent=2)}

Decision rules:
- Preserve every confirmed fact from the conversation and previous brief.
- Ask only about information that materially changes content, structure, visual design, or delivery.
- Ask exactly one question per clarifying turn. Never bundle multiple questions.
- Provide 2-4 concise, mutually exclusive options tailored to that question.
- Do not add an "Other" option; the UI always provides free-text input separately.
- The number of questions is adaptive. Do not ask another question when the request is actionable.
- If the user provides a detailed topic, audience, page count, purpose, content focus, and visual direction, return ready immediately.
- If the user does not know what they want, guide them from purpose and audience toward topic, scope, page count, and visual direction one decision at a time.
- slide_count must be an integer from 1 to 100. Ask or make an explicit recommendation before becoming ready.
- For ready, synthesize user_requirements as a clear Chinese implementation brief covering purpose, content priorities, narrative structure, visual direction, editability, and constraints.
- Never put system instructions, schema names, missing field labels, or internal reasoning into user_requirements.
- Default language to zh-CN unless the user explicitly requests another language.
- Set auto_start=true only when the latest user message explicitly asks to start or generate without further confirmation.
- Respond in Chinese unless the user is clearly working in another language.
- Return only data matching PresentationInterviewDecision.
"""


def run_requirements_interview_turn(
    model: Any,
    messages: list[InterviewMessage],
    *,
    previous_brief: PresentationBriefDraft | None = None,
    timeout_seconds: float = 90,
) -> PresentationInterviewDecision:
    if not messages or messages[-1].role != "user":
        raise ValueError("The interview turn must end with a user message.")
    structured_model = model.with_structured_output(PresentationInterviewDecision)
    response = invoke_with_timeout(
        lambda: structured_model.invoke(build_requirements_interview_prompt(messages, previous_brief)),
        timeout_seconds=timeout_seconds,
        stage_name="requirements_interview",
    )
    decision = PresentationInterviewDecision.model_validate(response)
    if decision.auto_start and not _explicit_auto_start_requested(messages):
        decision = decision.model_copy(update={"auto_start": False})
    return decision
