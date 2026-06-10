# DATA — Dashboard for Analytical Thought and Action

A self-hosted, local-first AI operations dashboard. DATA gives you a mission-control
interface for working with AI: a main chat channel wired to your AI provider, a crew
of ten specialist agents, persistent per-user memory, project workspaces, standing
orders (cron-scheduled AI tasks), a news feed, local weather, and live system vitals.

Two dark themes ship in the box — toggle from the bottom-left of the footer:

- **MINIMAL** (default) — professional, utilitarian, slightly futuristic. Near-black
  surfaces, hairline borders, one electric-blue accent, built for long sessions.
- **CYBER** — neon cyan/magenta, chamfered edges, scanlines.

Everything runs on your machine. No accounts, no telemetry, no cloud.

![themes](https://img.shields.io/badge/themes-MINIMAL%20%7C%20CYBER-4d9fff) ![python](https://img.shields.io/badge/python-3.10%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green)

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

Pick the agent for the main channel from the panel-header dropdown, or talk to them
in Conversation Mode (voice, where supported).

## Requirements

- **Python 3.10+** — no required packages (`psutil` is optional, for system vitals)
- **An AI provider CLI** — the bridge talks to whichever you have installed:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code) (recommended)
  - OpenAI Codex CLI, Gemini CLI, or a local [Ollama](https://ollama.com) model
- A modern browser

Works on **Windows**, **macOS**, **Linux**, and **Chromebooks** (via the built-in
Linux container — see below).

## Install

### Windows

```powershell
git clone https://github.com/huntermixhunter/D.A.T.A.git
cd DATA
.\install\install.ps1
```

Then double-click `start_data.bat` (or run it from a terminal).

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

The dashboard opens at **http://localhost:7777**.

## Configuration

Copy `.env.example` to `.env` and edit as needed:

| Variable | Purpose |
|----------|---------|
| `DATA_BRIDGE_TOKEN` | Optional shared secret for the bridge API (leave empty for localhost) |
| `DATA_LIFECYCLE_MODE` | `auto` (default — bridge exits when the tab closes) or `daemon` |
| `DATA_PORT` | Port for the bridge server (default `7777`) |
| `DATA_WEATHER_LAT` / `DATA_WEATHER_LON` | Your coordinates for the weather panel (US only — NWS) |

News sources live in `dashboard/news_sources.json` — plain RSS/Atom URLs, organized
by section. Edit freely.

## What's inside

```
DATA/
├── dashboard/
│   ├── bridge_server.py    # the bridge — HTTP server + AI provider dispatch
│   ├── index.html          # the dashboard UI
│   ├── app.js              # dashboard logic
│   ├── theme.css           # CYBER theme
│   ├── news_aggregator.py  # RSS/YouTube news feed
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
- **Neural Matrix** — a live node-graph of the system: skills, memory, crew
- **System vitals** — engine gauge and subsystem bars driven by real CPU/RAM/GPU
  metrics, with a Master Systems Display
- **News feed & weather** — RSS aggregator and NWS forecast panels

## License

MIT — see [LICENSE](LICENSE).
