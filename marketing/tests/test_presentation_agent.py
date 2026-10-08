import json
import threading
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


def test_normal_workflow_reviews_and_rechecks_each_repaired_page_separately(tmp_path,monkeypatch):
    plan=fixture_plan(tmp_path)
    plan['pages'].append({'title':'第二页','layout':'statement','items':[]})
    for name in ('plan.json','outline.json'):(tmp_path/name).write_text(json.dumps(plan))
    monkeypatch.setenv('MARKETING_VISION_ENABLED','true')
    monkeypatch.setenv('MARKETING_PPT_BUDGET_RMB','5')
    monkeypatch.setenv('MARKETING_VISION_MAX_REQUESTS','16')
    calls=[]
    def review(folder,plan,indexes):
        assert len(indexes)==1
        first=indexes not in calls;calls.append(list(indexes))
        return [{'page':indexes[0],'verdict':'fix' if first else 'pass',
                 'issues':[{'severity':'medium','type':'hierarchy','detail':'层级'}] if first else []}]
    monkeypatch.setattr(vision,'review_batch',review)
    monkeypatch.setattr(agent,'choose_repairs',lambda folder,plan,findings,history,limit:
        {'actions':[{'page':i,'brief':'调整布局'} for i in (1,2)] if not history else []})
    monkeypatch.setattr(vision,'repair_page',lambda folder,plan,finding,tools:finding['page'])
    monkeypatch.setattr(vision.PageTools,'cache_reviewed_page',lambda *args:None)
    def render():(tmp_path/'outline.json').write_bytes((tmp_path/'plan.json').read_bytes())
    result=vision.review_and_repair(tmp_path,render)
    assert result['status']=='passed' and sorted(calls)==[[1],[1],[2],[2]]


def test_normal_initial_screenshot_reviews_run_concurrently(tmp_path,monkeypatch):
    from marketing_agent import presentation_budget
    plan=fixture_plan(tmp_path)
    plan['pages'].append({'title':'第二页','layout':'statement','items':[]})
    for name in ('plan.json','outline.json'):(tmp_path/name).write_text(json.dumps(plan))
    monkeypatch.setenv('MARKETING_VISION_ENABLED','true')
    monkeypatch.setenv('MARKETING_VISION_WORKERS','2')
    monkeypatch.setattr(presentation_budget,'can_reserve',lambda *args:False)
    barrier=threading.Barrier(2)
    def review(folder,plan,indexes):
        assert len(indexes)==1
        barrier.wait(timeout=3)
        return [{'page':indexes[0],'verdict':'pass','issues':[]}]
    monkeypatch.setattr(vision,'review_batch',review)
    progress=[]
    report=vision.review_and_repair(tmp_path,lambda:None,lambda done,total:progress.append((done,total)))
    assert report['status']=='passed'
    assert [page['page'] for page in report['pages']]==[1,2]
    assert progress==[(1,2),(2,2)]


def test_concurrent_tool_calls_keep_run_state_consistent(tmp_path):
    run = agent.PresentationAgent(tmp_path)
    gate = threading.Event()

    def tool(index):
        gate.wait(timeout=10)
        if index == 3:
            raise ValueError('模拟一次工具失败')
        return index

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run.call, f'tool-{index}', tool, index) for index in range(8)]
        gate.set()
        for index, future in enumerate(futures):
            if index == 3:
                with pytest.raises(ValueError, match='模拟一次工具失败'):
                    future.result(timeout=10)
            else:
                assert future.result(timeout=10) == index
    state = json.loads((tmp_path / 'agent-run.json').read_text())
    assert len(state['tools']) == 8
    assert sorted(event['tool'] for event in state['tools']) == [f'tool-{index}' for index in range(8)]
    assert [event['status'] for event in state['tools']].count('failed') == 1
    assert all(event['status'] in {'completed', 'failed'} for event in state['tools'])
