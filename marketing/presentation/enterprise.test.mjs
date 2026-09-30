import assert from 'node:assert/strict';
import test from 'node:test';
import {chromium} from 'playwright';
import {inspectModelContract,measureEnterprise,renderEnterprise} from './enterprise.mjs';
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
