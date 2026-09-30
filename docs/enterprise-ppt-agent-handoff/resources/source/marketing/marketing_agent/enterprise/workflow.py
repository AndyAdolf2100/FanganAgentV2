"""Project-owned enterprise workflow: model planning, permissioned fill and QA."""
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from copy import deepcopy
from pathlib import Path

from bs4 import BeautifulSoup
from . import template_html as html, template_components as components
from . import template_component_render as fill
from .manuscript import parse_markdown, batches
from .prompts import PLAN_PROMPT
from . import adaptive, fixed
from ..presentation_options import check_page_count

ROOT = Path(__file__).resolve().parents[2] / 'presentation'
SKILL = ROOT / 'skills/enterprise-deck/SKILL.md'


def model_json(folder, name, policy, payload):
    """Use the project's configured text model, with reproducible local responses."""
    if not os.getenv('MARKETING_API_KEY'):
        raise ValueError('企业模板规划需要配置项目文稿模型')
    request = {'model': os.getenv('MARKETING_MODEL', 'glm-5'),
               'messages': [{'role': 'system', 'content': policy},
                            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
               'temperature': .1, 'max_tokens': 16000 if name == 'enterprise_full_html' else 9000, 'thinking': {'type': 'disabled'}}
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    target = folder / f'{name}-{digest[:16]}.json'
    started=time.monotonic();cache_hit=target.exists()
    if target.exists():
        result = json.loads(target.read_text())
    else:
        req = urllib.request.Request(os.environ['MARKETING_BASE_URL'].rstrip('/') + '/chat/completions',
            data=json.dumps(request).encode(), headers={'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + os.environ['MARKETING_API_KEY']})
        with urllib.request.urlopen(req, timeout=240) as response:
            result = json.loads(response.read(8 * 1024 * 1024))
        target.write_text(json.dumps(result, ensure_ascii=False))
    with (folder/'tool-trace.jsonl').open('a') as f:
        f.write(json.dumps({'tool': name, 'model': request['model'], 'cache_hit':cache_hit,
            'usage': {} if cache_hit else result.get('usage', {}),
            'elapsed_seconds':round(time.monotonic()-started,3)})+'\n')
    choice = result['choices'][0]
    if choice.get('finish_reason') == 'length':
        raise ValueError('企业模板规划输出截断')
    return html.parse_json(choice['message']['content'])


def plan_pages(folder, template, manuscript, demo=False):
    blocks = html.normalize_table_blocks(parse_markdown(manuscript))
    catalog = html.catalog(template)
    headings = [b for b in blocks if b['kind'] == 'heading' and b['level'] == 2 and not re.fullmatch(r'目录|目次|contents|agenda',b['text'].strip(),re.I)]
    agenda = [{'id': f'a{i+1}', 'text': b['text'], 'number': i+1} for i, b in enumerate(headings)]
    if not agenda:
        agenda = [{'id': 'a1', 'text': blocks[0]['text'], 'number': 1}]
    title = blocks[0]['text'][:160]
    planned, corrections = [], []
    groups = batches(blocks)
    for i, batch in enumerate(groups):
        payload = {'title': title, 'blocks': batch, 'templates': catalog, 'agenda': agenda,
                   'first_batch': i == 0, 'last_batch': i == len(groups)-1,
                   'page_budget': 200-len(planned)}
        error = ''
        if not demo:
            for attempt in range(2):
                try:
                    answer = model_json(folder, 'enterprise_plan', PLAN_PROMPT+'\n'+SKILL.read_text(), {**payload, 'validation_feedback': error})
                    pages = html.parse_plan(json.dumps(answer, ensure_ascii=False), batch, catalog, i == 0, i == len(groups)-1, agenda)
                    break
                except (ValueError, KeyError, TypeError) as exc:
                    error = str(exc)
            else:
                pages = None
        else:
            pages = None
        if pages is None:
            # Preserve every source block when model structure is unusable.
            pick = lambda role: next(p['template_page'] for p in catalog if p['role'] == role)
            body = max((c for c in components.inspect(template)['layouts'] if c['role'] == 'body'), key=lambda c: c['capacity'])['template_page']
            pages = []
            if i == 0:
                pages.append({'role': 'cover', 'template_page': pick('cover'), 'title': title})
                if any(c['role'] == 'contents' for c in catalog):
                    pages.append({'role': 'contents', 'template_page': pick('contents'), 'title': '目录', 'agenda_ids': [a['id'] for a in agenda]})
            pages.append({'role': 'body', 'template_page': body, 'title': next((b['text'] for b in batch if b['kind'] == 'heading'), '正文'), 'block_ids': [b['id'] for b in batch]})
            if i == len(groups)-1:
                pages.append({'role': 'ending', 'template_page': pick('ending'), 'title': '谢谢'})
            corrections.append({'batch': i+1, 'reason': error or '演示模式', 'action': '使用容量优先的原文排版，未删减正文'})
        planned.extend(pages)
    if not headings:
        # A short manuscript may have no H2 chapters. Its directory should
        # describe the actual body groups, not repeat the deck title in pieces.
        titles = list(dict.fromkeys(p['title'] for p in planned if p['role']=='body'))
        agenda = [{'id':f'a{i+1}','text':title,'number':i+1} for i,title in enumerate(titles)]
        contents_seen = False; adjusted = []
        for index,p in enumerate(planned):
            if p['role']=='contents':
                if contents_seen: continue
                p['agenda_ids']=[a['id'] for a in agenda]; contents_seen=True
            if p['role']=='section':
                following = next((b for b in planned[index+1:] if b['role']=='body'),None)
                if following:
                    p['title']=following['title'];p['section_number']=titles.index(following['title'])+1
            adjusted.append(p)
        planned=adjusted
    for p in planned:
        if p['role']=='contents':p['title']='目录'
    try:
        fixed.agenda_labels(agenda,template,None if demo else lambda policy,payload:model_json(folder,'enterprise_agenda_labels',policy,payload))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        fixed.agenda_labels(agenda,template)
        corrections.append({'action':'目录短标题使用通用章节标签，完整章节名保留在说明区域','reason':str(exc)[:200]})
    html.validate_deck(planned, catalog)
    return {'blocks': blocks, 'agenda': agenda, 'planned': planned, 'corrections': corrections, 'title': title}


def render_group(template, proposal, source, *, page_number=1, scale=1):
    blocks = [b for key in proposal.get('block_ids', []) for b in source['blocks'] if b['id'] == key]
    agenda = [a for key in proposal.get('agenda_ids', []) for a in source['agenda'] if a['id'] == key]
    return fill.render_pages(template, proposal, blocks, agenda, page_number=page_number, scale=scale,
                             headers=html.table_headers(source['blocks']))


def check_all(template, pages, source):
    for p in pages:
        if 'layout_spec' in p:
            expected = adaptive.compile_body(template, deepcopy(p))
            if p['html'] != expected:
                raise ValueError('正文页面与已验证的布局、原文或企业固定区域不一致')
        else:
            html.validate_html(p['html'], html.template_html(template, p['template_page']))
    agenda = source['agenda'] if any(p['role'] == 'contents' for p in pages) else []
    html.validate_sources([p['html'] for p in pages], source['blocks'], agenda, html.table_headers(source['blocks']))
    html.validate_deck(pages, html.catalog(template))


def run_renderer(folder, probe=False):
    with (folder/'renderer.log').open('a') as log:
        result = subprocess.run([os.getenv('PRESENTATION_NODE', 'node'), os.getenv('MARKETING_PPT_RENDERER',str(ROOT/'enterprise.mjs')), str(folder),
                                 'probe' if probe else 'export'], stdout=log, stderr=subprocess.STDOUT, timeout=900)
    if result.returncode:
        raise ValueError('企业模板渲染失败：'+(folder/'renderer.log').read_text()[-1600:])
    return json.loads((folder/'enterprise-probe.json').read_text())


def execute(jobs, job_id, agent):
    """Optional enterprise skill on the project presentation agent."""
    if os.getenv('MARKETING_RUNTIME', 'demo') != 'demo':
        from .model_html import execute as execute_model_html
        return execute_model_html(jobs, job_id, agent)
    return execute_demo(jobs, job_id, agent)


def execute_demo(jobs, job_id, agent):
    folder = jobs.root/job_id
    original_template = json.loads((folder/'template.json').read_text())
    manuscript = (folder/'manuscript.md').read_text()
    template = adaptive.prepare(fixed.prepare(original_template,fixed.metadata(manuscript)))
    (folder/'enterprise-design-contract.json').write_text(json.dumps({
        'mode': 'enterprise-adaptive-body-v2', 'theme': template['_theme'],
        'body_contracts': template['_contracts'], 'fixed_pages': ['cover', 'contents', 'section', 'ending'],
        'metadata':template['_metadata'],'fixed_content_adaptations':template['_fixed_changes'],
        'labels': 'reference_only_for_body', 'original_snapshot': 'template.json'}, ensure_ascii=False, indent=2))
    options = jobs.get(job_id).get('options', {})
    demo = os.getenv('MARKETING_RUNTIME', 'demo') == 'demo'
    agent.state.update(skill='enterprise-deck', skill_sha256=hashlib.sha256(SKILL.read_bytes()).hexdigest())
    agent.save()
    jobs.update(job_id, stage='designing', agent_owner='project_presentation_agent', template_id=template['id'],
                template_revision=options['template_revision'], content_mode='full_text', image_count=0,
                enterprise_layout_mode='adaptive-body-v2')
    source = agent.call('plan_enterprise_deck', plan_pages, folder, template, manuscript, demo)
    groups, scales = deepcopy(source['planned']), [1.0]*len(source['planned'])
    corrections = [*template['_fixed_changes'], *source['corrections']]
    for block in source['blocks']:
        if block['kind'] == 'table':
            block['table_header'] = html.table_headers(source['blocks']).get(block['id'])

    def save_corrections():
        (folder/'corrections.json').write_text(json.dumps(corrections, ensure_ascii=False, indent=2))
        (folder/'corrections.md').write_text('# 企业模板生成调整日志\n\n'+('\n\n'.join(json.dumps(c, ensure_ascii=False) for c in corrections) or '未发生内容或排版回退。'))
        jobs.update(job_id, corrections_available=True, correction_count=len(corrections))

    save_corrections()
    layout_calls = 0
    layout_limit = max(0, min(400, int(os.getenv('MARKETING_ENTERPRISE_LAYOUT_MAX_CALLS', '80'))))

    def design_body(page, finding=None):
        nonlocal layout_calls
        frame = template['_contracts'][str(page['template_page'])]['frame']
        page.setdefault('layout_spec', adaptive.fallback_spec(page['body_parts'], frame))
        if not demo:
            feedback = finding
            for attempt in range(2):
                try:
                    if layout_calls >= layout_limit:
                        raise ValueError('已达到本次任务的正文设计调用上限')
                    layout_calls += 1
                    page['layout_spec'] = agent.call('design_enterprise_body', adaptive.design, template, page,
                        lambda policy, payload: model_json(folder, 'enterprise_body_layout', policy, payload), feedback)
                    page['design_status'] = 'model_designed'
                    break
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    feedback = {'issue': str(exc)[:1500], 'previous_finding': finding}
            else:
                page['design_status'] = 'fallback'
                corrections.append({'template_page': page['template_page']+1, 'action': '正文模型设计未通过，保留完整原文基础版式', 'reason': feedback})
                save_corrections()
        else:
            page['design_status'] = 'demo'
        page['html'] = adaptive.compile_body(template, page)
        return page

    def make_pages():
        pages = []
        for gi, proposal in enumerate(groups):
            if proposal['role'] == 'body':
                blocks = [b for key in proposal['block_ids'] for b in source['blocks'] if b['id'] == key]
                frame = template['_contracts'][str(proposal['template_page'])]['frame']
                for parts in adaptive.paginate(blocks, frame):
                    pages.append(design_body({**proposal, 'layout': 'body', 'group': gi, 'body_parts': parts}))
                continue
            try:
                documents = agent.call('fill_enterprise_fields', render_group, template, proposal, source,
                                       page_number=len(pages)+1, scale=scales[gi])
            except fill.TitleFitError:
                if demo:
                    raise
                limit = fill.title_capacity(template, proposal['template_page'])
                shortened = model_json(folder, 'fit_enterprise_title', '只返回JSON {"title":"短展示标题"}。忠实概括原题，不添加事实；原题仍完整保留在正文和备注。',
                                       {'original_title': proposal['title'], 'max_characters': limit})
                before = proposal['title']
                if not isinstance(shortened.get('title'), str) or not shortened['title'].strip():
                    raise ValueError('模型未返回有效展示标题')
                proposal['title'] = shortened['title']
                corrections.append({'group': gi+1, 'before': before, 'after': proposal['title'], 'action': '标题适配；完整原题保留在正文和备注'})
                save_corrections()
                documents = render_group(template, proposal, source, page_number=len(pages)+1, scale=scales[gi])
            pages.extend({**proposal, 'layout': proposal['role'], 'html': d, 'group': gi} for d in documents)
        check_all(template, pages, source)
        check_page_count(len(pages), options)
        plan = {'title': source['title'], 'design_mode': 'enterprise', 'enterprise_layout_mode': 'adaptive-body-v2',
                'canvas': {'width': template['width'], 'height': template['height']},
                'theme': {**template['_theme'], 'template_name': template['name']}, 'style_id': 'enterprise', 'pages': pages,
                'source_blocks': source['blocks'], 'source_run_id': jobs.get(job_id)['run_id'],
                'source_sha256': jobs.get(job_id)['source_sha256'], 'template_id': template['id'],
                'template_revision': options['template_revision']}
        (folder/'plan.json').write_text(json.dumps(plan, ensure_ascii=False))
        return plan

    jobs.update(job_id, stage='page_generating')
    plan = make_pages()
    # Render original template references in the same browser/font environment.
    reference_folder = folder/'template-reference'; reference_folder.mkdir(exist_ok=True)
    from .uploaded_templates import render as render_reference
    (reference_folder/'plan.json').write_text(json.dumps({'canvas': plan['canvas'], 'title': '企业模板参考', 'pages': [
        {'html': render_reference(original_template, p)} for p in original_template['pages']]}, ensure_ascii=False))
    agent.call('render_enterprise_reference', run_renderer, reference_folder, True)
    # Body geometry is model-owned inside the safe frame; fixed pages stay locked.
    for attempt in range(4):
        probe = agent.call('measure_enterprise_pages', run_renderer, folder, True)
        failures = [p for p in probe['pages'] if p['issues']]
        if not failures:
            break
        if attempt == 3:
            raise ValueError('企业模板排版仍有问题：'+json.dumps(failures[:3], ensure_ascii=False)[:1400])
        for failure in sorted(failures, key=lambda f: f['page'], reverse=True):
            pi = failure['page']-1; page = plan['pages'][pi]
            if page['role'] != 'body':
                raise ValueError(f'模板固定页「{page["title"]}」文字溢出或重叠，请在前端修改模板字段或标题：'+json.dumps(failure, ensure_ascii=False)[:900])
            if attempt < 2:
                plan['pages'][pi] = design_body(deepcopy(page), failure)
                corrections.append({'page': pi+1, 'action': '项目模型根据浏览器问题重排整个正文区', 'finding': failure})
            else:
                parts = page['body_parts']
                if len(parts) == 1:
                    part = parts[0]
                    if part['kind'] == 'table':
                        raise ValueError('表格单页仍超出内容区，请拆分列或减少同页内容：'+json.dumps(failure, ensure_ascii=False)[:800])
                    parts = adaptive.fragments(parts, max(1, len(part['text'])//2))
                middle = max(1, len(parts)//2)
                replacement = []
                for subset in (parts[:middle], parts[middle:]):
                    if subset:
                        new = {k: deepcopy(v) for k,v in page.items() if k not in {'html', 'layout_spec', 'enterprise_charts'}}
                        new['body_parts'] = subset; replacement.append(design_body(new))
                plan['pages'][pi:pi+1] = replacement
                corrections.append({'page': pi+1, 'action': '正文仍拥挤，保留全文拆分续页'})
        save_corrections()
        check_all(template, plan['pages'], source)
        check_page_count(len(plan['pages']), options)
        (folder/'plan.json').write_text(json.dumps(plan, ensure_ascii=False))

    jobs.update(job_id, stage='rendering')
    agent.call('export_enterprise_pptx', run_renderer, folder)
    visual = {'status': 'disabled', 'pages': [], 'repairs': [], 'notice': '截图已生成；视觉模型复核未启用'}
    if not demo and os.getenv('MARKETING_VISION_ENABLED', 'false').lower() == 'true':
        from ..presentation_vision import review_batch
        jobs.update(job_id, stage='visual_review')
        try:
            for index in range(1, len(plan['pages'])+1):
                visual['pages'].extend(agent.call('review_enterprise_screenshots', review_batch, folder, plan, [index]))
            # Only body text patches can be proposed. Invalid changes roll back.
            for finding in visual['pages']:
                if finding['verdict'] == 'fix' and plan['pages'][finding['page']-1]['role'] != 'body':
                    visual['repairs'].append({'page': finding['page'], 'status': 'template_edit_required'})
            for round_number in (1, 2):
                from ..presentation_budget import can_reserve
                limit = max(0, min(1000, int(os.getenv('MARKETING_VISION_MAX_REQUESTS', '16'))))
                if not can_reserve(folder.parent/'vision-cache', '0.10', limit):
                    visual['repairs'].append({'round': round_number, 'status': 'budget_exhausted'})
                    break
                findings = [p for p in visual['pages'] if plan['pages'][p['page']-1]['role'] == 'body'
                            and any(i['severity'] in {'high', 'medium'} for i in p['issues'])][:3]
                if not findings:
                    break
                for finding in findings:
                    index = finding['page']-1
                    if plan['pages'][index]['role'] != 'body':
                        visual['repairs'].append({'round': round_number, 'page': index+1, 'status': 'template_edit_required'})
                        continue
                    before = deepcopy(plan)
                    try:
                        plan['pages'][index]['html'] = agent.call('repair_enterprise_text_regions', repair_text_regions, folder, template, plan['pages'][index], finding)
                        check_all(template, plan['pages'], source)
                        (folder/'plan.json').write_text(json.dumps(plan, ensure_ascii=False))
                        measured = run_renderer(folder, True)
                        if any(p['issues'] for p in measured['pages']):
                            raise ValueError('视觉修复未通过浏览器校验')
                        run_renderer(folder)
                        checked = review_batch(folder, plan, [index+1])[0]
                        if checked['verdict'] == 'fix':
                            raise ValueError('修复后仍需调整，回滚到原合格排版')
                        visual['pages'] = [checked if p['page'] == index+1 else p for p in visual['pages']]
                        visual['repairs'].append({'round': round_number, 'page': index+1, 'status': 'verified'})
                    except (ValueError, KeyError, TypeError, OSError) as exc:
                        plan = before
                        (folder/'plan.json').write_text(json.dumps(plan, ensure_ascii=False))
                        run_renderer(folder)
                        visual['repairs'].append({'round': round_number, 'page': index+1, 'status': 'rolled_back', 'reason': str(exc)[:250]})
            visual['status'] = 'needs_review' if any(p['verdict'] == 'fix' for p in visual['pages']) else 'passed'
            visual['notice'] = '项目视觉模型已复核全部页面；固定区域问题需在模板管理中处理' if visual['status'] == 'needs_review' else '项目视觉模型已复核全部页面'
        except (ValueError, KeyError, TypeError, OSError) as exc:
            visual.update(status='incomplete', notice='视觉复核未完成：'+str(exc)[:250])
    (folder/'visual-review.json').write_text(json.dumps(visual, ensure_ascii=False, indent=2))
    (folder/'corrections.json').write_text(json.dumps(corrections, ensure_ascii=False, indent=2))
    (folder/'corrections.md').write_text('# 企业模板生成调整日志\n\n'+('\n\n'.join(json.dumps(c, ensure_ascii=False) for c in corrections) or '未发生内容或排版回退。'))
    report = json.loads((folder/'report.json').read_text())
    from .service import font_status
    report['server_fonts'] = font_status(template)
    report['enterprise_layout_mode'] = 'adaptive-body-v2'
    report['body_design'] = {'model_calls': layout_calls, 'call_limit': layout_limit,
                           'pages': [{'page': i+1, 'status': p.get('design_status')} for i,p in enumerate(plan['pages']) if p['role']=='body']}
    report['visual_review'] = {'status': visual['status'], 'report': 'visual-review.json'}
    (folder/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    agent.finish('completed', page_count=len(plan['pages']), quality=visual['status'])
    jobs.update(job_id, status='completed', stage='completed', page_count=len(plan['pages']), checks=report['checks'],
                visual_status=visual['status'], visual_notice=visual['notice'], corrections_available=True,
                correction_count=len(corrections), repairs=len(corrections))


def repair_text_regions(folder, template, page, finding):
    """The model may reflow text islands; the host enforces exact text/brand styles."""
    if 'layout_spec' in page:
        page['layout_spec'] = adaptive.design(template, page,
            lambda policy, payload: model_json(folder, 'enterprise_visual_repair', policy, payload), finding)
        return adaptive.compile_body(template, page)
    original = BeautifulSoup(page['html'], 'html.parser')
    regions = {n['data-ppt-editable']: str(n) for n in original.select('[data-ppt-editable][data-edit-mode="body-text"]')}
    patch = model_json(folder, 'enterprise_visual_repair',
        '你是企业模板排版修复工具。只返回JSON {"regions":{"区域ID":"区域内部HTML"}}。'
        '只能在授权文字框内调整排布，逐字保留原文字及来源标记。保留字体与颜色；字号不能低于模板约束。不得添加图片、背景、装饰、脚本或修改区域坐标。',
        {'regions': regions, 'finding': finding, 'slots': html.template_slots(template, page['template_page'], page['html'])})
    if not isinstance(patch.get('regions'), dict) or not set(patch['regions']) <= set(regions):
        raise ValueError('修复包含未授权区域')
    for key, content in patch['regions'].items():
        node = original.select_one(f'[data-ppt-editable="{key}"]')
        inner = BeautifulSoup(content, 'html.parser')
        if re.sub(r'\s+', '', node.get_text()) != re.sub(r'\s+', '', inner.get_text()):
            raise ValueError('视觉修复修改了锁定文字')
        # No new typography colors or families; stronger than CSS allowlisting.
        styles = str(node)
        for declaration in re.findall(r'(?:font-family|color)\s*:[^;"<>]+', content, re.I):
            if declaration not in styles:
                raise ValueError('视觉修复修改了企业字体或颜色')
        node.clear()
        for child in list(inner.contents):
            node.append(child.extract())
    reference = html.template_html(template, page['template_page'])
    return html.validate_html(html.preserve_fixed_markup(str(original), reference), reference)
