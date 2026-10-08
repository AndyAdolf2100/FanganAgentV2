"""文稿语义块、无损分页与持久化演示文稿任务。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import presentation_images
from .presentation_styles import get_style, apply_style
from .presentation_options import PresentationOptions, apply_user_palette, check_page_count


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_VERSION = '3.0.0'
STYLE_RESEARCH_VERSION = 1
STYLE_RESEARCH_TTL_SECONDS = 7 * 24 * 60 * 60
STYLE_RESEARCH_RETRY_SECONDS = 60 * 60


def plain(text):
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)
    return re.sub(r"\*\*|__|`", "", text).strip()


def parse_manuscript(markdown):
    """保留原始块和稳定编号；表格行不按字符截断，链接原文进入备注。"""
    lines = markdown.splitlines()
    sections, blocks = [], []
    title, section, heading = "营销方案", "", ""
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            i += 1
            continue
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level, value = len(match[1]), plain(match[2])
            if level == 1 and not blocks and not sections:
                title = value
            else:
                if level <= 2:
                    section = value
                heading = value
                sections.append({"title": value, "section": section, "level": level, "block_start": len(blocks)})
            i += 1
            continue
        start = i
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i+1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [plain(c.replace('\\|', '|')) for c in re.split(r"(?<!\\)\|", lines[i].strip().strip('|'))]
                if not all(re.fullmatch(r":?-+:?", c.strip()) for c in row):
                    rows.append(row)
                i += 1
            width = max(map(len, rows))
            rows = [r + [''] * (width-len(r)) for r in rows]
            block = {"kind": "table", "header": rows[0], "rows": rows[1:]}
        else:
            # 列表项作为独立语义块；普通段落保留软换行。
            i += 1
            if not re.match(r"^(?:[-*+] |\d+[.、] |>)", line):
                while i < len(lines) and lines[i].strip() and not re.match(r"^(?:#|\||>|[-*+] |\d+[.、] )", lines[i].strip()):
                    i += 1
            raw = '\n'.join(lines[start:i])
            text = plain(re.sub(r"(?m)^>\s?", "", raw))
            block = {"kind": "text", "text": text}
        block.update(id=f"b{len(blocks):04d}", section=section, heading=heading or title,
                     source='\n'.join(lines[start:i]), source_line=start+1)
        blocks.append(block)
    if not blocks:
        raise ValueError("文稿没有可分页的正文")
    return {"title": title, "sections": sections, "blocks": blocks}


def plan_outline(markdown):
    parsed = parse_manuscript(markdown)
    pages, current = [], None
    for block in parsed['blocks']:
        heading = block['heading']
        # 每个小节独立起页，后续由真实渲染决定分页，避免凭字符数丢内容。
        if current is None or current['title'] != heading or block['kind'] == 'table':
            current = {"title": heading, "section": block['section'], "layout": "table" if block['kind']=='table' else "editorial", "blocks": []}
            pages.append(current)
        current['blocks'].append(dict(block))
        if block['kind'] == 'table':
            current = None
    return {"version": 1, "title": parsed['title'], "canvas": {"width":1280,"height":720},
            "source_sha256": hashlib.sha256(markdown.encode()).hexdigest(), "source_blocks": parsed['blocks'],
            "pages": pages, "assets": {}, "theme": {"background":"F7F6EF","text":"173B2B","accent":"476A34","muted":"526451","font":"Noto Sans CJK SC"}}


class PresentationJobs:
    def __init__(self, directory, store):
        self.root = Path(directory) / 'presentations'
        self.root.mkdir(exist_ok=True)
        self.store = store
        from .enterprise.service import TemplateLibrary
        self.templates = TemplateLibrary(directory)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        from .presentation_chat import PresentationChat
        self.chat = PresentationChat(self)

    def recover(self):
        for path in self.root.glob('*/job.json'):
            data = json.loads(path.read_text())
            if data['status'] in {'queued','running'}:
                data.update(status='failed', error='服务重启中断了PPT生成，请重新生成。', stage='interrupted')
                self._write(path, data)
        self.chat.recover()

    @staticmethod
    def _write(path, data):
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)

    def get(self, job_id):
        if not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise KeyError(job_id)
        path = self.root / job_id / 'job.json'
        if not path.exists():
            raise KeyError(job_id)
        data = json.loads(path.read_text())
        progress = path.parent/'progress.json'
        if data['status']=='running' and progress.exists():
            try:
                data['progress'] = json.loads(progress.read_text())
            except json.JSONDecodeError:
                pass
        return data

    def latest(self, run_id):
        matches = [json.loads(p.read_text()) for p in self.root.glob('*/job.json')]
        latest = max((j for j in matches if j['run_id']==run_id), key=lambda j:j['created'], default=None)
        return self.get(latest['id']) if latest else None

    def update(self, job_id, **values):
        with self.lock:
            job = self.get(job_id)
            job.update(values, updated=time.time())
            self._write(self.root/job_id/'job.json', job)
        self.store.event(job['run_id'], 'presentation', {k:job.get(k) for k in ('id','status','stage','page_count','error')})
        return job

    def create(self, run, style_id=None, options=None):
        options = PresentationOptions(**(options or {})).requirements()
        if run['status'] != 'completed' or not run['outputs'].get('assembly'):
            raise ValueError('请先完成并确认完整文稿')
        text = run['outputs']['assembly']
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        style_id = style_id if style_id is not None else run.get('presentation_style', 'auto')
        style = get_style(style_id)
        template = None
        if options.get('template_id'):
            try:
                template = self.templates.snapshot(options['template_id'], options['template_revision'])
            except FileNotFoundError:
                raise ValueError('企业模板版本不存在，请重新选择') from None
            style_id = 'auto'
            style = {'name': template['name'] + ' · v' + str(options['template_revision'])}
        pipeline_version = PIPELINE_VERSION + ('-enterprise-html-v5' if template else '')
        research_required = not template and os.getenv('MARKETING_RUNTIME', 'demo') != 'demo'
        with self.lock:
            last = self.latest(run['id'])
            if last and last['status'] == 'awaiting_outline_confirmation' and last.get('source_sha256') == fingerprint and last.get('style_id', 'auto') == style_id and last.get('options', {}) == options:
                return last
            if last and last['status'] in {'queued','running'}:
                if last.get('style_id', 'auto') != style_id or last.get('options', {}) != options:
                    raise ValueError('当前 PPT 正在生成，请完成后再切换风格或页面要求')
                return last
            retryable=last and (last.get('image_notice') or last.get('visual_status')=='incomplete' or last.get('quality_status')=='needs_review')
            research_ttl = (STYLE_RESEARCH_TTL_SECONDS if last and last.get('style_research_status') in {'analyzed', 'search_only'}
                            else STYLE_RESEARCH_RETRY_SECONDS)
            research_fresh = not research_required or (last and last.get('style_research_version') == STYLE_RESEARCH_VERSION
                and time.time() - last.get('created', 0) < research_ttl)
            if last and not retryable and research_fresh and last['status']=='completed' and last['source_sha256']==fingerprint and last.get('pipeline_version')==pipeline_version and last.get('style_id', 'auto')==style_id and last.get('options', {})==options:
                return last
            job_id = uuid.uuid4().hex
            folder = self.root/job_id
            folder.mkdir()
            (folder/'manuscript.md').write_text(text)
            if template:
                self._write(folder/'template.json', template)
            job = {'id':job_id,'run_id':run['id'],'status':'queued','stage':'queued','created':time.time(),
                   'source_sha256':fingerprint,'source_runtime':run.get('runtime'),'page_count':0,'error':None,'pipeline_version':pipeline_version,
                   'style_id':style_id,'style_name':style['name'],'options':options}
            if research_required:
                job['style_research_version'] = STYLE_RESEARCH_VERSION
            if template:
                job['enterprise_workflow_version']=5
                job['outline_review_required']=os.getenv('MARKETING_RUNTIME', 'demo') != 'demo'
            if last and (retryable or last['status']=='failed' or last.get('pipeline_version')!=pipeline_version) and last['source_sha256']==fingerprint and last.get('style_id','auto')==style_id and last.get('options', {})==options:
                for name in ('design-response-2.json','design-response-1.json','resumed-response.json'):
                    response=self.root/last['id']/name
                    if response.exists():
                        (folder/'resumed-response.json').write_bytes(response.read_bytes())
                        job['resumed_from']=last['id']
                        break
            self._write(folder/'job.json', job)
            self.pool.submit(self.execute, job_id)
            return job

    def outline(self, job_id):
        job = self.get(job_id)
        if job.get('enterprise_workflow_version') != 5:
            raise ValueError('只有企业模板任务提供生成前大纲')
        path = self.root / job_id / 'source-plan.json'
        if not path.is_file():
            raise ValueError('项目 Agent 尚未完成大纲规划')
        source = json.loads(path.read_text())
        blocks = {str(row.get('id')): row for row in source.get('blocks', [])}
        pages = []
        for number, item in enumerate(source.get('planned', []), 1):
            block_ids = item.get('block_ids') or []
            preview = ' '.join(str(blocks.get(str(block_id), {}).get('text') or '') for block_id in block_ids)
            pages.append({'number': number, 'role': item.get('role'), 'title': item.get('title'),
                          'template_page': item.get('template_page'), 'source_preview': preview[:180]})
        approved = job.get('outline_approval') or {}
        return {'job_id': job_id, 'title': source.get('title'), 'agenda': source.get('agenda') or [],
                'planned_page_count': len(pages), 'approved_page_count': approved.get('page_count'),
                'actual_page_count': job.get('page_count') if job.get('status') == 'completed' else None,
                'pages': pages, 'status': job['status']}

    def confirm_outline(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job.get('enterprise_workflow_version') != 5 or not job.get('outline_review_required'):
                raise ValueError('此任务不需要确认大纲')
            if job['status'] != 'awaiting_outline_confirmation':
                raise ValueError('当前任务不在等待确认大纲阶段')
            if self.latest(job['run_id'])['id'] != job_id:
                raise ValueError('这不是项目的最新 PPT 任务')
            current = self.store.get(job['run_id'])['outputs'].get('assembly', '')
            if hashlib.sha256(current.encode()).hexdigest() != job['source_sha256']:
                raise ValueError('文稿已更新，请重新规划大纲')
            path = self.root / job_id / 'source-plan.json'
            source = json.loads(path.read_text())
            count = len(source.get('planned') or [])
            if not count:
                raise ValueError('大纲没有页面，不能继续生成')
            approval = {'source_plan_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'page_count': count, 'approved_at': time.time()}
            job = self.update(job_id, outline_approval=approval, status='queued', stage='queued', error=None)
            self.pool.submit(self.execute, job_id)
            return job

    def optimize(self,job_id,feedback=None):
        """Create a visible version in the same project; preserve the source job."""
        from .enterprise.refinement import source_snapshot,merge_feedback
        with self.lock:
            original=self.get(job_id);source=self.root/job_id
            latest=self.latest(original['run_id'])
            if latest and latest['status'] in {'queued','running'}:return latest
            if original['status'] not in {'completed','failed'} or not (source/'template.json').exists():
                raise ValueError('目前仅支持优化已有完整企业模板PPT')
            plan_bytes=(source/'plan.json').read_bytes()
            plan=json.loads(plan_bytes)
            if not plan.get('pages'):raise ValueError('原任务没有完整页面')
            snapshot=source_snapshot(source,plan)
            feedback=feedback or []
            merge_feedback([{'page':i+1,'verdict':'pass','issues':[]} for i in range(len(plan['pages']))],feedback)
            new_id=uuid.uuid4().hex;folder=self.root/new_id;folder.mkdir()
            (folder/'plan.json').write_bytes(plan_bytes)
            (folder/'refinement-base-plan.json').write_bytes(plan_bytes)
            for name in ('template.json','manuscript.md','enterprise-design-contract.json'):
                shutil.copyfile(source/name,folder/name)
            for name in ('template-reference','previews'):
                if (source/name).exists():shutil.copytree(source/name,folder/name)
            self._write(folder/'source-plan.json',snapshot)
            self._write(folder/'reviewer-feedback.json',feedback)
            self._write(folder/'refinement-input.json',{'source_job_id':job_id,'source_plan_sha256':hashlib.sha256(plan_bytes).hexdigest()})
            keys=('run_id','source_sha256','source_runtime','pipeline_version','style_id','style_name','options')
            job={k:original.get(k) for k in keys}
            job.update(id=new_id,status='queued',stage='queued',created=time.time(),page_count=len(plan['pages']),error=None,
                       optimization_of=job_id,agent_owner='project_presentation_agent',visual_notice='项目Agent将在原稿基础上逐页审查与优化',
                       enterprise_workflow_version=5,pipeline_version=PIPELINE_VERSION+'-enterprise-html-v5')
            if original.get('outline_approval'):
                job['outline_approval'] = original['outline_approval']
            self._write(folder/'job.json',job)
            self.pool.submit(self.execute,new_id)
            return job

    def resume_optimization(self,job_id):
        with self.lock:
            job=self.get(job_id)
            if job['status'] in {'queued','running'}:return job
            resumable=job['status']=='failed' or (job['status']=='completed' and job.get('visual_status') in {'needs_review','incomplete'})
            if not resumable or (job.get('enterprise_workflow_version')!=5 and not (self.root/job_id/'refinement-input.json').exists()):
                raise ValueError('只能继续中断或待复核的企业任务')
            pending_chat = self.chat.pending_modifications(job_id)
            if pending_chat and os.getenv('MARKETING_VISION_ENABLED', 'false').lower() == 'true':
                from decimal import Decimal
                from .presentation_budget import can_reserve
                from .presentation_vision import review_reservation
                price = review_reservation()
                limit = max(0, min(1000, int(os.getenv('MARKETING_VISION_MAX_REQUESTS', '16'))))
                if not can_reserve(self.root / 'vision-cache', Decimal(price) * pending_chat, limit,
                                   requests=pending_chat):
                    raise ValueError(f'项目图片/视觉预算不足 {pending_chat} 张指定页面各一次复查（当前模型每次预留 {price} 元）；修改意见已保留，调整预算后继续当前任务。')
            folder=self.root/job_id;history=folder/'resume-history'/str(time.time_ns());history.mkdir(parents=True)
            for name in ('job.json','agent-run.json','visual-review.json','plan.json'):
                if (folder/name).exists():shutil.copyfile(folder/name,history/name)
            retried = self.chat.retry_unverified(job_id) if pending_chat else 0
            job=self.update(job_id,status='queued',stage='queued',error=None,resume_requested_at=time.time(),
                chat_resume=bool(pending_chat or retried))
            self.pool.submit(self.execute,job_id)
            return job

    def execute(self, job_id):
        folder = self.root/job_id
        from .presentation_agent import PresentationAgent, analyze_reference
        agent=PresentationAgent(folder)
        try:
            self.update(job_id,status='running',stage='pagination')
            if (folder/'template.json').is_file():
                from .enterprise.workflow import execute
                execute(self, job_id, agent)
                return
            source = (folder/'manuscript.md').read_text()
            plan = agent.call('read_manuscript',plan_outline,source)
            job = self.get(job_id)
            plan['source_run_id'] = job['run_id']
            plan['source_runtime'] = job['source_runtime']
            plan['style_id'] = job.get('style_id', 'auto')
            plan['presentation_options'] = job.get('options', {})
            plan['style'] = get_style(plan['style_id'])
            plan['theme'] = apply_user_palette(apply_style(plan['theme'], plan['style_id']), plan['presentation_options'])
            needs_cover=agent.call('prepare_assets',presentation_images.prepare_assets,plan,source,folder,ROOT/'presentation'/'assets')
            if os.getenv('MARKETING_RUNTIME','demo') != 'demo':
                self.update(job_id,stage='reference_analysis',agent_owner='project_presentation_agent')
                plan['style_reference']=agent.call('understand_reference',analyze_reference,folder,plan)
                from .presentation_style_research import research_online_style
                self.update(job_id,stage='online_style_research')
                online=agent.call('research_online_style',research_online_style,folder,plan)
                plan['style_reference']['online_inspiration']={
                    'status':online['status'],
                    'visual_analysis':online['analysis'],
                    'case_descriptions':[{'id':item['id'],'description':item['summary'] or item['title']}
                                         for item in online['references'][:3]],
                }
                (folder/'reference-analysis.json').write_text(json.dumps(plan['style_reference'],ensure_ascii=False,indent=2))
                self.update(job_id,style_research_status=online['status'],style_reference_count=len(online['references']))
                from .presentation_design import plan_design
                self.update(job_id,stage='designing')
                design=agent.call('plan_outline',plan_design,plan,folder)
                self.update(job_id,stage='editing')
                from .presentation_editorial import refine_design
                design=agent.call('edit_deck',refine_design,design,plan,folder)
                plan.update(pages=design['pages'],theme=design['visual_dna'],design_mode='narrative',
                            visual_dna=design['visual_dna'],note_only_source_ids=design['note_only_source_ids'],
                            corrections=design.get('corrections',[]))
                self.update(job_id,correction_count=len(plan['corrections']),
                            warning_count=sum(c['status']=='warning' for c in plan['corrections']),corrections_available=True)
            check_page_count(len(plan['pages']), plan['presentation_options'])
            self.update(job_id,stage='images')
            agent.call('generate_or_reuse_images',presentation_images.materialize_assets,plan,folder,needs_cover)
            self.update(job_id,image_count=len(plan['assets']),image_notice=plan.get('image_notice'))
            (folder/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
            if plan.get('design_mode')=='narrative':
                from .presentation_pages import generate_custom_pages
                def page_progress(current,total,page):
                    self.update(job_id,stage='page_generating',page_progress={'current':current,'total':total,'page':page})
                page_result=agent.call('generate_pages',generate_custom_pages,folder,plan,page_progress)
                self.update(job_id,page_generation=page_result)
            self.update(job_id,stage='rendering')
            def render():
                with (folder/'renderer.log').open('a') as log:
                    result = subprocess.run([os.getenv('PRESENTATION_NODE','node'),str(ROOT/'presentation'/'render.mjs'),str(folder)],
                                            stdout=log,stderr=subprocess.STDOUT,timeout=900)
                if result.returncode:
                    raise RuntimeError('排版检查或导出失败：'+(folder/'renderer.log').read_text()[-1800:])
            agent.call('render_probe_export_screenshots',render)
            if plan.get('design_mode')=='narrative':
                from .presentation_vision import review_and_repair
                self.update(job_id,stage='visual_review')
                visual=agent.call('review_and_repair',review_and_repair,folder,lambda:agent.call('render_after_repair',render),lambda current,total:self.update(job_id,visual_progress={'current':current,'total':total}))
                self.update(job_id,visual_status=visual['status'],visual_notice=visual.get('notice'))
                plan=json.loads((folder/'plan.json').read_text())
            report = json.loads((folder/'report.json').read_text())
            if plan.get('design_mode')=='narrative':
                report['visual_review']={'status':visual['status'],'report':'visual-review.json'}
                (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            check_page_count(report['page_count'], plan['presentation_options'])
            if not report['passed']:
                raise RuntimeError('页面仍有高危排版问题，未发布PPT')
            if plan.get('design_mode')=='narrative':
                from .presentation_compat import write_log
                write_log(folder,plan)
                self.update(job_id,correction_count=len(plan['corrections']),
                            warning_count=sum(c['status']=='warning' for c in plan['corrections']))
            agent.finish('completed',quality=report.get('visual_review',{}),page_count=report['page_count'])
            self.update(job_id,status='completed',stage='completed',page_count=report['page_count'],
                        checks=report['checks'],repairs=report['repair_count'])
        except Exception as exc:
            agent.finish('failed',error=type(exc).__name__)
            self.update(job_id,status='failed',stage='failed',error=str(exc)[:2200])
        finally:
            self.chat.finished(job_id)
