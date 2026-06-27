"""Render validated Slide IR decks to editable PowerPoint files."""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Inches, Pt

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

        for element in deck_slide.elements:
            if isinstance(element, TextElement):
                _render_text(slide, element, theme)
            elif isinstance(element, ShapeElement):
                _render_shape(slide, element, theme)
            elif isinstance(element, ImageElement):
                _render_image(slide, element, theme, assets_dir)

    presentation.save(output)
    return output
