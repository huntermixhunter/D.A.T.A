// ════════════════════════════════════════════════════════════════════════
// AUTO MODEL CHOICE, Settings panel (spec SPEC_auto_router.md, Task A12)
// ════════════════════════════════════════════════════════════════════════
// Lives at the top of Settings > MODEL, in #auto-settings-root. Every control
// round-trips through GET/POST /router/settings; the bridge validates
// strictly, so a rejected value shows its error and the panel re-reads the
// saved state. Only the Advanced open/closed state is kept in localStorage
// (data.router.advancedOpen). No prompt text is ever shown: the routing log
// reads ledger fields only.
//
// Loaded after app.js. Uses its globals when present: API_BASE, addLog,
// loadProviders, _populateMainProviderSelect, _loadAutoMode, _settingsLoadModel,
// settingsSetModel. The same file ships in every build of the dashboard.
(function () {
  'use strict';

  // Kept in step with auto_router.CATEGORIES and auto_router.LOCKED; a test
  // (tests/test_auto_settings_ui.py) fails if they drift.
  const CATEGORIES = [
    ['chat', 'Quick chat'],
    ['lookup', 'Quick questions'],
    ['rewrite', 'Editing your text'],
    ['writing', 'Writing'],
    ['research', 'Explaining and research'],
    ['personal', 'Personal topics'],
    ['vision', 'Reading files and images'],
    ['code_help', 'Explaining code'],
    ['planning', 'Plans and decisions'],
    ['health_legal', 'Health and legal'],
    ['multi_part', 'Several requests at once'],
    ['code_change', 'Changing code'],
    ['security', 'Passwords and security'],
    ['money', 'Money'],
    ['release', 'Publishing and servers'],
    ['action', 'Sending, deleting or changing things'],
  ];
  const LOCKED = new Set(['code_change', 'security', 'money', 'release', 'action']);
  const CAT_LABEL = Object.fromEntries(CATEGORIES);

  const TIER_NAME = { light: 'Fast', mid: 'Standard', top: 'Strongest' };
  const FLOOR_OPTS = [['auto', 'Let Auto decide'], ['fast', 'Fast'], ['standard', 'Standard'], ['strongest', 'Strongest']];
  const OVERRIDE_OPTS = [['', 'Default'], ['fast', 'Fast'], ['standard', 'Standard'], ['strongest', 'Strongest'], ['off', 'Never use']];
  const BIAS_HELP = {
    save: 'Uses the fast model more often. Good when your allowance is running low.',
    balanced: 'The recommended mix: fast for quick chat, standard for most work, strongest for plans and anything risky.',
    quality: 'Uses stronger models more often. Answers may be better, and they use more of your allowance.',
  };
  // Short reasons for the routing log (mirrors auto_router.COPY short forms).
  const REASON = {
    'unclear.safe': 'playing it safe', 'triage.down': 'quick check said simple',
    'triage.up': 'quick check said careful', 'follow.approval': 'carrying on',
    'follow.context': 'carrying on', 'follow.offer': 'carrying on the work', 'follow.pick': 'your choice',
    sticky: 'same conversation', 'user.request': 'you asked', redo: 'trying stronger',
    escalated: 'retried', 'limit.cross': 'a service was busy', 'limit.up': 'next level up',
    'limit.down': 'usage limits', fallback_default: 'your home model',
    'lock.security': 'about security', 'lock.money': 'about money',
    'lock.release': 'publishing or servers', 'lock.action': 'changes something',
    'lock.code_change': 'changing code', 'lock.crisis': 'taking care', 'lock.tools': 'needs tools',
  };
  const ADV_KEY = 'data.router.advancedOpen';

  let _state = null;        // last GET /router/settings
  let _log = null;          // last GET /router/log rows
  let _busy = false;

  const $ = (id) => document.getElementById(id);
  const base = () => (typeof API_BASE !== 'undefined' ? API_BASE : '');
  const note = (m) => { if (typeof addLog === 'function') addLog(m); };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function advOpen() {
    try { return localStorage.getItem(ADV_KEY) === '1'; } catch { return false; }
  }

  function setStatus(msg, isError) {
    const el = $('auto-set-status');
    if (!el) return;
    el.textContent = msg || '';
    el.classList.toggle('auto-set-error', !!isError);
  }

  function optionTags(opts, selected) {
    return opts.map(([v, label]) =>
      `<option value="${esc(v)}"${v === selected ? ' selected' : ''}>${esc(label)}</option>`).join('');
  }

  function choiceRow(key, items, current) {
    return `<div class="settings-opt-row" role="radiogroup">` + items.map(([v, label]) =>
      `<button type="button" class="settings-opt${v === current ? ' active' : ''}" role="radio"`
      + ` aria-checked="${v === current}" data-auto-key="${key}" data-auto-val="${esc(v)}">${esc(label)}</button>`).join('')
      + `</div>`;
  }

  function toggle(key, on, label, hint) {
    return `<label class="usage-toggle auto-set-toggle"><input type="checkbox" data-auto-bool="${key}"${on ? ' checked' : ''}>`
      + `<span>${esc(label)}</span></label>` + (hint ? `<p class="settings-sub auto-set-hint">${esc(hint)}</p>` : '');
  }

  function modelStatus(c) {
    if (!c.eligible) return c.why_not || 'not available';
    if (c.local) return 'ready, runs on this computer';
    if (typeof c.headroom_pct === 'number') return `ready, ${c.headroom_pct}% of the usage window left`;
    return 'ready';
  }

  function floorRows(s) {
    const floors = s.category_floor || {};
    return CATEGORIES.map(([cat, label]) => {
      if (LOCKED.has(cat)) {
        return `<tr class="auto-set-locked"><td>${esc(label)}</td><td>Always Strongest</td></tr>`;
      }
      return `<tr><td><label for="auto-floor-${cat}">${esc(label)}</label></td><td>`
        + `<select id="auto-floor-${cat}" data-auto-floor="${cat}">${optionTags(FLOOR_OPTS, floors[cat] || 'auto')}</select></td></tr>`;
    }).join('');
  }

  function modelRows(s, cands) {
    const allowed = s.allowed || {}, ov = s.model_overrides || {};
    return cands.map((c) => {
      const pid = c.pid;
      const on = c.eligible && allowed[pid] !== false;
      const tier = TIER_NAME[c.tier] || 'not rated';
      const sel = c.eligible
        ? `<select data-auto-override="${esc(pid)}" aria-label="Treat ${esc(c.label)} as">${optionTags(
          OVERRIDE_OPTS.map(([v, l]) => [v, v ? l : `Default (${tier})`]), ov[pid] || '')}</select>`
        : `<span class="auto-set-dim">${esc(tier)}</span>`;
      return `<tr class="${c.eligible ? '' : 'auto-set-off'}">`
        + `<td><input type="checkbox" data-auto-allow="${esc(pid)}" aria-label="Auto may use ${esc(c.label)}"`
        + `${on ? ' checked' : ''}${c.eligible ? '' : ' disabled'}></td>`
        + `<td>${esc(c.label)}</td><td>${sel}</td><td class="auto-set-dim">${esc(modelStatus(c))}</td></tr>`;
    }).join('');
  }

  function fmtTime(ts) {
    const d = new Date(ts);
    if (isNaN(d)) return esc(ts || '');
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function reasonText(r) {
    const code = r.route_reason || '';
    if (code.startsWith('cat.')) return CAT_LABEL[code.slice(4)] || code.slice(4);
    return REASON[code] || code || '';
  }

  function logRows() {
    if (_log === null) return `<tr><td colspan="5" class="auto-set-dim">Loading...</td></tr>`;
    if (!_log.length) return `<tr><td colspan="5" class="auto-set-dim">No decisions recorded yet.</td></tr>`;
    const labels = Object.fromEntries((_state?.candidates || []).map((c) => [c.pid, c.label]));
    return _log.map((r) => {
      const auto = r.route_mode === 'auto';
      const model = labels[r.chosen_provider] || r.chosen_provider || '';
      const level = auto ? (TIER_NAME[r.chosen_tier] || '') : 'You chose';
      const why = auto
        ? reasonText(r) + (r.redo_of ? ' (redo)' : '') + (r.escalated ? ' (retried)' : '')
        : (r.shadow_tier ? `Auto would have used ${TIER_NAME[r.shadow_tier] || r.shadow_tier}` : '');
      const kind = CAT_LABEL[r.category] || (r.category === 'unclear' ? 'Unclear' : (r.category || ''));
      return `<tr><td>${fmtTime(r.ts)}</td><td>${esc(kind)}</td><td>${esc(level)}</td>`
        + `<td>${esc(model)}</td><td>${esc(why)}</td></tr>`;
    }).join('');
  }

  function render() {
    const root = $('auto-settings-root');
    if (!root || !_state) return;
    const s = _state.settings || {};
    const cands = _state.candidates || [];
    const auto = s.mode === 'auto';
    root.innerHTML = `
      <h3 class="settings-h">Auto model choice</h3>
      <p class="settings-sub">Auto picks a model for each message: a fast one for quick chat, a stronger one
        for harder work. Anything that changes files, involves money or security, or sends something always
        goes to the strongest model. Fast and Standard answers can read and search but never change anything.</p>
      ${choiceRow('mode', [['auto', 'AUTO (RECOMMENDED)'], ['manual', 'CHOOSE A MODEL YOURSELF']], s.mode)}
      <div class="auto-set-body${auto ? '' : ' auto-set-paused'}">
        <h4 class="auto-set-h4">How should Auto choose?</h4>
        ${choiceRow('bias', [['save', 'SAVE USAGE'], ['balanced', 'BALANCED'], ['quality', 'BEST QUALITY']], s.bias)}
        <p class="settings-sub">${esc(BIAS_HELP[s.bias] || '')}</p>
        ${auto ? '' : '<p class="settings-sub">Auto is off. Your messages go to the model you pick. These choices are kept for when you turn Auto back on.</p>'}
      </div>
      <p class="settings-sub auto-set-status" id="auto-set-status" role="status" aria-live="polite"></p>
      <details class="auto-set-adv" id="auto-set-adv"${advOpen() ? ' open' : ''}>
        <summary>Advanced</summary>

        <h4 class="auto-set-h4">Minimum model per kind of task</h4>
        <p class="settings-sub">Raise a kind of task to always get a stronger model, or lower it to save usage.
          Risky kinds are always answered by the strongest model and cannot be lowered.</p>
        <table class="auto-set-table"><tbody>${floorRows(s)}</tbody></table>

        <h4 class="auto-set-h4">Models Auto may use</h4>
        <p class="settings-sub">Auto only uses your subscriptions and models on this computer, never anything
          billed per use. Untick a model to keep Auto away from it.</p>
        <table class="auto-set-table auto-set-models">
          <thead><tr><th>Use</th><th>Model</th><th>Treat as</th><th>Status</th></tr></thead>
          <tbody>${modelRows(s, cands)}</tbody></table>
        <div class="settings-opt-row">
          <button type="button" class="settings-opt" data-auto-action="refresh-models">CHECK FOR NEW MODELS</button>
        </div>

        <h4 class="auto-set-h4">Behaviour</h4>
        ${toggle('triage', s.triage, 'Quick check for unclear requests',
          'A short, tool-free check sizes up a vague request so a simple one can go to a lighter model. Off means unclear requests go to the strongest model.')}
        <label class="auto-set-field"><span>Where the quick check runs</span>
          <select data-auto-select="triage_backend">${optionTags(
            [['auto', 'Automatic'], ['local', 'This computer'], ['claude', 'Claude']], s.triage_backend)}</select></label>
        ${toggle('escalation', s.escalation, 'Retry with a stronger model when an answer looks unsure',
          'If a lighter model is unsure, cannot do what was asked, or fails, the next level up answers automatically. The first try stays visible, dimmed.')}
        ${toggle('auto_effort', s.auto_effort !== false, 'Pick the effort level for each request',
          'Only when Effort is set to Auto: quick requests think briefly, hard or risky ones think longer. An effort level you choose yourself always wins.')}
        ${toggle('allow_step_down_on_limit', s.allow_step_down_on_limit,
          'Allow a weaker model when usage limits are hit')}
        ${toggle('show_model_names', s.show_model_names, 'Show model names on badges')}
        <label class="auto-set-field"><span>Keep the same model during a conversation for</span>
          <input type="number" min="0" max="60" step="1" data-auto-int="sticky_minutes"
            value="${esc(s.sticky_minutes)}"> <span>minutes</span></label>

        <div class="auto-set-loghead">
          <h4 class="auto-set-h4">Recent Auto choices</h4>
          <button type="button" class="settings-opt" data-auto-action="refresh-log">REFRESH</button>
        </div>
        <p class="settings-sub">The last 50 decisions. Your messages are never stored here.</p>
        <div class="auto-set-logwrap"><table class="auto-set-table auto-set-log">
          <thead><tr><th>When</th><th>Kind</th><th>Level</th><th>Model</th><th>Why</th></tr></thead>
          <tbody id="auto-set-logbody">${logRows()}</tbody></table></div>
      </details>`;
    relabelDefault(auto);
  }

  // The Default LLM row becomes Auto's home model while Auto is on (spec 8.2).
  function relabelDefault(auto) {
    const row = $('settings-model-row');
    if (!row) return;
    const sub = row.previousElementSibling;
    const h = sub && sub.previousElementSibling;
    if (!h || !sub || !h.classList.contains('settings-h')) return;
    if (!h.dataset.autoOrig) { h.dataset.autoOrig = h.textContent; sub.dataset.autoOrig = sub.textContent; }
    h.textContent = auto ? 'Home model' : h.dataset.autoOrig;
    sub.textContent = auto ? 'Used when Auto has no better choice. Picking one here keeps Auto on.' : sub.dataset.autoOrig;
  }

  async function load() {
    const root = $('auto-settings-root');
    if (!root) return;
    try {
      const r = await fetch(`${base()}/router/settings`);
      if (!r.ok) { root.hidden = true; return; }
      _state = await r.json();
    } catch { root.hidden = true; return; }
    root.hidden = false;
    render();
    if (advOpen()) loadLog();
  }

  async function loadLog() {
    try {
      const r = await fetch(`${base()}/router/log?limit=50`);
      _log = r.ok ? ((await r.json()).rows || []) : [];
    } catch { _log = []; }
    const body = $('auto-set-logbody');
    if (body) body.innerHTML = logRows();
  }

  async function syncSelectors() {
    try {
      if (typeof loadProviders === 'function') await loadProviders();
      else if (typeof _loadAutoMode === 'function') await _loadAutoMode();
      if (typeof _populateMainProviderSelect === 'function') _populateMainProviderSelect();
    } catch { /* selectors refresh on their own next poll */ }
  }

  async function save(patch, okMsg) {
    if (_busy) return;
    _busy = true;
    try {
      const r = await fetch(`${base()}/router/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) {
        await load();
        setStatus(`Not saved: ${d.error || `HTTP ${r.status}`}`, true);
        return;
      }
      _state = { ..._state, settings: d.settings };
      render();
      setStatus(okMsg || 'Saved.');
      if ('mode' in patch) {
        note(d.settings.mode === 'auto' ? 'Auto model choice on' : 'Auto model choice off');
        await syncSelectors();
      }
    } catch (e) {
      setStatus(`Not saved: ${e.message || e}`, true);
    } finally {
      _busy = false;
    }
  }

  async function refreshModels(btn) {
    btn.disabled = true;
    setStatus('Checking for new models...');
    try {
      const r = await fetch(`${base()}/models/refresh`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      if (r.status === 404) {
        setStatus('This build checks for new models when it starts.');
      } else if (!r.ok) {
        setStatus(`Check failed: HTTP ${r.status}`, true);
      } else {
        await load();
        setStatus('Model list is up to date.');
      }
    } catch (e) {
      setStatus(`Check failed: ${e.message || e}`, true);
    } finally {
      btn.disabled = false;
    }
  }

  function onClick(e) {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.autoKey) {
      const k = b.dataset.autoKey, v = b.dataset.autoVal;
      if (_state?.settings?.[k] !== v) save({ [k]: v });
    } else if (b.dataset.autoAction === 'refresh-log') {
      loadLog();
    } else if (b.dataset.autoAction === 'refresh-models') {
      refreshModels(b);
    }
  }

  function onChange(e) {
    const t = e.target, ds = t.dataset;
    if (ds.autoBool) save({ [ds.autoBool]: t.checked });
    else if (ds.autoSelect) save({ [ds.autoSelect]: t.value });
    else if (ds.autoFloor) save({ category_floor: { [ds.autoFloor]: t.value } });
    else if (ds.autoAllow) save({ allowed: { [ds.autoAllow]: t.checked } });
    else if (ds.autoOverride !== undefined) {
      // "Default" sends null, which the bridge treats as "remove this key".
      save({ model_overrides: { [ds.autoOverride]: t.value || null } });
    } else if (ds.autoInt) {
      const n = Number(t.value);
      if (!Number.isInteger(n) || n < 0 || n > 60) { setStatus('Enter a whole number from 0 to 60.', true); return; }
      save({ [ds.autoInt]: n });
    }
  }

  function onToggle(e) {
    if (e.target.id !== 'auto-set-adv') return;
    const open = e.target.open;
    try { localStorage.setItem(ADV_KEY, open ? '1' : '0'); } catch { /* private mode */ }
    if (open && _log === null) loadLog();
  }

  function mount() {
    const root = $('auto-settings-root');
    if (!root || root.dataset.autoBound) return;
    root.dataset.autoBound = '1';
    root.addEventListener('click', onClick);
    root.addEventListener('change', onChange);
    root.addEventListener('toggle', onToggle, true);
  }

  // Refresh this panel whenever Settings repaints the MODEL pane.
  if (typeof window._settingsLoadModel === 'function') {
    const orig = window._settingsLoadModel;
    window._settingsLoadModel = function (...args) {
      const p = orig.apply(this, args);
      mount();
      load();
      return p;
    };
  }
  // While Auto is on, picking a Default LLM sets Auto's home model and keeps
  // Auto on (the row is labelled "Home model"). With Auto off, unchanged.
  if (typeof window.settingsSetModel === 'function') {
    const origSet = window.settingsSetModel;
    window.settingsSetModel = async function (id) {
      if (_state?.settings?.mode !== 'auto') return origSet.call(this, id);
      try {
        const r = await fetch(`${base()}/provider`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: id }),
        });
        const d = await r.json();
        if (d.error) { note(`Home model switch failed: ${d.error}`); return; }
        note(`Auto home model: ${id}`);
      } catch (e) { note(`Home model switch error: ${e.message || e}`); }
      await syncSelectors();
      window._settingsLoadModel();
    };
  }

  window.AutoSettings = { load, render, loadLog };
})();
