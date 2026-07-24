# Apex offchain vault reader

## Goal

Build a standalone ``eth_defi.apex`` reader that backfills every vault exposed
by ApeX Omni's public vault ranking API, then records current NAV and TVL
snapshots at a configurable timestamp cadence whose initial default is four
hours. The implementation is limited to the public API client, its DuckDB
database, synthetic chain registry entries, its standalone command and API
documentation. It must not merge data into the unified vault parquet or pickle,
register the global scanner, or add protocol metadata, curator configuration or
logos.

Use one reader, one database and one scan command. ApeX does not need separate
low-frequency and high-frequency pipelines: the inexpensive bulk ranking call
already exposes sufficiently fresh current metrics. The command scheduler owns
the arbitrary positive ranking observation interval (default four hours), and
every ``run_scan()`` invocation records a ranking observation for each selected
non-terminal vault. The scan has one independent internal due-time gate for
historical maintenance (default 24 hours).

## Data-source behaviour

The reader uses two public endpoints under ``https://omni.apex.exchange/api/v3``:

- ``GET /vault/ranking?page={page}&limit={limit}`` returns the complete,
  paginated current vault list, including current ``vaultNetValue``, ``tvl``,
  share count, status and reported Ethereum address.
- ``GET /vault/fund-net-values?vaultId={vault_id}`` returns historical entries
  containing ``timestamp``, ``netValue`` and ``totalValue``.

Validate both HTTP status and the application envelope. ApeX can return an
error object such as ``{"code": 2, "msg": "..."}`` with HTTP 200; missing,
null or malformed ``data``, ``vaultList`` or ``timeValue`` is a failed request,
not an empty successful result.

Live inspection on 2026-07-23 found that the ranking feed's ``updatedTime``
advances on an approximately 30-second platform tick. The historical endpoint
is age-adaptive: sampled non-terminal vaults returned approximately hourly
points for a young vault, daily points for medium-aged vaults and weekly points
for older vaults, normally followed by a current trailing point. These are
observations, not a contractual service-level guarantee, so preserve and test
the reader's behaviour without hard-coding these intervals.

The public history call currently exposes no pagination, range or completeness
metadata. Treat capability verification as an implementation gate before
writing the history fetcher: recheck the official client, site requests and
live response envelope for cursor, limit or range parameters and record the
result in the README. If pagination exists, define cursor/page termination,
require stable response metadata, retry the whole response on any page failure,
and consume every page before staging. If it remains a single bounded response,
preserve a fixture of the verified envelope and document that an initial
backfill can only recover the history ApeX still returns. Never claim
completeness beyond the observed minimum and maximum timestamps stored in
``history_sync``. Collapse duplicate timestamp entries once when their
canonical parsed ``timestamp``, ``netValue`` and ``totalValue`` fields are
equal, but reject and retry the complete response when the same timestamp
carries conflicting canonical values.

This source behaviour means the bulk ranking endpoint is sufficient for future
timestamp observations at the configured polling interval. Do not tie complete
history fetches to every ranking poll. Fetch history immediately for a newly
discovered vault, then refresh eligible non-terminal vaults according to the
separate history interval to capture retained fine-grained points and repair
gaps. Terminal vaults receive a final successful history sync and are not
refetched during normal incremental scans; the final sync must be non-empty.

The observed statuses are ``VAULT_IN_PROCESS`` and ``VAULT_FINISHED``. Treat
only the explicitly verified ``VAULT_FINISHED`` value as terminal. Treat every
other or newly introduced status as non-terminal, so an unknown status still
receives ranking observations and history maintenance rather than silently
falling out of the pipeline. If a terminal vault later becomes non-terminal,
clear its terminal/final-sync timestamps; a later terminal transition starts a
new finalisation generation.

Preserve every historical source timestamp as naive UTC. Do not interpolate,
forward-fill, round or floor timestamps to cadence buckets. For ranking
observations, use one common naive UTC ``observed_at`` captured after the
complete stabilised ranking read has been materialised. Store ApeX's ``updatedTime``
separately as ``source_updated_at`` when it is valid. The time-series key uses
the exact ``observed_at`` for ranking rows, so an unchanged or
backwards-moving source timestamp cannot erase evidence that a poll occurred.
Changing the scan interval later must require no schema migration or rewriting
of earlier rows.

## Identity and scope

Add ``APEX_CHAIN_ID = 9995`` and register it in
``eth_defi.chain.CHAIN_NAMES`` and ``CHAIN_HOMEPAGES`` as ApeX. Repository
inspection on 2026-07-23 confirms that ``9997`` is Hibachi, ``9998`` is Lighter,
``9999`` is Hypercore, ``9996`` is the retired Lighter Robinhood partition that
can remain in historical data, and ``9995`` is the first unused lower ID.

The reported ``vaultEthAddress`` is not a vault identifier. In a current sample
of the first 100 returned vaults it has only 48 unique values, and one address
is shared by 19 different vault IDs. Therefore the primary database key is the
string ``vault_id`` and the stable reader address is
``apex-vault-{vault_id}``. Retain the reported Ethereum address only as
metadata. Never deduplicate, aggregate or overwrite price history by the
reported Ethereum address.

The standalone scope deliberately does not make ``apex-vault-*`` a
``VaultSpec``-validated address and does not alter native-price exports. A later
unified-dataset integration can add that compatibility surface without changing
the persisted Apex identity.

## Package design

Create ``eth_defi/apex/`` with these public modules:

- ``__init__.py``: package marker and deliberately small public export surface.
- ``constants.py``: synthetic chain ID, public API base URL, default database
  path ``~/.tradingstrategy/vaults/apex-vaults.duckdb``, default scan and
  history intervals, conservative process-wide request-rate default, and finite
  default connect/read timeouts of 10 and 30 seconds. Also define a 60-second
  total request deadline, five-minute whole-ranking deadline, two-minute
  per-vault history deadline, 10-second maximum retry delay and 16 MiB maximum
  JSON response size. The interval constants are defaults only and must not
  appear in storage or parser logic.
- ``config.py``: slots configuration dataclass, strict environment parsing and
  ``parse_apex_duration()``. Accept positive decimal values with ``s``, ``m``,
  ``h`` or ``d`` suffixes, such as ``30s``, ``30m``, ``1.5h`` and ``2d``;
  reject zero, negative, unitless or unsupported values. Require every numeric
  rate, timeout, deadline and size limit to be finite and positive.
- ``session.py``: ``create_apex_session_pool(requests_per_second,
  pool_maxsize, timeout_policy)`` as the sole owner of retry, connection-pool,
  timeout and process-wide rate-limit configuration. Its JSON wrapper streams
  bounded chunks, checks a monotonic operation budget, rejects responses over the
  configured byte limit and always passes the finite connect/read timeout to
  ``requests``. Disable adapter-managed retries and perform explicit
  budget-aware retry handling in the wrapper. Limiter acquisition consumes
  the same operation budget, and every connect/read timeout, backoff and
  ``Retry-After`` delay is clamped to both the configured maximum and the
  remaining budget. Close the response on budget exhaustion, size, parse or
  envelope failure. Give each worker its own configured ``requests.Session``
  while every session shares the same limiter; never mutate a session after
  construction or share it between worker threads. Close all history-worker
  sessions after each completed joblib fetch phase, retaining only the calling
  thread's ranking session between loop cycles. Enforce one active scan per
  session pool and wait for every sibling worker scope to exit before cleanup
  when joblib surfaces an exceptional result early.
- ``vault.py``: slots dataclasses and typed fetch/parse functions for ranking
  pages and net-value history. Retain all returned vaults with no TVL or status
  filter.
- ``metrics.py``: DuckDB classes, historical-sync policy and the single Apex
  scan orchestrator. Do not create separate daily or high-frequency modules or
  databases.

Use ``@dataclass(slots=True)`` for parsed records, type every public argument
and return value, and prefix all network-reading functions with ``fetch_``.
Document public API functions and dataframe schemas according to repository
conventions.

Use ``joblib.Parallel`` with the ``threading`` backend, a ``max_workers``
argument, and ``tqdm_loggable.auto.tqdm`` progress reporting for per-vault
historic requests. Workers perform HTTP reads and parsing only and return
immutable result objects; they never access DuckDB. The owning thread applies
all database writes serially. Use a shared default limit of five requests per
second and eight workers. Retries for transport failures, retryable HTTP status
codes and retryable application-envelope errors must be bounded. A failed
history request records the vault ID and leaves it retryable without erasing
successfully stored histories for other vaults.

Pass one monotonic whole-operation budget through both stabilised ranking
passes and one per-vault budget through every history-response attempt. Limiter
queueing, connect/read timeout arguments, retries and delays consume the same
budget. The synchronous ``requests`` read timeout is inactivity-based, so a
server that continuously drips bytes without yielding a streamed chunk may
delay budget detection until that read yields; do not describe these budgets as
hard wall-clock guarantees.

### Ranking pagination

Treat ranking pagination as a stabilised paginated read using ApeX's observed
zero-based page numbers; the API does not offer a truly atomic snapshot. On
2026-07-23, pages ``0`` through ``6`` returned all 641 reported rows at limit
100, while page ``7`` returned an HTTP-200 application error. Each bounded
attempt performs two consecutive complete passes:

1. For each pass, fetch page zero, record ``totalSize`` and fetch every required
   page into memory without touching DuckDB.
2. Validate every envelope and require every page in the pass to report the
   same total.
3. Require the pass's raw row count to equal its total and every ``vault_id`` to
   be unique. A duplicate means page ordering changed and another vault may be
   missing; log the IDs and reject the attempt.
4. Require both passes to report the same total and identical vault-ID sets.
5. Use the second pass's records for metadata and observations after the ID set
   stabilises; metric fields may legitimately change between passes.

If totals or membership move, a page fails, the row count differs, or a
duplicate appears, discard both in-memory passes and retry from page zero. After
bounded whole-read retries, raise before any database mutation. This strict rule
preserves the all-vault guarantee; do not commit a partial list or silently
deduplicate it on the assumption that a later run will heal it. If an existing
non-empty database receives a stabilised zero-row all-vault result, fail before
mutation instead of marking every known vault missing.

### DuckDB schema and write strategy

Create these tables with forward-compatible nullable metadata fields and no
``PRIMARY KEY`` or ``UNIQUE`` constraints. DuckDB 1.5.0's ART indexes can cause
heap corruption for file-backed databases under Python 3.14 on macOS ARM64; use
application-enforced logical keys and transactional ``DELETE`` plus ``INSERT``
instead of ``ON CONFLICT``.

Parse NAV, TVL, share count and other numeric source values as Python
``float`` values and store them in DuckDB ``DOUBLE`` columns. Reject malformed
or non-finite values. Accept normal binary floating-point rounding at the
persistence boundary. Derive historical ``total_supply`` using floating-point
division only when NAV is positive.

1. ``vault_metadata`` logically keyed by ``vault_id``. Store the synthetic
   address, reported Ethereum address, name, description, status, vault type,
   created/updated/finished timestamps, subscription caps, current
   NAV/TVL/share count, first seen, last seen and nullable ``missing_since``.
   The sampled API only exposes zero or blank fee values and no authoritative
   source establishes whether non-zero values are fractions, percentages or
   basis points. Preserve ``purchaseFeeRate`` and ``shareProfitRatio`` as
   nullable raw strings. Do not expose them as ``eth_defi.types.Percent`` until
   their unit is verified; add the typed fields later through a
   forward-compatible migration.
2. ``vault_prices`` logically keyed by ``(vault_id, timestamp)``. Store the
   synthetic address, NAV as ``share_price``, TVL as ``total_assets``, derived
   ``total_supply``, source, nullable ``source_updated_at`` and ``written_at``.
   Use source values ``fund_net_values`` and ``ranking_snapshot``. A historical
   row uses the endpoint's timestamp as ``timestamp``. A ranking row uses the
   scan's common ``observed_at`` as ``timestamp`` and puts the valid server
   ``updatedTime`` in ``source_updated_at``. For a ranking snapshot, use its
   reported share count as ``total_supply`` when parseable. For historical data,
   derive it as ``totalValue / netValue`` only when NAV is positive.
3. ``history_sync`` logically keyed by ``vault_id``. Store the latest attempt's
   time, success/failure status, row count and nullable bounds, plus
   ``last_successful_attempt_at``. Keep separate latest non-empty successful
   response fields (time, row count and bounds). Also store
   canonical retained-history row count and bounds, recomputed from persisted
   ``fund_net_values`` rows after each successful replacement. Finally store
   ``terminal_observed_at``, ``final_history_sync_at``,
   ``missing_observed_at``, ``final_missing_history_sync_at`` and the most recent
   error text/time. Empty responses update attempt fields without destroying the
   non-empty diagnostic baseline.

Use timestamp-precision columns, never ``DATE`` columns or cadence-specific
tables. Do not store a fixed bucket number, assume a constant gap between rows,
or include the configured interval in a uniqueness constraint. Observations
created under different scan intervals coexist in the same table and are
ordered solely by their actual timestamps.

Apply historical and current observations idempotently. Historical
``fund_net_values`` entries are canonical: they replace a ranking snapshot at
the same ``(vault_id, timestamp)``. A ranking snapshot must not overwrite a
historical row at that timestamp. Store API zero NAV/TVL values as reported,
but leave historical ``total_supply`` null when division would be invalid.
Stage every batch and enforce the logical key in one transaction. For incoming
history, delete every existing row at the matching logical keys and insert the
history rows. For incoming ranking data, delete only matching ranking rows and
insert a ranking row only where no history row already exists. Metadata and
sync-state replacement must first carry forward values such as ``first_seen``
and the latest non-empty history baseline before deleting their old logical-key
rows. Validate the staged tables themselves contain no duplicate logical keys.

Historical maintenance is append-and-correct, never replace-and-prune. Stage
and fully parse a response before opening its write transaction. On success,
replace rows at the returned logical keys and update ``history_sync``
atomically, clearing the prior error. An empty response updates the diagnostic
state and canonical retained-history statistics but cannot delete existing rows
and becomes eligible again at the next configured history refresh; it must not
set a terminal vault's final-sync timestamp. Because age-adaptive resolution
legitimately reduces row counts, log a smaller response count at debug level
only. Log a warning when a non-empty response's maximum timestamp moves
backwards relative to the previous non-empty successful response; log an
advancing minimum timestamp at info level as a possible retention-window
change. Preserve all prior timestamps while still allowing corrected values at
returned timestamps to replace earlier values. The API exposes no completeness
token, so neither incremental nor forced refresh may delete omitted history.

All writes happen on the owning thread. Apply ranking metadata, ranking
observations and every affected ``history_sync`` lifecycle transition in one
transaction. This transaction creates missing sync rows, sets
``terminal_observed_at`` on the current generation's first terminal
observation, clears both terminal timestamps whenever the current status is
non-terminal, and sets or clears missing-generation timestamps under the
disappearance policy. Metadata status/disappearance and generation state
therefore cannot diverge after a failure. Apply each successful vault history
and its remaining sync-state transition in one per-vault transaction. On an API
failure, record the error in a separate small transaction. DuckDB errors are
not retryable API failures: roll back, propagate them and never continue with
ambiguous state. Capture the creating thread ID in
``ApexMetricsDatabase`` and assert it for every write, checkpoint and close
operation.

On file-backed databases execute ``SET wal_autocheckpoint = '1TB'`` to disable
automatic WAL checkpoints, then run one explicit ``CHECKPOINT`` after the scan's
writes finish. Closing still performs DuckDB's normal final flush.

### Scan and backfill behaviour

Expose ``run_scan()`` accepting a configured session pool, an already-open
``ApexMetricsDatabase``, an optional vault-ID filter, worker count, history mode
and history refresh interval. Session creation, not the scan function, owns the
request rate. The caller owns the database lifetime; ``run_scan()`` returns a
typed result summary rather than an open connection. ``run_scan()`` never
suppresses a ranking read based on elapsed time: each call records a current
ranking observation for every selected non-terminal vault, while only history
work is due-time gated.

Always fetch and validate the complete stabilised ranking read before applying
the optional vault-ID filter. With no filter, process every discovered vault.
With a filter, restrict metadata, ranking observations and history work to
those IDs; if any requested ID is absent from the validated snapshot, raise
before database mutation so a typo cannot look like a successful targeted scan.

Only an unfiltered scan may mark a known vault absent or schedule its
missing-generation finalisation. In every scan, however, each selected vault
that is present in the validated ranking atomically clears ``missing_since``,
``missing_observed_at`` and ``final_missing_history_sync_at``; a targeted scan
can therefore re-observe and reactivate a selected missing vault without
touching any unselected vault. For an absent vault, the ranking transaction sets
``missing_since`` and ``missing_observed_at``. A missing vault whose last known
status is non-terminal receives history attempts until one non-empty response
sets ``final_missing_history_sync_at``; empty or failed responses remain
retryable. A missing terminal vault still completes any outstanding terminal
final sync. After the applicable final sync, preserve its rows without further
normal history reads. Targeted scans never mutate disappearance state for
unselected vaults.

- ``history_mode="incremental"`` is the default. On a new database it
  backfills every selected vault. Afterwards it fetches a selected non-terminal
  vault when it has never succeeded or ``last_successful_attempt_at`` is at
  least the configured history refresh interval old. A selected terminal vault
  is fetched until one non-empty successful sync has occurred after the ranking
  first reports the terminal state, then remains immutable during normal scans.
  Every successful response follows the append-and-correct policy.
- ``history_mode="refresh"`` deliberately re-fetches every selected vault's
  history immediately for repairs or source revisions, but retains the same
  append-and-correct policy and never prunes omitted timestamps. Refreshing a
  terminal vault preserves an existing ``final_history_sync_at``. If no final
  sync exists yet, a non-empty successful forced refresh after
  ``terminal_observed_at`` sets it; an empty or failed refresh does not.
- ``history_mode="none"`` writes selected metadata and current ranking
  observations without fetching history.

Each scan performs one complete stabilised ranking attempt before any database
mutation. After validation, capture one ``observed_at``, atomically replace
metadata for all selected vaults and write one current observation for every
selected non-terminal vault. Write a terminal-vault ranking observation only
when it is newly discovered, has just transitioned to terminal, or its reported
NAV, TVL, share count or ``source_updated_at`` differs from its most recent
ranking observation. This retains every vault and its final state without
accumulating unchanged frozen-value rows indefinitely. Then select histories due
under the independent history gate, fetch and parse them in parallel, and apply
results serially. Checkpoint DuckDB only after all writes complete.

The standalone command creates the database before calling ``run_scan()`` and
closes it in ``finally`` on both success and failure. API failures may be
isolated per history request, but a ranking failure aborts the scan and a
database failure propagates. Preserve prior rows for vaults that disappear from
a later ranking response.

## Standalone command and documentation

Add ``scripts/apex/vault-metrics.py``. It defaults to one scan. ``LOOP=1``
repeats the same combined reader after ``SCAN_INTERVAL=4h`` minus the
completed-cycle duration. The history eligibility check runs inside each scan;
do not create a second low-frequency command or scheduler entry. Support these
environment variables:

- ``DB_PATH``;
- ``VAULT_IDS`` as a comma-separated targeted override;
- ``MAX_WORKERS`` (default ``8``);
- ``REQUESTS_PER_SECOND`` (process-wide default ``5``);
- ``CONNECT_TIMEOUT``, ``READ_TIMEOUT``, ``REQUEST_DEADLINE``,
  ``RANKING_DEADLINE``, ``HISTORY_DEADLINE`` and ``MAX_RETRY_DELAY`` as finite
  positive seconds;
- ``MAX_RESPONSE_BYTES`` as a positive integer;
- ``HISTORY_MODE`` (default ``incremental``);
- ``HISTORY_REFRESH_INTERVAL`` (default ``24h``); and
- ``LOOP`` and ``SCAN_INTERVAL`` (default ``4h``).

Use the project's console logger setup and avoid a command-line parser. Parse
``SCAN_INTERVAL`` and ``HISTORY_REFRESH_INTERVAL`` with
``parse_apex_duration()`` and sleep for
``max(0, interval.total_seconds() - cycle_duration)``. Construct the session
pool once, open the DuckDB database once per process, and close it in a
``finally`` clause together with every worker-local HTTP session. A later
configuration change from ``4h`` to another positive interval must only require
restarting the command with the new environment value; it must continue writing
to the existing database.

Add ``scripts/apex/README-apex-vaults.md`` with endpoint links, the observed
30-second ranking tick, age-adaptive historical resolution, the distinction
between polling time and source time, configurable independent intervals,
history pagination/retention capability findings, initial-backfill and
forced-refresh commands, and the environment configuration. State that ``4h``
and ``24h`` are operational defaults rather than schema constraints, the command
scheduler controls ranking cadence while history has an internal due-time gate,
an initial backfill cannot exceed the history ApeX still exposes, and API NAV and
TVL values are assumed to use the platform's USDT terms.

Add ``docs/source/api/apex/index.rst``, list ``config``, ``constants``,
``session``, ``vault`` and ``metrics`` in its autosummary, and add
``apex/index`` to the main ``docs/source/api/index.rst`` toctree.

If the implementation is later opened as a feature pull request, use a
``feat:`` title and add the dated feature entry to ``CHANGELOG.md`` as required
by the repository. The plan-only change does not itself require a changelog
entry.

## Tests

Add stable fixture-based tests under ``tests/apex/``. Do not make CI depend on
the live Apex service.

Implementation status for PR #1358: the 60-case fixture suite covers the core
parser, ranking stability, identity, session lifecycle, history gate and
file-backed DuckDB paths. The numbered list below is the broader design
checklist, not a claim that every case ships in this PR. Remaining follow-up
coverage includes slow-drip transport behaviour, concurrent rate-limit timing,
terminal-page and total-churn failures, log-level assertions, additional
ranking transaction failure injection and unequal command-loop intervals.

1. Parse ranking and history envelopes, convert millisecond timestamps to naive
   UTC, retain every status/type value, preserve raw fee strings and 18-decimal
   source values within expected floating-point tolerance, and reject HTTP-200
   application errors, missing/null ``data``, malformed arrays, malformed
   numeric values and non-finite numeric values.
2. Exercise both zero-based ranking passes, stable totals and membership,
   exhausted whole-read retries and duplicate-ID detection/logging. Assert that
   a terminal page failure, total churn, membership churn, row-count mismatch or
   duplicate ID rejects both passes and leaves the database untouched.
3. Parse and order hourly, daily, weekly and irregular history fixtures without
   generating missing points. Assert NAV, TVL and derived-supply behaviour,
   including zero NAV and a current trailing point. Cover verified paginated or
   single-envelope history behaviour, equivalent duplicate timestamps and
   conflicting duplicate-timestamp rejection.
4. Verify that multiple vault IDs sharing one reported Ethereum address retain
   distinct synthetic addresses, metadata rows and price histories. Treat known
   and unknown non-terminal statuses alike, test terminal reactivation and
   preserve ``first_seen`` across metadata replacement.
5. Exercise a file-backed DuckDB through large repeated write, checkpoint,
   close and reopen cycles. Assert the schema has no primary/unique constraints,
   logical-key replacement remains idempotent, automatic WAL checkpoint is
   disabled, and no duplicate logical keys appear. Verify ``DOUBLE`` values with
   ``pytest.approx()``, exact-timestamp history precedence over ranking data and
   preservation of unrelated rows.
6. Verify append-and-correct history safety: empty or shortened refreshes retain
   existing non-empty history and diagnostic baselines, corrected returned
   timestamps update, latest-attempt and latest-non-empty fields remain distinct,
   canonical retained-history bounds/count stay accurate, row-count shrinkage
   logs only at debug, maximum-timestamp regression warns, and absent vault rows
   remain.
7. Inject a database failure between history replacement and sync-state update and
   assert the per-vault transaction rolls back. Verify ranking writes are
   atomic, including failure injection around terminal transition, reactivation
   and missing-state changes. Verify API error-state writes are isolated,
   database errors propagate, and wrong-thread writes/checkpoint/close fail
   before touching DuckDB.
8. Run threaded mock history fetches and assert workers never write DuckDB and
   the owning thread serialises successful results. Cover connect/read timeout,
   slow-drip total-deadline exhaustion, oversized responses, excessive
   ``Retry-After``, bounded backoff, deadline expiry while queued for the rate
   limiter, clamping when the remaining budget is shorter than the configured
   read timeout, response closure on bounded-read failure, a transient
   per-vault API failure retried on a later scan, process-wide rate limiting,
   and closure of every worker-local session. Assert that one ranking deadline
   spans both stabilisation passes and one per-vault history deadline spans all
   retry attempts for that vault.
9. Mock the complete scan to cover new-database backfill, ranking observations
   for every selected non-terminal vault on every invocation, command scheduling
   at several unequal intervals, an interval change against an existing
   database, the independent history gate, retry after an empty response, new
   vault discovery, terminal transition, failed/empty final sync, successful
   final sync, reactivation, disappearance, reappearance, forced append-only
   refresh and no-history mode. Assert no resampling occurs when the interval
   changes and an unchanged terminal vault does not accumulate repeated ranking
   rows while a changed terminal record does. Verify a targeted reappearance
   clears the selected vault's complete missing generation without mutating
   unselected missing vaults.
10. Exercise ``parse_apex_duration()`` and command configuration without
    looping or contacting the live endpoint. Accept values such as ``30m``,
    ``90m`` and ``1.5h``; reject zero, negative and malformed durations, invalid
    history modes, non-positive or non-finite worker/rate/timeout/deadline values,
    invalid size limits, and missing targeted IDs. Verify a targeted scan
    validates the full ranking first and limits all writes and history requests
    to selected IDs.

Use fixtures or ``finally`` clauses to close every DuckDB database.

Run the focused suite with:

```shell
source .local-test.env && poetry run pytest tests/apex
```
