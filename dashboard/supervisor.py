"""
DATA Supervisor — the one process that stays alive when the bridge doesn't.

The bridge (bridge_server.py, port 7777) serves the dashboard itself. When it
dies — a crash, or the lifecycle watcher shutting it down after the browser
stops sending heartbeats — the dashboard windows are still open, but every
fetch to :7777 fails and the page reads OFFLINE. A browser can't relaunch an OS
process, so DATA had to be restarted by hand.

The supervisor is a tiny, always-on HTTP control server on 127.0.0.1:7766 whose
only job is to (re)launch the bridge. The dashboard's REBOOT button hits THIS,
not the dead bridge — so one click brings DATA back online without losing the
open windows.

Endpoints (localhost-only, CORS-open so the :7777 page can call across ports):
  GET  /ping    -> {"ok": true, "bridge_up": <bool>}
  POST /reboot  -> kills whatever holds :7777, relaunches the bridge

Launched by start_data.bat (which the installer writes); on startup it spawns
the bridge once, then idles. Portable: uses the same interpreter that launched
it, and the bridge sitting next to this file.
"""
import json
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE            = Path(__file__).resolve().parent
BRIDGE          = HERE / "bridge_server.py"
LOG             = HERE.parent / "bridge.log"
SUPERVISOR_PORT = 7766
BRIDGE_PORT     = 7777

# ── Crash self-heal ──────────────────────────────────────────
# A background watcher polls the bridge port. If it goes dark UNEXPECTEDLY — a
# crash, an OOM kill, a wedge — we relaunch it so a random bridge death heals
# itself instead of leaving the user on an OFFLINE page. A DELIBERATE shutdown
# (SYSTEM OFFLINE, or the browser-disconnect lifecycle) is told apart by a
# sentinel file the bridge writes on its way down: while it exists, the watcher
# stays its hand, so we never fight an intentional shutdown.
WATCH_INTERVAL_SECS = 5     # seconds between bridge-port polls
WATCH_MISS_LIMIT    = 2     # consecutive dark polls before respawn (~10s)
OFFLINE_SENTINEL    = HERE / ".data_offline"   # present ⇒ deliberate shutdown

# Shared with the watcher thread.
_reboot_in_progress = False   # true during /reboot so the watcher does not double-spawn
_bridge_seen_up     = False   # latches true once the bridge has answered at least once

# Reuse the interpreter that launched us — portable across installs (no
# hardcoded paths). On Windows this is whatever start_data.bat invoked.
PYTHON    = Path(sys.executable)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IS_WIN    = os.name == "nt"


def _log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[Supervisor] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _clear_offline_sentinel(reason: str = "") -> None:
    """Remove the deliberate-shutdown marker. Called on a fresh supervisor
    launch and on /reboot — both mean we are intentionally bringing DATA up, so
    the crash-watcher should be free to keep the bridge alive again."""
    try:
        if OFFLINE_SENTINEL.exists():
            OFFLINE_SENTINEL.unlink()
            _log(f"Cleared offline sentinel{f' ({reason})' if reason else ''}")
    except Exception as e:
        _log(f"clear sentinel error: {e}")


def _bridge_watcher() -> None:
    """Self-heal loop. Respawns the bridge if its port goes dark with no offline
    sentinel present — i.e. a crash, not a deliberate shutdown. Acts only after
    the bridge has been seen alive at least once this supervisor lifetime, so
    `--watch-only` at logon never forces up a bridge nobody launched."""
    global _bridge_seen_up
    misses = 0
    while True:
        time.sleep(WATCH_INTERVAL_SECS)
        if _reboot_in_progress:
            misses = 0
            continue
        if _port_listening(BRIDGE_PORT):
            _bridge_seen_up = True
            misses = 0
            continue
        # Port is dark.
        if not _bridge_seen_up:
            continue                      # never started yet — nothing to restore
        if OFFLINE_SENTINEL.exists():
            misses = 0
            continue                      # deliberate shutdown — leave it down
        misses += 1
        if misses >= WATCH_MISS_LIMIT:
            _log("Bridge port dark, no offline sentinel — auto-restarting (crash recovery)")
            _spawn_bridge()
            misses = 0
            time.sleep(3)                 # let the fresh bridge bind before re-checking


def _kill_bridge() -> None:
    """Stop whatever is holding :7777 — the bridge_server.py process — without
    ever killing this supervisor.py. Platform-specific so it works on the
    Windows desktop and a Linux/Mac install alike."""
    if IS_WIN:
        ps_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process | Where-Object {
    $c = $_.CommandLine
    $c -and ($c -match 'bridge_server\.py') -and ($c -notmatch 'supervisor\.py')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-NetTCPConnection -LocalPort 7777 -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force }
"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                timeout=10, capture_output=True, creationflags=NO_WINDOW,
            )
        except Exception as e:
            _log(f"kill_bridge (win) error: {e}")
    else:
        # pkill matches the full command line; -f bridge_server.py won't touch
        # this supervisor.py process.
        try:
            subprocess.run(["pkill", "-f", "bridge_server.py"], timeout=10, capture_output=True)
        except Exception as e:
            _log(f"kill_bridge (posix) error: {e}")


def _spawn_bridge() -> None:
    _log(f"Launching bridge via {PYTHON.name}: {BRIDGE}")
    subprocess.Popen(
        [str(PYTHON), str(BRIDGE)],
        cwd=str(HERE),
        creationflags=NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _reboot_bridge() -> None:
    """Hard restart: kill the old bridge, wait for the port to free, relaunch."""
    global _reboot_in_progress
    _reboot_in_progress = True
    try:
        _log("Reboot requested — killing bridge")
        # A manual reboot is a deliberate bring-back — clear any offline marker
        # so the watcher resumes keeping the bridge alive.
        _clear_offline_sentinel("manual reboot")
        _kill_bridge()
        for _ in range(30):
            if not _port_listening(BRIDGE_PORT):
                break
            time.sleep(0.2)
        time.sleep(0.4)
        _spawn_bridge()
        _log("Reboot: fresh bridge launched")
    finally:
        # Settle window so the crash-watcher does not also fire during the gap
        # while the fresh bridge is still binding the port.
        time.sleep(3)
        _reboot_in_progress = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path.split("?")[0] == "/ping":
            self._send(200, {"ok": True, "bridge_up": _port_listening(BRIDGE_PORT)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] == "/reboot":
            import threading
            threading.Thread(target=_reboot_bridge, daemon=True).start()
            self._send(200, {"ok": True, "rebooting": True})
        else:
            self._send(404, {"error": "not found"})


def main():
    # `--watch-only` (used by an at-logon autostart): stand up the control server
    # + crash-watcher but do NOT spawn the bridge at startup. The bridge comes up
    # when the user launches DATA (or presses REBOOT); the watcher then keeps it
    # alive. Without the flag (the normal launcher path) we spawn it immediately.
    watch_only = "--watch-only" in sys.argv[1:]

    try:
        server = ThreadingHTTPServer(("127.0.0.1", SUPERVISOR_PORT), Handler)
    except OSError as e:
        _log(f"Could not bind :{SUPERVISOR_PORT} ({e}) — another supervisor already running? Exiting.")
        return

    _log(f"Supervisor online at http://127.0.0.1:{SUPERVISOR_PORT}"
         + (" (watch-only)" if watch_only else ""))

    # A fresh supervisor launch is a deliberate bring-up — clear any stale
    # offline marker left by a prior shutdown so the watcher is free to heal.
    _clear_offline_sentinel("supervisor start")

    # Crash self-heal watcher (daemon so it dies with the supervisor).
    import threading
    threading.Thread(target=_bridge_watcher, daemon=True).start()

    if watch_only:
        _log("watch-only: not spawning bridge at startup — waiting for launch/reboot")
    elif not _port_listening(BRIDGE_PORT):
        _spawn_bridge()
    else:
        _log("Bridge already listening on :7777 — not spawning a duplicate")

    # serve_forever() can raise on resume-from-sleep when the listening socket
    # is briefly invalid. Re-enter it so the reboot service stays available
    # after the machine wakes (capped so a truly dead socket can't tight-loop).
    consecutive = 0
    while consecutive < 5:
        try:
            server.serve_forever()
            break
        except KeyboardInterrupt:
            _log("Supervisor shutting down (KeyboardInterrupt)")
            break
        except Exception as e:
            consecutive += 1
            _log(f"serve_forever error ({e}) — restarting listener {consecutive}/5 in 1s")
            time.sleep(1)


if __name__ == "__main__":
    main()
