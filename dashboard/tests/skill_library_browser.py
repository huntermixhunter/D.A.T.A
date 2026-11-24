"""Exercise Settings > Upgrades > Skill Library in the real dashboard.

Run: python dashboard/tests/skill_library_browser.py
Uses installed Playwright/Chromium. Installs go to a temp folder (never the
real skill folders) from the local skills_library/ copy; no network.
"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_skills  # noqa: E402
import skill_library as sl  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = {
    "/": ("index.html", "text/html"),
    "/index.html": ("index.html", "text/html"),
    "/app.js": ("app.js", "application/javascript"),
    "/theme.css": ("theme.css", "text/css"),
    "/skill_search.css": ("skill_search.css", "text/css"),
    "/skill_library.js": ("skill_library.js", "application/javascript"),
    "/skill_library.css": ("skill_library.css", "text/css"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _serve(self):
        try:
            if sl.handle_http(self):
                return
            path = urlsplit(self.path).path
            if self.command == "GET" and path in STATIC:
                name, mime = STATIC[path]
                body, status = (ROOT / name).read_bytes(), 200
            else:
                status, mime = 404, "application/json"
                body = json.dumps({"error": "This test only serves the skill library."}).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    do_GET = _serve
    do_POST = _serve


def main():
    tmp = tempfile.TemporaryDirectory()
    claude, hermes = Path(tmp.name, "claude"), Path(tmp.name, "hermes")
    # A skill the user already has: the library must leave it alone.
    (claude / "codex").mkdir(parents=True)
    (claude / "codex" / "SKILL.md").write_text("my own codex skill", encoding="utf-8")
    screen = {"calls": 0}

    def fake_start():
        screen["calls"] += 1
        with sl._screen_lock:
            sl._screen.update(running=False, ok=True, message="Screen control is installed. (test)")
        return {"ok": True, "running": False, "message": "Screen control is installed. (test)"}

    shots = ROOT.parent / "screenshots"
    shots.mkdir(exist_ok=True)
    with patch.object(install_skills, "CLAUDE_SKILLS_DIR", claude), \
         patch.object(install_skills, "HERMES_SKILLS_DIR", hermes), \
         patch.object(sl, "_screen_available", side_effect=lambda: screen["calls"] > 0), \
         patch.object(sl, "start_screen_control", side_effect=fake_start):
        assert sl.LOCAL_LIBRARY.is_dir(), "Run tools/build_skill_library.py first"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.route("**/*", lambda route: route.continue_()
                              if urlsplit(route.request.url).hostname in ("localhost", "127.0.0.1")
                              else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.set_default_timeout(8000)
                page.goto(f"http://localhost:{server.server_port}/#upgrades", wait_until="domcontentloaded")
                page.evaluate("openSettingsTab('upgrades')")
                page.evaluate("window.SkillLibraryUI && window.SkillLibraryUI.load(true)")
                status = page.locator("#skill-library-status")
                cards = page.locator(".skill-library-card")
                expect(status).to_contain_text("optional skills")
                total = cards.count()
                assert total >= 70, f"expected the full catalog, got {total}"

                # Mac-only notice: screen control missing -> install button.
                expect(page.locator("#skill-library-screen")).to_be_visible()
                page.get_by_role("button", name="Install screen control").click()
                expect(page.locator("#skill-library-screen-text")).to_contain_text("installed")

                codex = cards.filter(has=page.locator("h5", has_text="codex")).first
                expect(codex).to_contain_text("Already on this computer")
                expect(codex.get_by_role("button")).to_have_count(0)

                page.get_by_label("Filter", exact=True).fill("excalidraw")
                expect(cards).to_have_count(1)
                cards.first.get_by_role("button", name="Install excalidraw").click()
                expect(cards.first).to_contain_text("Installed")
                expect(status).to_contain_text("Installed excalidraw")
                assert (claude / "excalidraw" / "SKILL.md").exists()
                assert (claude / "excalidraw" / ".data-library.json").exists()

                page.get_by_label("Filter", exact=True).fill("")
                page.get_by_label("Category", exact=True).select_option(label="AI models (advanced)")
                vllm = cards.filter(has=page.locator("h5", has_text="vllm")).first
                expect(vllm).to_contain_text("Needs:")
                vllm.get_by_role("button", name="Install vllm").click()
                expect(vllm).to_contain_text("Installed")
                assert (hermes / "mlops" / "vllm").is_dir(), "nested Hermes skill not flattened"

                page.get_by_label("Category", exact=True).select_option("")
                page.get_by_label("Show", exact=True).select_option("installed")
                expect(cards).to_have_count(2)
                page.locator("#skill-library").scroll_into_view_if_needed()
                page.screenshot(path=str(shots / "skill-library-desktop.png"), animations="disabled")

                cards.filter(has=page.locator("h5", has_text="excalidraw")).first \
                    .get_by_role("button", name="Remove excalidraw").click()
                expect(cards).to_have_count(1)
                assert not (claude / "excalidraw").exists()
                assert (claude / "codex" / "SKILL.md").read_text(encoding="utf-8") == "my own codex skill"

                page.get_by_label("Show", exact=True).select_option("all")
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#skill-library").scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflows"
                page.screenshot(path=str(shots / "skill-library-mobile.png"), animations="disabled")
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            tmp.cleanup()
    print("skill library browser check passed")


if __name__ == "__main__":
    main()
