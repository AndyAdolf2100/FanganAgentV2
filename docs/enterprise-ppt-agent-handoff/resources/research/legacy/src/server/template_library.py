"""Management API for the loopback-only local workbench."""
import json
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from src.marketing_graph import uploaded_templates as library

router=APIRouter(prefix='/api/marketing/templates')
def local_only():
    if os.getenv('ENV')!='docker-local': raise HTTPException(404,'Not found')

@router.get('/library')
async def listing():
    local_only(); return {'templates':await run_in_threadpool(library.listing)}

@router.put('/library/{template_id}')
async def save(template_id:str,request:Request):
    local_only()
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>170*1024*1024: raise HTTPException(413,'模板文件过大')
        chunks.append(chunk)
    try:
        data=json.loads(b''.join(chunks))
        if not isinstance(data,dict) or data.get('id')!=template_id: raise ValueError('模板ID不匹配')
        try: previous = await run_in_threadpool(library.read, template_id)
        except FileNotFoundError: previous = {}
        if data.get('published') and data['published'] != previous.get('published'):
            data['published']['componentSchema'] = 1
        return await run_in_threadpool(library.save,data)
    except (ValueError,TypeError,KeyError,AttributeError) as error: raise HTTPException(400,str(error)) from error

@router.delete('/library/{template_id}')
async def remove(template_id:str):
    local_only()
    try: await run_in_threadpool(library.remove,template_id)
    except FileNotFoundError: raise HTTPException(404,'模板不存在')
    except ValueError as error: raise HTTPException(400,str(error)) from error
    return {'ok':True}

@router.get('/library/{template_id}/preview',response_class=HTMLResponse)
async def preview(template_id:str):
    local_only()
    try:
        template=await run_in_threadpool(library.read,template_id)
        page=(template.get('published') or template)['pages'][0]
        return HTMLResponse(library.render(template,page),headers={'Content-Security-Policy':"default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"})
    except (FileNotFoundError,ValueError): raise HTTPException(404,'模板不存在')

@router.post('/library/{template_id}/labels')
async def labels(template_id: str):
    local_only()
    from src.marketing_graph.template_labels import classify
    try:
        template = await run_in_threadpool(library.read, template_id)
        return await classify(template)
    except FileNotFoundError: raise HTTPException(404, '模板不存在')
    except (ValueError, KeyError, TypeError): raise HTTPException(422, '模板标签不完整或 AI 返回格式无效，请保存草稿后重试')
    except Exception: raise HTTPException(502, 'AI 标签识别失败，请稍后重试；已有草稿未改变，可继续手动编辑标签')


@router.get('/library/{template_id}/check')
async def check(template_id: str):
    local_only()
    from src.marketing_graph.template_components import inspect
    try:
        template = await run_in_threadpool(library.read, template_id)
        return await run_in_threadpool(inspect, template)
    except FileNotFoundError: raise HTTPException(404, '模板不存在')
    except ValueError as error: raise HTTPException(422, str(error)) from error


@router.get('/library/{template_id}/trial', response_class=HTMLResponse)
async def trial(template_id: str, page: int = 0):
    local_only()
    from src.marketing_graph.template_components import fields
    from src.marketing_graph.template_component_render import render_pages
    try:
        template = await run_in_threadpool(library.read, template_id)
        if not 0 <= page < len(template['pages']): raise ValueError('版式页码无效')
        layout = template['pages'][page]
        proposal = {'template_page': page, 'role': layout['role'], 'title': '内容试填', 'section_number': 1}
        if proposal['role'] == 'ending': proposal['title'] = '谢谢'
        if proposal['role'] == 'exclude': raise ValueError('该版式已停用')
        blocks = []
        if proposal['role'] == 'body':
            targets = [f for f in fields(layout) if f['role'] in {'body', 'itemBody', 'itemTitle'}]
            blocks = [{'id': f'b{i}', 'kind': 'paragraph', 'text': '重点内容' if f['role'] == 'itemTitle' else '这里展示本页的主要内容，文字将按标记位置填入，原有背景与装饰保持不变。'} for i, f in enumerate(targets)]
            proposal.update(block_ids=[b['id'] for b in blocks], fields={f['name']: [b['id']] for f, b in zip(targets, blocks)})
        agenda = [{'id': 'a1', 'text': '背景与目标', 'number': 1}, {'id': 'a2', 'text': '执行计划', 'number': 2}] if proposal['role'] == 'contents' else []
        documents = await run_in_threadpool(render_pages, template, proposal, blocks, agenda)
        return HTMLResponse(documents[0], headers={'Content-Security-Policy': "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"})
    except FileNotFoundError: raise HTTPException(404, '模板不存在')
    except ValueError as error: raise HTTPException(422, str(error)) from error
