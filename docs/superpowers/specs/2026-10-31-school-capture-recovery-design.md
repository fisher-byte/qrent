# Sydney failed-area capture recovery

The 2026-10-31 scheduled capture is still running. Three Domain queries exhausted their existing retries. The crawler correctly preserves old SQLite rows, but returns partial/1 and therefore never reaches the existing daily enrichment trigger. A standalone subset result is unsafe because the old daily selector trusts the latest result file.

Implement the requested bounded recovery after the original process exits. Keep the original full 41-query report immutable. Freeze its digest, query manifest and recovery requests; retry only still-failed Domain queries over the verified ordinary HK route, with no details, residential transport, scoring, commute calls or purchases. There are at most two recovery rounds across restarts. A started request without a trustworthy result remains uncertain and cannot be repeated automatically.

Retain every successful area entry, captured identity and school-cohort membership. Produce a separate aggregate with the original run identity only when all 41 original queries are successful and the original/round evidence can reconstruct the aggregate exactly. A partial report never becomes success by changing its exit code. Failed or uncertain recovery leaves the original inventory and ledger intact and does not start enrichment.

The school-cohort identity union must exactly equal captured identities. Source query counts bound distinct identities with overlap allowed. Freeze all public capture Python/configuration dependencies and launchers, excluding private environment, venv and cache files. Reject a missing/linked inventory before recovery and use a read-only original-inventory identity check before preparation; an incomplete replacement inventory cannot start paid enrichment.

The existing root entrypoint remains the service trigger. Its child wrapper handles a fresh full capture or explicit `--recover-only <original-report>`, always under the existing capture lock. The wrapper passes a fixed run tag so its exact full report is known. Daily selection and preparation validate a complete original or verified aggregate before any queue/API work.

Alternatives considered: allowing partial/1 to publish would bypass the requested complete-window boundary; repeating the full capture wastes already successful work. Neither is used. Implementation is local to the existing Python/shell crawler workflow; this change is not uploaded or executed on the server before review.
