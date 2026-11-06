// Chat view. Conversations live on the brain (GET/POST /v1/threads), so the phone, the
// terminal and any other window see the same threads, and clearing Safari loses nothing. The
// client sends only the new message; the brain adds the history and trims it to its own budget.
'use strict';

const ACTIVE_KEY = 'hestia.active_thread.v2';
const NARRATE_KEY = 'hestia.narration.v1';
const LEGACY_THREADS_KEY = 'hestia.threads.v1';
const LEGACY_CHAT_KEY = 'hestia.chat.v1';
const MIGRATED_KEY = 'hestia.threads.migrated.v1';

// One-tap starters on an empty conversation. `view` opens a no-model view instead of asking
// the brain; `fill` puts words in the box and waits for the rest.
const QUICK = [
  { label: "What's due today?", view: 'today', hint: 'no AI' },
  { label: "How's the garden doing?", ask: "How's the garden doing?" },
  { label: 'Weather tomorrow', ask: "What's the weather tomorrow?" },
  { label: 'Lights off…', fill: 'Turn off the ' },
];

const $ = (id) => document.getElementById(id);
let log, box, sendBtn, narrateBtn;
let threads = [];
let messages = [];            // the active thread's messages, as the brain last returned them
let activeId = store.get(ACTIVE_KEY) || 'main';
let retryTurn = null;
let inFlight = false;         // one turn at a time; Enter and the mic wait for it
let activeSpeakBtn = null;
let ttsAudio = null;          // one reused <audio>, unlocked on tap
let autoNarrate = store.get(NARRATE_KEY) === 'true';

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: opts.body ? { 'Content-Type': 'application/json', ...(opts.headers || {}) } : opts.headers,
  });
  let data = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) throw new Error((data && (data.error || data.detail)) || 'brain returned ' + r.status);
  return data;
}

// --- Threads ---
async function loadThreads() {
  threads = (await api('/v1/threads')).threads || [];
  if (!threads.some(t => t.id === activeId)) activeId = 'main';
  renderThreadList();
}

async function openThread(id) {
  activeId = id;
  store.set(ACTIVE_KEY, id);
  retryTurn = null;
  stopSpeaking();
  try {
    messages = (await api('/v1/threads/' + encodeURIComponent(id))).messages || [];
  } catch (e) {
    messages = [];
    render();
    bubble('err', "Couldn't load this conversation: " + e.message + '.');
    return;
  }
  render();
  renderThreadList();
}

// The first load on a phone that kept history in localStorage uploads it once, as its own
// threads, then never reads localStorage for history again. The old keys are left in place.
async function migrateLocalHistory() {
  if (store.get(MIGRATED_KEY)) return;
  let old = null;
  try { old = JSON.parse(store.get(LEGACY_THREADS_KEY)); } catch {}
  if (!Array.isArray(old)) {
    let legacy = null;
    try { legacy = JSON.parse(store.get(LEGACY_CHAT_KEY)); } catch {}
    old = Array.isArray(legacy) ? [{ id: 'main', messages: legacy }] : [];
  }
  // Each uploaded thread is ticked off as it lands, so a failure halfway never duplicates.
  const doneKey = MIGRATED_KEY + '.done';
  let done = [];
  try { done = JSON.parse(store.get(doneKey)) || []; } catch {}
  for (const t of old) {
    const msgs = (t.messages || []).filter(m => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string')
      .map(m => ({ role: m.role, content: m.content }));
    if (!msgs.length || done.includes(t.id)) continue;
    const title = t.id === 'main' ? 'Earlier chat (this phone)' : (t.title || 'Earlier side chat');
    await api('/v1/threads', { method: 'POST', body: JSON.stringify({ title, messages: msgs }) });
    done.push(t.id);
    store.set(doneKey, JSON.stringify(done));
  }
  store.set(MIGRATED_KEY, String(Date.now()));
}

function renderThreadList() {
  const list = $('threadList');
  list.replaceChildren();
  for (const t of threads) {
    const isMain = t.id === 'main';
    const row = document.createElement('div');
    row.className = 't-item' + (t.id === activeId ? ' active' : '');
    const main = document.createElement('button');
    main.className = 't-main';
    if (t.id === activeId) main.setAttribute('aria-current', 'true');
    main.innerHTML = '<span class="t-icon">' + (isMain ? ICONS.threadMain : ICONS.threadSide) + '</span>' +
      '<span class="t-info"><span class="t-title" style="display:block">' + esc(t.title || 'New chat') + '</span>' +
      '<span class="t-meta" style="display:block">' + (t.count ? t.count + ' message' + (t.count === 1 ? '' : 's') : 'Empty') + '</span></span>';
    main.addEventListener('click', () => { Hestia.closeMenu(); go('chat'); if (t.id !== activeId) openThread(t.id); });
    row.appendChild(main);
    if (!isMain) {
      const del = document.createElement('button');
      del.className = 't-del';
      del.setAttribute('aria-label', 'Delete conversation ' + (t.title || ''));
      del.innerHTML = ICONS.close;
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (t.count && !confirm('Delete "' + (t.title || 'this conversation') + '"?')) return;
        try {
          await api('/v1/threads/' + encodeURIComponent(t.id), { method: 'DELETE' });
          if (t.id === activeId) await openThread('main');
          await loadThreads();
        } catch (err) { bubble('err', "Couldn't delete it: " + err.message + '.'); }
      });
      row.appendChild(del);
    }
    list.appendChild(row);
  }
  updateThreadTag();
}

function updateThreadTag() {
  const tag = $('threadTag');
  const t = threads.find(x => x.id === activeId);
  const show = Hestia.current === 'chat' && t && t.id !== 'main';
  tag.hidden = !show;
  tag.textContent = show ? (t.title || 'New chat') : '';
}

async function newThread() {
  const cur = threads.find(t => t.id === activeId);
  if (cur && cur.id !== 'main' && !cur.count) { box.focus(); return; }
  try {
    const t = await api('/v1/threads', { method: 'POST', body: JSON.stringify({}) });
    await loadThreads();
    await openThread(t.id);
    box.focus();
  } catch (e) { bubble('err', "Couldn't start a conversation: " + e.message + '.'); }
}

async function clearThread() {
  if (messages.length && !confirm('Clear this conversation?')) return;
  try {
    await api('/v1/threads/' + encodeURIComponent(activeId) + '/messages', { method: 'DELETE' });
    messages = [];
    retryTurn = null;
    stopSpeaking();
    render();
    loadThreads();
  } catch (e) { bubble('err', "Couldn't clear it: " + e.message + '.'); }
}

// --- Rendering ---
function render() {
  log.replaceChildren();
  if (!messages.length) { renderEmpty(); return; }
  for (const m of messages) {
    const d = bubble(m.role === 'user' ? 'me' : 'you', m.content);
    if (m.role === 'assistant') addReceipts(d, m.receipts);
  }
  log.scrollTop = log.scrollHeight;
}

function renderEmpty() {
  const e = document.createElement('div');
  e.className = 'empty';
  e.innerHTML = '<div>' + (activeId === 'main'
    ? 'Ask about the house, the garden, the media library, or anything else.'
    : 'A fresh conversation. It shares Hestia’s memory, not this chat’s history.') + '</div>';
  const quick = document.createElement('div');
  quick.className = 'quick';
  for (const q of QUICK) {
    const b = document.createElement('button');
    b.type = 'button';
    b.innerHTML = esc(q.label) + (q.hint ? '<small>' + esc(q.hint) + '</small>' : '');
    b.addEventListener('click', () => {
      if (q.view) go(q.view);
      else if (q.ask) submit({ text: q.ask });
      else { box.value = q.fill; box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
    });
    quick.appendChild(b);
  }
  e.appendChild(quick);
  log.appendChild(e);
}

function bubble(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  if (cls === 'you') {
    const content = document.createElement('div');
    content.className = 'msg-content';
    content.innerHTML = formatMarkdown(text);
    d.appendChild(content);
    attachActions(d, text);
  } else {
    d.textContent = text;
  }
  log.querySelector('.empty')?.remove();
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

// The brain's own record of what it did, one chip per action: did the light actually go off.
function addReceipts(msgEl, receipts) {
  if (!receipts || !receipts.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'receipts';
  wrap.setAttribute('role', 'list');
  wrap.setAttribute('aria-label', 'What Hestia did');
  for (const r of receipts) {
    const status = ['succeeded', 'failed'].includes(r.status) ? r.status : 'unknown';
    const word = { succeeded: 'Done', failed: 'Failed', unknown: 'Unconfirmed' }[status];
    const chip = document.createElement('span');
    chip.className = 'receipt ' + status;
    chip.setAttribute('role', 'listitem');
    chip.title = r.text || '';
    chip.innerHTML = (status === 'succeeded' ? ICONS.check : status === 'failed' ? ICONS.cross : ICONS.question) +
      '<span><span class="sr-only">' + word + ': </span>' + esc(r.text || r.tool) + '</span>';
    wrap.appendChild(chip);
  }
  const actions = msgEl.querySelector('.msg-actions');
  msgEl.insertBefore(wrap, actions);
}

function attachActions(msgEl, text) {
  if (!text || text === '(empty reply)') return;
  const actions = document.createElement('div');
  actions.className = 'msg-actions';
  const copyBtn = document.createElement('button');
  copyBtn.className = 'act-btn';
  copyBtn.innerHTML = ICONS.copy + ' <span>Copy</span>';
  copyBtn.addEventListener('click', async () => {
    let ok = true;
    try { await navigator.clipboard.writeText(text); } catch { ok = false; }
    copyBtn.innerHTML = (ok ? ICONS.check : ICONS.close) + ' <span>' + (ok ? 'Copied' : 'Failed') + '</span>';
    setTimeout(() => { copyBtn.innerHTML = ICONS.copy + ' <span>Copy</span>'; }, 1500);
  });
  const speakBtn = document.createElement('button');
  speakBtn.className = 'act-btn';
  speakBtn.innerHTML = ICONS.speak + ' <span>Speak</span>';
  speakBtn.addEventListener('click', () => toggleSpeakMessage(text, speakBtn));
  actions.append(copyBtn, speakBtn);
  msgEl.appendChild(actions);
}

// --- Sending ---
// A 409 on a retry means the brain still holds the key without an answer: the first attempt
// was cut off mid-turn and is never replayed (it may have acted). Read the receipts it left so
// the thread says what happened, instead of offering a retry that can only 409 again.
async function interruptedNote(key) {
  let ops = [];
  try {
    const r = await fetch('/v1/operations/' + encodeURIComponent(key));
    if (r.ok) ops = (await r.json()).operations || [];
  } catch {}
  const did = ops.map(o => o.name + ' (' + o.status + ')').join(', ');
  return "That request was interrupted before it finished, so it won't be repeated. " +
    (did ? 'Actions it recorded: ' + did + '.' : 'It recorded no actions.') +
    ' Ask again if you still want it.';
}

async function submit({ viaVoice = false, text: given = null } = {}) {
  const text = (given ?? box.value).trim();
  if (!text) return;
  if (inFlight) {                 // one turn at a time: keep the words, send when this one lands
    if (given !== null) box.value = box.value.trim() ? box.value.trimEnd() + ' ' + text : text;
    return;
  }
  if (Hestia.current !== 'chat') go('chat');
  inFlight = true;
  if (given === null) { box.value = ''; box.style.height = 'auto'; }

  const threadId = activeId;
  const retry = retryTurn && retryTurn.text === text && retryTurn.threadId === threadId;
  if (!retry) {
    // A different message after a failure replaces the failed one: the brain never stored it,
    // so the screen should not keep an unanswered question the user has already rewritten.
    if (retryTurn && retryTurn.threadId === threadId && messages[messages.length - 1] === retryTurn.msg) {
      messages.pop();
      render();
    }
    const msg = { role: 'user', content: text };
    messages.push(msg);
    bubble('me', text);
    const rawKey = crypto.randomUUID ? crypto.randomUUID() :
      Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    retryTurn = {
      text, msg, threadId,
      key: rawKey.replace(/[^A-Za-z0-9_-]/g, ''),
      payload: { thread_id: threadId, messages: [msg], stream: true, hestia_events: true },
    };
  }
  const turn = retryTurn;
  stopSpeaking();

  sendBtn.disabled = true;
  const pending = document.createElement('div');
  pending.className = 'msg you pending';
  pending.textContent = 'Thinking…';
  log.appendChild(pending);
  Hestia.ember.set('turn', 'thinking');
  log.scrollTop = log.scrollHeight;
  let contentEl = null;
  const here = () => activeId === threadId;
  function settle(reply) {
    if (!here()) return;
    if (!contentEl) {
      pending.classList.remove('pending');
      pending.classList.add('streaming');
      Hestia.ember.set('turn', 'answering');
      pending.textContent = '';
      contentEl = document.createElement('div');
      contentEl.className = 'msg-content';
      pending.appendChild(contentEl);
    }
    contentEl.innerHTML = formatMarkdown(reply);
    log.scrollTop = log.scrollHeight;
  }
  function progress(status) {
    if (!here() || contentEl) return;
    pending.textContent = status;
    announce(status);
  }

  try {
    const r = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': turn.key },
      body: JSON.stringify(turn.payload),
    });
    let reply = '', receipts = null;
    if (r.status === 409 && retry) {
      reply = await interruptedNote(turn.key);
    } else if (!r.ok) {
      let detail = 'brain returned ' + r.status;
      try { const j = await r.json(); if (j.error) detail = j.error; } catch {}
      throw new Error(detail);
    } else if (r.body && r.body.getReader) {
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const data = trimmed.slice(5).trim();
          if (data === '[DONE]') continue;
          let ev;
          try { ev = JSON.parse(data); } catch { continue; }
          if (ev.object === 'hestia.status') { progress(ev.status); continue; }
          if (ev.receipts) receipts = ev.receipts;
          const delta = ev.choices?.[0]?.delta?.content || '';
          if (delta) { reply += delta; settle(reply); }
        }
      }
    } else {
      const data = await r.json();
      reply = data.choices?.[0]?.message?.content || '';
      receipts = data.receipts || null;
    }

    retryTurn = null;
    reply = reply.trim() || '(empty reply)';
    if (here()) {
      messages.push({ role: 'assistant', content: reply, receipts });
      settle(reply);
      addReceipts(pending, receipts);
      attachActions(pending, reply);
      announce(reply);
      if (autoNarrate || viaVoice) speak(reply);
    }
    if (inApp() && (receipts || []).some(r => r.tool === 'reminder' && r.status === 'succeeded')) {
      hestiaBridge.send({ type: 'sync_notifications' });   // the app schedules the new reminder now
    }
    Hestia.ember.set('brain', true);
    loadThreads().catch(() => {});      // titles and counts are the brain's to set
  } catch (e) {
    if (here()) {
      pending.remove();
      bubble('err', 'Could not get the reply: ' + e.message + '. Press Send to retry this same request safely.');
      if (!box.value.trim()) box.value = text;
    }
    Hestia.ember.set('brain', false);
  } finally {
    pending.classList.remove('pending', 'streaming');
    Hestia.ember.set('turn', 'idle');
    inFlight = false;
    sendBtn.disabled = false;
  }
}

// --- Voice: tap the mic to record, tap again to stop. The clip goes to the brain's STT proxy,
// the transcript is sent as a normal turn, and the reply is spoken back via the TTS proxy.
// getUserMedia needs a secure context, so over plain http we point at the https:// URL. ---
let recorder = null, chunks = [], recording = false;
async function toggleMic() {
  if (inApp()) { appToggleMic(); return; }
  stopSpeaking();
  const mic = $('mic');
  if (recording) { recorder.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia) {
    bubble('err', 'The mic needs a secure connection: open Hestia over the Tailscale https:// address, not the raw IP.');
    return;
  }
  if (!ttsAudio) ttsAudio = new Audio();   // created inside the gesture = allowed to play later
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      recording = false;
      mic.classList.remove('rec');
      mic.setAttribute('aria-label', 'Talk to Hestia');
      stream.getTracks().forEach((t) => t.stop());
      transcribeAndSend(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }));
    };
    recorder.start();
    recording = true;
    mic.classList.add('rec');
    mic.setAttribute('aria-label', 'Stop recording');
    announce('Listening');
  } catch (e) {
    bubble('err', "Couldn't access the mic: " + e.message);
  }
}

async function transcribeAndSend(blob) {
  const pending = bubble('note', 'Listening…');
  try {
    const fd = new FormData();
    fd.append('file', blob, 'clip.webm');          // ffmpeg sniffs the real container server-side
    const r = await fetch('/v1/audio/transcriptions', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('STT ' + r.status);
    const text = ((await r.json()).text || '').trim();
    pending.remove();
    if (!text) { bubble('err', "Didn't catch that. Try again, a bit closer to the mic."); return; }
    submit({ viaVoice: true, text });               // send it AND speak the reply
  } catch (e) {
    pending.remove();
    bubble('err', 'Transcription failed: ' + e.message + '. Is the STT service up?');
  }
}

function stopSpeaking() {
  if (inApp()) { appStopSpeaking(); return; }
  if (ttsAudio && !ttsAudio.paused) { ttsAudio.pause(); ttsAudio.currentTime = 0; }
  resetActiveSpeaker();
}
function resetActiveSpeaker() {
  if (activeSpeakBtn) { activeSpeakBtn.innerHTML = ICONS.speak + ' <span>Speak</span>'; activeSpeakBtn = null; }
}
async function toggleSpeakMessage(text, btn) {
  if (inApp()) { appToggleSpeak(text, btn); return; }
  if (ttsAudio && !ttsAudio.paused && activeSpeakBtn === btn) { stopSpeaking(); return; }
  resetActiveSpeaker();
  activeSpeakBtn = btn;
  btn.innerHTML = ICONS.stop + ' <span>Stop</span>';
  await speak(text);
}
async function speak(text) {
  if (inApp()) { appSpeak(text); return; }
  if (!ttsAudio) ttsAudio = new Audio();
  try {
    const r = await fetch('/v1/audio/speech', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: text }),
    });
    if (!r.ok) { resetActiveSpeaker(); return; }
    const url = URL.createObjectURL(await r.blob());
    ttsAudio.src = url;
    ttsAudio.onended = () => { URL.revokeObjectURL(url); resetActiveSpeaker(); };
    ttsAudio.onerror = resetActiveSpeaker;
    await ttsAudio.play().catch(resetActiveSpeaker);
  } catch { resetActiveSpeaker(); }
}

// --- Voice inside the iPhone app: the same buttons, through the app's on-device recognition
// and voice (bridge.js; contract in PHONE-APP.md, "The web view"). Nothing here runs in a
// browser, and after a bridge version mismatch the box's STT and TTS take over again. ---
const inApp = () => window.hestiaBridge?.active === true;
let appListening = null;       // the 'Listening…' note while the app is recognizing, else null
let appSpeakingId = null;      // the id of the reply the app is speaking, else null
let appSpeakSeq = 0;

function appMicDone() {
  const mic = $('mic');
  mic.classList.remove('rec');
  mic.setAttribute('aria-label', 'Talk to Hestia');
  const note = appListening;
  appListening = null;
  return note;
}
function appToggleMic() {
  if (appListening) { hestiaBridge.send({ type: 'listen_stop' }); return; }
  appStopSpeaking();
  if (hestiaBridge.speech === false) { appSpeechUnavailable(); return; }
  if (!hestiaBridge.send({ type: 'listen' })) { bubble('err', "Couldn't reach the app's speech recognition. Typing still works."); return; }
  appListening = bubble('note', 'Listening…');
  const mic = $('mic');
  mic.classList.add('rec');
  mic.setAttribute('aria-label', 'Stop recording');
  announce('Listening');
}
function appSpeechUnavailable() {
  bubble('err', "This phone can't recognize speech on the device, so the mic is off. Typing still works.");
}
function appOnTranscript(m) {
  if (!appListening) return;
  const text = String(m.text || '').trim();
  if (!m.final) { if (text) appListening.textContent = text + '…'; return; }
  appMicDone().remove();
  if (!text) { bubble('err', "Didn't catch that. Try again, a bit closer to the mic."); return; }
  submit({ viaVoice: true, text });               // send it AND speak the reply
}
function appOnListenError(m) {
  const note = appMicDone();
  if (note) note.remove();
  if (m.code === 'unavailable') { hestiaBridge.speech = false; appSpeechUnavailable(); }
  else if (m.code === 'denied') bubble('err', "Hestia isn't allowed to use the mic or speech recognition. Turn them on in Settings, Hestia. Typing still works.");
  else bubble('err', 'Speech recognition stopped: ' + (m.message || m.code || 'unknown error') + '.');
}

// The app gets plain words: the reply as it reads on screen, without markdown marks.
function speakableText(text) {
  const d = document.createElement('div');
  d.innerHTML = formatMarkdown(text);
  return (d.textContent || '').trim();
}
function appSpeak(text) {
  const words = speakableText(text);
  if (!words) { resetActiveSpeaker(); return; }
  if (appSpeakingId) hestiaBridge.send({ type: 'speak_stop' });
  appSpeakingId = String(++appSpeakSeq);
  if (!hestiaBridge.send({ type: 'speak', id: appSpeakingId, text: words })) { appSpeakingId = null; resetActiveSpeaker(); }
}
function appStopSpeaking() {
  if (appSpeakingId) hestiaBridge.send({ type: 'speak_stop' });
  appSpeakingId = null;
  resetActiveSpeaker();
}
function appToggleSpeak(text, btn) {
  if (appSpeakingId && activeSpeakBtn === btn) { appStopSpeaking(); return; }
  resetActiveSpeaker();
  activeSpeakBtn = btn;
  btn.innerHTML = ICONS.stop + ' <span>Stop</span>';
  appSpeak(text);
}
function appOnSpeakFinished(m) {
  if (m.id !== appSpeakingId) return;     // a reply that was already replaced or stopped
  appSpeakingId = null;
  resetActiveSpeaker();
}
if (window.hestiaBridge?.active) {
  hestiaBridge.on('transcript', appOnTranscript);
  hestiaBridge.on('listen_error', appOnListenError);
  hestiaBridge.on('speak_finished', appOnSpeakFinished);
  hestiaBridge.on('mismatch', () => { const note = appMicDone(); if (note) note.remove(); appSpeakingId = null; resetActiveSpeaker(); });
}

function updateNarrateUI() {
  narrateBtn.setAttribute('aria-pressed', String(autoNarrate));
  narrateBtn.innerHTML = autoNarrate ? ICONS.speakerOn : ICONS.speakerMute;
  narrateBtn.setAttribute('aria-label', autoNarrate ? 'Reading replies aloud (tap to stop)' : 'Read replies aloud');
}

// --- Photo: the same /ingest/photo the Shortcut uses, with the same token. ---
function setupPhoto() {
  const input = $('photoInput'), sheet = $('photoSheet');
  let file = null, previewUrl = null;
  $('photoBtn').addEventListener('click', () => {
    if (!getToken('ingest')) return;
    input.value = '';
    input.click();
  });
  input.addEventListener('change', () => {
    file = input.files[0];
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    $('photoPreview').src = previewUrl;
    $('photoDomain').value = store.get('hestia.photo.domain') || 'garden';
    sheet.showModal();
    $('photoSubject').focus();
  });
  sheet.addEventListener('close', async () => {
    if (sheet.returnValue !== 'upload' || !file) return;
    const subject = $('photoSubject').value.trim();
    if (!subject) return;
    const domain = $('photoDomain').value;
    store.set('hestia.photo.domain', domain);
    const fd = new FormData();
    fd.append('file', file, file.name || 'photo.jpg');
    fd.append('subject', subject);
    fd.append('domain', domain);
    fd.append('caption', $('photoCaption').value.trim());
    const note = bubble('note', 'Filing the photo…');
    try {
      const r = await fetch('/ingest/photo', { method: 'POST', body: fd, headers: { 'X-Ingest-Token': getToken('ingest', { ask: false }) } });
      const data = await r.json().catch(() => ({}));
      if (r.status === 401) {
        forgetToken('ingest');
        throw new Error('the brain rejected the token, so this phone forgot it. Tap the camera to enter it again');
      }
      if (!r.ok) throw new Error(data.error || 'brain returned ' + r.status);
      const names = (data.filed || []).map(f => f.subject).join(', ');
      note.textContent = 'Photo filed to ' + names + ' (' + domain + ').' + (data.warning ? '\n' + data.warning : '');
      announce(note.textContent);
      $('photoSubject').value = '';
      $('photoCaption').value = '';
    } catch (e) {
      note.className = 'msg err';
      note.textContent = 'The photo was not filed: ' + e.message + '.';
    }
  });
}

// --- Wiring ---
Hestia.views.chat = {
  title: 'Chat',
  enter() { if (!log) return; updateThreadTag(); log.scrollTop = log.scrollHeight; },
  leave() { $('threadTag').hidden = true; },
};

document.addEventListener('DOMContentLoaded', async () => {
  log = $('log'); box = $('box'); sendBtn = $('send'); narrateBtn = $('narrateBtn');
  updateNarrateUI();
  sendBtn.addEventListener('click', () => submit());
  box.addEventListener('keydown', (e) => {
    // Enter sends; Shift+Enter newlines. (On phones the on-screen Return key inserts a newline.)
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  box.addEventListener('input', () => {
    box.style.height = 'auto';
    box.style.height = Math.min(box.scrollHeight, 140) + 'px';
  });
  narrateBtn.addEventListener('click', () => {
    autoNarrate = !autoNarrate;
    store.set(NARRATE_KEY, String(autoNarrate));
    updateNarrateUI();
    if (!autoNarrate) stopSpeaking();
  });
  $('mic').addEventListener('click', toggleMic);
  $('headerNewBtn').addEventListener('click', newThread);
  $('menuNewThreadBtn').addEventListener('click', () => { Hestia.closeMenu(); go('chat'); newThread(); });
  $('clearThreadBtn').addEventListener('click', () => { Hestia.closeMenu(); go('chat'); clearThread(); });
  setupPhoto();

  try {
    await migrateLocalHistory();
  } catch (e) {
    bubble('err', "Couldn't move this phone's old chats to the brain yet (" + e.message + '). They are still on the phone; it will try again next time.');
  }
  try {
    await loadThreads();
    await openThread(activeId);
  } catch (e) {
    render();
    bubble('err', "Couldn't load conversations: " + e.message + '. Is the brain up and are you on the tailnet?');
  }
});
