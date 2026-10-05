"""Local source preparation for long-input experiments; no model calls.

All page numbers are physical, one-based PDF page numbers. Character counts
exclude inserted separators and source labels; source text is never summarized.
"""

from collections import Counter
from io import BytesIO
import hashlib
import math
from pathlib import Path
import re

from pypdf import PdfReader


_TERMS = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z0-9]+")
_CHAPTERS = (
    ("主要发展特点", 9, 14),
    ("用户普及", 15, 22),
    ("产业发展", 23, 24),
    ("典型应用", 25, 42),
    ("发展环境", 43, 50),
    ("国际态势", 51, 63),
)


def read_documents(paths: list[Path]) -> list[dict]:
    """Read PDFs in input order, including blank pages, with file hashes."""
    documents = []
    for index, path in enumerate(paths, start=1):
        resolved = Path(path).resolve()
        data = resolved.read_bytes()
        reader = PdfReader(BytesIO(data))
        pages = [
            {"pdf_page": number, "text": page.extract_text() or ""}
            for number, page in enumerate(reader.pages, start=1)
        ]
        documents.append({
            "doc_id": f"doc{index}",
            "path": str(resolved),
            "sha256": hashlib.sha256(data).hexdigest(),
            "pages": pages,
            "chars": sum(len(page["text"]) for page in pages),
        })
    return documents


def full_text(docs: list[dict]) -> str:
    """Join complete page text with explicit document and PDF page labels."""
    return "\n\n".join(
        f"[[{doc['doc_id']} PDF p{page['pdf_page']}]]\n{page['text']}"
        for doc in docs
        for page in doc["pages"]
    )


def cnnic_chapters(doc: dict) -> list[dict]:
    """Split the 2025 CNNIC report by its TOC (PDF pages 7–8).

The prescribed chapters span PDF pages 9–63. A truncated PDF raises rather
than silently exposing incomplete source as a complete chapter.
"""
    page_numbers = {page["pdf_page"] for page in doc["pages"]}
    missing = sorted(set(range(9, 64)) - page_numbers)
    if missing:
        raise ValueError(f"CNNIC chapters require PDF pages 9–63; missing {missing}")
    chapters = []
    for number, (title, start, end) in enumerate(_CHAPTERS, start=1):
        pages = sorted(
            (page for page in doc["pages"] if start <= page["pdf_page"] <= end),
            key=lambda page: page["pdf_page"],
        )
        chapters.append({
            "chapter_id": number,
            "title": title,
            "doc_id": doc["doc_id"],
            "pdf_page_start": start,
            "pdf_page_end": end,
            "pages": pages,
            "text": "\n".join(page["text"] for page in pages),
        })
    return chapters


def chunks(docs: list[dict], size: int = 1600, overlap: int = 300) -> list[dict]:
    """Chunk each document across pages, preserving the final partial window.

``size`` and ``overlap`` count characters. A chunk's page span covers actual
page text in its window, ignoring the separator at a page boundary.
"""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("Require size > 0 and 0 <= overlap < size")
    result = []
    for doc in docs:
        pages = doc["pages"]
        text = "\n".join(page["text"] for page in pages)
        if not text.strip():
            continue
        spans = []
        offset = 0
        for page in pages:
            end = offset + len(page["text"])
            spans.append((offset, end, page["pdf_page"]))
            offset = end + 1
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            touched = [
                number for page_start, page_end, number in spans
                if page_start < end and page_end > start
            ]
            # A whitespace-only window still retains its source position.
            if not touched:
                touched = [next(
                    number for page_start, page_end, number in spans
                    if page_start <= start <= page_end
                )]
            result.append({
                "doc_id": doc["doc_id"],
                "pdf_page_start": touched[0],
                "pdf_page_end": touched[-1],
                "text": text[start:end],
            })
            if end == len(text):
                break
            start += size - overlap
    return result


def tokenize(text: str) -> list[str]:
    """Return overlapping Chinese bigrams and lowercase ASCII word terms."""
    tokens = []
    for match in _TERMS.finditer(text):
        term = match.group()
        if term.isascii():
            tokens.append(term.lower())
        else:
            tokens.extend(term[index:index + 2] for index in range(len(term) - 1))
    return tokens


class BM25Index:
    """Pure-Python BM25 with positive Robertson IDF, k1=1.5 and b=0.75."""

    def __init__(self, texts: list[str]):
        self._frequencies = [Counter(tokenize(text)) for text in texts]
        self._lengths = [sum(terms.values()) for terms in self._frequencies]
        self._average_length = (
            sum(self._lengths) / len(texts) if texts else 0.0
        )
        document_frequencies = Counter(
            term for terms in self._frequencies for term in terms
        )
        self._idf = {
            term: math.log(1 + (len(texts) - count + 0.5) / (count + 0.5))
            for term, count in document_frequencies.items()
        }

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Return only positive matches; ties keep original document order."""
        query_terms = sorted(set(tokenize(query)) & self._idf.keys())
        if top_k <= 0 or not query_terms:
            return []
        matches = []
        for index, terms in enumerate(self._frequencies):
            normalizer = 1.5 * (
                0.25 + 0.75 * self._lengths[index] / self._average_length
            )
            score = sum(
                self._idf[term] * terms[term] * 2.5 / (terms[term] + normalizer)
                for term in query_terms if terms[term]
            )
            if score > 0:
                matches.append((index, score))
        return sorted(matches, key=lambda match: (-match[1], match[0]))[:top_k]


def select_chapter(query: str, chapters: list[dict]) -> tuple[dict | None, float]:
    """Select by title plus first 500 characters, then literal title fallback.

Fallback title matches have score 0.0, distinguishing them from BM25 matches.
If neither lexical route matches, return ``(None, 0.0)``.
"""
    index = BM25Index([
        f"{chapter['title']}\n{chapter['text'][:500]}" for chapter in chapters
    ])
    matches = index.search(query, top_k=1)
    if matches:
        position, score = matches[0]
        return chapters[position], score
    query_parts = [match.group().lower() for match in _TERMS.finditer(query)]
    for chapter in chapters:
        title = chapter["title"].lower()
        if any(part in title or title in part for part in query_parts if title):
            return chapter, 0.0
    return None, 0.0
