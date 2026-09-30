"""HTML generation contract: editable text islands inside an immutable template."""
import hashlib
import json
import re
from collections import Counter

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from . import uploaded_templates as library
from .ppt_content import compact

MAX_PAGES = 200

STRATEGY = 'model-template-html'
ROLES = {'cover', 'contents', 'section', 'body', 'ending'}
RULES = {
    'cover': '首页必须恰好一页且位于第一页面；只能改文字，保留文字样式和其他全部元素。',
    'ending': '尾页必须恰好一页且位于最后；只能改文字，保留文字样式和其他全部元素。',
    'contents': '目录文字过多可复制同一目录模板续页；保留字号和固定元素，不压缩或遗漏目录项。',
    'section': '章节页只能改章节序号和标题，副标题、说明及其他元素全部保持原样。',
    'body': '正文文字可重新排版、调整字号和段落，可续页；只在文字区域内调整，不得改变非文字元素。',
}


def editable_role(element, page_role):
    vector = element.get('artworkType') == 'outlinedText'
    role = element.get('artworkTextRole', 'title') if vector else element.get('textRole', 'body' if page_role == 'body' else 'title')
    if role in {'brand', 'decoration'} or (not vector and library.binding(element) == 'fixed'):
        return None
    if element['kind'] != 'text' and not vector:
        return None
    if page_role == 'section' and role not in {'title', 'sectionNumber'}:
        return None
    return role


def template_html(template, page_index):
    """Do not fill text. Send the actual template HTML, annotated with edit permissions."""
    page = template['pages'][page_index]
    soup = BeautifulSoup(library.render(template, page, annotate=True), 'html.parser')
    soup.select_one('.ppt-slide')['data-page-role'] = page['role']
    for i, element in enumerate(page['elements']):
        role = editable_role(element, page['role'])
        outer = soup.select_one(f'[data-template-element="{i}"]')
        if not role or outer is None:
            continue
        vector = element.get('artworkType') == 'outlinedText'
        if vector:
            region = soup.new_tag('div', style='position:relative;width:100%;height:100%')
            for child in list(outer.contents):
                region.append(child.extract())
            outer.append(region)
        else:
            # A text shape can also have its own vector background. Only its text
            # child is editable; its fill, border, SVG, transform and box stay fixed.
            region = outer.find('div', recursive=False)
        if region is None:
            continue
        region['data-ppt-editable'] = str(i)
        region['data-ppt-role'] = role
        reflow = page['role'] == 'body' and role in {'title', 'subtitle', 'body', 'itemTitle', 'itemBody'}
        region['data-edit-mode'] = 'vector-text' if vector else 'body-text' if reflow else 'text-only'
        # Measure against the original text frame, including its padding. CJK
        # glyphs may extend beyond the inner flex line while remaining in-frame.
        outer['data-ppt-slot'] = str(i)  # geometry checker only, never a fill API
        outer['data-ppt-text-check'] = 'true'
        outer['data-edit-mode'] = region['data-edit-mode']
        if reflow:
            outer['data-ppt-min-font-size'] = str(16 * template['height'] / 720)
        outer['data-ppt-allow-overflow'] = 'true' if role in {'contentsNumber', 'sectionNumber', 'pageNumber'} or (element.get('textLayout', {}).get('overflow') and page['role'] != 'body') else 'false'
    return str(soup)


def pack_resources(document):
    """Keep HTML/CSS/SVG nodes; losslessly alias images and long glyph/path coordinates."""
    resources = {}
    by_value = {}
    def token_for(value):
        if value not in by_value:
            token = f'__PPT_RESOURCE_{len(resources)}__'
            resources[token] = value
            by_value[value] = token
        return by_value[value]
    document = re.sub(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+', lambda m: token_for(m[0]), document)
    document = re.sub(r'<path\b[^>]*>', lambda m: re.sub(r'\bd="([^"]{256,})"',
                      lambda path: f'd="{token_for(path[1])}"', m[0]), document)
    return document, resources



def template_slots(template, page_index, document):
    """Describe authored tags using the same permissions as the HTML validator."""
    page = template['pages'][page_index]
    soup = BeautifulSoup(document, 'html.parser')
    slots = []
    for region in soup.select('[data-ppt-editable]'):
        index = int(region['data-ppt-editable'])
        element = page['elements'][index]
        role, order = region['data-ppt-role'], element.get('order', 1)
        minimum = 16 * template['height'] / 720 if region['data-edit-mode'] == 'body-text' else None
        padding = element.get('textLayout', {}).get('padding', [6, 12, 6, 12])
        # Conservative CJK capacity for a short display title, never a source-text limit.
        title_size = max(minimum or 0, max((r.get('size', 24) for p in element.get('paragraphs', []) for r in p.get('runs', [])), default=24))
        title_limit = max(1, int((element['width'] - padding[1] - padding[3]) / title_size) *
                          max(1, int((element['height'] - padding[0] - padding[2]) / (title_size * 1.35)))) if role == 'title' else None
        slots.append({
            'min_font_size': minimum, 'title_max_chars': title_limit,
            'editable_id': str(index),
            'field_name': element.get('fieldName') or f'{role}_{order}_{index}',
            'role': role, 'order': order, 'edit_mode': region['data-edit-mode'],
            'bounds': {key: element.get(key, 0) for key in ('x', 'y', 'width', 'height', 'rotation')},
            'text_layout': element.get('textLayout', {}),
            'paragraphs': element.get('paragraphs', []),
            'vector_text': element.get('artworkType') == 'outlinedText',
        })
    return slots


def unpack_resources(document, resources):
    for token, value in resources.items():
        document = document.replace(token, value)
    if '__PPT_RESOURCE_' in document:
        raise ValueError('模型引用了不存在的模板资源')
    return document


def catalog(template):
    result = []
    for i, page in enumerate(template['pages']):
        if page['role'] not in ROLES:
            continue
        roles = [editable_role(e, page['role']) for e in page['elements']]
        if not any(roles):
            continue
        reference = template_html(template, i)
        packed, _ = pack_resources(reference)
        if len(packed) > 350_000:
            continue
        regions = []
        for element, role in zip(page['elements'], roles):
            if role:
                size = 22 * template['height'] / 720 if page['role'] == 'body' else max(
                    (r.get('size', 24) for p in element.get('paragraphs', []) for r in p.get('runs', [])), default=24)
                regions.append({'role': role, 'width': element['width'], 'height': element['height'],
                                'estimated_characters': max(1, int((element['width'] - 24) / size)) * max(1, int((element['height'] - 12) / (size * 1.35)))})
        result.append({'template_page': i, 'role': page['role'], 'allowed_roles': [page['role']], 'name': page.get('name', ''),
                       'text_regions': regions, 'html_characters': len(packed),
                       'layout_kind': page.get('layoutKind', 'auto'),
                       'slots': template_slots(template, i, reference),
                       'rule': RULES[page['role']], 'text_roles': [r for r in roles if r],
                       'sample_text': '\n'.join(''.join(r['text'] for p in e.get('paragraphs', []) for r in p['runs']) for e in page['elements'])[:1200]})
    for role in ('cover', 'body', 'ending'):
        if not any(p['role'] == role for p in result):
            raise ValueError(f'模板缺少可编辑的{dict(cover="首页", body="正文页", ending="尾页")[role]}，请在模板管理中设置页面用途和文字区域后发布')
    return result


def parse_json(raw):
    source = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.I)
    try:
        return json.loads(source)
    except json.JSONDecodeError as error:
        from json_repair import loads
        # Recover only the wrapper. Decode each original HTML JSON string with
        # the strict decoder, so repair cannot rewrite Unicode or HTML content.
        data = loads(source)
        pages = data.get('pages') if isinstance(data, dict) else None
        values = []
        for match in re.finditer(r'(?<!\\)"html"\s*:\s*', source):
            value, _ = json.JSONDecoder().raw_decode(source, match.end())
            if not isinstance(value, str):
                raise error
            values.append(value)
        if not isinstance(pages, list) or not values or len(values) != len(pages):
            raise error
        for page, value in zip(pages, values):
            if not isinstance(page, dict):
                raise error
            page['html'] = value
        return data


def validate_deck(pages, templates, complete=True):
    if not pages or len(pages) > MAX_PAGES:
        raise ValueError(f'整套 PPT 必须包含1至{MAX_PAGES}页')
    for role in ('cover', 'ending'):
        count = sum(p['role'] == role for p in pages)
        if count > 1 or (complete and count != 1):
            raise ValueError('首页和尾页必须各且仅有一页')
    if any(p['role'] == 'cover' for p in pages[1:]) or any(p['role'] == 'ending' for p in pages[:-1]):
        raise ValueError('首页必须在第一位，尾页必须在最后一位')
    allowed = {p['template_page']: p['role'] for p in templates}
    mismatches = [{'slide': i + 1, 'template_page': p.get('template_page'), 'requested_role': p.get('role'),
                   'required_role': allowed.get(p.get('template_page'))} for i, p in enumerate(pages)
                  if allowed.get(p.get('template_page')) != p.get('role')]
    if mismatches:
        raise ValueError(f'生成页必须使用对应用途的模板，不得改变模板页面用途：{mismatches}。未提供目录或章节模板时，目录原文与章节标题应作为正文内容放在body页，不得添加contents/section辅助页。')


def parse_plan(raw, batch, templates, first, last, agenda):
    data = parse_json(raw)
    pages = data.get('slides') if isinstance(data, dict) else None
    if not isinstance(pages, list) or not 1 <= len(pages) <= 30:
        raise ValueError('每批应规划1至30页')
    used, entries, auxiliary = [], [], []
    for page_index, page in enumerate(pages):
        if not isinstance(page, dict) or type(page.get('template_page')) is not int:
            raise ValueError('模板索引无效')
        if not isinstance(page.get('title'), str) or not 0 < len(page['title'].strip()) <= 160:
            raise ValueError('页面标题无效')
        ids = page.get('block_ids', [])
        items = page.get('agenda_ids', [])
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ValueError('block_ids须为原文ID数组')
        if not isinstance(items, list) or any(not isinstance(i, str) for i in items):
            raise ValueError('agenda_ids须为目录ID数组')
        if page.get('role') == 'body':
            if not ids:
                raise ValueError(f'第{page_index + 1}页“{page["title"]}”是body，必须在block_ids中引用本批原文ID；不能将无原文引用的目录/章节过渡页改名body。请删除这种辅助页，把原文标题和正文分配到有block_ids的正文页。')
            used.extend(ids)
        elif ids:
            # Models legitimately cite source headings when naming a cover or
            # divider. Keep the title, but route its source text to a body page;
            # auxiliary pages must never receive prose during HTML generation.
            auxiliary.extend((page_index, key) for key in ids)
            page['block_ids'] = []
        if page.get('role') == 'contents':
            if not first or not items:
                raise ValueError('目录必须在第一批规划且引用目录条目')
            entries.extend(items)
        elif items:
            raise ValueError('仅目录页可以引用目录条目')
        if page.get('role') == 'section' and (type(page.get('section_number')) is not int or not 1 <= page['section_number'] <= len(agenda)):
            raise ValueError('章节页必须提供文稿中从1开始的section_number')
    validate_deck(pages, templates, complete=False)
    for role, required in [('cover', first), ('ending', last)]:
        if sum(p['role'] == role for p in pages) != int(required):
            raise ValueError('只在第一批生成一个首页，最后一批生成一个尾页')
    # A model often places source headings on cover/section pages without listing
    # their IDs. Preserve those headings with neighboring body text, as before;
    # never infer, summarize or silently restore omitted prose or numeric data.
    order = [b['id'] for b in batch]
    unknown = sorted({key for _, key in auxiliary if key not in order})
    if unknown:
        raise ValueError(f'辅助页引用了本批不存在的原文ID：{unknown}')
    for page_index, key in auxiliary:
        if key in used:
            continue
        neighbors = pages[page_index + 1:] + list(reversed(pages[:page_index]))
        target = next((p for p in neighbors if p.get('role') == 'body'), None)
        if target is None:
            raise ValueError(f'原文块{key}被分配到辅助页，但没有正文页承接，请规划正文页')
        ids = target['block_ids']
        position = order.index(key)
        following = next((i for i, value in enumerate(ids) if value in order and order.index(value) > position), len(ids))
        ids.insert(following, key)
        used.append(key)
    missing_headings = [b['id'] for b in batch if b.get('kind') == 'heading' and b['id'] not in used]
    for key in missing_headings:
        position = order.index(key)
        candidates = order[position + 1:] + list(reversed(order[:position]))
        target = next((p for candidate in candidates for p in pages if p.get('role') == 'body' and candidate in p.get('block_ids', [])), None)
        if target is not None:
            ids = target['block_ids']
            following = next((i for i, value in enumerate(ids) if value in order and order.index(value) > position), len(ids))
            ids.insert(following, key)
            used.append(key)
    expected = Counter(order)
    observed = Counter(used)
    if observed != expected:
        raise ValueError(f'当前批次原文块必须恰好覆盖一次；缺失ID：{list((expected-observed).elements())}；重复或未知ID：{list((observed-expected).elements())}')
    expected = [a['id'] for a in agenda] if first and any(t['role'] == 'contents' for t in templates) else []
    if entries != expected:
        raise ValueError('目录条目必须按顺序完整覆盖，可拆为多页')
    return pages


def tree(node, text_holes=False):
    """Compare parsed DOM, ignoring serializer differences, never CSS/attributes."""
    if isinstance(node, Comment):
        return ('comment', str(node))
    if isinstance(node, NavigableString):
        return None if not str(node).strip() or text_holes else ('text', str(node))
    attrs = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in node.attrs.items()
                         if k not in {'data-source-block', 'data-agenda-item'}))
    return (node.name, attrs, tuple(t for c in node.children if (t := tree(c, text_holes)) is not None))


def fixed_difference(expected, actual):
    left, right = expected.find_all(True), actual.find_all(True)
    if len(left) != len(right):
        return f'固定节点数量应为{len(left)}，实际{len(right)}'
    for before, after in zip(left, right):
        identity = before.get('data-template-element', before.name)
        if before.name != after.name:
            return f'元素{identity}标签应为{before.name}，实际{after.name}'
        if before.attrs != after.attrs:
            differences = []
            for key in before.attrs.keys() | after.attrs.keys():
                if before.get(key) != after.get(key):
                    expected_value, actual_value = str(before.get(key)), str(after.get(key))
                    if key == 'style':
                        # Show the precise differing declaration, including long
                        # transform/background styles, without dumping data URIs.
                        import difflib
                        for change in difflib.ndiff(expected_value.split(';'), actual_value.split(';')):
                            if change.startswith(('- ', '+ ')):
                                differences.append(change[:220])
                    else:
                        differences.append(f'{key}: 应为{expected_value[:120]}，实际{actual_value[:120]}')
            return f'元素{identity}固定属性差异：{differences[:8]}'
    return '固定节点文字、嵌套结构或顺序改变'


TEXT_TAGS = {'div', 'p', 'span', 'br', 'strong', 'b', 'em', 'i', 'u', 'small', 'sup', 'sub',
             'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tfoot', 'caption', 'tr', 'th', 'td', 'colgroup', 'col', 'svg', 'text', 'tspan'}
TEXT_STYLES = {'font-family', 'font-size', 'font-weight', 'font-style', 'line-height', 'letter-spacing',
               'word-spacing', 'text-align', 'text-decoration', 'text-shadow', 'color', 'white-space',
               'overflow-wrap', 'word-break', 'margin', 'margin-top', 'margin-bottom', 'margin-left',
               'margin-right', 'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right',
               'display', 'flex-direction', 'flex', 'flex-wrap', 'justify-content', 'align-items', 'gap',
               'width', 'height', 'max-width', 'box-sizing', 'vertical-align', 'border-collapse',
               'table-layout', 'border-spacing', 'caption-side', 'list-style-type', 'list-style-position',
               'column-count', 'column-gap', 'row-gap', 'break-inside', 'text-indent',
               'position', 'left', 'top', 'right', 'bottom', 'transform', 'transform-origin',
               'fill', 'stroke', 'stroke-width', 'paint-order', 'opacity'}


def safe_text(region):
    """New markup can describe visible text only, not images, scripts or CSS artwork."""
    for node in [region, *region.find_all(True)]:
        if node.name not in TEXT_TAGS:
            raise ValueError(f'文字区域禁止添加非文字标签 {node.name}')
        for key, value in node.attrs.items():
            if key not in {'style', 'data-source-block', 'data-agenda-item', 'data-ppt-editable',
                           'data-ppt-slot', 'data-ppt-role', 'data-edit-mode', 'xmlns', 'viewbox',
                           'width', 'height', 'x', 'y', 'dx', 'dy', 'font-size', 'font-family',
                           'font-weight', 'text-anchor', 'fill', 'stroke', 'stroke-width', 'paint-order',
                           'colspan', 'rowspan', 'cellpadding', 'cellspacing', 'span'}:
                raise ValueError(f'文字区域禁止属性 {key}')
            if re.search(r'url\s*\(|[<>\\]|!important|expression\s*\(', str(value), re.I):
                raise ValueError('文字区域不允许资源引用或覆盖全局样式')
        for declaration in node.get('style', '').split(';'):
            if not declaration.strip():
                continue
            key, sep, value = declaration.strip().partition(':')
            key, value = key.strip().lower(), value.strip()
            if key in {'border', 'border-top', 'border-right', 'border-bottom', 'border-left', 'outline'} and value.lower() in {'0', '0px', 'none'}:
                continue
            if key in {'background', 'background-color'} and value.lower() in {'none', 'transparent'}:
                continue
            if not sep or key not in TEXT_STYLES or re.search(r'[{}@]|/\*', value):
                raise ValueError(f'文字区域禁止样式 {key}')
            if key == 'display' and value not in {'block', 'inline', 'inline-block', 'flex', 'table', 'table-row', 'table-cell'}:
                raise ValueError('禁止隐藏文字')
            if key in {'color', 'fill', 'stroke'} and not re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|[a-zA-Z]+', value):
                raise ValueError('文字颜色不得带透明度')
            if key in {'color', 'fill'} and value.lower() in {'transparent', 'none'}:
                raise ValueError('禁止透明文字')
            if key == 'position' and value not in {'relative', 'absolute', 'static'}:
                raise ValueError('文字不得脱离页面定位')
            if key == 'opacity' and value not in {'1', '1.0'}:
                raise ValueError('禁止隐藏或淡化文稿文字')


def require_editable_regions(originals, generated):
    expected = Counter(n['data-ppt-editable'] for n in originals)
    actual = Counter(n['data-ppt-editable'] for n in generated)
    if expected != actual:
        raise ValueError(f'模型增删了模板文字区域标记；缺少ID：{list((expected - actual).elements())}；'
                         f'多余或重复ID：{list((actual - expected).elements())}；'
                         f'仅允许这些ID且各出现一次：{list(expected)}。'
                         '未标记为可编辑的装饰不可添加data-ppt-editable；不用的合法文字区保留空文字节点。')


def preserve_fixed_markup(document, reference):
    """Keep the model's authored text HTML, enforce the immutable template shell.

    This is a permissions merge of a complete model document, not text/slot
    rendering. No source text, typography or body layout is generated here.
    """
    if not isinstance(document, str) or len(document) > library.MAX_TEMPLATE_PAGE_CHARACTERS:
        raise ValueError('模型HTML缺失或超出单页大小限制')
    if not re.match(r'(?is)\s*(?:<!doctype html>\s*)?<html[\s>]', document) or not re.search(r'(?is)</html>\s*$', document):
        raise ValueError('模型必须返回完整HTML文档')
    actual, expected = BeautifulSoup(document, 'html.parser'), BeautifulSoup(reference, 'html.parser')
    if any(len(actual.find_all(tag)) != 1 for tag in ('html', 'head', 'body')):
        raise ValueError('模型必须保留单页HTML结构')
    originals, generated = expected.select('[data-ppt-editable]'), actual.select('[data-ppt-editable]')
    require_editable_regions(originals, generated)
    for node in actual.select('[data-source-block], [data-agenda-item]'):
        if not node.has_attr('data-ppt-editable') and not node.find_parent(attrs={'data-ppt-editable': True}):
            raise ValueError('原文标记只能位于可编辑文字区域')
    by_id = {n['data-ppt-editable']: n for n in generated}
    for before in originals:
        after = by_id[before['data-ppt-editable']]
        if any(before.get(key) != after.get(key) for key in ('data-ppt-role', 'data-edit-mode')):
            raise ValueError('模型更改了文字区域权限标记')
        if before['data-edit-mode'] in {'body-text', 'vector-text'}:
            # Decoration is outside the model's permissions. Keep its authored
            # text layout, but never introduce table rules or background fills.
            for node in after.find_all(style=True):
                styles = []
                for declaration in node['style'].split(';'):
                    key = declaration.strip().partition(':')[0].lower()
                    if (key.startswith('background') or key in {'box-shadow', 'outline', 'outline-color', 'outline-style', 'outline-width'} or
                            key.startswith('border') and key not in {'border-collapse', 'border-spacing'}):
                        continue
                    styles.append(declaration)
                node['style'] = ';'.join(styles)
        before.clear()
        for child in list(after.contents):
            before.append(child.extract())
        for key in ('data-source-block', 'data-agenda-item'):
            if after.has_attr(key):
                before[key] = after[key]
    return str(expected)


def validate_html(document, reference):
    if not isinstance(document, str) or len(document) > library.MAX_TEMPLATE_PAGE_CHARACTERS:
        raise ValueError('模型HTML缺失或超出单页大小限制')
    if not re.match(r'(?is)\s*(?:<!doctype html>\s*)?<html[\s>]', document) or not re.search(r'(?is)</html>\s*$', document):
        raise ValueError('模型必须返回完整HTML文档')
    expected = BeautifulSoup(reference, 'html.parser')
    actual = BeautifulSoup(document, 'html.parser')
    for tag in ('html', 'head', 'body'):
        if len(actual.find_all(tag)) != 1:
            raise ValueError('模型必须保留单页HTML结构')
    regions = expected.select('[data-ppt-editable]')
    candidates = actual.select('[data-ppt-editable]')
    require_editable_regions(regions, candidates)
    for before, after in zip(regions, candidates):
        permissions = lambda n: {k: v for k, v in n.attrs.items() if k not in {'data-source-block', 'data-agenda-item'}}
        if permissions(before) != permissions(after):
            raise ValueError('模型更改了文字区域边界或权限标记')
        mode = before['data-edit-mode']
        if mode == 'text-only':
            if tree(before, True) != tree(after, True):
                def skeleton(region):
                    return [(n.name, dict(n.attrs)) for n in region.find_all(True)]
                raise ValueError(f'首页、目录、章节页和尾页只能改文字，不能改文字样式或结构。区域{before["data-ppt-editable"]}应保留节点与属性{str(skeleton(before))[:2200]}，实际{str(skeleton(after))[:2200]}；多个span也必须全部保留，不用的span只清空文字。')
        elif tree(before) != tree(after):
            safe_text(after)
        # Preserve boundary and position, compare everything outside text islands.
        before.clear()
        after.clear()
    if tree(expected) != tree(actual):
        raise ValueError('模型改动了模板非文字部分（背景、图片、SVG、装饰、样式或元素位置）。' + fixed_difference(expected, actual) + '。只能在data-ppt-editable内部重排文字；不得拉高外框或改坐标，不够就续页。')
    # References must point to real visible text inside an authorized region.
    original = BeautifulSoup(document, 'html.parser')
    for node in original.select('[data-source-block], [data-agenda-item]'):
        if not node.find_parent(attrs={'data-ppt-editable': True}) and not node.has_attr('data-ppt-editable'):
            raise ValueError('原文标记只能位于可编辑文字区域')
        if node.select('[data-source-block], [data-agenda-item]'):
            raise ValueError('原文标记不可嵌套')
    return document


def source_text(value, kind=None):
    # Markdown punctuation is presentation syntax, unlike the prose and numbers
    # it surrounds. HTML lists/tables may render that syntax as actual structure.
    if kind in {'list', 'paragraph'}:
        value = re.sub(r'(?m)^\s*(?:[-*+]\s+|[•●▪◦]\s*)', '', value)
    if kind == 'table':
        value = '\n'.join(line for line in value.splitlines()
                          if not re.fullmatch(r'[\s|:\-]+', line))
        value = value.replace('|', '').replace('｜', '')
    return compact(value)


def annotate_visible_headings(documents, blocks):
    """Recover missing provenance only when a heading is already visible verbatim."""
    soups = [BeautifulSoup(document, 'html.parser') for document in documents]
    present = {n['data-source-block'] for soup in soups for n in soup.select('[data-source-block]')}
    for block in blocks:
        if block.get('kind') != 'heading' or block['id'] in present:
            continue
        value = compact(block['text'])
        match = None
        for soup in soups:
            for region in soup.select('[data-ppt-editable]'):
                for node in [region, *region.find_all(True)]:
                    if compact(node.get_text()) != value:
                        continue
                    if node.has_attr('data-source-block') or node.find_parent(attrs={'data-source-block': True}) or node.select('[data-source-block], [data-agenda-item]'):
                        continue
                    if any(compact(child.get_text()) == value for child in node.find_all(True)):
                        continue
                    match = node
                    break
                if match is not None:
                    break
            if match is not None:
                break
        if match is not None:
            match['data-source-block'] = block['id']
            present.add(block['id'])
    # A long table's row label may become the slide/section title. Recover its
    # provenance only when that exact label is already present, never write it.
    for block in blocks:
        if block.get('kind') != 'table':
            continue
        lines = block['text'].strip().splitlines()
        if len(lines) > 1 and re.fullmatch(r'[\s|:\-]+', lines[1]):
            lines = lines[2:]
        labels = [compact(line.strip().strip('|').split('|')[0]) for line in lines if line.strip().startswith('|')]
        expected = source_text(block['text'], 'table')
        for label in dict.fromkeys(labels):
            if len(label) < 2 or label.isdigit():
                continue
            actual = ''.join(source_text(n.get_text('\n'), 'table') for soup in soups
                             for n in soup.select('[data-source-block]') if n['data-source-block'] == block['id'])
            if actual.count(label) >= expected.count(label):
                continue
            matches = [n for soup in soups for region in soup.select('[data-ppt-editable]')
                       for n in [region, *region.find_all(True)]
                       if compact(n.get_text()) == label and not n.has_attr('data-source-block')
                       and not n.find_parent(attrs={'data-source-block': True})
                       and not n.select('[data-source-block], [data-agenda-item]')
                       and not any(compact(child.get_text()) == label for child in n.find_all(True))]
            if matches:
                matches[0]['data-source-block'] = block['id']
    return [str(soup) for soup in soups]


def normalize_table_blocks(blocks):
    """Blank lines may separate a table's rows without starting new prose."""
    normalized, columns = [], None
    for block in blocks:
        lines = block['text'].strip().splitlines()
        if block.get('kind') == 'paragraph' and columns and lines and all(
                line.strip().startswith('|') and line.strip().endswith('|') and
                len(line.strip().strip('|').split('|')) == columns for line in lines):
            block = {**block, 'kind': 'table'}
        if block.get('kind') != 'table':
            columns = None
        elif len(lines) > 1 and re.fullmatch(r'[\s|:\-]+', lines[1]):
            columns = len(lines[0].strip().strip('|').split('|'))
        normalized.append(block)
    return normalized


def table_headers(blocks):
    """Carry original column labels across consecutive chunks of a long table."""
    result, header = {}, None
    for block in blocks:
        if block.get('kind') != 'table':
            header = None
            continue
        lines = block['text'].strip().splitlines()
        if len(lines) > 1 and re.fullmatch(r'[\s|:\-]+', lines[1]):
            header = [compact(cell) for cell in lines[0].strip().strip('|').split('|')]
        if header:
            result[block['id']] = header
    return result


def text_difference(expected, actual):
    offset = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), min(len(expected), len(actual)))
    start = max(0, offset - 60)
    return {'at': offset, 'expected': expected[start:offset + 180], 'actual': actual[start:offset + 180],
            'expected_length': len(expected), 'actual_length': len(actual)}


def validate_sources(documents, blocks, agenda, table_context=None):
    for attr, records in [('data-source-block', blocks), ('data-agenda-item', agenda)]:
        kinds = {b['id']: b.get('kind') for b in records}
        expected = {b['id']: source_text(b['text'], b.get('kind')) for b in records}
        table_rows = {}
        headers = {**table_headers(records), **(table_context or {})}
        for record in records:
            lines = record['text'].strip().splitlines()
            if record.get('kind') == 'table' and all(line.strip().startswith('|') for line in lines):
                rows = [[compact(cell) for cell in line.strip().strip('|').split('|')]
                        for line in lines if not re.fullmatch(r'[\s|:\-]+', line)]
                table_rows[record['id']] = rows
                if len(lines) > 1 and re.fullmatch(r'[\s|:\-]+', lines[1]):
                    headers[record['id']] = rows[0]
        actual = {}
        actual_rows = {}
        seen_headers = {key for key, rows in table_rows.items() if key in headers and rows[0] != headers[key]}
        for document in documents:
            soup = BeautifulSoup(document, 'html.parser')
            for node in soup.select(f'[{attr}]'):
                key = node[attr]
                if key in table_rows:
                    header_text = ''.join(headers.get(key, []))
                    if header_text:
                        # Field-list layouts sometimes render a continued
                        # header as a paragraph instead of an HTML table row.
                        candidates = [n for n in [node, *node.find_all(True)]
                                      if n.name not in {'table', 'thead', 'tbody', 'tr', 'th', 'td'}
                                      and n.find_parent('table') is None
                                      and source_text(n.get_text('\n'), 'table') == header_text
                                      and not any(source_text(child.get_text('\n'), 'table') == header_text for child in n.find_all(True))]
                        for candidate in candidates:
                            if key in seen_headers:
                                candidate.decompose()
                            else:
                                seen_headers.add(key)
                    if node.name is None:
                        continue
                    rows = ([node] if node.name == 'tr' else node.find_all('tr'))
                    for row in rows:
                        cells = [compact(cell.get_text()) for cell in row.find_all(['td', 'th'], recursive=False)]
                        table = row.find_parent('table')
                        header = headers.get(key)
                        if table is not None and header is not None and not table.has_attr('data-header-checked'):
                            first_row = table.find('tr')
                            first_cells = [compact(cell.get_text()) for cell in first_row.find_all(['td', 'th'], recursive=False)]
                            if first_cells != header:
                                raise ValueError(f'表格 {key} 在不同文字区域或续页拆开时，每个表格都必须重复完整原表头，确保数据列可辨认。')
                            table['data-header-checked'] = 'true'
                        if cells == headers.get(key):
                            if key in seen_headers:
                                row.decompose()
                                continue
                            seen_headers.add(key)
                        actual_rows.setdefault(key, []).append(cells)
                if node.name is not None:
                    actual[key] = actual.get(key, '') + source_text(node.get_text('\n'), kinds.get(key))
        for key, rows in actual_rows.items():
            if rows != table_rows[key]:
                expected_rows = table_rows[key]
                row_index = next(i for i in range(max(len(rows), len(expected_rows)))
                                 if i >= len(rows) or i >= len(expected_rows) or rows[i] != expected_rows[i])
                before = expected_rows[row_index] if row_index < len(expected_rows) else []
                after = rows[row_index] if row_index < len(rows) else []
                column = next(i for i in range(max(len(before), len(after)))
                              if i >= len(before) or i >= len(after) or before[i] != after[i]) if before or after else 0
                detail = text_difference(before[column] if column < len(before) else '', after[column] if column < len(after) else '')
                raise ValueError(f'表格 {key} 第{row_index + 1}行第{column + 1}列单元格与原文不一致：{detail}；续表可重复原表头，但各列内容、数字与顺序必须保留。')
        if actual != expected:
            differences = [{'id': key, **text_difference(value, actual.get(key, ''))}
                           for key, value in expected.items() if actual.get(key) != value]
            raise ValueError(f'原文或目录内容不完整：必须在可见文字中保留全部内容、数字与顺序，续页可分段但不能遗漏或重复。差异：{differences[:5]}；多余ID：{sorted(actual.keys() - expected.keys())}')


def validate_sample_text(document, reference, proposal, blocks=(), agenda=()):
    """Unused editable sample copy must not leak into the finished presentation."""
    before, after = BeautifulSoup(reference, 'html.parser'), BeautifulSoup(document, 'html.parser')
    allowed = [compact(proposal.get('title', '')), *[compact(b['text']) for b in [*blocks, *agenda]]]
    actual = {n['data-ppt-editable']: compact(n.get_text()) for n in after.select('[data-ppt-editable]')}
    for node in before.select('[data-ppt-editable]'):
        sample = compact(node.get_text())
        if len(sample) < 4 or actual.get(node['data-ppt-editable']) != sample or any(sample in text for text in allowed):
            continue
        # Imported templates often label a footer website/email as a subtitle.
        # Keeping that original contact text is not leftover example prose.
        contact = node.get_text().strip()
        domain = r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}'
        if re.fullmatch(rf'(?:https?://)?{domain}(?::\d{{1,5}})?(?:/[^\s<>]*)?|[^\s@<>]+@{domain}', contact, re.I):
            continue
        if proposal['role'] == 'ending' and node.get('data-ppt-role') == 'title' and re.fullmatch(r'(?:谢谢|感谢).{0,12}', sample):
            continue
        raise ValueError(f'可编辑文字区域{node["data-ppt-editable"]}仍保留无关模板示例文案“{sample[:100]}”。请替换为本页相关文字，或清空其文字节点；固定样式与结构不变。')


def validate_heading(document, proposal):
    if proposal['role'] in {'body', 'contents'}:
        soup = BeautifulSoup(document, 'html.parser')
        titles = soup.select('[data-ppt-editable][data-ppt-role="title"], [data-ppt-editable][data-ppt-role="itemTitle"]')
        if titles and not any(n.get_text(strip=True) for n in titles):
            raise ValueError('正文或目录的每个续页都必须保留可见主题标题；可重复本页title且不加data-source-block标记，不能把标题区域清空。')
        return
    if proposal['role'] not in {'cover', 'section'}:
        return
    soup = BeautifulSoup(document, 'html.parser')
    selector = '[data-ppt-editable][data-ppt-role="title"]'
    if proposal['role'] == 'cover':
        selector += ',[data-ppt-editable][data-ppt-role="subtitle"]'
    title = ''.join(n.get_text() for n in soup.select(selector))
    required_title = proposal['title']
    section_prefix = r'^\s*(\d+)(?:[ \t、:：-]+|\.(?!\d)\s*|(?=[^\d.\s]))'
    numbers = soup.select('[data-ppt-editable][data-ppt-role="sectionNumber"]')
    if proposal['role'] == 'section' and numbers:
        rendered = ''.join(n.get_text() for n in numbers)
        expected = proposal['section_number']
        # Require an unambiguous numeric chapter index; labels such as PART are fine.
        if [int(n) for n in re.findall(r'\d+', rendered)] != [expected]:
            raise ValueError('章节序号应与section_number一致，保留原有文字风格')
    if proposal['role'] == 'section':
        # Keep the complete title and chapter index, while allowing a template
        # to separate "02 需求回顾" into frames or render it as "02.需求回顾".
        prefix = re.match(section_prefix, required_title)
        if prefix and int(prefix[1]) == proposal['section_number'] and required_title[prefix.end():].strip():
            rendered_prefix = re.match(section_prefix, title)
            if numbers:
                required_title = required_title[prefix.end():]
            elif rendered_prefix and int(rendered_prefix[1]) == proposal['section_number']:
                required_title = required_title[prefix.end():]
                title = title[rendered_prefix.end():]
    if compact(required_title) not in compact(title):
        raise ValueError(f'首页或章节页必须显示本页标题；矢量标题也需替换为可见文字。需要“{required_title[:120]}”，实际“{title[:120]}”')


def digest(document):
    return hashlib.sha256(document.encode()).hexdigest()
