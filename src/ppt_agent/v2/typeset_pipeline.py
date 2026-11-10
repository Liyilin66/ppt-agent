"""Content-only model calls followed by an ordered deterministic layout pass."""
from __future__ import annotations

import asyncio
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


async def generate_content(client, checkpoints, slot, brief, profile, *,
                           revision_instruction=None, force_regenerate=False, current_content: dict[str, Any] | None = None,
                           diversity: dict[str, Any] | None = None, source_references: list[str] | None = None, source_page_counts: dict[str, int] | None = None, evidence=None):
    source_references = source_references or re.findall(r'https?://[^\s]+', brief.source_digest or '')
    name = f'typeset/content_{slot.page_number:03d}.json'
    cached = None if force_regenerate else checkpoints.load(name)
    if cached is not None:
        parsed = content_adapter().validate_python(cached['content'])
        return parsed, cached['record']
    page_brief = slot.brief or PageBrief(title=slot.section_title or '内容要点')
    schema = content_adapter().json_schema()
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
    context = {'page_number': slot.page_number, 'page_brief': page_brief.model_dump(mode='json'),
               'section_title': slot.section_title, 'language': brief.language,
               'suggested_archetype': suggested, 'revision_instruction': revision_instruction,
               'current_content': current_content, 'diversity': diversity,
               'evidence': evidence.to_dict() if evidence is not None else None}
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
    numeric_candidate = None
    trims = []
    source_trims = []
    parsed = None
    for _ in range(2):
        attempts += 1
        try:
            payload = await client.complete_json(task='page_content', system=system, user=user, context=context)
            payload, trimmed = trim_overlong_lists(payload)
            if trimmed:
                trims.extend(trimmed)
            parsed = content_adapter().validate_python(payload)
            empty_sources += int(not parsed.source or not parsed.source.strip())
            trimmed_source, dropped_pages = trim_source(parsed.source, source_references, source_page_counts,
                                                        evidence.allowed_pages if evidence is not None else None)
            if dropped_pages:
                parsed = parsed.model_copy(update={'source': trimmed_source})
                source_trims.append({'attempt': attempts, 'dropped': dropped_pages})
            if not source_is_valid(parsed.source, source_references, source_page_counts,
                                   evidence.allowed_pages if evidence is not None else None):
                invalid_sources += 1
                raise ValueError('source must be a supplied filename, page or URL; no source material means source=null')
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
    record = {'page_number': slot.page_number, 'attempts': attempts, 'fallback': fallback,
              'validation_errors': errors, 'archetype': parsed.archetype,
              'source_invalid_attempts': invalid_sources, 'source_empty_attempts': empty_sources, 'source_trimmed': source_trims,
              'trimmed_items': trims,
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
    checkpoints.save('typeset_config.json', {'layout_engine': 'typeset', 'profile': profile.name,
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
    plan = plan_archetypes(content_slots, {
        number: has_numeric_evidence(packet.text if packet is not None else None)
        for number, packet in packets.items()
    }, {
        number: has_dated_evidence(packet.text if packet is not None else None)
        for number, packet in packets.items()
    })
    checkpoints.save('typeset/archetype_plan.json', {str(k): v for k, v in plan.items()})
    semaphore = asyncio.Semaphore(max(1, request.concurrency))

    async def produce(slot):
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
            content, record = await generate_content(client, checkpoints, slot, brief, profile,
                                                    diversity=diversity, source_references=references,
                                                    source_page_counts=page_counts, evidence=packet)
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
        checkpoints.save(f'typeset/content_{slot.page_number:03d}.json',
                         {'content': content.model_dump(mode='json'), 'record': record})
        progress(f'[content] page {slot.page_number} ready')
        return slot.page_number, (content, record)

    results = dict(await asyncio.gather(*(produce(slot) for slot in content_slots)))
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
                                    section_title=slot.section_title))
            elif framed is not None and not (slot.kind == 'toc' and summary_job_placed):
                summary_job_placed = summary_job_placed or slot.kind == 'toc'
                jobs.append(job_for(framed, page_number=slot.page_number, section_index=None, section_title=None))
            else:
                fixed.append({'kind': slot.kind, 'page_number': slot.page_number,
                              'deck_title': skeleton.deck_title, 'subtitle': skeleton.subtitle,
                              'sections': sections, 'section_index': slot.section_index,
                              'section_title': slot.section_title})
        try:
            html_pages, html_summary = await design_deck(
                client, html_theme, jobs, fixed, checkpoints=checkpoints,
                concurrency=request.concurrency, look=getattr(request, 'html_review', True), progress=progress)
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
            try:
                page, notes = typeset_page(reader_copy, profile, page_number=slot.page_number,
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
                checkpoints.save(f'typeset/content_{slot.page_number:03d}.json',
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
    run = {'html_design': html_summary, 'structural_numeric_cleanup': structural_removed, 'numeric_check_statistics': numeric_summary, 'content_statistics': statistics, 'request': request.model_dump(mode='json'), 'layout_engine': 'typeset', 'profile': profile.name,
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
