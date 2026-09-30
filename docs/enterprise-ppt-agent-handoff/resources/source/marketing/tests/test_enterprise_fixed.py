from copy import deepcopy
import re
from bs4 import BeautifulSoup

from test_enterprise import fixture
from marketing_agent.enterprise import fixed, template_html as html
from marketing_agent.enterprise.manuscript import parse_markdown, plain_text
from marketing_agent.enterprise.workflow import plan_pages, render_group


def test_markdown_becomes_display_text_without_changing_math_or_identifiers():
    value='> 客户：**轻芽**\n> > 这是**重点**说明\n\n- [x] 完成 `API`\n- 使用 [平台](https://example.com)\n\n成本 2 * 3 * 4，foo_bar，预算30万元。'
    blocks=parse_markdown(value)
    display='\n'.join(b['text'] for b in blocks)
    assert not re.search(r'(?m)^\s*>|\*\*|`|\[[ x]\]|\]\(',display)
    assert all(s in display for s in ['轻芽','这是重点说明','☑ 完成 API','https://example.com','2 * 3 * 4','foo_bar','预算30万元'])
    code=parse_markdown('```python\nprint(2 * 3)\n```')[0]
    assert code['kind']=='code' and code['text']=='print(2 * 3)'


def test_cover_and_ending_fill_metadata_with_aippt_defaults():
    original=fixture()
    for pi in [0,4]:
        e=deepcopy(original['pages'][pi]['elements'][0])
        e.update(textRole='subtitle',y=400,width=420,height=50,order=2)
        e['paragraphs'][0]['runs'][0]['text']='汇报：旧姓名  导师：旧老师'
        original['pages'][pi]['elements'].append(e)
    snapshot=deepcopy(original)
    meta=fixed.metadata('> 汇报人：张三\n> 日期：2026-09-29\n\n正文业务时间2027年。')
    assert meta=={'presenter':'张三','advisor':'AiPPT','date':'2026-09-29'}
    template=fixed.prepare(original,meta)
    assert original==snapshot
    for pi,role in [(0,'cover'),(4,'ending')]:
        proposal={'template_page':pi,'role':role,'title':'计划' if pi==0 else '谢谢'}
        doc=render_group(template,proposal,{'blocks':[],'agenda':[]})[0]
        text=BeautifulSoup(doc,'html.parser').get_text()
        assert all(s in text for s in ['汇报：张三','导师：AiPPT','时间：2026-09-29'])
        assert '旧姓名' not in text and '旧老师' not in text
    assert fixed.metadata('没有提供元信息')==dict(presenter='AiPPT',advisor='AiPPT',date='AiPPT')


def test_section_number_uses_authored_digit_width():
    template=fixture()
    e=template['pages'][2]['elements'][1]
    e['paragraphs'][0]['runs'][0]['text']='1'
    doc=render_group(template,{'template_page':2,'role':'section','title':'计划','section_number':2},{'blocks':[],'agenda':[]})[0]
    assert BeautifulSoup(doc,'html.parser').select_one('[data-ppt-editable][data-ppt-role="sectionNumber"]').get_text()=='2'
    e['paragraphs'][0]['runs'][0]['text']='01'
    assert fixed.number(e,2)=='02'


def test_semantic_directory_keeps_whole_title_and_correct_pairs(tmp_path):
    template=fixture();p=template['pages'][1]
    title=p['elements'][1];title.update(width=185,height=60,textRole='contentsSubtitle')
    title['paragraphs'][0]['runs'][0].update(size=40,text='第一部分')
    description=deepcopy(title);description.update(y=230,width=700,height=70,textRole='contentsItem')
    description['paragraphs'][0]['runs'][0].update(size=24,text='请添加内容')
    number=deepcopy(title);number.update(x=0,y=150,width=64,textRole='contentsNumber')
    number['paragraphs'][0]['runs'][0].update(size=40,text='01')
    p['elements'] += [description,number]
    template=fixed.prepare(template,fixed.metadata(''))
    agenda=[{'id':'a1','text':'一、项目背景与目标','display_title':'项目背景','number':1}]
    docs=render_group(template,{'template_page':1,'role':'contents','title':'目录','agenda_ids':['a1']},{'blocks':[],'agenda':agenda})
    assert len(docs)==1
    soup=BeautifulSoup(docs[0],'html.parser')
    assert soup.select_one('[data-ppt-editable][data-ppt-role="contentsItem"]').get_text()=='项目背景'
    assert soup.select_one('[data-ppt-editable][data-ppt-role="contentsNumber"]').get_text()=='01'
    html.validate_sources(docs,[],agenda)


def test_directory_excludes_directory_heading_and_uses_body_topics_for_short_manuscript(tmp_path,monkeypatch):
    source=plan_pages(tmp_path,fixture(),'# 计划\n\n## 目录\n\n一、预算\n\n## 一、预算\n\n预算30万元。',True)
    assert [a['text'] for a in source['agenda']]==['一、预算']
    def model(folder,name,policy,payload):
        if name=='enterprise_agenda_labels':return {'labels':{'a1':'背景','a2':'预算'}}
        return {'slides':[{'template_page':0,'role':'cover','title':'计划'},
            {'template_page':1,'role':'contents','title':'项目目录','agenda_ids':['a1']},
            {'template_page':3,'role':'body','title':'项目背景','block_ids':[b['id'] for b in payload['blocks'][:2]]},
            {'template_page':3,'role':'body','title':'预算安排','block_ids':[b['id'] for b in payload['blocks'][2:]]},
            {'template_page':4,'role':'ending','title':'谢谢'}]}
    monkeypatch.setattr('marketing_agent.enterprise.workflow.model_json',model)
    source=plan_pages(tmp_path,fixture(),'# 计划\n\n市场背景。\n\n预算30万元。')
    assert [a['text'] for a in source['agenda']]==['项目背景','预算安排']
    assert source['planned'][1]['agenda_ids']==['a1','a2']
    assert source['planned'][1]['title']=='目录'
