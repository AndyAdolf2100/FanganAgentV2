from .prompts import stage_prompt
from .store import Conflict
from .workflow import STAGES


class Engine:
    def __init__(self, store, runtime):
        self.store, self.runtime = store, runtime

    def claim(self, run_id):
        def claim(r):
            if r["status"] not in ("ready", "failed"):
                raise Conflict("任务当前不可执行")
            r.update(status="running", error=None, revision=r["revision"] + 1)
        return self.store.mutate(run_id, claim, "started")

    def execute(self, run_id):
        try:
            while True:
                run = self.store.get(run_id)
                if run["status"] != "running":
                    return
                stage = STAGES[run["index"]]
                self.store.event(run_id, "stage_started", {"stage": stage.key, "title": stage.title})
                result = self.runtime.generate(stage, run, stage_prompt(stage, run),
                    lambda kind, data: self.store.event(run_id, kind, {"stage": stage.key, **data}))
                if not result.strip() or result.strip() == "¥":
                    raise ValueError("本阶段未生成有效内容，请检查需求或模型配置。")
                plan = None
                if stage.key == "brief_intake":
                    plan = self.runtime.plan({**run, "outputs": {**run["outputs"], stage.key: result}})

                def finish(r):
                    r["outputs"][stage.key] = result
                    if plan is not None:
                        r["plan"] = plan
                    if stage.gate and r["mode"] == "guided":
                        r.update(status="waiting", gate=stage.gate)
                    else:
                        if stage.key == "theme":
                            r["selected_theme"] = "自主模式：采用主题候选中的第一个主题方案。"
                        r["index"] += 1
                        if r["index"] == len(STAGES):
                            r.update(status="completed", gate=None)
                finished = self.store.mutate(run_id, finish, "stage_completed")
                if finished["status"] != "running":
                    return
        except Exception as exc:
            # Keep previous successful stages; no silent switch to demo or fake output.
            message = str(exc)
            import os
            for name, value in os.environ.items():
                if any(s in name.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) >= 8:
                    message = message.replace(value, "[REDACTED]")
            self.store.mutate(run_id, lambda r: r.update(status="failed", error=message[:2000]), "failed")

    def feedback(self, run_id, action, text="", target=None):
        def apply(r):
            if r["status"] not in ("waiting", "completed", "failed"):
                raise Conflict("请等待当前阶段完成后再操作")
            if action == "revise":
                if not text.strip():
                    raise Conflict("修改要求不能为空")
                key = target or STAGES[min(r["index"], len(STAGES) - 1)].key
                keys = [s.key for s in STAGES]
                if key not in keys or key not in r["outputs"]:
                    raise Conflict("只能修改已产出的阶段")
                index = keys.index(key)
                r["history"].append({"revision": r["revision"], "outputs": dict(r["outputs"]), "feedback": text})
                # Keep target output as edit context; invalidate every downstream artifact.
                r["outputs"] = {k: v for k, v in r["outputs"].items() if keys.index(k) <= index}
                r["feedback"].setdefault(key, []).append(text)
                r["index"] = index
                if index <= 4:
                    r["selected_theme"] = ""
                    r["creative_input"] = ""
                if index == 0:
                    r["plan"] = {}
                r.update(status="ready", gate=None, error=None)
            elif action == "accept":
                if r["status"] != "waiting":
                    raise Conflict("任务没有待确认内容")
                if r["gate"] == "theme_confirm":
                    if not text.strip():
                        raise Conflict("请填写主题编号或选择意见")
                    r.update(selected_theme=text, gate="creative_feedback")
                    return
                if r["gate"] == "creative_feedback":
                    r["creative_input"] = text
                r["index"] += 1
                r.update(gate=None, status="completed" if r["index"] == len(STAGES) else "ready")
            else:
                raise Conflict("未知操作")
        return self.store.mutate(run_id, apply, "feedback")
