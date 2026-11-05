// Status view: the visual half of the `status` tool. Same /status snapshot, rendered as pills
// and bars. Polls every 15s only while the view is showing and the app is in the foreground.
'use strict';

(() => {
  const POLL_MS = 15000;
  let timer = null;
  const body = () => document.getElementById('statusBody');

  const clamp = (n) => Math.max(0, Math.min(100, Math.round(n)));
  const barClass = (v, warn, crit) => v >= crit ? 'bar crit' : v >= warn ? 'bar warn' : 'bar';
  function bar(label, v, warn, crit) {
    v = clamp(v);
    return '<div class="row"><div class="metric"><span class="k">' + esc(label) + '</span><span>' + v +
      '%</span></div><div class="' + barClass(v, warn, crit) + '" role="img" aria-label="' + esc(label) + ' ' + v +
      ' percent"><i style="width:' + v + '%"></i></div></div>';
  }
  const pill = (cls, label, sub) => '<span class="pill ' + cls + '"><span class="pdot"></span>' + esc(label) +
    (sub ? ' <span class="sub">' + esc(sub) + '</span>' : '') + '</span>';

  function render(s, maint, inbox) {
    const down = s.services.filter(x => !x.up).map(x => x.name);
    if (!s.brain.ollama_up) down.push('Ollama');
    const ok = down.length === 0;
    let h = '<div class="s-headline ' + (ok ? 'ok' : 'bad') + '" role="status">' +
      (ok ? 'All systems nominal' : down.length + ' issue' + (down.length > 1 ? 's' : '') + ': ' + esc(down.join(', ')) + ' down') + '</div>';

    h += '<div class="card"><h2>Services</h2><div class="pills">' +
      s.services.map(x => pill(x.up ? 'up' : 'down', x.name, x.detail)).join('') + '</div></div>';

    if (maint) {
      h += '<div class="card"><h2>Maintenance</h2><div class="pills">';
      h += maint.count > 0
        ? (maint.assets || []).map(a => pill('warn', a.name, a.reason || (a.days_since != null ? a.days_since + 'd overdue' : 'due'))).join('')
        : pill('up', 'All assets current');
      h += '</div></div>';
    }

    // Read-only on purpose: promoting a proposal into memory stays a human act at the CLI
    // (review_notes.py), so this panel can show the queue without becoming a way to nod it through.
    // Keeping and dropping live on Today, behind memory_api.py's guard.
    if (inbox) {
      h += '<div class="card"><h2>Memory proposals</h2>';
      if (inbox.count > 0) {
        h += '<div class="metric"><span class="k">Waiting for review</span><span>' + inbox.count + ' note' + (inbox.count > 1 ? 's' : '') + '</span></div>';
        for (const p of (inbox.proposals || []).slice(0, 3)) {
          h += '<div class="inbox-item"><div>' + esc(p.body) + '</div><div class="meta">' + esc(p.type) +
            (p.age_days != null ? ' · ' + p.age_days + 'd ago' : '') + (p.source ? ' · ' + esc(p.source) : '') + '</div></div>';
        }
        h += '<div class="meta" style="margin-top:8px"><a href="#today">Keep or drop them on Today</a>, or at the terminal: <code>uv run --project brain python brain/review_notes.py list</code></div>';
      } else {
        h += '<div class="pills">' + pill('up', 'Inbox clear') + '</div>';
      }
      h += '</div>';
    }

    const b = s.brain;
    h += '<div class="card"><h2>Brain</h2><div class="pills">' +
      pill(b.ollama_up ? 'up' : 'down', 'Ollama', b.version) +
      pill(b.resident ? 'up' : 'down', b.model, b.resident ? 'loaded' : 'not loaded') + '</div></div>';

    if (s.gpus && s.gpus.length) {
      h += '<div class="card"><h2>GPU</h2>';
      for (const g of s.gpus) {
        const memPct = clamp(100 * g.mem_used_gb / g.mem_total_gb);
        h += '<div class="gpu"><div class="glabel"><span>GPU' + g.index + ' ' + esc(g.name.replace('NVIDIA GeForce ', '')) +
          '</span><span class="sub">' + g.mem_used_gb + '/' + g.mem_total_gb + ' GB · ' + g.util_pct + '% · ' + g.temp_c +
          '°C</span></div><div class="' + barClass(memPct, 70, 90) + '"><i style="width:' + memPct + '%"></i></div></div>';
      }
      h += '</div>';
    }

    const sy = s.system;
    h += '<div class="card"><h2>Brain box</h2>';
    if (sy.load1 != null) h += '<div class="row metric"><span class="k">Load</span><span>' + esc(sy.load1) + ' / ' + esc(sy.cpus) + ' cores</span></div>';
    if (sy.mem_pct != null) h += bar('RAM', sy.mem_pct, 80, 92);
    h += bar('Swap' + (sy.swap_used_gb ? ' (' + sy.swap_used_gb + ' GB)' : ''), sy.swap_pct || 0, 20, 50);
    if (sy.disk_pct != null) h += bar('Disk', sy.disk_pct, 85, 95);
    h += '</div>';

    h += '<div class="card"><h2>Downloads</h2><div class="metric"><span class="k">Active</span><span>' +
      (s.downloads.active || 0) + '</span></div></div>';
    h += '<div class="foot-note">Updated ' + new Date().toLocaleTimeString() + '</div>';
    body().innerHTML = h;
  }

  async function refresh() {
    try {
      const [statusRes, maintRes, inboxRes] = await Promise.all([
        fetch('/status'),
        fetch('/maintenance/due').catch(() => null),
        fetch('/memory/inbox?limit=5').catch(() => null),
      ]);
      if (!statusRes.ok) throw new Error('status ' + statusRes.status);
      const s = await statusRes.json();
      const maint = maintRes && maintRes.ok ? await maintRes.json() : null;
      const inbox = inboxRes && inboxRes.ok ? await inboxRes.json() : null;
      render(s, maint, inbox);
    } catch (e) {
      body().innerHTML = '<div class="empty">Couldn’t load status: ' + esc(e.message) +
        '. Is the brain up and are you on the tailnet?</div>';
    }
  }
  const stop = () => { if (timer) { clearInterval(timer); timer = null; } };
  const start = () => { stop(); refresh(); timer = setInterval(refresh, POLL_MS); };

  Hestia.views.status = { title: 'Status', enter: start, leave: stop, pause: stop, resume: start };
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('statusRefresh').addEventListener('click', refresh);
  });
})();
