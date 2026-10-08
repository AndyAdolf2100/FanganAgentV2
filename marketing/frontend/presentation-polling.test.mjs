// Build first, then run with node --test. Every API call is mocked; no models/backend.
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {createServer} from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const {chromium}=createRequire(new URL('../presentation/package.json',import.meta.url))('playwright');
const root=path.dirname(fileURLToPath(import.meta.url));
let server,browser,base;
before(async()=>{
 server=createServer(async(req,res)=>{
  const pathname=new URL(req.url,'http://local').pathname;
  const name=pathname==='/'?'index.html':decodeURIComponent(pathname.slice(1));
  try{if(name.includes('..'))throw Error();const body=await fs.readFile(path.join(root,'dist',name));res.setHeader('Content-Type',name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(body)}catch{res.writeHead(404);res.end()}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base=`http://127.0.0.1:${server.address().port}`;
 browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(resolve=>server?.close(resolve));});
const job=status=>({id:'job',status,stage:status==='running'?'visual_review':'completed',page_count:2,style_id:'editorial'});
async function fixture(handler,{assembly=true,extra}={}){
 const page=await browser.newPage(),errors=[],counts={alpha:0,beta:0};
 page.on('pageerror',e=>errors.push(e.message));await page.clock.install();
 const runs=['alpha','beta'].map(id=>({id,brief:id==='alpha'?'Alpha project':'Beta project',created:1,status:'completed',source_type:'brief',outputs:assembly?{assembly:'# Plan'}:{},index:0}));
 const json=(route,value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(extra && await extra({route,p,json,page}))return;
  if(p==='/api/health')return json(route,{stages:[],tags:{},gates:{},runtime:'demo'});
  if(p==='/api/presentation-styles')return json(route,[{id:'auto',name:'Auto'},{id:'editorial',name:'Editorial'}]);
  if(p==='/api/enterprise-templates')return json(route,{templates:[]});
  if(p==='/api/runs')return json(route,runs);
  const presentation=p.match(/^\/api\/runs\/(alpha|beta)\/presentation$/);
  if(presentation){const id=presentation[1];return handler({route,id,count:++counts[id],json,page});}
  if(p.endsWith('/events'))return route.fulfill({contentType:'text/event-stream',body:'event: idle\ndata: {}\n\n'});
  if(p.endsWith('/feedback'))return json(route,{detail:'业务处理失败'},422);
  const run=p.match(/^\/api\/runs\/(alpha|beta)$/);if(run)return json(route,runs.find(r=>r.id===run[1]));
  return json(route,{detail:'unexpected mock endpoint'},404);
 });
 await page.goto(base+'/?project=alpha');
 return {page,counts,errors};
}
const pollAlert=page=>page.getByRole('alert').filter({hasText:/演示文稿进度/});

test('enterprise outline is previewed before the same job resumes',async()=>{
 let status='awaiting_outline_confirmation', confirmations=0;
 const waiting=()=>({id:'enterprise-job',status,stage:status==='awaiting_outline_confirmation'?'outline_review':'page_generating',page_count:2,enterprise_workflow_version:5,options:{template_id:'template-one',template_revision:1},outline_review_required:true});
 const outline={job_id:'enterprise-job',title:'规划测试',planned_page_count:2,agenda:[{id:'a1',number:1,text:'预算'}],pages:[{number:1,role:'cover',title:'规划测试',template_page:0},{number:2,role:'body',title:'预算',template_page:3,source_preview:'预算30万元。'}]};
 const {page,errors}=await fixture(({route,json})=>json(route,waiting()),{extra:({route,p,json})=>{
  if(p==='/api/presentations/enterprise-job/outline'){json(route,outline);return true;}
  if(p==='/api/presentations/enterprise-job/confirm-outline'){confirmations++;status='running';json(route,waiting(),202);return true;}
  return false;
 }});
 try{
  await page.getByRole('heading',{name:'确认生成大纲'}).waitFor();
  assert.equal(await page.getByText('预计 2 页').count(),1);
  assert.equal(await page.getByText('预算30万元。').count(),1);
  await page.getByRole('button',{name:'确认大纲并开始生成'}).click();
  await page.getByRole('button',{name:'正在生成…',exact:true}).waitFor();
  assert.equal(confirmations,1);assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

test('general outline pauses generation and remains viewable after completion',async()=>{
 let status='awaiting_outline_confirmation',confirmations=0;
 const current=()=>({id:'general-job',status,stage:status==='awaiting_outline_confirmation'?'outline_review':status==='running'?'rendering':'completed',page_count:status==='completed'?3:2,style_id:'auto',outline_review_required:true,outline_available:true,options:{}});
 const outline=()=>({job_id:'general-job',mode:'general',status,title:'品牌方案',planned_page_count:2,actual_page_count:status==='completed'?3:null,pages:[
  {number:1,role:'cover',title:'品牌主张',source_preview:'明确品牌定位',design_intent:{focus:'左侧大标题'}},
  {number:2,role:'steps',title:'分阶段传播',source_preview:'按阶段开展传播。',section:'执行'}]});
 const {page,errors}=await fixture(({route,count,json})=>{
  if(status==='running'&&count>=3)status='completed';
  return json(route,current());
 },{extra:({route,p,json})=>{
  if(p==='/api/presentations/general-job/outline'){json(route,outline());return true;}
  if(p==='/api/presentations/general-job/confirm-outline'){confirmations++;status='running';json(route,current(),202);return true;}
  return false;
 }});
 try{
  await page.getByRole('heading',{name:'确认生成大纲'}).waitFor();
  assert.equal(await page.getByText('分阶段传播').count(),1);
  assert.equal(await page.getByText('模板第 1 页').count(),0);
  await page.getByRole('button',{name:'确认大纲并开始生成'}).click();
  await page.getByRole('button',{name:'正在生成…',exact:true}).waitFor();
  await page.clock.fastForward(1500);
  await page.getByRole('button',{name:'查看大纲'}).waitFor();
  await page.getByRole('button',{name:'查看大纲'}).click();
  await page.getByRole('heading',{name:'生成大纲',exact:true}).waitFor();
  assert.equal(await page.getByText('预计 2 页 · 成品 3 页').count(),1);
  assert.equal(await page.getByRole('button',{name:'确认大纲并开始生成'}).count(),0);
  assert.equal(confirmations,1);assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

test('completed general PPT from before outline confirmation shows its actual pages',async()=>{
 const completed={id:'legacy-general',status:'completed',stage:'completed',page_count:2,style_id:'auto',outline_available:true,options:{}};
 const outline={job_id:'legacy-general',mode:'general',legacy:true,status:'completed',title:'品牌方案',planned_page_count:2,actual_page_count:2,pages:[
  {number:1,role:'editorial',title:'目标',source_preview:'提升产品认知。'},
  {number:2,role:'editorial',title:'执行',source_preview:'按阶段开展传播。'}]};
 const {page,errors}=await fixture(({route,json})=>json(route,completed),{extra:({route,p,json})=>{
  if(p==='/api/presentations/legacy-general/outline'){json(route,outline);return true;}
  return false;
 }});
 try{
  await page.getByRole('button',{name:'查看大纲'}).click();
  await page.getByRole('heading',{name:'成品页面结构'}).waitFor();
  assert.equal(await page.getByText('成品 2 页').count(),1);
  assert.equal(await page.getByText('按阶段开展传播。').count(),1);
  assert.equal(await page.getByRole('button',{name:'确认大纲并开始生成'}).count(),0);
  assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

test('502 recovers, preserves independent business errors, and stops on completion',async()=>{
 const {page,counts,errors}=await fixture(({route,count,json})=>count===2?json(route,{detail:'Bad Gateway'},502):json(route,job(count>=4?'completed':'running')));
 try{
  await page.getByRole('button',{name:'正在生成…',exact:true}).waitFor();
  await page.clock.fastForward(1500);await pollAlert(page).waitFor();
  assert.equal(await page.locator('.ppt-panel [role=alert]').count(),1);
  await page.getByLabel('确认或修改意见').fill('feedback');await page.getByRole('button',{name:'按意见修改当前阶段'}).click();
  await page.getByRole('alert').filter({hasText:'业务处理失败'}).waitFor();
  await page.clock.fastForward(1500);await pollAlert(page).waitFor({state:'hidden'});
  assert.equal(await page.getByRole('alert').filter({hasText:'业务处理失败'}).count(),1);
  await page.clock.fastForward(1500);await page.getByText('PPT 已完成',{exact:false}).waitFor();
  await page.clock.fastForward(120000);assert.equal(counts.alpha,4);assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

test('initial network failure is visible without a PPT panel and recovers',async()=>{
 const {page,counts,errors}=await fixture(({route,count,json})=>count===1?route.abort('failed'):json(route,null),{assembly:false});
 try{
  await pollAlert(page).waitFor();assert.equal(await page.locator('main > [role=alert]').count(),1);
  await page.clock.fastForward(1500);await pollAlert(page).waitFor({state:'hidden'});
  await page.clock.fastForward(120000);assert.equal(counts.alpha,2);assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

for(const staleStatus of [200,502])test(`project switch ignores an old pending ${staleStatus} response`,async()=>{
 let release;
 const {page,counts,errors}=await fixture(({route,id,count,json})=>id==='alpha'?new Promise(resolve=>{release=()=>json(route,staleStatus===200?job('running'):{detail:'stale gateway'},staleStatus).then(resolve)}):json(route,job(count===1?'running':'completed')));
 try{
  await page.getByRole('button',{name:/Beta project/}).click();await page.getByRole('button',{name:'正在生成…',exact:true}).waitFor();
  assert.ok(release);await release();
  await page.clock.fastForward(1500);await page.getByText('PPT 已完成',{exact:false}).waitFor();
  assert.equal(await pollAlert(page).count(),0);assert.ok((await page.locator('.history button.active').innerText()).includes('Beta'));
  await page.clock.fastForward(120000);assert.deepEqual(counts,{alpha:1,beta:2});assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

test('six consecutive failures stop retrying; selecting a project starts a fresh poll',async()=>{
 const {page,counts,errors}=await fixture(({route,id,json})=>id==='alpha'?json(route,{detail:'Bad Gateway'},502):json(route,job('completed')));
 try{
  await pollAlert(page).waitFor();
  for(const [index,delay] of [1500,3000,6000,12000,24000].entries()){
   await page.clock.fastForward(delay);
   await page.waitForFunction(expected=>document.querySelector('[role=alert]')?.textContent.includes(expected),index===4?'连续 6 次':`连续失败 ${index+2}/6`);
  }
  await page.clock.fastForward(120000);assert.equal(counts.alpha,6);
  assert.ok((await pollAlert(page).innerText()).includes('这不会重新生成任务'));
  await page.getByRole('button',{name:/Beta project/}).click();await page.getByText('PPT 已完成',{exact:false}).waitFor();
  assert.equal(await pollAlert(page).count(),0);assert.equal(counts.beta,1);assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

test('switching during backoff cancels the previous project retry',async()=>{
 const {page,counts,errors}=await fixture(({route,id,json})=>id==='alpha'?json(route,{detail:'Bad Gateway'},502):json(route,job('completed')));
 try{
  await pollAlert(page).waitFor();await page.getByRole('button',{name:/Beta project/}).click();
  await page.getByText('PPT 已完成',{exact:false}).waitFor();await page.clock.fastForward(120000);
  assert.deepEqual(counts,{alpha:1,beta:1});assert.equal(await pollAlert(page).count(),0);assert.deepEqual(errors,[]);
 }finally{await page.close();}
});

test('v5 group-local visual progress differs from final and legacy page progress',async()=>{
 const cases=[
  [{enterprise_workflow_version:5,page_progress:{current:15,total:52},visual_progress:{phase:'initial',current:1,total:1}},'页面组 15 / 52 · 本组截图 1 / 1'],
  [{enterprise_workflow_version:5,page_progress:{current:15,total:52},visual_progress:{phase:'candidate',current:0,total:1}},'页面组 15 / 52 · 本组截图 0 / 1'],
  [{enterprise_workflow_version:5,page_progress:{current:15,total:52},visual_progress:{phase:'final',current:4,total:56}},'冻结版本视觉验收 · 4 / 56'],
  [{enterprise_workflow_version:4,visual_progress:{phase:'candidate',page:8,current:1,total:1}},'修复后逐页复查 · 正在看第 8 页 · 1 / 1'],
  [{enterprise_workflow_version:5,visual_progress:{phase:'initial'}},'页面组 — / — · 本组截图 — / —'],
 ];
 const {page,errors}=await fixture(({route,count,json})=>json(route,{...job('running'),...cases[Math.min(count-1,cases.length-1)][0]}));
 try{
  for(let i=0;i<cases.length;i++){if(i)await page.clock.fastForward(1500);await page.getByText(cases[i][1],{exact:true}).waitFor();}
  assert.deepEqual(errors,[]);
 }finally{await page.close();}
});
