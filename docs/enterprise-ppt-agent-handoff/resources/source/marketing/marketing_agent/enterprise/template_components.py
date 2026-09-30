"""Compile authored HTML layouts into named, capacity-aware text components."""
import math
import re

from . import template_html as html
from .template_capacity import metrics, original_size

STRATEGY = 'template-components-v1'
LAYOUTS = {'auto', 'text', 'items', 'comparison', 'timeline', 'table', 'custom'}
CONTENT_ROLES = {'body', 'itemBody', 'itemTitle', 'subtitle'}


def fields(page):
    result = []
    for index, element in enumerate(page['elements']):
        role = html.editable_role(element, page['role'])
        if not role:
            continue
        order = element.get('order', 1)
        name = element.get('fieldName') or f'{role}_{order}_{index}'
        width, rows, _ = metrics(element)
        result.append({'name': name, 'element': index, 'role': role, 'order': order,
                       'width': element['width'], 'height': element['height'],
                       'font_size': original_size(element),
                       'capacity': max(0, math.floor(width) * rows),
                       'vector': element.get('artworkType') == 'outlinedText'})
    return result


def layout_kind(page, regions):
    configured = page.get('layoutKind', 'auto')
    if configured != 'auto':
        return configured
    return 'items' if sum(f['role'] == 'itemBody' for f in regions) > 1 else 'text'


def inspect(template):
    """Metadata only: never mutate a draft, a published snapshot or its geometry."""
    errors, warnings, catalog = [], [], []
    for index, page in enumerate(template['pages']):
        if page['role'] == 'exclude':
            continue
        regions = fields(page)
        prefix = f'第 {index + 1} 页'
        names = [f['name'] for f in regions]
        if len(names) != len(set(names)):
            errors.append(f'{prefix}有重复的填充字段名，请为每个文字区域设置不同名称')
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', name) for name in names):
            errors.append(f'{prefix}字段名须以英文字母开头，仅包含字母、数字和下划线，最多80字符')
        if page.get('layoutKind', 'auto') not in LAYOUTS:
            errors.append(f'{prefix}版式类型无效')
        roles = {f['role'] for f in regions}
        for element in page['elements']:
            sample = ''.join(r.get('text', '') for p in element.get('paragraphs', []) for r in p.get('runs', [])).strip()
            if sample and not html.editable_role(element, page['role']):
                warnings.append(f'{prefix}固定文字“{sample[:45]}”会原样保留，请确认不是业务示例文案；章节页仅序号和标题可替换')
        if 'title' not in roles:
            (warnings if page['role'] == 'body' else errors).append(f'{prefix}缺少可填充的页面标题'+('；生成时参考顶部文字区域识别标题' if page['role']=='body' else ''))
        if page['role'] == 'body' and not roles.intersection({'body', 'itemBody'}):
            warnings.append(f'{prefix}正文标签仅作参考，生成时会在页眉、标题与页脚之间重新排版')
        if page['role'] == 'contents' and 'contentsItem' not in roles:
            errors.append(f'{prefix}缺少目录项标签')
        if page['role'] == 'section' and 'sectionNumber' not in roles:
            warnings.append(f'{prefix}没有章节序号区域，将仅填充章节标题')
        paired = {f['order'] for f in regions if f['role'] == 'itemBody'}
        for field in regions:
            e = page['elements'][field['element']]
            if field['role'] == 'itemTitle' and field['order'] not in paired:
                warnings.append(f'{prefix}第 {field["order"]} 项标题没有对应正文，请检查配对顺序')
            if field['capacity'] == 0:
                warnings.append(f'{prefix}字段 {field["name"]} 按当前字号不足一行，请检查尺寸')
            if e.get('labelSource') == 'rule':
                warnings.append(f'{prefix}字段 {field["name"]} 为自动识别标签，请核对用途')
        catalog.append({'template_page': index, 'name': page.get('name', ''),
                        'role': page['role'], 'allowed_roles': [page['role']],
                        'layout_kind': layout_kind(page, regions), 'fields': regions,
                        'capacity': sum(f['capacity'] for f in regions if f['role'] in {'body', 'itemBody'}),
                        'rule': html.RULES[page['role']]})
    for role, label in [('cover', '首页'), ('body', '正文'), ('ending', '尾页')]:
        if not any(p['role'] == role for p in catalog):
            errors.append(f'套装缺少{label}版式')
    return {'schema': 1, 'engine': STRATEGY, 'valid': not errors,
            'errors': errors, 'warnings': list(dict.fromkeys(warnings)), 'layouts': catalog}


def catalog(template):
    report = inspect(template)
    # Old published suites may contain unusable optional body variants. Keep
    # their immutable revision intact and select only fully labelled variants.
    # New component publications must pass the complete management audit.
    if report['errors'] and (template.get('published') or {}).get('componentSchema') != 1:
        invalid = {p['template_page'] for p in report['layouts'] if p['role'] == 'body'
                   and any(e.startswith(f'第 {p["template_page"] + 1} 页') for e in report['errors'])}
        remaining = [p for p in report['layouts'] if p['template_page'] not in invalid]
        errors = [e for e in report['errors'] if not any(e.startswith(f'第 {i + 1} 页') for i in invalid)]
        if not errors and any(p['role'] == 'body' for p in remaining):
            return remaining
    if report['errors']:
        raise ValueError('请在企业模板管理中完成打标：' + '；'.join(report['errors']))
    return report['layouts']
