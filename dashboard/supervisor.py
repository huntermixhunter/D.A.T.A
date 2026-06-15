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
                timeout=10, capture_output=True,
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
    _log("Reboot requested — killing bridge")
    _kill_bridge()
    for _ in range(30):
        if not _port_listening(BRIDGE_PORT):
            break
        time.sleep(0.2)
    time.sleep(0.4)
    _spawn_bridge()
    _log("Reboot: fresh bridge launched")


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
    try:
        server = ThreadingHTTPServer(("127.0.0.1", SUPERVISOR_PORT), Handler)
    except OSError as e:
        _log(f"Could not bind :{SUPERVISOR_PORT} ({e}) — another supervisor already running? Exiting.")
        return

    _log(f"Supervisor online at http://127.0.0.1:{SUPERVISOR_PORT}")
    if not _port_listening(BRIDGE_PORT):
        _spawn_bridge()
    else:
        _log("Bridge already listening on :7777 — not spawning a duplicate")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("Supervisor shutting down (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
