"""Render validated Slide IR decks to editable PowerPoint files."""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Inches, Pt

from ppt_agent.layouts import is_template_layout
from ppt_agent.models import Deck, ImageElement, ShapeElement, SlideElement, TextElement, TextStyle
from ppt_agent.theme import Theme


def _rgb_color(hex_color: str) -> RGBColor:
    value = hex_color.removeprefix("#")
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _bbox_args(element: SlideElement) -> tuple:
    bbox = element.bbox
    return Inches(bbox.x), Inches(bbox.y), Inches(bbox.width), Inches(bbox.height)


def _apply_text_style(run, style: TextStyle) -> None:
    font = run.font

    if style.font_family:
        font.name = style.font_family
    if style.font_size_pt:
        font.size = Pt(style.font_size_pt)
    if style.color:
        font.color.rgb = _rgb_color(style.color)

    font.bold = style.bold
    font.italic = style.italic


def _write_text_to_shape(shape, text: str, style: TextStyle) -> None:
    text_frame = shape.text_frame
    text_frame.clear()

    lines = text.split("\n")
    for index, line in enumerate(lines):
        paragraph = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
        run = paragraph.add_run()
        run.text = line
        _apply_text_style(run, style)


def _render_text(slide, element: TextElement, theme: Theme) -> None:
    left, top, width, height = _bbox_args(element)
    shape = slide.shapes.add_textbox(left, top, width, height)
    text_style = element.style or theme.default_text_style
    _write_text_to_shape(shape, element.text, text_style)


def _render_shape(slide, element: ShapeElement, theme: Theme) -> None:
    left, top, width, height = _bbox_args(element)
    fill_color = element.style.fill_color if element.style and element.style.fill_color else theme.colors.surface
    stroke_color = (
        element.style.stroke_color if element.style and element.style.stroke_color else theme.colors.primary
    )
    stroke_width_pt = element.style.stroke_width_pt if element.style else None

    if element.shape == "line":
        shape = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, left, top, left + width, top + height)
        shape.line.color.rgb = _rgb_color(stroke_color)
        if stroke_width_pt:
            shape.line.width = Pt(stroke_width_pt)
        return

    shape_type = MSO_SHAPE.RECTANGLE if element.shape == "rectangle" else MSO_SHAPE.OVAL
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb_color(fill_color)
    shape.line.color.rgb = _rgb_color(stroke_color)
    if stroke_width_pt:
        shape.line.width = Pt(stroke_width_pt)


def _image_path(src: str, assets_dir: str | Path | None) -> Path:
    source_path = Path(src)
    if source_path.is_absolute() or assets_dir is None:
        return source_path
    return Path(assets_dir) / source_path


def _render_image_placeholder(slide, element: ImageElement, theme: Theme) -> None:
    left, top, width, height = _bbox_args(element)
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb_color(theme.colors.surface)
    shape.line.color.rgb = _rgb_color(theme.colors.muted_text)

    placeholder_style = TextStyle(
        font_family=theme.default_text_style.font_family or theme.fonts.body,
        font_size_pt=theme.default_text_style.font_size_pt or 14,
        color=theme.colors.muted_text,
    )
    _write_text_to_shape(shape, element.alt_text or "Image placeholder", placeholder_style)


def _render_image(slide, element: ImageElement, theme: Theme, assets_dir: str | Path | None) -> None:
    left, top, width, height = _bbox_args(element)
    path = _image_path(element.src, assets_dir)

    if path.exists():
        slide.shapes.add_picture(str(path), left, top, width=width, height=height)
        return

    _render_image_placeholder(slide, element, theme)


def _theme_text_style(
    theme: Theme,
    *,
    font_size_pt: float,
    color: str | None = None,
    bold: bool = False,
    font_family: str | None = None,
) -> TextStyle:
    return TextStyle(
        font_family=font_family or theme.fonts.body,
        font_size_pt=font_size_pt,
        color=color or theme.colors.text,
        bold=bold,
    )


def _add_textbox(
    slide,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    style: TextStyle,
) -> None:
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
    _write_text_to_shape(shape, text, style)


def _add_rect(
    slide,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    fill_color: str,
    stroke_color: str | None = None,
    stroke_width_pt: float | None = None,
):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(width), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb_color(fill_color)
    shape.line.color.rgb = _rgb_color(stroke_color or fill_color)
    if stroke_width_pt:
        shape.line.width = Pt(stroke_width_pt)
    return shape


def _template_texts(elements: list[SlideElement]) -> list[TextElement]:
    return [element for element in elements if isinstance(element, TextElement)]


def _title_and_body_texts(deck_slide) -> tuple[str, list[str]]:
    text_elements = _template_texts(deck_slide.elements)
    if not text_elements:
        return deck_slide.title, []

    title = text_elements[0].text or deck_slide.title
    body = [element.text for element in text_elements[1:] if element.text.strip()]
    return title, body


def _slot_texts(body_texts: list[str], slot_count: int, fallback: str = "") -> list[str]:
    slots = body_texts[:slot_count]
    if len(body_texts) > slot_count:
        slots[-1] = slots[-1] + "\n" + "\n".join(body_texts[slot_count:])
    while len(slots) < slot_count:
        slots.append(fallback)
    return slots


def _render_title_slide_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    subtitle = body[0] if body else deck_slide.title
    margin = 0.8

    _add_rect(
        slide,
        x=deck.canvas_width_in - 4.2,
        y=0,
        width=4.2,
        height=deck.canvas_height_in,
        fill_color=theme.colors.surface,
        stroke_color=theme.colors.surface,
    )
    _add_rect(slide, x=margin, y=3.1, width=3.4, height=0.08, fill_color=theme.colors.primary)
    _add_textbox(
        slide,
        x=margin,
        y=1.45,
        width=deck.canvas_width_in - 5.3,
        height=0.9,
        text=title,
        style=_theme_text_style(theme, font_size_pt=36, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_textbox(
        slide,
        x=margin,
        y=2.45,
        width=deck.canvas_width_in - 5.6,
        height=0.55,
        text=subtitle,
        style=_theme_text_style(theme, font_size_pt=18, color=theme.colors.muted_text),
    )


def _render_section_divider_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    _add_rect(slide, x=0.75, y=1.05, width=0.12, height=5.4, fill_color=theme.colors.primary)
    _add_textbox(
        slide,
        x=1.15,
        y=2.25,
        width=deck.canvas_width_in - 2.1,
        height=0.9,
        text=title,
        style=_theme_text_style(theme, font_size_pt=34, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    if body:
        _add_textbox(
            slide,
            x=1.17,
            y=3.25,
            width=deck.canvas_width_in - 2.3,
            height=0.7,
            text=body[0],
            style=_theme_text_style(theme, font_size_pt=18, color=theme.colors.muted_text),
        )


def _render_column_template(slide, deck_slide, deck: Deck, theme: Theme, column_count: int) -> None:
    title, body = _title_and_body_texts(deck_slide)
    margin = 0.65
    gutter = 0.28
    top = 1.55
    card_height = 4.95
    column_width = (deck.canvas_width_in - (margin * 2) - gutter * (column_count - 1)) / column_count
    slots = _slot_texts(body, column_count, fallback=" ")

    _add_textbox(
        slide,
        x=margin,
        y=0.48,
        width=deck.canvas_width_in - margin * 2,
        height=0.55,
        text=title,
        style=_theme_text_style(theme, font_size_pt=26, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_rect(slide, x=margin, y=1.16, width=2.2, height=0.06, fill_color=theme.colors.primary)

    for index, text in enumerate(slots):
        x = margin + index * (column_width + gutter)
        _add_rect(
            slide,
            x=x,
            y=top,
            width=column_width,
            height=card_height,
            fill_color=theme.colors.surface,
            stroke_color=theme.colors.surface,
        )
        _add_textbox(
            slide,
            x=x + 0.24,
            y=top + 0.32,
            width=column_width - 0.48,
            height=card_height - 0.64,
            text=text,
            style=_theme_text_style(theme, font_size_pt=16, color=theme.colors.text),
        )


def _render_metric_cards_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    margin = 0.72
    card_count = 3
    gutter = 0.32
    card_width = (deck.canvas_width_in - margin * 2 - gutter * (card_count - 1)) / card_count
    slots = _slot_texts(body, card_count, fallback=" ")

    _add_textbox(
        slide,
        x=margin,
        y=0.55,
        width=deck.canvas_width_in - margin * 2,
        height=0.55,
        text=title,
        style=_theme_text_style(theme, font_size_pt=26, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )

    for index, text in enumerate(slots):
        x = margin + index * (card_width + gutter)
        _add_rect(
            slide,
            x=x,
            y=1.75,
            width=card_width,
            height=3.4,
            fill_color=theme.colors.surface,
            stroke_color=theme.colors.secondary,
            stroke_width_pt=1.2,
        )
        _add_rect(slide, x=x, y=1.75, width=card_width, height=0.12, fill_color=theme.colors.primary)
        _add_textbox(
            slide,
            x=x + 0.28,
            y=2.18,
            width=card_width - 0.56,
            height=2.15,
            text=text,
            style=_theme_text_style(theme, font_size_pt=18, color=theme.colors.text, bold=True),
        )


def _render_closing_slide_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    subtitle = body[0] if body else ""
    _add_rect(slide, x=4.9, y=1.6, width=3.5, height=0.08, fill_color=theme.colors.accent)
    _add_textbox(
        slide,
        x=1.45,
        y=2.35,
        width=deck.canvas_width_in - 2.9,
        height=0.9,
        text=title,
        style=_theme_text_style(theme, font_size_pt=34, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    if subtitle:
        _add_textbox(
            slide,
            x=2.2,
            y=3.35,
            width=deck.canvas_width_in - 4.4,
            height=0.7,
            text=subtitle,
            style=_theme_text_style(theme, font_size_pt=18, color=theme.colors.muted_text),
        )


def _render_template_slide(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    if deck_slide.layout == "title_slide":
        _render_title_slide_template(slide, deck_slide, deck, theme)
    elif deck_slide.layout == "section_divider":
        _render_section_divider_template(slide, deck_slide, deck, theme)
    elif deck_slide.layout == "two_column":
        _render_column_template(slide, deck_slide, deck, theme, column_count=2)
    elif deck_slide.layout == "three_column":
        _render_column_template(slide, deck_slide, deck, theme, column_count=3)
    elif deck_slide.layout == "metric_cards":
        _render_metric_cards_template(slide, deck_slide, deck, theme)
    elif deck_slide.layout == "closing_slide":
        _render_closing_slide_template(slide, deck_slide, deck, theme)


def render_deck_to_pptx(
    deck: Deck,
    theme: Theme,
    output_path: str | Path,
    assets_dir: str | Path | None = None,
) -> Path:
    """Render a validated deck into an editable PowerPoint file."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    presentation = Presentation()
    presentation.slide_width = Inches(deck.canvas_width_in)
    presentation.slide_height = Inches(deck.canvas_height_in)
    blank_layout = presentation.slide_layouts[6]

    for deck_slide in deck.slides:
        slide = presentation.slides.add_slide(blank_layout)
        background_fill = slide.background.fill
        background_fill.solid()
        background_fill.fore_color.rgb = _rgb_color(theme.colors.background)

        if is_template_layout(deck_slide.layout):
            _render_template_slide(slide, deck_slide, deck, theme)
        else:
            for element in deck_slide.elements:
                if isinstance(element, TextElement):
                    _render_text(slide, element, theme)
                elif isinstance(element, ShapeElement):
                    _render_shape(slide, element, theme)
                elif isinstance(element, ImageElement):
                    _render_image(slide, element, theme, assets_dir)

    presentation.save(output)
    return output
