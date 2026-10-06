"""Typeset revisions must execute supported edits or explicitly reject them."""
import asyncio
from types import SimpleNamespace

import pytest

from ppt_agent.v2.orchestrator import _Checkpoints
from ppt_agent.v2.planning import ContentBrief, DeckOutline, DeckSkeleton, PageBrief, PageSlot, SectionOutline
from ppt_agent.v2.revise import RevisionError, revise_deck_async


class Planner:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.usage = SimpleNamespace(snapshot=lambda: {})

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture
def deck(tmp_path):
    cp = _Checkpoints(tmp_path / 'checkpoints', resume=True)
    brief = ContentBrief(topic='topic', deck_title='Deck')
    skeleton = DeckSkeleton(deck_title='Deck', language='zh-CN', total_pages=4, outline=DeckOutline(
        deck_title='Deck', sections=[SectionOutline(title='S1', content_pages=1)]),
        slots=[PageSlot(page_number=1,kind='cover'),PageSlot(page_number=2,kind='toc'),
               PageSlot(page_number=3,kind='content',section_index=0,section_title='S1',brief=PageBrief(title='Content',points=['One','Two'])),
               PageSlot(page_number=4,kind='closing')])
    cp.save('brief.json',brief.model_dump(mode='json'))
    cp.save('skeleton_with_briefs.json',skeleton.model_dump(mode='json'))
    cp.save('typeset_config.json',{'layout_engine':'typeset','profile':'consulting'})
    return tmp_path, cp


def test_structural_page_rejected_before_model_call(deck):
    root, _ = deck
    client = Planner({})
    with pytest.raises(RevisionError, match='内容页'):
        asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client,selected_pages=[1]))
    assert not client.calls


def test_no_executable_plan_is_not_reported_as_success(deck):
    root, _ = deck
    client = Planner({'reply':'Done', 'pages':[], 'profile':None})
    with pytest.raises(RevisionError, match='没有可执行'):
        asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='move icon',client=client))


def test_unsupported_profile_is_rejected(deck):
    root, _ = deck
    client = Planner({'reply':'Done', 'pages':[], 'profile':'custom'})
    with pytest.raises(RevisionError, match='四种'):
        asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='custom theme',client=client))


def test_unchanged_profile_is_not_reported_as_success(deck):
    root, _ = deck
    client = Planner({'reply':'Done', 'pages':[], 'profile':'consulting'})
    with pytest.raises(RevisionError, match='没有可执行'):
        asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='consulting',client=client))


def _content(title):
    return {'archetype':'points','title':title,'items':[{'heading':'One','body':'First point'}, {'heading':'Two','body':'Second point'}]}


class EditingPlanner(Planner):
    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs['task'] == 'typeset_revision_plan':
            return self.response
        assert kwargs['task'] == 'page_content'
        return _content('Rewritten content')


def _cache(cp, page_number, title):
    cp.save(f'typeset/content_{page_number:03d}.json', {'content':_content(title),
        'record':{'page_number':page_number,'attempts':1,'fallback':False,'validation_errors':[],'archetype':'points'}})


def test_target_content_forced_and_actual_pptx_exported(deck):
    import json
    root, cp = deck
    _cache(cp, 3, 'Original content')
    client = EditingPlanner({'reply':'Will edit', 'pages':[{'page_number':3,'instruction':'rewrite'}]})
    result = asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client,selected_pages=[3]))
    assert result.revised_pages == [3]
    assert result.pptx_path and (root / 'deck.pptx').is_file()
    assert cp.load('typeset/content_003.json')['content']['title'] == 'Rewritten content'
    assert [call['task'] for call in client.calls] == ['typeset_revision_plan', 'page_content']
    report = json.loads((root / 'deck_run_report.json').read_text())
    assert report['revision']['revised_pages'] == [3]
    assert report['typeset_pages'][0]['notes']


def test_profile_switch_reuses_content_and_changes_theme(deck):
    root, cp = deck
    _cache(cp, 3, 'Original content')
    client = EditingPlanner({'reply':'Will restyle','profile':'launch','pages':[]})
    result = asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='launch style',client=client))
    assert result.theme_changed
    assert [call['task'] for call in client.calls] == ['typeset_revision_plan']
    assert cp.load('typeset_config.json')['profile'] == 'launch'
    assert cp.load('brief.json')['deck_type'] == 'launch'
    assert cp.load('typeset/content_003.json')['content']['title'] == 'Original content'


def test_failed_regeneration_restores_previous_content(deck):
    root, cp = deck
    _cache(cp,3,'Original content')
    class InvalidPlanner(EditingPlanner):
        async def complete_json(self, **kwargs):
            if kwargs['task'] == 'page_content':
                self.calls.append(kwargs)
                return {'archetype':'points','title':'Bad'}
            return await super().complete_json(**kwargs)
    client = InvalidPlanner({'reply':'Will edit','pages':[{'page_number':3,'instruction':'rewrite'}]})
    with pytest.raises(RevisionError,match='校验失败'):
        asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client))
    assert cp.load('typeset/content_003.json')['content']['title'] == 'Original content'
    assert not (root / 'deck.pptx').exists()


def test_neighbor_content_reused_and_all_pages_retypeset_in_order(deck, monkeypatch):
    from ppt_agent.v2 import typeset_pipeline
    root, cp = deck
    skeleton = DeckSkeleton.model_validate(cp.load('skeleton_with_briefs.json'))
    neighbor = PageSlot(page_number=4,kind='content',section_index=0,section_title='S1',brief=PageBrief(title='Neighbor',points=['One','Two']))
    skeleton = skeleton.model_copy(update={'total_pages':5,'slots':skeleton.slots[:3]+[neighbor,PageSlot(page_number=5,kind='closing')]})
    cp.save('skeleton_with_briefs.json',skeleton.model_dump(mode='json'))
    _cache(cp,3,'Original content')
    _cache(cp,4,'Neighbor content')
    calls = []
    original = typeset_pipeline.typeset_page
    def tracked(content,profile,**kwargs):
        calls.append((kwargs['page_number'],list(kwargs['history'])))
        return original(content,profile,**kwargs)
    monkeypatch.setattr(typeset_pipeline,'typeset_page',tracked)
    client = EditingPlanner({'reply':'Will edit','pages':[{'page_number':3,'instruction':'rewrite'}]})
    asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client,selected_pages=[3]))
    assert [number for number, _ in calls] == [3,4]
    assert calls[0][1] == [] and len(calls[1][1]) == 1
    assert cp.load('typeset/content_004.json')['content']['title'] == 'Neighbor content'
    assert len([call for call in client.calls if call['task'] == 'page_content']) == 1


def test_model_unchanged_content_is_not_reported_as_edit(deck):
    root, cp = deck
    _cache(cp,3,'Original content')
    cache = cp.load('typeset/content_003.json')
    cache['content']['kicker'] = 'S1'
    cp.save('typeset/content_003.json',cache)
    class UnchangedPlanner(EditingPlanner):
        async def complete_json(self, **kwargs):
            if kwargs['task'] == 'page_content':
                self.calls.append(kwargs)
                from ppt_agent.v2.visual.content import PointsContent
                return PointsContent.model_validate(cache['content']).model_dump(mode='json')
            return await super().complete_json(**kwargs)
    # Stored checkpoints contain all validated optional fields.
    from ppt_agent.v2.visual.content import PointsContent
    cache['content'] = PointsContent.model_validate(cache['content']).model_dump(mode='json')
    cp.save('typeset/content_003.json',cache)
    client = UnchangedPlanner({'reply':'Done','pages':[{'page_number':3,'instruction':'rewrite'}]})
    with pytest.raises(RevisionError,match='没有产生实际'):
        asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client))
    assert cp.load('typeset/content_003.json') == cache


def _files(root):
    return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('failure',['exception','no_pptx','dropped','fallback'])
@pytest.mark.parametrize('restyle',[False,True])
def test_pipeline_failure_rolls_back_entire_revision(deck,monkeypatch,failure,restyle):
    import json
    from ppt_agent.v2 import typeset_pipeline
    root,cp=deck
    _cache(cp,3,'Original content')
    (root/'deck.pptx').write_bytes(b'original pptx')
    before=_files(root)
    original=typeset_pipeline.build_typeset_deck
    async def failing(*args,**kwargs):
        request=args[0]
        if failure=='exception':
            target=__import__('pathlib').Path(request.output_dir)
            (target/'deck.pptx').write_bytes(b'broken pptx')
            raise RuntimeError('layout/render failed')
        result=await original(*args,**kwargs)
        if failure=='no_pptx':
            return result.model_copy(update={'pptx_path':None})
        report_path=__import__('pathlib').Path(result.run_report_path)
        report=json.loads(report_path.read_text())
        if failure=='dropped':
            report['typeset_pages'][0]['notes'].append('dropped item 2')
        else:
            report['typeset_pages'][0]['fallback']=True
            report['typeset_pages'][0]['layout_errors']=['could not fit']
        report_path.write_text(json.dumps(report))
        return result
    monkeypatch.setattr(typeset_pipeline,'build_typeset_deck',failing)
    plan = {'reply':'Will restyle','profile':'launch','pages':[]} if restyle else {'reply':'Will edit','pages':[{'page_number':3,'instruction':'rewrite'}]}
    client=EditingPlanner(plan)
    with pytest.raises((RevisionError,RuntimeError)):
        asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client))
    assert _files(root)==before


def test_second_revision_planner_and_content_receive_current_copy(deck):
    root,cp=deck
    _cache(cp,3,'Current approved title')
    client=EditingPlanner({'reply':'Will edit','pages':[{'page_number':3,'instruction':'new request'}]})
    asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='new request',client=client))
    assert 'Current approved title' in client.calls[0]['user']
    content_call=next(call for call in client.calls if call['task']=='page_content')
    assert 'Current approved title' in content_call['user']
    assert 'new request' in content_call['user']


@pytest.mark.parametrize('preexisting_loss',[False,True])
def test_history_relayout_neighbor_new_loss_rejects_entire_revision(deck,monkeypatch,preexisting_loss):
    import json
    from pathlib import Path
    from ppt_agent.v2 import typeset_pipeline
    root,cp=deck
    skeleton=DeckSkeleton.model_validate(cp.load('skeleton_with_briefs.json'))
    neighbor=PageSlot(page_number=4,kind='content',section_index=0,section_title='S1',brief=PageBrief(title='Neighbor',points=['One','Two']))
    skeleton=skeleton.model_copy(update={'total_pages':5,'slots':skeleton.slots[:3]+[neighbor,PageSlot(page_number=5,kind='closing')]})
    cp.save('skeleton_with_briefs.json',skeleton.model_dump(mode='json'))
    _cache(cp,3,'Original content')
    _cache(cp,4,'Neighbor content')
    (root/'deck_run_report.json').write_text(json.dumps({'typeset_pages':[
        {'page_number':3,'fallback':False,'notes':[]},
        {'page_number':4,'fallback':False,'notes':['dropped item 2'] if preexisting_loss else [],'data_loss':preexisting_loss}]}))
    before=_files(root)
    original=typeset_pipeline.build_typeset_deck
    async def dropped_neighbor(*args,**kwargs):
        result=await original(*args,**kwargs)
        path=Path(result.run_report_path)
        report=json.loads(path.read_text())
        neighbor_record=next(record for record in report['typeset_pages'] if record['page_number']==4)
        neighbor_record['notes'].append('dropped item 2')
        neighbor_record['data_loss']=True
        path.write_text(json.dumps(report))
        return result
    monkeypatch.setattr(typeset_pipeline,'build_typeset_deck',dropped_neighbor)
    client=EditingPlanner({'reply':'Will edit','pages':[{'page_number':3,'instruction':'rewrite'}]})
    if preexisting_loss:
        result=asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client))
        assert result.revised_pages == [3]
    else:
        with pytest.raises(RevisionError,match='第 4 页'):
            asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='rewrite',client=client))
        assert _files(root)==before


def test_profile_switch_preserves_supplied_file_sources(deck):
    root, cp = deck
    cp.save('typeset_config.json', {'layout_engine':'typeset','profile':'consulting',
                                   'source_references':['report.pdf']})
    cp.save('typeset/content_003.json', {'content':_content('Existing') | {'source':'report.pdf 第12页'},
             'record':{'page_number':3,'attempts':1,'fallback':False,'validation_errors':[],'archetype':'points'}})
    client = Planner({'reply':'switch','pages':[],'profile':'launch'})
    asyncio.run(revise_deck_async(output_dir=root,deck_name='deck',message='launch',client=client))
    assert cp.load('typeset/content_003.json')['content']['source']=='report.pdf 第12页'
    assert cp.load('typeset_config.json')['source_references']==['report.pdf']
