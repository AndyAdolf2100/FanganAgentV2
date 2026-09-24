import asyncio
import fcntl
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .engine import Engine
from .runtime import DeerFlowRuntime, DemoRuntime
from .store import Conflict, Store
from .workflow import GATE_TITLES, STAGES, TAGS

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
        try:
            yield
        finally:
            pool.shutdown(wait=True)
            lock.close()

    app = FastAPI(title="Marketing V2 Agent", lifespan=lifespan)
    app.state.store, app.state.engine = store, engine

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

    return app


app = create_app()
