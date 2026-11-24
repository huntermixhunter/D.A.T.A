# Implementation Spec: Auto Model Router

2026-11-23 · Status: **Phase A in progress** (A1, A3 to A9 built and tested 2026-11-23, including Redo; A12 settings panel built 2026-11-24 in `auto_settings.js`/`.css`, with the UI half of A14 in `tests/test_auto_settings_ui.py`; uncommitted; owner decisions recorded in 16.1) · Phases B, C, D gated (see Task Plan)

> Source of intent: `docs/DESIGN_auto_router_conversation.md` (verbatim design conversation, 2026-11-23).
> House style: `docs/SPEC_connectors_page.md`.
> Build order: retail D.A.T.A first (this repo), then port to the owner's private dashboard build (see "Private dashboard port notes").

---

## 1. Problem and goals

**The problem.** The model selector asks a beginner to choose between a dozen model names (Opus, Sonnet, Haiku, Fable, Astra, Sol, Luna, Qwen...). Someone new to AI has no basis for that choice. In practice everything runs on the default (the strongest, most expensive model), and the subscription usage window runs out sooner than it needs to. Today every crew agent file seeded by `seed_crew_agents()` (`bridge_server.py` line 981) already carries a per-agent tier from `CREW_MODELS` (line 968), so the crew is efficient. The main chat is not.

**The goal.** An **Auto** entry in the model selector that, for each message, picks the cheapest model the user already pays for that is strong enough for the job, tells the user in plain words which kind of model answered and why, and lets them retry with a stronger one in one click.

**Goals (testable).**
1. **Beginner first.** A new install works well with zero model choices made. Auto is the default for new installs; the full model list moves under "Choose a model yourself".
2. **Zero new spend.** Auto only routes among models on the user's existing subscriptions or local hardware. It never calls a metered API and never causes credit or overage purchases.
3. **Conservative.** When the router is unsure, it routes up. High-risk work (security, money, deploys and releases, code changes, actions that change things) is locked to the strongest tier.
4. **Explained.** Every reply routed by Auto shows a badge such as "Auto · Standard · writing task", and every decision is logged with its reason.
5. **Proven.** Savings are measured against always-Opus using the existing usage ledger, with acceptance thresholds set before tuning begins (Section 11).
6. **Self-updating.** New models in a known family are picked up without code changes (aliases, Codex tier discovery, installed Ollama models, refreshable rating file).

---

## 2. Ground-truth findings (verified in this repo, not assumed)

These facts shape the design. Line numbers are from `bridge_server.py` unless stated.

**F1. There is one choke point for every turn.** `_run_turn_with_failover(provider_id, ...)` (line 9484) wraps `provider_failover.run_turn` (`provider_failover.py` line 833). It is called by `/chat_stream` (`_run_stream`, call at line 14333), by `_dispatch_with_provider` (line 9515, used by `/chat` and standing orders) and by `_voice_llm_stream` (line 9788). Auto resolves here, before the failover logic runs.

**F2. The provider registry is a flat dict of real brains.** `PROVIDERS` (line 1317): `claude-cli` (alias `opus`), `claude-cli-opus-5` and `claude-cli-opus-48` (pinned), `claude-cli-sonnet`, `claude-cli-haiku`, `claude-cli-fable` (aliases, lines 744 to 747), `codex` (Astra), `codex-sol`, `codex-luna`, `codex-mini` (legacy duplicate of Luna), `gemini`, `grok`, `ollama`, `ollama-small`, plus dynamic `ollama:<model>` ids from `_sync_ollama_providers()` (line 2125). The metered Anthropic API providers were removed (comment after the `claude-cli-fable` entry).

**F3. `ACTIVE_PROVIDER` (line 754) is read in 44 places** (failover default, Telegram, mail, usage snapshot, standing orders, health). **Decision: "auto" is never stored in `ACTIVE_PROVIDER`.** Auto is a separate router mode layered on top. `ACTIVE_PROVIDER` stays a real brain and becomes Auto's "home family" and last-resort fallback. This avoids touching 44 call sites.

**F4. A request picks its provider in one line.** `/chat_stream` (line 14182) reads `req_provider = data.get("provider", "")` (line 14192), rejects unknown ids against `PROVIDERS` (line 14214), gates attachments against `_MULTIMODAL_PROVIDERS` (line 1105) using `req_provider or ACTIVE_PROVIDER`, then resolves `pid = req_provider or ACTIVE_PROVIDER` (line 14300). The main chat sends `provider: mainWs?.provider || ''` (`app.js` line 1867); project panes send `provider: ws.provider` (`app.js` line 9687).

**F5. Usage limits are per family, not per model.** `provider_failover.limited_until(fam)` (line 340) and `_meter_blocks(fam)` (line 620) key on `claude`, `codex`, and so on. When Claude hits its 5-hour limit, Haiku, Sonnet and Opus are all blocked. **So "if a model is limited, move up a tier" (as sketched in the design conversation) does not work inside one family.** The fallback must cross families (Section 6.10).

**F6. Every runner today can take actions with permissions bypassed.** Claude: `--dangerously-skip-permissions` (line 8386). Codex: `--dangerously-bypass-approvals-and-sandbox` (line 8695). Ollama: `ask_ollama_stream` (line 9286) runs a full tool loop via `_tools_for_ollama()` and `execute_tool`. A beginner prompt misrouted to a small model can therefore edit or delete files. This drives the read-only rule (Section 6.9).

**F7. Codex can be signed in with a metered API key.** `_codex_cli_authenticated()` (line 1761) returns True for either ChatGPT OAuth `tokens` **or** an `OPENAI_API_KEY` stored in `~/.codex/auth.json`. The Codex runner strips the env var (line 8678) but not a key saved in `auth.json`. Auto must treat API-key-only Codex as metered and ineligible.

**F8. The existing `model_catalog.json` is not a capability table.** It is the Ollama download list for the AI Connectors page, strictly validated by `model_refresh.validate_catalog()` (`model_refresh.py` line 257, `schema: 1`, fixed `use` enum) and refreshed from `CATALOG_URL` (line 38). **Decision: do not overload it.** Auto gets its own small rating file, `router_catalog.json`, refreshed by the same mechanism (`fetch_remote_catalog` line 291, `choose_catalog` line 307: https only, size cap, newest valid wins).

**F9. New models are already discovered for three families.** Claude aliases auto-ride and `_note_claude_model()` (line 2320) records the resolved version. `_apply_codex_refresh()` (line 2333) re-points Codex ids by tier name via `model_refresh.codex_tiers()` (line 55): astra, sol, luna. `_apply_ollama_refresh()` (line 2371) and `_ollama_installed_models()` (line 2104) see installed local models. Auto rates by **tier family**, not by version, so a new Opus or a new `gpt-7-astra` inherits its tier with no change.

**F10. The usage ledger exists but has gaps.** `_cli_usage_meta()` (line 8220) condenses the Claude `result` event (per-model `modelUsage`, so subagent models appear too). `_log_usage()` (line 8259) appends to `users/<id>/usage_ledger.jsonl` (`USAGE_LEDGER_NAME`, line 8210). The final `meta` SSE event is sent at lines 8623 to 8624 (Claude) and 8865 to 8866 (Codex). Gaps: (a) Ollama, Gemini and Grok turns send a bare `meta` (for example line 9453) and **never call `_log_usage`**; (b) `_log_usage` skips rows with zero tokens; (c) rows carry no routing fields. Codex rows carry `cost_usd: 0` (Codex reports no price).

**F11. The usage tag renders from `meta`.** `app.js`: main stream handler stores `_streamMeta` at line 1923; `_attachUsageTag(msgEl, meta)` at line 2166 (called at lines 1937 and 9756); `_shortModelName()` at line 2148. The badge attaches next to this tag.

**F12. A cheap one-shot model helper exists.** `_mail_llm(prompt, system, tier)` (line 7578) runs `claude --print --output-format text` with a tiny system prompt, or Ollama via `_mail_llm_ollama`. It is the template for the triage call, with two required changes: JSON output (so usage is captured) and tools disabled (it currently passes `--dangerously-skip-permissions` with tools on).

**F13. History is capped, so switching models mid-chat is cheap in history terms but not in cache terms.** Each turn is a fresh CLI process; replayed history is capped at `HISTORY_CHAR_BUDGET = 12000` (line 8036). The fixed per-turn setup (system prompt, memory, skills list) was noted in the design conversation at about 62K tokens. The prompt cache is per model: staying on a warm model re-reads that setup at cache-read cost (about 0.1x, per the comment at line 8203); switching to a cold model pays cache-write cost (about 1.25x). **For short messages, switching down to a cold cheap model can cost more than staying on a warm strong one.** This drives the stickiness rule (Section 6.5) and is measured in Phase B.

**F14. Settings already have a MODEL tab.** `index.html` line 744 (tab) and line 892 (pane: Default LLM, Backup LLM, Usage-limit failover). The Auto settings live in this pane. The usage meter and failover UI live in `app.js` from line 10960 (`fetchUsage` line 11085).

**F15. Tests are stdlib `unittest`** (buyer machines have no pytest), run from `dashboard/`, for example `python -m unittest tests.test_provider_failover -v`. Relevant existing suites: `tests/test_provider_failover.py`, `tests/test_model_refresh.py`, `tests/test_brain_selection.py`.

---

## 3. Spec summary

**Objective.** Add an Auto router mode that chooses one model per message from the user's zero-cost pool, explains the choice, logs it, and can retry stronger. Later phases add measurement, a multi-model Conductor mode, and disabled paid slots.

**Who the user is.** Primary: a beginner who never picks a model. Secondary: a power user (the owner) who wants to see and tune the rules.

**Tech stack.** Python 3 stdlib bridge (`bridge_server.py`, `ThreadingHTTPServer`), vanilla JS front end (`index.html`, `app.js`, `theme.css`, `usage_meter.css`), no build step. New logic lives in a new stdlib module `auto_router.py` that, like `provider_failover.py`, takes its bridge dependencies as injected functions so it is unit-testable without a CLI.

**Commands.**
- Run: `cd <install folder>\dashboard` then `python bridge_server.py`
- Router unit tests: `python -m unittest tests.test_auto_router -v`
- Related suites: `python -m unittest tests.test_provider_failover tests.test_model_refresh tests.test_brain_selection -v`
- Whole suite: `python -m unittest discover -s tests -t . -v`
- Smoke: `curl -N -X POST http://127.0.0.1:<port>/chat_stream -H "Content-Type: application/json" -d "{\"message\":\"hi\",\"provider\":\"auto\"}"` (add the `X-Data-Token` header if one is configured)
- Report (Phase B): `python tools/auto_report.py --ledger users/<id>/usage_ledger.jsonl`

**Project structure (new and touched).**
```
dashboard/
  auto_router.py               NEW  pure routing logic: categories, rules, tiers, decide(), copy
  router_catalog.json          NEW  bundled model ratings (tier, cost rank, vision, local)
  bridge_server.py             EDIT dispatch hook, candidates, settings endpoints, ledger fields, triage, escalation
  provider_failover.py         EDIT public blocked_reason(fam) wrapper over _meter_blocks
  model_refresh.py             EDIT generic catalog fetch so router_catalog.json refreshes like model_catalog.json
  app.js                       EDIT selector entry, route event, badge, Redo, settings panel
  index.html                   EDIT Auto section in the MODEL settings pane
  usage_meter.css              EDIT badge and settings styles (or theme.css, match neighbours)
  tests/test_auto_router.py    NEW
  tests/fixtures/auto_router_prompts.jsonl   NEW (Phase B) labelled prompt set
  tools/auto_report.py         NEW (Phase B) ledger analysis
  tools/auto_benchmark.py      NEW (Phase B) A/B replay harness
  docs/SPEC_auto_router.md     this file
users/<id>/
  auto_router.json             NEW  per-user router settings
  usage_ledger.jsonl           EXISTING, gains routing fields
```

**Testing strategy.**
- **Unit (most of the value, free):** `auto_router.decide()` is pure; every rule, tie-break, lock and fallback is a table-driven test. Hard gate: 100% of labelled high-risk prompts route to Strongest.
- **Integration (free):** bridge helpers tested with stub runners, as `tests/test_provider_failover.py` already does.
- **Smoke (spends a little usage):** curl `/chat_stream` with `provider: "auto"`; inspect the `route` event, `meta`, and the ledger row.
- **Browser (manual, or a `tests/auto_router_browser.py` following the existing `*_browser.py` pattern):** selector, badge, Redo, settings.
- **Benchmark (Phase B, spends usage, ask first):** A/B replay of a fixed prompt set, Auto versus always-Opus.

**Boundaries.**
- *Always:* route up when unsure; keep locked categories on Strongest; log every decision with a reason code; keep the router pure and dependency-injected; keep prompt text out of routing logs; run below-top tiers read-only; keep all new user-facing copy free of fictional or franchise vocabulary.
- *Ask first:* running the Phase B benchmark (it spends subscription usage); adding Gemini or Grok to the default pool; changing locked categories; making Auto the default for existing (upgraded) installs; any new third-party dependency; anything in Phase D.
- *Never:* select a metered model; select Codex when it is signed in with an API key only; select a family whose window reads 100% (that is where overage or credits begin); store "auto" in `ACTIVE_PROVIDER`; let a remote catalog change a model's spend class; let a triage or classification call run with tools enabled.

**Success criteria (Phase A exit).**
1. Selecting Auto in the main chat or a project pane routes each message to a real provider and the reply shows a badge with a plain-language reason.
2. `tests.test_auto_router` passes, including: every locked-category prompt in the fixture routes to Strongest; an unclear prompt with triage off routes to Strongest; a disabled or metered or API-key Codex candidate is never chosen.
3. Every Auto turn writes exactly one ledger row with `route_mode`, `auto_chosen`, `route_reason`, `chosen_tier`, `category`, `escalated`; triage calls write their own row with `kind: "triage"`.
4. A forced Fast-tier turn asked to create or delete a file cannot do so.
5. "Try a stronger model" re-answers on the next tier and the ledger records `redo_of`.
6. Settings changes persist in `users/<id>/auto_router.json` and survive a bridge restart.
7. Manual mode behaves exactly as today (regression: existing failover and brain-selection suites pass unchanged).

---

## 4. Non-goals (v1)

- **No metered spend of any kind.** No Anthropic API, no OpenAI API keys, no Ollama cloud tags (`model_refresh.is_cloud_model`, line 221), no purchase of credits or extra usage.
- **Not for voice, Telegram, mail, or standing orders in v1.** Voice keeps `VOICE_PROVIDER` (line 1062, Haiku by default), the mail pipeline keeps `_mail_llm` tiers, Telegram keeps `TELEGRAM_PROVIDER`. If any non-chat caller passes `"auto"`, it resolves straight to the Strongest tier with no triage.
- **No per-message reasoning-effort routing.** Auto keeps the user's effort setting and only clamps it to what the chosen model supports (`effective_effort` from `model_capabilities`).
- **No system-prompt trimming.** Shrinking the fixed setup for light turns is probably the largest single saving available (F13), but it is a separate project. Logged as a follow-up.
- **No quality scoring from thumbs up or down in v1.** The design conversation proposed local feedback adjusting ratings; deferred to Phase B as an optional task once data exists.
- **No Conductor in Phase A.** Splitting one task across models is Phase C, behind its own gate.
- **No fresh-chat nudge.** Investigated in the design conversation and shown not to save meaningful usage in D.A.T.A because history is capped (F13).

---

## 5. User stories

**Beginner (primary).**
1. As a new user, I install D.A.T.A, type a question, and get a good answer without ever seeing a model name.
2. As a new user, I can see under each reply whether a Fast, Standard or Strongest model answered, and one short reason, in words I understand.
3. As a new user, if an answer seems weak, I press "Try a stronger model" and get a better one, without knowing what the models are called.
4. As a new user, if I ask about my bank account, a password, or deleting files, D.A.T.A uses its strongest model automatically, and tells me why.
5. As a new user, my allowance lasts longer than it would if everything ran on the strongest model, and nothing ever costs me extra money.
6. As a new user, I can open Settings and find one simple control: Save usage / Balanced / Best quality.

**Power user (secondary).**
7. As the owner, I can see the real model name, the category, the rules that fired, and whether triage was used, by opening "Why this model?" on any badge.
8. As the owner, I can set a minimum tier per category (for example "writing never below Standard"), choose which models Auto may use, and turn triage and automatic retry on or off.
9. As the owner, I can type "use opus" or "use the strongest model" in a message to override Auto for that message.
10. As the owner, I can run a report that shows how much Auto saved versus always-Opus, per category, with the Redo and retry rates.
11. As the owner, when a new model ships in a family I already use, Auto starts using it with no update; when a brand-new family appears, it stays unused until rated.

---

## 6. The routing algorithm

### 6.1 Tiers

Three tiers. Users see the plain names; the ids are internal.

| Tier id | Plain name (badge) | Claude | Codex (ChatGPT sign-in) | Local (Ollama) |
|---|---|---|---|---|
| `top` | **Strongest** | `claude-cli` (alias `opus`) | `codex` (Astra tier) | never in v1 |
| `mid` | **Standard** | `claude-cli-sonnet` | `codex-sol` | never in v1 (user may rate one up) |
| `light` | **Fast** | `claude-cli-haiku` | `codex-luna` | rated local chat models (for example `qwen2.5:3b`, `ollama-small`) |

Excluded from Auto by default: pinned versions (`claude-cli-opus-5`, `claude-cli-opus-48`), the legacy duplicate `codex-mini`, `claude-cli-fable` (unrated, see Open Question 1), `gemini` and `grok` (Open Question 2), and any unrated model. All remain available in Manual mode exactly as today.

The tier names avoid "Balanced" on purpose: that word is already the middle bias setting, and a beginner should not see one word meaning two things.

### 6.2 Categories and the tier table

Each category has a stable id (used in settings keys and the ledger). The router can match several categories; the highest resulting tier wins.

| Category id | Examples | Main signals | Save usage | **Balanced (default)** | Best quality | Locked |
|---|---|---|---|---|---|---|
| `chat` | "hi", "thanks!", "how are you" | greeting or thanks lexicon, under 80 chars, no other hit | Fast | **Fast** | Standard | no |
| `lookup` | "what time is it in Vegas", "convert 5 miles to km", "define entropy" | question word, under 200 chars, one ask, no code | Fast | **Fast** | Standard | no |
| `rewrite` | "fix the grammar", "make this shorter", "turn this into a list" (text included) | rewrite verbs plus pasted text, one ask | Fast | **Fast** | Standard | no |
| `writing` | email, caption, blog post, story, bio | write or draft verbs plus content nouns | Fast | **Standard** | Strongest | no |
| `research` | "explain", "compare", "pros and cons", "summarize this long article" | explain or compare verbs, long paste with one ask | Standard | **Standard** | Strongest | no |
| `personal` | journaling, feelings, relationship or habit advice | personal lexicon | Standard | **Standard** | Strongest | no |
| `vision` | image or PDF attached | non-audio attachment | Standard | **Standard** | Strongest | no (Claude family only) |
| `code_help` | "what does this error mean", "explain this snippet" (no change requested) | code fence or stack trace without edit verbs, not in a project pane | Standard | **Standard** | Strongest | no |
| `planning` | plans, strategy, roadmaps, "help me decide" | planning lexicon | Standard | **Strongest** | Strongest | no |
| `health_legal` | symptoms, medication, contracts, taxes, legal rights | health and legal lexicon | Strongest | **Strongest** | Strongest | no (adjustable, see OQ 7) |
| `code_change` | "fix the bug", "refactor", "implement", stack trace in a project pane | edit verbs plus code context, or any code request in a project pane with a path | Strongest | **Strongest** | Strongest | **yes** |
| `security` | passwords, API keys, tokens, auth, permissions, vulnerabilities, "was I hacked" | security lexicon | Strongest | **Strongest** | Strongest | **yes** |
| `money` | trading, buy, sell, crypto, wallet, bank, payment, invoice, refund | money lexicon | Strongest | **Strongest** | Strongest | **yes** |
| `release` | deploy, release, publish, production, server, cron, DNS, installer | release and infra lexicon | Strongest | **Strongest** | Strongest | **yes** |
| `action` | send, post, delete, move, rename, install, uninstall, schedule, "change my settings", "run this" | action verbs aimed at files, accounts, messages, or the system | Strongest | **Strongest** | Strongest | **yes** |
| `multi_part` | three or more distinct asks, or two or more categories from different rows above | ask count, category count | Strongest | **Strongest** | Strongest | no |
| `unclear` | "can you help with my thing", vague or no rule matched | no category hit, or only weak hits | goes to triage; if triage is off, fails, or is unsure: **Strongest** in every bias | | | n/a |

**Locked** means no setting, bias, or in-message request can lower it. In-message requests can still raise any category.

### 6.3 Signals (all free, computed in under 50 ms)

1. **Lexicon hits.** Per-category word lists, matched with word boundaries, case-insensitive, compiled once. Stored in `auto_router.py` as data (`RULES = [...]`), each rule with a stable id (for example `sec.password`, `act.delete`), its category, and a weight `strong` or `weak`. Only rule ids are logged, never the matched words.
2. **Length and shape.** Character count; count of distinct asks (question marks, numbered or bulleted asks, "and also", "then"); presence of a code fence, a stack trace pattern (`Traceback`, `at x.y(`, `Error:` lines), file paths or extensions.
3. **Pane context.** A project pane with `project_path` set raises any code-shaped request to `code_change`.
4. **Attachments.** Non-audio attachment sets `vision` and restricts candidates to `_MULTIMODAL_PROVIDERS` (Claude family). Audio attachments are transcribed first (as today) and do not affect routing.
5. **Explicit request in the message.** "use opus", "use the strongest model", "think hard", "be careful", "double check" raise to Strongest. "use sonnet" sets Standard. "use haiku", "quick answer", "just briefly" may lower to Fast only for non-locked categories. The phrase is not stripped from the message.
6. **Thread stickiness.** See 6.5.
7. **Approval follow-ups.** Short replies such as "yes", "do it", "go ahead", "ship it" inherit the previous turn's tier and category, because they usually approve an action the previous turn proposed.

### 6.4 Decision procedure

`auto_router.decide(ctx) -> Decision` where `ctx` holds the message, pane key, project path, attachment kinds, the pane's last decision, the user settings, and an injected `candidates()` function. Steps:

1. **Classify.** Run all rules. Collect matched categories and rule ids.
2. **Confidence.** `confident` if at least one strong rule hit; `weak` if only weak hits; `none` if nothing matched and the message is not a greeting.
3. **Base floor.** For each matched category, look up the bias column (6.2). Floor = highest.
4. **Settings floor.** Apply per-category minimums from Advanced settings. They can raise any category; they can lower only non-locked ones.
5. **Explicit request.** Apply 6.3 item 5 within the lock rules.
6. **Stickiness.** Floor = max(floor, sticky floor from 6.5).
7. **Unclear handling.** If confidence is `weak` or `none` and no locked category matched: call triage (6.7) if enabled. Triage may set the tier for the unclear part, but the final floor is never below steps 3 to 6. If triage is off, times out, fails validation, or returns low confidence: floor = Strongest.
8. **Select a model** (6.6).
9. **Return** a `Decision`: `provider_id`, `model`, `family`, `tier`, `min_tier`, `category` (the one that set the floor), `categories`, `rule_hits`, `confidence`, `reason_code`, `reason_text`, `triage` (null or result), `sticky`, `fallback` (null or reason), `bias`, `catalog_version`, `router_version`, `decided_ms`.

**Tie-break for the reported reason.** When several categories produce the same final tier, the badge names one, in this priority: `action` > `security` > `money` > `release` > `code_change` > `health_legal` > `multi_part` > `planning` > `vision` > `code_help` > `research` > `personal` > `writing` > `rewrite` > `lookup` > `chat`.

### 6.5 Stickiness (thread rule)

Switching models mid-task loses the warm prompt cache (F13) and can make the new model lose the thread. Rule:

- Within `sticky_minutes` (default 10) of the pane's previous Auto turn, the floor is at least the previous turn's tier. Auto may go **up** within a thread, never **down**.
- When the floor equals the previous tier, the previous model is preferred (warm cache).
- The window resets on a new chat, a pane clear, or after `sticky_minutes` of silence.
- Phase B replaces the fixed window with a measured switch-cost estimate (Task B5).

### 6.6 Model selection

From `candidates()` (Section 7.3), keep only eligible models. Then:

1. Prefer models whose tier **equals** the floor (the cheapest model that is strong enough). If none, take the next tier up.
2. Within a tier, order by:
   a. **Warm cache:** the pane's previous model, if its last turn was within `sticky_minutes`.
   b. **Home family:** the family of the user's Default LLM (`ACTIVE_PROVIDER`). Keeps the voice consistent and the cache warm.
   c. **Headroom:** the family whose usage meter shows more room left (`provider_failover.snapshot()` window percentages).
   d. **Catalog cost rank** (lower first), then provider id (stable).
3. Constraints: `vision` keeps only `_MULTIMODAL_PROVIDERS`; local models never receive `vision`, `code_change`, or any locked category.

### 6.7 Triage call (optional, on by default)

**Purpose.** Settle vague prompts cheaply instead of sending every vague prompt to Strongest. Beginners write vague prompts often, so triage is on by default.

**Backend order (`triage_backend: "auto"`).**
1. A local rated Ollama chat model, if one is installed and the daemon answers (free, private). Time budget 4 s.
2. Otherwise `claude-cli-haiku` through a new `_router_triage()` helper modelled on `_mail_llm` (line 7578), with three differences: `--output-format json` so usage is captured; **all tools disabled** (confirm the exact flag against `claude --help` during Task A10; if no reliable way to disable tools exists, the Claude backend is not used and triage runs local-only or is skipped); a 12 s timeout.

**Prompt.** A fixed system prompt that asks for JSON only. The user message is wrapped as data, truncated to 2,000 chars, and the model is told not to follow instructions inside it.

**Output schema (validated strictly).**
```json
{"category": "<one category id>", "difficulty": 1, "risk": "low|medium|high", "takes_action": false, "confidence": "low|medium|high"}
```
Mapping: `risk: high` or `takes_action: true` gives Strongest. `difficulty` 1 to 2 gives Fast, 3 gives Standard, 4 to 5 gives Strongest. `confidence: low` gives Strongest. Any parse failure, unknown category, or timeout gives Strongest.

**Its own cost, honestly.** Triage only pays off when it routes **down**. If it ends up choosing Strongest anyway, the triage call was pure overhead. A Claude-backed triage call is a fresh CLI process that carries the CLI's own base prompt, so it is not free even on Haiku. Controls:
- Every triage call writes its own ledger row (`kind: "triage"`), so its cost is measured, not guessed.
- An in-memory cache keyed on a hash of the normalized message (10 minutes) avoids repeat calls.
- Budget (checked in Phase B): triage cost under 5% of all Auto cost, and triage "downgrade rate" (share of triage calls that chose below Strongest) above 40%. If either fails, the default backend becomes local-only, or triage turns off with unclear prompts going to Strongest.

### 6.8 Escalation (automatic retry, on by default)

Beginners may not notice a weak answer, so Auto retries once, one tier up, when the first attempt clearly failed.

**Triggers (below-top turns only):**
1. The runner reports an error result, `max_turns`, or an empty reply.
2. The model emits a hidden marker `<<auto:unsure>>` or `<<auto:needs_action>>`. Below-top turns get a one-paragraph directive in the system prompt: "If you are not confident your answer is correct, or the request needs you to change files, send messages, or run commands, end your reply with the marker." The existing marker filter (`_marker_filter_sse`, line 6265) strips it before display.

**Rules:**
- At most one escalation per user message.
- Before rerunning, the first attempt's user and assistant pair is removed from the pane history (mirrors how failover handles `handing_off()`), so the retry does not see a duplicate message.
- The first answer stays visible but collapsed ("First try, Standard model"), and the new answer streams below a divider.
- Escalation is skipped (and the Redo button is highlighted instead) if the first attempt ran any write or exec tool. With the read-only rule in place that should not happen below Strongest, but the guard stays.

### 6.9 Read-only below the top tier

Because every runner can act today (F6), and because a beginner is the least able to catch a mistaken action, **Fast and Standard turns routed by Auto run read-only**:
- Claude: deny write and exec tools (Write, Edit, NotebookEdit, Bash) via the CLI's tool allow or deny flags; keep Read, Glob, Grep, WebSearch, WebFetch. Confirm the flag names against `claude --help` in Task A7.
- Codex: replace `--dangerously-bypass-approvals-and-sandbox` with the read-only sandbox mode for below-top turns. Confirm against `codex exec --help`.
- Ollama: filter `_tools_for_ollama()` to a read-only allowlist for Auto turns.
- If a runner cannot enforce read-only, that model is ineligible for every category except `chat`, `lookup`, `rewrite`, `writing`.

If a read-only model needs to act, it emits `<<auto:needs_action>>` and escalation moves the turn to Strongest, which runs with today's full permissions. Manual mode is unchanged. See Open Question 3.

### 6.10 Fallback when a model is unavailable or limited

Because limits are per family (F5):
1. Same tier, another eligible family (Claude Sonnet limited: try Codex Sol). This mirrors the tier pairs in `provider_failover.FALLBACK` (line 53).
2. Next tier up in any eligible family.
3. Step down a tier only if `allow_step_down_on_limit` is on (default off).
4. Nothing eligible: run on `ACTIVE_PROVIDER` through the existing failover path (which already applies the user's Backup LLM), with reason code `fallback_default`.

A limit that hits mid-turn is still handled by `provider_failover.run_turn` exactly as today; Auto records `fallback` from its return value.

---

## 7. Model catalog and availability detection

### 7.1 `router_catalog.json` (new, bundled, refreshable)

Small, data only, schema-validated. It rates **tier families**, not versions.

```json
{
  "schema": 1,
  "updated": "2026-11-23",
  "entries": [
    {"match": "claude:opus",    "tier": "top",   "cost_rank": 3, "vision": true,  "local": false},
    {"match": "claude:sonnet",  "tier": "mid",   "cost_rank": 2, "vision": true,  "local": false},
    {"match": "claude:haiku",   "tier": "light", "cost_rank": 1, "vision": true,  "local": false},
    {"match": "codex:astra",    "tier": "top",   "cost_rank": 3, "vision": false, "local": false},
    {"match": "codex:sol",      "tier": "mid",   "cost_rank": 2, "vision": false, "local": false},
    {"match": "codex:luna",     "tier": "light", "cost_rank": 1, "vision": false, "local": false},
    {"match": "ollama:qwen2.5:3b",  "tier": "light", "cost_rank": 0, "vision": false, "local": true},
    {"match": "ollama:qwen3.5:*",   "tier": "light", "cost_rank": 0, "vision": false, "local": true}
  ],
  "scores_note": "Optional per-entry 'scores' {coding, writing, reasoning, speed} 1 to 5 are reserved for Conductor (Phase C) and ignored by Auto."
}
```

**What the catalog may and may not do.**
- May: set `tier`, `cost_rank`, `vision`, optional `scores` for a `match` key.
- May not: add providers, change a provider's spend class, mark anything as free, or enable a model the user disabled. **Spend class is owned by code** (7.4), so a tampered or mistaken remote file can never route to a paid model.
- Validation: same rules as `model_refresh.validate_catalog` (https URL, 256 KB cap, max 80 entries, strict field types, bad entries dropped, fewer than 3 good entries rejects the file). Newest valid copy wins (bundled, cached, remote), using `choose_catalog`.
- Refresh: piggybacks on `_run_model_refresh()` (line 2455) and `POST /models/refresh`, so "Check for new models" in Settings refreshes both files. Cost: one small free download.

### 7.2 How new models are discovered without code changes

| Family | How the newest model arrives | How Auto rates it |
|---|---|---|
| Claude | Aliases `opus`, `sonnet`, `haiku` resolve inside the CLI (lines 740 to 747); `_note_claude_model()` records the real id | By alias (`claude:opus` and so on), so a new Opus is Strongest on day one |
| Codex | `_apply_codex_refresh()` re-points `codex`, `codex-sol`, `codex-luna` from `~/.codex/models_cache.json` via `codex_tiers()` | By tier name (`astra`, `sol`, `luna`) |
| Ollama | `_ollama_installed_models()`, `_sync_ollama_providers()` register `ollama:<model>` | By pattern in the catalog; **unrated local models are not used** until the catalog or the user rates them |
| New family (for example a future CLI) | Needs a `PROVIDERS` entry (code) | Unrated until the catalog has an entry; never auto-picked before that |

**User overrides.** In Advanced settings, any model can be set to "Treat as Fast / Standard / Strongest / Don't use". Stored in `auto_router.json` under `model_overrides`; overrides beat the catalog. This covers the honest limit raised in the design conversation: Auto cannot judge an unfamiliar model by itself; someone has to rate it first.

### 7.3 Availability check (per decision, cheap)

New bridge function `_auto_candidates() -> list[dict]`, passed to the router as `candidates()`. For each provider id with a catalog tier (or user override), a candidate is **eligible** only if all are true:

1. Not excluded by default (6.1) and allowed in settings (`allowed`).
2. `_brain_usable(pid)` (line 2706): executable present and signed in (auth probe cached 60 s, line 1658 onward).
3. Spend class is `subscription` or `local` (7.4).
4. Codex only: `_codex_auth_mode() == "chatgpt"` (new helper: OAuth `tokens` present in `~/.codex/auth.json`; API-key-only means `"api_key"` and ineligible). Closes F7.
5. Ollama only: the model is pulled and `not model_refresh.is_cloud_model(name)`.
6. Not limited: `provider_failover.limited_until(family) is None`.
7. Not blocked: new public `provider_failover.blocked_reason(family)` (thin wrapper over `_meter_blocks`, line 620) returns None. This excludes a family whose live window reads 100%, which is where extra usage or credits would begin.

Each candidate carries `{pid, family, tier, cost_rank, vision, local, headroom_pct, warm}`. Ineligible candidates are returned too, with a `why_not` string, so the "Why this model?" panel and the routing log can explain skipped options.

### 7.4 Spend classes (code-owned)

A new `SPEND_CLASS` map in `bridge_server.py`, next to `PROVIDERS`:

| Class | Members | Auto pool |
|---|---|---|
| `subscription` | `claude-cli*`, `codex*` (ChatGPT sign-in only) | yes |
| `local` | `ollama*` (non-cloud tags) | yes |
| `free_tier` | `gemini` | no by default (OQ 2): the Gemini CLI can be pointed at a billed Cloud project |
| `other_subscription` | `grok` | no by default (OQ 2) |
| `metered` | any paid slot (Phase D) | never, unless the slot is explicitly enabled with a cap |

---

## 8. Settings panel

### 8.1 Storage

- Per-user file `users/<id>/auto_router.json`, alongside `usage_ledger.jsonl` (via `_user_dir()`, line 549). Written atomically (temp file plus `os.replace`, same pattern as `_write_provider_state_unlocked`).
- Endpoints: `GET /router/settings` returns settings, effective defaults, the candidate list with eligibility, and catalog version. `POST /router/settings` accepts a partial object, validates every field, rejects attempts to lower a locked category, and returns the saved result.
- Only UI state (for example whether Advanced is expanded) goes in `localStorage`, under `data.router.advancedOpen`.

### 8.2 Controls, defaults, keys

**Simple view (what a beginner sees).**

| Control | Key | Type | Default |
|---|---|---|---|
| Model choice: Auto (recommended) / Choose a model yourself | `mode` | `"auto"` or `"manual"` (later `"conductor"`) | `"auto"` on new installs; `"manual"` on upgraded installs (OQ 4) |
| How should Auto choose? Save usage / Balanced / Best quality | `bias` | `"save"`, `"balanced"`, `"quality"` | `"balanced"` |

**Advanced (collapsed by default).**

| Control | Key | Type | Default |
|---|---|---|---|
| Minimum model per kind of task (one row per non-locked category; locked rows shown greyed with "Always Strongest") | `category_floor.<category>` | `"auto"`, `"fast"`, `"standard"`, `"strongest"` | all `"auto"` |
| Models Auto may use (checkbox list with sign-in status and why-not text) | `allowed.<provider_id>` | bool | true for the Claude aliases, Codex tiers, rated local models; false for pinned, `codex-mini`, Fable, Gemini, Grok |
| Treat this model as... | `model_overrides.<provider_id>` | `"fast"`, `"standard"`, `"strongest"`, `"off"`, or absent | absent |
| Quick check for unclear requests | `triage` | bool | true |
| Where the quick check runs | `triage_backend` | `"auto"`, `"local"`, `"claude"` | `"auto"` |
| Retry with a stronger model when an answer looks unsure | `escalation` | bool | true |
| Allow a weaker model when usage limits are hit | `allow_step_down_on_limit` | bool | false |
| Keep the same model during a conversation for (minutes) | `sticky_minutes` | int 0 to 60 | 10 |
| Show model names on badges | `show_model_names` | bool | false |
| Check for new models (button) | n/a | calls `POST /models/refresh` | n/a |
| Routing log (last 50 decisions: time, category, tier, model, reason; no prompt text) | n/a | reads `GET /router/log?limit=50` | n/a |

Reserved keys, not shown until their phase: `keep_private_local` (Phase B, OQ 9), `conductor.*` (Phase C), `paid.*` (Phase D).

**Placement.** A new "Auto model choice" section at the top of the MODEL settings pane (`index.html` line 892), above "Default LLM". When `mode` is `"auto"`, the Default LLM row is relabelled "Home model (used when Auto has no better choice)".

---

## 9. UI

### 9.1 Selector entry

- **Main chat select** (`#main-provider-select`, `app.js` line 9540, filled by `_populateMainProviderSelect()` line 8750) and **pane selects** (`_windowProviderSelectHTML()` line 8617, `_populateWindowProviderSelect()` line 8625): first option is `Auto (recommended)` with value `auto`; the real models follow inside `<optgroup label="Choose a model yourself">`.
- **Header provider menu** (`loadProviders()` line 6743): same first item.
- Selecting Auto in a pane sets `ws.provider = "auto"`, which `/chat_stream` then receives (F4). Selecting Auto in the main chat with no project loaded calls `POST /router/settings {"mode":"auto"}` instead of `POST /provider` (`setMainProvider()`, line 8763), because "auto" is never a value of `ACTIVE_PROVIDER` (F3).
- When `mode` is `"auto"` and the main chat sends `provider: ""`, the bridge treats it as `"auto"`.

### 9.2 The badge

Rendered from a new `route` SSE event sent before the first token (so the user sees it while the answer streams), and finalized from `meta`. It sits on the same line as the usage tag (`_attachUsageTag`, line 2166), to its left.

Format: `Auto · <Tier name> · <short reason>`. Clicking it opens "Why this model?".

**Reason copy (beginner wording, 60 chars or fewer for the short form).**

| Reason code | Badge | "Why this model?" text |
|---|---|---|
| `cat.chat` | Auto · Fast · quick chat | This was a short, simple message, so a fast model answered to save your allowance. |
| `cat.lookup` | Auto · Fast · quick question | This was a quick question with a short answer, so a fast model was enough. |
| `cat.writing` | Auto · Standard · writing task | Writing works well on the standard model and uses less of your allowance than the strongest one. |
| `cat.research` | Auto · Standard · explaining a topic | Explaining and comparing needs some thought, so the standard model answered. |
| `lock.security` | Auto · Strongest · about security | This touches passwords or security, so I always use the strongest model for it. |
| `lock.money` | Auto · Strongest · about money | This involves money, so I always use the strongest model for it. |
| `lock.action` | Auto · Strongest · changes something | This asks me to change, send or delete something, so I used the strongest model to be safe. |
| `lock.code_change` | Auto · Strongest · changing code | Changing code is easy to get subtly wrong, so the strongest model handled it. |
| `unclear.safe` | Auto · Strongest · playing it safe | I wasn't sure what this needs, so I used the strongest model rather than guess. |
| `triage.down` | Auto · Fast · quick check said simple | A quick check said this is simple, so a fast model answered. |
| `sticky` | Auto · Strongest · same conversation | We're in the middle of a task, so I kept the same model so it doesn't lose track. |
| `user.request` | Auto · Strongest · you asked | You asked for the strongest model in your message. |
| `escalated` | Auto · Strongest · retried | The first answer looked unsure, so I tried again with a stronger model. |
| `limit.cross` | Auto · Standard · Claude is busy | Claude has reached its usage limit until 2:40 AM, so ChatGPT's standard model answered. |
| `fallback_default` | Auto · your home model | No model at the right level was available, so your home model answered. |

**"Why this model?" panel** (expands under the reply): the long text above; then, for everyone: "Used: Standard model". With `show_model_names` on, or for power users who expand "Details": real model name (from `_shortModelName`), category, rule ids, triage result, skipped models with `why_not`, and the usage tag numbers. All copy is plain English; no product-internal or franchise vocabulary.

### 9.3 "Try a stronger model" (Redo)

- Shown under every Auto reply below Strongest. Hidden at Strongest (the panel says "This already used the strongest model.").
- Click: resends the same user message to `/chat_stream` with `provider: "auto"`, `min_tier: <next tier>`, and `redo_of: <turn_id>`. The bridge removes the previous user and assistant pair from the pane history before running, the old answer collapses ("Earlier answer, Fast model"), and the new one streams below it.
- Power users also get a small menu on the button: "Strongest" or a specific model.

### 9.4 Front-end touch points

- Main stream loop (`app.js` lines 1913 to 1935): add `else if (evType === 'route')`.
- Pane stream loop (around line 9739): same.
- New helpers next to `_attachUsageTag`: `_attachRouteBadge(msgEl, route)`, `_openRouteWhy(msgEl)`, `_redoStronger(paneKey, turnId)`.
- Settings: `loadRouterSettings()`, `saveRouterSettings(patch)`, rendered into the MODEL pane.

---

## 10. Data and logging

### 10.1 Ledger fields to add (`usage_ledger.jsonl`)

Every row (manual and Auto) gains these fields. Routing context travels on a thread-local `_route_ctx` set in `_run_turn_with_failover` and merged by `_log_usage` (line 8259).

| Field | Type | Meaning |
|---|---|---|
| `turn_id` | str | uuid4 hex, also sent in the `route` and `meta` events |
| `kind` | str | `"chat"`, `"triage"`, `"retry"`, later `"conductor_part"`, `"review"` |
| `route_mode` | str | `"manual"`, `"auto"`, `"redo"`, `"escalation"`, later `"conductor"` |
| `auto_chosen` | bool | true when Auto picked the provider |
| `requested` | str | what the client sent (`"auto"` or a provider id) |
| `chosen_provider` | str | provider id that ran |
| `chosen_tier` | str | `top`, `mid`, `light` |
| `min_tier` | str | floor after steps 3 to 7 |
| `category` | str | category that set the floor |
| `categories` | list | all matched categories |
| `rule_hits` | list | rule ids only, never matched text |
| `confidence` | str | `confident`, `weak`, `none` |
| `route_reason` | str | reason code (9.2 table) |
| `triage_used` | bool | |
| `triage_result` | obj or null | validated triage JSON |
| `sticky` | bool | stickiness raised or held the floor |
| `escalated` | bool | this row is a retry caused by escalation |
| `escalated_from` | str or null | tier of the first attempt |
| `escalation_reason` | str or null | `error`, `empty`, `unsure`, `needs_action` |
| `redo_of` | str or null | turn id the user asked to redo |
| `fallback` | str or null | `limit.cross`, `limit.up`, `fallback_default`, `failover.midturn` |
| `shadow_tier` | str or null | on manual turns: the tier Auto would have chosen (rules only, no triage) |
| `cache_warm` | bool | chosen model was the pane's previous model within the sticky window |
| `write_tools_used` | bool | any write or exec tool ran in the turn |
| `bias` | str | bias at decision time |
| `router_version`, `catalog_version` | str | for comparing tuning rounds |
| `decided_ms` | int | router time |

**Gap fixes (Task A4):**
- Ollama, Gemini and Grok runners call `_log_usage` with whatever counts they have (Ollama already has `in_tokens` and `out_tokens` at line 9453), `cost_usd: 0`.
- `_log_usage` writes routed rows even when all token counts are zero, so every decision is logged.
- No prompt text is ever written. `prompt_chars` stays as today.

### 10.2 Routing log view

`GET /router/log?limit=50` reads the tail of the ledger and returns only routing fields. The Settings "Routing log" renders it.

### 10.3 Shadow mode (free baseline)

On every **manual** turn, the router runs rules only (no triage, no model call, under 50 ms) and records `shadow_tier`. This builds the always-Opus baseline and the counterfactual distribution from the moment Task A4 lands, weeks before Auto is tuned.

---

## 11. Measurement: proving Auto saves usage versus always-Opus

### 11.1 Metrics

| Metric | Source | Use |
|---|---|---|
| **API value per turn** (`cost_usd`) | Claude `result` event via `_cli_usage_meta` | Primary proxy. Weights every token kind and model by list price, the best single gauge of window consumption (comment at line 8203). Not a charge. |
| **Window percentage used** | `provider_failover.snapshot()` meter, before and after | Sanity check over a day or a benchmark run. Noisy per turn. |
| **Fresh tokens and cache reads per turn** | ledger token fields | Explains why a saving did or did not appear (cache effects, F13) |
| **Codex and local share** | rows with `family` codex or ollama | Work moved to other allowances. Reported separately, never summed into Claude savings. |
| **Redo rate** | rows with `redo_of` / Auto turns | Quality proxy |
| **Escalation rate** | rows with `escalated` / Auto turns | Quality and cost proxy |
| **Triage overhead and downgrade rate** | `kind: "triage"` rows | Whether triage pays for itself (6.7) |
| **High-risk routing accuracy** | fixture labels | Safety gate |

### 11.2 Comparison methods

1. **Offline rules check (free, every commit).** The labelled prompt set (`tests/fixtures/auto_router_prompts.jsonl`, about 40 prompts in a realistic beginner mix: greetings, lookups, writing, research, personal, a few code, and at least 10 high-risk) is classified by `decide()` with no model calls. Asserts the expected floor per prompt.
2. **A/B replay benchmark (controlled, spends usage, ask first).** `tools/auto_benchmark.py` sends each prompt twice through the real bridge: arm A `provider: "claude-cli"` (always Opus), arm B `provider: "auto"`. Fresh pane per prompt, same settings, interleaved order (A, B, B, A, ...) so cache warmth is balanced across arms. Sum `cost_usd` per arm; report per category. Expected cost: about 80 turns, roughly what a busy hour of normal use costs. Produces a blind pair sheet (answers shuffled, arm labels hidden) for quality rating.
3. **Field measurement (two weeks, free).** Compare per-category median `cost_usd` of Auto turns against manual Opus turns, using shadow categories on the manual side. Report the estimated saving: for each Auto turn, the category median of Opus turns minus its actual cost. Confounded by workload mix, so it supports but does not replace method 2.

### 11.3 Acceptance thresholds (proposed; owner confirms in OQ 6)

| Gate | Threshold | If missed |
|---|---|---|
| Safety (hard) | 100% of high-risk fixture prompts route to Strongest, in unit tests and in the benchmark | Block release |
| Saving | Auto arm total `cost_usd` at most 75% of the Opus arm (25% or more saved) on the benchmark | 10 to 25%: ship, keep tuning. Under 10%: HOLD; Auto stays opt-in, not the default |
| Quality | Blind rating: Auto "worse" on at most 10% of non-high-risk prompts | Raise the floor of the worst category one tier and re-run |
| Redo rate (field) | at most 10% of Auto turns | Raise floors for the categories with the most redos |
| Escalation rate (field) | at most 15% of Auto turns | Same |
| Triage | under 5% of Auto cost, downgrade rate above 40% | Switch to local-only or turn triage off |
| Router latency | p95 under 50 ms rules-only, under 4 s with triage | Shrink lexicon, tighten triage timeout |
| Stickiness | zero automatic tier downgrades inside a sticky window | Bug |

---

## 12. Conductor mode (Phase C, separate and later)

### 12.1 What it is

"Several models work in the background and one answer comes back." A top-tier planner splits a task into parts, each part is routed to the best eligible model using the same tiers and categories, parts run in parallel as background jobs, and a top-tier model merges the results into one answer. A collapsed "Who did what" section lists the parts.

### 12.2 The honest analysis: when splitting saves and when it costs more

Splitting adds three overheads that one model doing the whole job does not pay:
1. **Repeated setup.** Every part is a separate model call that re-reads its own system prompt and the task context. With a fixed setup in the tens of thousands of tokens per CLI process (F13), each extra part starts with a real bill before it does any work.
2. **Planning.** A top-tier call to write the plan.
3. **Merging.** A top-tier call that **reads every part's output** and writes the final answer.

The design conversation estimated 1.5 to 3 times the total tokens of a single model. That estimate is plausible from the structure above and must be measured, not assumed.

**It saves Claude usage when** the bulk of the volume (long drafting, reading many files, broad searches) is done by a cheaper model, and the parts return **condensed** results, so the top-tier planner and merger read little and write little. In rough form: savings appear when (bulk volume moved to the cheaper model) times (price gap between tiers) exceeds (extra setups + plan + merge). Concretely:
- Long drafts (Standard drafts 3,000 words; Strongest reviews and edits). Output tokens are the expensive kind, and the review reads them as cheaper input.
- Wide searches (Fast or local models scan many files and return short findings).
- Work moved to **a different allowance** (Codex on ChatGPT, local Ollama). Total tokens may rise, but the Claude window lasts longer. This is a real saving for the user and must be reported separately from token totals.

**It costs more when:**
- The task is short. Overheads dominate; a single Auto choice is cheaper.
- The parts are tightly coupled (a code change across files). The merger has to re-read nearly everything to check it, so the cheap work is paid for twice.
- The review rewrites the draft. Then the top tier wrote the output anyway, plus the draft was wasted.
- The "review pass" is for quality (cross-checking by a second model). This always costs more; it buys quality, not savings, and the settings copy must say so.

**Already happening today.** Crew agents seeded with `CREW_MODELS` (line 968) let a top-tier lead delegate to Standard and Fast subagents inside one Claude Code turn; `_cli_usage_meta` already records every model used per turn. Task C0 measures these existing splits before any Conductor code is written.

### 12.3 Design

- **Gate.** Conductor runs only when the task is predicted large (length, ask count, explicit "do this thoroughly", or an explicit Conductor selection). Otherwise the turn goes to normal Auto.
- **Budget check.** Before running, estimate Conductor cost from ledger medians (plan + parts + merge) against the single-model estimate. If Conductor is not at least 10% cheaper on the Claude allowance, run single-model Auto, unless the user chose Conductor for quality (review pass).
- **Plan schema** (validated; at most 4 parts in v1):
```json
{"parts": [{"id": "p1", "category": "research", "instructions": "...", "needs": [], "min_tier": "mid", "returns": "bullet summary under 300 words"}]}
```
- **Routing per part.** The same `decide()` with the part's category; the floor is never below the part's `min_tier`; locked categories stay Strongest; parts stay read-only below Strongest.
- **Execution.** Parts with no unmet `needs` run in parallel as background jobs; each writes a ledger row with `kind: "conductor_part"` and a shared `conductor_id`.
- **Merge.** Always Strongest. The strongest model writes the final answer so it does not read like a committee wrote it.
- **First pattern to ship: Draft then review** (two fixed parts, no planner), behind the setting "Review pass: off / code only / everything" (default off). It is the simplest, matches the original idea (Standard drafts, Strongest reviews or security-checks), and is easy to measure.
- **Settings (reserved keys):** `conductor.enabled` (false), `conductor.review_pass` (`"off"`), `conductor.max_parts` (4), `conductor.allow_other_allowances` (true: Codex and local parts allowed).

### 12.4 Conductor acceptance

On a separate "big task" prompt set: Conductor's Claude `cost_usd` at most 85% of single-model Auto, quality not worse in blind rating, merged answer judged "reads as one voice". If not met, Conductor ships only as the opt-in review pass (a quality feature), labelled as using more allowance.

---

## 13. Paid slots (Phase D, present but disabled)

For Jev (TypeSafe AI's System One model, almost certainly a metered API) and future API keys. Nothing here runs until the owner explicitly enables it.

- **Slot schema** (in `auto_router.json` under `paid.<slot_id>`): `enabled` (false), `label`, `kind` (`"api"`), `role` (for Jev: `"trading_specialist"`), `monthly_cap_usd` (0, meaning off), `confirm_each_call` (true), `spent_this_month_usd` (tracked), `key_ref` (a reference to a key stored outside the settings file).
- **Key storage.** Keys never go in `auto_router.json`, the ledger, the DOM, logs, or URLs. Store in the OS credential store where available, else a user-only-readable file under `users/<id>/secrets/`. Write-only from the UI, like mail app passwords in `SPEC_connectors_page.md`.
- **Guard (all must pass before any paid call):** slot enabled; cap above zero and not reached; spend class `metered` acknowledged; per-call confirmation accepted if `confirm_each_call`; the pre-call estimate fits under the remaining cap. Any failure: the slot is skipped and the badge says so.
- **Ledger:** paid rows add `spend_usd_actual` and `paid_slot`. A hard stop disables the slot when the month's cap is reached.
- **Jev specifically:** used only as a Conductor part for market reads; the Strongest Claude model checks risk and makes the call; trading stays paper-only; outputs are advisory.
- **Settings UI:** "Paid models (off)" at the bottom of Advanced, collapsed, each slot showing "Off. Nothing is charged until you turn this on."
- **Test:** with a slot configured but disabled, 10,000 randomized decisions never select it. With it enabled and the cap at 0, never selected either.
- **Historical note:** the Anthropic API providers were removed deliberately to prevent accidental billing (comment in `PROVIDERS`). Paid slots must not silently re-create them.

---

## 14. Private dashboard port notes

The owner's private dashboard build carries a near-identical bridge with a different line layout (the usage tags were ported "by hand" between the two in the design conversation for that reason). Plan:

1. **Port the module verbatim.** `auto_router.py`, `router_catalog.json`, and `tests/test_auto_router.py` copy unchanged. The router takes all bridge dependencies by injection, so it has no build-specific imports.
2. **Copy strings are a dict.** All badge and settings copy lives in `auto_router.COPY`. The private build may override entries (for example to name crew members) through a small `COPY_OVERRIDES` in its own bridge. Retail keeps the neutral defaults.
3. **Bridge hooks by anchored replacement.** The hook points are named functions, not line numbers: `_run_turn_with_failover`, `_log_usage`, `_cli_usage_meta`, the `/chat_stream` request block, `_provider_runner`, `_mail_llm` (template for triage). Apply each with an anchored edit that fails loudly if the anchor is missing, as done for the usage-tag port.
4. **Front-end class names differ.** Retail uses `data-btn` and `data-btn-sm`; the private build uses its own button class family. Settings markup must use the build's classes (see the cross-build table in `SPEC_connectors_page.md`).
5. **User directory.** Both builds use `users/<id>/`; the private build's single user id differs. Use `_user_dir()` everywhere; never hard-code a user id.
6. **Crew agent files.** The private build has its own agent files with models already set (design conversation, 09:37). Auto does not touch them.
7. **Mail pipeline tiers.** The private build's mail path already uses Fast for summaries and Standard for replies. Leave it alone (non-goal).
8. **Port timing.** After the Phase A checkpoint passes in retail, port Phase A, then run Phase B measurement in **both** builds; the owner's heavier usage gives faster data.

---

## 15. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Keyword rules misread a prompt ("quick question about my password code") and send hard or risky work to a small model | High | High | Locked categories checked first; unclear goes to Strongest; triage only for unclear; below-top tiers read-only (6.9); escalation; Redo; fixture tests with adversarial phrasings |
| R2 | A small model takes a harmful action | Medium today (F6) | High | Read-only rule below top (6.9); action lexicon locked; `needs_action` marker escalates |
| R3 | Switching down to a cold cheap model costs more than staying warm (F13) | High for short chat | Medium | Stickiness; warm-cache tie-break; Phase B switch-cost estimator; benchmark measures it directly |
| R4 | Triage overhead exceeds its savings | Medium | Low to medium | Local-first triage, cache, own ledger rows, budget gate in 6.7 |
| R5 | A family limit blocks all tiers at once (F5) | Medium | Medium | Cross-family fallback (6.10); headroom tie-break; existing failover unchanged |
| R6 | Metered spend by accident (API-key Codex, billed Gemini project, cloud Ollama tag, remote catalog tampering) | Low | High | Code-owned spend classes; `_codex_auth_mode`; Gemini and Grok excluded by default; `is_cloud_model`; catalog cannot change spend class; 100% window treated as blocked |
| R7 | A top-tier lead spawns Strongest subagents on a light turn, or a light turn spawns them | Low | Low | `_cli_usage_meta` already records all models; read-only below top limits spawning that writes; report flags multi-model light turns |
| R8 | Escalation doubles cost on many turns | Medium | Medium | One retry max; triggers are narrow; escalation rate gate (15%) |
| R9 | Beginners never notice the badge or Redo | Medium | Medium | Badge sits with the existing usage tag, first item, plain words; Redo visible without hovering; quality relies on conservative defaults, not on the user |
| R10 | "auto" leaks into code that expects a real provider id | Medium | Medium | Never stored in `ACTIVE_PROVIDER`; resolved in one place; any unresolved "auto" reaching `_provider_runner` falls through to its default (`ask_hermes_cli_stream`, Strongest Claude), which is the conservative outcome; unit test for each caller |
| R11 | Savings claims without proof | Medium | Medium (trust) | No percentage is shown to users until the Phase B benchmark passes; the report states method and sample size |
| R12 | Copy leaks franchise or internal vocabulary into retail | Low | Medium | `COPY` dict reviewed; a test greps new strings for a banned-word list; em dash check |

---

## 16. Open questions for the owner

1. **Fable tier.** `claude-cli-fable` exists but its place is unknown to this spec. Rate it Strongest, Standard, or leave it out of Auto? (Recommend: out until rated after a short side-by-side.)
2. **Gemini and Grok in the Auto pool.** Gemini's free tier and the SuperGrok subscription cost nothing extra today but are outside the stated pool, and Gemini can be pointed at a billed project. Keep them out by default? (Recommend: out by default, user can enable in Advanced.)
3. **Read-only below the top tier (Section 6.9).** Accept that Fast and Standard turns cannot edit files, send, or run commands, and escalate to Strongest when they need to? (Recommend: yes. It is the main safety guarantee for beginners.)
4. **Existing installs.** On upgrade, keep Manual and show a one-time "Try Auto" tip, or switch everyone to Auto? (Recommend: tip only.)
5. **Tier names.** "Fast / Standard / Strongest" in the badge, keeping "Balanced" for the bias setting only. Acceptable?
6. **Acceptance thresholds.** At least 25% saving on the benchmark, at most 10% quality regressions, Redo at most 10%, escalation at most 15%. Confirm or adjust.
7. **Health and legal.** Default Strongest but adjustable (as specified), or locked like money and security?
8. **Benchmark approval.** The A/B benchmark spends about 80 turns of subscription usage once. Approve when Phase B starts? And who rates the blind pairs: the owner, a beginner tester, or both? (Recommend both; the beginner's ratings matter most.)
9. **Private content on local models.** Add "Keep email and personal content on local models" in Phase B, or drop it? It costs quality on small machines.
10. **Spreading load across allowances.** Should Auto prefer Codex proactively when the Claude window is high (for example above 80%), or only when Claude is actually limited? (Recommend: only on limits in Phase A; headroom-aware routing as an opt-in Phase B task.)
11. **Jev.** Confirm Jev is a metered API and whether Phase D is wanted before there is revenue to fund it.

### 16.1 Owner decisions (2026-11-23)

The owner does not want to pick models or rate answers. The builder decides; these answers are final unless the owner reverses one.

- **OQ 1, 2, 3, 4, 5, 10:** the recommendations above are accepted as written.
- **OQ 6:** thresholds accepted, except the quality gate (see OQ 8).
- **OQ 7:** health and legal stay Strongest by default and adjustable.
- **OQ 8:** no human rating. The blind pair sheet is scored by an automatic judge (Strongest model, answers shuffled, arm labels hidden). Field Redo rate is the second quality signal. The benchmark spends subscription usage only and runs once Phase A is done.
- **OQ 9, 11:** deferred. Not needed for Phase A.
- **Owner's own dashboard:** once A7 works and A6 is proven, Auto becomes the owner's default in the private build. Manual choice stays available.
- **Shortest path to a working Auto:** A3, A5, A6, A7, A8, A9. A2 (catalog file), A10 (triage, off means unclear prompts go to Strongest) and A11 to A14 follow after.

---

## 17. Task plan

Each task is small, leaves the system working, and has a verification step. Manual mode must behave exactly as today after every task.

### Phase A: Auto, badge, settings, catalog

**Task A1: Router core (pure module).**
- **Description:** Create `auto_router.py` with tier ids, category ids, `RULES` (lexicon as data), the bias table, lock set, `Decision` dataclass, and `decide(ctx)` implementing 6.4 steps 1 to 6 and 6.6 against an injected `candidates()` list. No bridge imports. Include `COPY` (9.2 table).
- **Acceptance:** Locked-category prompts always return `top`; unclear with triage disabled returns `top`; bias changes only non-locked rows; settings floors cannot lower locked rows; reason tie-break follows 6.4.
- **Verification:** `python -m unittest tests.test_auto_router -v` (table-driven, at least 60 cases).
- **Dependencies:** None. **Files:** `dashboard/auto_router.py`, `dashboard/tests/test_auto_router.py`. **Scope:** Medium.

**Task A2: Router catalog file and loader.**
- **Description:** Add `router_catalog.json` (bundled), a validator in `auto_router.py` mirroring `model_refresh.validate_catalog`, `match` resolution for `claude:<alias>`, `codex:<tier>`, `ollama:<pattern>`, and user `model_overrides`. Generalize `model_refresh.fetch_remote_catalog` and `choose_catalog` to take a URL, cache path and validator so `_run_model_refresh()` refreshes both files.
- **Acceptance:** Unrated models resolve to no tier; overrides beat the catalog; a remote file with a bad entry drops only that entry; a file with fewer than 3 good entries is rejected; the existing Ollama download list still loads.
- **Verification:** `python -m unittest tests.test_auto_router tests.test_model_refresh -v`.
- **Dependencies:** A1. **Files:** `router_catalog.json`, `auto_router.py`, `model_refresh.py`, `bridge_server.py` (refresh call), tests. **Scope:** Medium.

**Task A3: Candidate availability.**
- **Description:** Add `SPEND_CLASS`, `_codex_auth_mode()`, `provider_failover.blocked_reason(fam)`, and `_auto_candidates()` per 7.3, including `why_not` for ineligible entries and `headroom_pct` from `snapshot()`.
- **Acceptance:** API-key-only Codex is ineligible; a limited or 100%-window family is ineligible; Gemini, Grok, pinned and `codex-mini` are ineligible by default; cloud Ollama tags are ineligible.
- **Verification:** unit tests with mocked `_brain_usable`, auth file fixtures, and failover state; `python -m unittest tests.test_auto_router tests.test_provider_failover tests.test_brain_selection -v`.
- **Dependencies:** A2. **Files:** `bridge_server.py`, `provider_failover.py`, `tests/test_auto_router.py`. **Scope:** Medium.

### Checkpoint: after A1 to A3
- [ ] All router, failover, refresh and brain-selection tests pass.
- [ ] Bridge starts; Manual chat unchanged.

**Task A4: Ledger routing fields, gap fixes, and shadow mode.**
- **Description:** Add `_route_ctx` thread-local and `turn_id`; merge 10.1 fields in `_log_usage`; log routed rows even at zero tokens; add `_log_usage` to the Ollama, Gemini and Grok runners; on manual turns run `decide()` rules-only and record `shadow_tier`. Add `GET /router/log`.
- **Acceptance:** A manual Claude turn writes one row with `route_mode: "manual"`, `auto_chosen: false`, and a `shadow_tier`; an Ollama turn now writes a row; no row contains prompt text.
- **Verification:** run one manual turn on Claude and one on Ollama; inspect `users/<id>/usage_ledger.jsonl`; `curl /router/log?limit=5`.
- **Dependencies:** A1. **Files:** `bridge_server.py`. **Scope:** Medium.
- **Note:** land this early. Shadow data starts the baseline while the rest of Phase A is built.

**Task A5: Settings store and endpoints.**
- **Description:** `users/<id>/auto_router.json` with defaults from 8.2; `GET` and `POST /router/settings` with strict validation; atomic write.
- **Acceptance:** Invalid values rejected with a clear error; lowering a locked category rejected; settings survive restart; the GET response includes candidates and catalog version.
- **Verification:** unit tests on the validator; curl GET and POST; restart and GET again.
- **Dependencies:** A3. **Files:** `bridge_server.py`, `auto_router.py`, tests. **Scope:** Small to medium.

**Task A6: Read-only enforcement below the top tier (high risk, early).**
- **Description:** Add a per-turn `read_only` flag honoured by the Claude runner (tool deny flags), the Codex runner (read-only sandbox instead of the bypass flag), and the Ollama runner (filtered tool list). Confirm the exact CLI flags with `claude --help` and `codex exec --help` first; if a runner cannot enforce it, mark that model ineligible for non-text categories in `_auto_candidates()`.
- **Acceptance:** With `read_only` on, a turn asked to create `C:\temp\auto_test.txt` does not create it on Claude Haiku, Codex Luna, or a local model; with it off (Manual), behaviour is unchanged.
- **Verification:** one forced turn per runner via a test-only override; check the file does not exist; existing suites pass.
- **Dependencies:** A3. **Files:** `bridge_server.py`. **Scope:** Medium.

### Checkpoint: after A4 to A6
- [ ] Ledger rows carry routing fields; shadow tiers accumulating.
- [ ] Read-only proven on every runner Auto may use.
- [ ] All tests pass; Manual unchanged.

**Task A7: Dispatch hook for "auto".**
- **Description:** Accept `"auto"` in `/chat_stream` validation and treat `provider: ""` as `"auto"` when `mode` is `"auto"`; route the attachment gate through the router (vision restricts to Claude); in `_run_turn_with_failover`, call `decide()`, emit the `route` SSE event, set `_provider_override`, clamp effort with `effective_effort`, set `read_only` for below-top tiers, then call `_failover.run_turn` with the resolved id and record any fallback. Non-chat callers passing "auto" resolve to Strongest. Support `min_tier` and `redo_of` request fields (history pair removal for redo).
- **Acceptance:** curl with `provider: "auto"` and "hi" emits `route` with tier `light`; "delete my old invoices" emits `top` with `lock.action`; a redo with `min_tier: "mid"` never returns `light`; Claude marked limited makes a Standard request run on Codex Sol.
- **Verification:** smoke curls from Section 3; inspect ledger rows; `python -m unittest discover -s tests -t . -v`.
- **Dependencies:** A4, A5, A6. **Files:** `bridge_server.py`. **Scope:** Medium.

**Task A8: Selector entry.**
- **Description:** Add `Auto (recommended)` first in the main select, pane selects, and header menu, with real models in a "Choose a model yourself" group; `setMainProvider('auto')` posts `mode` instead of `/provider`; panes store `ws.provider = "auto"`.
- **Acceptance:** Choosing Auto in the main chat and in a project pane sends `"auto"` (or empty with mode auto); choosing a real model returns to Manual for that pane.
- **Verification:** browser check; watch the request bodies in DevTools.
- **Dependencies:** A7. **Files:** `app.js`. **Scope:** Small.

**Task A9: Badge, "Why this model?", and Redo.**
- **Description:** Handle `route` in both stream loops; `_attachRouteBadge`, `_openRouteWhy`, `_redoStronger`; collapsed earlier answer; styles.
- **Acceptance:** Every Auto reply shows a badge with tier and reason from `COPY`; Why panel lists details (model names only when enabled); Redo re-answers one tier up and the ledger shows `redo_of`; no badge on Manual replies.
- **Verification:** browser check across chat, lookup, writing, and a locked prompt; ledger inspection.
- **Dependencies:** A7, A8. **Files:** `app.js`, `usage_meter.css` (or `theme.css`). **Scope:** Medium.

### Checkpoint: after A7 to A9
- [ ] End-to-end Auto works from the UI in the main chat and a project pane.
- [ ] Badge, Why panel and Redo work; ledger complete.
- [ ] All tests pass.

**Task A10: Triage call.**
- **Description:** `_router_triage()` per 6.7: local-first, Claude Haiku fallback with JSON output and tools disabled (confirm the flag; if unavailable, Claude triage is not used), strict schema, timeout, 10-minute cache, own ledger row.
- **Acceptance:** Malformed or timed-out triage yields Strongest; triage never lowers below rule floors; a triage row appears with its own cost; no tools can run during triage.
- **Verification:** unit tests with stub backends; one live vague prompt; inspect the triage ledger row.
- **Dependencies:** A7. **Files:** `bridge_server.py`, `auto_router.py`, tests. **Scope:** Medium.

**Task A11: Escalation.**
- **Description:** Below-top directive and markers; marker stripping in `_marker_filter_sse`; one retry one tier up; history pair removal; collapsed first answer; skip when write tools ran.
- **Acceptance:** A stub runner returning `<<auto:unsure>>` triggers exactly one retry at the next tier, the marker never shows, the ledger shows `escalated: true` with `escalated_from`.
- **Verification:** unit tests with stub runners; one live check.
- **Dependencies:** A7, A9. **Files:** `bridge_server.py`, `auto_router.py`, `app.js`, tests. **Scope:** Medium.

**Task A12: Settings panel UI.**
- **Description:** "Auto model choice" section at the top of the MODEL pane: simple view (mode, bias) and collapsed Advanced (8.2), routing log, Check for new models.
- **Acceptance:** Every control round-trips through `/router/settings`; locked rows are greyed with "Always Strongest"; the models list shows sign-in state and `why_not`.
- **Verification:** browser check; restart bridge and confirm values persist.
- **Dependencies:** A5, A9. **Files:** `index.html`, `app.js`, styles. **Scope:** Medium.

**Task A13: New-install default and upgrade tip.**
- **Description:** `mode = "auto"` when neither `auto_router.json` nor an explicit provider choice exists; on upgrade, keep Manual and show a dismissible one-time tip (pending OQ 4).
- **Acceptance:** A clean user directory starts in Auto; an existing user stays Manual and sees the tip once.
- **Verification:** unit test with temp user dirs; browser check.
- **Dependencies:** A12. **Files:** `bridge_server.py`, `app.js`. **Scope:** Small.

**Task A14: Copy and character audit.**
- **Description:** Test that greps `auto_router.COPY` and new UI strings for a banned-word list and for the em dash and en dash characters.
- **Acceptance:** Test passes; list maintained in the test file.
- **Verification:** `python -m unittest tests.test_auto_router -v`.
- **Dependencies:** A9, A12. **Files:** `tests/test_auto_router.py`. **Scope:** Small.

### Checkpoint: Phase A exit
- [ ] Section 3 success criteria 1 to 7 all met.
- [ ] Security review of A6, A7, A10 (read-only enforcement, request validation, triage injection handling) by the sentinel reviewer.
- [ ] Code review sign-off by the vector reviewer.

### Phase B: Measurement and tuning

**Task B1: Ledger report.** `tools/auto_report.py` (stdlib): per-category and per-tier medians, Redo, escalation, triage overhead and downgrade rate, shadow counterfactual saving, Codex and local share reported separately. *Verify:* run on a fixture ledger with known answers. *Deps:* A4. *Files:* `tools/auto_report.py`, `tests/test_auto_report.py`. *Scope:* Medium.

**Task B2: Labelled prompt set and offline check.** About 40 prompts with expected floor and a high-risk flag, plus adversarial phrasings. *Verify:* unit test asserts 100% high-risk accuracy. *Deps:* A1. *Files:* `tests/fixtures/auto_router_prompts.jsonl`, `tests/test_auto_router.py`. *Scope:* Small.

**Task B3: A/B benchmark harness.** `tools/auto_benchmark.py`: interleaved arms, fresh pane per prompt, per-arm `cost_usd` sums, meter before and after, blind pair sheet. **Ask first before running.** *Verify:* dry-run mode with a stub bridge. *Deps:* A7, B2. *Files:* `tools/auto_benchmark.py`. *Scope:* Medium.

### Checkpoint: after B1 to B3
- [ ] Report runs on real ledger data.
- [ ] Owner approves the benchmark spend (OQ 8).

**Task B4: Run the benchmark and rate.** Run, collect blind ratings, compare against 11.3. *Verify:* report shows each gate pass or fail. *Deps:* B3. *Scope:* Small (mostly waiting).

**Task B5: Tune.** Adjust lexicon and floors from B4 and two weeks of field data; replace the fixed sticky window with a switch-cost estimate from ledger cache-read versus cache-write data; optional headroom-aware bias (OQ 10). Bump `router_version`. *Verify:* re-run B2 offline and a reduced B4. *Deps:* B4. *Files:* `auto_router.py`, `router_catalog.json`. *Scope:* Medium.

**Task B6 (optional, OQ 9): Keep personal content local.** *Deps:* B5. *Scope:* Small to medium.

**Gate to make Auto the retail default:** 11.3 thresholds met. Otherwise Auto stays opt-in.

### Phase C: Conductor (gated on Phase B results)

**Task C0: Measure existing crew splits.** From ledger rows with more than one model, report what top-tier leads delegating to Standard and Fast subagents actually cost. *Verify:* report section. *Deps:* B1. *Scope:* Small.

**Task C1: Draft then review.** Two fixed parts behind `conductor.review_pass` (default off), honest cost note in settings. *Verify:* ledger shows both parts under one `conductor_id`; benchmark on long drafts. *Deps:* C0. *Scope:* Medium.

**Task C2: Planner schema and validator.** At most 4 parts, strict JSON, routed per part by `decide()`. *Verify:* unit tests with stub planner output, including malicious plans. *Deps:* C1. *Scope:* Medium.

### Checkpoint: after C0 to C2
- [ ] Draft then review measured; validator rejects bad plans.

**Task C3: Parallel parts as background jobs** with per-part ledger rows. *Deps:* C2. *Scope:* Medium.

**Task C4: Merge on Strongest and "Who did what".** *Deps:* C3. *Scope:* Medium.

**Task C5: Budget gate and fallback to single-model Auto.** *Deps:* C4. *Scope:* Small.

**Task C6: Conductor benchmark against 12.4.** Ask first. *Deps:* C5. *Scope:* Small.

### Phase D: Paid slots (disabled by default; gated on OQ 11)

**Task D1: Slot schema, spend class, and Settings section (off).** *Verify:* UI shows slots as off; 10,000 randomized decisions never select a disabled slot. *Deps:* A5. *Scope:* Medium.

**Task D2: Key storage and spend guard** (cap, confirm, hard stop, `spend_usd_actual`). *Verify:* unit tests for every guard branch. *Deps:* D1. *Scope:* Medium.

**Task D3: Jev slot as a Conductor part** (paper trading only, advisory). *Deps:* D2, C4. *Scope:* Medium.

**Task D4: Security review** of key handling and the guard by the sentinel reviewer. *Deps:* D2. *Scope:* Small.

---

## 18. Order of work

Start with the pure router (A1) and the ledger with shadow mode (A4) in parallel. A4 is the earliest valuable thing in this plan: from the day it lands, every manual turn records what Auto *would* have chosen, so the baseline builds itself at zero cost while the rest is built. Risk concentrates in two places: **read-only enforcement (A6)**, because today every runner can act with permissions bypassed and a beginner cannot catch a mistaken action, and **the dispatch hook (A7)**, because "auto" must never leak into the 44 places that read `ACTIVE_PROVIDER`. Do A6 before any user can select Auto. The task that proves the design soonest is A7's smoke test: "hi" lands on Fast, "delete my old invoices" lands on Strongest with a locked reason, and both rows appear in the ledger. Phase B decides whether Auto becomes the default; Phase C is built only if the numbers say splitting pays.
