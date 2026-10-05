"""Planning-only experiment. Production source is imported, never patched."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

import tiktoken

from long_input_data import BM25Index, chunks, cnnic_chapters, full_text, read_documents, select_chapter
from ppt_agent.runtime import load_dotenv_file, sanitize_error_message
from ppt_agent.v2 import prompts
from ppt_agent.v2.orchestrator import BuildRequest, plan_deck_async
from ppt_agent.v2.planning import ContentBrief
from ppt_agent.v2.providers import OpenAICompatClient, UsageMeter, extract_json_payload, provider_config_from_env


ROOT = Path('/Users/jay/Documents/ppt-agent')
DEFAULT_OUT = ROOT / 'data/evaluation/long-input/runs/run1'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def official_cost(usage):
    if 'prompt_tokens' not in usage or 'completion_tokens' not in usage:
        raise ValueError('Missing token usage; cannot book zero cost.')
    input_tokens = int(usage['prompt_tokens'])
    output_tokens = int(usage['completion_tokens'])
    details = usage.get('prompt_tokens_details') or {}
    cached = int(details.get('cached_tokens') or 0)
    writes = int(details.get('cache_creation_tokens') or 0)
    if min(input_tokens, output_tokens, cached, writes) < 0 or cached + writes > input_tokens:
        raise ValueError('Invalid token accounting.')
    return ((input_tokens - cached - writes) * 2 + cached * 0.20
            + writes * 2.50 + output_tokens * 12) / 1_000_000


class OfficialBudget:
    def __init__(self, limit):
        self.limit = limit
        self.spent = 0.0
        self.reservations = {}

    def reserve(self, request_id, input_tokens, output_cap):
        # Allow tokenizer/message overhead and possible cache-write surcharge.
        amount = ((input_tokens * 1.10 + 100) * 2.50 + output_cap * 12) / 1_000_000
        if self.spent + sum(self.reservations.values()) + amount > self.limit + 1e-12:
            raise RuntimeError('Official-price budget would be exceeded before HTTP.')
        self.reservations[request_id] = amount
        return amount

    def settle(self, request_id, actual):
        reserved = self.reservations.pop(request_id)
        self.spent += reserved if actual is None else actual
        if self.spent > self.limit + 1e-12:
            raise RuntimeError('Observed official-price budget exceeded; stop all calls.')


class RoutedClient(OpenAICompatClient):
    def __init__(self, config, arm, docs, budget, output, model_identity, streaming=False, full_prefix=True):
        super().__init__(config, usage=UsageMeter())
        self.arm, self.docs, self.budget, self.output = arm, docs, budget, output
        self.model_identity = model_identity
        self.full_source = full_text(docs)
        self.chapters = cnnic_chapters(docs[0])
        self.blocks = chunks(docs)
        self.index = BM25Index([x['text'] for x in self.blocks])
        self.encoder = tiktoken.get_encoding('o200k_base')
        self.records = []
        self.pending_payload = None
        self.halted = False
        self.streaming = streaming
        self.full_prefix = full_prefix

    def _build_request(self, system, user, max_output_tokens, *, images=None):
        url, headers, body = super()._build_request(system, user, max_output_tokens, images=images)
        body['reasoning_effort'] = 'none'
        return url, headers, body

    def _parse_response(self, payload):
        self.pending_payload = payload
        observed = payload.get('model')
        if observed != self.config.model:
            self.halted = True
            raise RuntimeError(f'Returned model mismatch: {observed!r}; stop.')
        if self.model_identity.get('model') not in (None, observed):
            self.halted = True
            raise RuntimeError('Model changed between experiment calls; stop.')
        self.model_identity['model'] = observed
        official_cost(payload.get('usage') or {})
        return super()._parse_response(payload)

    async def _stream_json(self, system, user, output_cap):
        url, headers, body = self._build_request(system, user, output_cap)
        body.update(stream=True, stream_options={'include_usage': True})
        content, model, response_id, usage = [], None, None, None
        http = await self._http()
        async with http.stream('POST', url, headers=headers, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                frame = json.loads(data)
                if frame.get('error'):
                    raise RuntimeError('Provider reported an error inside the stream.')
                if frame.get('model'):
                    observed = frame['model']
                    if observed != self.config.model or model not in (None, observed):
                        self.halted = True
                        raise RuntimeError('Returned model mismatch inside stream; stop.')
                    model = observed
                response_id = frame.get('id') or response_id
                if frame.get('usage'):
                    usage = frame['usage']
                for choice in frame.get('choices', []):
                    text = (choice.get('delta') or {}).get('content')
                    if text:
                        content.append(text)
        payload = {'model': model, 'id': response_id, 'usage': usage,
                   'choices': [{'message': {'content': ''.join(content)}}]}
        text, input_tokens, output_tokens = self._parse_response(payload)
        await self.usage.record(input_tokens=input_tokens, output_tokens=output_tokens,
                                config=self.config)
        return extract_json_payload(text)

    def source_for(self, section):
        query = ' '.join([section['title'], section.get('goal', ''),
                          *section.get('talking_points', [])])
        if self.arm == 'B':
            return self.full_source, {'strategy': 'full', 'characters': len(self.full_source)}
        if self.arm == 'C':
            chapter, score = select_chapter(query, self.chapters)
            if chapter is None:
                return '(no source chapter matched)', {'strategy': 'chapter', 'query': query, 'no_match': True}
            text = full_text([{'doc_id': chapter['doc_id'], 'pages': chapter['pages']}])
            return text, {'strategy': 'chapter', 'query': query, 'chapter_id': chapter['chapter_id'],
                          'title': chapter['title'], 'pdf_pages': [chapter['pdf_page_start'], chapter['pdf_page_end']],
                          'score': score, 'characters': len(text)}
        ranked = self.index.search(query, top_k=4)
        selected = [dict(self.blocks[i], score=score, block_id=i) for i, score in ranked]
        text = '\n\n'.join(
            f"[[{x['doc_id']} PDF p{x['pdf_page_start']}-{x['pdf_page_end']}]]\n{x['text']}"
            for x in selected
        ) or '(no relevant source blocks matched)'
        return text, {'strategy': 'bm25', 'query': query, 'chunks': selected, 'characters': len(text)}

    async def complete_json(self, *, task, system, user, context=None, **kwargs):
        if self.halted:
            raise RuntimeError('Client stopped after an invalid response.')
        source_info = {'strategy': 'production_unmodified'}
        if self.arm != 'A' and self.full_prefix and task == 'brief':
            user = (f"User request:\n{context['user_prompt'].strip()}\n\n"
                    f"Planned deck length: {context['page_count']} slides.\n\n"
                    f'Source document digest:\n{self.full_source}')
            source_info = {'strategy': 'full_prefix', 'characters': len(self.full_source)}
        elif self.arm != 'A' and self.full_prefix and task == 'outline':
            brief = ContentBrief.model_validate(context['brief']).model_copy(update={'source_digest': self.full_source})
            user = prompts.build_outline_user_prompt(brief, content_budget=context['content_budget'])
            source_info = {'strategy': 'full_prefix', 'characters': len(self.full_source)}
        elif self.arm != 'A' and task == 'section_pages':
            source, source_info = self.source_for(context['section'])
            start = user.index('Source digest: ')
            end = user.index('\n\nSection: ', start)
            user = user[:start] + 'Source digest: ' + source + user[end:]
        number = len(self.records) + 1
        request_id = f'{self.output.name}-{number}'
        tokens = len(self.encoder.encode(system + '\n' + user))
        output_cap = kwargs.get('max_output_tokens') or self.config.max_output_tokens
        self.budget.reserve(request_id, tokens, output_cap)
        packet = {'task': task, 'system': system, 'user': user,
                  'source_selection': source_info, 'estimated_input_tokens': tokens,
                  'reasoning_effort': 'none', 'max_output_tokens': output_cap,
                  'transport': 'streaming' if self.streaming else 'nonstream'}
        save(self.output / 'calls' / f'{number:02d}-request.json', packet)
        record = {'request_id': request_id, 'task': task, 'source_selection': source_info,
                  'packet': f'calls/{number:02d}-request.json'}
        self.records.append(record)
        self.pending_payload = None
        start_time = time.perf_counter()
        actual_cost = None
        try:
            if self.streaming:
                result = await self._stream_json(system, user, output_cap)
            else:
                result = await super().complete_json(task=task, system=system, user=user,
                                                     context=context, **kwargs)
            record['status'] = 'succeeded'
            save(self.output / 'calls' / f'{number:02d}-response.json', {'parsed_json': result})
            return result
        except Exception as exc:
            self.halted = True
            record['status'] = 'failed'
            record['error'] = sanitize_error_message(str(exc)).replace(self.config.resolved_api_key(), '[redacted]')
            raise
        finally:
            if self.pending_payload is not None:
                record['response_model'] = self.pending_payload.get('model')
                record['response_id'] = self.pending_payload.get('id')
                record['usage'] = self.pending_payload.get('usage')
                try:
                    actual_cost = official_cost(record['usage'] or {})
                except ValueError:
                    self.halted = True
            record['official_cost_usd'] = actual_cost
            record['seconds'] = round(time.perf_counter() - start_time, 3)
            self.budget.settle(request_id, actual_cost)
            record['global_budget_booked_usd'] = self.budget.spent
            save(self.output / 'calls.json', self.records)


async def run_arm(name, strategy, docs, config, budget, identity, out, prompt, prefix=None,
                  streaming=False, production_prefix=False):
    folder = out / name
    if folder.exists():
        raise RuntimeError(f'{name} already exists; do not overwrite or rerun.')
    folder.mkdir(parents=True)
    if prefix:
        target = folder / 'checkpoints'
        target.mkdir()
        for file in ('brief.json', 'skeleton.json'):
            source = prefix / 'checkpoints' / file
            if source.exists():
                shutil.copy2(source, target / file)
    client = RoutedClient(config, strategy, docs, budget, folder, identity,
                          streaming=streaming, full_prefix=not production_prefix)
    request = BuildRequest(prompt=prompt, page_count=20, language='zh-CN',
                           source_paths=[x['path'] for x in docs], enable_search=False,
                           output_dir=str(folder), resume=bool(prefix), concurrency=1,
                           budget_usd=budget.limit)
    started = time.perf_counter()
    meta = {'arm': name, 'strategy': strategy, 'shared_prefix': prefix.name if prefix else None,
            'model_requested': config.model, 'request': request.model_dump(mode='json'),
            'transport': 'streaming' if streaming else 'nonstream',
            'prefix_policy': 'production_limits' if production_prefix else 'full/shared'}
    try:
        result = await plan_deck_async(request, client, progress=lambda x: print(f'[{name}] {x}', flush=True))
        save(folder / 'plan.json', result.model_dump(mode='json'))
        save(folder / 'scripts.json', [{'page': slot.page_number, **slot.brief.model_dump(mode='json')}
                                      for slot in result.skeleton.slots if slot.brief is not None])
        meta['status'] = 'succeeded'
        meta['content_script_pages'] = sum(x.brief is not None for x in result.skeleton.slots)
    except Exception as exc:
        meta['status'] = 'failed'
        meta['error'] = sanitize_error_message(str(exc)).replace(config.resolved_api_key(), '[redacted]')
        raise
    finally:
        meta['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        meta['usage'] = client.usage.snapshot()
        meta['official_direct_cost_usd'] = sum(x['official_cost_usd'] or 0 for x in client.records)
        meta['global_official_budget_booked_usd'] = budget.spent
        meta['responses'] = sorted({x.get('response_model') for x in client.records if x.get('response_model')})
        save(folder / 'run.json', meta)
        await client.aclose()
        print(json.dumps(meta, ensure_ascii=False), flush=True)


async def experiment(args):
    if args.supplement_d_large:
        return await supplement_d_large(args)
    if args.recover_streaming:
        return await recover_streaming(args)
    out = Path(args.output)
    if (out / 'manifest.json').exists():
        raise RuntimeError('Frozen experiment already exists; refuse duplicate paid runs.')
    small = read_documents([ROOT / 'eval/sources/cnnic_genai_2025.pdf'])
    expanded = read_documents([Path(x['path']) for x in json.loads(
        (ROOT / 'data/evaluation/long-input/sources/selected_source.json').read_text())['sources']])
    for i, doc in enumerate(expanded, start=2):
        doc['doc_id'] = f'doc{i}'
    large = small + expanded
    encoder = tiktoken.get_encoding('o200k_base')
    text = (ROOT / 'eval/tasks/T3_long_report.md').read_text()
    prompt = re.search(r'```\s*\n(.*?)\n```', text, re.S).group(1)
    manifest = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'small': [{k: v for k, v in x.items() if k != 'pages'} for x in small],
                'large': [{k: v for k, v in x.items() if k != 'pages'} for x in large],
                'small_context_tokens': len(encoder.encode(full_text(small))),
                'large_context_tokens': len(encoder.encode(full_text(large))),
                'official_budget_usd': args.budget, 'model': args.model,
                'pricing_basis': 'official API prices only, CCCX multiplier excluded by user',
                'shared_prefix_design': 'B/C/D share B; B_large/D_large share B_large',
                'retrieval': {'method': 'BM25', 'chunk_chars': 1600, 'overlap_chars': 300, 'top_k': 4},
                'reasoning_effort': 'none', 'provider_retries': 0, 'production_source_modified': False}
    if args.dry_run:
        save(out / 'dry-run.json', manifest)
        print(json.dumps({k: manifest[k] for k in ('small_context_tokens', 'large_context_tokens', 'official_budget_usd')}, indent=2))
        return
    load_dotenv_file(str(ROOT / '.env'))
    config = provider_config_from_env().model_copy(update={
        'model': args.model, 'max_output_tokens': 4096, 'max_retries': 0,
        'timeout_seconds': 300, 'input_cost_per_mtok_usd': 2.0, 'output_cost_per_mtok_usd': 12.0,
    })
    if config.protocol != 'openai' or config.model != 'gpt-5.6-terra':
        raise RuntimeError('Only the verified Terra official-price configuration is supported.')
    config.resolved_api_key()
    save(out / 'manifest.json', manifest)
    budget = OfficialBudget(args.budget)
    identity = {}
    cases = [('A','A',small,None), ('B','B',small,None),
             ('C','C',small,out/'B'), ('D','D',small,out/'B'),
             ('B_large','B',large,None), ('D_large','D',large,out/'B_large')]
    summary = {}
    try:
        for name, strategy, docs, prefix in cases:
            await run_arm(name, strategy, docs, config, budget, identity, out, prompt, prefix)
            summary[name] = json.loads((out / name / 'run.json').read_text())
    finally:
        save(out / 'summary.json', {'completed': summary, 'official_budget_booked_usd': budget.spent,
                                    'limit_usd': budget.limit, 'model_returned': identity.get('model')})


async def recover_streaming(args):
    out = Path(args.output)
    manifest = json.loads((out / 'manifest.json').read_text())
    prior = json.loads((out / 'summary.json').read_text())
    failed = json.loads((out / 'B_large' / 'run.json').read_text())
    if failed['status'] != 'failed' or '524' not in failed.get('error', ''):
        raise RuntimeError('Recovery is restricted to the recorded HTTP 524 failure.')
    if args.budget != prior['limit_usd'] or args.model != manifest['model']:
        raise RuntimeError('Recovery cannot change the approved budget or model.')
    docs = read_documents([Path(x['path']) for x in manifest['large']])
    if [x['sha256'] for x in docs] != [x['sha256'] for x in manifest['large']]:
        raise RuntimeError('Source material changed; refuse recovery.')
    load_dotenv_file(str(ROOT / '.env'))
    config = provider_config_from_env().model_copy(update={
        'model': args.model, 'max_output_tokens': 4096, 'max_retries': 0,
        'timeout_seconds': 300, 'input_cost_per_mtok_usd': 2.0, 'output_cost_per_mtok_usd': 12.0,
    })
    budget = OfficialBudget(prior['limit_usd'])
    budget.spent = prior['official_budget_booked_usd']
    identity = {'model': prior['model_returned']}
    recovery = {'reason': 'HTTP 524 after 125 seconds; change only transport',
                'prior_budget_booked': budget.spent, 'limit_usd': budget.limit,
                'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    try:
        await run_arm('B_large_stream', 'B', docs, config, budget, identity, out,
                      manifest['prompt'], out / 'B_large', streaming=True)
        await run_arm('D_large_stream', 'D', docs, config, budget, identity, out,
                      manifest['prompt'], out / 'B_large_stream', streaming=True)
        recovery['status'] = 'succeeded'
    finally:
        recovery['official_budget_booked_usd'] = budget.spent
        save(out / 'recovery.json', recovery)


async def supplement_d_large(args):
    out = Path(args.output)
    manifest = json.loads((out / 'manifest.json').read_text())
    recovery = json.loads((out / 'recovery.json').read_text())
    if args.model != manifest['model'] or args.budget != recovery['limit_usd']:
        raise RuntimeError('Supplement cannot change the model or approved official-price cap.')
    docs = read_documents([Path(x['path']) for x in manifest['large']])
    if [x['sha256'] for x in docs] != [x['sha256'] for x in manifest['large']]:
        raise RuntimeError('Frozen sources changed.')
    load_dotenv_file(str(ROOT / '.env'))
    config = provider_config_from_env().model_copy(update={
        'model': args.model, 'max_output_tokens': 4096, 'max_retries': 0,
        'timeout_seconds': 300, 'input_cost_per_mtok_usd': 2.0, 'output_cost_per_mtok_usd': 12.0,
    })
    budget = OfficialBudget(args.budget)
    budget.spent = recovery['official_budget_booked_usd']
    identity = {'model': args.model}
    receipt = {'design': 'production Brief/Outline limits; only chapter requests use BM25',
               'prior_booked_usd': budget.spent, 'limit_usd': budget.limit,
               'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    try:
        await run_arm('D_large_production_prefix', 'D', docs, config, budget, identity,
                      out, manifest['prompt'], production_prefix=True)
        receipt['status'] = 'succeeded'
    except Exception:
        receipt['status'] = 'failed_no_retry'
        raise
    finally:
        receipt['official_budget_booked_usd'] = budget.spent
        save(out / 'supplement.json', receipt)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(DEFAULT_OUT))
    parser.add_argument('--model', default='gpt-5.6-terra')
    parser.add_argument('--budget', type=float, default=3.0)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--recover-streaming', action='store_true')
    parser.add_argument('--supplement-d-large', action='store_true')
    asyncio.run(experiment(parser.parse_args()))
