from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from marketing_agent.api import create_app
from marketing_agent.runtime import DemoRuntime
from marketing_agent.enterprise import template_html as html
from marketing_agent.enterprise.service import TemplateLibrary
from marketing_agent.enterprise.workflow import plan_pages, render_group, check_all, repair_text_regions


def fixture():
    def text(role, y, h=80, value='模板示例', size=28):
        return {'id': role, 'kind': 'text', 'binding': 'fixed' if role=='brand' else 'content',
                'textRole': role, 'order': 1, 'x': 64, 'y': y, 'width': 1072, 'height': h,
                'paragraphs': [{'align': 'left', 'runs': [{'text': value, 'size': size,
                'font': 'Noto Sans CJK SC', 'color': '#223344', 'bold': False}]}]}
    pages=[]
    for role in ['cover','contents','section','body','ending']:
        elements=[text('title',40,value='谢谢' if role=='ending' else '模板示例')]
        if role=='body': elements.append(text('body',150,420))
        if role=='contents': elements.append(text('contentsItem',150,420))
        if role=='section': elements.append(text('sectionNumber',140))
        elements.append(text('brand',610,40,'企业固定品牌',18))
        elements.append({'kind':'shape','binding':'fixed','x':0,'y':0,'width':1200,'height':12,'fill':'#BB3322'})
        pages.append({'id':role,'name':role,'role':role,'background':'#FFFFFF','elements':elements})
    return {'id':'a'*64,'version':1,'name':'测试企业模板','createdAt':1,'width':1200,'height':675,
            'originalWidth':1200,'originalHeight':675,'aspectRatio':'16:9','pages':pages,'assets':[],
            'fonts':['Noto Sans CJK SC'],'colors':['#223344'],'warnings':[]}


def publish(library, template):
    template=deepcopy(template)
    template['published']={'revision':1,'name':template['name'],'pages':deepcopy(template['pages'])}
    template['reviewAccepted']=True
    return library.save(template)


def test_template_versions_and_drafts_are_isolated(tmp_path):
    library=TemplateLibrary(tmp_path)
    value=publish(library,fixture())
    old=library.snapshot(value['id'],1)
    value['pages'][0]['elements'][0]['x']=80
    library.save(value)
    assert library.snapshot(value['id'],1)==old
    value['published'].update(revision=2,pages=deepcopy(value['pages']))
    library.save(value)
    assert library.snapshot(value['id'],2)['pages'][0]['elements'][0]['x']==80
    assert library.snapshot(value['id'],1)==old
    library.remove(value['id'])
    assert library.snapshot(value['id'],1)==old


def test_publish_requires_review_and_16_by_9(tmp_path):
    library=TemplateLibrary(tmp_path)
    value=fixture();value['height']=900
    with pytest.raises(ValueError,match='16:9'):library.save(value)
    value=fixture();value['published']={'revision':1,'name':value['name'],'pages':value['pages']}
    with pytest.raises(ValueError,match='核对'):library.save(value)
    value['reviewAccepted']=True;value['pages'][3]['elements'][1]['textRole']='title'
    with pytest.raises(ValueError):library.save(value)


def test_api_template_job_snapshot_and_cache(tmp_path,monkeypatch):
    app=create_app(tmp_path,DemoRuntime())
    monkeypatch.setattr(app.state.presentations.pool,'submit',lambda *a:None)
    with TestClient(app) as client:
        value=publish(app.state.presentations.templates,fixture())
        assert client.get('/api/presentation-capabilities').json()['enterprise_templates']
        assert client.get('/api/enterprise-templates').json()['templates'][0]['id']==value['id']
        assert client.get('/api/enterprise-templates/'+value['id']+'/check').json()['valid']
        assert client.get('/api/enterprise-templates/'+value['id']+'/trial?page=3').status_code==200
        # There is deliberately no server labeling or PPTX upload endpoint.
        assert client.post('/api/enterprise-templates/'+value['id']+'/labels').status_code==404
        run=client.post('/api/runs/import',json={'title':'方案','manuscript':'# 方案\n\n正文原文。'}).json()
        url=f"/api/runs/{run['id']}/presentation"
        body={'template_id':value['id'],'template_revision':1}
        job=client.post(url,json=body).json()
        snapshot=app.state.presentations.root/job['id']/'template.json'
        assert snapshot.exists()
        assert client.post(url,json=body).json()['id']==job['id']
        assert client.post(url,json={**body,'palette':['#112233']}).status_code==422
        assert client.post(url,json={'template_id':value['id']}).status_code==422
        assert client.post(url,json={**body,'template_revision':99}).status_code==409


def test_lossless_long_text_and_fixed_shell(tmp_path):
    template=fixture()
    source=plan_pages(tmp_path,template,'# 年度计划\n\n## 预算\n\n'+('预算30万元，保持原文。'*200),True)
    pages=[]
    for p in source['planned']:
        pages.extend({**p,'html':d} for d in render_group(template,p,source,page_number=len(pages)+1))
    assert len(pages)>4
    check_all(template,pages,source)
    assert all('企业固定品牌' in p['html'] for p in pages)
    changed=pages[0]['html'].replace('企业固定品牌','偷偷换品牌')
    with pytest.raises(ValueError):html.validate_html(changed,html.template_html(template,pages[0]['template_page']))


def test_model_planning_contract_and_fallback(tmp_path,monkeypatch):
    calls=[]
    def response(folder,name,policy,payload):
        calls.append(payload)
        return {'slides':[{'role':'cover','template_page':0,'title':'计划'},
          {'role':'contents','template_page':1,'title':'目录','agenda_ids':['a1']},
          {'role':'body','template_page':3,'title':'预算','block_ids':[b['id'] for b in payload['blocks']]},
          {'role':'ending','template_page':4,'title':'谢谢'}]}
    monkeypatch.setattr('marketing_agent.enterprise.workflow.model_json',response)
    source=plan_pages(tmp_path,fixture(),'# 计划\n\n## 预算\n\n预算30万元。')
    assert len(calls)==1 and not source['corrections']
    monkeypatch.setattr('marketing_agent.enterprise.workflow.model_json',lambda *a:{'slides':[]})
    source=plan_pages(tmp_path,fixture(),'# 计划\n\n正文。')
    assert source['corrections'] and source['planned'][1]['role']=='contents'


def test_visual_patch_cannot_change_copy_or_fixed_fields(tmp_path,monkeypatch):
    template=fixture();source=plan_pages(tmp_path,template,'# 计划\n\n预算30万元。',True)
    p=next(p for p in source['planned'] if p['role']=='body')
    p={**p,'html':render_group(template,p,source)[0]}
    for patch in [{'regions':{'999':'改固定区域'}},{'regions':{'1':'删除全部原文'}}]:
        monkeypatch.setattr('marketing_agent.enterprise.workflow.model_json',lambda *a:patch)
        with pytest.raises(ValueError):repair_text_regions(tmp_path,template,p,{'page':1})


def test_table_values_preserved(tmp_path):
    template=fixture();source=plan_pages(tmp_path,template,'# 数据\n\n|渠道|预算|\n|---|---|\n|甲|30万元|\n|乙|20万元|',True)
    pages=[{**p,'html':d} for p in source['planned'] for d in render_group(template,p,source)]
    check_all(template,pages,source)


@pytest.mark.parametrize('invalid_patch',[False,True])
def test_workflow_visual_repair_verifies_or_rolls_back(tmp_path,monkeypatch,invalid_patch):
    from marketing_agent.enterprise import workflow
    from marketing_agent.presentation_agent import PresentationAgent
    app=create_app(tmp_path,DemoRuntime());jobs=app.state.presentations
    monkeypatch.setattr(jobs.pool,'submit',lambda *a:None)
    value=publish(jobs.templates,fixture())
    with TestClient(app) as client:
        run=client.post('/api/runs/import',json={'title':'计划','manuscript':'# 计划\n\n预算30万元。'}).json()
        job=client.post(f"/api/runs/{run['id']}/presentation",json={'template_id':value['id'],'template_revision':1}).json()
        folder=jobs.root/job['id']
        original_planner=workflow.plan_pages
        monkeypatch.setattr(workflow,'plan_pages',lambda f,t,m,d:original_planner(f,t,m,True))
        monkeypatch.setenv('MARKETING_RUNTIME','deerflow');monkeypatch.setenv('MARKETING_VISION_ENABLED','true')
        def render(folder,probe=False):
            plan=json.loads((folder/'plan.json').read_text())
            report={'pages':[{'page':i+1,'issues':[]} for i in range(len(plan['pages']))]}
            if not probe:(folder/'report.json').write_text(json.dumps({'checks':{},'passed':True}))
            return report
        monkeypatch.setattr(workflow,'run_renderer',render)
        def review(folder,plan,indexes):
            return [{'page':i,'verdict':'fix' if len(indexes)>1 and plan['pages'][i-1]['role']=='body' else 'pass',
                     'issues':[{'severity':'medium','detail':'正文行距'}] if len(indexes)>1 and plan['pages'][i-1]['role']=='body' else []} for i in indexes]
        monkeypatch.setattr('marketing_agent.presentation_vision.review_batch',review)
        def repair(folder,template,page,finding):
            return page['html'].replace('企业固定品牌','越权改品牌') if invalid_patch else page['html']
        monkeypatch.setattr(workflow,'repair_text_regions',repair)
        # This test exercises the retained legacy/demo repair implementation.
        # Never dispatch a live model when testing its deterministic rollback.
        from marketing_agent.enterprise import adaptive
        monkeypatch.setattr(adaptive,'design',lambda t,p,*a:adaptive.fallback_spec(p['body_parts'],t['_contracts'][str(p['template_page'])]['frame']))
        workflow.execute_demo(jobs,job['id'],PresentationAgent(folder))
        result=jobs.get(job['id']);assert result['status']=='completed'
        visual=json.loads((folder/'visual-review.json').read_text())
        assert visual['repairs'][0]['status']==('rolled_back' if invalid_patch else 'verified')
        assert visual['status']==('needs_review' if invalid_patch else 'passed')
        assert '越权改品牌' not in (folder/'plan.json').read_text()
