"""Tasks A3, A5, A6 and A7 of docs/SPEC_auto_router.md: which models Auto may
use, the settings store, read-only enforcement below the top tier, and the
Auto dispatch hook.

Runs the real _run_turn_with_failover wrapper with stub runners, so no CLI,
model, or network is touched and no usage is spent.

    cd dashboard
    python -m unittest tests.test_auto_dispatch -v
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_BRIDGE_PATH = Path(__file__).resolve().parent.parent / "bridge_server.py"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_server_autodispatch", str(_BRIDGE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bs = _load_bridge()
ar = bs._auto_router
_REAL_TRIAGE = bs._router_triage


def simple_run_turn(provider_id, message, project_path, sse, *, runner_for, set_provider, **kw):
    prev = set_provider(provider_id)
    try:
        runner_for(provider_id)(message, project_path, sse)
    finally:
        set_provider(prev)
    return provider_id


def _snapshot(claude_blocked=None, claude_pct=40):
    return {
        "claude": {"windows": [{"id": "five_hour", "pct": claude_pct, "live": True}],
                   "blocked_reason": claude_blocked, "signed_in": True},
        "codex": {"windows": [{"id": "primary", "pct": 10, "live": True}],
                  "blocked_reason": None, "signed_in": True},
    }


class _Base(unittest.TestCase):
    snapshot = staticmethod(_snapshot)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.udir = Path(self.tmp.name)
        self.snap = _snapshot()
        self.patches = [
            mock.patch.object(bs, "_user_dir", return_value=self.udir),
            mock.patch.object(bs._failover, "run_turn", side_effect=simple_run_turn),
            mock.patch.object(bs._failover, "snapshot", side_effect=lambda *a, **k: self.snap),
            mock.patch.object(bs._failover, "limited_until", return_value=None),
            mock.patch.object(bs, "_provider_available", return_value=True),
            mock.patch.object(bs, "_codex_auth_mode", return_value="chatgpt"),
            mock.patch.object(bs, "_ollama_installed_models",
                              return_value={p.get("model") for p in bs.PROVIDERS.values()}),
            mock.patch.object(bs, "ACTIVE_PROVIDER", "claude-cli"),
            mock.patch.object(bs, "ACTIVE_EFFORT", ""),
            # Never reach a real model from the shared setup. TriageTest
            # exercises the real helper with stubbed backends.
            mock.patch.object(bs, "_router_triage", return_value=None),
            # The history and archive paths are fixed at import, so point them
            # at the temp folder: no test may touch the real chat files.
            mock.patch.object(bs, "HISTORY_FILE", self.udir / "conversation_history.json"),
            mock.patch.object(bs, "CONVERSATION_ARCHIVE_FILE", self.udir / "conversation_archive.jsonl"),
        ]
        for p in self.patches:
            p.start()
        bs._route_prev.clear()
        bs._router_settings_cache.update(key=None, data=None)
        bs._history_state.key = "test-pane"
        self._saved_history = getattr(bs._history_state, "history", None)
        bs._history_state.history = []   # each test starts with an empty pane
        bs._request_attachments.list = []
        bs._provider_override.id = None
        bs._effort_override.level = None
        self.seen = []
        self.replies = []          # scripted replies; None sends no text

    def tearDown(self):
        bs._history_state.history = self._saved_history
        for p in self.patches:
            p.stop()
        bs._route_prev.clear()
        bs._router_settings_cache.update(key=None, data=None)
        bs._provider_override.id = None
        bs._effort_override.level = None
        self.tmp.cleanup()

    def recording_runner(self, message, project_path, sse):
        self.seen.append({
            "provider": bs._current_provider_id(),
            "read_only": bs._turn_read_only(),
            "claude_args": bs._claude_permission_args(),
            "codex_args": bs._codex_permission_args(),
            "effort": bs._effective_provider_effort(),
            "claude_effort_args": bs._claude_effort_args(),
            "soul_retry": bs._retry_directive() if bs._turn_read_only() else "",
        })
        meta = bs._empty_usage_meta(bs._current_provider_id())
        meta.update(input_tokens=500, output_tokens=20, cost_usd=0.001)
        bs._log_usage(meta, message)
        reply = self.replies.pop(0) if self.replies else "ok"
        if reply is not None:
            sse("token", reply)
        sse("done", "")

    def turn(self, message, pid="auto", **kw):
        events = []
        with mock.patch.object(bs, "_provider_runner", return_value=self.recording_runner):
            answered = bs._run_turn_with_failover(
                pid, message, "", lambda t, d: events.append((t, d)), **kw)
        routes = [json.loads(d) for t, d in events if t == "route"]
        return answered, routes

    def rows(self):
        path = self.udir / bs.USAGE_LEDGER_NAME
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def cands(self):
        return {c["pid"]: c for c in bs._auto_candidates()}


# ── A3: candidates ──────────────────────────────────────────────────────────
class CandidatesTest(_Base):
    def test_subscription_models_are_eligible(self):
        c = self.cands()
        for pid in ("claude-cli", "claude-cli-sonnet", "claude-cli-haiku", "codex", "codex-sol", "codex-luna"):
            if pid in bs.PROVIDERS:
                self.assertTrue(c[pid]["eligible"], (pid, c[pid]["why_not"]))

    def test_pinned_and_legacy_excluded(self):
        c = self.cands()
        for pid in ("claude-cli-opus-5", "claude-cli-opus-48", "codex-mini"):
            if pid in c:
                self.assertFalse(c[pid]["eligible"])
                self.assertTrue(c[pid]["why_not"])

    def test_unrated_families_excluded(self):
        c = self.cands()
        for pid in ("gemini", "grok", "claude-cli-fable"):
            if pid in c:
                self.assertFalse(c[pid]["eligible"], pid)

    def test_codex_on_api_key_is_never_used(self):
        with mock.patch.object(bs, "_codex_auth_mode", return_value="api_key"):
            c = self.cands()
        for pid, row in c.items():
            if row["family"] == "codex":
                self.assertFalse(row["eligible"], pid)

    def test_blocked_family_is_ineligible(self):
        self.snap = _snapshot(claude_blocked="Claude 5-hour window is at 100%")
        c = self.cands()
        self.assertFalse(c["claude-cli"]["eligible"])
        self.assertIn("100%", c["claude-cli"]["why_not"])

    def test_limited_family_is_ineligible(self):
        with mock.patch.object(bs._failover, "limited_until",
                               side_effect=lambda fam, *a: 9e9 if fam == "claude" else None):
            c = self.cands()
        self.assertFalse(c["claude-cli-haiku"]["eligible"])
        self.assertIn("limit", c["claude-cli-haiku"]["why_not"])

    def test_local_model_not_downloaded_is_ineligible(self):
        with mock.patch.object(bs, "_ollama_installed_models", return_value=set()):
            c = self.cands()
        self.assertFalse(c["ollama"]["eligible"])
        with mock.patch.object(bs, "_ollama_installed_models", return_value=None):
            self.assertFalse(self.cands()["ollama"]["eligible"])

    def test_headroom_from_meter(self):
        self.snap = _snapshot(claude_pct=70)
        self.assertEqual(self.cands()["claude-cli"]["headroom_pct"], 30)

    def test_only_subscription_or_local_spend(self):
        for row in bs._auto_candidates():
            if row["eligible"]:
                self.assertIn(row["spend_class"], ("subscription", "local"))


# ── A5: settings ────────────────────────────────────────────────────────────
class SettingsTest(_Base):
    def test_validator_accepts_defaults(self):
        s, errs = ar.validate_settings(dict(ar.DEFAULT_SETTINGS))
        self.assertEqual(errs, [])

    def test_validator_rejects_bad_values(self):
        _, errs = ar.validate_settings({"mode": "turbo", "bias": "x", "sticky_minutes": 99,
                                        "triage": "yes", "nope": 1})
        self.assertEqual(len(errs), 5)

    def test_locked_category_cannot_be_lowered(self):
        for cat in ar.LOCKED:
            _, errs = ar.validate_settings({"category_floor": {cat: "fast"}})
            self.assertTrue(errs, cat)
            _, errs = ar.validate_settings({"category_floor": {cat: "strongest"}})
            self.assertEqual(errs, [], cat)

    def test_save_round_trip_and_mode(self):
        self.assertFalse(bs._auto_mode_on())
        s, _ = ar.validate_settings({"mode": "auto"})
        bs._save_router_settings(s)
        self.assertTrue((self.udir / bs.ROUTER_SETTINGS_NAME).exists())
        self.assertTrue(bs._auto_mode_on())
        self.assertEqual(bs._router_settings()["mode"], "auto")


# ── A6: read-only enforcement ───────────────────────────────────────────────
class ReadOnlyTest(_Base):
    def test_manual_turn_keeps_full_permissions(self):
        self.turn("delete the temp files please", pid="claude-cli-haiku")
        s = self.seen[0]
        self.assertFalse(s["read_only"])
        self.assertEqual(s["claude_args"], ["--dangerously-skip-permissions"])
        self.assertEqual(s["codex_args"], ["--dangerously-bypass-approvals-and-sandbox"])

    def test_below_top_auto_turn_is_read_only(self):
        self.turn("hi there!")
        s = self.seen[0]
        self.assertTrue(s["read_only"])
        self.assertIn("--tools", s["claude_args"])
        tools = s["claude_args"][s["claude_args"].index("--tools") + 1]
        for banned in ("Bash", "Edit", "Write", "Agent", "WebFetch", "Skill"):
            self.assertNotIn(banned, tools.split(","))
        self.assertIn("--strict-mcp-config", s["claude_args"])
        self.assertEqual(s["codex_args"], ["--sandbox", "read-only"])

    def test_flag_cleared_after_turn(self):
        self.turn("hi there!")
        self.assertFalse(bs._turn_read_only())
        self.assertEqual(bs._claude_permission_args(), ["--dangerously-skip-permissions"])

    def test_soul_directive_only_when_read_only(self):
        bs._route_ctx.rec = {"read_only": True}
        try:
            with mock.patch.object(bs, "_turn_read_only", return_value=True):
                self.assertIn("read-only mode", bs._load_soul(mode="cli"))
        finally:
            bs._route_ctx.rec = None
        self.assertNotIn("read-only mode", bs._load_soul(mode="cli"))

    def test_read_only_blocks_state_changing_markers(self):
        fired = []
        handlers = {name: (lambda payload, n=name: fired.append(n)) for name in bs._MARKER_HANDLERS}
        with mock.patch.dict(bs._MARKER_HANDLERS, handlers):
            out = []
            f = bs._marker_filter_sse(lambda t, d: out.append((t, d)))

            def runner(message, project_path, sse):
                sse("token", '<<create_standing_order>>{"name":"x"}<</create_standing_order>>')
                sse("token", '<<send_to_pane>>{"to":"a","message":"b"}<</send_to_pane>>')
                sse("token", '<<spawn_workspaces>>{"workspaces":[]}<</spawn_workspaces>>')

            with mock.patch.object(bs, "_provider_runner", return_value=runner):
                bs._run_turn_with_failover("auto", "hi there!", "", f)
            f.finalize()
        self.assertNotIn("create_standing_order", fired)
        self.assertNotIn("send_to_pane", fired)
        self.assertIn("spawn_workspaces", fired)

    def test_markers_still_work_on_manual_turns(self):
        fired = []
        handlers = {name: (lambda payload, n=name: fired.append(n)) for name in bs._MARKER_HANDLERS}
        with mock.patch.dict(bs._MARKER_HANDLERS, handlers):
            f = bs._marker_filter_sse(lambda t, d: None)

            def runner(message, project_path, sse):
                sse("token", '<<create_standing_order>>{"name":"x"}<</create_standing_order>>')

            with mock.patch.object(bs, "_provider_runner", return_value=runner):
                bs._run_turn_with_failover("claude-cli", "schedule it", "", f)
            f.finalize()
        self.assertIn("create_standing_order", fired)


# ── A7: dispatch ────────────────────────────────────────────────────────────
class DispatchTest(_Base):
    def test_hi_goes_fast(self):
        answered, routes = self.turn("hi")
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["tier"], "light")
        self.assertEqual(answered, "claude-cli-haiku")
        self.assertEqual(self.seen[0]["provider"], "claude-cli-haiku")

    def test_destructive_request_goes_strongest_and_keeps_tools(self):
        answered, routes = self.turn("delete my old invoices")
        self.assertEqual(routes[0]["tier"], "top")
        self.assertTrue(routes[0]["reason"].startswith("lock."), routes[0]["reason"])
        self.assertEqual(answered, "claude-cli")
        self.assertFalse(self.seen[0]["read_only"])

    def test_min_tier_floor(self):
        answered, routes = self.turn("hi", min_tier="mid")
        self.assertNotEqual(routes[0]["tier"], "light")

    def test_claude_limited_moves_to_codex(self):
        self.snap = _snapshot(claude_blocked="Claude 5-hour window is at 100%")
        answered, routes = self.turn("hi")
        self.assertTrue(answered.startswith("codex"), answered)

    def test_ledger_row_is_auto(self):
        self.turn("hi")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["route_mode"], "auto")
        self.assertIs(r["auto_chosen"], True)
        self.assertEqual(r["chosen_provider"], "claude-cli-haiku")
        self.assertEqual(r["chosen_tier"], "light")
        self.assertIs(r["read_only"], True)
        self.assertIsNone(r["fallback"])

    def test_override_restored_and_auto_never_leaks(self):
        bs._provider_override.id = None
        self.turn("hi")
        self.assertIsNone(bs._provider_override.id)
        self.assertEqual(bs.ACTIVE_PROVIDER, "claude-cli")
        self.assertNotEqual(bs._current_provider_id(), "auto")

    def test_router_failure_falls_back_to_default(self):
        with mock.patch.object(bs._auto_router, "decide", side_effect=RuntimeError("boom")):
            answered, routes = self.turn("hi")
        self.assertEqual(answered, "claude-cli")
        self.assertEqual(self.rows()[0]["route_reason"], "fallback_default")

    def test_sticky_does_not_drop_tier_mid_thread(self):
        self.turn("delete my old invoices")
        _, routes = self.turn("thanks")
        self.assertEqual(routes[0]["tier"], "top")

    def _with_history(self, turns):
        saved = getattr(bs._history_state, "history", None)
        bs._history_state.history = list(turns)
        self.addCleanup(setattr, bs._history_state, "history", saved)

    def test_continue_reads_the_pane_history(self):
        # No previous Auto decision in memory (as after a restart): the saved
        # conversation alone tells the router that "continue" is code work.
        self._with_history([
            {"role": "user", "content": "fix the login bug in app.js"},
            {"role": "assistant", "content": "Found it in the token check. Half patched so far."},
        ])
        answered, routes = self.turn("continue")
        self.assertEqual(routes[0]["tier"], "top")
        self.assertEqual(routes[0]["reason"], "follow.context")
        self.assertFalse(self.seen[0]["read_only"])

    def test_yes_to_an_offer_goes_strongest(self):
        self.turn("what time is it in tokyo")
        self._with_history([
            {"role": "user", "content": "what time is it in tokyo"},
            {"role": "assistant", "content": "3:40 am. Want me to set a reminder for your call?"},
        ])
        _, routes = self.turn("yes")
        self.assertEqual(routes[0]["tier"], "top")
        self.assertEqual(routes[0]["reason"], "follow.offer")

    def test_followup_context_skips_the_current_message(self):
        self._with_history([
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "continue"},
        ])
        ask, reply = bs._route_followup_context("continue")
        self.assertEqual((ask, reply), ("fix the bug", "Done."))

    def test_route_event_has_no_prompt_text(self):
        _, routes = self.turn("my secret phrase is zebra-unique-42")
        self.assertNotIn("zebra-unique-42", json.dumps(routes))


# ── A9: "Try a stronger model" (Redo) ──────────────────────────────────────
class RedoTest(_Base):
    def setUp(self):
        super().setUp()
        self._prev_hist = getattr(bs._history_state, "history", None)
        self.saves = []
        self._save_patch = mock.patch.object(bs, "_save_history", side_effect=lambda *a: self.saves.append(1))
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()
        bs._history_state.history = self._prev_hist
        super().tearDown()

    def hist(self, *pairs):
        h = []
        for role, text in pairs:
            h.append({"role": role, "content": text})
        bs._history_state.history = h
        return h

    def test_redo_records_redo_of_in_ledger(self):
        _, routes = self.turn("hi", min_tier="mid", redo_of="turn-abc")
        self.assertNotEqual(routes[0]["tier"], "light")
        self.assertEqual(self.rows()[0]["redo_of"], "turn-abc")

    def test_normal_turn_has_no_redo_of(self):
        self.turn("hi")
        self.assertIsNone(self.rows()[0]["redo_of"])

    def test_drop_removes_matching_pair(self):
        h = self.hist(("user", "earlier"), ("assistant", "ok"),
                      ("user", "[Open panes: x]\nwhat is a ledger"), ("assistant", "short answer"))
        self.assertTrue(bs._drop_redo_exchange("what is a ledger"))
        self.assertEqual([m["content"] for m in h], ["earlier", "ok"])
        self.assertEqual(len(self.saves), 1)

    def test_drop_ignores_different_question(self):
        h = self.hist(("user", "what is a ledger"), ("assistant", "short answer"))
        self.assertFalse(bs._drop_redo_exchange("something else"))
        self.assertEqual(len(h), 2)
        self.assertEqual(self.saves, [])

    def test_drop_needs_answer_last(self):
        h = self.hist(("assistant", "hello"), ("user", "what is a ledger"))
        self.assertFalse(bs._drop_redo_exchange("what is a ledger"))
        self.assertEqual(len(h), 2)

    def test_drop_ignores_empty_message_and_short_history(self):
        self.hist(("user", "x"), ("assistant", "y"))
        self.assertFalse(bs._drop_redo_exchange(""))
        self.hist(("assistant", "y"))
        self.assertFalse(bs._drop_redo_exchange("x"))


# ── A10: triage call for vague messages ─────────────────────────────────────
SIMPLE = {"category": "lookup", "difficulty": 1, "risk": "low", "takes_action": False,
          "confidence": "high"}
SIMPLE_JSON = json.dumps(SIMPLE)


class _Proc:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


class TriageTest(_Base):
    VAGUE = "hmm what about the other one"

    def setUp(self):
        super().setUp()
        bs._triage_cache.clear()
        bs._route_ctx.rec = None

    def tearDown(self):
        bs._triage_cache.clear()
        bs._route_ctx.rec = None
        super().tearDown()

    # Dispatch: the router uses the triage answer.
    def test_vague_message_routed_down_by_triage(self):
        with mock.patch.object(bs, "_router_triage", return_value=dict(SIMPLE)) as tri:
            answered, routes = self.turn(self.VAGUE)
        tri.assert_called_once()
        self.assertEqual(routes[0]["tier"], "light")
        self.assertEqual(routes[0]["reason"], "triage.down")
        row = self.rows()[0]
        self.assertIs(row["triage_used"], True)
        self.assertEqual(row["triage_result"]["difficulty"], 1)

    def test_failed_triage_goes_strongest(self):
        with mock.patch.object(bs, "_router_triage", return_value=None):
            answered, routes = self.turn(self.VAGUE)
        self.assertEqual(routes[0]["tier"], "top")
        self.assertEqual(routes[0]["reason"], "unclear.safe")

    def test_triage_not_called_for_clear_or_locked_messages(self):
        with mock.patch.object(bs, "_router_triage", return_value=dict(SIMPLE)) as tri:
            self.turn("hi")
            _, routes = self.turn("delete my old invoices")
        tri.assert_not_called()
        self.assertEqual(routes[0]["tier"], "top")

    def test_triage_off_in_settings(self):
        bs._save_router_settings({**ar.normalize_settings(None), "mode": "auto", "triage": False})
        with mock.patch.object(bs, "_router_triage", return_value=dict(SIMPLE)) as tri:
            _, routes = self.turn(self.VAGUE)
        tri.assert_not_called()
        self.assertEqual(routes[0]["tier"], "top")

    def test_thinking_line_shown_while_checking(self):
        events = []
        with mock.patch.object(bs, "_router_triage", return_value=dict(SIMPLE)), \
             mock.patch.object(bs, "_provider_runner", return_value=self.recording_runner):
            bs._run_turn_with_failover("auto", self.VAGUE, "", lambda t, d: events.append((t, d)))
        self.assertTrue(any(t == "thinking" and "Quick check" in d for t, d in events))

    # The helper: backend order, cache, and the Claude call itself.
    def test_local_first_then_cached(self):
        with mock.patch.object(bs, "_triage_local", return_value=dict(SIMPLE)) as loc, \
             mock.patch.object(bs, "_triage_claude", return_value=None) as cl:
            self.assertEqual(_REAL_TRIAGE("Can you help"), SIMPLE)
            self.assertEqual(_REAL_TRIAGE("can  you help"), SIMPLE)
        self.assertEqual(loc.call_count, 1)
        cl.assert_not_called()

    def test_local_failure_falls_back_to_claude(self):
        with mock.patch.object(bs, "_triage_local", return_value=None), \
             mock.patch.object(bs, "_triage_claude", return_value=dict(SIMPLE)) as cl:
            self.assertEqual(_REAL_TRIAGE("x"), SIMPLE)
        cl.assert_called_once()

    def test_failures_are_not_cached(self):
        with mock.patch.object(bs, "_triage_local", return_value=None), \
             mock.patch.object(bs, "_triage_claude", return_value=None) as cl:
            self.assertIsNone(_REAL_TRIAGE("x"))
            self.assertIsNone(_REAL_TRIAGE("x"))
        self.assertEqual(cl.call_count, 2)

    def test_backend_setting_respected(self):
        for backend, want_local, want_claude in (("local", 1, 0), ("claude", 0, 1)):
            bs._triage_cache.clear()
            bs._save_router_settings({**ar.normalize_settings(None), "triage_backend": backend})
            with self.subTest(backend=backend), \
                 mock.patch.object(bs, "_triage_local", return_value=None) as loc, \
                 mock.patch.object(bs, "_triage_claude", return_value=None) as cl:
                _REAL_TRIAGE("x")
                self.assertEqual((loc.call_count, cl.call_count), (want_local, want_claude))

    def _claude_run(self, stdout):
        calls = []

        def fake_run(args, **kw):
            calls.append((args, kw))
            return _Proc(stdout)
        with mock.patch.object(bs, "_provider_executable", return_value="claude"), \
             mock.patch.object(bs.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(bs.os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            result = bs._triage_claude("help me", ar.normalize_settings(None), bs._auto_candidates())
        return result, calls

    def test_claude_triage_runs_with_no_tools(self):
        out = json.dumps({"type": "result", "is_error": False, "result": SIMPLE_JSON,
                          "usage": {"input_tokens": 5, "output_tokens": 30}, "total_cost_usd": 0.0003})
        bs._route_ctx.rec = {"turn_id": "turn-1", "_logged": 0}
        result, calls = self._claude_run(out)
        self.assertEqual(result, SIMPLE)
        args, kw = calls[0]
        i = args.index("--tools")
        self.assertEqual(args[i + 1], "")
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--no-session-persistence", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])
        self.assertLessEqual(kw["timeout"], ar.TRIAGE_CLOUD_TIMEOUT)
        self.assertIn("<request>", kw["input"])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "triage")
        self.assertEqual(rows[0]["turn_id"], "turn-1")
        self.assertIs(rows[0]["triage_ok"], True)
        self.assertNotIn("help me", json.dumps(rows[0]))
        self.assertEqual(bs._route_ctx.rec["_logged"], 0)   # not counted as the turn's row

    def test_claude_error_or_garbage_is_none(self):
        for out in ("", "not json", json.dumps({"is_error": True, "result": SIMPLE_JSON}),
                    json.dumps({"is_error": False, "result": "I think it is easy"})):
            with self.subTest(out=out):
                result, _ = self._claude_run(out)
                self.assertIsNone(result)

    def test_claude_triage_skipped_when_claude_limited(self):
        self.snap = _snapshot(claude_blocked="Claude 5-hour window is at 100%")
        result, calls = self._claude_run(json.dumps({"result": SIMPLE_JSON}))
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_local_triage_parses_ollama_reply(self):
        body = json.dumps({"message": {"content": SIMPLE_JSON},
                           "prompt_eval_count": 50, "eval_count": 20}).encode()
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(bs.urllib.request, "urlopen", return_value=resp) as uo:
            result = bs._triage_local("help me", ar.normalize_settings(None), bs._auto_candidates())
        self.assertEqual(result, SIMPLE)
        sent = json.loads(uo.call_args[0][0].data)
        self.assertEqual(sent["format"], "json")
        self.assertNotIn("tools", sent)
        self.assertLessEqual(uo.call_args[1]["timeout"], ar.TRIAGE_LOCAL_TIMEOUT)
        self.assertEqual(self.rows()[0]["kind"], "triage")


if __name__ == "__main__":
    unittest.main()


# ── Auto effort: the router picks the effort level when Effort is on Auto ───
class EffortTest(_Base):
    BLOCKED = "Claude 5-hour window is at 100%"

    def test_fast_tier_runs_at_low(self):
        self.snap = _snapshot(claude_blocked=self.BLOCKED)     # fast model is Luna
        _, routes = self.turn("hi")
        self.assertEqual(ar.provider_tier(self.seen[0]["provider"]), ar.LIGHT)
        self.assertEqual(self.seen[0]["effort"], "low")
        self.assertEqual(routes[0]["effort"], "low")
        self.assertTrue(routes[0]["effort_auto"])

    def test_model_without_effort_dial_gets_none(self):
        _, routes = self.turn("hi")                             # Haiku has no effort setting
        self.assertEqual(self.seen[0]["provider"], "claude-cli-haiku")
        self.assertEqual(self.seen[0]["claude_effort_args"], [])
        self.assertEqual(routes[0]["effort"], "")
        self.assertFalse(routes[0]["effort_auto"])

    def test_redo_to_standard_thinks_a_step_harder(self):
        self.turn("hi", min_tier="mid")
        # Standard is medium; a Redo is a retry, so one step more.
        self.assertEqual(self.seen[0]["effort"], "high")

    def test_action_on_strongest_gets_a_step_more(self):
        _, routes = self.turn("delete my old invoices")
        self.assertEqual(routes[0]["tier"], ar.TOP)
        self.assertEqual(self.seen[0]["effort"], "xhigh")

    def test_user_choice_always_wins(self):
        for level in ("max", "low"):
            bs._effort_override.level = level
            _, routes = self.turn("hi", min_tier="mid")
            self.assertEqual(self.seen[-1]["effort"], level)
            self.assertFalse(routes[0]["effort_auto"])
            self.assertEqual(bs._effort_override.level, level)   # left as the user set it

    def test_global_effort_setting_wins(self):
        with mock.patch.object(bs, "ACTIVE_EFFORT", "low"):
            _, routes = self.turn("hi", min_tier="mid")
        self.assertEqual(self.seen[0]["effort"], "low")
        self.assertFalse(routes[0]["effort_auto"])

    def test_setting_off_leaves_cli_default(self):
        bs._save_router_settings({**ar.normalize_settings(None), "mode": "auto", "auto_effort": False})
        _, routes = self.turn("hi", min_tier="mid")
        self.assertEqual(self.seen[0]["effort"], "")
        self.assertEqual(self.seen[0]["claude_effort_args"], [])
        self.assertEqual(routes[0]["effort"], "")

    def test_pick_does_not_leak_past_the_turn(self):
        self.turn("delete my old invoices")
        self.assertIsNone(bs._effort_override.level)
        self.assertEqual(bs._current_effort(), "")
        bs._route_prev.clear()                 # new conversation, no sticky tier
        self.turn("hi", min_tier="mid")
        self.assertEqual(self.seen[1]["effort"], "high")

    def test_manual_turn_untouched(self):
        self.turn("hi", pid="claude-cli-haiku")
        self.assertEqual(self.seen[0]["effort"], "")

    def test_ledger_records_effort(self):
        self.turn("delete my old invoices")
        row = self.rows()[-1]
        self.assertEqual(row["effort"], "xhigh")
        self.assertTrue(row["effort_auto"])
        self.assertNotIn("_user_effort", row)

    def test_model_without_effort_reports_none(self):
        rec = {"turn_id": "t"}
        d = ar.Decision(tier=ar.LIGHT, effort="low")
        settings = ar.normalize_settings(None)
        with mock.patch.object(bs, "_provider_effort_levels", return_value=[{"id": ""}]):
            eff, auto = bs._route_effort(rec, "gemini", ar.LIGHT, d, settings, False)
        self.assertEqual((eff, auto), ("", False))


# ── Auto retry one tier up (Task A11) ───────────────────────────────────────
class RetryTest(_Base):
    def run_events(self, message, **kw):
        events = []
        with mock.patch.object(bs, "_provider_runner", return_value=self.recording_runner), \
                mock.patch.object(bs, "_drop_redo_exchange", return_value=True) as drop:
            answered = bs._run_turn_with_failover(
                "auto", message, "", lambda t, d: events.append((t, d)), **kw)
        self.drop = drop
        return answered, events

    def test_unsure_answer_retried_one_tier_up(self):
        self.replies = ["maybe <<auto_unsure>>{}<</auto_unsure>>", "sure answer"]
        answered, events = self.run_events("hi")
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(ar.provider_tier(self.seen[0]["provider"]), ar.LIGHT)
        self.assertEqual(ar.provider_tier(self.seen[1]["provider"]), ar.MID)
        self.assertEqual(answered, self.seen[1]["provider"])
        esc = [json.loads(d) for t, d in events if t == "escalate"]
        self.assertEqual(len(esc), 1)
        self.assertEqual((esc[0]["reason"], esc[0]["from_tier"], esc[0]["to_tier"]),
                         ("unsure", ar.LIGHT, ar.MID))
        routes = [json.loads(d) for t, d in events if t == "route"]
        self.assertEqual(routes[-1]["reason"], "escalated")
        self.drop.assert_called_once_with("hi")

    def test_exactly_one_done_after_retry(self):
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>", "fine"]
        _, events = self.run_events("hi")
        self.assertEqual([t for t, _ in events].count("done"), 1)
        self.assertEqual(events[-1][0], "done")

    def test_retry_thinks_harder(self):
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>", "fine"]
        self.run_events("hi")
        self.assertEqual(self.seen[0]["effort"], "")         # Haiku: no effort dial
        self.assertEqual(self.seen[1]["effort"], "high")     # Standard plus the retry step

    def test_user_effort_kept_on_retry(self):
        bs._effort_override.level = "medium"
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>", "fine"]
        self.run_events("hi")
        self.assertEqual([x["effort"] for x in self.seen], ["", "medium"])
        self.assertEqual(bs._effort_override.level, "medium")

    def test_needs_action_and_empty_also_retry(self):
        for reply, reason in (("I need to edit files. <<auto_needs_action>>{}<</auto_needs_action>>",
                               "needs_action"), (None, "empty")):
            self.seen.clear()
            bs._route_prev.clear()
            self.replies = [reply, "done it"]
            _, events = self.run_events("hi")
            esc = [json.loads(d) for t, d in events if t == "escalate"]
            self.assertEqual(esc[0]["reason"], reason)
            self.assertEqual(len(self.seen), 2)

    def test_needs_action_skips_straight_to_top(self):
        # Every tier below the top is read-only, so a needs-action retry that
        # stopped at Standard could not act either (2026-11-24 misroute).
        self.replies = ["I need to edit files. <<auto_needs_action>>{}<</auto_needs_action>>", "done it"]
        _, events = self.run_events("hi")
        esc = [json.loads(d) for t, d in events if t == "escalate"]
        self.assertEqual((esc[0]["from_tier"], esc[0]["to_tier"]), (ar.LIGHT, ar.TOP))
        self.assertEqual(ar.provider_tier(self.seen[1]["provider"]), ar.TOP)
        routes = [json.loads(d) for t, d in events if t == "route"]
        self.assertFalse(routes[-1]["read_only"])

    def test_unsure_from_standard_goes_to_top(self):
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>", "fine"]
        self.run_events("explain how vaccines work")
        self.assertEqual([ar.provider_tier(x["provider"]) for x in self.seen], [ar.MID, ar.TOP])

    def test_confident_answer_not_retried(self):
        self.replies = ["hello!"]
        _, events = self.run_events("hi")
        self.assertEqual(len(self.seen), 1)
        self.assertFalse([t for t, _ in events if t == "escalate"])
        self.assertEqual([t for t, _ in events].count("done"), 1)
        self.drop.assert_not_called()

    def test_only_one_retry(self):
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>", "<<auto_unsure>>{}<</auto_unsure>>"]
        self.run_events("hi")
        self.assertEqual(len(self.seen), 2)

    def test_strongest_tier_never_retried(self):
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>"]
        self.run_events("delete my old invoices")
        self.assertEqual(len(self.seen), 1)

    def test_setting_off_disables_retry_and_directive(self):
        bs._save_router_settings({**ar.normalize_settings(None), "mode": "auto", "escalation": False})
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>"]
        self.run_events("hi")
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0]["soul_retry"], "")

    def test_lighter_model_told_how_to_hand_off(self):
        self.run_events("hi")
        self.assertIn("<<auto_unsure>>", self.seen[0]["soul_retry"])

    def test_ledger_marks_the_retry(self):
        self.replies = ["<<auto_unsure>>{}<</auto_unsure>>", "fine"]
        self.run_events("hi")
        row = self.rows()[-1]
        self.assertTrue(row.get("escalated"))
        self.assertEqual(row.get("escalation_reason"), "unsure")

    def test_markers_hidden_from_the_reply(self):
        out = []
        f = bs._marker_filter_sse(lambda t, d: out.append((t, d)))
        f("token", "Answer text. <<auto_unsure>>{}<</auto_unsure>>")
        f("done", "")
        text = "".join(d for t, d in out if t == "token")
        self.assertIn("Answer text.", text)
        self.assertNotIn("auto_unsure", text)
