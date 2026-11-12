# Morpho metadata request throttling

- Date: 2026-11-12
- Branch: `fix/morpho-metadata-rate-limiting`
- Status: Implemented locally; 60 focused tests pass and Claude Opus 5.5 approved the code follow-up.

## Problem and scope

Production logged 1,311 failed Morpho metadata requests between 16:51 and
16:52 UTC on 11 November, with similar bursts on 2, 4 and 10 November.
`_query_morpho_api()` currently uses plain `requests.post()` from concurrent
metadata workers. Per-vault cache locks do not limit different vaults.

The [Morpho API documentation](https://docs.morpho.org/api/#rate-limits-and-best-practices),
checked on 12 November, publishes per-IP limits of 750 requests/minute and
3,334 requests/10 minutes. Its documented 429 cooldowns are ten minutes and
seven days. The production logs did not record the actual response headers.

Add an aggregate **2 requests/second** limit and honour provider cooldowns
without sleeping scanner workers for days. Keep existing V1/V2 queries,
24-hour cache freshness, chain support and definitive not-found handling.
No changes to historical prices, scanner worker counts or API schemas.

## What was overengineered

| Previous proposal | Simpler decision |
| --- | --- |
| Custom transactional SQLite slot allocator | Use the installed `pyrate_limiter.FileLockSQLiteBucket`, which already refreshes counts and coordinates processes. |
| Three HTTP attempts, exponential backoff, jitter and a combined operation deadline | Make one HTTP attempt per lookup, as today. Ordinary transient failures retry on a later scan. |
| Separate handling of 5xx-directed cooldowns and speculative GraphQL rate-limit codes | Implement the observed HTTP-429 path. Revisit other response shapes only with evidence. |
| Eight-day corrupt-file quarantine and automatic recovery state machine | Report invalid state clearly, defer network reads, and document operator repair. Retry reading the state on later calls. |
| New counters, heartbeat and pause/rollback mechanisms | Keep existing scan progress and log cooldown transitions. |

The shared throttle, persisted cooldown and preservation of old warnings are
necessary. Removing them would leave the request burst or a data-loss risk.

## Implementation

### 1. Reuse the existing limiter components

Add `eth_defi/erc_4626/vault_protocol/morpho/session.py` with a small session
factory and a read helper. Use existing Requests, PyrateLimiter and Filelock
dependencies; no new package or custom quota schema.

- Default request accounting to `~/.tradingstrategy/morpho/rate-limit.sqlite`.
  Use one provider identity for all chains and both vault API versions.
- Use `FileLockSQLiteBucket`, not plain `SQLiteBucket`. The installed plain
  bucket caches its size and has only a per-instance lock. The file-lock
  variant re-reads size and supplies the cross-process locking we need.
- The library defaults its lock to `/tmp/pyrate_limiter.lock`. Bind the Morpho
  bucket to a dedicated lock beside its SQLite file, so containers sharing
  production state also share the lock. If this requires a tiny subclass to
  replace the bucket's lock, keep that compatibility detail in this module and
  cover it with the separate-process test. Do not change library globals.
- Reuse worker-local Requests sessions and a process-shared limiter. Separate
  processes use the same database, provider identity and shared lock path.
  Configure an explicit wall-clock time source for persisted bucket timestamps.
- Before each actual HTTP attempt, acquire quota using the limiter's public
  API, then recheck provider cooldown. Disable hidden HTTP-adapter retries and
  automatic redirects on this fixed GraphQL endpoint.
- Bound quota queueing to 60 seconds. With the observed production
  `MAX_WORKERS=24`, a wave needs roughly 12 seconds at this rate. Exhausting
  the queue budget returns `transient_error`; it is not a broken vault.
  Recompute the remaining budget after contention: the library's `max_delay`
  limits one wait, not total queueing. Allow clock/sleeper injection in tests.
- Retain the current 30-second request timeout. There is no new promise of a
  hard end-to-end deadline: Requests uses an inactivity timeout.

This borrows the GRVT/Derive session pattern without copying its urllib3 retry
policy. `LimiterAdapter` acquires quota outside that retry loop, and
`LoggingRetry` can sleep past its budget when given a long `Retry-After`.

### 2. Defer on HTTP 429

Persist one provider-wide `retry_at` timestamp in
`~/.tradingstrategy/morpho/provider-cooldown.json`.

- Parse `Retry-After` as seconds or an HTTP date. Missing, invalid or past
  values use ten minutes. Preserve valid seven-day delays. Reject non-finite
  or out-of-range timestamps as invalid and log the condition; never retry
  before a valid provider deadline simply to fit a local request budget.
- Read cooldown before queueing and immediately before HTTP. Deferred calls
  return `transient_error` without contacting Morpho. Already in-flight calls
  may still finish; queued calls must observe a newly recorded cooldown.
- Update with `max(existing_retry_at, new_retry_at)` under a short file lock
  and atomically replace the JSON file. A late successful response must not
  clear a newer cooldown. Expired timestamps allow normal rate-limited reads.
- Keep cooldown state outside `ratelimit_*` tables so startup request-history
  clearing cannot remove a provider ban.
- For invalid/unreadable cooldown state, log an actionable warning once per
  process and defer network reads while retrying state reads on later calls.
  Document the private file path and operator repair procedure. Storage
  recovery requires an operator if permissions or contents remain broken;
  there is no automatic quarantine or claim of self-healing.
- If a 429 timestamp cannot be saved, also retain it in process-shared memory
  to stop that process's workers. Report the persistence failure, since other
  processes cannot observe the unsaved deadline.

Log one warning when cooldown starts or extends. Do not emit a warning for
every vault intentionally skipped during the same cooldown. Cache hits bypass
both cooldown checks and quota acquisition.

### 3. Preserve existing warning data

This is required, rather than an optional cleanup: `VaultDatabase._merge_rows()`
does not currently retain Morpho fields on an otherwise good onchain rescan.
A week of deferred enrichment could erase warning flags and make a vault look
clean.

Carry the actual `MorphoVaultAPIResult.status` into scan rows with an optional
`_morpho_api_status` field. On a transient result, retain existing
`_morpho_offchain_data`, API-sourced curator information, explicitly listed
Morpho flags and their warning notes. Continue updating unrelated onchain data
and maintained descriptions. A fresh successful result can clear obsolete
warnings; a definitive miss keeps current not-found semantics. Do not infer
failure solely from missing warning data or introduce negative caching.

No migration is needed for this optional row key: readers must tolerate its
absence in old pickle rows. Confirm export handling before adding it. Do not
promote stale data to a successful fresh cache entry or reset its timestamp.

### 4. Wire it into the existing fetch path

Replace the HTTP call in `_query_morpho_api()` so V1 and V2 share this transport.
Keep public fetch signatures and existing result classification compatible.
Timeouts, 5xx and malformed responses remain transient and retry on later
scans; no inline retry loop is added. Fresh cache reads remain first.

Keep existing per-vault cache locking. Lock order: per-vault lock, brief quota
acquisition, release quota lock, brief cooldown-file lock. Never hold quota and
cooldown locks together, or hold either during HTTP or sleeps. Bound lock
waits, map optional-enrichment failures to deferred results, and recheck the
cache after acquiring its per-vault lock.

Update Morpho engineering documentation, the changelog and the API reference
for the new module. Use existing metadata progress output; add no new metric
framework. The two-request limit is aggregate, not per worker or per chain.
Independent hosts sharing one public IP are outside this local coordination.

## Focused validation

1. Concurrent sessions/threads share the budget. A short separate-process
   test uses the same database and lock to prove cross-process coordination.
2. V1/V2 use the same transport. Fresh cache hits bypass active cooldown and
   make no HTTP request; existing same-vault deduplication still works.
3. A 429 prevents further queued requests. New sessions and restarted processes
   observe the saved deadline; concurrent updates cannot shorten it.
4. Cover seconds/date headers, invalid headers, seven-day delays, expiry,
   unreadable/corrupt state and failed writes. Startup rate-table clearing must
   preserve cooldown. Test quota/lock timeouts without real long sleeps.
5. Verify a single HTTP attempt for timeout/5xx, with no hidden retries or
   redirects. Deferred reads never become `not_found` or a successful empty
   cache entry, and do not produce a per-vault warning storm.
6. Test full row merging: an existing RED warning, notes and curator survive
   a seven-day cooldown while onchain fields update. A later successful clean
   result can clear obsolete Morpho warnings. Include legacy rows without the
   new optional key and confirm public exports handle it correctly.
7. Run a few paced real-provider reads against an indexed vault, checking a
   valid response through the new session. Never deliberately trigger 429.
   Stop and honour any returned cooldown; record the dated command/result in
   the PR comment. This public endpoint needs no invented credentials.

Use `source .local-test.env && timeout 180 poetry run pytest ...` for focused
checks, Poetry/Ruff on changed Python paths, and `git diff --check`. Complete
one cleanup and independent code review after implementation. No Sphinx build.

## Boundaries and review status

Implementation was authorised after the simplification review. PR publication
and production deployment remain separate steps. A later deployment uses the normal stamped
scanner image and preserves the shared state mount. Never roll back to
unthrottled reads or clear a valid provider cooldown automatically.

The initial plan received a Claude CLI review using `claude-opus-5-5`, followed
by approval after revisions. That approval applied to the earlier design.
This version removes the custom quota scheduler, inline retries/deadline
machinery, corruption quarantine and new counters. It retains the two central
review safeguards: persistent cooldown and preservation of existing warnings.

Morpho documents GraphQL shutdown on 29 January 2027 in its
[migration guide](https://docs.morpho.org/developers/api/migrate-from-graphql/).
REST migration remains a separate change; avoid speculative transport
abstractions in this fix.

## Coding convention checks

Checked against `CLAUDE.md` before implementation: use threaded workers,
module-level imports, type hints, Sphinx docstrings and `#:` dataclass member
comments. Use naive UTC for persisted datetimes, concise deferred-operation
warnings without tracebacks, and the installed Poetry environment for scripts,
formatting and focused tests. Add a Sphinx API index entry for the session
module, but do not build documentation. Engineering text uses British English.
No dependency changes, public data descriptions, numbered migration or price
backfill are required. Complete the repository cleanup and independent review
once the change and its real-provider check are ready.

## Implementation notes

The implementation keeps only `fetch_response()` as the public HTTP read path;
Requests connections are private to prevent callers accidentally bypassing the
quota through an inherited `post()` method. It announces persisted cooldowns
and quota exhaustion once per process, without new counters or per-vault logs.
Maintained notes retain priority; warning notes are rebuilt from retained API
data. An unavailable first-ever lookup still follows existing transient-error
classification, with no invented not-found or risk flag.

Local validation on 12 November 2026 used the following command, including a
real public-provider query with isolated state/cache and no authentication:

```shell
source .local-test.env && timeout 180 poetry run pytest tests/morpho/test_morpho_session.py tests/morpho/test_morpho_session_live.py tests/morpho/test_morpho_offchain_metadata.py tests/erc_4626/test_scan_features.py -q --tb=short
```

Final result on 12 November: **60 passed**, including the real public-provider
query, threaded and separate-process quota checks, cooldown restart/expiry,
SQLite lock cleanup, warning preservation and public export compatibility.
Changed Python files are formatted and add no Ruff findings over the existing
baseline; new Python modules pass Ruff. Documentation builds were not run.

The independent Claude CLI review used `claude-opus-5-5`. Its requested fixes
for missing deferral logs and stale maintained notes were applied and tested.
The focused code follow-up found no blocking correctness issues and approved
the change. The reviewer inspected code; the implementing agent ran the tests.
Public-description style review is not applicable: only engineering text and
existing generated warning messages are involved.

No production deployment, commit, push or PR publication is part of this step.
