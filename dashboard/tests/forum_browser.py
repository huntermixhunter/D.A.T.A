"""Exercise the real forum UI with two accounts, offline retries and both themes."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketplace as mp
from forum import get_forum
from marketplace_preview import make_handler
from playwright.sync_api import expect, sync_playwright


def main():
    shots = mp.ROOT.parent / "screenshots"
    shots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DATA_COMMUNITY_URL": ""}):
        store = mp.Marketplace(directory)
        forum = get_forum(store)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://localhost:{server.server_port}"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in ("localhost", "127.0.0.1") else route.abort())
                page = context.new_page()
                page.set_default_timeout(6000)
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url) if "/marketplace/forum/" in request.url else None)
                page.goto(origin + "/#marketplace", wait_until="domcontentloaded")
                expect(page.locator(".mp-card")).to_have_count(15)
                expect(page.locator('[data-nav-panel="forum"]')).not_to_be_visible()
                assert not requests, "Forum fetched while still disabled"

                def dialog():
                    return page.locator(".mp-dialog[open]").last

                def enable():
                    page.locator('#settings-btn').click()
                    page.locator('.settings-tab[data-tab="view"]').click()
                    control = page.get_by_role("checkbox", name="Community Forum", exact=True)
                    expect(control).not_to_be_checked()
                    control.check()
                    page.locator('.settings-close').click()
                    page.locator('[data-nav-panel="forum"]').click()
                    expect(page.get_by_role("heading", name="People behind the dashboards.")).to_be_visible()
                    expect(page.locator('.forum-connection')).to_contain_text('Local preview')

                def signup(username, name):
                    page.locator('#forum-root').get_by_role('button', name='Sign in', exact=True).click()
                    dialog().get_by_role('button', name='Create a new account', exact=True).click()
                    dialog().get_by_label('Display name', exact=True).fill(name)
                    dialog().get_by_label('Username', exact=True).fill(username)
                    dialog().get_by_label('Password', exact=True).fill('browser-only test password')
                    dialog().get_by_role('button', name='Create account', exact=True).click()
                    expect(page.locator('#forum-root').get_by_role('button', name=name, exact=True)).to_be_visible()

                def create(title, body, category='general'):
                    page.locator('#forum-root').get_by_role('button', name='+ New discussion', exact=True).first.click()
                    dialog().get_by_label('Title', exact=True).fill(title)
                    dialog().get_by_label('Category', exact=True).select_option(category)
                    dialog().get_by_label('Your post', exact=True).fill(body)
                    dialog().get_by_role('button', name='Post discussion', exact=True).click()
                    expect(page.locator('.forum-topic-header h2')).to_have_text(title)

                enable()
                signup('alice', 'Alice Example')
                print('PASS: settings opt-in and shared local account', flush=True)
                # Account is the same one used by the existing Marketplace.
                page.locator('[data-nav-panel="marketplace"]').click()
                expect(page.locator('#marketplace-root').get_by_role('button', name='Alice Example', exact=True)).to_be_visible()
                page.locator('[data-nav-panel="forum"]').click()
                create('What does your everyday DATA setup look like?', 'I keep notes, tasks, and my favorite tools together. What works for you?')
                page.get_by_label('Your reply', exact=True).fill('Draft that belongs only to the first discussion')
                create('A space for weekend conversations', 'What are you making away from the screen?', 'off-topic')
                expect(page.get_by_label('Your reply', exact=True)).to_have_value('')
                page.get_by_role('button', name='Back to discussions', exact=True).click()
                page.get_by_role('button', name='What does your everyday DATA setup look like?', exact=True).click()
                expect(page.get_by_label('Your reply', exact=True)).to_have_value('Draft that belongs only to the first discussion')
                page.get_by_label('Your reply', exact=True).fill('A reply saved exactly once, even after a lost response.')

                lost = {"done": False}
                def lose_once(route):
                    if route.request.method == 'POST' and route.request.url.endswith('/reply') and not lost['done']:
                        lost['done'] = True
                        route.fetch()  # Commit on the real server, then simulate a disconnected response.
                        route.abort('failed')
                    else:
                        route.continue_()
                context.route('**/marketplace/forum/threads/*/reply', lose_once)
                page.get_by_role('button', name='Post reply', exact=True).click()
                expect(page.locator('.forum-reply .mp-error')).to_contain_text('Could not reach')
                page.get_by_role('button', name='Back to discussions', exact=True).click()
                page.get_by_role('button', name='What does your everyday DATA setup look like?', exact=True).click()
                expect(page.get_by_label('Your reply', exact=True)).to_have_value('A reply saved exactly once, even after a lost response.')
                page.get_by_role('button', name='Post reply', exact=True).click()
                expect(page.locator('.forum-post')).to_have_count(2)
                expect(page.get_by_label('Your reply', exact=True)).to_have_value('')

                # Plain text must never become a script or an image.
                print('PASS: isolated drafts and interrupted-reply retry', flush=True)
                page.get_by_label('Your reply', exact=True).fill('<img src=x onerror="window.FORUM_XSS=true">')
                page.get_by_role('button', name='Post reply', exact=True).click()
                expect(page.locator('.forum-post')).to_have_count(3)
                assert not page.evaluate('Boolean(window.FORUM_XSS)')
                expect(page.locator('.forum-post-body img')).to_have_count(0)
                page.locator('.forum-post').last.get_by_role('button', name='Edit', exact=True).click()
                dialog().get_by_label('Your post', exact=True).fill('An edited reply')
                dialog().get_by_role('button', name='Save changes', exact=True).click()
                expect(page.locator('.forum-post-body').last).to_have_text('An edited reply')
                page.locator('.forum-post').last.get_by_role('button', name='Delete', exact=True).click()
                dialog().get_by_role('button', name='Delete', exact=True).click()
                expect(page.locator('.forum-post-body').last).to_have_text('This post was deleted by its author.')

                page.locator('#forum-root').get_by_role('button', name='Alice Example', exact=True).click()
                dialog().get_by_role('button', name='Sign out', exact=True).click()
                expect(page.get_by_role('button', name='Sign in to reply', exact=True)).to_be_visible()
                signup('bobby', 'Bobby Example')
                expect(page.locator('.forum-post').first.get_by_role('button', name='Edit', exact=True)).to_have_count(0)
                page.locator('.forum-post').first.get_by_role('button', name='Like', exact=True).click()
                expect(page.locator('.forum-post').first.get_by_role('button', name='Liked 1', exact=True)).to_be_visible()
                page.get_by_role('button', name='Follow discussion', exact=True).click()
                expect(page.get_by_role('button', name='Following', exact=True)).to_be_visible()
                page.locator('.forum-post').first.get_by_role('button', name='Report', exact=True).click()
                dialog().get_by_label('What should the moderator know?', exact=True).fill('A test report for the moderator queue.')
                dialog().get_by_role('button', name='Send report', exact=True).click()
                expect(page.locator('#forum-status')).to_have_text('Report sent to the moderator.')
                print('PASS: text safety, ownership, editing, deletion, likes, follows and reports', flush=True)

                # Add fixture discussions to an isolated test database for layout coverage.
                with store.connection() as db:
                    owner = store.public_account(db.execute("SELECT * FROM accounts WHERE username='alice'").fetchone())
                for cat, title in [('help', 'Which workflow helped you get started?'), ('ideas', 'An idea for reusable project templates'), ('showcase', 'A small dashboard improvement that saved me time'), ('extensions', 'Share your favorite theme combinations')]:
                    forum.create({'title': title, 'body': 'Browser-test example discussion, stored only in a temporary database.', 'category': cat, 'request_id': secrets.token_hex(16)}, owner)
                page.get_by_role('button', name='Back to discussions', exact=True).click()
                expect(page.locator('.forum-thread-row')).to_have_count(6)
                page.get_by_role('button', name='Following', exact=True).click()
                expect(page.locator('.forum-thread-row')).to_have_count(1)
                page.get_by_role('button', name='Everyone', exact=True).click()
                page.get_by_role('searchbox', name='Search discussions').fill('weekend')
                page.get_by_role('button', name='Search', exact=True).click()
                expect(page.locator('.forum-thread-row')).to_have_count(1)
                page.get_by_role('searchbox', name='Search discussions').fill('')
                page.get_by_role('button', name='Search', exact=True).click()
                expect(page.locator('.forum-thread-row')).to_have_count(6)

                for theme in ('minimal', 'cyber'):
                    page.evaluate('t => settingsSetTheme(t)', theme)
                    page.screenshot(path=str(shots / f'forum-{theme}-desktop.png'), animations='disabled')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile document overflow'
                    assert page.locator('#panel-forum').evaluate('el => el.scrollWidth <= el.clientWidth'), 'Mobile forum overflow'
                    page.screenshot(path=str(shots / f'forum-{theme}-mobile.png'), animations='disabled')
                    page.set_viewport_size({'width': 1440, 'height': 1000})
                page.evaluate("settingsSetTheme('minimal')")
                page.locator('#settings-btn').click()
                page.locator('.settings-tab[data-tab="view"]').click()
                page.screenshot(path=str(shots / 'forum-settings.png'), animations='disabled')
                page.get_by_role('checkbox', name='Community Forum', exact=True).uncheck()
                expect(page.locator('[data-nav-panel="forum"]')).not_to_be_visible()
                expect(page.locator('#panel-forum')).not_to_have_class('panel active')
                page.locator('.settings-close').click()
                requests.clear()
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('.mp-card')).to_have_count(15)
                expect(page.locator('[data-nav-panel="forum"]')).not_to_be_visible()
                assert not requests, 'Disabled forum made a request after reload'
                page.evaluate("SidebarView.setProfile('another-test-profile'); openSettingsTab('view')")
                expect(page.get_by_role('checkbox', name='Community Forum', exact=True)).not_to_be_checked()
                assert not errors, errors
                browser.close()
                print('PASS: opt-in persistence, shared local account, threads, draft isolation, retry deduplication, text safety, edit/delete, likes, follows, reports, search, and both responsive themes.')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    main()
