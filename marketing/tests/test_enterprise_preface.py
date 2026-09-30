from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from test_enterprise import fixture, publish
from marketing_agent.enterprise import model_html as model, template_html as html
from marketing_agent.enterprise.manuscript import parse_markdown, preface_block_ids
from marketing_agent.enterprise.service import TemplateLibrary
from marketing_agent.api import create_app
from marketing_agent.runtime import DemoRuntime


def template_with_preface():
    template=fixture()
    page=deepcopy(template['pages'][3]);page.update(id='preface',role='preface',name='序言页')
    page['elements'][0]['paragraphs'][0]['runs'][0]['text']='序言'
    template['pages'].append(page)
    return template


def test_preface_scope_is_explicit_and_includes_nested_headings():
    blocks=parse_markdown('# 方案\n\n普通开场中提到序言。\n\n## **序言**\n\n说明。\n\n### 适用范围\n\n范围正文。\n\n## 项目背景\n\n正常内容。')
    ids=set(preface_block_ids(blocks))
    assert [b['text'] for b in blocks if b['id'] in ids]==['序言','说明。','适用范围','范围正文。']
    for text in ('# 方案\n\n普通开场。','## 引言\n\n内容。','## 项目序言说明\n\n内容。','## 序言\n\n## 正文\n\n内容。','```\n## 序言\n```'):
        assert not preface_block_ids(parse_markdown(text))


def planner_call(name,policy,payload):
    if name=='enterprise_brief':
        return {'title':'方案','metadata':dict.fromkeys(('presenter','advisor','date'),'AiPPT'),
                'agenda':[{'id':'a1','number':1,'text':'正文'}]}
    assert name=='enterprise_plan_html'
    pick=lambda role:next(t['template_page'] for t in payload['templates'] if t['role']==role)
    ids=payload['dedicated_preface_block_ids'];regular=[b['id'] for b in payload['blocks'] if b['id'] not in ids]
    pages=[]
    if payload['first_batch']:
        cover={'role':'cover','template_page':pick('cover'),'title':'方案'}
        if ids:cover['block_ids']=regular;regular=[]
        pages.append(cover)
    if ids:pages.append({'role':'preface','template_page':pick('preface'),'title':'序言','block_ids':ids})
    else:assert all(t['role']!='preface' for t in payload['templates'])
    if payload['contents_batch'] and any(t['role']=='contents' for t in payload['templates']):
        pages.append({'role':'contents','template_page':pick('contents'),'title':'目录','agenda_ids':['a1']})
    if regular:pages.append({'role':'body','template_page':pick('body'),'title':'正文','block_ids':regular})
    if payload['last_batch']:pages.append({'role':'ending','template_page':pick('ending'),'title':'谢谢'})
    return {'slides':pages}


@pytest.mark.parametrize('has_template,heading,expected',[(True,'序言',True),(True,'项目背景',False),(False,'序言',False)])
def test_planner_optional_preface_routing_and_fallback(tmp_path,has_template,heading,expected):
    template=template_with_preface() if has_template else fixture()
    before=deepcopy(template)
    result=model.plan_deck(tmp_path,template,f'# 方案\n\n## {heading}\n\n预算30万元。\n\n## 正文\n\n渠道20万元。',planner_call)
    roles=[p['role'] for p in result['planned']]
    assert ('preface' in roles)==expected
    if expected:assert roles==['cover','preface','contents','body','ending']
    else:assert roles==['cover','contents','body','ending']
    assert template==before
    assert sorted(i for p in result['planned'] for i in p.get('block_ids',[]))==sorted(b['id'] for b in result['blocks'])


def test_long_preface_batches_finish_before_directory_and_work_without_directory(tmp_path):
    manuscript='# 方案\n\n## 序言\n\n'+('很长的序言说明。'*900)+'\n\n## 正文\n\n正文内容。'
    result=model.plan_deck(tmp_path,template_with_preface(),manuscript,planner_call)
    roles=[p['role'] for p in result['planned']]
    assert roles.count('preface')>=2
    assert roles[0]=='cover' and roles[1:roles.index('contents')]==['preface']*roles.count('preface')
    t=template_with_preface();t['pages'][1]['role']='exclude'
    result=model.plan_deck(tmp_path,t,'# 方案\n\n## 序言\n\n序言文字。\n\n## 正文\n\n正文。',planner_call)
    assert [p['role'] for p in result['planned']]==['cover','preface','body','ending']


def test_rejects_misordered_preface_and_using_it_for_normal_body():
    t=template_with_preface();catalog=model.catalog(t)
    pages=[{'role':'cover','template_page':0,'title':'方案'},
           {'role':'contents','template_page':1,'title':'目录'},
           {'role':'preface','template_page':5,'title':'序言'},
           {'role':'ending','template_page':4,'title':'谢谢'}]
    with pytest.raises(ValueError,match='序言页须'):html.validate_deck(pages,catalog)
    blocks=[{'id':'b1','kind':'paragraph','text':'普通正文'}]
    pages=[{'role':'preface','template_page':5,'title':'序言','block_ids':['b1']}]
    with pytest.raises(ValueError,match='只能使用明确序言'):model.validate_plan({'slides':pages},blocks,catalog,[],False,False)
    with pytest.raises(ValueError,match='只能使用明确序言'):model.validate_plan({'slides':pages},blocks,catalog,[],False,False,['b2'])
    pages=[{'role':'body','template_page':3,'title':'序言','block_ids':['b1']}]
    with pytest.raises(ValueError,match='须放入preface'):model.validate_plan({'slides':pages},blocks,catalog,[],False,False,['b1'])


def test_publish_preface_revision_keeps_previous_snapshot_and_trial_works(tmp_path):
    app=create_app(tmp_path,DemoRuntime());client=TestClient(app);library=TemplateLibrary(tmp_path)
    old=publish(library,fixture());before=library.snapshot(old['id'],1)
    value=template_with_preface();value['published']={'revision':2,'name':value['name'],'pages':deepcopy(value['pages'])};value['reviewAccepted']=True
    response=client.put('/api/enterprise-templates/'+value['id'],json=value)
    assert response.status_code==200,response.text
    assert library.snapshot(value['id'],2)['pages'][-1]['role']=='preface'
    assert library.snapshot(value['id'],1)==before
    assert client.get('/api/enterprise-templates/'+value['id']+'/check').json()['valid']
    response=client.get('/api/enterprise-templates/'+value['id']+'/trial?page=5')
    assert response.status_code==200,response.text
    assert '这是试填正文' in response.text


def test_metadata_and_original_contents_misrouting_gives_actionable_model_feedback(tmp_path):
    from marketing_agent.enterprise.manuscript import parse_markdown
    source='# 方案\n\n客户：轻芽\n日期：2026-09-24\n预算：300万元\n\n## 目录\n\n1. 执行计划\n\n## 执行计划\n\n预算说明。'
    calls=[]
    def call(name,policy,payload):
        if name=='enterprise_brief':return planner_call(name,policy,payload)
        calls.append(deepcopy(payload))
        by={b['id']:b for b in payload['blocks']}
        assert by['b0002']['allowed_page_roles']==['body']
        assert by['b0004']['allowed_page_roles']==['body']
        result=planner_call(name,policy,payload)
        if len(calls)==1:
            body=next(p for p in result['slides'] if p['role']=='body')
            body['block_ids'].remove('b0002')
            result['slides'][0]['block_ids']=['b0002']
        return result
    result=model.plan_deck(tmp_path,fixture(),source,call)
    assert len(calls)==2
    assert 'b0002' in calls[1]['validation_feedback'] and 'cover' in calls[1]['validation_feedback']
    assert calls[1]['previous_plan']['slides'][0]['block_ids']==['b0002']
    assert calls[1]['repair_attempt']==1
    assert next(p for p in result['planned'] if p['role']=='body')['block_ids']==[b['id'] for b in parse_markdown(source)]
    assert 'b0002' in (tmp_path/'planning-corrections.jsonl').read_text()
