"""Local setup and package boundaries, exercised without installers or providers."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import advanced_tools as at
import marketplace as mp
from marketplace_preview import make_handler

PACKS = {'web-design-studio', 'brand-kit', 'content-repurposer', 'website-audit',
         'client-proposal-kit', 'research-desk', 'marketplace-creator-kit',
         'framepack-video-studio', 'image-studio', 'local-transcription'}


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manager = at.AdvancedTools(self.directory / 'settings', lambda: {
            'os': 'Windows', 'gpu_name': 'NVIDIA GeForce RTX 5070', 'vram_total_gb': 8})

    def test_catalog_is_read_only_and_checks_are_explicit(self):
        with patch.object(at, 'probe', side_effect=AssertionError('Unrequested probe')):
            catalog = self.manager.catalog()
        self.assertEqual(len(catalog['tools']), 3)
        self.assertFalse(self.manager.path.exists())
        self.assertFalse(any(t['saved'] for t in catalog['tools']))

    def test_save_survives_reload_and_invalid_save_preserves_settings(self):
        self.manager.save('comfyui', {'folder': str(self.directory), 'url': 'http://localhost:8188/'})
        before = self.manager.path.read_bytes()
        with self.assertRaises(at.SetupError):
            self.manager.save('comfyui', {'url': 'https://public.example:8188'})
        self.assertEqual(before, self.manager.path.read_bytes())
        reloaded = at.AdvancedTools(self.manager.directory).catalog()
        item = next(t for t in reloaded['tools'] if t['id'] == 'comfyui')
        self.assertEqual(item['settings']['url'], 'http://127.0.0.1:8188')
        self.assertTrue(item['saved'])

    def test_forget_keeps_application_and_model(self):
        executable = self.directory / 'whisper-cli'
        executable.write_text('test marker, never executed')
        model = self.directory / 'model.bin'
        model.write_bytes(b'test marker')
        self.manager.save('whispercpp', {'folder': str(self.directory), 'model': str(model)})
        self.assertEqual(self.manager.check('whispercpp')['state'], 'files-found')
        self.manager.forget('whispercpp')
        self.assertTrue(executable.exists() and model.exists())
        self.assertEqual(self.manager.check('whispercpp')['state'], 'incomplete')

    def test_profiles_use_separate_directories(self):
        self.manager.save('framepack', {'url': 'http://localhost:7861'})
        other = at.AdvancedTools(self.directory / 'another-profile')
        self.assertFalse(any(t['saved'] for t in other.catalog()['tools']))

    def test_corrupt_settings_are_not_overwritten(self):
        self.manager.directory.mkdir()
        self.manager.path.write_text('{broken')
        with self.assertRaises(at.SetupError):
            self.manager.save('comfyui', {})
        self.assertEqual(self.manager.path.read_text(), '{broken')

    def test_connection_addresses_are_literal_loopback_only(self):
        invalid = ['http://example.com:8188', 'http://127.1:8188', 'http://2130706433:8188',
                   'http://127.0.0.1:8188/config', 'http://user:password@localhost:8188',
                   'http://localhost:8188?query=yes', 'http://localhost:8188#fragment',
                   'http://localhost:7777', 'http://localhost:80', 'https://localhost:8188',
                   'http://localhost:8188\\@example.com', 'http://localhost:9000']
        for address in invalid:
            with self.subTest(address=address), self.assertRaises(at.SetupError):
                at.local_url(address, (9000,))
        self.assertEqual(at.local_url('http://[::1]:8188/'), 'http://[::1]:8188')

    def test_paths_and_unknown_options_are_rejected(self):
        for path in ['relative/folder', '//server/share', '\\\\server\\share', 'bad\npath']:
            with self.subTest(path=path), self.assertRaises(at.SetupError):
                at.local_path(path)
        for tool, values in [('comfyui', {'command': 'run'}), ('whispercpp', {'url': 'http://localhost:1234'}),
                             ('comfyui', {'model': str(self.directory / 'model')}), ('unknown', {})]:
            with self.subTest(tool=tool), self.assertRaises(at.SetupError):
                self.manager.save(tool, values)

    def test_hardware_labels_do_not_claim_generation_is_tested(self):
        self.assertEqual(at.compatibility('framepack', {'os': 'Darwin'})['state'], 'unsupported')
        self.assertEqual(at.compatibility('framepack', {'os': 'Windows'})['state'], 'unknown')
        self.assertEqual(at.compatibility('framepack', {'os': 'Linux', 'gpu_name': 'NVIDIA RTX 4090', 'vram_total_gb': 24})['state'], 'compatible')
        self.assertEqual(at.compatibility('framepack', {'os': 'Windows', 'gpu_name': 'NVIDIA RTX 3050', 'vram_total_gb': 4})['state'], 'unsupported')
        self.assertEqual(at.compatibility('whispercpp', {'os': 'Darwin'})['label'], 'CPU supported')


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.mode = 'comfyui'
        test = self
        class App(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                test.requests.append(self.path)
                if test.mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', '/unexpected-follow')
                    self.end_headers()
                    return
                payload = {'system': {}, 'devices': []} if test.mode == 'comfyui' else {'components': [], 'title': 'FramePack'} if test.mode == 'framepack' else {'components': []}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b' ' * (256 * 1024 + 1) if test.mode == 'large' else json.dumps(payload).encode())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), App)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.address = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_apps_must_identify_themselves(self):
        self.assertEqual(at.probe(at.TOOLS['comfyui'], self.address)['state'], 'connected')
        self.mode = 'generic'
        self.assertEqual(at.probe(at.TOOLS['framepack'], self.address)['state'], 'unconfirmed')
        self.mode = 'framepack'
        self.assertEqual(at.probe(at.TOOLS['framepack'], self.address)['state'], 'connected')
        self.assertEqual(self.requests, ['/system_stats', '/config', '/config'])

    def test_redirects_and_large_responses_are_refused(self):
        self.mode = 'redirect'
        self.assertEqual(at.probe(at.TOOLS['comfyui'], self.address)['state'], 'offline')
        self.assertEqual(self.requests, ['/system_stats'])
        self.mode = 'large'
        self.assertEqual(at.probe(at.TOOLS['comfyui'], self.address)['state'], 'offline')


class HttpAndCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = mp.Marketplace(cls.temp.name)
        class Protected(make_handler(cls.store)):
            def _check_auth(self):
                if self.headers.get('X-Data-Token') == 'test-token':
                    return True
                self.send_response(401)
                self.end_headers()
                return False
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Protected)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, method, path='catalog', data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        sent = {'X-Data-Request': '1', 'X-Data-Token': 'test-token', 'Content-Type': 'application/json'}
        sent.update(headers or {})
        conn.request(method, '/advanced-tools/' + path, None if data is None else json.dumps(data), sent)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_http_save_load_forget(self):
        self.assertEqual(self.request('POST', 'comfyui/save', {'url': 'http://localhost:8188'})[0], 200)
        status, raw = self.request('GET')
        self.assertEqual(status, 200)
        self.assertTrue(next(t for t in json.loads(raw)['tools'] if t['id'] == 'comfyui')['saved'])
        self.assertEqual(self.request('POST', 'comfyui/forget', {})[0], 200)

    def test_http_auth_origin_host_and_proxy_boundaries(self):
        for headers, expected in [({'X-Data-Token': ''}, 401), ({'X-Data-Request': ''}, 403),
                                   ({'Origin': 'https://unrelated.example'}, 403),
                                   ({'Sec-Fetch-Site': 'same-site'}, 403),
                                   ({'X-Forwarded-For': '127.0.0.1'}, 403),
                                   ({'Host': 'unrelated.example'}, 403)]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request('GET', headers=headers)[0], expected)
                self.assertEqual(self.request('POST', 'comfyui/save', {}, headers)[0], expected)

    def test_http_input_bounds_and_own_port(self):
        for path, data, status in [('unknown/save', {}, 404), ('comfyui/save', [], 400),
                                   ('comfyui/check', {'command': 'anything'}, 404),
                                   ('comfyui/save', {'folder': 'x' * 9000}, 413),
                                   ('comfyui/save', {'url': f'http://localhost:{self.server.server_port}'}, 400)]:
            with self.subTest(path=path):
                self.assertEqual(self.request('POST', path, data)[0], status)
        self.assertEqual(self.request('POST', 'comfyui/save', {}, {'Content-Type': 'text/plain'})[0], 415)

    def test_all_ten_packs_install_as_declarative_content(self):
        result, _ = self.store.account_action('register', {'username': 'pack-tester', 'name': 'Pack Tester', 'password': 'package test password'}, None, '', 'test')
        account = result['account']
        for pack in PACKS:
            item = self.store.detail('library-' + pack, account)
            self.assertEqual(item['price_cents'], 0)
            self.assertEqual(item['review_status'], 'unverified')
            self.store.install(item['id'], {'version': item['latest'], 'sha256': item['sha256'], 'permissions': item['manifest']['permissions'], 'acknowledge': True}, account)
        runtime = self.store.runtime(account)
        self.assertEqual(len(runtime), 10)
        self.assertEqual(sum(c['kind'] == 'page' for p in runtime for c in p['components']), 3)
        self.assertTrue(all(c['kind'] in ('page', 'skill') for p in runtime for c in p['components']))
        self.assertEqual(self.store.runtime(), [])
        self.assertFalse((Path(self.temp.name) / 'advanced-tools' / 'advanced_tools.json').exists())


if __name__ == '__main__':
    unittest.main()
