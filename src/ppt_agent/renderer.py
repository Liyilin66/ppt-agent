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
):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
    _write_text_to_shape(shape, text, style)
    return shape


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


def _title_slide_title_metrics(title: str) -> tuple[float, float]:
    word_count = len(title.split())
    char_count = len(title)

    if word_count > 12 or char_count > 82:
        return 26, 1.65
    if word_count > 9 or char_count > 58:
        return 30, 1.32
    return 36, 0.9


def _split_heading_body(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "", ""
    if len(lines) == 1:
        return lines[0], ""
    return lines[0], "\n".join(lines[1:])


def _keyword_from_title(title: str) -> str:
    words = [word.strip(" .,:;!?") for word in title.split() if word.strip(" .,:;!?")]
    if not words:
        return "PPT"
    return " ".join(words[:2]).upper()


def _render_heading_body_card(
    slide,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    theme: Theme,
    accent_color: str | None = None,
    number: int | None = None,
    label: str = "KEY POINT",
    heading_size_pt: float = 18,
    body_size_pt: float = 13.5,
) -> None:
    heading, body = _split_heading_body(text)
    accent = accent_color or theme.colors.primary
    padding_x = 0.24

    _add_rect(
        slide,
        x=x,
        y=y,
        width=width,
        height=height,
        fill_color=theme.colors.background,
        stroke_color=theme.colors.surface,
        stroke_width_pt=1.0,
    )
    _add_rect(slide, x=x, y=y, width=width, height=0.08, fill_color=accent)

    if number is not None:
        chip_width = 0.62
        chip_height = 0.28
        chip_x = x + width - padding_x - chip_width
        chip_y = y + 0.24
        _add_rect(
            slide,
            x=chip_x,
            y=chip_y,
            width=chip_width,
            height=chip_height,
            fill_color=theme.colors.surface,
            stroke_color=theme.colors.surface,
        )
        _add_textbox(
            slide,
            x=chip_x + 0.08,
            y=chip_y + 0.03,
            width=chip_width - 0.16,
            height=0.18,
            text=f"{number:02d}",
            style=_theme_text_style(theme, font_size_pt=8.5, color=accent, bold=True),
        )

        _add_textbox(
            slide,
            x=x + padding_x,
            y=y + 0.22,
            width=max(0.7, width - padding_x * 2 - chip_width - 0.12),
            height=0.22,
            text=label,
            style=_theme_text_style(theme, font_size_pt=7.5, color=accent, bold=True),
        )

    _add_textbox(
        slide,
        x=x + padding_x,
        y=y + 0.58,
        width=width - padding_x * 2,
        height=0.45,
        text=heading,
        style=_theme_text_style(theme, font_size_pt=heading_size_pt, color=theme.colors.text, bold=True),
    )
    if body:
        _add_textbox(
            slide,
            x=x + padding_x,
            y=y + 1.16,
            width=width - padding_x * 2,
            height=max(0.4, height - 1.38),
            text=body,
            style=_theme_text_style(theme, font_size_pt=body_size_pt, color=theme.colors.muted_text),
        )


def _render_title_slide_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    subtitle = body[0] if body else deck_slide.title
    margin = 0.8
    title_y = 1.18
    title_font_size, title_height = _title_slide_title_metrics(title)
    subtitle_y = title_y + title_height + 0.35
    subtitle_height = 0.62
    accent_y = subtitle_y + subtitle_height + 0.28

    _add_rect(
        slide,
        x=deck.canvas_width_in - 4.2,
        y=0,
        width=4.2,
        height=deck.canvas_height_in,
        fill_color=theme.colors.surface,
        stroke_color=theme.colors.surface,
    )
    _add_textbox(
        slide,
        x=deck.canvas_width_in - 3.75,
        y=1.0,
        width=3.0,
        height=0.35,
        text="TEMPLATE-GUIDED",
        style=_theme_text_style(theme, font_size_pt=10, color=theme.colors.primary, bold=True),
    )
    _add_textbox(
        slide,
        x=deck.canvas_width_in - 3.75,
        y=1.55,
        width=3.05,
        height=1.35,
        text=_keyword_from_title(title),
        style=_theme_text_style(theme, font_size_pt=34, color=theme.colors.muted_text, bold=True, font_family=theme.fonts.heading),
    )
    _add_rect(slide, x=deck.canvas_width_in - 3.75, y=3.16, width=1.05, height=0.08, fill_color=theme.colors.accent)
    _add_textbox(
        slide,
        x=deck.canvas_width_in - 3.75,
        y=3.55,
        width=3.0,
        height=0.42,
        text="Editable PPTX",
        style=_theme_text_style(theme, font_size_pt=14, color=theme.colors.text, bold=True),
    )
    _add_rect(slide, x=margin, y=accent_y, width=3.4, height=0.08, fill_color=theme.colors.primary)
    _add_textbox(
        slide,
        x=margin,
        y=title_y,
        width=deck.canvas_width_in - 5.3,
        height=title_height,
        text=title,
        style=_theme_text_style(theme, font_size_pt=title_font_size, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_textbox(
        slide,
        x=margin,
        y=subtitle_y,
        width=deck.canvas_width_in - 5.6,
        height=subtitle_height,
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
    top = 1.62
    card_height = 3.95 if column_count == 2 else 3.65
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
        _render_heading_body_card(
            slide,
            x=x,
            y=top,
            width=column_width,
            height=card_height,
            text=text,
            theme=theme,
            accent_color=theme.colors.primary if index % 2 == 0 else theme.colors.secondary,
            number=index + 1,
            label="COLUMN",
            heading_size_pt=18,
            body_size_pt=13.5,
        )


def _render_four_cards_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    margin = 0.72
    gutter_x = 0.34
    gutter_y = 0.34
    card_width = (deck.canvas_width_in - margin * 2 - gutter_x) / 2
    card_height = 1.9
    top = 1.48
    slots = _slot_texts(body, 4, fallback=" ")

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

    accents = [theme.colors.primary, theme.colors.secondary, theme.colors.accent, theme.colors.primary]
    for index, text in enumerate(slots):
        row = index // 2
        column = index % 2
        _render_heading_body_card(
            slide,
            x=margin + column * (card_width + gutter_x),
            y=top + row * (card_height + gutter_y),
            width=card_width,
            height=card_height,
            text=text,
            theme=theme,
            accent_color=accents[index],
            number=index + 1,
            label="CARD",
            heading_size_pt=17,
            body_size_pt=12.8,
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
        _render_heading_body_card(
            slide,
            x=x + 0.28,
            y=1.68,
            width=card_width - 0.56,
            height=2.75,
            text=text,
            theme=theme,
            accent_color=theme.colors.primary,
            number=index + 1,
            label="METRIC",
            heading_size_pt=18,
            body_size_pt=14,
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
    elif deck_slide.layout == "four_cards":
        _render_four_cards_template(slide, deck_slide, deck, theme)
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
