"""Loopback-only preview of the explicit static deployment directory."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        headers = (ROOT / 'dist' / '_headers').read_text(encoding='utf-8')
        for line in headers.splitlines():
            if line.startswith('/config.json'):
                break
            if line.startswith('  ') and ': ' in line:
                key, value = line.strip().split(': ', 1)
                if key != 'Cache-Control':
                    self.send_header(key, value)
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=7810)
    args = parser.parse_args()
    if not (ROOT / 'dist' / 'index.html').exists():
        parser.error('Run npm run build before starting the preview.')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(ROOT / 'dist')))
    print(f'Forum preview: http://localhost:{server.server_port}', flush=True)
    server.serve_forever()
