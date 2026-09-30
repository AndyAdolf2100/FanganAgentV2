"""Offline evidence lifecycle tests: no provider credentials or paid requests."""
from copy import deepcopy
import hashlib
import json
import urllib.error

import pytest
from PIL import Image

from marketing_agent.enterprise import diagnostics as d


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_VISION_MODEL', 'doubao-seed-2-1-pro-260915')
    folder = tmp_path / 'job'
    (folder / 'previews').mkdir(parents=True)
    Image.new('RGB', (320, 180), 'white').save(folder / 'previews' / '1.png')
    plan = {'pages': [{'slide_id': 'slide-stable-7', 'role': 'body', 'title': '测试', 'html': '<body>W1–3</body>'}]}
    screenshot_sha = hashlib.sha256((folder / 'previews' / '1.png').read_bytes()).hexdigest()
    probe = {'pages': [{'page': 1, 'screenshot_sha': screenshot_sha,
                       'html_sha256': hashlib.sha256(plan['pages'][0]['html'].encode()).hexdigest(),
                       'sources': [{'id': 'b7', 'text': 'W1–3', 'rect': {'x': 10, 'y': 20, 'width': 70, 'height': 30},
                                    'line_count': 2, 'inside_body': True}],
                       'elements': {'4': {'rect': {'x': 10, 'y': 20, 'width': 70, 'height': 30},
                                          'styles': {'backgroundImage': 'data:image/private-never-send'}}}, 'issues': []}]}
    return folder, plan, probe


def answer(issue=True):
    return {'observed': '中央徽章内W1–3分成两行，其余文字清晰。',
            'issues': [{'severity': 'medium', 'type': 'readability', 'detail': 'W1–3短标签断为两行',
                        'region_hint': '正文中部徽章', 'text_anchors': ['W1–3'], 'source_candidates': ['b7'],
                        'element_candidates': ['4'], 'verification_conditions': ['W1–3完整显示在一行且不越徽章边界'],
                        'uncertainty': 0.1, 'prior_issue_id': ''}] if issue else [],
            'aesthetics': {name: {'score': 3, 'evidence': '当前截图文字与背景可区分，主次明确。', 'uncertainty': 0.1}
                           for name in d.DIMENSIONS}, 'rechecks': []}


def diagnose(scene, result=None, **kwargs):
    folder, plan, probe = scene
    return d.diagnose_page(folder, plan, 1, probe=probe, provider=lambda payload: deepcopy(result if result is not None else answer()), **kwargs)


def test_evidence_binds_version_and_uses_only_browser_geometry(scene):
    row = diagnose(scene, version='rev-1')
    issue = row['issues'][0]
    assert row['page'] == 1 and issue['slide_id'] == 'slide-stable-7'
    assert issue['slide_version'] == 'rev-1' and issue['screenshot_sha'] == scene[2]['pages'][0]['screenshot_sha']
    assert issue['source_candidates'][0]['rect'] == scene[2]['pages'][0]['sources'][0]['rect']
    assert issue['element_candidates'][0]['provenance'] == 'browser_probe'
    assert issue['raw_issue'] == answer()['issues'][0]
    assert issue['verification_conditions'] and row['decision_owner'] == 'GLM'
    assert row['verdict'] == 'fix' and len(row['aesthetics']) == 7
    assert 'private-never-send' not in json.dumps(row)


def test_cache_keys_include_exact_image_version_rubric_and_context(scene):
    folder, plan, probe = scene
    calls = []
    def provider(payload):
        calls.append(payload)
        return answer()
    kw = {'probe': probe, 'provider': provider, 'version': 'rev-1'}
    first = d.diagnose_page(folder, plan, 1, **kw)
    assert d.diagnose_page(folder, plan, 1, **kw)['cache_hit']
    assert len(calls) == 1
    changed = d.diagnose_page(folder, plan, 1, **{**kw, 'version': 'rev-2'})
    assert changed['issues'][0]['issue_id'] != first['issues'][0]['issue_id']
    assert changed['issues'][0]['issue_key'] == first['issues'][0]['issue_key']
    d.diagnose_page(folder, plan, 1, **kw, rubric_version='new-rubric')
    d.diagnose_page(folder, plan, 1, **kw, context_id='different-context')
    Image.new('RGB', (320, 180), 'gray').save(folder / 'previews' / '1.png')
    probe['pages'][0]['screenshot_sha'] = hashlib.sha256((folder / 'previews' / '1.png').read_bytes()).hexdigest()
    d.diagnose_page(folder, plan, 1, **kw)
    assert len(calls) == 5
    record = folder / 'enterprise-diagnostics' / f"{first['request_sha256']}.json"
    assert json.loads(record.read_text())['cache_hit'] is False


def test_layout_facts_reach_single_image_review_without_inventing_shape_safe_area(scene):
    folder, plan, probe = scene
    measurements = {'schema_version': 1,
        'titles': [{'text': '标题', 'font_size_px': 44, 'font_weight': '700',
                    'text_bounds': {'x': 10, 'y': 12, 'width': 190, 'height': 45}}],
        'text_blocks': [{'text': 'W1–3', 'white_space': 'normal', 'line_count': 2,
                         'authored_break_count': 0, 'nowrap_width_px': 67, 'available_width_px': 40,
                         'contrast': {'ratio': None, 'basis': 'complex_background'}}],
        'body': {'left_gap_px': 250, 'right_gap_px': 25},
        'svg': [{'view_box': {'width': 600, 'height': 100}, 'internal_safe_area': {'status': 'unmeasured'},
                 'shapes': [{'tag': 'path', 'paint': {'fill': '#0044aa'}}],
                 'related_text': [{'text': '阶段', 'relation': 'overlapping_viewport'}],
                 'path_data': 'private-path', 'src': 'data:image/private-asset'}]}
    probe['pages'][0]['layout_measurements'] = measurements
    captured = []
    def provider(payload):
        captured.append(payload)
        return answer(False)
    row = d.diagnose_page(folder, plan, 1, probe=probe, provider=provider)
    brief = json.loads(captured[0]['messages'][1]['content'][0]['text'])
    facts = brief['browser_evidence']['layout_measurements']
    assert facts['titles'] == measurements['titles'] and facts['body'] == measurements['body']
    assert facts['text_blocks'][0]['contrast']['ratio'] is None
    assert facts['svg'][0]['internal_safe_area'] == {'status': 'unmeasured'}
    assert 'private-' not in json.dumps(brief)
    assert row['probe_evidence']['layout_measurements'] == facts
    assert row['verdict'] == 'pass' and row['issues'] == []  # Measurements alone do not decide defects.
    assert len([p for p in captured[0]['messages'][1]['content'] if p['type'] == 'image_url']) == 1


def test_changed_browser_layout_facts_do_not_reuse_same_screenshot_diagnosis(scene):
    folder, plan, probe = scene
    calls = []
    def provider(payload):
        calls.append(payload)
        return answer(False)
    probe['pages'][0]['layout_measurements'] = {'titles': [{'font_size_px': 44}]}
    first = d.diagnose_page(folder, plan, 1, probe=probe, provider=provider)
    probe['pages'][0]['layout_measurements']['titles'][0]['font_size_px'] = 30
    second = d.diagnose_page(folder, plan, 1, probe=probe, provider=provider)
    assert first['screenshot_sha'] == second['screenshot_sha']
    assert first['request_sha256'] != second['request_sha256'] and len(calls) == 2
    assert first['rubric_version'] == 'enterprise-visual-rubric-v3'


def test_generic_artwork_and_external_label_relations_keep_unknown_geometry(scene):
    folder, plan, probe = scene
    layout = {'schema_version': 1,
        'css_artwork': [{'kind': 'element', 'selector': '#abstract-mark', 'bounds_role': 'foreground',
            'paint': {'background_image_kinds': ['linear_gradient'],
                      'borders': [{'side': 'left', 'width_px': 3, 'style': 'solid', 'color': 'rgb(0, 0, 0)'}]},
            'clip_path': {'kind': 'polygon'}, 'transform': 'matrix(0, 1, -1, 0, 0, 0)',
            'related_text': [{'text': '外置标签', 'relation': 'near', 'relation_is_ownership_proof': False}],
            'internal_safe_area': {'status': 'unmeasured'}},
            {'kind': 'pseudo', 'host_rect': {'x': 12, 'y': 18, 'width': 70, 'height': 35},
             'geometry_bounds': None, 'bounds_status': 'unmeasured_pseudo_geometry'}],
        'svg': [{'groups': [{'selector': '#compound', 'screen_transform': {'a': 0.5, 'b': 0, 'c': 0, 'd': 0.5, 'e': 20, 'f': 5},
                             'clip_path_present': True}],
                 'internal_safe_area': {'status': 'unmeasured'}}],
        'body': {'foreground_bounds': {'x': 20, 'y': 30, 'width': 60, 'height': 80},
                 'background_surface_bounds': {'x': 0, 'y': 0, 'width': 300, 'height': 180},
                 'measurement_completeness': {'css_artwork_truncated': True}}}
    probe['pages'][0]['layout_measurements'] = layout
    captured = []
    def provider(payload):
        captured.append(payload)
        return answer(False)
    row = d.diagnose_page(folder, plan, 1, probe=probe, provider=provider)
    brief = json.loads(captured[0]['messages'][1]['content'][0]['text'])
    assert brief['browser_evidence']['layout_measurements'] == layout
    assert row['probe_evidence']['layout_measurements'] == layout
    # Neither external placement nor unknown interior geometry creates a host issue.
    assert row['verdict'] == 'pass' and row['issues'] == []


def test_final_review_has_fresh_context_and_does_not_receive_prior_findings(scene):
    first = diagnose(scene)
    calls = []
    def provider(payload):
        calls.append(payload)
        return answer(False)
    folder, plan, probe = scene
    final = d.diagnose_page(folder, plan, 1, probe=probe, phase='final',
                            prior_issues=first['issues'], provider=provider)
    brief = json.loads(calls[0]['messages'][1]['content'][0]['text'])
    assert brief['original_issues'] == [] and brief['phase'] == 'final'
    assert final['context_id'] != first['context_id']
    assert final['request_sha256'] != first['request_sha256'] and final['verdict'] == 'pass'
    assert final['model'] == first['model'] == 'doubao-seed-2-1-pro-260915'
    assert len(calls[0]['messages']) == 2
    assert len([x for x in calls[0]['messages'][1]['content'] if x['type'] == 'image_url']) == 1


def test_candidate_requires_every_original_issue_and_keeps_original_evidence(scene):
    first = diagnose(scene, version='rev-1')
    issue = first['issues'][0]
    with pytest.raises(ValueError, match='逐项回应'):
        diagnose(scene, answer(False), version='rev-2', phase='candidate', prior_issues=[issue])
    fixed = answer(False)
    fixed['rechecks'] = [{'issue_id': issue['issue_id'], 'status': 'resolved', 'evidence': '当前W1–3在一行完整显示。'}]
    candidate = diagnose(scene, fixed, version='rev-2', phase='candidate', prior_issues=[issue])
    assert candidate['rechecks'][0]['original_issue'] == issue
    assert candidate['verdict'] == 'pass'
    assert first['issues'][0]['raw_issue']['detail'] == 'W1–3短标签断为两行'
    fixed['rechecks'][0]['status'] = 'uncertain'
    uncertain = diagnose(scene, fixed, version='rev-3', phase='candidate', prior_issues=[issue])
    assert uncertain['verdict'] == 'fix'
    assert d.classify_persistence([first], uncertain)[0]['classification'] == 'unresolved_original_issue'


def test_persistent_issue_links_across_versions_without_erasing_original(scene):
    first = diagnose(scene, version='rev-1')
    original = first['issues'][0]
    followup = answer()
    followup['issues'][0]['prior_issue_id'] = original['issue_id']
    followup['issues'][0]['detail'] = '短时间标签仍然分行，问题依旧可见'
    followup['rechecks'] = [{'issue_id': original['issue_id'], 'status': 'persists', 'evidence': 'W1与3仍在上下两行'}]
    second = diagnose(scene, followup, version='rev-2', phase='candidate', prior_issues=[original])
    assert second['issues'][0]['issue_id'] != original['issue_id']
    assert second['issues'][0]['issue_key'] == original['issue_key']
    assert second['issues'][0]['origin_issue_id'] == original['issue_id']
    classified = d.classify_persistence([first, first], second)
    assert classified[0]['classification'] == 'unchanged_render'
    assert classified[0]['observations'] == 2 and classified[0]['decision_owner'] == 'GLM'
    third = deepcopy(second)
    third['request_sha256'], third['screenshot_sha'] = 'third-request', 'third-image'
    second['screenshot_sha'] = 'second-image'
    classified = d.classify_persistence([first, second], third)
    assert classified[0]['classification'] == 'persistent_across_versions'
    assert classified[0]['observations'] == 3


def test_unknown_locators_remain_uncertain_and_never_get_fabricated_rects(scene):
    raw = answer()
    raw['issues'][0]['source_candidates'] = ['not-in-browser']
    raw['issues'][0]['element_candidates'] = []
    row = diagnose(scene, raw)
    assert row['issues'][0]['source_candidates'] == []
    assert row['issues'][0]['unmatched_candidates']['source_candidates'] == ['not-in-browser']
    assert row['issues'][0]['uncertainty'] >= .75
    assert d.classify_persistence([], row)[0]['classification'] == 'uncertain_localization'


@pytest.mark.parametrize('mutation', [
    lambda a: a['issues'][0].update(rect={'x': 3, 'y': 4}),
    lambda a: a['issues'][0].update(verification_conditions=[]),
    lambda a: a['aesthetics']['readability'].update(score=5),
    lambda a: a['aesthetics']['readability'].update(score=True),
    lambda a: a['issues'][0].update(uncertainty=float('nan')),
    lambda a: a['issues'][0].update(prior_issue_id='unknown'),
])
def test_invalid_evidence_never_becomes_pass_or_enters_cache(scene, mutation):
    raw = answer()
    mutation(raw)
    with pytest.raises(ValueError):
        diagnose(scene, raw)
    cache = scene[0].parent / 'vision-cache' / 'enterprise-v5'
    assert not list(cache.glob('*.json'))


@pytest.mark.parametrize('field', ['screenshot_sha', 'html_sha256'])
def test_stale_browser_probe_is_rejected_before_model_call(scene, field):
    folder, plan, probe = scene
    probe['pages'][0][field] = 'stale'
    with pytest.raises(ValueError, match='版本不一致'):
        d.diagnose_page(folder, plan, 1, probe=probe, provider=lambda p: pytest.fail('must not call provider'))


def test_unbound_browser_geometry_is_explicitly_marked(scene):
    folder, plan, probe = scene
    del probe['pages'][0]['screenshot_sha']
    row = diagnose(scene)
    assert row['probe_evidence']['binding'] == 'unverified'
    assert row['limitations']


def test_model_and_slide_identity_never_silently_fall_back(scene, monkeypatch):
    monkeypatch.delenv('MARKETING_VISION_MODEL')
    with pytest.raises(ValueError, match='不自动选择其他模型'):
        diagnose(scene)
    monkeypatch.setenv('MARKETING_VISION_MODEL', 'doubao-seed-2-1-pro-260915')
    scene[2]['pages'][0]['slide_id'] = 'different-slide'
    with pytest.raises(ValueError, match='slide_id'):
        diagnose(scene)


def test_conflicting_resolution_cannot_close_original_issue(scene):
    first = diagnose(scene)
    issue = first['issues'][0]
    contradictory = answer()
    contradictory['issues'][0]['prior_issue_id'] = issue['issue_id']
    contradictory['rechecks'] = [{'issue_id': issue['issue_id'], 'status': 'resolved', 'evidence': '仍有断行'}]
    with pytest.raises(ValueError, match='同时声明'):
        diagnose(scene, contradictory, phase='candidate', prior_issues=[issue])


def test_candidate_uses_shared_budget_directory_and_never_logs_key(scene, tmp_path, monkeypatch):
    folder, plan, probe = scene
    candidate = folder / 'candidates' / 'rev-3'
    (candidate / 'previews').mkdir(parents=True)
    (candidate / 'previews' / '1.png').write_bytes((folder / 'previews' / '1.png').read_bytes())
    budget = tmp_path / 'shared' / 'vision-cache'
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'true')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '5')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS', '1')
    monkeypatch.setenv('MARKETING_IMAGE_BASE_URL', 'https://ark.invalid/api/v3')
    monkeypatch.setenv('MARKETING_IMAGE_API_KEY', 'offline-secret-must-never-be-persisted')
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer())}}],
                               'usage': {'total_tokens': 125}}).encode()
    def request(req, timeout):
        calls.append(req)
        return Response()
    monkeypatch.setattr(d.urllib.request, 'urlopen', request)
    result = d.diagnose_page(candidate, plan, 1, probe=probe, cache_root=budget)
    cached = d.diagnose_page(candidate, plan, 1, probe=probe, cache_root=budget)
    assert cached['cache_hit'] and result['usage']['total_tokens'] == 125 and len(calls) == 1
    assert len(json.loads((budget / 'budget.json').read_text())['reservations']) == 1
    assert not (candidate.parent / 'vision-cache' / 'budget.json').exists()
    with pytest.raises(ValueError, match='请求次数'):
        d.diagnose_page(candidate, plan, 1, probe=probe, version='another-version', cache_root=budget)
    assert len(calls) == 1
    assert all('offline-secret-must-never-be-persisted' not in p.read_text() for p in tmp_path.rglob('*.json'))
    trace = [json.loads(line) for line in (candidate / 'tool-trace.jsonl').read_text().splitlines()]
    assert trace[0]['usage']['total_tokens'] == 125 and trace[0]['cache_hit'] is False
    assert trace[1]['cache_hit'] is True and 'usage' not in trace[1]


def test_http_errors_do_not_retry_or_expose_provider_error_body(scene, tmp_path, monkeypatch):
    folder, plan, probe = scene
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'true')
    monkeypatch.setenv('MARKETING_IMAGE_BASE_URL', 'https://ark.invalid/api/v3')
    monkeypatch.setenv('MARKETING_IMAGE_API_KEY', 'offline-secret')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '5')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS', '5')
    calls = []
    def fail(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 403, 'sensitive-provider-body', {}, None)
    monkeypatch.setattr(d.urllib.request, 'urlopen', fail)
    with pytest.raises(ValueError, match='HTTP 403，未自动重试') as error:
        d.diagnose_page(folder, plan, 1, probe=probe)
    assert len(calls) == 1 and 'sensitive-provider-body' not in str(error.value)
    ledger = json.loads((folder.parent / 'vision-cache' / 'budget.json').read_text())
    assert len(ledger['reservations']) == 1


def test_truncated_provider_response_is_not_cached(scene):
    folder, plan, probe = scene
    raw = {'choices': [{'finish_reason': 'length', 'message': {'content': json.dumps(answer(False))}}]}
    with pytest.raises(ValueError, match='截断'):
        d.diagnose_page(folder, plan, 1, probe=probe, provider=lambda p: raw)


def test_parallel_single_image_reviews_keep_slide_identity(scene):
    folder, plan, probe = scene
    plan['pages'].append({**plan['pages'][0], 'slide_id': 'slide-stable-8'})
    (folder / 'previews' / '2.png').write_bytes((folder / 'previews' / '1.png').read_bytes())
    probe['pages'].append({**probe['pages'][0], 'page': 2})
    results = list(d.diagnose_pages(folder, plan, [1, 2], workers=2, probe=probe, provider=lambda p: answer(False)))
    rows = [batch[0] for batch in results]
    assert {row['slide_id'] for row in rows} == {'slide-stable-7', 'slide-stable-8'}
    assert {row['page'] for row in rows} == {1, 2}
