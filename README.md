# Marketing V2 Agent

独立运行的中文营销工作台，基于 **DeerFlow v2.0.0** 官方源码（commit `7e7f0410797693cf882594555ba414e0361d4c6f`），使用官方 `deerflow.client.DeerFlowClient` 执行各阶段的多轮模型与工具调用。

业务流程与提示词迁移自同级 FanganAgent 项目的 `src/marketing_v2_graph/` 和 `src/prompts/marketing_v2*.md`。保留上游源码及 MIT LICENSE；上游说明见 [README.upstream.md](README.upstream.md)。本项目的启动入口是下方命令，无需运行上游的 `make up`。

## 本地 Docker 启动

前提：Docker Desktop 正在运行，宿主机有 Python 3。首次构建会下载 Python、Node 镜像及 DeerFlow 依赖。

Python 锁文件的包下载地址使用清华 PyPI 镜像，保留原始 SHA-256 校验值；依赖版本仍由 `marketing/uv.lock` 固定。Docker 缓存依赖下载，修改业务代码不会重新下载整套依赖。

```sh
cd /Users/duxiao/workspace/marketing-agent/marketing_v2_agent
python3 marketing/manage.py start
python3 marketing/manage.py verify
```

打开 **http://127.0.0.1:18080**，接口文档为 **http://127.0.0.1:18080/docs**。

默认 **demo 演示模式**，不需要模型密钥，用于验证交互、阶段切换和持久化。演示结果有明确标记，不是实际调研。Compose 项目名 `marketing-v2-agent`、端口 `18080`、独立数据卷，与旧项目分离。API 不直接暴露到宿主机，前端通过 Nginx 代理。

```sh
python3 marketing/manage.py status
python3 marketing/manage.py logs
python3 marketing/manage.py stop
```

停止不删除数据。重新 `start` 保留项目和成果。服务中断的执行会标为失败，可在界面重试当前阶段；已完成阶段不重复生成。SQLite 数据保存在 Compose 的 `marketing_data` 命名卷中。单实例本地工作台，不含账户登录；默认只监听回环地址。

## 接入 GLM

首次启动自动创建 `.env.marketing`。在本机编辑，**不要将密钥提交到 Git**：

```dotenv
MARKETING_RUNTIME=deerflow
MARKETING_API_KEY=填写你自己的密钥
MARKETING_MODEL=填写账户可调用的模型名
MARKETING_BASE_URL=填写对应账户的OpenAI兼容API地址
MARKETING_PORT=18080
MARKETING_RECURSION_LIMIT=80
```

普通 API 示例入口为 `https://open.bigmodel.cn/api/paas/v4/`。Coding Plan 使用不同入口，是否可用于该应用及支持哪些模型须以你的账户说明为准。接口/套餐说明参考 [智谱平台](https://www.bigmodel.cn/glm-coding) 和 [官方模型连接文档](https://zcode.z.ai/cn/docs/configuration)。不要将套餐密钥直接套用到另一种 API 地址。

修改后执行 `python3 marketing/manage.py start`，检查右上角变为“DeerFlow · 真实执行”，再新建任务。旧演示任务可查看和下载，但不能续接真实模型混合生成。真实执行失败会显示错误，不会自动切换为演示。`verify` 在真实模式仅检查健康状态，不主动消耗模型额度。

模型应支持 OpenAI-compatible Chat Completions、流式输出与 tool calling。`config.marketing.yaml` 可调整模型超时、输出长度和搜索提供方；当前阶段工具循环上限默认 80。长方案可能需要更大上下文或输出额度。

## 工作流

需求梳理 → 方案大纲 → 市场研究与洞察 → 核心策略 → 传播主题 → 创意方案 → 达人策略 → 完整提案（含 KPI / 预算）。

- **自主执行**：Agent 自动选择项目类型及业务模块，按依赖顺序连续生成。主题默认采用第一个候选，缺失条件列为假设。可在完成后修改任何阶段。
- **逐步确认**：需求、大纲、调研、主题、创意、终稿分别等待确认；主题选择后另外收集创意方向。策略与达人阶段自动衔接。
- **修改续跑**：选择已生成的阶段，填写修改要求；保留历史版本作为审计记录，作废其下游成果并重新执行。历史版本保存在 API `history` 字段，界面展示当前版本。
- **项目类型**：抖音、小红书、整合营销、影视娱乐、游戏、IP/账号、通用；继承原项目的模块映射和平台主题、KPI 提示词。
- **资料模式**：联网 + 本地、仅联网、仅本地。仅本地在模型工具列表和实际工具分发层同时禁用网络工具。

每一阶段由 DeerFlow Agent 自主决定检索与推理步骤；外层业务控制器确保阶段顺序和确认规则。该版本不启用并行子 Agent、宿主机 Bash 或自动发布。任务在一个后台工作线程串行执行；新任务排队，不会并发修改 DeerFlow 的进程级配置。

## GLM 与外部能力的边界

| 能力 | 当前实现 | 还需要什么 |
| --- | --- | --- |
| 需求、策略、创意、提案文稿 | DeerFlow 调用 GLM | 可用的模型 key / 地址 / 模型名 |
| 实时网络调研 | DeerFlow DuckDuckGo 搜索 + Jina 网页读取 | 本机 Docker 可访问相关外网；质量或稳定性不够时可替换为付费搜索 |
| 本地品牌资料 | 粘贴文本或上传 TXT / Markdown，纳入阶段上下文 | 复杂文件先转文本；当前不是向量数据库 RAG |
| 达人价格、粉丝及效果数据 | 可检索公开资料，无法核实应标为待核实 | 精确数据需授权的数据平台/API 或用户提供资料 |
| 图片、海报、视频 | 尚未接入生成服务 | 独立图像/视频模型与 API |
| 提案导出 | Markdown 下载 | PPTX / 网页 PPT / PDF 排版暂未移植 |

当前目标是原项目 **Marketing V2 提案流程**。旧项目 V1 的绘图 PPT、企业模板和网页 PPT 分支不在这次流程迁移范围内。数据来源、预算验算和 KPI 依据通过阶段提示词约束；不代表事实或效果获得程序级证明，交付前仍需业务审核。

## 目录

```text
marketing/
  marketing_agent/    业务编排、DeerFlow 适配、API、SQLite、迁移的提示词
  frontend/           新 Vue 3 工作台（Vite 构建，Nginx 服务）
  tests/              流程、确认、重试、续跑、事件回放与适配测试
  manage.py           Docker 启停与无密钥验收
backend/packages/harness/  官方 DeerFlow v2.0.0 核心
compose.marketing.yaml     独立本地部署
config.marketing.yaml      模型与工具配置（不含密钥）
.env.marketing.example     配置示例
```

上游 `frontend/` 作为官方源码保留；本项目 Docker 构建使用 **`marketing/frontend/`**。

## 开发与测试

```sh
cd marketing
uv sync --extra test --extra runtime --python 3.12
uv run --extra test --extra runtime pytest -q
```

无模型的流程测试不消耗额度。DeerFlow 适配测试使用受控模型响应检验真实 harness 的工具循环，不代表 GLM 线上服务已验证。

原始接口：`POST /api/runs` 创建，`GET /api/runs/{id}` 查询，`GET /api/runs/{id}/events` SSE（支持 Last-Event-ID），`POST /api/runs/{id}/feedback` 确认或修改，`POST /api/runs/{id}/retry` 重试，`GET /api/runs/{id}/export` 下载。
