# Implementation Spec — Connections Hub (widgets page)
Stardate 2026.07.06 · Status: **CLEARED TO BUILD** (naming confirmed 2026.07.07)

> **Name confirmed (Captain, 2026.07.07): the page is titled "CONNECTIONS."**
> Visible caption/nav button = **CONNECTIONS**; internal panel id stays
> **`widgets`** so it does not collide with the existing green **Connectors**
> (model/hardware) hub. "CONNECTIONS" ≠ "Connectors" — distinct word, distinct
> id, no collision. Everything else in this spec is build-ready.

---

## Ground-truth findings (verified in-file, not assumed)

Three facts reshape the brief. They are surfaced first, plainly.

**1. The word "CONNECTORS" is already taken in this (retail) build.**
`dashboard/app.js` already routes `showPanel('connectors')`, already has a green Connectors nav button (nav index 4), and already defines `loadConnectors()`. That existing page is **not** a widgets hub — it is the **AI model / hardware / provider hub**: it calls `GET /llm/catalog`, renders detected CPU/GPU/RAM, model recommendations, local models, and provider connectors. Reusing "connectors" for the new widgets feature would collide head-on with a shipping page.

**Decision taken in this spec:** the widgets hub is named **WIDGETS** (panel id `widgets`), not "connectors." This gives one consistent panel id across the near-identical full and retail codebases and avoids the collision. If a "Connectors" caption is desired, it must differ from the existing green Connectors button in this build. See Open Questions §0.

**2. Button CSS class differs between the two builds.** The retail (this) build uses `class="data-btn …"` and `data-btn-sm`. The full/companion build uses `lcars-btn`. Every nav snippet below is given for the retail (`data-btn`) form. `showPanel()` internals also differ per build: `btnMap` and the `.querySelectorAll('.data-btn')` selector must match this build's class.

**3. The multi-inbox MAIL backend exists in the bridge — but the module is not shipped here.**
- Bridge HTTP endpoints already exist in this `bridge_server.py`: `GET /mail/accounts` → `{accounts:[…]}` (passwords stripped) and `POST /mail/accounts` → `{action:"add"|"remove", account:{…} | label:"…"}`. Agent tools `mail_inboxes`, `mail_unread`, `mail_search`, `mail_read`, `mail_draft`, `mail_send` are also wired.
- **Retail gap flagged:** the bridge does `import mail`, but **`dashboard/` contains no `mail.py`.** Today `_get_mail()` returns `None` and every mail call answers "mail module unavailable." The Email Inboxes widget is dead until `mail.py` is shipped into this folder. Tracked as **Task 0**.
- The mail module is pure Python stdlib (IMAP/SMTP), provider-neutral, and contains nothing product-specific — it can be shipped as-is. Its account config schema (below) is the field map for the connect flow.

**Mail account schema** (fields the connect flow must collect / default):
```
label            required, unique key
address          display address (defaults to imap_user)
imap_host        required (e.g. imap.gmail.com)
imap_port        default 993
imap_user        required (usually = address)
password         required (app-specific password, NOT the login password)
smtp_host        default = imap_host with imap→smtp
smtp_port        default 465
smtp_user        default = imap_user
smtp_password    optional (falls back to password)
send_as[]        optional [{name,address}] identities
default_send_as  optional (defaults to address)
```
Config persists to a local JSON store managed by the mail module; passwords are stripped from `GET /mail/accounts` responses. **Microsoft 365 / Outlook.com is unsupported** (requires OAuth XOAUTH2) — the connect flow must say so, not silently fail.

---

## Spec: Widgets Hub

**Objective.** A single dashboard page — **WIDGETS** — presenting a grid of widget cards. Each card, when opened, slides in a side panel hosting that widget's own menu. The page is *data-driven*: widgets come from a registry array, not hand-written markup, so adding a widget means appending one registry entry plus one open handler — never editing the grid markup. The first widget is **Email Inboxes**: view connected inboxes with status, add a new inbox through a guided connect flow, and manage/remove existing ones — backed by the mail endpoints already present in the bridge.

**Who the user is.** The dashboard owner (any end user). Non-technical-friendly: the connect flow must explain what an app password is and where to get one.

**What success looks like.**
- A WIDGETS nav button opens a panel showing a responsive grid of widget cards (icon, title, status pill: Connected / Available / Needs setup).
- Clicking any card opens a reusable right-side slide-in panel with that widget's UI. Backdrop click, ✕, and Esc all close it.
- Email Inboxes lists inboxes from `GET /mail/accounts`, adds via `POST /mail/accounts {action:"add"}`, removes via `POST /mail/accounts {action:"remove"}`, refreshing on success.
- Adding a second widget requires no change to the grid renderer or slide-in mechanic — only a new registry entry + handler.

**Tech Stack.** Vanilla JS (no framework/build step — static `index.html` + `app.js` + `theme.css` served by the Python `bridge_server.py`). Themed via existing CSS variables and `.data-*` classes. Python 3 stdlib bridge for mail.

**Commands.**
- Run bridge/dev: `python bridge_server.py` from the dashboard dir (serves the static files + JSON API on `127.0.0.1`).
- No frontend build/bundler/test-runner is configured. "Test" = load in a browser and exercise the panel. "Lint" = none; match existing style.
- Mail smoke test: `curl http://127.0.0.1:<port>/mail/accounts`.

**Project Structure (relevant files).**
```
dashboard/
  index.html        # nav buttons + <div class="panel"> per page; add WIDGETS button + panel + slide-in host
  app.js            # showPanel(), page logic; add widget registry + renderers + slide-in + email widget
  theme.css         # styles; add .widget-card, .slidein-* rules
  mail.py           # TO SHIP — multi-inbox IMAP/SMTP module (currently missing here)
  bridge_server.py  # EXISTING /mail/accounts endpoints (unchanged for v1)
  docs/SPEC_connectors_page.md   # this file
```

**Testing Strategy.** No automated UI harness exists; testing is manual + endpoint smoke tests.
- **Manual UI checks (acceptance path):** open WIDGETS → grid renders from registry; open Email Inboxes → list populates or shows empty state; add inbox → appears; remove inbox → disappears; slide-in opens/closes via card, ✕, backdrop, Esc.
- **Endpoint smoke tests:** `GET`/`POST /mail/accounts` (add a throwaway label, list it, remove it).
- **Regression guard:** confirm the existing green Connectors (model/hardware) page still opens and behaves — WIDGETS must not touch it.

**Boundaries.**
- *Always:* keep the widget system data-driven (registry + handler); reuse the single slide-in host for every widget; strip passwords from anything rendered client-side; keep this build's class names correct (`data-btn`).
- *Ask first:* adding any new bridge endpoint or Python dependency; changing the mail account schema; renaming the existing Connectors page; provider auto-detection of hosts (nice-to-have, not v1).
- *Never:* store or echo mail passwords in the DOM, logs, or the registry; break or rename the existing Connectors/model hub.

**Success Criteria (testable).**
1. Registry lives client-side; no new list endpoint needed for v1.
2. WIDGETS panel shows ≥1 card (Email Inboxes) rendered from the registry array.
3. Slide-in opens on card click, is dismissable by Esc, backdrop, and ✕; only one open at a time.
4. Email Inboxes: list reflects `GET /mail/accounts`; add persists (survives reload); remove persists.
5. A dummy "second widget" entry renders a card and opens its handler with **zero** edits to the grid renderer or slide-in code.
6. The existing green Connectors (model/hardware) page is unchanged.

---

## The WIDGETS page — design

### Registry model (data-driven)
A module-level array in `app.js`. Each widget is one object:
```js
const WIDGETS = [
  {
    id: 'email-inboxes',
    title: 'Email Inboxes',
    icon: '✉',
    status: 'available',            // resolved at render (see below)
    desc: 'Connect and manage your email accounts.',
    open: openEmailInboxesWidget,   // handler → fills the slide-in body
    // optional: statusProbe() async → 'connected'|'available'|'setup'
  },
  // future widgets append here — nothing else changes
];
```
- `status` resolves at render time. Email Inboxes: `connected` if `GET /mail/accounts` returns ≥1 account, else `available`. No probe → defaults to `available`.
- `renderWidgetsGrid()` maps `WIDGETS` → cards; never hard-codes a widget.

### Grid markup (host only; cards generated)
```html
<div class="panel" id="panel-widgets">
  <div class="panel-head"><h2>WIDGETS</h2>
    <button class="data-btn-sm yellow" onclick="renderWidgetsGrid(true)">↻ REFRESH</button>
    <button class="data-btn-sm orange" onclick="openAddWidgetPicker()">+ ADD WIDGET</button>
  </div>
  <div id="widgets-grid" class="widgets-grid"><!-- cards injected --></div>
</div>
```
Card template (built in JS):
```html
<button class="widget-card" data-widget-id="email-inboxes" onclick="openWidget('email-inboxes')">
  <span class="widget-card-icon">✉</span>
  <span class="widget-card-title">Email Inboxes</span>
  <span class="widget-card-status status-connected">Connected</span>
</button>
```

### "Add widget" affordance
For v1 the catalogue is the registry itself. **+ ADD WIDGET** opens the slide-in in "picker" mode, listing registry widgets not yet connected so the user can start their connect flow. It does not let users author arbitrary widgets in v1 (that is a later "custom button" feature — Open Questions §3).

### Slide-in side panel mechanic (reusable by any widget)
One host element, one open/close API, used by every widget.

**Markup (index.html, once, near end of body):**
```html
<div id="slidein-backdrop" class="slidein-backdrop" onclick="closeSlideIn()"></div>
<aside id="slidein" class="slidein" role="dialog" aria-modal="true" aria-hidden="true">
  <div class="slidein-head">
    <h3 id="slidein-title">Widget</h3>
    <button class="slidein-close" onclick="closeSlideIn()" aria-label="Close">✕</button>
  </div>
  <div id="slidein-body" class="slidein-body"><!-- widget fills this --></div>
</aside>
```

**CSS (theme.css) — right-edge slide, backdrop, motion:**
```css
.slidein {
  position: fixed; top: 0; right: 0; height: 100vh;
  width: min(460px, 92vw);
  transform: translateX(100%);
  transition: transform .28s ease;
  z-index: 1000;                 /* use existing panel bg / border-glow variables */
}
.slidein.open { transform: translateX(0); }
.slidein-backdrop {
  position: fixed; inset: 0; background: rgba(0,0,0,.55);
  opacity: 0; pointer-events: none; transition: opacity .28s ease; z-index: 999;
}
.slidein-backdrop.open { opacity: 1; pointer-events: auto; }
.slidein-head { display:flex; justify-content:space-between; align-items:center; }
.slidein-body { overflow-y:auto; height: calc(100vh - <head height>); }
@media (prefers-reduced-motion: reduce){ .slidein,.slidein-backdrop{ transition:none; } }
```

**JS (app.js) — open/close API any widget calls:**
```js
function openSlideIn(title, bodyHtml) {
  document.getElementById('slidein-title').textContent = title;
  document.getElementById('slidein-body').innerHTML = bodyHtml;
  document.getElementById('slidein').classList.add('open');
  document.getElementById('slidein-backdrop').classList.add('open');
  document.getElementById('slidein').setAttribute('aria-hidden','false');
}
function closeSlideIn() {
  document.getElementById('slidein').classList.remove('open');
  document.getElementById('slidein-backdrop').classList.remove('open');
  document.getElementById('slidein').setAttribute('aria-hidden','true');
}
function openWidget(id) {
  const w = WIDGETS.find(x => x.id === id);
  if (!w) return;
  playDataSound && playDataSound('confirm');   // match existing sound calls
  openSlideIn(w.title, '<div class="slidein-loading">…</div>');
  w.open();                                     // handler fills #slidein-body
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSlideIn(); });
```
Only one slide-in exists, so "one open at a time" is free. Widgets should scope their DOM ids (prefix with the widget id) to avoid collisions.

---

## Widget #1 — Email Inboxes (fully specified)

**What it does.** Lists every configured inbox with status, adds a new inbox via a guided connect flow, and removes an inbox.

**Open handler.** `openEmailInboxesWidget()` → `openSlideIn('Email Inboxes', …)` then `refreshInboxList()`.

**List (read).** `GET /mail/accounts` → `{accounts:[{label,address,imap_host,send_as,default_send_as}]}` (passwords stripped server-side). Render each as a row: label, address, host, green "Connected" pill, **Remove** button. Empty state: "No inboxes connected yet — Add one."

**Add (connect flow).** A form in the slide-in mapping 1:1 to the mail account schema:

| Field | Required | Maps to | Note for the user |
|---|---|---|---|
| Label | ✔ | `label` (unique key) | e.g. "personal" |
| Email address | ✔ | `imap_user` + `address` | full address; used as IMAP/SMTP username |
| App password | ✔ | `password` | *not* the login password — an app-specific password. Show a "How do I get this?" hint. |
| IMAP host | ✔ | `imap_host` | e.g. `imap.gmail.com`. Offer a preset dropdown (Gmail / iCloud / Fastmail / Custom) that pre-fills host + ports. |
| IMAP port | – | `imap_port` (default 993) | prefilled |
| SMTP host | – | `smtp_host` (default = imap→smtp) | prefilled |
| SMTP port | – | `smtp_port` (default 465) | prefilled |
| Display name | – | `send_as[].name` | optional |

On submit: `POST /mail/accounts {"action":"add","account":{…}}`. On `{ok:true}` → success toast/log, clear form, `refreshInboxList()`. On `{error}` → show inline (do **not** clear the app-password field). Outlook/365 preset entry must state it is unsupported.

**Remove/manage.** Remove → confirm → `POST /mail/accounts {"action":"remove","label":"<label>"}` → on `{ok:true}` refresh. (Editing = remove + re-add in v1.)

**Security.** App passwords are write-only from the UI: they go up in the POST and are never returned by `GET /mail/accounts`. Never place a password into the registry, a data-attribute, the activity log, or a URL. Use `type="password"` inputs.

**Bridge work for v1:** none — the `/mail/accounts` endpoints already exist. But **`mail.py` must be shipped into this folder first** (Task 0), or the endpoints answer "mail module unavailable."

---

## 3–5 additional widget ideas (same pattern)

Each is one registry entry + one `open*Widget()` handler filling the slide-in.

1. **Calendar Peek** — today/next-N events; "Connect calendar" if unauthorized. Menu: event list + refresh. API: existing calendar auth-status/list path. Status probe: `GET /calendar/auth_status`.
2. **Weather** — current + short forecast for a saved location; menu sets the location. API: existing weather fetch in the bridge. Minimal new work.
3. **Quick Links / Launcher** — user-defined buttons opening a path or URL. Menu: list + add/remove. API: existing `GET /open?path=…`. Persistence needs a small new store/endpoint — **flag as new work, ask first.**
4. **Standing Orders shortcut** — compact view/toggle inside the slide-in (full page already exists). API: existing standing-orders endpoints; no new work.
5. **System Vitals** — CPU/RAM/GPU at a glance. API: reuses `/hardware` (already used by the model hub). Read-only; no new work — and deliberately reuses that endpoint **without touching the Connectors page.**

Rule enforced by the pattern: a widget mapping to an *existing* endpoint is nearly free; one needing persistence (Quick Links) needs a new tiny endpoint — call it out and ask first.

---

## File-by-file change list (this / retail build)

### `index.html`
- Add WIDGETS **nav button** after the last page button:
  `<button class="data-btn <color>" onclick="showPanel('widgets')">WIDGETS</button>` — **choose a color/slot distinct from the existing green Connectors button (nav index 4).** WIDGETS becomes the new highest nav index.
- Add `<div class="panel" id="panel-widgets">…</div>` with the grid host + head buttons (markup above).
- Add the single slide-in host block (`#slidein`, `#slidein-backdrop`) once, near end of `<body>`.

### `app.js`
- Add `const WIDGETS = […]` registry.
- Add `renderWidgetsGrid()`, `openWidget()`, `openSlideIn()`, `closeSlideIn()`, `openAddWidgetPicker()`.
- Add Email Inboxes handlers: `openEmailInboxesWidget()`, `refreshInboxList()`, `addInboxSubmit()`, `removeInbox(label)`.
- Extend this build's `btnMap` with `widgets: <new index>` and lazy-init (`if (name === 'widgets') renderWidgetsGrid();`). Use the `.data-btn` selector — do not use `.lcars-btn` here.
- Add Esc-to-close listener.

### `theme.css`
- Add `.widgets-grid`, `.widget-card`, `.widget-card-*`, status-pill classes, and the `.slidein*` rules, using existing theme variables.

### `mail.py`
- **Ship `mail.py` into `dashboard/` (Task 0).** Pure stdlib, provider-neutral; can be shipped verbatim. Without it the endpoints return "mail module unavailable."

### `bridge_server.py`
- No change for v1 — `GET`/`POST /mail/accounts` already present. (Only later widget persistence like Quick Links would add endpoints.)

### Cross-build divergence (for the team maintaining both copies)
| Item | This (retail) build | Companion (full) build |
|---|---|---|
| Nav button class | `data-btn` / `data-btn-sm` | `lcars-btn` / `lcars-btn-sm` |
| `showPanel` selector | `.data-btn` | `.lcars-btn` |
| "connectors" name | **taken (model/hardware hub)** → use `widgets` | free |
| `mail.py` present | **no — must ship (Task 0)** | yes |

---

## Task Plan

### Task 0: Ship the mail module
- **Description:** Add `mail.py` to `dashboard/` so `_get_mail()` resolves.
- **Acceptance:** `curl /mail/accounts` on a running bridge returns `{accounts:[]}` (not "mail module unavailable").
- **Verification:** endpoint smoke test. **Dependencies:** None. **Scope:** Small.

### Task 1: Slide-in mechanic (shared)
- **Description:** Add reusable slide-in host + CSS + `openSlideIn/closeSlideIn` + Esc/backdrop dismiss.
- **Acceptance:** `openSlideIn('T','<p>hi</p>')` from console slides a panel in; ✕/backdrop/Esc close it.
- **Verification:** manual. **Dependencies:** None. **Scope:** Small–Medium.

### Task 2: WIDGETS page shell + registry + grid
- **Description:** Add nav button (`data-btn`), `#panel-widgets`, `WIDGETS` registry (one Email Inboxes entry), `renderWidgetsGrid()`/`openWidget()`; wire `showPanel()`.
- **Acceptance:** WIDGETS button opens a panel with one card; clicking it opens the slide-in.
- **Verification:** manual. **Dependencies:** 1. **Scope:** Medium.

### Checkpoint — after Tasks 1–2
- [ ] Slide-in opens/closes three ways.
- [ ] WIDGETS grid renders from registry; existing Connectors page still works.

### Task 3: Email Inboxes — list (read)
- **Description:** `openEmailInboxesWidget()` + `refreshInboxList()` → `GET /mail/accounts`; render rows + empty state.
- **Acceptance:** With ≥1 account, rows appear; with none, empty state shows.
- **Verification:** manual against a live bridge. **Dependencies:** 2, 0. **Scope:** Medium.

### Task 4: Email Inboxes — add (connect flow)
- **Description:** Add-inbox form + preset dropdown + `POST /mail/accounts {action:"add"}`; success refreshes; errors inline; Outlook/365 marked unsupported.
- **Acceptance:** A valid app-password account persists and appears after reload; a bad password shows the server error without clearing the field.
- **Verification:** manual add of a throwaway label. **Dependencies:** 3. **Scope:** Medium.

### Task 5: Email Inboxes — remove/manage
- **Description:** Remove → confirm → `POST …{action:"remove"}` → refresh.
- **Acceptance:** Removing a label deletes it from the list and the store.
- **Verification:** manual. **Dependencies:** 3. **Scope:** Small.

### Checkpoint — after Tasks 3–5
- [ ] Full add/list/remove round-trip works.
- [ ] No password appears in DOM/log/URL.

### Task 6: Second-widget proof
- **Description:** Add one more registry entry + handler (System Vitals via `/hardware`, or a dummy) with **no** edits to grid/slide-in code.
- **Acceptance:** New card renders and opens; shared code untouched.
- **Verification:** manual + diff review. **Dependencies:** 2. **Scope:** Small.

---

## Open Questions

- **§0 — RESOLVED (Captain, 2026.07.07):** visible caption/nav button = **CONNECTIONS**; internal panel id = **`widgets`** (unchanged). Distinct from the existing green "Connectors" model/hardware hub, so no collision. Every "WIDGETS" caption in the markup snippets above renders as **CONNECTIONS**; panel/registry/function ids stay `widgets`.
- **§1:** Auto-detect IMAP/SMTP hosts from the address domain, or is the preset dropdown enough for v1? (Recommend preset dropdown; auto-detect later.)
- **§2:** Is per-widget persistence (e.g. Quick Links) wanted in v1? If yes, that adds one small bridge endpoint (ask-first).
- **§3:** User-authored custom widgets — v1 or later? This spec treats it as later; v1's "+ ADD WIDGET" connects *known* registry widgets.

## Order of Battle
Start at the seams. **Task 0 first** — without `mail.py` the widget is a corpse; prove `GET /mail/accounts` answers before building UI on it. Then the **slide-in mechanic (Task 1)** — the reusable spine every widget depends on; get it right once. The **WIDGETS shell + registry (Task 2)** proves the data-driven claim. Risk concentrates in the **add-inbox connect flow (Task 4)** — the only place credentials move — so build it deliberately, test with a throwaway account, and confirm no password ever touches the DOM or log. **Task 6** is the proof of the thesis: a new widget with zero edits to shared code. If that lands clean, the design is sound.

---
The one blocking item is the name (§0). Confirm it, and this is ready to build.
