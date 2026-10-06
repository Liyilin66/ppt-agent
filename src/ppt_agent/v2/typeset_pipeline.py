"""Content-only model calls followed by an ordered deterministic layout pass."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
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


def fallback_content(slot):
    """Reuse source planning text; do not invent quantitative placeholders."""
    page = slot.brief or PageBrief(title=slot.section_title or '内容要点')
    points = [point.strip() for point in page.points if point.strip()][:5]
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
                           revision_instruction=None, force_regenerate=False, current_content: dict[str, Any] | None = None):
    name = f'typeset/content_{slot.page_number:03d}.json'
    cached = None if force_regenerate else checkpoints.load(name)
    if cached is not None:
        parsed = content_adapter().validate_python(cached['content'])
        return parsed, cached['record']
    page_brief = slot.brief or PageBrief(title=slot.section_title or '内容要点')
    schema = content_adapter().json_schema()
    suggested = suggested_archetype(page_brief.layout_hint)
    system = (
        'Return one JSON content object matching the schema. Choose an archetype and write content only. '
        'Never output coordinates. Respect every field length and item-count limit. '
        'Use the requested slide language. insights and metrics must be ordered by importance, '
        'with the first item carrying the core conclusion. Fill source with a real supplied source '
        'or user-provided requirement attribution. When revising, edit current_content according to the instruction and preserve unrelated existing content. Never invent citations or unsupported numbers; '
        'if no evidence supports a number, omit it and use points or statement instead. '
        'Chart categories and values must have identical lengths. Schema:\n' + json.dumps(schema, ensure_ascii=False)
    )
    context = {'page_number': slot.page_number, 'page_brief': page_brief.model_dump(mode='json'),
               'section_title': slot.section_title, 'language': brief.language,
               'suggested_archetype': suggested, 'revision_instruction': revision_instruction,
               'current_content': current_content}
    user = json.dumps({'page': context, 'audience': brief.audience, 'purpose': brief.purpose,
                       'profile': profile.name, 'source_digest': brief.source_digest, 'current_content': current_content,
                       'key_points': brief.key_points}, ensure_ascii=False)
    errors = []
    attempts = 0
    parsed = None
    for _ in range(2):
        attempts += 1
        try:
            payload = await client.complete_json(task='page_content', system=system, user=user, context=context)
            parsed = content_adapter().validate_python(payload)
            if parsed.archetype == 'chart' and len(parsed.categories) != len(parsed.values):
                raise ValueError('categories and values must have identical lengths')
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
        parsed = fallback_content(slot)
    if not parsed.kicker and slot.section_title:
        parsed = parsed.model_copy(update={'kicker': slot.section_title[:24]})
    if page_brief.speaker_notes:
        parsed = parsed.model_copy(update={'speaker_notes': page_brief.speaker_notes})
    record = {'page_number': slot.page_number, 'attempts': attempts, 'fallback': fallback,
              'validation_errors': errors, 'archetype': parsed.archetype}
    checkpoints.save(name, {'content': parsed.model_dump(mode='json'), 'record': record})
    return parsed, record


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
    checkpoints.save('typeset_config.json', {'layout_engine': 'typeset', 'profile': profile.name})
    checkpoints.save('theme.json', theme.model_dump(mode='json'))
    semaphore = asyncio.Semaphore(request.concurrency)
    async def generate(slot):
        async with semaphore:
            result = await generate_content(client, checkpoints, slot, brief, profile)
            progress(f'[content] page {slot.page_number} ready')
            return slot.page_number, result
    # Complete all model calls before the first layout call; rhythm is serial.
    results = dict(await asyncio.gather(*(generate(slot) for slot in skeleton.content_slots())))
    content_seconds = time.perf_counter() - started
    sections = _skeleton_sections(skeleton)
    history, pages, qa_results, outcomes, records = [], [], [], [], []
    seen_titles = set()
    for slot in sorted(skeleton.slots, key=lambda item: item.page_number):
        if slot.kind == 'content':
            content, record = results[slot.page_number]
            try:
                page, notes = typeset_page(content, profile, page_number=slot.page_number,
                                          deck_title=skeleton.deck_title, history=history)
            except ValueError as exc:
                # A legal schema may still be too dense for a composition.
                # Keep the failure visible and degrade this page only, offline.
                record = {**record, 'fallback': True, 'layout_errors': [str(exc)]}
                content = fallback_content(slot)
                page, notes = typeset_page(content, profile, page_number=slot.page_number,
                                          deck_title=skeleton.deck_title, history=history)
                checkpoints.save(f'typeset/content_{slot.page_number:03d}.json',
                                 {'content': content.model_dump(mode='json'), 'record': record})
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
                deck_title=skeleton.deck_title, subtitle=skeleton.subtitle,
                sections=sections, section_index=slot.section_index,
                section_title=slot.section_title,
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
    run = {'request': request.model_dump(mode='json'), 'layout_engine': 'typeset', 'profile': profile.name,
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
