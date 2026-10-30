"""Authenticated, profile-scoped dashboard HTTP integration."""
from pathlib import Path
import atexit
import json
import mimetypes
import secrets
import sqlite3
import threading
import urllib.parse

from .runtime import CrewRuntime, CrewError, workspace_path

ROOT = Path(__file__).resolve().parent
PUBLIC = {'index.html', 'host.js', 'host.css', 'access.js', 'deck.js', 'deck.css',
          'data.js', 'dialogue.js', 'controls.js', 'controls.css'}
ASSET_TYPES = {'.png', '.webp', '.json'}
_lock = threading.RLock()
_runtimes = {}


def _guard(env, owner):
    if env.get('_ACTIVE_USER') != owner:
        raise CrewError('The active profile changed. Reopen Crew Deck from the dashboard.')


def _runtime(env, owner):
    _guard(env, owner)
    root = env['_user_dir'](owner) / 'crew_deck'
    key = str(root.resolve())
    with _lock:
        if key not in _runtimes:
            agents = env['crew_agents_dir']() if 'crew_agents_dir' in env else Path.home() / '.claude' / 'agents'
            _runtimes[key] = CrewRuntime(root, agents, owner=owner, guard=lambda: _guard(env, owner))
        return _runtimes[key]


def initialize(env):
    """Restore only the active profile's existing mission journal at startup."""
    owner = env.get('_ACTIVE_USER', '')
    if owner and (env['_user_dir'](owner) / 'crew_deck' / 'crew.sqlite3').is_file():
        try:
            _runtime(env, owner)
        except (OSError, ValueError, sqlite3.Error) as error:
            logger = env.get('log')
            if logger:
                logger.warning('Crew mission recovery unavailable: %s', error)


def _close():
    for runtime in list(_runtimes.values()):
        runtime.close()


atexit.register(_close)


def _send(handler, data, mime, status=200, extra=None):
    handler.send_response(status)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Referrer-Policy', 'same-origin')
    handler.send_header('X-Frame-Options', 'SAMEORIGIN')
    for key, value in (extra or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(data)


def _json(handler, value, status=200):
    _send(handler, json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', status)


def _gate(handler, env):
    if handler.headers.get('X-Data-Request') != '1':
        raise CrewError('Open Crew Deck from the dashboard.')
    if handler.headers.get('Sec-Fetch-Site', '') not in {'', 'same-origin', 'none'}:
        raise CrewError('Cross-origin crew requests are not allowed.')
    origin = handler.headers.get('Origin', '')
    host = handler.headers.get('Host', '')
    if origin and urllib.parse.urlsplit(origin).netloc != host:
        raise CrewError('Origin not allowed.')
    # The normal bridge authentication gate verifies tokens. Without a token,
    # only loopback hosts are accepted, including protection against rebinding.
    hostname = urllib.parse.urlsplit('http://' + host).hostname
    if hostname not in {'localhost', '127.0.0.1', '::1'}:
        configured = env.get('DATA_BRIDGE_TOKEN')
        remote = env.get('REMOTE_ACCESS')
        if not configured and remote:
            configured = remote.remote_key()
        if not configured:
            raise CrewError('Remote crew requests require configured dashboard authentication.')


def handle_http(handler, env):
    parsed = urllib.parse.urlsplit(handler.path)
    path = parsed.path
    if path == '/crew-deck' and handler.command == 'GET':
        _send(handler, b'', 'text/plain', 302, {'Location': '/crew-deck/'})
        return True
    if path.startswith('/crew-deck/') and handler.command == 'GET':
        relative = urllib.parse.unquote(path[len('/crew-deck/'):]) or 'index.html'
        target = (ROOT / relative).resolve()
        allowed = relative in PUBLIC or (relative.startswith('assets/') and target.suffix in ASSET_TYPES)
        if not allowed or '..' in relative or '\\' in relative or not target.is_relative_to(ROOT) or not target.is_file():
            _json(handler, {'error': 'Not found'}, 404)
        else:
            mime = {'.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.html': 'text/html'}.get(target.suffix)
            mime = mime or mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            _send(handler, target.read_bytes(), mime)
        return True
    if not path.startswith('/api/crew/'):
        # Child activity polls carry the same profile lock as mission requests.
        scope = handler.headers.get('X-Crew-Scope')
        if scope and scope != env.get('_ACTIVE_USER'):
            _json(handler, {'error': 'The active profile changed.'}, 409)
            return True
        return False
    payload = b''
    if handler.command == 'POST':
        try:
            length = int(handler.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not 0 < length <= 100000:
            handler.close_connection = True
            _json(handler, {'error': 'Invalid request size'}, 413)
            return True
        # Consume bounded bodies before rejecting an expired profile or token.
        # Closing a socket with unread input can reset the error response.
        payload = handler.rfile.read(length)
    if not handler._check_auth():
        return True
    try:
        _gate(handler, env)
        owner = handler.headers.get('X-Crew-Scope', '')
        _guard(env, owner)
        runtime = _runtime(env, owner)
        query = urllib.parse.parse_qs(parsed.query)
        if handler.command == 'GET':
            if path == '/api/crew/state':
                data = runtime.snapshot()
            elif path == '/api/crew/personality':
                data = runtime.personality(query.get('id', [''])[0])
            elif path == '/api/crew/artifact':
                with runtime.lock:
                    run = runtime.runs.get(query.get('run', [''])[0])
                    if not run:
                        raise CrewError('Mission not found')
                    target = workspace_path(Path(run['workspace']), query.get('path', [''])[0])
                    if not target.is_file() or target.stat().st_size > 100000:
                        raise CrewError('Artifact not found or exceeds 100 KB')
                    raw = target.read_bytes()
                _guard(env, owner)
                _send(handler, raw, 'text/plain; charset=utf-8', extra={
                    'Content-Disposition': "attachment; filename*=UTF-8''" + urllib.parse.quote(target.name),
                    'Content-Security-Policy': "default-src 'none'; sandbox"})
                return True
            else:
                _json(handler, {'error': 'Unknown endpoint'}, 404)
                return True
        elif handler.command == 'POST':
            if not secrets.compare_digest(handler.headers.get('X-Crew-CSRF', ''), runtime.csrf):
                raise CrewError('Reload Crew Deck before saving changes.')
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise CrewError('JSON content is required')
            body = json.loads(payload)
            if not isinstance(body, dict):
                raise CrewError('Request must be an object')
            if path == '/api/crew/settings':
                runtime.update_settings(body.get('settings'))
            elif path == '/api/crew/templates':
                runtime.save_template(body)
            elif path == '/api/crew/templates/delete':
                runtime.delete_template(body.get('id', ''))
            elif path == '/api/crew/runs':
                runtime.start_run(body)
            elif path == '/api/crew/runs/control':
                runtime.control(body.get('id', ''), body.get('action', ''))
            elif path == '/api/crew/runs/message':
                runtime.steer(body.get('id', ''), body.get('message', ''))
            elif path == '/api/crew/runs/resolve':
                runtime.resolve(body.get('id', ''), body.get('decision', ''), body.get('note', ''))
            elif path == '/api/crew/personality/restore':
                runtime.restore(body.get('id', ''), body.get('version', ''))
            else:
                _json(handler, {'error': 'Unknown endpoint'}, 404)
                return True
            data = runtime.snapshot()
        else:
            _json(handler, {'error': 'Method not allowed'}, 405)
            return True
        _guard(env, owner)
        if 'roster' in data:
            import agent_profiles
            appearance = agent_profiles.snapshot(env, owner)
            profiles = {item['id']: item for item in appearance['agents']}
            for member in data['roster']:
                profile = profiles.get(member['id'])
                if profile:
                    member.update(name=profile['name'], avatar=profile['avatar'])
            data['assistant'] = profiles.get('data', {})
        _guard(env, owner)
        _json(handler, data)
    except CrewError as exc:
        _json(handler, {'error': str(exc)}, 409)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        _json(handler, {'error': 'Crew request could not be applied: ' + str(exc)}, 400)
    return True
