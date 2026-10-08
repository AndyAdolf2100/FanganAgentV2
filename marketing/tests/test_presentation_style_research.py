import io
import json
import sys
from datetime import datetime
from types import SimpleNamespace

from marketing_agent import presentation_style_research as research
from marketing_agent.presentation import PresentationJobs, STYLE_RESEARCH_TTL_SECONDS
from marketing_agent.store import Store


class Search:
    def __init__(self, timeout):
        assert timeout == 15

    def images(self, query, **kwargs):
        assert str(datetime.now().year) in query and 'marketing presentation' in query
        return [
            {'title': 'Presentation Design One', 'url': 'https://www.behance.net/gallery/1/deck',
             'thumbnail': 'https://mir-s3-cdn-cf.behance.net/project1.jpg'},
            {'title': 'Pitch Deck Design Two', 'url': 'https://www.behance.net/gallery/2/deck',
             'thumbnail': 'https://mir-s3-cdn-cf.behance.net/project2.jpg'},
            {'title': 'Unsafe Deck', 'url': 'https://127.0.0.1/private',
             'thumbnail': 'https://127.0.0.1/image.jpg'},
        ]

    def text(self, query, **kwargs):
        assert 'site:behance.net/gallery' in query
        return [{'title': 'Marketing Presentation', 'href': 'https://www.behance.net/gallery/3/deck',
                 'body': 'High contrast typography and generous whitespace.'}]


def test_online_references_feed_real_images_to_vision_and_keep_sources(tmp_path, monkeypatch):
    folder = tmp_path / 'job'
    folder.mkdir()
    monkeypatch.setitem(sys.modules, 'ddgs', SimpleNamespace(DDGS=Search))
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'true')
    monkeypatch.setenv('MARKETING_VISION_MODEL', 'doubao-seed-2-0-mini-260428')
    monkeypatch.setenv('MARKETING_IMAGE_BASE_URL', 'https://vision.example/v1')
    monkeypatch.setenv('MARKETING_IMAGE_API_KEY', 'fixture')
    monkeypatch.setattr(research, 'reserve_review', lambda *args: None)
    answer = {field: f'{field}：观察到具体的设计特点' for field in research.ANALYSIS_FIELDS}
    requests = []

    def urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(answer)}}]}).encode())

    monkeypatch.setattr(research.urllib.request, 'urlopen', urlopen)
    result = research.research_online_style(folder, {'title': '测试品牌', 'style_id': 'auto'})
    assert result['status'] == 'analyzed'
    assert len(result['references']) == 3
    assert result['analysis'] == answer
    assert len([part for part in requests[0]['messages'][1]['content'] if part['type'] == 'image_url']) == 2
    assert json.loads((folder / 'online-style-research.json').read_text()) == result


def test_online_search_falls_back_without_vision_or_network(tmp_path, monkeypatch):
    folder = tmp_path / 'job'
    folder.mkdir()
    monkeypatch.setitem(sys.modules, 'ddgs', SimpleNamespace(DDGS=Search))
    monkeypatch.setenv('MARKETING_VISION_ENABLED', 'false')
    result = research.research_online_style(folder, {'title': '测试品牌', 'style_id': 'business'})
    assert result['status'] == 'search_only'
    assert result['analysis'] is None
    assert result['references'][0]['summary'].startswith('High contrast')

    class BrokenSearch(Search):
        def images(self, *args, **kwargs):
            raise RuntimeError('network down')

        def text(self, *args, **kwargs):
            raise RuntimeError('network down')

    monkeypatch.setitem(sys.modules, 'ddgs', SimpleNamespace(DDGS=BrokenSearch))
    result = research.research_online_style(folder, {'title': '测试品牌', 'style_id': 'auto'})
    assert result['status'] == 'no_results'
    assert result['references'] == []


def test_real_general_jobs_refresh_references_after_seven_days(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_RUNTIME', 'real')
    jobs = PresentationJobs(tmp_path, Store(tmp_path / 'db.sqlite'))
    monkeypatch.setattr(jobs.pool, 'submit', lambda *args: None)
    run = {'id': 'a' * 32, 'status': 'completed', 'runtime': 'real',
           'outputs': {'assembly': '# 测试品牌\n\n营销提案正文。'}}
    first = jobs.create(run)
    first['status'] = 'completed'
    first['style_research_status'] = 'search_only'
    jobs._write(jobs.root / first['id'] / 'job.json', first)
    assert jobs.create(run)['id'] == first['id']
    first['created'] -= STYLE_RESEARCH_TTL_SECONDS + 1
    jobs._write(jobs.root / first['id'] / 'job.json', first)
    assert jobs.create(run)['id'] != first['id']
