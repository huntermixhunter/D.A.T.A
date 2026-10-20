"""Run on the bundled interpreter, against a disposable installed payload."""
import json
import os
import platform
from pathlib import Path
import sys
import threading
import urllib.request
from unittest import mock

root = Path(sys.argv[1]).resolve()
arch = sys.argv[2]
assert sys.platform == "darwin", sys.platform
assert platform.machine() == arch, platform.machine()
assert Path(sys.executable).resolve().is_relative_to(root / "runtime")
assert not (root / ".git").exists()
sys.path.insert(0, str(root / "dashboard"))
os.chdir(root / "dashboard")

import psutil
import bridge_server as bridge
import self_update as updater

assert psutil.virtual_memory().total > 0
assert not updater._is_git_clone()
assert updater.INSTALL_ROOT == root
assert (root / "dashboard/app.js").read_text(encoding="utf-8").find("/update/check") >= 0

# Exercise the actual update endpoint and disk writes without network downloads,
# AI calls, scheduler startup, or changing any source outside the test copy.
probe = "dashboard/mac-build-smoke.txt"
content = b"updated on macOS\n"
tree = [{"path": probe, "sha": updater._git_blob_sha(content)}]
private = root / "users" / "mac-smoke-private.txt"
private.parent.mkdir(exist_ok=True)
private.write_text("preserve me", encoding="utf-8")
with mock.patch("importlib.reload", side_effect=lambda module: module), mock.patch.object(updater, "fetch_remote_tree", return_value=tree):
    with bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/", timeout=15) as response:
                assert response.status == 200
                assert b"<html" in response.read().lower()
            with urllib.request.urlopen(base + "/update/check?force=1", timeout=15) as response:
                result = json.load(response)
            assert result.get("available"), result
            assert not (root / probe).exists(), "A check must not apply an update"
        finally:
            server.shutdown()
            worker.join(timeout=5)
    with mock.patch.object(updater, "_http_get", return_value=content):
        applied = updater.run_update()
    assert applied["status"] == "ok", applied
    assert (root / probe).read_bytes() == content
    assert private.read_text(encoding="utf-8") == "preserve me"
print(f"MAC PAYLOAD PASS: {arch}, HTTP startup, bundled Python, update detection/application, private-state preservation")
