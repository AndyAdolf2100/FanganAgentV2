import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
const output=await build({entryPoints:[new URL('./labels.ts',import.meta.url).pathname],bundle:true,write:false,format:'esm',platform:'node'});
const {labelPage}=await import('data:text/javascript;base64,'+Buffer.from(output.outputFiles[0].text).toString('base64'));
const text=(value,y=40)=>({kind:'text',x:80,y,width:800,height:70,binding:'content',fixed:false,paragraphs:[{align:'left',runs:[{text:value,size:32,font:'sans-serif',color:'#223344'}]}]});
const page=(elements,extra={})=>({role:'body',elements,background:'#FFFFFF',...extra});
test('recognizes 序言 and split 序/言 title on non-cover pages',()=>{
  for(const elements of [[text('序言'),text('说明文字',160)],[text('序'),text('言',110),text('说明文字',250)]]){
    const p=page(elements);labelPage(p,1,8,675,true);assert.equal(p.role,'preface');
  }
});
test('second page alone and prose mentions do not make a preface',()=>{
  for(const label of ['项目背景','介绍序言的相关内容','引言']){
    const p=page([text(label),text('普通正文',180)]);labelPage(p,1,8,675,true);assert.equal(p.role,'body');
  }
});
test('refreshes older automatic tags but preserves manually chosen page role',()=>{
  const automatic=page([text('序言')],{labelSource:'rule',labelRuleVersion:7});
  labelPage(automatic,1,8,675,true);assert.equal(automatic.role,'preface');
  const manual=page([text('序言')],{labelSource:'manual',labelRuleVersion:7});
  labelPage(manual,1,8,675,true);assert.equal(manual.role,'body');
});
