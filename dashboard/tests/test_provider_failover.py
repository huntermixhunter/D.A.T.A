"""Usage-limit failover + subscription usage meter (provider_failover.py).

Standard library unittest only, like the rest of the retail suite (buyer
machines have no pytest):

    cd dashboard
    python -m unittest tests.test_provider_failover -v

No real CLI is started: the routing tests use stand-in runners that call the
same hooks the real Claude and Codex runners in bridge_server.py call.
"""
import datetime
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))
import provider_failover as pf  # noqa: E402

LA = pf._tz("America/Los_Angeles")


def _at(y, mo, d, h, mi=0, tz=LA):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


NOW = _at(2026, 11, 11, 22, 0)
LIMIT = "You've hit your session limit · resets 6:10am (America/Los_Angeles)"


class _Fresh(unittest.TestCase):
    """Every test starts from a clean module state with a temp state file and
    no background refresh threads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        pf._state.update({"enabled": True, "limited": {}, "last_failover": None,
                          "backup": pf.BACKUP_AUTO})
        pf._meter.update({"claude": {}, "codex": {}})
        pf.init(self.tmp / "state.json")
        p = mock.patch.object(pf, "refresh_async", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)


# ── detection ───────────────────────────────────────────────────────────────

@unittest.skipIf(LA is None, "tz data for America/Los_Angeles not installed")
class Detection(_Fresh):
    def test_claude_limit_messages(self):
        cases = [
            (LIMIT, "session", _at(2026, 11, 12, 6, 10)),
            ("You've hit your 5-hour limit · resets 11:40pm (America/Los_Angeles)", "5-hour",
             _at(2026, 11, 11, 23, 40)),
            ("You've hit your weekly limit · resets Nov 14, 8am (America/Los_Angeles)", "weekly",
             _at(2026, 11, 14, 8)),
            ("You've hit your monthly spend limit · raise it at claude.ai/settings/usage?from=cc_cli_limit_message"
             " · your session limit resets 9:50pm (America/Los_Angeles)", "monthly spend",
             _at(2026, 11, 12, 21, 50)),
            ("You've hit your session limit · resets 1:40am (America/Los_Angeles)You've hit your session limit"
             " · resets 1:40am (America/Los_Angeles)", "session", _at(2026, 11, 12, 1, 40)),
        ]
        for text, kind, expect in cases:
            with self.subTest(text=text[:40]):
                lim = pf.match_limit(text, "claude", now=NOW)
                self.assertTrue(lim)
                self.assertEqual(lim["kind"], kind)
                self.assertAlmostEqual(lim["resets_at"], expect, places=3)

    def test_quoted_limit_text_inside_a_reply_is_not_a_limit(self):
        reply = ("The log shows the CLI saying \"You've hit your session limit · resets "
                 "6:10am\" several times last week.")
        self.assertIsNone(pf.match_limit(reply, "claude", now=NOW))
        self.assertIsNone(pf.match_limit("Normal answer.", "claude", now=NOW))

    def test_legacy_and_codex_messages(self):
        self.assertEqual(pf.match_limit("Claude AI usage limit reached|1794472200", "claude")["resets_at"],
                         1794472200)
        lim = pf.match_limit("You've hit your usage limit. Upgrade to Pro, or try again in 2 hours 5 minutes.",
                             "codex", now=NOW)
        self.assertAlmostEqual(lim["resets_at"], NOW + 2 * 3600 + 300, places=3)
        self.assertIsNone(pf.match_limit("unexpected status 401 Unauthorized", "codex"))


class LocalTime(_Fresh):
    def test_no_zone_reads_in_system_local_time(self):
        # Retail default: a reset time with no zone is read on the buyer's
        # own clock, not a hard-coded zone.
        self.assertIsNone(pf.DEFAULT_TZ)
        now = datetime.datetime(2026, 11, 11, 22, 0).timestamp()   # naive = local
        got = pf.parse_reset_text("6:10am", None, now)
        want = datetime.datetime(2026, 11, 12, 6, 10).timestamp()
        self.assertAlmostEqual(got, want, places=3)

    def test_unknown_zone_falls_back_to_local_instead_of_failing(self):
        self.assertIsNone(pf._tz("Not/AZone"))
        self.assertIsNotNone(pf.parse_reset_text("8am", "Not/AZone", time.time()))

    def test_unknown_reset_falls_back_to_short_retry_window(self):
        pf.mark_limited("claude", "session", None)
        until = pf.limited_until("claude")
        self.assertTrue(until and 0 < until - time.time() <= pf.UNKNOWN_RESET_SECS + 1)


# ── meter ───────────────────────────────────────────────────────────────────

class Meter(_Fresh):
    def test_rate_event_feeds_meter_and_flags_rejection(self):
        info = {"status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": 0.33, "resetsAt": 1794472200},
            "seven_day": {"utilization": 0.42, "resetsAt": 1794708000}}}
        self.assertIsNone(pf.note_claude_rate_event(info))
        w = {x["id"]: x for x in pf.snapshot()["claude"]["windows"]}
        self.assertEqual((w["five_hour"]["pct"], w["seven_day"]["pct"]), (33, 42))
        lim = pf.note_claude_rate_event({"status": "rejected", "rateLimitType": "five_hour",
                                         "resetsAt": 1794472200})
        self.assertEqual(lim, {"kind": "5-hour", "resets_at": 1794472200.0, "label": "5-hour limit"})

    def test_get_usage_answer_shape(self):
        pf.note_claude_usage({"five_hour": {"utilization": 34, "resets_at": "2026-11-12T08:30:00+00:00"},
                              "seven_day": {"utilization": 42, "resets_at": "2026-11-15T02:00:00+00:00"},
                              "seven_day_opus": None},
                             extra_usage={"is_enabled": False})
        snap = pf.snapshot()["claude"]
        self.assertEqual([w["pct"] for w in snap["windows"]], [34, 42])
        self.assertIs(snap["extra_usage_enabled"], False)

    def test_codex_session_file_parsing_and_reset_windows(self):
        day = self.tmp / "sessions" / "2026" / "11" / "11"
        day.mkdir(parents=True)
        now = time.time()
        rl = {"primary": {"used_percent": 99.0, "window_minutes": 300, "resets_at": int(now - 60)},
              "secondary": {"used_percent": 62.0, "window_minutes": 10080, "resets_at": int(now + 86400)},
              "credits": {"has_credits": True}, "rate_limit_reached_type": None}
        (day / "rollout-x.jsonl").write_text(
            json.dumps({"timestamp": "2026-11-11T10:00:00Z", "type": "event_msg",
                        "payload": {"type": "token_count", "rate_limits": rl}}) + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.tmp)}):
            pf.refresh_codex(check_login=False)
        w = {x["label"]: x for x in pf.snapshot()["codex"]["windows"]}
        self.assertEqual(w["5-HOUR"]["pct"], 0)          # reset since the reading
        self.assertIs(w["5-HOUR"]["live"], False)
        self.assertEqual(w["WEEKLY"]["pct"], 62)
        self.assertIs(pf.snapshot()["codex"]["credits_available"], True)

    def test_missing_claude_cli_reports_not_installed(self):
        snap = pf.refresh_claude(exe=str(self.tmp / "no-such-claude.exe"))
        self.assertEqual(snap.get("error"), "Claude CLI not installed")
        self.assertIs(snap.get("signed_in"), False)


# ── routing ─────────────────────────────────────────────────────────────────

class Harness:
    """Stand-in for the bridge. DATA runners report each tool call as a
    `thinking` line and each tool result as a "  -> preview" thinking line."""

    def __init__(self, claude_script, codex_reply="Codex finished it.", codex_script=None):
        self.events, self.history, self.calls = [], [], []
        self.pinned = None
        self.claude_script = claude_script
        self.codex_reply = codex_reply
        self.codex_script = codex_script

    def claude(self, message, project, sse):
        self.calls.append(("claude", self.pinned, message))
        for kind, payload in self.claude_script:
            if kind == "tool":
                sse("thinking", payload)
                sse("thinking", "  → file contents preview")
            elif kind == "text":
                if not pf.claim_limit_text(payload):
                    sse("token", payload)
        if not pf.handing_off():
            self.history += [{"role": "user", "content": message},
                             {"role": "assistant", "content": "claude reply"}]
        sse("meta", "{}")
        sse("done", "")

    def codex(self, message, project, sse):
        self.calls.append(("codex", self.pinned, message))
        if self.codex_script == "limit":
            # Mirrors ask_codex_cli_stream: no text, limit in the error, no
            # history write, still sends meta + done.
            pf.claim_limit_text("You've hit your usage limit. Try again in 3 hours.")
        else:
            sse("token", self.codex_reply)
            self.history += [{"role": "user", "content": message},
                             {"role": "assistant", "content": self.codex_reply}]
        sse("meta", "{}")
        sse("done", "")

    def run(self, pid="claude-cli", available=lambda p: True, announce=True):
        def set_provider(p):
            prev, self.pinned = self.pinned, p
            return prev
        return pf.run_turn(pid, "Fix the bug", "", lambda e, t: self.events.append((e, t)),
                           runner_for=lambda p: self.claude if p.startswith("claude") else self.codex,
                           set_provider=set_provider, provider_available=available,
                           history=self.history, save_history=lambda: None, announce=announce)

    def text(self):
        return "".join(t for e, t in self.events if e == "token")


def _codex_ready():
    pf._set_meter("codex", signed_in=True, windows=[{"label": "5-HOUR", "pct": 20, "live": True}])


def _claude_ready():
    pf._set_meter("claude", signed_in=True, windows=[{"label": "5-HOUR", "pct": 20, "live": True}])


class Routing(_Fresh):
    def test_no_limit_runs_untouched(self):
        h = Harness([("text", "All done.")])
        self.assertEqual(h.run(), "claude-cli")
        self.assertEqual(h.text(), "All done.")
        self.assertEqual([e for e, _ in h.events][-2:], ["meta", "done"])
        self.assertIsNone(pf.snapshot()["claude"]["limited"])

    def test_non_cli_provider_passes_straight_through(self):
        seen = []
        out = pf.run_turn("ollama", "hi", "", lambda e, t: seen.append(e),
                          runner_for=lambda p: (lambda m, pp, sse: sse("done", "")),
                          set_provider=lambda p: None, provider_available=lambda p: True)
        self.assertEqual((out, seen), ("ollama", ["done"]))

    def test_mid_turn_limit_hands_off_with_steps_and_tidy_history(self):
        _codex_ready()
        h = Harness([("text", "Reading the file first."), ("tool", "Read bridge_server.py"), ("text", LIMIT)])
        self.assertEqual(h.run(), "codex")
        self.assertEqual([c[:2] for c in h.calls], [("claude", "claude-cli"), ("codex", "codex")])
        handoff = h.calls[1][2]
        self.assertIn("FAILOVER HANDOFF", handoff)
        self.assertIn("- Read bridge_server.py", handoff)
        self.assertNotIn("file contents preview", handoff)     # tool results are not steps
        self.assertTrue(handoff.endswith("Fix the bug"))
        self.assertNotIn(LIMIT, h.text())
        self.assertIn("Codex is picking up", h.text())
        self.assertEqual([e for e, _ in h.events].count("done"), 1)   # Claude's done was held
        self.assertEqual(h.history[0], {"role": "user", "content": "Fix the bug"})
        self.assertTrue(h.history[1]["content"].startswith("Reading the file first."))
        self.assertIsNone(h.pinned)
        self.assertTrue(pf.limited_until("claude"))
        self.assertEqual(pf.snapshot()["last_failover"]["to"], "codex")

    def test_tier_mapping_keeps_fast_panes_fast(self):
        _codex_ready()
        h = Harness([("text", LIMIT)])
        self.assertEqual(h.run("claude-cli-haiku"), "codex-luna")
        self.assertEqual(pf.FALLBACK["claude-cli-sonnet"], "codex-sol")
        self.assertEqual(pf.FALLBACK["codex-mini"], "claude-cli-haiku")

    def test_codex_limit_hands_off_to_claude(self):
        _claude_ready()
        h = Harness([("text", "Claude took over.")], codex_script="limit")
        self.assertEqual(h.run("codex"), "claude-cli")
        self.assertIn("Claude is picking up", h.text())
        self.assertIn("Claude took over.", h.text())
        self.assertEqual([e for e, _ in h.events].count("done"), 1)
        self.assertTrue(pf.limited_until("codex"))

    def test_voice_turn_puts_notes_on_the_thinking_line(self):
        _codex_ready()
        h = Harness([("text", LIMIT)])
        h.run(announce=False)
        self.assertNotIn("picking up", h.text())
        self.assertTrue(any(e == "thinking" and "picking up" in t for e, t in h.events))

    def test_later_turns_route_straight_to_codex_then_return_after_reset(self):
        _codex_ready()
        pf.mark_limited("claude", "session", time.time() + 3600)
        h = Harness([("text", "unused")])
        self.assertEqual(h.run(), "codex")
        self.assertEqual(h.calls, [("codex", "codex", "Fix the bug")])
        self.assertIn("is answering", h.text())
        pf._state["limited"]["claude"]["until"] = time.time() - 1
        h2 = Harness([("text", "Back on Claude.")])
        self.assertEqual(h2.run(), "claude-cli")

    def test_signed_out_codex_blocks_failover_and_explains(self):
        pf._set_meter("codex", signed_in=False)
        h = Harness([("text", LIMIT)])
        self.assertEqual(h.run(), "claude-cli")
        self.assertIn(LIMIT, h.text())
        self.assertIn("Failover skipped: Codex is signed out", h.text())
        self.assertEqual([e for e, _ in h.events][-2:], ["meta", "done"])
        self.assertEqual(len(h.calls), 1)

    def test_uninstalled_partner_is_never_a_target(self):
        h = Harness([("text", LIMIT)])
        h.run(available=lambda p: p.startswith("claude"))
        self.assertIn("Codex is not installed", h.text())

    def test_codex_at_100_percent_is_never_a_target(self):
        pf._set_meter("codex", signed_in=True, windows=[{"label": "5-HOUR", "pct": 100, "live": True}])
        h = Harness([("text", LIMIT)])
        h.run()
        self.assertIn("5-hour window is at 100%", h.text())

    def test_switch_off_disables_both_routing_and_handoff(self):
        _codex_ready()
        pf.set_enabled(False)
        h = Harness([("text", LIMIT)])
        self.assertEqual(h.run(), "claude-cli")
        self.assertIn("switched off in Settings", h.text())
        h2 = Harness([("text", "ok")])
        self.assertEqual(h2.run(), "claude-cli")   # no sticky routing either

    def test_fresh_meter_reading_clears_limit_early(self):
        pf.mark_limited("claude", "session", time.time() + 3600)
        time.sleep(0.01)
        pf.note_claude_usage({"five_hour": {"utilization": 3}, "seven_day": {"utilization": 50}})
        self.assertIsNone(pf.limited_until("claude"))

    def test_state_survives_restart(self):
        pf.set_enabled(False)
        pf.mark_limited("codex", "usage", time.time() + 600)
        pf._state.update({"enabled": True, "limited": {}})
        pf.init(self.tmp / "state.json")
        self.assertIs(pf.enabled(), False)
        self.assertTrue(pf.limited_until("codex"))


# ── backup model chosen in Settings ─────────────────────────────────────────

GEMINI_QUOTA = ("Error: You have reached your daily gemini-2.5-pro quota limit. "
                "Please retry in 2 hours.")


class Multi:
    """Stand-in bridge with one runner per family. `scripts` maps a family to
    'limit' (fails with that family's limit text and writes no history) or a
    reply string."""

    LIMITS = {"claude": LIMIT, "codex": "You've hit your usage limit. Try again in 3 hours.",
              "gemini": GEMINI_QUOTA}

    def __init__(self, **scripts):
        self.scripts, self.events, self.history, self.calls = scripts, [], [], []
        self.pinned = None

    def runner(self, pid):
        fam = pf.family(pid)

        def run(message, project, sse):
            self.calls.append((pid, self.pinned, message))
            reply = self.scripts.get(fam, f"{fam} reply")
            if reply == "limit":
                pf.claim_limit_text(self.LIMITS[fam])
            else:
                sse("token", reply)
                self.history += [{"role": "user", "content": message},
                                 {"role": "assistant", "content": reply}]
            sse("meta", "{}")
            sse("done", "")
        return run

    def run(self, pid, default=None, available=lambda p: True):
        def set_provider(p):
            prev, self.pinned = self.pinned, p
            return prev
        return pf.run_turn(pid, "Fix the bug", "", lambda e, t: self.events.append((e, t)),
                           runner_for=self.runner, set_provider=set_provider,
                           provider_available=available, history=self.history,
                           save_history=lambda: None, default_provider=default or pid)

    def text(self):
        return "".join(t for e, t in self.events if e == "token")


class Backup(_Fresh):
    def test_automatic_keeps_the_tier_table(self):
        self.assertEqual(pf.backup_choice(), "auto")
        self.assertEqual(pf.backup_for("claude-cli-sonnet", "claude-cli-sonnet"), "codex-sol")
        self.assertEqual(pf.backup_for("codex", "claude-cli"), "claude-cli")
        self.assertIsNone(pf.backup_for("gemini", "gemini"))

    def test_chosen_backup_wins_for_any_family(self):
        pf.set_backup("ollama")
        for pid in ("claude-cli", "claude-cli-haiku", "codex-sol", "gemini"):
            self.assertEqual(pf.backup_for(pid, pid), "ollama", pid)

    def test_window_already_on_the_backup_falls_back_to_the_default(self):
        pf.set_backup("codex")
        self.assertEqual(pf.backup_for("codex-sol", "claude-cli"), "claude-cli")
        self.assertEqual(pf.backup_for("codex-sol", "gemini"), "gemini")

    def test_backup_sharing_the_default_account_uses_automatic(self):
        pf.set_backup("claude-cli-haiku")
        self.assertEqual(pf.backup_for("claude-cli", "claude-cli"), "codex")
        snap = pf.snapshot("claude-cli")
        self.assertEqual((snap["backup"], snap["backup_target"]), ("claude-cli-haiku", "codex"))

    def test_choice_is_validated_and_survives_restart(self):
        with self.assertRaises(ValueError):
            pf.set_backup("hermes")
        pf.set_backup("gemini")
        pf._state["backup"] = pf.BACKUP_AUTO
        pf.init(self.tmp / "state.json")
        self.assertEqual(pf.backup_choice(), "gemini")
        (self.tmp / "state.json").write_text(json.dumps({"backup": "bogus"}), encoding="utf-8")
        pf.init(self.tmp / "state.json")
        self.assertEqual(pf.backup_choice(), "auto")

    def test_snapshot_names_the_pairing(self):
        pf.set_backup("ollama-small")
        snap = pf.snapshot("gemini")
        self.assertEqual((snap["default"], snap["default_family"]), ("gemini", "gemini"))
        self.assertEqual((snap["backup_target"], snap["backup_family"]), ("ollama-small", "ollama"))
        self.assertNotIn("default", pf.snapshot())   # older callers keep the old shape

    def test_claude_limit_hands_off_to_chosen_local_model(self):
        pf.set_backup("ollama")
        h = Multi(claude="limit", ollama="Ollama finished it.")
        self.assertEqual(h.run("claude-cli"), "ollama")
        self.assertEqual([c[0] for c in h.calls], ["claude-cli", "ollama"])
        self.assertIn("Ollama is picking up from here", h.text())
        self.assertEqual(h.history[-1]["content"], "Ollama finished it.")
        h2 = Multi(ollama="Still Ollama.")
        self.assertEqual(h2.run("claude-cli"), "ollama")   # routed until the reset
        self.assertIn("so Ollama is answering", h2.text())

    def test_gemini_quota_hands_off_and_records_reset(self):
        pf.set_backup("claude-cli")
        _claude_ready()
        h = Multi(gemini="limit", claude="Claude finished it.")
        self.assertEqual(h.run("gemini"), "claude-cli")
        self.assertIn("Gemini hit its quota limit", h.text())
        until = pf.limited_until("gemini")
        self.assertAlmostEqual(until - time.time(), 7200, delta=60)

    def test_gemini_without_a_backup_explains_why(self):
        h = Multi(gemini="limit")
        self.assertEqual(h.run("gemini"), "gemini")
        self.assertIn("Failover skipped: no backup model is set", h.text())

    def test_uninstalled_chosen_backup_is_skipped(self):
        pf.set_backup("ollama")
        h = Multi(claude="limit")
        h.run("claude-cli", available=lambda p: not p.startswith("ollama"))
        self.assertIn("Ollama is not installed", h.text())

    def test_gemini_patterns(self):
        for text in (GEMINI_QUOTA, "Usage limit reached for all Pro models.",
                     "Quota exceeded for quota metric 'Gemini 2.5 Pro Requests'",
                     '[API Error: {"status": "RESOURCE_EXHAUSTED"}]'):
            self.assertIsNotNone(pf.match_limit(text, "gemini"), text)
        self.assertIsNone(pf.match_limit("Here is how API quotas work.", "gemini"))
        self.assertIsNone(pf.match_limit(GEMINI_QUOTA, "ollama"))


# ── bridge + UI wiring (static checks; the bridge is not started) ───────────

class Wiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = (DASH / "bridge_server.py").read_text(encoding="utf-8")
        cls.app = (DASH / "app.js").read_text(encoding="utf-8")
        cls.html = (DASH / "index.html").read_text(encoding="utf-8")
        cls.gitignore = (DASH.parent / ".gitignore").read_text(encoding="utf-8")

    def test_every_dispatch_site_goes_through_failover(self):
        self.assertNotRegex(self.bridge, r"\brunner\(message")
        self.assertEqual(self.bridge.count("_run_turn_with_failover("), 4)   # def + chat, sync, voice
        self.assertIn("announce=False", self.bridge)

    def test_runner_hooks_present(self):
        for hook in ('elif etype == "rate_limit_event"', "_failover.claim_rate_event(",
                     "_failover.handing_off()", "_failover.claim_limit_text(",
                     "_failover.claim_limit_text(stderr)", "not gemini_handed_off",
                     "default_provider=ACTIVE_PROVIDER", "_failover.set_backup(backup)"):
            self.assertIn(hook, self.bridge)

    def test_routes_and_static_css(self):
        for route in ('path == "/usage"', 'path == "/usage/refresh"', 'path == "/failover"',
                      '"/usage_meter.css": ("usage_meter.css"'):
            self.assertIn(route, self.bridge)
        self.assertTrue((DASH / "usage_meter.css").is_file())

    def test_state_file_is_local_and_never_ships(self):
        self.assertIn('PROJECT_DIR / "provider_failover_state.json"', self.bridge)
        self.assertIn("provider_failover_state.json", self.gitignore)

    def test_ui_pieces(self):
        for needle in ('id="usage-meter"', 'id="usage-rows"', 'id="failover-toggle"', "usage_meter.css",
                       'id="settings-backup-row"'):
            self.assertIn(needle, self.html)
        for fn in ("function fetchUsage", "function refreshUsage", "function setFailover",
                   "setInterval(fetchUsage", "function settingsSetBackup", "function _llmFamily("):
            self.assertIn(fn, self.app)

    def test_never_reads_claude_credentials(self):
        src = (DASH / "provider_failover.py").read_text(encoding="utf-8")
        self.assertNotIn(".credentials.json", src)


if __name__ == "__main__":
    unittest.main()
