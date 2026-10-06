"""One frozen real T2 run with live search and a USD 1 cost ceiling."""
import asyncio,json,time,subprocess
from pathlib import Path
from evidence_acceptance import AcceptanceClient,OfficialBudget,MODEL,save
from long_input_data import read_documents
from ppt_agent.runtime import load_dotenv_file
from ppt_agent.v2.providers import provider_config_from_env
from ppt_agent.v2.orchestrator import BuildRequest,build_deck_async
from ppt_agent.v2.search import TavilySearchProvider
from ppt_agent.v2.evidence_check import check_content_numbers
from ppt_agent.v2.typeset_pipeline import content_adapter,cited_evidence
from ppt_agent.v2.evidence import EvidencePacket
ROOT=Path('/Users/jay/Documents/ppt-agent');OUT=ROOT/'data/evaluation/evidence-check';RUN=OUT/'T2'
async def main():
 if RUN.exists():raise RuntimeError('One real run only; do not retry an existing run.')
 RUN.mkdir(parents=True);load_dotenv_file(str(ROOT/'.env'))
 config=provider_config_from_env().model_copy(update={'model':MODEL,'max_output_tokens':4096,'max_retries':0,'timeout_seconds':300,'input_cost_per_mtok_usd':2.,'output_cost_per_mtok_usd':12.})
 budget=OfficialBudget(.98) # Reserve up to $0.02 for one basic search; ledger stays below $1.
 client=AcceptanceClient(config,'A',read_documents([ROOT/'eval/sources/cnnic_genai_2025.pdf']),budget,RUN,{'model':MODEL})
 class Search(TavilySearchProvider):
  async def search(self,query,**kw):
   start=time.perf_counter();items=await super().search(query,**kw)
   save(OUT/'search-receipt.json',{'provider':'Tavily','search_calls':1,'results':len(items),'seconds':time.perf_counter()-start,'billing':'basic request; at most 1 credit per official documentation; dollar reserve USD0.02, exact wallet debit unavailable'})
   return items
 prompt=(ROOT/'eval/tasks/T2_product.md').read_text().split('```')[1].strip()
 request=BuildRequest(prompt=prompt,page_count=10,style_profile='corporate',enable_search=True,output_dir=str(RUN),deck_name='T2',budget_usd=.98)
 save(RUN/'manifest.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'request':request.model_dump(mode='json'),'model':MODEL,'total_cost_cap_usd':1,'search_reserve_usd':.02})
 try:
  start=time.perf_counter();result=await build_deck_async(request,client,search_provider=Search(),progress=print)
  save(RUN/'result.json',result.model_dump(mode='json'));save(OUT/'elapsed.json',{'seconds':time.perf_counter()-start})
  validation=[]
  for file in sorted((RUN/'checkpoints/typeset').glob('content_*.json')):
   x=json.loads(file.read_text());content=content_adapter().validate_python(x['content']);packet=EvidencePacket(**x['record']['evidence'])
   issues=check_content_numbers(content,cited_evidence(content.source,packet))
   assert not issues,(file.name,issues)
   validation.append({'page':x['record']['page_number'],'numeric_final_issues':issues,'source':content.source,'source_is_url':bool(content.source and content.source.startswith(('http://','https://')))})
  save(OUT/'numeric-final-check.json',validation)
 finally:
  await client.aclose();save(OUT/'billing.json',{'known_official_model_usd':sum(c.get('official_cost_usd') or 0 for c in client.records),'model_budget_booked_usd':budget.spent,'search_cost_reserve_usd':.02,'upper_accounted_usd':budget.spent+.02,'limit_usd':1,'actual_models':sorted({c.get('response_model') for c in client.records if c.get('response_model')})})
asyncio.run(main())
