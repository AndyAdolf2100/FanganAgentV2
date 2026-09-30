import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium} from 'playwright';
import {renderProbe} from './render.mjs';
import {customHTML} from './custom.mjs';
const folder=path.resolve(process.argv[2]),index=Number(process.argv[3]);
const plan=JSON.parse(await fs.readFile(path.join(folder,'plan.json'),'utf8'));
const custom=JSON.parse(await fs.readFile(path.join(folder,'page-revisions',`${index+1}.json`),'utf8'));
const assets={};for(const [key,file] of Object.entries(plan.assets))assets[key]='data:image/png;base64,'+(await fs.readFile(path.join(folder,file))).toString('base64');
const slide={...plan.pages[index],custom,asset_data:assets};
const browser=await chromium.launch({headless:true,...(process.env.PRESENTATION_CHROMIUM?{executablePath:process.env.PRESENTATION_CHROMIUM}:{}),args:['--no-sandbox']});
try{
 const page=await browser.newPage({viewport:{width:1280,height:720}});
 await page.route('**/*',r=>r.request().url().startsWith('data:')?r.continue():r.abort());
 const html=customHTML(slide,index,plan.pages.length,plan.theme);await page.setContent(html);await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>Promise.all([...document.images].map(i=>i.decode().catch(()=>{}))));
 const probe=await renderProbe(page);
 const minimum=await page.evaluate((typography)=>[...document.querySelectorAll('[data-text]')].flatMap(el=>{
  const font=parseFloat(getComputedStyle(el).fontSize),role=el.dataset.role;
  const lower=typography?.[role]??(role==='section'||role==='caption'?16:role==='title'?43:role==='label'?24:28);
  return font<lower?[{rule:'font_below_minimum',severity:'high',text:el.textContent.slice(0,30),font,lower}]:[];
 }),plan.theme.typography);
 probe.issues.push(...minimum);
 const out=path.join(folder,'page-revisions');await fs.writeFile(path.join(out,`${index+1}.html`),html);await page.screenshot({path:path.join(out,`${index+1}.png`)});
 await fs.writeFile(path.join(out,`${index+1}-probe.json`),JSON.stringify(probe,null,2));
 console.log(JSON.stringify({issues:probe.issues,counts:{texts:probe.texts.length,images:probe.images.length}}));
}finally{await browser.close()}
