"""Offline recovery checks for drafts with uncommitted generation groups."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from marketing_agent.enterprise import assets, model_html as m, pipeline
from marketing_agent.enterprise.revisions import atomic_json
from marketing_agent.presentation_agent import PresentationAgent
from test_enterprise_model_html import sample
from test_enterprise_pipeline import Harness


@pytest.fixture
def sparse_input():
    template, proposal, reference, contract, document = sample()
    blocks = [{'id': f'b{gi}', 'kind': 'paragraph', 'text': f'预算{30 + gi}万元。'} for gi in range(4)]
    groups = [{**proposal, 'group': gi, 'generation_group': gi, 'block_ids': [block['id']]}
              for gi, block in enumerate(blocks)]
    pages = [{**group, 'model_html': True, 'template_contract': deepcopy(contract),
              'html': document.replace('data-source-block="b1"', f'data-source-block="b{gi}"')
                              .replace('预算30万元。', blocks[gi]['text'])}
             for gi, group in enumerate(groups)]
    plan = {'title': '计划', 'design_mode': 'enterprise', 'enterprise_layout_mode': m.MODE,
            'canvas': {'width': 1200, 'height': 675}, 'source_sha256': 'same',
            'source_blocks': blocks, 'template_id': template['id'], 'template_revision': 1,
            'theme': {'accent': '#BB3322', 'text': '#223344', 'palette': ['#BB3322'], 'font': 'Noto Sans CJK SC'},
            'style_id': 'enterprise', 'source_run_id': 'run', 'enterprise_workflow_version': 5, 'pages': []}
    source = {'title': plan['title'], 'blocks': blocks, 'agenda': [], 'metadata': {},
              'planned': [{k: v for k, v in group.items() if k != 'generation_group'} for group in groups]}
    return SimpleNamespace(template=template, groups=groups, pages=pages, plan=plan, source=source,
                           references={3: reference}, contracts={'3': contract})


def restore(data, pages, **kwargs):
    return m.resume_prefix({**data.plan, 'pages': pages}, data.plan, data.groups,
                           data.references, data.contracts, data.source, data.template,
                           existing_contracts=True, **kwargs)


@pytest.mark.parametrize('present', [(0, 2), (1, 3), (0, 1, 2, 3)])
def test_sparse_import_preserves_group_positions_and_input(sparse_input, present):
    data = sparse_input
    pages = [deepcopy(data.pages[gi]) for gi in present]
    before = deepcopy(pages)
    restored = restore(data, pages, allow_sparse=True)
    assert [len(group) for group in restored] == [int(gi in present) for gi in range(4)]
    assert [page['generation_group'] for group in restored for page in group] == list(present)
    restored[present[0]][0]['html'] = 'mutated caller copy'
    assert pages == before


def test_default_prefix_mode_still_rejects_a_gap(sparse_input):
    with pytest.raises(ValueError, match='不连续'):
        restore(sparse_input, [sparse_input.pages[0], sparse_input.pages[2]])


@pytest.mark.parametrize('order', [(2, 0), (0, 2, 0), (4,)])
def test_sparse_import_rejects_out_of_order_repeated_or_unknown_groups(sparse_input, order):
    pages = [{**sparse_input.pages[min(gi, 3)], 'generation_group': gi} for gi in order]
    with pytest.raises(ValueError, match='不连续'):
        restore(sparse_input, pages, allow_sparse=True)


@pytest.mark.parametrize('invalid_group', [True, '2', -1, None])
def test_sparse_import_rejects_invalid_group_schema(sparse_input, invalid_group):
    page = {**sparse_input.pages[2], 'generation_group': invalid_group}
    with pytest.raises(ValueError, match='格式无效'):
        restore(sparse_input, [page], allow_sparse=True)


@pytest.mark.parametrize('change', ['source', 'brand', 'proposal', 'contract', 'model_html', 'duplicate_source'])
def test_sparse_import_does_not_weaken_page_validation(sparse_input, change):
    pages = [deepcopy(sparse_input.pages[2])]
    if change == 'source':
        pages[0]['html'] = pages[0]['html'].replace('预算32万元。', '预算99万元。')
    elif change == 'brand':
        pages[0]['html'] = pages[0]['html'].replace('企业固定品牌', '改动品牌')
    elif change == 'proposal':
        pages[0]['block_ids'] = ['b0']
    elif change == 'contract':
        pages[0]['template_contract']['protected_elements'] = [999]
    elif change == 'model_html':
        pages[0]['model_html'] = False
    else:
        pages.append(deepcopy(pages[0]))
    with pytest.raises(ValueError):
        restore(sparse_input, pages, allow_sparse=True)


def test_pipeline_generates_only_missing_groups_from_sparse_import(tmp_path, monkeypatch, sparse_input):
    data = sparse_input
    h = Harness(tmp_path, monkeypatch, count=4)
    h.plan, h.groups, h.source = deepcopy(data.plan), deepcopy(data.groups), deepcopy(data.source)
    h.generated = restore(data, [data.pages[0], data.pages[2]], allow_sparse=True)
    h.plan['pages'] = [page for group in h.generated for page in group]
    h.generate_hook = lambda gi, count, feedback, current: [deepcopy(data.pages[gi])]
    h.validation_hook = lambda plan: m.html.validate_sources(
        [page['html'] for page in plan['pages']], data.source['blocks'], [], [])

    quality = h.run()

    assert quality['ready_for_delivery'], quality['limitations']
    assert h.counts == Counter({1: 1, 3: 1})
    assert [page['generation_group'] for page in h.plan['pages']] == [0, 1, 2, 3]
    assert h.plan['pages'][0]['html'] == data.pages[0]['html']
    assert h.plan['pages'][2]['html'] == data.pages[2]['html']


@pytest.mark.parametrize('chat_resume', [False, True])
def test_execute_refinement_imports_sparse_snapshot_before_pipeline(tmp_path, monkeypatch, sparse_input, chat_resume):
    data = sparse_input
    folder = tmp_path / 'job'
    folder.mkdir()
    original = {**data.plan, 'pages': [data.pages[0], data.pages[2]]}
    atomic_json(folder / 'refinement-base-plan.json', original)
    atomic_json(folder / 'refinement-input.json', {'source_job_id': 'a' * 32,
        'source_plan_sha256': hashlib.sha256((folder / 'refinement-base-plan.json').read_bytes()).hexdigest()})
    atomic_json(folder / 'plan.json', original)
    atomic_json(folder / 'template.json', data.template)
    atomic_json(folder / 'source-plan.json', data.source)
    atomic_json(folder / 'enterprise-design-contract.json', {'contracts': data.contracts, 'visual_analysis': {'3': {}}})
    (folder / 'manuscript.md').write_text('离线草稿恢复测试')
    if chat_resume:
        # execute must still supply original sparse fallback slots; the real
        # pipeline separately validates and prefers its committed RevisionStore.
        atomic_json(folder / 'revision-state.json', {})
    state = {'enterprise_workflow_version': 5, 'chat_resume': chat_resume,
             'options': {'template_revision': 1}, 'run_id': 'run', 'source_sha256': 'same'}
    jobs = SimpleNamespace(root=tmp_path, get=lambda _: state,
                           update=lambda _, **changes: state.update(changes))
    monkeypatch.setenv('MARKETING_MODEL', 'glm-5')
    monkeypatch.setattr(m, 'model_json', lambda *args, **kwargs: pytest.fail('no paid model calls'))
    monkeypatch.setattr(m, 'run_renderer', lambda *args, **kwargs: {'pages': []})
    monkeypatch.setattr(assets, 'prepare_enterprise_assets', lambda *args, **kwargs: None)
    captured = {}
    def capture(jobs, job_id, agent, plan, source, groups, generated, *args):
        captured.update(generated=deepcopy(generated), plan=deepcopy(plan))
        return 'pipeline reached'
    monkeypatch.setattr(pipeline, 'run', capture)

    assert m.execute(jobs, 'job', PresentationAgent(folder)) == 'pipeline reached'
    assert [len(group) for group in captured['generated']] == [1, 0, 1, 0]
    assert [page['generation_group'] for page in captured['plan']['pages']] == [0, 2]
    agent_state = json.loads((folder / 'agent-run.json').read_text())
    assert agent_state['resumed_groups'] == 2 and agent_state['resumed_pages'] == 2
