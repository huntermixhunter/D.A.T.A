# DATA extension marketplace

The marketplace supports local mode and a shared online service. Accounts,
creator listings, versioned uploads, independent security assessments and ratings
can be shared across computers. Installation into the dashboard stays on each
device. Payments remain disabled. Public hosting has not been activated.

The starter collection adds ten free workflow and setup packs. Advanced application
setup lives in Settings > Upgrades, with links from the relevant listings.
See `ADVANCED_TOOLS.md` for the collection, setup controls and execution limits.

Set `DATA_SERVICES_URL` to connect Marketplace, Forum and Account settings to one
shared identity. See `SHARED_SERVICES.md` for the two-device preview, deployment
bundle and operating instructions. The older `DATA_COMMUNITY_URL` setting connects
only the Forum. Without either setting, the local behavior below applies.

## Try it locally

From the repository root:

```powershell
python dashboard/marketplace_preview.py --port 7800
```

Open the preview:

http://localhost:7800/#marketplace

This serves the real dashboard frontend and marketplace endpoints on loopback,
without starting AI providers, schedules, billing, or connected integrations.
Preview records live in `users/marketplace-preview/marketplace.sqlite3`.
The regular DATA bridge uses `users/marketplace/marketplace.sqlite3` and exposes
the Marketplace navigation entry. Both directories are excluded from Git and
retail release archives. Marketplace access currently requires this computer;
LAN addresses, tunnels and proxy requests are refused.

In local mode, accounts belong to this marketplace database. They do not register an online
identity. They do carry one thing into the dashboard: signing in or renaming
copies the nickname and picture to the local Captain profile, so the header pill
and what Data calls you match your posts. Private dashboard state — memory,
conversation history and project folders — is untouched by signing in or out.
Settings → Account manages the rest of the account (email, password and wallet
addresses); see `ACCOUNT.md`. Passwords are salted and hashed
with PBKDF2-SHA256 at 600,000 iterations. Session tokens are random, stored hashed,
and expire after seven days. Cookies are HttpOnly, SameSite=Strict, and scoped to
the marketplace path, with separate names per local server port. HTTP is accepted
only on loopback. A public service would require HTTPS and Secure cookies.

## What works

- Browse and search themes, skills, tools, pages and upgrade packs. Filter by
  independent review status and sort by price, date or rating.
- Create an account with username, display name, password and optional PNG/JPEG
  profile image. Sign in, sign out and edit the name or image.
- Upload a ZIP with a title, description, optional listing image and a USD price.
  Zero means free. Paid prices can be saved, but checkout and paid downloads are
  disabled. No money, seller payout or service credit balance is simulated.
- Install free packages after reviewing permissions and acknowledging the safety
  notice. The install transaction pins the displayed version and SHA-256.
- Apply themes; add sidebar pages and text tools; inspect skills and prepare a
  draft chat request. A skill request is never submitted automatically.
- Keep installations, selected colors and checkbox values separate by account.
  Signout removes that account's active pages and applied colors from the browser.
- Disable, enable or uninstall. Update explicitly, or restore a previously
  installed version after reviewing its status. Updates never inherit a prior
  version's security badge and never apply automatically.
- Rate and review an installed package. One review per account per listing can
  be updated; creators cannot review their own listings. Reviews retain the
  version used by the reviewer. These are local installation receipts, not proof
  of a purchase or independent real-world users.

## Package contract, version 1

Packages are declarative data interpreted by DATA's trusted runtime. They cannot
inject arbitrary JavaScript, Python, shell commands, HTML, CSS rules, dependencies
or installation hooks. Custom executable tools and fully programmable pages need
a separate isolated runtime and capability design before they can be supported.

| Type | Entry | Supported behavior | Permission |
|------|-------|--------------------|------------|
| `theme` | JSON | Allowlisted dashboard colors | `appearance.colors` |
| `skill` | Markdown | Instructions shown to the user, manual chat draft | `agent.instructions` |
| `tool` | JSON | Built-in browser text operations | `navigation.page`, `text.local` |
| `page` | JSON | Headings, paragraphs and saved checkboxes | `navigation.page` |
| `upgrade` | Multiple entries | A bundle of supported components | Exact combined permissions |

A root-level `manifest.json` is required:

```json
{
  "schema_version": 1,
  "id": "studio-notes",
  "version": "1.0.0",
  "name": "Studio Notes",
  "description": "A small project checklist in the sidebar.",
  "kind": "page",
  "license": "Apache-2.0",
  "external_costs": "None. Checklist values stay in this browser.",
  "permissions": ["navigation.page"],
  "components": [
    {"kind": "page", "entry": "page.json", "label": "Studio Notes"}
  ]
}
```

The accompanying `page.json`:

```json
{
  "blocks": [
    {"type": "heading", "text": "Before a release"},
    {"type": "paragraph", "text": "Keep the scope and verification visible."},
    {"type": "check", "text": "Review the changes"}
  ]
}
```

Include the actual license text and any required notices. A license field alone
does not supply the license document. Creators affirm their distribution rights.
Unknown manifest fields and undeclared content are rejected. Optional documents
must be Markdown/text files named with a README, LICENSE or NOTICE prefix.

The creator form offers downloadable starter ZIPs for all five types. Edit the
manifest and component files, then ZIP the files themselves, with no enclosing
folder and no directory entries. The server never extracts archives. It validates
every entry and stores the immutable ZIP and metadata in SQLite.

Limits: 2 MiB ZIP, 40 files, 256 KiB per expanded file, 1 MiB expanded total,
8 components, 40 blocks per page, 40,000 characters per skill. ZIP paths, symlinks,
duplicate names, encrypted archives, compression ratios, executable extensions,
JSON duplicate keys and exact permissions are checked. Images are limited to
PNG/JPEG, 512 KiB, 4096 pixels per side and 8 megapixels. Media is delivered
separately and fetched with the dashboard's authentication header.

Themes use six-digit hex colors and these variables only: `--data-orange`,
`--data-orange2`, `--data-yellow`, `--data-blue`, `--data-blue2`,
`--data-lightblue`, `--data-purple`, `--data-teal`, `--data-red`, `--data-green`,
`--data-pink`, `--data-bg`, `--data-bg2`, `--data-text`, `--data-text-dim`.
The theme entry has one `tokens` object mapping these names to values.

Tools declare `operations` and `placeholder`. Supported operations are
`word-count`, `uppercase`, `lowercase`, `trim-lines`, `deduplicate-lines`, and
`sort-lines`. These operate on text in the active browser page. They do not send
text to a provider or persist it.

## Security review

Automatic checks never grant a reviewed badge. Every community submission starts
unverified and enters the operator review queue. Instructions are treated as
untrusted third-party guidance. A review is evidence about a specific version,
not a safety guarantee or authorization for the agent to spend money.

Review occurs outside the untrusted dashboard HTTP API:

```powershell
python dashboard/marketplace_admin.py queue
python dashboard/marketplace_admin.py inspect LISTING_ID --version 1.0.0
python dashboard/marketplace_admin.py review LISTING_ID --version 1.0.0 --sha256 EXACT_DIGEST --reviewer "DATA Security" --risk low --verdict approved --findings-file findings.txt
```

For the preview database, put `--data-dir users/marketplace-preview` before the
subcommand. Inspect all files as data, without running package instructions or
following links. Record what was inspected, the review method and its limitations.
Only run the review command after the inspection. The CLI records a decision; it
does not invoke an AI service. An independent reviewer can use the inspection
output without granting a submitted package command execution.

Risk can be `low`, `moderate` or `high`. Verdict is `approved` or `rejected`.
Rejection blocks installation, re-enabling, rollback and runtime loading, including
already installed copies. It survives restart and policy changes until explicitly
overturned. A newer package version needs a new review. Positive badges require
the current policy and exact package ID, version and hash. The queue and review
commands are local operator tools, never creator-controlled HTTP endpoints.

The five initial packages in `marketplace_catalog/` were independently inspected
by a security-review AI agent. `reviews.json` records the exact reviewed hashes,
method, findings and date. The runtime recomputes deterministic ZIP hashes. These
are shipped local attestations, not cryptographic publisher signatures.

| Starter | Source | Risk |
|---------|--------|------|
| Midnight | Existing dashboard color-token system | Low |
| Project Checklist | Existing sidebar navigation and browser storage | Low |
| Text Workshop | Built-in local text operations | Low |
| Focus Pack | Color tokens plus a daily checklist page | Low |
| Repository Snapshot | Adapted repository-inspection workflow | Moderate |

Each starter includes its notice and complete Apache 2.0 license. Version 1.0.1
adds the self-contained license files. Earlier local prototype versions remain
immutable, and an explicit update is offered if they were installed.

## Moving optional bundled skills

The current core bundle remains intact. There is no reliable usage evidence to
call a particular skill unused. Specialty candidates include research search,
specific social-report formats, banners and slide creation. Before moving one,
check its license, required scripts/resources, private paths, external costs and
whether the extension runtime can preserve its behavior. An instruction-only
adaptation is not a replacement for an entire tool with executable dependencies.
Remove a core entry only after its optional replacement has been tested and
existing installations retain a working upgrade path. Do not remove installed
skills from a user's discovery directories during this migration.

## Before a public marketplace

Shared identity, catalogue transport, pagination, device installation isolation,
review rechecks and backup tooling are implemented in `SHARED_SERVICES.md`.
Public activation still needs an approved hosting route, HTTPS/proxy setup,
infrastructure quotas, backup retention, moderation operations and an account
recovery policy. Existing local accounts are never migrated automatically.

Paid checkout additionally needs server-authoritative entitlements, seller
onboarding, payment webhooks, refunds, settlement and support policies. Hosted
service credits remain a separate design. Public activation, paid APIs,
provisioning and accepting payments have not been performed by this build.

## Checks

```powershell
python -m unittest discover -s dashboard/tests -p test_marketplace.py -v
python -m unittest discover -s dashboard/tests -p test_account.py -v
python dashboard/tests/marketplace_browser.py
python dashboard/tests/account_browser.py
node --check dashboard/marketplace.js
```

Backend checks cover real HTTP guards and the account/package lifecycle. The
browser check uses a temporary database and a bridge-token-protected local
server. It exercises upload, images, version changes, installs, rollback,
reviews, text processing, account isolation, theme reload, and desktop/mobile
layouts. Browser verification needs an existing Playwright/Chromium installation.
It makes no provider calls and writes screenshots only to the ignored local
`screenshots/` directory.
