"""Content-only model calls followed by an ordered deterministic layout pass."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ppt_agent.v2.ir import DeckDesign
from ppt_agent.v2.planning import PageBrief
from ppt_agent.v2.providers import BudgetExceededError
from ppt_agent.v2.qa import PageQAResult, QAIssue, review_page, summarize
from ppt_agent.v2.render import render_deck
from ppt_agent.v2.visual.archetypes import typeset_page
from ppt_agent.v2.visual.structural import typeset_structural
from ppt_agent.v2.visual.content import PointsContent
from ppt_agent.v2.visual.profiles import PROFILES
from ppt_agent.v2.source_names import document_display_names, short_name


HINT_ARCHETYPES = {
    'cards': 'points', 'list': 'points', 'two_column': 'points',
    'timeline': 'timeline', 'table': 'table', 'comparison': 'compare',
    'stats': 'metrics', 'quote': 'statement', 'chart': 'chart', 'auto': 'points',
}


def content_adapter():
    # The schema stays owned by visual.content, including newly added types.
    from ppt_agent.v2.visual import content
    return TypeAdapter(content.ArchetypeContent)


def suggested_archetype(hint):
    schema = content_adapter().json_schema()
    supported = schema['discriminator']['mapping']
    suggested = HINT_ARCHETYPES.get(hint, 'points')
    if suggested in supported:
        return suggested
    return 'process' if suggested == 'timeline' else 'points'


def source_is_valid(source, references, page_counts=None, allowed_pages=None):
    """Validate every cited document and physical page, not claim semantics."""
    if not source or not str(source).strip():
        return True
    source = str(source).strip()
    clauses = [item.strip() for item in re.split(r'[;；]', source) if item.strip()]
    if len(clauses) > 1:
        return all(source_is_valid(item, references, page_counts, allowed_pages) for item in clauses)
    if any(label in source.lower() for label in (
        '用户提供', '用户需求', '需求说明', '需求背景', 'user-provided', 'user provided',
    )) or not references:
        return False
    urls = re.findall(r'https?://[^\s；;，,）)]+', source)
    if urls:
        return all(url in references for url in urls)
    known = [Path(ref).name for ref in references if not ref.startswith(('http://', 'https://'))]
    names = [name for name in known if re.search(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])', source)]
    remaining = source
    for name in sorted(names, key=len, reverse=True):
        remaining = remaining.replace(name, '')
    if re.search(r'\.(?:pdf|docx|md|txt|csv|xlsx)\b', remaining, re.I):
        return False
    remaining = re.sub(r'\bp(?:ages?)?\.?\s*(\d+)(?:[-–—](\d+))?',
                       lambda m: '第' + m[1] + ('-' + m[2] if m[2] else '') + '页', remaining, flags=re.I)
    pattern = r'(?:第\s*)?(\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,，、/]\s*\d+(?:\s*[-–—]\s*\d+)?)*)\s*页'
    spans = []
    for expression in re.findall(pattern, remaining):
        for part in re.split(r'[,，、/]', expression):
            numbers = re.findall(r'\d+', part)
            spans.append((numbers[0], numbers[-1]))
    # Reject any unparsed digits rather than certify only part of a page list.
    if re.search(r'\d', re.sub(pattern, '', remaining)):
        return False
    if len(names) > 1:
        return False  # Multiple documents must use explicit semicolon-separated citations.
    document = names[0] if names else None
    if document is None:
        pdfs = [name for name in known if name.lower().endswith('.pdf')]
        if len(pdfs) != 1 or not (page_counts or {}).get(pdfs[0]) or not spans:
            return False
        document = pdfs[0]
    if allowed_pages is not None and document.lower().endswith('.pdf') and not spans:
        return False
    for start, end in spans:
        start, end = int(start), int(end or start)
        if start < 1 or end < start or ((page_counts or {}).get(document) and end > page_counts[document]):
            return False
        if allowed_pages is not None and any(n not in allowed_pages.get(document, []) for n in range(start, end + 1)):
            return False
    return bool(names or spans)


def trim_source(source, references, page_counts=None, allowed_pages=None):
    """Keep the cited pages that were actually supplied; return (source, dropped).

    A model often lists one real page beside pages it never received. Dropping
    only the unsupported pages keeps the page; numbers are then checked against
    the remaining citation alone. With nothing valid left the source is unchanged.
    """
    if not source or source_is_valid(source, references, page_counts, allowed_pages):
        return source, []
    known = [Path(ref).name for ref in references if not ref.startswith(('http://', 'https://'))]
    kept, dropped = [], []
    for clause in [item.strip() for item in re.split(r'[;；]', str(source)) if item.strip()]:
        if source_is_valid(clause, references, page_counts, allowed_pages):
            kept.append(clause)
            continue
        names = [name for name in known if name in clause]
        pages = []
        if len(names) == 1:
            locator = re.sub(r'^.*?(?=第|\bp)', '', clause.replace(names[0], ''), flags=re.I)
            pages = re.findall(r'\d+(?:\s*[-–—]\s*\d+)?', locator)
        for span in pages:
            span = re.sub(r'\s+', '', span)
            candidate = f"{names[0]} 第{span}页"
            (kept if source_is_valid(candidate, references, page_counts, allowed_pages) else dropped).append(candidate)
        if not pages:
            dropped.append(clause)
    if not kept:
        return source, []
    return '；'.join(kept), dropped


def repair_citation(content, packet):
    """Cite the supplied pages that actually hold a number the model mis-cited.

    A figure present in this page's excerpts but on a page the model did not name
    is a citation slip, not an invention: correct the page instead of deleting it.
    """
    from ppt_agent.v2.evidence_check import _NUMBER, _key, check_content_numbers
    if packet is None or not packet.text:
        return content, []
    issues = check_content_numbers(content, cited_evidence(content.source, packet))
    missing = {_key(issue['value'].lstrip('-+')) for issue in issues
               if issue['reason'] == 'number_missing' and re.match(r'[-+]?\d', issue['value'])}
    if not missing:
        return content, []
    headers = list(re.finditer(r'^\[(.+?) \| (PDF|logical|web) p(\d+)\]\n', packet.text, re.M))
    added = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(packet.text)
        found = {_key(match.group()) for match in _NUMBER.finditer(packet.text[header.end():end])}
        if not missing & found:
            continue
        clause = header[1] if header[2] == 'web' else f'{header[1]} 第{header[3]}页'
        if clause not in added and clause not in (content.source or ''):
            added.append(clause)
    if not added:
        return content, []
    source = '；'.join(filter(None, [content.source, *added]))
    return content.model_copy(update={'source': source}), added


def cited_evidence(source, packet):
    """Restrict numeric checks to excerpts actually named by the citation."""
    from ppt_agent.v2.evidence import EvidencePacket
    if packet is None or not source:
        return EvidencePacket()
    blocks = []
    matches = list(re.finditer(r'^\[(.+?) \| (PDF|logical|web) p(\d+)\]\n', packet.text, re.M))
    for index, match in enumerate(matches):
        name, kind, number = match[1], match[2], int(match[3])
        allowed = {name: [number]}
        # Ask whether each supplied page is part of the full source citation by parsing its named ranges.
        if kind == 'web':
            included = name in re.findall(r'https?://[^\s；;，,）)]+', source)
        else:
            included = False
            for clause in re.split(r'[;；]', source):
                if name not in clause:
                    continue
                page_text = clause.replace(name, '')
                page_text = re.sub(r'\bp(?:ages?)?\.?\s*(\d+)', lambda m: '第'+m[1]+'页', page_text, flags=re.I)
                for expression in re.findall(r'(?:第\s*)?(\d+(?:[-–—]\d+)?(?:[,，、/]\d+(?:[-–—]\d+)?)*)\s*页', page_text):
                    for span in re.split(r'[,，、/]', expression):
                        nums = [int(n) for n in re.findall(r'\d+', span)]
                        included |= nums[0] <= number <= nums[-1]
        if included:
            end = matches[index+1].start() if index+1 < len(matches) else len(packet.text)
            blocks.append(packet.text[match.start():end])
    return EvidencePacket(text='\n'.join(blocks), references=packet.references,
                          page_counts=packet.page_counts, allowed_pages=packet.allowed_pages,
                          strategy=packet.strategy, chunk_ids=packet.chunk_ids)


# One caption line between the margin and the page number, in em: the tightest
# profile (88 margins, 11 pt caption) leaves 1024 units, about 69 em.
_FOOTER_EMS = 64
_WEB_TITLE_CHARS = 24


def _site(url):
    return re.sub(r'^www\.', '', urlparse(url).netloc)


def _pages(clause):
    """'第7-13页' -> '第 7–13 页'."""
    return re.sub(r'第\s*([^页]+?)\s*页',
                  lambda m: '第 ' + re.sub(r'\s*[-–—]\s*', '–', m[1].strip()) + ' 页', clause)


def _merge_pages(text):
    """'第 9 页、第 12 页' -> '第 9、12 页'."""
    previous = None
    while previous != text:
        previous, text = text, re.sub(r'第 ([\d–、]+) 页\s*[、，,；;]\s*第 ', r'第 \1、', text)
    return text


def _clip(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + '…'


def display_source(source, website_titles, document_names=None):
    """Footer text a reader can use; validation keeps the filename form."""
    if not source:
        return None
    document_names = document_names or {}
    clauses = [clause.strip() for clause in re.split(r'[;；]', source) if clause.strip()]

    def render(names_for, web_limit):
        entries, spans = [], {}
        for clause in clauses:
            if clause.startswith(('https://', 'http://')):
                title = website_titles.get(clause) or '网页资料'
                entries.append(_site(clause) + (' · ' + _clip(title, web_limit) if web_limit else ''))
                continue
            filename = next((name for name in document_names if name in clause), None)
            if filename is None:
                entries.append(_pages(clause))
                continue
            label = names_for(document_names[filename])
            if label not in spans:  # one mention per report, its pages merged
                spans[label] = []
                entries.append(label)
            spans[label] += [re.sub(r'\s*[-–—]\s*', '–', span.strip())
                             for span in re.findall(r'第\s*([^页]+?)\s*页', clause)]
        result = []
        for entry in entries:
            pages = '、'.join(dict.fromkeys(spans.get(entry, [])))
            result.append(entry + ('' if entry.endswith('》') else ' ') + f'第 {pages} 页'
                          if pages else entry)
        return '资料来源：' + '；'.join(dict.fromkeys(result))

    # Shorten in steps rather than clipping mid-title: issuer, then web titles.
    for names_for, web_limit in ((lambda name: name, _WEB_TITLE_CHARS), (short_name, _WEB_TITLE_CHARS),
                                 (short_name, None)):
        text = render(names_for, web_limit)
        if _ems(text) <= _FOOTER_EMS:
            return text
    while _ems(text) > _FOOTER_EMS:
        text = text[:-2] + '…'
    return text


def _ems(text):
    from ppt_agent.v2.visual.layout import _advance_em
    return sum(_advance_em(char) for char in text)


def display_ref(ref, document_names, page_documents):
    """Card-level citation: the page number when the footer already names the report."""
    if not ref:
        return ref
    for filename, name in document_names.items():
        if filename in ref:
            label = '' if page_documents == {filename} else short_name(name)
            return _merge_pages(_pages(re.sub(r'》\s+第', '》第', ref.replace(filename, label)).strip()))
    if re.match(r'https?://', ref) or re.fullmatch(r'[\w.-]+\.[a-z]{2,}', ref):
        return re.sub(r'^(?:https?://)?(?:www\.)?([^/\s]+).*$', r'\1', ref)
    return _merge_pages(_pages(ref))


def display_copy(content, website_titles, document_names):
    """The reader-facing copy of a page: readable sources, small non-repeating card refs."""
    document_names = document_names or {}
    update = {'source': display_source(content.source, website_titles, document_names)}
    items = getattr(content, 'items', None)
    if items and any(item.ref for item in items):
        page_documents = {name for name in document_names if name in (content.source or '')}
        refs = [display_ref(item.ref, document_names, page_documents) for item in items]
        if len(items) > 1 and all(ref for ref in refs) and len(set(refs)) == 1:
            refs = [None] * len(items)  # the same page on every card says nothing per card
        update['items'] = [item.model_copy(update={'ref': ref}) for item, ref in zip(items, refs)]
    return content.model_copy(update=update)


def qualitative_structural_text(text, field, page_number, removed):
    """Structure has no evidence footer; do not render unsupported business numbers."""
    if not text:
        return text
    from ppt_agent.v2.evidence_check import _NUMBER
    def drop(match):
        if re.fullmatch(r'(?:19|20)\d{2}', match.group().strip()):
            return match.group()  # a year dates the report; it is not a statistic
        removed.append({'page_number': page_number, 'field': field, 'value': match.group(),
                        'action': 'removed_number', 'reason': 'structural_copy_without_citation'})
        return ''
    return _NUMBER.sub(drop, text).strip() or '演示'


def diversity_statistics(contents, slots):
    counts = Counter(content.archetype for content in contents)
    total = len(contents)
    violations = []
    if counts['points'] > int(total * .4):
        violations.append({'rule': 'points_share', 'count': counts['points'], 'limit': int(total * .4)})
    for index in range(1, total):
        if contents[index].archetype == contents[index - 1].archetype:
            violations.append({'rule': 'consecutive_archetype', 'page_number': slots[index].page_number})
    for section in {slot.section_index for slot in slots}:
        if all(content.archetype == 'points' for content, slot in zip(contents, slots)
               if slot.section_index == section):
            violations.append({'rule': 'section_non_points', 'section_index': section})
    return {'content_pages': total, 'archetype_counts': dict(counts),
            'archetype_ratios': {key: count / total for key, count in counts.items()} if total else {},
            'diversity_violations': violations}


def normalize_chart_format(content):
    """Raw 47.1 percent data must display 47.1%, never Excel's 4710%."""
    if content.archetype != 'chart' or not content.unit_format or not content.values:
        return content, []
    fmt = content.unit_format
    if max(abs(value) for value in content.values) > 1 and '%' in fmt and '"%"' not in fmt and r'\%' not in fmt:
        fixed = fmt.replace('%', '"%"')
        return content.model_copy(update={'unit_format': fixed}), [
            {'from': fmt, 'to': fixed, 'reason': 'literal percentage values; prevent 100x display inflation'}]
    return content, []


def packet_citation(packet):
    """Declare delivered evidence ranges; do not invent a claim-level provenance."""
    clauses = []
    for name, pages in packet.allowed_pages.items():
        if name.startswith(('https://', 'http://')):
            clauses.append(name)
            continue
        ordered = sorted(set(pages))
        if not ordered:
            continue
        ranges = []
        first = last = ordered[0]
        for page in ordered[1:]:
            if page == last + 1:
                last = page
            else:
                ranges.append(str(first) if first == last else f'{first}-{last}')
                first = last = page
        ranges.append(str(first) if first == last else f'{first}-{last}')
        clauses.append(f"{name} 第{'、'.join(ranges)}页")
    citation = '；'.join(clauses)
    return citation if len(citation) <= 90 else None


_LIST_LIMITS = {"points": ("items", 4), "process": ("steps", 6), "metrics": ("metrics", 4),
                "chart": ("insights", 3), "timeline": ("milestones", 6)}


def trim_overlong_lists(payload):
    """Keep the first N items when a model overshoots a list limit.

    The same rule the typesetter applies to dense pages: trailing items go and
    the cut is recorded, rather than discarding an otherwise good page.
    """
    if not isinstance(payload, dict):
        return payload, []
    if isinstance(payload.get('items'), list):
        # A card ref is a short label; the page source keeps the full URL.
        payload = {**payload, 'items': [
            {**item, 'ref': re.sub(r'https?://([^/\s]+)\S*', r'\1', item['ref'])}
            if isinstance(item, dict) and isinstance(item.get('ref'), str) else item
            for item in payload['items']]}
    limit = _LIST_LIMITS.get(payload.get('archetype'))
    if not limit:
        return payload, []
    field, maximum = limit
    items = payload.get(field)
    if isinstance(items, list) and len(items) > maximum:
        return {**payload, field: items[:maximum]}, [{'field': field, 'from': len(items), 'to': maximum}]
    return payload, []


def fallback_content(slot):
    """Reuse source planning text; do not invent quantitative placeholders."""
    page = slot.brief or PageBrief(title=slot.section_title or '内容要点')
    points = [point.strip() for point in page.points if point.strip()][:4]
    if not points:
        points = [page.summary.strip() or '本页缺少可用资料，请补充原文。']
    if len(points) == 1:
        points.append('请核对原始资料后补充本页内容。')
    return PointsContent(title=page.title[:40], kicker=(slot.section_title or '')[:24] or None,
        items=[{'heading': re.split(r'[，。；：、,;:]', point)[0][:18] or f'要点{index + 1}', 'body': point[:100]}
               for index, point in enumerate(points)],
        speaker_notes=page.speaker_notes or None)


def content_qa(content, page_number, seen_titles=None):
    """Content checks only. Geometry belongs to the deterministic typesetter."""
    issues = []
    if seen_titles is not None:
        if content.title in seen_titles:
            issues.append(QAIssue(code='duplicate_title', severity='warning', message='内容页标题重复'))
        seen_titles.add(content.title)
    payload = content.model_dump(mode='json', exclude_none=True)
    for key in ('source', 'speaker_notes', 'kicker', 'archetype'):
        payload.pop(key, None)
    if re.search(r'\d', json.dumps(payload, ensure_ascii=False)) and not content.source:
        issues.append(QAIssue(code='number_without_source', severity='warning',
                              message='正文含数字但未填写 source；需人工核对数字与指标、时间范围'))
    return PageQAResult(page_number=page_number, issues=issues)


RICH_TIERS = {
    'visual': 'visual: the page is carried by ONE focal fact. Set highlight to that fact; give 1-3 short '
              'items that explain or qualify it. Nothing else.',
    'standard': 'standard: one claim argued with 3-5 items, each a distinct concrete fact. Set highlight '
                'when one fact clearly matters most.',
    'deep': 'deep: a dense reference page. Give 5-8 items, or a table (headers + rows, 3-6 rows) with 2-3 '
            'items that read it. Use every distinct relevant fact the evidence offers.',
}
RICH_SYSTEM = (
    'Return one JSON object matching the schema: the content of one slide. Content only, no layout. '
    'Use the requested slide language. Respect every field length and item-count limit. '
    'The page argues ONE claim. title is that claim as a complete conclusion a reader could repeat, '
    'with the most important number from the evidence when it has one (at most 40 characters); never a topic label. '
    'relation says how the facts relate, and decides how the page will be drawn - choose what the evidence '
    'actually supports: single (one figure carries it), comparison (things set against each other), '
    'trend (one measure over time), proportion (part of a whole), ranking (one measure across several things), '
    'sequence (steps in order), timeline (dated events: fill date on each item), structure (parts of a whole), '
    'hierarchy (levels where each rests on the one below, such as infrastructure / model / application: list the '
    'top level first and the foundation last; only when the source itself describes levels), '
    'cause (one thing leading to another), cases (named examples for one claim). '
    'highlight is the focal fact {{value, text}}: the figure a reader should see first and the words that say what it measures. '
    'It must be the same figure the title carries. '
    'Each item is one concrete fact from the evidence: heading names it in a few words, value is its figure '
    '(only when it has one; a figure with its unit, like 12.1% or 316亿美元), body says what the figure measures, '
    'whose it is and when. Items are ordered by importance. Do not pad: fewer real facts beat filler, and never '
    'restate the title as an item. Do not repeat what other_page_titles cover. '
    'Never fill categories, values, chart_title, unit_format, series or figure: charts are drawn by code from data '
    'checked against the source. diversity.available_series lists that data when the evidence has it, and '
    'diversity.available_figures lists figures of the source document. When the claim of this page is about one of '
    'them, say so with its exact values in highlight or items (a series is then drawn as a chart on this page; a '
    'figure is then shown beside the text) - a drawn comparison is worth more than the same numbers in prose. '
    'Do not bend the claim toward data that does not bear on it. '
    'diversity.content_plan, when given, is the deck plan: argue this_page.claim, lead with this_page.lead and '
    'add this_page.increment; build the items from this_page.owns. A figure another page leads with or owns may '
    'appear here at most once, as context, never as the title, the highlight or an item value. '
    'If your own evidence plainly contradicts the plan, follow the evidence. '
    'When this_page.role is judgement, the page draws a strategic judgement from the pages in draws_on: state the '
    'judgement and its reasons; use figures only from your own evidence. When it is visual, the page is built '
    'around the figure named in this_page.visual. '
    'The source field uses exactly the form "<filename> 第N页" or "<filename> 第N-M页"; several pages are separated '
    'by "；". Item ref is optional and uses the same form. '
    'Fill headers + rows when several cases share the same attributes; cells are short. '
    'takeaway is one sentence on what this means for the audience. lead only if it adds a fact the title lacks. '
    'Density for this page - {tier_rule} '
    'Grounding: only the supplied evidence excerpts are evidence. Retain exact subjects, metric labels, '
    'denominators and periods. Never move a number between different purposes, cases or groups. Keep '
    'expected/projected/planned qualifiers. Percentage and percentage-point are different units. Use literal '
    'source observations; do not compute new aggregates. If no evidence supports a number, leave it out. '
    'source must identify an actual supplied filename, page or URL in source_references; for PDF evidence use the '
    'exact filename and physical PDF page, e.g. report.pdf 第12页, only pages appearing in the excerpts. Without '
    'source material leave source null. When revising, edit current_content per the instruction and keep the rest. '
    'Schema:\n'
)


async def generate_content(client, checkpoints, slot, brief, profile, *,
                           revision_instruction=None, force_regenerate=False, current_content: dict[str, Any] | None = None,
                           diversity: dict[str, Any] | None = None, source_references: list[str] | None = None, source_page_counts: dict[str, int] | None = None, evidence=None,
                           rich_tier: str | None = None, versioned: bool = False):
    """``rich_tier`` asks for the richer page model the HTML engine designs from. ``versioned`` keeps an
    automatic revision apart from the first version (a person's revision replaces it)."""
    from ppt_agent.v2.visual.content import RichContent
    source_references = source_references or re.findall(r'https?://[^\s]+', brief.source_digest or '')
    name = f'typeset/content_{slot.page_number:03d}.json'
    if revision_instruction and versioned:
        # A revision is its own result, keyed by everything that shapes it: the instruction, the content it
        # revises, the evidence and the rules. The first version is never overwritten, so a resumed run
        # starts from the same content and reaches the same decisions (round 7: overwriting it made each
        # resume review rewritten pages and remove a different set).
        key = hashlib.sha1(json.dumps([RULES_VERSION, revision_instruction, current_content,
                                       getattr(evidence, 'text', None)], ensure_ascii=False, sort_keys=True,
                                      default=str).encode('utf-8')).hexdigest()[:12]
        name = f'typeset/content_{slot.page_number:03d}.rev-{key}.json'
    cached = checkpoints.load(name)
    if force_regenerate and not (revision_instruction and versioned):
        cached = None
    adapter = TypeAdapter(RichContent) if rich_tier else content_adapter()
    if cached is not None:
        saved = cached['content']
        parsed = (RichContent.model_validate(saved) if saved.get('archetype') == 'rich'
                  else content_adapter().validate_python(saved))
        return parsed, cached['record']
    page_brief = slot.brief or PageBrief(title=slot.section_title or '内容要点')
    schema = adapter.json_schema()
    if evidence is not None:
        source_references = evidence.references
        source_page_counts = evidence.page_counts
        brief = brief.model_copy(update={'source_digest': evidence.text})
    suggested = suggested_archetype(page_brief.layout_hint)
    diversity = diversity or {}
    system = (
        'Return one JSON content object matching the schema. Choose an archetype and write content only. '
        'Never output coordinates. Respect every field length and item-count limit. '
        'Use the requested slide language. Retain exact source subjects, metric labels, denominators and periods. '
        'Never transfer a number between different purposes, cases or population groups. '
        'Preserve expected/projected/planned qualifiers; forecasts are not observed outcomes. '
        'Percentage and percentage-point increases are different units and must be named accurately. '
        'insights and metrics must be ordered by importance, '
        'with the first item carrying the core conclusion. Each page argues one point. '
        'title is one complete conclusion a reader could repeat, with the most important number from the '
        'evidence when it has one (at most 40 characters), never a topic label. Omit lead unless it adds a '
        'fact the title lacks. Build every page on specifics from the evidence: use at least two concrete '
        'facts (figures, named companies, cases, dates) when the evidence has them, instead of general advice. '
        'Do not repeat what other_page_titles cover. '
        'points defaults to 3 items, at most 4; item body 30-70 characters carrying one concrete fact. '
        'takeaway is one sentence on what this means for the audience. '
        'When a points page has one key figure in the evidence, also set highlight to {value, text}. '
        'chart needs 3-8 comparable values in one unit; table compares the same attributes across cases, '
        'regions or options, with short cells; the first metric in metrics is the page headline, and the number in the title must be that same '
        'metric (likewise the first insight of a chart); metric values are always figures. '
        'Prefer metrics, chart or table for supported quantitative evidence, and statement for key conclusions. '
        'The deck mix is planned in advance: use diversity.assigned_archetype. Only if the supplied '
        'evidence cannot honestly fill it (e.g. no numbers for metrics or chart), choose another from '
        'allowed_archetypes. '
        'source must identify an actual supplied filename, page or URL in source_references. '
        'Without source material leave source null; never cite user requirements as a source. When revising, edit current_content according to the instruction and preserve unrelated existing content. Never invent citations or unsupported numbers; '
        'if no evidence supports a number, omit it and use points or statement instead. '
        'Use literal source observations; do not introduce computed aggregates unless the user explicitly asks for calculations. '
        'Current content is not numerical evidence: remove any prior aggregate absent from these excerpts. '
        'Only the supplied evidence excerpts are numerical evidence; planning suggestions are not evidence. '
        'For PDF evidence, source MUST use exact filename and physical PDF page, e.g. report.pdf 第12页, '
        'and only pages appearing in these excerpts; never confuse printed page numbers with physical PDF pages. '
         'Preserve complete quantitative bundles from the evidence visibly: count plus amount, '
        'quantity plus growth, or budget plus procurement capacity belong together. '
        'Do not hide necessary paired numbers in speaker_notes or secondary value fields: '
        'include them in visible item notes or insight text. '
        'For raw percentage values like 47.1, use unit_format 0.0"%", not 0.0%. '
        'Chart categories and values must have identical lengths. Schema:\n' + json.dumps(schema, ensure_ascii=False)
    )
    if rich_tier:
        system = RICH_SYSTEM.format(tier=rich_tier, tier_rule=RICH_TIERS[rich_tier]) + json.dumps(schema, ensure_ascii=False)
    context = {'page_number': slot.page_number, 'page_brief': page_brief.model_dump(mode='json'),
               'section_title': slot.section_title, 'language': brief.language,
               'suggested_archetype': suggested, 'revision_instruction': revision_instruction,
               'current_content': current_content, 'diversity': diversity,
               'evidence': evidence.to_dict() if evidence is not None else None,
               'rich_tier': rich_tier}
    prompt_page = dict(context)
    if evidence is not None:
        prompt_page['evidence'] = {key: value for key, value in evidence.to_dict().items() if key != 'text'}
    user = json.dumps({'page': prompt_page, 'audience': brief.audience, 'purpose': brief.purpose,
                       'profile': profile.name, 'source_digest': brief.source_digest, 'current_content': current_content,
                       'key_points': [] if evidence is not None else brief.key_points, 'diversity': diversity,
                       'other_page_titles': diversity.get('other_page_titles', []),
                       'source_references': source_references}, ensure_ascii=False)
    errors = []
    invalid_sources = 0
    empty_sources = 0
    attempts = 0
    numeric_failures = []
    relation_failures = []
    numeric_candidate = None
    trims = []
    source_trims = []
    parsed = None
    # A page that cannot be written becomes a placeholder; the richer page model gets one more try.
    tries = 3 if rich_tier else 2
    for _ in range(tries):
        attempts += 1
        try:
            payload = await client.complete_json(task='page_content', system=system, user=user, context=context)
            payload, trimmed = trim_overlong_lists(payload)
            if trimmed:
                trims.extend(trimmed)
            parsed = adapter.validate_python(payload)
            if rich_tier:
                # Data to draw is attached by code after it is checked, never taken from the author.
                update = {'tier': rich_tier, 'series': [], 'figure': None, 'categories': None, 'values': None,
                          'chart_title': None, 'unit_format': None}
                if parsed.highlight is not None and not re.search(r'\d', parsed.highlight.value or ''):
                    update['highlight'] = None  # the focal fact is a figure, or there is none
                parsed = parsed.model_copy(update=update)
            empty_sources += int(not parsed.source or not parsed.source.strip())
            trimmed_source, dropped_pages = trim_source(parsed.source, source_references, source_page_counts,
                                                        evidence.allowed_pages if evidence is not None else None)
            if dropped_pages:
                parsed = parsed.model_copy(update={'source': trimmed_source})
                source_trims.append({'attempt': attempts, 'dropped': dropped_pages})
            if not source_is_valid(parsed.source, source_references, source_page_counts,
                                   evidence.allowed_pages if evidence is not None else None):
                invalid_sources += 1
                allowed = dict(evidence.allowed_pages) if evidence is not None and getattr(evidence, 'allowed_pages', None) else None
                raise ValueError(
                    f'source {parsed.source!r} is not valid: it must be a supplied filename, page or URL; '
                    'no source material means source=null. '
                    f'Valid references: {source_references}.'
                    + (f' Valid pages for this slide: {allowed}.' if allowed else ''))
            if evidence is not None and content_qa(parsed, slot.page_number).issues and not parsed.source:
                raise ValueError('numeric content must cite the supplied filename and physical page in source')
            if parsed.archetype == 'metrics' and any(
                    not re.search(r'[\d百千万亿倍半]', metric.value) for metric in parsed.metrics):
                raise ValueError('metrics values must be figures; use points for qualitative content')
            if parsed.archetype == 'points' and len(parsed.items) > 4:
                raise ValueError('points must have at most 4 items, normally 3')
            if diversity.get('allowed_archetypes') and parsed.archetype not in diversity['allowed_archetypes']:
                raise ValueError('archetype violates deck diversity; choose from ' + str(diversity['allowed_archetypes']))
            if parsed.archetype == 'chart' and len(parsed.categories) != len(parsed.values):
                raise ValueError('categories and values must have identical lengths')
            if parsed.archetype == 'chart' and len(parsed.categories) < 3:
                raise ValueError('chart needs at least 3 comparable values in one unit; use metrics for fewer')
            from ppt_agent.v2.evidence_check import check_content_numbers, number_contexts
            if rich_tier and evidence is not None:
                # Two true figures and a false line between them: an arrow from an increase to a total,
                # a doubling that is not one, a cause the source never gives.
                from ppt_agent.v2.relations import check_content as check_relations
                relation_problems = check_relations(parsed.model_dump(mode='json', exclude_none=True), evidence.text)
                if relation_problems:
                    relation_failures.append({'attempt': attempts, 'problems': relation_problems})
                    if attempts < tries:
                        raise ValueError('Relations between figures are not supported by the source:\n'
                                         + '\n'.join(relation_problems))
            parsed, repaired = repair_citation(parsed, evidence)
            if repaired:
                source_trims.append({'attempt': attempts, 'added': repaired})
            numeric_issues = check_content_numbers(parsed, cited_evidence(parsed.source, evidence))
            if numeric_issues:
                numeric_candidate = parsed
                numeric_failures.append({'attempt': attempts, 'issues': numeric_issues})
                quotes = number_contexts(numeric_issues, evidence) if evidence is not None else []
                raise ValueError('Numeric evidence check failed: ' + json.dumps(numeric_issues, ensure_ascii=False)
                                 + ('\nSource wording around these values (label each value with the words '
                                    'next to it in the source, cite that page, or drop the value):\n'
                                    + '\n'.join(quotes) if quotes else ''))
            break
        except BudgetExceededError as exc:
            errors.append({'attempt': attempts, 'kind': 'budget', 'error': str(exc)})
            parsed = None
            break
        except (ValidationError, ValueError, RuntimeError) as exc:
            errors.append({'attempt': attempts, 'kind': 'validation' if isinstance(exc, (ValidationError, ValueError)) else 'provider',
                           'error': str(exc)})
            parsed = None
            user += '\nPrevious output failed validation. Correct these specific errors:\n' + str(exc)
    fallback = parsed is None
    if fallback:
        parsed = numeric_candidate if numeric_candidate is not None else fallback_content(slot)
    from ppt_agent.v2.evidence_check import check_content_numbers, sanitize_content_numbers
    final_issues = check_content_numbers(parsed, cited_evidence(parsed.source, evidence))
    removed = []
    if final_issues:
        parsed, removed = sanitize_content_numbers(parsed, final_issues)
        if any(item['path'] == 'title' for item in removed):
            # Stripping a figure from a sentence breaks it; the planned title reads whole.
            planned = page_brief.title[:40]
            if not check_content_numbers(parsed.model_copy(update={'title': planned}),
                                         cited_evidence(parsed.source, evidence)):
                parsed = parsed.model_copy(update={'title': planned})
            else:
                parsed = parsed.model_copy(update={'title': (slot.section_title or '要点')[:40]})
        if parsed.archetype == 'statement' and any(item['path'] in ('statement', 'title') for item in removed):
            # The sentence the page exists for cannot lose a figure and still read.
            parsed = parsed.model_copy(update={'statement': parsed.title[:44]})
    if check_content_numbers(parsed, cited_evidence(parsed.source, evidence)):
        raise ValueError('Numeric cleanup did not eliminate unsupported numbers; refusing export')
    if not parsed.kicker and slot.section_title:
        parsed = parsed.model_copy(update={'kicker': slot.section_title[:24]})
    if page_brief.speaker_notes:
        parsed = parsed.model_copy(update={'speaker_notes': page_brief.speaker_notes})
    record = {'page_number': slot.page_number, 'attempts': attempts, 'fallback': fallback, 'revision': revision_instruction,
              'checkpoint': name,
              'validation_errors': errors, 'archetype': parsed.archetype,
              'source_invalid_attempts': invalid_sources, 'source_empty_attempts': empty_sources, 'source_trimmed': source_trims,
              'trimmed_items': trims, 'relation_check': relation_failures,
              'evidence': evidence.to_dict() if evidence is not None else None,
              'numeric_check': {'blocked_attempts': len(numeric_failures), 'failures': numeric_failures,
                                'passed_after_retry': bool(numeric_failures and not fallback),
                                'removed': removed}}
    checkpoints.save(name, {'content': parsed.model_dump(mode='json'), 'record': record})
    return parsed, record


# Statistics, not counting words: "3 项" or "5 个" in prose must not make a page look
# data-rich enough for metrics or a chart.
_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|亿|万|倍|元|美元|欧元)|\d+\.\d+")
_DATE = re.compile(r"(?:19|20)\d{2}\s*(?:年|[.\-/]\s*\d{1,2})")

ARCHETYPE_CAPS = {"points": 0.4, "process": 0.3, "statement": 0.15, "compare": 0.2, "timeline": 0.2,
                  "metrics": 0.3, "chart": 0.3, "table": 0.2}
_GENERIC = ["points", "process", "compare", "statement"]
_NUMERIC = ["metrics", "chart", "table"]


def has_numeric_evidence(text: str | None, minimum: int = 3) -> bool:
    """Enough quantified facts in a page's evidence to carry metrics or a chart."""
    return bool(text) and len(_NUMBER.findall(text)) >= minimum


def has_dated_evidence(text: str | None, minimum: int = 2) -> bool:
    """Real dates (2024年 / 2025.08) to put on a timeline, not stage numbers."""
    return bool(text) and len(_DATE.findall(text)) >= minimum


_TIER_CYCLE = ('standard', 'visual', 'standard', 'deep', 'standard', 'standard', 'deep', 'visual',
               'standard', 'standard', 'deep', 'standard', 'visual', 'standard', 'deep', 'standard',
               'standard', 'visual', 'standard', 'deep')  # 20% visual, 55% standard, 25% deep


def plan_tiers(slots, numeric: dict[int, bool]) -> dict[int, str]:
    """Density per content page: about 20% visual, 55% standard, 25% deep.

    A visual page needs a figure to carry it; without one it becomes standard.
    """
    tiers = {}
    for index, slot in enumerate(sorted(slots, key=lambda item: item.page_number)):
        tier = _TIER_CYCLE[index % len(_TIER_CYCLE)]
        if tier == 'visual' and not numeric.get(slot.page_number):
            tier = 'standard'
        tiers[slot.page_number] = tier
    return tiers


def plan_archetypes(slots, numeric: dict[int, bool], dated: dict[int, bool] | None = None) -> dict[int, str]:
    """Assign every content page an archetype up front, so pages can be generated
    concurrently while the deck still obeys the mix rules.

    Rules, in priority order: no two consecutive pages share an archetype;
    metrics/chart/table only where the page's evidence carries numbers, and there
    ahead of generic layouts, least used first, so data reaches the page; timeline only
    where planning asked for one and the evidence carries real dates; points <= 40%, process <= 30%, statement
    <= 15% and never a section's first page; every section gets a non-points
    page. When nothing satisfies every cap, caps are relaxed (process, then
    points) rather than forcing an archetype the content cannot fill.
    """

    slots = sorted(slots, key=lambda slot: slot.page_number)
    total = len(slots)
    caps = {kind: max(1, int(total * share)) for kind, share in ARCHETYPE_CAPS.items()}
    counts: dict[str, int] = {}
    plan: dict[int, str] = {}
    previous = None
    for index, slot in enumerate(slots):
        hint = slot.brief.layout_hint if slot.brief else "auto"
        preferred = suggested_archetype(hint)
        has_numbers = numeric.get(slot.page_number, False)
        section = slot.section_index
        first_in_section = index == 0 or slots[index - 1].section_index != section
        last_in_section = index == total - 1 or slots[index + 1].section_index != section
        section_kinds = [plan[s.page_number] for s in slots[:index] if s.section_index == section]
        numeric_kinds = sorted(_NUMERIC, key=lambda kind: counts.get(kind, 0)) if has_numbers else []
        candidates = numeric_kinds + [preferred] + _GENERIC
        has_dates = (dated or {}).get(slot.page_number, False)
        if hint == "timeline" and has_dates:
            candidates.insert(0, "timeline")
        if preferred == "timeline" and not has_dates:
            candidates[candidates.index("timeline")] = "process"  # stages without dates read as a process
        ordered = list(dict.fromkeys(candidates))

        def allowed(kind: str, relax: frozenset = frozenset()) -> bool:
            if kind == previous:
                return False
            if kind in _NUMERIC and not has_numbers:
                return False
            if kind == "timeline" and (hint != "timeline" or not has_dates):
                return False
            if kind == "statement" and first_in_section:
                return False
            # all() over an empty list is True: a one-page section must not be points.
            if kind == "points" and last_in_section and all(k == "points" for k in section_kinds):
                return False
            if kind in caps and kind not in relax and counts.get(kind, 0) >= caps[kind]:
                return False
            return True

        since_statement = next((i for i, s in enumerate(reversed(slots[:index]))
                                if plan[s.page_number] == 'statement'), index)
        if since_statement >= 6 and not first_in_section and previous != 'statement':
            ordered.insert(0, 'statement')  # a long run of dense pages needs somewhere to breathe
        choice = None
        soft = frozenset({"compare", "timeline", *_NUMERIC})
        for relax in (frozenset(), soft, soft | {"process"}, soft | {"process", "points"}):
            choice = next((kind for kind in ordered if allowed(kind, relax)), None)
            if choice:
                break
        choice = choice or ("points" if previous != "points" else "process")
        plan[slot.page_number] = choice
        counts[choice] = counts.get(choice, 0) + 1
        previous = choice
    return plan


def _page_references(record, references, page_counts):
    """The references a page was actually given (its evidence packet), else the deck's."""
    evidence = record.get('evidence') or {}
    if evidence.get('references'):
        return evidence['references'], evidence.get('page_counts') or page_counts, evidence.get('allowed_pages')
    return references, page_counts, None


_FRAME_SYSTEM = (
    'You write the opening and closing of a finished management deck. Return one JSON object: '
    '{"summary": {"rows": [{"tag": str, "headline": str, "detail": str}], "takeaway": str}, '
    '"roadmap": {"title": str, "phases": [str], "rows": [{"label": str, "body": str, "start": int, "end": int}], '
    '"takeaway": str}}. '
    'summary: 3-4 rows telling the whole argument: situation, opportunity, risk, then recommendation last. '
    'tag at most 6 characters; headline at most 36, a conclusion with its key number; detail at most 80, '
    'citing two concrete facts. takeaway at most 70: the decision the audience must make. '
    'roadmap: a title of at most 40 characters stating the plan; 3 phases such as time ranges, at most 10 characters; '
    '3-5 rows of recommended actions, label at most 8 and body at most 44 characters; start and end are '
    '1-based phase positions; takeaway at most 70: what must be decided now. '
    'Every figure must appear in the supplied pages: add no new statistics. Use the slide language.'
)


def _frame_fallback(skeleton, results):
    """An opening built from the pages themselves when the model call fails."""
    from ppt_agent.v2.visual.content import SummaryContent, SummaryRow
    rows, seen = [], set()
    for slot in sorted(skeleton.content_slots(), key=lambda item: item.page_number):
        if slot.section_index in seen or len(rows) == 4:
            continue
        seen.add(slot.section_index)
        content = results[slot.page_number][0]
        rows.append(SummaryRow(tag=f'第{len(rows) + 1}部分', headline=content.title[:36],
                               detail=(content.takeaway or content.lead or content.title)[:80]))
    return SummaryContent(title='执行摘要', rows=rows) if len(rows) >= 2 else None


async def generate_deck_frame(client, checkpoints, skeleton, brief, results, document_names):
    """Executive summary and roadmap, written once every page exists.

    Pages are generated apart and cannot see the whole argument; this call can.
    Its figures are checked against the finished pages, so it adds no new claims.
    """
    from ppt_agent.v2.evidence_check import check_content_numbers, sanitize_content_numbers
    from ppt_agent.v2.visual.content import RoadmapContent, SummaryContent
    cached = checkpoints.load('typeset/deck_frame.json')
    if cached is not None:
        return {'summary': SummaryContent.model_validate(cached['summary']) if cached.get('summary') else None,
                'roadmap': RoadmapContent.model_validate(cached['roadmap']) if cached.get('roadmap') else None}
    pages = []
    for number in sorted(results):
        data = results[number][0].model_dump(mode='json', exclude_none=True)
        for key in ('source', 'speaker_notes', 'archetype', 'kicker', 'unit_format', 'chart'):
            data.pop(key, None)
        pages.append(json.dumps(data, ensure_ascii=False))
    # A long deck must still fit one call behind the proxy's gateway timeout.
    limit = 600 if len(pages) <= 30 else 240
    user = json.dumps({'deck_title': skeleton.deck_title, 'audience': brief.audience, 'purpose': brief.purpose,
                       'pages': [page[:limit] for page in pages]}, ensure_ascii=False)
    evidence = '\n'.join(pages)
    source = '资料来源：' + '；'.join(document_names.values()) if document_names else None
    note = '行动建议为基于报告内容的推断，需结合公司实际调整'
    frame = {'summary': None, 'roadmap': None}
    for _ in range(2):
        try:
            payload = await client.complete_json(task='deck_frame', system=_FRAME_SYSTEM, user=user,
                                                 context={'deck_title': skeleton.deck_title})
            summary = SummaryContent(title='执行摘要', source=source, **payload['summary'])
            roadmap = RoadmapContent(source=note, **payload['roadmap'])
            for key, content in (('summary', summary), ('roadmap', roadmap)):
                issues = check_content_numbers(content, evidence)
                frame[key] = sanitize_content_numbers(content, issues)[0] if issues else content
            break
        except BudgetExceededError:
            break
        except (ValidationError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            user += '\nPrevious output failed validation: ' + str(exc)[:600]
    if frame['summary'] is None or frame['summary'].archetype != 'summary':
        frame['summary'] = _frame_fallback(skeleton, results)
    if frame['roadmap'] is not None and frame['roadmap'].archetype != 'roadmap':
        frame['roadmap'] = None
    checkpoints.save('typeset/deck_frame.json', {
        key: value.model_dump(mode='json') if value is not None else None for key, value in frame.items()})
    return frame


def _skeleton_sections(skeleton) -> list[tuple[str, int]]:
    """(section title, first page) in deck order, for the cover and TOC."""

    seen: dict[int, tuple[str, int]] = {}
    for slot in sorted(skeleton.slots, key=lambda item: item.page_number):
        if slot.section_index is not None and slot.section_index not in seen:
            title = slot.section_title or f"第 {slot.section_index} 部分"
            seen[slot.section_index] = (title, slot.page_number)
    return [seen[index] for index in sorted(seen)]


def name_categories(content, series_pool):
    """Items whose figure is one category's value in a verified series, headed by that category's
    name as the source gives it. Only a value found under exactly one category is matched."""

    if getattr(content, 'archetype', None) != 'rich' or not series_pool:
        return content, []
    squash = lambda text: re.sub(r'[\s、，,（）()·：:\-—]', '', str(text or ''))
    owners = {}
    for series in series_pool:
        if series.kind == 'time':
            continue
        for point in series.points:
            owners.setdefault(squash(point.display), set()).add(point.label)
    items, renamed = [], []
    for item in content.items:
        names = owners.get(squash(item.value)) if item.value else None
        if names and len(names) == 1:
            label = next(iter(names))
            core = squash(re.sub(r'[（(][^）)]*[）)]', '', label))
            if core and core not in squash(item.heading):  # the heading names the category, not a paraphrase of it
                if 3 <= len(label) <= 18:
                    renamed.append({'value': item.value, 'was': item.heading, 'now': label})
                    item = item.model_copy(update={'heading': label})
                else:
                    # A one-word category ("男") or a long one does not make a heading: the heading stays, and
                    # the source's name for the value leads the body.
                    body = f"{label}：{item.body or ''}"[:90]
                    renamed.append({'value': item.value, 'was': item.heading, 'now': f'正文前加「{label}」'})
                    item = item.model_copy(update={'body': body})
        items.append(item)
    return (content.model_copy(update={'items': items}) if renamed else content), renamed


RULES_VERSION = 'p4-fix8'  # part of every revision's cache key: new rules, new revisions
NEAR_PASSAGE = 3  # chunks of the source: how far a repeated page may move to find material of its own


async def build_typeset_deck(request, client, checkpoints, brief, skeleton, *, progress):
    # Lazy imports keep the orchestrator's delegation free of import cycles.
    from ppt_agent.v2.orchestrator import BuildResult, PageOutcome
    started = time.perf_counter()
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = PROFILES[getattr(request, 'style_profile', None) or getattr(brief, 'deck_type', 'corporate')]
    theme = profile.theme()
    previous_config = checkpoints.load('typeset_config.json') or {}
    checkpoints.save('theme.json', theme.model_dump(mode='json'))
    references = [Path(path).name for path in request.source_paths]
    page_counts = dict(previous_config.get('source_page_counts', {})) if request.resume else {}
    if request.resume and not request.source_paths:
        references = list(previous_config.get('source_references', []))
    from pypdf import PdfReader
    for path in request.source_paths:
        if Path(path).suffix.lower() == '.pdf':
            page_counts[Path(path).name] = len(PdfReader(path).pages)
    references += re.findall(r'https?://[^\s]+', brief.source_digest or '')
    document_names = dict(previous_config.get('source_display_names', {})) if request.resume else {}
    document_names.update(document_display_names(request.source_paths))
    # Rendering rules a run was accepted under stay with it: a run begun before bars were drawn from
    # zero keeps PowerPoint's own axis, so resuming a signed-off deck does not change what was signed.
    bars_from_zero = previous_config.get('bars_from_zero', not (request.resume and bool(previous_config)))
    type_roles = previous_config.get('type_roles', not (request.resume and bool(previous_config)))
    checkpoints.save('typeset_config.json', {'layout_engine': 'typeset', 'profile': profile.name,
                                           'bars_from_zero': bars_from_zero, 'type_roles': type_roles,
                                           'html_theme': getattr(request, 'html_theme', None),
                                           'source_references': references, 'source_page_counts': page_counts,
                                           'source_display_names': document_names})
    from ppt_agent.v2.evidence import EvidenceStore
    evidence_path = checkpoints.root / 'evidence_store.json'
    evidence_store = EvidenceStore.from_dict(json.loads(evidence_path.read_text())) if evidence_path.is_file() else None
    content_slots = sorted(skeleton.content_slots(), key=lambda slot: slot.page_number)

    chunk_use = Counter()

    def packet_for(slot):
        if evidence_store is None:
            return None
        section = skeleton.outline.sections[(slot.section_index or 1) - 1]
        query = " ".join([slot.brief.title, *slot.brief.points, slot.section_title or '']) if slot.brief else (slot.section_title or '')
        section_query = " ".join([section.title, section.goal, *section.talking_points])
        return evidence_store.select(query, section_query=section_query, used=chunk_use)

    # Evidence selection is local BM25, so every packet is known before any
    # model call; the archetype mix is then fixed up front and pages can be
    # generated concurrently instead of one after another.
    packets = {slot.page_number: packet_for(slot) for slot in content_slots}
    budget = None
    page_plans_first = None
    original_slots = None
    if getattr(request, 'html_theme', None) and evidence_store is not None and type_roles:
        # Pages the source can carry: a group of pages sharing too few facts keeps, moves or drops pages
        # before anything is written, so no page exists only to retell another.
        from ppt_agent.v2 import content_plan as deck_content, page_budget
        # (the outline's own pages, kept apart from the plan of the pages that remain, so a resumed run asks once)
        first_plan = await deck_content.plan(client, checkpoints, content_slots, packets,
                                             name='typeset/content_plan_outline.json')
        page_plans_first = first_plan
        deck_content.require(first_plan, [slot.page_number for slot in content_slots], checkpoints,
                             name='typeset/content_plan_outline.json')
        budget = await page_budget.decide(client, checkpoints, content_slots, packets, evidence_store,
                                          thin=deck_content.thin(first_plan, packets), content_plan=first_plan)
        skeleton, packets = page_budget.apply(skeleton, packets, budget, evidence_store)
        content_slots = sorted(skeleton.content_slots(), key=lambda slot: slot.page_number)
        # (the deck plan below is made again when pages moved or went: its pages are no longer these)
        moved = sum(entry['decision'] == 'retarget' for entry in budget['decisions'].values())
        progress(f"[content] page budget: {budget['supported_content_pages']} of {budget['requested_content_pages']} "
                 f"content pages supported, {moved} moved to uncovered material")
    plan = plan_archetypes(content_slots, {
        number: has_numeric_evidence(packet.text if packet is not None else None)
        for number, packet in packets.items()
    }, {
        number: has_dated_evidence(packet.text if packet is not None else None)
        for number, packet in packets.items()
    })
    checkpoints.save('typeset/archetype_plan.json', {str(k): v for k, v in plan.items()})
    semaphore = asyncio.Semaphore(max(1, request.concurrency))
    # The HTML engine designs from a richer page model, in three densities.
    rich = bool(getattr(request, 'html_theme', None))
    tiers = plan_tiers(content_slots, {
        number: has_numeric_evidence(packet.text if packet is not None else None, minimum=1)
        for number, packet in packets.items()}) if rich else {}
    if rich:
        checkpoints.save('typeset/tier_plan.json', {str(k): v for k, v in tiers.items()})

    # What the source offers to draw, read and checked before any page is written.
    series_found, series_read = {}, []
    if rich and evidence_store is not None:
        from ppt_agent.v2 import data_series
        from ppt_agent.v2.visual.content import DataSeries

        # Figures' labels and values paired by their place on the page (v2.figure_text). A run that
        # read its series before this existed keeps them: its pages, some signed off, were built on them.
        from ppt_agent.v2 import figure_text
        pairing = checkpoints.load('typeset/figure_pairs.json')
        if pairing is None:
            pairing = {'on': not (request.resume and (checkpoints.root / 'series').is_dir())}
            checkpoints.save('typeset/figure_pairs.json', pairing)
        pdfs = {Path(path).name: path for path in request.source_paths if str(path).lower().endswith('.pdf')}

        def series_evidence(packet):
            if packet is None or not pairing['on']:
                return packet.text if packet is not None else ''
            extra = [figure_text.blocks(pdfs[name], pages) for name, pages in packet.allowed_pages.items()
                     if name in pdfs]
            return '\n'.join([packet.text, *[block for block in extra if block]])

        async def read_series(slot):
            async with semaphore:
                return slot.page_number, await data_series.extract(client, checkpoints,
                                                                  series_evidence(packets[slot.page_number]))

        extracted = dict(await asyncio.gather(*(read_series(slot) for slot in content_slots)))
        series_read = list(extracted.values())
        from ppt_agent.v2 import completeness
        axes = {}
        # Whether each series is all the source gives or a selection of it (a top three is not a distribution).
        series_found = {number: [completeness.assess(DataSeries.model_validate(item), pdfs, axes)
                                 for item in result['series']]
                        for number, result in extracted.items() if result['series']}
        progress(f'[data] {len({data_series.signature(s) for found in series_found.values() for s in found})} '
                 'series verified against the source')

    source_figures = []
    if rich:
        from ppt_agent.v2 import figures as source_figure
        source_figures = checkpoints.load('typeset/figures.json')
        if source_figures is None or any(not (output_dir / 'assets' / item['id']).is_file() for item in source_figures):
            source_figures = source_figure.extract(request.source_paths, output_dir / 'assets') or (source_figures or [])
            checkpoints.save('typeset/figures.json', source_figures)
        if source_figures:
            progress(f'[data] {len(source_figures)} captioned figures found in the source')

    # What each page argues, planned across the deck before any page is written.
    page_plans = {}
    if rich and evidence_store is not None:
        from ppt_agent.v2 import content_plan as deck_content
        page_plans = await deck_content.plan(client, checkpoints, content_slots, packets)
        if type_roles:
            deck_content.require(page_plans, [slot.page_number for slot in content_slots], checkpoints)
        unplanned = sorted({slot.page_number for slot in content_slots} - set(page_plans))
        progress(f'[content] {len(page_plans)} page claims planned across the deck'
                 + (f'; NOT planned: {unplanned} (see typeset/content_plan.json failures)' if unplanned else ''))

    async def produce(slot, revision=None, current=None):
        assigned = plan[slot.page_number]
        diversity = {'assigned_archetype': assigned,
                     # Only data-bound assignments may fall back: a page planned as
                     # metrics/chart/timeline may lack the numbers or dates; a generic
                     # assignment (process, compare, ...) must be followed.
                     'allowed_archetypes': ({'chart': ['chart', 'metrics', 'table', 'points'],
                                             'metrics': ['metrics', 'table', 'points', 'statement'],
                                             'table': ['table', 'points', 'statement'],
                                             'timeline': ['timeline', 'points', 'statement'],
                                             }.get(assigned, [assigned])),
                     'deck_plan': [plan[s.page_number] for s in content_slots],
                     'other_page_titles': [s.brief.title for s in content_slots
                                           if s.brief and s is not slot],
                     'original_layout_hint': slot.brief.layout_hint if slot.brief else 'auto'}
        packet = packets[slot.page_number]
        async with semaphore:
            if rich:
                diversity = {'other_page_titles': diversity['other_page_titles']}
                if page_plans.get(slot.page_number):
                    diversity['content_plan'] = deck_content.brief_for(slot.page_number, page_plans)
                if series_found.get(slot.page_number):
                    diversity['available_series'] = [
                        {'metric': item.metric, 'scope': item.scope, 'period': item.period,
                         'values': {point.label: point.display for point in item.points}}
                        for item in series_found[slot.page_number]]
                nearby = [f"{item['label']} {item['title']}" for item in source_figures
                          if packet is not None and item['page'] in packet.allowed_pages.get(item['document'], [])]
                if nearby:
                    diversity['available_figures'] = nearby
            content, record = await generate_content(client, checkpoints, slot, brief, profile,
                                                    revision_instruction=revision, force_regenerate=revision is not None,
                                                    current_content=current,
                                                    diversity=diversity, source_references=references,
                                                    source_page_counts=page_counts, evidence=packet,
                                                    rich_tier=tiers.get(slot.page_number), versioned=rich and bool(type_roles))
        record = {**record, 'assigned_archetype': assigned,
                  'archetype_downgraded': content.archetype != assigned}
        if record.get('evidence') is not None:
            from ppt_agent.v2.evidence import EvidencePacket
            packet = EvidencePacket(**record['evidence'])
        if packet is not None:
            references_for_page, page_counts_for_page = packet.references, packet.page_counts
        else:
            references_for_page, page_counts_for_page = references, page_counts
        from ppt_agent.v2.evidence_check import check_content_numbers, sanitize_content_numbers
        issues = check_content_numbers(content, cited_evidence(content.source, packet))
        if issues:
            content, removed = sanitize_content_numbers(content, issues)
            check_record = dict(record.get('numeric_check', {}))
            check_record['removed'] = check_record.get('removed', []) + removed
            check_record['cached_cleanup'] = True
            record = {**record, 'numeric_check': check_record}
        content, format_notes = normalize_chart_format(content)
        if format_notes:
            record = {**record, 'format_normalizations': record.get('format_normalizations', []) + format_notes}
        if packet is not None:
            # The numeric check above was scoped to the model's own citation, so those
            # pages are the tighter truth; fall back to everything delivered.
            citation = None if content.source and source_is_valid(
                content.source, packet.references, packet.page_counts, packet.allowed_pages
            ) else packet_citation(packet)
            if citation:
                record = {**record, 'model_source': record.get('model_source', content.source),
                          'citation_basis': 'all evidence ranges delivered for this page; not per-claim proof'}
                content = content.model_copy(update={'source': citation})
        # Old caches are preserved, but fabricated attribution is never displayed.
        if not source_is_valid(content.source, references_for_page, page_counts_for_page,
                               packet.allowed_pages if packet is not None else None):
            record = {**record, 'source_invalid_cached': True}
            content = content.model_copy(update={'source': None})
        checkpoints.save(record.get('checkpoint') or f'typeset/content_{slot.page_number:03d}.json',
                         {'content': content.model_dump(mode='json'), 'record': record})
        progress(f'[content] page {slot.page_number} ready')
        return slot.page_number, (content, record)

    results = dict(await asyncio.gather(*(produce(slot) for slot in content_slots)))
    if rich and evidence_store is not None and type_roles:
        # A page that failed its number check three times is diagnosed, not left as a placeholder: each rejected
        # figure is classed (printed on another source page / bare chart label / computed / unsupported) and the
        # page is written once more with that diagnosis, the pages holding mismatched citations added to its
        # evidence. If it fails again it is reported for review, under its planned claim. Run after the pages
        # are written and again after the overlap review, which rewrites and moves pages.
        from ppt_agent.v2 import content_recovery
        by_number = {slot.page_number: slot for slot in content_slots}
        by_chunk = {chunk['chunk_id']: chunk for chunk in evidence_store.chunks}
        names = {document['doc_id']: document['name'] for document in evidence_store.documents}

        async def recover(number):
            content, record = results[number]
            diagnosis = content_recovery.diagnose(record, evidence_store, moved=number in retargeted)
            if diagnosis['recoverable']:
                wanted = content_recovery.pages_to_add(diagnosis)
                packet = packets.get(number)
                # The pages holding the rejected figures go first, so the size limit never cuts them.
                chunks = [chunk for chunk in evidence_store.chunks
                          if set(chunk['pages']) & wanted.get(names.get(chunk['doc_id']), set())]
                chunks += [by_chunk[chunk_id] for chunk_id in (packet.chunk_ids if packet is not None else [])
                           if chunk_id in by_chunk and by_chunk[chunk_id] not in chunks]
                packets[number] = evidence_store.packet_of(chunks, max_chars=9500)
                _, (new_content, new_record) = await produce(by_number[number], revision=content_recovery.instruction(diagnosis),
                                                             current=content.model_dump(mode='json', exclude_none=True))
                if not new_record.get('fallback'):
                    new_record = {**new_record, 'content_status': 'CONTENT_RECOVERED', 'content_failure': diagnosis}
                    checkpoints.save(f'typeset/final/content_{number:03d}.json',
                                     {'content': new_content.model_dump(mode='json'), 'record': new_record})
                    return number, (new_content, new_record)
                content, record = new_content, new_record
            planned = None if number in retargeted else (page_plans or {}).get(number) or (page_plans_first or {}).get(number)
            title = content_recovery.title_for(planned, content.title)
            if title != content.title and content.title == (by_number[number].section_title or '')[:40]:
                content = content.model_copy(update={'title': title})
            # What failed the check does not stay on the page: it goes to the notes, marked unverified (P5 P0).
            dumped, removed = content_recovery.quarantine(content.model_dump(mode='json'), list(diagnosis.get('values') or {}),
                                                          (by_number[number].section_title or content.title or '')[:40])
            if removed:
                try:
                    content = type(content).model_validate(dumped)
                except ValueError:
                    pass  # the page cannot stand without them; it stays for review as it is
            record = {**record, 'content_status': 'NEEDS_CONTENT_REVIEW', 'content_failure': diagnosis,
                      'quarantined': removed}
            checkpoints.save(f'typeset/final/content_{number:03d}.json', {'content': content.model_dump(mode='json'), 'record': record})
            return number, (content, record)

        async def recover_failed(stage):
            failed = [number for number, (_, record) in results.items()
                      if record.get('fallback') and record.get('content_status') != 'NEEDS_CONTENT_REVIEW']
            if failed:
                results.update(dict(await asyncio.gather(*(recover(number) for number in failed))))
                progress(f"[content] {stage}: {len(failed)} page(s) failed their number check: "
                         + ", ".join(f"{n} {results[n][1].get('content_status')}" for n in sorted(failed)))

        retargeted: set[int] = set()
        await recover_failed('written')
    if rich and evidence_store is not None:
        # Pages that still argue one conclusion twice (checked even where planning failed): the later (or straying) one is rewritten once.
        signed = set((checkpoints.load('html/accepted.json') or {}).get('pages', []))
        by_number = {slot.page_number: slot for slot in content_slots}
        overlap = await deck_content.review(
            client, checkpoints, {number: content.model_dump(mode='json', exclude_none=True)
                                  for number, (content, _) in results.items()}, page_plans, signed)
        rewrites = overlap['rewrite']
        if rewrites:
            rewritten = await asyncio.gather(*(
                produce(by_number[page], revision=text,
                        current=results[page][0].model_dump(mode='json', exclude_none=True))
                for page, text in sorted(rewrites.items())))
            # A recovered page keeps its recovery record through an overlap rewrite.
            rewritten = [(page, (content, record if record.get('fallback') else
                                 {**record, **{key: results[page][1][key] for key in ('content_status', 'content_failure')
                                               if key in results[page][1] and key not in record}}))
                         for page, (content, record) in rewritten]
            results.update(dict(rewritten))
            overlap = await deck_content.review(
                client, checkpoints, {number: content.model_dump(mode='json', exclude_none=True)
                                      for number, (content, _) in results.items()}, page_plans, signed,
                rewritten=overlap)
        left = overlap.get('left') or []
        removed: set[int] = set()
        if left and evidence_store is not None and type_roles:
            # Rewriting did not part them: the later page moves to source material no page has used, or the
            # repeat is reported - it is never just retitled again.
            from ppt_agent.v2 import page_budget
            spare = page_budget.uncovered(evidence_store, packets, limit=len(evidence_store.chunks))
            by_id = {chunk['chunk_id']: chunk for chunk in evidence_store.chunks}
            moved = {}
            for pair in left:
                page = max(pair)
                if page in signed or page in moved or page in removed:
                    continue
                if not spare:
                    removed.add(page)
                    continue
                # The unused passage nearest in the source to what the page was about, so a moved page stays
                # with its section (CNNIC 57: the first unused passages, 政府网站, landed between AI pages).
                order = {item['chunk_id']: index for index, item in enumerate(evidence_store.chunks)}
                own = sorted(order[chunk_id] for chunk_id in (packets[page].chunk_ids if packets.get(page) else [])
                             if chunk_id in order)
                centre = own[len(own) // 2] if own else 0
                chunk = min(spare, key=lambda item: (abs(order.get(item['chunk_id'], 0) - centre), order.get(item['chunk_id'], 0)))
                if abs(order.get(chunk['chunk_id'], 0) - centre) > NEAR_PASSAGE:
                    # No unused passage near what the page was about: material from another part of the
                    # report would sit off-topic in this section, so the page goes (the deck is one shorter).
                    removed.add(page)
                    continue
                spare.remove(chunk)
                packets[page] = evidence_store.packet_of([by_id[chunk['chunk_id']]])
                moved[page] = chunk['chunk_id']
            if moved:
                renewed = await asyncio.gather(*(
                    produce(by_number[page], revision=(
                        f'本页和别页讲的是同一件事，已改用资料里还没有任何一页用过的段落（{moved[page]}）作为证据。'
                        '按这份证据重新写一页：观点、焦点数字和条目都来自它，不要再讲原来的主题。'),
                        current=None) for page in sorted(moved)))
                results.update(dict(renewed))
                overlap = await deck_content.review(
                    client, checkpoints, {number: content.model_dump(mode='json', exclude_none=True)
                                          for number, (content, _) in results.items()}, page_plans, signed,
                    rewritten=overlap)
                overlap['retargeted'] = moved
        checkpoints.save('typeset/content_overlap.json', overlap)
        progress(f"[content] {len(overlap['repeated'])} repeated pair(s) found, {len(rewrites)} page(s) rewritten, "
                 f"{len(overlap.get('retargeted', {}))} moved to uncovered material, {len(overlap.get('left', []))} left")
        if removed:
            overlap['removed'] = sorted(removed)
            for page in removed:
                content, record = results[page]
                results[page] = (content, {**record, 'content_status': 'PAGE_REMOVED',
                                           'removed_because': next(pair for pair in left if page in pair)})
            original_slots = list(skeleton.slots)
            content_slots = [slot for slot in content_slots if slot.page_number not in removed]
            skeleton = skeleton.model_copy(update={'slots': [slot for slot in skeleton.slots if slot.page_number not in removed]})
            checkpoints.save('typeset/content_overlap.json', overlap)
            progress(f"[content] removed page(s) {sorted(removed)}: still repeating another page, no unused material near them")
        if type_roles:
            # A page rewritten or moved by the overlap review can fail its number check like any other.
            retargeted.update(int(page) for page in overlap.get('retargeted', {}))
            await recover_failed('after overlap review')
        if type_roles and getattr(request, 'claim_audit', False):
            # The last look at what each page argues, once recovery, rewrites and removals are done.
            from ppt_agent.v2 import claim_audit
            auditor = client
            try:
                from ppt_agent import deck_jobs
                auditor = deck_jobs.create_v2_review_client()  # another model than the writer's
            except Exception as exc:  # noqa: BLE001
                progress(f'[content] claim audit uses the writing model: no reviewer configured ({exc})')

            async def audited(numbers):
                found = await asyncio.gather(*(claim_audit.audit_page(
                    auditor, checkpoints, number, results[number][0].model_dump(mode='json', exclude_none=True),
                    getattr(packets.get(number), 'text', '') or '') for number in numbers))
                return dict(zip(numbers, found))

            numbers = [slot.page_number for slot in content_slots]
            verdicts = await audited(numbers)

            def decided(number):
                return claim_audit.decide(verdicts[number], results[number][0].model_dump(mode='json', exclude_none=True))

            failing = [number for number in numbers if decided(number)['rewrite'] and number not in signed]
            defects = []

            def moved_out(content, indices):
                """REMOVE_ITEM: the unrelated items go to the notes; the page is not rewritten for them."""
                if not indices:
                    return content
                moved = [content.items[index] for index in indices]
                kept = [item for index, item in enumerate(content.items) if index not in indices]
                note = '与本页论点无关、移出页面的事实：' + '；'.join(
                    ' '.join(str(part) for part in (getattr(item, 'heading', None), getattr(item, 'value', None),
                                             getattr(item, 'body', None) or getattr(item, 'text', None)) if part)
                        for item in moved)  # a plain point has no value (crashed a 100-page run)
                return content.model_copy(update={'items': kept, 'speaker_notes': (
                    (content.speaker_notes + '\n') if content.speaker_notes else '') + note})

            async def repair(number):
                # Minimal, located repairs instead of a new page: unrelated items out, then the claim (title and
                # lead) rephrased from the page's own facts, verified by the audit; one more try, then a person.
                content, record = results[number]
                before = claim_audit.content_hash(content.model_dump(mode='json', exclude_none=True))
                decision = decided(number)
                content = moved_out(content, decision['move_items'])
                steps, verdict = [], verdicts[number]
                for attempt in (1, 2):
                    dumped = content.model_dump(mode='json', exclude_none=True)
                    proposal = await claim_audit.rephrase(client, checkpoints, number, dumped, decision)
                    if not proposal:
                        steps.append({'attempt': attempt, 'op': 'REPHRASE_CLAIM', 'result': 'rejected: added a figure or failed'})
                        continue
                    candidate = content.model_copy(update=proposal)
                    verdict = await claim_audit.audit_page(auditor, checkpoints, number,
                                                           candidate.model_dump(mode='json', exclude_none=True),
                                                           getattr(packets.get(number), 'text', '') or '')
                    decision = claim_audit.decide(verdict, candidate.model_dump(mode='json', exclude_none=True))
                    steps.append({'attempt': attempt, 'op': 'REPHRASE_CLAIM', 'proposal': proposal,
                                  'verdict': verdict.get('verdict'), 'still': decision['rewrite']})
                    content = moved_out(candidate, decision['move_items'])
                    if not decision['rewrite']:
                        break
                after = claim_audit.content_hash(content.model_dump(mode='json', exclude_none=True))
                status = 'VERIFIED_FIXED' if not decision['rewrite'] else 'NEEDS_REVIEW'
                defects.append({'defect_id': f'claim-{number:03d}', 'slide_id': f'p{number:03d}', 'defect_type': 'claim_evidence',
                                'findings': decided(number)['rewrite'], 'ops': steps, 'before_hash': before, 'after_hash': after,
                                'resolved': status})
                return number, (content, record), verdict

            if failing:
                from ppt_agent.v2 import content_plan as deck_content
                before = {number: (results[number], verdicts[number]) for number in failing}
                old_pairs = {tuple(pair['pages']) for pair in deck_content.suspects(
                    {n: c.model_dump(mode='json', exclude_none=True) for n, (c, _) in results.items() if n in numbers})}
                repaired = await asyncio.gather(*(repair(number) for number in failing))
                for number, result, verdict in repaired:
                    results[number] = result
                    verdicts[number] = verdict
                # A repair must not make two pages repeat each other; such a repair is undone (rolled back).
                for pair in deck_content.suspects({n: c.model_dump(mode='json', exclude_none=True)
                                                   for n, (c, _) in results.items() if n in numbers}):
                    for number in set(pair['pages']) & set(failing):
                        if tuple(pair['pages']) not in old_pairs:
                            (content, record), verdicts[number] = before[number]
                            results[number] = (content, {**record, 'claim_audit_reverted': pair})
                            for defect in defects:
                                if defect['slide_id'] == f'p{number:03d}':
                                    defect.update(resolved='NEEDS_REVIEW', rolled_back=pair)
            decisions = {number: decided(number) for number in numbers}
            for number in numbers:
                content, record = results[number]
                decision = decisions[number]
                if decision['move_items'] and number not in signed and number not in failing:
                    # An unrelated fact among the items goes to the notes; the page is not rewritten for it.
                    moved = [content.items[index] for index in decision['move_items']]
                    kept = [item for index, item in enumerate(content.items) if index not in decision['move_items']]
                    note = '与本页论点无关、移出页面的事实：' + '；'.join(
                        ' '.join(str(part) for part in (getattr(item, 'heading', None), getattr(item, 'value', None),
                                             getattr(item, 'body', None) or getattr(item, 'text', None)) if part)
                        for item in moved)  # a plain point has no value (crashed a 100-page run)
                    content = content.model_copy(update={'items': kept, 'speaker_notes': (
                        (content.speaker_notes + '\n') if content.speaker_notes else '') + note})
                record = {**record, 'claim_audit': {**verdicts[number], 'decision': decision},
                          'claim_audit_rewritten': number in failing}
                if decision['rewrite'] or verdicts[number].get('verdict') == 'error':
                    record['content_status'] = 'NEEDS_CONTENT_REVIEW'
                results[number] = (content, record)
                checkpoints.save(f'typeset/final/content_{number:03d}.json', {'content': content.model_dump(mode='json'), 'record': record})
            audit_summary = claim_audit.summary(verdicts)
            checkpoints.save('typeset/defects.json', defects)
            audit_summary.update(rewritten=failing, repairs={d['slide_id']: d['resolved'] for d in defects}, still_failing=[n for n in numbers if decisions[n]['rewrite']],
                                 items_moved={str(n): decisions[n]['move_items'] for n in numbers if decisions[n]['move_items']},
                                 minor={str(n): decisions[n]['minor'] for n in numbers if decisions[n]['minor']})
            checkpoints.save('typeset/claim_audit.json', audit_summary)
            progress(f"[content] claim audit: {len(failing)} page(s) rewritten {failing}; still failing {audit_summary['still_failing']}; "
                     f"items moved to notes {audit_summary['items_moved']}; "
                     f"audit errors {audit_summary['error']}")
    if type_roles and evidence_store is not None:
        # A figure printed verbatim is only as good as the source's agreement with itself (CNNIC 57
        # says "1717万人" on p9 and "17179万人" on p19 for the same growth); such a page goes to a person.
        from ppt_agent.v2 import source_conflicts
        held = {}
        for number, (content, record) in results.items():
            if record.get('content_status') == 'PAGE_REMOVED':
                continue
            found = source_conflicts.conflicts(content.model_dump(mode='json', exclude_none=True), evidence_store)
            if found:
                held[number] = found
                record = {**record, 'source_conflicts': found, 'content_status': 'NEEDS_CONTENT_REVIEW'}
                results[number] = (content, record)
                checkpoints.save(f'typeset/final/content_{number:03d}.json',
                                 {'content': content.model_dump(mode='json'), 'record': record})
        if held:
            progress(f"[content] source states a figure two ways on page(s) {sorted(held)}: "
                     + '; '.join(f"{f['figure']} (p{f['page']}) vs {f['other']} (p{f['other_page']})"
                                 for found in held.values() for f in found))
    if type_roles:
        # A content page left with no fact to show is removed and the deck renumbered, not shown for review
        # (scale test: a 100-page run kept a slide holding only its section name and "01").
        from ppt_agent.v2 import content_recovery as _recovery
        invalid = {}
        for number, (content, record) in results.items():
            if record.get('content_status') == 'PAGE_REMOVED':
                continue
            why = _recovery.invalid(content.model_dump(mode='json', exclude_none=True), record)
            if why:
                invalid[number] = why
                results[number] = (content, {**record, 'content_status': 'PAGE_REMOVED', 'removed_because': why})
        if invalid:
            original_slots = original_slots or list(skeleton.slots)
            content_slots = [slot for slot in content_slots if slot.page_number not in invalid]
            skeleton = skeleton.model_copy(update={'slots': [slot for slot in skeleton.slots if slot.page_number not in invalid]})
            progress(f"[content] removed page(s) {sorted(invalid)}: no fact left to show ({'; '.join(sorted(set(invalid.values())))})")
    series_summary = None
    if rich and evidence_store is not None:
        # What each page shows is decided from what the page claims (v2.visual_brief), among the
        # things the source offers it: series from its own evidence, columns of verified tables
        # and figures on the pages it cites.
        from ppt_agent.v2 import tables as source_table, visual_brief
        dumps = {number: content.model_dump(mode='json', exclude_none=True) for number, (content, _) in results.items()}
        found_tables = [table for path in request.source_paths if str(path).lower().endswith('.pdf')
                        for table in source_table.captions(path)]

        async def read_table(table):
            async with semaphore:
                return await source_table.read(client, checkpoints, table)

        read_tables = list(await asyncio.gather(*(read_table(table) for table in found_tables)))
        table_series = [(table, DataSeries.model_validate(item)) for table in read_tables
                        for item in source_table.series_of(table)]
        usable_figures = [item for item in source_figures if (output_dir / 'assets' / item['id']).is_file()]
        assessments = {}
        approved_redraws = set((checkpoints.load('figures/approved.json') or {}).get('ids', []))

        def series_id(item):
            # A series is its data: the same values under another metric wording are the same chart, used once.
            from ppt_agent.v2.htmldeck.charts import series_key
            return series_key(item.model_dump())

        async def brief_page(number):
            content = dumps[number]
            candidates = []
            def cited(item):
                # A series read from another page's evidence, every value of it on pages this page cites.
                places = [point.ref.rsplit(' ', 1) for point in item.points]
                return all(len(place) == 2 and re.fullmatch(r'第\d+页', place[1]) and
                           int(place[1][1:-1]) in source_figure._cited(content.get('source'), place[0])
                           for place in places)

            elsewhere = [item for other, found in series_found.items() if other != number
                         for item in found if cited(item)]
            for item in data_series.prune(series_found.get(number, []) + elsewhere + [
                    item for table, item in table_series
                    if table['page'] in source_figure._cited(content.get('source'), table['document'])]):
                places = {}
                for point in item.points:
                    match = re.fullmatch(r'(.+?) 第(\d+)(?:-(\d+))?页', point.ref)
                    if match:
                        places.setdefault(match.group(1), set()).update(
                            range(int(match.group(2)), int(match.group(3) or match.group(2)) + 1))
                candidates.append({'id': series_id(item), 'type': '核对过的数据序列', 'metric': item.metric,
                                   'pages': {name: sorted(numbers) for name, numbers in places.items()},
                                   **({'coverage': item.selection} if item.coverage == 'excerpt' else {}),
                                   'scope': item.scope, 'period': item.period,
                                   'values': {point.label: point.display for point in item.points}, 'payload': item})
            for figure in usable_figures:
                if figure['page'] not in source_figure._cited(content.get('source'), figure['document']):
                    continue
                async with semaphore:
                    verdict = await source_figure.assess(client, checkpoints, figure, output_dir / 'assets' / figure['id'])
                assessments[figure['id']] = verdict
                if verdict['use'] == 'redraw' and figure['id'] not in approved_redraws:
                    # Three automatic looks have still passed a redrawing that flattened a nested box.
                    # A redrawn figure is used only once a person has compared it with the original.
                    verdict = {**verdict, 'use': 'original' if verdict.get('legible') else 'evidence',
                               'why': '可以重画，但还没有人对照原图确认过；在确认之前'
                                      + ('用原图' if verdict.get('legible') else '只作为出处保留'),
                               'redraw_proposed': verdict['redraw']}
                    assessments[figure['id']] = verdict
                if verdict['use'] == 'evidence':
                    continue  # not legible on a slide and not safely redrawn: cited, never shown
                candidates.append({'id': 'figure:' + figure['id'],
                                   'type': '可按原样重画的图' if verdict['use'] == 'redraw' else '资料原图（原样放上页面）',
                                   'caption': f"{figure['label']} {figure['title']}", 'holds': verdict.get('redraw') or verdict.get('kind'),
                                   'payload': (figure, verdict)})
            async with semaphore:
                choice = await visual_brief.decide(client, checkpoints, number, content, candidates)
            chosen = next((candidate for candidate in candidates if candidate['id'] == choice['use']), None)
            if chosen is not None and chosen['id'].startswith('series:') and type_roles:
                # The final gate is the data: a chart that measures another subject is not evidence, whatever
                # the brief or the second look said.
                bound = visual_brief.binding(content, chosen)
                choice = {**choice, 'binding': bound}
                if bound['relation'] == 'topic':
                    choice = {**choice, 'use': 'none', 'rejected': {'id': chosen['id'], 'why': bound['why']}}
                    chosen = None
            if chosen is not None:
                async with semaphore:
                    check = await visual_brief.supports(client, checkpoints, number, content, chosen, choice)
                if not check['supports']:
                    # Shown beside a claim it does not evidence, true data misleads: the page goes without it.
                    choice = {**choice, 'use': 'none', 'rejected': {'id': chosen['id'], 'why': check['why']}}
            return number, candidates, choice

        briefed = await asyncio.gather(*(brief_page(number) for number in sorted(dumps)))
        settled = visual_brief.settle({number: choice for number, _, choice in briefed})
        placed, shown, redrawn = {}, {}, {}
        async def relevant(number):
            content = results[number][0]
            if not type_roles or getattr(content, 'archetype', None) != 'rich':
                return number, None
            # Every fact on the page evidences its claim; padding goes to the notes, nothing is lost.
            async with semaphore:
                return number, await visual_brief.unrelated_items(
                    client, checkpoints, number, content.model_dump(mode='json', exclude_none=True),
                    settled[number].get('claim'))

        relevance = dict(await asyncio.gather(*(relevant(number) for number, _, _ in briefed)))
        for number, candidates, _ in briefed:
            choice = settled[number]
            chosen = next((candidate for candidate in candidates if candidate['id'] == choice['use']), None)
            update = {'series': [], 'figure': None, 'diagram': None, 'visual': None}
            if chosen is not None:
                update['visual'] = {key: choice[key] for key in ('claim', 'core_metric', 'role', 'focus')}
                if chosen['id'].startswith('series:'):
                    update['series'] = placed[number] = [chosen['payload']]
                else:
                    figure, verdict = chosen['payload']
                    credit = (' · 来源：' + figure['credit']) if figure.get('credit') else ''
                    if verdict['use'] == 'redraw':
                        update['diagram'] = redrawn[number] = {
                            **verdict['redraw'], 'figure': figure['id'],
                            'caption': f"据原文{figure['label']}「{figure['title']}」重绘{credit} · 原文第 {figure['page']} 页"}
                    else:
                        update['figure'] = shown[number] = {
                            'id': figure['id'], 'caption': source_figure.caption(figure), 'ref': figure['ref'],
                            'ratio': round(figure['width'] / figure['height'], 3), 'min_width': verdict.get('min_width')}
            content, record = results[number]
            content = content.model_copy(update=update)
            # A value of a verified series written on the page goes under the source's own name for
            # its category ("生成会议纪要、PPT", not "办公提效"), whether or not the chart is drawn.
            # (every verified series whose values sit on pages this page cites, offered here or not)
            pool = [item for found in series_found.values() for item in found
                    if all(len(place := point.ref.rsplit(' ', 1)) == 2 and re.fullmatch(r'第\d+页', place[1])
                           and int(place[1][1:-1]) in source_figure._cited(content.source, place[0])
                           for point in item.points)]
            content, renamed = name_categories(content, pool)
            if renamed:
                record = {**record, 'category_names_restored': renamed}
            verdict = relevance.get(number) or {'unrelated': []}
            if verdict['unrelated']:
                moved = [item for index, item in enumerate(content.items, 1) if index in verdict['unrelated']]
                kept = [item for index, item in enumerate(content.items, 1) if index not in verdict['unrelated']]
                note = '页面未展开（与本页论点无直接关系）：' + '；'.join(
                    ' '.join(str(part) for part in (getattr(item, 'heading', None), getattr(item, 'value', None),
                                             getattr(item, 'body', None) or getattr(item, 'text', None)) if part)
                        for item in moved)  # a plain point has no value (crashed a 100-page run)
                content = content.model_copy(update={'items': kept, 'speaker_notes': (
                    (content.speaker_notes + '\n') if content.speaker_notes else '') + note})
                record = {**record, 'unrelated_items_moved': {'items': [item.heading for item in moved],
                                                              'why': verdict['why']}}
            results[number] = (content, record)
        series_summary = {**data_series.summary(series_read, placed),
                          'tables_found': len(read_tables), 'tables_verified': sum(bool(table['rows']) for table in read_tables),
                          'source_figures': len(source_figures), 'pages_with_figure': sorted(shown),
                          'pages_with_redrawn_figure': sorted(redrawn),
                          'pages_offered_a_visual': sorted(number for number, candidates, _ in briefed if candidates),
                          'pages_that_declined': sorted(number for number, candidates, _ in briefed
                                                        if candidates and settled[number]['use'] == 'none')}
        checkpoints.save('typeset/visual_plan.json', {
            'summary': series_summary,
            'pages': {str(number): {'offered': [{key: value for key, value in candidate.items() if key != 'payload'}
                                                for candidate in candidates], 'brief': settled[number]}
                      for number, candidates, _ in briefed if candidates},
            'figures': {name: {key: verdict.get(key) for key in ('use', 'why', 'kind', 'elements', 'shown_text_px', 'check', 'redraw_proposed')}
                        for name, verdict in sorted(assessments.items())},
            'tables': [{key: table[key] for key in ('label', 'title', 'page', 'problems')} | {'rows': len(table['rows'])}
                       for table in read_tables],
            'series_rejected': [entry for result in series_read for entry in result['rejected']]})
    frame = None
    if profile.name == 'consulting' and len(content_slots) >= 4:
        frame = await generate_deck_frame(client, checkpoints, skeleton, brief, results, document_names)
    content_seconds = time.perf_counter() - started
    sections = _skeleton_sections(skeleton)
    website_titles = {doc['name']: doc.get('title', '') for doc in evidence_store.documents
                      if doc.get('page_kind') == 'web'} if evidence_store is not None else {}
    html_pages, html_summary = {}, None
    html_theme = getattr(request, 'html_theme', None)
    if html_theme:
        # Model-designed pages. Anything that fails keeps the page the layout
        # engine builds below, so a deck is never short a page.
        from ppt_agent.v2.htmldeck import browser as html_browser, themes as html_themes
        from ppt_agent.v2.htmldeck.deck import design_deck, job_for
        theme = html_themes.get(html_theme).spec
        checkpoints.save('theme.json', theme.model_dump(mode='json'))
        jobs, fixed = [], []
        summary_job_placed = False
        for slot in sorted(skeleton.slots, key=lambda item: item.page_number):
            framed = frame.get({'toc': 'summary', 'closing': 'roadmap'}.get(slot.kind)) if frame else None
            if slot.kind == 'content':
                jobs.append(job_for(display_copy(results[slot.page_number][0], website_titles, document_names),
                                    page_number=slot.page_number, section_index=slot.section_index,
                                    section_title=slot.section_title, assets_dir=output_dir / 'assets'))
            elif framed is not None and not (slot.kind == 'toc' and summary_job_placed):
                summary_job_placed = summary_job_placed or slot.kind == 'toc'
                jobs.append(job_for(framed, page_number=slot.page_number, section_index=None, section_title=None))
            else:
                fixed.append({'kind': slot.kind, 'page_number': slot.page_number,
                              'deck_title': skeleton.deck_title, 'subtitle': skeleton.subtitle,
                              'sections': sections, 'section_index': slot.section_index,
                              'section_title': slot.section_title})
        for job in jobs:
            job.type_roles = type_roles
        judge = None
        if getattr(request, 'key_page_selection', False):
            from ppt_agent.v2.htmldeck import selection
            if selection.powerpoint_available():
                from ppt_agent import deck_jobs as _jobs
                judge = selection.powerpoint_judge(
                    _jobs.create_v2_review_client(),
                    {'deck_title': skeleton.deck_title, 'subtitle': skeleton.subtitle, 'language': brief.language,
                     'theme': theme.model_dump(mode='json')},
                    output_dir / 'assets', checkpoints.root / 'html' / 'selection')
            else:
                progress('[design] key-page selection skipped: Microsoft PowerPoint is not available to render the candidates')
        try:
            html_pages, html_summary = await design_deck(
                client, html_theme, jobs, fixed, checkpoints=checkpoints,
                concurrency=request.concurrency, look=getattr(request, 'html_review', True), progress=progress,
                extra_css=html_themes.studio_roles_css() if type_roles and html_theme == 'studio' else '', judge=judge)
        except html_browser.BrowserUnavailable as exc:
            progress(f'[design] no browser available, using the layout engine instead: {exc}')
            html_summary = {'theme': html_theme, 'unavailable': str(exc)}
    design_seconds = time.perf_counter() - started - content_seconds
    history, pages, qa_results, outcomes, records = [], [], [], [], []
    frame_history = []
    summary_placed = False
    seen_titles = set()
    final_contents = []
    structural_removed = []
    for slot in sorted(skeleton.slots, key=lambda item: item.page_number):
        if slot.kind == 'content':
            content, record = results[slot.page_number]
            reader_copy = display_copy(content, website_titles, document_names)
            record = {**record, 'source_display': reader_copy.source}
            layout_copy = reader_copy.to_points() if reader_copy.archetype == 'rich' else reader_copy
            try:
                page, notes = typeset_page(layout_copy, profile, page_number=slot.page_number,
                                          deck_title=skeleton.deck_title, history=history, frames=frame_history)
            except ValueError as exc:
                # A legal schema may still be too dense for a composition.
                # Keep the failure visible and degrade this page only, offline.
                record = {**record, 'fallback': True, 'layout_errors': [str(exc)]}
                content = fallback_content(slot)
                from ppt_agent.v2.evidence_check import check_content_numbers, sanitize_content_numbers
                invalid = check_content_numbers(content, '')
                content, stripped = sanitize_content_numbers(content, invalid)
                record.setdefault('numeric_check', {}).setdefault('removed', []).extend(stripped)
                page, notes = typeset_page(content, profile, page_number=slot.page_number,
                                          deck_title=skeleton.deck_title, history=history, frames=frame_history)
                checkpoints.save(f'typeset/final/content_{slot.page_number:03d}.json',
                                 {'content': content.model_dump(mode='json'), 'record': record})
            final_contents.append(content)
            page = page.model_copy(update={'section': slot.section_title})
            if notes:
                history.append(notes[0])
                frame_history.append(notes[1].removeprefix('frame:'))
            qa = content_qa(content, slot.page_number, seen_titles)
            status = 'fallback' if record['fallback'] else 'model'
            record = {**record, 'composition': notes[0] if notes else None, 'notes': notes,
                      'data_loss': any(note.startswith('dropped ') for note in notes)}
            records.append(record)
            outcome = PageOutcome(page_number=slot.page_number, status=status,
                model_attempts=record['attempts'], warning_issues=len(qa.issues),
                note='; '.join(notes))
        elif frame is not None and frame.get({'toc': 'summary', 'closing': 'roadmap'}.get(slot.kind)) and not (
                slot.kind == 'toc' and summary_placed):
            summary_placed = summary_placed or slot.kind == 'toc'  # a long deck has more than one TOC slot
            # The deck's opening and closing arguments take the TOC and thank-you slots.
            framed = frame[{'toc': 'summary', 'closing': 'roadmap'}[slot.kind]]
            page, notes = typeset_page(framed, profile, page_number=slot.page_number,
                                       deck_title=skeleton.deck_title)
            qa = PageQAResult(page_number=slot.page_number, issues=[])
            outcome = PageOutcome(page_number=slot.page_number, status='anchor', note='; '.join(notes))
        else:
            # Cover, TOC, dividers and closing come from the profile's own
            # structural designs, built only from skeleton facts.
            page = typeset_structural(
                slot.kind, profile, page_number=slot.page_number,
                deck_title=qualitative_structural_text(skeleton.deck_title, 'deck_title', slot.page_number, structural_removed),
                subtitle=qualitative_structural_text(skeleton.subtitle, 'subtitle', slot.page_number, structural_removed),
                sections=[(qualitative_structural_text(title, 'section_title', slot.page_number, structural_removed), number)
                          for title, number in sections], section_index=slot.section_index,
                section_title=qualitative_structural_text(slot.section_title, 'section_title', slot.page_number, structural_removed),
            )
            page, qa = review_page(page, theme)
            outcome = PageOutcome(page_number=slot.page_number, status='anchor',
                error_issues=len(qa.errors), warning_issues=len(qa.issues)-len(qa.errors))
        designed = html_pages.get(slot.page_number)
        if designed is not None and designed.ok:
            page = designed.page.model_copy(update={'section': slot.section_title})
            qa = PageQAResult(page_number=slot.page_number, issues=[])
            outcome = outcome.model_copy(update={
                'error_issues': 0, 'warning_issues': 0,
                'model_attempts': outcome.model_attempts + designed.attempts,
                'note': f'html design, {designed.attempts} call(s)'})
            if slot.kind == 'content':
                records[-1] = {**records[-1], 'html': {'attempts': designed.attempts,
                               'review_problems': designed.review_problems}}
        elif designed is not None and slot.kind == 'content':
            records[-1] = {**records[-1], 'html': {'fell_back': True, 'defects': designed.defects}}
        pages.append(page)
        qa_results.append(qa)
        outcomes.append(outcome)
        checkpoints.save(f'pages/page_{slot.page_number:03d}.json', {'page': page.model_dump(mode='json'),
                        'qa': qa.model_dump(mode='json'), 'outcome': outcome.model_dump(mode='json')})
        progress(f'[typeset] page {slot.page_number} done')
    shown_number = {page.page_number: index + 1 for index, page in enumerate(sorted(pages, key=lambda page: page.page_number))}
    if any(old != new for old, new in shown_number.items()):
        # A removed page leaves no gap in the numbers the slides show.
        from ppt_agent.v2.htmldeck.deck import renumber
        pages = [renumber(page, shown_number[page.page_number]) for page in pages]
    deck = DeckDesign(deck_title=skeleton.deck_title, subtitle=skeleton.subtitle,
                      language=brief.language, theme=theme, pages=pages)
    fallbacks = [item.page_number for item in outcomes if item.status == 'fallback']
    summary = summarize(qa_results, repaired=[], fallback=fallbacks)
    design_path = output_dir / f'{request.deck_name}_design.json'
    qa_path = output_dir / f'{request.deck_name}_qa_report.json'
    run_path = output_dir / f'{request.deck_name}_run_report.json'
    design_path.write_text(deck.model_dump_json(indent=2), encoding='utf-8')
    qa_path.write_text(summary.model_dump_json(indent=2), encoding='utf-8')
    passed = summary.pages_with_errors == 0
    pptx_path = None
    if not bars_from_zero:
        from ppt_agent.v2.ir import ChartItem
        deck = deck.model_copy(update={'pages': [page.model_copy(update={'elements': [
            element.model_copy(update={'zero_based': False}) if isinstance(element, ChartItem) else element
            for element in page.elements]}) for page in deck.pages]})
        design_path.write_text(deck.model_dump_json(indent=2), encoding='utf-8')
    if passed or request.qa_gate == 'lenient':
        pptx_path = render_deck(deck, output_dir / f'{request.deck_name}.pptx', assets_dir=output_dir / 'assets')
    seconds = {'content': content_seconds, 'html_design': design_seconds,
               'typeset_assemble_render': time.perf_counter()-started-content_seconds-design_seconds}
    statistics = diversity_statistics(final_contents, content_slots)
    statistics.update(
        source_empty_pages=sum(not content.source or not content.source.strip() for content in final_contents),
        source_invalid_pages=sum(
            not source_is_valid(content.source, *_page_references(record, references, page_counts))
            for content, record in zip(final_contents, records)),
        source_invalid_attempts=sum(record.get('source_invalid_attempts', 0) for record in records),
        source_invalid_cached_pages=sum(bool(record.get('source_invalid_cached')) for record in records),
        source_empty_attempts=sum(record.get('source_empty_attempts', 0) for record in records),
    )
    numeric_summary = {'blocked_attempts': sum(r.get('numeric_check', {}).get('blocked_attempts', 0) for r in records),
                       'passed_after_retry_pages': sum(bool(r.get('numeric_check', {}).get('passed_after_retry')) for r in records),
                       'removed_items_or_numbers': sum(len(r.get('numeric_check', {}).get('removed', [])) for r in records),
                       'removed_items': sum(len({'.'.join(x['path'].split('.')[:2]) for x in r.get('numeric_check', {}).get('removed', [])
                                                  if x.get('action') == 'removed_item'}) for r in records),
                       'removed_numbers': sum(x.get('action') == 'removed_number' for r in records
                                              for x in r.get('numeric_check', {}).get('removed', []))}
    numeric_summary['structural_removed_numbers'] = len(structural_removed)
    # Repetition a reader would notice: the same fact carrying several pages,
    # and the same arrangement coming round again too soon.
    from ppt_agent.v2.htmldeck import signature as layout_signature
    from ppt_agent.v2.htmldeck.ledger import FactLedger
    ledger = FactLedger()
    for slot, content in zip(content_slots, final_contents):
        ledger.register(slot.page_number, content.model_dump(mode='json', exclude_none=True))
    content_numbers = {shown_number.get(slot.page_number, slot.page_number) for slot in content_slots}  # as numbered now
    layouts = layout_signature.conflicts(
        [layout_signature.of(page) for page in pages if page.page_number in content_numbers])
    from ppt_agent.v2 import content_recovery as _recovery
    content_states = _recovery.summary({**{record.get('page_number', i): record for i, record in enumerate(records)},
                                        **{number: record for number, (_, record) in results.items()
                                           if record.get('content_status') == 'PAGE_REMOVED'}})
    content_states['shown_numbers'] = {str(old): new for old, new in shown_number.items() if old != new}
    # Stable identity: a slide keeps the id of the slot it was planned as, whatever is removed or moved
    # before it, so a finding names the right page in every file (round 6 named "6.02亿人" page 23; it is slide 24).
    slide_map = []
    for slot in sorted(original_slots or skeleton.slots, key=lambda item: item.page_number):
        record = results[slot.page_number][1] if slot.page_number in results else {}
        packet = packets.get(slot.page_number)
        slide_map.append({
            'slide_id': f'p{slot.page_number:03d}', 'kind': slot.kind, 'planned_page': slot.page_number,
            'final_page': shown_number.get(slot.page_number),
            'status': record.get('content_status') or ('CONTENT_VALID' if slot.kind == 'content' else slot.kind),
            'content_checkpoint': f'typeset/content_{slot.page_number:03d}.json' if slot.kind == 'content' else None,
            'design_checkpoint': f'html/page_{slot.page_number:03d}.json',
            'evidence_chunks': list(getattr(packet, 'chunk_ids', []) or []) if slot.kind == 'content' else [],
            'claim_audit': (record.get('claim_audit') or {}).get('verdict'),
            'source_conflicts': record.get('source_conflicts') or [],
            'content_version': record.get('checkpoint')})
    content_states['slides'] = slide_map
    claim_audit_summary = checkpoints.load('typeset/claim_audit.json')
    run = {'content_recovery': content_states, 'claim_audit': claim_audit_summary, 'page_budget': budget, 'data_series': series_summary, 'fact_ledger': ledger.audit(), 'layout_repetition': layouts, 'html_design': html_summary, 'structural_numeric_cleanup': structural_removed, 'numeric_check_statistics': numeric_summary, 'content_statistics': statistics, 'request': request.model_dump(mode='json'), 'layout_engine': 'typeset', 'profile': profile.name,
           'usage': client.usage.snapshot(), 'stage_seconds': seconds,
           'outcomes': [item.model_dump(mode='json') for item in outcomes], 'typeset_pages': records,
           'planning_events': skeleton.planning_events,
           'quality_gate': {'mode': request.qa_gate, 'passed': passed,
                            'pages_with_errors': summary.pages_with_errors, 'pptx_generated': pptx_path is not None}}
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
    status = ('quality_gate_failed' if request.qa_gate == 'strict' else 'completed_with_qa_errors') if not passed else (
        'succeeded_with_fallbacks' if fallbacks else 'succeeded')
    return BuildResult(status=status, pptx_path=str(pptx_path) if pptx_path else None,
        deck_design_path=str(design_path), qa_report_path=str(qa_path), run_report_path=str(run_path),
        page_count=len(pages), model_pages=len(results)-len(fallbacks), repaired_pages=0,
        fallback_pages=len(fallbacks), usage=client.usage.snapshot(), stage_seconds=seconds)
