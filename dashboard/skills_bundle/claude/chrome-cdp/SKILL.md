---
name: chrome-cdp
description: Drive the Captain's real, logged-in Chrome via the Chrome DevTools Protocol. Use when an automation needs to act inside an authenticated session — Skool, Instagram web, YouTube Studio, Threads, Vercel, Cloudflare, any site behind a login. Avoids bot detection because the browser, fingerprint, cookies, and IP all match the Captain's normal browsing.
license: MIT
---

# Chrome CDP Attach — drive the Captain's real Chrome

This is one of three browser control surfaces on LCARS. Pick this one when you need a **logged-in session**. For public scraping or testing your own dev server, use `webapp-testing` (clean Playwright Chromium) instead.

## When to use this skill

| Task | Skill to use |
|---|---|
| Public scraping, public screenshots, testing localhost | `webapp-testing` (clean Playwright) |
| Anything behind a login the Captain has — Skool community, IG web, YouTube Studio, Vercel dashboard | **`chrome-cdp` (this skill)** |
| Complex multi-step browser task that needs vision/reasoning | Hand off to the **Claude for Chrome extension** in his real browser |

## Setup (one-time)

```bat
C:\Users\mixma\Documents\LCARS\chrome_debug_launch.bat
```

This launches Chrome with `--remote-debugging-port=9222` using an isolated profile at `C:\Users\mixma\Documents\LCARS\chrome-debug-profile\`. **The Captain logs in once** to each site he wants automated. Sessions persist in that profile dir — log in once, automate forever.

The launcher is idempotent: if port 9222 is already listening, it exits without spawning a second Chrome.

## CLI usage

The helper lives at `C:\Users\mixma\Documents\LCARS\lcars-dashboard\chrome_cdp.py`.

```bash
# Is debug Chrome running? What tabs are open?
python lcars-dashboard/chrome_cdp.py status

# Full-page screenshot
python lcars-dashboard/chrome_cdp.py screenshot https://www.skool.com/auramaxxing-academy

# Save fully-rendered HTML to disk
python lcars-dashboard/chrome_cdp.py dom https://studio.youtube.com

# Run a JS expression and print the result
python lcars-dashboard/chrome_cdp.py eval https://example.com "document.title"

# Open a new tab and leave it open (hand off to the Captain)
python lcars-dashboard/chrome_cdp.py open https://www.skool.com/auramaxxing-academy/courses/new
```

Output files default to `C:\Users\mixma\Documents\LCARS\chrome-cdp-output\`.

## Python usage (write your own automation)

```python
from chrome_cdp import attach

with attach() as (browser, ctx, page):
    page.goto("https://www.skool.com/auramaxxing-academy/about")
    page.wait_for_load_state("networkidle")
    page.click("text=Edit")          # works because the Captain is logged in
    page.fill("textarea", "New about copy")
    page.click("text=Save")
```

`attach()` is a context manager that yields `(browser, context, page)`. It does NOT close the browser on exit — the Captain's debug Chrome stays running. It only closes the page it opened.

Use the **first existing context** (`browser.contexts[0]`) — that's where the logged-in cookies live. A fresh `new_context()` starts with no cookies.

## Common patterns

**Skool — create a course post**
```python
with attach() as (_, _, page):
    page.goto("https://www.skool.com/auramaxxing-academy/classroom")
    page.wait_for_load_state("networkidle")
    page.click('button:has-text("New course")')
    # ... fill form, save
```

**Instagram web — read DMs**
```python
with attach() as (_, _, page):
    page.goto("https://www.instagram.com/direct/inbox/")
    page.wait_for_load_state("networkidle")
    threads = page.locator('[role="listbox"] [role="listitem"]').all()
```

**YouTube Studio — pull video analytics**
```python
with attach() as (_, _, page):
    page.goto("https://studio.youtube.com/channel/UC.../analytics")
    page.wait_for_load_state("networkidle")
    rows = page.evaluate("[...document.querySelectorAll('tr')].map(r => r.innerText)")
```

## Pitfalls

- **Two Chromes on the same profile = lockfile error.** That's why the debug Chrome uses `chrome-debug-profile\` — separate from `C:\Users\mixma\AppData\Local\Google\Chrome\User Data`. Daily Chrome and debug Chrome can both run.
- **`browser.close()` would kill the Captain's session.** Never call it. Let `attach()` handle cleanup.
- **Sites can still detect automation via `navigator.webdriver` and similar.** This setup includes `--disable-features=AutomationControlled` which suppresses the most common flag, but determined detection (Cloudflare's bot fight mode, etc.) may still trip. If a site keeps challenging, fall back to the Claude for Chrome extension — that runs IN his real Chrome, no automation flags at all.
- **Captain's Chrome must be the v148+ install** at `C:\Program Files\Google\Chrome\Application\chrome.exe`. The Playwright bundled Chromium is at a different path and is what `webapp-testing` uses — those are intentionally separate.
- **First-time launch shows a clean Chrome profile.** That's correct. The Captain logs into sites once, those sessions persist in `chrome-debug-profile\Default\Cookies` and survive restarts.

## Reference

- Helper: `C:\Users\mixma\Documents\LCARS\lcars-dashboard\chrome_cdp.py`
- Launcher: `C:\Users\mixma\Documents\LCARS\chrome_debug_launch.bat`
- Profile: `C:\Users\mixma\Documents\LCARS\chrome-debug-profile\`
- Default port: `9222` (constant `DEBUG_PORT` in `chrome_cdp.py`)
- Output dir: `C:\Users\mixma\Documents\LCARS\chrome-cdp-output\`
