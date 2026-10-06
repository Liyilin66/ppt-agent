"""Audited evidence acceptance runner. No retrieval overrides; HTTP is explicit.

Run --preflight-only, then --run T3 or --run Large. Existing completed tasks
are rejected; --resume is required to use an unfinished task's checkpoints.
The $2 official-price budget is persisted across all invocations, including
unknown-cost failed calls. Numeric/source statistics are syntactic signals,
not a semantic truth or citation-support audit.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from long_input_experiment import OfficialBudget, save
from long_input_data import read_documents
from typeset_evaluation import AuditedClient, MODEL, ROOT
from ppt_agent.runtime import load_dotenv_file, sanitize_error_message
from ppt_agent.v2.orchestrator import BuildRequest, build_deck_async
from ppt_agent.v2.providers import provider_config_from_env
from ppt_agent.v2.typeset_pipeline import source_is_valid

OUT = ROOT / 'data/evaluation/evidence-retrieval'
CHAR_LIMIT = 40_000
TOKEN_LIMIT = 24_000


class PersistentBudget(OfficialBudget):
    """Write-ahead reservations prevent crashes from resetting paid exposure."""
    def __init__(self, path, limit=2.):
        super().__init__(limit)
        self.path = Path(path)
        if self.path.exists():
            prior = json.loads(self.path.read_text())
            if prior['limit_usd'] != limit:
                raise RuntimeError('Frozen official-price budget limit changed.')
            # In-flight reservations from a crashed process remain conservatively
            # booked. No assumption that missing usage means the call was free.
            self.spent = prior['booked_usd']
    def persist(self):
        save(self.path, {'limit_usd': self.limit, 'settled_usd': self.spent,
             'open_reservations': self.reservations,
             'booked_usd': self.spent + sum(self.reservations.values()),
             'pricing_basis': 'official API token prices; no CCCX multiplier'})
    def reserve(self, *args):
        value = super().reserve(*args)
        self.persist()
        return value
    def settle(self, *args):
        super().settle(*args)
        self.persist()


class AcceptanceClient(AuditedClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        previous = self.output / 'calls.json'
        if previous.exists():
            self.records = json.loads(previous.read_text())
    async def complete_json(self, *, task, system, user, context=None, **kwargs):
        chars = len(system) + len(user)
        tokens = len(self.encoder.encode(system + '\n' + user))
        if chars > CHAR_LIMIT or tokens > TOKEN_LIMIT:
            raise RuntimeError(f'Request bound exceeded before HTTP: {chars} chars, {tokens} tokens.')
        try:
            return await super().complete_json(task=task, system=system, user=user, context=context, **kwargs)
        finally:
            # Requests and evidence are recorded exactly as production supplied
            # them. No experiment-side source replacement or synthetic digest.
            if self.records:
                record = self.records[-1]
                record.update(input_characters=chars, estimated_input_tokens=tokens,
                              input_character_limit=CHAR_LIMIT, input_token_limit=TOKEN_LIMIT)
                save(self.output / 'calls.json', self.records)
    def _parse_response(self, payload):
        save(self.output / 'calls' / f'{len(self.records):02d}-raw-response.json', payload)
        return super()._parse_response(payload)


def frozen_input(name):
    manifest = json.loads((ROOT / 'data/evaluation/long-input/runs/run1/manifest.json').read_text())
    sources = manifest['small'] if name == 'T3' else manifest['large']
    docs = read_documents([Path(item['path']) for item in sources])
    for expected, observed in zip(sources, docs):
        if expected['sha256'] != observed['sha256']:
            raise RuntimeError(f"Source SHA changed: {observed['path']}")
    return manifest['prompt'], docs


def run_policy(folder, resume):
    if (folder / 'result.json').exists():
        raise RuntimeError('Completed paid run is immutable; refuse rerun.')
    if folder.exists() and not resume:
        raise RuntimeError('Unfinished paid run exists; explicit --resume required.')
    if resume and not (folder / 'manifest.json').exists():
        raise RuntimeError('No frozen manifest to resume.')


def page_statistics(folder, source_paths):
    """Counts body/chart numerals, excluding kicker/source/notes/page numbers."""
    references = [Path(path).name for path in source_paths]
    rows = []
    for path in sorted((folder / 'checkpoints/typeset').glob('content_*.json')):
        payload = json.loads(path.read_text())
        content, record = payload['content'], payload['record']
        body = {key: value for key, value in content.items() if key not in
                {'source', 'speaker_notes', 'kicker', 'archetype'}}
        numeric = bool(re.search(r'\d', json.dumps(body, ensure_ascii=False)))
        source = content.get('source')
        valid = bool(source and str(source).strip()) and source_is_valid(source, references)
        evidence = record.get('evidence_selection', record.get('evidence'))
        allowed = evidence.get('allowed_pages') if isinstance(evidence, dict) else None
        selected_valid = (bool(source and str(source).strip()) and
                          source_is_valid(source, list(allowed),
                                          page_counts={name: max(pages, default=0) for name, pages in allowed.items()},
                                          allowed_pages=allowed)) if allowed is not None else None
        rows.append({'page': record['page_number'], 'archetype': content['archetype'],
                     'numeric_content': numeric, 'source': source,
                     'nonempty_source_format_valid': valid,
                     'nonempty_source_selection_valid': selected_valid,
                     'evidence_selection': evidence,
                     'record': record, 'content_checkpoint': str(path)})
    numeric = [row for row in rows if row['numeric_content']]
    counts = Counter(row['archetype'] for row in rows)
    return {'definition': 'Arabic numeral in title/body/chart content, excluding kicker/source/notes; source validity is syntactic, not semantic support.',
            'pages': rows, 'content_pages': len(rows), 'archetype_counts': dict(counts),
            'archetype_ratios': {key: value / len(rows) for key, value in counts.items()} if rows else {},
            'numeric_pages': len(numeric),
            'numeric_pages_nonempty_source_format_valid': sum(row['nonempty_source_format_valid'] for row in numeric),
            'numeric_source_format_rate': sum(row['nonempty_source_format_valid'] for row in numeric) / len(numeric) if numeric else None,
            'numeric_pages_nonempty_source_selection_valid': sum(row['nonempty_source_selection_valid'] is True for row in numeric),
            'numeric_source_selection_rate': sum(row['nonempty_source_selection_valid'] is True for row in numeric) / len(numeric) if numeric else None,
            'semantic_fabrication_audit': 'pending manual claim-by-claim audit',
            'semantic_citation_support_audit': 'pending manual claim-by-claim audit'}


def billing_summary(out, budget):
    calls = []
    for path in out.glob('*/calls.json'):
        calls.extend(json.loads(path.read_text()))
    unique = {}
    for index, call in enumerate(calls):
        unique[call.get('request_id', f'legacy-{index}')] = call
    calls = list(unique.values())
    known = sum(call.get('official_cost_usd') or 0. for call in calls)
    value = {'known_official_usd': known, 'unknown_reserve_usd': max(0., budget.spent - known),
             'booked_usd': budget.spent, 'limit_usd': budget.limit,
             'actual_models': sorted({c['response_model'] for c in calls if c.get('response_model')}),
             'input_tokens': sum((c.get('usage') or {}).get('prompt_tokens', 0) for c in calls),
             'output_tokens': sum((c.get('usage') or {}).get('completion_tokens', 0) for c in calls),
             'pricing_basis': 'official token usage only; no CCCX 3x multiplier'}
    save(out / 'billing.json', value)
    return value


async def preflight(config, docs, budget, out):
    folder = out / 'preflight'
    if (folder / 'verified.json').exists():
        return
    if folder.exists():
        raise RuntimeError('Preflight already attempted; inspect receipts rather than retry automatically.')
    folder.mkdir(parents=True)
    client = AcceptanceClient(config.model_copy(update={'max_output_tokens': 32}),
                              'A', docs, budget, folder, {'model': MODEL})
    started = time.perf_counter()
    try:
        await client.complete_json(task='preflight', system='Return JSON only.',
                                   user='Return {"ok":true}.', context={}, max_output_tokens=32)
        save(folder / 'verified.json', {'actual_model': client.records[-1]['response_model'],
             'raw_response': 'calls/01-raw-response.json', 'usage': client.records[-1]['usage'],
             'base_url': config.base_url, 'official_input_per_million_usd': 2.,
             'official_cached_input_per_million_usd': .2, 'official_output_per_million_usd': 12.,
             'elapsed_seconds': time.perf_counter() - started})
    finally:
        await client.aclose()
        billing_summary(out, budget)


async def run(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    load_dotenv_file(str(ROOT / '.env'))
    config = provider_config_from_env().model_copy(update={'model': MODEL, 'max_output_tokens': 4096,
        'max_retries': 0, 'timeout_seconds': 300, 'input_cost_per_mtok_usd': 2., 'output_cost_per_mtok_usd': 12.})
    prompt, docs = frozen_input(args.run or 'T3')
    budget = PersistentBudget(out / 'budget.json')
    if args.preflight_only:
        await preflight(config, docs, budget, out)
        return
    if not args.run:
        raise RuntimeError('Choose --run T3 or --run Large; no implicit paid tasks.')
    if not (out / 'preflight/verified.json').exists():
        raise RuntimeError('Run --preflight-only first to verify actual model and usage.')
    folder = out / args.run
    run_policy(folder, args.resume)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    request = BuildRequest(prompt=prompt, page_count=20, style_profile='consulting',
                           source_paths=[doc['path'] for doc in docs], output_dir=str(folder),
                           deck_name=args.run, concurrency=1, budget_usd=2., resume=args.resume)
    frozen = {'commit': commit, 'model': MODEL, 'prompt': prompt,
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
              'sources': [{key: value for key, value in doc.items() if key != 'pages'} for doc in docs],
              'request': request.model_dump(mode='json'), 'input_character_limit': CHAR_LIMIT,
              'input_token_limit': TOKEN_LIMIT, 'output_token_limit': 4096,
              'reasoning_effort': 'none', 'http_retries': 0}
    if args.resume:
        previous = json.loads((folder / 'manifest.json').read_text())
        for key in ('commit', 'model', 'prompt_sha256', 'sources'):
            if previous[key] != frozen[key]:
                raise RuntimeError(f'Frozen {key} changed; cannot resume.')
    else:
        save(folder / 'manifest.json', frozen)
    client = AcceptanceClient(config, 'A', docs, budget, folder, {'model': MODEL})
    started = time.perf_counter()
    meta = {'name': args.run, 'resume': args.resume}
    try:
        result = await build_deck_async(request, client, progress=lambda value: print(f'[{args.run}] {value}', flush=True))
        save(folder / 'result.json', result.model_dump(mode='json'))
        save(folder / 'page-statistics.json', page_statistics(folder, request.source_paths))
        meta['status'] = result.status
    except Exception as exc:
        meta.update(status='failed', error=sanitize_error_message(str(exc)).replace(config.resolved_api_key(), '[redacted]'))
        raise
    finally:
        meta.update(elapsed_seconds=time.perf_counter() - started,
                    actual_models=sorted({record['response_model'] for record in client.records if record.get('response_model')}),
                    known_official_usd=sum(record.get('official_cost_usd') or 0. for record in client.records),
                    input_tokens=sum((record.get('usage') or {}).get('prompt_tokens', 0) for record in client.records),
                    output_tokens=sum((record.get('usage') or {}).get('completion_tokens', 0) for record in client.records),
                    max_request_input_characters=max((record.get('input_characters', 0) for record in client.records), default=0),
                    max_request_input_tokens=max((record.get('estimated_input_tokens', 0) for record in client.records), default=0))
        save(folder / 'run.json', meta)
        await client.aclose()
        billing_summary(out, budget)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--run', choices=['T3', 'Large'])
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--output', default=str(OUT))
    asyncio.run(run(parser.parse_args()))
