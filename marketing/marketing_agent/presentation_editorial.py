"""A bounded editorial pass before locking copy for page design."""
import hashlib
import json
import os
import re
import urllib.request
from copy import deepcopy

from .presentation_compat import numbers, record, write_log
from .presentation_design import validate_design


POLICY = '''你是商业提案的主编和艺术指导。将已有逐页规划调整到真实16:9幻灯片的容量，保留说服链，不写新的营销方案。
只返回JSON {"pages":[{"index":1,"title":"…","subtitle":"…","layout":"…","composition":"…","layout_brief":"构图骨架、唯一焦点、空间比例与留白位置","asset":"…","items":[{"label":"…","text":"…","value":"…"}]}]}。
所有页面都返回，index从1开始，顺序及页数不变。chart页不要返回修改，不改图表结构。
原稿全量保留在备注，因此正文可以提炼、删除重复解释，但不得新增或改变指标、单位、范围、事实、预测条件。items中的指标value必须逐项原样保留。不要把预测改为保证。不新增来源ID。
封面、section章节页和结束页：保留这些页的layout。标题≤14字，可自然换行，副标题≤26字；只留一个简短信息项，标签≤10字，text≤22字，value为空。封面不要塞周期、预算、核心心智三组长说明；结尾用行动主张而非夸张的GMV承诺。
其他标题≤22字；副标题≤36字。label≤10字。四栏及五栏正文每项≤28字；三栏正文≤38字。指标页每项正文≤22字；保留全部value。
依据信息关系选择composition：audience_bands分层受众，platform_bands平台分工，time_grid横向阶段，message_house信息屋，hero_statement主张。同类结构保持一致，不要连续三页相同分栏。三至四项的长内容优先横带，避免五列窄卡片。
每页layout_brief必须具体描述构图，不得仅复述页型名称。写明主焦点是哪项锁定文字、主次宽比、信息分组和留白位置。不能将所有信息屋写成三栏加顶线；可用主张占左侧四成、证据纵向排列等内容适配关系。不要要求增加不存在的图标、数字、文案。同类模块首现页定骨架，同模块后续页延续几何。
场景/情绪洞察/创意执行页优先用image布局：只配语义匹配的asset，正文一至两项（每项≤26字），不要给数据页贴无关图片。有cover素材时仅封面使用cover。scene1和scene2分别选择一个明确场景，同场景可复用。深色章节也可用纯色大字，不强迫配图。不得将概念图片当成实物照片或技术证据。
body从y230至625，禁止通过缩字号或裁切省略文字。只做文案容量、图片用途和语义构图决策；不输出HTML。'''


def refine_design(design, source, folder):
    """One cached text request; no repeated paid attempts on editorial failure."""
    def event(tool, **data):
        with (folder/'tool-trace.jsonl').open('a') as f:
            f.write(json.dumps({'tool':tool,**data},ensure_ascii=False)+'\n')
    brief={'pages':[{'index':i+1,**{k:p[k] for k in ('title','subtitle','layout','composition','items','asset') if k in p}}
                    for i,p in enumerate(design['pages'])],
           'assets':source.get('asset_descriptions', {k:k for k in source['assets']}), 'theme':design['visual_dna'],
           'reference_analysis':source.get('style_reference',{}),
           'presentation_options':source.get('presentation_options',{})}
    digest=hashlib.sha256(json.dumps({'brief':brief,'policy':POLICY,'model':os.getenv('MARKETING_MODEL')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    cache=folder.parent/'editorial-cache'/f'{digest}.json'
    try:
        if cache.exists():
            answer=json.loads(cache.read_text());event('edit_deck',cache_hit=True)
        else:
            payload={'model':os.environ['MARKETING_MODEL'],'messages':[{'role':'system','content':POLICY},{'role':'user','content':json.dumps(brief,ensure_ascii=False)}],
                     'temperature':0.2,'max_tokens':14000,'thinking':{'type':'disabled'}}
            req=urllib.request.Request(os.environ['MARKETING_BASE_URL'].rstrip('/')+'/chat/completions',data=json.dumps(payload).encode(),
                                       headers={'Authorization':'Bearer '+os.environ['MARKETING_API_KEY'],'Content-Type':'application/json'})
            event('edit_deck',cache_hit=False)
            with urllib.request.urlopen(req,timeout=240) as response:raw=json.loads(response.read(4*1024*1024))
            (folder/'editorial-response.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
            event('model_usage',stage='editorial',usage=raw.get('usage',{}))
            if raw['choices'][0].get('finish_reason')=='length':raise ValueError('文案精简输出截断')
            answer=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',raw['choices'][0]['message']['content'].strip()))
        updated=apply_edits(design,answer,source)
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(answer,ensure_ascii=False,indent=2))
        (folder/'editorial-design.json').write_text(json.dumps(updated,ensure_ascii=False,indent=2))
        write_log(folder,updated)
        return updated
    except (ValueError,KeyError,TypeError,OSError) as exc:
        event('editorial_fallback',reason=str(exc)[:500])
        design['editorial_notice']='文案精简未完成，保留原规划并继续排版。'
        return design


def apply_edits(design, answer, source):
    result=deepcopy(design)
    # Keep earlier corrections, but recalculate numeric warnings against final copy.
    result['corrections']=[c for c in result.get('corrections',[]) if c['status']!='warning']
    blocks={b['id']:b for b in source['source_blocks']}
    updates=answer['pages']
    if not isinstance(updates,list):raise ValueError('编辑输出缺少页面列表')
    seen=set()
    for update in updates:
        i=update['index']-1
        if i in seen or not 0<=i<len(result['pages']):raise ValueError('编辑页面编号无效')
        seen.add(i);page=result['pages'][i]
        if page['layout']=='chart':continue
        original=deepcopy(page)
        old_text=' '.join([page['title'],page.get('subtitle','')]+[str(v) for item in page.get('items',[]) for k,v in item.items() if k!='amount'])
        # Never introduce a new numeric token through the copy-edit stage.
        new_text=' '.join([update.get('title',''),update.get('subtitle','')]+[str(v) for item in update.get('items',[]) for k,v in item.items() if k!='amount'])
        # Permit a date's display notation to change only when the same date and
        # following phrase exist in the original (9.1悬念片首发 → 9月1日悬念片首发).
        def date_notation(match):
            dotted=f'{int(match[1])}.{int(match[2])}'
            following=new_text[match.end():match.end()+4]
            return dotted if len(following)==4 and dotted+following in old_text else match[0]
        compared=re.sub(r'(?<!\d)(\d{1,2})月(\d{1,2})日',date_notation,new_text)
        if numbers(compared)-numbers(old_text):
            record(result['corrections'],page,i,'editorial',new_text,old_text,'精简稿引入新数字，保留原稿措辞',blocks)
            continue
        if page['layout'] in {'metrics','bars'}:
            if [v.get('value','') for v in update.get('items',[])]!=[v.get('value','') for v in page.get('items',[])]:
                record(result['corrections'],page,i,'editorial',update,original,'指标精简改变数值，保留原稿',blocks)
                continue
            update['layout']=page['layout']
            update['composition']=''
            for item,old in zip(update['items'],page.get('items',[])):
                item.pop('amount',None)
                if 'amount' in old:item['amount']=old['amount']
        for key in ('title','subtitle','layout','composition','layout_brief','asset','items'):
            value=update.get(key, [] if key=='items' else '')
            if key=='layout_brief' and (not isinstance(value,str) or len(value)>1600):continue
            if key=='asset' and value and value not in source['assets']:continue
            before=page.get(key)
            if before!=value:
                page[key]=value
                record(result['corrections'],page,i,key,before,value,'按幻灯片容量精简并明确图片/构图；完整原文保留在备注',blocks)
        page.pop('custom',None);page.pop('needs_review',None)
        if page.get('layout') in {'image','cover','section','closing'} and page.get('composition'):
            before=page.pop('composition')
            record(result['corrections'],page,i,'composition',before,None,'场景页使用图片对开版式，避免无图主张母版覆盖配图',blocks)
        if page['layout'] in {'cover','closing'} and '，' in page['title'] and '\n' not in page['title']:
            before=page['title'];page['title']=before.replace('，','，\n',1)
            record(result['corrections'],page,i,'title',before,page['title'],'主视觉标题按语义断行，避免末行只剩一个字',blocks)
    required={i for i,p in enumerate(result['pages']) if p['layout']!='chart'}
    if not required<=seen:raise ValueError('编辑结果遗漏页面')
    return validate_design(result,source,compatible=True)
