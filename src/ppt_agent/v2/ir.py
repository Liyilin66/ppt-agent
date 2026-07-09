"""PageDesign IR: the constrained free-layout language the model writes per page.

Coordinates are canvas units on a fixed 1280x720 grid (16:9), converted to EMU
at render time. The model places elements freely but may only reference theme
color roles and text roles, so every page stays on the deck's design system.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from ppt_agent.models import StrictModel
from ppt_agent.v2.design import ColorRole, TextRole, ThemeSpec


CANVAS_WIDTH = 1280.0
CANVAS_HEIGHT = 720.0
MIN_ELEMENT_SIZE = 4.0

PageRole = Literal[
    "cover",
    "toc",
    "section_divider",
    "content",
    "quote",
    "stats",
    "comparison",
    "timeline",
    "closing",
]

ShapeKind = Literal[
    "rectangle",
    "rounded_rectangle",
    "pill",
    "ellipse",
    "triangle",
    "right_arrow",
    "chevron",
    "diamond",
    "hexagon",
    "parallelogram",
    "half_moon",
]

ChartKind = Literal["bar", "column", "line", "area", "pie", "doughnut"]

Align = Literal["left", "center", "right"]
VAlign = Literal["top", "middle", "bottom"]
Bullet = Literal["none", "dot", "number"]


class Frame(StrictModel):
    """Element bounding box in canvas units (1280x720 grid).

    Bounds allow moderate bleed past the canvas: cropped decorative shapes are
    a deliberate technique on anchor pages. Model output is still clamped fully
    inside the canvas by ``normalize_page_payload`` before validation.
    """

    x: float = Field(..., ge=-CANVAS_WIDTH / 2, le=CANVAS_WIDTH)
    y: float = Field(..., ge=-CANVAS_HEIGHT / 2, le=CANVAS_HEIGHT)
    w: float = Field(..., gt=0, le=CANVAS_WIDTH)
    h: float = Field(..., gt=0, le=CANVAS_HEIGHT)

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def intersection_over_union(self, other: "Frame") -> float:
        ix = max(0.0, min(self.right, other.right) - max(self.x, other.x))
        iy = max(0.0, min(self.bottom, other.bottom) - max(self.y, other.y))
        inter = ix * iy
        union = self.w * self.h + other.w * other.h - inter
        return inter / union if union else 0.0


class BaseElement(StrictModel):
    id: str = Field(..., min_length=1)
    frame: Frame


class Gradient(StrictModel):
    start: ColorRole = "primary"
    end: ColorRole = "secondary"
    angle_deg: float = Field(default=90, ge=0, lt=360)


class TextItem(BaseElement):
    type: Literal["text"] = "text"
    text: str = Field(..., min_length=1)
    role: TextRole = "body"
    color: ColorRole | None = None
    align: Align = "left"
    valign: VAlign = "top"
    bullet: Bullet = "none"
    size_pt: float | None = Field(default=None, gt=4, le=120)
    bold: bool | None = None
    italic: bool = False


class ShapeItem(BaseElement):
    type: Literal["shape"] = "shape"
    shape: ShapeKind = "rounded_rectangle"
    fill: ColorRole | None = "surface"
    fill_alpha: float = Field(default=1.0, ge=0, le=1)
    gradient: Gradient | None = None
    stroke: ColorRole | None = None
    stroke_width: float = Field(default=1.0, gt=0, le=12)
    rotation_deg: float = Field(default=0, ge=-180, le=180)


class LineItem(StrictModel):
    type: Literal["line"] = "line"
    id: str = Field(..., min_length=1)
    x1: float = Field(..., ge=0, le=CANVAS_WIDTH)
    y1: float = Field(..., ge=0, le=CANVAS_HEIGHT)
    x2: float = Field(..., ge=0, le=CANVAS_WIDTH)
    y2: float = Field(..., ge=0, le=CANVAS_HEIGHT)
    color: ColorRole = "primary"
    width: float = Field(default=1.5, gt=0, le=12)
    dash: bool = False


class IconItem(BaseElement):
    type: Literal["icon"] = "icon"
    name: str = Field(..., min_length=1)
    color: ColorRole = "primary"
    background: ColorRole | None = "primary_soft"
    background_shape: Literal["circle", "rounded", "none"] = "rounded"


class ChartSeries(StrictModel):
    name: str = Field(..., min_length=1)
    values: list[float] = Field(..., min_length=1)


class ChartItem(BaseElement):
    type: Literal["chart"] = "chart"
    chart: ChartKind
    title: str | None = None
    categories: list[str] = Field(..., min_length=1, max_length=12)
    series: list[ChartSeries] = Field(..., min_length=1, max_length=4)
    show_legend: bool = True
    show_data_labels: bool = False

    @model_validator(mode="after")
    def validate_series_lengths(self) -> Self:
        for series in self.series:
            if len(series.values) != len(self.categories):
                raise ValueError(
                    f"Chart '{self.id}' series '{series.name}' has "
                    f"{len(series.values)} values for {len(self.categories)} categories"
                )
        return self


class TableItem(BaseElement):
    type: Literal["table"] = "table"
    headers: list[str] = Field(..., min_length=1, max_length=8)
    rows: list[list[str]] = Field(..., min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_row_widths(self) -> Self:
        for index, row in enumerate(self.rows):
            if len(row) != len(self.headers):
                raise ValueError(
                    f"Table '{self.id}' row {index} has {len(row)} cells for "
                    f"{len(self.headers)} headers"
                )
        return self


class ImageItem(BaseElement):
    type: Literal["image"] = "image"
    src: str | None = None
    label: str = Field(default="Image", min_length=1)


PageElement = Annotated[
    TextItem | ShapeItem | LineItem | IconItem | ChartItem | TableItem | ImageItem,
    Field(discriminator="type"),
]


class PageDesign(StrictModel):
    """One slide, fully specified in theme tokens and canvas units."""

    page_number: int = Field(..., ge=1)
    role: PageRole = "content"
    section: str | None = None
    title: str | None = None
    background: ColorRole = "background"
    background_gradient: Gradient | None = None
    show_chrome: bool = True
    elements: list[PageElement] = Field(..., min_length=1)
    speaker_notes: str | None = None

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        seen: set[str] = set()
        for element in self.elements:
            if element.id in seen:
                raise ValueError(f"Duplicate element id '{element.id}' on page {self.page_number}")
            seen.add(element.id)
        return self


class DeckDesign(StrictModel):
    """The full deck artifact: theme plus every generated page."""

    deck_title: str = Field(..., min_length=1)
    subtitle: str | None = None
    language: str = "zh-CN"
    theme: ThemeSpec
    pages: list[PageDesign] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_page_numbers(self) -> Self:
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("Pages must be numbered 1..N in order")
        return self


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_frame_payload(frame: Any) -> Any:
    """Clamp an LLM-emitted frame into the canvas instead of hard-failing."""

    if not isinstance(frame, dict):
        return frame
    try:
        x = float(frame.get("x", 0))
        y = float(frame.get("y", 0))
        w = float(frame.get("w", MIN_ELEMENT_SIZE))
        h = float(frame.get("h", MIN_ELEMENT_SIZE))
    except (TypeError, ValueError):
        return frame
    x = _clamp(x, 0, CANVAS_WIDTH - MIN_ELEMENT_SIZE)
    y = _clamp(y, 0, CANVAS_HEIGHT - MIN_ELEMENT_SIZE)
    w = _clamp(w, MIN_ELEMENT_SIZE, CANVAS_WIDTH - x)
    h = _clamp(h, MIN_ELEMENT_SIZE, CANVAS_HEIGHT - y)
    return {"x": x, "y": y, "w": w, "h": h}


def normalize_page_payload(payload: dict[str, Any], *, page_number: int) -> dict[str, Any]:
    """Repair recoverable model-output issues before strict validation.

    Clamps frames/line endpoints into the canvas, drops unknown extra keys on
    elements, and forces the expected page number. Anything else still fails
    Pydantic validation loudly.
    """

    result = dict(payload)
    result["page_number"] = page_number
    elements = result.get("elements")
    if isinstance(elements, list):
        normalized_elements = []
        for raw in elements:
            if not isinstance(raw, dict):
                continue
            element = dict(raw)
            if "frame" in element:
                element["frame"] = _normalize_frame_payload(element["frame"])
            if element.get("type") == "line":
                for key, limit in (
                    ("x1", CANVAS_WIDTH),
                    ("x2", CANVAS_WIDTH),
                    ("y1", CANVAS_HEIGHT),
                    ("y2", CANVAS_HEIGHT),
                ):
                    try:
                        element[key] = _clamp(float(element.get(key, 0)), 0, limit)
                    except (TypeError, ValueError):
                        pass
            allowed = _allowed_keys_for_type(element.get("type"))
            if allowed:
                element = {key: value for key, value in element.items() if key in allowed}
            normalized_elements.append(element)
        result["elements"] = normalized_elements
    return result


_ELEMENT_TYPES: dict[str, type[StrictModel]] = {
    "text": TextItem,
    "shape": ShapeItem,
    "line": LineItem,
    "icon": IconItem,
    "chart": ChartItem,
    "table": TableItem,
    "image": ImageItem,
}


def _allowed_keys_for_type(type_name: Any) -> frozenset[str] | None:
    model = _ELEMENT_TYPES.get(type_name) if isinstance(type_name, str) else None
    if model is None:
        return None
    return frozenset(model.model_fields.keys())
