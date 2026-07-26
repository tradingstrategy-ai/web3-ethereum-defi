# Lead discovery cache

## Goal

Avoid repeating the expensive ERC-4626 lead discovery RPC work on every
all-chain scanner cycle. The scanner will persist a cache-validation state per
EVM chain and skip the entire lead-discovery phase while that state is valid.
A configuration change, an absent/corrupt state file, or cache expiry triggers
one complete historical lead discovery on the next scan.

This is deliberately a bounded-staleness cache: a newly deployed or newly
active vault can be absent from the metadata database for up to seven days.
Price scanning continues on every scheduled chain scan, using the latest saved
metadata database; neither price Parquet data nor reader state is reset by a
lead refresh.

## Cache contract

Create one JSON document per detected EVM chain at:

```
${PIPELINE_DATA_DIR}/lead-discovery-state-{chain_id}.json
```

Use an envelope with a schema version, chain ID, lead-discovery signature,
full-scan completion timestamp and the completed block number. The document is
cache-control state only: candidate leads and metadata remain authoritative in
``vault-metadata-db.pickle``. Write it atomically only after the full discovery
and metadata database write succeed. A missing, malformed, wrong-chain or
unknown-schema document is a cache miss; log the concrete reason and perform a
full discovery rather than trusting partial state.

Add ``LEAD_DISCOVERY_STATE_TIMEOUT``. Parse it with the scanner's existing
duration parser and default it to ``7d``. A cache hit requires all of:

- a readable state document with the current schema and chain ID;
- an equal signature;
- a completion timestamp younger than the configured timeout; and
- an existing vault metadata database with a ``last_scanned_block`` cursor for
  that chain. A cursor is valid even for a chain whose completed discovery
  found zero leads.

Treat zero or negative durations as configuration errors. Record cache hit,
miss reason, age, timeout, signature and completion block in scanner logs and
surface the hit/miss in the chain result/dashboard. On a hit, report lead
discovery as successful with zero items and zero RPC calls, then run the normal
price phase. Do not update the completion timestamp on a hit: expiry must be
measured from the last actual discovery, not from the latest loop tick.

Add ``FORCE_LEAD_DISCOVERY`` as a one-shot operational escape hatch. When true,
it bypasses an otherwise valid state document for the current invocation and
writes a replacement only after the full scan succeeds. It does not alter the
signature, delete state files, or affect price scanning. Log its use clearly.

## Signature

Implement a small local function-source hashing helper based on
``tradeexecutor.utils.python_function.hash_function`` from trade-executor
``origin/master``. It must use ``inspect.getsource()``, dedent and parse the
function AST, remove docstrings and the function name, then SHA-256 the
normalised AST. When source is unavailable, fall back to hashing bytecode. Do
not add trade-executor as a dependency.

The lead-discovery configuration signature is a canonical JSON SHA-256 digest
of exactly these two inputs:

1. The normalised source hash of
   ``eth_defi.erc_4626.discovery_base.get_vault_discovery_events`` (the lead
   detection function).
2. The sorted enabled EVM-chain configuration from ``build_chain_configs()``:
   each ``ChainConfig`` with ``scan_vaults=True`` contributes its name and RPC
   environment-variable name.

Keep the input mapping in the JSON document for diagnostics, alongside the
digest. Use sorted keys and lists so process order and dataclass representation
cannot affect the signature. A change to either source function or the enabled
chain set invalidates every per-chain state document, causing the next scan of
each enabled chain to run full discovery. Changes to price settings, RPC URLs,
worker count, scheduler cadence or disabled price scans must not change this
signature.

## Scanner flow

1. In ``eth_defi.vault.scan_all_chains``, derive the common enabled-chain
   signature once after ``build_chain_configs()`` and pass the signature,
   timeout and pipeline data directory through ``scan_chain()`` and
   ``scan_vaults_for_chain()``. Resolve the state-file name only after Web3 has
   verified the actual chain ID; never rely on the display name alone.
2. Before calling ``scan_leads()``, load and validate that chain's state. On a
   cache hit, bypass ``scan_leads()``, preserve existing vault-count reporting
   from the metadata database, record a zero-call ``lead_discovery`` phase and
   continue to price scanning.
3. On a cache miss, call ``scan_leads(force_full_discovery=True)``. This mode
   scans from block 1 through the current safe discovery head, does not seed
   historical leads (which would double the event counters), and re-probes and
   refreshes all leads/metadata found by the current configuration. The normal
   incremental path remains available for direct library callers but the
   scheduled cache miss must use this complete mode.
4. Persist the metadata database before atomically saving the successful state
   JSON. If discovery, probing, metadata extraction or either write fails, do
   not create or refresh the state file; the next scheduled scan retries the
   full discovery. Never delete the old state file before a replacement has
   succeeded. Keep this work inside the scanner's existing pipeline lock and
   sequential EVM-chain loop, so the shared metadata pickle cannot receive
   competing writes from two cache-miss refreshes.
5. Continue to use HyperSync for the historical event portion of a full scan.
   If it is unavailable, fail the cache-miss discovery clearly instead of
   attempting historical JSON-RPC ``eth_getLogs``. Full refreshes must not
   reset reader state, remove parquet rows, or trigger price-history backfills.

Update ``eth_defi.erc_4626.lead_scan_core`` and
``eth_defi.erc_4626.discovery_base`` to make full versus incremental discovery
explicit. Remove the current early return when no metadata rows were produced:
the metadata database must still receive the final discovery cursor and lead
map. Ensure a full run retains the existing broken-row protection while
refreshing current results. Update ``README-vault-leads.md`` and the scanner
script environment-variable documentation to describe the seven-day delay,
cache file, expiry and automatic full refresh behaviour.

## Tests

Add focused unit tests, without live RPC access, for:

- source hashing ignores docstrings/function names but changes for a behavioural
  function change, and bytecode fallback remains deterministic;
- signature changes when the detection-function hash or enabled-chain list
  changes, but not when irrelevant scheduler/price settings change;
- state JSON is atomic and valid only for matching schema, chain, signature,
  fresh timestamp and metadata database presence;
- a seven-day-old state is expired, while a younger matching state skips
  ``scan_leads()`` and still executes the price phase;
- a cache miss passes ``force_full_discovery=True`` and writes state only after
  a successful discovery/database write; failures retain the previous state;
- ``FORCE_LEAD_DISCOVERY=true`` bypasses a fresh matching state without
  changing the signature, and a cache hit accepts a zero-lead chain when its
  saved cursor is present;
- a cache-hit discovery phase is recorded with zero calls/items and existing
  RPC-accounting tests retain separate price-phase accounting; and
- full discovery starts at block 1 without re-seeding old leads, while an
  incremental no-new-row run still persists its end-block cursor.

Run the new focused test modules with ``source .local-test.env && poetry run
pytest`` and the required extended timeout. Format changed Python code with
``poetry run ruff format`` before implementation is handed off.
