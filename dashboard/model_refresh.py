"""Boot-time model refresh: keeps DATA's brain list pointed at the newest
models each provider actually offers, without a new release.

Every source is free and read-only:

  * Codex      ~/.codex/models_cache.json, which the Codex CLI keeps current
               for the signed-in account. The newest Astra / Sol / Luna slug
               in it wins.
  * Claude     The CLI's `init` stream event names the real model behind the
               "opus" / "sonnet" / "haiku" / "fable" aliases on every turn.
               DATA remembers the last one it saw, so the picker can say
               "Opus 5.5" instead of just "latest". The alias itself is still
               what DATA sends, so a new Claude release rides in on its own.
  * Grok       `grok models` lists the models the signed-in xAI account can
               use. Listing models generates nothing, so it costs nothing.
  * Ollama     The models already downloaded on this machine. The two
               built-in local brains are re-pointed at installed models when
               their compiled-in defaults are missing.
  * Download   A small JSON list of recommended local models, published in the
    list       DATA GitHub repo. It is data only: model names, sizes and blurbs.
               It can never carry a command. A bundled copy is the fallback
               when the machine is offline.

This module holds only pure logic plus a small persisted state file. The
bridge applies the results to its PROVIDERS table.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

CATALOG_URL = ("https://raw.githubusercontent.com/huntermixhunter/D.A.T.A/main/"
               "dashboard/model_catalog.json")
BUNDLED_CATALOG = Path(__file__).resolve().parent / "model_catalog.json"
CATALOG_SCHEMA = 1
_CATALOG_MAX_BYTES = 256 * 1024
_CATALOG_MAX_MODELS = 80


# ── Codex ───────────────────────────────────────────────────────────────────

_CODEX_SLUG_RE = re.compile(r"^gpt-(\d+(?:\.\d+)*)-(astra|sol|luna)$")


def _version_tuple(text: str) -> tuple:
    return tuple(int(p) for p in text.split(".") if p.isdigit())


def codex_tiers(catalog: dict) -> dict:
    """{'astra': slug, 'sol': slug, 'luna': slug}: the newest listed slug per
    tier. `catalog` is read_codex_catalog() output, which already drops hidden
    models, so an account only gets a tier the moment Codex offers it."""
    best: dict = {}
    for slug in catalog or {}:
        m = _CODEX_SLUG_RE.match(str(slug))
        if not m:
            continue
        ver, tier = _version_tuple(m.group(1)), m.group(2)
        if tier not in best or ver > best[tier][0]:
            best[tier] = (ver, slug)
    return {tier: slug for tier, (_, slug) in best.items()}


def codex_label(slug: str) -> str:
    """'gpt-6.1-sol' -> 'GPT-6.1 Sol'."""
    m = _CODEX_SLUG_RE.match(slug or "")
    if not m:
        return slug or ""
    return f"GPT-{m.group(1)} {m.group(2).capitalize()}"


# ── Claude ──────────────────────────────────────────────────────────────────

CLAUDE_ALIASES = ("opus", "sonnet", "haiku", "fable")
_CLAUDE_ID_RE = re.compile(
    r"^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")


def claude_pretty(model_id: str) -> str:
    """'claude-opus-5-5' -> 'Opus 5.5'; 'claude-haiku-4-5-20251001' -> 'Haiku
    4.5'. Returns '' for anything that is not a recognisable Claude id."""
    mid = re.sub(r"\[.*?\]$", "", str(model_id or "").strip().lower())
    m = _CLAUDE_ID_RE.match(mid)
    if not m:
        return ""
    ver = m.group(2) + (f".{m.group(3)}" if m.group(3) else "")
    return f"{m.group(1).capitalize()} {ver}"


# ── Grok ────────────────────────────────────────────────────────────────────

_GROK_ID_RE = re.compile(r"\bgrok-[a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?\b", re.I)
_GROK_NUMBERED_RE = re.compile(r"^grok-(\d+(?:\.\d+)*)$")


def parse_grok_models(text: str) -> tuple[list, str]:
    """Parse `grok models` output into (model ids in listed order, default id
    or ''). The output format is not documented, so this only trusts ids that
    look like grok-*, and treats a line marked 'default', '*' or a check/arrow
    glyph as the default."""
    ids: list = []
    default = ""
    for line in (text or "").splitlines():
        found = [m.group(0).lower() for m in _GROK_ID_RE.finditer(line)]
        if not found:
            continue
        for mid in found:
            if mid not in ids:
                ids.append(mid)
        stripped = line.strip()
        if not default and (re.search(r"\bdefault\b|\(current\)|\bactive\b", stripped, re.I)
                            or stripped[:1] in ("*", "✓", "→", "●", ">")):
            default = found[0]
    return ids, default


def newest_grok(ids: list) -> str:
    """Highest plainly-numbered model ('grok-4.7' beats 'grok-4.6'). Variant ids
    such as 'grok-code-fast-1' are listed but never auto-picked, because their
    limits and strengths differ from the flagship line."""
    best, best_ver = "", ()
    for mid in ids or []:
        m = _GROK_NUMBERED_RE.match(mid)
        if m:
            ver = _version_tuple(m.group(1))
            if ver > best_ver:
                best, best_ver = mid, ver
    return best


def grok_label(model_id: str) -> str:
    """'grok-4.7' -> 'Grok 4.7'."""
    m = _GROK_NUMBERED_RE.match(model_id or "")
    if m:
        return f"Grok {m.group(1)}"
    return (model_id or "").replace("grok-", "Grok ").replace("-", " ").strip()


def list_grok_models(exe: str, env: dict | None = None, timeout: float = 25.0) -> tuple[list, str, str]:
    """Run `grok models`. Returns (ids, default, error). Never raises."""
    if not exe:
        return [], "", "Grok Build is not installed"
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        r = subprocess.run([exe, "models", "--no-auto-update"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=env, cwd=str(Path.home()), **kw)
        if r.returncode != 0 and "no-auto-update" in (r.stderr or ""):
            # Older builds may not accept the flag on this subcommand.
            r = subprocess.run([exe, "models"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout,
                               env=env, cwd=str(Path.home()), **kw)
    except subprocess.TimeoutExpired:
        return [], "", "grok models timed out"
    except OSError as e:
        return [], "", f"grok models failed to start: {e}"
    ids, default = parse_grok_models(r.stdout)
    if not ids:
        err = (r.stderr or r.stdout or "").strip()[-300:] or f"exit {r.returncode}"
        return [], "", err
    return ids, default, ""


# ── Ollama defaults ─────────────────────────────────────────────────────────

def _is_chat_model(name: str) -> bool:
    n = (name or "").lower()
    return bool(n) and "embed" not in n


def pick_local_defaults(installed: dict, catalog: list, mem_limit_gb: float,
                        current_main: str, current_small: str) -> dict:
    """Choose models for the two built-in local brains.

    installed     {model name: size in GB (0 when unknown)}
    catalog       the active download list (used to prefer known-good models)
    mem_limit_gb  VRAM when a GPU is present, otherwise a share of RAM
    Returns {'ollama': name or None, 'ollama-small': name or None}. A slot whose
    current model is installed is left alone (None), so a deliberate choice is
    never overridden.
    """
    known = {m["model"] for m in catalog or [] if m.get("tools", True)}
    cands = [(n, float(s or 0)) for n, s in (installed or {}).items() if _is_chat_model(n)]
    if not cands:
        return {"ollama": None, "ollama-small": None}
    # Curated, tool-capable catalog models first; then anything else.
    def _known(name: str) -> bool:
        return name in known or (name.endswith(":latest") and name[:-7] in known)
    preferred = [c for c in cands if _known(c[0])] or cands
    out = {"ollama": None, "ollama-small": None}

    main = current_main if current_main in installed else None
    if main is None:
        fitting = [c for c in preferred if not mem_limit_gb or c[1] <= mem_limit_gb]
        pool = fitting or preferred
        main = max(pool, key=lambda c: (c[1], c[0]))[0]
        out["ollama"] = main
    if current_small not in installed:
        others = [c for c in preferred if c[0] != main] or preferred
        out["ollama-small"] = min(others, key=lambda c: (c[1], c[0]))[0]
    return out


# ── Recommended local-model catalog ─────────────────────────────────────────

_MODEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,63}(?::[a-z0-9][a-z0-9._\-]{0,31})?$")
_USES = {"chat", "code", "reason", "vision"}


_PARAMS_RE = re.compile(r"^\d+(?:\.\d+)?[BMK]?$")


def is_cloud_model(name: str) -> bool:
    """True for Ollama's hosted models (e.g. `gpt-oss:120b-cloud`). Those run
    on Ollama's servers and can bill, so DATA never offers them as a free
    local download."""
    return "cloud" in (name or "").lower()


def _clean_entry(e) -> dict | None:
    if not isinstance(e, dict):
        return None
    model = e.get("model")
    if not isinstance(model, str) or not _MODEL_NAME_RE.match(model) or is_cloud_model(model):
        return None
    try:
        size = float(e.get("size_gb"))
    except (TypeError, ValueError):
        return None
    if not 0 < size < 1000:
        return None
    use = e.get("use", "chat")
    if use not in _USES:
        return None
    label = e.get("label")
    params = e.get("params", "")
    blurb = e.get("blurb", "")
    if not (isinstance(label, str) and 0 < len(label) <= 60):
        return None
    if not (isinstance(params, str) and (params == "" or _PARAMS_RE.match(params))):
        return None
    if not (isinstance(blurb, str) and len(blurb) <= 200):
        return None
    return {"model": model, "label": label, "params": params,
            "size_gb": round(size, 1), "use": use, "blurb": blurb,
            "tools": bool(e.get("tools", False))}


def validate_catalog(data) -> dict | None:
    """Strictly validate a catalog document. Bad entries are dropped; a
    document with fewer than three good entries is rejected outright. Returns
    {'updated': 'YYYY-MM-DD', 'models': [...] sorted small to large} or None."""
    if not isinstance(data, dict) or data.get("schema") != CATALOG_SCHEMA:
        return None
    updated = data.get("updated", "")
    if not (isinstance(updated, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated)):
        return None
    raw = data.get("models")
    if not isinstance(raw, list):
        return None
    models, seen = [], set()
    for e in raw[:_CATALOG_MAX_MODELS]:
        c = _clean_entry(e)
        if c and c["model"] not in seen:
            seen.add(c["model"])
            models.append(c)
    if len(models) < 3:
        return None
    models.sort(key=lambda m: (m["size_gb"], m["model"]))
    return {"updated": updated, "models": models}


def read_catalog_file(path: Path) -> dict | None:
    try:
        raw = Path(path).read_bytes()
        if len(raw) > _CATALOG_MAX_BYTES:
            return None
        return validate_catalog(json.loads(raw.decode("utf-8")))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def fetch_remote_catalog(url: str = CATALOG_URL, timeout: float = 6.0) -> tuple[dict | None, str]:
    """Download and validate the published catalog. Returns (catalog, error)."""
    if not url.startswith("https://"):
        return None, "catalog URL must be https"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DATA-model-refresh"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_CATALOG_MAX_BYTES + 1)
        if len(raw) > _CATALOG_MAX_BYTES:
            return None, "catalog too large"
        cat = validate_catalog(json.loads(raw.decode("utf-8")))
        return (cat, "") if cat else (None, "catalog failed validation")
    except Exception as e:   # network, HTTP 404, JSON: all just mean "use a fallback"
        return None, f"{type(e).__name__}: {e}"[:200]


def choose_catalog(remote: dict | None, cached: dict | None, bundled: dict | None) -> tuple[dict | None, str]:
    """Newest valid catalog wins; on a tie remote beats cache beats bundled.
    That way a fresh app update (newer bundled copy) is never shadowed by an
    older cached download."""
    best, best_src = None, ""
    for src, cat in (("remote", remote), ("cache", cached), ("bundled", bundled)):
        if cat and (best is None or cat["updated"] > best["updated"]):
            best, best_src = cat, src
    return best, best_src


# ── Persisted state ─────────────────────────────────────────────────────────

class RefreshState:
    """Small JSON file holding what the refresh has learned. Thread-safe."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()   # one writer of the .tmp file at a time
        self.data = {"claude": {}, "grok": {}, "catalog": {}, "last_run": 0,
                     "last_network_run": 0, "changes": [], "errors": {}}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for k in self.data:
                    if isinstance(loaded.get(k), type(self.data[k])):
                        self.data[k] = loaded[k]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with self._lock:
            snapshot = json.dumps(self.data, indent=2)
        with self._save_lock:
            try:
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(snapshot, encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                pass

    def note_claude(self, alias: str, model_id: str) -> bool:
        """Remember the real model behind a Claude alias. Returns True when it
        changed (so the caller can refresh labels and persist)."""
        alias = (alias or "").lower()
        if alias not in CLAUDE_ALIASES or not claude_pretty(model_id):
            return False
        mid = re.sub(r"\[.*?\]$", "", str(model_id).strip().lower())
        with self._lock:
            cur = self.data["claude"].get(alias, {})
            if cur.get("model") == mid:
                cur["seen"] = int(time.time())
                return False
            self.data["claude"][alias] = {"model": mid, "seen": int(time.time())}
        return True

    def claude_model(self, alias: str) -> str:
        with self._lock:
            return (self.data["claude"].get(alias) or {}).get("model", "")

    def set(self, key: str, value) -> None:
        with self._lock:
            self.data[key] = value

    def get(self, key: str, default=None):
        with self._lock:
            return self.data.get(key, default)

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.data))
