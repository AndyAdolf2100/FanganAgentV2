# 用户排版反馈的四页独立观察

任务：`bc11692a2bdf4a86a10a3540d5a0a88c`。这是2026-09-30保存的候选快照，全部为`draft_needs_review`，不表示当前生产任务实时状态。观察来自Codex只读复核，不是Seed验收，也没有用于人工改写生产HTML。

| 原页 / 观察时新页 | 生成组 | 截图 | 观察摘要与残留 |
| --- | --- | --- | --- |
| 7 / 7 | 6 | [6-candidate.png](6-candidate.png) | 长标题40px单行，未见裁剪；与其他已查页标题上缘约差4.3px |
| 8 / 8 | 7 | [7-candidate.png](7-candidate.png) | W1–3、W3–6、W5–8完整单行；需跨页核对标题基线 |
| 13 / 13 | 12 | [12-candidate.png](12-candidate.png) | 正文整体右偏改善，左右边距更均衡；仍需Seed复验 |
| 20 / 20 | 18 | [18-candidate.png](18-candidate.png) | 01–04在有色箭头内，说明移到白底深字；箭头约250×138，原模板约219×211，仍偏扁 |

PNG文件名使用生成组索引，不能直接当作页码。[观察原文](codex-observations.md)保留完整结论；[JSON证据](codex-observations.json)保留slide_id、slide_version、HTML SHA256与浏览器截图SHA256。四张PNG均与记录中的截图哈希一致。

为便携使用，JSON中的`screenshot_file`改成相对本目录的文件名，原采集路径保留为`source_screenshot_file`，原JSON哈希记录在`portable_copy`；观察和截图哈希没有改写。真实像素、SVG内部文字安全区与最终审美不是单凭外接框可以证明的能力，当前整体质量状态仍为`needs_review`。
