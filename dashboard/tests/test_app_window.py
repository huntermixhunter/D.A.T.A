"""Tests for app_window.py, the standalone dashboard window launcher."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_window  # noqa: E402


class FlagTests(unittest.TestCase):
    def test_first_run_sets_size_and_private_profile(self):
        flags = app_window.browser_flags("http://localhost:7777/", Path("/p"), "DATA", True)
        self.assertIn("--app=http://localhost:7777/", flags)
        self.assertIn(f"--user-data-dir={Path('/p')}", flags)
        self.assertIn("--no-first-run", flags)
        self.assertTrue(any(f.startswith("--window-size=") for f in flags))

    def test_later_runs_keep_the_saved_window_size(self):
        flags = app_window.browser_flags("http://localhost:7777/", Path("/p"), "DATA", False)
        self.assertFalse(any(f.startswith("--window-size=") for f in flags))

    def test_mac_uses_open_for_a_detached_instance(self):
        with mock.patch.object(app_window.sys, "platform", "darwin"):
            cmd = app_window.launch_command("/Applications/Google Chrome.app", ["--app=x"])
        self.assertEqual(cmd, ["open", "-na", "/Applications/Google Chrome.app", "--args", "--app=x"])

    def test_windows_runs_the_browser_directly(self):
        with mock.patch.object(app_window.sys, "platform", "win32"):
            cmd = app_window.launch_command(r"C:\chrome.exe", ["--app=x"])
        self.assertEqual(cmd, [r"C:\chrome.exe", "--app=x"])


class ProfileTests(unittest.TestCase):
    def test_profiles_are_separated_by_name(self):
        self.assertNotEqual(app_window.profile_root("DATA"), app_window.profile_root("LCARS"))
        self.assertEqual(app_window.profile_root("DATA").name, "app-window")


class OpenTests(unittest.TestCase):
    def test_kill_switch_uses_the_default_browser(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "0"}), \
             mock.patch.object(app_window.webbrowser, "open") as opened, \
             mock.patch.object(app_window, "_spawn") as spawned:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_called_once_with("http://localhost:7777/")
        spawned.assert_not_called()
        self.assertIn("browser", result)

    def test_no_chromium_browser_falls_back(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "1"}), \
             mock.patch.object(app_window, "find_browser", return_value=None), \
             mock.patch.object(app_window.webbrowser, "open") as opened:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_called_once()
        self.assertIn("no Chrome or Edge", result)

    def test_spawn_failure_falls_back(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "1"}), \
             mock.patch.object(app_window, "find_browser", return_value=("chrome", "chrome")), \
             mock.patch.object(app_window, "profile_root", return_value=Path(self._tmp())), \
             mock.patch.object(app_window, "_spawn", side_effect=OSError("boom")), \
             mock.patch.object(app_window.webbrowser, "open") as opened:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_called_once()
        self.assertIn("failed", result)

    def test_app_window_launch(self):
        with mock.patch.dict(app_window.os.environ, {"DATA_APP_WINDOW": "1"}), \
             mock.patch.object(app_window, "find_browser", return_value=("edge", "msedge")), \
             mock.patch.object(app_window, "profile_root", return_value=Path(self._tmp())), \
             mock.patch.object(app_window, "_spawn") as spawned, \
             mock.patch.object(app_window.webbrowser, "open") as opened:
            result = app_window.open_app_window("http://localhost:7777/", "DATA")
        opened.assert_not_called()
        cmd = spawned.call_args[0][0]
        self.assertIn("--app=http://localhost:7777/", cmd)
        self.assertEqual(result, "edge app window")

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d


class WaitTests(unittest.TestCase):
    def test_wait_gives_up_after_timeout(self):
        # Port 9 (discard) is not served locally, so this must time out quickly.
        self.assertFalse(app_window.wait_for_bridge("http://127.0.0.1:9/", 0.2))


class DiscoveryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows discovery")
    def test_windows_finds_an_installed_browser_or_none(self):
        found = app_window.find_browser()
        if found is not None:
            label, path = found
            self.assertIn(label, ("chrome", "edge", "brave"))
            self.assertTrue(Path(path).is_file())


if __name__ == "__main__":
    unittest.main()
