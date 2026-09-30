import json
from copy import deepcopy

import pytest

from marketing_agent.presentation import plan_outline
from marketing_agent.presentation_design import validate_design, plan_design
from marketing_agent.presentation_compat import numbers, write_log


def fixture():
    source=plan_outline('# 测试方案\n\n## 节奏\n2025.9.1-10.15 开展预热。\n\n## 指标\n预计60万–120万，假设值。\n\n## 预算\n腰尾部合计60%预算保障破圈与口碑厚度。\n\n## 附录\n不得遗漏的原稿。')
    pages=[{'layout':'steps','title':'传播节奏','source_ids':['b0000'],'items':[
        {'label':'预热','text':'2025.9.1-10.15 开展预热。','value':'01'},
        {'label':'后续','text':'持续传播','value':'02'}]},
        {'layout':'statement','title':'指标说明','source_ids':['b0001'],'items':[]},
        {'layout':'closing','title':'下一步','source_ids':['b0002'],'items':[]}]
    return source, {'pages':pages}


def test_dates_ordinals_and_numeric_boundaries():
    source,data=fixture()
    assert not validate_design(data,source,compatible=True)['corrections']
    assert numbers('2025.9.1，1,000,000，0.3')=={'2025.9.1','1000000','0.3'}
    data['pages'][0]['items'][0]['text']='2025.9.2 开展预热'
    with pytest.raises(ValueError,match='数字'):validate_design(deepcopy(data),source)
    tolerant=validate_design(data,source,compatible=True)
    assert tolerant['corrections'][0]['field']=='items[0].text'
    assert tolerant['corrections'][0]['status']=='warning'
    assert '待核验' in tolerant['pages'][0]['evidence_note']


def test_composition_repair_and_exact_citation_without_number_only_match():
    source,data=fixture()
    data['pages'][0].update(layout='hero_statement',items=[],title='腰尾部合计60%预算保障破圈与口碑厚度')
    result=validate_design(data,source,compatible=True)
    assert result['pages'][0]['layout']=='statement'
    assert result['pages'][0]['composition']=='hero_statement'
    assert 'b0002' in result['pages'][0]['source_ids']
    assert {c['field'] for c in result['corrections']}=={'layout','source_ids'}
    source,data=fixture();data['pages'][0]['title']='获客60万人'
    result=validate_design(data,source,compatible=True)
    assert result['pages'][0]['source_ids']==['b0000']
    assert result['pages'][0]['needs_review']


@pytest.mark.parametrize('change',[
    {'layout':'metrics','items':[{'label':'目标','value':'120万','text':'预计'}]},
    {'layout':'chart','chart':{'type':'donut','categories':['甲','乙'],'series':[]},'items':[]},
    {'items':None}, {'title':'超长标题'*20}, {'layout':[],'composition':[],'items':[None]},
])
def test_bad_page_recovers_as_source_text_without_losing_original(change):
    source,data=fixture();data['pages'][1].update(change)
    result=validate_design(data,source,compatible=True)
    assert result['pages'][1]['compatibility_fallback']
    assert result['pages'][1]['layout']=='columns'
    assert any(c['page']==2 and c['field']=='page' for c in result['corrections'])
    assigned={b['id']:b['source'] for p in result['pages'] for b in p['blocks']}
    assert assigned=={b['id']:b['source'] for b in source['source_blocks']}


def test_cache_revalidation_does_not_use_note_only_blocks_as_evidence():
    source,data=fixture();data['pages'][-1].update(source_ids=['b0000'],title='预算60%')
    result=validate_design(data,source,compatible=True)
    assert result['pages'][-1]['source_ids']==['b0000']
    assert result['pages'][-1]['needs_review']
    again=validate_design(deepcopy(result),source,compatible=True)
    assert again['corrections']==result['corrections']
    assert len(again['pages'][-1]['blocks'])==len(result['pages'][-1]['blocks'])


def test_resume_response_uses_no_paid_model_and_maps_log_to_final_pages(tmp_path,monkeypatch):
    source,data=fixture();data['pages'][0]['title']='预计999万次'
    (tmp_path/'resumed-response.json').write_text(json.dumps({'choices':[{'message':{'content':json.dumps(data)}}]}))
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:pytest.fail('不应重复付费规划'))
    result=plan_design(source,tmp_path)
    assert len(result['pages'])==3
    assert (tmp_path/'corrections.md').exists()
    (tmp_path/'outline.json').write_text(json.dumps({'pages':[{'plan_page':1},{'plan_page':1},{'plan_page':2},{'plan_page':3}]}))
    write_log(tmp_path,result)
    assert '成品第 1、2 页' in (tmp_path/'corrections.md').read_text()
    assert result['corrections'][0]['sources'][0]['line']==4
    result['source_blocks']=source['source_blocks']
    result['page_generation']={'fallbacks':[{'page':2,'reason':'字号不足'}]}
    (tmp_path/'report.json').write_text(json.dumps({'repairs':[{'page':0,'attempt':1,'issues':[{'rule':'content_capacity'}]}]}))
    write_log(tmp_path,result)
    assert any(c['field']=='custom' and c['page']==2 and c['sources'] for c in result['corrections'])
    assert any(c['field']=='rendering[1].attempt[1]' for c in result['corrections'])
    count=len(result['corrections']);write_log(tmp_path,result)
    assert len(result['corrections'])==count


def test_two_invalid_plans_still_produce_a_source_based_deck(tmp_path,monkeypatch):
    from io import BytesIO
    source,_=fixture()
    monkeypatch.setenv('MARKETING_API_KEY','test')
    monkeypatch.setenv('MARKETING_BASE_URL','https://example.invalid')
    calls=[]
    def response(*args,**kwargs):
        calls.append(1)
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'not-json'}}]}).encode())
    monkeypatch.setattr('urllib.request.urlopen',response)
    result=plan_design(source,tmp_path)
    assert len(calls)==2
    assert len(result['pages'])>=3
    assert all(p['compatibility_fallback'] for p in result['pages'])
    assert result['corrections'][-1]['field']=='pages'


def test_retry_reuses_response_and_log_download_works_before_completion(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from marketing_agent.api import create_app
    from marketing_agent.runtime import DemoRuntime
    app=create_app(tmp_path,DemoRuntime())
    monkeypatch.setattr(app.state.presentations.pool,'submit',lambda *a:None)
    with TestClient(app) as client:
        run=client.post('/api/runs/import',json={'title':'兼容测试','manuscript':'# 稿件\n\n测试正文内容。'}).json()
        url='/api/runs/'+run['id']+'/presentation'
        first=client.post(url).json()
        folder=app.state.presentations.root/first['id']
        (folder/'design-response-2.json').write_text('{"original":true}')
        app.state.presentations.update(first['id'],status='failed')
        second=client.post(url).json()
        assert second['resumed_from']==first['id']
        new_folder=app.state.presentations.root/second['id']
        assert (new_folder/'resumed-response.json').read_text()=='{"original":true}'
        (new_folder/'corrections.md').write_text('测试改动日志')
        base='/api/presentations/'+second['id']+'/files/'
        assert client.get(base+'corrections.md').text=='测试改动日志'
        assert client.get(base+'presentation.pptx').status_code==404
        assert client.get(base+'resumed-response.json').status_code==404
