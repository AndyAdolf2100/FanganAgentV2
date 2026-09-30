// Run inside the API image with /test mounted read-only and no external network.
import assert from 'node:assert/strict';
import {createServer, request} from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../presentation/package.json',import.meta.url));
const {chromium}=require('playwright');
const JSZip=require('jszip');
if(process.env.MARKETING_RUNTIME!=='demo')throw new Error('Smoke test requires an isolated demo environment');
const root=path.dirname(new URL(import.meta.url).pathname);
const api=spawn('python',['-m','uvicorn','marketing_agent.api:app','--host','127.0.0.1','--port','18081'],{cwd:path.dirname(root),env:{...process.env,PYTHONPATH:path.dirname(root),MARKETING_DATA_DIR:'/tmp/import-smoke-data'},stdio:'ignore'});
let browser;
const server=createServer(async(req,res)=>{
 if(req.url.startsWith('/api/')){
  const proxy=request(new URL(req.url,'http://127.0.0.1:18081'),{method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res)});
  proxy.on('error',()=>{res.writeHead(502);res.end()});req.pipe(proxy);return;
 }
 try{
  const file=req.url==='/'?'index.html':decodeURIComponent(req.url.slice(1));
  if(file.includes('..'))throw new Error('bad path');
  const content=await fs.readFile(path.join(root,'dist',file));
  res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(content);
 }catch{res.writeHead(404);res.end()}
});
try{
 for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:18081/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,250))}
 await new Promise(r=>server.listen(18082,'127.0.0.1',r));
 browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1100}}), errors=[];
 page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR',e.message)});
 page.on('response',async r=>{if(r.status()>=400)console.error('HTTP ERROR',r.status(),r.url(),await r.text())});
 await page.goto('http://127.0.0.1:18082');
 await page.getByRole('button',{name:/上传已有文稿/}).click();
 const word=new JSZip();word.file('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Word 文稿标题</w:t></w:r></w:p><w:p><w:r><w:t>需要完整导入的正文。</w:t></w:r></w:p></w:body></w:document>');
 await page.getByLabel('上传完整文稿').setInputFiles({name:'已有方案.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:await word.generateAsync({type:'nodebuffer'})});
 await page.waitForFunction(()=>document.querySelector('[aria-label="完整文稿"]').value.includes('需要完整导入的正文。'));
 await page.getByText(/已提取 Word 正文和表格/).waitFor();
 const text='# 新品传播方案\n\n## 核心策略\n围绕办公室场景，清晰传递低糖主张。\n\n## 预算\n预算总额300万元。';
 await page.getByLabel('上传完整文稿').setInputFiles({name:'完整方案.md',mimeType:'text/markdown',buffer:Buffer.from(text)});
 try{await page.waitForFunction(expected=>document.querySelector('[aria-label="完整文稿"]').value===expected,text)}catch(e){console.error(await page.locator('body').innerText());throw e}
 assert.equal(await page.getByLabel('项目名称',{exact:true}).inputValue(),'新品传播方案');
 await page.getByRole('button',{name:'下一步：选择风格 →'}).click();
 await page.getByRole('radio',{name:/理性商务/}).check();
 await page.screenshot({path:'/artifacts/import-styles-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/artifacts/import-styles-mobile.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1100});
 await page.getByRole('button',{name:'创建项目并生成 PPT →'}).click();
 await page.getByText('PPT 已完成',{exact:false}).waitFor({timeout:90000});
 const state=await (await fetch('http://127.0.0.1:18081/api/runs')).json();assert.equal(state.length,1);
 const run=await (await fetch('http://127.0.0.1:18081/api/runs/'+state[0].id)).json();
 assert.equal(run.outputs.assembly,text);assert.equal(run.source_type,'manuscript');
 const job=await (await fetch('http://127.0.0.1:18081/api/runs/'+run.id+'/presentation')).json();
 assert.equal(job.style_id,'business');assert.equal(job.status,'completed');
 const html=await(await fetch('http://127.0.0.1:18081/api/presentations/'+job.id+'/pages/1')).text();
 assert.ok(html.includes('--c-bg:#F4F7FC'));
 const download=await fetch('http://127.0.0.1:18081/api/presentations/'+job.id+'/files/presentation.pptx');
 assert.equal(download.status,200);assert.ok((await download.arrayBuffer()).byteLength>1000);
 await page.getByLabel('生成风格',{exact:true}).selectOption('editorial');
 await page.getByRole('button',{name:'生成 PPT',exact:true}).click();
 await page.getByText('PPT 已完成',{exact:false}).waitFor({timeout:90000});
 const second=await(await fetch('http://127.0.0.1:18081/api/runs/'+run.id+'/presentation')).json();
 assert.notEqual(second.id,job.id);assert.equal(second.style_id,'editorial');
 await page.reload();
 await page.getByRole('button',{name:/新品传播方案/}).click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="生成风格"]')?.value==='editorial');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,upload:['Word','Markdown'],importPreserved:true,styles:['business','editorial'],pages:job.page_count,downloads:true,mobileNoOverflow:true,consoleErrors:errors}));
}finally{
 await browser?.close();server.closeAllConnections();server.close();api.kill('SIGTERM');
}
