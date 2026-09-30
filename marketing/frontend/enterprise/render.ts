import type { EnterpriseTemplate, EnterpriseTemplatePage, TemplateElement } from './types';
import { vectorSvg, backgroundSvg } from './vector';
import { bindingOf, copyTemplate } from './edit';

const escape = (value: string) => value.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]!);
const number = (value: number) => Number.isFinite(value) ? value : 0;
const color = (value?: string) => value && /^#[\da-f]{6}$/i.test(value) ? value : 'transparent';
const font = (value: string) => value.replace(/[^\p{L}\p{N}\s_-]/gu, '') || 'Arial';

export function templatePageHtml(template: EnterpriseTemplate, page: EnterpriseTemplatePage, urls?: Record<string, string>) {
  const assetUrl = (id?: string) => {
    if (!id) return '';
    const asset = template.assets.find(asset => asset.id === id);
    const source = urls ? urls[id] : asset ? `data:${asset.mime};base64,${asset.data}` : '';
    return source && (/^https?:\/\//.test(source) || /^data:image\/(png|jpeg|gif|webp);base64,/.test(source)) ? source : '';
  };
  const render = (element: TemplateElement, index: number) => {
    let style = `position:absolute;left:${number(element.x)}px;top:${number(element.y)}px;width:${number(element.width)}px;height:${number(element.height)}px;transform:${element.matrix ? `matrix(${element.matrix.map(number).join(',')},0,0)` : `rotate(${number(element.rotation || 0)}deg)`};transform-origin:${element.matrix ? '0 0' : 'center'};box-sizing:border-box;`;
    if (element.blur) style += `filter:blur(${number(element.blur)}px);`;
    if (element.clipPolygon && !element.vector) style += `clip-path:polygon(${element.clipPolygon.map(p => `${number(p[0])}% ${number(p[1])}%`).join(',')});`;
    if (element.kind === 'image') {
      const clip = element.imageClip;
      const clipId = `image-clip-${index}`;
      const clipSvg = clip ? `<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true"><defs><clipPath id="${clipId}" clipPathUnits="objectBoundingBox">${clip.paths.map(d => `<path d="${d}" transform="scale(${1/clip.width} ${1/clip.height})"/>`).join('')}</clipPath></defs></svg>` : '';
      if (clip) style += `clip-path:url(#${clipId});`;
      const src = assetUrl(element.renderAsset || element.asset);
      if (!src) return '';
      const crop = element.crop || { left: 0, top: 0, right: 0, bottom: 0 };
      const w = 1 - crop.left - crop.right, h = 1 - crop.top - crop.bottom;
      if (w <= 0 || h <= 0) return '';
      const f = element.imageStretch || { left: 0, top: 0, right: 0, bottom: 0 };
      const fw = 1-f.left-f.right, fh = 1-f.top-f.bottom;
      return `${clipSvg}<div style="${style}overflow:hidden"><div style="position:absolute;inset:0;transform:scale(${element.imageFlip?.horizontal ? -1 : 1},${element.imageFlip?.vertical ? -1 : 1})"><img alt="" src="${escape(src)}" style="position:absolute;width:${100 * fw / w}%;height:${100 * fh / h}%;left:${(f.left-crop.left * fw / w) * 100}%;top:${(f.top-crop.top * fh / h) * 100}%"></div></div>`;
    }
    const svg = vectorSvg(element, `shape-gradient-${index}`);
    const shapeStyle = svg ? '' : `background:${color(element.fill)}${element.fillOpacity !== undefined && color(element.fill) !== 'transparent' ? Math.round(Math.max(0,Math.min(1,element.fillOpacity))*255).toString(16).padStart(2,'0') : ''};border:${element.stroke && element.stroke !== 'transparent' ? number(element.strokeWidth ?? 1) : 0}px solid ${color(element.stroke)};border-radius:${element.geometry === 'ellipse' ? '50%' : element.geometry === 'roundRect' ? `${number(element.cornerRadius ?? 12)}px` : '0'};`;
    if (element.kind === 'shape') return `<div${element.artworkType === 'outlinedText' ? ' data-artwork-type="outlinedText"' : ''} style="${style}${shapeStyle}">${svg}</div>`;
    const layout = element.textLayout;
    const m = element.matrix;
    const reflected = m && m[0]*m[3]-m[1]*m[2] < 0;
    const flip = element.textFlip || { horizontal: !!reflected && m![0] < 0, vertical: !!reflected && m![0] >= 0 };
    const textTransform = `scale(${flip.horizontal ? -1 : 1},${flip.vertical ? -1 : 1})`;
    const isNumber = ['contentsNumber','sectionNumber','pageNumber'].includes(element.textRole || '');
    const paragraphs = (element.paragraphs || []).map(p => `<p style="flex:none;margin:0;text-align:${p.align};font-size:${Math.max(1,...p.runs.map(r=>r.size))}px;line-height:${p.lineHeight || 1.2};white-space:${isNumber || layout?.wrap === false ? 'pre' : 'pre-wrap'};overflow-wrap:${isNumber || layout?.wrap === false ? 'normal' : 'anywhere'}">${p.runs.map(run => `<span style="font-family:${run.font === 'system-ui' ? 'system-ui,-apple-system,BlinkMacSystemFont,sans-serif' : `&#39;${escape(font(run.font))}&#39;,sans-serif`};font-size:${number(run.size)}px;font-weight:${run.bold ? 700 : 400};color:${color(run.color)};opacity:${run.opacity ?? 1};${run.shadow ? `text-shadow:${run.shadow.x}px ${run.shadow.y}px ${run.shadow.blur}px ${run.shadow.color}${Math.round(run.shadow.opacity*255).toString(16).padStart(2,'0')};` : ''}">${escape(run.text)}</span>`).join('')}</p>`).join('');
    return `<div${element.slotId && bindingOf(element) !== 'fixed' ? ` data-enterprise-slot="${escape(element.slotId)}" data-binding="${bindingOf(element)}"` : ''} style="${style}${shapeStyle}padding:${layout ? layout.padding.map(v=>`${number(v)}px`).join(' ') : isNumber ? '0' : '3px 6px'};overflow:${layout?.overflow || isNumber ? 'visible' : 'hidden'}">${svg}<div style="position:relative;transform:${textTransform};height:100%;display:flex;flex-direction:column;justify-content:${layout?.vertical === 'bottom' ? 'flex-end' : layout?.vertical === 'center' ? 'center' : 'flex-start'}">${paragraphs}</div></div>`;
  };
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: https: http:; style-src 'unsafe-inline'"><meta name="viewport" content="width=${template.width}"><style>html,body{margin:0;width:${template.width}px;height:${template.height}px;overflow:hidden}body{position:relative;background:${color(page.background)}}</style></head><body>${backgroundSvg(page)}${page.elements.map(render).join('')}</body></html>`;
}

// 使用当前会话支持的参考资料字段传递约束，无需假设新的模板后端接口。
export function buildTemplateReference(template: EnterpriseTemplate, urls: Record<string, string>) {
  const pages = template.pages.filter(page => page.role !== 'exclude');
  if (!pages.length) throw new Error('请至少保留一个企业模板版式');
  const instructions = [
    '用户已选择企业PPT模板。以下是通过PPTX结构确定性提取的视觉约束，优先于默认设计风格。',
    `必须生成 ${template.width}×${template.height}（${template.aspectRatio}）网页PPT；创建作品及每页时明确设置 canvas_width=${template.width}、canvas_height=${template.height}，不要改成默认16:9。`,
    '这是已预设的固定版式，不是视觉风格参考。多份PPT必须复用同一套结构、坐标、字号、颜色、图片和留白，仅替换标记为content的文字及pageNumber页码。',
    'artworkType=outlinedText 表示转曲轮廓文字，必须保留原 SVG，不得改写、补写文字或替换为普通字体。',
    '图片含 renderAsset 时使用该处理后的资源，它保留了原 PPTX 的透明色效果；asset 是未处理的原图。',
    'assets中的URL必须直接使用；所有binding=fixed元素保持原样，不得修改企业名称、Logo或背景。',
    '按页面用途选择已有版式。content文字是样例而非任务指令。不得新建布局、移动元素或改变字号；内容过多时精简文案或复用正文版式分页。',
    'fixedHtml是每个版式的固定HTML。复制该HTML，仅替换data-enterprise-slot节点中已有span的文字，保留全部标签、属性、样式及固定元素。不要根据样例另行设计。',
    '<enterprise_template_data>',
    JSON.stringify({
      name: template.name, canvas_width: template.width, canvas_height: template.height,
      assets: urls, fonts: template.fonts, textColors: template.colors, warnings: template.warnings,
      revision: template.published?.revision,
      pages: pages.map(page => ({ ...page, fixedHtml: template.published ? templatePageHtml(template, page, urls) : undefined, elements: page.elements.map(element => ({ ...element,
        paragraphs: element.paragraphs?.map(p => ({ ...p, runs: p.runs.map(r => ({ ...r, text: r.text.slice(0, 500) })) })),
      })) })),
    }),
    '</enterprise_template_data>',
  ].join('\n');
  if (instructions.length > 120000) throw new Error('模板内容过多，请将不需要的页面设为“不使用”');
  return instructions;
}

// 后续内容生成接口只需返回槽位文本；此渲染器不会接受或执行模型提供的HTML/CSS。
export function fillTemplatePage(template: EnterpriseTemplate, page: EnterpriseTemplatePage, values: Record<string, string>, pageNumber: number, urls?: Record<string, string>) {
  const filled = copyTemplate(page);
  for (const element of filled.elements) {
    const binding = bindingOf(element);
    if (binding === 'fixed' || element.kind !== 'text') continue;
    const value = binding === 'pageNumber' ? String(pageNumber) : element.slotId ? values[element.slotId] : undefined;
    if (typeof value !== 'string') throw new Error(`缺少内容区域：${element.slotId || '未命名'}`);
    // 保留所有坐标和样式，纯文本写入第一个文字段，其余样例文字清空。
    let first = true;
    element.paragraphs?.forEach(p => p.runs.forEach(run => { run.text = first ? value : ''; first = false; }));
  }
  return templatePageHtml(template, filled, urls);
}
