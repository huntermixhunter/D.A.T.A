---
name: ig-field-debrief
description: >
  Generate a "D.A.T.A. Field Debrief" — a 6-page sci-fi/HUD-styled Instagram
  growth analysis PDF (vitals, top-post signal map, root-cause diagnosis, a
  six-directive fix protocol, and a 14-day flight plan). Use when the Captain
  says "run a debrief on @handle", "analyze this instagram", "field debrief",
  "IG recon", "break down [someone]'s instagram", or for a weekly auto-debrief
  of the Captain's own accounts. Produces a print-perfect PDF + page PNGs + a
  warm DM draft (for friends) or a gameplan note (for the Captain's own pages).
---

# IG Field Debrief

A repeatable pipeline that turns any public Instagram account into a polished,
spaceship-console "field debrief" PDF with a real, actionable growth plan.
The gold-standard reference output is `assets/reference_bthewizardart.html` —
open it to see the exact look, voice, and density to match.

## When to use
- **One-off recon** — "run a debrief on @somehandle", "analyze @x's instagram".
- **Weekly self-audit** — a standing order that debriefs the Captain's own
  accounts and produces a gameplan (see "Weekly mode" at the bottom).
- Friends, prospects, clients, or the Captain himself — same pipeline.

## Output location
- One-off for someone else: `%USERPROFILE%\Documents\<handle>_DATA_debrief\`
- Weekly self-audit: `%USERPROFILE%\Documents\IG_Debriefs\<YYYY-MM-DD>_<handle>\`

Each run produces: `debrief.html`, `DATA_Debrief_<handle>.pdf`,
`page_1..6.png`, and either `DM_to_<name>.md` (friend) or `gameplan.md` (own).

---

## STEP 1 — Gather the data

Goal: enough signal to be specific and honest. Never invent numbers — if a
metric can't be confirmed, label it as an estimate and say so.

### A. Someone else's public account
Use the least-friction source that works, in this order:
1. **chrome-cdp** (best for logged-in fidelity) — drive the Captain's real Chrome
   to the profile, scroll the grid, open recent posts, read like/comment counts.
   Launch `%USERPROFILE%\Documents\DATA\chrome_debug_launch.bat` if port 9222
   isn't up, then use the `chrome-cdp` skill helper.
2. **Clean Playwright** (`webapp-testing` skill) for public, unauthenticated
   scraping/screens of the profile + recent posts.
3. **Manual** — if the Captain pastes counts or a screenshot, work from that.

Pull at minimum:
- Profile: followers, following, total posts, bio, link-in-bio present (y/n).
- A sample of the **most-recent 25–40 posts**: a one-line description of each +
  its likes and comments (public engagement = likes + comments). Note format
  (reel / photo / carousel) and apparent topic/pillar.

### B. The Captain's own account
- If the account has a live IG Graph API token configured (see the
  `instagram`/Graph API setup in your install), use it for the media list +
  per-post insights (views, reach, saves, shares, follows) where scope allows.
- If the Captain has a prior insights/analysis note for the account, read it
  first for baseline + any locked content rules; the debrief must build on it,
  not contradict it.
- **Owner insights** (views, saves, shares, follows-from-post, % non-follower
  reach) are the gold metrics for the Captain's own pages — prioritize them over
  likes. If the API scope can't return insights headlessly, fall back to
  chrome-cdp against the logged-in professional dashboard.

### Compute these derived numbers
- Avg likes/post, avg comments/post over the sample.
- **Engagement rate** = (avg likes + avg comments) / followers × 100. Benchmark:
  healthy small accounts run 3–6%; flag <1% as critical.
- Posting cadence (posts/day or /week).
- Rank posts by engagement → identify the **top performers** and the
  **dead-weight** dragging the average.
- Cluster posts into **content pillars** and grade each pillar A–F by how the
  audience actually rewards it.

---

## STEP 2 — Run the analysis (the framework)

Every debrief answers the same five questions, in this order. This is the IP —
keep it rigorous and specific to the account, never generic.

1. **VITALS** — the raw telemetry and what it means. Lead with the one number
   that reframes everything (e.g. "0.18% engagement = 9 of every 5,000 react").
   Separate "the audience exists" from "the audience is being activated".
2. **SIGNAL MAP** — what the audience *actually rewards*. Two ranked bar lists:
   top transmissions vs. dead-weight. Name the pattern out loud.
3. **DIAGNOSIS** — the root cause(s), not symptoms. Usually 1–2 core problems
   (e.g. pillar scatter + announcement-style hooks). Pair a STRENGTHS column
   with a LEAKS column. Be honest but generous.
4. **THE PROTOCOL** — exactly six directives to raise the signal. Each = a
   punchy imperative title + 2–3 sentences of how + a one-line "WHY".
5. **14-DAY FLIGHT PLAN** — a day-by-day test run + the success KPIs to watch
   (saves, shares, watch%, non-follower reach — not vanity likes) + a final
   transmission that lands the encouragement.

Voice & stance:
- Confident, precise, "spaceship console" mission-debrief tone — but the advice
  underneath is real social-media strategy, not theatrics.
- Generous and on-the-creator's-side. The recurring thesis: *"You're not short
  on talent or material — the only fix is aim."* Diagnose problems as fixable.
- Honor solid short-form fundamentals: first-person high-stakes hooks, build
  every post to earn a SAVE or SHARE, hook = a promise/stake not scenery.

---

## STEP 3 — Build the HTML

1. Copy `assets/reference_bthewizardart.html` into the run's work dir as
   `debrief.html`.
2. Replace ALL content with the new account's real analysis. Keep the `<style>`
   block, the `.page` (794×1123) structure, the 6-page order, and every visual
   component (topbar, stat grid, bar rows, diag columns, directive cards,
   timeline, callouts, quotes, footer). Only the *content* changes.
3. Section grammar (repeatable building blocks already in the reference):
   - `.stat` cards with `.tag ok|warn|crit` and `.v ok|warn|crit` for color.
   - `.brow` bar rows: `<div class="lab">…</div><div class="track"><div class="fill f-green|f-cyan|f-amber|f-red" style="width:NN%"></div></div><div class="val">N</div>` — width = relative to the top item.
   - `.diag .col.good` / `.col.bad` for strengths vs leaks.
   - `.directive` cards numbered 01–06 with `.why` lines.
   - `.tl .ti` timeline items for the flight plan.
4. Update the cover target block (handle, operator, class, sector, scan date,
   sample depth) and every footer page label.
5. Keep it to **exactly 6 pages**. Density should match the reference — full but
   not overflowing a page.

## STEP 4 — Render
From the skill dir (`%USERPROFILE%\.claude\skills\ig-field-debrief`):
```
python render.py "<work_dir>"                 # -> DATA_Debrief.pdf
python shot.py   "<work_dir>"                 # -> page_1..6.png
```
Requires Playwright Chromium. Rename the PDF to `DATA_Debrief_<handle>.pdf`.
Embed the page PNGs in chat so the Captain can see them (markdown image syntax,
absolute paths).

## STEP 5 — Deliver
- **Friend/prospect:** write `DM_to_<name>.md` — a warm, casual, first-person DM
  in the Captain's voice (short + long version), explaining the gift and the one
  or two headline findings. Note that IG DMs don't take PDFs, so send the text
  then the PDF via email/text or as page screenshots.
- **Captain's own account:** write `gameplan.md` — the same findings distilled
  into this week's concrete posting plan (what to post, which hooks, what to
  stop), tied to any locked content rules for the account.

---

## Weekly mode (standing order)
When invoked as the weekly self-audit:
1. For each target handle, run STEPS 1–4 and save under
   `%USERPROFILE%\Documents\IG_Debriefs\<YYYY-MM-DD>_<handle>\`.
2. Compare against the **prior week's** debrief in `IG_Debriefs\` — call out
   what moved (followers, engagement rate, best/worst format) and whether last
   week's directives were followed and worked.
3. Write a combined `gameplan.md` for the week and post a short summary in chat:
   top win, top leak, and the 3 highest-leverage moves for the next 7 days.
4. Don't publish anything. This is analysis + plan only.
