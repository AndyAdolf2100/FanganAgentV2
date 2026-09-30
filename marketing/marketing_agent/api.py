import asyncio
import fcntl
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .engine import Engine
from .runtime import DeerFlowRuntime, DemoRuntime
from .store import Conflict, Store
from .workflow import GATE_TITLES, STAGES, TAGS
from .presentation import PresentationJobs
from .presentation_options import PresentationOptions
from .presentation_styles import STYLES, get_style
from .manuscripts import MAX_FILE_BYTES, MAX_TEXT_LENGTH, extract_manuscript, validate_manuscript

PROJECT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT / ".env.marketing")


class CreateRun(BaseModel):
    brief: str = Field(min_length=5, max_length=40000)
    mode: Literal["auto", "guided"] = "auto"
    c_tag: Literal["auto", "C001", "C002", "C003", "C004", "C005", "C006", "C008"] = "auto"
    knowledge_mode: Literal["both", "web", "local"] = "both"
    knowledge: str = Field(default="", max_length=100000)

    @model_validator(mode="after")
    def validate_text(self):
        if len(self.brief.strip()) < 5:
            raise ValueError("请输入有效营销需求")
        if self.knowledge_mode == "local" and not self.knowledge.strip():
            raise ValueError("仅本地模式需要提供知识材料")
        return self


class Feedback(BaseModel):
    action: Literal["accept", "revise"]
    text: str = Field(default="", max_length=20000)
    target: str | None = None


class ImportManuscript(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    manuscript: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    filename: str = Field(default='', max_length=255)
    style_id: str = 'auto'

    @model_validator(mode='after')
    def validate_import(self):
        if not self.title.strip():
            raise ValueError('请输入项目名称')
        validate_manuscript(self.manuscript)
        get_style(self.style_id)
        return self



def create_app(data_dir=None, runtime=None):
    directory = Path(data_dir or os.getenv("MARKETING_DATA_DIR", PROJECT / ".marketing-data"))
    directory.mkdir(parents=True, exist_ok=True)
    store = Store(directory / "marketing.sqlite3")
    is_demo = os.getenv("MARKETING_RUNTIME", "demo") == "demo" if runtime is None else isinstance(runtime, DemoRuntime)
    if runtime is None:
        backend = os.getenv("MARKETING_RUNTIME", "demo")
        if backend not in {"demo", "deerflow"}:
            raise ValueError("MARKETING_RUNTIME 必须为 demo 或 deerflow")
        runtime = DemoRuntime() if is_demo else DeerFlowRuntime(PROJECT / "config.marketing.yaml")
    engine = Engine(store, runtime)
    presentations = PresentationJobs(directory, store)
    from .enterprise.service import router_for
    pool = ThreadPoolExecutor(max_workers=1)  # DeerFlow configuration is process-global.

    @asynccontextmanager
    async def lifespan(app):
        lock = open(directory / "server.lock", "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("一个数据目录只能启动一个 API 进程；不要使用多个 uvicorn workers。")
        store.recover()
        presentations.recover()
        try:
            yield
        finally:
            pool.shutdown(wait=True)
            presentations.pool.shutdown(wait=True)
            lock.close()

    app = FastAPI(title="Marketing V2 Agent", lifespan=lifespan)
    app.state.store, app.state.engine = store, engine
    app.state.presentations = presentations
    app.include_router(router_for(presentations.templates))

    def get(run_id):
        try:
            return store.get(run_id)
        except KeyError:
            raise HTTPException(404, "任务不存在")

    def start(run_id):
        try:
            engine.claim(run_id)
        except Conflict as exc:
            raise HTTPException(409, str(exc))
        pool.submit(engine.execute, run_id)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "runtime": "demo" if is_demo else "deerflow", "upstream": "v2.0.0",
                "model_configured": bool(os.getenv("MARKETING_API_KEY")),
                "stages": [s.__dict__ for s in STAGES], "gates": GATE_TITLES, "tags": TAGS}

    @app.get("/api/runs")
    def list_runs():
        return [{k: r.get(k) for k in ("id", "brief", "status", "created", "mode", "index")} for r in store.list()]

    @app.get('/api/presentation-capabilities')
    def presentation_capabilities():
        return {'detailed_options': not is_demo, 'aspect_ratios': ['16:9'],
                'min_pages': 3, 'max_pages': 45, 'enterprise_templates': True,
                'enterprise_max_pages': 200, 'enterprise_content_mode': 'full_text'}

    @app.get('/api/presentation-styles')
    def presentation_styles():
        return [{k: value[k] for k in ('id', 'name', 'description', 'swatches')} for value in STYLES.values()]

    @app.post('/api/manuscripts/parse')
    async def parse_upload(request: Request, filename: str):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_FILE_BYTES:
                raise HTTPException(413, '文件不能超过 5 MB')
        try:
            return await asyncio.to_thread(extract_manuscript, filename, bytes(content))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post('/api/runs/import', status_code=201)
    def import_manuscript(body: ImportManuscript):
        return store.create({'brief': body.title.strip(), 'manuscript': body.manuscript,
                             'source_type': 'manuscript', 'source_filename': body.filename,
                             'presentation_style': body.style_id, 'mode': 'auto', 'c_tag': 'C008',
                             'knowledge_mode': 'local', 'knowledge': '',
                             'runtime': 'demo' if is_demo else 'deerflow'})

    @app.post("/api/runs", status_code=201)
    def create(body: CreateRun):
        if not is_demo and not os.getenv("MARKETING_API_KEY"):
            raise HTTPException(503, "尚未配置 MARKETING_API_KEY，真实运行未启动。")
        run = store.create({**body.model_dump(), "runtime": "demo" if is_demo else "deerflow"})
        start(run["id"])
        return get(run["id"])

    @app.get("/api/runs/{run_id}")
    def detail(run_id: str):
        return get(run_id)

    @app.post("/api/runs/{run_id}/feedback")
    def feedback(run_id: str, body: Feedback):
        existing = get(run_id)
        if existing.get('source_type') == 'manuscript':
            raise HTTPException(409, '导入文稿不进入营销策划流程；请修改原稿后重新导入')
        if existing["runtime"] != ("demo" if is_demo else "deerflow"):
            raise HTTPException(409, "运行模式已变更，请新建任务，避免混用演示与真实数据。")
        try:
            run = engine.feedback(run_id, body.action, body.text, body.target)
        except Conflict as exc:
            raise HTTPException(409, str(exc))
        if run["status"] == "ready":
            start(run_id)
        return get(run_id)

    @app.post("/api/runs/{run_id}/retry")
    def retry(run_id: str):
        run = get(run_id)
        if run.get('source_type') == 'manuscript':
            raise HTTPException(409, '请通过生成 PPT 按钮重试导入文稿')
        if run["runtime"] != ("demo" if is_demo else "deerflow"):
            raise HTTPException(409, "运行模式已变更，请新建任务，避免混用演示与真实数据。")
        start(run_id)
        return get(run_id)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str):
        run = get(run_id)
        if "assembly" not in run["outputs"] or run["index"] < len(STAGES) - 1:
            raise HTTPException(409, "完整方案尚未生成")
        return Response(run["outputs"]["assembly"], media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="marketing-{run_id[:8]}.md"'})

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0):
        get(run_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "无效的事件游标")

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(store.events, run_id, cursor)
                for event in batch:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: update\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                run = await asyncio.to_thread(store.get, run_id)
                if not batch and run["status"] in {"waiting", "completed", "failed"}:
                    yield 'event: idle\ndata: {}\n\n'
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post('/api/runs/{run_id}/presentation', status_code=202)
    def create_presentation(run_id: str, body: PresentationOptions | None = None):
        try:
            return presentations.create(get(run_id), body.style_id if body else None, body.requirements() if body else None)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get('/api/runs/{run_id}/presentation')
    def latest_presentation(run_id: str):
        run = get(run_id)
        job = presentations.latest(run_id)
        if job:
            job['stale'] = job['source_sha256'] != hashlib.sha256(run['outputs'].get('assembly','').encode()).hexdigest()
        return job

    @app.get('/api/presentations/{job_id}')
    def presentation_status(job_id: str):
        try:
            return presentations.get(job_id)
        except KeyError:
            raise HTTPException(404, 'PPT任务不存在')

    @app.get('/api/presentations/{job_id}/files/{filename}')
    def presentation_file(job_id: str, filename: str):
        job = presentation_status(job_id)
        allowed = {'presentation.pptx','presentation.html','outline.json','report.json','manuscript.md','visual-review.json','image-plan.json'}
        logs = {'corrections.md','corrections.json','agent-run.json','reference-analysis.json'}
        path = presentations.root/job_id/filename
        if filename not in allowed | logs or (filename not in logs and job['status']!='completed') or not path.is_file():
            raise HTTPException(404, '文件尚不可用')
        return FileResponse(path,filename=filename,
                            headers={'X-Content-Type-Options':'nosniff'})

    @app.get('/api/presentations/{job_id}/pages/{number}')
    def presentation_page(job_id: str, number: int):
        job = presentation_status(job_id)
        if job['status']!='completed' or number<1 or number>job['page_count']:
            raise HTTPException(404, '页面不存在')
        return FileResponse(presentations.root/job_id/'pages'/f'{number}.html',media_type='text/html',
                            headers={'Content-Security-Policy':"sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"})

    @app.get('/api/presentations/{job_id}/previews/{number}')
    def presentation_preview(job_id: str, number: int):
        job = presentation_status(job_id)
        if job['status']!='completed' or number<1 or number>job['page_count']:
            raise HTTPException(404, '页面不存在')
        return FileResponse(presentations.root/job_id/'previews'/f'{number}.png',media_type='image/png')

    return app


app = create_app()
