"""Browser integration against temporary Postgres, with Auth and CAPTCHA stubs.

No requests reach hosted services and no email is sent. Hosted email delivery and
the actual Turnstile challenge require separate launch verification.
"""
import functools
import importlib.util
import json
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview', ROOT / 'scripts/serve.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        fixture_port = sock.getsockname()[1]
    fixture = subprocess.Popen(['node', str(ROOT / 'tests/browser-fixture.mjs'), str(fixture_port)], cwd=ROOT)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(preview.Handler, directory=str(ROOT / 'dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    api = f'http://127.0.0.1:{fixture_port}'
    try:
        for _ in range(80):
            try:
                session = json.load(urllib.request.urlopen(api, timeout=1))
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Local database fixture did not start')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            errors = []
            auth_calls = []
            fail_reply = False
            screenshots = ROOT / 'test-results'
            screenshots.mkdir(exist_ok=True)
            config = json.loads((ROOT / 'dist/config.json').read_text())
            config.update(registrationEnabled=True, turnstileSiteKey='1x00000000000000000000AA')
            if not config['supabaseUrl']:
                config.update(supabaseUrl='https://local-test.supabase.co', supabasePublishableKey='sb_publishable_localtest')

            def route_request(route):
                nonlocal fail_reply
                request = route.request
                if request.url == origin + '/config.json':
                    route.fulfill(json=config)
                elif request.url.startswith('https://challenges.cloudflare.com/'):
                    route.fulfill(content_type='application/javascript', body='''
                      window.turnstile = {
                        render(el, options) { window.testChallenge = options; el.textContent = 'Test security check'; return 'local'; },
                        reset() {}, remove() { window.testChallenge = null; }
                      };
                    ''')
                elif request.url.startswith(config['supabaseUrl'] + '/auth/v1/'):
                    auth_calls.append(request.url)
                    if '/token' in request.url:
                        assert request.post_data_json['gotrue_meta_security']['captcha_token'] == 'test-token'
                        route.fulfill(json=session)
                    elif '/user' in request.url:
                        route.fulfill(json=session['user'])
                    elif '/signup' in request.url:
                        route.fulfill(json={'user': session['user'], 'session': None})
                    else:
                        route.fulfill(json={})
                elif request.url.startswith(config['supabaseUrl'] + '/rest/v1/rpc/forum_request'):
                    data = request.post_data_json
                    if fail_reply and data['route'].endswith('/reply'):
                        fail_reply = False
                        route.fulfill(status=503, json={'message': 'Temporary offline test'})
                        return
                    data['signedIn'] = request.headers.get('authorization') == 'Bearer ' + session['access_token']
                    response = context.request.post(api, data=data)
                    route.fulfill(status=response.status, content_type='application/json', body=response.body())
                elif request.url.startswith(origin + '/'):
                    route.continue_()
                else:
                    raise AssertionError('Unexpected external request: ' + request.url.split('?')[0])

            context.route('**/*', route_request)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin)
            expect(page.get_by_role('heading', name='All discussions')).to_be_visible()
            page.evaluate('document.fonts.ready')
            assert page.locator('.wordmark img').evaluate('(img) => img.complete && img.naturalWidth > 0')
            expect(page.get_by_role('navigation', name='Main navigation').get_by_role('link', name='Forum', exact=True)).to_have_attribute('aria-current', 'page')
            page.screenshot(path=str(screenshots / 'brand-desktop.png'), full_page=True)
            page.get_by_role('button', name='Sign in', exact=True).click()
            dialog = page.get_by_role('dialog')
            page.screenshot(path=str(screenshots / 'brand-signin.png'), full_page=True)
            dialog.get_by_label('Email', exact=True).fill('alice@test.invalid')
            dialog.get_by_label('Password', exact=True).fill('Local-test-password-123')
            dialog.get_by_role('button', name='Sign in', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('Complete the security check')
            assert not auth_calls
            expect(dialog.locator('.auth-captcha')).to_contain_text('Test security check')
            page.evaluate("window.testChallenge.callback('test-token')")
            dialog.get_by_role('button', name='Sign in', exact=True).click()
            expect(page.get_by_role('button', name='alice', exact=True)).to_be_visible()
            page.get_by_role('button', name='+ New discussion', exact=True).first.click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_label('Title', exact=True).fill('Browser integration discussion')
            dialog.get_by_label('Your post', exact=True).fill('<img src=x onerror=alert(1)> Plain text only.')
            dialog.get_by_role('button', name='Post discussion', exact=True).click()
            expect(page.get_by_role('heading', name='Browser integration discussion')).to_be_visible()
            assert page.locator('.forum-post-body img').count() == 0
            page.get_by_label('Your reply', exact=True).fill('Keep this reply during refresh and errors.')
            page.get_by_role('button', name='Refresh replies', exact=True).click()
            expect(page.get_by_label('Your reply', exact=True)).to_have_value('Keep this reply during refresh and errors.')
            fail_reply = True
            page.get_by_role('button', name='Post reply', exact=True).click()
            expect(page.locator('.forum-reply .mp-error')).to_contain_text('Your draft is still here')
            page.get_by_role('button', name='Post reply', exact=True).click()
            expect(page.locator('.forum-post')).to_have_count(2)
            expect(page.get_by_label('Your reply', exact=True)).to_have_value('')
            page.screenshot(path=str(screenshots / 'brand-discussion.png'), full_page=True)
            page.get_by_role('button', name='Like', exact=True).last.click()
            expect(page.get_by_role('button', name='Liked 1', exact=True)).to_be_visible()
            page.get_by_label('Your reply', exact=True).fill('Private draft before signing out')
            page.get_by_role('button', name='alice', exact=True).click()
            page.get_by_role('button', name='Sign out', exact=True).click()
            expect(page.get_by_role('button', name='Sign in to reply')).to_be_visible()
            page.get_by_role('button', name='Back to discussions', exact=True).click()
            page.get_by_role('button', name='Sign in', exact=True).click()
            page.get_by_role('button', name='Create a new account', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_label('Display name', exact=True).fill('Reserved name test')
            dialog.get_by_label('Username', exact=True).fill('admin')
            dialog.get_by_label('Email', exact=True).fill('test@test.invalid')
            dialog.get_by_label('Password', exact=True).fill('Local-test-password-123')
            dialog.get_by_role('button', name='Create account', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('taken or reserved')
            assert not any('/signup' in url for url in auth_calls)
            dialog.get_by_role('button', name='Close dialog').click()
            for width in (320, 390, 768, 1024, 1440):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}px'
                expect(page.get_by_role('heading', name='All discussions')).to_be_visible()
                if width < 1024:
                    menu = page.get_by_role('button', name='Toggle menu')
                    menu.click()
                    expect(menu).to_have_attribute('aria-expanded', 'true')
                    expect(page.get_by_role('navigation', name='Main navigation')).to_be_visible()
                    page.keyboard.press('Escape')
                    expect(menu).to_have_attribute('aria-expanded', 'false')
                    expect(menu).to_be_focused()
                page.get_by_role('button', name='Sign in', exact=True).click()
                dialog = page.get_by_role('dialog')
                assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth'), f'Dialog overflow at {width}px'
                dialog.get_by_role('button', name='Close dialog').click()
                page.evaluate('scrollTo(0, 0)')
                page.screenshot(path=str(screenshots / f'brand-{width}.png'), full_page=True)
            page.get_by_role('searchbox', name='Search discussions').fill('No matching local discussion')
            page.get_by_role('button', name='Search', exact=True).click()
            expect(page.get_by_role('heading', name='No matching conversations yet.')).to_be_visible()
            page.get_by_role('button', name='Clear search', exact=True).click()
            expect(page.get_by_role('button', name='Browser integration discussion')).to_be_visible()
            page.get_by_label('Sort discussions').select_option('newest')
            expect(page.get_by_role('button', name='Browser integration discussion')).to_be_visible()
            page.locator('.forum-category').nth(1).click()
            expect(page.locator('.forum-category').nth(1)).to_have_attribute('aria-pressed', 'true')
            page.locator('.forum-category').first.click()
            expect(page.get_by_role('heading', name='All discussions')).to_be_visible()
            # The mobile menu also resets when crossing the desktop breakpoint.
            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_role('button', name='Toggle menu').click()
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.get_by_role('button', name='Toggle menu')).to_have_attribute('aria-expanded', 'false')
            # A static backdrop must be used without fetching the video when motion is reduced.
            page.emulate_media(reduced_motion='reduce')
            video_requests = []
            page.on('request', lambda request: video_requests.append(request.url) if request.url.endswith('.mp4') else None)
            page.reload()
            expect(page.get_by_role('heading', name='All discussions')).to_be_visible()
            expect(page.locator('.ambient-toggle')).to_be_hidden()
            assert not page.locator('.site-video').get_attribute('src')
            assert not video_requests, video_requests
            page.emulate_media(reduced_motion='no-preference')
            expect(page.get_by_role('button', name='Pause background', exact=True)).to_be_visible()
            page.get_by_role('button', name='Pause background', exact=True).click()
            expect(page.get_by_role('button', name='Play background', exact=True)).to_be_visible()
            page.get_by_role('button', name='Play background', exact=True).click()
            expect(page.get_by_role('button', name='Pause background', exact=True)).to_be_visible()
            assert not errors, errors
            (screenshots / 'brand-verification.json').write_text(json.dumps({
                'passed': True, 'widths': [320, 390, 768, 1024, 1440], 'page_errors': errors,
                'checks': ['local SQL integration', 'login and CAPTCHA gating', 'create and reply',
                           'literal text rendering', 'draft preservation and retry', 'likes and signout',
                           'reserved usernames', 'search and clear', 'category and sort', 'navigation keyboard and resize',
                           'responsive dialogs', 'reduced motion avoids video request', 'pause and play background'],
                'hosted_writes': False, 'email_sent': False,
            }, indent=2), encoding='utf-8')
            browser.close()
        print('PASS: real forum SQL; CAPTCHA; login; create/reply; literal text; retry/drafts; likes; signout; reserved usernames; search, sort and categories; mobile navigation; dialogs; 5 widths; reduced motion; video pause/play. No hosted writes or email.')
    finally:
        server.shutdown()
        fixture.terminate()
        fixture.wait(timeout=10)


if __name__ == '__main__':
    main()
