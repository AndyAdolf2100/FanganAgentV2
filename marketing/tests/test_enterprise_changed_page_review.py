"""Offline acceptance checks for reusing complete observations of unchanged pages."""
from collections import Counter
import hashlib
import json

import pytest

from marketing_agent.enterprise import diagnostics
from marketing_agent.enterprise.revisions import atomic_json
from test_enterprise_pipeline import Harness


class FullPageHarness(Harness):
    def visual(self, folder, plan, index, **kwargs):
        row = super().visual(folder, plan, index, **kwargs)
        row['review_scope'] = 'full_page'
        row['review_evidence_sha256'] = diagnostics.review_evidence_sha256(
            plan['pages'][index - 1], kwargs['probe'])
        row.setdefault('text_visibility', {'status': 'clear', 'evidence': '当前截图全部文字清晰。'})
        return row


def assert_reused_from(row, phase):
    assert row['phase'] == phase
    assert row['reused_visual_review'] is True
    assert row['reused_final_review'] is True
    origin = row['review_reuse']
    assert isinstance(origin, dict) and origin
    encoded = json.dumps(origin)
    assert phase in encoded
    assert row['request_sha256'] in encoded


def test_fifty_clean_pages_need_exactly_fifty_visual_calls(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch, count=50)

    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert len(h.diagnoses) == 50
    assert Counter(event['phase'] for event in h.diagnoses) == {'initial': 50}
    assert 'final_enterprise_visual_review' not in h.agent.calls
    rows = h.report()['pages']
    assert [row['page'] for row in rows] == list(range(1, 51))
    for row in rows:
        assert_reused_from(row, 'initial')


def test_deck_critic_repair_reviews_only_the_changed_version_once(tmp_path, monkeypatch):
    count, repaired_group = 5, 2
    h = FullPageHarness(tmp_path, monkeypatch, count=count)

    def critic(payload, number):
        return {'issues': [{'slide_id': payload['pages'][repaired_group]['slide_id'],
                            'severity': 'medium', 'detail': '本页重点需要更突出',
                            'fix_hint': '调整正文层级'}] if number == 1 else []}

    h.critic_hook = critic
    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert len(h.diagnoses) == count + 1
    assert Counter(event['phase'] for event in h.diagnoses) == {'initial': count, 'candidate': 1}
    candidate = next(event for event in h.diagnoses if event['phase'] == 'candidate')
    assert candidate['page']['generation_group'] == repaired_group
    assert h.counts == Counter({group: 2 if group == repaired_group else 1 for group in range(count)})
    for index, row in enumerate(h.report()['pages']):
        assert_reused_from(row, 'candidate' if index == repaired_group else 'initial')


@pytest.mark.parametrize('changed', ['measurements', 'screenshot'])
def test_frozen_render_rechecks_only_the_page_with_changed_evidence(tmp_path, monkeypatch, changed):
    h = FullPageHarness(tmp_path, monkeypatch, count=3)
    target_id = 'group-0001-part-001'
    render = h.renderer

    def changed_render(folder, probe_only=False):
        probe = render(folder, probe_only)
        if folder == h.folder:
            page = next(page for page in probe['pages'] if page['slide_id'] == target_id)
            if changed == 'measurements':
                page['layout_measurements'] = {'titles': [{'font_size_px': 42}]}
            else:
                screenshot = folder / 'previews' / f"{page['page']}.png"
                screenshot.write_bytes(screenshot.read_bytes() + b'-frozen-render-change')
                page['screenshot_sha'] = hashlib.sha256(screenshot.read_bytes()).hexdigest()
            atomic_json(folder / 'enterprise-probe.json', probe)
        return probe

    h.renderer = changed_render
    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert len(h.diagnoses) == 4
    finals = [event for event in h.diagnoses if event['phase'] == 'final']
    assert [event['page']['slide_id'] for event in finals] == [target_id]
    initial = next(event for event in h.diagnoses
                   if event['phase'] == 'initial' and event['page']['slide_id'] == target_id)
    if changed == 'measurements':
        assert initial['probe']['screenshot_sha'] == finals[0]['probe']['screenshot_sha']
        assert h.critics[-1]['pages'][1]['layout_measurements']['titles'][0]['font_size_px'] == 42
    else:
        assert initial['probe']['screenshot_sha'] != finals[0]['probe']['screenshot_sha']
    for row in h.report()['pages']:
        if row['slide_id'] == target_id:
            assert row['phase'] == 'final' and not row.get('reused_visual_review')
        else:
            assert_reused_from(row, 'initial')


@pytest.mark.parametrize('status', ['persists', 'uncertain'])
def test_unresolved_candidate_rechecks_cannot_be_reused_as_acceptance(tmp_path, monkeypatch, status):
    h = FullPageHarness(tmp_path, monkeypatch)

    def visual(row, event):
        if event['phase'] == 'initial':
            row.update(verdict='fix', issues=[h.issue(row)])
        elif event['phase'] == 'candidate':
            row['verdict'] = 'fix'
            for recheck in row['rechecks']:
                recheck.update(status=status, evidence='当前截图仍有问题或不足以确认原问题已解决。')
        # Even a clear independent observation cannot silently resolve the
        # outstanding issue-by-page rechecks from the candidate workflow.

    h.visual_hook = visual
    quality = h.run()

    assert not quality['ready_for_delivery']
    assert any(item['type'] == 'seed_issues_unverified' for item in quality['limitations'])
    assert any(event['phase'] == 'candidate' for event in h.diagnoses)
    reused = [row for row in h.report()['pages']
              if row.get('reused_visual_review') and row['phase'] == 'candidate']
    assert reused
    by_id = {page['slide_id']: page for page in quality['pages']}
    for row in reused:
        assert_reused_from(row, 'candidate')
        assert row['verdict'] == 'fix'
        assert any(check['status'] == status for check in row['rechecks'])
        assert by_id[row['slide_id']]['visual_verified'] is False


def test_local_page_numbers_changing_to_deck_positions_do_not_trigger_reviews(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch, count=2)
    h.generate_hook = lambda group, count, feedback, current: [
        h.page(group, f'group {group}', part=part)
        for part in ((1, 2) if group == 0 else (1,))]

    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert [event['index'] for event in h.diagnoses] == [1, 2, 1]
    assert [event['phase'] for event in h.diagnoses] == ['initial'] * 3
    assert [row['page'] for row in h.report()['pages']] == [1, 2, 3]
    for row in h.report()['pages']:
        assert_reused_from(row, 'initial')


@pytest.fixture
def review_evidence_input(monkeypatch):
    monkeypatch.setenv('MARKETING_VISION_MODEL', 'fixture-vision-model')
    page = {'slide_id': 'group-0000-part-001', 'slide_version': 'version-1',
            'title': '来源标题', 'html': '<body>来源正文</body>'}
    probe = {'page': 1, 'slide_id': page['slide_id'], 'screenshot_sha': 'original-image',
             'layout_measurements': {'titles': [{'font_size_px': 40}]}, 'issues': []}
    return page, probe


def test_review_evidence_identity_ignores_only_the_probe_page_position(review_evidence_input):
    page, probe = review_evidence_input
    original = diagnostics.review_evidence_sha256(page, probe)
    probe['page'] = 30
    assert diagnostics.review_evidence_sha256(page, probe) == original


@pytest.mark.parametrize('changed', ['content', 'html', 'version', 'screenshot', 'measurements', 'model', 'policy'])
def test_review_evidence_identity_rejects_changed_input(review_evidence_input, monkeypatch, changed):
    page, probe = review_evidence_input
    original = diagnostics.review_evidence_sha256(page, probe)
    if changed == 'content':
        page['title'] = '另一来源标题'
    elif changed == 'html':
        page['html'] = '<body>更新后的正文</body>'
    elif changed == 'version':
        page['slide_version'] = 'version-2'
    elif changed == 'screenshot':
        probe['screenshot_sha'] = 'updated-image'
    elif changed == 'measurements':
        probe['layout_measurements']['titles'][0]['font_size_px'] = 42
    elif changed == 'model':
        monkeypatch.setenv('MARKETING_VISION_MODEL', 'another-vision-model')
    else:
        monkeypatch.setattr(diagnostics, 'POLICY', diagnostics.POLICY + '\n追加视觉检查要求。')
    assert diagnostics.review_evidence_sha256(page, probe) != original


@pytest.mark.parametrize('legacy', ['missing_scope', 'missing_fingerprint', 'region_scope'])
def test_legacy_or_partial_observations_require_a_new_full_page_review(tmp_path, monkeypatch, legacy):
    class LegacyHarness(FullPageHarness):
        def visual(self, folder, plan, index, **kwargs):
            row = super().visual(folder, plan, index, **kwargs)
            if kwargs['phase'] == 'initial':
                if legacy == 'missing_scope':
                    row.pop('review_scope')
                elif legacy == 'missing_fingerprint':
                    row.pop('review_evidence_sha256')
                else:
                    row['review_scope'] = 'region'
            return row

    h = LegacyHarness(tmp_path, monkeypatch)
    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert [event['phase'] for event in h.diagnoses] == ['initial', 'final']
    assert h.diagnoses[0]['probe']['screenshot_sha'] == h.diagnoses[1]['probe']['screenshot_sha']
    report = h.report()
    final = report['pages'][0]
    assert final['phase'] == 'final' and final['review_scope'] == 'full_page'
    assert final['review_evidence_sha256']
    assert not final.get('reused_visual_review')
    original = report['repairs'][0]['findings'][0]
    if legacy == 'missing_scope':
        assert 'review_scope' not in original
    elif legacy == 'missing_fingerprint':
        assert 'review_evidence_sha256' not in original
    else:
        assert original['review_scope'] == 'region'
