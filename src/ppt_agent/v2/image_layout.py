"""Deterministic typesetter for image-derived slides.

The model that reads a source image returns content only — a title, headline
numbers, a few sections, an optional caveat, an optional crop box. Every frame
on the page is then computed here.

Why not let the model place elements: asking a vision model for pixel
coordinates produces frames that are the wrong size for their text (4pt-tall
boxes holding a sentence), cards of identical visual weight, and copy squeezed
against the canvas edge. Measuring the text and dividing the canvas is both
cheaper and better. The model keeps the judgement calls it is actually good at.

Four archetypes cover what source images turn out to be:

  spec_cards  a subject with measurable specs — headline numbers, a photo,
              explanatory sections
  points      explanation or advice with no headline numbers
  compare     two alternatives, sides, or before/after states
  flow        an ordered process, timeline or set of steps
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from ppt_agent.models import StrictModel
from ppt_agent.v2.design import ThemeSpec
from ppt_agent.v2.icons import canonical_icon_name
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    Frame,
    IconItem,
    ImageItem,
    LineItem,
    PageDesign,
    PageElement,
    ShapeItem,
    TextItem,
)
from ppt_agent.v2.metrics import TYPE_SCALE, fit_font_size, text_height_units


ImageLayout = Literal["spec_cards", "points", "compare", "flow"]

MARGIN = 64.0
CONTENT_W = CANVAS_WIDTH - 2 * MARGIN
TOP = 52.0
BOTTOM = CANVAS_HEIGHT - 40.0
GAP = 16.0

# Routes whose slides are typeset here rather than positioned by the model.
TEMPLATE_ROUTES = frozenset({"design_from_content", "extract_text", "embed_with_notes"})


class CropBox(StrictModel):
    x: float = Field(..., ge=0, le=1)
    y: float = Field(..., ge=0, le=1)
    w: float = Field(..., gt=0, le=1)
    h: float = Field(..., gt=0, le=1)


class SlideFact(StrictModel):
    value: str = Field(..., min_length=1, max_length=40)
    label: str = Field(default="", max_length=60)


class SlideSection(StrictModel):
    heading: str = Field(..., min_length=1, max_length=60)
    icon: str = "dot"
    body: str = Field(default="", max_length=300)


class ComparePane(StrictModel):
    heading: str = Field(..., min_length=1, max_length=60)
    icon: str = "dot"
    points: list[str] = Field(default_factory=list)


class SlideCallout(StrictModel):
    heading: str = Field(default="", max_length=60)
    points: list[str] = Field(default_factory=list)


class ImageSlideContent(StrictModel):
    """What the model is allowed to decide about an image-derived slide."""

    title: str = Field(..., min_length=1, max_length=120)
    kicker: str = Field(default="", max_length=60)
    subtitle: str = Field(default="", max_length=200)
    layout: ImageLayout = "points"
    facts: list[SlideFact] = Field(default_factory=list)
    sections: list[SlideSection] = Field(default_factory=list)
    compare: list[ComparePane] = Field(default_factory=list)
    callout: SlideCallout | None = None
    subject_crop: CropBox | None = None
    speaker_notes: str = ""

    def effective_layout(self) -> ImageLayout:
        """The layout we can actually draw, given what content arrived.

        The model picks a layout and then sometimes fails to fill the fields it
        needs, so the choice is re-derived from the content itself.
        """

        if self.layout == "compare" and len(self.compare) >= 2:
            return "compare"
        if self.layout == "spec_cards" and self.facts:
            return "spec_cards"
        if self.layout == "flow" and len(self.sections) >= 2:
            return "flow"
        if self.facts and len(self.sections) >= 2:
            return "spec_cards"
        return "points"


# A "subject" occupying more of the source than this is not a subject, it is a
# slab of the page — and on a screenshot that slab carries back exactly the
# interface and promotional content the extraction step worked to leave out.
MAX_SUBJECT_AREA_FRACTION = 0.30
MAX_SUBJECT_ASPECT = 3.0

# Mask resolution for blob detection. Small enough to be cheap, large enough
# that a product photo and a line of text are clearly different sizes.
_MASK_EDGE_PX = 240
_BACKGROUND_TOLERANCE = 18

# A subject has to be more than a speck of the box that was pointed at, which
# rules out the single glyph a box aimed at a paragraph would otherwise yield.
# Kept low on purpose: a correct subject inside a loose box is only a few
# percent of it, and the colour-variety check below is what actually rejects
# type.
_MIN_BLOB_AREA_FRACTION = 0.03

# A photograph has many colours; a glyph or a flat badge has two or three. This
# separates a real subject from type that happens to form a large blob.
_MIN_DISTINCT_COLOURS = 16


def _has_photographic_variety(region: "object") -> bool:
    """Whether a region looks like a photo rather than rendered type."""

    from PIL import Image

    assert isinstance(region, Image.Image)
    scale = min(1.0, 120 / max(region.size))
    # Nearest neighbour: interpolation would blend two-tone type into a gradient
    # and invent exactly the variety this check is looking for.
    small = region.resize(
        (max(1, int(region.width * scale)), max(1, int(region.height * scale))),
        Image.NEAREST,
    )
    # Quantise to 4 bits per channel so JPEG noise does not count as variety.
    pixels = small.convert("RGB").load()
    buckets = {
        (pixels[x, y][0] >> 4, pixels[x, y][1] >> 4, pixels[x, y][2] >> 4)
        for y in range(small.height)
        for x in range(small.width)
    }
    return len(buckets) >= _MIN_DISTINCT_COLOURS


def _largest_blob_bounds(mask: "list[list[bool]]") -> tuple[int, int, int, int] | None:
    """Bounds of the largest 4-connected run of True cells, or None if empty.

    Iterative flood fill: a product photo is one big blob, while text is many
    small ones, so picking the largest blob separates a subject from the copy
    printed beside it.
    """

    height = len(mask)
    width = len(mask[0]) if height else 0
    seen = [[False] * width for _ in range(height)]
    best_size = 0
    best: tuple[int, int, int, int] | None = None

    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y][start_x] or seen[start_y][start_x]:
                continue
            stack = [(start_x, start_y)]
            seen[start_y][start_x] = True
            size = 0
            min_x = max_x = start_x
            min_y = max_y = start_y
            while stack:
                x, y = stack.pop()
                size += 1
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
                for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= next_x < width and 0 <= next_y < height:
                        if mask[next_y][next_x] and not seen[next_y][next_x]:
                            seen[next_y][next_x] = True
                            stack.append((next_x, next_y))
            if size > best_size:
                best_size = size
                best = (min_x, min_y, max_x + 1, max_y + 1)
    return best


def _subject_bounds(region: "object") -> tuple[float, float, float, float] | None:
    """Locate the subject inside a candidate region, as 0-1 fractions of it.

    Returns None when the region has no uniform backdrop to separate a subject
    from — on busy full-bleed artwork there is nothing to isolate.
    """

    from PIL import Image

    assert isinstance(region, Image.Image)
    scale = min(1.0, _MASK_EDGE_PX / max(region.size))
    small = region.resize(
        (max(1, int(region.width * scale)), max(1, int(region.height * scale)))
    )
    pixels = small.load()
    backdrop = pixels[0, 0]

    mask = [
        [
            sum(abs(pixels[x, y][band] - backdrop[band]) for band in range(3)) / 3
            > _BACKGROUND_TOLERANCE
            for x in range(small.width)
        ]
        for y in range(small.height)
    ]
    foreground = sum(sum(row) for row in mask)
    if not foreground:
        return None
    # Almost everything differs from the corner: no backdrop, nothing to isolate.
    if foreground > 0.85 * small.width * small.height:
        return None

    bounds = _largest_blob_bounds(mask)
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    if max_x - min_x < 3 or max_y - min_y < 3:
        return None
    blob_area = (max_x - min_x) * (max_y - min_y)
    if blob_area < _MIN_BLOB_AREA_FRACTION * small.width * small.height:
        return None
    return (
        min_x / small.width,
        min_y / small.height,
        max_x / small.width,
        max_y / small.height,
    )


def refine_crop(source: Path, box: CropBox, destination: Path, *, expand: float = 0.35) -> bool:
    """Tighten the model's crop box onto its subject; refuse it when unsure.

    Vision models place these boxes loosely — a box that clips the subject in
    half is common — so the box is widened and then trimmed back to the content
    bounds inside it. Over-expanding is the safe direction *provided the trim
    bites*: on a subject sitting on a card, the trim removes what the expansion
    picked up.

    On a full-bleed graphic there is no uniform border to trim, and the widened
    box would survive intact. That case is detected and the model's original box
    is used instead; a box that is still implausibly large or oddly shaped for a
    subject is refused outright, leaving the slide without a photo rather than
    with a screenshot fragment on it.
    """

    from PIL import Image

    try:
        image = Image.open(source).convert("RGB")
    except OSError:
        return False

    width, height = image.size

    # Judge the model's own box before refining it. A box covering a third of a
    # screenshot, or shaped like a banner, means no subject was identified —
    # refining it would just produce a tidy crop of the wrong thing.
    if box.w * box.h > MAX_SUBJECT_AREA_FRACTION:
        return False
    box_px = (box.w * width, box.h * height)
    if min(box_px) <= 0 or max(box_px) / min(box_px) > MAX_SUBJECT_ASPECT:
        return False

    def to_pixels(crop: CropBox, factor: float) -> tuple[int, int, int, int]:
        centre_x, centre_y = crop.x + crop.w / 2, crop.y + crop.h / 2
        half_w, half_h = crop.w * (1 + factor) / 2, crop.h * (1 + factor) / 2
        return (
            max(0, int((centre_x - half_w) * width)),
            max(0, int((centre_y - half_h) * height)),
            min(width, int((centre_x + half_w) * width)),
            min(height, int((centre_y + half_h) * height)),
        )

    wide = to_pixels(box, expand)
    if wide[2] - wide[0] < 8 or wide[3] - wide[1] < 8:
        return False
    candidate = image.crop(wide)

    bounds = _subject_bounds(candidate)
    if bounds is None:
        # No backdrop to separate a subject from — the box sits on busy artwork,
        # such as a promotional poster. Taking the model's word for it here is
        # what put price banners and gift-with-purchase strips on the slide as
        # pixels, so the crop is given up instead. The page reads fine without a
        # photo; it does not read fine with a slice of a storefront on it.
        return False

    # Pad relative to the subject we found, not to how wide a net the expansion
    # cast — otherwise a loose box keeps a loose margin.
    span_x = (bounds[2] - bounds[0]) * candidate.width
    span_y = (bounds[3] - bounds[1]) * candidate.height
    pad_x, pad_y = span_x * 0.04, span_y * 0.04
    region = candidate.crop(
        (
            max(0, int(bounds[0] * candidate.width - pad_x)),
            max(0, int(bounds[1] * candidate.height - pad_y)),
            min(candidate.width, int(bounds[2] * candidate.width + pad_x)),
            min(candidate.height, int(bounds[3] * candidate.height + pad_y)),
        )
    )

    if region.width < 8 or region.height < 8:
        return False
    if region.width * region.height > MAX_SUBJECT_AREA_FRACTION * width * height:
        return False
    longest, shortest = max(region.size), min(region.size)
    if shortest == 0 or longest / shortest > MAX_SUBJECT_ASPECT:
        return False
    if not _has_photographic_variety(region):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    region.save(destination)
    return True


def _needed_height(text: str, role: str, width: float) -> float:
    spec = TYPE_SCALE[role]
    return text_height_units(text, spec.size_pt, spec.line_spacing, width)


def _text(
    element_id: str,
    text: str,
    *,
    frame: Frame,
    role: str,
    color: str | None = None,
    bullet: str = "none",
    align: str = "left",
) -> TextItem:
    """A text item whose font size is already known to fit its frame."""

    return TextItem(
        id=element_id,
        frame=frame,
        text=text,
        role=role,
        color=color,
        bullet=bullet,
        align=align,
        size_pt=fit_font_size(
            text, role=role, frame_width_units=frame.w, frame_height_units=frame.h
        ),
    )


def _header(content: ImageSlideContent) -> tuple[list[PageElement], float]:
    """Kicker, title, subtitle and a rule; returns the elements and next y."""

    elements: list[PageElement] = []
    title_w = CONTENT_W - 40
    title_h = max(48.0, _needed_height(content.title, "title", title_w))
    has_kicker = bool(content.kicker)
    title_y = TOP + (28 if has_kicker else 0)

    elements.append(
        ShapeItem(
            id="hdr_stripe",
            frame=Frame(x=MARGIN, y=TOP + 4, w=6, h=title_h + (26 if has_kicker else 0)),
            shape="rectangle",
            fill="primary",
        )
    )
    if has_kicker:
        elements.append(
            _text("hdr_kicker", content.kicker,
                  frame=Frame(x=MARGIN + 22, y=TOP, w=560, h=24), role="kicker")
        )
    elements.append(
        _text("hdr_title", content.title,
              frame=Frame(x=MARGIN + 22, y=title_y, w=title_w, h=title_h), role="title")
    )
    y = title_y + title_h + 10

    if content.subtitle:
        sub_h = max(26.0, _needed_height(content.subtitle, "body", title_w))
        elements.append(
            _text("hdr_sub", content.subtitle,
                  frame=Frame(x=MARGIN + 22, y=y + 4, w=title_w, h=sub_h),
                  role="body", color="muted")
        )
        y += sub_h + 14

    elements.append(
        LineItem(id="hdr_rule", x1=MARGIN, y1=y, x2=CANVAS_WIDTH - MARGIN, y2=y,
                 color="muted", width=1)
    )
    return elements, y + 18


def _callout(content: ImageSlideContent) -> tuple[list[PageElement], float]:
    """The bottom caveat band; returns the elements and the y it starts at."""

    callout = content.callout
    if callout is None or not callout.points:
        return [], BOTTOM

    text = "\n".join(point.rstrip("。.") for point in callout.points[:3])
    body_w = CONTENT_W - 84
    height = max(84.0, _needed_height(text, "body_small", body_w) + 52)
    y = BOTTOM - height
    elements: list[PageElement] = [
        ShapeItem(id="callout_bg", frame=Frame(x=MARGIN, y=y, w=CONTENT_W, h=height),
                  shape="rounded_rectangle", fill="primary_soft"),
        ShapeItem(id="callout_bar", frame=Frame(x=MARGIN, y=y, w=5, h=height),
                  shape="rectangle", fill="warning"),
        IconItem(id="callout_icon", frame=Frame(x=MARGIN + 22, y=y + 18, w=30, h=30),
                 name="warning", color="warning", background=None, background_shape="none"),
        _text("callout_head", callout.heading or "提醒",
              frame=Frame(x=MARGIN + 62, y=y + 18, w=420, h=26), role="h3"),
        _text("callout_body", text,
              frame=Frame(x=MARGIN + 62, y=y + 44, w=body_w, h=height - 52),
              role="body_small", bullet="dot"),
    ]
    return elements, y - 20


def _fact_row(facts: list[SlideFact], y: float) -> tuple[list[PageElement], float]:
    elements: list[PageElement] = []
    count = len(facts)
    gap = 18.0
    width = (CONTENT_W - gap * (count - 1)) / count
    height = 88.0
    for index, fact in enumerate(facts):
        x = MARGIN + index * (width + gap)
        elements.extend([
            ShapeItem(id=f"fact_bg_{index}", frame=Frame(x=x, y=y, w=width, h=height),
                      shape="rounded_rectangle", fill="surface"),
            ShapeItem(id=f"fact_bar_{index}", frame=Frame(x=x, y=y, w=4, h=height),
                      shape="rectangle", fill="primary" if index % 2 == 0 else "secondary"),
            _text(f"fact_val_{index}", fact.value,
                  frame=Frame(x=x + 20, y=y + 18, w=width - 36, h=40), role="stat"),
        ])
        if fact.label:
            elements.append(
                _text(f"fact_lab_{index}", fact.label,
                      frame=Frame(x=x + 20, y=y + 60, w=width - 36, h=22),
                      role="stat_label", color="muted")
            )
    return elements, y + height + 20


# Row internals, measured from the type scale rather than guessed: an h3 line is
# 27.2 units tall and a body_small line 19.1 (15.6 at its minimum size), so a
# row needs 44 units for a heading alone and about 68 for a heading and one line
# of body. Reserving more than that silently dropped body text from rows that
# had room for it.
ROW_PAD = 8.0
ROW_HEAD_H = 28.0
ROW_HEAD_GAP = 4.0
MIN_ROW_H = ROW_HEAD_H + 2 * ROW_PAD
MIN_BODY_H = 16.0


def _section_rows(
    sections: list[SlideSection], *, x: float, y: float, width: float, bottom: float,
    numbered: bool = False,
) -> list[PageElement]:
    """Stacked icon + heading + body rows filling the space exactly.

    When the page is crowded — headline numbers, four sections and a caveat band
    all at once — sections are given up before body text is. Three rows that say
    something beat four bare labels, so the row count is reduced until the rows
    that remain have room to explain themselves. Only when even two rows cannot
    does the page fall back to headings alone.
    """

    def row_height(count: int) -> float:
        return (bottom - y - GAP * (count - 1)) / count

    def body_height(count: int) -> float:
        return row_height(count) - 2 * ROW_PAD - ROW_HEAD_H - ROW_HEAD_GAP

    elements: list[PageElement] = []
    count = len(sections)
    # Prefer fewer rows that keep their body text, down to a floor of two.
    while count > 2 and body_height(count) < MIN_BODY_H:
        count -= 1
    # If even that does not fit, headings alone — but they must still fit.
    while count > 1 and row_height(count) < MIN_ROW_H:
        count -= 1
    sections = sections[:count]
    each = max(MIN_ROW_H, row_height(count))
    body_h = each - 2 * ROW_PAD - ROW_HEAD_H - ROW_HEAD_GAP
    show_body = body_h >= MIN_BODY_H

    for index, section in enumerate(sections):
        row_y = y + index * (each + GAP)
        elements.append(
            ShapeItem(id=f"sec_bg_{index}", frame=Frame(x=x, y=row_y, w=width, h=each),
                      shape="rounded_rectangle", fill="surface")
        )
        badge_size = min(44.0, each - 12)
        badge = Frame(x=x + 20, y=row_y + (each - badge_size) / 2, w=badge_size, h=badge_size)
        if numbered:
            elements.extend([
                ShapeItem(id=f"sec_num_bg_{index}", frame=badge,
                          shape="rounded_rectangle", fill="primary"),
                _text(f"sec_num_{index}", f"{index + 1}",
                      frame=badge, role="h3", color="on_primary", align="center"),
            ])
        else:
            elements.append(
                IconItem(id=f"sec_icon_{index}", frame=badge,
                         name=canonical_icon_name(section.icon), color="primary",
                         background="primary_soft", background_shape="rounded")
            )
        text_x = x + 82
        text_w = width - 102
        if show_body and section.body:
            # Centre the heading + body block on the icon; pinning it to the top
            # of a tall row leaves the text floating above a centred badge.
            text_h = min(body_h, _needed_height(section.body, "body_small", text_w))
            head_y = row_y + (each - (ROW_HEAD_H + ROW_HEAD_GAP + text_h)) / 2
            body_y = head_y + ROW_HEAD_H + ROW_HEAD_GAP
            elements.extend([
                _text(f"sec_head_{index}", section.heading,
                      frame=Frame(x=text_x, y=head_y, w=text_w, h=ROW_HEAD_H),
                      role="h3"),
                _text(f"sec_body_{index}", section.body,
                      frame=Frame(x=text_x, y=body_y, w=text_w,
                                  h=row_y + each - ROW_PAD - body_y),
                      role="body_small", color="muted"),
            ])
        else:
            elements.append(
                _text(f"sec_head_{index}", section.heading,
                      frame=Frame(x=text_x, y=row_y + (each - ROW_HEAD_H) / 2,
                                  w=text_w, h=ROW_HEAD_H),
                      role="h3")
            )
    return elements


def _image_card(
    x: float, y: float, width: float, height: float, src: str
) -> list[PageElement]:
    pad = 22.0
    return [
        ShapeItem(id="img_card", frame=Frame(x=x, y=y, w=width, h=height),
                  shape="rounded_rectangle", fill="surface"),
        ImageItem(id="subject_img",
                  frame=Frame(x=x + pad, y=y + pad, w=width - 2 * pad, h=height - 2 * pad),
                  src=src, label="主图"),
    ]


def _compare_columns(panes: list[ComparePane], y: float, bottom: float) -> list[PageElement]:
    elements: list[PageElement] = []
    gap = 24.0
    width = (CONTENT_W - gap) / 2
    height = bottom - y
    # Both panes share one heading height so the two columns stay aligned.
    heading_w = width - 102
    # Pane headings are subheads: the section-divider scale would outrank the
    # page title.
    heading_h = max(
        40.0,
        *(_needed_height(pane.heading, "h3", heading_w) for pane in panes[:2]),
    )
    body_y = y + 30 + heading_h + 18
    for index, pane in enumerate(panes[:2]):
        x = MARGIN + index * (width + gap)
        accent = "primary" if index == 0 else "secondary"
        elements.extend([
            ShapeItem(id=f"cmp_bg_{index}", frame=Frame(x=x, y=y, w=width, h=height),
                      shape="rounded_rectangle", fill="surface"),
            ShapeItem(id=f"cmp_bar_{index}", frame=Frame(x=x, y=y, w=width, h=5),
                      shape="rectangle", fill=accent),
            IconItem(id=f"cmp_icon_{index}",
                     frame=Frame(x=x + 24, y=y + 30 + (heading_h - 40) / 2, w=40, h=40),
                     name=canonical_icon_name(pane.icon), color="primary",
                     background="primary_soft", background_shape="rounded"),
            _text(f"cmp_head_{index}", pane.heading,
                  frame=Frame(x=x + 78, y=y + 30, w=heading_w, h=heading_h), role="h3"),
        ])
        if pane.points:
            text = "\n".join(point.rstrip("。.") for point in pane.points[:4])
            elements.append(
                _text(f"cmp_body_{index}", text,
                      frame=Frame(x=x + 26, y=body_y, w=width - 52, h=y + height - 24 - body_y),
                      role="body", bullet="dot")
            )
    return elements


def build_slide(
    content: ImageSlideContent,
    *,
    page_number: int,
    image_src: str | None = None,
) -> PageDesign:
    """Typeset one image-derived slide. ``image_src`` is drawn when provided."""

    elements, y = _header(content)
    callout_elements, body_bottom = _callout(content)
    layout = content.effective_layout()

    # Two columns of points leave no room for a picture, so a page that must
    # show one cannot use the comparison archetype.
    if layout == "compare" and image_src is not None:
        layout = "spec_cards" if content.facts else "points"

    if layout == "compare":
        elements.extend(_compare_columns(content.compare, y, body_bottom))
    else:
        if layout == "spec_cards" and content.facts:
            fact_elements, y = _fact_row(content.facts[:4], y)
            elements.extend(fact_elements)

        column_x, column_w = MARGIN, CONTENT_W
        if image_src is not None:
            image_w = 268.0
            elements.extend(_image_card(MARGIN, y, image_w, body_bottom - y, image_src))
            column_x = MARGIN + image_w + 28
            column_w = CANVAS_WIDTH - MARGIN - column_x

        sections = content.sections[:4] or [
            SlideSection(heading=content.title, body=content.subtitle)
        ]
        elements.extend(
            _section_rows(sections, x=column_x, y=y, width=column_w,
                          bottom=body_bottom, numbered=layout == "flow")
        )

    elements.extend(callout_elements)
    return PageDesign(
        page_number=page_number,
        role="content",
        title=content.title,
        background="background",
        show_chrome=False,
        elements=elements,
        speaker_notes=content.speaker_notes or content.title,
    )
