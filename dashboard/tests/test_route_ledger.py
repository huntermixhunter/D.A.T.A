"""Task A4 of docs/SPEC_auto_router.md: routing fields in the usage ledger,
one row per turn, shadow mode, and the /router/log reader.

Runs the real _run_turn_with_failover wrapper with stub runners, so no CLI,
model, or network is touched and no usage is spent.

    cd dashboard
    python -m unittest tests.test_route_ledger -v
"""
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_BRIDGE_PATH = Path(__file__).resolve().parent.parent / "bridge_server.py"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_server_routeledger", str(_BRIDGE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bs = _load_bridge()


def simple_run_turn(provider_id, message, project_path, sse, *, runner_for, set_provider, **kw):
    prev = set_provider(provider_id)
    try:
        runner_for(provider_id)(message, project_path, sse)
    finally:
        set_provider(prev)
    return provider_id


def handoff_run_turn(provider_id, message, project_path, sse, *, runner_for, set_provider, **kw):
    """First provider runs and logs, then a backup finishes the turn."""
    prev = set_provider(provider_id)
    runner_for(provider_id)(message, project_path, sse)
    set_provider("codex")
    runner_for("codex")(message, project_path, sse)
    set_provider(prev)
    return "codex"


def logging_runner(tokens=1000, tools=()):
    def run(message, project_path, sse):
        for t in tools:
            bs._note_tool_use(t)
        meta = bs._empty_usage_meta(bs._current_provider_id())
        meta.update(input_tokens=tokens, output_tokens=50, cost_usd=0.01)
        bs._log_usage(meta, message)
        sse("meta", json.dumps(meta))
    return run


def silent_runner(message, project_path, sse):
    """Error path: sends a bare meta and never logs (as several runners do)."""
    sse("meta", json.dumps({"input_tokens": 0, "output_tokens": 0}))


class RouteLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.udir = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(bs, "_user_dir", return_value=self.udir),
            mock.patch.object(bs._failover, "run_turn", side_effect=simple_run_turn),
        ]
        for p in self.patches:
            p.start()
        bs._route_prev.clear()
        bs._router_settings_cache.update(key=None, data=None)
        bs._history_state.key = "test-pane"
        bs._request_attachments.list = []
        self.runner = logging_runner()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        bs._route_prev.clear()
        bs._request_attachments.list = []
        self.tmp.cleanup()

    def turn(self, message, pid="claude-cli", runner=None, **kw):
        r = runner or self.runner
        with mock.patch.object(bs, "_provider_runner", return_value=r):
            return bs._run_turn_with_failover(pid, message, "", lambda *a: None, **kw)

    def rows(self):
        path = self.udir / bs.USAGE_LEDGER_NAME
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    # ── one row per turn with routing fields ────────────────────────────────
    def test_manual_turn_row(self):
        self.turn("hi there, how are you?")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["route_mode"], "manual")
        self.assertIs(r["auto_chosen"], False)
        self.assertEqual(r["requested"], "claude-cli")
        self.assertEqual(r["chosen_provider"], "claude-cli")
        self.assertEqual(r["chosen_tier"], "top")
        self.assertEqual(r["shadow_tier"], "light")
        self.assertEqual(r["shadow_reason"], "cat.chat")
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(len(r["turn_id"]), 32)
        self.assertEqual(r["input_tokens"], 1000)
        self.assertIsNotNone(r["router_version"])
        self.assertFalse(any(k.startswith("_") for k in r))

    def test_no_prompt_text_in_row(self):
        secret = "my password is hunter2-unique-string"
        self.turn(secret)
        raw = (self.udir / bs.USAGE_LEDGER_NAME).read_text(encoding="utf-8")
        self.assertNotIn("hunter2", raw)
        self.assertEqual(self.rows()[0]["prompt_chars"], len(secret))
        self.assertEqual(self.rows()[0]["shadow_reason"], "lock.security")

    def test_silent_runner_still_gets_one_row(self):
        self.turn("explain how tides work", runner=silent_runner)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input_tokens"], 0)
        self.assertEqual(rows[0]["shadow_tier"], "mid")

    def test_runner_crash_still_logs(self):
        def boom(message, project_path, sse):
            raise RuntimeError("runner died")
        with self.assertRaises(RuntimeError):
            self.turn("hi", runner=boom)
        self.assertEqual(len(self.rows()), 1)
        self.assertIsNone(getattr(bs._route_ctx, "rec", None))

    def test_zero_token_rows_outside_a_turn_are_skipped(self):
        bs._log_usage(bs._empty_usage_meta("claude-cli"), "hi")
        self.assertEqual(self.rows(), [])

    def test_ollama_turn_writes_row(self):
        self.turn("hi", pid="ollama:qwen2.5:3b", runner=logging_runner(tokens=200))
        r = self.rows()[0]
        self.assertEqual(r["chosen_provider"], "ollama:qwen2.5:3b")
        self.assertEqual(r["chosen_tier"], "light")

    def test_voice_kind(self):
        self.turn("hi", kind="voice")
        self.assertEqual(self.rows()[0]["kind"], "voice")

    # ── tools ───────────────────────────────────────────────────────────────
    def test_write_tools_flag(self):
        self.turn("look at my notes", runner=logging_runner(tools=("Read", "Grep", "web_search")))
        self.assertIs(self.rows()[-1]["write_tools_used"], False)
        self.turn("tidy my notes", runner=logging_runner(tools=("Read", "Write")))
        self.assertIs(self.rows()[-1]["write_tools_used"], True)
        self.turn("run it", runner=logging_runner(tools=("codex:command_execution",)))
        self.assertIs(self.rows()[-1]["write_tools_used"], True)

    def test_note_tool_use_outside_turn_is_harmless(self):
        bs._note_tool_use("Write")

    # ── failover ────────────────────────────────────────────────────────────
    def test_failover_handoff_rows_share_turn_id(self):
        with mock.patch.object(bs._failover, "run_turn", side_effect=handoff_run_turn):
            self.turn("write a caption for my photo")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["turn_id"], rows[1]["turn_id"])
        self.assertIsNone(rows[0]["fallback"])
        self.assertEqual(rows[1]["chosen_provider"], "codex")
        self.assertEqual(rows[1]["fallback"], "failover.midturn")
        self.assertEqual(rows[1]["requested"], "claude-cli")

    # ── shadow mode ─────────────────────────────────────────────────────────
    def test_shadow_stickiness_within_pane(self):
        self.turn("help me make a plan for my week")
        self.turn("thanks!")
        rows = self.rows()
        self.assertEqual(rows[0]["shadow_tier"], "top")
        self.assertEqual(rows[1]["shadow_tier"], "top")
        self.assertEqual(rows[1]["shadow_reason"], "sticky")
        self.assertIs(rows[1]["cache_warm"], True)

    def test_shadow_not_sticky_across_panes(self):
        self.turn("help me make a plan for my week")
        bs._history_state.key = "other-pane"
        self.turn("thanks!")
        self.assertEqual(self.rows()[1]["shadow_tier"], "light")
        self.assertIs(self.rows()[1]["cache_warm"], False)

    def test_shadow_approval_inherits(self):
        self.turn("delete my old invoices")
        self.turn("yes")
        r = self.rows()[1]
        self.assertEqual((r["shadow_tier"], r["shadow_reason"], r["category"]),
                         ("top", "follow.approval", "action"))

    def test_shadow_sees_attachments(self):
        bs._request_attachments.list = [{"kind": "image", "name": "a.png"}]
        self.turn("what is in this picture")
        r = self.rows()[0]
        self.assertIn("vision", r["categories"])
        self.assertEqual(r["shadow_tier"], "mid")

    def test_shadow_uses_user_bias(self):
        (self.udir / bs.ROUTER_SETTINGS_NAME).write_text(json.dumps({"bias": "save"}), encoding="utf-8")
        self.turn("write a caption for my photo")
        r = self.rows()[0]
        self.assertEqual((r["bias"], r["shadow_tier"]), ("save", "light"))

    def test_broken_settings_file_uses_defaults(self):
        (self.udir / bs.ROUTER_SETTINGS_NAME).write_text("{not json", encoding="utf-8")
        self.turn("write a caption for my photo")
        self.assertEqual(self.rows()[0]["bias"], "balanced")

    def test_router_failure_never_breaks_the_turn(self):
        with mock.patch.object(bs._auto_router, "shadow", side_effect=ValueError("bad")):
            self.turn("hi")
        r = self.rows()[0]
        self.assertIsNone(r["shadow_tier"])
        self.assertEqual(r["route_mode"], "manual")

    # ── /router/log reader ──────────────────────────────────────────────────
    def test_router_log_newest_first_routing_fields_only(self):
        self.turn("hi")
        self.turn("delete my old invoices")
        # an old-style row without routing fields is skipped
        with open(self.udir / bs.USAGE_LEDGER_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "x", "input_tokens": 5}) + "\nnot json\n")
        rows = bs._router_log(10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["shadow_reason"], "lock.action")
        self.assertEqual(rows[1]["shadow_reason"], "cat.chat")
        self.assertNotIn("pane", rows[0])
        self.assertNotIn("prompt_chars", rows[0])
        self.assertEqual(len(bs._router_log(1)), 1)

    def test_router_log_missing_ledger(self):
        self.assertEqual(bs._router_log(5), [])

    def test_shadow_is_fast(self):
        t0 = time.perf_counter()
        for _ in range(20):
            self.turn("please explain how the stock market works " * 30)
        self.assertLess((time.perf_counter() - t0) / 20, 0.05)


if __name__ == "__main__":
    unittest.main()
