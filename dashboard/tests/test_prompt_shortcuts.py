"""prompt_shortcuts: storage rules and the /prompt-shortcuts routes behind a
real local HTTP server. Hermetic: every test uses its own temp user folder.

Shared by DATA/dashboard and LCARS/lcars-dashboard; keep the copies identical.
"""
import http.client
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prompt_shortcuts as ps  # noqa: E402


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, items):
        return [s["name"] for s in items]

    def test_starter_set_until_first_save(self):
        self.assertEqual(self.names(ps.load(self.dir)), ["brief", "review", "tests", "explain", "plan"])
        self.assertFalse((self.dir / ps.FILE_NAME).exists())

    def test_save_new_update_and_rename(self):
        ps.save(self.dir, {"name": "/Standup", "prompt": "  Summarise today  ", "desc": "daily\n  note"})
        got = {s["name"]: s for s in ps.load(self.dir)}
        self.assertEqual(got["standup"]["prompt"], "Summarise today")
        self.assertEqual(got["standup"]["desc"], "daily note")
        ps.save(self.dir, {"name": "standup", "prompt": "v2"})
        self.assertEqual({s["name"]: s for s in ps.load(self.dir)}["standup"]["prompt"], "v2")
        items = ps.save(self.dir, {"name": "eod", "prompt": "v3"}, rename_from="standup")
        self.assertIn("eod", self.names(items))
        self.assertNotIn("standup", self.names(items))
        self.assertEqual(len(items), 6)   # renamed in place, not added

    def test_rename_onto_existing_is_refused(self):
        with self.assertRaises(ps.ShortcutError) as cm:
            ps.save(self.dir, {"name": "review", "prompt": "x"}, rename_from="brief")
        self.assertEqual(cm.exception.status, 409)

    def test_validation(self):
        for bad in ({"name": "", "prompt": "x"}, {"name": "9lives", "prompt": "x"},
                    {"name": "has space", "prompt": "x"}, {"name": "a" * 33, "prompt": "x"},
                    {"name": "ok", "prompt": "   "}, {"name": "ok", "prompt": "x" * (ps.MAX_PROMPT + 1)}):
            with self.assertRaises(ps.ShortcutError, msg=bad["name"]):
                ps.save(self.dir, bad)
        self.assertFalse((self.dir / ps.FILE_NAME).exists())

    def test_delete_and_reset(self):
        self.assertNotIn("tests", self.names(ps.delete(self.dir, "/tests")))
        with self.assertRaises(ps.ShortcutError) as cm:
            ps.delete(self.dir, "tests")
        self.assertEqual(cm.exception.status, 404)
        ps.delete(self.dir, "brief")
        ps.delete(self.dir, "review")
        ps.delete(self.dir, "explain")
        self.assertEqual(self.names(ps.delete(self.dir, "plan")), [])
        self.assertEqual(ps.load(self.dir), [])   # an emptied list stays empty
        self.assertEqual(len(ps.reset(self.dir)), 5)

    def test_damaged_file_reads_as_starter_set_and_skips_bad_rows(self):
        f = self.dir / ps.FILE_NAME
        f.write_text("{not json", encoding="utf-8")
        self.assertEqual(len(ps.load(self.dir)), 5)
        f.write_text(json.dumps({"shortcuts": [{"name": "ok", "prompt": "p"}, {"name": "BAD NAME", "prompt": "p"},
                                               {"name": "ok", "prompt": "dupe"}, "junk"]}), encoding="utf-8")
        self.assertEqual(ps.load(self.dir), [{"name": "ok", "prompt": "p", "desc": ""}])


class _Handler(http.server.BaseHTTPRequestHandler):
    env: dict = {}

    def log_message(self, *a):
        pass

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def _check_auth(self):
        tok = self.env.get("DATA_BRIDGE_TOKEN")
        if not tok or self.headers.get("X-Data-Token") == tok:
            return True
        self.send_response(401)
        self.end_headers()
        return False

    def _route(self):
        if not ps.handle_http(self, self.env):
            self.send_response(404)
            self.end_headers()

    do_GET = _route
    do_POST = _route


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        _Handler.env = {"_user_dir": lambda: d}

    def tearDown(self):
        self.tmp.cleanup()

    def req(self, method, payload=None, headers=None, path=ps.ROUTE, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"localhost:{self.port}", "X-Data-Request": "1"}
        body = None
        if payload is not None or raw is not None:
            body = raw if raw is not None else json.dumps(payload).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        conn.request(method, path, body, h)
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text

    def test_get_and_save_roundtrip(self):
        status, body = self.req("GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["shortcuts"]), 5)
        status, body = self.req("POST", {"action": "save", "name": "standup", "prompt": "Summarise today"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        _, body = self.req("GET")
        self.assertIn("standup", [s["name"] for s in body["shortcuts"]])
        status, body = self.req("POST", {"action": "delete", "name": "standup"})
        self.assertEqual(status, 200)
        status, body = self.req("POST", {"action": "delete", "name": "standup"})
        self.assertEqual(status, 404)

    def test_other_paths_fall_through(self):
        status, _ = self.req("GET", path="/providers")
        self.assertEqual(status, 404)

    def test_missing_dashboard_header_is_refused(self):
        status, body = self.req("POST", {"action": "save", "name": "x", "prompt": "evil"},
                                {"X-Data-Request": None})
        self.assertEqual(status, 403)
        status, _ = self.req("GET", headers={"X-Data-Request": None})
        self.assertEqual(status, 403)

    def test_cross_site_and_foreign_origin_are_refused(self):
        status, _ = self.req("POST", {"action": "save", "name": "x", "prompt": "evil"},
                             {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, _ = self.req("POST", {"action": "save", "name": "x", "prompt": "evil"},
                             {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.req("GET", headers={"Origin": f"http://localhost:{self.port}",
                                             "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)

    def test_remote_host_needs_a_token(self):
        # A DNS-rebound or tunnel hostname: page and bridge share the origin, so
        # only a configured token makes it trustworthy.
        remote = {"Host": "evil.example:7777", "Origin": "http://evil.example:7777"}
        status, _ = self.req("POST", {"action": "save", "name": "x", "prompt": "evil"}, remote)
        self.assertEqual(status, 403)
        _Handler.env["DATA_BRIDGE_TOKEN"] = "s3cret"
        status, _ = self.req("GET", headers=remote)
        self.assertEqual(status, 401)
        status, _ = self.req("GET", headers={**remote, "X-Data-Token": "s3cret"})
        self.assertEqual(status, 200)

    def test_bad_bodies(self):
        status, _ = self.req("POST", raw=b"{oops")
        self.assertEqual(status, 400)
        status, _ = self.req("POST", {"action": "save", "name": "x", "prompt": "p"},
                             {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _ = self.req("POST", raw=b"x" * (ps.MAX_BODY + 1))
        self.assertEqual(status, 413)
        status, body = self.req("POST", {"action": "explode"})
        self.assertEqual(status, 400)
        status, body = self.req("POST", {"action": "save", "name": "Bad Name", "prompt": "p"})
        self.assertEqual(status, 400)
        self.assertIn("lowercase", body["error"])


if __name__ == "__main__":
    unittest.main()
