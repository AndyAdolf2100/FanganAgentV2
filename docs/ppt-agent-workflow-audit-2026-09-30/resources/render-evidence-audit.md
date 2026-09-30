# 第18页渲染证据核查

核查时间：2026-09-30 12:34–12:36（Asia/Shanghai）。项目 `99162889686a4e7ca7f3a69a83e93c86`，优化任务 `40bf3188a3bf430fa448a664ed8a4f4f`。

本次仅读取运行任务、独立渲染现有HTML、请求只读草稿预览，并在本审计目录保存证据。未修改任务文件、未调用模型、未重新生成PPT、未修改运行代码或重启服务。

## 结论

**本次没有复现“第18页预览无标题”。** 当前任务截图、此前复制的本地截图、草稿预览服务返回图、直接读取当前 `plan.json` 独立渲染的截图，四者逐字节相同，且人工检查均能看见顶部标题“3.1 核心策略主张”。不能把此前的观察直接定性为标题丢失、DOM遮挡、预览缓存或截图覆写故障。

## 可验证证据

| 来源 | 文件 | SHA256 |
| --- | --- | --- |
| 当前任务 `previews/18.png` | [任务截图](render-18-task-preview.png) | `b4ab92848fbde7cff1b38760644eb804a7dc48e1634edc2ff6b1b61218a07855` |
| 此前 `.local/enterprise-optimization/18.png` | [此前本地副本](render-18-prior-codex-copy.png) | 同上 |
| `GET /api/presentation-drafts/<job>/previews/18` | [草稿预览服务](render-18-preview-service.png) | 同上 |
| 当前第18页HTML，独立Chromium进程 | [独立渲染](render-18-independent.png) | 同上 |
| 当前任务 `backgrounds/18.png` | [PPTX底图](render-18-task-background.png) | `29bd61d20604caf38043da864cd25dcad576c3129ac6a2c7323a792426ef6acd` |

四张正常截图均为107615字节。草稿服务响应 `Cache-Control: no-store`。完整请求信息、HTML哈希、渲染器哈希、浏览器计算样式见 [元数据](render-evidence-metadata.json)。

独立渲染所用第18页HTML SHA256：`d2fa0ba7bed32900bd67797fc7b4759974971c0a0f1c2e30fdeeac530aa9638e`。

标题真实DOM为 `div.hdr[data-template-element="2"][data-ppt-slot="title"] > p`，文本不是只放在HTML `<title>` 中。浏览器实测：

- 文本：`3.1 核心策略主张`。
- 文字边界：x=43.65625，y=23.609375，width=324.796875，height=40。
- 计算样式：`display:block; visibility:visible; opacity:1; color:rgb(255,255,255); font-size:40px`。
- `-webkit-text-fill-color` 也是白色，未被设为透明。
- `document.fonts.status` 为 `loaded`。
- 标题中心点最上层元素就是该标题的 `<p>`，没有被背景遮住。

## 源码流程事实

### 正式渲染器的截图与导出

位置：[enterprise.mjs](../../../marketing/presentation/enterprise.mjs)。核查时工作区、API容器、preview容器三份文件哈希完全一致：`fe8f448d84dbd65fc677525a7ccf1ca307afb8204b6b53ee26d6e4c76c8688b6`。

1. `renderEnterprise` 开始时读取一次 `plan.json`，本次循环内使用该内存快照，不会在每页中途重新读取任务计划。
2. 整册复用同一个浏览器Page，但每页先 `goto('about:blank')`，再 `setContent(p.html)`，避免上页DOM和内联修改残留。
3. 填入图表后等待 `document.fonts.ready` 以及所有 `<img>` 的 `decode()`，之后才做布局检查和截图。
4. 第182行写 `previews/<页码>.png`。`probeOnly` 模式随即继续下一页，不执行隐藏文字和导出底图的操作。
5. 正式导出先保存完整 `pages/<页码>.html`，再为了PPTX可编辑文字叠加，把适用文本设置为透明，另写 `backgrounds/<页码>.png`。隐藏文字后的图没有写回 `previews/`。

因此，`backgrounds/` 中缺少可编辑文字是既有导出机制的一部分；它不能用作完整页面的视觉验收图。本次四张正常截图与底图的哈希不同，没有证据表明把底图误写进了预览路径。

### 优化中的任务截图确实会被重写

位置：[model_html.py](../../../marketing/marketing_agent/enterprise/model_html.py)，约第545–570行。

每次候选HTML生成后，会暂存到 `plan.json` 并对整册执行 `run_renderer(..., True)`，因此固定路径 `previews/<页码>.png` 会按本轮计划逐页覆盖。失败回滚后又保存原计划、重新渲染整册。这意味着任务运行中固定截图路径是可变产物，可能暂时对应尚未验收的候选版；源码没有为每轮截图建立不可变版本目录。

这是可确认的流程行为，但**不能据此断言它造成了第18页标题缺失**。本次抓取的四份图一致，且标题存在。

### 前端草稿预览不读取任务的可变截图

位置：[presentation_preview.py](../../../marketing/marketing_agent/presentation_preview.py)。

- 从 `plan.json` 建立快照，前后检查文件mtime和大小；读取不完整时退回上次完整快照。
- 按页面内容、canvas和theme计算revision，缓存文件名包含revision。
- 将目标页复制为单页临时计划，使用同一个 `enterprise.mjs` 的 `probe` 模式渲染；输出位于独立临时目录和预览缓存，不写原任务。
- `model_html=False` 只用于跳过任务约束检查，不改页面HTML；本次预览返回图与直接渲染结果完全一致。
- 缓存通过临时文件加 `replace` 写入，接口响应禁止HTTP缓存。
- 容器数据卷为只读，因此preview服务不能覆写任务的 `previews/18.png`。

## 未证实的解释与后续取证要求

此前所见无标题图可能涉及旧版本、不同路径、显示或观察差异，但当前没有那一时刻的独立无标题原图及哈希，不能判定具体原因。也没有证据支持本次问题来自字体未加载、Page复用污染或实际标题只存在于元数据。

若再次出现，应同时保存：完整原图、请求URL、页码、时间、图片SHA256、页面HTML SHA256、计划revision、任务阶段、是否候选/已验收，以及当时的计算样式。质量审查和交付宜绑定同一HTML/截图版本；该建议用于提高可追溯性，并不代表本次已经证实渲染器有缺陷。
