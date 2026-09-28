import json
from copy import deepcopy

from marketing_agent.presentation import plan_outline
from marketing_agent.presentation_design import validate_design
from marketing_agent.presentation_editorial import apply_edits
from marketing_agent.presentation_images import prepare_assets
from marketing_agent.presentation_pages import expand_text_refs, validate_html, load_master, reuse_prototype


def test_host_assigns_roles_and_adaptive_master_handles_four_items():
    page={'layout':'columns','composition':'audience_bands','title':'受众分层','subtitle':'从核心走向增量','section':'人群策略',
          'items':[{'label':f'人群{n}','text':'以对应场景承接需求','value':f'{n:02d}'} for n in range(1,5)]}
    markup=load_master(page,{})
    assert markup['master']=='adaptive-audience_bands'
    checked=validate_html(markup,page,{})
    assert '人群4' in checked['body']
    fixed=expand_text_refs("<p data-ref='item_0_label' data-role='body'></p>",page)
    assert 'data-role="label"' in fixed and "data-role='body'" not in fixed


def test_project_assets_are_source_bound_and_names_are_not_tea_specific(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_IMAGE_ENABLED','false')
    source=plan_outline('# 汽车方案\n\n家庭周末场景。')
    assets=tmp_path/'assets';collection=assets/'collections'/source['source_sha256'];collection.mkdir(parents=True)
    (collection/'scene.png').write_bytes(b'image-fixture')
    (collection/'manifest.json').write_text(json.dumps({'assets':{'home':{'file':'scene.png','description':'家庭场景'},'bad':{'file':'../secret.png'}}}))
    folder=tmp_path/'job';folder.mkdir()
    assert prepare_assets(source,'家庭周末场景',folder,assets) is False
    assert source['assets']=={'home':'home.png'}
    assert source['asset_descriptions']['home']=='家庭场景'
    other=plan_outline('# 茶饮方案\n\n其他产品。')
    prepare_assets(other,'其他产品',folder,assets)
    assert other['assets']=={} and '没有匹配配图' in other['image_notice']


def test_editorial_preserves_metrics_rejects_new_numbers_and_keeps_full_notes():
    source=plan_outline('# 方案\n\n预算300万元。\n\n## 目标\n预计60万–120万，假设值。\n\n## 原文\n完整的背景说明。')
    design={'pages':[{'layout':'cover','title':'项目提案','source_ids':['b0000'],'items':[]},
                     {'layout':'metrics','title':'预估目标','source_ids':['b0001'],'items':[{'label':'目标','value':'60万–120万','text':'预计，假设值'}]},
                     {'layout':'closing','title':'下一步','source_ids':['b0002'],'items':[]}]}
    design=validate_design(design,source,compatible=True)
    edits=deepcopy({'pages':[{'index':i+1,**{k:v for k,v in p.items() if k in ('title','layout','items')}} for i,p in enumerate(design['pages'])]})
    edits['pages'][0]['title']='预算999万元'
    edits['pages'][1]['items'][0]['value']='120万'
    original=deepcopy(design)
    result=apply_edits(design,edits,source)
    assert result['pages'][0]['title']=='项目提案'
    assert result['pages'][1]['items'][0]['value']=='60万–120万'
    assert design==original
    assert {b['id'] for p in result['pages'] for b in p['blocks']}=={b['id'] for b in source['source_blocks']}


def test_agent_prototype_rebinds_copy_and_is_scoped_to_module():
    first={'layout':'columns','composition':'audience_bands','title':'旧页面','section':'目标人群',
           'items':[{'label':'旧甲','text':'旧描述甲'},{'label':'旧乙','text':'旧描述乙'}]}
    first['custom']=load_master(first,{})
    next_page=deepcopy(first);next_page.pop('custom');next_page['title']='新页面'
    next_page['items']=[{'label':'新甲','text':'新描述甲'},{'label':'新乙','text':'新描述乙'}]
    plan={'pages':[first,next_page]}
    prototype=reuse_prototype(plan,1)
    validate_html(prototype,next_page,{})
    assert '新描述甲' in prototype['body'] and '旧描述' not in prototype['body']
    next_page['section']='其他模块'
    assert reuse_prototype(plan,1) is None


def test_editorial_equivalent_date_keeps_compact_closing_but_rejects_changed_date():
    source=plan_outline('# 方案\n\n9.1悬念片首发启动战役。')
    design=validate_design({'pages':[{'layout':'cover','title':'方案','source_ids':['b0000'],'items':[]},
        {'layout':'statement','title':'计划','source_ids':['b0000'],'items':[]},
        {'layout':'closing','title':'下一步','source_ids':['b0000'],
        'items':[{'label':'启动','text':'9.1悬念片首发启动战役','value':''}]}]},source,compatible=True)
    for date,accepted in [('9月1日',True),('9月2日',False)]:
        edit={'index':3,'layout':'closing','title':'下一步','items':[{'label':'启动','text':date+'悬念片首发，即刻启动','value':''}]}
        result=apply_edits(design,{'pages':[{'index':i+1,**deepcopy(p)} for i,p in enumerate(design['pages'][:2])]+[edit]},source)
        assert (result['pages'][2]['items'][0]['text']==edit['items'][0]['text']) is accepted


def test_image_pages_receive_custom_design_priority():
    from marketing_agent.presentation_pages import select_custom_pages
    plan={'style_id':'brand_launch','pages':[{'layout':'columns','composition':'message_house'},
          {'layout':'image','asset':'scene1'},{'layout':'image','asset':'scene2'}]}
    assert select_custom_pages(plan,3)==[1,2,0]


def test_self_closing_image_is_valid_html_and_does_not_pop_text_container():
    page={'title':'场景主张','section':'场景','items':[],'asset':'scene1'}
    body="<main><img src='asset:scene1' /><h1 data-ref='title'></h1><p data-ref='section'></p></main>"
    markup={'body':expand_text_refs(body,page),'css':'main{width:1280px;height:720px}'}
    assert validate_html(markup,page,{'scene1':'scene1.png'})
