// The native bridge, for when /app runs inside the iPhone app's web view. The contract is in
// PHONE-APP.md, "The web view". In a browser there is no window.webkit.messageHandlers.hestia,
// so this file defines an inactive window.hestiaBridge and does nothing else: no messages, no
// DOM changes, no listeners.
'use strict';

(function () {
  const V = 1;
  const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.hestia;
  const listeners = {};
  const last = {};               // the latest hello and connection, replayed to late listeners
  const STATE = ['hello', 'connection'];

  const bridge = {
    v: V,
    active: !!handler,           // false in a browser, and after a version mismatch
    speech: null,                // on-device recognition: null until the app says, then true/false
    appVersion: null,
    mismatch: null,              // the app's contract version when it differs from ours
    send(msg) {
      if (!bridge.active) return false;
      try { handler.postMessage(Object.assign({ v: V }, msg)); return true; } catch { return false; }
    },
    on(type, fn) {
      (listeners[type] = listeners[type] || []).push(fn);
      if (last[type]) fn(last[type]);
    },
    // The one entry point the app calls: window.hestiaBridge.receive({v: 1, type: ...}).
    receive(msg) {
      if (!handler) return;
      if (typeof msg === 'string') { try { msg = JSON.parse(msg); } catch { return; } }
      if (!msg || typeof msg.type !== 'string') return;
      if (msg.v !== V) { versionMismatch(msg.v); return; }
      if (msg.type === 'hello') {
        bridge.speech = msg.speech === true;
        bridge.appVersion = msg.app || null;
      }
      if (msg.type === 'connection') showConnection(msg);
      if (STATE.includes(msg.type)) last[msg.type] = msg;
      for (const fn of listeners[msg.type] || []) { try { fn(msg); } catch {} }
    },
  };
  window.hestiaBridge = bridge;
  if (!handler) return;

  // The phone's model socket, shown as a small tag beside the brain's dot.
  function tag() { return document.getElementById('phoneTag'); }
  function setTag(cls, label, title) {
    const el = tag();
    if (!el) return;
    el.hidden = false;
    el.className = 'phone-tag ' + cls;
    el.textContent = label;
    el.title = title;
    el.setAttribute('aria-label', title);
  }
  function showConnection(msg) {
    const detail = msg.detail ? ' (' + msg.detail + ')' : '';
    if (msg.state === 'connected') {
      setTag('ok', 'model ready', 'The phone is running the model' + detail);
    } else if (msg.state === 'downloading') {
      const p = typeof msg.progress === 'number' ? Math.max(0, Math.min(1, msg.progress)) : null;
      setTag('warn', p === null ? 'model …' : 'model ' + Math.floor(p * 100) + '%',
        'The phone is downloading the model' + (p === null ? '' : ', ' + Math.floor(p * 100) + ' percent') +
        '. Keep the app open' + detail);
    } else {
      setTag('bad', 'model offline', 'The phone is not connected to the box, so chat waits for it' + detail);
    }
  }
  function versionMismatch(appV) {
    bridge.active = false;
    bridge.mismatch = appV;
    for (const fn of listeners.mismatch || []) { try { fn({ type: 'mismatch', v: appV }); } catch {} }
    const show = () => setTag('bad', 'update app',
      'The app speaks bridge version ' + appV + ' and this page speaks ' + V + '. Speech and the model status are off until they match.');
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', show); else show();
  }

  // Say hello once the page is parsed; the app answers with hello and the connection state.
  const hello = () => bridge.send({ type: 'hello' });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', hello); else hello();
})();
