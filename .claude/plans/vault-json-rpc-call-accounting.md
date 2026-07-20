# Vault JSON-RPC call accounting plan

## Goal

Track the physical JSON-RPC requests spent by each EVM vault scan, persist the
counts in DuckDB, and display current-cycle and daily usage. Keep
`lead_discovery` and `price_scan` separate.

The production entry point is `scripts/erc-4626/scan-vaults-all-chains.py`,
implemented by `eth_defi/vault/scan_all_chains.py`. Apply the same accounting to
the standalone `scripts/erc-4626/scan-vaults.py` and
`scripts/erc-4626/scan-prices.py` commands. Use
`scripts/erc-4626/README-vault-scripts.md` for scanner configuration and
operator documentation.

## Counting rules

- Count outbound JSON-RPC attempts made during EVM `lead_discovery` and
  `price_scan` only.
- Count by the actual method, such as `eth_call`, `eth_chainId`, and
  `eth_getBlockByNumber`.
- Attribute each attempt to the domain of the concrete provider that handled
  it. Store only the hostname, never its scheme, credentials, path, query
  string, or API key.
- Count failed attempts and every `FallbackProvider` retry or provider switch.
  HTTP retries hidden below `HTTPProvider.make_request()` are not observable
  and are outside the exactness guarantee.
- Count a Multicall3 batch as one `eth_call`, not as its encoded inner calls.
- Exclude Hypersync, archive-node preflight, native-chain APIs, settlement
  scanning, post-processing, Core3, currency rates, and export traffic.
- Use naive UTC. `cycle_started` is the date on which the tick or standalone
  invocation began, even if it crosses midnight.
- Allocate one persistent `cycle_number` per all-chain tick or standalone
  invocation. Scanner retries reuse it.
- For lead discovery, `items_scanned` is the number of unique candidate
  addresses submitted to feature probing, including rejected candidates.
- For price scanning, `items_scanned` is the number of filtered, supported
  vault readers submitted to the historical scan.

## Minimal reusable API

Put generic collection and DuckDB support in `eth_defi/provider/rpcdb.py`. It
must not import vault, ERC-4626, protocol, or scanner modules.

Expose two classes:

- `RPCRequestStats`, a `dataclass(slots=True)` containing call counters keyed by
  `(rpc_provider_domain, api_call)` and error counters keyed by
  `(rpc_provider_domain, error_code, error_message)`.
- `RPCUsageDatabase`, which owns one DuckDB connection and provides
  `allocate_cycle()`, `record_scan()`, the report queries, and `close()`.

`RPCRequestStats` provides `record_call()`, `record_error()`, and `merge()`.
Updates are protected by an internal lock for shared use by worker threads.
Pickling serialises only the counters and recreates the lock, allowing workers
to return stats through joblib.

Do not add `RPCUsageSchema`, record/context wrapper classes, configurable table
names, dynamic SQL identifiers, phase enums, or vault report labels to the
provider module. `RPCUsageDatabase(path)` always uses the two tables defined
below. Its free-form `phase` and generic `items_scanned` values make it usable
by another indexer or scanner despite the historical vault table names required
here.

The main persistence call is deliberately direct:

```python
database.record_scan(
    chain=chain_id,
    phase="price_scan",
    cycle_started=cycle_started,
    cycle_number=cycle_number,
    stats=stats,
    items_scanned=len(vaults),
)
```

Follow the same default-path pattern as the repository's other DuckDB modules.
Define the constant and resolver beside `RPCUsageDatabase` in
`eth_defi.provider.rpcdb`:

```python
DEFAULT_RPC_TRACKING_DATABASE = Path.home() / ".tradingstrategy" / "rpc-tracking.duckdb"


def resolve_rpc_tracking_database_path() -> Path:
    path = os.environ.get("RPC_TRACKING_DATABASE_PATH")
    return Path(path).expanduser() if path else DEFAULT_RPC_TRACKING_DATABASE
```

The all-chain scanner and both standalone scripts call this resolver unless a
path is passed explicitly for a test. `RPCUsageDatabase` creates the resolved
path's parent directory before opening DuckDB.

## Fixed DuckDB schema

Create the tables without primary keys or indexes, matching the repository's
existing DuckDB stability practice.

```sql
CREATE TABLE IF NOT EXISTS vault_rpc_api_calls (
    chain INTEGER NOT NULL,
    phase VARCHAR NOT NULL,
    api_call VARCHAR NOT NULL,
    cycle_started DATE NOT NULL,
    cycle_number INTEGER NOT NULL,
    rpc_provider_domain VARCHAR NOT NULL,
    call_count UBIGINT NOT NULL,
    items_scanned INTEGER NOT NULL
)
```

```sql
CREATE TABLE IF NOT EXISTS vault_rpc_api_errors (
    chain INTEGER NOT NULL,
    phase VARCHAR NOT NULL,
    cycle_started DATE NOT NULL,
    cycle_number INTEGER NOT NULL,
    rpc_provider_domain VARCHAR NOT NULL,
    error_code VARCHAR NOT NULL,
    error_message VARCHAR NOT NULL,
    error_count UBIGINT NOT NULL
)
```

Allocate a cycle with `coalesce(max(cycle_number), 0) + 1` once per tick or
standalone invocation. The existing pipeline lock serialises this read and all
scanner writes; `RPCUsageDatabase` documents that other consumers must provide
the same external serialisation when sharing a database file.

### Append-only writes

Insert one batch when each scan attempt finishes. Retry attempts use the same
cycle number and append more rows; do not read, delete, replace, or upsert old
rows. Queries calculate cycle totals with `sum(call_count)` and the cycle item
count with `max(items_scanned)`.

For each attempt, insert:

- One call row for each observed `(rpc_provider_domain, api_call)` pair.
- If the attempt made no JSON-RPC requests, one marker row with
  `api_call = 'none'`, `rpc_provider_domain = 'none'`, and `call_count = 0` so
  the completed iteration and its `items_scanned` value remain visible.
- One error row per observed `(rpc_provider_domain, error_code,
  error_message)` tuple. Do not create a no-error marker row.

Perform each batch with `BEGIN`, `executemany()`, and `COMMIT`; roll back and
propagate a write failure. All writes happen in the scanner parent process.

Daily and cycle queries sum the method rows directly; the zero-call marker adds
zero and needs no special case. For a cycle retried several times, sum its call
rows and use the maximum item count rather than adding the same population
repeatedly. This maximum is an intentional approximation if retry filtering
produces a slightly different item population.

### Error normalisation

Normalise errors once at the provider boundary:

- JSON-RPC response: decimal JSON-RPC code, for example `-32005`.
- HTTP failure: `http_<status>`, for example `http_429`.
- Transport failure: concrete exception class, for example `ReadTimeout`.
- Otherwise: `unknown`.

Store the provider error message as received so the database retains its full
diagnostic context. Raw messages may contain endpoint credentials and
request-specific values, producing sensitive data and high-cardinality rows.
Treat the database and reports as sensitive operational data and monitor their
size during sustained provider failures.

## Provider instrumentation

Extend `FallbackProvider` without changing the existing successful-call
counter behaviour:

1. Accept an optional `RPCRequestStats` accumulator.
2. Provide `set_rpc_request_stats(stats_or_none)` for a cached subprocess Web3
   to attach one task's accumulator and detach it afterwards. Replacing the
   accumulator does not reset the existing successful-call counters.
3. Resolve each configured provider's safe domain once during initialisation.
4. Immediately before every concrete `provider.make_request(method, params)`,
   call `record_call(domain, method)`.
5. In the error path, call `record_error()` once for the same provider before
   retry classification or switching. An attempt that later succeeds still
   contributes one call and one error.
6. Count the direct `eth_chainId` requests used to verify or switch providers
   when they occur inside the scan phase.

Pass the optional accumulator through `create_multi_provider_web3()` and
`MultiProviderWeb3Factory`. Do not use only
`install_api_call_counter_middleware()`, because middleware cannot see physical
fallback attempts.

The dependency direction remains one way: providers and factories may import
`RPCRequestStats`, while `rpcdb.py` does not import provider implementations.

## Thread and subprocess propagation

Use one accumulator per phase and avoid a registry of created Web3 instances.

### Parent and threaded work

Create the phase accumulator after archive-node preflight, then construct the
phase's parent Web3 and thread-created Web3 instances with that accumulator.
Do not attach it to the preflight Web3 or reuse these phase-owned Web3 instances
for excluded work. Because all phase providers record directly into the shared,
locked accumulator, the parent/thread path needs no factory registry, stage
snapshot, detach step, or cached-Web3 re-registration.

Ensure the accumulator is used only by one phase. Lead discovery and price
scanning create separate accumulators, so their traffic cannot overlap.

### Subprocess work

A process cannot update the parent's in-memory accumulator. Existing workers
cache Web3 per process, so for each joblib `loky` task:

1. Create a task-local `RPCRequestStats`.
2. Create the worker Web3 with that accumulator on the cache miss, or use
   `set_rpc_request_stats()` to attach it to the cached provider.
3. Run the task and detach the accumulator in `finally`, preventing the next
   task in that process from recording into an already-returned object.
4. Return the task stats with a successful normal result and merge them once
   in the parent.

Update the two existing subprocess result paths:

- `eth_defi/event_reader/multicall_batcher.py` returns task stats with each
  completed Multicall task. Attach them to the batch result, never every inner
  encoded call.
- `eth_defi/event_reader/multicall_timestamp.py` returns task stats with each
  timestamp result. The Hypersync path contributes nothing.

If a subprocess task raises, propagate its normal exception without wrapping it
solely to recover counters. Calls made by failed or abruptly terminated tasks
may therefore be missing; subprocess accounting is explicitly a lower bound on
failed scans. Successful tasks, including tasks where `FallbackProvider`
recovers from an intermediate provider error, return complete counters.

Enable this return-and-merge path only for a process backend. When a reader uses
the threading backend, its Web3 instances already write to the shared phase
accumulator; returning and merging the same task counters would double-count.

## Scanner integration

Update `eth_defi/vault/scan_all_chains.py`:

1. Resolve the database with `resolve_rpc_tracking_database_path()` and include
   the file in `bkp_files`. Let the tick's existing pre-scan backup finish
   before opening the connection.
2. Open `RPCUsageDatabase` and allocate one cycle at the start of
   `run_scan_tick()` while the existing pipeline lock is held.
3. Pass `cycle_started`, `cycle_number`, and the database through the EVM scan
   calls. Retry passes reuse them.
4. After preflight, create a `lead_discovery` accumulator in `scan_chain()` and
   pass it through `scan_vaults_for_chain()`. Return only the chain id and item
   count in the existing metrics dictionary; the caller already owns the
   accumulator.
5. Do the same with a separate `price_scan` accumulator passed through
   `scan_prices_for_chain()`. Its item count is `len(vaults)` after filtering.
6. In `scan_chain()`, call `record_scan()` after each attempt and before
   returning its status. Log an accounting write failure prominently but do not
   fail and retry an otherwise successful vault scan, which would spend more
   RPC calls merely because observability storage failed.
7. Close the database before backup or post-processing reads it.
8. Do not create usage rows for native protocols or excluded phases.

The current all-chain loop is sequential and the scanner parent performs every
DuckDB call, so `RPCUsageDatabase` does not need an internal concurrency lock.
Other consumers must serialise calls if they share a connection across threads.

Do not add RPC stats to `LeadScanReport` or `ParquetScanResult`. The optional
accumulator is an input to the relevant functions, while the scanner's existing
metrics dictionary carries the small amount of phase accounting needed by its
caller. Add only an `items_scanned` integer to `LeadScanReport` if the prepared
feature-probe candidate count is not otherwise available at the wrapper.

Update the standalone lead and price scripts to open the same database,
allocate one cycle under `wait_other_writers()`, record their respective phase,
display the reports, and close the database.

## Reports

After each EVM chain finishes, display with `tabulate()` and log:

1. Current-cycle calls grouped by phase, provider domain, and API method, with
   a phase total and `items_scanned`.
2. Daily-to-date totals for that chain grouped separately by phase and provider
   domain.
3. A compact current-cycle error table when errors exist, grouped by phase,
   provider domain, error code, and normalised message.

Daily totals sum method rows directly. First group append-only attempts to one
cycle:

```text
cycle_calls = sum(call_count across method rows)
cycle_items = max(items_scanned across method or zero-marker rows)
daily_calls = sum(cycle_calls)
daily_items = sum(cycle_items)
```

Do not add calls-per-item ratios, configurable report labels, a second daily
error report, or an all-chain end-of-tick report in the first implementation.
They are not required to answer how many calls each phase and chain spent.

## Focused tests

Add offline pytest coverage for:

- Every physical fallback attempt increments the correct provider-domain and
  method counter; errors are recorded once and existing success counters are
  unchanged.
- JSON-RPC, HTTP, transport, and unknown error codes are normalised, while
  messages retain the provider's diagnostic text.
- A shared accumulator records threaded calls exactly once.
- Several `loky` tasks return and merge the exact sum of their calls and
  errors; one Multicall batch remains one `eth_call`.
- Timestamp RPC fallback is counted and the Hypersync path is not.
- DuckDB schema creation, restart-safe cycle allocation, append-only retry
  inserts, rollback, zero-call markers, and explicit close.
- Cycle and daily queries sum method rows and use `max(items_scanned)` across
  retry and fallback-provider rows.
- Lead and price scans use separate phases and counters, while excluded phases
  produce no rows.
- Default and `RPC_TRACKING_DATABASE_PATH` override resolution, including
  `~` expansion and parent-directory creation.
- Standalone scripts use the shared resolver.

Use focused test files and the repository-required invocation form:

```shell
source .local-test.env && poetry run pytest \
  tests/rpc/test_multi_provider.py \
  tests/provider/test_rpcdb.py \
  tests/vault/test_scan_all_chains_rpc_usage.py
```

Use the exact final paths selected during implementation and a three-minute
timeout. Do not run the full suite.

## Documentation

Update `scripts/erc-4626/README-vault-scripts.md` with:

- `RPC_TRACKING_DATABASE_PATH` and the default
  `~/.tradingstrategy/rpc-tracking.duckdb` location.
- The two phases and their `items_scanned` definitions.
- Provider-domain attribution, physical-attempt and Multicall semantics.
- Error normalisation and the transport-retry limitation.
- Excluded traffic and examples of both reports.
- A simple DuckDB daily-total query.

Add the required API stub for `eth_defi.provider.rpcdb` under
`docs/source/api/provider/` and link it from the provider API index. Show one
non-vault example that uses `RPCRequestStats` and `RPCUsageDatabase` with a
different phase and item meaning. Do not export this operational database to
public R2 storage.

## Acceptance criteria

- Every completed EVM lead-discovery and price-scan attempt records method rows
  or a zero-call marker.
- Calls and errors include failed fallback attempts and the concrete provider
  domain, without storing credentials or URL paths.
- Lead discovery and price scanning are independently queryable and displayed
  per chain for the current cycle and UTC day.
- Retry attempts append to the same cycle without multiplying
  `items_scanned` in aggregate queries.
- Successful thread and subprocess calls are merged exactly once; failed or
  terminated subprocess tasks are documented as a possible lower bound.
- Collection and persistence live in `eth_defi.provider.rpcdb` and work without
  importing vault modules or configuring a schema object.
- All scanners default to `~/.tradingstrategy/rpc-tracking.duckdb` through the
  shared provider-level resolver.
- Native protocols, Hypersync, preflight, settlement, and post-processing do
  not contribute rows.
- Focused provider, worker, DuckDB, and scanner tests pass.
