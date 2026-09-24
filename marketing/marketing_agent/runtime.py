import json
import os
import re
from typing import Callable

from .prompts import planning_prompt, validate_plan


class DemoRuntime:
    """Explicit fixtures for exercising the workflow. Never presented as research."""
    def plan(self, run):
        tag = run["c_tag"]
        if tag == "auto":
            tag = "C001" if "抖音" in run["brief"] else "C002" if "小红书" in run["brief"] else "C008"
        return validate_plan({"c_tag": tag, "rationale": "演示模式：按平台关键词选择模块。"}, tag)

    def generate(self, stage, run, prompt, emit: Callable):
        emit("runtime", {"message": "演示模式：未调用模型或搜索"})
        text = f"# {stage.title}\n\n> 演示数据，仅用于验证流程，不是实际营销方案。\n\n"
        if stage.key == "brief_intake":
            text += run["brief"]
        elif stage.key == "theme":
            text += "## 主题 1：让日常发生新鲜事\n场景化表达产品价值。\n\n## 主题 2：把热爱带进生活\n通过用户参与形成内容共创。"
        elif stage.key == "assembly":
            text += "\n\n".join(v for k, v in run["outputs"].items() if k != "assembly")
            text += "\n\n## KPI与预算\n待接入模型后，按真实预算核算；演示不提供虚构预测。"
        else:
            text += "## 执行要点\n根据已确认需求形成阶段产出。\n\n## 资料与假设\n真实数据、来源和成本待核实。"
        if run["feedback"].get(stage.key):
            text += "\n\n## 本次修改要求\n" + "\n".join(run["feedback"][stage.key])
        if stage.key == "creative":
            text += "\n\n主题选择：" + run["selected_theme"] + "\n\n创意方向：" + run["creative_input"]
        return text


class DeerFlowRuntime:
    def __init__(self, config_path):
        self.config_path = str(config_path)

    def client(self, knowledge_mode):
        from deerflow.client import DeerFlowClient
        from langchain.agents.middleware import AgentMiddleware
        from langgraph.checkpoint.memory import InMemorySaver

        allowed = {"web_search", "web_fetch"} if knowledge_mode != "local" else set()

        class ScopeTools(AgentMiddleware):
            # Filter both model-visible tools and actual dispatch; local means no web.
            @staticmethod
            def checked(response):
                for message in response.result:
                    metadata = getattr(message, "response_metadata", {})
                    if metadata.get("finish_reason") == "length" or metadata.get("stop_reason") == "max_tokens":
                        raise ValueError("模型输出达到长度上限，当前阶段未完成；请提高 max_tokens 或缩小本阶段范围后重试。")
                return response

            def wrap_model_call(self, request, handler):
                return self.checked(handler(request.override(tools=[t for t in request.tools if getattr(t, "name", None) in allowed])))

            async def awrap_model_call(self, request, handler):
                return self.checked(await handler(request.override(tools=[t for t in request.tools if getattr(t, "name", None) in allowed])))

            def wrap_tool_call(self, request, handler):
                if request.tool_call["name"] not in allowed:
                    raise ValueError("当前知识模式不允许该工具")
                return handler(request)

            async def awrap_tool_call(self, request, handler):
                if request.tool_call["name"] not in allowed:
                    raise ValueError("当前知识模式不允许该工具")
                return await handler(request)

        return DeerFlowClient(config_path=self.config_path, checkpointer=InMemorySaver(), thinking_enabled=False,
                              subagent_enabled=False, plan_mode=False, available_skills=set(),
                              middlewares=[ScopeTools()])

    def plan(self, run):
        answer = self.client("local").chat(planning_prompt(run),
                                           thread_id=f"{run['id']}-plan-{run['revision']}", recursion_limit=15)
        answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
        return validate_plan(json.loads(answer), run["c_tag"])

    def generate(self, stage, run, prompt, emit):
        chunks, last_id = {}, ""
        client = self.client(run["knowledge_mode"])
        for event in client.stream(prompt, thread_id=f"{run['id']}-{stage.key}-{run['revision']}",
                                   recursion_limit=int(os.getenv("MARKETING_RECURSION_LIMIT", "80"))):
            messages = event.data.get("messages", []) if event.type == "values" else [event.data]
            for message in messages:
                extra = message.get("additional_kwargs", {})
                if extra.get("deerflow_error_fallback"):
                    raise RuntimeError(extra.get("error_detail") or "模型调用失败，本阶段未完成")
            if event.type == "error":
                raise RuntimeError(str(event.data))
            if event.type == "messages-tuple":
                data = event.data
                if data.get("type") == "ai" and data.get("content"):
                    last_id = data.get("id") or "final"
                    chunks.setdefault(last_id, []).append(data["content"])
                    emit("delta", {"message_id": last_id, "text": data["content"]})
                if data.get("tool_calls"):
                    emit("tools", {"calls": [{"name": t.get("name"), "args": t.get("args")} for t in data["tool_calls"]]})
                if data.get("type") == "tool":
                    emit("tool_result", {"name": data.get("name"), "content": str(data.get("content", ""))[:12000]})
            elif event.type == "values" and event.data.get("__interrupt__"):
                raise RuntimeError("模型要求额外确认，本阶段未完成；请补充需求后重试。")
            elif event.type == "end":
                emit("usage", event.data)
        return "".join(chunks.get(last_id, []))
