/*
 * Dashboard slash commands: the chat box doubles as a command line.
 *
 * Shared by the retail dashboard (DATA/dashboard) and the private one
 * (LCARS/lcars-dashboard). Keep the two copies byte-identical.
 *
 *   /help            list commands            /panes           list open windows
 *   /new [dir] [m]   open a new chat window   /stop            stop this window's reply
 *   /model [name]    show or switch model     /clear           clear this window's screen
 *   /effort [level]  show or set effort       /close           close this window
 *   /shortcuts       saved prompts: list, add, edit, delete
 *
 * Prompt shortcuts: any other /name that matches a saved prompt sends that
 * prompt to the AI through the window's normal send path ("/brief", or
 * "/explain app.js" where the prompt holds {args}). The list is stored per user
 * on the bridge (prompt_shortcuts.py), so every browser on that bridge shares
 * it. Built-in commands always win over a shortcut of the same name.
 *
 * Sign-in commands (/login, /logout, /status, `claude login`, `install codex`)
 * are NOT handled here. They fall through to the in-chat console
 * (_consoleIntercept), whose allowlist lives on the bridge.
 *
 * Everything else acts on the dashboard itself through functions app.js
 * already exposes (setMainProvider, setWindowEffort, openProjectWorkspace...).
 * Nothing is sent to the model and nothing runs on the machine.
 *
 * Extending: DashSlash.register({ name, aliases, args, desc, run(ctx, args) }).
 */
(function () {
  'use strict';

  const CONSOLE_NAMES = new Set(['login', 'logout', 'status']);
  const COMMANDS = [];
  const byName = new Map();

  function register(cmd) {
    if (!cmd || !cmd.name || typeof cmd.run !== 'function') return;
    COMMANDS.push(cmd);
    for (const n of [cmd.name, ...(cmd.aliases || [])]) byName.set(n.toLowerCase(), cmd);
  }

  // ── Small helpers ─────────────────────────────────────────────────────────
  const has = (name) => typeof globalThis[name] === 'function';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const md = (s) => (has('renderMarkdown') ? globalThis.renderMarkdown(s) : esc(s).replace(/\n/g, '<br>'));

  function workspaces() {
    try { return _workspaces; } catch (_) { return new Map(); }   // eslint-disable-line no-undef
  }
  function providersCache() {
    try { return _providersCache || []; } catch (_) { return []; }   // eslint-disable-line no-undef
  }
  function activeProviderId() {
    try { return _lastKnownActiveProvider || ''; } catch (_) { return ''; }   // eslint-disable-line no-undef
  }
  function activeEffort() {
    try { return _lastKnownActiveEffort || ''; } catch (_) { return ''; }   // eslint-disable-line no-undef
  }
  function mainWs() {
    return [...workspaces().values()].find(w => w.isMain) || null;
  }

  // ctx = { main: true } or { wsId: n }. Resolve it to the pane it names.
  function resolve(ctx) {
    if (ctx && ctx.wsId != null && !ctx.main) {
      const ws = workspaces().get(ctx.wsId);
      return {
        main: false, wsId: ctx.wsId, ws,
        winEl: document.getElementById(`chat-win-ws${ctx.wsId}`),
        provider: ws?.provider || activeProviderId(),
        effort: ws?.effort || '',
        path: ws?.path || '',
        name: ws?.name || `Window ${ctx.wsId}`,
      };
    }
    const ws = mainWs();
    return {
      main: true, wsId: null, ws,
      winEl: document.getElementById('chat-window'),
      provider: ws?.provider || activeProviderId(),
      effort: ws ? (ws.effort || '') : activeEffort(),
      path: ws?.path || '',
      name: ws?.name || 'Main Channel',
    };
  }

  // ── Output: a compact card in the chat window ─────────────────────────────
  // Rendered directly (not through appendMessage) so a command result is never
  // spoken aloud, saved to chat history, or fed back to the model.
  function card(pane, command, html) {
    const winEl = pane.winEl;
    if (!winEl) return null;
    const pinned = winEl.scrollHeight - winEl.scrollTop - winEl.clientHeight < 80;
    const msg = document.createElement('div');
    msg.className = 'chat-message data slash-msg';
    msg.innerHTML =
      `<div class="avatar slash-avatar">/</div>` +
      `<div class="bubble slash-card">` +
      `<div class="slash-head">${esc(command)}</div>` +
      `<div class="slash-body">${html || ''}</div>` +
      `<div class="slash-actions"></div></div>`;
    winEl.appendChild(msg);
    if (pinned) winEl.scrollTop = winEl.scrollHeight;
    return {
      el: msg,
      body: msg.querySelector('.slash-body'),
      actions: msg.querySelector('.slash-actions'),
      set(h) { this.body.innerHTML = h; },
      button(label, onClick, opts = {}) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'slash-btn' + (opts.current ? ' current' : '');
        b.textContent = label;
        if (opts.title) b.title = opts.title;
        b.addEventListener('click', () => onClick(b));
        this.actions.appendChild(b);
        if (pinned) winEl.scrollTop = winEl.scrollHeight;
        return b;
      },
    };
  }

  // ── Providers and effort ──────────────────────────────────────────────────
  async function ensureProviders() {
    if (!providersCache().length && has('loadProviders')) {
      try { await globalThis.loadProviders(); } catch (_) {}
    }
    return providersCache();
  }

  function selectableProviders(currentId) {
    return providersCache().filter(p => p.available && (!p.hidden || p.id === currentId));
  }

  function providerLabel(id) {
    const p = providersCache().find(x => x.id === id);
    return p ? `${p.label}` : (id || 'default');
  }

  // Every word of the query must appear in the provider's id or label, ignoring
  // case and punctuation: "opus 4.8", "sonnet", "codex mini", "claude-cli-haiku".
  function matchProviders(query, currentId) {
    const list = selectableProviders(currentId);
    const q = norm(query);
    if (!q) return [];
    const exact = list.filter(p => norm(p.id) === q || norm(p.label) === q);
    if (exact.length) return exact;
    const words = String(query).toLowerCase().split(/\s+/).map(norm).filter(Boolean);
    return list.filter(p => {
      const hay = norm(p.id) + '|' + norm(p.label);
      return words.every(w => hay.includes(w));
    });
  }

  function effortLevelsFor(providerId) {
    const p = providersCache().find(x => x.id === providerId);
    if (p?.effort_levels?.length) return p.effort_levels;
    try { if (_effortLevelsCache.length) return _effortLevelsCache; } catch (_) {}   // eslint-disable-line no-undef
    try { return EFFORT_LEVELS_FALLBACK; } catch (_) {}   // eslint-disable-line no-undef
    return [{ id: '', short: 'AUTO' }, { id: 'low', short: 'LOW' }, { id: 'medium', short: 'MEDIUM' },
            { id: 'high', short: 'HIGH' }, { id: 'xhigh', short: 'XHIGH' }, { id: 'max', short: 'MAX' }];
  }

  function effortSupported(providerId) {
    const p = providersCache().find(x => x.id === providerId);
    return p ? p.supports_effort !== false : true;
  }

  const EFFORT_ALIASES = {
    auto: '', default: '', off: '', none: '', reset: '',
    lo: 'low', med: 'medium', mid: 'medium', hi: 'high',
    'x-high': 'xhigh', 'extra-high': 'xhigh', extra: 'xhigh', xh: 'xhigh', maximum: 'max',
  };

  function parseEffort(raw, levels) {
    const s = String(raw || '').trim().toLowerCase();
    const id = Object.prototype.hasOwnProperty.call(EFFORT_ALIASES, s) ? EFFORT_ALIASES[s] : s;
    const hit = levels.find(l => l.id === id || String(l.short || '').toLowerCase() === s);
    return hit ? hit.id : null;
  }

  async function applyProvider(pane, id) {
    if (pane.main) {
      if (has('setMainProvider')) await globalThis.setMainProvider(id);
      if (has('_populateMainProviderSelect')) globalThis._populateMainProviderSelect();
      if (has('_populateMainEffortSelect')) globalThis._populateMainEffortSelect();
    } else {
      if (has('setWindowProvider')) globalThis.setWindowProvider(pane.wsId, id);
      if (has('_populateWindowProviderSelect')) globalThis._populateWindowProviderSelect(pane.wsId);
    }
  }

  async function applyEffort(pane, level) {
    if (pane.main) {
      if (has('setMainEffort')) await globalThis.setMainEffort(level);
    } else if (has('setWindowEffort')) {
      globalThis.setWindowEffort(pane.wsId, level);
    }
  }

  // ── Commands ──────────────────────────────────────────────────────────────
  register({
    name: 'help', aliases: ['?', 'commands'], args: '', desc: 'List every slash command',
    async run(pane, args, raw) {
      const rows = COMMANDS.filter(c => !c.hidden).map(c =>
        `<tr><td><code>/${esc(c.name)}${c.args ? ' ' + esc(c.args) : ''}</code></td><td>${esc(c.desc)}</td></tr>`);
      if (has('_consoleIntercept')) {
        rows.push(
          `<tr><td><code>/login</code></td><td>Sign in to the AI CLI this window uses</td></tr>`,
          `<tr><td><code>/logout</code></td><td>Sign out of that CLI</td></tr>`,
          `<tr><td><code>/status</code></td><td>Show whether that CLI is signed in</td></tr>`);
      }
      const extra = has('_consoleIntercept')
        ? `<p class="slash-note">Setup commands also work: <code>claude login</code>, <code>codex status</code>, <code>install gemini</code>. Type <code>!help</code> for the full list.</p>`
        : '';
      await loadShortcuts();
      const mine = usableShortcuts();
      const prompts = mine.length
        ? `<p class="slash-sub">Your prompt shortcuts</p><table class="slash-table">${mine.map(s =>
            `<tr><td><code>/${esc(s.name)}${needsArgs(s) ? ' text' : ''}</code></td><td>${esc(s.desc || preview(s.prompt))}</td></tr>`).join('')}</table>`
        : '';
      card(pane, raw, `<table class="slash-table">${rows.join('')}</table>${prompts}${extra}` +
        `<p class="slash-note">Tab completes a command. Anything that is not a command goes to the AI as usual.</p>`);
    },
  });

  register({
    name: 'new', aliases: ['open', 'newchat', 'window'], args: '[folder] [model]',
    desc: 'Open a new chat window (same folder and model as this one by default)',
    async run(pane, args, raw) {
      await ensureProviders();
      const words = args ? args.match(/"[^"]+"|'[^']+'|\S+/g).map(w => w.replace(/^["']|["']$/g, '')) : [];
      let path = '';
      let provider = '';
      const loose = [];
      for (const w of words) {
        if (/[\\/:~]/.test(w) || w.startsWith('.')) path = w;
        else loose.push(w);
      }
      if (loose.length) {
        const hits = matchProviders(loose.join(' '), pane.provider);
        if (hits.length === 1) provider = hits[0].id;
        else if (!hits.length && !path && loose.length === 1) {
          // A bare word that is not a model: a sibling folder of this window's
          // project, e.g. "/new SaiSaiPro" from inside Documents\DATA.
          const base = pane.path || mainWs()?.path || '';
          const parent = base.replace(/[\\/]+$/, '').replace(/[\\/][^\\/]*$/, '');
          if (parent) path = `${parent}${parent.includes('\\') ? '\\' : '/'}${loose[0]}`;
        }
        if (!provider && hits.length > 1 && !path) {
          const c = card(pane, raw, `<p>"${esc(loose.join(' '))}" matches several models. Pick one:</p>`);
          for (const p of hits) c?.button(p.label, () => run(pane.main ? { main: true } : { wsId: pane.wsId }, `/new ${pane.path ? `"${pane.path}" ` : ''}${p.id}`));
          return;
        }
      }
      path = path || pane.path || mainWs()?.path || '';
      if (!path) {
        // No folder known (main channel not rooted): fall back to the folder picker.
        card(pane, raw, `<p>Choose a folder for the new window.</p>`);
        if (has('addProjectTab')) globalThis.addProjectTab();
        return;
      }
      if (!has('openProjectWorkspace')) { card(pane, raw, '<p>This dashboard cannot open windows.</p>'); return; }
      const wsId = await globalThis.openProjectWorkspace(path, { forceNewPane: true });
      if (!wsId) {
        card(pane, raw, `<p>Could not open <code>${esc(path)}</code>. Check that the folder exists.</p>`);
        return;
      }
      const ws = workspaces().get(wsId);
      if (ws) {
        ws.provider = provider || pane.provider || ws.provider;
        if (!ws.effort && pane.effort) ws.effort = pane.effort;
        if (has('_populateWindowProviderSelect')) globalThis._populateWindowProviderSelect(wsId);
        if (has('_populateWindowEffortSelect')) globalThis._populateWindowEffortSelect(wsId);
      }
      if (has('playDataSound')) globalThis.playDataSound('engage');
      card(pane, raw, `<p>Opened <strong>${esc(ws?.name || path)}</strong> on ${esc(providerLabel(ws?.provider))}` +
        `${ws?.effort ? `, effort ${esc(ws.effort)}` : ''}.</p>`);
      document.getElementById(`pane-input-ws${wsId}`)?.focus();
    },
  });

  register({
    name: 'model', aliases: ['models', 'm', 'provider'], args: '[name]',
    desc: "Show or switch this window's model (partial names work: opus, sonnet, codex)",
    async run(pane, args, raw) {
      await ensureProviders();
      const list = selectableProviders(pane.provider);
      if (!args) {
        const c = card(pane, raw, `<p>This window is on <strong>${esc(providerLabel(pane.provider))}</strong>. Pick another:</p>`);
        for (const p of list) {
          c?.button(p.label, async (b) => {
            await applyProvider(resolve(pane.main ? { main: true } : { wsId: pane.wsId }), p.id);
            c.actions.querySelectorAll('.slash-btn').forEach(x => x.classList.toggle('current', x === b));
            c.set(`<p>This window is now on <strong>${esc(p.label)}</strong>.</p>`);
          }, { current: p.id === pane.provider, title: p.id });
        }
        if (!list.length) c?.set('<p>No models are available yet. Connect one in Settings.</p>');
        return;
      }
      const hits = matchProviders(args, pane.provider);
      if (hits.length === 1) {
        await applyProvider(pane, hits[0].id);
        const levels = effortLevelsFor(hits[0].id);
        const note = pane.effort && !levels.some(l => l.id === pane.effort)
          ? ` Effort ${esc(pane.effort)} is not offered on this model, so it runs at the nearest level below.` : '';
        card(pane, raw, `<p>Model is now <strong>${esc(hits[0].label)}</strong>.${note}</p>`);
        return;
      }
      if (!hits.length) {
        const c = card(pane, raw, `<p>No available model matches "${esc(args)}". Available:</p>`);
        for (const p of list) c?.button(p.label, () => run(pane.main ? { main: true } : { wsId: pane.wsId }, `/model ${p.id}`), { title: p.id });
        return;
      }
      const c = card(pane, raw, `<p>"${esc(args)}" matches ${hits.length} models. Pick one:</p>`);
      for (const p of hits) c?.button(p.label, () => run(pane.main ? { main: true } : { wsId: pane.wsId }, `/model ${p.id}`), { title: p.id });
    },
  });

  register({
    name: 'effort', aliases: ['e', 'think', 'reasoning'], args: '[auto|low|medium|high|xhigh|max]',
    desc: "Show or set this window's reasoning effort",
    async run(pane, args, raw) {
      await ensureProviders();
      if (!effortSupported(pane.provider)) {
        card(pane, raw, `<p><strong>${esc(providerLabel(pane.provider))}</strong> has no reasoning effort control.</p>`);
        return;
      }
      const levels = effortLevelsFor(pane.provider);
      const showPicker = (lead) => {
        const c = card(pane, raw, `<p>${lead}</p>`);
        for (const l of levels) {
          c?.button(l.short || l.id || 'AUTO', async (b) => {
            await applyEffort(resolve(pane.main ? { main: true } : { wsId: pane.wsId }), l.id);
            c.actions.querySelectorAll('.slash-btn').forEach(x => x.classList.toggle('current', x === b));
            c.set(`<p>Effort is now <strong>${esc(l.short || l.id || 'AUTO')}</strong>.</p>`);
          }, { current: l.id === (pane.effort || '') });
        }
      };
      if (!args) {
        showPicker(`Effort is <strong>${esc(pane.effort || 'auto')}</strong> on ${esc(providerLabel(pane.provider))}. Pick a level:`);
        return;
      }
      const level = parseEffort(args, levels);
      if (level === null) {
        showPicker(`"${esc(args)}" is not a level this model offers. Pick one:`);
        return;
      }
      await applyEffort(pane, level);
      card(pane, raw, `<p>Effort is now <strong>${esc(level || 'auto')}</strong>.</p>`);
    },
  });

  register({
    name: 'panes', aliases: ['windows', 'tabs'], args: '', desc: 'List the open chat windows',
    async run(pane, args, raw) {
      await ensureProviders();
      const rows = [];
      const m = mainWs();
      if (!m) {
        rows.push(`<tr><td>Main Channel</td><td>${esc(providerLabel(activeProviderId()))}</td><td>${esc(activeEffort() || 'auto')}</td><td></td></tr>`);
      }
      for (const [, w] of workspaces()) {
        rows.push(`<tr><td>${esc(w.name || '')}${w.isMain ? ' (main)' : ''}</td>` +
          `<td>${esc(providerLabel(w.provider || activeProviderId()))}</td>` +
          `<td>${esc(w.effort || 'auto')}</td><td><code>${esc(w.path || '')}</code></td></tr>`);
      }
      card(pane, raw, `<table class="slash-table"><tr><th>Window</th><th>Model</th><th>Effort</th><th>Folder</th></tr>${rows.join('')}</table>`);
    },
  });

  register({
    name: 'stop', aliases: ['abort', 'cancel'], args: '', desc: "Stop this window's reply",
    run(pane, args, raw) {
      if (pane.main) {
        let busy = false;
        try { busy = isThinking; } catch (_) {}   // eslint-disable-line no-undef
        if (busy && has('stopData')) { globalThis.stopData(); card(pane, raw, '<p>Stopped.</p>'); }
        else card(pane, raw, '<p>Nothing is running in this window.</p>');
        return;
      }
      const ws = pane.ws;
      if (!ws || !ws.isThinking) { card(pane, raw, '<p>Nothing is running in this window.</p>'); return; }
      try { ws.abortController?.abort(); } catch (_) {}
      let paneId = '';
      try { paneId = _paneId(`ws${pane.wsId}`); } catch (_) {}   // eslint-disable-line no-undef
      let base = '';
      try { base = API_BASE; } catch (_) {}   // eslint-disable-line no-undef
      fetch(`${base}/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_path: ws.path || '', pane_id: paneId }),
      }).catch(() => {});
      ws.isThinking = false;
      ws.abortController = null;
      globalThis.PaneStatus?.stopped(globalThis.PaneStatus.keyForWs?.(pane.wsId));
      if (has('removeThinkingFromPane') && pane.winEl) globalThis.removeThinkingFromPane(pane.winEl);
      card(pane, raw, '<p>Stopped.</p>');
    },
  });

  register({
    name: 'clear', aliases: ['cls'], args: '', desc: "Clear this window's screen (memory is kept)",
    run(pane, args, raw) {
      if (!pane.winEl) return;
      pane.winEl.querySelectorAll('.chat-message').forEach(el => el.remove());
      card(pane, raw, '<p class="slash-note">Screen cleared. The conversation memory is unchanged.</p>');
    },
  });

  register({
    name: 'close', aliases: ['quit', 'exit'], args: '', desc: 'Close this chat window',
    run(pane, args, raw) {
      if (pane.main) { card(pane, raw, '<p>The main channel stays open. Use <code>/close</code> in another window.</p>'); return; }
      if (has('closeProjectWorkspace')) globalThis.closeProjectWorkspace(pane.wsId);
    },
  });

  // ── Prompt shortcuts ──────────────────────────────────────────────────────
  // Cached copy of the bridge's list. Refreshed when older than STALE_MS so a
  // shortcut saved on another device (or after a user switch) shows up.
  const SC = { list: [], at: 0, available: true, inflight: null, error: '' };
  const STALE_MS = 30000;
  const NAME_RE = /^[a-z][a-z0-9_-]{0,31}$/;

  function apiBase() {
    try { return API_BASE; } catch (_) { return ''; }   // eslint-disable-line no-undef
  }

  async function scRequest(method, body) {
    const init = { method, cache: 'no-store', headers: { 'X-Data-Request': '1' } };
    if (body) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const r = await fetch(`${apiBase()}/prompt-shortcuts`, init);
    let j = null;
    try { j = await r.json(); } catch (_) {}
    // An older bridge has no such route: 404, or a catch-all answer without a list.
    if (r.status === 404 || (r.ok && !Array.isArray(j?.shortcuts))) {
      SC.available = false;
      throw new Error('Prompt shortcuts turn on after the bridge restarts.');
    }
    if (!r.ok) throw new Error(j?.error || `The bridge answered ${r.status}.`);
    SC.available = true;
    SC.error = '';
    SC.list = j.shortcuts;
    SC.at = Date.now();
    return SC.list;
  }

  function loadShortcuts(force) {
    if (!force && SC.at && Date.now() - SC.at < STALE_MS) return Promise.resolve(SC.list);
    if (!SC.inflight) {
      SC.inflight = scRequest('GET')
        .catch((e) => { SC.error = e?.message || String(e); return SC.list; })
        .finally(() => { SC.inflight = null; });
    }
    return SC.inflight;
  }

  const isReserved = (name) => byName.has(name) || CONSOLE_NAMES.has(name);
  const usableShortcuts = () => SC.list.filter(s => !isReserved(s.name));
  const findShortcut = (name) => usableShortcuts().find(s => s.name === name) || null;
  const needsArgs = (s) => String(s.prompt).includes('{args}');
  const preview = (t, n = 70) => { const s = String(t).replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const paneCtx = (pane) => (pane.main ? { main: true } : { wsId: pane.wsId });

  function expand(s, args, pane) {
    let t = String(s.prompt);
    if (needsArgs(s)) t = t.split('{args}').join(args);
    else if (args) t = `${t}\n\n${args}`;
    return t.split('{folder}').join(pane.path || 'this folder');
  }

  function inputFor(pane) {
    return pane.main ? document.getElementById('chat-input') : document.getElementById(`pane-input-ws${pane.wsId}`);
  }

  function putInInput(pane, text) {
    const input = inputFor(pane);
    if (!input) return;
    input.value = text;
    input.focus();
    input.setSelectionRange?.(text.length, text.length);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // While true, handles() says no, so the expanded prompt cannot be re-read as
  // a command (a prompt that starts with "/" would otherwise loop).
  let sendingPrompt = false;

  // Hand the expanded prompt to the window's own send function, so attachments,
  // the queue, history and status badges all behave exactly as if typed.
  function sendPrompt(pane, text, raw = '') {
    const input = inputFor(pane);
    const fn = pane.main ? globalThis.sendMessage : globalThis.sendProjectMessage;
    if (!input || typeof fn !== 'function') { card(pane, raw, '<p>This window cannot send messages.</p>'); return; }
    if (!pane.main && pane.ws?.isThinking) {
      putInInput(pane, text);
      card(pane, raw, '<p>This window is still replying, so the prompt is waiting in the box. Press Enter when the reply finishes.</p>');
      return;
    }
    input.value = text;
    sendingPrompt = true;
    try { pane.main ? fn() : fn(pane.wsId); } finally { sendingPrompt = false; }
  }

  function runShortcut(pane, s, args, raw) {
    if (needsArgs(s) && !args) {
      putInInput(pane, `/${s.name} `);
      card(pane, raw, `<p><code>/${esc(s.name)}</code> needs something after it, for example <code>/${esc(s.name)} app.js</code>. Finish the line and press Enter.</p>`);
      return;
    }
    sendPrompt(pane, expand(s, args, pane), raw);
  }

  function lastUserText(pane) {
    const msgs = pane.winEl ? pane.winEl.querySelectorAll('.chat-message.user .text') : [];
    const el = msgs[msgs.length - 1];
    return el ? String(el.innerText || el.textContent || '').trim() : '';
  }

  // ownName: the shortcut being edited (its own name is not a clash), or ''.
  function nameProblem(name, ownName) {
    if (!NAME_RE.test(name)) return 'Names use lowercase letters, digits, - or _, start with a letter, and are at most 32 characters.';
    if (isReserved(name)) return `/${name} is a built-in command. Pick another name.`;
    if (name !== ownName && SC.list.some(s => s.name === name)) {
      return `/${name} already exists. Edit it instead, or pick another name.`;
    }
    return '';
  }

  async function saveShortcut(item, renameFrom) {
    return scRequest('POST', { action: 'save', ...item, rename_from: renameFrom || '' });
  }

  // Inline editor card. `existing` is a shortcut to edit, or null for a new one.
  function openEditor(pane, raw, existing, seed = {}) {
    const isNew = !existing;
    const c = card(pane, raw, '');
    if (!c) return;
    c.set(
      `<div class="slash-form">` +
      `<label>Name <span class="slash-form-prefix">/</span><input class="sf-name" maxlength="32" spellcheck="false" placeholder="brief"></label>` +
      `<label>Description <input class="sf-desc" maxlength="120" placeholder="Shown in the command list"></label>` +
      `<label>Prompt <textarea class="sf-prompt" rows="6" placeholder="What the AI should do when you type the shortcut"></textarea></label>` +
      `<p class="slash-note">Put <code>{args}</code> where text typed after the command should go, as in <code>/explain app.js</code>. ` +
      `Without it, anything typed after the command is added at the end. <code>{folder}</code> becomes this window's folder. ` +
      `Ctrl+Enter saves.</p>` +
      (seed.fromLast ? `<p class="slash-note">Filled in with the last message you sent in this window.</p>` : '') +
      `<p class="sf-err"></p></div>`);
    const nameEl = c.body.querySelector('.sf-name');
    const descEl = c.body.querySelector('.sf-desc');
    const promptEl = c.body.querySelector('.sf-prompt');
    const errEl = c.body.querySelector('.sf-err');
    nameEl.value = existing?.name || seed.name || '';
    descEl.value = existing?.desc || seed.desc || '';
    promptEl.value = existing?.prompt || seed.prompt || '';

    let busy = false;
    const doSave = async () => {
      if (busy) return;
      const name = nameEl.value.trim().replace(/^\//, '').toLowerCase();
      const prompt = promptEl.value.trim();
      const problem = nameProblem(name, isNew ? '' : existing.name) || (prompt ? '' : 'The prompt is empty.');
      if (problem) { errEl.textContent = problem; return; }
      busy = true;
      errEl.textContent = '';
      try {
        await saveShortcut({ name, prompt, desc: descEl.value.trim() }, isNew ? '' : existing.name);
      } catch (e) {
        errEl.textContent = e?.message || String(e);
        busy = false;
        return;
      }
      c.actions.replaceChildren();
      const renamed = !isNew && existing.name !== name ? ` (was /${esc(existing.name)})` : '';
      c.set(`<p>${isNew ? 'Saved' : 'Updated'} <code>/${esc(name)}</code>${renamed}. Type it in any chat window to send the prompt.</p>`);
    };
    c.button(isNew ? 'Save shortcut' : 'Save changes', doSave, { current: true });
    c.button('Cancel', () => { c.actions.replaceChildren(); c.set('<p class="slash-note">No changes saved.</p>'); });
    // Keys typed in the form belong to the form: stop the dashboard's global
    // hotkeys and the chat box's Enter handler from seeing them.
    c.body.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); doSave(); }
    });
    (isNew && !nameEl.value ? nameEl : promptEl).focus();
  }

  function listCard(pane, raw) {
    const mine = SC.list;
    const rows = mine.map(s => {
      const shadow = isReserved(s.name) ? ' <span class="slash-note">(hidden by a built-in command)</span>' : '';
      return `<tr data-name="${esc(s.name)}"><td><code>/${esc(s.name)}${needsArgs(s) ? ' text' : ''}</code>${shadow}</td>` +
        `<td>${esc(s.desc || preview(s.prompt))}</td>` +
        `<td class="slash-row-btns"><button type="button" class="slash-btn" data-act="run">Run</button>` +
        `<button type="button" class="slash-btn" data-act="edit">Edit</button>` +
        `<button type="button" class="slash-btn" data-act="delete">Delete</button></td></tr>`;
    }).join('');
    const c = card(pane, raw, mine.length
      ? `<table class="slash-table">${rows}</table>` +
        `<p class="slash-note">Type <code>/name</code> to send a saved prompt. Add one with <code>/shortcuts add name prompt text</code> or the button below.</p>`
      : `<p>No prompt shortcuts yet.</p>`);
    if (!c) return;
    c.body.addEventListener('click', async (e) => {
      const b = e.target.closest('button[data-act]');
      const name = b?.closest('tr')?.dataset.name;
      const s = name && SC.list.find(x => x.name === name);
      if (!s) return;
      const here = resolve(paneCtx(pane));
      if (b.dataset.act === 'run') runShortcut(here, s, '', `/${s.name}`);
      else if (b.dataset.act === 'edit') openEditor(here, `/shortcuts edit ${s.name}`, s);
      else if (b.dataset.act === 'delete') await deleteShortcut(here, s, `/shortcuts delete ${s.name}`);
    });
    c.button('New shortcut', () => openEditor(resolve(paneCtx(pane)), '/shortcuts add', null));
    c.button('Restore starter set', () => run(paneCtx(pane), '/shortcuts reset'));
  }

  async function deleteShortcut(pane, s, raw) {
    try {
      await scRequest('POST', { action: 'delete', name: s.name });
    } catch (e) {
      card(pane, raw, `<p>Could not delete <code>/${esc(s.name)}</code>: ${esc(e?.message || e)}</p>`);
      return;
    }
    const c = card(pane, raw, `<p>Deleted <code>/${esc(s.name)}</code>.</p>`);
    c?.button('Undo', async () => {
      try {
        await saveShortcut(s, '');
        c.actions.replaceChildren();
        c.set(`<p>Restored <code>/${esc(s.name)}</code>.</p>`);
      } catch (e) {
        c.set(`<p>Could not restore it: ${esc(e?.message || e)}</p>`);
      }
    });
  }

  register({
    name: 'shortcuts', aliases: ['shortcut', 'sc', 'prompts'], args: '[add|edit|delete|show] [name]',
    desc: 'Saved prompts you run as /name: list, add, edit, delete',
    async run(pane, args, raw) {
      await loadShortcuts(true);
      if (!SC.available || (SC.error && !SC.at)) {
        card(pane, raw, `<p>${esc(SC.error || 'Prompt shortcuts are unavailable.')}</p>`);
        return;
      }
      const m = String(args || '').match(/^(\S+)?\s*(\S+)?\s*([\s\S]*)$/) || [];
      const sub = (m[1] || '').toLowerCase();
      const name = (m[2] || '').replace(/^\//, '').toLowerCase();
      const rest = (m[3] || '').trim();
      const existing = name ? SC.list.find(s => s.name === name) : null;

      if (!sub || sub === 'list' || sub === 'ls') { listCard(pane, raw); return; }

      if (['add', 'new', 'save', 'create'].includes(sub)) {
        if (name && rest) {
          const problem = nameProblem(name, existing ? name : '');
          if (problem) { card(pane, raw, `<p>${esc(problem)}</p>`); return; }
          try {
            await saveShortcut({ name, prompt: rest, desc: existing?.desc || '' }, '');
          } catch (e) {
            card(pane, raw, `<p>Could not save: ${esc(e?.message || e)}</p>`);
            return;
          }
          card(pane, raw, `<p>${existing ? 'Updated' : 'Saved'} <code>/${esc(name)}</code>. Type it in any chat window to send the prompt.</p>`);
          return;
        }
        if (existing) { openEditor(pane, raw, existing); return; }
        const last = lastUserText(pane);
        openEditor(pane, raw, null, { name, prompt: last, fromLast: !!last });
        return;
      }

      if (['edit', 'change', 'rename'].includes(sub)) {
        if (!existing) { card(pane, raw, `<p>There is no <code>/${esc(name || '?')}</code> shortcut. Type <code>/shortcuts</code> for the list.</p>`); return; }
        openEditor(pane, raw, existing);
        return;
      }

      if (['delete', 'del', 'rm', 'remove'].includes(sub)) {
        if (!existing) { card(pane, raw, `<p>There is no <code>/${esc(name || '?')}</code> shortcut.</p>`); return; }
        await deleteShortcut(pane, existing, raw);
        return;
      }

      if (sub === 'reset') {
        const c = card(pane, raw, '<p>Replace every saved shortcut with the starter set (brief, review, tests, explain, plan)? Your own shortcuts will be deleted.</p>');
        c?.button('Yes, restore the starter set', async () => {
          c.actions.replaceChildren();
          try {
            await scRequest('POST', { action: 'reset' });
            c.set('<p>Restored the starter shortcuts.</p>');
          } catch (e) {
            c.set(`<p>Could not restore them: ${esc(e?.message || e)}</p>`);
          }
        });
        c?.button('Cancel', () => { c.actions.replaceChildren(); c.set('<p class="slash-note">Nothing changed.</p>'); });
        return;
      }

      // "/shortcuts brief" or "/shortcuts show brief": show the full prompt.
      const target = sub === 'show' || sub === 'view' ? existing : SC.list.find(s => s.name === sub.replace(/^\//, ''));
      if (target) {
        const c = card(pane, raw, `<p><code>/${esc(target.name)}</code>${target.desc ? ` ${esc(target.desc)}` : ''}</p>` +
          `<pre class="slash-pre">${esc(target.prompt)}</pre>`);
        c?.button('Run', () => runShortcut(resolve(paneCtx(pane)), target, '', `/${target.name}`));
        c?.button('Edit', () => openEditor(resolve(paneCtx(pane)), `/shortcuts edit ${target.name}`, target));
        return;
      }
      card(pane, raw, `<p>Unknown option "${esc(sub)}". Try <code>/shortcuts</code>, <code>/shortcuts add name prompt</code>, ` +
        `<code>/shortcuts edit name</code> or <code>/shortcuts delete name</code>.</p>`);
    },
  });

  // ── Dispatch ──────────────────────────────────────────────────────────────
  // "/name rest". A slash followed by another path segment ("/r/stocks is up")
  // is not a command, so it goes to the AI untouched.
  function parse(text) {
    const t = String(text || '').trim();
    if (!t.startsWith('/') || t.includes('\n')) return null;
    const m = t.match(/^\/([a-z?][\w-]*)?(?:\s+([\s\S]*))?$/i);
    if (!m) return null;
    return { name: (m[1] || 'help').toLowerCase(), args: (m[2] || '').trim(), raw: t };
  }

  // True when the text belongs to this module. Sign-in names are left for the
  // in-chat console so its allowlist stays the single authority for them.
  function handles(text) {
    if (sendingPrompt) return false;
    const p = parse(text);
    if (!p) return false;
    if (CONSOLE_NAMES.has(p.name) && has('_consoleIntercept')) return false;
    return true;
  }

  async function run(ctx, text) {
    hidePopup();
    const p = parse(text);
    if (!p) return false;
    const pane = resolve(ctx);
    const cmd = byName.get(p.name);
    if (!cmd) {
      await loadShortcuts();
      const s = findShortcut(p.name);
      if (s) { runShortcut(pane, s, p.args, p.raw); return true; }
      const names = [...COMMANDS.map(c => c.name), ...usableShortcuts().map(x => x.name)];
      const near = names.filter(n => n.startsWith(p.name.slice(0, 2))).map(n => `<code>/${esc(n)}</code>`);
      const off = SC.error ? ` (Prompt shortcuts: ${esc(SC.error)})` : '';
      card(pane, p.raw, `<p>Unknown command <code>/${esc(p.name)}</code>.${near.length ? ` Did you mean ${near.join(', ')}?` : ''} Type <code>/help</code> for the list.${off}</p>`);
      return true;
    }
    try {
      await cmd.run(pane, p.args, p.raw);
    } catch (e) {
      card(pane, p.raw, `<p>That command failed: ${esc(e?.message || e)}</p>`);
    }
    return true;
  }

  // ── Autocomplete popup ────────────────────────────────────────────────────
  let popup = null;
  let popupInput = null;
  let popupItems = [];
  let popupIndex = 0;

  function isChatInput(el) {
    return !!el && (el.id === 'chat-input' || /^pane-input-ws\d+$/.test(el.id || ''));
  }

  function suggestions(prefix) {
    const q = prefix.toLowerCase();
    const list = COMMANDS.filter(c => !c.hidden).map(c => ({ name: c.name, args: c.args, desc: c.desc, aliases: c.aliases || [] }));
    if (has('_consoleIntercept')) {
      list.push({ name: 'login', args: '', desc: 'Sign in to the AI CLI this window uses', aliases: [] },
                { name: 'logout', args: '', desc: 'Sign out of that CLI', aliases: [] },
                { name: 'status', args: '', desc: 'Show whether that CLI is signed in', aliases: [] });
    }
    for (const s of usableShortcuts()) {
      list.push({ name: s.name, args: needsArgs(s) ? 'text' : '', desc: s.desc || preview(s.prompt, 60), aliases: [], prompt: true });
    }
    return list.filter(c => c.name.startsWith(q) || c.aliases.some(a => a.startsWith(q)));
  }

  function hidePopup() {
    if (popup) popup.classList.add('hidden');
    popupInput = null;
    popupItems = [];
  }

  function renderPopup() {
    if (!popup) {
      popup = document.createElement('div');
      popup.className = 'slash-popup hidden';
      popup.setAttribute('role', 'listbox');
      // mousedown, not click: keep focus in the chat box.
      popup.addEventListener('mousedown', (e) => {
        const row = e.target.closest('.slash-pop-item');
        if (!row) return;
        e.preventDefault();
        popupIndex = Number(row.dataset.i) || 0;
        complete(true);
      });
      document.body.appendChild(popup);
    }
    popup.innerHTML = popupItems.map((c, i) =>
      `<div class="slash-pop-item${i === popupIndex ? ' active' : ''}${c.prompt ? ' prompt' : ''}" role="option" data-i="${i}">` +
      `<span class="slash-pop-name">/${esc(c.name)}</span>` +
      `<span class="slash-pop-args">${esc(c.args || '')}</span>` +
      `<span class="slash-pop-desc">${esc(c.desc)}</span></div>`).join('');
    const r = popupInput.getBoundingClientRect();
    popup.style.left = `${Math.max(8, r.left)}px`;
    popup.style.bottom = `${Math.max(8, window.innerHeight - r.top + 6)}px`;
    popup.style.width = `${Math.min(Math.max(r.width, 320), 560)}px`;
    popup.classList.remove('hidden');
  }

  function updatePopup(input) {
    const v = input.value;
    if (!v.startsWith('/') || /\s/.test(v)) { hidePopup(); return; }
    // Refresh a stale shortcut list in the background; redraw when it lands.
    if (!SC.inflight && (!SC.at || Date.now() - SC.at > STALE_MS) && SC.available) {
      loadShortcuts().then(() => { if (popupInput === input && input.value === v) updatePopup(input); });
    }
    const items = suggestions(v.slice(1));
    if (!items.length) { hidePopup(); return; }
    if (popupInput !== input) popupIndex = 0;
    popupInput = input;
    popupItems = items;
    popupIndex = Math.min(popupIndex, items.length - 1);
    renderPopup();
  }

  // Put the highlighted command in the box. withSpace = Tab/click (keep typing
  // arguments); without it = Enter (the normal send handler then runs it).
  function complete(withSpace) {
    const input = popupInput;
    const c = popupItems[popupIndex];
    if (!input || !c) return;
    input.value = `/${c.name}${withSpace ? ' ' : ''}`;
    hidePopup();
    if (withSpace) {
      input.focus();
      input.setSelectionRange?.(input.value.length, input.value.length);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  document.addEventListener('input', (e) => {
    if (isChatInput(e.target)) updatePopup(e.target);
  });

  // Capture phase on window so the popup's keys win over the chat box's own
  // Enter handler and the global Escape (unsend) handler.
  window.addEventListener('keydown', (e) => {
    if (!popupInput || e.target !== popupInput || !popup || popup.classList.contains('hidden')) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); e.stopImmediatePropagation();
      const n = popupItems.length;
      popupIndex = (popupIndex + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
      renderPopup();
    } else if (e.key === 'Tab') {
      e.preventDefault(); e.stopImmediatePropagation();
      complete(true);
    } else if (e.key === 'Escape') {
      e.preventDefault(); e.stopImmediatePropagation();
      hidePopup();
    } else if (e.key === 'Enter' && !e.shiftKey) {
      complete(false);   // fall through: the chat box sends the completed command
    }
  }, true);

  document.addEventListener('focusout', (e) => {
    if (e.target === popupInput) setTimeout(() => { if (document.activeElement !== popupInput) hidePopup(); }, 120);
  });
  window.addEventListener('resize', hidePopup);

  // ── Styles (shared by both dashboards; colours fall back across themes) ───
  const css = `
.chat-message.slash-msg .bubble.slash-card {
  border-left: 3px solid var(--data-teal, var(--lcars-teal, #44DDCC));
  font-size: 13px; max-width: min(720px, 100%);
}
.slash-avatar { font-family: var(--font-mono, monospace); font-weight: 700; }
.slash-head {
  font-family: var(--font-mono, monospace); font-size: 12px; letter-spacing: 0.04em;
  color: var(--data-teal, var(--lcars-teal, #44DDCC)); margin-bottom: 6px; word-break: break-all;
}
.slash-head::before { content: '> '; opacity: 0.6; }
.slash-body p { margin: 0 0 6px; }
.slash-body p:last-child { margin-bottom: 0; }
.slash-note { opacity: 0.7; font-size: 12px; }
.slash-table { border-collapse: collapse; width: 100%; margin: 2px 0 6px; }
.slash-table td, .slash-table th {
  text-align: left; padding: 3px 10px 3px 0; vertical-align: top;
  border-bottom: 1px solid rgba(255,255,255,0.07);
}
.slash-table th { font-size: 11px; letter-spacing: 0.06em; opacity: 0.65; font-weight: 600; }
.slash-table code { white-space: nowrap; }
.slash-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.slash-actions:empty { display: none; }
.slash-actions:not(:empty) { margin-top: 8px; }
.slash-btn {
  font: inherit; font-size: 12px; padding: 4px 10px; cursor: pointer; border-radius: 6px;
  color: var(--data-teal, var(--lcars-teal, #44DDCC)); background: transparent;
  border: 1px solid currentColor;
}
.slash-btn:hover { background: rgba(255,255,255,0.06); }
.slash-btn.current {
  color: var(--data-bg, var(--lcars-bg, #000));
  background: var(--data-teal, var(--lcars-teal, #44DDCC));
  border-color: var(--data-teal, var(--lcars-teal, #44DDCC));
}
.slash-popup {
  position: fixed; z-index: 10050; max-height: 300px; overflow-y: auto;
  background: var(--surf-card, var(--data-bg, #0a0a14));
  border: 1px solid var(--data-teal, var(--lcars-teal, #44DDCC));
  border-radius: 8px; padding: 4px; box-shadow: 0 8px 28px rgba(0,0,0,0.55);
  font-size: 13px;
}
.slash-popup.hidden { display: none; }
.slash-pop-item {
  display: grid; grid-template-columns: auto auto 1fr; gap: 10px; align-items: baseline;
  padding: 5px 8px; border-radius: 5px; cursor: pointer;
}
.slash-pop-item.active, .slash-pop-item:hover { background: rgba(68,221,204,0.14); }
.slash-pop-name { font-family: var(--font-mono, monospace); color: var(--data-teal, var(--lcars-teal, #44DDCC)); }
.slash-pop-args { font-family: var(--font-mono, monospace); opacity: 0.55; font-size: 12px; }
.slash-pop-desc { opacity: 0.8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.slash-pop-item.prompt .slash-pop-name { color: var(--data-amber, var(--lcars-orange, #FF9966)); }
.slash-pop-item.prompt .slash-pop-desc::before {
  content: 'PROMPT'; font-size: 9px; letter-spacing: 0.08em; padding: 1px 4px; margin-right: 6px;
  border: 1px solid currentColor; border-radius: 3px; opacity: 0.7;
}
.slash-sub { margin: 8px 0 2px; font-size: 11px; letter-spacing: 0.06em; opacity: 0.65; text-transform: uppercase; }
.slash-row-btns { white-space: nowrap; text-align: right; }
.slash-row-btns .slash-btn { padding: 2px 8px; margin-left: 4px; font-size: 11px; }
.slash-pre {
  white-space: pre-wrap; word-break: break-word; margin: 4px 0; padding: 8px; border-radius: 6px;
  background: rgba(255,255,255,0.04); font-family: var(--font-mono, monospace); font-size: 12px;
  max-height: 260px; overflow-y: auto;
}
.slash-form { display: grid; gap: 8px; }
.slash-form label { display: grid; gap: 3px; font-size: 11px; letter-spacing: 0.04em; opacity: 0.9; }
.slash-form label:first-child { grid-template-columns: auto auto 1fr; align-items: center; gap: 6px; }
.slash-form-prefix { font-family: var(--font-mono, monospace); color: var(--data-teal, var(--lcars-teal, #44DDCC)); }
.slash-form input, .slash-form textarea {
  font: inherit; font-size: 13px; color: inherit; width: 100%; box-sizing: border-box;
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; padding: 6px 8px;
}
.slash-form textarea { resize: vertical; min-height: 90px; font-family: var(--font-mono, monospace); font-size: 12px; }
.slash-form input:focus, .slash-form textarea:focus { outline: none; border-color: var(--data-teal, var(--lcars-teal, #44DDCC)); }
.sf-err { color: var(--data-red, var(--lcars-red, #FF6666)); font-size: 12px; margin: 0; }
.sf-err:empty { display: none; }
`;
  const style = document.createElement('style');
  style.id = 'dash-slash-style';
  style.textContent = css;
  document.head.appendChild(style);

  globalThis.DashSlash = { register, handles, run, parse, commands: COMMANDS, loadShortcuts };
})();
