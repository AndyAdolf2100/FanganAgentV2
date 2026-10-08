"""Enterprise-only asset integration contracts; no paid model calls."""
from copy import deepcopy
import hashlib
import json

from PIL import Image
import pytest

from marketing_agent.enterprise import assets as a


@pytest.fixture
def context(tmp_path, monkeypatch):
    folder = tmp_path / 'job'
    folder.mkdir()
    for key, value in {'MARKETING_MODEL': 'glm-5', 'MARKETING_IMAGE_MODEL': 'doubao-seedream-5-0-flash-260915',
                       'MARKETING_VISION_MODEL': 'doubao-seed-2-1-pro-260915', 'MARKETING_IMAGE_ENABLED': 'true',
                       'MARKETING_VISION_ENABLED': 'true', 'MARKETING_IMAGE_BASE_URL': 'https://test.invalid',
                       'MARKETING_IMAGE_API_KEY': 'test-only', 'MARKETING_IMAGE_SIZE': '2048x1152',
                       'MARKETING_IMAGE_PRICE_RMB': '0.12', 'MARKETING_PPT_BUDGET_RMB': '5',
                       'MARKETING_IMAGE_MAX_REQUESTS': '16', 'MARKETING_VISION_MAX_REQUESTS': '16'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('MARKETING_ENTERPRISE_ASSET_MAX', raising=False)
    monkeypatch.delenv('MARKETING_IMAGE_CACHE_DIR', raising=False)
    monkeypatch.setattr(a.urllib.request, 'urlopen', lambda *args, **kwargs: pytest.fail('Unexpected network request'))
    source = {'title': '项目方案', 'blocks': [{'id': 'b1', 'text': '在室外活动中体验概念产品。', 'kind': 'paragraph'}]}
    groups = [{'role': 'cover', 'template_page': 0, 'generation_group': 0},
              {'role': 'body', 'template_page': 1, 'generation_group': 1, 'title': '体验场景', 'block_ids': ['b1']}]
    contracts = {'0': {'body_frame': None, 'protected_elements': [0]},
                 '1': {'body_frame': {'x': 60, 'y': 120, 'width': 1080, 'height': 480}, 'protected_elements': [2, 3]}}
    theme = {'accent': '#009900', 'text': '#112233', 'font': 'Arial', 'palette': ['#009900', '#FFFFFF']}
    return folder, source, groups, contracts, theme


def task(name='scene', **changes):
    return {'id': name, 'generation_group': 1, 'target': 'body_content', 'purpose': '室外概念产品场景，主体居中且四周可裁切。',
            'prompt': 'A fictional unbranded concept product in a natural outdoor activity scene, no text or logos.',
            'required': True, 'reference_asset_id': None, **changes}


def planner(tasks):
    return lambda name, policy, payload: {'reason': '正文需要展示室外应用场景。' if tasks else '该页事实和表格足以表达，无需配图。', 'assets': deepcopy(tasks)}


def fake_tools(monkeypatch, verdict='pass'):
    generated, inspected = [], []

    def generate(prompt, destination, reference=None):
        generated.append({'prompt': prompt, 'reference': reference, 'destination': destination})
        Image.new('RGB', (512, 288), '#337766').save(destination)

    def inspect(folder, path, request, theme):
        inspected.append(request['id'])
        return {'verdict': verdict, 'observed': '绿色概念主体位于室外中央。',
                'issues': [] if verdict == 'pass' else [{'criterion': 'semantic', 'detail': '没有看见要求的主体。'}],
                'model': a._models()['vision'], 'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'policy_sha256': a._digest(a.REVIEW_POLICY)}

    monkeypatch.setattr(a.presentation_images, 'generate_image', generate)
    monkeypatch.setattr(a, 'inspect_asset', inspect)
    return generated, inspected


def prepare(context, call, **kwargs):
    return a.prepare_enterprise_assets(*context, call, **kwargs)


def test_no_demand_and_fixed_only_decks_do_not_generate_images(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)
    result = prepare(context, planner([]))
    assert result['status'] == 'not_needed' and result['quality_issues'] == []
    assert '无需配图' in result['reason']
    assert generated == inspected == []
    folder, source, groups, contracts, theme = context
    result = a.prepare_enterprise_assets(folder, source, groups[:1], contracts, theme,
        lambda *args: pytest.fail('fixed-only deck must not plan new artwork'))
    assert result['status'] == 'not_needed'


def test_asset_planner_sees_the_pages_template_based_design_intent(context, monkeypatch):
    context[2][1]['design_intent'] = {'focus': '室外体验', 'template_motif': '沿用模板的宽幅图形区域'}
    seen = []
    def call(name, policy, payload):
        seen.append(payload)
        return {'reason': '现有模板元素足以表达，无需新增图片。', 'assets': []}
    result = prepare(context, call)
    assert result['status'] == 'not_needed'
    assert seen[0]['groups'][0]['template_page'] == 1
    assert seen[0]['groups'][0]['design_intent'] == context[2][1]['design_intent']


@pytest.mark.parametrize('changes', [
    {'generation_group': 0}, {'target': 'brand'}, {'replace_template_element': 2},
    {'reference_asset_id': '__PPT_PROTECTED_2__'}, {'id': '../brand'}])
def test_unauthorized_brand_or_fixed_page_requests_never_reach_seedream(context, monkeypatch, changes):
    generated, inspected = fake_tools(monkeypatch)
    result = prepare(context, planner([task(**changes)]))
    assert result['status'] == 'degraded'
    assert result['quality_issues'][0]['type'] == 'asset_planning_unavailable'
    assert generated == inspected == []


def test_four_image_cap_rejects_oversized_plan_without_chargeable_calls(context, monkeypatch):
    generated, _ = fake_tools(monkeypatch)
    result = prepare(context, planner([task('scene' + str(i)) for i in range(5)]))
    assert result['status'] == 'degraded' and not generated


def test_accepted_assets_roundtrip_cache_content_theme_and_models(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)
    snapshot = deepcopy(context[1:])
    first = prepare(context, planner([task()]))
    assert first['status'] == 'accepted'
    assert first['items'][0]['alias'] == '__PPT_ASSET_scene__'
    second = prepare(context, planner([task()]))
    assert second['items'][0]['cache_hit'] is True
    assert len(generated) == len(inspected) == 1
    assert context[1:] == snapshot

    context[4]['accent'] = '#FF9900'
    third = prepare(context, planner([task()]))
    assert third['items'][0]['cache_key'] != first['items'][0]['cache_key']
    assert '#FF9900' in generated[-1]['prompt']
    context[1]['blocks'][0]['text'] += '家庭用户参与。'
    fourth = prepare(context, planner([task()]))
    assert fourth['items'][0]['cache_key'] != third['items'][0]['cache_key']
    monkeypatch.setenv('MARKETING_MODEL', 'glm-5.1')
    fifth = prepare(context, planner([task()]))
    assert fifth['items'][0]['cache_key'] != fourth['items'][0]['cache_key']
    assert len(generated) == len(inspected) == 4


def test_rejected_asset_has_no_alias_and_explicit_required_failure(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch, verdict='fix')
    result = prepare(context, planner([task()]))
    assert result['status'] == 'degraded'
    assert result['items'][0]['alias'] is None
    assert result['items'][0]['review']['verdict'] == 'fix'
    briefs, resources = a.assets_for_group(context[0], result, 1)
    assert resources == {} and briefs[0]['status'] == 'unavailable'
    usage = a.evaluate_required_usage(['<div data-enterprise-body></div>'], result, 1)
    assert usage['status'] == 'needs_assets'
    assert usage['unavailable_required_asset_ids'] == ['scene']
    assert len(generated) == len(inspected) == 1


def test_vision_disabled_avoids_unreviewable_generation(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'false')
    result = prepare(context, planner([task()]))
    assert result['status'] == 'degraded' and generated == inspected == []
    assert '未发送' in result['items'][0]['reason']


@pytest.mark.parametrize('key,value', [('MARKETING_MODEL', 'other-text'),
    ('MARKETING_IMAGE_MODEL', 'other-image'), ('MARKETING_VISION_MODEL', 'other-vision')])
def test_fixed_model_roles_fail_closed(context, monkeypatch, key, value):
    generated, inspected = fake_tools(monkeypatch)
    monkeypatch.setenv(key, value)
    result = prepare(context, planner([task()]))
    assert result['status'] == 'degraded' and generated == inspected == []


def test_failed_anchor_prevents_independent_subject_regeneration(context, monkeypatch):
    generated, _ = fake_tools(monkeypatch, verdict='fix')
    result = prepare(context, planner([task(), task('followup', reference_asset_id='scene')]))
    assert len(generated) == 1
    assert result['items'][1]['status'] == 'unavailable'
    assert '参考素材尚未验收' in result['items'][1]['reason']


def test_accepted_reference_uses_actual_prior_asset(context, monkeypatch):
    generated, _ = fake_tools(monkeypatch)
    result = prepare(context, planner([task(), task('followup', reference_asset_id='scene')]))
    assert result['status'] == 'accepted'
    assert generated[0]['reference'] is None
    assert generated[1]['reference'] == context[0] / result['items'][0]['file']


def test_usage_permits_continuation_group_and_rejects_brand_or_other_group(context, monkeypatch):
    fake_tools(monkeypatch)
    result = prepare(context, planner([task()]))
    briefs, resources = a.assets_for_group(context[0], result, 1)
    assert briefs[0]['width'] == 512
    uri = resources['__PPT_ASSET_scene__']
    document = f'<div data-enterprise-body><img src="{uri}"></div>'
    usage = a.evaluate_required_usage(['<div data-enterprise-body>第一页仅正文</div>', document], result, 1)
    assert usage == {'status': 'passed', 'used_asset_ids': ['scene'], 'unavailable_required_asset_ids': []}
    with pytest.raises(ValueError, match='缺少本组必要'):
        a.evaluate_required_usage(['<div data-enterprise-body></div>'], result, 1)
    with pytest.raises(ValueError, match='规划正文组'):
        a.validate_asset_usage(document, result, 0)
    with pytest.raises(ValueError, match='规划正文组'):
        a.validate_asset_usage(f'<img src="{uri}">', result, 1)
    with pytest.raises(ValueError, match='品牌节点'):
        a.validate_asset_usage(f'<div data-enterprise-body><div data-template-element="2"><img src="{uri}"></div></div>', result, 1)
    with pytest.raises(ValueError, match='全局CSS'):
        a.validate_asset_usage(f'<style>.brand{{background:url({uri})}}</style>', result, 1)
    with pytest.raises(ValueError, match='未知或未验收'):
        a.validate_asset_usage('<img src="__PPT_ASSET_missing__">', result, 1)
    # A later body-template choice uses its current protection contract.
    alternate = f'<div data-enterprise-body><div data-template-element="2"><img src="{uri}"></div></div>'
    assert a.validate_asset_usage(alternate, result, 1, {'protected_elements': [7]})['used_asset_ids'] == ['scene']
    with pytest.raises(ValueError, match='品牌节点'):
        a.validate_asset_usage(document.replace('<img', '<img data-template-element="7"'), result, 1, {'protected_elements': [7]})


def test_tampering_invalidates_accepted_asset_and_cache(context, monkeypatch):
    generated, _ = fake_tools(monkeypatch)
    result = prepare(context, planner([task()]))
    entry = result['items'][0]
    Image.new('RGB', (512, 288), '#FF3300').save(context[0] / entry['file'])
    with pytest.raises(ValueError, match='被修改'):
        a.assets_for_group(context[0], result, 1)
    cached = context[0].parent / 'enterprise-asset-cache' / (entry['cache_key'] + '.png')
    cached.write_bytes(b'broken')
    regenerated = prepare(context, planner([task()]))
    assert regenerated['status'] == 'accepted' and len(generated) == 2


def test_seed_inspection_has_one_image_and_uses_shared_budget(context, monkeypatch):
    folder = context[0]
    path = folder / 'asset.png'
    Image.new('RGB', (512, 288), '#337766').save(path)
    requests, reservations = [], []
    monkeypatch.setattr(a.presentation_vision, 'reserve_review', reservations.append)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return json.dumps({'choices': [{'message': {'content': json.dumps({'verdict': 'pass',
                'observed': '绿色概念产品居中。', 'issues': []})}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 30}}).encode()

    def request(value, **kwargs):
        requests.append(json.loads(value.data))
        return Response()

    monkeypatch.setattr(a.urllib.request, 'urlopen', request)
    review = a.inspect_asset(folder, path, task(), context[4])
    assert reservations == [folder.parent / 'vision-cache']
    image_parts = [part for message in requests[0]['messages'] if isinstance(message['content'], list)
                   for part in message['content'] if part['type'] == 'image_url']
    assert len(image_parts) == 1
    assert requests[0]['model'] == 'doubao-seed-2-1-pro-260915'
    assert review['image_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_budget_failure_records_degradation_without_retry(context, monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise ValueError('图片预算不足')

    monkeypatch.setattr(a.presentation_images, 'generate_image', unavailable)
    result = prepare(context, planner([task()]))
    assert calls == [1]
    assert result['status'] == 'degraded' and result['items'][0]['alias'] is None
    assert '预算不足' in result['items'][0]['reason']


def repair_planner(tasks, requests):
    def call(name, policy, payload):
        requests.append({'name': name, 'payload': deepcopy(payload), 'policy': policy})
        if name == 'enterprise_asset_requirements':
            return planner(tasks)(name, policy, payload)
        assert name == 'enterprise_asset_repair'
        return {'prompt': f'A fictional unbranded concept product, preserving its original identity. Composition revision {payload["attempt"]}: centered whole subject with wider margins, flat illustration, no additional text.',
                'reason': '主体原身份不变，增大留白并以扁平插画改善裁切和风格。'}
    return call


def repair_tools(monkeypatch, verdicts, identical=False):
    generated, inspected = [], []

    def generate(prompt, destination, reference=None):
        generated.append({'prompt': prompt, 'destination': destination, 'reference': reference})
        Image.new('RGB', (512, 288), '#337766' if identical else (len(generated) * 35, 90, 100)).save(destination)

    def inspect(folder, path, request, theme):
        index = len(inspected)
        inspected.append({'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'task': deepcopy(request)})
        verdict = verdicts[min(index, len(verdicts) - 1)]
        return {'verdict': verdict, 'observed': '同一概念主体，边缘过近且画面偏写实。',
                'issues': [] if verdict == 'pass' else [{'criterion': 'composition', 'detail': '主体边缘太近，需要增加裁切余量。'},
                                                      {'criterion': 'theme', 'detail': '画面偏写实，原任务要求扁平插画。'}],
                'model': a._models()['vision'], 'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'policy_sha256': a._digest(a.REVIEW_POLICY)}

    monkeypatch.setattr(a.presentation_images, 'generate_image', generate)
    monkeypatch.setattr(a, 'inspect_asset', inspect)
    return generated, inspected


def test_seed_rejection_drives_glm_prompt_repair_and_new_seedream_image(context, monkeypatch):
    generated, inspected = repair_tools(monkeypatch, ['fix', 'pass'])
    calls = []; original = task(); call = repair_planner([original], calls)
    result = prepare(context, call)
    entry = result['items'][0]
    assert result['status'] == 'accepted' and entry['attempt_count'] == 2
    assert [attempt['phase'] for attempt in entry['attempts']] == ['rejected', 'accepted']
    assert len(generated) == len(inspected) == 2 and generated[0]['prompt'] != generated[1]['prompt']
    assert [c['name'] for c in calls] == ['enterprise_asset_requirements', 'enterprise_asset_repair']
    feedback = calls[1]['payload']
    assert feedback['visual_rejection']['issues'][0]['detail'] == '主体边缘太近，需要增加裁切余量。'
    assert feedback['task'] == original and feedback['previous_prompt'] == original['prompt']
    assert inspected[1]['task']['original_prompt'] == original['prompt']
    for field in ('id', 'generation_group', 'target', 'purpose', 'required', 'reference_asset_id'):
        assert inspected[1]['task'][field] == entry[field] == original[field]
    before = deepcopy(calls)
    reused = prepare(context, lambda *args: pytest.fail('A restart must reuse the stable initial plan'))
    assert reused['items'][0]['cache_hit'] is True and reused['items'][0]['attempt_count'] == 2
    assert reused['items'][0]['effective_prompt'] == entry['effective_prompt']
    assert calls == before and len(generated) == len(inspected) == 2


def test_three_self_repairs_are_a_persistent_maximum_not_a_restart_allowance(context, monkeypatch):
    generated, inspected = repair_tools(monkeypatch, ['fix'])
    calls = []; result = prepare(context, repair_planner([task()], calls))
    assert result['status'] == 'degraded' and result['items'][0]['alias'] is None
    assert result['items'][0]['attempt_count'] == 4 and len(generated) == len(inspected) == 4
    assert len([call for call in calls if call['name'] == 'enterprise_asset_repair']) == 3
    history = deepcopy(result['items'][0]['attempts'])
    resumed = prepare(context, lambda *args: pytest.fail('Exhausted attempts cannot replan on resume'))
    assert resumed['items'][0]['attempts'] == history
    assert len(generated) == len(inspected) == 4


def test_identical_rejected_image_is_not_reinspected_even_for_changed_prompts(context, monkeypatch):
    generated, inspected = repair_tools(monkeypatch, ['fix'], identical=True)
    calls = []; result = prepare(context, repair_planner([task()], calls))
    assert len(generated) == 4 and len(inspected) == 1
    assert all(attempt['reused_rejection'] for attempt in result['items'][0]['attempts'][1:])
    assert result['status'] == 'degraded'


@pytest.mark.parametrize('phase', ['generated', 'rejected'])
def test_restart_resumes_saved_phase_without_repeating_image_or_old_visual_call(context, monkeypatch, phase):
    generated, inspected = repair_tools(monkeypatch, ['fix', 'pass'] if phase == 'rejected' else ['pass'])
    calls = []; call = repair_planner([task()], calls)
    original_write = a._write; interrupted = []

    def crash_after_save(path, value):
        original_write(path, value)
        if path.parent.name == 'enterprise-asset-attempts' and value['attempts'] and value['attempts'][-1]['phase'] == phase and not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt('simulate process crash after durable stage commit')

    monkeypatch.setattr(a, '_write', crash_after_save)
    with pytest.raises(KeyboardInterrupt):
        prepare(context, call)
    assert len(generated) == 1 and len(inspected) == int(phase == 'rejected')
    result = prepare(context, call)
    assert result['status'] == 'accepted'
    expected = 2 if phase == 'rejected' else 1
    assert len(generated) == len(inspected) == expected
    assert len([c for c in calls if c['name'] == 'enterprise_asset_requirements']) == 1


@pytest.mark.parametrize('stage', ['generation', 'vision', 'repair'])
def test_budget_or_tool_failure_stops_without_explicit_resume(context, monkeypatch, stage):
    generated, inspected = repair_tools(monkeypatch, ['fix'])
    calls = []; normal_call = repair_planner([task()], calls)

    def failed(*args, **kwargs):
        raise ValueError('工具预算已用完')

    if stage == 'generation':
        monkeypatch.setattr(a.presentation_images, 'generate_image', failed)
    elif stage == 'vision':
        monkeypatch.setattr(a, 'inspect_asset', failed)

    def call(name, policy, payload):
        if stage == 'repair' and name == 'enterprise_asset_repair':
            return failed()
        return normal_call(name, policy, payload)

    result = prepare(context, call)
    entry = result['items'][0]
    assert entry['attempts'][-1]['phase'] == 'failed' and '预算' in entry['reason']
    assert entry['attempt_count'] == (2 if stage == 'repair' else 1)
    previous = deepcopy(entry['attempts'])
    resumed = prepare(context, lambda *args: pytest.fail('Persisted failure must stop resume requests'))
    assert resumed['items'][0]['attempts'] == previous
    assert len(generated) <= 1 and len(inspected) <= 1


@pytest.mark.parametrize('stage', ['generation', 'vision', 'repair'])
def test_explicit_resume_continues_failed_stage_without_resetting_attempts(context, monkeypatch, stage):
    generated, inspected = repair_tools(monkeypatch, ['fix', 'pass'] if stage == 'repair' else ['pass'])
    calls = []; normal_call = repair_planner([task()], calls)
    normal_generate, normal_inspect = a.presentation_images.generate_image, a.inspect_asset
    failures = []

    def fail_once(callback, *args, **kwargs):
        if not failures:
            failures.append(True)
            raise ValueError('工具预算已用完')
        return callback(*args, **kwargs)

    if stage == 'generation':
        monkeypatch.setattr(a.presentation_images, 'generate_image', lambda *args, **kwargs: fail_once(normal_generate, *args, **kwargs))
    if stage == 'vision':
        monkeypatch.setattr(a, 'inspect_asset', lambda *args, **kwargs: fail_once(normal_inspect, *args, **kwargs))

    def call(name, policy, payload):
        if stage == 'repair' and name == 'enterprise_asset_repair':
            return fail_once(normal_call, name, policy, payload)
        return normal_call(name, policy, payload)

    failed = prepare(context, call)['items'][0]
    count = failed['attempt_count']
    result = prepare(context, call, resume=True)
    entry = result['items'][0]
    assert result['status'] == 'accepted' and entry['attempt_count'] == count
    assert entry['attempts'][-1]['resume_count'] == 1
    assert entry['attempts'][-1]['failures'] == failed['attempts'][-1]['failures']
    assert len(generated) == len(inspected) == (2 if stage == 'repair' else 1)
    assert len([c for c in calls if c['name'] == 'enterprise_asset_requirements']) == 1


def test_explicit_resume_still_stops_when_budget_remains_blocked(context, monkeypatch):
    requests = []

    def unavailable(*args, **kwargs):
        requests.append(True)
        raise ValueError('图片预算不足')

    monkeypatch.setattr(a.presentation_images, 'generate_image', unavailable)
    prepare(context, planner([task()]))
    result = prepare(context, planner([task()]), resume=True)
    entry = result['items'][0]
    assert result['status'] == 'degraded' and len(requests) == 2
    assert entry['attempt_count'] == 1 and entry['attempts'][0]['phase'] == 'failed'
    assert len(entry['attempts'][0]['failures']) == 2 and entry['attempts'][0]['resume_count'] == 1


def test_explicit_resume_cannot_reset_exhausted_visual_corrections(context, monkeypatch):
    generated, inspected = repair_tools(monkeypatch, ['fix'])
    calls = []; call = repair_planner([task()], calls)
    first = prepare(context, call)
    resumed = prepare(context, lambda *args: pytest.fail('Four rejected images cannot reset'), resume=True)
    assert resumed['items'][0]['attempts'] == first['items'][0]['attempts']
    assert len(generated) == len(inspected) == 4


def test_explicit_resume_can_recover_budget_blocked_initial_plan(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)

    def unavailable(*args):
        raise RuntimeError('企业模板模型调用上限已到')

    failed = prepare(context, unavailable)
    assert failed['status'] == 'degraded' and not generated
    prepare(context, lambda *args: pytest.fail('A normal reread cannot repeat planning'))
    resumed = prepare(context, planner([task()]), resume=True)
    assert resumed['status'] == 'accepted' and len(generated) == len(inspected) == 1
    history = json.loads((context[0] / 'enterprise-asset-plan.json').read_text())
    assert history['planning_attempts'] == 2 and history['phase'] == 'planned'
    assert history['failures'][0]['failed_phase'] == 'planning'


def test_explicit_resume_after_service_configuration_is_fixed(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)
    monkeypatch.setattr(a.presentation_images, 'configured', lambda: False)
    failed = prepare(context, planner([task()]))
    assert failed['status'] == 'degraded' and not generated
    monkeypatch.setattr(a.presentation_images, 'configured', lambda: True)
    resumed = prepare(context, lambda *args: pytest.fail('Keep the original task plan'), resume=True)
    assert resumed['status'] == 'accepted' and resumed['items'][0]['attempt_count'] == 1
    assert len(generated) == len(inspected) == 1


def test_combined_budget_preflight_avoids_unreviewable_generation_and_makes_no_reservation(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '0.20')
    result = prepare(context, planner([task()]))
    assert result['status'] == 'degraded' and generated == inspected == []
    assert '一次Seed验收' in result['items'][0]['reason']
    assert not list(context[0].parent.rglob('budget.json'))
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '0.62')
    resumed = prepare(context, planner([task()]), resume=True)
    assert resumed['status'] == 'accepted' and resumed['items'][0]['attempt_count'] == 1
    assert len(generated) == len(inspected) == 1
    # Both tools are mocked: any ledger here would mean the preflight itself charged.
    assert not list(context[0].parent.rglob('budget.json'))


def test_preflight_includes_existing_global_reservations(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)
    ledger = context[0].parent / 'vision-cache' / 'budget.json'
    a._write(ledger, {'reservations': [{'reserved_rmb': '4.40'}]})
    before = ledger.read_bytes()
    result = prepare(context, planner([task()]))
    assert result['status'] == 'degraded' and generated == inspected == []
    assert ledger.read_bytes() == before


def test_resume_existing_png_needs_only_visual_budget_and_accepted_cache_needs_none(context, monkeypatch):
    generated, inspected = fake_tools(monkeypatch)
    normal_inspect = a.inspect_asset

    def unavailable(*args):
        raise ValueError('Seed预算暂不足')

    monkeypatch.setattr(a, 'inspect_asset', unavailable)
    prepare(context, planner([task()]))
    assert len(generated) == 1 and not inspected
    monkeypatch.setattr(a, 'inspect_asset', normal_inspect)
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '0.50')
    resumed = prepare(context, planner([task()]), resume=True)
    assert resumed['status'] == 'accepted' and len(generated) == len(inspected) == 1
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB', '0')
    cached = prepare(context, planner([task()]), resume=True)
    assert cached['status'] == 'accepted' and cached['items'][0]['cache_hit'] is True
    assert len(generated) == len(inspected) == 1


@pytest.mark.parametrize('policy', ['VERSION', 'PLAN_POLICY', 'REVIEW_POLICY', 'REPAIR_POLICY'])
def test_policy_changes_never_reuse_old_visual_acceptance(context, monkeypatch, policy):
    generated, inspected = fake_tools(monkeypatch)
    first = prepare(context, planner([task()]))
    monkeypatch.setattr(a, policy, getattr(a, policy) + '\nUpdated policy')
    second = prepare(context, planner([task()]))
    assert first['items'][0]['cache_key'] != second['items'][0]['cache_key']
    assert len(generated) == len(inspected) == 2


@pytest.mark.parametrize('changes', [
    {'generation_group': 0}, {'purpose': 'changed use'}, {'id': 'changed'}, {'required': False},
    {'prompt': task()['prompt']},
])
def test_repair_cannot_change_locked_task_fields_or_repeat_same_prompt(context, monkeypatch, changes):
    generated, inspected = repair_tools(monkeypatch, ['fix'])

    def call(name, policy, payload):
        if name == 'enterprise_asset_requirements':
            return planner([task()])(name, policy, payload)
        return {'prompt': 'Preserve the original subject but add larger margins and use a flat illustration style.',
                'reason': '纠正构图', **changes}

    result = prepare(context, call)
    assert result['status'] == 'degraded' and result['items'][0]['attempts'][-1]['failed_phase'] == 'revising'
    assert len(generated) == len(inspected) == 1
    for field in ('id', 'purpose', 'generation_group', 'required'):
        assert result['items'][0][field] == task()[field]
    resumed = prepare(context, lambda *args: pytest.fail('Resume cannot bypass invalid repair output'), resume=True)
    assert resumed['items'][0]['attempts'] == result['items'][0]['attempts']


def test_unconfigured_service_stops_before_attempts_and_preserves_provenance_label_policy(context, monkeypatch):
    generated, inspected = repair_tools(monkeypatch, ['fix'])
    monkeypatch.setattr(a.presentation_images, 'configured', lambda: False)
    result = prepare(context, planner([task()]))
    assert result['status'] == 'degraded' and not generated and not inspected
    assert '未配置' in result['items'][0]['reason']
    for policy in (a.PLAN_POLICY, a.REVIEW_POLICY, a.REPAIR_POLICY):
        assert 'AI生成' in policy and '保留' in policy
    assert '不得据此拒绝' in a.REVIEW_POLICY


def test_service_failure_stops_other_assets_in_the_same_batch(context, monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('图片服务返回HTTP 503，未自动重复计费请求')

    monkeypatch.setattr(a.presentation_images, 'generate_image', unavailable)
    result = prepare(context, planner([task(), task('independent')]))
    assert calls == [1] and result['status'] == 'degraded'
    assert '未发送后续请求' in result['items'][1]['reason']
    prepare(context, lambda *args: pytest.fail('Resume uses the original plan'))
    assert calls == [1]
