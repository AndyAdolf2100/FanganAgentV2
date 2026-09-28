import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from marketing_agent.api import create_app
from marketing_agent.presentation import PresentationJobs, parse_manuscript, plan_outline
from marketing_agent.runtime import DemoRuntime
from marketing_agent.store import Store


def complete_run(store):
    run = store.create({'brief':'营销方案','mode':'auto','runtime':'demo'})
    def finish(r):
        r.update(status='completed',outputs={'assembly':'# 测试品牌\n\n## 预算\n总额300万元。\n\n| 项目 | 金额 |\n|---|---|\n| 制作 | 30万 |\n| 其他 | 270万 |'},index=8)
    return store.mutate(run['id'],finish)


def test_semantics_preserve_table_and_disclosure():
    text='# 标题\n\n## KPI\n预测而非保证。\n\n| 名称 | 数值 |\n|---|---|\n| A\\|B | **300万元** |\n\n来源：[报告](https://example.com/report)'
    parsed=parse_manuscript(text)
    table=next(b for b in parsed['blocks'] if b['kind']=='table')
    assert table['rows']==[['A|B','300万元']]
    assert 'https://example.com/report' in parsed['blocks'][-1]['source']
    plan=plan_outline(text)
    assert [b['id'] for p in plan['pages'] for b in p['blocks']]==[b['id'] for b in parsed['blocks']]
    with pytest.raises(ValueError):parse_manuscript('# 只有标题')


def test_jobs_deduplicate_and_snapshot(tmp_path,monkeypatch):
    store=Store(tmp_path/'db.sqlite'); run=complete_run(store); jobs=PresentationJobs(tmp_path,store)
    calls=[];monkeypatch.setattr(jobs.pool,'submit',lambda *a:calls.append(a))
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:jobs.create(run),range(4)))
    assert len({r['id'] for r in results})==1 and len(calls)==1
    job=results[0]
    assert (jobs.root/job['id']/'manuscript.md').read_text()==run['outputs']['assembly']
    jobs.recover(); assert jobs.get(job['id'])['status']=='failed'
    with pytest.raises(KeyError):jobs.get('../db.sqlite')
    jobs.pool.shutdown()


def test_presentation_endpoints_block_partial_and_path_access(tmp_path,monkeypatch):
    app=create_app(tmp_path,DemoRuntime())
    with TestClient(app) as client:
        store=app.state.store; jobs=app.state.presentations
        run=complete_run(store)
        monkeypatch.setattr(jobs.pool,'submit',lambda *a:None)
        response=client.post(f"/api/runs/{run['id']}/presentation")
        assert response.status_code==202
        job=response.json()
        assert client.get(f"/api/presentations/{job['id']}/files/presentation.pptx").status_code==404
        assert client.get(f"/api/presentations/{job['id']}/files/job.json").status_code==404
        assert client.get('/api/presentations/missing').status_code==404
        jobs.update(job['id'],status='completed',page_count=1)
        (jobs.root/job['id']/'presentation.pptx').write_bytes(b'test')
        assert client.get(f"/api/presentations/{job['id']}/files/presentation.pptx").content==b'test'
        assert client.get(f"/api/presentations/{job['id']}/pages/0").status_code==404
        def revise(r):r.update(status='running')
        store.mutate(run['id'],revise)
        assert client.post(f"/api/runs/{run['id']}/presentation").status_code==409


def test_image_validation_and_success_cache(tmp_path, monkeypatch):
    import base64
    import io
    from PIL import Image
    from marketing_agent import presentation_images as images
    for key, value in {'MARKETING_IMAGE_BASE_URL':'https://example.test/v1','MARKETING_IMAGE_API_KEY':'test-only','MARKETING_IMAGE_MODEL':'test','MARKETING_IMAGE_ENABLED':'true','MARKETING_IMAGE_PRICE_RMB':'0.1','MARKETING_IMAGE_MAX_REQUESTS':'2','MARKETING_IMAGE_CACHE_DIR':str(tmp_path/'cache')}.items():
        monkeypatch.setenv(key,value)
    content=io.BytesIO(); Image.new('RGB',(256,256),'green').save(content,format='PNG')
    payload={'data':[{'b64_json':base64.b64encode(content.getvalue()).decode()}]}
    calls=[]
    def response(request,**kwargs):
        calls.append(request)
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(images.urllib.request,'urlopen',response)
    target=tmp_path/'image.png'
    images.generate_image('test',target); images.generate_image('test',target)
    assert len(calls)==1 and target.exists()
    payload['data']=[{'url':'https://unknown.test/image.png'}]
    with pytest.raises(ValueError,match='b64_json'): images.generate_image('changed',target)


def test_package_normalizer_preserves_valid_parts_and_rejects_broken_relationship(tmp_path):
    import importlib.util
    from pathlib import Path
    from zipfile import ZipFile
    spec=importlib.util.spec_from_file_location('package_check',Path(__file__).parents[1]/'presentation'/'validate_package.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    target=tmp_path/'test.pptx'
    parts={'[Content_Types].xml':b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/ppt/slideMasters/slideMaster2.xml" ContentType="x"/></Types>',
           '_rels/.rels':b'<Relationships><Relationship Id="r1" Target="ppt/presentation.xml"/></Relationships>',
           'ppt/presentation.xml':b'<presentation/>'}
    with ZipFile(target,'w') as z:
        for key,value in parts.items():z.writestr(key,value)
    result=module.validate_package(target)
    assert result['normalized_orphan_masters']==1
    assert module.validate_package(target)['normalized_orphan_masters']==0
    with ZipFile(target) as z: assert z.read('ppt/presentation.xml')==parts['ppt/presentation.xml']
    parts.pop('ppt/presentation.xml')
    with ZipFile(target,'w') as z:
        for key,value in parts.items():z.writestr(key,value)
    with pytest.raises(ValueError,match='Missing relationship'):module.validate_package(target)


def test_visual_planner_rejects_uncited_numbers_and_preserves_notes():
    from marketing_agent.presentation_design import validate_design
    from copy import deepcopy
    source=plan_outline('# 方案\n\n## 指标\n预计60万–120万，假设值。\n\n## 预算\n预算300万元。\n\n## 来源\n数据待核验。')
    pages=[{'layout':'metrics','title':'规划目标','source_ids':['b0000'],'items':[{'label':'问询增量','value':'60万–120万','text':'预计，假设值'}]},
           {'layout':'statement','title':'预算','source_ids':['b0001'],'items':[]},
           {'layout':'closing','title':'行动','source_ids':['b0001'],'items':[]}]
    data=validate_design({'pages':deepcopy(pages)},source)
    assert {b['id'] for p in data['pages'] for b in p['blocks']}=={b['id'] for b in source['source_blocks']}
    pages[0]['items'][0]['value']='999万'
    with pytest.raises(ValueError,match='数字'):validate_design({'pages':pages},source)


def test_image_budget_unknown_price_and_limit_do_not_send(tmp_path,monkeypatch):
    from marketing_agent import presentation_images as images
    monkeypatch.setenv('MARKETING_IMAGE_PRICE_RMB','0')
    with pytest.raises(ValueError,match='单价'):images.reserve_budget(tmp_path,'a')
    monkeypatch.setenv('MARKETING_IMAGE_PRICE_RMB','1')
    monkeypatch.setenv('MARKETING_IMAGE_MAX_REQUESTS','1')
    images.reserve_budget(tmp_path,'a')
    with pytest.raises(ValueError,match='预算或请求'):images.reserve_budget(tmp_path,'b')


def test_metric_range_keeps_both_ends_and_units():
    from marketing_agent.presentation_design import protect_metric_range
    protect_metric_range('60万–120万','预计60 万–120 万，假设值')
    with pytest.raises(ValueError,match='单点'):protect_metric_range('120万','预计60万–120万')
    with pytest.raises(ValueError,match='区间或单位'):protect_metric_range('60万–120亿','预计60万–120万')


def test_page_tools_lock_text_and_reject_active_content(tmp_path):
    from marketing_agent.presentation_pages import validate_html, expand_text_refs, PageTools
    page={'title':'预计60万–120万','subtitle':'假设值','section':'KPI','items':[],'source_ids':['b0']}
    body="<main><h1 data-ref='title' data-role='title'></h1><p data-ref='subtitle' data-role='body'></p><span data-ref='section' data-role='section'></span></main>"
    markup={'body':expand_text_refs(body,page),'css':'main{width:1280px;height:720px;display:flex;justify-content:space-between}'}
    assert '预计60万–120万' in validate_html(markup,page,{})['body']
    with pytest.raises(ValueError,match='标签'):validate_html({**markup,'body':markup['body']+'<script>x</script>'},page,{})
    with pytest.raises(ValueError,match='漏改'):validate_html({**markup,'body':markup['body'].replace('60万–120万','90万')},page,{})
    tool=PageTools(tmp_path,{'pages':[page],'assets':{}})
    tool.write_page(0,markup,0)
    with pytest.raises(ValueError,match='版本'):tool.write_page(0,markup,0)
    assert tool.read_page(0)['rev']==1


def test_page_generator_call_budget_and_fallback_keep_locked_content(tmp_path,monkeypatch):
    import io
    from copy import deepcopy
    from marketing_agent import presentation_pages as pages
    page={'title':'人群策略','section':'人群策略','layout':'comparison','items':[], 'source_ids':['b0']}
    plan={'pages':[deepcopy(page),deepcopy(page)],'assets':{},'theme':{}}
    for key,value in {'MARKETING_MODEL':'test','MARKETING_BASE_URL':'https://example.test','MARKETING_API_KEY':'test'}.items():monkeypatch.setenv(key,value)
    calls=[]
    def respond(*args,**kwargs):
        calls.append(1)
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':'invalid JSON'}}]}).encode())
    monkeypatch.setattr(pages.urllib.request,'urlopen',respond)
    tool=pages.PageTools(tmp_path,plan,model_call_limit=4)
    with pytest.raises(ValueError):tool.generate_page(0)
    with pytest.raises(ValueError,match='预算'):tool.generate_page(1)
    assert len(calls)==4
    original=deepcopy(plan['pages'])
    monkeypatch.setenv('MARKETING_PPT_PAGE_CALL_LIMIT','0')
    result=pages.generate_custom_pages(tmp_path,plan)
    assert result['fallbacks'] and result['model_calls']==0
    assert [p['title'] for p in plan['pages']]==[p['title'] for p in original]
    assert all('custom' not in p for p in plan['pages'])
    assert len(calls)==4


def test_page_roles_cannot_shrink_body_into_caption(tmp_path):
    from marketing_agent.presentation_pages import validate_html,expand_text_refs
    page={'title':'预算说明','subtitle':'预算300万元，预测为假设','section':'说明','items':[]}
    body="<main><h1 data-ref='title' data-role='title'></h1><p data-ref='subtitle' data-role='caption'></p><p data-ref='section' data-role='section'></p></main>"
    expanded=expand_text_refs(body,page)
    validate_html({'body':expanded,'css':''},page,{})
    # Host corrects declared roles; manually downgrading the injected body still fails.
    with pytest.raises(ValueError,match='角色'):
        validate_html({'body':expanded.replace('data-role="body"','data-role="caption"'),'css':''},page,{})


def test_module_master_reuses_geometry_without_old_wording():
    from marketing_agent.presentation_pages import module_contract,select_custom_pages
    first={'section':'人群','layout':'comparison','composition':'audience_bands','title':'第一人群','items':[],
           'custom':{'body':'<h1 data-text>第一人群</h1><p data-text>人群</p>','css':'h1{font-size:46px}'}}
    second={'section':'人群','layout':'comparison','composition':'audience_bands','title':'第二人群','items':[]}
    plan={'pages':[first,second]}
    master=module_contract(plan,1)
    assert master['first_page']==1 and master['id']==module_contract(plan,0)['id']
    assert '第一人群' not in master['reference_html_structure']
    assert '{{title}}' in master['reference_html_structure']
    assert master['reference_css']==first['custom']['css']
    del first['custom']
    assert select_custom_pages(plan)==[0,1]


def test_chart_reads_source_cells_and_rejects_changed_values():
    from marketing_agent.presentation_charts import validate_chart
    import copy
    blocks={'t':{'kind':'table','header':['阶段','预算'],'rows':[['预热','80万'],['爆发','165万'],['收口','55万']]}}
    chart={'type':'column','unit':'万元','categories':['预热','爆发','收口'],'series':[{'name':'预算','source_refs':[{'block_id':'t','row':r,'column':1} for r in range(3)]}]}
    assert validate_chart(chart,blocks,['t'])['series'][0]['values']==[80,165,55]
    bad=copy.deepcopy(chart);bad['series'][0]['values']=[80,180,55]
    with pytest.raises(ValueError,match='不一致'):validate_chart(bad,blocks,['t'])
    with pytest.raises(ValueError,match='来源'):validate_chart(chart,blocks,[])
    bad=copy.deepcopy(chart);bad['series'][0]['source_refs'][0]['row']=10
    with pytest.raises(ValueError,match='越界'):validate_chart(bad,blocks,['t'])
    bad=copy.deepcopy(chart);bad['type']='line'
    with pytest.raises(ValueError,match='顺序'):validate_chart(bad,blocks,['t'])
    bad['ordered']=True
    assert validate_chart(bad,blocks,['t'])['ordered']
    bad=copy.deepcopy(chart);bad['unit']='元'
    with pytest.raises(ValueError,match='单位'):validate_chart(bad,blocks,['t'])
    for value in ['60–120万','约80万','','NaN','80万+']:
        blocks['t']['rows'][0][1]=value
        with pytest.raises(ValueError,match='单值'):validate_chart(chart,blocks,['t'])
    blocks['t']['rows'][0][1]='-80万';chart['type']='donut'
    with pytest.raises(ValueError,match='非负'):validate_chart(chart,blocks,['t'])


def test_local_master_injects_new_words_and_rejects_wrong_shape():
    from marketing_agent.presentation_pages import load_master,validate_html
    page={'composition':'platform_bands','title':'新的平台标题','subtitle':'新的策略副标题','section':'平台',
          'items':[{'label':'新标签'+str(i),'text':'新说明'+str(i)} for i in range(3)]}
    # Derive the exact available slot contract; no original brand wording is reused.
    from marketing_agent.presentation_pages import ROOT
    master=json.loads((ROOT/'skills/marketing-deck/masters/platform_bands.json').read_text())
    page['items']=[]
    for key in master['required_text_keys']:
        if key.startswith('item_'):
            _,number,field=key.split('_')
            while len(page['items'])<=int(number):page['items'].append({})
            page['items'][int(number)][field]='替换'+number+field
    result=load_master(page,{})
    assert result and '新的平台标题' in result['body']
    validate_html(result,page,{})
    page['items'].append({'label':'额外一项'})
    assert load_master(page,{}) is None


def test_images_reuse_before_requests_and_follow_planned_dna(tmp_path,monkeypatch):
    from marketing_agent import presentation_images as images
    assets=tmp_path/'known';assets.mkdir();(assets/'cover.png').write_bytes(b'existing-test-asset')
    job=tmp_path/'job';job.mkdir()
    monkeypatch.setattr(images,'configured',lambda:True)
    plan={'title':'轻芽','assets':{},'pages':[],'theme':{'background':'FFFFFF','text':'123ABC','accent':'FFAA00'}}
    assert images.prepare_assets(plan,'轻芽',job,assets) is False
    monkeypatch.setattr(images,'generate_image',lambda *a:pytest.fail('existing assets must not trigger a generation call'))
    assert images.materialize_assets(plan,job,False)['reused']==['cover']
    plan={'title':'另一品牌','assets':{},'design_mode':'narrative','pages':[{'layout':'cover','asset':'cover'}],
          'theme':{'background':'FFFFFF','text':'123ABC','accent':'FFAA00'}}
    new_job=tmp_path/'new';new_job.mkdir()
    assert images.prepare_assets(plan,'另一品牌',new_job,assets) is True
    prompts=[]
    def generate(prompt,destination,**kwargs):prompts.append(prompt);destination.write_bytes(b'generated-test-asset')
    monkeypatch.setattr(images,'generate_image',generate)
    monkeypatch.setattr(images,'plan_image_tasks',lambda plan,folder,roles:{'cover':{'prompt':'Agent brief #123ABC #FFAA00'}})
    images.materialize_assets(plan,new_job,True)
    assert len(prompts)==1 and '#123ABC' in prompts[0] and '#FFAA00' in prompts[0]
    assert 'Sage green' not in prompts[0]
    # Failure clears both the asset reference and any partial output, without retries.
    failed=tmp_path/'failed';failed.mkdir();plan['pages'][0]['layout']='image'
    def fail(prompt,destination,**kwargs):destination.write_bytes(b'partial');raise RuntimeError('test image unavailable')
    monkeypatch.setattr(images,'generate_image',fail)
    images.materialize_assets(plan,failed,True)
    assert plan['assets']=={} and plan['pages'][0]['layout']=='statement'
    assert not (failed/'cover.png').exists()
