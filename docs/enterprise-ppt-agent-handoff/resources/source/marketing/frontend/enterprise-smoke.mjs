// Isolated demo API + built frontend + actual browser PPTX import and export.
import assert from 'node:assert/strict';
import {createServer,request} from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../presentation/package.json',import.meta.url));
const {chromium}=require('playwright'),JSZip=require('jszip');
if(process.env.MARKETING_RUNTIME!=='demo')throw new Error('Requires isolated demo environment');
const root=path.dirname(new URL(import.meta.url).pathname),data='/tmp/enterprise-smoke-'+Date.now();
const api=spawn('python',['-m','uvicorn','marketing_agent.api:app','--host','127.0.0.1','--port','19181'],{cwd:path.dirname(root),env:{...process.env,PYTHONPATH:path.dirname(root),MARKETING_DATA_DIR:data},stdio:'ignore'});
const server=createServer(async(req,res)=>{
  if(req.url.startsWith('/api/')){const p=request(new URL(req.url,'http://127.0.0.1:19181'),{method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res)});p.on('error',()=>{res.writeHead(502);res.end()});req.pipe(p);return;}
  try{const name=req.url==='/'?'index.html':decodeURIComponent(req.url.slice(1));if(name.includes('..'))throw Error();const body=await fs.readFile(path.join(root,'dist',name));res.setHeader('Content-Type',name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(body)}catch{res.writeHead(404);res.end()}
});
const P='http://schemas.openxmlformats.org/presentationml/2006/main',A='http://schemas.openxmlformats.org/drawingml/2006/main',R='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
async function pptx(wide=true){
  const z=new JSZip(),W=12192000,H=wide?6858000:9144000;
  z.file('ppt/presentation.xml',`<p:presentation xmlns:p="${P}" xmlns:r="${R}"><p:sldIdLst>${[1,2,3].map(i=>`<p:sldId id="${255+i}" r:id="r${i}"/>`).join('')}</p:sldIdLst><p:sldSz cx="${W}" cy="${H}"/></p:presentation>`);
  z.file('ppt/_rels/presentation.xml.rels',`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${[1,2,3].map(i=>`<Relationship Id="r${i}" Type="${R}/slide" Target="slides/slide${i}.xml"/>`).join('')}</Relationships>`);
  const shape=(id,text,y,h,size,type='')=>`<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="文本${id}"/><p:nvPr>${type?`<p:ph type="${type}"/>`:''}</p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="650000" y="${y}"/><a:ext cx="10800000" cy="${h}"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:p><a:r><a:rPr sz="${size}"><a:solidFill><a:srgbClr val="223344"/></a:solidFill><a:ea typeface="Noto Sans CJK SC"/></a:rPr><a:t>${text}</a:t></a:r></a:p></p:txBody></p:sp>`;
  for(let i=1;i<=3;i++)z.file(`ppt/slides/slide${i}.xml`,`<p:sld xmlns:p="${P}" xmlns:a="${A}" xmlns:r="${R}"><p:cSld><p:spTree>${shape(1,i===3?'谢谢':'企业模板',500000,900000,3200,'title')}${i===2?shape(2,'正文内容占位',1800000,3900000,2000,'body'):''}${shape(3,'示例有限公司',6200000,400000,1200)}</p:spTree></p:cSld></p:sld>`);
  return z.generateAsync({type:'nodebuffer'});
}
let browser;
try{
  for(let i=0;i<80;i++){try{if((await fetch('http://127.0.0.1:19181/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,250));}
  await new Promise(r=>server.listen(19182,'127.0.0.1',r));
  browser=await chromium.launch({executablePath:process.env.PRESENTATION_CHROMIUM,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/'))requests.push({url:r.url(),method:r.method(),body:r.postData()});});
  await page.goto('http://127.0.0.1:19182');
  await page.getByRole('button',{name:'企业模板管理',exact:true}).click();
  await page.locator('input[type=file]').setInputFiles({name:'错误比例.pptx',mimeType:'application/vnd.openxmlformats-officedocument.presentationml.presentation',buffer:await pptx(false)});
  await page.getByRole('alert').filter({hasText:'仅支持 16:9'}).waitFor();
  assert.equal(requests.filter(r=>r.method==='PUT').length,0,'invalid PPTX must be rejected in browser');
  await page.locator('input[type=file]').setInputFiles({name:'企业联调模板.pptx',mimeType:'application/vnd.openxmlformats-officedocument.presentationml.presentation',buffer:await pptx()});
  await page.getByRole('button',{name:'发布模板',exact:true}).waitFor();
  await page.getByRole('button',{name:'检查标签',exact:true}).click();
  await page.getByText('标签检查通过',{exact:true}).waitFor();
  assert.equal(requests.filter(r=>r.url.endsWith('/labels')).length,0);
  assert.ok(requests.filter(r=>r.method==='PUT').every(r=>JSON.parse(r.body).pages.length===3),'server receives parsed JSON only');
  await page.getByRole('checkbox',{name:/我已核对/}).check();
  await page.getByRole('button',{name:'发布模板',exact:true}).click();
  await page.getByText(/已发布 v1，生成页面可选择此版本/).waitFor();
  assert.equal(await page.locator('.canvas .element').first().evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)','editor selection overlay must not cover template preview');
  await page.frameLocator('iframe[title="企业模板版式编辑画布"]').getByText('企业模板',{exact:true}).waitFor();
  await page.locator('.template-editor .stage').scrollIntoViewIfNeeded();
  await page.locator('.template-editor .stage').screenshot({path:path.join(root,'enterprise-canvas-smoke.png')});
  console.log('editor-frame', JSON.stringify(await page.frameLocator('iframe[title="企业模板版式编辑画布"]').getByText('企业模板',{exact:true}).evaluate(el=>({text:el.textContent,color:getComputedStyle(el).color,rect:el.getBoundingClientRect().toJSON()}))));
  await page.screenshot({path:path.join(root,'enterprise-manager-smoke.png'),fullPage:true});
  await page.getByRole('button',{name:'← 返回营销策划',exact:true}).click();
  await page.getByRole('button',{name:/上传已有文稿/}).click();
  await page.getByLabel('完整文稿',{exact:true}).fill('# 企业测试\n\n## 执行方案\n\n预算30万元，按照原稿执行。\n\n## 渠道\n\n渠道甲投入20万元，渠道乙投入10万元。');
  await page.getByRole('button',{name:'下一步：选择风格 →',exact:true}).click();
  const listing=await(await fetch('http://127.0.0.1:19181/api/enterprise-templates')).json(),template=listing.templates[0];
  await page.getByLabel('企业模板',{exact:true}).selectOption(template.id);
  await page.getByRole('button',{name:'创建项目并生成 PPT →',exact:true}).click();
  let job;
  for(let i=0;i<120;i++){
    const runs=await(await fetch('http://127.0.0.1:19181/api/runs')).json();
    if(runs[0])job=await(await fetch(`http://127.0.0.1:19181/api/runs/${runs[0].id}/presentation`)).json();
    if(job&&['completed','failed'].includes(job.status))break;
    await new Promise(r=>setTimeout(r,500));
  }
  assert.equal(job?.status,'completed',JSON.stringify(job));
  assert.equal(job.options.template_id,template.id);
  const buffer=await(await fetch(`http://127.0.0.1:19181/api/presentations/${job.id}/files/presentation.pptx`)).arrayBuffer();
  const zip=await JSZip.loadAsync(buffer),slides=Object.keys(zip.files).filter(n=>/^ppt\/slides\/slide\d+\.xml$/.test(n));
  assert.equal(slides.length,job.page_count);
  const xml=(await Promise.all(slides.map(n=>zip.file(n).async('string')))).join('');
  assert.ok(xml.includes('预算30万元'));
  assert.ok(xml.includes('示例有限公司'));
  assert.ok(xml.includes('Noto Sans CJK SC'));
  assert.ok(!requests.some(r=>/\/labels|\/images\/generations/.test(r.url)));
  assert.deepEqual(errors,[]);
  await page.getByRole('link',{name:'下载 PPTX',exact:true}).waitFor();
  await page.screenshot({path:path.join(root,'enterprise-result-smoke.png'),fullPage:true});
  console.log(JSON.stringify({passed:true,page_count:job.page_count,pptx_bytes:buffer.byteLength,frontend_import:true,local_labels:true,model_calls:0,data}));
}finally{await browser?.close();await new Promise(r=>server.close(r));api.kill('SIGTERM');}
