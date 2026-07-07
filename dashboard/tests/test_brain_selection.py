"""
Task 6 - selection-matrix tests for the CLI-first brain-selection logic in
dashboard/bridge_server.py.

Scope (the functions under test):
  - _claude_cli_authenticated / _codex_cli_authenticated / _gemini_cli_authenticated
    the credential-file auth probes (real JSON-parsing branches exercised against
    a fake HOME).
  - _provider_authenticated  the auth-probe TTL + credential-mtime cache.
  - _brain_usable            available AND authenticated.
  - _select_default_brain    the five-branch CLI-first resolver.
  - _needs_auth_payload      the sign-in CTA gate.

Harness note: this file uses the Python standard library `unittest` +
`unittest.mock` rather than pytest. The retail bridge ships an embeddable /
store Python with no pytest installed, and adding a global pytest to a buyer
box is not acceptable, so the suite is written to run with zero third-party
dependencies:

    cd dashboard
    python -m unittest tests.test_brain_selection -v

The mocking strategy still matches the intent of the task:
  * The selection-matrix cases patch the seams
    (_provider_available / _provider_authenticated / _best_ollama_provider /
    _set_active) and drive global module state directly, so no real filesystem
    or subprocess is touched.
  * The auth-probe cases point Path.home at a temp dir and write real JSON, so
    the true parse branches (present token, refresh-only, malformed, missing)
    are exercised end to end.
  * A fixture resets all mutated module globals between every test so cases do
    not bleed into one another.
"""

import json
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


# ── Load bridge_server.py by path (it is a script-style module, not a package) ──
_HERE = Path(__file__).resolve().parent
_BRIDGE_PATH = _HERE.parent / "bridge_server.py"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_server_undertest", str(_BRIDGE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bs = _load_bridge()


class BrainSelectionTestBase(unittest.TestCase):
    """Snapshots and restores every module global the selection logic mutates so
    no test can bleed state into the next. Also isolates the on-disk provider
    state file to a temp path so _set_active's persistence never touches the real
    install's provider_state.json."""

    def setUp(self):
        # Snapshot globals we mutate.
        self._saved = {
            "ACTIVE_PROVIDER": bs.ACTIVE_PROVIDER,
            "PROVIDER_EXPLICIT": bs.PROVIDER_EXPLICIT,
            "_PENDING_CLI_AUTH": bs._PENDING_CLI_AUTH,
            "PROVIDER_STATE_FILE": bs.PROVIDER_STATE_FILE,
            "auth_cache": dict(bs._auth_probe_cache),
        }
        # Redirect the persisted-state file to a throwaway temp file.
        self._tmpdir = tempfile.TemporaryDirectory()
        bs.PROVIDER_STATE_FILE = Path(self._tmpdir.name) / "provider_state.json"
        # Start every test from a clean auth cache.
        bs._auth_probe_cache.clear()

    def tearDown(self):
        bs.ACTIVE_PROVIDER = self._saved["ACTIVE_PROVIDER"]
        bs.PROVIDER_EXPLICIT = self._saved["PROVIDER_EXPLICIT"]
        bs._PENDING_CLI_AUTH = self._saved["_PENDING_CLI_AUTH"]
        bs.PROVIDER_STATE_FILE = self._saved["PROVIDER_STATE_FILE"]
        bs._auth_probe_cache.clear()
        bs._auth_probe_cache.update(self._saved["auth_cache"])
        self._tmpdir.cleanup()

    # ── Helpers to build the world for a matrix cell ──────────────────────
    def install_world(self, available, authenticated, ollama_pid=""):
        """Patch the selection seams for one matrix cell.

        available:     set/iterable of provider ids that are "installed".
        authenticated: set/iterable of provider ids that are "signed in".
        ollama_pid:    the id _best_ollama_provider should return ("" == none).

        Returns the mock for _set_active so the test can assert selection.
        """
        avail = set(available)
        auth = set(authenticated)

        av_patch = mock.patch.object(
            bs, "_provider_available", side_effect=lambda pid: pid in avail)
        auth_patch = mock.patch.object(
            bs, "_provider_authenticated", side_effect=lambda pid: pid in auth)
        ollama_patch = mock.patch.object(
            bs, "_best_ollama_provider", return_value=ollama_pid)
        set_active_patch = mock.patch.object(bs, "_set_active")

        self._av = av_patch.start()
        self._auth = auth_patch.start()
        self._ollama = ollama_patch.start()
        m_set_active = set_active_patch.start()
        self.addCleanup(av_patch.stop)
        self.addCleanup(auth_patch.stop)
        self.addCleanup(ollama_patch.stop)
        self.addCleanup(set_active_patch.stop)

        # _set_active is mocked, so reflect its effect on ACTIVE_PROVIDER manually
        # (branch 1 re-checks ACTIVE_PROVIDER, and _needs_auth_payload reads it).
        def _apply(pid, explicit=False):
            bs.ACTIVE_PROVIDER = pid
            bs.PROVIDER_EXPLICIT = explicit
        m_set_active.side_effect = _apply
        return m_set_active


# ══════════════════════════════════════════════════════════════════════════
# GROUP A - _select_default_brain: the five-branch priority matrix
# ══════════════════════════════════════════════════════════════════════════
class TestSelectDefaultBrainBranches(BrainSelectionTestBase):

    # ── Branch 2: highest-priority authenticated CLI wins ─────────────────
    def test_branch2_claude_authenticated_wins(self):
        set_active = self.install_world(
            available={"claude-cli", "codex", "gemini"},
            authenticated={"claude-cli", "codex", "gemini"},
            ollama_pid="ollama")
        bs.PROVIDER_EXPLICIT = False
        bs._select_default_brain()
        set_active.assert_called_once_with("claude-cli")
        self.assertEqual(bs._PENDING_CLI_AUTH, "")

    def test_branch2_priority_claude_over_codex(self):
        set_active = self.install_world(
            available={"claude-cli", "codex"},
            authenticated={"claude-cli", "codex"},
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("claude-cli")

    def test_branch2_priority_codex_beats_gemini_when_claude_absent(self):
        set_active = self.install_world(
            available={"codex", "gemini"},
            authenticated={"codex", "gemini"},
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("codex")
        self.assertEqual(bs._PENDING_CLI_AUTH, "")

    def test_branch2_gemini_only_authenticated_cli(self):
        set_active = self.install_world(
            available={"gemini"},
            authenticated={"gemini"},
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("gemini")

    def test_branch2_lower_priority_wins_when_higher_unauth(self):
        # claude present but logged out, codex present AND signed in -> codex wins,
        # and because an authenticated CLI is selected, no pending-auth CTA is set.
        set_active = self.install_world(
            available={"claude-cli", "codex"},
            authenticated={"codex"},
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("codex")
        self.assertEqual(bs._PENDING_CLI_AUTH, "")

    # ── Branch 3: CLI present but logged out -> Ollama fallback + PENDING set ─
    def test_branch3_claude_loggedout_falls_back_and_flags_claude(self):
        set_active = self.install_world(
            available={"claude-cli"},
            authenticated=set(),
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("ollama")
        self.assertEqual(bs._PENDING_CLI_AUTH, "claude-cli")

    def test_branch3_codex_loggedout_flags_codex(self):
        set_active = self.install_world(
            available={"codex"},
            authenticated=set(),
            ollama_pid="ollama-small")
        bs._select_default_brain()
        set_active.assert_called_once_with("ollama-small")
        self.assertEqual(bs._PENDING_CLI_AUTH, "codex")

    def test_branch3_gemini_loggedout_flags_gemini(self):
        set_active = self.install_world(
            available={"gemini"},
            authenticated=set(),
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("ollama")
        self.assertEqual(bs._PENDING_CLI_AUTH, "gemini")

    def test_branch3_pending_is_highest_priority_loggedout_cli(self):
        # Both claude and gemini installed but logged out -> PENDING is claude
        # (highest priority), not gemini.
        set_active = self.install_world(
            available={"claude-cli", "gemini"},
            authenticated=set(),
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("ollama")
        self.assertEqual(bs._PENDING_CLI_AUTH, "claude-cli")

    # ── Branch 4: no CLI installed at all -> Ollama, PENDING stays "" ──────
    def test_branch4_no_cli_ollama_fallback_no_pending(self):
        set_active = self.install_world(
            available=set(),
            authenticated=set(),
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("ollama")
        self.assertEqual(bs._PENDING_CLI_AUTH, "")

    # ── Branch 5: nothing usable -> keep compiled-in default ──────────────
    def test_branch5_nothing_usable_keeps_default(self):
        bs.ACTIVE_PROVIDER = "claude-cli"
        set_active = self.install_world(
            available=set(),
            authenticated=set(),
            ollama_pid="")   # no ollama model pulled
        bs._select_default_brain()
        set_active.assert_not_called()
        self.assertEqual(bs.ACTIVE_PROVIDER, "claude-cli")
        self.assertEqual(bs._PENDING_CLI_AUTH, "")

    def test_branch5_loggedout_cli_but_no_ollama_keeps_default_and_flags(self):
        # CLI installed, logged out, and NO ollama model pulled: cannot fall back,
        # so keep the compiled default but still record the pending sign-in.
        bs.ACTIVE_PROVIDER = "claude-cli"
        set_active = self.install_world(
            available={"claude-cli"},
            authenticated=set(),
            ollama_pid="")
        bs._select_default_brain()
        set_active.assert_not_called()
        self.assertEqual(bs.ACTIVE_PROVIDER, "claude-cli")
        self.assertEqual(bs._PENDING_CLI_AUTH, "claude-cli")


# ══════════════════════════════════════════════════════════════════════════
# GROUP B - Branch 1: explicit buyer-choice stickiness
# ══════════════════════════════════════════════════════════════════════════
class TestExplicitChoiceStickiness(BrainSelectionTestBase):

    def test_branch1_usable_explicit_choice_is_never_overridden(self):
        # Buyer explicitly picked gemini and it is still usable. Even though claude
        # is present and authenticated (higher priority), the explicit pick sticks.
        bs.ACTIVE_PROVIDER = "gemini"
        bs.PROVIDER_EXPLICIT = True
        set_active = self.install_world(
            available={"claude-cli", "codex", "gemini"},
            authenticated={"claude-cli", "codex", "gemini"},
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_not_called()
        self.assertEqual(bs.ACTIVE_PROVIDER, "gemini")
        self.assertEqual(bs._PENDING_CLI_AUTH, "")

    def test_branch1_explicit_choice_that_became_unusable_reselects(self):
        # Buyer explicitly picked gemini, but gemini is now logged out (unusable).
        # The explicit pick must NOT stick; selection re-runs and lands on the
        # authenticated claude CLI.
        bs.ACTIVE_PROVIDER = "gemini"
        bs.PROVIDER_EXPLICIT = True
        set_active = self.install_world(
            available={"claude-cli", "gemini"},
            authenticated={"claude-cli"},     # gemini available but not authed
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("claude-cli")

    def test_branch1_explicit_choice_unavailable_falls_back_to_ollama(self):
        # Explicit gemini is entirely gone (uninstalled). No other CLI. Falls to
        # Ollama. PENDING stays "" because no CLI is present to sign into.
        bs.ACTIVE_PROVIDER = "gemini"
        bs.PROVIDER_EXPLICIT = True
        set_active = self.install_world(
            available=set(),
            authenticated=set(),
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("ollama")
        self.assertEqual(bs._PENDING_CLI_AUTH, "")

    def test_non_explicit_active_is_overridable_by_authenticated_cli(self):
        # ACTIVE is ollama but it was an AUTO selection (explicit False). A newly
        # authenticated claude CLI should upgrade it.
        bs.ACTIVE_PROVIDER = "ollama"
        bs.PROVIDER_EXPLICIT = False
        set_active = self.install_world(
            available={"claude-cli", "ollama"},
            authenticated={"claude-cli", "ollama"},
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("claude-cli")

    def test_branch1_explicit_choice_not_in_providers_reselects(self):
        # Guard the `ACTIVE_PROVIDER in PROVIDERS` clause: a stale explicit id that
        # is no longer a known provider must not stick.
        bs.ACTIVE_PROVIDER = "some-removed-provider"
        bs.PROVIDER_EXPLICIT = True
        set_active = self.install_world(
            available={"claude-cli"},
            authenticated={"claude-cli"},
            ollama_pid="ollama")
        bs._select_default_brain()
        set_active.assert_called_once_with("claude-cli")


# ══════════════════════════════════════════════════════════════════════════
# GROUP C - _brain_usable: available AND authenticated
# ══════════════════════════════════════════════════════════════════════════
class TestBrainUsable(BrainSelectionTestBase):

    def test_usable_requires_both_available_and_authenticated(self):
        cases = [
            (True, True, True),
            (True, False, False),
            (False, True, False),
            (False, False, False),
        ]
        for avail, authed, expected in cases:
            with self.subTest(available=avail, authenticated=authed):
                with mock.patch.object(bs, "_provider_available", return_value=avail), \
                     mock.patch.object(bs, "_provider_authenticated", return_value=authed):
                    self.assertEqual(bs._brain_usable("claude-cli"), expected)


# ══════════════════════════════════════════════════════════════════════════
# GROUP D - _needs_auth_payload: the sign-in CTA gate
# ══════════════════════════════════════════════════════════════════════════
class TestNeedsAuthPayload(BrainSelectionTestBase):

    def test_empty_when_no_pending_auth(self):
        bs._PENDING_CLI_AUTH = ""
        self.assertEqual(bs._needs_auth_payload(), {})

    def test_cta_returned_when_pending_and_on_ollama_fallback(self):
        bs._PENDING_CLI_AUTH = "claude-cli"
        bs.ACTIVE_PROVIDER = "ollama"
        payload = bs._needs_auth_payload()
        self.assertEqual(payload["provider_id"], "claude-cli")
        self.assertTrue(payload["on_fallback"])
        # login_cmd is sourced from CONNECTOR_CATALOG for claude-cli.
        self.assertIn("login", payload["login_cmd"].lower())
        self.assertTrue(payload["name"])

    def test_cta_on_fallback_false_when_active_is_not_ollama(self):
        # PENDING is set but the active brain is NOT an ollama fallback -> the CTA
        # still returns (non-empty) but on_fallback is False, so the UI can decide.
        bs._PENDING_CLI_AUTH = "codex"
        bs.ACTIVE_PROVIDER = "claude-cli"
        payload = bs._needs_auth_payload()
        self.assertEqual(payload["provider_id"], "codex")
        self.assertFalse(payload["on_fallback"])

    def test_cta_on_fallback_true_for_dynamic_ollama_model_id(self):
        # ACTIVE_PROVIDER startswith("ollama") also covers "ollama:qwen2.5:3b".
        bs._PENDING_CLI_AUTH = "gemini"
        bs.ACTIVE_PROVIDER = "ollama:qwen2.5:3b"
        payload = bs._needs_auth_payload()
        self.assertTrue(payload["on_fallback"])


# ══════════════════════════════════════════════════════════════════════════
# GROUP E - the credential-file auth probes (real JSON-parsing branches)
# These point Path.home at a temp dir and write real credential files.
# ══════════════════════════════════════════════════════════════════════════
class AuthProbeTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        # Patch Path.home globally for the module under test.
        self._home_patch = mock.patch.object(bs.Path, "home", return_value=self.home)
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._tmp.cleanup()

    def _write(self, rel, obj):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(obj, str):
            p.write_text(obj, encoding="utf-8")
        else:
            p.write_text(json.dumps(obj), encoding="utf-8")
        return p


class TestClaudeCliAuthenticated(AuthProbeTestBase):
    def test_missing_file_is_not_authenticated(self):
        self.assertFalse(bs._claude_cli_authenticated())

    def test_access_token_present_is_authenticated(self):
        self._write(".claude/.credentials.json",
                    {"claudeAiOauth": {"accessToken": "abc"}})
        self.assertTrue(bs._claude_cli_authenticated())

    def test_refresh_token_only_is_authenticated(self):
        # An expired accessToken auto-refreshes; a refreshToken alone still counts.
        self._write(".claude/.credentials.json",
                    {"claudeAiOauth": {"refreshToken": "r"}})
        self.assertTrue(bs._claude_cli_authenticated())

    def test_empty_oauth_block_is_not_authenticated(self):
        self._write(".claude/.credentials.json", {"claudeAiOauth": {}})
        self.assertFalse(bs._claude_cli_authenticated())

    def test_malformed_json_is_not_authenticated(self):
        self._write(".claude/.credentials.json", "{ this is not json ")
        self.assertFalse(bs._claude_cli_authenticated())


class TestCodexCliAuthenticated(AuthProbeTestBase):
    def test_missing_file_is_not_authenticated(self):
        self.assertFalse(bs._codex_cli_authenticated())

    def test_oauth_access_token_is_authenticated(self):
        self._write(".codex/auth.json", {"tokens": {"access_token": "a"}})
        self.assertTrue(bs._codex_cli_authenticated())

    def test_id_token_is_authenticated(self):
        self._write(".codex/auth.json", {"tokens": {"id_token": "i"}})
        self.assertTrue(bs._codex_cli_authenticated())

    def test_openai_api_key_is_authenticated(self):
        self._write(".codex/auth.json", {"OPENAI_API_KEY": "sk-xxx"})
        self.assertTrue(bs._codex_cli_authenticated())

    def test_empty_tokens_and_no_key_is_not_authenticated(self):
        self._write(".codex/auth.json", {"tokens": {}})
        self.assertFalse(bs._codex_cli_authenticated())

    def test_malformed_json_is_not_authenticated(self):
        self._write(".codex/auth.json", "not json at all")
        self.assertFalse(bs._codex_cli_authenticated())


class TestGeminiCliAuthenticated(AuthProbeTestBase):
    def test_missing_file_is_not_authenticated(self):
        self.assertFalse(bs._gemini_cli_authenticated())

    def test_access_token_is_authenticated(self):
        self._write(".gemini/oauth_creds.json", {"access_token": "a"})
        self.assertTrue(bs._gemini_cli_authenticated())

    def test_refresh_token_only_is_authenticated(self):
        self._write(".gemini/oauth_creds.json", {"refresh_token": "r"})
        self.assertTrue(bs._gemini_cli_authenticated())

    def test_empty_creds_is_not_authenticated(self):
        self._write(".gemini/oauth_creds.json", {})
        self.assertFalse(bs._gemini_cli_authenticated())

    def test_malformed_json_is_not_authenticated(self):
        self._write(".gemini/oauth_creds.json", "{{{")
        self.assertFalse(bs._gemini_cli_authenticated())


# ══════════════════════════════════════════════════════════════════════════
# GROUP F - _provider_authenticated: TTL + credential-mtime cache freshness
# ══════════════════════════════════════════════════════════════════════════
class TestAuthProbeCache(BrainSelectionTestBase):
    """Exercise the caching wrapper in _provider_authenticated. We patch the
    seams (_provider_executable, _cli_cred_mtime, and the underlying claude probe)
    so we can drive TTL and mtime independently of the real clock/filesystem."""

    def _patch_seams(self, probe_returns, exe="/fake/claude", mtime=100.0):
        """probe_returns: list of values the underlying claude probe yields on
        successive real calls (to prove caching skips calls)."""
        self.addCleanup(mock.patch.object(
            bs, "_provider_executable", return_value=exe).stop)
        mock.patch.object(bs, "_provider_executable", return_value=exe).start()

        self._mtime_box = {"v": mtime}
        p_mtime = mock.patch.object(
            bs, "_cli_cred_mtime", side_effect=lambda pid: self._mtime_box["v"])
        p_mtime.start()
        self.addCleanup(p_mtime.stop)

        self._probe = mock.patch.object(
            bs, "_claude_cli_authenticated", side_effect=list(probe_returns))
        self._probe_mock = self._probe.start()
        self.addCleanup(self._probe.stop)

    def test_result_is_cached_within_ttl(self):
        # claude-cli is a subprocess provider; PROVIDERS['claude-cli'].kind is
        # 'subprocess' so the auth-probe path is taken.
        self._patch_seams(probe_returns=[True, False])  # 2nd would flip if re-probed
        with mock.patch.object(bs.time, "time", return_value=1000.0):
            first = bs._provider_authenticated("claude-cli")
            second = bs._provider_authenticated("claude-cli")
        self.assertTrue(first)
        self.assertTrue(second)                 # served from cache, not re-probed
        self.assertEqual(self._probe_mock.call_count, 1)

    def test_ttl_expiry_triggers_reprobe(self):
        self._patch_seams(probe_returns=[True, False])
        times = iter([1000.0, 1000.0 + bs._AUTH_PROBE_TTL + 1])
        with mock.patch.object(bs.time, "time", side_effect=lambda: next(times)):
            first = bs._provider_authenticated("claude-cli")
            second = bs._provider_authenticated("claude-cli")
        self.assertTrue(first)
        self.assertFalse(second)                # re-probed after TTL, got new value
        self.assertEqual(self._probe_mock.call_count, 2)

    def test_credential_mtime_change_busts_cache_within_ttl(self):
        # Same instant (within TTL) but the credential file's mtime changed, e.g.
        # the buyer just ran `claude /login`. Cache must bust and re-probe.
        self._patch_seams(probe_returns=[False, True])
        with mock.patch.object(bs.time, "time", return_value=2000.0):
            first = bs._provider_authenticated("claude-cli")
            self.assertFalse(first)
            self._mtime_box["v"] = 555.0        # login rewrote the cred file
            second = bs._provider_authenticated("claude-cli")
        self.assertTrue(second)
        self.assertEqual(self._probe_mock.call_count, 2)

    def test_non_subprocess_provider_authenticated_equals_available(self):
        # For http/api providers, authenticated == available; no probe is run.
        with mock.patch.object(bs, "_provider_available", return_value=True) as av:
            self.assertTrue(bs._provider_authenticated("ollama"))
        with mock.patch.object(bs, "_provider_available", return_value=False):
            self.assertFalse(bs._provider_authenticated("ollama"))
        av.assert_called()

    def test_subprocess_provider_without_executable_is_not_authenticated(self):
        # kind == subprocess but no exe on disk -> False, and no probe attempted.
        with mock.patch.object(bs, "_provider_executable", return_value=""), \
             mock.patch.object(bs, "_claude_cli_authenticated") as probe:
            self.assertFalse(bs._provider_authenticated("claude-cli"))
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
