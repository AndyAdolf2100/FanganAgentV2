"""Render numeric template geometry as inert SVG (never accept raw SVG markup)."""
import math
import re

def svg(element, index):
    v = element.get('vector')
    if not v: return ''
    def color(value): return value if isinstance(value,str) and re.fullmatch(r'#[a-fA-F0-9]{6}',value) else 'none'
    fill=color(element.get('fill')); defs=''; gradient=v.get('gradient'); opacity=element.get('fillOpacity',1)
    def gradient_svg(g,name):
        angle=math.radians(g['angle']);dx=math.cos(angle);dy=math.sin(angle);extent=abs(dx)+abs(dy)
        stops=''.join(f'<stop offset="{s["offset"]}" stop-color="{color(s["color"])}" stop-opacity="{s["opacity"]}"/>' for s in sorted(g['stops'],key=lambda stop:stop['offset']))
        return f'<linearGradient id="{name}" x1="{.5-dx*extent/2}" y1="{.5-dy*extent/2}" x2="{.5+dx*extent/2}" y2="{.5+dy*extent/2}">{stops}</linearGradient>'
    if gradient:
        name=f'shape-gradient-{index}';defs+=gradient_svg(gradient,name);fill=f'url(#{name})';opacity=1
    stroke=color(element.get('stroke'))
    if v.get('strokeGradient'):
        name=f'shape-gradient-{index}-stroke';defs+=gradient_svg(v['strokeGradient'],name);stroke=f'url(#{name})'
    glow=''
    if element.get('glow'):
        g=element['glow'];alpha=1-(1-min(.999999,g['opacity']))**(1/8)
        for step in range(8,0,-1):
            copies=''.join(f'<path d="{path}" fill="{color(g["color"])}" stroke="{color(g["color"])}" stroke-width="{g["radius"]*2*step/8}" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>' for path in v['paths'])
            glow+=f'<g opacity="{alpha}">{copies}</g>'
    shadow=''
    if element.get('shadow'):
        s=element['shadow'];rx=v['width']/max(.001,element['width']);ry=v['height']/max(.001,element['height'])
        a=s['scaleX'];b=math.tan(math.radians(s['skewY']))*ry/rx;c=math.tan(math.radians(s['skewX']))*rx/ry;d=s['scaleY']
        ax=v['width']*s['alignX'];ay=v['height']*s['alignY']
        samples=[(x,y,math.exp(-(x*x+y*y)/2)) for x in range(-2,3) for y in range(-2,3)] if s['blur'] else [(0,0,1)]
        total=sum(p[2] for p in samples)
        for x,y,weight in samples:
            tx=ax-a*ax-c*ay+(s['x']+x*s['blur']/4)*rx;ty=ay-b*ax-d*ay+(s['y']+y*s['blur']/4)*ry
            alpha=1-(1-min(.999999,s['opacity']))**(weight/total)
            shadow_fill=color(s['color']) if gradient or color(element.get('fill'))!='none' else 'none'
            shadow_stroke=color(s['color']) if stroke!='none' else 'none'
            copies=''.join(f'<path d="{path}" fill="{shadow_fill}" stroke="{shadow_stroke}" stroke-width="{element.get("strokeWidth",1)}" vector-effect="non-scaling-stroke"/>' for path in v['paths'])
            shadow+=f'<g transform="matrix({a} {b} {c} {d} {tx} {ty})" opacity="{alpha}">{copies}</g>'
    paths=''.join(f'<path d="{d}" fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" stroke-width="{element.get("strokeWidth",1)}" stroke-opacity="{element.get("strokeOpacity",1)}" vector-effect="non-scaling-stroke"/>' for d in v['paths'])
    width=max(1,element['width']);height=max(1,element['height'])
    view_width=v['width']*width/max(.001,element['width']);view_height=v['height']*height/max(.001,element['height'])
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view_width} {view_height}" preserveAspectRatio="none" style="position:absolute;inset:0;width:{width}px;height:{height}px;overflow:visible;pointer-events:none" aria-hidden="true"><defs>{defs}</defs>{shadow}{glow}{paths}</svg>'

def validate(vector, number):
    number(vector.get('width'),.001,1000000000);number(vector.get('height'),.001,1000000000)
    paths=vector.get('paths')
    if not isinstance(paths,list) or not 1<=len(paths)<=100: raise ValueError('SVG 路径无效')
    if sum(len(d) for d in paths if isinstance(d,str))>2000000: raise ValueError('SVG 路径总长度超过 200 万字符，请精简复杂图形')
    for d in paths:
        if not isinstance(d,str) or len(d)>1000000 or not re.fullmatch(r'[MLCQZmlcqz0-9eE+.,\s-]+',d): raise ValueError('SVG 路径无效')
    for gradient in [vector.get('gradient'),vector.get('strokeGradient')]:
        if not gradient: continue
        number(gradient.get('angle'),-360,360)
        stops=gradient.get('stops')
        if not isinstance(stops,list) or not 2<=len(stops)<=100: raise ValueError('渐变色标无效')
        for stop in stops:
            number(stop.get('offset'),0,1);number(stop.get('opacity'),0,1)
            if not isinstance(stop.get('color'),str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',stop['color']): raise ValueError('渐变颜色无效')

def background(page):
    p=page.get('backgroundPattern')
    if not p:return ''
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" style="position:absolute;inset:0;pointer-events:none" aria-hidden="true"><defs><pattern id="page-pattern" width="16" height="16" patternUnits="userSpaceOnUse"><rect width="16" height="16" fill="{p["background"]}"/><path d="M -8 -8 L 24 24 M -8 8 L 8 24 M 8 -8 L 24 8 M 0 0 L 8 -8 M 8 8 L 0 16 M 16 16 L 24 8" fill="none" stroke="{p["foreground"]}" stroke-width=".6"/></pattern></defs><rect width="100%" height="100%" fill="url(#page-pattern)"/></svg>'
