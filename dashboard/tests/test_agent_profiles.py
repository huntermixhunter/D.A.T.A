"""Profile persistence, image validation, user isolation and HTTP authorization."""
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from unittest.mock import patch

ROOT = Path(os.environ.get('PROFILE_TEST_DASHBOARD', str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(ROOT))
import agent_profiles as profiles


def png(width=1, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data))
    raw = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    raw += chunk(b'IDAT', zlib.compress((b'\x00' + b'\x40\x90\xc0' * width) * height)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')


class Fixture:
    def __init__(self, root=None):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-profile-test-')
        self.root = Path(self.temp.name)
        agents = self.root / 'agents'
        agents.mkdir()
        (agents / 'helper.md').write_text('Original helper personality.', encoding='utf-8')
        self.env = {'_ACTIVE_USER': 'fixture_a', '_user_dir': lambda uid: self.root / uid,
                    'crew_agents_dir': lambda: agents,
                    'CREW_VOICES': {'data': {'name': 'Data'}, 'helper': {'name': 'Helper'}},
                    'DATA_BRIDGE_TOKEN': 'fixture-secret'}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _check_auth(self):
                if self.headers.get('X-Data-Token') == 'fixture-secret':
                    return True
                profiles._json(self, {'error': 'auth_required'}, 401)
                return False

            def do_GET(self):
                if not profiles.handle_http(self, fixture.env):
                    profiles._json(self, {'error': 'not found'}, 404)

            do_POST = do_GET

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, body=None, headers=None, raw=None):
        defaults = {'X-Data-Token': 'fixture-secret', 'X-Data-Request': '1',
                    'Content-Type': 'application/json', 'X-Agent-Profile-Owner': 'fixture_a'}
        defaults.update(headers or {})
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + '/agent-profiles', data=data, headers=defaults)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.load(response)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()


class ProfilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def setUp(self):
        self.env = self.fixture.env
        self.env['_ACTIVE_USER'] = 'fixture_a'
        for uid in ['fixture_a', 'fixture_b']:
            path = self.env['_user_dir'](uid) / 'agent_profiles.json'
            path.unlink(missing_ok=True)

    def save(self, agent='data', **changes):
        return profiles.update(self.env, {'owner': 'fixture_a', 'id': agent, **changes})

    def profile(self, agent='data'):
        return next(item for item in profiles.snapshot(self.env)['agents'] if item['id'] == agent)

    def test_main_assistant_exists_without_personality_file(self):
        self.assertEqual(self.profile()['name'], 'Data')
        self.assertFalse((self.env['crew_agents_dir']() / 'data.md').exists())
        self.save(name='Nova')
        self.assertEqual(self.profile()['name'], 'Nova')

    def test_persists_after_reload_and_preserves_personality_and_id(self):
        path = self.env['crew_agents_dir']() / 'helper.md'
        before = path.read_bytes()
        self.save('helper', name='  Reviewer  ', avatar=png())
        importlib.reload(profiles)
        self.assertEqual(self.profile('helper')['name'], 'Reviewer')
        self.assertEqual(self.profile('helper')['avatar'], png())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.profile('helper')['id'], 'helper')

    def test_name_and_picture_can_be_changed_independently(self):
        self.save(name='Nova', avatar=png())
        self.save(name='Aster')
        self.assertEqual(self.profile()['avatar'], png())
        self.save(avatar='')
        self.assertEqual(self.profile()['name'], 'Aster')
        self.assertEqual(self.profile()['avatar'], '')

    def test_reset_survives_reload(self):
        self.save(name='Nova', avatar=png())
        self.save(name='', avatar='')
        importlib.reload(profiles)
        self.assertEqual(self.profile()['name'], 'Data')
        self.assertEqual(self.profile()['avatar'], '')

    def test_profiles_are_separate(self):
        self.save(name='Private A', avatar=png())
        self.env['_ACTIVE_USER'] = 'fixture_b'
        self.assertEqual(self.profile()['name'], 'Data')
        self.assertEqual(self.profile()['avatar'], '')
        profiles.update(self.env, {'owner': 'fixture_b', 'id': 'data', 'name': 'Private B'})
        self.env['_ACTIVE_USER'] = 'fixture_a'
        self.assertEqual(self.profile()['name'], 'Private A')

    def test_legacy_picture_is_preserved_only_for_original_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = base64.b64decode(png().split(',')[1])
            (Path(directory) / 'data.png').write_bytes(raw)
            with patch.dict(self.env, {'AGENT_AVATAR_DIR': Path(directory), '_USERS': {'fixture_a': {}, 'fixture_b': {}}}):
                self.assertEqual(self.profile()['avatar'], png())
                self.save(name='Nova')
                self.assertEqual(self.profile()['avatar'], png())
                self.env['_ACTIVE_USER'] = 'fixture_b'
                self.assertEqual(self.profile()['avatar'], '')
                self.env['_ACTIVE_USER'] = 'fixture_a'
                self.save(avatar='')
                self.assertEqual(self.profile()['avatar'], '')

    def test_stale_editor_cannot_save_into_another_user(self):
        self.env['_ACTIVE_USER'] = 'fixture_b'
        code, _ = self.fixture.request({'owner': 'fixture_a', 'id': 'data', 'name': 'Wrong'})
        self.assertEqual(code, 409)
        self.assertFalse((self.env['_user_dir']('fixture_b') / 'agent_profiles.json').exists())

    def test_stale_reader_cannot_fetch_another_user(self):
        self.env['_ACTIVE_USER'] = 'fixture_b'
        code, body = self.fixture.request()
        self.assertEqual(code, 409)
        self.assertNotIn('agents', body)

    def test_invalid_names_do_not_change_the_existing_profile(self):
        self.save(name='Nova')
        for value in ['x' * 61, 'two\nlines', 'null\x00byte', 12, None, []]:
            with self.subTest(value=value), self.assertRaises(profiles.ProfileError):
                self.save(name=value)
        self.assertEqual(self.profile()['name'], 'Nova')

    def test_names_are_stored_as_literal_text(self):
        name = '<img src=x onerror=alert(1)> & "Nova"'
        self.save(name=name)
        self.assertEqual(self.profile()['name'], name)

    def test_invalid_images_preserve_both_fields(self):
        self.save(name='Nova', avatar=png())
        for value in ['https://example.invalid/picture.png', 'data:image/svg+xml;base64,PHN2Zz4=',
                      'data:image/png;base64,aGVsbG8=', png(1025, 1), None, 0]:
            with self.subTest(value=str(value)[:60]), self.assertRaises(profiles.ProfileError):
                self.save(name='Wrong', avatar=value)
        self.assertEqual(self.profile()['name'], 'Nova')
        self.assertEqual(self.profile()['avatar'], png())

    def test_corrupt_png_crc_rejected(self):
        raw = bytearray(base64.b64decode(png().split(',')[1]))
        raw[30] ^= 1
        with self.assertRaises(profiles.ProfileError):
            self.save(avatar='data:image/png;base64,' + base64.b64encode(raw).decode())

    def test_unknown_id_and_traversal_rejected(self):
        for agent in ['../data', '..\\data', '/data', 'unknown', None]:
            with self.subTest(agent=agent), self.assertRaises(profiles.ProfileError):
                self.save(agent, name='Name')

    def test_interrupted_write_preserves_previous_profile(self):
        self.save(name='Nova')
        with patch.object(profiles.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.save(name='Wrong')
        self.assertEqual(self.profile()['name'], 'Nova')
        self.assertEqual(list(self.env['_user_dir']('fixture_a').glob('*.tmp')), [])

    def test_concurrent_different_agent_edits_are_preserved(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.save, 'data', name='Nova'), pool.submit(self.save, 'helper', name='Guide')]
            for future in futures:
                future.result()
        self.assertEqual(self.profile()['name'], 'Nova')
        self.assertEqual(self.profile('helper')['name'], 'Guide')

    def test_http_round_trip_and_authentication(self):
        code, result = self.fixture.request({'owner': 'fixture_a', 'id': 'data', 'name': 'Nova', 'avatar': png()})
        self.assertEqual(code, 200)
        self.assertEqual(result['agents'][0]['name'], 'Nova')
        code, _ = self.fixture.request(headers={'X-Data-Token': ''})
        self.assertEqual(code, 401)

    def test_same_origin_custom_header_and_content_type_required(self):
        for headers, status in [({'X-Data-Request': ''}, 403), ({'Origin': 'https://other.invalid'}, 403),
                                ({'Sec-Fetch-Site': 'cross-site'}, 403), ({'Host': 'rebind.invalid'}, 200),
                                ({'Content-Type': 'text/plain'}, 415)]:
            with self.subTest(headers=headers):
                code, _ = self.fixture.request({'owner': 'fixture_a', 'id': 'data', 'name': 'Nova'}, headers)
                self.assertEqual(code, status)

    def test_unauthenticated_remote_host_rejected(self):
        with patch.dict(self.env, {'DATA_BRIDGE_TOKEN': ''}):
            code, _ = self.fixture.request(headers={'Host': 'rebind.invalid'})
        self.assertEqual(code, 403)

    def test_bad_json_and_unknown_fields_rejected(self):
        for raw in [b'{', b'[]', b'null', b'{"owner":"fixture_a","id":"data","admin":true}']:
            code, _ = self.fixture.request(raw=raw)
            self.assertEqual(code, 400)

    def test_oversized_body_rejected(self):
        # The server must reject the declared size before receiving any body.
        # Sending unread bytes can cause Windows to reset the socket on close.
        code, _ = self.fixture.request(raw=b'', headers={'Content-Length': str(profiles.MAX_BODY + 1)})
        self.assertEqual(code, 413)

    def test_crew_deck_returns_saved_names_and_pictures(self):
        from test_crew_deck import Fixture as CrewFixture
        from crew_deck.runtime import OFFICERS
        fixture = CrewFixture()
        try:
            agent = next(iter(OFFICERS))
            source = fixture.env['crew_agents_dir']() / f'{agent}.md'
            source.write_text('Original personality.', encoding='utf-8')
            profiles.update(fixture.env, {'owner': 'fixture_a', 'id': agent, 'name': 'Guide', 'avatar': png()})
            code, raw, _ = fixture.request('/api/crew/state')
            self.assertEqual(code, 200)
            state = json.loads(raw)
            member = next(item for item in state['roster'] if item['id'] == agent)
            self.assertEqual(member['name'], 'Guide')
            self.assertEqual(member['avatar'], png())
            self.assertEqual(state['assistant']['id'], 'data')
            self.assertEqual(source.read_text(encoding='utf-8'), 'Original personality.')
        finally:
            fixture.close()


if __name__ == '__main__':
    unittest.main()
