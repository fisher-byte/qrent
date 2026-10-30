# QRent compact frontend and bilingual guide acceptance — 2026-10-31

## Production frontend

PR [#304](https://github.com/wiperi/qrent/pull/304) merged to original main as
`72ccc96f60afc77626bfd3d53022faeb1a2d62d4`. Required PR CI and main CI
[41117345379](https://github.com/wiperi/qrent/actions/runs/41117345379) passed;
backend deployment was correctly skipped. The original Vercel Production
integration reported success at 03:43:10 Asia/Shanghai. No additional hosting
permissions were needed. Deployment ID: `7447295978`.

Homepage copy now occupies one heading line and one description line. Both
languages at 390px show the search button within the first viewport (bottom at
646px); 768px and desktop layouts also passed. Repeated school/directory sections
are now a compact link row. The guide index uses article rows and a topic select,
with an expanded checklist on desktop and phone. Keyboard collapse/expansion,
checkbox persistence and reset, topic filtering, and same-article language
switching were exercised. Native Chrome confirmed the public homepage and
expanded Chinese guide checklist after deployment.

Ten distinct public source articles now each have complete Chinese and English
bodies, with unique slugs and reciprocal language links. All source tables,
numbers and external links remain. Duplicate document headers are omitted only
from presentation; full exports remain versioned. The three incorrect copied
summaries were replaced with summaries of their actual bodies. Original source
publication dates remain; the paired review timestamp supplies dateModified.
Future publication updates both languages in the same PR; Notion changes no
longer hot-publish independently and no runtime translation API is called.

The old Chinese before-renting URL redirects to shared-vs-private-renting; the
old English URL redirects to when-to-start-rental-search. Both targets were
verified against the actually served old source ID before release.

## Verification evidence

- 19 isolated SEO/content regressions passed; final pairing checks also compare
  each served body and title to its own source-language file.
- TypeScript, formatting and a production build passed. CI repeated the required
  checks against the exact merged source.
- Eight public HTTP checks passed, preserving the documented dynamic-blog-error
  initial-HTML limitation rather than claiming a no-JS article error passed.
- Twenty public article URLs were checked for title/body identity, language,
  unique body hashes, tables, links, original dates and reciprocal language links.
  Both guide indexes contain ten articles; two legacy redirects passed.
- All 38 production sitemap URLs were deeply scanned with the owner's
  site-audit-zyppy toolkit, cache hits zero: 5,693 PASS / 0 WARN / 0 FAIL.
- Independent review found no remaining Critical/Important frontend issue.

Technical checks do not establish search rankings or Search Console indexing.
Subscription, login/OTP and notification eligibility remain next-phase work.
This frontend/translation release made no paid provider requests or purchases.

## Crawler audit and recovery

A separate read-only check at 03:52 confirmed today's original collection had
naturally ended: 4,860 source IDs, 41 original site/area queries, partial/1. Domain
Maroubra, Kensington and Haymarket failed; the other queries remain successful.
The full run recorded 100 detail requests, zero scoring calls and 21,981,515
residential wire bytes. Old inventory was retained. The original report must
remain partial; it is not proof of a complete website update.

The previous root entry did not start daily enrichment on partial/1. Failed-only
recovery was deployed from fork commit
`7c9b940db66cad7050765227219f88d004619d75`, with 321 offline checks repeated on
the server. It retains the original inventory and request ledger, freezes each
request before sending it, and refuses to repeat a round with an uncertain
outcome. Capture validation requires the original 41 query identities and every
captured source ID in the existing inventory. A partial newer run cannot silently
fall back to an older successful run.

The one-off recovery began at 04:05 Asia/Shanghai. Round one requested only the
three failed Domain queries; round two requested only the still-failed Haymarket
query. Both rounds used the ordinary Hong Kong exit, with zero residential proxy
requests, model calls or commute calls. The recovered report is success/0, all
41/41 queries complete, with 5,064 source IDs. Its cohort and every source ID were
validated against the original inventory. The original partial report remains
unchanged, SHA-256
`3f335425f969e32e5e1b265c268e49c1f9c5f3b823562faa14e43056450f52d0`.
This is the original seven-page collection window, not all Sydney listings.

The existing wrapper automatically started the daily enrichment/publication
service at 04:09:54. Frozen input contains 2,733 deduplicated eligible addresses
and 4,085 original school associations. At 04:51, 850 addresses had been processed:
837 were published and 13 held. Held records in the inspected first batches lack
an acceptable exact transit result; unresolved requests are retained instead of
being repeatedly charged or filled with invented values. The final daily receipt
and queued read-only MySQL/public-API projection check are still pending; this
snapshot does not certify completion of the full batch.

The queued `qrent-daily-verification-20261031.service` requires the final frozen
daily and batch receipts, then compares every newly published field and school
association against a read-only consistent MySQL snapshot and the public search
API. It performs no provider calls or database writes. Its eventual result lives
under
`/root/qrent-scraper/output/school-enrichment-20261031_020005_264602/verification-*/publication-verification.json`.
An absent or unsuccessful result must not be interpreted as complete sync.

The original enabled crontab still invokes `/root/qrent-scraper/run_scraper.sh`
daily at 02:00 Asia/Shanghai. SQLite backup jobs remain enabled. The verification
service is ordered after this live enrichment run; no duplicate daily run was
started.

At 04:48, the formal server ledger recorded CNY 12.650741 in model spending or
reservations against the CNY 50 monthly cap, USD 0.60 in non-model spending
against the USD 4 monthly cap, and USD 0.578 of lifetime transit gift credit
against the USD 40 ceiling. The old USD 0.05 model reserve is already represented
in the model ledger and is excluded from the non-model total. These are program
ledger amounts, not a settled provider invoice. No new proxy or API purchase was
made during this work.

Private deployment receipts, HTTP/audit output, source exports and screenshots
are in the ignored recovery-workspace directory
`packages/scraper/output/compact-guides-20261031/`.
