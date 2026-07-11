#!/usr/bin/env python3
"""
DATA - dashboard self-updater (retail)
--------------------------------------
Scans the public GitHub repo and downloads any new/changed files under
`dashboard/` into this install, applying them in place. Designed to run
unattended from a standing order (action="update_dashboard") on the retail
build, which ships as an extracted product tree with NO `.git` directory.

How it works
  1. GET the recursive git tree of the default branch (one API call). Each
     entry carries git's own blob SHA-1 for that file.
  2. For every tracked file under dashboard/, compute the SAME blob SHA of the
     local copy (sha1("blob <len>\\0" + bytes)). Missing or mismatched -> stale.
  3. Download each stale file from raw.githubusercontent.com, RE-VERIFY its blob
     SHA against GitHub's before writing (rejects truncated/corrupt downloads),
     back up the existing file, then write atomically (temp + os.replace).

Safety
  * Retail-only: if `.git` exists at the install root this is the dev clone, so
    the updater refuses to run (use `git pull` there). It can never clobber a
    working tree.
  * Scope-locked to dashboard/ ; a denylist also protects runtime/state files
    (.env, logs, provider_state.json, standing_orders.json, users/, caches).
  * Every replaced file is backed up under dashboard/.update_backups/<ts>/ so a
    bad update can be rolled back by hand.
  * Never fetches from anywhere but the pinned OWNER/REPO over HTTPS.

Usage
  python self_update.py            # apply updates in place
  python self_update.py --check    # report what would change, write nothing
  python self_update.py --dry-run  # alias for --check
"""

import os
import sys
import json
import time
import hashlib
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

# ── Pinned source of truth ───────────────────────────────────
OWNER   = "huntermixhunter"
REPO    = "D.A.T.A"
BRANCH  = "main"
SUBTREE = "dashboard/"          # only files under here are synced
USER_AGENT = "DATA-self-update/1.0"

API_TREE = f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees/{BRANCH}?recursive=1"
RAW_BASE = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}/"

# ── Layout ───────────────────────────────────────────────────
DASHBOARD_DIR = Path(__file__).parent.resolve()          # ...\DATA\dashboard
INSTALL_ROOT  = DASHBOARD_DIR.parent                     # ...\DATA
BACKUP_ROOT   = DASHBOARD_DIR / ".update_backups"

# ── Limits (runaway / abuse guards) ──────────────────────────
MAX_FILE_BYTES  = 60 * 1024 * 1024      # 60 MB per file
MAX_TOTAL_BYTES = 400 * 1024 * 1024     # 400 MB per run
HTTP_TIMEOUT    = 30

# ── Denylist: never overwrite these even if tracked under dashboard/ ──
# Runtime state the app writes itself, caches, backups, and secrets.
_DENY_EXACT = {
    "dashboard/.env",
    "dashboard/provider_state.json",
    "dashboard/standing_orders.json",
    "dashboard/daily_briefing.json",
}
_DENY_PREFIX = (
    "dashboard/users/",
    "dashboard/.update_backups/",
    "dashboard/__pycache__/",
)
_DENY_SUFFIX = (
    ".log", ".err.log", ".out.log", ".pyc",
)


def _is_git_clone() -> bool:
    """True when this install is the developer git clone (has .git)."""
    return (INSTALL_ROOT / ".git").exists()


def _git_blob_sha(data: bytes) -> str:
    """Git's own object id for a blob: sha1('blob <len>\\0' + data)."""
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode() + b"\x00")
    h.update(data)
    return h.hexdigest()


def _denied(path: str) -> bool:
    if path in _DENY_EXACT:
        return True
    if any(path.startswith(p) for p in _DENY_PREFIX):
        return True
    if any(path.endswith(s) for s in _DENY_SUFFIX):
        return True
    return False


def _http_get(url: str, timeout: int = HTTP_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_remote_tree() -> list:
    """Return the list of {path, sha} blobs under SUBTREE from GitHub."""
    raw = _http_get(API_TREE)
    doc = json.loads(raw.decode("utf-8", errors="ignore"))
    if doc.get("truncated"):
        # The recursive tree is capped by GitHub at ~100k entries; this repo is
        # far smaller, but fail loudly rather than sync a partial view.
        raise RuntimeError("GitHub returned a truncated tree; aborting to avoid a partial sync.")
    out = []
    for e in doc.get("tree", []):
        if e.get("type") != "blob":
            continue
        p = e.get("path", "")
        if not p.startswith(SUBTREE):
            continue
        if _denied(p):
            continue
        out.append({"path": p, "sha": e.get("sha", "")})
    return out


def plan_updates() -> dict:
    """Compare remote blobs to local files. Returns a plan dict."""
    remote = fetch_remote_tree()
    stale, unchanged = [], 0
    for entry in remote:
        rel_from_root = entry["path"]                       # e.g. dashboard/app.js
        local = INSTALL_ROOT / rel_from_root
        if local.exists():
            try:
                local_sha = _git_blob_sha(local.read_bytes())
            except OSError:
                local_sha = ""
            if local_sha == entry["sha"]:
                unchanged += 1
                continue
            entry["reason"] = "changed"
        else:
            entry["reason"] = "new"
        stale.append(entry)
    return {"checked": len(remote), "unchanged": unchanged, "stale": stale}


def _download_verified(entry: dict) -> bytes:
    """Download one file and confirm its git blob SHA matches GitHub's."""
    # Percent-encode each path segment, keep the slashes.
    quoted = "/".join(urllib.parse.quote(seg) for seg in entry["path"].split("/"))
    data = _http_get(RAW_BASE + quoted)
    if len(data) > MAX_FILE_BYTES:
        raise RuntimeError(f"{entry['path']} exceeds per-file cap ({len(data)} bytes)")
    got = _git_blob_sha(data)
    if got != entry["sha"]:
        raise RuntimeError(f"{entry['path']} integrity check failed "
                           f"(expected {entry['sha'][:10]}, got {got[:10]})")
    return data


def _backup_and_write(rel_from_root: str, data: bytes, backup_dir: Path) -> None:
    """Back up the existing file (if any) then write atomically."""
    dest = INSTALL_ROOT / rel_from_root
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        bak = backup_dir / rel_from_root
        bak.parent.mkdir(parents=True, exist_ok=True)
        try:
            bak.write_bytes(dest.read_bytes())
        except OSError:
            pass
    # atomic replace: write to a temp file in the same dir, then os.replace
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".upd_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# Files that require a bridge restart to take effect once changed.
_RESTART_TRIGGERS = (".py",)


def run_update(dry_run: bool = False) -> dict:
    """Main entry. Returns a summary dict (JSON-serializable)."""
    result = {
        "status": "ok",
        "repo": f"{OWNER}/{REPO}@{BRANCH}",
        "checked": 0,
        "updated": [],
        "errors": [],
        "unchanged": 0,
        "restart_required": False,
        "backup_dir": "",
        "dry_run": bool(dry_run),
        "message": "",
    }

    if _is_git_clone():
        result["status"] = "skipped"
        result["message"] = ("This is the developer git clone (.git present); "
                             "skipping self-update. Use `git pull` here.")
        return result

    try:
        plan = plan_updates()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError, ValueError) as e:
        result["status"] = "error"
        result["message"] = f"Could not read the remote tree: {e}"
        result["errors"].append(str(e))
        return result

    result["checked"]   = plan["checked"]
    result["unchanged"] = plan["unchanged"]
    stale = plan["stale"]

    if not stale:
        result["message"] = f"Up to date — {plan['checked']} dashboard file(s) checked, none changed."
        return result

    if dry_run:
        result["updated"] = [{"path": s["path"], "reason": s["reason"]} for s in stale]
        result["restart_required"] = any(s["path"].endswith(_RESTART_TRIGGERS) for s in stale)
        result["message"] = f"{len(stale)} file(s) would be updated (dry run, nothing written)."
        return result

    # Apply. One backup folder per run.
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    backup_dir = BACKUP_ROOT / ts
    total = 0
    for entry in stale:
        try:
            data = _download_verified(entry)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise RuntimeError("total download cap exceeded; stopping this run")
            _backup_and_write(entry["path"], data, backup_dir)
            result["updated"].append({"path": entry["path"], "reason": entry["reason"],
                                      "bytes": len(data)})
            if entry["path"].endswith(_RESTART_TRIGGERS):
                result["restart_required"] = True
        except Exception as e:
            result["errors"].append(f"{entry['path']}: {e}")

    if result["updated"]:
        result["backup_dir"] = str(backup_dir)
    if result["errors"]:
        result["status"] = "partial" if result["updated"] else "error"

    n = len(result["updated"])
    msg = f"Applied {n} update(s) to the dashboard."
    if result["restart_required"]:
        msg += " Restart DATA to activate the changes."
    if result["errors"]:
        msg += f" {len(result['errors'])} file(s) failed."
    result["message"] = msg
    return result


def main(argv: list) -> int:
    dry = any(a in ("--check", "--dry-run", "-n") for a in argv)
    res = run_update(dry_run=dry)
    print(json.dumps(res, indent=2))
    if res["status"] in ("error",):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
