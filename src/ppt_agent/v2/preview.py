"""Debug preview: DeckDesign -> standalone HTML/SVG pages.

An approximate but faithful browser rendering of the PageDesign IR (same
frames, colors, and type scale as the PPTX renderer) so layout and styling
can be reviewed without opening PowerPoint. Charts are simplified sketches.
"""

from __future__ import annotations

import html
from pathlib import Path

from ppt_agent.v2.design import TYPE_SCALE, ThemeSpec
from ppt_agent.v2.icons import resolve_icon
from ppt_agent.v2.ir import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ChartItem,
    DeckDesign,
    IconItem,
    ImageItem,
    LineItem,
    PageDesign,
    ShapeItem,
    TableItem,
    TextItem,
)
from ppt_agent.v2.metrics import UNITS_PER_PT, fit_font_size


def _c(theme: ThemeSpec, role: str | None, fallback: str = "text") -> str:
    return theme.palette.resolve(role or fallback)


def _svg_text(item: TextItem, theme: ThemeSpec) -> str:
    spec = TYPE_SCALE[item.role]
    size_pt = fit_font_size(
        item.text,
        role=item.role,
        frame_width_units=item.frame.w,
        frame_height_units=item.frame.h,
        requested_size_pt=item.size_pt,
    )
    size_units = size_pt * UNITS_PER_PT
    color = _c(theme, item.color or spec.default_color)
    bold = item.bold if item.bold is not None else spec.bold
    weight = 700 if bold else 400
    align_css = {"left": "flex-start", "center": "center", "right": "flex-end"}[item.align]
    valign_css = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}[item.valign]
    lines = []
    for index, line in enumerate(item.text.split("\n")):
        if item.bullet == "dot" and line.strip():
            line = f"•  {line}"
        elif item.bullet == "number" and line.strip():
            line = f"{index + 1}.  {line}"
        lines.append(html.escape(line) or "&nbsp;")
    content = "<br/>".join(lines)
    style = (
        f"position:absolute;left:{item.frame.x}px;top:{item.frame.y}px;"
        f"width:{item.frame.w}px;height:{item.frame.h}px;display:flex;"
        f"align-items:{valign_css};justify-content:{align_css};"
        f"font-size:{size_units}px;line-height:{spec.line_spacing};color:{color};"
        f"font-weight:{weight};{'font-style:italic;' if item.italic else ''}"
        f"text-align:{item.align};overflow:hidden;"
    )
    return f'<div style="{style}"><span>{content}</span></div>'


def _svg_shape(item: ShapeItem, theme: ThemeSpec) -> str:
    radius = 0.0
    if item.shape == "rounded_rectangle":
        radius = theme.corner_radius
    elif item.shape == "pill":
        radius = min(item.frame.w, item.frame.h) / 2
    elif item.shape == "ellipse":
        radius = max(item.frame.w, item.frame.h)
    if item.gradient is not None:
        background = (
            f"background:linear-gradient({item.gradient.angle_deg + 90}deg,"
            f"{_c(theme, item.gradient.start)},{_c(theme, item.gradient.end)});"
        )
    elif item.fill is not None:
        background = f"background:{_c(theme, item.fill)};"
    else:
        background = "background:transparent;"
    border = (
        f"border:{item.stroke_width}px solid {_c(theme, item.stroke)};"
        if item.stroke
        else ""
    )
    style = (
        f"position:absolute;left:{item.frame.x}px;top:{item.frame.y}px;"
        f"width:{item.frame.w}px;height:{item.frame.h}px;{background}{border}"
        f"border-radius:{radius}px;opacity:{item.fill_alpha};"
        f"transform:rotate({item.rotation_deg}deg);"
    )
    return f'<div style="{style}"></div>'


def _svg_line(item: LineItem, theme: ThemeSpec) -> str:
    dash = 'stroke-dasharray="6 5"' if item.dash else ""
    return (
        f'<svg style="position:absolute;left:0;top:0;pointer-events:none" '
        f'width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}">'
        f'<line x1="{item.x1}" y1="{item.y1}" x2="{item.x2}" y2="{item.y2}" '
        f'stroke="{_c(theme, item.color)}" stroke-width="{item.width * UNITS_PER_PT}" {dash}/></svg>'
    )


def _svg_icon(item: IconItem, theme: ThemeSpec) -> str:
    glyph, monochrome = resolve_icon(item.name)
    parts = []
    if item.background and item.background_shape != "none":
        radius = "50%" if item.background_shape == "circle" else f"{theme.corner_radius}px"
        parts.append(
            f'<div style="position:absolute;left:{item.frame.x}px;top:{item.frame.y}px;'
            f"width:{item.frame.w}px;height:{item.frame.h}px;"
            f'background:{_c(theme, item.background)};border-radius:{radius};"></div>'
        )
    color = f"color:{_c(theme, item.color)};" if monochrome else ""
    parts.append(
        f'<div style="position:absolute;left:{item.frame.x}px;top:{item.frame.y}px;'
        f"width:{item.frame.w}px;height:{item.frame.h}px;display:flex;align-items:center;"
        f'justify-content:center;font-size:{item.frame.h * 0.55}px;{color}">{glyph}</div>'
    )
    return "".join(parts)


def _svg_chart(item: ChartItem, theme: ThemeSpec) -> str:
    """Simplified bar sketch standing in for the native PPTX chart."""

    colors = [theme.palette.primary, theme.palette.secondary, theme.palette.accent]
    max_value = max(max(series.values) for series in item.series) or 1
    bars = []
    count = len(item.categories)
    slot_width = item.frame.w / count
    for index, category in enumerate(item.categories):
        value = item.series[0].values[index]
        bar_height = (item.frame.h - 40) * value / max_value
        bar_width = slot_width * 0.55
        x = item.frame.x + index * slot_width + (slot_width - bar_width) / 2
        y = item.frame.y + (item.frame.h - 24) - bar_height
        bars.append(
            f'<div style="position:absolute;left:{x}px;top:{y}px;width:{bar_width}px;'
            f"height:{bar_height}px;background:{colors[index % 3]};"
            f'border-radius:4px 4px 0 0;"></div>'
            f'<div style="position:absolute;left:{item.frame.x + index * slot_width}px;'
            f"top:{item.frame.y + item.frame.h - 22}px;width:{slot_width}px;"
            f"text-align:center;font-size:12px;color:{theme.palette.muted};"
            f'overflow:hidden;">{html.escape(category)}</div>'
        )
    return "".join(bars)


def _svg_table(item: TableItem, theme: ThemeSpec) -> str:
    rows_html = []
    header_cells = "".join(
        f'<th style="background:{theme.palette.primary};color:{theme.palette.on_primary};'
        f'padding:6px 10px;font-size:13px;text-align:left;">{html.escape(cell)}</th>'
        for cell in item.headers
    )
    rows_html.append(f"<tr>{header_cells}</tr>")
    for row_index, row in enumerate(item.rows):
        band = theme.palette.surface_alt if row_index % 2 else theme.palette.surface
        cells = "".join(
            f'<td style="background:{band};color:{theme.palette.text};padding:6px 10px;'
            f'font-size:12.5px;">{html.escape(cell)}</td>'
            for cell in row
        )
        rows_html.append(f"<tr>{cells}</tr>")
    return (
        f'<table style="position:absolute;left:{item.frame.x}px;top:{item.frame.y}px;'
        f"width:{item.frame.w}px;border-collapse:collapse;\">{''.join(rows_html)}</table>"
    )


def _svg_image(item: ImageItem, theme: ThemeSpec) -> str:
    return (
        f'<div style="position:absolute;left:{item.frame.x}px;top:{item.frame.y}px;'
        f"width:{item.frame.w}px;height:{item.frame.h}px;background:{theme.palette.surface_alt};"
        f"border:1.5px dashed {theme.palette.muted};border-radius:{theme.corner_radius}px;"
        f"display:flex;align-items:center;justify-content:center;font-size:14px;"
        f'color:{theme.palette.muted};">🖼 {html.escape(item.label)}</div>'
    )


def page_to_html(page: PageDesign, deck: DeckDesign) -> str:
    theme = deck.theme
    if page.background_gradient is not None:
        background = (
            f"background:linear-gradient({page.background_gradient.angle_deg + 90}deg,"
            f"{_c(theme, page.background_gradient.start)},"
            f"{_c(theme, page.background_gradient.end)});"
        )
    else:
        background = f"background:{_c(theme, page.background)};"
    parts = []
    for element in page.elements:
        if isinstance(element, TextItem):
            parts.append(_svg_text(element, theme))
        elif isinstance(element, ShapeItem):
            parts.append(_svg_shape(element, theme))
        elif isinstance(element, LineItem):
            parts.append(_svg_line(element, theme))
        elif isinstance(element, IconItem):
            parts.append(_svg_icon(element, theme))
        elif isinstance(element, ChartItem):
            parts.append(_svg_chart(element, theme))
        elif isinstance(element, TableItem):
            parts.append(_svg_table(element, theme))
        elif isinstance(element, ImageItem):
            parts.append(_svg_image(element, theme))
    chrome = ""
    if page.show_chrome and page.role not in ("cover", "closing"):
        chrome = (
            f'<div style="position:absolute;left:64px;top:684px;font-size:13px;'
            f'color:{theme.palette.muted};">{html.escape(deck.deck_title)}</div>'
            f'<div style="position:absolute;right:64px;top:684px;font-size:13px;'
            f'color:{theme.palette.muted};">{page.page_number:02d}</div>'
        )
        if page.section and page.role not in ("toc", "section_divider"):
            chrome += (
                f'<div style="position:absolute;left:64px;top:26px;font-size:14px;'
                f"font-weight:700;letter-spacing:1px;color:{theme.palette.primary};\">"
                f"{html.escape(page.section.upper())}</div>"
            )
    font_stack = (
        f"'{theme.fonts.body_latin}','{theme.fonts.body_east_asian}','PingFang SC',sans-serif"
    )
    return (
        f'<div class="page" style="position:relative;width:{CANVAS_WIDTH}px;'
        f"height:{CANVAS_HEIGHT}px;{background}overflow:hidden;"
        f'font-family:{font_stack};">{"".join(parts)}{chrome}</div>'
    )


def deck_to_html(deck: DeckDesign, output_path: str | Path) -> Path:
    """Write one scrollable HTML file with every page of the deck."""

    pages = "\n".join(page_to_html(page, deck) for page in deck.pages)
    document = (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{html.escape(deck.deck_title)}</title>"
        "<body style='background:#333;margin:0;display:flex;flex-direction:column;"
        "align-items:center;gap:24px;padding:24px;'>"
        f"{pages}</body>"
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output


def page_to_standalone_html(page: PageDesign, deck: DeckDesign, output_path: str | Path) -> Path:
    document = (
        "<!doctype html><meta charset='utf-8'><body style='margin:0'>"
        + page_to_html(page, deck)
        + "</body>"
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output


def page_to_embedded_html(page: PageDesign, deck: DeckDesign) -> str:
    """Return a responsive HTML document suitable for an iframe preview."""

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>html,body{width:100%;height:100%;margin:0;overflow:hidden;background:#eef3f5;}"
        ".preview-stage{position:absolute;left:0;top:0;width:1280px;height:720px;"
        "transform-origin:top left;}</style></head><body>"
        '<div class="preview-stage">'
        + page_to_html(page, deck)
        + "</div><script>const stage=document.querySelector('.preview-stage');"
        "function fit(){const scale=Math.min(innerWidth/1280,innerHeight/720);"
        "stage.style.transform=`scale(${scale})`;"
        "stage.style.left=`${(innerWidth-1280*scale)/2}px`;"
        "stage.style.top=`${(innerHeight-720*scale)/2}px`;};"
        "addEventListener('resize',fit);fit();</script></body></html>"
    )
