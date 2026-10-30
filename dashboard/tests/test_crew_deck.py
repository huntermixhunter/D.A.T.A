"""Collaboration integration checks with temporary profiles and fake inference."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(os.environ.get('CREW_TEST_DASHBOARD', str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(ROOT))
from crew_deck import service
from crew_deck.runtime import CrewRuntime, CrewError, OFFICERS


class FakeRunner:
    def __init__(self):
        self.prompts = []

    def capability(self, settings):
        return {'ready': True, 'provider': 'fixture', 'detail': 'Simulated inference'}

    def __call__(self, prompt, settings, cancel, progress, guard):
        self.prompts.append(prompt)
        first = len(self.prompts) % 2 == 1
        return {'message': 'I prepared a specific proposal.' if first else 'I verified the proposal against the objective.',
                'handoff': 'Review brief.txt and its assumptions.', 'status': 'continue' if first else 'complete',
                'learning': {'lesson': '', 'evidence_event_ids': []},
                'actions': [{'type': 'write_file', 'path': 'brief.txt', 'content': 'A concrete local proposal.',
                             'url': '', 'reason': '', 'estimated_cost_usd': 0}] if first else []}


class Fixture:
    def __init__(self, root=None, start=False):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-integration-')
        self.path = Path(self.temp.name)
        self.root = Path(root or ROOT)
        self.runner = FakeRunner()
        self.env = {'_ACTIVE_USER': 'fixture_a', '_user_dir': lambda uid=None: self.path / (uid or self.env['_ACTIVE_USER']),
                    'crew_agents_dir': lambda: self.path / 'agents'}
        self.env['crew_agents_dir']().mkdir()
        self.runtime = CrewRuntime(self.env['_user_dir']() / 'crew_deck', self.env['crew_agents_dir'](), owner='fixture_a',
                                   guard=lambda: service._guard(self.env, 'fixture_a'), runner=self.runner, start=start)
        self.key = str(self.runtime.root)
        service._runtimes[self.key] = self.runtime
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _check_auth(self):
                if self.headers.get('X-Data-Token') == 'fixture-secret':
                    return True
                service._json(self, {'error': 'auth_required'}, 401)
                return False

            def do_GET(self):
                if service.handle_http(self, fixture.env):
                    return
                from urllib.parse import urlsplit
                route = urlsplit(self.path).path
                if route == '/user/active':
                    service._json(self, {'active': fixture.env['_ACTIVE_USER']})
                else:
                    service._json(self, {'error': 'Not found'}, 404)

            def do_POST(self):
                if not service.handle_http(self, fixture.env):
                    service._json(self, {'error': 'Not found'}, 404)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, body=None, headers=None):
        defaults = {'X-Data-Token': 'fixture-secret', 'X-Data-Request': '1', 'X-Crew-Scope': 'fixture_a',
                    'Content-Type': 'application/json', 'X-Crew-CSRF': self.runtime.csrf}
        defaults.update(headers or {})
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=defaults)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(), dict(error.headers)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        for key, runtime in list(service._runtimes.items()):
            if Path(key).is_relative_to(self.path):
                runtime.close()
                del service._runtimes[key]
        self.temp.cleanup()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.runtime = self.fixture.runtime
        self.officers = list(OFFICERS)[:2]
        self.order = {'title': 'Reviewable proposal', 'order': 'Write and verify a local brief.', 'officers': self.officers,
                      'mode': 'once', 'interval_seconds': 10, 'acknowledge_usage': True}

    def tearDown(self):
        self.fixture.close()

    def test_static_mount_and_private_file_denial(self):
        for path in ('/crew-deck/', '/crew-deck/access.js', '/crew-deck/assets/layout.json', '/crew-deck/assets/ship.webp'):
            self.assertEqual(self.fixture.request(path)[0], 200, path)
        for path in ('/crew-deck/runtime.py', '/crew-deck/service.py', '/crew-deck/assets/../../bridge_server.py',
                     '/crew-deck/assets/%2e%2e/%2e%2e/bridge_server.py', '/crew-deck/.env'):
            self.assertEqual(self.fixture.request(path)[0], 404, path)
        self.assertEqual(self.fixture.request('/crew-deck/')[2]['X-Frame-Options'], 'SAMEORIGIN')

    def test_auth_and_scope_required_for_state(self):
        self.assertEqual(self.fixture.request('/api/crew/state', headers={'X-Data-Token': ''})[0], 401)
        self.assertEqual(self.fixture.request('/api/crew/state', headers={'X-Crew-Scope': ''})[0], 409)
        self.assertEqual(self.fixture.request('/api/crew/state', headers={'X-Crew-Scope': 'fixture_b'})[0], 409)
        state = json.loads(self.fixture.request('/api/crew/state')[1])
        self.assertEqual(state['scope'], 'fixture_a')
        self.assertEqual([o['id'] for o in state['roster']], list(OFFICERS))

    def test_origin_rebinding_and_csrf_rejected(self):
        for headers in ({'Origin': 'https://untrusted.invalid'}, {'X-Data-Request': ''},
                        {'Sec-Fetch-Site': 'cross-site'}, {'X-Crew-CSRF': ''},
                        {'Host': 'untrusted.invalid', 'X-Data-Token': ''}, {'Host': 'untrusted.invalid'}):
            self.assertGreaterEqual(self.fixture.request('/api/crew/settings', {'settings': {'banter_enabled': False}}, headers)[0], 400)
        self.assertTrue(self.runtime.settings['banter_enabled'])

    def test_save_reload_and_delete_without_inference(self):
        code, raw, _ = self.fixture.request('/api/crew/templates', self.order)
        self.assertEqual(code, 200)
        template = json.loads(raw)['templates'][0]
        self.assertEqual(template['title'], self.order['title'])
        self.assertEqual(self.fixture.runner.prompts, [])
        self.assertEqual(self.runtime.runs, {})
        self.assertEqual(self.fixture.request('/api/crew/templates/delete', {'id': template['id']})[0], 200)
        self.assertEqual(self.runtime.snapshot()['templates'], [])

    def test_real_handoff_and_authenticated_artifact(self):
        code, raw, _ = self.fixture.request('/api/crew/runs', {**self.order, 'save_template': True})
        self.assertEqual(code, 200)
        rid = json.loads(raw)['runs'][0]['id']
        for _ in self.officers:
            self.runtime.runs[rid]['next_run_at'] = 0
            self.runtime.tick()
            self.runtime.worker.join(3)
        self.assertEqual(self.runtime.runs[rid]['status'], 'completed')
        self.assertIn('Wrote brief.txt', self.fixture.runner.prompts[1])
        path = '/api/crew/artifact?run=' + rid + '&path=brief.txt'
        code, raw, headers = self.fixture.request(path)
        self.assertEqual(code, 200)
        self.assertIn(b'concrete local proposal', raw)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(self.fixture.request(path, headers={'X-Data-Token': ''})[0], 401)

    def test_profile_switch_blocks_reads_writes_and_worker(self):
        rid = self.runtime.start_run(self.order)
        self.fixture.env['_ACTIVE_USER'] = 'fixture_b'
        self.assertEqual(self.fixture.request('/api/crew/state')[0], 409)
        self.assertEqual(self.fixture.request('/api/crew/templates', self.order)[0], 409)
        self.assertEqual(self.fixture.request('/jobs')[0], 409)
        self.runtime.tick()
        self.assertEqual(self.runtime.runs[rid]['status'], 'paused')
        self.assertEqual(self.fixture.runner.prompts, [])

    def test_new_profiles_have_separate_templates_and_settings(self):
        self.runtime.save_template(self.order)
        self.runtime.update_settings({'banter_enabled': False})
        self.fixture.env['_ACTIVE_USER'] = 'fixture_b'
        with patch.object(service, 'CrewRuntime', wraps=lambda *args, **kw: CrewRuntime(*args, **kw, runner=FakeRunner(), start=False)):
            code, raw, _ = self.fixture.request('/api/crew/state', headers={'X-Crew-Scope': 'fixture_b'})
        self.assertEqual(code, 200)
        state = json.loads(raw)
        self.assertEqual(state['templates'], [])
        self.assertTrue(state['settings']['banter_enabled'])
        self.assertFalse(state['settings']['personality_evolution'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
