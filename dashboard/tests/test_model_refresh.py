"""Tests for the boot-time model refresh (dashboard/model_refresh.py) and the
Grok Build wiring in the bridge.

Standard library only, like the rest of the suite:

    cd dashboard
    python -m unittest tests.test_model_refresh -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import model_refresh as mr  # noqa: E402


class CodexTiers(unittest.TestCase):
    def test_newest_slug_per_tier(self):
        cat = {s: {} for s in ("gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol", "gpt-6-luna",
                               "gpt-5.6-sol", "gpt-5.6-terra", "gpt-reserve")}
        self.assertEqual(mr.codex_tiers(cat),
                         {"astra": "gpt-6-astra", "sol": "gpt-6.1-sol", "luna": "gpt-6-luna"})

    def test_version_compare_is_numeric(self):
        cat = {"gpt-6.10-sol": {}, "gpt-6.9-sol": {}}
        self.assertEqual(mr.codex_tiers(cat)["sol"], "gpt-6.10-sol")

    def test_empty_catalog(self):
        self.assertEqual(mr.codex_tiers({}), {})

    def test_label(self):
        self.assertEqual(mr.codex_label("gpt-6.1-sol"), "GPT-6.1 Sol")
        self.assertEqual(mr.codex_label("gpt-6-astra"), "GPT-6 Astra")


class ClaudeNames(unittest.TestCase):
    def test_pretty(self):
        self.assertEqual(mr.claude_pretty("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(mr.claude_pretty("claude-opus-5"), "Opus 5")
        self.assertEqual(mr.claude_pretty("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(mr.claude_pretty("claude-opus-5-5[1m]"), "Opus 5.5")
        self.assertEqual(mr.claude_pretty("gpt-6-astra"), "")
        self.assertEqual(mr.claude_pretty(""), "")

    def test_state_remembers_and_reports_change(self):
        with tempfile.TemporaryDirectory() as d:
            st = mr.RefreshState(Path(d) / "s.json")
            self.assertTrue(st.note_claude("opus", "claude-opus-5-5"))
            self.assertFalse(st.note_claude("opus", "claude-opus-5-5"))   # unchanged
            self.assertFalse(st.note_claude("opus", "not-a-model"))       # rejected
            self.assertFalse(st.note_claude("claude-opus-5", "claude-opus-5"))  # pinned id, not an alias
            st.save()
            again = mr.RefreshState(Path(d) / "s.json")
            self.assertEqual(again.claude_model("opus"), "claude-opus-5-5")

    def test_corrupt_state_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text("{not json", encoding="utf-8")
            st = mr.RefreshState(p)
            self.assertEqual(st.get("claude"), {})


class GrokModels(unittest.TestCase):
    def test_parse_table_with_default_marker(self):
        out = ("MODEL            CONTEXT\n"
               "grok-4.5         256K\n"
               "* grok-4.7       256K   (default)\n"
               "grok-code-fast-1 256K\n")
        ids, default = mr.parse_grok_models(out)
        self.assertEqual(ids, ["grok-4.5", "grok-4.7", "grok-code-fast-1"])
        self.assertEqual(default, "grok-4.7")

    def test_newest_ignores_variants(self):
        self.assertEqual(mr.newest_grok(["grok-4.5", "grok-4.10", "grok-code-fast-9"]), "grok-4.10")
        self.assertEqual(mr.newest_grok(["grok-code-fast-1"]), "")

    def test_label(self):
        self.assertEqual(mr.grok_label("grok-4.7"), "Grok 4.7")

    def test_list_without_exe(self):
        self.assertEqual(mr.list_grok_models(""), ([], "", "Grok Build is not installed"))


class LocalDefaults(unittest.TestCase):
    CAT = [{"model": "qwen3.5:4b", "tools": True}, {"model": "qwen3.5:9b", "tools": True},
           {"model": "gemma3:4b", "tools": False}]

    def test_repoints_missing_defaults_to_installed(self):
        inst = {"qwen3.5:4b": 3.3, "qwen3.5:9b": 6.6, "hermes-abliterated:lcars": 6.5,
                "nomic-embed-text:latest": 0.3}
        picks = mr.pick_local_defaults(inst, self.CAT, 7.9, "qwen2.5-coder:7b", "qwen2.5:3b")
        self.assertEqual(picks, {"ollama": "qwen3.5:9b", "ollama-small": "qwen3.5:4b"})

    def test_leaves_installed_defaults_alone(self):
        inst = {"qwen2.5-coder:7b": 4.7, "qwen2.5:3b": 1.9, "qwen3.5:9b": 6.6}
        picks = mr.pick_local_defaults(inst, self.CAT, 7.9, "qwen2.5-coder:7b", "qwen2.5:3b")
        self.assertEqual(picks, {"ollama": None, "ollama-small": None})

    def test_respects_memory_limit(self):
        inst = {"qwen3.5:4b": 3.3, "qwen3.5:9b": 6.6}
        picks = mr.pick_local_defaults(inst, self.CAT, 4.0, "x", "y")
        self.assertEqual(picks["ollama"], "qwen3.5:4b")

    def test_nothing_installed(self):
        self.assertEqual(mr.pick_local_defaults({}, self.CAT, 8, "a", "b"),
                         {"ollama": None, "ollama-small": None})

    def test_never_picks_embedding_model(self):
        picks = mr.pick_local_defaults({"nomic-embed-text:latest": 0.3}, self.CAT, 8, "a", "b")
        self.assertEqual(picks, {"ollama": None, "ollama-small": None})


class CatalogValidation(unittest.TestCase):
    def _doc(self, models, **kw):
        d = {"schema": 1, "updated": "2026-11-15", "models": models}
        d.update(kw)
        return d

    def _m(self, name, size=1.0, **kw):
        e = {"model": name, "label": name, "params": "1B", "size_gb": size, "use": "chat", "blurb": "x"}
        e.update(kw)
        return e

    def test_bundled_catalog_is_valid(self):
        cat = mr.read_catalog_file(mr.BUNDLED_CATALOG)
        self.assertIsNotNone(cat)
        names = [m["model"] for m in cat["models"]]
        self.assertIn("deepseek-r1:8b", names)
        self.assertIn("qwen2.5:3b", names)   # voice-friendly built-in default stays installable
        self.assertFalse(any("cloud" in n for n in names), "cloud tags are billed")
        sizes = [m["size_gb"] for m in cat["models"]]
        self.assertEqual(sizes, sorted(sizes))

    def test_cloud_models_and_markup_params_dropped(self):
        doc = self._doc([self._m("a:1b"), self._m("b:1b"), self._m("c:1b"),
                         self._m("gpt-oss:120b-cloud"), self._m("cloudy:7b"),
                         self._m("d:1b", params="<style>"), self._m("e:1b", params="<!--")])
        cat = mr.validate_catalog(doc)
        self.assertEqual([m["model"] for m in cat["models"]], ["a:1b", "b:1b", "c:1b"])
        self.assertTrue(mr.is_cloud_model("gpt-oss:120b-cloud"))
        self.assertFalse(mr.is_cloud_model("qwen3:8b"))

    def test_bad_entries_dropped(self):
        doc = self._doc([self._m("a:1b"), self._m("b:1b"), self._m("c:1b"),
                         self._m("rm -rf /"), self._m("x:1b; calc"), self._m("big", size=5000),
                         self._m("u:1b", use="exec")])
        cat = mr.validate_catalog(doc)
        self.assertEqual([m["model"] for m in cat["models"]], ["a:1b", "b:1b", "c:1b"])

    def test_rejects_wrong_schema_or_too_few(self):
        self.assertIsNone(mr.validate_catalog(self._doc([self._m("a"), self._m("b"), self._m("c")], schema=2)))
        self.assertIsNone(mr.validate_catalog(self._doc([self._m("a"), self._m("b")])))
        self.assertIsNone(mr.validate_catalog(self._doc([self._m("a")] * 3, updated="soon")))
        self.assertIsNone(mr.validate_catalog(["not", "a", "dict"]))

    def test_choose_newest_then_remote(self):
        old = {"updated": "2026-10-01", "models": []}
        new = {"updated": "2026-11-15", "models": []}
        self.assertEqual(mr.choose_catalog(old, None, new)[1], "bundled")
        self.assertEqual(mr.choose_catalog(new, new, new)[1], "remote")
        self.assertEqual(mr.choose_catalog(None, old, None)[1], "cache")
        self.assertEqual(mr.choose_catalog(None, None, None), (None, ""))

    def test_remote_fetch_requires_https(self):
        self.assertEqual(mr.fetch_remote_catalog("http://example.com/x.json")[0], None)

    def test_remote_fetch_failure_is_soft(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            cat, err = mr.fetch_remote_catalog()
        self.assertIsNone(cat)
        self.assertIn("offline", err)


class CliConsoleGrok(unittest.TestCase):
    def setUp(self):
        import cli_console
        self.cc = cli_console

    def test_install_grok_is_script_not_npm(self):
        spec = self.cc.parse_command("install grok")
        self.assertEqual((spec["kind"], spec["family"], spec["action"]), ("script", "grok", "install"))
        argv, err = self.cc.build_argv(spec, lambda pid: "")
        self.assertEqual(err, "")
        self.assertIn("x.ai/cli/install", " ".join(argv))

    def test_grok_login_logout_version(self):
        self.assertEqual(self.cc.parse_command("grok login")["args"], ["login"])
        self.assertEqual(self.cc.parse_command("!grok logout")["args"], ["logout"])
        self.assertEqual(self.cc.parse_command("grok login --device-auth")["args"], ["login", "--device-auth"])
        self.assertEqual(self.cc.parse_command("grok --version")["args"], ["--version"])
        self.assertIsNone(self.cc.parse_command("grok rm -rf ~"))

    def test_slash_login_follows_grok_pane(self):
        self.assertEqual(self.cc.parse_command("/login", "grok")["family"], "grok")

    def test_child_env_strips_xai_key(self):
        with mock.patch.dict("os.environ", {"XAI_API_KEY": "xai-secret"}):
            self.assertNotIn("XAI_API_KEY", self.cc.child_env())

    def test_logged_out_detection(self):
        self.assertTrue(self.cc.looks_logged_out("grok", "Run `grok login` first, or set XAI_API_KEY"))
        self.assertFalse(self.cc.looks_logged_out("grok", "Here is your summary."))

    def test_install_cmd_matches_connector_shell_cmd(self):
        cmd = self.cc.grok_install_shell_cmd()
        self.assertIn("https://x.ai/cli/install", cmd)


class FailoverGrok(unittest.TestCase):
    def test_family_and_limit(self):
        import provider_failover as pf
        self.assertEqual(pf.family("grok"), "grok")
        self.assertIsNotNone(pf.match_limit("Error: rate limit exceeded (429)", "grok"))
        self.assertIsNone(pf.match_limit("All done.", "grok"))
        # Automatic backup pairs Claude and Codex only; Grok has no auto partner.
        with mock.patch.object(pf, "backup_choice", return_value=pf.BACKUP_AUTO):
            self.assertIsNone(pf.backup_for("grok"))


if __name__ == "__main__":
    unittest.main()
