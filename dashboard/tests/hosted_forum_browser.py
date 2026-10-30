"""Verify the optional dashboard entry opens the public forum without local API calls."""
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


def main():
    with tempfile.TemporaryDirectory() as directory:
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(mp.Marketplace(directory)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context()
                requests = []
                def route_request(route):
                    url = route.request.url
                    if '/marketplace/forum/' in url:
                        requests.append(url)
                    if url == 'https://forum.magimatix.com/':
                        assert not route.request.headers.get('authorization')
                        route.fulfill(content_type='text/html', body='<h1>Public community destination</h1>')
                    elif urlsplit(url).hostname in ('127.0.0.1', 'localhost'):
                        route.continue_()
                    else:
                        route.abort()
                context.route('**/*', route_request)
                page = context.new_page()
                page.goto(origin, wait_until='domcontentloaded')
                expect(page.locator('[data-nav-panel="forum"]')).not_to_be_visible()
                page.locator('#settings-btn').click()
                page.locator('.settings-tab[data-tab="view"]').click()
                page.get_by_role('checkbox', name='Community Forum', exact=True).check()
                page.locator('.settings-close').click()
                with page.expect_popup() as opened:
                    page.locator('[data-nav-panel="forum"]').click()
                popup = opened.value
                popup.wait_for_load_state('domcontentloaded')
                assert popup.url == 'https://forum.magimatix.com/'
                assert popup.evaluate('window.opener === null')
                expect(page.get_by_role('link', name='Open the community forum')).to_have_attribute('href', popup.url)
                popup.close()
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('[data-nav-panel="forum"]')).to_be_visible()
                page.locator('#settings-btn').click()
                page.locator('.settings-tab[data-tab="account"]').click()
                expect(page.locator('#settings-account-root')).to_contain_text('Your forum account is managed on the community website.')
                assert not requests, requests
                browser.close()
            print('PASS: optional forum setting persists; button opens the public site; opener and local accounts isolated; account explanation; zero local forum API calls.')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
