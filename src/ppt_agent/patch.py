"""Structured patch operations for validated Slide IR decks."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

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


class PatchChangedElement(StrictModel):
    slide_id: str = Field(..., min_length=1)
    element_id: str = Field(..., min_length=1)
    operation: str = Field(..., min_length=1)
    before: Any | None = None
    after: Any | None = None


class PatchResult(StrictModel):
    patch_id: str | None = Field(default=None, min_length=1)
    accepted: bool = False
    success: bool = False
    deck: Deck
    applied_count: int = Field(..., ge=0)
    issues: list[PatchIssue] = Field(default_factory=list)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    changed_elements: list[PatchChangedElement] = Field(default_factory=list)
    input_patch_path: str | None = None
    output_pptx_path: str | None = None
    generated_at: str | None = None


class PatchableElementSummary(StrictModel):
    element_id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    text_preview: str | None = None
    patchable_operations: list[str] = Field(default_factory=list)


class PatchableSlideSummary(StrictModel):
    slide_id: str = Field(..., min_length=1)
    slide_index: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    elements: list[PatchableElementSummary] = Field(default_factory=list)


class PatchableElementsReport(StrictModel):
    slides: list[PatchableSlideSummary] = Field(default_factory=list)


def _generated_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _truncate_text_preview(text: str, max_chars: int = 40) -> str:
    preview = " / ".join(part.strip() for part in text.splitlines() if part.strip())
    if len(preview) <= max_chars:
        return preview
    return preview[: max_chars - 3].rstrip() + "..."


def _patchable_operations_for_element(element_type: str) -> list[str]:
    operations = ["move_element", "resize_element"]
    if element_type == "text":
        return ["update_text", *operations]
    if element_type == "shape":
        return [*operations, "update_shape_style"]
    return operations


def build_patchable_elements_report(deck: Deck) -> PatchableElementsReport:
    """Derive a patchable elements index from an existing validated Deck IR."""

    slides: list[PatchableSlideSummary] = []
    for slide_index, slide in enumerate(deck.slides, start=1):
        elements: list[PatchableElementSummary] = []
        for element in slide.elements:
            text_preview = None
            if element.type == "text":
                text_preview = _truncate_text_preview(element.text)
            elements.append(
                PatchableElementSummary(
                    element_id=element.element_id,
                    type=element.type,
                    text_preview=text_preview,
                    patchable_operations=_patchable_operations_for_element(element.type),
                )
            )
        slides.append(
            PatchableSlideSummary(
                slide_id=slide.slide_id,
                slide_index=slide_index,
                title=slide.title,
                elements=elements,
            )
        )
    return PatchableElementsReport(slides=slides)


def build_patch_failure_result(
    deck: Deck,
    *,
    code: str,
    message: str,
    input_patch_path: str | None = None,
) -> PatchResult:
    """Create a consistent patch report when a patch cannot be parsed or validated."""

    return PatchResult(
        deck=deck,
        patch_id=None,
        accepted=False,
        success=False,
        applied_count=0,
        issues=[
            PatchIssue(
                operation_index=0,
                code=code,
                message=message,
            )
        ],
        operations=[],
        changed_elements=[],
        input_patch_path=input_patch_path,
        generated_at=_generated_timestamp(),
    )


def _operation_change_before(element: dict, operation: PatchOperation) -> Any | None:
    if isinstance(operation, UpdateTextOperation):
        return element.get("text")
    if isinstance(operation, (MoveElementOperation, ResizeElementOperation)):
        return deepcopy(element.get("bbox"))
    if isinstance(operation, UpdateShapeStyleOperation):
        return deepcopy(element.get("style"))
    return None


def _operation_change_after(element: dict, operation: PatchOperation) -> Any | None:
    if isinstance(operation, UpdateTextOperation):
        return element.get("text")
    if isinstance(operation, (MoveElementOperation, ResizeElementOperation)):
        return deepcopy(element.get("bbox"))
    if isinstance(operation, UpdateShapeStyleOperation):
        return deepcopy(element.get("style"))
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
    changed_elements: list[PatchChangedElement] = []
    operations = patch.model_dump(mode="json").get("operations", [])

    for operation_index, operation in enumerate(patch.operations):
        current_slide = _find_slide(payload, operation.slide_id)
        current_element = _find_element(current_slide, operation.element_id) if current_slide is not None else None
        before_value = _operation_change_before(current_element, operation) if current_element is not None else None
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

        candidate_slide = _find_slide(candidate_payload, operation.slide_id)
        candidate_element = _find_element(candidate_slide, operation.element_id) if candidate_slide is not None else None
        after_value = _operation_change_after(candidate_element, operation) if candidate_element is not None else None
        if candidate_element is not None:
            changed_elements.append(
                PatchChangedElement(
                    slide_id=operation.slide_id,
                    element_id=operation.element_id,
                    operation=operation.op,
                    before=before_value,
                    after=after_value,
                )
            )

        payload = candidate_payload
        current_deck = validated_deck
        applied_count += 1

    success = not issues and applied_count == len(patch.operations)
    return PatchResult(
        deck=current_deck,
        patch_id=patch.patch_id,
        accepted=success,
        success=success,
        applied_count=applied_count,
        issues=issues,
        operations=operations,
        changed_elements=changed_elements,
        generated_at=_generated_timestamp(),
    )
