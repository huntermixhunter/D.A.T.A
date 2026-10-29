# DATA shared accounts, marketplace and forum

The standalone service stores online accounts, creator profiles, listings,
versioned package archives, download receipts, ratings and discussions. The DATA
dashboard supplies the interface through a narrow local proxy. Installed
extensions belong to each device. Private chats, memory, project files, provider
credentials and bridge tools are never included in this protocol.

This implementation has been tested locally with two independent dashboards.
No public endpoint or retail release has been activated. Payments, seller payouts
and hosted execution are not implemented. Dollar-priced listings cannot be
downloaded or installed while checkout is disabled.

## Choose the connection mode

| Configuration | Marketplace and Account settings | Forum |
|---|---|---|
| No service URL | Local account and catalogue | Same local account |
| `DATA_COMMUNITY_URL` only | Local account and catalogue | Legacy shared forum account |
| `DATA_SERVICES_URL` | Shared online account and catalogue | The same online account |

`DATA_SERVICES_URL` takes precedence. Invalid configuration or an unavailable
service returns an error, never a fallback that writes shared content locally.
The URL is an operator configuration, not a URL supplied by package authors.
Existing local accounts and installs are preserved. They are never automatically
uploaded, merged, or treated as proof of ownership of an online username.

The base URL accepts a path prefix, so a dedicated service can sit behind a
route on an existing website. No new subdomain is required by this implementation.
The reverse proxy must strip the base path and forward `/marketplace/*` and
`/community/*` to the standalone service. It must never route to the private
dashboard bridge. The backend needs persistent disk and a long-lived Python
process; serving the dashboard HTML on a static website does not supply these.

## Review two local devices

From the repository root:

```powershell
python dashboard/shared_preview.py
```

Device A:

http://localhost:7803/#marketplace

Device B:

http://localhost:7804/#marketplace

This starts an isolated service on port 7812 and two independent preview stores
under `users/shared-preview`. It imports the five bundled DATA Library listings,
but creates no example members or discussions. No AI, billing, schedules or
connected integrations run. Settings remains available in the preview.

Create an account in A, then sign in with it in B. A listing or discussion
created on either device appears on both. An extension installed in A is not
installed in B. Enable the optional Forum in Settings > View > Navigation.
The preview omits general dashboard integrations and local profile management;
the shared Account settings endpoints work normally.

For independently managed processes:

```powershell
python dashboard/community_server.py --port 7812 --data-dir users/service-dev --seed-library
```

Configure each desktop process before starting it:

```powershell
Set-Item Env:DATA_SERVICES_URL http://127.0.0.1:7812
python dashboard/marketplace_preview.py --port 7803 --data-dir users/device-a
```

HTTP is permitted only on loopback. Remote URLs require verified HTTPS. The
client rejects redirects and unbounded, compressed or chunked responses. Each
request has a wall-clock deadline and bounded concurrent workers. Public service
responses must retain a correct Content-Length. A proxy should disable response
compression and buffer responses while preserving Content-Length.

## Account and device boundaries

One HttpOnly, SameSite=Strict cookie serves `/marketplace`, including its forum
routes. Its name includes the local bridge port. Its value is bound to the
normalized service URL, including any path prefix. A local account cookie or a
legacy forum cookie cannot authenticate the shared service. The browser never
receives the raw online bearer token in JSON. The public service deliberately
rejects browser-origin requests and has no CORS surface; the local proxy retains
its host, origin and bridge-authentication guards.

Username, password, public nickname and picture, optional private email, and
optional private receiving addresses live with the selected online account.
Email and wallets are returned only through the authenticated owner's Account
settings. Changing credentials or wallets requires the current password. Password
changes revoke other sessions. An incorrect confirmation password does not erase
the current session. Email is unverified and cannot recover an account.

Changing service URLs or account IDs switches the installation namespace. The
device database is `shared_extensions.sqlite3`, beside the local marketplace
database. It contains approved packages and installation history, not passwords,
session tokens or private profile fields. The service has no device-install or
runtime execution endpoint.

## Packages and review enforcement

Downloads require the exact displayed version, SHA-256, permissions and explicit
acknowledgement. The service validates these, checks the price and review status,
and issues a download receipt. The device independently checks the archive bytes,
manifest and permissions before activating declarative components. It rejects a
version whose bytes changed. Uploaded scripts and arbitrary executable code are
not supported. A package cannot execute a command merely by being published.

Receipts authorize community ratings; they attest to a download, not independently
verified installation or execution. The public service never records an enabled
installation for a desktop. Installs, updates, enable/disable, uninstall and
rollback all remain on the requesting device. Rollback requires that account's
prior install on that device.

At runtime refresh and before enable/rollback, the device checks the service's
review for each exact version. A rejected, missing or changed package cannot
load. Runtime refresh revalidates cached bytes and removes unavailable components
from the browser. A service outage leaves stored packages intact but prevents
authenticated runtime loading until connectivity returns. Already rendered
components are rechecked on refresh; this is not a push revocation channel.

Catalogue responses are paginated in groups of 25. The desktop collects the
bounded catalogue for its existing filters. Current limits are 1,000 accounts,
500 listings, 50 listings per creator, 100 versions per listing and 100 active
installation records per account per device. This is a small-community service.
Set infrastructure disk quotas and monitor growth before public registration.

## Package and operate the service

Build an allowlisted source bundle from the current working tree:

```powershell
python tools/package_shared_service.py dist/DATA-shared-service.zip
```

The command refuses to overwrite an existing archive. Its manifest records
SHA-256 for every included file. It includes the standalone service, moderation
tools, package review tools, docs and bundled catalogue. It excludes desktop
HTML/JavaScript, the private bridge, profiles, conversations, credentials, runtime
databases and installer contents. It is not the desktop retail release.

After extracting to a dedicated service directory, run with Python 3.10 or newer:

```text
python dashboard/community_server.py --host 127.0.0.1 --port 7812 --data-dir /var/lib/data-community
```

Use a dedicated unprivileged OS user, persistent storage outside the code
directory, a process supervisor, and an HTTPS reverse proxy. Expose only the two
public route prefixes. Set `--trusted-proxy` only for the exact reverse proxy
address after configuring it to replace X-Forwarded-For with one verified client
IP. Otherwise authentication limits apply to the proxy's common IP. Proxy body
limits must permit up to 5 MiB JSON package uploads. Keep browser headers rejected.
The service itself provides a 15-second connection deadline, concurrency limits,
route-specific body limits, request limits and bounded JSON responses.

Explicitly add `--seed-library` when the bundled catalogue should be imported.
Operator queue inspection does not implicitly add catalogue entries.

```text
python dashboard/shared_service_admin.py --data-dir /var/lib/data-community status
python dashboard/shared_service_admin.py --data-dir /var/lib/data-community backup /secure-backups/data-community-20261029.sqlite3
python dashboard/forum_admin.py --data-dir /var/lib/data-community reports
python dashboard/marketplace_admin.py --data-dir /var/lib/data-community queue
```

Backup uses SQLite's consistent backup API, verifies the resulting database and
refuses to overwrite a file. Treat backups as private account data. Test restoring
into a separate service directory before launch. For production restore, stop
the service first, preserve the current database, restore the verified snapshot
and restart. The Forum and Marketplace guides describe moderation and exact-hash
package review. These powers are not exposed over HTTP.

Public activation still needs the chosen hosting route, HTTPS/proxy integration,
capacity and cost approval, backup retention, moderation ownership and an account
recovery policy. There is no email verification or password-reset service. A
retail update must include the new modules and configure the approved service
URL; editing this checkout alone does not update installed copies.

## Verification

```powershell
python -m unittest discover -s dashboard/tests -p test_shared_services.py -v
python -m unittest discover -s dashboard/tests -p test_community_transport.py -v
python -m unittest discover -s dashboard/tests -p test_marketplace.py -v
python -m unittest discover -s dashboard/tests -p test_forum.py -v
python -m unittest discover -s dashboard/tests -p test_account.py -v
python dashboard/tests/shared_services_browser.py
python dashboard/tests/marketplace_browser.py
python dashboard/tests/community_browser.py
python dashboard/tests/account_browser.py
```

These checks use temporary databases, loopback HTTP and existing browser tooling.
They do not use paid APIs or production profiles.
