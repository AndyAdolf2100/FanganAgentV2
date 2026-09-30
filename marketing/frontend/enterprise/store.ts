import { prepareImageEffects } from './image-effects';
import { labelTemplate } from './labels';
import { reactive } from 'vue';
import type { EnterpriseTemplate } from './types';
import { parseEnterpriseTemplate } from './parser';
async function request(path = '', options: RequestInit = {}) {
  const response = await fetch(`/api/enterprise-templates${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '模板操作失败，请重试');
  return data;
}
const store = reactive({
  templates: [] as EnterpriseTemplate[], busy: false, error: '',
  async init() {
    this.busy = true;
    try { this.templates = (await request()).templates; this.error = ''; }
    catch (e) { this.error = e instanceof Error ? e.message : '模板列表读取失败'; throw e; }
    finally { this.busy = false; }
  },
  async importFile(file: File) {
    if (this.busy) return;
    this.busy = true; this.error = '';
    try {
      const template = await parseEnterpriseTemplate(file);
      const existing = this.templates.find(t => t.id === template.id);
      if (existing) {
        const migrated = labelTemplate(JSON.parse(JSON.stringify(existing)), true);
        migrated.warnings = template.warnings;
        for (const page of migrated.pages) {
          const source = template.pages.find(p => p.id === page.id);
          if (!page.backgroundPattern && source?.backgroundPattern) page.backgroundPattern=source.backgroundPattern;
          for (const e of page.elements) {
            const original = source?.elements.find(candidate => candidate.id === e.id);
            for (const key of ['imageColorChange','imageFlip','cornerRadius','strokeWidth','strokeOpacity','blur','shadow','glow'] as const) {
              if (e[key] === undefined && original?.[key] !== undefined) Object.assign(e,{[key]:original[key]});
            }
            if (original?.fillSource==='group' && e.fill==='transparent') { e.fill=original.fill;e.fillOpacity=original.fillOpacity;e.fillSource='group'; }
            e.paragraphs?.forEach((paragraph,pi)=>paragraph.runs.forEach((run,ri)=>{
              const originalRun=original?.paragraphs?.[pi]?.runs[ri];
              const scale=original?.textLayout?.fontScale;
              if (scale && scale!==1 && e.textLayout?.fontScale===undefined && originalRun && Math.abs(run.size*scale-originalRun.size)<.02) run.size=originalRun.size;
              if (!run.sourceFont && originalRun?.sourceFont) {
                run.sourceFont = originalRun.sourceFont;
                if (run.font === originalRun.sourceFont) run.font = 'system-ui';
              }
              if (!run.shadow && originalRun?.shadow && run.text===originalRun.text) run.shadow=originalRun.shadow;
              const opacity=originalRun?.opacity;
              if (run.opacity===undefined && opacity!==undefined) run.opacity=opacity;
            }));
            if (e.textLayout && original?.textLayout?.fontScale!==undefined) e.textLayout.fontScale=original.textLayout.fontScale;
            if (!e.textFlip && original?.textFlip) e.textFlip = original.textFlip;
            if (!e.textLayout && original?.textLayout) e.textLayout = original.textLayout;
            if (!e.textTransformVersion && original?.textTransformVersion && e.matrix) {
              const [a,b,c,d] = e.matrix, sx = Math.hypot(a,b), sy = Math.hypot(c,d);
              if (sx && sy) { e.width *= sx; e.height *= sy; e.matrix = [a/sx,b/sx,c/sy,d/sy]; e.textTransformVersion = 1; }
            }
            for (const key of ['vector','imageClip'] as const) {
              const geometry=e[key];
              if (geometry && Math.max(geometry.width,geometry.height)>100000) {
                geometry.paths=geometry.paths.map(path=>{let coordinate=0;return path.replace(/-?\d+(?:\.\d+)?/g,value=>String(Number(value)*1000/(coordinate++%2 ? geometry.height : geometry.width)));});
                geometry.width=1000;geometry.height=1000;
              }
            }
            if (e.vector && e.clipPolygon && !original?.clipPolygon) delete e.clipPolygon;
            if (!e.vector && original?.vector && ['ellipse','roundRect','rect'].includes(e.geometry || '')) e.vector = original.vector;
            if (e.vector && original?.vector) for (const key of ['gradient','strokeGradient'] as const) {
              if (!e.vector[key] && original.vector[key]) e.vector[key]=original.vector[key];
              e.vector[key]?.stops.sort((a,b)=>a.offset-b.offset);
            }
            if (!e.imageClip && original?.imageClip) e.imageClip = original.imageClip;
          }
          for (const shadow of source?.elements.filter(e=>e.id?.includes('-group-shadow-')) || []) {
            if (!page.elements.some(e=>e.id===shadow.id)) {
              const next=source!.elements.slice(source!.elements.indexOf(shadow)+1).find(e=>page.elements.some(old=>old.id===e.id));
              const position=next ? page.elements.findIndex(e=>e.id===next.id) : page.elements.length;
              page.elements.splice(position,0,shadow);
            }
          }
          for (const connector of source?.elements.filter(e=>e.id?.includes('-connector-') || e.id?.includes('-restored-line-')) || []) {
            if (!page.elements.some(e=>e.id===connector.id)) page.elements.push(connector);
          }
        }
        await prepareImageEffects(migrated);
        const saved = await request(`/${template.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(migrated) });
        this.templates = this.templates.map(t => t.id === saved.id ? saved : t);
        return saved;
      }
      const saved = await request(`/${template.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(template) });
      this.templates.unshift(saved); return saved;
    } catch (e) { this.error = e instanceof Error ? e.message : '导入失败'; throw e; }
    finally { this.busy = false; }
  },
  async update(template: EnterpriseTemplate) {
    const saved = await request(`/${template.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(template) });
    this.templates = this.templates.some(t => t.id === saved.id) ? this.templates.map(t => t.id === saved.id ? saved : t) : [saved, ...this.templates];
    return saved;
  },
  async check(id: string) { return request(`/${id}/check`); },
  async trial(id: string, page: number) {
    const response = await fetch(`/api/enterprise-templates/${id}/trial?page=${page}`);
    if (!response.ok) { const data = await response.json(); throw new Error(data.detail || '试填失败'); }
    return response.text();
  },
  async remove(id: string) {
    await request(`/${id}`, { method: 'DELETE' }); this.templates = this.templates.filter(t => t.id !== id);
  },
});
export const useEnterpriseTemplateStore = () => store;
