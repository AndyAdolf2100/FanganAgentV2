import type { TemplateElement, EnterpriseTemplatePage } from './types';
const color = (s?: string) => /^#[\da-f]{6}$/i.test(s || '') ? s! : 'none';
export function vectorSvg(e: TemplateElement, id: string) {
  const v = e.vector;
  if (!v) return '';
  const width = Math.max(1, e.width), height = Math.max(1, e.height);
  const viewWidth = v.width * width / Math.max(.001, e.width), viewHeight = v.height * height / Math.max(.001, e.height);
  let defs = '', fill = color(e.fill), stroke = color(e.stroke);
  const gradient = (g: NonNullable<typeof v.gradient>, name: string) => {
    const angle=g.angle*Math.PI/180, dx=Math.cos(angle),dy=Math.sin(angle),extent=Math.abs(dx)+Math.abs(dy);
    defs+=`<linearGradient id="${name}" x1="${.5-dx*extent/2}" y1="${.5-dy*extent/2}" x2="${.5+dx*extent/2}" y2="${.5+dy*extent/2}">${[...g.stops].sort((a,b)=>a.offset-b.offset).map(s=>`<stop offset="${s.offset}" stop-color="${color(s.color)}" stop-opacity="${s.opacity}"/>`).join('')}</linearGradient>`;
    return `url(#${name})`;
  };
  if(v.gradient) fill=gradient(v.gradient,id);
  if(v.strokeGradient) stroke=gradient(v.strokeGradient,`${id}-stroke`);
  let glow='';
  if(e.glow) for(let i=8;i>=1;i--) {
    const opacity=1-Math.pow(1-Math.min(.999999,e.glow.opacity),1/8);
    glow+=`<g opacity="${opacity}">${v.paths.map(path=>`<path d="${path}" fill="${color(e.glow!.color)}" stroke="${color(e.glow!.color)}" stroke-width="${e.glow!.radius*2*i/8}" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`).join('')}</g>`;
  }
  let shadow='';
  if(e.shadow) {
    const s=e.shadow, rx=v.width/Math.max(.001,e.width),ry=v.height/Math.max(.001,e.height);
    const a=s.scaleX,b=Math.tan(s.skewY*Math.PI/180)*ry/rx,c=Math.tan(s.skewX*Math.PI/180)*rx/ry,d=s.scaleY;
    const ax=v.width*s.alignX,ay=v.height*s.alignY;
    // Bake a soft shadow into ordinary vector layers so it survives SVG export without filters.
    const samples=s.blur ? [-2,-1,0,1,2].flatMap(x=>[-2,-1,0,1,2].map(y=>({x,y,w:Math.exp(-(x*x+y*y)/2)}))) : [{x:0,y:0,w:1}];
    const total=samples.reduce((sum,p)=>sum+p.w,0);
    shadow=samples.map(p=>{
      const tx=ax-a*ax-c*ay+(s.x+p.x*s.blur/4)*rx,ty=ay-b*ax-d*ay+(s.y+p.y*s.blur/4)*ry;
      const opacity=1-Math.pow(1-Math.min(.999999,s.opacity),p.w/total);
      return `<g transform="matrix(${a} ${b} ${c} ${d} ${tx} ${ty})" opacity="${opacity}">${v.paths.map(path=>`<path d="${path}" fill="${v.gradient || color(e.fill)!=='none' ? color(s.color) : 'none'}" stroke="${stroke!=='none'?color(s.color):'none'}" stroke-width="${e.strokeWidth ?? 1}" vector-effect="non-scaling-stroke"/>`).join('')}</g>`;
    }).join('');
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${viewWidth} ${viewHeight}" preserveAspectRatio="none" style="position:absolute;inset:0;width:${width}px;height:${height}px;overflow:visible;pointer-events:none" aria-hidden="true"><defs>${defs}</defs>${shadow}${glow}${v.paths.map(d => `<path d="${d}" fill="${fill}" fill-opacity="${v.gradient ? 1 : e.fillOpacity ?? 1}" stroke="${stroke}" stroke-width="${e.strokeWidth ?? 1}" stroke-opacity="${e.strokeOpacity ?? 1}" vector-effect="non-scaling-stroke"/>`).join('')}</svg>`;
}

export function backgroundSvg(page: EnterpriseTemplatePage) {
  const p=page.backgroundPattern;if(!p) return '';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" style="position:absolute;inset:0;pointer-events:none" aria-hidden="true"><defs><pattern id="page-pattern" width="16" height="16" patternUnits="userSpaceOnUse"><rect width="16" height="16" fill="${color(p.background)}"/><path d="M -8 -8 L 24 24 M -8 8 L 8 24 M 8 -8 L 24 8 M 0 0 L 8 -8 M 8 8 L 0 16 M 16 16 L 24 8" fill="none" stroke="${color(p.foreground)}" stroke-width=".6"/></pattern></defs><rect width="100%" height="100%" fill="url(#page-pattern)"/></svg>`;
}
