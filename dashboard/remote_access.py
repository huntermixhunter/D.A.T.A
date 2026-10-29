"""Local setup for private phone access and an owner-paired Telegram bot.

Importing this module never starts a service or contacts an external API.
Secrets live under users/, outside the dashboard's static file directory.
"""
import copy
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

PROXY_HEADERS = ("Cf-Connecting-Ip", "Cf-Ray", "Cdn-Loop", "X-Forwarded-For",
                 "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-Ip", "Forwarded", "Via",
                 "Tailscale-User-Login", "Tailscale-User-Name")
HTTPS_PORT = 8443


class SetupError(Exception):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def is_local(handler):
    """A direct loopback request with a loopback Host, never a reverse proxy."""
    port = handler.server.server_address[1]
    host = (handler.headers.get("Host") or "").lower()
    return (handler.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
            and host in {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
            and not any(handler.headers.get(key) for key in PROXY_HEADERS))


def setup_allowed(handler):
    if not is_local(handler) or handler.headers.get("X-Data-Request") != "1":
        return False
    if handler.headers.get("Sec-Fetch-Site", "").lower() in ("cross-site", "same-site"):
        return False
    origin = handler.headers.get("Origin", "").lower()
    return not origin or origin == "http://" + handler.headers.get("Host", "").lower()


def authorized(handler, manager, env_token=""):
    """Preserve an explicit bridge token; wizard keys protect remote requests."""
    if not env_token and is_local(handler):
        return True
    tokens = [token for token in (env_token, manager.remote_key()) if token]
    if not tokens:
        return False
    supplied = handler.headers.get("X-Data-Token", "").strip()
    if not supplied:
        try:
            supplied = parse_qs(urlsplit(handler.path).query, max_num_fields=50).get("key", [""])[0]
        except ValueError:
            return False
    return any(hmac.compare_digest(supplied.encode(), token.encode()) for token in tokens)


def _tailscale_binary():
    found = shutil.which("tailscale")
    if found:
        return found
    candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale/tailscale.exe",
                  Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
                  Path("/usr/local/bin/tailscale"), Path("/opt/homebrew/bin/tailscale")]
    return next((str(path) for path in candidates if path.is_file()), "")


def _approval_url(output):
    for value in re.findall(r"https://[^\s<>\"']+", output):
        parsed = urlsplit(value)
        if parsed.hostname == "login.tailscale.com" and parsed.path.startswith("/f/serve"):
            return value
    return ""


class RemoteAccess:
    def __init__(self, directory, port=7777, *, profile=lambda: "", reply=None,
                 env_token="", daemon=False):
        self.directory = Path(directory)
        self.path = self.directory / "config.json"
        self.port = port
        self.profile = profile
        self.reply = reply
        self.env_token = env_token
        self.daemon = daemon
        self.lock = threading.RLock()
        self.actions = threading.Lock()
        self.bot = None
        self.config = {"version": 1, "keep_running": False, "access_key": "",
                       "mobile": {}, "telegram": {}}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or saved.get("version") != 1:
                    raise ValueError("invalid settings")
                for key in ("mobile", "telegram"):
                    if not isinstance(saved.get(key, {}), dict):
                        raise ValueError("invalid settings")
                self.config.update(saved)
            except (OSError, ValueError) as exc:
                # Never overwrite unreadable credentials or silently remove protection.
                raise SetupError("Remote access settings could not be read. Restore users/remote-access/config.json.", 503) from exc

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(self.config, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.path)
        if os.name != "nt":
            self.path.chmod(0o600)

    def remote_key(self):
        with self.lock:
            # Disabling mobile access revokes wizard links even if the proxy is still up.
            return self.config.get("access_key", "") if self.config["mobile"].get("enabled") else ""

    @property
    def keep_running(self):
        with self.lock:
            return self.daemon or self.config.get("keep_running") is True

    def _run(self, args):
        binary = _tailscale_binary()
        if not binary:
            raise SetupError("Install Tailscale on this computer, then check the connection again.")
        try:
            result = subprocess.run([binary, *args], capture_output=True, text=True,
                                    timeout=12, stdin=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired as exc:
            output = "\n".join(x.decode(errors="replace") if isinstance(x, bytes) else (x or "")
                               for x in (exc.stdout, exc.stderr))
            approval = _approval_url(output)
            raise SetupError("Tailscale needs HTTPS approval. Open the approval link, then try again."
                             if approval else "Tailscale did not respond. Open its app, sign in, and try again.",
                             503, approval_url=approval) from None
        except OSError:
            raise SetupError("Could not open Tailscale. Open its app and check that it is running.", 503) from None
        if result.returncode:
            approval = _approval_url(output)
            raise SetupError("Approve HTTPS in Tailscale, then try again." if approval else
                             "Tailscale could not apply this change. Open its app and check sign-in and device permissions.",
                             503, approval_url=approval)
        # Diagnostics belong on stderr and must not corrupt a successful JSON result.
        return result.stdout.strip()

    def _json_command(self, args):
        try:
            value = json.loads(self._run(args))
            # Tailscale returns JSON null before Serve has ever been configured.
            if value is None and args == ["serve", "status", "--json"]:
                return {}
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise SetupError("Tailscale returned an unreadable status. Update Tailscale and try again.", 503) from None

    def _target(self):
        return f"http://127.0.0.1:{self.port}"

    def _owned(self, serve, host):
        entry = serve.get("Web", {}).get(host, {})
        handlers = entry.get("Handlers", {})
        return (handlers == {"/": {"Proxy": self._target()}}
                and serve.get("TCP", {}).get(str(HTTPS_PORT), {}).get("HTTPS") is True
                and not serve.get("AllowFunnel", {}).get(host))

    def mobile_status(self):
        with self.lock:
            saved = copy.deepcopy(self.config["mobile"])
        base = {"enabled": bool(saved.get("enabled")), "url": "", "state": "missing",
                "message": "Install Tailscale on your computer and phone to begin."}
        if not _tailscale_binary():
            return base
        try:
            status = self._json_command(["status", "--json"])
            if status.get("BackendState") != "Running":
                return dict(base, state="signed_out", message="Open Tailscale and sign in on both devices using the same account.")
            dns = str((status.get("Self") or {}).get("DNSName", "")).rstrip(".").lower()
            if not re.fullmatch(r"(?:[a-z0-9-]+\.)+ts\.net", dns):
                return dict(base, state="error", message="Enable MagicDNS in your Tailscale admin settings, then check again.")
            host = f"{dns}:{HTTPS_PORT}"
            serve = self._json_command(["serve", "status", "--json"])
            owned = self._owned(serve, host)
            occupied = (str(HTTPS_PORT) in serve.get("TCP", {})
                        or any(key.endswith(f":{HTTPS_PORT}") for key in serve.get("Web", {})))
            if occupied and not owned:
                return dict(base, state="conflict", message="Another Tailscale service uses port 8443. DATA will not replace it.")
            return dict(base, state="enabled" if owned and base["enabled"] else "ready",
                        url=f"https://{host}" if owned and base["enabled"] else "", host=host,
                        message="Your private phone link is ready." if owned and base["enabled"] else
                        "Tailscale is connected. You can enable the full dashboard.")
        except SetupError as exc:
            return dict(base, state="error", message=str(exc), **exc.details)

    def status(self):
        with self.lock:
            tg = self.config["telegram"]
            telegram = {"configured": bool(tg.get("token")), "enabled": bool(tg.get("enabled")),
                        "username": tg.get("username", ""), "paired": bool(tg.get("chat_id")),
                        "owner_name": tg.get("owner_name", ""),
                        "profile_active": not tg.get("profile") or tg.get("profile") == self.profile(),
                        "running": bool(self.bot and self.bot.running),
                        "error": self.bot.error if self.bot else ""}
        return {"mobile": self.mobile_status(), "telegram": telegram,
                "keep_running": self.keep_running, "daemon": self.daemon}

    def enable_mobile(self):
        state = self.mobile_status()
        if state["state"] not in ("ready", "enabled"):
            raise SetupError(state["message"], 409, approval_url=state.get("approval_url", ""))
        with self.lock:
            if not self.config.get("access_key"):
                self.config["access_key"] = secrets.token_urlsafe(32)
            # Save authentication before starting any network listener.
            self._save()
        if state["state"] != "enabled":
            self._run(["serve", "--bg", f"--https={HTTPS_PORT}", self._target()])
        serve = self._json_command(["serve", "status", "--json"])
        if not self._owned(serve, state["host"]):
            raise SetupError("Tailscale has not confirmed the private dashboard. Check HTTPS approval and try again.", 503)
        with self.lock:
            self.config["mobile"] = {"enabled": True, "url": "https://" + state["host"]}
            self.config["keep_running"] = True
            self._save()
        return {"message": "Full mobile access is enabled. Copy your phone link to connect."}

    def disable_mobile(self):
        with self.lock:
            saved = copy.deepcopy(self.config["mobile"])
            self.config["mobile"]["enabled"] = False
            self.config["access_key"] = secrets.token_urlsafe(32)
            self._save()
        # Revoke authentication first, even if Tailscale is unavailable.
        warning = ""
        if saved.get("enabled"):
            try:
                serve = self._json_command(["serve", "status", "--json"])
                if self._owned(serve, urlsplit(saved.get("url", "")).netloc):
                    self._run(["serve", "--bg", f"--https={HTTPS_PORT}", "off"])
                else:
                    warning = " The proxy configuration changed; check it in Tailscale."
            except SetupError:
                warning = " Tailscale could not be reached; check its app to remove the proxy."
        if self.env_token:
            warning += " Your separately configured bridge token remains valid for other remote connections."
        return {"message": "Mobile access is disabled and wizard access keys have been revoked." + warning}

    def phone_link(self):
        state = self.mobile_status()
        if state["state"] != "enabled":
            raise SetupError("Enable mobile access and check the connection before copying a phone link.", 409)
        key = self.remote_key()
        return {"link": state["url"] + "/#access=" + quote(key, safe="")}

    def start(self):
        from telegram_bot import TelegramBot
        with self.lock:
            if not self.bot:
                self.bot = TelegramBot(self)
            enabled = self.config["telegram"].get("enabled")
        if enabled:
            self.bot.start()

    def action(self, name, data):
        if not self.actions.acquire(blocking=False):
            raise SetupError("Another setup change is running. Please wait and try again.", 409)
        try:
            if name == "mobile/enable":
                if data.get("confirmed") is not True:
                    raise SetupError("Confirm that you want to enable full mobile access.")
                return self.enable_mobile()
            if name == "mobile/disable":
                return self.disable_mobile()
            if name == "mobile/link":
                return self.phone_link()
            if name == "keep-running":
                if type(data.get("enabled")) is not bool:
                    raise SetupError("Choose whether DATA should keep running.")
                with self.lock:
                    self.config["keep_running"] = data["enabled"]
                    self._save()
                return {"message": "Background access preference saved."}
            if name.startswith("telegram/"):
                from telegram_bot import TelegramBot
                if not self.bot:
                    self.bot = TelegramBot(self)
                return self.bot.action(name.split("/", 1)[1], data)
            raise SetupError("Unknown remote access action.", 404)
        finally:
            self.actions.release()


def handle_http(handler, manager):
    path = urlsplit(handler.path).path
    if not path.startswith("/remote-access/"):
        return False
    try:
        if not setup_allowed(handler):
            raise SetupError("Open Settings on the computer running DATA to manage remote access.", 403)
        if hasattr(handler, "_check_auth") and not handler._check_auth():
            return True
        if handler.command == "GET" and path == "/remote-access/status":
            payload = manager.status()
        elif handler.command == "POST":
            if handler.headers.get_content_type() != "application/json":
                raise SetupError("Use a JSON request.", 415)
            if handler.headers.get("Transfer-Encoding") or len(handler.headers.get_all("Content-Length", [])) != 1:
                raise SetupError("A single Content-Length is required.", 411)
            try:
                length = int(handler.headers.get("Content-Length", "0"))
            except ValueError:
                raise SetupError("Invalid request size.") from None
            if not 0 < length <= 8192:
                raise SetupError("Request is too large or empty.", 413)
            handler.connection.settimeout(15)
            try:
                data = json.loads(handler.rfile.read(length))
            except ValueError:
                raise SetupError("Invalid JSON.") from None
            if not isinstance(data, dict):
                raise SetupError("Use a JSON object.")
            payload = manager.action(path[len("/remote-access/"):], data)
        else:
            raise SetupError("Unknown remote access endpoint.", 404)
        code = 200
    except SetupError as exc:
        code, payload = exc.status, {"error": str(exc), **exc.details}
    except OSError:
        code, payload = 503, {"error": "Could not save remote access settings. Check local file permissions and disk space."}
    body = json.dumps(payload).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)
    return True
