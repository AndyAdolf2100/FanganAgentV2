"""Model-designed body layouts inside the authored enterprise header/footer.

Models choose geometry and source references, never rewrite source text or emit
active HTML. The host compiles the layout, tables and source-bound chart data.
The uploaded template remains immutable; this is a per-job design contract.
"""
from collections import Counter
from copy import deepcopy
import html as escape_html
import json
import math
import re
from pathlib import Path

from bs4 import BeautifulSoup

from . import template_html as html, template_component_render as fill
from ..presentation_charts import validate_chart


def text(element):
    return ''.join(r.get('text', '') for p in element.get('paragraphs', []) for r in p.get('runs', []))


def theme(template):
    colors, fonts, text_colors = Counter(), Counter(), Counter()
    for page in template['pages']:
        for e in page['elements']:
            for key in ('fill', 'stroke'):
                if re.fullmatch(r'#[0-9A-Fa-f]{6}', str(e.get(key, ''))):
                    colors[e[key].upper()] += 3
            for p in e.get('paragraphs', []):
                for r in p.get('runs', []):
                    if r.get('text', '').strip():
                        fonts[r.get('font', 'Noto Sans CJK SC')] += len(r['text'])
                        if re.fullmatch(r'#[0-9A-Fa-f]{6}', r.get('color', '')):
                            colors[r['color'].upper()] += 1
                            text_colors[r['color'].upper()] += len(r['text'])
    def chromatic(c):
        channels = [int(c[i:i+2], 16) for i in (1, 3, 5)]
        return max(channels)-min(channels) > 25
    accents = [c for c, _ in colors.most_common() if chromatic(c)]
    dark = [c for c, _ in text_colors.most_common() if sum(int(c[i:i+2], 16) for i in (1, 3, 5)) < 360]
    accent = next(iter(accents), next(iter(dark), '#223344'))
    return {'accent': accent, 'text': next(iter(dark), '#222222'),
            'palette': list(dict.fromkeys([accent, *accents, *dark, '#FFFFFF']))[:10],
            'font': next(iter(fonts), 'Noto Sans CJK SC')}


def body_contract(template, page_index):
    page = template['pages'][page_index]
    w, h = template['width'], template['height']
    candidates = [(i, e) for i, e in enumerate(page['elements']) if e['kind'] == 'text' and text(e).strip()
                  and e['y'] < h*.32 and not (e.get('labelSource') == 'manual' and e.get('textRole') in {'brand', 'decoration'})]
    # Top titles inherited from a master were often incorrectly tagged brand.
    # Prefer the actual header over a large number inside an infographic.
    candidates.sort(key=lambda pair: (0 if pair[1].get('textRole') == 'title' and pair[1].get('labelSource') == 'manual' else 1,
                                     pair[1]['y'], -pair[1]['width']))
    if not candidates:
        raise ValueError(f'第{page_index+1}页缺少可识别的顶部标题区域，请在前端标记标题')
    title_index, title = candidates[0]
    top = max(h*.16, title['y']+title['height']+h*.03)
    bottom = h*.89
    kept = []
    for i, e in enumerate(page['elements']):
        y, height = e['y'], e['height']
        background = e['kind'] != 'text' and e['width'] >= w*.9 and height >= h*.85
        # PPTX often represents the white content backdrop as an empty text
        # shape. Removing it exposes a full-slide photo behind the header.
        content_backdrop = not text(e).strip() and e['width'] >= w*.85 and height >= h*.5 and bool(re.fullmatch(r'#[0-9A-Fa-f]{6}', str(e.get('fill', ''))))
        header_band = e['kind'] != 'text' and y < h*.08 and y+height <= h*.3
        footer_band = y >= h*.75 and y+height >= h*.97
        edge = y+height <= top or y >= bottom or header_band or footer_band
        brand = e.get('labelSource') == 'manual' and e.get('textRole') == 'brand'
        if i == title_index or background or content_backdrop or edge or brand:
            kept.append(i)
            if header_band and not background:
                top = max(top, y+height+h*.02)
            if y >= h*.75 and y < bottom and not background:
                bottom = min(bottom, y-h*.02)
    frame = {'x': round(w*.05, 2), 'y': round(top, 2), 'width': round(w*.9, 2), 'height': round(bottom-top, 2)}
    if frame['height'] < h*.3:
        raise ValueError(f'第{page_index+1}页标题与页脚之间没有足够正文空间')
    return {'template_page': page_index, 'title_element': title_index, 'keep_elements': kept,
            'frame': frame, 'reference_layout': page.get('layoutKind', 'auto'),
            'protected_elements': [{'kind': page['elements'][i]['kind'], **{key: page['elements'][i][key] for key in ('x','y','width','height')}} for i in kept],
            'reference_elements': [{'kind': e['kind'], 'x': e['x'], 'y': e['y'], 'width': e['width'], 'height': e['height'],
                                    'hint': e.get('textRole'), 'sample': text(e)[:80]}
                                   for i, e in enumerate(page['elements']) if i not in kept]}


def prepare(template):
    """Derive a generation-only shell; do not relabel/save frontend templates."""
    working = deepcopy(template)
    working['_adaptive'] = True
    working['_theme'] = theme(template)
    contracts = {}
    for i, page in enumerate(working['pages']):
        if page['role'] != 'body':
            continue
        contract = body_contract(template, i)
        contracts[str(i)] = contract
        elements = [deepcopy(page['elements'][j]) for j in contract['keep_elements']]
        title_index = contract['keep_elements'].index(contract['title_element'])
        for j, e in enumerate(elements):
            if e['kind'] == 'text':
                e.update(binding='content' if j == title_index else 'fixed', fixed=j != title_index,
                         textRole='title' if j == title_index else 'brand')
        # A synthetic body region is a planning hint, not the rendered layout.
        elements.append({'kind': 'text', **contract['frame'], 'textRole': 'body', 'binding': 'content', 'order': 1,
                         'paragraphs': [{'align': 'left', 'runs': [{'text': '正文', 'font': working['_theme']['font'],
                                         'size': 24, 'color': working['_theme']['text']}]}]})
        page['elements'] = elements
        page['_adaptive_title'] = title_index
    working['_contracts'] = contracts
    return working


def table_data(block):
    lines = [line.strip().strip('|') for line in block['text'].splitlines() if line.strip() and not re.fullmatch(r'[\s|:\-]+', line)]
    rows = [[v.strip() for v in line.split('|')] for line in lines]
    if not rows or len({len(row) for row in rows}) != 1:
        raise ValueError('原稿表格列数不一致，不能生成统计图')
    has_header = any(re.fullmatch(r'[\s|:\-]+', line) for line in block['text'].splitlines())
    if not has_header and block.get('table_header'):
        return {'id': block['id'], 'kind': 'table', 'header': block['table_header'], 'rows': rows}
    return {'id': block['id'], 'kind': 'table', 'header': rows[0], 'rows': rows[1:]}


def fragments(blocks, limit=260):
    result = []
    for b in blocks:
        if b['kind'] == 'table':
            data = table_data(b)
            rows = data['rows']
            for n in range(0, max(1, len(rows)), 6):
                lines = ['|'+'|'.join(data['header'])+'|', '|'+'|'.join(['---']*len(data['header']))+'|']
                lines.extend('|'+ '|'.join(row)+'|' for row in rows[n:n+6])
                result.append({**b, 'text': '\n'.join(lines), 'ref': f'{b["id"]}_{n}'})
        elif b['kind'] == 'list' and '\n' in b['text']:
            for n, line in enumerate(b['text'].splitlines(keepends=True)):
                for part in fragments([{**b, 'kind': 'paragraph', 'text': line}], limit):
                    result.append({**part, 'kind': 'list', 'ref': f'{b["id"]}_line{n}_{part["ref"]}'})
        else:
            value = b['text']; n = 0
            while value:
                end = min(limit, len(value))
                if end < len(value):
                    cut = max(value.rfind(c, max(0, end//2), end) for c in '。；\n')
                    if cut >= 0:
                        end = cut+1
                result.append({**b, 'text': value[:end], 'ref': f'{b["id"]}_{n}'})
                value = value[end:]; n += 1
    return result


def paginate(blocks, frame):
    # Provisional grouping only. Actual Chromium feedback can split it again.
    budget = max(160, int(frame['width']/27 * frame['height']/38 * .7))
    groups, current, used = [], [], 0
    for f in fragments(blocks, min(260, budget)):
        cost = len(f['text'])+30
        if current and (used+cost > budget or f['kind'] == 'table' or current[-1]['kind'] == 'table'):
            groups.append(current); current = []; used = 0
        current.append(f); used += cost
    if current:
        groups.append(current)
    return groups


def fallback_spec(parts, frame):
    weights = [max(2, math.ceil(len(p['text'])/max(1, frame['width']/26))) for p in parts]
    available = frame['height']-16*(len(parts)-1)
    cursor, items = 0, []
    for p, weight in zip(parts, weights):
        height = available*weight/sum(weights)
        items.append({'ref': p['ref'], 'x': 0, 'y': round(cursor, 2), 'width': frame['width'], 'height': round(height, 2),
                      'font_size': 26 if p['kind'] == 'heading' else 24, 'emphasis': p['kind'] == 'heading'})
        cursor += height+16
    return {'items': items, 'charts': [], 'rationale': '全文保留的基础排版；模型未完成的设计会明确记录'}


def valid_box(item, frame):
    values = [item.get(k) for k in ('x', 'y', 'width', 'height')]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('排版区域必须使用有限数值坐标')
    x, y, w, h = values
    if min(x, y) < 0 or w < 25 or h < 20 or x+w > frame['width']+.1 or y+h > frame['height']+.1:
        raise ValueError('正文排版越过企业页眉、页脚或内容区边界')


def validate_spec(spec, parts, frame):
    if not isinstance(spec, dict) or not isinstance(spec.get('items'), list):
        raise ValueError('缺少正文排版items')
    if Counter(p.get('ref') for p in spec['items']) != Counter(p['ref'] for p in parts):
        raise ValueError('正文引用必须完整且恰好一次，不能删减或重复原稿；允许ref：'+str([p['ref'] for p in parts])+'；收到ref：'+str([p.get('ref') for p in spec['items']]))
    for item in spec['items']:
        valid_box(item, frame)
        size = item.get('font_size', 24)
        if type(size) not in (int, float) or not 18 <= size <= 44:
            raise ValueError('正文字号须为18–44px，放不下应续页')
        if item.get('align', 'left') not in {'left', 'center', 'right'}:
            raise ValueError('无效对齐方式')
        if item.get('tone', 'plain') not in {'plain', 'accent', 'tint'}:
            raise ValueError('只能使用企业主题配色')
    charts = spec.get('charts', [])
    if not isinstance(charts, list) or len(charts) > 1:
        raise ValueError('每页最多一个统计图')
    for chart in charts:
        valid_box(chart, frame)
        if chart['width'] < 850 or chart['height'] < 280:
            raise ValueError('统计图区域至少850×280，保证标签可读')
        chart_spec(chart, parts)
    return deepcopy(spec)


def chart_spec(request, parts):
    block = next((p for p in parts if p['ref'] == request.get('ref') and p['kind'] == 'table'), None)
    if block is None:
        raise ValueError('统计图只能引用当前页原稿表格，不能使用模板示例数据')
    data = table_data(block); col = request.get('column', 1)
    if type(col) is not int or not 1 <= col < len(data['header']):
        raise ValueError('无效统计列')
    raw = {'type': request.get('type', 'bar'), 'categories': [row[0] for row in data['rows']],
           'unit': request.get('unit', ''), 'ordered': request.get('ordered', False),
           'series': [{'name': data['header'][col], 'source_refs': [
               {'block_id': block['id'], 'row': n, 'column': col} for n in range(len(data['rows']))]}]}
    return validate_chart(raw, {block['id']: data}, [block['id']])


def compile_body(template, page):
    index = page['template_page']; contract = template['_contracts'][str(index)]
    frame = contract['frame']; parts = page['body_parts']
    spec = validate_spec(page['layout_spec'], parts, frame)
    palette = template['_theme']; layout = template['pages'][index]
    reference = html.template_html(template, index)
    soup = BeautifulSoup(reference, 'html.parser')
    # Replace header text in its original runs. Never shrink the authored title.
    title = soup.select_one(f'[data-ppt-editable="{layout["_adaptive_title"]}"]')
    fill.text_only(title, page['title'])
    title['data-edit-mode'] = 'text-only'
    for i, element in enumerate(layout['elements'][:-1]):
        if element['kind'] == 'text':
            outer = soup.select_one(f'[data-template-element="{i}"]')
            outer['data-ppt-slot'] = f'fixed-{i}'
    body_outer = soup.select_one(f'[data-template-element="{len(layout["elements"])-1}"]')
    body_outer.clear()
    body_outer['data-enterprise-body'] = 'true'
    # Independent slots expose collisions between newly designed content blocks.
    for key in ('data-ppt-slot', 'data-ppt-text-check', 'data-edit-mode', 'data-ppt-min-font-size'):
        body_outer.attrs.pop(key, None)
    esc = escape_html.escape
    font = re.sub(r'[^\w\s,-]', '', palette['font'])
    def rect(item):
        return ';'.join(f'{k}:{item[k]}px' for k in ('width', 'height'))+f';left:{item["x"]}px;top:{item["y"]}px'
    ordered = sorted(spec['items'], key=lambda item: next(i for i,p in enumerate(parts) if p['ref']==item['ref']))
    for n, item in enumerate(ordered):
        part = next(p for p in parts if p['ref'] == item['ref'])
        tone = item.get('tone', 'plain'); color = palette['text']
        background = 'transparent'
        if tone == 'accent':
            background = palette['accent']; channels = [int(background[i:i+2], 16) for i in (1, 3, 5)]
            color = '#FFFFFF' if sum(channels)/3 < 150 else '#111111'
        if tone == 'tint':
            background = '#'+''.join(f'{round(int(palette["accent"][i:i+2],16)*.08+255*.92):02X}' for i in (1,3,5))
        style = f'position:absolute;box-sizing:border-box;{rect(item)};font-family:{font};font-size:{item.get("font_size",24)}px;color:{color};background:{background};line-height:1.4;text-align:{item.get("align","left")};font-weight:{700 if item.get("emphasis") else 400};padding:{12 if tone!="plain" else 0}px;overflow:visible;overflow-wrap:anywhere'
        node = soup.new_tag('div', attrs={'data-ppt-slot': f'body-{n}', 'data-ppt-min-font-size': '18', 'style': style})
        if part['kind'] == 'table':
            data = table_data(part)
            markup = '<table data-source-block="'+esc(part['id'], quote=True)+'" style="width:100%;table-layout:fixed;border-collapse:collapse;font:inherit">'
            for ri, row in enumerate([data['header'], *data['rows']]):
                markup += '<tr>'
                for value in row:
                    tag = 'th' if ri == 0 else 'td'
                    markup += f'<{tag} style="padding:6px 8px;border-bottom:1px solid {palette["accent"]};text-align:left">{esc(value)}</{tag}>'
                markup += '</tr>'
            markup += '</table>'
        else:
            markup = f'<div data-source-block="{esc(part["id"],quote=True)}" style="white-space:pre-wrap">{esc(part["text"])}</div>'
        node.append(BeautifulSoup(markup, 'html.parser')); body_outer.append(node)
    chart_specs = []
    for n, c in enumerate(spec.get('charts', [])):
        chart_specs.append({'id': f'chart-{n}', 'chart': chart_spec(c, parts)})
        body_outer.append(soup.new_tag('div', attrs={'data-enterprise-chart': f'chart-{n}',
                                                   'style': 'position:absolute;'+rect(c)}))
    page['enterprise_charts'] = chart_specs
    return str(soup)


DESIGN_POLICY = '''你是项目Agent的企业PPT正文设计师。原模板中央只是设计参考，自动标签不是约束。
保留企业页眉页脚及标题区域，按内容关系在给定frame内重新排版，坐标相对frame左上角。
复用正常PPT设计原则：视觉主次、非对称布局、时间轴、对比、信息分组；不要每页都等宽卡片。
所有来源引用ref必须且只用一次，不输出文案。表格由宿主按原稿单元格生成。
只返回JSON {"rationale":"布局理由","items":[{"ref":"来源ref","x":0,"y":0,"width":900,"height":120,"font_size":24,"align":"left","emphasis":false,"tone":"plain"}],"charts":[]}。
字号18–44px，正文优先24–28px；tone仅plain/accent/tint，由宿主映射企业主题色；禁止放不下就压缩/隐藏文字。
统计图仅当原稿表格提供明确可比单值时使用，可选bar/column/line/donut。charts项为
{"ref":"表格ref","type":"bar","column":1,"unit":"万元","x":0,"y":0,"width":1000,"height":280,"ordered":false}。
保留原始表格items，图表是辅助对比，二者不得重叠。统计图至少850×280px；放不下优先完整表格，不强加图表。
不能照抄模板示例数字，不能把范围/约数转换为单值。纵横边界、文字高度都要预留余量。
若提供current_layout与finding，修复布局并保留所有来源ref。只返回JSON，不输出HTML。'''


def design(template, page, call, finding=None):
    contract = template['_contracts'][str(page['template_page'])]
    payload = {'title': page['title'], 'frame': contract['frame'], 'visual_dna': template['_theme'],
               'template_reference': contract['reference_elements'], 'content': page['body_parts'],
               'protected_elements': contract['protected_elements'],
               'current_layout': page.get('layout_spec'), 'finding': finding}
    common = (Path(__file__).resolve().parents[2]/'presentation/skills/marketing-deck/references/page-generation.md').read_text()
    principles = common.split('视觉判断：', 1)[-1].split('结果必须', 1)[0]
    policy = DESIGN_POLICY+'\n以下复用普通PPT排版原则；输出仍须为上述布局JSON、使用当前企业frame和主题：\n'+principles
    return validate_spec(call(policy, payload), page['body_parts'], contract['frame'])
