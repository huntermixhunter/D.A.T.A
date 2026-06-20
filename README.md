# DATA — Dashboard for Analytical Thought and Action

A self-hosted, local-first AI operations dashboard. DATA gives you a mission-control
interface for working with AI: a main chat channel wired to your AI provider, a crew
of ten specialist agents, persistent per-user memory, project workspaces, standing
orders (cron-scheduled AI tasks), and live system vitals.

Two dark themes ship in the box — toggle from the bottom-left of the footer:

- **MINIMAL** (default) — professional, utilitarian, slightly futuristic. Near-black
  surfaces, hairline borders, one electric-blue accent, built for long sessions.
- **CYBER** — neon cyan/magenta, chamfered edges, scanlines.

Everything runs on your machine. No accounts, no telemetry, no cloud.

![themes](https://img.shields.io/badge/themes-MINIMAL%20%7C%20CYBER-4d9fff) ![python](https://img.shields.io/badge/python-3.10%2B-blue) ![license](https://img.shields.io/badge/license-proprietary-orange)

---

## The Crew

| Agent | Role |
|-------|------|
| **DATA** | The main computer — your primary working channel |
| **Atlas** | Strategy & planning — turns vague ideas into structured plans |
| **Forge** | Builder — implements code, configs, automations |
| **Vector** | Reviewer — evaluates work before it ships |
| **Sentinel** | Security — threats, vulnerabilities, hardening |
| **Probe** | Test & debug — isolates faults to root cause |
| **Relay** | Operations — deployment, infrastructure, uptime |
| **Sage** | Advisor — second opinions and the long view |
| **Echo** | Counselor — reflection, clarity, steadiness |
| **Pulse** | Health coach — body, energy, rest, recovery |
| **Scout** | Drafter — quick drafts, copy, prototypes |

Pick the agent for the main channel from the panel-header dropdown.

## Requirements

- **Python 3.10+** — no required packages (`psutil` is optional, for system vitals)
- **An AI provider CLI** — the bridge talks to whichever you have installed:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code) (recommended) —
    the command-line tool, **not** the Claude Desktop app (see note below)
  - OpenAI Codex CLI, Gemini CLI, or a local [Ollama](https://ollama.com) model
- **[Node.js](https://nodejs.org) (LTS)** — required for the Claude Code, Codex,
  and Gemini CLIs, which install via `npm`. Not needed for Ollama-only setups.
- A modern browser

Works on **Windows**, **macOS**, **Linux**, and **Chromebooks** (via the built-in
Linux container — see below).

## Install

### Windows

```powershell
git clone https://github.com/huntermixhunter/D.A.T.A.git
cd DATA
.\install\install.bat
```

**Double-click `install\install.bat`** (or run the line above) — that is the only file
you need to launch on Windows. It bypasses Windows' unsigned-script block and pauses on
exit so you can always read the output. (Prerequisite: install **Python 3.10+** from
python.org first, checking **"Add python.exe to PATH"**.)

The installer asks **"Add a DATA icon to your desktop? [Y/n]"** — press **Enter**
for yes. Then just **double-click the DATA icon** to launch (it runs
`start_data.bat` and opens the dashboard for you).

### After install — your DATA icon

When the installer finishes, you'll have a **DATA** icon on your desktop:

![DATA desktop icon](dashboard/assets/icon-256.png)

**Double-click it** to start DATA — it launches the bridge and opens the dashboard
at **http://localhost:7777** automatically. The same mark appears on your browser
tab and in the taskbar while DATA is running. (Said *no* to the shortcut, or on
Mac/Linux? Launch with `start_data.bat` / `./start_data.sh` from the DATA folder
anytime — you can drag it to your dock or taskbar to pin it.)

### macOS / Linux

```bash
git clone https://github.com/huntermixhunter/D.A.T.A.git
cd DATA
bash install/install.sh
./start_data.sh
```

### Chromebook

1. Turn on the Linux development environment: **Settings → About ChromeOS → Linux**.
2. Open the Terminal app, then follow the macOS / Linux steps above.

The dashboard opens at **http://localhost:7777**. (Not technical? See
[INSTALL.txt](INSTALL.txt) for a plain-language walkthrough.)

Once it's running, the [Dashboard Guide](DASHBOARD_GUIDE.md) covers how to actually
drive it — talking to the main channel, customizing the UI by conversation, and
having DATA rewrite its own source to add features and capabilities.

## Connect your AI

DATA doesn't need API keys. You manage every brain from the **AI Connectors** page in
the dashboard, which **scans your hardware**, **recommends and installs a local model**
that fits your machine, and **connects cloud providers** through subscriptions you
already pay for. Whatever you connect there is exactly what the model menu on the main
channel offers — no config files, no restart.

| Provider | One-time setup | Billing |
|----------|---------------|---------|
| **Claude** (Opus / Sonnet / Haiku) | Install [Claude Code](https://docs.claude.com/en/docs/claude-code), then in a terminal run `claude` and type `/login` once to sign in | Your Claude subscription (Pro/Max) |

> **Claude Code CLI vs. Claude Desktop — don't confuse them.** DATA drives the
> **Claude Code command-line tool** (`claude` in a terminal), which you install
> from the link above. The **Claude Desktop app** is a separate GUI program and
> DATA cannot use it. Symptom of having the wrong one: the Claude Desktop window
> opens every time you send a message in DATA. Fix: install [Node.js](https://nodejs.org)
> (the CLI installs via npm — `npm install -g @anthropic-ai/claude-code`), run
> `claude` in a terminal and log in, then restart DATA. Verify with
> `claude --version` — a version number means you have the right tool.
>
> **Log in once by hand.** After installing, open a normal terminal, run
> `claude`, and type `/login` to sign in through the browser. DATA runs
> Claude in the background and cannot display the login prompt, so until
> you've authenticated this once, DATA's chat will error asking you to
> run `/login`. Once you log in by hand, the credentials are cached and
> DATA inherits them automatically — you won't need to do it again.
| **GPT-5 Codex** | `npm i -g @openai/codex`, then `codex login` | Your ChatGPT subscription |
| **Gemini 2.5** | `npm i -g @google/gemini-cli`, then log in on first run | Google free tier / account |
| **Ollama** (local models) | Install [Ollama](https://ollama.com), then `ollama pull qwen2.5-coder:7b` | Free — runs on your hardware |

Each person who installs DATA logs in with **their own** accounts on **their own
machine** — nothing is shared through the repo, and no keys ever live in the
project folder. Install any one of the four and chat works; install several and
you can switch per-conversation (project workspaces can even run different
providers side-by-side).

## Configuration

Copy `.env.example` to `.env` and edit as needed:

| Variable | Purpose |
|----------|---------|
| `DATA_BRIDGE_TOKEN` | Optional shared secret for the bridge API (leave empty for localhost) |
| `DATA_LIFECYCLE_MODE` | `auto` (default — bridge exits when the tab closes) or `daemon` |
| `DATA_PORT` | Port for the bridge server (default `7777`) |

## What's inside

```
DATA/
├── dashboard/
│   ├── bridge_server.py    # the bridge — HTTP server + AI provider dispatch
│   ├── index.html          # the dashboard UI
│   ├── app.js              # dashboard logic
│   ├── theme.css           # MINIMAL + CYBER themes
│   └── assets/             # schematics & images
├── install/                # per-OS install scripts
├── users/                  # created at runtime — per-user memory & history
└── .env.example
```

All state — memory, conversation history, standing orders, caches — is created at
runtime inside this folder and stays on your machine. A fresh clone starts with a
completely blank memory.

## Features

- **Main channel** — full working chat with your chosen provider; markdown, images,
  file links, streaming
- **Project workspaces** — spawn parallel chat panes rooted in any folder, each with
  its own provider and role
- **Memory banks** — persistent per-user memory plus a searchable archive of every
  conversation
- **Standing orders** — cron-scheduled AI tasks (reports, checks, refreshes)
- **AI Connectors** — hardware scan, one-click local-model install (via Ollama), and
  cloud-provider connect; populates the main-channel model menu live
- **Neural Matrix** — a live node-graph of the system: skills, memory, crew
- **System vitals** — engine gauge and subsystem bars driven by real CPU/RAM/GPU
  metrics, with a Master Systems Display

## License

Proprietary — all rights reserved. Use requires purchase or written
authorization. See [LICENSE](LICENSE).
