from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from marketing_agent.api import create_app
from marketing_agent.manuscripts import extract_manuscript
from marketing_agent.presentation import plan_outline
from marketing_agent.presentation_design import cache_key, validate_design
from marketing_agent.presentation_styles import get_style
from marketing_agent.runtime import DemoRuntime

TEXT = '# 新品传播方案\n\n## 策略\n以办公室场景传递低糖主张。\n\n## 预算\n总预算300万元。'


def docx(xml):
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return data.getvalue()


def test_docx_preserves_paragraph_order_headings_and_table():
    data = docx('''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>新品策略</w:t></w:r></w:p>
      <w:p><w:r><w:t>分段</w:t></w:r><w:r><w:t>正文</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>预算</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>A|B</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>60–120万元</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      <w:p><w:r><w:t>最后一段</w:t></w:r></w:p></w:body></w:document>''')
    result = extract_manuscript('策略.docx', data)
    assert result['title'] == '新品策略'
    assert result['text'].startswith('# 新品策略\n\n分段正文')
    assert result['text'].endswith('最后一段')
    blocks = plan_outline(result['text'])['source_blocks']
    assert blocks[1]['rows'] == [['A|B', '60–120万元']]
    assert result['notice']


@pytest.mark.parametrize('name,data', [('坏文件.docx', b'not zip'), ('empty.txt', b'  '), ('x.pdf', b'%PDF'),
    ('title.md', '# 只有标题'.encode()), ('x.txt', b'abc\x00def'),
    ('x.docx', docx('<!DOCTYPE x [<!ENTITY text "bad">]><x>&text;</x>'))])
def test_invalid_manuscripts_are_rejected(name, data):
    with pytest.raises(ValueError): extract_manuscript(name, data)


def test_import_skips_engine_and_style_is_persisted_and_used(tmp_path, monkeypatch):
    app = create_app(tmp_path, DemoRuntime())
    calls = []
    monkeypatch.setattr(app.state.engine, 'claim', lambda *_: pytest.fail('导入不能启动营销策划'))
    monkeypatch.setattr(app.state.presentations.pool, 'submit', lambda *args: calls.append(args))
    with TestClient(app) as client:
        parsed = client.post('/api/manuscripts/parse?filename=plan.md', content=TEXT.encode(), headers={'Content-Type': 'application/octet-stream'})
        assert parsed.status_code == 200 and parsed.json()['text'] == TEXT
        response = client.post('/api/runs/import', json={'title': '新品方案', 'manuscript': parsed.json()['text'], 'style_id': 'business'})
        assert response.status_code == 201
        run = response.json()
        assert run['status'] == 'completed' and run['outputs'] == {'assembly': TEXT}
        assert client.get('/api/runs/'+run['id']).json()['presentation_style'] == 'business'
        assert client.get('/api/runs/'+run['id']+'/export').text == TEXT
        assert client.post('/api/runs/'+run['id']+'/retry').status_code == 409
        assert client.post('/api/runs/'+run['id']+'/feedback', json={'action':'revise','target':'assembly','text':'更改'}).status_code == 409
        url = '/api/runs/'+run['id']+'/presentation'
        first = client.post(url).json()
        assert first['style_id'] == 'business'
        assert client.post(url).json()['id'] == first['id']
        assert client.post(url, json={'style_id':'editorial'}).status_code == 409
        app.state.presentations.update(first['id'], status='completed')
        assert client.post(url).json()['id'] == first['id']
        second = client.post(url, json={'style_id':'editorial'}).json()
        assert second['id'] != first['id'] and second['style_id'] == 'editorial'
        assert len(calls) == 2
        assert (app.state.presentations.root/second['id']/'manuscript.md').read_text() == TEXT
        assert client.post(url, json={'style_id':'invented'}).status_code == 422


def test_style_applies_to_planner_output_and_isolates_cache():
    source = plan_outline(TEXT)
    initial = cache_key(source)
    source['style_id'] = 'business'
    business = cache_key(source)
    assert initial != business
    pages = [{'layout': kind, 'title': '新品策略', 'items': [], 'source_ids': ['b0000']} for kind in ['cover','statement','closing']]
    data = validate_design({'pages': deepcopy(pages), 'visual_dna': {'background':'000000'}}, source)
    assert data['visual_dna']['background'] == get_style('business')['theme']['background']
    assert data['visual_dna']['style_id'] == 'business'
    source['style_id'] = 'editorial'
    assert cache_key(source) not in {initial, business}


def test_file_and_import_limits_do_not_create_projects(tmp_path, monkeypatch):
    from marketing_agent import api
    app = create_app(tmp_path, DemoRuntime())
    monkeypatch.setattr(api, 'MAX_FILE_BYTES', 20)
    with TestClient(app) as client:
        assert client.post('/api/manuscripts/parse?filename=x.md', content=b'x'*21).status_code == 413
        for manuscript, style in [('# 只有标题', 'auto'), ('正文', 'bad'), ('x'*100001, 'auto')]:
            assert client.post('/api/runs/import', json={'title':'测试','manuscript':manuscript,'style_id':style}).status_code == 422
        assert client.get('/api/runs').json() == []
        styles = client.get('/api/presentation-styles').json()
        assert {s['id'] for s in styles} == {'auto','natural','business','editorial','brand_launch'}
