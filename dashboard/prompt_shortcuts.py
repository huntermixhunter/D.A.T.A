"""
Saved prompt shortcuts for the dashboard slash commands.

Typing `/brief` in a chat window sends the prompt saved under "brief". The
shortcuts are stored per user on the bridge (users/<id>/prompt_shortcuts.json)
so every browser on the same bridge, including a phone over the tunnel, sees
the same list. The client side lives in slash_commands.js.

Shared by the retail dashboard (DATA/dashboard) and the private one
(LCARS/lcars-dashboard). Keep the two copies byte-identical.

Routes (both answered by handle_http, called at the top of do_GET / do_POST):
  GET  /prompt-shortcuts                          -> {"shortcuts": [...]}
  POST /prompt-shortcuts  {"action": "save",   "name", "prompt", "desc", "rename_from"?}
                          {"action": "delete", "name"}
                          {"action": "reset"}   (back to the starter set)

Why the checks matter: a saved prompt is later sent to an agent that holds
shell, file and mail tools. The bridge answers CORS *, so without these checks
any web page open in the browser could plant a shortcut (prompt injection on a
delay) or read the Captain's saved prompts. Same rules as agent_profiles.py:
  * the bridge token, when one is configured (handler._check_auth);
  * X-Data-Request: 1, which a cross-origin page cannot send without a
    preflight the bridge refuses;
  * Sec-Fetch-Site same-origin / none, and Origin, when sent, equal to Host;
  * a non-loopback Host only when the bridge has a token (defeats DNS
    rebinding a hostname the attacker owns onto this port).
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
from pathlib import Path

ROUTE = "/prompt-shortcuts"
FILE_NAME = "prompt_shortcuts.json"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_PROMPT = 8000
MAX_DESC = 120
MAX_COUNT = 100
MAX_BODY = 64 * 1024

# Starter set, used until the user saves or deletes anything. Written for any
# project folder: none of them assume a particular language or tool.
DEFAULTS = [
    {"name": "brief", "desc": "Status brief on this window's project",
     "prompt": ("Give me a short status brief on this project: what changed recently (check the git log "
                "and any uncommitted changes), what is in progress or broken, and the three most useful "
                "next steps. Keep it under 200 words and do not change any files.")},
    {"name": "review", "desc": "Review uncommitted changes, no edits",
     "prompt": ("Review the uncommitted changes in this project for bugs, security problems and anything "
                "that will break. List findings most severe first, each with file and line. Do not change "
                "any files.")},
    {"name": "tests", "desc": "Run the tests and report",
     "prompt": ("Run this project's tests and report what passed and what failed. For each failure give the "
                "most likely cause. Do not fix anything yet.")},
    {"name": "explain", "desc": "Explain a file, function or idea",
     "prompt": ("Explain {args} in plain language: what it does, how it fits into this project, and anything "
                "surprising about it.")},
    {"name": "plan", "desc": "Plan a change before building it",
     "prompt": ("Before writing any code, make a short step-by-step plan for: {args}\n\nList the files you "
                "would touch and the main risks, then wait for my go-ahead before building.")},
]

_lock = threading.Lock()


class ShortcutError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── Storage ──────────────────────────────────────────────────────────────────
def _path(user_dir: Path) -> Path:
    return Path(user_dir) / FILE_NAME


def _clean(item) -> dict:
    """Validate one shortcut. Raises ShortcutError with a user-facing reason."""
    if not isinstance(item, dict):
        raise ShortcutError("A shortcut must be an object.")
    name = str(item.get("name", "")).strip().lstrip("/").lower()
    prompt = str(item.get("prompt", "")).replace("\r\n", "\n").strip()
    desc = " ".join(str(item.get("desc", "")).split())
    if not NAME_RE.match(name):
        raise ShortcutError("Names use lowercase letters, digits, - or _, start with a letter, "
                            "and are at most 32 characters.")
    if not prompt:
        raise ShortcutError("The prompt is empty.")
    if len(prompt) > MAX_PROMPT:
        raise ShortcutError(f"The prompt is longer than {MAX_PROMPT} characters.")
    return {"name": name, "prompt": prompt, "desc": desc[:MAX_DESC]}


def load(user_dir: Path) -> list[dict]:
    """The user's shortcuts, or the starter set if they have never saved any.
    A damaged file is not overwritten here; it reads as the starter set until
    the next save replaces it."""
    try:
        raw = json.loads(_path(user_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [dict(d) for d in DEFAULTS]
    except (OSError, ValueError):
        return [dict(d) for d in DEFAULTS]
    items = raw.get("shortcuts") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return [dict(d) for d in DEFAULTS]
    out, seen = [], set()
    for it in items[:MAX_COUNT]:
        try:
            c = _clean(it)
        except ShortcutError:
            continue
        if c["name"] not in seen:
            seen.add(c["name"])
            out.append(c)
    return out


def _write(user_dir: Path, items: list[dict]) -> None:
    p = _path(user_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "shortcuts": items}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def save(user_dir: Path, item: dict, rename_from: str = "") -> list[dict]:
    c = _clean(item)
    old = str(rename_from or "").strip().lstrip("/").lower()
    with _lock:
        items = load(user_dir)
        idx = next((i for i, x in enumerate(items) if x["name"] == (old or c["name"])), -1)
        if old and old != c["name"] and any(x["name"] == c["name"] for x in items):
            raise ShortcutError(f"/{c['name']} already exists.", 409)
        if idx >= 0:
            items[idx] = c
        else:
            if len(items) >= MAX_COUNT:
                raise ShortcutError(f"There is a limit of {MAX_COUNT} shortcuts.")
            items.append(c)
        _write(user_dir, items)
        return items


def delete(user_dir: Path, name: str) -> list[dict]:
    name = str(name or "").strip().lstrip("/").lower()
    with _lock:
        items = load(user_dir)
        kept = [x for x in items if x["name"] != name]
        if len(kept) == len(items):
            raise ShortcutError(f"There is no /{name} shortcut.", 404)
        _write(user_dir, kept)
        return kept


def reset(user_dir: Path) -> list[dict]:
    with _lock:
        try:
            _path(user_dir).unlink()
        except FileNotFoundError:
            pass
        return load(user_dir)


# ── HTTP ─────────────────────────────────────────────────────────────────────
def gate(handler, env: dict) -> str:
    """'' if the request came from the dashboard page itself, else the reason."""
    if handler.headers.get("X-Data-Request") != "1":
        return "Open shortcuts from the dashboard."
    site = (handler.headers.get("Sec-Fetch-Site") or "").strip().lower()
    host = (handler.headers.get("Host") or "").strip().lower()
    origin = (handler.headers.get("Origin") or "").strip().lower()
    if site not in ("", "same-origin", "none") or (origin and urllib.parse.urlsplit(origin).netloc != host):
        return "Cross-origin shortcut requests are not allowed."
    hostname = urllib.parse.urlsplit("http://" + host).hostname or ""
    remote = env.get("REMOTE_ACCESS")
    authenticated = env.get("DATA_BRIDGE_TOKEN") or (remote and remote.remote_key())
    if hostname not in ("localhost", "127.0.0.1", "::1") and not authenticated:
        return "Shortcuts over a remote address need the bridge token set."
    return ""


def _send(handler, obj: dict, status: int = 200) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    if hasattr(handler, "send_cors"):
        handler.send_cors()
    handler.end_headers()
    handler.wfile.write(data)


def handle_http(handler, env: dict) -> bool:
    """Serve /prompt-shortcuts. Returns False for any other path, so the bridge
    carries on with its normal routing (the request body is untouched)."""
    if urllib.parse.urlsplit(handler.path).path != ROUTE:
        return False
    try:
        raw = b""
        if handler.command == "POST":
            try:
                length = int(handler.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_BODY:
                handler.close_connection = True
                raise ShortcutError("Invalid request size.", 413)
            raw = handler.rfile.read(length)
        if hasattr(handler, "_check_auth") and not handler._check_auth():
            return True   # _check_auth has already sent the 401
        reason = gate(handler, env)
        if reason:
            raise ShortcutError(reason, 403)
        user_dir = env["_user_dir"]()
        if handler.command == "GET":
            _send(handler, {"shortcuts": load(user_dir)})
            return True
        if handler.command != "POST":
            raise ShortcutError("Method not allowed.", 405)
        if (handler.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            raise ShortcutError("JSON content is required.", 415)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ShortcutError("Body must be a JSON object.")
        action = str(data.get("action", ""))
        if action == "save":
            items = save(user_dir, data, str(data.get("rename_from", "")))
        elif action == "delete":
            items = delete(user_dir, str(data.get("name", "")))
        elif action == "reset":
            items = reset(user_dir)
        else:
            raise ShortcutError("Unknown action.")
        _send(handler, {"ok": True, "shortcuts": items})
    except ShortcutError as exc:
        _send(handler, {"error": str(exc)}, exc.status)
    except (ValueError, UnicodeError):
        _send(handler, {"error": "Shortcut data could not be read. Nothing was changed."}, 400)
    except OSError:
        _send(handler, {"error": "Shortcuts could not be saved. Check that the user folder is writable."}, 500)
    return True
