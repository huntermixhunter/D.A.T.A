"""Shared accounts, catalogue and forum through two real local dashboard UIs."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketplace as mp
from community_server import create_server
from marketplace_preview import make_handler
from community_browser import PreviewServer, serve, same_origin_only, dialog
from playwright.sync_api import expect, sync_playwright
from test_marketplace import package

PASSWORD = "shared browser test password"


def api(page, path, data=None):
    return page.evaluate("""async ({path,data}) => {
      const r = await fetch('/marketplace/' + path, data === null ? {} : {
        method:'POST', headers:{'Content-Type':'application/json','X-Marketplace-Request':'1'}, body:JSON.stringify(data)});
      return {status:r.status, body:await r.json()};
    }""", {"path": path, "data": data})


def main():
    with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
        root = Path(directory)
        hub = mp.Marketplace(root / "hub", seed=False)
        stores = [mp.Marketplace(root / name, seed=False) for name in ("a", "b")]
        service = serve(create_server(("127.0.0.1", 0), hub), cleanup)
        cleanup.enter_context(patch.dict(os.environ, {"DATA_SERVICES_URL": service, "DATA_COMMUNITY_URL": ""}))
        servers = [PreviewServer(("127.0.0.1", 0), make_handler(store, hosted_forum=False)) for store in stores]
        origins = [serve(server, cleanup) for server in servers]
        with sync_playwright() as pw, ExitStack() as browser_cleanup:
            browser = pw.chromium.launch(headless=True)
            browser_cleanup.callback(browser.close)
            pages, errors, requests = [], [], []
            for origin in origins:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.route("**/*", same_origin_only(origin))
                context.add_init_script("localStorage.setItem('data-sidebar-view-v1:default', JSON.stringify({'nav:forum':true}));")
                page = context.new_page()
                page.set_default_timeout(6000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                pages.append(page)
            a, b = pages
            a.goto(origins[0] + "/#marketplace", wait_until="domcontentloaded")
            expect(a.locator("#marketplace-root")).to_contain_text("Shared marketplace.")
            assert not any("/forum/" in path for path in requests), "Unopened forum made requests"
            a.locator(".mp-head").get_by_role("button", name="Sign in", exact=True).click()
            dialog(a).get_by_role("button", name="Create a new account", exact=True).click()
            dialog(a).get_by_label("Display name", exact=True).fill("Shared Creator")
            dialog(a).get_by_label("Username", exact=True).fill("creator")
            dialog(a).get_by_label("Password", exact=True).fill(PASSWORD)
            dialog(a).get_by_role("button", name="Create account", exact=True).click()
            expect(a.locator(".mp-head").get_by_role("button", name="Shared Creator", exact=True)).to_be_visible()
            account = api(a, "session")["body"]["account"]

            a.get_by_role("button", name="+ Create listing", exact=True).click()
            dialog(a).get_by_label("Title", exact=True).fill("Shared Field Notes")
            dialog(a).get_by_label("Description", exact=True).fill("An original package shared between two test devices.")
            dialog(a).get_by_label("Extension package", exact=True).set_input_files({
                "name": "field-notes.zip", "mimeType": "application/zip", "buffer": package()})
            dialog(a).get_by_role("checkbox").check()
            dialog(a).get_by_role("button", name="Create listing", exact=True).click()
            expect(a.locator(".mp-card")).to_have_count(1)
            listing = api(a, "catalog")["body"]["listings"][0]
            print("PASS: shared registration and catalogue upload through the real UI.", flush=True)

            a.evaluate("showPanel('forum')")
            expect(a.locator(".forum-header").get_by_role("button", name="Shared Creator", exact=True)).to_be_visible()
            a.locator('.forum-header').get_by_role("button", name="+ New discussion", exact=True).click()
            dialog(a).get_by_label("Title", exact=True).fill("A shared workflow")
            dialog(a).get_by_label("Your post", exact=True).fill("The same online account created this listing and discussion.")
            dialog(a).get_by_role("button", name="Post discussion", exact=True).click()
            expect(a.locator(".forum-topic-header h2")).to_have_text("A shared workflow")

            b.goto(origins[1] + "/#forum", wait_until="domcontentloaded")
            expect(b.locator(".forum-mode")).to_have_text("Shared community")
            b.locator(".forum-header").get_by_role("button", name="Sign in", exact=True).click()
            expect(dialog(b).get_by_role("heading", name="Welcome back", exact=True)).to_be_visible()
            dialog(b).get_by_label("Username", exact=True).fill("creator")
            dialog(b).get_by_label("Password", exact=True).fill(PASSWORD)
            dialog(b).get_by_role("button", name="Sign in", exact=True).click()
            expect(b.locator(".forum-header").get_by_role("button", name="Shared Creator", exact=True)).to_be_visible()
            assert api(b, "session")["body"]["account"]["id"] == account["id"]
            b.get_by_role("button", name="A shared workflow", exact=True).click()
            expect(b.locator(".forum-post-body").first).to_contain_text("same online account")
            b.get_by_label("Your reply", exact=True).fill("Replying from the second device.")
            b.get_by_role("button", name="Post reply", exact=True).click()
            expect(b.locator(".forum-post")).to_have_count(2)
            print("PASS: Forum and Marketplace share login across two independent dashboards.", flush=True)

            b.evaluate("showPanel('marketplace')")
            expect(b.locator(".mp-card")).to_have_count(1)
            b.locator(".mp-card").get_by_role("button", name="View Shared Field Notes", exact=True).click()
            dialog(b).get_by_role("checkbox").check()
            dialog(b).get_by_role("button", name="Install extension", exact=True).click()
            expect(b.locator("#mp-status")).to_contain_text("installed")
            assert len(api(b, "runtime")["body"]["extensions"]) == 1
            assert api(a, "runtime")["body"]["extensions"] == []
            b.reload(wait_until="domcontentloaded")
            expect(b.locator("[data-marketplace-nav]")).to_have_count(1)

            # Settings uses the same cookie without contacting the Forum.
            b.evaluate("openSettingsTab('account')")
            settings = b.locator("#settings-account-root")
            expect(settings.locator(".settings-identity-name")).to_have_text("Shared Creator")
            expect(settings).to_contain_text("Account changes are saved online")
            card = settings.locator(".settings-card").filter(has=b.get_by_role("heading", name="Sign-in details", exact=True))
            card.get_by_label("Email", exact=True).fill("private@example.com")
            card.get_by_label("Current password", exact=True).fill(PASSWORD)
            card.get_by_role("button", name="UPDATE SIGN-IN DETAILS", exact=True).click()
            expect(settings.locator(".settings-identity-meta").first).to_contain_text("private@example.com")
            assert api(a, "account/me", {})["body"]["account"]["email"] == "private@example.com"
            print("PASS: installs persist on one device; Account settings update the shared identity.", flush=True)
            b.locator(".settings-close").click()

            screenshot = mp.ROOT.parent / "screenshots" / "shared-services-20261029.png"
            screenshot.parent.mkdir(exist_ok=True)
            b.evaluate("showPanel('marketplace')")
            b.screenshot(path=str(screenshot), full_page=True)
            b.set_viewport_size({"width": 390, "height": 844})
            expect(b.locator("#marketplace-root h1")).to_be_visible()
            assert b.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflow"

            # An outage cannot leave an installed component active after refresh.
            b.context.route("**/marketplace/runtime", lambda route: route.fulfill(status=502,
                content_type="application/json", body=json.dumps({"error": "Shared service unavailable"})))
            b.evaluate("MarketplaceUI.refresh()")
            expect(b.locator("#marketplace-root")).to_contain_text("Shared service unavailable")
            expect(b.locator("[data-marketplace-nav]")).to_have_count(0)
            b.context.unroute("**/marketplace/runtime")
            b.locator("#marketplace-root").get_by_role("button", name="Try again", exact=True).click()
            expect(b.locator("[data-marketplace-nav]")).to_have_count(1)
            b.locator(".mp-head").get_by_role("button", name="Shared Creator", exact=True).click()
            dialog(b).get_by_role("button", name="Sign out", exact=True).click()
            expect(b.locator(".mp-head").get_by_role("button", name="Sign in", exact=True)).to_be_visible()
            expect(b.locator("[data-marketplace-nav]")).to_have_count(0)
            assert api(b, "forum/session")["body"]["account"] is None
            assert api(a, "session")["body"]["account"]["id"] == account["id"]
            assert not errors, errors
            print("PASS: mobile layout, outage cleanup, retry, shared logout and zero page errors.", flush=True)


if __name__ == "__main__":
    main()
