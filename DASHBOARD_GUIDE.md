# Using DATA Daemon — The Full Guide

This guide picks up where the [README](README.md) leaves off. The README tells you
what DATA *is* and how to install it. This one tells you how to *drive* it — how to
talk to the dashboard, customize it by conversation, and have it rewrite its own
source so the tool reshapes itself around the way you work.

The core idea: **you don't configure DATA through menus. You talk to it.** The main
channel can read and edit every file the dashboard is built from, so almost anything
you can describe, it can change about itself.

DATA ships **fully functional as-is** — install it and it works, no further setup
required. But it's also a **baseplate**: every screen, control, and capability is
plain source the agent can rewrite, so you can grow it into whatever you need. Use it
out of the box, or build on top of it. Both are first-class.

---

## 1. Orientation — the layout

When the dashboard opens at **http://localhost:7777** you're looking at a handful of
views. You move between them from the left rail / view switcher.

| View | What it's for |
|------|---------------|
| **Main channel** | Your primary working chat with the active agent. Markdown, streaming, images, clickable file links. This is where you spend 90% of your time. |
| **Project workspaces** | Parallel chat panes, each rooted in a folder, each with its own provider and role. Spin several up to work a problem from multiple angles at once. |
| **Memory banks** | Persistent per-user memory plus a searchable archive of every conversation across every pane. |
| **Standing orders** | Cron-scheduled AI tasks — briefings, checks, refreshes that fire on their own. |
| **AI Connectors** | Pick the "brains." Scans your hardware, recommends and installs local models, and connects cloud providers (Claude / Codex / Gemini). Whatever you connect here is what the model menu offers. |
| **Neural / Positronic Matrix** | A live node-graph of the system: skills, memory nodes, crew. The Memory Banks are a hub inside it. |
| **System vitals** | Engine gauge and subsystem bars driven by real CPU / RAM / GPU metrics. |

**Panel header controls** (top of the main channel):
- **Agent dropdown** — pick which crew member answers on the main channel.
- **Model menu** — pick which AI answers on the main channel. It lists **only** the models you've connected on the **AI Connectors** page (§7), so there are never any dead options. Switch per conversation, anytime.
- **Theme toggle** (footer, bottom-left) — switch between the shipped themes.

---

## 2. Talking to the main channel

The main channel is a full working agent, not a Q&A box. Three things follow from that:

- **It acts, it doesn't just answer.** Ask it to read a file, run a command, search
  the web, edit code, post a video — it does the work and reports back. You rarely
  need to grant permission step by step.
- **It has real tools.** Filesystem, terminal, browser control, your own screen and
  keyboard, scheduled jobs, and any installed skills or MCP servers.
- **It remembers.** Within a pane it sees recent turns directly; everything older is
  searchable from the Memory Banks (see §6).

Good prompts are specific about the *outcome*, not the steps. "Pull this week's open
invoices into a spreadsheet sorted by due date" beats "open the invoice folder."

---

## 3. Customizing the dashboard by conversation

You change how DATA looks and behaves by **asking for it in plain language**. The
agent edits the underlying files and the change shows up on the next reload (UI) or
the next bridge restart (server behavior). Examples that work today:

**Look & feel**
- "Make the accent color a warmer amber instead of blue."
- "Tighten the spacing in the chat pane — it's too airy on my screen."
- "Add a third theme called MIDNIGHT — deep indigo surfaces, soft cyan accent."
- "Move the theme toggle to the top bar."

**Layout & behavior**
- "Add a clock to the panel header showing my local time."
- "When a message contains a localhost URL, render it as a clickable button."
- "Default the provider dropdown to Claude every time the dashboard loads."
- "Add a keyboard shortcut to jump to the Memory Banks."

**Sounds, copy, polish**
- "Play the confirm sound when a standing order fires."
- "Rename the 'Neural Matrix' view to 'Star Map' everywhere it appears."

For any of these, just describe what you want. The agent will find the right file,
make the edit, and tell you what to reload or restart.

---

## 4. Self-reprogramming — DATA editing its own source

This is the part that makes DATA different from a normal app: **it is built from
files it can read and write.** The whole dashboard lives here:

```
DATA/dashboard/
├── bridge_server.py   # the brain — HTTP server, AI dispatch, every tool & endpoint
├── index.html         # the dashboard markup / structure
├── app.js             # dashboard logic — views, controls, rendering, shortcuts
├── theme.css          # all themes, colors, spacing, typography
└── assets/  sounds/   # images and audio cues
```

Plus the agent's own behavior:

```
SOUL files               # the agent's personality & operating instructions
users/<you>/MEMORY       # your persistent memory the agent reads every request
standing_orders.json     # the scheduled tasks
```

### How the loop works

1. **You describe the change** in the main channel.
2. **The agent reads the relevant file**, makes a precise edit, and explains it.
3. **You apply it:**
   - Front-end changes (`index.html`, `app.js`, `theme.css`) — just **refresh the
     browser tab**.
   - Back-end changes (`bridge_server.py`, new tools, new endpoints, new crew
     wiring) — **restart the bridge** (relaunch DATA, or restart the bridge process).
4. **You verify**, and iterate. "Almost — make the border one shade lighter."

### Asking well — three tiers

- **Tweak:** "Change X to Y." Fast, low-risk, usually a one-line edit.
- **Feature:** "Add a button that exports the current chat to Markdown." The agent
  may touch two or three files. Ask it to walk you through what it changed.
- **New capability / tool:** "Give yourself a tool to read my Obsidian vault." This
  adds a new endpoint or skill to the bridge — a real code change. For anything at
  this level, it's worth asking the agent to **explain the plan before it edits**,
  and to keep the change isolated so it's easy to back out.

### Safety rails (so self-editing stays safe)

- **The repo is under git.** Before a big self-modification, ask the agent to commit
  the current state first ("snapshot before you start"), so any change is one
  `git revert` away. There are also dated copies in `Backups/`.
- **Restart, don't guess.** If the dashboard behaves oddly after a back-end edit,
  the first move is almost always "restart the bridge" — server code is only loaded
  at startup.
- **One change at a time** when you're modifying the brain. It's much easier to find
  what broke if each restart corresponds to one intent.
- **Ask for the diff.** "Show me exactly what you changed" is always fair, and a good
  habit for anything touching `bridge_server.py`.

---

## 5. Power commands — driving DATA, not just chatting

Some phrasings trigger structured actions rather than a normal reply. You don't need
to memorize syntax — natural language triggers them — but knowing they exist helps
you ask for the right thing.

| Say something like… | What happens |
|---------------------|--------------|
| "Spin up a workspace in `<folder>`" / "open three panes for this project" | New project chat pane(s), each rooted in a folder with its own provider and role. |
| "Re-root this chat to `<folder>`" / "switch this pane to my blog repo" | The **current** pane's working directory changes — every later command runs from there. (A *new* window is a workspace, above.) |
| "Schedule a daily briefing at 8am" / "remind yourself to check the deploy every 15 min" | A standing order is created and starts firing on its cron schedule. |
| "Search your memory banks for…" / "check your archive" / "what did we decide about X" | Searches the full conversation history (this pane, or all panes). |
| "Look at my screen and…" / "click the publish button" | Computer use — the agent screenshots, then drives mouse/keyboard to do the task. |
| "Upload this to YouTube" / "pin this to my board" | Direct integrations fire (when those accounts are authorized). |

---

## 6. Memory — teaching DATA who you are

DATA has two kinds of memory, and you control both by talking to it.

- **Persistent memory** (`users/<you>/…`) — facts, preferences, project context the
  agent loads on *every* request. This is how it knows your projects, your folders,
  your standing rules. Tell it **"remember that…"** and it saves the fact. Ask
  **"what do you have on file about my projects?"** to see what it knows. Ask it to
  **"forget…"** or **"update…"** to edit.
- **Conversation archive** — every turn from every pane, searchable. You don't manage
  this; you query it ("search your history for…").

The more you teach persistent memory, the less you repeat yourself. Good things to
store: folder locations, naming conventions, recurring people, hard rules ("never do
X"), and decisions you don't want re-litigated.

---

## 7. Growing capabilities — skills, providers, integrations

- **Skills** are installable capability packs the agent discovers automatically — no
  restart needed. Ask **"find me a skill for X"** or **"what skills can you use?"**
  New skills drop into the skills folders and become available on the next request.
- **Models & providers** are managed on the **AI Connectors** page (next section). You
  connect a brain there once, and it's swappable per conversation from the model menu.
  Different panes can even run different models side by side — useful for getting a
  second model's take on the same problem.
- **Integrations & MCP servers** extend what the agent can touch (calendars, email,
  design tools, deploy platforms). Ask the agent to wire one up; it knows how to
  register them.

If something you want doesn't exist yet, that's a §4 conversation: ask DATA to build
itself the capability.

---

## 8. The AI Connectors page — choosing your brains

DATA needs at least one AI "brain" to talk to. The **AI Connectors** page (left
navigation → **AI CONNECTORS**) is where you connect them. Whatever you connect here
is exactly what the **model menu** on the main channel offers — connect a model and it
appears; remove it and it's gone. No config files, no restart.

The page reads top to bottom:

1. **THIS MACHINE** — a live readout of your computer: CPU, RAM, graphics card/VRAM,
   and whether **Ollama** (the local-model engine) is installed. Everything below is
   sized against these numbers.
2. **RECOMMENDED LOCAL MODEL** — the largest local model that will run comfortably on
   your machine, flagged with a ★. The safe "just works, no fees, fully private"
   choice. Click **Install** and a progress bar streams the download; when it finishes
   the model is live in the menu — no restart.
3. **LOCAL MODELS** *(via Ollama — free, runs on your machine)* — the full catalog.
   Each card shows whether it **fits** your hardware and an install button. Bigger
   models need more RAM/VRAM; the page won't let you in over your head.
4. **CLI PROVIDERS** *(cloud models via your subscriptions)* — connect top-tier cloud
   models through plans you already pay for, with no per-message billing:
   - 🟢 **Available** — installed and signed in; its models are already in your menu.
   - ⚪ **Not ready** — the card shows the exact install command and the one-time
     sign-in step. Run them, then **RESCAN**.

| Provider | Models it adds | One-time setup |
|----------|----------------|----------------|
| **Claude Code** | Opus · Sonnet · Haiku · Fable | install Claude Code, run `claude`, sign in |
| **Codex** (OpenAI) | GPT-5 Codex | `npm i -g @openai/codex`, then `codex login` |
| **Gemini CLI** (Google) | Gemini 2.5 Pro | `npm i -g @google/gemini-cli`, then sign in |

**RESCAN** (top-right) re-reads your hardware and re-checks what's installed — hit it
after you install a model, sign in to a provider, or upgrade the machine. Sign-ins
always happen in a real terminal/browser window; **you never paste a password into the
dashboard.**

> **Quick start:** install the **★ recommended** local model for a private, no-cost
> brain, *and/or* connect one CLI provider for heavyweight thinking. Either one alone
> makes the main channel work. Then open **Communications** and pick your model from
> the menu.

---

## 9. A working phrasebook

Copy-paste starting points. Replace the specifics with yours.

```
Customize the look
  "Add a MIDNIGHT theme: indigo surfaces, soft cyan accent, and make it the default."
  "The chat font is too small — bump it one step and tighten line height."

Change behavior
  "Always start on the Claude provider and the Main channel when DATA loads."
  "Add a header button that exports the current conversation to a Markdown file."

Give DATA a new ability
  "Build yourself a tool to read and search my Obsidian vault at <path>."
  "Add an endpoint that returns today's standing-order run log."

Run the place
  "Spin up two workspaces in <repo> — one to write the feature, one to review it."
  "Schedule a market briefing every weekday at 7:30am."
  "Remember: my workday is 10–5 Pacific, hard stop."

Stay safe while self-editing
  "Commit the current state first, then make the change."
  "Show me the exact diff before you touch bridge_server.py."
  "That broke something — revert your last change and restart the bridge."
```

---

### The one rule worth keeping

When in doubt, **describe the outcome you want and ask DATA to make it so.** It can
read its own code, change it, and tell you how to reload. The dashboard is meant to
be reshaped by conversation — that's the whole point.
