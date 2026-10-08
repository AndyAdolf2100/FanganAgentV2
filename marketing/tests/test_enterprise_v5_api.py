"""Public boundary: browser suggestions and honest delivery/retry state."""
import json
from fastapi.testclient import TestClient
from marketing_agent.api import create_app
from marketing_agent.runtime import DemoRuntime
from marketing_agent.enterprise import template_analysis
from test_enterprise import fixture, publish


def test_analysis_endpoint_is_read_only_and_shares_usage_ledger(tmp_path, monkeypatch):
    app=create_app(tmp_path,DemoRuntime())
    monkeypatch.setattr(app.state.presentations.pool,'submit',lambda *args:None)
    value=publish(app.state.presentations.templates,fixture())
    before=app.state.presentations.templates.read(value['id'])
    seen=[]
    def analyze(folder,template_id,payload,*,vision_cache):
        seen.append((folder,template_id,payload,vision_cache))
        return {'status':'analyzed','snapshotRevision':payload['snapshotRevision']}
    monkeypatch.setattr(template_analysis,'analyze_page',analyze)
    with TestClient(app) as client:
        path='/api/enterprise-templates/'+value['id']+'/analyze-page'
        assert client.post(path,json={'snapshotRevision':'b'*64}).json()['status']=='analyzed'
        assert seen[0][3]==app.state.presentations.root/'vision-cache'
        assert app.state.presentations.templates.read(value['id'])==before
        assert client.post(path,content='bad json').status_code==422
        assert client.post('/api/enterprise-templates/'+'f'*64+'/analyze-page',json={}).status_code==404
        assert len(seen)==1


def test_enterprise_v5_drafts_are_retryable_and_stale_pptx_is_not_served(tmp_path,monkeypatch):
    app=create_app(tmp_path,DemoRuntime())
    monkeypatch.setattr(app.state.presentations.pool,'submit',lambda *args:None)
    value=publish(app.state.presentations.templates,fixture())
    with TestClient(app) as client:
        run=client.post('/api/runs/import',json={'title':'回归文稿','manuscript':'# 回归文稿\n\n预算30万元。'}).json()
        url=f"/api/runs/{run['id']}/presentation"
        body={'template_id':value['id'],'template_revision':1}
        job=client.post(url,json=body).json()
        assert job['enterprise_workflow_version']==5
        app.state.presentations.update(job['id'],status='completed',stage='needs_review',quality_status='needs_review',visual_status='needs_review',artifacts_available=['presentation.html'])
        folder=app.state.presentations.root/job['id']
        (folder/'presentation.pptx').write_text('stale artifact')
        assert client.get(f"/api/presentations/{job['id']}/files/presentation.pptx").status_code==404
        assert client.post(f"/api/presentations/{job['id']}/resume-optimization").status_code==202
        app.state.presentations.update(job['id'],status='completed',quality_status='needs_review')
        assert client.post(url,json=body).json()['id']!=job['id']


def test_enterprise_outline_is_previewed_and_confirmed_once(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_RUNTIME', 'deerflow')
    app = create_app(tmp_path, DemoRuntime())
    queued = []
    monkeypatch.setattr(app.state.presentations.pool, 'submit', lambda fn, job_id: queued.append(job_id))
    template = publish(app.state.presentations.templates, fixture())
    with TestClient(app) as client:
        run = client.post('/api/runs/import', json={'title': '规划测试', 'manuscript': '# 规划测试\n\n预算30万元。'}).json()
        url = f"/api/runs/{run['id']}/presentation"
        job = client.post(url, json={'template_id': template['id'], 'template_revision': 1}).json()
        assert job['outline_review_required'] is True
        assert client.post(url, json={'template_id': template['id'], 'template_revision': 1}).json()['id'] == job['id']
        folder = app.state.presentations.root / job['id']
        (folder / 'source-plan.json').write_text(json.dumps({
            'title': '规划测试', 'agenda': [{'id': 'a1', 'number': 1, 'text': '预算'}],
            'blocks': [{'id': 'b1', 'text': '预算30万元。'}],
            'planned': [{'role': 'cover', 'title': '规划测试', 'template_page': 0},
                        {'role': 'body', 'title': '预算', 'template_page': 3, 'block_ids': ['b1']}]}, ensure_ascii=False))
        app.state.presentations.update(job['id'], status='awaiting_outline_confirmation', stage='outline_review')
        outline = client.get(f"/api/presentations/{job['id']}/outline").json()
        assert outline['planned_page_count'] == 2
        assert outline['pages'][1]['source_preview'] == '预算30万元。'
        confirmed = client.post(f"/api/presentations/{job['id']}/confirm-outline")
        assert confirmed.status_code == 202
        assert confirmed.json()['outline_approval']['page_count'] == 2
        assert confirmed.json()['status'] == 'queued'
        assert queued == [job['id'], job['id']]
        assert client.post(f"/api/presentations/{job['id']}/confirm-outline").status_code == 409
