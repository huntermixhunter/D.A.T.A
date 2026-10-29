"""Real local UI checks with isolated settings and a simulated application."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketplace as mp
from marketplace_preview import make_handler
from playwright.sync_api import expect, sync_playwright


def main():
    screenshots = mp.ROOT.parent / 'screenshots'
    screenshots.mkdir(exist_ok=True)
    app_calls = []
    class LocalApp(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            app_calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'system': {}, 'devices': []}).encode())
    app = ThreadingHTTPServer(('127.0.0.1', 0), LocalApp)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        store = mp.Marketplace(directory / 'store')
        store.account_action('register', {'username': 'browser-tester', 'name': 'Browser Tester', 'password': 'browser test password'}, None, '', 'test')
        class Protected(make_handler(store)):
            def _check_auth(self):
                if self.headers.get('X-Data-Token') == 'advanced-browser-test':
                    return True
                self.send_response(401)
                self.end_headers()
                return False
        server = ThreadingHTTPServer(('127.0.0.1', 0), Protected)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://localhost:{server.server_port}'
        (directory / 'whisper-cli').write_text('test marker, never executed')
        (directory / 'model.bin').write_bytes(b'test marker')
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                context.add_init_script("localStorage.setItem('data-bridge-token', 'advanced-browser-test');")
                context.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).hostname in ('127.0.0.1', 'localhost') else route.abort())
                page = context.new_page()
                page.set_default_timeout(5000)
                page.set_default_navigation_timeout(15000)
                errors, sent = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: sent.append(urlsplit(request.url).path) if request.method == 'POST' else None)
                # The host polls continuously; wait for rendered API results.
                page.goto(origin + '/#upgrades', wait_until='domcontentloaded')
                expect(page.locator('.advanced-tool-card')).to_have_count(3)
                assert app_calls == [], 'Opening settings contacted an application'
                comfy = page.locator('#advanced-tool-comfyui')
                comfy.locator('summary').click()
                comfy.get_by_label('Application folder', exact=True).fill(str(directory))
                address = f'http://127.0.0.1:{app.server_port}'
                comfy.get_by_label('Local address', exact=True).fill(address)
                comfy.get_by_role('button', name='Save and check connection', exact=True).click()
                expect(comfy.locator('.advanced-tool-feedback')).to_contain_text('The local app responded')
                expect(comfy.get_by_role('link', name='Open local app')).to_have_attribute('href', address + '/')
                assert app_calls == ['/system_stats']
                page.reload(wait_until='domcontentloaded')
                expect(comfy).to_contain_text('Locations saved')
                assert app_calls == ['/system_stats'], 'Reload automatically checked the app'
                comfy.locator('summary').click()
                expect(comfy.get_by_label('Local address', exact=True)).to_have_value(address)
                comfy.get_by_label('Local address', exact=True).fill('https://public.example:8188')
                comfy.get_by_role('button', name='Save and check connection', exact=True).click()
                expect(comfy.locator('.advanced-tool-feedback')).to_contain_text('loopback HTTP address')
                expect(comfy.get_by_role('link', name='Open local app')).to_have_count(0)
                assert app_calls == ['/system_stats']
                comfy.get_by_role('button', name='Forget saved locations').click()
                expect(comfy.locator('.advanced-tool-feedback')).to_contain_text('Application and model files were kept')
                expect(comfy.get_by_label('Local address', exact=True)).to_have_value('http://127.0.0.1:8188')
                comfy.locator('summary').click()

                whisper = page.locator('#advanced-tool-whispercpp')
                whisper.locator('summary').click()
                whisper.get_by_label('Application folder', exact=True).fill(str(directory))
                whisper.get_by_label('Model file', exact=True).fill(str(directory / 'model.bin'))
                whisper.get_by_role('button', name='Save and check files', exact=True).click()
                expect(whisper.locator('.advanced-tool-feedback')).to_contain_text('Execution has not been tested')
                whisper.get_by_role('button', name='Forget saved locations').click()
                expect(whisper.locator('.advanced-tool-feedback')).to_contain_text('files were kept')
                assert (directory / 'model.bin').exists()
                whisper.locator('summary').click()

                page.locator('.settings-content').evaluate('e => { e.scrollTop = 0; }')
                page.screenshot(path=str(screenshots / 'advanced-tools-desktop.png'), animations='disabled')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('.settings-content').evaluate('e => { e.scrollTop = 0; }')
                assert page.locator('.settings-content').evaluate('e => e.scrollWidth <= e.clientWidth')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(screenshots / 'advanced-tools-mobile.png'), animations='disabled')
                comfy.locator('summary').click()
                expect(comfy.get_by_label('Local address', exact=True)).to_be_visible()
                assert page.locator('.settings-content').evaluate('e => e.scrollWidth <= e.clientWidth')
                page.set_viewport_size({'width': 1440, 'height': 1000})

                page.get_by_role('button', name='Browse marketplace', exact=True).click()
                expect(page.locator('.mp-card')).to_have_count(15)
                page.get_by_role('button', name='Sign in', exact=True).click()
                dialog = page.locator('dialog')
                dialog.get_by_label('Username', exact=True).fill('browser-tester')
                dialog.get_by_label('Password', exact=True).fill('browser test password')
                dialog.get_by_role('button', name='Sign in', exact=True).click()
                expect(page.locator('dialog')).to_have_count(0)
                search = page.get_by_label('Search extensions', exact=True)
                search.fill('Web Design Studio')
                expect(page.locator('.mp-card')).to_have_count(1)
                page.locator('.mp-card-open').click()
                expect(dialog).to_contain_text('No independent review is recorded')
                dialog.get_by_role('checkbox').check()
                dialog.get_by_role('button', name='Install extension', exact=True).click()
                expect(page.locator('dialog')).to_have_count(0)
                page.locator('.mp-card').get_by_role('button', name='Open', exact=True).click()
                expect(dialog).to_contain_text('Web Design Studio')
                dialog.get_by_role('button', name='Prepare request in chat', exact=True).click()
                assert 'Web Design Studio' in page.locator('#chat-input').input_value()
                assert not any(path in ('/chat', '/send', '/chat/stream') for path in sent), sent

                page.evaluate("showPanel('marketplace')")
                search.fill('FramePack')
                page.locator('.mp-card-open').click()
                expect(dialog).to_contain_text('application and models are installed separately')
                dialog.get_by_role('button', name='Configure application').click()
                expect(page.locator('#advanced-tool-framepack details')).to_have_attribute('open', '')
                expect(page.locator('#advanced-tool-framepack')).to_contain_text('Not configured')
                page.get_by_role('button', name='Browse marketplace', exact=True).click()
                search.fill('')
                page.locator('#panel-marketplace').evaluate('e => { e.scrollTop = 0; }')
                page.screenshot(path=str(screenshots / 'marketplace-starter-collection.png'), animations='disabled')
                assert not errors, errors
                browser.close()
            print('Advanced tools browser checks passed: settings persistence, explicit probes, file checks, phone layout, pack install, unsent draft and marketplace handoff.')
        finally:
            server.shutdown()
            server.server_close()
            app.shutdown()
            app.server_close()


if __name__ == '__main__':
    main()
