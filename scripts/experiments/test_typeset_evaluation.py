import asyncio
import json
import httpx

from typeset_evaluation import AuditedClient
from long_input_experiment import OfficialBudget
from ppt_agent.v2.providers import ProviderConfig


def test_concurrent_response_accounting_stays_with_request(tmp_path):
    async def check():
        async def handler(request):
            n=int(json.loads(request.content)['messages'][-1]['content'])
            await asyncio.sleep((4-n)*.01)
            return httpx.Response(200,json={'model':'gpt-5.6-terra','id':str(n),
                'choices':[{'message':{'content':json.dumps({'n':n})}}],
                'usage':{'prompt_tokens':n*100,'completion_tokens':n*10}})
        cfg=ProviderConfig(model='gpt-5.6-terra',api_key='test',max_retries=0)
        budget=OfficialBudget(1.5)
        docs=[{'doc_id':'doc1','pages':[{'pdf_page':n,'text':'参考文字'} for n in range(9,64)]}]
        client=AuditedClient(cfg,'A',docs,budget,tmp_path,{'model':cfg.model})
        await client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await asyncio.gather(*(client.complete_json(task='preflight',system='JSON',user=str(n),context={}) for n in [1,2,3]))
        assert [(r['response_id'],r['usage']['prompt_tokens']) for r in client.records]==[('1',100),('2',200),('3',300)]
        assert abs(budget.spent-.00192)<1e-9
        await client.aclose()
    asyncio.run(check())
