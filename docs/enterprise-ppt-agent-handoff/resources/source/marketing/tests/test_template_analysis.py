from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from marketing_agent.enterprise import template_analysis as analysis


def snapshot():
    return {'pageIndex': 0, 'totalPages': 1, 'width': 1200, 'height': 675,
            'snapshotRevision': 'b' * 64, 'html': '<html><body><p>市场分析</p></body></html>',
            'page': {'id': 'page-1', 'role': 'body', 'name': '正文', 'labelSource': 'rule',
                     'elements': [{'id': 'title', 'kind': 'text', 'x': 50, 'y': 40, 'width': 1000, 'height': 80,
                                   'textRole': 'title', 'order': 1, 'paragraphs': [{'runs': [{'text': '市场分析', 'size': 30}]}]},
                                  {'id': 'logo', 'kind': 'shape', 'x': 10, 'y': 10, 'width': 60, 'height': 60,
                                   'vector': {'paths': ['M0 0L10 10']}, 'labelSource': 'manual',
                                   'artworkType': 'outlinedText', 'artworkTextRole': 'brand', 'binding': 'fixed', 'fixed': True}]}}


def visual():
    return {'observed': '上方标题，下方正文', 'hierarchy': 'title为页面标题', 'groups': '从上到下',
            'fixed': 'logo是品牌标识', 'uncertainties': ''}


def suggestion():
    return {'role': 'body', 'layoutKind': 'text', 'confidence': .93, 'reason': '正文布局',
            'elements': [{'id': 'title', 'textRole': 'title', 'order': 1, 'confidence': .95, 'reason': '上方大字标题'},
                         {'id': 'logo', 'artworkTextRole': 'brand', 'order': 1, 'confidence': .91, 'reason': '左上品牌'}]}


def test_single_image_then_text_only_and_cache_preserves_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKETING_VISION_MODEL', 'doubao-seed-2-1-pro-260915')
    monkeypatch.setenv('MARKETING_MODEL', 'glm-5')
    value = snapshot(); original = deepcopy(value)
    calls = []
    vision_cache = tmp_path / 'shared-presentations' / 'vision-cache'

    def render(folder, payload):
        calls.append('render'); assert payload == value
        return folder / 'snapshot.png'

    def seed(folder, image, brief, *, vision_cache):
        calls.append('seed'); assert image.name == 'snapshot.png'
        assert vision_cache == tmp_path / 'shared-presentations' / 'vision-cache'
        assert len(brief['elements']) == 2
        return visual()

    def glm(folder, brief):
        calls.append('glm'); assert brief['visualEvidence'] == visual()
        assert 'html' not in brief and 'image_url' not in json.dumps(brief)
        assert 'paths' not in json.dumps(brief)
        return {**suggestion(), 'html': 'discard this unrequested model output'}

    options = dict(vision_cache=vision_cache, render=render, vision_call=seed, text_call=glm)
    result = analysis.analyze_page(tmp_path / 'analysis', 'a' * 64, value, **options)
    assert calls == ['render', 'seed', 'glm']
    assert result['snapshotRevision'] == value['snapshotRevision']
    assert 'html' not in result['suggestion']
    assert value == original
    assert analysis.analyze_page(tmp_path / 'analysis', 'a' * 64, value, **options)['cacheHit']
    assert len(calls) == 3
    value['snapshotRevision'] = 'c' * 64
    assert not analysis.analyze_page(tmp_path / 'analysis', 'a' * 64, value, **options)['cacheHit']
    assert len(calls) == 6


def test_text_failure_can_retry_without_a_second_paid_visual_call(tmp_path):
    calls = []

    def seed(*args, **kwargs):
        calls.append('seed'); return visual()

    def fail(*args):
        raise ValueError('GLM temporarily unavailable')

    options = dict(vision_cache=tmp_path / 'vision-cache', render=lambda folder, value: folder / 'snapshot.png', vision_call=seed)
    with pytest.raises(ValueError):
        analysis.analyze_page(tmp_path, 'a' * 64, snapshot(), text_call=fail, **options)
    result = analysis.analyze_page(tmp_path, 'a' * 64, snapshot(), text_call=lambda *args: suggestion(), **options)
    assert result['status'] == 'analyzed' and calls == ['seed']


@pytest.mark.parametrize('change', [
    lambda s: s.update(pageIndex=1), lambda s: s.update(width=float('nan')),
    lambda s: s.update(snapshotRevision='../snapshot'),
    lambda s: s['page']['elements'].append(deepcopy(s['page']['elements'][0])),
])
def test_invalid_snapshot_is_rejected_before_models(tmp_path, change):
    value = snapshot(); change(value)
    with pytest.raises(ValueError):
        analysis.analyze_page(tmp_path, 'a' * 64, value, vision_cache=tmp_path / 'vision-cache',
                              render=lambda *args: pytest.fail('invalid snapshot must not render'))
    with pytest.raises(ValueError):
        analysis.validate_snapshot('../outside', snapshot())


@pytest.mark.parametrize('change', [
    lambda s: s['elements'][0].update(id='invented'),
    lambda s: s['elements'].append(deepcopy(s['elements'][0])),
    lambda s: s['elements'][0].update(confidence=float('nan')),
    lambda s: s['elements'][0].update(order=True),
    lambda s: s['elements'][0].update(artworkTextRole='brand'),
    lambda s: s.update(role='generated-new-page'),
])
def test_untrusted_model_output_must_match_existing_ids_and_types(change):
    value = suggestion(); change(value)
    with pytest.raises(ValueError):
        analysis.validate_suggestion(value, snapshot()['page'])


def test_render_disables_active_content_and_all_external_requests(tmp_path, monkeypatch):
    value = snapshot()
    value['html'] = '''<html><head><meta http-equiv="refresh" content="0;url=http://localhost/secret"><script>fetch('/secret')</script></head>
    <body onload="fetch('/secret')"><iframe src="file:///etc/passwd"></iframe><img src="http://localhost/secret" onerror="alert(1)"><p>标题</p></body></html>'''

    def run(args, **kwargs):
        document = (tmp_path / 'snapshot.html').read_text()
        assert '<script' not in document and '<iframe' not in document
        assert 'onerror=' not in document and 'onload=' not in document
        assert 'refresh' not in document
        assert "img-src data:" in document and "default-src 'none'" in document
        assert 'javaScriptEnabled:false' in args[3]
        assert "route.abort()" in args[3] and "startsWith('data:')" in args[3]
        (tmp_path / 'snapshot.png').write_bytes(b'fixture')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(analysis.subprocess, 'run', run)
    assert analysis.render_snapshot(tmp_path, value).is_file()


def test_frontend_applies_labels_only_and_preserves_manual_locks(tmp_path):
    frontend = Path(__file__).resolve().parents[1] / 'frontend'
    esbuild = frontend / 'node_modules' / '.bin' / 'esbuild'
    if not esbuild.exists():
        pytest.skip('frontend dependencies are not installed')
    bundle = tmp_path / 'analysis.mjs'
    subprocess.run([str(esbuild), str(frontend / 'enterprise' / 'analysis.ts'), '--bundle', '--platform=node',
                    '--format=esm', f'--outfile={bundle}'], check=True, capture_output=True)
    page = snapshot()['page']; page['labelSource'] = 'manual'
    expected = suggestion(); expected['role'] = 'cover'; expected['elements'][1]['artworkTextRole'] = 'title'
    script = f'''
import assert from 'node:assert/strict';
import {{analysisRows, defaultAnalysisSelection, applyAnalysis}} from {json.dumps(bundle.as_uri())};
const page = {json.dumps(page, ensure_ascii=False)};
const original = structuredClone(page);
const result = {{pageId:page.id,suggestion:{json.dumps(expected, ensure_ascii=False)}}};
const rows = analysisRows(page,result);
assert(rows.find(r=>r.key==='page').conflict);
assert(rows.find(r=>r.key==='logo').conflict);
assert.deepEqual(defaultAnalysisSelection(page,result),['title']);
assert.equal(applyAnalysis(page,result,['page','title','logo']),1);
assert.equal(page.role,'body');
assert.equal(page.elements[0].labelSource,'model');
assert.equal(page.elements[0].confidence,.95);
assert.deepEqual(page.elements[0].paragraphs,original.elements[0].paragraphs);
assert.equal(page.elements[0].x,original.elements[0].x);
assert.deepEqual(page.elements[1],original.elements[1]);
result.suggestion.elements[0].confidence=.4;
assert.deepEqual(defaultAnalysisSelection(original,result),[]);
assert.throws(()=>applyAnalysis({{...page,id:'different'}},result,['title']));
'''
    subprocess.run(['node', '--input-type=module', '-e', script], check=True, capture_output=True, text=True)


def test_missing_glm_connection_prevents_visual_spend(tmp_path, monkeypatch):
    monkeypatch.delenv('MARKETING_API_KEY', raising=False)
    monkeypatch.setenv('MARKETING_MODEL', 'glm-5')
    with pytest.raises(ValueError, match='GLM文字模型连接未配置'):
        analysis.analyze_page(tmp_path, 'a' * 64, snapshot(), vision_cache=tmp_path / 'vision-cache',
                              vision_call=lambda *args, **kwargs: pytest.fail('must not spend on Seed before GLM is configured'),
                              render=lambda *args: pytest.fail('must preflight before rendering'))


def test_invalid_glm_output_gets_feedback_without_repeating_vision(tmp_path):
    calls = []

    def text_call(folder, brief):
        calls.append(brief)
        return {'role': 'invalid'} if len(calls) == 1 else suggestion()

    result = analysis.analyze_page(tmp_path, 'a' * 64, snapshot(), vision_cache=tmp_path / 'vision-cache',
                                  text_call=text_call, vision_call=lambda *args, **kwargs: visual(),
                                  render=lambda folder, payload: folder / 'snapshot.png')
    assert result['status'] == 'analyzed' and len(calls) == 2
    assert 'validationFeedback' in calls[1] and 'previousLabels' in calls[1]


def test_frontend_delivery_gate_preserves_legacy_jobs_and_labels_drafts(tmp_path):
    frontend = Path(__file__).resolve().parents[1] / 'frontend'
    esbuild = frontend / 'node_modules' / '.bin' / 'esbuild'
    if not esbuild.exists():
        pytest.skip('frontend dependencies are not installed')
    bundle = tmp_path / 'status.mjs'
    subprocess.run([str(esbuild), str(frontend / 'enterprise' / 'presentation-status.ts'), '--bundle', '--platform=node',
                    '--format=esm', f'--outfile={bundle}'], check=True, capture_output=True)
    script = f'''
import assert from 'node:assert/strict';
import {{presentationQuality as quality}} from {json.dumps(bundle.as_uri())};
const job={{status:'completed',stage:'needs_review',options:{{template_id:'test'}},quality_status:'needs_review',ready_for_delivery:false,artifacts_available:['presentation.html']}};
assert.deepEqual(quality(job),{{needsReview:true,accepted:false,html:true,pptx:false}});
assert.equal(quality({{...job,quality_status:'accepted'}}).accepted,false);
assert.equal(quality({{...job,quality_status:'accepted',ready_for_delivery:true}}).accepted,false);
assert.equal(quality({{...job,status:'running'}}).needsReview,false);
assert.deepEqual(quality({{status:'completed'}}),{{needsReview:false,accepted:false,html:true,pptx:true}});
assert.deepEqual(quality({{status:'completed',options:{{template_id:'legacy'}},visual_status:'needs_review'}}),{{needsReview:true,accepted:false,html:true,pptx:true}});
assert.deepEqual(quality({{...job,stage:'completed',quality_status:'accepted',ready_for_delivery:true,artifacts_available:['presentation.html','presentation.pptx']}}),{{needsReview:false,accepted:true,html:true,pptx:true}});
'''
    subprocess.run(['node', '--input-type=module', '-e', script], check=True, capture_output=True, text=True)
