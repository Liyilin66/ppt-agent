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


def plain(*, title: str | None, kicker: str | None, source: str | None, page_number: int,
          rows: list[tuple[str, str, str]], note: str | None = None) -> str:
    """A sober studio page built without the model: the claim on the left, the
    facts as a ledger on the right. Used when a designed page cannot be made to fit."""

    lines = "".join(
        f'<div class="li"><div class="fig{" n" if not any(ch.isdigit() for ch in figure) or len(figure) <= 2 else ""}">'
        f'{escape(figure)}</div><div class="r gap0"><div class="b bold">{escape(head)}</div>'
        + (f'<div class="c">{escape(body)}</div>' if body else "") + "</div></div>"
        for figure, head, body in rows[:6]
    )
    return (
        '<div class="page">'
        + (f'<div class="r" style="grid-column:1/9;grid-row:1/2"><div class="kicker">{escape(kicker)}</div></div>' if kicker else "")
        + f'<div class="r" style="grid-column:1/5;grid-row:2/8"><div class="t2">{escape(title or "")}</div>'
        + (f'<div class="s mut">{escape(note)}</div>' if note else "") + "</div>"
        + f'<div class="r" style="grid-column:5/13;grid-row:2/9"><div class="ledger">{lines}</div></div>'
        + f'<div class="source">{escape(source or "")}</div><div class="pageno">{page_number:02d}</div></div>'
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
