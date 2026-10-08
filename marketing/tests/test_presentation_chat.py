"""Persistent conversation/service/API checks; model calls and workers stay offline."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from marketing_agent.api import create_app
from marketing_agent.enterprise import workflow
from marketing_agent.enterprise.revisions import atomic_json, digest
from marketing_agent.presentation import PresentationJobs
from marketing_agent.runtime import DemoRuntime
from marketing_agent.store import Store


def page(group, part=1, text=None):
    result = {'slide_id': f'group-{group:04d}-part-{part:03d}', 'generation_group': group,
              'title': f'标题 {group}', 'role': 'body', 'block_ids': [f'b{group}'],
              'html': f'<style>.hidden{{color:red}}</style><main>{text or f"原文 {group}/{part}"}</main>'}
    return {**result, 'slide_version': digest(result)}


class Harness:
    def __init__(self, directory, monkeypatch, jobs=None):
        self.jobs = jobs or PresentationJobs(directory, Store(directory / 'chat.sqlite'))
        self.chat = self.jobs.chat
        self.queued = []
        monkeypatch.setattr(self.jobs.pool, 'submit', lambda *args: self.queued.append(args))
        self.run = self.jobs.store.create({'brief': '企业PPT排版测试', 'mode': 'auto', 'runtime': 'demo'})
        self.run_id = self.run['id']
        self.plan = {'title': '企业演示', 'pages': [page(0), page(1)]}
        self.source = {'title': '企业演示', 'planned': [{'generation_group': 0}, {'generation_group': 1}]}
        self.created = 0
        self.job = self.add_job()
        self.job_id = self.job['id']

    def add_job(self, status='running', plan=None, source=None):
        self.created += 1
        job = {'id': uuid4().hex, 'run_id': self.run_id, 'created': self.created,
               'status': status, 'stage': 'page_generating', 'source_sha256': 'source-v1',
               'options': {'template_id': 'template-v1', 'template_revision': 1},
               'enterprise_workflow_version': 5, 'page_count': len((plan or self.plan)['pages'])}
        folder = self.jobs.root / job['id']
        folder.mkdir()
        atomic_json(folder / 'job.json', job)
        atomic_json(folder / 'plan.json', plan or self.plan)
        atomic_json(folder / 'source-plan.json', source or self.source)
        return job

    def submit(self, text='第2页整体居中', key=None, pages=None, job_id=None):
        return self.chat.submit(self.run_id, text, key or uuid4().hex, pages, job_id or self.job_id)

    def request(self, index=0):
        return self.chat._read(self.run_id)['requests'][index]

    def modify(self, target=None):
        return {'intent': 'modify', 'reply': '准备调整指定页面的对齐，并检查文字可读性。',
                'changes': [{'targets': [target or self.plan['pages'][1]['slide_id']],
                             'instruction': '正文整体居中，保留原文及品牌区域', 'fix_hint': '调整正文容器的左右留白'}]}

    def answer(self):
        return {'intent': 'answer', 'reply': '当前正在处理页面，随后会检查修改结果。', 'changes': []}


@pytest.fixture
def h(tmp_path, monkeypatch):
    value = Harness(tmp_path, monkeypatch)
    yield value
    value.jobs.pool.shutdown(wait=True)


def test_concurrent_submission_is_durable_and_idempotent(h):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: h.submit(key='same-message-123', pages=[2, 1, 2]), range(8)))
    assert len({result['requests'][0]['id'] for result in results}) == 1
    assert len(h.chat.view(h.run_id)['requests']) == 1
    assert [message['role'] for message in h.chat.view(h.run_id)['messages']] == ['user', 'system']
    assert h.request()['page_numbers'] == [1, 2]
    assert len(h.queued) == 1
    with pytest.raises(ValueError, match='相同消息标识'):
        h.submit(text='不同要求', key='same-message-123', pages=[1, 2])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda number: h.submit(text=f'要求 {number}', key=f'distinct-{number:08d}'), range(8)))
    assert len(h.chat.view(h.run_id)['requests']) == 9
    assert len(h.chat.view(h.run_id)['messages']) == 18
    assert len(h.queued) == 1
    assert json.loads(h.chat._path(h.run_id).read_text())['requests'] == h.chat._read(h.run_id)['requests']


def test_running_queue_waits_for_checkpoint_and_model_reply_is_distinct(h, monkeypatch):
    monkeypatch.setattr(workflow, 'model_json', lambda *args: pytest.fail('idle worker must not analyze a running job'))
    h.submit(pages=[2])
    h.chat._idle(h.run_id)
    assert h.request()['status'] == 'queued'
    calls = []
    def model(name, policy, payload):
        calls.append(deepcopy(payload))
        assert name == 'enterprise_chat' and '没有接收截图' in policy
        assert payload['selected_targets'] == ['group-0001-part-001']
        assert '.hidden' not in payload['pages'][0]['text_excerpt']
        return h.modify()
    h.chat.checkpoint(h.job_id, h.plan, h.source, model)
    request = h.request()
    assert request['status'] == 'planned' and len(calls) == 1
    assert request['rows'][0]['issues'][0]['issue_id'].startswith('reviewer-chat-')
    assert request['rows'][0]['observed_revision'] == h.plan['pages'][1]['slide_version']
    messages = h.chat.view(h.run_id)['messages']
    assert [message['role'] for message in messages] == ['user', 'system', 'assistant', 'system']
    assert messages[2]['text'].startswith('修改计划：')
    claimed = h.chat.claim_ready(h.job_id, h.plan)
    assert claimed[0]['id'] == request['id'] and h.request()['status'] == 'applying'
    assert h.chat.claim_ready(h.job_id, h.plan) == claimed  # Restart can reclaim the same immutable work.
    h.chat.finish_request(h.job_id, request['id'], 'completed', '指定页已修改并检查。')
    h.chat.finish_request(h.job_id, request['id'], 'completed', '重复结果不能再次插入。')
    assert h.chat.claim_ready(h.job_id, h.plan) == []
    assert len([message for message in h.chat.view(h.run_id)['messages'] if message['text'] == '指定页已修改并检查。']) == 1


@pytest.mark.parametrize('intent', ['answer', 'clarify'])
def test_pure_answers_do_not_schedule_html_rewrites(h, intent):
    h.submit(text='现在处理到哪一步？')
    h.chat.checkpoint(h.job_id, h.plan, h.source,
        lambda *args: {'intent': intent, 'reply': '目前正在处理页面。', 'changes': []})
    assert h.request()['status'] == 'completed'
    assert h.chat.claim_ready(h.job_id, h.plan) == []
    assert h.chat.view(h.run_id)['messages'][-1]['role'] == 'assistant'
    assert len(h.queued) == 1  # Only the original queue wakeup, never jobs.execute.


def test_selected_page_identity_survives_global_page_number_shift(h):
    h.submit(pages=[2])
    shifted = {'pages': [page(0), page(0, 2), page(1)]}
    payloads = []
    def model(name, policy, payload):
        payloads.append(payload)
        return h.modify('group-0001-part-001')
    h.chat.checkpoint(h.job_id, shifted, h.source, model)
    row = h.request()['rows'][0]
    assert row['slide_id'] == 'group-0001-part-001' and row['generation_group'] == 1
    assert row['original_page'] == 2
    assert payloads[0]['selected_targets'] == ['group-0001-part-001']
    assert row['observed_revision'] == h.plan['pages'][1]['slide_version']


def test_partially_available_explicit_targets_keep_already_observed_identity(h):
    h.submit(pages=[2, 3])  # Page 2 exists; page 3 has not been produced yet.
    shifted = {'pages': [page(0), page(0, 2), page(1)]}
    def model(name, policy, payload):
        # Both selected positions now refer to the original second source group;
        # the new continuation at global page 2 must never replace its old target.
        assert 'group-0000-part-002' not in payload['selected_targets']
        return h.modify('group-0001-part-001')
    h.chat.checkpoint(h.job_id, shifted, h.source, model)
    assert h.request()['status'] == 'planned'
    assert {row['generation_group'] for row in h.request()['rows']} == {1}
    assert h.request()['rows'][0]['original_page'] == 2
    assert h.request()['rows'][0]['observed_revision'] == h.plan['pages'][1]['slide_version']


def test_natural_language_page_reference_retains_submission_catalog(h):
    h.submit(text='把第2页的正文居中')  # No optional page_numbers field.
    original = h.request()['submitted_catalog']
    assert original[1]['page'] == 2 and original[1]['slide_id'] == 'group-0001-part-001'
    shifted = {'pages': [page(0), page(0, 2), page(1)]}
    payloads = []
    def model(name, policy, payload):
        payloads.append(payload)
        assert payload['submitted_catalog'] == original
        return h.modify('group-0001-part-001')
    h.chat.checkpoint(h.job_id, shifted, h.source, model)
    assert len(payloads) == 1 and h.request()['status'] == 'planned'
    assert h.request()['rows'][0]['generation_group'] == 1


def test_planner_history_does_not_include_later_queued_user_messages(h):
    h.submit(text='第一条意见：第二页居中')
    h.submit(text='后来的意见：全册改成另一种样式')
    payloads = []
    def model(name, policy, payload):
        payloads.append(deepcopy(payload))
        return h.answer()
    h.chat.checkpoint(h.job_id, h.plan, h.source, model)
    assert len(payloads) == 2
    assert not any('后来的意见' in row['text'] for row in payloads[0]['history'])
    assert any('第一条意见' in row['text'] for row in payloads[0]['history'])
    assert any('后来的意见' in row['text'] for row in payloads[1]['history'])


@pytest.mark.parametrize('invalid', [
    {}, {'intent': 'modify', 'reply': '调整', 'changes': []},
    {'intent': 'modify', 'reply': '调整', 'changes': [{'targets': ['unknown'], 'instruction': '居中'}]},
    {'intent': 'answer', 'reply': '', 'changes': []},
])
def test_invalid_model_plan_has_at_most_four_attempts_and_cannot_apply(h, invalid):
    h.submit(pages=[2])
    attempts = []
    def model(name, policy, payload):
        attempts.append(deepcopy(payload))
        return deepcopy(invalid)
    h.chat.checkpoint(h.job_id, h.plan, h.source, model)
    assert [payload['attempt'] for payload in attempts] == [0, 1, 2, 3]
    assert all(payload.get('validation_feedback') for payload in attempts[1:])
    assert h.request()['status'] == 'failed' and h.chat.claim_ready(h.job_id, h.plan) == []
    assert not any(message['role'] == 'assistant' for message in h.chat.view(h.run_id)['messages'])


def test_invalid_plan_can_self_repair_on_fourth_attempt(h):
    h.submit(pages=[2])
    attempts = []
    def model(name, policy, payload):
        attempts.append(payload['attempt'])
        return h.modify() if payload['attempt'] == 3 else {'intent': 'modify', 'reply': '调整', 'changes': []}
    h.chat.checkpoint(h.job_id, h.plan, h.source, model)
    assert attempts == [0, 1, 2, 3] and h.request()['status'] == 'planned'


def test_deferred_request_waits_until_page_evidence_changes(h):
    incomplete = {'pages': [page(0)]}
    atomic_json(h.jobs.root / h.job_id / 'plan.json', incomplete)
    h.submit(text='全部页面标题保持一致')
    calls = []
    def model(name, policy, payload):
        calls.append(payload)
        return h.modify('group-0000-part-001') if payload['generation_complete'] else {
            'intent': 'defer', 'reply': '等正文页面就绪后统一检查。', 'changes': []}
    h.chat.checkpoint(h.job_id, incomplete, h.source, model)
    h.chat.checkpoint(h.job_id, incomplete, h.source, model)
    assert len(calls) == 1 and h.request()['status'] == 'queued'
    h.chat.checkpoint(h.job_id, h.plan, h.source, model)
    assert len(calls) == 2 and h.request()['status'] == 'planned'


def test_old_job_submission_is_rejected_without_a_new_message(h):
    h.add_job()
    with pytest.raises(ValueError, match='版本已更新'):
        h.submit()
    assert h.chat.view(h.run_id)['messages'] == [] and h.queued == []


def test_template_or_source_changes_do_not_apply_old_queued_requests(h):
    h.submit()
    h.jobs.update(h.job_id, source_sha256='source-v2')
    h.chat.checkpoint(h.job_id, h.plan, h.source, lambda *args: pytest.fail('changed inputs must not invoke the planner'))
    assert h.request()['status'] == 'needs_attention'


def test_late_message_finisher_wakes_idle_planner_and_resumes_existing_job(h, monkeypatch):
    h.submit(pages=[2])
    h.chat._idle(h.run_id)
    assert h.run_id not in h.chat.scheduled
    h.queued.clear()
    h.jobs.update(h.job_id, status='completed')
    atomic_json(h.jobs.root / h.job_id / 'revision-state.json', {})
    h.chat.finished(h.job_id)
    assert h.run_id in h.chat.scheduled and len(h.queued) == 1
    model_calls = []
    def model(folder, name, policy, payload):
        model_calls.append(payload)
        return h.modify()
    monkeypatch.setattr(workflow, 'model_json', model)
    function, *args = h.queued.pop(0)
    function(*args)
    assert len(model_calls) == 1 and h.request()['status'] == 'planned'
    assert h.jobs.get(h.job_id)['chat_resume'] is True and h.jobs.get(h.job_id)['status'] == 'queued'
    assert h.queued == [(h.jobs.execute, h.job_id)]
    assert h.run_id not in h.chat.scheduled


def test_jobs_execute_always_notifies_the_chat_finisher(h, monkeypatch):
    atomic_json(h.jobs.root / h.job_id / 'template.json', {})
    monkeypatch.setattr(workflow, 'execute', lambda jobs, job_id, agent: jobs.update(job_id, status='completed'))
    finished = []
    monkeypatch.setattr(h.chat, 'finished', lambda job_id: finished.append(job_id))
    h.jobs.execute(h.job_id)
    assert finished == [h.job_id] and h.jobs.get(h.job_id)['status'] == 'completed'


def test_job_replacement_during_idle_planning_closes_old_request(h, monkeypatch):
    h.jobs.update(h.job_id, status='completed')
    h.submit(pages=[2])
    def model(*args):
        h.add_job(status='running')
        return h.modify()
    monkeypatch.setattr(workflow, 'model_json', model)
    h.chat._idle(h.run_id)
    assert h.request()['status'] == 'needs_attention'
    assert not any(call[0] == h.jobs.execute for call in h.queued)


def test_idle_drain_eventually_answers_more_than_four_messages(h, monkeypatch):
    h.jobs.update(h.job_id, status='completed')
    for number in range(6):
        h.submit(text=f'现在进度如何？ {number}')
    calls = []
    def model(*args):
        calls.append(args[-1])
        return h.answer()
    monkeypatch.setattr(workflow, 'model_json', model)
    for _ in range(4):
        if not h.queued:
            break
        function, *args = h.queued.pop(0)
        function(*args)
    assert len(calls) == 6
    assert {request['status'] for request in h.chat._read(h.run_id)['requests']} == {'completed'}
    assert h.queued == []


def test_idle_snapshot_failure_finishes_queued_request_without_rescheduling_loop(h, monkeypatch):
    h.jobs.update(h.job_id, status='completed')
    h.submit()
    h.queued.clear()
    (h.jobs.root / h.job_id / 'plan.json').write_text('{broken json')
    monkeypatch.setattr(workflow, 'model_json', lambda *args: pytest.fail('bad snapshot must not reach the model'))
    h.chat._idle(h.run_id)
    assert h.request()['status'] == 'failed'
    assert h.queued == [] and h.run_id not in h.chat.scheduled


def test_incomplete_stopped_job_cannot_defer_a_request_forever(h, monkeypatch):
    h.jobs.update(h.job_id, status='failed')
    atomic_json(h.jobs.root / h.job_id / 'plan.json', {'pages': [page(0)]})
    h.submit(text='全部页统一调整标题层次')
    h.queued.clear()
    calls = []
    def model(*args):
        calls.append(args[-1])
        return {'intent': 'defer', 'reply': '等全部页面生成后处理。', 'changes': []}
    monkeypatch.setattr(workflow, 'model_json', model)
    h.chat._idle(h.run_id)
    assert h.request()['status'] in {'needs_attention', 'failed'}
    assert len(calls) <= 4 and h.queued == []


def test_failed_refinement_keeps_a_chat_edit_until_the_user_resumes(h, monkeypatch):
    h.jobs.update(h.job_id, status='failed', error='历史续跑失败')
    h.source['planned'].append({'generation_group': 2, 'title': '尚未生成的预算页', 'role': 'body'})
    atomic_json(h.jobs.root / h.job_id / 'source-plan.json', h.source)
    atomic_json(h.jobs.root / h.job_id / 'refinement-input.json', {'source_job_id': uuid4().hex})
    h.submit(text='把第2页正文居中', pages=[2])
    h.queued.clear()
    payloads = []
    def model(*args):
        payloads.append(args[-1])
        return h.modify()
    monkeypatch.setattr(workflow, 'model_json', model)
    h.chat._idle(h.run_id)
    request = h.request()
    assert request['status'] == 'planned' and request['awaiting_resume'] is True
    assert h.jobs.get(h.job_id)['status'] == 'failed' and h.queued == []
    assert payloads[0]['missing_groups'] == [{'generation_group': 2, 'title': '尚未生成的预算页', 'role': 'body'}]
    assert any('继续当前任务' in message['text'] for message in h.chat.view(h.run_id)['messages'])
    h.jobs.resume_optimization(h.job_id)
    assert h.request()['status'] == 'planned'
    assert h.queued == [(h.jobs.execute, h.job_id)]


def test_manual_resume_after_chat_restores_regular_optimization_scope(h):
    h.jobs.update(h.job_id, status='failed', chat_resume=True, error='检查中断')
    resumed = h.jobs.resume_optimization(h.job_id)
    assert resumed['status'] == 'queued' and resumed['chat_resume'] is False
    assert h.queued == [(h.jobs.execute, h.job_id)]
    snapshots = list((h.jobs.root / h.job_id / 'resume-history').glob('*/job.json'))
    assert len(snapshots) == 1 and json.loads(snapshots[0].read_text())['chat_resume'] is True


def test_unverified_chat_edit_retries_in_same_job_only_when_review_budget_is_available(h, monkeypatch):
    h.jobs.update(h.job_id, status='completed', stage='needs_review', visual_status='needs_review')
    h.submit(text='第2页标题需要更清晰', pages=[2])
    h.queued.clear()
    request = h.request()
    target = h.chat._catalog(h.plan)[1]
    row = {'generation_group': target['generation_group'], 'slide_id': target['slide_id'],
        'observed_revision': target['observed_revision'], 'original_page': 2,
        'observed_group_slide_ids': target['observed_group_slide_ids'],
        'verdict': 'fix', 'origin': 'independent_reviewer',
        'issues': [{'issue_id': 'reviewer-chat-' + request['id'], 'severity': 'medium',
                    'detail': '标题需要更清晰', 'slide_id': target['slide_id']}]}
    h.chat._set(h.run_id, request['id'], status='needs_attention', intent='modify', rows=[row], reason='视觉预算不足')
    atomic_json(h.jobs.root / h.job_id / 'chat-feedback-mapping.json',
        {'schema_version': 1, 'requests': {request['id']: {'rows': [row], 'status': 'needs_attention'}}})
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'true')
    monkeypatch.setenv('MARKETING_VISION_MODEL', 'doubao-seed-2-1-pro-260915')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '0.1')
    with pytest.raises(ValueError, match='不足 1 张指定页面各一次复查'):
        h.jobs.resume_optimization(h.job_id)
    assert h.jobs.get(h.job_id)['status'] == 'completed'
    assert h.request()['status'] == 'needs_attention' and h.queued == []

    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '2')
    resumed = h.jobs.resume_optimization(h.job_id)
    assert resumed['status'] == 'queued' and resumed['chat_resume'] is True
    assert h.request()['status'] == 'planned' and h.request()['retry_requested'] is True
    assert h.queued == [(h.jobs.execute, h.job_id)]


def test_recover_marks_active_conversations_once_and_never_replays_models(h):
    for number in range(4):
        h.submit(text=f'修改要求 {number}')
    requests = h.chat._read(h.run_id)['requests']
    h.chat._set(h.run_id, requests[1]['id'], status='planning')
    h.chat._set(h.run_id, requests[2]['id'], status='applying')
    h.chat.finish_request(h.job_id, requests[3]['id'], 'completed', '已处理。')
    queued_count = len(h.queued)
    h.jobs.recover()
    statuses = [request['status'] for request in h.chat._read(h.run_id)['requests']]
    assert statuses == ['needs_attention', 'needs_attention', 'needs_attention', 'completed']
    assert h.jobs.get(h.job_id)['status'] == 'failed'
    first = h.chat.view(h.run_id)
    h.jobs.recover()
    assert h.chat.view(h.run_id) == first and len(h.queued) == queued_count


def test_recover_preserves_a_failed_refinement_plan_waiting_for_user_resume(h):
    h.jobs.update(h.job_id, status='failed')
    atomic_json(h.jobs.root / h.job_id / 'refinement-input.json', {'source_job_id': uuid4().hex})
    h.submit(text='第2页正文需要更清晰', pages=[2])
    request = h.request()
    h.chat._set(h.run_id, request['id'], status='planned', intent='modify', rows=[{'generation_group': 1}], awaiting_resume=True)
    h.jobs.recover()
    assert h.request()['status'] == 'planned'
    assert h.request()['awaiting_resume'] is True
    h.chat._set(h.run_id, request['id'], status='needs_attention', reason='服务重启中断了处理，请核对草稿后重新发送意见。')
    h.jobs.recover()
    assert h.request()['status'] == 'planned' and 'reason' not in h.request()
    messages = h.chat.view(h.run_id)['messages']
    assert sum('无需重新发送' in message['text'] for message in messages) == 1
    h.jobs.recover()
    assert h.chat.view(h.run_id)['messages'] == messages


def test_api_chat_get_post_validation_and_stale_job_conflict(tmp_path, monkeypatch):
    app = create_app(tmp_path, DemoRuntime())
    with TestClient(app) as client:
        h = Harness(tmp_path, monkeypatch, jobs=app.state.presentations)
        url = f'/api/runs/{h.run_id}/presentation-chat'
        assert client.get(url).json() == {'messages': [], 'requests': [], 'active_job_id': h.job_id}
        body = {'text': '第二页居中', 'client_message_id': 'client-message-api-1', 'page_numbers': [2], 'job_id': h.job_id}
        first = client.post(url, json=body)
        assert first.status_code == 202
        assert client.post(url, json=body).json() == first.json()
        assert [message['role'] for message in first.json()['messages']] == ['user', 'system']
        for pages in ([True], ['2'], [0], [201], [1.1]):
            assert client.post(url, json={**body, 'page_numbers': pages}).status_code == 422
        assert client.post(url, json={**body, 'text': '   '}).status_code == 409
        assert client.post(url, json={k: value for k, value in body.items() if k != 'job_id'}).status_code == 422
        h.add_job()
        response = client.post(url, json={**body, 'client_message_id': 'client-message-api-2'})
        assert response.status_code == 409 and '版本已更新' in response.json()['detail']
        assert client.get(f'/api/runs/{"f" * 32}/presentation-chat').status_code == 404
