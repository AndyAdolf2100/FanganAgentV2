"""Deterministic text injection into immutable HTML template pages."""
import copy
import re
from collections import Counter

from bs4 import BeautifulSoup, NavigableString

from . import template_components as components, template_html as html
from .template_capacity import take, original_size
from . import fixed


class TitleFitError(ValueError):
    """The generated display title needs shortening; source blocks stay intact."""


def text_only(region, value):
    """Replace text, retaining every authored element, attribute and styled run."""
    leaves = list(region.find_all(string=lambda s: isinstance(s, NavigableString)))
    paragraphs = region.find_all('p')
    # Imported titles often contain several identically styled sample lines.
    # Let the replacement wrap naturally in the first paragraph; retain the
    # empty authored paragraphs and all styles for the immutable DOM contract.
    if len(paragraphs) > 1 and '\n' not in value:
        signatures = [(p.attrs, [(n.name, n.attrs) for n in p.find_all()]) for p in paragraphs]
        if all(s == signatures[0] for s in signatures):
            for p in paragraphs[1:]:
                for leaf in p.find_all(string=True):
                    leaf.replace_with('')
            leaves = list(paragraphs[0].find_all(string=True))
    if not leaves:
        if value:
            # A real text region needs a text-bearing element; never add new tags.
            target = region.find('span') or region.find('p')
            if target is None:
                raise ValueError('文字区域缺少文字节点，请在模板中添加示例文字')
            target.append(value)
        return
    weights = [max(1, len(str(s))) for s in leaves]
    total = sum(weights)
    cursor = used = 0
    for i, leaf in enumerate(leaves):
        used += weights[i]
        end = len(value) if i == len(leaves) - 1 else round(len(value) * used / total)
        leaf.replace_with(value[cursor:end])
        cursor = end


def put(soup, field, element, value, source=None, agenda=None, body=False, prefix='', join=False):
    region = soup.select_one(f'[data-ppt-editable="{field["element"]}"]')
    if region is None:
        raise ValueError(f'模板字段 {field["name"]} 不存在')
    if region['data-edit-mode'] == 'text-only':
        text_only(region, value)
        if source:
            region['data-source-block'] = source
        if agenda:
            region['data-agenda-item'] = agenda
        return
    if not body:
        region.clear()
    size = font_size(element, body)
    run = next((r for p in element.get('paragraphs', []) for r in p.get('runs', [])), {})
    font = re.sub(r'[^\w\s,-]', '', run.get('font', 'system-ui'))
    color = run.get('color', element.get('fill', '#222222'))
    if not re.fullmatch(r'#[a-fA-F0-9]{6}', color):
        color = '#222222'
    align = next((p.get('align', 'left') for p in element.get('paragraphs', [])), 'left')
    previous = region.find_all('p', recursive=False)
    p = previous[-1] if join and previous else None
    append = p is None
    if append:
        p = soup.new_tag('p', style=f'margin:0;font-size:{size}px;line-height:1.3;white-space:pre-wrap;overflow-wrap:anywhere;font-family:{font};color:{color};text-align:{align};font-weight:{700 if run.get("bold") else 400}')
    if prefix:
        label = soup.new_tag('span')
        label.string = prefix
        p.append(label)
    span = soup.new_tag('span')
    span.string = value
    if source:
        span['data-source-block'] = source
    if agenda:
        span['data-agenda-item'] = agenda
    p.append(span)
    if append:
        region.append(p)


def font_size(element, body=False):
    return max(original_size(element) * element.get('_font_scale', 1), element.get('_minimum_size', 0))


def effective(element, template, role, scale=1):
    if role != 'body':
        return element
    return {**element, '_minimum_size': 16 * template['height'] / 720, '_font_scale': max(.65, scale)}


def frame(element, scale=1):
    result = copy.deepcopy(element)
    result['height'] = max(0, result['height'] * scale)
    return result


def fits_text(value, element, scale=1, body=False):
    style = {'fontSize': font_size(element, body), 'lineHeight': 1.3} if body else None
    # Shrinking text must increase capacity, not also shrink its original frame.
    box = frame(element, 1 if body else scale)
    # Authored frames often fit glyph ink while the last line box extends
    # beyond them. Chromium still verifies the actual ink before publication.
    box['height'] += font_size(element) * .5
    return take(value, box, style)


def title_parts(template, proposal):
    layout = template['pages'][proposal['template_page']]
    fields = components.fields(layout)
    candidates = [f for f in fields if f['role'] == 'title']
    if proposal['role'] == 'cover':
        candidates += [f for f in fields if f['role'] == 'subtitle']
    title = proposal['title']
    if proposal['role'] == 'section':
        match = re.match(r'^\s*0*(\d+)[.、\s]+(.*)$', title)
        if match and int(match[1]) == proposal.get('section_number') and any(f['role'] == 'sectionNumber' for f in fields):
            title = match[2]
    # A short title already demonstrated by the authored template must not be
    # truncated merely because a conservative line-height estimate says no.
    sample_lengths = [len(''.join(r.get('text', '') for p in layout['elements'][f['element']].get('paragraphs', []) for r in p.get('runs', [])).strip()) for f in candidates]
    if proposal['role'] != 'body' and len(title) <= sum(sample_lengths):
        remaining, parts = title, []
        for field, length in zip(candidates, sample_lengths):
            parts.append((field, layout['elements'][field['element']], remaining[:length]))
            remaining = remaining[length:]
        return parts
    resizable = proposal['role'] == 'body'
    for factor in ([1, .95, .9, .85, .8, .75, .7, .65] if resizable else [1]):
        remaining, parts = title, []
        for field in candidates:
            element = effective(layout['elements'][field['element']], template, proposal['role'], factor)
            # As with fixed titles, measure glyph ink rather than rejecting a
            # short authored frame because the line box is a fraction taller.
            part, remaining = fits_text(remaining, element, body=resizable)
            parts.append((field, element, part))
        if not remaining.strip():
            return parts
    raise TitleFitError('页面标题超出模板文字区域，需要缩短展示标题；原文仍完整保留在正文')


def title_capacity(template, page_index):
    page = template['pages'][page_index]
    roles = {'title', 'subtitle'} if page['role'] == 'cover' else {'title'}
    capacity = 0
    for field in components.fields(page):
        if field['role'] not in roles:
            continue
        element = effective(page['elements'][field['element']], template, page['role'], .65)
        capacity += len(fits_text('文' * 1000, element, body=page['role'] == 'body')[0])
    return capacity


def base(template, proposal, page_number):
    layout = template['pages'][proposal['template_page']]
    reference = html.template_html(template, proposal['template_page'])
    soup = BeautifulSoup(reference, 'html.parser')
    regions = components.fields(layout)
    for f in regions:
        e = layout['elements'][f['element']]
        region = soup.select_one(f'[data-ppt-editable="{f["element"]}"]')
        if region is None:
            raise ValueError('模板文字区域无法渲染')
        if e.get('_replacement') is not None:
            put(soup, f, e, e['_replacement'])
        elif f['role'] == 'pageNumber':
            put(soup, f, e, str(page_number))
        elif f['role'] == 'sectionNumber':
            put(soup, f, e, fixed.number(e, proposal.get('section_number', 1)))
        elif region['data-edit-mode'] == 'text-only':
            text_only(region, '')
        else:
            region.clear()
    for field, element, part in title_parts(template, proposal):
        put(soup, field, element, part)
    return soup, reference, layout, regions


def units(blocks, headers):
    """Long tables become labelled field lists; source values remain verbatim."""
    result = []
    for block in blocks:
        if block.get('kind') != 'table':
            result.append({'id': block['id'], 'text': block['text'], 'kind': block.get('kind')})
            continue
        lines = block['text'].strip().splitlines()
        header = headers.get(block['id'], [])
        if len(lines) > 1 and re.fullmatch(r'[\s|:\-]+', lines[1]):
            result.append({'id': block['id'], 'text': ' '.join(lines[0].strip().strip('|').split('|')), 'kind': 'table'})
            lines = lines[2:]
        for row_index, row in enumerate(lines):
            cells = row.strip().strip('|').split('|')
            for i, cell in enumerate(cells):
                if not cell.strip():
                    continue
                result.append({'id': block['id'], 'text': cell.strip(), 'kind': 'table', 'row': f'{block["id"]}-{row_index}',
                               'prefix': ('；' if i else '') + ((header[i] + '：') if i < len(header) else '')})
    return result


def validate_assignments(proposal, regions):
    mapping = proposal.get('fields', {})
    if not isinstance(mapping, dict):
        raise ValueError('fields 必须是字段名到原文ID数组的对象')
    available = {f['name']: f for f in regions if f['role'] in components.CONTENT_ROLES}
    refs = []
    for name, ids in mapping.items():
        if name not in available or not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ValueError('填充字段不存在或原文ID数组无效')
        refs.extend(ids)
    if mapping and Counter(refs) != Counter(proposal.get('block_ids', [])):
        raise ValueError('fields 必须完整且不重复地分配本页全部 block_ids')
    return {key: name for name, ids in mapping.items() for key in ids}


def automatic_assignments(blocks, regions):
    """Like Canvas moduleType/moduleSort: pair source subheadings with bodies."""
    groups = []
    for title in sorted([f for f in regions if f['role'] == 'itemTitle'], key=lambda f: (f['order'], f['element'])):
        body = next((f for f in regions if f['role'] == 'itemBody' and f['order'] == title['order']), None)
        if body:
            groups.append((title, body))
    if not groups or not any(b.get('kind') == 'heading' and b.get('level', 0) >= 3 for b in blocks):
        return {}
    result, group = {}, -1
    for block in blocks:
        heading = block.get('kind') == 'heading' and block.get('level', 0) >= 3
        if heading:
            group += 1
        pair = groups[max(group, 0) % len(groups)]
        result[block['id']] = pair[0 if heading else 1]['name']
    return result


def flow_fields(layout, regions):
    """Avoid filling an empty background text shape on top of its text box."""
    def sample(element):
        return ''.join(r.get('text', '') for p in element.get('paragraphs', []) for r in p.get('runs', [])).strip()
    populated = [layout['elements'][f['element']] for f in regions
                 if f['role'] in components.CONTENT_ROLES and sample(layout['elements'][f['element']])]
    result = []
    for field in regions:
        element = layout['elements'][field['element']]
        covered = False
        if field['role'] in components.CONTENT_ROLES and not sample(element) and not element.get('artworkType'):
            for other in populated:
                width = max(0, min(element['x'] + element['width'], other['x'] + other['width']) - max(element['x'], other['x']))
                height = max(0, min(element['y'] + element['height'], other['y'] + other['height']) - max(element['y'], other['y']))
                if width * height > .65 * max(1, min(element['width'] * element['height'], other['width'] * other['height'])):
                    covered = True
                    break
        if not covered:
            result.append(field)
    return result


def continuation_fields(regions, assignments):
    """An unpaired body frame may continue the preceding paired body text."""
    used = set(assignments.values())
    bodies = sorted([f for f in regions if f['role'] in {'body', 'itemBody'}], key=lambda f: (f['order'], f['element']))
    aliases, previous = {}, None
    for field in bodies:
        if field['name'] in used:
            previous = field['name']
        elif previous is not None:
            aliases[field['name']] = previous
    return aliases


def render_pages(template, proposal, blocks=(), agenda=(), *, page_number=1, scale=1, headers=None):
    proposal = {**proposal, '_metadata_texts': [e['_replacement'] for e in template['pages'][proposal['template_page']]['elements'] if '_replacement' in e]}
    regions = components.fields(template['pages'][proposal['template_page']])
    assignments = validate_assignments(proposal, regions) if proposal['role'] == 'body' else {}
    automatic = not assignments and proposal['role'] == 'body'
    if automatic:
        regions = flow_fields(template['pages'][proposal['template_page']], regions)
        assignments = automatic_assignments(blocks, regions)
        # A source subheading is lossless content, not a generated display title.
        # Put a long one in its paired body instead of creating title-only pages.
        layout = template['pages'][proposal['template_page']]
        for block in blocks:
            field = next((f for f in regions if f['name'] == assignments.get(block['id']) and f['role'] == 'itemTitle'), None)
            if field is None:
                continue
            element = effective(layout['elements'][field['element']], template, 'body', scale)
            if fits_text(block['text'], element, body=True)[1]:
                body = next((f for f in regions if f['role'] == 'itemBody' and f['order'] == field['order']), None)
                if body:
                    assignments[block['id']] = body['name']
    aliases = continuation_fields(regions, assignments) if automatic and assignments else {}
    pending = units(blocks, headers or html.table_headers(blocks)) if proposal['role'] == 'body' else [dict(a) for a in agenda]
    documents = []
    while True:
        soup, reference, layout, fields = base(template, proposal, page_number + len(documents))
        before = sum(len(u['text']) for u in pending)
        if proposal['role'] == 'body':
            targets = [f for f in (regions if automatic else fields) if f['role'] in components.CONTENT_ROLES]
            if automatic and assignments:
                targets.sort(key=lambda f: (f['order'], 0 if f['role'] == 'itemTitle' else 1, f['element']))
            if not assignments:
                # Preserve source order and use semantic body regions; tiny headings
                # never become the only carrier for long prose.
                targets = [f for f in targets if f['role'] in {'body', 'itemBody'}]
            for f in targets:
                element = effective(layout['elements'][f['element']], template, proposal['role'], scale)
                consumed, last_row = '', None
                while pending:
                    unit = pending[0]
                    if assignments and assignments[unit['id']] != aliases.get(f['name'], f['name']):
                        break
                    prefix = unit.get('prefix', '')
                    row = unit.get('row')
                    leading = '\n' if consumed and (row is None or row != last_row) else ''
                    candidate = consumed + leading + prefix + unit['text']
                    part, _ = fits_text(candidate, element, scale, True)
                    count = len(part) - len(consumed) - len(leading) - len(prefix)
                    if count <= 0:
                        break
                    value = unit['text'][:count]
                    put(soup, f, element, value, source=unit['id'], body=True, prefix=prefix, join=row is not None and row == last_row)
                    consumed += leading + prefix + value
                    last_row = row
                    unit['text'] = unit['text'][len(value):]
                    if unit['text']:
                        break
                    pending.pop(0)
                if assignments and not automatic and pending and assignments[pending[0]['id']] == f['name']:
                    break
        elif proposal['role'] == 'contents':
            for f in sorted([f for f in fields if f['role'] == 'contentsItem'], key=lambda f: (f['order'], f['element'])):
                if not pending:
                    break
                unit = pending[0]
                e = layout['elements'][f['element']]
                number = next((n for n in fields if n['role'] == 'contentsNumber' and n['order'] == f['order']), None)
                subtitle = next((n for n in fields if n['role'] == 'contentsSubtitle' and n['order'] == f['order']), None)
                if layout.get('_semantic_contents') and subtitle and unit.get('display_title'):
                    # A short semantic heading and its complete description
                    # stay together. Never split a word across directory pages.
                    put(soup, f, e, unit['display_title'])
                    put(soup, subtitle, layout['elements'][subtitle['element']], unit['text'], agenda=unit['id'])
                    if number:
                        ne = layout['elements'][number['element']]
                        put(soup, number, ne, fixed.number(ne, unit.get('number', 1)))
                    pending.pop(0)
                    continue
                prefix = ''
                if number and not unit.get('continued'):
                    match = re.match(r'^(\s*\d+[.、]?\s+)', unit['text'])
                    if match:
                        prefix = match[1]
                        unit['text'] = unit['text'][len(prefix):]
                if number:
                    put(soup, number, layout['elements'][number['element']], prefix or fixed.number(layout['elements'][number['element']], unit.get('number',1)), agenda=unit['id'] if prefix else None)
                value, remaining = fits_text(unit['text'], e, scale)
                if not value:
                    raise ValueError('目录区域不足一行，请调整模板目录项尺寸')
                put(soup, f, e, value, agenda=unit['id'])
                if remaining:
                    unit.update(text=remaining, continued=True)
                    # Continue this entry on the next page. Filling subsequent
                    # slots on this page can reverse fragments in PPTX z-order.
                    break
                else:
                    pending.pop(0)
        document = html.validate_html(str(soup), reference)
        html.validate_heading(document, proposal)
        html.validate_sample_text(document, reference, proposal, blocks, agenda)
        documents.append(document)
        if not pending:
            break
        if sum(len(u['text']) for u in pending) >= before:
            raise ValueError('模板字段无法承载正文，请检查文字区域容量与字段对应顺序')
        if len(documents) >= html.MAX_PAGES or proposal['role'] not in {'body', 'contents'}:
            raise ValueError('模板续页超过页数限制')
    html.validate_sources(documents, blocks, agenda, headers)
    return documents
