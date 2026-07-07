═══════════════════════════════════════════
MISSION ARCHITECTURE — CLI-first brain selection on a fresh DATA install
U.S.S. Enterprise-D — Stardate 2026.07.06
═══════════════════════════════════════════

READINESS: MAKE IT SO

Scope is strictly `C:\Users\mixma\Documents\DATA\`. The personal LCARS dashboard at
`C:\Users\mixma\Documents\LCARS\` is OUT OF SCOPE and must not be touched.

---

## ASSUMPTIONS
1. The retail bridge is `C:\Users\mixma\Documents\DATA\dashboard\bridge_server.py`
   (10,408 lines). There is NO `lcars-dashboard/` folder in the DATA repo; the
   original brief's path was wrong. Confirmed by inspection.
2. There is NO `launch_data.bat`. The Windows first-run launcher is
   `C:\Users\mixma\Documents\DATA\installer\launchers\start_data.bat`, which
   simply frees ports 7777/7766 and starts `supervisor.py` via the bundled
   `pythonw.exe`. It does no brain selection. First-run brain logic therefore
   belongs in the bridge's Python startup, NOT the .bat — the .bat needs no change.
3. Provider availability today (`_provider_available`, line 1111) means "the
   executable exists on disk" for `subprocess`/`http` kinds. It does NOT check
   whether a CLI is authenticated. This is the central gap.
4. "Active CLI — Claude Code, Opus preferred" maps to provider id `claude-cli`
   (label "Claude Opus 4.8 (Subscription)", model `claude-opus-4-8`, PROVIDERS
   line 961). "A CLI is present" means any of the subprocess CLI providers
   (`claude-cli`, `codex`, `gemini`) resolves to a real executable, with
   `claude-cli` taking priority.
5. Ollama fallback default is the built-in `ollama` provider (Qwen2.5-Coder 7B,
   line 1007), matching the existing auto-connect logic.
→ Correct me now, Captain, or the crew will build on these.

---

## Spec: CLI-first brain selection

**Objective:** On a fresh DATA install the active brain must DEFAULT to the
active CLI (Claude Code, Opus preferred) whenever a CLI is present AND
authenticated. If a CLI is present but not yet authenticated, the buyer is
prompted to authenticate and Ollama is kept as the active fallback until auth
succeeds — never a dead brain. If no CLI is present at all, Ollama (installed as
the fallback) becomes active. A choice the buyer has explicitly made is never
overridden.

**Who the user is:** A retail buyer running DATA for the first time on their own
Windows / macOS / Linux machine, with any combination of {a working Claude Code
CLI, an installed-but-unauthenticated CLI, only Ollama, nothing}.

**Tech Stack:** Python 3 standard-library HTTP bridge (`bridge_server.py`), no
web framework; vanilla JS front-end (`dashboard/app.js`, `index.html`,
`theme.css`); Ollama for the local fallback brain; Claude Code / Codex / Gemini
CLIs as subprocess providers.

**Commands:**
- Run bridge (dev): `cd dashboard && python bridge_server.py`
- First-run launcher (Windows retail): `installer\launchers\start_data.bat`
- Manual smoke of the provider API: `POST /provider {"provider": "..."}`,
  `GET /providers` (served by the handler at bridge lines ~8633 / ~9691).
- No formal test runner is wired in this project; verification is by targeted
  Python `-c` harness calls against the new helpers plus manual UI check.

**Project Structure:**
- `dashboard/bridge_server.py` — all provider/selection/install logic (target of
  most changes).
- `dashboard/app.js`, `dashboard/index.html`, `dashboard/theme.css` — the
  "authenticate your CLI" call-to-action surface.
- `installer/launchers/start_data.bat` — launcher (expected: no change).
- `dashboard/PLAN_brain_selection.md` — this plan.

**Testing Strategy:** No formal framework present. Each task carries a concrete
verification: a Python one-liner exercising the new helper with a monkeypatched
provider-executable/auth probe, plus a manual matrix walk (see Task 6). Tests
live inline in the verification steps; if the crew wants a durable harness, add
`dashboard/tests/test_brain_selection.py` under Task 6.

**Boundaries:**
- Always: keep every change inside `C:\Users\mixma\Documents\DATA\`; preserve the
  existing Claude-Desktop-stub rejection (`_is_claude_desktop_stub`, line 1030);
  strip `ANTHROPIC_API_KEY` before probing/spawning the Claude CLI (as the
  existing runners do, lines 5402 / 5565 / 7937); keep the auth probe fast and
  cached so it never blocks a page load or the startup path perceptibly.
- Ask first: adding any new pay-per-token path (API mode is disabled by Captain
  order); changing the compiled-in default provider id; adding a new external
  network call beyond the existing Ollama install URLs.
- Never: touch `C:\Users\mixma\Documents\LCARS\`; spawn the Claude Desktop
  launcher; run an actual model prompt merely to test auth (probe cheaply);
  override a provider the buyer explicitly selected via `/provider`.

**Success Criteria (testable):**
1. Fresh install, authenticated Claude CLI present → active brain resolves to
   `claude-cli` on first startup, with no buyer action. Persisted across restart.
2. Fresh install, Claude CLI present but NOT authenticated → active brain is
   `ollama` (fallback), AND the UI shows a clear "authenticate Claude Code" CTA.
   After the buyer authenticates and the probe re-runs, active brain flips to
   `claude-cli`.
3. Fresh install, no CLI, Ollama installed → active brain is `ollama`, no CTA.
4. Buyer explicitly selects any provider via `/provider` → that choice survives
   restart and is never auto-overridden by the selection logic.
5. On a local-LLM install, Ollama is still installed as the fallback regardless
   of CLI presence, but only becomes ACTIVE when no authenticated CLI exists.
6. `claude-cli` priority holds: if both an authenticated Claude CLI and other
   CLIs are present, Claude wins.

---

## Current behavior — file:line citations (what changes)

- **`ACTIVE_PROVIDER = "claude-cli"`** compiled-in default — `bridge_server.py:500`.
- **`PROVIDERS` map** (claude-cli / -sonnet / -haiku / -fable / codex / gemini /
  ollama / ollama-small) — `:960–1027`.
- **`_provider_executable`** resolves a CLI on disk, rejecting the Claude Desktop
  stub — `:1042–1108`.
- **`_provider_available`** — `:1111–1121`. For `subprocess`/`http` this returns
  True whenever the executable exists. **It never checks CLI authentication.**
  This is the core defect behind "dead brain" on an unauthenticated CLI.
- **`_list_providers`** builds the UI list; `available` comes straight from
  `_provider_available` — `:1124–1137`. No auth/CTA signal today.
- **`_save_active_provider` / `_load_active_provider`** — `:1437–1464`.
  `_load_active_provider` restores a persisted choice only if still available,
  else keeps the compiled-in default `claude-cli`. **It performs NO CLI-first
  selection and NO auth check** — so a fresh no-auth machine boots pointed at a
  dead `claude-cli`.
- **`_ollama_provision_job` auto-connect** — `:1556–1592`. Step 4 (`:1579–1592`)
  activates Ollama only when `not _provider_available(ACTIVE_PROVIDER)`. Since an
  unauthenticated CLI still counts as "available", Ollama will **refuse to take
  over** even though the CLI cannot answer. Must switch to an auth-aware check.
- **Startup call** `_load_active_provider()` — `:10346`. The new first-run
  selection must run here (after provider sync, before standing orders).
- **CLI runners** already strip `ANTHROPIC_API_KEY` and reject the desktop stub —
  `:5387–5395`, `:5560–5585`, `:7925–7959`. These show the exact spawn pattern the
  auth probe should mirror.
- **`/provider` POST endpoint** — `:9691–9706`. Sets `ACTIVE_PROVIDER`, persists.
  This is the "explicit buyer choice" path that must be respected (see Task 5).
- **`GET /providers`** handler returns `{active, providers}` — `:8633`.
- **`CONNECTOR_CATALOG`** carries per-CLI `login_cmd` (e.g. `claude (then /login)`)
  — `:1171–1187`. The CTA copy should reuse these.
- **First-run launcher** `installer\launchers\start_data.bat` — no brain logic;
  expected untouched.

---

## Task Plan

### Task 1: Add an authentication probe for CLI providers
- **Description:** Introduce `_provider_authenticated(provider_id)` in
  `bridge_server.py` that, for a subprocess CLI provider, determines whether the
  CLI is logged in. It resolves the executable via `_provider_executable`
  (returning False if absent), strips `ANTHROPIC_API_KEY`, and runs a cheap,
  bounded, non-interactive auth check (short timeout, `CREATE_NO_WINDOW`, no model
  prompt). For `claude-cli*` prefer a lightweight signal that does not open the
  desktop app (e.g. a `--print` no-op / whoami-style call, or inspecting the CLI's
  known credential file) — the crew picks the cheapest reliable signal during
  build and documents it in a code comment. Results are cached (e.g. 60s TTL,
  keyed by executable path) so page loads and startup never re-probe repeatedly.
  For `http`/ollama providers, auth is not applicable → treat as authenticated
  when available.
- **Acceptance criteria:** `_provider_authenticated("claude-cli")` returns True on
  a logged-in machine, False when the executable exists but is not logged in,
  False when the executable is absent; the call returns within its timeout and is
  cached; `ANTHROPIC_API_KEY` is stripped from the probe env; the Claude Desktop
  stub is never spawned.
- **Verification:** `python -c "import bridge_server as b; print(b._provider_authenticated('claude-cli'))"`
  on a logged-in box (expect True) and with credentials removed / env forcing a
  logged-out state (expect False). Confirm no GUI window appears and the call
  returns fast on a second (cached) invocation.
- **Dependencies:** None.
- **Files likely touched:** `dashboard/bridge_server.py` (new helper near
  `_provider_available`, ~:1121).
- **Scope:** Small (1 file).

### Task 2: CLI-first startup selection logic
- **Description:** Add `_select_default_brain()` (called from startup) that
  implements the locked priority for a fresh/unset install: (a) if a persisted
  explicit buyer choice exists and is still available, keep it — do not override
  (defer the full "explicit choice" guarantee to Task 5, but honor persistence
  here); (b) else, in priority order [`claude-cli`, then other CLIs
  `codex`/`gemini`], if a CLI is present AND `_provider_authenticated` is True,
  set `ACTIVE_PROVIDER` to it (Claude/Opus first); (c) else if any CLI is present
  but unauthenticated, set `ACTIVE_PROVIDER` to the Ollama fallback if available
  and record that an auth prompt is needed (Task 4 surfaces it); (d) else if
  Ollama is available, select it; (e) else keep the compiled-in default. Persist
  the resulting choice via `_save_active_provider`. Wire this into
  `_load_active_provider` (or call it immediately after, at `:10346`) so the
  compiled-in `claude-cli` default is no longer trusted blindly on a no-auth box.
- **Acceptance criteria:** On a machine with an authenticated Claude CLI and no
  persisted choice, startup yields `ACTIVE_PROVIDER == "claude-cli"`. With an
  unauthenticated Claude CLI + Ollama present, startup yields
  `ACTIVE_PROVIDER == "ollama"` and an internal "needs auth" flag is set. With no
  CLI + Ollama present, yields `ollama`. A valid persisted choice is preserved.
- **Verification:** Run the bridge startup path with `_provider_authenticated`
  and `_provider_executable` monkeypatched to simulate each of the four machine
  states; assert the resulting `ACTIVE_PROVIDER` and needs-auth flag.
- **Dependencies:** Task 1.
- **Files likely touched:** `dashboard/bridge_server.py` (`:1446–1464`, startup
  `:10346`).
- **Scope:** Medium (1 file, two functions).

### Task 3: Make Ollama fallback auth-aware (fix the auto-connect gate)
- **Description:** Change the auto-connect gate in `_ollama_provision_job`
  (`:1581`) from `not _provider_available(ACTIVE_PROVIDER)` to an auth-aware
  condition: Ollama should take over as ACTIVE when there is no *usable* brain —
  i.e. the current active provider is unavailable OR (it is a CLI and it is not
  authenticated) — while still always honoring an explicit `connect=True`. Ensure
  that on a local-LLM install Ollama is still installed regardless of CLI presence
  (installation already happens before this gate), but only becomes ACTIVE per the
  new rule. Do not override an explicitly chosen, working provider.
- **Acceptance criteria:** With an unauthenticated Claude CLI active, finishing an
  Ollama provision flips `ACTIVE_PROVIDER` to the Ollama model. With an
  authenticated Claude CLI active and `connect=False`, an Ollama provision installs
  the model but leaves `ACTIVE_PROVIDER` as `claude-cli`. `connect=True` always
  activates the pulled model.
- **Verification:** Call `_ollama_provision_job` (or just its step-4 block,
  refactored to a testable helper) with the auth probe monkeypatched across the
  authenticated / unauthenticated / explicit-connect cases; assert `ACTIVE_PROVIDER`.
- **Dependencies:** Task 1.
- **Files likely touched:** `dashboard/bridge_server.py` (`:1579–1592`).
- **Scope:** Small (1 file).

### Task 4: Surface the "authenticate your CLI" call-to-action (API + UI)
- **Description:** Expose the "CLI present but unauthenticated" state to the
  front-end and render a clear CTA. Backend: extend `_list_providers` (`:1124`)
  and/or the `GET /providers` response (`:8633`) so each CLI provider carries an
  `authenticated` boolean and a top-level `needs_auth` signal with the relevant
  `login_cmd` (reuse `CONNECTOR_CATALOG.login_cmd`, `:1174` — e.g. "run `claude`
  then `/login`"). Front-end: in `app.js` / `index.html` / `theme.css`, when
  `needs_auth` is set and the active brain is the Ollama fallback, show a
  dismissible banner/CTA telling the buyer their CLI (Claude Code) is installed but
  needs sign-in, with the exact command, and note DATA is running on the local
  fallback until then. When the buyer authenticates and the probe flips, the CTA
  clears and the UI can offer to switch to the CLI.
- **Acceptance criteria:** `GET /providers` returns per-provider `authenticated`
  and a `needs_auth` payload on an unauthenticated-CLI machine; the dashboard
  renders the CTA with the correct login command; the CTA disappears once the CLI
  is authenticated; nothing appears on a fully-authenticated or no-CLI machine.
- **Verification:** With the auth probe forced to "unauthenticated", load the
  dashboard and confirm the banner + command render; force "authenticated" and
  confirm it clears. Inspect the raw `GET /providers` JSON for the new fields.
- **Dependencies:** Tasks 1, 2.
- **Files likely touched:** `dashboard/bridge_server.py` (`:1124–1137`, `:8633`),
  `dashboard/app.js`, `dashboard/index.html`, `dashboard/theme.css`.
- **Scope:** Medium (3–4 files).

### Checkpoint — after Tasks 1–4
- [ ] `_provider_authenticated`, `_select_default_brain`, auth-aware Ollama gate,
      and the CTA all present and individually verified.
- [ ] Bridge starts cleanly (`python bridge_server.py`) with no import/startup
      errors on the developer machine.

### Task 5: Guarantee the explicit-buyer-choice guarantee end-to-end
- **Description:** Ensure a provider the buyer explicitly set via `/provider`
  (`:9691`) is durably marked as an explicit choice (e.g. a flag persisted
  alongside `active_provider` in `PROVIDER_STATE_FILE`, `:1437`) so that
  `_select_default_brain` (Task 2) and the Ollama auto-connect (Task 3) never
  override it — even if a "better" CLI later becomes authenticated. Distinguish a
  choice DATA made automatically (overridable by improving conditions) from a
  choice the buyer made (sticky). Keep backward compatibility with existing
  persisted state files that lack the new flag (treat legacy as non-explicit).
- **Acceptance criteria:** After `POST /provider {"provider":"ollama"}` the buyer's
  choice survives restart AND survives a subsequent Claude CLI authentication —
  DATA does not silently switch back to the CLI. An auto-selected provider (no
  explicit choice) still upgrades to an authenticated CLI when one appears.
- **Verification:** Set an explicit provider, restart the bridge with an
  authenticated CLI present, assert `ACTIVE_PROVIDER` is unchanged; then clear the
  explicit flag (simulate fresh install), restart, assert it upgrades to
  `claude-cli`.
- **Dependencies:** Tasks 2, 3.
- **Files likely touched:** `dashboard/bridge_server.py` (`:1437–1464`, `:9691–9706`).
- **Scope:** Medium (1 file).

### Task 6: Verification pass — the machine-state matrix
- **Description:** Walk (and, if the crew wants a durable harness, encode in
  `dashboard/tests/test_brain_selection.py`) the full decision matrix by
  monkeypatching `_provider_executable` and `_provider_authenticated`:
  (1) auth'd Claude CLI → active `claude-cli`;
  (2) unauth'd Claude CLI + Ollama → active `ollama` + CTA;
  (3) no CLI + Ollama → active `ollama`, no CTA;
  (4) nothing installed → compiled-in default, install guidance;
  (5) explicit buyer choice sticky across restart and across later CLI auth;
  (6) Claude priority when multiple authenticated CLIs are present;
  (7) local-LLM install still installs Ollama regardless of CLI presence but only
  activates it when no authenticated CLI exists.
  Confirm the launcher `start_data.bat` still needs no change.
- **Acceptance criteria:** All seven cases produce the expected `ACTIVE_PROVIDER`
  and CTA state; no case leaves the buyer with a dead brain; the Claude Desktop
  stub is never spawned in any case.
- **Verification:** Run the harness / manual walk; capture pass/fail per case.
- **Dependencies:** Tasks 1–5.
- **Files likely touched:** `dashboard/tests/test_brain_selection.py` (new,
  optional) or a documented manual checklist.
- **Scope:** Medium.

### Checkpoint — after Tasks 5–6
- [ ] Full matrix passes.
- [ ] `start_data.bat` confirmed unchanged and functional.
- [ ] No changes anywhere outside `C:\Users\mixma\Documents\DATA\`.

---

## Risks & edge cases
- **CLI installed but not authenticated** — the whole reason for Task 1. Do NOT
  equate "executable on disk" with "usable brain." The auth probe is load-bearing.
- **Choosing the auth signal cheaply** — a real model call is slow, may bill, and
  may open the desktop app. The probe must be a lightweight non-interactive check
  with a hard timeout and `CREATE_NO_WINDOW`; if no clean CLI flag exists, fall
  back to inspecting the CLI's credential file. Flag to Worf for a security pass.
- **Claude Desktop stub** — `_is_claude_desktop_stub` (`:1030`) must remain the
  gate on every probe/spawn; a probe that resolves to the desktop launcher would
  pop the GUI on startup.
- **Multiple CLIs present** — priority is fixed: authenticated Claude first, then
  Codex, then Gemini. Encode the order explicitly; do not rely on dict order.
- **Wrong / old CLI version** — an outdated CLI may not support the chosen auth
  flag. Treat a probe that errors ambiguously as "unauthenticated" (safe: keeps
  Ollama fallback + CTA) rather than "authenticated" (unsafe: dead brain).
- **Offline install** — Ollama runtime/model download can fail with no network
  (`_install_ollama_binary`, `:1474`). If both the CLI is unauthenticated AND
  Ollama can't be provisioned, the buyer must see clear guidance, not a silent
  dead brain. Ensure the CTA copy covers this.
- **macOS / Linux vs Windows** — `_provider_executable` already covers per-OS
  install dirs (`:1067–1094`) and the Ollama installer branches per-OS
  (`:1481–1514`). The auth probe and selection logic are OS-agnostic, but verify
  the credential-file path (if that route is chosen) on all three platforms.
- **Startup latency** — selection now runs an auth probe at boot. Keep it bounded
  and cached so `pythonw.exe` startup (via `start_data.bat`) is not visibly slowed.
- **Concurrency** — provider state is mutated from the request thread (`/provider`),
  the install thread (`_ollama_provision_job`), and startup. Guard the explicit
  flag and `ACTIVE_PROVIDER` writes consistently with the existing locking style.

## Open Questions
- None that block Task 1. The one design decision to make DURING Task 1 — the
  exact Claude-CLI auth signal (a CLI subcommand/flag vs. inspecting the
  credentials file) — can be resolved by the build crew and does not gate the
  plan. Recommend Worf review that choice before it ships.

## The Order of Battle
Start at Task 1: the authentication probe is the keystone — every other task
depends on distinguishing "CLI on disk" from "CLI that can actually answer," and
it concentrates the real risk (choosing a cheap, safe, GUI-free auth signal that
works on all three OSes and never trips the desktop stub). Prove it in isolation
first. Tasks 2 and 3 then apply that signal to the two places today's code wrongly
trusts mere presence — startup selection and the Ollama auto-connect gate. Task 4
makes the unauthenticated state visible to the buyer so no one is stranded on a
silent fallback. Task 5 locks the buyer's own choice against all of it, and Task 6
walks the full matrix. The design is proven the moment Task 1 plus Task 2 yield
`claude-cli` on an authenticated box and `ollama` on an unauthenticated one.

═══════════════════════════════════════════
Make it so.
