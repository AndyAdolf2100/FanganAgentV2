"""Content bindings for fixed template pages, derived per job (never saved back)."""
from copy import deepcopy
import re


ALIASES = {'presenter': r'汇报人|汇报|主讲人|演讲人|报告人',
           'advisor': r'指导老师|指导教师|导师', 'date': r'汇报日期|汇报时间|日期|时间'}
LABELS = {'presenter': '汇报', 'advisor': '导师', 'date': '时间'}


def text(element):
    return ''.join(r.get('text', '') for p in element.get('paragraphs', []) for r in p.get('runs', []))


def metadata(manuscript):
    from .manuscript import plain_text
    opening = plain_text(manuscript[:5000])
    result = {}
    for key, aliases in ALIASES.items():
        found = re.search(r'(?:^|[\n｜|])\s*(?:'+aliases+r')\s*[:：]\s*([^\n｜|]+)', opening)
        value = found[1].strip() if found else ''
        result[key] = value if value and len(value) <= 60 else 'AiPPT'
    return result


def number(element, value):
    """Keep the template's number convention: 1 stays 1, 01 stays 01."""
    sample = text(element).strip()
    match = re.search(r'\d+', sample)
    digits = str(value).zfill(len(match[0])) if match and match[0].startswith('0') else str(value)
    return sample[:match.start()]+digits+sample[match.end():] if match else digits


def bind(element, role):
    element.update(textRole=role, binding='content', fixed=False)


def prepare(template, values):
    result = deepcopy(template); result['_metadata'] = dict(values)
    changes = []
    for pi, page in enumerate(result['pages']):
        elements = page['elements']
        if page['role'] in {'cover', 'ending'}:
            targets = [(i,e) for i,e in enumerate(elements) if e['kind']=='text' and
                       (any(re.search(aliases+r'\s*[:：]', text(e)) for aliases in ALIASES.values()) or e.get('textRole') in {'presenter','date'})]
            if targets:
                covered = set()
                for _, e in targets:
                    keys = [k for k,a in ALIASES.items() if re.search('(?:'+a+r')\s*[:：]',text(e))]
                    if not keys: keys = ['date' if e.get('textRole')=='date' else 'presenter']
                    e['_metadata_keys'] = keys; covered.update(keys)
                # Keep every requested field visible, even when the authored
                # strip only has reporter/advisor and no separate date box.
                targets[-1][1]['_metadata_keys'] += [k for k in ALIASES if k not in covered]
                for i,e in targets:
                    keys = e['_metadata_keys']; bind(e, 'presenter')
                    lines = ['  '.join(f'{LABELS[k]}：{values[k]}' for k in keys if k!='date')]
                    if 'date' in keys: lines.append(f'时间：{values["date"]}')
                    content = '\n'.join(line for line in lines if line)
                    e['_replacement'] = content
                    # A two-line info strip is an explicit content adaptation,
                    # with unchanged outer geometry/background. Record it.
                    size = min(24, max(16, (e['height']-4)/(1.15*len(content.splitlines()))))
                    run = deepcopy(e['paragraphs'][0]['runs'][0])
                    run.update(size=size, text=text(e))
                    e['paragraphs'] = [{'align':'center','lineHeight':1.15,'runs':[run]}]
                    e['textLayout'] = {'padding':[2,8,2,8],'vertical':'center','wrap':True,'overflow':False}
                    changes.append({'page':pi+1,'element':i,'action':'填充汇报人/导师/时间；缺失值AiPPT，信息栏在原框内适配两行'})
            else:
                # No pre-existing information region: add a compact strip in
                # the lower safe margin, without moving any authored element.
                w,h=result['width'],result['height']
                content='  '.join(f'{LABELS[k]}：{values[k]}' for k in ALIASES)
                y = next((h*ratio for ratio in (.85,.75,.65) if not any(text(e).strip() and e['y'] < h*ratio+h*.06 and e['y']+e['height'] > h*ratio for e in elements)),h*.85)
                elements.append({'kind':'text','x':w*.05,'y':y,'width':w*.9,'height':h*.06,
                    'textRole':'presenter','binding':'content','order':1,'_replacement':content,
                    'paragraphs':[{'align':'center','runs':[{'text':content,'font':'Noto Sans CJK SC','size':18,'color':'#222222'}]}]})
                changes.append({'page':pi+1,'action':'模板没有信息栏，增加汇报人/导师/时间默认信息'})
        if page['role'] == 'contents':
            numbers = [e for e in elements if e.get('textRole')=='contentsNumber']
            for n in numbers:
                match = re.search(r'\d+', text(n))
                if match: n['order'] = max(1,int(match[0]))
            groups = {}
            for e in elements:
                if e.get('textRole') not in {'contentsItem','contentsSubtitle'} or not numbers:
                    continue
                nearest = min(numbers,key=lambda n:(e['x']-n['x'])**2+(e['y']-n['y'])**2)
                groups.setdefault(nearest['order'],[]).append(e)
            for order, members in groups.items():
                title = max(members,key=lambda e:(max((r.get('size',0) for p in e.get('paragraphs',[]) for r in p['runs']),default=0),-len(text(e))))
                for e in members:
                    bind(e,'contentsItem' if e is title else 'contentsSubtitle'); e['order']=order
            page['_semantic_contents'] = True
        if page['role'] == 'section':
            for e in elements:
                if e.get('textRole') == 'subtitle' and re.search(r'添加内容|请输入|单击.*添加',text(e)):
                    e['_replace_placeholder'] = True
                    changes.append({'page':pi+1,'action':'清除章节页样例占位文字，保留原版式'})
    result['_fixed_changes'] = changes
    return result


def label_limits(template):
    limits = []
    for p in template['pages']:
        if p['role'] == 'contents':
            for e in p['elements']:
                if e.get('textRole')=='contentsItem':
                    size=max((r.get('size',24) for p in e.get('paragraphs',[]) for r in p['runs']),default=24)
                    limits.append(max(2,int((e['width']-16)/size)))
    return min(limits,default=8)


def agenda_labels(agenda, template, call=None):
    limit = label_limits(template)
    labels = {}
    if call and any(len(a['text'])>limit for a in agenda):
        answer = call('为PPT目录生成语义完整的短标题。只输出JSON {"labels":{"a1":"项目背景"}}。'
                      '每条不超过max_characters个字符，不要序号、半截词、省略号或新增事实；完整标题会在目录说明和正文保留。',
                      {'agenda':agenda,'max_characters':limit})
        labels=answer.get('labels',{})
        if not isinstance(labels,dict):raise ValueError('目录短标题格式无效')
    for a in agenda:
        clean = re.sub(r'^(?:[一二三四五六七八九十百\d]+[、.．：:]|第.{1,8}[章节部分]+[：:]?)\s*','',a['text'])
        label = labels.get(a['id'],clean if len(clean)<=limit else f'第{a["number"]}项')
        if not isinstance(label,str) or not label.strip() or len(label)>limit or re.search(r'[…\n]|\.\.\.',label):
            raise ValueError('目录短标题须完整且符合模板宽度')
        a['display_title']=label
    return agenda
