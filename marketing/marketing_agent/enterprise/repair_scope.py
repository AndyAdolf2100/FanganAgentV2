"""Constrain a local model rewrite without authoring or patching its layout.

Only evidence that can be resolved against the current DOM grants an editable
region. Unrelated DOM is losslessly aliased, then compared after expansion.
Global CSS is immutable during a local repair; the model can use inline styles
inside the selected component. DOM equality does not prove equal appearance:
browser and Seed checks still assess flow, geometry and visual side effects.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


VERSION = 'enterprise-local-repair-v2'
MARKER = 'data-enterprise-repair-region'
SOURCE_ATTRS = ('data-source-block', 'data-agenda-item', 'data-metadata')


class _Unscoped(ValueError):
    pass


def _unique(nodes):
    result = []
    for node in nodes:
        if not any(node is existing for existing in result):
            result.append(node)
    return result


def _within(node, parent):
    return node is parent or any(ancestor is parent for ancestor in node.parents)


def _sources(node):
    return [n for n in [node, *node.find_all(True)] if any(n.has_attr(attr) for attr in SOURCE_ATTRS)]


def _source_identity(node):
    return tuple((attr, str(node[attr])) for attr in SOURCE_ATTRS if node.has_attr(attr))


def _tree(node):
    if isinstance(node, Comment):
        return ('comment', str(node))
    if isinstance(node, NavigableString):
        return ('text', str(node)) if str(node).strip() else None
    attrs = tuple(sorted((k, tuple(v) if isinstance(v, list) else v)
                         for k, v in node.attrs.items() if k != MARKER))
    return (node.name, attrs, tuple(value for child in node.children
            if (value := _tree(child)) is not None))


def _masked(document, markers):
    soup = BeautifulSoup(document, 'html.parser')
    for marker in markers:
        found = soup.find_all(attrs={MARKER: marker})
        if len(found) != 1:
            raise ValueError(f'局部修复范围不一致：区域{marker}须保留且只能出现一次')
        found[0].replace_with(f'__ALLOWED_REGION_{marker}__')
    return _tree(soup)


def _component(node, soup, contract):
    """Expand a text anchor to its authored body component, never to a brand."""
    protected = {str(i) for i in contract.get('protected_elements', [])}
    chain = [node, *node.parents]
    if any(isinstance(parent, Tag) and str(parent.get('data-template-element')) in protected
           for parent in chain):
        return None
    if any(str(child.get('data-template-element')) in protected for child in node.find_all(True)):
        return None
    body = soup.select_one('[data-enterprise-body]')
    if body is not None and any(parent is body for parent in chain):
        if node is body:
            return None  # Whole-body reflow requires a separate strategy.
        owners = [p for p in chain if isinstance(p, Tag) and any(p.has_attr(a) for a in SOURCE_ATTRS)]
        allowed_sources = {_source_identity(n) for n in _sources(node)}
        if owners:
            allowed_sources.add(_source_identity(owners[0]))
        if len(allowed_sources) > 1:
            return None  # An explicit wrapper is not proof all its sources may change.
        target = node
        while target.parent is not body:
            parent = target.parent
            if not isinstance(parent, Tag) or not _within(parent, body):
                break
            if any(_source_identity(n) not in allowed_sources for n in _sources(parent)):
                break
            if any(str(n.get('data-template-element')) in protected for n in [parent, *parent.find_all(True)]):
                break
            target = parent
        return target
    # A title or fixed-page text frame may change internally; the existing
    # template contract/browser check still protects its outer geometry.
    frames = {str(i) for i in contract.get('text_frames', [])}
    return next((parent for parent in chain if isinstance(parent, Tag)
                 and str(parent.get('data-template-element')) in frames), None)


def _resolve(soup, issue, contract):
    nodes = []
    for source in issue.get('source_candidates', []):
        for attr in SOURCE_ATTRS:
            nodes.extend(soup.find_all(attrs={attr: str(source)}))
    for candidate in issue.get('element_candidates', []):
        key = str(candidate)
        # No arbitrary selector evaluation or guessed coordinates.
        found = []
        for attr in ('id', 'data-template-element', 'data-ppt-slot'):
            found.extend(soup.find_all(attrs={attr: key}))
        found = _unique(found)
        if len(found) > 1:
            raise _Unscoped('显式元素ID对应多个DOM节点，无法唯一定位局部修复')
        nodes.extend(found)
    explicit = bool(nodes)
    # Text anchors supplement missing IDs, never expand a unique cited ID to
    # every occurrence of the same label elsewhere on the slide.
    if not explicit:
        for text in issue.get('text_anchors', []):
            anchor = str(text).strip()
            if not anchor:
                continue
            matches = [node for node in soup.find_all(True)
                       if node.name not in {'style', 'script', 'head'}
                       and anchor in node.get_text('', strip=True)]
            matches = [node for node in matches if not any(
                isinstance(child, Tag) and anchor in child.get_text('', strip=True)
                for child in node.children)]
            if len(matches) > 1:
                raise _Unscoped('文字锚点重复且没有唯一显式ID，未启用硬局部范围')
            nodes.extend(matches)
    result = []
    for node in nodes:
        target = _component(node, soup, contract)
        if target is not None and not any(target is item for item in result):
            result.append(target)
    # A selected container subsumes its descendant; don't create nested masks.
    return [node for node in result if not any(parent is other
            for parent in node.parents for other in result)], explicit


def _nonlocal_dependency(soup, targets):
    """Conservative evidence of cross-region effects, not a CSS safety proof."""
    if not targets:
        return None
    for style in soup.find_all('style'):
        css = re.sub(r'/\*.*?\*/', '', style.get_text(), flags=re.S)
        for selector in re.findall(r'([^{}]+)\{', css):
            if re.search(r':has\s*\(', selector, re.I):
                return '存在:has关系选择器，局部DOM修改可能改变其它区域外观'
            cleaned = re.sub(r'\[[^]]*\]|\([^)]*\)', '', selector)
            if re.search(r'[+~]', cleaned):
                return '存在兄弟关系CSS选择器，未证明局部修改不会影响其它区域'

    def owner(node):
        return next((i for i, target in enumerate(targets) if _within(node, target)), None)

    for node in soup.find_all(True):
        refs = []
        for key, value in node.attrs.items():
            if not isinstance(value, str):
                continue
            refs.extend(re.findall(r'url\(\s*[\'"]?#([^\s)\'"]+)', value, re.I))
            if key in {'href', 'xlink:href'} and value.startswith('#'):
                refs.append(value[1:])
        if node.name == 'style':
            refs.extend(re.findall(r'url\(\s*[\'"]?#([^\s)\'"]+)', node.get_text(), re.I))
        for ref in refs:
            definitions = soup.find_all(id=ref)
            if any(owner(node) != owner(definition) and (owner(node) is not None or owner(definition) is not None)
                   for definition in definitions):
                return '存在跨修复区域的SVG/CSS片段引用，未启用硬局部范围'
    return None


def _chart_map(charts):
    if not isinstance(charts, list):
        raise ValueError('局部修复图表资料须为逐页规范图表数组')
    result = {}
    for chart in charts:
        if not isinstance(chart, dict) or not isinstance(chart.get('id'), str) or not chart['id'] or chart['id'] in result:
            raise ValueError('局部修复图表ID缺失或重复')
        result[chart['id']] = json.dumps(chart, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return result


@dataclass
class LocalRepairScope:
    documents: list
    resources: dict
    manifest: dict
    baselines: list

    def validate(self, documents, charts=None):
        if len(documents) != len(self.baselines):
            raise ValueError('局部修复须保持已有分页；容量不足须先升级正文布局策略')
        if any(b['has_charts'] for b in self.baselines) and charts is None:
            raise ValueError('局部修复须提供逐页规范图表资料，不能遗漏原图表只读校验')
        if charts is not None and (not isinstance(charts, list) or len(charts) != len(documents)):
            raise ValueError('局部修复图表资料与已有分页不一致')
        for number, (document, baseline) in enumerate(zip(documents, self.baselines), 1):
            soup = BeautifulSoup(document, 'html.parser')
            if [_tree(n) for n in soup.find_all('style')] != baseline['styles']:
                raise ValueError(f'局部修复越界：第{number}页修改了全局style规则；仅在目标组件中使用内联样式')
            if _masked(document, baseline['markers']) != baseline['tree']:
                raise ValueError(f'局部修复越界：第{number}页改动了非目标内容或全局CSS；'
                                 '保留current_pages中的只读别名，仅修改明确标出的局部区域，仍返回完整HTML')
            targets = [soup.find(attrs={MARKER: marker}) for marker in baseline['markers']]
            dependency = _nonlocal_dependency(soup, targets)
            if dependency:
                raise ValueError('局部修复越界：' + dependency)
            if charts is not None:
                actual = _chart_map(charts[number - 1])
                if any(actual.get(identifier) != value for identifier, value in baseline['readonly_charts'].items()):
                    raise ValueError(f'局部修复越界：第{number}页修改或遗漏了非目标图表的规范数据')


def prepare_local_repair(current, policy, *, report=None):
    """Return None when evidence cannot safely delimit the whole repair task."""
    def decline(reason):
        if report is not None:
            report.update(version=VERSION, enforced=False, reason=reason)
        return None
    if not current or not isinstance(policy, dict) or policy.get('scope') != 'local':
        return decline('无当前页面或策略不是局部修复')
    refs = policy.get('current_issue_refs', [])
    if not refs:
        return decline('没有可定位的当前问题引用')
    soups = [BeautifulSoup(page['html'], 'html.parser') for page in current]
    regions = [[] for _ in current]
    for issue in refs:
        if not isinstance(issue, dict):
            return decline('当前问题引用格式无效')
        matched = False
        anchor_pages = 0
        for i, page in enumerate(current):
            if issue.get('slide_id') and page.get('slide_id') and issue['slide_id'] != page['slide_id']:
                continue
            try:
                found, explicit = _resolve(soups[i], issue, page.get('template_contract', {}))
            except _Unscoped as exc:
                return decline(str(exc))
            anchor_pages += bool(found) and not explicit
            matched = matched or bool(found)
            regions[i].extend(node for node in found if not any(node is old for old in regions[i]))
        if not matched:
            # Mixed local/unmapped findings must not silently lose permissions
            # to fix the latter. Retain ordinary source/brand/browser guards.
            return decline('当前DOM不能安全完整定位全部问题；可能是受保护节点、整正文或包含其它独立来源的包装器')
        if anchor_pages > 1:
            return decline('文字锚点匹配多个页面且缺少唯一显式ID，未启用硬局部范围')
    key = hashlib.sha256(json.dumps({'html': [p['html'] for p in current],
            'charts': [p.get('enterprise_charts', []) for p in current], 'refs': refs,
            'version': VERSION}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    documents, resources, baselines, descriptions = [], {}, [], []
    for i, soup in enumerate(soups):
        targets = [node for node in regions[i] if not any(parent is other
                   for parent in node.parents for other in regions[i])]
        dependency = _nonlocal_dependency(soup, targets)
        if dependency:
            return decline(dependency)
        original_charts = deepcopy(current[i].get('enterprise_charts', []))
        try:
            chart_values = _chart_map(original_charts)
        except ValueError as exc:
            return decline(str(exc))
        editable_chart_ids = {node['data-enterprise-chart'] for target in targets
                              for node in [target, *target.find_all(attrs={'data-enterprise-chart': True})]
                              if node.has_attr('data-enterprise-chart')}
        markers = []
        for n, node in enumerate(targets):
            marker = f'{key}-p{i+1}-r{n+1}'
            node[MARKER] = marker
            markers.append(marker)
        baselines.append({'markers': markers, 'tree': _masked(str(soup), markers),
                          'styles': [_tree(n) for n in soup.find_all('style')], 'has_charts': bool(original_charts),
                          'readonly_charts': {key: value for key, value in chart_values.items() if key not in editable_chart_ids}})
        descriptions.append({'page': i + 1, 'regions': markers,
                             'mode': 'local' if markers else 'unchanged',
                             'readonly_chart_ids': sorted(baselines[-1]['readonly_charts'])})

        def pack(node):
            if not isinstance(node, Tag) or any(node is target for target in targets):
                return
            contains_target = any(any(parent is node for parent in target.parents) for target in targets)
            if not contains_target and node.name != '[document]':
                token = f'__PPT_REPAIR_LOCK_{key}_{len(resources)}__'
                resources[token] = str(node)
                node.replace_with(token)
            else:
                for child in list(node.children):
                    pack(child)

        pack(soup)
        documents.append(str(soup))
    manifest = {'version': VERSION, 'enforced': True, 'pages': descriptions,
        'appearance_guarantee': False,
        'limitations': '仅锁定非目标DOM、全局CSS源码与非目标图表数据；常规CSS/布局副作用仍须浏览器和单页Seed复核。',
        'instruction': '仍输出完整pages/HTML。以current_pages为底稿，原样保留__PPT_REPAIR_LOCK_只读节点别名；'
            '仅修改data-enterprise-repair-region标出的组件，保留这些标记且不复制。'
            '局部样式写在目标区域内联style，不改head/style全局规则，不添加style节点。'
            '保留原文与分页；未标记页面原样返回。若仍放不下，明确报告容量原因，不能擅自改其他区域。'}
    if report is not None:
        report.update(deepcopy(manifest))
    return LocalRepairScope(documents, resources, manifest, baselines)
