# Sydney failed-area recovery implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. Steps use checkbox syntax for tracking.

**Goal:** Finish or resume a complete Sydney daily capture by retrying only failed Domain areas, then permit the original enrichment trigger.

**Architecture:** Immutable original report plus durable per-round evidence; reconstruct and validate a separate 41-query aggregate. Reuse the original capture lock and root service trigger. A verified complete-window selector guards daily preparation.

**Tech Stack:** Existing Python standard library, unittest, shell and existing school-scope configuration; no dependencies.

**Spec:** `docs/superpowers/specs/2026-10-31-school-capture-recovery-design.md`

## Global constraints

- The current server run must finish naturally; no upload or server execution in this task.
- Preserve original reports, SQLite rows, identity/school unions and the sole formal ledger.
- At most two recovery rounds, only failed Domain queries, ordinary HK transport; no residential/model/map requests or purchases.
- All 41 queries must be proven successful before the existing daily service can start.
- Push only the existing `fisher-byte/qrent` crawler branch after local verification.

## Review focus

- A subset, duplicate query, wrong run or modified original must be rejected before requests/publication.
- A restarted recovery must reuse successful evidence and never repeat an uncertain started round.
- Locks must cover children as well as recovery orchestration.
- Non-Domain or detail/preflight failures must remain incomplete; no paid fallback.
- A newer partial capture must not fall back to an older success.

### Task 1: Evidence validation and bounded recovery

**Files:** new `packages/scraper/ops/recover_school_capture.py`; tests `packages/scraper/tests/test_capture_recovery.py`; small fixed-run-tag addition in `recovery_runner.py`.

**Interfaces:** `validate_complete_capture(path)` reads and verifies complete evidence; `recover(root, original, lock_fd, run_round=None)` returns a complete report or raises an incomplete/error condition. The default round runner invokes only the original Domain capture with fixed safe arguments.

- [x] Write failing offline tests for full-scope validation, two-round subset recovery, union preservation, restart/uncertainty, tampering and lock/transport bounds.
- [x] Observe RED; implement immutable evidence and reconstruction; observe GREEN.

### Task 2: Original daily integration and publication guard

**Files:** `ops/run_school_capture.sh`, `ops/daily_enrich.py`, `ops/prepare_school_enrichment.py`, their existing offline tests.

**Interfaces:** `select_complete_capture(folder)` selects the newest original run and accepts its verified aggregate, never an unrelated subset. The existing root wrapper receives success only after completion.

- [x] Add failing tests for subset/newer-partial rejection and original-run selection.
- [x] Wire the shell and both daily guards; retain source/job checks and original enrichment service.
- [x] Run the isolated complete scraper suite, shell syntax and diff checks.
- [x] Commit scoped source/tests/docs and push the existing fork branch; stop before server deployment.

## Execution evidence and decisions

- RED: 11 missing-capability tests, then fixed-run/daily-guard failures, actual shell exit-2 false triggers, and missing-earlier-round reset were observed before their fixes. GREEN: 314 scraper tests, shell syntax and diff check passed.
- Keep non-Domain/listing errors held rather than broadening recovery: ordinary Domain retries cannot repair other providers or incomplete detail fields.
- Convert child error 2 to failure 1 because the existing root treats 2 as publishable; the root deployment contract is otherwise retained.
- Recovery CLI is restricted to the current Shanghai calendar day, avoiding a historical completion accidentally starting today's unrelated daily service.
- Parent review is the independent whole-change gate. No server upload, collection recovery or production write is included in this implementation turn.
- Independent review fixes: exact cohort identity union; per-source overlap-aware count bounds; complete capture-source fingerprint; original-inventory existence/path and read-only captured-ID coverage before preparation. Their counterexamples were RED before the fixes. Final external-network-blocked suite 321/321 and independent targeted suite 32/32 passed; today's real 4,860-ID original report passes the stricter validator while remaining incomplete.
