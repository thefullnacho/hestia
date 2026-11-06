// Hestia app core: shared helpers, the one icon table, #hash routing between the four views,
// the menu, and the ask-once tokens. Loaded first; the view files register with Hestia.views.
'use strict';

const svg = (body, size = 15, extra = '') =>
  '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"' + extra + '>' + body + '</svg>';

const ICONS = {
  menu: svg('<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>', 18),
  chat: svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
  today: svg('<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><polyline points="9 15 11 17 15 13"/>'),
  status: svg('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
  goals: svg('<path d="M4 22V4"/><path d="M4 4h12l-2 4 2 4H4"/>'),
  library: svg('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'),
  clips: svg('<rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/>', 22),
  recipes: svg('<path d="M18 2h-8a4 4 0 0 0-4 4v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/><path d="M6 18h14"/><line x1="10" y1="6" x2="14" y2="6"/><line x1="10" y1="10" x2="14" y2="10"/>', 22),
  list: svg('<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><polyline points="3 6 4 7 6 5"/><polyline points="3 12 4 13 6 11"/><polyline points="3 18 4 19 6 17"/>'),
  paw: svg('<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="4" cy="8" r="2"/><path d="M8 14.5c0-2 1.8-3.5 3.5-3.5s3.5 1.5 3.5 3.5c1.5.5 2.5 1.6 2.5 3 0 1.9-1.6 3-3.5 3-1 0-1.6-.5-2.5-.5s-1.5.5-2.5.5c-1.9 0-3.5-1.1-3.5-3 0-1.4 1-2.5 2.5-3z"/>'),
  plus: svg('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>', 12, ' stroke-width="2.5"'),
  trash: svg('<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>', 12),
  close: svg('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>', 13),
  check: svg('<polyline points="20 6 9 17 4 12"/>', 13, ' stroke-width="2.5"'),
  cross: svg('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>', 12, ' stroke-width="2.5"'),
  question: svg('<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>', 12),
  copy: svg('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>', 13),
  speak: svg('<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>', 13),
  stop: '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>',
  speakerOn: svg('<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>', 17),
  speakerMute: svg('<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>', 17),
  mic: svg('<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>', 17),
  send: svg('<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>', 16, ' stroke-width="2.5"'),
  camera: svg('<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>', 17),
  threadMain: svg('<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>', 14),
  heart: svg('<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78z"/>', 15),
  threadSide: svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>', 14),
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// localStorage can throw (private mode, storage blocked); nothing here is load-bearing.
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} },
};

function formatMarkdown(raw) {
  if (!raw) return '';
  const codeBlocks = [];
  let s = String(raw).replace(/[\u0000\u0001]/g, '').replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push('<pre><code' + (lang ? ' class="lang-' + esc(lang) + '"' : '') + '>' + esc(code.trimEnd()) + '</code></pre>');
    return '\u0001' + idx + '\u0001';
  });

  // Inline code and links are set aside before emphasis runs, so an underscore or asterisk
  // inside `garden_bed_notes` or a URL is never read as italics. Placeholders are control
  // characters that no emphasis rule can match.
  const held = [];
  const hold = (html) => '\u0000' + (held.push(html) - 1) + '\u0000';
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, (_, code) => hold('<code>' + code + '</code>'));
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/(?!\/)[^\s)]*)\)/g,
    (_, label, href) => hold('<a href="' + href + '" target="_blank" rel="noopener">' + label + '</a>'));
  // Emphasis needs non-space just inside the markers ("2 * 3 * 4" stays arithmetic), and the
  // underscore forms need a word boundary outside them (snake_case stays snake_case).
  s = s.replace(/\*\*([^*\s](?:[^*]*[^*\s])?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^\w])__([^_\s](?:[^_]*[^_\s])?)__(?!\w)/g, '$1<strong>$2</strong>');
  s = s.replace(/\*([^*\s](?:[^*]*[^*\s])?)\*/g, '<em>$1</em>');
  s = s.replace(/(^|[^\w])_([^_\s](?:[^_]*[^_\s])?)_(?!\w)/g, '$1<em>$2</em>');
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => held[Number(i)]);

  const out = [];
  let inUl = false, inOl = false, pLines = [];
  const flushP = () => { if (pLines.length) { out.push('<p>' + pLines.join('<br>') + '</p>'); pLines = []; } };
  const closeLists = () => {
    if (inUl) { out.push('</ul>'); inUl = false; }
    if (inOl) { out.push('</ol>'); inOl = false; }
  };
  for (const line of s.split('\n')) {
    const trimmed = line.trim();
    if (/^\u0001\d+\u0001$/.test(trimmed)) { flushP(); closeLists(); out.push(trimmed); continue; }
    const ul = line.match(/^\s*[-*•]\s+(.*)$/);
    const ol = line.match(/^\s*(\d+)\.\s+(.*)$/);
    if (ul) {
      flushP();
      if (inOl) { out.push('</ol>'); inOl = false; }
      if (!inUl) { out.push('<ul>'); inUl = true; }
      out.push('<li>' + ul[1] + '</li>');
    } else if (ol) {
      flushP();
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (!inOl) { out.push('<ol>'); inOl = true; }
      out.push('<li>' + ol[2] + '</li>');
    } else if (trimmed === '') {
      flushP(); closeLists();
    } else {
      closeLists();
      pLines.push(trimmed);
    }
  }
  flushP(); closeLists();
  return out.join('\n').replace(/\u0001(\d+)\u0001/g, (_, idx) => codeBlocks[Number(idx)]);
}

// One polite live region for the whole app. Clearing first makes a repeated message re-read.
function announce(text) {
  const el = document.getElementById('announcer');
  el.textContent = '';
  setTimeout(() => { el.textContent = text; }, 50);
}

async function health() {
  const dot = document.getElementById('dot');
  let ok = false;
  try { ok = (await fetch('/health')).ok; } catch {}
  dot.className = 'dot ' + (ok ? 'ok' : 'err');
  dot.setAttribute('aria-label', ok ? 'Brain is up' : 'Brain is not answering');
}

// Ask-once tokens. The capture endpoints keep their own credentials; this phone remembers the
// one the operator typed, and forgets it the moment the brain rejects it.
const TOKENS = {
  ingest: { key: 'hestia.token.ingest', label: 'the photo ingest token (the one the Shortcut uses)' },
  nfc: { key: 'hestia.token.nfc', label: 'the NFC capture token (the one on the tags)' },
};
function getToken(kind, { ask = true } = {}) {
  const t = TOKENS[kind];
  let v = store.get(t.key);
  if (!v && ask) {
    v = (prompt('Paste ' + t.label + '. This phone will remember it.') || '').trim();
    if (v) store.set(t.key, v);
  }
  return v || '';
}
function forgetToken(kind) { store.del(TOKENS[kind].key); }

// --- Views and #hash routing. Each view file registers { enter, leave, title }. ---
const Hestia = { views: {}, current: null };
const VIEW_NAMES = ['chat', 'today', 'goals', 'status', 'library'];

function route() {
  const name = (location.hash.replace('#', '') || 'chat').split('/')[0];
  const next = VIEW_NAMES.includes(name) ? name : 'chat';
  if (Hestia.current === next) return;
  const prev = Hestia.current;
  if (prev && Hestia.views[prev]?.leave) Hestia.views[prev].leave();
  Hestia.current = next;
  for (const v of VIEW_NAMES) document.getElementById('view-' + v).hidden = v !== next;
  document.querySelectorAll('#menu [data-view]').forEach(a => {
    if (a.dataset.view === next) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  document.getElementById('viewTitle').textContent = next === 'chat' ? '' : Hestia.views[next]?.title || '';
  document.getElementById('headerNewBtn').hidden = next !== 'chat';
  if (Hestia.views[next]?.enter) Hestia.views[next].enter();
  // Move focus to the new view's heading for screen readers; never to the textarea, which
  // would throw the phone keyboard up on every switch.
  if (prev) document.getElementById('view-' + next).querySelector('h1')?.focus({ preventScroll: true });
}
function go(view) { if (location.hash !== '#' + view) location.hash = view; else route(); }

// Views that poll stop while the app is in the background, and catch up when it returns.
document.addEventListener('visibilitychange', () => {
  const v = Hestia.views[Hestia.current];
  if (document.hidden) v?.pause?.();
  else { v?.resume?.(); health(); }
});

// --- Menu ---
function setupMenu() {
  const btn = document.getElementById('menuBtn');
  const menu = document.getElementById('menu');
  const close = () => { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    btn.setAttribute('aria-expanded', String(!menu.hidden));
    if (!menu.hidden) menu.querySelector('[aria-current], a')?.focus();
  });
  document.addEventListener('click', (e) => { if (!menu.contains(e.target) && e.target !== btn) close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!menu.hidden) { close(); btn.focus(); }
  });
  menu.querySelectorAll('[data-view]').forEach(a => a.addEventListener('click', close));
  // Capture pages are plain server forms with their own token rules. The queue works read-only
  // without one (Done buttons appear with it); the litter board needs it.
  menu.querySelectorAll('[data-capture]').forEach(a => a.addEventListener('click', (e) => {
    e.preventDefault();
    const needs = a.dataset.capture === 'whelp';
    const token = getToken('nfc', { ask: needs });
    if (needs && !token) return;
    location.href = a.getAttribute('href') + (token ? '?token=' + encodeURIComponent(token) : '');
  }));
  Hestia.closeMenu = close;
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-icon]').forEach(el => {
    if (!el.innerHTML.trim()) el.innerHTML = ICONS[el.dataset.icon] || '';
  });
  setupMenu();
  window.addEventListener('hashchange', route);
  route();
  // A plain page's menu button lands here with ?menu=1: open the menu, then drop the flag
  // so a reload doesn't open it again.
  const q = new URLSearchParams(location.search);
  if (q.has('menu')) {
    q.delete('menu');
    history.replaceState(null, '', location.pathname + (q.size ? '?' + q : '') + location.hash);
    document.getElementById('menuBtn').click();
  }
  health();
});
