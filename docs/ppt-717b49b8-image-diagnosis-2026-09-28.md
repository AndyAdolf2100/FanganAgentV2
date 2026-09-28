# 会话 717B49B8 未生图诊断

检查日期：2026-09-28（Asia/Shanghai）。本次只读检查项目运行状态、任务文件、预算账本和调用代码，没有调用 Codex 生图，也没有向 Seedream 发送测试请求。

## 结论

项目 Agent 已生成有效图片任务及提示词，图片工具也进入执行路径；但请求发送前，本地图片与视觉共享累计预算检查失败。Seedream 的 HTTP 请求没有发出。本次没有证据支持“Seedream 接口故障”或“火山余额不足”。

这是前期节省费用设置的本地累计限制，对所有 PPT 任务共享，不是每个新会话各自拥有一份预算。

## 两次任务证据

来源项目：`717b49b837294e0ebb173771d0bbe04a`，通过文稿导入创建。

| 创建时间 | PPT 任务 | 风格 | 页数 | Agent 图片任务 | 结果 |
|---|---|---|---:|---|---|
| 15:24:07 | `ee100848388444d4895520c02fa2f426` | 理性商务 | 16 | cover、scene1；提示词非空 | 0 张图片；视觉复核 incomplete |
| 15:29:20 | `19446c6ba0ab469aa8a35a9ecb6bddd7` | 自然品牌 | 12 | cover、scene1、scene2；提示词非空 | 0 张图片；视觉复核 incomplete |

两份 `image-plan.json` 的第一张图均记录：

> 项目图片与视觉累计预算已用完；保留草稿和待处理项

后续场景图记录：

> 图片服务本轮不可用，跳过后续请求，未重试

两次任务均无可复用图片。封面失败后，程序暂停后续付费图片尝试，并移除不可用的图片角色，继续导出无图版本。任务的 `completed` 表示技术导出完成，不能解释为配图与视觉复核全部完成。

原始证据位于容器：

`/data/marketing/presentations/<上述任务编号>/`

重点文件为 `job.json`、`image-briefs.json`、`image-plan.json`、`agent-run.json`、`visual-review.json`。

## 配置与预算快照

| 项目 | 当前值 |
|---|---|
| 图片服务开关 | true |
| 图片 API 密钥 | 已配置；不记录密钥内容 |
| 图片模型 | `doubao-seedream-5-0-flash-260915` |
| `MARKETING_PPT_BUDGET_RMB` | 5 元共享累计上限 |
| 图片累计预留 | 5 次，0.60 元 |
| 视觉累计预留 | 44 次，4.40 元 |
| 共享累计预留 | 5.00 元，已达上限 |
| `MARKETING_IMAGE_BUDGET_RMB` | 1 元图片独立上限 |
| `MARKETING_IMAGE_MAX_REQUESTS` | 6 次累计图片预留上限，当前仅余 1 次 |
| `MARKETING_VISION_MAX_REQUESTS` | 64 次累计视觉预留上限 |

预留账本包含失败请求，不等于供应商实际扣费；5 元达到本地上限不表示用户充值的 20 元已耗尽。GLM 文本调用不包含在此图片与视觉预算中。

## 精确拦截位置

1. [presentation_images.py](../marketing/marketing_agent/presentation_images.py) 的 `materialize_assets()` 读取 Agent 生成的图片任务，进入 `generate_image()`。
2. `generate_image()` 检查图片缓存之后，先调用 `reserve_budget()`，再构造并发送 `/images/generations` 请求。
3. [presentation_budget.py](../marketing/marketing_agent/presentation_budget.py) 的 `reserve()` 汇总图片和视觉两个持久账本。
4. 当前总额 5.00 元，再预留一张图片的 0.12 元会超过 5 元，因此抛出异常，未执行 HTTP 请求。
5. 生图阶段捕获异常，保存 `image_notice`；场景图跳过。随后截图视觉请求也被同一共享预算拦截。

工具轨迹中的 `generate_image` 记录在预算检查之前写入。因此“日志有 generate_image”只能证明尝试进入工具，不能证明 Seedream 收到请求。

## 恢复所需调整

只重启 Docker、切换风格或再次点击生成，不能解除持久预算限制。清空账本会丢失累计记录，不建议这样恢复。

若要对最新的自然品牌任务重新生成三张图，需要同时处理共享金额和图片次数两项限制。仅提高共享金额，仍会在新增第一张图之后撞到累计 6 次图片上限。

一组节省费用的配置建议如下，本文未实施：

| 配置 | 当前 | 建议 |
|---|---:|---:|
| `MARKETING_PPT_BUDGET_RMB` | 5 | 6 |
| `MARKETING_IMAGE_MAX_REQUESTS` | 6 | 8 |
| `MARKETING_IMAGE_BUDGET_RMB` | 1 | 保持 1 |

按当前项目预留标准，新增三张图片需要 0.36 元；12 页首轮截图分三批需要 0.30 元，最多两轮修改页复核还需预留最多 0.20 元。共约 0.86 元，属于本地预算估算，不是供应商报价或最终账单。最终页数变化、并发的其他任务和失败重试会影响剩余额度。

配置生效后，应通过项目的 PPT 入口重试，复用已有文稿规划和图片任务缓存，让项目 Agent 调用 Seedream 生图，再运行截图检查。应保留现有账本、文稿和生成历史。Compose 环境变量调整需要重建容器配置，单纯 restart 不会载入新的环境变量。

## 后续配置调整：2026-09-28

用户随后要求本地总金额上限调整为 20 元、图片累计次数调整为 100 次。已更新 `.env.marketing` 并重建 API 容器使配置生效：

- `MARKETING_PPT_BUDGET_RMB=20`：图片与视觉共享累计上限。
- `MARKETING_IMAGE_MAX_REQUESTS=100`：图片累计请求预留次数上限。
- `MARKETING_IMAGE_BUDGET_RMB=20`：同步解除原先独立的 1 元图片限制，仍受共享 20 元总上限约束，不是额外增加 20 元。

历史预留账本保留，已有图片 5 次/0.60 元、视觉 44 次/4.40 元继续计入总额。本次仅调整配置，没有触发新的生图或 PPT 重跑。
