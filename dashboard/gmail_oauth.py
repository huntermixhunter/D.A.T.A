"""
Gmail OAuth (XOAUTH2) for Data's mail connector.

Lets the operator connect a Gmail inbox by signing in through Google's own
consent screen instead of pasting an app password. Google hands back a
limited, revocable OAuth token; nothing but that token is stored, and it is
kept locally in %LOCALAPPDATA%\\hermes just like every other credential.

The token carries the `https://mail.google.com/` scope, which authorizes the
SAME IMAP + SMTP paths mail.py already uses — we simply authenticate with
XOAUTH2 (an OAuth bearer token) instead of a password. That means the entire
folder / flag / move / search / draft / send machinery in mail.py stays
exactly as-is; only the login step changes.

State files (all under %LOCALAPPDATA%\\hermes):
  google_client_secret.json          shared OAuth client (Desktop app type)
  google_gmail_<label>_token.json    per-inbox OAuth token

Setup happens automatically from the dashboard's "Connect with Google" button
(the bridge spawns this module as a subprocess). It can also be run by hand:

    python dashboard\\gmail_oauth.py <label>

which opens the browser for consent and registers the inbox in
mail_accounts.json with auth_type="oauth".
"""
from __future__ import annotations

import os
import sys
import json
import base64
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger("gmail_oauth")

_STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_CLIENT_SECRET = _STATE_DIR / "google_client_secret.json"

# https://mail.google.com/  -> full IMAP + SMTP access via XOAUTH2.
# userinfo.*                 -> read the address + display name once, at setup,
#                              so we can register the inbox without asking.
SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Gmail's fixed server coordinates — the operator never types these.
GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465


# ── module-name helper (so printed hints match whichever tree we live in) ──

def _script_hint() -> str:
    parent = Path(__file__).resolve().parent.name  # "dashboard" or "lcars-dashboard"
    return f"python {parent}\\gmail_oauth.py <label>"


def _safe(label: str) -> str:
    s = "".join(c for c in (label or "") if c.isalnum() or c in ("-", "_"))
    if not s:
        raise ValueError(f"invalid inbox label: {label!r}")
    return s


def _token_path(label: str) -> Path:
    return _STATE_DIR / f"google_gmail_{_safe(label)}_token.json"


# ── lazy google imports (kept optional so mail.py loads without the libs) ──

def _import_google():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        return Credentials, Request
    except ImportError as e:
        raise RuntimeError(
            "Google auth libs missing - run: "
            "pip install google-auth google-auth-oauthlib"
        ) from e


def _import_flow():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        return InstalledAppFlow
    except ImportError as e:
        raise RuntimeError(
            "google-auth-oauthlib missing - run: pip install google-auth-oauthlib"
        ) from e


# ── availability / status ─────────────────────────────────────────

def libs_available() -> bool:
    try:
        _import_google()
        _import_flow()
        return True
    except Exception:
        return False


def client_secret_present() -> bool:
    return _CLIENT_SECRET.exists()


def is_available() -> bool:
    """True iff we could START an OAuth flow (libs + client secret both here)."""
    return libs_available() and client_secret_present()


def is_configured(label: str) -> bool:
    """True iff a token exists on disk for this inbox label."""
    try:
        return _token_path(label).exists()
    except ValueError:
        return False


# ── credentials / access token ────────────────────────────────────

def _get_creds(label: str):
    Credentials, Request = _import_google()
    tp = _token_path(label)
    if not tp.exists():
        raise RuntimeError(f"Gmail inbox '{label}' not authorized. Run: {_script_hint()}")
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tp.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                f"Gmail token for '{label}' invalid - re-run {_script_hint()}"
            )
    return creds


def get_access_token(label: str) -> str:
    """Return a currently-valid OAuth access token, refreshing if needed."""
    return _get_creds(label).token


def xoauth2_string(user: str, token: str) -> str:
    """The raw (un-base64'd) SASL XOAUTH2 initial-client-response string."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def xoauth2_b64(user: str, token: str) -> str:
    """base64 of the XOAUTH2 string - what SMTP AUTH expects as its argument."""
    return base64.b64encode(xoauth2_string(user, token).encode()).decode()


# ── one-time consent + inbox registration ─────────────────────────

def _fetch_userinfo(token: str) -> dict:
    """Read {email, name} for the just-authorized account."""
    req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def setup_oauth(label: str = "gmail") -> bool:
    """Open the browser for Google consent, save the token, and register the
    inbox in mail_accounts.json with auth_type='oauth'. Returns True on success."""
    label = (label or "gmail").strip() or "gmail"
    if not _CLIENT_SECRET.exists():
        print(f"\n[ERROR] Client secret not found at: {_CLIENT_SECRET}")
        print("Create one at https://console.cloud.google.com/apis/credentials")
        print("  -> Create Credentials -> OAuth client ID -> Desktop app -> Download JSON")
        print(f"Save it as: {_CLIENT_SECRET}\n")
        return False

    InstalledAppFlow = _import_flow()
    flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_SECRET), SCOPES)
    print(f"\nOpening browser for Gmail consent - inbox label '{label}'.")
    print("Pick the Google account you want to connect and approve access.\n")
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        authorization_prompt_message="",
        prompt="select_account consent",
    )
    tp = _token_path(label)
    tp.write_text(creds.to_json(), encoding="utf-8")

    # Read the address + display name so the inbox self-registers.
    try:
        info = _fetch_userinfo(creds.token)
    except Exception as e:
        log.warning(f"[gmail_oauth] userinfo lookup failed: {e}")
        info = {}
    address = (info.get("email") or "").strip()
    name = (info.get("name") or "").strip()
    if not address:
        print("[ERROR] Could not read the account's email address from Google.")
        print("Token was saved, but the inbox could not be auto-registered.")
        return False

    account = {
        "label": label,
        "address": address,
        "auth_type": "oauth",
        "imap_user": address,
        "imap_host": GMAIL_IMAP_HOST,
        "imap_port": GMAIL_IMAP_PORT,
        "smtp_host": GMAIL_SMTP_HOST,
        "smtp_port": GMAIL_SMTP_PORT,
        "smtp_user": address,
        "send_as": [{"name": name or address, "address": address}],
        "default_send_as": address,
    }
    try:
        import mail
        res = mail.add_account(account)
        if res.get("error"):
            print(f"[WARN] inbox registration returned: {res['error']}")
    except Exception as e:
        log.warning(f"[gmail_oauth] add_account failed: {e}")
        print(f"[WARN] token saved but inbox not registered: {e}")
        return False

    print(f"\n[OK] Gmail inbox '{label}' connected as {address}.")
    print(f"Token: {tp}")
    return True


def revoke(label: str) -> None:
    """Best-effort local cleanup when an inbox is removed."""
    try:
        tp = _token_path(label)
        if tp.exists():
            tp.unlink()
    except Exception as e:
        log.warning(f"[gmail_oauth] revoke cleanup failed for {label!r}: {e}")


if __name__ == "__main__":
    lbl = sys.argv[1] if len(sys.argv) > 1 else "gmail"
    ok = setup_oauth(lbl)
    sys.exit(0 if ok else 1)
