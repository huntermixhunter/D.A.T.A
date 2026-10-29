"""Remote access, HTTP boundaries and Telegram tests with no external services."""
import ast
import http.client
import io
import json
import logging
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remote_access as ra
import telegram_bot as tg

TOKEN = "123456789:" + "a" * 35
HOST = "desktop.example.ts.net:8443"


class FakeTailscale:
    def __init__(self):
        self.serve = None  # A fresh Tailscale install has no Serve configuration.
        self.state = "Running"
        self.calls = []

    def run(self, args):
        self.calls.append(args)
        if args == ["status", "--json"]:
            return json.dumps({"BackendState": self.state, "Self": {"DNSName": "desktop.example.ts.net."}})
        if args == ["serve", "status", "--json"]:
            return json.dumps(self.serve)
        if args == ["serve", "--bg", "--https=8443", "off"]:
            self.serve = None
            return ""
        if args[:2] == ["serve", "--bg"]:
            self.serve = {"TCP": {"8443": {"HTTPS": True}},
                          "Web": {HOST: {"Handlers": {"/": {"Proxy": args[-1]}}}}}
            return "Available within your tailnet"
        raise AssertionError(args)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = "owner"
        self.reply = Mock(return_value="A reply")
        self.manager = ra.RemoteAccess(self.temp.name, profile=lambda: self.profile, reply=self.reply)
        self.tailscale = FakeTailscale()
        self.manager._run = self.tailscale.run
        mock_binary = patch.object(ra, "_tailscale_binary", return_value="tailscale")
        mock_binary.start()
        self.addCleanup(mock_binary.stop)

    def connect(self):
        with patch.object(tg, "api", side_effect=[{"is_bot": True, "username": "data_test_bot"}, {"url": ""}]), patch.object(tg.TelegramBot, "start"):
            result = self.manager.action("telegram/connect", {"token": TOKEN, "confirmed": True})
        return self.manager.bot, result

    def update(self, text, who=123, kind="private", date=None, update_id=None):
        offset = self.manager.config["telegram"].get("offset", 0)
        return {"update_id": offset if update_id is None else update_id,
                "message": {"date": time.time() if date is None else date,
                            "chat": {"id": who, "type": kind},
                            "from": {"id": who, "first_name": "Owner", "is_bot": False}, "text": text}}

    def pair(self):
        bot, result = self.connect()
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(result["pair_url"]).query)["start"][0]
        with patch.object(tg, "api"):
            bot.process_update(bot._config(), self.update("/start " + code))
        return bot

    def test_import_and_status_do_not_enable_anything(self):
        self.assertEqual(self.manager.status()["mobile"]["state"], "ready")
        self.assertFalse(Path(self.temp.name, "config.json").exists())
        self.assertFalse(self.manager.keep_running)
        self.assertTrue(all(args[-1] == "--json" for args in self.tailscale.calls))

    def test_missing_and_signed_out_guidance(self):
        with patch.object(ra, "_tailscale_binary", return_value=""):
            self.assertEqual(self.manager.mobile_status()["state"], "missing")
        self.tailscale.state = "NeedsLogin"
        with self.assertRaises(ra.SetupError):
            self.manager.enable_mobile()
        self.assertFalse(self.manager.remote_key())

    def test_tailscale_json_ignores_diagnostic_stderr(self):
        result = SimpleNamespace(returncode=0, stdout='{"BackendState": "Running"}',
                                 stderr="Warning: client and server versions differ")
        with patch.object(ra.subprocess, "run", return_value=result):
            output = ra.RemoteAccess._run(self.manager, ["status", "--json"])
        self.assertEqual(json.loads(output)["BackendState"], "Running")

    def test_null_node_status_is_rejected_but_empty_serve_is_ready(self):
        self.assertEqual(self.manager.mobile_status()["state"], "ready")
        self.manager._run = Mock(return_value='{"BackendState": "Running", "Self": null}')
        self.assertEqual(self.manager.mobile_status()["state"], "error")

    def test_enable_verifies_proxy_and_persists_auth_and_lifecycle(self):
        self.manager.action("mobile/enable", {"confirmed": True})
        key = self.manager.remote_key()
        self.assertGreaterEqual(len(key), 40)
        self.assertIn(["serve", "--bg", "--https=8443", "http://127.0.0.1:7777"], self.tailscale.calls)
        restored = ra.RemoteAccess(self.temp.name)
        self.assertEqual(restored.remote_key(), key)
        self.assertTrue(restored.keep_running)
        self.assertEqual(self.manager.mobile_status()["state"], "enabled")

    def test_mobile_activation_requires_an_explicit_click(self):
        with self.assertRaises(ra.SetupError):
            self.manager.action("mobile/enable", {})
        self.assertFalse(self.tailscale.calls)

    def test_phone_secret_is_a_fragment_and_not_in_status(self):
        self.manager.enable_mobile()
        link = self.manager.phone_link()["link"]
        self.assertEqual(urllib.parse.urlsplit(link).query, "")
        self.assertIn("access=", urllib.parse.urlsplit(link).fragment)
        self.assertNotIn(self.manager.remote_key(), json.dumps(self.manager.status()))

    def test_existing_services_and_public_funnel_are_not_overwritten(self):
        for serve in ({"TCP": {"8443": {"TCPForward": "other:80"}}},
                      {"TCP": {"8443": {"HTTPS": True}}, "Web": {HOST: {"Handlers": {"/": {"Proxy": self.manager._target()}}}}, "AllowFunnel": {HOST: True}}):
            self.tailscale.serve = serve
            with self.subTest(serve=serve), self.assertRaises(ra.SetupError):
                self.manager.enable_mobile()
            self.assertFalse(any("--bg" in args for args in self.tailscale.calls))

    def test_disable_revokes_key_even_when_tailscale_is_offline(self):
        self.manager.enable_mobile()
        key = self.manager.remote_key()
        self.manager._run = Mock(side_effect=ra.SetupError("offline"))
        result = self.manager.disable_mobile()
        self.assertFalse(self.manager.remote_key())
        self.assertNotEqual(self.manager.config["access_key"], key)
        self.assertIn("could not be reached", result["message"])

    def test_disable_preserves_another_apps_proxy(self):
        self.manager.enable_mobile()
        self.tailscale.serve["Web"][HOST]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:9000"
        self.manager.disable_mobile()
        self.assertNotIn(["serve", "--bg", "--https=8443", "off"], self.tailscale.calls)

    def test_disable_removes_only_the_owned_proxy(self):
        self.manager.enable_mobile()
        result = self.manager.disable_mobile()
        self.assertIn(["serve", "--bg", "--https=8443", "off"], self.tailscale.calls)
        self.assertIsNone(self.tailscale.serve)
        self.assertFalse(self.manager.remote_key())
        self.assertIn("disabled", result["message"])

    def test_known_tailscale_approval_urls_only(self):
        self.assertEqual(ra._approval_url("https://login.tailscale.com.evil.test/f/serve"), "")
        self.assertEqual(ra._approval_url("approve https://login.tailscale.com/f/serve?node=abc"),
                         "https://login.tailscale.com/f/serve?node=abc")

    def test_corrupt_config_fails_closed(self):
        Path(self.temp.name, "config.json").write_text("broken", encoding="utf-8")
        with self.assertRaises(ra.SetupError):
            ra.RemoteAccess(self.temp.name)

    def test_lifecycle_choice_persists_and_daemon_override_is_reported(self):
        self.manager.action("keep-running", {"enabled": True})
        self.assertTrue(ra.RemoteAccess(self.temp.name).keep_running)
        self.manager.action("keep-running", {"enabled": False})
        self.assertFalse(ra.RemoteAccess(self.temp.name).keep_running)
        self.assertTrue(ra.RemoteAccess(self.temp.name, daemon=True).keep_running)
        with self.assertRaises(ra.SetupError):
            self.manager.action("keep-running", {"enabled": "false"})

    def test_invalid_token_makes_no_network_request(self):
        with patch.object(tg, "api") as request, self.assertRaises(ra.SetupError):
            self.manager.action("telegram/connect", {"token": "not-a-token", "confirmed": True})
        request.assert_not_called()

    def test_existing_telegram_webhook_is_preserved(self):
        with patch.object(tg, "api", side_effect=[{"is_bot": True, "username": "data_test_bot"}, {"url": "https://other.test"}]) as request:
            with self.assertRaises(ra.SetupError):
                self.manager.action("telegram/connect", {"token": TOKEN, "confirmed": True})
        self.assertEqual([call.args[1] for call in request.call_args_list], ["getMe", "getWebhookInfo"])
        self.assertFalse(self.manager.config["telegram"])

    def test_tokens_never_appear_in_setup_status(self):
        self.connect()
        status = json.dumps(self.manager.status())
        self.assertNotIn(TOKEN, status)
        self.assertNotIn(self.manager.bot.pair_code, status)

    def test_pairing_rejects_groups_wrong_codes_and_expired_codes(self):
        bot, _ = self.connect()
        with patch.object(tg, "api") as request:
            bot.process_update(bot._config(), self.update("/start wrong"))
            bot.process_update(bot._config(), self.update("/start " + bot.pair_code, kind="group"))
            bot.pair_until = time.time() - 1
            bot.process_update(bot._config(), self.update("/start " + bot.pair_code))
        self.assertFalse(bot._config().get("chat_id"))
        self.reply.assert_not_called()
        request.assert_not_called()

    def test_pairing_is_one_use_and_only_the_paired_owner_can_dispatch(self):
        bot = self.pair()
        self.assertEqual(bot.pair_code, "")
        self.assertEqual(bot._config()["chat_id"], 123)
        with patch.object(tg, "api"):
            bot.process_update(bot._config(), self.update("stranger", who=999))
            bot.process_update(bot._config(), self.update("group", kind="group"))
            self.reply.assert_not_called()
            bot.process_update(bot._config(), self.update("hello"))
        self.reply.assert_called_once_with("hello", "owner", "123")
        with self.assertRaises(ra.SetupError):
            bot._pairing()

    def test_duplicate_and_stale_messages_cannot_repeat_commands(self):
        bot = self.pair()
        with patch.object(tg, "api"):
            update = self.update("hello")
            bot.process_update(bot._config(), update)
            bot.process_update(bot._config(), update)
            bot.process_update(bot._config(), self.update("old", date=time.time() - 300))
        self.assertEqual(self.reply.call_count, 1)

    def test_other_profile_pauses_telegram_without_switching_it(self):
        bot = self.pair()
        self.profile = "another-profile"
        with patch.object(tg, "api") as request:
            bot.process_update(bot._config(), self.update("hello"))
        self.reply.assert_not_called()
        self.assertIn("paused", request.call_args.args[2]["text"])
        self.assertEqual(self.profile, "another-profile")

    def test_notifications_require_the_originating_and_active_profile(self):
        bot = self.pair()
        with patch.object(tg, "_default_bot", bot), patch.object(tg, "api") as request:
            self.assertFalse(tg.notify("another profile's result", source_profile="another-profile"))
            request.assert_not_called()
            self.profile = "another-profile"
            self.assertFalse(tg.notify("owner's result", source_profile="owner"))
            request.assert_not_called()
            self.profile = "owner"
            self.assertTrue(tg.notify("owner's result", source_profile="owner"))
            self.assertEqual(request.call_args.args[2]["chat_id"], 123)
            self.assertEqual(request.call_args.args[2]["text"], "owner's result")

    def test_help_status_and_connection_test_do_not_call_ai(self):
        bot = self.pair()
        with patch.object(tg, "api") as request:
            for text in ("/help", "/status", "/start", "/unknown"):
                bot.process_update(bot._config(), self.update(text))
            bot.action("test", {})
        self.reply.assert_not_called()
        self.assertEqual(request.call_count, 5)

    def test_disconnect_removes_token_and_blocks_further_requests(self):
        bot = self.pair()
        before = bot._config()
        bot.action("disconnect", {})
        with patch.object(tg, "api") as request:
            bot.process_update(before, self.update("hello"))
        self.reply.assert_not_called()
        request.assert_not_called()
        self.assertNotIn(TOKEN, self.manager.path.read_text())

    def test_outgoing_emoji_chunks_fit_telegram_limit(self):
        bot = self.pair()
        with patch.object(tg, "api") as request:
            bot.send(bot._config(), "\U0001f600" * 5000)
        chunks = [call.args[2]["text"] for call in request.call_args_list]
        self.assertEqual("".join(chunks), "\U0001f600" * 5000)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks))

    def test_api_error_never_echoes_the_token(self):
        error = urllib.error.HTTPError("https://api.telegram.org/bot" + TOKEN, 401, TOKEN, {}, io.BytesIO())
        with patch.object(tg.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(ra.SetupError) as caught:
                tg.api(TOKEN, "getMe")
        self.assertNotIn(TOKEN, str(caught.exception))


class HttpTests(SetupTests):
    # Run the production Handler without importing the bridge's startup state.
    # Only this class's HTTP tests are collected below (base tests have their own run).
    def setUp(self):
        super().setUp()
        source = Path(ra.__file__).with_name("bridge_server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        handler_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Handler")
        self.ns = {"BaseHTTPRequestHandler": BaseHTTPRequestHandler, "remote_access": ra,
                   "REMOTE_ACCESS": self.manager, "DATA_BRIDGE_TOKEN": "", "Path": Path,
                   "urllib": urllib, "json": json, "log": logging.getLogger("remote-test"),
                   "marketplace": SimpleNamespace(handle_http=lambda _h: False),
                   "advanced_tools": SimpleNamespace(handle_http=lambda *_args: False),
                   "_user_dir": lambda: Path(self.temp.name), "_detect_hardware": Mock(),
                   "skill_search": SimpleNamespace(handle_http=lambda _h: False),
                   "__file__": str(Path(self.temp.name, "bridge_server.py"))}
        exec(compile(ast.Module(body=[handler_class], type_ignores=[]), "bridge_handler_test", "exec"), self.ns)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.ns["Handler"])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path="/remote-access/status", data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        sent = {"X-Data-Request": "1", "Content-Type": "application/json", **(headers or {})}
        body = json.dumps(data).encode() if data is not None else None
        connection.request("POST" if data is not None else "GET", path, body, sent)
        response = connection.getresponse()
        raw = response.read()
        result = response.status, json.loads(raw) if raw else {}, dict(response.getheaders())
        connection.close()
        return result

    def test_http_status_and_secrets_are_local_only_and_uncached(self):
        self.manager.enable_mobile()
        code, state, headers = self.call()
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn(self.manager.remote_key(), json.dumps(state))
        for sent in ({"Host": HOST}, {"X-Forwarded-For": "100.100.1.2"},
                     {"Origin": "https://evil.example"}, {"X-Data-Request": ""},
                     {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=sent):
                self.assertEqual(self.call(headers=sent)[0], 403)
                self.assertEqual(self.call("/remote-access/mobile/link", {}, sent)[0], 403)

    def test_http_remote_api_requires_key_and_revokes_after_disable(self):
        remote = {"Host": HOST, "X-Forwarded-For": "100.100.1.2"}
        self.assertEqual(self.call("/tunnel_url", headers=remote)[0], 401)
        self.manager.enable_mobile()
        remote["X-Data-Token"] = self.manager.remote_key()
        self.assertEqual(self.call("/tunnel_url", headers=remote)[0], 200)
        self.manager.disable_mobile()
        self.assertEqual(self.call("/tunnel_url", headers=remote)[0], 401)

    def test_http_explicit_bridge_token_still_protects_local_requests(self):
        self.ns["DATA_BRIDGE_TOKEN"] = "explicit-key"
        self.assertEqual(self.call()[0], 401)
        self.assertEqual(self.call(headers={"X-Data-Token": "explicit-key"})[0], 200)

    def test_http_phone_link_is_revocable_when_an_environment_token_exists(self):
        self.ns["DATA_BRIDGE_TOKEN"] = self.manager.env_token = "explicit-key"
        self.manager.enable_mobile()
        link = self.manager.phone_link()["link"]
        key = urllib.parse.parse_qs(urllib.parse.urlsplit(link).fragment)["access"][0]
        self.assertNotEqual(key, "explicit-key")
        remote = {"Host": HOST, "X-Forwarded-For": "100.100.1.2", "X-Data-Token": key}
        self.assertEqual(self.call("/tunnel_url", headers=remote)[0], 200)
        self.manager.disable_mobile()
        self.assertEqual(self.call("/tunnel_url", headers=remote)[0], 401)
        remote["X-Data-Token"] = "explicit-key"
        self.assertEqual(self.call("/tunnel_url", headers=remote)[0], 200)

    def test_http_invalid_bodies_and_cross_origin_changes_are_rejected(self):
        self.assertEqual(self.call("/remote-access/keep-running", [], {})[0], 400)
        self.assertEqual(self.call("/remote-access/keep-running", {"enabled": True}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("/remote-access/keep-running", {"enabled": True}, {"Origin": "https://evil.example"})[0], 403)
        self.assertFalse(self.manager.keep_running)
        self.assertEqual(self.call("/remote-access/keep-running", {"enabled": True})[0], 200)
        self.assertTrue(self.manager.keep_running)


# Avoid rerunning the base unit tests with an HTTP server for every case.
for _name in list(vars(SetupTests)):
    if _name.startswith("test_"):
        setattr(HttpTests, _name, None)


class ProfileIsolationTests(unittest.TestCase):
    def standing_order_scope(self, dispatch):
        tree = ast.parse(Path(ra.__file__).with_name("bridge_server.py").read_text(encoding="utf-8"))
        names = {"_fire_standing_order", "_switch_active_user"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        ns = {"_profile_dispatch_lock": threading.RLock(), "_ACTIVE_USER": "owner",
              "_dispatch_with_provider": dispatch, "nullcontext": nullcontext,
              "log": Mock(), "time": time, "_push_ui_event": Mock(),
              "_switch_active_user_unlocked": Mock(return_value={"id": "other"})}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "standing_order_test", "exec"), ns)
        return ns

    def test_only_notifying_standing_orders_block_switches_during_dispatch(self):
        for enabled in (True, False):
            with self.subTest(notify_telegram=enabled):
                started, finish = threading.Event(), threading.Event()
                def dispatch(*_args, **_kwargs):
                    started.set()
                    finish.wait(2)
                    return "private result"
                ns = self.standing_order_scope(dispatch)
                order = {"id": "test", "name": "Test", "provider": "test", "prompt": "hi",
                         "notify_telegram": enabled}
                # Exercise a switch after dispatch, before the delivery check.
                ns["_push_ui_event"].side_effect = lambda *_args: ns.update(_ACTIVE_USER="other")
                with patch.object(tg, "notify") as notify:
                    worker = threading.Thread(target=ns["_fire_standing_order"], args=(order,))
                    worker.start()
                    try:
                        self.assertTrue(started.wait(1))
                        if enabled:
                            with self.assertRaises(ValueError):
                                ns["_switch_active_user"]("other")
                            ns["_switch_active_user_unlocked"].assert_not_called()
                        else:
                            ns["_switch_active_user"]("other")
                            ns["_switch_active_user_unlocked"].assert_called_once_with("other")
                    finally:
                        finish.set()
                        worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(order["last_result"], "private result")
                    if enabled:
                        self.assertEqual(notify.call_args.kwargs, {"source_profile": "owner"})
                        self.assertIn("private result", notify.call_args.args[0])
                        ns["_switch_active_user"]("other")
                        ns["_switch_active_user_unlocked"].assert_called_once_with("other")
                    else:
                        notify.assert_not_called()

    def test_notifying_order_rejects_a_switch_before_dispatch_lock(self):
        dispatch = Mock(return_value="private result")
        ns = self.standing_order_scope(dispatch)
        class SwitchingLock:
            def __enter__(self):
                ns["_ACTIVE_USER"] = "other"
            def __exit__(self, *_args):
                return False
        ns["_profile_dispatch_lock"] = SwitchingLock()
        order = {"id": "test", "name": "Test", "provider": "test", "prompt": "hi",
                 "notify_telegram": True}
        with patch.object(tg, "notify") as notify:
            ns["_fire_standing_order"](order)
        dispatch.assert_not_called()
        self.assertIn("profile changed", order["last_result"])
        self.assertEqual(notify.call_args.kwargs, {"source_profile": "owner"})

    def test_profile_switch_cannot_cross_an_inflight_telegram_dispatch(self):
        tree = ast.parse(Path(ra.__file__).with_name("bridge_server.py").read_text(encoding="utf-8"))
        names = {"_telegram_reply", "_switch_active_user"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        started, finish = threading.Event(), threading.Event()
        def dispatch(*_args):
            started.set()
            finish.wait(2)
            return "private reply"
        switch = Mock(return_value={"id": "other"})
        ns = {"_profile_dispatch_lock": threading.RLock(), "_ACTIVE_USER": "owner",
              "ACTIVE_PROVIDER": "test", "_dispatch_with_provider": dispatch,
              "_switch_active_user_unlocked": switch}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "profile_test", "exec"), ns)
        worker = threading.Thread(target=ns["_telegram_reply"], args=("hi", "owner", "123"))
        worker.start()
        self.assertTrue(started.wait(1))
        try:
            with self.assertRaises(ValueError):
                ns["_switch_active_user"]("other")
            switch.assert_not_called()
        finally:
            finish.set()
            worker.join(2)
        ns["_switch_active_user"]("other")
        switch.assert_called_once_with("other")


if __name__ == "__main__":
    unittest.main()
