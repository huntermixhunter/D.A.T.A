"""
Mail — multi-inbox IMAP/SMTP layer for Data.

Stores account config (incl. app passwords) at:
  %LOCALAPPDATA%\\hermes\\mail_accounts.json

JSON shape per account:
  {
    "label": "gmail",                            # required, unique
    "address": "hunterthomasmix@gmail.com",      # display address
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "imap_user": "hunterthomasmix@gmail.com",
    "password": "xxxx xxxx xxxx xxxx",           # Gmail app password
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "hunterthomasmix@gmail.com",
    "smtp_password": "...",                      # optional; falls back to password
    "send_as": [
      {"name": "Hunter Mix", "address": "hunter@elixirkeys.com"},
      {"name": "Hunter Mix", "address": "hunterthomasmix@gmail.com"}
    ],
    "default_send_as": "hunterthomasmix@gmail.com"
  }

OAuth (XOAUTH2) inboxes set auth_type="oauth" and oauth_provider in
{"google","microsoft"}; the login step dispatches to gmail_oauth.py or
microsoft_oauth.py respectively (see _oauth_mod). Everything else — folders,
flags, move, search, draft, send — is identical across auth types.
"""
import os
import re
import ssl
import json
import email
import email.header
import email.message
import email.utils
import email.policy
import imaplib
import smtplib
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("mail")

_STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_ACCOUNTS_FILE = _STATE_DIR / "mail_accounts.json"


# ── Config persistence ───────────────────────────────────────────

def _load_accounts() -> list:
    if not _ACCOUNTS_FILE.exists():
        return []
    try:
        return json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"mail_accounts.json unreadable: {e}")
        return []


def _save_accounts(accts: list) -> None:
    _ACCOUNTS_FILE.write_text(json.dumps(accts, indent=2), encoding="utf-8")


def list_accounts() -> list:
    """Safe-for-display list — passwords stripped."""
    out = []
    for a in _load_accounts():
        out.append({
            "label": a.get("label"),
            "address": a.get("address"),
            "imap_host": a.get("imap_host"),
            "auth_type": (a.get("auth_type") or "password"),
            "oauth_provider": (a.get("oauth_provider") or ("google" if (a.get("auth_type") or "").lower() == "oauth" else None)),
            "send_as": a.get("send_as", []),
            "default_send_as": a.get("default_send_as") or a.get("address"),
        })
    return out


def add_account(account: dict) -> dict:
    """Add or update by label. Required: label, imap_host, imap_user, password."""
    label = (account.get("label") or "").strip()
    if not label:
        return {"error": "label is required"}
    is_oauth = (account.get("auth_type") or "").lower() == "oauth"
    required = ("imap_host", "imap_user") if is_oauth else ("imap_host", "imap_user", "password")
    missing = [f for f in required if not account.get(f)]
    if missing:
        return {"error": f"Missing fields: {', '.join(missing)}"}
    account.setdefault("imap_port", 993)
    account.setdefault("smtp_host", account["imap_host"].replace("imap", "smtp"))
    account.setdefault("smtp_port", 465)
    account.setdefault("smtp_user", account["imap_user"])
    account.setdefault("address", account["imap_user"])
    account.setdefault("send_as", [])
    account.setdefault("default_send_as", account["address"])

    accts = _load_accounts()
    for i, a in enumerate(accts):
        if a.get("label") == label:
            accts[i] = account
            _save_accounts(accts)
            return {"ok": True, "updated": label}
    accts.append(account)
    _save_accounts(accts)
    return {"ok": True, "added": label}


def remove_account(label: str) -> dict:
    accts = _load_accounts()
    removed = next((a for a in accts if a.get("label") == label), None)
    new = [a for a in accts if a.get("label") != label]
    if len(new) == len(accts):
        return {"error": f"No account: {label}"}
    _save_accounts(new)
    if removed and (removed.get("auth_type") or "").lower() == "oauth":
        try:
            _oauth_mod(removed).revoke(label)   # delete the local token file
        except Exception:
            pass
    return {"ok": True, "removed": label}


def _find(label_or_address: str | None) -> dict | None:
    accts = _load_accounts()
    if not accts: return None
    if not label_or_address: return accts[0]
    for a in accts:
        if a.get("label") == label_or_address or a.get("address") == label_or_address:
            return a
    return None


# ── IMAP helpers ─────────────────────────────────────────────────

def _is_oauth(a: dict) -> bool:
    return (a.get("auth_type") or "").lower() == "oauth"


def _oauth_provider(a: dict) -> str:
    """Which OAuth provider owns this inbox. Defaults to 'google' so inboxes
    connected before the Microsoft lane existed keep working unchanged."""
    return (a.get("oauth_provider") or "google").strip().lower()


def _oauth_mod(a: dict):
    """Return the provider-specific OAuth module (gmail_oauth / microsoft_oauth).
    Both expose the same surface: get_access_token / xoauth2_string /
    xoauth2_b64 / revoke."""
    if _oauth_provider(a) == "microsoft":
        import microsoft_oauth as mod
    else:
        import gmail_oauth as mod
    return mod


def _imap(a: dict) -> imaplib.IMAP4_SSL:
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(a["imap_host"], int(a.get("imap_port", 993)), ssl_context=ctx)
    if _is_oauth(a):
        # OAuth (XOAUTH2) — same IMAP session, bearer token instead of password.
        mod = _oauth_mod(a)
        user = a.get("imap_user") or a.get("address")
        token = mod.get_access_token(a["label"])
        auth = mod.xoauth2_string(user, token).encode()
        conn.authenticate("XOAUTH2", lambda _=None: auth)
    else:
        conn.login(a["imap_user"], a["password"])
    return conn


def _smtp_login(s: smtplib.SMTP, a: dict, smtp_user: str) -> None:
    """Authenticate an SMTP session — XOAUTH2 for oauth accounts, else password."""
    if _is_oauth(a):
        mod = _oauth_mod(a)
        token = mod.get_access_token(a["label"])
        s.ehlo()
        code, resp = s.docmd("AUTH", "XOAUTH2 " + mod.xoauth2_b64(smtp_user, token))
        if code != 235:
            raise smtplib.SMTPAuthenticationError(code, resp)
    else:
        s.login(smtp_user, a.get("smtp_password") or a.get("password"))


def _is_gmail(a: dict) -> bool:
    return "gmail" in (a.get("imap_host", "") or "").lower()


def _decode(raw) -> str:
    if not raw: return ""
    try:
        parts = email.header.decode_header(raw)
        return "".join(
            (p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p)
            for p, enc in parts
        )
    except Exception:
        return str(raw)


def _summary(msg: email.message.Message, uid: str, label: str) -> dict:
    return {
        "account": label,
        "id": uid,
        "from": _decode(msg.get("From")),
        "to":   _decode(msg.get("To")),
        "subject": _decode(msg.get("Subject")),
        "date": _decode(msg.get("Date")),
        "message_id": (msg.get("Message-Id") or "").strip(),
    }


def _body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if "attachment" in (part.get("Content-Disposition") or "").lower():
                continue
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    pass
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return _strip_html(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
                except Exception:
                    pass
        return ""
    try:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        pass
    return str(msg.get_payload() or "")


def _strip_html(html: str) -> str:
    no_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    no_tags = re.sub(r"<[^>]+>", " ", no_script)
    return re.sub(r"\s+", " ", no_tags).strip()


# ── Read operations ──────────────────────────────────────────────

def list_unread(account: str | None = None, limit: int = 20, folder: str = "INBOX") -> list:
    accts = [_find(account)] if account else _load_accounts()
    accts = [a for a in accts if a]
    out = []
    for a in accts:
        label = a.get("label") or a.get("address") or "?"
        try:
            conn = _imap(a)
            try:
                conn.select(folder)
                typ, data = conn.uid("SEARCH", None, "UNSEEN")
                if typ != "OK": continue
                uids = data[0].split()
                for uid in reversed(uids[-limit:]):
                    typ, fd = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
                    if typ != "OK": continue
                    for part in fd:
                        if isinstance(part, tuple) and len(part) >= 2:
                            msg = email.message_from_bytes(part[1], policy=email.policy.default)
                            out.append(_summary(msg, uid.decode(), label))
                            break
            finally:
                try: conn.logout()
                except Exception: pass
        except Exception as e:
            out.append({"account": label, "error": str(e)})
    return out


def search(query: str, account: str | None = None, limit: int = 10) -> list:
    accts = [_find(account)] if account else _load_accounts()
    accts = [a for a in accts if a]
    out = []
    safe_query = (query or "").replace('"', "'")
    for a in accts:
        label = a.get("label") or a.get("address") or "?"
        try:
            conn = _imap(a)
            try:
                conn.select("INBOX")
                if _is_gmail(a):
                    # Gmail's powerful search syntax via X-GM-RAW
                    typ, data = conn.uid("SEARCH", None, "X-GM-RAW", f'"{safe_query}"')
                else:
                    typ, data = conn.uid("SEARCH", None, "TEXT", f'"{safe_query}"')
                if typ != "OK": continue
                uids = data[0].split()
                for uid in reversed(uids[-limit:]):
                    typ, fd = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
                    if typ != "OK": continue
                    for part in fd:
                        if isinstance(part, tuple) and len(part) >= 2:
                            msg = email.message_from_bytes(part[1], policy=email.policy.default)
                            out.append(_summary(msg, uid.decode(), label))
                            break
            finally:
                try: conn.logout()
                except Exception: pass
        except Exception as e:
            out.append({"account": label, "error": str(e)})
    return out[:limit] if not account else out


def read_message(account: str, message_id: str, folder: str = "INBOX") -> dict:
    a = _find(account)
    if not a:
        return {"error": f"Unknown account: {account}"}
    label = a.get("label") or a.get("address") or "?"
    try:
        conn = _imap(a)
        try:
            conn.select(folder)
            typ, fd = conn.uid("FETCH", message_id, "(BODY.PEEK[])")
            if typ != "OK":
                return {"error": "Fetch failed"}
            for part in fd:
                if isinstance(part, tuple) and len(part) >= 2:
                    msg = email.message_from_bytes(part[1], policy=email.policy.default)
                    body = _body(msg)
                    return {
                        "account": label,
                        "id": message_id,
                        "from": _decode(msg.get("From")),
                        "to":   _decode(msg.get("To")),
                        "cc":   _decode(msg.get("Cc")),
                        "subject": _decode(msg.get("Subject")),
                        "date": _decode(msg.get("Date")),
                        "message_id": (msg.get("Message-Id") or "").strip(),
                        "body": body[:20000],
                        "truncated": len(body) > 20000,
                    }
            return {"error": "Empty fetch result"}
        finally:
            try: conn.logout()
            except Exception: pass
    except Exception as e:
        log.exception("read_message failed")
        return {"error": str(e)}


# ── Cockpit operations: list-all, folders, flags, move, delete ───

def _parse_flags(meta) -> list:
    try:
        s = meta.decode(errors="replace") if isinstance(meta, (bytes, bytearray)) else str(meta)
    except Exception:
        s = str(meta)
    m = re.search(r"FLAGS \(([^)]*)\)", s)
    return m.group(1).split() if m else []


def list_messages(account: str | None = None, folder: str = "INBOX",
                  limit: int = 40, unread_only: bool = False) -> list:
    """Unified message list for the cockpit.

    account=None → walk every configured inbox (multi-inbox unified view).
    Returns lightweight header summaries plus seen/flagged flags — no body
    (the reader fetches the body on click). Newest first.
    """
    accts = [_find(account)] if account else _load_accounts()
    accts = [a for a in accts if a]
    out = []
    for a in accts:
        label = a.get("label") or a.get("address") or "?"
        try:
            conn = _imap(a)
            try:
                conn.select(folder)
                crit = "UNSEEN" if unread_only else "ALL"
                typ, data = conn.uid("SEARCH", None, crit)
                if typ != "OK":
                    continue
                uids = data[0].split()
                for uid in reversed(uids[-limit:]):
                    typ, fd = conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER])")
                    if typ != "OK":
                        continue
                    flags, header_bytes = [], None
                    for part in fd:
                        if isinstance(part, tuple) and len(part) >= 2:
                            flags = _parse_flags(part[0])
                            header_bytes = part[1]
                    if header_bytes is None:
                        continue
                    msg = email.message_from_bytes(header_bytes, policy=email.policy.default)
                    s = _summary(msg, uid.decode(), label)
                    s["seen"] = "\\Seen" in flags
                    s["flagged"] = "\\Flagged" in flags
                    s["folder"] = folder
                    out.append(s)
            finally:
                try: conn.logout()
                except Exception: pass
        except Exception as e:
            out.append({"account": label, "error": str(e)})
    return out


def list_folders(account: str) -> dict:
    a = _find(account)
    if not a:
        return {"error": f"Unknown account: {account}"}
    try:
        conn = _imap(a)
        try:
            typ, data = conn.list()
            names = []
            for line in (data or []):
                if not line:
                    continue
                s = line.decode(errors="replace") if isinstance(line, (bytes, bytearray)) else str(line)
                m = re.search(r'"([^"]*)"\s*$', s)      # quoted trailing name
                name = m.group(1) if m else s.split()[-1].strip('"')
                if name and name not in names:
                    names.append(name)
            return {"folders": names}
        finally:
            try: conn.logout()
            except Exception: pass
    except Exception as e:
        return {"error": str(e)}


def mark(account: str, message_id: str, seen: bool = True, folder: str = "INBOX") -> dict:
    a = _find(account)
    if not a:
        return {"error": f"Unknown account: {account}"}
    try:
        conn = _imap(a)
        try:
            conn.select(folder)
            op = "+FLAGS" if seen else "-FLAGS"
            typ, _ = conn.uid("STORE", message_id, op, "(\\Seen)")
            return {"ok": typ == "OK", "id": message_id, "seen": seen}
        finally:
            try: conn.logout()
            except Exception: pass
    except Exception as e:
        return {"error": str(e)}


def flag(account: str, message_id: str, on: bool = True, folder: str = "INBOX") -> dict:
    a = _find(account)
    if not a:
        return {"error": f"Unknown account: {account}"}
    try:
        conn = _imap(a)
        try:
            conn.select(folder)
            op = "+FLAGS" if on else "-FLAGS"
            typ, _ = conn.uid("STORE", message_id, op, "(\\Flagged)")
            return {"ok": typ == "OK", "id": message_id, "flagged": on}
        finally:
            try: conn.logout()
            except Exception: pass
    except Exception as e:
        return {"error": str(e)}


def move(account: str, message_id: str, dest: str, folder: str = "INBOX") -> dict:
    a = _find(account)
    if not a:
        return {"error": f"Unknown account: {account}"}
    try:
        conn = _imap(a)
        try:
            conn.select(folder)
            # Prefer the RFC 6851 MOVE extension; fall back to COPY + \Deleted.
            try:
                typ, _ = conn.uid("MOVE", message_id, dest)
                if typ == "OK":
                    return {"ok": True, "id": message_id, "dest": dest}
            except Exception:
                pass
            typ, _ = conn.uid("COPY", message_id, dest)
            if typ != "OK":
                return {"error": f"Copy to {dest} failed"}
            conn.uid("STORE", message_id, "+FLAGS", "(\\Deleted)")
            try: conn.expunge()
            except Exception: pass
            return {"ok": True, "id": message_id, "dest": dest}
        finally:
            try: conn.logout()
            except Exception: pass
    except Exception as e:
        return {"error": str(e)}


def delete(account: str, message_id: str, folder: str = "INBOX") -> dict:
    """Move to Trash (safe delete). Gmail uses [Gmail]/Trash."""
    a = _find(account)
    if not a:
        return {"error": f"Unknown account: {account}"}
    trash = "[Gmail]/Trash" if _is_gmail(a) else "Trash"
    res = move(account, message_id, trash, folder)
    if res.get("ok"):
        res["deleted"] = True
    return res


# ── Compose / send / draft ───────────────────────────────────────

def _build_msg(a: dict, to: str, subject: str, body: str,
               from_identity: str | None, cc: str | None, bcc: str | None,
               in_reply_to: str | None) -> tuple[email.message.EmailMessage, str]:
    from_addr = from_identity or a.get("default_send_as") or a.get("address")
    from_name = ""
    for ident in a.get("send_as", []):
        if (ident.get("address") or "").lower() == (from_addr or "").lower():
            from_name = ident.get("name", "")
            break

    msg = email.message.EmailMessage()
    msg["From"] = email.utils.formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = to
    if cc:  msg["Cc"]  = cc
    if bcc: msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    domain = from_addr.rsplit("@", 1)[-1] if "@" in (from_addr or "") else "localhost"
    msg["Message-Id"] = email.utils.make_msgid(domain=domain)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = in_reply_to
    msg.set_content(body)
    return msg, from_addr


def send(to: str, subject: str, body: str,
         from_account: str | None = None,
         from_identity: str | None = None,
         cc: str | None = None, bcc: str | None = None,
         in_reply_to: str | None = None) -> dict:
    a = _find(from_account)
    if not a:
        return {"error": f"No mail account: {from_account!r}"}
    try:
        msg, from_addr = _build_msg(a, to, subject, body, from_identity, cc, bcc, in_reply_to)
    except Exception as e:
        return {"error": f"compose failed: {e}"}

    smtp_host = a.get("smtp_host")
    smtp_port = int(a.get("smtp_port", 465))
    smtp_user = a.get("smtp_user") or a.get("imap_user")
    smtp_pass = a.get("smtp_password") or a.get("password")

    try:
        if smtp_port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=30) as s:
                _smtp_login(s, a, smtp_user)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
                s.ehlo(); s.starttls(); s.ehlo()
                _smtp_login(s, a, smtp_user)
                s.send_message(msg)
        return {"ok": True, "from": from_addr, "to": to,
                "subject": subject, "message_id": msg["Message-Id"]}
    except Exception as e:
        log.exception("send failed")
        return {"error": str(e)}


def draft(to: str, subject: str, body: str,
          from_account: str | None = None,
          from_identity: str | None = None,
          cc: str | None = None,
          in_reply_to: str | None = None) -> dict:
    """Append the message to the account's Drafts folder via IMAP."""
    a = _find(from_account)
    if not a:
        return {"error": f"No mail account: {from_account!r}"}
    try:
        msg, from_addr = _build_msg(a, to, subject, body, from_identity, cc, None, in_reply_to)
    except Exception as e:
        return {"error": f"compose failed: {e}"}

    drafts_folder = "[Gmail]/Drafts" if _is_gmail(a) else "Drafts"
    try:
        conn = _imap(a)
        try:
            conn.append(drafts_folder, "\\Draft",
                        imaplib.Time2Internaldate(datetime.now()), msg.as_bytes())
            return {"ok": True, "folder": drafts_folder,
                    "from": from_addr, "to": to, "subject": subject}
        finally:
            try: conn.logout()
            except Exception: pass
    except Exception as e:
        log.exception("draft failed")
        return {"error": str(e)}
