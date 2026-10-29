"""Shared forum integration and transport isolation, using loopback only."""
import http.client
import json
import os
import secrets
import ssl
import sys
import tempfile
import threading
import unittest
from email.message import Message
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import community_server as hub
import community_transport as transport
import marketplace


PASSWORD = "a shared forum test password"


def local_handler():
    """A local bridge harness. Production origin/auth guards are tested elsewhere."""
    class LocalBridge(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def dispatch(self):
            parts = urlsplit(self.path).path.removeprefix("/marketplace/forum/").split("/")
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))) if self.command == "POST" else b"{}"
            proxied = transport.proxy_request(self, parts, json.loads(body))
            result, status, value = proxied if proxied is not None else ({"local_fallback": True}, 200, None)
            payload = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if value is not None:
                self.send_header("Set-Cookie", transport.cookie_header(self.server.server_address[1], value))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = dispatch
        do_POST = dispatch
    return LocalBridge


class Client:
    def __init__(self, server, prefix="/marketplace/forum/"):
        self.port = server.server_address[1]
        self.prefix = prefix
        self.cookie = ""

    def request(self, route, data=None, headers=None, method=None):
        outgoing = dict(headers or {})
        if self.cookie and "Cookie" not in outgoing:
            outgoing["Cookie"] = self.cookie
        body = None if data is None else json.dumps(data).encode()
        if body is not None:
            outgoing.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method or ("POST" if body is not None else "GET"), self.prefix + route,
                         body=body, headers=outgoing)
            response = conn.getresponse()
            status, received = response.status, dict(response.getheaders())
            raw = response.read()
            if "Set-Cookie" in received:
                self.cookie = received["Set-Cookie"].split(";", 1)[0]
            return status, json.loads(raw) if raw else {}, received
        finally:
            conn.close()


class CommunityTests(unittest.TestCase):
    def start(self, server):
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.addCleanup(cleanup)
        return server

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = marketplace.Marketplace(self.directory.name, seed=False)
        self.received = []
        parent = hub.make_handler(self.store)
        received = self.received

        class RecordingHub(parent):
            def record(self):
                received.append((self.command, self.path, dict(self.headers)))
                super()._serve()
            do_GET = record
            do_POST = record

        self.server = self.start(hub.CommunityHTTPServer(("127.0.0.1", 0), RecordingHub))
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.env = mock.patch.dict(os.environ, {"DATA_COMMUNITY_URL": self.origin})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.a = Client(self.start(ThreadingHTTPServer(("127.0.0.1", 0), local_handler())))
        self.b = Client(self.start(ThreadingHTTPServer(("127.0.0.1", 0), local_handler())))
        self.direct = Client(self.server, "/community/")

    def register(self, client, name="alice"):
        status, result, headers = client.request("account/register", {
            "username": name, "name": name.title(), "password": PASSWORD})
        self.assertEqual(status, 200, result)
        self.assertNotIn(transport.TOKEN_FIELD, result)
        return result["account"], headers

    def topic(self, client, **extra):
        status, result, _ = client.request("threads", {
            "category": "general", "title": "Share a useful dashboard workflow",
            "body": "A discussion shared across two installations.",
            "request_id": secrets.token_hex(16), **extra})
        self.assertEqual(status, 200, result)
        return result

    def test_two_independent_bridges_share_accounts_threads_and_replies(self):
        account, _ = self.register(self.a)
        created = self.topic(self.a)
        status, listing, _ = self.b.request("threads?category=general&view=all&sort=newest&page=1")
        self.assertEqual(status, 200, listing)
        self.assertEqual(listing["threads"][0]["id"], created["thread_id"])
        self.assertEqual(listing["threads"][0]["author"]["id"], account["id"])
        status, signed_in, _ = self.b.request("account/login", {"username": "alice", "password": PASSWORD})
        self.assertEqual(status, 200, signed_in)
        self.assertEqual(signed_in["account"]["id"], account["id"])
        status, reply, _ = self.b.request("threads/" + created["thread_id"] + "/reply", {
            "body": "Reply from the second installation.", "request_id": secrets.token_hex(16)})
        self.assertEqual(status, 200, reply)
        status, detail, _ = self.a.request("threads/" + created["thread_id"] + "?page=1")
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["total"], 2)
        self.assertEqual(detail["posts"][1]["body"], "Reply from the second installation.")
        self.assertEqual(detail["account"]["id"], account["id"])
        self.assertNotEqual(self.a.cookie, self.b.cookie)
        status, meta, _ = self.a.request("meta")
        self.assertEqual((status, meta["mode"], meta["community_origin"]), (200, "shared", self.origin))
        self.assertEqual(meta["threads"], 1)
        self.assertIn("off-topic", [category["id"] for category in meta["categories"]])

    def test_local_credentials_and_browser_headers_are_never_forwarded(self):
        local_token = "l" * 43
        bridge_secret = "do-not-forward-bridge-auth"
        status, result, _ = self.a.request("meta", headers={
            "Cookie": f"data_market_session_{self.a.port}={local_token}; other=private",
            "Authorization": "Bearer " + bridge_secret, "X-Bridge-Token": bridge_secret,
            "X-Marketplace-Request": "1", "Origin": f"http://localhost:{self.a.port}",
            "Sec-Fetch-Site": "same-origin", "X-Forwarded-For": "192.0.2.15"})
        self.assertEqual(status, 200, result)
        self.assertIsNone(result["account"])
        request_headers = self.received[-1][2]
        self.assertNotIn("Authorization", request_headers)
        self.assertNotIn("Cookie", request_headers)
        self.assertNotIn("Origin", request_headers)
        self.assertNotIn("Sec-Fetch-Site", request_headers)
        self.assertNotIn("X-Forwarded-For", request_headers)
        self.assertNotIn(local_token, json.dumps(self.received))
        self.assertNotIn(bridge_secret, json.dumps(self.received))
        self.assertNotIn("X-Marketplace-Request", request_headers)

    def test_session_cookie_is_scoped_http_only_and_origin_bound(self):
        account, headers = self.register(self.a)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/marketplace/forum;", cookie)
        self.assertIn(f"data_forum_session_{self.a.port}=", cookie)
        status, result, _ = self.a.request("session")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["account"]["id"], account["id"])
        auth = self.received[-1][2]["Authorization"]
        self.assertTrue(auth.startswith("Bearer "))
        self.assertNotIn(".", auth)
        self.assertEqual(len(auth.removeprefix("Bearer ")), 43)
        status, result, _ = self.b.request("session", headers={"Cookie": self.a.cookie})
        self.assertEqual(status, 200, result)
        self.assertIsNone(result["account"])
        with mock.patch.dict(os.environ, {"DATA_COMMUNITY_URL": self.origin.replace("127.0.0.1", "localhost")}):
            status, result, _ = self.a.request("session")
        self.assertEqual(status, 200, result)
        self.assertIsNone(result["account"])
        self.assertNotIn("Authorization", self.received[-1][2])

    def test_logout_revokes_remote_token_and_clears_only_forum_cookie(self):
        self.register(self.a)
        cookies = SimpleCookie()
        cookies.load(self.a.cookie)
        token = next(iter(cookies.values())).value.split(".", 1)[1]
        self.assertIsNotNone(self.store.authenticate(token))
        status, result, headers = self.a.request("account/logout", {})
        self.assertEqual(status, 200, result)
        self.assertIsNone(result["account"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertNotIn("data_market_session", headers["Set-Cookie"])
        self.assertIsNone(self.store.authenticate(token))

    def test_expired_cookie_can_be_replaced_by_login(self):
        account, _ = self.register(self.a)
        with self.store.connection() as db:
            db.execute("DELETE FROM sessions")
        status, result, _ = self.a.request("account/login", {"username": "alice", "password": PASSWORD})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["account"]["id"], account["id"])
        with self.store.connection() as db:
            db.execute("DELETE FROM sessions")
        status, result, headers = self.a.request("meta")
        self.assertEqual(status, 401, result)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        status, result, _ = self.a.request("meta")
        self.assertEqual(status, 200, result)
        self.assertIsNone(result["account"])

    def test_anonymous_mutation_and_other_accounts_edit_are_refused(self):
        status, result, _ = self.a.request("threads", {
            "title": "Anonymous should fail", "category": "off-topic",
            "body": "No account attached.", "request_id": secrets.token_hex(16)})
        self.assertEqual(status, 401, result)
        self.register(self.a)
        created = self.topic(self.a)
        self.register(self.b, "bruno")
        status, result, _ = self.b.request("posts/" + created["post_id"] + "/edit", {"body": "Unauthorized edit"})
        self.assertEqual(status, 403, result)
        status, result, _ = self.b.request("posts/" + created["post_id"] + "/like", {"liked": True})
        self.assertEqual(status, 200, result)
        status, detail, _ = self.a.request("threads/" + created["thread_id"])
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["posts"][0]["likes"], 1)
        self.assertNotEqual(detail["posts"][0]["body"], "Unauthorized edit")

    def test_extra_private_fields_and_unsupported_paths_never_reach_hub(self):
        before = len(self.received)
        cases = [
            ("threads", {"account": {"id": "private"}}),
            ("account/login", {"username": "alice", "password": PASSWORD, "bridge_token": "secret"}),
            ("account/register", {"username": "alice", "name": "Alice", "password": PASSWORD, "avatar": "data:image/png;base64,AAAA"}),
            ("listings", {}),
            ("threads?bridge_token=secret", None),
            ("threads?category=general&category=off-topic", None),
            ("threads/../../install", {}),
        ]
        for route, data in cases:
            with self.subTest(route=route):
                status, result, _ = self.a.request(route, data)
                self.assertIn(status, (400, 404))
                self.assertNotIn("local_fallback", result)
        self.assertEqual(before, len(self.received))

    def test_public_service_has_no_bridge_routes_or_browser_cors(self):
        for prefix, route in (("/community/", "listings"), ("/", "health"),
                              ("/marketplace/", "runtime"), ("/", "execute")):
            direct = Client(self.server, prefix)
            status, result, _ = direct.request(route)
            self.assertEqual(status, 404, result)
        status, result, _ = Client(self.server, "/marketplace/").request("catalog")
        self.assertEqual((status, result["mode"]), (200, "shared"))
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Origin": ""}, {"Sec-Fetch-Site": "same-origin"}):
            status, result, received = self.direct.request("meta", headers=headers)
            self.assertEqual(status, 403, result)
            self.assertFalse(any(name.lower().startswith("access-control-") for name in received))
        status, result, received = self.direct.request("meta", method="OPTIONS")
        self.assertEqual(status, 405, result)
        self.assertNotIn("Access-Control-Allow-Origin", received)
        self.assertNotIn("Set-Cookie", received)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM listings").fetchone()[0], 0)

    def test_oversized_requests_and_auth_attempts_are_bounded(self):
        status, result, _ = self.direct.request("account/register", {"name": "a" * transport.MAX_AUTH_BODY})
        self.assertEqual(status, 413, result)
        for _ in range(12):
            status, result, _ = self.direct.request("account/login", {"username": "x", "password": "short"})
            self.assertEqual(status, 400, result)
        status, result, headers = self.direct.request("account/login", {"username": "x", "password": "short"})
        self.assertEqual(status, 429, result)
        self.assertEqual(headers["Retry-After"], "300")

    def test_bad_target_configuration_fails_without_local_fallback(self):
        invalid = ("http://public.example", "http://127.0.0.1.evil.example", "file:///tmp/x",
                   "https://user:pass@example.com", "https://example.com/community",
                   "https://example.com/?token=private", "https://example.com#fragment",
                   "https://example.com\\evil", "https://example.com:99999",
                   "https://example.com/%2e%2e", "https://example.com\nbad")
        before = len(self.received)
        for target in invalid:
            with self.subTest(target=target), mock.patch.dict(os.environ, {"DATA_COMMUNITY_URL": target}):
                status, result, _ = self.a.request("meta")
                self.assertEqual(status, 503, result)
                self.assertNotIn("local_fallback", result)
        self.assertEqual(before, len(self.received))
        with mock.patch.dict(os.environ, {"DATA_COMMUNITY_URL": ""}):
            status, result, _ = self.a.request("meta")
            self.assertEqual((status, result), (200, {"local_fallback": True}))

    def test_remote_json_errors_and_unavailability_do_not_fall_back(self):
        status, result, _ = self.a.request("threads/" + "0" * 32)
        self.assertEqual(status, 404, result)
        self.assertNotIn("local_fallback", result)
        unused = ThreadingHTTPServer(("127.0.0.1", 0), local_handler())
        unused_origin = f"http://127.0.0.1:{unused.server_address[1]}"
        unused.server_close()
        with mock.patch.dict(os.environ, {"DATA_COMMUNITY_URL": unused_origin}):
            status, result, _ = self.a.request("meta")
        self.assertEqual(status, 502, result)
        self.assertNotIn("local_fallback", result)

    def test_redirects_are_never_followed_and_responses_are_bounded(self):
        replies = [302, 200, 200, 200]
        calls = []

        class BadHub(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                mode = len(calls)
                calls.append(self.path)
                self.send_response(replies[mode])
                self.send_header("Location", self.server.destination + "/community/meta")
                self.send_header("Content-Type", "text/html" if mode == 1 else "application/json")
                length = transport.MAX_RESPONSE + 1 if mode == 2 else 2
                self.send_header("Content-Length", str(length))
                self.end_headers()
                self.wfile.write(b"{}" if mode != 3 else b"[]")

        bad = self.start(ThreadingHTTPServer(("127.0.0.1", 0), BadHub))
        bad.destination = self.origin
        before = len(self.received)
        with mock.patch.dict(os.environ, {"DATA_COMMUNITY_URL": f"http://127.0.0.1:{bad.server_address[1]}"}):
            for _ in range(4):
                status, result, _ = self.a.request("meta")
                self.assertEqual(status, 502, result)
                self.assertNotIn("local_fallback", result)
        self.assertEqual(before, len(self.received))
        self.assertEqual(len(calls), 4)

    def test_total_timeout_bounds_a_stalled_network_worker(self):
        release = threading.Event()
        def blocked(*_args):
            release.wait(1)
            return {}, 200
        try:
            with mock.patch.object(transport, "_send", side_effect=blocked), \
                    mock.patch.object(transport, "REQUEST_TIMEOUT", 0.02):
                status, result, _ = self.a.request("meta")
            self.assertEqual(status, 504, result)
            self.assertNotIn("local_fallback", result)
        finally:
            release.set()

    def test_auth_token_never_appears_in_json_responses(self):
        self.register(self.a)
        status, result, _ = self.a.request("session")
        self.assertEqual(status, 200, result)
        remote_token = self.received[-1][2]["Authorization"].removeprefix("Bearer ")
        self.assertNotIn(remote_token, json.dumps(result))
        self.assertNotIn(transport.TOKEN_FIELD, result)
        self.assertIsNotNone(self.store.authenticate(remote_token))
        for payload in ({transport.TOKEN_FIELD: remote_token}, {"account": {}, transport.TOKEN_FIELD: "bad"}):
            with mock.patch.object(transport, "_request", return_value=(dict(payload), 200)):
                status, result, headers = self.a.request("meta")
            self.assertEqual(status, 502, result)
            self.assertNotIn(remote_token, json.dumps(result))
            self.assertNotIn("Set-Cookie", headers)

    def test_trusted_proxy_address_only_used_when_explicitly_configured(self):
        target = self.start(hub.create_server(("127.0.0.1", 0), self.store, trusted_proxy="127.0.0.1"))
        client = Client(target, "/community/")
        status, result, _ = client.request("meta", headers={"X-Forwarded-For": "192.0.2.5, 192.0.2.6"})
        self.assertEqual(status, 400, result)
        status, result, _ = client.request("meta", headers={"X-Forwarded-For": "192.0.2.5"})
        self.assertEqual(status, 200, result)
        status, result, _ = self.direct.request("meta", headers={"X-Forwarded-For": "untrusted and invalid"})
        self.assertEqual(status, 200, result)


class TransportUnitTests(unittest.TestCase):
    def test_https_uses_default_verified_tls_and_only_selected_headers(self):
        response = mock.Mock()
        response.status = 200
        response.headers = Message()
        response.headers["Content-Length"] = "2"
        response.getheader.side_effect = lambda key, default=None: {
            "Content-Type": "application/json", "Content-Length": "2"}.get(key, default)
        response.read.return_value = b"{}"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        state = {"cancelled": threading.Event()}
        with mock.patch.object(http.client, "HTTPSConnection", return_value=connection) as constructor:
            result, status = transport._send("https://community.example", "/community/meta",
                                             "GET", b"{}", "", state)
        context = constructor.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual((result, status), ({}, 200))
        self.assertEqual(set(connection.request.call_args.kwargs["headers"]),
                         {"Accept", "Accept-Encoding", "Connection"})
        connection.close.assert_called_once()

    def test_origin_normalization_and_cookie_validation(self):
        for target, normalized in (("https://Example.com:443/", "https://example.com"),
                                   ("http://127.0.0.1:80", "http://127.0.0.1"),
                                   ("http://[::1]:7810/", "http://[::1]:7810")):
            with mock.patch.dict(os.environ, {"DATA_COMMUNITY_URL": target}):
                self.assertEqual(transport.configured_origin(), normalized)
        for value in ("unsafe\r\nSet-Cookie: injected=1", "plain-token", "." + "a" * 43):
            with self.assertRaises(marketplace.MarketError):
                transport.cookie_header(7777, value)


if __name__ == "__main__":
    unittest.main()
