"""Exercise Settings skill search in the real dashboard using a local catalog.

Run: python dashboard/tests/skill_search_browser.py
Uses installed Playwright/Chromium. No provider calls or external requests.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill_search as ss
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class SearchPreviewHandler(BaseHTTPRequestHandler):
    """Only static dashboard files and the fixture-backed search are served."""
    def log_message(self, *_args):
        pass

    def _check_auth(self):
        if self.headers.get("X-Data-Token") == "search-browser-test":
            return True
        self.send_response(401)
        self.end_headers()
        return False

    def do_GET(self):
        try:
            if ss.handle_http(self):
                return
            path = urlsplit(self.path).path
            files = {
                "/": ("index.html", "text/html"),
                "/index.html": ("index.html", "text/html"),
                "/app.js": ("app.js", "application/javascript"),
                "/theme.css": ("theme.css", "text/css"),
                "/skill_search.js": ("skill_search.js", "application/javascript"),
                "/skill_search.css": ("skill_search.css", "text/css"),
            }
            if path in files:
                name, mime = files[path]
                body = (ROOT / name).read_bytes()
                status = 200
            else:
                status, mime = 404, "application/json"
                body = json.dumps({"error": "This test only runs skill search."}).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # Aborted searches intentionally disconnect.

    def do_POST(self):
        self.send_response(403)
        self.end_headers()


def main():
    calls = []
    fail_once = [True]
    def catalog(query):
        calls.append(query)
        if query == "offline" and fail_once[0]:
            fail_once[0] = False
            raise ss.SearchUnavailable("Could not reach the skill catalog. Check your connection and try again.")
        if query == "nomatches":
            return []
        if query == "slow":
            time.sleep(.8)
        return ss._normalize([
            {"name": query, "source": "example/skills", "skillId": query.replace(" ", "-"), "installs": 25},
            {"name": query + " tools", "source": "example/skills", "skillId": "tools", "installs": 10000},
            {"name": "<img src=x onerror=alert(1)>", "source": "example/skills", "skillId": "markup", "installs": None},
        ])

    screenshots = ROOT.parent / "screenshots"
    screenshots.mkdir(exist_ok=True)
    with patch.object(ss, "_fetch", side_effect=catalog):
        ss._cache.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), SearchPreviewHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_init_script("localStorage.setItem('data-bridge-token', 'search-browser-test');")
                context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in ("localhost", "127.0.0.1") else route.abort())
                page = context.new_page()
                errors, dialogs = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
                page.set_default_timeout(5000)
                page.goto(f"http://localhost:{server.server_port}/#upgrades", wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle")
                query = page.get_by_label("Keywords", exact=True)
                sort = page.get_by_label("Sort results", exact=True)
                status = page.locator("#skill-search-status")
                cards = page.locator(".skill-search-card")
                expect(query).to_be_visible()
                assert calls == [], "Opening Upgrades should not contact the public catalog"

                query.fill("pdf")
                query.press("Enter")
                expect(cards).to_have_count(3)
                expect(cards.first.locator("h4")).to_have_text("pdf")
                expect(cards.first).to_contain_text("25 installs")
                expect(status).to_contain_text("closest matches first")
                expect(cards.locator("img")).to_have_count(0)
                assert dialogs == [], "Catalog metadata executed as markup"
                expect(cards.last).to_contain_text("Install count unavailable")
                expect(cards.first.get_by_role("link", name="View pdf on skills.sh")).to_have_attribute("href", "https://skills.sh/example/skills/pdf")

                sort.select_option("installs")
                expect(cards.first.locator("h4")).to_have_text("pdf tools")
                expect(status).to_contain_text("most installed first")
                assert calls == ["pdf"], "Changing sort should reuse the cached catalog response"
                sort.select_option("relevance")
                expect(cards.first.locator("h4")).to_have_text("pdf")

                # These screenshots contain the explicit catalog test fixtures.
                page.screenshot(path=str(screenshots / "skill-search-desktop.png"), animations="disabled")
                page.set_viewport_size({"width": 390, "height": 844})
                expect(query).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile document overflows"
                assert page.locator(".settings-content").evaluate("el => el.scrollWidth <= el.clientWidth"), "Settings overflows horizontally"
                page.screenshot(path=str(screenshots / "skill-search-mobile.png"), animations="disabled")
                page.set_viewport_size({"width": 1440, "height": 1000})

                query.fill("nomatches")
                expect(status).to_contain_text("No skills found")
                expect(cards).to_have_count(0)
                query.fill("offline")
                query.press("Enter")
                expect(status).to_contain_text("Could not reach")
                expect(page.locator("#skill-search-submit")).to_have_text("Search")
                query.press("Enter")
                expect(cards).to_have_count(3)

                # Older searches must not replace results after the input changes.
                query.fill("slow")
                query.press("Enter")
                expect(status).to_contain_text('Searching skills for "slow"')
                query.fill("fresh")
                query.press("Enter")
                expect(cards.first.locator("h4")).to_have_text("fresh")
                page.wait_for_timeout(1000)
                expect(cards.first.locator("h4")).to_have_text("fresh")
                page.get_by_role("button", name="Clear", exact=True).click()
                expect(query).to_have_value("")
                expect(query).to_be_focused()
                expect(cards).to_have_count(0)
                expect(status).to_contain_text("Try a task or topic")
                before = len(calls)
                query.fill("x")
                query.press("Enter")
                expect(status).to_contain_text("at least 2")
                assert len(calls) == before
                assert errors == [], errors
                assert dialogs == [], dialogs
                browser.close()
                print("PASS: search, ranking, sort/cache, escaping, links, empty/error/retry, stale responses, clear, keyboard and mobile layouts")
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    main()
