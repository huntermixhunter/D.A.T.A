"""Test actual bridge event producers and per-client delivery without a server."""
import ast
import collections
import json
import threading
import time
import types
import unittest
from pathlib import Path


BRIDGE = Path(__file__).resolve().parents[1] / "bridge_server.py"
TREE = ast.parse(BRIDGE.read_text(encoding="utf-8"), filename=str(BRIDGE))
FUNCTIONS = {
    "_history_key", "_push_ui_event", "_drain_ui_events",
    "_handle_spawn_workspaces_marker", "tool_spawn_workspaces",
    "_handle_ask_options_marker", "_handle_send_to_pane_marker",
}
CODE = compile(ast.Module(body=[
    node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS
], type_ignores=[]), str(BRIDGE), "exec")


def fixture():
    ns = {
        "json": json, "collections": collections, "time": time,
        "_history_state": threading.local(), "_ui_events_lock": threading.Lock(),
        "_ui_clients": {}, "_ui_backlog": collections.deque(maxlen=64),
        "_UI_QUEUE_MAX": 64, "_UI_BACKLOG_TTL": 12, "_UI_CLIENT_TTL": 90,
        "PROVIDERS": {"local-test": {}},
        "log": types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None),
    }
    exec(CODE, ns)
    return ns


class PaneEventOriginTests(unittest.TestCase):
    def setUp(self):
        self.ns = fixture()
        self.pane = self.ns["_history_key"](r"C:\projects\shared", "sowner-ws3")
        self.ns["_history_state"].key = self.pane
        self.spec = [{"path": r"C:\projects\next", "provider": "local-test", "role": "Review"}]

    def event(self):
        return self.ns["_ui_backlog"][-1]

    def test_spawn_marker_retains_origin(self):
        self.ns["_handle_spawn_workspaces_marker"]({"workspaces": self.spec})
        self.assertEqual(self.event()["payload"]["pane"], self.pane)
        self.assertEqual(self.event()["payload"]["workspaces"], self.spec)

    def test_native_spawn_retains_origin(self):
        result = self.ns["tool_spawn_workspaces"](self.spec)
        self.assertIn("Queued 1", result)
        self.assertEqual(self.event()["payload"]["pane"], self.pane)

    def test_background_spawn_is_explicitly_unscoped(self):
        del self.ns["_history_state"].key
        self.ns["_handle_spawn_workspaces_marker"]({"workspaces": self.spec})
        self.assertEqual(self.event()["payload"]["pane"], "")
        self.ns["tool_spawn_workspaces"](self.spec)
        self.assertEqual(self.event()["payload"]["pane"], "")

    def test_invalid_spawn_does_not_emit(self):
        self.ns["_handle_spawn_workspaces_marker"]({"workspaces": []})
        self.ns["tool_spawn_workspaces"]([{"path": "x", "provider": "missing", "role": "Review"}])
        self.assertFalse(self.ns["_ui_backlog"])

    def test_question_and_handoff_retain_exact_origin(self):
        self.ns["_handle_ask_options_marker"]({"question": "Choose", "options": ["One", "Two"]})
        self.assertEqual(self.event()["payload"]["pane"], self.pane)
        self.ns["_handle_send_to_pane_marker"]({"to": "sowner-ws2", "message": "Review"})
        self.assertEqual(self.event()["payload"]["from_key"], self.pane)

    def test_another_client_cannot_consume_the_owners_event(self):
        drain = self.ns["_drain_ui_events"]
        drain("owner")
        drain("other")
        self.ns["_handle_spawn_workspaces_marker"]({"workspaces": self.spec})
        other = drain("other")
        owner = drain("owner")
        self.assertEqual(len(other), 1)
        self.assertEqual(owner, other)
        self.assertEqual(owner[0]["payload"]["pane"], self.pane)
        self.assertEqual(drain("owner"), [])

    def test_late_client_backlog_preserves_origin(self):
        self.ns["_handle_spawn_workspaces_marker"]({"workspaces": self.spec})
        events = self.ns["_drain_ui_events"]("late-owner")
        self.assertEqual(events[0]["payload"]["pane"], self.pane)

    def test_concurrent_threads_keep_distinct_origins(self):
        barrier = threading.Barrier(2)

        def emit(pane):
            self.ns["_history_state"].key = pane
            barrier.wait(timeout=5)
            self.ns["_handle_spawn_workspaces_marker"]({"workspaces": self.spec})

        panes = {self.pane, self.ns["_history_key"](r"C:\projects\shared", "sowner-ws2")}
        threads = [threading.Thread(target=emit, args=(pane,)) for pane in panes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual({e["payload"]["pane"] for e in self.ns["_ui_backlog"]}, panes)


if __name__ == "__main__":
    unittest.main()
