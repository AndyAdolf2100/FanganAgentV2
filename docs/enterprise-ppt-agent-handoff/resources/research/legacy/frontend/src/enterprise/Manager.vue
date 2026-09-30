<template>
  <section class="template-management">
    <button class="button secondary" :disabled="busy" @click="close">← 返回营销策划</button>
    <header><div><h1>企业模板管理</h1><p>创建套装 → 配置版式 → 标记文字字段 → 检查试填 → 发布。生成时先规划每页内容，再将模板 HTML、文字标签和文稿交给模型，在固定布局内生成页面。</p></div><button :disabled="busy" @click="create">新建空白套装</button><button :disabled="busy" @click="input?.click()">{{ store.busy ? '正在解析…' : '导入 PPTX 模板' }}</button><input ref="input" hidden type="file" accept=".pptx" @change="importFile" /></header>
    <p class="hint">仅支持 PPTX，最大 30MB、40 页。模板保存在本地服务的企业模板列表；导入解析不调用模型。</p>
    <p class="hint">生成规则：正文文字可调整排版，目录可续页，首页和尾页各一页；章节页只改标题和序号。背景、图片与装饰保持原样。请将转曲文字标记为“轮廓文字”，并设置用途。</p>
    <p v-if="error || store.error" role="alert" class="error">{{ error || store.error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <div v-if="!store.templates.length" class="empty">还没有企业模板。导入企业 PPTX 后，配置封面、正文等版式，保存并发布。</div>
    <nav class="library" aria-label="企业模板列表"><button v-for="template in store.templates" :key="template.id" :disabled="busy" :class="{ active: draft?.id === template.id }" @click="open(template)">{{ template.name }} <span>{{ template.published ? `已发布 v${template.published.revision}` : '草稿' }}</span></button></nav>
    <section v-if="draft">
      <div class="toolbar"><label>模板名称<input v-model="draft.name" :disabled="busy" @input="dirty = true" /></label><span>{{ draft.aspectRatio }} · {{ draft.pages.length }} 页</span><button :disabled="busy || !dirty" @click="save">保存草稿</button><button :disabled="busy" @click="recognize">{{ recognizing ? '正在识别标签…' : 'AI 识别标签' }}</button><button :disabled="busy" @click="check">检查标签</button><button :disabled="busy" @click="trial">当前页试填</button><button :disabled="busy" class="primary" @click="publish">发布模板</button><button :disabled="busy" @click="remove">删除模板</button></div>
      <p class="hint">{{ draft.published ? `当前生成使用 v${draft.published.revision}，草稿修改在重新发布后生效。` : '尚未发布，生成页面暂不可选择。' }}{{ dirty ? ' 有未保存的修改。' : '' }}</p>
      <p v-if="missingFonts.length" class="font-warning" role="status">当前浏览器未检测到这些字体：{{ missingFonts.map(item => item.font).join('、') }}。显示时可能使用替代字体，影响排版。请安装原字体后刷新，或在编辑器中选择可用字体。</p>
      <details class="font-audit"><summary>字体与文字颜色检查（{{ fontAudit.length }} 种字体）</summary>
        <p>网页默认使用系统字体，原字体单独记录供导出还原，文字颜色沿用 PPTX。字体检测基于当前浏览器的字形宽度，不保证所有字符均有对应字形。</p>
        <button :disabled="busy" @click="fontCheckRevision++">重新检测字体</button>
        <ul><li v-for="item in fontAudit" :key="item.font"><strong>{{ item.font === 'system-ui' ? '系统默认字体' : item.font }}</strong> · {{ item.available === null ? '无法检测' : item.available ? '检测到字体字形' : '未检测到，可能替换' }} · 第 {{ item.pages }} 页
          <span> · 原字体：{{ item.sourceFonts.join('、') || item.font }}</span>
          <span v-for="color in item.colors" :key="color" class="color-value"><i :style="{ backgroundColor: color }"></i>{{ color }}</span>
        </li></ul>
      </details>
      <nav class="pages" aria-label="模板版式"><button v-for="(page, index) in draft.pages" :key="index" :disabled="busy" :class="{ active: pageIndex === index }" @click="pageIndex = index">{{ index + 1 }} · {{ roles[page.role] }}</button></nav>
      <div v-if="page" class="page-settings"><label>页面用途<select v-model="page.role" :disabled="busy" @change="page.labelSource = 'manual'; settingsChanged()"><option v-for="(label, role) in roles" :key="role" :value="role">{{ label }}</option></select></label><label>版式名称<input v-model="page.name" :disabled="busy" @input="settingsChanged()" /></label><label v-if="page.role === 'body'">正文结构<select v-model="page.layoutKind" :disabled="busy" @change="settingsChanged()"><option v-for="(label, kind) in layoutKinds" :key="kind" :value="kind">{{ label }}</option></select></label><button :disabled="busy || draft.pages.length >= 40" @click="duplicate">复制版式</button><span>同一套装可包含多种正文版式，生成时根据内容选用。</span></div>
      <section v-if="report" class="check-report" role="status"><strong>{{ report.valid ? '标签检查通过' : '请先修正以下标签' }}</strong><ul><li v-for="message in report.errors" :key="message" class="error">{{ message }}</li><li v-for="message in report.warnings" :key="message">{{ message }}</li></ul><p>可用版式 {{ report.layouts.length }} 个；每页字段名用于对应内容，配对顺序用于关联分项标题与正文。</p></section>
      <details v-if="pageFields.length" open><summary>当前版式的填充字段（{{ pageFields.length }}）</summary><table><thead><tr><th>字段</th><th>用途</th><th>配对顺序</th><th>参考容量</th></tr></thead><tbody><tr v-for="field in pageFields" :key="field.name"><td>{{ field.name }}</td><td>{{ textRoleLabels[field.role] || field.role }}</td><td>{{ field.order }}</td><td>{{ field.capacity }} 字</td></tr></tbody></table></details>
      <section v-if="trialHtml" class="trial"><div><strong>示例内容试填（当前页）</strong><button @click="trialHtml = ''">关闭</button></div><div ref="trialHost" class="trial-stage" :style="{ aspectRatio: `${draft.width}/${draft.height}` }"><iframe :srcdoc="trialHtml" title="企业模板内容试填预览" sandbox="" :style="{ width: `${draft.width}px`, height: `${draft.height}px`, transform: `scale(${trialWidth / draft.width || 1})` }" /></div><p>使用示例文字检查填充位置；真实文稿生成时仍会进行浏览器排版校验。</p></section>
      <fieldset :disabled="busy" class="editor-wrap"><EnterpriseTemplateEditor v-if="page" :key="`${draft.id}-${pageIndex}`" :template="draft" :page="page" @change="changePage" /></fieldset>
      <details v-if="draft.warnings.length"><summary>导入检查（{{ draft.warnings.length }}）</summary><ul><li v-for="warning in draft.warnings" :key="warning">{{ warning }}</li></ul></details>
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue';

import { useEnterpriseTemplateStore } from './store';
import type { EnterpriseTemplate, EnterpriseTemplatePage, TemplatePageRole } from './types';
import { labelTemplate, textRoleLabels } from './labels';
import { copyTemplate, publishTemplate, createBlankTemplate, duplicatePage } from './edit';
import EnterpriseTemplateEditor from './Editor.vue';
import { detectFont } from './font-check';
const emit = defineEmits<{ close: []; published: [] }>();
const store = useEnterpriseTemplateStore();
async function close() { if (!busy.value && await save()) emit('close'); }
const input = ref<HTMLInputElement | null>(null);
const draft = ref<EnterpriseTemplate | null>(null);
const dirty = ref(false), saving = ref(false), error = ref(''), notice = ref(''), pageIndex = ref(0);
const recognizing = ref(false);
const report = ref<any>(null), trialHtml = ref('');
const trialHost = ref<HTMLElement | null>(null), trialWidth = ref(0);
let trialObserver: ResizeObserver | undefined;
watch(trialHtml, async () => { await nextTick(); trialObserver?.disconnect(); if (trialHost.value) { trialObserver = new ResizeObserver(entries => { trialWidth.value = entries[0].contentRect.width; }); trialObserver.observe(trialHost.value); } });
onBeforeUnmount(() => trialObserver?.disconnect());
const layoutKinds = { auto: '自动识别', text: '长文正文', items: '分项介绍', comparison: '对比', timeline: '时间轴', table: '数据表格', custom: '自定义专业版式' };
watch(pageIndex, () => { trialHtml.value = ''; });
const pageFields = computed(() => report.value?.layouts.find((item: any) => item.template_page === pageIndex.value)?.fields || []);
const busy = computed(() => store.busy || saving.value || recognizing.value);
const page = computed(() => draft.value?.pages[pageIndex.value]);
const fontCheckRevision = ref(0);
const fontAudit = computed(() => {
  void fontCheckRevision.value;
  const usage = new Map<string, { text: string; colors: Set<string>; pages: Set<number>; sourceFonts: Set<string> }>();
  draft.value?.pages.forEach((page, index) => page.elements.forEach(element => element.paragraphs?.forEach(paragraph => paragraph.runs.forEach(run => {
    if (!run.text.trim()) return;
    const entry = usage.get(run.font) || { text: '', colors: new Set<string>(), pages: new Set<number>(), sourceFonts: new Set<string>() };
    if (run.sourceFont) entry.sourceFonts.add(run.sourceFont);
    entry.text += run.text; entry.colors.add(run.color); entry.pages.add(index + 1); usage.set(run.font, entry);
  }))));
  const ctx = document.createElement('canvas').getContext('2d');
  return [...usage].map(([font, entry]) => ({ font, sourceFonts: [...entry.sourceFonts], colors: [...entry.colors], pages: [...entry.pages].join('、'),
    available: font === 'system-ui' ? true : ctx ? detectFont(font, [...new Set(entry.text)].slice(0, 120).join(''), (family, text) => { ctx.font = `32px ${family}`; return ctx.measureText(text).width; }) : null,
  }));
});
const missingFonts = computed(() => fontAudit.value.filter(item => item.available === false));
const roles: Record<TemplatePageRole, string> = { cover: '封面', contents: '目录', section: '章节页', body: '正文', ending: '结束页', exclude: '不使用' };
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = ''; } }
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload));
onMounted(async () => { window.addEventListener('beforeunload', beforeUnload); try { await store.init(); if (store.templates[0]) await open(store.templates[0]); } catch {} });
async function save() {
  if (!draft.value || !dirty.value) return true;
  saving.value = true; error.value = '';
  try { await store.update(copyTemplate(draft.value)); dirty.value = false; notice.value = '草稿已保存'; return true; }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '保存失败'; return false; }
  finally { saving.value = false; }
}
async function open(template: EnterpriseTemplate) {
  if (dirty.value && !await save()) return;
  draft.value = labelTemplate(copyTemplate(template), true); pageIndex.value = 0; dirty.value = JSON.stringify(draft.value.pages) !== JSON.stringify(template.pages); notice.value = ''; error.value = ''; report.value = null; trialHtml.value = '';
  draft.value.pages.forEach(p => { p.layoutKind ||= 'auto'; });
}
async function importFile(event: Event) {
  const target = event.target as HTMLInputElement; const file = target.files?.[0]; target.value = '';
  if (!file || busy.value || (dirty.value && !await save())) return;
  try { const template = await store.importFile(file); if (template) await open(template); } catch {}
}
function settingsChanged() { dirty.value = true; report.value = null; trialHtml.value = ''; }
function changePage(value: EnterpriseTemplatePage) { if (!draft.value || busy.value) return; draft.value.pages[pageIndex.value] = value; dirty.value = true; notice.value = ''; report.value = null; trialHtml.value = ''; }
async function recognize() {
  if (!draft.value || busy.value || !await save()) return;
  recognizing.value = true; error.value = ''; notice.value = '';
  try { draft.value = await store.recognize(draft.value.id); dirty.value = true; notice.value = '已识别页面和文字标签，保留人工标签。请检查后保存或发布。'; }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '识别失败'; }
  finally { recognizing.value = false; }
}
async function publish() {
  if (!draft.value || busy.value || !await save()) return;
  saving.value = true; error.value = '';
  try { report.value = await store.check(draft.value.id); if (!report.value.valid) throw new Error('请先修正标签检查中的问题再发布'); const published = publishTemplate(draft.value); const saved = await store.update(published); draft.value = saved; dirty.value = false; notice.value = `已发布 v${saved.published!.revision}，生成页面可选择此版本`; emit('published'); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '发布失败'; }
  finally { saving.value = false; }
}
async function create() {
  if (busy.value || !await save()) return;
  saving.value = true; error.value = '';
  try { const value = createBlankTemplate(); await store.update(value); await open(value); notice.value = '已创建套装，可编辑文字、复制版式并设置页面用途。'; }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '创建失败'; }
  finally { saving.value = false; }
}
function duplicate() {
  if (!draft.value || !page.value || busy.value || draft.value.pages.length >= 40) return;
  draft.value.pages.splice(pageIndex.value + 1, 0, duplicatePage(page.value)); pageIndex.value++; dirty.value = true; report.value = null; trialHtml.value = '';
}
async function check() {
  if (!draft.value || busy.value || !await save()) return;
  saving.value = true; error.value = '';
  try { report.value = await store.check(draft.value.id); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '检查失败'; }
  finally { saving.value = false; }
}
async function trial() {
  if (!draft.value || busy.value || !await save()) return;
  saving.value = true; error.value = ''; trialHtml.value = '';
  try { trialHtml.value = await store.trial(draft.value.id, pageIndex.value); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '试填失败'; }
  finally { saving.value = false; }
}

async function remove() {
  if (!draft.value || busy.value) return;
  saving.value = true;
  try { await store.remove(draft.value.id); draft.value = store.templates[0] ? copyTemplate(store.templates[0]) : null; dirty.value = false; pageIndex.value = 0; notice.value = '模板已删除'; }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '删除失败'; }
  finally { saving.value = false; }
}

</script>
<style scoped >
.trial-stage { position: relative; overflow: hidden; width: 100%; }
.trial iframe { position: absolute; border: 0; transform-origin: top left; }
.check-report, .trial { padding: 16px; margin: 16px 0; background: #f7f7fb; border-radius: 10px; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 8px; border-bottom: 1px solid #e8e8ee; }
.template-management { --color-border: var(--border); --color-card-bg: #fff; --color-bg: #fff; --color-text-primary: #253137; --color-text-muted: #8a969e; --color-primary: var(--green); padding: 32px; max-width: 1440px; margin: 0 auto; color: var(--color-text-primary); width: 100%; box-sizing: border-box;
  header, .toolbar, .page-settings { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; } header { justify-content: space-between; } h1 { font-size: 24px; margin: 0; } p, .hint { font-size: 13px; color: var(--color-text-muted); line-height: 1.7; }
  button, input, select { background: var(--color-card-bg); color: inherit; border: 1px solid var(--color-border); border-radius: 8px; padding: 9px 12px; font: inherit; font-size: 13px; } button { cursor: pointer; } button:disabled { opacity: .5; cursor: not-allowed; } .primary { background: #7733f8; color: white; } .active { border-color: #7733f8; color: #7733f8; } label { display: flex; align-items: center; gap: 8px; }
  .library, .pages { display: flex; gap: 8px; overflow-x: auto; padding: 16px 0; button { white-space: nowrap; } span { font-size: 11px; margin-left: 8px; } }
  .font-warning { color: #925600; background: #fff8e6; padding: 10px 14px; border-radius: 8px; } .color-value { display: inline-flex; align-items: center; gap: 5px; margin-left: 10px; } .color-value i { width: 12px; height: 12px; border: 1px solid #aaa; display: inline-block; }
  .toolbar { margin-top: 16px; } .page-settings { margin-bottom: 16px; font-size: 13px; } .editor-wrap { padding: 0; margin: 0; border: 0; min-width: 0; } .empty { padding: 60px 20px; text-align: center; border: 1px dashed var(--color-border); border-radius: 12px; margin-top: 20px; } details { margin-top: 20px; font-size: 13px; line-height: 1.8; } .error { color: #d4380d; }
}
</style>
