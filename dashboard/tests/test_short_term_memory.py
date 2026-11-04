"""Tests for short-term memory: the "Recent conversation" block that gives a
stateless CLI turn this pane's latest turns.

The tests exec the real helper block (and, for the live test, the real
streaming CLI runner) out of bridge_server.py into a stub namespace, so they
exercise the shipping code rather than a copy.

What was broken: every prompt builder pasted the last 12 messages, each cut to
its FIRST 600 characters. A stored reply opens with step-by-step narration and
ends with the actual answer, so the answer was the part that got cut, and a
clicked option arrived without the question it answered. The block must keep
each reply's ending and the latest reply nearly whole, inside a fixed budget.

Standard-library unittest only (the retail runtime has no pytest):

    cd dashboard
    python -m unittest tests.test_short_term_memory -v

The live test makes one small Claude Code call (Haiku, on the signed-in
subscription; the runner strips ANTHROPIC_API_KEY) and is skipped unless the
environment sets DATA_LIVE_TESTS=1.
"""
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASH = HERE.parent
USERS = DASH.parent / "users"
SRC = io.open(DASH / "bridge_server.py", encoding="utf-8").read()

START = '# SHORT-TERM MEMORY: the "Recent conversation" block'
END = "def ask_hermes_cli(message: str"
TRIM = "[...earlier part trimmed...] "
HEADER_SLACK = 200  # header line plus per-line "Data: " prefixes and trim markers


def load_block():
    """A fresh namespace holding the real helper block from bridge_server.py."""
    i, j = SRC.find(START), SRC.find(END)
    if i == -1 or j <= i:
        raise AssertionError("short-term memory block not found in bridge_server.py")
    ns = {"MAX_HISTORY": 20, "conversation_history": []}
    code = SRC[SRC.rfind("# ═", 0, i):j]
    exec(compile(code, "bridge_server.py:short-term-memory", "exec"), ns)
    return ns


NS = load_block()
block_of = NS["_recent_conversation_block"]
BUDGET = NS["HISTORY_CHAR_BUDGET"]
OLDER_REPLY = NS["HISTORY_OLDER_REPLY"]


def old_block(history):
    """The rule that shipped until 2026-11-03: last 12 messages, first 600 chars each."""
    lines = []
    for turn in history[-12:]:
        role = "Captain" if turn["role"] == "user" else "Data"
        content = turn.get("content", "")
        if isinstance(content, str) and content:
            lines.append(f"{role}: {content[:600]}")
    return ("Recent conversation:\n" + "\n".join(lines)) if lines else ""


def narrated_reply(narration_chars, answer_chars, marker):
    """A reply shaped like a real stored one: step narration glued together
    first, the actual answer last, ending in an ask_options question."""
    steps, n = [], 0
    while sum(len(s) for s in steps) < narration_chars:
        n += 1
        steps.append(f"Step {n}: reading module {n} to trace the call path.")
    narration = "".join(steps)  # real narration has no separators
    answer = ("Here is what I found about the memory engine. "
              * (answer_chars // 47 + 1))[:answer_chars]
    question = ('<<ask_options>>{"question":"Which fix should I apply?",'
                '"options":["Fix it (recommended)","Leave it"]}<</ask_options>>')
    return narration + " " + answer + f" The key fact is {marker}. " + question


class RecentConversationBlockTests(unittest.TestCase):

    def test_empty_history_gives_no_block(self):
        self.assertEqual(block_of([]), "")

    def test_message_passes_through_untouched_with_no_history(self):
        NS["conversation_history"] = []
        self.assertEqual(NS["_with_recent_conversation"]("hello"), "hello")

    def test_latest_reply_keeps_its_conclusion_and_question(self):
        reply = narrated_reply(3000, 4000, "CODEWORD-ORCHID-58")
        hist = [{"role": "user", "content": "how does memory work?"},
                {"role": "assistant", "content": reply}]
        b = block_of(hist)
        self.assertIn("CODEWORD-ORCHID-58", b)
        self.assertIn("Which fix should I apply?", b)
        # The opening narration is what gets trimmed.
        self.assertNotIn("Step 1:", b)
        self.assertIn(TRIM, b)
        # Regression proof: the old rule lost exactly that conclusion.
        self.assertNotIn("CODEWORD-ORCHID-58", old_block(hist))

    def test_typical_full_reply_is_kept_whole(self):
        whole = "A typical full answer sentence. " * 140  # ~4,500 chars, the measured p90
        b = block_of([{"role": "user", "content": "q"},
                      {"role": "assistant", "content": whole}])
        self.assertIn(whole.strip(), b)

    def test_older_reply_keeps_its_ending_within_its_budget(self):
        older = narrated_reply(500, 2600, "OLDER-FACT-12")
        hist = [{"role": "user", "content": "first"}, {"role": "assistant", "content": older},
                {"role": "user", "content": "second"}, {"role": "assistant", "content": "short answer"}]
        lines = [l for l in block_of(hist).splitlines() if "OLDER-FACT-12" in l]
        self.assertEqual(len(lines), 1)
        self.assertLessEqual(len(lines[0]), len("Data: " + TRIM) + OLDER_REPLY)

    def test_trimmed_excerpt_starts_on_a_word_boundary(self):
        b = block_of([{"role": "assistant", "content": "alpha bravo charlie " * 600}])
        body = b.split(TRIM, 1)[1]
        self.assertIn(body.split(" ", 1)[0], ("alpha", "bravo", "charlie"))

    def test_long_pasted_message_keeps_both_ends(self):
        paste = ("fix this error please: " + ("Traceback line with detail. " * 220)
                 + " what broke here?")
        b = block_of([{"role": "user", "content": paste}, {"role": "assistant", "content": "ok"}])
        self.assertIn("fix this error please", b)
        self.assertIn("what broke here?", b)
        self.assertIn("[...middle trimmed...]", b)

    def test_non_string_content_is_skipped(self):
        hist = [{"role": "assistant", "content": [{"type": "text", "text": "x"}]}]
        self.assertEqual(block_of(hist), "")

    def test_budget_keeps_the_newest_turns_in_order(self):
        big = []
        for k in range(10):
            big += [{"role": "user", "content": f"question {k} " + "q" * 3000},
                    {"role": "assistant", "content": narrated_reply(1000, 4000, f"FACT-{k}")}]
        b = block_of(big)
        self.assertLessEqual(len(b), BUDGET + HEADER_SLACK)
        self.assertIn("FACT-9", b)
        data_lines = [l for l in b.splitlines() if l.startswith("Data: ")]
        self.assertIn("FACT-9", data_lines[-1])  # oldest first, newest last
        self.assertNotIn("FACT-0", b)            # the budget drops the oldest turns


class PromptBuilderWiringTests(unittest.TestCase):
    """Every runner builds its prompt through the shared helper, and the Claude
    runners keep the prompt off the command line."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SRC)
        cls.fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def test_every_builder_uses_the_shared_helper(self):
        for name in ("ask_hermes_cli", "ask_hermes_cli_stream", "_build_history_prompt"):
            with self.subTest(builder=name):
                body = ast.get_source_segment(SRC, self.fns[name])
                self.assertIn("_with_recent_conversation(message)", body)
                self.assertNotIn("[:600]", body)

    def test_claude_prompt_never_rides_on_the_command_line(self):
        # Windows caps a whole command line at 32,767 chars, and the npm .cmd
        # shim mangles a prompt passed through %*, so both runners use stdin.
        for name, feed_kw in (("ask_hermes_cli", "input"), ("ask_hermes_cli_stream", "stdin")):
            with self.subTest(runner=name):
                launches = []
                for call in ast.walk(self.fns[name]):
                    if not (isinstance(call, ast.Call) and call.args
                            and isinstance(call.func, ast.Attribute)
                            and call.func.attr in ("run", "Popen")):
                        continue
                    argv = {n.id for n in ast.walk(call.args[0]) if isinstance(n, ast.Name)}
                    if "claude_exe" in argv:
                        launches.append((call, argv))
                self.assertEqual(len(launches), 1, "expected one claude launch")
                call, argv = launches[0]
                self.assertNotIn("prompt", argv)
                self.assertIn(feed_kw, {k.arg for k in call.keywords})


class RealHistoryReplayTests(unittest.TestCase):
    """Replays the block over every local pane history on this install.
    Skipped on a fresh install with no conversations yet."""

    def test_latest_conclusion_visible_in_every_pane(self):
        panes = []
        for path in sorted(USERS.glob("*/conversation_history.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            for hist in (data.values() if isinstance(data, dict) else [data]):
                if not isinstance(hist, list):
                    continue
                hist = [m for m in hist if isinstance(m, dict) and "role" in m]
                replies = [m for m in hist if m["role"] == "assistant"
                           and isinstance(m.get("content"), str) and m["content"].strip()]
                if replies:
                    panes.append((hist, replies[-1]["content"].strip()[-150:]))
        if not panes:
            self.skipTest("no local conversation history yet")
        n = len(panes)
        old_hits = sum(ending in old_block(h) for h, ending in panes)
        new_hits = sum(ending in block_of(h) for h, ending in panes)
        print(f"\n    {n} panes: latest reply's ending visible with the old rule "
              f"{old_hits}/{n}, new rule {new_hits}/{n}", file=sys.stderr)
        self.assertEqual(new_hits, n)
        self.assertLessEqual(max(len(block_of(h)) for h, _ in panes), BUDGET + HEADER_SLACK)


def _claude_exe():
    native = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    return str(native) if native.is_file() else (shutil.which("claude") or "")


class _QuietLog:
    def info(self, m): pass
    def debug(self, m): pass
    def warning(self, m): print(f"    [warn] {m}", file=sys.stderr)
    def error(self, m): print(f"    [err] {m}", file=sys.stderr)
    def exception(self, m): print(f"    [exc] {m}", file=sys.stderr)


@unittest.skipUnless(os.environ.get("DATA_LIVE_TESTS") == "1",
                     "set DATA_LIVE_TESTS=1 to run (one small Claude Code call)")
class LiveRunnerTests(unittest.TestCase):
    """The real ask_hermes_cli_stream end to end: a prompt over the Windows
    command-line cap must launch, and the model must recall a fact that sits
    at the END of its previous reply (the part the old rule cut)."""

    def test_long_prompt_launches_and_model_recalls_reply_ending(self):
        exe = _claude_exe()
        if not exe:
            self.skipTest("Claude Code CLI not installed")
        tree = ast.parse(SRC)
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "ask_hermes_cli_stream")
        run_dir = tempfile.mkdtemp(prefix="stm-live-")
        self.addCleanup(shutil.rmtree, run_dir, ignore_errors=True)
        filler = "Attached log line with nothing important in it. " * 750  # ~36K chars
        inline = "\n\n--- Attached file: big.log ---\n" + filler + "\n--- end of big.log ---\n"
        saved = []
        ns = load_block()
        ns.update({
            "OPUS_ALIAS": "haiku", "PROVIDERS": {"claude-cli": {"model": "haiku"}},
            "Path": Path, "_TOOL_LABELS": {}, "_active_cwd": lambda: run_dir,
            "_claude_effort_args": lambda: [], "_cleanup_cli_attachments": lambda d: None,
            "_crew": None, "_current_provider_id": lambda: "claude-cli",
            "_data_notification_hint": lambda: "",
            "_emit_login_needed": lambda send_sse, pid: "LOGIN NEEDED",
            "_load_soul_cli": lambda: "You are a test harness. Answer in one short line, no tools.",
            "_project_path": "", "_provider_executable": lambda pid: exe,
            "_register_active_proc": lambda p: None, "_unregister_active_proc": lambda p: None,
            "_request_attachments": threading.local(),
            "_stage_cli_attachments": lambda a: ("", [], inline),
            "_save_history": lambda h: saved.append(len(h)), "_session_turns": 0,
            "auto_recall_hint": lambda m: "", "resume_digest_hint": lambda m: "",
            "cli_console": types.SimpleNamespace(looks_logged_out=lambda provider, text: False),
            "conversation_history": [
                {"role": "user", "content": "how does memory work?"},
                {"role": "assistant", "content": narrated_reply(2500, 2500, "CODEWORD-ORCHID-58")}],
            "json": json, "os": os, "subprocess": subprocess, "tempfile": tempfile,
            "threading": threading, "log": _QuietLog(),
        })
        exec(compile(ast.get_source_segment(SRC, fn),
                     "bridge_server.py:ask_hermes_cli_stream", "exec"), ns)
        question = ("Ignore the attached log. What exact code word did your previous reply "
                    "give as the key fact? Answer with the code word only.")
        prompt_len = len(ns["_with_recent_conversation"](question)) + len(inline)
        self.assertGreater(prompt_len, 32767, "prompt must exceed the command-line cap")

        events = []
        ns["ask_hermes_cli_stream"](question, "", lambda kind, data: events.append((kind, data)))
        answer = "".join(d for k, d in events if k == "token").strip()
        print(f"\n    prompt {prompt_len:,} chars; answer: {answer!r}", file=sys.stderr)
        self.assertIn("ORCHID-58", answer)
        self.assertTrue(saved, "turn was not saved")
        self.assertEqual(ns["conversation_history"][-1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main(verbosity=2)
