# DATA Community Forum

## Hosted website: 2026-10-29

The optional Forum navigation entry now opens the public community in the
browser at:

https://forum.magimatix.com

The website uses Cloudflare Pages and Supabase. Public browsing and registration
are live following the owner's explicit activation approval on 2026-10-29.
The email provider's free plan, public signup, confirmation delivery, password
login, and password recovery are verified. The dashboard keeps an explicit link
available if a browser blocks automatic opening. Community accounts are managed on that website,
separately from local dashboard and Marketplace accounts. Nothing private was
uploaded. These dashboard changes ship in DATA v1.0.58.

Source, deployment, verification, and moderation instructions are in
`community-web/README.md`. The hosted entry is verified with
`python dashboard/tests/hosted_forum_browser.py`.

The rest of this document describes the retained local/Python service. For that
legacy preview, add `--local-forum` to the preview command below. The legacy
browser tests select this mode explicitly.

The optional Forum page is off by default. Enable it in **Settings > View >
Navigation > Community Forum**. The setting is saved for the current dashboard
profile in this browser, alongside the other page toggles. Hiding the page
returns to the main page and makes no further forum requests. Opening a direct
forum link while it is disabled opens the View settings instead of opting in.

## Conversations

Six categories ship with the forum: Dashboard General, Help & Troubleshooting,
Ideas & Requests, Show & Tell, Extensions, and Off Topic. Members can create
discussions, reply, edit or delete their own posts, like posts, follow discussions,
and report posts to the operator. Search includes titles and visible post text.
Filters include Following, My discussions, recently active, newest, and unanswered.
Unread activity appears when a signed-in member returns. Refresh replies retrieves
new posts without clearing an unfinished reply. There is no background polling,
email notification, direct messaging, or automatic publication from private chats.

Posts are plain text. Names and unique usernames appear beside each post. Threads
are paginated in groups of 25 and posts in groups of 30. Titles are limited to
140 characters, posts to 12,000, reports to 1,000, and search to 120. Request IDs
prevent a retry after a lost network response from posting the same text twice.
Reply drafts and their request IDs stay in memory for that account and discussion
until sent or the tab is reloaded. Drafts are cleared on account changes.

## Try the local page

From the repository root:

```powershell
python dashboard/marketplace_preview.py --port 7802 --data-dir users/forum-preview
```

Then open:

http://localhost:7802/#forum

Enable Community Forum, close Settings, and choose Forum in the sidebar. The
preview serves the actual dashboard and account endpoints without starting AI
providers, schedules, payments, or connected integrations. Its empty database is
separate from regular DATA data. It does not invent members or discussions.

Without a shared service configured, the forum uses the same accounts, sessions,
display names and SQLite database as the local Marketplace. Existing accounts can
sign in immediately; no second local account is required. The regular database is
`users/marketplace/marketplace.sqlite3`. Preview data uses the specified directory.
Local posts remain on that computer. A prominent Local preview notice makes this
clear. Forum data is outside the static assets and excluded from release archives.

That same account is managed from **Settings → Account**: sign in or sign up,
set the nickname and picture that appear on your posts, and change your username,
email, password and wallet addresses. See `ACCOUNT.md`. A dashboard connected to
a shared community keeps those accounts on the service; the Account page says so
and leaves the community sign-in on this page.

## Unified shared services

Use `DATA_SERVICES_URL` for one online account across Forum, Marketplace and
Settings > Account. Listings, reviews and discussions are shared; installed
extensions remain on each device. Local accounts are preserved and are never
uploaded automatically. `SHARED_SERVICES.md` covers setup, a two-device preview,
the deployment bundle and backups. Forum remains optional and makes no request
until opened. The shared account is available even while Forum is disabled.

## Legacy forum-only configuration

Cross-computer discussions use one `community_server.py` instance with a shared
account database. The service now supports the shared Marketplace API too; this
legacy connection only uses forum and limited account endpoints. It does not expose
the dashboard, AI tools, private memory, files, schedules, or extension execution.

For local development, start the service in one terminal:

```powershell
python dashboard/community_server.py --port 7810 --data-dir users/community-dev
```

In a second terminal, configure the preview before launching it:

```powershell
Set-Item Env:DATA_COMMUNITY_URL http://127.0.0.1:7810
python dashboard/marketplace_preview.py --port 7802 --data-dir users/forum-preview
```

Every dashboard pointed at the same service sees the same threads. A member signs
into the shared community account from any connected device. Shared accounts use
the Marketplace password/session implementation, but are held in the service's
database. Device-local Marketplace accounts are **not automatically uploaded or
merged**. A matching local username is not proof of ownership of a shared identity.
The shared sign-in form explains this distinction. Moving existing identities to
a public service requires an explicit migration and account-recovery plan.

`DATA_COMMUNITY_URL` accepts an HTTPS origin, with HTTP permitted only for loopback
development. This is an operator setting, not a member-provided redirect or URL.
It is read only when a forum request is made. Clearing it returns to local mode;
the page identifies the mode. An unavailable shared service returns an error and
never silently saves a shared post into local storage.

The dashboard's loopback bridge proxies an allowlisted set of forum requests.
It never forwards its bridge token, private profile, local Marketplace password,
or local session. Shared sessions use a separate HttpOnly, SameSite=Strict cookie
scoped to the forum, server port, and configured service origin. The session is
passed to the service as a bearer token and is not returned to frontend JavaScript.
The client verifies TLS, rejects redirects, limits response size and duration, and
does not retry writes automatically. The hub refuses browser-origin requests and
provides no CORS access. The local bridge's host, origin and authentication guards
remain in place.

For a public service, keep the Python listener on loopback behind a TLS reverse
proxy that routes only `/community/`. Preserve Content-Length responses and disable
response compression on this route. Set `--trusted-proxy` only for the exact proxy
address, and configure it to replace X-Forwarded-For with one verified client IP.
Without this, authentication limits apply to the proxy's shared IP. Use persistent
storage, backups, monitoring and an operator responsible for reports and recovery.
The included account implementation caps registration at 1,000 accounts. This is
a small-community foundation, not an unlimited-scale service.

No hosting was provisioned, domain configured, public service activated, paid API
called, or retail release published by this change. Public launch still requires
an approved hosting destination, HTTPS configuration, an identity/recovery plan,
and an operational moderation process.

## Moderation

Members may only change their own posts. Deleting a reply erases its body and
leaves a notice. Deleting the opening post hides the discussion, including its
replies. Hidden or deleted content is excluded from search. Reports are private
to the operator and do not automatically hide a post. Operators should review the
queue regularly; filing a report does not send an email or start an AI agent.

The local CLI is the only moderation surface. Point it at the database used by
the local forum or shared service:

```powershell
python dashboard/forum_admin.py --data-dir users/community-dev reports
python dashboard/forum_admin.py --data-dir users/community-dev moderate lock THREAD_ID --reason "Closed while reviewing reports"
python dashboard/forum_admin.py --data-dir users/community-dev moderate hide-thread THREAD_ID --reason "Removed after operator review"
python dashboard/forum_admin.py --data-dir users/community-dev moderate hide-post POST_ID --reason "Removed after operator review"
python dashboard/forum_admin.py --data-dir users/community-dev moderate suspend ACCOUNT_ID --reason "Repeated community rule violations"
python dashboard/forum_admin.py --data-dir users/community-dev moderate resolve-report REPORT_ID --reason "Reviewed and action recorded"
```

Corresponding reversal actions are `unlock`, `restore-thread`, `restore-post`, and
`unsuspend`. Moderator actions are recorded in an audit table. Restoring visibility
does not recover a body erased by its author. Opening posts are moderated with
thread actions. No HTTP endpoint can grant a member moderator powers.

Forum mutations are limited to 30 per account per five minutes, new discussions
to 8 per fifteen minutes, and reports to 8 per hour. These limits survive restart.
The shared service also limits request concurrency, authentication work, request
body size, response size and upload duration. SQLite transactions protect posts
and their account-scoped relationships.

## Verification

```powershell
python -m unittest discover -s dashboard/tests -p test_forum.py -v
python -m unittest discover -s dashboard/tests -p test_community_transport.py -v
python -m unittest discover -s dashboard/tests -p test_marketplace.py -v
python -m unittest discover -s dashboard/tests -p test_account.py -v
python dashboard/tests/forum_browser.py
python dashboard/tests/community_browser.py
node --check dashboard/forum.js
```

Checks use temporary databases and existing local Playwright/Chromium. The shared
transport suite runs two independent dashboards against one service. Browser
checks cover real forms, optional-page persistence, account isolation, safe text,
draft preservation, lost-response retries, search, reports, and desktop/mobile
layouts in both themes. Test screenshots contain example accounts and conversations
from a temporary database, not activity from a public community.
