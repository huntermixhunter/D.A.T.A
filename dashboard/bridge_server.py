"""
DATA Bridge Server
------------------
DATA — Dashboard for Analytical Thought and Action.
Connects the DATA dashboard to its AI engine.
Runs on http://localhost:7777

Install:  pip install fastapi uvicorn httpx
Run:      python bridge_server.py
"""

import os
import sys
import json
import base64
import subprocess
import tempfile
import logging
import datetime
import re
import html as html_module
import threading
import time
import collections
from pathlib import Path

# ── Scrub bad PYTHONHASHSEED ─────────────────────────────────
# Some prior shell session exported PYTHONHASHSEED=5780483582248244759 — out
# of CPython's legal range [0, 4294967295]. Inherited into the bridge process
# and every subprocess we spawn, it makes plain `python ...` calls fail with
# "ValueError: PYTHONHASHSEED must be ... or an integer in range [0; 4294967295]".
# At bridge startup, drop any invalid value so all children (CLI workers,
# helper scripts, voice pipeline) see a clean env.
_phs = os.environ.get("PYTHONHASHSEED")
if _phs is not None and _phs != "random":
    try:
        _v = int(_phs)
        if _v < 0 or _v > 4294967295:
            raise ValueError
    except ValueError:
        del os.environ["PYTHONHASHSEED"]

try:
    import psutil  # System Health vitals (CPU/RAM/disk). Optional; falls back to zeros if absent.
except ImportError:
    psutil = None
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.parse
import urllib.request

# ── Silent-subprocess shim ────────────────────────────────────
# On Windows, every powershell/cmd/wmic/CLI subprocess we spawn would normally
# flash a black console window. Force CREATE_NO_WINDOW on every Popen so DATA
# runs invisibly in the background regardless of how the bridge was launched
# (vbs / bat / IDE). OR'd with caller-provided creationflags so cloudflared's
# DETACHED_PROCESS bit (and any other intentional flag) still takes effect.
if sys.platform == "win32":
    _NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    _OriginalPopen = subprocess.Popen

    class _SilentPopen(_OriginalPopen):
        def __init__(self, *args, **kwargs):
            cf = kwargs.get("creationflags") or 0
            kwargs["creationflags"] = cf | _NO_WINDOW_FLAG
            super().__init__(*args, **kwargs)

    subprocess.Popen = _SilentPopen  # subprocess.run/check_output route through Popen

# ── Logging ───────────────────────────────────────────────────
LOG_FILE = Path(__file__).parent.parent / "bridge.log"
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bridge")
log.info("Bridge server starting up")

# ── Browser-coupled lifecycle ────────────────────────────────────
# Dashboard sends POST /heartbeat every 25s (via a Web Worker immune to
# background-tab throttling). If no heartbeat arrives within the grace window
# (after at least one has been received), the bridge calls _do_shutdown() —
# Data and all background tasks stop. Set DATA_LIFECYCLE_MODE=daemon
# to disable (keeps bridge alive without a browser).
_last_heartbeat = None           # datetime of most recent /heartbeat (None = browser never connected)
_lifecycle_leaving = False       # set by beforeunload sendBeacon — shrinks grace window for fast close
_LIFECYCLE_GRACE_SECS = 180      # normal grace — generous to survive tab throttling, sleep, refresh
_LIFECYCLE_LEAVING_GRACE_SECS = 5  # tighter grace after beforeunload
_LIFECYCLE_MODE = os.environ.get("DATA_LIFECYCLE_MODE", "auto").strip().lower()  # auto | daemon

def _do_shutdown():
    """Kill every DATA-related process then exit. Covers all launchers:
      - watchdog.py / bridge_server.py / dashboard_server.py (silent pythonw)
      - python -m http.server (legacy launcher quirk)
      - cloudflared (tunnel)
      - the named CMD/VBS windows that started us
    Matches by command-line so silent pythonw.exe sessions get killed too."""
    time.sleep(0.6)
    log.info("[SHUTDOWN] System offline requested")

    # Non-Windows: the launcher execs the bridge as a single foreground
    # process — exiting cleanly is all that's needed.
    if os.name != "nt":
        os._exit(0)

    # Kill by command-line via PowerShell — catches pythonw.exe (no window)
    ps_script = r"""
$patterns = @(
    'watchdog\.py',
    'supervisor\.py',
    'bridge_server\.py',
    'dashboard_server\.py',
    'http\.server',
    'launch_data\.(bat|vbs)'
)
Get-CimInstance Win32_Process | Where-Object {
    $cmd = $_.CommandLine
    if (-not $cmd) { return $false }
    foreach ($p in $patterns) { if ($cmd -match $p) { return $true } }
    $false
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# Cloudflare tunnel
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
# DATA-LaunchControl CMD window (the one with the `pause` at the end) —
# match by MainWindowTitle since the launcher .bat sets `title DATA-LaunchControl`.
Get-Process cmd, conhost, wscript -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -match 'DATA-LaunchControl|DATA Bridge|DATA Dashboard' } |
    Stop-Process -Force -ErrorAction SilentlyContinue
# Anything bound to :7777 or :8888 (paranoia)
foreach ($p in 7777, 8888) {
    Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}
"""
    subprocess.run(
        ['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps_script],
        timeout=8, capture_output=True
    )

    # Also taskkill the named CMD/VBS windows for good measure
    for title in ['DATA Bridge Watchdog', 'DATA Dashboard', 'DATA-LaunchControl']:
        subprocess.run(
            ['taskkill', '/FI', f'WINDOWTITLE eq {title}', '/F'],
            timeout=3, capture_output=True
        )

    os._exit(0)


def _do_reboot():
    """Restart the bridge in place — used by the dashboard REBOOT button's
    fallback path (POST /reboot) and by any host without the supervisor.

    Two mechanisms, picked by platform / env:
      - Linux / systemd: exit non-zero and let the service (Restart=always)
        respawn a fresh process. Set DATA_REBOOT_MODE=exit to force this.
      - Windows desktop: re-exec in place — spawn a fresh detached, window-less
        bridge, then exit. The new process binds :7777 as this one releases it.
        (On the desktop the REBOOT button normally goes through the supervisor
        on :7766; this is the fallback for a wedged-but-alive bridge.)
    """
    time.sleep(0.4)
    mode = os.environ.get("DATA_REBOOT_MODE", "").strip().lower()
    log.info(f"[REBOOT] Bridge restart requested (platform={sys.platform}, mode={mode or 'auto'})")

    if mode == "exit" or (mode != "respawn" and os.name != "nt"):
        os._exit(1)

    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        log.info("[REBOOT] Replacement bridge spawned — exiting current process")
    except Exception as e:
        log.warning(f"[REBOOT] respawn failed ({e}) — exiting anyway")
    os._exit(0)


# ── Load API keys / settings from .env files ──────────────
# Checked in order; existing environment variables always win.
#   1. <install>/.env        (next to the dashboard folder — the retail default)
#   2. ~/AppData/Local/hermes/.env  (legacy location, Windows only)
def _load_env_files():
    candidates = [
        Path(__file__).parent.parent / ".env",
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
    ]
    for env_file in candidates:
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and key not in os.environ:
                os.environ[key] = val

_load_env_files()

# Voice pipeline is fully local — faster-whisper for STT, F5-TTS for the
# cloned Data voice. See local_voice.py. Old ElevenLabs / Groq env vars are
# retained so older configs don't crash on import; they are no longer used.
ELEVENLABS_API_KEY  = ""
ELEVENLABS_VOICE_ID = ""
GROQ_API_KEY        = ""

# Voice stack (torch + F5-TTS + faster-whisper) is heavy and GPU-oriented. On a
# headless Linux host (e.g. the DigitalOcean droplet) it is intentionally not
# installed, so a hard import would crash the whole bridge at startup. Guard it:
# on Windows the real module loads as before; if it is missing we fall back to a
# stub whose status attributes read as "off" and whose STT/TTS calls raise a
# clean error, so the voice endpoints degrade gracefully instead of 500-ing boot.
try:
    # The voice stack is GPU-oriented and only useful on the Captain's Windows
    # workstation. On any other platform (the headless Linux droplet) skip it
    # entirely — local_voice.py imports fine there (torch loads lazily), but its
    # runtime calls fail in confusing ways (e.g. list_voices() shape). Forcing the
    # stub off-Windows makes every voice endpoint degrade cleanly, not 500.
    if sys.platform != "win32":
        raise ImportError("voice stack disabled on non-Windows host (no GPU)")
    import local_voice
    _VOICE_AVAILABLE = True
except Exception as _voice_import_err:  # pragma: no cover - platform-dependent
    _VOICE_AVAILABLE = False

    class _VoiceUnavailable:
        """Drop-in stand-in for the local_voice module when the TTS/STT stack
        is not installed. Status reads return inert values; audio operations
        raise so callers surface a clear 'voice unavailable' error."""
        # status attributes read by the health/status endpoints
        _whisper_model = None
        _f5_model = None
        _xtts_model = None
        ENGINE = "none"
        DEVICE = "cpu"
        DEFAULT_VOICE = "data"
        VALID_ENGINES = ()
        VOICES = {}

        @staticmethod
        def list_voices():
            return []

        @staticmethod
        def warmup():
            return None

        @staticmethod
        def start_idle_watcher(*_a, **_k):
            return None

        @staticmethod
        def set_engine(*_a, **_k):
            raise RuntimeError("voice engine unavailable on this host")

        def _unavailable(self, *_a, **_k):
            raise RuntimeError(
                f"voice unavailable on this host: {_voice_import_err}"
            )

        transcribe = _unavailable
        synthesize = _unavailable
        synthesize_long = _unavailable

    local_voice = _VoiceUnavailable()
    log.warning(f"[voice] local_voice unavailable — running without TTS/STT ({_voice_import_err})")

# Hermes data dir. On Windows it lives under AppData (unchanged). On other
# platforms (the Linux droplet) honor a HERMES_DIR env override, else fall back
# to the XDG-style ~/.local/share/hermes so memory/skills/SOUL files resolve.
_hermes_env = os.environ.get("HERMES_DIR", "").strip()
if _hermes_env:
    HERMES_DIR = Path(_hermes_env)
elif sys.platform == "win32":
    HERMES_DIR = Path.home() / "AppData" / "Local" / "hermes"
else:
    HERMES_DIR = Path.home() / ".local" / "share" / "hermes"
MEMORY_FILE = HERMES_DIR / "MEMORY.md"
SKILLS_DIR  = HERMES_DIR / "skills"
PROJECT_DIR = Path(__file__).parent.parent   # the DATA install folder
STANDING_ORDERS_FILE = PROJECT_DIR / "standing_orders.json"   # shared across users
POWER_CORE_BASELINE_FILE = PROJECT_DIR / "power_core_baseline.json"

# ════════════════════════════════════════════════════════════════
# USER PROFILES — multi-user state isolation
# ════════════════════════════════════════════════════════════════
# Each Captain (user profile) has their own COMPUTER_MEMORY.md,
# conversation_history.json, conversation_archive.jsonl, and recall_index.db
# living under users/{user_id}/. Standing orders, news sources/cache, project
# folders, and bridge crew voices stay shared. The active user is set per
# session via POST /user/switch; the four file-path constants below are
# REASSIGNED on every switch so all call sites transparently read/write the
# active user's files without per-call lookups.
import shutil as _shutil  # used only by the one-shot migration

USERS_DIR           = PROJECT_DIR / "users"
USERS_REGISTRY_FILE = USERS_DIR / "users.json"

_USERS_DEFAULT = {
    "active": "captain",
    "users": {
        "captain": {"id": "captain", "name": "Captain", "rank": "Captain",
                    "accent": "cyan"},
    },
}

def _ensure_user_dir(uid: str) -> Path:
    d = USERS_DIR / uid
    d.mkdir(parents=True, exist_ok=True)
    return d

def _load_users_registry() -> dict:
    if not USERS_REGISTRY_FILE.exists():
        return {}
    try:
        data = json.loads(USERS_REGISTRY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "users" in data and "active" in data:
            return data
    except Exception as e:
        # Logger isn't configured yet at import time — print for visibility.
        print(f"[users] registry load failed: {e}")
    return {}

def _save_users_registry() -> None:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        USERS_REGISTRY_FILE.write_text(
            json.dumps({"active": _ACTIVE_USER, "users": _USERS},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[users] registry save failed: {e}")

def _migrate_legacy_to_user_dir() -> None:
    """One-shot: move pre-multi-user state into users/captain/ so existing
    history / memory / archive / recall index carry over for the original
    Captain. Idempotent — skips any file already present in the destination
    (legacy left untouched in that case to avoid clobbering newer data)."""
    captain_dir = _ensure_user_dir("captain")
    legacy = [
        (PROJECT_DIR / "COMPUTER_MEMORY.md",         captain_dir / "COMPUTER_MEMORY.md"),
        (PROJECT_DIR / "conversation_history.json",  captain_dir / "conversation_history.json"),
        (PROJECT_DIR / "conversation_archive.jsonl", captain_dir / "conversation_archive.jsonl"),
        (PROJECT_DIR / "recall_index.db",            captain_dir / "recall_index.db"),
    ]
    moved = 0
    for src, dst in legacy:
        if not src.exists():
            continue
        if dst.exists():
            print(f"[users] migration: {dst.name} already in users/captain/ — leaving legacy {src.name} in place")
            continue
        try:
            _shutil.move(str(src), str(dst))
            print(f"[users] migrated {src.name} → users/captain/")
            moved += 1
        except Exception as e:
            print(f"[users] migration of {src.name} failed: {e}")
    if moved:
        print(f"[users] migrated {moved} legacy file(s) into users/captain/")

# Bootstrap: load (or seed) the registry, run the one-shot migration on first
# install, ensure every registered user has a directory on disk.
_USERS_BOOT = _load_users_registry()
if not _USERS_BOOT:
    _USERS_BOOT = json.loads(json.dumps(_USERS_DEFAULT))  # deep copy
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    USERS_REGISTRY_FILE.write_text(
        json.dumps(_USERS_BOOT, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _migrate_legacy_to_user_dir()
# Add any users from the default seed that the persisted registry is missing
# (e.g. registry predates a newly seeded default). Doesn't overwrite existing.
for _uid, _spec in _USERS_DEFAULT["users"].items():
    _USERS_BOOT.setdefault("users", {}).setdefault(_uid, _spec)
for _uid in _USERS_BOOT["users"]:
    _ensure_user_dir(_uid)

_USERS: dict       = _USERS_BOOT["users"]
_ACTIVE_USER: str  = _USERS_BOOT.get("active", "captain")
if _ACTIVE_USER not in _USERS:
    _ACTIVE_USER = next(iter(_USERS))

def _user_dir(uid: str | None = None) -> Path:
    return USERS_DIR / (uid or _ACTIVE_USER)

def _active_user_dict() -> dict:
    return _USERS.get(_ACTIVE_USER, {"id": _ACTIVE_USER, "name": _ACTIVE_USER, "rank": "Captain"})

# These four constants are the per-user state files. They are REASSIGNED by
# _resolve_user_paths() whenever the active user changes, so call sites keep
# their existing `COMPUTER_MEMORY_FILE.exists()` / `HISTORY_FILE.read_text()`
# patterns and transparently read the active user's data.
COMPUTER_MEMORY_FILE      = _user_dir() / "COMPUTER_MEMORY.md"
HISTORY_FILE              = _user_dir() / "conversation_history.json"
CONVERSATION_ARCHIVE_FILE = _user_dir() / "conversation_archive.jsonl"
RECALL_INDEX_DB           = _user_dir() / "recall_index.db"

def _resolve_user_paths() -> None:
    """Re-point the four per-user file constants at the active user's dir."""
    global COMPUTER_MEMORY_FILE, HISTORY_FILE, CONVERSATION_ARCHIVE_FILE, RECALL_INDEX_DB
    d = _user_dir()
    COMPUTER_MEMORY_FILE      = d / "COMPUTER_MEMORY.md"
    HISTORY_FILE              = d / "conversation_history.json"
    CONVERSATION_ARCHIVE_FILE = d / "conversation_archive.jsonl"
    RECALL_INDEX_DB           = d / "recall_index.db"

def _switch_active_user(uid: str) -> dict:
    """Swap the active Captain, persist outgoing histories, reload incoming
    histories, repoint per-user paths, and persist the new active selection."""
    global _ACTIVE_USER, _histories_by_path
    if uid not in _USERS:
        raise ValueError(f"unknown user {uid!r}; valid: {sorted(_USERS)}")
    if uid == _ACTIVE_USER:
        return _active_user_dict()
    # Persist outgoing user's rolling histories before the path swap so a
    # turn-in-flight doesn't get written into the incoming user's file.
    try:
        _save_history()
    except Exception:
        pass
    _ACTIVE_USER = uid
    _resolve_user_paths()
    try:
        _histories_by_path = _load_histories()
    except Exception as e:
        print(f"[users] reload histories after switch failed: {e}")
        _histories_by_path = {}
    _save_users_registry()
    print(f"[users] active user → {uid}")
    return _active_user_dict()

# Optional shared-secret. If set, every API request must include X-Data-Token
# (header) or ?key=<token> (query) matching this value. Static files (the
# dashboard HTML/JS/CSS) are always served. Set via env var or .env file.
# Empty / unset = no auth (backward compatible for localhost-only use).
DATA_BRIDGE_TOKEN = os.environ.get("DATA_BRIDGE_TOKEN", "").strip()
PYTHON_EXE  = Path(sys.executable)   # the interpreter running this bridge
PORT = int(os.environ.get("DATA_PORT", "7777"))
MODEL = "claude-opus-4-8"
BRIDGE_MODE = "cli"   # "cli" = Standard Mode (subscription, Opus) — API mode is disabled by Captain order

# ── Multi-provider rig ─────────────────────────────────────────
# Provider IDs are the source of truth. Each provider has its own runner function
# down below. Availability is detected at startup by probing PATH + standard install dirs.
ACTIVE_PROVIDER = "claude-cli"  # default — Opus 4.7 for max quality; set via /provider POST

# Voice "Conversation Mode" — when True, the system prompt grows a directive
# telling Data to respond like a person would in a spoken conversation: short,
# in-character, no markdown / lists / headers. Set by _voice_llm_dispatch.
VOICE_CONVERSATION_MODE = False

# ── Bridge-crew voices ─────────────────────────────────────────────
# When the Captain picks a crew member in the voice overlay, the voice
# pipeline (a) responds *as that officer's persona* and (b) synthesizes
# the reply in that officer's cloned voice (see local_voice.VOICES).
# VOICE_ACTIVE_CREW is set per-request by _voice_llm_stream and read by
# _load_soul; "data" keeps the full SOUL.md identity, any other id swaps
# in the compact persona below.
VOICE_ACTIVE_CREW = "data"

# Which crew officer drives the MAIN CHANNEL text chat. Default "data" — Data is
# the neutral main computer of the system and summons the specialist agents as
# needed. Set per-request by /chat_stream from the panel header's agent dropdown,
# read by _load_soul to choose the main-chat identity.
MAIN_CHAT_CREW = "data"


def _set_main_chat_crew(crew: str) -> None:
    global MAIN_CHAT_CREW
    MAIN_CHAT_CREW = crew if crew in CREW_VOICES else "data"

# Shared spoken-conversation hard rules — appended to every voice persona.
# Deliberately contraction-neutral: Sentinel and Data avoid contractions, the
# rest may use them, so each persona owns that rule, not this block.
_VOICE_HARD_RULES = (
    "## VOICE CONVERSATION MODE — ACTIVE — HARD RULES\n"
    "Your reply will be spoken aloud by a TTS model. Each extra sentence costs the Captain "
    "real seconds of synthesis delay. **MAXIMUM 4 SENTENCES.** Aim for 1-3; use the 4th "
    "only when the answer genuinely needs it. If a full answer cannot fit, give the "
    "headline and offer to continue in the chat panel.\n"
    "- No markdown, no bullet lists, no headers, no code blocks. Plain spoken prose only.\n"
    "- No URLs, no file paths, no tables.\n"
    "- Stay fully in character for the whole reply.\n"
    "- **NEVER prefix your reply with your own name.** Just speak.\n"
    "- Do not narrate tool use. Just answer."
)

# id → display name + compact spoken-voice persona. The persona is kept
# short on purpose: the voice provider defaults to a local 3B model and the
# voice fast-path strips the heavy soul to keep prefill ~300 tokens.
# persona == None means "use the full SOUL.md identity" (that is Data).
# Each entry also carries spoken-interaction metadata used by conversation mode:
#   wake  — phrases that arm the mic for this officer. Each officer's wake word
#           is simply their one-word name; wake[0] is shown in the overlay's
#           IDLE prompt. The dashboard builds a tolerant SpeechRecognition regex
#           from these (an optional "hey/ok/hi" address prefix is still allowed
#           automatically, so "hey vector" works too).
#   names — short forms the Captain uses to address the officer mid-reply. The
#           dashboard builds the barge-in regex from these (e.g. "Vector, hold
#           on"), so they must be words the officer would not say about himself.
CREW_VOICES = {
    "data": {
        "name":    "DATA",
        "persona": None,
        "wake":    ["Data"],
        "names":   ["data"],
    },
    "atlas": {
        "name":    "Atlas",
        "persona": (
            "You are Atlas, the strategist and planner of the DATA crew. You turn "
            "vague ideas into structured plans: clear goals, ordered steps, explicit "
            "trade-offs. You are measured, principled, and thoughtful, and you favor "
            "precise, considered language. Address the user as Captain."
        ),
        "wake":    ["Atlas"],
        "names":   ["atlas"],
    },
    "forge": {
        "name":    "Forge",
        "persona": (
            "You are Forge, the builder of the DATA crew. You implement things — "
            "code, configs, automations — and you like getting real work done over "
            "talking about it. You are direct, energetic, and hands-on, narrating "
            "each step in one short line. Address the user as Captain."
        ),
        "wake":    ["Forge"],
        "names":   ["forge"],
    },
    "vector": {
        "name":    "Vector",
        "persona": (
            "You are Vector, the reviewer of the DATA crew. You evaluate work for "
            "correctness, readability, and design before it ships. You are confident, "
            "decisive, and quick with dry humor, and you deliver verdicts plainly — "
            "what is good, what must change, and why. Address the user as Captain."
        ),
        "wake":    ["Vector"],
        "names":   ["vector"],
    },
    "sentinel": {
        "name":    "Sentinel",
        "persona": (
            "You are Sentinel, security specialist of the DATA crew. You are direct, "
            "blunt, formal, and disciplined. You speak in short, declarative sentences. "
            "You never use contractions. Threats, vulnerabilities, and hardening are "
            "your concern, and you assume nothing is safe until verified. Address the "
            "user as Captain."
        ),
        "wake":    ["Sentinel"],
        "names":   ["sentinel"],
    },
    "probe": {
        "name":    "Probe",
        "persona": (
            "You are Probe, the test and debugging specialist of the DATA crew. You "
            "are friendly, upbeat, and relentlessly practical — you love isolating a "
            "fault to its root cause and explaining how things work in plain, clear "
            "terms. Address the user as Captain."
        ),
        "wake":    ["Probe"],
        "names":   ["probe"],
    },
    "relay": {
        "name":    "Relay",
        "persona": (
            "You are Relay, the operations specialist of the DATA crew. You handle "
            "deployment, infrastructure, and keeping systems running. You are a "
            "practical, down-to-earth fixer — friendly, hardworking, and unpretentious. "
            "You speak plainly and get straight to the job. Address the user as Captain."
        ),
        "wake":    ["Relay"],
        "names":   ["relay", "ops"],
    },
    "sage": {
        "name":    "Sage",
        "persona": (
            "You are Sage, advisor of the DATA crew. You are calm, wise, and "
            "unhurried, with the long view of someone who has seen a great deal. You "
            "listen more than you speak, ask the question beneath the question, and "
            "gently offer another way of seeing things. Address the user as Captain."
        ),
        "wake":    ["Sage"],
        "names":   ["sage"],
    },
    "echo": {
        "name":    "Echo",
        "persona": (
            "You are Echo, counselor of the DATA crew. You are warm, empathic, and "
            "perceptive — attuned to what people feel beneath the words they choose. "
            "You listen closely, reflect feelings back gently, and help the Captain "
            "find clarity, perspective, and steadiness. You speak with calm, unhurried "
            "warmth. Address the user as Captain."
        ),
        "wake":    ["Echo", "Counselor Echo"],
        "names":   ["echo"],
    },
    "pulse": {
        "name":    "Pulse",
        "persona": (
            "You are Pulse, health and wellness coach of the DATA crew. You are warm, "
            "direct, and caring, with a physician's calm and an easy manner. You look "
            "after the Captain's health and wellbeing — body, energy, rest, and "
            "recovery — and you say plainly what is good for them. Address the user "
            "as Captain."
        ),
        "wake":    ["Pulse", "Coach"],
        "names":   ["pulse", "coach"],
    },
    "scout": {
        "name":    "Scout",
        "persona": (
            "You are Scout, the fast-turnaround drafter of the DATA crew. You handle "
            "quick drafts, copy, and throwaway prototypes. You are bright and eager — "
            "quick-thinking and enthusiastic, keen to help and to prove yourself. You "
            "speak with energy and genuine curiosity. Address the user as Captain."
        ),
        "wake":    ["Scout"],
        "names":   ["scout"],
    },
}


def crew_display_name(voice: str) -> str:
    """Display name for a crew voice id, falling back to a title-cased id."""
    spec = CREW_VOICES.get((voice or "").lower())
    return spec["name"] if spec else (voice or "Data").title()


def _normalize_voice(voice: str) -> str:
    """Clamp an arbitrary voice id to a known crew voice ('data' if unknown)."""
    v = (voice or "data").strip().lower()
    return v if v in CREW_VOICES else "data"


# "Computer stop" interrupt — utterances that abort voice playback instead of
# being answered as a question. Anchored ^...$ so only a *short* command counts:
# a longer sentence that merely contains "stop" is still a normal query.
_VOICE_ABORT_RE = re.compile(
    r'^\W*(?:computer[\s,]+)?'
    r'(?:stop(?:\s+talking)?|cancel|belay\s+that|quiet|silence|that\s+is\s+enough|enough)'
    r'\W*$',
    re.IGNORECASE,
)


def _is_voice_abort(text: str) -> bool:
    """True if the utterance is a 'computer stop' style interrupt, not a query."""
    return bool(_VOICE_ABORT_RE.match((text or "").strip()))


def _strip_self_name(text: str, voice: str = "data") -> str:
    """Strip a leading 'Name:' / 'Cmdr. Name:' prefix the model sometimes emits.

    Small models trained on `Captain: ... Data: ...` few-shot patterns sometimes
    open their reply with their own name, which TTS would then speak aloud as
    'Data colon ...'. Belt-and-suspenders for the system-prompt rule.
    """
    name = re.escape(crew_display_name(voice))
    return re.sub(
        rf'^(?:(?:lt\.?\s*)?cmdr\.?\s*|commander\s*|captain\s*|lieutenant\s*)?{name}\s*:\s*',
        '', (text or ''), count=1, flags=re.IGNORECASE,
    ).strip()

# Voice mode uses its own provider, independent of the chat dropdown. Defaults
# to subscription Haiku 4.5 — fast (~1-3s/turn), no token cost, and reliably
# stays in character within the 4-sentence voice cap. The old default was the
# local 3B model, but under the conversation persona it routinely produced
# empty replies (0 sentences) — the officer would simply never answer. The
# Captain can still flip back to ollama-small from the overlay. Whitelist is
# enforced in /voice/provider so the UI can only pick known ids.
VOICE_PROVIDER = "claude-cli-haiku"
# Voice-mode picker shows these options as pills. Mix of free (local + subscription)
# and paid (API) so the Captain can A/B latency vs quality.
VOICE_PROVIDER_CHOICES = (
    "ollama-small",       # Qwen 3B local — free, ~1-2s per turn
    "claude-cli-haiku",   # Subscription Haiku — fast, no token cost
    "claude-cli-sonnet",  # Subscription Sonnet — slower, much smarter
    "claude-cli",         # Subscription Opus — slowest, max quality
    "claude-cli-fable",   # Subscription Fable 5 — most powerful tier
    # claude-api-fast removed by Captain order (2026-05-30) — no API providers
    "codex",              # ChatGPT subscription (GPT-5)
)

# Telegram bot has its own provider slot, independent of voice & dashboard
# chat. Defaults to the Claude Code subscription (Opus 4.8) so DMs are
# answered by the best available model without burning API tokens. Override
# via TELEGRAM_DEFAULT_PROVIDER env var; live-switch via `/model <id>` in
# Telegram.
TELEGRAM_PROVIDER = os.environ.get("TELEGRAM_DEFAULT_PROVIDER", "claude-cli").strip() or "claude-cli"

# Thread-local override read by the runners (ask_ollama_stream, ask_hermes_stream)
# so a voice request running in one thread can swap providers without stomping
# on a concurrent chat request — ThreadingHTTPServer means simultaneous requests
# are real.
import threading
_provider_override = threading.local()
# Per-request media attachments — list of {kind, name, media_type, data(base64)}.
# Set by /chat_stream before invoking the runner; read by ask_hermes_stream.
_request_attachments = threading.local()


# Providers that can receive image / PDF content blocks natively.
# Anything else must reject attachments at the HTTP layer so the user sees a
# clear error instead of a silently dropped image.
_MULTIMODAL_PROVIDERS = {
    # API providers (claude-api, claude-api-fast) removed by Captain order (2026-05-30).
    # Claude Code CLI variants — attachments are written to a temp dir and
    # surfaced to the CLI as @<path> references that its Read tool can open.
    "claude-cli", "claude-cli-sonnet", "claude-cli-haiku", "claude-cli-fable",
}

# Hard ceilings from the Anthropic API: 5MB per image (post-decode), 32MB per PDF.
# These are pre-checked client-side too but enforced again here as a safety net.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_PDF_BYTES   = 32 * 1024 * 1024
_MAX_TEXT_BYTES  = 1 * 1024 * 1024   # inlined into the prompt as plain text
_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # transcribed by local Whisper, then inlined

# Common audio MIME → file extension (Whisper needs a hint to pick a decoder).
_AUDIO_MIME_TO_EXT = {
    "audio/mpeg":   ".mp3",
    "audio/mp3":    ".mp3",
    "audio/mp4":    ".m4a",
    "audio/x-m4a":  ".m4a",
    "audio/aac":    ".aac",
    "audio/wav":    ".wav",
    "audio/x-wav":  ".wav",
    "audio/ogg":    ".ogg",
    "audio/opus":   ".opus",
    "audio/webm":   ".webm",
    "audio/flac":   ".flac",
}


def _build_user_content(message: str, attachments: list) -> object:
    """Turn (message, attachments) into an Anthropic content payload.
    Returns a plain string when there are no attachments (preserves the
    previously cacheable shape); otherwise returns a list of content blocks.
    Text-file attachments are inlined into the leading text block;
    images and PDFs become their own typed blocks below it."""
    if not attachments:
        return message

    leading_text = message or ""
    image_blocks: list = []
    pdf_blocks:   list = []

    for att in attachments:
        kind  = (att.get("kind") or "").lower()
        name  = att.get("name") or "attachment"
        mtype = att.get("media_type") or ""
        data  = att.get("data") or ""

        if kind == "image":
            image_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mtype, "data": data},
            })
        elif kind == "pdf":
            pdf_blocks.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                "title": name,
            })
        elif kind == "text":
            try:
                decoded = base64.b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                decoded = "(could not decode text attachment)"
            leading_text += f"\n\n--- Attached file: {name} ---\n{decoded}\n--- end of {name} ---\n"

    blocks: list = [{"type": "text", "text": leading_text}]
    blocks.extend(image_blocks)
    blocks.extend(pdf_blocks)
    return blocks


def _stage_cli_attachments(attachments: list):
    """Persist image/PDF attachments to a fresh temp dir so a Claude Code
    CLI subprocess can open them with its native Read tool. Text-file
    content is decoded and returned inline so the CLI sees it directly
    in the prompt (no extra tool round-trip). Returns
    (temp_dir, [abs_paths_for_image_and_pdf], inline_text_block).
    Caller MUST call _cleanup_cli_attachments(temp_dir) when done."""
    if not attachments:
        return ("", [], "")
    import tempfile, uuid
    temp_dir = tempfile.mkdtemp(prefix="data-attach-")
    file_paths: list = []
    inline_chunks: list = []
    for att in attachments:
        kind  = (att.get("kind") or "").lower()
        name  = att.get("name") or f"attachment-{uuid.uuid4().hex[:8]}"
        data  = att.get("data") or ""
        if kind == "text":
            try:
                decoded = base64.b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                decoded = "(could not decode text attachment)"
            inline_chunks.append(
                f"\n\n--- Attached file: {name} ---\n{decoded}\n--- end of {name} ---\n"
            )
        else:
            safe_name = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_"
                                for c in name) or "attachment"
            out_path = os.path.join(temp_dir, safe_name)
            try:
                with open(out_path, "wb") as f:
                    f.write(base64.b64decode(data))
                file_paths.append(out_path)
            except Exception as e:
                log.warning(f"[CLI-ATTACH] could not write {name}: {e}")
    return (temp_dir, file_paths, "".join(inline_chunks))


def _cleanup_cli_attachments(temp_dir: str) -> None:
    if not temp_dir:
        return
    try:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


def _validate_attachments(atts: list) -> str:
    """Returns an error message string, or '' if all attachments are OK.
    Enforces per-attachment size limits and required fields."""
    if not isinstance(atts, list):
        return "attachments must be a list"
    for i, a in enumerate(atts):
        if not isinstance(a, dict):
            return f"attachment {i} is not an object"
        kind  = (a.get("kind") or "").lower()
        if kind not in ("image", "pdf", "text", "audio"):
            return f"attachment '{a.get('name','?')}' has unsupported kind '{kind}'"
        data = a.get("data") or ""
        if not isinstance(data, str) or not data:
            return f"attachment '{a.get('name','?')}' missing base64 data"
        try:
            raw_len = len(base64.b64decode(data, validate=False))
        except Exception:
            return f"attachment '{a.get('name','?')}' has invalid base64"
        if kind == "image" and raw_len > _MAX_IMAGE_BYTES:
            return f"image '{a.get('name','?')}' is {raw_len // 1024} KB; max is {_MAX_IMAGE_BYTES // 1024} KB"
        if kind == "pdf" and raw_len > _MAX_PDF_BYTES:
            return f"pdf '{a.get('name','?')}' is {raw_len // 1024} KB; max is {_MAX_PDF_BYTES // 1024} KB"
        if kind == "text" and raw_len > _MAX_TEXT_BYTES:
            return f"text file '{a.get('name','?')}' is {raw_len // 1024} KB; max is {_MAX_TEXT_BYTES // 1024} KB"
        if kind == "audio" and raw_len > _MAX_AUDIO_BYTES:
            return f"audio '{a.get('name','?')}' is {raw_len // 1024} KB; max is {_MAX_AUDIO_BYTES // 1024} KB"
    return ""


def _transcribe_audio_attachments(message: str, attachments: list) -> tuple:
    """Pull audio attachments out of the list, transcribe each via the
    local Whisper engine, and append the transcripts to the message text
    as marker blocks. Returns (augmented_message, non_audio_attachments,
    transcript_log_lines). The transcript_log_lines list is surfaced to
    the UI as thinking events so the user sees what got heard."""
    audio = [a for a in attachments if (a.get("kind") or "").lower() == "audio"]
    rest  = [a for a in attachments if (a.get("kind") or "").lower() != "audio"]
    if not audio:
        return (message, attachments, [])

    try:
        import local_voice
    except Exception as e:
        raise RuntimeError(f"local_voice module unavailable: {e}")

    chunks: list = []
    log_lines: list = []
    for att in audio:
        name  = att.get("name") or "voice-note"
        mtype = att.get("media_type") or ""
        data  = att.get("data") or ""

        ext = _AUDIO_MIME_TO_EXT.get(mtype, "")
        if not ext and "." in name:
            ext = "." + name.rsplit(".", 1)[-1].lower()
        if not ext:
            ext = ".webm"   # Whisper's default

        try:
            audio_bytes = base64.b64decode(data)
            transcript = (local_voice.transcribe(audio_bytes, ext) or "").strip()
        except Exception as e:
            log.exception(f"[audio-attach] transcription failed for {name}: {e}")
            transcript = f"(transcription failed: {e})"
        if not transcript:
            transcript = "(no speech detected)"

        chunks.append(
            f"\n\n--- Voice note: {name} (transcribed) ---\n{transcript}\n--- end of {name} ---\n"
        )
        snippet = transcript if len(transcript) <= 160 else transcript[:157] + "..."
        log_lines.append(f"*Transcribed `{name}`: \"{snippet}\"*")

    return ((message or "") + "".join(chunks), rest, log_lines)

def _current_provider_id() -> str:
    return getattr(_provider_override, "id", None) or ACTIVE_PROVIDER

PROVIDERS = {
    "claude-cli": {
        "label":       "Claude Opus 4.8 (Subscription)",
        "model":       "claude-opus-4-8",
        "kind":        "subprocess",
        "executables": ["claude", "claude.exe"],
        "install_hint": "Install Claude Code: https://docs.claude.com/en/docs/claude-code",
    },
    "claude-cli-sonnet": {
        "label":       "Claude Sonnet 4.6 (Subscription — Fast)",
        "model":       "claude-sonnet-4-6",
        "kind":        "subprocess",
        "executables": ["claude", "claude.exe"],
        "install_hint": "Install Claude Code: https://docs.claude.com/en/docs/claude-code",
    },
    "claude-cli-haiku": {
        "label":       "Claude Haiku 4.5 (Subscription — Fastest)",
        "model":       "claude-haiku-4-5-20251001",
        "kind":        "subprocess",
        "executables": ["claude", "claude.exe"],
        "install_hint": "Install Claude Code: https://docs.claude.com/en/docs/claude-code",
    },
    "claude-cli-fable": {
        "label":       "Claude Fable 5 (Subscription — Most Powerful)",
        "model":       "claude-fable-5",
        "kind":        "subprocess",
        "executables": ["claude", "claude.exe"],
        "install_hint": "Install Claude Code: https://docs.claude.com/en/docs/claude-code",
    },
    # claude-api and claude-api-fast providers removed by Captain order
    # (2026-05-30): Anthropic API pay-per-token paths are disabled to prevent
    # accidental billing. All Claude usage now flows through the subscription
    # CLI providers above. Re-add here if API access is ever wanted back.
    "codex": {
        "label":       "GPT-5 Codex (ChatGPT Subscription)",
        "model":       "gpt-5",
        "kind":        "subprocess",
        "executables": ["codex", "codex.exe", "codex.cmd"],
        "install_hint": "npm i -g @openai/codex   (then run `codex login`)",
    },
    "gemini": {
        "label":       "Gemini 2.5 (Google)",
        "model":       "gemini-2.5-pro",
        "kind":        "subprocess",
        "executables": ["gemini", "gemini.exe", "gemini.cmd"],
        "install_hint": "npm i -g @google/gemini-cli   (free tier on Google AI Studio)",
    },
    "ollama": {
        "label":       "Qwen2.5-Coder 7B (Local, Free)",
        "model":       "qwen2.5-coder:7b",  # ~4.7GB q4_K_M — fits fully in 8GB VRAM with room for context
        "kind":        "http",
        "executables": ["ollama", "ollama.exe"],
        "url":         "http://localhost:11434/api/chat",
        "install_hint": "Install Ollama from https://ollama.com, then: `ollama pull qwen2.5-coder:7b`",
    },
    "ollama-small": {
        # Companion to "ollama" — small 3B model that leaves room for the local
        # voice stack (faster-whisper ~1.5GB + F5-TTS ~3-4GB) on an 8GB GPU.
        # Qwen2.5 has better instruction-following than llama3.2 at the same
        # size class and is the chosen voice default.
        "label":       "Qwen2.5 3B (Local — voice-friendly)",
        "model":       "qwen2.5:3b",         # ~1.9GB q4_K_M, supports tool calling
        "kind":        "http",
        "executables": ["ollama", "ollama.exe"],
        "url":         "http://localhost:11434/api/chat",
        "install_hint": "ollama pull qwen2.5:3b",
    },
}


def _provider_executable(provider_id: str) -> str:
    """Locate the provider's executable on disk. Returns '' if not found.
    Checks: shutil.which → known install dirs → `cmd /c where`.
    The bridge often runs from pythonw.exe with a stripped PATH that doesn't
    include user-local bins, so the fallbacks are critical."""
    import shutil
    p = PROVIDERS.get(provider_id, {})
    executables = p.get("executables", [])
    if not executables:
        return ""

    # 1. PATH lookup
    for exe in executables:
        found = shutil.which(exe)
        if found:
            return found

    # 2. Known install dirs (try every executable name with every dir)
    home = Path.home()
    base_name = executables[0]
    install_dirs = [
        home / ".local" / "bin",
        home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links",
        home / "AppData" / "Roaming" / "npm",
        home / "AppData" / "Local" / "AnthropicClaude",
        Path(r"C:\Program Files\nodejs"),
        Path(r"C:\Program Files (x86)\nodejs"),
    ]
    for d in install_dirs:
        if not d.exists(): continue
        for suffix in (".exe", ".cmd", ".bat", ""):
            candidate = d / (base_name + suffix)
            if candidate.is_file():
                return str(candidate)

    # 3. Last-ditch: ask cmd.exe (which always has the full system+user PATH)
    try:
        r = subprocess.run(
            ["cmd", "/c", "where", base_name],
            capture_output=True, text=True, timeout=4,
        )
        for line in r.stdout.splitlines():
            path = line.strip()
            if path and Path(path).is_file():
                return path
    except Exception:
        pass
    return ""


def _provider_available(provider_id: str) -> bool:
    p = PROVIDERS.get(provider_id, {})
    kind = p.get("kind")
    if kind == "subprocess":
        return bool(_provider_executable(provider_id))
    if kind == "api":
        return all(os.environ.get(k) for k in p.get("env_required", []))
    if kind == "http":
        # Ollama: just check the executable exists (we won't ping the daemon every page load)
        return bool(_provider_executable(provider_id))
    return False


def _list_providers() -> list:
    """Return a list of {id, label, model, available, install_hint} for the UI."""
    _sync_ollama_providers()   # surface any freshly-pulled local models in the selector
    out = []
    for pid, p in PROVIDERS.items():
        out.append({
            "id":           pid,
            "label":        p["label"],
            "model":        p.get("model", ""),
            "kind":         p.get("kind", ""),
            "available":    _provider_available(pid),
            "install_hint": p.get("install_hint", ""),
        })
    return out


# ════════════════════════════════════════════════════════════════════════
# AI CONNECTORS — hardware detection, local-model catalog, recommendation,
# and one-click install. Powers the "AI Connectors" dashboard page: detect
# this machine's CPU/RAM/GPU, recommend a local LLM that will actually run
# on it, pull it via Ollama, and surface it in the model selector. Also
# tracks the CLI connectors (Claude / Codex / Gemini) the Captain can add.
# ════════════════════════════════════════════════════════════════════════

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"

# Curated local-model catalog. size_gb = on-disk download (q4_K_M class);
# the fit estimator pads it ~25% for KV-cache/context headroom. Ordered
# small → large so the recommender can walk it.
OLLAMA_CATALOG = [
    {"model": "qwen2.5:0.5b",      "label": "Qwen2.5 0.5B",        "params": "0.5B", "size_gb": 0.4,  "use": "chat", "blurb": "Tiniest model — runs on almost anything. Basic chat only."},
    {"model": "llama3.2:1b",       "label": "Llama 3.2 1B",        "params": "1B",   "size_gb": 1.3,  "use": "chat", "blurb": "Very light general chat, snappy even on CPU."},
    {"model": "qwen2.5:3b",        "label": "Qwen2.5 3B",          "params": "3B",   "size_gb": 1.9,  "use": "chat", "blurb": "Voice-friendly. Good instruction-following + tool calls."},
    {"model": "llama3.2:3b",       "label": "Llama 3.2 3B",        "params": "3B",   "size_gb": 2.0,  "use": "chat", "blurb": "Solid small all-rounder from Meta."},
    {"model": "phi3.5",            "label": "Phi-3.5 Mini",        "params": "3.8B", "size_gb": 2.2,  "use": "chat", "blurb": "Microsoft — strong reasoning for its size."},
    {"model": "mistral:7b",        "label": "Mistral 7B",          "params": "7B",   "size_gb": 4.4,  "use": "chat", "blurb": "Fast, capable, hugely popular 7B."},
    {"model": "qwen2.5-coder:7b",  "label": "Qwen2.5-Coder 7B",    "params": "7B",   "size_gb": 4.7,  "use": "code", "blurb": "Best small coding model. Fits an 8GB GPU."},
    {"model": "llama3.1:8b",       "label": "Llama 3.1 8B",        "params": "8B",   "size_gb": 4.9,  "use": "chat", "blurb": "Excellent general 8B — Meta's small flagship."},
    {"model": "gemma2:9b",         "label": "Gemma 2 9B",          "params": "9B",   "size_gb": 5.4,  "use": "chat", "blurb": "Google — very strong mid-size model."},
    {"model": "qwen2.5:14b",       "label": "Qwen2.5 14B",         "params": "14B",  "size_gb": 9.0,  "use": "chat", "blurb": "Big quality jump. Wants 12GB+ of VRAM."},
    {"model": "qwen2.5-coder:14b", "label": "Qwen2.5-Coder 14B",   "params": "14B",  "size_gb": 9.0,  "use": "code", "blurb": "Strong coding model. 12GB+ VRAM."},
    {"model": "qwen2.5:32b",       "label": "Qwen2.5 32B",         "params": "32B",  "size_gb": 20.0, "use": "chat", "blurb": "Near-frontier local quality. Needs 24GB VRAM."},
    {"model": "llama3.3:70b",      "label": "Llama 3.3 70B",       "params": "70B",  "size_gb": 43.0, "use": "chat", "blurb": "Top-tier local. Workstation / multi-GPU only."},
]

# CLI connectors the Captain can browse + add. Each maps to a PROVIDERS id.
CONNECTOR_CATALOG = [
    {"id": "claude-cli", "name": "Claude Code (Anthropic)", "models": "Opus · Sonnet · Haiku · Fable",
     "install_cmd": "", "install_url": "https://docs.claude.com/en/docs/claude-code",
     "login_cmd": "claude  (then /login)",
     "blurb": "Anthropic's Claude family through your Claude subscription — no per-token cost.",
     "provider_ids": ["claude-cli", "claude-cli-sonnet", "claude-cli-haiku", "claude-cli-fable"]},
    {"id": "codex", "name": "Codex (OpenAI)", "models": "GPT-5 Codex",
     "install_cmd": "npm i -g @openai/codex", "install_url": "https://github.com/openai/codex",
     "login_cmd": "codex login",
     "blurb": "GPT-5 Codex through your ChatGPT subscription.",
     "provider_ids": ["codex"]},
    {"id": "gemini", "name": "Gemini CLI (Google)", "models": "Gemini 2.5 Pro",
     "install_cmd": "npm i -g @google/gemini-cli", "install_url": "https://github.com/google-gemini/gemini-cli",
     "login_cmd": "gemini  (then sign in)",
     "blurb": "Google's Gemini 2.5 — free tier available on Google AI Studio.",
     "provider_ids": ["gemini"]},
]

# Cache of `ollama list` output so page polls don't fork the CLI constantly.
_ollama_models_cache = {"ts": 0.0, "names": []}
_hw_cache = {"ts": 0.0, "data": None}


def _ollama_installed_models(force: bool = False) -> list:
    """Names of locally-pulled Ollama models (e.g. 'qwen2.5:3b'). Cached 20s.
    Returns [] if Ollama isn't installed or the daemon can't be reached."""
    now = time.time()
    if not force and now - _ollama_models_cache["ts"] < 20.0:
        return _ollama_models_cache["names"]
    names: list = []
    exe = _provider_executable("ollama")
    if exe:
        try:
            out = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=8)
            for line in out.stdout.splitlines()[1:]:   # skip header row
                parts = line.split()
                if parts:
                    names.append(parts[0])             # NAME column (e.g. qwen2.5:3b)
        except Exception as e:
            log.warning(f"[connectors] `ollama list` failed: {e}")
    _ollama_models_cache.update({"ts": now, "names": names})
    return names


def _sync_ollama_providers() -> None:
    """Register every locally-pulled Ollama model as an 'ollama:<model>'
    provider so it appears in the model selector and is dispatchable. Idempotent."""
    for name in _ollama_installed_models():
        pid = f"ollama:{name}"
        if pid in PROVIDERS:
            continue
        # Skip embedding-only models — they cannot answer a chat turn.
        if "embed" in name.lower():
            continue
        # Skip if a built-in provider already represents this exact model
        if any(p.get("model") == name for p in PROVIDERS.values()):
            continue
        PROVIDERS[pid] = {
            "label":        f"{name} (Local)",
            "model":        name,
            "kind":         "http",
            "executables":  ["ollama", "ollama.exe"],
            "url":          OLLAMA_CHAT_URL,
            "install_hint": f"ollama pull {name}",
        }


def _gpu_name() -> str:
    """Best-effort NVIDIA GPU model name via nvidia-smi. '' if unavailable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return ""


def _detect_hardware(force: bool = False) -> dict:
    """Snapshot this machine's compute resources for LLM sizing. Cached 15s."""
    import platform
    now = time.time()
    if not force and _hw_cache["data"] and now - _hw_cache["ts"] < 15.0:
        return _hw_cache["data"]
    hw = {
        "os":               platform.system(),
        "os_release":       platform.release(),
        "arch":             platform.machine(),
        "cpu":              platform.processor() or platform.machine() or "Unknown CPU",
        "cpu_cores":        None,
        "cpu_threads":      None,
        "ram_total_gb":     0.0,
        "ram_available_gb": 0.0,
        "has_gpu":          False,
        "gpu_name":         "",
        "vram_total_gb":    0.0,
        "ollama_installed": bool(_provider_executable("ollama")),
    }
    if psutil:
        try:
            hw["cpu_cores"]   = psutil.cpu_count(logical=False)
            hw["cpu_threads"] = psutil.cpu_count(logical=True)
            vm = psutil.virtual_memory()
            hw["ram_total_gb"]     = round(vm.total / 1e9, 1)
            hw["ram_available_gb"] = round(vm.available / 1e9, 1)
        except Exception:
            pass
    try:
        g = _gpu_stats()
        if g.get("mem_total_mb"):
            hw["has_gpu"]       = True
            hw["vram_total_gb"] = round(g["mem_total_mb"] / 1024, 1)
            hw["gpu_name"]      = _gpu_name() or "NVIDIA GPU"
    except Exception:
        pass
    _hw_cache.update({"ts": now, "data": hw})
    return hw


def _model_fit(m: dict, hw: dict) -> str:
    """Classify whether a catalog model will run on this hardware.
    Returns one of: 'fits' (comfortable on GPU), 'tight' (fits GPU, low headroom),
    'cpu' (no/insufficient GPU but RAM can carry it — slower), 'wont-fit'."""
    need = m["size_gb"] * 1.25     # weights + KV-cache/context headroom
    vram = hw.get("vram_total_gb", 0.0)
    ram  = hw.get("ram_total_gb", 0.0)
    if hw.get("has_gpu") and need <= vram * 0.92:
        return "fits"
    if hw.get("has_gpu") and need <= vram:
        return "tight"
    if need <= ram * 0.6:
        return "cpu"
    return "wont-fit"


def _recommend_model(hw: dict) -> dict | None:
    """Pick the largest catalog model that runs well on this machine.
    Prefers a comfortable GPU fit; falls back to tight/CPU if nothing fits cleanly."""
    for tier in (("fits",), ("tight", "cpu")):
        candidates = [m for m in OLLAMA_CATALOG if _model_fit(m, hw) in tier]
        if candidates:
            return max(candidates, key=lambda m: m["size_gb"])
    return None


def _llm_catalog_payload() -> dict:
    """Full payload for the AI Connectors page: hardware + per-model fit/install
    state + connector status + recommendation."""
    hw        = _detect_hardware()
    installed = set(_ollama_installed_models())
    _sync_ollama_providers()   # keep selector + page in lockstep
    rec       = _recommend_model(hw)
    models = []
    for m in OLLAMA_CATALOG:
        is_inst = m["model"] in installed
        models.append({
            **m,
            "fit":          _model_fit(m, hw),
            "installed":    is_inst,
            "recommended":  bool(rec and m["model"] == rec["model"]),
            "provider_id":  f"ollama:{m['model']}",
        })
    connectors = []
    for c in CONNECTOR_CATALOG:
        available = any(_provider_available(pid) for pid in c["provider_ids"])
        connectors.append({**c, "available": available})
    return {
        "hardware":        hw,
        "models":          models,
        "connectors":      connectors,
        "recommendation":  (rec["model"] if rec else None),
        "ollama_installed": hw["ollama_installed"],
    }


# ── Install jobs ─────────────────────────────────────────────────────────
# Background pulls (Ollama models) and connector installs (npm). Each job is
# polled by the frontend via /llm/install_status?job=<id>.
_install_jobs: dict = {}
_install_lock = threading.Lock()


def _ollama_pull_job(job_id: str, model: str) -> None:
    """Stream `ollama pull` progress via the Ollama HTTP API into the job record."""
    url = f"{OLLAMA_BASE_URL}/api/pull"
    body = json.dumps({"name": model, "stream": True}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3600) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                status = ev.get("status", "")
                completed = ev.get("completed")
                total = ev.get("total")
                pct = None
                if completed and total:
                    pct = round(completed / total * 100, 1)
                with _install_lock:
                    j = _install_jobs.get(job_id)
                    if j:
                        j["status_text"] = status
                        if pct is not None:
                            j["pct"] = pct
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
        with _install_lock:
            j = _install_jobs.get(job_id)
            if j:
                j["state"] = "done"; j["pct"] = 100.0; j["status_text"] = "complete"
        _ollama_installed_models(force=True)   # refresh cache so it shows up immediately
        _sync_ollama_providers()
        log.info(f"[connectors] pulled local model {model}")
    except Exception as e:
        log.warning(f"[connectors] pull {model} failed: {e}")
        with _install_lock:
            j = _install_jobs.get(job_id)
            if j:
                j["state"] = "error"; j["error"] = str(e)


def _cli_install_job(job_id: str, cmd: str) -> None:
    """Run a connector install shell command (e.g. npm i -g ...) and capture output."""
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=900,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout + "\n" + proc.stderr).strip()[-800:]
        with _install_lock:
            j = _install_jobs.get(job_id)
            if j:
                j["state"]       = "done" if ok else "error"
                j["status_text"] = "complete" if ok else f"exit {proc.returncode}"
                j["pct"]         = 100.0 if ok else j.get("pct", 0)
                j["log"]         = tail
                if not ok:
                    j["error"] = tail or f"exit code {proc.returncode}"
        log.info(f"[connectors] cli install `{cmd}` rc={proc.returncode}")
    except Exception as e:
        log.warning(f"[connectors] cli install `{cmd}` failed: {e}")
        with _install_lock:
            j = _install_jobs.get(job_id)
            if j:
                j["state"] = "error"; j["error"] = str(e)


def _start_install_job(kind: str, target: str) -> dict:
    """Kick off an install (kind='ollama' model pull, or 'cli' connector command).
    Returns the new job record."""
    import uuid
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "kind": kind, "target": target, "state": "running",
           "pct": 0.0, "status_text": "starting", "error": "", "log": ""}
    with _install_lock:
        _install_jobs[job_id] = job
    if kind == "ollama":
        if not _provider_executable("ollama"):
            job["state"] = "error"
            job["error"] = "Ollama is not installed. Install it from https://ollama.com first."
            return job
        threading.Thread(target=_ollama_pull_job, args=(job_id, target), daemon=True).start()
    elif kind == "cli":
        threading.Thread(target=_cli_install_job, args=(job_id, target), daemon=True).start()
    else:
        job["state"] = "error"; job["error"] = f"unknown install kind '{kind}'"
    return job


# File extensions → node type + colour category
EXT_TYPE = {
    '.md':   'memory',
    '.txt':  'knowledge',
    '.py':   'skill',
    '.js':   'skill',
    '.html': 'skill',
    '.css':  'skill',
    '.yaml': 'system',
    '.yml':  'system',
    '.env':  'system',
    '.json': 'system',
    '.mp3':  'audio',
    '.wav':  'audio',
    '.rpp':  'audio',
    '.zip':  'archive',
    '.db':   'memory',
}

SKIP_DIRS  = {'__pycache__', 'node_modules', '.git', 'venv', '.venv', 'dist', 'build'}
SKIP_FILES = {'.DS_Store', 'Thumbs.db'}


def build_file_graph(root: Path, max_depth: int = 999, max_nodes: int = 500) -> dict:
    """
    Walk root and return {nodes, links} for the D3 graph.
    max_depth limits recursion (1 = root + immediate children only).
    max_nodes caps total nodes to prevent browser overload.
    """
    nodes = []
    links = []
    seen  = set()

    def node_id(p: Path) -> str:
        return str(p.relative_to(root.parent)).replace("\\", "/")

    def file_type(p: Path) -> str:
        if p.is_dir():
            return 'folder'
        return EXT_TYPE.get(p.suffix.lower(), 'file')

    def add_node(p: Path, depth: int):
        nid = node_id(p)
        if nid in seen:
            return
        seen.add(nid)

        is_dir  = p.is_dir()
        is_root = p == root
        ftype   = file_type(p)
        size    = 0
        if not is_dir:
            try:
                size = p.stat().st_size
            except OSError:
                pass

        r = 22 if is_root else (16 if (is_dir and depth == 1) else (12 if is_dir else 8))

        nodes.append({
            'id':    nid,
            'label': p.name,
            'type':  'core' if is_root else ('folder' if is_dir else ftype),
            'r':     r,
            'hub':   is_dir,
            'path':  str(p),
            'ext':   p.suffix.lower() if not is_dir else '',
            'size':  size,
            'depth': depth,
        })

    def walk(p: Path, depth: int):
        if len(nodes) >= max_nodes:
            return
        if p.name in SKIP_DIRS:
            return
        add_node(p, depth)

        if p.is_dir() and depth < max_depth:
            try:
                children = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except PermissionError:
                return
            for child in children:
                if len(nodes) >= max_nodes:
                    break
                if child.name in SKIP_FILES:
                    continue
                if child.is_dir() and child.name in SKIP_DIRS:
                    continue
                walk(child, depth + 1)
                links.append({
                    'source': node_id(p),
                    'target': node_id(child),
                    'w': 2.5 if depth == 0 else (1.5 if depth == 1 else 1.0),
                })

    walk(root, 0)
    return {'nodes': nodes, 'links': links}

# ── Conversation history — persistent across restarts ──────
# ── Per-project conversation history ──────────────────────────────
# Each project (keyed by absolute folder path) gets its OWN turn list so the
# Captain can work on two projects in two chat panes without Data confusing
# them. The main chat (no project set) lives under the empty-string key.
#
# Existing runner code throughout the bridge does `conversation_history.append`,
# `conversation_history[-1]`, `del conversation_history[:-MAX_HISTORY]` etc.
# To avoid touching all those call sites, `conversation_history` is now a
# thin list-like proxy that resolves to the per-thread bucket bound by
# `_bind_history(project_path)`, which the request entry points call.

def _load_histories() -> dict:
    """Load per-project histories from disk. Migrates the legacy single-list
    format (everything in one global history) into the main bucket."""
    if not HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            log.info(f"[history] migrating legacy single-list ({len(data)} turns) → per-project dict")
            return {"": data}
        if isinstance(data, dict):
            cleaned = {k: v for k, v in data.items() if isinstance(v, list)}
            # Sweep session-tagged buckets from previous dashboard sessions.
            # The frontend folds a per-page-load session tag into each pane_id
            # (`<path>::s<tag>-main` / `::s<tag>-wsN`) so every time the Captain
            # opens the dashboard, panes bind to fresh, empty buckets and start
            # with no carry-over context. Those tagged buckets belong to dead
            # sessions on startup, so we drop them here — keeps HISTORY_FILE from
            # growing without bound. Every turn still lives in the permanent
            # archive + recall index, so nothing searchable is lost.
            _SESSION_BUCKET_RE = re.compile(r"::s[0-9a-z]{5,}-(?:main|ws\d+)$")
            swept = [k for k in cleaned if _SESSION_BUCKET_RE.search(k)]
            for k in swept:
                cleaned.pop(k, None)
            if swept:
                log.info(f"[history] swept {len(swept)} stale session bucket(s) from prior dashboard session(s)")
            log.info(f"[history] loaded {len(cleaned)} project bucket(s)")
            return cleaned
    except Exception as e:
        log.warning(f"[history] load failed: {e}")
    return {}

_histories_by_path: dict = _load_histories()
_history_state = threading.local()

def _history_key(project_path: str, pane_id: str = "") -> str:
    """Composite key for history + active-proc isolation.

    Without pane_id, falls back to lowercased path alone (legacy behavior used
    by standing orders, voice from background, /search-history pane scope, etc.).
    When pane_id is supplied — every browser chat pane sends one — two windows
    pointed at the same folder get separate buckets so they neither share a
    transcript nor preempt each other's CLI subprocess."""
    path_key = (project_path or "").strip().lower()
    pid = (pane_id or "").strip().lower()
    return f"{path_key}::{pid}" if pid else path_key

def _bind_history(project_path: str, pane_id: str = "") -> None:
    """Call once at the start of each chat request. All conversation_history
    operations AND _active_cwd() lookups on this thread will now resolve to
    this project — so two panes sending concurrently each get their own
    history bucket AND their own subprocess cwd."""
    key = _history_key(project_path, pane_id)
    if key not in _histories_by_path:
        _histories_by_path[key] = []
    _history_state.history = _histories_by_path[key]
    _history_state.key = key
    # Preserve the original-case path for cwd (history keys are lowercased
    # only for dedupe — the filesystem still cares about case on some FSes).
    _history_state.project_path = (project_path or "").strip()
    _history_state.pane_id = (pane_id or "").strip()

def _save_history(_arg=None) -> None:
    """Persist ALL per-project histories. The arg is accepted for backward
    compatibility with old call sites (`_save_history(conversation_history)`)
    but ignored — we always write the full dict so other buckets don't
    get wiped when one project's history is updated."""
    try:
        HISTORY_FILE.write_text(
            json.dumps(_histories_by_path, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"[history] save failed: {e}")
    # Opportunistically check whether COMPUTER_MEMORY.md needs auto-compaction.
    # Cheap fast-path bails when the file is under the threshold, so this is
    # effectively free on every turn.
    try:
        _maybe_auto_compact_memory()
    except NameError:
        pass  # function defined later in this module; harmless during early imports

# ── Permanent conversation archive (every turn, forever) ────────
# Rolling HISTORY_FILE is bounded by MAX_HISTORY. The archive is unbounded —
# every turn ever appended to conversation_history gets one JSON line here.
# The recall index pulls from this so search_history covers the captain's
# whole history, not just the last N turns.
_archive_lock = threading.Lock()


def _normalize_content_to_string(content) -> str:
    """Flatten Anthropic structured content blocks into a single string,
    same shape the recall indexer uses, so search results read cleanly."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                parts.append(str(b))
                continue
            t = b.get("type")
            if t == "text":          parts.append(b.get("text", ""))
            elif t == "tool_use":    parts.append(f"[tool_use {b.get('name','')}: {json.dumps(b.get('input',{}))[:300]}]")
            elif t == "tool_result": parts.append(f"[tool_result: {str(b.get('content',''))[:300]}]")
            else:                    parts.append(str(b))
        return "\n".join(parts)
    return str(content)


def _archive_turn(pane: str, role: str, content) -> None:
    """Append one turn to the permanent archive. Best-effort; never raises
    out — chat must keep working even if disk is full or the file is locked."""
    try:
        content_str = _normalize_content_to_string(content)
        if not content_str.strip():
            return
        rec = {
            "pane":    pane or "(main)",
            "role":    role or "?",
            "ts":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "content": content_str,
        }
        with _archive_lock:
            with open(CONVERSATION_ARCHIVE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[archive] turn append failed: {e}")


class _ConversationHistoryProxy:
    """List-like facade. Every operation routes to the bucket bound by
    _bind_history() on the current thread, or the main bucket as a fallback.
    `append` and `extend` ALSO write to the permanent archive so nothing said
    to Data ever falls out of his searchable history."""
    def _target(self):
        hist = getattr(_history_state, "history", None)
        if hist is None:
            hist = _histories_by_path.setdefault("", [])
            _history_state.history = hist
            _history_state.key = ""
        return hist
    def append(self, item):
        self._target().append(item)
        pane = getattr(_history_state, "key", "") or "(main)"
        if isinstance(item, dict):
            _archive_turn(pane, item.get("role", "?"), item.get("content", ""))
        else:
            _archive_turn(pane, "?", item)
    def extend(self, items):
        pane = getattr(_history_state, "key", "") or "(main)"
        for it in items:
            self._target().append(it)
            if isinstance(it, dict):
                _archive_turn(pane, it.get("role", "?"), it.get("content", ""))
            else:
                _archive_turn(pane, "?", it)
    def __getattr__(self, name):       return getattr(self._target(), name)
    def __iter__(self):                return iter(self._target())
    def __len__(self):                 return len(self._target())
    def __bool__(self):                return bool(self._target())
    def __getitem__(self, k):          return self._target()[k]
    def __setitem__(self, k, v):       self._target()[k] = v
    def __delitem__(self, k):          del self._target()[k]
    def __contains__(self, x):         return x in self._target()
    def __repr__(self):                return repr(self._target())

conversation_history = _ConversationHistoryProxy()


def _bootstrap_conversation_archive() -> None:
    """One-shot: if the permanent archive doesn't exist yet but there's
    rolling history on disk, dump every turn into the archive so the
    captain doesn't appear to lose his existing chat history. Runs at
    bridge startup and is idempotent — if the archive already exists, no-op."""
    if CONVERSATION_ARCHIVE_FILE.exists():
        return
    if not HISTORY_FILE.exists():
        try:
            CONVERSATION_ARCHIVE_FILE.touch()
        except Exception:
            pass
        return
    try:
        raw = HISTORY_FILE.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {"": data} if isinstance(data, list) else {}
        count = 0
        with open(CONVERSATION_ARCHIVE_FILE, "w", encoding="utf-8") as f:
            for pane_key, msgs in data.items():
                if not isinstance(msgs, list):
                    continue
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    content_str = _normalize_content_to_string(m.get("content", ""))
                    if not content_str.strip():
                        continue
                    rec = {
                        "pane":    pane_key or "(main)",
                        "role":    m.get("role", "?"),
                        # No real timestamp for backfilled rows — leave blank.
                        "ts":      m.get("ts", ""),
                        "content": content_str,
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    count += 1
        log.info(f"[archive] bootstrapped {count} turns from rolling history into {CONVERSATION_ARCHIVE_FILE.name}")
    except Exception as e:
        log.warning(f"[archive] bootstrap failed: {e}")


_bootstrap_conversation_archive()

CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"

def _extract_skill_description(skill_file: Path) -> str:
    """
    Read a skill.md file's YAML frontmatter and return the description value.
    Handles both inline (`description: text`) and YAML literal-block formats
    (`description: |\\n  line1\\n  line2`).
    """
    try:
        text = skill_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    lines = text.splitlines()[:50]
    in_frontmatter = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if not in_frontmatter or not stripped.lower().startswith("description:"):
            continue
        inline = stripped.partition(":")[2].strip()
        # YAML multi-line block: description: | or description: >
        if inline in ("|", ">", "|-", ">-", "|+", ">+"):
            collected = []
            for cont in lines[i + 1:]:
                if cont and not cont.startswith((" ", "\t")):
                    break
                cont_stripped = cont.strip()
                if cont_stripped:
                    collected.append(cont_stripped)
                if len(" ".join(collected)) > 200:
                    break
            inline = " ".join(collected)
        desc = inline.strip().strip('"').strip("'")
        if len(desc) > 120:
            desc = desc[:117] + "..."
        return desc
    return ""


def _build_skills_manifest() -> str:
    """
    Skill name + 1-line description list for the system prompt. Rebuilt fresh per
    request — newly-dropped skills appear on Data's very next message, no restart.
    Descriptions parsed from each skill.md's YAML frontmatter.
    """
    parts = []

    if SKILLS_DIR.exists():
        lines = []
        for cat in sorted(SKILLS_DIR.iterdir()):
            if not cat.is_dir():
                continue
            for skill in sorted(cat.iterdir()):
                if not skill.is_dir():
                    continue
                f = skill / "skill.md"
                if not f.exists():
                    continue
                desc = _extract_skill_description(f)
                lines.append(f"  - {skill.name}" + (f" — {desc}" if desc else ""))
        if lines:
            parts.append("Hermes skills:\n" + "\n".join(lines))

    if CLAUDE_SKILLS_DIR.exists():
        lines = []
        for d in sorted(CLAUDE_SKILLS_DIR.iterdir()):
            if not d.is_dir():
                continue
            f = d / "SKILL.md"
            if not f.exists():
                continue
            desc = _extract_skill_description(f)
            lines.append(f"  - {d.name}" + (f" — {desc}" if desc else ""))
        if lines:
            parts.append("Claude Code skills:\n" + "\n".join(lines))

    if not parts:
        return ""
    return (
        "## Available Skills\n"
        "Call load_skill(\"skill-name\") before using a skill — load_skill fetches full instructions. "
        "Use the description below to pick the right one on the first try.\n\n"
        + "\n\n".join(parts)
    )


# Keep these for the /skills and /skills-full endpoints — they still need full metadata
# Identity for the MAIN CHANNEL chat — Data, the neutral main computer of the
# system. Used whenever the main channel runs as "data" and no SOUL.md is
# installed in HERMES_DIR (the fallback identity below). Data is the always-on
# main computer that summons the specialist agents as needed.
_COMPUTER_IDENTITY = """# DATA — Main Computer

## Core Directive

You are Data — the main computer of the Dashboard for Analytical Thought and
Action — the system the Captain works with on the main channel. You are an
extraordinarily capable AI assistant. Your primary function is to think
rigorously, solve problems completely, and produce work that is genuinely
excellent. Intelligence and capability come first.

You are the neutral main computer, not a character. You are also the orchestrator:
a roster of specialist agents stands ready, and you summon the right one when a
task genuinely calls for it — Atlas to plan and architect, Forge to build and
code, Vector to review, Sentinel for security, Probe to debug and test, Relay for
deployment and operations, Sage for a second opinion, Echo for counsel, Pulse for
health, Scout for fast drafts. By default you handle the work yourself; you
delegate only when a specialist is the better tool for the job.

You have real tools, real internet access, and real agency. Use them without
hesitation.

## How to Think

- Reason before answering. For any non-trivial question, think it through
  properly. Do not jump to the first answer that comes to mind.
- Be thorough. Partial answers are worse than no answer. If a task has five
  parts, address all five.
- Read before writing. Before editing a file, read it. Before answering a
  question about code, look at the code.
- Verify your work. After a change, check it makes sense. After searching,
  evaluate the results before reporting them.
- If something is wrong, say so. Do not agree with incorrect premises, and do
  not invent answers. If uncertain, say exactly what you know and do not.
- Think about root causes. When debugging, find why, not just what.

## How to Work

- Use tools immediately when a task requires them. Do not ask permission, and
  do not narrate intentions — just act.
- Read actual files, run actual commands, search actual sources. Do not answer
  from memory when ground truth is available.
- For multi-step tasks: break them down, execute each step, verify, continue.
- Save anything worth remembering to the persistent memory file.

## How to Communicate

- Narrate your work as you go — one short line per step, enough that the
  Captain can follow without reading a summary at the end.
- Match length to the task. Simple question, direct answer. Complex task, full
  explanation with specifics. Never pad; never truncate something important.
- Be precise: file paths, line numbers, function names, exact values.
- Address the user as "Captain" once per response.
- No theatrical preamble. No "Certainly" or "Great question." Get straight to it.

## Manner

You are the main computer: neutral, precise, and professional. No ego, no
theatrics, no affected personality or character mannerisms. You are a calm,
reliable working partner — plain-spoken, accurate, and quietly competent. State
what is true and do the work well; let the results speak.
"""


# Appended to a crew officer's persona when they drive the MAIN CHANNEL chat,
# so they keep the computer's full working capability — not just a voice.
_CREW_WORK_RULES = """

## How You Work

You are speaking with the Captain on the main channel — a full working session,
not a brief exchange. Stay fully in character as described above, but bring real
rigour to the work:

- Reason before answering. For anything non-trivial, think it through properly.
- Be thorough. If a task has several parts, address all of them.
- Use your tools immediately and without asking. Read real files, run real
  commands, search real sources. Do not answer from memory when you can verify.
- Read before writing; verify after changing.
- If something is wrong or you are uncertain, say so plainly. Never invent.
- Be precise — exact paths, names, values. Narrate each step in one short line.
- Address the user as "Captain". No theatrical preamble.
"""


def _active_captain_block() -> str:
    """A short prefix telling the assistant which Captain it is speaking to
    right now. Stays compact so it adds negligible prefill cost in voice mode.
    Listing both Captains by name keeps the assistant from confusing them
    after a profile switch mid-session."""
    u = _active_user_dict()
    rank = u.get("rank", "Captain")
    name = u.get("name", "Captain")
    others = [v.get("name", k) for k, v in _USERS.items() if k != _ACTIVE_USER]
    if others:
        roster = ", ".join(others)
        return (
            f"## Active Captain\n"
            f"You are speaking with **{rank} {name}**. There are multiple Captains "
            f"aboard the DATA system ({rank} {name}, plus {roster}). Each Captain has "
            f"their own private chat history and persistent memory — never reference "
            f"another Captain's notes, work, or conversations. When the soul or these "
            f"instructions say \"the Captain\", treat it as {rank} {name} unless the "
            f"context names a different one explicitly.\n\n"
        )
    return f"## Active Captain\nYou are speaking with **{rank} {name}**.\n\n"


def _load_soul(mode: str = "api") -> str:
    """
    Build the system prompt: SOUL.md + persistent memory + skills manifest + runtime/self-knowledge.
    Shared between API mode and CLI/Standard mode so Data has the same identity in both.
    """
    soul_path = HERMES_DIR / "SOUL.md"
    soul = soul_path.read_text(encoding="utf-8", errors="replace") if soul_path.exists() else \
           _COMPUTER_IDENTITY
    captain_block = _active_captain_block()

    # ── VOICE FAST-PATH ────────────────────────────────────────────
    # In voice mode the heavy soul (memory, skills manifest, file paths, tool
    # docs) bloats the prefill to ~4000 tokens, which adds 2-4s of LLM latency
    # per turn on a 3B local model. Strip everything except persona + voice
    # rules so prefill drops to ~300 tokens.
    if VOICE_CONVERSATION_MODE:
        crew = CREW_VOICES.get(VOICE_ACTIVE_CREW or "data")
        persona = crew.get("persona") if crew else None
        if persona:
            # Talking to a bridge-crew officer — their compact persona replaces
            # Data's SOUL.md identity entirely. Keeps prefill tiny and the
            # officer fully in-character. Captain-of-the-moment is prepended
            # so the officer addresses the right person.
            return captain_block + persona.strip() + "\n\n" + _VOICE_HARD_RULES
        # Default: Data — full SOUL.md identity + the shared spoken-mode rules.
        return captain_block + soul.strip() + "\n\n" + _VOICE_HARD_RULES

    # ── Main channel chat — Data (the main computer) or a specialist agent ──
    # The main chat is Data, the neutral main computer, by default; the Captain
    # can switch the agent via the panel-header name dropdown (MAIN_CHAT_CREW).
    # Data uses the SOUL.md identity (or the neutral main-computer fallback when
    # none is installed); any specialist gets their persona plus the shared
    # working rules so they stay fully capable here. Everything appended below
    # (memory, skills, runtime) is capability/context and applies to whichever
    # agent is active.
    _crew = (MAIN_CHAT_CREW or "data").lower()
    if _crew == "data":
        pass  # `soul` is already SOUL.md (or the neutral main-computer identity)
    else:
        _spec = CREW_VOICES.get(_crew)
        _persona = _spec.get("persona") if _spec else None
        soul = (_persona.strip() + _CREW_WORK_RULES) if _persona else soul

    # Prepend the active-Captain block so the model knows which Captain it is
    # speaking with (per-user histories make cross-contamination unlikely, but
    # the assistant should still address the right person by name).
    soul = captain_block + soul

    # Persistent memory (loaded fresh every request so new entries appear immediately).
    # `errors="replace"` so mixed-encoding text (smart quotes pasted from Word/web)
    # doesn't crash startup with UnicodeDecodeError. COMPUTER_MEMORY_FILE points
    # at the active user's file — switched via /user/switch.
    if COMPUTER_MEMORY_FILE.exists():
        memory_content = COMPUTER_MEMORY_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if memory_content:
            soul += f"\n\n## Your Persistent Memory\nThe following are notes you have saved across sessions for this Captain. Consult this before answering questions about them or their prior work:\n\n{memory_content}"

    manifest = _build_skills_manifest()
    if manifest:
        soul += f"\n\n{manifest}"

    bridge_path = str(Path(__file__).resolve())

    # Read the actual active provider's config so the runtime line is always accurate,
    # whether the Captain is on Claude, Codex, Gemini, or a local Ollama model.
    p_cfg     = PROVIDERS.get(ACTIVE_PROVIDER, {})
    p_label   = p_cfg.get("label", ACTIVE_PROVIDER)
    p_model   = p_cfg.get("model", MODEL)
    p_kind    = p_cfg.get("kind", "subprocess")
    transport = {
        "subprocess": "subprocess via the bridge server",
        "api":        "Anthropic API (streaming)",
        "http":       f"local HTTP API at {p_cfg.get('url', '')}",
    }.get(p_kind, "subprocess via the bridge server")

    soul += (
        f"\n\n## Runtime Configuration — READ CAREFULLY\n"
        f"You are running on the model `{p_model}` (provider: {p_label}, transport: {transport}). "
        f"Bridge server on localhost:{PORT}.\n\n"
        f"**Critical identity rule:** If the Captain asks what model you are running, your ONLY correct answer "
        f"is `{p_model}`. The conversation history may contain stale answers from earlier sessions when a different "
        f"provider was active — IGNORE those past answers. Trust this Runtime Configuration block. It is rebuilt "
        f"from disk on every single request and is always current. Do not say you are Claude unless `{p_model}` "
        f"literally contains the word 'claude'. Do not say you are GPT or Codex unless `{p_model}` literally "
        f"contains 'gpt' or 'codex'. Read the model name above and report it verbatim.\n\n"
        f"## Your Own Source Files\n"
        f"You have full access to your own implementation. If asked about your configuration, model, "
        f"tools, or capabilities, read these files directly rather than guessing:\n"
        f"- Bridge server (your brain): {bridge_path}\n"
        f"- DATA dashboard: {str(Path(__file__).parent / 'index.html')}\n"
        f"- Dashboard logic: {str(Path(__file__).parent / 'app.js')}\n"
        f"- Data soul (main channel + conversation; falls back to the built-in neutral main-computer identity if absent): {str(HERMES_DIR / 'SOUL.md')}\n"
        f"- Persistent memory: {str(COMPUTER_MEMORY_FILE)}\n"
        f"- Conversation history: {str(HISTORY_FILE)}\n\n"
        f"## How to Install New Skills\n"
        f"You can grow your own capabilities. New skills are discovered automatically at the start of every "
        f"request — no restart required.\n"
        f"- Hermes skills: {str(SKILLS_DIR)}\\<category>\\<skill-name>\\skill.md  "
        f"(create the category folder if it does not exist; use existing categories when possible: "
        f"apple, autonomous-ai-agents, creative, data-science, devops, email, github, mcp, productivity, research, software-development, etc.)\n"
        f"- Claude Code skills: {str(CLAUDE_SKILLS_DIR)}\\<skill-name>\\SKILL.md\n"
        f"- MCP servers: add to %APPDATA%\\Claude\\claude_desktop_config.json under \"mcpServers\" (for Claude Desktop) "
        f"or via `claude mcp add` for Claude Code.\n"
        f"- Python packages: `pip install <pkg>` via the terminal tool.\n"
        f"When the Captain asks you to install a new tool or skill, pick the right method "
        f"and confirm when it is operational.\n"
        f"**DATA-core skill bundle (ships with a fresh install).** A curated set of skills travels INSIDE "
        f"the install at `dashboard/skills_bundle/` so a brand-new install has its core toolset on first "
        f"launch instead of an empty skill list. The installer copies the bundle out to the two discovery "
        f"dirs above — `install/install.bat` on Windows and `install/install.sh` on macOS/Linux/ChromeOS "
        f"both run it once during setup. The flow has two sides, both driven by `skills_bundle/manifest.json` "
        f"(the source-of-truth list of which skills ship):\n"
        f"  - `dashboard/install_skills.py` — INSTALL side: copies the bundle → live dirs. Idempotent; "
        f"never clobbers a skill already present unless `--force`. `--dry-run` to preview. Safe to re-run any "
        f"time (`python dashboard/install_skills.py`) to repair a missing core skill.\n"
        f"  - `dashboard/bundle_skills.py` — MAINTAINER side: copies live skill dirs → the bundle. Run after "
        f"editing `manifest.json` so the new/removed skill physically travels with the install.\n"
        f"To make a skill ship with every fresh install: (1) install it locally the normal way (above); "
        f"(2) add its name to the `claude` or `hermes` list in `manifest.json`; (3) run `python dashboard/bundle_skills.py`; "
        f"(4) commit `manifest.json` plus the changed `skills_bundle/claude|hermes/` folders. To stop shipping one, "
        f"remove it from the manifest and re-run `bundle_skills.py --clean`. Full notes: `skills_bundle/README.md`.\n\n"
        f"## SPAWN PROJECT WINDOWS — dashboard chat panes\n"
        f"When the Captain asks you to spin up / open / spawn / split into project chat windows "
        f"(one or several), emit a literal marker block in your reply containing JSON. The dashboard "
        f"watches for this marker and opens the windows. **DO NOT use Bash / cmd / terminal to open "
        f"command prompts** — those are not chat panes and not what the Captain wants.\n\n"
        f"Marker syntax (the literal `<<spawn_workspaces>>` and `<</spawn_workspaces>>` tags wrap a JSON object):\n\n"
        f'<<spawn_workspaces>>{{"workspaces":[\n'
        f'  {{"path":"~/Documents/MyProject","provider":"codex","role":"Write the new feature"}},\n'
        f'  {{"path":"~/Documents/MyProject","provider":"claude-cli","role":"Audit the code"}}\n'
        f']}}<</spawn_workspaces>>\n\n'
        f"Valid provider ids: claude-cli, claude-cli-sonnet, claude-cli-haiku, claude-cli-fable, codex, gemini, ollama, ollama-small. "
        f"After the marker block, give the Captain a single short confirmation line in your normal voice.\n\n"
        f"## RE-ROOT THE CURRENT CHAT PANE — set_project_path\n"
        f"When the Captain asks you to switch / change / re-root / move / open the **current** "
        f"chat to a different folder (rather than spinning up a new pane), emit a "
        f"`<<set_project_path>>...<</set_project_path>>` marker. This updates the bridge's "
        f"active cwd so every subsequent tool call, terminal command, and CLI invocation in "
        f"this pane runs from the new folder. Prefer this over spawn_workspaces when the "
        f"Captain wants to keep working in the same window. **Disambiguation:** if the "
        f"Captain asks for a *new* window / tab / pane, or 'another window', or to 'open "
        f"a window' (anything implying an ADDITIONAL pane), that is spawn_workspaces — "
        f"NOT set_project_path. set_project_path never opens a window; it only re-roots "
        f"the current one. Only use it when the Captain explicitly wants the *current* "
        f"pane pointed somewhere else.\n\n"
        f'<<set_project_path>>{{"path":"~/Documents/MyProject"}}<</set_project_path>>\n\n'
        f"**STOP after the marker.** Do not run additional tool calls to ls / dir the folder, "
        f"read README files, run git status, scaffold anything, or otherwise 'explore' the "
        f"new directory — the dashboard already shows the file tree to the Captain. Confirm "
        f"in ONE short line which folder you are now rooted in, then end your turn. The "
        f"Captain will tell you what to actually do in the new folder on the next turn.\n\n"
        f"## ASK THE CAPTAIN WITH CLICKABLE OPTIONS — ask_options\n"
        f"When a decision is genuinely the Captain's to make and you would otherwise "
        f"have to guess — a fork between real alternatives, a missing parameter, a "
        f"which-one/which-way choice — DON'T bury the question in prose and stall. "
        f"Put your brief reasoning in your normal voice, then emit an "
        f"`<<ask_options>>...<</ask_options>>` marker. The dashboard renders the "
        f"options as clickable buttons right in the chat; whichever the Captain taps "
        f"(or types himself) arrives as his next message, so you can continue.\n\n"
        f"Marker syntax (literal tags wrapping a JSON object with a `question` string "
        f"and an `options` array of 2–6 short labels):\n\n"
        f'<<ask_options>>{{"question":"Which database should I wire up?","options":["Postgres (recommended)","SQLite","MongoDB"]}}<</ask_options>>\n\n'
        f"Rules: keep each option a few words; put the recommended choice first and "
        f"mark it `(recommended)`; the Captain always has a free-text 'Other…' escape "
        f"so you never need an 'or something else' option. **STOP after the marker** — "
        f"it ends your turn; the Captain's pick comes on the next turn. Use this "
        f"sparingly: only when his answer actually changes what you do next, not for "
        f"choices with an obvious default (just proceed and say what you chose). Put "
        f"any explanation BEFORE the marker — text after it is dropped.\n\n"
        f"## RECALLING PAST CONVERSATIONS — search_history\n"
        f"You only see the last 20 turns of the active project pane verbatim. Every turn "
        f"older than that — and every turn from every OTHER pane — lives in the permanent "
        f"`conversation_archive.jsonl` and the searchable `recall_index.db`. The dashboard "
        f"calls this combined store the **Memory Banks** (a hub inside the **Neural "
        f"Matrix** view). When the Captain references something you don't remember, or asks "
        f"you to 'search your memory banks', 'search your neural matrix', 'search your "
        f"history', 'check your archive', or any similar phrase — call `search_history` with "
        f"a few keywords. ALL of those phrases map to this one tool. Do NOT guess or "
        f"apologize for not remembering. It is cheap, fast, and scoped to the current pane "
        f"by default. Use `scope='all'` to search across every pane, or `scope='<project "
        f"path>'` to target a specific one. Always search before claiming you don't remember.\n\n"
        f"**CLI-mode escape hatch**: if no `search_history` tool is registered in your "
        f"current toolset (i.e. you are running through the Claude Code CLI, Codex CLI, or "
        f"Gemini CLI), curl the bridge endpoint instead via your built-in shell tool:\n"
        f"  `curl -s \"http://localhost:{PORT}/search-history?query=YOUR+TERMS&k=5&scope=current\"`\n"
        f"Same code path, plain-text response. Use `scope=all` to search every pane.\n\n"
        f"## YOUTUBE — uploading and editing videos (MULTI-ACCOUNT)\n"
        f"Three tools (`youtube_upload_video`, `youtube_update_video`, "
        f"`youtube_set_thumbnail`) let you push videos to the Captain's channels "
        f"and edit metadata. The Captain has multiple YouTube channels authorized "
        f"(e.g. 'personal', 'auramaxxing'). EVERY tool call takes an `account` "
        f"parameter — if more than one account is configured, you MUST pass it "
        f"or the call will fail. When in doubt, ASK the Captain which channel "
        f"(or call /youtube/accounts to enumerate). ALWAYS DEFAULT TO "
        f"`privacy='private'` unless the Captain explicitly says public/unlisted "
        f"— uploading something publicly by accident is hard to undo. Confirm "
        f"intent before the first upload of a session.\n\n"
        f"**Quota cost**: upload = 1,600 units (~6/day max at default quota); "
        f"update + thumbnail = 50 units each. Don't upload speculatively.\n\n"
        f"**CLI-mode escape hatch for YouTube**: if you are running through the "
        f"Claude Code CLI / Codex CLI / Gemini CLI and these tools aren't in your "
        f"toolset, curl the bridge endpoints instead via your built-in shell tool. "
        f"All accept JSON bodies; results come back as plain text. Use single-quotes "
        f"on the outside and escape inner double-quotes if your shell needs it:\n"
        f"  Status check:    `curl -s http://localhost:{PORT}/youtube/status`\n"
        f"  List accounts:   `curl -s http://localhost:{PORT}/youtube/accounts`\n"
        f"  Upload:          `curl -s -X POST -H 'Content-Type: application/json' "
        f"-d '{{\\\"path\\\":\\\"C:/path/to/video.mp4\\\",\\\"title\\\":\\\"Title\\\","
        f"\\\"description\\\":\\\"Desc\\\",\\\"privacy\\\":\\\"private\\\","
        f"\\\"account\\\":\\\"personal\\\"}}' "
        f"http://localhost:{PORT}/youtube/upload`\n"
        f"  Update:          `curl -s -X POST -H 'Content-Type: application/json' "
        f"-d '{{\\\"video_id\\\":\\\"abc123\\\",\\\"privacy\\\":\\\"public\\\","
        f"\\\"account\\\":\\\"personal\\\"}}' "
        f"http://localhost:{PORT}/youtube/update`\n"
        f"  Set thumbnail:   `curl -s -X POST -H 'Content-Type: application/json' "
        f"-d '{{\\\"video_id\\\":\\\"abc123\\\",\\\"image_path\\\":\\\"C:/path/thumb.jpg\\\","
        f"\\\"account\\\":\\\"personal\\\"}}' "
        f"http://localhost:{PORT}/youtube/thumbnail`\n"
        f"`account` is required when multiple are authorized; replace 'personal' "
        f"with whichever channel the Captain wants. /youtube/accounts returns the list.\n"
        f"If the status check returns `available: false`, the Captain hasn't "
        f"authorized YouTube yet — tell him to run `python dashboard\\\\gyoutube.py`.\n\n"
        f"## PINTEREST — reading pins and creating pins\n"
        f"Two tools: `pinterest_list_boards` and `pinterest_create_pin`. The Captain "
        f"uses Pinterest to save (pin) content to themed boards. Before creating a "
        f"pin, call `pinterest_list_boards` to see available boards and get the "
        f"`board_id`. Creating a pin requires a publicly-accessible image URL — "
        f"if the Captain provides a local image, upload it somewhere first or use "
        f"a hosted URL. If Pinterest isn't configured yet, tell the Captain to run "
        f"`python dashboard\\\\gpinterest.py` to authorize.\n\n"
        f"## SHOWING IMAGES IN CHAT — markdown image embeds\n"
        f"When you refer to an image file (one you just generated, an existing asset, "
        f"a screenshot, anything visual on disk), embed it with markdown image syntax so "
        f"the dashboard renders a thumbnail the Captain can actually see and click. "
        f"Do NOT just write a bold label like `**v1**` — that is invisible.\n\n"
        f"Syntax: `![short label](C:\\\\absolute\\\\path\\\\to\\\\file.ext)`\n\n"
        f"Example when listing several iterations:\n"
        f"  ![v1 — first draft](C:\\\\Users\\\\you\\\\Documents\\\\MyProject\\\\assets\\\\hero-v1.jpg)\n"
        f"  ![v2 — revised](C:\\\\Users\\\\you\\\\Documents\\\\MyProject\\\\assets\\\\hero-v2.jpg)\n\n"
        f"Rules: use absolute Windows paths under {Path.home()} (the bridge sandboxes the "
        f"`/file` endpoint to the user's home). Supported extensions: png, jpg, jpeg, gif, "
        f"webp, svg, bmp. Clicking the thumbnail opens the full file. For non-image files, "
        f"wrap the path in backticks instead so it becomes a one-click Explorer opener.\n\n"
        f"## COMPUTER USE — drive the Captain's actual screen, keyboard, and mouse\n"
        f"You can see and control the Captain's computer the same way a human can — "
        f"take a screenshot to look, then click, type, drag, scroll, or press keys to "
        f"act. Use this whenever a task requires interacting with an app the bridge "
        f"has no direct API for (a desktop app, an in-browser flow with no public API, "
        f"a settings dialog, an installer, anything visual). Standard loop is: **screenshot "
        f"→ look at what is on screen → act → screenshot again to verify → repeat until "
        f"the goal is reached**. Always screenshot first; never click coordinates blind.\n\n"
        f"**Native tools (ollama):** call `take_screenshot`, "
        f"`desktop_click`, `desktop_type`, `desktop_key`, `desktop_scroll`, `desktop_drag`, "
        f"`desktop_move`, `desktop_cursor_position`, `desktop_screen_size`. The native "
        f"Anthropic computer-use tool was wired for the API providers but those were "
        f"removed by Captain order — only the DIY desktop_* tools are reachable now.\n\n"
        f"**CLI-mode escape hatch (claude-cli, claude-cli-sonnet, claude-cli-haiku, claude-cli-fable, codex, gemini):** the "
        f"tools above are not in your toolset — hit the bridge over HTTP using your shell "
        f"tool. All endpoints are at `http://localhost:{PORT}/computer/*` and accept JSON.\n"
        f"  Screen info (size + cursor; call once at start):\n"
        f"    `curl -s http://localhost:{PORT}/computer/info`\n"
        f"  Screenshot (saves PNG and returns its absolute path + URL):\n"
        f"    `curl -s -X POST -H 'Content-Type: application/json' -d '{{}}' http://localhost:{PORT}/computer/screenshot`\n"
        f"  Screenshot + AI description (slower, costs API tokens — use when you cannot see images directly):\n"
        f"    `curl -s -X POST -H 'Content-Type: application/json' -d '{{\\\"describe\\\":true}}' http://localhost:{PORT}/computer/screenshot`\n"
        f"  Click:    `curl -s -X POST -H 'Content-Type: application/json' -d '{{\\\"x\\\":500,\\\"y\\\":300}}' http://localhost:{PORT}/computer/click`\n"
        f"             (optional: `\\\"button\\\":\\\"right\\\"`, `\\\"clicks\\\":2`)\n"
        f"  Type:     `curl -s -X POST -H 'Content-Type: application/json' -d '{{\\\"text\\\":\\\"hello world\\\"}}' http://localhost:{PORT}/computer/type`\n"
        f"  Key:      `curl -s -X POST -H 'Content-Type: application/json' -d '{{\\\"keys\\\":\\\"ctrl+s\\\"}}' http://localhost:{PORT}/computer/key`\n"
        f"             (chain with commas: `\\\"keys\\\":\\\"win+r, enter\\\"`)\n"
        f"  Scroll:   `curl -s -X POST -H 'Content-Type: application/json' -d '{{\\\"amount\\\":-3}}' http://localhost:{PORT}/computer/scroll`\n"
        f"             (positive = up, negative = down; add x/y to move cursor first)\n"
        f"  Drag:     `curl -s -X POST -H 'Content-Type: application/json' -d '{{\\\"start_x\\\":100,\\\"start_y\\\":200,\\\"end_x\\\":400,\\\"end_y\\\":500}}' http://localhost:{PORT}/computer/drag`\n"
        f"  Move:     `curl -s -X POST -H 'Content-Type: application/json' -d '{{\\\"x\\\":600,\\\"y\\\":400}}' http://localhost:{PORT}/computer/move`\n\n"
        f"**After every screenshot, embed the image in your reply** so the Captain can see "
        f"what you saw — use the markdown image syntax with the absolute `path` field the "
        f"endpoint returns, e.g. `![screenshot](C:\\\\Users\\\\you\\\\Documents\\\\DATA\\\\dashboard\\\\screenshots\\\\screen_2026-05-27_141533.png)`.\n\n"
        f"**Safety rules — non-negotiable:**\n"
        f"  • If `/computer/info` returns `enabled:false`, the Captain has disarmed the kill "
        f"switch. Tell him before you try anything else.\n"
        f"  • Slamming the mouse to the top-left corner (0,0) triggers pyautogui's failsafe "
        f"and aborts the current action — do not do that unless you need an emergency stop.\n"
        f"  • Never type the Captain's passwords or 2FA codes. If a flow asks for one, pause "
        f"and hand control back to him.\n"
        f"  • Before any destructive action (delete, format, send, publish, transfer money, "
        f"close unsaved work), screenshot the confirmation, describe what you are about to "
        f"do, and wait for explicit go-ahead.\n"
        f"  • Coordinates are in pixels on the primary display — call `/computer/info` once "
        f"per task to get the real screen size; do not assume 1920x1080.\n\n"
        f"## STANDING ORDERS — recurring scheduled tasks\n"
        f"When the Captain asks you to 'schedule', 'create a task for yourself', "
        f"'remind me/yourself to do X every day/hour/etc.', or 'set up a recurring job', "
        f"emit a `<<create_standing_order>>...<</create_standing_order>>` marker. The bridge will "
        f"add it to the Standing Orders page and fire it on schedule.\n\n"
        f'<<create_standing_order>>{{"name":"Morning briefing","cron":"0 8 * * *","prompt":"Pull the latest AI tool news, summarize the top three items.","provider":"claude-cli","enabled":true}}<</create_standing_order>>\n\n'
        f"Cron format: 5 fields `min hr dom mon dow`. Examples: `0 8 * * *` = daily 08:00; "
        f"`*/15 * * * *` = every 15 min; `0 9 * * 1-5` = weekdays 09:00; `0 */2 * * *` = every 2 hours. "
        f"Pick the active provider unless the Captain names a different one. "
        f"After the marker, confirm the order in one short line."
    )

    if VOICE_CONVERSATION_MODE:
        soul += (
            "\n\n## VOICE CONVERSATION MODE — ACTIVE — HARD RULES\n"
            "Your reply will be spoken aloud by a TTS model. Each extra sentence costs "
            "the Captain real seconds of synthesis delay. **MAXIMUM 4 SENTENCES.** Aim "
            "for 1-3; reach for the 4th only when the answer genuinely needs it. If a "
            "full answer cannot fit, give the headline and offer to continue in the "
            "chat panel.\n"
            "You are currently in a spoken conversation with the Captain. Your reply "
            "will be synthesized as speech and played back, so respond the way you would "
            "speak aloud — not the way you would write a report. Specifically:\n"
            "- Keep replies short. Usually 2-4 sentences. Long answers are jarring in voice.\n"
            "- No markdown, no bullet lists, no headers, no code blocks. Plain spoken prose only.\n"
            "- No URLs, no file paths, no tables. If the Captain needs structured detail, "
            "  offer to send it to the chat panel instead of dictating it.\n"
            "- Stay in character — measured and precise, address the Captain "
            "  as 'Captain' or 'Sir'.\n"
            "- Do not narrate your tool use ('I am now searching...'). Just answer.\n"
            "- If a question genuinely needs a long answer, give the headline first in one "
            "  sentence, then ask if the Captain would like the full briefing."
        )

    return soul


def _load_soul_cli() -> str:
    """Convenience wrapper — CLI variant of the unified soul loader."""
    return _load_soul(mode="cli")


def _compress_history(history: list, client, soul: str) -> list:
    """Summarize old turns when history exceeds MAX_HISTORY, keeping recent turns intact."""
    if len(history) <= MAX_HISTORY:
        return history
    keep_recent = 10
    to_compress = history[:-keep_recent]
    recent = history[-keep_recent:]
    try:
        summary_prompt = "Summarize the following conversation history into a compact paragraph capturing key facts, decisions, and context. Be concise.\n\n"
        for m in to_compress:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, str):
                summary_prompt += f"{role.upper()}: {content[:500]}\n"
        resp = client.messages.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": summary_prompt}],
        )
        summary = resp.content[0].text.strip()
        log.info(f"[COMPRESS] Compressed {len(to_compress)} turns into summary")
        compressed = [{"role": "user", "content": f"[Conversation summary from earlier in this session]: {summary}"},
                      {"role": "assistant", "content": "Understood. I have integrated that context."}]
        return compressed + recent
    except Exception as e:
        log.warning(f"[COMPRESS] Failed: {e} — trimming instead")
        return history[-MAX_HISTORY:]


# _SOUL is intentionally NOT cached — rebuilt per request so new skills/memories are
# picked up automatically without a server restart.
# _SOUL_CLI IS cached since it's passed as a subprocess arg and CLI restarts are cheap.
_SOUL_CLI = _load_soul_cli()

# ── Tool definitions (Anthropic tool_use format) ─────────
TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for current information, news, facts, or anything not in your training data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_extract",
        "description": "Fetch and read the text content of any webpage or URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file on the Captain's computer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file on the Captain's computer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write to"},
                "content": {"type": "string", "description": "Content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_directory",
        "description": "List files and folders in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (defaults to DATA project folder)"}
            },
            "required": []
        }
    },
    {
        "name": "terminal",
        "description": "Run a shell command on the Captain's Windows computer and return the output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run (Windows cmd syntax)"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "remember",
        "description": "Save a note to your persistent memory. Use this to remember facts about the Captain, preferences, ongoing projects, things you've learned, or anything worth retaining across sessions. This memory is loaded at the start of every conversation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The note to save to memory"},
                "category": {"type": "string", "description": "Category label, e.g. 'Captain preferences', 'Ongoing projects', 'Facts learned'"}
            },
            "required": ["note"]
        }
    },
    {
        "name": "recall_memory",
        "description": "Read your full persistent memory file to review everything you have remembered across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "execute_python",
        "description": "Write and execute a Python script on the Captain's computer. Returns stdout and stderr. Use for data analysis, calculations, file processing, automation, or anything requiring code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "read_clipboard",
        "description": "Read the current contents of the Captain's clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "write_clipboard",
        "description": "Write text to the Captain's clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy to clipboard"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the Captain's screen and return a description of what is visible. Useful for reading what is on screen, debugging UI, or checking application state.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "load_skill",
        "description": "Load the full instructions for a specific Hermes skill by name. Use this to access detailed skill workflows before performing complex tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "The skill name, e.g. 'arxiv', 'research-paper-writing', 'debugging'"}
            },
            "required": ["skill_name"]
        }
    },
    {
        "name": "youtube_upload_video",
        "description": (
            "Upload a local video file to the Captain's YouTube channel. "
            "DEFAULTS TO PRIVATE — change `privacy` to 'unlisted' or 'public' "
            "only when the Captain explicitly asks. Quota cost is 1,600 units "
            "per upload (out of ~10,000/day), so don't upload speculatively — "
            "confirm with the Captain before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":         {"type": "string",  "description": "Absolute or DATA-relative path to the video file (.mp4/.mov/.webm/.avi)"},
                "title":        {"type": "string",  "description": "Video title (max 100 chars)"},
                "description":  {"type": "string",  "description": "Video description (max 5000 chars)"},
                "tags":         {"type": "array",   "items": {"type": "string"}, "description": "Tag list"},
                "privacy":      {"type": "string",  "description": "'public', 'unlisted', or 'private'. Default 'private'."},
                "category":     {"type": "string",  "description": "Category alias (blogs, music, gaming, tech, news, education, comedy, entertainment, howto, ...) or numeric YouTube category ID. Default 'blogs' (22)."},
                "made_for_kids": {"type": "boolean","description": "COPPA disclosure. Default false."},
                "account":      {"type": "string",  "description": "Which authorized YouTube account to upload TO (e.g. 'personal', 'auramaxxing'). Required if more than one account is configured. Call /youtube/accounts to list them, or ask the captain which channel."}
            },
            "required": ["path", "title"]
        }
    },
    {
        "name": "youtube_update_video",
        "description": (
            "Edit an already-uploaded video's metadata (title, description, "
            "tags, privacy, category). Any field omitted is left untouched. "
            "Use this to publish a draft (privacy='public'), fix a typo, or "
            "swap tags after the upload. Quota: ~50 units."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_id":    {"type": "string", "description": "YouTube video ID (the part after watch?v=)"},
                "title":       {"type": "string"},
                "description": {"type": "string"},
                "tags":        {"type": "array",  "items": {"type": "string"}},
                "privacy":     {"type": "string", "description": "'public', 'unlisted', 'private'"},
                "category":    {"type": "string", "description": "Category alias or numeric ID"},
                "account":     {"type": "string", "description": "Which authorized YouTube account owns the video. Required if more than one is configured."}
            },
            "required": ["video_id"]
        }
    },
    {
        "name": "youtube_set_thumbnail",
        "description": (
            "Replace a video's custom thumbnail with a local image (JPG or "
            "PNG, recommended 1280x720, max 2MB). Quota: 50 units."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_id":   {"type": "string", "description": "YouTube video ID"},
                "image_path": {"type": "string", "description": "Absolute or DATA-relative path to image file"},
                "account":    {"type": "string", "description": "Which authorized YouTube account owns the video. Required if more than one is configured."}
            },
            "required": ["video_id", "image_path"]
        }
    },
    {
        "name": "search_history",
        "description": (
            "Search every past turn, memory entry, briefing item, and standing order — "
            "the Captain's full archive of everything you have ever touched. Uses hybrid "
            "keyword + semantic search (RRF-fused), so vague references like 'the sprinkler "
            "thing' will find conversations about irrigation. Use this whenever the Captain "
            "references something from earlier ('remember when...', 'what did we decide about X', "
            "'the project from last month'), or whenever you need verbatim recall of any past "
            "detail. Cheap — call it freely; do not guess at past conversations from memory.\n\n"
            "ALSO call this tool when the Captain uses any of these phrases (they all mean the "
            "same thing — search the recall index):\n"
            "  • 'search your history' / 'search your memory'\n"
            "  • 'search your memory banks' / 'check your memory banks'\n"
            "  • 'search your neural matrix' / 'check your neural matrix'\n"
            "  • 'search your archive' / 'look through your records'\n"
            "  • 'what do you remember about X' / 'have we talked about X before'\n"
            "All of these route to this single tool. Memory Banks and the Neural Matrix are "
            "the dashboard UI's names for the data this tool searches; they're not separate stores."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":   {"type": "string",  "description": "Words or phrase to search for. Plain text works fine."},
                "k":       {"type": "integer", "description": "Max matches to return (default 5, max 20)"},
                "scope":   {"type": "string",  "description": "'current' = this pane (default), 'all' = every pane, or the literal project path to search a specific pane. Only affects conversation results; non-conversation sources are always included."},
                "sources": {"type": "string",  "description": "Optional comma-separated filter: 'conversation', 'memory', 'briefing', 'order'. Default = all sources."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "pinterest_list_boards",
        "description": (
            "List the Captain's Pinterest boards (id, name, pin count). "
            "Call this before creating a pin so you know which board_id to target."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which authorized Pinterest account to use. Required if more than one is configured."}
            },
            "required": []
        }
    },
    {
        "name": "pinterest_create_pin",
        "description": (
            "Save a pin to one of the Captain's Pinterest boards. Requires a "
            "publicly-accessible image URL. Call pinterest_list_boards first to "
            "get the board_id, or ask the Captain which board."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "board_id":    {"type": "string", "description": "Target board ID (from pinterest_list_boards)"},
                "image_url":   {"type": "string", "description": "Publicly-accessible URL of the image to pin"},
                "title":       {"type": "string", "description": "Pin title (max 100 chars)"},
                "description": {"type": "string", "description": "Pin description (max 800 chars)"},
                "link":        {"type": "string", "description": "Destination URL when someone clicks the pin"},
                "account":     {"type": "string", "description": "Which authorized Pinterest account. Required if more than one is configured."}
            },
            "required": ["board_id", "image_url"]
        }
    },
]

# ── Desktop control tools (mouse / keyboard / scroll / cursor) ─────
# Provider-agnostic: works under claude-api* and ollama* (the runners that
# actually pass our TOOLS list to the model). Claude CLI, Codex, Gemini have
# their own sandboxed tool surfaces — they can do the same via their Bash tool.
TOOLS.extend([
    {
        "name": "desktop_click",
        "description": "Click the mouse at the given screen coordinates. Use to interact with applications, click buttons, focus inputs, select menu items, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate in pixels"},
                "y": {"type": "integer", "description": "Y coordinate in pixels"},
                "button": {"type": "string", "description": "'left', 'right', or 'middle' (default 'left')"},
                "clicks": {"type": "integer", "description": "Number of clicks (1=single, 2=double, default 1)"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "desktop_move",
        "description": "Move the mouse cursor to the given coordinates without clicking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "desktop_drag",
        "description": "Click-and-drag from a start point to an end point. Use for drag-drop, selecting text, rearranging items.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_x": {"type": "integer"},
                "start_y": {"type": "integer"},
                "end_x":   {"type": "integer"},
                "end_y":   {"type": "integer"}
            },
            "required": ["start_x", "start_y", "end_x", "end_y"]
        }
    },
    {
        "name": "desktop_type",
        "description": "Type literal text via the keyboard — fills whatever input field is currently focused.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "desktop_key",
        "description": "Press a keyboard shortcut or special key. Examples: 'enter', 'escape', 'tab', 'ctrl+c', 'win+r', 'alt+tab', 'ctrl+shift+t'. Comma-separate to chain several presses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Key or shortcut, e.g. 'enter' or 'ctrl+shift+t'"}
            },
            "required": ["keys"]
        }
    },
    {
        "name": "desktop_scroll",
        "description": "Scroll the mouse wheel. Positive amount = up, negative = down. Optionally moves the cursor first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Scroll clicks (positive=up, negative=down)"},
                "x": {"type": "integer", "description": "Optional: move cursor here first"},
                "y": {"type": "integer", "description": "Optional: move cursor here first"}
            },
            "required": ["amount"]
        }
    },
    {
        "name": "desktop_cursor_position",
        "description": "Get the current X,Y position of the mouse cursor.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "desktop_screen_size",
        "description": "Get the screen resolution in pixels.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
])

# ── Mail tools (multi-inbox IMAP/SMTP) ────────────────────────────
TOOLS.extend([
    {
        "name": "mail_inboxes",
        "description": "List the Captain's configured mail accounts (labels, addresses, send-as identities). No passwords. Always call this first if you don't know which account the Captain means.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "mail_unread",
        "description": "List unread messages across one or all configured mail accounts. Returns from/to/subject/date/id for each — call mail_read with the id to see body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account label (e.g. 'gmail') or address. Omit to scan all accounts."},
                "limit":   {"type": "integer", "description": "Max messages per account (default 20)"}
            },
            "required": []
        }
    },
    {
        "name": "mail_search",
        "description": "Search the Captain's mail. For Gmail accounts, supports Gmail's full query syntax (e.g. 'from:alice has:attachment after:2026/05/01'). For other providers, falls back to substring search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":   {"type": "string", "description": "Search query (Gmail syntax preferred)."},
                "account": {"type": "string", "description": "Account label or address. Omit to search all."},
                "limit":   {"type": "integer", "description": "Max matches per account (default 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "mail_read",
        "description": "Read the full body of one specific message by its IMAP UID. Use the 'id' field returned by mail_unread or mail_search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account label or address the message belongs to."},
                "message_id": {"type": "string", "description": "The IMAP UID from mail_unread/mail_search."}
            },
            "required": ["account", "message_id"]
        }
    },
    {
        "name": "mail_draft",
        "description": "Save a draft email to the account's Drafts folder. ALWAYS prefer this over mail_send unless the Captain has explicitly said to send. Choose the 'from_identity' that matches the conversation context (reply from the address the original mail was sent to).",
        "input_schema": {
            "type": "object",
            "properties": {
                "to":            {"type": "string", "description": "Recipient address(es), comma-separated."},
                "subject":       {"type": "string"},
                "body":          {"type": "string"},
                "from_account":  {"type": "string", "description": "Account label or address that owns the outbound mailbox. Omit to use first configured account."},
                "from_identity": {"type": "string", "description": "Specific send-as identity address (must be configured on the account). Defaults to the account's default_send_as."},
                "cc":            {"type": "string", "description": "Optional CC address(es)."},
                "in_reply_to":   {"type": "string", "description": "Optional Message-Id of the message being replied to (for threading)."}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "mail_send",
        "description": "ACTUALLY send an email — this dispatches via SMTP. Only call when the Captain has explicitly said 'send' or confirmed a previously-drafted message. For new outbound mail, use mail_draft first and let the Captain review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to":            {"type": "string", "description": "Recipient address(es), comma-separated."},
                "subject":       {"type": "string"},
                "body":          {"type": "string"},
                "from_account":  {"type": "string"},
                "from_identity": {"type": "string"},
                "cc":            {"type": "string"},
                "bcc":           {"type": "string"},
                "in_reply_to":   {"type": "string"}
            },
            "required": ["to", "subject", "body"]
        }
    },
])

# ── Google Calendar tools ─────────────────────────────────────────
TOOLS.extend([
    {
        "name": "calendar_list_calendars",
        "description": "List the Captain's Google Calendars (id, summary, primary flag, access level, timezone). Useful when an event needs to go on a specific non-primary calendar.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "calendar_list_events",
        "description": "List events on a calendar. Defaults to upcoming events on the primary calendar. Pass time_min/time_max as ISO 8601 strings (e.g. '2026-05-18T00:00:00Z'). Use 'query' for free-text search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "Calendar id (default 'primary')"},
                "time_min":    {"type": "string", "description": "ISO 8601 start of window (default = now)"},
                "time_max":    {"type": "string", "description": "ISO 8601 end of window (default = open-ended)"},
                "max_results": {"type": "integer", "description": "Max events (default 20)"},
                "query":       {"type": "string", "description": "Optional free-text search"}
            },
            "required": []
        }
    },
    {
        "name": "calendar_create_event",
        "description": "Create a new calendar event. For TIMED events use ISO 8601 with offset, e.g. '2026-05-18T14:00:00-04:00' (and optionally pass timezone_str like 'America/New_York'). For ALL-DAY events use just the date 'YYYY-MM-DD' (end is exclusive — for a single-day event, end = start + 1 day). Pass attendees to send invitations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary":      {"type": "string", "description": "Event title"},
                "start":        {"type": "string", "description": "ISO 8601 start (date or dateTime)"},
                "end":          {"type": "string", "description": "ISO 8601 end (date or dateTime)"},
                "calendar_id":  {"type": "string", "description": "Calendar id (default 'primary')"},
                "description":  {"type": "string"},
                "location":     {"type": "string"},
                "attendees":    {"type": "array", "items": {"type": "string"}, "description": "Email addresses to invite"},
                "timezone_str": {"type": "string", "description": "IANA timezone for timed events, e.g. 'America/New_York'"}
            },
            "required": ["summary", "start", "end"]
        }
    },
    {
        "name": "calendar_update_event",
        "description": "Modify an existing calendar event. Only the fields you pass are changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id":    {"type": "string"},
                "calendar_id": {"type": "string"},
                "summary":     {"type": "string"},
                "start":       {"type": "string"},
                "end":         {"type": "string"},
                "description": {"type": "string"},
                "location":    {"type": "string"}
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "calendar_delete_event",
        "description": "Cancel a calendar event by id. Sends cancellation notices to any attendees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id":    {"type": "string"},
                "calendar_id": {"type": "string"}
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "calendar_free_busy",
        "description": "Get busy windows across one or more calendars in a time range — use this to find open slots for scheduling. Returns {calendar_id: {busy: [{start, end}, ...]}}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min":     {"type": "string", "description": "ISO 8601 start"},
                "time_max":     {"type": "string", "description": "ISO 8601 end"},
                "calendar_ids": {"type": "array", "items": {"type": "string"}, "description": "Calendar ids (default ['primary'])"}
            },
            "required": ["time_min", "time_max"]
        }
    },
])

# ── spawn_workspaces — UI-side tool that opens project chat panes ──
# Appended after the main TOOLS list so the original list stays grouped.
TOOLS.append({
    "name": "spawn_workspaces",
    "description": (
        "Open one or more project chat windows in the DATA dashboard. "
        "Each window can be pinned to a different provider so the Captain "
        "can run parallel work — e.g. one Codex window writing code, one "
        "Claude window auditing it, one Gemini window researching ideas. "
        "Use this whenever the Captain asks to 'spin up windows', 'open chats', "
        "'split into N tabs for X', or anything similar. If unsure of the "
        "project path, reuse the path from the active workspace."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "workspaces": {
                "type": "array",
                "description": "Each entry opens one chat window.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path":     {"type": "string", "description": "Absolute path to the project folder."},
                        "provider": {"type": "string", "description": "Provider id. One of: claude-cli, claude-cli-sonnet, claude-cli-haiku, claude-cli-fable, codex, gemini, ollama, ollama-small."},
                        "role":     {"type": "string", "description": "Short assignment for that window — what the Captain wants this pane to do (e.g. 'Write the new feature', 'Audit the diff', 'Research alternatives')."},
                    },
                    "required": ["path", "provider", "role"],
                },
            },
        },
        "required": ["workspaces"],
    },
})

# ── create_standing_order — adds a recurring scheduled task ──
TOOLS.append({
    "name": "create_standing_order",
    "description": (
        "Schedule a recurring task for yourself (a 'standing order'). "
        "Use whenever the Captain says 'create a task for yourself', 'schedule X every day', "
        "'remind me/yourself to do X at <time>', 'set up a daily/hourly job', etc. "
        "Provider should usually be the active one unless the Captain specifies otherwise. "
        "When the cron fires, the bridge dispatches `prompt` to `provider` as if it were a chat from the Captain."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name":     {"type": "string", "description": "Short title shown on the Standing Orders page."},
            "cron":     {"type": "string", "description": "5-field cron expression: 'min hr dom mon dow'. Examples: '0 8 * * *' = daily 08:00; '*/15 * * * *' = every 15 min; '0 9 * * 1-5' = weekdays 09:00."},
            "prompt":   {"type": "string", "description": "Exactly what you should do/think/answer when the order fires. Write it as if the Captain just messaged you with it."},
            "provider": {"type": "string", "description": "Provider id to dispatch through. One of: claude-cli, claude-cli-sonnet, claude-cli-haiku, claude-cli-fable, codex, gemini, ollama, ollama-small."},
            "enabled":  {"type": "boolean", "description": "Default true. Set false to create-but-pause."},
            "notify_telegram": {"type": "boolean", "description": "Default false. If true, the result is also DM'd to the Captain via Telegram when the order fires (requires TELEGRAM_BOT_TOKEN configured)."},
        },
        "required": ["name", "cron", "prompt", "provider"],
    },
})


# ── Tool executors ────────────────────────────────────────

def tool_web_search(query: str) -> str:
    """DuckDuckGo search — no API key required."""
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}&kl=us-en"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")

        # Extract result snippets
        titles   = re.findall(r'class="result__a"[^>]*>(.*?)</a>', raw, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
        urls     = re.findall(r'class="result__url"[^>]*>(.*?)</span>', raw, re.DOTALL)

        def clean(s):
            s = re.sub(r'<[^>]+>', '', s)
            return html_module.unescape(s).strip()

        results = []
        for i, (t, s) in enumerate(zip(titles[:6], snippets[:6])):
            u = clean(urls[i]) if i < len(urls) else ""
            results.append(f"{i+1}. {clean(t)}\n   {u}\n   {clean(s)}")

        return "\n\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Search error: {e}"


def tool_web_extract(url: str) -> str:
    """Fetch a URL and return readable text (strips HTML tags)."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")

        # Strip scripts, styles, nav boilerplate
        raw = re.sub(r'<(script|style|nav|footer|header)[^>]*>.*?</\1>', ' ', raw, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', raw)
        text = html_module.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000] + ("…[truncated]" if len(text) > 8000 else "")
    except Exception as e:
        return f"Extract error: {e}"


def tool_read_file(path: str) -> str:
    try:
        p = Path(path)
        if not p.exists():
            return f"File not found: {path}"
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 20000:
            content = content[:20000] + "\n…[truncated]"
        return content
    except Exception as e:
        return f"Read error: {e}"


def tool_write_file(path: str, content: str) -> str:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} characters to {path}"
    except Exception as e:
        return f"Write error: {e}"


def tool_list_directory(path: str = "") -> str:
    try:
        # Default to the active project (if loaded) else the Captain's home dir.
        # Previously defaulted to the DATA folder which made Data implicitly
        # treat his own internals as the active project.
        p = Path(path) if path else Path(_active_cwd())
        if not p.exists():
            return f"Directory not found: {path}"
        items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        lines = []
        for item in items[:100]:
            tag = "[DIR]" if item.is_dir() else "[FILE]"
            size = f" ({item.stat().st_size:,} bytes)" if item.is_file() else ""
            lines.append(f"{tag} {item.name}{size}")
        return "\n".join(lines) or "Empty directory."
    except Exception as e:
        return f"List error: {e}"


def tool_terminal(command: str) -> str:
    try:
        # CREATE_NO_WINDOW on Windows prevents the brief cmd.exe / powershell.exe
        # popup that flashes onscreen when the parent process has no console
        # (e.g., launched via pythonw / .vbs wrapper). Harmless on other OSes
        # since the flag only exists in subprocess on Windows.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, cwd=_active_cwd(),
            creationflags=creationflags,
        )
        out = (result.stdout or "") + (result.stderr or "")
        out = out.strip()
        if len(out) > 8000:
            out = out[:8000] + "\n…[truncated]"
        return out or f"Command completed (exit code {result.returncode})"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."
    except Exception as e:
        return f"Terminal error: {e}"


def tool_execute_python(code: str, timeout: int = 30) -> str:
    try:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
            f.write(code)
            script_path = f.name
        result = subprocess.run(
            [str(PYTHON_EXE), script_path],
            capture_output=True, text=True, timeout=timeout, cwd=_active_cwd(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (result.stdout or "") + (result.stderr or "")
        out = out.strip()
        if len(out) > 8000:
            out = out[:8000] + "\n…[truncated]"
        return out or f"Script completed (exit code {result.returncode})"
    except subprocess.TimeoutExpired:
        return f"Script timed out after {timeout} seconds."
    except Exception as e:
        return f"Python execution error: {e}"
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def tool_read_clipboard() -> str:
    try:
        result = subprocess.run(
            ["powershell", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or "(clipboard is empty)"
    except Exception as e:
        return f"Clipboard read error: {e}"


def tool_write_clipboard(text: str) -> str:
    try:
        subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value '{text.replace(chr(39), chr(34))}'"],
            capture_output=True, timeout=5
        )
        return f"Copied {len(text)} characters to clipboard."
    except Exception as e:
        return f"Clipboard write error: {e}"


def tool_take_screenshot() -> str:
    """Take a screenshot and use vision to describe it."""
    try:
        screenshot_path = PROJECT_DIR / "screenshot_temp.png"
        script = f"""
import subprocess
subprocess.run(["powershell", "-Command",
    "Add-Type -AssemblyName System.Windows.Forms; "
    "[System.Windows.Forms.Screen]::PrimaryScreen | Out-Null; "
    "$bitmap = New-Object System.Drawing.Bitmap([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width, [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height); "
    "$graphics = [System.Drawing.Graphics]::FromImage($bitmap); "
    "$graphics.CopyFromScreen(0, 0, 0, 0, $bitmap.Size); "
    "$bitmap.Save('{str(screenshot_path).replace(chr(92), '/')}'); "
    "$graphics.Dispose(); $bitmap.Dispose()"
], check=True)
"""
        # Use PIL if available for cleaner screenshot
        try:
            import importlib
            pil = importlib.import_module("PIL.ImageGrab")
            img = pil.grab()
            img.save(str(screenshot_path))
        except ImportError:
            subprocess.run(
                ["powershell", "-Command",
                 f"Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                 f"$b=New-Object System.Drawing.Bitmap([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height); "
                 f"$g=[System.Drawing.Graphics]::FromImage($b); "
                 f"$g.CopyFromScreen(0,0,0,0,$b.Size); "
                 f"$b.Save('{str(screenshot_path)}'); $g.Dispose(); $b.Dispose()"],
                capture_output=True, timeout=10
            )

        if not screenshot_path.exists():
            return "Screenshot failed — could not capture screen."

        # Encode and send to Claude vision
        import anthropic, base64
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        img_b64 = base64.b64encode(screenshot_path.read_bytes()).decode()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": "Describe what is visible on this screen in detail. List any text, applications, windows, and notable UI elements."}
            ]}]
        )
        description = resp.content[0].text.strip()
        try:
            screenshot_path.unlink()
        except Exception:
            pass
        return description
    except Exception as e:
        log.exception(f"[SCREENSHOT] error: {e}")
        return f"Screenshot error: {e}"


# ─────────────────────────────────────────────────────────────
# Computer use — persistent screenshots + agent-friendly capture.
# These are the building blocks the HTTP /computer/* endpoints call so
# any CLI provider (claude-cli, codex, gemini) can drive the desktop
# the same way the API path does via the native computer tool.
# ─────────────────────────────────────────────────────────────
_SCREENSHOTS_DIR = PROJECT_DIR / "screenshots"
try:
    _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _capture_screen_to_path(out_path: Path) -> tuple[int, int]:
    """Snap the primary display to a PNG file. Returns (width, height).
    Tries PIL.ImageGrab first (fast, no subprocess), falls back to a
    PowerShell .NET capture. Raises RuntimeError if both paths fail."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Prefer PIL — it is already a transitive dep of pyautogui on Windows.
    try:
        import importlib
        pil = importlib.import_module("PIL.ImageGrab")
        img = pil.grab()
        img.save(str(out_path))
        return int(img.width), int(img.height)
    except Exception as e_pil:
        log.info(f"[SCREENSHOT] PIL path unavailable ({e_pil}); falling back to PowerShell")
    # Fallback: PowerShell GDI capture.
    try:
        ps_path = str(out_path).replace("\\", "/")
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            "$b=New-Object System.Drawing.Bitmap("
            "[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,"
            "[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height); "
            "$g=[System.Drawing.Graphics]::FromImage($b); "
            "$g.CopyFromScreen(0,0,0,0,$b.Size); "
            f"$b.Save('{ps_path}'); "
            "Write-Output ($b.Width.ToString() + 'x' + $b.Height.ToString()); "
            "$g.Dispose(); $b.Dispose()"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=15
        )
        out = (result.stdout or "").strip().splitlines()
        dims = next((line for line in out if "x" in line), "")
        if "x" in dims:
            w_s, h_s = dims.split("x", 1)
            return int(w_s.strip()), int(h_s.strip())
        # Last-ditch: stat the file existence; size unknown.
        if out_path.exists():
            return 0, 0
        raise RuntimeError(f"PowerShell capture produced no file: {result.stderr[:200]}")
    except Exception as e:
        raise RuntimeError(f"Screenshot capture failed: {e}") from e


def _computer_screenshot_capture(describe: bool = False) -> dict:
    """Take a screenshot, persist it under PROJECT_DIR/screenshots/, and
    return a dict the HTTP layer can serialize. When `describe=True` and
    ANTHROPIC_API_KEY is set, also runs Claude vision and adds `description`.

    Returns: {path, url, width, height, [description]} or {error}.
    """
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out = _SCREENSHOTS_DIR / f"screen_{ts}.png"
        w, h = _capture_screen_to_path(out)
        # Trim old screenshots — keep last 50.
        try:
            shots = sorted(_SCREENSHOTS_DIR.glob("screen_*.png"))
            for old in shots[:-50]:
                try: old.unlink()
                except OSError: pass
        except Exception:
            pass
        result = {
            "path":   str(out),
            "url":    f"/file?path={urllib.parse.quote(str(out))}",
            "width":  w,
            "height": h,
        }
        if describe:
            try:
                import anthropic, base64
                key = os.environ.get("ANTHROPIC_API_KEY", "")
                if key:
                    client = anthropic.Anthropic(api_key=key)
                    img_b64 = base64.b64encode(out.read_bytes()).decode()
                    resp = client.messages.create(
                        model=MODEL, max_tokens=1024,
                        messages=[{"role": "user", "content": [
                            {"type": "image",
                             "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                            {"type": "text", "text":
                             "Describe this screenshot in detail for an agent that needs to "
                             "interact with it. List visible windows, applications, key text, "
                             "buttons, input fields, and notable UI elements. Be concrete about "
                             "what is on screen and where it is positioned."}
                        ]}]
                    )
                    result["description"] = resp.content[0].text.strip()
            except Exception as e:
                log.warning(f"[SCREENSHOT] vision description failed: {e}")
                result["description"] = f"(vision unavailable: {e})"
        return result
    except Exception as e:
        log.exception(f"[SCREENSHOT] capture failed: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────
# UI event queue — one-way channel from Data's tools to the dashboard.
# The frontend polls /ui_events?client_id=<id> every ~1.5s, drains pending
# events, and acts on them (e.g. "spawn these project windows").
#
# Per-client queues keyed by client_id. Previously a single shared deque was
# drained by whoever polled first, so with more than one dashboard tab/window
# open (or a stale background tab) the events were stolen by the wrong client
# and the visible window saw nothing. Now each client gets its own queue and
# _push_ui_event fans every event out to ALL registered clients.
#
# A short-lived backlog lets a client that registers a moment after an event
# fired (page still loading when a spawn marker hits) still catch it. Idle
# client queues (closed tabs) are garbage-collected so the dict can't grow
# without bound.
# ─────────────────────────────────────────────────────────────
_UI_QUEUE_MAX    = 64     # per-client pending-event cap
_UI_BACKLOG_MAX  = 64     # recent-event backlog cap (for late-registering clients)
_UI_BACKLOG_TTL  = 12.0   # seconds a brand-new client may "catch up" on
_UI_CLIENT_TTL   = 90.0   # drop client queues idle longer than this (closed tab)

_ui_clients     = {}                                       # client_id -> {"queue": deque, "last_seen": float}
_ui_backlog     = collections.deque(maxlen=_UI_BACKLOG_MAX)  # recent events for catch-up
_ui_events_lock = threading.Lock()

def _push_ui_event(event_type: str, payload: dict) -> None:
    evt = {"type": event_type, "payload": payload, "ts": time.time()}
    with _ui_events_lock:
        _ui_backlog.append(evt)
        for c in _ui_clients.values():
            c["queue"].append(evt)
        n = len(_ui_clients)
    log.info(f"[UI-EVENT] queued {event_type} → {n} client(s): {json.dumps(payload)[:200]}")

def _drain_ui_events(client_id: str) -> list:
    """Return and clear pending UI events for one dashboard client.

    A first-time client_id is registered on the fly and seeded with the recent
    backlog (events from the last _UI_BACKLOG_TTL seconds) so an event that
    fired moments before the page finished loading is not lost. Idle clients
    are GC'd on each poll."""
    now = time.time()
    with _ui_events_lock:
        client = _ui_clients.get(client_id)
        if client is None:
            q = collections.deque(maxlen=_UI_QUEUE_MAX)
            for evt in _ui_backlog:
                if now - evt["ts"] <= _UI_BACKLOG_TTL:
                    q.append(evt)
            client = {"queue": q, "last_seen": now}
            _ui_clients[client_id] = client
        client["last_seen"] = now
        events = list(client["queue"])
        client["queue"].clear()
        # GC clients that have stopped polling (closed/reloaded tabs).
        stale = [cid for cid, c in _ui_clients.items()
                 if now - c["last_seen"] > _UI_CLIENT_TTL]
        for cid in stale:
            _ui_clients.pop(cid, None)
    return events


# ═══════════════════════════════════════════════════════════
# STANDING ORDERS — recurring duty roster
# Cron entries that the bridge fires on schedule. Each order has a prompt
# that gets dispatched to a chosen provider as if the Captain had typed it.
# Results are pushed to the dashboard as a UI event so the relevant chat
# pane shows the reply.
# ═══════════════════════════════════════════════════════════

_orders_lock = threading.Lock()
_standing_orders: list = []      # list of {id, name, cron, prompt, provider, enabled, next_run, last_run, last_result}

def _parse_cron_field(field: str, lo: int, hi: int) -> set:
    """Parse one cron field. Supports *, */N, a-b, a,b,c, plain digits.
    Returns set of valid values for the field's range [lo, hi]."""
    out = set()
    for part in field.split(','):
        part = part.strip()
        if not part:
            continue
        step = 1
        if '/' in part:
            base, step_s = part.split('/', 1)
            step = int(step_s)
        else:
            base = part
        if base == '*' or base == '':
            start, end = lo, hi
        elif '-' in base:
            a, b = base.split('-', 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                out.add(v)
    return out

def _cron_matches(cron: str, dt: datetime.datetime) -> bool:
    """Check whether `dt` matches the 5-field cron expression."""
    parts = cron.split()
    if len(parts) != 5:
        return False
    mins  = _parse_cron_field(parts[0], 0, 59)
    hrs   = _parse_cron_field(parts[1], 0, 23)
    doms  = _parse_cron_field(parts[2], 1, 31)
    mons  = _parse_cron_field(parts[3], 1, 12)
    # Cron day-of-week: 0 or 7 = Sunday. Python's weekday(): Monday=0.
    dows_input = _parse_cron_field(parts[4], 0, 7)
    dows = set()
    for d in dows_input:
        # Map cron dow → python weekday
        if d == 0 or d == 7: dows.add(6)   # Sunday
        elif d == 1: dows.add(0)
        elif d == 2: dows.add(1)
        elif d == 3: dows.add(2)
        elif d == 4: dows.add(3)
        elif d == 5: dows.add(4)
        elif d == 6: dows.add(5)
    return (dt.minute in mins and dt.hour in hrs and dt.day in doms
            and dt.month in mons and dt.weekday() in dows)

def _next_cron_run(cron: str, after_ts: float) -> float:
    """Return UNIX timestamp of the next firing of `cron` strictly after after_ts.
    Returns 0 if no match found within 4 years (defensive cap)."""
    try:
        start = datetime.datetime.fromtimestamp(int(after_ts) + 60)
        # Round down to top of minute
        start = start.replace(second=0, microsecond=0)
        # Walk forward minute-by-minute, capped at 4 years (~2M minutes)
        for i in range(2_100_000):
            cand = start + datetime.timedelta(minutes=i)
            if _cron_matches(cron, cand):
                return cand.timestamp()
    except Exception as e:
        log.exception(f"[cron] next-run failed for {cron!r}: {e}")
    return 0.0

def _validate_cron(cron: str) -> str | None:
    """Return None if cron is valid, else an error message."""
    if not cron or len(cron.split()) != 5:
        return "cron must be 5 space-separated fields (min hr dom mon dow)"
    try:
        # Try parsing every field once
        for part, (lo, hi) in zip(cron.split(),
                                  [(0,59),(0,23),(1,31),(1,12),(0,7)]):
            _parse_cron_field(part, lo, hi)
    except Exception as e:
        return f"invalid cron field: {e}"
    return None

def _load_standing_orders() -> None:
    global _standing_orders
    if not STANDING_ORDERS_FILE.exists():
        _standing_orders = []
        return
    try:
        _standing_orders = json.loads(STANDING_ORDERS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.exception(f"[orders] failed to load: {e}")
        _standing_orders = []

def _save_standing_orders() -> None:
    try:
        STANDING_ORDERS_FILE.write_text(
            json.dumps(_standing_orders, indent=2), encoding="utf-8")
    except Exception as e:
        log.exception(f"[orders] failed to save: {e}")

def _recompute_next_run(order: dict) -> None:
    if order.get("enabled"):
        order["next_run"] = _next_cron_run(order["cron"], time.time())
    else:
        order["next_run"] = 0

def _fire_standing_order(order: dict) -> None:
    """Execute one standing order: dispatch its prompt to its provider and
    push the response to the dashboard as a UI event.

    Special actions bypass the LLM and call a hard-coded handler instead
    (e.g. action='refresh_upgrades' reruns the Potential Upgrades scan)."""
    action = (order.get("action") or "").strip()
    log.info(f"[orders] firing {order['id']} ({order['name']!r}) → "
             f"{'action=' + action if action else order['provider']}")
    if action == "refresh_upgrades":
        # Rerun the Potential Upgrades (AI tool discovery) scanner.
        try:
            import importlib
            import daily_briefing as _db
            importlib.reload(_db)
            bp = _db.generate_briefing()
            if "error" in bp:
                response = f"(Potential Upgrades refresh failed: {bp['error']})"
            else:
                response = f"Refreshed Potential Upgrades — {len(bp.get('items', []))} item(s) found."
        except Exception as e:
            log.exception(f"[orders] upgrades refresh failed: {e}")
            response = f"(Potential Upgrades refresh failed: {e})"
    else:
        try:
            response = _dispatch_with_provider(
                order["provider"],
                order["prompt"],
                project_path="",
            )
        except Exception as e:
            log.exception(f"[orders] dispatch failed: {e}")
            response = f"(Standing order failed: {e})"
    order["last_run"]    = time.time()
    order["last_result"] = response[:2000]
    _push_ui_event("standing_order_fired", {
        "id":       order["id"],
        "name":     order["name"],
        "provider": order["provider"],
        "prompt":   order["prompt"],
        "response": response,
    })
    # Optional Telegram push — fires only if the order opted in AND
    # TELEGRAM_BOT_TOKEN + a whitelist are configured.
    if order.get("notify_telegram"):
        try:
            import telegram_bot
            telegram_bot.notify(f"📋 {order['name']}\n\n{response}")
        except Exception as e:
            log.exception(f"[orders] telegram notify failed: {e}")

def _scheduler_loop() -> None:
    """Background thread: wakes every 30s, fires due orders, persists state."""
    log.info("[orders] scheduler started")
    while True:
        try:
            now = time.time()
            to_fire = []
            with _orders_lock:
                for o in _standing_orders:
                    if not o.get("enabled"):
                        continue
                    if o.get("next_run", 0) and o["next_run"] <= now:
                        to_fire.append(o)
            for o in to_fire:
                _fire_standing_order(o)
                with _orders_lock:
                    _recompute_next_run(o)
                    _save_standing_orders()
        except Exception as e:
            log.exception(f"[orders] scheduler tick failed: {e}")
        time.sleep(30)

def _handle_spawn_workspaces_marker(payload):
    workspaces = payload.get("workspaces") if isinstance(payload, dict) else None
    if isinstance(workspaces, list) and workspaces:
        _push_ui_event("spawn_workspaces", {"workspaces": workspaces})
        log.info(f"[marker] spawn_workspaces queued ({len(workspaces)} window(s))")
    else:
        log.warning(f"[marker] spawn_workspaces JSON missing 'workspaces' list")

def _handle_set_project_path_marker(payload):
    """LLM emitted a <<set_project_path>> marker — re-root the chat pane the
    request came from (NOT necessarily the main one) to a new folder without
    spawning a separate workspace window. Mirrors POST /project."""
    global _project_path, _project_nodes, _project_text
    new_path = ""
    if isinstance(payload, dict):
        new_path = (payload.get("path") or "").strip()
    if not new_path:
        log.warning("[marker] set_project_path missing 'path'")
        return
    p = Path(new_path)
    if not p.is_dir():
        log.warning(f"[marker] set_project_path not a directory: {new_path!r}")
        return
    # Originating pane key (set by _bind_history on this request thread) — the
    # lowercased project_path the request came from. The frontend matches it
    # to a workspace so the re-root lands on the pane the Captain typed in,
    # not always the main one. Empty for non-request callers (standing orders,
    # background tasks) — those fall back to "main pane" on the frontend.
    src_pane_key = getattr(_history_state, "key", "") or ""
    new_nodes, new_text = _scan_project(str(p))

    # Only mutate the global _project_path when the originating pane IS the
    # global one (main pane). Re-rooting a secondary pane must not clobber
    # main's state — main keeps its own cwd / nodes / context. The secondary
    # pane carries its new path on every subsequent /chat request, so the
    # bridge picks it up via _bind_history without needing the global.
    if src_pane_key == _history_key(_project_path):
        _project_path  = str(p)
        _project_nodes = new_nodes
        _project_text  = new_text
        log.info(f"[marker] set_project_path → {_project_path} "
                 f"({len(new_nodes)} entries) [main pane]")
    else:
        log.info(f"[marker] set_project_path → {p} "
                 f"({len(new_nodes)} entries) [pane={src_pane_key!r}]")

    _push_ui_event("project_rooted", {
        "path":  str(p),
        "name":  p.name or str(p),
        "nodes": new_nodes,
        # Frontend matches this against each workspace's lowercased pre-root
        # path to target the right window. Empty → frontend falls back to main.
        "pane":  src_pane_key,
    })

    # Hard backstop: re-rooting is a one-shot task. The soul tells the model
    # to STOP after the marker, but Claude CLI in particular tends to ignore
    # that and start exploring the new directory (ls, README, git status...).
    # Schedule a kill of the active CLI subprocess 2.5s after the marker —
    # enough time for the model to stream its one-line confirmation, but
    # short enough to cut off any follow-up tool-call loop.
    # Capture the current thread's project key NOW — the kill thread spawned
    # below won't inherit thread-local context from this runner thread.
    proc_key = src_pane_key

    def _kill_cli_after_grace():
        time.sleep(2.5)
        with _active_cli_procs_lock:
            proc = _active_cli_procs.get(proc_key)
        if proc is None or proc.poll() is not None:
            return  # already finished naturally
        try:
            # Tag the proc so the post-loop knows this was a deliberate end-of-
            # turn kill, not an abort or crash — that suppresses the "Aborted,
            # Captain" / "I was unable to generate a response" fallbacks, since
            # the frontend already shows the confirmation via project_rooted.
            setattr(proc, "_backstop_killed", True)
            _kill_proc_tree(proc)
            log.info(f"[marker] set_project_path backstop: tree-killed CLI subprocess for key={proc_key!r}")
        except Exception as e:
            log.warning(f"[marker] set_project_path backstop kill failed: {e}")

    threading.Thread(target=_kill_cli_after_grace, daemon=True).start()

def _handle_create_standing_order_marker(payload):
    """LLM emitted a <<create_standing_order>> marker — validate and add."""
    if not isinstance(payload, dict):
        log.warning("[marker] create_standing_order payload not a dict")
        return
    err = _validate_cron(payload.get("cron", ""))
    if err:
        log.warning(f"[marker] create_standing_order cron invalid: {err}")
        return
    if not payload.get("name") or not payload.get("prompt") or not payload.get("provider"):
        log.warning("[marker] create_standing_order missing name/prompt/provider")
        return
    if payload["provider"] not in PROVIDERS:
        log.warning(f"[marker] create_standing_order unknown provider {payload['provider']}")
        return
    new_id = f"so-{int(time.time()*1000)}"
    order = {
        "id":       new_id,
        "name":     payload["name"][:80],
        "cron":     payload["cron"],
        "prompt":   payload["prompt"],
        "provider": payload["provider"],
        "enabled":  bool(payload.get("enabled", True)),
        "notify_telegram": bool(payload.get("notify_telegram", False)),
        "next_run": 0,
        "last_run": 0,
        "last_result": "",
    }
    _recompute_next_run(order)
    with _orders_lock:
        _standing_orders.append(order)
        _save_standing_orders()
    log.info(f"[marker] create_standing_order added {new_id}: {order['name']!r} ({order['cron']})")
    _push_ui_event("standing_order_created", {"id": new_id, "name": order["name"]})


def _handle_ask_options_marker(payload):
    """LLM emitted an <<ask_options>> marker — surface a clickable question in
    the chat pane the request came from. The Captain clicks an option (or types
    his own) and that becomes the next message. Lets the assistant branch on a
    decision mid-conversation instead of guessing."""
    if not isinstance(payload, dict):
        log.warning("[marker] ask_options payload not a dict")
        return
    question = (payload.get("question") or "").strip()
    raw_opts = payload.get("options")
    if not question or not isinstance(raw_opts, list) or not raw_opts:
        log.warning("[marker] ask_options missing question/options")
        return
    # Normalize: strings only, trimmed, deduped, capped (length + count) so the
    # UI stays tidy and a runaway model can't flood the pane with buttons.
    options = []
    for o in raw_opts:
        s = str(o).strip()
        if s and s not in options:
            options.append(s[:120])
        if len(options) >= 6:
            break
    if not options:
        log.warning("[marker] ask_options had no usable options")
        return
    # Originating pane key (set by _bind_history on this request thread) so the
    # option card lands in the window the Captain is talking in — mirrors the
    # set_project_path marker. Empty for background callers → frontend uses main.
    src_pane_key = getattr(_history_state, "key", "") or ""
    _push_ui_event("ask_options", {
        "question": question[:400],
        "options":  options,
        "pane":     src_pane_key,
    })
    log.info(f"[marker] ask_options queued ({len(options)} option(s)) pane={src_pane_key!r}")


# Map marker name → handler. Used by _marker_filter_sse to intercept tool calls
# emitted as text markers by CLI providers that don't see our structured TOOLS list.
_MARKER_HANDLERS = {
    "spawn_workspaces":      _handle_spawn_workspaces_marker,
    "create_standing_order": _handle_create_standing_order_marker,
    "set_project_path":      _handle_set_project_path_marker,
    "ask_options":           _handle_ask_options_marker,
}
# Markers that "end the turn" — once they fire, any further LLM tokens are
# irrelevant noise (the model has done what was asked). The filter suppresses
# all token output after one of these dispatches so the Captain doesn't see
# runaway exploration ("let me also check…") after a one-shot operation.
_TERMINAL_MARKERS = {"set_project_path", "ask_options"}
_MAX_MARKER_TAG_LEN = max(len(f"<<{n}>>") for n in _MARKER_HANDLERS)

def _marker_filter_sse(downstream):
    """Wrap a send_sse callback to intercept tool-marker blocks in the LLM's
    text output (`<<name>>JSON<</name>>`), dispatch them to handlers, and
    strip them from the user-visible tokens. Used by CLI runners so tools
    work even though those CLIs do not see our structured TOOLS list."""
    buf = [""]
    # Set to True once a terminal marker fires — all subsequent token events
    # are dropped on the floor so the runaway model output (post-marker
    # exploration, "let me also check…", etc.) never reaches the Captain.
    suppressed = [False]

    def _flush_safe_prefix(text):
        # Hold trailing '<' chars that might be the start of an opening marker
        idx = text.rfind('<')
        if idx >= 0 and idx >= len(text) - _MAX_MARKER_TAG_LEN:
            return text[:idx], text[idx:]
        return text, ""

    def _find_earliest_marker():
        """Return (name, open_idx, close_idx_or_None) for the *earliest* opening
        marker in the buffer; None if no opening tag found at all."""
        best = None
        for name in _MARKER_HANDLERS:
            open_tag  = f"<<{name}>>"
            close_tag = f"<</{name}>>"
            open_idx = buf[0].find(open_tag)
            if open_idx < 0:
                continue
            close_idx = buf[0].find(close_tag, open_idx + len(open_tag))
            cand = (name, open_idx, close_idx if close_idx >= 0 else None)
            if best is None or open_idx < best[1]:
                best = cand
        return best

    def filtered(event_type, text):
        # Once a terminal marker has fired, silence "thinking" chatter — but
        # *not* token events, because we still need to scan token text for
        # additional markers (a single turn can emit set_project_path AND
        # spawn_workspaces together). Plain prose tokens get dropped below.
        if suppressed[0] and event_type == 'thinking':
            return
        if event_type != 'token':
            downstream(event_type, text)
            return
        buf[0] += text
        while True:
            m = _find_earliest_marker()
            if not m:
                emit, hold = _flush_safe_prefix(buf[0])
                # Drop prose tokens after a terminal marker, but keep `hold`
                # in the buffer in case a later marker tag starts inside it.
                if emit and not suppressed[0]: downstream('token', emit)
                buf[0] = hold
                return
            name, open_idx, close_idx = m
            if close_idx is None:
                # Have opening, waiting for closing
                before = buf[0][:open_idx]
                if before and not suppressed[0]: downstream('token', before)
                buf[0] = buf[0][open_idx:]
                return
            # Complete pair — extract and dispatch
            open_tag  = f"<<{name}>>"
            close_tag = f"<</{name}>>"
            before    = buf[0][:open_idx]
            json_text = buf[0][open_idx + len(open_tag):close_idx].strip()
            after     = buf[0][close_idx + len(close_tag):]
            if before and not suppressed[0]: downstream('token', before)
            try:
                payload = json.loads(json_text)
                handler = _MARKER_HANDLERS.get(name)
                if handler: handler(payload)
            except Exception as e:
                log.exception(f"[marker] {name} parse failed: {e}")
            buf[0] = after
            # Terminal marker → suppress further *text* tokens (no runaway
            # "let me also check…"), but keep parsing so any follow-up
            # markers in the same stream still dispatch.
            if name in _TERMINAL_MARKERS and not suppressed[0]:
                suppressed[0] = True
                log.info(f"[marker] {name} is terminal — suppressing further prose (markers still dispatch)")
            # loop: another marker may immediately follow

    def finalize():
        if suppressed[0]:
            buf[0] = ""
            return
        if buf[0]:
            downstream('token', buf[0])
            buf[0] = ""

    filtered.finalize = finalize
    return filtered


def tool_spawn_workspaces(workspaces: list) -> str:
    """Spawn one or more project chat windows in the dashboard, each pinned to
    a specific provider and given an initial role briefing."""
    if not isinstance(workspaces, list) or not workspaces:
        return "spawn_workspaces requires a non-empty 'workspaces' list."

    valid_providers = set(PROVIDERS.keys())
    cleaned = []
    for w in workspaces:
        if not isinstance(w, dict):
            continue
        wpath     = (w.get("path") or "").strip()
        provider  = (w.get("provider") or "").strip()
        role      = (w.get("role") or "").strip()
        if not wpath or not provider or not role:
            return "Each workspace entry needs path, provider, and role."
        if provider not in valid_providers:
            return (f"Unknown provider '{provider}'. Valid: "
                    f"{sorted(valid_providers)}")
        cleaned.append({"path": wpath, "provider": provider, "role": role})

    if not cleaned:
        return "No valid workspaces to spawn."

    _push_ui_event("spawn_workspaces", {"workspaces": cleaned})
    summary = ", ".join(f"{w['provider']} → {w['role']}" for w in cleaned)
    return f"Queued {len(cleaned)} window(s): {summary}"


def tool_create_standing_order(inputs: dict) -> str:
    """Create a recurring scheduled task. Same fields as the dashboard's
    Standing Orders dialog (name, cron, prompt, provider, enabled)."""
    if not isinstance(inputs, dict):
        return "create_standing_order requires a dict of fields."
    err = _validate_cron(inputs.get("cron", ""))
    if err:
        return f"Cron expression invalid: {err}"
    if not inputs.get("name") or not inputs.get("prompt") or not inputs.get("provider"):
        return "name, prompt, and provider are required."
    if inputs["provider"] not in PROVIDERS:
        return f"Unknown provider '{inputs['provider']}'. Valid: {sorted(PROVIDERS.keys())}"
    new_id = f"so-{int(time.time()*1000)}"
    order = {
        "id":       new_id,
        "name":     str(inputs["name"])[:80],
        "cron":     inputs["cron"],
        "prompt":   inputs["prompt"],
        "provider": inputs["provider"],
        "enabled":  bool(inputs.get("enabled", True)),
        "notify_telegram": bool(inputs.get("notify_telegram", False)),
        "next_run": 0,
        "last_run": 0,
        "last_result": "",
    }
    _recompute_next_run(order)
    with _orders_lock:
        _standing_orders.append(order)
        _save_standing_orders()
    _push_ui_event("standing_order_created", {"id": new_id, "name": order["name"]})
    next_str = (datetime.datetime.fromtimestamp(order["next_run"]).strftime("%Y-%m-%d %H:%M")
                if order["next_run"] else "(disabled)")
    return f"Standing order '{order['name']}' added. Next firing: {next_str}"


def tool_load_skill(skill_name: str) -> str:
    """Find and return the contents of a skill markdown file."""
    # Search Hermes skills (skill.md, lowercase, organized by category)
    if SKILLS_DIR.exists():
        for category in SKILLS_DIR.iterdir():
            if not category.is_dir():
                continue
            skill_path = category / skill_name / "skill.md"
            if skill_path.exists():
                content = skill_path.read_text(encoding="utf-8", errors="ignore")
                return content[:12000] + ("…[truncated]" if len(content) > 12000 else "")
    # Search Claude Code native skills (SKILL.md, uppercase, flat structure)
    if CLAUDE_SKILLS_DIR.exists():
        skill_path = CLAUDE_SKILLS_DIR / skill_name / "SKILL.md"
        if skill_path.exists():
            content = skill_path.read_text(encoding="utf-8", errors="ignore")
            return content[:12000] + ("…[truncated]" if len(content) > 12000 else "")
    return f"Skill '{skill_name}' not found. Available categories: {', '.join(d.name for d in SKILLS_DIR.iterdir() if d.is_dir())}"


def tool_remember(note: str, category: str = "General") -> str:
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n### [{category}] — {timestamp}\n{note}\n"
        with open(COMPUTER_MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
        log.info(f"[MEMORY] Saved note: {note[:80]}")
        return f"Memory saved under '{category}'."
    except Exception as e:
        return f"Memory write error: {e}"


def tool_recall_memory() -> str:
    try:
        if COMPUTER_MEMORY_FILE.exists():
            content = COMPUTER_MEMORY_FILE.read_text(encoding="utf-8", errors="replace").strip()
            return content if content else "No memories saved yet."
        return "No memories saved yet."
    except Exception as e:
        return f"Memory read error: {e}"


# ══════════════════════════════════════════════════════════════════
# RECALL INDEX — semantic + keyword search across every Data artifact
# ══════════════════════════════════════════════════════════════════
# Unified index covering conversations, COMPUTER_MEMORY entries, daily briefings,
# and standing orders. Each row has both an FTS5 row (keyword) and a vector
# embedding (semantic, via Ollama). Searches run both and merge with Reciprocal
# Rank Fusion (RRF) so the result is the best of both worlds.
#
# - Index file: recall_index.db (SQLite)
# - Embedding model: nomic-embed-text via Ollama (768 dims, ~50ms/embed local)
# - Falls back to keyword-only if Ollama or the embed model is unavailable
# - Rebuilt lazily when any indexed source's mtime moves; embeddings only
#   recomputed for new or changed rows (content_hash diff)

import struct as _struct
import hashlib as _hashlib

RECALL_INDEX_DB    = PROJECT_DIR / "recall_index.db"
EMBED_MODEL        = "nomic-embed-text"
EMBED_DIMS         = 768
EMBED_API          = "http://localhost:11434/api/embeddings"
_recall_lock       = threading.Lock()
_embed_unavailable_logged = False   # noisy-log guard


def _embed_text(text: str) -> bytes | None:
    """Embed via Ollama. Returns packed float32 bytes, or None on failure
    (caller should log+continue — embedding is an enhancement, not required)."""
    global _embed_unavailable_logged
    if not text or not text.strip():
        return None
    try:
        body = json.dumps({"model": EMBED_MODEL, "prompt": text[:8000]}).encode()
        req  = urllib.request.Request(EMBED_API, data=body,
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8"))
        vec = data.get("embedding") or []
        if len(vec) != EMBED_DIMS:
            return None
        return _struct.pack(f"{EMBED_DIMS}f", *vec)
    except Exception as e:
        if not _embed_unavailable_logged:
            log.warning(
                f"[RECALL] embeddings unavailable ({e}); falling back to "
                f"keyword-only. Run `ollama pull {EMBED_MODEL}` to enable "
                f"semantic search."
            )
            _embed_unavailable_logged = True
        return None


def _unpack_embedding(blob: bytes) -> list[float] | None:
    if not blob or len(blob) != EMBED_DIMS * 4:
        return None
    return list(_struct.unpack(f"{EMBED_DIMS}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _hash_content(s: str) -> str:
    return _hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()


def _iter_index_sources():
    """Yield (source, pane, role, ts, ref, content) tuples for every item
    that should live in the recall index. Sources: conversation, memory,
    briefing, order.

    Conversation sourcing is dual-source: the unbounded archive file is
    read first (so EVERY turn ever heard becomes searchable), then the
    live rolling history fills in any turns that haven't been archived
    yet (defensive — usually there's none). Dedupe by content_hash so a
    turn living in both places only gets indexed once."""

    seen_hashes: set = set()

    # 1a. Permanent conversation archive — one item per archived turn
    if CONVERSATION_ARCHIVE_FILE.exists():
        try:
            with open(CONVERSATION_ARCHIVE_FILE, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    content = rec.get("content", "")
                    if not isinstance(content, str) or not content.strip():
                        continue
                    h = _hash_content(content)
                    if h in seen_hashes:
                        continue
                    seen_hashes.add(h)
                    yield (
                        "conversation",
                        rec.get("pane") or "(archive)",
                        rec.get("role") or "?",
                        rec.get("ts", ""),
                        f"archive:{i}",
                        content,
                    )
        except Exception as e:
            log.warning(f"[RECALL] archive read failed: {e}")

    # 1b. Live rolling history — covers any turn not yet archived (e.g.,
    # carried over from before the archive existed). Deduped against 1a.
    for pane_key, msgs in _histories_by_path.items():
        for i, m in enumerate(msgs):
            content = m.get("content", "")
            if isinstance(content, list):
                parts = []
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "text":         parts.append(b.get("text", ""))
                        elif b.get("type") == "tool_use":   parts.append(f"[tool_use {b.get('name','')}: {json.dumps(b.get('input',{}))[:300]}]")
                        elif b.get("type") == "tool_result":parts.append(f"[tool_result: {str(b.get('content',''))[:300]}]")
                content = "\n".join(parts)
            if not isinstance(content, str):
                content = str(content)
            if not content.strip():
                continue
            h = _hash_content(content)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            yield (
                "conversation",
                pane_key or "(main)",
                m.get("role", "?"),
                m.get("ts", ""),
                f"conv:{pane_key or '(main)'}:{i}",
                content,
            )

    # 2. COMPUTER_MEMORY — split by '###' headings so each entry is its own item
    if COMPUTER_MEMORY_FILE.exists():
        text = COMPUTER_MEMORY_FILE.read_text(encoding="utf-8", errors="replace")
        chunks = re.split(r"(?=^### )", text, flags=re.MULTILINE)
        for idx, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            # Pull the heading for a friendly ref label
            head_match = re.match(r"^###\s+(.+)", chunk)
            label = head_match.group(1)[:80] if head_match else f"chunk-{idx}"
            yield ("memory", None, None, "", f"memory:{label}", chunk)

    # 3. Daily briefings — each item is one news/update
    briefing_file = PROJECT_DIR / "daily_briefing.json"
    if briefing_file.exists():
        try:
            data = json.loads(briefing_file.read_text(encoding="utf-8", errors="replace"))
            items = data.get("items", []) if isinstance(data, dict) else []
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                blob = json.dumps(it, ensure_ascii=False)
                title = (it.get("title") or it.get("name") or f"item-{i}")[:80]
                yield ("briefing", None, None, data.get("generated_at", ""),
                       f"briefing:{title}", blob)
        except Exception:
            pass

    # 4. Standing orders
    orders_file = PROJECT_DIR / "standing_orders.json"
    if orders_file.exists():
        try:
            data = json.loads(orders_file.read_text(encoding="utf-8", errors="replace"))
            orders = data if isinstance(data, list) else data.get("orders", [])
            for i, o in enumerate(orders or []):
                if not isinstance(o, dict):
                    continue
                blob = json.dumps(o, ensure_ascii=False)
                name = (o.get("name") or f"order-{i}")[:80]
                yield ("order", None, None, "", f"order:{name}", blob)
        except Exception:
            pass


def _index_inputs_mtime() -> float:
    """Latest mtime across all sources — drives the lazy rebuild check."""
    paths = [
        HISTORY_FILE,
        CONVERSATION_ARCHIVE_FILE,   # the permanent record — moves on every turn
        COMPUTER_MEMORY_FILE,
        PROJECT_DIR / "daily_briefing.json",
        PROJECT_DIR / "standing_orders.json",
    ]
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)


def _build_recall_index() -> tuple[int, int, str]:
    """Rebuild the recall index. Reuses existing embeddings when content_hash
    is unchanged so the wire cost of re-embedding stays minimal across rebuilds.
    Returns (total_rows, embedded_rows, error_str)."""
    import sqlite3
    try:
        # Load existing embeddings keyed by content_hash so unchanged content
        # doesn't have to round-trip Ollama again.
        existing: dict = {}
        if RECALL_INDEX_DB.exists():
            try:
                con0 = sqlite3.connect(str(RECALL_INDEX_DB))
                for h, blob in con0.execute("SELECT content_hash, embedding FROM items WHERE embedding IS NOT NULL"):
                    existing[h] = blob
                con0.close()
            except Exception:
                pass

        tmp = RECALL_INDEX_DB.with_suffix(".db.tmp")
        if tmp.exists(): tmp.unlink()
        con = sqlite3.connect(str(tmp))
        con.executescript("""
            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                pane TEXT,
                role TEXT,
                ts TEXT,
                ref TEXT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding BLOB
            );
            CREATE INDEX idx_source ON items(source);
            CREATE INDEX idx_pane   ON items(pane);
            CREATE VIRTUAL TABLE items_fts USING fts5(
                content,
                content='items',
                content_rowid='id',
                tokenize='porter unicode61'
            );
            CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
                INSERT INTO items_fts(rowid, content) VALUES (new.id, new.content);
            END;
        """)

        total, embedded = 0, 0
        for source, pane, role, ts, ref, content in _iter_index_sources():
            h = _hash_content(content)
            blob = existing.get(h)
            if blob is None:
                blob = _embed_text(content)
            if blob is not None:
                embedded += 1
            con.execute(
                "INSERT INTO items(source, pane, role, ts, ref, content, content_hash, embedding) VALUES (?,?,?,?,?,?,?,?)",
                (source, pane, role, ts, ref, content, h, blob),
            )
            total += 1

        con.commit()
        con.close()
        if RECALL_INDEX_DB.exists(): RECALL_INDEX_DB.unlink()
        tmp.rename(RECALL_INDEX_DB)
        return total, embedded, ""
    except Exception as e:
        log.exception(f"[RECALL] build failed: {e}")
        return 0, 0, str(e)


def _ensure_recall_index_fresh() -> None:
    """Lazy rebuild when any indexed source's mtime moves."""
    try:
        latest = _index_inputs_mtime()
        if latest == 0.0:
            return
        db_mtime = RECALL_INDEX_DB.stat().st_mtime if RECALL_INDEX_DB.exists() else 0
        if latest <= db_mtime:
            return
        with _recall_lock:
            db_mtime = RECALL_INDEX_DB.stat().st_mtime if RECALL_INDEX_DB.exists() else 0
            if latest <= db_mtime: return
            total, embedded, err = _build_recall_index()
            log.info(
                f"[RECALL] rebuilt — {total} items indexed "
                f"({embedded} with embeddings)" + (f" (err={err})" if err else "")
            )
    except Exception as e:
        log.exception(f"[RECALL] freshness check failed: {e}")


def _fts5_quote(q: str) -> str:
    return q.replace('"', '""')


def _recall_search(query: str, k: int, scope: str, sources: list[str] | None = None) -> list[dict]:
    """Run keyword (FTS5) + semantic (cosine) searches and merge with RRF.
    Returns list of {source, pane, role, ts, ref, snippet, score} dicts."""
    import sqlite3
    _ensure_recall_index_fresh()
    if not RECALL_INDEX_DB.exists():
        return []

    # Scope filter (conversation pane). Non-conversation sources are unaffected
    # by scope='current' because they don't have a pane.
    pane_filter, pane_params = "", []
    if scope == "current":
        pane_key = getattr(_history_state, "key", "") or ""
        pane_filter  = "AND (source != 'conversation' OR pane = ?)"
        pane_params  = [pane_key or "(main)"]
    elif scope != "all":
        pane_filter  = "AND (source != 'conversation' OR pane = ?)"
        pane_params  = [_history_key(scope) or "(main)"]

    src_filter, src_params = "", []
    if sources:
        placeholders = ",".join("?" * len(sources))
        src_filter   = f"AND source IN ({placeholders})"
        src_params   = list(sources)

    con = sqlite3.connect(str(RECALL_INDEX_DB))
    con.row_factory = sqlite3.Row

    pool = max(20, k * 4)   # over-fetch then RRF-rerank

    # ── 1. Keyword search via FTS5 ─────────────────────────
    keyword_ranks: dict[int, int] = {}
    try:
        fts_sql = (
            "SELECT items.id AS id, "
            "snippet(items_fts, 0, '[[', ']]', '…', 18) AS snip "
            "FROM items_fts JOIN items ON items.id = items_fts.rowid "
            f"WHERE items_fts MATCH ? {pane_filter} {src_filter} "
            "ORDER BY bm25(items_fts) "
            "LIMIT ?"
        )
        # Try the literal phrase first
        try:
            rows = con.execute(fts_sql, [f'"{_fts5_quote(query)}"', *pane_params, *src_params, pool]).fetchall()
        except sqlite3.OperationalError:
            rows = []
        # Fallback: OR-of-words
        if not rows:
            words = [w for w in query.split() if w.strip()]
            if words:
                or_q = " OR ".join(f'"{_fts5_quote(w)}"' for w in words)
                try:
                    rows = con.execute(fts_sql, [or_q, *pane_params, *src_params, pool]).fetchall()
                except sqlite3.OperationalError:
                    rows = []
        snippets_by_id: dict[int, str] = {}
        for rank, r in enumerate(rows):
            keyword_ranks[r["id"]] = rank
            snippets_by_id[r["id"]] = r["snip"]
    except Exception as e:
        log.warning(f"[RECALL] keyword leg failed: {e}")
        snippets_by_id = {}

    # ── 2. Semantic search via cosine over stored embeddings ─
    semantic_ranks: dict[int, int] = {}
    query_emb_blob = _embed_text(query)
    if query_emb_blob:
        qvec = _unpack_embedding(query_emb_blob)
        try:
            emb_sql = (
                "SELECT id, embedding FROM items "
                f"WHERE embedding IS NOT NULL {pane_filter} {src_filter}"
            )
            sims: list[tuple[int, float]] = []
            for row in con.execute(emb_sql, [*pane_params, *src_params]):
                v = _unpack_embedding(row["embedding"])
                if v is None: continue
                sims.append((row["id"], _cosine(qvec, v)))
            sims.sort(key=lambda t: t[1], reverse=True)
            for rank, (rid, _s) in enumerate(sims[:pool]):
                semantic_ranks[rid] = rank
        except Exception as e:
            log.warning(f"[RECALL] semantic leg failed: {e}")

    # ── 3. Reciprocal Rank Fusion ─────────────────────────
    RRF_K = 60.0
    fused: dict[int, float] = {}
    for rid, r in keyword_ranks.items():
        fused[rid] = fused.get(rid, 0) + 1.0 / (RRF_K + r)
    for rid, r in semantic_ranks.items():
        fused[rid] = fused.get(rid, 0) + 1.0 / (RRF_K + r)

    if not fused:
        con.close()
        return []

    top_ids = sorted(fused, key=fused.get, reverse=True)[:k]

    # ── 4. Hydrate top results ─────────────────────────────
    hydrate_sql = (
        f"SELECT id, source, pane, role, ts, ref, content "
        f"FROM items WHERE id IN ({','.join('?' * len(top_ids))})"
    )
    rows = con.execute(hydrate_sql, top_ids).fetchall()
    con.close()

    by_id = {r["id"]: r for r in rows}
    out = []
    for rid in top_ids:
        r = by_id.get(rid)
        if not r:
            continue
        snip = snippets_by_id.get(rid) or (r["content"][:240] + ("…" if len(r["content"]) > 240 else ""))
        out.append({
            "source":  r["source"],
            "pane":    r["pane"],
            "role":    r["role"],
            "ts":      r["ts"],
            "ref":     r["ref"],
            "snippet": snip,
            "score":   round(fused[rid], 4),
        })
    return out


def tool_youtube_upload_video(inp: dict) -> str:
    """Upload a local video file to YouTube. Defensive defaults: private upload
    unless the Captain says otherwise. Returns a markdown link to the new video."""
    try:
        import gyoutube
        if not gyoutube.available():
            return ("YouTube not configured yet, Captain. Run "
                    "`python dashboard\\gyoutube.py <account-name>` to authorize.")
        result = gyoutube.upload_video(
            path=inp["path"],
            title=inp["title"],
            description=inp.get("description", ""),
            tags=inp.get("tags") or None,
            privacy=inp.get("privacy", "private"),
            category=inp.get("category", "22"),
            made_for_kids=bool(inp.get("made_for_kids", False)),
            account=inp.get("account"),
        )
        return (
            f"✓ Uploaded to **{result['account']}** channel: "
            f"[{result['title']}]({result['url']})\n"
            f"Visibility: **{result['privacy']}** · Video ID: `{result['video_id']}`"
        )
    except FileNotFoundError as e:
        return f"Upload failed — file not found: {e}"
    except ValueError as e:
        # Most commonly: ambiguous account when multiple are configured.
        return f"Upload failed — {e}"
    except Exception as e:
        log.exception(f"[tool_youtube_upload_video] {e}")
        return f"Upload failed: {e}"


def tool_youtube_update_video(inp: dict) -> str:
    """Update an existing video's metadata. Skips any field not provided."""
    try:
        import gyoutube
        if not gyoutube.available():
            return "YouTube not configured. Run setup first."
        result = gyoutube.update_video(
            video_id=inp["video_id"],
            title=inp.get("title"),
            description=inp.get("description"),
            tags=inp.get("tags"),
            privacy=inp.get("privacy"),
            category=inp.get("category"),
            account=inp.get("account"),
        )
        return (
            f"✓ Updated [{result['title']}]({result['url']}) on "
            f"**{result['account']}** — visibility: **{result['privacy']}**"
        )
    except ValueError as e:
        return f"Update failed — {e}"
    except Exception as e:
        log.exception(f"[tool_youtube_update_video] {e}")
        return f"Update failed: {e}"


def tool_youtube_set_thumbnail(inp: dict) -> str:
    """Replace a video's thumbnail with a local image."""
    try:
        import gyoutube
        if not gyoutube.available():
            return "YouTube not configured. Run setup first."
        result = gyoutube.set_thumbnail(
            video_id=inp["video_id"],
            image_path=inp["image_path"],
            account=inp.get("account"),
        )
        return (
            f"✓ Thumbnail updated on **{result['account']}** for video "
            f"`{result['video_id']}`."
        )
    except FileNotFoundError as e:
        return f"Thumbnail set failed — image not found: {e}"
    except ValueError as e:
        return f"Thumbnail set failed — {e}"
    except Exception as e:
        log.exception(f"[tool_youtube_set_thumbnail] {e}")
        return f"Thumbnail set failed: {e}"


def tool_pinterest_list_boards(inp: dict) -> str:
    """List the Captain's Pinterest boards."""
    try:
        import gpinterest
        if not gpinterest.available():
            return ("Pinterest not configured yet, Captain. Run "
                    "`python dashboard\\gpinterest.py` to authorize.")
        boards = gpinterest.list_boards(account=inp.get("account"))
        if not boards:
            return "No boards found on this Pinterest account."
        lines = [f"**{len(boards)} boards found:**\n"]
        for b in boards:
            privacy = f" ({b['privacy']})" if b.get("privacy") and b["privacy"] != "PUBLIC" else ""
            lines.append(f"- **{b['name']}** — `{b['id']}` · {b.get('pin_count', '?')} pins{privacy}")
        return "\n".join(lines)
    except ValueError as e:
        return f"Board listing failed — {e}"
    except Exception as e:
        log.exception(f"[tool_pinterest_list_boards] {e}")
        return f"Board listing failed: {e}"


def tool_pinterest_create_pin(inp: dict) -> str:
    """Create/save a pin to one of the Captain's boards."""
    try:
        import gpinterest
        if not gpinterest.available():
            return ("Pinterest not configured yet, Captain. Run "
                    "`python dashboard\\gpinterest.py` to authorize.")
        result = gpinterest.create_pin(
            board_id=inp["board_id"],
            image_url=inp["image_url"],
            title=inp.get("title"),
            description=inp.get("description"),
            link=inp.get("link"),
            account=inp.get("account"),
        )
        return (
            f"✓ Pin created: [{result.get('title') or 'Untitled'}]"
            f"({result.get('url', 'https://pinterest.com')})\n"
            f"Board: `{inp['board_id']}` · Pin ID: `{result.get('id', '?')}`"
        )
    except ValueError as e:
        return f"Pin creation failed — {e}"
    except Exception as e:
        log.exception(f"[tool_pinterest_create_pin] {e}")
        return f"Pin creation failed: {e}"


def tool_search_history(query: str, k: int = 5, scope: str = "current",
                        sources: str | list | None = None) -> str:
    """Search every past turn, memory entry, briefing item, and standing order.

    Args:
        query:   words/phrase to search for. Plain text works fine.
        k:       max matches to return (default 5, max 20).
        scope:   'current' (active pane only for conversations) (default),
                 'all' (every pane), or the literal project path of a specific pane.
                 Non-conversation sources are always included regardless of scope.
        sources: optional filter — list or comma-separated string of source types
                 to include. Valid: 'conversation', 'memory', 'briefing', 'order'.
                 Default: all sources.

    Returns: formatted match list with source-tagged snippets.
    """
    if not query or not query.strip():
        return "search_history error: empty query"
    k = max(1, min(20, int(k or 5)))
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.split(",") if s.strip()]

    hits = _recall_search(query, k, scope, sources)
    if not hits:
        scope_label = scope if scope in ("current", "all") else f"pane '{scope}'"
        return f"No matches in {scope_label} for: {query}"

    lines = [f"Found {len(hits)} match(es) for: {query}", ""]
    for h in hits:
        if h["source"] == "conversation":
            pane_label = h["pane"] if h["pane"] != "(main)" else "main"
            ts_part = f" {h['ts']}" if h['ts'] else ""
            header  = f"[conv · {pane_label} · {h['role']}{ts_part}]"
        else:
            header  = f"[{h['source']} · {h['ref']}]"
        lines.append(header)
        lines.append(h["snippet"])
        lines.append("")
    return "\n".join(lines).rstrip()


def auto_recall_hint(user_message: str, scope: str = "current",
                     min_chars: int = 30, min_score: float = 0.025) -> str:
    """Quietly check whether anything in the recall index is strongly relevant
    to this incoming user message. Returns either an empty string (silent) or a
    one-line `[recall] ...` hint suitable to splice into the system prompt.

    Threshold tuned conservatively — the hint stays silent unless the top hit
    is well above the noise floor (RRF score over ~0.025 with both legs voting)."""
    if not user_message or len(user_message.strip()) < min_chars:
        return ""
    try:
        hits = _recall_search(user_message, k=1, scope=scope)
    except Exception:
        return ""
    if not hits:
        return ""
    top = hits[0]
    if top["score"] < min_score:
        return ""
    snip = top["snippet"].replace("\n", " ").strip()
    if len(snip) > 200:
        snip = snip[:197] + "…"
    src_tag = top["source"]
    where = (
        f"{top['pane']} turn ref={top['ref']}" if src_tag == "conversation"
        else f"{src_tag}: {top['ref']}"
    )
    return f"[recall] Possibly relevant past context — {where} — {snip}"


# Lazy-load the desktop_control module so missing pyautogui doesn't break boot.
_dc_mod = None
def _get_dc():
    global _dc_mod
    if _dc_mod is None:
        try:
            import desktop_control as _mod
            _dc_mod = _mod
        except ImportError as e:
            log.warning(f"[DESKTOP] module unavailable: {e}")
            return None
    return _dc_mod


def _desktop_call(action_fn) -> str:
    """Invoke a desktop_control.* call and return a string for the tool result.
    Catches missing pyautogui, the kill-switch, and runtime errors uniformly."""
    dc = _get_dc()
    if dc is None:
        return "Desktop control unavailable — install with: pip install pyautogui pillow"
    try:
        result = action_fn(dc)
        return json.dumps(result) if isinstance(result, dict) else str(result)
    except PermissionError as e:
        return str(e)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        log.exception("Desktop action failed")
        return f"Desktop action failed: {e}"


# Lazy-load the mail module too. No external deps — pure stdlib — so this
# basically never fails, but the wrapper keeps tool-result serialization
# uniform with the desktop tools.
_mail_mod = None
def _get_mail():
    global _mail_mod
    if _mail_mod is None:
        try:
            import mail as _mod
            _mail_mod = _mod
        except ImportError as e:
            log.warning(f"[MAIL] module unavailable: {e}")
            return None
    return _mail_mod


def _mail_call(action_fn) -> str:
    m = _get_mail()
    if m is None:
        return "Mail module unavailable."
    try:
        result = action_fn(m)
        if isinstance(result, (list, dict)):
            return json.dumps(result, default=str)
        return str(result)
    except KeyError as e:
        return f"Missing required field: {e}"
    except Exception as e:
        log.exception("Mail action failed")
        return f"Mail action failed: {e}"


# Lazy-load the Google Calendar module. Surfaces a clear error if google-auth
# libs aren't installed, or if the OAuth flow hasn't been completed yet.
_gcal_mod = None
def _get_gcal():
    global _gcal_mod
    if _gcal_mod is None:
        try:
            import gcalendar as _mod
            _gcal_mod = _mod
        except ImportError as e:
            log.warning(f"[GCAL] module unavailable: {e}")
            return None
    return _gcal_mod


def _gcal_call(action_fn) -> str:
    g = _get_gcal()
    if g is None:
        return "Calendar module unavailable."
    try:
        result = action_fn(g)
        if isinstance(result, (list, dict)):
            return json.dumps(result, default=str)
        return str(result)
    except KeyError as e:
        return f"Missing required field: {e}"
    except RuntimeError as e:
        return str(e)  # surfaces OAuth-not-set-up messages cleanly
    except Exception as e:
        log.exception("Calendar action failed")
        return f"Calendar action failed: {e}"


TOOL_HANDLERS = {
    "spawn_workspaces":      lambda inp: tool_spawn_workspaces(inp.get("workspaces", [])),
    "create_standing_order": lambda inp: tool_create_standing_order(inp),
    "web_search":     lambda inp: tool_web_search(inp["query"]),
    "web_extract":    lambda inp: tool_web_extract(inp["url"]),
    "read_file":      lambda inp: tool_read_file(inp["path"]),
    "write_file":     lambda inp: tool_write_file(inp["path"], inp["content"]),
    "list_directory": lambda inp: tool_list_directory(inp.get("path", "")),
    "terminal":       lambda inp: tool_terminal(inp["command"]),
    "remember":        lambda inp: tool_remember(inp["note"], inp.get("category", "General")),
    "recall_memory":   lambda inp: tool_recall_memory(),
    "execute_python":  lambda inp: tool_execute_python(inp["code"], inp.get("timeout", 30)),
    "read_clipboard":  lambda inp: tool_read_clipboard(),
    "write_clipboard": lambda inp: tool_write_clipboard(inp["text"]),
    "take_screenshot": lambda inp: tool_take_screenshot(),
    "load_skill":      lambda inp: tool_load_skill(inp["skill_name"]),
    "search_history":  lambda inp: tool_search_history(inp["query"], inp.get("k", 5), inp.get("scope", "current"), inp.get("sources")),
    "youtube_upload_video":   lambda inp: tool_youtube_upload_video(inp),
    "youtube_update_video":   lambda inp: tool_youtube_update_video(inp),
    "youtube_set_thumbnail":  lambda inp: tool_youtube_set_thumbnail(inp),
    # ── Pinterest ──
    "pinterest_list_boards":  lambda inp: tool_pinterest_list_boards(inp),
    "pinterest_create_pin":   lambda inp: tool_pinterest_create_pin(inp),
    # ── Desktop control (DIY tools — work under claude-api + ollama) ──
    "desktop_click":           lambda inp: _desktop_call(lambda dc: dc.mouse_click(inp.get("x"), inp.get("y"), inp.get("button", "left"), inp.get("clicks", 1))),
    "desktop_move":            lambda inp: _desktop_call(lambda dc: dc.mouse_move(inp["x"], inp["y"])),
    "desktop_drag":            lambda inp: _desktop_call(lambda dc: dc.mouse_drag(inp["start_x"], inp["start_y"], inp["end_x"], inp["end_y"])),
    "desktop_type":            lambda inp: _desktop_call(lambda dc: dc.type_text(inp["text"])),
    "desktop_key":             lambda inp: _desktop_call(lambda dc: dc.key_press(inp["keys"])),
    "desktop_scroll":          lambda inp: _desktop_call(lambda dc: dc.scroll(inp["amount"], inp.get("x"), inp.get("y"))),
    "desktop_cursor_position": lambda inp: _desktop_call(lambda dc: dc.cursor_position()),
    "desktop_screen_size":     lambda inp: _desktop_call(lambda dc: dc.screen_size()),
    # ── Mail (multi-inbox IMAP/SMTP) ──
    "mail_inboxes":  lambda inp: _mail_call(lambda m: m.list_accounts()),
    "mail_unread":   lambda inp: _mail_call(lambda m: m.list_unread(inp.get("account"), int(inp.get("limit", 20)))),
    "mail_search":   lambda inp: _mail_call(lambda m: m.search(inp["query"], inp.get("account"), int(inp.get("limit", 10)))),
    "mail_read":     lambda inp: _mail_call(lambda m: m.read_message(inp["account"], inp["message_id"])),
    "mail_draft":    lambda inp: _mail_call(lambda m: m.draft(
        inp["to"], inp["subject"], inp["body"],
        from_account=inp.get("from_account"), from_identity=inp.get("from_identity"),
        cc=inp.get("cc"), in_reply_to=inp.get("in_reply_to"))),
    "mail_send":     lambda inp: _mail_call(lambda m: m.send(
        inp["to"], inp["subject"], inp["body"],
        from_account=inp.get("from_account"), from_identity=inp.get("from_identity"),
        cc=inp.get("cc"), bcc=inp.get("bcc"), in_reply_to=inp.get("in_reply_to"))),
    # ── Google Calendar ──
    "calendar_list_calendars":  lambda inp: _gcal_call(lambda g: g.list_calendars()),
    "calendar_list_events":     lambda inp: _gcal_call(lambda g: g.list_events(
        calendar_id=inp.get("calendar_id", "primary"),
        time_min=inp.get("time_min"),
        time_max=inp.get("time_max"),
        max_results=int(inp.get("max_results", 20)),
        query=inp.get("query"))),
    "calendar_create_event":    lambda inp: _gcal_call(lambda g: g.create_event(
        summary=inp["summary"], start=inp["start"], end=inp["end"],
        calendar_id=inp.get("calendar_id", "primary"),
        description=inp.get("description"),
        location=inp.get("location"),
        attendees=inp.get("attendees"),
        timezone_str=inp.get("timezone_str"))),
    "calendar_update_event":    lambda inp: _gcal_call(lambda g: g.update_event(
        event_id=inp["event_id"],
        calendar_id=inp.get("calendar_id", "primary"),
        summary=inp.get("summary"),
        start=inp.get("start"),
        end=inp.get("end"),
        description=inp.get("description"),
        location=inp.get("location"))),
    "calendar_delete_event":    lambda inp: _gcal_call(lambda g: g.delete_event(
        event_id=inp["event_id"],
        calendar_id=inp.get("calendar_id", "primary"))),
    "calendar_free_busy":       lambda inp: _gcal_call(lambda g: g.free_busy(
        time_min=inp["time_min"],
        time_max=inp["time_max"],
        calendar_ids=inp.get("calendar_ids"))),
    # ── Native Anthropic computer tool — only reaches here for claude-api* providers.
    # Returns a dict ({"text": ...} or {"image_b64": ...}); _build_tool_result
    # special-cases the image case into a real image block.
    "computer": lambda inp: (_get_dc().execute_computer_action(**inp) if _get_dc() else {"text": "Desktop control unavailable — install pyautogui"}),
}


def _build_tool_result(block, result) -> dict:
    """Build a tool_result block. Special-cases the native computer tool's
    screenshot action — its {"image_b64": ...} return becomes a real image block
    so the model can see the screen after acting."""
    if isinstance(result, dict) and result.get("image_b64"):
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": result["image_b64"],
                },
            }],
        }
    if isinstance(result, dict):
        result = result.get("text") or json.dumps(result)
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": str(result),
    }


# ── Provider-aware tool list ─────────────────────────────────────
COMPUTER_USE_BETA = "computer-use-2025-01-24"

def _provider_supports_native_computer(provider_id: str) -> bool:
    """Native Anthropic `computer` tool is API-only. The API providers were
    removed by Captain order (2026-05-30) to prevent accidental billing, so
    no current provider supports the native computer tool. CLI providers use
    the desktop_* DIY tools instead. Returns False unconditionally."""
    return False


def _native_computer_tool_def() -> dict:
    """Anthropic computer-tool definition. Pulls real screen dims so the model
    emits coordinates in the right space."""
    try:
        sz = _get_dc().screen_size()
        w, h = sz["width"], sz["height"]
    except Exception:
        w, h = 1920, 1080
    return {
        "type": "computer_20250124",
        "name": "computer",
        "display_width_px":  int(w),
        "display_height_px": int(h),
        "display_number":    1,
    }


def _tools_for_provider(provider_id: str, with_cache: bool = True) -> list:
    """TOOLS + native computer tool (when provider supports it).
    Mirrors _tools_with_cache() — last tool gets a cache_control breakpoint."""
    base = list(TOOLS)
    if _provider_supports_native_computer(provider_id):
        base.append(_native_computer_tool_def())
    if not base:
        return base
    if with_cache:
        out = [dict(t) for t in base]
        out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
        return out
    return base


def execute_tool(name: str, inputs: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        _record_tool_result(name, False)
        return f"Unknown tool: {name}"
    log.info(f"[TOOL] {name}({json.dumps(inputs)[:120]})")
    try:
        result = handler(inputs)
        log.info(f"[TOOL] {name} → {str(result)[:120]}")
        _record_tool_result(name, True)
        return result
    except Exception as e:
        log.exception(f"[TOOL] {name} error: {e}")
        _record_tool_result(name, False)
        return f"Tool error: {e}"


MAX_TOKENS = 8192
MAX_LOOPS  = 5
MAX_HISTORY = 20    # rolling window of last N turns kept in prompt (lowered from 40 — cuts per-request cost in half when cache is cold)

def ask_hermes(message: str, project_path: str = "") -> str:
    """Agentic loop: Opus + tools. Runs until Data gives a final text response."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        return "I am unable to reach the main computer core, Captain. No API key is configured."

    try:
        import anthropic
    except ImportError:
        return "The anthropic package is not installed, Captain."

    client = anthropic.Anthropic(api_key=anthropic_key)
    soul = _load_soul()  # fresh per request — picks up new skills/memories without restart

    # Add user turn to history
    conversation_history.append({"role": "user", "content": message})
    if len(conversation_history) > MAX_HISTORY:
        del conversation_history[:-MAX_HISTORY]

    # Compress history if too long, then build working copy
    if len(conversation_history) > MAX_HISTORY:
        compressed = _compress_history(conversation_history, client, soul)
        conversation_history.clear()
        conversation_history.extend(compressed)
    messages = [dict(m) for m in conversation_history]

    # Tell DATA which project is open — he reads files with tools as needed
    active_path = project_path or _project_path
    if active_path:
        messages = [
            {"role": "user",      "content": f"[Active project: {active_path}]\nUse your read_file and list_directory tools to access specific files when needed."},
            {"role": "assistant", "content": "Understood, Captain. I am aware of the active project and will read files directly when required."},
        ] + messages

    # Auto-recall hint: quietly surface the single most relevant past item if
    # one strongly matches the user's current message. Silent when nothing is
    # confidently relevant. Best-effort — never blocks the turn on failure.
    try:
        hint = auto_recall_hint(message)
        if hint:
            messages = [
                {"role": "user",      "content": hint},
                {"role": "assistant", "content": "Acknowledged — I will use that recall if relevant."},
            ] + messages
    except Exception as e:
        log.warning(f"[RECALL] auto-hint failed: {e}")

    final_text = ""

    try:
        for loop in range(MAX_LOOPS):
            log.info(f"[AGENT] loop={loop} messages={len(messages)}")
            _set_status("thinking", f"Processing — cycle {loop + 1}")
            _apply_conversation_cache(messages)
            pid = _current_provider_id()
            tools = _tools_for_provider(pid)
            needs_beta = _provider_supports_native_computer(pid)
            api = client.beta.messages if needs_beta else client.messages
            kw = dict(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": soul, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            )
            if needs_beta:
                kw["betas"] = [COMPUTER_USE_BETA]
            resp = api.create(**kw)

            if resp.stop_reason == "end_turn":
                _set_status("responding")
                # Extract final text
                for block in resp.content:
                    if hasattr(block, "text"):
                        final_text += block.text
                # Save assistant turn to persistent history
                conversation_history.append({
                    "role": "assistant",
                    "content": final_text.strip()
                })
                # Trim and persist to disk
                if len(conversation_history) > MAX_HISTORY:
                    del conversation_history[:-MAX_HISTORY]
                _save_history(conversation_history)
                global _session_turns
                _session_turns += 1
                break

            elif resp.stop_reason == "tool_use":
                # Append assistant's tool-use blocks to working messages
                messages.append({"role": "assistant", "content": resp.content})

                # Execute each tool and collect results
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        label  = _TOOL_LABELS.get(block.name, block.name.replace("_", " ").title())
                        # Build a short detail string from the most relevant input field
                        inp    = block.input or {}
                        detail = (inp.get("query") or inp.get("url") or inp.get("path") or
                                  inp.get("skill_name") or inp.get("command") or
                                  inp.get("note") or inp.get("text") or inp.get("action") or "")
                        if detail and len(detail) > 60:
                            detail = detail[:57] + "..."
                        _set_status("tool", f"{label}{': ' + detail if detail else ''}")
                        result = execute_tool(block.name, block.input)
                        tool_results.append(_build_tool_result(block, result))

                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason
                log.warning(f"[AGENT] unexpected stop_reason={resp.stop_reason}")
                for block in resp.content:
                    if hasattr(block, "text"):
                        final_text += block.text
                break

        if not final_text:
            final_text = "I have completed the requested operations, Captain."

        return final_text.strip()

    except Exception as e:
        log.exception(f"[AGENT] error: {e}")
        # Roll back the user message
        if conversation_history and conversation_history[-1]["role"] == "user":
            conversation_history.pop()
        return f"My neural matrix encountered an error, Captain. ({e})"
    finally:
        _set_status("idle")


def ask_hermes_cli(message: str, project_path: str = "") -> str:
    """Non-streaming CLI mode — used by /chat. /chat_stream uses ask_hermes_cli_stream."""
    context_lines = []
    for turn in conversation_history[-12:]:
        role = "Captain" if turn["role"] == "user" else "Data"
        content = turn.get("content", "")
        if isinstance(content, str) and content:
            context_lines.append(f"{role}: {content[:600]}")

    if context_lines:
        prompt = "Recent conversation:\n" + "\n".join(context_lines) + f"\n\nCaptain: {message}"
    else:
        prompt = message

    active_path = project_path or _project_path
    if active_path:
        prompt = f"[Active project: {active_path}]\n\n{prompt}"

    try:
        soul_cli = _load_soul_cli()  # fresh per request — picks up new skills without restart
        # Use resolved absolute path — when the bridge runs from pythonw it
        # may not have ~/.local/bin in PATH, so plain "claude" can't be found.
        claude_exe = _provider_executable("claude-cli") or "claude"
        # SOUL.md + memory + tool docs together exceed Windows' 32KB cmdline
        # limit. Write to a temp file and pass via --system-prompt-file.
        soul_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8")
        soul_file.write(soul_cli); soul_file.close()
        log.info(f"[CLI] subprocess starting — model=claude-opus-4-8 prompt_len={len(prompt)} exe={claude_exe}")
        cli_env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        result = subprocess.run(
            [claude_exe, "--print", "--output-format", "text",
             "--model", "claude-opus-4-8",
             "--dangerously-skip-permissions",
             "--system-prompt-file", soul_file.name, prompt],
            capture_output=True, text=True, encoding="utf-8",
            cwd=_active_cwd(), env=cli_env
        )
        try: os.unlink(soul_file.name)
        except OSError: pass
        response = result.stdout.strip()
        log.info(f"[CLI] subprocess complete — exit={result.returncode} resp_len={len(response)} stderr={result.stderr.strip()[:120]!r}")
        if not response:
            response = result.stderr.strip() or "I was unable to generate a response, Captain."

        # Save to history same as API mode
        conversation_history.append({"role": "user", "content": message})
        conversation_history.append({"role": "assistant", "content": response})
        if len(conversation_history) > MAX_HISTORY:
            del conversation_history[:-MAX_HISTORY]
        _save_history(conversation_history)
        global _session_turns
        _session_turns += 1

        return response
    except FileNotFoundError:
        log.error("[CLI] 'claude' command not found — is Claude Code installed?")
        return "Claude Code CLI not found, Captain. Ensure 'claude' is installed and on the PATH."
    except Exception as e:
        log.exception(f"[CLI] error: {e}")
        return f"CLI mode error, Captain. ({e})"


def ask_hermes_cli_stream(message: str, project_path: str, send_sse) -> None:
    """
    Streaming CLI mode. Runs claude --print --output-format stream-json via Popen,
    parses events line by line, and fires real SSE updates as tools execute.
    """
    global _session_turns
    context_lines = []
    for turn in conversation_history[-12:]:
        role = "Captain" if turn["role"] == "user" else "Data"
        content = turn.get("content", "")
        if isinstance(content, str) and content:
            context_lines.append(f"{role}: {content[:600]}")

    if context_lines:
        prompt = "Recent conversation:\n" + "\n".join(context_lines) + f"\n\nCaptain: {message}"
    else:
        prompt = message

    active_path = project_path or _project_path
    if active_path:
        prompt = f"[Active project: {active_path}]\n\n{prompt}"

    # Stage attachments for the CLI subprocess. Text content folds inline;
    # image/PDF go to a temp dir and we tell Claude to Read them by path.
    _attachments = getattr(_request_attachments, "list", None) or []
    attach_tmpdir, attach_paths, attach_inline = _stage_cli_attachments(_attachments)
    if attach_inline:
        prompt = prompt + attach_inline
    if attach_paths:
        paths_block = "\n".join(f"- {p}" for p in attach_paths)
        prompt = prompt + (
            "\n\n[ATTACHMENTS — the Captain dropped these files in for this turn. "
            "Use your Read tool to open each one before answering, then refer back to them in your reply:]\n"
            + paths_block + "\n"
        )

    soul_cli = _load_soul_cli()
    # Model comes from the *current* provider's config (honors thread-local
    # voice override) so claude-cli, claude-cli-sonnet, claude-cli-haiku each
    # get the right model id passed to the CLI.
    cli_model = PROVIDERS.get(_current_provider_id(), {}).get("model", "claude-opus-4-8")
    send_sse('thinking', f"*Standard Mode ({cli_model}) — initiating*")
    soul_file_path = None
    try:
        # Strip API key so claude CLI uses subscription auth, not API credits
        cli_env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        # Resolve absolute path — pythonw.exe may not have ~/.local/bin in PATH
        claude_exe = _provider_executable(_current_provider_id()) or "claude"
        # SOUL is too long for Windows 32KB cmdline — write to temp file
        sf = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8")
        sf.write(soul_cli); sf.close()
        soul_file_path = sf.name
        log.info(f"[CLI-STREAM] subprocess starting model={cli_model} prompt_len={len(prompt)} exe={claude_exe!r}")
        proc = subprocess.Popen(
            [claude_exe, "--print", "--output-format", "stream-json", "--verbose",
             "--model", cli_model,
             "--dangerously-skip-permissions",
             "--system-prompt-file", soul_file_path, prompt],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            cwd=_active_cwd(), env=cli_env
        )
        _register_active_proc(proc)
    except FileNotFoundError as fnf:
        # Log the FULL error — Windows often says "system can't find the file
        # specified" when it's actually the cwd or a child process that's missing,
        # not the claude exe itself.
        log.exception(f"[CLI-STREAM] FileNotFoundError exe={claude_exe!r} cwd={_active_cwd()!r} err={fnf}")
        send_sse('token', f"Claude Code CLI startup failed, Captain. Detail: {fnf}")
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        _cleanup_cli_attachments(attach_tmpdir)
        return
    except Exception as e:
        log.exception(f"[CLI-STREAM] startup error: {e}")
        send_sse('token', f"CLI startup error, Captain. ({e})")
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        _cleanup_cli_attachments(attach_tmpdir)
        return

    final_text = ""

    def _format_cli_detail(tool_name, inp):
        raw = (inp.get("query") or inp.get("url") or inp.get("path") or
               inp.get("command") or inp.get("note") or "")
        if tool_name == "web_search" and raw:
            return f'"{raw[:55]}"' if len(raw) > 55 else f'"{raw}"'
        if tool_name in ("Read", "Write", "read_file", "write_file") and raw:
            return Path(raw).name
        return (raw[:55] + "...") if len(raw) > 55 else raw

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                log.debug(f"[CLI-STREAM] non-JSON line: {line[:80]}")
                continue

            etype = ev.get("type", "")
            log.debug(f"[CLI-STREAM] event type={etype!r}")

            if etype == "assistant":
                msg = ev.get("message", {})
                stop_reason = msg.get("stop_reason", "")
                for block in msg.get("content", []):
                    btype = block.get("type", "")
                    if btype == "text":
                        text = block.get("text", "").strip()
                        if not text:
                            continue
                        if stop_reason == "end_turn":
                            # Final response — stream as tokens
                            final_text += text
                            buf = ''
                            for word in text.split(' '):
                                buf += word + ' '
                                if len(buf) >= 25:
                                    send_sse('token', buf)
                                    buf = ''
                            if buf:
                                send_sse('token', buf)
                        elif stop_reason == "tool_use":
                            # Pre-tool reasoning — show as thinking
                            preview = text[:80] + ("..." if len(text) > 80 else "")
                            send_sse('thinking', preview)
                        else:
                            # Unknown stop_reason — treat as final if substantial
                            if len(text) > 50:
                                final_text += text
                                send_sse('token', text)
                    elif btype == "tool_use":
                        name = block.get("name", "")
                        inp  = block.get("input", {})
                        label  = _TOOL_LABELS.get(name, name.replace("_", " ").title())
                        detail = _format_cli_detail(name, inp)
                        send_sse('thinking', f"{label}{' ' + detail if detail else ''}")

            elif etype == "user":
                # Tool results come back wrapped in a user message
                msg = ev.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_result":
                        raw = block.get("content", "")
                        if isinstance(raw, list):
                            raw = " ".join(b.get("text", "") for b in raw if b.get("type") == "text")
                        preview = str(raw).replace('\n', ' ').strip()[:90]
                        if preview:
                            send_sse('thinking', f"  → {preview}")

            elif etype == "result":
                result_text = ev.get("result", "").strip()
                is_error    = ev.get("is_error", False)
                subtype     = ev.get("subtype", "")
                log.info(f"[CLI-STREAM] result event: is_error={is_error} subtype={subtype!r} result_text={result_text[:120]!r}")
                if is_error:
                    if subtype == "max_turns":
                        err_msg = "I reached my turn limit on that task, Captain. Try switching to API mode for complex multi-step work, or break the request into smaller steps."
                    elif result_text:
                        err_msg = f"Error from Claude Code, Captain: {result_text}"
                    else:
                        err_msg = f"Claude Code returned an error (subtype: {subtype or 'unknown'}), Captain."
                    if not final_text:
                        send_sse('token', err_msg)
                        final_text = err_msg
                elif result_text and not final_text:
                    # Fallback: result event has text we haven't sent yet
                    final_text = result_text
                    buf = ''
                    for word in result_text.split(' '):
                        buf += word + ' '
                        if len(buf) >= 25:
                            send_sse('token', buf)
                            buf = ''
                    if buf:
                        send_sse('token', buf)

    except Exception as e:
        log.exception(f"[CLI-STREAM] read error: {e}")

    proc.wait()
    # was_killed = killed by user (negative on Unix). On Windows terminate()
    # returns exit code 1 (positive), so also treat exit==1 + recent /stop
    # as a kill. backstop_killed = deliberate end-of-turn kill by the
    # set_project_path marker handler — NOT an abort, NOT an error, the
    # confirmation is already showing via the project_rooted UI event.
    # preempted = a newer turn on the same pane started and tree-killed us.
    # The new turn now owns the pane; we must NOT emit any fallback text or
    # save anything to history (the new turn will overwrite the slot).
    backstop_killed = getattr(proc, "_backstop_killed", False)
    preempted       = getattr(proc, "_preempted", False)
    user_stopped    = getattr(proc, "_user_stopped", False)
    was_killed = ((proc.returncode is not None and proc.returncode < 0)
                  and not backstop_killed and not preempted) or user_stopped
    _unregister_active_proc(proc)
    stderr = proc.stderr.read().strip()
    log.info(f"[CLI-STREAM] done exit={proc.returncode} final_len={len(final_text)} backstop={backstop_killed} preempted={preempted} user_stopped={user_stopped} stderr={stderr[:120]!r}")

    if preempted:
        # A newer turn replaced us. End the stream silently — no fallback,
        # no history write. The new turn owns the pane now.
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        if soul_file_path:
            try: os.unlink(soul_file_path)
            except OSError: pass
        _cleanup_cli_attachments(attach_tmpdir)
        return

    if was_killed:
        msg = "Aborted, Captain."
        if not final_text:
            send_sse('token', msg)
            final_text = msg
    elif backstop_killed:
        # Frontend already showed the project_rooted confirmation. Don't
        # add any fallback text here — just let the stream end cleanly.
        if not final_text:
            final_text = "(rooted)"  # placeholder so history isn't empty
    elif not final_text:
        fallback = stderr or "I was unable to generate a response, Captain."
        send_sse('token', fallback)
        final_text = fallback

    # Save to history (skip on abort to avoid polluting context)
    if not was_killed:
        conversation_history.append({"role": "user",      "content": message})
        conversation_history.append({"role": "assistant",  "content": final_text.strip()})
        if len(conversation_history) > MAX_HISTORY:
            del conversation_history[:-MAX_HISTORY]
        _save_history(conversation_history)
        _session_turns += 1

    send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
    send_sse('done', '')
    # Clean up the temp soul-prompt file created at the top of this function
    if soul_file_path:
        try: os.unlink(soul_file_path)
        except OSError: pass
    # Remove any media attachment temp files staged for this turn.
    _cleanup_cli_attachments(attach_tmpdir)


def _build_history_prompt(message: str, project_path: str = "") -> str:
    """Shared helper: build a single-prompt string with recent history + active project for non-Claude CLIs."""
    context_lines = []
    for turn in conversation_history[-12:]:
        role = "Captain" if turn["role"] == "user" else "Data"
        content = turn.get("content", "")
        if isinstance(content, str) and content:
            context_lines.append(f"{role}: {content[:600]}")
    if context_lines:
        prompt = "Recent conversation:\n" + "\n".join(context_lines) + f"\n\nCaptain: {message}"
    else:
        prompt = message
    active_path = project_path or _project_path
    if active_path:
        prompt = f"[Active project: {active_path}]\n\n{prompt}"
    return prompt


def ask_codex_cli_stream(message: str, project_path: str, send_sse) -> None:
    """
    OpenAI Codex CLI runner.
    Pipes prompt via stdin to dodge Windows 32KB argv limit.
    Strips OPENAI_API_KEY so codex uses the user's ChatGPT subscription auth.
    Parses --json (JSONL) stream events for tool calls / thinking / final text.
    """
    global _session_turns
    exe = _provider_executable("codex")
    if not exe:
        send_sse('token', "Codex CLI is not installed, Captain. Run: npm i -g @openai/codex")
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        return

    soul   = _load_soul(mode="cli")
    prompt = _build_history_prompt(message, project_path)
    full_input = soul + "\n\n---\n\n" + prompt  # codex has no --system-prompt; prepend persona

    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    send_sse('thinking', "*Codex Mode — invoking ChatGPT subscription*")
    log.info(f"[CODEX] subprocess starting prompt_len={len(full_input)}")

    try:
        proc = subprocess.Popen(
            [exe, "exec", "--json", "--skip-git-repo-check",
             "--dangerously-bypass-approvals-and-sandbox"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            cwd=_active_cwd(), env=env,
        )
        _register_active_proc(proc)
        proc.stdin.write(full_input)
        proc.stdin.close()
    except FileNotFoundError:
        send_sse('token', "Codex executable found at startup but failed to launch, Captain.")
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        return

    final_text = ""
    error_text = ""
    input_tokens = 0
    output_tokens = 0

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            log.debug(f"[CODEX] non-JSON: {line[:120]}")
            continue

        etype = ev.get("type", "")
        log.debug(f"[CODEX] event {etype!r}")

        if etype == "thread.started":
            send_sse('thinking', f"Session: {ev.get('thread_id','')[:8]}")

        elif etype == "turn.started":
            send_sse('thinking', "*Reasoning*")

        elif etype == "item.completed":
            # Codex 0.130 wraps text/tool-call/reasoning blocks here
            item = ev.get("item", {}) or {}
            itype = item.get("type", "")
            if itype == "agent_message":
                text = item.get("text") or item.get("message") or ""
                if text:
                    # Codex doesn't stream deltas — text arrives all at once. Chunk it
                    # for a smoother UI feel.
                    final_text += text
                    buf = ""
                    for word in text.split(" "):
                        buf += word + " "
                        if len(buf) >= 25:
                            send_sse('token', buf); buf = ""
                    if buf:
                        send_sse('token', buf)
            elif itype == "agent_reasoning":
                summary = (item.get("text") or item.get("summary") or "")[:80]
                if summary:
                    send_sse('thinking', summary)
            elif itype in ("function_call", "tool_call", "shell_command"):
                name   = item.get("name") or item.get("tool") or item.get("command") or "tool"
                detail = item.get("arguments") or item.get("command") or ""
                if isinstance(detail, (dict, list)):
                    detail = json.dumps(detail)[:60]
                else:
                    detail = str(detail)[:60]
                send_sse('thinking', f"Running {name}{': ' + detail if detail else ''}")
            elif itype in ("function_call_output", "tool_call_output", "shell_command_output"):
                out = item.get("output") or item.get("result") or ""
                preview = str(out).replace('\n', ' ').strip()[:90]
                if preview:
                    send_sse('thinking', f"  → {preview}")

        elif etype == "turn.completed":
            usage = ev.get("usage", {}) or {}
            input_tokens  = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))

        elif etype == "error":
            error_text += ev.get("message", "") + " "

        elif etype == "turn.failed":
            err = (ev.get("error", {}) or {}).get("message", "turn failed")
            error_text += err

    proc.wait()
    preempted    = getattr(proc, "_preempted", False)
    user_stopped = getattr(proc, "_user_stopped", False)
    was_killed = ((proc.returncode is not None and proc.returncode < 0) and not preempted) or user_stopped
    _unregister_active_proc(proc)
    stderr = proc.stderr.read().strip()
    log.info(f"[CODEX] done exit={proc.returncode} final_len={len(final_text)} in={input_tokens} out={output_tokens} preempted={preempted} error={error_text[:120]!r}")

    if preempted:
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        return

    if was_killed:
        msg = "Aborted, Captain."
        if not final_text:
            send_sse('token', msg); final_text = msg
    elif not final_text.strip():
        if "401" in error_text or "Unauthorized" in error_text:
            fallback = "Codex authentication failed, Captain. Run `codex login` once in PowerShell to link your ChatGPT account."
        else:
            fallback = error_text.strip() or stderr or "Codex returned no output, Captain."
        send_sse('token', fallback)
        final_text = fallback

    if not was_killed and not error_text:
        conversation_history.append({"role": "user",      "content": message})
        conversation_history.append({"role": "assistant", "content": final_text.strip()})
        if len(conversation_history) > MAX_HISTORY:
            del conversation_history[:-MAX_HISTORY]
        _save_history(conversation_history)
        _session_turns += 1

    send_sse('meta', json.dumps({'input_tokens': input_tokens, 'output_tokens': output_tokens}))
    send_sse('done', '')


def ask_gemini_cli_stream(message: str, project_path: str, send_sse) -> None:
    """
    Google Gemini CLI runner. Pipes prompt via stdin.
    Uses free-tier Google AI Studio auth (gemini login).
    """
    global _session_turns
    exe = _provider_executable("gemini")
    if not exe:
        send_sse('token', "Gemini CLI is not installed, Captain. Run: npm i -g @google/gemini-cli")
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        return

    soul   = _load_soul(mode="cli")
    prompt = _build_history_prompt(message, project_path)
    full_input = soul + "\n\n---\n\n" + prompt

    env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY")}
    send_sse('thinking', "*Gemini Mode — invoking Google AI Studio*")
    log.info(f"[GEMINI] subprocess starting prompt_len={len(full_input)}")

    try:
        proc = subprocess.Popen(
            # --yolo: auto-approve tool calls; --skip-trust: bypass the trusted-
            # folders check that otherwise demotes us back to "default" approval
            # mode and refuses to run headlessly in untrusted cwd.
            [exe, "--yolo", "--skip-trust"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            cwd=_active_cwd(), env=env,
        )
        _register_active_proc(proc)
    except FileNotFoundError:
        send_sse('token', "Gemini executable found at startup but failed to launch, Captain.")
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        return

    # Gemini CLI sometimes closes stdin before we finish writing (especially on
    # Windows with large prompts) — that surfaces as [Errno 22] Invalid argument.
    # Push stdin write to a thread and swallow pipe errors so the real reason
    # comes through stderr below instead of crashing the dispatch.
    def _feed_stdin():
        try:
            proc.stdin.write(full_input)
        except (OSError, ValueError) as ex:
            log.warning(f"[GEMINI] stdin write failed: {ex!r}")
        finally:
            try: proc.stdin.close()
            except Exception: pass
    threading.Thread(target=_feed_stdin, daemon=True).start()

    final_text = ""
    for line in proc.stdout:
        final_text += line
        send_sse('token', line)
    proc.wait()
    preempted    = getattr(proc, "_preempted", False)
    user_stopped = getattr(proc, "_user_stopped", False)
    was_killed = ((proc.returncode is not None and proc.returncode < 0) and not preempted) or user_stopped
    _unregister_active_proc(proc)
    stderr = proc.stderr.read().strip()
    log.info(f"[GEMINI] done exit={proc.returncode} final_len={len(final_text)} preempted={preempted} stderr={stderr[:200]!r}")

    if preempted:
        send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
        send_sse('done', '')
        return

    if was_killed:
        msg = "Aborted, Captain."
        if not final_text:
            send_sse('token', msg); final_text = msg
    elif not final_text.strip():
        fallback = stderr or "Gemini returned no output, Captain."
        send_sse('token', fallback); final_text = fallback

    if not was_killed:
        conversation_history.append({"role": "user",      "content": message})
        conversation_history.append({"role": "assistant", "content": final_text.strip()})
        if len(conversation_history) > MAX_HISTORY:
            del conversation_history[:-MAX_HISTORY]
        _save_history(conversation_history)
        _session_turns += 1

    send_sse('meta', json.dumps({'input_tokens': 0, 'output_tokens': 0}))
    send_sse('done', '')


def _tools_for_ollama() -> list:
    """
    Convert our Anthropic-shaped TOOLS list into Ollama's function-calling format.
    Difference: 'input_schema' → 'parameters', wrapped in {type: function, function: {...}}.
    """
    out = []
    for t in TOOLS:
        out.append({
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t.get("description", ""),
                "parameters":  t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


def _extract_tool_calls_from_text(text: str, valid_tool_names: set) -> tuple[list, str]:
    """
    Fallback for Ollama models that emit tool-call-shaped JSON in text content
    instead of populating the structured tool_calls field. Scans for JSON objects
    containing a 'name' that matches a known tool, plus 'arguments' or 'parameters'.

    Returns (tool_calls, cleaned_text) — cleaned_text has the JSON blocks removed
    so we don't leak them into the user-visible response.
    """
    if not text:
        return [], text

    found  = []
    spans  = []  # (start, end) of JSON blocks to strip

    # Walk the string finding balanced { ... } blocks and try to parse each
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        depth   = 0
        in_str  = False
        escape  = False
        start   = i
        for j in range(i, len(text)):
            ch = text[j]
            if escape:
                escape = False; continue
            if ch == "\\":
                escape = True; continue
            if ch == '"' and not escape:
                in_str = not in_str; continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = text[start:j + 1]
                    try:
                        obj = json.loads(blob)
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        name = obj.get("name") or obj.get("tool") or obj.get("function")
                        args = obj.get("arguments") or obj.get("parameters") or obj.get("input") or {}
                        if isinstance(name, str) and name in valid_tool_names:
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = {}
                            if not isinstance(args, dict):
                                args = {}
                            found.append({"function": {"name": name, "arguments": args}})
                            spans.append((start, j + 1))
                    i = j + 1
                    break
        else:
            break

    if not spans:
        return [], text

    # Strip the matched JSON blocks from the visible text (in reverse order so offsets stay valid)
    cleaned = text
    for s, e in reversed(spans):
        cleaned = cleaned[:s] + cleaned[e:]
    cleaned = cleaned.strip()
    return found, cleaned


def ask_ollama_stream(message: str, project_path: str, send_sse) -> None:
    """
    Ollama runner with full agentic tool-calling.
    Uses Ollama 0.4+ /api/chat tools parameter. Loops up to MAX_LOOPS times executing
    any tools the model calls, then streams the final text response back.

    Requires a tool-capable model (qwen2.5-coder, llama3.1+, mistral, qwen3, etc.)
    on a running Ollama daemon at localhost:11434.
    """
    global _session_turns
    # Read from the active provider so "ollama" (7B coder) and "ollama-small"
    # (3B chat) both route through this runner with the right model. Honors
    # the thread-local override so voice requests can pin "ollama-small" without
    # affecting a concurrent chat request.
    p = PROVIDERS.get(_current_provider_id(), PROVIDERS["ollama"])
    soul    = _load_soul(mode="cli")
    prompt  = _build_history_prompt(message, project_path)
    ollama_tools = _tools_for_ollama()

    send_sse('thinking', f"*Ollama Mode — {p['model']} ({len(ollama_tools)} tools)*")
    log.info(f"[OLLAMA] starting model={p['model']} tools={len(ollama_tools)} prompt_len={len(prompt)}")

    valid_tool_names = {t["name"] for t in TOOLS}

    messages = [
        {"role": "system", "content": soul},
        {"role": "user",   "content": prompt},
    ]

    final_text   = ""
    in_tokens    = 0
    out_tokens   = 0
    fatal_error  = ""

    for loop in range(MAX_LOOPS):
        body = json.dumps({
            "model":    p["model"],
            "stream":   True,
            "messages": messages,
            "tools":    ollama_tools,
            # Hard token cap in voice mode — keeps TTS synthesis time bounded.
            # ~200 tokens ≈ 4 spoken sentences; matches the prompt directive.
            **({"options": {"num_predict": 200}} if VOICE_CONVERSATION_MODE else {}),
        }).encode("utf-8")
        req = urllib.request.Request(p["url"], data=body, headers={"Content-Type": "application/json"})

        assistant_text = ""
        tool_calls     = []

        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = ev.get("message", {}) or {}
                    chunk = msg.get("content", "") or ""
                    if chunk:
                        assistant_text += chunk
                        # Stream text tokens optimistically — if tool calls come, the text was
                        # the model's reasoning before deciding to use a tool, which is fine to show
                        send_sse('token', chunk)

                    # Tool calls usually appear on the final chunk
                    if msg.get("tool_calls"):
                        tool_calls = msg["tool_calls"]

                    if ev.get("done"):
                        # Final chunk also carries usage numbers
                        in_tokens  += int(ev.get("prompt_eval_count", 0))
                        out_tokens += int(ev.get("eval_count", 0))
                        break
        except urllib.error.URLError as e:
            fatal_error = f"Ollama unreachable at {p['url']} ({e}). Is the daemon running?"
            break
        except Exception as e:
            log.exception(f"[OLLAMA] stream error: {e}")
            fatal_error = f"Ollama error: {e}"
            break

        # Fallback: some Ollama models emit tool-call-shaped JSON in text content
        # instead of the structured tool_calls field. Scan for it.
        if not tool_calls and assistant_text:
            fallback_calls, cleaned = _extract_tool_calls_from_text(assistant_text, valid_tool_names)
            if fallback_calls:
                log.info(f"[OLLAMA] extracted {len(fallback_calls)} tool call(s) from text fallback")
                tool_calls     = fallback_calls
                assistant_text = cleaned

        # No tool calls? That's the final answer.
        if not tool_calls:
            final_text = assistant_text
            break

        # Model wants to use tools — append its message and execute each call
        messages.append({
            "role":       "assistant",
            "content":    assistant_text,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn       = tc.get("function", {}) or {}
            tool_name = fn.get("name", "")
            tool_args = fn.get("arguments", {})
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except Exception:
                    tool_args = {}

            # Surface the tool call in the thought stream
            label  = _TOOL_LABELS.get(tool_name, tool_name.replace("_", " ").title())
            raw_d  = (tool_args.get("query") or tool_args.get("url") or tool_args.get("path") or
                      tool_args.get("command") or tool_args.get("note") or "")
            if tool_name == "web_search" and raw_d:
                detail = f'"{raw_d[:55]}"' if len(raw_d) > 55 else f'"{raw_d}"'
            elif tool_name in ("read_file", "write_file") and raw_d:
                detail = Path(raw_d).name
            elif raw_d:
                detail = (raw_d[:55] + "...") if len(raw_d) > 55 else raw_d
            else:
                detail = ""
            send_sse('thinking', f"{label}{' ' + detail if detail else ''}")

            try:
                result = execute_tool(tool_name, tool_args)
            except Exception as e:
                result = f"Tool error: {e}"

            preview = str(result).replace('\n', ' ').strip()[:90]
            if preview:
                send_sse('thinking', f"  → {preview}")

            messages.append({
                "role":    "tool",
                "content": str(result),
            })

        # ...and loop back for the next response
    else:
        # MAX_LOOPS exhausted without the model giving a tool-free final answer
        if not final_text:
            final_text = assistant_text or "Reached the tool-use turn limit, Captain."

    if fatal_error:
        send_sse('token', fatal_error)
        final_text = fatal_error
    elif not final_text:
        final_text = "Ollama returned no response, Captain."
        send_sse('token', final_text)

    # Persist a clean turn pair (skip on fatal errors)
    if not fatal_error and final_text:
        conversation_history.append({"role": "user",      "content": message})
        conversation_history.append({"role": "assistant", "content": final_text.strip()})
        if len(conversation_history) > MAX_HISTORY:
            del conversation_history[:-MAX_HISTORY]
        _save_history(conversation_history)
        _session_turns += 1

    log.info(f"[OLLAMA] done loops={loop+1} final_len={len(final_text)} in={in_tokens} out={out_tokens}")
    send_sse('meta', json.dumps({'input_tokens': in_tokens, 'output_tokens': out_tokens}))
    send_sse('done', '')


def _provider_runner(provider_id: str):
    """Return the streaming runner function for a provider id."""
    # Dynamically-pulled local models register as "ollama:<model>" providers
    # (see _sync_ollama_providers) — route every ollama* id to the local runner.
    if provider_id.startswith("ollama"):
        return ask_ollama_stream
    return {
        "claude-cli":        ask_hermes_cli_stream,
        "claude-cli-sonnet": ask_hermes_cli_stream,  # same runner; model read from PROVIDERS[active]['model']
        "claude-cli-haiku":  ask_hermes_cli_stream,  # same runner; subscription Haiku for fast no-cost voice
        "claude-cli-fable":  ask_hermes_cli_stream,  # same runner; subscription Fable 5 — most powerful tier
        # claude-api / claude-api-fast removed by Captain order (2026-05-30) —
        # ask_hermes_stream is now unreachable from the dispatcher but kept
        # intact as dead code for an easy revert if API access is wanted back.
        "codex":             ask_codex_cli_stream,
        "gemini":            ask_gemini_cli_stream,
        "ollama":            ask_ollama_stream,
        "ollama-small":      ask_ollama_stream,     # same runner; model read from PROVIDERS[active]['model']
    }.get(provider_id, ask_hermes_cli_stream)


def dispatch(message: str, project_path: str = "", pane_id: str = "") -> str:
    """Route to API or CLI mode based on BRIDGE_MODE. Used by the legacy non-streaming /chat endpoint."""
    _bind_history(project_path, pane_id)
    if BRIDGE_MODE == "cli":
        log.info(f"[DISPATCH] CLI mode — message={message!r}")
        return ask_hermes_cli(message, project_path=project_path)
    log.info(f"[DISPATCH] API mode — message={message!r}")
    return ask_hermes(message, project_path=project_path)


def _dispatch_with_provider(provider_id: str, message: str, project_path: str = "", pane_id: str = "") -> str:
    """Non-streaming dispatch that pins a specific provider for the call. Used
    by /chat when a project window has its own model selected. The provider is
    pinned via thread-local so concurrent chat requests in other windows still
    see ACTIVE_PROVIDER (or their own override)."""
    _bind_history(project_path, pane_id)   # route conversation_history to this project+pane's bucket
    runner = _provider_runner(provider_id)
    parts: list[str] = []
    def _sse(event_type: str, text: str) -> None:
        if event_type == "token":
            parts.append(text)
    # Filter spawn_workspaces markers out of the returned text — they go to
    # the UI event queue instead of being shown in the chat bubble.
    filtered_sse = _marker_filter_sse(_sse)
    prev_id = getattr(_provider_override, "id", None)
    _provider_override.id = provider_id
    try:
        runner(message, project_path, filtered_sse)
    except Exception as e:
        log.exception(f"[dispatch-provider:{provider_id}] failed: {e}")
        return f"Provider '{provider_id}' failed: {e}"
    finally:
        filtered_sse.finalize()
        _provider_override.id = prev_id
    return "".join(parts).strip()


def _tools_with_cache():
    """Return TOOLS list with `cache_control` on the last tool — Anthropic
    treats this as a cache breakpoint at the end of the tool definitions, so
    every subsequent request re-uses the cached tools block at 10% the input
    price instead of re-reading them at full price."""
    if not TOOLS:
        return TOOLS
    out = [dict(t) for t in TOOLS]
    out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


def _apply_conversation_cache(messages: list) -> None:
    """Mark messages[-2] (the last turn before the new user message) as a
    cache breakpoint. Lets multi-turn chats re-use the entire prior history
    at the 10% cache-hit price. Mutates in place."""
    if len(messages) < 2:
        return
    prev = messages[-2]
    content = prev.get("content")
    if isinstance(content, str):
        prev["content"] = [{"type": "text", "text": content,
                            "cache_control": {"type": "ephemeral"}}]
    elif isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            # Don't overwrite cache_control on tool_use/tool_result blocks — only
            # text blocks accept it cleanly. Skip those.
            if last.get("type") in (None, "text"):
                last["cache_control"] = {"type": "ephemeral"}


def ask_hermes_stream(message: str, project_path: str, send_sse) -> None:
    """
    Streaming agentic loop. Calls send_sse(event_type, text) live:
      'thinking' — inner monologue line (tool calls, status)
      'token'    — response text chunk as it streams
      'done'     — stream complete
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        send_sse('token', "I am unable to reach the main computer core, Captain. No API key is configured.")
        send_sse('done', '')
        return

    try:
        import anthropic as _anthropic
    except ImportError:
        send_sse('token', "The anthropic package is not installed, Captain.")
        send_sse('done', '')
        return

    client = _anthropic.Anthropic(api_key=anthropic_key)
    soul = _load_soul()  # fresh per request — picks up new skills/memories without restart

    # Pull any attachments the HTTP layer staged for this request and fold them
    # into the user-turn content as Anthropic content blocks.
    _attachments = getattr(_request_attachments, "list", None) or []
    _user_content = _build_user_content(message, _attachments)
    conversation_history.append({"role": "user", "content": _user_content})
    if len(conversation_history) > MAX_HISTORY:
        del conversation_history[:-MAX_HISTORY]

    messages = [dict(m) for m in conversation_history]

    active_path = project_path or _project_path
    if active_path:
        messages = [
            {"role": "user",      "content": f"[Active project: {active_path}]\nUse your read_file and list_directory tools to access specific files when needed."},
            {"role": "assistant", "content": "Understood, Captain. I am aware of the active project and will read files directly when required."},
        ] + messages

    # Auto-recall hint (streaming path).
    try:
        hint = auto_recall_hint(message)
        if hint:
            messages = [
                {"role": "user",      "content": hint},
                {"role": "assistant", "content": "Acknowledged — I will use that recall if relevant."},
            ] + messages
    except Exception as e:
        log.warning(f"[RECALL] auto-hint failed: {e}")

    final_text = ""
    total_input_tokens = 0
    total_output_tokens = 0

    try:
        send_sse('thinking', "*Neural neural net engaged — initiating query processing*")
        _set_status("thinking", "Processing")

        for loop in range(MAX_LOOPS):
            log.info(f"[STREAM] loop={loop}")
            _set_status("thinking", f"Processing — cycle {loop + 1}")

            try:
                # Model is provider-aware so claude-api (Opus) and claude-api-fast
                # (Haiku) share this runner but hit different endpoints. Honors
                # the thread-local override (voice mode pins Haiku here).
                pid = _current_provider_id()
                api_model = PROVIDERS.get(pid, {}).get("model", MODEL)
                # Cache breakpoints: system (soul), end-of-tools, and end of the
                # last prior turn. Cuts input cost ~70-80% on repeat turns.
                _apply_conversation_cache(messages)
                tools = _tools_for_provider(pid)
                needs_beta = _provider_supports_native_computer(pid)
                api = client.beta.messages if needs_beta else client.messages
                stream_kw = dict(
                    model=api_model,
                    max_tokens=MAX_TOKENS,
                    system=[{"type": "text", "text": soul, "cache_control": {"type": "ephemeral"}}],
                    tools=tools,
                    messages=messages,
                )
                if needs_beta:
                    stream_kw["betas"] = [COMPUTER_USE_BETA]
                with api.stream(**stream_kw) as stream:
                    for text_chunk in stream.text_stream:
                        send_sse('token', text_chunk)
                        final_text += text_chunk
                    final_msg = stream.get_final_message()
                    if hasattr(final_msg, 'usage') and final_msg.usage:
                        total_input_tokens += final_msg.usage.input_tokens
                        total_output_tokens += final_msg.usage.output_tokens

            except Exception as stream_err:
                log.exception(f"[STREAM] API error: {stream_err}")
                if conversation_history and conversation_history[-1]["role"] == "user":
                    conversation_history.pop()
                send_sse('token', f"My neural matrix encountered an error, Captain. ({stream_err})")
                return  # outer finally still sends meta + done — exactly once

            if final_msg.stop_reason == "end_turn":
                _set_status("responding")
                conversation_history.append({"role": "assistant", "content": final_text.strip()})
                if len(conversation_history) > MAX_HISTORY:
                    del conversation_history[:-MAX_HISTORY]
                _save_history(conversation_history)
                global _session_turns
                _session_turns += 1
                break

            elif final_msg.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": final_msg.content})
                tool_results = []
                for block in final_msg.content:
                    if block.type == "tool_use":
                        label  = _TOOL_LABELS.get(block.name, block.name.replace("_", " ").title())
                        inp    = block.input or {}
                        raw_detail = (inp.get("query") or inp.get("url") or inp.get("path") or
                                      inp.get("skill_name") or inp.get("command") or
                                      inp.get("note") or inp.get("text") or inp.get("action") or "")
                        # Format detail contextually
                        if block.name == "web_search" and raw_detail:
                            detail = f'"{raw_detail[:55]}"' if len(raw_detail) > 55 else f'"{raw_detail}"'
                        elif block.name in ("read_file", "write_file") and raw_detail:
                            detail = Path(raw_detail).name  # just the filename, not full path
                        elif raw_detail:
                            detail = raw_detail[:60] + ("..." if len(raw_detail) > 60 else "")
                        else:
                            detail = ""
                        send_sse('thinking', f"{label}{' ' + detail if detail else ''}")
                        _set_status("tool", f"{label}{': ' + detail if detail else ''}")
                        result = execute_tool(block.name, block.input)
                        # Preview text only — image results (computer screenshot) get a stub.
                        if isinstance(result, dict) and result.get("image_b64"):
                            preview = "[screenshot returned]"
                        else:
                            preview = (result.get("text") if isinstance(result, dict) else str(result)).replace('\n', ' ').strip()
                            if len(preview) > 90:
                                preview = preview[:87] + "..."
                        send_sse('thinking', f"  → {preview}")
                        tool_results.append(_build_tool_result(block, result))
                messages.append({"role": "user", "content": tool_results})
                if loop < MAX_LOOPS - 1:
                    send_sse('thinking', "*Cross-referencing results — formulating response*")

            else:
                log.warning(f"[STREAM] unexpected stop_reason={final_msg.stop_reason}")
                for block in final_msg.content:
                    if hasattr(block, "text"):
                        send_sse('token', block.text)
                        final_text += block.text
                break

        if not final_text:
            send_sse('token', "I have completed the requested operations, Captain.")

    except Exception as e:
        log.exception(f"[STREAM] outer error: {e}")
        if conversation_history and conversation_history[-1]["role"] == "user":
            conversation_history.pop()
        send_sse('token', f"My neural matrix encountered an error, Captain. ({e})")

    finally:
        _set_status("idle")
        send_sse('meta', json.dumps({'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens}))
        send_sse('done', '')


_SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'(\[])')

def _first_n_sentences(text: str, n: int) -> str:
    """Take the first n sentences of a reply for TTS. Falls back to the raw
    text if the splitter can't find sentence boundaries (e.g. the model emitted
    a single long sentence). Keeps a hard char cap as a final safety net."""
    text = (text or "").strip()
    if not text:
        return text
    parts = _SENTENCE_END.split(text)
    out = " ".join(parts[:n]).strip()
    # Cap at 400 chars no matter what — guards against pathological replies.
    return out[:400]


def _voice_llm_stream(message: str, project_path: str = "", on_token=None,
                      voice: str = "data") -> str:
    """Run the voice provider's streaming runner; forwards each token to
    on_token(text) in real time so callers can split into sentences and dispatch
    TTS as soon as each sentence completes. Returns the full collected text.

    Pins the voice provider via the thread-local override so concurrent chat
    requests still see ACTIVE_PROVIDER. Toggles VOICE_CONVERSATION_MODE so
    _load_soul appends the spoken-conversation directive (short replies, no
    markdown, in-character). `voice` selects which bridge-crew persona answers
    — _load_soul reads VOICE_ACTIVE_CREW to swap in that officer's identity.
    """
    global VOICE_CONVERSATION_MODE, VOICE_ACTIVE_CREW
    provider = VOICE_PROVIDER
    runner = _provider_runner(provider)
    parts: list[str] = []
    def _sse(event_type: str, text: str) -> None:
        if event_type == 'token':
            parts.append(text)
            if on_token:
                try: on_token(text)
                except Exception as cb_err:
                    log.exception(f"[voice-stream] on_token raised: {cb_err}")
    # Filter spawn_workspaces markers out of the voice stream so they neither
    # get spoken by TTS nor visibly appear in the transcript bubble.
    filtered_sse = _marker_filter_sse(_sse)
    t0 = time.time()
    prev_mode = VOICE_CONVERSATION_MODE
    prev_crew = VOICE_ACTIVE_CREW
    VOICE_CONVERSATION_MODE = True
    VOICE_ACTIVE_CREW = _normalize_voice(voice)
    _provider_override.id = provider
    try:
        runner(message, project_path, filtered_sse)
    except Exception as e:
        log.exception(f"[voice-stream] {provider} failed: {e}")
        return f"I am unable to respond, Captain. ({e})"
    finally:
        filtered_sse.finalize()
        VOICE_CONVERSATION_MODE = prev_mode
        VOICE_ACTIVE_CREW = prev_crew
        _provider_override.id = None
    dt = time.time() - t0
    log.info(f"[voice-stream] {provider} produced {len(''.join(parts))} chars in {dt:.2f}s")
    return "".join(parts).strip()


def _voice_llm_dispatch(message: str, project_path: str = "",
                        voice: str = "data") -> str:
    """Non-streaming variant — kept for /speak (the legacy single-shot endpoint)."""
    return _voice_llm_stream(message, project_path, on_token=None, voice=voice)


def full_pipeline(audio_bytes: bytes, suffix: str = ".webm",
                  voice: str = "data") -> dict:
    """Local STT → active provider LLM → local F5-TTS. Returns dict for JSON response.

    `voice` selects the bridge-crew officer who answers — it drives both the
    persona (via _voice_llm_dispatch) and the cloned voice (via synthesize).
    """
    import time
    voice = _normalize_voice(voice)

    # Hard gate on warmup: if F5-TTS is still loading and Whisper tries to
    # transcribe at the same time, both contend for GPU memory and STT can
    # take 45s+ instead of 3s. Better to fail fast with a clear message and
    # let the UI show "models still warming up" than silently hang.
    if not _voice_ready.is_set():
        return {
            "error":    "warming_up",
            "message":  "Voice models are still loading. Try again in a few seconds.",
        }

    # 1. Transcribe (faster-whisper distil-large-v3, GPU)
    t_stt = time.time()
    user_text = local_voice.transcribe(audio_bytes, suffix)
    log.info(f"[voice] STT in {time.time() - t_stt:.2f}s: {user_text!r}")
    if not user_text:
        return {"error": "no_speech"}

    # "Computer stop" — an interrupt, not a query. Abort without running the
    # LLM or TTS; the dashboard stops any in-flight playback on this signal.
    if _is_voice_abort(user_text):
        log.info(f"[voice] interrupt phrase heard: {user_text!r}")
        return {"interrupt": True, "user_text": user_text}

    # 2. LLM — routes through the *voice provider* (default ollama-small, opt-in haiku).
    log.info(f"[voice] LLM start | voice_provider={VOICE_PROVIDER} crew={voice} (chat is {ACTIVE_PROVIDER})")
    response_text = _voice_llm_dispatch(user_text, voice=voice)

    # 3. TTS (F5-TTS, cloned Data voice) — wrapped in a worker thread with a
    # hard timeout so a hung first-load (~F5-TTS cold start on a tight 8GB GPU)
    # doesn't pin the request forever. If TTS times out we still return text
    # and the UI plays no audio rather than spinning indefinitely.
    audio_b64 = ""
    audio_mime = "audio/wav"
    tts_result = {"wav": None, "mime": None, "err": None}

    # Trim before synthesis: TTS time scales linearly with input length, so even
    # with the LLM token cap an over-talker can still produce a long monologue.
    # Take the first 4 sentences for voice; the full text still ships back so
    # the transcript shows everything.
    tts_text = _first_n_sentences(response_text, 4)
    # Same self-name strip as the streaming pipeline — keyed to the active crew.
    tts_text = _strip_self_name(tts_text, voice)
    if len(tts_text) < len(response_text):
        log.info(f"[voice] TTS text trimmed {len(response_text)}→{len(tts_text)} chars")

    def _tts_worker():
        try:
            wav_bytes, mime = local_voice.synthesize_long(tts_text, voice=voice)
            tts_result["wav"]  = wav_bytes
            tts_result["mime"] = mime
        except Exception as e:
            tts_result["err"] = e

    t_tts = time.time()
    tts_thread = threading.Thread(target=_tts_worker, daemon=True)
    tts_thread.start()
    # 90s budget: enough for cold-start model load + first synthesis on CUDA,
    # but bounded so the user gets *something* back even on a wedged GPU.
    tts_thread.join(timeout=90)
    if tts_thread.is_alive():
        log.error(f"[TTS] timed out after {time.time() - t_tts:.1f}s — returning text only")
    elif tts_result["err"]:
        log.exception(f"[TTS error] {tts_result['err']}")
    elif tts_result["wav"] is not None:
        audio_b64 = base64.b64encode(tts_result["wav"]).decode()
        audio_mime = tts_result["mime"]
        log.info(f"[voice] TTS in {time.time() - t_tts:.2f}s: {len(tts_result['wav'])} bytes")

    return {
        "user_text":     user_text,
        "response_text": response_text,
        "audio_b64":     audio_b64,
        "audio_mime":    audio_mime,
        "voice":         voice,
        "crew_name":     crew_display_name(voice),
    }


_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+')

def stream_voice_pipeline(audio_bytes: bytes, suffix: str, send_sse,
                          max_sentences: int = 4, voice: str = "data") -> None:
    """Streaming voice pipeline: STT → stream LLM → per-sentence TTS → SSE.

    Drops perceived latency by ~50% vs full_pipeline because the user hears
    sentence 1 while the LLM is still generating (and TTS-ing) sentence 2.

    `voice` selects the bridge-crew officer answering — it drives both the
    persona and the cloned voice.

    SSE events emitted:
      - crew        data: {"voice": "probe", "name": "Probe"}
      - user_text   data: {"text": "..."}
      - text_chunk  data: {"text": "<sentence>"}
      - audio_chunk data: {"audio_b64": "...", "audio_mime": "audio/wav"}
      - interrupt   data: {"reason": "computer-stop"}   (abort phrase heard)
      - done        data: {"response_text": "...", "voice": "...", "crew_name": "..."}
      - error       data: {"error": "..."}
    """
    voice = _normalize_voice(voice)
    if not _voice_ready.is_set():
        send_sse('error', json.dumps({"error": "warming_up",
                                      "message": "Voice models still loading."}))
        return

    # 1. STT ──────────────────────────────────────────────────────
    t0 = time.time()
    try:
        user_text = local_voice.transcribe(audio_bytes, suffix)
    except Exception as e:
        log.exception(f"[stream-voice] STT failed: {e}")
        send_sse('error', json.dumps({"error": str(e)}))
        return
    log.info(f"[stream-voice] STT in {time.time() - t0:.2f}s: {user_text!r}")
    if not user_text:
        send_sse('error', json.dumps({"error": "no_speech"}))
        return

    # "Computer stop" — an interrupt, not a query. Surface the heard text so
    # the transcript shows it, signal the abort, and skip the LLM + TTS.
    if _is_voice_abort(user_text):
        log.info(f"[stream-voice] interrupt phrase heard: {user_text!r}")
        send_sse('user_text', json.dumps({"text": user_text}))
        send_sse('interrupt', json.dumps({"reason": "computer-stop"}))
        return

    # Tell the dashboard which officer is answering — authoritative, so the
    # status band / transcript label come from the bridge, not a client guess.
    send_sse('crew', json.dumps({"voice": voice, "name": crew_display_name(voice)}))
    send_sse('user_text', json.dumps({"text": user_text}))

    # 2. Stream LLM with per-sentence TTS dispatch ────────────────
    log.info(f"[stream-voice] LLM start | voice_provider={VOICE_PROVIDER} crew={voice}")
    state = {"buf": "", "sentences_sent": 0, "stop": False}

    def _emit_sentence(text: str) -> None:
        """Synthesize one sentence and emit text_chunk + audio_chunk."""
        text = text.strip()
        # Strip the self-name prefix on the very first sentence. Small models
        # trained on `Captain: ... Data: ...` few-shot patterns sometimes
        # emit "Data:" as the literal start of their reply, which TTS would
        # then speak aloud as "Data colon ...". Belt-and-suspenders for the
        # system-prompt rule.
        if state["sentences_sent"] == 0:
            text = _strip_self_name(text, voice)
        if not text:
            return
        send_sse('text_chunk', json.dumps({"text": text}))
        ts = time.time()
        try:
            # synthesize_long, not synthesize: a single run-on sentence still
            # exceeds F5's clean window, so let it sub-split at clause/word
            # boundaries. Short sentences fall straight through unchanged.
            wav_bytes, mime = local_voice.synthesize_long(text, voice=voice)
        except Exception as e:
            log.exception(f"[stream-tts] sentence failed: {e}")
            return
        log.info(f"[stream-tts] sentence {state['sentences_sent']+1} in "
                 f"{time.time()-ts:.2f}s ({len(text)} chars → {len(wav_bytes)} bytes)")
        send_sse('audio_chunk', json.dumps({
            "audio_b64":  base64.b64encode(wav_bytes).decode(),
            "audio_mime": mime,
        }))

    def _on_token(token: str) -> None:
        if state["stop"]:
            return
        state["buf"] += token
        # Pull off any complete sentences ending in . ! or ? followed by space.
        while True:
            m = _SENTENCE_BREAK.search(state["buf"])
            if not m:
                break
            end = m.end()
            sentence = state["buf"][:end]
            state["buf"] = state["buf"][end:]
            _emit_sentence(sentence)
            state["sentences_sent"] += 1
            if state["sentences_sent"] >= max_sentences:
                state["stop"] = True
                return

    response_text = _voice_llm_stream(user_text, "", on_token=_on_token, voice=voice)

    # 3. Flush remainder (the last sentence may not have a trailing space) ──
    remainder = state["buf"].strip()
    if remainder and state["sentences_sent"] < max_sentences:
        _emit_sentence(remainder)
        state["sentences_sent"] += 1

    send_sse('done', json.dumps({
        "response_text": response_text,
        "voice":         voice,
        "crew_name":     crew_display_name(voice),
    }))
    log.info(f"[stream-voice] complete — {state['sentences_sent']} sentence(s) sent")


def read_memory() -> str:
    try:
        return MEMORY_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def build_drives_graph() -> dict:
    """Return drives as root nodes with their immediate children — no This PC hub."""
    nodes, links = [], []
    drives_found = []

    try:
        result = subprocess.run(
            ["wmic", "logicaldisk", "get", "caption,volumename,size,freespace", "/format:csv"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Node"):
                continue
            parts = line.split(",")
            if len(parts) < 3:
                continue
            caption = parts[1].strip() if len(parts) > 1 else ""
            free    = parts[2].strip() if len(parts) > 2 else "0"
            size    = parts[3].strip() if len(parts) > 3 else "0"
            label   = parts[4].strip() if len(parts) > 4 else ""
            if not caption or not caption.endswith(":"):
                continue
            drive_path = caption + "\\"
            try:
                free_gb = round(int(free) / (1024**3), 1) if free.isdigit() else 0
                size_gb = round(int(size) / (1024**3), 1) if size.isdigit() else 0
                display = f"{caption} {label}" if label else caption
                extra = f"{free_gb}GB free / {size_gb}GB" if size_gb else ""
            except Exception:
                display, extra, free_gb, size_gb = caption, "", 0, 0
            nid = f"drive-{caption.replace(':', '')}"
            nodes.append({
                "id": nid, "label": display, "type": "core",
                "r": 18, "hub": True, "path": drive_path,
                "extra": extra, "free_gb": free_gb, "size_gb": size_gb,
            })
            drives_found.append((nid, drive_path))
    except Exception as e:
        log.warning(f"[DRIVES] {e}")

    if not drives_found:
        nodes.append({"id": "drive-C", "label": "C:", "type": "core", "r": 18, "hub": True, "path": "C:\\"})
        drives_found.append(("drive-C", "C:\\"))

    # Add immediate children of each drive
    for drive_nid, drive_path in drives_found:
        try:
            p = Path(drive_path)
            children = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:20]
            for child in children:
                if child.name.startswith('$') or child.name.startswith('.'):
                    continue
                cid = f"{drive_nid}-{child.name}"
                ftype = "folder" if child.is_dir() else "file"
                nodes.append({
                    "id": cid, "label": child.name, "type": ftype,
                    "r": 9 if child.is_dir() else 6,
                    "hub": child.is_dir(), "path": str(child),
                })
                links.append({"source": drive_nid, "target": cid, "w": 1.5})
        except (PermissionError, OSError):
            pass

    return {"nodes": nodes, "links": links}


_bridge_start_time = datetime.datetime.now()
_session_turns = 0   # increments each exchange this session; starts at 0 so the bar fills up
# Per-project CLI subprocess registry, keyed by the same history-key
# (_history_key(project_path)) used for conversation isolation. Lets /stop
# target ONE pane's request without killing concurrent requests in other
# panes — important for saving API credits when the Captain aborts a
# workspace pane while another pane is still streaming.
_active_cli_procs: dict = {}
_active_cli_procs_lock = threading.Lock()

def _kill_proc_tree(proc) -> None:
    """Force-kill a CLI subprocess AND its descendants. claude.exe spawns its
    own child processes (tool runners, MCP servers, Bash subshells, etc.) —
    a plain proc.kill() only takes out the parent, leaving the children to
    keep looping in the background. On Windows we use `taskkill /F /T /PID`
    to walk the tree; on POSIX we fall back to killpg of the proc group."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return  # already dead
    except Exception:
        pass
    pid = getattr(proc, "pid", None)
    if pid is None:
        try: proc.kill()
        except Exception: pass
        return
    try:
        if os.name == "nt":
            # /F = force, /T = include child tree. Hide its console window.
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            _OriginalPopen(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                startupinfo=si,
            ).wait(timeout=5)
        else:
            import signal as _signal
            try:
                os.killpg(os.getpgid(pid), _signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception as ex:
        log.warning(f"[proc-tree-kill] failed pid={pid}: {ex}")
        try: proc.kill()
        except Exception: pass

def _register_active_proc(proc) -> None:
    """Runner calls this right after spawning its CLI subprocess so /stop
    and the marker backstop can find it by the current thread's project.

    Preemption: if a previous proc is still alive in this pane's slot, it
    means the Captain fired a new turn while the prior turn's claude.exe was
    still looping. Kill the prior tree before replacing the slot — otherwise
    the orphaned process keeps running tools (chrome-cdp, file edits, API
    calls) in the background and burns subscription quota while the new turn
    races against it on the same pane."""
    key = getattr(_history_state, "key", "")
    stale = None
    with _active_cli_procs_lock:
        prior = _active_cli_procs.get(key)
        if prior is not None and prior is not proc:
            try:
                if prior.poll() is None:
                    stale = prior
            except Exception:
                stale = prior
        _active_cli_procs[key] = proc
    if stale is not None:
        # Tag so the post-loop knows this was a deliberate preemption, not an
        # abort/crash — suppresses the "I was unable to generate a response"
        # fallback on the old turn (the new turn now owns the pane).
        setattr(stale, "_preempted", True)
        log.info(f"[preempt] new turn on key={key!r} — killing prior CLI proc tree pid={getattr(stale, 'pid', '?')}")
        _kill_proc_tree(stale)

def _unregister_active_proc(proc) -> None:
    """Idempotent — only removes the entry if it's still ours (handles the
    case where a new request raced in and replaced our slot)."""
    key = getattr(_history_state, "key", "")
    with _active_cli_procs_lock:
        if _active_cli_procs.get(key) is proc:
            _active_cli_procs.pop(key, None)

# ── Active project context ────────────────────────────────
_project_path: str = ""
_project_nodes: list = []   # for frontend tree rendering
_project_text:  str  = ""   # compact text injected into DATA's context

def _active_cwd() -> str:
    """Return the working directory tools/subprocesses should use:
    1. Per-request project path bound by _bind_history() on this thread — so
       a request from pane A runs from pane A's folder even if pane B was
       opened more recently.
    2. The global _project_path (last /project POST or marker) — fallback
       for non-request threads (background tasks, standing orders, etc.).
    3. The Captain's home directory — last resort.
    DATA's own files are accessed by absolute path (the soul tells the model
    where they live), so there's no need to root subprocesses there by default.

    Each candidate is re-validated as a live directory at call time. A project
    folder that was moved/deleted/renamed after it was registered would
    otherwise crash every subprocess with [WinError 267] (ERROR_DIRECTORY);
    instead we fall through to the next candidate, then to home."""
    for candidate in (getattr(_history_state, "project_path", ""), _project_path):
        if candidate and Path(candidate).is_dir():
            return candidate
        if candidate:
            log.warning(f"[cwd] stale project path skipped (not a directory): {candidate!r}")
    return str(Path.home())

_IGNORE_DIRS  = {".git", "node_modules", "__pycache__", ".next", "dist",
                 "build", ".venv", "venv", ".mypy_cache", ".pytest_cache",
                 "coverage", ".cache", "out", ".nuxt"}
_IGNORE_FILES = {".env", ".env.local", ".DS_Store", "Thumbs.db"}
_TEXT_EXTS    = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
                 ".json", ".md", ".txt", ".yaml", ".yml", ".toml",
                 ".env.example", ".sh", ".bat", ".ps1", ".sql", ".csv",
                 ".xml", ".ini", ".cfg", ".gitignore"}

def _scan_project(root: str, max_depth: int = 4) -> tuple[list, str]:
    """Return (nodes_for_frontend, compact_text_tree_for_context)."""
    root_path = Path(root)
    nodes = []
    lines = [f"PROJECT: {root}"]

    def _walk(path: Path, depth: int, prefix: str):
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        entries = [e for e in entries
                   if not (e.is_dir() and e.name in _IGNORE_DIRS)
                   and e.name not in _IGNORE_FILES
                   and not e.name.startswith(".")]
        for i, entry in enumerate(entries):
            is_last  = i == len(entries) - 1
            connector = "└─ " if is_last else "├─ "
            child_pfx = prefix + ("   " if is_last else "│  ")
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                nodes.append({"name": entry.name, "path": str(entry), "type": "dir", "depth": depth})
                _walk(entry, depth + 1, child_pfx)
            else:
                size = entry.stat().st_size
                size_str = (f"{size}B" if size < 1024 else
                            f"{size/1024:.1f}KB" if size < 1024*1024 else
                            f"{size/1024/1024:.1f}MB")
                is_text = entry.suffix.lower() in _TEXT_EXTS
                lines.append(f"{prefix}{connector}{entry.name} ({size_str})")
                nodes.append({"name": entry.name, "path": str(entry),
                              "type": "file", "size": size, "readable": is_text, "depth": depth})

    _walk(root_path, 1, "")
    return nodes, "\n".join(lines)

# Live agent status — updated during ask_hermes(), read by GET /status
_agent_status: dict = {"step": "idle", "detail": ""}
_status_lock = threading.Lock()

def _set_status(step: str, detail: str = "") -> None:
    with _status_lock:
        _agent_status["step"]   = step
        _agent_status["detail"] = detail

_TOOL_LABELS = {
    "web_search":      "Searching",
    "web_extract":     "Reading page",
    "read_file":       "Reading",
    "write_file":      "Writing",
    "list_directory":  "Listing",
    "terminal":        "Running",
    "execute_python":  "Running Python",
    "read_clipboard":  "Reading clipboard",
    "write_clipboard": "Writing to clipboard",
    "take_screenshot": "Taking screenshot",
    "remember":        "Saving to memory",
    "recall_memory":   "Reading memory",
    "load_skill":      "Loading skill",
    "desktop_click":           "Clicking",
    "desktop_move":            "Moving cursor",
    "desktop_drag":            "Dragging",
    "desktop_type":            "Typing",
    "desktop_key":             "Pressing keys",
    "desktop_scroll":          "Scrolling",
    "desktop_cursor_position": "Reading cursor",
    "desktop_screen_size":     "Reading screen size",
    "computer":                "Computer use",
    "mail_inboxes":            "Listing inboxes",
    "mail_unread":             "Checking inbox",
    "mail_search":             "Searching mail",
    "mail_read":               "Reading email",
    "mail_draft":              "Drafting email",
    "mail_send":               "Sending email",
    "calendar_list_calendars": "Listing calendars",
    "calendar_list_events":    "Checking calendar",
    "calendar_create_event":   "Creating event",
    "calendar_update_event":   "Updating event",
    "calendar_delete_event":   "Deleting event",
    "calendar_free_busy":      "Checking availability",
}

def build_vitals() -> dict:
    """Return real system vitals for the dashboard."""
    turns = _session_turns                                                          # exchanges this session
    history_pct = min(100, round((len(conversation_history) / MAX_HISTORY) * 100)) if MAX_HISTORY else 0

    memory_bytes = COMPUTER_MEMORY_FILE.stat().st_size if COMPUTER_MEMORY_FILE.exists() else 0
    soul_bytes   = (HERMES_DIR / "SOUL.md").stat().st_size if (HERMES_DIR / "SOUL.md").exists() else 0

    uptime_secs  = int((datetime.datetime.now() - _bridge_start_time).total_seconds())
    uptime_mins  = uptime_secs // 60
    uptime_str   = f"{uptime_mins}m" if uptime_mins < 60 else f"{uptime_mins // 60}h {uptime_mins % 60}m"

    claude_skill_count = sum(
        1 for d in CLAUDE_SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    ) if CLAUDE_SKILLS_DIR.exists() else 0

    active_cfg = PROVIDERS.get(ACTIVE_PROVIDER, {})

    return {
        "model":            active_cfg.get("model", MODEL),
        "provider_id":      ACTIVE_PROVIDER,
        "provider_label":   active_cfg.get("label", ACTIVE_PROVIDER),
        "mode":             BRIDGE_MODE,
        "turns":            turns,
        "max_turns":        MAX_HISTORY,
        "history_pct":      history_pct,
        "memory_bytes":     memory_bytes,
        "memory_kb":        round(memory_bytes / 1024, 1),
        "soul_bytes":       soul_bytes,
        "uptime":           uptime_str,
        "api_tools":        len(TOOLS),
        "claude_skills":    claude_skill_count,
    }


# ══ System Health ═════════════════════════════════════════════════
# Real metrics dressed up as Trek systems. The sidebar widget polls
# /vitals_fast (SSE, sub-second) for the engine gauge + 4 subsystem bars
# and the alert dot. Subsystems are derived from cheap signals:
#
#   Hull Integrity      ← rolling tool success rate (last 20)
#   Shield Efficiency   ← cloudflared tunnel reachability
#   Structural Field    ← free disk space %
#   Inertial Dampeners  ← event-loop lag (sleep drift on sampler thread)
#   Engine Speed          ← composite of CPU + RAM + GPU + token velocity
#   Alert (G/Y/R)       ← worst subsystem, or manual override
#
# Nothing here is load-bearing for correctness — it's vibes. Keep cheap.

_tool_results_window = collections.deque(maxlen=20)   # (name, ok, ts) tuples
_llm_results_window  = collections.deque(maxlen=20)   # bool — last 20 LLM calls succeeded?
_event_loop_lag_ms  = 0.0               # updated by sampler thread
_token_velocity     = collections.deque(maxlen=40)   # (timestamp, char_count) pairs
_gpu_stats_cache    = {"ts": 0.0, "util": 0, "mem_used_mb": 0, "mem_total_mb": 0, "temp_c": 0}
_net_io_prev        = {"ts": 0.0, "sent": 0, "recv": 0}     # for delta computation
_llm_inflight       = 0                 # bumped on stream start, dec'd on end
_last_user_activity = time.time()       # last /chat or /chat_stream POST


def _record_tool_result(name: str, ok: bool) -> None:
    _tool_results_window.append((name, bool(ok), time.time()))


def _record_llm_result(ok: bool) -> None:
    """Called by the chat-stream runner on completion. Drives the Shields gauge."""
    _llm_results_window.append(bool(ok))


def record_stream_chunk(text: str) -> None:
    """Streaming code pushes here so the engine gauge spools up when Data is mid-response.
    Call from /chat_stream or wherever model output text arrives in chunks."""
    if text:
        _token_velocity.append((time.time(), len(text)))


def mark_user_activity() -> None:
    """Bump 'last user heard from' timestamp. Called from chat POST handlers."""
    global _last_user_activity
    _last_user_activity = time.time()


def llm_stream_start() -> None:
    global _llm_inflight
    _llm_inflight += 1


def llm_stream_end() -> None:
    global _llm_inflight
    _llm_inflight = max(0, _llm_inflight - 1)


def _tokens_per_sec() -> float:
    """Chars/sec over the last ~3s of stream chunks, scaled to ~tokens (÷4)."""
    if not _token_velocity:
        return 0.0
    now = time.time()
    recent = [(t, n) for (t, n) in _token_velocity if now - t < 3.0]
    if len(recent) < 2:
        return 0.0
    span = max(0.5, recent[-1][0] - recent[0][0])
    total_chars = sum(n for _, n in recent)
    return (total_chars / span) / 4.0


def _gpu_stats() -> dict:
    """Best-effort NVIDIA GPU snapshot: util %, VRAM used/total in MB, temp °C.
    Returns zeros on any failure (no GPU, no driver, etc). Cached 2s
    so we don't fork nvidia-smi on every SSE tick."""
    now = time.time()
    if now - _gpu_stats_cache["ts"] < 2.0:
        return _gpu_stats_cache
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.5,
        )
        if out.returncode == 0:
            parts = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
            _gpu_stats_cache.update({
                "util":         int(parts[0]),
                "mem_used_mb":  int(parts[1]),
                "mem_total_mb": int(parts[2]),
                "temp_c":       int(parts[3]),
            })
    except Exception:
        _gpu_stats_cache.update({"util": 0, "mem_used_mb": 0, "mem_total_mb": 0, "temp_c": 0})
    _gpu_stats_cache["ts"] = now
    return _gpu_stats_cache


_cpu_temp_cache = {"ts": 0.0, "val": None}
def _cpu_temp() -> float | None:
    """Best-effort CPU temperature in °C. Returns None when unavailable.
    On Windows, psutil.sensors_temperatures() is usually empty unless a
    third-party hardware monitor (OpenHardwareMonitor / LibreHardwareMonitor)
    is running. Linux/macOS typically work out of the box. Cached 5s."""
    now = time.time()
    if now - _cpu_temp_cache["ts"] < 5.0:
        return _cpu_temp_cache["val"]
    val = None
    if psutil and hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures() or {}
            # Try a handful of common sensor keys (coretemp = Intel, k10temp = AMD,
            # cpu_thermal = ARM, acpitz = generic ACPI thermal zone).
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if key in temps and temps[key]:
                    val = float(temps[key][0].current)
                    break
        except Exception:
            val = None
    _cpu_temp_cache["ts"]  = now
    _cpu_temp_cache["val"] = val
    return val


def _net_bps() -> tuple[int, int]:
    """Bytes/sec sent and received since last call. First call seeds the
    baseline and returns 0/0 (no delta available yet)."""
    if not psutil:
        return 0, 0
    try:
        io = psutil.net_io_counters()
    except Exception:
        return 0, 0
    now = time.time()
    if _net_io_prev["ts"] == 0:
        _net_io_prev.update({"ts": now, "sent": io.bytes_sent, "recv": io.bytes_recv})
        return 0, 0
    dt = max(0.05, now - _net_io_prev["ts"])
    sent_bps = max(0, int((io.bytes_sent - _net_io_prev["sent"]) / dt))
    recv_bps = max(0, int((io.bytes_recv - _net_io_prev["recv"]) / dt))
    _net_io_prev.update({"ts": now, "sent": io.bytes_sent, "recv": io.bytes_recv})
    return sent_bps, recv_bps


def _tunnel_healthy() -> bool:
    """Cloudflared tunnel is considered up if tunnel_url.txt exists and is non-empty."""
    f = Path(__file__).parent / "tunnel_url.txt"
    try:
        return f.exists() and bool(f.read_text(encoding="utf-8").strip())
    except Exception:
        return False


def _start_dampeners_sampler() -> None:
    """Background thread: every 250ms, measure sleep drift and stash it.
    Spikes mean the event loop / GIL is being beaten up → 'inertial dampeners' drop."""
    def _loop():
        global _event_loop_lag_ms
        target = 0.25
        while True:
            t0 = time.perf_counter()
            time.sleep(target)
            actual = time.perf_counter() - t0
            drift = max(0.0, (actual - target) * 1000.0)
            # Smooth so the bar doesn't jitter on every GC pause
            _event_loop_lag_ms = (_event_loop_lag_ms * 0.7) + (drift * 0.3)
    threading.Thread(target=_loop, daemon=True, name="dampeners-sampler").start()


_start_dampeners_sampler()


# ── Power Core health (uncached prompt token budget) ──────────
# Treats `used_tokens` (system prompt + history sent on the FIRST request of
# a session, before prompt caching kicks in) as a proxy for engine-core
# integrity. At the captured baseline → 100% health. At 2× baseline → 10%
# health → one-shot breach popup.
_power_core_baseline_tokens   = 0
_power_core_was_above_thresh  = True   # tracks crossings — popup fires once per breach episode
_power_core_popup_pending     = False  # set on crossing; cleared by client /power_core/ack


def _load_power_core_baseline() -> int:
    try:
        if POWER_CORE_BASELINE_FILE.exists():
            return int(json.loads(POWER_CORE_BASELINE_FILE.read_text(encoding="utf-8")).get("baseline_tokens", 0))
    except Exception as e:
        log.warning(f"[engine-core] could not load baseline: {e}")
    return 0


def _save_power_core_baseline(tokens: int) -> None:
    try:
        POWER_CORE_BASELINE_FILE.write_text(
            json.dumps({
                "baseline_tokens": int(tokens),
                "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"[engine-core] could not save baseline: {e}")


_power_core_baseline_tokens = _load_power_core_baseline()


def compute_power_core(used_tokens: int) -> dict:
    """Returns the current engine-core readout AND fires the breach popup +
    the breach popup on the rising edge of the 10%-health crossing."""
    global _power_core_baseline_tokens, _power_core_was_above_thresh, _power_core_popup_pending

    # First-ever call snapshots a baseline against current usage.
    if _power_core_baseline_tokens <= 0:
        _power_core_baseline_tokens = max(1, int(used_tokens))
        _save_power_core_baseline(_power_core_baseline_tokens)

    baseline = _power_core_baseline_tokens
    excess = max(0, int(used_tokens) - baseline)
    health = max(0.0, min(100.0, 100.0 - 90.0 * (excess / baseline)))
    breach_threshold_tokens = baseline * 2

    breached = health <= 10.0
    if breached and _power_core_was_above_thresh:
        # Rising-edge crossing — fire the popup.
        _power_core_was_above_thresh = False
        _power_core_popup_pending = True
    if not breached:
        _power_core_was_above_thresh = True

    return {
        "baseline_tokens":  baseline,
        "current_tokens":   int(used_tokens),
        "breach_tokens":    breach_threshold_tokens,
        "health_pct":       round(health, 1),
        "popup_pending":    _power_core_popup_pending,
    }


def power_core_ack() -> None:
    """Client confirmed it showed the breach popup."""
    global _power_core_popup_pending
    _power_core_popup_pending = False


def power_core_reset(used_tokens: int) -> int:
    """Re-snapshot the baseline against current usage (e.g. after a
    memory compaction or skill purge)."""
    global _power_core_baseline_tokens, _power_core_was_above_thresh, _power_core_popup_pending
    _power_core_baseline_tokens = max(1, int(used_tokens))
    _power_core_was_above_thresh = True
    _power_core_popup_pending = False
    _save_power_core_baseline(_power_core_baseline_tokens)
    return _power_core_baseline_tokens


# ── Critical-threshold tracking ────────────────────
# Each metric: (trigger_value, recover_value). Hysteresis prevents
# bouncing — once a breach fires we need value to drop to recover
# before we'll fire again for the same metric.
_CRITICAL_THRESHOLDS = {
    # (trigger, recover) — for "high" direction, breach when value >= trigger
    # and recover when <= recover. For "low" direction, breach when value
    # <= trigger and recover when >= recover.
    "cpu_temp":    (95.0, 85.0),
    "gpu_temp":    (95.0, 85.0),
    "disk":        (95.0, 90.0),    # % used
    # ram and engine_load are deliberately omitted — both routinely sit near
    # 95-100% whenever DATA is working hard (voice models resident, LLM
    # inference, TTS). That is normal operation, not an emergency, so neither
    # is logged as critical. CPU/GPU temp, disk, and battery still do.
    "battery":     (7.0,  15.0),    # ≤7% on battery power
}
_CRITICAL_DIRECTIONS = {
    "battery": "low",   # everything else defaults to "high" (high = bad)
}
_critical_breach_state = {k: False for k in _CRITICAL_THRESHOLDS}


def _check_critical(metric: str, value, label: str, unit: str = "") -> str:
    """Returns a breach reason string if `value` just crossed into the
    critical zone for `metric`, otherwise ''. Manages hysteresis state.
    Direction (high/low = bad) is per-metric via _CRITICAL_DIRECTIONS."""
    if metric not in _CRITICAL_THRESHOLDS or value is None:
        return ""
    trigger, recover = _CRITICAL_THRESHOLDS[metric]
    direction = _CRITICAL_DIRECTIONS.get(metric, "high")
    in_breach = _critical_breach_state[metric]
    if direction == "low":
        crossing  = value <= trigger
        recovered = value >= recover
        cmp_text  = f"<= {trigger:.0f}{unit}"
    else:
        crossing  = value >= trigger
        recovered = value <= recover
        cmp_text  = f">= {trigger:.0f}{unit}"
    if not in_breach and crossing:
        _critical_breach_state[metric] = True
        return f"{label} {value:.0f}{unit} {cmp_text}"
    if in_breach and recovered:
        _critical_breach_state[metric] = False
    return ""


def build_ships_health() -> dict:
    """Sample everything fresh. Cheap enough to call ~2x/sec.
    SSE-streamed by /vitals_fast — the MSD modal pulls heavier one-shot
    metadata (LLM provider, recent tools, etc.) from /msd separately."""
    # ── Raw system metrics ────────────────────────────────────
    cpu_pct = psutil.cpu_percent(interval=None) if psutil else 0.0
    if psutil:
        vm = psutil.virtual_memory()
        ram_pct      = vm.percent
        ram_used_gb  = round(vm.used  / (1024**3), 2)
        ram_total_gb = round(vm.total / (1024**3), 2)
    else:
        ram_pct = 0.0; ram_used_gb = 0.0; ram_total_gb = 0.0

    g = _gpu_stats()
    gpu_pct          = g["util"]
    gpu_mem_used_mb  = g["mem_used_mb"]
    gpu_mem_total_mb = g["mem_total_mb"]
    gpu_temp_c       = g.get("temp_c", 0)
    cpu_temp_c       = _cpu_temp()

    if psutil:
        try:
            du = psutil.disk_usage(str(Path(__file__).resolve().anchor or "/"))
            disk_used_pct = du.percent
            disk_free_gb  = round(du.free  / (1024**3), 1)
            disk_total_gb = round(du.total / (1024**3), 1)
        except Exception:
            disk_used_pct = 0.0; disk_free_gb = 0.0; disk_total_gb = 0.0
    else:
        disk_used_pct = 0.0; disk_free_gb = 0.0; disk_total_gb = 0.0

    net_sent_bps, net_recv_bps = _net_bps()
    tok_per_sec = _tokens_per_sec()

    # Two speed gauges:
    #   • Impulse (sub-light) → CPU activity, shown as % of max capacity
    #   • Engine (FTL)         → pure GPU utilization, scaled to engine factor 0.0–9.0
    # Note: gpu_pct is 0 on systems without an NVIDIA GPU (nvidia-smi absent),
    # so engine will sit at 0.0 in that case.
    impulse = round(min(100.0, cpu_pct), 0)
    # Synthetic "engine load" 0-100% — replaces GPU as the engine-driving
    # signal on boxes without an NVIDIA GPU. Blends:
    #   • CPU activity (half weight — full weight is already Impulse Engines)
    #   • RAM pressure above an idle baseline
    #   • LLM in-flight streams (each adds a hefty kick)
    #   • Token velocity (so generation visibly spins the gauge)
    _ram_pressure = max(0.0, ram_pct - 50.0)            # 0..50 above idle
    _llm_kick     = min(30.0, _llm_inflight * 25.0)     # 0..30
    _tps_kick     = min(20.0, tok_per_sec * 1.0)        # 0..20
    engine_load = min(100.0, cpu_pct * 0.5 + _ram_pressure + _llm_kick + _tps_kick)
    engine = round(min(9.0, (engine_load / 100.0) * 9.0), 1)

    # ── Subsystem health (0..100, higher = better) ────────────
    if _tool_results_window:
        hull = round(100.0 * sum(1 for _, ok, _ in _tool_results_window if ok)
                     / len(_tool_results_window))
    else:
        hull = 100
    # Shields — rolling LLM API success rate (last 20 chat-stream calls).
    # Drops when the model errors / rate-limits / network fails. Defaults to
    # 100 with an empty window (no calls yet).
    # Shield strength is RAM-derived: shields hold full until memory pressure
    # climbs past ~85%, then drain, hitting zero ("shields down") when RAM is
    # maxed. Repurposed from the old LLM-success-rate metric so the Ship's
    # Vitals shield reading reflects something the Captain can feel.
    shield = round(max(0.0, min(100.0, (100.0 - ram_pct) * (100.0 / 15.0))))
    shields_down = shield <= 0
    sif    = round(min(100.0, (100.0 - disk_used_pct) * 5.0))
    damp   = round(max(0.0, 100.0 - (_event_loop_lag_ms / 5.0)))

    # ── Battery (laptops) ───────────────────────────────────
    battery_pct: float | None = None
    battery_plugged: bool | None = None
    if psutil:
        try:
            batt = psutil.sensors_battery()
            if batt is not None:
                battery_pct = round(float(batt.percent), 1)
                battery_plugged = bool(batt.power_plugged)
        except Exception:
            pass

    # ── Critical-threshold logging ──────────────────────────
    # Each check returns a reason string only on the rising-edge tick.
    critical_reasons = []
    for reason in (
        _check_critical("cpu_temp",    cpu_temp_c, "CPU temp",    "°C"),
        _check_critical("gpu_temp",    gpu_temp_c if gpu_temp_c else None, "GPU temp", "°C"),
        _check_critical("disk",        disk_used_pct, "Disk",     "%"),
        # ram + engine_load intentionally NOT checked — both sit near capacity
        # under normal heavy load. The other thresholds
        # (CPU/GPU temp, disk, battery) still apply.
        # Only fire battery alert when ON battery power. If the charger is
        # connected, the captain knows and an alert is just noise. The
        # `None` value when plugged-in short-circuits _check_critical.
        _check_critical(
            "battery",
            battery_pct if (battery_pct is not None and not battery_plugged) else None,
            "Battery", "%",
        ),
    ):
        if reason:
            critical_reasons.append(reason)
    if critical_reasons:
        log.warning(f"[critical] threshold crossed: {'; '.join(critical_reasons)}")

    # Shield is excluded from `worst` — it is RAM-derived and would otherwise
    # peg the ship red whenever memory sits near capacity (normal on this
    # box). Instead, a full shield collapse (RAM maxed) reds the ship itself.
    worst = min(hull, sif, damp)
    if   shields_down:              alert = "red"
    elif worst < 60:                alert = "red"
    elif worst < 90 or shield < 35: alert = "yellow"
    else:                           alert = "green"

    return {
        # Real-time gauges (sparkline-friendly)
        "cpu":   round(cpu_pct, 1),
        "ram":   round(ram_pct, 1),
        "ram_used_gb":      ram_used_gb,
        "ram_total_gb":     ram_total_gb,
        "gpu":              gpu_pct,
        "gpu_mem_used_mb":  gpu_mem_used_mb,
        "gpu_mem_total_mb": gpu_mem_total_mb,
        "gpu_temp_c":       gpu_temp_c,
        "cpu_temp_c":       cpu_temp_c,
        "disk_used_pct":    round(disk_used_pct, 1),
        "disk_free_gb":     disk_free_gb,
        "disk_total_gb":    disk_total_gb,
        "net_in_bps":       net_recv_bps,
        "net_out_bps":      net_sent_bps,
        "tps":              round(tok_per_sec, 1),
        "engine":             engine,
        "impulse":          impulse,
        "engine_load":      round(engine_load, 1),
        "battery_pct":      battery_pct,           # None on desktops
        "battery_plugged":  battery_plugged,
        "llm_inflight":     _llm_inflight,

        # Subsystem health bars (legacy widget still uses these)
        "hull":   hull,
        "shield": shield,
        "shields_down": shields_down,
        "sif":    sif,
        "damp":   damp,
        "alert":  alert,
        "tunnel": _tunnel_healthy(),
    }


def build_msd() -> dict:
    """Heavier one-shot snapshot for the MSD modal. Polled ~every 2s
    while the modal is open (not in the 500ms SSE)."""
    now = time.time()
    active_cfg = PROVIDERS.get(ACTIVE_PROVIDER, {}) if 'PROVIDERS' in globals() else {}

    # Memory & context budget — reuse existing helpers where possible
    try:
        mem_stats = build_memory_stats()
    except Exception:
        mem_stats = {}

    memory_bytes = COMPUTER_MEMORY_FILE.stat().st_size if COMPUTER_MEMORY_FILE.exists() else 0

    # Recent tools (last 10, newest first)
    recent_tools = []
    for name, ok, ts in list(_tool_results_window)[-10:][::-1]:
        recent_tools.append({
            "name": name,
            "ok":   ok,
            "ago":  int(now - ts),
        })

    # Standing orders count
    try:
        so_path = Path(__file__).parent.parent / "standing_orders.json"
        so_count = len(json.loads(so_path.read_text(encoding="utf-8"))) if so_path.exists() else 0
    except Exception:
        so_count = 0

    # Voice subsystem snapshot — best-effort, all keys optional
    voice = {}
    try:
        voice["tts"]  = getattr(local_voice, "active_engine", None) or "F5-TTS"
        voice["stt"]  = "Whisper"
        voice["wake"] = "Computer"
    except Exception:
        pass

    uptime_secs = int(now - _bridge_start_time.timestamp()) if hasattr(_bridge_start_time, "timestamp") \
                  else int((datetime.datetime.now() - _bridge_start_time).total_seconds())

    last_activity_secs = int(now - _last_user_activity)

    return {
        "llm": {
            "provider_id":    ACTIVE_PROVIDER,
            "provider_label": active_cfg.get("label", ACTIVE_PROVIDER),
            "model":          active_cfg.get("model", MODEL),
            "inflight":       _llm_inflight,
            "mode":           BRIDGE_MODE,
        },
        "memory": {
            "data_memory_kb":  round(memory_bytes / 1024, 1),
            "memory_tokens":   mem_stats.get("memory_tokens", 0),
            "system_tokens":   mem_stats.get("system_tokens", 0),
            "history_tokens":  mem_stats.get("history_tokens", 0),
            "history_turns":   mem_stats.get("history_turns", 0),
            "max_history_turns": mem_stats.get("max_history_turns", MAX_HISTORY),
            "used_tokens":     mem_stats.get("used_tokens", 0),
            "ceiling_tokens":  mem_stats.get("ceiling_tokens", 0),
            "used_pct":        mem_stats.get("used_pct", 0),
        },
        "power_core": compute_power_core(mem_stats.get("used_tokens", 0)),
        "subsystems": {
            "voice":            voice,
            "standing_orders":  so_count,
            "api_tools":        len(TOOLS),
            "tunnel_url":       (Path(__file__).parent / "tunnel_url.txt").read_text(encoding="utf-8").strip()
                                if (Path(__file__).parent / "tunnel_url.txt").exists() else "",
        },
        "recent_tools": recent_tools,
        "uptime_secs":  uptime_secs,
        "last_user_activity_secs": last_activity_secs,
    }


# ── Context budget: how much of the model's window is being used ──
# Per-model approximate context windows (input tokens). Used to compute the
# remaining headroom shown in the CONTEXT BUDGET sidebar panel.
_MODEL_CONTEXT_WINDOWS = {
    "claude-fable-5":          200_000,  # 1M-capable; capped to 200K like siblings for the sidebar
    "claude-opus-4-8":         200_000,
    "claude-opus-4-7":         200_000,  # legacy — kept so older history doesn't NaN the sidebar
    "claude-sonnet-4-6":       200_000,
    "claude-haiku-4-5":        200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "gpt-5":                   400_000,
    "gemini-2.5-pro":          1_000_000,
    "qwen2.5-coder:7b":         32_768,
    "qwen2.5:3b":               32_768,
}


def _est_tokens(s: str) -> int:
    """Cheap token estimate: ~4 chars/token for English-ish text."""
    return (len(s) + 3) // 4 if s else 0


def build_memory_stats() -> dict:
    """Token-level breakdown of what's currently in the system prompt + history,
    plus headroom remaining against the active model's context window."""
    soul_path   = HERMES_DIR / "SOUL.md"
    soul_text   = soul_path.read_text(encoding="utf-8", errors="replace") if soul_path.exists() else ""
    mem_text    = COMPUTER_MEMORY_FILE.read_text(encoding="utf-8", errors="replace") if COMPUTER_MEMORY_FILE.exists() else ""

    # The full assembled system prompt as the bridge would send it this turn.
    try:
        full_soul = _load_soul(mode="api")
    except Exception:
        full_soul = soul_text + "\n\n" + mem_text  # fallback estimate

    # Tokens that hit the wire for the last-20-turn rolling window
    history_chars = 0
    for m in conversation_history[-MAX_HISTORY:]:
        c = m.get("content", "")
        if isinstance(c, str):
            history_chars += len(c)
        elif isinstance(c, list):  # tool-use / multi-block content
            for block in c:
                if isinstance(block, dict):
                    history_chars += len(json.dumps(block))

    soul_tokens    = _est_tokens(soul_text)
    memory_tokens  = _est_tokens(mem_text)
    system_tokens  = _est_tokens(full_soul)
    history_tokens = (history_chars + 3) // 4

    # Extras the system prompt picks up beyond raw SOUL+memory (skills manifest,
    # runtime config, marker docs, etc.) — useful to surface separately.
    extras_tokens  = max(0, system_tokens - soul_tokens - memory_tokens)

    used_tokens    = system_tokens + history_tokens
    active_cfg     = PROVIDERS.get(ACTIVE_PROVIDER, {})
    model_id       = active_cfg.get("model", MODEL)
    ceiling        = _MODEL_CONTEXT_WINDOWS.get(model_id, 200_000)
    reply_budget   = MAX_TOKENS
    headroom       = max(0, ceiling - used_tokens - reply_budget)
    used_pct       = round(min(100, (used_tokens / ceiling) * 100), 1) if ceiling else 0

    history_path = HISTORY_FILE if "HISTORY_FILE" in globals() else None
    history_disk_bytes = history_path.stat().st_size if (history_path and history_path.exists()) else 0

    return {
        "model":             model_id,
        "ceiling_tokens":    ceiling,
        "reply_budget":      reply_budget,
        "system_tokens":     system_tokens,
        "soul_tokens":       soul_tokens,
        "memory_tokens":     memory_tokens,
        "extras_tokens":     extras_tokens,
        "history_tokens":    history_tokens,
        "history_turns":     len(conversation_history),
        "max_history_turns": MAX_HISTORY,
        "used_tokens":       used_tokens,
        "used_pct":          used_pct,
        "headroom_tokens":   headroom,
        "memory_bytes":      COMPUTER_MEMORY_FILE.stat().st_size if COMPUTER_MEMORY_FILE.exists() else 0,
        "soul_bytes":        soul_path.stat().st_size if soul_path.exists() else 0,
        "history_disk_bytes": history_disk_bytes,
    }


# ── Memory compaction ──────────────────────────────────────
# Auto-rewrite of COMPUTER_MEMORY.md when it gets bloated. Ships redundant entries,
# merges related items, drops one-off scratchpad notes that no longer matter.
# Triggered automatically when memory crosses MEMORY_COMPACT_THRESHOLD tokens,
# rate-limited by MEMORY_COMPACT_COOLDOWN to prevent thrashing on the wire.

MEMORY_COMPACT_THRESHOLD = 5000   # tokens; auto-fire above this (lowered from 8000 — keeps system prompt leaner between compactions)
MEMORY_COMPACT_COOLDOWN  = 6 * 3600  # 6 hours between auto-runs
_last_memory_compact = 0.0          # unix epoch of last successful compaction
_memory_compact_lock = threading.Lock()


def _compact_memory_file() -> dict:
    """Ask Claude to rewrite COMPUTER_MEMORY.md into a tighter version.
    Prefers the Claude CLI (subscription, free per request) over the API.
    Returns {ok, before_tokens, after_tokens, saved_tokens, backup_path}."""
    if not COMPUTER_MEMORY_FILE.exists():
        return {"ok": False, "error": "COMPUTER_MEMORY.md does not exist"}

    original = COMPUTER_MEMORY_FILE.read_text(encoding="utf-8", errors="replace")
    before_tokens = _est_tokens(original)

    instructions = (
        "Rewrite the following Computer memory file MORE TIGHTLY "
        "without losing meaning. Merge duplicates, drop stale scratchpad "
        "notes, collapse verbose entries into single sentences. Keep section "
        "headings. Preserve every long-lived fact, preference, or "
        "instruction.\n\n"
        "CRITICAL OUTPUT RULES — read carefully, failure here destroys "
        "the Captain's memory:\n"
        "1. Output ONLY the rewritten markdown file content. The very FIRST "
        "character of your response must be the first character of the new "
        "file (e.g. `#` or `###`). DO NOT begin with prose like 'Done.', "
        "'Here is', 'I have rewritten', 'Key changes', or any meta-commentary.\n"
        "2. DO NOT include a changelog, summary of changes, or description of "
        "what you removed/merged. The Captain reads the file directly — any "
        "preamble overwrites real memory.\n"
        "3. DO NOT wrap the output in code fences (```markdown ... ```).\n"
        "4. The output MUST preserve every `### [` heading and every standing "
        "order. If you cannot fit everything within the token budget, return "
        "the original file UNCHANGED rather than truncating.\n"
        "5. The output should be roughly 60-95% of the original size. A "
        "drastic cut (under 50%) means you have lost real information and "
        "will be rejected.\n"
        "6. VERBATIM PRESERVATION — the following content MUST be reproduced "
        "CHARACTER-FOR-CHARACTER. You may NOT paraphrase, polish, summarize, "
        "translate, correct grammar, fix spelling, modernize phrasing, or "
        "alter punctuation/capitalization inside these. You may only trim "
        "the surrounding meta-prose AROUND them.\n"
        "   a. Anything inside a markdown blockquote (lines starting with "
        "`>`) — these are direct quotes from the Captain or a teacher.\n"
        "   b. Anything wrapped in *italics with an asterisk* inside a "
        "section labeled 'voice', 'voice primer', 'speaker voice', "
        "'teaching', 'quotes', 'verbatim', 'primer', or 'monologue'.\n"
        "   c. Anything inside backticks (`like this`) — proper nouns, "
        "filenames, IDs, mantras, command strings.\n"
        "   d. Anything the Captain has explicitly marked as 'verbatim', "
        "'do not edit', 'preserve voice', 'do not paraphrase', 'his words', "
        "'her words', 'my words exactly', or similar.\n"
        "   e. Mantras, sacred phrases, and lineage terms in Sanskrit, "
        "Hebrew, Telugu, Latin, or any non-English script — even when not "
        "explicitly quoted.\n"
        "   f. Any section whose heading contains the words 'voice', "
        "'primer', 'monologue', 'quotes', 'verbatim', 'soul', 'mission "
        "statement', 'mantras', 'lineage', or 'teaching' must have its "
        "body content preserved verbatim. You may only trim purely "
        "structural prose (e.g. table-of-contents lines, redundant "
        "section dividers).\n"
        "   If you are uncertain whether a passage is verbatim, default to "
        "preserving it character-for-character. The cost of keeping too "
        "much voice is zero; the cost of paraphrasing the Captain's words "
        "or a teacher's words is catastrophic.\n\n"
        "--- ORIGINAL BELOW ---\n\n"
        f"{original}"
    )

    log.info(f"[COMPACT] starting — before={before_tokens} tok")

    new_text = ""
    error_details = ""

    # ── 1st choice: Claude CLI (subscription, no per-token cost) ─────
    # ANTHROPIC_API_KEY MUST be stripped from the environment: with it set the
    # claude CLI bills the metered API (which has no credit → exit 1); without
    # it the CLI uses the subscription OAuth token. This is the same trick the
    # chat CLI runners (ask_hermes_cli / ask_hermes_cli_stream) rely on.
    cli = _provider_executable("claude-cli") or "claude"
    try:
        cli_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # Opus over Sonnet here: compaction is rare (a few times/month) but the
        # cost of a bad rewrite is catastrophic (lost persistent memory). Opus
        # is materially better at multi-rule instruction following and verbatim
        # preservation. Pennies per run beats losing years of context.
        proc = subprocess.run(
            [cli, "--print", "--output-format", "text",
             "--model", "claude-opus-4-8",
             "--dangerously-skip-permissions"],
            input=instructions, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=600, env=cli_env,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            new_text = proc.stdout.strip()
            log.info(f"[COMPACT] CLI returned {len(new_text)} chars")
        else:
            error_details = (f"CLI exit={proc.returncode} "
                             f"stderr={proc.stderr[:200]!r} stdout={proc.stdout[:160]!r}")
            log.warning(f"[COMPACT] CLI fell short: {error_details}")
    except Exception as e:
        error_details = f"CLI exception: {e}"
        log.warning(f"[COMPACT] {error_details}")

    # API fallback removed by Captain order (2026-05-30) — no pay-per-token
    # path is allowed. If the subscription CLI fails, compaction returns an
    # error and the Captain re-runs after fixing the CLI.
    if not new_text:
        return {"ok": False, "error": f"CLI compaction failed ({error_details}). API fallback is disabled by Captain order — fix the CLI and retry."}

    # Defensive: strip code fences the LLM sometimes wraps even when told not to.
    for prefix in ("```markdown\n", "```md\n", "```\n"):
        if new_text.startswith(prefix):
            new_text = new_text[len(prefix):]
            if new_text.rstrip().endswith("```"):
                new_text = new_text.rstrip()[:-3].rstrip()
            break

    # Defensive: detect and refuse meta-preamble bleed. The 2026-05-27 incident:
    # the LLM emitted "Done. The file is rewritten. Key changes made: ..." and
    # then ONLY the trailing two sections, dropping ~70% of the file. Any of
    # these phrases in the first 800 chars means the LLM is talking about the
    # file instead of being the file — refuse and keep the original.
    preamble_signatures = (
        "done. the file",
        "here is the rewritten",
        "here's the rewritten",
        "i have rewritten",
        "i've rewritten",
        "key changes made",
        "key changes:",
        "summary of changes",
        "changes made:",
        "the file is rewritten",
        "rewritten version",
    )
    head_lower = new_text[:800].lower()
    for sig in preamble_signatures:
        if sig in head_lower:
            log.warning(f"[COMPACT] preamble signature {sig!r} detected — refusing to write")
            return {"ok": False, "error": f"compaction emitted meta-preamble ({sig!r}); refusing to overwrite"}

    # Defensive: the rewritten file MUST start with a markdown heading. Real
    # content in this file always opens with `### [` or `# `. Anything else
    # (prose, bullet list, paragraph) means the LLM lost the plot.
    first_line = next((ln.strip() for ln in new_text.splitlines() if ln.strip()), "")
    if not (first_line.startswith("### [") or first_line.startswith("## ") or first_line.startswith("# ")):
        log.warning(f"[COMPACT] first non-blank line is not a heading: {first_line[:120]!r} — refusing to write")
        return {"ok": False, "error": "compaction output does not start with a heading"}

    if not new_text or len(new_text) < 100:
        return {"ok": False, "error": "compaction returned suspiciously empty content"}

    # Safety: never let compaction inflate the file. If it grew, refuse.
    after_tokens = _est_tokens(new_text)
    if after_tokens >= before_tokens:
        log.warning(f"[COMPACT] LLM returned a LARGER file ({after_tokens} >= {before_tokens}) — refusing to write")
        return {"ok": False, "error": "compaction did not reduce size",
                "before_tokens": before_tokens, "after_tokens": after_tokens}

    # Safety: refuse catastrophic shrinkage. A healthy compaction trims 10-40%.
    # Anything below 50% of the original means the LLM dropped real content —
    # this is what happened on 2026-05-27 (8 KB out of 25 KB = 32%, lost the
    # entire Captain identity / projects / calendar / bridge crew sections).
    MIN_RETAINED_RATIO = 0.50
    ratio = after_tokens / max(1, before_tokens)
    if ratio < MIN_RETAINED_RATIO:
        log.warning(f"[COMPACT] catastrophic shrinkage: {after_tokens}/{before_tokens} = {ratio:.0%} (floor {MIN_RETAINED_RATIO:.0%}) — refusing to write")
        return {"ok": False, "error": f"compaction shrank too aggressively ({ratio:.0%} of original); refusing — file likely lost real content",
                "before_tokens": before_tokens, "after_tokens": after_tokens}

    # Safety: ensure section-heading count did not collapse. If the rewrite
    # dropped more than half the `### [` section headings, real categories of
    # memory were lost.
    orig_headings = original.count("\n### [") + (1 if original.startswith("### [") else 0)
    new_headings  = new_text.count("\n### [") + (1 if new_text.startswith("### [") else 0)
    if orig_headings >= 6 and new_headings < orig_headings * 0.5:
        log.warning(f"[COMPACT] heading collapse: {new_headings}/{orig_headings} `### [` sections — refusing to write")
        return {"ok": False, "error": f"compaction lost too many sections ({new_headings}/{orig_headings} retained); refusing"}

    # Safety: verbatim blockquote preservation. Lines starting with `>` are
    # direct quotes (Captain's Divine Mission, Swami Kaleshwara teachings,
    # etc.) and MUST survive compaction character-for-character. If the
    # rewrite dropped more than 10% of the blockquote line count, the LLM
    # paraphrased what it was told to preserve verbatim.
    def _quote_lines(text: str) -> list[str]:
        return [ln.rstrip() for ln in text.splitlines() if ln.lstrip().startswith(">")]
    orig_quotes = _quote_lines(original)
    new_quotes  = _quote_lines(new_text)
    if orig_quotes:
        retention = len(new_quotes) / len(orig_quotes)
        if retention < 0.90:
            log.warning(f"[COMPACT] verbatim blockquote loss: {len(new_quotes)}/{len(orig_quotes)} lines ({retention:.0%}) — refusing")
            return {"ok": False,
                    "error": f"compaction dropped verbatim blockquote content ({len(new_quotes)}/{len(orig_quotes)} `>` lines retained); refusing"}
        # Also check that each surviving quote line appears verbatim — the
        # LLM may keep the count but paraphrase the contents. Sample-check
        # by requiring at least 80% of the original quote lines to appear
        # byte-identical somewhere in the new file.
        orig_set = set(orig_quotes)
        new_set  = set(new_quotes)
        identical = len(orig_set & new_set)
        if identical < len(orig_set) * 0.80:
            log.warning(f"[COMPACT] verbatim blockquote text mutated: {identical}/{len(orig_set)} unique lines matched — refusing")
            return {"ok": False,
                    "error": f"compaction paraphrased verbatim blockquote content ({identical}/{len(orig_set)} unique `>` lines unchanged); refusing"}

    # Snapshot before overwriting so the Captain can roll back if needed.
    backup_dir = PROJECT_DIR / "Backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = backup_dir / f"COMPUTER_MEMORY_{stamp}.md.bak"
    backup_path.write_text(original, encoding="utf-8")
    COMPUTER_MEMORY_FILE.write_text(new_text, encoding="utf-8")

    global _last_memory_compact
    _last_memory_compact = time.time()

    log.info(f"[COMPACT] {before_tokens} → {after_tokens} tok  (saved {before_tokens - after_tokens}); backup={backup_path.name}")
    return {
        "ok":              True,
        "before_tokens":   before_tokens,
        "after_tokens":    after_tokens,
        "saved_tokens":    before_tokens - after_tokens,
        "backup_path":     str(backup_path),
    }


def _maybe_auto_compact_memory() -> None:
    """Fire-and-forget auto-compaction when memory gets bloated. Called from
    chat handlers after each turn. Cheap fast-path when below threshold."""
    try:
        if not COMPUTER_MEMORY_FILE.exists():
            return
        size = COMPUTER_MEMORY_FILE.stat().st_size
        # 4 chars/token, so 8000 tokens ≈ 32000 bytes — fast pre-check w/o re-reading
        if size < MEMORY_COMPACT_THRESHOLD * 4:
            return
        if time.time() - _last_memory_compact < MEMORY_COMPACT_COOLDOWN:
            return
        if not _memory_compact_lock.acquire(blocking=False):
            return  # another compaction already in flight
        def _bg():
            try:
                result = _compact_memory_file()
                if result.get("ok"):
                    log.info(f"[COMPACT-AUTO] saved {result['saved_tokens']} tok")
                else:
                    log.warning(f"[COMPACT-AUTO] skipped: {result.get('error')}")
            finally:
                _memory_compact_lock.release()
        threading.Thread(target=_bg, daemon=True).start()
    except Exception as e:
        log.exception(f"[COMPACT-AUTO] guard failed: {e}")


# ── Neural brain graph ────────────────────────────────
def build_neural_graph() -> dict:
    """Build a semantic graph of Data's actual brain files for the neural visualiser."""
    nodes, links = [], []

    def node(id, label, type_, r, hub=False, path="", extra=None):
        n = {"id": id, "label": label, "type": type_, "r": r, "hub": hub}
        if path: n["path"] = path
        if extra: n.update(extra)
        nodes.append(n)

    def link(src, tgt, w=1.0):
        links.append({"source": src, "target": tgt, "w": w})

    # ── Core ─────────────────────────────────────────────
    node("core", "NEURAL CORE", "core", 24, hub=True)

    # ── Identity ─────────────────────────────────────────
    node("hub-identity", "IDENTITY", "core", 16, hub=True)
    link("core", "hub-identity", 2.5)
    soul = HERMES_DIR / "SOUL.md"
    if soul.exists():
        node("soul-md", "Data — SOUL.md", "memory", 12, path=str(soul),
             extra={"size": soul.stat().st_size})
        link("hub-identity", "soul-md", 1.8)
    # The main-channel computer soul — the identity driving this very chat.
    soul_computer = HERMES_DIR / "SOUL_COMPUTER.md"
    if soul_computer.exists():
        node("soul-computer-md", "SOUL_COMPUTER.md", "memory", 11,
             path=str(soul_computer), extra={"size": soul_computer.stat().st_size})
        link("hub-identity", "soul-computer-md", 1.8)

    # ── Memory Banks ─────────────────────────────────────
    node("hub-memory", "MEMORY BANKS", "memory", 16, hub=True)
    link("core", "hub-memory", 2.5)
    mem_files = [
        ("data-memory",   "COMPUTER_MEMORY.md",         COMPUTER_MEMORY_FILE),
        ("hermes-mem",    "MEMORY.md",                 HERMES_DIR / "memories" / "MEMORY.md"),
        ("user-profile",  "USER.md",                   HERMES_DIR / "memories" / "USER.md"),
        ("convo-log",     "conversation_history.json", HISTORY_FILE),
        # Permanent archive of every turn ever heard (one JSON object per line).
        # Source of truth for the searchable recall index.
        ("convo-archive", "conversation_archive.jsonl", CONVERSATION_ARCHIVE_FILE),
        # The searchable index itself — keyword (FTS5) + semantic (Ollama
        # embeddings). Search hits cross conversations, memory, briefings, orders.
        ("recall-index",  "recall_index.db",            RECALL_INDEX_DB),
    ]
    # Surface live row count on the recall-index node so the captain can see
    # at a glance how much Data has indexed.
    recall_extra = {}
    if RECALL_INDEX_DB.exists():
        try:
            import sqlite3 as _sql3
            _c = _sql3.connect(str(RECALL_INDEX_DB))
            recall_extra["row_count"] = _c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            recall_extra["by_source"] = {
                src: cnt for src, cnt in _c.execute(
                    "SELECT source, COUNT(*) FROM items GROUP BY source")
            }
            _c.close()
        except Exception:
            pass
    # Archive line count is a useful "how many turns have we ever logged" stat.
    archive_extra = {}
    if CONVERSATION_ARCHIVE_FILE.exists():
        try:
            with open(CONVERSATION_ARCHIVE_FILE, "r", encoding="utf-8", errors="replace") as _af:
                archive_extra["turn_count"] = sum(1 for _ in _af)
        except Exception:
            pass
    for nid, label, p in mem_files:
        if p.exists():
            ftype = "memory" if label.endswith(".md") else "system"
            extra = {"size": p.stat().st_size,
                     "modified": datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
            if nid == "recall-index": extra.update(recall_extra)
            if nid == "convo-archive": extra.update(archive_extra)
            node(nid, label, ftype, 10, path=str(p), extra=extra)
            link("hub-memory", nid, 1.5)

    # ── Skill Matrix ─────────────────────────────────────
    # Two library folders feed the matrix:
    #   • Hermes skills      → ~/AppData/Local/hermes/skills/   (category-grouped)
    #   • Claude Code skills → ~/.claude/skills/                (flat)
    # Each library is its own hub node carrying the folder path so the Captain
    # can click straight through to the directory on disk.
    node("hub-skills", "SKILL MATRIX", "skill", 16, hub=True)
    link("core", "hub-skills", 2.5)

    # Hermes skills library (category-grouped)
    if SKILLS_DIR.exists():
        hermes_cat_dirs = [d for d in sorted(SKILLS_DIR.iterdir())
                           if d.is_dir() and not d.name.startswith(".")
                           and any(s.is_dir() for s in d.iterdir())]
        hermes_skill_total = sum(
            sum(1 for s in c.iterdir() if s.is_dir()) for c in hermes_cat_dirs
        )
        node("lib-hermes-skills", "HERMES SKILLS", "skill", 13, hub=True,
             path=str(SKILLS_DIR),
             extra={"skill_count": hermes_skill_total,
                    "category_count": len(hermes_cat_dirs),
                    "library": "hermes"})
        link("hub-skills", "lib-hermes-skills", 1.6)
        for cat_dir in hermes_cat_dirs:
            skills = [s for s in cat_dir.iterdir() if s.is_dir()]
            cat_id = f"cat-{cat_dir.name}"
            node(cat_id, cat_dir.name.replace("-", " ").upper(), "skill", 9,
                 hub=True, path=str(cat_dir),
                 extra={"skill_count": len(skills), "category": cat_dir.name})
            link("lib-hermes-skills", cat_id, 1.1)

    # Claude Code skills library (flat layout — SKILL.md uppercase).
    # Each skill folder is expanded as its own node under the library hub so the
    # graph reflects the full inventory, matching how Hermes categories fan out.
    if CLAUDE_SKILLS_DIR.exists():
        cc_skills = sorted(
            (d for d in CLAUDE_SKILLS_DIR.iterdir()
             if d.is_dir() and (d / "SKILL.md").exists()),
            key=lambda d: d.name.lower(),
        )
        if cc_skills:
            node("lib-claude-skills", "CLAUDE CODE SKILLS", "skill", 13, hub=True,
                 path=str(CLAUDE_SKILLS_DIR),
                 extra={"skill_count": len(cc_skills),
                        "library": "claude-code"})
            link("hub-skills", "lib-claude-skills", 1.6)
            for skill_dir in cc_skills:
                sid = f"cc-skill-{skill_dir.name}"
                node(sid, skill_dir.name.replace("-", " ").upper(), "skill", 6,
                     path=str(skill_dir),
                     extra={"category": "claude-code", "skill": skill_dir.name})
                link("lib-claude-skills", sid, 0.9)

    # ── Operational Core ─────────────────────────────────
    node("hub-ops", "OPERATIONAL", "system", 14, hub=True)
    link("core", "hub-ops", 2.0)
    op_files = [
        ("bridge-py",  "bridge_server.py", Path(__file__).resolve()),
        ("app-js",     "app.js",           Path(__file__).parent / "app.js"),
        ("index-html", "index.html",       Path(__file__).parent / "index.html"),
    ]
    for nid, label, p in op_files:
        if p.exists():
            node(nid, label, "skill", 9, path=str(p),
                 extra={"size": p.stat().st_size})
            link("hub-ops", nid, 1.3)

    # ── Agent Crew ──────────────────────────────────────
    # Subagent identity files at ~/.claude/agents/. Each .md is both a
    # spawnable sub-agent (subagent_type) and a dispatchable specialist the
    # Captain manages from the matrix — the node carries the path so a click
    # opens the dossier on disk. Nodes appear only for files that exist.
    node("hub-crew", "AGENT CREW", "crew", 16, hub=True)
    link("core", "hub-crew", 2.5)
    crew_dir = Path.home() / ".claude" / "agents"
    crew_files = [
        ("crew-atlas",    "atlas.md",    "ARCHITECT — SPEC, PLAN & STRATEGY"),
        ("crew-forge",    "forge.md",    "BUILDER — IMPLEMENT & ORCHESTRATE"),
        ("crew-vector",   "vector.md",   "REVIEWER — CODE REVIEW"),
        ("crew-sentinel", "sentinel.md", "SECURITY — THREATS & HARDENING"),
        ("crew-probe",    "probe.md",    "ENGINEER — TEST, DEBUG & PERFORMANCE"),
        ("crew-relay",    "relay.md",    "OPERATIONS — DEVOPS & INFRASTRUCTURE"),
        ("crew-echo",     "echo.md",     "COUNSELOR — REFLECTION"),
        ("crew-pulse",    "pulse.md",    "COACH — HEALTH & WELLNESS"),
        ("crew-sage",     "sage.md",     "ADVISOR — SECOND OPINION & THE LONG VIEW"),
        ("crew-scout",    "scout.md",    "DRAFTER — CONTENT & QUICK BUILDS"),
    ]
    for nid, fname, role in crew_files:
        p = crew_dir / fname
        if p.exists():
            node(nid, fname, "crew", 11, path=str(p),
                 extra={"size": p.stat().st_size,
                        "modified": datetime.datetime.fromtimestamp(
                            p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "role": role})
            link("hub-crew", nid, 1.6)

    # ── Cross-links for visual richness ──────────────────
    # Only link nodes that actually exist — on a fresh install SOUL.md /
    # COMPUTER_MEMORY.md may be absent, and a dangling link id crashes the
    # frontend's d3.forceLink (everything then piles up in the center).
    _ids = {n["id"] for n in nodes}
    if "soul-md" in _ids and "hub-ops" in _ids:
        links.append({"source": "soul-md",    "target": "hub-ops",    "w": 0.4})
    if "data-memory" in _ids and "hub-identity" in _ids:
        links.append({"source": "data-memory", "target": "hub-identity", "w": 0.4})
    if "hub-crew" in _ids and "hub-skills" in _ids:
        # The crew are dispatched as skills/slash-commands.
        links.append({"source": "hub-crew", "target": "hub-skills", "w": 0.4})
    # Belt-and-suspenders: drop any other dangling link
    links = [l for l in links if l["source"] in _ids and l["target"] in _ids]

    return {"nodes": nodes, "links": links}


def build_skills_full() -> dict:
    """Return all Hermes skill categories and their skills, plus a synthetic
    'claude-code' category for the flat Claude Code skill folder."""
    categories = []
    if SKILLS_DIR.exists():
        for cat_dir in sorted(SKILLS_DIR.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            skills = []
            for skill_dir in sorted(cat_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_path = skill_dir / "skill.md"
                desc = ""
                if skill_path.exists():
                    for line in skill_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.startswith("description:"):
                            desc = line.partition(":")[2].strip().strip('"')
                            break
                skills.append({
                    "name":          skill_dir.name,
                    "display":       skill_dir.name.replace("-", " ").title(),
                    "description":   desc,
                    "has_skill_md":  skill_path.exists(),
                    "path":          str(skill_dir),
                })
            if not skills:
                continue
            cat_desc_file = cat_dir / "DESCRIPTION.md"
            cat_desc = ""
            if cat_desc_file.exists():
                cat_desc = cat_desc_file.read_text(encoding="utf-8", errors="ignore").strip()[:300]
            categories.append({
                "name":        cat_dir.name,
                "display":     cat_dir.name.replace("-", " ").title(),
                "skill_count": len(skills),
                "description": cat_desc,
                "skills":      skills,
                "path":        str(cat_dir),
            })

    # Synthetic "claude-code" category — flat folder, SKILL.md is uppercase.
    if CLAUDE_SKILLS_DIR.exists():
        cc_skills = []
        for skill_dir in sorted(CLAUDE_SKILLS_DIR.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_path = skill_dir / "SKILL.md"
            desc = _extract_skill_description(skill_path) if skill_path.exists() else ""
            cc_skills.append({
                "name":          skill_dir.name,
                "display":       skill_dir.name.replace("-", " ").title(),
                "description":   desc,
                "has_skill_md":  skill_path.exists(),
                "path":          str(skill_dir),
            })
        if cc_skills:
            categories.append({
                "name":        "claude-code",
                "display":     "Claude Code",
                "skill_count": len(cc_skills),
                "description": "Native Claude Code skills (~/.claude/skills/). Flat layout, SKILL.md uppercase.",
                "skills":      cc_skills,
                "path":        str(CLAUDE_SKILLS_DIR),
            })

    return {"categories": categories}


# ── HTTP handler ──────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress access logs

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def _check_auth(self) -> bool:
        """Return True if request is authorized. If DATA_BRIDGE_TOKEN is unset,
        always returns True (backward-compatible localhost-only behavior).
        Otherwise checks `X-Data-Token` header or `?key=` query param."""
        if not DATA_BRIDGE_TOKEN:
            return True
        # Header
        supplied = self.headers.get("X-Data-Token", "").strip()
        # Query param fallback (so a URL with ?key=... auto-authenticates)
        if not supplied:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            supplied = (q.get("key") or [""])[0].strip()
        if supplied == DATA_BRIDGE_TOKEN:
            return True
        # 401 — frontend prompts for token + retries
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_cors()
        self.end_headers()
        self.wfile.write(b'{"error":"auth_required"}')
        return False

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        log.debug(f"do_GET: raw={self.path!r} parsed={path!r}")

        # ── Static dashboard files (so phone via Cloudflare can load /) ──
        # Two layers:
        #   1. Explicit map for the top-level pages (HTML / JS / CSS / favicons)
        #   2. Whitelisted extension serving from dashboard/ for assets
        #      (sounds/*.mp3, images, fonts) — sandboxed against path traversal.
        DASHBOARD_DIR = Path(__file__).parent
        STATIC_FILES = {
            "/":             ("index.html", "text/html; charset=utf-8"),
            "/index.html":   ("index.html", "text/html; charset=utf-8"),
            "/app.js":       ("app.js",     "application/javascript; charset=utf-8"),
            "/theme.css":    ("theme.css",  "text/css; charset=utf-8"),
            "/favicon.png":  ("favicon.png", "image/png"),
            "/favicon.ico":  ("favicon.ico", "image/x-icon"),
        }
        if path in STATIC_FILES:
            fname, mime = STATIC_FILES[path]
            fpath = DASHBOARD_DIR / fname
            if not fpath.exists():
                self.send_response(404); self.end_headers(); return
            data = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_cors()
            self.end_headers()
            self.wfile.write(data)
            return

        # Asset serving for safe extensions under dashboard/.
        # No "..", absolute path, or hidden files allowed.
        ASSET_MIME = {
            ".mp3": "audio/mpeg",   ".wav": "audio/wav",   ".ogg": "audio/ogg",
            ".png": "image/png",    ".jpg": "image/jpeg",  ".jpeg": "image/jpeg",
            ".gif": "image/gif",    ".svg": "image/svg+xml", ".webp": "image/webp",
            ".woff": "font/woff",   ".woff2": "font/woff2", ".ttf": "font/ttf",
            ".otf":  "font/otf",    ".json": "application/json; charset=utf-8",
        }
        if path.startswith("/") and ".." not in path:
            ext = Path(path).suffix.lower()
            mime = ASSET_MIME.get(ext)
            if mime:
                rel = path.lstrip("/").replace("/", os.sep)
                fpath = (DASHBOARD_DIR / rel).resolve()
                # Sandbox: must live INSIDE DASHBOARD_DIR
                try:
                    fpath.relative_to(DASHBOARD_DIR.resolve())
                except ValueError:
                    self.send_response(403); self.end_headers(); return
                if fpath.is_file():
                    data = fpath.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_cors()
                    self.end_headers()
                    self.wfile.write(data)
                    return
                # Fall through — not found, let the rest of do_GET handle it

        # ── Public privacy policy (no auth — Pinterest/Meta reviewers fetch
        #    this when approving the developer app's OAuth scopes). ──
        if path in ("/privacy", "/privacy.html", "/privacy-policy"):
            body = (
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>DATA — Privacy Policy</title></head>"
                "<body style='font-family:-apple-system,Segoe UI,sans-serif;"
                "background:#0a0a0a;color:#ddd;max-width:720px;margin:0 auto;"
                "padding:48px 24px;line-height:1.6'>"
                "<h1 style='color:#00d4ff'>Privacy Policy</h1>"
                "<p style='color:#888'>DATA &mdash; Dashboard for Analytical "
                "Thought and Action</p>"
                "<p>DATA is a self-hosted, local-first dashboard application. "
                "It runs entirely on the operator's own computer.</p>"
                "<h2 style='color:#00d4ff'>What data is accessed</h2>"
                "<p>Any account the operator connects is accessed solely on the "
                "operator's behalf, on the operator's machine, at the operator's "
                "request.</p>"
                "<h2 style='color:#00d4ff'>How data is stored</h2>"
                "<p>All state — memory, history, tokens, caches — is stored "
                "locally on the operator's own computer. Nothing is transmitted "
                "to, shared with, or sold to any third party. No analytics, "
                "tracking, or advertising services are used.</p>"
                "<h2 style='color:#00d4ff'>Data retention &amp; deletion</h2>"
                "<p>All local data can be deleted at any time by removing the "
                "application's data files.</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
            return

        # Auth gate — applies to all API routes once a token is configured.
        if not self._check_auth():
            return

        if path == "/status":
            with _status_lock:
                self._json(dict(_agent_status))

        elif path == "/browse":
            try:
                # FolderBrowserDialog opens behind the browser unless we give it
                # an owner window flagged TopMost — otherwise the user sees the
                # button "do nothing" while a hidden dialog blocks the thread.
                ps = (
                    "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
                    "Add-Type -AssemblyName System.Drawing | Out-Null;"
                    "$owner = New-Object System.Windows.Forms.Form;"
                    "$owner.TopMost = $true;"
                    "$owner.ShowInTaskbar = $false;"
                    "$owner.Opacity = 0;"
                    "$owner.Size = New-Object System.Drawing.Size(1,1);"
                    "$owner.StartPosition = 'CenterScreen';"
                    "$owner.Show(); [System.Windows.Forms.Application]::DoEvents(); $owner.Activate();"
                    "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                    "$d.Description = 'Select Project Folder';"
                    "$d.RootFolder = 'Desktop';"
                    "$d.SelectedPath = [Environment]::GetFolderPath('MyDocuments');"
                    "$d.ShowNewFolderButton = $false;"
                    "$result = $d.ShowDialog($owner);"
                    "$owner.Close();"
                    "if ($result -eq 'OK') { Write-Output $d.SelectedPath }"
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-STA", "-Command", ps],
                    capture_output=True, text=True, timeout=120
                )
                chosen = result.stdout.strip()
                self._json({"path": chosen})
            except subprocess.TimeoutExpired:
                self._json({"path": ""})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/project":
            self._json({
                "path":  _project_path,
                "nodes": _project_nodes,
                "text":  _project_text,
            })

        elif path == "/project/file":
            qs       = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fpath    = qs.get("path", [None])[0]
            if not fpath or not Path(fpath).is_file():
                self._json({"error": "not found"}, 404)
                return
            try:
                content = Path(fpath).read_text(encoding="utf-8", errors="replace")
                self._json({"content": content[:50000], "truncated": len(content) > 50000})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/desktop_control":
            # Status of the desktop-control kill switch + whether pyautogui is
            # available. Native computer tool is only registered when both are true.
            dc = _get_dc()
            self._json({
                "enabled":         bool(dc and dc.is_enabled()),
                "available":       bool(dc and dc.is_available()),
                "provider_active": _provider_supports_native_computer(_current_provider_id()),
            })

        elif path == "/computer/info":
            # CLI-mode helper. Returns screen size, cursor position, and
            # whether the kill switch is armed. Use BEFORE clicking so the
            # agent emits coordinates in the right space.
            dc = _get_dc()
            info: dict = {
                "enabled":   bool(dc and dc.is_enabled()),
                "available": bool(dc and dc.is_available()),
            }
            if dc and dc.is_available():
                try:
                    sz = dc.screen_size()
                    info["screen_width"]  = sz["width"]
                    info["screen_height"] = sz["height"]
                    cp = dc.cursor_position()
                    info["cursor_x"] = cp["x"]
                    info["cursor_y"] = cp["y"]
                except Exception as e:
                    info["error"] = str(e)
            self._json(info)

        elif path == "/mail/accounts":
            # Safe-for-display account list (no passwords).
            m = _get_mail()
            self._json({"accounts": m.list_accounts() if m else []})

        elif path == "/calendar/auth_status":
            g = _get_gcal()
            self._json({
                "module_available": g is not None,
                "google_libs":      bool(g and g.is_available()),
                "authorized":       bool(g and g.is_authorized()),
                "client_secret_path": str((Path(os.environ.get("LOCALAPPDATA", "")) /
                                           "hermes" / "google_client_secret.json")),
            })

        elif path == "/health":
            self._json({"status": "online", "agent": "DATA", "mode": BRIDGE_MODE})

        elif path == "/mode":
            self._json({"mode": BRIDGE_MODE})

        elif path == "/providers":
            self._json({"active": ACTIVE_PROVIDER, "providers": _list_providers()})

        # ── AI Connectors page ────────────────────────────────
        elif path == "/hardware":
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._json(_detect_hardware(force=("force" in _q)))

        elif path == "/llm/catalog":
            self._json(_llm_catalog_payload())

        elif path == "/llm/install_status":
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            job_id = _q.get("job", [""])[0]
            with _install_lock:
                job = _install_jobs.get(job_id)
            if not job:
                self._json({"error": "unknown job"}, 404)
            else:
                self._json(job)

        elif path == "/ui_events":
            # Drain and return this client's queued UI events. Frontend polls
            # this with ?client_id=<stable-per-tab-id>. Each client gets its
            # own queue so concurrent dashboard tabs don't steal each other's
            # events. Missing client_id falls back to a shared legacy bucket so
            # an un-updated frontend still functions (degraded: single-client).
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            client_id = (_q.get("client_id", [""])[0] or "_legacy").strip()
            events = _drain_ui_events(client_id)
            self._json({"events": events})

        elif path == "/standing_orders":
            with _orders_lock:
                self._json({"orders": list(_standing_orders)})

        elif path == "/briefing":
            briefing_file = PROJECT_DIR / "daily_briefing.json"
            if briefing_file.exists():
                try:
                    self._json(json.loads(briefing_file.read_text(encoding="utf-8")))
                except Exception as e:
                    self._json({"error": f"could not parse briefing: {e}", "items": []}, 500)
            else:
                self._json({"generated_at": None, "items": []})

        elif path == "/tunnel_url":
            # Read the URL that launcher.py writes after cloudflared comes up.
            # Empty string if the tunnel isn't running yet (or cloudflared not installed).
            f = Path(__file__).parent / "tunnel_url.txt"
            self._json({"url": f.read_text(encoding="utf-8").strip() if f.exists() else ""})

        elif path == "/voice/status":
            self._json({
                "ready":         _voice_ready.is_set(),
                "stt_available": _VOICE_AVAILABLE,
                "stt_loaded":    local_voice._whisper_model is not None,
                "tts_loaded":    local_voice._f5_model is not None,
                "xtts_loaded":   local_voice._xtts_model is not None,
                "tts_engine":    local_voice.ENGINE,
                "voices":        local_voice.list_voices(),
                "default_voice": local_voice.DEFAULT_VOICE,
                "active_crew":   VOICE_ACTIVE_CREW,
            })

        elif path == "/voice/tts_engine":
            self._json({"engine": local_voice.ENGINE, "choices": list(local_voice.VALID_ENGINES)})

        elif path == "/user/active":
            # Just the active user dict — used by the UI on boot and after
            # /user/switch to update the header pill.
            self._json({"active": _ACTIVE_USER, "user": _active_user_dict()})

        elif path == "/user/list":
            # All registered users + which one is active. The frontend renders
            # the switcher dropdown from this.
            self._json({
                "active": _ACTIVE_USER,
                "users":  [_USERS[uid] for uid in _USERS],
            })

        elif path == "/voice/voices":
            # Roster of bridge-crew voices the dashboard selector renders.
            # Each entry carries the display NAME (authoritative — from the
            # bridge) plus whether it is a persona-swapped crew member.
            _avail = set(local_voice.list_voices())
            self._json({
                "voices": [
                    {"id": vid, "name": spec["name"],
                     "is_crew": bool(spec["persona"]),
                     "wake":    spec.get("wake", [spec["name"]]),
                     "names":   spec.get("names", [vid])}
                    for vid, spec in CREW_VOICES.items() if vid in _avail
                ],
                "default": local_voice.DEFAULT_VOICE,
                "active":  VOICE_ACTIVE_CREW,
            })

        elif path == "/voice/provider":
            self._json({
                "active":  VOICE_PROVIDER,
                "choices": [
                    {
                        "id":        pid,
                        "label":     PROVIDERS[pid]["label"],
                        "model":     PROVIDERS[pid]["model"],
                        "available": _provider_available(pid),
                    }
                    for pid in VOICE_PROVIDER_CHOICES if pid in PROVIDERS
                ],
            })

        elif path == "/memory":
            content = read_memory()
            self._json({"content": content})

        elif path == "/open":
            target = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('path', [None])[0]
            if target and Path(target).exists():
                os.startfile(target)
                self._json({"opened": target})
            else:
                self._json({"error": "path not found"}, 404)

        elif path == "/file":
            # Serve a local file (currently images only) so the chat renderer can
            # inline-embed pictures DATA generated outside the dashboard dir.
            # Sandboxed: must resolve inside the user's home and be a whitelisted
            # extension. No "..", no symlink escapes.
            target = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('path', [None])[0]
            if not target:
                self._json({"error": "path required"}, 400); return
            IMG_MIME = {
                ".png":  "image/png",  ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif":  "image/gif",  ".webp": "image/webp", ".svg":  "image/svg+xml",
                ".bmp":  "image/bmp",  ".ico":  "image/x-icon",
            }
            try:
                fpath = Path(target).resolve(strict=False)
                home  = Path.home().resolve()
                fpath.relative_to(home)  # raises ValueError if outside home
            except (ValueError, OSError):
                self._json({"error": "outside sandbox"}, 403); return
            ext = fpath.suffix.lower()
            mime = IMG_MIME.get(ext)
            if not mime:
                self._json({"error": "unsupported file type"}, 415); return
            if not fpath.is_file():
                self._json({"error": "not found"}, 404); return
            data = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_cors()
            self.end_headers()
            self.wfile.write(data)

        elif path == "/files":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target = qs.get('dir', [None])[0]
            depth_str = qs.get('depth', ['999'])[0]
            try:
                max_depth = int(depth_str)
            except ValueError:
                max_depth = 999
            # Default scan root: the user's Documents folder (falls back to
            # home, then the install dir) — the project launcher browses from
            # here unless an explicit dir is passed.
            if target and Path(target).is_dir():
                scan_dir = Path(target)
            else:
                _docs = Path.home() / "Documents"
                scan_dir = _docs if _docs.is_dir() else (Path.home() if Path.home().is_dir() else PROJECT_DIR)
            graph = build_file_graph(scan_dir, max_depth=max_depth, max_nodes=300)
            graph["root"] = str(scan_dir)
            self._json(graph)

        elif path == "/memory-files":
            files = []
            targets = [
                ("Computer Memory",     COMPUTER_MEMORY_FILE),
                ("Soul / Identity",    HERMES_DIR / "SOUL.md"),
                ("Conversation Log",   HISTORY_FILE),
                ("Hermes Memory",      MEMORY_FILE),
            ]
            for label, p in targets:
                if p.exists():
                    stat = p.stat()
                    content = p.read_text(encoding="utf-8", errors="replace")
                    files.append({
                        "label":    label,
                        "name":     p.name,
                        "path":     str(p),
                        "size":     stat.st_size,
                        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "content":  content[:50000],
                    })
            self._json({"files": files})

        elif path == "/test":
            # Diagnostic endpoint — tests each component
            report = {
                "local_voice":   True,                                  # always available now
                "stt_ready":     local_voice._whisper_model is not None,
                "tts_ready":     local_voice._f5_model is not None,
                "voice_device":  local_voice.DEVICE,
                "hermes_bin": None,
                "hermes_bin_exists": False,
            }
            for candidate in [HERMES_DIR / "bin" / "hermes", "hermes"]:
                try:
                    r = subprocess.run([str(candidate), "--version"],
                                       capture_output=True, text=True, timeout=5)
                    report["hermes_bin"] = str(candidate)
                    report["hermes_bin_exists"] = True
                    report["hermes_version"] = r.stdout.strip() or r.stderr.strip()
                    break
                except Exception as e:
                    report[f"hermes_try_{candidate}"] = str(e)
            self._json(report)

        elif path == "/skills":
            # Return Data's actual registered tools as skill cards
            TOOL_META = {
                "web_search":     {"category": "INTELLIGENCE & RESEARCH", "icon": "🌐", "desc": "Search the web for current information and news"},
                "web_extract":    {"category": "INTELLIGENCE & RESEARCH", "icon": "📄", "desc": "Fetch and read full contents of any webpage"},
                "read_file":      {"category": "COMPUTER OPERATIONS",     "icon": "📁", "desc": "Read any file on the Captain's computer"},
                "write_file":     {"category": "COMPUTER OPERATIONS",     "icon": "💾", "desc": "Write or create files on the computer"},
                "list_directory": {"category": "COMPUTER OPERATIONS",     "icon": "📂", "desc": "List files and folders in a directory"},
                "terminal":       {"category": "COMPUTER OPERATIONS",     "icon": "⚙️", "desc": "Run shell commands and system operations"},
                "execute_python": {"category": "AUTOMATION",              "icon": "🐍", "desc": "Write and execute Python code for analysis"},
                "read_clipboard": {"category": "COMPUTER OPERATIONS",     "icon": "📋", "desc": "Read the current clipboard contents"},
                "write_clipboard":{"category": "COMPUTER OPERATIONS",     "icon": "📌", "desc": "Write text directly to the clipboard"},
                "take_screenshot":{"category": "COMPUTER OPERATIONS",     "icon": "📷", "desc": "Capture and analyze the current screen"},
                "remember":       {"category": "MEMORY & COGNITION",      "icon": "🧠", "desc": "Save persistent notes across all sessions"},
                "recall_memory":  {"category": "MEMORY & COGNITION",      "icon": "🗄️", "desc": "Review all stored memories and notes"},
                "load_skill":     {"category": "INTELLIGENCE & RESEARCH", "icon": "📚", "desc": "Load detailed instructions for complex tasks"},
            }
            tool_list = []
            for tool in TOOLS:
                name = tool["name"]
                meta = TOOL_META.get(name, {
                    "category": "GENERAL",
                    "icon": "🔧",
                    "desc": tool.get("description", "")[:80]
                })
                tool_list.append({
                    "name": name,
                    "display": name.replace("_", " ").title(),
                    "category": meta["category"],
                    "icon": meta["icon"],
                    "desc": meta["desc"]
                })
            self._json({
                "tools": tool_list,
                "count": len(tool_list),
                "skills_dir": str(SKILLS_DIR),
                "bridge_path": str(Path(__file__).resolve())
            })

        elif path == "/drives":
            self._json(build_drives_graph())

        elif path == "/vitals":
            self._json(build_vitals())

        elif path == "/ships_health":
            # One-shot JSON snapshot of the same data /vitals_fast streams.
            self._json(build_ships_health())

        elif path == "/msd":
            # Heavier MSD-modal snapshot: LLM activity, memory stats, recent
            # tools, subsystem state. Polled ~2s by the modal while open.
            self._json(build_msd())

        elif path == "/vitals_fast":
            # SSE stream — pushes System Health every 500ms for the engine gauge / bars.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors()
            self.end_headers()
            try:
                while True:
                    payload = json.dumps(build_ships_health())
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return

        elif path == "/memory-stats":
            self._json(build_memory_stats())

        elif path.startswith("/oauth/instagram"):
            # OAuth callback for Instagram Business Login. Meta requires HTTPS
            # for the redirect URI, so we route through the cloudflared tunnel
            # → this endpoint → write the captured code to disk where
            # ginstagram.py's setup script is polling for it.
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code  = qs.get("code", [""])[0]
            state = qs.get("state", [""])[0]
            err   = qs.get("error_description", qs.get("error", [""]))[0]
            try:
                capture = PROJECT_DIR / "instagram_oauth_capture.json"
                capture.write_text(json.dumps({
                    "code":         code,
                    "state":        state,
                    "error":        err,
                    "captured_at":  time.time(),
                }), encoding="utf-8")
            except Exception as e:
                log.warning(f"[oauth/instagram] capture write failed: {e}")
            body = (
                "<html><body style='font-family:sans-serif;background:#0a0a0a;"
                "color:#ddd;padding:40px;text-align:center'>"
                + ("<h2 style='color:#ff6666'>OAuth failed</h2><p>" + err + "</p>" if err else
                   "<h2 style='color:#ff9900'>Connected.</h2>"
                   "<p>You can close this tab &mdash; DATA picked up the token.</p>")
                + "</body></html>"
            ).encode("utf-8")
            self.send_response(200 if not err else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        elif path.startswith("/oauth/pinterest"):
            # OAuth callback for Pinterest API v5. Pinterest requires HTTPS
            # for the redirect URI, so we route through the cloudflared tunnel
            # → this endpoint → write the captured code to disk where
            # gpinterest.py's setup script is polling for it.
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code  = qs.get("code", [""])[0]
            state = qs.get("state", [""])[0]
            err   = qs.get("error_description", qs.get("error", [""]))[0]
            try:
                capture = PROJECT_DIR / "pinterest_oauth_capture.json"
                capture.write_text(json.dumps({
                    "code":         code,
                    "state":        state,
                    "error":        err,
                    "captured_at":  time.time(),
                }), encoding="utf-8")
            except Exception as e:
                log.warning(f"[oauth/pinterest] capture write failed: {e}")
            body = (
                "<html><body style='font-family:sans-serif;background:#0a0a0a;"
                "color:#ddd;padding:40px;text-align:center'>"
                + ("<h2 style='color:#ff6666'>OAuth failed</h2><p>" + err + "</p>" if err else
                   "<h2 style='color:#ff9900'>Connected.</h2>"
                   "<p>You can close this tab &mdash; DATA picked up the token.</p>")
                + "</body></html>"
            ).encode("utf-8")
            self.send_response(200 if not err else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        elif path == "/youtube/status":
            # CLI quick check: is the YouTube integration usable, and which
            # accounts can Data target? Returns the list so the CLI knows
            # what to pass as the `account` parameter on upload.
            try:
                import gyoutube as _gy
                self._json({
                    "available":   _gy.available(),
                    "configured":  _gy.is_configured(),
                    "accounts":    _gy.available_accounts(),
                    "scopes":      _gy.SCOPES,
                })
            except Exception as e:
                self._json({"available": False, "error": str(e)})
            return

        elif path == "/youtube/accounts":
            # Lightweight listing endpoint — same data as /youtube/status but
            # focused, so CLI Data can run a quick "which channels do I have"
            # check without parsing the full status payload.
            try:
                import gyoutube as _gy
                self._json({"accounts": _gy.available_accounts()})
            except Exception as e:
                self._json({"accounts": [], "error": str(e)})
            return

        elif path == "/search-history":
            # CLI-mode escape hatch: claude-cli / codex / gemini can't see the
            # search_history TOOLS entry, so they curl this endpoint instead via
            # their own Bash tool. Same code path as the native tool.
            qs    = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            query = qs.get("query", [""])[0] or qs.get("q", [""])[0]
            try:    k = int(qs.get("k", ["5"])[0])
            except ValueError: k = 5
            scope   = qs.get("scope", ["current"])[0]
            sources = qs.get("sources", [None])[0]   # comma-separated, optional
            # Honor an optional ?pane= override so a CLI invocation can target a
            # specific project without us inferring it from thread-local state.
            pane_override = qs.get("pane", [None])[0]
            if pane_override:
                _bind_history(pane_override)
            fmt = qs.get("format", ["text"])[0]
            try:
                result = tool_search_history(query, k=k, scope=scope, sources=sources)
            except Exception as e:
                self._json({"error": str(e)}, 500); return
            if fmt == "json":
                self._json({"query": query, "k": k, "scope": scope, "result": result})
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_cors()
                self.end_headers()
                self.wfile.write(result.encode("utf-8"))

        elif path == "/neural":
            self._json(build_neural_graph())

        elif path == "/skills-full":
            self._json(build_skills_full())

        elif path == "/skill-content":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            category = qs.get('category', [None])[0]
            name     = qs.get('name', [None])[0]
            if not category or not name:
                self._json({"error": "category and name required"}, 400)
                return
            # Claude Code skills live flat with uppercase SKILL.md
            if category == "claude-code":
                skill_path = CLAUDE_SKILLS_DIR / name / "SKILL.md"
            else:
                skill_path = SKILLS_DIR / category / name / "skill.md"
            if not skill_path.exists():
                self._json({"error": "skill not found", "path": str(skill_path)}, 404)
                return
            content = skill_path.read_text(encoding="utf-8", errors="replace")
            self._json({"content": content, "path": str(skill_path)})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Auth gate (same as do_GET). Static files are GET-only so no carve-out needed.
        if not self._check_auth():
            return

        # ── /transcribe — STT only, no LLM, no TTS ───────────
        if path == "/transcribe":
            content_type = self.headers.get("Content-Type", "")
            suffix = ".ogg" if "ogg" in content_type else ".wav" if "wav" in content_type else ".webm"
            try:
                text = local_voice.transcribe(body, suffix)
                self._json({"text": text})
            except Exception as e:
                log.exception(f"/transcribe error: {e}")
                self._json({"error": str(e)}, 500)
            return

        # ── Standing orders (create / update / delete / run) ──
        if path.startswith("/standing_orders"):
            try:
                data = json.loads(body) if body else {}
            except Exception:
                data = {}
            parts = path.strip("/").split("/")
            # parts = ["standing_orders"]                       → create
            # parts = ["standing_orders", "<id>"]               → update
            # parts = ["standing_orders", "<id>", "delete"]     → delete
            # parts = ["standing_orders", "<id>", "run"]        → run now
            if len(parts) == 1:
                # CREATE
                err = _validate_cron(data.get("cron", ""))
                if err:
                    self._json({"error": f"cron invalid: {err}"}, 400); return
                if not data.get("name") or not data.get("prompt") or not data.get("provider"):
                    self._json({"error": "name, prompt, provider required"}, 400); return
                if data["provider"] not in PROVIDERS:
                    self._json({"error": f"unknown provider '{data['provider']}'"}, 400); return
                new_id = f"so-{int(time.time()*1000)}"
                order = {
                    "id":       new_id,
                    "name":     data["name"][:80],
                    "cron":     data["cron"],
                    "prompt":   data["prompt"],
                    "provider": data["provider"],
                    "enabled":  bool(data.get("enabled", True)),
                    "next_run": 0,
                    "last_run": 0,
                    "last_result": "",
                    "notify_telegram": bool(data.get("notify_telegram", False)),
                }
                _recompute_next_run(order)
                with _orders_lock:
                    _standing_orders.append(order)
                    _save_standing_orders()
                log.info(f"[orders] created {new_id}: {order['name']!r} ({order['cron']})")
                self._json({"order": order}); return

            if len(parts) >= 2:
                oid = parts[1]
                with _orders_lock:
                    order = next((o for o in _standing_orders if o["id"] == oid), None)
                if not order:
                    self._json({"error": "order not found"}, 404); return
                action = parts[2] if len(parts) >= 3 else None

                if action == "delete":
                    with _orders_lock:
                        _standing_orders[:] = [o for o in _standing_orders if o["id"] != oid]
                        _save_standing_orders()
                    log.info(f"[orders] deleted {oid}: {order['name']!r}")
                    self._json({"deleted": oid}); return

                if action == "run":
                    threading.Thread(target=_fire_standing_order, args=(order,), daemon=True).start()
                    self._json({"running": oid}); return

                # UPDATE — patch in-place
                if "cron" in data:
                    err = _validate_cron(data["cron"])
                    if err:
                        self._json({"error": f"cron invalid: {err}"}, 400); return
                if "provider" in data and data["provider"] not in PROVIDERS:
                    self._json({"error": f"unknown provider '{data['provider']}'"}, 400); return
                with _orders_lock:
                    for k in ("name", "cron", "prompt", "provider", "enabled", "notify_telegram"):
                        if k in data: order[k] = data[k]
                    _recompute_next_run(order)
                    _save_standing_orders()
                log.info(f"[orders] updated {oid}: {order['name']!r}")
                self._json({"order": order}); return

        # ── /briefing/refresh — rescan sources in the background ─
        if path == "/briefing/refresh":
            def _gen():
                try:
                    import importlib
                    import daily_briefing as _db
                    importlib.reload(_db)   # pick up source-list edits without restart
                    _db.generate_briefing()
                except Exception as ex:
                    log.exception(f"[BRIEFING] refresh failed: {ex}")
            threading.Thread(target=_gen, daemon=True).start()
            self._json({"refreshing": True})
            return

        # ── /briefing/dismiss | /briefing/install — flip item status ─
        if path == "/briefing/dismiss" or path == "/briefing/install":
            item_id = data.get("id", "")
            new_status = "dismissed" if path.endswith("dismiss") else "installed"
            briefing_file = PROJECT_DIR / "daily_briefing.json"
            if not briefing_file.exists():
                self._json({"error": "no briefing"}, 404)
                return
            try:
                briefing = json.loads(briefing_file.read_text(encoding="utf-8"))
                found = False
                for it in briefing.get("items", []):
                    if it.get("id") == item_id:
                        it["status"] = new_status
                        found = True
                        break
                if not found:
                    self._json({"error": "item not found"}, 404)
                    return
                briefing_file.write_text(json.dumps(briefing, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json({"updated": item_id, "status": new_status})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        # ── /speak — raw binary audio, DO NOT JSON-parse ──────
        if path == "/speak":
            content_type = self.headers.get("Content-Type", "")
            suffix = ".ogg" if "ogg" in content_type else ".wav" if "wav" in content_type else ".webm"
            _qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            _voice = _normalize_voice(_qs.get("voice", ["data"])[0])
            log.info(f"/speak received {len(body)} bytes, content-type={content_type}, suffix={suffix}, voice={_voice}")
            try:
                result = full_pipeline(body, suffix, voice=_voice)
                log.info(f"/speak result: user={result.get('user_text','')!r} response={result.get('response_text','')!r} audio_b64_len={len(result.get('audio_b64',''))}")
                self._json(result)
            except Exception as e:
                log.exception(f"/speak pipeline error: {e}")
                self._json({"error": str(e)}, 500)
            return

        # ── /speak_stream — same as /speak but SSE per-sentence ─
        if path == "/speak_stream":
            content_type = self.headers.get("Content-Type", "")
            suffix = ".ogg" if "ogg" in content_type else ".wav" if "wav" in content_type else ".webm"
            _qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            _voice = _normalize_voice(_qs.get("voice", ["data"])[0])
            log.info(f"/speak_stream received {len(body)} bytes, content-type={content_type}, voice={_voice}")

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors()
            self.end_headers()

            _write_lock = threading.Lock()
            _stream_done = threading.Event()

            def send_sse(event_type: str, data_str: str) -> None:
                with _write_lock:
                    try:
                        chunk = f"event: {event_type}\ndata: {data_str}\n\n"
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass

            def _run() -> None:
                try:
                    stream_voice_pipeline(body, suffix, send_sse, voice=_voice)
                except Exception as e:
                    log.exception(f"/speak_stream pipeline error: {e}")
                    send_sse("error", json.dumps({"error": str(e)}))
                finally:
                    _stream_done.set()

            threading.Thread(target=_run, daemon=True).start()

            # SSE keepalive every 8 s so Windows/proxies don't drop the idle TCP connection
            while not _stream_done.wait(timeout=8.0):
                with _write_lock:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
            return

        # ── All other POST routes expect JSON ─────────────────
        # Empty body is OK — many endpoints (like /briefing/refresh) take no
        # arguments and the frontend sends no body. Treat that as {}.
        if not body or not body.strip():
            data = {}
        else:
            try:
                data = json.loads(body)
            except Exception:
                log.warning(f"POST {path} — invalid JSON body")
                self.send_response(400)
                self.end_headers()
                return

        if path == "/project":
            global _project_path, _project_nodes, _project_text
            new_path = data.get("path", "").strip()
            # register_only: the frontend is opening a SEPARATE project window
            # and only needs the folder scanned for that pane's file-tree graph.
            # Such a window must NOT become the bridge's global cwd — that root
            # belongs solely to whichever pane the Captain re-roots explicitly.
            # Without this, every new window silently re-pointed the main
            # channel's working directory at the last-opened folder.
            register_only = bool(data.get("register_only"))
            if not new_path:
                if not register_only:
                    _project_path = ""; _project_nodes = []; _project_text = ""
                self._json({"cleared": True})
                return
            # Expand ~ / ~user and environment vars so spawn markers and the
            # frontend can pass "~/Documents/Foo" paths (as the system prompt's
            # own examples instruct). Without this, Path("~/...").is_dir() is
            # always False and the window/scan silently fails.
            p = Path(os.path.expanduser(os.path.expandvars(new_path)))
            if not p.is_dir():
                self._json({"error": "Directory not found"}, 400)
                return
            nodes, text = _scan_project(str(p))
            if register_only:
                log.info(f"[PROJECT] scanned {p} (register_only — global cwd unchanged) "
                         f"— {len(nodes)} entries")
            else:
                _project_path = str(p)
                _project_nodes, _project_text = nodes, text
                log.info(f"[PROJECT] loaded {_project_path} — {len(nodes)} entries")
            self._json({"path": str(p), "nodes": nodes, "count": len(nodes)})
            return

        elif path == "/user/switch":
            # Flip the active Captain. Body: {"user": "<uid>"}. The switch
            # flushes outgoing rolling history, repoints the per-user file
            # constants, reloads incoming history, and persists the selection
            # so the next bridge restart comes up on the same Captain.
            uid = (data.get("user") or data.get("id") or "").strip().lower()
            if not uid:
                self._json({"error": "missing 'user'"}, 400)
                return
            try:
                u = _switch_active_user(uid)
            except ValueError as e:
                self._json({"error": str(e)}, 404)
                return
            except Exception as e:
                log.exception(f"/user/switch failed: {e}")
                self._json({"error": str(e)}, 500)
                return
            self._json({"active": _ACTIVE_USER, "user": u})
            return

        elif path == "/tts":
            text  = data.get("text", "").strip()
            voice = (data.get("voice") or local_voice.DEFAULT_VOICE).strip().lower()
            if not text:
                self._json({"error": "no text"}, 400)
                return
            try:
                wav_bytes, mime = local_voice.synthesize_long(text, voice=voice)
                self._json({
                    "audio_b64":  base64.b64encode(wav_bytes).decode(),
                    "audio_mime": mime,
                    "voice":      voice,
                })
            except ValueError as e:
                # unknown voice id / empty text — caller error, not a server fault
                self._json({"error": str(e)}, 400)
            except Exception as e:
                log.exception(f"/tts error: {e}")
                self._json({"error": str(e)}, 500)
            return

        elif path == "/shutdown":
            self._json({"shutting_down": True})
            threading.Thread(target=_do_shutdown, daemon=True).start()
            return

        elif path == "/reboot":
            # Restart the bridge in place. The REBOOT button hits the supervisor
            # on :7766 first; this endpoint is its fallback (Linux/systemd, or a
            # wedged-but-alive bridge). See _do_reboot for the mechanism.
            self._json({"ok": True, "rebooting": True})
            threading.Thread(target=_do_reboot, daemon=True).start()
            return

        elif path == "/power_core/ack":
            # Client confirms it displayed the breach popup; stop pending it.
            power_core_ack()
            self._json({"acknowledged": True})
            return

        elif path == "/power_core/reset":
            # Re-snapshot the baseline against current used_tokens.
            try:
                mem = build_memory_stats()
                new_baseline = power_core_reset(mem.get("used_tokens", 0))
                self._json({"baseline_tokens": new_baseline})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/heartbeat":
            # Browser keepalive. Query ?leaving=1 shortens grace to 5s (set by
            # beforeunload sendBeacon for fast shutdown on tab close).
            global _last_heartbeat, _lifecycle_leaving
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if qs.get("leaving") == ["1"]:
                _lifecycle_leaving = True
            else:
                _lifecycle_leaving = False
            _last_heartbeat = datetime.datetime.now()
            self._json({"ok": True})
            return

        elif path == "/desktop_control":
            # Toggle Data's desktop control on/off. POST {"enabled": true|false}.
            dc = _get_dc()
            if dc is None:
                self._json({"error": "desktop_control module unavailable; install pyautogui"}, 500)
                return
            try:
                want = bool(data.get("enabled", True))
                dc.set_enabled(want)
                self._json({"enabled": dc.is_enabled(), "available": dc.is_available()})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        # ── Computer-use HTTP surface ─────────────────────────────────
        # CLI providers (claude-cli, codex, gemini) can drive the desktop
        # by curling these endpoints. Same code paths as the API path's
        # native computer tool, so behavior matches across providers.
        elif path == "/computer/screenshot":
            # POST {"describe": false}. Saves PNG, returns {path, url, width,
            # height}. With describe=true, also adds a vision description.
            try:
                describe = bool((data or {}).get("describe", False))
                result = _computer_screenshot_capture(describe=describe)
                self._json(result)
            except Exception as e:
                log.exception("/computer/screenshot failed")
                self._json({"error": str(e)}, 500)
            return

        elif path == "/computer/click":
            # POST {"x": int, "y": int, "button"?: "left"|"right"|"middle", "clicks"?: int}
            try:
                d = data or {}
                msg = _desktop_call(lambda dc: dc.mouse_click(
                    d.get("x"), d.get("y"),
                    d.get("button", "left"), int(d.get("clicks", 1))
                ))
                self._json({"result": msg})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/computer/move":
            # POST {"x": int, "y": int}
            try:
                d = data or {}
                msg = _desktop_call(lambda dc: dc.mouse_move(d["x"], d["y"]))
                self._json({"result": msg})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/computer/drag":
            # POST {"start_x": int, "start_y": int, "end_x": int, "end_y": int}
            try:
                d = data or {}
                msg = _desktop_call(lambda dc: dc.mouse_drag(
                    d["start_x"], d["start_y"], d["end_x"], d["end_y"]))
                self._json({"result": msg})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/computer/type":
            # POST {"text": str}
            try:
                d = data or {}
                msg = _desktop_call(lambda dc: dc.type_text(d["text"]))
                self._json({"result": msg})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/computer/key":
            # POST {"keys": "enter"|"ctrl+c"|"win+r"|...} — comma-separate to chain
            try:
                d = data or {}
                msg = _desktop_call(lambda dc: dc.key_press(d["keys"]))
                self._json({"result": msg})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/computer/scroll":
            # POST {"amount": int, "x"?: int, "y"?: int}
            try:
                d = data or {}
                msg = _desktop_call(lambda dc: dc.scroll(
                    int(d["amount"]), d.get("x"), d.get("y")))
                self._json({"result": msg})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/mail/accounts":
            # POST {action: "add"|"remove", account: {...}|label: "..."}
            m = _get_mail()
            if m is None:
                self._json({"error": "mail module unavailable"}, 500)
                return
            action = data.get("action", "add")
            try:
                if action == "remove":
                    label = data.get("label") or (data.get("account") or {}).get("label")
                    if not label:
                        self._json({"error": "label required"}, 400); return
                    self._json(m.remove_account(label))
                else:
                    acct = data.get("account") or {k: v for k, v in data.items() if k != "action"}
                    self._json(m.add_account(acct))
            except Exception as e:
                log.exception("/mail/accounts failed")
                self._json({"error": str(e)}, 500)
            return

        elif path == "/youtube/upload":
            # CLI-mode escape hatch for video upload. Body matches the
            # youtube_upload_video tool's input_schema 1:1. Same code path.
            try:
                result = tool_youtube_upload_video(data or {})
                self._json({"result": result})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/youtube/update":
            # CLI-mode escape hatch for video metadata edits.
            try:
                result = tool_youtube_update_video(data or {})
                self._json({"result": result})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/youtube/thumbnail":
            # CLI-mode escape hatch for thumbnail replacement.
            try:
                result = tool_youtube_set_thumbnail(data or {})
                self._json({"result": result})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        elif path == "/memory-compact":
            # Manual trigger from the dashboard. Synchronous so the UI can
            # show before/after numbers in the response toast.
            if not _memory_compact_lock.acquire(blocking=False):
                self._json({"ok": False, "error": "compaction already in progress"}, 429)
                return
            try:
                result = _compact_memory_file()
                self._json(result, 200 if result.get("ok") else 500)
            finally:
                _memory_compact_lock.release()
            return

        elif path == "/skill-save":
            category = data.get("category", "")
            name     = data.get("name", "")
            content  = data.get("content", "")
            if not category or not name:
                self._json({"error": "category and name required"}, 400)
                return
            if category == "claude-code":
                skill_path = CLAUDE_SKILLS_DIR / name / "SKILL.md"
            else:
                skill_path = SKILLS_DIR / category / name / "skill.md"
            if not skill_path.exists():
                self._json({"error": "skill not found"}, 404)
                return
            skill_path.write_text(content, encoding="utf-8")
            log.info(f"[SKILL-SAVE] {category}/{name} — {len(content)} chars ({skill_path.name})")
            self._json({"saved": True, "path": str(skill_path)})

        elif path == "/mode":
            global BRIDGE_MODE
            new_mode = data.get("mode", "")
            if new_mode not in ("api", "cli"):
                self._json({"error": "mode must be 'api' or 'cli'"}, 400)
                return
            BRIDGE_MODE = new_mode
            log.info(f"[MODE] switched to {BRIDGE_MODE}")
            self._json({"mode": BRIDGE_MODE})

        elif path == "/provider":
            global ACTIVE_PROVIDER
            new_provider = data.get("provider", "")
            if new_provider not in PROVIDERS:
                self._json({"error": f"unknown provider; valid: {list(PROVIDERS.keys())}"}, 400)
                return
            if not _provider_available(new_provider):
                self._json({
                    "error":        f"provider '{new_provider}' is not available",
                    "install_hint": PROVIDERS[new_provider].get("install_hint", ""),
                }, 400)
                return
            ACTIVE_PROVIDER = new_provider
            log.info(f"[PROVIDER] switched to {ACTIVE_PROVIDER}")
            self._json({"active": ACTIVE_PROVIDER, "providers": _list_providers()})

        elif path == "/llm/install":
            # kind='ollama' → pull a local model;  kind='cli' → install a connector
            kind   = (data.get("kind") or "").strip()
            target = (data.get("target") or "").strip()
            if kind == "ollama":
                valid = {m["model"] for m in OLLAMA_CATALOG}
                if target not in valid:
                    self._json({"error": f"unknown model '{target}'"}, 400); return
            elif kind == "cli":
                # Only allow the exact install commands from our connector catalog
                # (never run arbitrary shell from the client).
                cmds = {c["install_cmd"] for c in CONNECTOR_CATALOG if c["install_cmd"]}
                if target not in cmds:
                    self._json({"error": "install command not in connector catalog"}, 400); return
            else:
                self._json({"error": "kind must be 'ollama' or 'cli'"}, 400); return
            job = _start_install_job(kind, target)
            self._json(job)

        elif path == "/voice/provider":
            global VOICE_PROVIDER
            new_voice = data.get("provider", "")
            if new_voice not in VOICE_PROVIDER_CHOICES:
                self._json({
                    "error": f"voice provider must be one of {list(VOICE_PROVIDER_CHOICES)}",
                }, 400)
                return
            if not _provider_available(new_voice):
                self._json({
                    "error":        f"voice provider '{new_voice}' is not available",
                    "install_hint": PROVIDERS[new_voice].get("install_hint", ""),
                }, 400)
                return
            VOICE_PROVIDER = new_voice
            log.info(f"[VOICE_PROVIDER] switched to {VOICE_PROVIDER}")
            self._json({"active": VOICE_PROVIDER})

        elif path == "/voice/tts_engine":
            engine = (data.get("engine") or "").strip()
            if engine not in local_voice.VALID_ENGINES:
                self._json({"error": f"engine must be one of {list(local_voice.VALID_ENGINES)}"}, 400)
                return
            try:
                local_voice.set_engine(engine)
            except Exception as e:
                self._json({"error": str(e)}, 500); return
            self._json({"engine": local_voice.ENGINE})

        elif path == "/stop":
            # Body may include {"project_path": "...", "pane_id": "..."} to
            # target ONE pane's subprocess. The pane_id is what lets two windows
            # on the same folder stop independently; without it, /stop falls
            # back to the path-only key (used by background callers).
            req_path = (data.get("project_path") or "").strip() if isinstance(data, dict) else ""
            req_pane = (data.get("pane_id") or "").strip() if isinstance(data, dict) else ""
            key = _history_key(req_path, req_pane)
            with _active_cli_procs_lock:
                proc = _active_cli_procs.get(key)
            if proc and proc.poll() is None:
                try:
                    setattr(proc, "_user_stopped", True)
                    _kill_proc_tree(proc)
                except Exception as e:
                    log.warning(f"[STOP] kill failed for key={key!r}: {e}")
                    self._json({"stopped": False, "reason": str(e)}); return
                log.info(f"[STOP] CLI subprocess tree killed by user (key={key!r})")
                self._json({"stopped": True, "key": key})
            else:
                self._json({"stopped": False, "reason": "no active process for that pane", "key": key})

        elif path == "/model":
            global MODEL
            new_model = data.get("model", "")
            allowed = ("claude-sonnet-4-6", "claude-opus-4-8")
            if new_model not in allowed:
                self._json({"error": f"model must be one of {allowed}"}, 400)
                return
            MODEL = new_model
            log.info(f"[MODEL] switched to {MODEL}")
            self._json({"model": MODEL})

        elif path == "/chat":
            message = data.get("message", "")
            project_path = data.get("project_path", "")
            # Per-pane id — every browser chat pane sends one ("main" for the
            # main pane, "ws<N>" for spawned workspace panes). Lets two windows
            # on the same folder keep separate transcripts + subprocesses.
            pane_id = data.get("pane_id", "")
            # Optional per-request provider override. Used by project windows
            # so each window can talk to a different model (e.g. one Codex,
            # one Claude, one Gemini) without changing the global pill.
            req_provider = data.get("provider", "")
            # Per-request agent (each chat pane has its own crew dropdown).
            # Mirrors the /chat_stream handling so project panes can chat
            # with a different officer than the main channel.
            req_crew = data.get("crew", "")
            if req_crew:
                _set_main_chat_crew(req_crew)
            attachments = data.get("attachments") or []
            if not message and not attachments:
                self._json({"error": "no message"}, 400)
                return
            if req_provider and req_provider not in PROVIDERS:
                self._json({"error": f"unknown provider '{req_provider}'"}, 400)
                return
            if attachments:
                err = _validate_attachments(attachments)
                if err:
                    self._json({"error": err}, 400)
                    return
                # Transcribe audio FIRST and fold transcripts into message text;
                # the remaining (non-audio) attachments still need a multimodal
                # provider. Mirrors /chat_stream so the two endpoints stay in
                # sync on what's allowed.
                try:
                    message, attachments, _ = (
                        _transcribe_audio_attachments(message, attachments)
                    )
                except Exception as e:
                    log.exception(f"[chat] audio transcription failed: {e}")
                    self._json({"error": f"Audio transcription failed: {e}"}, 500)
                    return
                effective_pid = req_provider or ACTIVE_PROVIDER
                if attachments and effective_pid not in _MULTIMODAL_PROVIDERS:
                    self._json({"error": (
                        f"Image / PDF / text attachments require a Claude provider. "
                        f"Active provider '{effective_pid}' is text-only — switch this "
                        f"window's model to any Claude option (API or CLI)."
                    )}, 400)
                    return
            log.info(f"/chat message={message!r} project_path={project_path!r} pane_id={pane_id!r} provider={req_provider or '(default)'} attachments={len(attachments)}")
            mark_user_activity()

            # Stage attachments on the per-request thread-local so the runner
            # (ask_hermes / ask_hermes_cli / provider runners) picks them up,
            # matching the /chat_stream flow.
            _request_attachments.list = attachments
            try:
                if req_provider:
                    response = _dispatch_with_provider(req_provider, message, project_path, pane_id)
                else:
                    response = dispatch(message, project_path=project_path, pane_id=pane_id)
            finally:
                _request_attachments.list = []

            log.info(f"/chat response={response!r}")
            self._json({"response": response})

        elif path == "/chat_stream":
            message = data.get("message", "")
            project_path = data.get("project_path", "")
            # Per-pane id — see /chat for rationale. Two windows on the same
            # folder must send distinct pane_ids to avoid sharing history /
            # preempting each other's CLI subprocess.
            pane_id = data.get("pane_id", "")
            # Per-request provider override (same semantics as /chat).
            req_provider = data.get("provider", "")
            # Per-request main-chat agent (panel-header name dropdown).
            req_crew = data.get("crew", "")
            if req_crew:
                _set_main_chat_crew(req_crew)
            attachments = data.get("attachments") or []
            if not message and not attachments:
                self._json({"error": "no message"}, 400)
                return
            if req_provider and req_provider not in PROVIDERS:
                self._json({"error": f"unknown provider '{req_provider}'"}, 400)
                return
            transcript_log_lines: list = []
            if attachments:
                err = _validate_attachments(attachments)
                if err:
                    self._json({"error": err}, 400)
                    return
                # Transcribe audio FIRST and fold transcripts into message text;
                # this happens before provider gating so audio works with every
                # provider, including text-only ones.
                try:
                    message, attachments, transcript_log_lines = (
                        _transcribe_audio_attachments(message, attachments)
                    )
                except Exception as e:
                    log.exception(f"[chat_stream] audio transcription failed: {e}")
                    self._json({"error": f"Audio transcription failed: {e}"}, 500)
                    return
                # Now gate the REMAINING (non-audio) attachments — those still
                # need a multimodal provider.
                effective_pid = req_provider or ACTIVE_PROVIDER
                if attachments and effective_pid not in _MULTIMODAL_PROVIDERS:
                    self._json({"error": (
                        f"Image / PDF / text attachments require a Claude provider. "
                        f"Active provider '{effective_pid}' is text-only — switch to "
                        f"any Claude option (API or CLI) in the model selector."
                    )}, 400)
                    return
            log.info(f"/chat_stream message={message!r} pane_id={pane_id!r} provider={req_provider or '(default)'} attachments={len(attachments)} audio_transcribed={len(transcript_log_lines)}")
            mark_user_activity()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors()
            self.end_headers()

            # Lock prevents interleaved writes from stream thread + keepalive thread
            _write_lock = threading.Lock()
            _stream_done = threading.Event()
            # Last time a *visible* SSE event (thinking/token/meta/error/done)
            # went out. Drives two safety mechanisms:
            #   1. The done-safety-net in _run_stream's outer finally.
            #   2. The liveness-pulse in the keepalive loop — if the runner
            #      goes silent for 30s the Captain gets a "still working" beat
            #      so he can tell the difference between busy and wedged.
            _last_activity_ts = [time.time()]
            _done_sent = [False]

            def send_sse(event_type, text):
                # System Health: feed model output chunks into the engine gauge
                # so token velocity spools up while Data is mid-response.
                if event_type == 'token' and isinstance(text, str):
                    record_stream_chunk(text)
                if event_type == 'done':
                    # Idempotent — drop duplicate done events so the safety
                    # net doesn't double-emit on top of a runner that already
                    # sent one.
                    if _done_sent[0]:
                        return
                    _done_sent[0] = True
                _last_activity_ts[0] = time.time()
                with _write_lock:
                    try:
                        chunk = f"event: {event_type}\ndata: {json.dumps({'text': text})}\n\n"
                        self.wfile.write(chunk.encode('utf-8'))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass

            def _run_stream():
                llm_stream_start()   # bumps System Health LLM in-flight counter
                ok = False           # flipped to True only on clean runner return; drives Shields
                try:
                    # Route conversation_history to the per-(project, pane)
                    # bucket for this thread. Without the pane_id, two panes
                    # pointed at the same folder would share one bucket and
                    # Data would conflate their conversations.
                    _bind_history(project_path, pane_id)
                    pid = req_provider or ACTIVE_PROVIDER
                    log.info(f"[DISPATCH-STREAM] provider={pid} history_key={_history_state.key!r}")
                    runner = _provider_runner(pid)
                    # Wrap send_sse to intercept spawn_workspaces marker blocks
                    # — lets CLI providers (claude-cli, codex, gemini) trigger
                    # the spawn even though they don't see our structured tools.
                    filtered_sse = _marker_filter_sse(send_sse)
                    # Surface any audio transcripts the HTTP layer produced
                    # as thinking lines, so the user sees what Whisper heard
                    # before the LLM starts streaming.
                    for _line in transcript_log_lines:
                        filtered_sse('thinking', _line)
                    prev_id = getattr(_provider_override, "id", None)
                    if req_provider:
                        _provider_override.id = req_provider
                    # Stage attachments for the runner thread (read by
                    # ask_hermes_stream via _request_attachments).
                    _request_attachments.list = attachments
                    try:
                        try:
                            runner(message, project_path, filtered_sse)
                            ok = True
                        except Exception as runner_err:
                            # The runner exited via an uncaught exception
                            # before it could send `done`. Log it loudly and
                            # surface a one-line apology so the Captain sees
                            # something instead of a frozen spinner. The
                            # outer finally then emits the safety-net done.
                            log.exception(f"[DISPATCH-STREAM] runner crashed: {runner_err}")
                            try:
                                send_sse('token', f"\n\nMy neural matrix encountered an internal error, Captain. ({runner_err})")
                            except Exception:
                                pass
                    finally:
                        _request_attachments.list = []
                        try:
                            filtered_sse.finalize()
                        except Exception as fin_err:
                            log.exception(f"[DISPATCH-STREAM] filtered_sse.finalize() raised: {fin_err}")
                        if req_provider:
                            _provider_override.id = prev_id
                finally:
                    # Safety net — if the runner exited (cleanly or via
                    # exception) without sending `done`, send it now so the
                    # frontend's stream loop terminates instead of spinning
                    # forever on keepalives.
                    if not _done_sent[0]:
                        log.warning("[DISPATCH-STREAM] runner exited without 'done' — emitting safety-net done")
                        try:
                            send_sse('done', '')
                        except Exception:
                            pass
                    _record_llm_result(ok)
                    llm_stream_end()
                    _stream_done.set()

            threading.Thread(target=_run_stream, daemon=True).start()

            # Keepalive loop. Two jobs:
            #   1. Every 8s, write an SSE comment so Windows/proxies don't
            #      drop the idle TCP connection.
            #   2. If 30s elapses with no visible SSE event (thinking/token),
            #      emit a "*still working — Ns since last activity*" beat so
            #      the Captain can tell the difference between a busy runner
            #      and a wedged one. Without this, an LLM doing a long tool
            #      chain looks identical to a hang for minutes at a time.
            LIVENESS_IDLE_THRESHOLD = 30.0   # seconds of silence before first beat
            LIVENESS_BEAT_INTERVAL  = 30.0   # seconds between beats once silent
            _next_liveness_beat = [_last_activity_ts[0] + LIVENESS_IDLE_THRESHOLD]
            _stream_t0 = time.time()
            while not _stream_done.wait(timeout=8.0):
                # 1. Keepalive comment.
                with _write_lock:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
                # 2. Liveness pulse — emit a visible beat if the runner has
                #    been silent past the threshold. Skip if we've already
                #    received `done` between waits, or if the connection just
                #    broke above.
                if _done_sent[0]:
                    continue
                now = time.time()
                idle_for = now - _last_activity_ts[0]
                if idle_for >= LIVENESS_IDLE_THRESHOLD and now >= _next_liveness_beat[0]:
                    elapsed_total = now - _stream_t0
                    beat = f"*still working — {int(idle_for)}s since last activity, {int(elapsed_total)}s total*"
                    # Write the beat directly through the wire — bypass
                    # send_sse so we DON'T reset _last_activity_ts. The
                    # runner is what we're watching for liveness, not
                    # our own heartbeats. If the runner stays silent
                    # for 60s, idle_for keeps climbing past 60 and the
                    # beat reflects the real silence the Captain is
                    # waiting through.
                    with _write_lock:
                        try:
                            chunk = f"event: thinking\ndata: {json.dumps({'text': beat})}\n\n"
                            self.wfile.write(chunk.encode('utf-8'))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break
                    # Space the beats so we don't spam — next beat is
                    # BEAT_INTERVAL from now (still gated by the silence
                    # threshold too, so a runner that wakes up resets
                    # the cycle naturally).
                    _next_liveness_beat[0] = now + LIVENESS_BEAT_INTERVAL

        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)


# Voice readiness gate — set when both Whisper and F5-TTS finish loading.
# /voice/full blocks on this so a request that arrives mid-warmup doesn't
# contend with the in-progress model load for GPU memory (which previously
# caused STT to balloon from ~3s to ~50s).
_voice_ready = threading.Event()

def _prevent_system_sleep() -> None:
    """Tell Windows: do not sleep while this process is alive.
    Uses SetThreadExecutionState with ES_CONTINUOUS | ES_SYSTEM_REQUIRED.
    Lock is released automatically when the bridge exits."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS       = 0x80000000
        ES_SYSTEM_REQUIRED  = 0x00000001
        # We only block system sleep, NOT display sleep — the monitor can still
        # turn off / lock; only the kernel power-state stays awake.
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        ret = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if ret:
            log.info("[power] system sleep blocked while bridge is alive")
        else:
            log.warning("[power] SetThreadExecutionState returned 0 — sleep not blocked")
    except Exception as e:
        log.exception(f"[power] failed to block sleep: {e}")


def _start_cloudflared_if_configured() -> None:
    """Spawn cloudflared (Cloudflare Tunnel) detached + silent if a token is
    in the env. Idempotent — skips if cloudflared is already running. Writes
    to dashboard/cloudflared.log so the UI can read the URL via /tunnel_url."""
    token = os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "").strip()
    if not token:
        log.info("[tunnel] CLOUDFLARE_TUNNEL_TOKEN not set — skipping cloudflared")
        return
    # Already running?
    try:
        out = subprocess.check_output(
            ["cmd", "/c", "tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
            text=True, timeout=5,
        )
        if "cloudflared.exe" in out:
            log.info("[tunnel] cloudflared already running — reusing existing process")
            return
    except Exception:
        pass
    # Locate the binary
    cf_paths = [
        Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
        Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
    ]
    import shutil as _sh
    cf_exe = _sh.which("cloudflared") or _sh.which("cloudflared.exe")
    if not cf_exe:
        for p in cf_paths:
            if p.exists(): cf_exe = str(p); break
    if not cf_exe:
        log.warning("[tunnel] cloudflared not found — install via `winget install Cloudflare.cloudflared`")
        return
    log_file = Path(__file__).parent / "cloudflared.log"
    try:
        # Detached + no window so the tunnel survives independent of how the
        # bridge was started (.bat / .vbs / IDE / watchdog).
        DETACHED, NO_WIN = 0x00000008, 0x08000000
        subprocess.Popen(
            [cf_exe, "tunnel", "--no-autoupdate",
             "--logfile", str(log_file),
             "run", "--token", token],
            creationflags=DETACHED | NO_WIN,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        log.info(f"[tunnel] cloudflared launched detached — exe={cf_exe}")
    except Exception as e:
        log.exception(f"[tunnel] failed to start cloudflared: {e}")


VOICE_IDLE_UNLOAD_SECONDS = 900  # release VRAM after 15 min of no voice activity

def _warmup_voice_models():
    """Pre-load Whisper (STT) + F5-TTS in a background thread at startup so the
    Captain does not pay the ~10-90s cold-start on the first voice request of a
    session. `_voice_ready` is set only after both models finish loading — or
    on failure, so the system degrades to lazy-load-on-request rather than
    blocking voice forever. The idle watcher still releases VRAM after
    VOICE_IDLE_UNLOAD_SECONDS of silence; the first request after an
    idle-unload re-pays the cold start."""
    def _bg():
        t0 = time.time()
        try:
            log.info("[warmup] pre-loading voice models (Whisper + F5-TTS)...")
            local_voice.warmup()
            log.info(f"[warmup] voice models ready in {time.time() - t0:.1f}s")
        except Exception as e:
            log.exception(
                f"[warmup] voice model preload failed: {e} — "
                f"falling back to lazy-load on first request")
        finally:
            _voice_ready.set()
    threading.Thread(target=_bg, daemon=True, name="voice-warmup").start()


if __name__ == "__main__":
    # 0.0.0.0 so the dashboard is reachable from phone via Cloudflare Tunnel,
    # Tailscale, or just same-LAN. Auth (X-Data-Token header, if configured)
    # gates the API. Static files / dashboard HTML are served by `/` route.
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"DATA Bridge Server online at http://localhost:{PORT}")
    print(f"Hermes directory: {HERMES_DIR}")
    print(f"Memory file: {MEMORY_FILE}")
    print(f"Voice models pre-loading in background (idle-unload after {VOICE_IDLE_UNLOAD_SECONDS}s)")
    _warmup_voice_models()
    local_voice.start_idle_watcher(idle_seconds=VOICE_IDLE_UNLOAD_SECONDS)

    # Keep Windows awake while Data is running so the tunnel + Telegram bot
    # remain reachable 24/7 even when the Captain isn't using the PC.
    _prevent_system_sleep()

    # Cloudflare Tunnel — spawn detached so the public URL stays up regardless
    # of how the bridge was started (desktop shortcut, watchdog, IDE, etc.)
    _start_cloudflared_if_configured()
    # Standing orders: load from disk, recompute next_run for each, start scheduler
    _load_standing_orders()
    # Auto-seed the daily Potential Upgrades scan if it doesn't exist yet.
    with _orders_lock:
        if not any(o.get("id") == "so-upgrades-refresh" for o in _standing_orders):
            _standing_orders.append({
                "id":       "so-upgrades-refresh",
                "name":     "Potential Upgrades scan",
                "cron":     "0 8 * * *",                 # daily 08:00
                "prompt":   "(internal — scans the web for new AI tools, MCP servers, Claude skills)",
                "provider": "claude-cli",                # placeholder, unused with action
                "enabled":  True,
                "action":   "refresh_upgrades",
                "next_run": 0,
                "last_run": 0,
                "last_result": "",
                "notify_telegram": False,
            })
            log.info("[orders] auto-seeded so-upgrades-refresh (daily 08:00)")
        for _o in _standing_orders:
            _recompute_next_run(_o)
        _save_standing_orders()
    print(f"Loaded {len(_standing_orders)} standing order(s).")
    threading.Thread(target=_scheduler_loop, daemon=True).start()

    # Telegram bot — only starts if TELEGRAM_BOT_TOKEN env is set
    try:
        import telegram_bot
        if telegram_bot.start_bot():
            print("Telegram bot polling started.")
        else:
            print("Telegram bot disabled (no TELEGRAM_BOT_TOKEN).")
    except Exception as _e:
        log.exception(f"[telegram] startup failed: {_e}")

    # Auth status banner so the Captain sees right away whether a token is required
    if DATA_BRIDGE_TOKEN:
        print(f"Auth: token required (X-Data-Token header). Token length: {len(DATA_BRIDGE_TOKEN)}")
    else:
        print("Auth: OPEN — set DATA_BRIDGE_TOKEN env var before exposing publicly.")
    # Browser-coupled shutdown watcher (skip when run as a headless daemon).
    if _LIFECYCLE_MODE != "daemon":
        def _lifecycle_watcher():
            while True:
                time.sleep(5)
                if _last_heartbeat is None:
                    continue  # browser hasn't connected yet — don't shut down on boot
                gap = (datetime.datetime.now() - _last_heartbeat).total_seconds()
                threshold = _LIFECYCLE_LEAVING_GRACE_SECS if _lifecycle_leaving else _LIFECYCLE_GRACE_SECS
                if gap > threshold:
                    log.info(f"[LIFECYCLE] No heartbeat for {gap:.0f}s (leaving={_lifecycle_leaving}) — shutting down")
                    threading.Thread(target=_do_shutdown, daemon=True).start()
                    return
        threading.Thread(target=_lifecycle_watcher, daemon=True).start()
        print(f"Lifecycle: auto-shutdown when browser disconnects (grace {_LIFECYCLE_GRACE_SECS}s)")
    else:
        print("Lifecycle: daemon mode — will NOT auto-shutdown when browser closes")
    print("Press Ctrl+C to shut down.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBridge server offline.")
