# What DATA Can Do — The Capabilities Guide

This is the field manual for DATA. The [README](README.md) tells you what DATA is and
how to install it. The [Dashboard Guide](DASHBOARD_GUIDE.md) tells you how to reshape
DATA by talking to it. **This guide is the catalog of everything DATA can actually do,
and the exact way to ask for each thing.**

There is one rule that runs underneath all of it:

> **You do not operate DATA through menus and buttons. You tell it the outcome you
> want, in plain language, and it does the work with real tools.**

Everything below is a different kind of outcome you can ask for. You do not have to
memorize any syntax. The example prompts are starting points, not magic words. Say it
your own way and DATA will understand.

---

## Table of contents

1. [The first five minutes](#1-the-first-five-minutes)
2. [How to ask well](#2-how-to-ask-well)
3. [Files and the terminal](#3-files-and-the-terminal)
4. [The web — search, fetch, research](#4-the-web--search-fetch-research)
5. [The crew — ten specialists](#5-the-crew--ten-specialists)
6. [Project workspaces — many panes at once](#6-project-workspaces--many-panes-at-once)
7. [Re-rooting the current pane](#7-re-rooting-the-current-pane)
8. [Standing orders — scheduled AI tasks](#8-standing-orders--scheduled-ai-tasks)
9. [Memory — teaching DATA who you are](#9-memory--teaching-data-who-you-are)
10. [Searching your history](#10-searching-your-history)
11. [Skills — installable capability packs](#11-skills--installable-capability-packs)
12. [Voice — talk to it, hear it back](#12-voice--talk-to-it-hear-it-back)
13. [Computer use — driving your screen](#13-computer-use--driving-your-screen)
14. [Integrations — YouTube, Pinterest, social, calendar, mail](#14-integrations--youtube-pinterest-social-calendar-mail)
15. [Design and documents](#15-design-and-documents)
16. [Decisions with clickable options](#16-decisions-with-clickable-options)
17. [Self-reprogramming — DATA editing itself](#17-self-reprogramming--data-editing-itself)
18. [Remote access — reach DATA from anywhere](#18-remote-access--reach-data-from-anywhere)
19. [Choosing your brains — the AI Connectors page](#19-choosing-your-brains--the-ai-connectors-page)
20. [The views](#20-the-views)
21. [Settings and lifecycle](#21-settings-and-lifecycle)
22. [Safety](#22-safety)
23. [A working phrasebook](#23-a-working-phrasebook)

---

## 1. The first five minutes

1. **Launch DATA.** Double-click the DATA icon (or run `start_data.bat` /
   `./start_data.sh`). The dashboard opens at http://localhost:7777 .
2. **Connect a brain.** Open the **AI Connectors** page and either install the
   recommended local model (free, private) or connect a cloud provider you already
   pay for. Details in [section 19](#19-choosing-your-brains--the-ai-connectors-page).
   Chat will not answer until at least one brain is connected.
3. **Pick your model** from the model menu at the top of the main channel.
4. **Say hello and give it a task.** Try: "List the files in my Documents folder and
   tell me which three are largest." Watch it use real tools and report back.

That is the whole onboarding. Everything else in this guide is something you can layer
on when you need it.

---

## 2. How to ask well

DATA is a working agent, not a search box. Three habits make it dramatically better:

- **Ask for the outcome, not the steps.** "Pull this week's open invoices into a
  spreadsheet sorted by due date" beats "open the invoice folder." The agent figures
  out the steps.
- **Be specific about the details that matter to you.** Paths, names, formats,
  deadlines, tone. Vague in, vague out.
- **Iterate in place.** You do not have to get it perfect in one prompt. "Almost —
  make the total row bold and move it to the top" is a normal next message.

You rarely need to grant permission step by step. DATA acts, then reports what it did.

---

## 3. Files and the terminal

**What it does.** DATA can read, create, edit, move, and delete files anywhere you
point it, and it can run terminal commands. This is the foundation everything else is
built on.

**How to ask:**

- "Read `report.md` and summarize the three key findings."
- "Find every file in this folder that mentions 'Q3 budget' and list them."
- "Create a `notes/` folder and move all my `.txt` files into it."
- "Run the test suite and tell me what failed."
- "Rename these screenshots to `screen-01.png`, `screen-02.png`, and so on."
- "This CSV has duplicate rows — clean it and save a deduped copy."

It works in whatever folder the current pane is rooted in (see
[section 7](#7-re-rooting-the-current-pane) to change that).

---

## 4. The web — search, fetch, research

**What it does.** DATA can search the web, open and read specific pages, and pull the
result back into your work. With the research skill it can run a deep, multi-source,
fact-checked report.

**How to ask:**

- "Search for the current best practices on X and summarize the top three."
- "Read this URL and pull out every statistic with its source: <url>"
- "Compare the pricing of these three tools and put it in a table."
- "Do deep research on <topic> and write me a cited report." (fans out many searches,
  verifies claims, and synthesizes)
- "Watch this YouTube video and extract the key takeaways." (pulls the transcript, no
  audio download needed)

Paste a bare URL with a space on each side and DATA will treat it as something to open.

---

## 5. The crew — ten specialists

DATA is the main computer, but it can hand a job to a specialist and bring back the
result. Each has a focus. You summon one by name, or just describe the kind of help
you want and DATA routes it.

| Agent | Use it for |
|-------|------------|
| **DATA** | The main channel — your default working partner and orchestrator |
| **Atlas** | Strategy and planning — turn a vague idea into a structured plan or roadmap |
| **Forge** | Building — write the code, config, or automation |
| **Vector** | Review — evaluate work across correctness, readability, architecture, security, performance |
| **Sentinel** | Security — threat modeling, vulnerability scan, hardening, "is this safe to install" |
| **Probe** | Test and debug — write tests, isolate a bug to its root cause |
| **Relay** | Operations — deploy, infrastructure, uptime, scheduled jobs |
| **Sage** | Advisor — a second opinion, the devil's advocate, the long view |
| **Echo** | Counselor — reflection, journaling, clarity, steadiness |
| **Pulse** | Health coach — training, sleep, nutrition, recovery, energy |
| **Scout** | Drafter — fast copy, captions, descriptions, throwaway prototypes |

**How to ask:**

- "Have Atlas turn this idea into a phased plan."
- "Ask Sentinel whether this npm package is safe before I install it."
- "Get Vector to review the change I just made."
- "Summon the crew — I want to plan, build, and review this feature end to end."
- Change who answers the main channel from the **agent dropdown** at the top of the
  panel.

A useful pattern for real builds: **Atlas plans, Forge builds, Vector reviews,
Sentinel checks security, Probe tests, Relay ships** — and DATA coordinates the hand-offs.

---

## 6. Project workspaces — many panes at once

**What it does.** Spin up one or more parallel chat panes, each rooted in its own
folder, each with its own model and its own role. Work a problem from several angles
at the same time, or keep separate projects in separate panes.

**How to ask:**

- "Spin up a workspace in my blog repo."
- "Open two panes on this project — one to write the feature, one to review it."
- "Give me three windows: one on the frontend folder, one on the backend, one for
  notes."
- "Open a Codex pane and a Claude pane on the same folder so I can compare their takes."

Each pane keeps its own conversation and history. Different panes can run different
models side by side, which is the fastest way to get a second model's opinion on the
same problem.

---

## 7. Re-rooting the current pane

**What it does.** Point the pane you are already in at a different folder, without
opening a new window. Every command, file operation, and tool call after that runs
from the new location.

**How to ask:**

- "Re-root this chat to my `Documents/website` folder."
- "Switch this pane over to the design repo."

If you want an *additional* window instead of moving the current one, that is a
workspace ([section 6](#6-project-workspaces--many-panes-at-once)).

---

## 8. Standing orders — scheduled AI tasks

**What it does.** Standing orders are recurring jobs DATA runs on a schedule, on its
own, whether or not you are watching. Briefings, checks, refreshes, reminders,
digests. They appear on the **Standing Orders** page and fire on their cron schedule.

**How to ask:**

- "Every weekday at 7:30am, give me a market briefing on the assets I care about."
- "Check the deploy every 15 minutes and tell me if it goes down."
- "Every Sunday afternoon, summarize what we worked on this week."
- "Remind me to stretch every two hours during my workday."

You can also open the **Standing Orders** page to see, pause, edit, or delete any of
them. For a job to run while your computer is off, DATA needs to be hosted somewhere
that stays on, or set to daemon lifecycle mode (see
[section 21](#21-settings-and-lifecycle)).

**One ships built in: keeping DATA updated.** Every install comes with a standing
order named **Dashboard self-update**, enabled and set to run nightly at 04:00. It
checks GitHub, downloads any changed dashboard files in place, and restarts to
activate them. To update right now instead of waiting, open the **Standing Orders**
page and click **RUN NOW** on it, or just say **"update yourself."** It works on **any
provider** (Claude, Codex, Gemini, or a local model) because the update is a direct
download, not an AI task, so it never depends on which brain you have connected. Do
this once right after installing to get on the latest build. Replaced files are backed
up under `dashboard/.update_backups/` in case you ever want to roll one back.

---

## 9. Memory — teaching DATA who you are

**What it does.** DATA keeps persistent, per-user memory — facts, preferences, and
context it loads on *every* request. This is how it stops making you repeat yourself.
It lives under `users/<you>/` and you edit it entirely by talking.

**How to ask:**

- "Remember that my main project folder is `Documents/Acme` and I work 9 to 5 Eastern."
- "Remember: never use exclamation marks in anything you write for me."
- "What do you have on file about my projects?"
- "Update the note about my deadline — it moved to Friday."
- "Forget the old address you have for the office."

Good things to store: folder locations, naming conventions, recurring people, hard
rules, and decisions you do not want to re-litigate. The more you teach it, the less
you type.

---

## 10. Searching your history

**What it does.** A pane shows you only its most recent turns directly. Every older
turn, and every turn from every other pane, is kept in a searchable archive — the
**Memory Banks**. When you reference something DATA no longer has in view, it can
search the archive instead of guessing.

**How to ask:**

- "Search your memory banks for what we decided about the pricing page."
- "Check your archive — did we ever talk about the vendor in Ohio?"
- "What did we settle on for the logo colors last month?"

By default it searches the current pane. Ask it to "search all panes" to go wider.

---

## 11. Skills — installable capability packs

**What it does.** Skills are drop-in capability packs the agent discovers
automatically — no restart. DATA ships with a curated bundle (design, documents,
web, media, and more) and you can add more. When a task matches a skill, DATA loads it
and follows its expert playbook.

**How to ask:**

- "What skills can you use?"
- "Find me a skill for editing PDFs."
- "Install a skill that does X, then use it on this file."
- "Build yourself a new skill for <thing you do often>."

New skills become available on the very next request. There is nothing to reboot.

---

## 12. Voice — talk to it, hear it back

**What it does.** DATA has a fully local voice stack. It can transcribe what you say
into the chat (speech to text) and read its replies aloud (text to speech). Both run
on your own machine, so nothing is sent to a cloud voice service. On first use it
downloads the small voice models; if they are not installed it falls back gracefully
to text.

**How to use it:**

- Use the **microphone control** in the chat input to dictate a message instead of
  typing.
- Use the **play / speak control** on a reply to hear it read back.
- "Read that last answer out loud."
- "Turn on voice replies for this session."

**Hands-free wake word.** Tap the **◌ WAKE** pill at the bottom of the dashboard
(desktop Chrome) to arm always-listening mode. Then just say one of these and it
starts taking dictation — no clicking:

- **"Computer"** (on its own), or **"Hey data" / "Ok computer" / "Yo data"**
- **"Data, start listening"** or **"Wake up"**
- Say any officer's name — **Atlas, Forge, Vector, Sentinel, Probe, Relay, Echo, Pulse, Sage, Scout** — to switch the main channel to that officer and dictate to them
- **"Computer, what can you do"** (or "Computer, help") — DATA speaks a short rundown of the voice commands
- **"Computer, stop"** cuts off a spoken reply; **"Computer, voice off"** switches the wake word off (tap the pill to turn it back on); **"Computer, shut down"** powers DATA off (five-second cancel)

Wake listening runs on the browser's own speech recognition, so it stays off until
you turn it on and it is desktop-Chrome only (mobile browsers keep the mic notice up).

The voice engine is CPU-friendly by default (Kokoro for speech, faster-whisper for
transcription), so it works even without a powerful graphics card.

---

## 13. Computer use — driving your screen

**What it does.** DATA can see your screen and control your mouse, keyboard, and
clicks — the same way a person would. Use it for anything that has no clean API: a
desktop app, a settings dialog, an installer, or a website flow that has to be clicked
through. The loop is always: screenshot, look, act, screenshot again to check.

**How to ask:**

- "Look at my screen and tell me what is open."
- "Open the settings dialog and turn on dark mode."
- "Click through this installer, accepting the defaults, and stop before the final
  confirm so I can check it."
- "Find the publish button on this page and click it."

**Safety.** There is a kill switch, and DATA will pause and hand control back before
anything destructive (delete, send, publish, pay). It will never type your passwords
or two-factor codes — if a flow needs one, it stops and asks you to take over. Slamming
the mouse to the top-left corner is an emergency stop.

---

## 14. Integrations — YouTube, Pinterest, social, calendar, mail

**What it does.** Once you authorize an account, DATA can act inside it directly.
These require a one-time sign-in (DATA will walk you through it, and sign-ins always
happen in a real browser window, never by pasting a password into the dashboard).

- **YouTube** — upload videos, edit titles and descriptions, set thumbnails, across
  multiple channels. Uploads default to **private** unless you say otherwise.
  - "Upload this video as private and set the thumbnail to `thumb.jpg`."
  - "Change the description on that video to include the new link."
- **Pinterest** — list your boards and create pins.
  - "Show me my boards, then pin this image to the recipes board."
- **Instagram** — connect an account for posting and insights (authorize first).
- **Calendar** — read and create events, find open time.
  - "What is on my calendar tomorrow?"
  - "Book an hour on Thursday afternoon and call it 'deep work.'"
- **Mail** — search, read, draft, and label email.
  - "Draft a reply to the last email from the vendor and hold it for my review."
- **Google Drive and Docs / other connected services** — through MCP connectors, DATA
  can reach the tools you wire up (files, documents, deploy platforms, and more).
  - "Wire up my Google Drive so you can search my files."

Ask DATA to "connect <service>" and it will tell you the exact one-time steps.

---

## 15. Design and documents

**What it does.** Through its skill bundle, DATA can produce real, polished
deliverables — not just text about them.

- **Websites and UI** — "Build me a landing page for X." DATA loads its design skills
  first, builds it, starts a local server, and gives you a clickable link.
- **Slides** — "Make a 10-slide deck on X with charts."
- **Documents** — "Write this up as a formatted Word document with headings."
- **Spreadsheets** — "Turn this data into an Excel file with a totals row and a chart."
- **PDFs** — "Fill in this PDF form" or "fix the typo on page 3 of this PDF."
- **Logos, banners, icons, social images** — "Design three logo directions for X" or
  "make a YouTube banner in a minimalist style."
- **Diagrams** — "Draw the architecture as a diagram."

For anything visual, DATA embeds a preview in the chat so you can see it and click
through to the full file.

---

## 16. Decisions with clickable options

**What it does.** When a choice is genuinely yours to make and the answer changes what
DATA does next, it can present the options as **clickable buttons** right in the chat
instead of burying the question in a paragraph. You tap one (or type your own) and it
continues.

You do not trigger this yourself — DATA offers it when a real fork appears. It keeps
decisions crisp and stops the agent from guessing on something it should ask about.

---

## 17. Self-reprogramming — DATA editing itself

**What it does.** DATA is built from files it can read and write — the server, the UI,
the themes, its own personality and memory. So you can change almost anything about the
tool by describing it. This is a whole topic on its own; the
[Dashboard Guide](DASHBOARD_GUIDE.md) covers it in depth. The short version:

- "Make the accent color a warmer amber."
- "Add a header button that exports this conversation to Markdown."
- "Give yourself a tool to read my note vault at `<path>`."

Front-end changes take effect on a browser refresh; back-end changes take effect on a
bridge restart. Before a big self-modification, ask it to "commit the current state
first" so the change is one revert away.

---

## 18. Remote access — reach DATA from anywhere

**What it does.** DATA can open a secure public tunnel (via cloudflared) so you can
reach your own dashboard from your phone or another computer, while the bridge keeps
running on your home machine.

**How to ask:**

- "Start a tunnel so I can reach DATA from my phone."
- "What is my remote URL right now?"

The tunnel URL is shown once it is live. Pair this with daemon lifecycle mode
([section 21](#21-settings-and-lifecycle)) so standing orders keep firing while you are
away.

---

## 19. Choosing your brains — the AI Connectors page

DATA needs at least one AI "brain." The **AI Connectors** page is where you connect
them, and whatever you connect there is exactly what the model menu on the main
channel offers. No config files, no restart. The page reads top to bottom:

1. **This machine** — a live readout of your CPU, RAM, graphics card, and whether
   Ollama (the local-model engine) is installed. Everything below is sized against
   these numbers.
2. **Recommended local model** — the largest local model that will run comfortably on
   your hardware, marked with a star. The private, no-cost, "just works" choice. Click
   **Install** and the download streams in; when it finishes it is live in the menu.
3. **Local models** (via Ollama, free, on your machine) — the full catalog, each card
   showing whether it fits your hardware.
4. **Cloud providers** (via your subscriptions, no per-message billing):

| Provider | Models it adds | One-time setup |
|----------|----------------|----------------|
| **Claude Code** | Opus, Sonnet, Haiku, Fable | install Claude Code, run `claude`, sign in |
| **Codex** (OpenAI) | GPT-5 Codex | `npm i -g @openai/codex`, then `codex login` |
| **Gemini CLI** (Google) | Gemini 2.5 Pro | `npm i -g @google/gemini-cli`, then sign in |

Hit **RESCAN** after you install a model or sign in to a provider. Install any one
brain and chat works; connect several and you can switch per conversation.

---

## 20. The views

You move between these from the left navigation.

| View | What it is for |
|------|----------------|
| **Main channel** | Your primary working chat. This is where you spend most of your time. |
| **Project workspaces** | Parallel panes, each rooted in a folder with its own model and role. |
| **Memory Banks** | Persistent memory plus the searchable archive of every conversation. |
| **Standing Orders** | Your scheduled AI tasks. |
| **AI Connectors** | Connect and manage the models. |
| **Neural / Positronic Matrix** | A live node-graph of the system — skills, memory, crew. |
| **System vitals** | Engine gauge and subsystem bars driven by real CPU / RAM / GPU metrics. |

Two themes ship in the box — **MINIMAL** (default) and **CYBER**. Toggle from the
bottom-left of the footer.

---

## 21. Settings and lifecycle

Most behavior is changed by conversation. A few base settings live in `.env` (copy
`.env.example` to `.env`):

| Variable | Purpose |
|----------|---------|
| `DATA_BRIDGE_TOKEN` | Optional shared secret for the bridge API (leave empty for localhost) |
| `DATA_LIFECYCLE_MODE` | `auto` (default — the bridge exits shortly after you close the tab) or `daemon` (keeps running so standing orders fire with no tab open) |
| `DATA_PORT` | Port for the bridge server (default `7777`) |

Set `DATA_LIFECYCLE_MODE=daemon` if you want standing orders and remote access to keep
working after you close the browser.

---

## 22. Safety

DATA is a real autonomous agent with real tools. Treat it accordingly.

- **It can run commands, change files, install software, drive your screen, and act in
  connected accounts.** AI is non-deterministic and can take unintended or
  irreversible actions. Keep backups.
- **The project is under git.** Before large or risky work, ask DATA to commit or
  snapshot first, so any change is one revert away.
- **Review before destructive actions.** For anything that deletes, sends, publishes,
  or pays, ask DATA to show you what it is about to do and wait for your go-ahead.
- **Your credentials stay yours.** Sign-ins happen in a real browser window. DATA will
  not type your passwords or two-factor codes.
- **One change at a time when editing the brain.** If something breaks after a
  back-end edit, the first move is almost always "revert that and restart the bridge."

See [DISCLAIMER.md](DISCLAIMER.md) and the [LICENSE](LICENSE) for the full terms.

---

## 23. A working phrasebook

Copy-paste starting points. Replace the specifics with yours.

```
Get work done
  "Read every file in this folder and give me a one-paragraph summary of each."
  "Turn this messy data into a clean spreadsheet sorted by date, with a totals row."
  "Do deep research on <topic> and write me a cited report."
  "Build a landing page for <thing> and give me the link."

Run several angles at once
  "Open two workspaces on this repo — one to build, one to review."
  "Re-root this pane to my design folder."
  "Summon Atlas to plan it, then Forge to build it, then Vector to review."

Automate it
  "Every weekday at 8am, brief me on <topic>."
  "Check the site every 15 minutes and alert me if it's down."

Remember and recall
  "Remember: my workday is 9 to 5 Eastern, hard stop."
  "Search your memory banks for what we decided about pricing."

Reach beyond the chat
  "Look at my screen and click the publish button."
  "Upload this video to YouTube as private."
  "What's on my calendar tomorrow?"
  "Start a tunnel so I can reach DATA from my phone."

Reshape DATA itself
  "Add a header button that exports this chat to Markdown."
  "Make the accent color amber and set MINIMAL as default."
  "Commit the current state first, then make the change."
```

---

### The one rule worth keeping

When in doubt, **describe the outcome you want and ask DATA to make it happen.** It has
real tools, it can read and change its own code, and it will tell you how to reload.
The whole point is that you drive it by talking.
