import { labelTemplate } from './labels';
import type { EnterpriseTemplate, EnterpriseTemplatePage, TemplateElement } from './types';

export const copyTemplate = <T>(value: T): T => JSON.parse(JSON.stringify(value));
export function textValue(element: TemplateElement) {
  return element.paragraphs?.map(p => p.runs.map(r => r.text).join('')).join('\n') || '';
}
export function bindingOf(element: TemplateElement) {
  if (element.binding) return element.binding;
  if (element.kind !== 'text' || element.fixed) return 'fixed';
  return element.placeholder === 'sldNum' || /^(页码|\d+)$/.test(textValue(element).trim()) ? 'pageNumber' : 'content';
}
export function moveElement(element: TemplateElement, dx: number, dy: number, width: number, height: number, resize = false): TemplateElement {
  if (resize && element.matrix) {
    const [a,b,c,d] = element.matrix, determinant = a*d-b*c;
    if (Math.abs(determinant) > 1e-8) { const x = (d*dx-c*dy)/determinant; dy = (-b*dx+a*dy)/determinant; dx = x; }
  }
  if (resize) return { ...element, width: Math.max(20, Math.min(width - element.x, element.width + dx)), height: Math.max(20, Math.min(height - element.y, element.height + dy)) };
  return { ...element, x: Math.max(0, Math.min(width - element.width, element.x + dx)), y: Math.max(0, Math.min(height - element.height, element.y + dy)) };
}
export function addText(template: EnterpriseTemplate, page: EnterpriseTemplatePage, title: boolean) {
  page.elements.push({ id: `element-${crypto.randomUUID()}`, kind: 'text', x: template.width * .08, y: template.height * (title ? .12 : .28), width: template.width * .76,
    height: template.height * (title ? .1 : .5), binding: 'content', fixed: false, textRole: title ? 'title' : 'body', labelSource: 'manual',
    paragraphs: [{ align: 'left', runs: [{ text: title ? '在此填写标题' : '在此填写正文内容', font: template.fonts[0] || '微软雅黑', size: title ? 36 : 24, color: '#222222', bold: title }] }] });
}
export function publishTemplate(template: EnterpriseTemplate): EnterpriseTemplate {
  const result = labelTemplate(copyTemplate(template));
  if (!result.name.trim()) throw new Error('请填写模板名称');
  const enabled = result.pages.filter(p => p.role !== 'exclude');
  const content = (e: TemplateElement) => bindingOf(e) === 'content' || (e.artworkType === 'outlinedText' && !['brand', 'decoration'].includes(e.artworkTextRole || 'title'));
  if (!enabled.length) throw new Error('请至少启用一个版式');
  if (!enabled.some(p => p.elements.some(content))) throw new Error('请添加至少一个可替换的内容区域');
  if (enabled.some(p => p.role === 'body' && !p.elements.some(content))) throw new Error('正文版式缺少内容区域，请先添加标题或正文');
  if (!enabled.some(p => p.role === 'body' && p.elements.some(e => content(e) && ['body', 'itemBody'].includes(e.artworkType === 'outlinedText' ? e.artworkTextRole || '' : e.textRole || '')))) throw new Error('请标记至少一个正文或分项正文区域后发布');
  result.pages.forEach((page, pi) => page.elements.forEach((element, ei) => {
    element.binding = bindingOf(element);
    element.id ||= `${page.id}-element-${ei + 1}`;
    element.slotId ||= element.id;
  }));
  result.published = { componentSchema: 1, revision: (template.published?.revision || 0) + 1, name: result.name.trim(), pages: copyTemplate(result.pages) };
  return result;
}

// Keep PPTX text runs intact: editing one input must not flatten adjacent colors/styles.
export function editableTextParts(element: TemplateElement) {
  return (element.paragraphs || []).flatMap((p, pi) => p.runs.map((run, ri) => ({ pi, ri, run })))
    .filter(part => part.run.text !== '\n' && part.run.text !== '\r\n');
}
export function editTextPart(element: TemplateElement, pi: number, ri: number, patch: Partial<import('./types').TemplateTextRun>) {
  const result = copyTemplate(element);
  const run = result.paragraphs?.[pi]?.runs[ri];
  if (run) Object.assign(run, patch);
  return result;
}


export function createBlankTemplate(): EnterpriseTemplate {
  const id = [...crypto.getRandomValues(new Uint8Array(32))].map(n => n.toString(16).padStart(2, '0')).join('');
  const template: EnterpriseTemplate = { version: 1, id, name: '新企业套装', createdAt: Date.now(), width: 1200, height: 675,
    originalWidth: 1200, originalHeight: 675, aspectRatio: '16:9', pages: [], assets: [], fonts: ['system-ui'], colors: ['#222222'], warnings: [] };
  for (const role of ['cover', 'body', 'ending'] as const) {
    const page: EnterpriseTemplatePage = { id: crypto.randomUUID(), name: role === 'cover' ? '首页' : role === 'body' ? '长文正文' : '尾页', role, layoutKind: 'text', labelSource: 'manual', background: '#FFFFFF', elements: [] };
    addText(template, page, true);
    page.elements[0].order = 1; page.elements[0].fieldName = 'PageTitle';
    if (role !== 'body') { page.elements[0].y = 220; page.elements[0].height = 160; }
    if (role === 'ending') page.elements[0].paragraphs![0].runs[0].text = '谢谢';
    if (role === 'body') { addText(template, page, false); page.elements[1].order = 1; page.elements[1].fieldName = 'BodyText'; }
    template.pages.push(page);
  }
  return template;
}

export function duplicatePage(page: EnterpriseTemplatePage): EnterpriseTemplatePage {
  const result = copyTemplate(page); result.id = crypto.randomUUID(); result.name += '（副本）';
  result.elements.forEach(e => { e.id = crypto.randomUUID(); e.slotId = e.id; });
  return result;
}
