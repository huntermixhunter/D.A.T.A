# LCARS Dashboard — Setup Manual (Retail Edition)

**Run your own AI command center, online 24/7, with your computer turned off.**

This guide walks you from a fresh account to a private, always-on dashboard you
reach from any browser. No prior server experience required — every command is
copy-and-paste, and each step says exactly what you should see when it works.

You will end up with:
- A small cloud server (a "droplet") running your dashboard around the clock.
- A stable, private web address only you (and anyone you invite) can open.
- A token-locked door so nobody else can drive your AI.
- An **AI Connectors** page where you pick which AI models power the dashboard —
  cloud models and local models alike — which then appear in the model menu on
  the Communications page.

**Time:** ~30 minutes start to finish.
**Cost:** about **$12/month** for the server. The dashboard software and the
secure tunnel are free.

---

## Before you start — what you need

| You need | Where to get it | Cost |
|---|---|---|
| A credit card | — | for the server only |
| A cloud server account | digitalocean.com (or any VPS host) | pay-as-you-go |
| A domain name *(optional but recommended)* | any registrar + Cloudflare | ~$10/yr |
| An AI subscription **or** API key | e.g. an AI provider plan | your existing plan |

> **No domain?** You can still run everything — Phase 5 shows a no-domain "quick
> tunnel" that gives you a temporary web address. A domain just makes the address
> permanent and memorable.

---

## How the pieces fit together

```
   Your browser  ─────►  bridge.yourdomain.com  ─────►  Cloudflare Tunnel
 (laptop / phone)          (your private URL)               │
                                                            ▼
                                              ┌──────────────────────────┐
                                              │   Your cloud server       │
                                              │   • Dashboard "bridge"    │
                                              │   • AI models you connect │
                                              │   • Standing orders       │
                                              └──────────────────────────┘
```

- The **bridge** is the dashboard's engine. It runs on the server day and night.
- The **tunnel** safely exposes it at a web address without opening risky ports.
- A **token** (a long secret string) is required on every request — your lock.
- The **AI Connectors** decide which models the bridge can talk to.

---

# Part 1 — Stand up the 24/7 dashboard

## Phase 1 — Create the server *(~5 min)*

1. Go to **digitalocean.com → Create → Droplets**.
2. **Region:** pick the data center closest to you.
3. **Image:** **Ubuntu 24.04 LTS x64**.
4. **Size:** **Basic → Regular → $12/mo (2 GB RAM / 1 vCPU / 50 GB)**.
   *(You can resize later from the panel if you want more power.)*
5. **Authentication:** choose **SSH key** and add your public key. (DigitalOcean
   shows a one-click guide if you don't have one yet. SSH keys are safer than
   passwords and let you log in without typing one.)
6. **Hostname:** `lcars-bridge`.
7. Click **Create Droplet**, then copy the server's public **IPv4 address** —
   you'll paste it into the commands below wherever it says `YOUR_SERVER_IP`.

> **What's a droplet?** Just a small computer that lives in a data center and
> never sleeps. That's what keeps your dashboard online when your laptop is off.

---

## Phase 2 — Install the dashboard *(one script, ~5 min)*

Open a terminal on your own computer and log in to the server:

```bash
ssh root@YOUR_SERVER_IP
```

Then download and run the setup script. It installs everything the bridge needs
(Python, Node, the AI command-line tools, the tunnel software), creates a
locked-down `lcars` user, copies in the dashboard, turns on the firewall,
**generates your secret token**, and registers the dashboard as a service that
restarts itself on reboot.

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_REPO/main/lcars-dashboard/deploy/setup_vps.sh -o setup_vps.sh
bash setup_vps.sh
```

When it finishes it prints a line like:

```
DATA_BRIDGE_TOKEN = a1b2c3d4e5f6...   ← SAVE THIS
```

**Copy that token somewhere safe.** It is the password to your dashboard. You'll
paste it into the dashboard once, and into the health check below.

> **Getting the software onto the server.** If the `curl` line can't reach the
> code (private repository, or no GitHub access from the server), use the **copy
> method** in Appendix A instead — you push the dashboard folder straight from
> your computer with `scp`. Nothing else in this guide changes.

---

## Phase 3 — Add the dashboard's personality files *(~1 min)*

The dashboard's voice and behavior live in two small text files on your own
computer. Copy them up to the server. From a terminal on your machine:

```bash
scp SOUL_COMPUTER.md lcars@YOUR_SERVER_IP:/home/lcars/AppData/Local/hermes/
scp SOUL.md          lcars@YOUR_SERVER_IP:/home/lcars/AppData/Local/hermes/
```

*(On Windows PowerShell these files live under `$env:LOCALAPPDATA\hermes\`.)*

> Don't copy any other secret files unless a feature specifically needs them.
> Keeping keys off the public server is the safer default.

---

## Phase 4 — Sign in to your AI *(~2 min)*

This is the step that powers the dashboard's brain. You'll do the full,
friendly version of this on the **AI Connectors page in Part 2** — but to get
the default model working immediately, sign in once now:

```bash
ssh root@YOUR_SERVER_IP
sudo -u lcars -i
claude login        # prints a URL + code → open it in your browser, approve
exit
exit
```

The login persists across reboots. You only re-do it if it expires (weeks out).

> The AI Connectors page in Part 2 lets you add other providers (Codex, Gemini)
> the same way — through their own quick one-time sign-in.

---

## Phase 5 — Give it a web address *(~5 min)*

### Option A — Permanent address (recommended, needs a domain on Cloudflare)

This gives you a stable URL like `bridge.yourdomain.com` that never changes.

```bash
ssh root@YOUR_SERVER_IP
cloudflared tunnel login                          # opens a URL → pick your domain
cloudflared tunnel create lcars-bridge            # prints a tunnel ID + a creds file path
cloudflared tunnel route dns lcars-bridge bridge.yourdomain.com

mkdir -p /etc/cloudflared
cp /home/lcars/DATA/lcars-dashboard/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
nano /etc/cloudflared/config.yml                  # paste the tunnel ID + your hostname

cloudflared service install                       # run the tunnel on boot
systemctl enable --now cloudflared
```

### Option B — Temporary address (no domain needed, for a quick test)

```bash
cloudflared tunnel --url http://localhost:7777
```

It prints a random `https://…trycloudflare.com` address. Fine for trying things
out — but it changes every restart, so use Option A once you're ready for daily
use or want to invite someone.

> **Why a tunnel and not just the IP?** The tunnel keeps the server's ports
> closed to the public internet while still letting *you* reach the dashboard.
> It's both easier and safer than exposing the server directly.

---

## Phase 6 — Launch and verify *(~2 min)*

```bash
systemctl start lcars-bridge
systemctl status lcars-bridge      # should say: active (running)
```

From your own computer, confirm it answers (paste your token):

```bash
curl -s https://bridge.yourdomain.com/health -H "X-Data-Token: YOUR_TOKEN"
```

A healthy reply looks like:

```json
{"status":"online","agent":"DATA","mode":"cli"}
```

Now open the dashboard in your browser, go to **Settings**, paste in
`https://bridge.yourdomain.com` as the server address and your **token**, and
you're live. To invite someone, give them the same URL and token.

✅ **Your dashboard is now running 24/7.** Part 2 sets up which AI models it uses.

---

# Part 2 — The AI Connectors page

The **AI Connectors** page (left-hand navigation — **AI CONNECTORS**) is where you
choose the "brains" of your dashboard. Anything you connect here shows up
automatically in the **model menu on the Communications page**, so you can pick a
model per conversation. Nothing is hard-coded — connect a model and it appears;
disconnect it and it disappears.

At the top of the page:

- an **active** pill showing how many brains are currently live, and
- a **RESCAN** button that re-reads your server's hardware and re-checks which
  models and tools are installed.

The page reads top to bottom in four sections.

### A. THIS MACHINE — your server's hardware

A live snapshot of the server the dashboard runs on: CPU and core/thread count,
total and available RAM, graphics card and VRAM (if one is present), and whether
**Ollama** — the engine that runs local models — is installed. Every
recommendation below is sized against these numbers. After you resize the droplet
or add a GPU, hit **RESCAN** and this readout (and the recommendation) update on
the spot.

### B. RECOMMENDED LOCAL MODEL

From the hardware above, the dashboard picks the **largest local model that will
comfortably run on your server** and flags it with a ★. This is the safe
"just works" choice — install it and you have a private model with no usage fees.

> **Reality check for the $12 droplet:** 2 GB of RAM, no graphics card. Its
> recommendation will be a tiny model — fine for light tasks, but pair it with a
> **CLI provider** (Section D) for the heavy thinking. Want big local models
> running 24/7? Resize the droplet or add a GPU server, then **RESCAN**.

### C. LOCAL MODELS — free, run on this machine *(via Ollama)*

A catalog of models you can run entirely on your own server. They are **private**
(nothing leaves the machine) and have **no per-use cost** — limited only by your
memory. Each card shows whether it **fits** your hardware plus an install button.

| Your server's memory | Good local model size | Examples in the catalog |
|---|---|---|
| 2–4 GB (the $12 droplet) | tiny (0.5–3B) | Qwen2.5 0.5B, Llama 3.2 1–3B |
| 8 GB GPU | small (7–8B) | Mistral 7B, Qwen2.5-Coder 7B, Llama 3.1 8B |
| 12–16 GB | medium (13–14B) | Qwen2.5 14B, Qwen2.5-Coder 14B |
| 24 GB+ / workstation | large (32–70B) | Qwen2.5 32B, Llama 3.3 70B |

To install one:

1. Local models need **Ollama**. If the THIS MACHINE readout says Ollama isn't
   installed, install it once from **https://ollama.com** — the card tells you.
2. Click **Install** on the ★ recommended model (or any model that shows as a fit).
3. A progress bar streams the download. Big models take a few minutes.
4. When it finishes, the model is added **live — no restart, no code editing** —
   and appears in the Communications dropdown.

### D. CLI PROVIDERS — cloud models via your subscriptions

These connect the dashboard to top-tier cloud models through **subscriptions you
already pay for**, so there is **no per-token charge**. Three are built in:

| Provider | Models | One-time setup |
|---|---|---|
| **Claude Code** (Anthropic) | Opus · Sonnet · Haiku · Fable | installed by the setup script → sign in with `claude`, then `/login` |
| **Codex** (OpenAI) | GPT-5 Codex | `npm i -g @openai/codex`, then `codex login` (uses your ChatGPT plan) |
| **Gemini CLI** (Google) | Gemini 2.5 Pro | `npm i -g @google/gemini-cli`, then sign in (free tier available) |

Each card shows a status dot:

- 🟢 **Available** — installed and signed in; its models are already in your menu.
- ⚪ **Not ready** — the card shows the exact install command and the login step.
  Run the install from the card, then do the one-time sign-in.

Once a provider is available, **all** of its models appear in the Communications
model menu (Claude Code alone adds Opus, Sonnet, Haiku, and Fable).

> Sign-ins happen in a real terminal/browser session — you never paste a password
> into the dashboard. A subscription login uses a plan you already pay for, with
> no per-message billing.

> **Windows — "running scripts is disabled on this system".** If the `claude`
> command throws this error in PowerShell, Windows is blocking it under its
> default execution policy. Run this once, then retry `claude`:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
> ```
> It affects only your user account and is safe to leave in place.

### E. Using your connected models

- Open the **Communications** page.
- Use the **model menu** at the top of a chat to pick which connected model
  answers — switch per conversation, mid-project, anytime.
- The menu lists **only** models you've actually connected, so there are no dead
  options.

### F. Keeping the catalog fresh

The model world moves fast. **RESCAN** at the top of the page re-reads your
hardware, re-checks installed tools, and refreshes which models are available —
so your options track your machine instead of freezing in time.

---

## Security checklist — please don't skip

- [ ] Your **token** is set and non-empty (the setup script generates one).
      Without it, **anyone with your URL can drive your AI.**
- [ ] The firewall is on; only the SSH login port is open. The dashboard port
      (`7777`) is **not** exposed publicly — the tunnel reaches it privately.
- [ ] You never paste passwords or 2FA codes into the dashboard. Sign-ins happen
      in a real browser window.
- [ ] API keys live only on your own server, entered through the AI Connectors
      page — never shared in a chat or committed to a repo.
- [ ] SSH login is key-based (the DigitalOcean default when you chose SSH keys).

---

## Day-2 operations — the handful of commands you'll ever need

| Task | Command |
|---|---|
| Restart the dashboard | `systemctl restart lcars-bridge` |
| See live logs | `journalctl -u lcars-bridge -f` |
| Update to the latest version | `sudo -u lcars git -C /home/lcars/DATA pull && systemctl restart lcars-bridge` |
| Restart the tunnel | `systemctl restart cloudflared` |
| Change/rotate your token | edit `/etc/lcars/bridge.env`, then `systemctl restart lcars-bridge`, then re-share |
| Add or remove an AI model | use the **AI Connectors** page — no commands needed |

Both the dashboard and the tunnel are set to **start on boot**, so a server
reboot brings everything back on its own.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health` returns `401` | wrong or missing token | re-copy the token from `/etc/lcars/bridge.env`; include the `X-Data-Token` header |
| Dashboard won't load | bridge not running | `systemctl status lcars-bridge`; if stopped, `systemctl start lcars-bridge` and check `journalctl -u lcars-bridge -f` |
| Web address unreachable | tunnel down | `systemctl restart cloudflared`; for Option B the URL changes each restart |
| A model is greyed out in the chat menu | not connected | open **AI Connectors** and connect/sign in to it |
| Local model won't install | Ollama not installed, or not enough memory | install Ollama from ollama.com; then pick the **★ recommended** model or a smaller one |
| A CLI provider shows "not ready" | not installed or not signed in | run the install command on its card, then the login step |

---

## Appendix A — Copy method (no GitHub access on the server)

If you'd rather push the dashboard straight from your computer instead of
cloning it from GitHub, copy the folder up with `scp`, then run the setup script
and skip its clone step:

```bash
scp -r ./lcars-dashboard lcars@YOUR_SERVER_IP:/home/lcars/DATA/
```

Everything else in Phases 2–6 is identical.

---

## Appendix B — Glossary

- **Droplet / VPS** — a small always-on computer rented in a data center.
- **Bridge** — the dashboard's engine that runs on that server.
- **Tunnel** — the secure pipe that gives your dashboard a web address without
  opening dangerous ports.
- **Token** — the long secret string that locks your dashboard to you.
- **Connector** — a link to an AI model (cloud or local) you add on the AI
  Connectors page.
- **Local model** — an AI that runs on your own server: private, no usage fees,
  limited by your hardware.
- **CLI provider** — a cloud AI (Claude Code, Codex, Gemini) reached through a
  subscription you already pay for, added on the AI Connectors page.
- **Ollama** — the free engine that runs local models on your own server.

---

*That's the whole setup. Stand up the server once, connect your models on the
AI Connectors page, and the dashboard is yours — online, private, and answering
on whatever brains you choose to give it.*
