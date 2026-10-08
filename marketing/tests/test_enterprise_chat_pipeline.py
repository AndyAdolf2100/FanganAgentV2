"""Offline queue-to-candidate integration; no service, network or paid models."""
from collections import Counter
from copy import deepcopy
import json
import threading

import pytest

from marketing_agent.enterprise import pipeline
from test_enterprise_changed_page_review import FullPageHarness


class Chat:
    def __init__(self, h):
        self.h = h
        self.owner = threading.get_ident()
        self.requests = {}
        self.checkpoints = []
        self.finishes = []
        self.on_checkpoint = None
        self.on_claim = None
        self.on_finish = None
        h.jobs.chat = self
        h.jobs.get = lambda job_id: h.jobs.state

    def enqueue(self, request_id, plan, group=0):
        members = [(i, p) for i, p in enumerate(plan['pages'], 1) if p['generation_group'] == group]
        index, page = members[0]
        issue_id = 'reviewer-chat-' + request_id
        issue = {'issue_id': issue_id, 'issue_key': issue_id, 'origin_issue_id': issue_id,
                 'origin': 'independent_reviewer', 'type': 'user_request', 'severity': 'medium',
                 'detail': '把指定正文标题加粗并加大间距', 'fix_hint': '只修改指定标题',
                 'slide_id': page['slide_id'], 'observed_revision': page['slide_version']}
        row = {'generation_group': group, 'slide_id': page['slide_id'],
               'observed_revision': page['slide_version'], 'original_page': index,
               'observed_group_slide_ids': [p['slide_id'] for _, p in members],
               'verdict': 'fix', 'origin': 'independent_reviewer', 'issues': [issue]}
        self.requests[request_id] = {'id': request_id, 'rows': [row], 'status': 'planned'}

    def checkpoint(self, job_id, plan, source, call):
        assert threading.get_ident() == self.owner
        self.checkpoints.append(deepcopy(plan))
        if self.on_checkpoint:
            self.on_checkpoint(plan)

    def claim_ready(self, job_id, plan):
        assert threading.get_ident() == self.owner
        if self.on_claim:
            self.on_claim(plan)
        result = []
        for request in self.requests.values():
            if request['status'] in {'planned', 'applying'}:
                request['status'] = 'applying'
                result.append(deepcopy(request))
        return result

    def finish_request(self, job_id, request_id, status, text, details=None):
        assert threading.get_ident() == self.owner
        if self.on_finish:
            self.on_finish(request_id, status)
        self.requests[request_id]['status'] = status
        self.finishes.append({'id': request_id, 'status': status, 'details': deepcopy(details)})


def enqueue_once_after_groups(chat, count, request_id='one', group=0):
    def checkpoint(plan):
        if request_id not in chat.requests and len({p['generation_group'] for p in plan['pages']}) == count:
            chat.enqueue(request_id, plan, group)
    chat.on_checkpoint = checkpoint


def test_chat_repairs_target_group_once_and_retains_original_binding(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch, count=3)
    chat = Chat(h)
    enqueue_once_after_groups(chat, 3, group=2)

    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert h.counts == Counter({0: 1, 1: 1, 2: 2})
    assert Counter(e['phase'] for e in h.diagnoses) == {'initial': 3, 'candidate': 1}
    candidate = next(e for e in h.diagnoses if e['phase'] == 'candidate')
    assert candidate['page']['generation_group'] == 2 and candidate['index'] == 1
    prior = candidate['prior_issues'][0]
    assert prior['original_page'] == 3 and prior['original_slide_id'] == 'group-0002-part-001'
    assert prior['issue_id'] == 'reviewer-chat-one'
    saved = json.loads((h.folder / 'chat-feedback-mapping.json').read_text())['requests']['one']
    assert saved['rows'] == chat.requests['one']['rows']
    assert saved['status'] == chat.requests['one']['status'] == 'completed'
    assert chat.finishes[0]['details'][0]['generated_and_committed']
    assert not (h.folder / 'reviewer-feedback.json').exists()
    assert not (h.folder / 'reviewer-feedback-mapping.json').exists()


def test_chat_plans_at_group_boundaries_but_claims_only_after_all_reviews_drain(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch, count=4)
    chat = Chat(h)
    monkeypatch.setenv('MARKETING_VISION_WORKERS', '2')
    barrier = threading.Barrier(2, timeout=10)
    active = set()
    lock = threading.Lock()

    def visual(row, event):
        if event['phase'] != 'initial':
            return
        with lock:
            active.add(event['page']['generation_group'])
        try:
            barrier.wait()
        finally:
            with lock:
                active.remove(event['page']['generation_group'])

    def checkpoint(plan):
        if 'one' not in chat.requests and plan['pages']:
            chat.enqueue('one', plan, plan['pages'][0]['generation_group'])

    def claim(plan):
        with lock:
            assert not active
        assert len({p['generation_group'] for p in plan['pages']}) == 4

    h.visual_hook, chat.on_checkpoint, chat.on_claim = visual, checkpoint, claim
    quality = h.run()

    assert quality['ready_for_delivery']
    assert len(h.diagnoses) == 5
    assert chat.requests['one']['status'] == 'completed'


def test_multiple_requests_for_the_same_group_share_one_repair(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch)
    chat = Chat(h)

    def checkpoint(plan):
        if not chat.requests and plan['pages']:
            chat.enqueue('one', plan)
            chat.enqueue('two', plan)

    chat.on_checkpoint = checkpoint
    quality = h.run()

    assert quality['ready_for_delivery']
    assert h.counts[0] == 2 and len(h.diagnoses) == 2
    candidate = h.diagnoses[-1]
    assert {i['issue_id'] for i in candidate['prior_issues']} == {'reviewer-chat-one', 'reviewer-chat-two'}
    assert [item['status'] for item in chat.finishes] == ['completed', 'completed']


@pytest.mark.parametrize('outcome', ['generation_error', 'uncertain'])
def test_failed_chat_keeps_obligations_without_repeating_it_on_resume(tmp_path, monkeypatch, outcome):
    h = FullPageHarness(tmp_path, monkeypatch, count=2)
    chat = Chat(h)
    enqueue_once_after_groups(chat, 2)

    def generate(group, count, feedback, current):
        if group == 0 and count > 1 and outcome == 'generation_error':
            raise ValueError('模拟本组修改失败')
        return [h.page(group, f'g{group} version {count}')]

    def visual(row, event):
        if event['phase'] == 'candidate' and outcome == 'uncertain':
            row['verdict'] = 'fix'
            for check in row['rechecks']:
                check.update(status='uncertain', evidence='不能确认修改要求已完成。')

    h.generate_hook, h.visual_hook = generate, visual
    quality = h.run()
    before_calls, before_counts = len(h.diagnoses), h.counts.copy()
    assert not quality['ready_for_delivery']
    assert chat.requests['one']['status'] == 'needs_attention'
    assert any(item['type'] == 'reviewer_feedback_unverified' for item in quality['limitations'])

    h.jobs.state['chat_resume'] = True
    resumed = h.run()

    assert not resumed['ready_for_delivery']
    assert h.counts == before_counts and len(h.diagnoses) == before_calls
    assert len(chat.finishes) == 1
    saved = json.loads((h.folder / 'chat-feedback-mapping.json').read_text())['requests']['one']
    assert saved['status'] == 'needs_attention'


def test_explicit_retry_reopens_unverified_chat_edit_in_same_job(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch)
    chat = Chat(h)
    enqueue_once_after_groups(chat, 1)

    def uncertain(row, event):
        if event['phase'] == 'candidate':
            row['verdict'] = 'fix'
            for check in row['rechecks']:
                check.update(status='uncertain', evidence='截图不足以确认改动。')

    h.visual_hook = uncertain
    assert not h.run()['ready_for_delivery']
    assert chat.requests['one']['status'] == 'needs_attention'
    before = h.counts[0]

    chat.requests['one'].update(status='planned', retry_requested=True)
    h.jobs.state['chat_resume'] = True
    h.visual_hook = None
    result = h.run()

    assert result['ready_for_delivery'], result['limitations']
    assert h.counts[0] == before + 1
    assert chat.requests['one']['status'] == 'completed'
    saved = json.loads((h.folder / 'chat-feedback-mapping.json').read_text())['requests']['one']
    assert saved['status'] == 'completed'


@pytest.mark.parametrize('crash_at', ['after_commit', 'acknowledgement'])
def test_chat_resume_reconciles_committed_work_without_regenerating(tmp_path, monkeypatch, crash_at):
    h = FullPageHarness(tmp_path, monkeypatch, count=2)
    chat = Chat(h)
    enqueue_once_after_groups(chat, 2)
    planning = chat.on_checkpoint
    crashed = False

    def checkpoint(plan):
        nonlocal crashed
        planning(plan)
        if crash_at == 'after_commit' and h.counts[0] == 2 and not crashed:
            crashed = True
            raise RuntimeError('模拟提交后进程中断')

    def finish(request_id, status):
        nonlocal crashed
        if crash_at == 'acknowledgement' and not crashed:
            crashed = True
            raise RuntimeError('模拟提交后进程中断')

    chat.on_checkpoint, chat.on_finish = checkpoint, finish
    with pytest.raises(RuntimeError, match='提交后进程中断'):
        h.run()
    before_calls, before_counts = len(h.diagnoses), h.counts.copy()
    assert chat.requests['one']['status'] == 'applying'
    h.jobs.state['chat_resume'] = True

    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert h.counts == before_counts and len(h.diagnoses) == before_calls
    assert chat.requests['one']['status'] == 'completed'


def test_request_arriving_during_final_audit_waits_for_chat_continuation(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch, count=3)
    chat = Chat(h)

    def critic(payload, number):
        if number == 1:
            chat.enqueue('late', h.plan, 1)
        return {'issues': []}

    h.critic_hook = critic
    first = h.run()
    assert first['ready_for_delivery']
    assert chat.requests['late']['status'] == 'planned' and not chat.finishes
    assert len(h.diagnoses) == 3
    h.jobs.state['chat_resume'] = True

    result = h.run()

    assert result['ready_for_delivery'], result['limitations']
    assert h.counts == Counter({0: 1, 1: 2, 2: 1})
    assert len(h.diagnoses) == 4 and chat.requests['late']['status'] == 'completed'


def test_chat_resume_repairs_named_page_before_unrelated_draft_issue(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch, count=3)
    assert h.run()['ready_for_delivery']
    chat = Chat(h)
    chat.enqueue('priority', h.plan, group=2)
    h.jobs.state['chat_resume'] = True
    (h.folder / 'reviewer-feedback.json').write_text(json.dumps([
        {'page': 1, 'issues': [{'severity': 'medium', 'detail': '第一页需要更清晰'}]}
    ], ensure_ascii=False))
    generated = []
    h.generate_hook = lambda group, count, feedback, current: (
        generated.append(group) or [h.page(group, f'g{group} version {count}')])

    result = h.run()

    assert not result['ready_for_delivery']
    assert generated == [2]
    assert chat.requests['priority']['status'] == 'completed'


def test_chat_batch_limit_leaves_new_messages_for_continuation(tmp_path, monkeypatch):
    h = FullPageHarness(tmp_path, monkeypatch)
    chat = Chat(h)
    enqueue_once_after_groups(chat, 1)

    def finish(request_id, status):
        chat.enqueue(f'next-{len(chat.requests)}', h.plan)

    chat.on_finish = finish
    quality = h.run()

    assert quality['ready_for_delivery']
    assert len(chat.finishes) == pipeline.MAX_CHAT_BATCHES
    assert h.counts[0] == pipeline.MAX_CHAT_BATCHES + 1
    assert sum(request['status'] == 'planned' for request in chat.requests.values()) == 1
