"""Local enterprise template library and deterministic rendering of imported PPTX layouts."""
import base64
import copy
import json
import math
import os
import re
import tempfile
import threading
from html import escape
from pathlib import Path
from .ppt_content import text_pages, MAX_PLAN_PAGES
from . import template_vector

ROOT = Path(os.getenv('LOCAL_UPLOADED_TEMPLATE_DIR', '/app/uploaded-ppt-templates'))
LOCK = threading.RLock()
MAX_TEMPLATE_PAGE_CHARACTERS = 8_000_000
MAX_ELEMENTS_PER_PAGE = 2000
MAX_ELEMENTS_PER_TEMPLATE = 20000

ROLES = {'cover', 'contents', 'section', 'body', 'ending', 'exclude'}

def valid_id(value):
    if not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('模板 ID 无效')
    return value

def read(template_id, revision=None):
    valid_id(template_id)
    if revision is not None and (type(revision) is not int or revision < 1):
        raise ValueError('模板版本无效')
    filename = f'{template_id}.json' if revision is None else f'{template_id}-v{revision}.json'
    return json.loads((ROOT / filename).read_text())

def listing():
    if not ROOT.exists(): return []
    return sorted([json.loads(p.read_text()) for p in ROOT.glob('*.json') if re.fullmatch(r'[a-f0-9]{64}', p.stem)], key=lambda t:t.get('createdAt',0), reverse=True)

def number(value, minimum=-10000, maximum=10000):
    if type(value) not in (float, int) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('模板坐标或尺寸无效')
    return value

def binding(element):
    return element.get('binding', 'fixed' if element.get('fixed') or element['kind'] != 'text' else 'content')

def validate(template):
    if not isinstance(template, dict) or template.get('version') != 1: raise ValueError('模板结构无效')
    valid_id(template.get('id',''))
    if not isinstance(template.get('name'), str) or not 1 <= len(template['name'].strip()) <= 200: raise ValueError('模板名称不能为空或超过200字')
    number(template.get('width'), 200, 2400); number(template.get('height'), 200, 2400)
    assets = template.get('assets', [])
    if not isinstance(assets,list) or len(assets)>400: raise ValueError('模板图片过多')
    ids=set(); total=0
    for asset in assets:
        if not re.fullmatch(r'asset-\d+',asset.get('id','')) or asset['id'] in ids: raise ValueError('图片标识无效')
        ids.add(asset['id'])
        if asset.get('mime') not in ('image/png','image/jpeg','image/gif','image/webp'): raise ValueError('图片类型无效')
        data=base64.b64decode(asset.get('data',''),validate=True);total+=len(data)
        if total>120*1024*1024: raise ValueError('模板图片过大')
        if not data.startswith((b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff',b'GIF87a',b'GIF89a',b'RIFF')): raise ValueError('图片内容无效')
    def pages_check(pages):
        if not isinstance(pages,list) or not 1<=len(pages)<=40: raise ValueError('模板必须包含1–40页')
        total_elements=0
        for page_index,page in enumerate(pages,1):
            if not isinstance(page,dict): raise ValueError(f'第 {page_index} 页版式数据不是对象，请重新导入')
            if page.get('role') not in ROLES: raise ValueError(f'第 {page_index} 页用途无效：{str(page.get("role"))[:60]}，请重新导入')
            if not isinstance(page.get('elements'),list): raise ValueError(f'第 {page_index} 页元素列表无效，请重新导入')
            if len(page['elements'])>MAX_ELEMENTS_PER_PAGE: raise ValueError(f'第 {page_index} 页包含 {len(page["elements"])} 个元素，超过单页 {MAX_ELEMENTS_PER_PAGE} 个元素限制，请精简该页后导入')
            total_elements+=len(page['elements'])
            if total_elements>MAX_ELEMENTS_PER_TEMPLATE: raise ValueError(f'模板元素总数超过 {MAX_ELEMENTS_PER_TEMPLATE} 个，请精简后导入')
            if 'backgroundPattern' in page:
                pattern=page['backgroundPattern']
                if not isinstance(pattern,dict) or pattern.get('type')!='diagBrick' or any(not re.fullmatch(r'#[a-fA-F0-9]{6}',pattern.get(k,'')) for k in ('foreground','background')): raise ValueError('背景纹理无效')
            if page.get('layoutKind', 'auto') not in {'auto','text','items','comparison','timeline','table','custom'}: raise ValueError('版式类型无效')
            for e in page['elements']:
                if 'fieldName' in e and (not isinstance(e['fieldName'], str) or e['fieldName'] and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', e['fieldName'])): raise ValueError('填充字段名无效')
                if e.get('kind') not in ('text','image','shape') or binding(e) not in ('fixed','content','pageNumber'): raise ValueError('元素类型无效')
                for key in ('x','y','width','height'): number(e.get(key), 0 if key in ('width','height') else -10000)
                number(e.get('rotation',0))
                if e.get('matrix') is not None:
                    if not isinstance(e['matrix'],list) or len(e['matrix']) != 4: raise ValueError('元素变换无效')
                    for v in e['matrix']: number(v, -100, 100)
                if e.get('renderAsset') and e['renderAsset'] not in ids: raise ValueError('图片渲染资源无效')
                if 'imageColorChange' in e:
                    effect=e['imageColorChange']
                    if e['kind']!='image' or not isinstance(effect,dict) or any(not isinstance(effect.get(k),str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',effect[k]) for k in ('from','to')): raise ValueError('图片替换颜色无效')
                    number(effect.get('opacity'),0,1)
                if 'artworkType' in e:
                    if e['artworkType'] != 'outlinedText' or e['kind'] != 'shape' or not e.get('vector') or e.get('binding') != 'fixed': raise ValueError('轮廓文字必须保留为固定 SVG 图形')
                if 'artworkTextRole' in e:
                    if e.get('artworkType') != 'outlinedText' or e['artworkTextRole'] not in {'title','subtitle','sectionNumber','body','brand','decoration'}:
                        raise ValueError('矢量文字用途无效')
                if e.get('textRole'):
                    from .template_labels import TEXT_ROLES
                    if e['kind'] != 'text' or e['textRole'] not in TEXT_ROLES: raise ValueError('文字标签无效')
                    if type(e.get('order')) is not int or not 1 <= e['order'] <= MAX_ELEMENTS_PER_PAGE: raise ValueError('文字填充顺序无效')
                    expected = 'fixed' if e['textRole'] in {'brand','decoration'} else 'pageNumber' if e['textRole'] == 'pageNumber' else 'content'
                    if binding(e) != expected: raise ValueError('文字标签与元素用途不一致')
                if e.get('asset') and e['asset'] not in ids: raise ValueError('模板引用缺失图片')
                for v in e.get('crop',{}).values(): number(v,-10,10)
                for v in e.get('imageStretch',{}).values(): number(v,-10,10)
                if 'textFlip' in e and (not isinstance(e['textFlip'],dict) or any(type(e['textFlip'].get(k)) is not bool for k in ('horizontal','vertical'))): raise ValueError('文字翻转无效')
                if 'textLayout' in e:
                    layout=e['textLayout']
                    if layout.get('vertical') not in {'top','center','bottom'} or type(layout.get('wrap')) is not bool or type(layout.get('overflow')) is not bool: raise ValueError('文字排版无效')
                    if not isinstance(layout.get('padding'),list) or len(layout['padding'])!=4: raise ValueError('文字内边距无效')
                    for v in layout['padding']: number(v,0,1000)
                for key in ('cornerRadius','strokeWidth','blur'):
                    if key in e: number(e[key],0,2400)
                if 'glow' in e:
                    glow=e['glow']
                    if not isinstance(glow,dict) or not re.fullmatch(r'#[a-fA-F0-9]{6}',glow.get('color','')): raise ValueError('发光颜色无效')
                    number(glow.get('opacity'),0,1);number(glow.get('radius'),0,2400)
                if 'shadow' in e:
                    shadow=e['shadow']
                    if not isinstance(shadow,dict) or not re.fullmatch(r'#[a-fA-F0-9]{6}',shadow.get('color','')): raise ValueError('阴影颜色无效')
                    for key in ('opacity','alignX','alignY'): number(shadow.get(key),0,1)
                    number(shadow.get('blur'),0,2400)
                    for key in ('x','y'): number(shadow.get(key))
                    for key in ('scaleX','scaleY'): number(shadow.get(key),-100,100)
                    for key in ('skewX','skewY'): number(shadow.get(key),-89,89)
                if 'strokeOpacity' in e: number(e['strokeOpacity'],0,1)
                if 'imageFlip' in e and (not isinstance(e['imageFlip'],dict) or any(type(e['imageFlip'].get(k)) is not bool for k in ('horizontal','vertical'))): raise ValueError('图片翻转无效')
                if 'imageClip' in e: template_vector.validate(e['imageClip'], number)
                if 'vector' in e: template_vector.validate(e['vector'], number)
                if 'fillOpacity' in e: number(e['fillOpacity'],0,1)
                if 'clipPolygon' in e:
                    points=e['clipPolygon']
                    if not isinstance(points,list) or not 3 <= len(points) <= 500: raise ValueError('图形裁剪无效')
                    for point in points:
                        if not isinstance(point,list) or len(point)!=2: raise ValueError('图形裁剪无效')
                        for v in point: number(v)
                for p in e.get('paragraphs',[]):
                    if p.get('lineHeight') is not None: number(p['lineHeight'],.1,10)
                    for r in p.get('runs',[]):
                        if 'sourceFont' in r and (not isinstance(r['sourceFont'],str) or len(r['sourceFont'])>200): raise ValueError('原字体信息无效')
                        if 'shadow' in r:
                            shadow=r['shadow']
                            if not isinstance(shadow,dict) or not re.fullmatch(r'#[a-fA-F0-9]{6}',shadow.get('color','')): raise ValueError('文字阴影颜色无效')
                            number(shadow.get('opacity'),0,1);number(shadow.get('blur'),0,2400)
                            for key in ('x','y'): number(shadow.get(key))
                        if not isinstance(r.get('text'),str) or len(r['text'])>20000: raise ValueError('文字内容无效')
                        number(r.get('size'),1,1000)
                        if 'opacity' in r: number(r['opacity'],0,1)
                        if not isinstance(r.get('font'),str) or len(r['font'])>200: raise ValueError('字体无效')
    pages_check(template.get('pages'))
    if template.get('published'):
        pub=template['published'];pages_check(pub.get('pages'))
        if type(pub.get('revision')) is not int or pub['revision']<1: raise ValueError('发布版本无效')
        bodies=[p for p in pub['pages'] if p['role']=='body' and any((e['kind']=='text' and binding(e)=='content') or (e.get('artworkType')=='outlinedText' and e.get('artworkTextRole')=='body') for e in p['elements'])]
        if not bodies: raise ValueError('请设置至少一个包含正文内容区域的正文版式后发布')
        if any(e.get('textRole') for p in pub['pages'] for e in p['elements']) and not any(e.get('textRole') in {'body','itemBody'} or (e.get('artworkType')=='outlinedText' and e.get('artworkTextRole')=='body') for p in bodies for e in p['elements']): raise ValueError('请标记至少一个正文或分项正文区域后发布')
    return template

def atomic(path, data):
    ROOT.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=ROOT,delete=False) as f:
        json.dump(data,f,ensure_ascii=False); temp=f.name
    os.replace(temp,path)

def save(template):
    validate(template)
    template = copy.deepcopy(template)
    with LOCK:
        try: old=read(template['id'])
        except FileNotFoundError: old=None
        if not old and len(listing())>=40: raise ValueError('最多保存40套企业模板')
        pub=template.get('published'); prev=(old or {}).get('published')
        if pub != prev:
            if not pub or pub['revision'] != (prev or {}).get('revision',0)+1: raise ValueError('模板版本已变化，请刷新列表后重试')
            # Deleting the library entry deliberately retains immutable revisions.
            # Reimporting the same file restores its ID, so allocate beyond those
            # revisions under the same lock instead of attempting to replace v1.
            revisions = [int(match[1]) for path in ROOT.glob(f"{template['id']}-v*.json")
                         if (match := re.fullmatch(r'[a-f0-9]{64}-v([1-9][0-9]*)\.json', path.name))]
            pub['revision'] = max(pub['revision'], max(revisions, default=0) + 1)
            snapshot={**template,'pages':pub['pages'],'name':pub['name']}
            if pub.get('componentSchema') == 1:
                from .template_components import inspect
                report = inspect(snapshot)
                if not report['valid']: raise ValueError('模板发布检查未通过：' + '；'.join(report['errors']))
            archive = ROOT/f"{template['id']}-v{pub['revision']}.json"
            if archive.exists() and json.loads(archive.read_text()) != snapshot: raise ValueError('此版本已有历史任务引用，请使用新的版本号')
            for page_index,page in enumerate(pub['pages'],1):
                size=len(render(snapshot,page))
                if size>MAX_TEMPLATE_PAGE_CHARACTERS:
                    raise ValueError(f'第 {page_index} 页完整内容为 {size/1000000:.2f} 百万字符，超过单页 {MAX_TEMPLATE_PAGE_CHARACTERS/1000000:g} 百万字符限制（包括内嵌图片、SVG 和阴影），请精简该页资源后重新导入')
            atomic(archive,snapshot)
        atomic(ROOT/f"{template['id']}.json",template)
    return template

def remove(template_id):
    # Published revisions remain available to in-flight/history tasks.
    with LOCK: (ROOT/f'{valid_id(template_id)}.json').unlink()

def color(value, default='transparent'):
    return value if isinstance(value,str) and re.fullmatch(r'#[a-fA-F0-9]{6}',value) else default

def render(template,page,values=None,page_number=1,slot_styles=None,layout_variant=None,annotate=False):
    from .template_body_layout import resolve_layout
    page = resolve_layout(template, page, layout_variant)
    assets={a['id']:f"data:{a['mime']};base64,{a['data']}" for a in template['assets']}
    elements=[]
    for i,e in enumerate(page['elements']):
        identity = f' data-template-element="{i}"' if annotate else ''
        style=f"position:absolute;left:{e['x']}px;top:{e['y']}px;width:{e['width']}px;height:{e['height']}px;transform:rotate({e.get('rotation',0)}deg);box-sizing:border-box;"
        if e.get('matrix'):
            style += 'transform:matrix(' + ','.join(str(v) for v in e['matrix']) + ',0,0);transform-origin:0 0;'
        if e.get('blur'): style+=f'filter:blur({e["blur"]}px);'
        if e.get('clipPolygon') and not e.get('vector'):
            style += 'clip-path:polygon(' + ','.join(f'{x}% {y}%' for x,y in e['clipPolygon']) + ');'
        if e['kind']=='image':
            clip=e.get('imageClip');clip_svg=''
            if clip:
                clip_id=f'image-clip-{i}'
                paths=''.join(f'<path d="{d}" transform="scale({1/clip["width"]} {1/clip["height"]})"/>' for d in clip['paths'])
                clip_svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true"><defs><clipPath id="{clip_id}" clipPathUnits="objectBoundingBox">{paths}</clipPath></defs></svg>'
                style+=f'clip-path:url(#{clip_id});'
            crop=e.get('crop',{});l=crop.get('left',0);r=crop.get('right',0);t=crop.get('top',0);b=crop.get('bottom',0)
            if 1-l-r<=0 or 1-t-b<=0: continue
            fill=e.get('imageStretch',{});fl=fill.get('left',0);ft=fill.get('top',0)
            fw=1-fl-fill.get('right',0);fh=1-ft-fill.get('bottom',0)
            image_flip=e.get('imageFlip',{})
            image_transform=f'scale({-1 if image_flip.get("horizontal") else 1},{-1 if image_flip.get("vertical") else 1})'
            elements.append(f'{clip_svg}<div{identity} style="{style}overflow:hidden"><div style="position:absolute;inset:0;transform:{image_transform}"><div style="background-image:url({assets.get(e.get("renderAsset") or e.get("asset"), "")});background-size:100% 100%;position:absolute;width:{100*fw/(1-l-r)}%;height:{100*fh/(1-t-b)}%;left:{(fl-l*fw/(1-l-r))*100}%;top:{(ft-t*fh/(1-t-b))*100}%"></div></div></div>');continue
        fill_color=color(e.get('fill'))
        if 'fillOpacity' in e and fill_color != 'transparent': fill_color += f"{round(e['fillOpacity']*255):02x}"
        vector=template_vector.svg(e,i)
        if not vector:
            style+=f"background:{fill_color};border:{e.get('strokeWidth',1) if color(e.get('stroke'))!='transparent' else 0}px solid {color(e.get('stroke'))};border-radius:{'50%' if e.get('geometry')=='ellipse' else str(e.get('cornerRadius',12))+'px' if e.get('geometry')=='roundRect' else '0'};"
        paragraphs=copy.deepcopy(e.get('paragraphs',[]))
        if values is not None and binding(e)!='fixed':
            value=str(page_number) if binding(e)=='pageNumber' else values.get(str(i),'')
            if any(p.get('runs') for p in paragraphs):
                # One replacement paragraph: old empty sample paragraphs must not
                # consume height after filling a slot.
                paragraph = next(p for p in paragraphs if p.get('runs')); run = paragraph['runs'][0]
                run['text'] = value
                style_override = (slot_styles or {}).get(str(i))
                if style_override:
                    from .template_capacity import BODY_RESIZABLE_ROLES, original_size
                    if page['role'] != 'body' or e.get('textRole', 'body' if binding(e)=='content' else '') not in BODY_RESIZABLE_ROLES:
                        raise ValueError('仅允许调整正文内容区域的字号')
                    font = style_override['fontSize']
                    minimum = 16 if layout_variant else 18
                    if not min(original_size(e),minimum*template['height']/720)-.01 <= font <= original_size(e)+.01:
                        raise ValueError('正文调整字号超出可读范围')
                    run['size'] = font
                    paragraph['lineHeight'] = 1.25
                paragraph['runs'] = [run]; paragraphs = [paragraph]
        is_number=e.get('textRole') in {'contentsNumber','sectionNumber','pageNumber'}
        layout=e.get('textLayout',{})
        matrix=e.get('matrix',[1,0,0,1]);reflected=matrix[0]*matrix[3]-matrix[1]*matrix[2]<0
        flip=e.get('textFlip',{'horizontal':reflected and matrix[0]<0,'vertical':reflected and matrix[0]>=0})
        text_transform=f'scale({-1 if flip["horizontal"] else 1},{-1 if flip["vertical"] else 1})'
        text_whitespace='pre' if is_number or layout.get('wrap') is False else 'pre-wrap'
        text_wrap='normal' if is_number or layout.get('wrap') is False else 'anywhere'
        text_padding='0' if is_number else '3px 6px'
        if layout: text_padding=' '.join(f'{v}px' for v in layout['padding'])
        text_overflow='visible' if is_number or layout.get('overflow') else 'hidden'
        vertical={'top':'flex-start','center':'center','bottom':'flex-end'}.get(layout.get('vertical'),'flex-start')
        texts=[]
        for p in paragraphs:
            spans=[]
            for run in p['runs']:
                font='system-ui,-apple-system,BlinkMacSystemFont' if run['font']=='system-ui' else re.sub(r'[^\w\s-]','',run['font'])
                shadow=run.get('shadow');shadow_style=''
                if shadow: shadow_style=f'text-shadow:{shadow["x"]}px {shadow["y"]}px {shadow["blur"]}px {shadow["color"]}{round(shadow["opacity"]*255):02x};'
                spans.append(f'<span style="font-family:{escape(font,quote=True)},sans-serif;font-size:{run["size"]}px;color:{color(run.get("color"),"#222222")};opacity:{run.get("opacity",1)};font-weight:{700 if run.get("bold") else 400};{shadow_style}">{escape(run["text"])}</span>')
            align=p.get('align') if p.get('align') in ('left','center','right') else 'left'
            texts.append(f'<p style="flex:none;margin:0;font-size:{max((r["size"] for r in p["runs"]),default=16)}px;line-height:{p.get("lineHeight") or 1.2};white-space:{text_whitespace};overflow-wrap:{text_wrap};text-align:{align}">{"".join(spans)}</p>')
        artwork=' data-artwork-type="outlinedText"' if e.get('artworkType')=='outlinedText' else ''
        allow_overflow = 'true' if layout.get('overflow') and page['role'] != 'body' else 'false'
        marker = f' data-ppt-allow-overflow="{allow_overflow}" data-ppt-slot="{i}" data-ppt-role="{escape(e.get("textRole","text"),quote=True)}"' if values is not None and binding(e)!='fixed' and paragraphs else ''
        elements.append(f'<div{identity}{artwork}{marker} style="{style}padding:{text_padding};overflow:{text_overflow}">{vector}<div style="position:relative;transform:{text_transform};height:100%;display:flex;flex-direction:column;justify-content:{vertical}">{"".join(texts)}</div></div>')
    w,h=template['width'],template['height']
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="ppt-width" content="{w}"><meta name="ppt-height" content="{h}"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'"><style>html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden}}body{{position:relative;background:{color(page.get("background"),"#FFFFFF")}}}</style></head><body><div class="ppt-slide" style="position:relative;width:{w}px;height:{h}px">{template_vector.background(page)}{"".join(elements)}</div></body></html>'

def plan(template,manuscript):
    from .template_plan import plan as semantic_plan
    # Older templates without semantic labels use the same capacity planner.
    normalized = copy.deepcopy(template)
    for page in normalized['published']['pages']:
        for e in page['elements']:
            if not e.get('textRole') and e.get('kind') == 'text' and binding(e) == 'content':
                e['textRole'] = 'body' if page['role'] == 'body' else 'title'
    return semantic_plan(normalized, manuscript)
