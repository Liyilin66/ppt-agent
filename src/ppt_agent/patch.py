"""Structured patch operations for validated Slide IR decks."""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from ppt_agent.models import Deck, HexColor, StrictModel


class UpdateTextOperation(StrictModel):
    op: Literal["update_text"]
    slide_id: str = Field(..., min_length=1)
    element_id: str = Field(..., min_length=1)
    text: str


class MoveElementOperation(StrictModel):
    op: Literal["move_element"]
    slide_id: str = Field(..., min_length=1)
    element_id: str = Field(..., min_length=1)
    dx: float
    dy: float


class ResizeElementOperation(StrictModel):
    op: Literal["resize_element"]
    slide_id: str = Field(..., min_length=1)
    element_id: str = Field(..., min_length=1)
    width: float = Field(..., gt=0)
    height: float = Field(..., gt=0)


class UpdateShapeStyleOperation(StrictModel):
    op: Literal["update_shape_style"]
    slide_id: str = Field(..., min_length=1)
    element_id: str = Field(..., min_length=1)
    fill_color: HexColor | None = None
    stroke_color: HexColor | None = None
    stroke_width_pt: float | None = Field(default=None, gt=0)


PatchOperation = Annotated[
    UpdateTextOperation | MoveElementOperation | ResizeElementOperation | UpdateShapeStyleOperation,
    Field(discriminator="op"),
]


class SlidePatch(StrictModel):
    patch_id: str | None = Field(default=None, min_length=1)
    operations: list[PatchOperation] = Field(..., min_length=1)


class PatchIssue(StrictModel):
    operation_index: int = Field(..., ge=0)
    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    slide_id: str | None = None
    element_id: str | None = None


class PatchResult(StrictModel):
    deck: Deck
    applied_count: int = Field(..., ge=0)
    issues: list[PatchIssue] = Field(default_factory=list)


def _issue(operation_index: int, operation: PatchOperation, code: str, message: str) -> PatchIssue:
    return PatchIssue(
        operation_index=operation_index,
        code=code,
        message=message,
        slide_id=operation.slide_id,
        element_id=operation.element_id,
    )


def _find_slide(payload: dict, slide_id: str) -> dict | None:
    for slide in payload["slides"]:
        if slide["slide_id"] == slide_id:
            return slide
    return None


def _find_element(slide: dict, element_id: str) -> dict | None:
    for element in slide["elements"]:
        if element["element_id"] == element_id:
            return element
    return None


def _apply_operation(payload: dict, operation: PatchOperation, operation_index: int) -> PatchIssue | None:
    slide = _find_slide(payload, operation.slide_id)
    if slide is None:
        return _issue(
            operation_index,
            operation,
            "SLIDE_NOT_FOUND",
            f"Slide '{operation.slide_id}' was not found for operation '{operation.op}'.",
        )

    element = _find_element(slide, operation.element_id)
    if element is None:
        return _issue(
            operation_index,
            operation,
            "ELEMENT_NOT_FOUND",
            (
                f"Element '{operation.element_id}' was not found on slide "
                f"'{operation.slide_id}' for operation '{operation.op}'."
            ),
        )

    if isinstance(operation, UpdateTextOperation):
        if element["type"] != "text":
            return _issue(
                operation_index,
                operation,
                "ELEMENT_TYPE_MISMATCH",
                (
                    f"Operation 'update_text' requires a TextElement, but element "
                    f"'{operation.element_id}' on slide '{operation.slide_id}' is type "
                    f"'{element['type']}'."
                ),
            )
        element["text"] = operation.text
        return None

    if isinstance(operation, MoveElementOperation):
        element["bbox"]["x"] += operation.dx
        element["bbox"]["y"] += operation.dy
        return None

    if isinstance(operation, ResizeElementOperation):
        element["bbox"]["width"] = operation.width
        element["bbox"]["height"] = operation.height
        return None

    if isinstance(operation, UpdateShapeStyleOperation):
        if element["type"] != "shape":
            return _issue(
                operation_index,
                operation,
                "ELEMENT_TYPE_MISMATCH",
                (
                    f"Operation 'update_shape_style' requires a ShapeElement, but element "
                    f"'{operation.element_id}' on slide '{operation.slide_id}' is type "
                    f"'{element['type']}'."
                ),
            )

        style = element.setdefault("style", {})
        for field_name in ("fill_color", "stroke_color", "stroke_width_pt"):
            if field_name in operation.model_fields_set:
                style[field_name] = getattr(operation, field_name)
        return None

    return _issue(
        operation_index,
        operation,
        "UNSUPPORTED_OPERATION",
        f"Operation '{operation.op}' is not supported.",
    )


def apply_patch(deck: Deck, patch: SlidePatch) -> PatchResult:
    """Apply a structured patch to a deck without mutating the input deck."""

    payload = deck.model_dump(mode="json")
    current_deck = Deck.model_validate(payload)
    issues: list[PatchIssue] = []
    applied_count = 0

    for operation_index, operation in enumerate(patch.operations):
        candidate_payload = deepcopy(payload)
        operation_issue = _apply_operation(candidate_payload, operation, operation_index)
        if operation_issue:
            issues.append(operation_issue)
            continue

        try:
            validated_deck = Deck.model_validate(candidate_payload)
        except ValidationError as exc:
            issues.append(
                _issue(
                    operation_index,
                    operation,
                    "DECK_VALIDATION_FAILED",
                    (
                        f"Operation '{operation.op}' on slide '{operation.slide_id}' "
                        f"element '{operation.element_id}' would make the deck invalid: {exc}"
                    ),
                )
            )
            continue

        payload = candidate_payload
        current_deck = validated_deck
        applied_count += 1

    return PatchResult(deck=current_deck, applied_count=applied_count, issues=issues)
