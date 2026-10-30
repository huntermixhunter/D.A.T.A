# Magimatix community forum

Public site:

https://forum.magimatix.com

Cloudflare Pages serves the static website. Supabase stores community profiles,
topics, replies, likes, follows, reports, and moderation records. The laptop and
the local Python bridge are not needed to keep the website online.

## Magimatix styling update: 2026-10-30, published

The forum now uses the main site's logo, Geist and Geist Mono typography, black
glass surfaces, cyan/blue/purple accents, gradient buttons, header/footer links,
and the same ambient background video. The navigation matches the published
main-site destinations; the separate, unpublished Dashboards service is not linked.

The original logo and `bg-video-0605.mp4` were copied from `Magimatix/site/public`.
The Latin font subsets were copied from the main site's current Next build.
Both fonts use the identical SIL Open Font License included as `public/geist-OFL.txt`,
obtained from the Google Fonts Geist distribution. Assets are included explicitly
by the existing build allowlist. CSP permits same-origin video and retains the
existing restrictions on scripts, styles, connections, frames, and forms.

`src/shell.js` handles the mobile menu, Escape/resize behavior, copyright year,
and background playback controls. Reduced-motion and data-saving preferences
prevent the video request; a static background remains. Forum data, account,
registration, moderation, and email configuration are unchanged.

`npm run build:release` and `python tests/browser.py` passed. The browser suite
uses temporary local SQL, Auth/CAPTCHA stubs, and blocked external requests.
It covers posting, replies, login, draft preservation/retry, likes, signout,
reserved names, search, sort, categories, mobile menu keyboard/resize behavior,
responsive dialogs, reduced motion, and video pause/play at 320, 390, 768, 1024,
and 1440px. No page errors or horizontal overflow were observed. It sends no
real email and writes no hosted records. Evidence: `test-results/brand-verification.json`
and `test-results/brand-*.png`.

Published with the owner's approval on 2026-10-30. Production deployment
`b44e6183-84cd-464f-814b-1fe6702ba0be` serves the custom domain. All 11 public
files match the release build, allowing for Cloudflare's existing analytics
script injection in HTML. Public metadata and discussion reads return HTTP 200.
Registration remains open. Evidence: `test-results/brand-live-verification.json`.
Previous production for rollback: `a195cd49-9a9a-473c-849f-d4821a3900a3`.

## Current launch state: 2026-10-30

- Pages project: `magimatix-community`, production branch `main`.
- Production deployment: `b44e6183.magimatix-community.pages.dev`.
- Custom domain: `forum.magimatix.com`, active with HTTPS.
- Supabase project: `hrkwubdlunpuqcwswmqd`, `us-west-2`, in the Free
  `Magimatix Community` organization. The initial migration is applied.
- Public browsing works. There are no seeded members or discussions.
- Public registration is open following the owner's explicit approval on
  2026-10-29. The deployed `config.json` has `registrationEnabled=true`, and
  Supabase Auth has `disable_signup=false`.
- Auth uses confirmed emails, a 12-character password minimum, and Turnstile.
  SMTP is configured for `community@send.magimatix.com` with a domain-restricted,
  send-only Resend key. Email rate limit is four messages per hour for now.
  Resend's `mixer96er@outlook.com` account was verified on Free at $0/month,
  with no payment method and all pay-as-you-go switches disabled.
  Confirmation and recovery emails were delivered to the owner's Gmail inbox
  using a temporary test address. Both links, password login, signout, password
  update, and login with the new password passed against the live website.
  The test account and its temporary credentials were removed afterward.
- The DATA dashboard's optional Forum entry opens this website. Local dashboard
  and Marketplace accounts are separate; no private accounts, history, or posts
  were migrated. The entry is included in published retail release v1.0.58.
  Enable it under Settings > View > Navigation > Community Forum, then click
  Forum in the sidebar. The local dashboard integration test passed again on
  2026-10-30, including persistence, the external destination, and opener isolation.
- Magimatix's navigation links and `/forum` redirect are live. Both were verified
  against the public main website on 2026-10-30.

Free services have quotas and availability limits. Supabase Free projects may
pause after inactivity. Do not upgrade a plan or enable billable features without
the owner's explicit approval.

## Develop

Use Node 20 or newer and Python 3. Dependencies are pinned in `package-lock.json`.

```powershell
npm ci
npm run build
python scripts/serve.py --port 7816
```

Preview:

http://localhost:7816

Without `config.json`, the preview shows the layout with interactions disabled.
Copy `config.example.json` to `config.json` and set the public Supabase project
URL, publishable key, Turnstile site key, and public forum origin to connect it.
`config.json`, `dist`, and test output are ignored by Git.

Only a publishable key or the legacy `anon` key may enter the static build. Never
put a Supabase secret/service-role key, database password, Turnstile secret,
Resend API key, or Cloudflare credentials into this directory or its output.
The build rejects extra configuration fields and service-role JWTs.

## Validate

```powershell
npm run test:database
npm run build:release
npm run test:browser
python ../dashboard/tests/hosted_forum_browser.py
```

The 46 database checks use isolated PGlite databases and exercise the actual
migration, permissions, ownership, idempotency, moderation, limits, and filters.
Browser integration uses that same SQL behind a local fixture. Auth sessions and
CAPTCHA are stubbed in this test, and all other external requests are blocked.
It tests forms, public text rendering, posting, draft preservation, retry,
signout, reserved names, and 320/390/768/1440px layouts. It does not prove hosted
email delivery. The dashboard test checks the optional setting, external link,
opener isolation, account explanation, and absence of local forum requests.

Live checks passed for HTTPS, public metadata, anonymous write rejection, the
initial signup closure, and the production Turnstile widget. Public signup and
confirmation were verified after activation. Screenshots are in `test-results`.
Cloudflare's optional analytics beacon is blocked by the website's CSP; analytics
is not required for the forum.

Live email verification on 2026-10-29 used an admin-created, unconfirmed test
account while public signup stayed closed. The confirmation resend used a real
Turnstile token, and password recovery ran through the public form. Resend
recorded both messages as Delivered to Gmail. Confirmation, password login,
logout, recovery, and login with the replacement password all passed. The account
was deleted and the Auth user count returned to zero. Sanitized evidence is in
`test-results/live-email-check.json`; no test password or login link is retained.
After activation on 2026-10-29, the public Create account form returned HTTP 200
and created an unconfirmed account. Resend recorded its confirmation email as
Delivered. Following the confirmation link verified the account and signed in.
Password login and logout then passed. The temporary account and credentials
were removed; the Auth user count returned to zero. Sanitized activation evidence
is in `test-results/live-signup-check.json`. Email confirmation, Turnstile,
the 12-character password minimum, and the four-email-per-hour limit remain on.

The older Python forum remains available for regression testing. Use
`python ../dashboard/marketplace_preview.py --local-forum --port 7802` to preview
it. Its browser suites explicitly select `hosted_forum=False`.

## Deploy

Deploy only `dist`, never the repository root. This is a static Pages project;
there are no Workers Functions, metered AI calls, or payment integrations.

```powershell
npm run build:release
npx wrangler@4.92.0 pages deploy dist --project-name magimatix-community --branch main --commit-dirty=true
```

Wrangler 4.92.0 was verified for this deployment. The installed 4.155.0 delegates
Pages project creation to Workers and failed without a Worker entry point.
Do not accept a migration to a different hosting product during a routine deploy.

The domain CNAME points to `magimatix-community.pages.dev`. Keep the existing
Magimatix apex and www DNS records unchanged. Public redirects and CSP headers
are generated by the build. Supabase redirects are limited to the public custom
origin and the stable Pages origin, not arbitrary preview URLs.

Membership was opened with the owner's explicit approval on 2026-10-29 after
verifying the SMTP account's free plan and confirmation/password-reset delivery.
Both Auth `disable_signup=false` and the deployed `registrationEnabled=true`
are verified. To close registration, set Auth `disable_signup=true`, set
`registrationEnabled=false` in the public build, and redeploy. Recheck billing and
delivery if the provider or credentials change. Do not infer mail delivery from
local stub tests or the Turnstile widget alone.

## Moderation and operations

Run moderator actions from the Supabase SQL editor as the database owner.
Public users cannot call these functions or read raw tables, reports, or email
addresses. Reports require manual review and do not send notifications.

```sql
select r.id, r.post_id, r.reason, r.created,
       p.thread_id, p.body
from forum_private.reports r
join forum_private.posts p on p.id = r.post_id
where not r.resolved
order by r.created;

select forum_private.moderate('lock', '<thread UUID>', 'Reason for this action');
select forum_private.moderate('hide-thread', '<thread UUID>', 'Reason for this action');
select forum_private.moderate('hide-post', '<post UUID>', 'Reason for this action');
select forum_private.moderate('suspend', '<account UUID>', 'Reason for this action');
select forum_private.moderate('resolve-report', '<report UUID>', 'Review completed');
```

Reversal actions are `unlock`, `restore-thread`, `restore-post`, and `unsuspend`.
Author-deleted text cannot be restored. Review the report queue regularly and
export database backups before schema changes. The first schema was applied
through the Supabase management API; do not blindly re-run the initial SQL on the
live database. Use a new migration for subsequent changes.

Official service documentation:

https://supabase.com/docs/guides/auth/auth-smtp

https://supabase.com/docs/guides/auth/auth-captcha

https://developers.cloudflare.com/pages/get-started/direct-upload/

https://supabase.com/pricing
