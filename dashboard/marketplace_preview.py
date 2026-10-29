"""Serve the dashboard, marketplace and skill search without AI or schedules.

For local development only. Runtime data is in users/marketplace-preview by
default, separate from the regular marketplace. This server binds loopback.
Settings > Upgrades searches the public skills.sh catalog only when typed into.
Open /#upgrades to preview it directly. Other integrations remain inactive.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from marketplace import Marketplace, ROOT, handle_http
import skill_search
import advanced_tools


def make_handler(store):
    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if advanced_tools.handle_http(self, store.directory / "advanced-tools"):
                return
            if handle_http(self, store):
                return
            if skill_search.handle_http(self):
                return
            path = urlsplit(self.path).path
            files = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                     "/app.js": ("app.js", "application/javascript"), "/theme.css": ("theme.css", "text/css"),
                     "/skill_search.js": ("skill_search.js", "application/javascript"),
                     "/skill_search.css": ("skill_search.css", "text/css"),
                     "/advanced_tools.js": ("advanced_tools.js", "application/javascript"),
                     "/advanced_tools.css": ("advanced_tools.css", "text/css"),
                     "/marketplace.js": ("marketplace.js", "application/javascript"),
                     "/marketplace.css": ("marketplace.css", "text/css"), "/favicon.ico": ("favicon.ico", "image/x-icon")}
            files.update({"/forum.js": ("forum.js", "application/javascript"), "/forum.css": ("forum.css", "text/css")})
            if path in files:
                name, mime = files[path]
                payload = (ROOT / name).read_bytes()
                if name == "index.html":
                    payload = payload.replace(b'<body class="theme-minimal">', b'<body class="theme-minimal" data-marketplace-preview>')
                status = 200
            else:
                status, mime = 404, "application/json"
                payload = json.dumps({"error": "This preview runs the marketplace and skill search. AI and dashboard integrations are inactive."}).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if advanced_tools.handle_http(self, store.directory / "advanced-tools"):
                return
            if not handle_http(self, store):
                self.send_response(403)
                self.end_headers()

    return PreviewHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7800)
    parser.add_argument("--data-dir", type=Path, default=ROOT.parent / "users" / "marketplace-preview")
    args = parser.parse_args()
    store = Marketplace(args.data_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    print(f"Marketplace preview: http://localhost:{args.port}/#marketplace", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
