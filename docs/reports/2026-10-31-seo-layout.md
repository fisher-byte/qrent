# QRent SEO layout production acceptance — 2026-10-31

The layout refinement is live through the original repository and frontend
deployment integration. No additional hosting account access was required.

## Release evidence

- Original PR: https://github.com/wiperi/qrent/pull/303, merged at
  2026-10-30 18:51:13 UTC.
- Production commit: `77a7c46c93ac3fcec5631cce153e2ae4308953a4`.
- Main CI: https://github.com/wiperi/qrent/actions/runs/41110419356, success.
  Backend tests/deployment were correctly skipped for this frontend change.
- Original Vercel production deployment reported success at
  2026-10-30 18:52:34 UTC (2026-10-31 02:52:34 Asia/Shanghai).
- Deployment receipt:
  https://vercel.com/wiperis-projects/qrent-frontend/69CigHS4Cz8rwfr4KcVzeHqKPk9H.
- Served page: https://www.qrent.rent/en, visually verified in native Chrome
  after the successful deployment on desktop and iPhone 16 dimensions.

## Delivered presentation

- Homepage heading wraps into balanced lines; the comparison sentence and
  “Free to use.” occupy separate paragraphs. Chinese introduction is also split.
- Shared container alignment, readable text width, section spacing and visible
  focus styling across the homepage, school directory and guide navigation.
- School area links use responsive columns and 48px minimum targets.
- Listing previews align addresses, sources, weekly prices and commute details.
  Search links retain the original school/area query and real listing values.
- Mobile navigation uses the existing SVG logo; headings and long text wrap
  without horizontal overflow.

## Verification

- 14 isolated SEO regressions, TypeScript, format checks and production build
  passed. Tests did not run the legacy database-backed Vitest setup.
- Six built-localhost HTTP checks and eight public production HTTP checks passed.
- The owner's `site-audit-zyppy` toolkit scanned all 25 sitemap URLs without
  cached results: 4,069 checks passed, zero warnings and zero failures.
- Local browser checks covered 1440px, 768px and 390px; keyboard focus and the
  Eastgardens search link were exercised. Live desktop and 393px mobile views
  confirmed the deployed layout.
- One Impeccable scan returned no findings. Independent source review found no
  Critical/Important issue.

The audit is a technical regression check, not evidence that all source-content
issues or search rankings have been resolved. Dynamic article errors retain the
documented upstream Next.js initial-HTML limitation; HTTP 404/noindex is checked
separately from browser recovery.

## Release boundary and follow-up

PR #302 had not deployed successfully and its production build logs remained
inaccessible. This release restores the previously served `f516490` guide
reader, preview and school mapping, while preserving independent safe UI fixes.
The successful deployment does not establish the root cause of the #302 failure.
That migration remains preserved on `fix/home-guide-links-20261031` and deferred;
duplicate article slugs, mislabeled source-body languages and the proposed old
English article redirect are not claimed as completed.

Subscription/login/OTP, daily notifications, one-month subscription limits and
eligibility rules remain next-phase requirements in the layout design spec.

This release made no model, transit or proxy requests, purchases, crawler
restarts, database changes or backend deployments. It does not establish a new
daily-crawler completion result.

Private deployment/CI receipts, HTTP/audit logs and screenshots are retained in
the ignored `packages/scraper/output/seo-layout-20261031/` evidence directory in
the recovery workspace. No private credentials or databases are part of this
report.
