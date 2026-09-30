# 企业模板网页 PPT Agent：方案与交接包

日期：2026-09-30。适用范围：16:9、前端解析并打标的企业 PPTX 模板、已有完整文稿、项目 Agent 自动设计和修复。

**建议采用“现有网页 PPT Agent + 企业模板分支 + 可验证的质量闭环”。** 前端负责拆分、打标、确认和发布；后端 GLM 负责理解模板、分页、视觉设计、图片任务和完整 HTML；Seedream 按需生成正文素材；视觉模型逐张审查实际截图；工具负责真实浏览器验证、版本保存和验收门禁。

企业v5核心流程已接入18080：前端快照自动分析与标签建议、企业Seedream素材编排、逐组候选检查与修复、版本回滚、独立终审及质量状态。**尚未达到稳定无人干预产出高质量成品的验收条件**；7页短稿曾因本地视觉预算停止，仍保留needs_review历史结论，不能把工程测试通过当作最终美观验收。请先读[本轮v5实施与验收](v5实施与验收.md)，其余初版方案保留作研究背景。

最新人工问题ID、逐项Seed复验、当前版本门禁与保留原稿恢复补强已部署，renderer和项目skill SHA与新版源码吻合。`bc11692a2bdf4a86a10a3540d5a0a88c`第一轮已完成56页、通过来源/浏览器/导出版本匹配检查，但视觉与素材未验收。用户随后明确授权本地图片/视觉共享上限调至200元并继续同一任务；图片单独20元上限和累计账本不变，负责人正重建API环境并从原检查点续跑，进入真实Seed复验与必要修复。

本轮另同步了标题左/中/右对齐锚点、重分页后的人工问题复验矩阵、rubric v3与缓存门禁、仅描述测量升级的版本兼容、CSS/SVG通用图形证据及前端进度字段兼容修复。模板通用性与尚未验证的边界见[多套企业模板的通用适配](多套企业模板的通用适配.md)；原第7/8/13/20页的[四页独立观察](resources/evidence/user-layout-review/README.md)保留改善与残留问题，仍为needs_review。

本轮工程检查为417项Python通过、1项跳过、Chrome21项通过，前端构建及5种进度状态浏览器mock回归通过，18080真实访问无console error。本目录仍等待续跑最终生产结果，当前[准备状态](resources/PREPARING.md)说明适用；现有manifest和ZIP暂时属于此前快照。

## 阅读顺序

1. [完整方案与差距分析](方案与实施说明.md)：为什么这样做、生成顺序、模型职责、修复和质量验收。
2. [前后端输入协议](前端输入协议.md)：前端同事只需要交付什么，哪些字段不能丢。
3. [工具与接入说明](工具与接入说明.md)：现有可调用 API、Python 工具映射、迁移边界。
4. [当前项目实际skill](resources/source/marketing/presentation/skills/enterprise-deck/SKILL.md)：v5工具已在企业分支接入；另附[目标流程规范](resources/skills/enterprise-ppt-agent/SKILL.md)，不可把目标中所有拓展都当成已验收能力。
5. [当前实测与验收状态](resources/evidence/README.md)：当前任务、前后截图及仍未解决的问题。
6. [多套企业模板的通用适配](多套企业模板的通用适配.md)：不同模板的标题、形状、分页及复验如何共用流程，哪些能力尚未得到真实审美验证。

## 可以直接交给同事的内容

| 路径 | 内容 | 可用程度 |
|---|---|---|
| `resources/tools/project_ppt_client.py` | 无第三方依赖的 HTTP 客户端；导入文稿、创建任务、查询、优化、草稿查询 | 调用现有接口；查询已联调 |
| `resources/openapi.json` | 18080更新后的OpenAPI，含自动分析标签接口 | 当前接口真实快照，不是拟议接口 |
| `resources/source/marketing/marketing_agent/` | 后端工具源码快照 | 集成参考；不能脱离依赖与数据目录直接声称可运行 |
| `resources/source/marketing/presentation/` | 渲染器及项目实际 skill | 与源码配套；依赖见 package-lock.json |
| `resources/skills/enterprise-ppt-agent/` | 目标编排 skill、质量与工具协议 | 需按能力矩阵完成适配；不能仅加载文件就认为具备全部工具 |
| `resources/examples/frontend-template.json` | 已通过当前后端验证器的无敏感信息模板样例 | 用于协议联调，不是高质量设计样例 |
| `resources/examples/repair-feedback.json` | 真实可执行的优化请求结构 | 可用在 `/optimize` 请求体 |
| `resources/research/` | 118 研究与本地适配依据 | 研究证据；不代表取得118私有后端 |
| `resources/manifest.json` | 文件 SHA256 与快照说明 | 校验交接文件一致性 |

包内不包含密钥、用户数据库、node_modules 或运行环境。所有本地引用均位于本文件夹，复制整个文件夹即可保留文档链接。运行已有服务是最快的接入方式；移植源码需要补齐模型配置、字体、浏览器、存储与任务执行器。

## 当前项目入口

[PROJECT / 99162889](http://localhost:18080/?project=99162889686a4e7ca7f3a69a83e93c86)。用户排版反馈优化任务：`bc11692a2bdf4a86a10a3540d5a0a88c`（优化未结束，阶段协议修复后按原ID续跑，当前阶段以接口为准）；上一优化任务：`40bf3188a3bf430fa448a664ed8a4f4f`，旧版：`5eacb1f68f17447fbbf179bfce3effa4`。

旧56页优化任务已结束，但visual_status仍为needs_review，新版前端将它显示为待复核草稿。v5短稿在隔离服务验证，使用GLM、Seedream和Seed，Codex没有手写生产HTML或补图。Codex的独立观察单独记录，不伪装成项目模型结论。
