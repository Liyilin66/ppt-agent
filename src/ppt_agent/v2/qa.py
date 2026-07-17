"""Rule-based QA for PageDesigns, with deterministic auto-fixes first.

Order of defense: (1) schema validation already rejected malformed output,
(2) deterministic fixes here repair what code can safely repair (contrast,
chrome-zone intrusion), (3) only unfixable issues — real overflow, colliding
text — are escalated to one LLM repair round by the orchestrator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ppt_agent.models import StrictModel
from ppt_agent.v2.design import (
    TYPE_SCALE,
    ThemeSpec,
    best_text_color,
    contrast_ratio,
)
from ppt_agent.v2.icons import ICON_GLYPHS
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ChartItem,
    Frame,
    IconItem,
    PageDesign,
    ShapeItem,
    TableItem,
    TextItem,
)
from ppt_agent.v2.metrics import estimated_overflow_ratio, fit_font_size


SAFE_TOP = 56.0
SAFE_BOTTOM = CANVAS_HEIGHT - 56.0
OVERFLOW_WARNING = 0.05
OVERFLOW_ERROR = 0.35
TEXT_OVERLAP_IOU = 0.15
# Charts/tables auto-render their own text; anything drawn on top collides.
HEAVY_OVERLAP_RATIO = 0.12
# python-pptx grows table rows to fit ~10.5pt text + margins; below this many
# canvas units per row the table overflows its declared frame downwards.
TABLE_MIN_ROW_UNITS = 28.0
# Content pages whose text/icons/charts/tables cover less of the content zone
# than this read as half-empty slides.
SPARSE_COVERAGE_ERROR = 0.20
BOTTOM_EMPTY_COVERAGE = 0.08

Severity = Literal["warning", "error"]


class QAIssue(StrictModel):
    code: str
    severity: Severity
    message: str
    element_id: str | None = None


class PageQAResult(StrictModel):
    page_number: int
    issues: list[QAIssue] = Field(default_factory=list)
    auto_fixes: list[str] = Field(default_factory=list)

    @property
    def errors(self) -> list[QAIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def passed(self) -> bool:
        return not self.errors


def _effective_background_hex(page: PageDesign, item: TextItem, theme: ThemeSpec) -> str:
    """Best guess of what sits behind a text frame: its card, or the page."""

    center_x = item.frame.x + item.frame.w / 2
    center_y = item.frame.y + item.frame.h / 2
    backdrop: str | None = None
    for element in page.elements:
        if element is item:
            break
        if not isinstance(element, ShapeItem):
            continue
        frame = element.frame
        if not (frame.x <= center_x <= frame.right and frame.y <= center_y <= frame.bottom):
            continue
        if element.gradient is not None:
            backdrop = theme.palette.resolve(element.gradient.start)
        elif element.fill is not None and element.fill_alpha >= 0.5:
            backdrop = theme.palette.resolve(element.fill)
    if backdrop is not None:
        return backdrop
    if page.background_gradient is not None:
        return theme.palette.resolve(page.background_gradient.start)
    return theme.palette.resolve(page.background)


def _overlap_area(a: Frame, b: Frame) -> float:
    width = min(a.right, b.right) - max(a.x, b.x)
    height = min(a.bottom, b.bottom) - max(a.y, b.y)
    return max(0.0, width) * max(0.0, height)


def _frame_area(frame: Frame) -> float:
    return frame.w * frame.h


def _content_coverage(elements: list, *, y_from: float, y_to: float) -> float:
    """Fraction of the given horizontal band covered by real content frames."""

    boxes = [
        element.frame
        for element in elements
        if isinstance(element, (TextItem, IconItem, ChartItem, TableItem))
    ]
    if not boxes or y_to <= y_from:
        return 0.0
    columns, rows = 32, 12
    x_from, x_to = 64.0, CANVAS_WIDTH - 64.0
    covered = 0
    for column in range(columns):
        px = x_from + (column + 0.5) * (x_to - x_from) / columns
        for row in range(rows):
            py = y_from + (row + 0.5) * (y_to - y_from) / rows
            if any(
                box.x <= px <= box.right and box.y <= py <= box.bottom for box in boxes
            ):
                covered += 1
    return covered / (columns * rows)


def _shift_into_safe_zone(frame: Frame) -> Frame | None:
    """Move a frame out of the chrome strips if it fits; None when impossible."""

    y = frame.y
    if frame.y < SAFE_TOP:
        y = SAFE_TOP
    if y + frame.h > SAFE_BOTTOM:
        y = SAFE_BOTTOM - frame.h
    if y < SAFE_TOP or y + frame.h > SAFE_BOTTOM:
        return None
    if y == frame.y:
        return frame
    return frame.model_copy(update={"y": y})


def review_page(page: PageDesign, theme: ThemeSpec) -> tuple[PageDesign, PageQAResult]:
    """Apply deterministic fixes and report remaining issues."""

    issues: list[QAIssue] = []
    fixes: list[str] = []
    elements = list(page.elements)

    # Chrome-zone intrusion: shift content out of the reserved strips.
    if page.show_chrome:
        for index, element in enumerate(elements):
            frame = getattr(element, "frame", None)
            if frame is None:
                continue
            if frame.y >= SAFE_TOP - 26 and frame.bottom <= SAFE_BOTTOM + 26:
                continue
            shifted = _shift_into_safe_zone(frame)
            if shifted is not None and shifted is not frame:
                elements[index] = element.model_copy(update={"frame": shifted})
                fixes.append(f"moved '{element.id}' into the safe zone")

    text_items = [
        (index, element)
        for index, element in enumerate(elements)
        if isinstance(element, TextItem)
    ]

    # Contrast: recolor unreadable text to the readable token for its backdrop.
    for index, item in text_items:
        working_page = page.model_copy(update={"elements": elements})
        background_hex = _effective_background_hex(working_page, item, theme)
        spec = TYPE_SCALE[item.role]
        color_hex = theme.palette.resolve(item.color or spec.default_color)
        threshold = 3.0 if (item.size_pt or spec.size_pt) >= 18 else 4.5
        if contrast_ratio(color_hex, background_hex) < threshold:
            replacement = best_text_color(theme.palette, background_hex)
            elements[index] = item.model_copy(update={"color": replacement})
            fixes.append(
                f"recolored '{item.id}' to '{replacement}' for contrast"
            )

    # Overflow: renderer autoshrinks to the role minimum; beyond that, escalate.
    for index, item in text_items:
        item = elements[index]  # may have been recolored
        if not isinstance(item, TextItem):
            continue
        final_size = fit_font_size(
            item.text,
            role=item.role,
            frame_width_units=item.frame.w,
            frame_height_units=item.frame.h,
            requested_size_pt=item.size_pt,
        )
        overflow = estimated_overflow_ratio(
            item.text,
            role=item.role,
            size_pt=final_size,
            frame_width_units=item.frame.w,
            frame_height_units=item.frame.h,
        )
        if overflow > OVERFLOW_ERROR:
            issues.append(
                QAIssue(
                    code="text_overflow",
                    severity="error",
                    element_id=item.id,
                    message=(
                        f"Text in '{item.id}' overflows its frame by "
                        f"~{overflow:.0%} even at minimum size; shorten the text "
                        "or enlarge the frame "
                        f"(frame {item.frame.w:.0f}x{item.frame.h:.0f}, "
                        f"{len(item.text)} chars)"
                    ),
                )
            )
        elif overflow > OVERFLOW_WARNING:
            issues.append(
                QAIssue(
                    code="text_tight",
                    severity="warning",
                    element_id=item.id,
                    message=f"Text in '{item.id}' is tight (~{overflow:.0%} over)",
                )
            )

    # Colliding text frames are a layout defect the model must resolve.
    for a_position, (index_a, _) in enumerate(text_items):
        for index_b, _ in text_items[a_position + 1 :]:
            item_a, item_b = elements[index_a], elements[index_b]
            if not (isinstance(item_a, TextItem) and isinstance(item_b, TextItem)):
                continue
            iou = item_a.frame.intersection_over_union(item_b.frame)
            if iou > TEXT_OVERLAP_IOU:
                issues.append(
                    QAIssue(
                        code="text_overlap",
                        severity="error",
                        element_id=item_a.id,
                        message=(
                            f"Text frames '{item_a.id}' and '{item_b.id}' overlap "
                            f"(IoU {iou:.2f}); separate them"
                        ),
                    )
                )

    # Charts and tables render their own labels; overlapping elements collide.
    heavy_items = [
        element for element in elements if isinstance(element, (ChartItem, TableItem))
    ]
    for heavy in heavy_items:
        for element in elements:
            if element is heavy:
                continue
            if not isinstance(element, (TextItem, ChartItem, TableItem, IconItem)):
                continue
            overlap = _overlap_area(heavy.frame, element.frame)
            smaller = min(_frame_area(heavy.frame), _frame_area(element.frame))
            if smaller > 0 and overlap / smaller > HEAVY_OVERLAP_RATIO:
                issues.append(
                    QAIssue(
                        code="heavy_overlap",
                        severity="error",
                        element_id=heavy.id,
                        message=(
                            f"'{element.id}' overlaps the {heavy.type} '{heavy.id}' "
                            f"({overlap / smaller:.0%} of the smaller frame); charts and "
                            "tables need their own clear region — move the element away "
                            "or drop it"
                        ),
                    )
                )

    # Tables physically grow to fit their text; a frame that allots less than
    # ~28 units per row will overflow downwards over whatever sits below it.
    for element in elements:
        if not isinstance(element, TableItem):
            continue
        rows = len(element.rows) + 1
        needed = rows * TABLE_MIN_ROW_UNITS
        if needed > element.frame.h * 1.1:
            issues.append(
                QAIssue(
                    code="table_overflow",
                    severity="error",
                    element_id=element.id,
                    message=(
                        f"Table '{element.id}' has {rows} rows in a "
                        f"{element.frame.h:.0f}-unit-tall frame; it will render "
                        f"~{needed:.0f} units tall and spill over elements below — "
                        "enlarge the frame to at least that height or remove rows"
                    ),
                )
            )

    # Half-empty content pages: measure how much of the content zone actual
    # content (text/icons/charts/tables) covers, overall and in the bottom half.
    if page.role not in ("cover", "toc", "section_divider", "closing"):
        coverage = _content_coverage(elements, y_from=SAFE_TOP + 84, y_to=SAFE_BOTTOM)
        bottom_coverage = _content_coverage(
            elements, y_from=(SAFE_TOP + SAFE_BOTTOM) / 2, y_to=SAFE_BOTTOM
        )
        if coverage < SPARSE_COVERAGE_ERROR:
            issues.append(
                QAIssue(
                    code="layout_sparse",
                    severity="error",
                    message=(
                        f"Content covers only ~{coverage:.0%} of the usable canvas; "
                        "large regions are empty — enlarge cards and typography, add "
                        "supporting structure, and spread content across the page"
                    ),
                )
            )
        elif bottom_coverage < BOTTOM_EMPTY_COVERAGE and coverage < 0.5:
            issues.append(
                QAIssue(
                    code="bottom_half_empty",
                    severity="error",
                    message=(
                        "The bottom half of the slide is almost empty while the top is "
                        "loaded — rebalance the layout vertically or enlarge the content"
                    ),
                )
            )

    # Density bounds.
    if page.role == "content" and len(elements) < 3:
        issues.append(
            QAIssue(
                code="too_sparse",
                severity="error",
                message=f"Only {len(elements)} elements; add structure (cards, icons, data)",
            )
        )
    elif len(elements) > 24:
        issues.append(
            QAIssue(
                code="too_dense",
                severity="warning",
                message=f"{len(elements)} elements; consider simplifying",
            )
        )

    # Unknown icons degrade to a dot; surface it so the model can pick better.
    for element in elements:
        if isinstance(element, IconItem) and element.name.strip().lower() not in ICON_GLYPHS:
            issues.append(
                QAIssue(
                    code="unknown_icon",
                    severity="warning",
                    element_id=element.id,
                    message=f"Icon '{element.name}' is not in the catalog; a dot is used",
                )
            )

    fixed_page = page.model_copy(update={"elements": elements})
    return fixed_page, PageQAResult(
        page_number=page.page_number, issues=issues, auto_fixes=fixes
    )


class DeckQASummary(StrictModel):
    total_pages: int
    pages_with_errors: int
    pages_with_warnings: int
    auto_fix_count: int
    repaired_pages: list[int] = Field(default_factory=list)
    fallback_pages: list[int] = Field(default_factory=list)
    results: list[PageQAResult] = Field(default_factory=list)


def summarize(results: list[PageQAResult], *, repaired: list[int], fallback: list[int]) -> DeckQASummary:
    return DeckQASummary(
        total_pages=len(results),
        pages_with_errors=sum(1 for result in results if result.errors),
        pages_with_warnings=sum(
            1 for result in results if result.issues and not result.errors
        ),
        auto_fix_count=sum(len(result.auto_fixes) for result in results),
        repaired_pages=sorted(repaired),
        fallback_pages=sorted(fallback),
        results=results,
    )
