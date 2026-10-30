# Crew Deck

The dashboard navigation opens this workspace at `/crew-deck/`. The bridge
serves the public assets and profile-scoped `/api/crew/` endpoints. A separate
preview server is not needed. Restart the bridge after installing backend changes.

The page shows live activity from `/crew_activity`, falling back to clearly
labeled assignment rules when that feed is unavailable. Missions add real
officer turns, conversation, tool evidence, reusable orders, and isolated files.
Closing or leaving the page unloads its animation without stopping missions.

State belongs to the active profile under `users/<profile>/crew_deck/`:

- `crew.sqlite3` stores settings, saved orders, mission checkpoints, and events.
- `missions/<id>/workspace/` contains mission artifacts.
- `personalities/<officer>.md` contains optional learned notes. The original
  identity files remain in the dashboard's existing agent directory.

The API checks dashboard authentication, origin, active profile, and a CSRF token
for mutations. Credentials are exchanged with the embedded frame in memory and
sent in headers. Source files, private state, and workspaces are not static assets.
Profile changes hide earlier private data and pause the former profile's work.

New missions require a usage acknowledgement. Codex uses its existing ChatGPT
subscription login with native execution tools disabled. Ollama uses installed
local models only. The service can read, write, and list workspace files and
retrieve public text pages over HTTPS. Paid services, publication, deployment,
payments, and signing require an external manual handoff. Nothing in this module
connects a wallet or executes arbitrary shell commands.

Ambient dialogue runs locally without inference. Personality evolution is off
by default, requires evidence from another officer's real mission work, and
preserves revision history. Single missions pause at their configured review
limit. Continuous missions continue between rounds while the bridge is running.
On restart, completed handoffs persist; interrupted turns pause for review.

Run the integration tests from the repository root:

```text
python -I dashboard/tests/test_crew_deck.py
node dashboard/tests/test_crew_dialogue.cjs
```
