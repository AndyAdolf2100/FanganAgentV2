# 目标逻辑工具协议

这里是供主Agent注册工具的建议契约，不是当前服务已有的同名HTTP路由。字段使用稳定ID，文件内容通过受控资源ID读取，不让模型处理巨量base64。

所有结果应带`task_id`、`artifact_revision`、`status`、`evidence_refs`、`error`。状态至少区分ok、needs_repair、unavailable；不可用不能包装成pass。

| 逻辑工具 | 输入 | 输出 | 当前对应/缺口 |
|---|---|---|---|
| load_template_snapshot | template_id、revision | 不可变页面/元素/资源/字体 | TemplateLibrary.snapshot已有 |
| render_template_reference | template snapshot | 每页PNG、原始元素几何 | enterprise renderer已有 |
| understand_template | 元素、原HTML、参考截图 | 主题、fixed/text/body合同及理由 | analyze_reference + analyze_contract已有 |
| plan_deck | 文稿来源块、合同目录、用户约束 | 页面/来源/模板/视觉关系计划 | plan_deck已有；明确视觉计划需扩展 |
| plan_enterprise_assets | 页面计划、文稿、主题、真实素材 | 每张图用途、prompt、画幅、引用与使用页 | 企业分支缺失 |
| generate_asset | prompt、size、reference_asset_id | 受控asset_id、hash、尺寸、生成记录 | generate_image可复用，需企业适配 |
| review_asset | 一张实际图片、用途、参考关系 | 可用性、主体/裁切/事实风险问题 | 需补独立素材审查 |
| generate_page | 页面计划、合同、来源、可用素材 | 完整HTML、图表声明 | enterprise_full_html已有，需素材清单 |
| validate_page | HTML、合同、来源与资产清单 | 来源/资源/品牌结构错误 | document_check已有，需资产扩展 |
| render_probe | 候选版本 | PNG、DOM几何、错误位置 | enterprise.mjs已有 |
| review_page | 一张PNG、角色、合同摘要、未解决问题 | observed、问题、修复建议、逐项解决状态 | 单图已有；问题ID与逐项关闭需扩展 |
| choose_repair | 问题台账、历史、正文变体目录 | 重排/合同重分析/换模板/换素材/续页 | 部分已有，换素材/重规划需扩展 |
| commit_candidate | 已通过检查的候选版本 | accepted_revision指针 | 当前先存后回滚；建议改事务提交 |
| final_review | 冻结版本、每页证据与全册摘要 | accepted/needs_review/incomplete | 严格冻结终审门禁待补 |
| export_deck | 冻结已验收版本 | HTML/PPTX/PNG/报告 | 导出已有；发布门禁待补 |

## 素材清单建议

```json
{
  "asset_id": "asset-concept-scene-01",
  "source": "generated_concept",
  "model": "由部署配置提供",
  "sha256": "实际图片文件摘要",
  "reference_asset_id": null,
  "width": 2048,
  "height": 1152,
  "allowed_page_ids": ["page-brand-scene"],
  "usage": "正文使用场景，非真实产品实拍",
  "review_status": "accepted",
  "resource_ref": "由资源服务提供的受控引用"
}
```

这不是现有后端已接受的请求。实现时应给模型资源别名，并在HTML校验器/渲染器中只展开清单内资源。不能仅修改提示词要求模型插图，却保持工具禁止所有新素材。

## 修复请求建议

```json
{
  "page_id": "page-strategy-timeline",
  "template_revision": 1,
  "contract_revision": 2,
  "candidate_revision": 4,
  "original_task_feedback": ["原始遮挡及时间标签可读性问题"],
  "open_issues": [{
    "issue_id": "timeline-label-wrap",
    "severity": "medium",
    "detail": "W1–3在徽章内分成两行并偏离中心",
    "fix_hint": "由模型改为语义完整的标签构图，不改原始时间区间"
  }],
  "current_html_ref": "受控候选HTML资源ID",
  "browser_findings": [],
  "screenshot_ref": "实际候选PNG资源ID",
  "history": [],
  "remaining_attempts": 3
}
```

逐页复查返回每个旧issue_id为resolved/unresolved及具体证据，并单列new_issues。宿主还应核验截图hash、HTMLhash、素材hash、字体/渲染器版本，避免将旧审查结论用于新版本。

## 质量状态机

`planned → assets_ready → candidate → browser_passed → visually_reviewed → page_accepted → deck_frozen → final_reviewed → published`

任意缺失证据进入incomplete；可修缺陷进入needs_repair；次数用尽保留最后可用版本及原因。最终验收需要整个冻结版本的来源/品牌/浏览器/视觉证据完整，不只检查最后一次工具是否返回200。
