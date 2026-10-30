"""Local, user-scoped display names and raster portraits for dashboard agents."""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import threading
import urllib.parse

_lock = threading.RLock()
MAX_IMAGE = 512 * 1024
MAX_BODY = 720000
_ID = re.compile(r"[a-z0-9_-]{1,80}")


class ProfileError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _owner(env, expected=None):
    owner = env.get('_ACTIVE_USER', '')
    if not owner or (expected is not None and expected != owner):
        raise ProfileError('The active profile changed. Reload settings before saving.', 409)
    return owner


def _read(env, owner):
    path = env['_user_dir'](owner) / 'agent_profiles.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        value = {}
    if not isinstance(value, dict) or any(not isinstance(item, dict) for item in value.values()):
        raise ProfileError('Saved agent profiles could not be read.', 500)
    # The earlier uploader stored shared pictures. Preserve those for the
    # original dashboard owner, without copying them to other user profiles.
    legacy = env.get('AGENT_AVATAR_DIR')
    original_owner = next(iter(env.get('_USERS', {})), None)
    if legacy and owner == original_owner:
        for path in sorted(Path(legacy).glob('*')):
            if not _ID.fullmatch(path.stem) or path.suffix not in ('.png', '.jpg'):
                continue
            if 'avatar' in value.get(path.stem, {}) or not path.is_file() or path.stat().st_size > MAX_IMAGE:
                continue
            mime = 'png' if path.suffix == '.png' else 'jpeg'
            try:
                avatar = _image('data:image/' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii'), max_side=4096)
            except ProfileError:
                continue
            value.setdefault(path.stem, {})['avatar'] = avatar
    return value


def _agents_dir(env):
    return env.get('crew_agents_dir', lambda: Path.home() / '.claude' / 'agents')()


def _defaults(env):
    names = {'data': 'Data'}
    names.update({key: spec['name'] for key, spec in env.get('CREW_VOICES', {}).items()})
    for path in sorted(_agents_dir(env).glob('*.md')):
        if _ID.fullmatch(path.stem):
            names.setdefault(path.stem, path.stem.title())
    return names


def display_name(env, agent_id, default):
    with _lock:
        saved = _read(env, _owner(env)).get(agent_id, {})
        return saved.get('name') or default


def snapshot(env, expected_owner=None):
    with _lock:
        owner = _owner(env, expected_owner)
        saved = _read(env, owner)
        agents = []
        for agent_id, default in _defaults(env).items():
            profile = saved.get(agent_id, {})
            agents.append({'id': agent_id, 'name': profile.get('name') or default,
                           'default_name': default, 'custom_name': profile.get('name', ''),
                           'avatar': profile.get('avatar', '')})
        _owner(env, owner)
        return {'owner': owner, 'agents': agents}


def _image(value, max_side=1024):
    if value == '':
        return ''
    if not isinstance(value, str) or len(value) > MAX_BODY - 1000:
        raise ProfileError('Choose a PNG or JPEG picture under 512 KB.')
    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise ProfileError('Choose a PNG or JPEG picture.')
    try:
        raw = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProfileError('The picture is not valid base64.') from exc
    if len(raw) > MAX_IMAGE:
        raise ProfileError('The picture must be under 512 KB.', 413)
    width = height = 0
    if match[1] == 'png':
        if len(raw) < 45 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
            raise ProfileError('Invalid PNG picture.')
        pos, data_seen, ended = 8, False, False
        while pos + 12 <= len(raw):
            size = int.from_bytes(raw[pos:pos + 4], 'big')
            kind = raw[pos + 4:pos + 8]
            end = pos + 8 + size
            if end + 4 > len(raw) or binascii.crc32(raw[pos + 4:end]) != int.from_bytes(raw[end:end + 4], 'big'):
                raise ProfileError('Invalid PNG picture.')
            if pos == 8:
                if size != 13:
                    raise ProfileError('Invalid PNG header.')
                width, height = struct.unpack('>II', raw[pos + 8:pos + 16])
            data_seen = data_seen or kind == b'IDAT'
            pos = end + 4
            if kind == b'IEND':
                ended = size == 0 and pos == len(raw)
                break
        if not ended or not data_seen:
            raise ProfileError('Incomplete PNG picture.')
    else:
        if raw[:2] != b'\xff\xd8' or raw[-2:] != b'\xff\xd9':
            raise ProfileError('Invalid JPEG picture.')
        pos = 2
        while pos + 4 <= len(raw):
            if raw[pos] != 255:
                raise ProfileError('Invalid JPEG picture.')
            marker = raw[pos + 1]
            if marker == 255:
                pos += 1
                continue
            size = int.from_bytes(raw[pos + 2:pos + 4], 'big')
            if size < 2 or pos + size + 2 > len(raw):
                raise ProfileError('Invalid JPEG picture.')
            if marker in (0xc0, 0xc1, 0xc2):
                if size < 8:
                    raise ProfileError('Invalid JPEG dimensions.')
                height, width = struct.unpack('>HH', raw[pos + 5:pos + 9])
                break
            if marker == 0xda:
                break
            pos += size + 2
    if not 0 < width <= max_side or not 0 < height <= max_side or width * height > 8000000:
        raise ProfileError(f'Pictures must be between 1 and {max_side} pixels per side and under 8 megapixels.')
    return value


def update(env, body):
    if not isinstance(body, dict):
        raise ProfileError('A JSON object is required.')
    if set(body) - {'owner', 'id', 'name', 'avatar'} or not ({'name', 'avatar'} & body.keys()):
        raise ProfileError('Provide a display name or picture.')
    agent_id = body.get('id')
    if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
        raise ProfileError('Invalid agent id.')
    changes = {}
    if 'name' in body:
        name = body['name']
        if not isinstance(name, str) or len(name) > 60 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ProfileError('Use a display name of up to 60 characters, on one line.')
        changes['name'] = name.strip()
    if 'avatar' in body:
        changes['avatar'] = _image(body['avatar'])
    with _lock:
        owner = _owner(env, body.get('owner', ''))
        if agent_id not in _defaults(env):
            raise ProfileError('Unknown agent.', 404)
        saved = _read(env, owner)
        saved[agent_id] = {**saved.get(agent_id, {}), **changes}
        directory = env['_user_dir'](owner)
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.agent-profiles-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(saved, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            _owner(env, owner)
            os.replace(name, directory / 'agent_profiles.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return snapshot(env, owner)


def _json(handler, value, status=200):
    raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    handler.wfile.write(raw)


def handle_http(handler, env):
    if urllib.parse.urlsplit(handler.path).path != '/agent-profiles':
        return False
    try:
        raw = b''
        if handler.command == 'POST':
            try:
                length = int(handler.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_BODY:
                handler.close_connection = True
                raise ProfileError('Invalid request size.', 413)
            raw = handler.rfile.read(length)
        if not handler._check_auth():
            return True
        # A custom header and same-origin checks also protect bridges without a token.
        if handler.headers.get('X-Data-Request') != '1':
            raise ProfileError('Open profiles from the dashboard.', 403)
        origin = handler.headers.get('Origin', '')
        host = handler.headers.get('Host', '')
        if handler.headers.get('Sec-Fetch-Site', '') not in ('', 'same-origin', 'none') or (origin and urllib.parse.urlsplit(origin).netloc != host):
            raise ProfileError('Cross-origin profile requests are not allowed.', 403)
        hostname = urllib.parse.urlsplit('http://' + host).hostname
        remote = env.get('REMOTE_ACCESS')
        authenticated = env.get('DATA_BRIDGE_TOKEN') or (remote and remote.remote_key())
        if hostname not in ('localhost', '127.0.0.1', '::1') and not authenticated:
            raise ProfileError('Remote profiles require dashboard authentication.', 403)
        if handler.command == 'GET':
            _json(handler, snapshot(env, handler.headers.get('X-Agent-Profile-Owner')))
        elif handler.command == 'POST':
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ProfileError('JSON content is required.', 415)
            _json(handler, update(env, json.loads(raw)))
        else:
            raise ProfileError('Method not allowed.', 405)
    except ProfileError as exc:
        _json(handler, {'error': str(exc)}, exc.status)
    except (ValueError, UnicodeError):
        _json(handler, {'error': 'Profile data could not be read. Nothing was changed.'}, 400)
    except OSError:
        _json(handler, {'error': 'Profiles could not be saved. Check that the user folder is writable.'}, 500)
    return True
