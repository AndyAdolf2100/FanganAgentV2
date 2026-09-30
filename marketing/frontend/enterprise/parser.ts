import JSZip from 'jszip';
import { prepareImageEffects } from './image-effects';
import { labelTemplate } from './labels';
import type { EnterpriseTemplate, EnterpriseTemplatePage, TemplateElement, TemplateTextRun } from './types';

export const MAX_TEMPLATE_BYTES = 30 * 1024 * 1024;
const MAX_UNPACKED_BYTES = 120 * 1024 * 1024;
const MAX_PAGES = 40;
// DrawingML preset names use CSS colors, with dk/lt/med aliases.
const PRESET_COLORS: Record<string, string> = {
  "aliceblue": "F0F8FF",
  "antiquewhite": "FAEBD7",
  "aqua": "00FFFF",
  "aquamarine": "7FFFD4",
  "azure": "F0FFFF",
  "beige": "F5F5DC",
  "bisque": "FFE4C4",
  "black": "000000",
  "blanchedalmond": "FFEBCD",
  "blue": "0000FF",
  "blueviolet": "8A2BE2",
  "brown": "A52A2A",
  "burlywood": "DEB887",
  "cadetblue": "5F9EA0",
  "chartreuse": "7FFF00",
  "chocolate": "D2691E",
  "coral": "FF7F50",
  "cornflowerblue": "6495ED",
  "cornsilk": "FFF8DC",
  "crimson": "DC143C",
  "cyan": "00FFFF",
  "darkblue": "00008B",
  "darkcyan": "008B8B",
  "darkgoldenrod": "B8860B",
  "darkgray": "A9A9A9",
  "darkgrey": "A9A9A9",
  "darkgreen": "006400",
  "darkkhaki": "BDB76B",
  "darkmagenta": "8B008B",
  "darkolivegreen": "556B2F",
  "darkorange": "FF8C00",
  "darkorchid": "9932CC",
  "darkred": "8B0000",
  "darksalmon": "E9967A",
  "darkseagreen": "8FBC8F",
  "darkslateblue": "483D8B",
  "darkslategray": "2F4F4F",
  "darkslategrey": "2F4F4F",
  "darkturquoise": "00CED1",
  "darkviolet": "9400D3",
  "deeppink": "FF1493",
  "deepskyblue": "00BFFF",
  "dimgray": "696969",
  "dimgrey": "696969",
  "dodgerblue": "1E90FF",
  "firebrick": "B22222",
  "floralwhite": "FFFAF0",
  "forestgreen": "228B22",
  "fuchsia": "FF00FF",
  "gainsboro": "DCDCDC",
  "ghostwhite": "F8F8FF",
  "gold": "FFD700",
  "goldenrod": "DAA520",
  "gray": "808080",
  "grey": "808080",
  "green": "008000",
  "greenyellow": "ADFF2F",
  "honeydew": "F0FFF0",
  "hotpink": "FF69B4",
  "indianred": "CD5C5C",
  "indigo": "4B0082",
  "ivory": "FFFFF0",
  "khaki": "F0E68C",
  "lavender": "E6E6FA",
  "lavenderblush": "FFF0F5",
  "lawngreen": "7CFC00",
  "lemonchiffon": "FFFACD",
  "lightblue": "ADD8E6",
  "lightcoral": "F08080",
  "lightcyan": "E0FFFF",
  "lightgoldenrodyellow": "FAFAD2",
  "lightgreen": "90EE90",
  "lightgray": "D3D3D3",
  "lightgrey": "D3D3D3",
  "lightpink": "FFB6C1",
  "lightsalmon": "FFA07A",
  "lightseagreen": "20B2AA",
  "lightskyblue": "87CEFA",
  "lightslategray": "778899",
  "lightslategrey": "778899",
  "lightsteelblue": "B0C4DE",
  "lightyellow": "FFFFE0",
  "lime": "00FF00",
  "limegreen": "32CD32",
  "linen": "FAF0E6",
  "magenta": "FF00FF",
  "maroon": "800000",
  "mediumaquamarine": "66CDAA",
  "mediumblue": "0000CD",
  "mediumorchid": "BA55D3",
  "mediumpurple": "9370DB",
  "mediumseagreen": "3CB371",
  "mediumslateblue": "7B68EE",
  "mediumspringgreen": "00FA9A",
  "mediumturquoise": "48D1CC",
  "mediumvioletred": "C71585",
  "midnightblue": "191970",
  "mintcream": "F5FFFA",
  "mistyrose": "FFE4E1",
  "moccasin": "FFE4B5",
  "navajowhite": "FFDEAD",
  "navy": "000080",
  "oldlace": "FDF5E6",
  "olive": "808000",
  "olivedrab": "6B8E23",
  "orange": "FFA500",
  "orangered": "FF4500",
  "orchid": "DA70D6",
  "palegoldenrod": "EEE8AA",
  "palegreen": "98FB98",
  "paleturquoise": "AFEEEE",
  "palevioletred": "DB7093",
  "papayawhip": "FFEFD5",
  "peachpuff": "FFDAB9",
  "peru": "CD853F",
  "pink": "FFC0CB",
  "plum": "DDA0DD",
  "powderblue": "B0E0E6",
  "purple": "800080",
  "rebeccapurple": "663399",
  "red": "FF0000",
  "rosybrown": "BC8F8F",
  "royalblue": "4169E1",
  "saddlebrown": "8B4513",
  "salmon": "FA8072",
  "sandybrown": "F4A460",
  "seagreen": "2E8B57",
  "seashell": "FFF5EE",
  "sienna": "A0522D",
  "silver": "C0C0C0",
  "skyblue": "87CEEB",
  "slateblue": "6A5ACD",
  "slategray": "708090",
  "slategrey": "708090",
  "snow": "FFFAFA",
  "springgreen": "00FF7F",
  "steelblue": "4682B4",
  "tan": "D2B48C",
  "teal": "008080",
  "thistle": "D8BFD8",
  "tomato": "FF6347",
  "turquoise": "40E0D0",
  "violet": "EE82EE",
  "wheat": "F5DEB3",
  "white": "FFFFFF",
  "whitesmoke": "F5F5F5",
  "yellow": "FFFF00",
  "yellowgreen": "9ACD32"
};
const NS = {
  p: 'http://schemas.openxmlformats.org/presentationml/2006/main',
  a: 'http://schemas.openxmlformats.org/drawingml/2006/main',
  r: 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
};
const all = (node: Element | Document | null | undefined, tag: string): Element[] => {
  const [prefix, local] = tag.split(':');
  return node ? Array.from(node.getElementsByTagNameNS(NS[prefix as keyof typeof NS], local)) : [];
};
const first = (node: Element | Document | null | undefined, tag: string) => all(node, tag)[0];
const children = (node: Element | null | undefined) => node ? Array.from(node.childNodes).filter((n): n is Element => n.nodeType === 1) : [];
const child = (node: Element | null | undefined, name: string) => children(node).find(n => n.localName === name);
const num = (node: Element | null | undefined, attr: string, fallback = 0) => {
  const value = node?.getAttribute(attr);
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : fallback;
};
const relationId = (node?: Element) => node?.getAttributeNS(NS.r, 'id') || '';
const round = (n: number) => Math.round(n * 100) / 100;

// 在解压前检查中央目录大小，拒绝异常文件和过大的解压内容。
function checkArchive(buffer: ArrayBuffer) {
  const view = new DataView(buffer);
  if (view.byteLength < 22 || view.getUint32(0, true) !== 0x04034b50) throw new Error('文件不是有效的 PPTX，请勿直接修改扩展名');
  let end = view.byteLength - 22;
  const lower = Math.max(0, end - 65535);
  while (end >= lower && view.getUint32(end, true) !== 0x06054b50) end--;
  if (end < lower) throw new Error('PPTX 文件损坏，请重新导出');
  const count = view.getUint16(end + 10, true);
  let cursor = view.getUint32(end + 16, true);
  if (count > 4000 || cursor === 0xffffffff) throw new Error('模板结构过大，请精简后上传');
  let total = 0;
  for (let i = 0; i < count; i++) {
    if (cursor + 46 > end || view.getUint32(cursor, true) !== 0x02014b50) throw new Error('PPTX 压缩结构无效');
    if (view.getUint16(cursor + 8, true) & 1) throw new Error('不支持加密的 PPTX');
    total += view.getUint32(cursor + 24, true);
    if (total > MAX_UNPACKED_BYTES) throw new Error('模板解压后过大，请压缩图片后重新上传');
    cursor += 46 + view.getUint16(cursor + 28, true) + view.getUint16(cursor + 30, true) + view.getUint16(cursor + 32, true);
  }
}

export async function parseEnterpriseTemplate(file: { name: string; size: number; arrayBuffer(): Promise<ArrayBuffer> }): Promise<EnterpriseTemplate> {
  if (!/\.pptx$/i.test(file.name)) throw new Error('企业模板仅支持 .pptx 文件，旧版 .ppt 请先另存为 .pptx');
  if (!file.size || file.size > MAX_TEMPLATE_BYTES) throw new Error('模板文件需大于 0 且不超过 30MB');
  const buffer = await file.arrayBuffer();
  if (buffer.byteLength > MAX_TEMPLATE_BYTES) throw new Error('模板文件不能超过 30MB');
  checkArchive(buffer);
  const zip = await JSZip.loadAsync(buffer);
  if (Object.keys(zip.files).some(path => /vbaProject\.bin$/i.test(path))) throw new Error('不支持包含宏的模板');
  const warnings = new Set<string>();
  const docs = new Map<string, Document>();
  const xml = async (path: string) => {
    if (docs.has(path)) return docs.get(path)!;
    const entry = zip.file(path);
    if (!entry) throw new Error(`模板缺少必要文件：${path}`);
    const source = await entry.async('string');
    if (source.length > 8 * 1024 * 1024 || /<!DOCTYPE|<!ENTITY/i.test(source)) throw new Error('模板 XML 结构不受支持');
    const doc = new DOMParser().parseFromString(source, 'application/xml');
    if (doc.getElementsByTagName('parsererror').length) throw new Error('模板 XML 损坏，请重新导出');
    docs.set(path, doc);
    return doc;
  };
  const relationships = async (path: string) => {
    const slash = path.lastIndexOf('/');
    const relPath = `${path.slice(0, slash + 1)}_rels/${path.slice(slash + 1)}.rels`;
    const result = new Map<string, { target: string; type: string }>();
    if (!zip.file(relPath)) return result;
    const doc = await xml(relPath);
    for (const rel of Array.from(doc.getElementsByTagNameNS('*', 'Relationship'))) {
      if (rel.getAttribute('TargetMode') === 'External') continue;
      const target = rel.getAttribute('Target') || '';
      if (!target || /[\\?#]|^[a-z]+:/i.test(target)) continue;
      const parts: string[] = target.startsWith('/') ? [] : path.slice(0, slash).split('/');
      let valid = true;
      for (const part of target.split('/')) {
        if (part === '..') { if (!parts.length) { valid = false; break; } parts.pop(); }
        else if (part && part !== '.') parts.push(part);
      }
      if (valid) result.set(rel.getAttribute('Id') || '', { target: parts.join('/'), type: rel.getAttribute('Type') || '' });
    }
    return result;
  };
  const presentation = await xml('ppt/presentation.xml');
  const size = first(presentation, 'p:sldSz');
  const originalWidth = num(size, 'cx');
  const originalHeight = num(size, 'cy');
  if (originalWidth <= 0 || originalHeight <= 0) throw new Error('无法识别模板的页面尺寸');
  const ratio = originalWidth / originalHeight;
  const isFourThree = Math.abs(ratio - 4 / 3) < 0.00001;
  const isWide = Math.abs(ratio - 16 / 9) < 0.00001;
  if (!isWide) throw new Error('当前仅支持 16:9 企业模板，请在 PowerPoint 中调整比例后再上传');
  const width = 1200;
  const height = width / ratio;
  if (height < 200 || height > 2400) throw new Error('模板页面比例不受支持');
  const slideIds = all(presentation, 'p:sldId');
  if (!slideIds.length || slideIds.length > MAX_PAGES) throw new Error(`模板需包含 1–${MAX_PAGES} 页，请保留需要复用的版式`);
  const presentationRels = await relationships('ppt/presentation.xml');
  const assets: EnterpriseTemplate['assets'] = [];
  const assetIds = new Map<string, string>();
  const fonts = new Set<string>();
  const colors = new Set<string>();
  const extractImage = async (path?: string) => {
    if (!path) { warnings.add('外链或缺失图片未导入，请改为嵌入图片后重新上传'); return undefined; }
    if (assetIds.has(path)) return assetIds.get(path);
    const ext = path.split('.').pop()?.toLowerCase() || '';
    const mime = ({ png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp' } as Record<string, string>)[ext];
    if (!mime || !zip.file(path)) { warnings.add('部分图片格式不受支持（如 SVG、EMF、WMF），预览中未显示'); return undefined; }
    const id = `asset-${assets.length + 1}`;
    assets.push({ id, mime, data: await zip.file(path)!.async('base64') });
    assetIds.set(path, id);
    return id;
  };
  const pages: EnterpriseTemplatePage[] = [];
  for (let index = 0; index < slideIds.length; index++) {
    const slidePath = presentationRels.get(relationId(slideIds[index]))?.target;
    if (!slidePath) throw new Error('模板页面引用损坏');
    const slide = await xml(slidePath);
    const slideRels = await relationships(slidePath);
    const layoutPath = [...slideRels.values()].find(rel => rel.type.endsWith('/slideLayout'))?.target;
    const layout = layoutPath ? await xml(layoutPath) : undefined;
    const layoutRels = layoutPath ? await relationships(layoutPath) : new Map<string, { target: string; type: string }>();
    const masterPath = [...layoutRels.values()].find(rel => rel.type.endsWith('/slideMaster'))?.target;
    const master = masterPath ? await xml(masterPath) : undefined;
    const masterRels = masterPath ? await relationships(masterPath) : new Map<string, { target: string; type: string }>();
    const themePath = [...masterRels.values()].find(rel => rel.type.endsWith('/theme'))?.target;
    const theme = themePath ? await xml(themePath) : undefined;
    const palette: Record<string, string> = {};
    for (const entry of children(first(theme, 'a:clrScheme'))) {
      const color = children(entry)[0];
      palette[entry.localName] = color?.getAttribute('lastClr') || color?.getAttribute('val') || '000000';
    }
    const colorMap = first(slide, 'a:overrideClrMapping') || first(layout, 'a:overrideClrMapping') || first(master, 'p:clrMap');
    const resolveColor = (node?: Element, fallback = '#222222', referenceColor = fallback): string => {
      if (!node) return fallback;
      const solid = node.localName === 'solidFill' ? node : child(node, 'solidFill');
      const valueNode = solid ? children(solid)[0] : ['srgbClr','schemeClr','sysClr','prstClr'].includes(node.localName) ? node : children(node).find(n => ['srgbClr','schemeClr','sysClr','prstClr'].includes(n.localName));
      if (!valueNode) return fallback;
      let value = valueNode.getAttribute('val') || '';
      if (valueNode.localName === 'schemeClr' && value === 'phClr') value = referenceColor.replace('#','');
      if (valueNode.localName === 'schemeClr' && valueNode.getAttribute('val') !== 'phClr') value = palette[colorMap?.getAttribute(value) || ({ bg1: 'lt1', tx1: 'dk1', bg2: 'lt2', tx2: 'dk2' } as Record<string, string>)[value] || value] || '';
      if (valueNode.localName === 'sysClr') value = valueNode.getAttribute('lastClr') || '';
      if (valueNode.localName === 'prstClr') value = PRESET_COLORS[value.replace(/^dk/, 'dark').replace(/^lt/, 'light').replace(/^med/, 'medium').toLowerCase()] || '';
      if (!/^[a-f\d]{6}$/i.test(value)) { warnings.add(`第 ${index + 1} 页存在未解析的颜色：${valueNode.localName} ${valueNode.getAttribute('val') || ''}`); return fallback; }
      let rgb = [0,2,4].map(i => parseInt(value.slice(i,i+2),16)/255);
      for (const effect of children(valueNode)) {
        const amount = num(effect,'val')/100000;
        if (effect.localName === 'shade') rgb = rgb.map(v=>v*amount);
        else if (effect.localName === 'tint') rgb = rgb.map(v=>v*amount+1-amount);
        else if (['lumMod','lumOff'].includes(effect.localName)) {
          const hi=Math.max(...rgb),lo=Math.min(...rgb),l=(hi+lo)/2;
          const saturation=hi===lo ? 0 : (hi-lo)/(1-Math.abs(2*l-1));
          const next=Math.max(0,Math.min(1,effect.localName==='lumMod' ? l*amount : l+amount));
          const chroma=(1-Math.abs(2*next-1))*saturation;
          rgb=rgb.map(v=>hi===lo ? next : (v-lo)/(hi-lo)*chroma+next-chroma/2);
        }
      }
      return '#' + rgb.map(v=>Math.round(Math.max(0,Math.min(1,v))*255).toString(16).padStart(2,'0')).join('').toUpperCase();
    };
    const parseShadow = (shadow: Element, referenceColor = '#000000'): NonNullable<TemplateElement['shadow']> => {
      const direction=num(shadow,'dir')/60000*Math.PI/180, distance=num(shadow,'dist')/originalWidth*width;
      const align=shadow.getAttribute('algn') || 'b';
      return {color:resolveColor(shadow,'#000000',referenceColor),opacity:num(first(shadow,'a:alpha'),'val',100000)/100000,
        blur:num(shadow,'blurRad')/originalWidth*width,x:Math.cos(direction)*distance,y:Math.sin(direction)*distance,
        scaleX:num(shadow,'sx',100000)/100000,scaleY:num(shadow,'sy',100000)/100000,
        skewX:num(shadow,'kx')/60000,skewY:num(shadow,'ky')/60000,
        alignX:align.includes('l')?0:align.includes('r')?1:.5,alignY:align.includes('t') && align!=='ctr'?0:align.includes('b')?1:.5};
    };
    const themeFont = (value: string, language: string, text: string) => {
      if (!value.startsWith('+')) return value;
      const major = value.startsWith('+mj');
      const scheme = first(theme, major ? 'a:majorFont' : 'a:minorFont');
      const script = /^zh-(TW|HK|MO)/i.test(language) ? 'Hant' : /^ja/i.test(language) ? 'Jpan' : /^ko/i.test(language) ? 'Hang' : /^zh/i.test(language) || /[\u3400-\u9fff]/.test(text) ? 'Hans' : '';
      const slot = value.endsWith('-lt') ? 'latin' : value.endsWith('-cs') ? 'cs' : 'ea';
      return child(scheme, slot)?.getAttribute('typeface') || (slot === 'ea' && children(scheme).find(n => n.localName === 'font' && n.getAttribute('script') === script)?.getAttribute('typeface')) || child(scheme, 'latin')?.getAttribute('typeface') || 'Arial';
    };
    const page: EnterpriseTemplatePage = { name: `第 ${index + 1} 页`, role: index === 0 ? 'cover' : index === slideIds.length - 1 ? 'ending' : 'body', background: '#FFFFFF', elements: [] };
    const layers = [
      { doc: master, rels: masterRels, fixed: true },
      { doc: layout, rels: layoutRels, fixed: true },
      { doc: slide, rels: slideRels, fixed: false },
    ];
    // 背景按 slide → layout → master 继承，不能把多个背景叠加。
    for (const layer of [...layers].reverse()) {
      const bg = first(layer.doc, 'p:bg');
      if (!bg) continue;
      page.background = resolveColor(first(bg, 'p:bgPr'), '#FFFFFF');
      const pattern=first(bg,'a:pattFill');
      if(pattern?.getAttribute('prst')==='diagBrick') page.backgroundPattern={type:'diagBrick',foreground:resolveColor(child(pattern,'fgClr'),'#DDDDDD'),background:resolveColor(child(pattern,'bgClr'),'#FFFFFF')};
      else if(pattern) warnings.add(`第 ${index+1} 页的 ${pattern.getAttribute('prst')} 背景纹理暂未还原`);
      const blip = first(bg, 'a:blip');
      if (blip) {
        const asset = await extractImage(layer.rels.get(blip.getAttributeNS(NS.r, 'embed') || '')?.target);
        if (asset) page.elements.push({ kind: 'image', x: 0, y: 0, width, height, asset, fixed: true });
      }
      if (first(bg, 'p:bgRef') || first(bg, 'a:gradFill')) warnings.add(`第 ${index+1} 页的主题背景引用或背景渐变暂未完整还原，请检查预览`);
      break;
    }
    // 展开组合并把子坐标转换到页面坐标；保留原绘制顺序。
    type Matrix = [number, number, number, number, number, number];
    const multiply = (m: Matrix, n: Matrix): Matrix => [m[0]*n[0]+m[2]*n[1], m[1]*n[0]+m[3]*n[1], m[0]*n[2]+m[2]*n[3], m[1]*n[2]+m[3]*n[3], m[0]*n[4]+m[2]*n[5]+m[4], m[1]*n[4]+m[3]*n[5]+m[5]];
    const translation = (x: number, y: number): Matrix => [1,0,0,1,x,y];
    const rotate = (xfrm: Element | undefined, cx: number, cy: number): Matrix => {
      const angle = num(xfrm, 'rot') / 60000 * Math.PI / 180;
      const flip = (key: string) => ['1','true'].includes(xfrm?.getAttribute(key) || '') ? -1 : 1;
      const c = Math.cos(angle), sn = Math.sin(angle);
      return multiply(multiply(translation(cx,cy), [c*flip('flipH'),sn*flip('flipH'),-sn*flip('flipV'),c*flip('flipV'),0,0]),translation(-cx,-cy));
    };
    const groupShadows = new Map<string, { shadow: NonNullable<TemplateElement['shadow']>; members: TemplateElement[]; name: string }>();
    const flatten = (nodes: Element[], transform: Matrix = [1,0,0,1,0,0], flips = { horizontal: false, vertical: false }, shadowGroups: string[] = [], groupFill?: Element): Element[] => nodes.flatMap(node => {
      const isGroup = node.localName === 'grpSp';
      const xfrm = child(child(node, isGroup ? 'grpSpPr' : 'spPr'), 'xfrm');
      const off = child(xfrm, 'off'), ext = child(xfrm, 'ext');
      const textFlip = { horizontal: flips.horizontal !== ['1','true'].includes(xfrm?.getAttribute('flipH') || ''), vertical: flips.vertical !== ['1','true'].includes(xfrm?.getAttribute('flipV') || '') };
      if (isGroup) {
        const chOff = child(xfrm, 'chOff'), chExt = child(xfrm, 'chExt');
        const sx = num(chExt,'cx') ? num(ext,'cx') / num(chExt,'cx') : 1;
        const sy = num(chExt,'cy') ? num(ext,'cy') / num(chExt,'cy') : 1;
        const map: Matrix = [sx,0,0,sy,num(off,'x')-num(chOff,'x')*sx,num(off,'y')-num(chOff,'y')*sy];
        const local = multiply(rotate(xfrm,num(off,'x')+num(ext,'cx')/2,num(off,'y')+num(ext,'cy')/2),map);
        const outer=child(child(child(node,'grpSpPr'),'effectLst'),'outerShdw');
        let groups=shadowGroups;
        if (outer) {
          const id=`page-${index+1}-group-shadow-${groupShadows.size+1}`;
          const shadow=parseShadow(outer);
          if (shadow.scaleX!==1 || shadow.scaleY!==1 || shadow.skewX || shadow.skewY || num(xfrm,'rot')) warnings.add(`第 ${index+1} 页组合 ${first(node,'p:cNvPr')?.getAttribute('name') || ''} 的缩放/旋转阴影使用近似还原`);
          groupShadows.set(id,{shadow,members:[],name:first(node,'p:cNvPr')?.getAttribute('name') || id});groups=[...groups,id];
        }
        const ownFill=children(child(node,'grpSpPr')).find(n=>['solidFill','gradFill','blipFill','noFill','pattFill','grpFill'].includes(n.localName));
        return flatten(children(node), multiply(transform,local), textFlip, groups, ownFill && ownFill.localName !== 'grpFill' ? ownFill : groupFill);
      }
      if (!off || !ext) return [node];
      const matrix = multiply(multiply(transform,rotate(xfrm,num(off,'x')+num(ext,'cx')/2,num(off,'y')+num(ext,'cy')/2)),translation(num(off,'x'),num(off,'y')));
      const copy = node.cloneNode(true) as Element;
      const copiedProps=child(copy,'spPr')!;
      const inheritedFill=child(copiedProps,'grpFill');
      if (inheritedFill && groupFill) { copiedProps.replaceChild(groupFill.cloneNode(true),inheritedFill);copy.setAttribute('data-group-fill','1'); }
      const cloned = child(copiedProps,'xfrm')!;
      const clonedOff = child(cloned,'off')!;
      clonedOff.setAttribute('x',String(matrix[4])); clonedOff.setAttribute('y',String(matrix[5]));
      cloned.setAttribute('rot','0'); cloned.removeAttribute('flipH'); cloned.removeAttribute('flipV');
      copy.setAttribute('data-shadow-groups',JSON.stringify(shadowGroups));
      copy.setAttribute('data-matrix',JSON.stringify(matrix.slice(0,4)));
      copy.setAttribute('data-text-flip',JSON.stringify(textFlip));
      return [copy];
    });
    const connectors: TemplateElement[] = [];
    for (const layer of layers) {
      if (layer.fixed && slide.documentElement.getAttribute('showMasterSp') === '0') continue;
      const tree = first(layer.doc, 'p:spTree');
      for (const node of flatten(children(tree))) {
        const kind = node.localName;
        if (['nvGrpSpPr', 'grpSpPr', 'extLst'].includes(kind)) continue;
        if (!['sp', 'pic', 'cxnSp'].includes(kind)) { warnings.add('图表、表格和嵌入对象暂不自动还原，请在生成前检查'); continue; }
        const ph = first(node, 'p:ph');
        if (layer.fixed && ph) continue;
        const placeholder = ph?.getAttribute('type') || (ph ? 'body' : undefined);
        const samePlaceholder = (doc?: Document) => all(doc, 'p:sp').find(sp => {
          const candidate = first(sp, 'p:ph');
          return candidate && (candidate.getAttribute('idx') || '0') === (ph?.getAttribute('idx') || '0') &&
            (candidate.getAttribute('type') || 'body') === (placeholder || 'body');
        });
        const inherited = ph ? [samePlaceholder(layout), samePlaceholder(master)].filter((v): v is Element => !!v) : [];
        const xfrm = first(node, 'a:xfrm') || inherited.map(sp => first(sp, 'a:xfrm')).find(Boolean);
        const off = first(xfrm, 'a:off');
        const ext = first(xfrm, 'a:ext');
        if (!off || !ext) { warnings.add('部分占位符缺少位置，未纳入可复用布局'); continue; }
        const element: TemplateElement = {
          kind: kind === 'pic' ? 'image' : 'shape',
          x: round(num(off, 'x') / originalWidth * width), y: round(num(off, 'y') / originalHeight * height),
          width: round(num(ext, 'cx') / originalWidth * width), height: round(num(ext, 'cy') / originalHeight * height),
          rotation: num(xfrm, 'rot') / 60000,
          matrix: node.hasAttribute('data-matrix') ? JSON.parse(node.getAttribute('data-matrix')!) : undefined,
          textFlip: node.hasAttribute('data-text-flip') ? JSON.parse(node.getAttribute('data-text-flip')!) : undefined,
          placeholder, fixed: layer.fixed,
        };
        // Fold group scale into physical dimensions before rounding tiny child coordinates.
        // Keep only rotation/reflection in the matrix, including empty decorative text and lines.
        if (element.matrix) {
          const [a,b,c,d]=element.matrix,sx=Math.hypot(a,b),sy=Math.hypot(c,d);
          if (sx && sy) {
            element.width=round(num(ext,'cx')/originalWidth*width*sx);
            element.height=round(num(ext,'cy')/originalHeight*height*sy);
            element.matrix=[a/sx,b/sx,c/sy,d/sy];element.textTransformVersion=1;
          }
        }
        if (kind === 'cxnSp') {
          const props=child(node,'spPr'), ownLine=child(props,'ln'), ref=first(node,'a:lnRef');
          const themeLine=children(first(theme,'a:lnStyleLst'))[num(ref,'idx')-1];
          const line=ownLine || themeLine;
          const w=element.width,h=element.height;
          // Subpixel SVG viewports can round to zero in Safari; keep path coordinates unchanged.
          element.width=Math.max(1,w);element.height=Math.max(1,h);
          element.stroke=child(line,'noFill') ? 'transparent' : resolveColor(line,resolveColor(ref,'#000000'),resolveColor(ref,'#000000'));
          element.strokeWidth=num(line,'w',num(themeLine,'w',12700))/originalWidth*width;
          element.strokeOpacity=num(first(line,'a:alpha'),'val',100000)/100000;
          element.vector={width:element.width,height:element.height,paths:[`M 0 0 L ${w} ${h}`]};
          const length=Math.hypot(w,h);
          if (length) for (const end of ['headEnd','tailEnd']) {
            const type=child(line,end)?.getAttribute('type');if (!type || type==='none') continue;
            const direction=end==='tailEnd' ? 1 : -1, x=end==='tailEnd' ? w : 0,y=end==='tailEnd' ? h : 0;
            const size=Math.max(3,element.strokeWidth*3),dx=w/length*size*direction,dy=h/length*size*direction;
            element.vector.paths.push(`M ${x-dx-dy/2} ${y-dy+dx/2} L ${x} ${y} L ${x-dx+dy/2} ${y-dy-dx/2}`);
          }
          element.fill='transparent';element.fixed=true;
          element.id=`page-${index+1}-connector-${connectors.length+1}`;
          connectors.push(element);continue;
        }
        const isShapeLine=first(child(node,'spPr'),'a:prstGeom')?.getAttribute('prst')==='line';
        if (isShapeLine && (element.width>0 || element.height>0)) {
          if (!element.width || !element.height) element.id=`page-${index+1}-restored-line-${first(node,'p:cNvPr')?.getAttribute('id')}`;
          element.width=Math.max(.01,element.width);element.height=Math.max(.01,element.height);
        } else if (element.width <= 0 || element.height <= 0) continue;
        if (xfrm?.getAttribute('flipH') === '1' || xfrm?.getAttribute('flipV') === '1') warnings.add('图片或图形翻转效果未还原');
        let shapeImage: TemplateElement | undefined;
        if (kind === 'pic') {
          const blip = first(node, 'a:blip');
          element.asset = await extractImage(layer.rels.get(blip?.getAttributeNS(NS.r, 'embed') || '')?.target);
          if (!element.asset) continue;
          const change=child(blip,'clrChange');
          if (change) element.imageColorChange={from:resolveColor(child(change,'clrFrom'),'#FFFFFF'),to:resolveColor(child(change,'clrTo'),'#FFFFFF'),opacity:num(first(child(change,'clrTo'),'a:alpha'),'val',100000)/100000};
          const crop = first(node, 'a:srcRect');
          if (crop) element.crop = { left: num(crop, 'l') / 100000, top: num(crop, 't') / 100000, right: num(crop, 'r') / 100000, bottom: num(crop, 'b') / 100000 };
          element.fixed = true;
        } else {
          const props = child(node, 'spPr');
          const imageFill = child(props, 'blipFill');
          if (imageFill) {
            const blip = first(imageFill, 'a:blip');
            const asset = await extractImage(layer.rels.get(blip?.getAttributeNS(NS.r, 'embed') || '')?.target);
            if (asset) {
              const crop = child(imageFill, 'srcRect');
              shapeImage = { ...element, kind: 'image', asset, fixed: true,
                crop: crop ? { left: num(crop, 'l') / 100000, top: num(crop, 't') / 100000, right: num(crop, 'r') / 100000, bottom: num(crop, 'b') / 100000 } : undefined };
            }
          }
          const geometry = first(props, 'a:prstGeom')?.getAttribute('prst');
          element.geometry = geometry === 'ellipse' || geometry === 'roundRect' ? geometry : 'rect';
          if (geometry === 'roundRect') {
            const adj = all(first(props,'a:prstGeom'),'a:gd').find(n=>n.getAttribute('name')==='adj')?.getAttribute('fmla')?.match(/^val\s+(-?[\d.]+)$/);
            element.cornerRadius = Math.min(element.width,element.height)*Math.max(0,Math.min(.5,Number(adj?.[1] || 16667)/100000));
          }

          element.fill = resolveColor(props, 'transparent');
          if (node.getAttribute('data-group-fill')==='1') element.fillSource='group';
          const alpha = first(child(props, 'solidFill'), 'a:alpha');
          if (alpha) element.fillOpacity = Math.max(0, Math.min(1, num(alpha, 'val') / 100000));
          const paths = children(child(child(props, 'custGeom'), 'pathLst'));
          if (paths.length === 1 && children(paths[0]).filter(n=>n.localName==='moveTo').length===1 && num(paths[0], 'w') > 0 && num(paths[0], 'h') > 0 && children(paths[0]).every(n => ['moveTo','lnTo','close'].includes(n.localName))) {
            const points = children(paths[0]).filter(n => n.localName !== 'close').map(n => child(n,'pt'));
            if (points.length >= 3 && points.every(pt => pt && ['x','y'].every(key => /^-?\d+(?:\.\d+)?$/.test(pt.getAttribute(key) || '')))) {
              element.clipPolygon = points.map(pt => [num(pt,'x') / num(paths[0],'w') * 100, num(pt,'y') / num(paths[0],'h') * 100]);
              if (shapeImage) shapeImage.clipPolygon = element.clipPolygon;
            }
          }
          const fillRect = first(imageFill, 'a:fillRect');
          if (shapeImage && imageFill?.getAttribute('rotWithShape') === '0') shapeImage.imageFlip = element.textFlip;
          if (shapeImage && fillRect) shapeImage.imageStretch = { left: num(fillRect,'l')/100000, right: num(fillRect,'r')/100000, top: num(fillRect,'t')/100000, bottom: num(fillRect,'b')/100000 };
          const ownOutline = child(props, 'ln'), outlineRef = child(child(node, 'style'), 'lnRef');
          const themeOutline = children(first(theme, 'a:lnStyleLst'))[num(outlineRef, 'idx') - 1];
          const outlineFill = children(ownOutline).find(n => ['solidFill','noFill','gradFill'].includes(n.localName));
          const outline = outlineFill ? ownOutline : themeOutline;
          const outlineColor = resolveColor(outlineRef, 'transparent');
          element.stroke = child(outline, 'noFill') ? 'transparent' : resolveColor(outline, outlineColor, outlineColor);
          element.strokeWidth = num(ownOutline, 'w', num(themeOutline, 'w', 12700))/originalWidth*width;
          element.strokeOpacity = num(first(child(outline, 'solidFill'),'a:alpha'),'val',100000)/100000;
          const ownEffects=child(props,'effectLst'), effectRef=child(child(node,'style'),'effectRef');
          const effects=ownEffects || child(children(first(theme,'a:effectStyleLst'))[num(effectRef,'idx')-1],'effectLst');
          const outerShadow=child(effects,'outerShdw');
          if (outerShadow) element.shadow=parseShadow(outerShadow,resolveColor(effectRef,'#000000'));
          const glow=child(effects,'glow');
          if(glow) element.glow={color:resolveColor(glow,'#FFFFFF'),opacity:num(first(glow,'a:alpha'),'val',100000)/100000,radius:num(glow,'rad')/originalWidth*width};
          const softEdge = first(child(props,'effectLst'),'a:softEdge');
          if (softEdge) element.blur = num(softEdge,'rad')/originalWidth*width;
          if (paths[0]?.getAttribute('fill') === 'none') element.fill = 'transparent';
          // Resolve a native geometry into numeric SVG paths; no model supplies SVG markup.
          let vectorWidth = element.width, vectorHeight = element.height;
          let vectorPaths: string[] = [];
          if (geometry === 'line') {
            vectorPaths=[`M 0 0 L ${num(ext,'cx') ? vectorWidth : 0} ${num(ext,'cy') ? vectorHeight : 0}`];element.fill='transparent';
          } else if (geometry === 'ellipse') {
            const w=vectorWidth,h=vectorHeight,k=.5522847498307936;
            vectorPaths=[`M ${w} ${h/2} C ${w} ${h*(1+k)/2} ${w*(1+k)/2} ${h} ${w/2} ${h} C ${w*(1-k)/2} ${h} 0 ${h*(1+k)/2} 0 ${h/2} C 0 ${h*(1-k)/2} ${w*(1-k)/2} 0 ${w/2} 0 C ${w*(1+k)/2} 0 ${w} ${h*(1-k)/2} ${w} ${h/2} Z`];
          } else if (geometry === 'roundRect') {
            const w=vectorWidth,h=vectorHeight,r=element.cornerRadius || 0,k=.5522847498307936;
            vectorPaths=[`M ${r} 0 L ${w-r} 0 C ${w-r+k*r} 0 ${w} ${r-k*r} ${w} ${r} L ${w} ${h-r} C ${w} ${h-r+k*r} ${w-r+k*r} ${h} ${w-r} ${h} L ${r} ${h} C ${r-k*r} ${h} 0 ${h-r+k*r} 0 ${h-r} L 0 ${r} C 0 ${r-k*r} ${r-k*r} 0 ${r} 0 Z`];
          } else if (geometry === 'arc') {
            const adjustments=all(first(props,'a:prstGeom'),'a:gd');
            const angle=(name:string,fallback:number)=>Number(adjustments.find(n=>n.getAttribute('name')===name)?.getAttribute('fmla')?.split(/\s+/)[1] ?? fallback)/60000*Math.PI/180;
            const start=angle('adj1',16200000),end=angle('adj2',0); let sweep=end-start; if(sweep<=0)sweep+=2*Math.PI;
            const points=Array.from({length:65},(_,i)=>{const t=start+sweep*i/64;return `${vectorWidth/2*(1+Math.cos(t))} ${vectorHeight/2*(1+Math.sin(t))}`;});
            vectorPaths=['M '+points.join(' L ')]; element.fill='transparent';
          } else if (geometry === 'parallelogram') {
            const adjustment = all(first(props,'a:prstGeom'),'a:gd').find(n => n.getAttribute('name') === 'adj')?.getAttribute('fmla')?.match(/^val\s+(-?[\d.]+)$/);
            const skew = Math.max(0,Math.min(vectorWidth, Math.min(vectorWidth,vectorHeight) * Number(adjustment?.[1] || 25000) / 100000));
            vectorPaths = [`M ${skew} 0 L ${vectorWidth} 0 L ${vectorWidth-skew} ${vectorHeight} L 0 ${vectorHeight} Z`];
          } else if (paths.length === 1 && num(paths[0],'w') > 0 && num(paths[0],'h') > 0) {
            vectorWidth = num(paths[0],'w'); vectorHeight = num(paths[0],'h');
            const commands: string[] = []; let supported = true;
            for (const command of children(paths[0])) {
              const op = ({moveTo:'M',lnTo:'L',cubicBezTo:'C',quadBezTo:'Q',close:'Z'} as Record<string,string>)[command.localName];
              const points = children(command);
              if (!op || points.some(pt => !['x','y'].every(k => /^-?\d+(?:\.\d+)?$/.test(pt.getAttribute(k) || '')))) { supported = false; break; }
              commands.push(op + ' ' + points.map(pt => `${num(pt,'x')} ${num(pt,'y')}`).join(' '));
            }
            if (supported) vectorPaths = [commands.join(' ')];
          }
          // Keep large EMU paths in a compact SVG coordinate space for browser precision.
          if (vectorPaths.length && Math.max(vectorWidth,vectorHeight)>100000) {
            vectorPaths=vectorPaths.map(path=>{let coordinate=0;return path.replace(/-?\d+(?:\.\d+)?/g,value=>String(Math.round(Number(value)*1000000/(coordinate++%2 ? vectorHeight : vectorWidth))/1000));});
            vectorWidth=1000;vectorHeight=1000;
          }
          const fillRef = first(node,'a:fillRef');
          const referenceColor = resolveColor(fillRef,'transparent');
          const themeFill = children(first(theme,'a:fillStyleLst'))[num(fillRef,'idx') - 1];
          const ownFill = children(props).find(n => ['solidFill','gradFill','noFill','blipFill'].includes(n.localName));
          const actualFill = ownFill || themeFill;
          if (!ownFill && actualFill?.localName === 'solidFill') element.fill = resolveColor(actualFill, referenceColor, referenceColor);
          if (!vectorPaths.length && (actualFill?.localName === 'gradFill' || child(outline,'gradFill')) && (!geometry || geometry === 'rect')) vectorPaths = [`M 0 0 L ${vectorWidth} 0 L ${vectorWidth} ${vectorHeight} L 0 ${vectorHeight} Z`];
          if (!vectorPaths.length && element.shadow && geometry === 'rect') vectorPaths=[`M 0 0 L ${vectorWidth} 0 L ${vectorWidth} ${vectorHeight} L 0 ${vectorHeight} Z`];
          if (vectorPaths.length && shapeImage) shapeImage.imageClip = {width:vectorWidth,height:vectorHeight,paths:vectorPaths};
          if (vectorPaths.length && !shapeImage) {
            element.vector = {width:vectorWidth,height:vectorHeight,paths:vectorPaths};
            const outlineGradient=child(outline,'gradFill');
            if (outlineGradient && child(outlineGradient,'lin')) element.vector.strokeGradient={angle:num(child(outlineGradient,'lin'),'ang')/60000,stops:children(child(outlineGradient,'gsLst')).map(gs=>({offset:num(gs,'pos')/100000,color:resolveColor(gs,'#000000',outlineColor),opacity:num(first(gs,'a:alpha'),'val',100000)/100000}))};
            if (actualFill?.localName === 'gradFill' && child(actualFill,'lin')) {
              element.vector.gradient = {angle:num(child(actualFill,'lin'),'ang') / 60000, stops:children(child(actualFill,'gsLst')).map(gs => ({
                offset:num(gs,'pos') / 100000, color:resolveColor(gs,'#000000',referenceColor), opacity:first(gs,'a:alpha') ? num(first(gs,'a:alpha'),'val') / 100000 : 1,
              }))};
            }
          }

          const elementName=first(node,'p:cNvPr')?.getAttribute('name') || '未命名元素';
          if (actualFill?.localName==='gradFill' && (!child(actualFill,'lin') || !element.vector?.gradient)) warnings.add(`第 ${index+1} 页 ${elementName} 的填充渐变暂未完整还原`);
          if (child(outline,'gradFill') && !element.vector?.strokeGradient) warnings.add(`第 ${index+1} 页 ${elementName} 的边框渐变暂未完整还原`);
          if (geometry && !['rect','ellipse','roundRect'].includes(geometry) && !vectorPaths.length) warnings.add(`第 ${index+1} 页 ${elementName} 的 ${geometry} 轮廓暂按矩形还原`);
          if (paths.length && !vectorPaths.length) warnings.add(`第 ${index+1} 页 ${elementName} 的自定义轮廓暂未完整还原`);
          for (const effect of children(effects)) if (!['outerShdw','softEdge','glow'].includes(effect.localName)) warnings.add(`第 ${index+1} 页 ${elementName} 的 ${effect.localName} 效果暂未还原`);
          if (outerShadow && !element.vector) warnings.add(`第 ${index+1} 页 ${elementName} 的形状阴影暂未完整还原`);
          const paragraphs = all(child(node, 'txBody'), 'a:p');
          const text = paragraphs.map(p => all(p, 'a:t').map(t => t.textContent).join('')).join('\n');
          if (/www\.1ppt\.com/i.test(text)) warnings.add('检测到来源网站文字，请在编辑器中核对是否保留');
          if (child(node, 'txBody')) {
            element.kind = 'text';
            const bodyStyles = [child(child(node,'txBody'),'bodyPr'), ...inherited.map(sp => child(child(sp,'txBody'),'bodyPr'))].filter((v): v is Element => !!v);
            const bodyAttr = (key: string) => bodyStyles.map(s => s.getAttribute(key)).find(v => v !== null && v !== '');
            const autofit=bodyStyles.map(s=>children(s).find(n=>['normAutofit','noAutofit','spAutoFit'].includes(n.localName))).find(Boolean);
            const fontScale=autofit?.localName==='normAutofit' ? Math.max(.01,Math.min(1,num(autofit,'fontScale',100000)/100000)) : 1;
            const inset = (key: string, fallback: number) => Number(bodyAttr(key) ?? fallback) / originalWidth * width;
            element.textLayout = { fontScale, wrap: bodyAttr('wrap') !== 'none', vertical: bodyAttr('anchor') === 'ctr' ? 'center' : bodyAttr('anchor') === 'b' ? 'bottom' : 'top',
              padding: [inset('tIns',45720),inset('rIns',91440),inset('bIns',45720),inset('lIns',91440)], overflow: bodyAttr('vertOverflow') !== 'clip' };

            element.paragraphs = paragraphs.map(p => {
              const pPr = child(p, 'pPr');
              const align = [pPr, child(first(node,'a:lstStyle'), `lvl${num(pPr,'lvl')+1}pPr`), ...inherited.map(sp => first(sp,'a:pPr')), ...inherited.map(sp => child(first(sp,'a:lstStyle'), `lvl${num(pPr,'lvl')+1}pPr`))].map(p=>p?.getAttribute('algn')).find(Boolean);
              const level = num(pPr, 'lvl');
              const textStyle = first(master, placeholder === 'title' || placeholder === 'ctrTitle' ? 'p:titleStyle' : placeholder ? 'p:bodyStyle' : 'p:otherStyle');
              const defaults = [child(pPr, 'defRPr'), first(node, 'a:lstStyle') && child(child(first(node, 'a:lstStyle'), `lvl${level + 1}pPr`), 'defRPr'),
                ...inherited.map(sp => first(sp, 'a:defRPr')), child(child(textStyle, `lvl${level + 1}pPr`), 'defRPr')].filter((v): v is Element => !!v);
              const runs: TemplateTextRun[] = children(p).flatMap<TemplateTextRun>(run => {
                if (run.localName === 'br') return [{ text: '\n', size: 24, font: 'system-ui', color: '#222222', bold: false }];
                if (!['r', 'fld'].includes(run.localName)) return [];
                const styles = [child(run, 'rPr'), ...defaults].filter((v): v is Element => !!v);
                const value = (attr: string) => styles.map(s => s.getAttribute(attr)).find(v => v !== null && v !== '');
                const text = first(run, 'a:t')?.textContent || '';
                const eastAsian = /[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff]/.test(text);
                const font = themeFont(styles.map(s => child(s, eastAsian ? 'ea' : 'latin')?.getAttribute('typeface') || child(s, eastAsian ? 'latin' : 'ea')?.getAttribute('typeface')).find(Boolean) || 'Arial', value('lang') || '', text);
                const color = resolveColor(styles.find(s => child(s, 'solidFill')), '#222222');
                const runEffects=styles.map(s=>child(s,'effectLst')).find(Boolean);
                const runShadow=child(runEffects,'outerShdw');
                const shadow=runShadow ? parseShadow(runShadow) : undefined;
                if (shadow && (shadow.scaleX!==1 || shadow.scaleY!==1 || shadow.skewX || shadow.skewY)) warnings.add(`第 ${index+1} 页文字 ${text.slice(0,20)} 的变形阴影使用近似还原`);
                fonts.add(font); colors.add(color);
                return [{ shadow, text: first(run, 'a:t')?.textContent || '', size: round(Number(value('sz') || 1800) / 100 * 12700 / originalWidth * width * num(node, 'data-group-scale', 1) * fontScale), font: font || 'system-ui', sourceFont: font, color, opacity: num(first(child(styles.find(s=>child(s,'solidFill')), 'solidFill'), 'a:alpha'),'val',100000)/100000, bold: value('b') === '1' }];
              });
              if (!runs.length) runs.push({ text: '', size: 24, font: 'system-ui', color: '#222222', bold: false });
              return { lineHeight: first(child(pPr,'lnSpc'),'a:spcPct') ? num(first(child(pPr,'lnSpc'),'a:spcPct'),'val')/100000 : undefined, align: align === 'ctr' ? 'center' as const : align === 'r' ? 'right' as const : 'left' as const, runs };
            });
          } else element.fixed = true;
        }
        for (const id of JSON.parse(node.getAttribute('data-shadow-groups') || '[]')) groupShadows.get(id)?.members.push(shapeImage || element);
        if (shapeImage) page.elements.push(shapeImage);
        if (!shapeImage || element.kind === 'text') page.elements.push(element);
      }
    }
    // Assign stable source IDs before inserting generated group-shadow layers.
    let sourceElementIndex=0;
    page.elements.forEach(e=>{if(!e.id) e.id=`page-${index+1}-element-${++sourceElementIndex}`;});
    for (const [id,group] of [...groupShadows].reverse()) {
      const paths:string[]=[];
      for (const e of group.members) {
        if (!e.vector || (e.fill==='transparent' && !e.vector.gradient)) continue;
        const [a,b,c,d]=e.matrix || [1,0,0,1];
        for (const path of e.vector.paths) paths.push(path.replace(/(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)/g,(_,x,y)=>{
          const px=Number(x)*e.width/e.vector!.width,py=Number(y)*e.height/e.vector!.height;
          return `${round(e.x+a*px+c*py)} ${round(e.y+b*px+d*py)}`;
        }));
      }
      if (paths.length) {
        const position=page.elements.findIndex(e=>group.members.includes(e));
        page.elements.splice(Math.max(0,position),0,{id,kind:'shape',fixed:true,x:0,y:0,width,height,fill:'#000000',fillOpacity:0,stroke:'transparent',shadow:group.shadow,vector:{width,height,paths}});
      }
      if (group.members.some(e=>!e.vector)) warnings.add(`第 ${index+1} 页组合 ${group.name} 中的图片或文字整体阴影暂未完整还原`);
    }
    for (const e of page.elements) for (const gradient of [e.vector?.gradient,e.vector?.strokeGradient]) gradient?.stops.sort((a,b)=>a.offset-b.offset);
    page.elements.push(...connectors);
    const pageText = page.elements.flatMap(e => e.paragraphs?.flatMap(p => p.runs.map(r => r.text)) || []).join('');
    if (/目录|contents|agenda/i.test(pageText)) page.role = 'contents';
    if (index === slideIds.length - 1 && /谢谢|感谢|thank/i.test(pageText)) page.role = 'ending';
    if (page.role === 'body' && !page.elements.some(e => e.kind === 'text' && !e.fixed && e.placeholder !== 'sldNum' &&
      !/^(页码|\d+)$/.test(e.paragraphs?.flatMap(p => p.runs.map(r => r.text)).join('').trim() || '页码'))) {
      warnings.add(`第 ${index + 1} 页只有正文底板，没有正文占位符；生成时会在背景留白处安排内容`);
    }
    pages.push(page);
    // 多页解析时让浏览器有机会更新进度与响应操作。
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', buffer));
  const template = labelTemplate({
    version: 1, id: Array.from(bytes, b => b.toString(16).padStart(2, '0')).join(''), name: file.name.replace(/\.pptx$/i, ''), createdAt: Date.now(),
    width, height, originalWidth, originalHeight, aspectRatio: isFourThree ? '4:3' : isWide ? '16:9' : `${round(ratio)}:1`,
    pages, assets, fonts: [...fonts], colors: [...colors], warnings: [...warnings],
  }, true);
  await prepareImageEffects(template);
  return template;
}
