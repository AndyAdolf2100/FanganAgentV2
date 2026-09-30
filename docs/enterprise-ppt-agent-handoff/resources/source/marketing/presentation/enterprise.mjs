// Enterprise HTML uses its own canvas and fixed shell, never the generic theme.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
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

// Descriptive evidence only. The agent decides whether wrapping, spacing or
// contrast is inappropriate; this function never adds a hard validation issue.
export async function measureLayoutEvidence(page, canvas, contract=null) {
  return page.evaluate(({canvas,contract})=>{
    const box=r=>({x:r.x,y:r.y,width:r.width,height:r.height});
    const rect=n=>box(n.getBoundingClientRect());
    const union=items=>items.length?{x:Math.min(...items.map(r=>r.x)),y:Math.min(...items.map(r=>r.y)),
      width:Math.max(...items.map(r=>r.x+r.width))-Math.min(...items.map(r=>r.x)),
      height:Math.max(...items.map(r=>r.y+r.height))-Math.min(...items.map(r=>r.y))}:null;
    const intersect=(a,b)=>{const x=Math.max(a.x,b.x),y=Math.max(a.y,b.y),right=Math.min(a.x+a.width,b.x+b.width),bottom=Math.min(a.y+a.height,b.y+b.height);return right>x&&bottom>y?{x,y,width:right-x,height:bottom-y}:null;};
    const visible=n=>{for(let p=n;p instanceof Element;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||s.visibility!=='visible'||Number(s.opacity)===0)return false;}return !!n.getClientRects().length;};
    const selector=n=>{if(n===document.body)return 'body';if(n===document.documentElement)return 'html';if(n.id)return '#'+CSS.escape(n.id);for(const key of ['data-ppt-slot','data-source-block','data-template-element'])if(n.hasAttribute(key))return `[${key}="${CSS.escape(n.getAttribute(key))}"]`;const parts=[];for(let p=n;p&&p!==document.body;p=p.parentElement){const siblings=[...p.parentElement?.children||[]].filter(c=>c.tagName===p.tagName);parts.unshift(p.tagName.toLowerCase()+`:nth-of-type(${siblings.indexOf(p)+1})`);}return 'body>'+parts.join('>');};
    const identity=n=>({selector:selector(n),source_id:n.closest('[data-source-block]')?.dataset.sourceBlock??null,
      slot_id:n.closest('[data-ppt-slot]')?.dataset.pptSlot??null,template_element:n.closest('[data-template-element]')?.dataset.templateElement??null});
    const type=s=>({font_family:s.fontFamily,font_weight:s.fontWeight,font_size_px:parseFloat(s.fontSize),line_height:s.lineHeight,
      white_space:s.whiteSpace,letter_spacing:s.letterSpacing,word_spacing:s.wordSpacing,text_align:s.textAlign,
      word_break:s.wordBreak,overflow_wrap:s.overflowWrap,text_transform:s.textTransform,color:s.color});
    const rgb=value=>{const match=value.match(/^rgba?\(([^)]+)\)$/);if(!match)return null;const parts=match[1].split(/[,\s/]+/).filter(Boolean).map(Number);return parts.length>=3&&parts.every(Number.isFinite)?[...parts.slice(0,3),parts[3]??1]:null;};
    const luminance=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
    const contrast=n=>{
      const foreground=getComputedStyle(n).color,layers=[];let background=null,complex=false;
      for(let p=n;p instanceof Element;p=p.parentElement){const s=getComputedStyle(p),c=rgb(s.backgroundColor),image=s.backgroundImage!=='none';
        complex ||= Number(s.opacity)!==1||s.filter!=='none'||s.mixBlendMode!=='normal';
        if(background)continue;
        if(layers.length<8)layers.push({selector:selector(p),color:s.backgroundColor,has_background_image:image,opacity:Number(s.opacity)});
        complex ||= image;
        if(c&&c[3]===1){background=s.backgroundColor;continue;}
        if(c&&c[3]>0&&c[3]<1)complex=true;
      }
      const fg=rgb(foreground),bg=rgb(background||'');
      let ratio=null;if(fg&&fg[3]===1&&bg&&!complex){const a=luminance(fg),b=luminance(bg);ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);}
      return {foreground,solid_ancestor_background:background,ratio,background_layers:layers,
        basis:ratio===null?'unmeasured_complex_or_transparent_background':'computed_solid_ancestor_estimate',
        limitations:['未采样截图像素，不能排除覆盖层、SVG图形或图片影响']};
    };
    const textNodes=n=>{const result=[],walker=document.createTreeWalker(n,NodeFilter.SHOW_TEXT);let t;while(t=walker.nextNode())if(!t.parentElement.closest('style,script,svg')&&visible(t.parentElement))result.push(t);return result;};
    const lineRects=nodes=>{const result=[];for(const t of nodes){if(!t.textContent.trim())continue;const range=document.createRange();range.selectNodeContents(t);for(const r of range.getClientRects())if(r.width&&r.height)result.push(box(r));}return result;};
    const groupedLines=rects=>{const lines=[];for(const r of [...rects].sort((a,b)=>a.y-b.y||a.x-b.x)){const found=lines.find(line=>Math.min(line.y+line.height,r.y+r.height)-Math.max(line.y,r.y)>Math.min(line.height,r.height)*.5);if(found)Object.assign(found,union([found,r]));else lines.push({...r});}return lines;};
    const nowrapWidth=nodes=>{
      const clone=document.createElement('div');clone.style.cssText='position:fixed!important;left:-100000px!important;top:-100000px!important;width:max-content!important;height:auto!important;max-width:none!important;min-width:0!important;visibility:hidden!important;white-space:pre!important;display:inline-block!important;padding:0!important;border:0!important;margin:0!important;transform:none!important;contain:layout style paint;';
      let lastSpace=true,previousNode=null;
      for(const t of nodes){const s=getComputedStyle(t.parentElement);let value=t.textContent.replace(/[\r\n]+/g,' ');
        if(previousNode&&!lastSpace){const between=document.createRange();between.setStartAfter(previousNode);between.setEndBefore(t);if(between.cloneContents().querySelector('br'))value=' '+value;}
        previousNode=t;
        if(!['pre','pre-wrap','break-spaces'].includes(s.whiteSpace)){value=value.replace(/\s+/g,' ');if(lastSpace)value=value.replace(/^ /,'');}
        if(!value)continue;lastSpace=/\s$/.test(value);
        const span=document.createElement('span');for(const key of ['fontFamily','fontSize','fontWeight','fontStyle','fontStretch','fontVariant','fontFeatureSettings','fontKerning','letterSpacing','wordSpacing','textTransform'])span.style[key]=s[key];
        span.style.whiteSpace='pre';span.textContent=value;clone.append(span);
      }
      const last=clone.lastChild;if(last&&!['pre','pre-wrap','break-spaces'].includes(getComputedStyle(nodes.at(-1)?.parentElement||document.body).whiteSpace))last.textContent=last.textContent.replace(/\s+$/,'');
      document.body.append(clone);try{return clone.getBoundingClientRect().width;}finally{clone.remove();}
    };
    const textMeasurement=n=>{
      const nodes=textNodes(n),rects=lineRects(nodes),lines=groupedLines(rects),s=getComputedStyle(n),first=nodes.find(t=>t.textContent.trim());
      let wrapping=n;while(wrapping.parentElement&&['inline','contents'].includes(getComputedStyle(wrapping).display))wrapping=wrapping.parentElement;
      const wrappingStyle=getComputedStyle(wrapping);
      const runs=[];for(const t of nodes){if(!t.textContent.trim())continue;const style=type(getComputedStyle(t.parentElement));if(!runs.some(run=>JSON.stringify(run)===JSON.stringify(style)))runs.push(style);}
      return {...identity(n),text:n.textContent.trim().slice(0,1200),tag:n.tagName.toLowerCase(),rect:rect(n),
        ...type(first?getComputedStyle(first.parentElement):s),typography_runs:runs,
        line_count:lines.length,line_rects:lines,text_bounds:union(rects),line_measurement:'visible DOM Range rectangles grouped by vertical overlap',
        authored_break_count:n.querySelectorAll('br').length,
        nowrap_width_px:nowrapWidth(nodes),available_width_px:Math.max(0,wrapping.clientWidth-parseFloat(wrappingStyle.paddingLeft)-parseFloat(wrappingStyle.paddingRight)),
        available_width_selector:selector(wrapping),available_width_rect:rect(wrapping),
        width_basis:'CSS pixels before transforms; flattened visible text runs with authored breaks replaced by spaces',
        transform:s.transform,contrast:contrast(first?.parentElement||n)};
    };
    const titleSet=new Set();
    if(contract?.title_element!==undefined){const n=document.querySelector(`[data-template-element="${contract.title_element}"]`);if(n)titleSet.add(n);}
    if(!titleSet.size)for(const n of document.querySelectorAll('[data-ppt-role="title"],h1'))titleSet.add(n);
    const titleNodes=[...titleSet].filter(visible),nodes=new Set(titleNodes);
    for(const n of document.querySelectorAll('[data-source-block],[data-ppt-slot],[data-agenda-item],[data-metadata],[data-section-number],h1,h2,h3'))if(visible(n))nodes.add(n);
    const allNodes=[...nodes],measured=allNodes.slice(0,300).map(n=>({node:n,value:textMeasurement(n)}));
    const titles=measured.filter(({node})=>titleSet.has(node)).map(({value})=>value);
    const safeArea=()=>({status:'unmeasured',reason:'外接矩形不是内部文字安全区；未求解真实绘制轮廓、裁切、滤镜或遮挡'});
    const clipKind=value=>({kind:value==='none'?'none':value.includes('url(')?'reference':value.split('(')[0].trim().replace(/-/g,'_')||'unknown'});
    const clippingContext=n=>{const result=[];for(let p=n;p instanceof Element;p=p.parentElement){const st=getComputedStyle(p);
      if(st.clipPath!=='none'||st.maskImage!=='none'||st.overflowX!=='visible'||st.overflowY!=='visible')result.push({selector:selector(p),clip_path:clipKind(st.clipPath),mask_present:st.maskImage!=='none',overflow_x:st.overflowX,overflow_y:st.overflowY});
    }return result;};
    const relatedText=(n,bounds)=>{const related=[];for(const {node,value} of measured){
      const overlap=bounds&&value.text_bounds&&intersect(bounds,value.text_bounds),text=value.text_bounds;
      const contains=!!(bounds&&text&&text.x>=bounds.x&&text.y>=bounds.y&&text.x+text.width<=bounds.x+bounds.width&&text.y+text.height<=bounds.y+bounds.height);
      const slot=n.closest('[data-ppt-slot]'),sharedSlot=slot&&slot===node.closest('[data-ppt-slot]');
      const parent=n.parentElement,sharedContainer=parent&&!parent.matches('body,html,.ppt-slide,[data-enterprise-body]')&&parent.contains(node);
      const descendant=n.contains(node),label=(n.getAttribute('aria-labelledby')||'').split(/\s+/).some(id=>id&&document.getElementById(id)===node);
      if(overlap||sharedSlot||sharedContainer||descendant||label)related.push({selector:value.selector,source_id:value.source_id,slot_id:value.slot_id,text:value.text.slice(0,160),text_anchor_truncated:value.text.length>160,
        bounds:text,nowrap_width_px:value.nowrap_width_px,available_width_px:value.available_width_px,line_count:value.line_count,
        relation:label?'aria_labelledby':sharedSlot?'shared_slot':descendant?'descendant':sharedContainer?'shared_container':'viewport_overlap',
        geometry_relation:!bounds||!text?'unmeasured':contains?'contains':overlap?'overlap':'disjoint',
        distance_px:bounds&&text?Math.hypot(Math.max(bounds.x-text.x-text.width,text.x-bounds.x-bounds.width,0),Math.max(bounds.y-text.y-text.height,text.y-bounds.y-bounds.height,0)):null,
        relation_is_ownership_proof:!!(label||sharedSlot)});
    }return {related_text:related.slice(0,24),related_text_total:related.length,related_text_truncated:related.length>24};};
    // Record paint kinds, never image URLs/data URIs or complete gradient/path data.
    const cssPaint=st=>{const color=rgb(st.backgroundColor),borders=['top','right','bottom','left'].map(side=>({side,width_px:parseFloat(st.getPropertyValue(`border-${side}-width`)),style:st.getPropertyValue(`border-${side}-style`),color:st.getPropertyValue(`border-${side}-color`)})).filter(b=>b.width_px>0&&!['none','hidden'].includes(b.style)&&(rgb(b.color)?.[3]??1)>0);
      const kinds=[...new Set([...st.backgroundImage.matchAll(/((?:repeating-)?(?:linear|radial|conic))-gradient\(/g)].map(m=>m[1].replace(/-/g,'_')+'_gradient'))];
      if(st.backgroundImage!=='none'&&(/url\(|image-set\(/.test(st.backgroundImage)||!kinds.length))kinds.push('image');
      return {background_color:st.backgroundColor,has_background_fill:color?color[3]>0:!['transparent',''].includes(st.backgroundColor),background_image_kinds:kinds,borders,
        border_radius:st.borderRadius,has_box_shadow:st.boxShadow!=='none',has_outline:st.outlineStyle!=='none'&&parseFloat(st.outlineWidth)>0};};
    const hasPaint=p=>p.has_background_fill||p.background_image_kinds.length||p.borders.length||p.has_box_shadow||p.has_outline;
    const cssCandidates=[...document.querySelectorAll('body,body *')].filter(n=>n instanceof HTMLElement&&!n.matches('style,script,link,meta')&&visible(n));
    const artwork=[];let cssElementsScanned=0;
    for(const n of cssCandidates.slice(0,1500)){cssElementsScanned++;const st=getComputedStyle(n),paint=cssPaint(st),host=rect(n);
      if(hasPaint(paint)&&host.width>0&&host.height>0){
        // Container surfaces remain visible evidence but do not masquerade as
        // foreground mass. This is a DOM classification, not an aesthetic judgment.
        const surface=n.matches('body,.ppt-slide,[data-enterprise-body]')||textNodes(n).some(t=>t.textContent.trim())||[...n.children].some(visible);
        artwork.push({node:n,value:{...identity(n),kind:'element',geometry_bounds:host,bounds_status:'border_box_estimate',bounds_role:surface?'background_surface':'foreground',
          role_basis:surface?'DOM_heuristic_root_or_container_with_visible_content':'DOM_heuristic_painted_element_without_visible_child_content',role_is_semantic_proof:false,paint,clip_path:clipKind(st.clipPath),mask_present:st.maskImage!=='none',filter_present:st.filter!=='none',transform:st.transform,
          clipping_context:clippingContext(n),internal_safe_area:safeArea(),...relatedText(n,host),
          bounds_limitations:['边框外接框不等于实际绘制像素；未求解裁切、圆角、阴影、滤镜、祖先裁切及遮挡']}});
      }
      for(const pseudo of ['::before','::after']){const ps=getComputedStyle(n,pseudo);if(['none','normal'].includes(ps.content)||ps.display==='none'||ps.visibility!=='visible'||Number(ps.opacity)===0)continue;
        const paint=cssPaint(ps),hasText=!['""',"''"].includes(ps.content);if(!hasPaint(paint)&&!hasText)continue;
        artwork.push({node:n,value:{...identity(n),selector:selector(n)+pseudo,host_selector:selector(n),kind:'pseudo',pseudo,host_rect:host,geometry_bounds:null,
          bounds_status:'unmeasured_pseudo_geometry',bounds_role:'unmeasured',paint,has_generated_content:hasText,
          computed_geometry:{position:ps.position,width:ps.width,height:ps.height,left:ps.left,right:ps.right,top:ps.top,bottom:ps.bottom,box_sizing:ps.boxSizing},
          clip_path:clipKind(ps.clipPath),mask_present:ps.maskImage!=='none',filter_present:ps.filter!=='none',transform:ps.transform,
          clipping_context:clippingContext(n),internal_safe_area:safeArea(),...relatedText(n,null),
          bounds_limitations:['浏览器没有伪元素getBoundingClientRect；仅记录计算样式和宿主矩形，未把宿主冒充伪元素实际边界']}});
      }
    }
    const cssArtwork=artwork.slice(0,200).map(item=>item.value);
    const svgNodes=[...document.querySelectorAll('svg')].filter(n=>visible(n)&&n.getBoundingClientRect().width>0&&n.getBoundingClientRect().height>0);
    const projected=n=>{try{const b=n.getBBox(),m=n.getScreenCTM();if(!m)return null;const points=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(m));return union(points.map(p=>({x:p.x,y:p.y,width:0,height:0})));}catch{return null;}};
    const screenMatrix=n=>{const m=n.getScreenCTM?.();return m?{a:m.a,b:m.b,c:m.c,d:m.d,e:m.e,f:m.f}:null;};
    const svgs=svgNodes.slice(0,80).map(n=>{
      const viewport=rect(n),s=getComputedStyle(n),view=n.viewBox?.baseVal;
      const groupNodes=[...n.querySelectorAll('g,svg')].filter(p=>visible(p)&&!p.closest('defs,clipPath,mask'));
      const groups=groupNodes.map(p=>({...identity(p),tag:p.tagName,geometry_bounds:projected(p),transform:getComputedStyle(p).transform,screen_transform:screenMatrix(p),clipping_context:clippingContext(p)}));
      const shapes=[...n.querySelectorAll('path,circle,ellipse,rect,line,polyline,polygon,text,use,image')].filter(p=>visible(p)&&!p.closest('defs,clipPath,mask')).map(p=>{
        const st=getComputedStyle(p),paint=value=>value.includes('url(')?'paint_server':value;return {...identity(p),tag:p.tagName,geometry_bounds:projected(p),fill:paint(st.fill),stroke:paint(st.stroke),stroke_width:st.strokeWidth,
          transform:st.transform,screen_transform:screenMatrix(p),group_ancestors:groupNodes.filter(g=>g!==p&&g.contains(p)).map(selector),
          clip_path_present:st.clipPath!=='none',mask_present:st.maskImage!=='none',clipping_context:clippingContext(p)};
      }).filter(shape=>shape.geometry_bounds);
      const geometry=union(shapes.filter(shape=>shape.fill!=='none'||shape.stroke!=='none').map(shape=>shape.geometry_bounds));
      return {...identity(n),view_box:n.hasAttribute('viewBox')?{x:view.x,y:view.y,width:view.width,height:view.height}:null,
        viewport_rect:viewport,geometry_bounds:geometry,visible_bounds:geometry&&s.overflow!=='visible'?intersect(geometry,viewport):geometry,
        overflow:s.overflow,clip_path_present:s.clipPath!=='none',mask_present:s.maskImage!=='none',
        bounds_basis:'visible SVG primitive getBBox projected into viewport; overflow clips to SVG viewport',
        bounds_status:'geometry_envelope_estimate',clipping_context:clippingContext(n),
        bounds_limitations:['几何bounds不含描边扩张、滤镜、mask/clipPath内部裁剪和其他元素遮挡；不是文字安全区'],
        internal_safe_area:safeArea(),groups:groups.slice(0,80),groups_truncated:groups.length>80,
        svg_text_measurement:{status:'unsupported_by_model_html_contract',count:n.querySelectorAll('text,foreignObject').length},
        shapes:shapes.slice(0,80),shapes_truncated:shapes.length>80,...relatedText(n,viewport)};
    });
    const bodyNode=document.querySelector('[data-enterprise-body]');let body=null;
    if(bodyNode){const textBounds=union(lineRects(textNodes(bodyNode))),imageBounds=[...bodyNode.querySelectorAll('img')].filter(visible).map(rect);
      const svgBounds=svgs.filter((_,i)=>bodyNode.contains(svgNodes[i])).map(s=>s.visible_bounds).filter(Boolean);
      const bodyArtwork=artwork.filter(({node})=>bodyNode.contains(node)).map(({value})=>value),cssBounds=union(bodyArtwork.filter(a=>a.bounds_role==='foreground').map(a=>a.geometry_bounds).filter(Boolean)),surfaceBounds=union(bodyArtwork.filter(a=>a.bounds_role==='background_surface').map(a=>a.geometry_bounds).filter(Boolean));
      const bounds=union([textBounds,...imageBounds,...svgBounds,cssBounds].filter(Boolean)),container=rect(bodyNode),frame=contract?.body_frame||container;
      const gaps=r=>r?{left_px:r.x-frame.x,right_px:frame.x+frame.width-r.x-r.width,top_px:r.y-frame.y,bottom_px:frame.y+frame.height-r.y-r.height}:null;
      body={frame,frame_source:contract?.body_frame?'contract':'body_container',container_rect:container,
        text_content_bounds:textBounds,content_bounds:bounds,text_gaps:gaps(textBounds),content_gaps:gaps(bounds),
        foreground_bounds:bounds,css_artwork_bounds:cssBounds,background_surface_bounds:surfaceBounds,all_surface_bounds:union([bounds,surfaceBounds].filter(Boolean)),
        measurement_completeness:{css_elements_scanned:cssElementsScanned,css_elements_total:cssCandidates.length,css_scan_truncated:cssCandidates.length>1500,css_detail_truncated:artwork.length>200,svg_scan_truncated:svgNodes.length>80,
          pseudo_bounds_unmeasured:bodyArtwork.filter(a=>a.kind==='pseudo').length,painted_pixels:'unmeasured',visual_weight_or_centroid:'unmeasured'},
        left_gap_px:bounds?bounds.x-frame.x:null,right_gap_px:bounds?frame.x+frame.width-bounds.x-bounds.width:null,
        canvas_gaps:bounds?{left_px:bounds.x,right_px:canvas.width-bounds.x-bounds.width}:null,
        content_bounds_basis:'visible DOM text ranges, image rectangles, SVG geometry and standalone CSS artwork border boxes; excludes container surfaces and unmeasured pseudo geometry; not pixel coverage or visual centroid'};
    }
    return {schema_version:1,titles,text_blocks:measured.filter(({node})=>!titleSet.has(node)).map(({value})=>value),body,svg:svgs,css_artwork:cssArtwork,
      truncated:{text_blocks:allNodes.length>300,svg:svgNodes.length>80,css_scan:cssCandidates.length>1500,css_artwork:artwork.length>200},
      limitations:['仅提供实测描述；字号、换行、留白和颜色是否恰当由视觉证据与页面任务判断，不自动判fail或修改模板保护']};
  },{canvas,contract});
}

// Validation only: record real computed geometry/styles, never repair a layout.
export async function inspectModelContract(page, canvas, contract, reference) {
  return page.evaluate(({canvas,contract,reference})=>{
    const issues=[],elements={},sources=[];
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
    for(const n of document.querySelectorAll('[data-source-block],[data-agenda-item],[data-metadata]')){
      const s=getComputedStyle(n),range=document.createRange();range.selectNodeContents(n);
      const lines=new Set([...range.getClientRects()].filter(r=>r.width&&r.height).map(r=>Math.round(r.y)));
      sources.push({id:n.dataset.sourceBlock||n.dataset.agendaItem||n.dataset.metadata,rect:rect(n),
        inside_body:!!n.closest('[data-enterprise-body]'),font:s.fontFamily,font_size:parseFloat(s.fontSize),
        line_count:lines.size,text:n.textContent.slice(0,500)});
    }
    if(!contract)return {issues,elements,sources};
    const root=document.querySelector('.ppt-slide');
    if(!root||Math.abs(root.getBoundingClientRect().width-canvas.width)>1||Math.abs(root.getBoundingClientRect().height-canvas.height)>1||Math.abs(root.getBoundingClientRect().x)>1||Math.abs(root.getBoundingClientRect().y)>1)issues.push({type:'wrong_canvas'});
    const near=(a,b)=>a&&b&&Object.keys(b).every(k=>typeof b[k]==='number'?Math.abs(a[k]-b[k])<=1:a[k]===b[k]);
    for(const id of [...contract.protected_elements,...contract.text_frames]){
      const a=elements[id],b=reference?.elements?.[id];
      if(!a&&contract.text_frames.includes(id)&&id!==contract.title_element)continue;
      const node=document.querySelector(`[data-template-element="${id}"]`);
      if(contract.text_frames.includes(id)&&id!==contract.title_element&&!node?.textContent.trim())continue;
      const approvedTitle=contract.body_frame&&id===contract.title_element&&contract.text_frames.includes(id)&&contract.title_frame;
      const expected=approvedTitle?contract.title_frame:b?.rect;
      if(!a||!b||!near(a.rect,expected))issues.push({type:'template_geometry_changed',element:id,
        selector:`[data-template-element="${id}"]`,expected_bounds:expected??null,actual_bounds:a?.rect??null,
        fix_hint:approvedTitle?'恢复已批准title_frame外框的x/y/width/height，以expected_bounds为准；采用合同已批准的标题宽度，检查CSS和父级变换。外框内调整字号、行距和换行，保留完整原文。':'恢复模板原外框的x/y/width/height，检查覆盖它的CSS和父级变换。文字框内可调整字号、行距和换行，不得扩大外框；保留完整原文。'});
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
    return {issues,elements,sources};
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
      reports.at(-1).sources=contractReport.sources;
      reports.at(-1).layout_measurements=await measureLayoutEvidence(page,plan.canvas,p.model_html?p.template_contract:null);
      reports.at(-1).slide_id=p.slide_id;
      reports.at(-1).html_sha256=createHash('sha256').update(p.html).digest('hex');
      await page.screenshot({path:path.join(folder,'previews',`${i+1}.png`)});
      reports.at(-1).screenshot_sha=createHash('sha256').update(await fs.readFile(path.join(folder,'previews',`${i+1}.png`))).digest('hex');
      if(probeOnly)continue;
      if(report.issues.length&&!plan.html_only)throw new Error(`第${i+1}页未通过排版检查`);
      const renderedHTML=await page.content();
      await fs.writeFile(path.join(folder,'pages',`${i+1}.html`),renderedHTML);
      frames.push(`<iframe title="第${i+1}页" sandbox srcdoc="${esc(renderedHTML)}"></iframe>`);
      if(plan.html_only)continue;
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
    if(!plan.html_only)await pptx.writeFile({fileName:path.join(folder,'presentation.pptx')});
    await fs.writeFile(path.join(folder,'presentation.html'),`<!doctype html><meta charset="utf-8"><title>${esc(plan.title)}</title><style>body{margin:0;padding:24px;background:#222;display:grid;justify-content:center;gap:24px}iframe{border:0;width:${width}px;height:${height}px}@media print{body{padding:0;display:block}iframe{display:block;break-after:page}}@page{size:${width/96}in ${height/96}in;margin:0}</style>${frames.join('')}`);
    await fs.writeFile(path.join(folder,'outline.json'),JSON.stringify({title:plan.title,pages:plan.pages.map(({html,...p})=>p)},null,2));
    await fs.writeFile(path.join(folder,'report.json'),JSON.stringify({passed:reports.every(p=>!p.issues.length),page_count:plan.pages.length,repair_count:0,
      checks:{source_coverage:'validated_by_agent',source_coverage_mode:'literal_on_slides',fixed_template_structure:reports.every(p=>!p.issues.length),browser_render:true,
        editable_text:!plan.html_only,native_charts:plan.html_only?0:plan.pages.reduce((n,p)=>n+(p.enterprise_charts?.length||0),0),decorations_rasterized:!plan.html_only,transformed_text_rasterized:!plan.html_only,pptx_application_render:plan.html_only?'not_exported':'pending'},
      limitations:['复杂装饰与旋转文字以图像保留；普通文字可编辑','未在PowerPoint应用内验证；缺少企业字体时可能发生替换']},null,2));
  }finally{await browser.close();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)await renderEnterprise(path.resolve(process.argv[2]),process.argv[3]==='probe');
