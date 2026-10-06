"""Structural pages — cover, table of contents, section divider, closing — per profile.

These pages carry no model-written content: everything comes from the
skeleton (deck title, subtitle, section titles and their page numbers), so
they are fully deterministic. Each profile gets its own treatment so the
first and last impression match the content pages instead of a generic
template.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ppt_agent.v2.ir import CANVAS_HEIGHT, CANVAS_WIDTH, PageDesign
from ppt_agent.v2.metrics import text_width_units
from ppt_agent.v2.visual.layout import MIN_BODY_PT, _Builder, _height
from ppt_agent.v2.visual.profiles import StyleProfile

StructuralKind = Literal["cover", "toc", "section_divider", "closing"]
Section = tuple[str, int]  # (title, first page number)

W, H = CANVAS_WIDTH, CANVAS_HEIGHT


def _fit(text: str, role: str, size: float, width: float, max_h: float, floor: float) -> float:
    """Largest size <= ``size`` whose block fits ``max_h`` at ``width``."""

    while size > floor and _height(text, role, size, width) > max_h:
        size -= 1.0
    return size


def _shape(b: _Builder) -> str:
    return "rounded_rectangle" if b.profile.card_radius else "rectangle"


# --------------------------------------------------------------------------
# Cover
# --------------------------------------------------------------------------


def _cover(b: _Builder, title: str, subtitle: str | None, kicker: str,
           sections: Sequence[Section]) -> None:
    p = b.profile
    s = p.sizes
    name = p.name
    if name == "consulting":
        x, w = 96.0, 860.0
        size = _fit(title, "title", 40, w, 150, 28)
        th = _height(title, "title", size, w)
        sh = _height(subtitle, "subtitle", s.lead + 3, w) if subtitle else 0
        kh = _height(kicker, "kicker", s.kicker + 2, w)
        block = kh + 18 + th + (20 + sh if sh else 0)
        y = (H - block) / 2 - 20
        b.shape(x=72, y=y, w=6, h=block, fill="primary")
        b.text(kicker, x=x, y=y, w=w, h=kh, role="kicker", size=s.kicker + 2, color="accent")
        b.text(title, x=x, y=y + kh + 18, w=w, h=th, role="title", size=size, color="primary")
        if subtitle:
            b.text(subtitle, x=x, y=y + kh + 18 + th + 20, w=w, h=sh, role="subtitle",
                   size=s.lead + 3, color="muted")
        b.line(72, H - 96, W - 72, H - 96, color="surface_alt", width=0.75)
    elif name == "corporate":
        panel = W * 0.6
        b.shape(x=0, y=0, w=panel, h=H, fill="primary")
        x, w = 88.0, panel - 88 - 64
        size = _fit(title, "title", 40, w, 170, 28)
        th = _height(title, "title", size, w)
        sh = _height(subtitle, "subtitle", s.lead + 2, w) if subtitle else 0
        y = H * 0.36
        b.text(kicker, x=x, y=y - 46, w=w, h=26, role="kicker", size=s.kicker + 2,
               color="primary_soft")
        b.text(title, x=x, y=y, w=w, h=th, role="title", size=size, color="on_primary")
        b.shape(x=x, y=y + th + 22, w=56, h=5, fill="accent")
        if subtitle:
            b.text(subtitle, x=x, y=y + th + 50, w=w, h=sh, role="subtitle", size=s.lead + 2,
                   color="primary_soft")
        if sections:
            rx, rw = panel + 64, W - panel - 64 - 72
            b.text("本次汇报", x=rx, y=H * 0.3 - 40, w=rw, h=24, role="kicker",
                   size=s.kicker + 2, color="muted")
            y = H * 0.3
            for index, (sec, _) in enumerate(sections[:6]):
                hh = _height(sec, "h3", s.heading - 1, rw - 44)
                b.text(f"{index + 1:02d}", x=rx, y=y, w=40, h=hh, role="h3", size=s.heading - 1,
                       color="secondary")
                b.text(sec, x=rx + 44, y=y, w=rw - 44, h=hh, role="h3", size=s.heading - 1,
                       color="text")
                y += hh + 18
    elif name == "launch":
        w = 980.0
        x = (W - w) / 2
        size = _fit(title, "title", 54, w, 180, 34)
        th = _height(title, "title", size, w)
        sh = _height(subtitle, "subtitle", s.lead + 2, w) if subtitle else 0
        block = 30 + 26 + th + (30 + sh if sh else 0)
        y = (H - block) / 2
        b.text(kicker, x=x, y=y, w=w, h=26, role="kicker", size=s.kicker + 1, color="secondary",
               align="center")
        b.shape(x=W / 2 - 24, y=y + 34, w=48, h=3, fill="primary")
        b.text(title, x=x, y=y + 56, w=w, h=th, role="title", size=size, color="text",
               align="center")
        if subtitle:
            b.text(subtitle, x=x, y=y + 56 + th + 30, w=w, h=sh, role="subtitle",
                   size=s.lead + 2, color="muted", align="center")
    else:  # training
        x, w = 88.0, 900.0
        pill_w = text_width_units(kicker, s.kicker + 1) + 40
        size = _fit(title, "title", 44, w, 160, 30)
        th = _height(title, "title", size, w)
        sh = _height(subtitle, "subtitle", s.lead + 2, w) if subtitle else 0
        y = 150.0
        b.shape(x=x, y=y, w=pill_w, h=34, shape="pill", fill="primary_soft")
        b.text(kicker, x=x + 20, y=y, w=pill_w - 40, h=34, role="kicker", size=s.kicker + 1,
               color="primary", valign="middle")
        b.text(title, x=x, y=y + 60, w=w, h=th, role="title", size=size, color="text")
        if subtitle:
            b.text(subtitle, x=x, y=y + 60 + th + 18, w=w, h=sh, role="subtitle",
                   size=s.lead + 2, color="muted")
        band_y = H - 150
        b.shape(x=0, y=band_y, w=W, h=150, fill="primary")
        if sections:
            n = min(len(sections), 4)
            col = (W - 2 * x - 24 * (n - 1)) / n
            for index, (sec, _) in enumerate(sections[:n]):
                cx = x + index * (col + 24)
                b.text(f"第 {index + 1} 章", x=cx, y=band_y + 34, w=col, h=22, role="kicker",
                       size=s.kicker, color="primary_soft")
                sz = _fit(sec, "h3", s.heading - 2, col, 52, MIN_BODY_PT)
                b.text(sec, x=cx, y=band_y + 62, w=col, h=_height(sec, "h3", sz, col),
                       role="h3", size=sz, color="on_primary")


# --------------------------------------------------------------------------
# Table of contents
# --------------------------------------------------------------------------


def _toc(b: _Builder, sections: Sequence[Section]) -> None:
    p = b.profile
    s = p.sizes
    name = p.name
    x0 = p.margin_x
    width = W - 2 * x0
    title_size = s.title
    b.text("目录", x=x0, y=p.title_top, w=400, h=_height("目录", "title", title_size, 400),
           role="title", size=title_size, color="text")
    top = p.title_top + 96
    n = len(sections)
    if name == "launch":
        per_row = min(n, 4)
        rows = (n + per_row - 1) // per_row
        col = (width - 32 * (per_row - 1)) / per_row
        row_h = min(200.0, (H - top - 80) / max(rows, 1))
        for index, (sec, page) in enumerate(sections):
            r, c = divmod(index, per_row)
            x = x0 + c * (col + 32)
            y = top + r * row_h
            num_h = _height("00", "stat", 44, col)
            b.text(f"{index + 1:02d}", x=x, y=y, w=col, h=num_h, role="stat", size=44,
                   color="primary")
            sz = _fit(sec, "h3", s.heading, col, row_h - num_h - 20, MIN_BODY_PT)
            b.text(sec, x=x, y=y + num_h + 6, w=col, h=_height(sec, "h3", sz, col), role="h3",
                   size=sz, color="text")
        return
    cols = 2 if n > 4 else 1
    gap = 56.0
    col = (width - gap * (cols - 1)) / cols
    per_col = (n + cols - 1) // cols
    row_h = min(78.0, (H - top - 80) / max(per_col, 1))
    for index, (sec, page) in enumerate(sections):
        c, r = divmod(index, per_col)
        x = x0 + c * (col + gap)
        y = top + r * row_h
        num_w = 64.0
        page_w = 60.0
        text_w = col - num_w - page_w - 24
        sz = _fit(sec, "h3", s.heading + 1, text_w, row_h - 22, MIN_BODY_PT)
        inner_h = row_h - 16
        if name == "corporate":
            b.shape(x=x, y=y, w=col, h=row_h - 12, shape=_shape(b), fill="surface",
                    stroke="surface_alt")
            b.shape(x=x + 18, y=y + (row_h - 12 - 32) / 2, w=32, h=32, shape=_shape(b),
                    fill="primary_soft")
            b.text(f"{index + 1}", x=x + 18, y=y + (row_h - 12 - 32) / 2, w=32, h=32, role="h3",
                   size=min(17.0, s.marker), color="primary", align="center", valign="middle")
            b.text(sec, x=x + num_w, y=y, w=text_w, h=row_h - 12, role="h3", size=sz,
                   color="text", valign="middle")
            b.text(f"P{page:02d}", x=x + col - page_w - 16, y=y, w=page_w, h=row_h - 12,
                   role="caption", size=s.caption + 1, color="muted", align="right",
                   valign="middle")
            continue
        if name == "training":
            b.shape(x=x, y=y + (inner_h - 40) / 2, w=40, h=40, shape="ellipse", fill="primary")
            b.text(f"{index + 1}", x=x, y=y + (inner_h - 40) / 2, w=40, h=40, role="h3",
                   size=18, color="on_primary", align="center", valign="middle")
        else:
            b.text(f"{index + 1:02d}", x=x, y=y, w=num_w, h=inner_h, role="h3",
                   size=s.heading + 1, color="accent", valign="middle")
        b.text(sec, x=x + num_w, y=y, w=text_w, h=inner_h, role="h3", size=sz, color="text",
               valign="middle")
        b.text(f"{page:02d}", x=x + col - page_w, y=y, w=page_w, h=inner_h, role="caption",
               size=s.caption + 1, color="muted", align="right", valign="middle")
        b.line(x, y + row_h - 8, x + col, y + row_h - 8, color="surface_alt", width=0.75)


# --------------------------------------------------------------------------
# Section divider and closing
# --------------------------------------------------------------------------


def _divider(b: _Builder, index: int, title: str) -> None:
    p = b.profile
    s = p.sizes
    filled = p.name in ("corporate", "training")
    if filled:
        b.shape(x=0, y=0, w=W, h=H, fill="primary")
    x, w = 120.0, 900.0
    number = f"{index:02d}"
    on = "on_primary" if filled else "text"
    num_color = "primary_soft" if filled else ("accent" if p.name == "consulting" else "primary")
    num_h = _height(number, "stat", 96, 300)
    top = H * 0.24
    b.text(number, x=x, y=top, w=300, h=num_h, role="stat", size=96, color=num_color)
    size = _fit(title, "title", 40, w, 130, 28)
    rule_y = top + num_h + 8
    if p.name == "consulting":
        b.shape(x=x, y=rule_y, w=64, h=4, fill="primary")
    b.text(title, x=x, y=rule_y + 24, w=w, h=_height(title, "title", size, w), role="title",
           size=size, color=on)


def _closing(b: _Builder, deck_title: str) -> None:
    p = b.profile
    s = p.sizes
    filled = p.name == "corporate"
    if filled:
        b.shape(x=0, y=0, w=W, h=H, fill="primary")
    centered = p.name in ("launch", "training")
    thanks = "感谢参与" if p.name == "training" else "谢谢"
    on = "on_primary" if filled else "text"
    w = 900.0
    x = (W - w) / 2 if centered else 120.0
    align = "center" if centered else "left"
    th = _height(thanks, "title", 56, w)
    dh = _height(deck_title, "subtitle", s.lead + 2, w)
    y = (H - th - 28 - dh) / 2
    if p.name == "consulting":
        b.shape(x=x - 24, y=y, w=6, h=th + 28 + dh, fill="primary")
    b.text(thanks, x=x, y=y, w=w, h=th, role="title", size=56,
           color=on if p.name != "consulting" else "primary", align=align)
    b.text(deck_title, x=x, y=y + th + 28, w=w, h=dh, role="subtitle", size=s.lead + 2,
           color="primary_soft" if filled else "muted", align=align)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def typeset_structural(
    kind: StructuralKind,
    profile: StyleProfile,
    *,
    page_number: int,
    deck_title: str,
    subtitle: str | None = None,
    kicker: str | None = None,
    sections: Sequence[Section] = (),
    section_index: int | None = None,
    section_title: str | None = None,
) -> PageDesign:
    b = _Builder(profile)
    if kind == "cover":
        _cover(b, deck_title, subtitle, kicker or profile.label, sections)
    elif kind == "toc":
        _toc(b, sections)
    elif kind == "section_divider":
        _divider(b, section_index or 1, section_title or deck_title)
    elif kind == "closing":
        _closing(b, deck_title)
    else:  # pragma: no cover - guarded by the Literal
        raise ValueError(f"Unknown structural page kind '{kind}'")
    if kind == "toc":
        b.text(f"{page_number:02d}", x=W - profile.margin_x - 60, y=H - 52, w=60, h=22,
               role="caption", size=profile.sizes.caption, color="muted", align="right",
               valign="middle")
    role = {"cover": "cover", "toc": "toc", "section_divider": "section_divider",
            "closing": "closing"}[kind]
    return PageDesign(
        page_number=page_number,
        role=role,
        title=deck_title if kind != "section_divider" else section_title,
        background="background",
        show_chrome=False,
        elements=b.elements or [],
    )
