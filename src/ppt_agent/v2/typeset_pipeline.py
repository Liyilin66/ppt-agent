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


def display_source(source, website_titles):
    if not source:
        return None
    result = []
    for clause in re.split(r'[;；]', source):
        clause = clause.strip()
        if not clause:
            continue
        if clause.startswith(('https://', 'http://')):
            result.append(urlparse(clause).netloc + ' · ' + (website_titles.get(clause) or '网页资料'))
        else:
            clause = re.sub(r'第\s*([^页]+)\s*页', lambda m: '第 ' + m[1].strip() + ' 页', clause)
            result.append(clause)
    return '；'.join(result) or None


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
        items=[{'heading': '核心要点' if index == 0 else '补充说明', 'body': point[:80]}
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
        'points defaults to 3 items, at most 4; keep item body preferably within 40 characters. '
        'Prefer metrics or chart for supported quantitative evidence, and statement for key conclusions. '
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
            if parsed.archetype == 'points' and len(parsed.items) > 4:
                raise ValueError('points must have at most 4 items, normally 3')
            if diversity.get('allowed_archetypes') and parsed.archetype not in diversity['allowed_archetypes']:
                raise ValueError('archetype violates deck diversity; choose from ' + str(diversity['allowed_archetypes']))
            if parsed.archetype == 'chart' and len(parsed.categories) != len(parsed.values):
                raise ValueError('categories and values must have identical lengths')
            from ppt_agent.v2.evidence_check import check_content_numbers, number_contexts
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

ARCHETYPE_CAPS = {"points": 0.4, "process": 0.3, "statement": 0.15}
_GENERIC = ["points", "process", "compare", "statement"]
_NUMERIC = ["metrics", "chart"]


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
    metrics/chart only where the page's evidence carries numbers; timeline only
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
        candidates = [preferred] + (_NUMERIC if has_numbers else []) + _GENERIC
        has_dates = (dated or {}).get(slot.page_number, False)
        if hint == "timeline" and has_dates:
            candidates.insert(1, "timeline")
        if preferred == "timeline" and not has_dates:
            candidates[0] = "process"  # stages without dates read as a process
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

        choice = None
        for relax in (frozenset(), frozenset({"process"}), frozenset({"process", "points"})):
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
    checkpoints.save('typeset_config.json', {'layout_engine': 'typeset', 'profile': profile.name,
                                           'source_references': references, 'source_page_counts': page_counts})
    from ppt_agent.v2.evidence import EvidenceStore
    evidence_path = checkpoints.root / 'evidence_store.json'
    evidence_store = EvidenceStore.from_dict(json.loads(evidence_path.read_text())) if evidence_path.is_file() else None
    content_slots = sorted(skeleton.content_slots(), key=lambda slot: slot.page_number)

    def packet_for(slot):
        if evidence_store is None:
            return None
        section = skeleton.outline.sections[(slot.section_index or 1) - 1]
        query = " ".join([slot.brief.title, *slot.brief.points, slot.section_title or '']) if slot.brief else (slot.section_title or '')
        section_query = " ".join([section.title, section.goal, *section.talking_points])
        return evidence_store.select(query, section_query=section_query)

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
                     'allowed_archetypes': (list(dict.fromkeys([assigned, 'points', 'statement']))
                                            if assigned in ('metrics', 'chart', 'timeline') else [assigned]),
                     'deck_plan': [plan[s.page_number] for s in content_slots],
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
            citation = packet_citation(packet)
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
    content_seconds = time.perf_counter() - started
    sections = _skeleton_sections(skeleton)
    website_titles = {doc['name']: doc.get('title', '') for doc in evidence_store.documents
                      if doc.get('page_kind') == 'web'} if evidence_store is not None else {}
    history, pages, qa_results, outcomes, records = [], [], [], [], []
    seen_titles = set()
    final_contents = []
    structural_removed = []
    for slot in sorted(skeleton.slots, key=lambda item: item.page_number):
        if slot.kind == 'content':
            content, record = results[slot.page_number]
            display_copy = content.model_copy(update={'source': display_source(content.source, website_titles)})
            record = {**record, 'source_display': display_copy.source}
            try:
                page, notes = typeset_page(display_copy, profile, page_number=slot.page_number,
                                          deck_title=skeleton.deck_title, history=history)
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
                                          deck_title=skeleton.deck_title, history=history)
                checkpoints.save(f'typeset/content_{slot.page_number:03d}.json',
                                 {'content': content.model_dump(mode='json'), 'record': record})
            final_contents.append(content)
            page = page.model_copy(update={'section': slot.section_title})
            if notes:
                history.append(notes[0])
            qa = content_qa(content, slot.page_number, seen_titles)
            status = 'fallback' if record['fallback'] else 'model'
            record = {**record, 'composition': notes[0] if notes else None, 'notes': notes,
                      'data_loss': any(note.startswith('dropped ') for note in notes)}
            records.append(record)
            outcome = PageOutcome(page_number=slot.page_number, status=status,
                model_attempts=record['attempts'], warning_issues=len(qa.issues),
                note='; '.join(notes))
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
    seconds = {'content': content_seconds, 'typeset_assemble_render': time.perf_counter()-started-content_seconds}
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
    run = {'structural_numeric_cleanup': structural_removed, 'numeric_check_statistics': numeric_summary, 'content_statistics': statistics, 'request': request.model_dump(mode='json'), 'layout_engine': 'typeset', 'profile': profile.name,
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
