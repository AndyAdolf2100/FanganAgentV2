import json

from fastapi.testclient import TestClient

from marketing_agent.api import create_app
from marketing_agent.runtime import DemoRuntime
from marketing_agent import (presentation, presentation_agent, presentation_design,
                             presentation_editorial, presentation_images, presentation_style_research)


MANUSCRIPT = '# 品牌方案\n\n## 目标\n提升产品认知。\n\n## 执行\n按阶段开展传播。'


def test_general_outline_pauses_then_resumes_same_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_RUNTIME', 'demo')
    monkeypatch.setenv('MARKETING_IMAGE_ENABLED', 'false')
    app = create_app(tmp_path, DemoRuntime())
    jobs = app.state.presentations
    queued = []
    monkeypatch.setattr(jobs.pool, 'submit', lambda fn, job_id: queued.append(job_id))
    original = presentation.plan_outline
    planned = []

    def count_planning(source):
        planned.append(source)
        return original(source)

    monkeypatch.setattr(presentation, 'plan_outline', count_planning)
    with TestClient(app) as client:
        run = client.post('/api/runs/import', json={'title': '品牌方案', 'manuscript': MANUSCRIPT}).json()
        job = client.post(f"/api/runs/{run['id']}/presentation").json()
        assert job['outline_review_required'] is True
        jobs.execute(job['id'])
        assert jobs.get(job['id'])['status'] == 'awaiting_outline_confirmation'
        outline = client.get(f"/api/presentations/{job['id']}/outline").json()
        assert outline['mode'] == 'general'
        assert outline['planned_page_count'] == len(outline['pages'])
        assert any(page['source_preview'] for page in outline['pages'])
        assert not (jobs.root / job['id'] / 'presentation.pptx').exists()
        confirmed = client.post(f"/api/presentations/{job['id']}/confirm-outline")
        assert confirmed.status_code == 202
        assert confirmed.json()['status'] == 'queued'
        assert queued == [job['id'], job['id']]
        assert client.post(f"/api/presentations/{job['id']}/confirm-outline").status_code == 409

        def stop_at_images(*args):
            raise RuntimeError('resume reached images')

        monkeypatch.setattr(presentation_images, 'materialize_assets', stop_at_images)
        jobs.execute(job['id'])
        assert len(planned) == 1
        assert jobs.get(job['id'])['error'] == 'resume reached images'


def test_confirmed_general_outline_rejects_changed_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_RUNTIME', 'demo')
    monkeypatch.setenv('MARKETING_IMAGE_ENABLED', 'false')
    app = create_app(tmp_path, DemoRuntime())
    jobs = app.state.presentations
    monkeypatch.setattr(jobs.pool, 'submit', lambda *args: None)
    with TestClient(app) as client:
        run = client.post('/api/runs/import', json={'title': '品牌方案', 'manuscript': MANUSCRIPT}).json()
        job = client.post(f"/api/runs/{run['id']}/presentation").json()
        jobs.execute(job['id'])
        assert client.post(f"/api/presentations/{job['id']}/confirm-outline").status_code == 202
        path = jobs.root / job['id'] / 'normal-outline-plan.json'
        saved = json.loads(path.read_text())
        saved['plan']['pages'][0]['title'] = '未经确认的改动'
        path.write_text(json.dumps(saved, ensure_ascii=False))
        jobs.execute(job['id'])
        assert '大纲或文稿发生变化' in jobs.get(job['id'])['error']


def test_real_general_outline_shows_model_pages_without_replanning(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_RUNTIME', 'deerflow')
    monkeypatch.setenv('MARKETING_IMAGE_ENABLED', 'false')
    monkeypatch.setattr(presentation_agent, 'analyze_reference', lambda *args: {'status': 'not_provided'})
    monkeypatch.setattr(presentation_style_research, 'research_online_style',
                        lambda *args: {'status': 'no_results', 'references': [], 'analysis': None})
    planned = []

    def design(plan, folder):
        planned.append(True)
        return {'pages': [{'title': '品牌主张', 'layout': 'cover', 'source_ids': ['b0000'],
                           'layout_brief': '左侧大标题，右侧留白承载主视觉'},
                          {'title': '分阶段传播', 'layout': 'steps', 'source_ids': ['b0001'],
                           'layout_brief': '按时间从左到右排列三个阶段'}],
                'visual_dna': plan['theme'], 'note_only_source_ids': [], 'corrections': []}

    monkeypatch.setattr(presentation_design, 'plan_design', design)
    monkeypatch.setattr(presentation_editorial, 'refine_design', lambda result, *args: result)
    app = create_app(tmp_path, DemoRuntime())
    jobs = app.state.presentations
    monkeypatch.setattr(jobs.pool, 'submit', lambda *args: None)
    with TestClient(app) as client:
        run = client.post('/api/runs/import', json={'title': '品牌方案', 'manuscript': MANUSCRIPT}).json()
        job = client.post(f"/api/runs/{run['id']}/presentation").json()
        jobs.execute(job['id'])
        outline = client.get(f"/api/presentations/{job['id']}/outline").json()
        assert [page['title'] for page in outline['pages']] == ['品牌主张', '分阶段传播']
        assert outline['pages'][0]['design_intent']['focus'].startswith('左侧大标题')
        assert outline['pages'][1]['source_preview'] == '按阶段开展传播。'
        assert client.post(f"/api/presentations/{job['id']}/confirm-outline").status_code == 202
        monkeypatch.setattr(presentation_images, 'materialize_assets',
                            lambda *args: (_ for _ in ()).throw(RuntimeError('resume reached images')))
        jobs.execute(job['id'])
        assert planned == [True]
        assert jobs.get(job['id'])['error'] == 'resume reached images'


def test_old_completed_general_ppt_exposes_read_only_actual_outline(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_RUNTIME', 'demo')
    app = create_app(tmp_path, DemoRuntime())
    jobs = app.state.presentations
    monkeypatch.setattr(jobs.pool, 'submit', lambda *args: None)
    with TestClient(app) as client:
        run = client.post('/api/runs/import', json={'title': '品牌方案', 'manuscript': MANUSCRIPT}).json()
        job = client.post(f"/api/runs/{run['id']}/presentation").json()
        folder = jobs.root / job['id']
        actual = presentation.plan_outline(MANUSCRIPT)
        actual['pages'].append(dict(actual['pages'][-1], title='执行续页'))
        (folder / 'outline.json').write_text(json.dumps(actual, ensure_ascii=False))
        jobs.update(job['id'], status='completed', stage='completed', page_count=len(actual['pages']),
                    outline_review_required=False)
        current = client.get(f"/api/runs/{run['id']}/presentation").json()
        assert current['outline_available'] is True
        outline = client.get(f"/api/presentations/{job['id']}/outline").json()
        assert outline['mode'] == 'general'
        assert outline['legacy'] is True
        assert outline['actual_page_count'] == len(actual['pages'])
        assert [page['title'] for page in outline['pages']] == [page['title'] for page in actual['pages']]
        assert outline['pages'][0]['source_preview']
