"""Recipe drafts and explicit approval; only approved markdown is visible to lookup."""
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


def digest(data): return hashlib.sha256(data).hexdigest()


def slug(name):
    if not isinstance(name,str) or not name.strip() or len(name)>200 or '\n' in name or '\r' in name:
        raise ValueError('Give the recipe a single-line name (up to 200 characters).')
    result = re.sub('[^a-z0-9]+','-',name.lower()).strip('-')
    if not result: raise ValueError('Use at least one letter or number in the name.')
    return result


def root():
    from tools import recipe
    return recipe.RECIPES_DIR


def private(): return root()/'.review'


def atomic(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


@contextmanager
def lock():
    private().mkdir(parents=True,exist_ok=True)
    fd = os.open(private()/'lock',os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'w') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        yield


def draft_path(identifier):
    if not re.fullmatch('[a-f0-9]{32}',identifier): raise ValueError('Invalid draft id.')
    return private()/'drafts'/f'{identifier}.json'


def read(identifier):
    return json.loads(draft_path(identifier).read_text())


def create(source, original):
    identifier = uuid.uuid4().hex
    entry = {**source,'id':identifier,'created_at':dt.datetime.now().astimezone().isoformat(),
             'source_sha256':digest(original),'status':'pending'}
    entry['revision'] = digest(json.dumps(entry,sort_keys=True).encode())
    atomic(private()/'sources'/f'{identifier}.bin',original)
    atomic(draft_path(identifier),json.dumps(entry,ensure_ascii=False).encode())
    return entry


def pending():
    directory = private()/'drafts'
    if not directory.exists(): return []
    entries = [json.loads(p.read_text()) for p in directory.glob('*.json')]
    return [{'id':r['id'],'created_at':r['created_at'], 'names':[c.get('name') for c in r['candidates']]} for r in sorted(entries,key=lambda r:r['created_at'],reverse=True) if r['status']=='pending']


def target(name):
    path = root()/f'{slug(name)}.md'
    data = path.read_bytes() if path.exists() else None
    return {'exists':data is not None,'sha256':digest(data) if data is not None else None,
            'content':data.decode('utf-8') if data is not None else ''}


def approve(identifier, *, revision, name, content, servings='', aliases='', expected_target_hash=None, replace=False, acknowledged=False):
    """Browser-only approval. Never exposed as a model tool. Compare-and-swap under a lock."""
    stem = slug(name)
    if not acknowledged: raise ValueError('Review the source and draft, then acknowledge the quantities and missing details.')
    if not isinstance(content,str) or not content.strip() or len(content.encode())>100000:
        raise ValueError('Supply a nonempty reviewed recipe under 100 KB.')
    for label,value in [('servings',servings),('aliases',aliases)]:
        if not isinstance(value,str) or len(value)>500 or '\n' in value or '\r' in value:
            raise ValueError(f'{label} must be a short single-line value.')
    request = {'name':name,'content':content,'servings':servings,'aliases':aliases,
               'expected_target_hash':expected_target_hash,'replace':replace}
    request_hash = digest(json.dumps(request,sort_keys=True).encode())
    with lock():
        entry = read(identifier)
        if entry['revision'] != revision: raise ValueError('Draft changed. Reload the review.')
        if entry['status']=='approved':
            if entry['approval_request'] != request_hash: raise ValueError('This draft was already approved differently. Import a new draft.')
            return {'status':'approved','name':entry['approved_name'],'id':identifier}
        path = root()/f'{stem}.md'
        old = path.read_bytes() if path.exists() else None
        meta = [f'name: {name.strip()}', f'saved: {dt.date.today().isoformat()}', f'recipe_draft: {identifier}']
        if servings.strip(): meta.append(f'servings: {servings.strip()}')
        if aliases.strip(): meta.append(f'aliases: {aliases.strip()}')
        data = ('---\n'+'\n'.join(meta)+'\n---\n\n'+content.strip()+'\n').encode()
        if old != data:  # Recover a crash between canonical write and draft receipt.
            current_hash = digest(old) if old is not None else None
            if current_hash != expected_target_hash: raise ValueError('The saved recipe changed. Reload and review it before replacing.')
            if old is not None and not replace: raise ValueError('A recipe with this name exists. Explicitly choose replacement or use a different name.')
            if old is not None:
                atomic(private()/'versions'/stem/f'{current_hash}.md',old)
            atomic(path,data)
        from recipe_import import warnings_for
        entry['approval_warnings'] = warnings_for(request,entry.get('text',''))
        entry.update(status='approved',approved_name=name,approval_request=request_hash,
                     approved_at=dt.datetime.now().astimezone().isoformat(),approved_sha256=digest(data),
                     reviewed_content=content,previous_sha256=expected_target_hash,
                     approved_candidate={**{k:request[k] for k in ('name','content','servings','aliases')},'method':'human-reviewed'})
        atomic(draft_path(identifier),json.dumps(entry,ensure_ascii=False).encode())
    return {'status':'approved','name':name,'id':identifier}
