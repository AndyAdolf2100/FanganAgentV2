import fs from 'node:fs/promises';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const css=await fs.readFile(new URL('./design.css',import.meta.url),'utf8');
const text=(tag,cls,value)=>value?`<${tag} class="${cls}" data-text>${esc(value)}</${tag}>`:'';
export function designHTML(p,index,total,theme){
 if(theme.style_id==='brand_launch'&&['cover','section'].includes(p.layout))theme={...theme,background:'0D0D0D',text:'FFFFFF',muted:'C7C7C7'};
 if(theme.style_id==='brand_launch'&&p.layout==='closing')theme={...theme,background:'FF6900',text:'151515',muted:'252525'};
 if(p.emphasis==='brand')theme={...theme,background:theme.text,text:theme.background,muted:'D5DECD'};
 const items=p.items||[],kind=p.layout,withImage=!!p.image;
 const cards=items.map((a,i)=>`<article class="item">${text('div','value',a.value)}${text('h2','label',a.label)}${text('p','detail',a.text)}</article>`).join('');
 let content;
 if(kind==='bars'){
  const max=p.chart_max||Math.max(...items.map(i=>i.amount||0),1);
  content=items.map(a=>`<article class="bar-row"><div class="bar-meta">${text('h2','label',a.label)}${text('div','value',a.value)}</div><div class="track"><div class="fill" style="width:${Math.max(0,Math.min(100,a.amount/max*100))}%"></div></div>${text('p','detail',a.text)}</article>`).join('');
 }else content=cards;
 const mix=(a,b,t)=>[0,2,4].map(i=>Math.round(parseInt(a.slice(i,i+2),16)*(1-t)+parseInt(b.slice(i,i+2),16)*t).toString(16).padStart(2,'0')).join('');
 const style=`--bg:#${theme.background};--ink:#${theme.text};--accent:#${theme.accent};--muted:#${theme.muted};--tint:#${mix(theme.background,theme.text,.07)};--rule:#${mix(theme.background,theme.text,.3)};--bg-rgb:${[0,2,4].map(i=>parseInt(theme.background.slice(i,i+2),16)).join(',')}`;
 const visual=withImage?`<img class="hero" src="${esc(p.image)}" alt="概念场景">`:'';
 return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';img-src data:;style-src 'unsafe-inline';font-src 'none'"><title>${esc(p.title)}</title><style>${css}</style></head><body style="${style}"><main class="deck ${kind} ${p.emphasis==='brand'?'brand':''} ${withImage?'with-image':''} ${theme.style_id && theme.style_id!=='auto'?'preset preset-'+esc(theme.style_id):''} count-${items.length}">${visual}<div class="image-wash"></div><header>${text('div','eyebrow',p.section||'营销提案')}${text('h1','headline',p.title)}${text('p','subhead',p.subtitle)}</header><section class="design-content">${content}</section><footer>${text('span','evidence',p.evidence_note+(withImage?' · AI概念图':''))}${text('span','folio',String(index+1).padStart(2,'0')+' / '+total)}</footer></main></body></html>`;
}

export function verifyNotes(source,pages){
 const assigned=pages.flatMap(p=>p.blocks||[]);
 for(const b of source)if(!assigned.some(x=>x.id===b.id&&x.source===b.source))throw new Error('原稿备注遗漏或变化：'+b.id);
 if(assigned.some(x=>!source.some(b=>b.id===x.id&&b.source===x.source)))throw new Error('备注出现未知原稿块');
 return true;
}

export function repairDesign(page){
 const items=page.items||[];
 if(items.length>=2){
  const mid=Math.ceil(items.length/2);
  return [{...page,items:items.slice(0,mid)},{...page,items:items.slice(mid),continuation:true}];
 }
 // Keep all words and source notes; give a long lone item a full-width statement layout.
 if(page.layout!=='statement')return [{...page,layout:'statement',asset:undefined,image:undefined}];
 throw new Error('单项仍超出容量，需要修改策划文案：'+page.title);
}
