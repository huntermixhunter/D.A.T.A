"""
Tests for the in-chat console (cli_console.py) and its bridge wiring:

  * parse_command   — the allowlist: what runs, what is refused (incl. shell
                      injection attempts), and /login family mapping.
  * ConsoleSession  — a real piped child: prompt without a trailing newline
                      streams immediately, stdin input reaches the child, exit
                      code is reported, stop() ends it.
  * logged-out detection + login_notice text.
  * Handler._console_gate — loopback-only, tunnel/proxy headers refused,
                      foreign Origin refused, DNS-rebinding Host refused.
  * ask_hermes_cli_stream end to end with a fake Claude Code that emits the
    real logged-out stream: the reply is sign-in instructions + a needs_login
    event, not a raw error, and nothing is saved to history.

Standard-library unittest only (the retail runtime has no pytest):

    cd dashboard
    python -m unittest discover -s tests -t . -p "test_cli_console.py" -v
"""

import importlib.util
import json
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_DASH = _HERE.parent
if str(_DASH) not in sys.path:
    sys.path.insert(0, str(_DASH))

import cli_console as cc  # noqa: E402


def _load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_server_console_test",
                                                  str(_DASH / "bridge_server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─────────────────────────────────────────────────────────────────────────────
class ParseCommandTests(unittest.TestCase):

    def p(self, text, provider=""):
        return cc.parse_command(text, provider)

    def test_claude_login_variants(self):
        for t in ("claude", "Claude", "!claude", "claude login", "claude /login",
                  "claude auth login", "  !claude   login  "):
            s = self.p(t)
            self.assertIsNotNone(s, t)
            self.assertEqual(s["args"], ["auth", "login"], t)
            self.assertEqual(s["mode"], "pipe")
            self.assertEqual(s["action"], "login")

    def test_claude_login_flags(self):
        self.assertEqual(self.p("claude login --console")["args"], ["auth", "login", "--console"])
        self.assertEqual(self.p("claude auth login --sso")["args"], ["auth", "login", "--sso"])
        self.assertIsNone(self.p("claude status --console"))

    def test_claude_other(self):
        self.assertEqual(self.p("claude logout")["args"], ["auth", "logout"])
        self.assertEqual(self.p("claude status")["args"], ["auth", "status", "--text"])
        self.assertEqual(self.p("claude --version")["args"], ["--version"])
        self.assertEqual(self.p("claude terminal")["mode"], "terminal")

    def test_codex(self):
        self.assertEqual(self.p("codex")["args"], ["login"])
        self.assertEqual(self.p("codex login --device-auth")["args"], ["login", "--device-auth"])
        self.assertEqual(self.p("codex status")["args"], ["login", "status"])
        self.assertEqual(self.p("codex logout")["args"], ["logout"])

    def test_gemini_login_is_terminal(self):
        for t in ("gemini", "gemini login", "gemini /auth"):
            s = self.p(t)
            self.assertEqual(s["mode"], "terminal", t)
            self.assertEqual(s["args"], [], t)
        self.assertEqual(self.p("gemini --version")["mode"], "pipe")

    def test_slash_login_maps_to_pane_family(self):
        self.assertEqual(self.p("/login", "codex-luna")["family"], "codex")
        self.assertEqual(self.p("/login", "claude-cli-sonnet")["family"], "claude")
        self.assertEqual(self.p("/login", "gemini")["mode"], "terminal")
        self.assertEqual(self.p("/login", "ollama")["family"], "claude")  # sensible default
        self.assertEqual(self.p("/status", "codex")["args"], ["login", "status"])

    def test_install(self):
        s = self.p("install codex")
        self.assertEqual((s["kind"], s["args"]), ("npm", ["install", "-g", "@openai/codex"]))
        s = self.p("npm i -g @anthropic-ai/claude-code@latest")
        self.assertEqual(s["args"], ["install", "-g", "@anthropic-ai/claude-code"])
        self.assertIsNone(self.p("npm i -g left-pad"))
        self.assertIsNone(self.p("npm i -g @openai/codex --foo"))
        self.assertIsNone(self.p("install everything"))

    def test_help(self):
        self.assertEqual(self.p("!help")["kind"], "help")
        self.assertIn("claude login", cc.help_text())

    def test_rejects_everything_else(self):
        for t in ("", "   ", "hello there", "claude, what is the weather?",
                  "claude login; calc.exe", "claude login && del /q C:\\",
                  "claude login | powershell", "claude $(whoami)", "codex exec rm -rf /",
                  "claude --dangerously-skip-permissions -p hi", "!dir", "!powershell",
                  "gemini -p 'do stuff'", "claude login\nclaude logout", "x" * 200,
                  'claude "unterminated'):
            self.assertIsNone(self.p(t), repr(t))

    def test_no_user_text_reaches_argv(self):
        # Whatever the text, argv[1:] always comes from the fixed table.
        resolve = lambda pid: f"/bin/{pid}"
        argv, err = cc.build_argv(self.p("claude login --sso"), resolve)
        self.assertEqual(err, "")
        self.assertEqual(argv, ["/bin/claude-cli", "auth", "login", "--sso"])

    def test_build_argv_missing_cli(self):
        argv, err = cc.build_argv(self.p("codex login"), lambda pid: "")
        self.assertEqual(argv, [])
        self.assertIn("install codex", err)


# ─────────────────────────────────────────────────────────────────────────────
class DetectionTests(unittest.TestCase):

    def test_claude_logged_out(self):
        self.assertTrue(cc.looks_logged_out("claude", "Not logged in · Please run /login"))
        self.assertTrue(cc.looks_logged_out("claude", "OAuth token has expired"))
        self.assertFalse(cc.looks_logged_out("claude", "Rate limit reached"))

    def test_codex_logged_out(self):
        self.assertTrue(cc.looks_logged_out("codex", "unexpected status 401 Unauthorized"))
        self.assertTrue(cc.looks_logged_out("codex", "Not logged in. Run `codex login`."))
        self.assertFalse(cc.looks_logged_out("codex", "model overloaded"))

    def test_gemini_logged_out(self):
        self.assertTrue(cc.looks_logged_out(
            "gemini", "Please set an Auth method in your settings.json or specify GEMINI_API_KEY"))
        self.assertFalse(cc.looks_logged_out("gemini", "quota exceeded"))

    def test_login_notice(self):
        n = cc.login_notice("claude")
        self.assertEqual(n["command"], "claude login")
        self.assertIn("claude login", n["text"])
        self.assertFalse(n["terminal_only"])
        self.assertTrue(cc.login_notice("gemini")["terminal_only"])

    def test_clean_output(self):
        self.assertEqual(cc.clean_output("\x1b[32mok\x1b[0m"), "ok")
        self.assertEqual(cc.clean_output("50%\r100%\n"), "100%\n")
        self.assertEqual(cc.clean_output("a\r\nb"), "a\nb")


# ─────────────────────────────────────────────────────────────────────────────
class SessionTests(unittest.TestCase):
    """Real subprocess: a Python child standing in for `claude auth login`."""

    CHILD = textwrap.dedent("""
        import sys
        sys.stdout.write("Visit: https://example.com/auth?x=1\\nPaste code here if prompted > ")
        sys.stdout.flush()
        code = sys.stdin.readline().strip()
        print("got:" + code)
        sys.exit(3 if code == "bad" else 0)
    """)

    def _run(self, answer):
        spec = {"kind": "cli", "family": "claude", "args": [], "mode": "pipe",
                "action": "login", "label": "fake"}
        sess = cc.ConsoleSession(spec, [sys.executable, "-c", self.CHILD])
        sess.start()
        seen = ""
        sent = False
        code = "unset"
        deadline = time.time() + 30
        for kind, val in sess.events(keepalive=0.5):
            if time.time() > deadline:
                sess.stop()
                self.fail("session did not finish")
            if kind == "out":
                seen += val
                # The prompt has NO trailing newline — it must still arrive
                # before we send anything.
                if not sent and "prompted >" in seen:
                    self.assertTrue(sess.send_input(answer))
                    sent = True
            elif kind == "exit":
                code = val
                break
        return seen, code, sess

    def test_prompt_input_exit(self):
        seen, code, sess = self._run("ABC123")
        self.assertIn("https://example.com/auth?x=1", seen)
        self.assertIn("got:ABC123", seen)
        self.assertEqual(code, 0)
        self.assertFalse(sess.running)
        self.assertFalse(sess.send_input("late"))

    def test_nonzero_exit(self):
        _, code, _ = self._run("bad")
        self.assertEqual(code, 3)

    def test_stop(self):
        spec = {"kind": "cli", "family": "claude", "args": [], "mode": "pipe",
                "action": "login", "label": "sleeper"}
        reg = cc.SessionRegistry()
        sess = reg.start(spec, [sys.executable, "-c", "import time; time.sleep(60)"], pane_id="p1")
        self.assertIs(reg.get(sess.id), sess)
        # Starting another command in the same pane stops the first.
        sess2 = reg.start(spec, [sys.executable, "-c", "import time; time.sleep(60)"], pane_id="p1")
        for kind, _ in sess.events(keepalive=0.5):
            if kind == "exit":
                break
        self.assertFalse(sess.running)
        self.assertTrue(sess.stopped)
        sess2.stop()
        for kind, _ in sess2.events(keepalive=0.5):
            if kind == "exit":
                break
        self.assertFalse(sess2.running)


# ─────────────────────────────────────────────────────────────────────────────
bs = _load_bridge()


class _FakeHandler:
    """Just enough of BaseHTTPRequestHandler for _console_gate."""
    _CONSOLE_PROXY_HEADERS = bs.Handler._CONSOLE_PROXY_HEADERS
    _console_gate = bs.Handler._console_gate

    def __init__(self, peer="127.0.0.1", headers=None):
        self.client_address = (peer, 50000)
        h = {"Host": f"localhost:{bs.PORT}", "Origin": f"http://localhost:{bs.PORT}"}
        h.update(headers or {})
        self.headers = {k: v for k, v in h.items() if v is not None}


class GateTests(unittest.TestCase):

    def test_local_dashboard_allowed(self):
        self.assertEqual(_FakeHandler()._console_gate(), "")
        self.assertEqual(_FakeHandler(headers={"Origin": None})._console_gate(), "")
        self.assertEqual(_FakeHandler(peer="::1", headers={
            "Host": f"127.0.0.1:{bs.PORT}", "Origin": f"http://127.0.0.1:{bs.PORT}"})._console_gate(), "")

    def test_lan_peer_refused(self):
        self.assertNotEqual(_FakeHandler(peer="192.168.1.20")._console_gate(), "")

    def test_tunnel_refused(self):
        # cloudflared connects from loopback; its headers give it away.
        for hdr in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For", "Forwarded"):
            self.assertNotEqual(_FakeHandler(headers={hdr: "1.2.3.4"})._console_gate(), "", hdr)

    def test_foreign_origin_refused(self):
        for origin in ("https://evil.example", "null", "http://localhost:9999"):
            self.assertNotEqual(_FakeHandler(headers={"Origin": origin})._console_gate(), "", origin)

    def test_dns_rebinding_host_refused(self):
        self.assertNotEqual(_FakeHandler(headers={"Host": f"evil.example:{bs.PORT}",
                                                  "Origin": None})._console_gate(), "")
        self.assertNotEqual(_FakeHandler(headers={"Host": "data.mytunnel.com",
                                                  "Origin": None})._console_gate(), "")


# ─────────────────────────────────────────────────────────────────────────────
_FAKE_LOGGED_OUT_CLAUDE = textwrap.dedent(r'''
    import sys, json
    sys.stdin.read()
    print(json.dumps({"type": "system", "subtype": "init"}))
    print(json.dumps({"type": "assistant", "error": "authentication_failed",
                      "message": {"stop_reason": "stop_sequence",
                                  "content": [{"type": "text", "text": "Not logged in \u00b7 Please run /login"}]}}))
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                      "result": "Not logged in \u00b7 Please run /login"}))
''')


class LoggedOutChatTests(unittest.TestCase):

    def setUp(self):
        self._saved_pending = bs._PENDING_CLI_AUTH
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = Path(self._tmp.name) / "fake_claude.py"
        self.fake.write_text(_FAKE_LOGGED_OUT_CLAUDE, encoding="utf-8")

    def tearDown(self):
        bs._PENDING_CLI_AUTH = self._saved_pending
        self._tmp.cleanup()

    def test_logged_out_turn_becomes_sign_in_card(self):
        real_popen = bs.subprocess.Popen
        fake = str(self.fake)

        def fake_popen(argv, *a, **kw):
            # Swap the claude exe for our fake, keep every other Popen kwarg.
            return real_popen([sys.executable, fake], *a, **kw)

        events = []
        history = []
        with mock.patch.object(bs, "_provider_executable", return_value="C:/fake/claude.exe"), \
             mock.patch.object(bs, "_current_provider_id", return_value="claude-cli"), \
             mock.patch.object(bs.subprocess, "Popen", side_effect=fake_popen), \
             mock.patch.object(bs, "conversation_history", history), \
             mock.patch.object(bs, "_save_history") as save, \
             mock.patch.object(bs, "auto_recall_hint", return_value=""), \
             mock.patch.object(bs, "resume_digest_hint", return_value=""), \
             mock.patch.object(bs, "_data_notification_hint", return_value=""), \
             mock.patch.object(bs, "_load_soul_cli", return_value="soul"):
            bs.ask_hermes_cli_stream("hello", "", lambda t, x: events.append((t, x)))

        tokens = "".join(x for t, x in events if t == "token")
        self.assertIn("isn't signed in", tokens)
        self.assertIn("claude login", tokens)
        self.assertNotIn("Error from Claude Code", tokens)
        login = [json.loads(x) for t, x in events if t == "needs_login"]
        self.assertEqual(len(login), 1)
        self.assertEqual(login[0]["family"], "claude")
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(history, [])          # signed-out turn not remembered
        save.assert_not_called()
        self.assertEqual(bs._PENDING_CLI_AUTH, "claude-cli")   # banner will show


if __name__ == "__main__":
    unittest.main()
