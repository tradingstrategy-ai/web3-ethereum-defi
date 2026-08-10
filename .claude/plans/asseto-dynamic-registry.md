# Asseto dynamic registry plan

## Goal

Make the scheduled `Asseto` scan self-contained after every process restart.
Each due Asseto cycle must fetch and validate the current EVM product registry,
merge maintained manual overlays, refresh supported product metadata, and scan
prices from that same registry snapshot. A locked, atomic disk cache must make
the last valid snapshot available after a process restart without allowing a
failed or empty API response to replace it.

Asseto API data is the default source for product names, symbols,
descriptions, denominations and public price-series identifiers. Manual data
must be limited to explicit, source-backed fields that Asseto does not publish
or publishes incorrectly.

## Current failure

The historical Asseto backfill fetches the public registry and inserts
temporary `AssetoProduct` objects into the process-local `ASSETO_PRODUCTS`
mapping. The recurring tokenised-fund runner does not perform that preparation.
After a scanner restart, the mapping contains only the hardcoded HashKey AoABT
entry, while the persistent vault database still contains dynamically
discovered products such as Ethereum
`0x78e80da0616887b46a31f39310c2a8b0fbd6a42d`.

The scheduled runner selects that row by its `asseto_like` feature, but
`AssetoVault` cannot find a matching `AssetoProduct` and raises
`Unsupported Asseto product`. Retrying the cycle cannot repair the missing
process-local registry.

## Source and precedence rules

Use one documented field-level merge policy in fresh mode. Do not keep complete
copied `AssetoProduct` records when only one or two fields need manual
correction.

| Priority | Source | Intended fields |
| --- | --- | --- |
| 1 | Maintained product overlay | Reviewed corrections and data Asseto does not expose: manager, pricer, fee terms, homepage, denomination correction or an exceptional description |
| 2 | Current Asseto detail API | Long description and richer current product metadata when the endpoint returns a valid non-empty value |
| 3 | Current Asseto registry API | Chain, token address, product id/key, name, symbol, type, denomination, introduction, displayed TVL/APY and public price-history identity |
| 4 | Onchain reads | Contract existence, deployment block/time, token metadata, supply and onchain pricer values |
| 5 | Previous detail metadata for the same product | Preserve a non-empty detail description when an optional current detail request fails or becomes empty |
| 6 | Generic text | Only when neither a maintained overlay nor current Asseto data supplies a meaningful description |

This table applies only when the registry result is `fresh`. A `stale` result
may reconstruct runtime adapters for already-known products, but must perform
zero metadata upserts. Previous registry fields are never merged into current
metadata as if they were fresh. Onchain values remain canonical for supply and
contract state; Asseto's displayed TVL and APY are informational metadata.

Validation has two severities. Snapshot-fatal errors include duplicate
`(chain_id, address)` keys, conflicting product ids or chains for the same
identity, an empty 200 response and an invalid response envelope. They reject
the candidate before any metadata or Parquet write and retain the previous
cache. Product-fatal errors such as an invalid address, unknown chain or missing
denomination exclude only that product with a diagnostic. An excluded malformed
product is not treated as absent from an otherwise complete response for
lifecycle decisions.

## Existing offchain metadata practices

The repository does not currently contain modules literally named
`offchain_data.py`; the established equivalents are the protocol
`offchain_metadata.py` modules. Follow the strongest shared patterns from
Lagoon, IPOR, Morpho and Accountable:

- keep raw HTTP parsing separate from cache and lifecycle policy;
- use a disk JSON cache under `DEFAULT_CACHE_ROOT` so valid data survives a
  scanner restart;
- use an expiring in-process cache as well as the disk cache, with keys that
  include the cache path/source so tests and alternate endpoints are isolated;
- inject `now_` and cache duration into public fetch helpers for deterministic
  TTL tests;
- protect freshness checks and replacement writes with `wait_other_writers()`;
- write successful cache replacements with `atomic_write()`;
- never silently reset a corrupt cache to an empty mapping: quarantine it with
  a timestamped name and visible error when a complete validated fresh response
  can replace it, or hard-fail when the API cannot provide such a response;
- never overwrite a non-empty cache with an empty, partial or malformed API
  response;
- retain individual stale records when a detail request fails, as Lagoon does;
- distinguish `found`, `not_found` and `transient_error` outcomes where absence
  changes product lifecycle behaviour, following Morpho's three-way result;
- short-circuit permanently unsupported chains before making repeated API or
  RPC requests;
- preserve durable descriptions when a formerly listed vault is omitted, as
  Accountable does.

Asseto differs because the offchain registry also supplies adapter-critical
product ids and daily price-series identity. Its cache is therefore required
for adapter construction, not merely optional description enrichment.

## Maintained overlays

Replace the implicit mixture of hardcoded products, curator mappings and logo
lookups with explicit overlay records:

- add a frozen, slotted `AssetoProductOverlay` keyed by `VaultSpec`;
- use an explicit unset sentinel so “not overridden” differs from “override
  this field to null”;
- require every overlay to include a canonical source URL, reason and review
  date;
- allow an explicit `allow_api_missing` flag only for a reviewed deployment
  that is intentionally supported without a current API registry row;
- keep curator name/slug and partner-logo resolution as reviewed overlays,
  but give them the same provenance fields and validation;
- apply overlays field by field after parsing the API, leaving all unspecified
  names, descriptions and product data under Asseto's control;
- remove the universal AoABT homepage behaviour: use a product-specific API
  link or the Asseto product index by default, with the AoABT documentation
  link supplied only by its overlay.

Permit a standalone product only with `allow_api_missing=True`. If Asseto later
publishes the same `VaultSpec`, use the API record as the base, apply the
standalone fields as an overlay, and emit a warning and audit finding so the
record can be demoted to a normal field overlay.

Add an offline structural validator that rejects malformed addresses, unknown
curator slugs, duplicate keys and overlays without provenance. A separate
read-only operator command should reconcile the live API snapshot and overlays,
report new, removed and changed products, orphan overlays without
`allow_api_missing`, and API-missing active products. It must not modify the
cache or vault database and must not take the cache write lock.

## Shared offchain metadata and registry services

Keep `offchain_api.py` as the raw HTTP/envelope/parser layer. Add
`eth_defi/tokenised_fund/asseto/offchain_metadata.py` for two-level caching,
refresh status and stale-record retention, and add `registry.py` for overlay
merge plus runtime/onchain preparation. Move reusable registry logic out of the
command-oriented `backfill.py` module.

The service should expose `dataclass(frozen=True, slots=True)` result objects,
use `HexAddress` for addresses and `Percent` for APY values, such as:

- `AssetoOffchainRegistryResult`: `fresh`, `stale`, `not_found` or
  `transient_error` status, source timestamp, cache age, normalised API products
  and diagnostics;
- `AssetoRegistrySnapshot`: offchain source status, validated API products,
  merged runtime products and diagnostics;
- `AssetoRegistryRefreshResult`: added, updated, unchanged, inactive,
  unsupported-chain and missing-source product sets;
- `fetch_asseto_registry_snapshot(...)`: perform one registry fetch, optional
  detail enrichment, validation and overlay merge;
- `fetch_asseto_deployment_data(...)`: resolve supported chains, reuse persisted
  deployment data for known products and perform onchain deployment discovery
  only for new products;
- `apply_asseto_registry(...)`: take already-fetched API and deployment data and
  atomically upsert leads/metadata without performing network reads;
- `install_asseto_runtime_registry(...)`: replace API-derived entries as one
  operation while retaining explicit standalone overlays.

The backfill and recurring scheduler must both call this service. Remove the
current ad-hoc mutation of `ASSETO_PRODUCTS` from `backfill_chain()` so there is
only one construction and merge implementation.

Fetch `/api/home/products` at most once per due Asseto cycle, not once per chain
or vault. A due cycle must pass `force_refresh=True` (or a zero `max_age`) so it
always attempts one HTTP refresh; cache freshness must not suppress the next
daily request. Use an in-process success TTL of one hour only for reuse within a
cycle and a failure retry delay configured by
`ASSETO_REGISTRY_FAILED_REFRESH_RETRY_SECONDS`, defaulting to 900 seconds, so a
failed item does not hammer the endpoint on every loop tick. Keep the last valid
disk record indefinitely, report its age, and apply a configurable seven-day
maximum stale age for adapter use. A record older than that yields `failed`, not
`degraded`.

Fetch optional detail descriptions only for selected EVM products; if a detail
call fails, keep its prior cached detail or fall back to the current registry
introduction. Specify bounded connect/read timeouts, retry count and backoff in
`offchain_api.py`: retries log warnings without tracebacks and the final failure
logs one error with traceback. If detail calls are parallelised, use
`joblib.Parallel` with the threading backend and expose
`ASSETO_REGISTRY_MAX_WORKERS`, defaulting conservatively to four.

Store the cache and its lock together under the mounted `DEFAULT_CACHE_ROOT`,
for example `~/.tradingstrategy/asseto/registry-cache.json`, so separate scanner
containers coordinate. Lock acquisition has a bounded timeout; timeout yields
`transient_error` and never permission to write. The cache file must store a
normalised, versioned JSON envelope containing the source fetch timestamp and
products keyed by `(chain_id, lowercase_address)`.
Each product also records `first_seen_in_api_at`, `last_seen_in_api_at` and
whether it was present in the latest complete response. When a valid response
omits a previously known product, retain its last valid record as an explicitly
missing entry instead of dropping the adapter-critical product id. Do not
pickle runtime `AssetoProduct` objects. Validate the complete merged candidate
snapshot before atomically replacing the cache. An unknown or older envelope
version is `not_found` and triggers a refetch, rather than being treated as
corruption; never write an older version over a newer file.

Prefer passing the immutable `AssetoRegistrySnapshot` explicitly into adapter
construction. If a compatibility runtime global remains, build a new dict and
atomically rebind a `MappingProxyType`; never mutate the shared mapping with
`clear()` or `update()` while threaded scans may read it. Show progress with
`tqdm_loggable.auto` if onchain preparation can run for more than a minute.

## Scheduled Asseto cycle

Give Asseto an explicit preparation hook or dedicated runner in
`eth_defi/tokenised_fund/scan.py` instead of relying solely on the generic
feature-to-adapter path:

1. Force one refresh attempt for the due cycle or load one validated stale
   Asseto snapshot after that attempt fails. Record the explicit source status.
2. Merge maintained overlays and install the complete runtime mapping before
   constructing any `AssetoVault`.
3. Group supported EVM products by chain. Skip unsupported chains and missing
   RPC configuration with visible diagnostics.
4. Reuse `first_seen_at_block` and `first_seen_at` from existing vault
   detections. Query an archive RPC for deployment information only when a new
   product is first registered. Respect per-chain historical-state capability;
   never assume archive-complete state or probe arbitrary old state on Monad.
5. Only for a `fresh` result, upsert new products and refresh existing Asseto
   metadata from the merged snapshot. A `stale` result reconstructs existing
   adapters but hard-skips every metadata upsert. Preserve historical rows;
   never delete a product merely because it disappears from one API response.
6. Load any required non-USD conversion history from the same currency-rate
   database used by the existing Asseto backfill.
7. Select price-capable products from the successfully prepared snapshot and
   invoke the existing address-scoped daily historical price path. Scope every
   deletion and write to `(chain_id, address)`. Initialise reader state for a
   new product independently at its deployment block and backfill only that
   address; its older deployment block must never lower a chain-wide start block
   or delete another product's rows.
8. Publish registry counts, skipped products, source status/cache age, API
   freshness and price results on the `Asseto` dashboard row. Record one of
   three states: `ok` for a fresh completed cycle (advance the cycle key),
   `degraded` for a stale or per-product coverage failure (advance the cycle key
   and publish its age/reasons), or `failed` when no valid snapshot exists or a
   registry-wide write fails (do not advance the cycle key).

Pass the currency database path and Asseto registry worker limit through
`TokenisedFundPriceScanContext`, `run_scan_tick()` and production Compose
configuration. The manual recurring-path backfill must use the same Asseto
preparation service, including exact product filters.

## Failure and product-lifecycle behaviour

- A transient, empty, malformed or internally inconsistent registry response
  must not replace the last valid disk cache. If a valid stale snapshot exists,
  use it only to reconstruct adapters for known products and leave stored
  metadata untouched, while exposing the stale source timestamp and refresh
  failure in diagnostics.
- If neither a fresh response nor a valid stale cache exists, abort the Asseto
  item before any metadata or raw-price write. Existing pipeline data stays
  untouched and the cycle key is not advanced, so the next loop retries.
- Do not use a stale snapshot to register a product as new, mark a product
  removed, or erase/replace current metadata. Stale data may only retain known
  products and support their existing price adapters.
- A retry within `ASSETO_REGISTRY_FAILED_REFRESH_RETRY_SECONDS` reuses the
  in-process failure result without issuing HTTP. A failed Asseto item must
  never block or delay later tokenised-fund protocols in the same tick.
- Optional detail or role requests may fall back to other valid API fields and
  add diagnostics; they must not replace good descriptions with blank text.
- A product removed from a valid API snapshot is retained in the cache,
  metadata and raw history with its last-seen timestamp. If it has zero supply
  it becomes inactive and is not rescanned.
- A missing API product with positive supply or an expected price feed is a
  per-product coverage error unless a maintained `allow_api_missing` overlay
  supplies the required adapter data. Retain its metadata and price rows,
  exclude it from price scanning, publish an error diagnostic and continue the
  overall cycle as `degraded`; only registry-wide failures abort the item.
- A new supported product is registered automatically, then enters the same
  daily price scan. A new product without a usable denomination or price source
  remains visible with an explicit diagnostic and cannot silently claim fresh
  price coverage.
- Per-product API price histories remain cached inside the adapter for the
  duration of the cycle. No API request is made for every sampled block. Track
  registry/detail and price-history endpoint health separately; after three
  consecutive price endpoint failures in a cycle, short-circuit the remaining
  products with one diagnostic and record the cycle as `degraded`.
- Missing FX coverage is a per-product error: make no Parquet write and never
  substitute a 1.0 rate. Continue the cycle as `degraded`.
- Registry or adapter failures must never widen a Parquet deletion window or
  discard another product's existing rows.

## Code changes

1. Add `eth_defi/tokenised_fund/asseto/offchain_metadata.py` for the versioned
   disk cache, expiring in-process cache and explicit refresh outcomes.
2. Add `eth_defi/tokenised_fund/asseto/registry.py` for validation, overlay
   merge, deployment reuse/discovery and metadata upserts.
3. Refactor `constants.py` into immutable standalone products plus maintained
   field overlays; keep the runtime registry clearly labelled as process-local.
4. Refactor `backfill.py` to consume the shared registry service and retain
   only command configuration, plan rendering and historical repair control.
5. Update `vault.py` to consume merged runtime products, return product-specific
   links, and use API descriptions unless an explicit overlay wins.
6. Add the Asseto preparation/runner hook and currency-database context to
   `eth_defi/tokenised_fund/scan.py` and `scan_all_chains.py`.
7. Add a read-only `scripts/erc-4626/check-asseto-registry.py` audit command.
8. Update Compose environment forwarding for any new Asseto settings.
9. Add API stubs for the new modules under `docs/source/api` and include them in
   the relevant API index. Add `README-Asseto.md` to the README table in
   `CLAUDE.md`.
10. Add a dated `CHANGELOG.md` feature entry and use a `feat:` PR title.

## Tests

Use captured API fixtures for deterministic CI; do not make the normal test
suite depend on Asseto availability.

Add focused coverage for:

- rebuilding the runtime registry in a fresh process before adapter creation;
- the exact Ethereum regression product
  `0x78e80da0616887b46a31f39310c2a8b0fbd6a42d`;
- one registry request per cycle and reuse across chains/products;
- two consecutive due cycles issuing two registry requests;
- fresh, stale, not-found and transient registry outcomes;
- stale registry data reconstructing adapters while producing zero metadata
  writes;
- disk-cache survival across a simulated process restart;
- a cold process with no cache reconstructing from a healthy API;
- cache-path/source isolation and injected-time TTL behaviour;
- cache envelope version mismatch and lock timeout yielding `transient_error`;
- atomic replacement and concurrent-reader locking;
- corrupt cache plus healthy API being quarantined and recovered, and corrupt
  cache plus API failure causing zero writes and no cycle-key advance;
- empty/partial API responses retaining the last complete snapshot;
- per-product detail failures retaining cached descriptions;
- new-product metadata registration and existing-product description refresh;
- API description defaults and field-level overlay precedence;
- offline overlay structure/provenance validation and online orphan-overlay
  reconciliation;
- standalone-overlay and API `VaultSpec` collision behaviour;
- snapshot-fatal records failing before writes and product-fatal records being
  excluded without marking them removed;
- API outage preserving metadata, reader state and Parquet rows;
- total price-endpoint outage short-circuiting after three consecutive failures;
- unsupported chains and missing RPCs producing diagnostics;
- non-USD exchange-rate loading and the existing synthetic-USD rule;
- missing FX coverage skipping the affected product without a 1.0 fallback;
- removed zero-supply products being retained but inactive;
- a fresh snapshot omitting a product retaining its metadata and history;
- active products missing from the API requiring a maintained overlay;
- a newly registered product with an older deployment block leaving all other
  products' Parquet rows and reader state unchanged;
- the manual Asseto backfill and recurring cycle producing the same merged
  `AssetoProduct` values;
- the Asseto cycle failing without advancing cycle state when preparation
  fails, while later tokenised-fund protocols still run;
- `degraded` dashboard contents, staleness alert threshold and cycle-key
  advancement;
- the audit command not mutating the cache or vault database.

Keep a separately invoked live API smoke test or audit command for operators;
CI should test parsing and merge behaviour from recorded responses.

## README-Asseto.md

Add `eth_defi/tokenised_fund/asseto/README-Asseto.md` as the operational and
architectural source of truth. It should contain:

- what Asseto products are and why they use `VaultBase` rather than ERC-4626;
- the public application endpoints and their undocumented/stability caveat;
- the two-level cache, forced daily refresh, one-hour in-process reuse, 15-minute
  failed-refresh delay, seven-day stale limit and source-age diagnostics;
- the registry/detail/onchain/overlay precedence table;
- the scheduled cycle from registry refresh through metadata and daily prices;
- product identity, supported-chain filtering and new/removed product handling;
- onchain pricer versus Asseto daily API price sources;
- USD, stablecoin, synthetic-USD and historical FX conversion rules;
- the exact purpose, format, provenance and maintenance process for overlays;
- KYC-gated subscription/redemption limitations;
- failure behaviour, dashboard diagnostics and safe retry semantics;
- environment variables and examples for registry audit, dry-run backfill and
  targeted repair;
- relevant implementation files and focused tests.

Link this README from the Asseto API and vault Sphinx pages and from
`scripts/erc-4626/README-vault-scripts.md`. Update those shorter documents to
defer detailed behaviour to the README and remove claims that the recurring
cycle does not yet implement. Add API stubs for new modules to the Sphinx API
index, but per repository guidance do not build Sphinx during development.

## Production rollout

1. Run the registry audit against production configuration and review every
   supported, unsupported, removed and overlaid product.
2. Run the Asseto cycle in dry-run mode and confirm the Ethereum regression
   address resolves without relying on prior process state.
3. Back up the vault metadata, raw-price files and Asseto registry cache through
   the existing pipeline backup mechanism.
4. Run one targeted production Asseto cycle, then inspect product counts,
   descriptions, denominations, source diagnostics and daily price rows.
5. Exercise two cold starts: move the cache aside with the API reachable to
   prove reconstruction from a fresh fetch, then restore the cache and block the
   API to prove restart survival from disk. Preserve the moved cache for
   recovery and perform both checks as dry runs.
6. Enable the normal 24-hour schedule and monitor the `Asseto` dashboard row
   for at least two cycles, with an alert when source age reaches the configured
   stale limit.

## Acceptance criteria

The work is complete when:

1. a fresh scanner process can construct every supported registered Asseto
   adapter from a newly fetched and validated API snapshot;
2. new supported EVM products can enter metadata and daily scanning without a
   code release or manual backfill;
3. API names, descriptions and product data are used by default, while every
   manual field has explicit provenance and deterministic precedence;
4. API failures or unexplained active-product gaps preserve existing data;
   valid stale caches remain explicitly stale, while a missing valid snapshot
   leaves the Asseto cycle failed/retryable;
5. the Ethereum regression address scans successfully after a container
   restart;
6. the backfill and recurring scanner share one registry implementation;
7. `README-Asseto.md` accurately documents sources, overlays, lifecycle,
   pricing, operations and failure semantics.
