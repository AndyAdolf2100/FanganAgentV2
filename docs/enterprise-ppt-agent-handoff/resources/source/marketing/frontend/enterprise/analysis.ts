import type { EnterpriseTemplate, EnterpriseTemplatePage, TemplatePageAnalysis, TemplateAnalysisRequest } from './types';
import { setTextRole, textOf, textRoleLabels } from './labels';
import { templatePageHtml } from './render';

export const pageRoleLabels = { cover: '封面', preface: '序言页', contents: '目录', section: '章节页', body: '正文', ending: '结束页', exclude: '不使用' };
export const layoutKindLabels = { auto: '自动识别', text: '长文正文', items: '分项介绍', comparison: '对比', timeline: '时间轴', table: '数据表格', custom: '自定义专业版式' };
export const MIN_CONFIDENCE = .75;
export interface AnalysisRow { key: string; name: string; current: string; suggested: string; confidence: number; reason: string; locked: boolean; conflict: boolean }

export async function analysisRequest(template: EnterpriseTemplate, pageIndex: number): Promise<TemplateAnalysisRequest> {
  const page = JSON.parse(JSON.stringify(template.pages[pageIndex])) as EnterpriseTemplatePage;
  const snapshot = { pageIndex, totalPages: template.pages.length, width: template.width, height: template.height, page, html: templatePageHtml(template, page) };
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(snapshot)));
  return { ...snapshot, snapshotRevision: [...new Uint8Array(digest)].map(n => n.toString(16).padStart(2, '0')).join('') };
}

export function analysisRows(page: EnterpriseTemplatePage, analysis: TemplatePageAnalysis): AnalysisRow[] {
  const suggestion = analysis.suggestion;
  const pageLocked = page.labelSource === 'manual';
  const rows: AnalysisRow[] = [{ key: 'page', name: '页面用途与结构',
    current: `${pageRoleLabels[page.role]} / ${layoutKindLabels[page.layoutKind || 'auto']}`,
    suggested: `${pageRoleLabels[suggestion.role]} / ${layoutKindLabels[suggestion.layoutKind]}`,
    confidence: suggestion.confidence, reason: suggestion.reason, locked: pageLocked,
    conflict: pageLocked && (page.role !== suggestion.role || (page.layoutKind || 'auto') !== suggestion.layoutKind) }];
  for (const item of suggestion.elements) {
    const element = page.elements.find(e => e.id === item.id);
    if (!element) continue;
    const currentRole = element.textRole || element.artworkTextRole;
    const nextRole = item.textRole || item.artworkTextRole;
    const locked = element.labelSource === 'manual';
    rows.push({ key: item.id, name: textOf(element).trim().slice(0, 40) || `轮廓元素 ${page.elements.indexOf(element) + 1}`,
      current: `${currentRole ? textRoleLabels[currentRole] : '未标记'} · ${element.order || 1}`,
      suggested: `${nextRole ? textRoleLabels[nextRole] : '未标记'} · ${item.order}`,
      confidence: item.confidence, reason: item.reason, locked,
      conflict: locked && (currentRole !== nextRole || element.order !== item.order) });
  }
  return rows;
}

export function defaultAnalysisSelection(page: EnterpriseTemplatePage, analysis: TemplatePageAnalysis) {
  return analysisRows(page, analysis).filter(row => !row.locked && row.confidence >= MIN_CONFIDENCE).map(row => row.key);
}

// Snapshot freshness is checked before calling this function. Mutate labels only.
export function applyAnalysis(page: EnterpriseTemplatePage, analysis: TemplatePageAnalysis, selected: string[]) {
  if (page.id !== analysis.pageId) throw new Error('模板页已变更，请重新分析');
  let applied = 0;
  if (selected.includes('page') && page.labelSource !== 'manual') {
    page.role = analysis.suggestion.role; page.layoutKind = analysis.suggestion.layoutKind;
    page.labelSource = 'model'; page.confidence = analysis.suggestion.confidence; page.labelReason = analysis.suggestion.reason; applied++;
  }
  for (const suggestion of analysis.suggestion.elements) {
    if (!selected.includes(suggestion.id)) continue;
    const element = page.elements.find(e => e.id === suggestion.id);
    if (!element || element.labelSource === 'manual') continue;
    if (suggestion.textRole && element.kind === 'text') setTextRole(element, suggestion.textRole, 'model');
    else if (suggestion.artworkTextRole && element.kind === 'shape' && element.vector) {
      element.artworkType = 'outlinedText'; element.artworkTextRole = suggestion.artworkTextRole;
      element.binding = 'fixed'; element.fixed = true; element.labelSource = 'model';
    } else continue;
    element.order = suggestion.order; element.confidence = suggestion.confidence; element.labelReason = suggestion.reason; applied++;
  }
  return applied;
}
