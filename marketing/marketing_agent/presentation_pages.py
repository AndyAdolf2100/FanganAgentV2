"""Page-scoped generation tools: brief → model HTML → probe → bounded repair.

The orchestrator chooses pages; the child model controls layout inside a fixed
canvas. Source wording is locked and resource access is limited to known assets.
"""
import hashlib
import html
import fcntl
import json
import os
import re
import subprocess
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'presentation'


def canonical_texts(page):
    texts=[page['title'],page.get('subtitle',''),page.get('section','')]
    for item in page.get('items',[]):
        texts.extend(item.get(k,'') for k in ('value','label','text'))
    return [t for t in texts if t]


def text_catalog(page):
    result={k:page[k] for k in ('title','subtitle','section') if page.get(k)}
    for i,item in enumerate(page.get('items',[])):
        for k in ('value','label','text'):
            if item.get(k):result[f'item_{i}_{k}']=item[k]
    return result


def expand_text_refs(markup,page):
    catalog=text_catalog(page)
    def substitute(match):
        tag,attrs,key=match.group(1),match.group(2),match.group(3)
        if key not in catalog:raise ValueError('未知文字引用：'+key)
        # Role is determined by the locked field, not guessed by the model.
        # A label emitted as a paragraph must not burn three repair requests.
        role='title' if key=='title' else 'section' if key=='section' else 'label' if key.endswith(('_label','_value')) else 'body'
        attrs=re.sub(r"\s+data-role=['\"][^'\"]*['\"]",'',attrs)
        attrs+=f' data-role="{role}"'
        if 'data-text' not in attrs:attrs+=' data-text'
        return '<'+tag+attrs+'>'+html.escape(catalog[key])+'</'+tag+'>'
    return re.sub(r"<([a-z0-9]+)([^>]*?data-ref=['\"]([^'\"]+)['\"][^>]*)>\s*</\1>",substitute,markup)


def load_master(page,assets):
    name=page.get('composition')
    if name not in COMPOSITIONS:return None
    path=ROOT/'skills'/'marketing-deck'/'masters'/f'{name}.json'
    from .presentation_masters import adaptive_master
    adaptive=adaptive_master(page)
    if adaptive:
        adaptive['body']=expand_text_refs(adaptive['body'],page)
        return adaptive
    if not path.exists():return None
    master=json.loads(path.read_text())
    if set(master['required_text_keys'])!=set(text_catalog(page)):return None
    body=master['body']
    if master.get('requires_scene'):
        if page.get('asset') not in assets:return None
        body=body.replace('asset:__scene__','asset:'+page['asset'])
    return {'body':expand_text_refs(body,page),'css':master['css'],
            'master':name,'master_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


class PageMarkup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text=[];self.marked=0;self.stack=[];self.images=[];self.records=[];self.active=None

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag not in {'main','section','article','div','span','h1','h2','h3','p','strong','b','em','br','img'}:
            raise ValueError('不允许的页面标签：'+tag)
        if any(k.startswith('on') or k in {'srcset','href'} for k in attrs):
            raise ValueError('页面包含活动内容或未知资源')
        if 'style' in attrs:validate_css(attrs['style'])
        if tag=='img':
            if not re.fullmatch(r'asset:[a-z][a-z0-9_-]{0,31}',attrs.get('src','')):raise ValueError('图片必须使用已注册asset引用')
            self.images.append(attrs['src'][6:])
        marked='data-text' in attrs
        if marked and self.marked:raise ValueError('data-text不可嵌套，否则PPTX重复导出')
        if marked:
            self.active={'role':attrs.get('data-role','body'),'text':''}
            self.records.append(self.active)
        if tag not in {'img','br'}:
            self.stack.append((tag,marked));self.marked+=int(marked)

    def handle_endtag(self,tag):
        if not self.stack or self.stack[-1][0]!=tag:raise ValueError('HTML标签未正确闭合')
        _,marked=self.stack.pop();self.marked-=int(marked)
        if marked:self.active=None

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in {'img','br'}:self.handle_endtag(tag)

    def handle_data(self,data):
        if data.strip():
            if not self.marked:raise ValueError('所有可见文字必须置于data-text元素内')
            self.text.append(data)
            if self.active is not None:self.active['text']+=data


def validate_css(css):
    if re.search(r'url\s*\(|@import|@font-face|expression\s*\(|javascript:|</|(?:^|[;{])\s*content\s*:',css,re.I):
        raise ValueError('CSS包含外部资源或伪元素文字')
    if re.search(r'\b(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\D|$)|transform\s*:\s*scale)',css,re.I):
        raise ValueError('禁止隐藏或整体缩小文字以通过检查')


def normalize(text):
    return re.sub(r'\s+','',text)


def validate_html(result,page,assets):
    body,css=result.get('body',''),result.get('css','')
    if not isinstance(body,str) or not isinstance(css,str) or not 100<len(body)<50000 or len(css)>30000:
        raise ValueError('页面HTML/CSS长度不合规范')
    validate_css(css)
    parser=PageMarkup();parser.feed(body);parser.close()
    if parser.stack:raise ValueError('HTML标签未闭合')
    if any(k not in assets for k in parser.images):raise ValueError('引用未提供的素材')
    if page.get('asset') in assets and page['asset'] not in parser.images:
        raise ValueError('页面遗漏已指定的场景图片：'+page['asset'])
    visible=normalize(''.join(parser.text))
    expected=canonical_texts(page)
    remaining=visible
    for text in sorted(expected,key=len,reverse=True):
        value=normalize(text)
        if value not in remaining:raise ValueError('页面漏改锁定文字：'+text[:60])
        remaining=remaining.replace(value,'',1)
    if remaining:raise ValueError('页面出现未经brief授权的文字：'+remaining[:80])
    catalog=text_catalog(page)
    for record in parser.records:
        keys=[k for k,v in catalog.items() if normalize(v)==normalize(record['text'])]
        if not keys:raise ValueError('一个文字元素必须对应一个完整文案ID')
        allowed={'title' if k=='title' else 'section' if k=='section' else 'label' if k.endswith(('_label','_value')) else 'body' for k in keys}
        if record['role'] not in allowed:raise ValueError('文字角色不能降级字号：'+record['text'][:40])
    return {'body':body,'css':css}


COMPOSITIONS={'message_house','audience_bands','platform_bands','time_grid','storyboard','subject_profile','hero_statement'}


def module_contract(plan,index):
    page=plan['pages'][index]
    def signature(p):return (p.get('section',''),p.get('composition',p['layout']),len(p.get('items',[])),bool(p.get('asset')))
    identity=signature(page)
    first=next(i for i,p in enumerate(plan['pages'][:index+1]) if signature(p)==identity)
    contract={'id':hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:12],
              'module':identity[0],'composition':identity[1],'first_page':first+1,
              'header':{'left':64,'eyebrow_top':42,'title_top':88,'title_font':46},
              'content_bottom':625,'footer_top':660}
    master=plan['pages'][first].get('custom')
    if first<index and master:
        skeleton=master['body']
        for key,value in sorted(text_catalog(plan['pages'][first]).items(),key=lambda pair:len(pair[1]),reverse=True):
            skeleton=skeleton.replace(html.escape(value),'{{'+key+'}}')
        contract['reference_html_structure']=skeleton
        contract['reference_css']=master['css']
    return contract


class PageTools:
    def __init__(self,folder,plan,model_call_limit=8):
        self.folder=Path(folder);self.plan=plan
        self.model_call_limit=model_call_limit;self.model_calls=0
        self.root=self.folder/'page-revisions';self.root.mkdir(exist_ok=True)
        self.cache=self.folder.parent/'page-cache';self.cache.mkdir(exist_ok=True)

    def trace(self,tool,**detail):
        with (self.folder/'tool-trace.jsonl').open('a') as f:f.write(json.dumps({'tool':tool,**detail},ensure_ascii=False)+'\n')

    def read_page(self,index):
        path=self.root/f'{index+1}.json'
        return json.loads(path.read_text()) if path.exists() else {'rev':0,'page_index':index}

    def write_page(self,index,markup,expected_rev):
        # File lock makes the compare-and-swap atomic across instances/processes.
        with (self.root/f'{index+1}.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            current=self.read_page(index)
            if current['rev']!=expected_rev:raise ValueError('页面版本已变化，请先重新读取')
            checked=validate_html(markup,self.plan['pages'][index],self.plan['assets'])
            result={**checked,'rev':expected_rev+1,'page_index':index}
            path=self.root/f'{index+1}.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2));tmp.replace(path)
        self.trace('write_page',page_index=index,expected_rev=expected_rev,rev=result['rev'])
        return result

    def render_probe(self,index):
        completed=subprocess.run([os.getenv('PRESENTATION_NODE','node'),str(ROOT/'probe-page.mjs'),str(self.folder),str(index)],capture_output=True,text=True,timeout=45)
        if completed.returncode:raise RuntimeError('页面探针执行失败：'+completed.stderr[-600:])
        result=json.loads(completed.stdout)
        self.trace('render_probe',page_index=index,issues=result['issues'])
        return result

    def page_brief(self,index):
        page=self.plan['pages'][index]
        return {'page_index':index,'conclusion':page['title'],'section':page.get('section'),
               'assigned_asset':page.get('asset'),
               'asset_descriptions':self.plan.get('asset_descriptions',{}),
               'reference_analysis':self.plan.get('style_reference',{}),
               'layout_brief':page.get('layout_brief','按内容关系确定主焦点、分组、空间比例，不默认等宽三栏'),
               'text_catalog':text_catalog(page),'source_ids':page['source_ids'],
               'visual_dna':self.plan['theme'],'asset_names':list(self.plan['assets']),
               'module_master':module_contract(self.plan,index),
               'previous_conclusion':self.plan['pages'][index-1]['title'] if index else None,
               'next_conclusion':self.plan['pages'][index+1]['title'] if index+1<len(self.plan['pages']) else None}

    def page_cache_path(self,brief,skill):
        digest=hashlib.sha256(json.dumps({'brief':brief,'skill':skill,'model':os.getenv('MARKETING_MODEL')},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return self.cache/(digest+'.json')

    def cache_reviewed_page(self,index):
        skill=(ROOT/'skills'/'marketing-deck'/'references'/'page-generation.md').read_text()
        checked=validate_html(self.plan['pages'][index]['custom'],self.plan['pages'][index],self.plan['assets'])
        self.page_cache_path(self.page_brief(index),skill).write_text(json.dumps(checked,ensure_ascii=False,indent=2))
        self.trace('cache_reviewed_page',page_index=index)

    def generate_page(self,index):
        page=self.plan['pages'][index]
        if page.get('custom'):
            result=self.write_page(index,page['custom'],self.read_page(index)['rev'])
            if not self.render_probe(index)['issues']:
                self.trace('page_cache',page_index=index,hit=True,source='reviewed_plan')
                return result
        skill=(ROOT/'skills'/'marketing-deck'/'references'/'page-generation.md').read_text()
        brief=self.page_brief(index)
        cached=self.page_cache_path(brief,skill)
        if cached.exists():
            result=self.write_page(index,json.loads(cached.read_text()),self.read_page(index)['rev'])
            if not self.render_probe(index)['issues']:
                self.trace('page_cache',page_index=index,hit=True)
                return result
        # Reference-driven first prototypes must be designed by the page agent;
        # otherwise the fixed skeleton wins before the model sees the brief.
        master=None if self.plan.get('style_id')=='brand_launch' and page.get('layout_brief') else load_master(page,self.plan['assets'])
        if master:
            try:
                result=self.write_page(index,master,self.read_page(index)['rev'])
                probe=self.render_probe(index)
                self.trace('load_master',page_index=index,name=master['master'],sha256=master['master_sha256'],passed=not probe['issues'])
                if not probe['issues']:return result
            except (ValueError,TypeError,KeyError) as exc:
                self.trace('master_rejected',page_index=index,reason=str(exc))
        messages=[{'role':'system','content':skill},{'role':'user','content':json.dumps(brief,ensure_ascii=False)}]
        self.trace('load_skill',name='marketing-deck/page-generation',page_index=index)
        for attempt in range(3):
            if self.model_calls>=self.model_call_limit:raise ValueError('逐页模型调用预算已用完，转用已验证的结构化版式')
            self.model_calls+=1
            self.trace('generate_page',page_index=index,attempt=attempt+1,job_call=self.model_calls)
            payload={'model':os.environ['MARKETING_MODEL'],'messages':messages,'max_tokens':7500,'temperature':0.35,'thinking':{'type':'disabled'}}
            req=urllib.request.Request(os.environ['MARKETING_BASE_URL'].rstrip('/')+'/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['MARKETING_API_KEY']})
            with urllib.request.urlopen(req,timeout=180) as response:answer=json.loads(response.read(2*1024*1024))
            responses=self.folder/'page-responses';responses.mkdir(exist_ok=True)
            (responses/f'{index+1}-{attempt+1}.json').write_text(json.dumps(answer,ensure_ascii=False,indent=2))
            self.trace('model_usage',page_index=index,usage=answer.get('usage',{}))
            if answer['choices'][0].get('finish_reason')=='length':raise ValueError('页面生成被截断')
            raw=answer['choices'][0]['message']['content'];messages.append({'role':'assistant','content':raw})
            try:
                markup=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip()))
                markup['body']=expand_text_refs(markup['body'],page)
                result=self.write_page(index,markup,self.read_page(index)['rev'])
                probe=self.render_probe(index)
                if probe['issues']:raise ValueError(json.dumps(probe['issues'],ensure_ascii=False))
                cached.write_text(json.dumps(result,ensure_ascii=False,indent=2))
                return result
            except (ValueError,TypeError,KeyError) as exc:
                self.trace('repair_page',page_index=index,attempt=attempt+1,reason=str(exc)[:1800])
                if attempt==2:raise
                messages.append({'role':'user','content':'根据真实检查修正此页，锁定文字不变，返回完整JSON：'+str(exc)[:3000]})
        raise RuntimeError('页面未完成')


def select_custom_pages(plan,limit=4):
    """Prioritize semantic structures; reviewed pages never consume new model calls."""
    reviewed=[i for i,p in enumerate(plan['pages']) if p.get('custom')]
    if reviewed:return reviewed
    families=[('信息架构','信息屋','message_house'),('人群策略','受众','audience_bands'),
              ('平台生态','平台链路','platform_bands'),('创意排期','执行排期','time_grid')]
    selected=[i for i,p in enumerate(plan['pages']) if p.get('composition') in COMPOSITIONS]
    for terms in families:
        for i,page in enumerate(plan['pages']):
            if i in selected or page['layout'] in {'cover','closing','image','metrics','bars','chart'}:continue
            text=page.get('section','')+' '+page['title']
            if any(term in text for term in terms[:-1]):
                page['composition']=terms[-1];selected.append(i);break
    if plan.get('style_id')=='brand_launch':
        distinct=[];seen=set()
        for i in selected:
            family=plan['pages'][i].get('composition')
            if family not in seen:distinct.append(i);seen.add(family)
        distinct.sort(key=lambda i:plan['pages'][i].get('composition')=='hero_statement')
        images=[i for i,p in enumerate(plan['pages']) if p.get('layout')=='image' and p.get('asset')]
        return (images+distinct)[:limit]
    return selected[:limit]


def reuse_prototype(plan,index):
    page=plan['pages'][index]
    if not page.get('composition'):return None
    for earlier in plan['pages'][:index]:
        if (not earlier.get('custom') or earlier.get('section')!=page.get('section') or
            earlier.get('composition')!=page.get('composition') or
            bool(earlier.get('asset'))!=bool(page.get('asset')) or
            set(text_catalog(earlier))!=set(text_catalog(page))):continue
        markup=earlier['custom']
        body=re.sub(r"(<([a-z0-9]+)[^>]*\bdata-ref=['\"][^'\"]+['\"][^>]*>).*?(</\2>)",r'\1\3',markup['body'],flags=re.S)
        body=expand_text_refs(body,page)
        if page.get('asset'):body=body.replace('asset:'+earlier['asset'],'asset:'+page['asset'])
        return {'body':body,'css':markup['css'],'master':'agent-module-prototype',
                'master_sha256':hashlib.sha256((body+markup['css']).encode()).hexdigest()}
    return None


def generate_custom_pages(folder,plan,progress=None):
    """Bounded child generation; a failed bespoke layout falls back with a visible record.

    The final renderer still probes every page, including every fallback. Reviewed
    cached HTML is remeasured using this deployment's actual fonts before export.
    """
    folder=Path(folder)
    limit=max(0,min(12,int(os.getenv('MARKETING_PPT_CUSTOM_PAGES','4'))))
    call_limit=max(0,min(36,int(os.getenv('MARKETING_PPT_PAGE_CALL_LIMIT','8'))))
    tools=PageTools(folder,plan,model_call_limit=call_limit)
    selected=select_custom_pages(plan,limit)
    result={'selected':[i+1 for i in selected],'passed':[],'fallbacks':[],'model_calls':0}
    for position,index in enumerate(selected):
        if progress:progress(position+1,len(selected),index+1)
        try:
            page=tools.generate_page(index)
            plan['pages'][index]['custom']=page
            result['passed'].append(index+1)
        except (ValueError,RuntimeError,OSError,subprocess.SubprocessError,KeyError,TypeError) as exc:
            plan['pages'][index].pop('custom',None)
            reason=str(exc)[:700]
            result['fallbacks'].append({'page':index+1,'reason':reason})
            tools.trace('page_fallback',page_index=index,layout=plan['pages'][index]['layout'],reason=reason)
        temporary=folder/'plan.tmp'
        temporary.write_text(json.dumps(plan,ensure_ascii=False,indent=2));temporary.replace(folder/'plan.json')
    # First design the selected prototypes, then bind their locked text IDs for
    # compatible later pages in the same module. Static masters are a fallback.
    for index,page in enumerate(plan['pages']):
        if index in selected or page.get('custom'):continue
        master=reuse_prototype(plan,index) or load_master(page,plan['assets'])
        if not master:continue
        try:
            markup=tools.write_page(index,master,tools.read_page(index)['rev'])
            probe=tools.render_probe(index)
            tools.trace('load_master',page_index=index,name=master['master'],passed=not probe['issues'])
            if probe['issues']:continue
            page['custom']=markup
            result['selected'].append(index+1);result['passed'].append(index+1)
        except (ValueError,RuntimeError,OSError,subprocess.SubprocessError,KeyError,TypeError) as exc:
            tools.trace('master_rejected',page_index=index,reason=str(exc)[:500])
    result['model_calls']=tools.model_calls
    plan['page_generation']=result
    (folder/'page-generation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (folder/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    return result
