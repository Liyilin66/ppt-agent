import asyncio
import json
from pathlib import Path

from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest,build_deck,_Checkpoints
from ppt_agent.v2.planning import ContentBrief,PageBrief,PageSlot
from ppt_agent.v2.typeset_pipeline import generate_content
from ppt_agent.v2.visual.profiles import PROFILES

class Capture(MockLLMClient):
    def __init__(self):super().__init__();self.content_requests=[]
    async def complete_json(self,**kw):
        if kw['task']=='page_content':self.content_requests.append(kw)
        return await super().complete_json(**kw)


def test_planned_archetypes_reach_prompts_and_deck_statistics(tmp_path):
    client=Capture();r=build_deck(BuildRequest(prompt='离线方案',page_count=20,output_dir=str(tmp_path)),client,progress=lambda _:None)
    report=json.loads(Path(r.run_report_path).read_text());stats=report['content_statistics']
    assert stats['archetype_ratios'].get('points',0)<=.4
    assert stats['diversity_violations']==[]
    assert stats['source_empty_pages']==17 and stats['source_invalid_pages']==0
    assigned={p['page_number']:p['assigned_archetype'] for p in report['typeset_pages']}
    for req in client.content_requests:
        payload=json.loads(req['user'].split('\nPrevious output failed validation.')[0])
        assert payload['diversity']['assigned_archetype']==assigned[req['context']['page_number']]
    plan=[assigned[n] for n in sorted(assigned)]
    assert all(a!=b for a,b in zip(plan,plan[1:]))
    assert payload['diversity']['deck_plan']==plan
    assert 'body' in client.content_requests[0]['system'] and '40' in client.content_requests[0]['system']


def test_invalid_source_retries_and_records_attempt(tmp_path):
    class SourceClient(MockLLMClient):
        def __init__(self):super().__init__();self.n=0;self.requests=[]
        async def complete_json(self,**kw):
            self.n+=1;self.requests.append(kw)
            return {'archetype':'statement','title':'观点','statement':'先验证再投入',
                    'source':'用户提供的需求' if self.n==1 else None}
    client=SourceClient();slot=PageSlot(page_number=3,kind='content',brief=PageBrief(title='观点'))
    c,r=asyncio.run(generate_content(client,_Checkpoints(tmp_path,resume=False),slot,
                                   ContentBrief(topic='方案',deck_title='方案'),PROFILES['corporate']))
    assert c.source is None and r['source_invalid_attempts']==1 and r['attempts']==2
    assert 'source' in client.requests[1]['user']


def test_source_requires_an_actual_supplied_file_or_url():
    from ppt_agent.v2.typeset_pipeline import source_is_valid
    assert source_is_valid('report.pdf 第12页',['report.pdf'])
    assert source_is_valid('https://example.com/report', ['https://example.com/report'])
    assert not source_is_valid('https://fake.com/report',['report.pdf'])
    assert not source_is_valid('report.pdf',[])
    assert not source_is_valid('用户提供的需求',['report.pdf'])
    assert not source_is_valid('report.pdf；另一份fake.pdf',['report.pdf'])

def test_diversity_violation_is_retried_then_honestly_reported(tmp_path):
    class AlwaysPoints(MockLLMClient):
        async def complete_json(self,**kw):
            if kw['task']=='page_content':
                return {'archetype':'points','title':f"页面{kw['context']['page_number']}",
                        'items':[{'heading':'观点','body':'明确问题'}, {'heading':'行动','body':'验证方案'}]}
            return await super().complete_json(**kw)
    r=build_deck(BuildRequest(prompt='拒绝多样化的模型',page_count=10,output_dir=str(tmp_path)),
                 AlwaysPoints(),progress=lambda _:None)
    report=json.loads(Path(r.run_report_path).read_text())
    assert report['content_statistics']['diversity_violations']
    assert any(p['fallback'] for p in report['typeset_pages'])
    assert any('diversity' in e['error'] for p in report['typeset_pages'] for e in p['validation_errors'])


def test_original_comparison_timeline_hints_reach_real_prompt(tmp_path):
    for hint in ('comparison','timeline'):
        client=Capture()
        slot=PageSlot(page_number=3,kind='content',brief=PageBrief(title='观点',layout_hint=hint))
        asyncio.run(generate_content(client,_Checkpoints(tmp_path/hint,resume=False),slot,
                                   ContentBrief(topic='方案',deck_title='方案'),PROFILES['corporate']))
        payload=json.loads(client.content_requests[0]['user'])
        assert payload['page']['page_brief']['layout_hint']==hint


def test_resumed_invalid_source_is_removed_and_counted(tmp_path):
    request=BuildRequest(prompt='缓存校验',page_count=10,output_dir=str(tmp_path))
    build_deck(request,MockLLMClient(),progress=lambda _:None)
    p=tmp_path/'checkpoints/typeset/content_003.json'
    cached=json.loads(p.read_text());cached['content']['source']='用户提供的需求'
    p.write_text(json.dumps(cached))
    r=build_deck(request.model_copy(update={'resume':True}),MockLLMClient(),progress=lambda _:None)
    stats=json.loads(Path(r.run_report_path).read_text())['content_statistics']
    assert stats['source_invalid_cached_pages']==1 and stats['source_invalid_pages']==0
    assert json.loads(p.read_text())['content']['source'] is None

def test_bare_page_needs_unique_pdf_and_actual_page_count():
    from ppt_agent.v2.typeset_pipeline import source_is_valid
    assert not source_is_valid('PDF 1页',['https://example.com/a'])
    assert not source_is_valid('第999999页',['report.pdf'],{'report.pdf':63})
    assert source_is_valid('第12页',['report.pdf'],{'report.pdf':63})
    assert not source_is_valid('第12页',['a.pdf','b.pdf'],{'a.pdf':63,'b.pdf':63})
    assert not source_is_valid('report.pdf.exe',['report.pdf'])
    assert not source_is_valid('report.pdf 第64页',['report.pdf'],{'report.pdf':63})


def test_fallback_keeps_long_factual_body_instead_of_soft_limit_truncation():
    from ppt_agent.v2.typeset_pipeline import fallback_content
    point='资料原文中的背景说明'*4+'数量538项'
    c=fallback_content(PageSlot(page_number=3,kind='content',brief=PageBrief(title='保留事实',points=[point,'核查来源'])))
    assert c.items[0].body==point and '538项' in c.items[0].body
