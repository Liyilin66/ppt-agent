"""Cover, agenda, section and closing pages: filled templates, no model call."""

from __future__ import annotations

from html import escape


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
        + f'<div class="cover-main"><h1 class="cover-title">{escape(title)}</h1>'
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
