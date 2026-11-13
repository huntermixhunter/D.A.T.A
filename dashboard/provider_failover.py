"""Usage-limit failover and the subscription usage meter.

Two jobs:

1. Usage meter. Reads how much of each subscription window is used, at no
   token cost:
   - Claude: the CLI's own `get_usage` control request (no prompt is sent, so
     no turn runs), plus the `rate_limit_event` every bridge turn already
     emits. The CLI handles its own login refresh; this module never reads
     credentials.
   - Codex: the `rate_limits` block Codex writes into its session files under
     ~/.codex/sessions, plus `codex login status`.

2. Failover. When a Claude CLI turn hits its usage limit, the turn is handed
   to Codex with a summary of what Claude had already done, and later turns
   go straight to Codex until Claude's window resets. Codex hitting its limit
   fails over to Claude the same way.

Spend guard: a handoff never targets a provider whose last reading shows a
window at 100%, is signed out, or is already marked limited. Codex accounts
can carry purchasable credits, so this keeps failover from pushing work onto
a provider that may only continue on paid credits. Nothing here buys,
upgrades, or enables metered billing.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

log = logging.getLogger("bridge")

# Reset times without an explicit zone are read in the computer's own local
# time (None = system local), so a buyer anywhere sees the right clock.
DEFAULT_TZ = None

# Which provider takes over when another hits its limit. Tiers map to tiers so
# a fast-model pane stays on a fast model after the switch.
FALLBACK = {
    "claude-cli":         "codex",
    "claude-cli-opus-5":  "codex",
    "claude-cli-opus-48": "codex",
    "claude-cli-fable":   "codex",
    "claude-cli-sonnet":  "codex-sol",
    "claude-cli-haiku":   "codex-luna",
    "codex":              "claude-cli",
    "codex-sol":          "claude-cli-sonnet",
    "codex-luna":         "claude-cli-haiku",
    "codex-mini":         "claude-cli-haiku",
}

FAMILY_LABEL = {"claude": "Claude", "codex": "Codex"}

CLAUDE_POLL_SECS = 300      # get_usage refresh while idle
CODEX_POLL_SECS = 60        # session-file scan (cheap)
CODEX_LOGIN_POLL_SECS = 300
UNKNOWN_RESET_SECS = 15 * 60  # retry window when a reset time cannot be parsed


def family(provider_id: str) -> str | None:
    pid = provider_id or ""
    if pid.startswith("claude-cli"):
        return "claude"
    if pid.startswith("codex"):
        return "codex"
    return None


# ── Limit-message detection ─────────────────────────────────────────────────

# Real Claude Code messages seen in the conversation archive:
#   You've hit your session limit · resets 6:10am (America/Los_Angeles)
#   You've hit your 5-hour limit · resets 2:30am (America/Los_Angeles)
#   You've hit your weekly limit · resets Jul 2, 8am (America/Los_Angeles)
#   You've hit your monthly spend limit · raise it at claude.ai/settings/usage
#     ?from=cc_cli_limit_message · your session limit resets 9:50pm (...)
_CLAUDE_LIMIT_RE = re.compile(
    r"^\s*You(?:'|’)ve hit your (?P<kind>[A-Za-z0-9 \-]{1,32}?) limit\b", re.I)
_CLAUDE_LEGACY_RE = re.compile(r"^\s*Claude AI usage limit reached\|(?P<ts>\d{9,11})", re.I)
# Codex: "You've hit your usage limit. ... try again at Nov 12th, 2026 3:45 AM."
_CODEX_LIMIT_RE = re.compile(r"You(?:'|’)ve hit your usage limit|usage_limit_reached", re.I)
_RESETS_RE = re.compile(
    r"resets\s+(?P<when>[^()·\n]+?)\s*(?:\((?P<tz>[A-Za-z_]+/[A-Za-z_/+\-]+|UTC)\))?\s*(?:$|[·\n\"])",
    re.I)
_TRY_AGAIN_AT_RE = re.compile(r"try again at (?P<when>[^\n]+?)\.?\s*$", re.I)
_TRY_AGAIN_IN_RE = re.compile(r"try again in (?P<span>[0-9 a-z,]+)", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _tz(name: str | None):
    """ZoneInfo for `name`, or None (system local time) when no zone is
    given, the zone is unknown, or tz data is missing on this machine."""
    name = name or DEFAULT_TZ
    if ZoneInfo is None or not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def parse_reset_text(when: str, tz_name: str | None = None, now: float | None = None) -> float | None:
    """Turn '6:10am', '8am', 'Jul 2, 8am' or 'Nov 12th, 2026 3:45 AM' into an
    epoch. A bare time is the next time that clock reading comes round."""
    if not when:
        return None
    tz = _tz(tz_name)
    now_dt = datetime.datetime.fromtimestamp(now if now is not None else time.time(), tz)
    s = when.strip().lower().replace(",", " ")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"\bat\b", " ", s)
    m = re.search(
        r"(?:(?P<mon>[a-z]{3,9})\.?\s+(?P<day>\d{1,2})\s+(?:(?P<year>\d{4})\s+)?)?"
        r"(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm)\b", s)
    if not m:
        return None
    hour = int(m.group("h")) % 12 + (12 if m.group("ap") == "pm" else 0)
    minute = int(m.group("mi") or 0)
    if m.group("mon"):
        mon = _MONTHS.get(m.group("mon")[:3])
        if not mon:
            return None
        year = int(m.group("year") or now_dt.year)
        try:
            cand = now_dt.replace(year=year, month=mon, day=int(m.group("day")),
                                  hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if not m.group("year") and cand < now_dt - datetime.timedelta(days=2):
            cand = cand.replace(year=cand.year + 1)
    else:
        cand = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand <= now_dt:
            cand += datetime.timedelta(days=1)
    return cand.timestamp()


def _parse_span(span: str) -> float | None:
    total = 0
    for n, unit in re.findall(r"(\d+)\s*(day|hour|hr|minute|min|second|sec)", span.lower()):
        total += int(n) * {"day": 86400, "hour": 3600, "hr": 3600, "minute": 60,
                           "min": 60, "second": 1, "sec": 1}[unit]
    return float(total) if total else None


def match_limit(text: str, fam: str, now: float | None = None) -> dict | None:
    """Return {kind, resets_at, label} if `text` is a usage-limit message from
    that provider family, else None. Claude matches are anchored to the start
    of the text block so a reply that merely quotes the message (inside a
    longer answer) never trips failover."""
    if not text:
        return None
    now = time.time() if now is None else now
    if fam == "claude":
        legacy = _CLAUDE_LEGACY_RE.match(text)
        if legacy:
            return {"kind": "usage", "resets_at": float(legacy.group("ts")), "label": "usage limit"}
        m = _CLAUDE_LIMIT_RE.match(text)
        if not m or len(text.strip()) > 400:
            return None
        kind = m.group("kind").strip().lower()
        r = _RESETS_RE.search(text + "\n")
        resets_at = parse_reset_text(r.group("when"), r.group("tz"), now) if r else None
        return {"kind": kind, "resets_at": resets_at, "label": f"{kind} limit"}
    if fam == "codex":
        if not _CODEX_LIMIT_RE.search(text):
            return None
        resets_at = None
        at = _TRY_AGAIN_AT_RE.search(text.strip())
        if at:
            resets_at = parse_reset_text(at.group("when"), None, now)
        if resets_at is None:
            span = _TRY_AGAIN_IN_RE.search(text)
            secs = _parse_span(span.group("span")) if span else None
            if secs:
                resets_at = now + secs
        return {"kind": "usage", "resets_at": resets_at, "label": "usage limit"}
    return None


# ── Persistent state ────────────────────────────────────────────────────────

_lock = threading.RLock()
_state_file: Path | None = None
_state = {
    "enabled": True,
    # family -> {"until": epoch, "kind": str, "since": epoch}
    "limited": {},
    "last_failover": None,
}
_meter = {"claude": {}, "codex": {}}


def init(state_file: Path) -> None:
    """Load persisted state. Called once at bridge startup."""
    global _state_file
    _state_file = Path(state_file)
    try:
        data = json.loads(_state_file.read_text(encoding="utf-8"))
        with _lock:
            _state["enabled"] = bool(data.get("enabled", True))
            _state["limited"] = {k: v for k, v in (data.get("limited") or {}).items()
                                 if k in ("claude", "codex") and isinstance(v, dict)}
            _state["last_failover"] = data.get("last_failover")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"[FAILOVER] state load failed: {e}")


def _save() -> None:
    if not _state_file:
        return
    try:
        with _lock:
            blob = json.dumps(_state, indent=2)
        tmp = _state_file.with_suffix(".tmp")
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, _state_file)
    except Exception as e:
        log.warning(f"[FAILOVER] state save failed: {e}")


def enabled() -> bool:
    return bool(_state.get("enabled", True))


def set_enabled(on: bool) -> None:
    with _lock:
        _state["enabled"] = bool(on)
    _save()


def mark_limited(fam: str, kind: str, resets_at: float | None) -> None:
    now = time.time()
    until = resets_at if resets_at and resets_at > now else now + UNKNOWN_RESET_SECS
    with _lock:
        _state["limited"][fam] = {"until": until, "kind": kind, "since": now,
                                  "reset_known": bool(resets_at)}
    log.warning(f"[FAILOVER] {fam} limited ({kind}) until {until:.0f}")
    _save()


def clear_limited(fam: str, why: str = "") -> None:
    with _lock:
        if _state["limited"].pop(fam, None) is None:
            return
    log.info(f"[FAILOVER] {fam} limit cleared {why}".rstrip())
    _save()


def limited_until(fam: str, now: float | None = None) -> float | None:
    """Epoch until which the family is known to be limited, or None. An
    expired entry is cleared as a side effect."""
    now = time.time() if now is None else now
    with _lock:
        entry = _state["limited"].get(fam)
    if not entry:
        return None
    until = float(entry.get("until") or 0)
    if until <= now:
        clear_limited(fam, "(reset time passed)")
        return None
    return until


# ── Usage meter ─────────────────────────────────────────────────────────────

def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _iso_to_epoch(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_CLAUDE_WINDOW_LABELS = [
    ("five_hour", "5-HOUR"), ("seven_day", "WEEKLY"),
    ("seven_day_opus", "OPUS WK"), ("seven_day_sonnet", "SONNET WK"),
]


def _set_meter(fam: str, **fields) -> None:
    with _lock:
        _meter[fam] = {**_meter.get(fam, {}), **fields}


def _maybe_clear_from_meter(fam: str) -> None:
    """Switch back automatically once a fresh reading taken after the limit
    shows every window under 100%."""
    with _lock:
        entry = _state["limited"].get(fam)
        m = dict(_meter.get(fam) or {})
    if not entry or not m.get("windows"):
        return
    if float(m.get("updated_at") or 0) <= float(entry.get("since") or 0):
        return
    if all(not w.get("live") or w.get("pct", 0) < 100 for w in m["windows"]):
        clear_limited(fam, "(meter shows room again)")


def note_claude_usage(rate_limits: dict, *, extra_usage: dict | None = None,
                      source: str = "cli") -> None:
    """Record Claude windows from a get_usage answer (`rate_limits`)."""
    windows = []
    for key, label in _CLAUDE_WINDOW_LABELS:
        w = (rate_limits or {}).get(key)
        if not isinstance(w, dict) or w.get("utilization") is None:
            continue
        windows.append({"id": key, "label": label, "pct": round(float(w["utilization"])),
                        "resets_at": _iso_to_epoch(w.get("resets_at")), "live": True})
    fields = {"windows": windows, "updated_at": time.time(), "source": source,
              "signed_in": True, "error": None}
    if extra_usage is not None:
        fields["extra_usage_enabled"] = bool(extra_usage.get("is_enabled"))
    _set_meter("claude", **fields)
    _maybe_clear_from_meter("claude")


def note_claude_rate_event(info: dict) -> dict | None:
    """Record a `rate_limit_event` from a live Claude turn. Returns a limit
    dict when the event says the request was rejected."""
    if not isinstance(info, dict):
        return None
    uw = info.get("unifiedWindows") or {}
    if uw:
        windows = []
        for key, label in _CLAUDE_WINDOW_LABELS:
            w = uw.get(key)
            if not isinstance(w, dict) or w.get("utilization") is None:
                continue
            u = float(w["utilization"])
            pct = u * 100 if u <= 1.0 else u   # events report 0-1, get_usage 0-100
            windows.append({"id": key, "label": label, "pct": round(pct),
                            "resets_at": _iso_to_epoch(w.get("resetsAt")), "live": True})
        if windows:
            _set_meter("claude", windows=windows, updated_at=time.time(), source="turn",
                       signed_in=True, error=None)
    if str(info.get("status", "")).lower() == "rejected":
        kind = str(info.get("rateLimitType") or "usage").replace("_", "-")
        kind = {"five-hour": "5-hour", "seven-day": "weekly"}.get(kind, kind)
        return {"kind": kind, "resets_at": _iso_to_epoch(info.get("resetsAt")),
                "label": f"{kind} limit"}
    _maybe_clear_from_meter("claude")
    return None


def refresh_claude(exe: str | None = None, timeout: float = 25.0) -> dict:
    """Ask the Claude CLI for usage via its control protocol. No prompt is
    sent, so no turn runs and no tokens are spent."""
    exe = exe or shutil.which("claude") or "claude"
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = None
    try:
        proc = subprocess.Popen(
            [exe, "--print", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            cwd=tempfile.gettempdir(), env=env, creationflags=_no_window_flags())
        killer = threading.Timer(timeout, lambda: proc.kill())
        killer.daemon = True
        killer.start()
        try:
            for rid, req in (("data-u1", {"subtype": "initialize"}),
                             ("data-u2", {"subtype": "get_usage", "skip_behaviors": True})):
                proc.stdin.write(json.dumps({"type": "control_request", "request_id": rid,
                                             "request": req}) + "\n")
            proc.stdin.flush()
            answer = None
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "control_response":
                    continue
                resp = ev.get("response") or {}
                if resp.get("request_id") == "data-u2":
                    answer = resp
                    break
        finally:
            killer.cancel()
        if not answer or answer.get("subtype") != "success":
            err = (answer or {}).get("error") or "no usage answer from the Claude CLI"
            _set_meter("claude", error=str(err)[:200], checked_at=time.time())
            return snapshot()["claude"]
        body = answer.get("response") or {}
        if not body.get("rate_limits_available", True) or not body.get("rate_limits"):
            _set_meter("claude", windows=[], updated_at=time.time(), source="cli",
                       error="this login has no subscription limits to read")
        else:
            note_claude_usage(body["rate_limits"], extra_usage=body["rate_limits"].get("extra_usage"))
        _set_meter("claude", plan=body.get("subscription_type") or "", checked_at=time.time())
    except FileNotFoundError:
        _set_meter("claude", error="Claude CLI not installed", signed_in=False, checked_at=time.time())
    except Exception as e:
        log.warning(f"[FAILOVER] claude usage refresh failed: {e}")
        _set_meter("claude", error=str(e)[:200], checked_at=time.time())
    finally:
        if proc:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    return snapshot()["claude"]


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _newest_codex_sessions(limit: int = 6) -> list[Path]:
    """Newest rollout files, walking only the newest date folders."""
    root = _codex_home() / "sessions"
    out: list[Path] = []

    def _desc_dirs(p: Path):
        try:
            return sorted((d for d in p.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
        except OSError:
            return []

    for y in _desc_dirs(root):
        for mo in _desc_dirs(y):
            for d in _desc_dirs(mo):
                try:
                    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
                except OSError:
                    files = []
                out.extend(files)
                if len(out) >= limit:
                    return out[:limit]
    return out


def _last_codex_rate_limits(path: Path) -> tuple[dict | None, float | None]:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 512 * 1024))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None, None
    for line in reversed(tail.splitlines()):
        if '"rate_limits"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        rl = (ev.get("payload") or {}).get("rate_limits")
        if isinstance(rl, dict) and (rl.get("primary") or rl.get("secondary")):
            return rl, _iso_to_epoch(ev.get("timestamp"))
    return None, None


def _codex_window_label(minutes) -> str:
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return "WINDOW"
    if m == 300:
        return "5-HOUR"
    if m == 10080:
        return "WEEKLY"
    return f"{m // 60}-HOUR" if m < 1440 else f"{m // 1440}-DAY"


def note_codex_rate_limits(rl: dict, read_at: float | None, now: float | None = None) -> None:
    now = time.time() if now is None else now
    windows = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict) or w.get("used_percent") is None:
            continue
        resets_at = _iso_to_epoch(w.get("resets_at"))
        if resets_at is None and w.get("resets_in_seconds") is not None and read_at:
            resets_at = read_at + float(w["resets_in_seconds"])
        live = not (resets_at and resets_at <= now)
        windows.append({"id": key, "label": _codex_window_label(w.get("window_minutes")),
                        # A window that has reset since the reading is back to 0.
                        "pct": round(float(w["used_percent"])) if live else 0,
                        "resets_at": resets_at if live else None, "live": live})
    credits = rl.get("credits") or {}
    _set_meter("codex", windows=windows, updated_at=read_at or now, source="session",
               credits_available=bool(credits.get("has_credits")),
               limit_reached=bool(rl.get("rate_limit_reached_type")))
    _maybe_clear_from_meter("codex")


def refresh_codex(exe: str | None = None, check_login: bool = True) -> dict:
    if check_login:
        exe = exe or shutil.which("codex") or shutil.which("codex.cmd")
        if not exe:
            _set_meter("codex", signed_in=False, error="Codex CLI not installed")
        else:
            try:
                r = subprocess.run([exe, "login", "status"], capture_output=True, text=True,
                                   timeout=20, encoding="utf-8", errors="replace",
                                   creationflags=_no_window_flags())
                out = (r.stdout + r.stderr).strip()
                signed_in = r.returncode == 0 and "not logged in" not in out.lower()
                _set_meter("codex", signed_in=signed_in, login_detail=out[:120],
                           error=None if signed_in else "signed out: run `codex login`",
                           login_checked_at=time.time())
            except Exception as e:
                _set_meter("codex", error=f"login check failed: {e}"[:200])
    for path in _newest_codex_sessions():
        rl, ts = _last_codex_rate_limits(path)
        if rl:
            note_codex_rate_limits(rl, ts)
            break
    _set_meter("codex", checked_at=time.time())
    return snapshot()["codex"]


def _meter_blocks(fam: str) -> str | None:
    """Reason the family cannot take a handoff, or None if it can."""
    with _lock:
        m = dict(_meter.get(fam) or {})
    if m.get("signed_in") is False:
        return f"{FAMILY_LABEL[fam]} is signed out"
    full = [w for w in m.get("windows") or [] if w.get("live") and w.get("pct", 0) >= 100]
    if full:
        return f"{FAMILY_LABEL[fam]} {full[0]['label'].lower()} window is at 100%"
    if fam == "codex" and m.get("limit_reached"):
        return "Codex reports its limit is reached"
    return None


def snapshot() -> dict:
    now = time.time()
    with _lock:
        meter = json.loads(json.dumps(_meter))
        st = json.loads(json.dumps(_state))
    limited = {}
    for fam, entry in (st.get("limited") or {}).items():
        if float(entry.get("until") or 0) > now:
            limited[fam] = entry
    for fam in ("claude", "codex"):
        meter[fam]["limited"] = limited.get(fam)
        meter[fam]["blocked_reason"] = _meter_blocks(fam)
    return {"enabled": bool(st.get("enabled", True)), "claude": meter["claude"],
            "codex": meter["codex"], "last_failover": st.get("last_failover"), "now": now}


# ── Background poller ───────────────────────────────────────────────────────

_poller_started = False


def start_poller(claude_exe_fn=None, codex_exe_fn=None) -> None:
    global _poller_started
    if _poller_started:
        return
    _poller_started = True

    def _loop():
        last_claude = last_login = 0.0
        while True:
            now = time.time()
            try:
                c_upd = float(_meter["claude"].get("updated_at") or 0)
                if now - max(last_claude, c_upd) >= CLAUDE_POLL_SECS:
                    last_claude = now
                    refresh_claude(claude_exe_fn() if claude_exe_fn else None)
            except Exception as e:
                log.warning(f"[FAILOVER] claude poll failed: {e}")
            try:
                do_login = now - last_login >= CODEX_LOGIN_POLL_SECS
                if do_login:
                    last_login = now
                refresh_codex(codex_exe_fn() if codex_exe_fn else None, check_login=do_login)
            except Exception as e:
                log.warning(f"[FAILOVER] codex poll failed: {e}")
            for fam in ("claude", "codex"):
                limited_until(fam)   # expire passed entries
            time.sleep(CODEX_POLL_SECS)

    threading.Thread(target=_loop, name="usage-meter", daemon=True).start()


def refresh_async(fam: str, exe_fn=None) -> None:
    fn = refresh_claude if fam == "claude" else refresh_codex
    threading.Thread(target=lambda: fn(exe_fn() if exe_fn else None),
                     name=f"usage-refresh-{fam}", daemon=True).start()


# ── Per-turn context and routing ────────────────────────────────────────────

_tl = threading.local()


class TurnCtx:
    def __init__(self, provider: str, allow_handoff: bool, can_take: callable):
        self.provider = provider
        self.family = family(provider)
        self.allow_handoff = allow_handoff
        self._can_take = can_take
        self.limit: dict | None = None
        self.handoff_to: str | None = None
        self.skip_reason: str | None = None
        self.tokens: list[str] = []
        self.tools: list[str] = []

    def record_limit(self, lim: dict) -> None:
        if self.limit is not None:
            return
        self.limit = lim
        if self.family:
            mark_limited(self.family, lim.get("kind", "usage"), lim.get("resets_at"))
        if not self.allow_handoff:
            self.skip_reason = "this turn is already a failover turn"
            return
        if not enabled():
            self.skip_reason = "failover is switched off in Settings"
            return
        target = FALLBACK.get(self.provider)
        if not target:
            self.skip_reason = "this model has no failover partner"
            return
        reason = self._can_take(target)
        if reason:
            self.skip_reason = reason
            return
        self.handoff_to = target


def current() -> TurnCtx | None:
    return getattr(_tl, "ctx", None)


def claim_limit_text(text: str) -> bool:
    """Runner hook for an assistant text block or error string. Returns True
    when the text is a limit message that should be suppressed because the
    turn is being handed off."""
    ctx = current()
    if ctx is None or not ctx.family:
        return False
    lim = match_limit(text, ctx.family)
    if not lim:
        return False
    ctx.record_limit(lim)
    return ctx.handoff_to is not None


def claim_rate_event(info: dict) -> bool:
    """Runner hook for a Claude `rate_limit_event`. Returns True when the
    event rejected the request and the turn will be handed off."""
    lim = note_claude_rate_event(info)
    ctx = current()
    if lim and ctx is not None and ctx.family == "claude":
        ctx.record_limit(lim)
    return bool(ctx and ctx.handoff_to)


def handing_off() -> bool:
    ctx = current()
    return bool(ctx and ctx.handoff_to)


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return ""
    tz = _tz(DEFAULT_TZ)
    dt = datetime.datetime.fromtimestamp(ts, tz)
    now = datetime.datetime.now(tz)
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now.date():
        return clock
    if (dt.date() - now.date()).days == 1:
        return f"tomorrow {clock}"
    return f"{dt.strftime('%a %b')} {dt.day}, {clock}"


def _handoff_message(ctx: TurnCtx, original: str, from_label: str) -> str:
    if not ctx.tools and not "".join(ctx.tokens).strip():
        return original
    parts = [f"[FAILOVER HANDOFF: {from_label} reached its {ctx.limit.get('label', 'usage limit')} "
             "partway through the user's request below, so you are taking over the same task."]
    if ctx.tools:
        parts.append("Steps it had already taken this turn (oldest first):")
        parts.extend(f"- {t}" for t in ctx.tools[-40:])
    said = "".join(ctx.tokens).strip()
    if said:
        parts.append("What it had already written to the user:")
        parts.append(said[-4000:])
    parts.append("Continue from where it stopped. Do not redo completed steps; check the current "
                 "state of any files or systems you rely on, then finish the task. Do not quote "
                 "this note back to the user.]")
    return "\n".join(parts) + "\n\n" + original


_SKIP_STEP_PREFIXES = ("→", "->", "Session:", "*Reasoning")


def _step_line(text) -> str | None:
    """DATA runners report each tool call as a `thinking` line ("Read
    bridge_server.py", "Running shell: ..."). Keep those for the handoff note;
    drop tool-result previews and status chatter."""
    if not isinstance(text, str):
        return None
    line = " ".join(text.split())
    if not line or line.startswith(_SKIP_STEP_PREFIXES):
        return None
    return line[:240]


def _tool_line(payload) -> str | None:
    if not isinstance(payload, dict) or payload.get("phase") != "call":
        return None
    name = payload.get("name") or "tool"
    inp = payload.get("input") or ""
    if not isinstance(inp, str):
        inp = json.dumps(inp, ensure_ascii=False, default=str)
    inp = " ".join(inp.split())
    return f"{name} {inp[:220]}" if inp else name


def run_turn(provider_id: str, message: str, project_path: str, sse, *,
             runner_for, set_provider, provider_available, history=None,
             save_history=None, announce: bool = True) -> str:
    """Run one turn on `provider_id`, routing around a known limit first and
    handing the turn to the partner provider if the limit hits mid-turn.

    `runner_for(pid)` returns the bridge runner, `set_provider(pid)` pins the
    thread's provider and returns the previous value, `provider_available(pid)`
    says whether its CLI is installed. Returns the provider that answered last.
    """
    fam = family(provider_id)
    if fam is None:
        runner_for(provider_id)(message, project_path, sse)
        return provider_id

    def can_take(target: str) -> str | None:
        tfam = family(target)
        if not provider_available(target):
            return f"{FAMILY_LABEL.get(tfam, target)} is not installed"
        if limited_until(tfam):
            return f"{FAMILY_LABEL[tfam]} is also at its limit"
        return _meter_blocks(tfam)

    def note(text: str) -> None:
        if announce:
            sse("token", text)
        else:
            sse("thinking", text.strip().strip("*"))

    # 1. Known limit: route straight to the partner until the reset.
    pid = provider_id
    until = limited_until(fam) if enabled() else None
    if until:
        target = FALLBACK.get(provider_id)
        reason = can_take(target) if target else "no failover partner"
        if target and not reason:
            entry = _state["limited"].get(fam) or {}
            when = _fmt_time(until) if entry.get("reset_known") else ""
            note(f"*{FAMILY_LABEL[fam]} is at its {entry.get('kind', 'usage')} limit"
                 f"{' until ' + when if when else ''}, so {FAMILY_LABEL[family(target)]} "
                 f"is answering.*\n\n")
            pid = target
            _record_failover(provider_id, target, "routed (limit active)")

    # 2. Run, watching for a limit that hits mid-turn.
    ctx = TurnCtx(pid, allow_handoff=(pid == provider_id), can_take=can_take)
    held: list[tuple[str, object]] = []

    def proxy(event_type, text):
        if event_type == "token" and isinstance(text, str):
            ctx.tokens.append(text)
        elif event_type == "tool":
            line = _tool_line(text)
            if line:
                ctx.tools.append(line)
        elif event_type == "thinking":
            line = _step_line(text)
            if line and (not ctx.tools or ctx.tools[-1] != line):
                ctx.tools.append(line)
        # Hold the closing events once a limit is seen so a handoff or an
        # explanation can still be appended to the same reply.
        if ctx.limit is not None and event_type in ("meta", "done"):
            held.append((event_type, text))
            return
        sse(event_type, text)

    prev_ctx = getattr(_tl, "ctx", None)
    _tl.ctx = ctx
    prev_pid = set_provider(pid)
    try:
        runner_for(pid)(message, project_path, proxy)
    finally:
        set_provider(prev_pid)
        _tl.ctx = prev_ctx
    if family(pid) == "codex":
        refresh_async("codex")

    if ctx.limit is None:
        return pid

    from_label = FAMILY_LABEL[ctx.family]
    when = _fmt_time(ctx.limit.get("resets_at"))
    if not ctx.handoff_to:
        sse("token", f"\n\n*{from_label} hit its {ctx.limit.get('label', 'usage limit')}"
                     f"{' (resets ' + when + ')' if when else ''}. Failover skipped: "
                     f"{ctx.skip_reason}.*")
        for ev, txt in held:
            sse(ev, txt)
        return pid

    # 3. Mid-turn handoff.
    target = ctx.handoff_to
    tlabel = FAMILY_LABEL[family(target)]
    partial = "".join(ctx.tokens).strip()
    note(f"{chr(10) * 2 if partial else ''}*{from_label} hit its {ctx.limit.get('label', 'usage limit')}"
         f"{' (resets ' + when + ')' if when else ''}. {tlabel} is picking up from here.*\n\n")
    _record_failover(pid, target, f"mid-turn {ctx.limit.get('label', 'limit')}")
    handoff_msg = _handoff_message(ctx, message, from_label)

    sub = TurnCtx(target, allow_handoff=False, can_take=can_take)
    _tl.ctx = sub
    prev_pid = set_provider(target)
    try:
        runner_for(target)(handoff_msg, project_path, sse)
    finally:
        set_provider(prev_pid)
        _tl.ctx = prev_ctx
    if family(target) == "codex":
        refresh_async("codex")

    # Keep the saved history readable: the user's own words, and the
    # partial answer joined to the continuation.
    if history is not None:
        try:
            if (len(history) >= 2 and isinstance(history[-2], dict)
                    and history[-2].get("role") == "user"
                    and history[-2].get("content") == handoff_msg):
                history[-2] = {"role": "user", "content": message}
                if partial and isinstance(history[-1], dict):
                    history[-1] = {"role": "assistant", "content":
                                   f"{partial}\n\n[{from_label} reached its limit here; "
                                   f"{tlabel} continued.]\n\n{history[-1].get('content', '')}"}
                if save_history:
                    save_history()
        except Exception as e:
            log.warning(f"[FAILOVER] history tidy failed: {e}")
    return target


def _record_failover(src: str, dst: str, why: str) -> None:
    with _lock:
        _state["last_failover"] = {"at": time.time(), "from": src, "to": dst, "why": why}
    log.warning(f"[FAILOVER] {src} -> {dst}: {why}")
    _save()
