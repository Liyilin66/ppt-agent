"""Readable names for cited files.

Citations are validated against the uploaded filenames; only what the reader
sees changes. A report reads as "中国信息通信研究院《人工智能发展报告（2024年）》",
not "caict_ai_2024.pdf". Names come from the document itself (PDF cover,
DOCX title, Markdown heading); when nothing reliable is found the filename stem
is shown instead, so a wrong guess never replaces a correct filename.
"""
from __future__ import annotations

import re
from pathlib import Path

_CJK = "一-鿿"
_TITLE_WORDS = ("报告", "白皮书", "蓝皮书", "年鉴", "指南", "办法", "规定", "条例")
_ISSUER_ENDINGS = (
    "研究院", "研究所", "研究中心", "信息中心", "中心", "协会", "学会", "委员会", "联盟",
    "大学", "学院", "公司", "集团", "办公室", "部", "局", "署",
)
_MAX_TITLE = 30
_MAX_ISSUER = 20


def _compact(line: str) -> str:
    """Undo cover typesetting: "中 国 信 息" -> "中国信息", "(2024 年)" -> "（2024年）"."""
    line = re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", line.strip())
    line = re.sub(r"(?<=\d)\s+(?=年)|(?<=[(（])\s+|\s+(?=[)）])", "", line)
    return line.replace("(", "（").replace(")", "）")


def _is_title(text: str) -> bool:
    return (4 <= len(text) <= _MAX_TITLE and any(word in text for word in _TITLE_WORDS)
            and not re.search(r"[。，；：:]", text) and not text.startswith(("本", "该")))


def _continues_title(line: str) -> bool:
    return line.startswith(("（", "报告", "白皮书", "蓝皮书")) and len(line) <= 12


def name_from_cover(lines: list[str]) -> str | None:
    """Issuer and title from the first lines of a report; None when unsure."""
    lines = [text for text in (_compact(line) for line in lines) if text]
    title = None
    for index, line in enumerate(lines):
        candidate = line
        if index + 1 < len(lines) and _continues_title(lines[index + 1]):
            candidate += lines[index + 1]
        if _is_title(candidate):
            title = candidate
            break
    if title is None:
        return None
    issuer = next((line for line in lines
                   if len(line) <= _MAX_ISSUER and re.fullmatch(rf"[{_CJK}]+", line)
                   and line.endswith(_ISSUER_ENDINGS)), None)
    return f"{issuer or ''}《{title}》"


def _fallback(path: Path) -> str:
    return re.sub(r"[_\-]+", " ", path.stem).strip() or path.name


def _document_name(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        lines: list[str] = []
        for page in PdfReader(str(path)).pages[:2]:
            lines += (page.extract_text() or "").splitlines()
        return name_from_cover(lines)
    if suffix == ".docx":
        from docx import Document
        document = Document(str(path))
        title = (document.core_properties.title or "").strip()
        if not title:
            title = next((p.text.strip() for p in document.paragraphs
                          if p.text.strip() and p.style.name.lower().startswith(("title", "heading"))), "")
        return _wrap(title)
    if suffix in {".md", ".txt"}:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line:
                return _wrap(line.lstrip("#").strip()) if suffix == ".md" and line.startswith("#") else None
    return None


def _wrap(title: str) -> str | None:
    title = _compact(title)
    if not title or len(title) > _MAX_TITLE:
        return None
    return f"《{title}》" if re.search(rf"[{_CJK}]", title) else title


def document_display_names(paths) -> dict[str, str]:
    """{filename: readable name} for every source path."""
    names = {}
    for value in paths:
        path = Path(value)
        try:
            name = _document_name(path)
        except Exception:  # an unreadable cover must not fail the deck
            name = None
        names[path.name] = name or _fallback(path)
    return names


def short_name(display: str) -> str:
    """The title alone, for tight spots: "中国信息通信研究院《X》" -> "《X》"."""
    match = re.search(r"《[^》]+》", display)
    return match.group() if match else display
