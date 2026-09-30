from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import subprocess
import urllib.request

import pytest

from marketing_agent.enterprise import template_profile as profiles


def template():
    return {'id': 'a' * 64, 'version': 1, 'published': {'revision': 7}, 'width': 1200, 'height': 675,
        'assets': [{'id': 'asset-1', 'data': 'large-image-payload'}],
        'pages': [{'id': 'body-left', 'role': 'body', 'layoutKind': 'table', 'elements': [
            {'id': 'title', 'kind': 'text', 'x': 50, 'y': 20, 'width': 430, 'height': 60,
             'textRole': 'title', 'binding': 'content', 'labelSource': 'manual', 'order': 1,
             'paragraphs': [{'align': 'left', 'runs': [{'text': '样例标题', 'size': 32, 'font': 'Template Font', 'sourceFont': 'Original Font'}]}]},
            {'id': 'body-a', 'kind': 'text', 'x': 300, 'y': 130, 'width': 250, 'height': 200,
             'textRole': 'itemTitle', 'order': 2, 'paragraphs': [{'runs': [{'text': '第一组', 'size': 24, 'font': 'Template Font'}]}]},
            {'id': 'body-b', 'kind': 'text', 'x': 700, 'y': 130, 'width': 280, 'height': 260,
             'textRole': 'itemBody', 'order': 2, 'paragraphs': [{'runs': [{'text': '第一组说明', 'size': 22, 'font': 'Template Font'}]}]},
            {'id': 'logo', 'kind': 'shape', 'x': 1060, 'y': 20, 'width': 100, 'height': 60,
             'artworkTextRole': 'brand', 'labelSource': 'manual', 'fixed': True,
             'vector': {'paths': ['long-SVG-path-data']}, 'renderAsset': 'asset-1'}]},
          {'id': 'excluded', 'role': 'exclude', 'elements': []}]}


def evidence():
    contract = {'0': {'title_element': 0, 'title_frame': {'x': 50, 'y': 20, 'width': 950, 'height': 60},
                     'body_frame': {'x': 60, 'y': 120, 'width': 1080, 'height': 450},
                     'text_frames': [0], 'protected_elements': [3]}}
    probe = {'pages': [{'page': 1, 'screenshot_sha': 'b' * 64, 'layout_measurements': {
        'titles': [{'template_element': '0', 'font_size_px': 32, 'font_family': 'Template Font',
                    'line_count': 1, 'nowrap_width_px': 128, 'available_width_px': 406,
                    'white_space': 'normal', 'rect': {'x': 50, 'y': 20, 'width': 430, 'height': 60}}],
        'svg': [{'shapes': [{'path': 'must-not-enter-profile'}]}]}}]}
    return contract, probe


def test_structure_only_marks_missing_capacity_body_and_visual_facts_unknown(monkeypatch):
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('no model or network calls'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('no new rendering'))
    source = template(); before = deepcopy(source)
    result = profiles.build_layout_profile(source)
    page = result['pages'][0]['layout_profile']
    assert page['body']['status'] == 'unknown' and page['body']['frame'] is None
    assert page['body']['labelled_sample_regions'][0]['frame']['x'] == 300
    assert page['titles'][0]['frame']['width'] == 430
    assert page['titles'][0]['typography']['fonts'] == ['Template Font']
    assert page['titles'][0]['content_capacity']['status'] == 'unknown'
    assert page['titles'][0]['browser_sample']['status'] == 'unknown'
    assert page['adaptation']['table']['status'] == 'declared'
    assert page['adaptation']['chart']['status'] == 'unknown'
    assert page['adaptation']['visual_quality'] == 'unknown'
    assert page['protection']['manual_brand_indexes'] == [3]
    assert page['label_groups'] == [{'order': 2, 'element_indexes': [1, 2], 'omitted_elements': 0}]
    assert source == before
    encoded = json.dumps(result)
    assert 'large-image-payload' not in encoded and 'long-SVG-path-data' not in encoded
    assert result['cache']['status'] == 'disabled'
    assert page['template_sha256'] == profiles.template_fingerprint(source)
    assert [p['template_page'] for p in profiles.profile_catalog(source)] == [0]


def test_bound_contract_and_probe_add_measured_facts_not_title_capacity():
    source = template(); contracts, probe = evidence(); original = deepcopy((source, contracts, probe))
    result = profiles.build_layout_profile(source, contracts=contracts, probe=probe,
        evidence_template_sha256=profiles.template_fingerprint(source))
    p = result['pages'][0]['layout_profile']
    assert p['body']['frame'] == contracts['0']['body_frame']
    assert p['body']['canvas_area_ratio'] == .6
    assert p['titles'][0]['contract_frame']['width'] == 950
    assert p['titles'][0]['browser_sample']['available_width_px'] == 406
    assert p['titles'][0]['browser_sample']['screenshot_sha'] == 'b' * 64
    assert p['titles'][0]['browser_sample']['applies_to'] == 'template_sample_only'
    assert p['titles'][0]['content_capacity']['status'] == 'unknown'
    assert p['evidence_status'] == 'bound_to_template'
    assert (source, contracts, probe) == original


@pytest.mark.parametrize('fingerprint', [None, 'c' * 64])
def test_unbound_or_stale_evidence_is_explicitly_ignored(fingerprint):
    contracts, probe = evidence()
    p = profiles.profile_catalog(template(), contracts=contracts, probe=probe,
        evidence_template_sha256=fingerprint)[0]['layout_profile']
    assert p['evidence_status'] == 'ignored_binding_mismatch'
    assert p['body']['frame'] is None
    assert p['titles'][0]['contract_frame'] is None
    assert p['titles'][0]['browser_sample']['status'] == 'unknown'


@pytest.mark.parametrize('mutate', [
    lambda t: t['published'].update(revision=8),
    lambda t: t['assets'][0].update(data='new-image-payload'),
    lambda t: t['pages'][0]['elements'][0].update(width=600),
    lambda t: t['pages'][0]['elements'][0]['paragraphs'][0]['runs'][0].update(font='Other Font'),
    lambda t: t['pages'][0]['elements'][0].update(labelSource='model'),
    lambda t: t['pages'][0].update(layoutKind='comparison'),
])
def test_snapshot_changes_invalidate_profile_even_if_template_id_unchanged(tmp_path, mutate):
    source = template(); contracts, probe = evidence(); old_sha = profiles.template_fingerprint(source)
    options = dict(cache_root=tmp_path, contracts=contracts, probe=probe, evidence_template_sha256=old_sha)
    first = profiles.build_layout_profile(source, **options)
    assert first['cache']['status'] == 'miss'
    assert profiles.build_layout_profile(source, **options)['cache']['status'] == 'hit'
    mutate(source)
    second = profiles.build_layout_profile(source, **options)
    assert second['cache']['status'] == 'miss' and first['cache']['key'] != second['cache']['key']
    assert second['pages'][0]['layout_profile']['evidence_status'] == 'ignored_binding_mismatch'
    assert len(list(tmp_path.glob('*.json'))) == 2


def test_contract_probe_and_profile_schema_changes_invalidate_cache(tmp_path, monkeypatch):
    source = template(); contracts, probe = evidence()
    options = dict(cache_root=tmp_path, contracts=contracts, probe=probe, evidence_template_sha256=profiles.template_fingerprint(source))
    results = [profiles.build_layout_profile(source, **options)]
    contracts['0']['body_frame']['width'] = 1000
    results.append(profiles.build_layout_profile(source, **options))
    probe['pages'][0]['screenshot_sha'] = 'd' * 64
    results.append(profiles.build_layout_profile(source, **options))
    monkeypatch.setattr(profiles, 'SCHEMA_VERSION', 'fixture-new-schema')
    results.append(profiles.build_layout_profile(source, **options))
    assert len({r['cache']['key'] for r in results}) == 4
    assert all(r['cache']['status'] == 'miss' for r in results)


def test_atomic_shared_cache_rebuilds_corrupt_content(tmp_path):
    source = template()
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: profiles.build_layout_profile(source, cache_root=tmp_path), range(5)))
    assert Counter(r['cache']['status'] for r in results) == {'miss': 1, 'hit': 4}
    key = results[0]['cache']['key']; path = tmp_path / f'{key}.json'
    saved = json.loads(path.read_text()); saved['profile']['pages'][0]['role'] = 'tampered'
    path.write_text(json.dumps(saved))
    repaired = profiles.build_layout_profile(source, cache_root=tmp_path)
    assert repaired['cache']['status'] == 'miss' and repaired['pages'][0]['role'] == 'body'
    assert not list(tmp_path.glob('*.tmp'))


def test_large_decorative_template_stays_small_with_explicit_omissions():
    source = template(); base = source['pages'][0]['elements'][1]
    source['pages'][0]['elements'].extend({**deepcopy(base), 'id': f'item-{i}', 'order': i + 5} for i in range(150))
    p = profiles.profile_catalog(source)[0]['layout_profile']
    assert len(profiles._json(p)) <= profiles.MAX_PAGE_CHARS
    assert p['summary']['truncated']
    assert p['summary']['sample_regions_total'] == 152
    assert p['summary']['sample_regions_sent'] < 152
    assert p['labels']['itemTitle'] == 151
    assert p['titles']  # Summary pressure must not erase the main title first.


def test_missing_font_and_ambiguous_browser_identity_remain_unknown():
    source = template(); source['pages'][0]['elements'][0]['paragraphs'] = []
    contracts, probe = evidence(); probe['pages'].append(deepcopy(probe['pages'][0]))
    p = profiles.profile_catalog(source, contracts=contracts, probe=probe,
        evidence_template_sha256=profiles.template_fingerprint(source))[0]['layout_profile']
    assert p['titles'][0]['typography']['status'] == 'unknown'
    assert p['titles'][0]['browser_sample']['status'] == 'unknown'
