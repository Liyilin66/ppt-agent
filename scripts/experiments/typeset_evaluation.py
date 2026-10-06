"""Audited real-model validation of the typeset pipeline; no visual changes."""
import argparse
import asyncio
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import subprocess

from long_input_experiment import OfficialBudget, RoutedClient, official_cost, save
from long_input_data import read_documents
from ppt_agent.runtime import load_dotenv_file
from ppt_agent.v2.orchestrator import BuildRequest, build_deck_async
from ppt_agent.v2.providers import provider_config_from_env

ROOT=Path('/Users/jay/Documents/ppt-agent')
OUT=ROOT/'data/evaluation/step3b'
MODEL='gpt-5.6-terra'

class AuditedClient(RoutedClient):
    """Keep response usage local to each asynchronous call, not shared state."""
    def __init__(self,*args,**kwargs):
        self._response=ContextVar('typeset_response',default=None)
        super().__init__(*args,**kwargs)
    @property
    def pending_payload(self):return self._response.get()
    @pending_payload.setter
    def pending_payload(self,payload):self._response.set(payload)

async def run(preflight_only=False):
    OUT.mkdir(parents=True,exist_ok=True)
    load_dotenv_file(str(ROOT/'.env'))
    config=provider_config_from_env().model_copy(update={'model':MODEL,'max_output_tokens':4096,
        'max_retries':0,'timeout_seconds':300,'input_cost_per_mtok_usd':2.,'output_cost_per_mtok_usd':12.})
    docs=read_documents([ROOT/'eval/sources/cnnic_genai_2025.pdf'])
    budget=OfficialBudget(1.5)
    receipt=OUT/'budget.json'
    if receipt.exists():budget.spent=json.loads(receipt.read_text())['booked_usd']
    identity={'model':MODEL}
    pre=OUT/'preflight'
    if not (pre/'verified.json').exists():
        client=AuditedClient(config,'A',docs,budget,pre,identity)
        try:
            await client.complete_json(task='preflight',system='Return JSON only.',user='Return {"ok":true}.',context={},max_output_tokens=32)
            save(pre/'verified.json',{'model':MODEL,'base_url':config.base_url,'pricing_basis':'official token usage, no CCCX 3x multiplier',
                                    'uncached_input_per_million':2,'cached_input_per_million':.2,'output_per_million':12,
                                    'pricing_source':'https://developers.openai.com/api/docs/models/gpt-5.6-terra'})
        finally:
            await client.aclose();save(receipt,{'booked_usd':budget.spent,'limit_usd':1.5})
    if preflight_only:return
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    t3=json.loads((ROOT/'data/evaluation/long-input/runs/run1/manifest.json').read_text())['prompt']
    t2=(ROOT/'eval/tasks/T2_product.md').read_text().split('```')[1].strip()
    tasks=[('T3',t3,20,'consulting',[docs[0]['path']]),('T2',t2,10,'corporate',[])]
    for name,prompt,pages,profile,sources in tasks:
        folder=OUT/name
        if (folder/'result.json').exists():continue
        if folder.exists():raise RuntimeError(f'{folder} contains an unfinished paid run; do not silently rerun.')
        folder.mkdir()
        request=BuildRequest(prompt=prompt,page_count=pages,style_profile=profile,layout_engine='typeset',
                             output_dir=str(folder),deck_name=name,source_paths=sources,concurrency=3,budget_usd=1.5)
        save(folder/'manifest.json',{'commit':commit,'model':MODEL,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                                    'source_sha256':docs[0]['sha256'] if sources else None,'request':request.model_dump(mode='json')})
        client=AuditedClient(config,'A',docs,budget,folder,identity)
        try:
            result=await build_deck_async(request,client,progress=lambda x:print(f'[{name}] {x}',flush=True))
            save(folder/'result.json',result.model_dump(mode='json'))
        finally:
            await client.aclose();save(receipt,{'booked_usd':budget.spent,'limit_usd':1.5})
    known=0.;unknown=0.;models=set()
    for path in OUT.glob('*/calls.json'):
        for c in json.loads(path.read_text()):
            known+=c['official_cost_usd'] or 0.
            if c.get('response_model'):models.add(c['response_model'])
    save(OUT/'billing.json',{'known_official_usd':known,'unknown_reserve_usd':max(0,budget.spent-known),
                            'booked_usd':budget.spent,'limit_usd':1.5,'actual_models':sorted(models)})

if __name__=='__main__':
    args=argparse.ArgumentParser();args.add_argument('--preflight-only',action='store_true')
    asyncio.run(run(args.parse_args().preflight_only))
