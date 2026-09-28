"""文稿语义块、无损分页与持久化演示文稿任务。"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import presentation_images
from .presentation_styles import get_style, apply_style


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_VERSION = '3.0.0'


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
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1)

    def recover(self):
        for path in self.root.glob('*/job.json'):
            data = json.loads(path.read_text())
            if data['status'] in {'queued','running'}:
                data.update(status='failed', error='服务重启中断了PPT生成，请重新生成。', stage='interrupted')
                self._write(path, data)

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

    def create(self, run, style_id=None):
        if run['status'] != 'completed' or not run['outputs'].get('assembly'):
            raise ValueError('请先完成并确认完整文稿')
        text = run['outputs']['assembly']
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        style_id = style_id if style_id is not None else run.get('presentation_style', 'auto')
        style = get_style(style_id)
        with self.lock:
            last = self.latest(run['id'])
            if last and last['status'] in {'queued','running'}:
                if last.get('style_id', 'auto') != style_id:
                    raise ValueError('当前 PPT 正在生成，请完成后再切换风格')
                return last
            retryable=last and (last.get('image_notice') or last.get('visual_status')=='incomplete')
            if last and not retryable and last['status']=='completed' and last['source_sha256']==fingerprint and last.get('pipeline_version')==PIPELINE_VERSION and last.get('style_id', 'auto')==style_id:
                return last
            job_id = uuid.uuid4().hex
            folder = self.root/job_id
            folder.mkdir()
            (folder/'manuscript.md').write_text(text)
            job = {'id':job_id,'run_id':run['id'],'status':'queued','stage':'queued','created':time.time(),
                   'source_sha256':fingerprint,'source_runtime':run.get('runtime'),'page_count':0,'error':None,'pipeline_version':PIPELINE_VERSION,
                   'style_id':style_id,'style_name':style['name']}
            if last and (retryable or last['status']=='failed' or last.get('pipeline_version')!=PIPELINE_VERSION) and last['source_sha256']==fingerprint and last.get('style_id','auto')==style_id:
                for name in ('design-response-2.json','design-response-1.json','resumed-response.json'):
                    response=self.root/last['id']/name
                    if response.exists():
                        (folder/'resumed-response.json').write_bytes(response.read_bytes())
                        job['resumed_from']=last['id']
                        break
            self._write(folder/'job.json', job)
            self.pool.submit(self.execute, job_id)
            return job

    def execute(self, job_id):
        folder = self.root/job_id
        from .presentation_agent import PresentationAgent, analyze_reference
        agent=PresentationAgent(folder)
        try:
            self.update(job_id,status='running',stage='pagination')
            source = (folder/'manuscript.md').read_text()
            plan = agent.call('read_manuscript',plan_outline,source)
            job = self.get(job_id)
            plan['source_run_id'] = job['run_id']
            plan['source_runtime'] = job['source_runtime']
            plan['style_id'] = job.get('style_id', 'auto')
            plan['style'] = get_style(plan['style_id'])
            plan['theme'] = apply_style(plan['theme'], plan['style_id'])
            needs_cover=agent.call('prepare_assets',presentation_images.prepare_assets,plan,source,folder,ROOT/'presentation'/'assets')
            if os.getenv('MARKETING_RUNTIME','demo') != 'demo':
                self.update(job_id,stage='reference_analysis',agent_owner='project_presentation_agent')
                plan['style_reference']=agent.call('understand_reference',analyze_reference,folder,plan)
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
