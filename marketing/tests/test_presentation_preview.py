import json

import pytest
from fastapi.testclient import TestClient

from marketing_agent.presentation_preview import create_app

JOB = 'a' * 32


@pytest.fixture
def preview(tmp_path):
    folder = tmp_path / 'jobs' / JOB
    folder.mkdir(parents=True)
    job = {'status': 'running', 'stage': 'page_generating', 'options': {'template_id': 'template'}}
    (folder / 'job.json').write_text(json.dumps(job))
    plan = {'canvas': {'width': 1200, 'height': 675}, 'pages': [
        {'title': '封面', 'model_html': True, 'html': '<html>cover</html>'},
        {'title': '正文', 'model_html': True, 'html': '<html>body</html>'},
        {'title': '未完成'}]}
    (folder / 'plan.json').write_text(json.dumps(plan))
    rendered = []
    def render(draft, destination):
        rendered.append(draft)
        destination.write_bytes(b'png-fixture')
    app = create_app(tmp_path / 'jobs', tmp_path / 'cache', render)
    return TestClient(app), folder, rendered


def test_only_complete_html_available(preview):
    client, folder, _ = preview
    result = client.get(f'/api/presentation-drafts/{JOB}')
    assert result.headers['cache-control'] == 'no-store'
    data = result.json()
    assert data['draft'] is True
    assert data['available_pages'] == 2
    assert [p['number'] for p in data['pages']] == [1, 2]
    assert 'html' not in data['pages'][0]
    assert client.get(f'/api/presentation-drafts/{JOB}/previews/3').status_code == 404
    assert client.get('/api/presentation-drafts/invalid').status_code == 404
    assert client.get('/api/presentation-drafts/' + 'b' * 32).status_code == 404


def test_cached_render_is_read_only_and_updates(preview):
    client, folder, rendered = preview
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    url = f'/api/presentation-drafts/{JOB}/previews/1'
    assert client.get(url).content == b'png-fixture'
    assert client.get(url).status_code == 200
    assert len(rendered) == 1
    assert rendered[0]['pages'][0]['model_html'] is False
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    plan = json.loads((folder / 'plan.json').read_text())
    plan['pages'][0]['html'] = '<html>updated</html>'
    (folder / 'plan.json').write_text(json.dumps(plan))
    assert client.get(url).status_code == 200
    assert len(rendered) == 2
    assert rendered[-1]['pages'][0]['html'] == '<html>updated</html>'


def test_partial_write_retains_last_complete_snapshot(preview):
    client, folder, _ = preview
    url = f'/api/presentation-drafts/{JOB}'
    first = client.get(url).json()
    plan = (folder / 'plan.json').read_text()
    (folder / 'plan.json').write_text('{"pages":[')
    assert client.get(url).json()['pages'] == first['pages']
    updated = json.loads(plan)
    updated['pages'][2].update(model_html=True, html='<html>new</html>')
    (folder / 'plan.json').write_text(json.dumps(updated))
    assert client.get(url).json()['available_pages'] == 3


def test_no_plan_and_normal_workflow(preview):
    client, folder, _ = preview
    (folder / 'plan.json').unlink()
    assert client.get(f'/api/presentation-drafts/{JOB}').json()['available_pages'] == 0
    (folder / 'job.json').write_text(json.dumps({'status': 'running', 'options': {}}))
    assert client.get(f'/api/presentation-drafts/{JOB}').json()['available_pages'] == 0


def test_render_failure_never_changes_job_and_can_retry(preview):
    client, folder, _ = preview
    before = (folder / 'job.json').read_bytes()
    renderer = client.app.state.previews.render
    def broken(*args):
        raise RuntimeError('renderer failed')
    client.app.state.previews.render = broken
    url = f'/api/presentation-drafts/{JOB}/previews/1'
    assert client.get(url).status_code == 503
    assert (folder / 'job.json').read_bytes() == before
    client.app.state.previews.render = renderer
    assert client.get(url).status_code == 200
