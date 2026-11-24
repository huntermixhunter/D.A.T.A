"""Task A12 (and the UI half of A14) of docs/SPEC_auto_router.md: the Auto
model choice Settings panel.

Checks the settings merge the panel relies on, that the panel's category and
reason lists stay in step with auto_router, that its copy is clean, and that
the page and the bridge actually load it. No model, network or CLI is used.

    cd dashboard
    python -m unittest tests.test_auto_settings_ui -v
"""
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_DASH = Path(__file__).resolve().parent.parent
_JS = (_DASH / "auto_settings.js").read_text(encoding="utf-8")
_CSS = (_DASH / "auto_settings.css").read_text(encoding="utf-8")
_HTML = (_DASH / "index.html").read_text(encoding="utf-8")


def _load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_server_autosettings", str(_DASH / "bridge_server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bs = _load_bridge()
ar = bs._auto_router


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.udir = Path(self.tmp.name)
        self.p = mock.patch.object(bs, "_user_dir", return_value=self.udir)
        self.p.start()
        bs._router_settings_cache.update(key=None, data=None)

    def tearDown(self):
        self.p.stop()
        bs._router_settings_cache.update(key=None, data=None)
        self.tmp.cleanup()

    def _save(self, patch):
        s, errs = ar.validate_settings(bs._merge_router_settings(patch))
        self.assertEqual(errs, [])
        bs._save_router_settings(s)
        return bs._router_settings()

    def test_null_removes_override_and_keeps_others(self):
        self._save({"model_overrides": {"claude-cli-sonnet": "strongest", "ollama": "off"}})
        s = self._save({"model_overrides": {"claude-cli-sonnet": None}})
        self.assertEqual(s["model_overrides"], {"ollama": "off"})

    def test_null_removes_floor_and_allowed(self):
        self._save({"category_floor": {"writing": "strongest"}, "allowed": {"ollama": False}})
        s = self._save({"category_floor": {"writing": None}, "allowed": {"ollama": None}})
        self.assertEqual(s["category_floor"], {})
        self.assertEqual(s["allowed"], {})

    def test_partial_post_keeps_unrelated_settings(self):
        self._save({"bias": "save", "category_floor": {"planning": "standard"}})
        s = self._save({"category_floor": {"writing": "fast"}})
        self.assertEqual(s["bias"], "save")
        self.assertEqual(s["category_floor"], {"planning": "standard", "writing": "fast"})

    def test_locked_category_still_cannot_be_lowered(self):
        _, errs = ar.validate_settings(bs._merge_router_settings({"category_floor": {"money": "fast"}}))
        self.assertTrue(errs)

    def test_settings_survive_reload(self):
        self._save({"sticky_minutes": 25, "show_model_names": True})
        bs._router_settings_cache.update(key=None, data=None)
        raw = json.loads((self.udir / bs.ROUTER_SETTINGS_NAME).read_text(encoding="utf-8"))
        self.assertEqual(raw["sticky_minutes"], 25)
        self.assertTrue(bs._router_settings()["show_model_names"])


class PanelListsTest(unittest.TestCase):
    def _js_array_ids(self, name):
        m = re.search(r"const %s = \[(.*?)\n  \];" % name, _JS, re.S)
        self.assertIsNotNone(m, name)
        return re.findall(r"\['(\w+)',", m.group(1))

    def test_categories_match_router(self):
        # Display order is the panel's own (locked kinds last); membership must match.
        ids = self._js_array_ids("CATEGORIES")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(ar.CATEGORIES))

    def test_locked_matches_router(self):
        m = re.search(r"const LOCKED = new Set\(\[(.*?)\]\)", _JS)
        self.assertEqual(set(re.findall(r"'(\w+)'", m.group(1))), set(ar.LOCKED))

    def test_reason_codes_exist_in_router_copy(self):
        m = re.search(r"const REASON = \{(.*?)\};", _JS, re.S)
        keys = re.findall(r"'?([\w.]+)'?:\s*'", m.group(1))
        self.assertTrue(keys)
        for k in keys:
            self.assertIn(k, ar.COPY, k)
        for code in ar.COPY:
            if not code.startswith("cat."):
                self.assertIn(code, keys, f"routing log has no wording for {code}")

    def test_settings_keys_are_real(self):
        for key in re.findall(r"data-auto-(?:bool|select|int)=\"(\w+)\"|toggle\('(\w+)'", _JS):
            k = key[0] or key[1]
            self.assertIn(k, ar.DEFAULT_SETTINGS, k)


class CopyAuditTest(unittest.TestCase):
    # The same file ships in retail D.A.T.A, so no private-build vocabulary.
    BANNED = ("captain", "lcars", "starfleet", "stardate", "enterprise", "make it so",
              "engage", "autonomous", "unattended", "vibe coding")

    def test_no_em_or_en_dash(self):
        for name, text in (("auto_settings.js", _JS), ("auto_settings.css", _CSS)):
            self.assertNotIn("—", text, name)
            self.assertNotIn("–", text, name)

    def test_no_banned_words(self):
        low = (_JS + _CSS).lower()
        for w in self.BANNED:
            m = re.search(r"\b%s\b" % re.escape(w), low)
            self.assertIsNone(m, f"banned word '{w}' in the panel files")


class WiringTest(unittest.TestCase):
    def test_mount_point_is_first_in_model_pane(self):
        m = re.search(r'data-pane="model">\s*<div id="auto-settings-root" hidden></div>', _HTML)
        self.assertIsNotNone(m)

    def test_script_loads_after_app_js(self):
        app = _HTML.index('<script src="app.js')
        mod = _HTML.index('<script src="auto_settings.js')
        self.assertLess(app, mod)
        self.assertIn('href="auto_settings.css', _HTML)

    def test_bridge_serves_both_files(self):
        src = (_DASH / "bridge_server.py").read_text(encoding="utf-8")
        self.assertIn('"/auto_settings.js": ("auto_settings.js"', src)
        self.assertIn('"/auto_settings.css": ("auto_settings.css"', src)


if __name__ == "__main__":
    unittest.main()
