"""Exercise Mac setup routing with stub OS commands; no installs or network.

These checks run in Git Bash on Windows or Bash on macOS/Linux. They do not
simulate Finder, Gatekeeper, or macOS privacy dialogs.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
if os.name == "nt":
    git = shutil.which("git")
    git_bash = Path(git).parent.parent / "bin/bash.exe" if git else None
    BASH = str(git_bash) if git_bash and git_bash.is_file() else None


@unittest.skipUnless(BASH, "Bash or Git for Windows is required")
class MacFirstRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="data mac setup ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Downloads/DATA copy with spaces"
        self.log = self.base / "events"
        self.helper = self.root / "install/mac_first_run.command"
        self.write(self.root / "dashboard/bridge_server.py", "")
        self.write(self.helper, (REPO / "install/mac_first_run.command").read_text())
        self.write(
            self.root / "install/start_mac.command",
            (REPO / "install/start_mac.command").read_text(),
        )
        self.write(self.root / "install/install.sh", """#!/usr/bin/env bash
printf 'install:%s\\n' "$*" >> "$DATA_TEST_LOG"
exit "${DATA_TEST_INSTALL_EXIT:-0}"
""")
        self.write(self.root / "start_data.sh", """#!/usr/bin/env bash
printf 'launch\\n' >> "$DATA_TEST_LOG"
""")
        self.write(self.base / "mock-bin/uname", """#!/usr/bin/env bash
if [ "$1" = -s ]; then printf '%s\\n' "${DATA_TEST_OS:-Darwin}"
else printf '%s\\n' "${DATA_TEST_ARCH:-arm64}"; fi
""")
        self.write(self.base / "mock-bin/xattr", """#!/usr/bin/env bash
printf 'xattr:%s\\n' "$@" >> "$DATA_TEST_LOG"
if [ "${DATA_TEST_XATTR_EXIT:-0}" != 0 ]; then
    printf 'xattr: Operation not permitted\\n' >&2
    exit "$DATA_TEST_XATTR_EXIT"
fi
""")

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        path.chmod(0o755)

    def dmg(self):
        self.write(self.root / "MAC_ARCH.txt", "arm64\n")
        self.write(self.root / "runtime/python/bin/python3", "fixture")

    def run_helper(self, **overrides):
        env = os.environ.copy()
        env.update(overrides)
        result = subprocess.run(
            [
                BASH, "-c",
                'base="$(cd "$1" && pwd -P)"; '
                'export DATA_TEST_LOG="$base/events"; '
                'export PATH="$base/mock-bin:$PATH"; exec bash "$2"',
                "mac-setup-test", self.base.as_posix(), self.helper.as_posix(),
            ],
            env=env, input="", text=True, capture_output=True, timeout=15,
        )
        events = self.log.read_text().splitlines() if self.log.exists() else []
        return result, events

    def test_dmg_uses_offline_setup_in_path_with_spaces(self):
        self.dmg()
        result, events = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events[:2], ["xattr:-dr", "xattr:com.apple.quarantine"])
        target = events[2].removeprefix("xattr:")
        # Git Bash can map the Windows temporary folder to /tmp.
        if os.name == "nt":
            target = subprocess.check_output(
                [BASH, "-c", 'cygpath -w "$1"', "native-path", target],
                text=True, timeout=5,
            ).strip()
        self.assertEqual(Path(target).resolve(), self.root.resolve())
        self.assertEqual(events[3:], ["install:--offline", "launch"])

    def test_source_zip_runs_regular_installer_then_launches(self):
        result, events = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events[-2:], ["install:", "launch"])

    def test_quarantine_failure_stops_before_install_or_launch(self):
        self.dmg()
        result, events = self.run_helper(DATA_TEST_XATTR_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(events), 3)
        self.assertIn("Operation not permitted", result.stderr)
        self.assertNotIn("[OK]", result.stdout)

    def test_install_failure_never_launches_either_download(self):
        for packaged in (False, True):
            with self.subTest(dmg=packaged):
                if packaged:
                    self.dmg()
                result, events = self.run_helper(DATA_TEST_INSTALL_EXIT="7")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("launch", events)
                self.assertIn("Setup failed", result.stderr)
                self.log.unlink()

    def test_wrong_processor_stops_before_quarantine_change(self):
        self.dmg()
        result, events = self.run_helper(DATA_TEST_ARCH="x86_64")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        self.assertIn("different Mac processor", result.stderr)

    def test_missing_bundled_runtime_stops_before_quarantine_change(self):
        self.write(self.root / "MAC_ARCH.txt", "arm64\n")
        result, events = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        self.assertIn("runtime or launcher is missing", result.stderr)

    def test_incomplete_folder_stops_before_quarantine_change(self):
        (self.root / "dashboard/bridge_server.py").unlink()
        result, events = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        self.assertIn("folder is incomplete", result.stderr)

    def test_non_mac_does_not_modify_quarantine_or_install(self):
        result, events = self.run_helper(DATA_TEST_OS="Linux")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        self.assertIn("for macOS", result.stderr)


if __name__ == "__main__":
    unittest.main()
