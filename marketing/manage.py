#!/usr/bin/env python3
"""Local Docker lifecycle; no third-party dependencies required."""
import argparse
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Marketing V2 本地 Docker 管理")
    parser.add_argument("command", choices=["start", "stop", "status", "logs", "verify"])
    args = parser.parse_args()
    env = ROOT / ".env.marketing"
    if not env.exists():
        shutil.copyfile(ROOT / ".env.marketing.example", env)
        env.chmod(0o600)
    prefix = ["docker", "compose", "--env-file", str(env), "-f", str(ROOT / "compose.marketing.yaml")]
    commands = {"start": ["up", "-d", "--build"], "stop": ["stop"], "status": ["ps"], "logs": ["logs", "--tail", "100"]}
    if args.command != "verify":
        subprocess.run(prefix + commands[args.command], cwd=ROOT, check=True)
        return
    port = "18080"
    for line in env.read_text().splitlines():
        if line.startswith("MARKETING_PORT="):
            port = line.split("=", 1)[1].strip().strip('\"\'')
    base = f"http://127.0.0.1:{port}"
    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return json.load(response)
    health = get("/api/health")
    print(json.dumps(health, ensure_ascii=False, indent=2))
    if health["runtime"] != "demo":
        print("真实模式：健康检查通过，未自动发起消耗额度的生成。")
        return
    payload = {"brief": (ROOT / "marketing/examples/brief.txt").read_text(), "mode": "auto"}
    req = urllib.request.Request(base + "/api/runs", json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        run = json.load(response)
    for _ in range(60):
        run = get("/api/runs/" + run["id"])
        if run["status"] in {"completed", "failed"}:
            break
        time.sleep(0.5)
    if run["status"] != "completed" or len(run["outputs"]) != 8:
        raise SystemExit("流程验证失败：" + str(run.get("error") or run["status"]))
    with urllib.request.urlopen(base + f"/api/runs/{run['id']}/export") as response:
        assert "演示" in response.read().decode()
    print("PASS：8 阶段自主流程、状态保存和 Markdown 下载。打开 " + base)


if __name__ == "__main__":
    main()
