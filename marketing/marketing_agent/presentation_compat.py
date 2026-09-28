"""Recover imperfect planning output without silently declaring claims verified."""
import json
import re
from copy import deepcopy


# Treat dotted dates/version numbers as whole tokens, never decimal fragments.
NUMBER = re.compile(r'(?<![\d.])\d+(?:\.\d+)*(?![\d.])')


def numbers(text):
    return set(NUMBER.findall(re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text)))


def evidence_for(ids, blocks):
    # Headings belong to their source blocks too (e.g. a dated section).
    return '\n'.join('\n'.join(str(blocks[i].get(k, '')) for k in ('heading', 'source')) for i in ids)


def record(log, page, index, field, before, after, reason, blocks, status='corrected'):
    log.append({'page': index + 1, 'title': page.get('title', ''), 'field': field,
                'status': status, 'before': deepcopy(before), 'after': deepcopy(after), 'reason': reason,
                'sources': [{'id': i, 'line': blocks[i].get('source_line'), 'heading': blocks[i].get('heading', '')}
                            for i in page.get('source_ids', []) if i in blocks]})


def fields(page):
    for key in ('title', 'subtitle'):
        yield key, page.get(key, '')
    ordinal = (page.get('layout') in {'columns', 'steps', 'comparison'} and
               len(page.get('items', [])) >= 2 and
               [v.get('value', '') for v in page['items']] ==
               [f'{i+1:02d}' for i in range(len(page['items']))])
    for i, item in enumerate(page.get('items', [])):
        for key in ('label', 'text', 'value'):
            if key == 'value' and ordinal:
                continue
            yield f'items[{i}].{key}', item.get(key, '')
    if page.get('layout') == 'chart':
        chart = page['chart']
        yield 'chart.source_note', chart.get('source_note', '')
        for i, label in enumerate(chart['categories']):
            yield f'chart.categories[{i}]', label
        for i, series in enumerate(chart['series']):
            yield f'chart.series[{i}].name', series['name']


def add_exact_citations(page, blocks, log, index):
    """Only attach full phrase matches; finding the same number is not evidence."""
    def clean(value):
        return re.sub(r'\s+|\*\*|[“”「」]', '', value)
    for field, text in fields(page):
        missing = numbers(text) - numbers(evidence_for(page['source_ids'], blocks))
        if not missing:
            continue
        # Ignore generic isolated numbers and short labels. Require a substantial
        # exact phrase containing the missing claim, not fuzzy topical similarity.
        phrases = [clean(t) for t in re.split(r'[，。；：｜\n]', text)]
        phrases = [t for t in phrases if len(t) >= 8 and numbers(t) & missing]
        candidates = [b for b in blocks.values() if b['id'] not in page['source_ids'] and
                      any(t in clean(b['source']) for t in phrases)]
        if candidates:
            before = list(page['source_ids'])
            page['source_ids'].append(candidates[0]['id'])
            record(log, page, index, 'source_ids', before, page['source_ids'],
                   f'{field} 的完整短句在原稿中有明确匹配，补充引用', blocks)


def source_fallback(page, blocks):
    """An invalid metric/chart becomes source text, never fabricated chart data."""
    ids = [i for i in page.get('source_ids', []) if i in blocks] or [next(iter(blocks))]
    def excerpt(text, limit):
        text = re.sub(r'\*\*|__|`', '', text).strip()
        if len(text) <= limit:
            return text
        # Cut at a clause boundary; never cut a numeric claim in the middle.
        clauses = re.split(r'(?<=[。；，！？])', text)
        result = ''
        for clause in clauses:
            if len(result + clause) > limit:
                break
            result += clause
        return result or '完整原稿及数据口径见本页备注'
    return {'layout': 'columns', 'title': excerpt(blocks[ids[0]].get('heading', '原稿内容'), 42),
            'subtitle': '按原稿保留，数据口径待核验；完整内容见备注', 'section': '', 'source_ids': ids,
            'items': [{'label': '原稿摘录', 'text': excerpt(blocks[i]['source'], 95), 'value': ''} for i in ids[:4]],
            'compatibility_fallback': True}


def normalize_page(page, blocks, log, index):
    from .presentation_design import LAYOUTS
    from .presentation_pages import COMPOSITIONS
    page = deepcopy(page) if isinstance(page, dict) else {}
    ids = page.get('source_ids', [])
    valid = list(dict.fromkeys(i for i in ids if isinstance(i, str) and i in blocks)) if isinstance(ids, list) else []
    if valid != ids or not valid:
        page['source_ids'] = valid or [next(iter(blocks))]
        record(log, page, index, 'source_ids', ids, page['source_ids'], '移除无效引用；无有效引用时回退原稿摘录', blocks)
        if not valid:
            recovered = source_fallback(page, blocks)
            record(log, recovered, index, 'page', page, recovered, '页面没有有效来源，使用原稿开篇摘录继续', blocks)
            return recovered
    if not isinstance(page.get('layout'), str):
        before = page.get('layout')
        page['layout'] = 'columns'
        record(log, page, index, 'layout', before, 'columns', '无效页型使用标准栏目版式', blocks)
    if page.get('layout') in COMPOSITIONS:
        before = page['layout']
        page['composition'] = before
        page['layout'] = 'statement' if before == 'hero_statement' else 'columns'
        record(log, page, index, 'layout', before, page['layout'], '将构图名称移至 composition 字段', blocks)
    if page.get('layout') not in LAYOUTS:
        before = page.get('layout')
        page['layout'] = 'columns'
        record(log, page, index, 'layout', before, 'columns', '未知页型使用标准栏目版式', blocks)
    if page.get('composition') and (not isinstance(page['composition'],str) or page['composition'] not in COMPOSITIONS):
        before = page.pop('composition')
        record(log, page, index, 'composition', before, None, '未知构图使用标准版式', blocks)
    return page


def write_log(folder, data):
    entries = data.get('corrections', [])
    blocks = {b['id']: b for b in data.get('source_blocks', [])}
    pages = data.get('pages', [])
    for fallback in data.get('page_generation', {}).get('fallbacks', []):
        index = fallback['page'] - 1
        page = pages[index]
        if not any(c['page']==index+1 and c['field']=='custom' for c in entries):
            record(entries,page,index,'custom',page.get('composition','自定义版式'),page['layout'],
                   '自定义排版未通过，使用标准版式继续：'+fallback['reason'],blocks)
    outline = folder / 'outline.json'
    if outline.exists():
        pages = json.loads(outline.read_text()).get('pages', [])
        report = folder / 'report.json'
        if report.exists():
            for repair in json.loads(report.read_text()).get('repairs', []):
                page = pages[repair['page']]
                index = page.get('plan_page', repair['page']+1) - 1
                field = f"rendering[{repair['page']+1}].attempt[{repair['attempt']}]"
                if not any(c['field']==field for c in entries):
                    record(entries,page,index,field,repair['issues'],'自动拆页或调整版式；文字保留',
                           '浏览器检测到排版容量问题并已修复',blocks)
        for item in entries:
            item['final_pages'] = [i + 1 for i, p in enumerate(pages) if p.get('plan_page') == item['page']]
    data['corrections'] = entries
    (folder / 'corrections.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2))
    lines = ['# PPT 兼容处理与待核验日志', '', '原始文稿未改写；页码对应分页方案。已修正与待核验分开记录，待核验不代表事实已验证。', '']
    for item in entries:
        location = '成品第 ' + '、'.join(map(str, item['final_pages'])) + ' 页' if item.get('final_pages') else f"规划第 {item['page']} 页"
        lines.extend([f"## {location} · {item['title']}", '',
                      f"位置：`{item['field']}`；状态：{'待核验' if item['status'] == 'warning' else '已修正'}", '',
                      item['reason'], '', '处理前：', '```json', json.dumps(item['before'], ensure_ascii=False), '```',
                      '处理后：', '```json', json.dumps(item['after'], ensure_ascii=False), '```',
                      '原稿位置：' + '；'.join(f"{s['id']}（第 {s['line']} 行，{s['heading']}）" for s in item['sources']), ''])
    if not entries:
        lines.append('未发生兼容修正或新增待核验项。')
    (folder / 'corrections.md').write_text('\n'.join(lines))
