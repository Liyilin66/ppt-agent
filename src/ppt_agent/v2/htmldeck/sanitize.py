"""Keep model-written slide HTML inside what the converter can reproduce.

The model styles a page with the theme's classes. Inline styles may only
arrange things (flex, grid, sizes, spacing); colours, fonts and effects belong
to the theme. Anything else is removed and reported so the model can be told.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset(
    "div span p h1 h2 h3 h4 b strong i em br ul ol li table thead tbody tr th td "
    "section header footer small".split()
)
VOID_TAGS = frozenset({"br"})
# Removed together with everything inside them.
DROP_WITH_CONTENT = frozenset("script style svg canvas iframe object video audio template noscript".split())
ALLOWED_ATTRS = frozenset({"class", "style", "data-chart", "data-variant"})
_LAYOUT_PROPERTY = re.compile(
    r"^(display|flex|flex-[a-z]+|grid|grid-[a-z-]+|gap|row-gap|column-gap|order|"
    r"width|height|min-width|min-height|max-width|max-height|"
    r"margin|margin-[a-z]+|padding|padding-[a-z]+|"
    r"position|left|top|right|bottom|inset|"
    r"text-align|white-space|align-[a-z]+|justify-[a-z]+|place-[a-z]+)$"
)
_BAD_VALUE = re.compile(r"url\(|expression|javascript:|@import|[<>{}\\]", re.I)


def _clean_style(value: str, rejected: list[str]) -> str:
    kept = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        name, _, raw = declaration.partition(":")
        name, raw = name.strip().lower(), raw.strip()
        if not name or not raw:
            continue
        if not _LAYOUT_PROPERTY.match(name) or _BAD_VALUE.search(raw):
            rejected.append(f"内联样式 {name}")
            continue
        kept.append(f"{name}:{raw}")
    return ";".join(kept)


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.rejected: list[str] = []
        self._skip_depth = 0
        self._skip_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.rejected.append(f"<{tag}>")
            self._skip_depth, self._skip_tag = 1, tag
            return
        if tag not in ALLOWED_TAGS:
            if tag not in ("html", "body", "head", "meta", "title", "main", "article"):
                self.rejected.append(f"<{tag}>")
            return  # unwrap: keep the children
        parts = [tag]
        for name, value in attrs:
            name = name.lower()
            if name not in ALLOWED_ATTRS:
                if name.startswith("on") or name in ("href", "src", "srcset"):
                    self.rejected.append(f"属性 {name}")
                continue
            value = value or ""
            if name == "style":
                value = _clean_style(value, self.rejected)
                if not value:
                    continue
            elif name == "class":
                value = " ".join(re.findall(r"[A-Za-z0-9_-]+", value))
            parts.append(f'{name}="{html.escape(value, quote=True)}"')
        self.out.append("<" + " ".join(parts) + ">")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.out.append(html.escape(data, quote=False))


def strip_fences(text: str) -> str:
    """Drop a Markdown code fence a model may wrap its HTML in."""

    match = re.search(r"```(?:html)?\s*\n(.*?)```", text, re.S)
    return (match.group(1) if match else text).strip()


def clean(raw_html: str) -> tuple[str, list[str]]:
    """Return slide HTML reduced to the allowed vocabulary, and what was removed."""

    cleaner = _Cleaner()
    cleaner.feed(strip_fences(raw_html))
    cleaner.close()
    return "".join(cleaner.out).strip(), list(dict.fromkeys(cleaner.rejected))
