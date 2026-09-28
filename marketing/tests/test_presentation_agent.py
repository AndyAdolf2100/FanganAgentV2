import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from marketing_agent import presentation_agent as agent, presentation_vision as vision
from marketing_agent.presentation_budget import reserve


def test_shared_budget_includes_legacy_image_and_vision_reservations(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','0.20')
    reserve(tmp_path/'image-cache','.12',6)
    with pytest.raises(ValueError,match='累计预算'):reserve(tmp_path/'vision-cache','.10',64)
    assert not (tmp_path/'vision-cache/budget.json').exists()


def test_shared_budget_concurrent_tools_cannot_overspend(tmp_path,monkeypatch):
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','0.10')
    def call(name):
        try:reserve(tmp_path/name,'.10',64);return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(call,['image-cache','vision-cache']))==1


def test_project_agent_records_real_tool_failure(tmp_path):
    a=agent.PresentationAgent(tmp_path)
    assert a.call('read_manuscript',lambda:7)==7
    with pytest.raises(ValueError):a.call('generate_pages',lambda:(_ for _ in ()).throw(ValueError('fixture')))
    a.finish('failed')
    report=json.loads((tmp_path/'agent-run.json').read_text())
    assert [t['status'] for t in report['tools']]==['completed','failed']


def test_director_rejects_arbitrary_tools_without_dispatch(tmp_path,monkeypatch):
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return json.dumps({'choices':[{'message':{'content':json.dumps({'actions':[{'tool':'shell','page':1,'brief':'bad'}]})}}]}).encode()
    monkeypatch.setenv('MARKETING_MODEL','test');monkeypatch.setenv('MARKETING_BASE_URL','https://example.invalid');monkeypatch.setenv('MARKETING_API_KEY','fixture')
    monkeypatch.setattr(agent.urllib.request,'urlopen',lambda *a,**kw:Response())
    finding={'page':1,'verdict':'fix','issues':[{'severity':'high','detail':'裁切'}]}
    decision=agent.choose_repairs(tmp_path,{'pages':[{'layout':'image'}]},[finding],[],3)
    assert decision['actions']==[] and decision['decided_by']=='unavailable'


def fixture_plan(folder):
    plan={'pages':[{'title':'锁定标题','layout':'cover','items':[]}],'assets':{},'theme':{}}
    for name in ('plan.json','outline.json'):(folder/name).write_text(json.dumps(plan))
    return plan


def test_autonomous_review_director_repair_and_recheck(tmp_path,monkeypatch):
    fixture_plan(tmp_path);monkeypatch.setenv('MARKETING_VISION_ENABLED','true')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','5');monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    findings=iter([[{'page':1,'verdict':'fix','issues':[{'severity':'high','type':'hierarchy','detail':'拥挤','fix_hint':'删文字'}]}],
                   [{'page':1,'verdict':'pass','issues':[]}]])
    monkeypatch.setattr(vision,'review_batch',lambda *a:next(findings))
    decisions=[]
    def decide(folder,plan,findings,history,limit):
        decisions.append(findings)
        return {'actions':[{'tool':'repair_page','page':1,'brief':'保留文字，改为两列'}] if len(decisions)==1 else [],'reason':'fixture'}
    monkeypatch.setattr(agent,'choose_repairs',decide)
    def repair(folder,plan,finding,tools):
        assert finding['director_brief']=='保留文字，改为两列'
        plan['pages'][0]['custom']={'body':'fixture','css':''};return 1
    monkeypatch.setattr(vision,'repair_page',repair)
    monkeypatch.setattr(vision.PageTools,'cache_reviewed_page',lambda *a:None)
    renders=[]
    def render():renders.append(True);(tmp_path/'outline.json').write_bytes((tmp_path/'plan.json').read_bytes())
    report=vision.review_and_repair(tmp_path,render)
    assert report['status']=='passed' and len(renders)==1 and len(report['rounds'])==1
    assert json.loads((tmp_path/'plan.json').read_text())['pages'][0]['title']=='锁定标题'


def test_autonomous_failed_render_rolls_back_without_quality_pass(tmp_path,monkeypatch):
    original=fixture_plan(tmp_path);monkeypatch.setenv('MARKETING_VISION_ENABLED','true')
    monkeypatch.setattr(vision,'review_batch',lambda *a:[{'page':1,'verdict':'fix','issues':[{'severity':'high','type':'hierarchy','detail':'拥挤'}]}])
    decisions=iter([{'actions':[{'tool':'repair_page','page':1,'brief':'改布局'}]}, {'actions':[]}])
    monkeypatch.setattr(agent,'choose_repairs',lambda *a:next(decisions))
    def repair(folder,plan,*a):plan['pages'][0]['custom']={'body':'fixture','css':''};return 1
    monkeypatch.setattr(vision,'repair_page',repair)
    def render():
        if json.loads((tmp_path/'plan.json').read_text())['pages'][0].get('custom'):raise RuntimeError('fixture collision')
    report=vision.review_and_repair(tmp_path,render)
    assert report['status']=='needs_review' and report['repairs'][0]['status']=='rolled_back'
    assert json.loads((tmp_path/'plan.json').read_text())==original
