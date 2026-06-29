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

    if word_count > 16 or char_count > 96:
        return 24, 1.48
    if word_count > 11 or char_count > 64:
        return 28, 1.16
    return 36, 0.88


def _title_slide_subtitle_metrics(subtitle: str) -> tuple[float, float]:
    char_count = len(subtitle)
    word_count = len(subtitle.split())

    if word_count > 24 or char_count > 110:
        return 13.2, 1.05
    if word_count > 16 or char_count > 76:
        return 14.5, 0.9
    return 16, 0.72


def _split_heading_body(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "", ""
    if len(lines) == 1:
        return lines[0], ""
    return lines[0], "\n".join(lines[1:])


def _short_phrase(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized

    words: list[str] = []
    for word in normalized.split():
        candidate = " ".join([*words, word])
        if len(candidate) > max_chars:
            break
        words.append(word)

    return " ".join(words)


def _safe_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


def _keywords_from_title(title: str) -> list[str]:
    stop_words = {"a", "an", "and", "for", "how", "in", "of", "the", "to", "with"}
    words = [
        word.strip(" .,:;!?")
        for word in title.split()
        if word.strip(" .,:;!?") and word.strip(" .,:;!?").lower() not in stop_words
    ]
    keywords = [word for word in words if len(word) <= 14][:3]
    return keywords or ["Overview"]


def _cover_keywords(title: str, body: list[str], deck: Deck) -> list[str]:
    source = " ".join([title, *body])
    if any("\u4e00" <= char <= "\u9fff" for char in source):
        candidates = [
            token.strip(" ，。,:;!?")
            for token in source.replace("/", " ").replace("｜", " ").split()
            if token.strip(" ，。,:;!?")
        ]
        compact = [token for token in candidates if 2 <= len(token) <= 8]
        if compact:
            return compact[:3]
        cjk_chars = [char for char in title if "\u4e00" <= char <= "\u9fff"]
        if len(cjk_chars) >= 6:
            return ["".join(cjk_chars[:4]), "".join(cjk_chars[4:8]), "".join(cjk_chars[8:12])][:3]
        return [_surface_label(deck, "Product", "产品"), _surface_label(deck, "Workflow", "工作流"), _surface_label(deck, "AI", "AI")]

    return _keywords_from_title(title)


def _is_sparse_card_text(text: str) -> bool:
    heading, body = _split_heading_body(text)
    word_count = len(" ".join([heading, body]).split())
    return word_count <= 7


def _body_lines(body_texts: list[str]) -> list[str]:
    return [
        line.strip()
        for text in body_texts
        for line in text.splitlines()
        if line.strip()
    ]


def _compact_lines(text: str, limit: int | None = None) -> list[str]:
    lines = [line.strip(" -\u2022\t") for line in text.splitlines() if line.strip(" -\u2022\t")]
    if not lines and text.strip():
        lines = [text.strip()]
    return lines[:limit] if limit is not None else lines


def _heading_body_with_fallback(text: str, fallback: str) -> tuple[str, str]:
    heading, body = _split_heading_body(text)
    if not heading:
        heading = fallback
    if not body:
        body = fallback
    return heading, body


def _is_english_language(deck: Deck) -> bool:
    marker = f"{deck.title} {deck.theme_name or ''}".lower()
    if "lang:en" in marker or "language:en" in marker:
        return True

    deck_text = " ".join(
        element.text
        for slide in deck.slides
        for element in slide.elements
        if isinstance(element, TextElement)
    )
    has_cjk_text = any("\u4e00" <= char <= "\u9fff" for char in deck_text)
    return not has_cjk_text


def _surface_label(deck: Deck, english: str, chinese: str) -> str:
    return english if _is_english_language(deck) else chinese


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
    label: str | None = None,
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

        if label:
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
    keywords = _cover_keywords(title, body, deck)
    margin = 0.8
    title_y = 1.34
    title_font_size, title_height = _title_slide_title_metrics(title)
    subtitle_font_size, subtitle_height = _title_slide_subtitle_metrics(subtitle)
    subtitle_y = title_y + title_height + 0.34
    accent_y = subtitle_y + subtitle_height + 0.34
    right_x = 8.96
    right_width = deck.canvas_width_in - right_x - 0.72
    label = _surface_label(deck, "Product Briefing", "技术产品分享")

    _add_rect(slide, x=0, y=0, width=deck.canvas_width_in, height=deck.canvas_height_in, fill_color=theme.colors.background)
    _add_rect(slide, x=right_x + 0.1, y=0.7, width=right_width, height=5.95, fill_color=theme.colors.surface, stroke_color=theme.colors.surface)
    _add_rect(slide, x=right_x + 0.48, y=1.08, width=right_width - 0.72, height=2.08, fill_color=theme.colors.background, stroke_color=theme.colors.surface, stroke_width_pt=1.0)
    _add_rect(slide, x=right_x + right_width - 0.92, y=0.36, width=0.72, height=2.1, fill_color=theme.colors.primary, stroke_color=theme.colors.primary)
    _add_rect(slide, x=right_x + 0.18, y=5.58, width=1.35, height=0.14, fill_color=theme.colors.accent)
    _add_rect(slide, x=margin, y=0.92, width=0.9, height=0.08, fill_color=theme.colors.accent)
    _add_rect(slide, x=margin, y=accent_y, width=3.6, height=0.08, fill_color=theme.colors.primary)

    _add_textbox(
        slide,
        x=margin,
        y=0.64,
        width=3.2,
        height=0.24,
        text=label,
        style=_theme_text_style(theme, font_size_pt=9.5, color=theme.colors.primary, bold=True),
    )
    _add_textbox(
        slide,
        x=margin,
        y=title_y,
        width=7.85,
        height=title_height,
        text=title,
        style=_theme_text_style(theme, font_size_pt=title_font_size, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_textbox(
        slide,
        x=margin,
        y=subtitle_y,
        width=7.58,
        height=subtitle_height,
        text=subtitle,
        style=_theme_text_style(theme, font_size_pt=subtitle_font_size, color=theme.colors.muted_text),
    )

    for index, keyword in enumerate(keywords[:3]):
        chip_width = min(2.25, max(1.0, 0.42 + len(keyword) * 0.16))
        _add_rect(
            slide,
            x=margin + index * 2.02,
            y=5.72,
            width=chip_width,
            height=0.36,
            fill_color=theme.colors.surface,
            stroke_color=theme.colors.surface,
        )
        _add_textbox(
            slide,
            x=margin + index * 2.02 + 0.16,
            y=5.82,
            width=chip_width - 0.32,
            height=0.14,
            text=_safe_text(keyword, 16),
            style=_theme_text_style(theme, font_size_pt=8.4, color=theme.colors.primary, bold=True),
        )

    hero_keyword = _safe_text(keywords[0] if keywords else title, 16)
    _add_textbox(
        slide,
        x=right_x + 0.62,
        y=1.5,
        width=right_width - 1.1,
        height=0.82,
        text=hero_keyword,
        style=_theme_text_style(theme, font_size_pt=27, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_textbox(
        slide,
        x=right_x + 0.64,
        y=2.42,
        width=right_width - 1.16,
        height=0.36,
        text=_surface_label(deck, "Workflow-ready perspective", "面向工作流的产品视角"),
        style=_theme_text_style(theme, font_size_pt=11.5, color=theme.colors.muted_text),
    )
    for index, keyword in enumerate(keywords[1:3], start=1):
        _add_textbox(
            slide,
            x=right_x + 0.62,
            y=3.58 + (index - 1) * 0.56,
            width=right_width - 1.1,
            height=0.2,
            text=f"{index:02d}  {_safe_text(keyword, 18)}",
            style=_theme_text_style(theme, font_size_pt=11, color=theme.colors.muted_text, bold=True),
        )


def _render_section_divider_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    keywords = _cover_keywords(title, body, deck)
    label = _surface_label(deck, "Section", "章节过渡")

    _add_rect(slide, x=9.35, y=0.78, width=2.9, height=5.7, fill_color=theme.colors.surface, stroke_color=theme.colors.surface)
    _add_rect(slide, x=10.0, y=1.42, width=1.45, height=1.45, fill_color=theme.colors.background, stroke_color=theme.colors.surface, stroke_width_pt=1.0)
    _add_rect(slide, x=10.78, y=4.75, width=1.18, height=0.12, fill_color=theme.colors.accent)
    _add_rect(slide, x=0.75, y=1.05, width=0.12, height=5.4, fill_color=theme.colors.primary)
    _add_textbox(
        slide,
        x=1.15,
        y=1.68,
        width=2.4,
        height=0.24,
        text=label,
        style=_theme_text_style(theme, font_size_pt=9.5, color=theme.colors.primary, bold=True),
    )
    _add_textbox(
        slide,
        x=1.15,
        y=2.1,
        width=8.0,
        height=0.9,
        text=title,
        style=_theme_text_style(theme, font_size_pt=34, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    if body:
        _add_textbox(
            slide,
            x=1.17,
            y=3.18,
            width=7.65,
            height=0.7,
            text=body[0],
            style=_theme_text_style(theme, font_size_pt=18, color=theme.colors.muted_text),
        )

    for index, keyword in enumerate(keywords[:3]):
        chip_width = min(2.1, max(1.0, 0.42 + len(keyword) * 0.16))
        _add_rect(
            slide,
            x=1.17 + index * 2.2,
            y=4.62,
            width=chip_width,
            height=0.34,
            fill_color=theme.colors.surface,
            stroke_color=theme.colors.surface,
        )
        _add_textbox(
            slide,
            x=1.31 + index * 2.2,
            y=4.71,
            width=chip_width - 0.28,
            height=0.14,
            text=_safe_text(keyword, 16),
            style=_theme_text_style(theme, font_size_pt=8.2, color=theme.colors.primary, bold=True),
        )


def _render_column_template(slide, deck_slide, deck: Deck, theme: Theme, column_count: int) -> None:
    title, body = _title_and_body_texts(deck_slide)
    margin = 0.65
    gutter = 0.28
    top = 1.62
    card_height = 3.95 if column_count == 2 else 3.65
    column_width = (deck.canvas_width_in - (margin * 2) - gutter * (column_count - 1)) / column_count
    slots = _slot_texts(body, column_count, fallback=" ")
    if column_count == 2 and all(_is_sparse_card_text(text) for text in slots):
        top = 1.84
        card_height = 2.45

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
            label=_surface_label(deck, "Insight", "洞察"),
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
            label=_surface_label(deck, "Action", "行动"),
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
            label=_surface_label(deck, "Priority", "重点"),
            heading_size_pt=18,
            body_size_pt=14,
        )


def _render_comparison_matrix_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    slots = _slot_texts(body, 3, fallback=" ")
    left_heading, left_body = _split_heading_body(slots[0])
    right_heading, right_body = _split_heading_body(slots[1])
    decision_rule = slots[2].strip()
    margin = 0.72
    table_x = margin
    table_y = 1.48
    table_width = deck.canvas_width_in - margin * 2
    dimension_width = 2.25
    column_width = (table_width - dimension_width) / 2
    header_height = 0.62
    row_height = 0.58

    _add_textbox(
        slide,
        x=margin,
        y=0.5,
        width=deck.canvas_width_in - margin * 2,
        height=0.55,
        text=title,
        style=_theme_text_style(theme, font_size_pt=26, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_rect(slide, x=margin, y=1.17, width=2.2, height=0.06, fill_color=theme.colors.primary)

    _add_rect(
        slide,
        x=table_x,
        y=table_y,
        width=table_width,
        height=header_height,
        fill_color=theme.colors.surface,
        stroke_color=theme.colors.surface,
    )
    _add_textbox(
        slide,
        x=table_x + 0.18,
        y=table_y + 0.2,
        width=dimension_width - 0.36,
        height=0.18,
        text=_surface_label(deck, "Dimension", "对比维度"),
        style=_theme_text_style(theme, font_size_pt=9.2, color=theme.colors.primary, bold=True),
    )
    for x, heading, accent, label in [
        (table_x + dimension_width, left_heading, theme.colors.primary, _surface_label(deck, "Option A", "方案 A")),
        (table_x + dimension_width + column_width, right_heading, theme.colors.secondary, _surface_label(deck, "Option B", "方案 B")),
    ]:
        _add_textbox(
            slide,
            x=x + 0.18,
            y=table_y + 0.13,
            width=0.88,
            height=0.18,
            text=label,
            style=_theme_text_style(theme, font_size_pt=8.5, color=accent, bold=True),
        )
        _add_textbox(
            slide,
            x=x + 1.08,
            y=table_y + 0.13,
            width=column_width - 1.26,
            height=0.25,
            text=_safe_text(heading or " ", 28),
            style=_theme_text_style(theme, font_size_pt=12.8, color=theme.colors.text, bold=True),
        )

    left_points = _compact_lines(left_body, 5)
    right_points = _compact_lines(right_body, 5)
    row_count = max(3, min(5, max(len(left_points), len(right_points))))
    dimensions = [
        _surface_label(deck, "Input / Output", "输入输出"),
        _surface_label(deck, "State", "状态管理"),
        _surface_label(deck, "Tool Use", "工具调用"),
        _surface_label(deck, "Failure", "失败处理"),
        _surface_label(deck, "Product Focus", "产品重点"),
    ]
    while len(left_points) < row_count:
        left_points.append("")
    while len(right_points) < row_count:
        right_points.append("")

    for row_index in range(row_count):
        y = table_y + header_height + row_index * row_height
        fill = theme.colors.background if row_index % 2 == 0 else theme.colors.surface
        _add_rect(
            slide,
            x=table_x,
            y=y,
            width=table_width,
            height=row_height,
            fill_color=fill,
            stroke_color=theme.colors.surface,
            stroke_width_pt=0.6,
        )
        _add_textbox(
            slide,
            x=table_x + 0.18,
            y=y + 0.17,
            width=dimension_width - 0.36,
            height=0.18,
            text=dimensions[row_index],
            style=_theme_text_style(theme, font_size_pt=10.2, color=theme.colors.text, bold=True),
        )
        _add_textbox(
            slide,
            x=table_x + dimension_width + 0.18,
            y=y + 0.15,
            width=column_width - 0.36,
            height=0.24,
            text=_safe_text(left_points[row_index], 54) or " ",
            style=_theme_text_style(theme, font_size_pt=10.8, color=theme.colors.muted_text),
        )
        _add_textbox(
            slide,
            x=table_x + dimension_width + column_width + 0.18,
            y=y + 0.15,
            width=column_width - 0.36,
            height=0.24,
            text=_safe_text(right_points[row_index], 54) or " ",
            style=_theme_text_style(theme, font_size_pt=10.8, color=theme.colors.muted_text),
        )

    _add_rect(
        slide,
        x=table_x + dimension_width,
        y=table_y,
        width=0.02,
        height=header_height + row_count * row_height,
        fill_color=theme.colors.background,
    )
    _add_rect(
        slide,
        x=table_x + dimension_width + column_width,
        y=table_y,
        width=0.02,
        height=header_height + row_count * row_height,
        fill_color=theme.colors.background,
    )

    if decision_rule:
        _add_rect(
            slide,
            x=margin + 0.6,
            y=5.95,
            width=deck.canvas_width_in - margin * 2 - 1.2,
            height=0.56,
            fill_color=theme.colors.surface,
            stroke_color=theme.colors.surface,
        )
        _add_textbox(
            slide,
            x=margin + 0.92,
            y=6.09,
            width=deck.canvas_width_in - margin * 2 - 1.84,
            height=0.28,
            text=_safe_text(decision_rule, 88),
            style=_theme_text_style(theme, font_size_pt=14, color=theme.colors.text, bold=True),
        )


def _render_process_flow_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    steps = _slot_texts(body, 3, fallback=" ") if len(body) < 3 else body[:5]
    margin = 0.7

    _add_textbox(
        slide,
        x=margin,
        y=0.5,
        width=deck.canvas_width_in - margin * 2,
        height=0.55,
        text=title,
        style=_theme_text_style(theme, font_size_pt=26, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_rect(slide, x=margin, y=1.17, width=2.2, height=0.06, fill_color=theme.colors.primary)

    if len(steps) <= 3:
        columns_by_row = [len(steps)]
    elif len(steps) == 4:
        columns_by_row = [2, 2]
    else:
        columns_by_row = [3, 2]

    card_height = 1.55 if len(columns_by_row) > 1 else 2.15
    row_gap = 0.52
    top = 1.72 if len(columns_by_row) > 1 else 2.05
    step_index = 0
    rows: list[list[tuple[int, float, float, float, float, str]]] = []

    for row_index, column_count in enumerate(columns_by_row):
        row_steps = steps[step_index : step_index + column_count]
        gutter = 0.34
        available_width = deck.canvas_width_in - margin * 2
        step_width = (available_width - gutter * (column_count - 1)) / column_count
        y = top + row_index * (card_height + row_gap)
        row_offset = 0.0
        if len(row_steps) == 2 and len(steps) == 5:
            step_width = (available_width - gutter * 2) / 3
            row_offset = (available_width - (step_width * 2 + gutter)) / 2

        row_positions: list[tuple[int, float, float, float, float, str]] = []
        for column_index, text in enumerate(row_steps):
            index = step_index + column_index + 1
            x = margin + row_offset + column_index * (step_width + gutter)
            row_positions.append((index, x, y, step_width, card_height, text))
        rows.append(row_positions)
        step_index += column_count

    def add_connector(x1: float, y1: float, x2: float, y2: float) -> None:
        if abs(y1 - y2) < 0.001 and x2 < x1:
            x1, x2 = x2, x1
        if abs(x1 - x2) < 0.001 and y2 < y1:
            y1, y2 = y2, y1
        connector = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            Inches(x1),
            Inches(y1),
            Inches(x2),
            Inches(y2),
        )
        connector.line.color.rgb = _rgb_color(theme.colors.surface)
        connector.line.width = Pt(1.0)

    for row_positions in rows:
        for current, following in zip(row_positions, row_positions[1:]):
            _index, x, y, width, _height, _text = current
            _next_index, next_x, _next_y, _next_width, _next_height, _next_text = following
            connector_y = y + 0.36
            add_connector(x + width + 0.06, connector_y, next_x - 0.06, connector_y)

    if len(rows) == 2 and len(steps) == 5:
        last_top = rows[0][-1]
        first_bottom = rows[1][0]
        _index, x, y, width, height, _text = last_top
        _bottom_index, bottom_x, bottom_y, _bottom_width, _bottom_height, _bottom_text = first_bottom
        turn_x = deck.canvas_width_in - margin - 0.16
        gap_y = y + height + row_gap * 0.46
        add_connector(x + width + 0.06, gap_y, turn_x, gap_y)
        add_connector(turn_x, gap_y, turn_x, bottom_y - 0.18)
        add_connector(turn_x, bottom_y - 0.18, bottom_x + 0.28, bottom_y - 0.18)

    for row_positions in rows:
        for index, x, y, step_width, card_height, text in row_positions:
            accent = theme.colors.primary if index % 2 else theme.colors.secondary
            _add_rect(
                slide,
                x=x,
                y=y,
                width=step_width,
                height=card_height,
                fill_color=theme.colors.background,
                stroke_color=theme.colors.surface,
                stroke_width_pt=1.0,
            )
            _add_rect(
                slide,
                x=x + 0.22,
                y=y + 0.18,
                width=0.58,
                height=0.36,
                fill_color=accent,
                stroke_color=accent,
            )
            _add_textbox(
                slide,
                x=x + 0.34,
                y=y + 0.26,
                width=0.34,
                height=0.16,
                text=f"{index:02d}",
                style=_theme_text_style(theme, font_size_pt=8.2, color=theme.colors.background, bold=True),
            )
            heading, body_text = _heading_body_with_fallback(
                text,
                _surface_label(deck, "Clarify the next action.", "明确下一步行动。"),
            )
            _add_textbox(
                slide,
                x=x + 0.24,
                y=y + 0.7,
                width=step_width - 0.48,
                height=0.3,
                text=_safe_text(heading, 30),
                style=_theme_text_style(theme, font_size_pt=14.6, color=theme.colors.text, bold=True),
            )
            _add_textbox(
                slide,
                x=x + 0.24,
                y=y + 1.08,
                width=step_width - 0.48,
                height=0.34,
                text=_safe_text(body_text, 58),
                style=_theme_text_style(theme, font_size_pt=10.8, color=theme.colors.muted_text),
            )


def _render_risk_matrix_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    risks = body[:4] if len(body) >= 3 else _slot_texts(body, 3, fallback=" ")
    margin = 0.7
    table_x = margin
    table_y = 1.5
    table_width = deck.canvas_width_in - margin * 2
    header_height = 0.48
    row_height = 0.92 if len(risks) <= 3 else 0.8
    columns = [0.32, 0.22, 0.46]
    widths = [table_width * ratio for ratio in columns]
    labels = [
        _surface_label(deck, "Risk", "风险"),
        _surface_label(deck, "Impact", "影响"),
        _surface_label(deck, "Mitigation", "缓解措施"),
    ]

    _add_textbox(
        slide,
        x=margin,
        y=0.5,
        width=deck.canvas_width_in - margin * 2,
        height=0.55,
        text=title,
        style=_theme_text_style(theme, font_size_pt=26, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    _add_rect(slide, x=margin, y=1.17, width=2.2, height=0.06, fill_color=theme.colors.primary)
    _add_rect(
        slide,
        x=table_x,
        y=table_y,
        width=table_width,
        height=header_height,
        fill_color=theme.colors.primary,
        stroke_color=theme.colors.primary,
    )

    x = table_x
    for label, width in zip(labels, widths):
        _add_textbox(
            slide,
            x=x + 0.18,
            y=table_y + 0.13,
            width=width - 0.36,
            height=0.18,
            text=label,
            style=_theme_text_style(theme, font_size_pt=9.5, color=theme.colors.background, bold=True),
        )
        x += width

    for row_index, risk_text in enumerate(risks):
        y = table_y + header_height + row_index * row_height
        fill = theme.colors.background if row_index % 2 == 0 else theme.colors.surface
        _add_rect(
            slide,
            x=table_x,
            y=y,
            width=table_width,
            height=row_height,
            fill_color=fill,
            stroke_color=theme.colors.surface,
            stroke_width_pt=0.8,
        )
        parts = _compact_lines(risk_text, 3)
        while len(parts) < 3:
            parts.append("")
        x = table_x
        for col_index, (text, width) in enumerate(zip(parts, widths)):
            color = theme.colors.text if col_index == 0 else theme.colors.muted_text
            max_chars = 42 if col_index == 0 else 58
            _add_textbox(
                slide,
                x=x + 0.18,
                y=y + 0.18,
                width=width - 0.36,
                height=0.42,
                text=_safe_text(text, max_chars),
                style=_theme_text_style(theme, font_size_pt=11.2, color=color, bold=col_index == 0),
            )
            x += width


def _render_key_takeaway_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    takeaways = body[:4] if len(body) >= 2 else _slot_texts(body, 2, fallback=" ")
    fallback_explanation = _surface_label(
        deck,
        "Turn this point into a concrete next action.",
        "把这一点转化为明确的下一步行动。",
    )
    main_heading, main_body = _heading_body_with_fallback(takeaways[0], fallback_explanation)
    action_items = [
        _heading_body_with_fallback(takeaway, fallback_explanation)
        for takeaway in takeaways[1:4]
    ]
    margin = 0.82

    _add_rect(slide, x=0, y=0, width=0.18, height=deck.canvas_height_in, fill_color=theme.colors.primary)
    _add_textbox(
        slide,
        x=margin,
        y=0.58,
        width=deck.canvas_width_in - margin * 2,
        height=0.44,
        text=_surface_label(deck, "Key Takeaway", "核心结论"),
        style=_theme_text_style(theme, font_size_pt=10, color=theme.colors.primary, bold=True),
    )
    _add_textbox(
        slide,
        x=margin,
        y=0.88,
        width=deck.canvas_width_in - margin * 2,
        height=0.26,
        text=title,
        style=_theme_text_style(theme, font_size_pt=11.5, color=theme.colors.muted_text, bold=True),
    )
    _add_textbox(
        slide,
        x=margin,
        y=1.28,
        width=deck.canvas_width_in - margin * 2,
        height=0.9,
        text=_safe_text(main_heading or title, 72),
        style=_theme_text_style(theme, font_size_pt=32, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    if main_body:
        _add_textbox(
            slide,
            x=margin,
            y=2.3,
            width=deck.canvas_width_in - margin * 2.3,
            height=0.56,
            text=_safe_text(main_body, 104),
            style=_theme_text_style(theme, font_size_pt=17, color=theme.colors.muted_text),
        )
    _add_rect(slide, x=margin, y=3.15, width=deck.canvas_width_in - margin * 2, height=0.04, fill_color=theme.colors.surface)

    for index, (action_title, action_body) in enumerate(action_items, start=1):
        y = 3.55 + (index - 1) * 0.64
        _add_rect(
            slide,
            x=margin,
            y=y,
            width=0.44,
            height=0.32,
            fill_color=theme.colors.surface,
            stroke_color=theme.colors.surface,
        )
        _add_textbox(
            slide,
            x=margin + 0.1,
            y=y + 0.07,
            width=0.24,
            height=0.14,
            text=f"{index}",
            style=_theme_text_style(theme, font_size_pt=8.2, color=theme.colors.primary, bold=True),
        )
        _add_textbox(
            slide,
            x=margin + 0.68,
            y=y - 0.01,
            width=deck.canvas_width_in - margin * 2 - 0.72,
            height=0.2,
            text=_safe_text(action_title, 44),
            style=_theme_text_style(theme, font_size_pt=14.5, color=theme.colors.text, bold=True),
        )
        _add_textbox(
            slide,
            x=margin + 0.68,
            y=y + 0.24,
            width=deck.canvas_width_in - margin * 2 - 0.72,
            height=0.22,
            text=_safe_text(action_body, 76),
            style=_theme_text_style(theme, font_size_pt=11.8, color=theme.colors.muted_text),
        )


def _render_closing_slide_template(slide, deck_slide, deck: Deck, theme: Theme) -> None:
    title, body = _title_and_body_texts(deck_slide)
    actions = _body_lines(body)[:3]
    _add_rect(slide, x=4.9, y=1.6, width=3.5, height=0.08, fill_color=theme.colors.accent)
    _add_textbox(
        slide,
        x=1.45,
        y=2.05 if actions else 2.35,
        width=deck.canvas_width_in - 2.9,
        height=0.9,
        text=title,
        style=_theme_text_style(theme, font_size_pt=34, color=theme.colors.text, bold=True, font_family=theme.fonts.heading),
    )
    if actions:
        for index, action in enumerate(actions, start=1):
            y = 3.15 + (index - 1) * 0.58
            _add_rect(
                slide,
                x=2.55,
                y=y + 0.02,
                width=0.58,
                height=0.3,
                fill_color=theme.colors.surface,
                stroke_color=theme.colors.surface,
            )
            _add_textbox(
                slide,
                x=2.62,
                y=y + 0.06,
                width=0.44,
                height=0.18,
                text=f"{index:02d}",
                style=_theme_text_style(theme, font_size_pt=7.5, color=theme.colors.primary, bold=True),
            )
            _add_textbox(
                slide,
                x=3.24,
                y=y - 0.01,
                width=deck.canvas_width_in - 6.1,
                height=0.38,
                text=_short_phrase(action, max_chars=56),
                style=_theme_text_style(theme, font_size_pt=16, color=theme.colors.muted_text),
            )
    elif body:
        _add_textbox(
            slide,
            x=2.2,
            y=3.35,
            width=deck.canvas_width_in - 4.4,
            height=0.7,
            text=_short_phrase(body[0], max_chars=72),
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
    elif deck_slide.layout == "comparison_matrix":
        _render_comparison_matrix_template(slide, deck_slide, deck, theme)
    elif deck_slide.layout == "process_flow":
        _render_process_flow_template(slide, deck_slide, deck, theme)
    elif deck_slide.layout == "risk_matrix":
        _render_risk_matrix_template(slide, deck_slide, deck, theme)
    elif deck_slide.layout == "key_takeaway":
        _render_key_takeaway_template(slide, deck_slide, deck, theme)


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
