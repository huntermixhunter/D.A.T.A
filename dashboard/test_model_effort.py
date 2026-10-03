"""Offline regression checks. No bridge startup, credentials, or inference."""
import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import model_capabilities as caps

BRIDGE = Path(__file__).with_name("bridge_server.py")
TREE = ast.parse(BRIDGE.read_text(encoding="utf-8"))


def bridge_scope():
    """Execute production routing helpers without starting background services."""
    scope = {"OPUS_ALIAS": "opus", "SONNET_ALIAS": "sonnet", "HAIKU_ALIAS": "haiku",
             "FABLE_ALIAS": "fable", "_CODEX_SOL_MODEL": "gpt-6-sol",
             "_CODEX_SOL_LABEL": "GPT-6 Sol", "_CODEX_CATALOG": {},
             "EFFORT_IDS": caps.EFFORT_IDS, "_model_effort_levels": caps.effort_levels,
             "effective_effort": caps.effective_effort, "ACTIVE_PROVIDER": "codex",
             "ACTIVE_EFFORT": "high", "_effort_override": threading.local(),
             "_provider_override": threading.local(),
             "_provider_available": lambda pid: True,
             "_provider_authenticated": lambda pid: True,
             "_sync_ollama_providers": lambda: None}
    functions = {"_current_provider_id", "_current_effort", "_provider_effort_levels",
                 "_effective_provider_effort", "_claude_effort_args", "_codex_effort_args",
                 "_provider_supports_effort", "_normalize_effort", "_list_providers"}
    nodes = [n for n in TREE.body if
             (isinstance(n, ast.FunctionDef) and n.name in functions) or
             (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PROVIDERS" for t in n.targets))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BRIDGE), "exec"), scope)
    return scope


class EffortTests(unittest.TestCase):
    def setUp(self):
        self.scope = bridge_scope()

    def args(self, provider, effort):
        self.scope["_provider_override"].id = provider
        self.scope["_effort_override"].level = effort
        fn = "_codex_effort_args" if provider.startswith("codex") else "_claude_effort_args"
        return self.scope[fn]()

    def test_max_passes_through_to_all_current_codex_models(self):
        for pid in ("codex", "codex-sol", "codex-luna", "codex-mini"):
            with self.subTest(provider=pid):
                self.assertEqual(self.args(pid, "max"), ["-c", "model_reasoning_effort=max"])

    def test_ultra_supported_on_astra_and_sol(self):
        for pid in ("codex", "codex-sol"):
            self.assertEqual(self.args(pid, "ultra"), ["-c", "model_reasoning_effort=ultra"])

    def test_ultra_preference_clamps_for_luna_and_restores_on_astra(self):
        self.assertEqual(self.args("codex-luna", "ultra"), ["-c", "model_reasoning_effort=max"])
        self.assertEqual(self.args("codex", "ultra"), ["-c", "model_reasoning_effort=ultra"])

    def test_claude_supported_levels(self):
        self.assertEqual(self.args("claude-cli", "max"), ["--effort", "max"])
        self.assertEqual(self.args("claude-cli-sonnet", "xhigh"), ["--effort", "xhigh"])
        self.assertEqual(self.args("claude-cli-opus-48", "high"), ["--effort", "high"])
        self.assertEqual(self.args("claude-cli-fable", "ultra"), ["--effort", "max"])

    def test_haiku_gemini_ollama_disable_effort(self):
        for pid in ("claude-cli-haiku", "gemini", "ollama", "ollama-small"):
            self.assertFalse(self.scope["_provider_supports_effort"](pid))
        self.assertEqual(self.args("claude-cli-haiku", "max"), [])

    def test_auto_overrides_global_and_absent_inherits(self):
        self.assertEqual(self.args("codex", ""), [])
        self.assertEqual(self.args("codex", None), ["-c", "model_reasoning_effort=high"])

    def test_both_http_handlers_preserve_empty_auto(self):
        assignments = [n for n in ast.walk(TREE) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id in {"_req_effort", "req_effort"} for t in n.targets)]
        self.assertEqual(len(assignments), 2)
        for assignment in assignments:
            for data, expected in (({"effort": ""}, ""), ({"effort": "auto"}, ""), ({}, None), ({"effort": "max"}, "max")):
                scope = dict(self.scope, data=data)
                result = eval(compile(ast.Expression(assignment.value), str(BRIDGE), "eval"), scope)
                self.assertEqual(result, expected)

    def test_pane_effort_does_not_leak_between_threads(self):
        barrier = threading.Barrier(2)
        results = {}
        def run(effort):
            self.scope["_effort_override"].level = effort
            barrier.wait(timeout=3)
            results[effort] = self.scope["_codex_effort_args"]()
        workers = [threading.Thread(target=run, args=(e,)) for e in ("", "max")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=4)
        self.assertEqual(results, {"": [], "max": ["-c", "model_reasoning_effort=max"]})
        self.assertEqual(self.scope["_current_effort"](), "high")

    def test_invalid_level_does_not_become_cli_argument(self):
        for value in ("invalid", "high --flag", 15):
            self.assertIsNone(self.scope["_normalize_effort"](value))

    def test_catalog_advertised_levels_take_precedence(self):
        self.scope["_CODEX_CATALOG"] = {"gpt-6-astra": {"supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh")]}}
        self.assertEqual(self.args("codex", "max"), ["-c", "model_reasoning_effort=xhigh"])

    def test_provider_metadata_agrees_with_cli_arguments(self):
        for provider in self.scope["_list_providers"]():
            pid = provider["id"]
            levels = provider["effort_levels"]
            self.assertEqual(provider["supports_effort"], len(levels) > 1)
            for level in levels:
                if not level["id"]:
                    continue
                args = self.args(pid, level["id"])
                expected = f'model_reasoning_effort={level["id"]}' if pid.startswith("codex") else level["id"]
                self.assertEqual(args[-1], expected)

    def test_gemini_model_is_passed_to_subprocess(self):
        runner = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "ask_gemini_cli_stream")
        popen = next(n for n in ast.walk(runner) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Popen")
        args = eval(compile(ast.Expression(popen.args[0]), str(BRIDGE), "eval"), dict(self.scope, exe="gemini"))
        self.assertEqual(args[args.index("--model") + 1], "auto-gemini-3")

    def test_claude_aliases_and_intentional_pins_remain(self):
        providers = self.scope["PROVIDERS"]
        for pid, model in (("claude-cli", "opus"), ("claude-cli-sonnet", "sonnet"), ("claude-cli-haiku", "haiku"), ("claude-cli-fable", "fable"), ("claude-cli-opus-48", "claude-opus-4-8")):
            self.assertEqual(providers[pid]["model"], model)


class CatalogTests(unittest.TestCase):
    def test_sol_upgrade_requires_account_catalog_entry(self):
        self.assertEqual(caps.codex_sol_model({}), "gpt-6-sol")
        self.assertEqual(caps.codex_sol_model({"gpt-6.1-sol": {}}), "gpt-6.1-sol")

    def test_missing_malformed_and_available_catalog(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_HOME": temp}):
            path = Path(temp) / "models_cache.json"
            self.assertEqual(caps.read_codex_catalog(), {})
            for invalid in ("{broken", "null", "[]", '{"models": null}'):
                path.write_text(invalid, encoding="utf-8")
                self.assertEqual(caps.read_codex_catalog(), {})
            path.write_text(json.dumps({"models": [{"slug": "gpt-6.1-sol", "visibility": "list"}, {"slug": "hidden", "visibility": "hide"}]}), encoding="utf-8")
            self.assertEqual(list(caps.read_codex_catalog()), ["gpt-6.1-sol"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
