"""Exercise real version commits with deterministic offline model/renderer tools."""
from collections import Counter
from copy import deepcopy
import base64
import hashlib
import json
import subprocess

import pytest
from PIL import Image

from marketing_agent.enterprise import diagnostics, pipeline
from marketing_agent.enterprise.revisions import RevisionStore, atomic_json, digest


class Jobs:
    def __init__(self, root):
        self.root, self.state, self.events = root, {}, []

    def update(self, job_id, **changes):
        self.state.update(changes)
        self.events.append(deepcopy(changes))


class Agent:
    def __init__(self):
        self.calls, self.finished = [], None

    def call(self, name, fn, *args, **kwargs):
        self.calls.append(name)
        return fn(*args, **kwargs)

    def finish(self, status, **fields):
        self.finished = {'status': status, **fields}


class Harness:
    def __init__(self, tmp_path, monkeypatch, count=1):
        monkeypatch.setenv('MARKETING_VISION_ENABLED', 'true')
        self.folder = tmp_path / 'job'
        self.folder.mkdir()
        self.jobs, self.agent = Jobs(tmp_path), Agent()
        self.groups = [{'group': gi, 'role': 'body', 'title': f'Title {gi}'} for gi in range(count)]
        self.plan = {'title': 'Offline test', 'theme': {}, 'source_blocks': [], 'pages': []}
        self.generated, self.generations, self.diagnoses, self.critics, self.renders = [], [], [], [], []
        self.comparisons, self.compare_hook = [], None
        self.counts = Counter()
        self.visual_hook, self.generate_hook, self.critic_hook, self.browser_hook = None, None, None, None
        self.validation_hook, self.export_hook = None, None
        self.measurement_hook, self.policies = None, {}
        self.corrections = []
        self.asset_manifest = None
        self.source = {}
        monkeypatch.setattr(diagnostics, 'diagnose_page', self.visual)

    def page(self, gi, label, part=1):
        return {**self.groups[gi], 'generation_group': gi, 'html': f'<body>{label} part {part}</body>'}

    def generate(self, proposal, feedback=None, current=None):
        gi = proposal['group']
        self.counts[gi] += 1
        self.generations.append({'group': gi, 'feedback': deepcopy(feedback), 'current': deepcopy(current)})
        if self.generate_hook:
            return self.generate_hook(gi, self.counts[gi], feedback, current)
        return [self.page(gi, f'group {gi} generation {self.counts[gi]}')]

    def renderer(self, folder, probe_only=False):
        plan = json.loads((folder / 'plan.json').read_text())
        self.renders.append({'folder': folder, 'probe_only': probe_only, 'plan': deepcopy(plan)})
        (folder / 'previews').mkdir(exist_ok=True)
        rows = []
        for index, page in enumerate(plan['pages'], 1):
            screenshot = ('offline-screenshot:' + page['html']).encode()
            (folder / 'previews' / f'{index}.png').write_bytes(screenshot)
            issues = self.browser_hook(page, folder, probe_only) if self.browser_hook else []
            rows.append({'page': index, 'slide_id': page['slide_id'], 'issues': issues,
                         'layout_measurements': self.measurement_hook(page) if self.measurement_hook else {},
                         'html_sha256': hashlib.sha256(page['html'].encode()).hexdigest(),
                         'screenshot_sha': hashlib.sha256(screenshot).hexdigest()})
        probe = {'pages': rows}
        if not probe_only and callable(self.export_hook):
            self.export_hook(plan, probe)
        atomic_json(folder / 'enterprise-probe.json', probe)
        if not probe_only:
            (folder / 'presentation.html').write_text('current HTML ' + digest(plan))
            if not plan.get('html_only') and self.export_hook != 'missing-pptx':
                (folder / 'presentation.pptx').write_bytes(('current PPTX ' + digest(plan)).encode())
            atomic_json(folder / 'report.json', {'passed': True, 'checks': {'editable_text': True}})
        return probe

    def issue(self, row, detail='短标签被挤碎'):
        issue_id = 'issue-' + digest({'slide': row['slide_id'], 'version': row['slide_version'], 'phase': row['phase']})[:24]
        return {'issue_id': issue_id, 'issue_key': 'label-' + row['slide_id'], 'origin_issue_id': issue_id,
                'slide_id': row['slide_id'], 'slide_version': row['slide_version'], 'screenshot_sha': row['screenshot_sha'],
                'severity': 'medium', 'type': 'readability', 'detail': detail, 'uncertainty': 0,
                'region_hint': '正文徽章', 'source_candidates': [], 'element_candidates': [],
                'verification_conditions': ['短标签完整显示在一行'], 'prior_issue_id': '', 'unmatched_candidates': {}}

    def visual(self, folder, plan, index, **kwargs):
        page, probe = plan['pages'][index - 1], kwargs['probe']
        event = {'folder': folder, 'page': deepcopy(page), 'index': index, **deepcopy(kwargs)}
        self.diagnoses.append(event)
        row = {'page': index, 'slide_id': page['slide_id'], 'slide_version': page['slide_version'],
               'rubric_version': diagnostics.RUBRIC_VERSION, 'schema_version': diagnostics.SCHEMA_VERSION,
               'html_sha256': probe['html_sha256'], 'screenshot_sha': probe['screenshot_sha'],
               'verdict': 'pass', 'observed': page['html'], 'issues': [], 'aesthetics': {},
               'probe_evidence': diagnostics.browser_evidence(probe, screenshot_sha=probe['screenshot_sha'],
                                                             html_sha256=probe['html_sha256']),
               'phase': kwargs['phase'], 'context_id': kwargs.get('context_id') or kwargs['phase'],
               'request_sha256': digest({'html': page['html'], 'phase': kwargs['phase'], 'context': kwargs.get('context_id')}),
               'rechecks': [{'issue_id': i['issue_id'], 'status': 'resolved', 'evidence': '当前已清晰', 'original_issue': deepcopy(i)}
                            for i in kwargs.get('prior_issues') or []]}
        if self.visual_hook:
            self.visual_hook(row, event)
        return row

    def call(self, name, policy, payload):
        self.policies[name] = policy
        if name == 'enterprise_candidate_compare':
            self.comparisons.append(deepcopy(payload))
            return self.compare_hook(payload, len(self.comparisons)) if self.compare_hook else {'accept': True, 'reason': '原问题解决且无明确退步'}
        assert name == 'enterprise_deck_critic'
        self.critics.append(deepcopy(payload))
        return self.critic_hook(payload, len(self.critics)) if self.critic_hook else {'issues': []}

    def check(self):
        if self.validation_hook:
            self.validation_hook(self.plan)

    def run(self):
        return pipeline.run(self.jobs, 'job', self.agent, self.plan, self.source, self.groups, self.generated,
                            self.generate, self.check, self.call, self.renderer, self.corrections, lambda: None,
                            asset_manifest=self.asset_manifest)

    def report(self):
        return json.loads((self.folder / 'visual-review.json').read_text())


def test_candidate_rechecks_keep_uncertain_original_and_feed_strategy(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if event['phase'] == 'initial':
            row.update(verdict='fix', issues=[h.issue(row)])
        elif event['phase'] == 'candidate' and h.counts[0] == 2:
            assert len(event['prior_issues']) == 1
            row.update(verdict='fix')
            row['rechecks'][0]['status'] = 'uncertain'
    h.visual_hook = visual
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 3
    third = h.generations[2]['feedback']
    assert third['findings'][0]['rechecks'][0]['status'] == 'uncertain'
    assert third['strategy_assessment'][0]['classification'] == 'unresolved_original_issue'
    candidates = [e for e in h.diagnoses if e['phase'] == 'candidate']
    assert candidates[0]['prior_issues'][0]['issue_id'] == candidates[1]['prior_issues'][0]['issue_id']
    assert {e['cache_root'] for e in h.diagnoses} == {tmp_path / 'vision-cache'}
    assert quality['limitations'] == []


def test_seed_and_glm_final_findings_return_to_groups_then_refreeze_with_repagination(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=3)
    def visual(row, event):
        if event['phase'] == 'final' and event['context_id'].endswith(':final-0') and event['page']['group'] == 0:
            row.update(verdict='fix', issues=[h.issue(row)])
    def critic(payload, number):
        return {'issues': [{'slide_id': payload['pages'][1]['slide_id'], 'severity': 'medium',
                            'detail': '第二组与前页叙事重复', 'fix_hint': '调整视觉强调'}]} if number == 1 else {'issues': []}
    def generate(gi, count, feedback, current):
        return [h.page(gi, f'group {gi} generation {count}', part)
                for part in range(1, 3 if gi == 0 and count == 2 else 2)]
    h.visual_hook, h.critic_hook, h.generate_hook = visual, critic, generate
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts == {0: 2, 1: 2, 2: 1}
    assert len(h.plan['pages']) == 4 and len(h.critics) == 2
    fixes = [g for g in h.generations if g['feedback']]
    assert fixes[0]['feedback']['findings'][0]['issues'][0]['detail'] == '短标签被挤碎'
    assert fixes[1]['feedback']['findings'][0]['origin'] == 'deck_critic'
    candidate = next(e for e in h.diagnoses if e['phase'] == 'candidate' and e['page']['group'] == 0)
    assert candidate['prior_issues'][0]['slide_id'] == candidate['page']['slide_id']
    finals = [e for e in h.diagnoses if e['phase'] == 'final']
    assert len({e['context_id'] for e in finals}) == 2
    assert all(e['prior_issues'] is None for e in finals)
    assert h.report()['issues'] == [] and len(h.report()['audits']) == 2
    assert digest(json.loads((h.folder / 'frozen-plan.json').read_text())) == quality['deck_revision']


def test_real_layout_facts_drive_glm_title_repair_and_version_comparison(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=3)
    h.groups[2]['role'] = 'cover'
    def measurements(page):
        small = page['group'] == 1 and 'generation 1' in page['html']
        return {'schema_version': 1, 'titles': [{'text': page['title'],
            'font_size_px': 24 if small else (64 if page['role'] == 'cover' else 44), 'font_weight': '700',
            'text_bounds': {'x': 30, 'y': 20, 'width': 200, 'height': 48}, 'line_count': 1}],
            'body': {'left_gap_px': 40, 'right_gap_px': 40},
            'svg': [{'internal_safe_area': {'status': 'unmeasured'}}]}
    h.measurement_hook = measurements
    def critic(payload, number):
        target = payload['pages'][1]
        font = target['layout_measurements']['titles'][0]['font_size_px']
        return {'issues': [{'slide_id': target['slide_id'], 'severity': 'medium',
            'detail': f'同类页标题44px，本页标题实测{font}px，起点相同但层级偏弱',
            'fix_hint': '检查标题容器宽度并统一同类标题层级'}]} if font == 24 else {'issues': []}
    h.critic_hook = critic
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts == {0: 1, 1: 2, 2: 1}
    feedback = next(g['feedback'] for g in h.generations if g['feedback'])
    assert feedback['findings'][0]['origin'] == 'deck_critic'
    refs = feedback['accepted_typography_references']
    assert {r['role'] for r in refs} == {'body'}
    assert any(r['titles'][0]['font_size_px'] == 44 for r in refs)
    assert 'title_frame' in feedback['repair_goal'] and '对齐锚点' in feedback['repair_goal']
    assert '保留y/height' in feedback['repair_goal'] and '外置关联标签' in feedback['repair_goal']
    compare = h.comparisons[0]
    assert compare['before'][0]['layout_measurements']['titles'][0]['font_size_px'] == 24
    assert compare['after'][0]['layout_measurements']['titles'][0]['font_size_px'] == 44
    assert h.critics[-1]['pages'][2]['layout_measurements']['titles'][0]['font_size_px'] == 64
    assert '不要求封面/章节/正文强行一致' in h.policies['enterprise_deck_critic']


def test_hard_check_repair_keeps_browser_layout_measurements(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    measured = {'text_blocks': [{'text': 'W1–3', 'line_count': 2, 'authored_break_count': 1,
                                'nowrap_width_px': 65, 'available_width_px': 40}],
                'svg': [{'internal_safe_area': {'status': 'unmeasured'}}]}
    h.measurement_hook = lambda page: deepcopy(measured)
    h.browser_hook = lambda page, folder, probe_only: [{'type': 'text_overflow'}] if 'generation 1' in page['html'] else []
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 2
    findings = h.generations[1]['feedback']['findings'][0]
    assert findings['origin'] == 'browser_probe'
    assert findings['probe_evidence']['layout_measurements'] == measured
    # GLM/Seed accepted the remaining measurements. The host adds no stylistic heuristic.
    assert h.critics[0]['pages'][0]['layout_measurements'] == measured


def test_final_repair_is_bounded_and_failed_drafts_cannot_replace_accepted_reviews(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if event['phase'] in {'candidate', 'final'}:
            issue = h.issue(row)
            if event['prior_issues']:
                issue['prior_issue_id'] = event['prior_issues'][0]['issue_id']
                issue['issue_key'] = event['prior_issues'][0]['issue_key']
            row.update(verdict='fix', issues=[issue])
            for check in row['rechecks']:
                check['status'] = 'persists'
    h.visual_hook = visual
    quality = h.run()
    assert quality['quality_status'] == 'needs_review' and not quality['ready_for_delivery']
    assert h.counts[0] == 5 and len(h.report()['audits']) == 2
    assert 'generation 1' in h.plan['pages'][0]['html']
    assert 'generation 1' in h.report()['committed_reviews']['0'][0]['observed']
    failed = [r for r in h.report()['repairs'] if r['deck_round'] == 1]
    assert len(failed) == 4 and all(not r['committed'] for r in failed)
    assert any(l['type'] == 'visual_issue' for l in quality['limitations'])
    assert all(l['type'] != 'repair_exhausted' for l in quality['limitations'])


def test_bad_group_does_not_stop_others_and_html_only_archives_stale_pptx(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    (h.folder / 'presentation.pptx').write_bytes(b'old-pptx-not-current')
    def generate(gi, count, feedback, current):
        if gi == 0:
            raise ValueError('组0来源缺失')
        return [h.page(gi, 'group 1 good')]
    h.generate_hook = generate
    quality = h.run()
    assert h.counts == {0: 4, 1: 1} and h.jobs.state['stage'] == 'needs_review'
    assert quality['artifact_formats'] == ['html']
    assert not quality['checks']['pptx_exported'] and not (h.folder / 'presentation.pptx').exists()
    assert len(list((h.folder / 'artifact-history').glob('*.pptx'))) == 1
    frozen = json.loads((h.folder / 'frozen-plan.json').read_text())
    assert frozen['html_only'] and digest(frozen) == quality['deck_revision']
    assert json.loads((h.folder / 'report.json').read_text())['checks']['editable_text'] is False


def test_visual_budget_exhaustion_preserves_all_other_groups_as_checked_drafts(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    h.visual_hook = lambda row, event: (_ for _ in ()).throw(ValueError('视觉预算已用完'))
    quality = h.run()
    assert h.counts == {0: 1, 1: 1} and len(h.plan['pages']) == 2
    assert len(h.diagnoses) == 1 and not quality['ready_for_delivery']
    assert quality['checks']['source_complete'] and quality['checks']['browser_passed']
    assert not quality['checks']['visual_complete']


def test_export_screenshot_mismatch_invalidates_acceptance(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.export_hook = lambda plan, probe: probe['pages'][0].update(screenshot_sha='f' * 64)
    quality = h.run()
    assert quality['quality_status'] == 'needs_review' and not quality['checks']['export_matches_review']
    assert any(l['type'] == 'export_version_mismatch' for l in quality['limitations'])
    assert h.agent.finished['quality'] == 'needs_review'


def test_user_feedback_maps_original_page_to_group_and_survives_format_error(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    h.generated = [[h.page(0, 'existing 0')], [h.page(1, 'existing 1')]]
    h.plan['pages'] = [p for group in h.generated for p in group]
    feedback = [{'page': 2, 'issues': [{'severity': 'medium', 'type': 'readability', 'detail': '来源列表的文字太小', 'fix_hint': '重新排版'}]}]
    atomic_json(h.folder / 'reviewer-feedback.json', feedback)
    def generate(gi, count, received, current):
        assert gi == 1
        assert [i['raw_issue'] for i in received['original_findings'][0]['issues']] == feedback[0]['issues']
        if count == 1:
            raise ValueError('首次JSON格式错误')
        return [h.page(gi, 'fixed reviewer issue')]
    h.generate_hook = generate
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts == {1: 2}
    assert h.report()['reviewer_feedback'] == feedback
    assert h.generations[0]['feedback']['findings'][0]['origin'] == 'independent_reviewer'
    candidate = next(e for e in h.diagnoses if e['phase'] == 'candidate')
    issue = candidate['prior_issues'][0]
    assert issue['issue_id'].startswith('reviewer-') and issue['raw_issue'] == feedback[0]['issues'][0]
    assert issue['slide_id'] == 'group-0001-part-001'
    assert quality['reviewer_feedback'][0]['status'] == 'resolved'
    assert all(e['prior_issues'] is None for e in h.diagnoses if e['phase'] == 'final')


def test_partial_final_error_keeps_visual_flags_bound_to_slide_id(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    def visual(row, event):
        if event['phase'] == 'final' and event['page']['group'] == 0:
            raise ValueError('该页视觉服务暂不可用')
    h.visual_hook = visual
    quality = h.run()
    assert [p['visual_verified'] for p in quality['pages']] == [False, True]
    assert quality['quality_status'] == 'needs_review' and not h.critics
    assert len([e for e in h.diagnoses if e['phase'] == 'final']) == 2


def test_resolved_initial_failure_does_not_survive_in_current_quality_limitations(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def generate(gi, count, feedback, current):
        if count == 1:
            raise ValueError('可修复的格式错误')
        return [h.page(gi, 'now valid')]
    h.generate_hook = generate
    quality = h.run()
    assert quality['ready_for_delivery'] and quality['limitations'] == []
    assert h.corrections[0]['findings'][0]['issues'][0]['detail'] == '可修复的格式错误'


def test_reused_html_missing_new_required_asset_must_generate_again(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    path = h.folder / 'enterprise-asset-scene.png'
    Image.new('RGB', (512, 288), '#226644').save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    h.asset_manifest = {'items': [{'id': 'scene', 'status': 'accepted', 'generation_group': 0,
        'file': path.name, 'sha256': sha, 'required': True, 'alias': '__PPT_ASSET_scene__',
        'review': {'verdict': 'pass', 'image_sha256': sha}}], 'quality_issues': []}
    h.generated = [[h.page(0, 'existing HTML without newly required image')]]
    h.plan['pages'] = deepcopy(h.generated[0])
    encoded = base64.b64encode(path.read_bytes()).decode()
    def generate(gi, count, feedback, current):
        assert '必要已验收素材' in feedback['findings'][0]['issues'][0]['detail']
        page = h.page(0, 'new with asset')
        page['html'] = f'<div data-enterprise-body><img src="data:image/png;base64,{encoded}"></div>'
        return [page]
    h.generate_hook = generate
    quality = h.run()
    assert h.counts[0] == 1 and quality['ready_for_delivery'] and quality['checks']['assets_complete']


def test_missing_pptx_or_disabled_vision_never_claims_delivery(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.export_hook = 'missing-pptx'
    quality = h.run()
    assert not quality['ready_for_delivery'] and quality['artifact_formats'] == ['html']
    assert any(l['type'] == 'pptx_missing' for l in quality['limitations'])


def test_disabled_vision_keeps_source_checked_draft_without_model_diagnosis(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'false')
    quality = h.run()
    assert not h.diagnoses and not quality['ready_for_delivery']
    assert quality['checks']['source_complete'] and not quality['checks']['visual_complete']
    assert any(l['type'] == 'vision_disabled' for l in quality['limitations'])


def test_restart_restores_feedback_identity_after_repagination(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    h.generated = [[h.page(0, 'old 0')], [h.page(1, 'old 1')]]
    h.plan['pages'] = [p for group in h.generated for p in group]
    atomic_json(h.folder / 'reviewer-feedback.json', [{'page': 2, 'issues': [
        {'severity': 'medium', 'type': 'readability', 'detail': '原第二组要修复'}]}])
    h.generate_hook = lambda gi, count, feedback, current: [h.page(gi, f'fixed {count}', part) for part in (1, 2)]
    first = h.run()
    assert first['ready_for_delivery']
    assert first['reviewer_feedback'][0]['status'] == 'resolved'
    assert len(first['reviewer_feedback'][0]['page_rechecks']) == 2
    mapping = json.loads((h.folder / 'reviewer-feedback-mapping.json').read_text())
    assert mapping['groups']['1'][0]['slide_id'] == 'group-0001-part-001'
    h.plan['pages'], h.generated = [], []  # Real model_html resume path.
    before = len(h.generations)
    resumed = h.run()
    assert resumed['ready_for_delivery'] and len(h.generations) == before
    assert resumed['reviewer_feedback'][0]['status'] == 'resolved'
    assert resumed['reviewer_feedback'][0]['issue_id'] == first['reviewer_feedback'][0]['issue_id']
    assert all(l['type'] != 'reviewer_feedback_unmapped' for l in resumed['limitations'])


def test_renderer_subprocess_failure_is_local_to_group(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    def browser(page, folder, probe_only):
        if page['group'] == 0:
            raise subprocess.CalledProcessError(1, ['offline-renderer'])
        return []
    h.browser_hook = browser
    quality = h.run()
    assert h.counts == {0: 4, 1: 1}
    assert [page['generation_group'] for page in h.plan['pages']] == [1]
    assert quality['quality_status'] == 'needs_review' and quality['artifact_formats'] == ['html']


def test_glm_comparison_rejects_clear_regression_before_replacing_accepted(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if event['phase'] == 'final' and event['context_id'].endswith(':final-0'):
            row.update(verdict='fix', issues=[h.issue(row)])
    h.visual_hook = visual
    h.compare_hook = lambda payload, count: {'accept': count > 1,
        'reason': '修复标签但标题层次明确退步，请恢复原主次' if count == 1 else '标签已修复且原主次保留'}
    quality = h.run()
    assert quality['ready_for_delivery'] and len(h.comparisons) == 2 and h.counts[0] == 3
    assert h.comparisons[0]['accepted_revision'] == h.comparisons[1]['accepted_revision']
    assert all('generation 1' in p['before'][0]['observed'] for p in h.comparisons)
    assert 'generation 2' in h.comparisons[0]['after'][0]['observed']
    assert '明确退步' in h.generations[2]['feedback']['findings'][1]['issues'][0]['detail']
    rejected = next(r for r in h.report()['repairs'] if r.get('comparison') and not r['comparison']['accept'])
    assert rejected['committed'] is False
    assert 'generation 3' in h.plan['pages'][0]['html'] and quality['limitations'] == []


def test_successful_resume_replaces_stale_final_needs_review_state(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if event['phase'] == 'final':
            raise ValueError('暂不可验证终审')
    h.visual_hook = visual
    assert h.run()['quality_status'] == 'needs_review'
    first = json.loads((h.folder / 'revision-state.json').read_text())['final_quality']
    assert first['status'] == 'needs_review'
    h.visual_hook = None
    h.plan['pages'], h.generated = [], []
    assert h.run()['quality_status'] == 'accepted'
    latest = json.loads((h.folder / 'revision-state.json').read_text())['final_quality']
    assert latest['status'] == 'accepted'


def test_manuscript_budget_text_is_not_a_provider_spending_limit():
    from marketing_agent.enterprise.pipeline import _budget_error
    assert not _budget_error("来源不完整：expected='预算30万元、工具调用上限说明'，actual='预算20万元'")
    assert _budget_error('项目图片与视觉累计预算已用完；保留草稿和待处理项')
    assert _budget_error('工具预算或请求次数已用完')
    assert _budget_error('企业模板模型调用上限已到；保留草稿')


def test_budget_exhaustion_still_allows_glm_to_fix_later_browser_errors(tmp_path,monkeypatch):
    h=Harness(tmp_path,monkeypatch,count=2)
    def visual(row,event):
        raise ValueError('视觉预算不足')
    h.visual_hook=visual
    h.browser_hook=lambda p,*args: [{'type':'out_of_slot'}] if p['generation_group']==1 and 'generation 1' in p['html'] else []
    quality=h.run()
    assert h.counts[1]==2
    assert len(h.plan['pages'])==2
    assert quality['checks']['browser_passed']
    assert quality['quality_status']=='needs_review'


def reviewer_input(h, count=1):
    h.generated = [[h.page(gi, 'original')] for gi in range(len(h.groups))]
    h.plan['pages'] = [p for group in h.generated for p in group]
    feedback = [{'page': 1, 'issues': [{'severity': 'medium', 'type': 'readability',
        'detail': f'人工问题 {i + 1}：短标签需要完整显示', 'fix_hint': '保留完整文字并解决多余换行'} for i in range(count)]}]
    atomic_json(h.folder / 'reviewer-feedback.json', feedback)
    return feedback


def committed_record(h, gi=0):
    state = json.loads((h.folder / 'revision-state.json').read_text())
    entry = state['groups'][str(gi)]
    return entry, json.loads((h.folder / entry['check']).read_text())


def test_reviewer_applied_draft_resumes_seed_without_rewriting_and_resolved_is_reused(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h, count=2)
    def exhausted(row, event):
        raise ValueError('视觉预算不足')
    h.visual_hook = exhausted
    first = h.run()
    assert h.counts[0] == 1 and not first['ready_for_delivery']
    entry, record = committed_record(h)
    notes = json.loads(record['reason'])
    application = notes['reviewer_feedback_application']
    assert application['revision'] == entry['revision'] and len(application['issue_ids']) == 2
    assert application['feedback_sha256'] == digest(json.loads((h.folder / 'reviewer-feedback.json').read_text()))
    h.plan['pages'], h.generated = [], []
    second = h.run()
    assert not second['ready_for_delivery'] and h.counts[0] == 1
    assert json.loads(committed_record(h)[1]['reason'])['reviewer_feedback_application'] == application
    h.visual_hook = None
    third = h.run()
    assert third['ready_for_delivery'] and h.counts[0] == 1
    assert {c['status'] for c in third['reviewer_feedback']} == {'resolved'}
    before = len(h.diagnoses)
    fourth = h.run()
    assert fourth['ready_for_delivery'] and h.counts[0] == 1
    assert all(e['phase'] == 'final' and e['prior_issues'] is None for e in h.diagnoses[before:])


@pytest.mark.parametrize('invalid', ['missing', 'duplicate', 'no-evidence', 'uncertain', 'persists'])
def test_blind_final_pass_cannot_close_missing_or_unresolved_human_rechecks(tmp_path, monkeypatch, invalid):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h, count=2)
    def visual(row, event):
        if event['phase'] != 'candidate':
            return
        if invalid == 'missing':
            row['rechecks'] = []
        elif invalid == 'duplicate':
            row['rechecks'][1] = deepcopy(row['rechecks'][0])
        elif invalid == 'no-evidence':
            row['rechecks'][0]['evidence'] = ''
        else:
            row['rechecks'][0]['status'] = invalid
    h.visual_hook = visual
    quality = h.run()
    assert not quality['ready_for_delivery'] and not quality['checks']['reviewer_feedback_complete']
    assert any(c['status'] != 'resolved' for c in quality['reviewer_feedback'])
    assert any(e['phase'] == 'final' for e in h.diagnoses)
    assert all(e['prior_issues'] is None for e in h.diagnoses if e['phase'] == 'final')


@pytest.mark.parametrize('result', ['uncertain', 'low-and-uncertain', 'persists', 'new-issue'])
def test_resumed_draft_only_rewrites_after_seed_confirms_a_fix(tmp_path, monkeypatch, result):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h)
    h.visual_hook = lambda row, event: (_ for _ in ()).throw(ValueError('视觉预算不足'))
    assert not h.run()['ready_for_delivery']
    def visual(row, event):
        if event['phase'] != 'candidate' or h.counts[0] != 1:
            return
        if result == 'new-issue':
            row.update(verdict='fix', issues=[h.issue(row, '新增明确对比问题')])
        elif result == 'low-and-uncertain':
            row.update(verdict='fix', issues=[{**h.issue(row, '轻微样式建议'), 'severity': 'low'}])
            row['rechecks'][0]['status'] = 'uncertain'
        else:
            row['rechecks'][0]['status'] = result
    h.visual_hook = visual
    quality = h.run()
    pending = result in {'uncertain', 'low-and-uncertain'}
    assert h.counts[0] == (1 if pending else 2)
    assert quality['ready_for_delivery'] == (not pending)


def test_legacy_mapping_and_draft_upgrade_rechecks_existing_version_without_claiming_applied(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    raw = reviewer_input(h)
    original = deepcopy(h.plan)
    atomic_json(h.folder / 'refinement-base-plan.json', original)
    atomic_json(h.folder / 'refinement-input.json', {'source_job_id': 'a' * 32,
        'source_plan_sha256': hashlib.sha256((h.folder / 'refinement-base-plan.json').read_bytes()).hexdigest()})
    h.visual_hook = lambda row, event: (_ for _ in ()).throw(ValueError('视觉预算不足'))
    h.run()
    mapping_path = h.folder / 'reviewer-feedback-mapping.json'
    mapping = json.loads(mapping_path.read_text())
    stable_id = mapping['groups']['0'][0]['issues'][0]['issue_id']
    mapping.pop('schema_version')
    mapping['groups']['0'][0].pop('observed_group_slide_ids')
    mapping['groups']['0'][0]['issues'] = deepcopy(raw[0]['issues'])
    atomic_json(mapping_path, mapping)
    entry, record = committed_record(h)
    store = RevisionStore(h.folder, h.plan)
    store.record(0, entry['revision'], record['probe'], [], 'draft_needs_review', '[{"type":"visual_unverified"}]')
    assert store.commit(0, entry['revision'], 'draft_needs_review')
    h.plan['pages'], h.generated = [], []
    h.visual_hook = None
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 1
    upgraded = json.loads(mapping_path.read_text())
    assert upgraded['schema_version'] == 2
    assert upgraded['groups']['0'][0]['issues'][0]['issue_id'] == stable_id
    assert upgraded['groups']['0'][0]['observed_group_slide_ids'] == ['group-0000-part-001']
    notes = json.loads(committed_record(h)[1]['reason'])
    assert 'reviewer_feedback_application' not in notes
    assert notes['reviewer_feedback_application_status'] == 'legacy_unverified'


def test_legacy_scope_unknown_can_close_with_explicit_current_scope_rechecks(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h)
    h.visual_hook = lambda row, event: (_ for _ in ()).throw(ValueError('视觉预算不足'))
    h.run()
    path = h.folder / 'reviewer-feedback-mapping.json'
    mapping = json.loads(path.read_text())
    mapping['groups']['0'][0].pop('observed_group_slide_ids')
    atomic_json(path, mapping)
    h.plan['pages'], h.generated, h.visual_hook = [], [], None
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 1
    assert quality['reviewer_feedback'][0]['status'] == 'resolved'
    assert quality['reviewer_feedback'][0]['page_rechecks'][0]['status'] == 'resolved'


def test_removed_continuation_cannot_make_its_human_problem_disappear(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.generated = [[h.page(0, 'old', part) for part in (1, 2)]]
    h.plan['pages'] = deepcopy(h.generated[0])
    atomic_json(h.folder / 'reviewer-feedback.json', [{'page': 2, 'issues': [
        {'severity': 'high', 'detail': '原续页文字与箭头重叠'}]}])
    def visual(row, event):
        if event['phase'] == 'candidate':
            row['rechecks'] = []
    h.visual_hook = visual
    quality = h.run()
    check = quality['reviewer_feedback'][0]
    assert not quality['ready_for_delivery'] and check['slide_id'] == 'group-0000-part-002'
    assert check['status'] == 'unverified' and check['slide_version'] is None
    assert check['page_rechecks'][0]['slide_id'] == 'group-0000-part-001'
    assert h.counts[0] == 1


def test_deck_repair_reopens_all_human_issues_for_the_new_version(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h, count=2)
    def critic(payload, number):
        return {'issues': [{'slide_id': payload['pages'][0]['slide_id'], 'severity': 'medium',
            'detail': '跨页标题字号不一致', 'fix_hint': '统一标题层级'}]} if number == 1 else {'issues': []}
    h.critic_hook = critic
    quality = h.run()
    candidates = [e for e in h.diagnoses if e['phase'] == 'candidate']
    assert quality['ready_for_delivery'] and h.counts[0] == 2 and len(candidates) == 2
    assert [i['issue_id'] for i in candidates[0]['prior_issues']] == [i['issue_id'] for i in candidates[1]['prior_issues']]
    assert candidates[0]['page']['slide_version'] != candidates[1]['page']['slide_version']
    assert {c['slide_version'] for c in quality['reviewer_feedback']} == {h.plan['pages'][0]['slide_version']}


def test_all_repairs_fail_preserves_original_as_unverified_draft_with_source_and_browser_checks(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h)
    h.groups[0]['block_ids'] = ['b1']
    h.source = {'blocks': [{'id': 'b1', 'kind': 'paragraph', 'text': '完整原文'}], 'agenda': []}
    h.generated[0][0]['html'] = '<body><p data-source-block="b1">完整原文</p></body>'
    original = deepcopy(h.generated[0])
    h.generate_hook = lambda *args: (_ for _ in ()).throw(ValueError('所有新HTML均不合法'))
    quality = h.run()
    assert h.counts[0] == 4 and len(h.plan['pages']) == 1 and not quality['ready_for_delivery']
    assert h.plan['pages'][0]['html'] == original[0]['html']
    entry, record = committed_record(h)
    assert entry['status'] == 'draft_needs_review' and record['reviews'] == []
    assert json.loads(record['reason'])['original_fallback'] is True
    assert 'reviewer_feedback_application' not in json.loads(record['reason'])
    assert quality['reviewer_feedback'][0]['status'] == 'unverified'
    assert any(i['type'] == 'original_fallback' for i in quality['limitations'])
    assert h.generated[0] == original


@pytest.mark.parametrize('failure', ['browser', 'source'])
def test_invalid_original_cannot_be_committed_as_fallback(tmp_path, monkeypatch, failure):
    h = Harness(tmp_path, monkeypatch, count=2)
    reviewer_input(h)
    h.groups[0]['block_ids'] = ['b1']
    h.source = {'blocks': [{'id': 'b1', 'kind': 'paragraph', 'text': '完整原文'}], 'agenda': []}
    h.generated[0][0]['html'] = '<body><p data-source-block="b1">完整原文</p></body>' if failure == 'browser' else '<body>缺少来源</body>'
    def generate(gi, count, feedback, current):
        if gi == 0:
            raise ValueError('修复候选无效')
        return [h.page(gi, 'valid other group')]
    h.generate_hook = generate
    if failure == 'browser':
        h.browser_hook = lambda p, *args: [{'type': 'text_out_of_frame'}] if p['generation_group'] == 0 else []
    quality = h.run()
    state = json.loads((h.folder / 'revision-state.json').read_text())
    assert '0' not in state['groups'] and '1' in state['groups']
    assert [p['generation_group'] for p in h.plan['pages']] == [1]
    assert not quality['ready_for_delivery']
    assert any(i['type'] == 'original_fallback_failed' for i in h.report()['history'])


@pytest.mark.parametrize(('original_count', 'current_count'), [(1, 3), (3, 1)])
def test_repagination_closes_only_after_all_current_pages_recheck_original_ids(tmp_path, monkeypatch, original_count, current_count):
    h = Harness(tmp_path, monkeypatch)
    h.generated = [[h.page(0, 'original source', part) for part in range(1, original_count + 1)]]
    h.plan['pages'] = deepcopy(h.generated[0])
    raw_issue = {'severity': 'medium', 'detail': '原来源区域文字可读性不足', 'fix_hint': '重排并保持来源完整'}
    atomic_json(h.folder / 'reviewer-feedback.json', [{'page': original_count, 'issues': [raw_issue]}])
    h.generate_hook = lambda *args: [h.page(0, 'reflowed content', part) for part in range(1, current_count + 1)]
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 1
    item = quality['reviewer_feedback'][0]
    assert item['status'] == 'resolved' and len(item['page_rechecks']) == current_count
    mapping = json.loads((h.folder / 'reviewer-feedback-mapping.json').read_text())['groups']['0'][0]
    assert mapping['slide_id'] == f'group-0000-part-{original_count:03d}'
    assert len(mapping['observed_group_slide_ids']) == original_count
    events = [e for e in h.diagnoses if e['phase'] == 'candidate']
    assert len(events) == current_count
    for event in events:
        issue = event['prior_issues'][0]
        assert issue['issue_id'] == mapping['issues'][0]['issue_id'] == item['issue_id']
        assert issue['raw_issue'] == raw_issue and issue['original_slide_id'] == mapping['slide_id']
        assert issue['slide_id'] == issue['review_target']['slide_id'] == event['page']['slide_id']
        assert issue['review_target']['slide_version'] == event['page']['slide_version']
        assert issue['review_target']['html_sha256'] == hashlib.sha256(event['page']['html'].encode()).hexdigest()
        assert issue['review_scope_sha256'] == item['review_scope_sha256']
    before = len(h.diagnoses)
    h.plan['pages'], h.generated = [], []
    assert h.run()['ready_for_delivery'] and h.counts[0] == 1
    assert all(e['phase'] == 'final' for e in h.diagnoses[before:])
    assert json.loads((h.folder / 'reviewer-feedback-mapping.json').read_text())['groups']['0'][0] == mapping


@pytest.mark.parametrize('incomplete', ['missing_issue', 'uncertain', 'budget', 'stale_scope', 'stale_target'])
def test_partial_scope_evidence_resumes_only_missing_cells_without_more_html(tmp_path, monkeypatch, incomplete):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h, count=2)
    h.generate_hook = lambda *args: [h.page(0, 'reflowed content', part) for part in (1, 2)]
    def partial(row, event):
        if event['phase'] != 'candidate' or event['index'] != 2:
            return
        if incomplete == 'missing_issue':
            row['rechecks'].pop()
        elif incomplete == 'uncertain':
            row['rechecks'][0]['status'] = 'uncertain'
        elif incomplete == 'budget':
            raise ValueError('视觉预算不足')
        elif incomplete == 'stale_scope':
            row['rechecks'][0]['original_issue']['review_scope_sha256'] = '0' * 64
        else:
            row['rechecks'][0]['original_issue']['review_target']['html_sha256'] = '0' * 64
    h.visual_hook = partial
    first = h.run()
    assert not first['ready_for_delivery'] and h.counts[0] == 1
    assert not first['checks']['reviewer_feedback_complete']
    assert all(i['page_rechecks'][0]['status'] == 'resolved' for i in first['reviewer_feedback'])
    assert any(i['page_rechecks'][1]['status'] != 'resolved' for i in first['reviewer_feedback'])
    before = len(h.diagnoses)
    h.plan['pages'], h.generated, h.visual_hook = [], [], None
    resumed = h.run()
    assert resumed['ready_for_delivery'] and h.counts[0] == 1
    assert [e['index'] for e in h.diagnoses[before:] if e['phase'] == 'candidate'] == [2]
    assert {i['review_scope_sha256'] for i in first['reviewer_feedback']} == {i['review_scope_sha256'] for i in resumed['reviewer_feedback']}
    assert all(c['status'] == 'resolved' for i in resumed['reviewer_feedback'] for c in i['page_rechecks'])


def test_same_page_count_content_migration_is_reviewed_on_every_page(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h)
    h.generated[0].append(h.page(0, 'original continuation', 2))
    h.plan['pages'] = deepcopy(h.generated[0])
    def generate(gi, count, feedback, current):
        # Page 1 stays identical, while its original target moved into page 2.
        return [h.page(0, 'unchanged layout', 1), h.page(0, 'migrated target' if count == 1 else 'fixed target', 2)]
    def visual(row, event):
        if event['phase'] == 'candidate' and event['index'] == 2 and h.counts[0] == 1:
            row['rechecks'][0].update(status='persists', evidence='原问题随内容移到了本页，仍需修复')
    h.generate_hook, h.visual_hook = generate, visual
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 2
    events = [e for e in h.diagnoses if e['phase'] == 'candidate']
    assert [e['index'] for e in events] == [1, 2, 1, 2]
    assert events[0]['page']['slide_version'] == events[2]['page']['slide_version']
    assert events[0]['prior_issues'][0]['review_scope_sha256'] != events[2]['prior_issues'][0]['review_scope_sha256']
    assert all(e['prior_issues'][0]['original_slide_id'] == 'group-0000-part-001' for e in events)
    assert h.generations[1]['feedback']['findings'][1]['rechecks'][0]['status'] == 'persists'


def test_resume_invalidates_whole_matrix_when_another_page_version_changes(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h)
    h.generate_hook = lambda *args: [h.page(0, 'current', part) for part in (1, 2)]
    def partial(row, event):
        if event['phase'] == 'candidate' and event['index'] == 2:
            raise ValueError('视觉预算不足')
    h.visual_hook = partial
    first = h.run()
    entry, old_record = committed_record(h)
    store = RevisionStore(h.folder, h.plan)
    pages = store.committed_group(0)
    page_one_version = pages[0]['slide_version']
    pages[1]['html'] = '<body>changed continuation content</body>'
    folder, candidate, revision = store.candidate(0, pages)
    probe = h.renderer(folder, True)
    # A bound draft may contain retained observations, which are not evidence
    # for its new group scope even if one individual page is unchanged.
    store.record(0, revision, probe, old_record['reviews'], 'draft_needs_review', old_record['reason'])
    assert store.commit(0, revision, 'draft_needs_review')
    assert candidate['pages'][0]['slide_version'] == page_one_version
    before = len(h.diagnoses)
    h.plan['pages'], h.generated, h.visual_hook = [], [], None
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 1
    assert [e['index'] for e in h.diagnoses[before:] if e['phase'] == 'candidate'] == [1, 2]
    assert quality['reviewer_feedback'][0]['review_scope_sha256'] != first['reviewer_feedback'][0]['review_scope_sha256']


@pytest.mark.parametrize('committed_state', ['accepted', 'partial'])
@pytest.mark.parametrize(('field', 'stale'), [
    ('rubric_version', 'enterprise-visual-rubric-v2'),
    ('schema_version', 'enterprise-diagnostics-obsolete'),
    ('rubric_version', None), ('schema_version', None)])
def test_resume_rechecks_stale_or_missing_diagnostic_protocol(tmp_path, monkeypatch, committed_state, field, stale):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h)
    h.generate_hook = lambda *args: [h.page(0, 'current', part) for part in (1, 2)]
    if committed_state == 'partial':
        def partial(row, event):
            if event['phase'] == 'candidate' and event['index'] == 2:
                raise ValueError('视觉预算不足')
        h.visual_hook = partial
    first = h.run()
    assert first['ready_for_delivery'] == (committed_state == 'accepted')
    entry, record = committed_record(h)
    for row in record['reviews']:
        if stale is None:
            row.pop(field)
        else:
            row[field] = stale
    # Persist a legitimate historical check under the old protocol. Every
    # other scope, HTML, page-version and screenshot binding is unchanged.
    store = RevisionStore(h.folder, h.plan)
    store.record(0, entry['revision'], record['probe'], record['reviews'], entry['status'], record['reason'])
    assert store.commit(0, entry['revision'], entry['status'])
    before = len(h.diagnoses)
    h.plan['pages'], h.generated, h.visual_hook = [], [], None
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 1
    assert [e['index'] for e in h.diagnoses[before:] if e['phase'] == 'candidate'] == [1, 2]
    assert quality['reviewer_feedback'][0]['review_scope_sha256'] == first['reviewer_feedback'][0]['review_scope_sha256']
    assert all(row['rubric_version'] == diagnostics.RUBRIC_VERSION and row['schema_version'] == diagnostics.SCHEMA_VERSION
               for row in committed_record(h)[1]['reviews'])
