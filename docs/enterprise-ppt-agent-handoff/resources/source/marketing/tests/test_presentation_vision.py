import json
import pytest
from marketing_agent import presentation_images as images
from marketing_agent.presentation_vision import validate_review, reserve_review, review_and_repair


def test_each_review_request_has_exactly_one_image_even_when_references_exist(tmp_path,monkeypatch):
    from marketing_agent import presentation_vision as vision
    folder=tmp_path/'job';folder.mkdir()
    references=folder/'template-reference'/'previews';references.mkdir(parents=True)
    for i in (1,2):(references/f'{i}.png').touch()
    plan={'design_mode':'enterprise','style_id':'fixture','theme':{},
          'pages':[{'layout':'body','title':str(i),'template_page':i-1} for i in (1,2)]}
    monkeypatch.setenv('MARKETING_VISION_MODEL','doubao-seed-2-0-mini-260428')
    monkeypatch.setenv('MARKETING_IMAGE_BASE_URL','https://example.invalid')
    monkeypatch.setenv('MARKETING_IMAGE_API_KEY','fixture')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','5')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    monkeypatch.setattr(vision,'image_part',lambda path,*args:{'type':'image_url','image_url':{'url':str(path)}})
    requests=[]
    class Response:
        def __init__(self,page):self.page=page
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):
            answer={'pages':[{'page':self.page,'verdict':'pass','observed':'fixture','issues':[]}]}
            return json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}]}).encode()
    def request(req,**kwargs):
        payload=json.loads(req.data);requests.append(payload)
        schema=payload['response_format']['json_schema']['schema']['properties']['pages']
        assert schema['minItems']==schema['maxItems']==1
        page=schema['items']['properties']['page']['enum']
        assert len(page)==1
        urls=[x['image_url']['url'] for x in payload['messages'][1]['content'] if x['type']=='image_url']
        assert urls==[str(folder/'previews'/f'{page[0]}.png')]
        return Response(page[0])
    monkeypatch.setattr(vision.urllib.request,'urlopen',request)
    results=vision.review_batch(folder,plan,[1,2])
    assert [p['page'] for p in results]==[1,2] and len(requests)==2
    assert vision.review_batch(folder,plan,[1,2])==results and len(requests)==2
    assert len(json.loads((tmp_path/'vision-cache/budget.json').read_text())['reservations'])==2


def test_single_page_review_rejects_another_page_number():
    with pytest.raises(ValueError,match='遗漏或重复'):
        validate_review({'pages':[{'page':2,'verdict':'pass','issues':[]}]},[1])


def test_pro_format_correction_retains_one_image_and_reserves_each_request(tmp_path,monkeypatch):
    from marketing_agent import presentation_vision as vision
    folder=tmp_path/'job';folder.mkdir();payloads=[]
    monkeypatch.setenv('MARKETING_VISION_MODEL','doubao-seed-2-1-pro-260915')
    monkeypatch.setenv('MARKETING_IMAGE_BASE_URL','https://example.invalid')
    monkeypatch.setenv('MARKETING_IMAGE_API_KEY','fixture')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','5')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','10')
    monkeypatch.setattr(vision,'image_part',lambda *args:{'type':'image_url','image_url':{'url':'fixture'}})
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):
            value='{"pages":[' if len(payloads)==1 else json.dumps({'pages':[{'page':1,'verdict':'pass','observed':'actual','issues':[]}]})
            return json.dumps({'choices':[{'message':{'content':value}}]}).encode()
    def request(req,**kwargs):
        p=json.loads(req.data);payloads.append(p)
        assert p['thinking']['type']=='enabled' and p['max_tokens']==12000
        assert sum(x['type']=='image_url' for m in p['messages'] if isinstance(m['content'],list) for x in m['content'])==1
        return Response()
    monkeypatch.setattr(vision.urllib.request,'urlopen',request)
    plan={'theme':{},'style_id':'fixture','pages':[{'layout':'body','title':'title'}]}
    assert vision.review_batch(folder,plan,[1])[0]['verdict']=='pass'
    assert len(payloads)==2 and 'JSON格式' in payloads[1]['messages'][-1]['content']
    assert [r['reserved_rmb'] for r in json.loads((tmp_path/'vision-cache/budget.json').read_text())['reservations']]==['0.50','0.50']


def test_concurrent_reviews_remain_separate_single_page_calls(tmp_path,monkeypatch):
    from marketing_agent import presentation_vision as vision
    calls=[]
    def review(folder,plan,indexes):calls.append(indexes);return [{'page':indexes[0]}]
    monkeypatch.setattr(vision,'review_batch',review)
    result=list(vision.review_pages(tmp_path,{},[1,2,3,4],workers=3))
    assert sorted(calls)==[[1],[2],[3],[4]]
    assert sorted(p['page'] for rows in result for p in rows)==[1,2,3,4]


def test_visual_result_cannot_hide_missing_pages_or_high_severity():
    with pytest.raises(ValueError):validate_review({'pages':[{'page':1,'verdict':'pass','issues':[]}]},[1,2])
    result=validate_review({'pages':[{'page':1,'verdict':'pass','issues':[{'severity':'high','detail':'标题覆盖产品'}]}]},[1])
    assert result[0]['verdict']=='fix'
    suggestion={'pages':[{'page':1,'verdict':'fix','issues':[{'severity':'low','detail':'风格偏好'}]}]}
    assert validate_review(suggestion,[1])[0]=={'page':1,'verdict':'pass','issues':[{'severity':'low','detail':'风格偏好'}]}


def test_visual_budget_is_persistent_and_stops_before_next_call(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_VISION_MODEL','doubao-seed-2-0-mini-260428')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    for _ in range(16):reserve_review(tmp_path)
    with pytest.raises(ValueError):reserve_review(tmp_path)
    assert len(json.loads((tmp_path/'budget.json').read_text())['reservations'])==16


def test_visual_budget_extension_keeps_old_reservations_and_hard_cap(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_VISION_MODEL','doubao-seed-2-0-mini-260428')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','20')
    monkeypatch.setenv('MARKETING_VISION_MODEL','doubao-seed-2-0-mini-260428')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    for _ in range(16):reserve_review(tmp_path)
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','65')
    for _ in range(49):reserve_review(tmp_path)
    with pytest.raises(ValueError):reserve_review(tmp_path)
    assert len(json.loads((tmp_path/'budget.json').read_text())['reservations'])==65


@pytest.mark.parametrize('configured',['1000','2000'])
def test_visual_limit_allows_thousand_and_stops_at_boundary(tmp_path,monkeypatch,configured):
    monkeypatch.setenv('MARKETING_VISION_MODEL','doubao-seed-2-0-mini-260428')
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
