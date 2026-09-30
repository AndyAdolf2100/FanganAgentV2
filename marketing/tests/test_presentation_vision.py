import json
import pytest
from marketing_agent import presentation_images as images
from marketing_agent.presentation_vision import validate_review, reserve_review, review_and_repair


def test_visual_result_cannot_hide_missing_pages_or_high_severity():
    with pytest.raises(ValueError):validate_review({'pages':[{'page':1,'verdict':'pass','issues':[]}]},[1,2])
    result=validate_review({'pages':[{'page':1,'verdict':'pass','issues':[{'severity':'high','detail':'标题覆盖产品'}]}]},[1])
    assert result[0]['verdict']=='fix'


def test_visual_budget_is_persistent_and_stops_before_next_call(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    for _ in range(16):reserve_review(tmp_path)
    with pytest.raises(ValueError):reserve_review(tmp_path)
    assert len(json.loads((tmp_path/'budget.json').read_text())['reservations'])==16


def test_visual_budget_extension_keeps_old_reservations_and_hard_cap(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','20')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    for _ in range(16):reserve_review(tmp_path)
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','65')
    for _ in range(49):reserve_review(tmp_path)
    with pytest.raises(ValueError):reserve_review(tmp_path)
    assert len(json.loads((tmp_path/'budget.json').read_text())['reservations'])==65


@pytest.mark.parametrize('configured',['1000','2000'])
def test_visual_limit_allows_thousand_and_stops_at_boundary(tmp_path,monkeypatch,configured):
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','20')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS',configured)
    # Synthetic lower historical reservations isolate the request boundary
    # from the independent RMB cap; no real ledger or model is touched.
    previous=[{'reserved_rmb':'0.001'} for _ in range(999)]
    (tmp_path/'budget.json').write_text(json.dumps({'reservations':previous}))
    reserve_review(tmp_path)
    with pytest.raises(ValueError,match='请求次数'):reserve_review(tmp_path)
    actual=json.loads((tmp_path/'budget.json').read_text())['reservations']
    assert len(actual)==1000 and actual[:999]==previous


def test_project_image_collection_reuse_is_source_and_style_bound(tmp_path,monkeypatch):
    original=tmp_path/'original';original.mkdir();(original/'cover.png').write_bytes(b'project-agent-asset')
    plan={'source_sha256':'a'*64,'style_id':'brand_launch','assets':{'cover':'cover.png'},'asset_provenance':'project_agent'}
    images.cache_asset_collection(plan,original)
    monkeypatch.setattr(images,'configured',lambda:True)
    for source,style,reused in [('a'*64,'brand_launch',True),('b'*64,'brand_launch',False),('a'*64,'business',False)]:
        folder=tmp_path/(source[:1]+style);folder.mkdir()
        current={'source_sha256':source,'style_id':style,'assets':{}}
        assert images.prepare_assets(current,'汽车文稿',folder,tmp_path/'builtins') is not reused
        assert (folder/'cover.png').exists() is reused


def test_disabled_review_never_reports_pass(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_VISION_ENABLED','false')
    result=review_and_repair(tmp_path,lambda:pytest.fail('no render required'))
    assert result['status']=='not_reviewed'


def test_agent_image_failure_pauses_later_paid_calls(tmp_path,monkeypatch):
    plan={'assets':{'cover':'cover.png','scene1':'scene1.png'},'generated_asset_slots':['cover','scene1'],
          'design_mode':'narrative','pages':[{'layout':'cover','asset':'cover'},{'layout':'image','asset':'scene1'}]}
    monkeypatch.setattr(images,'plan_image_tasks',lambda *a:{'cover':{'prompt':'anchor'},'scene1':{'prompt':'same product','reference_role':'cover'}})
    attempts=[]
    def fail(*a,**kw):attempts.append(a);raise RuntimeError('quota unavailable')
    monkeypatch.setattr(images,'generate_image',fail)
    result=images.materialize_assets(plan,tmp_path,True)
    assert len(attempts)==1 and not plan['assets']
    assert plan['pages'][1]['layout']=='statement' and result['notice']


def test_scene_edit_receives_successful_anchor(tmp_path,monkeypatch):
    plan={'assets':{'cover':'cover.png','scene1':'scene1.png'},'generated_asset_slots':['cover','scene1'],
          'design_mode':'narrative','pages':[{'layout':'cover','asset':'cover'},{'layout':'image','asset':'scene1'}]}
    monkeypatch.setattr(images,'plan_image_tasks',lambda *a:{'cover':{'prompt':'anchor'},'scene1':{'prompt':'same product','reference_role':'cover'}})
    refs=[]
    def generate(prompt,destination,reference=None):refs.append(reference);destination.write_bytes(b'fixture')
    monkeypatch.setattr(images,'generate_image',generate)
    images.materialize_assets(plan,tmp_path,True)
    assert refs==[None,tmp_path/'cover.png'] and len(plan['assets'])==2
