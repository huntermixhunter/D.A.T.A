"""Local DATA Arcade preview. No AI, schedules, payments, or live integrations."""
import argparse
import ast
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parent


def static_routes(root=ROOT):
    tree = ast.parse((root / "bridge_server.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STATIC_FILES" for t in node.targets):
            routes = ast.literal_eval(node.value)
            crew = ast.parse((root / "crew_deck/service.py").read_text(encoding="utf-8"))
            for item in crew.body:
                if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PUBLIC" for t in item.targets):
                    for name in ast.literal_eval(item.value):
                        routes["/crew-deck/" + name] = ("crew_deck/" + name, mimetypes.guess_type(name)[0] or "application/octet-stream")
            routes["/crew-deck/"] = ("crew_deck/index.html", "text/html; charset=utf-8")
            return routes
    raise RuntimeError("Dashboard static routes are missing")


class Handler(BaseHTTPRequestHandler):
    routes = {}
    root = ROOT

    def log_message(self, *_args):
        pass

    def send(self, payload, mime="application/json; charset=utf-8", status=200):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/arcade":
            self.send_response(308)
            self.send_header("Location", "/arcade/")
            self.end_headers()
            return
        route = self.routes.get(path)
        relative = path.lstrip("/").replace("crew-deck/assets/", "crew_deck/assets/", 1)
        target = (self.root / (route[0] if route else relative)).resolve()
        asset = target.suffix.lower() in {".png", ".jpg", ".webp", ".svg", ".ico", ".woff2", ".mp3", ".wav"}
        asset = asset or (relative.startswith("crew_deck/assets/") and target.suffix == ".json")
        if target.is_relative_to(self.root) and target.is_file() and (route or asset):
            data = target.read_bytes()
            mime = route[1] if route else mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send(data, mime)
            return
        fixtures = {
            "/user/list": {"active": "arcade-preview", "users": [{"id": "arcade-preview", "name": "Preview", "rank": "Player"}]},
            "/user/active": {"active": "arcade-preview"},
            "/api/crew/state": {"scope": "arcade-preview", "settings": {}, "roster": [], "runs": [], "templates": [], "capabilities": {"ready": False}},
            "/agent-profiles": {"owner": "arcade-preview", "agents": []},
            "/providers": {"providers": [], "active": "", "needs_auth": []},
            "/status": {"status": "online", "step": "idle", "detail": "Local arcade preview"},
            "/health": {"status": "online"}, "/mode": {"mode": "conversation"},
            "/agents": {"agents": []}, "/memory": {"content": "", "path": ""},
            "/conversations/recent": {"turns": []}, "/ui-events": {"events": []},
            "/standing_orders": {"orders": []}, "/jobs": {"jobs": []},
            "/briefing": {"items": []}, "/tunnel_url": {"url": ""},
            "/vitals": {"turns": 0, "memory_kb": 0, "api_tools": 0, "claude_skills": 0, "uptime": "PREVIEW"},
            "/memory-stats": {"used_pct": 0, "history_turns": 0, "max_history_turns": 20},
            "/update/check": {"status": "ok", "available": False},
        }
        if path.startswith("/arcade/") or ".." in path or target.suffix:
            self.send({"error": "Not found"}, status=404)
        elif path == "/vitals_fast":
            self.send(b": local preview\n\n", "text/event-stream")
        else:
            self.send(fixtures.get(path, {}))

    def do_POST(self):
        self.close_connection = True
        self.send({"error": "Live integrations are disabled in the arcade preview."}, status=409)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7817)
    parser.add_argument("--root", type=Path, default=ROOT, help="Dashboard source to preview with synthetic profiles")
    args = parser.parse_args()
    Handler.root = args.root.resolve()
    Handler.routes = static_routes(Handler.root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"DATA Arcade: http://localhost:{args.port}/#arcade", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
