"""Exercise the embedded hosted build with local SQL and stubbed Auth/CAPTCHA.

No request reaches a hosted service, sends email, or changes a real account.
Run npm run build:release in community-web before this test.
"""
import json
import mimetypes
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketplace as mp
from marketplace_preview import make_handler
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
COMMUNITY = ROOT / 'community-web'
HOSTED = 'https://forum.magimatix.com'


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        fixture_port = sock.getsockname()[1]
    fixture = subprocess.Popen(['node', str(COMMUNITY / 'tests/browser-fixture.mjs'), str(fixture_port)], cwd=COMMUNITY)
    api = f'http://127.0.0.1:{fixture_port}'
    try:
        for _ in range(80):
            try:
                session = json.load(urllib.request.urlopen(api, timeout=1))
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Local forum SQL fixture did not start.')
        check_dashboard(api, session)
    finally:
        fixture.terminate()
        fixture.wait(timeout=10)


def check_dashboard(api, session):
    headers = {}
    for line in (COMMUNITY / 'dist/_headers').read_text(encoding='utf-8').splitlines():
        if line.startswith('/config.json'):
            break
        if line.startswith('  ') and ': ' in line:
            key, value = line.strip().split(': ', 1)
            headers[key] = value
    assert 'X-Frame-Options' not in headers
    assert 'frame-ancestors http://localhost:* http://127.0.0.1:*;' in headers['Content-Security-Policy']
    config = json.loads((COMMUNITY / 'dist/config.json').read_text())
    config.update(turnstileSiteKey='1x00000000000000000000AA')
    evidence = ROOT / 'build/forum-embed-checks'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(mp.Marketplace(directory)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                local_forum_requests, documents, errors = [], [], []
                unavailable = False

                def route_request(route):
                    request = route.request
                    url = request.url
                    if '/marketplace/forum/' in url:
                        local_forum_requests.append(url)
                    if url.startswith(HOSTED + '/'):
                        assert not request.headers.get('authorization')
                        assert not request.headers.get('x-data-token')
                        path = urlsplit(url).path
                        if request.is_navigation_request():
                            documents.append(url)
                            assert not request.headers.get('referer')
                            if unavailable:
                                route.abort()
                                return
                        if path == '/config.json':
                            route.fulfill(json=config)
                        else:
                            assert path != '/bg-video-0605.mp4' or request.frame.parent_frame is None, 'Embedding must not download the background video'
                            file = COMMUNITY / 'dist' / (path.lstrip('/') or 'index.html')
                            assert file.is_file(), path
                            route.fulfill(body=file.read_bytes(), headers=headers,
                                          content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                    elif url.startswith('https://challenges.cloudflare.com/'):
                        route.fulfill(content_type='application/javascript', body='''
                          window.turnstile = {
                            render(el, options) { window.testChallenge = options; return 'local'; },
                            reset() {}, remove() { window.testChallenge = null; }
                          };
                        ''')
                    elif url.startswith(config['supabaseUrl'] + '/auth/v1/'):
                        if '/token' in url:
                            assert request.post_data_json['gotrue_meta_security']['captcha_token'] == 'test-token'
                            route.fulfill(json=session)
                        elif '/user' in url:
                            route.fulfill(json=session['user'])
                        else:
                            route.fulfill(json={})
                    elif url.startswith(config['supabaseUrl'] + '/rest/v1/rpc/forum_request'):
                        data = request.post_data_json
                        data['signedIn'] = request.headers.get('authorization') == 'Bearer ' + session['access_token']
                        response = context.request.post(api, data=data)
                        route.fulfill(status=response.status, content_type='application/json', body=response.body())
                    elif url == 'https://untrusted-embed.invalid/':
                        route.fulfill(content_type='text/html', body=f'<iframe src="{HOSTED}/?embed=1" referrerpolicy="no-referrer"></iframe>')
                    elif urlsplit(url).hostname in ('127.0.0.1', 'localhost'):
                        route.continue_()
                    else:
                        route.abort()
                context.route('**/*', route_request)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/#forum', wait_until='domcontentloaded')
                expect(page.locator('[data-nav-panel="forum"]')).not_to_be_visible()
                expect(page.locator('.settings-tab[data-tab="view"]')).to_have_class('settings-tab active')
                assert not documents, documents
                page.get_by_role('checkbox', name='Community Forum', exact=True).check()
                page.locator('.settings-close').click()
                page.locator('[data-nav-panel="forum"]').click()
                forum = page.frame_locator('.forum-embed')
                expect(forum.get_by_role('heading', name='All discussions')).to_be_visible()
                expect(page.locator('#forum-root')).to_have_attribute('aria-busy', 'false')
                expect(page.locator('.forum-embed-status')).to_be_hidden()
                expect(forum.locator('.site-header')).to_be_hidden()
                expect(forum.locator('.site-footer')).to_be_hidden()
                assert len(context.pages) == 1, 'Selecting Forum must not open a browser tab'
                assert documents == [HOSTED + '/?embed=1'], documents
                page.screenshot(path=str(evidence / 'desktop.png'), animations='disabled')

                # Sign in and create a discussion through the actual embedded UI.
                forum.get_by_role('button', name='Sign in', exact=True).click()
                dialog = forum.get_by_role('dialog')
                dialog.get_by_label('Email', exact=True).fill('alice@test.invalid')
                dialog.get_by_label('Password', exact=True).fill('Local-test-password-123')
                child = page.locator('.forum-embed').element_handle().content_frame()
                child.wait_for_function('window.testChallenge')
                child.evaluate("window.testChallenge.callback('test-token')")
                dialog.get_by_role('button', name='Sign in', exact=True).click()
                expect(forum.get_by_role('button', name='alice', exact=True)).to_be_visible()
                forum.get_by_role('button', name='+ New discussion', exact=True).first.click()
                dialog.get_by_label('Title', exact=True).fill('Embedded forum test')
                dialog.get_by_label('Your post', exact=True).fill('Temporary local integration check.')
                dialog.get_by_role('button', name='Post discussion', exact=True).click()
                reply = forum.get_by_label('Your reply', exact=True)
                reply.fill('Keep this unfinished reply while I change dashboard pages.')
                count = len(documents)
                page.evaluate("showPanel('chat')")
                page.evaluate("showPanel('forum')")
                expect(reply).to_have_value('Keep this unfinished reply while I change dashboard pages.')
                assert len(documents) == count, 'Returning to Forum must retain the existing frame'
                forum.get_by_role('button', name='Post reply', exact=True).click()
                expect(forum.locator('.forum-post')).to_have_count(2)
                forum.get_by_role('button', name='Back to discussions', exact=True).click()
                for width in (320, 390, 768, 1440):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                    assert child.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                    assert page.locator('.forum-embed').bounding_box()['height'] >= 240
                    if width == 390:
                        page.screenshot(path=str(evidence / 'mobile.png'), animations='disabled')

                with page.expect_popup() as opened:
                    page.get_by_role('link', name='Open in browser', exact=True).click()
                popup = opened.value
                popup.wait_for_load_state('domcontentloaded')
                assert popup.url == HOSTED + '/'
                assert popup.evaluate('window.opener === null')
                popup.close()
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('[data-nav-panel="forum"]')).to_be_visible()
                expect(forum.get_by_role('heading', name='All discussions')).to_be_visible()
                page.locator('#settings-btn').click()
                page.locator('.settings-tab[data-tab="account"]').click()
                expect(page.locator('#settings-account-root')).to_contain_text('Sign in to your community account from the Forum page.')
                page.locator('.settings-tab[data-tab="view"]').click()
                page.get_by_role('checkbox', name='Community Forum', exact=True).uncheck()
                expect(page.locator('.forum-embed')).to_have_count(0)
                expect(page.locator('#panel-chat')).to_have_class('panel active')

                # Real browser enforcement of the release CSP rejects foreign parents.
                outsider = context.new_page()
                violations = []
                outsider.on('console', lambda message: violations.append(message.text) if 'frame-ancestors' in message.text else None)
                outsider.goto('https://untrusted-embed.invalid/')
                expect(outsider.frame_locator('iframe').locator('#forum-root')).to_have_count(0)
                assert violations, 'The hosted CSP must block embedding by an untrusted site'
                outsider.close()

                # A failed frame load cannot masquerade as a successful connection.
                unavailable = True
                page.clock.install()
                page.get_by_role('checkbox', name='Community Forum', exact=True).check()
                page.locator('.settings-close').click()
                page.locator('[data-nav-panel="forum"]').click()
                expect(page.locator('#forum-root')).to_have_attribute('aria-busy', 'true')
                page.evaluate('''() => {
                  const source = document.querySelector('.forum-embed').contentWindow;
                  const data = {type: 'data:forum:ready'};
                  dispatchEvent(new MessageEvent('message', {origin: 'https://untrusted-embed.invalid', source, data}));
                  dispatchEvent(new MessageEvent('message', {origin: 'https://forum.magimatix.com', source: window, data}));
                }''')
                expect(page.locator('#forum-root')).to_have_attribute('aria-busy', 'true')
                page.clock.fast_forward(16000)
                expect(page.locator('.forum-embed-status')).to_contain_text('The forum has not connected.')
                unavailable = False
                page.get_by_role('button', name='Reload forum', exact=True).click()
                expect(forum.get_by_role('heading', name='All discussions')).to_be_visible()
                expect(page.locator('.forum-embed-status')).to_be_hidden()
                assert not local_forum_requests, local_forum_requests
                assert not errors, errors
                (evidence / 'verification.json').write_text(json.dumps({
                    'passed': True, 'widths': [320, 390, 768, 1440], 'page_errors': errors,
                    'checks': ['opt-in and hidden deep link', 'embedded navigation without popups',
                               'sandboxed sign-in and posting', 'draft retention across pages',
                               'no background video', 'responsive layout', 'explicit browser fallback',
                               'settings persistence and disable unload', 'foreign embedding blocked',
                               'origin and source checks', 'unavailable state and retry',
                               'no dashboard credentials or referrer sent to forum'],
                    'hosted_writes': False, 'email_sent': False,
                }, indent=2), encoding='utf-8')
                browser.close()
            print('PASS: embedded forum; sign-in; posts/replies; draft retention; 4 widths; explicit browser fallback; opt-in/persistence/disable; CSP and message isolation; failed load/retry. No hosted requests or email.')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
