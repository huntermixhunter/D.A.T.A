"""Run the actual dashboard UI against local fixture profiles, with external traffic blocked."""
import ast
import base64
import json
import mimetypes
from pathlib import Path
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright, expect
from test_agent_profiles import Fixture, ROOT, png, profiles


def run():
    fixture = Fixture()
    tree = ast.parse((ROOT / 'bridge_server.py').read_text(encoding='utf-8'))
    voices = next(ast.literal_eval(item.value) for item in tree.body if isinstance(item, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'CREW_VOICES' for target in item.targets))
    fixture.env['CREW_VOICES'] = voices
    specialist = next(key for key in voices if key not in ('data', 'computer'))
    (fixture.env['crew_agents_dir']() / f'{specialist}.md').write_text('Keep the original personality.', encoding='utf-8')
    original = fixture.server.RequestHandlerClass

    class Handler(original):
        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == '/agent-profiles':
                return super().do_GET()
            if path == '/agents':
                return profiles._json(self, {'agents': [{'id': p.stem, 'name': p.stem.title(), 'path': str(p),
                                                         'content': p.read_text(encoding='utf-8')}
                                                        for p in fixture.env['crew_agents_dir']().glob('*.md')]})
            if path == '/user/list':
                return profiles._json(self, {'active': 'fixture_a', 'users': [{'id': 'fixture_a', 'name': 'Fixture User', 'rank': 'User'}]})
            if path == '/user/active':
                return profiles._json(self, {'active': 'fixture_a'})
            if path == '/voice/voices':
                return profiles._json(self, {'default': 'data', 'voices': [{'id': key, 'name': value['name'], 'wake': [key], 'names': [key]}
                                                                         for key, value in voices.items()]})
            if path == '/providers':
                return profiles._json(self, {'active': 'codex', 'providers': []})
            if path == '/status':
                return profiles._json(self, {'step': 'idle', 'status': 'online', 'detail': ''})
            if path == '/effort':
                return profiles._json(self, {'active': 'medium', 'levels': []})
            target = (ROOT / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
            if target.is_relative_to(ROOT) and target.is_file() and target.suffix in ('.html', '.js', '.css', '.png', '.jpg', '.webp', '.woff', '.woff2', '.svg', '.mp3'):
                body = target.read_bytes()
                if target.name == 'index.html':
                    # Only the real main app and profile UI are needed for these checks.
                    html = body.decode('utf-8')
                    html = re.sub(r'<script\b[^>]*src=["\']([^"\']+)["\'][^>]*>\s*</script>',
                                  lambda m: m[0] if m[1].split('?')[0] in ('app.js', 'agent_profiles.js') else '', html)
                    body = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            profiles._json(self, {'agents': [], 'orders': [], 'jobs': [], 'events': [], 'nodes': [], 'edges': [], 'tools': [], 'categories': []})

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path == '/agent-profiles':
                return super().do_POST()
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            profiles._json(self, {})

    fixture.server.RequestHandlerClass = Handler
    artifacts = Path(sys.argv[1]) if len(sys.argv) > 1 else fixture.root / 'screenshots'
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1100})
            context.add_init_script("localStorage.setItem('data-bridge-token', 'fixture-secret'); localStorage.setItem('wake-word-enabled', '0');")
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(fixture.base + '/') else route.abort())
            page = context.new_page()
            page.on('dialog', lambda dialog: dialog.dismiss())
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            # The dashboard polls continuously, so networkidle is never a readiness signal.
            page.goto(fixture.base, wait_until='domcontentloaded')
            page.wait_for_function("window.AgentProfiles?.owner === 'fixture_a'")
            page.evaluate("openSettings(); settingsTab('crew');")
            expect(page.locator('#agent-display-name')).to_have_value(voices['data']['name'])
            name = 'Nova <img src=x onerror=window.profileXSS=1>'
            page.locator('#agent-display-name').fill(name)
            page.locator('#agent-picture-file').set_input_files({'name': 'portrait.png', 'mimeType': 'image/png',
                                                                 'buffer': base64.b64decode(png(12, 12).split(',')[1])})
            expect(page.locator('.agent-profile-status')).to_contain_text('Picture ready')
            page.get_by_role('button', name='Save profile', exact=True).click()
            expect(page.locator('.agent-profile-status')).to_have_text('Profile saved.')
            assert page.evaluate('window.profileXSS') is None
            assert page.locator('#pane-crew-main option[value=data]').text_content().startswith(name.upper())
            page.evaluate("appendMessage('data', 'Profile check'); _settingsPaintVoiceRow();")
            expect(page.locator('[data-agent-avatar=data] img').last).to_be_attached()
            assert name.upper() in page.locator('[data-agent-label=data]').last.text_content()
            assert page.locator('#settings-voice-row img').count() == 0
            if page.locator('#convo-face-idle').count():
                assert page.locator('#convo-face-idle').get_attribute('src').startswith('data:image/png;')
            page.evaluate("const pane = document.createElement('div'); pane.id='profile-test-pane'; document.body.append(pane); appendMessageToPane(pane, 'data', 'Second pane', 'data');")
            expect(page.locator('#profile-test-pane [data-agent-avatar=data] img')).to_be_attached()
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("window.AgentProfiles?.owner === 'fixture_a'")
            page.evaluate("openSettings(); settingsTab('crew');")
            expect(page.locator('#agent-display-name')).to_have_value(name)
            expect(page.locator('.agent-profile-preview img')).to_be_visible()
            page.locator('#agent-display-name').fill('Nova')
            page.get_by_role('button', name='Save profile', exact=True).click()
            expect(page.locator('.agent-profile-status')).to_have_text('Profile saved.')
            page.screenshot(path=str(artifacts / (ROOT.parent.name.lower() + '-profiles-desktop.png')))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(artifacts / (ROOT.parent.name.lower() + '-profiles-mobile.png')))
            form = page.locator('.agent-profile-form').bounding_box()
            assert form and form['x'] >= 0 and form['x'] + form['width'] <= 391, form
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('button', name='Remove picture', exact=True).click()
            page.get_by_role('button', name='Save profile', exact=True).click()
            expect(page.locator('.agent-profile-status')).to_have_text('Profile saved.')
            assert page.locator('[data-agent-avatar=data] img').count() == 0
            page.get_by_role('button', name='Reset to default', exact=True).click()
            page.get_by_role('button', name='Save profile', exact=True).click()
            expect(page.locator('#agent-display-name')).to_have_value(voices['data']['name'])
            expect(page.locator('.agent-profile-status')).to_have_text('Profile saved.')
            page.locator(f'#settings-crew-pick [data-agent={specialist}]').click()
            page.locator('#agent-display-name').fill('Guide')
            page.get_by_role('button', name='Save profile', exact=True).click()
            expect(page.locator('.agent-profile-status')).to_have_text('Profile saved.')
            assert 'GUIDE' in page.locator(f'#pane-crew-main option[value={specialist}]').text_content()
            assert (fixture.env['crew_agents_dir']() / f'{specialist}.md').read_text(encoding='utf-8') == 'Keep the original personality.'
            fixture.env['_ACTIVE_USER'] = 'fixture_b'
            page.locator('#agent-display-name').fill('Wrong user')
            page.get_by_role('button', name='Save profile', exact=True).click()
            expect(page.locator('.agent-profile-status')).to_contain_text('active profile changed')
            assert not (fixture.env['_user_dir']('fixture_b') / 'agent_profiles.json').exists()
            assert not [e for e in errors if 'AgentProfiles' in e or 'crewLabel' in e or '_crewAvatar' in e], errors
            print(json.dumps({'dashboard': str(ROOT), 'result': 'PASS', 'checks': ['rename', 'upload', 'chat', 'second pane', 'voice', 'reload', 'mobile', 'remove', 'reset', 'specialist', 'XSS', 'stale user'], 'fixture_console_errors': errors, 'artifacts': str(artifacts)}))
            browser.close()
    finally:
        fixture.close()


if __name__ == '__main__':
    run()
