"""Code-owned presentation design constraints."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from ppt_agent.models import HexColor, StrictModel


DensityLevel = Literal["low", "medium", "high"]
FontScale = Literal["compact", "standard", "large"]
SlideRole = Literal[
    "cover",
    "context",
    "comparison",
    "framework",
    "process",
    "metrics",
    "risk",
    "summary",
]


class DesignSpec(StrictModel):
    theme_name: str = "clean_business"
    visual_tone: str = "clean, professional, presentation-ready"
    density_level: DensityLevel = "medium"
    font_scale: FontScale = "standard"
    accent_color: HexColor | None = None
    background_style: str = "light"


class LayoutContract(StrictModel):
    layout_name: str = Field(..., min_length=1)
    best_for: list[str] = Field(..., min_length=1)
    required_slots: list[str] = Field(..., min_length=1)
    optional_slots: list[str] = Field(default_factory=list)
    min_items: int = Field(..., ge=0)
    max_items: int = Field(..., ge=0)
    avoid_when: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_capacity(self) -> Self:
        if self.min_items > self.max_items:
            raise ValueError(
                f"LayoutContract '{self.layout_name}' has min_items={self.min_items} "
                f"greater than max_items={self.max_items}."
            )
        return self


SUPPORTED_LAYOUT_CONTRACTS: tuple[LayoutContract, ...] = (
    LayoutContract(
        layout_name="title_slide",
        best_for=["cover"],
        required_slots=["title"],
        optional_slots=["subtitle"],
        min_items=1,
        max_items=2,
        avoid_when=[
            "dense content",
            "detailed comparison",
            "more than one supporting idea",
        ],
    ),
    LayoutContract(
        layout_name="section_divider",
        best_for=["context", "framework", "process"],
        required_slots=["section_title"],
        optional_slots=["section_subtitle"],
        min_items=1,
        max_items=2,
        avoid_when=[
            "short decks",
            "data-heavy slides",
            "standalone tactical recommendations",
        ],
    ),
    LayoutContract(
        layout_name="two_column",
        best_for=["comparison", "context", "risk"],
        required_slots=["left_column", "right_column"],
        min_items=2,
        max_items=2,
        avoid_when=[
            "three or more parallel concepts",
            "single closing message",
        ],
    ),
    LayoutContract(
        layout_name="three_column",
        best_for=["framework", "process", "risk"],
        required_slots=["column_1", "column_2", "column_3"],
        min_items=3,
        max_items=3,
        avoid_when=[
            "binary comparisons",
            "four-part frameworks",
        ],
    ),
    LayoutContract(
        layout_name="four_cards",
        best_for=["framework", "process", "summary"],
        required_slots=["card_1", "card_2", "card_3"],
        optional_slots=["card_4"],
        min_items=3,
        max_items=4,
        avoid_when=[
            "long paragraphs",
            "single takeaway slides",
        ],
    ),
    LayoutContract(
        layout_name="metric_cards",
        best_for=["metrics", "comparison"],
        required_slots=["metric_1", "metric_2"],
        optional_slots=["metric_3"],
        min_items=2,
        max_items=3,
        avoid_when=[
            "non-quantified ideas",
            "more than three KPIs",
        ],
    ),
    LayoutContract(
        layout_name="closing_slide",
        best_for=["summary"],
        required_slots=["closing_message"],
        optional_slots=["next_step_1", "next_step_2"],
        min_items=1,
        max_items=3,
        avoid_when=[
            "new evidence",
            "detailed analysis",
            "more than three action items",
        ],
    ),
    LayoutContract(
        layout_name="comparison_matrix",
        best_for=[
            "comparing two options",
            "before/after",
            "normal AI vs Agent",
            "tradeoff analysis",
        ],
        required_slots=["left_title", "left_points", "right_title", "right_points"],
        optional_slots=["decision_rule"],
        min_items=2,
        max_items=2,
        avoid_when=[
            "three or more options",
            "linear process steps",
            "risk register content",
        ],
    ),
    LayoutContract(
        layout_name="process_flow",
        best_for=[
            "workflows",
            "pipelines",
            "step-by-step processes",
            "implementation sequence",
        ],
        required_slots=["steps"],
        optional_slots=["checkpoints"],
        min_items=3,
        max_items=5,
        avoid_when=[
            "binary comparisons",
            "risk impact mitigation rows",
            "single takeaway pages",
        ],
    ),
    LayoutContract(
        layout_name="risk_matrix",
        best_for=[
            "risks",
            "impact",
            "mitigation",
            "governance",
        ],
        required_slots=["risks"],
        optional_slots=["risk / impact / mitigation"],
        min_items=3,
        max_items=4,
        avoid_when=[
            "non-risk summaries",
            "step-by-step process slides",
            "two-option comparisons",
        ],
    ),
    LayoutContract(
        layout_name="key_takeaway",
        best_for=[
            "conclusion",
            "summary",
            "action checklist",
            "pre-closing takeaway",
        ],
        required_slots=["takeaways"],
        optional_slots=["next_actions"],
        min_items=2,
        max_items=4,
        avoid_when=[
            "new detailed evidence",
            "dense metrics",
            "more than four takeaways",
        ],
    ),
)

_LAYOUT_CONTRACTS_BY_NAME = {
    contract.layout_name: contract for contract in SUPPORTED_LAYOUT_CONTRACTS
}


def get_layout_contract(layout_name: str) -> LayoutContract:
    try:
        contract = _LAYOUT_CONTRACTS_BY_NAME[layout_name]
    except KeyError as exc:
        supported = ", ".join(sorted(_LAYOUT_CONTRACTS_BY_NAME))
        raise ValueError(
            f"Unsupported layout '{layout_name}'. Supported LayoutContract "
            f"layout_name values: {supported}."
        ) from exc
    return contract.model_copy(deep=True)


def list_layout_contracts() -> list[LayoutContract]:
    return [contract.model_copy(deep=True) for contract in SUPPORTED_LAYOUT_CONTRACTS]
