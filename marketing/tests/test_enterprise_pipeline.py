"""Exercise real version commits with deterministic offline model/renderer tools."""
from collections import Counter
from copy import deepcopy
import base64
import hashlib
import json
import subprocess
import threading

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
        # Existing assertions assume deterministic event order; parallel review
        # is opt-in per test. Production defaults to concurrent single-image
        # reviews via MARKETING_VISION_WORKERS.
        monkeypatch.setenv('MARKETING_VISION_WORKERS', '1')
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
        self.measurement_hook, self.freeze_measurement_hook, self.policies = None, None, {}
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
            # Opt-in: model a measured difference that first appears in the
            # assembled deck. Ordinary frozen pages retain candidate evidence.
            measurements = self.measurement_hook(page) if self.measurement_hook else {}
            if folder == self.folder and self.freeze_measurement_hook:
                measurements = self.freeze_measurement_hook(page)
            rows.append({'page': index, 'slide_id': page['slide_id'], 'issues': issues,
                         'layout_measurements': measurements,
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
               'review_scope': 'full_page',
               'review_evidence_sha256': diagnostics.review_evidence_sha256(page, probe),
               'rubric_version': diagnostics.RUBRIC_VERSION, 'schema_version': diagnostics.SCHEMA_VERSION,
               'html_sha256': probe['html_sha256'], 'screenshot_sha': probe['screenshot_sha'],
               'verdict': 'pass', 'observed': page['html'], 'issues': [], 'aesthetics': {},
               'probe_evidence': diagnostics.browser_evidence(probe, screenshot_sha=probe['screenshot_sha'],
                                                             html_sha256=probe['html_sha256']),
               'phase': kwargs['phase'],
               'context_id': kwargs.get('context_id') or f'enterprise-{kwargs["phase"]}-{diagnostics.RUBRIC_VERSION}',
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


def test_confirmed_outline_page_count_is_checked_at_delivery(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.jobs.get = lambda job_id: {'outline_approval': {'page_count': 2}}
    quality = h.run()
    assert quality['outline_page_count'] == {'approved': 2, 'actual': 1, 'matches': False,
                                            'directory_continuation_verified': False}
    assert quality['checks']['outline_page_count_matches'] is False
    assert quality['checks']['outline_page_count_valid'] is False
    assert quality['ready_for_delivery'] is False
    assert any(issue['type'] == 'outline_page_count_mismatch' for issue in quality['limitations'])


def test_verified_directory_continuation_can_pass_approved_outline(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=3)
    for group, role in zip(h.groups, ('cover', 'contents', 'ending')):
        group.update(role=role, template_page=group['group'])
    h.jobs.get = lambda job_id: {'outline_approval': {'page_count': 3}}
    h.generate_hook = lambda gi, attempt, feedback, current: [h.page(gi, f'group {gi}', part=part)
        for part in (1, 2) if gi == 1] if gi == 1 else [h.page(gi, f'group {gi}')]
    quality = h.run()
    assert quality['ready_for_delivery'] is True
    assert quality['outline_page_count'] == {'approved': 3, 'actual': 4, 'matches': False,
                                             'directory_continuation_verified': True}
    assert quality['checks']['outline_page_count_valid'] is True
    assert not any(issue['type'] == 'outline_page_count_mismatch' for issue in quality['limitations'])


def test_only_same_template_directory_pages_qualify_as_approved_continuation():
    groups = [{'role': 'cover', 'template_page': 0}, {'role': 'contents', 'template_page': 1},
              {'role': 'ending', 'template_page': 2}]
    pages = [{'generation_group': gi, **group} for gi, group in enumerate(groups)]
    pages.insert(2, {**pages[1]})
    assert pipeline.verified_directory_continuations(3, groups, pages)
    assert not pipeline.verified_directory_continuations(3, groups, [*pages[:2], {**pages[2], 'template_page': 9}, *pages[3:]])
    assert not pipeline.verified_directory_continuations(3, groups, [pages[0], pages[0], *pages[1:]])
    assert not pipeline.verified_directory_continuations(3, groups, [pages[0], pages[1], pages[3], pages[2]])
    assert not pipeline.verified_directory_continuations(3, groups, pages[:-1])


def test_candidate_uncertain_rechecks_keep_html_and_request_fresh_review(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if event['phase'] == 'initial':
            row.update(verdict='fix', issues=[h.issue(row)])
        elif event['phase'] == 'candidate' and len([e for e in h.diagnoses if e['phase']=='candidate']) == 1:
            assert len(event['prior_issues']) == 1
            row.update(verdict='fix')
            row['rechecks'][0]['status'] = 'uncertain'
    h.visual_hook = visual
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 2
    candidates = [e for e in h.diagnoses if e['phase'] == 'candidate']
    assert candidates[0]['page']['html']==candidates[1]['page']['html']
    assert candidates[0]['context_id']!=candidates[1]['context_id']
    assert candidates[1]['context_id'].startswith('enterprise-candidate-review-retry:')
    assert candidates[0]['prior_issues'][0]['issue_id'] == candidates[1]['prior_issues'][0]['issue_id']
    assert {e['cache_root'] for e in h.diagnoses} == {tmp_path / 'vision-cache'}
    assert quality['limitations'] == []


def test_persistent_uncertain_reviews_use_distinct_contexts_within_existing_attempt_limit(tmp_path,monkeypatch):
    h=Harness(tmp_path,monkeypatch)
    def visual(row,event):
        if event['phase']=='initial':row.update(verdict='fix',issues=[h.issue(row)])
        elif event['phase']=='candidate':
            row['verdict']='fix'
            for check in row['rechecks']:check.update(status='uncertain',evidence='当前边缘仍无法确认')
    h.visual_hook=visual
    quality=h.run()
    candidates=[e for e in h.diagnoses if e['phase']=='candidate']
    group_reviews=[e for e in h.diagnoses if e['phase']!='final']
    assert len(group_reviews)==pipeline.MAX_GROUP_ATTEMPTS==4
    assert h.counts[0]==2 and not quality['ready_for_delivery']
    assert len({e['context_id'] for e in candidates})==3
    assert len({e['page']['html'] for e in candidates})==1
    assert all('review-retry' not in e['context_id'] for e in h.diagnoses if e['phase']=='final')


def test_uncertain_recheck_does_not_hide_a_confirmed_current_visual_problem(tmp_path,monkeypatch):
    h=Harness(tmp_path,monkeypatch)
    def visual(row,event):
        if event['phase']=='initial':row.update(verdict='fix',issues=[h.issue(row)])
        elif event['phase']=='candidate' and h.counts[0]==2:
            row.update(verdict='fix',issues=[h.issue(row,'当前标签明确被图形遮挡')])
            for check in row['rechecks']:check.update(status='uncertain',evidence='旧问题尚无法确认')
    h.visual_hook=visual
    quality=h.run()
    assert quality['ready_for_delivery'] and h.counts[0]==3
    assert h.generations[2]['feedback']['repair_policy']['scope']=='local'


def test_seed_protocol_retry_keeps_same_html_and_does_not_regenerate(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if len(h.diagnoses) == 1:
            raise ValueError('Expecting property name enclosed in double quotes')
    h.visual_hook = visual
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 1
    initial, retry = h.diagnoses[:2]
    assert initial['page']['html'] == retry['page']['html']
    assert initial['page']['slide_version'] == retry['page']['slide_version']
    repairs = h.report()['repairs']
    assert repairs[1]['repair_policy']['scope'] == 'review_only'
    assert repairs[1]['generated'] is False


def test_seed_invalid_response_retries_are_bounded_and_remain_unverified(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        raise ValueError('视觉诊断未逐项复验全部原问题')
    h.visual_hook = visual
    quality = h.run()
    group_diagnoses = [e for e in h.diagnoses if e['phase'] != 'final']
    assert len(group_diagnoses) == pipeline.MAX_GROUP_ATTEMPTS == 4
    assert h.counts[0] == 1 and not quality['ready_for_delivery']
    assert len({e['page']['slide_version'] for e in group_diagnoses}) == 1
    assert len([e for e in h.diagnoses if e['phase'] == 'final']) == 1


def test_missing_human_rechecks_retry_same_candidate_and_all_stable_issue_ids(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    reviewer_input(h, count=2)
    def visual(row, event):
        if len(h.diagnoses) == 1:
            row['rechecks'] = []
    h.visual_hook = visual
    quality = h.run()
    first, retry = h.diagnoses[:2]
    assert quality['ready_for_delivery'] and h.counts[0] == 1
    assert first['page']['slide_version'] == retry['page']['slide_version']
    assert first['prior_issues'] == retry['prior_issues'] and len(retry['prior_issues']) == 2
    assert quality['checks']['reviewer_feedback_complete']


def test_current_source_error_cannot_reselect_template_due_to_old_visual_issue(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if event['phase'] == 'initial':
            row.update(verdict='fix', issues=[h.issue(row, '组件空间不足，标签被裁切')])
    def generate(gi, count, feedback, current):
        if count == 2:
            raise ValueError('原文或目录内容不完整：遗漏“留”，原问题为组件空间不足')
        return [h.page(gi, f'candidate {count}')]
    h.visual_hook, h.generate_hook = visual, generate
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 3
    local, source = [g['feedback'] for g in h.generations if g['feedback']]
    assert local['repair_policy']['scope'] == 'local' and local['keep_selected_template']
    assert source['repair_policy']['scope'] == 'source_only' and source['keep_selected_template']
    assert not source['allow_repagination'] and source['required_page_count'] == 1
    assert source['original_findings'][0]['issues'][0]['detail'] == '组件空间不足，标签被裁切'
    assert source['findings'][0]['issues'][0]['detail'].startswith('原文或目录内容不完整')


def test_repeated_capacity_escalates_second_repair_with_all_original_evidence(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        if event['phase'] != 'final' and h.counts[0] < 3:
            row.update(verdict='fix', issues=[h.issue(row, '文本框空间不足，完整标签被裁切')])
            for check in row['rechecks']:
                check.update(status='persists', evidence='当前文本框空间不足，完整标签仍被裁切')
    h.visual_hook = visual
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 3
    first, second = [g['feedback'] for g in h.generations if g['feedback']]
    assert first['repair_policy']['scope'] == 'local' and first['keep_selected_template']
    assert second['repair_policy']['scope'] == 'body_layout'
    assert not second['keep_selected_template'] and second['allow_repagination']
    assert second['repair_policy']['capacity_evidence'] and second['repair_policy']['repeated_issue_refs']
    assert second['original_findings'][0]['issues'][0]['detail'] == '文本框空间不足，完整标签被裁切'


def test_repeated_browser_capacity_reaches_layout_escalation(tmp_path,monkeypatch):
    h=Harness(tmp_path,monkeypatch)
    def browser(page,folder,probe_only):
        if h.counts[0]<3:
            return [{'type':'out_of_slot','source':'b1','element':4,'text':'同一完整标签',
                     'bounds':{'x':90,'y':10,'w':50,'h':20},'frame':{'x':0,'y':0,'w':100,'h':50}}]
        return []
    h.browser_hook=browser
    quality=h.run()
    assert quality['ready_for_delivery'] and h.counts[0]==3
    first,second=[g['feedback']['repair_policy'] for g in h.generations if g['feedback']]
    assert first['scope']=='local'
    assert second['scope']=='body_layout' and second['allow_template_switch']
    assert second['capacity_evidence'][0]['kind']=='measured_overflow'


def test_seed_and_glm_final_findings_return_to_groups_then_refreeze_with_repagination(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=3)
    h.freeze_measurement_hook = lambda page: ({'titles': [{'font_size_px': 24}]}
        if page['group'] == 0 and 'generation 1' in page['html'] else {})
    def visual(row, event):
        if event['phase'] == 'final' and h.counts[0] == 1 and event['page']['group'] == 0:
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
    # Only the genuinely changed frozen measurement needs a new observation.
    # Repaired groups already have candidate reviews; a moved page number alone
    # does not invalidate the untouched group's initial visual evidence.
    assert [e['page']['group'] for e in finals] == [0]
    assert all(e['prior_issues'] is None for e in finals)
    reused = [row for row in h.report()['pages'] if row.get('reused_final_review')]
    assert len(reused) == 4
    assert [row['phase'] for row in reused] == ['candidate', 'candidate', 'candidate', 'initial']
    assert reused[-1]['page'] == 4 and reused[-1]['review_reuse']['page'] == 1
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
    compact = findings['probe_evidence']['layout_measurements']
    assert compact['text_blocks'] == measured['text_blocks']
    assert compact['svg'][0]['internal_safe_area'] == {'status': 'unmeasured'}
    # GLM/Seed accepted the remaining measurements. The host adds no stylistic heuristic.
    assert h.critics[0]['pages'][0]['layout_measurements']['text_blocks'] == measured['text_blocks']


def test_final_repair_is_bounded_and_failed_drafts_cannot_replace_accepted_reviews(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.freeze_measurement_hook = lambda page: {'titles': [{'font_size_px': 24}]}
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
    h.freeze_measurement_hook = lambda page: {'titles': [{'font_size_px': 24}]}
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
    h.freeze_measurement_hook = lambda page: ({'titles': [{'font_size_px': 24}]}
        if 'generation 1' in page['html'] else {})
    def visual(row, event):
        if event['phase'] == 'final' and h.counts[0] == 1:
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
    h.freeze_measurement_hook = lambda page: {'titles': [{'font_size_px': 24}]}
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
    h.freeze_measurement_hook = lambda page: {'titles': [{'font_size_px': 24}]}
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


def large_legacy_evidence():
    rect = {'x': 10, 'y': 20, 'width': 160, 'height': 70}
    return {'binding': 'screenshot_sha_verified',
        'sources': {'source-kept': {'source_id': 'source-kept', 'text': '可定位的完整来源线索', 'rect': rect}},
        'elements': {'element-kept': {'element_id': 'element-kept', 'text': '组件文字', 'rect': rect}}, 'issues': [],
        'layout_measurements': {'titles': [{'text': '标题', 'font_size_px': 44, 'line_count': 1}],
            'svg': [{'element_id': f'shape-{i}', 'viewport': rect, 'geometry_bounds': rect,
                'internal_safe_area': {'status': 'unmeasured'},
                'related_text': [{'text': '相同组件文字及几何测量', 'rect': rect} for _ in range(16)]} for i in range(500)]}}


def test_glm_feedback_compacts_legacy_measurements_without_mutating_source_or_issue_records():
    issue = {'issue_id': 'human-stable', 'slide_id': 'original-slide', 'detail': '完整人工问题不能截断',
             'fix_hint': '完整修复要求', 'source_candidates': [{'source_id': 'source-kept', 'text': '原始文字'}],
             'raw_issue': {'detail': '原始用户描述', 'extra': '需要原样保留的原问题字段'}}
    row = {'phase': 'candidate', 'slide_id': 'current-slide', 'slide_version': 'version-1',
           'observed': '必须保留的实际观察', 'probe_evidence': large_legacy_evidence(),
           'issues': [issue], 'rechecks': [{'issue_id': 'human-stable', 'status': 'uncertain',
               'evidence': '完整复验原文', 'original_issue': deepcopy(issue)}],
           'raw_answer': {'observed': '重复响应', 'copied_measurements': large_legacy_evidence()}}
    html, source, contract = '<html>完整HTML原样保留</html>', '完整来源原文' * 10000, {'body_frame': {'x': 20, 'width': 1100}}
    payload = {'findings': [row], 'original_findings': [row], 'previous_reviews': [row],
        'attempt_history': [{'findings': [row]}], 'strategy_assessment': [{'original_issue': issue}],
        'source_blocks': [{'id': 'source-kept', 'text': source}], 'html': html, 'template_contract': contract}
    before = digest(payload)
    sent = pipeline._model_feedback(payload)
    assert digest(payload) == before and len(json.dumps(row['probe_evidence'], ensure_ascii=False)) > 500000
    for compact in [sent['findings'][0], sent['original_findings'][0], sent['previous_reviews'][0], sent['attempt_history'][0]['findings'][0]]:
        assert len(json.dumps(compact['probe_evidence'], ensure_ascii=False, separators=(',', ':'))) <= 24000
        assert 'raw_answer' not in compact and compact['issues'] == row['issues'] and compact['rechecks'] == row['rechecks']
        assert compact['slide_version'] == row['slide_version'] and compact['observed'] == row['observed']
        assert 'source-kept' in compact['probe_evidence']['sources']
    assert sent['source_blocks'] == payload['source_blocks'] and sent['html'] == html and sent['template_contract'] == contract
    assert sent['strategy_assessment'] == payload['strategy_assessment']


def test_all_glm_paths_compact_large_reviews_while_hashed_history_stays_full(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def visual(row, event):
        row['probe_evidence'] = large_legacy_evidence()
        row['raw_answer'] = {'observed': '重复响应正文', 'measurements': large_legacy_evidence()}
        if event['phase'] == 'initial':
            issue = h.issue(row)
            issue['source_candidates'] = [{'source_id': 'source-kept', 'text': '完整原始来源定位'}]
            issue['raw_issue'] = {'detail': issue['detail'], 'source_candidates': ['source-kept']}
            row.update(verdict='fix', issues=[issue])
    h.visual_hook = visual
    h.critic_hook = lambda payload, count: {'issues': [{'slide_id': payload['pages'][0]['slide_id'],
        'severity': 'medium', 'detail': '需要比较的一处明确主次变化', 'fix_hint': '恢复强调'}]} if count == 1 else {'issues': []}
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 3
    feedback = h.generations[1]['feedback']
    assert len(json.dumps(feedback, ensure_ascii=False)) < 100000
    assert feedback['findings'][0]['issues'][0]['source_candidates'][0]['text'] == '完整原始来源定位'
    assert feedback['findings'][0]['issues'][0]['raw_issue']['source_candidates'] == ['source-kept']
    assert 'raw_answer' not in feedback['findings'][0]
    assert len(json.dumps(feedback['attempt_history'][0]['findings'][0]['probe_evidence'], ensure_ascii=False)) < 26000
    for payload in h.comparisons:
        assert len(json.dumps(payload, ensure_ascii=False)) < 100000
        assert payload['before'][0]['layout_measurements']['titles'][0]['font_size_px'] == 44
        assert payload['repair_targets'][0]['issues'][0]['detail'] == '需要比较的一处明确主次变化'
    assert all(len(json.dumps(payload, ensure_ascii=False)) < 100000 for payload in h.critics)
    state = json.loads((h.folder / 'revision-state.json').read_text())
    for entry in state['history']:
        record = json.loads((h.folder / entry['check']).read_text())
        assert digest(record) == entry['check_sha256']
        assert all('raw_answer' in row and len(json.dumps(row['probe_evidence'], ensure_ascii=False)) > 500000 for row in record['reviews'])


def legacy_seed_pass_after_open_issue(h, edited=False):
    """Reproduce the persisted v3 fix -> v4 blind pass on unchanged HTML."""
    store = RevisionStore(h.folder, h.plan)
    folder, candidate, revision = store.candidate(0, [h.page(0, 'legacy original')])
    probe = h.renderer(folder, True)
    row = h.visual(folder, candidate, 1, probe=probe['pages'][0], phase='initial')
    issue = h.issue(row, '已记录的旧Seed问题必须逐项回应')
    row.update(verdict='fix', issues=[issue], rubric_version='enterprise-visual-rubric-v3')
    store.record(0, revision, probe, [row], 'draft_needs_review')
    assert store.commit(0, revision, 'draft_needs_review')
    if edited:
        folder, candidate, revision = store.candidate(0, [h.page(0, 'user edited content')])
        probe = h.renderer(folder, True)
    passed = h.visual(folder, candidate, 1, probe=probe['pages'][0], phase='initial')
    store.record(0, revision, probe, [passed], 'accepted')
    assert store.commit(0, revision, 'accepted')
    immutable = {entry['check']: (h.folder / entry['check']).read_bytes() for entry in store.state['history']}
    return issue, immutable


@pytest.mark.parametrize('answer', ['missing', 'uncertain'])
def test_legacy_same_revision_plain_pass_cannot_erase_seed_issue_on_resume(tmp_path, monkeypatch, answer):
    h = Harness(tmp_path, monkeypatch)
    issue, immutable = legacy_seed_pass_after_open_issue(h)
    def unresolved(row, event):
        if event['phase'] != 'candidate':
            return
        assert event['prior_issues'][0]['issue_id'] == issue['issue_id']
        if answer == 'missing':
            row['rechecks'] = []
        else:
            row['rechecks'][0]['status'] = 'uncertain'
    h.visual_hook = unresolved
    first = h.run()
    assert not first['ready_for_delivery'] and not first['checks']['seed_issues_complete']
    assert not h.generations  # Missing evidence cannot force an HTML rewrite.
    assert first['seed_issues'][0]['issue_id'] == issue['issue_id']
    assert first['seed_issues'][0]['status'] != 'resolved'
    assert any(i['type'] == 'seed_issues_unverified' for i in first['limitations'])
    # A new explicit answer can reject the old diagnosis as a false positive.
    def resolved(row, event):
        if event['phase'] == 'candidate':
            row['rechecks'][0]['evidence'] = '当前截图文字完整，旧观察不成立，未见该问题'
    h.visual_hook = resolved
    quality = h.run()
    assert quality['ready_for_delivery'] and quality['checks']['seed_issues_complete'] and not h.generations
    assert quality['seed_issues'][0]['status'] == 'resolved'
    assert '旧观察不成立' in quality['seed_issues'][0]['page_rechecks'][0]['evidence']
    assert all((h.folder / path).read_bytes() == raw for path, raw in immutable.items())
    before = len(h.diagnoses)
    assert h.run()['ready_for_delivery']
    assert all(e['phase'] == 'final' for e in h.diagnoses[before:])


def test_legacy_seed_history_does_not_override_a_different_user_edited_version(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    issue, immutable = legacy_seed_pass_after_open_issue(h, edited=True)
    before = len(h.diagnoses)
    quality = h.run()
    assert quality['ready_for_delivery'] and quality['seed_issues'] == [] and not h.generations
    assert 'user edited content' in h.plan['pages'][0]['html']
    assert all(not e.get('prior_issues') for e in h.diagnoses[before:])
    assert all((h.folder / path).read_bytes() == raw for path, raw in immutable.items())


def test_seed_issues_follow_repagination_and_can_close_on_all_current_pages(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    def generate(gi, count, feedback, current):
        return [h.page(gi, f'generation {count}', part) for part in ((1,) if count == 1 else (1, 2))]
    def visual(row, event):
        if event['phase'] == 'initial':
            row.update(verdict='fix', issues=[h.issue(row)])
        elif event['phase'] == 'candidate' and event['index'] == 2 and h.counts[0] == 2:
            row['rechecks'][0].update(status='persists', evidence='原问题已移到此续页，仍存在')
    h.generate_hook, h.visual_hook = generate, visual
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 3
    events = [e for e in h.diagnoses if e['phase'] == 'candidate']
    assert [e['index'] for e in events] == [1, 2, 1, 2]
    ids = {e['prior_issues'][0]['issue_id'] for e in events}
    assert len(ids) == 1 and quality['seed_issues'][0]['issue_id'] in ids
    assert len(quality['seed_issues'][0]['page_rechecks']) == 2
    assert quality['seed_issues'][0]['status'] == 'resolved'
    registry = json.loads(committed_record(h)[1]['reason'])['seed_issue_registry']
    assert registry[0]['issues'][0]['issue_id'] in ids
    assert h.generations[2]['feedback']['findings'][1]['rechecks'][0]['status'] == 'persists'


def test_partial_seed_matrix_resumes_without_losing_registry_or_redoing_html(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.generate_hook = lambda gi, count, *args: [h.page(gi, f'generation {count}', part)
        for part in ((1,) if count == 1 else (1, 2))]
    def visual(row, event):
        if event['phase'] == 'initial':
            row.update(verdict='fix', issues=[h.issue(row)])
        elif event['phase'] == 'candidate' and event['index'] == 2:
            raise ValueError('视觉预算不足')
    h.visual_hook = visual
    first = h.run()
    assert not first['ready_for_delivery'] and h.counts[0] == 2
    assert first['seed_issues'][0]['page_rechecks'][0]['status'] == 'resolved'
    assert first['seed_issues'][0]['page_rechecks'][1]['status'] == 'unverified'
    before = len(h.diagnoses)
    h.plan['pages'], h.generated, h.visual_hook = [], [], None
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 2
    assert [e['index'] for e in h.diagnoses[before:] if e['phase'] == 'candidate'] == [2]
    assert quality['seed_issues'][0]['issue_id'] == first['seed_issues'][0]['issue_id']


def test_failed_replacements_cannot_erase_final_seed_issue_of_retained_version(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.freeze_measurement_hook = lambda page: {'titles': [{'font_size_px': 24}]}
    observed = []
    def visual(row, event):
        if event['phase'] == 'final' and not observed:
            issue = h.issue(row, '当前已提交版的未关闭问题')
            observed.append(issue['issue_id'])
            row.update(verdict='fix', issues=[issue])
    def generate(gi, count, feedback, current):
        if count > 1:
            raise ValueError('修复服务暂不可用，候选未能产生')
        return [h.page(gi, 'retained original')]
    h.visual_hook, h.generate_hook = visual, generate
    first = h.run()
    assert not first['ready_for_delivery'] and h.counts[0] == 5
    assert first['checks']['visual_complete'] and first['checks']['deck_review'] == 'passed'
    assert first['seed_issues'][0]['issue_id'] == observed[0] and first['seed_issues'][0]['status'] == 'unverified'
    assert 'retained original' in h.plan['pages'][0]['html']
    entry, record = committed_record(h)
    assert entry['status'] == 'accepted'  # Failed replacements did not downgrade the protected version.
    assert json.loads(record['reason'])['seed_issue_registry'][0]['issues'][0]['issue_id'] == observed[0]
    before = len(h.diagnoses)
    h.plan['pages'], h.generated = [], []
    quality = h.run()
    assert quality['ready_for_delivery'] and h.counts[0] == 5
    event = next(e for e in h.diagnoses[before:] if e['phase'] == 'candidate')
    assert event['prior_issues'][0]['issue_id'] == observed[0]


def test_parallel_reviews_overlap_and_keep_page_order(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    monkeypatch.setenv('MARKETING_VISION_WORKERS', '5')
    h.generate_hook = lambda gi, count, feedback, current: [h.page(gi, f'part {part}') for part in (1, 2, 3, 4)]
    # Every single-image pass has exactly four pages; the barrier only releases
    # if the window truly runs them concurrently.
    barrier = threading.Barrier(4, timeout=20)
    h.visual_hook = lambda row, event: barrier.wait()
    quality = h.run()
    assert quality['ready_for_delivery'] and quality['checks']['visual_complete'], quality['limitations']
    assert [row['page'] for row in h.report()['pages']] == [1, 2, 3, 4]
    assert {row['slide_id'] for row in h.report()['pages']} == {p['slide_id'] for p in h.plan['pages']}


def test_parallel_reviews_stop_reserving_after_budget_exhaustion(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    monkeypatch.setenv('MARKETING_VISION_WORKERS', '2')
    h.generate_hook = lambda gi, count, feedback, current: [h.page(gi, f'part {part}') for part in (1, 2, 3, 4)]
    h.visual_hook = lambda row, event: (_ for _ in ()).throw(ValueError('视觉预算不足'))
    quality = h.run()
    assert not quality['ready_for_delivery']
    # Only the in-flight window paid a failed reservation; the rest are marked
    # unverified without another model call. A fast failure can stop the window
    # even earlier, so the invariant is "no paid call beyond the window".
    calls = len([e for e in h.diagnoses if e['phase'] == 'initial'])
    assert 1 <= calls <= 2
    entry = next(item for item in h.report()['repairs'] if item['group'] == 0)
    types = [error['type'] for error in entry['errors']]
    assert len(types) == 4
    assert types.count('visual_unverified') == calls
    assert types.count('visual_budget_exhausted') == 4 - calls


def test_deck_repair_round_re_reviews_only_changed_pages(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    def critic(payload, number):
        return {'issues': [{'slide_id': payload['pages'][0]['slide_id'], 'severity': 'medium',
                            'detail': '第一页与第二页强调重复', 'fix_hint': '调整第一页层级'}]} if number == 1 else {'issues': []}
    def generate(gi, count, feedback, current):
        return [h.page(gi, f'g{gi} ' + ('repaired' if count > 1 else 'initial'))]
    h.critic_hook, h.generate_hook = critic, generate
    quality = h.run()
    assert quality['ready_for_delivery'] and quality['limitations'] == []
    assert [(e['phase'], e['page']['slide_id']) for e in h.diagnoses] == [
        ('initial', 'group-0000-part-001'), ('initial', 'group-0001-part-001'),
        ('candidate', 'group-0000-part-001')]
    assert h.counts == {0: 2, 1: 1}
    rows = h.report()['pages']
    assert [row['slide_id'] for row in rows] == ['group-0000-part-001', 'group-0001-part-001']
    assert [row['phase'] for row in rows] == ['candidate', 'initial']
    assert all(row.get('reused_visual_review') for row in rows)
    assert all(row['review_reuse']['source'] == 'committed_candidate' for row in rows)
    assert [row['review_reuse']['phase'] for row in rows] == ['candidate', 'initial']
    assert 'repaired' in rows[0]['observed'] and 'initial' in rows[1]['observed']
    audit0 = json.loads((h.folder / 'final-audit-0.json').read_text())
    assert [row['phase'] for row in audit0['pages']] == ['initial', 'initial']
    assert all(row['review_reuse']['source'] == 'committed_candidate' for row in audit0['pages'])
    assert rows[1]['request_sha256'] == audit0['pages'][1]['request_sha256']
    assert rows[0]['request_sha256'] != audit0['pages'][0]['request_sha256']
    audit1 = json.loads((h.folder / 'final-audit-1.json').read_text())
    assert audit0['reused_visual_reviews'] == audit1['reused_visual_reviews'] == 2


def test_single_page_groups_share_bounded_reviews_across_multiple_windows(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=12)
    monkeypatch.setenv('MARKETING_VISION_WORKERS', '4')
    owner = threading.get_ident()
    barrier = threading.Barrier(4, timeout=10)
    lock = threading.Lock()
    active = peak = 0

    def visual(row, event):
        nonlocal active, peak
        assert threading.get_ident() != owner
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait()
        finally:
            with lock:
                active -= 1

    def generate(gi, count, feedback, current):
        assert threading.get_ident() == owner  # GLM and workflow mutations remain serialized.
        return [h.page(gi, f'group {gi}')]

    original_update = h.jobs.update
    def update(*args, **kwargs):
        assert threading.get_ident() == owner
        original_update(*args, **kwargs)

    h.visual_hook, h.generate_hook = visual, generate
    monkeypatch.setattr(h.jobs, 'update', update)
    quality = h.run()
    assert quality['ready_for_delivery'] and peak == 4
    assert Counter(e['phase'] for e in h.diagnoses) == {'initial': 12}
    assert all(row['phase'] == 'initial' and row.get('reused_visual_review') for row in h.report()['pages'])
    assert json.loads((h.folder / 'final-audit-0.json').read_text())['reused_visual_reviews'] == 12
    assert [p['generation_group'] for p in h.plan['pages']] == list(range(12))
    assert [r['page'] for r in h.report()['pages']] == list(range(1, 13))


def test_mixed_parallel_failures_keep_budget_stop_and_all_pages(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=4)
    monkeypatch.setenv('MARKETING_VISION_WORKERS', '2')
    barrier = threading.Barrier(2, timeout=10)

    def visual(row, event):
        barrier.wait()
        if event['page']['group'] == 1:
            raise ValueError('视觉预算不足')
        raise ValueError('视觉响应JSON无效')

    h.visual_hook = visual
    quality = h.run()
    assert not quality['ready_for_delivery']
    assert len(h.plan['pages']) == 4
    assert len(h.diagnoses) == 2  # No final/repair model requests after the stop.
    errors = h.report()['issues']
    assert len([error for error in errors if error['type'] == 'visual_budget_exhausted']) == 4


def test_final_reuse_requires_current_browser_measurements(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, count=2)
    h.measurement_hook = lambda page: {'titles': [{'font_size_px': 39 + h.counts[0]}]}
    def critic(payload, number):
        return {'issues': [{'slide_id': payload['pages'][0]['slide_id'], 'severity': 'medium',
                            'detail': '修复第一页强调', 'fix_hint': '调整层级'}]} if number == 1 else {'issues': []}
    h.critic_hook = critic
    quality = h.run()
    assert quality['ready_for_delivery']
    assert Counter(e['phase'] for e in h.diagnoses) == {'initial': 2, 'candidate': 1, 'final': 1}
    audit0 = json.loads((h.folder / 'final-audit-0.json').read_text())
    assert all(row['phase'] == 'initial' and row.get('reused_visual_review') for row in audit0['pages'])
    unchanged = [e for e in h.diagnoses if e['phase'] == 'final' and e['page']['group'] == 1]
    assert len(unchanged) == 1
    initial = next(e for e in h.diagnoses if e['phase'] == 'initial' and e['page']['group'] == 1)
    assert initial['probe']['screenshot_sha'] == unchanged[0]['probe']['screenshot_sha']
    assert initial['probe']['layout_measurements'] != unchanged[0]['probe']['layout_measurements']
    assert h.critics[-1]['pages'][1]['layout_measurements']['titles'][0]['font_size_px'] == 41
    assert not h.report()['pages'][1].get('reused_final_review')
