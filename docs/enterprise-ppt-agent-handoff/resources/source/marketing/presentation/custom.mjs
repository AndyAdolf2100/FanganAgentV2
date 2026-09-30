const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function customHTML(page,index,total,theme){
 let markup=page.custom.body;
 const evidence=page.evidence_note+(/<img[\s>]/i.test(markup)?' · AI概念图':'');
 for(const [key,url] of Object.entries(page.asset_data||{}))markup=markup.replaceAll(`asset:${key}`,url);
 const mix=(a,b,t)=>[0,2,4].map(i=>Math.round(parseInt(a.slice(i,i+2),16)*(1-t)+parseInt(b.slice(i,i+2),16)*t).toString(16).padStart(2,'0')).join('');
 const vars=`--bg:#${theme.background};--ink:#${theme.text};--accent:#${theme.accent};--muted:#${theme.muted};--tint:#${mix(theme.background,theme.text,.09)};--paper:#${mix(theme.background,'FFFFFF',.55)};--rule:#${mix(theme.background,theme.text,.28)}`;
 const base=`*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;background:var(--bg);color:var(--ink);font-family:"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif}h1,h2,h3,p{margin:0} .custom-footer{position:absolute;top:660px;left:64px;right:64px;height:45px;border-top:1px solid #bdc5b6;display:flex;justify-content:space-between;padding-top:12px;font-size:16px;line-height:22px;background:var(--bg);color:var(--muted)}[data-text]{white-space:pre-line}`;
 const profile=theme.style_id==='brand_launch'?'.semantic .headline{font-size:44px}.semantic .subhead,.semantic .detail{font-size:22px;line-height:1.55}.semantic .label{font-size:26px}.semantic .entries{top:266px}.semantic.hero .headline{font-size:64px}.semantic.hero .entries{top:400px}.bands .label{flex-basis:230px}.house .entries{border-top-width:12px}.custom-footer{font-size:14px}':'';
 return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';img-src data:;style-src 'unsafe-inline';font-src 'none'"><title>${esc(page.title)}</title><style>${base}\n${page.custom.css}\n${profile}</style></head><body style="${vars}">${markup}<footer class="custom-footer"><span data-text data-role="caption">${esc(evidence)}</span><span data-text data-role="caption">${index+1} / ${total}</span></footer></body></html>`;
}
