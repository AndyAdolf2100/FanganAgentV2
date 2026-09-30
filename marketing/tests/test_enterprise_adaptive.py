from copy import deepcopy
import json
import pytest

from test_enterprise import fixture
from marketing_agent.enterprise import adaptive, template_html as html
from marketing_agent.enterprise.workflow import plan_pages, render_group, check_all


def test_body_contract_is_derived_without_mutating_template():
    original = fixture()
    page = original['pages'][3]
    page['elements'][0].update(textRole='brand', labelSource='rule', binding='fixed')
    page['elements'].append({'kind':'shape','x':100,'y':200,'width':80,'height':80,'fill':'#223344'})
    before = deepcopy(original)
    working = adaptive.prepare(original)
    assert original == before
    assert working['pages'][0] == original['pages'][0]
    assert working['pages'][3]['elements'][0]['textRole'] == 'title'
    assert len(working['pages'][3]['elements']) == 4
    assert working['_theme']['accent'] in {'#BB3322','#223344'}
    assert working['pages'][3]['elements'][-1]['width'] > 1000


def test_empty_text_shape_content_backdrop_stays_under_body():
    template = fixture()
    backdrop = {'kind':'text','x':0,'y':125,'width':1200,'height':500,'fill':'#FFFFFF',
                'textRole':'decoration','binding':'fixed','paragraphs':[{'runs':[{'text':''}]}]}
    template['pages'][3]['elements'].append(backdrop)
    contract = adaptive.body_contract(template,3)
    assert 4 in contract['keep_elements']


def sample_page():
    template = adaptive.prepare(fixture())
    parts = adaptive.fragments([{'id':'b1','kind':'paragraph','text':'渠道甲投入30万元，渠道乙投入20万元。'},
                               {'id':'b2','kind':'paragraph','text':'保留原稿与企业品牌。'}])
    frame = template['_contracts']['3']['frame']
    page = {'template_page':3,'role':'body','title':'渠道策略','body_parts':parts,
            'layout_spec':adaptive.fallback_spec(parts,frame)}
    return template, page


def test_model_controls_body_geometry_but_cannot_drop_source_or_escape_shell():
    template, page = sample_page()
    frame = template['_contracts']['3']['frame']
    spec = deepcopy(page['layout_spec'])
    spec['items'][0].update(x=0,y=0,width=600,height=200,tone='tint')
    spec['items'][1].update(x=640,y=80,width=400,height=200,tone='plain')
    answer = adaptive.design(template,page,lambda policy,payload: spec)
    page['layout_spec'] = answer
    page['html'] = adaptive.compile_body(template,page)
    assert 'left:640px' in page['html'] and '企业固定品牌' in page['html']
    source = {'blocks':[{'id':p['id'],'text':p['text'],'kind':p['kind']} for p in page['body_parts']], 'agenda':[]}
    html.validate_sources([page['html']],source['blocks'],[])
    wrong = deepcopy(spec); wrong['items'][0]['y'] = -10
    with pytest.raises(ValueError,match='边界'):adaptive.validate_spec(wrong,page['body_parts'],frame)
    wrong = deepcopy(spec); wrong['items'].pop()
    with pytest.raises(ValueError,match='恰好一次'):adaptive.validate_spec(wrong,page['body_parts'],frame)
    page['html'] = page['html'].replace('企业固定品牌','错误品牌')
    with pytest.raises(ValueError,match='固定区域'):check_all(template,[page],source)


def test_chart_reads_original_cells_and_retains_table():
    template = adaptive.prepare(fixture())
    block = {'id':'t1','kind':'table','text':'|渠道|预算（万元）|\n|---|---|\n|甲|30|\n|乙|20|'}
    parts = adaptive.fragments([block]); ref = parts[0]['ref']
    spec = {'items':[{'ref':ref,'x':0,'y':300,'width':1080,'height':130,'font_size':18}],
            'charts':[{'ref':ref,'type':'bar','column':1,'unit':'万元','x':0,'y':0,'width':1080,'height':280}]}
    page = {'template_page':3,'role':'body','title':'渠道预算','body_parts':parts,'layout_spec':spec}
    document = adaptive.compile_body(template,page)
    html.validate_sources([document],[block],[])
    assert page['enterprise_charts'][0]['chart']['series'][0]['values'] == [30,20]
    bad = deepcopy(parts); bad[0]['text'] = bad[0]['text'].replace('|30|','|30–40|')
    with pytest.raises(ValueError,match='单值'):adaptive.chart_spec(spec['charts'][0],bad)
    bad = deepcopy(spec); bad['charts'][0]['ref'] = 'template_sample'
    with pytest.raises(ValueError,match='原稿表格'):adaptive.compile_body(template,{**page,'layout_spec':bad})


def test_long_body_and_tables_preserve_every_cell_across_new_layouts(tmp_path):
    template = adaptive.prepare(fixture())
    manuscript = '# 计划\n\n## 渠道\n\n'+('预算30万元，执行计划不变。'*80)+'\n\n|渠道|预算|\n|---|---|\n'+'\n'.join(f'|渠道{i}|{i}万元|' for i in range(18))
    source = plan_pages(tmp_path,template,manuscript,True)
    pages = []
    for proposal in source['planned']:
        if proposal['role'] != 'body':
            pages.extend({**proposal,'html':d} for d in render_group(template,proposal,source))
            continue
        blocks = [b for b in source['blocks'] if b['id'] in proposal['block_ids']]
        frame = template['_contracts'][str(proposal['template_page'])]['frame']
        for parts in adaptive.paginate(blocks,frame):
            p={**proposal,'body_parts':parts,'layout_spec':adaptive.fallback_spec(parts,frame)}
            p['html']=adaptive.compile_body(template,p); pages.append(p)
    check_all(template,pages,source)
    assert sum(p['role']=='body' for p in pages) > 3


def test_contents_split_follows_logical_sequence_despite_reversed_z_order(tmp_path):
    template = fixture(); p = template['pages'][1]
    first = p['elements'][1]; first.update(width=100,height=90,order=1)
    second = deepcopy(first); second.update(y=320,order=2)
    p['elements'][1:2] = [second,first]
    agenda = [{'id':'a1','text':'一、项目背景与目标及完整业务说明','number':1}]
    proposal = {'template_page':1,'role':'contents','title':'目录','agenda_ids':['a1']}
    documents = render_group(template,proposal,{'blocks':[],'agenda':agenda})
    html.validate_sources(documents,[],agenda)
    assert len(documents)>1
