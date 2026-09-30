from copy import deepcopy
import pytest
from bs4 import BeautifulSoup
from test_enterprise import fixture
from marketing_agent.enterprise import model_html as m
from marketing_agent.enterprise import uploaded_templates as library


def sample():
    t=fixture();p={'role':'body','template_page':3,'title':'模板示例','block_ids':['b1']}
    ref=library.render(t,t['pages'][3],annotate=True)
    contract={'protected_elements':[2,3],'text_frames':[0],'title_element':0,'body_frame':{'x':60,'y':140,'width':1080,'height':460}}
    soup=BeautifulSoup(ref,'html.parser')
    body=soup.select_one('[data-template-element="1"]')
    body.clear();body['data-enterprise-body']='true'
    body.append(BeautifulSoup('<p data-source-block="b1" data-ppt-slot="source">预算30万元。</p>','html.parser'))
    return t,p,ref,contract,str(soup)


def test_model_html_keeps_arbitrary_model_layout_and_frontend_snapshot():
    t,p,ref,c,doc=sample();snapshot=deepcopy(t)
    assert m.contract_check(c,t,3)==c
    assert m.document_check(doc,ref,c,p,[{'id':'b1','kind':'paragraph','text':'预算30万元。'}],[],{})==doc
    assert t==snapshot
    # No template filler or source injection runs in the HTML validation path.
    assert '<p data-ppt-slot="source" data-source-block="b1">预算30万元。</p>' in doc


def test_model_cannot_delete_brand_or_bypass_resource_constraints():
    t,p,ref,c,doc=sample()
    for changed in (doc.replace('企业固定品牌','偷偷改品牌'),doc.replace('</body>','<script>alert(1)</script></body>'),doc.replace('</body>','<img src="https://example.com/a.png"></body>')):
        with pytest.raises(ValueError):m.document_check(changed,ref,c,p,[],[],{})
    t['pages'][3]['elements'][2]['labelSource']='manual'
    with pytest.raises(ValueError,match='品牌'):m.contract_check({**c,'protected_elements':[3]},t,3)


def test_fixed_pages_preserve_geometry_but_allow_model_typography():
    t=fixture();p={'role':'cover'};ref=library.render(t,t['pages'][0],annotate=True)
    c={'protected_elements':[1,2],'text_frames':[0],'title_element':0,'body_frame':None}
    m.contract_check(c,t,0)
    soup=BeautifulSoup(ref,'html.parser');soup.select_one('[data-template-element="0"] div')['style']='font-size:32px;line-height:1.2'
    for key in ('presenter','advisor','date'):
        n=soup.new_tag('span',attrs={'data-metadata':key});n.string='AiPPT';soup.body.append(n)
    metadata=dict.fromkeys(('presenter','advisor','date'),'AiPPT')
    assert m.document_check(str(soup),ref,c,p,[],[],metadata)==str(soup)
    with pytest.raises(ValueError,match='metadata'):m.document_check(ref,ref,c,p,[],[],metadata)
    with pytest.raises(ValueError,match='固定页'):m.contract_check({**c,'protected_elements':[]},t,0)


def test_real_enterprise_route_does_not_use_rule_renderer(monkeypatch):
    from marketing_agent.enterprise import workflow
    monkeypatch.setenv('MARKETING_RUNTIME','deerflow')
    monkeypatch.setattr(workflow,'execute_demo',lambda *a:pytest.fail('must not call rule renderer'))
    monkeypatch.setattr(m,'execute',lambda *a:'project-model-html')
    assert workflow.execute(None,None,None)=='project-model-html'


def test_protected_artwork_alias_roundtrip_is_lossless():
    from marketing_agent.enterprise import template_html
    t,p,ref,c,doc=sample()
    packed,resources=m.reference_for_model(ref,c)
    assert '__PPT_PROTECTED_2__' in packed and '企业固定品牌' not in packed
    expanded=template_html.unpack_resources(packed,resources)
    assert template_html.tree(BeautifulSoup(expanded,'html.parser'))==template_html.tree(BeautifulSoup(ref,'html.parser'))
    assert m.pack_current(expanded,resources)==packed


def test_protected_feedback_lists_all_damage_and_exact_alias_repair():
    from marketing_agent.enterprise import template_html
    t,p,ref,c,doc=sample()
    reference=BeautifulSoup(ref,'html.parser')
    # A complex decoration may not be recognizable from scalar element metadata.
    reference.select_one('[data-template-element="3"]').append(BeautifulSoup('<svg><path d="M0 0 L20 20"/></svg>','html.parser'))
    ref=str(reference)
    packed,resources=m.reference_for_model(ref,c)
    soup=BeautifulSoup(doc,'html.parser')
    soup.select_one('[data-template-element="2"]').clear()
    soup.select_one('[data-template-element="3"]').clear()
    wrong=str(soup);saved=deepcopy(resources);requests=[];errors=[]
    # A fake project-model response tests feedback transport, never repairs HTML in the validator.
    def call(name,policy,payload):
        requests.append(deepcopy(payload))
        if len(requests)==1:return {'html':wrong}
        feedback=payload['validation_feedback']
        assert '__PPT_PROTECTED_2__' in feedback and '__PPT_PROTECTED_3__' in feedback
        assert 'svg' in feedback and 'path' in feedback and '不是幻灯片页码' in feedback
        assert '不要套进另一个' in feedback
        result=BeautifulSoup(doc,'html.parser')
        for i in c['protected_elements']:
            result.select_one(f'[data-template-element="{i}"]').replace_with(f'__PPT_PROTECTED_{i}__')
        return {'html':str(result)}
    def validate(answer):
        document=template_html.unpack_resources(answer['html'],resources)
        return m.document_check(document,ref,c,p,[],[],{})
    result=m.repair_model(call,'enterprise_full_html','policy',{'reference_html':packed},validate,errors.append)
    assert len(requests)==2 and requests[1]['previous_result']=={'html':wrong}
    assert resources==saved
    result=BeautifulSoup(result,'html.parser')
    assert template_html.tree(result.select_one('[data-template-element="3"]'))==template_html.tree(reference.select_one('[data-template-element="3"]'))


@pytest.mark.parametrize('duplicate',[False,True])
def test_missing_or_duplicate_protected_nodes_have_actionable_feedback(duplicate):
    t,p,ref,c,doc=sample();soup=BeautifulSoup(doc,'html.parser')
    node=soup.select_one('[data-template-element="2"]')
    if duplicate:soup.body.append(deepcopy(node))
    else:node.decompose()
    with pytest.raises(ValueError,match='__PPT_PROTECTED_2__') as error:
        m.document_check(str(soup),ref,c,p,[],[],{})
    assert ('实际2' if duplicate else '实际0') in str(error.value)


@pytest.mark.parametrize("malformed_first",[False,True])
def test_full_model_workflow_uses_shared_visual_director_and_repairs_cover(tmp_path,monkeypatch,malformed_first):
    import json
    from marketing_agent.presentation_agent import PresentationAgent
    from marketing_agent.enterprise import template_html
    from marketing_agent import presentation_agent, presentation_vision
    template=fixture();folder=tmp_path/'job';folder.mkdir()
    (folder/'template.json').write_text(json.dumps(template))
    (folder/'manuscript.md').write_text('# 计划\n\n预算30万元。')
    source={'title':'计划','metadata':dict.fromkeys(('presenter','advisor','date'),'AiPPT'),
        'agenda':[{'id':'a1','number':1,'text':'预算'}],
        'blocks':[{'id':'b1','kind':'paragraph','text':'预算30万元。'}],
        'planned':[{'role':'cover','template_page':0,'title':'计划'},
            {'role':'body','template_page':3,'title':'预算','block_ids':['b1']},
            {'role':'ending','template_page':4,'title':'谢谢'}]}
    class Jobs:
        root=tmp_path
        data={'options':{'template_revision':1},'run_id':'test','source_sha256':'fixture','error':'previous interrupted attempt'}
        def get(self,_):return self.data
        def update(self,_,**kw):self.data.update(kw)
    jobs=Jobs();calls=[]
    monkeypatch.setenv('MARKETING_VISION_ENABLED','true')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','5')
    monkeypatch.setattr(m,'plan_deck',lambda *args:deepcopy(source))
    monkeypatch.setattr(presentation_agent,'analyze_reference',lambda *a:{'status':'analyzed'})
    def model(folder,name,policy,payload):
        calls.append(name)
        if malformed_first and name=='enterprise_template_contract' and calls.count(name)==1:
            raise json.JSONDecodeError('fixture invalid JSON','{',1)
        if name=='enterprise_theme':return {'accent':'#BB3322','text':'#223344','palette':['#BB3322'],'font':'Noto Sans CJK SC'}
        if name=='enterprise_template_contract':
            return {'protected_elements':[2,3] if payload['role']=='body' else [1,2],
                'text_frames':[0], 'title_element':0, 'body_frame':{'x':60,'y':140,'width':1080,'height':460} if payload['role']=='body' else None}
        assert name=='enterprise_full_html'
        p=payload['proposal'];ref=payload['reference_html'];soup=BeautifulSoup(ref,'html.parser')
        title=soup.select_one('[data-template-element="0"] div');title.clear();title.string=p['title']
        if p['role']=='body':
            body=soup.select_one('[data-template-element="1"]');body.clear();body['data-enterprise-body']='true'
            body.append(BeautifulSoup('<p data-source-block="b1">预算30万元。</p>','html.parser'))
        else:
            for key in source['metadata']:
                n=soup.new_tag('span',attrs={'data-metadata':key});n.string='AiPPT';soup.body.append(n)
        return {'pages':[{'html':str(soup),'charts':[]}],'reason':'model fixture'}
    monkeypatch.setattr(m,'model_json',model)
    def render(path,probe=False):
        plan=json.loads((path/'plan.json').read_text())
        if not probe:(path/'report.json').write_text(json.dumps({'checks':{}}))
        return {'pages':[{'page':i+1,'issues':[]} for i in range(len(plan['pages']))]}
    monkeypatch.setattr(m,'run_renderer',render)
    def review(folder,plan,indexes):
        return [{'page':i,'verdict':'fix' if i==1 and len(indexes)>1 else 'pass',
                 'issues':[{'severity':'medium','detail':'信息栏需调整'}] if i==1 and len(indexes)>1 else []} for i in indexes]
    monkeypatch.setattr(presentation_vision,'review_batch',review)
    def decision(folder,plan,findings,history,limit):
        return {'actions':[{'page':1,'brief':'调整信息栏'}] if not history else []}
    monkeypatch.setattr(presentation_agent,'choose_repairs',decision)
    m.execute(jobs,'job',PresentationAgent(folder))
    assert jobs.data['status']=='completed' and jobs.data['visual_status']=='passed'
    assert jobs.data['error'] is None
    assert calls.count('enterprise_full_html')==4  # three pages plus cover repair
    plan=json.loads((folder/'plan.json').read_text())
    assert all(p['model_html'] and p['design_status']=='model_full_html' for p in plan['pages'])
    assert json.loads((folder/'visual-review.json').read_text())['repairs']==[{'pages':[1],'status':'verified'}]
    assert json.loads((folder/'template.json').read_text())==template
    # A restart reuses complete model-authored pages, without new HTML calls.
    before=json.loads((folder/'plan.json').read_text())['pages']
    html_calls=calls.count('enterprise_full_html')
    monkeypatch.setenv('MARKETING_VISION_ENABLED','false')
    m.execute(jobs,'job',PresentationAgent(folder))
    assert calls.count('enterprise_full_html')==html_calls
    assert json.loads((folder/'plan.json').read_text())['pages']==before
    assert json.loads((folder/'agent-run.json').read_text())['resumed_pages']==3


def test_resume_validates_source_and_page_prefix_without_mutation():
    t,p,ref,c,doc=sample();p={**p,'generation_group':0}
    blocks=[{'id':'b1','kind':'paragraph','text':'预算30万元。'}]
    source={'blocks':blocks,'agenda':[],'metadata':{}}
    page={**p,'model_html':True,'html':doc,'template_contract':c}
    plan={'enterprise_layout_mode':m.MODE,'canvas':{'width':1200,'height':675},'source_sha256':'same','source_blocks':blocks,'template_id':'t','template_revision':1,'theme':{}}
    saved={**plan,'pages':[page]};snapshot=deepcopy(saved)
    following={**p,'generation_group':1}
    assert m.resume_prefix(saved,plan,[p,following],{3:ref},{'3':c},source)==[[page]]
    assert saved==snapshot
    with pytest.raises(ValueError,match='source_sha256'):
        m.resume_prefix(saved,{**plan,'source_sha256':'changed'},[p],{3:ref},{'3':c},source)
    with pytest.raises(ValueError,match='不连续'):
        m.resume_prefix({**saved,'pages':[{**page,'generation_group':1}]},plan,[p,following],{3:ref},{'3':c},source)
    with pytest.raises(ValueError,match='品牌'):
        m.resume_prefix({**saved,'pages':[{**page,'html':doc.replace('企业固定品牌','改动品牌')}]},plan,[p],{3:ref},{'3':c},source)


def test_model_contract_cannot_lock_the_same_body_area_it_declares_free():
    t,p,ref,c,doc=sample()
    with pytest.raises(ValueError,match='正文自由区'):
        m.contract_check({**c,'text_frames':[0,1]},t,3)


def test_unused_directory_text_frame_can_be_omitted_without_changing_artwork():
    t=fixture();ref=library.render(t,t['pages'][1],annotate=True)
    c={'protected_elements':[2,3],'text_frames':[0,1],'body_frame':None}
    soup=BeautifulSoup(ref,'html.parser');soup.select_one('[data-template-element="1"]').decompose()
    assert m.document_check(str(soup),ref,c,{'role':'contents','title':'模板示例'},[],[],{})==str(soup)


def test_planner_can_bind_source_heading_to_cover_without_host_reassignment():
    blocks=[{'id':'b1','kind':'heading','text':'计划'},{'id':'b2','kind':'paragraph','text':'预算30万元。'}]
    pages=[{'role':'cover','template_page':0,'title':'计划','block_ids':['b1']},
        {'role':'contents','template_page':1,'title':'目录','agenda_ids':['a1']},
        {'role':'body','template_page':3,'title':'预算','block_ids':['b2']},
        {'role':'ending','template_page':4,'title':'谢谢'}]
    saved=deepcopy(pages);agenda=[{'id':'a1','number':1,'text':'预算'}]
    assert m.validate_plan({'slides':pages},blocks,m.catalog(fixture()),agenda,True,True)==saved
    assert pages==saved
    pages[0].pop('block_ids')
    with pytest.raises(ValueError,match='b1'):m.validate_plan({'slides':pages},blocks,m.catalog(fixture()),agenda,True,True)


def test_normal_web_ppt_keeps_original_entry_and_pipeline_version(tmp_path,monkeypatch):
    from marketing_agent.api import create_app
    from marketing_agent.runtime import DemoRuntime
    from marketing_agent import presentation
    from marketing_agent.enterprise import workflow
    app=create_app(tmp_path,DemoRuntime());jobs=app.state.presentations
    monkeypatch.setattr(jobs.pool,'submit',lambda *a:None)
    monkeypatch.setattr(workflow,'execute',lambda *a:pytest.fail('normal PPT must not enter enterprise workflow'))
    reached=[]
    def original_entry(source):
        reached.append(source)
        raise RuntimeError('test-stops-at-normal-entry')
    monkeypatch.setattr(presentation,'plan_outline',original_entry)
    run={'id':'a'*32,'status':'completed','runtime':'demo','outputs':{'assembly':'# 普通方案\n\n预算30万元。'}}
    job=jobs.create(run)
    assert job['pipeline_version']==presentation.PIPELINE_VERSION=='3.0.0'
    assert not (jobs.root/job['id']/'template.json').exists()
    jobs.execute(job['id'])
    assert reached==[run['outputs']['assembly']]
    assert jobs.get(job['id'])['error']=='test-stops-at-normal-entry'


def test_enterprise_skill_reuses_design_principles_without_normal_output_protocol():
    # The normal file is read-only and keeps its own data-ref/body-css contract.
    assert 'text_catalog' in m.NORMAL_DESIGN.read_text()
    policy=m.skill_text()
    assert '一页只一个视觉焦点' in policy
    assert '不要抄写原文，使用空元素引用' not in policy
    assert 'body不含html/head/body外层标签' not in policy
    assert '完整 HTML' in policy


def test_contract_retry_explains_frame_lock_and_does_not_reuse_identical_request():
    t,p,ref,c,doc=sample();before=deepcopy(t);requests=[];errors=[]
    wrong={**c,'text_frames':[0,1]}
    def call(name,policy,payload):
        requests.append(deepcopy(payload))
        return deepcopy(wrong if len(requests)<3 else c)
    result=m.analyze_contract(t,3,ref,{'status':'not_reviewed'},call,errors.append)
    assert result==c and t==before
    assert len(errors)==2 and len(requests)==3
    assert requests[1]['previous_contract']==wrong
    assert requests[1]['repair_attempt']==1 and requests[2]['repair_attempt']==2
    feedback=requests[1]['validation_feedback']
    assert '"element": 1' in feedback and 'element_bounds' in feedback
    assert '锁定位置和尺寸' in feedback and '同时移出text_frames和protected_elements' in feedback


@pytest.mark.parametrize('succeeds',[True,False])
def test_model_self_correction_has_three_repairs_shared_by_json_and_content(succeeds):
    import json
    requests=[];errors=[];original={'blocks':['source']}
    def call(name,policy,payload):
        requests.append(deepcopy(payload))
        if len(requests)==1:raise json.JSONDecodeError('missing comma','{"broken":',9)
        return {'valid':succeeds and len(requests)==4}
    def validate(value):
        if not value['valid']:raise ValueError('固定区域与正文冲突')
        return value
    if succeeds:
        assert m.repair_model(call,'test','policy',original,validate,errors.append)=={'valid':True}
    else:
        with pytest.raises(ValueError,match='自我纠错3轮后仍未通过'):
            m.repair_model(call,'test','policy',original,validate,errors.append)
    assert len(requests)==4
    assert [p['repair_attempt'] for p in requests]==[0,1,2,3]
    assert requests[1]['previous_result']=='{"broken":'
    assert requests[2]['previous_result']=={'valid':False}
    assert '固定区域与正文冲突' in requests[3]['validation_feedback']
    assert errors[-1]['will_retry']==succeeds
    assert original=={'blocks':['source']}


def test_budget_exhaustion_does_not_enter_model_self_correction():
    errors=[];calls=[]
    def call(*args):
        calls.append(args);raise m.ModelCallLimitError('累计调用预算用尽')
    with pytest.raises(m.ModelCallLimitError):
        m.repair_model(call,'test','policy',{},lambda x:x,errors.append)
    assert len(calls)==1 and not errors


def test_browser_feedback_keeps_bounds_but_excludes_render_snapshots():
    issue={'type':'template_geometry_changed','element':2,
           'expected_bounds':{'x':31.65,'y':13.32,'width':428.125,'height':60.578},
           'actual_bounds':{'x':31.65,'y':13.32,'width':560,'height':60.578}}
    probe={'pages':[{'page':47,'issues':[issue],'elements':{'backgroundImage':'data:image/png;base64,large-image'}},
                    {'page':48,'issues':[],'elements':{}}]}
    before=deepcopy(probe)
    assert m.browser_findings(probe)==[{'page':47,'issues':[issue]}]
    assert probe==before
