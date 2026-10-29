"""Standalone shared account, marketplace and forum service.

Run behind a TLS reverse proxy for a public community. Local development may
connect directly over loopback HTTP. Browser clients use their own local DATA
bridge; this service accepts bearer sessions and deliberately provides no CORS.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from community_transport import MAX_AUTH_BODY, MAX_BODY, MAX_RESPONSE, TOKEN_FIELD, validate_request
from forum import get_forum
from marketplace import MarketError, Marketplace, json_object, require
import shared_service


DEFAULT_DATA = Path(__file__).resolve().parent.parent / "users" / "community"


class RequestLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.windows = {}

    def check(self, peer, auth=False):
        now = time.monotonic()
        with self.lock:
            self.windows = {key: value for key, value in self.windows.items() if value[0] > now - 60}
            keys = [(("peer", peer), 180), (("global", ""), 1200)]
            if auth:
                keys.append((("auth", ""), 60))
            require(len(self.windows) < 4096, "Community service is busy. Please retry.", 429)
            for key, limit in keys:
                require(self.windows.get(key, (now, 0))[1] < limit,
                        "Too many community requests. Please try again shortly.", 429)
            for key, _ in keys:
                started, count = self.windows.get(key, (now, 0))
                self.windows[key] = (started, count + 1)


class CommunityHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_handler(store, trusted_proxy=None):
    forum = get_forum(store)
    limits = RequestLimits()
    trusted = str(ipaddress.ip_address(trusted_proxy)) if trusted_proxy else None

    class CommunityHandler(BaseHTTPRequestHandler):
        server_version = "DATA-Community"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)
            # Socket inactivity timeouts alone do not stop slow trickle uploads.
            self.deadline = threading.Timer(15, self._expire)
            self.deadline.daemon = True
            self.deadline.start()

        def _expire(self):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def finish(self):
            self.deadline.cancel()
            super().finish()

        def log_message(self, *_args):
            # URLs, usernames, request bodies and tokens are never logged.
            pass

        def peer(self):
            peer = str(ipaddress.ip_address(self.client_address[0]))
            forwarded = self.headers.get_all("X-Forwarded-For", [])
            if trusted and peer == trusted and forwarded:
                require(len(forwarded) == 1, "Invalid proxy client address.")
                try:
                    peer = str(ipaddress.ip_address(forwarded[0].strip()))
                except ValueError as exc:
                    raise MarketError("The trusted proxy must forward a single client address.") from exc
            return peer

        def _serve(self):
            result, status = {}, 200
            maximum_response = MAX_RESPONSE
            expired = False
            try:
                require(self.headers.get("Origin") is None and
                        not any(name.lower().startswith("sec-fetch-") for name in self.headers),
                        "Use the community page inside your DATA dashboard.", 403)
                require(self.command in ("GET", "POST"), "Method not supported.", 405)
                require(len(self.path) <= 4096 and self.path.startswith(("/community/", "/marketplace/")),
                        "Unknown community endpoint.", 404)
                parsed = urlsplit(self.path)
                require(not parsed.scheme and not parsed.netloc and not parsed.fragment,
                        "Unknown community endpoint.", 404)
                shared_marketplace = parsed.path.startswith("/marketplace/")
                prefix = "/marketplace/" if shared_marketplace else "/community/"
                parts = parsed.path[len(prefix):].split("/")
                validator = shared_service.validate_request if shared_marketplace else validate_request
                if shared_marketplace:
                    maximum_response = shared_service.MAX_RESPONSE
                try:
                    query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
                except ValueError as exc:
                    raise MarketError("Invalid community query.") from exc
                # Validate route before reading bodies or doing password work.
                validator(parts, self.command, {}, query)
                peer = self.peer()
                limits.check(peer, parts[:1] == ["account"] and parts[-1] in ("register", "login"))
                require(not self.headers.get_all("Transfer-Encoding"), "Chunked uploads are not supported.")
                lengths = self.headers.get_all("Content-Length", [])
                data = {}
                if self.command == "POST":
                    require(self.headers.get_content_type() == "application/json", "Use application/json.", 415)
                    require(len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdigit(),
                            "A single Content-Length is required.", 411)
                    maximum = (shared_service.body_limit(parts) if shared_marketplace else
                               MAX_AUTH_BODY if parts[0] == "account" else MAX_BODY)
                    length = int(lengths[0])
                    require(0 < length <= maximum, "Community request is too large.", 413)
                    body = self.rfile.read(length)
                    require(len(body) == length, "Incomplete community request.")
                    data = json_object(body)
                else:
                    require(not lengths or lengths == ["0"], "GET requests cannot include a body.")
                validator(parts, self.command, data, query)
                auth = self.headers.get_all("Authorization", [])
                require(len(auth) <= 1, "Invalid community session.", 401)
                token = ""
                if auth:
                    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43})", auth[0])
                    require(match, "Invalid community session.", 401)
                    token = match[1]
                account = store.authenticate(token)
                if token and parts not in (["account", "login"], ["account", "register"], ["account", "logout"]):
                    expired = not account
                    require(account, "Your community session has expired. Sign in again.", 401)
                if shared_marketplace:
                    result, issued = shared_service.route(store, parts, self.command, data, account, token, peer, query)
                    if issued is not None:
                        result[TOKEN_FIELD] = issued
                    result.update(mode="shared", identity_scope="shared")
                elif len(parts) == 2 and parts[0] == "account":
                    result, issued = store.account_action(parts[1], data, account, token, peer)
                    if issued is not None:
                        result[TOKEN_FIELD] = issued
                else:
                    result = forum.route(parts, self.command, data, account, query)
                if parts in (["meta"], ["session"]):
                    result["mode"] = "shared"
            except MarketError as exc:
                result, status = {"error": str(exc)}, exc.status
                if expired:
                    result["session_expired"] = True
            except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
                result, status = {"error": "Invalid community request."}, 400
            except (sqlite3.Error, OSError):
                result, status = {"error": "Community storage is unavailable. Please retry."}, 503
            self._respond(result, status, maximum_response)

        def _respond(self, result, status, maximum=MAX_RESPONSE):
            payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(payload) > maximum:
                status, payload = 503, b'{"error":"Community response is too large. Please contact the operator."}'
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                self.send_header("Connection", "close")
                if status == 429:
                    self.send_header("Retry-After", "300")
                self.close_connection = True
                self.end_headers()
                self.wfile.write(payload)
            except (OSError, TimeoutError):
                self.close_connection = True

        do_GET = _serve
        do_POST = _serve
        do_OPTIONS = _serve
        do_HEAD = _serve
        do_PUT = _serve
        do_DELETE = _serve
        do_PATCH = _serve

    return CommunityHandler


def create_server(address, store, trusted_proxy=None):
    return CommunityHTTPServer(address, make_handler(store, trusted_proxy))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. Use a TLS reverse proxy for public access.")
    parser.add_argument("--port", type=int, default=7810)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--trusted-proxy",
                        help="Exact proxy IP allowed to supply one X-Forwarded-For client IP.")
    parser.add_argument("--seed-library", action="store_true",
                        help="Import the bundled DATA Library catalogue and its existing review records.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.trusted_proxy:
        try:
            ipaddress.ip_address(args.trusted_proxy)
        except ValueError:
            parser.error("--trusted-proxy must be an IP address")
    store = Marketplace(args.data_dir, seed=args.seed_library)
    server = create_server((args.host, args.port), store, args.trusted_proxy)
    print(f"Community service listening on {args.host}:{args.port}.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
