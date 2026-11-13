// ===========================================================
// PANE STATUS: a status badge on every chat window
//
// Each chat window shows one of:
//   WORKING    a reply is in progress
//   NEEDS YOU  the reply stopped on clickable choices, ended with a question,
//              or the model is signed out and waiting for you to log in
//   DONE       a reply landed while you were looking at another window, and
//              you have not looked at it yet
//   ERROR      the reply failed or stalled
//   (nothing)  idle
//
// A summary in the top header counts the windows that want attention, the
// browser tab title gets a "(n)" prefix, and when the dashboard is in the
// background a sound plus a desktop notification fire (the bell in the
// summary switches those off).
//
// app.js calls the hooks through optional chaining (globalThis.PaneStatus?.x),
// so if this file fails to load, chat keeps working exactly as before.
//
// Pane keys: 'main' for the main window (with or without a project folder),
// 'ws<N>' for project window N.
// ===========================================================
(function () {
  'use strict';

  const LABELS = { working: 'WORKING', needs: 'NEEDS YOU', done: 'DONE', error: 'ERROR' };
  const ATTENTION = ['needs', 'error', 'done'];   // priority order for the summary jump
  const ALERTS_KEY = 'paneStatus.alerts';
  const BASE_TITLE = document.title;

  const recs = new Map();   // key -> { state, reason, since, askPending, stopped }
  let lastTouched = 'main';

  function alertsOn() {
    try { return localStorage.getItem(ALERTS_KEY) !== 'off'; } catch { return true; }
  }

  function rec(key) {
    let r = recs.get(key);
    if (!r) {
      r = { state: 'idle', reason: '', since: Date.now(), askPending: false, stopped: false };
      recs.set(key, r);
    }
    return r;
  }

  // ── Key resolution ────────────────────────────────────────
  function keyForWs(wsId) {
    if (wsId == null) return 'main';
    const ws = (typeof _workspaces !== 'undefined') ? _workspaces.get(wsId) : null;
    return ws?.isMain ? 'main' : `ws${wsId}`;
  }

  // The bridge tags pushed events with the full history key `path::pane_id`.
  function keyForBridgePane(paneKey) {
    try {
      const t = _resolvePaneTarget(paneKey);
      if (t) return (t.isMain || t.wsId == null) ? 'main' : `ws${t.wsId}`;
    } catch { /* resolver unavailable */ }
    return null;
  }

  function keyFromPaneEl(el) {
    const pane = el?.closest?.('.chat-pane');
    if (!pane) return null;
    if (pane.id === 'pane-main') return 'main';
    const m = pane.id.match(/^pane-(ws\d+)$/);
    return m ? m[1] : null;
  }

  function els(key) {
    if (key === 'main') {
      return { pane: document.getElementById('pane-main'), header: document.getElementById('pane-main-header') };
    }
    return { pane: document.getElementById(`pane-${key}`), header: document.getElementById(`pane-${key}-header`) };
  }

  function paneName(key) {
    const h = els(key).header;
    return (h?.querySelector('.pane-name')?.textContent || (key === 'main' ? 'MAIN CHANNEL' : key)).trim();
  }

  function visiblePaneCount() {
    return document.querySelectorAll('#chats-wrapper .chat-pane:not([hidden])').length;
  }

  function backgrounded() {
    return document.visibilityState !== 'visible' || !document.hasFocus();
  }

  // The user is looking at this window right now.
  function inView(key) {
    if (backgrounded()) return false;
    return lastTouched === key || visiblePaneCount() <= 1;
  }

  // ── Rendering ─────────────────────────────────────────────
  function timeOf(ts) {
    return new Date(ts).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }

  function renderBadge(key) {
    const { pane, header } = els(key);
    if (!header) return;
    const r = recs.get(key);
    const state = r?.state || 'idle';
    let badge = header.querySelector('.pane-status');
    if (!badge) {
      badge = document.createElement('button');
      badge.type = 'button';
      badge.className = 'pane-status';
      badge.addEventListener('click', (e) => {
        e.stopPropagation();
        const k = keyFromPaneEl(badge) || key;
        const cur = recs.get(k);
        if (cur && cur.state !== 'working') setState(k, 'idle', '');
      });
      header.insertBefore(badge, header.firstChild);
    }
    badge.dataset.state = state;
    badge.hidden = state === 'idle';
    badge.textContent = LABELS[state] || '';
    badge.title = state === 'idle' ? ''
      : `${LABELS[state]}: ${r.reason} (since ${timeOf(r.since)})` +
        (state === 'working' ? '' : '. Click to dismiss.');
    badge.setAttribute('aria-label', badge.title || 'Idle');
    if (pane) pane.dataset.paneStatus = state;
  }

  function renderSummary() {
    const counts = { working: 0, needs: 0, done: 0, error: 0 };
    for (const [key, r] of recs) {
      if (!els(key).pane) continue;
      if (r.state in counts) counts[r.state]++;
    }
    const attention = counts.needs + counts.done + counts.error;
    document.title = attention ? `(${attention}) ${BASE_TITLE}` : BASE_TITLE;

    const box = document.getElementById('pane-status-summary');
    if (!box) return;
    const parts = [];
    if (counts.needs)   parts.push(`<span class="pss-part" data-state="needs">${counts.needs} NEED${counts.needs === 1 ? 'S' : ''} YOU</span>`);
    if (counts.error)   parts.push(`<span class="pss-part" data-state="error">${counts.error} ERROR</span>`);
    if (counts.done)    parts.push(`<span class="pss-part" data-state="done">${counts.done} DONE</span>`);
    if (counts.working) parts.push(`<span class="pss-part" data-state="working">${counts.working} WORKING</span>`);
    const label = box.querySelector('.pss-label');
    if (label) {
      label.innerHTML = parts.length
        ? parts.join('<span class="pss-sep">·</span>')
        : '<span class="pss-part" data-state="idle">CHATS QUIET</span>';
      label.title = attention ? 'Jump to the window that needs you' : 'No chat window needs you';
    }
    box.dataset.attention = attention ? '1' : '0';
    const bell = box.querySelector('.pss-bell');
    if (bell) {
      const on = alertsOn();
      bell.textContent = on ? '🔔' : '🔕';
      bell.title = on
        ? 'Background alerts on: a sound and a desktop notification when a chat finishes or needs you while the dashboard is in the background. Click to turn off.'
        : 'Background alerts off. Click to turn on.';
      bell.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
  }

  function render(key) { renderBadge(key); renderSummary(); }

  function refreshAll() {
    renderBadge('main');
    for (const key of recs.keys()) renderBadge(key);
    renderSummary();
  }

  // ── Alerts ────────────────────────────────────────────────
  function notifyDesktop(title, body, tag) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      const n = new Notification(title, { body, tag, renotify: true });
      n.onclick = () => { try { window.focus(); } catch {} n.close(); };
    } catch { /* some browsers only allow notifications from a service worker */ }
  }

  function alert(key, state, reason, { sound = true } = {}) {
    if (!alertsOn() || !backgrounded()) return;
    if (sound && typeof playDataSound === 'function') playDataSound('doorbell');
    notifyDesktop(`${paneName(key)}: ${LABELS[state]}`, reason, `pane-status-${key}`);
  }

  // ── State transitions ─────────────────────────────────────
  function setState(key, state, reason) {
    // A reply can settle after its window was closed; never bring it back.
    if (!els(key).pane) { recs.delete(key); renderSummary(); return; }
    const r = rec(key);
    r.state = state;
    r.reason = reason || '';
    r.since = Date.now();
    render(key);
  }

  function start(key) {
    const r = rec(key);
    r.askPending = false;
    r.stopped = false;
    setState(key, 'working', 'Working on your message');
  }

  // The user pressed stop: no badge for a reply they cancelled.
  function stopped(key) {
    const r = rec(key);
    r.stopped = true;
    r.askPending = false;
    setState(key, 'idle', '');
  }

  // A reply ended. `text` is the reply, `error` is set when it failed, and
  // `needs` is set when the window is waiting on the user (for example a
  // signed-out model showing its login card).
  function finish(key, { text = '', error = '', needs = '' } = {}) {
    const r = rec(key);
    if (r.stopped) { r.stopped = false; setState(key, 'idle', ''); return; }
    let state, reason;
    if (needs) {
      state = 'needs';
      reason = String(needs);
    } else if (error) {
      state = 'error';
      reason = String(error).replace(/\s+/g, ' ').trim().slice(0, 140) || 'The reply failed';
    } else if (r.askPending) {
      state = 'needs';
      reason = 'Stopped on clickable choices for you to pick';
    } else if (/\?\s*$/.test(String(text).trim())) {
      state = 'needs';
      reason = 'The reply ends with a question for you';
    } else {
      state = 'done';
      reason = 'Reply finished';
    }
    // Looking right at it: a plain finish needs no badge.
    if (state === 'done' && inView(key)) { setState(key, 'idle', ''); return; }
    setState(key, state, reason);
    alert(key, state, reason, { sound: true });
  }

  // Clickable choices were posted. They can arrive before or after the reply ends.
  function ask(key, question) {
    if (!key) return;
    const r = rec(key);
    r.askPending = true;
    if (r.state === 'working') return;   // finish() promotes it to NEEDS YOU
    const reason = question ? `Asked: ${String(question).slice(0, 120)}` : 'Stopped on clickable choices for you to pick';
    setState(key, 'needs', reason);
    alert(key, 'needs', reason, { sound: false });   // the choices card already rang
  }

  // A message was pushed into a window from outside its own reply (for
  // example a message relayed from another window). Only the badge changes.
  function pushed(key, reason) {
    if (!key) return;
    const r = rec(key);
    if (r.state === 'working' || r.state === 'needs') return;
    if (inView(key)) return;
    setState(key, 'done', reason || 'A message arrived');
  }

  // The user looked at (touched) this window.
  function seen(key) {
    if (!key) return;
    lastTouched = key;
    const r = recs.get(key);
    if (r && (r.state === 'done' || r.state === 'error')) setState(key, 'idle', '');
  }

  function remove(key) {
    recs.delete(key);
    if (lastTouched === key) lastTouched = 'main';
    renderSummary();
  }

  // Jump to the highest-priority window that wants attention.
  function jump() {
    for (const want of ATTENTION) {
      for (const [key, r] of recs) {
        if (r.state !== want) continue;
        const { pane } = els(key);
        if (!pane || pane.hidden) continue;
        pane.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        pane.classList.remove('pane-status-flash');
        void pane.offsetWidth;   // restart the flash animation
        pane.classList.add('pane-status-flash');
        const input = key === 'main' ? document.getElementById('chat-input')
                                     : document.getElementById(`pane-input-${key}`);
        try { input?.focus({ preventScroll: true }); } catch {}
        seen(key);
        return;
      }
    }
  }

  function toggleAlerts() {
    const next = alertsOn() ? 'off' : 'on';
    try { localStorage.setItem(ALERTS_KEY, next); } catch {}
    // Ask for notification permission on the click that turns alerts on.
    if (next === 'on' && 'Notification' in window && Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch {}
    }
    renderSummary();
  }

  // ── Wiring ────────────────────────────────────────────────
  function onTouch(e) {
    const key = keyFromPaneEl(e.target);
    if (key) seen(key);
  }
  document.addEventListener('pointerdown', onTouch, true);
  document.addEventListener('focusin', onTouch, true);

  // Coming back to the dashboard counts as looking at the last-touched window.
  const backInView = () => { if (!backgrounded()) seen(lastTouched); };
  window.addEventListener('focus', backInView);
  document.addEventListener('visibilitychange', backInView);

  function initSummary() {
    const box = document.getElementById('pane-status-summary');
    if (box) {
      box.querySelector('.pss-label')?.addEventListener('click', jump);
      box.querySelector('.pss-bell')?.addEventListener('click', (e) => { e.stopPropagation(); toggleAlerts(); });
    }
    renderBadge('main');
    renderSummary();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initSummary);
  else initSummary();

  window.PaneStatus = {
    keyForWs, keyForBridgePane,
    start, stopped, finish, ask, pushed, seen, remove, jump, refreshAll,
    _recs: recs,   // exposed for tests and debugging
  };
})();
