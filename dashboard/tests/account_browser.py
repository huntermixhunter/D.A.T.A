"""Settings > Account in a real browser. Requires an existing Playwright/Chromium.

Run from the repository root: python dashboard/tests/account_browser.py

Drives the actual bridge request handler, so the page under test talks to the
same endpoints the Captain's dashboard does. Every file it touches lives in a
temporary directory: a throwaway account database, a throwaway users/ registry
and a throwaway ~/.claude/agents. No AI provider is started and no real profile,
memory file or conversation history is read or written.
"""
import base64
import json
import shutil
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketplace as mp
from playwright.sync_api import sync_playwright, expect

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
PASSWORD = "a long local test password"
NEXT_PASSWORD = "a second long test password"
EVM = "0x52908400098527886E0F7030069857D2E4169EE7"


def main():
    import bridge_server as bs

    screenshots = mp.ROOT.parent / "screenshots"
    screenshots.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp())
    home = temp / "home"
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "agents" / "data.md").write_text("# Data\n\nThe operations officer.\n", encoding="utf-8")
    (home / ".claude" / "agents" / "vector.md").write_text("# Vector\n\nThe reviewer.\n", encoding="utf-8")
    store = mp.Marketplace(temp / "market", seed=False)

    patches = [
        mock.patch.object(bs, "USERS_DIR", temp / "users"),
        mock.patch.object(bs, "USERS_REGISTRY_FILE", temp / "users" / "users.json"),
        mock.patch.object(bs, "AGENT_AVATAR_DIR", temp / "users" / "agent_avatars"),
        mock.patch.object(bs, "_USERS", {"captain": {"id": "captain", "name": "Captain", "rank": "Captain", "accent": "cyan"}}),
        mock.patch.object(bs, "_ACTIVE_USER", "captain"),
        mock.patch.object(bs, "DATA_BRIDGE_TOKEN", ""),
        mock.patch.object(bs, "_update_check", return_value={"available": False}),
        mock.patch.object(bs.Path, "home", staticmethod(lambda: home)),
        mock.patch.object(mp, "_store", store),
    ]
    for patch in patches:
        patch.start()
    (temp / "users" / "captain").mkdir(parents=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), bs.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://localhost:{server.server_address[1]}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.route("**/*", lambda route: route.continue_()
                          if urlsplit(route.request.url).hostname in ("localhost", "127.0.0.1") else route.abort())
            page = context.new_page()
            page.set_default_timeout(10000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.dismiss())
            page.goto(origin + "/", wait_until="domcontentloaded")

            def account():
                return page.locator("#settings-account-root")

            def card_of(title):
                return account().locator(f".settings-card:has(.settings-h:text-is('{title}'))")

            def field(card, label):
                return card.locator(f".settings-field:has(label:text-is('{label}')) .settings-input")

            def status_of(title):
                return card_of(title).locator(".settings-status")

            page.evaluate("openSettingsTab('account')")
            expect(page.locator(".settings-pane[data-pane='account']")).to_have_class("settings-pane active")

            # ── Signed out: create the account ──────────────────────────────
            expect(account().get_by_role("heading", name="Sign in to DATA")).to_be_visible()
            account().get_by_role("button", name="CREATE ACCOUNT").first.click()
            expect(account().get_by_role("heading", name="Create your DATA account")).to_be_visible()
            signup = card_of("Create your DATA account")
            field(signup, "Username").fill("gyani")
            field(signup, "Password").fill(PASSWORD)
            field(signup, "Nickname").fill("Gyani")
            field(signup, "Email (optional)").fill("gyani@example.com")
            signup.locator("button.settings-save").click()
            expect(account().locator(".settings-identity-name")).to_have_text("Gyani")
            # The sign-in card is gone, so its confirmation moves to the card
            # that replaced it rather than disappearing.
            expect(status_of("Nickname and picture")).to_contain_text("Account created")
            expect(account().locator(".settings-identity-meta").first).to_contain_text("@gyani")
            assert store.account_action("login", {"username": "gyani", "password": PASSWORD}, None, "", "check")[1]

            # The nickname reached the dashboard profile, the header pill and
            # the block handed to the model.
            expect(page.locator("#captain-name")).to_have_text("GYANI")
            assert "**Captain Gyani**" in bs._active_captain_block(), bs._active_captain_block()

            # ── Rename, and give the Captain a picture ──────────────────────
            card = card_of('Nickname and picture')
            card.locator(".settings-input").first.fill("Captain Mix")
            card.locator(".settings-file").set_input_files(
                files=[{"name": "me.png", "mimeType": "image/png", "buffer": PNG}])
            # The chosen picture previews before it is saved, and says so.
            expect(card.locator(".settings-identity-av img")).to_be_visible()
            expect(status_of("Nickname and picture")).to_contain_text("press SAVE")
            card.locator("button.settings-save").click()
            expect(status_of("Nickname and picture")).to_have_class("settings-status ok")
            assert "**Captain Captain Mix**" in bs._active_captain_block()
            profile = json.loads((temp / "users" / "users.json").read_text(encoding="utf-8"))
            assert profile["users"]["captain"]["name"] == "Captain Mix", profile
            assert (temp / "users" / "captain" / "avatar.png").read_bytes() == PNG
            expect(page.locator("#captain-avatar img")).to_be_visible()

            # ── Wallet addresses: a typo is refused, a good one is kept ─────
            wallets = card_of('Crypto wallet addresses')
            wallets.get_by_role("button", name="+ ADD ADDRESS").click()
            wallets.locator(".settings-wallet-chain").select_option("ethereum")
            wallets.locator(".settings-wallet-address").fill(EVM[:-1])
            wallets.locator(".settings-wallet-label").fill("Payouts")
            wallets.locator(".settings-input[type='password']").fill(PASSWORD)
            wallets.locator("button.settings-save").click()
            expect(status_of("Crypto wallet addresses")).to_contain_text("not a valid Ethereum address")
            wallets.locator(".settings-wallet-address").fill(EVM)
            wallets.locator(".settings-input[type='password']").fill(PASSWORD)
            wallets.locator("button.settings-save").click()
            expect(status_of("Crypto wallet addresses")).to_contain_text("Saved 1 address")
            account_row = store.authenticate(
                store.account_action("login", {"username": "gyani", "password": PASSWORD}, None, "", "check")[1])
            saved = store.account_action("me", {}, account_row, "", "check")[0]["account"]
            assert saved["wallets"] == [{"chain": "ethereum", "address": EVM, "label": "Payouts"}], saved

            page.locator(".settings-content").evaluate("el => el.scrollTop = 0")
            page.screenshot(path=str(screenshots / "account-desktop.png"), full_page=True, animations="disabled")
            page.locator(".settings-content").evaluate("el => el.scrollTop = el.scrollHeight")
            page.screenshot(path=str(screenshots / "account-desktop-wallets.png"), full_page=True, animations="disabled")

            # ── Sign-in details: the wrong password changes nothing ─────────
            details = card_of('Sign-in details')
            field(details, 'Username').fill("gyani.mix")
            field(details, 'Current password').fill("wrong password!!")
            details.locator("button.settings-save").click()
            expect(status_of("Sign-in details")).to_have_class("settings-status err")
            assert store.authenticate(
                store.account_action("login", {"username": "gyani", "password": PASSWORD}, None, "", "check")[1]), \
                "the username must not have changed"

            field(details, 'New password').fill(NEXT_PASSWORD)
            field(details, 'Repeat new password').fill(NEXT_PASSWORD + "typo")
            field(details, 'Current password').fill(PASSWORD)
            details.locator("button.settings-save").click()
            expect(status_of("Sign-in details")).to_contain_text("do not match")

            field(details, 'Repeat new password').fill(NEXT_PASSWORD)
            field(details, 'Current password').fill(PASSWORD)
            details.locator("button.settings-save").click()
            expect(status_of("Sign-in details")).to_have_class("settings-status ok")
            expect(account().locator(".settings-identity-meta").first).to_contain_text("@gyani.mix")
            assert store.account_action("login", {"username": "gyani.mix", "password": NEXT_PASSWORD}, None, "", "check")[1]

            # ── Officer pictures live with the other agent settings ─────────
            page.evaluate("settingsTab('crew')")
            crew = page.locator("#settings-crew-avatar")
            expect(crew.locator(".settings-identity-av")).to_be_visible()
            crew.locator(".settings-file").set_input_files(
                files=[{"name": "officer.png", "mimeType": "image/png", "buffer": PNG}])
            expect(crew.locator(".settings-status.ok")).to_contain_text("Picture saved")
            stored = sorted(p.name for p in (temp / "users" / "agent_avatars").iterdir())
            assert stored and stored[0].endswith(".png"), stored
            expect(crew.locator(".settings-identity-av img")).to_be_visible()
            crew.get_by_role("button", name="REMOVE PICTURE").click()
            expect(crew.locator(".settings-status.ok")).to_contain_text("Picture removed")
            assert not list((temp / "users" / "agent_avatars").iterdir())
            page.screenshot(path=str(screenshots / "account-crew-picture.png"), full_page=True, animations="disabled")

            # ── Sign out keeps the dashboard identity ───────────────────────
            page.evaluate("settingsTab('account')")
            account().get_by_role("button", name="SIGN OUT").click()
            expect(account().get_by_role("heading", name="Sign in to DATA")).to_be_visible()
            expect(status_of("Sign in to DATA")).to_contain_text("Signed out")
            expect(page.locator("#captain-name")).to_have_text("CAPTAIN MIX")
            expect(card_of('Nickname and picture').locator('.settings-input').first).to_have_value("Captain Mix")

            # ── Both themes, and the phone layout ───────────────────────────
            page.evaluate("settingsSetTheme('cyber')")
            expect(account().get_by_role("heading", name="Sign in to DATA")).to_be_visible()
            page.screenshot(path=str(screenshots / "account-cyber.png"), full_page=True, animations="disabled")
            page.evaluate("settingsSetTheme('minimal')")

            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate("settingsTab('account')")
            expect(account().get_by_role("heading", name="Sign in to DATA")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Account pane overflows on mobile"
            page.screenshot(path=str(screenshots / "account-mobile.png"), full_page=True, animations="disabled")

            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        for patch in patches:
            patch.stop()
        shutil.rmtree(temp, ignore_errors=True)
    print("Account browser checks passed. Screenshots in", screenshots)


if __name__ == "__main__":
    main()
