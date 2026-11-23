// Today view: what is due, read straight from the stores by GET /today. No model, no GPU.
// Each section can fail on its own (HA down, calendar unreachable) without blanking the rest.
// On top, while the note-taker has proposals waiting, a card to keep or drop each one: nothing
// is remembered until a person taps Keep (POST /api/memory/inbox, memory_api.py). Below it, items a
// receipt proposed (receipts.py), kept into the inventory the same way (receipts_api.py), and
// house docs the model drafted (docs_store.py), kept into the library the same way (docs_api.py).
'use strict';

(() => {
  const body = () => document.getElementById('todayBody');
  const MEMORY_HEADERS = { 'Content-Type': 'application/json', 'X-Hestia-Memory': '1' };
  let inbox = null;   // GET /memory/inbox, or null when it could not be read
  const INV_HEADERS = { 'Content-Type': 'application/json', 'X-Hestia-Inventory': '1' };
  let receiptItems = null;   // GET /api/inventory/receipt-items, or null
  const DOC_HEADERS = { 'Content-Type': 'application/json', 'X-Hestia-Docs': '1' };
  let houseDocs = null;      // GET /api/docs/proposed, or null
  const KIND = { guide: 'Guide', account: 'Account', note: 'Note' };
  const amountOf = (r) => r.qty == null ? 'some' : (r.unit ? r.qty + ' ' + r.unit : String(r.qty));
  const day = (iso) => iso ? new Date(iso + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) : '';

  const time = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  };
  const failed = (sec) => sec && sec.error ? '<p class="t-err">' + esc(sec.error) + '</p>' : '';
  const card = (title, inner) => '<div class="card"><h2>' + esc(title) + '</h2>' + inner + '</div>';
  const list = (rows, none) => rows.length ? '<ul class="t-list">' + rows.join('') + '</ul>' : '<p class="t-none">' + esc(none) + '</p>';

  function memoryItem(p) {
    return '<li class="mem-item" data-id="' + esc(p.id) + '"><div class="mem-text">' + esc(p.body) + '</div>' +
      '<div class="meta">' + esc(p.type) + (p.age_days ? ' · ' + p.age_days + 'd ago' : ' · today') + '</div>' +
      '<p class="t-err" data-err hidden></p>' +
      '<div class="mem-actions"><button class="btn" data-mem="discard">Not this</button>' +
      '<button class="btn" data-mem="edit">Edit</button>' +
      '<button class="btn primary" data-mem="keep">Keep</button></div></li>';
  }

  function memoryCard() {
    if (!inbox || !inbox.count) return '';
    const n = inbox.count;
    return '<div class="card" id="memCard"><h2>Hestia wants to remember</h2>' +
      '<p class="meta" style="margin:0 0 10px">From recent conversations. Nothing is remembered until you keep it' +
      (n > inbox.proposals.length ? '; showing ' + inbox.proposals.length + ' of ' + n : '') + '.</p>' +
      '<ul class="mem-list">' + inbox.proposals.map(memoryItem).join('') + '</ul></div>';
  }

  function receiptCards() {
    if (!receiptItems || !receiptItems.length) return '';
    const groups = new Map();
    receiptItems.forEach(r => { if (!groups.has(r.receipt)) groups.set(r.receipt, []); groups.get(r.receipt).push(r); });
    let h = '';
    for (const [rid, rows] of groups) {
      const head = [rows[0].store, day(rows[0].bought)].filter(Boolean).join(', ');
      h += '<div class="card rc-card" data-receipt="' + esc(rid) + '"><h2>From a receipt' + (head ? ': ' + esc(head) : '') + '</h2>' +
        '<p class="meta" style="margin:0 0 10px">Nothing goes into the inventory until you keep it. Fix a name or amount with Edit.</p>' +
        '<ul class="mem-list">' + rows.map(r =>
          '<li class="mem-item rc-item" data-pid="' + r.id + '"><div class="mem-text"><span class="rc-name">' + esc(r.name) +
          '</span> · <span class="rc-amount">' + esc(amountOf(r)) + '</span></div>' +
          '<div class="meta">' + (r.adds_to ? 'adds to ' + esc(r.adds_to) : 'new item') + ' · ' + esc(r.line) + '</div>' +
          '<p class="t-err" data-err hidden></p>' +
          '<div class="mem-actions"><button class="btn" data-rc="discard">Not this</button>' +
          '<button class="btn" data-rc="edit">Edit</button><button class="btn primary" data-rc="keep">Keep</button></div></li>').join('') +
        '</ul>' + (rows.length > 1 ? '<div class="mem-actions"><button class="btn primary" data-rc="keep-all">Keep all ' + rows.length + '</button></div>' : '') +
        '</div>';
    }
    return h;
  }

  function docCards() {
    if (!houseDocs || !houseDocs.length) return '';
    return houseDocs.map(d =>
      '<div class="card doc-card" data-doc="' + d.id + '"><h2>A house doc to keep</h2>' +
      '<p class="meta" style="margin:0 0 10px">' + esc(KIND[d.kind] || d.kind) +
      (d.about ? ' about ' + esc(d.about) : '') + (d.page_cite ? ', pinned to ' + esc(d.page_cite) : '') +
      '. Nothing goes into the library until you keep it.' +
      (d.revises_title ? ' Keeping it replaces “' + esc(d.revises_title) + '”.' : '') + '</p>' +
      '<div class="doc-view"><div class="mem-text"><strong class="doc-title">' + esc(d.title) + '</strong></div>' +
      '<div class="mem-text doc-body" style="white-space:pre-wrap;margin-top:6px">' + esc(d.body) + '</div>' +
      (d.sources && d.sources.length ? '<div class="meta" style="margin-top:6px">Sources: ' + d.sources.map(esc).join('; ') + '</div>' : '') +
      '</div><p class="t-err" data-err hidden></p>' +
      '<div class="mem-actions"><button class="btn" data-doc-act="discard">Not this</button>' +
      '<button class="btn" data-doc-act="edit">Edit</button>' +
      '<button class="btn primary" data-doc-act="keep">Keep</button></div></div>').join('');
  }

  async function docPost(id, act, payload) {
    const r = await fetch('/api/docs/' + id + '/' + act, { method: 'POST', headers: DOC_HEADERS, body: JSON.stringify(payload || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }

  async function onDoc(e) {
    const btn = e.target.closest('button[data-doc-act]');
    if (!btn) return;
    const card = btn.closest('.doc-card');
    const id = Number(card.dataset.doc);
    const act = btn.dataset.docAct;
    const err = card.querySelector('[data-err]');
    if (act === 'edit') {
      const d = houseDocs.find(x => x.id === id);
      card.querySelector('.doc-view').innerHTML =
        '<label class="g-label">Title<input class="g-input" data-f="title" value="' + esc(d.title) + '"></label>' +
        '<label class="g-label">Doc<textarea class="g-input" data-f="body" rows="12">' + esc(d.body) + '</textarea></label>';
      btn.hidden = true;
      card.querySelector('[data-doc-act="keep"]').textContent = 'Keep my version';
      return;
    }
    const buttons = card.querySelectorAll('button');
    buttons.forEach(b => { b.disabled = true; });
    try {
      const f = (n) => card.querySelector('[data-f="' + n + '"]');
      const payload = act === 'keep' && f('title') ? { title: f('title').value, body: f('body').value } : {};
      await docPost(id, act === 'keep' ? 'keep' : 'discard', payload);
      houseDocs = houseDocs.filter(x => x.id !== id);
      card.remove();
    } catch (ex) {
      buttons.forEach(b => { b.disabled = false; });
      err.textContent = ex.message;
      err.hidden = false;
    }
  }

  async function receiptPost(path, payload) {
    const r = await fetch(path, { method: 'POST', headers: INV_HEADERS, body: JSON.stringify(payload || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }

  async function onReceipt(e) {
    const btn = e.target.closest('button[data-rc]');
    if (!btn) return;
    const card = btn.closest('.rc-card');
    const li = btn.closest('.rc-item');
    const act = btn.dataset.rc;
    const err = (li || card).querySelector('[data-err]');
    if (act === 'edit') {
      const name = li.querySelector('.rc-name'), amount = li.querySelector('.rc-amount');
      li.querySelector('.mem-text').innerHTML =
        '<label class="g-label">Item<input class="g-input" data-f="name" value="' + esc(name.textContent) + '"></label>' +
        '<label class="g-label">Amount<input class="g-input" data-f="amount" value="' + esc(amount.textContent) + '"></label>';
      btn.hidden = true;
      return;
    }
    const buttons = (li || card).querySelectorAll('button');
    buttons.forEach(b => { b.disabled = true; });
    try {
      if (act === 'keep-all') {
        await receiptPost('/api/inventory/receipts/' + encodeURIComponent(card.dataset.receipt) + '/keep-all');
        receiptItems = receiptItems.filter(r => r.receipt !== card.dataset.receipt);
        card.remove();
        return;
      }
      const pid = Number(li.dataset.pid);
      const f = (n) => li.querySelector('[data-f="' + n + '"]');
      const payload = act === 'keep' && f('name') ? { name: f('name').value, amount: f('amount').value } : {};
      await receiptPost('/api/inventory/receipt-items/' + pid + '/' + (act === 'keep' ? 'keep' : 'discard'), payload);
      receiptItems = receiptItems.filter(r => r.id !== pid);
      li.remove();
      if (!card.querySelector('.rc-item')) card.remove();
    } catch (ex) {
      buttons.forEach(b => { b.disabled = false; });
      if (err) { err.textContent = ex.message; err.hidden = false; } else alert(ex.message);
    }
  }

  async function memoryPost(id, act, payload) {
    const r = await fetch('/api/memory/inbox/' + encodeURIComponent(id) + '/' + act,
      { method: 'POST', headers: MEMORY_HEADERS, body: JSON.stringify(payload || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }

  async function onMemory(e) {
    const btn = e.target.closest('button[data-mem]');
    if (!btn) return;
    const li = btn.closest('.mem-item');
    const id = li.dataset.id;
    const err = li.querySelector('[data-err]');
    const act = btn.dataset.mem;
    if (act === 'edit') {
      const text = li.querySelector('.mem-text');
      text.innerHTML = '<textarea class="g-input mem-edit" rows="3">' + esc(text.textContent) + '</textarea>';
      text.querySelector('textarea').focus();
      btn.hidden = true;
      li.querySelector('[data-mem="keep"]').textContent = 'Keep my words';
      return;
    }
    const buttons = li.querySelectorAll('button');
    buttons.forEach(b => { b.disabled = true; });
    try {
      const edited = li.querySelector('.mem-edit');
      const j = await memoryPost(id, act === 'keep' ? 'keep' : 'discard',
        act === 'keep' && edited ? { text: edited.value } : {});
      inbox.proposals = inbox.proposals.filter(p => p.id !== id);
      inbox.count = j.left;
      li.remove();
      if (!inbox.proposals.length) {
        if (inbox.count) refresh();                 // more were waiting beyond the first page
        else document.getElementById('memCard')?.remove();
      }
    } catch (ex) {
      buttons.forEach(b => { b.disabled = false; });
      err.textContent = ex.message;
      err.hidden = false;
    }
  }

  function render(t) {
    let h = memoryCard() + docCards() + receiptCards();

    const rem = t.reminders || {};
    h += card('Reminders', failed(rem) || list((rem.items || []).map(r =>
      '<li><span>' + esc(r.text) + '</span><span class="when' + (r.overdue ? ' late' : '') + '">' +
      (r.overdue ? 'overdue · ' : '') + time(r.due_at) + '</span></li>'), 'Nothing due today.'));

    const gl = t.goals || {};
    if (gl.error) h += card('Goals', failed(gl));
    else if ((gl.active || []).length || gl.drafts) {
      const rows = [];
      (gl.overdue || []).forEach(s => rows.push('<li><span>' + esc(s.text) + ' <span class="muted">· ' + esc(s.goal_title) +
        '</span></span><span class="when late">overdue ' + s.days + 'd</span></li>'));
      (gl.today || []).forEach(s => rows.push('<li><span>' + esc(s.text) + ' <span class="muted">· ' + esc(s.goal_title) +
        '</span></span><span class="when">today</span></li>'));
      const shown = new Set([...(gl.overdue || []), ...(gl.today || [])].map(s => s.id));
      (gl.active || []).forEach(g => {
        if (g.next && !shown.has(g.next.id)) rows.push('<li><span>' + esc(g.next.text) + ' <span class="muted">· ' +
          esc(g.title) + '</span></span><span class="when">' + g.done + '/' + g.total + '</span></li>');
      });
      h += card('Goals', (gl.drafts ? '<p class="t-err" style="margin:0 0 10px">' + gl.drafts + ' draft' +
        (gl.drafts > 1 ? 's' : '') + ' waiting for approval.</p>' : '') + list(rows, 'Nothing due.') +
        '<p class="meta" style="margin-top:10px"><a href="#goals">Open goals</a></p>');
    }

    const cal = t.calendar || {};
    h += card('Calendar', failed(cal) || list((cal.events || []).map(e =>
      '<li><span>' + esc(e.summary) + (e.location ? ' <span class="muted">· ' + esc(e.location) + '</span>' : '') +
      '</span><span class="when">' + (e.all_day ? 'all day' : time(e.start)) + '</span></li>'), 'Nothing on the calendar.'));

    const q = t.queue || {};
    if (q.error) h += card('House and garden', failed(q));
    else {
      const cols = q.columns || {};
      const home = (cols.home || []).map(i => '<li><span>' + esc(i.title) + '</span><span class="when">' + esc(i.sub) + '</span></li>');
      const hands = (cols.hands || []).map(i => '<li><span>' + esc(i.title) + '</span><span class="when">' + esc(i.sub) + '</span></li>');
      h += card('House and garden', (q.headline ? '<p style="margin:0 0 10px">' + esc(q.headline) + '</p>' : '') +
        list(home, 'All quiet.'));
      h += card('Chores', list(hands, 'No chores queued.') +
        '<p class="meta" style="margin-top:10px"><a href="/queue" data-capture-link>Open the queue</a> to mark things done.</p>');
    }

    const m = t.maintenance || {};
    h += card('Maintenance', failed(m) || list((m.assets || []).map(a =>
      '<li><span>' + esc(a.name) + '</span><span class="when late">' +
      esc(a.reason || (a.days_since != null ? a.days_since + 'd overdue' : 'due')) + '</span></li>'), 'All assets current.'));

    const shop = t.shopping || {};
    h += card('Shopping list', failed(shop) || list((shop.items || []).map(i => '<li><span>' + esc(i) + '</span></li>'), 'The list is empty.'));

    h += '<div class="foot-note">' + esc(t.date || '') + ' · read at ' + new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + '</div>';
    body().innerHTML = h;
    // The queue link carries the NFC token when this phone has one, the same as the menu entry.
    body().querySelector('[data-capture-link]')?.addEventListener('click', (e) => {
      const token = getToken('nfc', { ask: false });
      if (token) { e.preventDefault(); location.href = '/queue?token=' + encodeURIComponent(token); }
    });
  }

  async function refresh() {
    // The review queue loads beside /today, never instead of it: either can fail alone.
    const inboxReq = fetch('/memory/inbox?limit=20').then(r => r.ok ? r.json() : null).catch(() => null);
    const receiptReq = fetch('/api/inventory/receipt-items').then(r => r.ok ? r.json() : null).catch(() => null);
    const docsReq = fetch('/api/docs/proposed').then(r => r.ok ? r.json() : null).catch(() => null);
    try {
      const r = await fetch('/today');
      if (!r.ok) throw new Error('today ' + r.status);
      const t = await r.json();
      inbox = await inboxReq;
      receiptItems = (await receiptReq)?.items || null;
      houseDocs = (await docsReq)?.docs || null;
      render(t);
    } catch (e) {
      body().innerHTML = '<div class="empty">Couldn’t load today: ' + esc(e.message) +
        '. Is the brain up and are you on the tailnet?</div>';
    }
  }

  Hestia.views.today = { title: 'Today', enter: refresh, resume: refresh };
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('todayRefresh').addEventListener('click', refresh);
    body().addEventListener('click', onMemory);
    body().addEventListener('click', onReceipt);
    body().addEventListener('click', onDoc);
  });
})();
