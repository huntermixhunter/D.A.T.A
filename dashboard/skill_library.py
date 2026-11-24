"""Optional Skill Library for Settings > Upgrades.

Extra skills a user can add or remove one at a time. The catalog
(skill_library_catalog.json) ships with DATA and self-updates; the skill files
themselves live in the public repo under skills_library/ and are NOT in any
download (see .gitattributes export-ignore). Clicking Install fetches only that
skill's files from the pinned GitHub repo and checks every file against
GitHub's own blob SHA before anything is written.

Installed skills carry a .data-library.json marker so DATA only ever removes a
skill it installed itself. A skill folder the user already had is shown as
"Already on this computer" and is never touched.

Also offers the one-click install of the screen-control packages (pyautogui
and Pillow), which the Mac DMG leaves out.

Endpoints (local Settings only, same gate as Advanced tools):
    GET  /upgrades/skill-library                 catalog + per-skill state
    POST /upgrades/skill-library/install         {"id": "<skill>"}
    POST /upgrades/skill-library/remove          {"id": "<skill>"}
    POST /upgrades/skill-library/screen-control  {}  (starts a background pip install)
"""

import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import install_skills
import self_update
from remote_access import SetupError, setup_allowed

HERE = Path(__file__).resolve().parent
INSTALL_ROOT = HERE.parent
CATALOG_FILE = HERE / "skill_library_catalog.json"
LOCAL_LIBRARY = INSTALL_ROOT / "skills_library"       # present only in the dev clone
BACKUP_DIR = HERE / ".skill_library_backups"
MARKER = ".data-library.json"
PREFIX = "/upgrades/skill-library"

MAX_FILES = 400
MAX_SKILL_BYTES = 25 * 1024 * 1024
TREE_TTL = 600

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 ._()+-]{0,127}$")

_lock = threading.Lock()
_tree_cache = {"at": 0.0, "entries": None}
_screen = {"running": False, "ok": None, "message": ""}
_screen_lock = threading.Lock()


# ── Catalog and state ────────────────────────────────────────

def _catalog() -> dict:
    try:
        doc = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SetupError("The skill library catalog is missing. Update DATA and try again.", 503)
    if not isinstance(doc, dict) or not isinstance(doc.get("skills"), list):
        raise SetupError("The skill library catalog could not be read.", 503)
    return doc


def _entry(skill_id: str) -> dict:
    if not isinstance(skill_id, str) or not _SAFE_NAME.match(skill_id):
        raise SetupError("Unknown skill.", 404)
    for e in _catalog()["skills"]:
        if e.get("id") == skill_id:
            return e
    raise SetupError("Unknown skill.", 404)


def _target(entry: dict) -> Path:
    name, kind, cat = entry.get("name", ""), entry.get("kind"), entry.get("category", "")
    if not _SAFE_NAME.match(name):
        raise SetupError("This catalog entry has an invalid name.", 400)
    if kind == "claude":
        base = install_skills.CLAUDE_SKILLS_DIR
        dst = base / name
    elif kind == "hermes" and _SAFE_NAME.match(cat):
        base = install_skills.HERMES_SKILLS_DIR
        dst = base / cat / name
    else:
        raise SetupError("This catalog entry has an invalid location.", 400)
    # Belt and braces: the folder must sit inside the skill folder DATA reads.
    if base.resolve() not in dst.resolve().parents:
        raise SetupError("This catalog entry has an invalid location.", 400)
    return dst


def _read_marker(dst: Path):
    try:
        doc = json.loads((dst / MARKER).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _state(entry: dict) -> str:
    """absent | installed | modified | present (a folder DATA did not install)."""
    try:
        dst = _target(entry)
    except SetupError:
        return "absent"
    if not dst.exists():
        return "absent"
    marker = _read_marker(dst)
    if not marker:
        return "present"
    return "installed" if install_skills.fingerprint(dst) == marker.get("fingerprint") else "modified"


def _screen_available() -> bool:
    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec("pyautogui") is not None
    except (ImportError, ValueError):
        return False


def listing() -> dict:
    doc = _catalog()
    skills = []
    for e in doc["skills"]:
        item = {k: e.get(k) for k in ("id", "name", "kind", "category", "group",
                                      "description", "needs", "files", "kb")}
        item["state"] = _state(e)
        skills.append(item)
    with _screen_lock:
        screen = dict(_screen)
    screen["available"] = _screen_available()
    screen["platform"] = sys.platform
    return {"groups": doc.get("groups", {}), "skills": skills, "screen_control": screen,
            "source": "local" if LOCAL_LIBRARY.is_dir() else "github"}


# ── Fetching one skill ───────────────────────────────────────

def _safe_rel(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel)
    if p.is_absolute() or not p.parts or any(not _SAFE_SEGMENT.match(s) or s in (".", "..")
                                             for s in p.parts):
        raise SetupError(f"Refusing an unsafe file name in this skill: {rel}", 502)
    return p


def _remote_tree() -> list:
    now = time.time()
    if _tree_cache["entries"] is not None and now - _tree_cache["at"] < TREE_TTL:
        return _tree_cache["entries"]
    try:
        raw = self_update._http_get(self_update.API_TREE)
        doc = json.loads(raw.decode("utf-8", errors="ignore"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise SetupError(f"Could not reach GitHub to download the skill. Check the internet connection. ({e})", 503)
    if doc.get("truncated"):
        raise SetupError("GitHub returned a partial file list. Try again later.", 503)
    entries = [{"path": e["path"], "sha": e.get("sha", ""), "size": int(e.get("size") or 0)}
               for e in doc.get("tree", [])
               if e.get("type") == "blob" and str(e.get("path", "")).startswith("skills_library/")]
    _tree_cache.update(at=now, entries=entries)
    return entries


def _stage_from_github(entry: dict, stage: Path) -> str:
    prefix = entry["path"].rstrip("/") + "/"
    files = [e for e in _remote_tree() if e["path"].startswith(prefix)]
    if not files:
        raise SetupError("This skill is not on GitHub yet. Try again after the next DATA update.", 404)
    if len(files) > MAX_FILES or sum(f["size"] for f in files) > MAX_SKILL_BYTES:
        raise SetupError("This skill is larger than the library allows.", 502)
    total = 0
    for f in files:
        rel = _safe_rel(f["path"][len(prefix):])
        try:
            data = self_update._download_verified(f)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SetupError(f"Download failed for {rel}. Try again. ({e})", 503)
        except RuntimeError as e:
            raise SetupError(f"A downloaded file failed its integrity check, so nothing was installed. ({e})", 502)
        total += len(data)
        if total > MAX_SKILL_BYTES:
            raise SetupError("This skill is larger than the library allows.", 502)
        out = stage.joinpath(*rel.parts)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return "github"


def _stage_from_local(entry: dict, stage: Path) -> str:
    src = (INSTALL_ROOT / entry["path"]).resolve()
    if LOCAL_LIBRARY.resolve() not in src.parents or not src.is_dir():
        raise SetupError("This skill is missing from the local library folder.", 404)
    shutil.copytree(src, stage, dirs_exist_ok=True, ignore=install_skills._IGNORE)
    return "local"


def install(skill_id: str) -> dict:
    entry = _entry(skill_id)
    dst = _target(entry)
    with _lock:
        state = _state(entry)
        if state == "present":
            raise SetupError(f"{entry['name']} is already on this computer and was not installed "
                             "by the library, so DATA leaves it alone.", 409)
        if state == "installed":
            return {"ok": True, "state": "installed", "message": f"{entry['name']} is already installed."}
        dst.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{entry['name']}.lib-", dir=str(dst.parent)))
        old = None
        try:
            source = (_stage_from_local if LOCAL_LIBRARY.is_dir() else _stage_from_github)(entry, stage)
            if entry["kind"] == "hermes":
                install_skills._normalize_hermes(stage)
            if not install_skills._has_manifest(stage):
                raise SetupError("The downloaded skill has no instructions file, so it was not installed.", 502)
            fp = install_skills.fingerprint(stage)
            (stage / MARKER).write_text(json.dumps({
                "id": entry["id"], "source": source, "fingerprint": fp,
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }, indent=1), encoding="utf-8")
            if dst.exists():                      # an edited copy DATA installed: replace it
                old = dst.with_name(f".{dst.name}.old-{int(time.time())}")
                os.replace(dst, old)
            try:
                os.replace(stage, dst)
            except OSError:
                if old is not None and old.exists() and not dst.exists():
                    os.replace(old, dst)          # put the previous copy back
                    old = None
                raise
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            if old is not None and old.exists():
                shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "state": "installed",
            "message": f"Installed {entry['name']}. DATA can use it from your next message."}


def remove(skill_id: str) -> dict:
    entry = _entry(skill_id)
    dst = _target(entry)
    with _lock:
        state = _state(entry)
        if state == "absent":
            return {"ok": True, "state": "absent", "message": f"{entry['name']} is not installed."}
        if state == "present":
            raise SetupError(f"{entry['name']} was not installed by the library, so DATA will not remove it.", 409)
        if state == "modified":
            # Keep the user's edits recoverable.
            keep = BACKUP_DIR / time.strftime("%Y%m%d_%H%M%S") / entry["name"]
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(keep))
            msg = f"Removed {entry['name']}. Your edited copy was saved to {keep}."
        else:
            shutil.rmtree(dst)
            msg = f"Removed {entry['name']}."
    return {"ok": True, "state": "absent", "message": msg}


# ── Screen-control packages ──────────────────────────────────

def _pip(args: list, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                           "--no-input", *args], capture_output=True, text=True, timeout=timeout)


def _screen_worker() -> None:
    ok, message = False, ""
    try:
        if importlib.util.find_spec("pip") is None:
            subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                           capture_output=True, text=True, timeout=300)
        packages = ["pyautogui", "pillow"]
        proc = _pip(packages)
        if proc.returncode != 0:
            proc = _pip(["--user", *packages])
        ok = proc.returncode == 0 and _screen_available()
        if ok:
            message = "Screen control is installed. DATA can now take screenshots and use the mouse and keyboard."
            if sys.platform == "darwin":
                message += (" The first time it acts, macOS will ask for Accessibility and Screen Recording "
                            "permission for the app running DATA (usually Terminal). Allow both in "
                            "System Settings > Privacy & Security, then restart DATA.")
        else:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
            message = f"The install did not finish: {tail[0][:300]}"
    except subprocess.TimeoutExpired:
        message = "The install timed out. Check the internet connection and try again."
    except Exception as e:
        message = f"The install did not finish: {e}"
    with _screen_lock:
        _screen.update(running=False, ok=ok, message=message)


def start_screen_control() -> dict:
    if _screen_available():
        return {"ok": True, "running": False, "message": "Screen control is already installed."}
    with _screen_lock:
        if _screen["running"]:
            return {"ok": True, "running": True, "message": "Already installing."}
        _screen.update(running=True, ok=None, message="Installing pyautogui and Pillow...")
    threading.Thread(target=_screen_worker, daemon=True, name="screen-control-install").start()
    return {"ok": True, "running": True, "message": "Installing pyautogui and Pillow..."}


# ── HTTP ─────────────────────────────────────────────────────

def _read_json(handler) -> dict:
    if handler.headers.get_content_type() != "application/json":
        raise SetupError("Use a JSON request.", 415)
    if handler.headers.get("Transfer-Encoding") or len(handler.headers.get_all("Content-Length", [])) != 1:
        raise SetupError("A single Content-Length is required.", 411)
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise SetupError("Invalid request size.") from None
    if not 0 < length <= 4096:
        raise SetupError("Request is too large or empty.", 413)
    handler.connection.settimeout(5)
    try:
        data = json.loads(handler.rfile.read(length))
    except ValueError:
        raise SetupError("Invalid JSON.") from None
    if not isinstance(data, dict):
        raise SetupError("Use a JSON object.")
    return data


def handle_http(handler) -> bool:
    path = urlsplit(handler.path).path
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return False
    try:
        if not setup_allowed(handler):
            raise SetupError("Open Settings on the computer running DATA to manage skills.", 403)
        if hasattr(handler, "_check_auth") and not handler._check_auth():
            return True
        action = path[len(PREFIX):].strip("/")
        if handler.command == "GET" and action == "":
            payload = listing()
        elif handler.command == "POST" and action in ("install", "remove"):
            data = _read_json(handler)
            payload = (install if action == "install" else remove)(data.get("id", ""))
        elif handler.command == "POST" and action == "screen-control":
            _read_json(handler)
            payload = start_screen_control()
        else:
            raise SetupError("Unknown skill library endpoint.", 404)
        status = 200
    except SetupError as exc:
        status, payload = exc.status, {"error": str(exc)}
    except OSError as exc:
        status, payload = 503, {"error": f"The skill folder could not be changed. Check permissions and disk space. ({exc})"}
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)
    return True
