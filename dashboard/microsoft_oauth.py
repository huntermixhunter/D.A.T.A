"""
Microsoft OAuth (XOAUTH2) for Data's mail connector.

The Outlook / Office 365 / Hotmail counterpart to gmail_oauth.py. Lets the
operator connect a Microsoft mailbox by signing in through Microsoft's own
consent screen instead of pasting a password. Microsoft hands back a limited,
revocable OAuth token; nothing but that token (inside an MSAL cache) is stored,
kept locally in %LOCALAPPDATA%\\hermes just like every other credential.

The token carries the Outlook IMAP + SMTP scopes, which authorize the SAME
IMAP + SMTP paths mail.py already uses — we simply authenticate with XOAUTH2
(an OAuth bearer token) instead of a password. That means the entire folder /
flag / move / search / draft / send machinery in mail.py stays exactly as-is;
only the login step changes.

This module deliberately mirrors gmail_oauth.py's public surface
(libs_available / client_secret_present / is_available / is_configured /
get_access_token / xoauth2_string / xoauth2_b64 / setup_oauth / revoke) so
mail.py can dispatch to either provider through one generic code path.

State files (all under %LOCALAPPDATA%\\hermes):
  microsoft_client.json               shared OAuth client id (public/desktop app)
  microsoft_<label>_token.json        per-inbox MSAL token cache

microsoft_client.json shape (one of):
  {"client_id": "xxxxxxxx-xxxx-...", "authority": "https://login.microsoftonline.com/common"}
  {"installed": {"client_id": "xxxxxxxx-..."}}          # tolerated for symmetry

Register the app once in the Azure portal (portal.azure.com):
  Azure Active Directory -> App registrations -> New registration
    Supported account types: "Accounts in any org directory and personal
      Microsoft accounts" (so both work/school and outlook.com sign in)
    Redirect URI: Public client/native -> http://localhost
  Copy the "Application (client) ID" into microsoft_client.json. No secret is
  needed for a public desktop client.

Setup happens automatically from the dashboard's "Connect with Microsoft"
button (the bridge spawns this module as a subprocess). It can also be run by
hand:

    python dashboard\\microsoft_oauth.py <label>

which opens the browser for consent and registers the inbox in
mail_accounts.json with auth_type="oauth", oauth_provider="microsoft".
"""
from __future__ import annotations

import os
import sys
import json
import base64
import logging
from pathlib import Path

log = logging.getLogger("microsoft_oauth")

_STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_CLIENT_SECRET = _STATE_DIR / "microsoft_client.json"

# Outlook IMAP + SMTP over XOAUTH2. MSAL implicitly adds openid / profile /
# offline_access, so we get an id_token (for the address) and a refresh token.
SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
]

# Works for BOTH personal (outlook.com / hotmail / live) and work/school accounts.
DEFAULT_AUTHORITY = "https://login.microsoftonline.com/common"

# Microsoft's fixed server coordinates — the operator never types these.
MS_IMAP_HOST = "outlook.office365.com"
MS_IMAP_PORT = 993
MS_SMTP_HOST = "smtp.office365.com"
MS_SMTP_PORT = 587  # STARTTLS — mail.py.send() handles non-465 with starttls.


# ── module-name helper (so printed hints match whichever tree we live in) ──

def _script_hint() -> str:
    parent = Path(__file__).resolve().parent.name  # "dashboard" or "lcars-dashboard"
    return f"python {parent}\\microsoft_oauth.py <label>"


def _safe(label: str) -> str:
    s = "".join(c for c in (label or "") if c.isalnum() or c in ("-", "_"))
    if not s:
        raise ValueError(f"invalid inbox label: {label!r}")
    return s


def _token_path(label: str) -> Path:
    return _STATE_DIR / f"microsoft_{_safe(label)}_token.json"


# ── lazy msal import (kept optional so mail.py loads without the lib) ──

def _import_msal():
    try:
        import msal
        return msal
    except ImportError as e:
        raise RuntimeError("Microsoft auth lib missing - run: pip install msal") from e


# ── client config ─────────────────────────────────────────────────

def _read_client() -> tuple[str, str]:
    """Return (client_id, authority) from microsoft_client.json."""
    raw = json.loads(_CLIENT_SECRET.read_text(encoding="utf-8"))
    inner = raw.get("installed") or raw.get("public") or raw
    client_id = (inner.get("client_id") or "").strip()
    if not client_id:
        raise RuntimeError(f"microsoft_client.json has no client_id (at {_CLIENT_SECRET})")
    authority = (inner.get("authority") or raw.get("authority") or DEFAULT_AUTHORITY).strip()
    return client_id, authority


# ── availability / status ─────────────────────────────────────────

def libs_available() -> bool:
    try:
        _import_msal()
        return True
    except Exception:
        return False


def client_secret_present() -> bool:
    return _CLIENT_SECRET.exists()


def is_available() -> bool:
    """True iff we could START an OAuth flow (lib + client id both here)."""
    if not libs_available() or not _CLIENT_SECRET.exists():
        return False
    try:
        _read_client()
        return True
    except Exception:
        return False


def is_configured(label: str) -> bool:
    """True iff a token cache exists on disk for this inbox label."""
    try:
        return _token_path(label).exists()
    except ValueError:
        return False


# ── token cache / access token ────────────────────────────────────

def _load_cache(label: str):
    msal = _import_msal()
    cache = msal.SerializableTokenCache()
    tp = _token_path(label)
    if tp.exists():
        try:
            cache.deserialize(tp.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"[microsoft_oauth] token cache unreadable for {label!r}: {e}")
    return cache


def _save_cache(label: str, cache) -> None:
    if cache.has_state_changed:
        _token_path(label).write_text(cache.serialize(), encoding="utf-8")


def _app(label: str, cache):
    msal = _import_msal()
    client_id, authority = _read_client()
    return msal.PublicClientApplication(client_id, authority=authority, token_cache=cache)


def get_access_token(label: str) -> str:
    """Return a currently-valid OAuth access token, refreshing silently if needed."""
    tp = _token_path(label)
    if not tp.exists():
        raise RuntimeError(f"Microsoft inbox '{label}' not authorized. Run: {_script_hint()}")
    cache = _load_cache(label)
    app = _app(label, cache)
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(f"Microsoft token for '{label}' has no account - re-run {_script_hint()}")
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(label, cache)
    if not result or "access_token" not in result:
        raise RuntimeError(
            f"Microsoft token for '{label}' invalid or expired - re-run {_script_hint()}"
        )
    return result["access_token"]


def xoauth2_string(user: str, token: str) -> str:
    """The raw (un-base64'd) SASL XOAUTH2 initial-client-response string."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def xoauth2_b64(user: str, token: str) -> str:
    """base64 of the XOAUTH2 string - what SMTP AUTH expects as its argument."""
    return base64.b64encode(xoauth2_string(user, token).encode()).decode()


# ── one-time consent + inbox registration ─────────────────────────

def setup_oauth(label: str = "outlook") -> bool:
    """Open the browser for Microsoft consent, save the token cache, and register
    the inbox in mail_accounts.json with auth_type='oauth',
    oauth_provider='microsoft'. Returns True on success."""
    label = (label or "outlook").strip() or "outlook"
    if not _CLIENT_SECRET.exists():
        print(f"\n[ERROR] Microsoft client id not found at: {_CLIENT_SECRET}")
        print("Register a public desktop app at https://portal.azure.com")
        print("  Azure AD -> App registrations -> New registration")
        print("    Account types: any org directory + personal Microsoft accounts")
        print("    Redirect URI: Public client/native -> http://localhost")
        print(f"Then save its client id as: {_CLIENT_SECRET}")
        print('  {"client_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}\n')
        return False

    try:
        client_id, authority = _read_client()
    except Exception as e:
        print(f"[ERROR] {e}")
        return False

    cache = _load_cache(label)
    app = _app(label, cache)
    print(f"\nOpening browser for Microsoft consent - inbox label '{label}'.")
    print("Pick the Microsoft account you want to connect and approve access.\n")
    try:
        result = app.acquire_token_interactive(
            SCOPES,
            prompt="select_account",
        )
    except Exception as e:
        print(f"[ERROR] Microsoft sign-in failed: {e}")
        return False

    if not result or "access_token" not in result:
        err = (result or {}).get("error_description") or (result or {}).get("error") or "unknown error"
        print(f"[ERROR] Microsoft did not return a token: {err}")
        return False

    _save_cache(label, cache)

    # The address + display name come straight out of the id_token claims — no
    # extra network round-trip needed.
    claims = result.get("id_token_claims") or {}
    address = (claims.get("preferred_username") or claims.get("email") or "").strip()
    name = (claims.get("name") or "").strip()
    if not address:
        # Fall back to the account MSAL cached.
        try:
            accts = app.get_accounts()
            address = (accts[0].get("username") if accts else "") or ""
        except Exception:
            address = ""
    if not address:
        print("[ERROR] Could not read the account's email address from Microsoft.")
        print("Token was saved, but the inbox could not be auto-registered.")
        return False

    account = {
        "label": label,
        "address": address,
        "auth_type": "oauth",
        "oauth_provider": "microsoft",
        "imap_user": address,
        "imap_host": MS_IMAP_HOST,
        "imap_port": MS_IMAP_PORT,
        "smtp_host": MS_SMTP_HOST,
        "smtp_port": MS_SMTP_PORT,
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
        log.warning(f"[microsoft_oauth] add_account failed: {e}")
        print(f"[WARN] token saved but inbox not registered: {e}")
        return False

    print(f"\n[OK] Microsoft inbox '{label}' connected as {address}.")
    print(f"Token cache: {_token_path(label)}")
    return True


def revoke(label: str) -> None:
    """Best-effort local cleanup when an inbox is removed."""
    try:
        tp = _token_path(label)
        if tp.exists():
            tp.unlink()
    except Exception as e:
        log.warning(f"[microsoft_oauth] revoke cleanup failed for {label!r}: {e}")


if __name__ == "__main__":
    lbl = sys.argv[1] if len(sys.argv) > 1 else "outlook"
    ok = setup_oauth(lbl)
    sys.exit(0 if ok else 1)
