from copy import deepcopy

from bs4 import BeautifulSoup
import pytest

from marketing_agent.enterprise.repair_scope import MARKER, prepare_local_repair
from marketing_agent.enterprise.template_html import unpack_resources


def page(slide='page-1'):
    return {'slide_id': slide, 'template_contract': {'protected_elements': [9], 'text_frames': [0]},
        'html': '''<!doctype html><html><head><style>.text{font-size:24px}</style></head><body>
        <div class="ppt-slide"><div data-template-element="9"><svg><path d="M 0 0 L 8 8"/></svg></div>
        <div data-template-element="0"><h1>企业标题</h1></div>
        <main data-enterprise-body="true"><section id="arrow" style="width:250px">
        <svg><path d="M 0 0 L 40 10 L 0 20 Z"/></svg><span data-source-block="b1">调性 × 视觉 × 文案</span></section>
        <section id="other"><p data-source-block="b2">第二段30万元。</p></section></main></div></body></html>'''}


def policy(**issue):
    return {'scope': 'local', 'current_issue_refs': [{'slide_id': 'page-1', **issue}]}


def repaired(scope):
    soup = BeautifulSoup(scope.documents[0], 'html.parser')
    soup.find(attrs={MARKER: True})['style'] = 'width:300px;padding:16px'
    return unpack_resources(str(soup), scope.resources)


@pytest.mark.parametrize('anchor', [{'source_candidates': ['b1']}, {'text_anchors': ['调性 × 视觉 × 文案']},
                                   {'element_candidates': ['arrow']}])
def test_local_arrow_and_label_can_resize_without_rewriting_unrelated_content(anchor):
    current = [page()]; before = deepcopy(current)
    scope = prepare_local_repair(current, policy(**anchor))
    assert scope and scope.manifest['enforced']
    assert '__PPT_REPAIR_LOCK_' in scope.documents[0]
    assert '第二段30万元' not in scope.documents[0]
    result = repaired(scope)
    scope.validate([result])
    assert 'width:300px;padding:16px' in result and '第二段30万元' in result
    assert current == before  # scope tools never alter a committed page


@pytest.mark.parametrize('change', ['unrelated_text', 'source_marker', 'global_css', 'new_local_style', 'lost_region', 'new_sibling'])
def test_local_repair_cannot_mutate_unrelated_dom_or_global_css(change):
    scope = prepare_local_repair([page()], policy(source_candidates=['b1']))
    soup = BeautifulSoup(repaired(scope), 'html.parser')
    if change == 'unrelated_text':
        soup.select_one('#other p').string = '第二段31万元。'
    elif change == 'source_marker':
        soup.select_one('#other p')['data-source-block'] = 'b3'
    elif change == 'global_css':
        soup.style.string = '.text{font-size:8px}'
    elif change == 'new_local_style':
        style = soup.new_tag('style'); style.string = '#other{opacity:0}'
        soup.find(attrs={MARKER: True}).append(style)
    elif change == 'lost_region':
        del soup.find(attrs={MARKER: True})[MARKER]
    else:
        soup.select_one('main').append(soup.new_tag('div'))
    with pytest.raises(ValueError, match='局部修复'):
        scope.validate([str(soup)])


def test_unmapped_or_brand_only_evidence_does_not_grant_arbitrary_scope():
    assert prepare_local_repair([page()], policy(region_hint='右下方')) is None
    assert prepare_local_repair([page()], policy(element_candidates=['9'])) is None
    mixed = policy(source_candidates=['b1'])
    mixed['current_issue_refs'].append({'text_anchors': ['不在当前图上的文字']})
    assert prepare_local_repair([page()], mixed) is None
    assert prepare_local_repair([page()], {'scope': 'body_layout'}) is None


def test_only_cited_page_is_editable_and_pagination_stays_bound():
    current = [page(), page('page-2')]
    scope = prepare_local_repair(current, policy(source_candidates=['b1']))
    result = [repaired(scope), unpack_resources(scope.documents[1], scope.resources)]
    scope.validate(result)
    assert scope.manifest['pages'][1]['mode'] == 'unchanged'
    with pytest.raises(ValueError, match='非目标'):
        scope.validate([result[0], result[1].replace('30万元', '31万元')])
    with pytest.raises(ValueError, match='分页'):
        scope.validate(result[:1])


def test_title_target_keeps_body_and_brand_unchanged():
    scope = prepare_local_repair([page()], policy(element_candidates=['0']))
    soup = BeautifulSoup(scope.documents[0], 'html.parser')
    soup.select_one('h1')['style'] = 'white-space:nowrap'
    scope.validate([unpack_resources(str(soup), scope.resources)])
    assert '第二段30万元' not in scope.documents[0]


def test_whole_body_candidate_does_not_expand_a_specific_source_target():
    current = page(); current['html'] = current['html'].replace('<main ', '<main data-source-block="all" ')
    scope = prepare_local_repair([current], policy(source_candidates=['all', 'b1']))
    assert len(scope.manifest['pages'][0]['regions']) == 1
    soup = BeautifulSoup(scope.documents[0], 'html.parser')
    assert not soup.select_one('main').has_attr(MARKER)
    assert soup.select_one('#arrow').has_attr(MARKER)
    scope.validate([unpack_resources(scope.documents[0], scope.resources)])


def test_nested_grid_does_not_grant_edit_access_to_other_source_cards():
    current = page()
    current['html'] = current['html'].replace('<section id="arrow"', '<div class="grid"><section id="arrow"').replace('</main>', '</div></main>')
    scope = prepare_local_repair([current], policy(source_candidates=['b1']))
    soup = BeautifulSoup(scope.documents[0], 'html.parser')
    assert soup.select_one('#arrow').has_attr(MARKER)
    assert not soup.select_one('.grid').has_attr(MARKER)
    assert '第二段30万元' not in scope.documents[0]
    changed = BeautifulSoup(repaired(scope), 'html.parser')
    changed.select_one('#other p')['style'] = 'font-size:8px;color:white'
    with pytest.raises(ValueError, match='非目标'):
        scope.validate([str(changed)])


@pytest.mark.parametrize('attr', ['data-agenda-item', 'data-metadata'])
def test_all_real_source_locator_types_work_and_stop_at_independent_sibling(attr):
    current = page()
    current['html'] = current['html'].replace('data-source-block="b1"', f'{attr}="b1"')
    current['html'] = current['html'].replace('<section id="arrow"', '<div class="grid"><section id="arrow"').replace('</main>', '</div></main>')
    scope = prepare_local_repair([current], policy(source_candidates=['b1']))
    assert scope is not None
    soup = BeautifulSoup(scope.documents[0], 'html.parser')
    assert soup.select_one('#arrow').has_attr(MARKER)
    assert not soup.select_one('.grid').has_attr(MARKER)
    scope.validate([repaired(scope)])


def test_duplicate_anchor_without_unique_id_declines_but_explicit_source_disambiguates():
    current = page(); current['html'] = current['html'].replace('第二段30万元。', '调性 × 视觉 × 文案')
    report = {}
    assert prepare_local_repair([current], policy(text_anchors=['调性 × 视觉 × 文案']), report=report) is None
    assert not report['enforced'] and '锚点重复' in report['reason']
    scope = prepare_local_repair([current], policy(source_candidates=['b1'], text_anchors=['调性 × 视觉 × 文案']))
    assert len(scope.manifest['pages'][0]['regions']) == 1
    assert 'data-source-block="b2"' not in scope.documents[0]


def test_ambiguous_anchor_across_pages_and_element_ids_declines():
    report = {}
    assert prepare_local_repair([page(), page('page-2')],
        {'scope': 'local', 'current_issue_refs': [{'text_anchors': ['调性 × 视觉 × 文案']}]}, report=report) is None
    assert '多个页面' in report['reason']
    current = page(); current['html'] = current['html'].replace('id="other"', 'id="arrow"')
    assert prepare_local_repair([current], policy(element_candidates=['arrow']), report=report) is None
    assert '显式元素ID' in report['reason']


def chart_page():
    current = page()
    current['html'] = current['html'].replace('</main>', '<section><div data-enterprise-chart="sales"></div></section></main>')
    current['enterprise_charts'] = [{'id': 'sales', 'chart': {'type': 'bar', 'unit': '万元',
        'categories': ['A', 'B'], 'series': [{'name': '销售额', 'values': [30, 40],
        'source_refs': [{'block_id': 'table-1', 'row': 0, 'column': 1}, {'block_id': 'table-1', 'row': 1, 'column': 1}]}]}}]
    return current


@pytest.mark.parametrize('change', ['value', 'type', 'source_ref', 'missing', 'duplicate', 'bool_number'])
def test_non_target_chart_data_is_strictly_immutable(change):
    current = chart_page(); before = deepcopy(current)
    scope = prepare_local_repair([current], policy(source_candidates=['b1']))
    charts = deepcopy(current['enterprise_charts'])
    if change == 'value': charts[0]['chart']['series'][0]['values'][0] = 31
    elif change == 'type': charts[0]['chart']['type'] = 'line'
    elif change == 'source_ref': charts[0]['chart']['series'][0]['source_refs'][0]['column'] = 2
    elif change == 'missing': charts = []
    elif change == 'duplicate': charts.append(deepcopy(charts[0]))
    else: charts[0]['chart']['series'][0]['source_refs'][0]['row'] = False
    with pytest.raises(ValueError, match='图表'):
        scope.validate([repaired(scope)], charts=[charts])
    assert current == before


def test_original_charts_require_metadata_and_survive_local_retry_alias_roundtrip():
    current = chart_page()
    scope = prepare_local_repair([current], policy(source_candidates=['b1']))
    result = repaired(scope)
    with pytest.raises(ValueError, match='须提供'):
        scope.validate([result])
    scope.validate([result], charts=[current['enterprise_charts']])
    scope.validate([unpack_resources(scope.documents[0], scope.resources)], charts=[current['enterprise_charts']])
    assert scope.manifest['pages'][0]['readonly_chart_ids'] == ['sales']
    assert 'data-enterprise-chart="sales"' not in scope.documents[0]


def test_target_chart_may_change_but_other_page_chart_stays_locked():
    current = chart_page(); current['html'] = current['html'].replace('data-enterprise-chart="sales"', 'id="chart-target" data-enterprise-chart="sales"')
    other = chart_page(); other['slide_id'] = 'page-2'
    scope = prepare_local_repair([current, other], policy(element_candidates=['chart-target']))
    charts = [deepcopy(current['enterprise_charts']), deepcopy(other['enterprise_charts'])]
    charts[0][0]['chart']['type'] = 'line'
    documents = [unpack_resources(document, scope.resources) for document in scope.documents]
    scope.validate(documents, charts=charts)
    charts[1][0]['chart']['type'] = 'line'
    with pytest.raises(ValueError, match='非目标图表'):
        scope.validate(documents, charts=charts)


@pytest.mark.parametrize('css', ['.x:has(.active) .y{color:white}', '.active+.other{color:white}', '.active ~ .other{color:white}'])
def test_known_relational_css_dependency_declines_with_explicit_reason(css):
    current = page(); current['html'] = current['html'].replace('.text{font-size:24px}', css)
    report = {}
    assert prepare_local_repair([current], policy(source_candidates=['b1']), report=report) is None
    assert not report['enforced'] and '选择器' in report['reason']


def test_ordinary_css_is_not_claimed_to_prove_appearance_or_geometry():
    current = page(); current['html'] = current['html'].replace('.text{font-size:24px}', '.text:nth-child(2n+1){font-size:24px}')
    scope = prepare_local_repair([current], policy(source_candidates=['b1']))
    assert scope is not None and scope.manifest['appearance_guarantee'] is False
    assert '浏览器和单页Seed' in scope.manifest['limitations']


def test_shared_svg_fragment_declines_and_new_cross_region_reference_is_rejected():
    current = page()
    current['html'] = current['html'].replace('<svg><path d="M 0 0 L 40 10 L 0 20 Z"/>', '<svg><defs><linearGradient id="paint"><stop offset="0"/></linearGradient></defs><path d="M 0 0 L 40 10 L 0 20 Z"/>')
    current['html'] = current['html'].replace('<section id="other">', '<section id="other" style="fill:url(#paint)">')
    report = {}
    assert prepare_local_repair([current], policy(source_candidates=['b1']), report=report) is None
    assert '片段引用' in report['reason']
    current['html'] = current['html'].replace('style="fill:url(#paint)"', 'style="fill:url(#later)"')
    scope = prepare_local_repair([current], policy(source_candidates=['b1']))
    assert scope is not None
    document = repaired(scope).replace('id="paint"', 'id="later"')
    with pytest.raises(ValueError, match='片段引用'):
        scope.validate([document])
