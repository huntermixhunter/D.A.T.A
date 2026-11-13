"""Tests for app_window.py, the standalone dashboard window launcher."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_window  # noqa: E402


class FlagTests(unittest.TestCase):
    def test_first_run_sets_size_and_private_profile(self):
        flags = app_window.browser_flags("http://localhost:7777/", Path("/p"), "DATA", True)
        self.assertIn("--app=http://localhost:7777/", flags)
        self.assertIn(f"--user-data-dir={Path('/p')}", flags)
        self.assertIn("--no-first-run", flags)
        self.assertTrue(any(f.startswith("--window-size=") for f in flags))

    def test_later_runs_keep_the_saved_window_size(self):
        flags = app_window.browser_flags("http://localhost:7777/", Path("/p"), "DATA", False)
        self.assertFalse(any(f.startswith("--window-size=") for f in flags))

    def test_mac_uses_open_for_a_detached_instance(self):
        with mock.patch.object(app_window.sys, "platform", "darwin"):
            cmd = app_window.launch_command("/Applications/Google Chrome.app", ["--app=x"])
        self.assertEqual(cmd, ["open", "-na", "/Applications/Google Chrome.app", "--args", "--app=x"])

    def test_windows_runs_the_browser_directly(self):
        with mock.patch.object(app_window.sys, "platform", "win32"):
            cmd = app_window.launch_command(r"C:\chrome.exe", ["--app=x"])
        self.assertEqual(cmd, [r"C:\chrome.exe", "--app=x"])


class ProfileTests(unittest.TestCase):
    def test_profiles_are_separated_by_name(self):
        self.assertNotEqual(app_window.profile_root("DATA"), app_window.profile_root("LCARS"))
        self.assertEqual(app_window.profile_root("DATA").name, "app-window")


class OpenTests(unittest.TestCase):
    def test_kill_switch_uses_the_default_browser(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "0"}), \
             mock.patch.object(app_window.webbrowser, "open") as opened, \
             mock.patch.object(app_window, "_spawn") as spawned:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_called_once_with("http://localhost:7777/")
        spawned.assert_not_called()
        self.assertIn("browser", result)

    def test_no_chromium_browser_falls_back(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "1"}), \
             mock.patch.object(app_window, "find_browser", return_value=None), \
             mock.patch.object(app_window.webbrowser, "open") as opened:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_called_once()
        self.assertIn("no Chrome or Edge", result)

    def test_spawn_failure_falls_back(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "1"}), \
             mock.patch.object(app_window, "find_browser", return_value=("chrome", "chrome")), \
             mock.patch.object(app_window, "profile_root", return_value=Path(self._tmp())), \
             mock.patch.object(app_window, "_spawn", side_effect=OSError("boom")), \
             mock.patch.object(app_window.webbrowser, "open") as opened:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_called_once()
        self.assertIn("failed", result)

    def test_app_window_launch(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "1"}), \
             mock.patch.object(app_window, "find_browser", return_value=("edge", "msedge")), \
             mock.patch.object(app_window, "profile_root", return_value=Path(self._tmp())), \
             mock.patch.object(app_window, "_spawn") as spawned, \
             mock.patch.object(app_window.webbrowser, "open") as opened:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_not_called()
        cmd = spawned.call_args[0][0]
        self.assertIn("--app=http://localhost:7777/?window=app", cmd)
        self.assertEqual(result, "edge app window")

    def test_app_url_tag(self):
        self.assertEqual(app_window.app_url("http://localhost:7777/"),
                         "http://localhost:7777/?window=app")
        self.assertEqual(app_window.app_url("http://localhost:7777/?vitals=1"),
                         "http://localhost:7777/?vitals=1&window=app")

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d


class _FakeHandler:
    """Just enough of BaseHTTPRequestHandler for the /open-external route."""

    def __init__(self, headers=None, peer="127.0.0.1", port=7777):
        base = {"Host": f"localhost:{port}", "X-Data-Request": "1",
                "Origin": f"http://localhost:{port}", "Sec-Fetch-Site": "same-origin"}
        base.update(headers or {})
        self.headers = {k: v for k, v in base.items() if v is not None}
        self.client_address = (peer, 50000)
        self.server = mock.Mock(server_address=("127.0.0.1", port))
        self.replies = []

    def _json(self, obj, code=200):
        self.replies.append((code, obj))


class ExternalUrlTests(unittest.TestCase):
    def test_plain_web_links_pass(self):
        for url in ("https://t.me/BotFather", "http://example.com/a?b=c#d",
                    "https://tailscale.com/download"):
            self.assertEqual(app_window.external_url(url), url)

    def test_other_schemes_are_refused(self):
        for url in ("file:///C:/Windows/System32/calc.exe", "ms-settings:privacy",
                    "javascript:alert(1)", "mailto:a@b.c", r"C:\Windows\notepad.exe",
                    "//example.com", "https://", "", None, 42, {"url": "x"}):
            self.assertIsNone(app_window.external_url(url), url)

    def test_odd_characters_and_huge_urls_are_refused(self):
        for url in ('https://a.com/"x', "https://a.com/x y", "https://a.com/\x00",
                    "https://a.com/\nx", "https://a.com/" + "a" * 5000):
            self.assertIsNone(app_window.external_url(url))


class LocalRequestTests(unittest.TestCase):
    def test_dashboard_on_this_computer_is_allowed(self):
        self.assertEqual(app_window.local_dashboard_request(_FakeHandler()), "")
        self.assertEqual(app_window.local_dashboard_request(
            _FakeHandler({"Origin": None, "Sec-Fetch-Site": None})), "")
        self.assertEqual(app_window.local_dashboard_request(
            _FakeHandler({"Host": "127.0.0.1:7777", "Origin": "http://127.0.0.1:7777"})), "")

    def test_refusals(self):
        cases = {
            "remote peer": _FakeHandler(peer="100.64.0.7"),
            "tunnel": _FakeHandler({"Cf-Connecting-Ip": "1.2.3.4"}),
            "rebinding host": _FakeHandler({"Host": "evil.example:7777",
                                            "Origin": "http://evil.example:7777"}),
            "wrong port": _FakeHandler({"Host": "localhost:9999"}),
            "no marker": _FakeHandler({"X-Data-Request": None}),
            "cross-site": _FakeHandler({"Sec-Fetch-Site": "cross-site"}),
            "foreign origin": _FakeHandler({"Origin": "https://evil.example"}),
        }
        for label, handler in cases.items():
            self.assertNotEqual(app_window.local_dashboard_request(handler), "", label)


class OpenExternalTests(unittest.TestCase):
    def test_opens_in_default_browser(self):
        h, opener = _FakeHandler(), mock.Mock(return_value=True)
        app_window.handle_open_external(h, b'{"url": "https://t.me/BotFather"}', opener)
        opener.assert_called_once_with("https://t.me/BotFather")
        self.assertEqual(h.replies, [(200, {"ok": True})])

    def test_refused_request_never_opens(self):
        h, opener = _FakeHandler({"X-Data-Request": None}), mock.Mock()
        app_window.handle_open_external(h, b'{"url": "https://t.me/BotFather"}', opener)
        opener.assert_not_called()
        self.assertEqual(h.replies[0][0], 403)

    def test_bad_bodies_are_rejected(self):
        for body in (b"", b"not json", b"[]", b'{"url": "file:///etc/passwd"}'):
            h, opener = _FakeHandler(), mock.Mock()
            app_window.handle_open_external(h, body, opener)
            opener.assert_not_called()
            self.assertEqual(h.replies[0][0], 400, body)

    def test_browser_failure_is_reported(self):
        for opener in (mock.Mock(return_value=False), mock.Mock(side_effect=OSError("x"))):
            h = _FakeHandler()
            app_window.handle_open_external(h, b'{"url": "https://example.com"}', opener)
            self.assertEqual(h.replies[0][0], 500)


class WaitTests(unittest.TestCase):
    def test_wait_gives_up_after_timeout(self):
        # Port 9 (discard) is not served locally, so this must time out quickly.
        self.assertFalse(app_window.wait_for_bridge("http://127.0.0.1:9/", 0.2))


class WindowModeTests(unittest.TestCase):
    """Settings > Window: app window (default) or a normal browser tab."""

    def setUp(self):
        import shutil
        import tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        patcher = mock.patch.object(app_window, "profile_root",
                                    side_effect=lambda name: d / name / "app-window")
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "1"})
        env.start()
        self.addCleanup(env.stop)

    def test_defaults_to_app_window(self):
        self.assertEqual(app_window.load_mode("DATA"), "app")

    def test_choice_is_saved_per_dashboard(self):
        app_window.save_mode("DATA", "browser")
        self.assertEqual(app_window.load_mode("DATA"), "browser")
        self.assertEqual(app_window.load_mode("LCARS"), "app")
        app_window.save_mode("DATA", "app")
        self.assertEqual(app_window.load_mode("DATA"), "app")

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            app_window.save_mode("DATA", "kiosk")

    def test_damaged_file_falls_back_to_default(self):
        for text in ("not json", "[]", '{"mode": "kiosk"}', ""):
            path = app_window.mode_file("DATA")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.assertEqual(app_window.load_mode("DATA"), "app", text)

    def test_browser_choice_opens_a_tab(self):
        app_window.save_mode("DATA", "browser")
        with mock.patch.object(app_window.webbrowser, "open") as opened, \
             mock.patch.object(app_window, "open_app_window") as app:
            result = app_window.open_dashboard("http://localhost:7777/", "DATA")
        opened.assert_called_once_with("http://localhost:7777/")
        app.assert_not_called()
        self.assertIn("browser", result)

    def test_app_choice_opens_the_app_window(self):
        with mock.patch.object(app_window, "open_app_window", return_value="app window") as app, \
             mock.patch.object(app_window.webbrowser, "open") as opened:
            app_window.open_dashboard("http://localhost:7777/", "DATA")
        app.assert_called_once_with("http://localhost:7777/", "DATA")
        opened.assert_not_called()

    def test_handler_reports_state(self):
        h = _FakeHandler({"Origin": None})
        with mock.patch.object(app_window, "find_browser", return_value=("chrome", "c")):
            app_window.handle_window_mode(h, "GET", b"", "DATA")
        self.assertEqual(h.replies, [(200, {"mode": "app", "app_available": True,
                                            "forced_browser": False})])

    def test_handler_saves_and_can_open_now(self):
        h, opener = _FakeHandler(), mock.Mock(return_value="browser")
        with mock.patch.object(app_window, "find_browser", return_value=None):
            app_window.handle_window_mode(h, "POST", b'{"mode": "browser", "open_now": true}',
                                          "DATA", opener)
        opener.assert_called_once_with("http://localhost:7777/", "DATA")
        code, reply = h.replies[0]
        self.assertEqual(code, 200)
        self.assertEqual((reply["mode"], reply["opened"], reply["app_available"]),
                         ("browser", "browser", False))
        self.assertEqual(app_window.load_mode("DATA"), "browser")

    def test_handler_saves_without_opening(self):
        h, opener = _FakeHandler(), mock.Mock()
        app_window.handle_window_mode(h, "POST", b'{"mode": "browser"}', "DATA", opener)
        opener.assert_not_called()
        self.assertEqual(h.replies[0][0], 200)

    def test_handler_rejects_bad_input_and_remote_callers(self):
        for body in (b"", b"nope", b"[]", b'{"mode": "kiosk"}'):
            h = _FakeHandler()
            app_window.handle_window_mode(h, "POST", body, "DATA")
            self.assertEqual(h.replies[0][0], 400, body)
        for method in ("GET", "POST"):
            h, opener = _FakeHandler(peer="100.64.0.7"), mock.Mock()
            app_window.handle_window_mode(h, method, b'{"mode": "browser", "open_now": true}',
                                          "DATA", opener)
            self.assertEqual(h.replies[0][0], 403)
            opener.assert_not_called()
        self.assertEqual(app_window.load_mode("DATA"), "app")


class DiscoveryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows discovery")
    def test_windows_finds_an_installed_browser_or_none(self):
        found = app_window.find_browser()
        if found is not None:
            label, path = found
            self.assertIn(label, ("chrome", "edge", "brave"))
            self.assertTrue(Path(path).is_file())


if __name__ == "__main__":
    unittest.main()
