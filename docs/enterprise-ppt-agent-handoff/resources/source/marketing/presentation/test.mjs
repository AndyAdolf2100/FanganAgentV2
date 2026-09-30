import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { pageHTML, renderProbe, splitPage, verifyCoverage } from './render.mjs';

test('拆页保留长文本、数字、表格每一行，缺行必须失败',()=>{
  const source=[{id:'p',kind:'text',text:'预测而非保证。预算300万元。'.repeat(80)},
    {id:'t',kind:'table',header:['项目','金额'],rows:[['达人','120'],['投放','100'],['制作','30'],['渠道','25'],['机动','25']]}];
  let pages=source.map(b=>({title:'测试',blocks:[b]}));
  pages=pages.flatMap(splitPage).flatMap(splitPage);
  assert.equal(verifyCoverage(source,pages),true);
  const broken=structuredClone(pages);broken.find(p=>p.blocks[0].kind==='table').blocks[0].rows.pop();
  assert.throws(()=>verifyCoverage(source,broken),/表格内容变化/);
});

test('正文中的HTML不能成为活动内容',()=>{
  const html=pageHTML({title:'<script>bad()</script>',blocks:[{id:'b',kind:'text',text:'<img src=x onerror=alert(1)>'}]});
  assert.ok(!html.includes('<script>bad()'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes("default-src 'none'"));
});

test('浏览器探针命中碰撞、低对比、裂图和裸文本出界',async()=>{
  const browser=await chromium.launch({...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    await page.setContent(`<body style="background:white"><div data-text style="position:absolute;top:50px;left:40px;font-size:32px">文字甲</div><div data-text style="position:absolute;top:50px;left:40px;font-size:32px">文字乙</div><div data-text style="position:absolute;top:150px;color:white">白底白字</div><div data-text style="position:absolute;top:740px">画布外文本</div><img src="data:image/png;base64,broken"></body>`);
    const {issues}=await renderProbe(page);
    const collision=issues.find(i=>i.rule==='text_collision');assert.ok(collision.a_selector&&collision.a_text&&collision.a_rect.w>0);
    const rules=new Set(issues.map(i=>i.rule));
    for(const rule of ['text_collision','low_contrast_text','resource_failed','element_out_of_canvas'])assert.ok(rules.has(rule),rule);
  }finally{await browser.close()}
});

test('提案缺少原稿备注必须失败，拆页保留完整项和图表比例',async()=>{
  const {verifyNotes,repairDesign}=await import('./design.mjs');
  const source=[{id:'b0',source:'预计60万–120万'}];
  assert.throws(()=>verifyNotes(source,[{blocks:[]}]),/遗漏/);
  const page={layout:'bars',blocks:source,chart_max:120,items:[{label:'甲',value:'120万'},{label:'乙',value:'25万'}]};
  const parts=repairDesign(page);
  assert.ok(verifyNotes(source,parts));
  assert.ok(parts.every(p=>p.chart_max===120));
  assert.deepEqual(parts.flatMap(p=>p.items),page.items);
});


test('自定义页正文不能挤入统一页脚留白',async()=>{
  const browser=await chromium.launch({...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    await page.setContent(`<body style="background:white"><p data-text style="position:absolute;top:620px;font-size:28px;line-height:40px;margin:0">侵入留白的正文</p><footer class="custom-footer" style="position:absolute;top:660px"><span data-text style="font-size:16px">正常页脚</span></footer></body>`);
    const {issues}=await renderProbe(page);
    assert.ok(issues.some(i=>i.rule==='footer_clearance'&&i.text.includes('正文')));
    assert.ok(!issues.some(i=>i.rule==='footer_clearance'&&i.text.includes('页脚')));
  }finally{await browser.close()}
});


test('四种图表离线渲染，刻度跨零且原生图表保留同一数据',async()=>{
 const {chartScale,nativeChartSpec}=await import('./charts.mjs');
 const theme={background:'F6F3E9',text:'143C30',accent:'C8D45A',muted:'536359'};
 const s=chartScale([-35,80]);assert.ok(s.min<=-35&&s.max>=80&&s.ticks.includes(0));
 const browser=await chromium.launch({...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
 try{const page=await browser.newPage({viewport:{width:1280,height:720}});
  for(const type of ['bar','column','line','donut']){
   const c={type,categories:['预热期','爆发期','收口期'],series:[{name:'计划预算',values:[80,165,55]}],unit:'万元',source_note:'测试数据，计划非实际'};
   const p={layout:'chart',title:'计划投入按阶段分布',section:'预算',subtitle:'预算数据检查',chart:c,evidence_note:'测试用途'};
   await page.setContent(pageHTML(p,0,1,theme));await page.evaluate(()=>document.fonts.ready);
   assert.deepEqual((await renderProbe(page)).issues,[],type);
   const spec=nativeChartSpec(c,theme);assert.deepEqual(spec.data[0].values,c.series[0].values);assert.deepEqual(spec.data[0].labels,c.categories);
   assert.equal(await page.locator('script,canvas').count(),0);
  }
 }finally{await browser.close()}
});

test('同类页面实际字体漂移会失败，跨构图字号差异允许',async()=>{
 const {auditConsistency}=await import('./consistency.mjs');
 const pages=[{layout:'columns',items:[{},{}]},{layout:'columns',items:[{},{}]}];
 const text={selector:'h1.headline',fontSize:46,fontFamily:'Microsoft YaHei',bold:true};
 assert.equal(auditConsistency(pages,[{texts:[text]},{texts:[{...text,fontSize:42}]}]).passed,false);
 pages[1].layout='statement';
 assert.equal(auditConsistency(pages,[{texts:[text]},{texts:[{...text,fontSize:78}]}]).passed,true);
});

test('图表页面之后图片页不能继承禁图策略',async()=>{
 const {chartHTML}=await import('./charts.mjs');
 const browser=await chromium.launch({...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
 try{const page=await browser.newPage({viewport:{width:1280,height:720}});
  const theme={background:'F6F3E9',text:'143C30',accent:'C8D45A',muted:'536359'};
  await page.setContent(chartHTML({title:'测试',chart:{type:'bar',categories:['甲','乙'],series:[{name:'数值',values:[1,2]}],unit:'次'}},0,1,theme));
  const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
  await page.setContent(pageHTML({layout:'image',title:'图片检查',image:png,subtitle:'',caption:''}));
  await page.evaluate(()=>Promise.all([...document.images].map(im=>im.decode())));
  assert.equal(await page.locator('img').evaluate(im=>im.naturalWidth),1);
 }finally{await browser.close()}
});

test('选定风格落到真实背景、卡片与品牌强调页，且可读性通过',async()=>{
 const themes=[
  {style_id:'natural',background:'F6F3E9',text:'143C30',accent:'C8D45A',muted:'536359'},
  {style_id:'business',background:'F4F7FC',text:'172F50',accent:'B6CDF5',muted:'4E6178'},
  {style_id:'editorial',background:'FAF5EC',text:'442D28',accent:'EEC69A',muted:'786151'}
 ];
 const browser=await chromium.launch({...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:720}});
  const rgb=hex=>'rgb('+[0,2,4].map(i=>parseInt(hex.slice(i,i+2),16)).join(', ')+')';
  for(const theme of themes){
   const data={layout:'comparison',title:'清晰呈现传播策略',subtitle:'围绕场景建立品牌记忆',section:'策略',evidence_note:'来源：用户文稿',items:[{label:'场景',text:'聚焦办公室的真实需求'},{label:'表达',text:'用简洁内容传递产品价值'}]};
   await page.goto('about:blank');await page.setContent(pageHTML(data,0,2,theme));
   assert.equal(await page.evaluate(()=>getComputedStyle(document.body).backgroundColor),rgb(theme.background));
   assert.equal(await page.locator('.item').first().evaluate(el=>getComputedStyle(el).backgroundColor),rgb(theme.accent));
   assert.deepEqual((await renderProbe(page)).issues,[]);
   await page.goto('about:blank');await page.setContent(pageHTML({...data,layout:'statement',emphasis:'brand',items:[]},1,2,theme));
   assert.deepEqual((await renderProbe(page)).issues,[]);
  }
 }finally{await browser.close()}
});

test('可选序号与栏目标题分开比较，仍拒绝真实标题字号漂移',async()=>{
 const {auditConsistency}=await import('./consistency.mjs');
 const pages=[{composition:'audience_bands',items:[{},{}]},{composition:'audience_bands',items:[{},{}]}];
 const label={role:'label',textKey:'item_0_label',fontSize:30,fontFamily:'sans-serif',bold:true};
 const value={role:'label',textKey:'item_0_value',fontSize:48,fontFamily:'sans-serif',bold:true};
 assert.equal(auditConsistency(pages,[{texts:[label,value]},{texts:[label]}]).passed,true);
 assert.equal(auditConsistency(pages,[{texts:[label,value]},{texts:[{...label,fontSize:26}]}]).passed,false);
});


test('品牌发布风格按角色切换深浅背景，仍通过真实排版探针',async()=>{
 const theme={style_id:'brand_launch',background:'F5F5F5',text:'1A1A1A',accent:'FF6900',muted:'646464'};
 const browser=await chromium.launch({...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
 try{const page=await browser.newPage({viewport:{width:1280,height:720}});
  for(const [layout,bg] of [['section','rgb(13, 13, 13)'],['columns','rgb(245, 245, 245)'],['closing','rgb(255, 105, 0)']]){
   await page.goto('about:blank');await page.setContent(pageHTML({layout,title:'核心用户与场景',subtitle:'围绕具体需求展开传播',section:'策略',evidence_note:'原稿提案',items:[{label:'核心场景',text:'让产品进入真实生活'}]},0,3,theme));
   await page.evaluate(()=>document.fonts.ready);
   assert.equal(await page.evaluate(()=>getComputedStyle(document.body).backgroundColor),bg);
   assert.deepEqual((await renderProbe(page)).issues,[],layout);
  }
 }finally{await browser.close()}
});
