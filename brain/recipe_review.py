"""Private browser review API. Approval is deliberately absent from model tools."""
import asyncio
import base64
import json
from pathlib import Path
import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

import config
import recipe_drafts as drafts
import recipe_import as importing

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='recipe-import')
_busy = False


async def worker(fn, *args):
    global _busy
    if _busy:
        raise ValueError('Another recipe import is still running. Try again shortly.')
    _busy = True
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_executor, fn, *args)
    def release(_):
        global _busy
        _busy = False
    future.add_done_callback(release)
    return await asyncio.wait_for(asyncio.shield(future), 50)


def blank_candidate(source):
    return {'name':'','content':source['text'] if source['kind'] != 'web page' else '',
            'servings':'','aliases':'','method':'unorganized source'}


def stage(body, media_type, url=''):
    source = importing.extract(body, media_type, url)
    if not source['candidates']:
        source['candidates'] = [blank_candidate(source)]
        source['warnings'].append('This source needs ingredient and step review before approval.')
    return drafts.create(source, body)


async def body_json(request):
    if request.headers.get('X-Hestia-Recipe-Review') != '1':
        raise ValueError('Use the recipe review page for this action.')
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise ValueError('Recipe changes must come from this Hestia page.')
    if request.headers.get('content-type','').split(';')[0] != 'application/json':
        raise ValueError('Expected JSON from the recipe review page.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>7*1024*1024:
            raise ValueError('Import request exceeds 7 MiB.')
    data = json.loads(raw)
    if not isinstance(data,dict): raise ValueError('Expected an object.')
    return data


def public_view(entry):
    view = dict(entry)
    # Original bytes remain downloadable; do not send a huge blog into the page renderer.
    view['source_preview'] = view.pop('text','')[:150000]
    view['source_preview_truncated'] = len(entry.get('text',''))>150000
    view['warnings'] = list(entry.get('warnings',[]))
    candidates = [entry['approved_candidate']] if entry.get('approved_candidate') else entry['candidates']
    view['candidates'] = [{**c,'warnings':importing.warnings_for(c,entry.get('text',''))} for c in candidates]
    return view


def router(organize):
    api = APIRouter()

    @api.get('/recipes')
    @api.get('/recipes/review/{identifier}')
    async def page(identifier: str = ''):
        return FileResponse(config.CLIENTS_DIR/'recipes.html', headers={'Cache-Control':'no-store'})

    @api.get('/api/recipes/drafts')
    async def listing():
        return JSONResponse(await asyncio.to_thread(drafts.pending),headers={'Cache-Control':'no-store'})

    @api.get('/api/recipes/target')
    async def target(name: str):
        try:
            return JSONResponse(await asyncio.to_thread(drafts.target,name),headers={'Cache-Control':'no-store'})
        except ValueError as exc:
            return JSONResponse({'error':str(exc)},status_code=400)

    @api.get('/api/recipes/drafts/{identifier}')
    async def review(identifier: str):
        try:
            return JSONResponse(public_view(await asyncio.to_thread(drafts.read,identifier)),headers={'Cache-Control':'no-store'})
        except (ValueError,FileNotFoundError):
            return JSONResponse({'error':'Draft not found.'},status_code=404)

    @api.get('/api/recipes/drafts/{identifier}/source')
    async def original(identifier: str):
        try:
            entry = await asyncio.to_thread(drafts.read,identifier)
        except (ValueError,FileNotFoundError):
            return JSONResponse({'error':'Draft not found.'},status_code=404)
        ext = '.pdf' if entry['kind']=='pdf' else '.html' if entry['kind']=='web page' else '.txt'
        return FileResponse(drafts.private()/'sources'/f'{identifier}.bin',
            media_type='application/octet-stream',filename='original-recipe-source'+ext,
            headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})

    @api.post('/api/recipes/import')
    async def import_recipe(request: Request):
        try:
            data = await body_json(request)
            provided = [k for k in ('url','text','pdf_base64') if data.get(k)]
            if len(provided)!=1: raise ValueError('Provide one URL, pasted recipe, or PDF.')
            url = ''
            if provided[0]=='url':
                url = data['url']
                if not isinstance(url,str) or len(url)>4000: raise ValueError('Invalid source URL.')
                raw, media_type = await worker(importing.fetch_public,url)
            elif provided[0]=='pdf_base64':
                raw = base64.b64decode(data['pdf_base64'],validate=True)
                if not raw.startswith(b'%PDF-'): raise ValueError('Upload a PDF file.')
                media_type = 'application/pdf'
            else:
                if not isinstance(data['text'],str): raise ValueError('Paste recipe text.')
                raw, media_type = data['text'].encode(),'text/plain'
            source = await worker(importing.extract,raw,media_type,url)
            if not source['candidates']:
                source['candidates'] = [blank_candidate(source)]
                if source['kind'] != 'web page' and 0<len(source['text'].encode())<=14000:
                    try:
                        proposal = await organize(source['text'])
                        source['candidates'] = [proposal]
                        source['warnings'].append('Hestia organized this source. Verify every quantity and step against the original.')
                    except Exception:
                        source['warnings'].append('Automatic organization was unavailable. Edit the source into ingredients and steps below.')
                elif source['kind']!='web page':
                    source['warnings'].append('The source is empty or too long for automatic organization. Paste the recipe section or edit the draft manually.')
            entry = await worker(drafts.create,source,raw)
            return {'id':entry['id'],'review_url':f'/recipes/review/{entry["id"]}'}
        except (ValueError,TypeError,KeyError) as exc:
            return JSONResponse({'error':str(exc)},status_code=400)
        except asyncio.TimeoutError:
            return JSONResponse({'error':'Import timed out. Try pasting the recipe section.'},status_code=504)
        except Exception:
            return JSONResponse({'error':'Could not import this source. Try a direct recipe URL or paste its text.'},status_code=502)

    @api.post('/api/recipes/drafts/{identifier}/approve')
    async def approve(identifier: str, request: Request):
        try:
            data = await body_json(request)
            if data.get('acknowledged') is not True: raise ValueError('Review and acknowledge the source and quantities first.')
            result = await asyncio.to_thread(drafts.approve,identifier,
                revision=data.get('revision'),name=data.get('name'),content=data.get('content'),
                servings=data.get('servings',''),aliases=data.get('aliases',''),
                expected_target_hash=data.get('expected_target_hash'),replace=data.get('replace') is True,
                acknowledged=True)
            return result
        except (ValueError,FileNotFoundError,TypeError) as exc:
            return JSONResponse({'error':str(exc)},status_code=409)

    return api
