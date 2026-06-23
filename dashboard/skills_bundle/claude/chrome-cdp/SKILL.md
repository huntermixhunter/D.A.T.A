---
name: chrome-cdp
description: Drive the Captain's real, logged-in Chrome via the Chrome DevTools Protocol. Use when an automation needs to act inside an authenticated session — community platforms, Instagram web, YouTube Studio, Threads, Vercel, Cloudflare, any site behind a login. Avoids bot detection because the browser, fingerprint, cookies, and IP all match the Captain's normal browsing.
license: MIT
---

# Chrome CDP Attach — drive the Captain's real Chrome

This is one of three browser control surfaces in DATA. Pick this one when you need a **logged-in session**. For public scraping or testing your own dev server, use `webapp-testing` (clean Playwright Chromium) instead.

> **Setup required (one-time).** This skill drives Chrome through two small helpers — a launcher batch file and a Python CDP helper. If they are not present in your install, create them first (the launcher just starts Chrome with `--remote-debugging-port=9222 --disable-features=AutomationControlled` against an isolated profile dir; the helper is a thin Playwright `connect_over_cdp` wrapper). Paths below assume they live under your DATA install.

## When to use this skill

| Task | Skill to use |
|---|---|
| Public scraping, public screenshots, testing localhost | `webapp-testing` (clean Playwright) |
| Anything behind a login the Captain has — community platform, IG web, YouTube Studio, Vercel dashboard | **`chrome-cdp` (this skill)** |
| Complex multi-step browser task that needs vision/reasoning | Hand off to the **Claude for Chrome extension** in his real browser |

## Setup (one-time)

```bat
%USERPROFILE%\Documents\DATA\chrome_debug_launch.bat
```

This launches Chrome with `--remote-debugging-port=9222` using an isolated profile at `%USERPROFILE%\Documents\DATA\chrome-debug-profile\`. **The Captain logs in once** to each site he wants automated. Sessions persist in that profile dir — log in once, automate forever.

The launcher is idempotent: if port 9222 is already listening, it exits without spawning a second Chrome.

## CLI usage

The helper lives at `%USERPROFILE%\Documents\DATA\dashboard\chrome_cdp.py`.

```bash
# Is debug Chrome running? What tabs are open?
python dashboard/chrome_cdp.py status

# Full-page screenshot
python dashboard/chrome_cdp.py screenshot https://www.skool.com/your-community

# Save fully-rendered HTML to disk
python dashboard/chrome_cdp.py dom https://studio.youtube.com

# Run a JS expression and print the result
python dashboard/chrome_cdp.py eval https://example.com "document.title"

# Open a new tab and leave it open (hand off to the Captain)
python dashboard/chrome_cdp.py open https://www.skool.com/your-community/courses/new
```

Output files default to `%USERPROFILE%\Documents\DATA\chrome-cdp-output\`.

## Python usage (write your own automation)

```python
from chrome_cdp import attach

with attach() as (browser, ctx, page):
    page.goto("https://www.skool.com/your-community/about")
    page.wait_for_load_state("networkidle")
    page.click("text=Edit")          # works because the Captain is logged in
    page.fill("textarea", "New about copy")
    page.click("text=Save")
```

`attach()` is a context manager that yields `(browser, context, page)`. It does NOT close the browser on exit — the Captain's debug Chrome stays running. It only closes the page it opened.

Use the **first existing context** (`browser.contexts[0]`) — that's where the logged-in cookies live. A fresh `new_context()` starts with no cookies.

## Common patterns

**Community platform — create a course post**
```python
with attach() as (_, _, page):
    page.goto("https://www.skool.com/your-community/classroom")
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

- **Two Chromes on the same profile = lockfile error.** That's why the debug Chrome uses `chrome-debug-profile\` — separate from `%LOCALAPPDATA%\Google\Chrome\User Data`. Daily Chrome and debug Chrome can both run.
- **`browser.close()` would kill the Captain's session.** Never call it. Let `attach()` handle cleanup.
- **Sites can still detect automation via `navigator.webdriver` and similar.** This setup includes `--disable-features=AutomationControlled` which suppresses the most common flag, but determined detection (Cloudflare's bot fight mode, etc.) may still trip. If a site keeps challenging, fall back to the Claude for Chrome extension — that runs IN his real Chrome, no automation flags at all.
- **Use the Captain's normal Chrome install** (the default `chrome.exe` under `%ProgramFiles%\Google\Chrome\Application\`). The Playwright bundled Chromium is at a different path and is what `webapp-testing` uses — those are intentionally separate.
- **First-time launch shows a clean Chrome profile.** That's correct. The Captain logs into sites once, those sessions persist in `chrome-debug-profile\Default\Cookies` and survive restarts.

## Reference

- Helper: `%USERPROFILE%\Documents\DATA\dashboard\chrome_cdp.py`
- Launcher: `%USERPROFILE%\Documents\DATA\chrome_debug_launch.bat`
- Profile: `%USERPROFILE%\Documents\DATA\chrome-debug-profile\`
- Default port: `9222` (constant `DEBUG_PORT` in `chrome_cdp.py`)
- Output dir: `%USERPROFILE%\Documents\DATA\chrome-cdp-output\`
