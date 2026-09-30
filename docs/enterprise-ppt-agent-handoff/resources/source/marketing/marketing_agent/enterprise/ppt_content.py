"""Lossless continuation pages for manuscript content and oversized slide text."""
import re
from html import escape

MAX_PAGES = 100
# Accept older/model intermediate plans before fitting the final deliverable.
MAX_PLAN_PAGES = 300


def compact(text):
    return re.sub(r'\s+', '', text)


def fit_page_budget(outline, manuscript):
    """Remove duplicated summary/appendix pages before reducing text size."""
    if len(outline['slides']) <= MAX_PAGES:
        return outline
    source = re.sub(r'\n[ \t]*\n+', '\n', manuscript.strip())
    if not source:
        raise ValueError('缺少完整文稿，无法在100页内重新排版。')
    opening = [p for p in outline['slides'] if p.get('layout') in ('cover','agenda')]
    ending = [p for p in outline['slides'] if p.get('layout') == 'ending'][-1:]
    available = MAX_PAGES - len(opening) - len(ending)
    # 1280x720 canvas: conservative line width and height budgets, minimum 18px.
    for font, line_height, columns, rows in ((24,34,76,14),(22,30,88,16),(20,28,100,18),(18,24,112,21)):
        chunks = text_pages(source, columns=columns, rows=rows)
        if len(chunks) <= available:
            body = [{'title':f'完整文稿 · {n+1}', 'content':chunk,
                     'layout':'manuscript','_verbatim':True,
                     '_text_font':font,'_text_line_height':line_height}
                    for n,chunk in enumerate(chunks)]
            assert compact(''.join(chunks)) == compact(source)
            return {**outline, 'slides':opening + body + ending,
                    'manuscript_continuations':0, 'page_limit':MAX_PAGES,
                    'pagination_mode':'full_manuscript', 'body_font_size':font}
    raise ValueError('完整文稿在正文不小于18px的情况下仍无法放入100页。请拆分文稿或明确允许精简内容；未删减正文、未生成超限PPT。')


def text_pages(text, columns=76, rows=14):
    """Conservative visual line budget; no character is discarded."""
    lines, line, width = [], '', 0
    for char in text:
        # Count even wide Latin letters as a full em; tab expansion is bounded.
        size = 8 if char == '\t' else 2
        if char == '\n':
            lines.append(line); line, width = '', 0
        else:
            if width + size > columns:
                lines.append(line); line, width = '', 0
            line += char; width += size
    if line:
        lines.append(line)
    return ['\n'.join(lines[i:i + rows]) for i in range(0, len(lines), rows)]


def ensure_manuscript(outline, manuscript):
    """Keep designed pages; add exact source passages the planner did not retain."""
    pages = outline['slides']
    represented = compact('\n'.join(
        '\n'.join([p.get('title', ''), p.get('subtitle', ''), p.get('content', '')]
                  + [i.get('title', '') + '\n' + i.get('body', '') for i in p.get('items', [])])
        for p in pages))
    # Compare small source units across slide boundaries. Formatting and comma
    # variants do not justify re-appending an entire already represented paragraph.
    def normalized(value):
        value = re.sub(r'(?m)^\s*#{1,6}\s+', '', value)
        value = value.replace('**','').replace('`','')
        return re.sub(r'[\s，,、；;。！？!?]', '', value)
    represented = normalized(represented)
    missing = []
    for paragraph in re.split(r'\n\s*\n', manuscript.strip()):
        if not paragraph.strip() or normalized(paragraph) in represented:
            continue
        units = re.findall(r'[^\n，,、；;。！？!?]+[\n，,、；;。！？!?]*', paragraph)
        remainder = ''.join(unit for unit in units if normalized(unit) and normalized(unit) not in represented)
        if remainder.strip(): missing.append(remainder)
    additions = [{'title':f'文稿补充 · {n+1}', 'content':text,
                  'layout':'manuscript', '_verbatim':True}
                 for n,text in enumerate(text_pages('\n\n'.join(missing)))]
    # Keep the closing slide last. All new pages are checkpointed like regular slides.
    position = len(pages) - 1 if pages and pages[-1].get('layout') == 'ending' else len(pages)
    pages[position:position] = additions
    outline['manuscript_continuations'] = len(additions)
    return fit_page_budget(outline, manuscript)


def render_text(page, index, total, design='', brand=''):
    """Measured line budgets with normal flow; no model can omit this text."""
    colors = re.findall(r'#[0-9a-fA-F]{6}\b', design)
    accent = '#28166b' if brand else (colors[0] if colors else '#285f52')
    font = page.get('_text_font',24)
    line_height = {24:34,22:30,20:28,18:24}.get(font,34)
    if font not in (18,20,22,24):
        font = 24
    artwork = f'<div class="brand" style="background-image:url({escape(brand, quote=True)})"></div>' if brand else ''
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>{escape(page['title'])}</title>
<style>*{{box-sizing:border-box}}html,body{{margin:0}}.ppt-slide{{width:1280px;height:720px;background:#f7f7f4;color:#252a29;padding:36px 70px;display:flex;flex-direction:column;font-family:"Microsoft YaHei","PingFang SC",sans-serif}}header{{display:flex;align-items:center;justify-content:space-between;min-height:82px;border-bottom:8px solid {accent};margin-bottom:24px}}h1{{font-size:28px;margin:0;max-width:730px;line-height:1.35}}.brand{{width:310px;height:72px;background-size:1040px 585px;background-position:right top;background-repeat:no-repeat}}.text{{font-size:{font}px;line-height:{line_height}px;white-space:pre-wrap;overflow-wrap:anywhere;margin:0;flex:1}}footer{{height:24px;text-align:right;font-size:16px;color:#606862}}</style></head>
<body><main class="ppt-slide"><header><h1>{escape(page['title'])}</h1>{artwork}</header><div class="text">{escape(page['content'])}</div><footer>{index+1} / {total}</footer></main></body></html>'''
