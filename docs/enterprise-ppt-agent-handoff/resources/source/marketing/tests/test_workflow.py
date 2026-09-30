import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from marketing_agent.api import create_app
from marketing_agent.engine import Engine
from marketing_agent.prompts import stage_prompt, validate_plan
from marketing_agent.runtime import DemoRuntime
from marketing_agent.store import Conflict, Store
from marketing_agent.workflow import STAGES, TAGS


def request(**kw):
    return {"brief": "为新品茶饮策划抖音营销，预算300万元，周期两个月。", "mode": "auto",
            "c_tag": "auto", "knowledge_mode": "both", "knowledge": "", "runtime": "demo", **kw}


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "state.db")
    return store, Engine(store, DemoRuntime())


def execute(engine, run_id):
    engine.claim(run_id)
    engine.execute(run_id)


def test_autonomous_completion_and_restart(setup):
    store, engine = setup
    run = store.create(request())
    execute(engine, run["id"])
    result = Store(store.path).get(run["id"])
    assert result["status"] == "completed"
    assert list(result["outputs"]) == [s.key for s in STAGES]
    assert result["selected_theme"]
    assert "演示" in result["outputs"]["assembly"]
    assert len([e for e in store.events(run["id"]) if e["kind"] == "stage_completed"]) == 8


def test_all_guided_gates_theme_and_creative(setup):
    store, engine = setup
    run = store.create(request(mode="guided"))
    execute(engine, run["id"])
    for gate in ["brief_confirm", "outline_confirm", "research_confirm", "theme_confirm", "creative_feedback", "creative_confirm", "report_edit"]:
        r = store.get(run["id"])
        assert r["status"] == "waiting" and r["gate"] == gate
        text = "主题2" if gate == "theme_confirm" else "聚焦办公室场景" if gate == "creative_feedback" else ""
        r = engine.feedback(run["id"], "accept", text)
        if r["status"] == "ready":
            execute(engine, run["id"])
    r = store.get(run["id"])
    assert r["status"] == "completed"
    assert r["selected_theme"] == "主题2"
    assert "聚焦办公室场景" in r["outputs"]["creative"]


def test_revision_invalidates_downstream_preserves_history(setup):
    store, engine = setup
    run = store.create(request())
    execute(engine, run["id"])
    edited = engine.feedback(run["id"], "revise", "预算改为200万元", "brief_intake")
    assert list(edited["outputs"]) == ["brief_intake"]
    assert "assembly" in edited["history"][0]["outputs"]
    assert edited["selected_theme"] == ""
    execute(engine, run["id"])
    assert store.get(run["id"])["status"] == "completed"
    assert "预算改为200万元" in store.get(run["id"])["outputs"]["brief_intake"]


def test_duplicate_claim_is_rejected(setup):
    store, engine = setup
    run = store.create(request())
    def claim():
        try:
            engine.claim(run["id"])
            return True
        except Conflict:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == [False, True]


def test_failure_retry_keeps_completed_stages(setup):
    store, _ = setup
    class Flaky(DemoRuntime):
        fail = True
        def generate(self, stage, run, prompt, emit):
            if stage.key == "research" and self.fail:
                self.fail = False
                raise RuntimeError("搜索暂不可用")
            return super().generate(stage, run, prompt, emit)
    engine = Engine(store, Flaky())
    run = store.create(request())
    execute(engine, run["id"])
    failed = store.get(run["id"])
    assert failed["status"] == "failed" and failed["index"] == 2
    original = failed["outputs"].copy()
    execute(engine, run["id"])
    final = store.get(run["id"])
    assert final["status"] == "completed"
    assert all(final["outputs"][k] == v for k, v in original.items())


def test_interrupted_task_can_resume(setup):
    store, engine = setup
    run = store.create(request())
    engine.claim(run["id"])
    store.recover()
    assert store.get(run["id"])["status"] == "failed"
    execute(engine, run["id"])
    assert store.get(run["id"])["status"] == "completed"


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("mode", ["both", "web", "local"])
def test_all_migrated_prompts_render(setup, tag, mode):
    store, _ = setup
    run = store.create(request(c_tag=tag, knowledge_mode=mode, knowledge="品牌资料"))
    run["plan"] = validate_plan({"c_tag": tag})
    for stage in STAGES:
        prompt = stage_prompt(stage, run)
        assert "{{" not in prompt and "{%" not in prompt
        assert stage.title in prompt


def test_plan_filters_unavailable_modules():
    plan = validate_plan({"c_tag": "C001", "active_modules": {"A001_research": ["B001", "B999"]}})
    assert plan["active_modules"]["A001_research"] == ["B001"]


def test_api_validation_and_event_replay(tmp_path):
    with TestClient(create_app(tmp_path, DemoRuntime())) as client:
        assert client.get("/api/health").json()["runtime"] == "demo"
        assert client.post("/api/runs", json=request(knowledge_mode="local")).status_code == 422
        assert client.get("/api/runs/missing").status_code == 404
        # Drive engine synchronously to avoid sleeps in the test.
        store, engine = client.app.state.store, client.app.state.engine
        run = store.create(request(mode="guided"))
        execute(engine, run["id"])
        assert client.get(f"/api/runs/{run['id']}/export").status_code == 409
        assert client.post(f"/api/runs/{run['id']}/retry").status_code == 409
        response = client.get(f"/api/runs/{run['id']}/events")
        assert "event: idle" in response.text
        first_id = store.events(run["id"])[0]["id"]
        replay = client.get(f"/api/runs/{run['id']}/events", headers={"Last-Event-ID": str(first_id)})
        ids = [int(line[4:]) for line in replay.text.splitlines() if line.startswith("id: ")]
        assert ids and min(ids) > first_id
        assert client.post(f"/api/runs/{run['id']}/feedback", json={"action":"revise", "text":""}).status_code == 409


def test_secret_redaction(setup, monkeypatch):
    store, _ = setup
    monkeypatch.setenv("MARKETING_API_KEY", "secret-for-test-only")
    class Broken(DemoRuntime):
        def generate(self, *args):
            raise RuntimeError("provider rejected secret-for-test-only")
    run = store.create(request())
    execute(Engine(store, Broken()), run["id"])
    assert "secret-for-test-only" not in store.get(run["id"])["error"]
