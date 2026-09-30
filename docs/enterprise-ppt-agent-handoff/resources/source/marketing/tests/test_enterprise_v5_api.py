"""Public boundary: browser suggestions and honest delivery/retry state."""
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
