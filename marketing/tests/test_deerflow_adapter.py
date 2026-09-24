"""Real upstream harness, deterministic model/tool; no provider credentials/network."""
import uuid
from pathlib import Path

import pytest
import yaml

pytest.importorskip("deerflow.client")

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from marketing_agent.runtime import DeerFlowRuntime
from marketing_agent.workflow import STAGES


class ToolModel(FakeMessagesListChatModel):
    seen: list = []

    def bind_tools(self, tools, **kwargs):
        self.seen.append([t.name for t in tools])
        return self


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("DEER_FLOW_PROJECT_ROOT", str(root))
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "deerflow"))
    monkeypatch.setenv("MARKETING_API_KEY", "test-key-never-sent")
    monkeypatch.setenv("MARKETING_MODEL", "test-model")
    monkeypatch.setenv("MARKETING_BASE_URL", "http://127.0.0.1:1/v1")
    config = yaml.safe_load((root / "config.marketing.yaml").read_text())
    config["summarization"] = {"enabled": False}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    return DeerFlowRuntime(config_path)


def test_real_harness_tool_loop(runtime, monkeypatch):
    import deerflow.client
    calls = []

    @tool
    def web_search(query: str) -> str:
        """Search fixture evidence."""
        calls.append(query)
        return "受控测试资料：https://example.com/research"

    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": "茶饮趋势"}, "id": "call1", "type": "tool_call"}]),
        AIMessage(content="# 已完成调研\n受控测试，不是线上研究。"),
    ])
    monkeypatch.setattr(deerflow.client, "create_chat_model", lambda **kw: model)
    monkeypatch.setattr(deerflow.client.DeerFlowClient, "_get_tools", staticmethod(lambda **kw: [web_search]))
    events = []
    result = runtime.generate(STAGES[2], {"id": uuid.uuid4().hex, "revision": 1, "knowledge_mode": "web"},
                              "检索茶饮趋势并返回结论。", lambda kind, data: events.append((kind, data)))
    assert "已完成调研" in result
    assert calls == ["茶饮趋势"]
    assert any(kind == "tools" for kind, _ in events)
    assert any(kind == "tool_result" for kind, _ in events)


def test_local_mode_removes_web_tools(runtime, monkeypatch):
    import deerflow.client
    calls = []

    @tool
    def web_search(query: str) -> str:
        """Never called in local mode."""
        calls.append(query)
        return "unreachable"

    model = ToolModel(responses=[AIMessage(content="仅根据本地资料输出。")])
    monkeypatch.setattr(deerflow.client, "create_chat_model", lambda **kw: model)
    monkeypatch.setattr(deerflow.client.DeerFlowClient, "_get_tools", staticmethod(lambda **kw: [web_search]))
    result = runtime.generate(STAGES[2], {"id": uuid.uuid4().hex, "revision": 1, "knowledge_mode": "local"},
                              "本地资料：茶饮品牌推广需求。", lambda *a: None)
    assert "本地资料" in result
    assert not calls
    assert all("web_search" not in names for names in model.seen)


def test_truncated_output_is_not_accepted(runtime, monkeypatch):
    import deerflow.client
    model = ToolModel(responses=[AIMessage(content="这是被截断的方案", response_metadata={"finish_reason": "length"})])
    monkeypatch.setattr(deerflow.client, "create_chat_model", lambda **kw: model)
    monkeypatch.setattr(deerflow.client.DeerFlowClient, "_get_tools", staticmethod(lambda **kw: []))
    with pytest.raises(RuntimeError, match="长度上限"):
        runtime.generate(STAGES[2], {"id": uuid.uuid4().hex, "revision": 1, "knowledge_mode": "local"},
                         "生成方案", lambda *a: None)


def test_provider_error_fallback_is_not_a_success(runtime, monkeypatch):
    import deerflow.client
    model = ToolModel(responses=[AIMessage(content="服务暂时不可用", additional_kwargs={
        "deerflow_error_fallback": True, "error_detail": "provider unavailable"})])
    monkeypatch.setattr(deerflow.client, "create_chat_model", lambda **kw: model)
    monkeypatch.setattr(deerflow.client.DeerFlowClient, "_get_tools", staticmethod(lambda **kw: []))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        runtime.generate(STAGES[2], {"id": uuid.uuid4().hex, "revision": 1, "knowledge_mode": "local"},
                         "生成方案", lambda *a: None)
