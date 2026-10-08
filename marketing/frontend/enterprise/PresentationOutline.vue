<script setup>
defineProps({outline: {type: Object, default: null}, busy: Boolean, readOnly: Boolean})
defineEmits(['confirm'])
const roleNames = {cover:'封面',preface:'序言',contents:'目录',section:'章节',body:'正文',ending:'尾页',
  editorial:'正文',table:'表格',statement:'核心主张',metrics:'关键数字',columns:'并列信息',
  comparison:'对比',steps:'执行步骤',bars:'数据比较',chart:'图表',image:'场景视觉',closing:'收尾'}
</script>

<template>
  <section class="presentation-outline" aria-label="PPT 大纲">
    <div class="outline-heading"><div><h3>{{outline?.legacy ? '成品页面结构' : readOnly ? '生成大纲' : '确认生成大纲'}}</h3><p v-if="outline?.legacy">这份 PPT 生成时尚无大纲确认环节；以下按实际成品页面展示。</p><p v-else-if="outline?.mode==='general'">项目 Agent 已根据文稿和所选风格规划页面。确认后才会开始排版与配图。</p><p v-else>项目 Agent 已根据文稿和企业模板规划页面。确认后才会开始排版与配图。</p></div><strong v-if="outline"><template v-if="outline.legacy">成品 {{outline.actual_page_count || outline.planned_page_count}} 页</template><template v-else>预计 {{outline.planned_page_count}} 页<template v-if="readOnly && outline.actual_page_count != null"> · 成品 {{outline.actual_page_count}} 页</template></template></strong></div>
    <p v-if="!outline" class="muted">正在读取大纲…</p>
    <template v-else>
      <div v-if="outline.agenda?.length" class="outline-agenda"><b>章节目录</b><span v-for="chapter in outline.agenda" :key="chapter.id">{{chapter.number}}. {{chapter.text}}</span></div>
      <ol class="outline-pages"><li v-for="page in outline.pages" :key="page.number"><span class="outline-number">{{String(page.number).padStart(2,'0')}}</span><div><b>{{page.title || roleNames[page.role] || '未命名页面'}}</b><small>{{roleNames[page.role] || page.role}}<template v-if="outline.mode==='general' && page.section"> · {{page.section}}</template><template v-if="outline.mode!=='general'"> · 模板第 {{Number(page.template_page) + 1}} 页</template></small><p v-if="page.design_intent?.focus"><strong>设计重点：</strong>{{page.design_intent.focus}}</p><p v-if="page.design_intent?.template_motif"><strong>模板元素：</strong>{{page.design_intent.template_motif}}</p><p v-if="page.source_preview">{{page.source_preview}}</p></div></li></ol>
      <p v-if="!readOnly && outline.mode==='general'" class="muted">这是排版前的页面规划；若页面内容溢出，排版修复可能增加成品页数。</p>
      <p v-else-if="!readOnly" class="muted">生成后会核对成品页数。若因缺页或续页与已确认大纲不符，草稿会保留并标记为待复核。</p>
      <button v-if="!readOnly" type="button" :disabled="busy" @click="$emit('confirm')">确认大纲并开始生成</button>
    </template>
  </section>
</template>

<style scoped>
.presentation-outline{margin:18px 0;padding:22px;border:1px solid #d8e1da;border-radius:16px;background:#f8fbf7;color:#173b2b}
.outline-heading{display:flex;justify-content:space-between;gap:18px;align-items:start}.outline-heading h3{margin:0 0 5px;font-size:20px}.outline-heading p{margin:0;color:#526451}.outline-heading strong{white-space:nowrap;background:#e3f0df;padding:6px 12px;border-radius:99px}
.outline-agenda{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:18px 0}.outline-agenda b{margin-right:6px}.outline-agenda span{padding:5px 9px;background:#e9f2e7;border-radius:8px}
.outline-pages{list-style:none;padding:0;margin:16px 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;max-height:420px;overflow:auto}.outline-pages li{display:flex;gap:12px;padding:12px;border:1px solid #e0e8df;border-radius:10px;background:white;min-width:0}.outline-number{font-weight:700;color:#4d7953}.outline-pages li div{min-width:0}.outline-pages b,.outline-pages small{display:block}.outline-pages small{color:#64806b;margin:3px 0}.outline-pages p{font-size:12px;color:#526451;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;margin:6px 0 0}.presentation-outline button{margin-top:10px}
</style>
