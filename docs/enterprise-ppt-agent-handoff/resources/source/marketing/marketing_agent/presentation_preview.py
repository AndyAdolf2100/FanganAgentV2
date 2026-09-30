"""Read-only, on-demand draft previews; never starts/recoveries a generation job.

A separate lightweight process can be deployed while the project agent runs.
Generated HTML remains untouched; Chromium uses the same enterprise renderer.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

RENDERER = Path(__file__).resolve().parents[1]/'presentation/enterprise.mjs'


def render_draft(plan, destination):
    with tempfile.TemporaryDirectory(prefix='ppt-draft-') as directory:
        folder=Path(directory)
        (folder/'plan.json').write_text(json.dumps(plan,ensure_ascii=False))
        result=subprocess.run(['node',str(RENDERER),str(folder),'probe'],capture_output=True,text=True,timeout=60)
        if result.returncode:
            raise RuntimeError('草稿预览暂时无法渲染，请稍后重试')
        image=folder/'previews/1.png'
        if not image.is_file():raise RuntimeError('草稿截图尚不可用')
        temporary=destination.with_suffix('.tmp')
        temporary.write_bytes(image.read_bytes());temporary.replace(destination)


class DraftPreviews:
    def __init__(self, root, cache, render=render_draft):
        self.root=Path(root);self.cache=Path(cache);self.render=render
        self.snapshots=OrderedDict();self.lock=threading.RLock();self.render_lock=threading.Lock()

    def snapshot(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}',job_id):raise HTTPException(404,'PPT任务不存在')
        folder=self.root/job_id
        try:job=json.loads((folder/'job.json').read_text())
        except (OSError,ValueError):raise HTTPException(404,'PPT任务不存在') from None
        if not job.get('options',{}).get('template_id'):
            return job,None,[]
        path=folder/'plan.json'
        with self.lock:
            previous=self.snapshots.get(job_id)
            try:
                stat=path.stat();signature=(stat.st_mtime_ns,stat.st_size)
                if previous and previous[0]==signature:
                    self.snapshots.move_to_end(job_id)
                    return job,previous[1],previous[2]
                plan=json.loads(path.read_text())
                after=path.stat()
                if signature!=(after.st_mtime_ns,after.st_size):raise ValueError('snapshot still being written')
                if not isinstance(plan,dict) or not isinstance(plan.get('pages'),list):raise ValueError('pages not ready')
                pages=[]
                for index,page in enumerate(plan['pages']):
                    if not isinstance(page,dict):continue
                    if not page.get('model_html') or not isinstance(page.get('html'),str) or not page['html']:continue
                    revision=hashlib.sha256(json.dumps({'page':page,'canvas':plan['canvas'],'theme':plan.get('theme',{})},sort_keys=True).encode()).hexdigest()[:24]
                    pages.append({'number':index+1,'title':page.get('title',''),'revision':revision,
                                  'slide_id':page.get('slide_id'),'quality_state':page.get('quality_state')})
                self.snapshots[job_id]=(signature,plan,pages);self.snapshots.move_to_end(job_id)
                while len(self.snapshots)>2:self.snapshots.popitem(last=False)
                return job,plan,pages
            except (OSError,ValueError,KeyError,TypeError):
                # Existing in-flight workers predate atomic plan writes. Keep
                # their last complete snapshot while a newer one is being saved.
                return (job,previous[1],previous[2]) if previous else (job,None,[])

    def manifest(self,job_id):
        job,plan,pages=self.snapshot(job_id)
        optimization=None
        if job.get('optimization_of'):
            try:review=json.loads((self.root/job_id/'visual-review.json').read_text())
            except (OSError,ValueError):review={}
            verified={n for repair in review.get('repairs',[]) if repair.get('status')=='verified' for n in repair.get('pages',[])}
            progress=job.get('visual_progress') or {}
            active=set(progress.get('pages',[])) if progress.get('phase')=='repair' else {progress.get('page')} if progress.get('phase')=='recheck' else set()
            if job.get('status') not in {'queued','running'}:active=set()
            pages=[{**page,'optimization_state':'reviewing' if page['number'] in active else 'verified' if page['number'] in verified else 'retained'} for page in pages]
            optimization={'verified_pages':sorted(verified-active),'source_job_id':job['optimization_of']}
        if job.get('enterprise_workflow_version')==5:
            pages=[{**page,'optimization_state':'verified' if page.get('quality_state')=='accepted' else 'retained'} for page in pages]
        return {'job_id':job_id,'status':job['status'],'stage':job.get('stage'),
                'draft':job['status']!='completed' or job.get('quality_status')=='needs_review','available_pages':len(pages),'pages':pages,
                'quality_status':job.get('quality_status'),'ready_for_delivery':job.get('ready_for_delivery'),
                'optimization':optimization,
                'canvas':plan.get('canvas') if plan else None}

    def preview(self,job_id,number):
        _,plan,pages=self.snapshot(job_id)
        entry=next((p for p in pages if p['number']==number),None)
        if not entry:raise HTTPException(404,'该页尚未设计完成')
        self.cache.mkdir(parents=True,exist_ok=True)
        destination=self.cache/f'{job_id}-{entry["revision"]}.png'
        with self.render_lock:
            if not destination.is_file():
                page=deepcopy(plan['pages'][number-1])
                # Preview is presentation only, not a second QA/export run.
                # Browser findings never mutate the actual task or its reports.
                page['model_html']=False
                draft={'title':plan.get('title','草稿'),'canvas':plan['canvas'],'theme':plan.get('theme',{}),'pages':[page]}
                try:self.render(draft,destination)
                except (RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
                    raise HTTPException(503,'草稿预览暂时无法渲染，请稍后重试') from exc
                for old in sorted(self.cache.glob('*.png'),key=lambda p:p.stat().st_mtime,reverse=True)[200:]:
                    old.unlink(missing_ok=True)
        return destination


def create_app(root=None,cache=None,render=render_draft):
    app=FastAPI(title='Read-only PPT draft previews')
    reader=DraftPreviews(root or Path(os.getenv('MARKETING_DATA_DIR','/data/marketing'))/'presentations',
                         cache or Path(tempfile.gettempdir())/'marketing-draft-previews',render)
    app.state.previews=reader
    @app.get('/health')
    def health():return {'status':'ok','mode':'read_only_preview'}
    @app.get('/api/presentation-drafts/{job_id}')
    def manifest(job_id:str):
        return JSONResponse(reader.manifest(job_id),headers={'Cache-Control':'no-store'})
    @app.get('/api/presentation-drafts/{job_id}/previews/{number}')
    def preview(job_id:str,number:int):
        return FileResponse(reader.preview(job_id,number),media_type='image/png',
                            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    return app


app=create_app()
