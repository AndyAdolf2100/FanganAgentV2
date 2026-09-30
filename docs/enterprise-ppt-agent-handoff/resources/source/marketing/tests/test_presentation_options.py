from copy import deepcopy
from fastapi.testclient import TestClient
import pytest
from marketing_agent.api import create_app
from marketing_agent.runtime import DemoRuntime
from marketing_agent.presentation import plan_outline
from marketing_agent.presentation_design import cache_key, validate_design
from marketing_agent.presentation_options import PresentationOptions, check_page_count

TEXT = '# 产品方案\n\n## 品牌\n介绍产品价值。\n\n## 传播\n通过内容触达用户。'


def test_options_reach_job_and_isolate_cache(tmp_path, monkeypatch):
    app = create_app(tmp_path, DemoRuntime())
    monkeypatch.setattr(app.state.presentations.pool, 'submit', lambda *args: None)
    with TestClient(app) as client:
        run = client.post('/api/runs/import', json={'title': '产品方案', 'manuscript': TEXT}).json()
        url = f"/api/runs/{run['id']}/presentation"
        options = {'design_prompt': '深色高端网格', 'page_prompt': '包含封面、正文、结尾',
                   'palette': ['#123456', '#ffffff'], 'page_count_min': 3, 'page_count_max': 5}
        first = client.post(url, json=options)
        assert first.status_code == 202
        job = first.json()
        assert job['options'] == options
        assert (app.state.presentations.root / job['id'] / 'manuscript.md').read_text() == TEXT
        assert client.post(url, json=options).json()['id'] == job['id']
        changed = {**options, 'design_prompt': '浅色网格'}
        assert client.post(url, json=changed).status_code == 409
        app.state.presentations.update(job['id'], status='completed')
        assert client.post(url, json=options).json()['id'] == job['id']
        assert client.post(url, json=changed).json()['id'] != job['id']
        assert client.post(url, json={'aspect_ratio': '4:3'}).status_code == 422
        assert client.post(url, json={'page_count_min': 46, 'page_count_max': 60}).status_code == 422
        assert client.post(url, json={'unknown_requirement': True}).status_code == 422


def test_requirements_change_plan_cache_and_apply_palette():
    source = plan_outline(TEXT)
    old = cache_key(source)
    source['presentation_options'] = {'palette': ['#123456', '#ffffff'], 'page_count_min': 3, 'page_count_max': 3}
    assert cache_key(source) != old
    pages = [{'layout': kind, 'title': '产品价值', 'items': [], 'source_ids': ['b0000']} for kind in ['cover', 'statement', 'closing']]
    result = validate_design({'pages': deepcopy(pages)}, source)
    assert result['visual_dna']['background'] == 'ffffff'
    assert result['visual_dna']['accent'] == '123456'
    assert result['visual_dna']['text'] == '141414'
    with pytest.raises(ValueError, match='不符合用户要求'):
        validate_design({'pages': pages + [deepcopy(pages[-1])]}, source)
    with pytest.raises(ValueError):
        check_page_count(4, source['presentation_options'])


@pytest.mark.parametrize('options', [
    {'page_count_min': 10}, {'page_count_min': 20, 'page_count_max': 10},
    {'page_count_min': 3.5, 'page_count_max': 5}, {'palette': ['red']}, {'palette': ['#fff']}
])
def test_invalid_options(options):
    with pytest.raises(ValueError):
        PresentationOptions(**options)
