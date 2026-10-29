"""Exercise shared forum UI through two isolated local dashboards and one hub."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from community_server import create_server
import marketplace as mp
from marketplace_preview import make_handler
from playwright.sync_api import expect, sync_playwright


PASSWORD = "shared browser test password"
TITLE = "A workflow shared across our dashboards"


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


def serve(server, cleanup):
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    cleanup.callback(stop)
    return f"http://127.0.0.1:{server.server_port}"


def dialog(page):
    return page.locator(".mp-dialog[open]").last


def header(page):
    return page.locator("#forum-root .forum-header")


def session(page, shared=True):
    path = "/marketplace/forum/session" if shared else "/marketplace/session"
    return page.evaluate("path => fetch(path).then(response => response.json())", path)["account"]


def same_origin_only(origin):
    def handle(route):
        target = urlsplit(route.request.url)
        if f"{target.scheme}://{target.netloc}" == origin:
            route.continue_()
        else:
            route.abort("blockedbyclient")
    return handle


def sign_up(page, username, name):
    header(page).get_by_role("button", name="Sign in", exact=True).click()
    expect(dialog(page).get_by_role("heading", name="Sign in to the community", exact=True)).to_be_visible()
    dialog(page).get_by_role("button", name="Create a new account", exact=True).click()
    expect(dialog(page).get_by_role("heading", name="Join the community", exact=True)).to_be_visible()
    dialog(page).get_by_label("Display name", exact=True).fill(name)
    dialog(page).get_by_label("Username", exact=True).fill(username)
    dialog(page).get_by_label("Password", exact=True).fill(PASSWORD)
    dialog(page).get_by_role("button", name="Create account", exact=True).click()
    expect(header(page).get_by_role("button", name=name, exact=True)).to_be_visible()
    return session(page)


def sign_out(page, name):
    header(page).get_by_role("button", name=name, exact=True).click()
    expect(dialog(page).get_by_role("heading", name="Your community account", exact=True)).to_be_visible()
    dialog(page).get_by_role("button", name="Sign out", exact=True).click()
    expect(header(page).get_by_role("button", name="Sign in", exact=True)).to_be_visible()
    assert session(page) is None, "Shared session survived sign-out"


def main():
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
        root = Path(directory)
        shared = mp.Marketplace(root / "hub", seed=False)
        local_a = mp.Marketplace(root / "dashboard-a", seed=False)
        local_b = mp.Marketplace(root / "dashboard-b", seed=False)
        origin = serve(create_server(("127.0.0.1", 0), shared), cleanup)
        previews = [
            PreviewServer(("127.0.0.1", 0), make_handler(local_a)),
            PreviewServer(("127.0.0.1", 0), make_handler(local_b)),
        ]
        origins = [serve(server, cleanup) for server in previews]
        cleanup.enter_context(patch.dict(os.environ, {"DATA_COMMUNITY_URL": origin}))

        # Existing local identities prove shared mode cannot silently import them.
        fixtures = []
        for store, username, name in ((local_a, "alice", "Local Alice"),
                                      (local_b, "local_bob", "Local Bob")):
            result, token = store.account_action("register", {
                "username": username, "name": name, "password": PASSWORD,
            }, None, "", "browser-fixture")
            fixtures.append((result["account"], token))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                pages, contexts, errors, account_requests = [], [], [], []
                for number, preview_origin in enumerate(origins):
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    contexts.append(context)
                    context.route("**/*", same_origin_only(preview_origin))
                    context.add_init_script(
                        "localStorage.setItem('data-sidebar-view-v1:default', "
                        "JSON.stringify({'nav:forum': true}));"
                    )
                    context.add_cookies([{
                        "name": f"data_market_session_{previews[number].server_port}",
                        "value": fixtures[number][1], "domain": "127.0.0.1", "path": "/marketplace",
                        "httpOnly": True, "secure": False, "sameSite": "Strict",
                    }])
                    page = context.new_page()
                    page.set_default_timeout(5000)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request:
                            account_requests.append(request.url)
                            if request.method == "POST" and "/account/" in request.url else None)
                    pages.append(page)
                a, b = pages

                failure = {"active": True, "requests": 0}

                def metadata(route):
                    if failure["active"]:
                        failure["requests"] += 1
                        route.fulfill(status=503, content_type="application/json",
                                      body='{"error":"Community metadata temporarily unavailable."}')
                    else:
                        route.continue_()

                contexts[0].route("**/marketplace/forum/meta", metadata)
                # The host dashboard polls continuously; forum DOM states are
                # the readiness signal for this focused browser integration.
                a.goto(origins[0] + "/#forum", wait_until="domcontentloaded")
                expect(a.locator("#forum-root .mp-note-warning")).to_contain_text("temporarily unavailable")
                expect(header(a).get_by_role("button", name="Sign in", exact=True)).to_be_disabled()
                expect(header(a).get_by_role("button", name="+ New discussion", exact=True)).to_be_disabled()
                expect(a.locator(".mp-dialog[open]")).to_have_count(0)
                assert failure["requests"], "Initial metadata failure was not exercised"
                failure["active"] = False
                a.get_by_role("button", name="Try again", exact=True).click()
                expect(a.locator(".forum-mode")).to_have_text("Shared community")
                expect(header(a).get_by_role("button", name="Sign in", exact=True)).to_be_enabled()
                expect(header(a).get_by_role("button", name="+ New discussion", exact=True)).to_be_enabled()
                assert session(a, shared=False)["id"] == fixtures[0][0]["id"]
                assert session(a) is None, "A local account was imported into the shared community"
                print("PASS: initial metadata failure disables actions; retry restores shared mode.", flush=True)

                alice = sign_up(a, "alice", "Community Alice")
                assert alice["id"] != fixtures[0][0]["id"], "Shared and local identities were conflated"
                assert session(a, shared=False)["name"] == "Local Alice"
                header(a).get_by_role("button", name="+ New discussion", exact=True).click()
                dialog(a).get_by_label("Title", exact=True).fill(TITLE)
                dialog(a).get_by_label("Category", exact=True).select_option("general")
                dialog(a).get_by_label("Your post", exact=True).fill(
                    "This discussion belongs to the shared community and appears on both installations."
                )
                dialog(a).get_by_role("button", name="Post discussion", exact=True).click()
                expect(a.locator(".forum-topic-header h2")).to_have_text(TITLE)
                expect(a.locator(".forum-post").first.get_by_role("button", name="Edit", exact=True)).to_be_visible()

                b.goto(origins[1] + "/#forum", wait_until="domcontentloaded")
                expect(b.locator(".forum-mode")).to_have_text("Shared community")
                expect(header(b).get_by_role("button", name="Sign in", exact=True)).to_be_visible()
                assert session(b) is None, "The second dashboard imported a local account"
                assert session(b, shared=False)["id"] == fixtures[1][0]["id"]
                b.get_by_role("button", name=TITLE, exact=True).click()
                expect(b.locator(".forum-post-body").first).to_contain_text("both installations")
                bruno = sign_up(b, "bruno", "Community Bruno")
                assert len({alice["id"], bruno["id"], fixtures[0][0]["id"], fixtures[1][0]["id"]}) == 4
                expect(b.locator(".forum-post").first.get_by_role("button", name="Edit", exact=True)).to_have_count(0)
                b.get_by_label("Your reply", exact=True).fill("I can reply from a separate dashboard installation.")
                b.get_by_role("button", name="Post reply", exact=True).click()
                expect(b.locator(".forum-post")).to_have_count(2)
                b.locator(".forum-post").last.get_by_role("button", name="Edit", exact=True).click()
                dialog(b).get_by_label("Your post", exact=True).fill("An edited reply from the second dashboard.")
                dialog(b).get_by_role("button", name="Save changes", exact=True).click()
                expect(b.locator(".forum-post-body").last).to_have_text("An edited reply from the second dashboard.")
                a.get_by_role("button", name="Back to discussions", exact=True).click()
                a.get_by_role("button", name=TITLE, exact=True).click()
                expect(a.locator(".forum-post")).to_have_count(2)
                expect(a.locator(".forum-post-body").last).to_have_text("An edited reply from the second dashboard.")
                expect(a.locator(".forum-post").first.get_by_role("button", name="Edit", exact=True)).to_be_visible()
                expect(a.locator(".forum-post").last.get_by_role("button", name="Edit", exact=True)).to_have_count(0)
                print("PASS: separate dashboard instances share discussions, replies and correct edit ownership.", flush=True)

                sign_out(b, "Community Bruno")
                header(b).get_by_role("button", name="Sign in", exact=True).click()
                expect(dialog(b).get_by_role("heading", name="Sign in to the community", exact=True)).to_be_visible()
                dialog(b).get_by_label("Username", exact=True).fill("alice")
                dialog(b).get_by_label("Password", exact=True).fill(PASSWORD)
                dialog(b).get_by_role("button", name="Sign in", exact=True).click()
                expect(header(b).get_by_role("button", name="Community Alice", exact=True)).to_be_visible()
                assert session(b)["id"] == alice["id"], "Shared account identity changed between installations"
                expect(b.locator(".forum-post").first.get_by_role("button", name="Edit", exact=True)).to_be_visible()
                expect(b.locator(".forum-post").last.get_by_role("button", name="Edit", exact=True)).to_have_count(0)
                sign_out(b, "Community Alice")
                assert session(a)["id"] == alice["id"], "Signing out another device revoked the first session"
                assert session(b, shared=False)["id"] == fixtures[1][0]["id"]
                assert session(a, shared=False)["id"] == fixtures[0][0]["id"]
                assert account_requests and all("/marketplace/forum/account/" in url for url in account_requests), (
                    "The shared account UI submitted to local Marketplace authentication", account_requests)
                cookies = contexts[0].cookies()
                shared_cookie = next(item for item in cookies if item["name"] ==
                                     f"data_forum_session_{previews[0].server_port}")
                assert shared_cookie["httpOnly"] and shared_cookie["path"] == "/marketplace/forum"
                for store, expected in ((shared, 2), (local_a, 1), (local_b, 1)):
                    with store.connection() as db:
                        assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == expected
                assert not errors, errors
                elapsed = time.monotonic() - started
                assert elapsed < 60, f"Shared browser check exceeded 60 seconds: {elapsed:.1f}s"
                print(f"PASS: shared login across devices, isolated logout, local account isolation and no page errors ({elapsed:.1f}s).",
                      flush=True)
            finally:
                browser.close()


if __name__ == "__main__":
    main()
