import type { EnterpriseTemplate, EnterpriseTemplatePage, TemplateElement, TemplateTextRole } from './types';
export const textRoleLabels: Record<TemplateTextRole, string> = {
  title: '页面标题', subtitle: '副标题', body: '正文', itemTitle: '分项标题', itemBody: '分项正文',
  contentsItem: '目录项', contentsNumber: '目录序号', contentsSubtitle: '目录说明', sectionNumber: '章节序号', pageNumber: '页码', date: '日期', presenter: '汇报人', brand: '品牌固定文字', decoration: '装饰文字',
};
export const textOf = (e: TemplateElement) => e.paragraphs?.map(p => p.runs.map(r => r.text).join('')).join('\n') || '';
const fontSize = (e: TemplateElement) => Math.max(0, ...(e.paragraphs?.flatMap(p => p.runs.map(r => r.size)) || []));
// Keep chapter syntax and numeric markers shared by page and element labeling.
const chapterOrdinal = '(?:[0-9]+|[IVXLCDM]+|ONE|TWO|THREE|FOUR(?:TH)?|FIVE|FIFTH|SIX(?:TH)?|SEVEN(?:TH)?|EIGHT(?:H)?|NINE|NINTH|TEN|TENTH)';
const chapterPrefix = new RegExp(`^(?:第[零〇一二三四五六七八九十百千万两0-9]+(?:部分|章节|章|节|篇|部)|(?:PART|CHAPTER|SECTION)[.．\\s]*${chapterOrdinal}(?=$|[\\s:：.．、\\u3400-\\u9fff]))`, 'i');
const chapterNumber = new RegExp(`^(?:[0-9]{1,3}|[零〇一二三四五六七八九十百两]+|(?:PART|CHAPTER|SECTION)[.．\\s]*${chapterOrdinal})[.．:：]?$`, 'i');
const chineseChapterNumber = /^第[零〇一二三四五六七八九十百千万两0-9]+(?:部分|章节|章|节|篇|部)[：:]?$/;
const isChapterNumber = (text: string) => chapterNumber.test(text) || chineseChapterNumber.test(text);
const normalizedText = (e: TemplateElement) => textOf(e).normalize('NFKC').trim();
function isChapterPage(elements: TemplateElement[]) {
  const entries = elements.map(e => ({ e, text: normalizedText(e) })).filter(item => item.text);
  if (!entries.length) return false;
  const headings = entries.filter(item => chapterPrefix.test(item.text));
  if (headings.length === 1) {
    const heading = headings[0];
    const support = entries.filter(item => item !== heading && !isChapterNumber(item.text));
    const headlineSize = Math.max(fontSize(heading.e), ...support.filter(item => item.text.length <= 40).map(item => fontSize(item.e)));
    // A short English subtitle contains many characters but few words. Accept it
    // only when its font and box clearly form a small caption, not body prose.
    const isCaption = (item: typeof entries[number]) => {
      const words = item.text.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g) || [];
      return item.text.length <= 180 && words.length >= 4 && words.length <= 30 &&
        words.join('').length / item.text.replace(/\s/g, '').length >= .85 &&
        fontSize(item.e) <= headlineSize * .45 && item.e.height <= fontSize(item.e) * 4.5;
    };
    if (heading.text.length >= 80) return false;
    const content = support.filter(item => !isCaption(item));
    // Chapter dividers can include several short agenda bullets. Long body copy,
    // multiple chapter headings and dense equal-size lists are not dividers.
    return entries.length <= 12 && content.every(item => item.text.length <= 40) &&
      content.reduce((sum,item) => sum + item.text.length,0) <= 240 &&
      (entries.length <= 4 || content.every(item => fontSize(heading.e) >= fontSize(item.e) * 1.3));
  }
  if (headings.length > 1) return false;
  return entries.length <= 4 && entries.every(item => item.text.length < 80) && entries.some(item => /^\d{1,2}[.．、\s]/.test(item.text));
}
export function setTextRole(element: TemplateElement, role: TemplateTextRole, source: 'rule' | 'model' | 'manual' = 'manual') {
  element.textRole = role; element.labelSource = source;
  if (source !== 'model') { delete element.confidence; delete element.labelReason; }
  element.binding = ['brand', 'decoration'].includes(role) ? 'fixed' : role === 'pageNumber' ? 'pageNumber' : 'content';
  element.fixed = element.binding === 'fixed';
}
export function labelPage(page: EnterpriseTemplatePage, index: number, total: number, canvasHeight: number, inferRole = false) {
  page.id ||= `page-${index + 1}`;
  // An empty DrawingML text body on vector artwork is not editable text.
  for (const e of page.elements) if (e.kind === 'text' && e.vector && !textOf(e).trim() && !e.placeholder && e.labelSource !== 'manual') {
    e.kind = 'shape'; e.fixed = true; e.binding = 'fixed'; delete e.textRole;
  }
  const refreshRules = inferRole && page.labelRuleVersion !== 8;
  const texts = page.elements.filter(e => e.kind === 'text');
  const previousRole = page.role;
  const refreshContents = inferRole && page.role === 'contents' && page.labelRuleVersion !== 8;
  if (inferRole && (!page.labelSource || (page.labelSource === 'rule' && page.labelRuleVersion !== 8))) {
    // PPTX decorative shapes often have an empty txBody; they are not content blocks.
    const candidates = texts.filter(e => !e.fixed && !['brand','decoration','pageNumber'].includes(e.textRole || '') && e.placeholder !== 'sldNum');
    const samples = candidates.map(normalizedText).filter(Boolean);
    if (index === 0) page.role = 'cover';
    else if (samples.some(t => /^序\s*言$/.test(t)) || samples.filter(t => /^[序言]$/.test(t)).join('') === '序言') page.role = 'preface';
    else if (samples.some(t => /^(目录|目次|contents|agenda)(?:$|\s|<)/i.test(t))) page.role = 'contents';
    else if (index === total - 1 && samples.some(t => /谢谢|感谢|thank\s*you|the end/i.test(t))) page.role = 'ending';
    else if (isChapterPage(candidates)) page.role = 'section';
    else page.role = 'body';
    page.labelSource = 'rule'; page.labelRuleVersion = 8;
  }
  if (previousRole !== page.role || refreshContents || refreshRules) texts.forEach(e => { if (e.labelSource === 'rule') delete e.textRole; });
  const largest = texts.filter(e => textOf(e).trim() && !e.fixed && (page.role !== 'section' || !isChapterNumber(normalizedText(e)))).sort((a,b) => fontSize(b) - fontSize(a) || a.y-b.y)[0];
  const reading = [...texts].sort((a,b) => Math.abs(a.y-b.y) < 12 ? a.x-b.x : a.y-b.y);
  page.elements.forEach((e, ei) => {
    e.id ||= `${page.id}-element-${ei + 1}`; e.slotId ||= e.id;
    if (e.kind !== 'text') return;
    e.order ??= reading.indexOf(e) + 1;
    if (e.textRole) return;
    const text = textOf(e).trim();
    let role: TemplateTextRole = 'body';
    if (page.role === 'contents' && /^\d{1,3}[.．、]?$/.test(text)) role = 'contentsNumber';
    else if (page.role === 'contents' && /^(目录|目次|agenda|contents)$/i.test(text)) role = 'title';
    else if (!text && !e.placeholder) role = 'decoration';
    else if (e.binding === 'pageNumber' || e.placeholder === 'sldNum' || (/^(页码|\d+)$/.test(text) && e.y > canvasHeight * .8)) role = 'pageNumber';
    else if (e.fixed || /^PPT NEVER SLEEPS$|copyright|©|版权所有|有限公司|股份公司/i.test(text)) role = 'brand';
    else if (/汇报人|演讲人|\u5206\u4eab[\uff1a:]|\u7b14\u8bb0[\uff1a:]|presenter|speaker/i.test(text)) role = 'presenter';
    else if (/^(日期|时间|date|20\d{2}[年./-])/i.test(text)) role = 'date';
    else if (page.role === 'section' && isChapterNumber(normalizedText(e))) role = 'sectionNumber';
    else if (page.role === 'section' && chapterPrefix.test(normalizedText(e))) role = 'title';
    else if (e.placeholder === 'title' || e.placeholder === 'ctrTitle' || e === largest) role = page.role === 'body' && texts.length === 1 && e.height > canvasHeight * .2 ? 'body' : 'title';
    else if (e.placeholder === 'subTitle' || page.role === 'cover' || page.role === 'section') role = 'subtitle';
    else if (page.role === 'contents') role = 'contentsItem';
    else if (text.length < 40 && e.height < canvasHeight * .15) role = 'itemTitle';
    setTextRole(e, role, 'rule');
  });
  if (page.role === 'contents') {
    const numbers = texts.filter(e => e.textRole === 'contentsNumber');
    for (const n of numbers) if (n.labelSource === 'rule') n.order = Math.max(1, Number(textOf(n).replace(/[.．、]/g,'')) || 1);
    const groups = new Map<TemplateElement, TemplateElement[]>();
    for (const e of texts.filter(e => ['contentsItem','contentsSubtitle'].includes(e.textRole || ''))) {
      const nearest = [...numbers].sort((a,b) => (e.x-a.x)**2+(e.y-a.y)**2 - ((e.x-b.x)**2+(e.y-b.y)**2))[0];
      if (nearest) groups.set(nearest, [...(groups.get(nearest) || []), e]);
    }
    for (const [n, items] of groups) {
      const title = [...items].sort((a,b) => fontSize(b)-fontSize(a) || a.y-b.y)[0];
      for (const e of items) if (e.labelSource === 'rule') { setTextRole(e, e === title ? 'contentsItem' : 'contentsSubtitle', 'rule'); e.order = n.order; }
    }
  }
}
// Split differently colored explicit lines into independently selectable canvas elements.
// Retain a shared source ID so generation can distribute a title without duplicating it.
export function splitColoredText(page: EnterpriseTemplatePage) {
  page.elements = page.elements.flatMap(e => {
    if (e.kind !== 'text' || e.textGroupId || e.vector || (e.fill && e.fill !== 'transparent') || e.rotation) return [e];
    const lines: { align: 'left' | 'center' | 'right'; runs: NonNullable<TemplateElement['paragraphs']>[number]['runs'] }[] = [];
    for (const p of e.paragraphs || []) {
      let line: typeof lines[number] = { align: p.align, runs: [] }; lines.push(line);
      for (const run of p.runs) run.text.split('\n').forEach((text, i) => {
        if (i) { line = { align: p.align, runs: [] }; lines.push(line); }
        if (text) line.runs.push({ ...run, text });
      });
    }
    const colors = new Set(lines.flatMap(l => l.runs.filter(r => r.text.trim()).map(r => r.color)));
    if (lines.length < 2 || colors.size < 2 || lines.some(l => !l.runs.length)) return [e];
    // Restrict automatic splitting to explicit single-color lines; mixed inline colors
    // remain rich text rather than inventing their horizontal coordinates.
    if (lines.some(l => new Set(l.runs.map(r => r.color)).size !== 1)) return [e];
    const heights = lines.map(l => Math.max(...l.runs.map(r => r.size)) * 1.2);
    if (heights.reduce((a,b) => a+b,0) + 6 > e.height) return [e];
    const padding=e.textLayout?.padding || [3,6,3,6];
    const remaining=Math.max(0,e.height-padding[0]-padding[2]-heights.reduce((a,b)=>a+b,0));
    let offset=padding[0]+(e.textLayout?.vertical==='center' ? remaining/2 : e.textLayout?.vertical==='bottom' ? remaining : 0);
    const group = e.id!;
    return lines.map((line, i) => {
      const [a,b,c,d] = e.matrix || [1,0,0,1];
      const part: TemplateElement = { ...e, id: `${group}-part-${i+1}`, slotId: `${group}-part-${i+1}`, textGroupId: group, textPartIndex: i,
        x: e.x + c*offset, y: e.y + d*offset, height: heights[i], textLayout: {wrap:e.textLayout?.wrap ?? true,vertical:'top',padding:[0,padding[1],0,padding[3]],overflow:true}, paragraphs: [line] };
      offset += heights[i];
      return part;
    });
  });
}
export function labelTemplate(template: EnterpriseTemplate, inferRole = false) {
  template.pages.forEach((p,i) => { labelPage(p,i,template.pages.length,template.height,inferRole); if (p.role === 'cover') splitColoredText(p); });
  return template;
}
