"""Exact source-directory reuse; all model responses are local test doubles."""
from collections import Counter
from copy import deepcopy
from html import escape

import pytest
from bs4 import BeautifulSoup

from marketing_agent.enterprise import model_html as model, template_html as html
from marketing_agent.enterprise.manuscript import parse_markdown, preface_block_ids
from test_enterprise import fixture


REAL_TITLES = [
    '项目背景与目标', '市场洞察（调研发现与核心洞察提炼）',
    '营销策略（核心策略·传播·内容·人群·媒介）',
    '传播主题（#给生活留一点甜# 一主三辅话题体系）',
    '创意执行（创意概念·内容矩阵·分阶段规划·物料）',
    '达人策略（矩阵架构·筛选标准·推荐名单·合作方案）',
    '效果预估与预算分配（KPI 体系·预算验算·优化机制）',
    '执行排期与项目管理', '假设与待核验清单（合规披露）', '数据来源与核验清单',
]


def agenda(titles):
    return [{'id': f'a{i}', 'number': i, 'text': title} for i, title in enumerate(titles, 1)]


def manuscript(titles, *, inline=False, preface=''):
    listing='\n'.join(f'{i}. {title}' for i,title in enumerate(titles,1))
    return ('# 方案\n\n客户：轻芽\n预算：300万元\n\n'+preface+
            ('目录\n' if inline else '## 目录\n\n')+listing+'\n\n'+
            '\n\n'.join(f'## {title}\n\n第 {i} 章的正文。' for i,title in enumerate(titles,1)))


def planner(entries, seen):
    def call(name, policy, payload):
        if name=='enterprise_brief':
            return {'title':'方案','metadata':dict.fromkeys(('presenter','advisor','date'),'AiPPT'),'agenda':deepcopy(entries)}
        assert name=='enterprise_plan_html'
        seen.append(deepcopy(payload))
        pick=lambda role:next(t['template_page'] for t in payload['templates'] if t['role']==role)
        remaining=list(payload['blocks']);pages=[]
        if payload['first_batch']:
            title_ids=[b['id'] for b in remaining if b['kind']=='heading' and b.get('level')==1 and 'cover' in b['allowed_page_roles']]
            pages.append({'role':'cover','template_page':pick('cover'),'title':'方案','block_ids':title_ids})
            remaining=[b for b in remaining if b['id'] not in title_ids]
        preface=[b['id'] for b in remaining if b['allowed_page_roles']==['preface']]
        if preface:pages.append({'role':'preface','template_page':pick('preface'),'title':'序言','block_ids':preface})
        directory=[b['id'] for b in remaining if b['allowed_page_roles']==['contents']]
        if payload['contents_batch'] and any(t['role']=='contents' for t in payload['templates']):
            pages.append({'role':'contents','template_page':pick('contents'),'title':'目录','block_ids':directory,
                          'agenda_ids':payload['contents_agenda_ids'],
                          'contents_source_bindings':[{'block_id':'forged'}]})
        body=[b['id'] for b in remaining if b['id'] not in preface+directory]
        if body:pages.append({'role':'body','template_page':pick('body'),'title':'正文','block_ids':body})
        if payload['last_batch']:pages.append({'role':'ending','template_page':pick('ending'),'title':'谢谢'})
        return {'slides':pages}
    return call


@pytest.mark.parametrize('inline',[False,True])
def test_real_directory_titles_are_shared_with_contents_without_dropping_sources(tmp_path,inline):
    text=manuscript(REAL_TITLES,inline=inline);seen=[];template=fixture();before=deepcopy(template)
    source=model.plan_deck(tmp_path,template,text,planner(agenda(REAL_TITLES),seen))
    bindings=source['contents_source_bindings'];bound={b['block_id'] for b in bindings}
    assert source['blocks']==parse_markdown(text)
    assert Counter(i for p in source['planned'] for i in p.get('block_ids',[]))==Counter(b['id'] for b in source['blocks'])
    assert all(not (set(p.get('block_ids',[]))&bound) for p in source['planned'] if p['role']!='contents')
    contents=[p for p in source['planned'] if p['role']=='contents']
    assert [i for p in contents for i in p['agenda_ids']]==[a['id'] for a in agenda(REAL_TITLES)]
    assert [b for p in contents for b in p['contents_source_bindings']]==bindings
    assert all(b['block_id']!='forged' for b in bindings)
    assert [s['prefix'] for b in bindings for s in b['segments'] if s['agenda_id']]==[f'{i}、' for i in range(1,11)]
    assert template==before
    if not inline:
        assert bound=={'b0003','b0004'}
        assert source['blocks'][3]['kind']=='list'


@pytest.mark.parametrize('directory',[
    '## 目录\n\n1. 甲\n2. 不同标题',
    '## 目录\n\n1. 甲',
    '## 目录\n\n1. 甲\n3. 乙',
    '## 目录\n\n1. 甲\n2. 乙\n\n另有必须保留的说明。',
    '## 目录\n\n1. 甲……3\n2. 乙……6',
    '## 目录\n\n2. 乙\n1. 甲',
    '## 目录\n\n### 子目录\n\n1. 甲\n2. 乙',
    '## 工作内容\n\n1. 甲\n2. 乙',
    '## 目录\n\n1. 甲\n2. 乙\n\n## 目录\n\n1. 甲\n2. 乙',
])
def test_unmatched_or_ambiguous_sources_remain_intact_in_body(tmp_path,directory):
    text='# 方案\n\n'+directory+'\n\n## 甲\n\n甲内容。\n\n## 乙\n\n乙内容。'
    source=model.plan_deck(tmp_path,fixture(),text,planner(agenda(['甲','乙']),[]))
    assert not source.get('contents_source_bindings')
    assert all('contents_source_bindings' not in p for p in source['planned'])
    assert source['blocks']==parse_markdown(text)
    nonhead={b['id'] for b in source['blocks'] if b['kind']!='heading'}
    assert nonhead<=set(i for p in source['planned'] if p['role']=='body' for i in p.get('block_ids',[]))


def test_no_contents_template_preserves_original_directory_as_body(tmp_path):
    template=fixture();template['pages'][1]['role']='exclude'
    source=model.plan_deck(tmp_path,template,manuscript(['甲','乙']),planner(agenda(['甲','乙']),[]))
    assert not source.get('contents_source_bindings')
    assert all(p['role']!='contents' for p in source['planned'])
    assert 'b0004' in next(p['block_ids'] for p in source['planned'] if p['role']=='body')


def test_long_directory_and_preface_batches_cover_each_item_once(tmp_path):
    template=fixture();p=deepcopy(template['pages'][3]);p.update(id='preface',role='preface');template['pages'].append(p)
    titles=[f'主题{i:03}：'+('完整内容标题'*9) for i in range(1,181)]
    text=manuscript(titles,preface='## 序言\n\n'+('完整序言。'*1200)+'\n\n')
    seen=[];source=model.plan_deck(tmp_path,template,text,planner(agenda(titles),seen))
    roles=[p['role'] for p in source['planned']]
    assert roles.count('preface')>1 and roles.count('contents')>1
    assert max(i for i,r in enumerate(roles) if r=='preface')<roles.index('contents')
    assert max(i for i,r in enumerate(roles) if r=='contents')<roles.index('body')
    assert [a for p in source['planned'] for a in p.get('agenda_ids',[])]==[a['id'] for a in agenda(titles)]
    assert Counter(i for p in source['planned'] for i in p.get('block_ids',[]))==Counter(b['id'] for b in source['blocks'])
    assert all(sum(len(b['text']) for b in payload['blocks'])<=5500 for payload in seen)
    for page in source['planned']:
        for binding in page.get('contents_source_bindings',[]):
            assert set(binding['agenda_ids'])<=set(page['agenda_ids'])


def test_directory_inside_explicit_preface_is_not_reassigned():
    blocks=parse_markdown('# 方案\n\n## 序言\n\n### 目录\n\n1. 甲\n2. 乙\n\n## 甲\n\n正文')
    assert not model.match_contents_sources(blocks,agenda(['甲','乙']),preface_block_ids(blocks))


def test_without_preface_template_explicit_preface_stays_body(tmp_path):
    text=manuscript(['甲','乙'],preface='## 序言\n\n前言说明。\n\n')
    source=model.plan_deck(tmp_path,fixture(),text,planner(agenda(['甲','乙']),[]))
    preface=set(preface_block_ids(source['blocks']))
    assert preface
    assert preface<=set(i for p in source['planned'] if p['role']=='body' for i in p.get('block_ids',[]))
    assert source['contents_source_bindings']


def test_matched_source_must_share_its_planning_group_with_all_its_agenda_items():
    blocks=parse_markdown('## 目录\n\n1. 甲\n2. 乙');entries=agenda(['甲','乙'])
    bindings=model.match_contents_sources(blocks,entries)
    pages=[{'role':'contents','template_page':1,'title':'目录','block_ids':['b0001','b0002'],'agenda_ids':['a1']},
           {'role':'contents','template_page':1,'title':'目录','agenda_ids':['a2']}]
    with pytest.raises(ValueError,match='同一规划组'):
        model.validate_plan({'slides':pages},blocks,model.catalog(fixture()),entries,False,False,contents_batch=True,contents_source_bindings=bindings)
    pages=[{'role':'contents','template_page':1,'title':'目录','block_ids':['b0001'],'agenda_ids':['a1','a2']},
           {'role':'body','template_page':3,'title':'重复目录','block_ids':['b0002']}]
    with pytest.raises(ValueError,match='重复正文目录'):
        model.validate_plan({'slides':pages},blocks,model.catalog(fixture()),entries,False,False,contents_batch=True,contents_source_bindings=bindings)


def test_legacy_plan_validation_does_not_migrate_existing_directory_assignments():
    blocks=parse_markdown('## 目录\n\n1. 甲\n2. 乙');entries=agenda(['甲','乙'])
    pages=[{'role':'contents','template_page':1,'title':'目录','block_ids':['b0001'],'agenda_ids':['a1','a2']},
           {'role':'body','template_page':3,'title':'原目录','block_ids':['b0002']}]
    before=deepcopy(pages)
    assert model.validate_plan({'slides':pages},blocks,model.catalog(fixture()),entries,False,False,contents_batch=True)==before
    assert pages==before


@pytest.mark.parametrize('change',['missing','duplicate','foreign'])
def test_matched_contents_cannot_omit_duplicate_or_absorb_unmatched_sources(change):
    blocks=parse_markdown('## 目录\n\n1. 甲\n2. 乙');entries=agenda(['甲','乙'])
    bindings=model.match_contents_sources(blocks,entries)
    pages=[{'role':'contents','template_page':1,'title':'目录','block_ids':['b0001','b0002'],'agenda_ids':['a1','a2']}]
    if change=='missing':pages[0]['block_ids'].remove('b0002')
    elif change=='duplicate':pages[0]['block_ids'].append('b0002')
    else:
        blocks.append({'id':'b0003','kind':'paragraph','text':'预算为300万元','level':0})
        pages[0]['block_ids'].append('b0003')
    with pytest.raises(ValueError,match='恰好覆盖全部来源|block_ids分配错误'):
        model.validate_plan({'slides':pages},blocks,model.catalog(fixture()),entries,False,False,contents_batch=True,contents_source_bindings=bindings)


def shared_documents(bindings):
    """One visible entry supplies two independent provenance checks, unnested."""
    documents=[]
    for binding in bindings:
        for segment in binding['segments']:
            attr=f'data-source-block="{binding["block_id"]}"'
            if segment['agenda_id']:
                documents.append(f'<span {attr}>{escape(segment["prefix"])}</span><span {attr} data-agenda-item="{segment["agenda_id"]}">{escape(segment["text"])}</span>')
            else:documents.append(f'<h1 {attr}>{escape(segment["text"])}</h1>')
    return ['<html><body>'+''.join(documents[:2])+'</body></html>',
            '<html><body>'+''.join(documents[2:])+'</body></html>']


def test_visible_shared_text_proves_full_sources_across_contents_continuations():
    blocks=parse_markdown('## 目录\n\n01. 甲\n02. 乙');entries=agenda(['甲','乙'])
    bindings=model.match_contents_sources(blocks,entries);documents=shared_documents(bindings)
    assert bindings[1]['segments'][0]['prefix']=='01、'
    html.validate_sources(documents,blocks,entries)
    for document in documents:
        for node in BeautifulSoup(document,'html.parser').select('[data-source-block],[data-agenda-item]'):
            assert not node.select('[data-source-block],[data-agenda-item]')
    assert ''.join(BeautifulSoup(d,'html.parser').get_text() for d in documents).count('甲')==1


@pytest.mark.parametrize('corruption',['number','punctuation','missing','duplicate','order','label'])
def test_sharing_never_weakens_existing_source_or_agenda_checks(corruption):
    blocks=parse_markdown('## 目录\n\n1. 甲\n2. 乙');entries=agenda(['甲','乙'])
    documents=shared_documents(model.match_contents_sources(blocks,entries))
    if corruption=='number':documents[0]=documents[0].replace('1、','')
    elif corruption=='punctuation':documents[0]=documents[0].replace('1、','1')
    elif corruption=='missing':documents=documents[:1]
    elif corruption=='duplicate':documents.append(documents[-1])
    elif corruption=='order':documents.reverse()
    else:documents[0]=documents[0].replace('甲','改写的甲')
    with pytest.raises(ValueError,match='原文或目录内容不完整'):
        html.validate_sources(documents,blocks,entries)
