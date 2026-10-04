"""Offline regressions using production functions without starting the bridge.

Set DATA_TEST_BRIDGE to check another bridge source with the same harness.
No credentials, model calls, live histories, or services are used.
"""
import ast
import builtins
from contextlib import closing
import hashlib
import io
import json
import sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import cli_console
import logging
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.request

BRIDGE = Path(os.environ.get("DATA_TEST_BRIDGE") or Path(__file__).with_name("bridge_server.py"))
SOURCE = BRIDGE.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def functions(scope, *names):
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BRIDGE), "exec"), scope)


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scope = dict(
            time=time, threading=threading, json=json, urllib=urllib,
            _struct=struct, _hashlib=hashlib, log=Mock(),
            RECALL_INDEX_DB=Path(self.tmp.name) / "recall_index.db",
            _history_state=threading.local(), _history_key=lambda x: x,
            _index_inputs_mtime=lambda: time.time() + 10,
        )
        constants = [n for n in TREE.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and (t.id.startswith("EMBED_") or
            t.id.startswith("_EMBED_") or t.id.startswith("_embed_") or
            t.id.startswith("_recall_") or t.id in {"_REBUILD_EMBED_BUDGET_SEC", "_RECALL_DEBOUNCE_SEC"})
            for t in n.targets)]
        exec(compile(ast.Module(body=constants, type_ignores=[]), str(BRIDGE), "exec"), self.scope)
        functions(self.scope, "_ollama_reachable", "_embed_breaker_open", "_embed_note_failure",
                  "_embed_note_success", "_embed_text", "_hash_content", "_build_recall_index",
                  "_recall_rebuild_worker", "_ensure_recall_index_fresh", "_recall_search",
                  "_unpack_embedding", "_cosine", "_fts5_quote")

    def rows(self):
        return [("conversation", "test-pane", "user", "", "one", "orchard irrigation"),
                ("conversation", "test-pane", "user", "", "two", "mountain camping")]

    def test_index_path_is_not_overwritten_by_recall_setup(self):
        assignments = [n for n in TREE.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "RECALL_INDEX_DB" for t in n.targets)]
        self.assertEqual(len(assignments), 1)
        path = eval(compile(ast.Expression(assignments[0].value), str(BRIDGE), "eval"),
                    {"_user_dir": lambda: Path(self.tmp.name)})
        self.assertEqual(path, self.scope["RECALL_INDEX_DB"])

    def test_dead_embedder_stops_probing_after_three_failures(self):
        probe = self.scope["_ollama_reachable"] = Mock(return_value=False)
        with patch.object(urllib.request, "urlopen", side_effect=AssertionError("network forbidden")):
            for _ in range(100):
                self.assertIsNone(self.scope["_embed_text"]("example"))
        self.assertEqual(probe.call_count, 3)
        self.assertTrue(self.scope["_embed_breaker_open"]())

    def test_probe_uses_ipv4_and_short_timeout(self):
        with patch("socket.create_connection", side_effect=OSError("offline")) as connect:
            self.assertFalse(self.scope["_ollama_reachable"]())
        connect.assert_called_once_with(("127.0.0.1", 11434), timeout=0.35)

    def test_embedder_recovers_after_cooldown(self):
        self.scope.update(_embed_fail_count=3, _embed_breaker_until=time.monotonic() - 1,
                          _ollama_reachable=lambda: True)
        data = json.dumps({"embedding": [1.0] * self.scope["EMBED_DIMS"]}).encode()
        with patch.object(urllib.request, "urlopen", return_value=io.BytesIO(data)) as request:
            self.assertEqual(len(self.scope["_embed_text"]("example")), 768 * 4)
        self.assertEqual(request.call_args.kwargs["timeout"], 5)
        self.assertEqual(self.scope["_embed_fail_count"], 0)
        self.assertFalse(self.scope["_embed_breaker_open"]())

    def test_slow_rebuild_does_not_block_and_only_starts_once(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_build():
            entered.set()
            release.wait(3)
            return 2, 0, ""
        build = self.scope["_build_recall_index"] = Mock(side_effect=blocked_build)
        try:
            self.scope["_ensure_recall_index_fresh"]()
            self.assertTrue(entered.wait(1))
            worker = self.scope["_recall_rebuild_thread"]
            self.assertTrue(worker.is_alive())
            for _ in range(10):
                self.scope["_ensure_recall_index_fresh"]()
            self.assertEqual(build.call_count, 1)
            self.assertIs(worker, self.scope["_recall_rebuild_thread"])
        finally:
            release.set()
            worker = self.scope["_recall_rebuild_thread"]
            if worker:
                worker.join(2)
        self.scope["_ensure_recall_index_fresh"]()
        self.assertEqual(build.call_count, 1, "completed rebuild must be debounced")

    def test_embedding_budget_still_builds_complete_keyword_index(self):
        embed = self.scope["_embed_text"] = Mock(side_effect=AssertionError("budget exhausted"))
        self.scope.update(_iter_index_sources=self.rows, _REBUILD_EMBED_BUDGET_SEC=-1)
        self.assertEqual(self.scope["_build_recall_index"](), (2, 0, ""))
        embed.assert_not_called()
        with closing(sqlite3.connect(self.scope["RECALL_INDEX_DB"])) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM items").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT count(*) FROM items_fts WHERE items_fts MATCH 'orchard'").fetchone()[0], 1)

    def test_rebuild_reuses_cached_embeddings(self):
        embed = self.scope["_embed_text"] = Mock(return_value=struct.pack("768f", *([1.0] * 768)))
        self.scope["_iter_index_sources"] = self.rows
        self.assertEqual(self.scope["_build_recall_index"](), (2, 2, ""))
        self.assertEqual(self.scope["_build_recall_index"](), (2, 2, ""))
        self.assertEqual(embed.call_count, 2)

    def test_keyword_and_semantic_search_work_without_numpy(self):
        self.scope.update(_iter_index_sources=self.rows, _embed_text=lambda _: None)
        self.assertEqual(self.scope["_build_recall_index"](), (2, 0, ""))
        self.scope["_ensure_recall_index_fresh"] = lambda: None
        self.assertEqual(self.scope["_recall_search"]("orchard", 5, "all")[0]["ref"], "one")
        vector = struct.pack("768f", *([1.0] * 768))
        self.scope["_embed_text"] = lambda _: vector
        self.assertEqual(self.scope["_build_recall_index"](), (2, 2, ""))
        native_import = builtins.__import__
        def no_numpy(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("optional dependency absent")
            return native_import(name, *args, **kwargs)
        normal = self.scope["_recall_search"]("orchard", 5, "all")
        with patch("builtins.__import__", side_effect=no_numpy):
            fallback = self.scope["_recall_search"]("orchard", 5, "all")
        self.assertEqual(normal, fallback)


class CompletionTests(unittest.TestCase):
    def run_turn(self, failed=False, slow_shutdown=False):
        proc = Mock(returncode=None, stdin=io.StringIO(), stderr=io.StringIO("diagnostic"))
        proc._preempted = False
        proc._user_stopped = False
        terminal = {"type": "turn.failed", "error": {"message": "test failure"}} if failed else {
            "type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 3}}
        def stdout():
            if not failed:
                yield json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Finished."}})
            yield json.dumps(terminal)
            raise AssertionError("runner tried to read past the terminal event")
        proc.stdout = stdout()
        waits = []
        def wait(timeout=None):
            self.assertIsNotNone(timeout, "shutdown must be bounded")
            waits.append(timeout)
            if slow_shutdown and len(waits) < 3:
                raise subprocess.TimeoutExpired("fake", timeout)
            proc.returncode = -9 if slow_shutdown else 0
            return proc.returncode
        proc.wait.side_effect = wait
        scope = dict(json=json, os=os, threading=threading, log=Mock(),
                     subprocess=SimpleNamespace(Popen=Mock(return_value=proc), PIPE=subprocess.PIPE,
                                                TimeoutExpired=subprocess.TimeoutExpired),
                     _session_turns=0, _provider_executable=lambda _: "fake-codex",
                     _load_soul=lambda **_: "test soul", _build_history_prompt=lambda *_: "test prompt",
                     _current_provider_id=lambda: "codex", _current_effort=lambda: "",
                     _codex_effort_args=lambda: [], PROVIDERS={"codex": {"model": "gpt-6-astra"}},
                     _active_cwd=lambda: ".", _register_active_proc=Mock(), _unregister_active_proc=Mock(),
                     conversation_history=[], MAX_HISTORY=20, _save_history=Mock(),
                     cli_console=cli_console, _emit_login_needed=Mock(return_value="sign in"))
        functions(scope, "ask_codex_cli_stream")
        events = []
        scope["ask_codex_cli_stream"]("test", "", lambda *event: events.append(event))
        self.assertEqual(events[-1], ("done", ""))
        self.assertEqual(sum(kind == "done" for kind, _ in events), 1)
        scope["_unregister_active_proc"].assert_called_once_with(proc)
        if failed:
            self.assertEqual(scope["conversation_history"], [])
            self.assertIn(("token", "test failure"), events)
        else:
            self.assertEqual(scope["conversation_history"][-1]["content"], "Finished.")
        if slow_shutdown:
            proc.terminate.assert_called_once()
            proc.kill.assert_called_once()
            self.assertEqual(waits, [3, 2, 2])

    def test_completed_turn_does_not_wait_for_stdout_eof(self):
        self.run_turn()

    def test_failed_turn_does_not_wait_for_stdout_eof(self):
        self.run_turn(failed=True)

    def test_completed_turn_survives_forced_process_cleanup(self):
        self.run_turn(slow_shutdown=True)

    def test_partial_marker_text_is_flushed_before_done(self):
        events = []
        scope = dict(json=json, log=Mock(), _MARKER_HANDLERS={"choice": Mock()},
                     _MAX_MARKER_TAG_LEN=10, _TERMINAL_MARKERS=set())
        functions(scope, "_marker_filter_sse")
        filtered = scope["_marker_filter_sse"](lambda *event: events.append(event))
        filtered("token", "answer <")
        filtered("done", "")
        filtered.finalize()
        self.assertEqual(events, [("token", "answer "), ("token", "<"), ("done", "")])


class LoggingTests(unittest.TestCase):
    def test_rotating_log_defaults_and_noisy_library_levels(self):
        start = SOURCE.index("import logging.handlers as _logging_handlers")
        end = SOURCE.index('log = logging.getLogger("bridge")', start)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), \
                patch.object(logging, "basicConfig") as configure, patch.object(logging, "getLogger") as logger:
            scope = dict(logging=logging, os=os, LOG_FILE=Path(tmp) / "bridge.log")
            exec(SOURCE[start:end], scope)
            handler = scope["_log_handler"]
            try:
                self.assertEqual(configure.call_args.kwargs["level"], logging.INFO)
                self.assertEqual(handler.maxBytes, 25 * 1024 * 1024)
                self.assertEqual(handler.backupCount, 5)
                self.assertIn(unittest.mock.call("httpx"), logger.call_args_list)
                logger.return_value.setLevel.assert_called_with(logging.WARNING)
                handler.maxBytes = 64
                for _ in range(30):
                    handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "test log record", (), None))
                self.assertLessEqual(len(list(Path(tmp).glob("bridge.log*"))), 6)
            finally:
                handler.close()


if __name__ == "__main__":
    unittest.main()
