// Deterministic, offline charts. HTML uses SVG geometry plus measurable DOM text;
// PPTX uses the same values in native charts with embedded workbooks.
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const CHART_FRAME={x:64,y:252,w:1152,h:366};
export const formatValue=v=>String(v);
const numberFormat=values=>{const digits=Math.max(...values.map(v=>{const [mantissa,exponent='0']=String(v).toLowerCase().split('e');return Math.max(0,(mantissa.split('.')[1]||'').length-Number(exponent));}));return digits?'0.'+'0'.repeat(Math.min(16,digits)):'0';};
export function chartScale(values){
 const low=Math.min(0,...values),high=Math.max(0,...values);
 const span=high-low||1,raw=span/4,power=10**Math.floor(Math.log10(raw));
 const step=[1,2,2.5,5,10].find(v=>v*power>=raw)*power;
 const min=Math.floor(low/step)*step,max=Math.ceil((high||step)/step)*step;
 return {min,max,step,ticks:Array.from({length:Math.round((max-min)/step)+1},(_,i)=>Number((min+i*step).toPrecision(12)))};
}
export function chartPalette(theme){return theme.chartColors?.length ? Array.from({length:6},(_,i)=>theme.chartColors[i%theme.chartColors.length].replace(/^#/,'')) : [theme.text,'7A651E','4C806A','A67553','66828C','8B7394'];}
export function chartHTML(p,index,total,theme){
 const c=p.chart,colors=chartPalette(theme),f=CHART_FRAME,scale=chartScale(c.series.flatMap(s=>s.values));
 const svg=[],labels=[];
 const line=(x1,y1,x2,y2,color='#d5d8ca',width=1)=>svg.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${width}"/>`);
 const text=(value,x,y,w,h=36,align='left',size=26,bold=false)=>labels.push(`<div data-text class="chart-label" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;text-align:${align};font-size:${size}px;font-weight:${bold?700:400}">${esc(value)}</div>`);
 const rect=(x,y,w,h,fill)=>svg.push(`<rect x="${x}" y="${y}" width="${Math.max(0,w)}" height="${Math.max(0,h)}" fill="#${fill}"/>`);
 const n=c.categories.length,m=c.series.length;
 if(c.type==='donut'){
  const values=c.series[0].values,sum=values.reduce((a,b)=>a+b,0),cx=230,cy=176,r=140,inner=85;let start=-Math.PI/2;
  values.forEach((v,i)=>{const angle=v/sum*Math.PI*2,end=start+angle,point=(rad,a)=>[cx+rad*Math.cos(a),cy+rad*Math.sin(a)];
   if(angle>0){if(angle>=Math.PI*2-1e-9)svg.push(`<circle cx="${cx}" cy="${cy}" r="${(r+inner)/2}" fill="none" stroke="#${colors[i]}" stroke-width="${r-inner}"/>`);
   else{const a=point(r,start),b=point(r,end),d=point(inner,end),e=point(inner,start),large=angle>Math.PI?1:0;svg.push(`<path d="M ${a} A ${r} ${r} 0 ${large} 1 ${b} L ${d} A ${inner} ${inner} 0 ${large} 0 ${e} Z" fill="#${colors[i]}"/>`);}}
   start=end;const y=12+i*55;rect(480,y+8,18,18,colors[i]);text(c.categories[i],516,y,375,36,'left',26);text(formatValue(v),905,y,180,36,'right',28,true);
  });
  text(formatValue(sum),cx-85,cy-35,170,60,'center',44,true);text(c.unit,cx-85,cy+37,170,32,'center',22);
 }else if(c.type==='bar'){
  const left=scale.min<0?465:365,right=1060,top=12,bottom=318,width=right-left,x=v=>left+(v-scale.min)/(scale.max-scale.min)*width;
  scale.ticks.forEach(v=>{line(x(v),top,x(v),bottom);text(formatValue(v),x(v)-40,bottom+8,80,32,'center',22)});
  const band=(bottom-top)/n,bar=Math.min(34,(band-12)/m);
  c.categories.forEach((category,i)=>{const y=top+i*band; text(category,0,y+(band-36)/2,340,36,'right',26);
   c.series.forEach((s,j)=>{const v=s.values[i],yy=y+(band-bar*m)/2+j*bar;rect(Math.min(x(0),x(v)),yy,Math.abs(x(v)-x(0)),bar-2,colors[j]);
    text(formatValue(v),v>=0?x(v)+8:x(v)-92,yy-4,84,34,v>=0?'left':'right',24,true);
   });
  });
  line(x(0),top,x(0),bottom,'#8e9b8f',2);
 }else{
  const left=72,right=1110,top=12,bottom=266,height=bottom-top,y=v=>bottom-(v-scale.min)/(scale.max-scale.min)*height;
  scale.ticks.forEach(v=>{line(left,y(v),right,y(v));text(formatValue(v),0,y(v)-16,58,32,'right',22)});
  const band=(right-left)/n,pointX=i=>left+band*(i+.5);
  c.categories.forEach((category,i)=>text(category,pointX(i)-band/2+4,bottom+16,band-8,80,'center',24));
  c.series.forEach((s,j)=>{
   if(c.type==='column')s.values.forEach((v,i)=>{const bar=Math.min(80,(band-42)/m),x=pointX(i)-bar*m/2+j*bar;rect(x,Math.min(y(0),y(v)),bar-6,Math.abs(y(v)-y(0)),colors[j]);text(formatValue(v),x-8,v>=0?y(v)-34:y(v)+3,bar+10,32,'center',24,true)});
   else{
    svg.push(`<polyline points="${s.values.map((v,i)=>`${pointX(i)},${y(v)}`).join(' ')}" fill="none" stroke="#${colors[j]}" stroke-width="4"/>`);
    s.values.forEach((v,i)=>svg.push(`<circle cx="${pointX(i)}" cy="${y(v)}" r="6" fill="#${colors[j]}"/>`));
   }
  });
  line(left,y(0),right,y(0),'#8e9b8f',2);
 }
 const legend=c.type!=='donut'&&m>1?c.series.map((s,j)=>`<span style="display:inline-flex;gap:10px;margin-left:30px"><i style="width:16px;height:16px;background:#${colors[j]};margin-top:7px"></i><span data-text>${esc(s.name)}</span></span>`).join(''):'';
 return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';img-src data:;style-src 'unsafe-inline'"><style>*{box-sizing:border-box}body{margin:0;width:1280px;height:720px;background:#${theme.background};color:#${theme.text};font-family:"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif}h1,p{margin:0}.chart-label{position:absolute;line-height:1.3;overflow:visible;word-break:break-all}.footer{position:absolute;left:64px;right:64px;top:665px;border-top:1px solid #bdc5b6;padding-top:12px;display:flex;justify-content:space-between;font-size:16px;color:#${theme.muted}}</style><body><div data-text style="position:absolute;left:64px;top:42px;font-size:24px">${esc(p.section)}</div><h1 data-text style="position:absolute;left:64px;top:88px;width:1152px;font-size:44px;line-height:1.2">${esc(p.title)}</h1><p data-text style="position:absolute;left:64px;top:157px;width:1152px;font-size:27px;line-height:1.4">${esc(p.subtitle)}</p><div data-text style="position:absolute;left:64px;top:215px;font-size:22px;color:#${theme.muted}">单位：${esc(c.unit)}</div><div data-chart-legend style="position:absolute;right:64px;top:215px;font-size:22px">${legend}</div><div data-chart-plot style="position:absolute;left:${f.x}px;top:${f.y}px;width:${f.w}px;height:${f.h}px"><svg width="${f.w}" height="${f.h}" style="position:absolute" aria-hidden="true">${svg.join('')}</svg>${labels.join('')}</div><p data-text style="position:absolute;left:64px;top:632px;width:1152px;font-size:16px;color:#${theme.muted}">${esc(c.source_note)}</p><footer class="footer"><span data-text>${esc(p.evidence_note)}</span><span data-text>${index+1} / ${total}</span></footer></body></html>`;
}
export function nativeChartSpec(c,theme){
 const scale=chartScale(c.series.flatMap(s=>s.values)),f=CHART_FRAME;
 const options={x:f.x/96,y:f.y/96,w:f.w/96,h:f.h/96,
  chartColors:c.type==='donut'?chartPalette(theme):chartPalette(theme).slice(0,c.series.length),chartArea:{fill:{color:theme.background},border:{color:theme.background,pt:0}},plotArea:{fill:{color:theme.background},border:{color:theme.background,pt:0}},
  showTitle:false,showLegend:c.type==='donut'||c.series.length>1,legendPos:c.type==='donut'?'r':'b',legendFontFace:'Microsoft YaHei',legendFontSize:18,
  catAxisLabelFontFace:'Microsoft YaHei',valAxisLabelFontFace:'Microsoft YaHei',dataLabelFormatCode:numberFormat(c.series.flatMap(s=>s.values)),valAxisLabelFormatCode:numberFormat([scale.step]),catAxisLabelRotate:0.001,catAxisLabelFrequency:'1',catAxisLabelPos:'low',valAxisLabelPos:'low',
  catAxisLabelFontSize:18,valAxisLabelFontSize:16,dataLabelColor:theme.text,dataLabelFontFace:'Microsoft YaHei',dataLabelFontSize:18,
  catAxisLabelColor:theme.text,valAxisLabelColor:theme.muted,
  catAxisLineShow:false,valAxisLineShow:false,valGridLine:{color:'D5D8CA',width:1},
  valAxisMinVal:scale.min,valAxisMaxVal:scale.max,valAxisMajorUnit:scale.step,showValue:c.type!=='line',showLabel:false,
  showBorder:false,showShadow:false};
 if(c.type==='bar'||c.type==='column')Object.assign(options,{barDir:c.type==='bar'?'bar':'col',barGrouping:'clustered',catAxisOrientation:c.type==='bar'?'maxMin':'minMax',barGapWidthPct:90,dataLabelPosition:'outEnd'});
 if(c.type==='line')Object.assign(options,{lineSize:3,lineDataSymbol:'circle',lineDataSymbolSize:7,lineSmooth:false,showValue:false});
 if(c.type==='donut')Object.assign(options,{holeSize:62,firstSliceAng:270,showValue:true,showPercent:false,dataLabelPosition:'bestFit',dataLabelColor:'FFFFFF'});
 return {type:({bar:'bar',column:'bar',line:'line',donut:'doughnut'})[c.type],data:c.series.map(s=>({name:s.name,labels:c.categories,values:s.values})),options};
}
