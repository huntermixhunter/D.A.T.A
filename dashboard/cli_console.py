"""
In-chat console — run a small, fixed set of CLI sign-in / status / install
commands straight from a DATA chat pane.

Why this exists
---------------
A buyer who has just installed DATA often still needs to sign their AI CLI in
(Claude Code, Codex, Gemini). Before this module, the only route was "open
PowerShell and type `claude`, then /login", which is exactly the step new buyers
get stuck on. Typing `/login` into DATA's chat did nothing, because the chat
forwards text to the (logged-out) model.

What it is NOT
--------------
This is deliberately NOT a general shell. A chat box that runs arbitrary
commands is remote code execution the moment the dashboard is reachable through
the Cloudflare tunnel, and the bridge answers every Origin with CORS `*`. So:

  * Only the commands in `parse_command` below can run. Anything else is
    rejected with a help message. The executable is always resolved by the
    bridge (never taken from user text) and arguments are a fixed list.
  * The HTTP layer (bridge_server.py `_console_gate`) additionally refuses any
    request that did not originate on this machine, came through a proxy or
    tunnel, or came from a web page other than the dashboard itself.

Two run modes
-------------
  pipe      The command runs headless with stdin/stdout piped. Output streams
            into a console card in the chat; the buyer's next chat message is
            written to the command's stdin (that covers "Paste code here if
            prompted >" from `claude auth login`).
  terminal  Full-screen TUI programs (plain `gemini`, plain `claude`) cannot
            render inside a chat bubble. For those we open a real console
            window on the buyer's machine running that one allowlisted command.

Pure standard library; no third-party deps (the retail runtime is embeddable
Python).
"""

from __future__ import annotations

import codecs
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Families and the npm package each one installs from.
# ─────────────────────────────────────────────────────────────────────────────

FAMILIES = {
    "claude": {"name": "Claude Code", "provider": "claude-cli",
               "npm": "@anthropic-ai/claude-code"},
    "codex":  {"name": "Codex",       "provider": "codex",
               "npm": "@openai/codex"},
    "gemini": {"name": "Gemini CLI",  "provider": "gemini",
               "npm": "@google/gemini-cli"},
}

_NPM_TO_FAMILY = {v["npm"]: k for k, v in FAMILIES.items()}

# Seconds before a piped command is force-stopped. Sign-in waits on a human in a
# browser, so it gets generous time; status/version should be instant.
_TIMEOUTS = {"login": 900, "install": 900, "default": 120}

# Strip ANSI colour / cursor sequences and OSC hyperlinks so output reads
# cleanly in a <pre>. Covers CSI (ESC[ ... letter), OSC (ESC] ... BEL/ST) and
# lone two-char escapes.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def family_for_provider(provider_id: str) -> str:
    """Map a DATA provider id (claude-cli-sonnet, codex-luna, gemini, ...) to a
    CLI family key, or '' for non-CLI providers (Ollama, etc.)."""
    pid = (provider_id or "").lower()
    if pid.startswith("claude"):
        return "claude"
    if pid.startswith("codex"):
        return "codex"
    if pid.startswith("gemini"):
        return "gemini"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Parsing — the allowlist lives here and ONLY here.
# ─────────────────────────────────────────────────────────────────────────────

def _spec(kind: str, family: str, args: list, mode: str = "pipe",
          action: str = "default", label: str = "") -> dict:
    return {
        "kind": kind,          # "cli" (run the family's own exe) | "npm" | "help"
        "family": family,
        "args": list(args),
        "mode": mode,          # "pipe" | "terminal"
        "action": action,      # "login" | "logout" | "status" | "version" | "install" | "update" | "default"
        "label": label,
    }


def help_text() -> str:
    return (
        "Commands you can run here (start with `!`, or just type them):\n"
        "  claude login        Sign in to Claude Code (opens your browser)\n"
        "  claude logout       Sign out of Claude Code\n"
        "  claude status       Show whether Claude Code is signed in\n"
        "  codex login         Sign in to Codex with your ChatGPT account\n"
        "  codex logout | codex status\n"
        "  gemini login        Opens a terminal window to sign in to Gemini\n"
        "  install claude | install codex | install gemini\n"
        "  claude --version | codex --version | gemini --version\n"
        "  /login              Sign in to the CLI this window is using\n"
        "This box only runs sign-in and setup commands. It is not a general terminal."
    )


def parse_command(text: str, pane_provider: str = "") -> Optional[dict]:
    """Turn chat text into an allowlisted command spec, or None if the text is
    not a recognised command. Callers treat None as 'not a command': a message
    with an explicit `!` prefix gets the help text; one without falls through to
    the normal chat.

    Accepted shapes (case-insensitive, extra whitespace ignored):
      [!]claude | claude login | claude /login | claude auth login [--console|--sso|--claudeai]
      [!]claude logout | claude auth logout | claude /logout
      [!]claude status | claude auth status
      [!]claude --version | claude -v | claude version | claude update
      [!]claude terminal                       (opens a real console running claude)
      [!]codex | codex login [--device-auth] | codex logout | codex status | codex login status
      [!]codex --version | codex terminal
      [!]gemini | gemini login | gemini /login | gemini /auth | gemini terminal   → terminal
      [!]gemini --version
      [!]install claude|codex|gemini
      [!]npm i|install -g <one of the three packages>[@latest]
      /login | /logout | /status               (family from the pane's provider)
      !help | !?
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or "\n" in raw or len(raw) > 160:
        return None
    if raw.startswith("!"):
        raw = raw[1:].strip()
    try:
        toks = shlex.split(raw, posix=True)
    except ValueError:
        return None
    if not toks:
        return None
    toks = [t.lower() for t in toks]
    head, rest = toks[0], toks[1:]

    if head in ("help", "?", "commands") and not rest:
        return _spec("help", "", [], action="help", label="help")

    # Bare slash commands → the family of the CLI this pane is talking to.
    if head in ("/login", "/logout", "/status") and not rest:
        fam = family_for_provider(pane_provider) or "claude"
        return parse_command(f"{fam} {head[1:]}")

    # "install claude" / "install codex" / "install gemini"
    if head == "install" and len(rest) == 1 and rest[0] in FAMILIES:
        fam = rest[0]
        pkg = FAMILIES[fam]["npm"]
        return _spec("npm", fam, ["install", "-g", pkg], action="install",
                     label=f"npm install -g {pkg}")

    # npm i -g <known package>
    if head == "npm" and len(rest) == 3 and rest[0] in ("i", "install") and rest[1] in ("-g", "--global"):
        pkg = rest[2]
        if pkg.endswith("@latest"):
            pkg = pkg[: -len("@latest")]
        fam = _NPM_TO_FAMILY.get(pkg)
        if fam:
            return _spec("npm", fam, ["install", "-g", pkg], action="install",
                         label=f"npm install -g {pkg}")
        return None

    if head not in FAMILIES:
        return None
    fam = head
    joined = " ".join(rest)

    if fam == "claude":
        login_flags = [t for t in rest if t in ("--console", "--sso", "--claudeai")]
        core = [t for t in rest if t not in login_flags]
        cj = " ".join(core)
        if cj in ("", "login", "/login", "auth login"):
            return _spec("cli", fam, ["auth", "login", *login_flags], action="login",
                         label="claude auth login" + ("".join(" " + f for f in login_flags)))
        if login_flags:
            return None
        if cj in ("logout", "/logout", "auth logout"):
            return _spec("cli", fam, ["auth", "logout"], action="logout", label="claude auth logout")
        if cj in ("status", "/status", "auth status", "whoami"):
            return _spec("cli", fam, ["auth", "status", "--text"], action="status", label="claude auth status")
        if cj in ("--version", "-v", "version"):
            return _spec("cli", fam, ["--version"], action="version", label="claude --version")
        if cj in ("update", "upgrade"):
            return _spec("cli", fam, ["update"], action="update", label="claude update")
        if cj in ("terminal", "open", "interactive"):
            return _spec("cli", fam, [], mode="terminal", action="login", label="claude")
        return None

    if fam == "codex":
        if joined in ("", "login", "/login"):
            return _spec("cli", fam, ["login"], action="login", label="codex login")
        if joined in ("login --device-auth", "login device", "device-auth"):
            return _spec("cli", fam, ["login", "--device-auth"], action="login",
                         label="codex login --device-auth")
        if joined in ("logout", "/logout"):
            return _spec("cli", fam, ["logout"], action="logout", label="codex logout")
        if joined in ("status", "/status", "login status", "whoami"):
            return _spec("cli", fam, ["login", "status"], action="status", label="codex login status")
        if joined in ("--version", "-v", "version"):
            return _spec("cli", fam, ["--version"], action="version", label="codex --version")
        if joined in ("terminal", "open", "interactive"):
            return _spec("cli", fam, [], mode="terminal", action="default", label="codex")
        return None

    if fam == "gemini":
        # Gemini's sign-in is an interactive menu inside its TUI; it has no
        # headless login subcommand, so every sign-in route opens a terminal.
        if joined in ("", "login", "/login", "auth", "/auth", "terminal", "open", "interactive"):
            return _spec("cli", fam, [], mode="terminal", action="login", label="gemini")
        if joined in ("--version", "-v", "version"):
            return _spec("cli", fam, ["--version"], action="version", label="gemini --version")
        return None

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Executable resolution
# ─────────────────────────────────────────────────────────────────────────────

def find_npm() -> str:
    """Locate npm. The bridge may run under pythonw with a trimmed PATH, so fall
    back to the standard Node install locations."""
    for name in ("npm.cmd", "npm") if os.name == "nt" else ("npm",):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    if os.name == "nt":
        candidates = [Path(r"C:\Program Files\nodejs\npm.cmd"),
                      Path(r"C:\Program Files (x86)\nodejs\npm.cmd"),
                      Path.home() / "AppData" / "Roaming" / "npm" / "npm.cmd"]
    else:
        candidates = [Path("/usr/local/bin/npm"), Path("/opt/homebrew/bin/npm"), Path("/usr/bin/npm")]
    for c in candidates:
        if c.is_file():
            return str(c)
    return ""


def build_argv(spec: dict, resolve_exe: Callable[[str], str]) -> tuple[list, str]:
    """(argv, error). resolve_exe(provider_id) → absolute exe path or ''."""
    fam = spec.get("family", "")
    info = FAMILIES.get(fam, {})
    if spec["kind"] == "npm":
        npm = find_npm()
        if not npm:
            return [], ("Node.js (npm) isn't installed on this computer, so the CLI can't be "
                        "installed from here. Install Node.js LTS from https://nodejs.org, "
                        "restart DATA, then try again.")
        return [npm, *spec["args"]], ""
    if spec["kind"] == "cli":
        exe = resolve_exe(info.get("provider", ""))
        if not exe:
            return [], (f"{info.get('name', fam)} isn't installed on this computer yet. "
                        f"Type `install {fam}` here to install it.")
        return [exe, *spec["args"]], ""
    return [], "Nothing to run."


def child_env() -> dict:
    """Environment for console children. API keys are stripped so status/login
    reflect the buyer's SUBSCRIPTION sign-in, matching how the chat runs the
    CLIs. Colour is turned off so output is plain text."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")}
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --no-deprecation").strip()
    return env


# ─────────────────────────────────────────────────────────────────────────────
# Piped sessions
# ─────────────────────────────────────────────────────────────────────────────

_EOF = object()


class ConsoleSession:
    """One running allowlisted command. Output chunks land on `out_q`; the
    bridge's SSE handler drains it. Input is written with `send_input`."""

    def __init__(self, spec: dict, argv: list, pane_id: str = ""):
        self.id = uuid.uuid4().hex
        self.spec = spec
        self.argv = argv
        self.pane_id = pane_id
        self.out_q: "queue.Queue" = queue.Queue()
        self.proc: Optional[subprocess.Popen] = None
        self.exit_code: Optional[int] = None
        self.started = time.time()
        self.ended: Optional[float] = None
        self.stopped = False
        self.timed_out = False
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      cwd=str(Path.home()), env=child_env(), bufsize=0)
        if os.name == "nt":
            # No flashing console window behind the dashboard.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.proc = subprocess.Popen(self.argv, **kwargs)
        threading.Thread(target=self._pump, daemon=True, name=f"console-pump-{self.id[:6]}").start()
        threading.Thread(target=self._watchdog, daemon=True, name=f"console-wd-{self.id[:6]}").start()

    def _pump(self) -> None:
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        out = self.proc.stdout
        try:
            while True:
                # read1/read on an unbuffered pipe returns as soon as ANY bytes
                # are available — essential, because prompts like
                # "Paste code here if prompted >" have no trailing newline.
                chunk = out.read1(4096) if hasattr(out, "read1") else out.read(4096)
                if not chunk:
                    break
                text = clean_output(dec.decode(chunk))
                if text:
                    self.out_q.put(text)
        except (OSError, ValueError):
            pass
        tail = clean_output(dec.decode(b"", final=True))
        if tail:
            self.out_q.put(tail)
        try:
            self.exit_code = self.proc.wait(timeout=10)
        except Exception:
            self.exit_code = self.proc.returncode
        with self._lock:
            for pipe in (self.proc.stdin, self.proc.stdout):
                try:
                    if pipe:
                        pipe.close()
                except OSError:
                    pass
        self.ended = time.time()
        self.out_q.put(_EOF)

    def _watchdog(self) -> None:
        limit = _TIMEOUTS.get(self.spec.get("action", ""), _TIMEOUTS["default"])
        deadline = self.started + limit
        while self.proc and self.proc.poll() is None:
            if time.time() > deadline:
                self.timed_out = True
                self.out_q.put(f"\n[Stopped after {limit // 60 or 1} min with no finish.]\n")
                self.stop()
                return
            time.sleep(1.0)

    def send_input(self, text: str) -> bool:
        with self._lock:
            if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
                return False
            try:
                self.proc.stdin.write((text.rstrip("\r\n") + "\n").encode("utf-8"))
                self.proc.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def stop(self) -> None:
        self.stopped = True
        p = self.proc
        if not p or p.poll() is not None:
            return
        try:
            if os.name == "nt":
                # npm/.cmd shims spawn node children; kill the whole tree.
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            else:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return bool(self.proc) and self.proc.poll() is None

    # -- draining ---------------------------------------------------------
    def events(self, keepalive: float = 8.0):
        """Yield ('out', text) | ('keepalive', None) | ('exit', code) until the
        process ends. Small consecutive chunks are coalesced."""
        while True:
            try:
                item = self.out_q.get(timeout=keepalive)
            except queue.Empty:
                yield ("keepalive", None)
                continue
            if item is _EOF:
                yield ("exit", self.exit_code)
                return
            buf = item
            while True:
                try:
                    nxt = self.out_q.get_nowait()
                except queue.Empty:
                    break
                if nxt is _EOF:
                    yield ("out", buf)
                    yield ("exit", self.exit_code)
                    return
                buf += nxt
            yield ("out", buf)


def clean_output(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    # Collapse carriage-return spinner redraws to the last frame per line.
    if "\r" in text:
        lines = text.replace("\r\n", "\n").split("\n")
        text = "\n".join(line.rstrip("\r").split("\r")[-1] for line in lines)
    return text


class SessionRegistry:
    """Tracks live sessions. One session per pane: starting a new command in a
    pane stops whatever that pane was running."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict = {}

    def start(self, spec: dict, argv: list, pane_id: str = "") -> ConsoleSession:
        self._reap()
        with self._lock:
            old = [s for s in self._sessions.values() if pane_id and s.pane_id == pane_id and s.running]
        for s in old:
            s.stop()
        sess = ConsoleSession(spec, argv, pane_id)
        sess.start()
        with self._lock:
            self._sessions[sess.id] = sess
        return sess

    def get(self, session_id: str) -> Optional[ConsoleSession]:
        with self._lock:
            return self._sessions.get(session_id or "")

    def _reap(self) -> None:
        cutoff = time.time() - 600
        with self._lock:
            for sid in [k for k, s in self._sessions.items() if s.ended and s.ended < cutoff]:
                self._sessions.pop(sid, None)

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            s.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Real terminal window (for TUI programs)
# ─────────────────────────────────────────────────────────────────────────────

def open_terminal(argv: list, family: str = "") -> tuple[bool, str]:
    """Open a visible console window on THIS machine running argv. Returns
    (ok, message). argv must come from build_argv (allowlisted)."""
    name = FAMILIES.get(family, {}).get("name", "the CLI")
    if family == "gemini":
        after = ("In the window that opened, choose **Login with Google**, finish in your browser, "
                 "then type `/quit` and close the window.")
    elif family == "claude":
        after = ("In the window that opened, type `/login`, finish in your browser, "
                 "then type `/exit` and close the window.")
    else:
        after = f"Finish signing in to {name} in the window that opened, then close it."
    try:
        if os.name == "nt":
            subprocess.Popen(["cmd.exe", "/k", *argv], cwd=str(Path.home()),
                             env=child_env(),
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010))
            return True, after
        if sys.platform == "darwin":
            cmd = " ".join(shlex.quote(a) for a in argv)
            script = cmd.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript",
                              "-e", f'tell application "Terminal" to do script "{script}"',
                              "-e", 'tell application "Terminal" to activate'])
            return True, after
        for term, pre in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]),
                          ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("xterm", ["-e"])):
            if shutil.which(term):
                subprocess.Popen([term, *pre, *argv], cwd=str(Path.home()))
                return True, after
        return False, ("No terminal app was found on this computer. Open a terminal yourself and run: "
                       + " ".join(shlex.quote(a) for a in argv))
    except Exception as e:  # pragma: no cover — platform-specific
        return False, f"Couldn't open a terminal window ({e})."


# ─────────────────────────────────────────────────────────────────────────────
# Logged-out detection for the chat runners
# ─────────────────────────────────────────────────────────────────────────────

_AUTH_FAIL = {
    "claude": re.compile(
        r"not logged in|please run /login|authentication_failed|oauth token (?:has )?expired|"
        r"invalid (?:api key|bearer token)|\b401\b", re.I),
    "codex": re.compile(
        r"\b401\b|unauthori[sz]ed|not (?:logged|signed) in|codex login|"
        r"token could not be refreshed|refresh[_ ]token|(?:log|sign) ?in again", re.I),
    "gemini": re.compile(
        r"auth(?:entication)? method|GEMINI_API_KEY|login required|not (?:logged|signed) in|"
        r"reauthenticat|oauth|credentials? (?:not found|expired|missing)", re.I),
}


def looks_logged_out(family: str, text: str) -> bool:
    pat = _AUTH_FAIL.get(family)
    return bool(pat and text and pat.search(text))


def login_notice(family: str) -> dict:
    """Payload for the chat 'needs_login' event plus the plain-text message that
    goes into the reply bubble (readable even on an older dashboard)."""
    info = FAMILIES.get(family, {"name": family or "This CLI"})
    name = info["name"]
    if family == "gemini":
        how = ("Type `gemini login` here (or click **Sign in** below). A terminal window opens; "
               "choose **Login with Google**, finish in your browser, then type `/quit`.")
    elif family == "codex":
        how = ("Type `codex login` here (or click **Sign in** below). Your browser opens to the "
               "ChatGPT sign-in page; once it says you're signed in, come back here.")
    else:
        how = ("Type `claude login` here (or click **Sign in** below). Your browser opens to the "
               "Claude sign-in page. If it shows you a code, paste the code into this chat and press Enter.")
    text = (f"**{name} isn't signed in on this computer yet**, Captain, so it can't answer. "
            f"{how} Then send your message again.")
    return {
        "family": family,
        "name": name,
        "command": f"{family} login",
        "terminal_only": family == "gemini",
        "text": text,
    }
