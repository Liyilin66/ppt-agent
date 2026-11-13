"""Cover, agenda, section and closing pages: filled templates, no model call."""

from __future__ import annotations

from html import escape


def _title_lines(title: str) -> str:
    """Break a title at its colon so no line ends on an orphaned character."""

    for mark in ("：", ":", "——"):
        head, found, tail = title.partition(mark)
        if found and head.strip() and tail.strip():
            return f"{escape(head.strip())}{'：' if mark != '——' else ''}<br>{escape(tail.strip())}"
    return escape(title)


PLAIN_VARIANTS = ("ledger", "poster", "columns")


def plain(*, title: str | None, kicker: str | None, source: str | None, page_number: int,
          rows: list[tuple[str, str, str]], note: str | None = None, variant: str = "ledger",
          highlight: tuple[str, str] | None = None) -> str:
    """A sober studio page built without the model, for a page whose designed
    versions could not be made to fit. Three arrangements, so that two such pages
    near each other do not look the same:

    ``ledger``  the claim on the left, the facts as ruled rows on the right
    ``poster``  the focal figure large on the left, three facts beside it
    ``columns`` the claim across the top, the facts side by side under rules

    ``rows`` are (figure, heading, body); a figure is the fact's own value or "".
    """

    head = f'<div class="r" style="grid-column:1/9;grid-row:1/2"><div class="kicker">{escape(kicker)}</div></div>' if kicker else ""
    foot = f'<div class="source">{escape(source or "")}</div><div class="pageno">{page_number:02d}</div></div>'
    call = f'<div class="call">{escape(note)}</div>' if note else ""
    name = escape(title or "")

    if variant == "poster" and highlight:
        value, meaning = highlight
        size = "d2" if len(value) <= 5 else "d3" if len(value) <= 8 else "d4"
        facts = "".join(
            f'<div class="r gap0 lt"><div class="t3">{escape(f"{figure} {heading}".strip())}</div>'
            + (f'<div class="c">{escape(body)}</div>' if body else "") + "</div>"
            for figure, heading, body in rows[:3])
        return (
            '<div class="page">' + head
            + f'<div class="r" style="grid-column:1/12;grid-row:2/3"><div class="t2">{name}</div></div>'
            + f'<div class="r mid" style="grid-column:1/8;grid-row:3/7"><div class="{size} red">{escape(value)}</div>'
            + f'<div class="b">{escape(meaning)}</div></div>'
            + f'<div class="r spread" style="grid-column:8/13;grid-row:3/9">{facts}</div>'
            + (f'<div class="r bot" style="grid-column:1/8;grid-row:7/9">{call}</div>' if call else "")
            + foot)

    if variant == "columns" and 2 <= len(rows) <= 4:
        span = 12 // len(rows)
        columns = "".join(
            f'<div class="r lt" style="grid-column:{1 + index * span}/{1 + (index + 1) * span};grid-row:4/7">'
            + (f'<div class="t2 red">{escape(figure)}</div>' if figure else "")
            + f'<div class="t3">{escape(heading)}</div>' + (f'<div class="c">{escape(body)}</div>' if body else "")
            + "</div>"
            for index, (figure, heading, body) in enumerate(rows))
        return (
            '<div class="page">' + head
            + f'<div class="r" style="grid-column:1/11;grid-row:2/4"><div class="t2">{name}</div></div>'
            + columns
            + (f'<div class="r bot" style="grid-column:1/9;grid-row:7/9">{call}</div>' if call else "")
            + foot)

    lines = "".join(
        f'<div class="li"><div class="fig{"" if figure else " n"}">{escape(figure or f"{index:02d}")}</div>'
        f'<div class="r gap0"><div class="b bold">{escape(heading)}</div>'
        + (f'<div class="c">{escape(body)}</div>' if body else "") + "</div></div>"
        for index, (figure, heading, body) in enumerate(rows[:6], 1)
    )
    return (
        '<div class="page">' + head
        + f'<div class="r gap-l" style="grid-column:1/5;grid-row:2/9"><div class="t2">{name}</div>{call}</div>'
        + f'<div class="r" style="grid-column:5/13;grid-row:2/9"><div class="ledger fill">{lines}</div></div>'
        + foot
    )


def cover(title: str, subtitle: str | None, sections: list[str], kicker: str = "") -> str:
    agenda = "".join(
        f'<div class="item"><span class="num">{index:02d}</span>'
        f'<span class="bold">{escape(name)}</span></div>'
        for index, name in enumerate(sections[:6], 1)
    )
    aside = (
        f'<div class="cover-aside"><div class="label">本次汇报</div><div class="list">{agenda}</div></div>'
        if agenda else ""
    )
    return (
        '<div class="page cover"><div class="deco"></div><div class="deco2"></div><div class="deco3"></div>'
        + (f'<div class="kicker">{escape(kicker)}</div>' if kicker else "")
        + f'<div class="cover-main"><h1 class="cover-title">{_title_lines(title)}</h1>'
        + (f'<p class="cover-sub">{escape(subtitle)}</p>' if subtitle else "")
        + f"</div>{aside}</div>"
    )


def section(index: int | None, title: str, subtitle: str | None = None) -> str:
    return (
        '<div class="page section"><div class="deco"></div><div class="deco2"></div><div class="section-main">'
        + (f'<div class="section-no">{index:02d}</div>' if index else "")
        + f'<h1 class="section-title">{escape(title)}</h1>'
        + (f'<p class="section-sub">{escape(subtitle)}</p>' if subtitle else "")
        + "</div></div>"
    )


def agenda(sections: list[tuple[str, int]], page_number: int) -> str:
    rows = "".join(
        f'<div class="item"><div class="key">{index:02d}</div><div class="h f1">{escape(name)}</div>'
        f'<div class="label">第 {first} 页</div></div>'
        for index, (name, first) in enumerate(sections[:8], 1)
    )
    return (
        '<div class="page"><div class="head"><h1 class="title">目录</h1></div>'
        f'<div class="body col center"><div class="list">{rows}</div></div>'
        f'<div class="foot"><div class="source"></div><div class="pageno">{page_number:02d}</div></div></div>'
    )


def closing(title: str, subtitle: str | None = None) -> str:
    return (
        '<div class="page statement"><div class="deco"></div><div class="statement-main">'
        f'<div class="quote">谢谢</div><p class="lead">{escape(title)}'
        + (f"｜{escape(subtitle)}" if subtitle else "")
        + "</p></div></div>"
    )
