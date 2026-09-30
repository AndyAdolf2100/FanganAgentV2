<template>
  <div class="template-editor">
    <div ref="host" class="stage" :style="{ aspectRatio: `${template.width}/${template.height}` }">
      <div class="canvas" :style="{ width: `${template.width}px`, height: `${template.height}px`, transform: `scale(${scale})` }">
        <iframe :srcdoc="html" title="企业模板版式编辑画布" sandbox="" />
        <button v-for="(element, index) in page.elements" :key="index" type="button" class="element" :class="{ selected: selected === index, locked: bindingOf(element) === 'fixed', tagged: showLabels && element.kind === 'text' }"
          :style="{ ...box(element), zIndex: selected === index ? 3 : element.kind === 'text' && textValue(element).trim() ? 2 : 1, pointerEvents: element.kind === 'text' && textValue(element).trim() || selected === index ? 'auto' : 'none' }" :aria-label="`选择元素 ${index + 1}：${element.kind === 'text' ? textValue(element) : '背景或装饰'}`"
          @pointerdown="start($event, index, false)" @click="selected = index" @keydown="moveByKey($event, index)">
          <span v-if="showLabels && element.textRole" class="tag">{{ textRoleLabels[element.textRole] }} · {{ element.order }}{{ element.fieldName ? ` · ${element.fieldName}` : '' }}</span>
          <span v-if="selected === index && canMove(element)" class="handle" @pointerdown.stop="start($event, index, true)"></span>
        </button>
      </div>
    </div>
    <aside>
      <label><span><input v-model="showLabels" type="checkbox" />显示文字区域标签</span></label>
      <div class="tools"><button @click="insert(true)">添加标题</button><button @click="insert(false)">添加正文</button></div>
      <label>选择元素<select v-model.number="selected"><option :value="-1">请选择</option><option v-for="(element, index) in page.elements" :key="index" :value="index">{{ index + 1 }} · {{ element.kind === 'text' ? textValue(element).slice(0, 22) : element.kind === 'image' ? '图片' : element.artworkType === 'outlinedText' ? '矢量文字' : '固定图形' }}</option></select></label>
      <template v-if="current">
        <p v-if="current.labelSource === 'model'">模型建议 · 置信度 {{ Math.round((current.confidence || 0) * 100) }}%<br />{{ current.labelReason }}</p>
        <p v-else-if="current.labelSource === 'manual'">人工标签已锁定，自动分析仅展示建议。</p>
        <label v-if="current.kind === 'shape' && current.vector"><span><input type="checkbox" :checked="current.artworkType === 'outlinedText'" @change="setOutlinedText(($event.target as HTMLInputElement).checked)" />轮廓文字（转曲）</span></label>
        <template v-if="current.artworkType === 'outlinedText'">
          <label>矢量文字用途<select :value="current.artworkTextRole || 'title'" @change="setArtworkRole(($event.target as HTMLSelectElement).value)"><option value="title">标题</option><option value="sectionNumber">章节序号</option><option value="subtitle">副标题</option><option value="body">正文</option><option value="brand">固定品牌文字</option><option value="decoration">固定装饰文字</option></select></label>
          <p>生成时可按此用途改写矢量文字，沿用模板风格与位置。章节页只开放标题和序号；固定品牌与装饰文字保持原样。原转曲对象没有可恢复的字体信息。</p>
        </template>
        <label v-if="current.kind === 'text' || current.artworkType === 'outlinedText'">填充字段名<input :value="current.fieldName || ''" placeholder="例如 Item1Title（可留空自动生成）" maxlength="80" @change="setFieldName(($event.target as HTMLInputElement).value)" /></label>
        <label>元素用途<select :value="bindingOf(current)" @change="setBinding(($event.target as HTMLSelectElement).value)"><option value="fixed">固定品牌元素（锁定）</option><template v-if="current.kind === 'text'"><option value="content">可替换内容</option><option value="pageNumber">自动页码</option></template></select></label>
        <label v-if="bindingOf(current) === 'fixed'"><span><input v-model="editFixed" type="checkbox" />允许调整固定元素的位置</span></label>
        <template v-if="canMove(current)">
          <div class="geometry"><label v-for="key in dimensions" :key="key">{{ dimensionLabels[key] }}<input type="number" :value="current[key]" @change="setDimension(key, ($event.target as HTMLInputElement).value)" /></label></div>
        </template>
        <template v-if="current.kind === 'text'">
          <label>文字标签<select :value="current.textRole" @change="setRole(($event.target as HTMLSelectElement).value as TemplateTextRole)"><option v-for="(label, role) in textRoleLabels" :key="role" :value="role">{{ label }}</option></select></label>
          <label>条目配对顺序<input type="number" min="1" max="2000" :value="current.order" @change="setOrder(Number(($event.target as HTMLInputElement).value))" /></label>
          <small>同一项的标题与正文使用相同顺序；字段名在当前页内唯一。</small>
          <div v-for="(part, index) in textParts" :key="`${selected}-${part.pi}-${part.ri}`" class="text-part">
            <label>{{ textParts.length > 1 ? `文字分段 ${index + 1}` : '示例文字' }}<textarea :style="{ color: part.run.color, fontFamily: part.run.font }" :value="part.run.text" rows="2" @input="changePart(part.pi, part.ri, 'text', ($event.target as HTMLTextAreaElement).value)" /></label>
            <label>字体<input :value="part.run.font" @change="changePart(part.pi, part.ri, 'font', ($event.target as HTMLInputElement).value)" /></label>
            <small>system-ui 使用系统默认字体<span v-if="part.run.sourceFont">；原字体（导出参考）：{{ part.run.sourceFont }}</span></small>
            <label>字号<input type="number" min="8" max="180" :value="part.run.size" @change="changePart(part.pi, part.ri, 'size', Number(($event.target as HTMLInputElement).value))" /></label>
            <label>颜色<input type="color" :value="part.run.color" @input="changePart(part.pi, part.ri, 'color', ($event.target as HTMLInputElement).value)" /></label>
          </div>
          <button @click="remove">删除文字元素</button>
        </template>
      </template>
      <p>拖动内容框移动，拖动右下角调整大小；方向键微调，Shift + 方向键移动 10px。固定品牌元素不参与内容替换。</p>
    </aside>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, onBeforeUnmount, watch } from 'vue';
import { onMounted } from 'vue';
import type { EnterpriseTemplate, EnterpriseTemplatePage, TemplateElement, TemplateTextRole } from './types';
import { textRoleLabels, setTextRole, labelPage } from './labels';
import { templatePageHtml } from './render';
import { addText, bindingOf, copyTemplate, moveElement, textValue, editableTextParts, editTextPart } from './edit';
const props = defineProps<{ template: EnterpriseTemplate; page: EnterpriseTemplatePage }>();
const emit = defineEmits<{ change: [page: EnterpriseTemplatePage] }>();
const host = ref<HTMLElement | null>(null);
const width = ref(0);
let observer: ResizeObserver;
onMounted(() => { observer = new ResizeObserver(entries => { width.value = entries[0].contentRect.width; }); if (host.value) observer.observe(host.value); });
onBeforeUnmount(() => observer?.disconnect());
const scale = computed(() => width.value / props.template.width || 1);
const selected = ref(-1);
const editFixed = ref(false);
const showLabels = ref(true);
const canMove = (element: TemplateElement) => bindingOf(element) !== 'fixed' || editFixed.value;
watch(selected, () => { editFixed.value = false; });
const current = computed(() => props.page.elements[selected.value]);
const textParts = computed(() => current.value ? editableTextParts(current.value) : []);
const html = computed(() => templatePageHtml(props.template, props.page));
const dimensions = ['x', 'y', 'width', 'height'] as const;
const dimensionLabels = { x: '横坐标', y: '纵坐标', width: '宽度', height: '高度' };
function box(e: TemplateElement) { return { left: `${e.x}px`, top: `${e.y}px`, width: `${e.width}px`, height: `${e.height}px`, transform: e.matrix ? `matrix(${e.matrix.join(',')},0,0)` : `rotate(${e.rotation || 0}deg)`, transformOrigin: e.matrix ? '0 0' : 'center' }; }
function update(element: TemplateElement, index = selected.value) {
  const page = copyTemplate(props.page); page.elements[index] = element; emit('change', page);
}
let stopDrag: (() => void) | undefined;
function start(event: PointerEvent, index: number, resize: boolean) {
  if (event.button !== 0) return;
  selected.value = index;
  const element = copyTemplate(props.page.elements[index]);
  if (!canMove(element)) return;
  event.preventDefault(); stopDrag?.();
  const startX = event.clientX, startY = event.clientY, zoom = scale.value;
  const move = (e: PointerEvent) => update(moveElement(element, (e.clientX - startX) / zoom, (e.clientY - startY) / zoom, props.template.width, props.template.height, resize), index);
  const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); window.removeEventListener('pointercancel', stop); stopDrag = undefined; };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop); window.addEventListener('pointercancel', stop); stopDrag = stop;
}
onBeforeUnmount(() => stopDrag?.());
watch(() => props.page.name, () => { selected.value = -1; stopDrag?.(); });
function moveByKey(event: KeyboardEvent, index: number) {
  const vector = ({ ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] } as Record<string, number[]>)[event.key];
  const element = props.page.elements[index];
  if (!vector || !canMove(element)) return;
  event.preventDefault(); const step = event.shiftKey ? 10 : 1;
  update(moveElement(element, vector[0] * step, vector[1] * step, props.template.width, props.template.height), index);
}
function insert(title: boolean) { const page = copyTemplate(props.page); addText(props.template, page, title); labelPage(page, 0, 1, props.template.height); emit('change', page); selected.value = page.elements.length - 1; }
function setOutlinedText(enabled: boolean) {
  if (!current.value || current.value.kind !== 'shape' || !current.value.vector) return;
  const element = copyTemplate(current.value);
  element.labelSource = 'manual'; delete element.confidence; delete element.labelReason;
  if (enabled) { element.artworkType = 'outlinedText'; element.artworkTextRole ||= 'title'; element.fixed = true; element.binding = 'fixed'; }
  else { delete element.artworkType; delete element.artworkTextRole; }
  update(element);
}
function setArtworkRole(role: string) {
  if (!current.value || !['title', 'subtitle', 'sectionNumber', 'body', 'brand', 'decoration'].includes(role)) return;
  update({ ...current.value, artworkTextRole: role as TemplateElement['artworkTextRole'], labelSource: 'manual', confidence: undefined, labelReason: undefined });
}
function setRole(role: TemplateTextRole) { if (!current.value || !textRoleLabels[role]) return; const e = copyTemplate(current.value); setTextRole(e, role); update(e); }
function setFieldName(value: string) { if (current.value) update({ ...current.value, fieldName: value.trim(), labelSource: 'manual' }); }
function setOrder(order: number) { if (current.value && Number.isInteger(order) && order >= 1 && order <= 2000) update({ ...current.value, order, labelSource: 'manual' }); }
function setBinding(value: string) { if (!current.value) return; if (current.value.kind === 'text') setRole(value === 'fixed' ? 'brand' : value === 'pageNumber' ? 'pageNumber' : 'body'); }
function setDimension(key: typeof dimensions[number], value: string) {
  if (!current.value || !Number.isFinite(Number(value))) return;
  const element = { ...current.value, [key]: Number(value) };
  element.width = Math.max(20, Math.min(props.template.width, element.width)); element.height = Math.max(20, Math.min(props.template.height, element.height));
  update(moveElement(element, 0, 0, props.template.width, props.template.height));
}
function changePart(pi: number, ri: number, key: 'text' | 'font' | 'size' | 'color', value: string | number) {
  if (!current.value || (key === 'size' && (!Number.isFinite(value) || Number(value) < 8 || Number(value) > 180))) return;
  update(editTextPart(current.value, pi, ri, { [key]: value }));
}
function remove() { const page = copyTemplate(props.page); page.elements.splice(selected.value, 1); selected.value = -1; emit('change', page); }
</script>
<style scoped >
.template-editor { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: 20px; align-items: start; }
.stage { position: relative; overflow: hidden; background: white; box-shadow: 0 0 0 1px #ddd; }
.canvas { position: absolute; transform-origin: top left; iframe { width: 100%; height: 100%; border: 0; pointer-events: none; } }
.element { position: absolute; border: 1px solid transparent; background: transparent; padding: 0; cursor: move; touch-action: none; &:hover, &:focus-visible { border: 2px dashed #7733f8; } &.selected { border: 2px solid #7733f8; } &.locked { cursor: default; } }
.tag { position: absolute; top: -17px; left: 0; background: #7733f8; color: white; white-space: nowrap; font-size: 12px; padding: 1px 4px; pointer-events: none; }
.element.tagged { border: 1px dashed #7733f899; }
.template-editor .canvas .element { background: transparent; padding: 0; border-radius: 0; }
.handle { position: absolute; width: 16px; height: 16px; bottom: -8px; right: -8px; background: #7733f8; cursor: se-resize; }
aside { display: flex; flex-direction: column; gap: 12px; font-size: 13px; label { display: flex; flex-direction: column; gap: 6px; } input, select, textarea, button { width: 100%; box-sizing: border-box; padding: 7px; background: var(--color-card-bg); color: inherit; border: 1px solid var(--color-border); border-radius: 6px; } p { line-height: 1.6; color: var(--color-text-muted); } }
.text-part { display: grid; gap: 8px; border: 1px solid var(--color-border); border-radius: 8px; padding: 10px; }
.tools, .geometry { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media(max-width: 800px) { .template-editor { grid-template-columns: minmax(0, 1fr); } }
</style>
