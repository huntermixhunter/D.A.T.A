"""Exercise the real lifecycle startup code without starting or stopping services.

The bridge used to exit on its own after about three minutes without a
dashboard heartbeat. A missing heartbeat does not actually prove the browser
is gone: several concurrent chat and status streams can occupy every HTTP
connection the browser will open to one origin, so the heartbeat queues behind
them while work is still running. These tests pin the current contract, which
is that the bridge stays up unless DATA_LIFECYCLE_MODE=auto explicitly asks
for the old browser-coupled shutdown.

Run: python tests/test_lifecycle.py
"""

import ast
import datetime
import types
import unittest
from pathlib import Path


BRIDGE = Path(__file__).resolve().parents[1] / "bridge_server.py"
TREE = ast.parse(BRIDGE.read_text(encoding="utf-8"), filename=str(BRIDGE))
CONFIG_NAMES = {
    "_last_heartbeat", "_lifecycle_leaving", "_LIFECYCLE_GRACE_SECS",
    "_LIFECYCLE_LEAVING_GRACE_SECS", "_LIFECYCLE_MODE_ENV", "_LIFECYCLE_MODE",
}
CONFIG = [
    node for node in TREE.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id in CONFIG_NAMES
            for target in node.targets)
]
MAIN = next(
    node for node in TREE.body if isinstance(node, ast.If)
    and ast.unparse(node.test) == "__name__ == '__main__'"
)
STARTUP = next(
    node for node in MAIN.body if isinstance(node, ast.If)
    and any(isinstance(child, ast.Name) and child.id == "_LIFECYCLE_MODE"
            for child in ast.walk(node.test))
)


class EndSimulation(Exception):
    pass


def config(mode=None):
    """Evaluate the real module-level lifecycle config under a chosen env."""
    namespace = {"os": types.SimpleNamespace(
        environ={} if mode is None else {"DATA_LIFECYCLE_MODE": mode})}
    exec(compile(ast.Module(body=CONFIG, type_ignores=[]), str(BRIDGE), "exec"),
         namespace)
    return namespace


def simulate(mode=None, gap=184, leaving=False, keep_running=False):
    """Run two watcher ticks with a fake clock and a harmless shutdown recorder."""
    now = datetime.datetime(2026, 10, 29, 2, 41, 56)
    ticks = 0
    shutdowns = []

    def sleep(seconds):
        nonlocal ticks, now
        ticks += 1
        if ticks > 2:
            raise EndSimulation
        now += datetime.timedelta(seconds=seconds)

    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    namespace = config(mode)
    namespace.update({
        "datetime": types.SimpleNamespace(datetime=types.SimpleNamespace(
            now=lambda: now,
        )),
        "time": types.SimpleNamespace(sleep=sleep),
        "threading": types.SimpleNamespace(Thread=Thread),
        "log": types.SimpleNamespace(info=lambda *args, **kwargs: None),
        "print": lambda *args, **kwargs: None,
        "_do_shutdown": lambda **kwargs: shutdowns.append(kwargs),
        "REMOTE_ACCESS": types.SimpleNamespace(keep_running=keep_running),
    })
    namespace["_last_heartbeat"] = (
        None if gap is None else now - datetime.timedelta(seconds=gap)
    )
    namespace["_lifecycle_leaving"] = leaving
    try:
        exec(compile(ast.Module(body=[STARTUP], type_ignores=[]), str(BRIDGE), "exec"),
             namespace)
    except EndSimulation:
        pass
    return shutdowns


class LifecycleTests(unittest.TestCase):
    def test_missing_heartbeat_does_not_stop_bridge_by_default(self):
        self.assertEqual(simulate(gap=184), [])

    def test_tab_close_does_not_stop_bridge_by_default(self):
        self.assertEqual(simulate(gap=10, leaving=True), [])

    def test_blank_or_unknown_mode_does_not_enable_automatic_shutdown(self):
        for mode in ("", "   ", "typo"):
            with self.subTest(mode=mode):
                self.assertEqual(simulate(mode=mode), [])

    def test_explicit_daemon_survives_missing_heartbeat(self):
        self.assertEqual(simulate(mode=" DAEMON ", gap=3600), [])

    def test_explicit_auto_retains_missing_heartbeat_shutdown(self):
        self.assertEqual(simulate(mode=" AUTO "), [{"preserve_supervisor": True}])

    def test_explicit_auto_retains_tab_close_shutdown(self):
        self.assertEqual(simulate(mode="auto", gap=10, leaving=True),
                         [{"preserve_supervisor": True}])

    def test_explicit_auto_keeps_fresh_connection_alive(self):
        self.assertEqual(simulate(mode="auto", gap=0), [])

    def test_explicit_auto_waits_for_first_browser_connection(self):
        self.assertEqual(simulate(mode="auto", gap=None), [])

    def test_explicit_auto_respects_background_access(self):
        """Background Access must still hold the bridge open in auto mode."""
        self.assertEqual(simulate(mode="auto", gap=3600, keep_running=True), [])


class BackgroundAccessSwitchTests(unittest.TestCase):
    """The daemon default must not seize the user's Background Access switch.

    remote_access.js disables the control whenever the manager reports
    daemon=true, and the bridge derives that flag from _LIFECYCLE_MODE_ENV.
    Deriving it from the resolved mode instead would grey the switch out for
    every user who never asked for a headless launch.
    """

    def test_unset_env_leaves_switch_under_user_control(self):
        namespace = config()
        self.assertEqual(namespace["_LIFECYCLE_MODE"], "daemon")
        self.assertNotEqual(namespace["_LIFECYCLE_MODE_ENV"], "daemon")

    def test_explicit_daemon_launch_still_pins_switch_on(self):
        self.assertEqual(config(" DAEMON ")["_LIFECYCLE_MODE_ENV"], "daemon")

    def test_auto_launch_leaves_switch_under_user_control(self):
        self.assertNotEqual(config("auto")["_LIFECYCLE_MODE_ENV"], "daemon")


if __name__ == "__main__":
    unittest.main(verbosity=2)
