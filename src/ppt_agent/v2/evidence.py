"""Bounded, page-preserving evidence allocation without model calls.

PDF citations use physical pages. Text and Word sources use explicitly labelled
logical pages, since their pagination is not available to the text parser.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import re
from typing import Protocol

_TERMS = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z0-9]+')
_HEADING = re.compile(r'^(?:第\s*[一二三四五六七八九十百0-9]+\s*[章篇部]|#{1,3}\s+).{1,100}$')
_SUBHEADING = re.compile(r'^[一二三四五六七八九十]+、.{2,65}$')


def tokenize(text: str) -> list[str]:
    result = []
    for match in _TERMS.finditer(text):
        term = match.group()
        result.extend([term.lower()] if term.isascii() else [term[i:i + 2] for i in range(len(term) - 1)])
    return result


class Retriever(Protocol):
    def search(self, query: str, top_k: int) -> list[tuple[int, float]]: ...


class BM25Retriever:
    def __init__(self, texts: list[str]):
        self.terms = [Counter(tokenize(text)) for text in texts]
        self.lengths = [sum(t.values()) for t in self.terms]
        self.average = sum(self.lengths) / len(texts) if texts else 0
        frequency = Counter(term for terms in self.terms for term in terms)
        self.idf = {term: math.log(1 + (len(texts) - n + .5) / (n + .5)) for term, n in frequency.items()}

    def search(self, query: str, top_k: int = 4) -> list[tuple[int, float]]:
        terms = set(tokenize(query)) & self.idf.keys()
        if not terms or self.average == 0:
            return []
        scores = []
        for i, counts in enumerate(self.terms):
            norm = 1.5 * (.25 + .75 * self.lengths[i] / self.average)
            score = sum(self.idf[t] * counts[t] * 2.5 / (counts[t] + norm) for t in terms if counts[t])
            if score > 0:
                scores.append((i, score))
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:max(0, top_k)]


@dataclass
class EvidencePacket:
    text: str = ''
    references: list[str] = field(default_factory=list)
    page_counts: dict[str, int] = field(default_factory=dict)
    allowed_pages: dict[str, list[int]] = field(default_factory=dict)
    strategy: str = 'bm25'
    chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


def _chunk_document(document: dict, size: int = 1500, overlap: int = 300) -> list[dict]:
    text = '\n'.join(p['text'] for p in document['pages'])
    spans, offset = [], 0
    for page in document['pages']:
        end = offset + len(page['text'])
        spans.append((offset, end, page['page']))
        offset = end + 1
    result, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        parts = [{'page': n, 'text': text[max(start, a):min(end, b)]} for a, b, n in spans if a < end and b > start]
        if parts and any(part['text'].strip() for part in parts):
            result.append({'chunk_id': f"{document['doc_id']}:c{len(result) + 1}", 'doc_id': document['doc_id'],
                           'text': text[start:end], 'pages': [p['page'] for p in parts], 'parts': parts})
        if end == len(text):
            break
        start += size - overlap
    return result


def _chapters(document: dict) -> list[dict]:
    starts = []
    for page in document['pages']:
        lines = page['text'].splitlines()
        is_toc = bool(re.search(r'目\s*录', page['text'][:180]))
        if is_toc:
            continue
        for line in lines:
            clean = line.strip()
            if re.search(r'\.{3}|…{2}', clean):
                continue
            if _HEADING.match(clean):
                starts.append((page['page'], clean.lstrip('#').strip()))
                break
    # Some reports put international developments under their final chapter.
    # Detect descriptive top-level subsection headings without fixed page ranges.
    if starts:
        for page in document['pages']:
            for line in page['text'].splitlines():
                clean = line.strip()
                if _SUBHEADING.match(clean) and re.search(r'国际|海外|全球', clean) and not re.search(r'\.{3}|…{2}', clean):
                    if page['page'] > starts[-1][0] and len(clean) < 50:
                        starts.append((page['page'], clean))
                        break
    if not starts:
        return []
    starts = sorted(set(starts))
    result = []
    for i, (start, title) in enumerate(starts):
        end = starts[i + 1][0] - 1 if i + 1 < len(starts) else len(document['pages'])
        pages = [p for p in document['pages'] if start <= p['page'] <= end]
        if not pages:
            continue
        result.append({'chapter_id': f"{document['doc_id']}:h{i + 1}", 'doc_id': document['doc_id'],
                       'title': title, 'pages': [p['page'] for p in pages],
                       'lead': '\n'.join(p['text'] for p in pages)[:400]})
    return result


def _logical_pages(text: str) -> list[str]:
    """Logical sections for non-PDF files; never claim physical pagination."""
    blocks, current = [], []
    for line in text.splitlines():
        if _HEADING.match(line.strip()) and current:
            blocks.append('\n'.join(current))
            current = []
        current.append(line)
    if current:
        blocks.append('\n'.join(current))
    return blocks or ['']


class EvidenceStore:
    def __init__(self, documents: list[dict], warnings: list[str] | None = None):
        self.documents = documents
        self.warnings = list(warnings or [])
        self.parsed_files = [doc.get('path', doc['name']) for doc in documents]
        self.chunks = [chunk for doc in documents for chunk in _chunk_document(doc)]
        self.chapters = [chapter for doc in documents for chapter in _chapters(doc)]
        self.retriever: Retriever = BM25Retriever([c['text'] for c in self.chunks])
        self._chapter_index = BM25Retriever([c['title'] + '\n' + c['lead'] for c in self.chapters])
        self._docs = {doc['doc_id']: doc for doc in documents}

    @classmethod
    def from_paths(cls, paths: list[str | Path]) -> EvidenceStore:
        documents, warnings = [], []
        for raw in paths:
            path = Path(raw)
            try:
                data = path.read_bytes()
                suffix = path.suffix.lower()
                if suffix == '.pdf':
                    from pypdf import PdfReader
                    pages = [p.extract_text() or '' for p in PdfReader(str(path)).pages]
                    kind = 'PDF'
                elif suffix == '.docx':
                    from docx import Document
                    doc = Document(str(path))
                    text = '\n'.join([p.text for p in doc.paragraphs] + [' | '.join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows])
                    pages, kind = _logical_pages(text), 'logical'
                elif suffix in {'.txt', '.md', '.markdown'}:
                    pages, kind = _logical_pages(data.decode('utf-8', errors='replace')), 'logical'
                else:
                    warnings.append(f'Unsupported evidence source: {path.name}')
                    continue
                name = path.name
                if any(doc['name'] == name for doc in documents):
                    # Ambiguous filename citations must not silently resolve.
                    warnings.append(f'Duplicate source filename excluded: {name}')
                    continue
                documents.append({'doc_id': f'doc{len(documents) + 1}', 'name': name,
                                  'path': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                                  'page_kind': kind, 'pages': [{'page': i + 1, 'text': t} for i, t in enumerate(pages)]})
            except Exception as exc:
                warnings.append(f'Failed to parse {path.name}: {exc}')
        return cls(documents, warnings)

    def to_dict(self) -> dict:
        return {'documents': self.documents, 'warnings': self.warnings}

    @classmethod
    def from_dict(cls, data: dict) -> EvidenceStore:
        return cls(data.get('documents', []), data.get('warnings', []))

    def document_map(self, max_chars: int = 10000) -> str:
        entries = []
        for doc in self.documents:
            chapters = [c for c in self.chapters if c['doc_id'] == doc['doc_id']]
            if not chapters:
                # Unstructured reports still expose every page range, not a prefix.
                chapters = [{'title': f"Pages {i + 1}–{min(i + 8, len(doc['pages']))}",
                             'pages': [p['page'] for p in doc['pages'][i:i + 8]]} for i in range(0, len(doc['pages']), 8)]
            for chapter in chapters:
                pages = [p for p in doc['pages'] if p['page'] in chapter['pages']]
                lead = re.sub(r'\s+', ' ', pages[0]['text']).strip()[:120]
                numbers = []
                for page in pages:
                    content_lines = [line.strip() for line in page['text'].splitlines() if line.strip() and not re.fullmatch(r'[0-9IVX]+', line.strip()) and not re.search(r'报告[（(].*[）)]$', line.strip())]
                    flattened = re.sub(r'\s+', ' ', ' '.join(content_lines))
                    for sentence in re.split(r'(?<=[。！？;；])', flattened):
                        if re.search(r'\d[\d.,．]*\s*(?:亿|万|%|％|人|项|台|元|美元|倍|款|EFLOPS)', sentence) and '来源：' not in sentence and len(sentence) <= 500 and len(re.findall(r'[\u4e00-\u9fff]', sentence)) >= 5:
                            numbers.append(f"[{doc['page_kind']} p{page['page']}] {sentence.strip()}")
                # Spread numeric examples over the chapter rather than only its lead.
                chosen = numbers
                heading = f"{doc['name']} [{doc['page_kind']} p{chapter['pages'][0]}–{chapter['pages'][-1]}] {chapter['title']}"
                entries.append((heading, lead, chosen))
        if not entries or max_chars <= 0:
            return ''
        quota = max(0, (max_chars - len(entries) + 1) // len(entries))
        result = []
        for heading, lead, numbers in entries:
            # Numeric statements precede lead so late-chapter facts remain visible.
            prefix = heading[:quota]
            remaining = quota - len(prefix)
            lines = []
            lead_line = '\n' + lead[:min(120, max(0, remaining // 5))]
            remaining -= len(lead_line)
            # Round-robin pages avoids starving late pages within a long chapter.
            by_page = {}
            for statement in numbers:
                page_label = statement.split(']', 1)[0]
                by_page.setdefault(page_label, []).append(statement)
            page_groups = list(by_page.values())
            # Alternate the front and back: a short map must still expose the
            # final physical pages of a numerically dense chapter.
            balanced = []
            while page_groups:
                balanced.append(page_groups.pop(0))
                if page_groups:
                    balanced.append(page_groups.pop())
            while any(balanced):
                for statements in balanced:
                    if not statements:
                        continue
                    statement = statements.pop(0)
                    if len(statement) + 1 <= remaining:
                        lines.append(statement)
                        remaining -= len(statement) + 1
            result.append(prefix + lead_line + ''.join('\n' + line for line in lines))
        return '\n'.join(result)[:max_chars]

    def select(self, query: str, section_query: str | None = None, mode: str = 'auto', max_chars: int = 6500) -> EvidencePacket:
        if mode not in {'auto', 'chapter', 'retrieval', 'bm25'}:
            raise ValueError(f'Unknown evidence mode: {mode}')
        strategy, candidates = 'bm25', []
        total = sum(len(p['text']) for d in self.documents for p in d['pages'])
        if mode in {'auto', 'chapter'} and len(self.documents) == 1 and total <= 80000 and self.chapters:
            chapter_query = section_query or query
            ranked = self._chapter_index.search(chapter_query, 2)
            if ranked:
                # Require meaningful query coverage; a lone generic word must not
                # route an unrelated section to the first long chapter.
                query_terms = set(tokenize(chapter_query))
                chapter = self.chapters[ranked[0][0]]
                terms = set(tokenize(chapter['title'] + '\n' + chapter['lead']))
                coverage = len(query_terms & terms) / max(1, len(query_terms))
                if coverage >= .2 and len(query_terms & terms) >= 2:
                    selected = [self.chapters[i] for i, score in ranked if score >= ranked[0][1] * .75]
                    selected_pages = {p for c in selected for p in c['pages']}
                    candidates = []
                    for chunk in self.chunks:
                        parts = [part for part in chunk['parts'] if part['page'] in selected_pages]
                        if parts:
                            candidates.append({**chunk, 'parts': parts,
                                               'pages': [part['page'] for part in parts],
                                               'text': '\n'.join(part['text'] for part in parts)})
                    strategy = 'chapter'
        if not candidates:
            candidates = [self.chunks[i] for i, _ in self.retriever.search(query, 4)]
        else:
            ranked = BM25Retriever([c['text'] for c in candidates]).search(query, 4)
            candidates = [candidates[i] for i, _ in ranked] if ranked else candidates[:4]
        packet = EvidencePacket(strategy=strategy, page_counts={d['name']: len(d['pages']) for d in self.documents})
        blocks = []
        for chunk in candidates[:4]:
            doc = self._docs[chunk['doc_id']]
            added = False
            for part in chunk['parts']:
                label = f"[{doc['name']} | {doc['page_kind']} p{part['page']}]\n"
                budget = max_chars - sum(len(b) for b in blocks) - 2 * len(blocks)
                if budget <= len(label):
                    break
                text = part['text'][:budget - len(label)]
                if not text.strip():
                    continue
                blocks.append(label + text)
                packet.allowed_pages.setdefault(doc['name'], []).append(part['page'])
                added = True
            if added:
                packet.chunk_ids.append(chunk['chunk_id'])
        packet.text = '\n\n'.join(blocks)
        packet.allowed_pages = {name: sorted(set(pages)) for name, pages in packet.allowed_pages.items()}
        packet.references = list(packet.allowed_pages)
        return packet
