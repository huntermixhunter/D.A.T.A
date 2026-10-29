"""Ranking, public-catalog boundaries, caching and authenticated HTTP tests.

Run: python -m unittest discover -s dashboard/tests -p test_skill_search.py -v
All upstream responses are fixtures. No paid or live services are contacted.
"""
import io
import http.client
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill_search as ss


def row(name, installs, source="example/skills", **kwargs):
    return {"name": name, "skillId": name, "source": source, "installs": installs, **kwargs}


class SkillSearchTests(unittest.TestCase):
    def setUp(self):
        ss._cache.clear()

    def results(self, rows, query="pdf", sort="relevance"):
        with patch.object(ss, "_fetch", return_value=ss._normalize(rows)):
            return ss.search(query, sort)["skills"]

    def test_exact_match_precedes_more_popular_partial_match(self):
        items = self.results([row("pdf-tools", 999999), row("pdf", 10)])
        self.assertEqual([i["name"] for i in items], ["pdf", "pdf-tools"])

    def test_popularity_breaks_relevance_ties(self):
        items = self.results([row("pdf-reader", 2), row("pdf-editor", 500)])
        self.assertEqual(items[0]["name"], "pdf-editor")

    def test_phrase_matches_and_typos_precede_unrelated_names(self):
        items = self.results([row("pdf", 99999), row("pdf-editing", 2)], "PDF editing")
        self.assertEqual(items[0]["name"], "pdf-editing")
        ss._cache.clear()
        items = self.results([row("design", 99999), row("spreadsheets", 2)], "spreadshets")
        self.assertEqual(items[0]["name"], "spreadsheets")

    def test_popularity_sort_keeps_missing_counts_last(self):
        items = self.results([row("pdf", None), row("pdf-reader", 0), row("pdf-editor", 500)], sort="installs")
        self.assertEqual([i["installs"] for i in items], [500, 0, None])

    def test_deduplicates_and_rejects_untrusted_sources_and_paths(self):
        items = ss._normalize([
            row("pdf", 1), row("pdf", 100), None,
            row("bad", 1, source="evil.test/user/repo"),
            row("bad", 1, source="https://evil.test/repo"),
            row("bad", 1, skillId="../bad"), row("bad", 1, skillId="x?redirect=evil"),
            row("ok", True, url="javascript:alert(1)", description="<img onerror=alert(1)>"),
        ])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["url"], "https://skills.sh/example/skills/ok")
        self.assertIsNone(items[1]["installs"])
        self.assertNotIn("rating", items[1])

    def test_supports_slug_fallback_from_catalog_id(self):
        items = ss._normalize([{"id": "example/skills/pdf", "source": "example/skills", "name": "PDF"}])
        self.assertEqual(items[0]["skill_id"], "pdf")

    def test_invalid_inputs_never_contact_catalog(self):
        with patch.object(ss, "_fetch") as fetch:
            for query in ("", " ", "a", "!?!", "a" * 161):
                with self.subTest(query=query), self.assertRaises(ValueError):
                    ss.search(query)
            with self.assertRaises(ValueError):
                ss.search("pdf", "ratings")
            fetch.assert_not_called()

    def test_cache_reused_across_normalized_query_and_sort(self):
        with patch.object(ss, "_fetch", return_value=ss._normalize([row("pdf", 1)])) as fetch:
            self.assertFalse(ss.search(" PDF ")["cached"])
            self.assertTrue(ss.search("pdf", "installs")["cached"])
            fetch.assert_called_once_with("PDF")
            # Per-request scoring and sorting must not mutate the cached rows.
            self.assertNotIn("relevance", ss._cache["pdf"][1][0])

    def test_cache_expiry_and_size_are_bounded(self):
        with patch.object(ss, "CACHE_LIMIT", 2), patch.object(ss, "_fetch", return_value=[]) as fetch:
            with patch.object(ss.time, "monotonic", return_value=1):
                ss.search("one"); ss.search("two"); ss.search("three")
            self.assertEqual(len(ss._cache), 2)
            self.assertNotIn("one", ss._cache)
            with patch.object(ss.time, "monotonic", return_value=ss.CACHE_TTL + 2):
                self.assertFalse(ss.search("two")["cached"])
            self.assertEqual(fetch.call_count, 4)

    def test_failed_search_is_retryable_and_not_cached(self):
        with patch.object(ss, "_fetch", side_effect=[ss.SearchUnavailable("offline"), []]) as fetch:
            with self.assertRaises(ss.SearchUnavailable):
                ss.search("pdf")
            self.assertFalse(ss.search("pdf")["cached"])
            self.assertEqual(fetch.call_count, 2)

    def test_upstream_request_is_encoded_bounded_and_credential_free(self):
        with patch.object(ss.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b'{"skills":[]}')
            self.assertEqual(ss._fetch("pdf & docs"), [])
            args, kwargs = opener.return_value.open.call_args
            request = args[0]
            self.assertTrue(request.full_url.startswith(ss.SEARCH_URL + "?"))
            self.assertIn("q=pdf+%26+docs", request.full_url)
            self.assertNotIn("Authorization", dict(request.header_items()))
            self.assertEqual(kwargs["timeout"], 10)

    def test_upstream_invalid_payloads_and_errors_are_explained(self):
        for body in (b'[]', b'{"skills":{}}', b'not json', b'x' * (ss.MAX_BYTES + 1)):
            with self.subTest(body=body[:20]), patch.object(ss.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(body)
                with self.assertRaises(ss.SearchUnavailable):
                    ss._fetch("pdf")
        for error in (TimeoutError(), http.client.IncompleteRead(b"partial"),
                      urllib.error.HTTPError(ss.SEARCH_URL, 429, "rate limit", {}, None)):
            with patch.object(ss.urllib.request, "build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(ss.SearchUnavailable):
                    ss._fetch("pdf")

    def test_redirects_cannot_change_upstream_host(self):
        self.assertIsNone(ss._NoRedirect().redirect_request(None, None, 302, "", {}, "http://127.0.0.1"))

    def test_http_auth_validation_success_and_failure(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def _check_auth(self):
                if self.headers.get("X-Data-Token") == "test-only":
                    return True
                self.send_response(401); self.end_headers()
                return False
            def do_GET(self):
                if not ss.handle_http(self):
                    self.send_response(404); self.end_headers()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(query, auth=True):
            return urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}{ss.SEARCH_PATH}?{query}",
                headers={"X-Data-Token": "test-only"} if auth else {})
        try:
            with patch.object(ss, "_fetch", return_value=ss._normalize([row("pdf", 100)])) as fetch:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(request("q=pdf", False))
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                fetch.assert_not_called()
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    urllib.request.urlopen(request("q=x"))
                self.assertEqual(invalid.exception.code, 400)
                invalid.exception.close()
                fetch.assert_not_called()
                with urllib.request.urlopen(request("q=pdf")) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(json.load(response)["skills"][0]["installs"], 100)
            with patch.object(ss, "_fetch", side_effect=ss.SearchUnavailable("Catalog is busy.")):
                with self.assertRaises(urllib.error.HTTPError) as failed:
                    urllib.request.urlopen(request("q=design"))
                self.assertEqual(failed.exception.code, 503)
                self.assertEqual(json.load(failed.exception)["error"], "Catalog is busy.")
                failed.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
