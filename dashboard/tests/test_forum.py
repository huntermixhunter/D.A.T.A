"""Forum persistence, ownership and HTTP regression checks. Uses temporary data."""
import http.client
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import forum
import marketplace as mp
from marketplace_preview import make_handler


class ForumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = mp.Marketplace(self.temp.name, seed=False)
        self.forum = forum.get_forum(self.store)
        self.owner, self.owner_token = self.register("author")
        self.reader, self.reader_token = self.register("reader")

    def register(self, name):
        result, token = self.store.account_action("register", {"username": name, "name": name.title(), "password": "a long test-only password"}, None, "", name)
        return result["account"], token

    def create(self, account=None, **extra):
        data = {"title": "A useful discussion", "body": "A question about DATA.", "category": "general", "request_id": secrets.token_hex(16), **extra}
        return self.forum.create(data, account or self.owner)

    def reply(self, tid, account=None, **extra):
        return self.forum.create({"body": "Here is a useful reply.", "request_id": secrets.token_hex(16), **extra}, account or self.reader, tid)

    def test_same_marketplace_account_profile_and_persistent_history(self):
        topic = self.create()
        reply = self.reply(topic["thread_id"])
        result, _ = self.store.account_action("profile", {"name": "Updated Name"}, self.owner, self.owner_token, "author")
        reopened = forum.get_forum(mp.Marketplace(self.temp.name, seed=False))
        detail = reopened.detail(topic["thread_id"], {}, self.reader)
        self.assertEqual(detail["thread"]["author"]["name"], "Updated Name")
        self.assertEqual(detail["thread"]["author"]["id"], result["account"]["id"])
        self.assertEqual(detail["thread"]["replies"], 1)
        self.assertEqual(detail["posts"][1]["id"], reply["post_id"])
        self.assertNotIn("password", json.dumps(detail))
        self.assertEqual(reopened.meta(self.owner)["threads"], 1)

    def test_guest_can_read_but_cannot_write_or_read_following(self):
        topic = self.create()
        self.assertEqual(len(self.forum.detail(topic["thread_id"], {}, None)["posts"]), 1)
        for action in (lambda: self.forum.create({"body": "hello"}, None),
                       lambda: self.forum.threads({"view": ["following"]}, None),
                       lambda: self.forum.post_action(topic["post_id"], "delete", {}, None)):
            with self.assertRaises(mp.MarketError) as caught:
                action()
            self.assertEqual(caught.exception.status, 401)

    def test_identity_spoof_cannot_edit_or_delete_another_members_post(self):
        topic = self.create()
        for action in ("edit", "delete"):
            with self.assertRaises(mp.MarketError) as caught:
                self.forum.post_action(topic["post_id"], action, {"body": "stolen", "author_id": self.owner["id"]}, self.reader)
            self.assertEqual(caught.exception.status, 403)
        self.forum.post_action(topic["post_id"], "edit", {"body": "Edited safely", "title": "A better title"}, self.owner)
        detail = self.forum.detail(topic["thread_id"], {}, self.reader)
        self.assertEqual(detail["thread"]["title"], "A better title")
        self.assertTrue(detail["posts"][0]["edited"])

    def test_categories_search_sort_and_account_filters(self):
        self.create(title="Gardening conversations", body="Unrelated weekend question", category="off-topic")
        topic = self.create(title="Dashboard shortcuts", body="Unique body searchable phrase")
        self.reply(topic["thread_id"])
        self.assertEqual(self.forum.threads({"category": ["off-topic"]}, None)["total"], 1)
        self.assertEqual(self.forum.threads({"q": ["searchable phrase"]}, None)["total"], 1)
        self.assertEqual(self.forum.threads({"q": ["%"]}, None)["total"], 0)
        self.assertEqual(self.forum.threads({"sort": ["unanswered"]}, None)["total"], 1)
        self.assertEqual(self.forum.threads({"view": ["mine"]}, self.reader)["total"], 0)
        self.assertEqual(self.forum.threads({"view": ["following"]}, self.reader)["total"], 1)
        for query in ({"category": ["missing"]}, {"page": ["0"]}, {"page": ["-1"]}, {"q": ["x" * 121]}, {"sort": ["sql"]}):
            with self.assertRaises(mp.MarketError):
                self.forum.threads(query, self.reader)

    def test_deduplication_stops_retried_posts_without_losing_content(self):
        data = {"title": "Retry test", "body": "Retried message", "category": "general", "request_id": secrets.token_hex(16)}
        first = self.forum.create(data, self.owner)
        duplicate = self.forum.create(data, self.owner)
        self.assertEqual(first["thread_id"], duplicate["thread_id"])
        self.assertTrue(duplicate["duplicate"])
        with self.assertRaises(mp.MarketError):
            self.forum.create(dict(data, body="Different message"), self.owner)
        reply = {"body": "Reply once", "request_id": secrets.token_hex(16)}
        self.forum.create(reply, self.reader, first["thread_id"])
        self.forum.create(reply, self.reader, first["thread_id"])
        self.assertEqual(self.forum.detail(first["thread_id"], {}, None)["total"], 2)

    def test_likes_and_follows_are_idempotent_and_account_scoped(self):
        topic = self.create()
        for _ in range(2):
            self.forum.post_action(topic["post_id"], "like", {"liked": True}, self.reader)
            self.forum.follow(topic["thread_id"], {"following": True}, self.reader)
        owner_view = self.forum.detail(topic["thread_id"], {}, self.owner)
        reader_view = self.forum.detail(topic["thread_id"], {}, self.reader)
        self.assertEqual(owner_view["posts"][0]["likes"], 1)
        self.assertFalse(owner_view["posts"][0]["liked"])
        self.assertTrue(reader_view["posts"][0]["liked"])
        self.forum.follow(topic["thread_id"], {"following": False}, self.reader)
        self.assertEqual(self.forum.threads({"view": ["following"]}, self.reader)["total"], 0)

    def test_deleted_reply_redacts_body_and_deleted_thread_is_not_discoverable(self):
        topic = self.create()
        reply = self.reply(topic["thread_id"], body="Unpublished personal information")
        self.forum.post_action(reply["post_id"], "delete", {}, self.reader)
        detail = self.forum.detail(topic["thread_id"], {}, None)
        self.assertEqual(detail["posts"][1]["body"], "")
        self.assertTrue(detail["posts"][1]["deleted"])
        self.assertEqual(detail["thread"]["replies"], 0)
        self.assertEqual(self.forum.threads({"q": ["personal information"]}, None)["total"], 0)
        self.forum.post_action(topic["post_id"], "delete", {}, self.owner)
        with self.assertRaises(mp.MarketError):
            self.forum.detail(topic["thread_id"], {}, None)
        self.assertEqual(self.forum.meta(None)["posts"], 0)

    def test_reports_are_private_and_moderation_has_no_http_route(self):
        topic = self.create()
        self.forum.post_action(topic["post_id"], "report", {"reason": "Private moderator context"}, self.reader)
        self.assertNotIn("Private moderator", json.dumps(self.forum.detail(topic["thread_id"], {}, self.owner)))
        with self.assertRaises(mp.MarketError):
            self.forum.route(["moderate"], "POST", {"action": "hide-thread"}, self.reader, {})
        self.forum.moderate("lock", topic["thread_id"], "Closed for review")
        with self.assertRaises(mp.MarketError) as caught:
            self.reply(topic["thread_id"])
        self.assertEqual(caught.exception.status, 409)
        self.forum.moderate("unlock", topic["thread_id"], "Review complete")
        reply = self.reply(topic["thread_id"], body="Moderated hidden text")
        self.forum.moderate("hide-post", reply["post_id"], "Hidden for review")
        self.assertEqual(self.forum.detail(topic["thread_id"], {}, self.reader)["total"], 1)
        self.assertEqual(self.forum.threads({"q": ["Moderated hidden"]}, None)["total"], 0)
        self.forum.moderate("suspend", self.reader["id"], "Repeated abuse reports")
        with self.assertRaises(mp.MarketError) as caught:
            self.reply(topic["thread_id"])
        self.assertEqual(caught.exception.status, 403)
        self.assertTrue(self.forum.meta(self.reader)["restricted"])

    def test_rate_limit_persists_across_restarts(self):
        for i in range(8):
            self.create(title=f"Discussion number {i}")
        reopened = forum.get_forum(mp.Marketplace(self.temp.name, seed=False))
        with self.assertRaises(mp.MarketError) as caught:
            reopened.create({"title": "Too many threads", "body": "A post", "category": "general", "request_id": secrets.token_hex(16)}, self.owner)
        self.assertEqual(caught.exception.status, 429)

    def test_pagination_unread_and_plain_text_payloads(self):
        base = forum.now()
        with patch.object(forum, "now", return_value=base):
            topic = self.create(body='<img src=x onerror="alert(1)">')
        self.assertTrue(self.forum.threads({}, self.reader)["threads"][0]["unread"])
        self.forum.detail(topic["thread_id"], {}, self.reader)
        self.assertFalse(self.forum.threads({}, self.reader)["threads"][0]["unread"])
        for i in range(31):
            with patch.object(forum, "now", return_value=base + (i + 1) * 60_000):
                retry_id = secrets.token_hex(16)
                last = self.reply(topic["thread_id"], account=self.owner, body=f"Reply {i}", request_id=retry_id)
        repeated = self.reply(topic["thread_id"], account=self.owner, body="Reply 30", request_id=retry_id)
        self.assertEqual((last["page"], repeated["page"]), (2, 2))
        page1 = self.forum.detail(topic["thread_id"], {}, self.reader)
        self.assertEqual(len(page1["posts"]), 30)
        self.assertTrue(self.forum.threads({}, self.reader)["threads"][0]["unread"])
        page2 = self.forum.detail(topic["thread_id"], {"page": ["2"]}, self.reader)
        self.assertEqual(len(page2["posts"]), 2)
        self.assertFalse(self.forum.threads({}, self.reader)["threads"][0]["unread"])
        self.assertIn('<img src=x', page1["posts"][0]["body"])


class ForumHttpTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DATA_COMMUNITY_URL": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = mp.Marketplace(self.temp.name, seed=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        supplied = {"Host": f"localhost:{self.server.server_port}", "Content-Type": "application/json", "X-Marketplace-Request": "1", **(headers or {})}
        conn.request("GET" if data is None else "POST", "/marketplace/" + path, body=None if data is None else json.dumps(data), headers=supplied)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), json.loads(response.read())
        conn.close()
        return result

    def test_http_same_cookie_registration_logout_and_csrf_guards(self):
        status, headers, account = self.request("account/register", {"username": "member", "name": "Forum Member", "password": "a long test password"})
        self.assertEqual(status, 200)
        cookie = {"Cookie": headers["Set-Cookie"]}
        meta = self.request("forum/meta", headers=cookie)[2]
        self.assertEqual(meta["account"]["id"], account["account"]["id"])
        data = {"title": "HTTP discussion", "body": "One shared local identity", "category": "general", "request_id": secrets.token_hex(16)}
        self.assertEqual(self.request("forum/threads", data)[0], 401)
        topic = self.request("forum/threads", data, cookie)[2]
        self.assertTrue(topic["thread_id"])
        for malicious in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"X-Forwarded-For": "127.0.0.1"}, {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.request("forum/meta", headers=malicious)[0], 403)
        self.assertEqual(self.request("forum/threads", data, {**cookie, "X-Marketplace-Request": ""})[0], 403)
        self.request("account/logout", {}, cookie)
        self.assertEqual(self.request("forum/threads", dict(data, request_id=secrets.token_hex(16)), cookie)[0], 401)


if __name__ == "__main__":
    unittest.main()
