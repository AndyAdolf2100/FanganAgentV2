# 营销提案生成器

完整说明见 [方案与实施说明](../../../../方案与实施说明.md)，持续实验记录见 [实测记录](../../../evidence/README.md)。

## 运行

从仓库根目录执行：

```sh
docker compose --env-file .env.marketing -f compose.marketing.yaml up -d --build
```

在 `http://localhost:18080` 打开已完成文稿，点击“生成 PPT”。产物包括可编辑文字的PPTX、离线HTML、逐页预览与原稿备注。服务持久化保存文稿快照、计划、检查结果、单页修订和工具轨迹。

2.6 新增“新建营销项目 → 上传已有文稿 → 选择风格 → 创建项目并生成 PPT”。支持 `.docx`、`.md`、`.txt`（最大 5 MB、提取后最多 10 万字），也可粘贴正文。Word 提取正文、标题与表格，图片、页眉页脚、批注和复杂版式不导入，提交前可核对修改。导入项目直接保存完整文稿，不执行营销调研和写稿阶段。

四种风格为智能匹配、自然品牌、理性商务、暖调杂志；选择同时进入策划提示、配色和缓存标识。已有完整方案也可在 PPT 面板切换风格再生成。生成中不能切换风格；相同文稿与风格可复用已完成任务。

接口：`POST /api/manuscripts/parse?filename=...` 接收原始文件字节，`POST /api/runs/import` 接收确认后的文稿，`GET /api/presentation-styles` 返回风格目录；生成接口可传 `{"style_id":"business"}`。导入项目不允许进入原营销工作流的重试/反馈路径。

## 职责

| 部件 | 作用 |
|---|---|
| `../marketing_agent/presentation.py` | 文稿语义块、持久化任务、阶段编排 |
| `../marketing_agent/presentation_design.py` | skill策划、来源/数值/区间校验、缓存 |
| `../marketing_agent/presentation_pages.py` | 逐页brief、母版传递、锁定文字、CAS写页、有限修复 |
| `../marketing_agent/presentation_images.py` | 图片服务、费用预留、缓存、失败停止重试 |
| `../marketing_agent/presentation_charts.py`、`charts.mjs` | 原表绑定、离线SVG图表、原生PPT图表与数据工作簿 |
| `consistency.mjs` | 同类页面实际字体族、字号和字重漂移检查 |
| `render.mjs` | 浏览器行框检查、重排、截图、PPTX与HTML导出 |
| `custom.mjs`、`probe-page.mjs` | 自定义HTML的宿主与检查 |
| `audit_native_pdf.py` | 对真实PowerPoint导出PDF做全页文字与几何复核 |

## Skill目录

运行时加载 [marketing-deck](skills/marketing-deck/SKILL.md) 和 [逐页生成规则](skills/marketing-deck/references/page-generation.md)。`skills/vendor/`保留118提供的原件，不直接执行其脚本或把经验数字填入用户文稿。

- `xiaofang-methodology`：模块说服链、视觉DNA、母版与60张参考图，共62个原文件。
- `ppt-guardian-pro`：容量、字号与HTML质量规则，共12个原文件。
- 七套community视觉包与一套额外营销样例：用于研究结构和材质，不混用其品牌内容。

各包的provenance记录下载或文本转录来源、文件校验值。两套核心包74个原文件已匹配远端SHA256；它们不是118后端生成器源代码。

## 检查和费用

默认最多生成4个重点页面；单页最多初始加两次修复，任务页面模型调用总上限8次。已审阅缓存按部署字体重新测量。回退仍必须通过最终检查，并在`page-generation.json`留下原因。对应环境变量为`MARKETING_PPT_CUSTOM_PAGES`和`MARKETING_PPT_PAGE_CALL_LIMIT`。

图片费用独立限制：必须配置单价才能发起新请求，默认最多1次，上限20元；未知扣费的失败不自动释放预留，也不自动重试。预留不是供应商账单。当前方舟联调遇到服务额度限制，配置保持关闭，现有轻芽稿复用缓存概念图。

```sh
uv run --project marketing pytest marketing/tests -q
cd marketing/presentation
npm ci
npm test
```

实际应用检查须先用PowerPoint打开PPTX并导出本地PDF，再运行：

```sh
uv run --with pymupdf --with pillow python audit_native_pdf.py native.pdf outline.json --output audit
```

此脚本不会调用PowerPoint；输入必须来自实际应用。浏览器通过、PPTX结构通过、原生渲染通过和主观审美评审是不同结果。文字可编辑，背景和装饰栅格化；`chart`页导出原生可编辑图表，旧`bars`版式仍使用背景比例条。完整原文在演讲者备注中。

## 图表契约

`layout: chart`支持bar、column、line、donut，items必须为空。每个系列通过`source_refs: [{block_id,row,column}, ...]`绑定本页引用的原稿表格，行号不含表头，均从0开始。宿主读取数值；类别必须对应首列，不接受模型改值、类别错配、区间变单点或隐式换单位。无单位的非零单元格必须在列标题明确单位。所有数值有限，环形图非负、合计大于零；折线必须声明真实顺序。

条/柱最多6类2序列，双序列横条最多4类；折线最多8类3序列；环形最多6类1序列。超限应拆页，不能缩字塞满。HTML中图形为SVG，文字走DOM行框测量；导出时移除背景图中的图表区域，插入同一数据的原生PPT图表。`validate_package.py`逐项核对chart XML缓存和内嵌Excel单元格，不只检查文件能否打开。PowerPoint的布局引擎与浏览器不同，仍需要真实应用检查标签与显示结果。
