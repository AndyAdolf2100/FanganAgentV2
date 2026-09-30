"""Recovery and evidence tests use only temporary files, never model calls."""
from copy import deepcopy
import hashlib
import json

import pytest

from marketing_agent.enterprise import revisions as r


@pytest.fixture
def store(tmp_path):
    folder = tmp_path / 'job'
    folder.mkdir()
    (folder / 'manuscript.md').write_text('原稿预算30万元。')
    r.atomic_json(folder / 'template.json', {'id': 'enterprise', 'pages': [{'brand': 'fixed'}]})
    r.atomic_json(folder / 'source-plan.json', {'planned': [{'role': 'body', 'block_ids': ['b1']}]})
    r.atomic_json(folder / 'workflow-checkpoint.json', {'input_sha256': 'f' * 64, 'model_calls': 2})
    r.atomic_json(folder / 'template-reference' / 'enterprise-probe.json', {'pages': [{'page': 1, 'elements': {}}]})
    base = {'title': '计划', 'pages': [], 'enterprise_workflow_version': 5,
            'source_blocks': [{'id': 'b1', 'text': '原稿预算30万元。'}], 'source_sha256': 'a' * 64,
            'template_id': 'enterprise', 'template_revision': 2,
            'theme': {'accent': '#009900'}, 'canvas': {'width': 1280, 'height': 720}}
    return r.RevisionStore(folder, base)


def candidate(store, group=0, count=1, label='原版'):
    return store.candidate(group, [{'html': f'<html><body>{label}-{i}</body></html>', 'role': 'body'} for i in range(count)])


def evidence(plan, status='accepted'):
    probes, reviews = [], []
    for index, page in enumerate(plan['pages'], 1):
        screenshot = hashlib.sha256(('screenshot:' + page['html']).encode()).hexdigest()
        probes.append({'page': index, 'issues': [], 'screenshot_sha': screenshot})
        reviews.append({'page': index, 'slide_id': page['slide_id'], 'slide_version': page['slide_version'],
            'html_sha256': hashlib.sha256(page['html'].encode()).hexdigest(), 'screenshot_sha': screenshot,
            'verdict': 'pass', 'issues': [], 'rechecks': []})
    return {'pages': probes}, reviews if status == 'accepted' else []


def commit_candidate(store, group=0, count=1, label='原版', status='accepted'):
    folder, plan, revision = candidate(store, group, count, label)
    probe, reviews = evidence(plan, status)
    store.record(group, revision, probe, reviews, status)
    store.commit(group, revision, status)
    return folder, plan, revision


def test_candidate_identity_is_stable_across_quality_and_version_metadata(store):
    folder, plan, revision = candidate(store)
    pages = deepcopy(plan['pages'])
    pages[0].update(quality_state='accepted', slide_version='wrong-old-version')
    same_folder, same_plan, same_revision = store.candidate(0, pages)
    assert (same_folder, same_plan, same_revision) == (folder, plan, revision)
    assert 'quality_state' not in same_plan['pages'][0]
    assert pages[0]['quality_state'] == 'accepted'


def test_candidate_reuse_checks_actual_files_instead_of_directory_existence(store):
    folder, _, _ = candidate(store)
    (folder / 'plan.json').write_text('{}')
    with pytest.raises(ValueError, match='哈希'):
        candidate(store)


def test_template_probe_is_an_independent_snapshot(store):
    folder, _, _ = candidate(store)
    source = store.folder / 'template-reference' / 'enterprise-probe.json'
    snapshot = folder / 'template-reference' / 'enterprise-probe.json'
    before = snapshot.read_bytes()
    source.write_text('{"changed":true}')
    assert snapshot.read_bytes() == before
    with pytest.raises(ValueError, match='探针依赖改变'):
        candidate(store)


def reference_probe():
    return {'schema_version': 1, 'canvas': {'width': 1280, 'height': 720}, 'pages': [
        {'page': 1, 'elements': {'2': {'rect': {'x': 20, 'y': 30, 'width': 100, 'height': 50},
                                     'styles': {'fontSize': '40px', 'fontFamily': 'Template Font'},
                                     'layout_measurements': {'nested_protected_value': 1}}},
         'sources': [], 'issues': [], 'screenshot_sha': 'd' * 64, 'html_sha256': 'e' * 64,
         'layout_measurements': {'schema_version': 1, 'titles': [], 'svg': []}}]}


@pytest.mark.parametrize('description_change', ['add', 'update', 'remove'])
def test_descriptive_reference_upgrade_restores_without_rewriting_history(store, description_change):
    source = store.folder / 'template-reference' / 'enterprise-probe.json'
    original = reference_probe()
    if description_change == 'add':
        original['pages'][0].pop('layout_measurements')
    r.atomic_json(source, original)
    folder, plan, revision = commit_candidate(store)
    manifest = (folder / 'candidate.json').read_bytes()
    snapshot = (folder / 'template-reference' / 'enterprise-probe.json').read_bytes()
    state = store.path.read_bytes()
    upgraded = deepcopy(original)
    if description_change == 'remove':
        upgraded['pages'][0].pop('layout_measurements')
    else:
        upgraded['pages'][0]['layout_measurements'] = {'schema_version': 2, 'css_artwork': [{'kind': 'pseudo'}]}
    r.atomic_json(source, upgraded)

    resumed = r.RevisionStore(store.folder, store.base)
    assert resumed.committed_group(0) == plan['pages']
    assert candidate(resumed)[2] == revision
    assert (folder / 'candidate.json').read_bytes() == manifest
    assert (folder / 'template-reference' / 'enterprise-probe.json').read_bytes() == snapshot
    assert store.path.read_bytes() == state

    # A genuinely new candidate retains the complete upgraded reference hash.
    new_folder, _, _ = candidate(resumed, group=1, label='新版')
    new_manifest = json.loads((new_folder / 'candidate.json').read_text())
    assert new_manifest['reference_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (new_folder / 'template-reference' / 'enterprise-probe.json').read_bytes() == source.read_bytes()


@pytest.mark.parametrize('mutation', ['geometry', 'font', 'screenshot', 'html', 'issues',
                                    'page_order', 'root_field', 'nested_description', 'scalar_type'])
def test_reference_upgrade_never_ignores_template_invariants(store, mutation):
    source = store.folder / 'template-reference' / 'enterprise-probe.json'
    original = reference_probe()
    r.atomic_json(source, original)
    commit_candidate(store)
    before = store.path.read_bytes()
    changed = deepcopy(original)
    changed['pages'][0]['layout_measurements'] = {'css_artwork': []}
    page = changed['pages'][0]
    if mutation == 'geometry':
        page['elements']['2']['rect']['x'] += 1
    elif mutation == 'font':
        page['elements']['2']['styles']['fontFamily'] = 'Other Font'
    elif mutation == 'screenshot':
        page['screenshot_sha'] = 'c' * 64
    elif mutation == 'html':
        page['html_sha256'] = 'c' * 64
    elif mutation == 'issues':
        page['issues'] = [{'type': 'covered_text'}]
    elif mutation == 'page_order':
        changed['pages'].insert(0, {**deepcopy(page), 'page': 2})
    elif mutation == 'root_field':
        changed['layout_measurements'] = {'not_a_page_field': True}
    elif mutation == 'nested_description':
        page['elements']['2']['layout_measurements']['nested_protected_value'] = 2
    else:
        page['page'] = True  # Python value equality alone would overlook this.
    r.atomic_json(source, changed)
    with pytest.raises(ValueError, match='探针依赖改变'):
        r.RevisionStore(store.folder, store.base)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('tampering', ['description', 'whitespace', 'missing'])
def test_original_reference_snapshot_raw_hash_stays_mandatory(store, tampering):
    source = store.folder / 'template-reference' / 'enterprise-probe.json'
    r.atomic_json(source, reference_probe())
    folder, _, _ = commit_candidate(store)
    snapshot = folder / 'template-reference' / 'enterprise-probe.json'
    if tampering == 'missing':
        snapshot.unlink()
    elif tampering == 'whitespace':
        snapshot.write_bytes(snapshot.read_bytes() + b'\n')
    else:
        changed = json.loads(snapshot.read_text())
        changed['pages'][0]['layout_measurements'] = {'schema_version': 999}
        r.atomic_json(snapshot, changed)
    with pytest.raises(ValueError, match='探针依赖改变'):
        r.RevisionStore(store.folder, store.base)


@pytest.mark.parametrize('mutation', ['theme', 'template', 'source', 'base', 'plan', 'options'])
def test_changed_inputs_cannot_restore_old_commits(store, mutation):
    commit_candidate(store)
    old_state = store.path.read_bytes()
    base = deepcopy(store.base)
    if mutation == 'theme':
        base['theme']['accent'] = '#FF9900'
    elif mutation == 'template':
        r.atomic_json(store.folder / 'template.json', {'id': 'enterprise', 'pages': [{'brand': 'other'}]})
    elif mutation == 'source':
        (store.folder / 'manuscript.md').write_text('预算60万元。')
    elif mutation == 'base':
        base['canvas']['width'] = 1920
    elif mutation == 'plan':
        r.atomic_json(store.folder / 'source-plan.json', {'planned': [{'role': 'body', 'block_ids': ['b2']}]})
    else:
        r.atomic_json(store.folder / 'workflow-checkpoint.json', {'input_sha256': 'e' * 64, 'model_calls': 3})
    with pytest.raises(ValueError, match='指纹不符'):
        r.RevisionStore(store.folder, base)
    assert store.path.read_bytes() == old_state


def test_progress_counter_changes_do_not_change_input_identity(store):
    commit_candidate(store)
    r.atomic_json(store.folder / 'workflow-checkpoint.json', {'input_sha256': 'f' * 64, 'model_calls': 19})
    resumed = r.RevisionStore(store.folder, store.base)
    assert resumed.pages()[0]['quality_state'] == 'accepted'


def test_legacy_unfingerprinted_state_fails_closed(store):
    r.atomic_json(store.path, {'schema_version': 1, 'groups': {}, 'issues': []})
    with pytest.raises(ValueError, match='指纹不符'):
        r.RevisionStore(store.folder, store.base)


def test_accepted_commit_requires_complete_matching_evidence(store):
    _, plan, revision = candidate(store)
    with pytest.raises(ValueError, match='尚无相应检查'):
        store.commit(0, revision, 'accepted')
    probe, reviews = evidence(plan)
    reviews[0]['screenshot_sha'] = 'b' * 64
    store.record(0, revision, probe, reviews, 'accepted')
    with pytest.raises(ValueError, match='截图版本不匹配'):
        store.commit(0, revision, 'accepted')
    assert store.pages() == []


@pytest.mark.parametrize('problem', ['missing_probe', 'hard_issue', 'missing_review', 'wrong_page', 'medium', 'unresolved'])
def test_invalid_acceptance_evidence_cannot_be_promoted(store, problem):
    _, plan, revision = candidate(store)
    probe, reviews = evidence(plan)
    if problem == 'missing_probe':
        probe['pages'] = []
    elif problem == 'hard_issue':
        probe['pages'][0]['issues'] = [{'type': 'overflow'}]
    elif problem == 'missing_review':
        reviews = []
    elif problem == 'wrong_page':
        reviews[0]['slide_id'] = 'group-0001-part-001'
    elif problem == 'medium':
        reviews[0]['issues'] = [{'severity': 'medium', 'detail': '拥挤'}]
    else:
        reviews[0]['rechecks'] = [{'status': 'unresolved'}]
    store.record(0, revision, probe, reviews, 'accepted')
    with pytest.raises(ValueError):
        store.commit(0, revision, 'accepted')
    assert store.pages() == []


def test_stale_instance_cannot_downgrade_accepted_or_drop_other_groups(store):
    stale = r.RevisionStore(store.folder, store.base)
    _, original, revision = commit_candidate(store)
    _, draft, draft_revision = candidate(stale, label='差版')
    probe, reviews = evidence(draft, 'draft_needs_review')
    stale.record(0, draft_revision, probe, reviews, 'draft_needs_review')
    assert stale.commit(0, draft_revision, 'draft_needs_review') is False
    assert stale.committed_group(0) == original['pages']
    commit_candidate(stale, group=1, status='draft_needs_review')
    assert len(store.materialize()['pages']) == 2
    assert store.state['groups']['0']['revision'] == revision


def test_crash_after_pointer_commit_recovers_latest_verified_plan(store, monkeypatch):
    _, old, _ = commit_candidate(store)
    _, new, revision = candidate(store, label='修复版')
    probe, reviews = evidence(new)
    store.record(0, revision, probe, reviews, 'accepted')
    original_writer = r.atomic_json

    def interrupted(path, value):
        if path == store.folder / 'plan.json':
            raise OSError('simulated interruption after commit pointer')
        return original_writer(path, value)

    monkeypatch.setattr(r, 'atomic_json', interrupted)
    with pytest.raises(OSError):
        store.commit(0, revision, 'accepted')
    assert json.loads((store.folder / 'plan.json').read_text())['pages'][0]['html'] == old['pages'][0]['html']
    monkeypatch.setattr(r, 'atomic_json', original_writer)
    resumed = r.RevisionStore(store.folder, store.base)
    assert resumed.committed_group(0) == new['pages']
    assert resumed.materialize()['pages'][0]['html'] == new['pages'][0]['html']


def test_uncommitted_candidate_never_becomes_recovered_page(store):
    _, accepted, _ = commit_candidate(store)
    _, pending, revision = candidate(store, label='未检查候选')
    resumed = r.RevisionStore(store.folder, store.base)
    assert resumed.committed_group(0) == accepted['pages']
    assert resumed.committed_group(0) != pending['pages']


def test_check_tampering_and_committed_candidate_damage_block_recovery(store):
    _, _, _ = commit_candidate(store)
    entry = store.state['groups']['0']
    check = store.folder / entry['check']
    record = json.loads(check.read_text())
    record['reviews'][0]['verdict'] = 'fix'
    r.atomic_json(check, record)
    with pytest.raises(ValueError, match='检查记录'):
        r.RevisionStore(store.folder, store.base)


def test_two_hundred_page_limit_is_atomic_across_groups(store):
    commit_candidate(store, count=199, status='draft_needs_review')
    before = store.path.read_bytes()
    _, plan, revision = candidate(store, group=1, count=2)
    probe, reviews = evidence(plan, 'draft_needs_review')
    store.record(1, revision, probe, reviews, 'draft_needs_review')
    with pytest.raises(ValueError, match='超过200页'):
        store.commit(1, revision, 'draft_needs_review')
    assert len(store.pages()) == 199
    assert '1' not in store.state['groups']
    commit_candidate(store, group=1, count=1, status='draft_needs_review')
    assert len(store.pages()) == 200


@pytest.mark.parametrize('group,count', [(0, 0), (0, 201), (-1, 1), (200, 1), (True, 1)])
def test_invalid_page_count_or_group_is_rejected_without_state_mutation(store, group, count):
    with pytest.raises(ValueError):
        candidate(store, group=group, count=count)
    assert store.pages() == []


def test_cross_group_candidate_and_revision_path_cannot_be_reused(store):
    _, plan, revision = candidate(store)
    with pytest.raises(ValueError, match='页面组不符'):
        store.record(1, revision, {}, [], 'failed')
    with pytest.raises(ValueError, match='版本ID无效'):
        store.commit(0, '../plan', 'accepted')
    with pytest.raises(ValueError, match='混入其他页面组'):
        store.candidate(1, plan['pages'])


def test_final_review_invalidation_keeps_accepted_group_pointer(store):
    _, plan, revision = commit_candidate(store)
    store.invalidate_acceptance('终审截图改变')
    assert store.state['final_quality']['status'] == 'needs_review'
    assert store.committed_group(0) == plan['pages']
    assert store.state['groups']['0']['revision'] == revision


def final_report(store, status='accepted'):
    frozen = store.materialize()
    r.atomic_json(store.folder / 'frozen-plan.json', frozen)
    artifacts = {}
    for name in ('presentation.html', 'presentation.pptx'):
        path = store.folder / name
        path.write_bytes(('current artifact ' + name).encode())
        artifacts[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'quality_status': status, 'ready_for_delivery': status == 'accepted',
        'deck_revision': r.digest(frozen), 'artifact_sha256': artifacts,
        'pages': [{'slide_id': p['slide_id'], 'slide_version': p['slide_version'],
                   'screenshot_sha': 'e' * 64, 'visual_verified': status == 'accepted'} for p in frozen['pages']],
        'checks': {'source_complete': True, 'browser_passed': True, 'visual_complete': True,
                   'assets_complete': True, 'export_matches_review': True, 'pptx_exported': True,
                   'deck_review': 'passed'}}
    r.atomic_json(store.folder / 'quality-report.json', report)
    return report


def test_final_report_binding_replaces_stale_needs_review(store):
    commit_candidate(store)
    store.invalidate_acceptance('过去的未完成终审')
    report = final_report(store)
    result = store.record_final_quality()
    assert result['status'] == 'accepted' and result['ready_for_delivery'] is True
    assert result['artifact_binding'] and result['revision_bound']
    assert result['deck_revision'] == report['deck_revision']
    assert result['observed_artifact_sha256'] == report['artifact_sha256']
    assert result['report_sha256'] == hashlib.sha256((store.folder / 'quality-report.json').read_bytes()).hexdigest()
    resumed = r.RevisionStore(store.folder, store.base)
    assert resumed.state['final_quality'] == result


@pytest.mark.parametrize('damage', ['artifact', 'missing_artifact', 'frozen_plan', 'new_commit',
                                  'missing_page', 'unverified_page', 'wrong_slide', 'incomplete_check', 'wrong_status'])
def test_final_acceptance_rejects_report_or_artifact_mismatch(store, damage):
    commit_candidate(store)
    store.invalidate_acceptance('保留先前待审标记')
    report = final_report(store)
    if damage == 'artifact':
        (store.folder / 'presentation.pptx').write_bytes(b'other version')
    elif damage == 'missing_artifact':
        (store.folder / 'presentation.html').unlink()
    elif damage == 'frozen_plan':
        r.atomic_json(store.folder / 'frozen-plan.json', {'pages': []})
    elif damage == 'new_commit':
        commit_candidate(store, label='后续修改')
    elif damage == 'missing_page':
        report['pages'] = []
    elif damage == 'unverified_page':
        report['pages'][0]['visual_verified'] = False
    elif damage == 'wrong_slide':
        report['pages'][0]['slide_id'] = 'group-0001-part-001'
    elif damage == 'incomplete_check':
        report['checks']['visual_complete'] = False
    else:
        report['ready_for_delivery'] = False
    r.atomic_json(store.folder / 'quality-report.json', report)
    with pytest.raises(ValueError):
        store.record_final_quality()
    assert store.state['final_quality']['status'] == 'needs_review'
    assert store.state['final_quality']['reason'] == '保留先前待审标记'


def test_needs_review_records_incomplete_frozen_and_artifact_bindings(store):
    commit_candidate(store)
    final_report(store)
    store.record_final_quality()
    report = final_report(store, 'needs_review')
    (store.folder / 'presentation.pptx').unlink()
    (store.folder / 'frozen-plan.json').write_text('incomplete JSON')
    record = store.record_final_quality()
    assert record['status'] == 'needs_review' and record['ready_for_delivery'] is False
    assert record['artifact_sha256'] == report['artifact_sha256']
    assert record['observed_artifact_sha256']['presentation.pptx'] is None
    assert not record['artifact_binding'] and not record['revision_bound']


def test_final_quality_cannot_read_external_artifact_paths(store):
    commit_candidate(store)
    report = final_report(store)
    report['artifact_sha256']['../external.pptx'] = 'a' * 64
    r.atomic_json(store.folder / 'quality-report.json', report)
    with pytest.raises(ValueError, match='产物路径'):
        store.record_final_quality()
