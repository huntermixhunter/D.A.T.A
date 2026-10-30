"""Connect a dashboard to an existing portable archive, without copying its data."""
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit

LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def archive_root():
    override = os.environ.get('DATA_ARCHIVE_ROOT')
    if override:
        return Path(override).expanduser().resolve()
    from maps_offline import maps_root
    candidates = [maps_root().parent, Path(__file__).parent / 'offline']
    for drive in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        candidates.extend(Path(drive + ':/' + name) for name in ('DATA Offline', 'LCARS Offline'))
    return next((p for p in candidates if (p / 'server.py').is_file() and (p / 'library').is_dir()), candidates[0])


def status():
    root = archive_root()
    portable = (root / 'runtime/python/pythonw.exe').is_file() and (root / 'server.py').is_file()
    result = {'installed': portable, 'root': str(root), 'running': False, 'url': 'http://127.0.0.1:7788/'}
    try:
        with LOCAL.open('http://127.0.0.1:7788/api/status', timeout=2) as response:
            live = json.load(response)
        if live.get('app') in ('DATA Offline', 'LCARS Offline'):
            result.update(running=True, app=live['app'], books=len(live.get('books', [])),
                          embeddings=live.get('embeddings'), version=live.get('version', 1))
    except (OSError, ValueError):
        pass
    return result


def handle_http(handler, path, body=None):
    if not path.startswith('/offline/'):
        return False
    try:
        if body is None and path == '/offline/status':
            handler._json(status())
        elif body is not None and path == '/offline/start':
            origin = handler.headers.get('Origin')
            if origin and urlsplit(origin).netloc != handler.headers.get('Host'):
                handler._json({'error': 'Start the archive from this dashboard.'}, 403)
                return True
            current = status()
            if current['running']:
                handler._json(current)
                return True
            if not current['installed']:
                raise ValueError('Connect the archive drive first. This button starts an installed portable archive.')
            from offline_tasks import start
            root = archive_root()
            job = start(root / 'state', 'archive-server',
                [str(root / 'runtime/python/pythonw.exe'), str(root / 'server.py')], 'Portable offline library')
            handler._json({'ok': True, 'job': job})
        else:
            handler._json({'error': 'Unknown archive route'}, 404)
    except (ValueError, OSError) as exc:
        handler._json({'error': str(exc)}, 400)
    return True
