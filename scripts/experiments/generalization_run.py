"""One audited generalization run; production prompts and retrieval are unchanged."""
import asyncio
import sys
sys.path.append("/Users/jay/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages")
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import subprocess
import time
import tiktoken
from long_input_experiment import OfficialBudget, official_cost, save
from ppt_agent.runtime import load_dotenv_file, sanitize_error_message
from ppt_agent.v2.providers import OpenAICompatClient, provider_config_from_env
from ppt_agent.v2 import orchestrator
from ppt_agent.v2.orchestrator import BuildRequest, build_deck_async
from ppt_agent.v2.search import TavilySearchProvider
ROOT = Path('/Users/jay/Documents/ppt-agent')
OUT = ROOT / 'data/evaluation/generalization'
RUN = OUT / 'main-run'
SOURCE = ROOT / 'eval/sources/caict_ai_2024.pdf'
MODEL = 'gpt-5.6-terra'
class AuditClient(OpenAICompatClient):
    def __init__(self, config):
        super().__init__(config)
        self.records = []
        self.payload = ContextVar('response', default=None)
        self.budget = OfficialBudget(1.5)
        self.encoder = tiktoken.get_encoding('o200k_base')
        self.halted = False
    def _parse_response(self, payload):
        self.payload.set(payload)
        if payload.get('model') != MODEL:
            self.halted = True
            raise RuntimeError('Returned model differs from frozen model; stop.')
        return super()._parse_response(payload)
    async def complete_json(self, *, task, system, user, **kwargs):
        if self.halted:
            raise RuntimeError('Audit client stopped after provider failure.')
        number = len(self.records) + 1
        rid = f'generalization-{number}'
        tokens = len(self.encoder.encode(system + '\n' + user))
        cap = kwargs.get('max_output_tokens') or self.config.max_output_tokens
        if len(system) + len(user) > 40000:
            raise RuntimeError('Request exceeds frozen 40000-character ceiling.')
        self.budget.reserve(rid, tokens, cap)
        record = {'request_id': rid, 'task': task, 'estimated_input_tokens': tokens}
        self.records.append(record)
        save(RUN / 'calls' / f'{number:03d}-request.json', {'task':task,'system':system,'user':user})
        self.payload.set(None)
        started = time.perf_counter()
        cost = None
        try:
            result = await super().complete_json(task=task, system=system, user=user, **kwargs)
            record['status'] = 'succeeded'
            save(RUN / 'calls' / f'{number:03d}-response.json', result)
            return result
        except Exception as exc:
            self.halted = True
            record['status'] = 'failed'
            record['error'] = sanitize_error_message(str(exc)).replace(self.config.resolved_api_key(), '[redacted]')
            raise
        finally:
            payload = self.payload.get()
            if payload:
                record.update(response_model=payload.get('model'),usage=payload.get('usage'),response_id=payload.get('id'))
                cost = official_cost(payload.get('usage') or {})
            record.update(official_cost_usd=cost, seconds=round(time.perf_counter()-started,3))
            self.budget.settle(rid,cost)
            save(RUN / 'calls.json',self.records)
class Search(TavilySearchProvider):
    def __init__(self):
        super().__init__(); self.records=[]
    async def search(self, query, **kwargs):
        start=time.perf_counter()
        try:
            results=await super().search(query,**kwargs)
            self.records.append({'query':query,'results':len(results),'seconds':round(time.perf_counter()-start,3)})
            return results
        finally:
            save(RUN/'search-calls.json',self.records)
async def main():
    if RUN.exists(): raise RuntimeError('Run already exists; no silent rerun or resume.')
    imported_repo = Path(orchestrator.__file__).resolve().parents[3]
    imported_commit = subprocess.check_output(['git','-C',str(imported_repo),'rev-parse','HEAD'],text=True).strip()
    if imported_repo != ROOT or not imported_commit.startswith('d0cf557'):
        raise RuntimeError('Frozen run requires original main d0cf557 via PYTHONPATH; refusing a different source checkout.')
    RUN.mkdir(parents=True)
    load_dotenv_file(str(ROOT/'.env'))
    config=provider_config_from_env().model_copy(update={'model':MODEL,'reasoning_effort':'none','max_retries':0,'max_output_tokens':4096,'input_cost_per_mtok_usd':2.,'output_cost_per_mtok_usd':12.})
    client=AuditClient(config); search=Search()
    prompt='请根据附件中国信通院《人工智能发展报告（2024年）》，为企业管理层制作一份20页中文咨询风格汇报。目的：理解人工智能技术与产业发展态势，判断企业应用机会和治理风险。覆盖报告所有一级章节，兼顾技术、产业、应用及治理，不只总结开头。数字、单位、时间和指标含义必须与原文一致，标明文件名和PDF实际页码；联网搜索仅用于补充背景并标明网址，不替代附件中的数据。不编造统计数字。每页讲清一个观点，优先使用有依据的图表和指标，保持可编辑。'
    request=BuildRequest(prompt=prompt,page_count=20,style_profile='consulting',source_paths=[str(SOURCE)],layout_engine='typeset',enable_search=True,output_dir=str(RUN),deck_name='generalization',concurrency=3,budget_usd=1.5)
    save(RUN/'manifest.json',{'baseline_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'source_url':'https://www.caict.ac.cn/kxyj/qwfb/bps/202412/P020241210548865982463.pdf','request':request.model_dump(mode='json'),'requested_model':MODEL,'pricing':'official token prices; excludes CCCX multiplier; model cap USD1.5; Tavily separately recorded'})
    start=time.perf_counter()
    try:
        result=await build_deck_async(request,client,search_provider=search,progress=lambda s: print(s,flush=True))
        save(RUN/'result.json',result.model_dump(mode='json'))
    finally:
        await client.aclose()
        save(RUN/'billing.json',{'seconds':round(time.perf_counter()-start,3),'known_official_usd':sum(r.get('official_cost_usd') or 0 for r in client.records),'booked_usd':client.budget.spent,'models':sorted({r['response_model'] for r in client.records if r.get('response_model')}),'search_calls':len(search.records),'search_usd':'exact debit unavailable; basic search credits recorded separately'})
if __name__=='__main__': asyncio.run(main())
