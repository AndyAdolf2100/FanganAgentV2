"""Exercise the v5 full-HTML adapter with real validation and no paid tools."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import urllib.request

from bs4 import BeautifulSoup
from PIL import Image
import pytest

from test_enterprise import fixture
from marketing_agent.enterprise import assets, model_html as adapter, pipeline
from marketing_agent.presentation_agent import PresentationAgent
from marketing_agent import presentation_agent


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_MODEL', 'glm-5')
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'false')
    monkeypatch.setenv('MARKETING_ENTERPRISE_LAYOUT_MAX_CALLS', '40')
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('No network requests in adapter tests'))
    folder = tmp_path / 'job'; folder.mkdir()
    template = fixture()
    (folder / 'template.json').write_text(json.dumps(template))
    (folder / 'manuscript.md').write_text('# 计划\n\n预算30万元。')
    source = {'title': '计划', 'metadata': dict.fromkeys(('presenter', 'advisor', 'date'), 'AiPPT'),
              'agenda': [], 'blocks': [{'id': 'b1', 'kind': 'paragraph', 'text': '预算30万元。'}],
              'planned': [{'role': 'cover', 'template_page': 0, 'title': '计划'},
                          {'role': 'body', 'template_page': 3, 'title': '预算', 'block_ids': ['b1']},
                          {'role': 'ending', 'template_page': 4, 'title': '谢谢'}]}

    class Jobs:
        root = tmp_path

        def __init__(self):
            self.data = {'options': {'template_revision': 1}, 'run_id': 'fixture',
                         'source_sha256': 'fixture-source', 'enterprise_workflow_version': 5}

        def get(self, job_id):
            assert job_id == 'job'
            return self.data

        def update(self, job_id, **changes):
            assert job_id == 'job'
            self.data.update(changes)

    value = SimpleNamespace(folder=folder, template=template, source=source, jobs=Jobs(), calls=[],
                            renders=[], pipelines=[], asset_preparations=[], html_behavior=None,
                            manifest={'version': assets.VERSION, 'status': 'not_needed', 'items': [], 'quality_issues': []})

    def full_html(payload):
        proposal = payload['proposal']
        soup = BeautifulSoup(payload['reference_html'], 'html.parser')
        title = soup.select_one('[data-template-element="0"] div')
        title.clear(); title.string = proposal['title']
        if proposal['role'] == 'body':
            body = soup.select_one('[data-template-element="1"]')
            body.clear(); body['data-enterprise-body'] = 'true'
            body.append(BeautifulSoup('<p data-source-block="b1">预算30万元。</p>', 'html.parser'))
        else:
            for key, text in source['metadata'].items():
                node = soup.new_tag('span', attrs={'data-metadata': key}); node.string = text; soup.body.append(node)
        return {'pages': [{'html': str(soup), 'charts': []}], 'reason': 'Fixture model-authored HTML'}

    value.full_html = full_html

    def model(folder, name, policy, payload):
        value.calls.append({'name': name, 'policy': policy, 'payload': deepcopy(payload)})
        if name == 'enterprise_theme':
            return {'accent': '#BB3322', 'text': '#223344', 'palette': ['#BB3322'], 'font': 'Noto Sans CJK SC'}
        if name == 'enterprise_template_contract':
            return {'protected_elements': [2, 3] if payload['role'] == 'body' else [1, 2],
                    'text_frames': [0], 'title_element': 0,
                    'body_frame': {'x': 60, 'y': 140, 'width': 1080, 'height': 460} if payload['role'] == 'body' else None}
        assert name == 'enterprise_full_html', f'Unexpected GLM step: {name}'
        return (value.html_behavior or full_html)(payload)

    def render(path, probe=False):
        value.renders.append((path.name, probe))
        plan = json.loads((path / 'plan.json').read_text())
        if not probe:
            (path / 'report.json').write_text(json.dumps({'checks': {}}))
        return {'pages': [{'page': i + 1, 'issues': []} for i in range(len(plan['pages']))]}

    def prepare(*args):
        value.asset_preparations.append(args)
        return deepcopy(value.manifest)

    def capture(*args):
        names = ('jobs', 'job_id', 'agent', 'plan', 'source', 'groups', 'generated', 'generate',
                 'check_deck', 'call', 'renderer', 'corrections', 'log', 'asset_manifest')
        captured = SimpleNamespace(**dict(zip(names, args)))
        value.pipelines.append(captured)
        return captured

    monkeypatch.setattr(adapter, 'plan_deck', lambda *args: deepcopy(source))
    monkeypatch.setattr(adapter, 'model_json', model)
    monkeypatch.setattr(adapter, 'run_renderer', render)
    monkeypatch.setattr(presentation_agent, 'analyze_reference', lambda *args: {'status': 'analyzed'})
    monkeypatch.setattr(assets, 'prepare_enterprise_assets', prepare)
    monkeypatch.setattr(pipeline, 'run', capture)
    value.execute = lambda: adapter.execute(value.jobs, 'job', PresentationAgent(folder))
    return value


def html_calls(context):
    return [call for call in context.calls if call['name'] == 'enterprise_full_html']


@pytest.mark.parametrize('version', [5, 4, None])
def test_only_explicit_v5_jobs_enter_the_new_pipeline(context, version):
    if version is None:
        context.jobs.data.pop('enterprise_workflow_version')
    else:
        context.jobs.data['enterprise_workflow_version'] = version
    result = context.execute()
    assert len(context.pipelines) == int(version == 5)
    assert len(context.asset_preparations) == int(version == 5)
    assert (context.folder / 'workflow-checkpoint.json').exists() == (version == 5)
    if version == 5:
        assert result.plan['enterprise_workflow_version'] == 5
        assert result.asset_manifest == context.manifest
        assert html_calls(context) == []  # Generation now belongs to pipeline.run.
        assert result.groups[1]['generation_group'] == 1
    else:
        assert context.jobs.data['status'] == 'completed'
        assert len(html_calls(context)) == 3
        assert 'enterprise_workflow_version' not in json.loads((context.folder / 'plan.json').read_text())
    assert json.loads((context.folder / 'template.json').read_text()) == context.template


@pytest.mark.parametrize('failure', ['malformed_json', 'non_object', 'bad_page_count', 'changed_source'])
def test_one_bad_full_html_attempt_is_returned_to_pipeline_without_nested_retry(context, failure):
    captured = context.execute()
    malformed = '{"pages": [{"html": "broken'
    bad_answer = [] if failure == 'non_object' else {'pages': []}

    def invalid(payload):
        if failure == 'malformed_json':
            raise json.JSONDecodeError('Unterminated string', malformed, 20)
        if failure == 'changed_source':
            answer = context.full_html(payload)
            answer['pages'][0]['html'] = answer['pages'][0]['html'].replace('预算30万元。', '预算31万元。')
            return answer
        return bad_answer

    context.html_behavior = invalid
    proposal = captured.groups[1]
    with pytest.raises(ValueError):
        captured.generate(proposal)
    assert len(html_calls(context)) == 1
    checkpoint = json.loads((context.folder / 'workflow-checkpoint.json').read_text())
    assert checkpoint['model_calls'] == len(context.calls)

    # The pipeline explicitly owns the next attempt and its original diagnosis.
    feedback = {'keep_selected_template': True, 'findings': [{'detail': '字号拥挤，请保留原文并调整分栏'}]}
    context.html_behavior = context.full_html
    pages = captured.generate(proposal, feedback)
    assert len(html_calls(context)) == 2 and len(pages) == 1
    assert html_calls(context)[1]['payload']['validation_feedback'] == feedback
    if failure == 'malformed_json':
        assert html_calls(context)[1]['payload']['previous_result'] == malformed
    elif failure in ('bad_page_count', 'changed_source'):
        assert html_calls(context)[1]['payload']['previous_result'] is not None
    assert '预算30万元。' in pages[0]['html'] and '预算31万元。' not in pages[0]['html']


def accepted_asset(context):
    path = context.folder / 'enterprise-asset-scene.png'
    Image.new('RGB', (512, 288), '#337766').save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    item = {'id': 'scene', 'generation_group': 1, 'target': 'body_content', 'purpose': '正文概念场景',
            'required': True, 'status': 'accepted', 'alias': '__PPT_ASSET_scene__',
            'file': path.name, 'sha256': sha, 'protected_elements': [2, 3],
            'review': {'verdict': 'pass', 'image_sha256': sha}}
    context.manifest = {'version': assets.VERSION, 'status': 'accepted', 'items': [item], 'quality_issues': []}
    return item


def with_asset(context, payload, placement='body'):
    answer = context.full_html(payload)
    soup = BeautifulSoup(answer['pages'][0]['html'], 'html.parser')
    if placement != 'missing':
        node = soup.new_tag('img', src='__PPT_ASSET_unknown__' if placement == 'unknown' else '__PPT_ASSET_scene__')
        (soup.select_one('[data-enterprise-body]') if placement in {'body', 'unknown'} else soup.body).append(node)
    answer['pages'][0]['html'] = str(soup)
    return answer


def test_accepted_asset_aliases_reach_glm_and_expand_after_generation(context):
    item = accepted_asset(context)
    context.html_behavior = lambda payload: with_asset(context, payload)
    captured = context.execute()
    pages = captured.generate(captured.groups[1])
    request = html_calls(context)[0]['payload']
    assert request['available_assets'][0]['alias'] == item['alias']
    assert request['available_assets'][0]['width'] == 512
    assert 'data:image/png;base64,' not in json.dumps(request)
    assert '__PPT_PROTECTED_2__' in request['reference_html']
    assert item['alias'] not in pages[0]['html'] and 'data:image/png;base64,' in pages[0]['html']
    assert '企业固定品牌' in pages[0]['html']
    # Revisions are packed back into both asset and protected-node aliases.
    captured.generate(captured.groups[1], {'keep_selected_template': True}, pages)
    packed = html_calls(context)[1]['payload']['current_pages'][0]['html']
    assert item['alias'] in packed and '__PPT_PROTECTED_2__' in packed
    assert 'data:image/png;base64,' not in packed


@pytest.mark.parametrize('placement', ['missing', 'outside_body', 'unknown'])
def test_asset_usage_errors_reach_pipeline_after_one_glm_attempt(context, placement):
    accepted_asset(context)
    context.html_behavior = lambda payload: with_asset(context, payload, placement)
    captured = context.execute()
    with pytest.raises(ValueError):
        captured.generate(captured.groups[1])
    assert len(html_calls(context)) == 1


def test_resume_preserves_checkpoint_model_count_and_call_limit(context, monkeypatch):
    captured = context.execute()
    before = json.loads((context.folder / 'workflow-checkpoint.json').read_text())['model_calls']
    assert before == len(context.calls) > 0
    # Pipeline persists base metadata even if no generated group was committed yet.
    (context.folder / 'plan.json').write_text(json.dumps(captured.plan))
    monkeypatch.setattr(adapter, 'plan_deck', lambda *args: pytest.fail('Resume must reuse source plan'))
    context.calls.clear()
    resumed = context.execute()
    assert context.calls == []
    assert json.loads((context.folder / 'workflow-checkpoint.json').read_text())['model_calls'] == before
    resumed.generate(resumed.groups[1])
    after = json.loads((context.folder / 'workflow-checkpoint.json').read_text())['model_calls']
    assert after == before + 1
    # Reload at the exact limit; restarting cannot grant another paid attempt.
    monkeypatch.setenv('MARKETING_ENTERPRISE_LAYOUT_MAX_CALLS', str(after))
    at_limit = context.execute()
    count = len(context.calls)
    with pytest.raises(adapter.ModelCallLimitError, match='调用上限'):
        at_limit.generate(at_limit.groups[1])
    assert len(context.calls) == count
    assert json.loads((context.folder / 'workflow-checkpoint.json').read_text())['model_calls'] == after


def test_changed_inputs_cannot_reuse_model_call_checkpoint(context):
    context.execute()
    prior = (context.folder / 'workflow-checkpoint.json').read_text()
    (context.folder / 'manuscript.md').write_text('# 另一份文稿\n\n预算30万元。')
    before = len(context.calls)
    with pytest.raises(ValueError, match='任务输入已变化'):
        context.execute()
    assert len(context.calls) == before
    assert (context.folder / 'workflow-checkpoint.json').read_text() == prior


def test_unreviewed_embedded_bitmap_cannot_bypass_asset_gate():
    known='data:image/png;base64,YWJj'
    reference=f'<img src="{known}">'
    adapter.check_image_resources(reference,reference,{})
    accepted='data:image/png;base64,YXNzZXQ='
    adapter.check_image_resources(f'<img src="{accepted}">',reference,{'__PPT_ASSET_1__':accepted})
    with pytest.raises(ValueError,match='未验收图片'):
        adapter.check_image_resources('<img src="data:image/png;base64,bmV3">',reference,{})
    with pytest.raises(ValueError,match='未验收图片'):
        adapter.check_image_resources('<style>body{background:url(data:image/svg+xml,%3Csvg%3E)}</style>',reference,{})


def test_reanalyzed_template_does_not_reuse_invalid_html_with_old_aliases(context,monkeypatch):
    captured=context.execute();proposal=captured.groups[1]
    def wrong(payload):
        answer=context.full_html(payload)
        answer['pages'][0]['html']=answer['pages'][0]['html'].replace('30','31')
        return answer
    context.html_behavior=wrong
    with pytest.raises(ValueError):captured.generate(proposal)
    monkeypatch.setattr(adapter,'select_body_repair_template',lambda *args:{'template_page':3,'reanalyze_contract':True,'reason':'Reanalyze bounds from current template'})
    def corrected(payload):
        assert payload['previous_result'] is None
        assert payload['required_dom']['body_container']
        return context.full_html(payload)
    context.html_behavior=corrected
    result=captured.generate(proposal,{'findings':[{'issues':[{'severity':'high','detail':'Reanalyze contract'}]}]})
    assert result[0]['body_template_revision']['original_template_page']==3


def test_resuming_old_page_does_not_roll_back_newer_template_contract(context):
    first=context.execute()
    groups=[first.generate(p) for p in first.groups]
    old=groups[1][0]
    old['body_template_revision']={'original_template_page':3,'reason':'Historical repair'}
    saved=deepcopy(first.plan);saved['pages']=[p for group in groups for p in group]
    (context.folder/'plan.json').write_text(json.dumps(saved))
    contract_file=context.folder/'enterprise-design-contract.json'
    metadata=json.loads(contract_file.read_text())
    metadata['contracts']['3']['body_frame']={'x':40,'y':130,'width':1120,'height':480}
    metadata['contracts']['3']['reason']='New model analysis opens the available white panel'
    contract_file.write_text(json.dumps(metadata))
    resumed=context.execute()
    assert resumed.generated[1][0]['template_contract']['body_frame']['x']==60
    assert json.loads((context.folder/'plan.json').read_text())['pages'][1]['html']==old['html']
    changed=resumed.generate(resumed.groups[1])
    assert html_calls(context)[-1]['payload']['constraints']['body_frame']['x']==40
    assert changed[0]['template_contract']['body_frame']['x']==40


def test_optimization_resume_retains_untouched_original_pages_after_prefix_commit(context):
    first=context.execute()
    original=deepcopy(first.plan)
    original['pages']=[page for proposal in first.groups for page in first.generate(proposal)]
    raw=json.dumps(original).encode()
    (context.folder/'refinement-base-plan.json').write_bytes(raw)
    (context.folder/'refinement-input.json').write_text(json.dumps({
        'source_job_id':'a'*32,'source_plan_sha256':hashlib.sha256(raw).hexdigest()}))
    prefix=deepcopy(original);prefix['pages']=prefix['pages'][:1]
    (context.folder/'plan.json').write_text(json.dumps(prefix))
    # The pipeline double captures args; a real store owns/validates this file.
    (context.folder/'revision-state.json').write_text('{}')
    before=len(html_calls(context))
    resumed=context.execute()
    assert len(resumed.generated)==3
    assert resumed.generated[1][0]['html']==original['pages'][1]['html']
    assert resumed.generated[2][0]['html']==original['pages'][2]['html']
    assert len(html_calls(context))==before
