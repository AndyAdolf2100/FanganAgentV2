import json
import hashlib
from copy import deepcopy
import pytest
from marketing_agent.enterprise.refinement import load_refinement_base,merge_feedback,source_snapshot
from marketing_agent.presentation import PresentationJobs
from marketing_agent.store import Store
from marketing_agent.presentation_budget import reserve


def test_refinement_creates_visible_version_and_preserves_original(tmp_path,monkeypatch):
    jobs=PresentationJobs(tmp_path,Store(tmp_path/'test.sqlite'));submitted=[]
    monkeypatch.setattr(jobs.pool,'submit',lambda *args:submitted.append(args))
    original={'id':'a'*32,'run_id':'b'*32,'status':'completed','created':1,'options':{'template_revision':1},'source_sha256':'original'}
    folder=jobs.root/original['id'];folder.mkdir()
    data={'job.json':original,'plan.json':{'pages':[{'title':'原页'}]},'template.json':{},'source-plan.json':{'planned':[]},'enterprise-design-contract.json':{}}
    for name,value in data.items():(folder/name).write_text(json.dumps(value))
    (folder/'manuscript.md').write_text('原文')
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    new=jobs.optimize(original['id'],[{'page':1,'issues':[{'severity':'medium','detail':'留白失衡'}]}])
    assert new['optimization_of']==original['id'] and new['run_id']==original['run_id']
    assert jobs.latest(original['run_id'])['id']==new['id'] and submitted[0][1]==new['id']
    assert {p.name:p.read_bytes() for p in folder.iterdir()}==before
    assert (jobs.root/new['id']/'plan.json').read_bytes()==before['plan.json']
    assert (jobs.root/new['id']/'refinement-base-plan.json').read_bytes()==before['plan.json']
    assert load_refinement_base(jobs.root/new['id'])==data['plan.json']
    (jobs.root/new['id']/'plan.json').write_text(json.dumps({'pages':[{'title':'优化后已完成前缀'}]}))
    assert load_refinement_base(jobs.root/new['id'])==data['plan.json']
    assert jobs.optimize(original['id'])['id']==new['id'] and len(submitted)==1


def test_feedback_is_separate_from_model_observation_and_page_bound():
    old=[{'page':1,'verdict':'pass','observed':'model observation','issues':[]}];snapshot=deepcopy(old)
    result=merge_feedback(old,[{'page':1,'issues':[{'severity':'medium','detail':'真实遮挡'}]}])
    assert old==snapshot and result[0]['verdict']=='fix'
    assert result[0]['observed']=='model observation' and result[0]['issues'][0]['origin']=='independent_reviewer'
    with pytest.raises(ValueError):merge_feedback(old,[{'page':2,'issues':[]}])


@pytest.fixture
def legacy_refinement(tmp_path):
    source = tmp_path / ('a' * 32)
    target = tmp_path / ('b' * 32)
    source.mkdir(); target.mkdir()
    full = {'title': '原始完整优化输入', 'pages': [{'generation_group': i, 'title': f'旧页{i}'} for i in range(56)]}
    raw = json.dumps(full, ensure_ascii=False, indent=2).encode()
    (source / 'plan.json').write_bytes(raw)
    metadata = {'source_job_id': source.name, 'source_plan_sha256': hashlib.sha256(raw).hexdigest()}
    (target / 'refinement-input.json').write_text(json.dumps(metadata))
    (target / 'plan.json').write_text(json.dumps({'pages': full['pages'][:3]}))
    return source, target, full, raw, metadata


def test_old_job_backfills_full_base_without_modifying_source_or_current_prefix(legacy_refinement):
    source, target, full, raw, _ = legacy_refinement
    before = (target / 'plan.json').read_bytes()
    assert load_refinement_base(target) == full
    assert (target / 'refinement-base-plan.json').read_bytes() == raw
    assert (source / 'plan.json').read_bytes() == raw
    assert (target / 'plan.json').read_bytes() == before
    # Once captured, recovery no longer depends on a mutable/source job folder.
    (source / 'plan.json').unlink()
    assert len(load_refinement_base(target)['pages']) == 56


@pytest.mark.parametrize('where', ['source', 'base'])
def test_refinement_base_rejects_tampering_without_falling_back(legacy_refinement, where):
    source, target, _, raw, _ = legacy_refinement
    if where == 'base':
        (target / 'refinement-base-plan.json').write_bytes(raw)
    path = source / 'plan.json' if where == 'source' else target / 'refinement-base-plan.json'
    path.write_text(json.dumps({'pages': [{'title': '被修改内容'}]}))
    damaged = path.read_bytes()
    with pytest.raises(ValueError, match='SHA256不匹配'):
        load_refinement_base(target)
    assert path.read_bytes() == damaged
    if where == 'source':
        assert not (target / 'refinement-base-plan.json').exists()
    else:
        assert (source / 'plan.json').read_bytes() == raw


@pytest.mark.parametrize('source_id', ['../escape', '', None, 'g' * 32, 'a' * 31])
def test_refinement_base_rejects_invalid_source_id_even_with_local_base(legacy_refinement, source_id):
    _, target, _, raw, metadata = legacy_refinement
    (target / 'refinement-base-plan.json').write_bytes(raw)
    metadata['source_job_id'] = source_id
    (target / 'refinement-input.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='32位十六进制'):
        load_refinement_base(target)


def test_missing_original_never_falls_back_to_partial_current_plan(legacy_refinement):
    source, target, _, _, _ = legacy_refinement
    (source / 'plan.json').unlink()
    with pytest.raises(ValueError, match='不能以当前已完成前缀代替'):
        load_refinement_base(target)
    assert not (target / 'refinement-base-plan.json').exists()


def test_authorized_budget_above_twenty_preserves_ledger(tmp_path,monkeypatch):
    root=tmp_path/'vision-cache';root.mkdir();previous=[{'reserved_rmb':'20'}]
    (root/'budget.json').write_text(json.dumps({'reservations':previous}))
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','50')
    reserve(root,'0.10',1000)
    assert json.loads((root/'budget.json').read_text())['reservations'][:1]==previous
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','20')
    with pytest.raises(ValueError,match='累计预算'):reserve(root,'0.10',1000)
