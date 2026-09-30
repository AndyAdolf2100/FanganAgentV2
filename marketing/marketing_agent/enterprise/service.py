"""Persist browser-authored templates; never parse PPTX or classify labels here."""
import copy
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import uploaded_templates as library, template_components, template_html
from .template_component_render import render_pages
from . import adaptive


def font_status(template):
    names = sorted({r['font'] for p in template['pages'] for e in p['elements']
                    for paragraph in e.get('paragraphs', []) for r in paragraph.get('runs', [])})
    result = []
    for name in names:
        if not shutil.which('fc-match'):
            result.append({'requested': name, 'status': 'unverified'})
            continue
        try:
            resolved = subprocess.run(['fc-match', '--format=%{family}', name], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            result.append({'requested': name, 'status': 'unverified'})
            continue
        generic = name.lower() in {'system-ui', 'sans-serif', 'serif', 'monospace'}
        matched = generic or name.casefold() in [s.strip().casefold() for s in resolved.split(',')]
        result.append({'requested': name, 'resolved': resolved, 'status': 'available' if matched else 'fallback'})
    return result


class TemplateLibrary:
    def __init__(self, directory):
        self.root = Path(directory) / 'enterprise-templates'

    @contextmanager
    def context(self):
        # The ported storage engine uses a module root. Isolate app/test data dirs.
        with library.LOCK:
            previous = library.ROOT
            library.ROOT = self.root
            try:
                yield
            finally:
                library.ROOT = previous

    def listing(self):
        with self.context():
            return library.listing()

    def read(self, template_id, revision=None):
        with self.context():
            return library.read(template_id, revision)

    def save(self, value):
        value = copy.deepcopy(value)
        with self.context():
            try:
                old = library.read(value['id'])
            except FileNotFoundError:
                old = {}
            published = value.get('published')
            if published and published != old.get('published'):
                if value.get('reviewAccepted') is not True:
                    raise ValueError('请先在前端核对模板预览、字体及导入警告')
                published['componentSchema'] = 1
                template_html.catalog(adaptive.prepare({**value, 'pages': published['pages']}))
            return library.save(value)

    def remove(self, template_id):
        with self.context():
            library.remove(template_id)

    def snapshot(self, template_id, revision):
        template = self.read(template_id, revision)
        library.validate(template)
        if not template.get('published') or template['published']['revision'] != revision:
            raise ValueError('请选择已发布的企业模板版本')
        template['pages'] = template['published']['pages']
        template_html.catalog(adaptive.prepare(template))
        return template


def router_for(store):
    router = APIRouter(prefix='/api/enterprise-templates')

    def read(key):
        try:
            return store.read(key)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, '企业模板不存在')

    @router.get('')
    def listing():
        return {'templates': store.listing()}

    @router.put('/{template_id}')
    async def save(template_id: str, request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 170 * 1024 * 1024:
                raise HTTPException(413, '模板数据过大')
        import json
        try:
            value = json.loads(data)
            if not isinstance(value, dict) or value.get('id') != template_id:
                raise ValueError('模板ID不匹配')
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(store.save, value)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(422, str(exc))

    @router.delete('/{template_id}')
    def remove(template_id: str):
        read(template_id)
        store.remove(template_id)
        return {'ok': True}

    @router.get('/{template_id}/check')
    def check(template_id: str):
        template = read(template_id)
        report = template_components.inspect(template)
        try:
            adaptive.prepare(template)
        except ValueError as exc:
            report['errors'].append(str(exc)); report['valid'] = False
        report['server_fonts'] = font_status(template)
        report['warnings'].extend(f'生成服务字体“{f["requested"]}”将替换为“{f["resolved"]}”，请安装原字体或在前端明确修改模板字体'
                                  for f in report['server_fonts'] if f['status'] == 'fallback')
        return report

    @router.get('/{template_id}/trial', response_class=HTMLResponse)
    def trial(template_id: str, page: int = 0):
        template = read(template_id)
        if page < 0 or page >= len(template['pages']):
            raise HTTPException(404, '模板页不存在')
        layout = template['pages'][page]
        proposal = {'template_page': page, 'role': layout['role'], 'title': '模板试填', 'section_number': 1}
        blocks = [{'id': 'example', 'kind': 'paragraph', 'text': '这是试填正文，用于核对文字区域、字号与企业模板样式。'}] if layout['role'] in {'body','preface'} else []
        agenda = [{'id': 'a1', 'text': '示例章节', 'number': 1}] if layout['role'] == 'contents' else []
        try:
            if layout['role'] == 'body':
                working = adaptive.prepare(template)
                parts = adaptive.fragments(blocks)
                p = {**proposal, 'body_parts': parts, 'layout_spec': adaptive.fallback_spec(parts, working['_contracts'][str(page)]['frame'])}
                document = adaptive.compile_body(working, p)
            elif layout['role'] == 'preface':
                # Label trial only: flow sample prose into authored text fields.
                # Real generation uses model HTML and retains the preface role.
                working = copy.deepcopy(template)
                working['pages'][page]['role'] = 'body'
                document = render_pages(working, {**proposal, 'role':'body', 'title':'序言'}, blocks, [])[0]
            else:
                document = render_pages(template, proposal, blocks, agenda)[0]
            return HTMLResponse(document, headers={'Content-Security-Policy': "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"})
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    return router
