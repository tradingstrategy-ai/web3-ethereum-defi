# Neutral Trade offchain integration plan

Date: 2026-11-12. Status: authenticated verification passed; common discovery/backfill/live collection, private storage, native exports, manual reports, tests, migration and documentation implemented locally. Production activation is pending review and migration execution.

## Objective and scope

Add Neutral Trade lead discovery, historical backfill and ongoing reads through one API collector, using the existing native vault architecture. Include native Solana vaults and registered EVM records, preserve Curated/Marketplace distinctions when verified, and report Voltr coverage explicitly. Produce the shared metadata, price history and JSON consumed by the vault website.

WSOL-denominated vaults are excluded from this integration. Identify them from the directory's denomination asset/mint, retain only a minimal skipped-lead record with reason `unsupported_denomination`, and do not fetch their detail/history, backfill them, poll their prices or export them. Apply this exclusion even when `VAULT_IDS` explicitly selects one. Resolve the denomination from verified asset metadata rather than the vault name; unresolved assets remain ineligible until identified. No SOL/WSOL cleaning, valuation or website support is part of this plan.

Use REST only: no RPC connections, contract ABIs, transaction builders, wallet signing or execution adapter. This is a data integration; existing EVM readers remain separate sources.

The [research](../../docs/protocol-research/neutral-trade-api-integration-2026-11-12.md) records the anonymous checks and limitations. The [API specification](https://api.neutral.trade/openapi.json) documents the authenticated contracts, which have not yet been tested. The public feed returned 14 Solana vaults and one EVM vault on the research date, not a proven complete Marketplace/Voltr catalogue.

## Existing implementation templates

Before writing Python code, read and follow the current [CLAUDE.md](../../CLAUDE.md), including its Python rules, comments/docstrings, type hints, logging, datetime, enums, pytest, command-line scripts, parallelisation and formatting conventions. These requirements apply to every implementation phase, including the initial inspector, reusable modules, tests and migration scripts. Existing integrations are architectural templates; their older code does not override the current conventions.

In particular, use Python 3.14, fully typed arguments and return values, `dataclass(slots=True)`, module-level imports, threaded network reads, `fetch_` names and iterators where appropriate. Follow the Sphinx docstring and `#:` member-comment rules, module-level loggers with deferred `%s` formatting, specific exception handling, naive UTC datetime helpers and snake_case string enums. Use `HexAddress` for EVM addresses, a suitable Solana address type for base58 accounts, and `Percent` for fee fractions. Follow the repository's Poetry/environment commands, focused pytest conventions, progress reporting and explicit-path Ruff formatting. Consult `CLAUDE.md` for the complete requirements rather than treating this summary as a replacement.

Use these concrete precedents instead of introducing a new ingestion framework:

- `eth_defi/apex/session.py`, `vault.py`, `metrics.py`, `config.py` and `vault_data_export.py`: bounded HTTP reads, typed responses, discovery and history orchestration, single-owner DuckDB writes, lifecycle state and shared exports.
- `eth_defi/derive/v3_vault_metrics.py` and `v3_vault_data_export.py`: native API observations, explicit accounting denomination, mainnet export and history preservation.
- `eth_defi/hibachi/vault_data_export.py` and `eth_defi/grvt/vault_data_export.py`: `VaultRow`, API price provenance and fee conventions. Do not copy a chain-wide delete-and-replace merge; retain unmatched history.
- `eth_defi/vault/scan_all_chains.py`: `scan_apex_fn`, scheduled native items, cycle callbacks, native price merging and database backup registration.
- `scripts/apex/vault-metrics.py` and `scripts/erc-4626/post-process-prices.py`: standalone recurring reader and shared post-processing entry point.
- `tests/apex/`: HTTP, ingestion, exporter and scanner orchestration tests; `tests/derive/test_v3_vaults.py` for native API coverage.
- `docs/source/api/apex/index.rst` and `docs/source/api/hibachi/index.rst`: Sphinx module stubs and index registration.

## Architecture and invariants

```text
Authenticated directory -> persistent leads and metadata
                                   |
                     one per-vault collection routine
                       /                        \
          first/full history                 incremental history
                       \                        /
                  canonical observations in DuckDB
                                   |
                 shared metadata and price export
                                   |
                   cleaning -> returns -> JSON
```

Live reading means periodic REST polling. Discovery, first backfill, refresh and incremental scans share the same client, parsers, storage, cursor handling and export rules. Manual scripts only choose configuration and call this implementation.

Important invariants:

- Keep exact API identities, original units, source timestamps and provenance.
- Never delete old metadata or prices because an API response is empty, shortened, partial or unavailable.
- A failed page cannot advance the completed history boundary; a failed vault remains due.
- Backtest and pre-launch display history never silently become live performance.
- Existing Accountable records retain their identity, curator and authoritative fields.
- Only one process writes a source database at a time, and worker threads never write DuckDB.

## Phase 1: establish the authenticated contract

Phase 1 is a hard go/no-go gate before implementing the persistent schemas, collector and export in phases 2–5. Use a supplied `NEUTRAL_TRADE_API_KEY` to run a bounded inspector against `/v2/status`, `/v2/vaults`, detail, share-price, snapshots and metrics. Revise the provisional storage contracts below against the real responses before proceeding. Never log credentials or HTTP authentication headers. Read `/public/yields` as a coverage cross-check, not a fallback pretending to provide the authenticated catalogue or history. Without a key, complete the inspector and fixture tests but report authenticated integration as blocked; do not enable production collection.

Confirm public redistribution rights and quota allocation. Prefer separate development, CI and production keys; otherwise allocate a conservative shared quota across processes/environments, because an in-process limiter alone does not coordinate them. Check UTC day boundaries and which legacy rows represent actual live prices.

For representative USDC and EVM USDe vaults (skip WSOL at directory filtering):

1. Record catalogue identity, visibility, manager, asset decimals, fee units, lifecycle fields and projection timestamps.
2. Fetch at least two pages with a small limit from the canonical price and snapshot routes where history is available. Confirm cursor ordering, inclusive date bounds, ties and terminal cursor behaviour.
3. Confirm PPS/share-supply scales and NAV units against simultaneous API values within documented rounding. Do not infer share decimals solely from the deposit token.
4. Confirm canonical history start, legacy provenance, and whether observed PPS is gross or net of each fee. Keep fees unknown where evidence is insufficient.
5. Compare catalogue membership with the public feed and website. Confirm how to identify Curated, Marketplace, external and Voltr products, and whether their full histories are available. Do not infer product tier from names.

Save small sanitised response fixtures with retrieval date and endpoint, excluding holder/user data. Record missing scopes explicitly. A partial integration may cover verified registry vaults, but must not claim complete Voltr/Marketplace coverage. If an external product is not exposed by the documented routes, specify a separately reviewed data source before adding it.

Deliverable: `scripts/neutral-trade/inspect-vaults.py`, reusable client/parsers and a dated real API result. The inspector defaults to a small catalogue/history sample and writes no production state.

## Phase 2: client, models and identity

Create `eth_defi/neutral_trade/` with `__init__.py`, `constants.py`, `config.py`, `session.py`, `vault.py`, `metrics.py`, `vault_data_export.py` and reviewed `tags.py`. Avoid extra abstraction layers; share genuinely reusable existing HTTP/storage helpers where compatible, without importing ApeX business rules.

Use typed `dataclass(slots=True)` records for leads, vault configuration, canonical price ticks, daily snapshots and scan results. Network functions use the `fetch_` prefix and expose iterators for paginated results. Parse exact integer strings before converting normalised reporting values, validate finite numbers, and retain missing values as null. Use naive UTC datetimes.

Use a bounded requests-based client with connect/read timeouts, operation deadlines, response limits, explicit 401/403 failures, and bounded retries for 429, 5xx and transient network failures. Honour `Retry-After` within the operation deadline. Coordinate quotas across workers. Log retryable errors as concise warnings; log a traceback only on final failure. Use `joblib.Parallel` with threading and a `max_workers` argument; expose `MAX_WORKERS` to operators. Dispatch bounded page work in rounds and consume `return_as="generator_unordered"` results in the owner thread, committing each page before scheduling its successor. Do not buffer an entire vault history in a worker before persisting progress.

Identity is an implementation gate, not a cosmetic string change:

- Internal source key: deployment plus chain plus exact `bundleKey`.
- EVM shared identity: existing numeric chain ID and normalised contract address.
- Solana: use the canonical vault account identified by `bundleKey`, not a manager or share mint; verify this in phase 1. Decode base58 to exactly 32 bytes without an on-curve restriction, because PDAs are valid vault accounts. Encode these bytes as lowercase hex in the provider-neutral `solana-vault-{64_hex_digits}`. Future providers must reuse this identity for the same account. Store the exact base58 address as `other_data.neutral_trade.vault_address` with `source_chain=solana` and the explorer link in the existing JSON metadata extension; audit the exporter to preserve these fields. Reuse a declared base58/Solana codec, or add a direct dependency with the required reason comment.
- The synthetic chain denotes Solana mainnet across providers, not Neutral Trade. Before persisting any shared data, reserve an unused numeric ID after checking chain mappings and persisted datasets, and record it as `SOLANA_DATASET_CHAIN_ID` in the implementation and this plan. That pre-persistence gate remains necessary because persisted datasets have not been inspected during planning. Add the chain's website/export metadata and explorer routing, coordinate any frontend chain allowlist update, and verify base58 display/link behaviour end-to-end. No raw Parquet schema change is needed for the string identifier or existing JSON extension.
- Register the prefix, native feature/protocol names, slug routing, chain metadata and API price provenance wherever the existing native integrations require them. Audit hard-coded native allowlists rather than changing global EVM normalisation.
- Test round trips and distinct valid Solana addresses whose encodings differ in case; never lowercase the original base58 address.

## Phase 3: discovery, storage, backfill and live reading

One `run_scan()` discovers the directory and invokes the same per-vault reader for new and existing leads. Reuse the directory's actual response shape; do not assume it is paginated just because history is. A target-vault filter limits collection, not the meaning of a complete catalogue observation.

Persist in-scope authenticated leads, including hidden or inactive records, with first/last seen times, reviewed visibility and availability. Excluded WSOL leads retain only the minimal skip record described above and are never scheduled for data reads. Only eligible `visible=true` records may enter public presentation. Retain disappeared records and history, but distinguish absence from explicit deprecation. An incomplete directory or `unavailableVaults` response must not mark omitted vaults retired. Refresh flags for previously exported records so hiding a vault removes public eligibility without destroying its history.

Use `neutral-trade-vaults.duckdb` under the configured pipeline data directory, with logical tables:

| Table | Purpose and logical key |
| --- | --- |
| `vault_metadata` | Latest lead/configuration per deployment, chain and exact API ID; lifecycle, tokens, raw source metadata and last successful observation |
| `price_ticks` | Canonical PPS/equity/share observations, identified by source event identity and timestamp; retain ties and provenance |
| `daily_snapshots` | One source snapshot per vault and UTC date; retain close versus legacy-mean method and valuation timestamp |
| `history_sync` | Per vault and endpoint: pending range/cursor, successful coverage bounds, attempts, errors and refresh eligibility |

Finalise the exact event key in phase 1: prefer documented immutable event identity including event index where supplied. Legacy daily rows use `(vault, UTC date, legacy source kind)`; snapshots use `(vault, UTC date)`. If an endpoint exposes multiple canonical rows at one timestamp but no stable discriminator, do not invent an order: withhold that ambiguous series pending clarification. Corrections replace the same logical key by latest successful fetch, retaining revision provenance. The shared export key is `(chain, address, timestamp)` with deterministic source precedence. A missing row in a shorter or bounded response is not a retraction. Only an explicit source tombstone or verified complete authoritative range can soft-supersede a previous tick; absent that contract, retain and flag the discrepancy. Propagate confirmed retractions to matching Neutral-owned exported keys through a scoped reconciliation, preserving all other history.

Preserve raw amount strings and explicit scales; normalised floats are export values, not the sole stored source. Preserve returned cursors, `asOf`, `priceAsOf`, `sourceEvent`, `isLegacy`, method and fetched-at time where applicable. Display history, if collected for diagnostics, is a separate dataset with `phase`/`carried`; it is not a price-source fallback.

Follow ApeX's single owner-thread writes. Use unconstrained tables, transactional staging/hash-join deduplication and `wal_autocheckpoint='1TB'`; no ART-backed primary/unique constraints. Close connections in context managers/finally. Test with a file-backed database and substantial repeated ingestion.

Collection policy:

- **New lead:** apply the denomination exclusion first; for an eligible lead, save discovery immediately, start a resumable backfill of the recoverable canonical range and snapshots, and report the earliest available observations. Do not call that inception unless supported by source evidence.
- **Incremental read:** refresh current metadata/PPS and request history from the last completed date with a default seven-day correction overlap, using a fixed end date for each pass. Store exact source time, not polling time, as the price observation time.
- **History maintenance:** daily eligibility by default; the first backfill is immediate. Periodic/operator full refresh repairs corrections outside the overlap without erasing unmatched rows.
- **Resume:** commit a page and its progress atomically. Keep in-progress cursors separate from completed range coverage. On cursor rejection/expiry, restart the bounded range and deduplicate; stop on repeating cursors or no progress. A partial page set remains incomplete and retryable.
- **Lifecycle:** final history refresh for explicit retirement, while preserving records. Disappearance or a transient empty history does not prove finality. Empty histories produce no fabricated price rows and remain distinguishable from successful populated coverage.
- **Visibility:** retain history for analysis, but apply current catalogue approval and stale/unavailable status at the shared presentation boundary.

Scheduled collection has a default five-minute item budget and 100-page ceiling per tick, stops scheduling new requests before the budget is exhausted, and resumes persisted cursors on the next tick. Divide work fairly among new and existing vaults so current reads continue during backfill. A normal budget yield is `pending_history`, not a failure or completed coverage; it can advance the scheduling timestamp while remaining eligible next tick. Actual failed operations do not advance scheduler success state. Manual initial collection may run to completion with bounded individual requests. Emit progress bars and counters for leads, vaults, pages, inserted/corrected rows, recoverable bounds and failures; no long scan should be silent for a minute.

## Phase 4: shared export and scanner integration

Implement `VaultRow` and raw price DataFrame export using ApeX/Derive. Preserve names, fees, token units, deposit permissions, lock/redemption settings, source links, product tier and verified curator identity. Use `PriceSource.api`; unsupported positions, exposure and redemption capacity remain null. Keep catalogue approval separate from whether deposits are currently possible.

Use canonical PPS ticks for live price observations. Daily snapshots supply historical TVL and source daily closes when ticks are absent, under an explicit source precedence rule. Do not mix daily means, closes and event ticks as interchangeable duplicate samples. On the shared timestamp key, choose the canonical event close deterministically and preserve discarded provenance locally. Export exact timestamps and `block_number=0`, as Derive does; the `1h` filename does not mandate synthesising hourly observations. Exclude legacy daily means from comparable live returns unless their semantics have been verified. Use API-reported `priceUsd`/`closeTvlUsd` and `priceAsOf` for USD valuation only when provenance is verified; do not assume a stablecoin equals one dollar. Legacy snapshots can contain an artificial USD $1 fallback: retain those raw source values locally, flag their provenance and leave admissible USD valuation null unless independently verified. A fresh timestamp alone is insufficient. Backward joins require a price no older than three hours; otherwise USD values remain null. Preserve denomination-unit `total_assets` rather than placing USD values in that field. WSOL records never enter this export path.

Establish fee handling per vault type before enabling comparable net-return ranking: Solana depositor-specific high-water-mark fees, EVM fee dilution and external net PPS cannot share an assumed fee deduction. Keep provider APY as attributed auxiliary data; derive our performance from admitted live price history. If shared fee handling cannot represent a source faithfully, publish only supported metadata/gross data with an explicit limitation and withhold misleading net rankings.

Duplicate policy:

- Store Neutral's EVM observations locally but do not overwrite existing EVM canonical metadata/prices automatically.
- Match existing records by chain/address. Preserve Meridian on Robinhood as Accountable with curator Kappa Lab, and preserve Autopilot's Accountable feeders.
- In the first implementation, EVM records and their historical data remain fully collected in the source DuckDB but do not write shared EVM metadata or price rows. This avoids last-writer ownership and block-range deletion conflicts. Unknown EVM addresses are reported as leads for the existing EVM pipeline, without triggering RPC from this collector.
- Shared EVM enrichment or API price ownership is a follow-up requiring an explicit side-channel/precedence policy and migration. Offchain reading of those records is included now; full shared presentation of unknown EVM records is explicitly deferred.
- Record feeder/master and Autopilot/underlying relationships only when verified. Do not claim a deduplicated platform TVL from summing vault TVLs; withhold that aggregate until the relationship accounting supports it.

Register `scan_neutral_trade_fn`, `SCAN_NEUTRAL_TRADE`, a `Neutral Trade` scheduling item, configurable database path, backups, and native price merging in `eth_defi/vault/scan_all_chains.py`. Wire the same merge through `MERGE_NEUTRAL_TRADE` in `post-process-prices.py`. Preserve old unmatched Parquet rows and unrelated protocol data. Failed schema casts abort; no reset-to-empty fallback.

Persist the catalogue decision in a dedicated metadata extension and enforce it in `eth_defi/vault/top_vaults_json.py` before rankings, aggregate construction and sticky/fallback record reuse; a best-effort cleanup hook is insufficient. Apply the same eligibility filter to publicly distributed metadata/price datasets. Keep hidden/private histories in the private source database and internal archive only. Register that database only with the private backup path in `eth_defi/vault/duckdb_backup.py`, whose current contract is private R2 backups. Do not commit hidden/private response fixtures. An absent source database makes `MERGE_NEUTRAL_TRADE` a logged no-op.

Use a default four-hour scan cadence and 24-hour historical maintenance interval, with new leads backfilled immediately. Keep opt-in until authenticated checks pass and the production credential is provisioned. Explicit enablement without a key fails with a clear configuration error; do not silently switch to public yields. Update scanner environment documentation and Compose wiring, without deploying as part of implementation.

## Phase 5: manual commands and production backfill

Provide these thin environment-configured entry points, all using the same collector:

- `scripts/neutral-trade/inspect-vaults.py`: read-only discovery and a bounded sample, tabulated output.
- `scripts/neutral-trade/vault-metrics.py`: the normal initial and incremental reader. With no stored state it backfills recoverable history; later runs resume. `HISTORY_MODE=refresh` deliberately re-reads available history. No separate duplicate backfill engine.
- Production onboarding will use the next unused `migration/scripts/NNN-neutral-trade-vault-backfill.py` before enabling scheduled reads. Freeze the inventory/range after phase 1, use the collector helpers and migration context, and add the PR's “Run after merge” reminder. Later production refresh/date-range repairs require a new numbered migration. The normal recurring collector can backfill newly discovered leads; manual refresh and experimental range overrides operate only on explicitly isolated paths. Do not add a legacy standalone `backfill-*.py` or `migrate-*.py` repair.

Defaults: required `NEUTRAL_TRADE_API_KEY`; canonical API base; mainnet only; source database in the pipeline directory; `MAX_WORKERS=2`; history page size 100 where supported; connect/read timeouts 10/30 seconds; bounded 120-second page operation deadline; normal incremental mode. Optional `VAULT_IDS`, `START_DATE`, `END_DATE`, `HISTORY_MODE`, logging and path settings are validated before writing. Range overrides request coverage outside the usual daily eligibility gate. A manual full initial scan has no arbitrary total timeout that truncates history, but every request is bounded and progress remains visible; scheduled scans retain their per-tick budget.

Manual collector runs default to source DuckDB only. Shared pickle/Parquet publication is performed through the existing all-chain/post-processing path and its `scan-pipeline` writer lock. Source writers use one `filelock.FileLock` at the resolved DuckDB path plus `.lock`, acquired by the entry-point owner before opening DuckDB. Lock order is lifecycle guard, pipeline writer lock where applicable, then source lock; worker/helper functions do not reacquire locks. Standalone use of production paths requires the daemon stopped and `acquire_scanner_lifecycle_guard()`; isolated source paths can run independently. Migration helpers run under runner-owned lifecycle/pipeline locks and take only the source lock.

The README must show ordinary collection/backfill commands after loading the private environment, and explicit temporary paths for experimentation. Production one-off repair/backfill uses the numbered migration runner with preview/apply, the looped scanner stopped, lifetime guard before writer lock, backups and the production Compose mounts preserved. Inspect Compose and follow the current migration runbook; do not copy historical standalone repair commands.

### Manual data verification

The manual backfill/backtest verification run of `scripts/neutral-trade/vault-metrics.py` must print human-readable tables from the persisted results, using `tabulate.tabulate()`. This verifies the data that a backtest would consume; it does not introduce a strategy backtesting engine. Print these tables by default for standalone runs, including a failed or partial run's available results, with explicit run status and incomplete coverage. Reuse the same read-only report helper in `inspect-vaults.py` and the numbered migration's preview/apply reporting.

| Table | Required columns |
| --- | --- |
| Vault summary, one row per selected vault | Name, exact API ID, chain, denomination, inclusion/skip reason, current PPS and its unit, shares, TVL in asset units, verified USD TVL or unknown, source timestamp/age, fees and fee basis, history row count, earliest/latest observation, complete/pending/failed status |
| Historical samples per included vault | Timestamp, PPS and unit, asset TVL, verified USD TVL or unknown, period return, source/method, legacy flag, USD provenance and warnings |
| Data quality exceptions | Vault ID, affected timestamp/range, missing/stale values, gaps, duplicates, non-finite/invalid values, large price changes, scale/accounting mismatches and unresolved fee or valuation provenance |

Show the first five and last five historical observations per vault by default, plus rows surrounding the largest absolute returns and any legacy/live transition. Deduplicate overlapping samples. Keep the complete exception report in a local report file if console output is capped. Display unknowns explicitly, never as zero. Include excluded WSOL vaults only in the skip summary; do not fetch their data for the report. Distinguish API-reported APY from a locally computed period return, with its time span and gross/net basis; do not annualise a tiny sample to suggest reliable performance. Use contemporaneous price/share/TVL observations when checking their accounting relationship and label a mismatch in observation times.

After the script finishes, the implementing agent or operator must manually inspect the tables: confirm IDs and denominations, plausible scales and prices, chronological history, coverage and gaps, jumps/flat periods, fee treatment and missing USD valuations. Compare representative current records and first/last samples with the corresponding API records or product page using matching timestamps/basis. Investigate anomalies and rerun the focused collection when needed; a successful exit code is not a manual data-quality pass. Record the command, UTC date, reviewed vault IDs/range, observations and pass/fail with a sanitised table excerpt in the implementation PR comment. Keep private/hidden records out of public comments.

Complete this inspection on an isolated backfill before accepting the data for publication or enabling the production schedule. Migration preview and apply reports provide a second check for production onboarding. The script remains non-interactive so it works in automation; human inspection is an explicit acceptance step, not a fake automatic approval flag.

## Phase 6: tests and acceptance

Add focused tests under `tests/neutral_trade/` using the existing pytest function/fixture style.

| Coverage | Required assertions |
| --- | --- |
| Client/parsers | Exact decimals, missing versus zero, timestamps, mixed chains, malformed JSON/envelopes, auth errors, quota/retry deadlines and redaction |
| Identity | Validated base58 round trips, case-sensitive distinct IDs, lowercase-safe shared key, existing EVM key matching and no synthetic chain collision |
| Discovery/lifecycle | New/hidden/private/deprecated leads, partial/unavailable directory, genuine empty catalogue, filtered scans, WSOL exclusion even with explicit selection, and removal of public eligibility without history loss |
| Pagination/resume | Multiple pages, inclusive boundaries, tied timestamps, repeated/expired cursor, failure mid-range, atomic progress, restart and full refresh |
| Persistence | Idempotent/corrected ingestion, shorter responses retaining older rows, file-backed substantial ingestion, no primary/unique constraints, closed connections and competing-writer rejection |
| Export/accounting | USDC/USDe scales, WSOL absence, nullable historical USD, rejection of unverified legacy $1 fallback valuations, price precedence, no backtest leakage, fee modes, provenance, Meridian/feeder deduplication and curator preservation |
| Orchestration | New-lead backfill and later incremental read through the same function, enable/disable flags, missing key, failed-history cycle state, backup registration and resource closure |
| Shared pipeline | Temporary DuckDB -> metadata/raw Parquet -> cleaning -> combined JSON, older-history preservation, visibility enforcement and unrelated protocol rows unchanged |
| Manual verification report | Reports reflect persisted rows, label units/unknowns/partial failures, include boundary and anomaly samples, show WSOL only as skipped, and never report an automatic human-inspection pass |

Real integration tests are required, not replaced by fixtures:

1. Public feed smoke test, without asserting permanently fixed vault counts.
2. Credential-gated authenticated test selecting an eligible non-WSOL real vault from the directory, reading detail/current price, traversing at least two tiny history pages where available, and checking units/provenance and cursor progress.
3. A minimal real scan into a temporary file-backed database followed by a second scan and shared export to temporary files. Assert successful non-empty authenticated data and stable identity/history retention; no production paths.

Missing credentials skip locally with an explicit reason. Add an opt-in manually dispatched job in `.github/workflows/test-neutral-trade.yml`, using secret `NEUTRAL_TRADE_API_KEY` and `REQUIRE_NEUTRAL_TRADE_LIVE_TESTS=true` to fail missing-key preflight. Fork PRs run fixtures only; never expose a secret to untrusted PR code. The equivalent manual command uses the same requirement switch. If CI credentials or reliable access are unavailable, record authenticated validation as incomplete until a real manual run succeeds. Bound the real database/export test to one verified vault and a recent seven-day range with small page/request limits. An empty real history cannot count as a successful historical integration test; report the gap and use another verified vault. Inject clocks in retry tests, assert redaction in logs/exceptions/URLs, and do not pre-mark live tests flaky.

Run focused tests using `source .local-test.env && poetry run pytest tests/neutral_trade/ ...` with an extended three-minute command timeout, splitting longer coverage into bounded groups. In a worktree, copy the existing private source file from the main checkout if needed; never edit it. Reuse the installed parent Poetry environment. No RPC or Anvil fixture is required.

Record the exact live command/test, UTC date, pass/fail, manual versus CI and redacted provider/environment in an implementation PR comment. Run Ruff only on changed Python paths, `git diff --check`, and focused shared-pipeline regressions. If a shared Parquet schema change proves necessary, test it against a local copy of production Parquet before merge; prefer source-specific metadata that avoids a schema change.

## Phase 7: documentation and delivery

Create the specifically requested `eth_defi/neutral_trade/README-delta-neutral.md` as the canonical architecture and operator guide. There is no existing file with that name in this checkout. Explain that Neutral Trade is the provider and its catalogue includes directional and options products as well as delta-neutral strategies.

Cover credentials, endpoint contracts, coverage gaps and WSOL exclusion, identifier mapping, fee/valuation provenance, discovery/backfill/live lifecycle, database schema, scheduler settings, manual commands, data verification tables and the human inspection checklist, migration procedure, rate limiting, recovery, visibility and test commands. Include sane defaults and distinguish required secrets from optional settings. Document earliest recoverable history rather than promising inception coverage. Link sources inline once and keep this README as the operational source of truth.

Add `docs/source/api/neutral_trade/index.rst` with module autosummary entries, register it in `docs/source/api/index.rst`, and add a short narrative page under that directory describing the collection workflow and linking the README. Cross-link the vault scanner documentation. Use Sphinx docstrings for new public APIs and dataframe contracts. Do not build Sphinx locally or edit generated `_autosummary` files.

Register the requested README in the README table in `CLAUDE.md`, and link it from a small `scripts/neutral-trade/README-neutral-trade-vaults.md` operator entry point and `scripts/erc-4626/README-vault-scripts.md`. Preserve the exact `README-delta-neutral.md` filename requested by the user. Follow `eth_defi/data/vaults/README.md` for protocol metadata and reuse the existing Neutral Trade logos after checking suitability. Discover and read matching repository skills (including `add-vault-protocol` for protocol registration) before those implementation steps. Use `Percent` for fee fractions and a Solana-specific address alias rather than `HexAddress`; string enum names and values use snake_case.

Add the dated feature changelog entry and use `feat:` for the eventual implementation PR. Apply the website writing style guide only to actual public metadata/descriptions, and reuse reviewed existing curator assets where appropriate. Complete `clean-up-for-pr` once for the implemented change and record focused checks and external integration results. No production activation is implied by writing this plan.

## Completion gates

- All changed Python modules, scripts, migrations and tests conform to the current `CLAUDE.md` coding conventions; review that conformance during cleanup and format the changed Python paths with Poetry/Ruff.
- Verified authenticated discovery, historical pagination and current reads work through one collector.
- Resume, rerun and partial-failure tests prove history preservation and correct cycle state.
- Solana identities survive every shared pipeline boundary without base58 corruption.
- Existing EVM records are neither duplicated nor reassigned, and live performance excludes backtests.
- Manual initial backfill and refresh commands work; any production repair has a reviewed numbered migration.
- WSOL vaults are skipped before detail/history reads and absent from shared outputs, including explicitly selected runs.
- The manual verification script outputs persisted-data tables; the implementing agent or operator has inspected them, investigated anomalies and recorded the result before publication.
- The requested README and Sphinx source pages exist and link to the actual APIs/modules.
- Coverage gaps and unverified fee semantics remain explicit; no unsupported completeness or net-return claims.

## Plan review

### Implementation progress

Phase-1 implementation on 2026-11-12 adds the bounded REST client, typed directory records, WSOL admission checks, detail/metrics/history reads, read-only tabular inspector, requested README, Sphinx API registration and focused tests. No pipeline databases or production state were modified.

Validation: `source .local-test.env && timeout 180 poetry run pytest tests/neutral_trade/test_api.py -q` passed 15 tests with one authenticated test skipped. The real public feed request passed against `https://api.neutral.trade`; no fixed live vault counts were asserted. Ruff lint/format and whitespace checks passed. Claude CLI Opus 5.5 independently reviewed the code and returned no blocking findings after the focused corrections. Public description style review is not applicable to this engineering-only change; Sphinx was not built.

At the initial phase-1 checkpoint the private environment had no partner key, so authenticated verification was pending. The user subsequently supplied it; the authenticated implementation update below supersedes that checkpoint.

Reviewed on 2026-11-12 with Claude CLI using the exact requested Fable 5.2 model (`claude-fable-5-2`), after a successful model smoke test. The primary agent inspected the repository templates; Claude reviewed the complete plan inline without tools, following the repository's plan-review instructions. This was a plan review, not an authenticated integration test or independent code audit.

The first review requested changes. The plan now resolves its five blocking findings:

1. Authenticated contract validation is a hard gate before persistent collection/export implementation.
2. Solana identities are provider-neutral, preserve base58 case and accept PDA accounts; numeric chain allocation is a pre-persistence gate.
3. The first collector stores EVM data locally without competing for shared EVM row ownership.
4. Corrections, ambiguous ties and verified retractions have explicit rules and shared export keys.
5. Scheduled backfills have time/page budgets and resumable pending state.

Additional feedback incorporated streaming page commits, exact-timestamp/zero-block export, private backups, public visibility filtering, API-sourced USD valuation, lock ordering, numbered production onboarding, bounded credentialed tests and documentation registration. The requested README filename is retained.

The final focused review returned **“No blocking findings”** and **“the plan is approved to proceed to phase 1.”** It emphasised one implementation acceptance check: hiding a previously visible vault must filter distributed price Parquet as well as JSON, while retaining private historical data. Add that transition explicitly to the shared-pipeline test.

Local plan checks: whitespace validation and the research-document link passed. Runtime tests and Sphinx builds are not applicable to this plan-only change. Public website description style review is not applicable to engineering planning text. The exact authenticated responses, partner access and complete Marketplace/Voltr coverage remain phase-1 prerequisites.

A subsequent clean subagent review found that WSOL lacks a supported shared publication path and that legacy USD snapshots can contain a $1 fallback. Following the user's scope decision, this revision excludes WSOL from collection/export, rejects unverified fallback USD valuations and adds explicit manual data-table inspection to backfill acceptance. The earlier review verdicts apply to their respective revisions; they do not certify implementation or live data quality.

A focused Fable 5.2 review of these amendments returned **“No blocking findings”**, confirming that WSOL exclusion, manual tables/human inspection and legacy USD provenance rules are consistent across the plan. Whitespace validation also passed.

## Authenticated implementation update

On 2026-11-12 the supplied partner key passed real status, directory, metrics and historical-pagination checks. Full source collection completed in isolated files and native USDC data traversed the standard cleaner and combined JSON exporter. Dataset partition 9992 is registered for provider-independent Solana account identities. WSOL is excluded and EVM observations remain private. A manually reviewed Options Market Making basis boundary starts canonical publication on 2026-07-09. Two other discontinuous PPS histories remain quarantined pending provider clarification, with all original source data preserved. No production data or remote PR was changed by these local implementation runs.

Final implementation validation on 2026-11-12: 58 focused tests passed, including real provider-to-JSON collection. Manual verification inspected all public native histories and the existing EVM sample. Initial and existing-state migration previews left original input bytes unchanged; existing archival rows remained 3587 before/after. Independent Claude Opus 5.5 review approved after fixes separating archival retention from public eligibility and preserving generic empty-output protection. Migration preview copies existing source and Parquet; a fixed older onboarding range never rewinds newer coverage. Public metadata descriptions/notes were checked against the description writing guide. Production execution and remote PR updates remain separate operator actions.


## Risk and fee correction on 2026-11-12

Supersedes earlier fee-mode uncertainty: the published native Solana fee
schedule and SDK user-share deduction accounting establish externalised fees.
Only explicitly all-zero schedules are feeless. Shared net-return exports may
estimate base rates, while wallet overrides and historical high-water-mark
settlement remain unreconstructed. Native risk is High for centralised
counterparty, custody and asset-freeze exposure despite transparency dashboards.
Official documentation permits CEX, DEX and DeFi venues; do not claim the
protocol never trades on DEXs. Migration 067 refreshes persisted classifications
without rerunning onboarding or changing source/prices/EVM products.


## Curator and description wiring on 2026-11-12

The current partner REST schema and authenticated detail response have a
manager wallet but no named curator or editorial descriptions. Supplement all
thirteen exact native USDC accounts with reviewed official strategy metadata,
retaining its documentation URL separately from API metrics. Export named
curators, short/full vault descriptions and curator profiles through the
existing shared fields. Do not infer an organisation from the common manager
wallet or venue/product names. Published aliases must remain aliases. Native
strategy tags require independent source review and no perpetual-futures
default. Migration 068 refreshes cached editorial fields/tags without changes
to visibility, quarantines, fees, risk, prices or EVM identities.

## PR cleanup on 2026-11-12

- [x] Reviewed staged, unstaged and new files together, then staged the complete integration.
- [x] Fixed rejected-page transaction rollback and retained sanitised deferred-error diagnostics.
- [x] Preserved existing native risk, inception and history decisions on migration 066 retries; metadata commits follow price validation. Source copies and backups require the source lock and no pending WAL.
- [x] Kept the complete migration batch test independent of partner credentials and provider reads. Authenticated end-to-end coverage remains separate.
- [x] Checked public copy against the description writing style guide, corrected short-description sentence fragments and fee wording, and reused the prior source review for unchanged claims.
- [x] Updated engineering documentation, checked Sphinx source references without building Sphinx, formatted reviewed Python files and passed whitespace checks.
- [x] Independent Claude CLI review using `claude-opus-5-5`: approved, including focused follow-ups on the fixes.

The full focused integration, identity, curator, analytics, pipeline and
migration selection passed **544 tests**, with one unrelated opt-in live test
skipped. After the final backup guard and diagnostic changes, the affected
selection passed **21 tests**:

```shell
source .local-test.env && PYTHONPATH=. timeout 180 poetry run pytest tests/neutral_trade/test_metrics.py tests/vault/test_numbered_migrations.py::test_complete_batch_preview_apply_and_scanner_semantics -q
```

This was a manual local run on 2026-11-12, including successful authenticated
Neutral Trade production REST reads through isolated persistence, cleaning and
JSON export. The partner credential came from the private sourced environment;
no RPC or production pipeline writes were used. Integration lint checks passed;
pre-existing lint findings in shared files were not expanded into this change.
Production migrations and publication remain deployment actions after merge.
