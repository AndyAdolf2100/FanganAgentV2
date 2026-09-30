import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from 'playwright';
import {inspectModelContract,measureEnterprise,measureLayoutEvidence,renderEnterprise} from './enterprise.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
test('v4 renderer loads template reference for protected elements', async()=>{
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'enterprise-v4-test-'));
 try{
  const reference=path.join(folder,'template-reference');await fs.mkdir(reference);
  const html='<html><body style="margin:0"><div class="ppt-slide" style="position:relative;width:1200px;height:675px"><div data-template-element="0" style="position:absolute;left:40px;top:20px;width:200px;height:60px;font-size:24px">品牌</div></div></body></html>';
  const plan={title:'test',canvas:{width:1200,height:675},pages:[{html}]};
  await fs.writeFile(path.join(reference,'plan.json'),JSON.stringify(plan));
  await renderEnterprise(reference,true);
  plan.enterprise_layout_mode='enterprise-model-html-v4';
  plan.pages[0]={html,model_html:true,template_page:0,template_contract:{protected_elements:[0],text_frames:[]}};
  await fs.writeFile(path.join(folder,'plan.json'),JSON.stringify(plan));
  await renderEnterprise(folder,true);
  const report=JSON.parse(await fs.readFile(path.join(folder,'enterprise-probe.json'),'utf8'));
  assert.deepEqual(report.pages[0].issues,[]);
 }finally{await fs.rm(folder,{recursive:true,force:true});}
});
test('enterprise model HTML preserves computed brand geometry and rejects hidden/out-of-bounds sources', async()=>{
const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
try{
 const page=await browser.newPage({viewport:{width:1200,height:675}});
 const canvas={width:1200,height:675};
 const doc='<html><head><style>body{margin:0}</style></head><body><div class="ppt-slide" style="width:1200px;height:675px;position:relative"><div data-template-element="0" style="position:absolute;left:40px;top:20px;color:#123456">品牌</div><div data-enterprise-body style="position:absolute;left:60px;top:140px;width:1080px;height:450px"><p data-source-block="b1" data-ppt-slot="b1" style="font-size:24px">预算30万元。</p></div></div></body></html>';
 await page.setContent(doc);
 const baseline=await inspectModelContract(page,canvas,null,null);
 const contract={protected_elements:[0],text_frames:[],body_frame:{x:60,y:140,width:1080,height:460}};
 assert.deepEqual((await inspectModelContract(page,canvas,contract,baseline)).issues,[]);
 assert.deepEqual((await measureEnterprise(page,canvas,true)).issues,[]);
 const brand=page.locator('[data-template-element="0"]');
 await brand.evaluate(n=>n.style.width='560px');
 const geometry=(await inspectModelContract(page,canvas,contract,baseline)).issues.find(p=>p.type==='template_geometry_changed');
 assert.deepEqual(geometry.expected_bounds,baseline.elements[0].rect);
 assert.equal(geometry.actual_bounds.width,560);
 assert.equal(geometry.selector,'[data-template-element="0"]');
 assert.ok(geometry.fix_hint.includes('不得扩大外框'));
 await brand.evaluate(n=>n.style.removeProperty('width'));
 await page.evaluate(()=>{const n=document.createElement('div');n.id='occluder';n.style='position:absolute;left:0;top:0;width:1200px;height:675px;background:white;z-index:99';document.body.append(n);});
 assert.ok((await inspectModelContract(page,canvas,contract,baseline)).issues.some(p=>p.type==='covered_text'));
 await page.locator('#occluder').evaluate(n=>n.remove());
 await page.addStyleTag({content:'[data-template-element]{color:red!important}'});
 assert.ok((await inspectModelContract(page,canvas,contract,baseline)).issues.some(p=>p.type==='protected_style_changed'));
 await page.locator('[data-source-block]').evaluate(n=>n.style.opacity='0');
 assert.ok((await measureEnterprise(page,canvas,true)).issues.some(p=>p.type==='invisible_text'));
 await page.locator('[data-enterprise-body]').evaluate(n=>n.style.left='-50px');
 assert.ok((await inspectModelContract(page,canvas,contract,baseline)).issues.some(p=>p.type==='body_outside_contract'));

}finally{await browser.close();}

});

test('v5 evidence binds HTML, screenshot and source geometry; failed draft exports HTML only', async()=>{
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'enterprise-v5-evidence-'));
 try{
  const html='<html><body style="margin:0"><div class="ppt-slide" style="position:relative;width:1200px;height:675px"><div data-enterprise-body style="position:absolute;left:60px;top:140px;width:1080px;height:460px"><p data-source-block="b1" style="font-size:8px">测试来源预算30万元。</p></div></div></body></html>';
  const plan={title:'evidence fixture',canvas:{width:1200,height:675},html_only:true,source_blocks:[],pages:[{html,slide_id:'group-0000-part-0000',model_html:true}]};
  await fs.mkdir(path.join(folder,'template-reference'));
  await fs.writeFile(path.join(folder,'template-reference','enterprise-probe.json'),JSON.stringify({pages:[]}));
  await fs.writeFile(path.join(folder,'plan.json'),JSON.stringify(plan));
  await renderEnterprise(folder);
  const probe=JSON.parse(await fs.readFile(path.join(folder,'enterprise-probe.json'),'utf8'));
  const report=JSON.parse(await fs.readFile(path.join(folder,'report.json'),'utf8'));
  assert.equal(probe.pages[0].slide_id,plan.pages[0].slide_id);
  assert.match(probe.pages[0].html_sha256,/^[a-f0-9]{64}$/);
  assert.match(probe.pages[0].screenshot_sha,/^[a-f0-9]{64}$/);
  assert.equal(probe.pages[0].sources[0].id,'b1');
  assert.equal(probe.pages[0].sources[0].inside_body,true);
  assert.equal(probe.pages[0].sources[0].font_size,8);
  assert.equal(probe.pages[0].layout_measurements.schema_version,1);
  assert.ok(probe.pages[0].layout_measurements.body.text_content_bounds);
  assert.ok(probe.pages[0].issues.length);
  assert.equal(report.passed,false);
  assert.equal(report.checks.editable_text,false);
  await fs.access(path.join(folder,'presentation.html'));
  await assert.rejects(fs.access(path.join(folder,'presentation.pptx')));
 }finally{await fs.rm(folder,{recursive:true,force:true});}
});

test('layout evidence measures authored title wrapping and real typography without changing the DOM', async()=>{
 const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:675}});
  await page.setContent(`<html><body style="margin:0;background:white"><div class="ppt-slide" style="position:relative;width:1200px;height:675px">
   <div id="title" data-template-element="0" style="position:absolute;left:40px;top:20px;width:428px;height:90px;font:18px Arial;color:#777;background:#fff">
    <span style="font:700 32px/38px Arial;white-space:normal">Quarterly<br>Update</span></div>
   <div data-enterprise-body style="position:absolute;left:60px;top:140px;width:1080px;height:460px">
    <div data-source-block="b1" style="position:absolute;left:370px;top:10px;width:300px;font:24px/30px Arial"><span data-ppt-slot="inline-label">Body content</span></div></div>
  </div></body></html>`);
  await page.evaluate(()=>document.fonts.ready);
  const before=await page.content(),contract={title_element:0,body_frame:{x:60,y:140,width:1080,height:460}};
  const evidence=await measureLayoutEvidence(page,{width:1200,height:675},contract);
  const title=evidence.titles[0];
  assert.equal(title.font_size_px,32);
  assert.equal(title.font_weight,'700');
  assert.equal(title.line_height,'38px');
  assert.equal(title.white_space,'normal');
  assert.equal(title.line_count,2);
  assert.equal(title.authored_break_count,1);
  assert.equal(title.available_width_px,428);
  assert.ok(title.nowrap_width_px>200&&title.nowrap_width_px<428);
  assert.ok(title.contrast.ratio>4&&title.contrast.ratio<5);
  assert.equal(title.contrast.basis,'computed_solid_ancestor_estimate');
  assert.ok(evidence.body.text_gaps.left_px>350);
  assert.ok(evidence.body.canvas_gaps.left_px>400);
  const inline=evidence.text_blocks.find(n=>n.slot_id==='inline-label');
  assert.equal(inline.available_width_px,300);
  assert.equal(inline.available_width_selector,'[data-source-block="b1"]');
  assert.equal(evidence.body.content_gaps.left_px,evidence.body.left_gap_px);
  assert.equal(await page.content(),before);
  assert.equal('issues' in evidence,false);
  await page.locator('.ppt-slide').evaluate(n=>n.style.opacity='.5');
  assert.equal((await measureLayoutEvidence(page,{width:1200,height:675},contract)).titles[0].contrast.ratio,null);
 }finally{await browser.close();}
});

test('short-label and SVG measurements distinguish viewport from circle safe area', async()=>{
 const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:675}});
  await page.setContent(`<html><body style="margin:0;background:white"><div class="ppt-slide" style="position:relative;width:1200px;height:675px">
   <div data-enterprise-body style="position:absolute;left:60px;top:120px;width:1080px;height:460px">
    <div id="badge" style="position:absolute;left:100px;top:30px;width:120px;height:120px">
     <svg id="circle" viewBox="0 0 120 120" width="120" height="120" style="position:absolute;overflow:hidden"><circle cx="60" cy="60" r="40" fill="#0055aa"/></svg>
     <div id="label" data-ppt-slot="time-label" style="position:absolute;left:40px;top:35px;width:32px;font:24px/26px Arial;color:white;word-break:break-all">W1–3</div>
    </div>
    <div data-source-block="b2" style="position:absolute;left:500px;top:30px;width:240px;font:24px Arial;background:linear-gradient(white,black)">Complex background</div>
   </div></div></body></html>`);
  const evidence=await measureLayoutEvidence(page,{width:1200,height:675},{body_frame:{x:60,y:120,width:1080,height:460}});
  const label=evidence.text_blocks.find(n=>n.slot_id==='time-label');
  assert.ok(label.line_count>1);
  assert.ok(label.nowrap_width_px>label.available_width_px);
  const svg=evidence.svg.find(n=>n.selector==='#circle');
  assert.deepEqual(svg.view_box,{x:0,y:0,width:120,height:120});
  assert.deepEqual(svg.viewport_rect,{x:160,y:150,width:120,height:120});
  assert.deepEqual(svg.geometry_bounds,{x:180,y:170,width:80,height:80});
  assert.equal(svg.internal_safe_area.status,'unmeasured');
  assert.ok(svg.related_text.some(n=>n.slot_id==='time-label'&&n.text==='W1–3'));
  assert.equal(svg.shapes[0].tag,'circle');
  const complex=evidence.text_blocks.find(n=>n.source_id==='b2');
  assert.equal(complex.contrast.ratio,null);
  assert.equal(complex.contrast.basis,'unmeasured_complex_or_transparent_background');
  assert.ok(!JSON.stringify(evidence).includes('data:image'));
  assert.ok(!JSON.stringify(evidence).includes('linear-gradient'));
  assert.equal('issues' in evidence,false);
 }finally{await browser.close();}
});

test('body title_frame widens only the authorized title geometry and still measures text overflow', async()=>{
 const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:675}}),canvas={width:1200,height:675};
  await page.setContent(`<html><body style="margin:0"><div class="ppt-slide" style="position:relative;width:1200px;height:675px">
   <div data-template-element="0" style="position:absolute;left:40px;top:20px;width:428px;height:60px;font:32px/40px Arial;white-space:nowrap">Business plan and delivery</div>
   <div data-template-element="1" style="position:absolute;left:1050px;top:20px;width:100px;height:50px">BRAND</div>
   <div data-enterprise-body style="position:absolute;left:60px;top:140px;width:1080px;height:460px"></div>
  </div></body></html>`);
  const baseline=await inspectModelContract(page,canvas,null,null);
  const base={protected_elements:[1],text_frames:[0],title_element:0,body_frame:{x:60,y:140,width:1080,height:460}};
  await page.locator('[data-template-element="0"]').evaluate(n=>n.style.width='900px');
  assert.ok((await inspectModelContract(page,canvas,base,baseline)).issues.some(i=>i.type==='template_geometry_changed'&&i.element===0));
  const widened={...base,title_frame:{x:40,y:20,width:900,height:60}};
  assert.deepEqual((await inspectModelContract(page,canvas,widened,baseline)).issues,[]);
  assert.deepEqual((await measureEnterprise(page,canvas,true)).issues,[]);
  await page.locator('[data-template-element="0"]').evaluate(n=>n.style.width='428px');
  const approvedTitle=(await inspectModelContract(page,canvas,widened,baseline)).issues.find(i=>i.type==='template_geometry_changed'&&i.element===0);
  assert.equal(approvedTitle.expected_bounds.width,900);
  assert.ok(approvedTitle.fix_hint.includes('已批准title_frame'));
  assert.ok(approvedTitle.fix_hint.includes('expected_bounds'));
  assert.ok(!approvedTitle.fix_hint.includes('恢复模板原外框'));
  await page.locator('[data-template-element="0"]').evaluate(n=>n.style.width='900px');
  await page.locator('[data-template-element="0"]').evaluate(n=>n.textContent='Very long '.repeat(30));
  assert.ok((await measureEnterprise(page,canvas,true)).issues.some(i=>i.type==='out_of_template_text_frame'));
  await page.locator('[data-template-element="1"]').evaluate(n=>n.style.width='130px');
  const other=(await inspectModelContract(page,canvas,widened,baseline)).issues.find(i=>i.type==='template_geometry_changed'&&i.element===1);
  assert.equal(other.expected_bounds.width,100);
  const fixed={...widened,body_frame:null};
  const fixedTitle=(await inspectModelContract(page,canvas,fixed,baseline)).issues.find(i=>i.type==='template_geometry_changed'&&i.element===0);
  assert.equal(fixedTitle.expected_bounds.width,428);
 }finally{await browser.close();}
});

test('generic CSS artwork separates foreground geometry from container surfaces and records uncertain paint', async()=>{
 const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:675}});
  await page.setContent(`<html><body style="margin:0;background:white"><div class="ppt-slide" style="width:1200px;height:675px;position:relative">
   <div id="body" data-enterprise-body style="position:absolute;left:60px;top:120px;width:1080px;height:460px;background:#eee">
    <div id="panel" style="position:absolute;left:0;top:0;width:1080px;height:460px;background:#ddd">
     <p id="copy" data-source-block="copy" style="position:absolute;left:400px;top:100px;margin:0;width:200px;font:24px/30px Arial">Body text</p>
    </div>
    <div id="polygon" aria-labelledby="outside-label" style="position:absolute;left:80px;top:40px;width:160px;height:100px;background:linear-gradient(45deg,red,blue);clip-path:polygon(0 0,100% 30%,70% 100%,20% 80%)"></div>
    <div id="outside-label" data-ppt-slot="outside" style="position:absolute;left:70px;top:165px;font:24px Arial">External label</div>
    <div id="ring" style="position:absolute;left:300px;top:20px;width:80px;height:80px;border:6px dashed #008080;border-radius:40%;transform:rotate(15deg)"></div>
    <div id="radial" style="position:absolute;left:650px;top:30px;width:70px;height:90px;background:radial-gradient(white,green);mask-image:linear-gradient(black,transparent);box-shadow:4px 4px 8px #555"></div>
    <div id="private-image" style="position:absolute;left:750px;top:30px;width:30px;height:30px;background-image:url(data:image/png;base64,PRIVATE_MARKER)"></div>
   </div></div></body></html>`);
  const before=await page.content(),e=await measureLayoutEvidence(page,{width:1200,height:675});
  const polygon=e.css_artwork.find(n=>n.selector==='#polygon');
  assert.equal(polygon.bounds_role,'foreground');
  assert.equal(polygon.role_is_semantic_proof,false);
  assert.deepEqual(polygon.geometry_bounds,{x:140,y:160,width:160,height:100});
  assert.deepEqual(polygon.paint.background_image_kinds,['linear_gradient']);
  assert.equal(polygon.clip_path.kind,'polygon');
  assert.equal(polygon.internal_safe_area.status,'unmeasured');
  const external=polygon.related_text.find(n=>n.selector==='#outside-label');
  assert.equal(external.relation,'aria_labelledby');
  assert.equal(external.geometry_relation,'disjoint');
  assert.ok(external.distance_px>0);
  assert.equal(e.css_artwork.find(n=>n.selector==='#ring').paint.borders.length,4);
  assert.equal(e.css_artwork.find(n=>n.selector==='#radial').mask_present,true);
  assert.deepEqual(e.css_artwork.find(n=>n.selector==='#radial').paint.background_image_kinds,['radial_gradient']);
  assert.equal(e.css_artwork.find(n=>n.selector==='#panel').bounds_role,'background_surface');
  assert.deepEqual(e.body.background_surface_bounds,{x:60,y:120,width:1080,height:460});
  assert.ok(e.body.content_bounds.x>60);
  assert.ok(e.body.content_bounds.width<1080);
  assert.deepEqual(e.body.content_bounds,e.body.foreground_bounds);
  assert.deepEqual(e.body.all_surface_bounds,e.body.background_surface_bounds);
  assert.equal(e.body.measurement_completeness.visual_weight_or_centroid,'unmeasured');
  assert.equal(e.body.measurement_completeness.painted_pixels,'unmeasured');
  assert.equal(e.truncated.css_scan,false);
  assert.ok(!JSON.stringify(e).includes('PRIVATE_MARKER'));
  assert.ok(!JSON.stringify(e).includes('url('));
  assert.equal('issues' in e,false);
  assert.equal(await page.content(),before);
 }finally{await browser.close();}
});

test('pseudo artwork exposes computed geometry without inventing a rectangle or complete body coverage', async()=>{
 const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:675}});
  await page.setContent(`<html><head><style>
   #badge::before{content:"";position:absolute;left:-40px;top:5px;width:0;height:0;border-top:20px solid transparent;border-bottom:20px solid transparent;border-right:30px solid red;transform:rotate(10deg)}
   #badge::after{content:"Note";position:absolute;left:120px;top:0;width:100px;height:40px;background:conic-gradient(red,blue);clip-path:inset(2px round 5px)}
  </style></head><body style="margin:0"><div data-enterprise-body style="position:absolute;left:60px;top:120px;width:1080px;height:460px">
   <div id="badge" data-ppt-slot="badge" style="position:absolute;left:100px;top:60px;width:100px;height:50px;font:24px Arial">Badge</div>
  </div></body></html>`);
  const before=await page.content(),e=await measureLayoutEvidence(page,{width:1200,height:675});
  const p=e.css_artwork.find(n=>n.selector==='#badge::before');
  assert.equal(p.geometry_bounds,null);
  assert.equal(p.bounds_status,'unmeasured_pseudo_geometry');
  assert.equal(p.computed_geometry.width,'0px');
  assert.equal(p.paint.borders.length,1);
  assert.deepEqual(p.host_rect,{x:160,y:180,width:100,height:50});
  assert.equal(p.internal_safe_area.status,'unmeasured');
  assert.ok(p.related_text.some(t=>t.slot_id==='badge'&&t.geometry_relation==='unmeasured'));
  const after=e.css_artwork.find(n=>n.selector==='#badge::after');
  assert.deepEqual(after.paint.background_image_kinds,['conic_gradient']);
  assert.equal(after.clip_path.kind,'inset');
  assert.equal(after.has_generated_content,true);
  assert.equal(e.body.measurement_completeness.pseudo_bounds_unmeasured,2);
  assert.equal(e.body.css_artwork_bounds,null);
  assert.equal(await page.content(),before);
 }finally{await browser.close();}
});

test('arbitrary SVG paths retain nested group transforms and clipping uncertainty', async()=>{
 const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:675}});
  await page.setContent(`<html><body style="margin:0"><div data-enterprise-body style="position:absolute;left:60px;top:120px;width:1080px;height:460px">
   <div id="compound" style="position:absolute;left:40px;top:20px;width:400px;height:300px">
    <svg id="art" width="300" height="200" viewBox="0 0 300 200" aria-labelledby="external">
     <defs><clipPath id="clip"><polygon points="0,0 80,0 80,40 0,30"/></clipPath><mask id="mask"><rect width="300" height="200" fill="white"/></mask><path id="symbol" d="M0 0 L10 0 L5 12Z"/></defs>
     <g id="outer" transform="translate(20 30)" clip-path="url(#clip)"><g id="inner" transform="scale(2)"><path id="free-path" d="M0 0 L40 10 L25 30 L5 20Z" fill="#090"/><line id="line" x1="0" y1="35" x2="35" y2="35" stroke="black" stroke-width="8"/></g></g>
     <g id="rotated" transform="translate(150 80) rotate(30)" mask="url(#mask)"><use href="#symbol" fill="#f90"/></g>
    </svg>
    <div id="external" data-ppt-slot="external" style="position:absolute;left:0;top:220px;font:24px Arial">Below the diagram</div>
   </div></div></body></html>`);
  const before=await page.content(),e=await measureLayoutEvidence(page,{width:1200,height:675}),svg=e.svg.find(n=>n.selector==='#art');
  const free=svg.shapes.find(n=>n.selector==='#free-path');
  assert.deepEqual(free.geometry_bounds,{x:120,y:170,width:80,height:60});
  assert.deepEqual(free.group_ancestors,['#outer','#inner']);
  assert.equal(free.screen_transform.a,2);
  assert.equal(free.screen_transform.e,120);
  assert.ok(free.clipping_context.some(n=>n.selector==='#outer'&&n.clip_path.kind==='reference'));
  assert.ok(svg.groups.some(g=>g.selector==='#rotated'&&Math.abs(g.screen_transform.b-.5)<.001));
  assert.ok(svg.shapes.some(n=>n.tag==='use'));
  assert.ok(svg.shapes.some(n=>n.tag==='line'&&n.stroke_width==='8px'));
  assert.equal(svg.bounds_status,'geometry_envelope_estimate');
  assert.equal(svg.internal_safe_area.status,'unmeasured');
  assert.equal(svg.svg_text_measurement.status,'unsupported_by_model_html_contract');
  assert.equal(svg.svg_text_measurement.count,0);
  assert.ok(svg.related_text.some(t=>t.slot_id==='external'&&t.geometry_relation==='disjoint'&&t.relation==='aria_labelledby'));
  assert.ok(!JSON.stringify(e).includes('url('));
  assert.equal('issues' in e,false);
  assert.equal(await page.content(),before);
 }finally{await browser.close();}
});

test('graphics evidence reports bounded scan and detail truncation', async()=>{
 const browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:675}});
  await page.setContent('<html><body style="margin:0"><div data-enterprise-body style="width:1080px;height:460px">'+Array.from({length:1510},(_,i)=>`<i style="display:inline-block;width:1px;height:1px;background:red" data-index="${i}"></i>`).join('')+'</div></body></html>');
  const e=await measureLayoutEvidence(page,{width:1200,height:675});
  assert.equal(e.css_artwork.length,200);
  assert.equal(e.truncated.css_artwork,true);
  assert.equal(e.truncated.css_scan,true);
  assert.equal(e.body.measurement_completeness.css_elements_scanned,1500);
  assert.equal(e.body.measurement_completeness.css_scan_truncated,true);
  await page.setContent('<html><body style="margin:0"><div id="surface" data-enterprise-body style="width:1080px;height:460px;background:white">'+Array.from({length:30},(_,i)=>`<div data-source-block="s${i}">${'Long source text '.repeat(30)}</div>`).join('')+'</div></body></html>');
  const relations=(await measureLayoutEvidence(page,{width:1200,height:675})).css_artwork.find(n=>n.selector==='#surface');
  assert.equal(relations.related_text.length,24);
  assert.equal(relations.related_text_total,30);
  assert.equal(relations.related_text_truncated,true);
  assert.equal(relations.related_text[0].text.length,160);
  assert.equal(relations.related_text[0].text_anchor_truncated,true);
 }finally{await browser.close();}
});
