"""Marketplace trust boundaries and account/package lifecycle, without providers."""
import base64
import http.client
import io
import json
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketplace as mp
from marketplace_preview import make_handler


def package(kind="page", version="1.0.0", transform=None):
    files = mp.parse_package(mp.starter_package(kind))["files"]
    manifest = json.loads(files["manifest.json"])
    manifest["version"] = version
    files["manifest.json"] = json.dumps(manifest)
    if transform:
        transform(files)
    return mp.make_archive(files)


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


class PackageTests(unittest.TestCase):
    def test_all_starters_and_bundled_packages_validate(self):
        for kind in sorted(mp.KINDS):
            with self.subTest(kind=kind):
                self.assertEqual(mp.parse_package(mp.starter_package(kind))["manifest"]["kind"], kind)
        for folder in (mp.ROOT / "marketplace_catalog").iterdir():
            if folder.is_dir():
                files = {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file()}
                parsed = mp.parse_package(mp.make_archive(files))
                self.assertEqual(parsed["sha256"], mp.parse_package(mp.make_archive(files))["sha256"])

    def test_archive_path_and_code_attacks(self):
        for name in ("../stolen.md", "/root.md", "C:/root.md", "nested\\root.md", "CON.txt", "aux/readme.md", "nested/../x.md", ".hidden.md", "code.js", "code.py"):
            with self.subTest(name=name), self.assertRaises(mp.MarketError):
                mp.parse_package(mp.make_archive({"manifest.json": "{}", name: "unsafe"}))

    def test_duplicate_names_symlinks_and_zip_bombs(self):
        for mode in ("duplicate", "link", "bomb"):
            with self.subTest(mode=mode):
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                    if mode == "duplicate":
                        archive.writestr("manifest.json", "{}")
                        archive.writestr("MANIFEST.json", "{}")
                    elif mode == "link":
                        info = zipfile.ZipInfo("manifest.json")
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        archive.writestr(info, "/secret")
                    else:
                        archive.writestr("manifest.json", " " * (mp.MAX_FILE + 1))
                with self.assertRaises(mp.MarketError):
                    mp.parse_package(output.getvalue())

    def test_no_script_css_html_or_permission_smuggling(self):
        attacks = [
            ("theme", "theme.json", {"tokens": {"--data-bg": "url(https://invalid.test)"}}),
            ("theme", "theme.json", {"tokens": {"position": "#000000"}}),
            ("page", "page.json", {"blocks": [{"type": "html", "text": "<script>bad()</script>"}]}),
            ("tool", "tool.json", {"operations": ["exec"], "placeholder": "run"}),
        ]
        for kind, entry, content in attacks:
            with self.subTest(kind=kind, content=content), self.assertRaises(mp.MarketError):
                mp.parse_package(package(kind, transform=lambda files: files.update({entry: json.dumps(content)})))
        def bad_permissions(files):
            manifest = json.loads(files["manifest.json"])
            manifest["permissions"] = []
            files["manifest.json"] = json.dumps(manifest)
        with self.assertRaises(mp.MarketError):
            mp.parse_package(package(transform=bad_permissions))

    def test_json_duplicates_and_bad_images(self):
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '[]'):
            with self.assertRaises(mp.MarketError):
                mp.json_object(value)
        for image in ("https://example.test/pic.jpg", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,AAAA"):
            with self.assertRaises(mp.MarketError):
                mp.image_data(image)
        self.assertEqual(mp.image_data(PNG), PNG)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = mp.Marketplace(self.temp.name, seed=False)
        self.owner, self.owner_token = self.register("creator")
        self.reader, self.reader_token = self.register("reader")

    def register(self, username):
        result, token = self.store.account_action("register", {"username": username, "name": username.title(), "password": "a long local test password", "avatar": PNG}, None, "", username)
        return result["account"], token

    def submit(self, kind="page", version="1.0.0", **extra):
        return self.store.submit({"package": base64.b64encode(package(kind, version)).decode(), "rights_confirmed": True, **extra}, self.owner)

    def install(self, item, account=None):
        return self.store.install(item["id"], {"sha256": item["sha256"], "version": item["latest"], "permissions": item["manifest"]["permissions"], "acknowledge": True}, account or self.reader)

    def review(self, item, verdict="approved", risk="low"):
        return self.store.review_package(item["id"], item["latest"], item["sha256"], "Test reviewer", "Source inspected for the test.", risk, verdict)

    def test_passwords_and_sessions_are_hashed_and_revoked(self):
        self.assertEqual(self.store.authenticate(self.reader_token)["id"], self.reader["id"])
        with self.store.connection() as db:
            row = db.execute("SELECT password,salt FROM accounts WHERE id=?", (self.reader["id"],)).fetchone()
            self.assertEqual(len(row["password"]), 64)
            self.assertEqual(len(row["salt"]), 32)
            self.assertNotIn(self.reader_token, str([tuple(r) for r in db.execute("SELECT * FROM sessions")]))
        self.assertNotIn("password", self.reader)
        self.store.account_action("logout", {}, self.reader, self.reader_token, "reader")
        self.assertIsNone(self.store.authenticate(self.reader_token))
        with self.assertRaises(mp.MarketError):
            self.store.account_action("login", {"username": "reader", "password": "incorrect password"}, None, "", "reader")

    def test_profile_and_media_are_bounded_and_do_not_repeat_inline(self):
        item = self.submit(image=PNG)
        catalog = self.store.catalog(self.reader)
        self.assertNotIn("base64,", json.dumps(catalog))
        self.assertNotIn("base64,", json.dumps(self.reader))
        self.assertTrue(item["image"].startswith("/marketplace/media/listing/"))
        self.assertEqual(self.store.media("listing", item["id"])[1], "image/png")
        result, _ = self.store.account_action("profile", {"name": "New Name"}, self.reader, self.reader_token, "reader")
        self.assertTrue(result["account"]["avatar"])
        self.assertEqual(self.store.media("account", self.reader["id"])[0][:8], b"\x89PNG\r\n\x1a\n")
        updated = self.submit(version="1.1.0", listing_id=item["id"])
        self.assertTrue(updated["image"])

    def test_install_is_explicit_pinned_and_account_scoped(self):
        item = self.submit()
        for changed in ({"acknowledge": False}, {"sha256": "0" * 64}, {"version": "9.0.0"}, {"permissions": []}):
            data = {"sha256": item["sha256"], "version": item["latest"], "permissions": item["manifest"]["permissions"], "acknowledge": True, **changed}
            with self.assertRaises(mp.MarketError):
                self.store.install(item["id"], data, self.reader)
        self.install(item)
        self.assertEqual(len(self.store.runtime(self.reader)), 1)
        self.assertEqual(self.store.runtime(self.owner), [])
        self.assertEqual(self.store.runtime(), [])
        self.assertIsNone(self.store.detail(item["id"], self.owner)["installation"])
        with self.assertRaises(mp.MarketError):
            self.store.manage(item["id"], "uninstall", {}, self.owner)
        self.install(item, self.owner)
        self.store.manage(item["id"], "disable", {}, self.reader)
        self.assertEqual(self.store.runtime(self.reader), [])
        self.assertEqual(len(self.store.runtime(self.owner)), 1)
        self.store.manage(item["id"], "enable", {}, self.reader)
        self.store.manage(item["id"], "uninstall", {}, self.reader)
        self.assertEqual(self.store.runtime(self.reader), [])
        self.assertEqual(len(self.store.runtime(self.owner)), 1)

    def test_paid_content_is_not_delivered_or_installed(self):
        item = self.submit(price_cents=2500)
        self.assertEqual(self.store.detail(item["id"], self.reader)["files"], {})
        self.assertTrue(self.store.detail(item["id"], self.owner)["files"])
        with self.assertRaises(mp.MarketError) as caught:
            self.install(item)
        self.assertEqual(caught.exception.status, 402)

    def test_reviews_require_install_and_cannot_be_self_written(self):
        item = self.submit()
        with self.assertRaises(mp.MarketError):
            self.store.leave_review(item["id"], {"rating": 5, "body": "Good"}, self.reader)
        self.install(item)
        self.store.leave_review(item["id"], {"rating": 5, "body": "Useful page"}, self.reader)
        self.store.leave_review(item["id"], {"rating": 4, "body": "Updated opinion"}, self.reader)
        detail = self.store.detail(item["id"], self.reader)
        self.assertEqual((detail["review_count"], detail["rating"]), (1, 4))
        with self.assertRaises(mp.MarketError):
            self.store.leave_review(item["id"], {"rating": 5, "body": "My product"}, self.owner)

    def test_version_update_does_not_inherit_review_or_auto_update(self):
        item = self.submit()
        self.review(item)
        self.install(item)
        new = self.submit(version="1.1.0", listing_id=item["id"])
        self.assertEqual(new["review_status"], "unverified")
        self.assertEqual(self.store.runtime(self.reader)[0]["version"], "1.0.0")
        self.assertTrue(self.store.detail(item["id"], self.reader)["update_available"])
        with self.assertRaises(mp.MarketError):
            self.install(item)
        with self.assertRaises(mp.MarketError):
            self.submit(version="1.1.0", listing_id=item["id"])
        with self.assertRaises(mp.MarketError):
            self.store.submit({"package": base64.b64encode(package(version="1.2.0")).decode(), "listing_id": item["id"], "rights_confirmed": True}, self.reader)

    def test_rollback_requires_prior_install_and_records_target(self):
        old = self.submit()
        self.install(old)
        new = self.submit(version="1.1.0", listing_id=old["id"])
        self.install(new)
        data = {"version": "1.0.0", "sha256": old["sha256"], "acknowledge": True}
        with self.assertRaises(mp.MarketError):
            self.store.manage(old["id"], "rollback", {**data, "sha256": "0" * 64}, self.reader)
        self.store.manage(old["id"], "rollback", data, self.reader)
        self.assertEqual(self.store.runtime(self.reader)[0]["version"], "1.0.0")
        self.store.leave_review(old["id"], {"rating": 4, "body": "Restored version"}, self.reader)
        detail = self.store.detail(old["id"], self.reader)
        self.assertEqual(detail["reviews"][0]["version"], "1.0.0")
        self.assertTrue(all(v["was_installed"] for v in detail["versions"]))
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT version FROM audit WHERE event='rollback'").fetchone()[0], "1.0.0")

    def test_rejected_bytes_cannot_install_enable_rollback_or_run(self):
        old = self.submit()
        self.install(old)
        new = self.submit(version="1.1.0", listing_id=old["id"])
        self.install(new)
        self.review(old, "rejected", "high")
        with self.assertRaises(mp.MarketError):
            self.store.manage(old["id"], "rollback", {"version": old["latest"], "sha256": old["sha256"], "acknowledge": True}, self.reader)
        self.review(new, "rejected", "high")
        self.assertEqual(self.store.runtime(self.reader), [])
        for action in (lambda: self.install(new), lambda: self.store.manage(new["id"], "enable", {}, self.reader)):
            with self.assertRaises(mp.MarketError):
                action()
        self.assertEqual(self.store.detail(new["id"], self.reader)["review_status"], "blocked")
        with mock.patch.object(mp, "POLICY_VERSION", "future-policy"):
            self.assertEqual(self.store.detail(new["id"], self.reader)["review_status"], "blocked")

    def test_reviews_require_exact_hash_and_positive_badges_expire_with_policy(self):
        item = self.submit()
        with self.assertRaises(mp.MarketError):
            self.store.review_package(item["id"], item["latest"], "0" * 64, "Reviewer", "Inspected.")
        self.review(item)
        self.assertEqual(self.store.detail(item["id"], self.reader)["review_status"], "reviewed")
        self.assertEqual(self.store.review_queue(), [])
        with mock.patch.object(mp, "POLICY_VERSION", "future-policy"):
            self.assertEqual(self.store.detail(item["id"], self.reader)["review_status"], "unverified")
            self.assertEqual(self.store.review_queue()[0]["listing_id"], item["id"])

    def test_tampered_archive_fails_closed(self):
        item = self.submit()
        self.install(item)
        with self.store.connection() as db:
            db.execute("UPDATE packages SET archive=? WHERE listing_id=?", (package("page", "2.0.0"), item["id"]))
        self.assertEqual(self.store.runtime(self.reader), [])
        with self.assertRaises(mp.MarketError):
            self.install(item)

    def test_seed_review_does_not_overwrite_operator_rejection(self):
        seeded = mp.Marketplace(self.temp.name)
        item = seeded.detail('library-midnight', None)
        self.assertEqual(item["review_status"], "reviewed")
        seeded.review_package(item["id"], item["latest"], item["sha256"], "Operator", "Rejected for test.", "high", "rejected")
        restarted = mp.Marketplace(self.temp.name)
        self.assertEqual(restarted.detail(item["id"], None)["review_status"], "blocked")

    def test_legacy_device_install_migrates_without_loss(self):
        item = self.submit()
        self.install(item)
        with self.store.connection() as db:
            db.execute("ALTER TABLE installations RENAME TO installations_new")
            db.execute("CREATE TABLE installations (listing_id TEXT PRIMARY KEY, version TEXT, sha256 TEXT, enabled INTEGER, installed_by TEXT, installed INTEGER)")
            db.execute("INSERT INTO installations SELECT * FROM installations_new")
            db.execute("DROP TABLE installations_new")
        migrated = mp.Marketplace(self.temp.name, seed=False)
        self.assertEqual(len(migrated.runtime(self.reader)), 1)
        self.assertEqual(migrated.runtime(self.owner), [])

    def test_legacy_shapes_that_used_to_abort_the_migration(self):
        """The fixture above is the one legacy shape that cannot fail: it
        already has installed_by, holding a live account id. The prototype that
        recorded no owner raised on the column count, and a row naming a deleted
        account raised on the foreign key. Because DDL autocommits, the half-done
        rename survived, the next launch skipped the block, and every install was
        stranded. Both shapes are pinned here."""
        item = self.submit()
        self.install(item)

        def rebuild(columns, values):
            with self.store.connection() as db:
                db.execute("DROP TABLE installations")
                db.execute(f"CREATE TABLE installations ({columns}, PRIMARY KEY(listing_id))")
                db.execute(f"INSERT INTO installations VALUES ({values})",
                           (item["id"], item["latest"], item["sha256"]))

        # No installed_by column at all: the device-wide prototype.
        rebuild("listing_id TEXT NOT NULL, version TEXT NOT NULL, sha256 TEXT NOT NULL, "
                "enabled INTEGER NOT NULL DEFAULT 1, installed INTEGER NOT NULL", "?,?,?,1,0")
        migrated = mp.Marketplace(self.temp.name, seed=False)
        with migrated.connection() as db:
            owners = {r[0] for r in db.execute("SELECT installed_by FROM installations")}
            stranded = db.execute("SELECT count(*) FROM sqlite_master WHERE name='installations_legacy'").fetchone()[0]
        self.assertEqual(owners, {self.owner["id"], self.reader["id"]},
                         "a device-wide install belongs to every account on the device")
        self.assertEqual(stranded, 0, "nothing may be left behind in installations_legacy")

        # installed_by naming an account that no longer exists.
        rebuild("listing_id TEXT NOT NULL, version TEXT NOT NULL, sha256 TEXT NOT NULL, "
                "enabled INTEGER NOT NULL DEFAULT 1, installed_by TEXT NOT NULL, installed INTEGER NOT NULL",
                "?,?,?,1,'deleted-account',0")
        migrated = mp.Marketplace(self.temp.name, seed=False)
        with migrated.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM installations").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='installations_legacy'").fetchone()[0], 0)
        # Re-opening a migrated database must stay a no-op, and must still work.
        self.assertEqual(mp.Marketplace(self.temp.name, seed=False).catalog(self.reader)[0]["id"], item["id"])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = mp.Marketplace(cls.temp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.store))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path="catalog", method="GET", data=None, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        defaults = {"Host": f"localhost:{self.port}"}
        if method == "POST":
            defaults.update({"Content-Type": "application/json", "X-Marketplace-Request": "1"})
        defaults.update(headers)
        connection.request(method, "/marketplace/" + path, body=json.dumps(data or {}) if method == "POST" else None, headers=defaults)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_foreign_origin_rebinding_and_proxies_blocked(self):
        for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"}, {"Origin": "null"}, {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "cross-site"}, {"X-Forwarded-For": "127.0.0.1"}, {"Cf-Ray": "proxy"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(**headers)[0], 403)

    def test_body_limits_and_csrf_header(self):
        self.assertEqual(self.request("account/login", "POST", **{"X-Marketplace-Request": ""})[0], 403)
        self.assertEqual(self.request("account/login", "POST", **{"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("account/login", "POST", **{"Content-Length": str(mp.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.request("account/login", "POST", **{"Transfer-Encoding": "chunked"})[0], 400)

    def test_http_cookie_logout_media_and_no_remote_review_route(self):
        status, headers, body = self.request("account/register", "POST", {"username": "browser", "name": "Browser", "password": "long enough test password", "avatar": PNG})
        self.assertEqual(status, 200, body)
        cookie = headers["Set-Cookie"]
        self.assertTrue(cookie.startswith(f"data_market_session_{self.port}="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        account = json.loads(body)["account"]
        self.assertEqual(self.request("session", Cookie=cookie)[0], 200)
        status, image_headers, raw = self.request("media/account/" + account["id"])
        self.assertEqual((status, image_headers["Content-Type"]), (200, "image/png"))
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.request("admin/review", "POST", {}, Cookie=cookie)[0], 404)
        self.assertEqual(self.request("account/logout", "POST", {}, Cookie=cookie)[0], 200)
        self.assertIsNone(json.loads(self.request("session", Cookie=cookie)[2])["account"])


if __name__ == "__main__":
    unittest.main()
