# Your DATA Account

**Connection modes.** With `DATA_SERVICES_URL` configured, Account settings,
Marketplace and Forum use one online account. Without it, this guide's local
account paths apply. `DATA_COMMUNITY_URL` alone retains the older forum-only
connection with separate sign-in. See `SHARED_SERVICES.md` for setup and privacy
boundaries. Local accounts and profiles are never uploaded automatically.

**Settings → Account.** One identity for the dashboard, the Marketplace and the
local Forum: sign in or create an account, set the nickname and picture Data
uses for you, and change your username, email, password and crypto wallet
addresses. Officer pictures live one tab over, in Crew Personalities, beside the
personality file they belong to.

## Two records, on purpose

| | Where it lives | What it is for |
|---|---|---|
| **Account** | `users/marketplace/marketplace.sqlite3` | Username, password, email, wallet addresses, nickname and picture. The same account the Marketplace and local Forum use, so a post signed here is signed there. |
| **Captain profile** | `users/users.json` and `users/<id>/avatar.png` | The dashboard identity. Its nickname is the header pill and the name Data is told to call you in every system prompt. |

Saving a nickname or picture writes both, so there is one name and one face
everywhere. The Captain profile half works signed out, so a single-user install
can set what Data calls them without creating an account at all. Signing out
ends the browser session and deletes nothing; the nickname and picture stay.

Signing in adopts the account's nickname as the dashboard identity. With shared
services, the same account can access listings and discussions on another device.
It does not synchronize installed extensions, memory or private conversation
history, and it does not merge two existing accounts.

## Nickname

The nickname is what the header pill shows, what Data calls you, and the name on
your Marketplace and Forum posts. 1 to 60 characters. The optional title goes in
front of it — "Captain", "Dr.", or empty for none. Both reach the model on your
next message, not retroactively: earlier turns keep whatever name was set then.

With one profile the header pill appears once a nickname or picture is set, and
opens this page. With two or more it is the Captain switcher, with this page at
the bottom of its menu.

## Pictures

PNG or JPEG, up to 512 KB, at most 4096 pixels a side and 8 megapixels. SVG,
URLs and every active document type are refused. Uploads are checked for a real
file header, not just a file extension, and stored as plain image files:
`users/<id>/avatar.png` for a Captain and `users/agent_avatars/<officer>.png`
for an officer. Replacing a picture removes the previous file rather than
leaving it to be served. An officer needs a personality file in
`~/.claude/agents` before a picture can be attached to them.

Pictures are served with `nosniff` and a `default-src 'none'` policy, and the
bridge resolves the file itself from the id in the URL — a request cannot name a
path. They are not in the static assets directory and are excluded from release
archives.

## Sign-in details

Username, email and password changes each require your current password, so a
browser someone else left open is not enough to take the account over. Changing
your password re-hashes it with a fresh salt and ends every other session for
that account — including one an attacker already holds — while leaving the
browser you changed it in signed in.

Usernames follow the registration rules: 3 to 32 characters, letters, numbers,
dots, hyphens or underscores, trimmed and lowercased, not one of the reserved
names, and not already taken. Passwords are 12 to 128 characters and are stored
only as a PBKDF2-SHA256 hash.

**Email is optional and is a note to yourself.** It is stored with the selected
account, locally or on the configured service. It is never verified, never sent
to, and it cannot recover a lost password. There is
no password reset: a forgotten password means a new account. Do not treat the
field as a recovery channel, because it is not one.

## Crypto wallet addresses

Your own receiving addresses, up to twelve, each with a network and an optional
40-character label. They are recorded for your reference only. **DATA never
holds a key, signs a transaction, or sends anything to them**, and nothing in
the dashboard reads them to move money.

Each address is checked against its network's format before it is stored —
EVM chains, Solana, Bitcoin, Litecoin, Dogecoin, Monero, Cardano and Tron each
have their own shape — because a typo in a receiving address loses funds. "Other"
accepts a conservative generic character set for anything not listed. Saving the
list requires your current password, since redirecting a payout address is
exactly what someone else would try to do.

A stored row that fails re-validation on read is shown flagged, not hidden. If
the format checks are ever tightened, a previously valid address would otherwise
disappear quietly and then be erased by the next save, which rebuilds the list
from what was on screen.

Wallet addresses and the email are returned only to the signed-in owner through
`account/me`. They are not in any public account, post author, catalog entry or
review. With shared services, they travel only through the owner's explicit
Account settings requests, never through the public Forum or catalogue.

## Scope

The browser-facing `/marketplace/account/*` endpoints are loopback-only,
reject proxy headers and cross-site requests, and require the marketplace
request header on writes. With `DATA_SERVICES_URL`, these endpoints proxy only
allowlisted account fields to the configured service. They share an online
session with the Marketplace and Forum; local account cookies remain separate.

The bridge routes that hold the dashboard half — `/user/profile`, `/user/list`,
`/user/switch`, `/user/avatar`, `/agents/avatar` — pass `_dashboard_gate` first.
This matters more than it looks: the bridge answers `Access-Control-Allow-Origin: *`
and does not require a JSON content type, so without the gate any page the
Captain happened to have open could POST a new nickname — and the nickname is
interpolated into Data's system prompt on every turn, which makes a drive-by
write prompt injection against an agent holding shell, file and mail tools. The
gate requires an `X-Data-Request: 1` header that cannot be set cross-origin
without a preflight the bridge refuses, checks `Sec-Fetch-Site`, and requires
`Origin` to match the `Host` addressed. `Host` is deliberately not pinned to
localhost so the dashboard still works over the Captain's own tunnel; off the
loopback names the bridge token is required, which is what refuses a hostname
rebound onto this port. `dashboard/tests/test_account.py` carries the three
exploit attempts as tests.

Rate limits are keyed per account, not per peer — every local request arrives
from loopback, so a peer key would be one global budget shared by everyone. A
change the password authorized clears the budget, so a dozen honest saves do not
lock the owner out. Reads of `account/me` are throttled separately.

**One accepted risk, stated plainly.** `account/me` returns the email and the
wallet addresses to any caller holding a valid session, without re-asking for
the password. The session is the authentication for viewing your own settings,
and masking the addresses would mean you could not check an address you are
about to rely on. Someone who steals the session can read them. If that trade is
wrong for you, mask them in `private_account` and gate the reveal behind
`verify_password`.

In legacy forum-only mode (`DATA_COMMUNITY_URL` without `DATA_SERVICES_URL`),
community accounts live on the service. This page manages the local account in
that legacy mode; the separate community sign-in stays on the Forum page. The community
transport's field allowlist is unchanged — email, password changes and wallet
addresses are not proxied anywhere.

Existing databases gain the two new columns in place on first open, so nobody
has to recreate an account. No hosting was provisioned, no public service was
activated, no email was sent, no paid API was called and no release was
published by this feature.

## Verification

```powershell
python -m unittest discover -s dashboard/tests -p test_account.py -v
python -m unittest discover -s dashboard/tests -p test_marketplace.py -v
python dashboard/tests/account_browser.py
node --check dashboard/app.js
```

`test_account.py` covers the account store (the private view, password-confirmed
changes, session revocation, username and email rules, per-network address
checks, list bounds, a corrupt wallet column, per-account rate limiting and the
column migration) and the bridge endpoints driven over real HTTP against a
temporary users directory — including three attacks that worked before the gate
was added: a cross-origin write of the nickname, a cross-origin read of the
profile, and a rebound hostname.
`account_browser.py` runs the actual request handler and drives the pane in
Chromium: create an account, rename, upload a picture, a refused wallet typo
followed by a good address, a refused wrong password, a password change, an
officer picture, sign out, and the phone layout. Both use temporary directories
and leave the real profile, memory and history untouched.
