"""Local browser integration check. Requires an existing Playwright/Chromium install.

Run from the repository root: python dashboard/tests/marketplace_browser.py
Uses an isolated temporary database and real HTTP endpoints. No AI requests.
"""
import base64
import json
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketplace as mp
from marketplace_preview import make_handler
from playwright.sync_api import sync_playwright, expect

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
BRIDGE_TOKEN = "isolated-test-token"


def main():
    screenshots = mp.ROOT.parent / "screenshots"
    screenshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store = mp.Marketplace(directory)
        class ProtectedHandler(make_handler(store)):
            def _check_auth(self):
                if self.headers.get("X-Data-Token") == BRIDGE_TOKEN:
                    return True
                payload = b'{"error":"auth_required"}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return False

        server = ThreadingHTTPServer(("127.0.0.1", 0), ProtectedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://localhost:{server.server_address[1]}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_init_script(f"localStorage.setItem('data-bridge-token', '{BRIDGE_TOKEN}');")
                context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in ("localhost", "127.0.0.1") else route.abort())
                page = context.new_page()
                page.set_default_timeout(7000)
                errors, dialogs, media = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
                page.on("response", lambda response: media.append(response.status) if "/marketplace/media/" in response.url else None)
                # The host app continuously polls status. Use DOM and real API
                # assertions after DOMContentLoaded instead of networkidle.
                page.goto(origin + "/#marketplace", wait_until="domcontentloaded")
                expect(page.locator(".mp-card")).to_have_count(15)
                expect(page.locator(".mp-badge-reviewed")).to_have_count(5)
                page.screenshot(path=str(screenshots / "marketplace-desktop.png"), full_page=True, animations="disabled")
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.get_by_role("heading", name="Make DATA your own.")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows"
                page.screenshot(path=str(screenshots / "marketplace-mobile.png"), full_page=True, animations="disabled")
                page.set_viewport_size({"width": 1440, "height": 1000})

                def dialog():
                    return page.locator(".mp-dialog[open]").last

                def signup(username, name):
                    page.get_by_role("button", name="Sign in", exact=True).click()
                    dialog().get_by_role("button", name="Create a new account").click()
                    current = dialog()
                    current.get_by_label("Display name", exact=True).fill(name)
                    current.get_by_label("Profile image (optional)").set_input_files({"name": "profile.png", "mimeType": "image/png", "buffer": PNG})
                    current.get_by_label("Username", exact=True).fill(username)
                    current.get_by_label("Password", exact=True).fill("local browser test password")
                    current.get_by_role("button", name="Create account", exact=True).click()
                    expect(page.locator("#marketplace-root").get_by_role("button", name=name, exact=True)).to_be_visible()
                    expect(page.locator("#marketplace-root .mp-head img")).to_have_js_property("naturalWidth", 1)

                def signout(name):
                    page.locator("#marketplace-root").get_by_role("button", name=name, exact=True).click()
                    dialog().get_by_role("button", name="Sign out", exact=True).click()
                    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()

                def open_listing(title):
                    page.get_by_role("button", name="View " + title, exact=True).click()
                    expect(dialog().get_by_role("heading", name=title, exact=True)).to_be_visible()

                def install(title, update=False):
                    open_listing(title)
                    current = dialog()
                    current.get_by_role("checkbox").check()
                    current.get_by_role("button", name="Install update" if update else "Install extension", exact=True).click()
                    expect(page.locator(".mp-dialog[open]")).to_have_count(0)
                    expect(page.locator("#mp-status")).to_contain_text(title + " installed")

                def page_package(version):
                    files = mp.parse_package(mp.starter_package("page"))["files"]
                    manifest = json.loads(files["manifest.json"])
                    manifest["version"] = version
                    files["manifest.json"] = json.dumps(manifest)
                    return {"name": "studio-notes.zip", "mimeType": "application/zip", "buffer": mp.make_archive(files)}

                signup("maker", "Browser Maker")
                page.get_by_role("button", name="+ Create listing", exact=True).click()
                current = dialog()
                current.get_by_label("Title", exact=True).fill("Studio Notes")
                current.get_by_label("Description", exact=True).fill("A community sidebar checklist for this browser test.")
                current.get_by_label("Listing image (optional)").set_input_files({"name": "cover.png", "mimeType": "image/png", "buffer": PNG})
                current.get_by_label("Extension package").set_input_files(page_package("1.0.0"))
                current.get_by_role("checkbox").check()
                current.get_by_role("button", name="Create local listing", exact=True).click()
                expect(page.get_by_role("button", name="View Studio Notes")).to_be_visible()
                expect(page.locator(".mp-card .mp-art img")).to_have_js_property("naturalWidth", 1)
                install("Studio Notes")
                page.locator("#sidebar-navigation").get_by_role("button", name="My page", exact=True).click()
                page.get_by_label("My first checklist item", exact=True).check()
                page.locator("#sidebar-navigation").get_by_role("button", name="MARKETPLACE", exact=True).click()
                page.get_by_role("button", name="My listings", exact=True).click()
                page.get_by_role("button", name="Upload new version", exact=True).click()
                dialog().get_by_label("Extension package").set_input_files(page_package("1.1.0"))
                dialog().get_by_role("checkbox").check()
                with page.expect_response(lambda response: response.url == origin + "/marketplace/listings" and response.request.method == "POST") as updated:
                    dialog().get_by_role("button", name="Submit new version", exact=True).click()
                assert updated.value.status == 200, updated.value.json()
                try:
                    expect(page.get_by_role("button", name="Update available", exact=True)).to_be_visible()
                except AssertionError:
                    result = updated.value.json()
                    print({"latest": result["latest"], "installation": result["installation"], "ui": page.locator("#marketplace-root").inner_text()}, flush=True)
                    raise
                install("Studio Notes", update=True)
                open_listing("Studio Notes")
                dialog().get_by_text("Version history and rollback", exact=True).click()
                dialog().get_by_role("button", name="Restore v1.0.0", exact=True).click()
                dialog().get_by_role("checkbox").check()
                dialog().get_by_role("button", name="Restore version", exact=True).click()
                expect(page.locator(".mp-dialog[open]")).to_have_count(0)
                signout("Browser Maker")
                expect(page.locator("[data-marketplace-nav]")).to_have_count(0)

                signup("reader", "Browser Reader")
                page.get_by_role("button", name="Explore", exact=True).click()
                install("Studio Notes")
                page.locator("#sidebar-navigation").get_by_role("button", name="My page", exact=True).click()
                expect(page.get_by_label("My first checklist item", exact=True)).not_to_be_checked()
                page.locator("#sidebar-navigation").get_by_role("button", name="MARKETPLACE", exact=True).click()
                open_listing("Studio Notes")
                dialog().get_by_label("Your review", exact=True).fill("Useful checklist. <script>window.badReview=true</script>")
                dialog().get_by_role("button", name="Post a review", exact=True).click()
                expect(dialog().locator(".mp-review-text")).to_contain_text("<script>")
                assert page.evaluate("window.badReview === undefined"), "Review executed as HTML"
                dialog().get_by_role("button", name="Close dialog").click()
                install("Text Workshop")
                page.locator("#sidebar-navigation").get_by_role("button", name="Text Workshop", exact=True).click()
                page.get_by_label("Text Workshop input", exact=True).fill("one two three")
                page.get_by_role("button", name="Count words", exact=True).click()
                expect(page.locator(".panel.active pre")).to_contain_text("3 words")
                page.locator("#sidebar-navigation").get_by_role("button", name="MARKETPLACE", exact=True).click()
                install("Midnight")
                token = page.evaluate("document.body.style.getPropertyValue('--data-bg')")
                assert token, "Theme did not apply"
                page.goto(origin + "/", wait_until="domcontentloaded")
                expect(page.locator("[data-marketplace-nav]")).to_have_count(2)
                expect(page.locator("body")).to_have_css("--data-bg", token)
                page.locator("#sidebar-navigation").get_by_role("button", name="MARKETPLACE", exact=True).click()
                page.get_by_role("button", name="Installed", exact=True).click()
                workshop = page.locator(".mp-card").filter(has=page.get_by_role("heading", name="Text Workshop", exact=True))
                workshop.get_by_role("button", name="Disable", exact=True).click()
                expect(page.locator("[data-marketplace-nav]")).to_have_count(1)
                workshop.get_by_role("button", name="Uninstall", exact=True).click()
                dialog().get_by_role("button", name="Uninstall extension", exact=True).click()
                expect(page.get_by_role("button", name="View Text Workshop")).to_have_count(0)
                signout("Browser Reader")
                assert page.evaluate("document.body.style.getPropertyValue('--data-bg') === ''")
                page.get_by_role("button", name="Sign in", exact=True).click()
                dialog().get_by_label("Username", exact=True).fill("reader")
                dialog().get_by_label("Password", exact=True).fill("wrong long password")
                dialog().get_by_role("button", name="Sign in", exact=True).click()
                expect(dialog().locator(".mp-error")).to_contain_text("incorrect")
                assert not dialogs, f"Unexpected native prompts: {dialogs}"
                assert not errors, f"Browser errors: {errors}"
                assert media and all(status == 200 for status in media), f"Authenticated media failed: {media}"
                print("PASS: desktop/mobile, accounts/images, upload/update, install, sidebar/checklist isolation, review escaping, text tool, theme reload, rollback, disable/uninstall, logout and wrong-password handling.", flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
