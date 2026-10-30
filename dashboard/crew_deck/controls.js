'use strict';
/* Mission controls use only this page's local service and never a bridge URL. */
(async function () {
  await window.CrewAccess.ready;
  const $ = (s, within = document) => within.querySelector(s);
  const $$ = (s, within = document) => Array.from(within.querySelectorAll(s));
  const alive = new Set(['queued', 'running', 'waiting', 'paused', 'pausing', 'stopping', 'blocked']);
  const api = '/api/crew';
  let state = null;
  let activeRun = '';
  let activeTab = 'missions';
  let online = false;
  let loading = true;
  let busy = false;
  let pollBusy = false;
  let pollTimer = 0;
  let failures = 0;
  let mutationEpoch = 0;
  let templateEdit = '';
  let personalityId = '';
  let personalityRequest = 0;
  let signatures = {};
  let noticeTimer = 0;
  let lastMode = '';
  const focusBeforeDialog = new WeakMap();

  function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = String(text ?? '');
    return e;
  }
  function button(text, action, kind = '') {
    const b = el('button', 'crew-button ' + kind, text);
    b.type = 'button';
    b.addEventListener('click', action);
    return b;
  }
  function isDemo() { return (window.__crewDeck?.S?.mode || new URLSearchParams(location.search).get('mode')) === 'demo'; }
  function writable() { return !!(online && state?.csrf && !busy && !isDemo()); }
  function canRun() { return writable() && !!state?.capabilities?.ready; }
  function officerName(id) { return state?.roster?.find(o => o.id === id)?.name || (id === 'operator' ? 'User' : id === 'system' ? 'System' : id) || 'System'; }
  function dateValue(value) {
    if (!value) return null;
    const n = typeof value === 'number' ? (value < 1e12 ? value * 1000 : value) : value;
    const d = new Date(n);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  function time(value) {
    return dateValue(value)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) || '';
  }
  function dateTime(value) { return dateValue(value)?.toLocaleString() || 'Not recorded'; }
  function period(n) {
    const seconds = Number(n) || 60;
    return seconds % 3600 === 0 ? (seconds / 3600) + 'h' : seconds % 60 === 0 ? (seconds / 60) + 'm' : seconds + 's';
  }
  function changed(name, value) {
    const next = JSON.stringify(value);
    if (signatures[name] === next) return false;
    signatures[name] = next;
    return true;
  }
  function rememberFocus(container, work) {
    const current = document.activeElement;
    const key = container.contains(current) ? current.dataset.focusKey : '';
    work();
    if (key) $$('[data-focus-key]', container).find(e => e.dataset.focusKey === key)?.focus({ preventScroll: true });
  }
  function mutationButton(b, run = false) {
    b.dataset.crewMutation = run ? 'run' : 'write';
    b.disabled = run ? !canRun() : !writable();
    return b;
  }

  const panel = el('section', 'crew-panel');
  panel.id = 'crew-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Crew missions');
  panel.innerHTML = `
    <header class="crew-panel-head">
      <div class="crew-panel-title"><span class="crew-eyebrow">MISSION CONTROL</span><span id="crew-connection" class="crew-connection" role="status">Connecting...</span></div>
      <nav class="crew-tablist" role="tablist" aria-label="Mission control">
        <button id="crew-tab-missions" role="tab" aria-selected="true" aria-controls="crew-missions-view" tabindex="0">MISSIONS</button>
        <button id="crew-tab-templates" role="tab" aria-selected="false" aria-controls="crew-templates-view" tabindex="-1">SAVED ORDERS</button>
      </nav>
      <div class="crew-head-actions"><button id="crew-new-order" class="crew-button primary" type="button" aria-haspopup="dialog">+ NEW ORDER</button><button id="crew-panel-close" class="crew-close" type="button" aria-label="Close mission control">&times;</button></div>
    </header>
    <div id="crew-mode-notice" class="crew-mode-notice" hidden>DEMO is a scripted preview. Switch to LIVE to manage real missions.</div>
    <div id="crew-service-error" class="crew-inline-error" role="status" hidden></div>
    <div id="crew-missions-view" class="crew-missions-view" role="tabpanel" aria-labelledby="crew-tab-missions">
      <aside class="crew-run-sidebar" aria-label="Mission list"><div class="crew-section-heading">MISSION REGISTER<span id="crew-run-count">0</span></div><div id="crew-run-list" class="crew-run-list"></div></aside>
      <section class="crew-conversation" aria-label="Selected mission">
        <div id="crew-run-info" class="crew-run-info"></div>
        <section id="crew-handoff" class="crew-handoff" hidden aria-label="Pending external handoff"><div><div class="crew-handoff-heading"><strong>EXTERNAL HANDOFF</strong><span id="crew-handoff-estimate"></span></div><p id="crew-handoff-reason"></p></div><button id="crew-handoff-open" type="button" class="crew-button" aria-haspopup="dialog">RECORD OUTCOME</button></section>
        <div class="crew-event-tools"><span id="crew-evidence-count">CONVERSATION &amp; EVIDENCE</span><label class="crew-follow"><input type="checkbox" id="crew-follow" checked>Follow latest</label></div>
        <ol id="crew-events" class="crew-events" role="log" aria-label="Mission conversation and evidence" aria-live="polite" aria-relevant="additions"></ol>
        <form id="crew-steer-form" class="crew-steer-form"><label class="crew-sr-only" for="crew-steer">Instruction for this mission</label><textarea id="crew-steer" rows="1" maxlength="6000" placeholder="Give the crew a course correction..." required></textarea><button type="submit" class="crew-button" data-crew-mutation="write">SEND</button><span id="crew-steer-help" class="crew-help">Delivered at the next turn. Use Shift+Enter for a new line.</span></form>
      </section>
    </div>
    <div id="crew-templates-view" class="crew-templates-view" role="tabpanel" aria-labelledby="crew-tab-templates" hidden><div class="crew-section-heading">REPEATABLE ORDERS<span class="crew-help">Each run keeps its own conversation.</span></div><div id="crew-template-list" class="crew-template-list"></div></div>`;
  $('#app').append(panel);

  function dialog(id, label, content, wide = false) {
    const d = el('dialog', 'crew-dialog' + (wide ? ' crew-dialog-wide' : ''));
    d.id = id;
    d.setAttribute('aria-labelledby', id + '-title');
    d.innerHTML = `<header class="crew-dialog-head"><div><span class="crew-eyebrow">CREW DECK</span><h2 id="${id}-title">${label}</h2></div><button class="crew-close" type="button" aria-label="Close dialog">&times;</button></header>${content}`;
    $('.crew-close', d).addEventListener('click', () => d.close());
    d.addEventListener('click', e => { if (e.target === d) { const b = d.getBoundingClientRect(); if (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom) d.close(); } });
    d.addEventListener('close', () => focusBeforeDialog.get(d)?.focus({ preventScroll: true }));
    document.body.append(d);
    return d;
  }

  const orderDialog = dialog('crew-order-dialog', 'Give the crew an order', `
    <form id="crew-order-form" class="crew-form">
      <div id="crew-order-error" class="crew-inline-error" role="alert" hidden></div>
      <label class="crew-field">MISSION NAME<input id="crew-order-title" name="title" maxlength="120" placeholder="Find our next business opportunity" required autofocus></label>
      <label class="crew-field">ORDER<textarea id="crew-order-text" name="order" rows="5" maxlength="20000" placeholder="Describe the outcome, constraints, and what success looks like. The crew will build on each other's work." required></textarea></label>
      <fieldset class="crew-officer-fieldset"><legend>ASSEMBLE THE TEAM</legend><div class="crew-selection-tools"><button type="button" id="crew-select-all" class="crew-text-button">Select all</button><button type="button" id="crew-select-none" class="crew-text-button">Clear</button><span id="crew-selected-count" class="crew-help"></span></div><div id="crew-officer-choices" class="crew-officer-choices"></div></fieldset>
      <div class="crew-form-grid"><label class="crew-field">RUN MODE<select id="crew-order-mode" name="mode"><option value="once">One mission, until complete</option><option value="continuous">Continuous until stopped</option></select></label><label class="crew-field">BETWEEN ROUNDS (SECONDS)<input id="crew-order-interval" type="number" min="10" max="86400" step="1" value="60" required></label></div>
      <p id="crew-order-mode-help" class="crew-help"></p>
      <details class="crew-funding"><summary>Mission budget and wallet reference (optional)</summary><p class="crew-help">For planning only. No wallet signer is connected. Enter a public address, never a private key or seed phrase. A budget here does not authorize spending.</p><div class="crew-form-grid"><label class="crew-field">PLANNING BUDGET (USD)<input id="crew-order-budget" type="number" min="0" max="1000000" step="0.01" placeholder="100"></label><label class="crew-field">PUBLIC WALLET ADDRESS<input id="crew-order-wallet" minlength="20" maxlength="140" autocomplete="off" spellcheck="false" placeholder="Public address only"></label></div></details>
      <label class="crew-checkline"><input id="crew-order-save" type="checkbox" checked><span>Save this as a repeatable order</span></label>
      <div class="crew-usage"><strong id="crew-usage-title">MISSION USAGE</strong><p id="crew-usage-description"></p><label class="crew-checkline"><input id="crew-usage-ack" type="checkbox"><span id="crew-usage-ack-label">I understand the usage and want to start this mission.</span></label></div>
      <div class="crew-form-actions"><button id="crew-order-save-only" type="button" class="crew-button" data-crew-mutation="write">SAVE ORDER ONLY</button><button id="crew-order-start" type="submit" class="crew-button primary" data-crew-mutation="run">START MISSION</button></div>
      <p class="crew-help">The mission starts only when you press Start mission. Saving an order runs nothing.</p>
    </form>`, true);

  const settingsDialog = dialog('crew-settings-dialog', 'Deck settings', `
    <form id="crew-settings-form" class="crew-form">
      <div id="crew-settings-error" class="crew-inline-error" role="alert" hidden></div>
      <div id="crew-settings-readonly" class="crew-mode-notice" hidden>Settings are read-only in DEMO. Switch to LIVE to save changes.</div>
      <section class="crew-settings-section"><div class="crew-settings-title"><span class="crew-step">01</span><div><h3>Conversation</h3><p>Ambient exchanges draw from each officer's current personality.</p></div></div>
        <label class="crew-switch"><span><strong>Ambient banter</strong><small>Allow off-duty conversations on the ship.</small></span><input name="banter_enabled" type="checkbox" role="switch"></label>
        <div class="crew-form-grid"><label class="crew-field">BANTER INTERVAL (SECONDS)<input name="banter_interval_seconds" type="number" min="6" max="180" step="1" required></label><label class="crew-field">BUBBLE DURATION (SECONDS)<input name="bubble_seconds" type="number" min="3" max="15" step="1" required></label></div>
        <label class="crew-field">CONVERSATION VARIETY <output id="crew-variety-value"></output><input name="banter_variety" type="range" min="0" max="1" step="0.05"></label><div class="crew-range-labels"><span>Familiar habits</span><span>More surprises</span></div>
        <label class="crew-switch"><span><strong>Reduced motion</strong><small>Calmer ship animation and interface transitions.</small></span><input name="reduced_motion" type="checkbox" role="switch"></label>
      </section>
      <section class="crew-settings-section"><div class="crew-settings-title"><span class="crew-step">02</span><div><h3>Learning and identity</h3><p>Let experiences shape how the officers work together.</p></div></div>
        <label class="crew-switch"><span><strong>Allow personality evolution</strong><small>Officers can update their own learned personality notes after mission rounds. Every revision is saved and can be restored.</small></span><input name="personality_evolution" type="checkbox" role="switch"></label>
        <label class="crew-field">REFLECT EVERY N ROUNDS<input name="evolution_every_rounds" type="number" min="1" max="20" step="1" required></label>
        <div id="crew-personality-roster" class="crew-personality-roster"></div>
      </section>
      <section class="crew-settings-section"><div class="crew-settings-title"><span class="crew-step">03</span><div><h3>Mission execution</h3><p>Defaults for the next mission. Existing runs keep their own interval.</p></div></div>
        <label class="crew-field">EXECUTION PROVIDER<select name="provider"><option value="codex">Codex subscription</option><option value="ollama">Ollama on this computer</option></select></label>
        <label id="crew-model-field" class="crew-field">LOCAL MODEL<input name="local_model" maxlength="100" placeholder="Installed Ollama model name" spellcheck="false"></label>
        <p id="crew-settings-provider-note" class="crew-help"></p>
        <div class="crew-form-grid"><label class="crew-field">DEFAULT ROUND INTERVAL (SECONDS)<input name="mission_interval_seconds" type="number" min="10" max="86400" step="1" required></label><label class="crew-field">TURN TIMEOUT (SECONDS)<input name="turn_timeout_seconds" type="number" min="30" max="900" step="1" required></label></div>
        <label class="crew-field">ONE-MISSION REVIEW LIMIT (ROUNDS)<input name="max_rounds" type="number" min="1" max="100" step="1" required></label><p class="crew-help">Single missions pause here if the objective is still incomplete. Continuous missions keep running until stopped.</p>
      </section>
      <div class="crew-form-actions"><button type="button" class="crew-button" id="crew-settings-cancel">CANCEL</button><button type="submit" class="crew-button primary" data-crew-mutation="write">SAVE SETTINGS</button></div>
    </form>`, true);

  const personalityDialog = dialog('crew-personality-dialog', 'Personality record', `
    <div class="crew-form"><div id="crew-personality-error" class="crew-inline-error" role="alert" hidden></div><p id="crew-personality-source" class="crew-help"></p><div id="crew-personality-content"></div><h3 class="crew-small-heading">VERSION HISTORY</h3><div id="crew-personality-history"></div></div>`, true);
  const handoffDialog = dialog('crew-handoff-dialog', 'Record an external outcome', `
    <form id="crew-handoff-form" class="crew-form"><div class="crew-handoff-heading"><strong id="crew-handoff-dialog-officer"></strong><span id="crew-handoff-dialog-estimate"></span></div><p id="crew-handoff-dialog-reason" class="crew-preserve"></p><p class="crew-help">Record what you completed elsewhere, or skip this step. These buttons record the outcome and do not perform an external action. The mission stays paused until you resume it.</p><label class="crew-field">OUTCOME OR REASON<textarea id="crew-handoff-note" rows="4" maxlength="4000" required placeholder="Record the result, confirmation, or reason to skip..."></textarea></label><div id="crew-handoff-error" class="crew-inline-error" role="alert" hidden></div><div class="crew-form-actions crew-handoff-actions"><button type="submit" data-decision="skip" data-crew-mutation="write" class="crew-button">SKIP STEP</button><button type="submit" data-decision="done" data-crew-mutation="write" class="crew-button primary">RECORD COMPLETED EXTERNALLY</button></div></form>`, true);
  const confirmDialog = dialog('crew-confirm-dialog', 'Confirm action', `
    <div class="crew-form"><p id="crew-confirm-text"></p><div id="crew-confirm-error" class="crew-inline-error" role="alert" hidden></div><div class="crew-form-actions"><button id="crew-confirm-cancel" type="button" class="crew-button">CANCEL</button><button id="crew-confirm-yes" type="button" class="crew-button danger">CONFIRM</button></div></div>`);
  const toast = el('div', 'crew-toast');
  toast.setAttribute('role', 'status');
  toast.hidden = true;
  document.body.append(toast);

  function showDialog(d) {
    if (!d.open) { focusBeforeDialog.set(d, document.activeElement); d.showModal(); d.scrollTop = 0; }
  }
  function notice(message) {
    clearTimeout(noticeTimer);
    toast.textContent = message;
    toast.hidden = false;
    noticeTimer = setTimeout(() => { toast.hidden = true; }, 5000);
  }
  function errorAt(id, error) {
    const e = $(id);
    e.textContent = error ? String(error.message || error) : '';
    e.hidden = !error;
  }
  function setTab(tab, focus = false) {
    activeTab = tab === 'templates' ? 'templates' : 'missions';
    for (const name of ['missions', 'templates']) {
      const selected = name === activeTab;
      const b = $('#crew-tab-' + name);
      b.setAttribute('aria-selected', String(selected));
      b.tabIndex = selected ? 0 : -1;
      $('#crew-' + name + '-view').hidden = !selected;
      if (selected && focus) b.focus();
    }
  }
  function open(tab = 'missions') {
    if (tab === 'settings') return openSettings();
    if (tab === 'new') return openOrder();
    panel.hidden = false;
    document.body.classList.add('crew-panel-open');
    $('#crew-missions-toggle').classList.add('on');
    $('#crew-missions-toggle').setAttribute('aria-expanded', 'true');
    setTab(tab);
    render();
  }
  function closePanel() {
    panel.hidden = true;
    document.body.classList.remove('crew-panel-open');
    $('#crew-missions-toggle').classList.remove('on');
    $('#crew-missions-toggle').setAttribute('aria-expanded', 'false');
    $('#crew-missions-toggle').focus();
  }

  async function request(path, body) {
    const ctl = new AbortController();
    const timeout = setTimeout(() => ctl.abort(), 15000);
    try {
      const response = await window.CrewAccess.fetch(api + path, {
        method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', signal: ctl.signal,
        headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Crew-CSRF': state?.csrf || '' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Crew service returned an unreadable response.'); }
      if (!response.ok || data.error) {
        const error = new Error(data.error || 'Crew service returned HTTP ' + response.status + '.');
        error.scopeUnverified = [401, 403].includes(response.status) || /active operator|verify the active|private crew|scope|belongs to/i.test(error.message);
        throw error;
      }
      return data;
    } catch (e) {
      if (e.name === 'AbortError') throw new Error('Crew service did not respond in time. Check the mission register before trying again.');
      throw e;
    } finally { clearTimeout(timeout); }
  }
  function applyState(result) {
    const next = result.state || result;
    if (!next || next.scope !== window.CrewAccess.scope) {
      clearPrivateState();
      const error = new Error('The active profile changed. Reopen Crew Deck from the dashboard.'); error.scopeUnverified = true; throw error;
    }
    if (!next.settings || !Array.isArray(next.roster)) throw new Error('Crew service returned incomplete state.');
    const firstState = !state;
    online = true;
    loading = false;
    failures = 0;
    errorAt('#crew-service-error', null);
    state = next;
    window.CrewControls.state = state;
    if (!state.runs?.some(r => r.id === activeRun)) activeRun = state.runs?.find(r => alive.has(r.status))?.id || state.runs?.[0]?.id || '';
    if (changed('state', next)) window.dispatchEvent(new CustomEvent('crew-control-state', { detail: state }));
    document.body.classList.toggle('crew-reduced-motion', !!state.settings.reduced_motion);
    if (firstState && settingsDialog.open) openSettings();
    if (firstState && orderDialog.open) populateOfficers((state.roster || []).slice(0, 3).map(o => o.id));
    render();
  }
  function clearPrivateState() {
    state = null; activeRun = ''; templateEdit = ''; personalityId = ''; personalityRequest += 1;
    online = false; signatures = {};
    window.CrewControls.state = null;
    window.dispatchEvent(new CustomEvent('crew-control-state', { detail: null }));
    for (const d of [confirmDialog, personalityDialog, settingsDialog, orderDialog, handoffDialog]) if (d.open) d.close();
    $('#crew-order-form').reset(); $('#crew-settings-form').reset();
    $('#crew-officer-choices').replaceChildren(); $('#crew-personality-roster').replaceChildren();
    $('#crew-personality-content').replaceChildren(); $('#crew-personality-history').replaceChildren();
    $('#crew-personality-source').textContent = '';
    $('#crew-confirm-text').textContent = '';
    $('#crew-steer').value = ''; $('#crew-handoff-note').value = '';
    render();
  }
  async function refresh() {
    if (pollBusy || busy) return schedulePoll();
    pollBusy = true;
    const epoch = mutationEpoch;
    try {
      const result = await request('/state');
      if (epoch === mutationEpoch && !busy) applyState(result);
    } catch (e) {
      if (epoch === mutationEpoch) {
        loading = false;
        failures += 1;
        clearPrivateState();
        errorAt('#crew-service-error', 'Mission service unavailable: ' + e.message + ' Retrying automatically.');
        render();
      }
    } finally { pollBusy = false; schedulePoll(); }
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(refresh, Math.min(30000, 2000 * Math.pow(2, Math.min(failures, 4))));
  }
  async function mutate(path, body) {
    if (isDemo()) throw new Error('Switch to LIVE to manage real missions.');
    if (busy) throw new Error('An action is already being saved.');
    if (!online || !state?.csrf) throw new Error('Wait for the mission service to reconnect.');
    busy = true;
    mutationEpoch += 1;
    updateLocks();
    try {
      const result = await request(path, body);
      applyState(result);
      return result.state || result;
    } catch (error) {
      if (error.scopeUnverified) { clearPrivateState(); errorAt('#crew-service-error', error); notice(error.message); }
      throw error;
    } finally { busy = false; updateLocks(); schedulePoll(); }
  }
  function updateLocks() {
    $$('[data-crew-mutation]').forEach(b => { b.disabled = b.dataset.crewMutation === 'run' ? !canRun() : !writable(); });
    const selected = state?.runs?.find(r => r.id === activeRun);
    const steerable = writable() && selected && alive.has(selected.status) && !['stopping', 'pausing'].includes(selected.status);
    $('#crew-steer').disabled = !steerable;
    $('button', $('#crew-steer-form')).disabled = !steerable;
    $('#crew-steer-form').setAttribute('aria-busy', String(busy));
    $('#crew-mode-notice').hidden = !isDemo();
    $('#crew-settings-readonly').hidden = !isDemo();
    $('#crew-order-form').setAttribute('aria-busy', String(busy));
    $('#crew-settings-form').setAttribute('aria-busy', String(busy));
    const connection = $('#crew-connection');
    connection.textContent = loading ? 'Connecting...' : busy ? 'Saving...' : online ? 'LOCAL SERVICE ONLINE' : 'SERVICE OFFLINE';
    connection.className = 'crew-connection ' + (online ? 'online' : loading ? '' : 'offline');
  }
  function empty(parent, title, detail, action) {
    const box = el('div', 'crew-empty');
    box.append(el('strong', '', title), el('p', '', detail));
    if (action) box.append(action);
    parent.append(box);
  }
  function render() {
    const runs = state?.runs || [];
    const count = runs.filter(r => alive.has(r.status)).length;
    const countEl = $('#crew-active-count');
    countEl.textContent = String(count);
    countEl.hidden = count === 0;
    $('#crew-run-count').textContent = String(runs.length);
    renderRuns(runs);
    renderSelected(runs.find(r => r.id === activeRun));
    renderTemplates(state?.templates || []);
    updateLocks();
  }
  function renderRuns(runs) {
    if (!changed('runs', [runs, activeRun, loading])) return;
    const list = $('#crew-run-list');
    rememberFocus(list, () => {
      list.replaceChildren();
      if (!runs.length) { empty(list, loading ? 'Connecting to crew...' : 'No missions yet', 'Give the crew an order to begin a shared task.'); return; }
      const sorted = [...runs].sort((a, b) => Number(alive.has(b.status)) - Number(alive.has(a.status)) || (dateValue(b.created_at)?.getTime() || 0) - (dateValue(a.created_at)?.getTime() || 0));
      for (const r of sorted) {
        const b = button('', () => {
          activeRun = r.id;
          $('#crew-steer').value = '';
          signatures.events = '';
          render();
        }, 'crew-run-item' + (r.id === activeRun ? ' selected' : ''));
        b.dataset.focusKey = 'run-' + r.id;
        b.setAttribute('aria-pressed', String(r.id === activeRun));
        const status = el('span', 'crew-status', String(r.status || 'unknown').toUpperCase());
        status.dataset.status = r.status;
        b.append(status, el('strong', '', r.title), el('span', 'crew-run-meta', 'ROUND ' + (r.round || 0) + ' / ' + (r.mode === 'continuous' ? 'CONTINUOUS' : 'ONE MISSION')));
        if (r.current_officer) b.append(el('span', 'crew-run-officer', officerName(r.current_officer)));
        list.append(b);
      }
    });
  }
  function renderSelected(run) {
    const info = $('#crew-run-info');
    if (changed('selected', [run, state?.roster])) rememberFocus(info, () => {
      info.replaceChildren();
      if (!run) { info.append(el('h3', '', 'The next mission starts here'), el('p', 'crew-help', 'Choose an order or create one for the crew.')); return; }
      const title = el('div', 'crew-run-heading');
      const text = el('div');
      text.append(el('h3', '', run.title), el('p', 'crew-help', (run.officers || []).map(officerName).join(' + ')));
      const actions = el('div', 'crew-run-actions');
      const makeAction = (label, action, kind = '') => {
        const b = mutationButton(button(label, () => {
          if (action === 'stop') confirm('Stop this mission?', 'The current turn will be stopped and no more rounds will run. Its conversation and work remain available.', 'STOP MISSION', () => controlRun(run.id, action));
          else controlRun(run.id, action).catch(e => notice(e.message));
        }, kind));
        b.dataset.focusKey = 'control-' + action;
        actions.append(b);
      };
      if (['paused', 'blocked'].includes(run.status) && !run.pending_approval) makeAction('RESUME', 'resume');
      else if (['queued', 'running', 'waiting'].includes(run.status)) makeAction('PAUSE', 'pause');
      if (alive.has(run.status) && run.status !== 'stopping') makeAction('STOP', 'stop', 'danger');
      else if (!alive.has(run.status)) actions.append(button('RUN AGAIN', () => openOrder(run), ''));
      title.append(text, actions);
      info.append(title);
      const progress = el('div', 'crew-progress');
      const st = el('span', 'crew-status', String(run.status || 'unknown').toUpperCase()); st.dataset.status = run.status;
      progress.append(st, el('span', '', 'Round ' + (run.round || 0) + ' / Turn ' + (run.turn || 0)));
      if (run.current_officer) progress.append(el('span', '', officerName(run.current_officer)));
      if (run.next_run_at && run.status === 'waiting') progress.append(el('span', '', 'Next round: ' + time(run.next_run_at)));
      info.append(progress);
      if (run.current_step) info.append(el('p', 'crew-current-step', run.current_step));
      if (run.error) info.append(el('p', 'crew-inline-error', run.error));
      const details = el('details', 'crew-mission-details'); details.append(el('summary', '', 'Original order and workspace'));
      details.append(el('p', 'crew-preserve', run.order));
      if (run.workspace) details.append(el('p', 'crew-help crew-break', 'Workspace: ' + run.workspace));
      if (run.budget_usd !== undefined && run.budget_usd !== null) details.append(el('p', 'crew-help', 'Planning budget: $' + Number(run.budget_usd).toFixed(2) + '. Wallet execution is not connected.'));
      if (run.wallet_address) details.append(el('p', 'crew-help crew-break', 'Public wallet reference: ' + run.wallet_address));
      info.append(details);
    });
    renderHandoff(run);
    renderEvents(run);
  }
  function renderHandoff(run) {
    const pending = run && alive.has(run.status) ? run.pending_approval : null;
    $('#crew-handoff').hidden = !pending;
    if (!changed('handoff', [run?.id, pending])) return;
    $('#crew-handoff-note').value = '';
    errorAt('#crew-handoff-error', null);
    $('#crew-handoff-reason').textContent = pending?.reason || '';
    $('#crew-handoff-estimate').textContent = pending ? officerName(pending.officer) + ' / Estimated cost: $' + (Number(pending.estimated_cost_usd) || 0).toFixed(2) : '';
    $('#crew-handoff-dialog-reason').textContent = pending?.reason || '';
    $('#crew-handoff-dialog-officer').textContent = pending ? officerName(pending.officer) : '';
    $('#crew-handoff-dialog-estimate').textContent = pending ? 'Estimated cost: $' + (Number(pending.estimated_cost_usd) || 0).toFixed(2) : '';
    if (!pending && handoffDialog.open) handoffDialog.close();
  }
  function renderEvents(run) {
    const events = (state?.events || []).filter(e => e.run_id === run?.id);
    if (!changed('events', [run?.id, events, loading])) return;
    const log = $('#crew-events');
    const shouldFollow = $('#crew-follow').checked;
    const previousScroll = log.scrollTop;
    if (log.dataset.run !== (run?.id || '')) { log.replaceChildren(); log.dataset.run = run?.id || ''; }
    $('#crew-evidence-count').textContent = 'CONVERSATION & EVIDENCE' + (events.length ? ' / ' + events.length : '');
    if (!run) { log.replaceChildren(); empty(log, loading ? 'Loading mission records...' : 'A shared place to work', 'Real crew messages, tool activity, and saved artifacts appear here once a mission runs.'); return; }
    if (!events.length) { log.replaceChildren(); empty(log, 'Waiting for the first turn', 'The first crew event will appear here when the mission begins.'); return; }
    $$('.crew-empty', log).forEach(e => e.remove());
    const retained = new Set(events.map(e => String(e.id)));
    const existing = new Map($$('.crew-event', log).map(e => [e.dataset.eventId, e]));
    for (const [id, node] of existing) if (!retained.has(id)) node.remove();
    for (const event of events) {
      const previous = existing.get(String(event.id));
      const fingerprint = JSON.stringify(event);
      if (previous?.crewEventFingerprint === fingerprint) continue;
      const li = el('li', 'crew-event');
      li.dataset.eventId = String(event.id);
      li.crewEventFingerprint = fingerprint;
      li.dataset.kind = event.kind || 'status';
      const head = el('div', 'crew-event-head');
      const tag = el('span', 'crew-event-kind', event.kind || 'status');
      const t = el('time', '', time(event.at));
      const d = dateValue(event.at); if (d) t.dateTime = d.toISOString();
      t.title = dateTime(event.at);
      head.append(el('strong', '', officerName(event.officer)), tag, t);
      li.append(head, el('div', 'crew-event-text', event.text));
      if (event.artifact) {
        const artifact = typeof event.artifact === 'string' ? { path: event.artifact } : event.artifact;
        if (artifact.path) {
          const a = el('a', 'crew-artifact', artifact.label || artifact.name || artifact.path);
          a.href = api + '/artifact?' + new URLSearchParams({ run: run.id, path: artifact.path }).toString();
          a.target = '_blank'; a.rel = 'noopener noreferrer';
          a.append(el('span', '', ' / OPEN ARTIFACT'));
          li.append(a);
        }
      }
      if (previous) previous.replaceWith(li); else log.append(li);
    }
    if (shouldFollow) log.scrollTop = log.scrollHeight;
    else log.scrollTop = previousScroll;
  }
  function renderTemplates(templates) {
    if (!changed('templates', [templates, state?.roster, loading])) return;
    const list = $('#crew-template-list');
    rememberFocus(list, () => {
      list.replaceChildren();
      if (!templates.length) { empty(list, loading ? 'Loading saved orders...' : 'Build a library of repeatable work', 'Save an order once, then launch a fresh mission whenever you need it.', button('CREATE AN ORDER', () => openOrder(), 'primary')); return; }
      for (const t of templates) {
        const card = el('article', 'crew-template');
        const top = el('div', 'crew-template-top');
        top.append(el('h3', '', t.title), el('span', 'crew-template-mode', t.mode === 'continuous' ? 'CONTINUOUS / ' + period(t.interval_seconds) : 'ONE MISSION'));
        const body = el('p', 'crew-template-order', t.order); body.title = t.order;
        card.append(top, body, el('p', 'crew-help', (t.officers || []).map(officerName).join(' + ')));
        const actions = el('div', 'crew-template-actions');
        const run = button('RUN AGAIN', () => openOrder(t), 'primary');
        const use = button('USE', () => openOrder(t));
        const edit = button('EDIT', () => openOrder(t, true));
        const del = mutationButton(button('DELETE', () => confirm('Delete saved order?', 'Remove "' + t.title + '" from saved orders. Previous runs and their work will remain available.', 'DELETE ORDER', async () => { await mutate('/templates/delete', { id: t.id }); notice('Saved order deleted.'); }), 'crew-subtle-danger'));
        [run, use, edit, del].forEach((b, i) => { b.dataset.focusKey = 'template-' + t.id + '-' + i; actions.append(b); });
        card.append(actions); list.append(card);
      }
    });
  }

  function populateOfficers(selected) {
    const list = $('#crew-officer-choices');
    list.replaceChildren();
    for (const o of state?.roster || []) {
      const label = el('label', 'crew-officer-choice');
      const input = el('input'); input.type = 'checkbox'; input.value = o.id; input.name = 'officers'; input.checked = selected.includes(o.id);
      input.addEventListener('change', selectionCount);
      const text = el('span'); text.append(el('strong', '', o.name), el('small', '', o.role));
      label.append(input, text); list.append(label);
    }
    if (!list.childNodes.length) empty(list, 'Roster unavailable', 'Wait for the crew service to connect.');
    selectionCount();
  }
  function selectionCount() { $('#crew-selected-count').textContent = $$('#crew-officer-choices input:checked').length + ' selected'; }
  function orderMode() {
    const continuous = $('#crew-order-mode').value === 'continuous';
    $('#crew-order-mode-help').textContent = continuous ? 'The team repeats rounds until you stop the mission. Closing this page does not stop a run while the local service stays on.' : 'The team works in rounds until the final officer reports the objective complete. After ' + (state?.settings?.max_rounds || 12) + ' rounds it pauses for your review.';
    updateUsage();
  }
  function updateUsage() {
    const local = state?.settings?.provider === 'ollama';
    const continuous = $('#crew-order-mode').value === 'continuous';
    $('#crew-usage-title').textContent = local ? 'LOCAL MODEL USAGE' : 'CODEX SUBSCRIPTION USAGE';
    $('#crew-usage-description').textContent = local
      ? 'Uses the configured Ollama model and this computer while the mission runs. No paid API is called by this runner.'
      : 'Uses your Codex subscription allowance for each officer turn' + (continuous ? ' and keeps using it on each round until stopped.' : ' until the mission completes or pauses for review.') + ' No paid API is called by this runner.';
    if (!state?.capabilities?.ready) $('#crew-usage-description').append(document.createTextNode(' Provider is not ready: ' + (state?.capabilities?.detail || 'service unavailable') + '.'));
  }
  function openOrder(source = null, edit = false) {
    templateEdit = edit ? source?.id || '' : '';
    $('#crew-order-dialog-title').textContent = edit ? 'Edit saved order' : source ? 'Run a saved order' : 'Give the crew an order';
    $('#crew-order-form').reset();
    $('#crew-order-title').value = source?.title || '';
    $('#crew-order-text').value = source?.order || '';
    $('#crew-order-mode').value = source?.mode || 'once';
    $('#crew-order-interval').value = source?.interval_seconds || state?.settings?.mission_interval_seconds || 60;
    $('#crew-order-budget').value = source?.budget_usd ?? '';
    $('#crew-order-wallet').value = source?.wallet_address || '';
    $('#crew-order-save').checked = !source;
    $('#crew-order-save-only').textContent = edit ? 'SAVE CHANGES' : 'SAVE ORDER ONLY';
    const defaults = (state?.roster || []).filter(o => ['command', 'engineering', 'review'].includes(o.role)).map(o => o.id);
    populateOfficers(source?.officers || (defaults.length ? defaults : (state?.roster || []).slice(0, 3).map(o => o.id)));
    errorAt('#crew-order-error', null);
    orderMode();
    updateLocks();
    showDialog(orderDialog);
  }
  function orderPayload() {
    if (!$('#crew-order-form').reportValidity()) return null;
    const officers = $$('#crew-officer-choices input:checked').map(i => i.value);
    const title = $('#crew-order-title').value.trim();
    const order = $('#crew-order-text').value.trim();
    if (!title || !order) { errorAt('#crew-order-error', 'Enter a mission name and an order.'); return null; }
    if (officers.length < 2) { errorAt('#crew-order-error', 'Choose at least two officers so they can work together.'); $('#crew-officer-choices input')?.focus(); return null; }
    const p = { title, order, officers, mode: $('#crew-order-mode').value, interval_seconds: Number($('#crew-order-interval').value) || 60 };
    if ($('#crew-order-budget').value !== '') p.budget_usd = Number($('#crew-order-budget').value);
    if ($('#crew-order-wallet').value.trim()) p.wallet_address = $('#crew-order-wallet').value.trim();
    return p;
  }
  async function saveOrder() {
    errorAt('#crew-order-error', null);
    const payload = orderPayload(); if (!payload) return;
    if (templateEdit) payload.id = templateEdit;
    try {
      await mutate('/templates', payload);
      orderDialog.close(); open('templates'); notice(templateEdit ? 'Saved order updated.' : 'Order saved. No mission was started.');
    } catch (e) { errorAt('#crew-order-error', e); }
  }
  async function startOrder(e) {
    e.preventDefault();
    errorAt('#crew-order-error', null);
    const payload = orderPayload(); if (!payload) return;
    if (!$('#crew-usage-ack').checked) { errorAt('#crew-order-error', 'Acknowledge the usage above before starting.'); $('#crew-usage-ack').focus(); return; }
    if (!canRun()) { errorAt('#crew-order-error', isDemo() ? 'Switch to LIVE to start a mission.' : 'The selected provider is not ready. Check Deck settings.'); return; }
    const previous = new Set((state?.runs || []).map(r => r.id));
    payload.save_template = $('#crew-order-save').checked;
    payload.acknowledge_usage = true;
    try {
      const next = await mutate('/runs', payload);
      activeRun = (next.runs || []).find(r => !previous.has(r.id))?.id || activeRun;
      orderDialog.close(); open('missions'); notice('Mission started. Its progress will appear in the conversation.');
    } catch (err) { errorAt('#crew-order-error', err); }
  }
  async function controlRun(id, action) {
    await mutate('/runs/control', { id, action });
    notice(action === 'pause' ? 'Pause requested.' : action === 'resume' ? 'Mission resumed.' : 'Stop requested.');
  }
  async function steer(e) {
    e.preventDefault();
    const input = $('#crew-steer');
    const message = input.value.trim();
    if (!message || !activeRun || busy) return;
    try { await mutate('/runs/message', { id: activeRun, message }); input.value = ''; input.focus(); notice('Instruction added to the mission.'); }
    catch (err) { notice(err.message); }
  }
  async function resolveHandoff(e) {
    e.preventDefault();
    const run = state?.runs?.find(r => r.id === activeRun);
    if (!run?.pending_approval || !alive.has(run.status) || busy) return;
    const note = $('#crew-handoff-note').value.trim();
    if (!note) { errorAt('#crew-handoff-error', 'Record the outcome or reason before continuing.'); return; }
    const decision = e.submitter?.dataset.decision;
    if (!['done', 'skip'].includes(decision)) return;
    try { await mutate('/runs/resolve', { id: run.id, decision, note }); handoffDialog.close(); notice('Outcome recorded. Review the mission, then press Resume when ready.'); }
    catch (err) { errorAt('#crew-handoff-error', err); }
  }

  const settingDefaults = { banter_enabled: true, banter_interval_seconds: 18, banter_variety: .8, bubble_seconds: 7, reduced_motion: false, personality_evolution: false, evolution_every_rounds: 1, mission_interval_seconds: 60, turn_timeout_seconds: 300, provider: 'codex', local_model: '', max_rounds: 12 };
  function openSettings() {
    const settings = { ...settingDefaults, ...(state?.settings || {}) };
    for (const [name, value] of Object.entries(settings)) {
      const input = $('#crew-settings-form').elements.namedItem(name);
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = !!value; else input.value = value;
    }
    errorAt('#crew-settings-error', null);
    renderPersonalityRoster();
    settingHints(); updateLocks(); showDialog(settingsDialog);
  }
  function settingHints() {
    const form = $('#crew-settings-form');
    $('#crew-variety-value').textContent = Math.round(Number(form.elements.namedItem('banter_variety').value) * 100) + '%';
    const local = form.elements.namedItem('provider').value === 'ollama';
    $('#crew-model-field').hidden = !local;
    form.elements.namedItem('local_model').required = local;
    $('#crew-settings-provider-note').textContent = local ? 'Use a model already installed in Ollama. Changing settings does not download a model or start a mission.' : 'Officer turns use your existing Codex subscription allowance. Changing settings does not start a mission.';
  }
  function renderPersonalityRoster() {
    const list = $('#crew-personality-roster'); list.replaceChildren();
    for (const o of state?.roster || []) {
      const b = button('', () => openPersonality(o.id), 'crew-personality-button');
      b.append(el('strong', '', o.name), el('span', '', 'REV ' + (o.revision ?? 0)), el('span', '', 'VIEW HISTORY'));
      list.append(b);
    }
    if (!list.childNodes.length) list.append(el('p', 'crew-help', 'Personality records appear when the service connects.'));
  }
  async function saveSettings(e) {
    e.preventDefault(); errorAt('#crew-settings-error', null);
    const form = $('#crew-settings-form');
    if (!form.reportValidity()) return;
    const settings = {};
    for (const name of Object.keys(settingDefaults)) {
      const input = form.elements.namedItem(name);
      settings[name] = input.type === 'checkbox' ? input.checked : ['number', 'range'].includes(input.type) ? Number(input.value) : input.value.trim();
    }
    try { await mutate('/settings', { settings }); settingsDialog.close(); notice('Deck settings saved.'); }
    catch (err) { errorAt('#crew-settings-error', err); }
  }
  async function openPersonality(id) {
    personalityId = id;
    const requestId = ++personalityRequest;
    $('#crew-personality-dialog-title').textContent = officerName(id) + ': personality';
    $('#crew-personality-source').textContent = state?.roster?.find(o => o.id === id)?.source || '';
    $('#crew-personality-content').replaceChildren(el('p', 'crew-help', 'Loading personality record...'));
    $('#crew-personality-history').replaceChildren();
    errorAt('#crew-personality-error', null);
    showDialog(personalityDialog);
    try {
      const data = await request('/personality?' + new URLSearchParams({ id }).toString());
      if (requestId !== personalityRequest || personalityId !== id) return;
      const content = $('#crew-personality-content'); content.replaceChildren();
      const base = el('details', 'crew-personality-record'); base.append(el('summary', '', 'Founding identity'), el('pre', '', typeof data.base === 'string' ? data.base : JSON.stringify(data.base || {}, null, 2)));
      const evolved = el('section', 'crew-personality-record'); evolved.append(el('h3', 'crew-small-heading', 'LEARNED PERSONALITY'), el('pre', '', typeof data.evolved === 'string' ? data.evolved || 'No learned revisions yet.' : JSON.stringify(data.evolved || {}, null, 2)));
      content.append(evolved, base);
      const history = $('#crew-personality-history');
      for (const version of data.versions || []) {
        const row = el('div', 'crew-version');
        const info = el('div'); info.append(el('strong', '', dateTime(version.at)), el('p', 'crew-help', version.reason || 'Saved personality revision'));
        const restore = mutationButton(button('RESTORE', () => confirm('Restore this personality?', officerName(id) + ' will use the selected saved revision. Their founding identity remains intact.', 'RESTORE VERSION', async () => { await mutate('/personality/restore', { id, version: version.id }); await openPersonality(id); renderPersonalityRoster(); notice('Personality revision restored.'); })));
        row.append(info, restore); history.append(row);
      }
      if (!history.childNodes.length) empty(history, 'No revisions yet', 'Saved reflections will appear here when personality evolution is enabled and a mission reaches its reflection interval.');
    } catch (e) {
      if (e.scopeUnverified) { clearPrivateState(); errorAt('#crew-service-error', e); }
      else if (requestId === personalityRequest) errorAt('#crew-personality-error', e);
    }
  }
  function confirm(title, description, label, action) {
    $('#crew-confirm-dialog-title').textContent = title;
    $('#crew-confirm-text').textContent = description;
    errorAt('#crew-confirm-error', null);
    const yes = $('#crew-confirm-yes');
    yes.textContent = label; yes.disabled = !writable();
    yes.dataset.crewMutation = 'write';
    yes.onclick = async () => {
      if (busy) return;
      try { await action(); confirmDialog.close(); }
      catch (e) { errorAt('#crew-confirm-error', e); }
    };
    showDialog(confirmDialog); $('#crew-confirm-cancel').focus();
  }

  $('#crew-missions-toggle').addEventListener('click', () => panel.hidden ? open() : closePanel());
  $('#crew-settings-open').addEventListener('click', openSettings);
  $('#crew-panel-close').addEventListener('click', closePanel);
  $('#crew-new-order').addEventListener('click', () => openOrder());
  $('#crew-tab-missions').addEventListener('click', () => setTab('missions'));
  $('#crew-tab-templates').addEventListener('click', () => setTab('templates'));
  $('.crew-tablist').addEventListener('keydown', e => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      e.preventDefault(); setTab(e.key === 'Home' ? 'missions' : e.key === 'End' ? 'templates' : activeTab === 'missions' ? 'templates' : 'missions', true);
    }
  });
  $('#crew-select-all').addEventListener('click', () => { $$('#crew-officer-choices input').forEach(i => { i.checked = true; }); selectionCount(); });
  $('#crew-select-none').addEventListener('click', () => { $$('#crew-officer-choices input').forEach(i => { i.checked = false; }); selectionCount(); });
  $('#crew-order-mode').addEventListener('change', orderMode);
  $('#crew-order-save-only').addEventListener('click', saveOrder);
  $('#crew-order-form').addEventListener('submit', startOrder);
  $('#crew-steer-form').addEventListener('submit', steer);
  $('#crew-handoff-open').addEventListener('click', () => showDialog(handoffDialog));
  $('#crew-handoff-form').addEventListener('submit', resolveHandoff);
  $('#crew-steer').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!$('#crew-steer').disabled) $('#crew-steer-form').requestSubmit(); } });
  $('#crew-follow').addEventListener('change', () => { if ($('#crew-follow').checked) $('#crew-events').scrollTop = $('#crew-events').scrollHeight; });
  $('#crew-settings-form').addEventListener('input', settingHints);
  $('#crew-settings-form').addEventListener('submit', saveSettings);
  $('#crew-settings-cancel').addEventListener('click', () => settingsDialog.close());
  $('#crew-confirm-cancel').addEventListener('click', () => confirmDialog.close());
  // Keep ship shortcuts out of inputs, dialogs, and keyboard-operated controls.
  document.addEventListener('keydown', e => {
    if (e.target?.closest('.crew-dialog, .crew-panel')) {
      e.stopPropagation();
      if (e.key === 'Escape' && !$$('dialog[open]').length && !panel.hidden) closePanel();
    }
  });
  // The canvas owns LIVE/DEMO; watch its public state instead of replacing it.
  setInterval(() => { const mode = window.__crewDeck?.S?.mode || 'live'; if (mode !== lastMode) { lastMode = mode; updateLocks(); } }, 250);
  window.CrewControls = { state: null, open, refresh, isDemo };
  document.addEventListener('click', async e => {
    const link = e.target.closest('a.crew-artifact');
    if (!link) return;
    e.preventDefault();
    try { await window.CrewAccess.download(link.getAttribute('href')); }
    catch (error) { errorAt('#crew-service-error', error.message); }
  });
  window.addEventListener('crew-access-lost', clearPrivateState);
  render();
  refresh();
})().catch(error => { const box = document.getElementById('banner'); if (box) { box.className = 'banner show'; box.textContent = error.message; } });
