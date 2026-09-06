"""Bounded public recipe acquisition and deterministic Schema.org extraction."""
from __future__ import annotations

import html
from html.parser import HTMLParser
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, urljoin

MAX_SOURCE = 5 * 1024 * 1024


def fetch_public(url):
    """Pin the connection to an address validated before dialing; preserve TLS SNI.

    No redirects, proxy environment, credentials, private hosts or non-web ports.
    """
    u = urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('Use a public http(s) recipe URL without credentials.')
    port = u.port or (443 if u.scheme == 'https' else 80)
    if port not in (80, 443):
        raise ValueError('Only standard web ports are supported.')
    addresses = {r[4][0] for r in socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast or
                            ipaddress.ip_address(a) in ipaddress.ip_network('100.64.0.0/10') for a in addresses):
        raise ValueError('Recipe import is limited to public websites.')
    address = sorted(addresses)[0]
    cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
    conn = cls(u.hostname, port, timeout=12)
    conn._create_connection = lambda ignored, timeout, source_address=None: socket.create_connection((address, port), timeout, source_address)
    try:
        conn.request('GET', (u.path or '/') + ('?' + u.query if u.query else ''),
                     headers={'User-Agent': 'Hestia-recipe-import/1', 'Accept-Encoding': 'identity'})
        response = conn.getresponse()
        if 300 <= response.status < 400:
            raise ValueError('This URL redirects. Open the destination in your browser and import that URL explicitly.')
        if response.status != 200:
            raise ValueError(f'The recipe website returned HTTP {response.status}.')
        if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
            raise ValueError('Compressed responses are not supported. Paste the recipe text instead.')
        chunks, size, deadline = [], 0, time.monotonic()+30
        while True:
            if time.monotonic()>deadline: raise ValueError('Source download took too long.')
            chunk = response.read1(min(65536, MAX_SOURCE+1-size))
            if not chunk: break
            chunks.append(chunk)
            size += len(chunk)
            if size>MAX_SOURCE: raise ValueError('Source exceeds the 5 MiB import limit.')
        body = b''.join(chunks)
        return body, response.getheader('Content-Type', '').split(';')[0].lower()
    finally:
        conn.close()


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ld = []
        self.links = []
        self.text = []
        self.script = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.script = [] if attrs.get('type', '').lower() == 'application/ld+json' else None
        if tag in ('script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'aside', 'video', 'iframe'):
            self.skip += 1
        if tag == 'a' and attrs.get('href'):
            if '.pdf' in attrs['href'].lower() or attrs.get('type') == 'application/pdf':
                self.links.append(attrs['href'])
        if tag in ('p', 'li', 'div', 'br', 'h1', 'h2', 'h3') and not self.skip:
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.ld.append(''.join(self.script))
            self.script = None
        if tag in ('script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'aside', 'video', 'iframe'):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        elif not self.skip:
            self.text.append(data)


def plain(value):
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return ''
    return html.unescape(re.sub('<[^>]*>', ' ', value)).strip()


def lines(value):
    if isinstance(value, list):
        return [line for item in value for line in lines(item)]
    if isinstance(value, dict):
        if 'item' in value:
            return lines(value['item'])
        if 'value' in value and ('unitText' in value or 'unitCode' in value or 'name' in value):
            return [' '.join(filter(None,[plain(value['value']),plain(value.get('unitText') or value.get('unitCode')),plain(value.get('name'))]))]
        nested = value.get('itemListElement')
        if nested is not None:
            title = plain(value.get('name'))
            return ([title] if title else []) + lines(nested)
        return lines(value.get('text') or value.get('value') or value.get('name') or '')
    return [plain(value)] if plain(value) else []


def recipes_in(value, depth=0):
    if depth > 30:
        return []
    found = []
    if isinstance(value, list):
        for item in value:
            found.extend(recipes_in(item, depth+1))
    elif isinstance(value, dict):
        types = value.get('@type', [])
        types = [types] if isinstance(types, str) else types
        if isinstance(types, list) and any(str(t).split('/')[-1] == 'Recipe' for t in types):
            found.append(value)
        else:
            for item in value.values():
                if isinstance(item, (dict, list)):
                    found.extend(recipes_in(item, depth+1))
    return found


def candidate(recipe):
    ingredients = lines(recipe.get('recipeIngredient', recipe.get('ingredients', [])))
    steps = lines(recipe.get('recipeInstructions', []))
    timing = [' '.join([label, *lines(recipe.get(key))]).strip() for key, label in
              [('prepTime', 'Prep time:'), ('cookTime', 'Cook time:'), ('totalTime', 'Total time:')]]
    timing = [x for x in timing if not x.endswith(':')]
    body = '## Ingredients\n' + '\n'.join('- '+x for x in ingredients)
    body += '\n\n## Steps\n' + '\n'.join(f'{i}. {x}' for i,x in enumerate(steps,1))
    if timing:
        body += '\n\n## Timing\n' + '\n'.join(timing)
    return {'name': plain(recipe.get('name')), 'servings': '; '.join(lines(recipe.get('recipeYield'))),
            'content': body, 'aliases': '', 'method': 'structured recipe data'}


def pdf_text(body):
    with tempfile.TemporaryDirectory(prefix='hestia-recipe-pdf-') as tmp:
        source = Path(tmp)/'source.pdf'
        target = Path(tmp)/'extracted.txt'
        source.write_bytes(body)
        try:
            subprocess.run(['pdftotext', '-f', '1', '-l', '40', '-layout', str(source), str(target)],
                           check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise ValueError('PDF text extraction needs pdftotext. Paste the recipe text instead.') from None
        except (subprocess.SubprocessError, OSError):
            raise ValueError('Could not extract text from this PDF. Paste a transcription instead.') from None
        if target.stat().st_size > MAX_SOURCE:
            raise ValueError('Extracted PDF text is too large.')
        return target.read_text(errors='replace')


def extract(body, media_type, url=''):
    if not body or len(body) > MAX_SOURCE:
        raise ValueError('Supply a nonempty source no larger than 5 MiB.')
    result = {'url':url, 'media_type':media_type, 'candidates':[], 'pdf_links':[], 'warnings':[]}
    if media_type == 'application/pdf' or body.startswith(b'%PDF-'):
        result['text'] = pdf_text(body)
        result['kind'] = 'pdf'
        result['warnings'].append('PDF text extraction covers at most 40 pages. Check the original for layout errors or missing pages; scanned images are not transcribed.')
    elif media_type == 'text/plain':
        result['text'] = body.decode('utf-8', errors='replace')
        result['kind'] = 'pasted text'
    elif media_type in ('text/html','application/xhtml+xml',''):
        page = Page()
        page.feed(body.decode('utf-8', errors='replace'))
        result['kind'] = 'web page'
        result['text'] = ''.join(page.text).strip()
        result['pdf_links'] = list(dict.fromkeys(urljoin(url, link) for link in page.links))[:20]
        seen = set()
        for block in page.ld:
            try:
                decoded = json.loads(block)
            except (ValueError, RecursionError):
                result['warnings'].append('Some structured data could not be read.')
                continue
            for item in recipes_in(decoded):
                fingerprint = json.dumps(item,sort_keys=True)
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    result['candidates'].append(candidate(item))
        if result['candidates']:
            result['warnings'].append('Compare the structured recipe with the original page; printed instructions or notes may differ.')
        if len(result['candidates']) > 1:
            result['warnings'].append('Multiple recipes were found. Choose one; they have not been combined.')
        if not result['candidates']:
            result['warnings'].append('No structured recipe was found. Choose a linked PDF or paste just the recipe section; the blog text has not been guessed into a recipe.')
    else:
        raise ValueError('Import HTML, text, or a PDF.')
    if len(result['candidates']) > 20:
        raise ValueError('This page contains too many recipes. Use the individual recipe page.')
    return result


def warnings_for(proposal, source_text):
    body = proposal.get('content','')
    warnings = []
    if not proposal.get('servings','').strip(): warnings.append('Yield or servings are missing.')
    if not re.search(r'\b\d+\s*(?:°\s*)?(?:degrees|[FC]\b)', body, re.I): warnings.append('No temperature was identified. Check whether this recipe needs one.')
    if not re.search(r'\b(?:\d+\s*(?:minutes?|mins?|hours?|hrs?|seconds?)|PT\d)', body, re.I): warnings.append('No preparation or cooking time was identified.')
    cleaned = re.sub(r'^\s*\d+[.)]\s+', '', body, flags=re.M)
    numbers = lambda text: set(re.findall(r'\d+(?:[./]\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞]', text))
    extra = numbers(cleaned) - numbers(source_text)
    if extra: warnings.append('Check quantities not found verbatim in the extracted source: '+', '.join(sorted(extra)))
    if proposal.get('method') == 'model-organized source':
        missing = numbers(source_text) - numbers(cleaned + ' ' + proposal.get('servings',''))
        if missing: warnings.append('Source numbers missing from the draft: '+', '.join(sorted(missing)[:12]))
    quantities = {}
    for amount,unit,ingredient in re.findall(r'(\d+(?:[./]\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞])\s*(cups?|tablespoons?|teaspoons?|tbsp|tsp|grams?|ounces?)\s+(?:of\s+)?([a-z]+)', body.lower()):
        quantities.setdefault((unit.rstrip('s'),ingredient),set()).add(amount)
    for (unit,ingredient),amounts in quantities.items():
        if len(amounts)>1: warnings.append(f'Multiple amounts for {ingredient} in {unit}: {", ".join(sorted(amounts))}. Check for a conflict or an intentional divided amount.')
    if not re.search(r'ingredients\s*\n\s*[-*]\s*\S', body, re.I): warnings.append('A populated ingredient list is missing.')
    if not re.search(r'steps\s*\n\s*1[.)]\s*\S', body, re.I): warnings.append('Numbered preparation steps are missing.')
    return warnings
