"""Source-document intake: turn user files into a digest the planner can use.

Parsing is deliberately lossy — the planner needs grounded facts and
structure, not a faithful reproduction. Unsupported or unreadable files
degrade to a warning instead of failing the run.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from ppt_agent.models import StrictModel


MAX_DIGEST_CHARS = 24_000


class IntakeResult(StrictModel):
    digest: str = ""
    parsed_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages[:120]]
    return "\n".join(pages)


def _read_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


_READERS = {
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".md": _read_text,
    ".markdown": _read_text,
    ".txt": _read_text,
}


def ingest_sources(paths: list[str | Path]) -> IntakeResult:
    """Parse every supported source file into one bounded digest."""

    chunks: list[str] = []
    parsed: list[str] = []
    warnings: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            warnings.append(f"Source not found: {path}")
            continue
        reader = _READERS.get(path.suffix.lower())
        if reader is None:
            warnings.append(
                f"Unsupported source type '{path.suffix}' for {path.name}; "
                "supported: pdf, docx, md, txt"
            )
            continue
        try:
            text = reader(path).strip()
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            warnings.append(f"Failed to parse {path.name}: {exc}")
            continue
        if text:
            chunks.append(f"### {path.name}\n{text}")
            parsed.append(str(path))
        else:
            warnings.append(f"No extractable text in {path.name}")
    digest = "\n\n".join(chunks)
    if len(digest) > MAX_DIGEST_CHARS:
        digest = digest[:MAX_DIGEST_CHARS] + "\n…(truncated)"
    return IntakeResult(digest=digest, parsed_files=parsed, warnings=warnings)
