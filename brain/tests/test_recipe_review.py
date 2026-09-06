import asyncio
import json
from pathlib import Path
import re
import socket

import httpx
import pytest

import hestia
import recipe_drafts as drafts
import recipe_import as importing
import recipe_review as review
from eval_support import fixtures
from tool_contract import validate
import tools


@pytest.fixture
def collection(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.recipe,'RECIPES_DIR',tmp_path/'recipes')
    return tmp_path/'recipes'


def make_draft(name='Test pancakes', content='## Ingredients\n- 2 cups flour\n\n## Steps\n1. Mix.'):
    return drafts.create({'kind':'pasted text','text':content,'url':'','media_type':'text/plain',
        'warnings':[],'pdf_links':[], 'candidates':[{'name':name,'content':content,'servings':'4','aliases':''}]},content.encode())


def approval(entry, **extra):
    return {'revision':entry['revision'],**entry['candidates'][0],'acknowledged':True,**extra}


def test_draft_is_not_canonical_and_source_bytes_survive(collection):
    entry=make_draft()
    assert not list(collection.glob('*.md'))
    assert (drafts.private()/'sources'/f"{entry['id']}.bin").read_text()==entry['text']
    assert 'No saved' in tools.recipe.execute('lookup',name='Test pancakes')
    drafts.approve(entry['id'],**approval(entry))
    assert '2 cups flour' in tools.recipe.execute('lookup',name='Test pancakes')
    assert not drafts.pending()
    assert list(collection.glob('*.md'))[0].stat().st_mode & 0o777 == 0o600


def test_approval_requires_acknowledgment_and_exact_revision(collection):
    entry=make_draft()
    with pytest.raises(ValueError,match='acknowledge'):
        drafts.approve(entry['id'],**approval(entry,acknowledged=False))
    with pytest.raises(ValueError,match='changed'):
        drafts.approve(entry['id'],**approval(entry,revision='forged'))
    assert not list(collection.glob('*.md'))


def test_replacement_requires_current_version_and_explicit_choice(collection):
    first=make_draft();drafts.approve(first['id'],**approval(first))
    before=drafts.target('Test pancakes')
    second=make_draft(content='## Ingredients\n- 3 cups flour\n\n## Steps\n1. Mix.')
    with pytest.raises(ValueError,match='changed'):
        drafts.approve(second['id'],**approval(second,replace=True))
    with pytest.raises(ValueError,match='Explicitly'):
        drafts.approve(second['id'],**approval(second,expected_target_hash=before['sha256']))
    args=approval(second,expected_target_hash=before['sha256'],replace=True)
    drafts.approve(second['id'],**args)
    assert '3 cups' in tools.recipe.execute('lookup',name='Test pancakes')
    backup=drafts.private()/'versions'/'test-pancakes'/f"{before['sha256']}.md"
    assert backup.read_text()==before['content']
    assert drafts.approve(second['id'],**args)['status']=='approved'
    with pytest.raises(ValueError,match='already approved'):
        drafts.approve(second['id'],**approval(second,content='changed'))


def test_stale_approval_does_not_clobber_newer_recipe(collection):
    first=make_draft();drafts.approve(first['id'],**approval(first))
    before=drafts.target('Test pancakes')
    one=make_draft(content='version one');two=make_draft(content='version two')
    drafts.approve(two['id'],**approval(two,expected_target_hash=before['sha256'],replace=True))
    with pytest.raises(ValueError,match='changed'):
        drafts.approve(one['id'],**approval(one,expected_target_hash=before['sha256'],replace=True))
    assert 'version two' in tools.recipe.execute('lookup',name='Test pancakes')


def test_structured_recipe_graph_preserves_amounts_and_separates_candidates():
    obj={'@graph':[{'@type':'Recipe','name':'A','recipeIngredient':['½ cup milk','2 cups flour'],
        'recipeInstructions':[{'@type':'HowToSection','name':'Batter','itemListElement':[{'@type':'HowToStep','text':'Mix. Bake at 350F for 20 minutes.'}]}],
        'recipeYield':'4 servings'}, {'@type':['Thing','Recipe'],'name':'B','recipeIngredient':[{'@type':'PropertyValue','value':'3','unitText':'cups','name':'water'}], 'recipeInstructions':'Stir.'}]}
    html=('<html><script type="application/ld+json">'+json.dumps(obj)+'</script><p>Long blog.</p><a href="card.pdf">PDF</a></html>').encode()
    result=importing.extract(html,'text/html','https://example.org/post')
    assert len(result['candidates'])==2
    assert '½ cup milk' in result['candidates'][0]['content']
    assert '350F for 20 minutes' in result['candidates'][0]['content']
    assert '3 cups water' in result['candidates'][1]['content']
    assert result['pdf_links']==['https://example.org/card.pdf']
    assert any('Multiple' in warning for warning in result['warnings'])


def test_blog_without_recipe_is_not_guessed_and_pdf_not_followed():
    source=importing.extract(b'<h1>A story</h1><a href="/actual.pdf">Recipe PDF</a>','text/html','https://example.org/blog')
    assert source['candidates']==[]
    assert source['pdf_links']==['https://example.org/actual.pdf']
    assert review.blank_candidate(source)['content']==''


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.2','169.254.169.254','100.64.0.1','::1'])
def test_private_source_addresses_rejected(monkeypatch,address):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',(address,443))])
    with pytest.raises(ValueError,match='public'):
        importing.fetch_public('https://example.org/recipe')


def test_connection_is_pinned_and_redirect_not_followed(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('8.8.8.8',443))])
    seen=[]
    monkeypatch.setattr(socket,'create_connection',lambda address,*a:seen.append(address))
    class Response:
        status=302
    class Connection:
        def __init__(self,*a,**k):pass
        def request(self,*a,**k):self._create_connection(('rebind.example',443),12)
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(importing.http.client,'HTTPSConnection',Connection)
    with pytest.raises(ValueError,match='redirects'):
        importing.fetch_public('https://example.org/recipe')
    assert seen==[('8.8.8.8',443)]


def test_missing_and_changed_numbers_are_flagged():
    warnings=importing.warnings_for({'content':'## Ingredients\n- 9 cups flour\n\n## Steps\n1. Mix.','servings':''},'2 cups flour')
    assert any('9' in w for w in warnings)
    assert any('Yield' in w for w in warnings)
    assert any('temperature' in w for w in warnings)
    assert any('time' in w for w in warnings)


def test_model_cannot_call_approval(collection):
    assert validate('recipe',{'action':'approve','name':'Test'},tools.SCHEMAS)
    result=tools.dispatch('recipe',{'action':'save','name':'Test','content':'Draft','approved':True})
    assert result.startswith('Error')
    assert not list(collection.glob('*.md'))


def test_browser_api_import_review_approve_and_source_download(collection,monkeypatch):
    async def organize(source):
        return {'name':'Pasted test','content':'## Ingredients\n- 2 cups flour\n\n## Steps\n1. Mix.','servings':'','aliases':'','method':'test'}
    from fastapi import FastAPI
    app=FastAPI();app.include_router(review.router(organize))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            headers={'X-Hestia-Recipe-Review':'1'}
            original='A test recipe: 2 cups flour. Mix.'
            rejected=await client.post('/api/recipes/import',json={'text':original})
            assert rejected.status_code==400
            made=await client.post('/api/recipes/import',json={'text':original},headers=headers)
            assert made.status_code==200,made.text
            rid=made.json()['id']
            entry=(await client.get('/api/recipes/drafts/'+rid)).json()
            assert entry['source_preview']==original
            source=await client.get('/api/recipes/drafts/'+rid+'/source')
            assert source.text==original
            assert 'attachment' in source.headers['content-disposition']
            assert source.headers['x-content-type-options']=='nosniff'
            assert not list(collection.glob('*.md'))
            fields={k:entry['candidates'][0][k] for k in ('name','content','servings','aliases')}
            data={'revision':entry['revision'],**fields,'acknowledged':True}
            forbidden=await client.post('/api/recipes/drafts/'+rid+'/approve',json=data,headers={**headers,'Origin':'http://hostile.example'})
            assert forbidden.status_code==409
            approved=await client.post('/api/recipes/drafts/'+rid+'/approve',json=data,headers=headers)
            assert approved.status_code==200,approved.text
            assert '2 cups flour' in tools.recipe.execute('lookup',name='Pasted test')
    asyncio.run(scenario())


def test_chat_draft_keeps_actual_user_source(monkeypatch):
    original='Save this recipe as Test: two cups flour, mix.'
    async def chat(messages,schemas):
        if messages[-1]['role']=='tool': return {'content':'Ready for review.'}
        return {'tool_calls':[{'function':{'name':'recipe','arguments':{'action':'save','name':'Test','content':'## Ingredients\n- 2 cups flour\n\n## Steps\n1. Mix.'}}}]}
    monkeypatch.setattr(hestia,'_ollama_chat',chat)
    with fixtures():
        answer=asyncio.run(hestia.run_agent([{'role':'user','content':original}]))
        entry=drafts.read(drafts.pending()[0]['id'])
        assert original in entry['text']
        assert answer.startswith('Drafted ')
        assert not list(tools.recipe.RECIPES_DIR.glob('*.md'))


def test_text_pdf_extraction_with_real_local_parser():
    import shutil
    if not shutil.which('pdftotext'):
        pytest.skip('pdftotext not installed')
    stream=b'BT /F1 12 Tf 72 720 Td (Test recipe: 2 cups flour. Mix.) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
             b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    pdf=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(pdf));pdf+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(pdf)
    pdf+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{off:010d} 00000 n \n'.encode() for off in offsets[1:])
    pdf+=f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    source=importing.extract(pdf,'application/pdf')
    assert source['kind']=='pdf'
    assert '2 cups flour' in source['text']
    assert any('40 pages' in warning for warning in source['warnings'])


def test_structured_web_import_does_not_use_model(collection,monkeypatch):
    raw=b'<script type="application/ld+json">{"@type":"Recipe","name":"Test","recipeIngredient":["2 cups flour"],"recipeInstructions":"Mix."}</script>'
    monkeypatch.setattr(importing,'fetch_public',lambda url:(raw,'text/html'))
    async def forbidden(_):pytest.fail('structured recipes must not require model rewriting')
    from fastapi import FastAPI
    app=FastAPI();app.include_router(review.router(forbidden))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            response=await client.post('/api/recipes/import',json={'url':'https://example.org/recipe'},headers={'X-Hestia-Recipe-Review':'1'})
            assert response.status_code==200,response.text
            entry=drafts.read(response.json()['id'])
            assert '2 cups flour' in entry['candidates'][0]['content']
            assert (drafts.private()/'sources'/f"{entry['id']}.bin").read_bytes()==raw
    asyncio.run(scenario())


def test_conflicting_amounts_and_omissions_are_flagged():
    proposal={'method':'model-organized source','servings':'4',
              'content':'## Ingredients\n- 2 cups flour\n\n## Steps\n1. Add 3 cups flour.'}
    warnings=importing.warnings_for(proposal,'2 cups flour, 1/2 teaspoon salt. Makes 4.')
    assert any('Multiple amounts for flour' in w for w in warnings)
    assert any('1/2' in w and 'missing' in w for w in warnings)
