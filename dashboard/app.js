/* ═══════════════════════════════════════════════════════════
   DATA DATA Dashboard — Main Application Logic
   ═══════════════════════════════════════════════════════════ */

// ── Config ────────────────────────────────────────────────
// API base resolves at runtime:
//   - When the dashboard is opened as a file:// URL → fall back to localhost
//   - When served by the bridge (via Cloudflare Tunnel, LAN, etc.) → use the
//     origin we were served from so phones / remote browsers reach the same host
const API_BASE = (location.protocol === 'file:' || !location.origin || location.origin === 'null')
  ? 'http://localhost:7777'
  : location.origin;

// ── Mobile nav + vitals toggles ──────────────────────────────────────────
function toggleMobileNav() {
  document.body.classList.toggle('mobile-nav-open');
  // Only one panel open at a time
  document.body.classList.remove('mobile-vitals-open');
}
function toggleMobileVitals() {
  document.body.classList.toggle('mobile-vitals-open');
  document.body.classList.remove('mobile-nav-open');
}
// Close any open mobile panel when:
//   - a nav button is tapped (so the user lands on the page)
//   - the user taps outside the slide-in panel (backdrop dismiss)
document.addEventListener('click', (e) => {
  const navOpen = document.body.classList.contains('mobile-nav-open');
  const vitOpen = document.body.classList.contains('mobile-vitals-open');
  if (!navOpen && !vitOpen) return;
  if (e.target.closest('.data-btn')) {
    document.body.classList.remove('mobile-nav-open');
    document.body.classList.remove('mobile-vitals-open');
    return;
  }
  // Backdrop dismiss: tap outside the panel AND outside the toggle buttons
  if (e.target.closest('.data-sidebar') || e.target.closest('.data-right')) return;
  if (e.target.closest('.mobile-nav-toggle') ||
      e.target.closest('.mobile-vitals-toggle') ||
      e.target.closest('.mobile-theme-toggle')) return;
  document.body.classList.remove('mobile-nav-open');
  document.body.classList.remove('mobile-vitals-open');
});

// ── Tunnel URL widget (Cloudflare phone access) ──────────────────────────
let _tunnelUrl = '';
async function _pollTunnelUrl() {
  try {
    const res  = await fetch(`${API_BASE}/tunnel_url`);
    const data = await res.json();
    _tunnelUrl = (data.url || '').trim();
    const row = document.getElementById('tunnel-row');
    if (row) row.style.display = _tunnelUrl ? 'flex' : 'none';
  } catch (e) { /* bridge offline — silent */ }
}
setInterval(_pollTunnelUrl, 4000);
setTimeout(_pollTunnelUrl, 800);

function openTunnel() {
  if (!_tunnelUrl) { addLog('No tunnel URL yet — cloudflared still starting?'); return; }
  window.open(_tunnelUrl, '_blank');
}

function copyPhoneLink() {
  if (!_tunnelUrl) { addLog('No tunnel URL yet — cloudflared still starting?'); return; }
  const tok = localStorage.getItem('data-bridge-token') || '';
  const link = tok ? `${_tunnelUrl}/?key=${encodeURIComponent(tok)}` : _tunnelUrl;
  navigator.clipboard.writeText(link).then(
    () => addLog(`Phone link copied (${link.length} chars)`),
    () => addLog('Copy failed — clipboard blocked')
  );
}

// ── Auth token (X-Data-Token header) ─────────────────────────────────────
// If the bridge has DATA_BRIDGE_TOKEN set, every API request needs this
// header. Stored in localStorage so it survives reloads. First visit on a
// tunneled URL: bridge returns 401 → we prompt for the token and retry.
function _getAuthToken() {
  // URL ?key=<token> override (one-tap link sharing) → write into storage
  try {
    const params = new URLSearchParams(location.search);
    const k = params.get('key');
    if (k) {
      localStorage.setItem('data-bridge-token', k);
      // Clean the URL so the token isn't visible in browser history
      history.replaceState({}, '', location.pathname);
      return k;
    }
  } catch {}
  return localStorage.getItem('data-bridge-token') || '';
}

let _authToken = _getAuthToken();

// Wrap window.fetch so every API call carries the auth header automatically.
// On 401, prompt for token, save, retry once.
const _rawFetch = window.fetch.bind(window);
window.fetch = async function(input, init) {
  init = init || {};
  const headers = new Headers(init.headers || {});
  if (_authToken) headers.set('X-Data-Token', _authToken);
  init.headers = headers;
  let res = await _rawFetch(input, init);
  if (res.status === 401) {
    const tok = prompt('This bridge requires a token. Paste DATA_BRIDGE_TOKEN:');
    if (tok) {
      _authToken = tok.trim();
      localStorage.setItem('data-bridge-token', _authToken);
      headers.set('X-Data-Token', _authToken);
      res = await _rawFetch(input, init);
    }
  }
  return res;
};

// ── Per-session pane token ────────────────────────────────────────
// Regenerated on EVERY full page load. Chat panes fold this into the
// `pane_id` they send to the bridge, so the bridge's history bucket key
// (`path::<pane_id>`) is unique to this dashboard session. Result: every
// time the Captain opens the dashboard, panes bind to BRAND-NEW, empty
// history buckets and start with fresh context — no carry-over from what
// was being worked on in the previous session.
//
// Why this was needed: pane numbers (`ws1`, `ws2`, …) reset to 1 on each
// page load, so the Nth pane of a new session used to collide with the Nth
// pane of the previous session pointed at the same folder and inherit its
// transcript. The session tag breaks that collision.
//
// What is NOT lost: the permanent conversation archive + searchable Memory
// Banks (recall_index.db) and COMPUTER_MEMORY.md persist independently of
// this — only the rolling ~20-turn live context resets. Search history
// still reaches every past session.
const _SESSION_TAG = 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
function _paneId(base) { return `${_SESSION_TAG}-${base}`; }

// DATA sound palette — original synthesized UI tones, generated from pure
// math by tools/make_sounds.py (no sampled or licensed audio anywhere).
const DATA_SOUNDS = {
  transmit:   { src: 'sounds/transmit.wav',   volume: 0.44, cooldown: 120 },
  confirm:    { src: 'sounds/confirm.wav',    volume: 0.24, cooldown: 80 },
  receive:    { src: 'sounds/receive.wav',    volume: 0.34, cooldown: 220 },
  engage:     { src: 'sounds/engage.wav',     volume: 0.30, cooldown: 180 },
  error:      { src: 'sounds/error.wav',      volume: 0.44, cooldown: 1000 },
  processing: { src: 'sounds/processing.wav', volume: 0.26, cooldown: 280 },
  doorbell:   { src: 'sounds/doorbell.wav',   volume: 0.40, cooldown: 1200 },
};

const _dataSoundCache = new Map();
const _dataSoundLastPlayed = {};

function initDataSounds() {
  return; // SFX disabled — do not preload UI sound files
  for (const [name, cfg] of Object.entries(DATA_SOUNDS)) {
    if (_dataSoundCache.has(name)) continue;
    const audio = new Audio(cfg.src);
    audio.preload = 'auto';
    audio.volume = cfg.volume;
    _dataSoundCache.set(name, audio);
  }
}

function playDataSound(name) {
  return; // SFX disabled — UI sounds removed per Captain's order (better set TBD)
  const cfg = DATA_SOUNDS[name];
  if (!cfg) return;

  const now = performance.now();
  const last = _dataSoundLastPlayed[name] || 0;
  if (now - last < (cfg.cooldown || 120)) return;
  _dataSoundLastPlayed[name] = now;

  initDataSounds();
  const cached = _dataSoundCache.get(name);
  if (!cached) return;

  const audio = cached.cloneNode();
  audio.volume = cfg.volume;
  audio.play().catch(() => {});
}

document.addEventListener('pointerdown', initDataSounds, { once: true, capture: true });
document.addEventListener('keydown', initDataSounds, { once: true, capture: true });

// Delegated click handler — any element rendered with [data-open-path] (file
// paths inside backticks in chat bubbles) opens that path in File Explorer
// via the bridge's /open endpoint, the same way mini-matrix nodes do.
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-open-path]');
  if (!el) return;
  e.preventDefault();
  const p = el.getAttribute('data-open-path');
  if (p) openPath(p);
});

// Esc cascade — handles ONE job per press, in priority order:
//   1. Cancel any active dictation (wake-dictation or manual mic recording).
//   2. Else if last-active window had a queued follow-up, unsend it.
//   3. Else if last-active window has an in-flight request, abort ONLY that
//      window's request — never bleed into other panes.
// Conversation mode has its own Esc handler so we don't touch it here.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (typeof BRAIN !== 'undefined' && BRAIN.active) return;

  // 1. Cancel listening (always global — only one mic stream at a time).
  let handled = false;
  if (typeof WAKE_DICTATION !== 'undefined' && WAKE_DICTATION.active && WAKE_DICTATION.recorder) {
    WAKE_DICTATION.aborted = true;
    try { WAKE_DICTATION.recorder.stop(); } catch {}
    handled = true;
  }
  if (_dictActive && _dictRecorder) {
    _dictAborted = true;
    try { _dictRecorder.stop(); } catch {}
    handled = true;
  }

  // 2 + 3. Route to whichever window the Captain last interacted with.
  if (!handled) {
    if (_lastActiveWsId === null) {
      // ── MAIN PANE ──
      // 2a. Pop the most recently queued main-pane follow-up.
      if (_messageQueue.length) {
        const popped = _messageQueue.pop();
        try { popped.bubble?.remove(); } catch {}
        _updateQueueBadge();
        addLog(`Unsent queued message (${_messageQueue.length} still queued)`);
        handled = true;
      } else if (isThinking) {
        // 3a. Abort the in-flight main-pane prompt and remove its bubble.
        // Calls stopData() which aborts the fetch + tells the bridge to stop
        // its CLI subprocess. Workspace pane requests are NOT touched here.
        const inflight = _inFlightUserBubble;
        _inFlightUserBubble = null;
        stopData();
        try { inflight?.remove(); } catch {}
        addLog('Unsent in-flight prompt (main pane)');
        handled = true;
      }
    } else {
      // ── WORKSPACE PANE ──
      const ws = _workspaces.get(_lastActiveWsId);
      if (ws && ws.isThinking && ws.abortController) {
        try { ws.abortController.abort(); } catch {}
        // Tell the bridge to kill the CLI subprocess for THIS pane's path —
        // saves API credits / compute by stopping the model immediately
        // instead of letting it run to completion with output nobody reads.
        // No-op for other panes' subprocesses thanks to per-project tracking.
        fetch(`${API_BASE}/stop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_path: ws.path || '',
            pane_id:      _paneId(`ws${_lastActiveWsId}`),
          }),
        }).catch(() => {});
        ws.isThinking = false;
        ws.abortController = null;
        // Strip the "thinking" indicator from this pane's chat window.
        const winEl = document.getElementById(`chat-win-ws${_lastActiveWsId}`);
        if (winEl) removeThinkingFromPane(winEl);
        addLog(`Aborted in-flight prompt in pane "${ws.name || _lastActiveWsId}"`);
        handled = true;
      }
    }
  }

  if (handled) e.preventDefault();
});

// ── Stardate ──────────────────────────────────────────────
// Format: MM.DD.YYYY.HH:MM:SS — real local date + time, ticking every second.
function updateStardate() {
  const now = new Date();
  const p = n => String(n).padStart(2, '0');
  const stamp =
    `${p(now.getMonth() + 1)}.${p(now.getDate())}.${now.getFullYear()}.` +
    `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
  const el = document.getElementById('stardate');
  if (el) el.textContent = stamp;
}
updateStardate();
setInterval(updateStardate, 1000);

// Set init timestamp
document.getElementById('init-timestamp').textContent =
  new Date().toLocaleTimeString('en-US', { hour12: false });

// ── Panel navigation ──────────────────────────────────────
function showPanel(name) {
  playDataSound('confirm');
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.data-btn').forEach(b => b.classList.remove('active'));

  document.getElementById(`panel-${name}`).classList.add('active');

  const btnMap = { chat: 0, matrix: 1, orders: 2, briefing: 3, connectors: 4 };
  const idx = btnMap[name];
  if (idx !== undefined) document.querySelectorAll('.data-btn')[idx].classList.add('active');

  // Lazy-init panels
  if (name === 'matrix' && !matrixInitialized) initMatrix();
  if (name === 'orders') refreshStandingOrders();
  if (name === 'briefing') loadBriefing();
  if (name === 'connectors') loadConnectors();
}

// ── Open a path in Windows Explorer ──────────────────────
function openPath(path) {
  if (!path) return;
  fetch(`${API_BASE}/open?path=${encodeURIComponent(path)}`)
    .then(r => r.json())
    .then(d => { if (d.error) addLog(`Open failed: ${d.error}`); })
    .catch(() => addLog('Bridge offline — cannot open path'));
}

// ── Activity log ──────────────────────────────────────────
function addLog(text) {
  const log = document.getElementById('activity-log');
  const entry = document.createElement('div');
  entry.className = 'log-entry new';
  entry.textContent = text;
  log.insertBefore(entry, log.firstChild);
  // Previously auto-played the error sound on text-pattern matches, but that
  // misfired constantly on mobile where background polls (tunnel offline,
  // wake-word events, voice dictation aborts) push routine log lines through
  // every couple seconds. Sounds are now triggered only by explicit callers.
  setTimeout(() => entry.classList.remove('new'), 1000);
  // Keep max 20 entries
  while (log.children.length > 20) log.removeChild(log.lastChild);
}

// ── Chat ──────────────────────────────────────────────────
let isThinking = false;
let _abortController = null;

function stopData() {
  if (_abortController) _abortController.abort();
  // Target only the main pane's bridge subprocess. The bridge now tracks
  // procs per-project so this won't kill workspace pane requests.
  const mainWs = [..._workspaces.values()].find(w => w.isMain);
  fetch(`${API_BASE}/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_path: mainWs?.path || '',
      pane_id:      _paneId('main'),
    }),
  }).catch(() => {});
  // ABORT also discards any queued follow-ups — if the Captain wants to stop,
  // they don't want their queued messages auto-firing right after.
  if (_messageQueue.length) {
    addLog(`Dropped ${_messageQueue.length} queued message(s)`);
    _messageQueue = [];
    _updateQueueBadge();
  }
}

// Messages typed while Data is still processing the previous one. Drained
// FIFO from the trailing finally-block of sendMessage(). Each item is
// { text, bubble } so Esc can pop the latest and remove its bubble.
let _messageQueue = [];
// DOM node for the user bubble whose response is currently streaming. Set
// when a dispatch starts; cleared when isThinking flips back to false. Esc
// uses this to "unsend" the in-flight message.
let _inFlightUserBubble = null;

function _updateQueueBadge() {
  const btn = document.getElementById('stop-btn');
  if (!btn) return;
  const n = _messageQueue.length;
  btn.textContent = n > 0 ? `ABORT (+${n})` : 'ABORT';
}

function handleInputKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

// Tracks the currently playing TTS audio + its button
let _ttsAudio    = null;
let _ttsBtn      = null;
// One-shot: when true, the next assistant reply auto-plays its TTS. Set by the
// wake-dictation path so spoken prompts get spoken replies hands-free.
let _autoSpeakNextReply = false;

// The agent you are chatting with on the main channel. Drives the persona
// (sent to /chat_stream as `crew`), the bubble name + avatar, and the TTS
// voice. Switchable from the panel-header name dropdown. Default: Data, the
// neutral main computer that summons the specialist agents as needed.
let MAIN_CHAT_CREW = localStorage.getItem('main-chat-crew') || 'data';
// Migrate the retired "computer" agent onto Data (Data is now the main computer).
if (MAIN_CHAT_CREW === 'computer') { MAIN_CHAT_CREW = 'data'; localStorage.setItem('main-chat-crew', 'data'); }
const CREW_LABELS = {
  data:     'Data',
  atlas:   'Atlas - Plan/Architect',
  forge:    'Forge - Build/Code',
  vector:    'Vector - Review',
  sentinel:     'Sentinel - Security',
  probe:   'Probe - Debug/Test',
  relay:   "Relay - DevOps",
  echo:     'Echo - Spirituality',
  pulse:  'Pulse - Health',
  sage:   'Sage - Advisor',
  scout:   'Scout - Content',
};
function crewLabel(id) { return CREW_LABELS[id] || 'Data'; }
function _crewAvatar(id) {
  return id === 'data' ? '◉' : (crewLabel(id)[0] || '◉').toUpperCase();
}

// Crew wake words carried over from Conversation Mode into the global comms
// listener. The whole finalized utterance must BE the wake word (optionally
// "hey"-prefixed) — anchored, so "the data looks fine" never false-triggers.
const CREW_WAKE = {
  data:     ['data'],
  vector:    ['vector'],
  probe:   ['probe'],
  atlas:   ['atlas'],
  sentinel:     ['sentinel'],
  echo:     ['echo', 'counselor echo'],
};
function _crewWakeMatch(heard) {
  const h = (heard || '').toLowerCase().trim()
    .replace(/[.,!?;:'"]+$/g, '')
    .replace(/^(?:hey|hi|hello|ok|okay|yo)\s+/, '')
    .trim();
  for (const id in CREW_WAKE) {
    if (CREW_WAKE[id].indexOf(h) !== -1) return id;
  }
  return null;
}

// Per-pane crew dropdown — every chat window owns its own agent. The MAIN
// pane uses MAIN_CHAT_CREW (which also persists to localStorage), and each
// project workspace stores its own `crew` field on the workspace record.
function _crewSelectOptionsHTML(selectedId) {
  return Object.keys(CREW_LABELS).map(id =>
    `<option value="${id}"${id === selectedId ? ' selected' : ''}>${crewLabel(id).toUpperCase()}</option>`
  ).join('');
}

function _crewSelectHTML(wsKey, selectedId) {
  return `<select class="pane-crew-select" id="pane-crew-${wsKey}"
              title="Agent for this chat window"
              onchange="setPaneCrew('${wsKey}', this.value)">${_crewSelectOptionsHTML(selectedId)}</select>`;
}

function _populatePaneCrewSelect(wsKey, selectedId) {
  const sel = document.getElementById(`pane-crew-${wsKey}`);
  if (!sel) return;
  sel.innerHTML = _crewSelectOptionsHTML(selectedId);
}

function setPaneCrew(wsKey, id) {
  if (!CREW_LABELS[id]) return;
  if (wsKey === 'main') {
    MAIN_CHAT_CREW = id;
    localStorage.setItem('main-chat-crew', id);
    _populatePaneCrewSelect('main', id);
    addLog('Main channel agent → ' + crewLabel(id));
    return;
  }
  // wsKey is "ws<N>" — match it against the workspaces map.
  const wsId = parseInt(wsKey.replace(/^ws/, ''), 10);
  const ws = _workspaces.get(wsId);
  if (!ws) return;
  ws.crew = id;
  _populatePaneCrewSelect(wsKey, id);
  addLog(`[${ws.name}] agent → ${crewLabel(id)}`);
}

// Legacy entry-point — wake-word handler still calls this to switch the main
// channel agent on "computer / data / atlas / …".
function setMainChatCrew(id) { setPaneCrew('main', id); }

window.addEventListener('DOMContentLoaded', () => {
  _populatePaneCrewSelect('main', MAIN_CHAT_CREW);
  bootCaptains();
  initChatInputResizer();
  // A fresh dashboard load means no project is attached to the main pane.
  // Clear any stale project cwd the bridge kept in memory from a prior
  // set_project_path / project load so the default first window opens with
  // NO folder. Symmetric with what closeProjectWorkspace() POSTs on close.
  fetch(`${API_BASE}/project`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: '' }),
  }).catch(() => {});
});

// ── Resizable prompt box ───────────────────────────────────────────
// The handle above the main chat textarea lets the Captain drag the prompt
// area taller (to see a long message while typing) or shorter. The chosen
// height persists across reloads in localStorage. Dragging UP grows the box.
const _CHAT_INPUT_HEIGHT_KEY = 'data.chatInputHeight';
const _CHAT_INPUT_MIN_PX = 38;
function _chatInputMaxPx() {
  // Never let the box swallow the whole window — cap at 60% of viewport.
  return Math.max(120, Math.round(window.innerHeight * 0.6));
}
function _applyChatInputHeight(px) {
  return _clampChatInputHeight(px);
}
function _clampChatInputHeight(px) {
  return Math.min(_chatInputMaxPx(), Math.max(_CHAT_INPUT_MIN_PX, Math.round(px)));
}
// Wire a drag handle to a textarea so the Captain can resize the prompt box.
// Generic on purpose: the same function serves the main pane AND every spawned
// project pane. Each pane persists its own height under its own storage key,
// so resizing one window no longer depends on hardcoded main-pane element ids.
function _wireInputResizer(handle, ta, storageKey) {
  if (!handle || !ta) return;
  const applyH = (px) => { ta.style.height = _clampChatInputHeight(px) + 'px'; };

  // Restore a previously saved height.
  const saved = parseInt(localStorage.getItem(storageKey) || '', 10);
  if (!isNaN(saved)) applyH(saved);

  let startY = 0, startH = 0, dragging = false;
  const onMove = (e) => {
    if (!dragging) return;
    const y = (e.touches ? e.touches[0].clientY : e.clientY);
    // Handle is ABOVE the textarea, so dragging up (smaller Y) grows it.
    applyH(startH + (startY - y));
    e.preventDefault();
  };
  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.userSelect = '';
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    localStorage.setItem(storageKey, String(parseInt(ta.style.height, 10) || _CHAT_INPUT_MIN_PX));
  };
  handle.addEventListener('pointerdown', (e) => {
    dragging = true;
    startY = e.clientY;
    startH = ta.getBoundingClientRect().height;
    handle.classList.add('dragging');
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    e.preventDefault();
  });
  // Double-click the handle to reset to the default two-row height.
  handle.addEventListener('dblclick', () => {
    ta.style.height = '';
    localStorage.removeItem(storageKey);
  });
}
function initChatInputResizer() {
  _wireInputResizer(
    document.getElementById('chat-input-resizer'),
    document.getElementById('chat-input'),
    _CHAT_INPUT_HEIGHT_KEY
  );
}

// ══════════════════════════════════════════════════════════════════
// CAPTAIN SWITCHER — selects which Captain (user profile) is active
// ══════════════════════════════════════════════════════════════════
// Each Captain has private per-user state on the backend
// (COMPUTER_MEMORY.md, conversation_history.json, conversation_archive.jsonl,
// recall_index.db). Switching is a POST /user/switch which repoints the
// bridge's per-user file constants and reloads rolling histories. After
// switching, the dashboard clears the visible chat-window so the prior
// Captain's transcript is not displayed under the new identity.
let _captains = [];      // [{id, name, rank, accent}, …]
let _activeCaptain = ''; // uid string

async function bootCaptains() {
  try {
    const res = await fetch(`${API_BASE}/user/list`);
    if (!res.ok) return;
    const data = await res.json();
    _captains = Array.isArray(data.users) ? data.users : [];
    _activeCaptain = data.active || (_captains[0] && _captains[0].id) || '';
    // Hide the switcher entirely if there is only one Captain — no point
    // showing a dropdown with a single item. Multi-user setups get the UI.
    const wrap = document.getElementById('captain-switch-wrap');
    if (!wrap) return;
    if (_captains.length < 2) { wrap.hidden = true; return; }
    wrap.hidden = false;
    renderCaptainSwitcher();
  } catch (e) {
    console.warn('bootCaptains failed:', e);
  }
}

function _captainById(uid) { return _captains.find(c => c.id === uid); }
function _accentColor(c) {
  // Map a registry accent token to a CSS color. Extend here when new
  // accents are added to users.json.
  const palette = {
    orange:   '#ff9933',
    lavender: '#b794f6',
    blue:     '#5599ff',
    teal:     '#33ccaa',
    yellow:   '#ffcc33',
    green:    '#66cc66',
    pink:     '#ff66aa',
  };
  return palette[(c && c.accent) || 'orange'] || palette.orange;
}

function renderCaptainSwitcher() {
  const wrap   = document.getElementById('captain-switch-wrap');
  const btn    = document.getElementById('captain-switch-btn');
  const av     = document.getElementById('captain-avatar');
  const nameEl = document.getElementById('captain-name');
  const menu   = document.getElementById('captain-menu');
  if (!wrap || !btn || !av || !nameEl || !menu) return;

  const active = _captainById(_activeCaptain) || _captains[0];
  if (!active) return;
  const accent = _accentColor(active);
  wrap.style.setProperty('--captain-accent', accent);
  av.textContent = (active.name || '?').slice(0, 1).toUpperCase();
  nameEl.textContent = (active.name || active.id).toUpperCase();
  btn.title = `Active Captain: ${active.rank || 'Captain'} ${active.name}. Click to switch.`;

  // Build dropdown — every Captain (active row highlighted, click is a no-op).
  menu.innerHTML = '';
  _captains.forEach(c => {
    const item = document.createElement('button');
    item.className = 'captain-menu-item' + (c.id === _activeCaptain ? ' active' : '');
    item.style.setProperty('--row-accent', _accentColor(c));
    item.innerHTML = `
      <span class="captain-menu-av">${(c.name || '?').slice(0, 1).toUpperCase()}</span>
      <span>${c.name || c.id}</span>
      <span class="captain-menu-rank">${(c.rank || 'CAPTAIN').toUpperCase()}</span>
    `;
    item.addEventListener('click', () => switchCaptain(c.id));
    menu.appendChild(item);
  });
}

function toggleCaptainMenu(ev) {
  if (ev) ev.stopPropagation();
  const menu = document.getElementById('captain-menu');
  if (!menu) return;
  const willShow = menu.hidden;
  menu.hidden = !menu.hidden;
  if (willShow) {
    // Dismiss on next outside click.
    setTimeout(() => {
      const onDoc = (e) => {
        const btn = document.getElementById('captain-switch-btn');
        if (!menu.contains(e.target) && (!btn || !btn.contains(e.target))) {
          menu.hidden = true;
          document.removeEventListener('click', onDoc);
        }
      };
      document.addEventListener('click', onDoc);
    }, 0);
  }
}

async function switchCaptain(uid) {
  if (!uid || uid === _activeCaptain) {
    const menu = document.getElementById('captain-menu');
    if (menu) menu.hidden = true;
    return;
  }
  const target = _captainById(uid);
  if (!target) return;
  try {
    const res = await fetch(`${API_BASE}/user/switch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: uid }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      addLog(`Captain switch failed: ${err.error || res.status}`);
      playDataSound('error');
      return;
    }
    const data = await res.json();
    _activeCaptain = data.active || uid;
    renderCaptainSwitcher();
    const menu = document.getElementById('captain-menu');
    if (menu) menu.hidden = true;

    // Wipe the visible main chat so the prior Captain's transcript is not
    // shown under the new identity. The backend has already swapped to the
    // incoming Captain's rolling history (used as model context, not for
    // DOM rendering). Announce the switch as a fresh COMPUTER greeting.
    const win = document.getElementById('chat-window');
    if (win) {
      win.innerHTML = '';
      const rank = (target.rank || 'Captain');
      appendMessage(
        'data',
        `Captain identity updated. Welcome aboard, **${rank} ${target.name}**. Your private memory and history are loaded. How may I assist you?`
      );
    }
    playDataSound('engage');
    addLog(`Active Captain → ${target.name}`);
  } catch (e) {
    console.warn('switchCaptain failed:', e);
    addLog(`Captain switch error: ${e.message || e}`);
    playDataSound('error');
  }
}

// ── Streaming per-sentence speech for the global comm channel ──────────────
// When a reply is armed for auto-speak (wake-word dictation), each sentence is
// sent to /tts the moment it completes and queued for playback — so the
// Computer starts speaking sentence one while the model is still generating
// the rest, instead of waiting for the whole reply to finish.
const _speakStream = { active: false, buf: '', queue: [], pumping: false, cancelled: false };

// Strip markdown so the Computer does not read out "asterisk", backticks, etc.
function _ttsClean(s) {
  return String(s || '')
    .replace(/```[\s\S]*?```/g, ' ')          // drop fenced code blocks
    .replace(/`([^`]*)`/g, '$1')              // inline code → bare text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')  // [label](url) → label
    .replace(/[*_#>~]+/g, '')                 // bold / italic / heading / quote marks
    .replace(/\s+/g, ' ')
    .trim();
}

function _speakStreamBegin() {
  _speakStream.active = true;
  _speakStream.cancelled = false;
  _speakStream.buf = '';
  _speakStream.queue = [];
  clearTtsQueue();   // stop any prior playback before this reply starts
}

function _speakStreamFeed(token) {
  if (!_speakStream.active) return;
  _speakStream.buf += token;
  // Dispatch every complete sentence — punctuation followed by whitespace.
  const RE = /[.!?]+(?:["'’”)\]]+)?\s+/g;
  let lastEnd = 0, m;
  while ((m = RE.exec(_speakStream.buf))) {
    const sentence = _speakStream.buf.slice(lastEnd, m.index + m[0].length).trim();
    if (sentence) _speakStream.queue.push(sentence);
    lastEnd = m.index + m[0].length;
  }
  if (lastEnd) _speakStream.buf = _speakStream.buf.slice(lastEnd);
  _speakStreamPump();
}

function _speakStreamEnd() {
  if (!_speakStream.active) return;
  _speakStream.active = false;
  const tail = _speakStream.buf.trim();
  _speakStream.buf = '';
  if (tail) _speakStream.queue.push(tail);
  _speakStreamPump();
}

async function _speakStreamPump() {
  if (_speakStream.pumping) return;
  _speakStream.pumping = true;
  while (_speakStream.queue.length && !_speakStream.cancelled) {
    const sentence = _ttsClean(_speakStream.queue.shift());
    if (!sentence) continue;
    try {
      const res = await fetch(`${API_BASE}/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sentence, voice: MAIN_CHAT_CREW }),
      });
      const data = await res.json();
      if (data && data.audio_b64 && !_speakStream.cancelled) enqueueTtsChunk(data.audio_b64, data.audio_mime);
    } catch (e) {
      // One sentence failing must not abort the rest of the reply.
    }
  }
  _speakStream.pumping = false;
}

function stopTts() {
  if (_ttsAudio) { _ttsAudio.pause(); _ttsAudio = null; }
  if (_ttsBtn)   { _ttsBtn.textContent = '▶'; _ttsBtn.classList.remove('speaking'); _ttsBtn = null; }
}

async function toggleTts(btn, text) {
  // If this button is already playing, stop it
  if (_ttsBtn === btn) { stopTts(); return; }
  // Stop whatever was playing before
  stopTts();

  btn.textContent = '...';
  btn.disabled = true;

  try {
    const res  = await fetch(`${API_BASE}/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: _ttsClean(text), voice: MAIN_CHAT_CREW }),
    });
    const data = await res.json();
    if (!data.audio_b64) throw new Error(data.error || 'no audio');

    const bytes = Uint8Array.from(atob(data.audio_b64), c => c.charCodeAt(0));
    const mime  = data.audio_mime || 'audio/wav';
    const url   = URL.createObjectURL(new Blob([bytes], { type: mime }));
    const audio = new Audio(url);
    audio.playbackRate = PLAYBACK_RATE;

    // Route through a GainNode so we can amplify above 1.0 (audio.volume cap).
    try {
      const ctx = BRAIN.getAudioCtx();
      if (ctx) {
        if (ctx.state === 'suspended') ctx.resume();   // mobile: graph is muted while suspended
        const src  = ctx.createMediaElementSource(audio);
        const gain = ctx.createGain();
        gain.gain.value = TTS_GAIN;
        src.connect(gain).connect(ctx.destination);
      }
    } catch (_) { /* gain optional */ }

    _ttsAudio = audio;
    _ttsBtn   = btn;
    btn.textContent = '■';
    btn.classList.add('speaking');
    btn.disabled = false;

    audio.onended = () => { URL.revokeObjectURL(url); stopTts(); };
    audio.onerror = () => { URL.revokeObjectURL(url); stopTts(); };
    audio.play().catch(() => stopTts());
  } catch (e) {
    btn.textContent = '▶';
    btn.disabled = false;
    addLog('TTS error: ' + e.message);
  }
}

// Returns true if the chat window is scrolled near its bottom (or has no
// scrollbar yet). Used to gate auto-scroll so the Captain does not get yanked
// back to the bottom while reading earlier messages. Threshold defaults to
// 120px which forgives small rendering jitter from images/markdown reflows.
function _isPinnedToBottom(winEl, threshold) {
  if (!winEl) return true;
  threshold = (typeof threshold === 'number') ? threshold : 120;
  const remaining = winEl.scrollHeight - winEl.scrollTop - winEl.clientHeight;
  return remaining <= threshold;
}

function appendMessage(role, text) {
  const win = document.getElementById('chat-window');
  const msg = document.createElement('div');
  msg.className = `chat-message ${role}`;

  const isData = role === 'data';
  const avatarLetter = isData ? _crewAvatar(MAIN_CHAT_CREW) : 'C';
  const senderLabel = isData ? crewLabel(MAIN_CHAT_CREW).toUpperCase() : 'CAPTAIN';
  const ts = new Date().toLocaleTimeString('en-US', { hour12: false });

  msg.innerHTML = `
    <div class="avatar">${avatarLetter}</div>
    <div class="bubble">
      ${isData ? '<button class="tts-btn" title="Speak">▶</button>' : ''}
      <button class="copy-btn" title="Copy">⧉</button>
      <div class="sender">${senderLabel}</div>
      <div class="text md-content">${renderMarkdown(text)}</div>
      <div class="timestamp">${ts}</div>
    </div>
  `;

  if (isData) {
    msg.querySelector('.tts-btn').addEventListener('click', function() {
      toggleTts(this, text);
    });
  }

  msg.querySelector('.copy-btn').addEventListener('click', function() {
    navigator.clipboard.writeText(text).then(() => {
      this.textContent = '✓';
      this.classList.add('copied');
      setTimeout(() => { this.textContent = '⧉'; this.classList.remove('copied'); }, 1500);
    }).catch(() => {
      this.textContent = '!';
      setTimeout(() => { this.textContent = '⧉'; }, 1500);
    });
  });

  const wasPinned = _isPinnedToBottom(win);
  win.appendChild(msg);
  if (wasPinned) win.scrollTop = win.scrollHeight;
  return msg;
}

function appendThinking() {
  const win = document.getElementById('chat-window');
  const msg = document.createElement('div');
  msg.className = 'chat-message data';
  msg.id = 'thinking-msg';
  msg.innerHTML = `
    <div class="avatar">${_crewAvatar(MAIN_CHAT_CREW)}</div>
    <div class="bubble">
      <div class="sender">${crewLabel(MAIN_CHAT_CREW).toUpperCase()}</div>
      <div class="text thinking-dots" id="thinking-step">
        Processing<span>.</span><span>.</span><span>.</span>
      </div>
      <div class="thinking-detail" id="thinking-detail"></div>
    </div>
  `;
  const wasPinned = _isPinnedToBottom(win);
  win.appendChild(msg);
  if (wasPinned) win.scrollTop = win.scrollHeight;
}

function removeThinking() {
  const el = document.getElementById('thinking-msg');
  if (el) el.remove();
}

function setStatus(text) {
  // Status line was retired from the chat input area; keep a hook so existing
  // callers still work and surface the message in the activity log instead.
  const el = document.getElementById('input-status');
  if (el) el.textContent = text;
}

// Track which textarea should receive dictated text. Updated whenever the user
// focuses an input (main pane or any project pane).
let _activeInputId = 'chat-input';
// Which window the Captain last interacted with: null = main pane, otherwise
// a workspace id. Esc routes its abort/unsend to ONLY this window so action
// in one pane doesn't bleed into another pane's in-flight request.
let _lastActiveWsId = null;
function setActiveInput(id) {
  if (id) _activeInputId = id;
  // Infer the window from the input id: 'chat-input' = main pane,
  // 'pane-input-ws<N>' = workspace N.
  if (id === 'chat-input') {
    _lastActiveWsId = null;
  } else {
    const m = (id || '').match(/^pane-input-ws(\d+)$/);
    if (m) _lastActiveWsId = parseInt(m[1], 10);
  }
}

let _statusPoller = null;

function startStatusPolling() {
  _statusPoller = setInterval(async () => {
    try {
      const r = await fetch(`${API_BASE}/status`);
      if (!r.ok) return;
      const s = await r.json();
      const stepEl   = document.getElementById('thinking-step');
      const detailEl = document.getElementById('thinking-detail');
      if (!stepEl) return;
      if (s.step === 'thinking') {
        stepEl.innerHTML = `Processing<span>.</span><span>.</span><span>.</span>`;
        detailEl.textContent = s.detail || '';
      } else if (s.step === 'tool') {
        stepEl.innerHTML = ``;
        stepEl.textContent = '▸ ' + (s.detail || 'Working...');
        detailEl.textContent = '';
      } else if (s.step === 'responding') {
        stepEl.textContent = '◆ Composing response...';
        detailEl.textContent = '';
      }
    } catch { /* bridge busy — silently skip */ }
  }, 600);
}

function stopStatusPolling() {
  if (_statusPoller) { clearInterval(_statusPoller); _statusPoller = null; }
}

// ── Media attachments (image / PDF / text) ───────────────────────
// User picks files via the [+] button next to the dictate mic. Files are
// staged per-pane in _pendingAttachmentsByPane and shipped with the next
// /chat_stream (main) or /chat (project pane) POST. Server gates by
// provider — attachments only work with claude-api* (or claude-cli).
// Pane keys: 'main' for the main channel, `ws<id>` for project workspaces.
const _pendingAttachmentsByPane = new Map();   // key -> [{name, kind, media_type, size, data(base64)}]
function _getPendingForPane(paneKey) {
  let list = _pendingAttachmentsByPane.get(paneKey);
  if (!list) { list = []; _pendingAttachmentsByPane.set(paneKey, list); }
  return list;
}
function _trayIdForPane(paneKey) {
  return paneKey === 'main' ? 'attachment-tray' : `attachment-tray-${paneKey}`;
}
function _fileInputIdForPane(paneKey) {
  return paneKey === 'main' ? 'attach-file-input' : `attach-file-input-${paneKey}`;
}
const _ATTACH_MAX = {
  image: 5 * 1024 * 1024,
  pdf:   32 * 1024 * 1024,
  text:  1 * 1024 * 1024,
  audio: 25 * 1024 * 1024,
};

function _classifyFile(file) {
  const t = file.type || '';
  const name = (file.name || '').toLowerCase();
  if (t.startsWith('image/')) {
    // Anthropic accepts jpeg, png, gif, webp.
    if (['image/jpeg','image/png','image/gif','image/webp'].includes(t)) return 'image';
    return null;
  }
  if (t === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
  // Audio — Whisper transcribes server-side, then result is folded into the prompt.
  const audioExts = ['.mp3','.m4a','.m4r','.wav','.ogg','.webm','.aac','.flac','.opus'];
  if (t.startsWith('audio/') || audioExts.some(e => name.endsWith(e))) return 'audio';
  // Text-ish — anything with text/* MIME OR a known code/text extension.
  const textExts = ['.md','.markdown','.txt','.py','.js','.ts','.jsx','.tsx','.json',
                    '.yaml','.yml','.toml','.html','.css','.sh','.bat','.ps1','.csv','.log'];
  if (t.startsWith('text/') || textExts.some(e => name.endsWith(e))) return 'text';
  return null;
}

function _readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      // FileReader gives "data:<mime>;base64,<data>" — strip the prefix.
      const res = reader.result || '';
      const comma = res.indexOf(',');
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function handleAttachmentFiles(fileList, paneKey) {
  if (!fileList || !fileList.length) return;
  paneKey = paneKey || 'main';
  // Snapshot into a real array NOW, before the first `await` below. The input's
  // onchange clears the field (this.value='') synchronously right after calling
  // us, and a FileList is a *live* view of input.files — so once we suspend on
  // the first await, the live list empties and only file #1 survives the loop.
  // Array.from copies the File references out, decoupling us from the input.
  const files = Array.from(fileList);
  const bucket = _getPendingForPane(paneKey);
  for (const file of files) {
    const kind = _classifyFile(file);
    if (!kind) {
      addLog(`Skipped ${file.name} — unsupported file type`);
      continue;
    }
    if (file.size > _ATTACH_MAX[kind]) {
      addLog(`Skipped ${file.name} — exceeds ${Math.round(_ATTACH_MAX[kind]/1024/1024)} MB limit for ${kind}`);
      continue;
    }
    try {
      const data = await _readFileAsBase64(file);
      bucket.push({
        name: file.name,
        kind,
        media_type: file.type || (kind === 'pdf' ? 'application/pdf' : 'text/plain'),
        size: file.size,
        data,
      });
    } catch (e) {
      addLog(`Could not read ${file.name}: ${e.message || e}`);
    }
  }
  _renderAttachmentTray(paneKey);
}

function _removePendingAttachment(idx, paneKey) {
  paneKey = paneKey || 'main';
  const bucket = _getPendingForPane(paneKey);
  if (idx >= 0 && idx < bucket.length) {
    bucket.splice(idx, 1);
    _renderAttachmentTray(paneKey);
  }
}

function _renderAttachmentTray(paneKey) {
  paneKey = paneKey || 'main';
  const tray = document.getElementById(_trayIdForPane(paneKey));
  if (!tray) return;
  const bucket = _getPendingForPane(paneKey);
  if (bucket.length === 0) {
    tray.hidden = true;
    tray.innerHTML = '';
    return;
  }
  tray.hidden = false;
  tray.innerHTML = bucket.map((a, i) => {
    const icon = a.kind === 'image' ? '🖼' :
                 a.kind === 'pdf'   ? '📄' :
                 a.kind === 'audio' ? '🎤' : '📝';
    const sizeKb = (a.size / 1024).toFixed(0);
    const safeName = a.name.replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
    return `
      <div class="attachment-chip" data-kind="${a.kind}" title="${safeName} (${sizeKb} KB)">
        <span class="attachment-chip-icon">${icon}</span>
        <span class="attachment-chip-name">${safeName}</span>
        <span class="attachment-chip-size">${sizeKb} KB</span>
        <button type="button" class="attachment-chip-remove" onclick="_removePendingAttachment(${i}, '${paneKey}')" aria-label="Remove ${safeName}">✕</button>
      </div>`;
  }).join('');
}

async function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  const mainBucket = _getPendingForPane('main');
  // Allow sending with only attachments (no text) — like ChatGPT/Gemini.
  if (!text && mainBucket.length === 0) return;

  _mainChatUsed = true;

  // Snapshot + clear the staged attachments now so a fast second submit
  // can't double-send them.
  const attachments = mainBucket.splice(0);
  _renderAttachmentTray('main');

  input.value = '';
  _lastActiveWsId = null;  // sending into main pane = main pane is last-active

  // Bubble preview — show the text plus a small list of attached files.
  let bubbleText = text;
  if (attachments.length) {
    const list = attachments.map(a => `📎 ${a.name}`).join('\n');
    bubbleText = text ? `${text}\n\n${list}` : list;
  }
  const bubble = appendMessage('user', bubbleText);
  addLog(`Captain: ${(text || `[${attachments.length} attachment(s)]`).substring(0, 30)}...`);

  if (isThinking) {
    _messageQueue.push({ text, bubble, attachments });
    _updateQueueBadge();
    addLog(`Queued (#${_messageQueue.length}) — will send when Data finishes`);
    return;
  }

  _inFlightUserBubble = bubble;
  _dispatchChatMessage(text, attachments);
}

async function _dispatchChatMessage(text, attachments) {
  attachments = attachments || [];
  playDataSound('transmit');
  // Speak this reply sentence-by-sentence in the Computer voice if the
  // wake-word dictation path armed it. Captured + cleared here so it can
  // never leak onto a later turn.
  const speakReply = _autoSpeakNextReply;
  _autoSpeakNextReply = false;
  isThinking = true;
  _abortController = new AbortController();
  // Keep the send button visible while thinking — relabel it QUEUE so the
  // Captain can stack follow-ups (Enter or click) mid-turn instead of only
  // seeing ABORT. sendMessage() already routes these into _messageQueue.
  const _sendBtn = document.getElementById('send-btn');
  _sendBtn.textContent = 'QUEUE';
  _sendBtn.title = 'Send a follow-up now — it queues and fires when Data finishes';
  _sendBtn.classList.add('queue-mode');
  document.getElementById('stop-btn').style.display = '';
  setStatus('TRANSMITTING...');

  const chatWin = document.getElementById('chat-window');
  const thoughtEl = _createThoughtStream(chatWin);
  let streamMsg = null;
  let streamEnded = false;   // set true when the `done` (or `error`) SSE event arrives
  let serverError = '';      // text from an `error` event, if any

  // IDLE WATCHDOG (heartbeat-driven) — mirrors the project-pane path. A healthy
  // stream is NEVER silent: tokens, thinking lines, and a `: keepalive` SSE
  // comment every 8s keep bytes flowing. So we don't cap total elapsed time; we
  // cap silence. If no bytes arrive for IDLE_LIMIT_MS the tunnel or worker has
  // stalled (slow-link buffering, TCP drop, child crash) — abort so the UI can
  // recover instead of hanging on "Processing" forever. Reset on every read.
  let watchdogFired = false;
  const IDLE_LIMIT_MS = 90 * 1000;
  let idleTimer = null;
  const resetIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      watchdogFired = true;
      try { _abortController?.abort(); } catch {}
    }, IDLE_LIMIT_MS);
  };

  try {
    resetIdle();
    // Honor the main pane's per-window provider override if a project is
    // active; otherwise fall through to the global ACTIVE_PROVIDER.
    const mainWs = [..._workspaces.values()].find(w => w.isMain);
    const res = await fetch(`${API_BASE}/chat_stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: _abortController.signal,
      body: JSON.stringify({
        message:      text,
        project_path: mainWs?.path || '',
        pane_id:      _paneId('main'),
        provider:     mainWs?.provider || '',
        crew:         MAIN_CHAT_CREW,
        attachments:  attachments,
      }),
    });

    // Server returns 400 with {"error": "..."} when attachments are sent to
    // a non-multimodal provider, or when a file is too large. Surface that
    // clearly instead of pretending the message was sent.
    if (res.status === 400) {
      let errMsg = `HTTP 400`;
      try { const j = await res.json(); if (j && j.error) errMsg = j.error; } catch (_) {}
      throw new Error(errMsg);
    }
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      resetIdle();   // ← heartbeat: any byte (incl. `: keepalive`) = stream still alive
      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by \n\n
      const events = buffer.split('\n\n');
      buffer = events.pop();

      for (const eventStr of events) {
        if (!eventStr.trim()) continue;
        let evType = 'message', evData = '';
        for (const line of eventStr.split('\n')) {
          if (line.startsWith('event: ')) evType = line.slice(7).trim();
          else if (line.startsWith('data: '))  evData = line.slice(6).trim();
        }
        if (!evData) continue;
        try {
          const payload = JSON.parse(evData);
          if (evType === 'thinking') {
            _addThoughtLine(thoughtEl, payload.text);
          } else if (evType === 'token') {
            if (!streamMsg) {
              thoughtEl.classList.add('done');
              streamMsg = _startStreamBubble(chatWin);
              if (speakReply) _speakStreamBegin();
            }
            _appendStreamToken(streamMsg, payload.text);
            if (speakReply) _speakStreamFeed(payload.text);
          } else if (evType === 'meta') {
            try { _streamMeta = JSON.parse(payload.text); } catch { _streamMeta = null; }
          } else if (evType === 'error') {
            // Server surfaced a hard error mid-stream. Capture it, end the loop.
            serverError = payload.text || 'Unknown error';
            streamEnded = true;
          } else if (evType === 'done') {
            streamEnded = true;
            clearInterval(_streamTimerInterval); _streamTimerInterval = null;
            if (speakReply) _speakStreamEnd();
            const elapsed = ((Date.now() - _streamStartTime) / 1000).toFixed(1);
            if (streamMsg) _finalizeStreamBubble(streamMsg);
            const timerEl = thoughtEl.querySelector('.thought-timer');
            if (timerEl) {
              const fmt = n => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
              const hasTokens = _streamMeta &&
                (_streamMeta.input_tokens > 0 || _streamMeta.output_tokens > 0);
              // Fold token stats into the timer chip so the reasoning trail
              // below stays intact and readable while it lingers.
              timerEl.textContent = hasTokens
                ? `${elapsed}s · ${fmt(_streamMeta.input_tokens)}↓ ${fmt(_streamMeta.output_tokens)}↑`
                : `${elapsed}s`;
            }
            // Dim the trail and keep it visible for 2s so the Captain can see
            // what Data did, then clear it.
            thoughtEl.classList.add('done');
            setTimeout(() => thoughtEl.remove(), 2000);
            playDataSound('receive');
            addLog('Data responded');
          }
        } catch { /* ignore malformed SSE lines */ }
      }
      // Don't wait for the HTTP body to physically close — through the
      // cloudflared tunnel on a slow link that close can be buffered or lost,
      // leaving the loop blocked on reader.read() forever (the 8s keepalive
      // keeps the socket alive so it never errors out). Break as soon as we've
      // seen `done`/`error`, and cancel the reader to release the connection.
      if (streamEnded) { try { await reader.cancel(); } catch {} break; }
    }

    // An `error` event ends the stream with no bubble — surface it plainly.
    if (serverError && !streamMsg) {
      clearInterval(_streamTimerInterval); _streamTimerInterval = null;
      thoughtEl.remove();   // don't strand the thought card when no reply followed
      appendMessage('data', `⚠ ${serverError}`);
      setStatus('STREAM ERROR');
      addLog(`Stream error: ${serverError}`);
    }

  } catch (e) {
    clearInterval(_streamTimerInterval); _streamTimerInterval = null;
    if (speakReply) _speakStreamEnd();
    thoughtEl.remove();
    if (streamMsg) {
      // Partial reply already on screen — keep what arrived rather than discard.
      _finalizeStreamBubble(streamMsg);
      if (watchdogFired) {
        setStatus('STREAM STALLED');
        addLog(`Stream went silent for ${IDLE_LIMIT_MS / 1000}s — aborted by watchdog (partial reply kept; reload to see full saved reply)`);
      }
    } else if (watchdogFired) {
      // No bytes for IDLE_LIMIT_MS — tunnel/worker stalled. The reply is saved
      // to conversation_history.json before `done` is sent, so Data retains the
      // context even though this window can't repaint it (no history rehydrate).
      appendMessage('data', `⏱ The connection went silent for ${IDLE_LIMIT_MS / 1000}s and was aborted, Captain. The reply likely completed server-side — I've kept it in memory, so just continue the conversation (the exact text is in conversation_history.json if you need it).`);
      setStatus('STREAM STALLED');
      addLog('Stream went silent — aborted by idle watchdog');
    } else {
      appendMessage('data', offlineResponse(text));
      setStatus('BRIDGE OFFLINE — LOCAL MODE');
      addLog('Bridge server not connected');
    }
  } finally {
    if (idleTimer) clearTimeout(idleTimer);
  }

  isThinking = false;
  _abortController = null;
  _inFlightUserBubble = null;
  const _sendBtnDone = document.getElementById('send-btn');
  _sendBtnDone.textContent = 'TRANSMIT';
  _sendBtnDone.title = '';
  _sendBtnDone.classList.remove('queue-mode');
  _sendBtnDone.style.display = '';
  document.getElementById('stop-btn').style.display = 'none';
  setStatus('AWAITING INPUT');
  fetchVitals();

  // Drain one queued message — re-enter via setTimeout so this stack unwinds
  // and the DOM finishes painting before the next request takes over.
  // Wrap the queued text with a context hint so Data knows it arrived while
  // he was still responding to the previous turn and can decide whether to
  // fold it into the next reply or change the plan.
  if (_messageQueue.length) {
    const next = _messageQueue.shift();
    _updateQueueBadge();
    addLog(`Dispatching queued message (${_messageQueue.length} remaining)`);
    const wrapped =
      "[QUEUED FOLLOW-UP — the Captain sent this while you were still " +
      "responding to the previous turn. If it changes the plan, fold it " +
      "into your reply; otherwise address it directly now.]\n\n" + next.text;
    _inFlightUserBubble = next.bubble;
    setTimeout(() => _dispatchChatMessage(wrapped, next.attachments || []), 50);
  }
}

// ── Streaming thought stream + bubble helpers ─────────────

function _activeMainProviderLabel() {
  // Best-effort label for the model about to answer the main pane, so the
  // thought stream can open with something concrete instead of a blank line.
  const mainWs = [..._workspaces.values()].find(w => w.isMain);
  const id = mainWs?.provider || _lastKnownActiveProvider;
  const p = (_providersCache || []).find(x => x.id === id);
  return (p && p.label) ? p.label : 'the main computer core';
}

function _createThoughtStream(winEl) {
  playDataSound('processing');
  _streamStartTime = Date.now();
  _streamMeta = null;
  const el = document.createElement('div');
  el.className = 'thought-stream';
  el.innerHTML =
    '<div class="thought-stream-top">' +
      '<div class="thought-stream-header">NEURAL INNER MONOLOGUE</div>' +
      '<span class="thought-timer">0.0s</span>' +
    '</div>' +
    '<div class="thought-lines"></div>';
  // Seed line — the stream is never blank, even before the first SSE event,
  // and a fast text-only reply (no `thinking` events) still shows context.
  _addThoughtLine(el, `*Engaging ${_activeMainProviderLabel()}…*`);
  const wasPinned = _isPinnedToBottom(winEl);
  winEl.appendChild(el);
  if (wasPinned) winEl.scrollTop = winEl.scrollHeight;
  const timerEl = el.querySelector('.thought-timer');
  _streamTimerInterval = setInterval(() => {
    timerEl.textContent = ((Date.now() - _streamStartTime) / 1000).toFixed(1) + 's';
  }, 100);
  return el;
}

// Rolling log of what Data is doing — each `thinking` event APPENDS a step
// (mode banner, tool call, tool result, reasoning preview) rather than
// overwriting, capped to the last N so a long agentic turn stays compact.
const _THOUGHT_MAX_LINES = 6;
function _addThoughtLine(thoughtEl, text) {
  const container = thoughtEl.querySelector('.thought-lines') || thoughtEl;
  const win = thoughtEl.closest('.chat-window');
  const wasPinned = win ? _isPinnedToBottom(win) : false;
  const line = document.createElement('div');
  line.className = 'thought-line' + (
    text.startsWith('  →') ? ' tool-result' :
    text.startsWith('*')   ? ' status' : ' tool-call'
  );
  line.textContent = text;
  container.appendChild(line);
  while (container.children.length > _THOUGHT_MAX_LINES) {
    container.removeChild(container.firstChild);
  }
  if (win && wasPinned) win.scrollTop = win.scrollHeight;
}

let _streamAccum = '';
let _streamStartTime = 0;
let _streamTimerInterval = null;
let _streamMeta = null;

function _startStreamBubble(winEl) {
  _streamAccum = '';
  const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
  const msg = document.createElement('div');
  msg.className = 'chat-message data';
  msg.innerHTML = `
    <div class="avatar">${_crewAvatar(MAIN_CHAT_CREW)}</div>
    <div class="bubble">
      <div class="sender">${crewLabel(MAIN_CHAT_CREW).toUpperCase()}</div>
      <div class="text streaming"></div>
      <div class="timestamp">${ts}</div>
    </div>
  `;
  const wasPinned = _isPinnedToBottom(winEl);
  winEl.appendChild(msg);
  if (wasPinned) winEl.scrollTop = winEl.scrollHeight;
  return msg;
}

function _appendStreamToken(streamMsg, token) {
  _streamAccum += token;
  const textEl = streamMsg.querySelector('.text');
  const win = streamMsg.closest('.chat-window');
  // Capture pin-state BEFORE mutating textContent so growing content doesn't
  // spuriously flip us out of "pinned" each token.
  const wasPinned = _isPinnedToBottom(win);
  if (textEl) textEl.textContent = _streamAccum;
  if (win && wasPinned) win.scrollTop = win.scrollHeight;
}

function _finalizeStreamBubble(streamMsg) {
  const finalText = _streamAccum;
  const textEl = streamMsg.querySelector('.text');
  if (textEl) {
    textEl.className = 'text md-content';
    textEl.innerHTML = renderMarkdown(finalText);
  }
  const bubble = streamMsg.querySelector('.bubble');
  if (bubble) {
    const ttsBtn = document.createElement('button');
    ttsBtn.className = 'tts-btn';
    ttsBtn.title = 'Speak';
    ttsBtn.textContent = '▶';
    ttsBtn.addEventListener('click', function() { toggleTts(this, finalText); });

    const copyBtn = document.createElement('button');
    copyBtn.className = 'copy-btn';
    copyBtn.title = 'Copy';
    copyBtn.textContent = '⧉';
    copyBtn.addEventListener('click', function() {
      navigator.clipboard.writeText(finalText).then(() => {
        this.textContent = '✓'; this.classList.add('copied');
        setTimeout(() => { this.textContent = '⧉'; this.classList.remove('copied'); }, 1500);
      }).catch(() => { this.textContent = '!'; setTimeout(() => { this.textContent = '⧉'; }, 1500); });
    });

    bubble.insertBefore(copyBtn, bubble.firstChild);
    bubble.insertBefore(ttsBtn, bubble.firstChild);
  }
}

function offlineResponse(text) {
  const responses = [
    "I am processing your inquiry, Captain. However, my connection to the primary computer core appears to be experiencing intermittent disruptions. I recommend ensuring the bridge server is online on port 7777.",
    "Fascinating query, Captain. My neural matrix is functioning within normal parameters, however the main computer interface is currently unavailable. Please verify the DATA bridge server is running.",
    "Captain, I must inform you that my sub-processor subroutines are unable to reach the primary inference core at this time. The bridge server on port 7777 does not appear to be responding.",
    "I am unable to provide a full analysis at this moment, Sir. The communication link between this interface and my primary neural net appears to be offline. Please start the bridge server.",
  ];
  return responses[Math.floor(Math.random() * responses.length)];
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
}

// Match a Windows absolute path (e.g. C:\Users\you\Documents\MyProject) or
// a POSIX absolute path (/home/...). Used inside code spans so the bridge's
// `Rooted in C:\...\MyProject` style messages become one-click openable.
const _PATH_RE = /^([A-Za-z]:[\\/][^\s"<>]+|\/(?:[^\s"<>/]+\/?)+)$/;

function _inlineMd(text) {
  // Extract markdown links + bare URLs to placeholders first, so the bold /
  // italic / code regexes below don't chew up URL characters.
  const slots = [];
  const stash = (html) => {
    slots.push(html);
    return ` L${slots.length - 1} `;
  };

  // 0. ![alt](path) — markdown image embed. Two forms:
  //      ![alt](http(s)://...)            → straight remote <img>
  //      ![alt](C:\path\to\file.png)      → routed through /file?path= so the
  //                                          bridge serves it from the sandbox.
  //    Click opens the full file in Explorer/default viewer via openPath().
  const IMG_EXT_RE = /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i;
  text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, alt, url) => {
    const safeAlt = alt.replace(/"/g, '&quot;');
    if (/^https?:\/\//i.test(url)) {
      return stash(`<img class="md-img" src="${url}" alt="${safeAlt}" loading="lazy">`);
    }
    if (!IMG_EXT_RE.test(url)) {
      const safe = url.replace(/"/g, '&quot;');
      return stash(`<code class="md-code md-path" data-open-path="${safe}" title="Open ${url}">${url}</code>`);
    }
    const safePath = url.replace(/"/g, '&quot;');
    const src      = `${API_BASE}/file?path=${encodeURIComponent(url)}`;
    return stash(
      `<img class="md-img" src="${src}" alt="${safeAlt}" title="${safePath}" ` +
      `data-open-path="${safePath}" loading="lazy">`
    );
  });

  // 1. [label](url)   — full markdown link
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_m, label, url) =>
    stash(`<a class="md-link" href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`));

  // 2. Bare http(s) URLs — but only when not already inside a stashed link
  //     Exclude '*' from the URL so a bold/italic wrapper (**url** / *url*)
  //     is not swallowed into the href — the trailing markers stay outside and
  //     the bold/italic pass below wraps a working link instead of breaking it.
  text = text.replace(/(^|[^"=>])(https?:\/\/[^\s<>"*]+[^\s<>".,;:!?)\]'*])/g,
    (_m, lead, url) => `${lead}${stash(`<a class="md-link" href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`)}`);

  // 3. Inline code — and if the code is an absolute path, make it clickable.
  text = text.replace(/`([^`]+)`/g, (_m, inner) => {
    if (_PATH_RE.test(inner)) {
      const safe = inner.replace(/"/g, '&quot;');
      return stash(`<code class="md-code md-path" data-open-path="${safe}" title="Open ${safe}">${inner}</code>`);
    }
    return stash(`<code class="md-code">${inner}</code>`);
  });

  // 4. Now run bold / italic on the placeholderized text. Placeholders are
  //     L<idx>  — no markdown chars, won't be touched.
  text = text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');

  // 5. Restore the link/code placeholders.
  return text.replace(/ L(\d+) /g, (_m, i) => slots[+i]);
}

function renderMarkdown(raw) {
  const escaped = raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  const lines = escaped.split('\n');
  const out = [];
  let inList = false;

  for (const line of lines) {
    if (/^-{3,}$/.test(line.trim())) {
      if (inList) { out.push('</ul>'); inList = false; }
      out.push('<hr class="md-hr">');
      continue;
    }
    if (/^[-*] /.test(line)) {
      if (!inList) { out.push('<ul class="md-list">'); inList = true; }
      out.push('<li>' + _inlineMd(line.replace(/^[-*] /, '')) + '</li>');
      continue;
    }
    if (inList) { out.push('</ul>'); inList = false; }
    if (line.trim() === '') { out.push('<div class="md-gap"></div>'); continue; }
    out.push('<p class="md-p">' + _inlineMd(line) + '</p>');
  }
  if (inList) out.push('</ul>');
  return out.join('');
}

// ═══════════════════════════════════════════════════════════
// NEURAL MATRIX — Obsidian-style zoomable graph
// ═══════════════════════════════════════════════════════════

let matrixInitialized = false;
let simulation = null;
let matrixNodes = [];
let matrixLinks = [];
let matrixZoom = null;
let matrixSvg = null;

const TYPE_COLOR = {
  core:      '#FF9900',   // root folder
  folder:    '#FC6600',   // subfolders
  memory:    '#FFCC00',   // .md .db
  skill:     '#99AAFF',   // .py .js .html .css
  knowledge: '#44FF99',   // .txt
  system:    '#CC88FF',   // .yaml .env .json
  audio:     '#FF88CC',   // .mp3 .wav .rpp
  archive:   '#888888',   // .zip
  file:      '#555577',   // unknown
  crew:      '#00CCFF',   // bridge crew officers
};

// Hub nodes are the cluster centres — larger, pinned loosely to sectors
const SEED_NODES = [
  // ── Root ───────────────────────────────────────────────
  { id: 'neural-core', label: 'NEURAL CORE',    type: 'core',      r: 22, hub: true },

  // ── Hub tier (branches off root) ───────────────────────
  { id: 'memory-banks',    label: 'MEMORY BANKS',       type: 'memory',    r: 16, hub: true },
  { id: 'skill-net',       label: 'SKILL NET',          type: 'skill',     r: 16, hub: true },
  { id: 'logic-engine',    label: 'LOGIC ENGINE',       type: 'core',      r: 15, hub: true },
  { id: 'identity',        label: 'IDENTITY',           type: 'core',      r: 15, hub: true },
  { id: 'emotion-chip',    label: 'EMOTION CHIP',       type: 'emotion',   r: 14, hub: true },

  // ── Memory cluster ─────────────────────────────────────
  { id: 'captain-profile', label: 'CAPTAIN PROFILE',   type: 'memory',    r: 10 },
  { id: 'atlas-profile',   label: 'ATLAS',             type: 'memory',    r: 9  },
  { id: 'ops-manual',      label: 'OPS MANUAL',        type: 'knowledge', r: 9  },
  { id: 'system-specs',    label: 'SYSTEM SPECS',      type: 'memory',    r: 9  },
  { id: 'crew-profiles',   label: 'CREW PROFILES',     type: 'memory',    r: 8  },

  // ── Skill cluster ──────────────────────────────────────
  { id: 'web-search',      label: 'WEB SEARCH',        type: 'skill',     r: 10 },
  { id: 'computer-access', label: 'COMPUTER ACCESS',   type: 'skill',     r: 10 },
  { id: 'file-ops',        label: 'FILE OPS',          type: 'skill',     r: 9  },
  { id: 'calendar',        label: 'SCHEDULING',        type: 'skill',     r: 9  },
  { id: 'research',        label: 'RESEARCH',          type: 'skill',     r: 9  },
  { id: 'browser-auto',    label: 'BROWSER AUTO',      type: 'skill',     r: 8  },

  // ── Identity / System cluster ──────────────────────────
  { id: 'tts-module',      label: 'VOICE TTS',         type: 'system',    r: 10 },
  { id: 'stt-module',      label: 'VOICE STT',         type: 'system',    r: 10 },
  { id: 'wake-word',       label: '"COMPUTER"',        type: 'system',    r: 8  },
  { id: 'data-ui',        label: 'DATA INTERFACE',   type: 'system',    r: 8  },

  // ── Logic / Knowledge cluster ──────────────────────────
  { id: 'language-matrix', label: 'LANGUAGE',          type: 'knowledge', r: 10 },
  { id: 'math-engine',     label: 'MATHEMATICS',       type: 'knowledge', r: 9  },
  { id: 'science-db',      label: 'SCIENCE DATABASE',  type: 'knowledge', r: 9  },
  { id: 'reasoning',       label: 'REASONING',         type: 'core',      r: 9  },

  // ── Emotion cluster ────────────────────────────────────
  { id: 'spot',            label: 'WONDER',            type: 'emotion',   r: 9  },
  { id: 'curiosity',       label: 'CURIOSITY',         type: 'emotion',   r: 8  },
  { id: 'friendship',      label: 'FRIENDSHIP',        type: 'emotion',   r: 8  },

  // -- Agent Crew cluster
  { id: 'bridge-crew',     label: 'AGENT CREW',        type: 'crew',      r: 16, hub: true },
  { id: 'atlas-crew',      label: 'ATLAS',             type: 'crew',      r: 11 },
  { id: 'vector-crew',     label: 'VECTOR',            type: 'crew',      r: 10 },
  { id: 'sentinel-crew',   label: 'SENTINEL',          type: 'crew',      r: 10 },
  { id: 'probe-crew',      label: 'PROBE',             type: 'crew',      r: 10 },
];

const SEED_LINKS = [
  // Root → hubs
  { source: 'neural-core', target: 'memory-banks',    w: 2.5 },
  { source: 'neural-core', target: 'skill-net',       w: 2.5 },
  { source: 'neural-core', target: 'logic-engine',    w: 2.5 },
  { source: 'neural-core', target: 'identity',        w: 2.5 },
  { source: 'neural-core', target: 'emotion-chip',    w: 2.0 },

  // Memory cluster
  { source: 'memory-banks',    target: 'captain-profile', w: 1.5 },
  { source: 'memory-banks',    target: 'atlas-profile',  w: 1.2 },
  { source: 'memory-banks',    target: 'ops-manual',      w: 1.2 },
  { source: 'memory-banks',    target: 'system-specs',    w: 1.2 },
  { source: 'memory-banks',    target: 'crew-profiles',   w: 1.0 },
  { source: 'crew-profiles',   target: 'atlas-profile',  w: 0.8 },

  // Skill cluster
  { source: 'skill-net',       target: 'web-search',      w: 1.5 },
  { source: 'skill-net',       target: 'computer-access', w: 1.5 },
  { source: 'skill-net',       target: 'file-ops',        w: 1.2 },
  { source: 'skill-net',       target: 'calendar',        w: 1.2 },
  { source: 'skill-net',       target: 'research',        w: 1.2 },
  { source: 'skill-net',       target: 'browser-auto',    w: 1.0 },
  { source: 'web-search',      target: 'research',        w: 0.8 },
  { source: 'computer-access', target: 'file-ops',        w: 0.8 },

  // Identity / system
  { source: 'identity',        target: 'tts-module',      w: 1.5 },
  { source: 'identity',        target: 'stt-module',      w: 1.5 },
  { source: 'stt-module',      target: 'wake-word',       w: 1.2 },
  { source: 'identity',        target: 'data-ui',        w: 1.0 },

  // Logic / knowledge
  { source: 'logic-engine',    target: 'language-matrix', w: 1.5 },
  { source: 'logic-engine',    target: 'math-engine',     w: 1.2 },
  { source: 'logic-engine',    target: 'science-db',      w: 1.2 },
  { source: 'logic-engine',    target: 'reasoning',       w: 1.5 },
  { source: 'language-matrix', target: 'reasoning',       w: 0.8 },

  // Emotion cluster
  { source: 'emotion-chip',    target: 'spot',            w: 1.5 },
  { source: 'emotion-chip',    target: 'curiosity',       w: 1.2 },
  { source: 'emotion-chip',    target: 'friendship',      w: 1.2 },

  // Cross-cluster (makes it feel like Obsidian)
  { source: 'captain-profile', target: 'tts-module',      w: 0.6 },
  { source: 'research',        target: 'science-db',      w: 0.7 },
  { source: 'language-matrix', target: 'captain-profile', w: 0.6 },
  { source: 'reasoning',       target: 'skill-net',       w: 0.7 },
  { source: 'curiosity',       target: 'research',        w: 0.6 },
  { source: 'friendship',      target: 'captain-profile', w: 0.7 },

  // Bridge Crew
  { source: 'neural-core', target: 'bridge-crew',   w: 2.0 },
  { source: 'bridge-crew',     target: 'atlas-crew',   w: 1.5 },
  { source: 'bridge-crew',     target: 'vector-crew',    w: 1.5 },
  { source: 'bridge-crew',     target: 'sentinel-crew',     w: 1.5 },
  { source: 'bridge-crew',     target: 'probe-crew',   w: 1.5 },
  { source: 'crew-profiles',   target: 'bridge-crew',   w: 0.8 },
];

async function initMatrix() {
  matrixInitialized = true;
  playDataSound('engage');

  // Show loading state
  const graphEl = document.getElementById('matrix-graph');
  graphEl.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:center;height:100%;
                font-family:var(--font-mono);font-size:13px;color:var(--data-orange2);
                letter-spacing:0.15em;flex-direction:column;gap:12px;">
      <div style="animation:blink 1.2s infinite">SCANNING FILE SYSTEM...</div>
      <div style="font-size:10px;color:#444">Accessing main computer core on port 7777</div>
    </div>`;

  try {
    const res = await fetch(`${API_BASE}/neural`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    matrixNodes = data.nodes;
    // Drop links whose endpoints don't exist — a single dangling id makes
    // d3.forceLink throw and the whole graph collapses into the center.
    const _nodeIds = new Set(matrixNodes.map(n => n.id));
    matrixLinks = (data.links || []).filter(l => _nodeIds.has(l.source) && _nodeIds.has(l.target));
    addLog('Neural matrix mapped');
  } catch (e) {
    // Bridge offline — fall back to neural map
    addLog('Bridge offline — using neural map');
    matrixNodes = SEED_NODES.map(n => ({ ...n }));
    matrixLinks = SEED_LINKS.map(l => ({ ...l }));

    // Show offline notice briefly
    graphEl.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:center;height:100%;
                  font-family:var(--font-mono);font-size:12px;color:#444;
                  flex-direction:column;gap:8px;">
        <div>BRIDGE SERVER OFFLINE</div>
        <div style="font-size:10px">Start bridge_server.py to see your project files</div>
        <div style="font-size:10px;color:#333">Showing neural map instead</div>
      </div>`;
    await new Promise(r => setTimeout(r, 1800));
  }

  document.getElementById('node-count').textContent = `${matrixNodes.length} NODES`;
  document.getElementById('edge-count').textContent = `${matrixLinks.length} CONNECTIONS`;

  renderMainMatrix();
  renderMiniMatrix();

  // DOCUMENTS is the default sub-tab — populate its project grid so it is
  // ready the moment the Captain opens the Neural Matrix panel.
  loadDocsProjects();
}

// Scan a specific directory
async function scanDir() {
  const dir = prompt('Directory path to scan:',
    '');
  if (!dir) return;
  playDataSound('engage');
  try {
    const res = await fetch(`${API_BASE}/files?dir=${encodeURIComponent(dir)}`);
    const data = await res.json();
    matrixNodes = data.nodes;
    matrixLinks = data.links;
    document.getElementById('node-count').textContent = `${matrixNodes.length} NODES`;
    document.getElementById('edge-count').textContent = `${matrixLinks.length} CONNECTIONS`;
    renderMainMatrix();
    renderMiniMatrix();
    addLog(`Scanned: ${dir}`);
  } catch {
    addLog('Scan failed — bridge offline?');
  }
}

function renderMainMatrix() {
  const container = document.getElementById('matrix-graph');
  container.innerHTML = '';
  const w = container.clientWidth || 900;
  const h = container.clientHeight || 520;

  matrixSvg = d3.select('#matrix-graph')
    .append('svg')
    .attr('width', '100%')
    .attr('height', '100%')
    .style('background', '#020208');

  const defs = matrixSvg.append('defs');

  // Glow filters per type
  Object.entries(TYPE_COLOR).forEach(([type, color]) => {
    const f = defs.append('filter')
      .attr('id', `glow-${type}`)
      .attr('x', '-50%').attr('y', '-50%')
      .attr('width', '200%').attr('height', '200%');
    f.append('feGaussianBlur').attr('in', 'SourceGraphic').attr('stdDeviation', '4').attr('result', 'blur');
    const merge = f.append('feMerge');
    merge.append('feMergeNode').attr('in', 'blur');
    merge.append('feMergeNode').attr('in', 'SourceGraphic');
  });

  // Arrowhead marker
  defs.append('marker')
    .attr('id', 'arrow')
    .attr('viewBox', '0 -4 8 8')
    .attr('refX', 8).attr('refY', 0)
    .attr('markerWidth', 4).attr('markerHeight', 4)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-4L8,0L0,4')
    .attr('fill', '#2a2a4a');

  // Zoomable canvas
  const canvas = matrixSvg.append('g').attr('class', 'canvas');

  matrixZoom = d3.zoom()
    .scaleExtent([0.15, 4])
    .on('zoom', (event) => canvas.attr('transform', event.transform));

  matrixSvg.call(matrixZoom)
    .on('dblclick.zoom', null); // disable dblclick zoom

  // Start centered
  matrixSvg.call(matrixZoom.transform,
    d3.zoomIdentity.translate(w / 2, h / 2).scale(0.75));

  // Links — curved paths
  const linkG = canvas.append('g').attr('class', 'links');
  const nodeG = canvas.append('g').attr('class', 'nodes');

  const linkSel = linkG.selectAll('path')
    .data(matrixLinks)
    .enter().append('path')
    .attr('fill', 'none')
    .attr('stroke', d => d.w > 2 ? '#3a3a5a' : d.w > 1 ? '#252535' : '#181825')
    .attr('stroke-width', d => d.w * 0.9)
    .attr('stroke-opacity', d => d.w > 2 ? 0.9 : d.w > 1 ? 0.7 : 0.45)
    .attr('marker-end', d => d.w < 1 ? 'url(#arrow)' : null);

  // Node groups
  const nodeSel = nodeG.selectAll('g')
    .data(matrixNodes)
    .enter().append('g')
    .attr('class', 'node')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) simulation.alphaTarget(0.25).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end', (event, d) => {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = d.x; d.fy = d.y; // keep pinned where dropped
      })
    )
    .on('click', (event, d) => {
      event.stopPropagation();
      highlightNode(d, nodeSel, linkSel);
      if (!d.path) return;
      openPath(d.path);
      addLog(`Opening: ${d.label}`);
    });

  // Outer glow ring
  nodeSel.append('circle')
    .attr('r', d => (d.r || 10) + 5)
    .attr('fill', 'none')
    .attr('stroke', d => TYPE_COLOR[d.type] || '#FF9900')
    .attr('stroke-opacity', 0.15)
    .attr('stroke-width', 1);

  // Main circle
  nodeSel.append('circle')
    .attr('r', d => d.r || 10)
    .attr('fill', d => TYPE_COLOR[d.type] || '#FF9900')
    .attr('fill-opacity', d => d.hub ? 0.25 : 0.15)
    .attr('stroke', d => TYPE_COLOR[d.type] || '#FF9900')
    .attr('stroke-width', d => d.hub ? 2 : 1.2)
    .attr('filter', d => `url(#glow-${d.type})`);

  // Labels — always visible
  nodeSel.append('text')
    .attr('dy', d => (d.r || 10) + 13)
    .attr('text-anchor', 'middle')
    .attr('fill', d => TYPE_COLOR[d.type] || '#FF9900')
    .attr('fill-opacity', d => d.hub ? 1 : 0.8)
    .attr('font-family', "'Share Tech Mono', monospace")
    .attr('font-size', d => d.hub ? '10px' : '9px')
    .attr('font-weight', d => d.hub ? 'bold' : 'normal')
    .attr('pointer-events', 'none')
    .text(d => d.label);

  // Force simulation — strong repulsion so clusters spread out clearly
  simulation = d3.forceSimulation(matrixNodes)
    .force('link', d3.forceLink(matrixLinks)
      .id(d => d.id)
      .distance(d => {
        const isHubLink = d.w >= 2;
        return isHubLink ? 160 : 100;
      })
      .strength(d => d.w >= 2 ? 0.7 : 0.4)
    )
    .force('charge', d3.forceManyBody()
      .strength(d => d.hub ? -600 : -200)
      .distanceMax(400)
    )
    .force('center', d3.forceCenter(0, 0))
    .force('collision', d3.forceCollide().radius(d => (d.r || 10) + 20).strength(0.8))
    .alphaDecay(0.02)
    .velocityDecay(0.3);

  // Curved path generator (quadratic bezier)
  function linkPath(d) {
    const dx = d.target.x - d.source.x;
    const dy = d.target.y - d.source.y;
    const dr = Math.sqrt(dx * dx + dy * dy);
    // cross-cluster links get a slight curve, hub links are straight
    const curve = d.w < 1 ? dr * 0.3 : 0;
    if (curve === 0) {
      return `M${d.source.x},${d.source.y}L${d.target.x},${d.target.y}`;
    }
    const mx = (d.source.x + d.target.x) / 2 - dy * (curve / dr);
    const my = (d.source.y + d.target.y) / 2 + dx * (curve / dr);
    return `M${d.source.x},${d.source.y}Q${mx},${my} ${d.target.x},${d.target.y}`;
  }

  simulation.on('tick', () => {
    linkSel.attr('d', linkPath);
    nodeSel.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`);
  });

  // Pulse core node
  setInterval(() => {
    nodeSel.filter(d => d.id === 'neural-core')
      .select('circle:last-of-type')
      .transition().duration(900)
      .attr('fill-opacity', 0.5)
      .transition().duration(900)
      .attr('fill-opacity', 0.25);
  }, 2200);

  // Click on background to clear highlights
  matrixSvg.on('click', () => clearHighlight(nodeSel, linkSel));

  // Legend
  addMatrixLegend(matrixSvg, w);
}

function highlightNode(d, nodeSel, linkSel) {
  const connectedIds = new Set([d.id]);
  matrixLinks.forEach(l => {
    const sid = typeof l.source === 'object' ? l.source.id : l.source;
    const tid = typeof l.target === 'object' ? l.target.id : l.target;
    if (sid === d.id) connectedIds.add(tid);
    if (tid === d.id) connectedIds.add(sid);
  });

  nodeSel.select('circle:last-of-type')
    .attr('fill-opacity', n => connectedIds.has(n.id) ? 0.5 : 0.05)
    .attr('stroke-opacity', n => connectedIds.has(n.id) ? 1 : 0.1);
  nodeSel.select('text')
    .attr('fill-opacity', n => connectedIds.has(n.id) ? 1 : 0.15);
  linkSel
    .attr('stroke-opacity', l => {
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      return (s === d.id || t === d.id) ? 1 : 0.05;
    });
}

function clearHighlight(nodeSel, linkSel) {
  nodeSel.select('circle:last-of-type')
    .attr('fill-opacity', d => d.hub ? 0.25 : 0.15)
    .attr('stroke-opacity', 1);
  nodeSel.select('text')
    .attr('fill-opacity', d => d.hub ? 1 : 0.8);
  linkSel
    .attr('stroke-opacity', d => d.w > 2 ? 0.9 : d.w > 1 ? 0.7 : 0.45);
}

function addMatrixLegend(svg, w) {
  const entries = [
    ['core',      '#FF9900', 'ROOT FOLDER'],
    ['folder',    '#FC6600', 'FOLDER'],
    ['memory',    '#FFCC00', '.md / .db'],
    ['skill',     '#99AAFF', '.py / .js / .html'],
    ['knowledge', '#44FF99', '.txt'],
    ['system',    '#CC88FF', '.yaml / .env / .json'],
    ['audio',     '#FF88CC', '.mp3 / .wav'],
  ];

  const leg = svg.append('g')
    .attr('transform', 'translate(12, 12)')
    .attr('pointer-events', 'none');

  leg.append('rect')
    .attr('width', 160).attr('height', entries.length * 18 + 14)
    .attr('rx', 4).attr('fill', 'rgba(2,2,8,0.85)')
    .attr('stroke', '#1a1a2a').attr('stroke-width', 1);

  entries.forEach(([type, color, label], i) => {
    const row = leg.append('g').attr('transform', `translate(8, ${i * 18 + 12})`);
    row.append('circle').attr('r', 4).attr('fill', color).attr('fill-opacity', 0.3)
      .attr('stroke', color).attr('cx', 4).attr('cy', 1);
    row.append('text')
      .attr('x', 14).attr('y', 5)
      .attr('fill', color).attr('font-size', '9px')
      .attr('font-family', "'Share Tech Mono', monospace")
      .text(label);
  });
}

function renderMiniMatrix() {
  const container = document.getElementById('mini-matrix');
  if (!container) return;
  container.innerHTML = '';
  // Mark the placeholder render so the fullscreen button knows to attach
  // itself here too (called once at the end of this function).
  const w = container.clientWidth || 180;
  const h = container.clientHeight || 100;

  const miniNodes = matrixNodes.filter(n => n.hub || n.id === 'neural-core').map(n => ({...n}));
  const ids = new Set(miniNodes.map(n => n.id));
  const miniLinks = SEED_LINKS.filter(l => ids.has(l.source) && ids.has(l.target) && l.w >= 2).map(l => ({...l}));

  const svg = d3.select('#mini-matrix').append('svg').attr('width', '100%').attr('height', '100%').style('background', '#020208');
  const canvas = svg.append('g');

  const zoom = d3.zoom().scaleExtent([0.2, 8]).on('zoom', e => canvas.attr('transform', e.transform));
  svg.call(zoom).on('dblclick.zoom', null);

  const linkSel = canvas.append('g').selectAll('line')
    .data(miniLinks).enter().append('line')
    .attr('stroke', '#2a2a4a').attr('stroke-width', 1).attr('stroke-opacity', 0.7);

  const nodeSel = canvas.append('g').selectAll('g')
    .data(miniNodes).enter().append('g')
    .style('cursor', 'pointer')
    .on('click', (event, d) => { event.stopPropagation(); _handleMiniNodeClick(d); });

  // Tooltip — tells the Captain the node is clickable and what will happen.
  nodeSel.append('title').text(d => d.path ? `Open: ${d.path}` : `${d.label} — expand full Neural Matrix`);

  // Larger transparent hit target — the rendered circle is tiny so we widen
  // the clickable area so it's not fiddly to hit while the simulation moves.
  nodeSel.append('circle')
    .attr('class', 'hit-target')
    .attr('r', d => Math.max(3, (d.r || 10) * 0.4) + 10)
    .attr('fill', 'transparent');

  nodeSel.append('circle')
    .attr('r', d => Math.max(3, (d.r || 10) * 0.4))
    .attr('fill', d => TYPE_COLOR[d.type]).attr('fill-opacity', 0.4)
    .attr('stroke', d => TYPE_COLOR[d.type]).attr('stroke-width', 0.8)
    .attr('pointer-events', 'none');   // hit-target above already catches clicks

  nodeSel.append('text')
    .attr('dy', d => Math.max(3, (d.r || 10) * 0.4) + 9)
    .attr('text-anchor', 'middle')
    .attr('fill', d => TYPE_COLOR[d.type])
    .attr('fill-opacity', 0.85)
    .attr('font-family', "'Share Tech Mono', monospace")
    .attr('font-size', '7px')
    .attr('pointer-events', 'none')
    .text(d => d.label.length > 12 ? d.label.slice(0, 11) + '…' : d.label);

  const sim = d3.forceSimulation(miniNodes)
    .force('link', d3.forceLink(miniLinks).id(d => d.id).distance(70).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-220))
    .force('center', d3.forceCenter(w / 2, h / 2))
    .force('collide', d3.forceCollide(d => Math.max(3, (d.r || 10) * 0.4) + 22));

  sim.on('tick', () => {
    linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
           .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`);
  });

  // Fullscreen toggle is available in the placeholder render too, so the
  // Captain can blow up the matrix even before a project is loaded.
  _addMiniMatrixFullscreenBtn(container);
}

function _miniFileType(name) {
  const ext = name.slice(name.lastIndexOf('.')).toLowerCase();
  const MAP = {
    '.md': 'memory', '.db': 'memory',
    '.txt': 'knowledge',
    '.py': 'skill', '.js': 'skill', '.html': 'skill', '.css': 'skill',
    '.yaml': 'system', '.yml': 'system', '.env': 'system', '.json': 'system',
    '.mp3': 'audio', '.wav': 'audio', '.rpp': 'audio',
    '.zip': 'archive',
  };
  return MAP[ext] || 'file';
}

// Most-recent inputs to renderProjectMiniMatrix — used by the fullscreen
// toggle so we can re-run with the new container dimensions and let the
// force layout spread the nodes out for the bigger viewport.
let _lastMiniProjectNodes = null;
let _lastMiniRootPath = '';

// Unified click action for any mini-matrix node, in either renderer.
// Files / folders open in Explorer. Structural hubs / category nodes (no path)
// expand into the full Neural Matrix panel for proper exploration. If the
// mini is in fullscreen mode, exit it first so the destination panel is visible.
function _handleMiniNodeClick(d) {
  if (d && d.path) { openPath(d.path); return; }
  const c = document.getElementById('mini-matrix');
  if (c && c.classList.contains('mini-matrix-fullscreen')) {
    c.classList.remove('mini-matrix-fullscreen');
  }
  showPanel('matrix');
}

function toggleMiniMatrixFullscreen() {
  const c = document.getElementById('mini-matrix');
  if (!c) return;
  c.classList.toggle('mini-matrix-fullscreen');
  if (_lastMiniProjectNodes) {
    renderProjectMiniMatrix(_lastMiniProjectNodes, _lastMiniRootPath);
  }
}

function _addMiniMatrixFullscreenBtn(container) {
  const btn = document.createElement('button');
  btn.className = 'mini-matrix-fullscreen-btn';
  const isFull = container.classList.contains('mini-matrix-fullscreen');
  btn.textContent = isFull ? '✕ EXIT' : '⛶';
  btn.title = isFull ? 'Exit fullscreen' : 'Fullscreen project matrix';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleMiniMatrixFullscreen();
  });
  container.appendChild(btn);
}

function renderProjectMiniMatrix(projectNodes, rootPath) {
  const container = document.getElementById('mini-matrix');
  if (!container) return;
  _lastMiniProjectNodes = projectNodes;
  _lastMiniRootPath = rootPath || '';
  container.innerHTML = '';
  // Use the *live* container size so fullscreen mode gets the full viewport.
  const w = container.clientWidth || 180;
  const h = container.clientHeight || 100;

  if (!projectNodes || !projectNodes.length) { renderMiniMatrix(); _addMiniMatrixFullscreenBtn(container); return; }

  // Limit to depth ≤ 2, cap at 80 nodes
  const filtered = projectNodes.filter(n => n.depth <= 2).slice(0, 80);

  // Synthetic root node
  const rootName = (rootPath || '').split(/[/\\]/).pop() || 'PROJECT';
  const nodes = [
    { id: '__root__', label: rootName, type: 'core', r: 7, hub: true, path: rootPath },
    ...filtered.map((n, i) => ({
      id: `p-${i}`,
      label: n.name,
      type: n.type === 'dir' ? 'folder' : _miniFileType(n.name),
      r: n.type === 'dir' ? 5 : 3,
      hub: n.type === 'dir',
      path: n.path,
      depth: n.depth,
    })),
  ];

  // Build links: for each node, find closest ancestor in the array
  const links = [];
  for (let i = 1; i < nodes.length; i++) {
    const d = nodes[i].depth;
    if (d === 1) {
      links.push({ source: '__root__', target: nodes[i].id, w: 1.5 });
    } else {
      for (let j = i - 1; j >= 1; j--) {
        if (nodes[j].depth === d - 1) {
          links.push({ source: nodes[j].id, target: nodes[i].id, w: 1 });
          break;
        }
      }
    }
  }

  const svg = d3.select('#mini-matrix').append('svg')
    .attr('width', '100%').attr('height', '100%')
    .style('background', '#020208');
  const canvas = svg.append('g');

  const zoom = d3.zoom().scaleExtent([0.2, 8]).on('zoom', e => canvas.attr('transform', e.transform));
  svg.call(zoom).on('dblclick.zoom', null);

  const linkSel = canvas.append('g').selectAll('line')
    .data(links).enter().append('line')
    .attr('stroke', '#2a2a4a').attr('stroke-width', 0.8).attr('stroke-opacity', 0.7);

  const nodeSel = canvas.append('g').selectAll('g')
    .data(nodes).enter().append('g')
    .style('cursor', 'pointer')
    .on('click', (event, d) => { event.stopPropagation(); _handleMiniNodeClick(d); });

  // Native SVG tooltip — hovering a node tells the Captain it's clickable
  // and shows the full path, since the rendered label is truncated to ~14 chars.
  nodeSel.append('title').text(d => d.path ? `Open: ${d.path}` : `${d.label} — expand full Neural Matrix`);

  // Larger transparent hit target so file nodes (r=3) aren't fiddly to click
  // while the simulation is moving them around.
  nodeSel.append('circle')
    .attr('class', 'hit-target')
    .attr('r', d => d.r + 6)
    .attr('fill', 'transparent')
    .attr('stroke', 'none');

  nodeSel.append('circle')
    .attr('r', d => d.r)
    .attr('fill', d => TYPE_COLOR[d.type] || '#FF9900')
    .attr('fill-opacity', 0.4)
    .attr('stroke', d => TYPE_COLOR[d.type] || '#FF9900')
    .attr('stroke-width', 0.8)
    .attr('pointer-events', 'none');   // hit-target above already catches clicks

  nodeSel.append('text')
    .attr('dy', d => d.r + 9)
    .attr('text-anchor', 'middle')
    .attr('fill', d => TYPE_COLOR[d.type] || '#FF9900')
    .attr('fill-opacity', 0.85)
    .attr('font-family', "'Share Tech Mono', monospace")
    .attr('font-size', d => d.hub ? '7px' : '6px')
    .attr('pointer-events', 'none')
    .text(d => d.label.length > 14 ? d.label.slice(0, 13) + '…' : d.label);

  // Drop the fullscreen toggle on top of the SVG, after rendering so it
  // survives the container.innerHTML = '' at the top of this function.
  _addMiniMatrixFullscreenBtn(container);

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(55).strength(0.5))
    .force('charge', d3.forceManyBody().strength(d => d.hub ? -180 : -80))
    .force('center', d3.forceCenter(w / 2, h / 2))
    .force('collide', d3.forceCollide(d => d.r + 22));

  sim.on('tick', () => {
    linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
           .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`);
  });
}

function resetMatrix() {
  playDataSound('engage');
  matrixNodes.forEach(n => { delete n.fx; delete n.fy; delete n.x; delete n.y; delete n.vx; delete n.vy; });
  initMatrix();
}

function zoomMatrix(factor) {
  if (!matrixSvg || !matrixZoom) return;
  matrixSvg.transition().duration(300).call(matrixZoom.scaleBy, factor);
}

function fitMatrix() {
  if (!matrixSvg || !matrixZoom) return;
  const container = document.getElementById('matrix-graph');
  const w = container.clientWidth;
  const h = container.clientHeight;
  matrixSvg.transition().duration(500)
    .call(matrixZoom.transform, d3.zoomIdentity.translate(w / 2, h / 2).scale(0.7));
}

function addMemoryNode() {
  const label = prompt('New memory node label:');
  if (!label) return;
  const id = `mem-${Date.now()}`;
  matrixNodes.push({ id, label: label.toUpperCase(), type: 'memory', r: 9 });
  matrixLinks.push({ source: 'memory-banks', target: id, w: 1.2 });
  document.getElementById('node-count').textContent = `${matrixNodes.length} NODES`;
  document.getElementById('edge-count').textContent = `${matrixLinks.length} CONNECTIONS`;
  renderMainMatrix();
  addLog(`Memory node added: ${label}`);
}

let forceEnabled = true;
function toggleForce() {
  playDataSound('confirm');
  forceEnabled = !forceEnabled;
  if (simulation) {
    if (forceEnabled) simulation.alpha(0.3).restart();
    else simulation.stop();
  }
}

// ═══════════════════════════════════════════════════════════
// SKILLS TREE
// ═══════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════
// STANDING ORDERS — recurring duty roster (cron jobs)
// ═══════════════════════════════════════════════════════════
let _editingOrderId = null;

async function refreshStandingOrders() {
  const list = document.getElementById('orders-list');
  if (!list) return;
  list.innerHTML = '<div class="orders-empty">LOADING...</div>';
  try {
    const res = await fetch(`${API_BASE}/standing_orders`);
    const data = await res.json();
    const orders = data.orders || [];
    const countEl = document.getElementById('orders-count');
    if (countEl) countEl.textContent = `${orders.length} ORDER${orders.length === 1 ? '' : 'S'}`;
    if (!orders.length) {
      list.innerHTML = '<div class="orders-empty">NO STANDING ORDERS. Press + NEW ORDER or ask Data to add one.</div>';
      return;
    }
    list.innerHTML = '';
    for (const o of orders) {
      const row = document.createElement('div');
      row.className = 'order-row' + (o.enabled ? '' : ' disabled');
      const nextTxt = o.next_run ? `NEXT ${new Date(o.next_run * 1000).toLocaleString()}` : '';
      row.innerHTML = `
        <div class="order-info">
          <div class="order-name">${escapeHtml(o.name)}</div>
          <div class="order-prompt">${escapeHtml(o.prompt)}</div>
        </div>
        <div class="order-cron">${escapeHtml(o.cron)}</div>
        <div class="order-next">${nextTxt}</div>
        <div class="order-actions">
          <button class="order-btn" data-action="toggle">${o.enabled ? 'PAUSE' : 'ENABLE'}</button>
          <button class="order-btn" data-action="run">RUN NOW</button>
          <button class="order-btn" data-action="edit">EDIT</button>
          <button class="order-btn danger" data-action="delete">DELETE</button>
        </div>
      `;
      row.querySelector('[data-action="toggle"]').onclick = () => _toggleStandingOrder(o.id, !o.enabled);
      row.querySelector('[data-action="run"]').onclick    = () => _runStandingOrderNow(o.id);
      row.querySelector('[data-action="edit"]').onclick   = () => openNewStandingOrderDialog(o);
      row.querySelector('[data-action="delete"]').onclick = () => _deleteStandingOrder(o.id, o.name);
      list.appendChild(row);
    }
  } catch (e) {
    list.innerHTML = `<div class="orders-empty">ERROR: ${escapeHtml(e.message || String(e))}</div>`;
  }
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function openNewStandingOrderDialog(existing) {
  _editingOrderId = existing?.id || null;
  document.getElementById('orders-dialog-title').textContent =
    existing ? `EDIT ORDER — ${existing.name}` : 'NEW STANDING ORDER';
  document.getElementById('orders-field-name').value     = existing?.name    || '';
  document.getElementById('orders-field-cron').value     = existing?.cron    || '0 8 * * *';
  document.getElementById('orders-field-prompt').value   = existing?.prompt  || '';
  document.getElementById('orders-field-enabled').checked = existing ? !!existing.enabled : true;
  document.getElementById('orders-field-notify').checked  = existing ? !!existing.notify_telegram : false;
  const provSel = document.getElementById('orders-field-provider');
  provSel.innerHTML = (_providersCache.length ? _providersCache : [{ id: 'claude-cli', label: 'claude-cli', available: true }])
    .map(p => `<option value="${p.id}"${(existing?.provider || _lastKnownActiveProvider) === p.id ? ' selected' : ''}${p.available ? '' : ' disabled'}>${p.available ? p.label : p.label + ' — n/a'}</option>`)
    .join('');
  document.getElementById('orders-dialog').classList.remove('hidden');
}

function closeStandingOrderDialog() {
  document.getElementById('orders-dialog').classList.add('hidden');
  _editingOrderId = null;
}

async function saveStandingOrderDialog() {
  const payload = {
    name:            document.getElementById('orders-field-name').value.trim(),
    cron:            document.getElementById('orders-field-cron').value.trim(),
    prompt:          document.getElementById('orders-field-prompt').value.trim(),
    provider:        document.getElementById('orders-field-provider').value,
    enabled:         document.getElementById('orders-field-enabled').checked,
    notify_telegram: document.getElementById('orders-field-notify').checked,
  };
  if (!payload.name || !payload.cron || !payload.prompt) {
    addLog('Standing order: name, cron, and prompt are required.');
    return;
  }
  const url = _editingOrderId
    ? `${API_BASE}/standing_orders/${encodeURIComponent(_editingOrderId)}`
    : `${API_BASE}/standing_orders`;
  try {
    const res = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) { addLog('Standing order save failed: ' + data.error); return; }
    addLog(`Standing order ${_editingOrderId ? 'updated' : 'created'}: ${payload.name}`);
    closeStandingOrderDialog();
    refreshStandingOrders();
  } catch (e) {
    addLog('Standing order save error: ' + e.message);
  }
}

async function _toggleStandingOrder(id, enabled) {
  await fetch(`${API_BASE}/standing_orders/${encodeURIComponent(id)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  refreshStandingOrders();
}

async function _runStandingOrderNow(id) {
  addLog('Running standing order now...');
  await fetch(`${API_BASE}/standing_orders/${encodeURIComponent(id)}/run`, { method: 'POST' });
  refreshStandingOrders();
}

async function _deleteStandingOrder(id, name) {
  if (!confirm(`Delete standing order "${name}"?`)) return;
  await fetch(`${API_BASE}/standing_orders/${encodeURIComponent(id)}/delete`, { method: 'POST' });
  addLog(`Standing order deleted: ${name}`);
  refreshStandingOrders();
}

// ═══════════════════════════════════════════════════════════
// SKILLS PANEL (legacy — page removed from nav, function kept for safety)
// ═══════════════════════════════════════════════════════════

let skillsInitialized = false;

async function initSkills() {
  skillsInitialized = true;
  const container = document.getElementById('skills-tree');
  container.innerHTML = '<div class="memory-loading">SCANNING NEURAL SUBROUTINES...</div>';

  let toolData = null, cliData = null;
  try {
    const [r1, r2] = await Promise.all([
      fetch(`${API_BASE}/skills`),
      fetch(`${API_BASE}/skills-full`),
    ]);
    toolData = await r1.json();
    cliData  = await r2.json();
  } catch (e) {
    container.innerHTML = '<div class="memory-loading">BRIDGE OFFLINE — CANNOT LOAD SKILLS</div>';
    return;
  }

  container.innerHTML = '';

  // ── Controls bar ───────────────────────────────────────
  const controls = document.createElement('div');
  controls.className = 'matrix-controls';
  const btnFolder  = document.createElement('button');
  btnFolder.className = 'data-btn-sm orange';
  btnFolder.textContent = '📂 SKILLS FOLDER';
  btnFolder.onclick = () => openPath(toolData.skills_dir || '');
  const btnBridge  = document.createElement('button');
  btnBridge.className = 'data-btn-sm yellow';
  btnBridge.textContent = '⚙️ BRIDGE CODE';
  btnBridge.onclick = () => openPath(toolData.bridge_path || '');
  const btnRefresh = document.createElement('button');
  btnRefresh.className = 'data-btn-sm blue';
  btnRefresh.textContent = '↺ REFRESH';
  btnRefresh.onclick = () => initSkills();
  controls.append(btnFolder, btnBridge, btnRefresh);
  container.appendChild(controls);

  // ── Section: API Mode Tools ────────────────────────────
  const apiHeader = document.createElement('div');
  apiHeader.className = 'skills-section-header';
  apiHeader.innerHTML = '<span class="pill orange" style="font-size:11px;padding:3px 10px;">API MODE</span> ACTIVE BRIDGE TOOLS';
  container.appendChild(apiHeader);

  const toolList = toolData.tools || [];
  const apiCats = {};
  toolList.forEach(t => {
    if (!apiCats[t.category]) apiCats[t.category] = [];
    apiCats[t.category].push(t);
  });
  const apiGrid = document.createElement('div');
  apiGrid.className = 'skills-grid';
  let delay = 0;
  Object.entries(apiCats).forEach(([cat, tools]) => {
    const h = document.createElement('div');
    h.className = 'skill-category-header';
    h.textContent = `◆ ${cat}`;
    apiGrid.appendChild(h);
    tools.forEach(tool => {
      const card = document.createElement('div');
      card.className = 'skill-card';
      card.style.animationDelay = `${delay}ms`;
      card.innerHTML = `<div class="skill-icon">${tool.icon}</div>
        <div class="skill-name">${tool.display}</div>
        <div class="skill-desc">${tool.desc}</div>`;
      card.onclick = () => {
        showPanel('chat');
        document.getElementById('chat-input').value = `Tell me about your ${tool.display} capability and how I can use it.`;
        setTimeout(sendMessage, 100);
      };
      apiGrid.appendChild(card);
      delay += 40;
    });
  });
  container.appendChild(apiGrid);

  // ── Section: CLI Skill Library ─────────────────────────
  const cliHeader = document.createElement('div');
  cliHeader.className = 'skills-section-header';
  cliHeader.style.marginTop = '24px';
  const categories = cliData.categories || [];
  const totalCli = categories.reduce((s, c) => s + c.skill_count, 0);
  cliHeader.innerHTML = `<span class="pill blue" style="font-size:11px;padding:3px 10px;">CLI MODE</span> HERMES SKILL LIBRARY — ${categories.length} CATEGORIES · ${totalCli} SKILLS`;
  container.appendChild(cliHeader);

  const cliContainer = document.createElement('div');
  cliContainer.className = 'cli-skills-container';

  categories.forEach(cat => {
    const catBlock = document.createElement('div');
    catBlock.className = 'cli-category-block';

    const catHeader = document.createElement('div');
    catHeader.className = 'cli-category-header';
    catHeader.innerHTML = `
      <span class="cli-cat-name">◆ ${cat.display.toUpperCase()}</span>
      <span class="cli-cat-count">${cat.skill_count} skills</span>
      <span class="cli-cat-chevron">▶</span>`;
    catBlock.appendChild(catHeader);

    const skillList = document.createElement('div');
    skillList.className = 'cli-skill-list hidden';
    cat.skills.forEach(skill => {
      const row = document.createElement('div');
      row.className = 'cli-skill-row';
      row.innerHTML = `
        <span class="cli-skill-name">${skill.display}</span>
        <span class="cli-skill-desc">${skill.description || '—'}</span>
        ${skill.has_skill_md ? '<span class="cli-skill-badge">SKILL.MD</span>' : ''}`;
      row.onclick = () => openSkillViewer(cat.name, skill);
      skillList.appendChild(row);
    });
    catBlock.appendChild(skillList);

    catHeader.onclick = () => {
      const open = !skillList.classList.contains('hidden');
      skillList.classList.toggle('hidden', open);
      catHeader.querySelector('.cli-cat-chevron').textContent = open ? '▶' : '▼';
      catBlock.classList.toggle('open', !open);
    };

    cliContainer.appendChild(catBlock);
  });
  container.appendChild(cliContainer);

  const totalTools = toolList.length;
  document.getElementById('skill-count').textContent = `${totalTools} TOOLS · ${totalCli} CLI`;
  document.getElementById('skill-pct').textContent = totalTools;
  addLog(`Skills loaded: ${totalTools} API tools, ${totalCli} CLI skills`);
}

// ── Skill viewer / editor ─────────────────────────────────
let _skillViewerCat = '', _skillViewerName = '';

async function openSkillViewer(category, skill) {
  const viewer = document.getElementById('skill-viewer');
  const title  = document.getElementById('skill-viewer-title');
  const content = document.getElementById('skill-viewer-content');
  const textarea = document.getElementById('skill-viewer-editor');
  const saveBtn  = document.getElementById('skill-viewer-save');
  const editBtn  = document.getElementById('skill-viewer-edit');

  title.textContent = `◆ ${category.toUpperCase()} / ${skill.display.toUpperCase()}`;
  content.textContent = 'LOADING...';
  textarea.classList.add('hidden');
  saveBtn.classList.add('hidden');
  editBtn.textContent = 'EDIT';
  viewer.classList.remove('hidden');
  document.getElementById('skills-tree').style.display = 'none';

  _skillViewerCat  = category;
  _skillViewerName = skill.name;

  if (!skill.has_skill_md) {
    content.textContent = '(No skill.md found for this skill)';
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/skill-content?category=${encodeURIComponent(category)}&name=${encodeURIComponent(skill.name)}`);
    const data = await res.json();
    content.textContent = data.content || '(empty)';
    textarea.value = data.content || '';
  } catch (e) {
    content.textContent = 'Error loading skill content.';
  }
}

function closeSkillViewer() {
  document.getElementById('skill-viewer').classList.add('hidden');
  document.getElementById('skills-tree').style.display = '';
}

function toggleSkillEdit() {
  const content  = document.getElementById('skill-viewer-content');
  const textarea = document.getElementById('skill-viewer-editor');
  const saveBtn  = document.getElementById('skill-viewer-save');
  const editBtn  = document.getElementById('skill-viewer-edit');
  const editing  = !textarea.classList.contains('hidden');
  if (editing) {
    // Switch back to view
    content.textContent = textarea.value;
    content.classList.remove('hidden');
    textarea.classList.add('hidden');
    saveBtn.classList.add('hidden');
    editBtn.textContent = 'EDIT';
  } else {
    // Switch to edit
    textarea.value = content.textContent;
    content.classList.add('hidden');
    textarea.classList.remove('hidden');
    saveBtn.classList.remove('hidden');
    editBtn.textContent = 'CANCEL';
  }
}

async function saveSkillContent() {
  const textarea = document.getElementById('skill-viewer-editor');
  const content  = document.getElementById('skill-viewer-content');
  const saveBtn  = document.getElementById('skill-viewer-save');
  saveBtn.textContent = 'SAVING...';
  saveBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/skill-save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: _skillViewerCat, name: _skillViewerName, content: textarea.value }),
    });
    const data = await res.json();
    if (data.saved) {
      content.textContent = textarea.value;
      content.classList.remove('hidden');
      textarea.classList.add('hidden');
      saveBtn.classList.add('hidden');
      document.getElementById('skill-viewer-edit').textContent = 'EDIT';
      addLog(`Skill saved: ${_skillViewerCat}/${_skillViewerName}`);
    } else {
      addLog('Save failed: ' + (data.error || 'unknown error'));
    }
  } catch (e) {
    addLog('Save error: ' + e.message);
  }
  saveBtn.textContent = 'SAVE';
  saveBtn.disabled = false;
}

// ═══════════════════════════════════════════════════════════
// MEMORY PANEL
// ═══════════════════════════════════════════════════════════

let memoryInitialized = false;

const MEMORY_DATA = [
  {
    section: 'IDENTITY',
    entries: [
      'Designation: DATA — Dashboard for Analytical Thought and Action',
      'Role: self-hosted AI operations dashboard',
      'Primary directive: Assist the Captain with rigorous, complete work',
    ]
  },
  {
    section: 'CAPTAIN PROFILE',
    entries: [
      'Address as: Captain (primary), Sir (alternate)',
      'Prefers concise, actionable responses',
      'Profile and preferences live in persistent memory',
    ]
  },
  {
    section: 'OPERATING PROTOCOLS',
    entries: [
      'Verify before answering — read real files, run real commands',
      'Reference past sessions via the Memory Banks recall index',
      'Say plainly what is known and what is not',
    ]
  },
  {
    section: 'SYSTEM CONFIGURATION',
    entries: [
      'LLM: your configured provider (Claude CLI by default)',
      'Memory: per-user persistent memory + searchable archive',
      'Crew: Data (the main computer) + 10 specialist agents it can summon',
    ]
  },
];

function initMemory() {
  memoryInitialized = true;
  const container = document.getElementById('memory-content');
  container.innerHTML = '';

  MEMORY_DATA.forEach(section => {
    const title = document.createElement('div');
    title.className = 'memory-section-title';
    title.textContent = `◆ ${section.section}`;
    container.appendChild(title);

    section.entries.forEach(entry => {
      const el = document.createElement('div');
      el.className = 'memory-entry';
      el.textContent = `▸ ${entry}`;
      container.appendChild(el);
    });
  });

  // Try to load live memory from bridge
  fetch(`${API_BASE}/memory`)
    .then(r => r.json())
    .then(data => {
      if (data.content) {
        const liveTitle = document.createElement('div');
        liveTitle.className = 'memory-section-title';
        liveTitle.textContent = '◆ LIVE NEURAL LOGS';
        container.appendChild(liveTitle);

        const pre = document.createElement('div');
        pre.className = 'memory-entry';
        pre.style.whiteSpace = 'pre-wrap';
        pre.textContent = data.content;
        container.appendChild(pre);
      }
    })
    .catch(() => {/* bridge offline, local data only */});
}

// ═══════════════════════════════════════════════════════════
// MATRIX SUB-TABS
// ═══════════════════════════════════════════════════════════

let currentMatrixTab = 'docs';
let computerInitialized = false;
let computerStack = [];  // directory history for "UP" navigation
let computerSvg = null, computerZoom = null, computerSimulation = null;

function switchMatrixTab(tab) {
  playDataSound('confirm');
  currentMatrixTab = tab;
  document.querySelectorAll('.matrix-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.matrix-subpanel').forEach(p => p.classList.remove('active'));
  document.getElementById(`mtab-${tab}`)?.classList.add('active');
  document.getElementById(`msub-${tab}`)?.classList.add('active');

  const titles = {
    docs:     'COMPUTER CORES — DOCUMENTS · PROJECT LAUNCHER',
    graph:    'COMPUTER CORES — NEURAL MATRIX',
    computer: 'COMPUTER CORES — SHIP\'S COMPUTER',
  };
  let title = titles[tab];
  if (!title && tab.startsWith('ws')) {
    const wsId = parseInt(tab.slice(2));
    const ws = _workspaces.get(wsId);
    title = ws ? `COMPUTER CORES — ${ws.name.toUpperCase()}` : 'COMPUTER CORES — PROJECT';
  }
  document.getElementById('matrix-panel-title').textContent = title || 'COMPUTER CORES';

  if (tab === 'docs') loadDocsProjects();
  if (tab === 'computer' && !computerInitialized) initShipsComputer();
  // The Neural Matrix graph is often first built while its tab is
  // hidden, so its force layout settles unseen. Re-seed node positions and
  // redraw on entry so the nodes visibly fan out, like the other graphs.
  if (tab === 'graph' && matrixNodes.length) {
    matrixNodes.forEach(n => { delete n.fx; delete n.fy; delete n.x; delete n.y; delete n.vx; delete n.vy; });
    renderMainMatrix();
  }
}


// ── DOCUMENTS — project launcher (default matrix tab) ────────
// Lists the project folders under the Captain's Documents directory as
// clickable cards. Selecting one opens a fresh workspace tab rooted in
// that folder plus a new com window — via openProjectWorkspace().
// Empty = let the bridge fall back to its project dir (the DATA install folder).
const DOCS_ROOT = '';
let docsLoaded = false;

async function loadDocsProjects(force = false) {
  if (docsLoaded && !force) return;
  const grid = document.getElementById('docs-project-grid');
  if (!grid) return;
  grid.innerHTML = '<div class="docs-empty">SCANNING DOCUMENTS…</div>';
  try {
    const res = await fetch(`${API_BASE}/files?dir=${encodeURIComponent(DOCS_ROOT)}&depth=1`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // Show the actual scan root the bridge resolved (defaults to ~/Documents)
    const pathLbl = document.getElementById('docs-path-label');
    if (pathLbl && data.root) pathLbl.textContent = data.root;
    const folders = (data.nodes || [])
      .filter(n => n.type === 'folder' && n.depth === 1)
      .sort((a, b) => a.label.toLowerCase().localeCompare(b.label.toLowerCase()));
    if (!folders.length) {
      grid.innerHTML = '<div class="docs-empty">No project folders found.</div>';
      return;
    }
    grid.innerHTML = '';
    folders.forEach(f => {
      const card = document.createElement('button');
      card.className = 'docs-project-card';
      card.title = f.path;
      card.innerHTML = `
        <span class="docs-card-name"><span class="docs-card-icon">▣</span>${escapeHtml(f.label)}</span>
        <span class="docs-card-path">${escapeHtml(f.path)}</span>`;
      card.addEventListener('click', () => openDocsProject(f.path, f.label));
      grid.appendChild(card);
    });
    docsLoaded = true;
    addLog(`Documents: ${folders.length} projects available`);
  } catch (e) {
    grid.innerHTML = '<div class="docs-empty">Could not scan Documents — bridge offline?</div>';
    addLog('Documents scan failed: ' + e.message);
  }
}


// -- DOCUMENTS -- view toggle: card grid <-> node graph -----
// The card grid stays the default. The graph is the same force-directed
// style as the Neural Matrix / Ship's Computer graphs: a central
// DOCUMENTS hub with every project folder fanned out around it.
let docsView = 'grid';
let docsSvg = null, docsZoom = null, docsSimulation = null;

function toggleDocsView() {
  docsView = (docsView === 'grid') ? 'graph' : 'grid';
  playDataSound('confirm');
  const grid  = document.getElementById('docs-project-grid');
  const graph = document.getElementById('docs-graph');
  const btn   = document.getElementById('docs-view-toggle');
  if (docsView === 'graph') {
    if (grid)  grid.style.display  = 'none';
    if (graph) graph.style.display = '';
    if (btn)   btn.textContent = 'VIEW: GRAPH';
    renderDocsGraph();
  } else {
    if (graph) graph.style.display = 'none';
    if (grid)  grid.style.display  = '';
    if (btn)   btn.textContent = 'VIEW: GRID';
  }
}

async function renderDocsGraph() {
  const container = document.getElementById('docs-graph');
  if (!container) return;
  container.innerHTML = '<div class="docs-empty">MAPPING DOCUMENTS…</div>';

  let folders = [];
  try {
    const res = await fetch(`${API_BASE}/files?dir=${encodeURIComponent(DOCS_ROOT)}&depth=1`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    folders = (data.nodes || [])
      .filter(n => n.type === 'folder' && n.depth === 1)
      .sort((a, b) => a.label.toLowerCase().localeCompare(b.label.toLowerCase()));
  } catch (e) {
    container.innerHTML = '<div class="docs-empty">Could not scan Documents — bridge offline?</div>';
    return;
  }
  if (!folders.length) {
    container.innerHTML = '<div class="docs-empty">No project folders found.</div>';
    return;
  }
  container.innerHTML = '';

  // Central DOCUMENTS hub + one node per project folder.
  const nodes = [{ id: '__docs_root__', label: 'DOCUMENTS', hub: true, r: 20 }];
  const links = [];
  folders.forEach((f, i) => {
    const id = `docnode-${i}`;
    nodes.push({ id, label: f.label, path: f.path, r: 11 });
    links.push({ source: '__docs_root__', target: id });
  });

  const W = container.clientWidth  || 900;
  const H = container.clientHeight || 500;

  docsSvg = d3.select(container).append('svg')
    .attr('width', '100%').attr('height', '100%')
    .style('background', '#050505');
  const g = docsSvg.append('g');
  docsZoom = d3.zoom().scaleExtent([0.15, 6])
    .on('zoom', e => g.attr('transform', e.transform));
  docsSvg.call(docsZoom).on('dblclick.zoom', null);

  docsSimulation = d3.forceSimulation(nodes)
    .force('link',   d3.forceLink(links).id(d => d.id).distance(d => d.source.hub ? 150 : 110))
    .force('charge', d3.forceManyBody().strength(d => d.hub ? -900 : -340).distanceMax(600))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide(d => (d.r || 10) + 20));

  const link = g.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', '#FF990033').attr('stroke-width', 1.4);

  const node = g.append('g').selectAll('g').data(nodes).join('g')
    .style('cursor', d => d.hub ? 'default' : 'pointer')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) docsSimulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) docsSimulation.alphaTarget(0); d.fx = null; d.fy = null; }))
    .on('click', (e, d) => {
      e.stopPropagation();
      if (!d.hub && d.path) openDocsProject(d.path, d.label);
    });

  node.append('circle')
    .attr('r', d => d.r || 10)
    .attr('fill', d => (d.hub ? '#FF9900' : '#FFCC00') + '22')
    .attr('stroke', d => d.hub ? '#FF9900' : '#FFCC00')
    .attr('stroke-width', 1.5);

  node.append('text')
    .attr('dy', d => (d.r || 10) + 12)
    .attr('text-anchor', 'middle')
    .attr('font-family', 'Share Tech Mono, monospace')
    .attr('font-size', d => d.hub ? '10px' : '8px')
    .attr('fill', d => d.hub ? '#FF9900' : '#9a9aae')
    .text(d => d.label.length > 18 ? d.label.slice(0, 16) + '…' : d.label);

  docsSimulation.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  addLog(`Documents graph: ${folders.length} projects mapped`);
}

function openDocsProject(path, name) {
  playDataSound('engage');
  addLog(`Opening project: ${name}`);
  // A pristine main channel (no project attached, nothing sent yet) adopts
  // the first DOCUMENTS click — the main window re-roots to that folder
  // instead of spawning a second pane. Once the main chat has been used or
  // a project already owns it, every click opens a new window as before.
  const adoptMain = !_mainProjectSet && !_mainChatUsed;
  openProjectWorkspace(path, { forceNewPane: !adoptMain });
}

// Refresh whichever matrix sub-tab is currently active.
async function refreshCurrentMatrix() {
  const tab = currentMatrixTab;
  playDataSound('engage');
  addLog(`Refreshing matrix: ${tab}`);
  if (tab === 'docs') {
    await loadDocsProjects(true);
    return;
  }
  if (tab === 'graph') {
    await initMatrix();
    return;
  }
  if (tab === 'computer') {
    const cur = computerStack[computerStack.length - 1] || COMPUTER_HOME;
    await loadComputerDir(cur);
    return;
  }
  if (tab.startsWith('ws')) {
    const wsId = parseInt(tab.slice(2));
    const ws = _workspaces.get(wsId);
    if (!ws) return;
    try {
      const res = await fetch(`${API_BASE}/project`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: ws.path }),
      });
      const data = await res.json();
      if (data.error) { addLog('Refresh failed: ' + data.error); return; }
      ws.projectNodes = data.nodes || [];
      loadProjectGraph(wsId, ws.path, ws.projectNodes);
      if (ws.isMain) renderProjectMiniMatrix(ws.projectNodes, ws.path);
    } catch (e) {
      addLog('Refresh error: ' + e.message);
    }
  }
}

// ── SHIP'S COMPUTER ──────────────────────────────────────────

const COMPUTER_HOME = 'THIS_PC';

async function initShipsComputer() {
  computerInitialized = true;
  await loadComputerDir(COMPUTER_HOME);
}

async function loadComputerDir(dirPath, highlightName = null) {
  const graphEl = document.getElementById('computer-graph');
  const label = dirPath === COMPUTER_HOME ? 'THIS PC' : dirPath;
  graphEl.innerHTML = `<div class="matrix-loading">
    <div class="loading-spinner"></div>
    <div>SCANNING ${escapeHtml(label)}...</div>
  </div>`;

  updateComputerBreadcrumb(dirPath);

  try {
    let data;
    if (dirPath === COMPUTER_HOME) {
      const res = await fetch(`${API_BASE}/drives`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      addLog(`Ship's Computer: This PC — ${data.nodes.length - 1} drives`);
    } else {
      const res = await fetch(`${API_BASE}/files?dir=${encodeURIComponent(dirPath)}&depth=1`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      addLog(`Ship's Computer: ${data.nodes.length} nodes in ${dirPath.split('\\').pop() || dirPath}`);
    }
    renderComputerGraph(data.nodes, data.links, dirPath, graphEl, highlightName);
  } catch (e) {
    graphEl.innerHTML = `<div class="matrix-loading">SCAN ERROR: ${escapeHtml(e.message)}</div>`;
  }
}

function updateComputerBreadcrumb(path) {
  const bc = document.getElementById('computer-breadcrumb');
  if (path === COMPUTER_HOME) {
    bc.textContent = '🖥 This PC';
    return;
  }
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
  bc.textContent = '🖥 This PC › ' + parts.join(' › ');
}

function computerUp() {
  if (computerStack.length > 1) {
    computerStack.pop();
    loadComputerDir(computerStack[computerStack.length - 1]);
  } else {
    // Already at drive root or one level deep — go to This PC
    computerStack = [COMPUTER_HOME];
    loadComputerDir(COMPUTER_HOME);
  }
}

function renderComputerGraph(nodes, links, currentDir, container, highlightName = null) {
  if (!computerStack.includes(currentDir)) computerStack.push(currentDir);

  container.innerHTML = '';
  const W = container.clientWidth || 900;
  const H = container.clientHeight || 500;

  const COLOR = {
    core:    '#FF9900', folder: '#FFCC00', file:    '#9999FF',
    memory:  '#CC88FF', skill:  '#44FF88', system:  '#4488FF',
    audio:   '#FF88AA', archive:'#FF4444', knowledge:'#99CCFF',
  };

  computerSvg = d3.select(container).append('svg')
    .attr('width', '100%').attr('height', '100%')
    .style('background', '#050505');

  const defs = computerSvg.append('defs');
  defs.append('filter').attr('id', 'comp-glow')
    .append('feGaussianBlur').attr('stdDeviation', '2.5').attr('result', 'blur');

  const g = computerSvg.append('g');
  computerZoom = d3.zoom().scaleExtent([0.1, 8])
    .on('zoom', e => g.attr('transform', e.transform));
  computerSvg.call(computerZoom);

  computerSimulation = d3.forceSimulation(nodes)
    .force('link',   d3.forceLink(links).id(d => d.id).distance(d => d.source.hub ? 160 : 100))
    .force('charge', d3.forceManyBody().strength(d => d.hub ? -800 : -300).distanceMax(600))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide(d => (d.r || 8) + 18));

  const link = g.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', '#222').attr('stroke-width', d => d.w || 1);

  const node = g.append('g').selectAll('g').data(nodes).join('g')
    .style('cursor', d => d.hub ? 'pointer' : 'default')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) computerSimulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) computerSimulation.alphaTarget(0); d.fx = null; d.fy = null; }))
    .on('click', (e, d) => {
      e.stopPropagation();
      if (d.hub && d.path) {
        loadComputerDir(d.path);
      } else if (!d.hub && d.path) {
        // File node — open the FILE itself in its default app, not its
        // parent folder. openPath() hits /open → os.startfile(path).
        openPath(d.path);
        addLog(`Opening: ${d.label}`);
      }
    });

  node.append('circle')
    .attr('r', d => d.r || 8)
    .attr('fill', d => (COLOR[d.type] || '#555') + '22')
    .attr('stroke', d => COLOR[d.type] || '#555')
    .attr('stroke-width', 1.5);

  node.append('text')
    .attr('dy', d => (d.r || 8) + 10)
    .attr('text-anchor', 'middle')
    .attr('font-family', 'Share Tech Mono, monospace')
    .attr('font-size', d => d.hub ? '9px' : '7px')
    .attr('fill', d => d.hub ? '#FFCC00' : '#666')
    .text(d => d.label.length > 16 ? d.label.slice(0, 14) + '…' : d.label);

  computerSimulation.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // Fit after settling, then highlight target node if requested
  setTimeout(() => {
    const bounds = g.node().getBBox();
    if (bounds.width && bounds.height) {
      const scale = Math.min(0.85, Math.min(W / bounds.width, H / bounds.height));
      const tx = W/2 - scale*(bounds.x + bounds.width/2);
      const ty = H/2 - scale*(bounds.y + bounds.height/2);
      computerSvg.transition().duration(600)
        .call(computerZoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }

    if (!highlightName) return;
    const targetNode = nodes.find(n =>
      n.label === highlightName ||
      (n.path && n.path.split(/[/\\]/).pop() === highlightName)
    );
    if (!targetNode) return;

    // Highlight the matching node
    node.filter(d => d === targetNode)
      .select('circle')
      .transition().duration(300)
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 3)
      .attr('r', d => (d.r || 8) * 1.5);

    // Pulse ring
    node.filter(d => d === targetNode)
      .append('circle')
      .attr('class', 'highlight-pulse')
      .attr('r', (targetNode.r || 8) * 2)
      .attr('fill', 'none')
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1.5)
      .attr('stroke-opacity', 0.8)
      .transition().duration(1200).ease(d3.easeSinOut)
      .attr('r', (targetNode.r || 8) * 4)
      .attr('stroke-opacity', 0)
      .on('end', function() { d3.select(this).remove(); });

    // Pan to center on the highlighted node
    if (targetNode.x != null && targetNode.y != null) {
      const scale = 1.8;
      const tx = W/2 - scale * targetNode.x;
      const ty = H/2 - scale * targetNode.y;
      computerSvg.transition().delay(300).duration(700)
        .call(computerZoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }
  }, 1500);
}

// ═══════════════════════════════════════════════════════════
// NEURAL BAR ANIMATION
// ═══════════════════════════════════════════════════════════

function animateBars() {
  const bars = document.querySelectorAll('.bar-fill');
  bars.forEach(bar => {
    if (bar.id) return; // skip data-driven bars (history, memory)
    const base = parseInt(bar.style.width);
    const jitter = (Math.random() - 0.5) * 6;
    bar.style.width = Math.min(100, Math.max(40, base + jitter)) + '%';
  });
}
setInterval(animateBars, 3000);

// ═══════════════════════════════════════════════════════════
// VOICE CONVERSATION ENGINE
// ═══════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════
// CONVERSATION MODE — Jarvis-style brain visualization
// ═══════════════════════════════════════════════════════════
const BRAIN = {
  // Audio reactivity inputs (set elsewhere — read every frame)
  inputRms:  0,   // mic level (during user speech)
  outputRms: 0,   // TTS playback level (during Data speech)
  // Frequency-domain data, Uint8Array 0..255. Whichever side is louder wins.
  inputFreq:  null,
  outputFreq: null,
  state:     'idle', // 'idle' | 'listening' | 'recording' | 'thinking' | 'speaking'

  // Mesh geometry
  NUM_VERTS:    110,    // sphere vertices
  NN_K:         3,      // edges per vertex to its K nearest neighbors

  // Internals
  active:        false,
  canvas:        null,
  ctx:           null,
  raf:           null,
  verts:         null,  // Float32Array of unit-sphere XYZ, length = NUM_VERTS*3
  edges:         null,  // Uint16Array pairs [a,b,a,b,...]
  vertBinIdx:    null,  // Uint8Array — which freq bin drives each vertex
  vertAmp:       null,  // Float32Array — smoothed per-vertex amplitude
  rotY:          0,
  rotX:          0,
  time:          0,
  smoothedRms:   0,
  audioCtx:      null,
  // Screen-space scratch buffers for projected coords (avoid per-frame alloc)
  _sx: null, _sy: null, _depth: null,

  getAudioCtx() {
    if (CONVO.audioCtx) return CONVO.audioCtx;
    if (this.audioCtx)  return this.audioCtx;
    try {
      this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      return this.audioCtx;
    } catch { return null; }
  },

  // Read the active theme's primary + secondary tokens. In DATA those resolve
  // to orange + yellow; in cyberpunk they resolve to cyan + magenta. Either way
  // the sphere automatically matches the theme without any branching here.
  themePalette() {
    const css = getComputedStyle(document.body);
    const parse = (varName, fallback) => {
      const v = css.getPropertyValue(varName).trim() || fallback;
      // Accept either #RRGGBB or rgb(...)
      if (v.startsWith('#')) {
        const n = v.length === 4
          ? v.slice(1).split('').map(c => parseInt(c + c, 16))
          : [parseInt(v.slice(1,3),16), parseInt(v.slice(3,5),16), parseInt(v.slice(5,7),16)];
        return { r: n[0], g: n[1], b: n[2] };
      }
      const m = v.match(/(\d+)\D+(\d+)\D+(\d+)/);
      return m ? { r:+m[1], g:+m[2], b:+m[3] } : { r:255, g:153, b:0 };
    };
    return {
      primary:   parse('--data-orange', '#FF9900'),  // → cyan in cyberpunk
      secondary: parse('--data-yellow', '#FFCC00'),  // → magenta in cyberpunk
    };
  },

  init() {
    // Sphere visualization retired (2026-06-28, full port from LCARS). BRAIN
    // remains the conversation-mode state machine — its `active` flag and
    // `state` are read all over the voice loop — but it no longer builds
    // geometry or paints the canvas. The framed face video is the focal point
    // now; the canvas competing for the main thread was part of the old chop.
    // Everything below this guard is dead unless the sphere is re-enabled.
    this.canvas = document.getElementById('convo-canvas');
    return;
    // eslint-disable-next-line no-unreachable
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this._resize();
    window.addEventListener('resize', this._resizeHandler = () => this._resize());

    // Fibonacci sphere — even point distribution, no clustering at poles.
    const N = this.NUM_VERTS;
    this.verts      = new Float32Array(N * 3);
    this.vertAmp    = new Float32Array(N);
    this.vertBinIdx = new Uint8Array(N);
    this._sx        = new Float32Array(N);
    this._sy        = new Float32Array(N);
    this._depth     = new Float32Array(N);
    const phi = Math.PI * (3 - Math.sqrt(5));   // golden angle
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2;
      const r = Math.sqrt(1 - y * y);
      const th = phi * i;
      this.verts[i*3+0] = Math.cos(th) * r;
      this.verts[i*3+1] = y;
      this.verts[i*3+2] = Math.sin(th) * r;
      // Assign each vertex a frequency bin based on latitude — poles map to
      // low (bass) bins, equator to high (treble). This makes the sphere
      // deform asymmetrically per voice rather than uniformly pulsing.
      const lat = Math.abs(y);                  // 0 equator .. 1 pole
      // 128 bins (mic) or 256 (TTS); we keep the index in 0..127 and clamp
      // when reading so it works with either.
      this.vertBinIdx[i] = Math.min(127, Math.floor(lat * 127));
    }

    // Edge list: each vertex connects to its K nearest neighbors. Deduplicate
    // by storing pairs with a < b. ~110 verts × 3 ≈ 200 unique edges, cheap.
    const seen = new Set();
    const edges = [];
    for (let i = 0; i < N; i++) {
      const ix = this.verts[i*3], iy = this.verts[i*3+1], iz = this.verts[i*3+2];
      // Build a list of [j, dist²] and pick the K smallest.
      const dists = [];
      for (let j = 0; j < N; j++) {
        if (j === i) continue;
        const dx = ix - this.verts[j*3];
        const dy = iy - this.verts[j*3+1];
        const dz = iz - this.verts[j*3+2];
        dists.push([j, dx*dx + dy*dy + dz*dz]);
      }
      dists.sort((a, b) => a[1] - b[1]);
      for (let k = 0; k < this.NN_K; k++) {
        const j = dists[k][0];
        const a = Math.min(i, j), b = Math.max(i, j);
        const key = a * 1024 + b;
        if (seen.has(key)) continue;
        seen.add(key);
        edges.push(a, b);
      }
    }
    this.edges = new Uint16Array(edges);

    this.rotX = 0; this.rotY = 0; this.time = 0;
  },

  _resize() {
    // Cap internal render resolution. The brain repaints the full viewport with
    // radial gradients every frame; at devicePixelRatio 2 that is ~4x the fill
    // rate for a soft neon sphere no one reads pixel-sharp. Capping at 1.5 frees
    // GPU budget so the face video stops dropping frames.
    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    this.canvas.width  = this.canvas.clientWidth  * dpr;
    this.canvas.height = this.canvas.clientHeight * dpr;
  },

  start() {
    // Sphere render loop removed — just flip the state machine on. No RAF, no
    // canvas painting (that competed with the face video for the main thread).
    this.active = true;
  },

  stop() {
    this.active = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
    if (this._resizeHandler) window.removeEventListener('resize', this._resizeHandler);
    if (this.ctx) this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  },

  _activeFreq() {
    const outActive = this.outputRms * 1.4 >= this.inputRms;
    return outActive ? (this.outputFreq || this.inputFreq)
                     : (this.inputFreq  || this.outputFreq);
  },

  _frame(dt, t) {
    const ctx = this.ctx;
    const W = this.canvas.width, H = this.canvas.height;
    const cx = W / 2, cy = H / 2;
    const minDim = Math.min(W, H);
    const dpr = window.devicePixelRatio || 1;
    this.time += dt;

    // ── Audio reactivity ──────────────────────────────────────────────
    const rawRms = Math.max(this.outputRms * 1.6, this.inputRms);
    this.smoothedRms += (rawRms - this.smoothedRms) * 0.22;
    const audio  = Math.min(1.2, this.smoothedRms * 5);
    const breath = 0.5 + 0.5 * Math.sin(this.time * 0.9);

    // Update per-vertex morph targets from the active analyser; idle fallback
    // uses a soft sinusoidal pattern so the mesh always breathes.
    const N = this.NUM_VERTS;
    const freq = this._activeFreq();
    const attack = 0.45, decay = 0.10;
    if (freq && freq.length) {
      const binMax = freq.length - 1;
      for (let i = 0; i < N; i++) {
        const bin = Math.min(binMax, this.vertBinIdx[i]);
        const tgt = freq[bin] / 255;
        const cur = this.vertAmp[i];
        const k = tgt > cur ? attack : decay;
        this.vertAmp[i] = cur + (tgt - cur) * k;
      }
    } else {
      for (let i = 0; i < N; i++) {
        const phase = i * 0.21 + this.time * 1.1;
        const tgt = 0.08 + 0.06 * (0.5 + 0.5 * Math.sin(phase));
        this.vertAmp[i] = this.vertAmp[i] + (tgt - this.vertAmp[i]) * 0.10;
      }
    }

    // ── Hard clear (no trail — would smear the neon lines) ───────────
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);

    const pal = this.themePalette();
    const cP = pal.primary, cS = pal.secondary;

    // Outer atmospheric halo — grows with overall loudness. Adds depth and a
    // sense of energy spilling off the sphere.
    {
      const sphereR = minDim * 0.24;
      const haloR   = sphereR * (1.6 + audio * 0.5);
      const g = ctx.createRadialGradient(cx, cy, sphereR * 0.6, cx, cy, haloR);
      g.addColorStop(0,   `rgba(${cP.r},${cP.g},${cP.b},${0.10 + audio * 0.15})`);
      g.addColorStop(0.6, `rgba(${cP.r},${cP.g},${cP.b},${0.03 + audio * 0.05})`);
      g.addColorStop(1,   `rgba(${cP.r},${cP.g},${cP.b},0)`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    }

    // ── 3D rotation matrices (X tilt + Y spin) ───────────────────────
    // Spin speed slightly amplified by audio so the mesh feels more alive
    // when Data is talking.
    this.rotY += dt * (0.35 + audio * 0.25);
    this.rotX  = Math.sin(this.time * 0.35) * 0.45;   // gentle nod
    const cy_ = Math.cos(this.rotY), sy_ = Math.sin(this.rotY);
    const cx_ = Math.cos(this.rotX), sx_ = Math.sin(this.rotX);

    // Perspective: small focal length → exaggerated front/back size delta,
    // large focal length → flatter. We want some depth so the mesh reads as 3D.
    const sphereR = minDim * 0.24;
    const focal   = 2.2;     // higher = less perspective
    const sx = this._sx, sy = this._sy, depth = this._depth;

    // Project all vertices once; pull each one outward by its bar amplitude
    // plus a small Perlin-ish wobble for organic morphing.
    for (let i = 0; i < N; i++) {
      // Base unit sphere coords
      let x = this.verts[i*3];
      let y = this.verts[i*3+1];
      let z = this.verts[i*3+2];

      // Per-vertex radial morph: audio amplitude + slow per-vertex sine
      const wob = 0.04 * Math.sin(this.time * 1.4 + i * 0.91)
                + 0.03 * Math.sin(this.time * 2.3 + i * 0.43);
      const rMul = 1 + this.vertAmp[i] * (0.22 + audio * 0.10) + wob + breath * 0.015;
      x *= rMul; y *= rMul; z *= rMul;

      // Rotate around Y, then around X
      const x1 = x * cy_ + z * sy_;
      const z1 = -x * sy_ + z * cy_;
      const y2 = y * cx_ - z1 * sx_;
      const z2 = y * sx_ + z1 * cx_;

      // Perspective project (z2 in -1..1 roughly; map to 2D)
      const zp = focal / (focal + z2);
      sx[i] = cx + x1 * sphereR * zp;
      sy[i] = cy + y2 * sphereR * zp;
      depth[i] = (z2 + 1) * 0.5;   // 0 back ... 1 front
    }

    // ── Mesh edges (additive blending = neon glow buildup) ───────────
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = 'round';
    const E = this.edges.length;
    for (let e = 0; e < E; e += 2) {
      const a = this.edges[e], b = this.edges[e+1];
      const d  = (depth[a] + depth[b]) * 0.5;   // edge depth 0..1
      const amp = Math.max(this.vertAmp[a], this.vertAmp[b]);

      // Front-facing edges brighter; loud edges brighter still.
      const alpha = (0.10 + 0.45 * d) * (0.6 + 0.8 * amp + audio * 0.4);
      // Crossfade primary→secondary based on amplitude so loud parts shift
      // toward the secondary color (DATA yellow / cyberpunk magenta).
      const mix = Math.min(1, amp * 1.6);
      const r = cP.r * (1 - mix) + cS.r * mix;
      const g = cP.g * (1 - mix) + cS.g * mix;
      const bb = cP.b * (1 - mix) + cS.b * mix;

      ctx.strokeStyle = `rgba(${r|0},${g|0},${bb|0},${Math.min(1, alpha)})`;
      ctx.lineWidth = (0.6 + d * 1.4) * dpr;
      ctx.beginPath();
      ctx.moveTo(sx[a], sy[a]);
      ctx.lineTo(sx[b], sy[b]);
      ctx.stroke();
    }

    // ── Vertex nodes (small bright neon dots) ────────────────────────
    for (let i = 0; i < N; i++) {
      const d = depth[i];
      const amp = this.vertAmp[i];
      const size = (1.2 + d * 2.0 + amp * 2.5) * dpr;
      const alpha = (0.45 + 0.45 * d) * (0.5 + amp * 1.2 + audio * 0.3);
      // Hot nodes (loud bin) glow secondary color
      const mix = Math.min(1, amp * 1.8);
      const r = cP.r * (1 - mix) + cS.r * mix;
      const g = cP.g * (1 - mix) + cS.g * mix;
      const bb = cP.b * (1 - mix) + cS.b * mix;
      // Soft radial dot
      const rg = ctx.createRadialGradient(sx[i], sy[i], 0, sx[i], sy[i], size * 2.5);
      rg.addColorStop(0,    `rgba(255,255,255,${Math.min(1, alpha)})`);
      rg.addColorStop(0.4,  `rgba(${r|0},${g|0},${bb|0},${alpha * 0.7})`);
      rg.addColorStop(1,    `rgba(${r|0},${g|0},${bb|0},0)`);
      ctx.fillStyle = rg;
      ctx.beginPath();
      ctx.arc(sx[i], sy[i], size * 2.5, 0, Math.PI * 2);
      ctx.fill();
    }

    // ── Central soft glow (the "consciousness" at the sphere's core) ──
    {
      const coreR = sphereR * (0.50 + audio * 0.18 + breath * 0.04);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
      g.addColorStop(0,    `rgba(255,255,255,${0.45 + audio * 0.30})`);
      g.addColorStop(0.4,  `rgba(${cS.r},${cS.g},${cS.b},${0.30 + audio * 0.25})`);
      g.addColorStop(1,    `rgba(${cP.r},${cP.g},${cP.b},0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.globalCompositeOperation = 'source-over';
  },
};

async function enterConvoMode() {
  // Conversation Mode runs on the local Kokoro voice engine (CPU, offline).
  // If the optional voice dependencies are not installed, /voice/status reports
  // stt_available:false and warmup fails cleanly — the overlay still opens and
  // shows a clear "voice unavailable" status rather than silently doing nothing.
  const overlay = document.getElementById('convo-overlay');
  if (!overlay) return;
  if (BRAIN.active) return; // already in convo mode

  // Free the mic from the global dashboard wake-word listener so conversation
  // mode owns it. exitConvoMode restarts it if the Captain had it on.
  _stopWakeListener();

  // Voice uses its own provider, independent of the chat pill. Sync the toggle
  // UI; await the crew sync so each officer's wake words / barge-in names are
  // loaded from the bridge before the IDLE listener arms.
  syncVoiceProviderToggle();
  syncTtsEngineToggle();
  await syncCrewVoiceToggle();

  document.getElementById('convo-transcript').innerHTML = '';
  overlay.classList.remove('hidden');
  document.body.classList.add('convo-mode-active');

  BRAIN.init();
  BRAIN.start();
  _convoFaceSync('idle');   // start ONLY the idle loop (autoplay removed from markup)
  _prefetchSpeakingFace();  // warm the speaking clip so the first swap is instant
  document.addEventListener('keydown', _convoKeyHandler);

  CONVO.state  = 'idle';
  CONVO.active = true;

  // The voice stack (STT/TTS) is baked into the runtime at install time, so a
  // fresh machine already has the wheels. If they still report unavailable, the
  // baked-in deps failed to import (e.g. a missing C++ runtime) — surface the
  // reason and bail rather than looping on a silent failure.
  setConvoBrainState('thinking', 'CHECKING VOICE COMPONENTS...');
  try {
    const vstat = await (await fetch(`${API_BASE}/voice/status`)).json();
    if (!BRAIN.active) return;
    if (vstat && vstat.stt_available === false) {
      setConvoBrainState('idle', `VOICE UNAVAILABLE - ${(vstat.voice_error || 'see bridge log').slice(0, 90)}`);
      return;
    }
  } catch (_) { /* status fetch failed — fall through to warmup */ }

  // Block the mic until the voice models are loaded — otherwise STT contends
  // with the in-progress TTS load and a 3s transcription balloons to ~50s. The
  // ceiling is generous because the first run still lazy-downloads the model
  // assets (Kokoro ~340MB + Whisper) even though the wheels are baked in.
  setConvoBrainState('thinking', 'WARMING UP VOICE MODELS...');
  const ready = await _waitForVoiceReady(360);
  if (!BRAIN.active) return;  // Captain closed the overlay during warmup
  if (!ready) {
    setConvoBrainState('idle', 'WARMUP FAILED — CHECK BRIDGE LOG');
    return;
  }

  // Armed, but hands-off: the Captain must say the officer's wake word
  // ("Vector", …) or tap the sphere to begin.
  convoGoIdle();
}

// Polls /voice/status until ready (or hits a hard ceiling). Updates the
// status band with a countdown so the user knows the wait is bounded.
async function _waitForVoiceReady(maxSeconds = 180) {
  const start = Date.now();
  while (Date.now() - start < maxSeconds * 1000) {
    if (!BRAIN.active) return false;
    try {
      const r = await fetch(`${API_BASE}/voice/status`);
      const d = await r.json();
      if (d.ready) return true;
      const elapsed = ((Date.now() - start) / 1000).toFixed(0);
      const parts = [];
      if (!d.stt_loaded) parts.push('STT');
      if (!d.tts_loaded) parts.push('TTS');
      setConvoBrainState('thinking', `WARMING UP ${parts.join(' + ')}  (${elapsed}s)`);
    } catch (e) {
      setConvoBrainState('thinking', `WAITING FOR BRIDGE  (${((Date.now()-start)/1000).toFixed(0)}s)`);
    }
    await new Promise(res => setTimeout(res, 1000));
  }
  return false;
}

function exitConvoMode() {
  if (!BRAIN.active) return;
  document.getElementById('convo-overlay').classList.add('hidden');
  document.body.classList.remove('convo-mode-active');
  BRAIN.stop();
  _convoFaceStop();   // stop both 720p face loops decoding while overlay hidden
  document.removeEventListener('keydown', _convoKeyHandler);

  convoTeardown();   // stop every recognizer, the recorder, playback + the mic

  // Re-arm the global dashboard wake-word listener if the Captain had it on.
  if (WAKE.enabled) _startWakeListener();
}

// ═══════════════════════════════════════════════════════════
// WAKE WORD — passive listener on browser-side SpeechRecognition.
// "Computer" / "Wake up" → arms wake dictation into the active input.
// Uses Chrome's built-in Web Speech API (sends audio to Google for
// recognition), so it costs nothing and runs all the time.
// ═══════════════════════════════════════════════════════════
const WAKE = {
  enabled:    false,           // user-facing on/off
  recognizer: null,            // current SpeechRecognition instance
  // Wake phrases: an address prefix ("hey", "hi", "hello", "ok", "okay",
  // "yo") + the assistant name, OR bare "computer" said on its own, OR the
  // explicit "data start listening" / "wake up" commands. Bare "data" still
  // does not count — it triggered constantly in normal speech.
  words:      ['hey data', 'hi data', 'hello data', 'yo data',
               'hey computer', 'hi computer', 'hello computer',
               'ok data', 'okay data', 'ok computer', 'okay computer',
               'computer', 'data start listening', 'wake up'],
  cooldownUntil: 0,            // ms timestamp; ignore matches before this
};

function _wakeWordRegex() {
  // Four branches:
  //   1. addressing prefix + name — "hey data", "yo computer", "okay data"
  //   2. bare "computer" — but ONLY as a whole standalone utterance (^…$),
  //      so "computer" buried in a normal sentence does not fire. The wake
  //      listener acts on finalized segments, so saying "Computer" and then
  //      pausing makes it its own segment. Unanchored bare "computer" was
  //      removed precisely because it triggered constantly mid-speech.
  //   3. explicit command — "data start listening" (comma optional)
  //   4. standalone wake — "wake up"
  return /(?:\b(?:hey|hi|hello|ok|okay|yo)[\s,]+(?:data|computer)\b)|(?:^\W*computer\W*$)|(?:\bdata[,\s]+start\s+listening\b)|(?:\bwake[\s-]?up\b)/i;
}

// ── Voice shutdown command — "Computer, shut down" ─────────────────────────
// Heard by the wake listener; powers DATA down entirely (bridge, voice,
// tunnel, and dashboard) after a short cancellable countdown.
let _shutdownPending = false;

function _shutdownRegex() {
  // "computer" + optional "please" / "perform (a/an)" / "emergency" +
  // shut down | shutdown | power down | power off. Anchored ^…$ so the whole
  // utterance must BE the command — "my computer shut down overnight" said in
  // normal conversation does not trigger it.
  return /^\W*computer[\s,]+(?:please[\s,]+)?(?:perform[\s,]+(?:an?[\s,]+)?)?(?:emergency[\s,]+)?(?:shut[\s,]*down|power[\s,]*(?:down|off))\W*$/i;
}

function initiateDataShutdown() {
  if (_shutdownPending) return;
  _shutdownPending = true;
  try { playDataSound('error'); } catch (e) {}
  addLog('Shutdown command accepted — 5s to cancel');

  const overlay = document.createElement('div');
  overlay.id = 'data-shutdown-overlay';
  overlay.style.cssText =
    'position:fixed;inset:0;z-index:99999;display:flex;flex-direction:column;' +
    'align-items:center;justify-content:center;gap:26px;text-align:center;' +
    'background:rgba(10,0,0,0.95);font-family:inherit;';
  overlay.innerHTML =
    '<div style="color:#ff5544;font-size:40px;font-weight:bold;letter-spacing:3px;">' +
      '⚠ DATA SHUTDOWN INITIATED</div>' +
    '<div style="color:#ffb08a;font-size:17px;max-width:560px;">' +
      'Powering down all systems — bridge, voice, tunnel, and dashboard.</div>' +
    '<div id="data-shutdown-count" style="color:#ff5544;font-size:104px;' +
      'font-weight:bold;line-height:1;">5</div>' +
    '<button id="data-shutdown-cancel" style="font-size:19px;padding:14px 44px;' +
      'cursor:pointer;background:#ff9966;color:#160500;border:none;' +
      'border-radius:24px;font-weight:bold;letter-spacing:2px;">CANCEL  ·  ESC</button>';
  document.body.appendChild(overlay);

  const countEl = overlay.querySelector('#data-shutdown-count');
  let remaining = 5;
  const tick = setInterval(() => {
    remaining -= 1;
    if (remaining > 0) { countEl.textContent = String(remaining); return; }
    clearInterval(tick);
    document.removeEventListener('keydown', onKey);
    _performDataShutdown(overlay);
  }, 1000);

  const cancel = () => {
    clearInterval(tick);
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    _shutdownPending = false;
    addLog('Shutdown cancelled');
    _restartWakeIfEnabled();
  };
  const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); cancel(); } };
  overlay.querySelector('#data-shutdown-cancel').addEventListener('click', cancel);
  document.addEventListener('keydown', onKey);
}

async function _performDataShutdown(overlay) {
  const countEl = overlay.querySelector('#data-shutdown-count');
  if (countEl) countEl.textContent = '·';
  const btn = overlay.querySelector('#data-shutdown-cancel');
  if (btn) btn.remove();
  try {
    await fetch(`${API_BASE}/shutdown`, { method: 'POST' });
  } catch (e) {
    // Expected — the bridge kills itself mid-response.
  }
  overlay.innerHTML =
    '<div style="color:#ff5544;font-size:46px;font-weight:bold;letter-spacing:4px;">' +
      'DATA OFFLINE</div>' +
    '<div style="color:#ffb08a;font-size:16px;max-width:560px;">' +
      'All systems powered down. You may close this window — ' +
      'relaunch any time with launch_data.bat.</div>';
  setTimeout(() => { try { window.close(); } catch (e) {} }, 900);
}

function toggleWakeListener() {
  // On mobile, the pill cannot enable wake listening — keep it pinned off.
  if (_isMobileDevice()) {
    _stopWakeListener();
    WAKE.enabled = false;
    localStorage.setItem('wake-enabled', '0');
    addLog('Wake word: unavailable on mobile view');
    _updateWakeButton();
    return;
  }
  if (WAKE.enabled) {
    _stopWakeListener();
    WAKE.enabled = false;
    localStorage.setItem('wake-enabled', '0');
  } else {
    WAKE.enabled = true;
    localStorage.setItem('wake-enabled', '1');
    _startWakeListener();
  }
  _updateWakeButton();
}

function _updateWakeButton() {
  const btn = document.getElementById('wake-toggle-btn');
  if (!btn) return;
  // Mobile view: wake listening is disabled — show it plainly, not as "ON".
  if (_isMobileDevice()) {
    btn.textContent = '◌ WAKE: N/A';
    btn.classList.remove('active');
    btn.title = 'Wake word listening is disabled on mobile view';
    return;
  }
  const listening = WAKE.enabled && !BRAIN.active;
  btn.textContent = WAKE.enabled
    ? (listening ? '◉ WAKE: ON' : '◌ WAKE: PAUSED')
    : '◌ WAKE: OFF';
  btn.classList.toggle('active', listening);
}

function _startWakeListener() {
  // Hard block on mobile/tablet. Mobile Chrome ignores
  // SpeechRecognition.continuous, so the recognizer ends every few seconds and
  // auto-restarts — thrashing the mic and triggering a perpetual OS
  // "site is using your microphone" notification. Every start path (page-load
  // init, WAKE pill tap, onend restart, _restartWakeIfEnabled) funnels through
  // here, so this single guard disables wake-word listening on mobile view
  // completely. The Captain uses the dictation button there instead.
  if (_isMobileDevice()) {
    WAKE.enabled = false;
    addLog('Wake word: disabled on mobile view');
    _updateWakeButton();
    return;
  }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    addLog('Wake word: SpeechRecognition not supported (Chrome required)');
    return;
  }
  // Don't compete with conversation mode, active wake-dictation, or a
  // browser-STT dictation session for the mic / recognizer slot
  if (BRAIN.active || WAKE_DICTATION.active || (typeof _browserRec !== 'undefined' && _browserRec)) { _updateWakeButton(); return; }

  if (WAKE.recognizer) { try { WAKE.recognizer.stop(); } catch {} WAKE.recognizer = null; }

  const rec = new SR();
  rec.continuous     = true;
  rec.interimResults = true;
  rec.lang           = 'en-US';

  rec.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const heard = (event.results[i][0].transcript || '').toLowerCase();
      // Shutdown command — checked on interim results too (it is a long,
      // distinctive, anchored phrase) and ignores the wake cooldown, so
      // "Computer, shut down" always lands. The cancellable countdown in
      // initiateDataShutdown() guards against a mis-hear.
      if (_shutdownRegex().test(heard)) {
        addLog(`Shutdown command heard: "${heard.trim().slice(0, 48)}"`);
        _stopWakeListener();
        initiateDataShutdown();
        return;
      }
      // Voice stop — "<officer> stop" / "computer stop" halts all playback.
      // The wake listener keeps running, so it is already waiting for the
      // next wake word. Checked on interim results for a fast cut-off.
      if (_stopPhraseRegex().test(heard)) {
        addLog('Voice stop — playback halted, awaiting wake word');
        _stopAllVoice();
        WAKE.cooldownUntil = Date.now() + 1500;
        return;
      }
      // Wake word — finalized segments only (a real pause after the word),
      // and not during the post-wake echo cooldown.
      if (!event.results[i].isFinal) continue;
      if (Date.now() < WAKE.cooldownUntil) continue;
      // Crew wake word (carried over from Conversation Mode) — saying an
      // officer's name switches the main-channel agent to them, then dictates.
      const crewHit = _crewWakeMatch(heard);
      if (crewHit) {
        addLog(`Crew wake: ${crewLabel(crewHit)}`);
        WAKE.cooldownUntil = Date.now() + 5000;
        if (typeof setMainChatCrew === 'function') setMainChatCrew(crewHit);
        _stopWakeListener();
        startWakeDictation();
        return;
      }
      if (_wakeWordRegex().test(heard)) {
        addLog(`Wake word heard: "${heard.trim().slice(0, 40)}"`);
        WAKE.cooldownUntil = Date.now() + 5000;  // 5s lockout against echo
        _stopWakeListener();
        startWakeDictation();
        return;
      }
    }
  };

  rec.onerror = (e) => {
    // "no-speech" and "aborted" are normal; only complain about real failures
    if (e.error && !['no-speech', 'aborted', 'audio-capture'].includes(e.error)) {
      console.warn('[wake] error:', e.error);
    }
    // not-allowed = mic denied; turn off so we don't keep retrying
    if (e.error === 'not-allowed') {
      WAKE.enabled = false;
      addLog('Wake word: mic permission denied — disabling');
      _updateWakeButton();
    }
  };

  rec.onend = () => {
    // Web Speech API auto-stops after ~60s of silence; restart if still on.
    // Skip during dictation / conversation mode so we don't grab the mic
    // while another path owns it.
    if (WAKE.enabled && !BRAIN.active && !WAKE_DICTATION.active &&
        !(typeof _browserRec !== 'undefined' && _browserRec)) {
      setTimeout(_startWakeListener, 250);
    }
  };

  try {
    rec.start();
    WAKE.recognizer = rec;
    _updateWakeButton();
  } catch (e) {
    // start() throws if the recognizer is already running; just ignore.
    console.warn('[wake] start failed:', e);
  }
}

function _stopWakeListener() {
  if (WAKE.recognizer) {
    // Null the onend handler BEFORE abort — otherwise the recognizer's
    // built-in onend fires and schedules a racing _startWakeListener that
    // would try to grab the mic while wake-dictation owns it. That race
    // was what made wake stop responding after the first reply.
    WAKE.recognizer.onend = null;
    WAKE.recognizer.onerror = null;
    try { WAKE.recognizer.abort(); } catch {}
    WAKE.recognizer = null;
  }
  _updateWakeButton();
}

// ── Wake → dictate-into-last-input → auto-send after 5s silence ─────────
// Records audio with WebAudio VAD. When the Captain has been silent for 5s,
// stops recording, transcribes via /transcribe, injects into whichever input
// they last interacted with, then submits that pane's chat.
const WAKE_DICTATION = {
  active:   false,
  stream:   null,
  ctx:      null,
  recorder: null,
  aborted:  false,   // set true by Esc to skip transcribe + auto-submit
};

const WAKE_DICT_SILENCE_THRESHOLD = 0.012;
const WAKE_DICT_SILENCE_MS        = 1500;     // silence after speech that ends + sends — 1.5s cutoff (was 2s)
const WAKE_DICT_MAX_MS            = 3600000;  // 60 minutes (ceiling; the silence cutoff above ends it earlier)

function _resolveActiveInput() {
  const id = _activeInputId || 'chat-input';
  return { id, el: document.getElementById(id) };
}

function _autoSubmitFromInput(inputId) {
  if (inputId === 'chat-input') {
    sendMessage();
    return;
  }
  const m = inputId.match(/^pane-input-ws(\d+)$/);
  if (m) {
    const wsId = parseInt(m[1]);
    if (!isNaN(wsId)) sendProjectMessage(wsId);
  }
}

async function startWakeDictation() {
  if (WAKE_DICTATION.active) return;
  const { id: targetId, el: targetInput } = _resolveActiveInput();
  if (!targetInput) {
    addLog('Wake dictation: no active input found');
    _restartWakeIfEnabled();
    return;
  }

  // No server Whisper (core build) — route to the browser-STT fallback.
  // Wake-initiated, so run it hands-free (wakeMode=true): the recognizer stops
  // at the first natural pause, then auto-submits and re-arms the wake listener.
  if (!(await _serverSttAvailable())) {
    const btn = document.querySelector(`.dictate-btn[data-target-input="${targetId}"]`)
             || document.getElementById('dictate-btn');
    _browserDictationToggle(btn, targetId, true);
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    addLog('Wake dictation: mic denied');
    _restartWakeIfEnabled();
    return;
  }

  WAKE_DICTATION.active = true;
  WAKE_DICTATION.stream = stream;
  // Sweep any stale highlight off other inputs (in case a prior dictation
  // targeted a different element and cleanup missed it).
  document.querySelectorAll('.wake-dictating').forEach(el => el.classList.remove('wake-dictating'));
  // Force a clean class toggle: remove first, force layout reflow to commit
  // the removal, then re-add. Without the reflow, browsers may skip the
  // animation restart when the class is added back too quickly.
  void targetInput.offsetWidth;
  targetInput.classList.add('wake-dictating');
  targetInput.focus();
  addLog(`Wake dictation → ${targetId} — speak after the tone`);

  // WebAudio VAD
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  WAKE_DICTATION.ctx = ctx;
  await ctx.resume();
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  const silentSink = ctx.createGain();
  silentSink.gain.value = 0;
  silentSink.connect(ctx.destination);
  src.connect(proc);
  proc.connect(silentSink);

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus' : 'audio/ogg;codecs=opus';
  const chunks = [];
  const recorder = new MediaRecorder(stream, { mimeType });
  WAKE_DICTATION.recorder = recorder;
  recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };

  const startTime = Date.now();
  let lastSpeechTime = startTime;
  let everSpoke = false;
  let stopped = false;

  const _stop = (reason) => {
    if (stopped) return;
    stopped = true;
    try { proc.disconnect(); } catch {}
    proc.onaudioprocess = null;
    if (recorder.state !== 'inactive') recorder.stop();
    addLog(`Wake dictation: stopping (${reason})`);
  };

  proc.onaudioprocess = e => {
    if (stopped) return;
    const data = e.inputBuffer.getChannelData(0);
    let s = 0; for (let i = 0; i < data.length; i++) s += data[i] * data[i];
    const rms = Math.sqrt(s / data.length);
    const now = Date.now();
    if (rms > WAKE_DICT_SILENCE_THRESHOLD) {
      lastSpeechTime = now;
      everSpoke = true;
    }
    if (everSpoke && (now - lastSpeechTime) > WAKE_DICT_SILENCE_MS) {
      _stop('4s silence');
    }
    if (now - startTime > WAKE_DICT_MAX_MS) {
      _stop(`${Math.round(WAKE_DICT_MAX_MS / 1000)}s max`);
    }
  };

  recorder.onstop = async () => {
    stream.getTracks().forEach(t => t.stop());
    try { ctx.close(); } catch {}
    targetInput.classList.remove('wake-dictating');
    const wasAborted = WAKE_DICTATION.aborted;
    WAKE_DICTATION.active = false;
    WAKE_DICTATION.stream = null;
    WAKE_DICTATION.ctx = null;
    WAKE_DICTATION.recorder = null;
    WAKE_DICTATION.aborted = false;

    if (wasAborted) {
      addLog('Wake dictation: cancelled (Esc)');
      _restartWakeIfEnabled();
      return;
    }

    if (!everSpoke) {
      addLog('Wake dictation: no speech detected');
      _restartWakeIfEnabled();
      return;
    }

    const blob = new Blob(chunks, { type: mimeType });
    try {
      const res  = await fetch(`${API_BASE}/transcribe`, {
        method:  'POST',
        headers: { 'Content-Type': mimeType },
        body:    blob,
      });
      const data = await res.json();
      if (data.text) {
        const existing = targetInput.value.trimEnd();
        targetInput.value = (existing ? existing + ' ' : '') + data.text;
        addLog(`Wake dictation: ${data.text.substring(0, 60)}`);
        // Spoken prompt → spoken reply: arm auto-speak so the Computer reads
        // the response back sentence-by-sentence, hands-free. Main comm
        // channel only (chat-input); project panes stay silent.
        if (targetId === 'chat-input') _autoSpeakNextReply = true;
        _autoSubmitFromInput(targetId);
      } else {
        addLog('Wake dictation: empty transcript');
      }
    } catch (e) {
      addLog('Wake dictation error: ' + e.message);
    }
    _restartWakeIfEnabled();
  };

  recorder.start(100);
  // Audible + visual "go" cue. The Captain speaks AFTER this tone, so the
  // recorder is already capturing (first words are not clipped) and they
  // speak fluently in one breath instead of hesitating — hesitation pauses
  // were tripping the silence cutoff and chopping the prompt into fragments.
  playDataSound('confirm');
  setStatus('◉ LISTENING — SPEAK YOUR MESSAGE NOW');
}

function _restartWakeIfEnabled() {
  if (WAKE.enabled && !BRAIN.active) setTimeout(_startWakeListener, 500);
}

// Restore wake state on page load.
//   - Desktop: defaults to ON (must explicitly disable to keep off).
//   - Mobile/tablet: defaults to OFF. Phones keep the mic open for wake-word
//     recognition, which makes Android show constant "site using microphone"
//     system notifications. Captain can still tap the WAKE pill manually.
// Either way, an explicit user choice (localStorage 'wake-enabled' = '0' or '1')
// overrides the default.
function _isMobileDevice() {
  return /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
         (window.matchMedia && window.matchMedia('(max-width: 900px)').matches);
}
(function _initWake() {
  setTimeout(() => {
    const stored = localStorage.getItem('wake-enabled');
    let enabled;
    // Mobile/tablet: always off, regardless of any stored choice. Mobile
    // Chrome ignores SpeechRecognition.continuous, so the wake listener ends
    // every few seconds and auto-restarts — thrashing the mic and making the
    // OS show a perpetual "site is using your microphone" notification.
    // A stale wake-enabled=1 from an earlier session must not re-arm it.
    if (_isMobileDevice()) enabled = false;
    else if (stored === '0') enabled = false;
    else if (stored === '1') enabled = true;
    // No explicit choice yet: desktop defaults ON.
    else enabled = true;
    if (enabled) {
      WAKE.enabled = true;
      _startWakeListener();
    }
    _updateWakeButton();
  }, 500);
})();

// ── Voice provider toggle (LOCAL · 3B / HAIKU · API) ───────────────────────
async function syncVoiceProviderToggle() {
  try {
    const res = await fetch(`${API_BASE}/voice/provider`);
    const data = await res.json();
    _paintVoiceToggle(data.active, data.choices || []);
  } catch (e) {
    addLog(`Voice provider fetch failed: ${e.message || e}`);
  }
}

// Compact label/title for each voice provider id, shown on the pill.
const _VOICE_PROVIDER_LABELS = {
  'ollama-small':      { short: 'LOCAL · 3B',       title: 'Qwen 2.5 3B — local on your GPU, no token cost, ~1-2s/turn' },
  'claude-cli-haiku':  { short: 'HAIKU · SUB',      title: 'Claude Haiku 4.5 via your Code subscription, no token cost' },
  'claude-cli-sonnet': { short: 'SONNET · SUB',     title: 'Claude Sonnet 4.6 via your Code subscription, slower / smarter' },
  'claude-cli':        { short: 'OPUS · SUB',       title: 'Claude Opus 4.7 via your Code subscription, slowest / max quality' },
  'claude-api-fast':   { short: 'HAIKU · API',      title: 'Claude Haiku 4.5 via API (pay per token, fast)' },
  'codex':             { short: 'GPT-5 · SUB',      title: 'OpenAI Codex (GPT-5) via your ChatGPT subscription' },
};

function _paintVoiceToggle(activeId, choices) {
  const row = document.getElementById('convo-provider-toggle');
  if (!row) return;
  // Rebuild the pills each render so the order from /voice/provider drives layout
  row.innerHTML = (choices || []).map(c => {
    const meta  = _VOICE_PROVIDER_LABELS[c.id] || { short: c.id.toUpperCase(), title: c.label };
    const cls   = [
      'convo-provider-opt',
      c.id === activeId ? 'active' : '',
      c.available ? '' : 'unavailable',
    ].filter(Boolean).join(' ');
    const safeShort = meta.short.replace(/"/g, '&quot;');
    const safeTitle = meta.title.replace(/"/g, '&quot;');
    return `<button type="button" class="${cls}"
              data-provider="${c.id}"
              onclick="setVoiceProvider('${c.id}')"
              title="${safeTitle}">${safeShort}</button>`;
  }).join('');
}

async function setVoiceProvider(providerId) {
  try {
    const res = await fetch(`${API_BASE}/voice/provider`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ provider: providerId }),
    });
    const data = await res.json();
    if (data.error) {
      addLog(`Voice provider switch failed: ${data.error}`);
      if (data.install_hint) addLog(`Install hint: ${data.install_hint}`);
      return;
    }
    addLog(`Voice provider → ${providerId}`);
    syncVoiceProviderToggle();
  } catch (e) {
    addLog(`Voice provider switch error: ${e.message || e}`);
  }
}

// ── TTS engine toggle (F5 / XTTS) ─────────────────────────────────────────
async function syncTtsEngineToggle() {
  try {
    const res = await fetch(`${API_BASE}/voice/tts_engine`);
    const data = await res.json();
    _paintTtsToggle(data.engine);
  } catch (e) {
    addLog(`TTS engine fetch failed: ${e.message || e}`);
  }
}

function _paintTtsToggle(engineId) {
  document.querySelectorAll('.convo-tts-opt').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.engine === engineId);
  });
}

async function setTtsEngine(engineId) {
  try {
    const res = await fetch(`${API_BASE}/voice/tts_engine`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ engine: engineId }),
    });
    const data = await res.json();
    if (data.error) {
      addLog(`TTS engine switch failed: ${data.error}`);
      return;
    }
    addLog(`TTS engine → ${engineId} (next reply uses this)`);
    _paintTtsToggle(data.engine || engineId);
  } catch (e) {
    addLog(`TTS engine switch error: ${e.message || e}`);
  }
}

// ── Crew voice selector — which officer the Captain talks to ───────────────
// The roster + display names come from the bridge (/voice/voices) so the
// names are authoritative. CREW_VOICE is the chosen officer id; it is sent
// to /speak_stream as ?voice= and drives both the persona and the cloned
// voice the reply is synthesized in.
let CREW_VOICE = localStorage.getItem('crew-voice') || 'data';
let CREW_VOICES_LIST = [];   // [{id, name, is_crew}] — populated from the bridge

function crewName(id) {
  const v = CREW_VOICES_LIST.find(x => x.id === (id || CREW_VOICE));
  return v ? v.name : 'Data';
}

async function syncCrewVoiceToggle() {
  try {
    const res  = await fetch(`${API_BASE}/voice/voices`);
    const data = await res.json();
    CREW_VOICES_LIST = data.voices || [];
    // Fall back to the bridge default if the saved choice is no longer valid.
    if (!CREW_VOICES_LIST.some(v => v.id === CREW_VOICE))
      CREW_VOICE = (data.default || 'data');
    _paintCrewToggle();
  } catch (e) {
    addLog(`Crew voices fetch failed: ${e.message || e}`);
  }
}

function _paintCrewToggle() {
  const row = document.getElementById('convo-crew-toggle');
  if (!row) return;
  if (!CREW_VOICES_LIST.length) { row.innerHTML = ''; return; }
  row.innerHTML = CREW_VOICES_LIST.map(v => {
    const cls = 'convo-crew-opt' + (v.id === CREW_VOICE ? ' active' : '');
    const nm  = (v.name || v.id).replace(/"/g, '&quot;');
    return `<button type="button" class="${cls}" data-voice="${v.id}"
              onclick="setCrewVoice('${v.id}')"
              title="Talk to ${nm}">${nm.toUpperCase()}</button>`;
  }).join('');
}

function setCrewVoice(id) {
  CREW_VOICE = id;
  localStorage.setItem('crew-voice', id);
  _paintCrewToggle();
  const nm = crewName(id);
  addLog(`Voice → ${nm}`);
  // If conversation mode is idle, re-arm the wake listener for the newly
  // selected officer and refresh the IDLE prompt with his wake phrase.
  if (BRAIN.active && CONVO.state === 'idle') {
    _convoStopWakeListener();
    convoState('idle');
    _convoLabel(_convoIdlePrompt(id));
    _convoStartWakeListener();
  }
}

function setConvoBrainState(state, label) {
  BRAIN.state = state;
  const lbl = document.getElementById('convo-status-label');
  if (lbl) lbl.textContent = label || state.toUpperCase();
}

function _convoOverlayClick(evt) {
  // The sphere is a universal button — tap to start, force-submit, or barge
  // in, depending on the state. Clicks on the toggle pills / exit button reach
  // their own handlers (their ids do not match), so this only acts on a tap on
  // the backdrop, the canvas, or the status label.
  const id = evt.target.id;
  if (id !== 'convo-overlay' && id !== 'convo-canvas' && id !== 'convo-status-label') return;
  if      (CONVO.state === 'idle')      convoStartListening();
  else if (CONVO.state === 'speaking')  convoBargeIn();
  else if (CONVO.state === 'listening') {
    if (CONVO.speechDetected) convoFlush();   // force-submit what is recorded
    else convoGoIdle();                       // nothing said yet — stand down
  }
  // 'thinking' → ignore; a tap cannot help mid-request
}

function _convoKeyHandler(e) {
  if (e.key === 'Escape') exitConvoMode();
}

function _convoAppendTurn(role, text) {
  if (!BRAIN.active) return;
  const t = document.getElementById('convo-transcript');
  if (!t) return;
  const div = document.createElement('div');
  div.className = role === 'user' ? 'convo-turn-user' : 'convo-turn-data';
  div.textContent = (role === 'user' ? 'You: ' : '') + text;
  t.appendChild(div);
  while (t.children.length > 6) t.removeChild(t.firstChild);
}

// CONVO.state drives the whole conversation loop. CONVO.active mirrors
// (state !== 'off') for the few legacy callers (STOP_LISTENER, _convoAppendTurn)
// that still read a boolean.
const CONVO = {
  state:  'off',     // 'off' | 'idle' | 'listening' | 'thinking' | 'speaking'
  active: false,
  gen:    0,         // turn counter — guards a stale turn against re-listening

  // Mic graph — built once per overlay session by _convoEnsureMic, reused.
  stream:      null,
  audioCtx:    null,
  source:      null,
  processor:   null,
  micAnalyser: null,
  threshold:   0.018,
  calibrated:  false,

  // Per-turn recorder
  mediaRecorder: null,
  chunks:        [],

  // VAD bookkeeping
  speechDetected: false,
  speechStart:    null,
  silenceStart:   null,
  aboveStart:     null,

  // Recognizers — at most one runs at a time (wake in IDLE, barge-in otherwise)
  wakeRec:      null,
  interruptRec: null,

  // Playback + in-flight request
  currentAudio: null,
  abort:        null,   // AbortController for the active /speak_stream fetch

  // Timers
  maxTimer:  null,
  idleTimer: null,

  // ── Tuning ────────────────────────────────────────────────
  SILENCE_HOLD_MS: 1500,   // sustained silence that ends an utterance (1.5s = ~500ms faster than old 2s)
  SPEECH_HOLD_MS:  200,    // sustained energy before it counts as real speech
  MIN_SPEECH_MS:   400,    // discard utterances shorter than this
  MAX_SPEECH_MS:   30000,  // hard cap — submit at 30s regardless
  IDLE_TIMEOUT_MS: 15000,  // no speech in LISTENING this long → drop to IDLE
};

// Central transition — sets the logical state, the brain visualization state,
// the status-band label, and the (optional) footer button.
function convoState(state, label) {
  CONVO.state  = state;
  CONVO.active = (state !== 'off');
  if (typeof BRAIN !== 'undefined' && BRAIN) {
    const brainMap = { idle: 'idle', listening: 'listening', recording: 'recording',
                       thinking: 'thinking', speaking: 'speaking' };
    BRAIN.state = brainMap[state] || 'idle';
  }
  _convoFaceSync(state);
  if (label) _convoLabel(label);
  const btn = document.getElementById('voice-toggle-btn');
  if (btn) {
    const icons = { idle: '◌', listening: '◉', recording: '⏺',
                    thinking: '…', speaking: '▶', off: '🗣️' };
    btn.textContent = icons[state] || '🗣️';
    btn.className = 'voice-toggle-btn' + (state !== 'off' ? ` voice-${state}` : '');
  }
}

// ── DATA DAEMON face — SINGLE-STREAM SOURCE SWAP (idle ↔ speaking) ───────────
// One <video> element, one decoder, ever. When Data speaks we swap the element's
// src to the speaking loop; when he stops we swap back to the idle loop. Because
// only ONE 720p stream is ever decoding, this carries NO added main-thread cost
// over the plain idle loop — the chop only ever came from running TWO loops at
// once (the retired crossfade). The CSS "speaking" glow rides along on top.
// Single chokepoint: convoState() → _convoFaceSync().
const _FACE_SRC = {
  idle:     'assets/faces/daita_face_idle.opt.mp4',
  speaking: 'assets/faces/daita_face_loop.opt.mp4',
};
let _faceMode = 'idle';   // which clip the single element is currently playing

// Warm the speaking clip into the HTTP cache once, so the first idle→speaking
// swap decodes from disk instead of stalling on a network/disk fetch. This does
// NOT create a second decoder — it is a byte prefetch only.
let _facePrefetched = false;
function _prefetchSpeakingFace() {
  if (_facePrefetched) return;
  _facePrefetched = true;
  try { fetch(_FACE_SRC.speaking, { cache: 'force-cache' }).catch(() => {}); } catch (e) {}
}

function _convoFaceSwap(mode) {
  const vid = document.getElementById('convo-face-idle');
  if (!vid) return;
  // Still-image wireframe face (locked 2026-06-30): there is no video to swap —
  // the idle/speaking cue is carried entirely by the CSS .speaking class (faster
  // breathe + brighter glow). Never assign an .mp4 to an <img>. No-op here.
  if (vid.tagName !== 'VIDEO') return;
  if (_faceMode === mode) {                       // already on the right clip
    if (vid.paused) { try { vid.play(); } catch (e) {} }
    return;
  }
  _faceMode = mode;
  const src = _FACE_SRC[mode] || _FACE_SRC.idle;
  // Resolve against the current URL so a bare relative path compares cleanly
  // and we never reload the same file we are already playing.
  try {
    if (vid.src && vid.src.indexOf(src) !== -1) {
      if (vid.paused) { try { vid.play(); } catch (e) {} }
      return;
    }
    vid.src = src;            // assigning src triggers the load of the new clip
    const p = vid.play();     // loop/muted/playsinline attributes persist
    if (p && p.catch) p.catch(() => {});
  } catch (e) {}
}

function _convoFaceSync(state) {
  const face = document.getElementById('convo-face');
  if (!face) return;
  const speaking = (state === 'speaking');
  face.classList.toggle('speaking', speaking);     // glow cue
  _convoFaceSwap(speaking ? 'speaking' : 'idle');  // swap the single decoder
}

// Pause the single loop on exit so a hidden overlay is not decoding in the
// background (saves GPU/battery when conversation mode is closed). Reset to idle
// so the next entry starts on the idle clip.
function _convoFaceStop() {
  const face = document.getElementById('convo-face');
  if (face) face.classList.remove('speaking');
  const vid = document.getElementById('convo-face-idle');
  if (vid) { try { vid.pause(); } catch (e) {} }
  _faceMode = null;   // force the next sync to re-evaluate the clip on re-entry
}

function _convoLabel(text) {
  const lbl = document.getElementById('convo-status-label');
  if (lbl) lbl.textContent = text;
}

// ── Per-officer wake words + barge-in names ────────────────────────────────
// Metadata comes from the bridge (/voice/voices). _crewEntry resolves the
// roster row; the builders turn its `wake` / `names` lists into tolerant
// matchers for Chrome SpeechRecognition transcripts.
function _crewEntry(id) {
  return CREW_VOICES_LIST.find(x => x.id === (id || CREW_VOICE)) || null;
}

// Canonical wake phrase for the IDLE prompt, e.g. "Vector".
function convoWakePhrase(id) {
  const e = _crewEntry(id);
  return (e && e.wake && e.wake[0]) || crewName(id);
}

// IDLE status prompt — the selected officer's own wake phrase. (Bare
// "Computer" is a ship-wide wake word for the comm chat, not for this loop.)
// On mobile we skip SpeechRecognition entirely (see _convoStartWakeListener),
// so the prompt reflects tap-only interaction.
function _convoIdlePrompt(id) {
  if (_isMobileDevice()) return 'TAP THE AVATAR TO SPEAK';
  return `SAY "${convoWakePhrase(id).toUpperCase()}" — OR TAP THE AVATAR`;
}

function _convoEsc(s) {
  return String(s).toLowerCase().trim()
                  .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
                  .replace(/\s+/g, '[\\s,]+');
}

// Wake regex — the selected officer's own wake phrases, with an optional
// "hey/hi/hello/ok/okay/yo" address prefix. (Bare "Computer" wakes the
// ship-wide comm chat instead — see _wakeWordRegex — not this loop.)
function _convoWakeRegex(id) {
  const e = _crewEntry(id);
  const phrases = (e && e.wake && e.wake.length) ? e.wake : [crewName(id)];
  const body = phrases.map(_convoEsc).join('|');
  return new RegExp(`\\b(?:hey|hi|hello|ok|okay|yo)?[\\s,]*(?:${body})\\b`, 'i');
}

// Barge-in regex — the officer's name + an interrupt verb (either order), a
// bare "hey <officer>", or any "computer stop"-style command.
function _convoInterruptRegex(id) {
  const e = _crewEntry(id);
  const names = (e && e.names && e.names.length) ? e.names : [(id || CREW_VOICE)];
  const n = names.map(_convoEsc).join('|');
  const verb = '(?:stop|hold[\\s,]*on|hold[\\s,]*up|wait|hang[\\s,]*on|' +
               'one[\\s,]*(?:second|moment)|enough|that[\\s,]*is[\\s,]*enough|' +
               'stop[\\s,]*talking|shut[\\s,]*up|quiet|cancel|belay[\\s,]*that)';
  return new RegExp(
    `\\b(?:${n})\\b[\\s,]*${verb}` +           // "vector, hold on"
    `|\\b${verb}[\\s,]+(?:${n})\\b` +          // "hold on vector"
    `|\\b(?:hey|ok|okay)[\\s,]+(?:${n})\\b` +  // "hey vector"
    `|\\bcomputer[\\s,]*${verb}`,              // "computer stop"
    'i');
}

// ── IDLE — armed, waiting for the officer's wake word or a tap ─────────────
function convoGoIdle() {
  CONVO.gen++;   // invalidate any in-flight turn so it cannot re-open the mic
  _convoClearIdleTimer();
  clearTimeout(CONVO.maxTimer);
  _convoStopInterruptListener();
  _convoReleaseMic();              // hand the mic to the wake-word recognizer
  convoState('idle');
  _convoLabel(_convoIdlePrompt(CREW_VOICE));
  _convoStartWakeListener();
}

// Full teardown — every recognizer, the recorder, playback, and the mic graph.
function convoTeardown() {
  _convoClearIdleTimer();
  clearTimeout(CONVO.maxTimer);
  _convoStopWakeListener();
  _convoStopInterruptListener();
  if (CONVO.abort)        { try { CONVO.abort.abort(); } catch (e) {} CONVO.abort = null; }
  clearTtsQueue();
  if (CONVO.currentAudio) { try { CONVO.currentAudio.pause(); } catch (e) {} CONVO.currentAudio = null; }
  _convoReleaseMic();
  if (CONVO.audioCtx)     { try { CONVO.audioCtx.close(); } catch (e) {} CONVO.audioCtx = null; }
  CONVO.calibrated = false;
  convoState('off');
}

// ── Voice stop — "<officer> stop" / "computer stop" ────────────────────────
// _stopPhraseRegex matches a hard stop addressed to an officer or the
// computer. It is name-anchored, so a bare "stop" mid-sentence never halts
// playback. _stopAllVoice() then kills every playback path — the streaming
// per-sentence speech, the audio-chunk queue, per-bubble 🔊, and any
// conversation-mode reply + its in-flight fetch.
function _stopPhraseRegex() {
  const names = 'computer|data|vector|probe|atlas|sentinel|echo'
              + '|counselor[\\s,]+echo|number[\\s,]+one|la[\\s,]+forge';
  const verb  = '(?:stop|stop[\\s,]+talking|cancel|quiet|silence|enough'
              + '|that[\\s,]+is[\\s,]+enough|belay[\\s,]+that|shut[\\s,]+up)';
  return new RegExp(
    `\\b(?:${names})\\b[\\s,]*${verb}\\b|\\b${verb}[\\s,]+(?:${names})\\b`, 'i');
}

function _stopAllVoice() {
  _speakStream.cancelled = true;
  _speakStream.active = false;
  _speakStream.queue.length = 0;
  clearTtsQueue();                                // streaming + conversation audio queue
  if (typeof stopTts === 'function') stopTts();   // per-bubble 🔊 playback
  if (CONVO.currentAudio) { try { CONVO.currentAudio.pause(); } catch (e) {} CONVO.currentAudio = null; }
  if (CONVO.abort) { try { CONVO.abort.abort(); } catch (e) {} CONVO.abort = null; }
  BRAIN.outputRms = 0;
  BRAIN.outputFreq = null;
}

// Kept for the STOP_LISTENER barge-in recognizer (per-bubble 🔊 playback).
function abortAllSpeech() {
  _stopAllVoice();
  addLog('Speech aborted — "computer stop"');
}

// Dedicated barge-in recognizer. Runs only while TTS audio is playing AND
// conversation mode is OFF — conversation mode already barges in on any voice
// via its VAD loop, and the bridge catches "computer stop" server-side there.
// On hearing the stop phrase it aborts playback instantly, with no server trip.
const STOP_LISTENER = {
  rec:    null,
  active: false,
  phrase: /\b(?:computer[\s,]*)?stop\b|\bbelay that\b|\bstop talking\b|\bcomputer cancel\b/i,
};

function _startStopListener() {
  // Hard block on mobile/tablet. Mobile Chrome ignores
  // SpeechRecognition.continuous, so this barge-in recognizer ends every few
  // seconds and onend (below) restarts it — re-grabbing the mic during every
  // TTS playback and triggering a perpetual OS "site is using your microphone"
  // notification that thrashes audio output. On mobile view the Captain taps
  // to stop playback instead; voice input there runs ONLY from the dictation
  // button. This guard, paired with the one in _startWakeListener(), means no
  // recognizer auto-grabs the mic on a phone.
  if (_isMobileDevice()) return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR || STOP_LISTENER.active || CONVO.active) return;
  _stopWakeListener();                   // never run two recognizers at once
  let rec;
  try { rec = new SR(); } catch (e) { _restartWakeIfEnabled(); return; }
  rec.continuous     = true;
  rec.interimResults = true;             // interim → catch "stop" before the pause
  rec.lang           = 'en-US';
  rec.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const heard = event.results[i][0].transcript || '';
      if (_stopPhraseRegex().test(heard)) {
        addLog(`Voice stop heard: "${heard.trim().slice(0, 40)}"`);
        abortAllSpeech();
        _stopStopListener();
        return;
      }
    }
  };
  rec.onerror = (e) => {
    if (e.error === 'not-allowed') _stopStopListener();
  };
  rec.onend = () => {
    // Web Speech auto-stops periodically; restart while playback is still live.
    if (STOP_LISTENER.active) { try { rec.start(); } catch (e) {} }
  };
  try {
    rec.start();
    STOP_LISTENER.rec    = rec;
    STOP_LISTENER.active = true;
  } catch (e) { /* already running */ }
}

function _stopStopListener() {
  if (!STOP_LISTENER.active && !STOP_LISTENER.rec) return;
  STOP_LISTENER.active = false;
  if (STOP_LISTENER.rec) {
    STOP_LISTENER.rec.onend   = null;
    STOP_LISTENER.rec.onerror = null;
    try { STOP_LISTENER.rec.abort(); } catch (e) {}
    STOP_LISTENER.rec = null;
  }
  _restartWakeIfEnabled();                // hand the mic back to the wake listener
}

// ── Mic graph ──────────────────────────────────────────────────────────────
// The AudioContext lives for the whole overlay session; the mic STREAM is
// (re)acquired only for LISTENING and released in every other state, so the
// VAD recorder and a SpeechRecognition instance never own the mic at once —
// the old design's hard-won rule. Re-acquiring getUserMedia after the first
// grant costs ~150ms and never re-prompts.
async function _convoEnsureMic() {
  if (!CONVO.audioCtx) {
    CONVO.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  try { await CONVO.audioCtx.resume(); } catch (e) {}
  if (CONVO.stream && CONVO.processor) return true;
  try {
    CONVO.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,   // reject the officer's own TTS coming back in
        noiseSuppression: true,   // reject steady room noise
        autoGainControl:  true,
      },
      video: false,
    });
  } catch (e) {
    addLog('Convo mic denied: ' + (e.message || e));
    return false;
  }
  const ctx = CONVO.audioCtx;
  const source = ctx.createMediaStreamSource(CONVO.stream);

  // Silent sink — runs the processor without echoing the mic to the speakers.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  sink.connect(ctx.destination);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  source.connect(proc);
  proc.connect(sink);

  try {
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.5;
    source.connect(analyser);
    BRAIN.inputFreq = new Uint8Array(analyser.frequencyBinCount);
    CONVO.micAnalyser = analyser;
  } catch (e) { CONVO.micAnalyser = null; }

  CONVO.source    = source;
  CONVO.processor = proc;
  return true;
}

// Stop the mic stream + per-turn nodes; keep CONVO.audioCtx alive.
function _convoReleaseMic() {
  clearTimeout(CONVO.maxTimer);
  if (CONVO.mediaRecorder) {
    CONVO.mediaRecorder.ondataavailable = null;
    CONVO.mediaRecorder.onstop = null;
    if (CONVO.mediaRecorder.state !== 'inactive') {
      try { CONVO.mediaRecorder.stop(); } catch (e) {}
    }
    CONVO.mediaRecorder = null;
  }
  if (CONVO.processor) {
    CONVO.processor.onaudioprocess = null;
    try { CONVO.processor.disconnect(); } catch (e) {}
    CONVO.processor = null;
  }
  if (CONVO.source) { try { CONVO.source.disconnect(); } catch (e) {} CONVO.source = null; }
  if (CONVO.stream) { CONVO.stream.getTracks().forEach(t => t.stop()); CONVO.stream = null; }
  CONVO.micAnalyser = null;
}

// One-time ambient-noise calibration. The threshold is deliberately less
// sensitive than the old design (which tripped on background noise): the old
// floor was 0.003, the new floor is 0.014, multiplier 4×.
async function _convoCalibrate() {
  if (CONVO.calibrated || !CONVO.processor) return;
  _convoLabel('CALIBRATING…');
  const samples = [];
  await new Promise(resolve => {
    let n = 0;
    CONVO.processor.onaudioprocess = e => {
      const d = e.inputBuffer.getChannelData(0);
      let s = 0; for (let i = 0; i < d.length; i++) s += d[i] * d[i];
      samples.push(Math.sqrt(s / d.length));
      if (++n >= 24) resolve();          // ~24 buffers ≈ 1.1s
    };
  });
  CONVO.processor.onaudioprocess = null;
  const ambient = samples.reduce((a, b) => a + b, 0) / (samples.length || 1);
  CONVO.threshold  = Math.min(0.05, Math.max(0.014, ambient * 4));
  CONVO.calibrated = true;
  addLog(`Convo calibrated — ambient=${ambient.toFixed(4)} threshold=${CONVO.threshold.toFixed(4)}`);
}

// ── LISTENING — open the mic, record, watch for 2.5s of sustained silence ──
async function convoStartListening() {
  if (!BRAIN.active) return;
  const gen = ++CONVO.gen;               // stale turns compare against this
  _convoStopWakeListener();
  _convoStopInterruptListener();
  if (CONVO.abort) { try { CONVO.abort.abort(); } catch (e) {} CONVO.abort = null; }

  const ok = await _convoEnsureMic();
  if (!ok || !BRAIN.active || CONVO.gen !== gen) {
    if (!ok && BRAIN.active && CONVO.gen === gen) {
      convoState('idle');
      _convoLabel('MIC ACCESS DENIED — ENABLE IT, THEN TAP THE AVATAR');
    }
    return;
  }
  await _convoCalibrate();
  if (!BRAIN.active || CONVO.gen !== gen) return;

  CONVO.speechDetected = false;
  CONVO.speechStart    = null;
  CONVO.silenceStart   = null;
  CONVO.aboveStart     = null;
  clearTimeout(CONVO.maxTimer);

  // Fresh recorder each turn so the first chunk carries the WebM container header.
  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus' : 'audio/ogg;codecs=opus';
  CONVO.chunks = [];
  CONVO.mediaRecorder = new MediaRecorder(CONVO.stream, { mimeType: mime });
  CONVO.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) CONVO.chunks.push(e.data); };
  CONVO.mediaRecorder.start(100);

  CONVO.processor.onaudioprocess = _convoVadTick;
  convoState('listening', 'LISTENING — SPEAK NOW');
  _convoArmIdleTimer();
}

// If the Captain says nothing at all, drop back to the wake-word IDLE state
// instead of recording background noise forever.
function _convoArmIdleTimer() {
  _convoClearIdleTimer();
  CONVO.idleTimer = setTimeout(() => {
    if (CONVO.state === 'listening' && !CONVO.speechDetected) {
      addLog('Convo idle — no speech; re-armed for wake word');
      convoGoIdle();
    }
  }, CONVO.IDLE_TIMEOUT_MS);
}
function _convoClearIdleTimer() {
  if (CONVO.idleTimer) { clearTimeout(CONVO.idleTimer); CONVO.idleTimer = null; }
}

// VAD — runs on every audio buffer while state === 'listening'.
function _convoVadTick(e) {
  if (CONVO.state !== 'listening') return;
  const d = e.inputBuffer.getChannelData(0);
  let s = 0; for (let i = 0; i < d.length; i++) s += d[i] * d[i];
  const rms = Math.sqrt(s / d.length);

  BRAIN.inputRms = rms;
  if (CONVO.micAnalyser && BRAIN.inputFreq) CONVO.micAnalyser.getByteFrequencyData(BRAIN.inputFreq);

  const micBar = document.getElementById('mic-bar');
  if (micBar) {
    micBar.style.width = Math.min(100, rms * 2000) + '%';
    micBar.style.background = rms > CONVO.threshold ? 'var(--data-red)' : 'var(--data-green)';
  }

  if (!CONVO.speechDetected) {
    // Waiting for speech — require sustained energy so a click cannot trigger.
    if (rms > CONVO.threshold) {
      if (!CONVO.aboveStart) CONVO.aboveStart = Date.now();
      if (Date.now() - CONVO.aboveStart >= CONVO.SPEECH_HOLD_MS) {
        CONVO.speechDetected = true;
        CONVO.speechStart    = Date.now();
        CONVO.silenceStart   = null;
        CONVO.aboveStart     = null;
        _convoClearIdleTimer();            // real speech — cancel the idle drop
        BRAIN.state = 'recording';
        _convoLabel('RECORDING…');
        CONVO.maxTimer = setTimeout(convoFlush, CONVO.MAX_SPEECH_MS);
      }
    } else {
      CONVO.aboveStart = null;
    }
  } else {
    // Speech in progress — submit after SILENCE_HOLD_MS of sustained quiet.
    if (rms < CONVO.threshold) {
      if (!CONVO.silenceStart) CONVO.silenceStart = Date.now();
      const left = (CONVO.SILENCE_HOLD_MS - (Date.now() - CONVO.silenceStart)) / 1000;
      _convoLabel(`RECORDING…  ${left > 0 ? left.toFixed(1) : '0.0'}s`);
      if (left <= 0) convoFlush();
    } else {
      CONVO.silenceStart = null;
      _convoLabel('RECORDING…');
    }
  }
}

// End the current utterance and hand the recorded audio to the bridge. The
// chunk snapshot is taken inside onstop so it includes the final flushed
// chunk, then the mic is released before the SpeechRecognition-owned states.
function convoFlush() {
  if (CONVO.state !== 'listening' || !CONVO.speechDetected) return;
  clearTimeout(CONVO.maxTimer);
  _convoClearIdleTimer();
  if (CONVO.processor) CONVO.processor.onaudioprocess = null;
  const duration = Date.now() - (CONVO.speechStart || Date.now());
  const gen = CONVO.gen;
  const rec = CONVO.mediaRecorder;
  if (!rec) { convoStartListening(); return; }
  rec.onstop = () => {
    const captured = [...CONVO.chunks];   // includes the final flushed chunk
    _convoReleaseMic();                   // free the mic for the barge-in recognizer
    if (CONVO.gen !== gen || !BRAIN.active) return;
    if (duration < CONVO.MIN_SPEECH_MS) { convoStartListening(); return; }
    convoProcess(captured, gen);
  };
  if (rec.state !== 'inactive') { try { rec.stop(); } catch (e) { rec.onstop(); } }
  else rec.onstop();
}

// Barge-in — abort the in-flight reply + playback, then loop straight back to
// LISTENING. Fired by the barge-in recognizer on "Vector, hold on" /
// "computer stop" / etc., and by a tap on the sphere while the officer speaks.
function convoBargeIn() {
  if (CONVO.abort) { try { CONVO.abort.abort(); } catch (e) {} CONVO.abort = null; }
  clearTtsQueue();
  if (CONVO.currentAudio) { try { CONVO.currentAudio.pause(); } catch (e) {} CONVO.currentAudio = null; }
  BRAIN.outputRms = 0; BRAIN.outputFreq = null;
  _convoStopInterruptListener();
  addLog('Convo barge-in — listening again');
  convoStartListening();
}

// ── Wake-word recognizer — runs only in IDLE ───────────────────────────────
function _convoStartWakeListener() {
  // Hard block on mobile/tablet. Mobile Chrome ignores SpeechRecognition.continuous,
  // so the recognizer ends every few seconds and rec.onend restarts it — thrashing
  // the mic and triggering a perpetual OS "site is using your microphone" notification.
  // On mobile the Captain taps the sphere to start each turn instead of speaking a
  // wake phrase. _convoOverlayClick already calls convoStartListening() on an idle tap.
  if (_isMobileDevice()) return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR || CONVO.wakeRec) return;
  let rec;
  try { rec = new SR(); } catch (e) { return; }
  rec.continuous     = true;
  rec.interimResults = false;     // a finalized phrase = a real pause after the word
  rec.lang           = 'en-US';
  const re = _convoWakeRegex(CREW_VOICE);
  rec.onresult = (ev) => {
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      if (!ev.results[i].isFinal) continue;
      const heard = (ev.results[i][0].transcript || '').toLowerCase();
      if (re.test(heard)) {
        addLog(`Convo wake: "${heard.trim().slice(0, 40)}"`);
        _convoStopWakeListener();
        convoStartListening();
        return;
      }
    }
  };
  rec.onerror = (e) => {
    if (e.error === 'not-allowed') {
      _convoStopWakeListener();
      convoState('idle');
      _convoLabel('MIC ACCESS DENIED — ENABLE IT, THEN TAP THE AVATAR');
    }
  };
  rec.onend = () => {
    // Chrome auto-stops the recognizer every ~60s; restart while still idle.
    if (CONVO.wakeRec === rec && CONVO.state === 'idle') {
      try { rec.start(); } catch (e) {}
    }
  };
  try { rec.start(); CONVO.wakeRec = rec; } catch (e) {}
}

function _convoStopWakeListener() {
  const rec = CONVO.wakeRec;
  if (!rec) return;
  CONVO.wakeRec = null;
  rec.onend = null; rec.onerror = null; rec.onresult = null;
  try { rec.abort(); } catch (e) {}
}

// ── Barge-in recognizer — runs in THINKING + SPEAKING ──────────────────────
function _convoStartInterruptListener() {
  // Hard block on mobile/tablet — same reason as _convoStartWakeListener above.
  // On mobile the Captain taps the sphere to barge in instead (handled by
  // _convoOverlayClick → convoBargeIn() when state is 'speaking').
  if (_isMobileDevice()) return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR || CONVO.interruptRec) return;
  let rec;
  try { rec = new SR(); } catch (e) { return; }
  rec.continuous     = true;
  rec.interimResults = true;      // catch the interrupt before the user pauses
  rec.lang           = 'en-US';
  const re = _convoInterruptRegex(CREW_VOICE);
  rec.onresult = (ev) => {
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const heard = ev.results[i][0].transcript || '';
      // Hard stop — "<officer> stop" / "computer stop": halt playback and
      // drop to IDLE so the Captain must say a wake word to resume.
      if (_stopPhraseRegex().test(heard)) {
        addLog(`Convo stop: "${heard.trim().slice(0, 40)}" — standing by for wake word`);
        _stopAllVoice();
        convoGoIdle();
        return;
      }
      // Conversational barge-in ("Vector, hold on") — cut in and resume listening.
      if (re.test(heard)) {
        addLog(`Convo interrupt: "${heard.trim().slice(0, 40)}"`);
        convoBargeIn();
        return;
      }
    }
  };
  rec.onerror = () => {};
  rec.onend = () => {
    if (CONVO.interruptRec === rec &&
        (CONVO.state === 'thinking' || CONVO.state === 'speaking')) {
      try { rec.start(); } catch (e) {}
    }
  };
  try { rec.start(); CONVO.interruptRec = rec; } catch (e) {}
}

function _convoStopInterruptListener() {
  const rec = CONVO.interruptRec;
  if (!rec) return;
  CONVO.interruptRec = null;
  rec.onend = null; rec.onerror = null; rec.onresult = null;
  try { rec.abort(); } catch (e) {}
}

// ── TTS audio queue — plays sentence chunks back-to-back so the user hears
// Data start talking as soon as sentence 1 is synthesized, while sentence 2
// is still being generated. Drops perceived latency by ~50% vs the old
// full-pipeline-then-play approach.
const _ttsQueue = { items: [], playing: false, current: null, analyser: null, sampleTimer: null };

function enqueueTtsChunk(b64, mime) {
  _ttsQueue.items.push({ b64, mime });
  if (!_ttsQueue.playing) _playNextTtsChunk();
}

function clearTtsQueue() {
  _ttsQueue.items.length = 0;
  if (_ttsQueue.sampleTimer) { clearInterval(_ttsQueue.sampleTimer); _ttsQueue.sampleTimer = null; }
  if (_ttsQueue.current) { try { _ttsQueue.current.pause(); } catch {} _ttsQueue.current = null; }
  _ttsQueue.playing = false;
  BRAIN.outputRms = 0;
  BRAIN.outputFreq = null;
  _stopStopListener();   // playback over — release the barge-in recognizer
}

function _playNextTtsChunk() {
  const next = _ttsQueue.items.shift();
  if (!next) {
    _ttsQueue.playing = false;
    BRAIN.outputRms = 0;
    BRAIN.outputFreq = null;
    _stopStopListener();   // queue drained — release the barge-in recognizer
    return;
  }
  _ttsQueue.playing = true;
  // Arm the voice barge-in recognizer for global-comms playback so the
  // Captain can cut it off by saying "computer stop". Conversation mode runs
  // its own interrupt listener, so skip it there. _startStopListener()
  // self-guards, so calling it on every chunk is harmless.
  if (!CONVO.active) _startStopListener();

  const bytes = Uint8Array.from(atob(next.b64), c => c.charCodeAt(0));
  const url   = URL.createObjectURL(new Blob([bytes], { type: next.mime || 'audio/wav' }));
  const audio = new Audio(url);
  audio.playbackRate = PLAYBACK_RATE;
  _ttsQueue.current = audio;
  CONVO.currentAudio = audio;       // so convoBargeIn()/convoTeardown() can kill it

  // Per-chunk analyser feed for the BRAIN visualization
  let sampleTimer = null;
  try {
    const ctx = BRAIN.getAudioCtx();
    if (ctx) {
      if (ctx.state === 'suspended') ctx.resume();   // mobile: graph is muted while suspended
      const src = ctx.createMediaElementSource(audio);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.6;
      const gain = ctx.createGain();
      gain.gain.value = TTS_GAIN;
      src.connect(analyser);
      analyser.connect(gain);
      gain.connect(ctx.destination);
      const buf     = new Uint8Array(analyser.frequencyBinCount);
      const freqBuf = new Uint8Array(analyser.frequencyBinCount);
      BRAIN.outputFreq = freqBuf;
      sampleTimer = setInterval(() => {
        analyser.getByteTimeDomainData(buf);
        analyser.getByteFrequencyData(freqBuf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        BRAIN.outputRms = Math.sqrt(sum / buf.length);
      }, 33);
      _ttsQueue.sampleTimer = sampleTimer;
    }
  } catch (e) { /* analyser optional */ }

  const cleanup = () => {
    URL.revokeObjectURL(url);
    if (sampleTimer) clearInterval(sampleTimer);
    _ttsQueue.sampleTimer = null;
    _ttsQueue.current = null;
    // Hand off to the next chunk (or stop if queue drained)
    _playNextTtsChunk();
  };
  audio.onended = cleanup;
  audio.onerror = cleanup;
  audio.play().catch(cleanup);
}

// ── THINKING → SPEAKING — upload the audio, stream the reply, play it back ─
// `gen` is the turn token captured at convoFlush. If a barge-in starts a new
// turn (bumping CONVO.gen) this run becomes stale and bows out without looping.
async function convoProcess(chunks, gen) {
  if (!BRAIN.active || CONVO.gen !== gen) return;
  convoState('thinking', `${crewName(CREW_VOICE).toUpperCase()} IS THINKING…`);
  _convoStartInterruptListener();           // "computer stop" works during the wait
  addLog('Transcribing…');

  const mime = chunks[0]?.type || 'audio/webm';
  const blob = new Blob(chunks, { type: mime });

  // One growing assistant bubble — sentences stream in but render into it.
  let dataMsgEl = null, dataConvoTurn = null, fullResponse = '', spoke = false;
  let crewTurnName = crewName(CREW_VOICE);
  let convoVoiceErr = '';
  CONVO.abort = new AbortController();

  try {
    const res = await fetch(`${API_BASE}/speak_stream?voice=${encodeURIComponent(CREW_VOICE)}`, {
      method:  'POST',
      headers: { 'Content-Type': mime },
      body:    blob,
      signal:  CONVO.abort.signal,
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '', stop = false;

    while (!stop) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;                              // SSE events end with a blank line
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (raw.startsWith(':')) continue;  // keepalive comment
        let evt = 'message', dataLine = '';
        for (const line of raw.split('\n')) {
          if      (line.startsWith('event: ')) evt = line.slice(7).trim();
          else if (line.startsWith('data: '))  dataLine = line.slice(6);
        }
        if (!dataLine) continue;
        let payload = {};
        try { payload = JSON.parse(dataLine); } catch { continue; }

        if (evt === 'crew') {
          if (payload.name) crewTurnName = payload.name;
        } else if (evt === 'interrupt') {
          // Server STT heard "computer stop" — discard, no reply expected.
          addLog('Voice interrupt — message discarded');
          stop = true;
          break;
        } else if (evt === 'user_text') {
          appendMessage('user', payload.text);
          _convoAppendTurn('user', payload.text);
        } else if (evt === 'text_chunk') {
          fullResponse = (fullResponse + ' ' + payload.text).trim();
          const shown = (crewTurnName && crewTurnName !== 'Data')
            ? `${crewTurnName}: ${fullResponse}` : fullResponse;
          if (!dataMsgEl) {
            dataMsgEl = appendMessage('data', fullResponse);
            if (BRAIN.active) {
              const t = document.getElementById('convo-transcript');
              if (t) {
                dataConvoTurn = document.createElement('div');
                dataConvoTurn.className = 'convo-turn-data';
                dataConvoTurn.textContent = shown;
                t.appendChild(dataConvoTurn);
                while (t.children.length > 6) t.removeChild(t.firstChild);
              }
            }
          } else {
            const el = dataMsgEl.querySelector('.text.md-content');
            if (el) el.innerHTML = renderMarkdown(fullResponse);
            if (dataConvoTurn) dataConvoTurn.textContent = shown;
          }
        } else if (evt === 'audio_chunk') {
          if (!spoke) {
            spoke = true;
            convoState('speaking',
              `${crewTurnName.toUpperCase()} SPEAKING — SAY "${crewTurnName.toUpperCase()}, HOLD ON" TO CUT IN`);
          }
          enqueueTtsChunk(payload.audio_b64, payload.audio_mime);
        } else if (evt === 'done') {
          addLog(`${crewTurnName}: ${(payload.response_text || '').substring(0, 40)}…`);
        } else if (evt === 'error') {
          const err = payload.error || 'unknown';
          if (err === 'no_speech') {
            addLog('No speech detected');
          } else if (err === 'warming_up') {
            addLog(payload.message || 'Voice models warming up');
            if (BRAIN.active) _convoLabel('VOICE MODELS WARMING UP — ONE MOMENT…');
          } else {
            // Never silently loop on a hard error — surface why.
            addLog(`Voice error: ${err}`);
            convoVoiceErr = err;
            if (BRAIN.active) {
              _convoLabel(/unavailable/i.test(err)
                ? 'VOICE COMPONENTS NOT INSTALLED'
                : `VOICE ERROR — ${String(err).slice(0, 40)}`);
            }
          }
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') return;    // convoBargeIn owns the next transition
    addLog(`Bridge error: ${e.message || e}`);
  }
  CONVO.abort = null;

  // Wait for queued sentences to finish before re-opening the mic, so it does
  // not capture the officer's own voice as the next utterance.
  while (_ttsQueue.playing && BRAIN.active && CONVO.gen === gen) {
    await new Promise(r => setTimeout(r, 100));
  }
  if (!spoke && CONVO.gen === gen) addLog('No spoken reply received');

  // Voice stack reported unavailable mid-conversation. The deps are baked into
  // the runtime, so this means the baked-in wheels failed to import — surface
  // the reason and stop looping rather than re-opening the mic on silence.
  if (convoVoiceErr && /unavailable/i.test(convoVoiceErr) && BRAIN.active && CONVO.gen === gen) {
    setConvoBrainState('idle', `VOICE UNAVAILABLE - ${convoVoiceErr.slice(0, 90)}`);
    return;
  }

  // Loop — unless a barge-in (which bumps CONVO.gen) already moved us on.
  if (BRAIN.active && CONVO.gen === gen) convoStartListening();
}

const PLAYBACK_RATE = 1.0;   // natural pace. 1.15 was 15% faster than Data should sound.
// >1.0 amplifies via WebAudio GainNode (HTMLAudio.volume caps at 1.0). 1.5 ≈
// +3.5 dB; F5/XTTS output is conservative so this stays clean of clipping.
const TTS_GAIN = 1.5;

function playBase64Audio(b64, mime) {
  return new Promise(resolve => {
    const bytes  = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const blob   = new Blob([bytes], { type: mime || 'audio/wav' });
    const url    = URL.createObjectURL(blob);
    const audio  = new Audio(url);
    audio.playbackRate = PLAYBACK_RATE;
    CONVO.currentAudio = audio;
    _startStopListener();   // arm "computer stop" barge-in for this playback

    // Brain visualization: route playback through an AnalyserNode so the
    // Jarvis-style sphere pulses with Data's voice while he is speaking.
    let analyser = null, sampleTimer = null;
    try {
      const ctx = BRAIN.getAudioCtx();
      if (ctx) {
        if (ctx.state === 'suspended') ctx.resume();   // mobile: graph is muted while suspended
        const src = ctx.createMediaElementSource(audio);
        analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.6;
        const gain = ctx.createGain();
        gain.gain.value = TTS_GAIN;
        src.connect(analyser);
        analyser.connect(gain);
        gain.connect(ctx.destination);
        const buf      = new Uint8Array(analyser.frequencyBinCount);
        const freqBuf  = new Uint8Array(analyser.frequencyBinCount);
        BRAIN.outputFreq = freqBuf;
        sampleTimer = setInterval(() => {
          analyser.getByteTimeDomainData(buf);
          analyser.getByteFrequencyData(freqBuf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) {
            const v = (buf[i] - 128) / 128;
            sum += v * v;
          }
          BRAIN.outputRms = Math.sqrt(sum / buf.length);
        }, 33);
      }
    } catch (e) { /* analyser optional — playback still works without it */ }

    const cleanup = () => {
      URL.revokeObjectURL(url);
      if (sampleTimer) clearInterval(sampleTimer);
      BRAIN.outputRms = 0;
      BRAIN.outputFreq = null;
      _stopStopListener();   // playback finished — release the recognizer
      resolve();
    };
    audio.onended = cleanup;
    audio.onerror = cleanup;
    audio.play().catch(cleanup);
  });
}

// ── Mobile audio unlock ────────────────────────────────────────────────────
// Browsers create AudioContexts in the "suspended" state until a user gesture
// resumes them. Every TTS path routes its <audio> element through
// BRAIN.getAudioCtx() via createMediaElementSource(), so a suspended context
// means total silence even though audio.play() resolves. Resume the context on
// the first tap / click / key anywhere on the dashboard — one gesture unlocks
// audio for the whole session. Critical on mobile, harmless on desktop.
let _audioUnlocked = false;
const _AUDIO_UNLOCK_EVENTS = ['pointerdown', 'touchend', 'click', 'keydown'];
function _unlockAudio() {
  if (_audioUnlocked) return;
  const ctx = BRAIN.getAudioCtx();
  if (!ctx) return;
  const finish = () => {
    _audioUnlocked = true;
    _AUDIO_UNLOCK_EVENTS.forEach(ev =>
      window.removeEventListener(ev, _unlockAudio, true));
  };
  if (ctx.state === 'suspended') {
    ctx.resume().then(() => {
      // iOS Safari needs a real buffer played during the gesture to fully unlock.
      try {
        const src = ctx.createBufferSource();
        src.buffer = ctx.createBuffer(1, 1, 22050);
        src.connect(ctx.destination);
        src.start(0);
      } catch (_) {}
      finish();
    }).catch(() => {});
  } else {
    finish();
  }
}
_AUDIO_UNLOCK_EVENTS.forEach(ev =>
  window.addEventListener(ev, _unlockAudio, true));

// ── Test Record (bypass VAD) ──────────────────────────────
async function testRecord() {
  const btn = document.getElementById('test-record-btn');
  btn.disabled = true;
  btn.textContent = '⏺ 3...';
  addLog('TEST: recording 3 seconds...');

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    addLog('TEST: mic denied — ' + e.message);
    btn.disabled = false;
    btn.textContent = '⏺ TEST 3s';
    return;
  }

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus' : 'audio/ogg;codecs=opus';
  const recorder = new MediaRecorder(stream, { mimeType });
  const chunks = [];
  recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };

  recorder.start(100);

  for (let i = 2; i >= 0; i--) {
    await new Promise(r => setTimeout(r, 1000));
    btn.textContent = `⏺ ${i}...`;
  }

  recorder.onstop = async () => {
    stream.getTracks().forEach(t => t.stop());
    btn.textContent = 'SENDING...';
    addLog(`TEST: captured ${chunks.length} chunks`);

    const blob = new Blob(chunks, { type: mimeType });
    addLog(`TEST: blob size=${blob.size} bytes, type=${mimeType}`);
    console.log('[TEST] blob:', blob.size, 'bytes', mimeType);

    try {
      const res = await fetch(`${API_BASE}/speak`, {
        method: 'POST',
        headers: { 'Content-Type': mimeType },
        body: blob,
      });
      console.log('[TEST] /speak HTTP', res.status);
      const data = await res.json();
      console.log('[TEST] response:', data);
      addLog(`TEST: transcribed="${data.user_text}"`);
      addLog(`TEST: Data said="${(data.response_text||'').substring(0,50)}"`);
      if (data.user_text)     appendMessage('user', data.user_text);
      if (data.response_text) appendMessage('data', data.response_text);
      if (data.audio_b64) {
        addLog('TEST: playing audio...');
        await playBase64Audio(data.audio_b64, data.audio_mime);
        addLog('TEST: audio done');
      }
      if (data.error) addLog(`TEST ERROR: ${data.error}`);
    } catch (e) {
      console.error('[TEST] fetch error:', e);
      addLog('TEST: fetch failed — ' + e.message);
    }

    btn.disabled = false;
    btn.textContent = '⏺ TEST 3s';
  };

  recorder.stop();
}

// ── Dictation (mic → text box) ────────────────────────────
let _dictRecorder = null;
let _dictChunks   = [];
let _dictActive   = false;
let _dictAborted  = false;   // set true by Esc to skip transcribe on stop

// ── Browser-STT fallback (Web Speech API) ──────────────────
// The core build ships without the server Whisper stack, so /transcribe is
// unavailable. When the bridge reports no server STT, dictation runs on the
// browser's own SpeechRecognition (Chrome/Edge) and feeds final transcripts
// straight into the target input.
let _sttServerAvailable = null;   // null = not probed yet
let _browserRec = null;           // active SpeechRecognition session

async function _serverSttAvailable() {
  if (_sttServerAvailable !== null) return _sttServerAvailable;
  try {
    const r = await fetch(`${API_BASE}/voice/status`);
    const d = await r.json();
    _sttServerAvailable = !!(d.stt_available ?? d.stt_loaded);
  } catch {
    _sttServerAvailable = false;
  }
  return _sttServerAvailable;
}

function _browserDictationToggle(btn, targetId, wakeMode = false) {
  if (_browserRec) { try { _browserRec.stop(); } catch {} return; }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    addLog('Dictation unavailable: no server STT and this browser has no SpeechRecognition (use Chrome/Edge)');
    return;
  }
  const input = document.getElementById(targetId) || document.getElementById('chat-input');
  if (!input) { addLog('Dictation: no input found'); return; }

  const rec = new SR();
  rec.lang = navigator.language || 'en-US';
  // Wake-initiated dictation is hands-free: continuous=false makes the
  // recognizer auto-stop at the first natural pause so it can submit on its
  // own. Manual (button) dictation stays continuous until the Captain hits ⏹.
  rec.continuous = !wakeMode;
  rec.interimResults = false;
  let _heardText = false;
  rec.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) {
        const txt = e.results[i][0].transcript.trim();
        if (txt) { input.value = (input.value.trimEnd() ? input.value.trimEnd() + ' ' : '') + txt; _heardText = true; }
      }
    }
  };
  rec.onerror = (e) => {
    if (e.error !== 'no-speech' && e.error !== 'aborted') addLog('Dictation error: ' + e.error);
  };
  rec.onend = () => {
    _browserRec = null;
    if (btn) { btn.textContent = '🎙'; btn.classList.remove('dictating'); }
    input.focus();
    addLog('Dictation complete (browser STT)');
    // Hands-free wake path: speak the reply back and auto-submit the dictated
    // prompt. sendMessage() flips BRAIN.active, which gates the wake re-arm
    // below so the mic does not immediately reopen mid-reply.
    if (wakeMode && _heardText) {
      if (targetId === 'chat-input') _autoSpeakNextReply = true;
      if (typeof _autoSubmitFromInput === 'function') _autoSubmitFromInput(targetId);
    }
    // Hand the recognizer slot back to the wake-word listener
    if (typeof _restartWakeIfEnabled === 'function') _restartWakeIfEnabled();
  };

  _browserRec = rec;
  if (btn) { btn.textContent = '⏹'; btn.classList.add('dictating'); }
  addLog(wakeMode
    ? 'Wake dictation (browser STT) — speak; stops automatically at a pause'
    : 'Dictation (browser STT) — speak, click ⏹ to finish');
  rec.start();
}

async function toggleDictation(btnFromEvent) {
  if (_browserRec) {                 // browser-STT session in flight — stop it
    try { _browserRec.stop(); } catch {}
    return;
  }
  if (_dictActive) {
    _dictRecorder?.stop();
    return;
  }

  // btn may be the button that fired the click (per-pane), or fall back to the
  // main pane button if invoked without an argument.
  const btn = btnFromEvent || document.getElementById('dictate-btn');
  const targetId = btn?.dataset?.targetInput || _activeInputId || 'chat-input';

  // No server Whisper? Run dictation on the browser's own recognizer.
  if (!(await _serverSttAvailable())) {
    _browserDictationToggle(btn, targetId);
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    addLog('Mic access denied');
    return;
  }

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus' : 'audio/ogg;codecs=opus';

  _dictChunks  = [];
  _dictActive  = true;
  btn.textContent = '⏹';
  btn.classList.add('dictating');

  _dictRecorder = new MediaRecorder(stream, { mimeType });
  _dictRecorder.ondataavailable = e => { if (e.data.size > 0) _dictChunks.push(e.data); };
  _dictRecorder.onstop = async () => {
    _dictActive = false;
    btn.textContent = '🎙';
    btn.classList.remove('dictating');
    stream.getTracks().forEach(t => t.stop());

    if (_dictAborted) {
      _dictAborted = false;
      addLog('Dictation: cancelled (Esc)');
      btn.disabled = false;
      return;
    }

    btn.disabled = true;
    btn.textContent = '…';

    const blob = new Blob(_dictChunks, { type: mimeType });
    try {
      const res  = await fetch(`${API_BASE}/transcribe`, {
        method: 'POST',
        headers: { 'Content-Type': mimeType },
        body: blob,
      });
      const data = await res.json();
      if (data.text) {
        const input = document.getElementById(targetId) || document.getElementById('chat-input');
        if (input) {
          input.value = (input.value.trimEnd() ? input.value.trimEnd() + ' ' : '') + data.text;
          input.focus();
        }
        addLog('Dictation complete');
      } else {
        addLog('Dictation: no speech detected');
      }
    } catch (e) {
      addLog('Dictation error: ' + e.message);
    }
    btn.disabled = false;
    btn.textContent = '🎙';
  };

  _dictRecorder.start();
}

// ── Backend Mode Toggle ───────────────────────────────────
async function fetchMode() {
  try {
    const res = await fetch(`${API_BASE}/mode`);
    const data = await res.json();
    updateModeUI(data.mode);
  } catch (e) { /* bridge not yet up */ }
}

async function setMode(mode) {
  try {
    const res = await fetch(`${API_BASE}/mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    });
    const data = await res.json();
    updateModeUI(data.mode);
    addLog(`Mode: ${data.mode === 'cli' ? 'STANDARD' : 'EXPERIMENTAL (API)'}`);
  } catch (e) {
    addLog('Mode switch failed');
  }
}

async function setModel(model) {
  try {
    const res = await fetch(`${API_BASE}/model`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model })
    });
    const data = await res.json();
    updateModelUI(data.model);
    addLog(`Model: ${data.model.includes('sonnet') ? 'Sonnet (fast)' : 'Opus (quality)'}`);
  } catch (e) {
    addLog('Model switch failed — API mode only');
  }
}

// Sidebar mode/model widgets were removed in favor of the comms-page provider
// dropdown. These functions stay as null-safe no-ops so callers (fetchVitals)
// don't blow up. The provider dropdown is now the source of truth.
function updateModelUI(_model) { /* widget removed */ }
function updateModeUI(_mode)   { /* widget removed */ }

// ── Provider pill (top-bar clickable selector) ────────────
const PROVIDER_PILL_MAP = {
  'claude-cli':        { text: 'CLAUDE OPUS 4.7',      cls: 'orange' },
  'claude-cli-sonnet': { text: 'CLAUDE SONNET 4.6',    cls: 'teal'   },
  'claude-api':        { text: 'CLAUDE (API)',         cls: 'orange' },
  'claude-api-fast':   { text: '⚡ HAIKU 4.5 (FAST)',  cls: 'yellow' },
  'codex':             { text: 'OPENAI CODEX (GPT-5)', cls: 'green'  },
  'gemini':            { text: 'GOOGLE GEMINI 2.5',    cls: 'blue'   },
  'ollama':            { text: 'OLLAMA',               cls: 'purple' },
  'ollama-small':      { text: 'QWEN 3B (LOCAL)',      cls: 'teal'   },
};

function _providerPillCfg(provider) {
  const base = PROVIDER_PILL_MAP[provider.id]
    || { text: (provider.label || provider.id).toUpperCase(), cls: 'orange' };
  // For ollama, prefer the running model name in the pill text
  if (provider.id === 'ollama' && provider.model) {
    return { text: provider.model.toUpperCase(), cls: base.cls };
  }
  return base;
}

function _updateProviderPill(provider) {
  if (!provider) return;
  const pill  = document.getElementById('provider-pill');
  const label = document.getElementById('provider-pill-label');
  if (!pill || !label) return;
  const cfg = _providerPillCfg(provider);
  label.textContent = cfg.text;
  pill.className    = `pill ${cfg.cls} provider-pill-btn`;
}

function toggleProviderMenu(evt) {
  if (evt) evt.stopPropagation();
  const menu = document.getElementById('provider-menu');
  if (!menu) return;
  menu.classList.toggle('hidden');
}

function _closeProviderMenuOnOutsideClick(e) {
  const menu = document.getElementById('provider-menu');
  const pill = document.getElementById('provider-pill');
  if (!menu || menu.classList.contains('hidden')) return;
  if (menu.contains(e.target) || pill?.contains(e.target)) return;
  menu.classList.add('hidden');
}
document.addEventListener('click', _closeProviderMenuOnOutsideClick);

function _updateVitalsModelLine(provider) {
  const el = document.getElementById('vitals-model');
  if (!el || !provider) return;
  // Compact display: just the model name (truncated if long)
  const m = provider.model || provider.id || '—';
  el.textContent = m.length > 24 ? m.slice(0, 21) + '...' : m;
  el.title = `${provider.label || ''} (${m})`;
}

// Cache of /providers — used by per-window dropdowns so they don't each
// hit the bridge separately. Refreshed by loadProviders().
let _providersCache = [];
let _lastKnownActiveProvider = null;

async function loadProviders() {
  try {
    const res = await fetch(`${API_BASE}/providers`);
    const data = await res.json();
    _providersCache = data.providers || [];
    _lastKnownActiveProvider = data.active;
    // Sign-in CTA: render BEFORE any early return so it shows even when the
    // provider dropdown menu is not mounted in the current view.
    _renderAuthCta(data.needs_auth, data.providers || [], data.active);
    // Refresh any per-window dropdowns that are already mounted
    _refreshAllWindowProviderDropdowns();
    const menu = document.getElementById('provider-menu');
    if (!menu) return;
    menu.innerHTML = '';
    let activeProvider = null;
    // Selector shows ONLY connected/installed models. Discovery + install of
    // new ones lives on the AI Connectors page.
    const connected = data.providers.filter(p => p.available);
    for (const p of connected) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'provider-menu-item';
      item.textContent = p.label;
      if (p.id === data.active) {
        item.classList.add('active');
        activeProvider = p;
      }
      item.addEventListener('click', () => {
        document.getElementById('provider-menu')?.classList.add('hidden');
        setProvider(p.id);
      });
      menu.appendChild(item);
    }
    // Footer link into the connector manager
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'provider-menu-item provider-menu-add';
    add.textContent = '+ Add / manage models…';
    add.addEventListener('click', () => {
      document.getElementById('provider-menu')?.classList.add('hidden');
      showPanel('connectors');
    });
    menu.appendChild(add);
    if (!activeProvider) activeProvider = connected.find(p => p.id === data.active) || null;
    // Sync the title-bar pill and the sidebar model line
    _updateProviderPill(activeProvider);
    _updateVitalsModelLine(activeProvider);
  } catch (e) {
    addLog(`Could not load providers: ${e.message || e}`);
  }
}

// ══ CLI sign-in call-to-action ══════════════════════════════
// Shown when a CLI (e.g. Claude Code) is installed but NOT signed in, so DATA is
// running on the local Ollama fallback brain. The backend flags this via the
// `needs_auth` payload on /providers. We ALSO consult the live per-provider
// `authenticated` flag so the banner self-clears the instant the buyer signs in
// (via `claude` → /login in their terminal) — no bridge restart required. While
// the banner is up we poll /providers lightly so that flip is picked up promptly.
let _authCtaPollTimer = null;
let _authCtaDismissed = '';   // provider_id the buyer dismissed this session

function _ctaEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _startAuthCtaPoll() {
  if (_authCtaPollTimer) return;
  _authCtaPollTimer = setInterval(loadProviders, 15000);
}
function _stopAuthCtaPoll() {
  if (_authCtaPollTimer) { clearInterval(_authCtaPollTimer); _authCtaPollTimer = null; }
}

function _renderAuthCta(needsAuth, providers, active) {
  const el = document.getElementById('auth-cta');
  if (!el) return;
  const pid = needsAuth && needsAuth.provider_id;
  // No pending CLI auth, or buyer dismissed it this session → hide + stop polling.
  if (!pid || _authCtaDismissed === pid) {
    el.classList.add('hidden');
    el.innerHTML = '';
    _stopAuthCtaPoll();
    return;
  }
  const name = needsAuth.name || 'Your CLI';
  const cmd = needsAuth.login_cmd || '';
  // Has that CLI just been signed in? (live probe from _list_providers)
  const prov = (providers || []).find(p => p.id === pid);
  const nowAuthed = !!(prov && prov.authenticated);

  if (nowAuthed) {
    _stopAuthCtaPoll();
    // Already switched over → nothing to show.
    if (active === pid) { el.classList.add('hidden'); el.innerHTML = ''; return; }
    el.classList.remove('hidden');
    el.classList.add('ready');
    el.innerHTML =
      `<div class="auth-cta-body">
         <span class="auth-cta-icon">✓</span>
         <span class="auth-cta-text"><strong>${_ctaEsc(name)}</strong> is signed in. Switch DATA's brain from the local fallback?</span>
         <button type="button" class="auth-cta-btn" id="auth-cta-switch">Switch to ${_ctaEsc(name)}</button>
         <button type="button" class="auth-cta-x" id="auth-cta-close" title="Dismiss">✕</button>
       </div>`;
    document.getElementById('auth-cta-switch')?.addEventListener('click', () => { setProvider(pid); });
    document.getElementById('auth-cta-close')?.addEventListener('click', () => {
      _authCtaDismissed = pid; _renderAuthCta({}, providers, active);
    });
    return;
  }

  // Still not signed in → show the sign-in instructions with the exact command.
  el.classList.remove('hidden');
  el.classList.remove('ready');
  const onFallback = needsAuth.on_fallback
    ? ', so DATA is running on the local fallback brain' : '';
  el.innerHTML =
    `<div class="auth-cta-body">
       <span class="auth-cta-icon">◎</span>
       <span class="auth-cta-text"><strong>${_ctaEsc(name)}</strong> is installed but not signed in${onFallback}. Sign in to use it:</span>
       ${cmd ? `<code class="auth-cta-cmd" id="auth-cta-cmd" title="Click to copy">${_ctaEsc(cmd)}</code>` : ''}
       <button type="button" class="auth-cta-x" id="auth-cta-close" title="Dismiss until restart">✕</button>
     </div>`;
  const cmdEl = document.getElementById('auth-cta-cmd');
  if (cmdEl) cmdEl.addEventListener('click', () => {
    navigator.clipboard?.writeText(cmd)
      .then(() => { cmdEl.classList.add('copied'); setTimeout(() => cmdEl.classList.remove('copied'), 1200); })
      .catch(() => {});
  });
  document.getElementById('auth-cta-close')?.addEventListener('click', () => {
    _authCtaDismissed = pid; _renderAuthCta({}, providers, active);
  });
  _startAuthCtaPoll();
}

async function setProvider(providerId) {
  try {
    const res = await fetch(`${API_BASE}/provider`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: providerId }),
    });
    const data = await res.json();
    if (data.error) {
      addLog(`Provider switch failed: ${data.error}`);
      if (data.install_hint) addLog(`Install hint: ${data.install_hint}`);
      loadProviders();
      return;
    }
    addLog(`Provider → ${providerId}`);
    // Refresh menu + pill + sidebar from the fresh /providers response
    loadProviders();
  } catch (e) {
    addLog(`Provider switch error: ${e.message || e}`);
  }
}

// ══ AI Connectors ═══════════════════════════════════════════
// Hardware scan → local-model catalog with fit badges → one-click install
// (Ollama pull / CLI connector) → newly-installed models appear in the
// model selector. Active install polls are tracked here.
let _connectorsLoaded = false;
const _installPolls = {};   // model/connector id → interval handle

const _FIT_META = {
  'fits':     { cls: 'green',  label: 'RUNS GREAT' },
  'tight':    { cls: 'yellow', label: 'TIGHT FIT' },
  'cpu':      { cls: 'orange', label: 'CPU / SLOW' },
  'wont-fit': { cls: 'red',    label: "WON'T FIT" },
};

async function loadConnectors(force = false) {
  const btn = document.getElementById('connectors-refresh-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'SCANNING…'; }
  try {
    // Force a fresh hardware read on explicit rescan
    if (force) await fetch(`${API_BASE}/hardware?force=1`).catch(() => {});
    const res  = await fetch(`${API_BASE}/llm/catalog`);
    const data = await res.json();
    _renderHardware(data.hardware);
    _renderRecommendation(data);
    _renderModels(data.models, data.ollama_installed);
    _renderConnectors(data.connectors);
    const pill = document.getElementById('connectors-active-pill');
    if (pill) pill.textContent = (_lastKnownActiveProvider || '—').toUpperCase();
    _connectorsLoaded = true;
  } catch (e) {
    addLog(`Connectors load failed: ${e.message || e}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'RESCAN'; }
  }
}

function _renderHardware(hw) {
  const el = document.getElementById('conn-hw-grid');
  if (!el || !hw) return;
  const cells = [
    ['CPU',  hw.cpu || '—'],
    ['CORES', (hw.cpu_cores != null ? `${hw.cpu_cores}c` : '—') + (hw.cpu_threads != null ? ` / ${hw.cpu_threads}t` : '')],
    ['RAM',  hw.ram_total_gb ? `${hw.ram_total_gb} GB` : '—'],
    ['FREE RAM', hw.ram_available_gb ? `${hw.ram_available_gb} GB` : '—'],
    ['GPU',  hw.has_gpu ? (hw.gpu_name || 'GPU') : 'None detected'],
    ['VRAM', hw.has_gpu ? `${hw.vram_total_gb} GB` : '—'],
    ['OS',   `${hw.os || ''} ${hw.os_release || ''}`.trim() || '—'],
    ['OLLAMA', hw.ollama_installed ? 'Installed ✓' : 'Not installed'],
  ];
  el.innerHTML = cells.map(([k, v]) =>
    `<div class="conn-hw-cell"><div class="conn-hw-k">${k}</div><div class="conn-hw-v">${_esc(String(v))}</div></div>`
  ).join('');
}

function _renderRecommendation(data) {
  const sec  = document.getElementById('conn-rec-section');
  const card = document.getElementById('conn-rec-card');
  if (!sec || !card) return;
  const rec = data.models.find(m => m.recommended);
  if (!rec) { sec.style.display = 'none'; return; }
  sec.style.display = '';
  const action = rec.installed
    ? `<button class="data-btn-sm green" onclick="useModel('${rec.provider_id}')">USE NOW</button>`
    : `<button class="data-btn-sm orange" id="install-btn-${_cssId(rec.model)}" onclick="installModel('${rec.model}')">INSTALL</button>`;
  card.innerHTML = `
    <div class="conn-card conn-card-rec">
      <div class="conn-card-main">
        <div class="conn-card-title">★ ${_esc(rec.label)} <span class="conn-pill ${_FIT_META[rec.fit]?.cls||'blue'}">${_FIT_META[rec.fit]?.label||rec.fit}</span></div>
        <div class="conn-card-blurb">${_esc(rec.blurb)} — best fit for your ${data.hardware.has_gpu ? `${data.hardware.vram_total_gb} GB GPU` : 'CPU'}.</div>
        <div class="conn-card-meta">${rec.params} · ${rec.size_gb} GB download</div>
      </div>
      <div class="conn-card-action" id="action-${_cssId(rec.model)}">${action}</div>
    </div>`;
}

function _renderModels(models, ollamaInstalled) {
  const el = document.getElementById('conn-models');
  if (!el) return;
  let head = '';
  if (!ollamaInstalled) {
    head = `<div class="conn-warn">Ollama (the free local-model runtime) is not installed yet — but you do not need to install it by hand.
      Click <b>INSTALL</b> on any model below and DATA sets up Ollama automatically, then downloads the model and connects it.
      <a href="https://ollama.com" target="_blank" rel="noopener">Learn more</a></div>`;
  }
  el.innerHTML = head + models.map(m => {
    const fit = _FIT_META[m.fit] || { cls: 'blue', label: m.fit };
    let action;
    if (m.installed) {
      action = `<button class="data-btn-sm green" onclick="useModel('${m.provider_id}')">USE</button>`;
    } else if (m.fit === 'wont-fit') {
      action = `<button class="data-btn-sm" disabled title="Exceeds this machine's memory">TOO BIG</button>`;
    } else {
      // The backend installs the Ollama runtime automatically on first pull, so
      // this button is always live — even when Ollama is not yet on the machine.
      const tip = ollamaInstalled
        ? 'Download and run this model locally'
        : 'Installs the Ollama runtime automatically, then downloads this model';
      action = `<button class="data-btn-sm orange" id="install-btn-${_cssId(m.model)}" onclick="installModel('${m.model}')" title="${tip}">INSTALL</button>`;
    }
    return `
      <div class="conn-card" id="card-${_cssId(m.model)}">
        <div class="conn-card-main">
          <div class="conn-card-title">${_esc(m.label)}
            <span class="conn-pill ${fit.cls}">${fit.label}</span>
            ${m.installed ? '<span class="conn-pill teal">INSTALLED</span>' : ''}
          </div>
          <div class="conn-card-blurb">${_esc(m.blurb)}</div>
          <div class="conn-card-meta">${m.params} · ${m.size_gb} GB · ${m.use === 'code' ? 'coding' : 'general'}</div>
        </div>
        <div class="conn-card-action" id="action-${_cssId(m.model)}">${action}</div>
      </div>`;
  }).join('');
}

function _renderConnectors(connectors) {
  const el = document.getElementById('conn-connectors');
  if (!el) return;
  el.innerHTML = (connectors || []).map(c => {
    let action;
    if (c.available) {
      action = `<span class="conn-pill green">CONNECTED</span>`;
    } else if (c.install_cmd) {
      action = `<button class="data-btn-sm orange" id="install-btn-${_cssId(c.id)}" onclick="installConnector('${c.id}', '${_esc(c.install_cmd)}')">INSTALL</button>`;
    } else {
      action = `<a class="data-btn-sm blue" href="${c.install_url}" target="_blank" rel="noopener" style="text-decoration:none">GET IT</a>`;
    }
    return `
      <div class="conn-card" id="card-${_cssId(c.id)}">
        <div class="conn-card-main">
          <div class="conn-card-title">${_esc(c.name)}</div>
          <div class="conn-card-blurb">${_esc(c.blurb)}</div>
          <div class="conn-card-meta">${_esc(c.models)}${c.available ? '' : ` · after install: <code>${_esc(c.login_cmd)}</code>`}</div>
        </div>
        <div class="conn-card-action" id="action-${_cssId(c.id)}">${action}</div>
      </div>`;
  }).join('');
}

async function installModel(model) {
  const actionEl = document.getElementById(`action-${_cssId(model)}`);
  if (actionEl) actionEl.innerHTML = `<div class="conn-progress"><div class="conn-progress-bar" id="bar-${_cssId(model)}"></div><span class="conn-progress-txt" id="txt-${_cssId(model)}">starting…</span></div>`;
  try {
    const res = await fetch(`${API_BASE}/llm/install`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'ollama', target: model }),
    });
    const job = await res.json();
    if (job.error) { _installFailed(model, job.error); return; }
    addLog(`Pulling local model ${model}…`);
    _pollInstall(job.id, model, true);
  } catch (e) { _installFailed(model, e.message || e); }
}

async function installConnector(id, cmd) {
  const actionEl = document.getElementById(`action-${_cssId(id)}`);
  if (actionEl) actionEl.innerHTML = `<span class="conn-progress-txt">installing…</span>`;
  try {
    const res = await fetch(`${API_BASE}/llm/install`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'cli', target: cmd }),
    });
    const job = await res.json();
    if (job.error) { _installFailed(id, job.error); return; }
    addLog(`Installing connector ${id}…`);
    _pollInstall(job.id, id, false);
  } catch (e) { _installFailed(id, e.message || e); }
}

function _pollInstall(jobId, key, isModel) {
  if (_installPolls[key]) clearInterval(_installPolls[key]);
  _installPolls[key] = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/llm/install_status?job=${jobId}`);
      const j = await res.json();
      if (j.error) return;
      if (isModel) {
        const bar = document.getElementById(`bar-${_cssId(key)}`);
        const txt = document.getElementById(`txt-${_cssId(key)}`);
        if (bar) bar.style.width = `${j.pct || 0}%`;
        if (txt) txt.textContent = `${j.status_text || ''} ${j.pct ? Math.round(j.pct) + '%' : ''}`.trim();
      }
      if (j.state === 'done') {
        clearInterval(_installPolls[key]); delete _installPolls[key];
        addLog(`Installed ${key} ✓`);
        loadProviders();              // refresh the selector with the new model
        loadConnectors();             // refresh this page
      } else if (j.state === 'error') {
        clearInterval(_installPolls[key]); delete _installPolls[key];
        _installFailed(key, j.error || 'install failed');
      }
    } catch (_) { /* transient — keep polling */ }
  }, 1200);
}

function _installFailed(key, msg) {
  addLog(`Install failed (${key}): ${msg}`);
  const actionEl = document.getElementById(`action-${_cssId(key)}`);
  if (actionEl) actionEl.innerHTML = `<span class="conn-pill red" title="${_esc(String(msg))}">FAILED</span>`;
}

async function useModel(providerId) {
  await setProvider(providerId);
  addLog(`Active model → ${providerId}`);
  const pill = document.getElementById('connectors-active-pill');
  if (pill) pill.textContent = providerId.toUpperCase();
  loadConnectors();
}

// CSS-safe id from a model name (qwen2.5:3b → qwen2-5_3b)
function _cssId(s) { return String(s).replace(/[^a-zA-Z0-9]/g, '_'); }
function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Vitals ────────────────────────────────────────────────
async function fetchVitals() {
  try {
    const res  = await fetch(`${API_BASE}/vitals`);
    const v    = await res.json();

    // Right panel
    document.getElementById('vitals-turns').textContent  = v.turns;
    document.getElementById('vitals-memory').textContent = v.memory_kb;
    document.getElementById('skill-pct').textContent     = `${v.api_tools}+${v.claude_skills}`;
    document.getElementById('vitals-status').textContent = 'ONLINE';
    document.getElementById('reboot-btn')?.classList.remove('reboot-attention');

    // (Left sidebar HISTORY/MEMORY bars were replaced by SYSTEM HEALTH —
    // live-updated from /vitals_fast SSE in subscribeShipsHealth(). The
    // memory_kb / history_pct fields are still surfaced in the CONTEXT
    // BUDGET panel via fetchMemoryStats below.)
    document.getElementById('vitals-uptime').textContent = v.uptime;
    // Keep the provider pill / per-pane dropdowns synced from the bridge state
    loadProviders();
  } catch (e) {
    document.getElementById('vitals-status').textContent = 'OFFLINE';
    // Surface the recovery path: pulse the REBOOT button so the user knows how
    // to bring the bridge back without closing the dashboard.
    if (!_rebooting) document.getElementById('reboot-btn')?.classList.add('reboot-attention');
  }
  fetchMemoryStats();  // independent endpoint, separate try/catch
}

// ══ System Health ══════════════════════════════════════════════
// Subscribes to /vitals_fast SSE (every 500ms) and updates the
// sidebar widget + (if open) the fullscreen MSD modal readouts.
// Auto-reconnects on disconnect.
let _shipsHealthSSE   = null;
let _shipsModalOpen   = false;
let _lastShipsHealth  = null;

function _setBar(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}
function _alertClass(pct) {
  if (pct < 60) return 'red';
  if (pct < 90) return 'yellow';
  return 'green';
}
function _setBarColor(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('orange', 'yellow', 'blue', 'green', 'red');
  // Map subsystem severity to existing bar-fill color tokens.
  const sev = _alertClass(pct);
  if      (sev === 'red')    el.classList.add('orange'); // we use 'orange' slot but recolor below
  else if (sev === 'yellow') el.classList.add('yellow');
  else                       el.classList.add('green');
  if (sev === 'red') {
    el.style.background = 'var(--data-red)';
    el.style.boxShadow  = '0 0 6px var(--data-red)';
  } else {
    el.style.background = '';
    el.style.boxShadow  = '';
  }
}

// ── Sparkline ring buffers ─────────────────────────────────────
// Each metric keeps the last SPARK_LEN samples. Buffers fill regardless
// of whether the modal is open so opening it shows instant history.
const SPARK_LEN = 120;                    // 60s of history at 2Hz SSE
const _sparkBufs = {
  cpu: [], ram: [], gpu: [], net: [], tps: [], battery: [],
};
// Highest CPU temperature seen this page-session — surfaced in the Engine
// Nacelle card as the PEAK reading. Resets on reload.
let _cpuTempPeak = 0;
function _pushSpark(key, val) {
  const buf = _sparkBufs[key];
  if (!buf) return;
  buf.push(val);
  if (buf.length > SPARK_LEN) buf.shift();
}
function _drawSpark(svg, buf, max) {
  if (!svg || !buf || buf.length < 2) return;
  const poly = svg.querySelector('polyline');
  if (!poly) return;
  const W = 120, H = 24;
  const m = Math.max(max || 1, ...buf, 0.0001);
  const dx = W / (SPARK_LEN - 1);
  const offset = SPARK_LEN - buf.length;
  const ptArr = buf.map((v, i) => {
    const x = (i + offset) * dx;
    const y = H - (Math.max(0, v) / m) * (H - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  poly.setAttribute('points', ptArr.join(' '));

  // Area-fill polygon underneath the line — created lazily, then updated.
  // Sits visually behind the polyline so the bright stroke reads on top.
  let area = svg.querySelector('polygon.area');
  if (!area) {
    area = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    area.setAttribute('class', 'area');
    svg.insertBefore(area, poly);
  }
  const firstX = (offset) * dx;
  const lastX  = (SPARK_LEN - 1) * dx;
  area.setAttribute('points', `${firstX.toFixed(1)},${H} ${ptArr.join(' ')} ${lastX.toFixed(1)},${H}`);
}
function _fmtBytes(bps) {
  if (bps < 1024)        return `${bps} B/s`;
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(1)} KB/s`;
  return `${(bps / (1024 * 1024)).toFixed(2)} MB/s`;
}
function _setValSev(el, pct, warn, crit) {
  if (!el) return;
  el.classList.remove('warn', 'crit');
  if      (pct >= crit) el.classList.add('crit');
  else if (pct >= warn) el.classList.add('warn');
}

// Battery -> shield strength. The Captain's laptop tops out at 80% charge,
// so an 80% cell reads as 100% shields; anything above clamps to full.
// Returns null on desktops with no battery sensor.
const BATTERY_FULL_PCT = 80;
function _batteryShield(batteryPct) {
  if (typeof batteryPct !== 'number') return null;
  return Math.min(100, Math.max(0, Math.round(batteryPct / BATTERY_FULL_PCT * 100)));
}

function _applyShipsHealth(h) {
  _lastShipsHealth = h;

  // ── Sidebar widget (impulse + engine values, Hull/Shield/SIF/Damp bars, alert dot) ──
  const engineEl = document.getElementById('engine-value');
  if (engineEl) engineEl.textContent = Math.round(h.engine ?? 0);
  const impEl = document.getElementById('impulse-value');
  if (impEl) impEl.textContent = `${Math.round(h.impulse ?? 0)}%`;

  // SHIELDS tracks the laptop battery (scaled so a full 80% cell = 100%), matching
  // the MSD "SHIELD STRENGTH · main battery" reading. Falls back to the bridge's
  // RAM-derived h.shield on desktops with no battery sensor so the bar still moves.
  const _shieldBar = _batteryShield(h.battery_pct) ?? h.shield;
  _setBar('hull-bar',   h.hull);
  _setBar('shield-bar', _shieldBar);
  _setBar('sif-bar',    h.sif);
  _setBar('damp-bar',   h.damp);
  _setBarColor('hull-bar',   h.hull);
  _setBarColor('shield-bar', _shieldBar);
  _setBarColor('sif-bar',    h.sif);
  _setBarColor('damp-bar',   h.damp);

  const dot    = document.getElementById('alert-dot');
  const widget = document.getElementById('ships-health-widget');
  if (dot) {
    dot.classList.remove('green', 'yellow', 'red');
    dot.classList.add(h.alert);
  }
  if (widget) {
    widget.classList.remove('alert-green', 'alert-yellow', 'alert-red');
    widget.classList.add(`alert-${h.alert}`);
  }
  // Shields down (RAM maxed) — collapses the shield ring around the ship.
  document.body.classList.toggle('shields-down', !!h.shields_down);

  // ── Sparkline buffers — always push so opening the modal shows history ──
  _pushSpark('cpu', h.cpu);
  _pushSpark('ram', h.ram);
  // 'gpu' buffer now stores engine_load% — feeds the Engine Nacelle sparkline.
  _pushSpark('gpu', (typeof h.engine_load === 'number') ? h.engine_load : 0);
  _pushSpark('net', Math.max(h.net_in_bps || 0, h.net_out_bps || 0));
  _pushSpark('tps', h.tps);
  // Shield strength — laptop battery scaled so a full (80%) cell = 100%.
  const _shieldPct = _batteryShield(h.battery_pct);
  _pushSpark('battery', _shieldPct == null ? 0 : _shieldPct);

  // ── MSD modal (only update DOM when visible) ──
  if (_shipsModalOpen) {
    const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };

    // Speed gauges (bottom-center): Impulse (CPU %) + Engine (GPU/tok 0.0–9.9)
    set('msd-engine',    Math.round(h.engine ?? 0));
    set('msd-impulse', `${Math.round(h.impulse ?? 0)}%`);

    // CPU + temp (CPU temp is null on Windows without a hardware sensor app running)
    set('msd-cpu', `${h.cpu}%`);
    _setValSev(document.getElementById('msd-cpu'), h.cpu, 70, 90);
    set('msd-cpu-temp', h.cpu_temp_c != null ? `TEMP ${Math.round(h.cpu_temp_c)}°C` : 'TEMP —');

    // RAM (used GB / total GB + % below)
    set('msd-ram',    `${h.ram_used_gb} / ${h.ram_total_gb} GB`);
    set('msd-ram-pct', `${h.ram}%`);
    _setValSev(document.getElementById('msd-ram'), h.ram, 80, 92);

    // Engine Nacelle — synthetic "engine load" 0-100%. Blends CPU load,
    // RAM pressure, in-flight LLM streams, and token velocity. Replaces
    // the GPU reading (always 0 here — no NVIDIA card).
    const eload = (typeof h.engine_load === 'number') ? h.engine_load : 0;
    if (eload > _cpuTempPeak) _cpuTempPeak = eload;     // reusing the peak slot
    set('msd-gpu', `${Math.round(eload)}%`);
    set('msd-vram', `CPU ${Math.round(h.cpu ?? 0)}% · STREAMS ${h.llm_inflight ?? 0}`);
    set('msd-gpu-temp', `PEAK ${Math.round(_cpuTempPeak)}%`);
    _setValSev(document.getElementById('msd-gpu'), eload, 70, 90);

    // Network
    set('msd-net', `↓ ${_fmtBytes(h.net_in_bps)}   ↑ ${_fmtBytes(h.net_out_bps)}`);

    // Shield strength — laptop battery scaled to a 0-100% shield reading
    const shieldEl = document.getElementById('msd-shield-batt');
    if (shieldEl) {
      shieldEl.textContent = _shieldPct == null ? 'N/A' : `${_shieldPct}%`;
      shieldEl.classList.remove('warn', 'crit');
      if      (_shieldPct != null && _shieldPct <= 25) shieldEl.classList.add('crit');
      else if (_shieldPct != null && _shieldPct <= 50) shieldEl.classList.add('warn');
    }
    set('msd-batt-state', h.battery_pct == null
      ? 'NO POWER CELL'
      : `${Math.round(h.battery_pct)}% CELL · ${h.battery_plugged ? 'CHARGING' : 'ON BATTERY'}`);

    // Disk
    set('msd-disk',     `${h.disk_free_gb} GB free`);
    set('msd-disk-pct', `${h.disk_used_pct}% used of ${h.disk_total_gb} GB`);
    _setValSev(document.getElementById('msd-disk'), h.disk_used_pct, 80, 92);

    // Subsystem pills — tunnel status from fast-vitals
    const tunnelPill = document.getElementById('msd-sub-tunnel');
    if (tunnelPill) {
      tunnelPill.textContent = h.tunnel ? 'TUNNEL ●' : 'TUNNEL ✗';
      tunnelPill.classList.toggle('ok',  !!h.tunnel);
      tunnelPill.classList.toggle('warn', !h.tunnel);
    }

    // Alert pill (top bar)
    const pill = document.getElementById('msd-alert-pill');
    if (pill) {
      pill.classList.remove('yellow', 'red');
      if (h.alert === 'yellow') pill.classList.add('yellow');
      if (h.alert === 'red')    pill.classList.add('red');
      pill.textContent = `CONDITION ${h.alert.toUpperCase()}`;
    }

    // Sparklines — % metrics max=100, network auto-scales, tps autoscales
    _drawSpark(document.querySelector('[data-spark="cpu"]'), _sparkBufs.cpu, 100);
    _drawSpark(document.querySelector('[data-spark="ram"]'), _sparkBufs.ram, 100);
    _drawSpark(document.querySelector('[data-spark="gpu"]'), _sparkBufs.gpu, 100);
    _drawSpark(document.querySelector('[data-spark="net"]'), _sparkBufs.net);
    _drawSpark(document.querySelector('[data-spark="battery"]'), _sparkBufs.battery, 100);
  }
}

// ── MSD heavy-poll: LLM info, memory, recent tools, subsystems ──
let _msdPollTimer = null;
function _startMsdPoll() {
  _refreshMsd();   // immediate paint
  _msdPollTimer = setInterval(_refreshMsd, 2000);
}
function _stopMsdPoll() {
  if (_msdPollTimer) { clearInterval(_msdPollTimer); _msdPollTimer = null; }
}
function _fmtUptime(s) {
  if (s < 60)   return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m`;
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
  return `${h}h ${m}m`;
}
function _fmtAgo(s) {
  if (s < 5)    return 'just now';
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
}
async function _refreshMsd() {
  try {
    const r = await fetch(`${API_BASE}/msd`);
    if (!r.ok) return;
    const d = await r.json();
    const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };

    // Top bar
    const sd = new Date();
    const day = String(Math.floor((sd - new Date(sd.getFullYear(), 0, 0)) / 86400000)).padStart(3, '0');
    set('msd-stardate', `${sd.getFullYear()}.${day}`);
    set('msd-uptime',   _fmtUptime(d.uptime_secs || 0));
    set('msd-last-cmd', _fmtAgo(d.last_user_activity_secs || 0));
    set('msd-llm-summary', `${d.llm.provider_label} · ${d.llm.model}`);

    // Power Core health (uncached prompt token budget). Baseline is the
    // first-ever used_tokens reading; at 2× baseline the core is at 10%
    // health, which triggers a one-shot breach popup.
    const wc = d.power_core || {};
    const health = (typeof wc.health_pct === 'number') ? wc.health_pct : null;
    set('msd-ctx', health === null ? '—' : `${health.toFixed(0)}% HEALTH`);
    set('msd-ctx-detail',
      `${((wc.current_tokens||0)/1000).toFixed(1)}k / ${((wc.breach_tokens||0)/1000).toFixed(1)}k tok`);
    // Severity inverts the health score: low health = high severity (red).
    _setValSev(document.getElementById('msd-ctx'),
               health === null ? 0 : (100 - health), 70, 90);

    if (wc.popup_pending) _showEngineCoreBreach(wc);

    // Subsystem pills
    const voice = d.subsystems.voice || {};
    const vp = document.getElementById('msd-sub-voice');
    if (vp) { vp.textContent = `VOICE ${voice.tts || '—'}`; vp.classList.toggle('ok', !!voice.tts); }
    const op = document.getElementById('msd-sub-orders');
    if (op) op.textContent = `ORDERS ${d.subsystems.standing_orders}`;
    const tp = document.getElementById('msd-sub-tools');
    if (tp) tp.textContent = `TOOLS ${d.subsystems.api_tools}`;

    // (Recent-tools ledger removed by request — recent_tools field is still
    // returned by /msd in case the side widget surfaces it later.)
  } catch (_) { /* swallow — modal stays on last-good snapshot */ }
}

// ── Leader lines (cards ↔ ship anatomy) ────────────────────────
// Each metric card has data-zone="<key>" pointing at one of these
// normalized (0..1) coordinates within the ship image's rendered rect.
// A full-frame SVG overlay (`.msd-leaders-global`) draws a dashed line
// from the card's inner edge to the anatomy point and dots both ends.
// Coords are normalized to the rendered ship image rect (object-fit: contain).
// Calibrated against the actual visible features in the source PNG so the
// leader-line dots land on real structures, not empty hull/black space.
// Leader-line anatomy anchors — normalized (0..1) coords on the rendered
// ship-image rect, calibrated to the DATA-Class Starfighter schematic.
const SHIP_ZONES = {
  'saucer':            { x: 0.60, y: 0.43 },
  'engineering':       { x: 0.45, y: 0.50 },
  'engine-core':         { x: 0.62, y: 0.50 },
  'bridge':            { x: 0.30, y: 0.49 },
  'starboard-nacelle': { x: 0.76, y: 0.40 },
  'port-nacelle':      { x: 0.75, y: 0.60 },
  'deflector':         { x: 0.70, y: 0.50 },
  'aft':               { x: 0.55, y: 0.50 },
  'neural':        { x: 0.50, y: 0.50 },
  'comms':             { x: 0.62, y: 0.70 },
};
function _shipZones() {
  return SHIP_ZONES;
}

// Lock the in-stage overlay (shield bubble, radar cone, engine glows) to the
// ship image's ACTUAL rendered rect. The image uses object-fit: contain and is
// centered in .msd-stage-inner, so on a screen whose aspect differs from the
// calibration machine it gets letterboxed. The overlay's viewBox coords
// (cx=50,cy=24 ship center, engine glows at 78%, etc.) are calibrated to the
// ship pixels — so the overlay element must cover exactly the image rect, not
// the whole stage box. Without this, the cone apex and shields drift off-ship.
function _positionShipOverlay() {
  const overlay = document.querySelector('.msd-overlay');
  const inner   = document.querySelector('.msd-stage-inner');
  const img     = document.getElementById('ship-schematic-img');
  if (!overlay || !inner || !img || !img.naturalWidth) return;

  const innerRect = inner.getBoundingClientRect();
  if (innerRect.width === 0 || innerRect.height === 0) return;

  // Compute the object-fit: contain rect of the image inside stage-inner.
  const aspect = img.naturalWidth / img.naturalHeight;
  let rW = innerRect.width, rH = innerRect.height;
  if (rW / rH > aspect) rW = rH * aspect;   // limited by height → shrink width
  else                  rH = rW / aspect;   // limited by width  → shrink height
  const offX = (innerRect.width  - rW) / 2;
  const offY = (innerRect.height - rH) / 2;

  // Pin the overlay to that rect (offset parent is .msd-stage-inner).
  overlay.style.left   = offX + 'px';
  overlay.style.top    = offY + 'px';
  overlay.style.right  = 'auto';
  overlay.style.bottom = 'auto';
  overlay.style.width  = rW + 'px';
  overlay.style.height = rH + 'px';
}

function _drawLeaderLines() {
  _positionShipOverlay();   // keep shields/cone/engine glows locked to the ship
  const svg = document.querySelector('.msd-leaders-global');
  const img = document.getElementById('ship-schematic-img');
  if (!svg || !img || !img.naturalWidth) return;

  const svgRect = svg.getBoundingClientRect();
  const imgRect = img.getBoundingClientRect();
  if (imgRect.width === 0 || imgRect.height === 0) return;

  // object-fit: contain may letterbox — compute the actual rendered image rect.
  const aspect = img.naturalWidth / img.naturalHeight;
  let rW = imgRect.width, rH = imgRect.height;
  if (rW / rH > aspect) rW = rH * aspect;
  else                  rH = rW / aspect;
  const rX = imgRect.left + (imgRect.width  - rW) / 2 - svgRect.left;
  const rY = imgRect.top  + (imgRect.height - rH) / 2 - svgRect.top;
  const shipCx = rX + rW / 2;
  const shipCy = rY + rH / 2;

  // Clear and redraw
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const zones = _shipZones();
  document.querySelectorAll('.msd-metric[data-zone]').forEach(card => {
    const zone = zones[card.dataset.zone];
    if (!zone) return;
    const r = card.getBoundingClientRect();

    // Card anchor point: midpoint of the edge facing the ship center.
    const cardCx = r.left + r.width  / 2;
    const cardCy = r.top  + r.height / 2;
    let cardAnchorX;
    if (cardCx < shipCx) cardAnchorX = r.right - svgRect.left;     // exit right edge
    else                 cardAnchorX = r.left  - svgRect.left;     // exit left edge
    const cardAnchorY = r.top + r.height / 2 - svgRect.top;

    const zoneX = rX + zone.x * rW;
    const zoneY = rY + zone.y * rH;

    const NS = 'http://www.w3.org/2000/svg';
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', cardAnchorX);
    line.setAttribute('y1', cardAnchorY);
    line.setAttribute('x2', zoneX);
    line.setAttribute('y2', zoneY);
    svg.appendChild(line);

    const cardDot = document.createElementNS(NS, 'circle');
    cardDot.setAttribute('class', 'card');
    cardDot.setAttribute('cx', cardAnchorX);
    cardDot.setAttribute('cy', cardAnchorY);
    cardDot.setAttribute('r', 2.5);
    svg.appendChild(cardDot);

    const zoneDot = document.createElementNS(NS, 'circle');
    zoneDot.setAttribute('class', 'zone');
    zoneDot.setAttribute('cx', zoneX);
    zoneDot.setAttribute('cy', zoneY);
    zoneDot.setAttribute('r', 3);
    svg.appendChild(zoneDot);
  });
}

// Recompute leaders on window resize while the modal is open.
window.addEventListener('resize', () => {
  if (_shipsModalOpen) requestAnimationFrame(_drawLeaderLines);
});

// ── MSD card placement ─────────────────────────────────────────
// Each corner cluster holds two metric cards, tuned to the DATA-Class
// Starfighter anchors. Cards are moved between clusters by data-zone,
// so this stays correct if the markup changes.
const MSD_LAYOUT = {
  'cluster-tl': ['saucer', 'engineering'],        // IMPULSE ENGINES, MAIN DEFLECTOR
  'cluster-tr': ['starboard-nacelle', 'aft'],     // ENGINE NACELLE, CARGO HOLD
  'cluster-bl': ['bridge', 'neural'],         // SHIELD STRENGTH, SUBSPACE BANDWIDTH
  'cluster-br': ['engine-core', 'comms'],           // POWER CORE, AUX SYSTEMS
};
function _arrangeMsdCards() {
  const layout = MSD_LAYOUT;
  const cards = {};
  document.querySelectorAll('.msd-metric[data-zone]').forEach(c => { cards[c.dataset.zone] = c; });
  for (const cls in layout) {
    const cluster = document.querySelector('.msd-cluster.' + cls);
    if (!cluster) continue;
    layout[cls].forEach(z => { if (cards[z]) cluster.appendChild(cards[z]); });
  }
  if (_shipsModalOpen) requestAnimationFrame(_drawLeaderLines);
}
_arrangeMsdCards();   // place cards for the theme active at load

function subscribeShipsHealth() {
  if (_shipsHealthSSE) { try { _shipsHealthSSE.close(); } catch (_) {} }
  try {
    _shipsHealthSSE = new EventSource(`${API_BASE}/vitals_fast`);
    _shipsHealthSSE.onmessage = (ev) => {
      try { _applyShipsHealth(JSON.parse(ev.data)); }
      catch (_) { /* malformed frame — skip */ }
    };
    _shipsHealthSSE.onerror = () => {
      // Browser auto-retries EventSource; if it gives up, reconnect ourselves.
      if (_shipsHealthSSE.readyState === EventSource.CLOSED) {
        setTimeout(subscribeShipsHealth, 3000);
      }
    };
  } catch (e) {
    // Browser too old, or fetch base unreachable — fall back to slow polling.
    setInterval(async () => {
      try {
        const r = await fetch(`${API_BASE}/ships_health`);
        if (r.ok) _applyShipsHealth(await r.json());
      } catch (_) {}
    }, 2000);
  }
}

function openShipModal() {
  const m = document.getElementById('ship-modal');
  if (!m) return;
  m.classList.add('open');
  m.setAttribute('aria-hidden', 'false');
  _shipsModalOpen = true;
  if (_lastShipsHealth) _applyShipsHealth(_lastShipsHealth); // immediate paint of live values
  _startMsdPoll();                                            // begin /msd polling for heavy data

  // Leader lines: defer one frame so the grid lays out before we measure.
  // If the image is still loading on first open, hook a one-shot load too.
  requestAnimationFrame(() => requestAnimationFrame(_drawLeaderLines));
  const img = document.getElementById('ship-schematic-img');
  if (img && !img.complete) img.addEventListener('load', _drawLeaderLines, { once: true });
}
function closeShipModal(ev) {
  // Only close on backdrop click, close button, or Esc — not on inner clicks.
  if (ev && ev.target && ev.target.id !== 'ship-modal' && ev.type !== 'keydown' && !ev._fromCloseBtn) {
    if (ev.currentTarget && ev.currentTarget.id !== 'ship-modal') return;
  }
  const m = document.getElementById('ship-modal');
  if (!m) return;
  m.classList.remove('open');
  m.setAttribute('aria-hidden', 'true');
  _shipsModalOpen = false;
  _stopMsdPoll();
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _shipsModalOpen) closeShipModal();
});

// ── Power Core breach popup — fires once per breach episode ──
// Server sets power_core.popup_pending=true on the rising-edge crossing
// of the 10%-health threshold. We display the modal exactly once and
// ack the server so it doesn't re-send.
function _showEngineCoreBreach(wc) {
  const modal = document.getElementById('engine-core-breach-modal');
  if (!modal || !modal.hidden) return;        // already shown
  const tEl = document.getElementById('engine-core-breach-tokens');
  const pEl = document.getElementById('engine-core-breach-pct');
  if (tEl) tEl.textContent = (wc.current_tokens || 0).toLocaleString();
  if (pEl) pEl.textContent = (typeof wc.health_pct === 'number')
                              ? wc.health_pct.toFixed(0) : '—';
  modal.hidden = false;
  // Fire-and-forget ack so the server doesn't keep flagging popup_pending.
  fetch(`${API_BASE}/power_core/ack`, { method: 'POST' }).catch(() => {});
}

function closeEngineCoreBreach() {
  const modal = document.getElementById('engine-core-breach-modal');
  if (modal) modal.hidden = true;
}

// ── Context budget readout (CONTEXT BUDGET sidebar panel) ──
// Polled with /vitals. Computes used / ceiling, colours the bar by severity,
// and fills the per-segment numeric breakdown. Tokens are bridge-side estimates
// (~4 chars/token), so treat as ±10% accurate, not exact.
function _fmtTok(n) {
  if (n == null) return '—';
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
  return String(n);
}
async function fetchMemoryStats() {
  try {
    const res = await fetch(`${API_BASE}/memory-stats`);
    const s   = await res.json();
    const pct = s.used_pct || 0;

    const fill = document.getElementById('ctx-budget-fill');
    fill.style.width = `${Math.min(100, pct)}%`;
    fill.classList.remove('warn', 'alert', 'crit');
    if      (pct >= 90) fill.classList.add('crit');
    else if (pct >= 70) fill.classList.add('alert');
    else if (pct >= 40) fill.classList.add('warn');

    document.getElementById('ctx-budget-pct').textContent = `${pct.toFixed(1)}%`;
    document.getElementById('ctx-system').textContent     = _fmtTok(s.system_tokens);
    document.getElementById('ctx-memory').textContent     = _fmtTok(s.memory_tokens);
    // Memory threshold pulse — keep DATA_MEMORY.md lean. Warn ≥8k, alarm ≥10k.
    const memRow = document.getElementById('ctx-memory').parentElement;
    memRow.classList.remove('mem-warn', 'mem-alert');
    if      (s.memory_tokens >= 10000) memRow.classList.add('mem-alert');
    else if (s.memory_tokens >=  8000) memRow.classList.add('mem-warn');
    document.getElementById('ctx-history').textContent    =
      `${_fmtTok(s.history_tokens)} (${s.history_turns}/${s.max_history_turns})`;
    document.getElementById('ctx-used').textContent       = _fmtTok(s.used_tokens);
    document.getElementById('ctx-headroom').textContent   = _fmtTok(s.headroom_tokens);
    document.getElementById('ctx-ceiling').textContent    = _fmtTok(s.ceiling_tokens);
  } catch (e) {
    document.getElementById('ctx-budget-pct').textContent = 'OFF';
  }
}

// ── Manual memory compaction trigger ─────────────────────
async function compactMemory() {
  const btn = document.getElementById('memory-compact-btn');
  if (!btn || btn.disabled) return;
  if (!confirm('Ask Data to compact DATA_MEMORY.md? A backup is saved to /Backups before the file is overwritten.')) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'COMPACTING…';
  try {
    const res = await fetch(`${API_BASE}/memory-compact`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    });
    const r = await res.json();
    if (r.ok) {
      addLog(`Memory compacted: ${r.before_tokens} → ${r.after_tokens} tok (saved ${r.saved_tokens})`);
      fetchMemoryStats();  // refresh panel immediately
    } else {
      addLog(`Compact failed: ${r.error || 'unknown'}`);
    }
  } catch (e) {
    addLog(`Compact request error: ${e.message || e}`);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// ── Lifecycle heartbeat ───────────────────────────────────
// Browser pulses /heartbeat every 25s. Bridge shuts itself down after 180s
// without a beat (5s after a beforeunload "leaving" signal). Refresh
// recovers within the grace window so it doesn't trip the watcher.
// Set DATA_LIFECYCLE_MODE=daemon on the bridge to disable this entirely.
//
// IMPORTANT: Uses a Web Worker for the timer so Chrome/Edge don't throttle
// the interval when the tab is in the background (browsers reduce setInterval
// to ~1/min on background tabs, which was causing Data to go offline).
let _lifecycleWorker = null;
let _lifecycleFallback = null;
function startLifecycleHeartbeat() {
  if (_lifecycleWorker || _lifecycleFallback) return;
  const beat = () => {
    fetch(`${API_BASE}/heartbeat`, { method: 'POST', keepalive: true }).catch(() => {});
  };
  beat();  // immediate pulse so the bridge marks the browser as connected

  // Web Worker approach — immune to background-tab throttling
  try {
    const workerCode = `setInterval(() => postMessage('beat'), 25000);`;
    const blob = new Blob([workerCode], { type: 'application/javascript' });
    _lifecycleWorker = new Worker(URL.createObjectURL(blob));
    _lifecycleWorker.onmessage = beat;
  } catch (_) {
    // Fallback for environments where Workers aren't available
    _lifecycleFallback = setInterval(beat, 25000);
  }

  window.addEventListener('beforeunload', () => {
    // sendBeacon is the only reliable way to fire a request as the page unloads.
    // The ?leaving=1 query drops the bridge's grace period to 5s for fast shutdown.
    try { navigator.sendBeacon(`${API_BASE}/heartbeat?leaving=1`, ''); } catch (e) {}
  });
}
startLifecycleHeartbeat();

// ── System Shutdown ───────────────────────────────────────
async function systemShutdown() {
  const btn = document.getElementById('shutdown-btn');
  if (!confirm('Take DATA offline and close all windows?')) return;
  btn.textContent = 'SHUTTING DOWN...';
  btn.disabled = true;
  try {
    await fetch(`${API_BASE}/shutdown`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch (e) { /* expected — server dies mid-response */ }
  btn.textContent = 'OFFLINE';
  addLog('System offline');
  window.close();
}

// ── System Reboot ─────────────────────────────────────────
// Brings the bridge back online WITHOUT closing the dashboard windows. The
// bridge serves this very page on :7777, so when it dies the page can't
// relaunch it. The supervisor (supervisor.py) is a tiny always-on process on
// :7766 whose only job is to (re)start the bridge — the REBOOT button hits the
// supervisor, then waits for :7777 to answer again and refreshes the panes.
// Falls back to the bridge's own /reboot endpoint (Linux/systemd, or a wedged-
// but-alive bridge). Localhost target so reboot works from this machine.
const SUPERVISOR_BASE = 'http://127.0.0.1:7766';
let _rebooting = false;

async function systemReboot() {
  if (_rebooting) return;
  const btn = document.getElementById('reboot-btn');

  const looksOnline = document.getElementById('vitals-status')?.textContent === 'ONLINE';
  if (looksOnline && !confirm('Restart the DATA bridge now? This relaunches the server and ends any in-flight reply. Your open windows stay open.')) {
    return;
  }

  _rebooting = true;
  const original = btn.textContent;
  btn.disabled = true;
  btn.classList.remove('reboot-attention');
  btn.textContent = '⟳ REBOOTING…';
  addLog('Reboot requested — relaunching bridge');

  // 1. Supervisor first (desktop). If unreachable, fall back to the bridge's
  //    own /reboot (droplet/systemd, or a wedged-but-alive bridge).
  let issued = false;
  try {
    await fetch(`${SUPERVISOR_BASE}/reboot`, { method: 'POST' });
    issued = true;
  } catch (e) {
    try {
      await fetch(`${API_BASE}/reboot`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: '{}' });
      issued = true;
    } catch (e2) { /* both failed — handled below */ }
  }

  if (!issued) {
    _rebooting = false;
    btn.disabled = false;
    btn.textContent = original;
    addLog('Reboot could not be issued (supervisor :7766 and bridge both unreachable)');
    appendMessage('data',
      'I could not reach a reboot service, Captain. On the desktop the supervisor runs ' +
      'on `127.0.0.1:7766` and starts with the bridge — if this session predates it, ' +
      'relaunch via **start_data.bat** (or the DATA desktop shortcut) to enable one-click ' +
      'reboot. If the bridge process is fully stopped, it must be started from outside the browser.');
    return;
  }

  // 2. Poll /health (same-origin) until the fresh bridge answers. Cap ~60s.
  setStatus('REBOOTING BRIDGE…');
  const deadline = Date.now() + 60000;
  let back = false;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1500));
    try {
      const res = await fetch(`${API_BASE}/health`, { cache: 'no-store' });
      if (res.ok) { back = true; break; }
    } catch (_) { /* still down — keep polling */ }
  }

  _rebooting = false;
  btn.disabled = false;

  if (back) {
    btn.textContent = '✓ BACK ONLINE';
    addLog('Bridge back online');
    playDataSound('engage');
    setStatus('BRIDGE ONLINE');
    try { fetchVitals(); } catch (_) {}
    try { subscribeShipsHealth(); } catch (_) {}
    try { _pollUiEvents(); } catch (_) {}
    setTimeout(() => { btn.textContent = '⟳ REBOOT BRIDGE'; }, 4000);
  } else {
    btn.textContent = '⟳ REBOOT BRIDGE';
    setStatus('REBOOT TIMED OUT');
    addLog('Bridge did not come back within 60s');
    appendMessage('data',
      'The bridge did not answer within 60 seconds, Captain. The relaunch was issued — ' +
      'check `bridge.log` for a startup error, or try once more.');
  }
}

// ═══════════════════════════════════════════════════════════
// MULTI-PROJECT WORKSPACE
// ═══════════════════════════════════════════════════════════

let _wsCounter = 0;
let _mainProjectSet = false;
// True once the user has sent anything through the main channel this
// session. A pristine main chat lets the first DOCUMENTS project click
// re-root the main window instead of spawning a second pane.
let _mainChatUsed = false;
const _workspaces = new Map();  // wsId → { path, name, isThinking, projectNodes, isMain, provider }

// ── Per-window provider dropdown ────────────────────────────
function _windowProviderSelectHTML(wsId, selected) {
  // Empty options to start — _populateWindowProviderSelect fills them after
  // the providers cache loads. Keeps the markup short and avoids stale state.
  return `<select class="pane-provider-select" id="pane-provider-ws${wsId}"
              title="Model for this window"
              onchange="setWindowProvider(${wsId}, this.value)"></select>`;
}

function _populateWindowProviderSelect(wsId) {
  const sel = document.getElementById(`pane-provider-ws${wsId}`);
  const ws  = _workspaces.get(wsId);
  if (!sel || !ws) return;
  const opts = (_providersCache.length ? _providersCache.filter(p => p.available)
                                       : [{ id: ws.provider, label: ws.provider, available: true }]);
  sel.innerHTML = opts.map(p =>
    `<option value="${p.id}"${p.id === ws.provider ? ' selected' : ''}>${p.label}</option>`
  ).join('');
}

function _refreshAllWindowProviderDropdowns() {
  for (const wsId of _workspaces.keys()) _populateWindowProviderSelect(wsId);
  _populateMainProviderSelect();
}

function setWindowProvider(wsId, providerId) {
  const ws = _workspaces.get(wsId);
  if (!ws) return;
  ws.provider = providerId;
  addLog(`[${ws.name}] provider → ${providerId}`);
}

// Main pane's always-visible provider dropdown. When a project is loaded
// into the main pane, this still works — it updates the main workspace's
// provider. When no project is loaded, it updates the global ACTIVE_PROVIDER
// so the next chat message routes through the chosen model.
function _populateMainProviderSelect() {
  const sel = document.getElementById('main-provider-select');
  if (!sel) return;
  const mainWs = [..._workspaces.values()].find(w => w.isMain);
  const selected = mainWs?.provider || _lastKnownActiveProvider || 'claude-cli';
  const opts = _providersCache.length
    ? _providersCache.filter(p => p.available)
    : [{ id: selected, label: selected, available: true }];
  sel.innerHTML = opts.map(p =>
    `<option value="${p.id}"${p.id === selected ? ' selected' : ''}>${p.label}</option>`
  ).join('');
}

async function setMainProvider(providerId) {
  const mainWs = [..._workspaces.values()].find(w => w.isMain);
  if (mainWs) {
    // Per-workspace override — same as the inline dropdown when a project is loaded
    mainWs.provider = providerId;
    addLog(`Main pane provider → ${providerId}`);
    return;
  }
  // No project loaded — flip the global ACTIVE_PROVIDER instead
  try {
    const res = await fetch(`${API_BASE}/provider`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ provider: providerId }),
    });
    const data = await res.json();
    if (data.error) { addLog(`Provider switch failed: ${data.error}`); return; }
    addLog(`Active provider → ${providerId}`);
    loadProviders();
  } catch (e) {
    addLog(`Provider switch error: ${e.message || e}`);
  }
}

function _showPaneHeaders(visible) {
  // Spawned-pane headers toggle with workspace presence. The MAIN pane header
  // stays visible at all times — it hosts the always-on model selector.
  document.querySelectorAll('.chat-pane-header').forEach(h => {
    if (h.id === 'pane-main-header') return;
    h.classList.toggle('hidden', !visible);
  });
}

// Drag-resize state for the chat pane grid. Ratios live in [0.1, 0.9].
let _paneColRatio = 0.5;   // left:right split for any row with 2 columns
let _paneRowRatio = 0.5;   // top:bottom split between row 1 and row 2

function _updateChatGrid() {
  const wrapper = document.getElementById('chats-wrapper');
  // Hidden panes (e.g. pane-main after the Captain closes it while project
  // panes are still open) must not reserve a grid column — count only
  // visible panes so the leftmost project pane truly becomes "the first".
  const panes   = wrapper.querySelectorAll('.chat-pane:not([hidden])');
  const count   = panes.length;
  // The pristine main pane's CLOSE button only makes sense when there is
  // another window to promote in its place — hide it when main stands alone.
  const mainCloseEmpty = document.getElementById('main-close-empty');
  if (mainCloseEmpty) mainCloseEmpty.style.display = count > 1 ? '' : 'none';
  const c       = _paneColRatio;
  const r       = _paneRowRatio;
  const isMobile = window.matchMedia('(max-width: 900px)').matches;

  // Reset any per-pane grid spans we may have set before
  panes.forEach(p => { p.style.gridColumn = ''; });

  if (isMobile) {
    // Vertical stack — single column, N rows. Row ratio resizes the
    // first row vs the rest so the horizontal splitter has something
    // to drag against for the 2-pane case.
    wrapper.style.gridTemplateColumns = '1fr';
    if (count <= 1)        wrapper.style.gridTemplateRows = '1fr';
    else if (count === 2)  wrapper.style.gridTemplateRows = `${r}fr ${1 - r}fr`;
    else                   wrapper.style.gridTemplateRows = `repeat(${count}, 1fr)`;
  } else if (count <= 1) {
    wrapper.style.gridTemplateColumns = '1fr';
    wrapper.style.gridTemplateRows    = '1fr';
  } else if (count === 2) {
    wrapper.style.gridTemplateColumns = `${c}fr ${1 - c}fr`;
    wrapper.style.gridTemplateRows    = '1fr';
  } else if (count === 3) {
    // Row 1 = two panes side by side, row 2 = third pane spanning both columns
    wrapper.style.gridTemplateColumns = `${c}fr ${1 - c}fr`;
    wrapper.style.gridTemplateRows    = `${r}fr ${1 - r}fr`;
    if (panes[2]) panes[2].style.gridColumn = '1 / -1';
  } else if (count === 4) {
    // 4 panes (2×2) — both ratios drive the split
    wrapper.style.gridTemplateColumns = `${c}fr ${1 - c}fr`;
    wrapper.style.gridTemplateRows    = `${r}fr ${1 - r}fr`;
  } else {
    // 5+ panes: 2 columns, ceil(count/2) UNIFORM rows. Declaring the exact
    // number of row tracks stops overflow panes from falling into implicit
    // auto-rows (the old bug that left the grid ragged past 4). The vertical
    // splitter still drives the column ratio across every row; the horizontal
    // splitter is dropped for this case (a single handle can't address one
    // boundary among many — see _renderChatSplitters).
    const rows = Math.ceil(count / 2);
    wrapper.style.gridTemplateColumns = `${c}fr ${1 - c}fr`;
    wrapper.style.gridTemplateRows    = `repeat(${rows}, 1fr)`;
    // Odd count → last pane spans both columns so there is no empty cell.
    if (count % 2 === 1 && panes[count - 1]) panes[count - 1].style.gridColumn = '1 / -1';
  }

  _renderChatSplitters(wrapper, count, isMobile);
}

function _renderChatSplitters(wrapper, count, isMobile) {
  // Clear out any existing splitters before re-drawing for the new layout.
  wrapper.querySelectorAll('.chat-splitter').forEach(el => el.remove());
  if (count < 2) return;

  // Use percentage positioning so splitters render correctly even before
  // the wrapper has been measured (avoids the "stuck at left: 0" bug).
  const colPct = (_paneColRatio * 100).toFixed(3) + '%';
  const rowPct = (_paneRowRatio * 100).toFixed(3) + '%';

  if (isMobile) {
    // On mobile the panes stack vertically; a horizontal splitter sits
    // between the first and second pane for the 2-pane case. 3+ panes
    // auto-distribute equally (no splitter) since fitting N-1 splitters
    // with proportional sizing on a phone is more UI than it's worth.
    if (count === 2) {
      const h = document.createElement('div');
      h.className = 'chat-splitter horizontal';
      h.style.top = rowPct;
      wrapper.appendChild(h);
      _setupSplitterDrag(h, 'horizontal');
    }
    return;
  }

  // Vertical splitter — present for any layout with 2 columns (2+ panes)
  const v = document.createElement('div');
  v.className = 'chat-splitter vertical';
  v.style.left = colPct;
  wrapper.appendChild(v);
  _setupSplitterDrag(v, 'vertical');

  // Horizontal splitter — only when the grid is exactly two rows (3–4 panes).
  // For 5+ panes the rows are uniform (repeat(ceil(N/2), 1fr)); a single
  // horizontal splitter can't meaningfully resize one boundary among many,
  // so we omit it and keep the vertical (column) splitter, which still
  // applies uniformly across every row.
  if (count === 3 || count === 4) {
    const h = document.createElement('div');
    h.className = 'chat-splitter horizontal';
    h.style.top = rowPct;
    wrapper.appendChild(h);
    _setupSplitterDrag(h, 'horizontal');
  }
}

function _setupSplitterDrag(splitter, axis) {
  const wrapper = document.getElementById('chats-wrapper');
  splitter.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    splitter.setPointerCapture(e.pointerId);
    splitter.classList.add('dragging');

    const onMove = (ev) => {
      const rect = wrapper.getBoundingClientRect();
      if (axis === 'vertical') {
        const x = ev.clientX - rect.left;
        const ratio = Math.min(0.9, Math.max(0.1, x / rect.width));
        _paneColRatio = ratio;
        wrapper.style.gridTemplateColumns = `${ratio}fr ${1 - ratio}fr`;
        splitter.style.left = (ratio * 100).toFixed(3) + '%';
      } else {
        const y = ev.clientY - rect.top;
        const ratio = Math.min(0.9, Math.max(0.1, y / rect.height));
        _paneRowRatio = ratio;
        wrapper.style.gridTemplateRows = `${ratio}fr ${1 - ratio}fr`;
        splitter.style.top = (ratio * 100).toFixed(3) + '%';
      }
    };

    const onUp = () => {
      splitter.classList.remove('dragging');
      try { splitter.releasePointerCapture(e.pointerId); } catch {}
      splitter.removeEventListener('pointermove', onMove);
      splitter.removeEventListener('pointerup',   onUp);
      splitter.removeEventListener('pointercancel', onUp);
    };

    splitter.addEventListener('pointermove',   onMove);
    splitter.addEventListener('pointerup',     onUp);
    splitter.addEventListener('pointercancel', onUp);
  });
}

// Re-position splitters when the wrapper resizes (window resize, panel
// transitions, mobile rotation, etc.) — the ratio stays but the pixel
// offsets need recomputing.
window.addEventListener('resize', () => {
  const wrapper = document.getElementById('chats-wrapper');
  if (!wrapper) return;
  // Count only VISIBLE panes — a hidden (closed) main pane must not be
  // included or the splitter math drifts out of sync with _updateChatGrid.
  const count = wrapper.querySelectorAll('.chat-pane:not([hidden])').length;
  const isMobile = window.matchMedia('(max-width: 900px)').matches;
  _renderChatSplitters(wrapper, count, isMobile);
});

function addProjectTab() {
  fetch(`${API_BASE}/browse`)
    .then(r => r.json())
    .then(data => {
      if (data.path) {
        playDataSound('engage');
        openProjectWorkspace(data.path, { forceNewPane: true });
      }
    })
    .catch(() => addLog('Browse failed — bridge offline?'));
}

async function openProjectWorkspace(path, opts = {}) {
  // forceNewPane: the caller is an explicit "open a new window/tab" action
  // (the matrix + button, or Data's spawn_workspaces marker). Those must
  // ALWAYS create a separate pane — never silently adopt the main channel,
  // which the Captain reads as "you re-rooted my current window". Adoption
  // is allowed only for the project_rooted fallback (re-root with no main
  // yet) and a DOCUMENTS click on a pristine main chat (openDocsProject).
  const forceNewPane = opts.forceNewPane === true;
  const wsId = ++_wsCounter;
  const name = path.split(/[/\\]/).pop() || `PROJECT ${wsId}`;
  // Decided up front: this project only becomes the bridge's global cwd when
  // it adopts the main channel. A separate window registers register_only so
  // the bridge scans its file tree WITHOUT re-pointing the main channel.
  const isMain = !forceNewPane && !_mainProjectSet;

  // Register project with bridge server
  let projectNodes = [];
  try {
    const res = await fetch(`${API_BASE}/project`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, register_only: !isMain }),
    });
    const data = await res.json();
    if (data.error) { addLog('Project error: ' + data.error); _wsCounter--; return; }
    projectNodes = data.nodes || [];
  } catch (e) {
    addLog('Could not register project: ' + e.message);
  }

  // Each workspace gets its own provider slot. Defaults to whatever the global
  // pill is currently set to; user can change it from the in-pane dropdown so
  // multiple windows can talk to different models simultaneously.
  // `role` is set when Data spawns the pane via spawn_workspaces; it gets
  // prepended to every user message from that pane so the LLM stays in role.
  const defaultProvider = _lastKnownActiveProvider || 'claude-cli';
  // Each pane gets its own agent — inherits whatever the main channel is set
  // to right now, then can be flipped independently from the pane's header.
  _workspaces.set(wsId, { path, name, isThinking: false, projectNodes, isMain, provider: defaultProvider, role: '', crew: MAIN_CHAT_CREW });

  addMatrixProjectTab(wsId, name, path, projectNodes);

  if (isMain) {
    _mainProjectSet = true;
    // If the main pane was hidden by a previous close, bring it back so
    // its chat-window is visible again before we attach the project to it.
    const mainPane = document.getElementById('pane-main');
    if (mainPane && mainPane.hidden) mainPane.hidden = false;
    _setMainPaneProject(wsId, name, path);
  } else {
    addChatPane(wsId, name, path);
    _showPaneHeaders(true);
    _updateChatGrid();
  }

  renderProjectMiniMatrix(projectNodes, path);
  playDataSound('engage');
  addLog(`Workspace: ${name}`);
  showPanel('chat');
  return wsId;
}

function addMatrixProjectTab(wsId, name, path, projectNodes) {
  const tabsEl = document.querySelector('.matrix-tabs');
  const addBtn = document.getElementById('mtab-add');

  const tab = document.createElement('button');
  tab.className = 'matrix-tab matrix-tab-project';
  tab.id = `mtab-ws${wsId}`;
  tab.innerHTML = `${name} <button class="tab-close-x" title="Close workspace">✕</button>`;
  tab.querySelector('.tab-close-x').addEventListener('click', e => {
    e.stopPropagation();
    closeProjectWorkspace(wsId);
  });
  tab.addEventListener('click', () => switchMatrixTab(`ws${wsId}`));
  tabsEl.insertBefore(tab, addBtn);

  // Create subpanel
  const sub = document.createElement('div');
  sub.className = 'matrix-subpanel';
  sub.id = `msub-ws${wsId}`;

  const controls = document.createElement('div');
  controls.className = 'matrix-controls';
  controls.innerHTML = `
    <span style="font-family:var(--font-mono);font-size:10px;color:var(--data-teal);flex:1;
      overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${path}</span>
    <button class="data-btn-sm blue" onclick="fitMatrix()">FIT</button>
    <button class="data-btn-sm orange" onclick="zoomMatrix(1.4)">+</button>
    <button class="data-btn-sm orange" onclick="zoomMatrix(0.7)">−</button>
  `;
  sub.appendChild(controls);

  const graphDiv = document.createElement('div');
  graphDiv.id = `ws-graph-${wsId}`;
  graphDiv.style.cssText = 'flex:1;overflow:hidden;background:#020208;';
  sub.appendChild(graphDiv);

  // Insert subpanel into the matrix panel area
  const msub_computer = document.getElementById('msub-computer');
  msub_computer.parentElement.appendChild(sub);

  if (projectNodes && projectNodes.length) {
    setTimeout(() => loadProjectGraph(wsId, path, projectNodes), 50);
  }
}

function loadProjectGraph(wsId, path, nodes) {
  const container = document.getElementById(`ws-graph-${wsId}`);
  if (!container) return;

  const rootName = path.split(/[/\\]/).pop() || 'PROJECT';
  const rootId = `ws${wsId}-root`;
  const d3nodes = [{ id: rootId, label: rootName, type: 'core', r: 18, hub: true, path }];
  const d3links = [];

  // Track the last dir node seen at each depth so files link to their parent
  const parentAtDepth = { 0: rootId };

  nodes.slice(0, 300).forEach((n, i) => {
    const nid = `ws${wsId}-n${i}`;
    const ftype = n.type === 'dir' ? 'folder' : _miniFileType(n.name);
    d3nodes.push({
      id: nid, label: n.name, type: ftype,
      r: n.type === 'dir' ? 10 : 6, hub: n.type === 'dir', path: n.path,
    });
    const parentId = parentAtDepth[n.depth - 1] || rootId;
    d3links.push({ source: parentId, target: nid, w: n.depth <= 1 ? 1.5 : 1 });
    if (n.type === 'dir') parentAtDepth[n.depth] = nid;
  });

  renderComputerGraph(d3nodes, d3links, path, container);
}

function _setMainPaneProject(wsId, name, path) {
  const header = document.getElementById('pane-main-header');
  if (!header) return;

  // The main pane's crew dropdown stays driven by MAIN_CHAT_CREW even when a
  // project is loaded — its onchange still routes through setPaneCrew('main', ...).
  header.innerHTML = `
    <span class="pane-name">${name.toUpperCase()}</span>
    <span class="pane-path">${path}</span>
    <select class="pane-crew-select" id="pane-crew-main"
            title="Agent for this chat window"
            onchange="setPaneCrew('main', this.value)">${_crewSelectOptionsHTML(MAIN_CHAT_CREW)}</select>
    ${_windowProviderSelectHTML(wsId)}
    <button class="chat-pane-close">✕ CLOSE</button>
  `;
  header.querySelector('.chat-pane-close').addEventListener('click', () => closeProjectWorkspace(wsId));
  header.classList.remove('hidden');
  _populateWindowProviderSelect(wsId);

  // Announce in main chat
  const winEl = document.getElementById('chat-window');
  if (winEl) {
    appendMessage('data', `Project workspace initialized for **${name}**. I am now monitoring \`${path}\` and will read files on demand. How may I assist you with this project, Captain?`);
  }
}

function addChatPane(wsId, name, path) {
  const wrapper = document.getElementById('chats-wrapper');
  const pane = document.createElement('div');
  pane.className = 'chat-pane';
  pane.id = `pane-ws${wsId}`;

  const inputId = `pane-input-ws${wsId}`;
  const paneKey = `ws${wsId}`;
  const trayId = _trayIdForPane(paneKey);
  const fileInputId = _fileInputIdForPane(paneKey);
  const ws = _workspaces.get(wsId);
  const paneCrew = ws?.crew || MAIN_CHAT_CREW;
  pane.innerHTML = `
    <div class="chat-pane-header" id="pane-ws${wsId}-header">
      <span class="pane-name">${name.toUpperCase()}</span>
      <span class="pane-path">${path}</span>
      ${_crewSelectHTML(`ws${wsId}`, paneCrew)}
      ${_windowProviderSelectHTML(wsId)}
      <button class="chat-pane-close">✕ CLOSE</button>
    </div>
    <div class="chat-window" id="chat-win-ws${wsId}"></div>
    <div class="chat-input-area">
      <!-- Drag up/down to resize this pane's prompt box. Height persists per project. -->
      <div class="input-resize-handle" id="pane-resizer-ws${wsId}"
           title="Drag to resize the prompt area" role="separator"
           aria-orientation="horizontal"></div>
      <div class="attachment-tray" id="${trayId}" hidden></div>
      <div class="input-row">
        <div class="input-prefix">CAPTAIN ›</div>
        <div class="input-wrapper has-attach">
          <textarea class="data-input" id="${inputId}" rows="2"
            placeholder="${name} — type or use voice..."
            onfocus="setActiveInput(this.id)"></textarea>
          <div class="input-right-btns">
            <button class="dictate-btn" data-target-input="${inputId}" onclick="toggleDictation(this)" title="Dictate to text box">🎙</button>
            <button class="attach-btn" type="button"
                    onclick="document.getElementById('${fileInputId}').click()"
                    title="Attach files (images, PDFs, text, audio)">+</button>
            <input type="file" id="${fileInputId}" multiple hidden
                   accept="image/*,application/pdf,audio/*,text/*,.md,.markdown,.py,.js,.ts,.jsx,.tsx,.json,.yaml,.yml,.toml,.html,.css,.sh,.bat,.ps1,.csv,.log,.mp3,.m4a,.m4r,.wav,.ogg,.webm,.aac,.flac,.opus"
                   onchange="handleAttachmentFiles(this.files, '${paneKey}'); this.value=''"/>
          </div>
        </div>
        <button class="send-btn" id="pane-send-ws${wsId}">TRANSMIT</button>
      </div>
    </div>
  `;

  pane.querySelector('.chat-pane-close').addEventListener('click', () => closeProjectWorkspace(wsId));
  pane.querySelector(`#${inputId}`).addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendProjectMessage(wsId); }
  });
  pane.querySelector(`#pane-send-ws${wsId}`).addEventListener('click', () => sendProjectMessage(wsId));
  // Wire this pane's prompt-box resize handle. Persist height per project path
  // so reopening the same project restores its chosen size.
  _wireInputResizer(
    pane.querySelector(`#pane-resizer-ws${wsId}`),
    pane.querySelector(`#${inputId}`),
    `${_CHAT_INPUT_HEIGHT_KEY}:${path || `ws${wsId}`}`
  );

  wrapper.appendChild(pane);
  _populateWindowProviderSelect(wsId);

  appendMessageToPane(
    document.getElementById(`chat-win-ws${wsId}`),
    'data',
    `Project workspace initialized for **${name}**. I am monitoring \`${path}\` and will read files on demand. How may I assist you with this project, Captain?`,
    paneCrew
  );
}

function closeMainPane() {
  // The pristine MAIN CHANNEL pane (no project loaded) is not in _workspaces,
  // so it has no wsId. Closing it just hides pane-main so the next open pane
  // becomes the leftmost ("first") window — the same promote behavior as
  // closing a rooted main pane. Never leave zero visible panes: if main is the
  // only one, do nothing (the button is hidden in that state anyway).
  const wrapper = document.getElementById('chats-wrapper');
  const visible = wrapper.querySelectorAll('.chat-pane:not([hidden])').length;
  if (visible <= 1) { addLog('Main channel is the only window — nothing to promote'); return; }
  // Stop any in-flight main-pane turn before closing — otherwise the bridge's
  // claude --print subprocess keeps running to completion in the background,
  // burning compute/quota on a response no window will read. stopData() aborts
  // the fetch AND tells the bridge to tree-kill this pane's CLI subprocess.
  if (isThinking) stopData();
  const mainPane = document.getElementById('pane-main');
  if (mainPane) mainPane.hidden = true;
  _showPaneHeaders(true);
  _updateChatGrid();
  addLog('Main channel closed — next window promoted');
}

function closeProjectWorkspace(wsId) {
  const ws = _workspaces.get(wsId);
  // Stop any in-flight turn BEFORE tearing down the pane. Without this the
  // frontend removes the window but the bridge's claude --print subprocess
  // (and its MCP node children) keep running to completion in the background —
  // computing output nobody will ever read and burning subscription quota.
  // Mirrors the Escape/Stop key handler's per-pane /stop call; the bridge
  // tree-kills only THIS pane's subprocess thanks to per-project tracking.
  if (ws?.isMain) {
    // The rooted main pane is driven by the global in-flight state.
    if (isThinking) stopData();
  } else if (ws?.isThinking && ws.abortController) {
    try { ws.abortController.abort(); } catch {}
    fetch(`${API_BASE}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_path: ws.path || '',
        pane_id:      _paneId(`ws${wsId}`),
      }),
    }).catch(() => {});
    ws.isThinking = false;
    ws.abortController = null;
  }
  _workspaces.delete(wsId);

  document.getElementById(`mtab-ws${wsId}`)?.remove();
  document.getElementById(`msub-ws${wsId}`)?.remove();

  if (ws?.isMain) {
    // Reset the main pane header to the default no-project state so that if
    // the pane is restored later (next project load, or last pane closed)
    // it comes back clean instead of with a stale project name.
    const header = document.getElementById('pane-main-header');
    if (header) {
      header.innerHTML = `
        <span class="pane-name">MAIN CHANNEL</span>
        <span class="pane-path"></span>
        <select class="pane-crew-select" id="pane-crew-main"
                title="Agent for this chat window"
                onchange="setPaneCrew('main', this.value)">${_crewSelectOptionsHTML(MAIN_CHAT_CREW)}</select>
        <select class="pane-provider-select" id="main-provider-select"
                title="Model for the main chat"
                onchange="setMainProvider(this.value)"></select>
        <button class="chat-pane-close" id="main-close-empty"
                style="display:none" title="Close — promote the next window"
                onclick="closeMainPane()">✕ CLOSE</button>
      `;
      header.classList.remove('hidden');
      _populateMainProviderSelect();
    }
    _mainProjectSet = false;
    fetch(`${API_BASE}/project`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '' }),
    }).catch(() => {});
    renderMiniMatrix();

    // If there are other workspaces still open, fully hide the main pane so
    // the next pane visually becomes the leftmost ("first") pane. We hide
    // rather than remove because the static IDs inside pane-main (chat-window,
    // chat-input, send-btn) are referenced from many places — keeping the
    // DOM around lets us simply unhide it when a new project loads or when
    // every other pane is closed. (Project panes are still removed outright
    // in the else-branch below — they have unique per-workspace IDs.)
    const mainPane = document.getElementById('pane-main');
    if (mainPane && _workspaces.size > 0) mainPane.hidden = true;
  } else {
    document.getElementById(`pane-ws${wsId}`)?.remove();
    _pendingAttachmentsByPane.delete(`ws${wsId}`);
  }

  // Safety net — never leave the Captain with zero visible panes. If main
  // was hidden by a previous close and the last project pane just went
  // away, bring main back so there's always something to chat with.
  const wrapper = document.getElementById('chats-wrapper');
  const visibleCount = wrapper.querySelectorAll('.chat-pane:not([hidden])').length;
  if (visibleCount === 0) {
    const mainPane = document.getElementById('pane-main');
    if (mainPane) mainPane.hidden = false;
  }

  // Hide spawned-pane headers if only main pane remains and no project is set
  if (_workspaces.size === 0) _showPaneHeaders(false);
  _updateChatGrid();
  if (currentMatrixTab === `ws${wsId}`) switchMatrixTab('graph');

  addLog(`Workspace closed`);
}

async function sendProjectMessage(wsId) {
  const ws = _workspaces.get(wsId);
  if (!ws || ws.isThinking) return;
  const input = document.getElementById(`pane-input-ws${wsId}`);
  const text = input?.value.trim() || '';
  const paneKey = `ws${wsId}`;
  const paneBucket = _getPendingForPane(paneKey);
  // Allow sending with only attachments (no text) — matches main pane.
  if (!text && paneBucket.length === 0) return;
  const winEl = document.getElementById(`chat-win-ws${wsId}`);
  if (!winEl) return;

  // Snapshot + clear staged attachments now so a fast second submit
  // can't double-send them.
  const attachments = paneBucket.splice(0);
  _renderAttachmentTray(paneKey);

  if (input) input.value = '';
  ws.isThinking = true;
  // Esc routes to whoever was last interacted with; sending counts.
  _lastActiveWsId = wsId;
  // Per-window abort controller so Esc on this pane only cancels THIS fetch.
  // Capture a local ref too — the watchdog needs a stable handle even if
  // ws.abortController gets nulled out by another code path mid-flight.
  const controller = new AbortController();
  ws.abortController = controller;
  // IDLE WATCHDOG (heartbeat-driven): a long Codex/Claude build can take
  // 30 + minutes, but a healthy worker is NEVER silent — the bridge streams
  // thinking lines, tool calls, and token chunks throughout, plus a
  // `: keepalive` SSE comment every 8 s when the runner is between events.
  // So we don't cap total elapsed time; we cap silence. If no bytes arrive
  // for IDLE_LIMIT_MS, the worker is dead (TCP drop, child crash, OS kill,
  // backgrounded-tab throttle) — abort. The timer is reset on every read.
  // 90 s = ~11 missed keepalives → definitely not just a slow tool call.
  let watchdogFired = false;
  const IDLE_LIMIT_MS = 90 * 1000;
  let idleTimer = null;
  const resetIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      watchdogFired = true;
      try { controller.abort(); } catch {}
    }, IDLE_LIMIT_MS);
  };
  playDataSound('transmit');
  let bubbleText = text;
  if (attachments.length) {
    const list = attachments.map(a => `📎 ${a.name}`).join('\n');
    bubbleText = text ? `${text}\n\n${list}` : list;
  }
  appendMessageToPane(winEl, 'user', bubbleText, ws.crew);
  const thoughtEl = _createPaneThoughtStream(winEl, ws.crew, ws);

  // Per-window role: when Data spawned this pane with an assignment, prepend
  // it to every message so the LLM stays in character across turns. The user
  // sees their plain text in the chat bubble — only the wire payload carries
  // the [Assignment: ...] prefix.
  const wireText = ws.role
    ? `[Assignment for this window: ${ws.role}]\n\n${text}`
    : text;

  try {
    resetIdle();
    const res = await fetch(`${API_BASE}/chat_stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        message:      wireText,
        project_path: ws.path,
        pane_id:      _paneId(`ws${wsId}`),   // session-tagged: isolates this pane from siblings AND from prior sessions
        provider:     ws.provider,   // per-window model override
        crew:         ws.crew,       // per-window agent (officer persona)
        attachments,
      }),
    });
    // Surface 400s (e.g. attachments + text-only provider) before stream parse.
    if (res.status === 400) {
      removeThinkingFromPane(winEl);
      let errMsg = `HTTP 400`;
      try { const j = await res.json(); if (j && j.error) errMsg = j.error; } catch {}
      appendMessageToPane(winEl, 'data', errMsg, ws.crew);
      playDataSound('error');
    } else if (!res.ok || !res.body) {
      removeThinkingFromPane(winEl);
      appendMessageToPane(winEl, 'data', offlineResponse(text), ws.crew);
      playDataSound('error');
    } else {
      // Stream the SSE response. Every chunk (event OR `: keepalive`) resets
      // the idle watchdog. Token events accumulate into one final bubble so
      // the pane UX matches the previous single-bubble behavior.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let answer = '';
      let serverError = '';
      let streamDone = false;
      let paneMeta = null;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        resetIdle();  // ← heartbeat: any byte = worker still alive
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop();
        for (const eventStr of events) {
          if (!eventStr.trim() || eventStr.startsWith(':')) continue;  // skip keepalives
          let evType = 'message', evData = '';
          for (const line of eventStr.split('\n')) {
            if      (line.startsWith('event: ')) evType = line.slice(7).trim();
            else if (line.startsWith('data: '))  evData = line.slice(6).trim();
          }
          if (!evData) continue;
          try {
            const payload = JSON.parse(evData);
            if (evType === 'token')        answer += payload.text || '';
            else if (evType === 'thinking') _addThoughtLine(thoughtEl, payload.text);
            else if (evType === 'meta')    { try { paneMeta = JSON.parse(payload.text); } catch { paneMeta = null; } }
            else if (evType === 'error')   serverError = payload.text || '';
            else if (evType === 'done')    streamDone = true;
          } catch { /* malformed SSE line, skip */ }
        }
      }
      const finalText = answer || serverError || offlineResponse(text);
      if (answer) {
        // Clean reply — dim the reasoning trail and let it linger ~2s.
        _finalizePaneThoughtStream(thoughtEl, paneMeta);
      } else {
        // No answer (error/empty) — pull the trail immediately, no fake history.
        removeThinkingFromPane(winEl);
      }
      appendMessageToPane(winEl, 'data', finalText, ws.crew);
      if (streamDone && answer) {
        playDataSound('receive');
        addLog(`[${ws.name}] ${crewLabel(ws.crew)} responded`);
      } else {
        playDataSound('error');
      }
    }
  } catch {
    removeThinkingFromPane(winEl);
    // Distinguish an idle-watchdog abort (worker went silent — likely dead)
    // from a user Esc or bridge-offline failure, so the Captain knows the
    // pane stalled rather than thinking the bridge is offline.
    const msg = watchdogFired
      ? `⏱ Worker stalled — no output for ${IDLE_LIMIT_MS / 1000}s. The CLI process likely crashed. The pane is unlocked; try resending, or check the bridge logs.`
      : offlineResponse(text);
    appendMessageToPane(winEl, 'data', msg, ws.crew);
    playDataSound('error');
    if (watchdogFired) addLog(`[${ws.name}] Worker went silent — aborted by idle watchdog`);
  } finally {
    // GUARANTEED cleanup — runs on success, error, abort, and watchdog fire.
    // The previous version reset state OUTSIDE the try, so if the await ever
    // resolved to neither value nor exception, the pane locked permanently.
    if (idleTimer) clearTimeout(idleTimer);
    ws.isThinking = false;
    ws.abortController = null;
    fetchVitals();
  }
}

// ═══════════════════════════════════════════════════════════
// UI EVENTS — bridge → dashboard side channel
// Lets Data's tools push commands to the UI (e.g. spawn N project
// windows). Frontend polls /ui_events every ~1.5s and dispatches.
// ═══════════════════════════════════════════════════════════
// Stable per-tab client id so the bridge can keep a private event queue for
// THIS dashboard instead of a single shared deque that concurrent tabs would
// race to drain. Persisted in sessionStorage so a reload reuses the same id
// (one tab == one client); each separate tab/window gets its own.
const _UI_CLIENT_ID = (() => {
  try {
    let id = sessionStorage.getItem('dataUiClientId');
    if (!id) {
      id = (crypto?.randomUUID?.() ||
            `c${Date.now()}-${Math.random().toString(36).slice(2)}`);
      sessionStorage.setItem('dataUiClientId', id);
    }
    return id;
  } catch (_) {
    return `c${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
})();

async function _pollUiEvents() {
  try {
    const res = await fetch(`${API_BASE}/ui_events?client_id=${encodeURIComponent(_UI_CLIENT_ID)}`);
    const { events } = await res.json();
    for (const evt of (events || [])) _handleUiEvent(evt);
  } catch (e) { /* bridge offline — silent */ }
}
setInterval(_pollUiEvents, 1500);

function _handleUiEvent(evt) {
  if (evt.type === 'spawn_workspaces') {
    _spawnWorkspacesFromEvent(evt.payload?.workspaces || []);
  } else if (evt.type === 'project_rooted') {
    const p = evt.payload || {};
    const newPath = p.path || '';
    const newName = p.name || (newPath.split(/[/\\]/).pop()) || '(unknown)';
    // Originating pane key (lowercased pre-root path of the workspace that
    // asked). When present, target THAT workspace; fall back to the main
    // workspace only for events with no pane (POST /project, standing orders).
    const paneKey = (p.pane || '').toLowerCase();

    // Pick the workspace to re-root: pane-match first, then main as fallback.
    let targetWsId = null;
    let targetWs   = null;
    if (paneKey) {
      for (const [id, w] of _workspaces.entries()) {
        // The bridge tags the event with the originating pane's FULL history
        // key — `path::<pane_id>` (see _history_key on the backend). Rebuild
        // the same composite key here so the re-root lands on the exact pane
        // that asked, even when two windows share a folder. The base segment
        // mirrors what each pane sends as pane_id: 'main' for the main pane,
        // 'ws<id>' for secondaries.
        const wsPath = (w.path || '').toLowerCase();
        const base   = w.isMain ? 'main' : `ws${id}`;
        const wsKey  = `${wsPath}::${_paneId(base).toLowerCase()}`;
        // Match the composite key first; fall back to a plain path match so
        // legacy/no-pane_id callers (POST /project, standing orders) still work.
        if (paneKey === wsKey || paneKey === wsPath) { targetWsId = id; targetWs = w; break; }
      }
    }
    if (!targetWs) {
      for (const [id, w] of _workspaces.entries()) {
        if (w.isMain) { targetWsId = id; targetWs = w; break; }
      }
    }
    const targetIsMain = !!targetWs?.isMain;

    addLog(`Rooted ${targetIsMain ? 'main pane' : `pane ${targetWs?.name || targetWsId}`} → ${newName}`);
    playDataSound('engage');
    // Always drop a visible confirmation bubble in the chat that asked. The
    // LLM's own post-marker confirmation often gets cut off by the backstop
    // kill (or never streams at all if the model jumped into another tool
    // call), so this guarantees the Captain sees what happened — and in the
    // window they were typing in, not always the main one.
    const targetWin = targetWsId != null ? document.getElementById(`chat-win-ws${targetWsId}`) : null;
    if (targetWin) {
      appendMessageToPane(targetWin, 'data', `Rooted in **${newName}**, Captain. \`${newPath}\``, targetWs?.crew);
    } else {
      appendMessage('data', `Rooted in **${newName}**, Captain. \`${newPath}\``);
    }

    // No workspace at all (main hasn't opened yet, no pane match) — fall back
    // to the same flow Data uses for "spawn a new project window", which
    // builds the pane header, matrix tab, file tree, etc. The bridge already
    // set _project_path via the marker, so openProjectWorkspace just re-confirms it.
    if (!targetWs && newPath) {
      openProjectWorkspace(newPath);
      return;
    }

    if (targetWs && newPath) {
      // 1. Update the in-memory workspace so the next /chat request
      //    sends the NEW project_path rather than the old one.
      targetWs.path = newPath;
      targetWs.name = newName;
      if (Array.isArray(p.nodes)) targetWs.projectNodes = p.nodes;

      // 2. Refresh the pane header. The main pane's header has a fixed id;
      //    secondary panes use the per-workspace id pattern.
      const headerId = targetIsMain ? 'pane-main-header' : `pane-ws${targetWsId}-header`;
      const header = document.getElementById(headerId);
      if (header) {
        const nameEl = header.querySelector('.pane-name');
        const pathEl = header.querySelector('.pane-path');
        if (nameEl) nameEl.textContent = newName.toUpperCase();
        if (pathEl) pathEl.textContent = newPath;
      }

      // 3. Refresh the matrix tab label (preserves the close button).
      const tab = document.getElementById(`mtab-ws${targetWsId}`);
      if (tab) {
        const closeBtn = tab.querySelector('.tab-close-x');
        tab.textContent = newName + ' ';
        if (closeBtn) tab.appendChild(closeBtn);
      }

      // 4. Refresh the path label inside the matrix subpanel controls.
      const sub = document.getElementById(`msub-ws${targetWsId}`);
      if (sub) {
        const pathSpan = sub.querySelector('.matrix-controls span');
        if (pathSpan) pathSpan.textContent = newPath;
      }

      // 5. Rebuild the d3 project graph for the matrix tab with new nodes.
      if (Array.isArray(p.nodes)) {
        loadProjectGraph(targetWsId, newPath, p.nodes);
      }
    }

    // Refresh the project mini-matrix in the main pane only when the re-root
    // affected main — otherwise we'd overwrite main's tree with a secondary
    // pane's nodes.
    if (targetIsMain && Array.isArray(p.nodes)) renderProjectMiniMatrix(p.nodes, newPath);
  } else if (evt.type === 'ask_options') {
    _renderAskOptions(evt.payload || {});
  } else {
    addLog(`Unknown UI event: ${evt.type}`);
  }
}

// ── ask_options — the assistant poses a clickable choice in the chat ──
// Emitted via an <<ask_options>> marker when a decision is the Captain's to
// make; the bridge forwards it here. We render the question with option
// buttons in the pane that asked. Tapping one (or typing via "Other…") sends
// it as the Captain's next message, so the conversation continues in place.
function _renderAskOptions(payload) {
  const question = (payload.question || '').trim();
  const options  = Array.isArray(payload.options) ? payload.options : [];
  if (!question || !options.length) return;

  // Resolve which chat window asked — mirror the project_rooted pane matching
  // so the card lands in the right pane, falling back to the main window.
  const paneKey = (payload.pane || '').toLowerCase();
  let targetWsId = null, targetWs = null;
  if (paneKey) {
    for (const [id, w] of _workspaces.entries()) {
      const wsPath = (w.path || '').toLowerCase();
      const base   = w.isMain ? 'main' : `ws${id}`;
      const wsKey  = `${wsPath}::${_paneId(base).toLowerCase()}`;
      if (paneKey === wsKey || paneKey === wsPath) { targetWsId = id; targetWs = w; break; }
    }
  }
  if (!targetWs) {
    for (const [id, w] of _workspaces.entries()) { if (w.isMain) { targetWsId = id; targetWs = w; break; } }
  }
  const targetIsMain = !!targetWs?.isMain;
  const paneWin = (targetWsId != null && !targetIsMain)
    ? document.getElementById(`chat-win-ws${targetWsId}`) : null;
  const chatWin = paneWin || document.getElementById('chat-window');
  if (!chatWin) return;

  playDataSound('doorbell');

  const crewId = targetIsMain ? MAIN_CHAT_CREW : (targetWs?.crew || MAIN_CHAT_CREW);
  const card = document.createElement('div');
  card.className = 'chat-message data ask-options-card';
  const optBtns = options.map((o, i) =>
    `<button class="ask-opt-btn" data-idx="${i}">${escapeHtml(o)}</button>`).join('');
  card.innerHTML = `
    <div class="avatar">${_crewAvatar(crewId)}</div>
    <div class="bubble ask-options-bubble">
      <div class="ask-options-q">${escapeHtml(question)}</div>
      <div class="ask-options-row">
        ${optBtns}
        <button class="ask-opt-btn ask-opt-other">✎ Other…</button>
      </div>
    </div>`;

  card.querySelectorAll('.ask-opt-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      if (card.classList.contains('answered')) return;
      if (btn.classList.contains('ask-opt-other')) {
        _focusPaneInput(targetWsId, targetIsMain);
        return;
      }
      _answerAskOptions(card, options[parseInt(btn.dataset.idx, 10)], targetWsId, targetIsMain);
    });
  });

  const wasPinned = _isPinnedToBottom(chatWin);
  chatWin.appendChild(card);
  if (wasPinned) chatWin.scrollTop = chatWin.scrollHeight;
  addLog(`Asking: ${question.substring(0, 40)}…`);
}

function _answerAskOptions(card, choice, wsId, isMain) {
  card.classList.add('answered');
  card.querySelectorAll('.ask-opt-btn').forEach(b => {
    b.disabled = true;
    if (b.textContent === choice) b.classList.add('chosen');
  });
  if (isMain || wsId == null) {
    const input = document.getElementById('chat-input');
    if (input) input.value = choice;
    sendMessage();
  } else {
    const input = document.getElementById(`pane-input-ws${wsId}`);
    if (input) input.value = choice;
    sendProjectMessage(wsId);
  }
}

function _focusPaneInput(wsId, isMain) {
  const id = (isMain || wsId == null) ? 'chat-input' : `pane-input-ws${wsId}`;
  document.getElementById(id)?.focus();
}

async function _spawnWorkspacesFromEvent(specs) {
  if (!specs.length) return;
  playDataSound('doorbell');
  addLog(`Spawning ${specs.length} project window(s) on Data's order...`);
  for (const spec of specs) {
    const wsId = await openProjectWorkspace(spec.path, { forceNewPane: true });
    if (!wsId) {
      addLog(`Could not open ${spec.path}`);
      continue;
    }
    const ws = _workspaces.get(wsId);
    if (!ws) continue;

    // Pin the provider this window will use
    ws.provider = spec.provider;
    ws.role     = spec.role;
    _populateWindowProviderSelect(wsId);

    // Drop the role briefing into the pane chat so the Captain (and the LLM
    // when the user sends a follow-up) sees the assignment up front.
    const briefing =
      `**ASSIGNMENT** — *${spec.role}*\n\n` +
      `This window is running **${spec.provider}**. ` +
      `Every message you send here will be prefixed with this assignment so I stay in role.`;
    if (ws.isMain) {
      appendMessage('data', briefing);
    } else {
      const winEl = document.getElementById(`chat-win-ws${wsId}`);
      if (winEl) appendMessageToPane(winEl, 'data', briefing, ws.crew);
    }
  }
}

function appendMessageToPane(winEl, role, text, crewId) {
  const msg = document.createElement('div');
  msg.className = `chat-message ${role}`;
  const isData = role === 'data';
  const crew = crewId || 'data';
  const avatar = isData ? _crewAvatar(crew) : 'C';
  const sender = isData ? crewLabel(crew).toUpperCase() : 'CAPTAIN';
  const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
  msg.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div class="bubble">
      ${isData ? '<button class="tts-btn" title="Speak">▶</button>' : ''}
      <button class="copy-btn" title="Copy">⧉</button>
      <div class="sender">${sender}</div>
      <div class="text md-content">${renderMarkdown(text)}</div>
      <div class="timestamp">${ts}</div>
    </div>
  `;
  if (isData) {
    msg.querySelector('.tts-btn').addEventListener('click', function() {
      toggleTts(this, text);
    });
  }
  msg.querySelector('.copy-btn').addEventListener('click', function() {
    navigator.clipboard.writeText(text).then(() => {
      this.textContent = '✓'; this.classList.add('copied');
      setTimeout(() => { this.textContent = '⧉'; this.classList.remove('copied'); }, 1500);
    }).catch(() => { this.textContent = '!'; setTimeout(() => { this.textContent = '⧉'; }, 1500); });
  });
  const wasPinned = _isPinnedToBottom(winEl);
  winEl.appendChild(msg);
  if (wasPinned) winEl.scrollTop = winEl.scrollHeight;
}

function appendThinkingToPane(winEl, crewId) {
  playDataSound('processing');
  const crew = crewId || MAIN_CHAT_CREW;
  const msg = document.createElement('div');
  msg.className = 'chat-message data';
  msg.dataset.thinking = '1';
  msg.innerHTML = `
    <div class="avatar">${_crewAvatar(crew)}</div>
    <div class="bubble">
      <div class="sender">${crewLabel(crew).toUpperCase()}</div>
      <div class="text thinking-dots">Processing<span>.</span><span>.</span><span>.</span></div>
    </div>
  `;
  const wasPinned = _isPinnedToBottom(winEl);
  winEl.appendChild(msg);
  if (wasPinned) winEl.scrollTop = winEl.scrollHeight;
}

// Best-effort model label for a project pane, so its thought stream can open
// with something concrete instead of a blank line.
function _paneProviderLabel(ws) {
  const id = ws?.provider || _lastKnownActiveProvider;
  const p = (_providersCache || []).find(x => x.id === id);
  return (p && p.label) ? p.label : 'the main computer core';
}

// Per-pane rolling thought stream — the project-pane equivalent of the main
// chat's _createThoughtStream. State (start time, timer interval) lives ON the
// element, NOT in globals, so multiple panes can stream concurrently without
// clobbering each other's timer. Reuses the generic _addThoughtLine for steps.
function _createPaneThoughtStream(winEl, crewId, ws) {
  playDataSound('processing');
  const el = document.createElement('div');
  el.className = 'thought-stream';
  el.dataset.thinking = '1';   // so removeThinkingFromPane() clears it on error paths
  el.innerHTML =
    '<div class="thought-stream-top">' +
      '<div class="thought-stream-header">NEURAL INNER MONOLOGUE</div>' +
      '<span class="thought-timer">0.0s</span>' +
    '</div>' +
    '<div class="thought-lines"></div>';
  el._tsStart = Date.now();
  _addThoughtLine(el, `*Engaging ${_paneProviderLabel(ws)}…*`);
  const wasPinned = _isPinnedToBottom(winEl);
  winEl.appendChild(el);
  if (wasPinned) winEl.scrollTop = winEl.scrollHeight;
  const timerEl = el.querySelector('.thought-timer');
  el._tsTimer = setInterval(() => {
    timerEl.textContent = ((Date.now() - el._tsStart) / 1000).toFixed(1) + 's';
  }, 100);
  return el;
}

// Dim the trail, fold token stats into the timer chip, and remove after ~2s —
// mirrors the main chat's `done` handling. Called only on clean completion;
// error paths use removeThinkingFromPane() for immediate removal.
function _finalizePaneThoughtStream(el, meta) {
  if (!el) return;
  if (el._tsTimer) { clearInterval(el._tsTimer); el._tsTimer = null; }
  const timerEl = el.querySelector('.thought-timer');
  if (timerEl) {
    const elapsed = ((Date.now() - (el._tsStart || Date.now())) / 1000).toFixed(1);
    const fmt = n => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
    const hasTokens = meta && (meta.input_tokens > 0 || meta.output_tokens > 0);
    timerEl.textContent = hasTokens
      ? `${elapsed}s · ${fmt(meta.input_tokens)}↓ ${fmt(meta.output_tokens)}↑`
      : `${elapsed}s`;
  }
  el.removeAttribute('data-thinking');   // we own removal now
  el.classList.add('done');
  setTimeout(() => el.remove(), 2000);
}

function removeThinkingFromPane(winEl) {
  winEl.querySelectorAll('[data-thinking]').forEach(el => {
    if (el._tsTimer) clearInterval(el._tsTimer);   // stop the thought-stream ticker if present
    el.remove();
  });
}

function toggleChatFullscreen() {
  playDataSound('confirm');
  const panel = document.getElementById('panel-chat');
  const btn = document.getElementById('fullscreen-btn');
  panel.classList.toggle('fullscreen');
  btn.textContent = panel.classList.contains('fullscreen') ? '⊡' : '⛶';
}

// ── Find in conversation ─────────────────────────────────
// Client-side search over the main channel's rendered messages. Matches are
// wrapped in <mark class="chat-find-hl"> spans (walking text nodes only, so the
// rendered markdown/HTML is never corrupted) and can be stepped through. The
// highlights are fully removed when the bar closes or the query is cleared, so
// the message DOM always returns to its original state.
let _chatFindMarks = [];      // <mark> elements for the current query, in order
let _chatFindIdx   = -1;      // index of the currently-focused match
let _chatFindDebounce = null;

function toggleChatFind(force) {
  const bar = document.getElementById('chat-find-bar');
  if (!bar) return;
  const show = (force === undefined) ? bar.hidden : force;
  bar.hidden = !show;
  if (show) {
    playDataSound('confirm');
    const input = document.getElementById('chat-find-input');
    input.focus();
    input.select();
    if (input.value.trim()) chatFindRun(input.value);
  } else {
    _chatFindClearHighlights();
    _chatFindIdx = -1;
    _updateChatFindCount();
  }
}

function chatFindKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); toggleChatFind(false); return; }
  if (e.key === 'Enter')  { e.preventDefault(); chatFindStep(e.shiftKey ? -1 : 1); }
}

// Remove every highlight span, restoring the original text nodes. Idempotent.
function _chatFindClearHighlights() {
  const win = document.getElementById('chat-window');
  if (win) {
    win.querySelectorAll('mark.chat-find-hl').forEach(m => {
      const parent = m.parentNode;
      if (!parent) return;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();   // merge the split text nodes back together
    });
  }
  _chatFindMarks = [];
}

function chatFindRun(query) {
  clearTimeout(_chatFindDebounce);
  _chatFindDebounce = setTimeout(() => _chatFindApply(query), 90);
}

function _chatFindApply(query) {
  _chatFindClearHighlights();
  _chatFindIdx = -1;
  const q = (query || '').trim();
  const win = document.getElementById('chat-window');
  if (!win || q.length < 1) { _updateChatFindCount(); return; }

  const needle = q.toLowerCase();
  // Only search inside the rendered message text, never sender/timestamp/buttons.
  const textBlocks = win.querySelectorAll('.chat-message .text');
  textBlocks.forEach(block => {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, null);
    const targets = [];
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeValue && node.nodeValue.toLowerCase().includes(needle)) {
        targets.push(node);
      }
    }
    targets.forEach(textNode => _chatFindHighlightNode(textNode, needle));
  });

  _chatFindMarks = Array.from(win.querySelectorAll('mark.chat-find-hl'));
  if (_chatFindMarks.length) chatFindStep(1);
  _updateChatFindCount();
}

// Split one text node into [before][mark][before][mark]… for each match.
function _chatFindHighlightNode(textNode, needle) {
  const text = textNode.nodeValue;
  const lower = text.toLowerCase();
  const frag = document.createDocumentFragment();
  let pos = 0, hit;
  while ((hit = lower.indexOf(needle, pos)) !== -1) {
    if (hit > pos) frag.appendChild(document.createTextNode(text.slice(pos, hit)));
    const mark = document.createElement('mark');
    mark.className = 'chat-find-hl';
    mark.textContent = text.slice(hit, hit + needle.length);
    frag.appendChild(mark);
    pos = hit + needle.length;
  }
  if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
  textNode.parentNode.replaceChild(frag, textNode);
}

function chatFindStep(dir) {
  if (!_chatFindMarks.length) return;
  if (_chatFindIdx >= 0 && _chatFindMarks[_chatFindIdx]) {
    _chatFindMarks[_chatFindIdx].classList.remove('active');
  }
  _chatFindIdx = (_chatFindIdx + dir + _chatFindMarks.length) % _chatFindMarks.length;
  const cur = _chatFindMarks[_chatFindIdx];
  cur.classList.add('active');
  cur.scrollIntoView({ block: 'center', behavior: 'smooth' });
  _updateChatFindCount();
}

function _updateChatFindCount() {
  const el = document.getElementById('chat-find-count');
  if (!el) return;
  const total = _chatFindMarks.length;
  el.textContent = total ? `${_chatFindIdx + 1}/${total}` : '0/0';
  el.classList.toggle('no-match', !total && !!document.getElementById('chat-find-input')?.value.trim());
}

// Ctrl/Cmd+F opens the conversation find bar while the Bridge (chat) panel is
// active, replacing the browser's native page find with an in-app search that
// understands the message list. Ignored elsewhere so other panels keep the
// native find.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'f' || !(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
  const chatPanel = document.getElementById('panel-chat');
  if (!chatPanel || !chatPanel.classList.contains('active')) return;
  e.preventDefault();
  toggleChatFind(true);
});

// ── Potential Upgrades (AI tool discovery) ───────────────
let _briefingRefreshing = false;
let _lastBriefingBadge = 0;

function _briefingTypeLabel(t) {
  return {
    'mcp-server':   'MCP SERVER',
    'claude-skill': 'CLAUDE SKILL',
    'hermes-skill': 'HERMES SKILL',
    'pip-package':  'PYTHON PKG',
    'link':         'LINK',
  }[t] || (t || 'ITEM').toUpperCase();
}

function _briefingTypeColor(t) {
  return {
    'mcp-server':   'teal',
    'claude-skill': 'purple',
    'hermes-skill': 'blue',
    'pip-package':  'yellow',
    'link':         'orange',
  }[t] || 'orange';
}

async function loadBriefing() {
  const listEl = document.getElementById('briefing-list');
  const tsEl   = document.getElementById('briefing-timestamp');
  try {
    const res = await fetch(`${API_BASE}/briefing`);
    const data = await res.json();
    _renderBriefing(data);
    if (data.generated_at) {
      const d = new Date(data.generated_at);
      tsEl.textContent = d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } else {
      tsEl.textContent = 'NEVER';
    }
  } catch (e) {
    listEl.innerHTML = '<div class="briefing-empty">Bridge offline — cannot load briefing.</div>';
  }
}

function _renderBriefing(data) {
  const listEl = document.getElementById('briefing-list');
  const items = (data.items || []).filter(it => it.status !== 'dismissed');
  if (!items.length) {
    listEl.innerHTML = data.generated_at
      ? '<div class="briefing-empty">All caught up — no new items. Click REFRESH to scan again.</div>'
      : '<div class="briefing-empty">No briefing yet. Click REFRESH to generate one.</div>';
    _updateBriefingBadge(0);
    return;
  }
  const newCount = items.filter(it => it.status === 'new').length;
  _updateBriefingBadge(newCount);

  listEl.innerHTML = items.map(it => {
    const typeColor = _briefingTypeColor(it.install_type);
    const statusBadge = it.status === 'installed' ? '<span class="briefing-status installed">✓ INSTALLED</span>' : '';
    const safeTitle   = (it.title || 'Untitled').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]);
    const safeSummary = (it.summary || '').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]);
    const safeWhy     = (it.why_relevant || '').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]);
    const safeSource  = (it.source || '').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]);
    const url         = it.url || '#';
    return `
      <div class="briefing-card ${it.status}" data-id="${it.id}">
        <div class="briefing-card-header">
          <span class="pill ${typeColor}">${_briefingTypeLabel(it.install_type)}</span>
          <span class="briefing-source">${safeSource}</span>
          ${statusBadge}
        </div>
        <div class="briefing-card-title">${safeTitle}</div>
        <div class="briefing-card-summary">${safeSummary}</div>
        <div class="briefing-card-why"><span class="briefing-why-label">RELEVANCE:</span> ${safeWhy}</div>
        <div class="briefing-card-actions">
          <a href="${url}" target="_blank" rel="noopener" class="lcars-btn-sm teal">↗ OPEN</a>
          <button class="lcars-btn-sm orange" onclick="installBriefingItem('${it.id}')">INSTALL</button>
          <button class="lcars-btn-sm" onclick="dismissBriefingItem('${it.id}')">DISMISS</button>
        </div>
      </div>
    `;
  }).join('');
}

function _updateBriefingBadge(n) {
  const badge = document.getElementById('briefing-badge');
  if (!badge) return;
  if (n > _lastBriefingBadge) playDataSound('doorbell');
  _lastBriefingBadge = n;
  if (n > 0) {
    badge.textContent = n;
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

async function refreshBriefing() {
  if (_briefingRefreshing) return;
  _briefingRefreshing = true;
  const btn = document.getElementById('briefing-refresh-btn');
  if (btn) { btn.textContent = 'SCANNING…'; btn.disabled = true; }
  try {
    await fetch(`${API_BASE}/briefing/refresh`, { method: 'POST' });
    addLog('Daily briefing refresh triggered');
    // Poll the file for ~60s
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 2000));
      const res = await fetch(`${API_BASE}/briefing`);
      const data = await res.json();
      const ts = data.generated_at ? new Date(data.generated_at).getTime() : 0;
      if (ts > Date.now() - 90 * 1000) {
        _renderBriefing(data);
        const d = new Date(data.generated_at);
        document.getElementById('briefing-timestamp').textContent =
          d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        addLog(`Briefing updated — ${(data.items || []).length} items`);
        break;
      }
    }
  } catch (e) {
    addLog(`Briefing refresh failed: ${e.message || e}`);
  } finally {
    _briefingRefreshing = false;
    if (btn) { btn.textContent = 'REFRESH'; btn.disabled = false; }
  }
}

async function dismissBriefingItem(id) {
  try {
    await fetch(`${API_BASE}/briefing/dismiss`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    loadBriefing();
  } catch (e) {
    addLog(`Dismiss failed: ${e.message || e}`);
  }
}

async function installBriefingItem(id) {
  const card = document.querySelector(`.briefing-card[data-id="${id}"]`);
  if (!card) return;
  const title   = card.querySelector('.briefing-card-title')?.textContent || '';
  const summary = card.querySelector('.briefing-card-summary')?.textContent || '';
  const url     = card.querySelector('a')?.href || '';
  const type    = card.querySelector('.pill')?.textContent || '';

  // Mark installed in storage
  await fetch(`${API_BASE}/briefing/install`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });

  // Hand the actual install work to Data — he uses his tools to set it up
  showPanel('chat');
  const inputEl = document.getElementById('chat-input');
  inputEl.value = `Install "${title}" for me. Type: ${type}. URL: ${url}. ${summary}\n\nFigure out the right way to add it to my system (MCP server config, Hermes skill folder, pip install, etc.) and confirm when it is operational.`;
  setTimeout(sendMessage, 100);
  loadBriefing();
}

// ── Init ──────────────────────────────────────────────────
addLog('Dashboard initialized');
addLog('DATA interface online');
fetchMode();
fetchVitals();
setInterval(fetchVitals, 30000);
subscribeShipsHealth();   // /vitals_fast SSE → engine gauge + Hull/Shield/SIF/Damp bars + alert dot
// Quietly load the upgrades briefing on startup so the badge is correct
loadBriefing();
// Populate provider dropdown
loadProviders();


// ════════════════════════════════════════════════════════════════════════
// SETTINGS PANEL — themes, voice, default LLM, crew personalities, memory,
// conversation tuning. Opened from the footer Settings button (replaced the
// old theme-cycle cap). Ported from LCARS and adapted to retail: 2 themes
// (MINIMAL/CYBER via the inline toggleTheme system), Kokoro-only voice (crew
// selector, no F5/XTTS/tier rows). Reuses existing globals: _themeLabel,
// setProvider/loadProviders, setCrewVoice/syncCrewVoiceToggle,
// toggleWakeListener/WAKE, CONVO.
// ════════════════════════════════════════════════════════════════════════
let _settingsAgents = [];        // [{id,name,path,content}] from /agents
let _settingsActiveAgent = null;
let _sttState = {};              // {config, models, compute_types} from /voice/stt
let _sttPollTimer = null;        // polls while a model is downloading

function openSettings() {
  const ov = document.getElementById('settings-overlay');
  if (!ov) return;
  ov.classList.remove('hidden');
  document.addEventListener('keydown', _settingsKey);
  settingsTab('appearance');
  _settingsPaintTheme();
  _settingsLoadVoice();
  _settingsLoadModel();
  _settingsLoadCrew();
  _settingsLoadMemory();
  _settingsLoadConvo();
}
function closeSettings() {
  const ov = document.getElementById('settings-overlay');
  if (ov) ov.classList.add('hidden');
  document.removeEventListener('keydown', _settingsKey);
  if (_sttPollTimer) { clearInterval(_sttPollTimer); _sttPollTimer = null; }
}
function _settingsBackdrop(evt) { if (evt.target.id === 'settings-overlay') closeSettings(); }
function _settingsKey(e) { if (e.key === 'Escape') { e.stopPropagation(); closeSettings(); } }

function settingsTab(name) {
  document.querySelectorAll('.settings-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.settings-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.pane === name));
}

// ── Appearance — retail 2-theme system (MINIMAL default / CYBER) ────────────
function _settingsCurrentTheme() {
  return document.body.classList.contains('theme-cyberpunk') ? 'cyber' : 'minimal';
}
function _settingsPaintTheme() {
  const cur = _settingsCurrentTheme();
  document.querySelectorAll('#settings-theme-row .settings-opt').forEach(b =>
    b.classList.toggle('active', b.dataset.theme === cur));
}
function settingsSetTheme(t) {
  const toCyber = (t === 'cyber');
  document.body.classList.toggle('theme-cyberpunk', toCyber);
  document.body.classList.toggle('theme-minimal', !toCyber);
  try { localStorage.setItem('data-theme', toCyber ? 'cyber' : 'minimal'); } catch (e) {}
  if (typeof _themeLabel === 'function') _themeLabel();
  if (typeof addLog === 'function') addLog('Theme → ' + (toCyber ? 'CYBER' : 'MINIMAL'));
  _settingsPaintTheme();
}

// ── Voice — retail ships a single engine (Kokoro); only the crew voice (which
//    officer answers) is user-selectable. F5/XTTS + model-tier rows are
//    intentionally omitted (they do not exist in retail). ─────────────────────
async function _settingsLoadVoice() {
  if (!CREW_VOICES_LIST.length) { try { await syncCrewVoiceToggle(); } catch (e) {} }
  _settingsPaintVoiceRow();
  _settingsLoadStt();
}
function _settingsPaintVoiceRow() {
  const row = document.getElementById('settings-voice-row');
  if (!row) return;
  if (!CREW_VOICES_LIST.length) { row.innerHTML = '<span class="settings-sub">No crew voices available.</span>'; return; }
  row.innerHTML = (CREW_VOICES_LIST || []).map(v =>
    `<button class="settings-opt${v.id === CREW_VOICE ? ' active' : ''}" data-voice="${v.id}" onclick="settingsSetCrewVoice('${v.id}')">${(v.name || v.id).toUpperCase()}</button>`).join('');
}
function settingsSetCrewVoice(id) { if (typeof setCrewVoice === 'function') setCrewVoice(id); _settingsPaintVoiceRow(); }

// ── STT (speech-to-text) tuning — model / beam size / compute precision ──────
// The retail Whisper engine. Lets the Captain trade accuracy for speed on a
// GPU-less machine and download new models on demand.
async function _settingsLoadStt() {
  try {
    const d = await (await fetch(`${API_BASE}/voice/stt`)).json();
    if (d && !d.error) { _sttState = d; _settingsPaintStt(); }
  } catch (e) { /* voice engine may be absent — silently skip */ }
}
function _settingsPaintStt() {
  const d = _sttState; if (!d || !d.config) return;
  // model row — each model is a button; ⤓ = not installed, ⏳ = downloading
  const mrow = document.getElementById('settings-stt-model-row');
  if (mrow) {
    mrow.innerHTML = (d.models || []).map(m => {
      const active = m.id === d.config.model;
      const dl = m.status === 'downloading';
      const err = m.status === 'error';
      const tag = dl ? ' ⏳' : (err ? ' ⚠' : (m.installed ? '' : ' ⤓'));
      const state = dl ? 'downloading…' : (err ? ('error: ' + (m.error || 'failed')) : (m.installed ? 'installed' : 'click to install'));
      const title = `${m.note} · ~${m.size_mb} MB · ${state}`;
      return `<button class="settings-opt${active ? ' active' : ''}${dl ? ' busy' : ''}" title="${title}" onclick="settingsSetSttModel('${m.id}')">${(m.label || m.id).toUpperCase()}${tag}</button>`;
    }).join('');
  }
  // beam slider
  const beam = document.getElementById('settings-stt-beam');
  const beamv = document.getElementById('settings-stt-beam-val');
  if (beam) beam.value = d.config.beam_size;
  if (beamv) beamv.textContent = `${d.config.beam_size} ${d.config.beam_size === 1 ? '(fastest, default)' : '(slower, more accurate)'}`;
  // compute precision row
  const crow = document.getElementById('settings-stt-compute-row');
  if (crow) {
    crow.innerHTML = (d.compute_types || []).map(c =>
      `<button class="settings-opt${c === d.config.compute_type ? ' active' : ''}" onclick="settingsSetCompute('${c}')">${c.toUpperCase()}</button>`).join('');
  }
  // poll while anything is downloading; stop when idle
  const anyDl = (d.models || []).some(m => m.status === 'downloading');
  if (anyDl && !_sttPollTimer) {
    _sttPollTimer = setInterval(_settingsLoadStt, 2000);
  } else if (!anyDl && _sttPollTimer) {
    clearInterval(_sttPollTimer); _sttPollTimer = null;
  }
}
async function _sttPost(body) {
  try {
    const d = await (await fetch(`${API_BASE}/voice/stt`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
    if (d.error) { addLog(`STT setting failed: ${d.error}`); return null; }
    if (d.config) { _sttState = Object.assign({}, _sttState, { config: d.config, models: d.models || _sttState.models }); _settingsPaintStt(); }
    return d;
  } catch (e) { addLog(`STT setting error: ${e.message || e}`); return null; }
}
async function settingsSetSttModel(id) {
  const m = (_sttState.models || []).find(x => x.id === id);
  await _sttPost({ model: id });
  if (m && !m.installed && m.status !== 'downloading') {
    try {
      await fetch(`${API_BASE}/voice/stt/install`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: id }) });
      addLog(`Downloading STT model ${id} (~${m.size_mb} MB)…`);
    } catch (e) { addLog(`STT install error: ${e.message || e}`); }
    _settingsLoadStt();   // refresh → shows ⏳ and starts the poll
  } else {
    addLog(`STT model → ${id}`);
  }
}
function settingsSetBeam(v) {
  const n = parseInt(v);
  const beamv = document.getElementById('settings-stt-beam-val');
  if (beamv) beamv.textContent = `${n} ${n === 1 ? '(fastest, default)' : '(slower, more accurate)'}`;
  _sttPost({ beam_size: n, best_of: n });
}
function settingsSetCompute(c) { _sttPost({ compute_type: c }); }

// ── Default LLM ─────────────────────────────────────────────
async function _settingsLoadModel() {
  try {
    const d = await (await fetch(`${API_BASE}/providers`)).json();
    const row = document.getElementById('settings-model-row');
    if (!row) return;
    row.innerHTML = (d.providers || []).map(p =>
      `<button class="settings-opt${p.id === d.active ? ' active' : ''}" ${p.available ? '' : 'disabled'} data-prov="${p.id}" onclick="settingsSetModel('${p.id}')" title="${(p.model || '')}${p.available ? '' : ' — not installed'}">${(p.label || p.id).toUpperCase()}</button>`).join('');
  } catch (e) { addLog(`Providers load failed: ${e.message || e}`); }
}
async function settingsSetModel(id) {
  if (typeof setProvider === 'function') { await setProvider(id); }
  _settingsLoadModel();
}

// ── Crew personalities ──────────────────────────────────────
async function _settingsLoadCrew() {
  try {
    const d = await (await fetch(`${API_BASE}/agents`)).json();
    _settingsAgents = d.agents || [];
    const pick = document.getElementById('settings-crew-pick');
    if (pick) {
      pick.innerHTML = _settingsAgents.length
        ? _settingsAgents.map(a =>
            `<button class="settings-opt${a.id === _settingsActiveAgent ? ' active' : ''}" data-agent="${a.id}" onclick="settingsPickAgent('${a.id}')">${(a.name || a.id).toUpperCase()}</button>`).join('')
        : '<span class="settings-sub">No officer personality files found in ~/.claude/agents.</span>';
    }
    if (_settingsActiveAgent && _settingsAgents.some(a => a.id === _settingsActiveAgent)) settingsPickAgent(_settingsActiveAgent);
  } catch (e) { addLog(`Crew personalities load failed: ${e.message || e}`); }
}
function settingsPickAgent(id) {
  _settingsActiveAgent = id;
  const a = _settingsAgents.find(x => x.id === id);
  const ed = document.getElementById('settings-crew-editor');
  const pa = document.getElementById('settings-crew-path');
  if (a && ed) ed.value = a.content || '';
  if (a && pa) pa.textContent = a.path || '';
  document.querySelectorAll('#settings-crew-pick .settings-opt').forEach(b =>
    b.classList.toggle('active', b.dataset.agent === id));
}
async function settingsSaveAgent() {
  if (!_settingsActiveAgent) { addLog('Pick an officer first'); return; }
  const ed = document.getElementById('settings-crew-editor');
  const btn = document.getElementById('settings-crew-save');
  try {
    const d = await (await fetch(`${API_BASE}/agents`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: _settingsActiveAgent, content: ed.value }) })).json();
    if (d.error) { addLog(`Save failed: ${d.error}`); return; }
    const a = _settingsAgents.find(x => x.id === _settingsActiveAgent); if (a) a.content = ed.value;
    addLog(`Personality saved → ${_settingsActiveAgent} (${d.bytes} bytes)`);
    _flashSaved(btn);
  } catch (e) { addLog(`Save error: ${e.message || e}`); }
}

// ── Memory ──────────────────────────────────────────────────
async function _settingsLoadMemory() {
  try {
    const d = await (await fetch(`${API_BASE}/crew-memory`)).json();
    const ed = document.getElementById('settings-memory-editor');
    const pa = document.getElementById('settings-memory-path');
    if (ed) ed.value = d.content || '';
    if (pa) pa.textContent = d.path || '';
  } catch (e) { addLog(`Memory load failed: ${e.message || e}`); }
}
async function settingsSaveMemory() {
  const ed = document.getElementById('settings-memory-editor');
  const btn = document.getElementById('settings-memory-save');
  try {
    const d = await (await fetch(`${API_BASE}/crew-memory`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: ed.value }) })).json();
    if (d.error) { addLog(`Memory save failed: ${d.error}`); return; }
    addLog(`Memory saved (${d.bytes} bytes)`);
    _flashSaved(btn);
  } catch (e) { addLog(`Memory save error: ${e.message || e}`); }
}

// ── Conversation history (Memory Banks) ─────────────────────
function _settingsHistEscape(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function _settingsHistTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch (e) { return ''; }
}
function _settingsHistPane(p) {
  if (!p || p === '(main)') return 'MAIN';
  // pane looks like "C:\path\to\project::sessionid" — show the folder leaf.
  const folder = String(p).split('::', 1)[0];
  const leaf = folder.split(/[\\/]/).filter(Boolean).pop();
  return (leaf || folder || 'MAIN').toUpperCase();
}
function _settingsRenderTurns(turns) {
  const list = document.getElementById('settings-history-list');
  if (!list) return;
  if (!turns || !turns.length) {
    list.innerHTML = '<div class="settings-history-empty">No conversation turns found.</div>';
    return;
  }
  list.innerHTML = turns.map(t => {
    const role = (t.role === 'user') ? 'CAPTAIN' : 'DATA';
    const roleCls = (t.role === 'user') ? 'user' : 'data';
    const body = _settingsHistEscape(t.content);
    return `<div class="settings-history-item ${roleCls}">
      <div class="settings-history-meta">
        <span class="settings-history-role">${role}</span>
        <span class="settings-history-pane">${_settingsHistEscape(_settingsHistPane(t.pane))}</span>
        <span class="settings-history-ts">${_settingsHistEscape(_settingsHistTime(t.ts))}</span>
      </div>
      <div class="settings-history-text">${body}</div>
    </div>`;
  }).join('');
}
async function _settingsLoadConvo() {
  const list = document.getElementById('settings-history-list');
  if (list) list.innerHTML = '<div class="settings-history-empty">Loading recent turns…</div>';
  const q = document.getElementById('settings-history-q');
  if (q) q.value = '';
  try {
    const d = await (await fetch(`${API_BASE}/history?limit=60`)).json();
    if (d.error) { if (list) list.innerHTML = `<div class="settings-history-empty">Error: ${_settingsHistEscape(d.error)}</div>`; return; }
    _settingsRenderTurns(d.turns || []);
  } catch (e) {
    if (list) list.innerHTML = `<div class="settings-history-empty">Failed to load history: ${_settingsHistEscape(e.message || e)}</div>`;
  }
}
async function settingsSearchHistory() {
  const q = document.getElementById('settings-history-q');
  const list = document.getElementById('settings-history-list');
  const query = (q && q.value || '').trim();
  if (!query) { _settingsLoadConvo(); return; }
  if (list) list.innerHTML = '<div class="settings-history-empty">Searching the Memory Banks…</div>';
  try {
    const url = `${API_BASE}/search-history?query=${encodeURIComponent(query)}&k=20&scope=all&format=text`;
    const txt = await (await fetch(url)).text();
    if (list) list.innerHTML = `<pre class="settings-history-results">${_settingsHistEscape(txt)}</pre>`;
  } catch (e) {
    if (list) list.innerHTML = `<div class="settings-history-empty">Search failed: ${_settingsHistEscape(e.message || e)}</div>`;
  }
}

function _flashSaved(btn) {
  if (!btn) return;
  const old = btn.textContent;
  btn.textContent = '✓ SAVED';
  btn.classList.add('saved');
  setTimeout(() => { btn.textContent = old; btn.classList.remove('saved'); }, 1400);
}

// Apply persisted conversation-tuning overrides at startup (no-op unless the
// Captain has moved the sliders before — so zero impact on defaults).
(function _settingsApplyConvoOverrides() {
  try {
    const m = parseFloat(localStorage.getItem('convo-mic-threshold'));
    const s = parseInt(localStorage.getItem('convo-silence-hold'));
    if (typeof CONVO !== 'undefined') {
      if (!isNaN(m)) { CONVO.threshold = m; CONVO.calibrated = true; }
      if (!isNaN(s)) CONVO.SILENCE_HOLD_MS = s;
    }
  } catch (e) {}
})();
