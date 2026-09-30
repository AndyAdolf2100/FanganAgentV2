"""Small, deterministic template facts for planning and repair selection.

This is neither a vision analysis nor permission to change a template. Existing
Seed/reference and contract caches remain their owners' responsibility. Optional
contract/browser evidence is used only after the caller binds it to the exact
template snapshot (the enterprise job already verifies its input fingerprint).
``cache_root`` is the profile cache directory itself, not its parent.
"""
from collections import Counter
from copy import deepcopy
import fcntl
import hashlib
import json
import math
from pathlib import Path
import tempfile

SCHEMA_VERSION = 'enterprise-layout-profile-v1'
MAX_PAGE_CHARS = 6000
BODY_LABELS = {'body', 'itemBody', 'itemTitle', 'contentsItem', 'contentsNumber', 'contentsSubtitle'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(value):
        digest.update(chunk.encode())
    return digest.hexdigest()


def template_fingerprint(template):
    """Include assets, labels, fonts, geometry and revision, even if ID is reused."""
    return _hash(template)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _rect(value):
    if not isinstance(value, dict) or any(not _number(value.get(k)) for k in ('x', 'y', 'width', 'height')):
        return None
    if value['width'] <= 0 or value['height'] <= 0:
        return None
    return {k: value[k] for k in ('x', 'y', 'width', 'height')}


def _frame(value, template):
    box = _rect(value)
    if box and box['x'] >= 0 and box['y'] >= 0 and box['x'] + box['width'] <= template['width'] and box['y'] + box['height'] <= template['height']:
        return box
    return None


def _short(value, limit=120):
    return value[:limit] if isinstance(value, str) else None


def _typography(element):
    paragraphs = element.get('paragraphs', [])
    runs = [r for p in paragraphs for r in p.get('runs', [])]
    alignments = sorted({p['align'] for p in paragraphs if p.get('align') in {'left', 'center', 'right', 'justify'}})
    sizes = sorted({r['size'] for r in runs if _number(r.get('size')) and r['size'] > 0})
    fonts = sorted({r['font'][:120] for r in runs if isinstance(r.get('font'), str) and r['font']})
    source_fonts = sorted({r['sourceFont'][:120] for r in runs if isinstance(r.get('sourceFont'), str) and r['sourceFont']})
    return {'status': 'declared' if sizes or fonts else 'unknown', 'font_sizes': sizes[:6],
            'fonts': fonts[:3], 'source_fonts': source_fonts[:3], 'alignments': alignments,
            'truncated': len(sizes) > 6 or len(fonts) > 3 or len(source_fonts) > 3,
            'font_loading_verified': False}


def _element(index, item):
    text = '\n'.join(''.join(str(r.get('text', '')) for r in p.get('runs', [])) for p in item.get('paragraphs', []))
    return {'element_index': index, 'element_id': _short(item.get('id'), 200), 'kind': item.get('kind'),
            'label': item.get('textRole', item.get('artworkTextRole')), 'label_source': item.get('labelSource', 'unknown'),
            'binding': item.get('binding', 'unknown'), 'fixed': item.get('fixed') is True,
            'frame': _rect(item), 'rotation': item.get('rotation', 0),
            'has_transform': bool(item.get('rotation') or item.get('matrix') not in (None, [1, 0, 0, 1])),
            'typography': _typography(item), 'sample_text': text[:60], 'sample_character_count': len(text),
            'sample_text_truncated': len(text) > 60}


def _measurement(probe_page, element_index):
    layout = probe_page.get('layout_measurements', {}) if isinstance(probe_page, dict) else {}
    rows = [r for r in layout.get('titles', []) if str(r.get('template_element')) == str(element_index)]
    if len(rows) != 1:
        return {'status': 'unknown', 'reason': '没有唯一绑定到该模板元素的浏览器标题测量'}
    row = rows[0]
    result = {'status': 'browser_measured', 'applies_to': 'template_sample_only',
              'screenshot_sha': probe_page.get('screenshot_sha'), 'html_sha256': probe_page.get('html_sha256')}
    for key in ('font_size_px', 'available_width_px', 'nowrap_width_px', 'line_count', 'authored_break_count'):
        if _number(row.get(key)):
            result[key] = row[key]
    for key in ('font_family', 'font_weight', 'line_height', 'white_space'):
        if isinstance(row.get(key), str):
            result[key] = _short(row[key])
    for key in ('rect', 'text_bounds'):
        if _rect(row.get(key)):
            result[key] = _rect(row[key])
    return result


def _page_profile(template, page, index, contract, probe_page, evidence_status, binding, cache_key):
    items = page['elements']
    labels = Counter(e.get('textRole', e.get('artworkTextRole', 'unlabelled')) for e in items)
    declared_titles = [i for i, e in enumerate(items) if e.get('textRole', e.get('artworkTextRole')) == 'title']
    title_index = contract.get('title_element') if isinstance(contract, dict) else None
    title_index = title_index if type(title_index) is int and 0 <= title_index < len(items) else None
    title_indexes = list(dict.fromkeys(([title_index] if title_index is not None else []) + declared_titles))
    titles = []
    for i in title_indexes[:3]:
        row = _element(i, items[i])
        row.update(selected_by_contract=i == title_index,
                   contract_frame=_frame(contract.get('title_frame'), template) if i == title_index else None,
                   browser_sample=_measurement(probe_page, i), content_capacity={'status': 'unknown',
                   'reason': '原文字数和字体几何不是新标题容量；新内容须经浏览器测量及截图检查'})
        titles.append(row)
    slots = [_element(i, e) for i, e in enumerate(items) if e.get('textRole', e.get('artworkTextRole')) in BODY_LABELS]
    groups = {}
    for i, e in enumerate(items):
        if type(e.get('order')) is int and e['order'] > 0 and e.get('textRole') in BODY_LABELS:
            groups.setdefault(e['order'], []).append(i)
    body_frame = _frame(contract.get('body_frame'), template) if isinstance(contract, dict) else None
    layout = page.get('layoutKind', 'auto')
    value = {'schema_version': SCHEMA_VERSION, 'template_sha256': binding['template_sha256'],
        'profile_key': cache_key, 'evidence_status': evidence_status,
        'layout_kind': {'value': layout, 'basis': 'frontend_declared'},
        'labels': dict(sorted(labels.items())), 'titles': titles,
        'body': {'status': 'contract_declared' if body_frame else 'unknown', 'frame': body_frame,
                 'canvas_area_ratio': round(body_frame['width'] * body_frame['height'] / (template['width'] * template['height']), 6) if body_frame else None,
                 'labelled_sample_regions': slots[:6],
                 'notice': '示例文字框不等于可自由重排正文区；合同区域也不是视觉安全区或容量保证'},
        'label_groups': [{'order': order, 'element_indexes': ids[:8], 'omitted_elements': max(0, len(ids) - 8)}
                         for order, ids in sorted(groups.items())[:8]],
        'adaptation': {'table': {'status': 'declared' if layout == 'table' else 'unknown', 'basis': 'layoutKind only'},
                       'chart': {'status': 'unknown', 'reason': '形状或表格标签不能证明图表类型、序列或数据适配'},
                       'internal_graphic_safe_area': 'unknown', 'visual_quality': 'unknown'},
        'artwork': {'shape_count': sum(e.get('kind') == 'shape' for e in items),
                    'image_count': sum(e.get('kind') == 'image' for e in items),
                    'vector_count': sum(bool(e.get('vector')) for e in items)},
        'protection': {'manual_brand_indexes': [i for i, e in enumerate(items)
                        if e.get('labelSource') == 'manual' and e.get('textRole', e.get('artworkTextRole')) == 'brand'][:20],
                       'contract_protected_count': len(contract.get('protected_elements', [])) if isinstance(contract, dict) else None,
                       'notice': '仅描述现有标签和合同，不新增、解除或替代品牌锁'},
        'summary': {'max_chars': MAX_PAGE_CHARS, 'titles_total': len(title_indexes), 'sample_regions_total': len(slots),
                    'label_groups_total': len(groups), 'truncated': False}}
    # Bound planning input without losing source counts or pretending omitted
    # candidates were seen. No paths, SVG primitive expansion, HTML or assets.
    for key, parent in (('labelled_sample_regions', value['body']), ('label_groups', value), ('titles', value)):
        while len(_json(value)) > MAX_PAGE_CHARS - 160 and parent[key]:
            parent[key].pop()
    summary = value['summary']
    summary.update(titles_sent=len(value['titles']), sample_regions_sent=len(value['body']['labelled_sample_regions']),
                   label_groups_sent=len(value['label_groups']))
    summary['truncated'] = (summary['titles_sent'] != len(title_indexes) or summary['sample_regions_sent'] != len(slots)
                            or summary['label_groups_sent'] != len(groups) or any(g['omitted_elements'] for g in value['label_groups']))
    if len(_json(value)) > MAX_PAGE_CHARS:
        raise ValueError('模板资料摘要超过长度上限；未发送模型请求')
    return {'template_page': index, 'page_id': _short(page.get('id'), 200), 'role': page.get('role', 'unknown'), 'layout_profile': value}


def build_layout_profile(template, *, contracts=None, probe=None, evidence_template_sha256=None, cache_root=None):
    """Return deterministic facts plus cache metadata; never call a model/render.

    Evidence omitted or bound to another snapshot is ignored, explicitly. A
    caller must bind only its already-validated current-template evidence.
    """
    if not isinstance(template, dict) or not isinstance(template.get('pages'), list) or not template['pages']:
        raise ValueError('模板资料缺少页面')
    if any(not _number(template.get(k)) or template[k] <= 0 for k in ('width', 'height')):
        raise ValueError('模板资料画布无效')
    if any(not isinstance(p, dict) or not isinstance(p.get('elements'), list) for p in template['pages']):
        raise ValueError('模板资料元素无效')
    fingerprint = template_fingerprint(template)
    evidence_bound = evidence_template_sha256 == fingerprint
    supplied = contracts is not None or probe is not None
    evidence_status = 'bound_to_template' if evidence_bound and supplied else 'ignored_binding_mismatch' if supplied else 'not_provided'
    binding = {'template_id': template.get('id'), 'template_revision': template.get('revision', template.get('published', {}).get('revision')),
               'template_sha256': fingerprint, 'contracts_sha256': _hash(contracts) if contracts is not None else None,
               'probe_sha256': _hash(probe) if probe is not None else None,
               'evidence_template_sha256': evidence_template_sha256}
    key = _hash({'schema_version': SCHEMA_VERSION, 'binding': binding})

    def build():
        pages = []
        probe_pages = (probe or {}).get('pages', []) if evidence_bound else []
        page_counts = Counter(p.get('page') for p in probe_pages if type(p.get('page')) is int)
        measurements = {p['page'] - 1: p for p in probe_pages if type(p.get('page')) is int and page_counts[p['page']] == 1}
        for i, page in enumerate(template['pages']):
            contract = (contracts or {}).get(str(i), (contracts or {}).get(i)) if evidence_bound else None
            pages.append(_page_profile(template, page, i, contract, measurements.get(i, {}), evidence_status, binding, key))
        return {'schema_version': SCHEMA_VERSION, 'binding': binding,
                'canvas': {k: template[k] for k in ('width', 'height')}, 'pages': pages,
                'notice': '确定性结构资料；不是视觉分析、推荐排名或布局/容量验收。未知项由现有模型与浏览器流程补证。'}

    if cache_root is None:
        return {**build(), 'cache': {'status': 'disabled', 'key': key}}
    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{key}.json'
    with (root / f'{key}.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(path.read_text())
            profile = cached['profile']
            if (cached['input_sha256'] == key and cached['profile_sha256'] == _hash(profile)
                and profile['binding'] == binding and profile['schema_version'] == SCHEMA_VERSION):
                return {**profile, 'cache': {'status': 'hit', 'key': key}}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        profile = build()
        with tempfile.NamedTemporaryFile('w', dir=root, prefix=f'{key}.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(_json({'input_sha256': key, 'profile_sha256': _hash(profile), 'profile': profile}))
        temporary.replace(path)
        return {**profile, 'cache': {'status': 'miss', 'key': key}}


def profile_catalog(template, **kwargs):
    """List with explicit zero-based template_page; excluded pages stay excluded."""
    profile = build_layout_profile(template, **kwargs)
    return [deepcopy(page) for page in profile['pages'] if page['role'] != 'exclude']
