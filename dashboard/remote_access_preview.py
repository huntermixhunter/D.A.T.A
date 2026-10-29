"""Read-only local preview of Settings > Remote Access. No connected services.

Run: python dashboard/remote_access_preview.py --port 7803
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import remote_access

ROOT = Path(__file__).resolve().parent


class PreviewState:
    def status(self):
        return {"preview": True, "keep_running": False, "daemon": False,
                "mobile": {"enabled": False, "state": "ready", "url": "",
                           "message": "After installing Tailscale, check the connection in your running DATA dashboard."},
                "telegram": {"configured": False, "enabled": False, "paired": False,
                             "running": False, "username": "", "owner_name": "", "profile_active": True, "error": ""}}

    def action(self, *_args):
        raise remote_access.SetupError("This preview cannot activate remote access. Use Settings in your running DATA dashboard.", 403)


def make_handler(manager=None):
    manager = manager or PreviewState()

    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if remote_access.handle_http(self, manager):
                return
            path = urlsplit(self.path).path
            files = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html")}
            for name in ("app", "skill_search", "remote_access"):
                files[f"/{name}.js"] = (f"{name}.js", "application/javascript")
            for name in ("theme", "skill_search", "remote_access"):
                files[f"/{name}.css"] = (f"{name}.css", "text/css")
            if path in files:
                name, mime = files[path]
                body, code = (ROOT / name).read_bytes(), 200
            else:
                body, code, mime = json.dumps({"error": "This preview only runs Remote Access settings."}).encode(), 404, "application/json"
            self.send_response(code)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_POST(self):
            if not remote_access.handle_http(self, manager):
                self.send_response(403)
                self.end_headers()

    return PreviewHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7803)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler())
    print(f"Remote access preview: http://localhost:{args.port}/#remote-access", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
