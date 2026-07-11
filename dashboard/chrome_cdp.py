"""
chrome_cdp.py — drive the Captain's real, logged-in Chrome via DevTools Protocol.

Workflow:
    1. Captain runs `chrome_debug_launch.bat` once. Chrome opens on port 9222
       with a dedicated user-data-dir, and the Captain logs into sites he
       wants automated (Instagram web, YouTube Studio, Vercel, etc.).
    2. From then on, automation here attaches to that running Chrome and
       drives it. The Captain can keep using it normally — automation just
       opens new tabs.

Why CDP attach vs fresh Playwright Chromium:
    - The site sees a normal Chrome instance with real cookies/session.
    - No login wall, no bot detection on first request.
    - Browser fingerprint matches the Captain's real device.

CLI usage:
    python dashboard/chrome_cdp.py status
    python dashboard/chrome_cdp.py screenshot <url> [out.png]
    python dashboard/chrome_cdp.py dom <url>
    python dashboard/chrome_cdp.py eval <url> "<js expression>"
    python dashboard/chrome_cdp.py open <url>           # leave the tab open

Python import usage:
    from chrome_cdp import attach
    with attach() as (browser, ctx, page):
        page.goto("https://example.com/about")
        page.screenshot(path="shot.png", full_page=True)
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Iterator, Tuple

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        sync_playwright,
    )
except ImportError:
    print(
        "[chrome_cdp] ERROR: Playwright not installed. Run:\n"
        "  pip install playwright\n"
        "  playwright install chromium",
        file=sys.stderr,
    )
    raise

DEBUG_PORT = 9222
CDP_URL = f"http://localhost:{DEBUG_PORT}"

# Install root — resolved at runtime so this works for ANY user on ANY machine.
# The launcher and this helper both live inside the DATA install; state dirs
# (profile, output) sit alongside them under the same install root.
#   dashboard/chrome_cdp.py  ->  parents[1] == the DATA install root
_DATA_ROOT = Path(
    os.environ.get("DATA_HOME") or (Path(__file__).resolve().parents[1])
)
_LAUNCHER = _DATA_ROOT / "chrome_debug_launch.bat"

# Default output directory for screenshots / dumps
DEFAULT_OUT_DIR = _DATA_ROOT / "chrome-cdp-output"


def is_debug_chrome_running() -> bool:
    """Quick probe — is debug Chrome listening on port 9222?"""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return "Browser" in data
    except Exception:
        return False


@contextlib.contextmanager
def attach() -> Iterator[Tuple[Browser, BrowserContext, Page]]:
    """Attach to the running debug Chrome.

    Yields (browser, context, page). The page is reused from an existing
    blank-tab context if available, otherwise a new tab is opened.

    Does NOT close the browser on exit — that would kill the Captain's
    debug Chrome. Only closes the page if it was newly created.
    """
    if not is_debug_chrome_running():
        raise RuntimeError(
            "Debug Chrome is not running on port 9222.\n"
            "Start it with:\n"
            f"  {_LAUNCHER}"
        )

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        # Reuse the first existing context (the Captain's logged-in session)
        # or create a new one if there are none.
        if browser.contexts:
            ctx = browser.contexts[0]
        else:
            ctx = browser.new_context()

        page = ctx.new_page()
        try:
            yield browser, ctx, page
        finally:
            with contextlib.suppress(Exception):
                page.close()
            # Do NOT close browser or context — the Captain's Chrome stays up.
    finally:
        pw.stop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _ensure_out_dir() -> Path:
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_OUT_DIR


def cmd_status(_args: argparse.Namespace) -> int:
    running = is_debug_chrome_running()
    if not running:
        print(
            "[chrome_cdp] Debug Chrome is NOT running.\n"
            f"  Start it: {_LAUNCHER}"
        )
        return 1

    with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    print(f"[chrome_cdp] OK — port {DEBUG_PORT} is live")
    print(f"  Browser     : {info.get('Browser')}")
    print(f"  Protocol-Ver: {info.get('Protocol-Version')}")
    print(f"  User-Agent  : {info.get('User-Agent')}")

    with urllib.request.urlopen(f"{CDP_URL}/json", timeout=2) as resp:
        tabs = json.loads(resp.read().decode("utf-8"))
    print(f"  Open tabs   : {len(tabs)}")
    for t in tabs[:5]:
        title = t.get("title", "")[:60]
        url = t.get("url", "")[:80]
        print(f"    - {title}  ::  {url}")
    return 0


def cmd_screenshot(args: argparse.Namespace) -> int:
    out_path = Path(args.out) if args.out else _ensure_out_dir() / f"shot_{int(time.time())}.png"
    with attach() as (_b, _c, page):
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15_000)
        page.screenshot(path=str(out_path), full_page=True)
    print(f"[chrome_cdp] screenshot -> {out_path}")
    return 0


def cmd_dom(args: argparse.Namespace) -> int:
    out_path = Path(args.out) if args.out else _ensure_out_dir() / f"dom_{int(time.time())}.html"
    with attach() as (_b, _c, page):
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15_000)
        html = page.content()
    out_path.write_text(html, encoding="utf-8")
    print(f"[chrome_cdp] dom -> {out_path}  ({len(html):,} chars)")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    with attach() as (_b, _c, page):
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15_000)
        result = page.evaluate(args.expr)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    # Open a new tab and leave it open — useful for handing off to the Captain.
    if not is_debug_chrome_running():
        print("[chrome_cdp] Debug Chrome not running.", file=sys.stderr)
        return 1
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(args.url)
        print(f"[chrome_cdp] opened tab: {args.url}")
    finally:
        pw.stop()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive the Captain's debug Chrome via CDP.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="Is debug Chrome running? Show tabs.")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("screenshot", help="Full-page screenshot of a URL.")
    sp.add_argument("url")
    sp.add_argument("out", nargs="?")
    sp.set_defaults(func=cmd_screenshot)

    sp = sub.add_parser("dom", help="Save fully-rendered HTML of a URL.")
    sp.add_argument("url")
    sp.add_argument("out", nargs="?")
    sp.set_defaults(func=cmd_dom)

    sp = sub.add_parser("eval", help="Run a JS expression in the page.")
    sp.add_argument("url")
    sp.add_argument("expr")
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("open", help="Open a URL in a new tab and leave it open.")
    sp.add_argument("url")
    sp.set_defaults(func=cmd_open)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
