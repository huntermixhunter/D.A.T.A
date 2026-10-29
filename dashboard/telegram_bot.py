"""Optional Telegram transport. Standard library only; no work starts on import."""
import hmac
import http.client
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

from remote_access import SetupError

_default_bot = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(token, method, data=None):
    """Fixed API host, bounded responses, no token-bearing exception messages."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(data or {}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "DATA-Telegram/1.0"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=15) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError()
        return result.get("result")
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        message = {401: "Telegram rejected this bot token. Create a new token in BotFather.",
                   403: "Telegram could not deliver the message. Open your bot and unblock it.",
                   409: "Another app is using this bot. Stop that connection or create a separate DATA bot.",
                   429: "Telegram is rate limiting requests. Wait a minute and try again."}.get(
                       code, "Telegram is unavailable. Try again shortly.")
        raise SetupError(message, 503, telegram_code=code) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise SetupError("Could not reach Telegram. Check your internet connection and try again.", 503) from None


class TelegramBot:
    def __init__(self, manager):
        self.manager = manager
        self.thread = None
        self.stop_event = threading.Event()
        self.error = ""
        self.pair_code = ""
        self.pair_until = 0
        self.started_at = 0

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set())

    def _config(self):
        with self.manager.lock:
            return dict(self.manager.config["telegram"])

    def _pairing(self):
        config = self._config()
        if not config.get("enabled"):
            raise SetupError("Connect a bot before pairing your phone.", 409)
        if config.get("chat_id"):
            raise SetupError("This bot is already paired. Disconnect it before pairing another account.", 409)
        with self.manager.lock:
            self.pair_code = secrets.token_urlsafe(24)
            self.pair_until = time.time() + 600
        return {"message": "Open this link in Telegram and tap Start within 10 minutes.",
                "pair_url": f"https://t.me/{config['username']}?start={self.pair_code}",
                "expires_at": self.pair_until}

    def action(self, name, data):
        global _default_bot
        if name == "connect":
            if data.get("confirmed") is not True:
                raise SetupError("Confirm that you want to connect your Telegram bot.")
            if self.thread and self.thread.is_alive():
                raise SetupError("This bot is connected or finishing its last request. Disconnect and wait before replacing it.", 409)
            if self._config().get("token"):
                raise SetupError("Disconnect the saved bot before replacing it.", 409)
            token = str(data.get("token", "")).strip()
            if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}", token):
                raise SetupError("Paste the complete bot token from BotFather.")
            me = api(token, "getMe")
            if not isinstance(me, dict) or not me.get("is_bot") or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", me.get("username", "")):
                raise SetupError("Telegram did not return a valid bot account.")
            webhook = api(token, "getWebhookInfo")
            if not isinstance(webhook, dict) or webhook.get("url"):
                raise SetupError("This bot already has a webhook. Create a separate bot for DATA so its existing integration is preserved.", 409)
            with self.manager.lock:
                self.manager.config["telegram"] = {"token": token, "username": me["username"],
                    "enabled": True, "profile": self.manager.profile(), "offset": 0}
                self.manager.config["keep_running"] = True
                self.manager._save()
            pairing = self._pairing()
            self.start()
            return pairing
        if name == "pair":
            result = self._pairing()
            self.start()
            return result
        if name == "restart":
            if not self._config().get("token"):
                raise SetupError("Connect a bot first.", 409)
            self.start()
            return {"message": "Telegram connection restarted."}
        if name == "disconnect":
            self.stop_event.set()
            with self.manager.lock:
                self.pair_code = ""
                self.pair_until = 0
                self.manager.config["telegram"] = {}
                self.manager._save()
            self.error = ""
            return {"message": "Telegram disconnected. Any task already running can finish on this computer."}
        if name == "test":
            config = self._config()
            if not config.get("chat_id") or config.get("profile") != self.manager.profile():
                raise SetupError("Pair your Telegram account and select its DATA profile first.", 409)
            self.send(config, "DATA is connected. You can send a message here while DATA is running on your computer.")
            return {"message": "Test message sent to your paired Telegram chat. No AI request was made."}
        raise SetupError("Unknown Telegram action.", 404)

    def start(self):
        global _default_bot
        if self.thread and self.thread.is_alive():
            if self.stop_event.is_set():
                raise SetupError("The previous Telegram connection is still stopping. Try again shortly.", 409)
            return
        if not self._config().get("enabled"):
            return
        self.stop_event = threading.Event()
        self.error = ""
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._poll, name="data-telegram", daemon=True)
        _default_bot = self
        self.thread.start()

    def _still_current(self, config):
        current = self._config()
        return (not self.stop_event.is_set() and current.get("enabled")
                and current.get("token") == config.get("token")
                and current.get("chat_id") == config.get("chat_id"))

    def send(self, config, text):
        if not self._still_current(config):
            raise SetupError("Telegram is disconnected.", 409)
        # 1,800 Unicode code points fit the 4,096 UTF-16-unit limit even for emoji.
        for start in range(0, len(text), 1800):
            if not self._still_current(config):
                return
            api(config["token"], "sendMessage", {"chat_id": config["chat_id"],
                "text": text[start:start + 1800], "link_preview_options": {"is_disabled": True}})

    def process_update(self, config, update):
        if not isinstance(update, dict) or type(update.get("update_id")) is not int:
            return
        with self.manager.lock:
            current = self.manager.config["telegram"]
            if self.stop_event.is_set() or current.get("token") != config.get("token"):
                return
            if update["update_id"] < current.get("offset", 0):
                return
            # Persist BEFORE dispatch: reconnects and crashes must not repeat a command.
            current["offset"] = update["update_id"] + 1
            self.manager._save()
        message = update.get("message") or {}
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        if (chat.get("type") != "private" or sender.get("is_bot")
                or type(sender.get("id")) is not int or sender["id"] != chat.get("id")):
            return
        if not isinstance(message.get("date"), (int, float)) or message["date"] < max(self.started_at - 5, time.time() - 120):
            return  # Never run queued commands from before a restart or long outage.
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        text = text.strip()
        config = self._config()
        if not config.get("chat_id"):
            code = text.partition(" ")[2] if text.startswith("/start ") else ""
            with self.manager.lock:
                if (not self.pair_code or time.time() > self.pair_until
                        or not hmac.compare_digest(code.encode(), self.pair_code.encode())):
                    return
                self.manager.config["telegram"].update({"chat_id": chat["id"], "user_id": sender["id"],
                    "owner_name": str(sender.get("first_name", "Your account"))[:80]})
                self.pair_code = ""
                self.pair_until = 0
                self.manager._save()
            self.send(self._config(), "Paired with DATA. Only this private chat can send requests. Send /help for instructions.")
            return
        if chat["id"] != config.get("chat_id") or sender["id"] != config.get("user_id"):
            return
        if config.get("profile") != self.manager.profile():
            self.send(config, "Telegram is paused because another DATA profile is selected. Select your profile on the computer to resume.")
            return
        if text in ("/start", "/help"):
            self.send(config, "Send a text message to talk to DATA. /status checks the connection. Replies use your selected AI connection and its normal limits or charges. Manage or disconnect this bot in Settings > Remote Access on your computer.")
        elif text == "/status":
            self.send(config, "DATA is online and this Telegram chat is paired.")
        elif text.startswith("/"):
            self.send(config, "Unknown command. Send /help for instructions, or type a normal message.")
        elif self.manager.reply and self._still_current(config):
            response = self.manager.reply(text, config["profile"], str(config["chat_id"]))
            if self._still_current(config) and config["profile"] == self.manager.profile():
                self.send(config, str(response or "No reply was returned. Check the AI connection on your computer."))

    def _poll(self):
        while not self.stop_event.is_set():
            config = self._config()
            if not config.get("enabled"):
                return
            try:
                updates = api(config["token"], "getUpdates", {"offset": config.get("offset", 0),
                    "timeout": 5, "limit": 20, "allowed_updates": ["message"]})
                if not isinstance(updates, list):
                    raise SetupError("Telegram returned an unreadable response.", 503)
                for update in updates:
                    if self.stop_event.is_set():
                        return
                    self.process_update(config, update)
                self.error = ""
            except SetupError as exc:
                self.error = str(exc)
                if exc.details.get("telegram_code") in (401, 409):
                    return
                self.stop_event.wait(30 if exc.details.get("telegram_code") == 429 else 5)
            except Exception:
                self.error = "Telegram could not complete the request. Check DATA on your computer."
                self.stop_event.wait(5)


def notify(text, source_profile):
    """Deliver only results originating from the paired, still-active profile."""
    bot = _default_bot
    if not bot:
        return False
    config = bot._config()
    if (not source_profile or not config.get("chat_id")
            or source_profile != config.get("profile")
            or source_profile != bot.manager.profile()):
        return False
    bot.send(config, str(text))
    return True
