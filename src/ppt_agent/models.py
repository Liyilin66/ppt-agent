"""Pydantic models for the minimal Slide IR."""

from __future__ import annotations

from typing import Annotated, Any, Iterable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


def _duplicate_values(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []

    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)

    return duplicates


class StrictModel(BaseModel):
    """Shared strict base so the IR stays explicit and renderer-friendly."""

    model_config = ConfigDict(extra="forbid")


class BBox(StrictModel):
    """PowerPoint-style bounding box, measured in inches."""

    x: float = Field(..., ge=0, description="Left offset in inches.")
    y: float = Field(..., ge=0, description="Top offset in inches.")
    width: float = Field(..., gt=0, description="Width in inches.")
    height: float = Field(..., gt=0, description="Height in inches.")


class TextStyle(StrictModel):
    """Small text style model that can grow with renderer needs later."""

    font_family: str | None = Field(default=None, min_length=1)
    font_size_pt: float | None = Field(default=None, gt=0)
    color: HexColor | None = None
    bold: bool = False
    italic: bool = False


class ShapeStyle(StrictModel):
    """Small shape style model for validated visual attributes."""

    fill_color: HexColor | None = None
    stroke_color: HexColor | None = None
    stroke_width_pt: float | None = Field(default=None, gt=0)


class Element(StrictModel):
    """Base fields shared by every slide element."""

    element_id: str = Field(..., min_length=1)
    type: str
    bbox: BBox
    style: dict[str, Any] | None = None


class TextElement(Element):
    type: Literal["text"] = "text"
    text: str
    style: TextStyle | None = None


class ShapeElement(Element):
    type: Literal["shape"] = "shape"
    shape: Literal["rectangle", "ellipse", "line"]
    style: ShapeStyle | None = None


class ImageElement(Element):
    type: Literal["image"] = "image"
    src: str = Field(..., min_length=1)
    alt_text: str | None = None
    style: dict[str, Any] | None = None


SlideElement = Annotated[
    TextElement | ShapeElement | ImageElement,
    Field(discriminator="type"),
]


class Slide(StrictModel):
    slide_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    layout: str = Field(..., min_length=1)
    elements: list[SlideElement] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_unique_element_ids(self) -> Self:
        duplicates = _duplicate_values(element.element_id for element in self.elements)
        if duplicates:
            duplicate_list = ", ".join(f"'{element_id}'" for element_id in duplicates)
            raise ValueError(
                f"Slide '{self.slide_id}' element_id values must be unique; "
                f"duplicates: {duplicate_list}"
            )
        return self


class Deck(StrictModel):
    deck_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    theme_name: str | None = Field(default=None, min_length=1)
    canvas_width_in: float = Field(default=13.333, gt=0)
    canvas_height_in: float = Field(default=7.5, gt=0)
    slides: list[Slide] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_deck_relationships(self) -> Self:
        errors: list[str] = []

        duplicate_slide_ids = _duplicate_values(slide.slide_id for slide in self.slides)
        if duplicate_slide_ids:
            duplicate_list = ", ".join(f"'{slide_id}'" for slide_id in duplicate_slide_ids)
            errors.append(
                f"Deck '{self.deck_id}' slide_id values must be unique; "
                f"duplicates: {duplicate_list}"
            )

        for slide in self.slides:
            for element in slide.elements:
                right_edge = element.bbox.x + element.bbox.width
                bottom_edge = element.bbox.y + element.bbox.height

                if right_edge > self.canvas_width_in:
                    errors.append(
                        f"Element '{element.element_id}' on slide '{slide.slide_id}' "
                        f"exceeds canvas width: bbox.x + bbox.width = {right_edge} "
                        f"> canvas_width_in = {self.canvas_width_in}"
                    )

                if bottom_edge > self.canvas_height_in:
                    errors.append(
                        f"Element '{element.element_id}' on slide '{slide.slide_id}' "
                        f"exceeds canvas height: bbox.y + bbox.height = {bottom_edge} "
                        f"> canvas_height_in = {self.canvas_height_in}"
                    )

        if errors:
            raise ValueError("; ".join(errors))

        return self
