// Compare like-for-like page families. Hero/image variants are separate families;
// intentional changes between different compositions are not treated as drift.
export function auditConsistency(pages,probes){
 const groups=new Map(),issues=[];
 pages.forEach((p,i)=>{
  const key=[p.section||'',p.composition||p.layout,p.emphasis||'normal',(p.items||[]).length,p.has_image||p.image?'image':'text',p.custom?'custom':'base'].join(':');
  const roles=new Map();
  for(const t of probes[i].texts){
   if(t.chartLabel)continue;
   // Values and labels have distinct typography. An optional ordinal/metric
   // appearing on only one page must not make its labels look inconsistent.
   const role=t.textKey?.endsWith('_value')?'value':t.role||t.selector;
   const signature=[t.fontSize,t.fontFamily,t.bold].join('|');
   if(!roles.has(role))roles.set(role,new Set());roles.get(role).add(signature);
  }
  const signatures=Object.fromEntries([...roles].map(([role,set])=>[role,[...set].sort()]));
  if(!groups.has(key))groups.set(key,{reference_page:i+1,signatures,pages:[]});
  const group=groups.get(key);group.pages.push(i+1);
  for(const [role,signature] of Object.entries(signatures)){
   if(group.signatures[role]&&JSON.stringify(signature)!==JSON.stringify(group.signatures[role]))issues.push({rule:'same_family_font_drift',page:i+1,reference_page:group.reference_page,family:key,role,actual:signature,expected:group.signatures[role]});
  }
 });
 return {passed:issues.length===0,issues,families:[...groups].map(([family,g])=>({family,pages:g.pages,reference_page:g.reference_page})),
  scope:'Actual browser font family/size/weight within the same module, layout, emphasis, item count and image variant; cross-module prototypes intentionally remain distinct',
  limitations:['This checks typography consistency, not semantic completeness or subjective aesthetics']};
}
