"""Shared marketplace/forum with two real device proxies, no external network."""
import base64
import http.client
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import community_server
import community_transport
import marketplace as mp
import shared_transport
from device_extensions import get_library
from marketplace_preview import make_handler
import shared_service_admin
from test_marketplace import PNG, package
from test_community_transport import Client


PASSWORD = "shared-services-test-password"


class DeviceClient(Client):
    def __init__(self, server):
        super().__init__(server, "/marketplace/")

    def request(self, route, data=None, headers=None, method=None):
        return super().request(route, data, {"X-Marketplace-Request": "1", **(headers or {})}, method)


class SharedServicesTests(unittest.TestCase):
    def start(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.addCleanup(stop)
        return server

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = mp.Marketplace(self.root / "hub", seed=False)
        self.devices = [mp.Marketplace(self.root / name, seed=False) for name in ("a", "b")]
        self.received = []
        records = self.received
        parent = community_server.make_handler(self.store)
        class RecordingService(parent):
            def dispatch(self):
                records.append((self.path, dict(self.headers)))
                # Simulate a reverse proxy mounting the API at a site path.
                if self.path.startswith("/data-services/"):
                    self.path = self.path[len("/data-services"):]
                super()._serve()
            do_GET = dispatch
            do_POST = dispatch
        self.hub = self.start(community_server.CommunityHTTPServer(("127.0.0.1", 0), RecordingService))
        self.url = f"http://127.0.0.1:{self.hub.server_port}/data-services"
        env = mock.patch.dict(os.environ, {"DATA_SERVICES_URL": self.url, "DATA_COMMUNITY_URL": ""})
        env.start()
        self.addCleanup(env.stop)
        self.servers = [self.start(ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))) for store in self.devices]
        self.a, self.b = [DeviceClient(server) for server in self.servers]

    def ok(self, client, route, data=None):
        status, result, _ = client.request(route, data)
        self.assertEqual(status, 200, result)
        self.assertNotIn("_session_token", result)
        return result

    def register(self, client, name="creator", **extra):
        return self.ok(client, "account/register", {"username": name, "name": name.title(), "password": PASSWORD, **extra})["account"]

    def listing(self, client=None, version="1.0.0", **extra):
        return self.ok(client or self.a, "listings", {
            "title": "Shared workflow", "description": "An original shared extension for testing.",
            "package": base64.b64encode(package(version=version)).decode(),
            "price_cents": 0, "rights_confirmed": True, **extra})

    def install(self, client, listing):
        return self.ok(client, f"listings/{listing['id']}/install", {
            "version": listing["latest"], "sha256": listing["sha256"],
            "permissions": listing["manifest"]["permissions"], "acknowledge": True})

    def test_one_account_for_settings_marketplace_and_forum_across_devices(self):
        account = self.register(self.a, email="private@example.com", avatar=PNG)
        self.assertEqual(self.ok(self.a, "account/me", {})["account"]["id"], account["id"])
        self.assertEqual(self.ok(self.a, "forum/session")["account"]["id"], account["id"])
        self.assertEqual(self.ok(self.a, "forum/meta")["identity_scope"], "shared")
        topic = self.ok(self.a, "forum/threads", {"category": "general", "title": "A shared account",
            "body": "Created after signing in from the marketplace.", "request_id": secrets.token_hex(16)})
        self.assertEqual(self.ok(self.b, "forum/threads")["threads"][0]["id"], topic["thread_id"])
        other = self.ok(self.b, "forum/account/login", {"username": "creator", "password": PASSWORD})["account"]
        self.assertEqual(account["id"], other["id"])
        self.assertEqual(self.ok(self.b, "session")["account"]["id"], account["id"])
        self.assertEqual(self.ok(self.b, "account/me", {})["account"]["email"], "private@example.com")
        self.assertTrue(all(path.startswith("/data-services/") for path, _ in self.received))

    def test_shared_catalog_upload_update_download_review_and_local_installs(self):
        self.register(self.a)
        listing = self.listing(image=PNG)
        self.assertEqual(self.ok(self.b, "catalog")["listings"][0]["id"], listing["id"])
        buyer = self.register(self.b, "reader")
        self.install(self.b, listing)
        self.assertEqual(len(self.ok(self.b, "runtime")["extensions"]), 1)
        self.assertEqual(self.ok(self.a, "runtime")["extensions"], [])
        self.ok(self.b, f"listings/{listing['id']}/review", {"rating": 5, "body": "Useful original extension."})
        self.assertEqual(self.ok(self.a, f"listings/{listing['id']}")["reviews"][0]["account_id"], buyer["id"])
        updated = self.listing(version="1.1.0", listing_id=listing["id"])
        self.assertTrue(self.ok(self.b, "catalog")["listings"][0]["update_available"])
        self.install(self.b, updated)
        self.assertEqual(self.ok(self.b, "runtime")["extensions"][0]["version"], "1.1.0")
        self.ok(self.b, f"listings/{listing['id']}/rollback", {
            "version": "1.0.0", "sha256": listing["sha256"], "acknowledge": True})
        self.assertEqual(self.ok(self.b, "runtime")["extensions"][0]["version"], "1.0.0")
        self.ok(self.b, f"listings/{listing['id']}/disable", {})
        self.assertEqual(self.ok(self.b, "runtime")["extensions"], [])
        self.ok(self.b, f"listings/{listing['id']}/enable", {})
        self.ok(self.b, f"listings/{listing['id']}/uninstall", {})
        self.assertIsNone(self.ok(self.b, f"listings/{listing['id']}")["installation"])
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM installations").fetchone()[0], 0)

    def test_same_online_account_does_not_sync_installs_or_local_accounts(self):
        local, local_token = self.devices[0].account_action("register", {
            "username": "creator", "name": "Local identity", "password": PASSWORD,
            "email": "local-only@example.com"}, None, "", "fixture")
        self.a.cookie = f"data_market_session_{self.a.port}={local_token}"
        self.assertIsNone(self.ok(self.a, "session")["account"])
        remote = self.register(self.a)
        self.assertNotEqual(local["account"]["id"], remote["id"])
        listing = self.listing()
        self.install(self.a, listing)
        self.ok(self.b, "account/login", {"username": "creator", "password": PASSWORD})
        self.assertIsNone(self.ok(self.b, f"listings/{listing['id']}")["installation"])
        self.assertEqual(self.ok(self.b, "runtime")["extensions"], [])
        self.assertFalse(any(v["was_installed"] for v in self.ok(self.b, f"listings/{listing['id']}")["versions"]))
        self.ok(self.a, "account/logout", {})
        self.assertEqual(self.ok(self.a, "runtime")["extensions"], [])
        self.register(self.a, "different")
        self.assertEqual(self.ok(self.a, "runtime")["extensions"], [])
        with self.devices[0].connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM accounts").fetchone()[0], 1)
        self.assertNotIn(local_token, json.dumps(self.received))
        self.assertNotIn("local-only@example.com", json.dumps(self.received))

    def test_settings_password_change_revokes_other_devices_and_wrong_password_keeps_session(self):
        account = self.register(self.a)
        self.ok(self.b, "account/login", {"username": "creator", "password": PASSWORD})
        status, _, headers = self.a.request("account/credentials", {"current_password": "wrong", "email": "me@example.com"})
        self.assertEqual(status, 401)
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.ok(self.a, "forum/session")["account"]["id"], account["id"])
        self.ok(self.a, "account/credentials", {"current_password": PASSWORD, "password": "new shared test password"})
        status, _, headers = self.b.request("forum/session")
        self.assertEqual(status, 401)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertIsNone(self.ok(self.b, "session")["account"])
        self.assertEqual(self.ok(self.a, "session")["account"]["id"], account["id"])

    def test_private_account_fields_are_only_returned_to_owner(self):
        account = self.register(self.a, email="me@example.com")
        wallet = {"chain": "ethereum", "address": "0x" + "a" * 40, "label": "Personal"}
        self.ok(self.a, "account/wallets", {"current_password": PASSWORD, "wallets": [wallet]})
        self.assertEqual(self.ok(self.a, "account/me", {})["account"]["wallets"], [wallet])
        listing = self.listing()
        for route in ("session", "catalog", "forum/meta", f"listings/{listing['id']}"):
            result = self.ok(self.a, route)
            self.assertNotIn("me@example.com", json.dumps(result))
            self.assertNotIn(wallet["address"], json.dumps(result))
        self.register(self.b, "another")
        self.assertNotEqual(self.ok(self.b, "account/me", {})["account"]["id"], account["id"])
        status, _, _ = self.b.request("account/me", {"account_id": account["id"]})
        self.assertEqual(status, 400)

    def test_shared_cookie_bound_to_port_service_and_path(self):
        _, _, headers = self.a.request("account/register", {"username": "creator", "name": "Creator", "password": PASSWORD})
        cookie = headers["Set-Cookie"]
        for marker in ("HttpOnly", "SameSite=Strict", "Path=/marketplace;", f"data_services_session_{self.a.port}="):
            self.assertIn(marker, cookie)
        self.assertNotIn("data_market_session", cookie)
        self.assertIsNone(self.b.request("session", headers={"Cookie": self.a.cookie})[1]["account"])
        with mock.patch.dict(os.environ, {"DATA_SERVICES_URL": self.url.removesuffix("/data-services")}):
            self.assertIsNone(self.ok(self.a, "session")["account"])

    def test_package_review_revocation_blocks_installed_runtime_and_rollback(self):
        self.register(self.a)
        listing = self.listing()
        self.install(self.a, listing)
        self.store.review_package(listing["id"], listing["latest"], listing["sha256"],
                                  "Operator", "Rejected after inspection.", "high", "rejected")
        self.assertEqual(self.ok(self.a, "runtime")["extensions"], [])
        self.assertEqual(self.ok(self.a, f"listings/{listing['id']}")["installation"]["enabled"], 0)
        status, _, _ = self.a.request(f"listings/{listing['id']}/enable", {})
        self.assertEqual(status, 403)
        status, _, _ = self.a.request(f"listings/{listing['id']}/rollback", {
            "version": listing["latest"], "sha256": listing["sha256"], "acknowledge": True})
        self.assertEqual(status, 403)

    def test_stale_hash_permissions_paid_and_ownership_are_refused(self):
        self.register(self.a)
        listing = self.listing()
        self.register(self.b, "reader")
        values = {"version": listing["latest"], "sha256": listing["sha256"],
                  "permissions": listing["manifest"]["permissions"], "acknowledge": True}
        for changes in ({"sha256": "0" * 64}, {"permissions": []}, {"acknowledge": False}):
            status, _, _ = self.b.request(f"listings/{listing['id']}/install", {**values, **changes})
            self.assertIn(status, (400, 409))
        status, _, _ = self.b.request("listings", {"listing_id": listing["id"], "rights_confirmed": True,
            "package": base64.b64encode(package(version="1.1.0")).decode()})
        self.assertEqual(status, 403)
        paid = self.listing(price_cents=500)
        status, _, _ = self.b.request(f"listings/{paid['id']}/install", {**values, "sha256": paid["sha256"]})
        self.assertEqual(status, 402)
        self.assertEqual(self.ok(self.b, f"listings/{paid['id']}")["files"], {})
        self.assertEqual(self.ok(self.b, "runtime")["extensions"], [])

    def test_device_rechecks_download_bytes_and_cached_runtime(self):
        account = self.register(self.a)
        listing = self.listing()
        data = {"version": listing["latest"], "sha256": listing["sha256"],
                "permissions": listing["manifest"]["permissions"], "acknowledge": True}
        library = get_library(self.devices[0])
        with self.assertRaises(mp.MarketError):
            library.install(self.url, account, {"account": account, "listing": listing,
                "package": base64.b64encode(package(version="1.1.0")).decode()}, data)
        self.assertEqual(library.records(self.url, account), [])
        self.install(self.a, listing)
        with library.connection() as db:
            db.execute("UPDATE packages SET archive=?", (b"corrupt",))
        self.assertEqual(self.ok(self.a, "runtime")["extensions"], [])

    def test_media_proxy_checks_raster_and_does_not_forward_private_headers(self):
        account = self.register(self.a, avatar=PNG)
        conn = http.client.HTTPConnection("127.0.0.1", self.a.port, timeout=5)
        try:
            conn.request("GET", account["avatar"])
            response = conn.getresponse()
            self.assertEqual((response.status, response.getheader("Content-Type")), (200, "image/png"))
            self.assertTrue(response.read().startswith(b"\x89PNG"))
        finally:
            conn.close()
        self.a.request("catalog", headers={"Authorization": "Bearer private-bridge-token", "X-Data-Token": "private-bridge-token"})
        serialized = json.dumps(self.received)
        self.assertNotIn("private-bridge-token", serialized)
        self.assertTrue(all("Cookie" not in h and "X-Marketplace-Request" not in h for _, h in self.received))

    def test_unknown_fields_and_device_runtime_routes_do_not_cross_public_boundary(self):
        before = len(self.received)
        for route, data in (("account/register", {"history": "private"}), ("listings", {"shell": "anything"}),
                            ("catalog?token=private", None), ("execute", {}), ("forum/threads", {"files": "private"})):
            status, _, _ = self.a.request(route, data)
            self.assertIn(status, (400, 404))
        self.assertEqual(len(self.received), before)
        direct = Client(self.hub, "/marketplace/")
        for route, data in (("runtime", None), ("listings/" + "a" * 32 + "/install", {}), ("account/reset", {})):
            self.assertEqual(direct.request(route, data)[0], 404)
        self.assertEqual(direct.request("catalog", headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(direct.request("catalog", method="OPTIONS")[0], 405)

    def test_invalid_or_unavailable_service_never_uses_local_fallback(self):
        for url in ("http://public.example", "https://user:pass@example.com", "https://example.com/../private", "https://example.com/?token=x"):
            with mock.patch.dict(os.environ, {"DATA_SERVICES_URL": url}):
                status, result, _ = self.a.request("catalog")
                self.assertEqual(status, 503, result)
                self.assertNotIn("listings", result)
        with mock.patch.object(community_transport, "_request", side_effect=mp.MarketError("Unavailable", 502)):
            for route, data in (("catalog", None), ("account/register", {"username": "creator", "name": "Creator", "password": PASSWORD}), ("forum/meta", None)):
                self.assertEqual(self.a.request(route, data)[0], 502)
        for device in self.devices:
            with device.connection() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM accounts").fetchone()[0], 0)

    def test_catalog_pagination_collects_shared_listings(self):
        self.register(self.a)
        for number in range(27):
            # Direct fixtures avoid intentionally enforced public upload limits.
            account = self.ok(self.a, "session")["account"]
            self.store.submit({"title": f"Extension {number}", "rights_confirmed": True,
                "package": base64.b64encode(package()).decode()}, account)
        result = self.ok(self.b, "catalog")
        self.assertEqual(len(result["listings"]), 27)
        self.assertIsNone(result["next_page"])
        self.assertTrue(any("catalog?page=2" in path for path, _ in self.received))

    def test_consistent_backup_restores_accounts_and_forum_without_overwriting(self):
        account = self.register(self.a)
        self.ok(self.a, "forum/threads", {"category": "general", "title": "A durable discussion",
            "body": "This discussion must survive a database backup.", "request_id": secrets.token_hex(16)})
        target = self.root / "backup.sqlite3"
        backup = shared_service_admin.backup(self.store.db, target)
        self.assertEqual(backup["integrity"], "ok")
        restored = shared_service_admin.status(target)
        self.assertEqual((restored["accounts"], restored["forum_threads"], restored["forum_posts"]), (1, 1, 1))
        self.assertNotIn(account["id"], json.dumps(restored))
        original = target.read_bytes()
        with self.assertRaises(ValueError):
            shared_service_admin.backup(self.store.db, target)
        self.assertEqual(target.read_bytes(), original)
        with self.assertRaises(ValueError):
            shared_service_admin.backup(self.root / "missing.sqlite3", self.root / "bad.sqlite3")
        self.assertFalse((self.root / "bad.sqlite3").exists())

    def test_incomplete_backup_is_not_left_as_a_valid_snapshot(self):
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a database")
        target = self.root / "incomplete.sqlite3"
        with self.assertRaises(sqlite3.DatabaseError):
            shared_service_admin.backup(corrupt, target)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
