import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { customHTML } from './custom.mjs';
import { chartHTML, nativeChartSpec } from './charts.mjs';
import { auditConsistency } from './consistency.mjs';
import { designHTML, verifyNotes, repairDesign } from './design.mjs';
const pptxgen = createRequire(import.meta.url)('pptxgenjs');

const esc = value => String(value).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const W=1280, H=720;
export const themeCSS = await fs.readFile(new URL('./theme.css', import.meta.url), 'utf8');

function tableHTML(block) {
  const all=[block.header,...block.rows];
  // 内容权重分列，避免均分宽度将说明栏挤成十几行。
  const weights=block.header.map((_,i)=>Math.max(3,Math.min(22,Math.max(...all.map(r=>[...String(r[i]||'')].length))**0.62)));
  const sum=weights.reduce((a,b)=>a+b,0);
  return `<table data-id="${esc(block.id)}"><colgroup>${weights.map(w=>`<col style="width:${w/sum*100}%">`).join('')}</colgroup><thead><tr>${block.header.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${block.rows.map(r=>`<tr>${r.map(c=>`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

export function pageHTML(page, index=0, total=0, theme=null) {
  if(page.layout==='chart') return chartHTML(page,index,total,theme);
  if(page.custom) return customHTML(page,index,total,theme);
  if(theme) return designHTML(page,index,total,theme);
  let body;
  if(page.layout==='cover'||page.layout==='image'){
    body=`<main class="slide ${page.layout==='cover'?'cover':'image-page'}"><img src="${esc(page.image)}" alt="${esc(page.caption)}"><h1 data-text>${esc(page.title)}</h1><div class="subtitle" data-text>${esc(page.subtitle)}</div><div class="caption" data-text>${esc(page.caption)}</div></main>`;
  }else{
    body=`<main class="slide"><div class="section" data-text>${esc(page.section||'营销方案')}</div><h1 data-text>${esc(page.title+(page.continuation?' · 续':''))}</h1><div class="content">${page.blocks.map(b=>b.kind==='table'?tableHTML(b):`<p class="block" data-text data-id="${esc(b.id)}">${esc(b.text)}</p>`).join('')}</div><div class="page-no" data-text>${String(index+1).padStart(2,'0')} / ${total}</div></main>`;
  }
  const t=page.detail_theme;
  const selected=t?`:root{--c-bg:#${t.background};--c-text:#${t.text};--c-accent:#${t.accent};--c-muted:#${t.muted}}th{background:#${t.accent}}tr:nth-child(even) td{background:#${t.background}}td,th{border-color:#${t.muted}}`:'';
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src 'none'"><title>${esc(page.title)}</title><style>${themeCSS}\n${selected}</style></head><body>${body}</body></html>`;
}

// 浏览器事实采集：采集真实行框，而非仅比较文本元素外框。
export async function renderProbe(browserPage) {
  return browserPage.evaluate(()=>{
    const rect=r=>({x:r.x,y:r.y,w:r.width,h:r.height});
    const rgb=s=>(s.match(/[\d.]+/g)||[]).slice(0,3).map(Number);
    const lum=c=>c.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
    const issues=[],texts=[],tables=[],images=[];
    function actualLines(el){
      const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT), nodes=[];
      while(walker.nextNode())nodes.push(walker.currentNode);
      const lines=[];
      for(const node of nodes){
        for(let i=0;i<node.length;){
          const len=node.textContent.codePointAt(i)>0xFFFF?2:1;
          const r=document.createRange();r.setStart(node,i);r.setEnd(node,i+len);const box=r.getBoundingClientRect();
          const text=node.textContent.slice(i,i+len);i+=len;
          if(!box.height||text==='\n')continue;
          let line=lines.at(-1);
          if(!line||Math.abs(line.y-box.y)>3){line={x:box.x,y:box.y,w:0,h:box.height,text:''};lines.push(line)}
          line.text+=text;line.w=Math.max(line.w,box.right-line.x);line.h=Math.max(line.h,box.height);
        }
      }
      return lines;
    }
    function background(el){
      while(el){const s=getComputedStyle(el).backgroundColor;if(s!=='rgba(0, 0, 0, 0)'&&s!=='transparent')return s;el=el.parentElement}
      return 'rgb(247,246,239)';
    }
    for(const el of document.querySelectorAll('[data-text],td,th')){
      const box=el.getBoundingClientRect(), style=getComputedStyle(el), lines=actualLines(el);
      const id=el.dataset.id||el.className||el.tagName.toLowerCase(), bg=background(el), fg=style.color;
      const selector=el.tagName.toLowerCase()+(el.className?'.'+String(el.className).trim().split(/\s+/).join('.'):'');
      const ratio=(Math.max(lum(rgb(bg)),lum(rgb(fg)))+.05)/(Math.min(lum(rgb(bg)),lum(rgb(fg)))+.05);
      for(const line of lines){
        if(line.x<0||line.y<0||line.x+line.w>1280.5||line.y+line.h>720)issues.push({rule:'element_out_of_canvas',severity:'high',id,rect:line});
        if(line.x<box.x-.5||line.x+line.w>box.right+1||(['hidden','clip','scroll','auto'].includes(style.overflowY)&&line.y+line.h>box.bottom+2))issues.push({rule:'out_of_container',severity:'high',id,rect:line});
      }
      if(ratio<3)issues.push({rule:'low_contrast_text',severity:'high',id,ratio});
      const item={id,selector,...rect(box),text:el.textContent,lines,fontSize:parseFloat(style.fontSize),bold:Number(style.fontWeight)>=600,color:fg,bg,lineHeight:parseFloat(style.lineHeight)};
      item.role=el.dataset.role||null;item.fontFamily=style.fontFamily;
      item.textKey=el.dataset.ref||null;
      if(el.closest('[data-chart-plot],[data-chart-legend]'))item.chartLabel=true;
      if(el.hasAttribute('data-text'))texts.push(item);
    }
    for(let i=0;i<texts.length;i++)for(let j=i+1;j<texts.length;j++){
      const a=texts[i],b=texts[j];
      if(a.lines.some(x=>b.lines.some(y=>Math.min(x.x+x.w,y.x+y.w)-Math.max(x.x,y.x)>2&&Math.min(x.y+x.h,y.y+y.h)-Math.max(x.y,y.y)>2)))issues.push({rule:'text_collision',severity:'high',a:a.id,b:b.id,a_selector:a.selector,b_selector:b.selector,a_text:a.text.slice(0,80),b_text:b.text.slice(0,80),a_rect:{x:a.x,y:a.y,w:a.w,h:a.h},b_rect:{x:b.x,y:b.y,w:b.w,h:b.h}});
    }
    for(const el of document.querySelectorAll('table')){
      const rows=[...el.rows].map(r=>[...r.cells].map(c=>({text:c.textContent,...rect(c.getBoundingClientRect()),bg:getComputedStyle(c).backgroundColor,bold:c.tagName==='TH'})));
      tables.push({...rect(el.getBoundingClientRect()),rows,fontSize:parseFloat(getComputedStyle(el).fontSize)});
    }
    for(const el of document.images){
      if(!el.complete||el.naturalWidth===0)issues.push({rule:'resource_failed',severity:'high',src:el.src.slice(0,80)});
      const r=el.getBoundingClientRect();images.push({...rect(r),src:el.src});
      for(const t of texts)if(t.lines.some(l=>{
        const x=Math.max(l.x,r.x)+Math.min(l.w,r.width)/2,y=Math.max(l.y,r.y)+Math.min(l.h,r.height)/2;
        return x<Math.min(l.x+l.w,r.right)&&y<Math.min(l.y+l.h,r.bottom)&&document.elementFromPoint(x,y)===el;
      }))issues.push({rule:'image_covers_text',severity:'high',id:t.id});
    }
    if(document.querySelector('.custom-footer')){
      for(const t of texts){
        if(t.selector.includes('custom-footer'))continue;
        const inFooter=t.y>=660&&t.fontSize<=16;
        if(!inFooter&&t.lines.some(l=>l.y+l.h>638))issues.push({rule:'footer_clearance',severity:'high',id:t.id,selector:t.selector,text:t.text.slice(0,80),bottom:Math.max(...t.lines.map(l=>l.y+l.h)),limit:638});
      }
    }
    const designed=document.querySelector('.design-content');
    if(designed){
      const limit=designed.getBoundingClientRect();
      for(const el of designed.querySelectorAll('[data-text]')){
        for(const line of actualLines(el))if(line.y+line.h>limit.bottom+2)issues.push({rule:'content_capacity',severity:'high',id:el.className});
      }
      const header=document.querySelector('header').getBoundingClientRect();
      if(header.bottom>limit.top-14)issues.push({rule:'content_capacity',severity:'high',id:'header'});
    }
    const content=document.querySelector('.content');
    if(content&&content.getBoundingClientRect().top+content.scrollHeight>659)issues.push({rule:'content_capacity',severity:'high'});
    return {issues,texts,tables,images};
  });
}

export function splitPage(page) {
  const blocks=page.blocks;
  if(!blocks?.length)throw new Error('封面或场景页排版异常，不能自动删减标题');
  if(blocks.length>1){
    const cut=Math.ceil(blocks.length/2);
    return [{...page,blocks:blocks.slice(0,cut)},{...page,continuation:true,blocks:blocks.slice(cut)}];
  }
  const b=blocks[0];
  if(b.kind==='table'){
    if(b.rows.length<2)throw new Error(`表格单行超出容量，需调整列布局：${b.id}`);
    const cut=Math.ceil(b.rows.length/2);
    return [{...page,blocks:[{...b,rows:b.rows.slice(0,cut)}]},{...page,continuation:true,blocks:[{...b,rows:b.rows.slice(cut)}]}];
  }
  if(b.text.length<70)throw new Error(`短文本仍溢出，需检查主题：${b.id}`);
  const middle=Math.floor(b.text.length/2), punctuation=/[。；！？\n]/g;
  const candidates=[...b.text.matchAll(punctuation)].map(m=>m.index+1).filter(n=>n>20&&n<b.text.length-20);
  let cut=candidates.sort((a,c)=>Math.abs(a-middle)-Math.abs(c-middle))[0]||middle;
  if(/[\uD800-\uDBFF]/.test(b.text[cut-1]))cut++;
  return [{...page,blocks:[{...b,text:b.text.slice(0,cut)}]},{...page,continuation:true,blocks:[{...b,text:b.text.slice(cut)}]}];
}

export function verifyCoverage(source, pages) {
  const out=pages.flatMap(p=>p.blocks||[]);
  for(const b of source){
    const found=out.filter(x=>x.id===b.id);
    if(!found.length)throw new Error(`正文遗漏：${b.id}`);
    if(b.kind==='table'){
      if(JSON.stringify(found.flatMap(x=>x.rows))!==JSON.stringify(b.rows)||found.some(x=>JSON.stringify(x.header)!==JSON.stringify(b.header)))throw new Error(`表格内容变化：${b.id}`);
    }else if(found.map(x=>x.text).join('')!==b.text)throw new Error(`正文变化：${b.id}`);
  }
  if(out.some(b=>!source.some(s=>s.id===b.id)))throw new Error('出现无来源正文块');
  return true;
}

const color=s=>{const nums=s.match(/[\d.]+/g);return nums?.length>=3?nums.slice(0,3).map(n=>Math.round(Number(n)).toString(16).padStart(2,'0')).join(''):'173B2B'};
async function exportPptx(pages, probes, plan, folder){
  const pptx=new pptxgen();pptx.defineLayout({name:'MARKETING',width:W/96,height:H/96});pptx.layout='MARKETING';
  pptx.author='Marketing V2 Agent';pptx.subject='由完整文稿生成的可编辑营销提案';pptx.title=plan.title;pptx.lang='zh-CN';
  pptx.theme={headFontFace:'Microsoft YaHei',bodyFontFace:'Microsoft YaHei',lang:'zh-CN'};
  for(let i=0;i<pages.length;i++){
    const page=pages[i],probe=probes[i],slide=pptx.addSlide();slide.background={color:plan.theme.background};
    if(page.backgroundFile)slide.addImage({path:path.join(folder,page.backgroundFile),x:0,y:0,w:W/96,h:H/96});
    if(page.layout==='chart'){
      const chart=nativeChartSpec(page.chart,plan.theme);
      slide.addChart(chart.type,chart.data,chart.options);
    }
    for(const img of (page.backgroundFile?[]:probe.images))slide.addImage({data:img.src,x:img.x/96,y:img.y/96,w:img.w/96,h:img.h/96,sizing:{type:'cover',w:img.w/96,h:img.h/96}});
    // 使用浏览器实际断行，避免不同文字测量库在中英文混排时重新换行。
    for(const t of probe.texts){
      if(!t.lines.length||t.chartLabel)continue;
      if(plan.design_mode==='narrative'){
        // Each browser line is one native text shape, avoiding PowerPoint font-dependent leading.
        for(const line of t.lines)slide.addText(line.text,{x:line.x/96,y:(t.y+line.y-t.lines[0].y)/96,
          w:(t.w+3)/96,h:(Math.max(t.lineHeight||0,t.fontSize*1.4)+5)/96,
          fontFace:'Microsoft YaHei',lang:'zh-CN',fontSize:t.fontSize*.75,color:color(t.color),bold:t.bold,
          margin:0,paraSpaceAfter:0,breakLine:false,valign:'top',wrap:false});
      }else{
        slide.addText(t.lines.map(l=>l.text).join('\n'),{x:t.x/96,y:t.y/96,w:(t.w+2)/96,h:(t.h+5)/96,
          fontFace:'Microsoft YaHei',lang:'zh-CN',fontSize:t.fontSize*.75,color:color(t.color),bold:t.bold,
          margin:0,paraSpaceAfter:0,lineSpacingMultiple:1.15,valign:'top',wrap:false});
      }
    }
    for(const t of probe.tables){
      const rows=t.rows.map((row,ri)=>row.map(c=>({text:c.text,options:{bold:c.bold,fill:ri===0?'E2E9D6':ri%2===0?'EFF1E5':'F7F6EF'}})));
      slide.addTable(rows,{x:t.x/96,y:t.y/96,w:t.w/96,h:t.h/96,
        colW:t.rows[0].map(c=>c.w/96),rowH:t.rows.map(r=>r[0].h/96),fontFace:'Microsoft YaHei',
        lang:'zh-CN',fontSize:t.fontSize*.75,color:'173B2B',margin:[9,10.5,9,10.5],border:{type:'solid',pt:.5,color:'BDC9B3'},
        valign:'top',align:'left',autoPage:false,paraSpaceAfter:0});
    }
    const sources=(page.blocks||[]).map(b=>`${b.id}（原稿第${b.source_line}行）\n${b.source}`).join('\n\n');
    const urls=[...new Set((plan.source_blocks.flatMap(b=>b.source.match(/https?:\/\/[^)\s]+/g)||[])))];
    slide.addNotes([sources||page.subtitle,`源任务：${plan.source_run_id}；SHA256：${plan.source_sha256}`,
      '原稿中的预测、假设和待核验状态原样保留；本次排版未重新核实市场数据。',
      page.image?plan.asset_provenance:'', '文稿来源链接：\n'+urls.join('\n')].join('\n\n'));
  }
  await pptx.writeFile({fileName:path.join(folder,'presentation.pptx')});
}

export async function renderDeck(folder) {
  const plan=JSON.parse(await fs.readFile(path.join(folder,'plan.json'),'utf8'));
  const assets={};for(const [k,v] of Object.entries(plan.assets))assets[k]='data:image/png;base64,'+(await fs.readFile(path.join(folder,v))).toString('base64');
  const narrative=plan.design_mode==='narrative', theme=narrative?plan.theme:null;
  let pages=plan.pages.map(p=>({...structuredClone(p),...(p.custom?{asset_data:assets}:{}),...(p.asset&&assets[p.asset]?{image:assets[p.asset]}:{})}));
  if(!narrative&&assets.cover)pages.unshift({layout:'cover',title:plan.title.includes('轻芽')?'轻芽\n低糖茶饮新品上市':plan.title,subtitle:plan.title.includes('轻芽')?'抖音全链路整合营销提案':'营销方案',caption:'AI 生成概念视觉',image:assets.cover,blocks:[]});
  const insert=(key,match,title,subtitle)=>{
    if(!assets[key])return;const at=pages.findIndex(p=>p.title.includes(match));
    if(at>=0)pages.splice(at,0,{layout:'image',title,subtitle,caption:'AI 生成场景概念图 · 产品参数待核验',image:assets[key],blocks:[]});
  };
  if(!narrative)insert('office','5.1','下午三点\n给生活留一点甜','午后办公场景\n用日常共情承接产品体验');
  if(!narrative)insert('outdoor','5.3','出游出片搭子','周末出游场景\n让产品进入真实生活的构图');
  if(!narrative && plan.style_id && plan.style_id!=='auto')pages.forEach(p=>p.detail_theme=plan.theme);
  const browser=await chromium.launch({headless:true,...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:W,height:H},deviceScaleFactor:1});
  await page.route('**/*',route=>route.request().url().startsWith('data:')?route.continue():route.abort());
  const repairs=[],probes=[];
  try{
    for(let i=0;i<pages.length;i++){
      const p=pages[i];await page.goto('about:blank');await page.setContent(pageHTML(p,i,pages.length,theme));await page.evaluate(()=>document.fonts.ready);
      await page.evaluate(()=>Promise.all([...document.images].map(im=>im.decode().catch(()=>{}))));
      const probe=await renderProbe(page);
      if(probe.issues.length){
        const attempt=(p.repairAttempt||0)+1;
        repairs.push({page:i,attempt,issues:probe.issues});
        if(attempt>7||probe.issues.some(x=>!['element_out_of_canvas','out_of_container','content_capacity','text_collision'].includes(x.rule)))throw new Error(`无法自动修复 ${p.title}: ${JSON.stringify(probe.issues).slice(0,600)}`);
        if(p.custom)throw new Error('自定义页面复检失败，应交回页面Agent修正：'+p.title);
        if(p.layout==='chart')throw new Error('图表标签超出容量，请减少每页类别或缩短标签：'+p.title);
        const parts=(narrative?repairDesign(p):splitPage(p)).map(x=>({...x,repairAttempt:attempt}));pages.splice(i,1,...parts);i--;continue;
      }
    }
    if(narrative)verifyNotes(plan.source_blocks,pages);else verifyCoverage(plan.source_blocks,pages);
    await fs.mkdir(path.join(folder,'pages'),{recursive:true});await fs.mkdir(path.join(folder,'previews'),{recursive:true});
    for(let i=0;i<pages.length;i++){
      const html=pageHTML(pages[i],i,pages.length,theme);await fs.writeFile(path.join(folder,'pages',`${i+1}.html`),html);
      await page.goto('about:blank');await page.setContent(html);await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>Promise.all([...document.images].map(im=>im.decode().catch(()=>{}))));
      probes.push(await renderProbe(page));await page.screenshot({path:path.join(folder,'previews',`${i+1}.png`)});
      if(narrative){
        // Decorations/images flatten separately; text remains native, selectable and editable.
        await page.addStyleTag({content:'[data-text]{color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important}[data-chart-plot],[data-chart-legend]{visibility:hidden!important}'});
        pages[i].backgroundFile=`previews/${i+1}-background.png`;
        await page.screenshot({path:path.join(folder,pages[i].backgroundFile)});
      }
      await fs.appendFile(path.join(folder,'tool-trace.jsonl'),JSON.stringify({tool:'render_probe',page:i+1,issues:probes.at(-1).issues})+'\n');
      await fs.writeFile(path.join(folder,'progress.tmp'),JSON.stringify({stage:'rendering',completed_pages:i+1,total_pages:pages.length}));
      await fs.rename(path.join(folder,'progress.tmp'),path.join(folder,'progress.json'));
    }
    await exportPptx(pages,probes,plan,folder);
    const consistency=auditConsistency(pages,probes);
    await fs.writeFile(path.join(folder,'consistency.json'),JSON.stringify(consistency,null,2));
    if(!consistency.passed)throw new Error('同类页面字号/字体不一致：'+JSON.stringify(consistency.issues).slice(0,700));
    await fs.appendFile(path.join(folder,'tool-trace.jsonl'),JSON.stringify({tool:'export_pptx',pages:pages.length})+'\n');
    const packageCheck=JSON.parse(execFileSync(process.env.PRESENTATION_PYTHON||'python3',
      [fileURLToPath(new URL('./validate_package.py',import.meta.url)),path.join(folder,'presentation.pptx'),path.join(folder,'plan.json')],{encoding:'utf8'}));
    if(packageCheck.slides!==pages.length)throw new Error('PPTX页数与HTML不一致');
    // 可离线播放的单文件，不依赖远程 CDN。
    const frames=pages.map((p,i)=>`<iframe title="第${i+1}页" sandbox srcdoc="${esc(pageHTML(p,i,pages.length,theme))}"></iframe>`).join('');
    await fs.writeFile(path.join(folder,'presentation.html'),`<!doctype html><meta charset="utf-8"><title>${esc(plan.title)}</title><style>body{margin:0;background:#202920;display:grid;gap:24px;justify-content:center;padding:32px}iframe{width:1280px;height:720px;border:0}@media print{body{display:block;padding:0}iframe{display:block;break-after:page}}@page{size:13.333in 7.5in;margin:0}</style>${frames}`);
    await fs.writeFile(path.join(folder,'outline.json'),JSON.stringify({...plan,pages:pages.map(({image,asset_data,...p})=>({...p,has_image:!!image}))},null,2));
    await fs.writeFile(path.join(folder,'geometry.json'),JSON.stringify(probes.map(({images,...p})=>({...p,image_count:images.length})),null,2));
    const report={passed:probes.every(p=>p.issues.length===0),page_count:pages.length,repair_count:repairs.length,repairs,package:packageCheck,page_generation:plan.page_generation||null,
      checks:{source_coverage:true,source_coverage_mode:narrative?'full_original_in_speaker_notes':'literal_on_slides',table_rows_preserved:true,browser_render:true,text_collision:true,contrast:true,resources:true,same_family_typography:consistency.passed,
        editable_text:true,editable_charts:pages.filter(p=>p.layout==='chart').length,editable_tables:!narrative,decorations_rasterized:narrative,pptx_package:true,pptx_application_render:'pending'},
      limitations:['规则检查不等于审美判断；未声称复刻远端探针的私有阈值','未重新核实原稿中的市场来源与预测','PPTX需另做实际应用渲染抽检']};
    await fs.writeFile(path.join(folder,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify({page_count:pages.length,repair_count:repairs.length,passed:report.passed}));
  }finally{await browser.close()}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)await renderDeck(path.resolve(process.argv[2]));
