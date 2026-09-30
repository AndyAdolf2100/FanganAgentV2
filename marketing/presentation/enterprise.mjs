// Enterprise HTML uses its own canvas and fixed shell, never the generic theme.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {chromium} from 'playwright';
import {chartHTML,nativeChartSpec,CHART_FRAME} from './charts.mjs';
const PptxGenJS=createRequire(import.meta.url)('pptxgenjs');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function measureEnterprise(page, canvas, modelMode=false) {
  return page.evaluate(({width,height,modelMode})=>{
    const issues=[],texts=[],slotLines=new Map(),slotIds=new WeakMap(),context=document.createElement('canvas').getContext('2d');let nextSlot=0;
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let node;
    const outside=(r,b)=>r.x<b.x-2||r.y<b.y-2||r.x+r.w>b.x+b.w+2||r.y+r.h>b.y+b.h+2;
    const box=r=>({x:r.x,y:r.y,w:r.width,h:r.height});
    while(node=walker.nextNode()){
      const el=node.parentElement;
      if(!node.textContent.trim()||el.closest('style,script,svg'))continue;
      const s=getComputedStyle(el),slot=el.closest('[data-ppt-slot]')||(modelMode?el.closest('p,li,td,th,h1,h2,h3,div,[data-source-block],[data-agenda-item],[data-metadata],[data-agenda-number],[data-section-number]')||el:null), region=el.closest('[data-ppt-editable]');
      let hidden=false;
      for(let p=el;p;p=p.parentElement){const c=getComputedStyle(p);hidden||=c.display==='none'||c.visibility!=='visible'||Number(c.opacity)<.1;}
      const alpha=s.color.match(/rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)/);
      hidden ||= !!alpha && Number(alpha[1])<.1;
      if(hidden){if(region||modelMode)issues.push({type:'invisible_text',slot:slot?.dataset.pptSlot});continue;}
      context.font=`${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
      const metrics=context.measureText(node.textContent),size=parseFloat(s.fontSize),lines=[];
      if(modelMode&&el.closest('[data-enterprise-body]')&&!el.closest('[data-enterprise-chart]')&&size<18)issues.push({type:'font_below_minimum',size});
      for(let i=0;i<node.length;){
        const length=node.textContent.codePointAt(i)>0xffff?2:1;
        const range=document.createRange();range.setStart(node,i);range.setEnd(node,i+length);
        const raw=range.getBoundingClientRect(),value=node.textContent.slice(i,i+length);i+=length;
        if(value==='\n'||!raw.width||!raw.height)continue;
        let line=lines.at(-1);
        if(!line||Math.abs(line.y-raw.y)>2){line={...box(raw),text:''};lines.push(line);}
        line.text+=value;line.w=Math.max(line.w,raw.right-line.x);line.h=Math.max(line.h,raw.height);
      }
      for(const line of lines){
        const ink={...line};
        if(Number.isFinite(metrics.fontBoundingBoxAscent+metrics.fontBoundingBoxDescent)&&Math.abs(line.h-metrics.fontBoundingBoxAscent-metrics.fontBoundingBoxDescent)<3){
          ink.y=line.y+metrics.fontBoundingBoxAscent-metrics.actualBoundingBoxAscent-1;
          ink.h=metrics.actualBoundingBoxAscent+metrics.actualBoundingBoxDescent+2;
        }
        if(outside(ink,{x:0,y:0,w:width,h:height}))issues.push({type:'out_of_canvas',slot:slot?.dataset.pptSlot,text:line.text.slice(0,40),bounds:ink,frame:{x:0,y:0,w:width,h:height}});
        const templateFrame=el.closest('[data-template-element]');
        if(modelMode&&templateFrame&&outside(ink,box(templateFrame.getBoundingClientRect())))issues.push({type:'out_of_template_text_frame',element:templateFrame.dataset.templateElement,text:line.text.slice(0,40),bounds:ink,frame:box(templateFrame.getBoundingClientRect())});
        if(slot){
          if(!slotIds.has(slot))slotIds.set(slot,`measured-${nextSlot++}`);
          const key=slot.dataset.pptSlot||slotIds.get(slot), role=region?.dataset.pptRole;
          const allow=['pageNumber','sectionNumber','contentsNumber'].includes(role)&&slot.dataset.pptAllowOverflow==='true';
          if(!allow&&outside(ink,box(slot.getBoundingClientRect())))issues.push({type:'out_of_slot',slot:key,text:line.text.slice(0,40),bounds:ink,frame:box(slot.getBoundingClientRect())});
          if(size<Number(slot.dataset.pptMinFontSize||0)-.1)issues.push({type:'font_below_minimum',slot:key,size});
          if(!slotLines.has(key))slotLines.set(key,[]);slotLines.get(key).push(ink);
        }
        // A transformed text box is rasterized rather than exported incorrectly.
        let transformed=false;
        for(let p=el;p;p=p.parentElement){const c=getComputedStyle(p);transformed ||= !['none','matrix(1, 0, 0, 1, 0, 0)'].includes(c.transform)||Number(c.opacity)!==1||c.textShadow!=='none'||c.textDecorationLine!=='none'||!['normal','0px'].includes(c.letterSpacing);}
        texts.push({...line,size,font:s.fontFamily,bold:Number(s.fontWeight)>=600,italic:s.fontStyle==='italic',color:s.color,transformed,chart:!!el.closest('[data-enterprise-chart]')});
      }
    }
    const entries=[...slotLines];
    for(let i=0;i<entries.length;i++)for(let j=i+1;j<entries.length;j++){
      const collision=entries[i][1].some(a=>entries[j][1].some(b=>{
        const w=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x),h=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);
        return w>3&&h>3&&w*h>Math.min(a.w*a.h,b.w*b.h)*.15;
      }));
      if(collision)issues.push({type:'text_overlap',slot:entries[i][0],other:entries[j][0]});
    }
    for(const img of document.images)if(!img.complete||!img.naturalWidth)issues.push({type:'missing_image'});
    return {issues,texts};
  },{...canvas,modelMode});
}

// Validation only: record real computed geometry/styles, never repair a layout.
export async function inspectModelContract(page, canvas, contract, reference) {
  return page.evaluate(({canvas,contract,reference})=>{
    const issues=[],elements={};
    const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
    const outside=(a,b)=>a.x<b.x-2||a.y<b.y-2||a.x+a.width>b.x+b.width+2||a.y+a.height>b.y+b.height+2;
    const properties=['display','visibility','opacity','backgroundColor','backgroundImage','transform','clipPath','filter','zIndex','borderTopWidth','borderTopColor','borderRadius','fill','stroke'];
    const appearance=n=>{
      const s=getComputedStyle(n),result=Object.fromEntries(properties.map(k=>[k,s[k]]));
      if([...n.childNodes].some(c=>c.nodeType===Node.TEXT_NODE&&c.textContent.trim())){
        for(const k of ['color','fontFamily','fontSize','fontWeight','lineHeight','letterSpacing'])result[k]=s[k];
      }
      return result;
    };
    for(const n of document.querySelectorAll('[data-template-element]')){
      elements[n.dataset.templateElement]={rect:rect(n),styles:appearance(n),
        children:[...n.querySelectorAll('*')].map(c=>({rect:rect(c),styles:appearance(c)}))};
    }
    if(!contract)return {issues,elements};
    const root=document.querySelector('.ppt-slide');
    if(!root||Math.abs(root.getBoundingClientRect().width-canvas.width)>1||Math.abs(root.getBoundingClientRect().height-canvas.height)>1||Math.abs(root.getBoundingClientRect().x)>1||Math.abs(root.getBoundingClientRect().y)>1)issues.push({type:'wrong_canvas'});
    const near=(a,b)=>a&&b&&Object.keys(b).every(k=>typeof b[k]==='number'?Math.abs(a[k]-b[k])<=1:a[k]===b[k]);
    for(const id of [...contract.protected_elements,...contract.text_frames]){
      const a=elements[id],b=reference?.elements?.[id];
      if(!a&&contract.text_frames.includes(id)&&id!==contract.title_element)continue;
      const node=document.querySelector(`[data-template-element="${id}"]`);
      if(contract.text_frames.includes(id)&&id!==contract.title_element&&!node?.textContent.trim())continue;
      if(!a||!b||!near(a.rect,b.rect))issues.push({type:'template_geometry_changed',element:id,
        selector:`[data-template-element="${id}"]`,expected_bounds:b?.rect??null,actual_bounds:a?.rect??null,
        fix_hint:'恢复模板原外框的x/y/width/height，检查覆盖它的CSS和父级变换。文字框内可调整字号、行距和换行，不得扩大外框；保留完整原文。'});
      if(contract.protected_elements.includes(id)&&a&&b&&(!near(a.styles,b.styles)||a.children.length!==b.children.length||a.children.some((c,i)=>!near(c.rect,b.children[i]?.rect)||!near(c.styles,b.children[i]?.styles))))issues.push({type:'protected_style_changed',element:id});
    }
    const frame=contract.body_frame;
    if(frame){
      const body=document.querySelector('[data-enterprise-body]');
      if(!body||outside(rect(body),frame))issues.push({type:'body_outside_contract',frame,bounds:body?rect(body):null});
      for(const n of document.querySelectorAll('[data-source-block]')){
        const header=n.closest('[data-template-element]');
        if(header&&contract.text_frames.includes(Number(header.dataset.templateElement)))continue;
        if(!body?.contains(n)||outside(rect(n),frame))issues.push({type:'source_outside_body',source:n.dataset.sourceBlock,frame,bounds:rect(n),inside_body:!!body?.contains(n)});
      }
    }
    for(const n of document.querySelectorAll('[data-source-block],[data-agenda-item],[data-agenda-number],[data-metadata],[data-section-number]')){
      if(n.closest('[hidden]')||!n.getClientRects().length)issues.push({type:'invisible_source'});
      for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||s.visibility!=='visible'||Number(s.opacity)<.1)issues.push({type:'invisible_source'});}
      // Check paint order too: a number behind a template hexagon is present
      // in the DOM and has valid geometry, but cannot be read on the slide.
      const walker=document.createTreeWalker(n,NodeFilter.SHOW_TEXT);let text,covered=false;
      while((text=walker.nextNode())&&!covered){
        if(!text.textContent.trim())continue;
        for(const i of [...new Set([0,Math.floor(text.length/2),text.length-1])]){
          if(!text.textContent[i]?.trim())continue;
          const range=document.createRange();range.setStart(text,i);range.setEnd(text,i+1);
          const r=range.getBoundingClientRect(),top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          if(!top||n.contains(top)||top.contains(n))continue;
          const s=getComputedStyle(top),opaque=!['transparent','rgba(0, 0, 0, 0)'].includes(s.backgroundColor);
          if(opaque||['IMG','path','rect','polygon','circle','ellipse'].includes(top.tagName)){
            issues.push({type:'covered_text',source:n.dataset.sourceBlock||n.dataset.agendaNumber||n.dataset.metadata||'section-number',text:text.textContent.slice(0,40),covering_element:top.closest('[data-template-element]')?.dataset.templateElement});covered=true;break;
          }
        }
      }
    }
    return {issues,elements};
  },{canvas,contract,reference});
}

export async function renderEnterprise(folder, probeOnly=false){
  const plan=JSON.parse(await fs.readFile(path.join(folder,'plan.json'),'utf8'));
  const {width,height}=plan.canvas;
  if(Math.abs(width/height-16/9)>.0001)throw new Error('企业模板仅支持16:9');
  await Promise.all(['previews','pages','backgrounds'].map(n=>fs.mkdir(path.join(folder,n),{recursive:true})));
  const browser=await chromium.launch({headless:true,...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
  const pptx=new PptxGenJS();pptx.defineLayout({name:'ENTERPRISE',width:width/96,height:height/96});pptx.layout='ENTERPRISE';
  pptx.title=plan.title;pptx.author='Marketing Project Agent';pptx.lang='zh-CN';
  const reports=[],frames=[];
  let referenceReports=[];
  try{
    if(plan.pages.some(p=>p.model_html))referenceReports=JSON.parse(await fs.readFile(path.join(folder,'template-reference','enterprise-probe.json'),'utf8')).pages;
    const page=await browser.newPage({viewport:{width:Math.round(width),height:Math.round(height)},deviceScaleFactor:1});
    await page.route('**/*',r=>r.request().url().startsWith('data:')?r.continue():r.abort());
    for(let i=0;i<plan.pages.length;i++){
      const p=plan.pages[i];await page.goto('about:blank');await page.setContent(p.html);
      const chartTheme={background:'FFFFFF',text:(plan.theme?.text||'#223344').replace(/^#/,''),muted:(plan.theme?.text||'#223344').replace(/^#/,''),chartColors:plan.theme?.palette?.filter(c=>!['#FFFFFF','#000000'].includes(c))};
      const nativeCharts=[];
      for(const entry of p.enterprise_charts||[]){
        const markup=chartHTML({chart:entry.chart,title:'',subtitle:'',section:'',evidence_note:''},0,1,chartTheme);
        const rect=await page.evaluate(({id,markup,unit,font,frame})=>{
          const target=document.querySelector(`[data-enterprise-chart="${id}"]`);
          if(!target)throw Error('Missing source-bound chart region');
          target.dataset.pptSlot=id;
          target.dataset.pptMinFontSize='15';
          const doc=new DOMParser().parseFromString(markup,'text/html'),plot=doc.querySelector('[data-chart-plot]');
          const scale=Math.min(target.clientWidth/frame.w,(target.clientHeight-28)/frame.h);
          const caption=document.createElement('div');caption.textContent=`单位：${unit}`;
          caption.style.cssText=`font:18px "${font}",sans-serif;line-height:24px`;target.append(caption);
          const wrap=document.createElement('div');wrap.style.cssText=`position:absolute;left:0;top:28px;width:${frame.w}px;height:${frame.h}px;transform:scale(${scale});transform-origin:top left;font-family:"${font}",sans-serif`;
          for(const child of [...plot.childNodes])wrap.append(child.cloneNode(true));
          for(const label of wrap.querySelectorAll('.chart-label')){label.style.position='absolute';label.style.lineHeight='1.3';label.style.overflow='visible';label.style.wordBreak='break-all';}
          target.append(wrap);const r=target.getBoundingClientRect();
          return {x:r.x,y:r.y,w:frame.w*scale,h:frame.h*scale,scale};
        },{id:entry.id,markup,unit:entry.chart.unit,font:plan.theme?.font||'Noto Sans CJK SC',frame:CHART_FRAME});
        nativeCharts.push({...entry,rect});
      }
      await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})));});
      const report=await measureEnterprise(page,plan.canvas,p.model_html===true);reports.push({page:i+1,issues:report.issues});
      const contractReport=await inspectModelContract(page,plan.canvas,p.model_html?p.template_contract:null,referenceReports[p.template_page]);
      report.issues.push(...contractReport.issues);reports.at(-1).elements=contractReport.elements;
      await page.screenshot({path:path.join(folder,'previews',`${i+1}.png`)});
      if(probeOnly)continue;
      if(report.issues.length)throw new Error(`第${i+1}页未通过排版检查`);
      const renderedHTML=await page.content();
      await fs.writeFile(path.join(folder,'pages',`${i+1}.html`),renderedHTML);
      frames.push(`<iframe title="第${i+1}页" sandbox srcdoc="${esc(renderedHTML)}"></iframe>`);
      // Hide only normal text ink; retain SVG, photos and all fixed decorations.
      await page.evaluate(()=>{
        const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),elements=new Set();let n;
        while(n=walker.nextNode()){
          const el=n.parentElement;if(!n.textContent.trim()||el.closest('style,script,svg'))continue;
          let transformed=false;for(let p=el;p;p=p.parentElement){const c=getComputedStyle(p);transformed ||= !['none','matrix(1, 0, 0, 1, 0, 0)'].includes(c.transform)||Number(c.opacity)!==1||c.textShadow!=='none'||c.textDecorationLine!=='none'||!['normal','0px'].includes(c.letterSpacing);}
          if(!transformed)elements.add(el);
        }
        for(const el of elements){el.style.setProperty('-webkit-text-fill-color','transparent','important');el.style.setProperty('text-shadow','none','important');}
        for(const el of document.querySelectorAll('[data-enterprise-chart]'))el.style.visibility='hidden';
      });
      const background=path.join(folder,'backgrounds',`${i+1}.png`);await page.screenshot({path:background});
      const slide=pptx.addSlide();slide.addImage({path:background,x:0,y:0,w:width/96,h:height/96});
      for(const t of report.texts){
        if(t.transformed||t.chart)continue;
        const rgb=t.color.match(/[\d.]+/g)||[0,0,0],color=rgb.slice(0,3).map(v=>Math.round(Number(v)).toString(16).padStart(2,'0')).join('');
        const font=t.font.split(',')[0].replace(/["']/g,'').trim();
        slide.addText(t.text,{x:t.x/96,y:t.y/96,w:(t.w+3)/96,h:t.h/96,margin:0,breakLine:false,
          fontFace:font==='system-ui'?'Noto Sans CJK SC':font,fontSize:t.size*.75,bold:t.bold,italic:t.italic,color,
          wrap:false,valign:'top',paraSpaceAfter:0});
      }
      for(const entry of nativeCharts){
        const spec=nativeChartSpec(entry.chart,chartTheme),r=entry.rect;
        Object.assign(spec.options,{x:r.x/96,y:(r.y+28)/96,w:r.w/96,h:r.h/96});
        for(const key of ['legendFontFace','catAxisLabelFontFace','valAxisLabelFontFace','dataLabelFontFace'])spec.options[key]=plan.theme?.font||'Noto Sans CJK SC';
        for(const key of ['legendFontSize','catAxisLabelFontSize','valAxisLabelFontSize','dataLabelFontSize'])spec.options[key]*=r.scale;
        slide.addChart(spec.type,spec.data,spec.options);
        slide.addText(`单位：${entry.chart.unit}`,{x:r.x/96,y:r.y/96,w:r.w/96,h:24/96,fontFace:plan.theme?.font||'Noto Sans CJK SC',fontSize:13.5,color:chartTheme.text,margin:0});
      }
      const ids=new Set(p.block_ids||[]);
      slide.addNotes([plan.source_blocks.filter(b=>ids.has(b.id)).map(b=>b.text).join('\n\n'),
        `模板 ${plan.template_id} v${plan.template_revision}；原稿 SHA256 ${plan.source_sha256}`]);
    }
    await fs.writeFile(path.join(folder,'enterprise-probe.json'),JSON.stringify({pages:reports},null,2));
    if(probeOnly)return reports;
    await pptx.writeFile({fileName:path.join(folder,'presentation.pptx')});
    await fs.writeFile(path.join(folder,'presentation.html'),`<!doctype html><meta charset="utf-8"><title>${esc(plan.title)}</title><style>body{margin:0;padding:24px;background:#222;display:grid;justify-content:center;gap:24px}iframe{border:0;width:${width}px;height:${height}px}@media print{body{padding:0;display:block}iframe{display:block;break-after:page}}@page{size:${width/96}in ${height/96}in;margin:0}</style>${frames.join('')}`);
    await fs.writeFile(path.join(folder,'outline.json'),JSON.stringify({title:plan.title,pages:plan.pages.map(({html,...p})=>p)},null,2));
    await fs.writeFile(path.join(folder,'report.json'),JSON.stringify({passed:true,page_count:plan.pages.length,repair_count:0,
      checks:{source_coverage:true,source_coverage_mode:'literal_on_slides',fixed_template_structure:true,browser_render:true,
        editable_text:true,native_charts:plan.pages.reduce((n,p)=>n+(p.enterprise_charts?.length||0),0),decorations_rasterized:true,transformed_text_rasterized:true,pptx_application_render:'pending'},
      limitations:['复杂装饰与旋转文字以图像保留；普通文字可编辑','未在PowerPoint应用内验证；缺少企业字体时可能发生替换']},null,2));
  }finally{await browser.close();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)await renderEnterprise(path.resolve(process.argv[2]),process.argv[3]==='probe');
