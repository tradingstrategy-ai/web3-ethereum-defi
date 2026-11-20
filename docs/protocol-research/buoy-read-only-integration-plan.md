# Buoy read-only integration plan

Status: implemented in the worktree; production migration pending. Date: 2026-11-14.

## Objective and scope

Add Buoy vault discovery, metadata, onchain accounting and historical published
share prices to the existing vault scanner and exports on HyperEVM (999).
**Onchain data is authoritative.** Offchain data enriches descriptions and
presentation through a protocol-local `offchain_metadata.py`, following Lagoon
and IPOR. Do not use the Buoy API for NAV, TVL, prices, historical returns,
fees, token identity, supply or deposit permission.

This plan supersedes the API-backed pricing recommendation in the earlier
[preliminary research](buoy-0x87f76a7e6c346904192c884d48c02d3ac640de1e.md).
That note records initial evidence and unverified deployment details.

Deposit/redemption managers, transactions, guard policies and independent live
HyperCore NAV reconstruction are outside this change. Public execution
capability remains unsupported (`None`); do not certify synchronous ERC-4626
support. The user-authorised exception to `add-vault-protocol` is limited to
execution managers, guards and transaction certification; retain the other
read-only onboarding requirements.

## Source contract

Buoy's [integration documentation](https://docs.buoy.finance/developers/smart-contract-integration)
describes ERC-20 shares and ERC-4626-like valuation views, but a bespoke Router
request lifecycle. Its NAV is calculated by a protocol fulfiller and published
onchain. There is no periodic heartbeat: settlement and explicit NAV
publication update it. This integration observes the published accounting;
it does not independently verify the fulfiller's valuation.

| Field | Authoritative source |
| --- | --- |
| Vault existence, factory and implementation generation | Reviewed factory registries, clone bytecode and creation events |
| Name, symbol, share decimals and supply | Vault ERC-20 reads |
| Denomination token and its decimals | `asset()` and denomination ERC-20 reads |
| Published net assets and share price | Version-verified `totalAssets()`, `pricePerShare()` / `convertToAssets()` |
| Gross cached NAV and valuation time | `latestNav()`, `latestNavTimestamp()` |
| Pending deposits and withdrawal liabilities | `pendingDepositsUsdc()`, `totalOwedUsdc()` |
| Leader, fee rate, high-water mark and pause state | Vault configuration reads at the requested block |
| Admission, caps and leader-share limits | Version-specific onchain rules; v3 `policy()` and policy views |
| Description, summary, image and public social links | Buoy public API via `offchain_metadata.py` |

All economic reads in a snapshot use the same block. API values cannot fill
missing economic reads. Unknown and failed reads remain unknown, preserving
prior verified values with their provenance through existing recovery paths.

## 1. Verify interfaces and versions

- Follow `eth_defi/abi/README.md`. Obtain verified Solidity sources or canonical
  application-exported interfaces for the Router, each supported vault
  implementation, factory and policy. Save external interfaces under
  `eth_defi/abi/buoy/` with a source/date/deployment README.
- Resolve the Router proxy implementation and vault clone targets. Build a
  reviewed v1/v2/v3 inventory, with activation blocks. v1 lacks the documented
  `VAULT_VERSION()` accessor; this must not reject legitimate v1 deployments.
  Record availability of the NAV timestamp accessor per generation. A generation
  without a verifiable publication time remains catalogue-only unless a reviewed
  onchain publication event supplies the equivalent timestamp.
- Confirm valuation units, pending-deposit exclusion, withdrawal-obligation
  subtraction, fee-share dilution and zero/empty-supply behaviour from source
  and block-pinned reads. Do not subtract liabilities twice from `totalAssets`.
- Verify which operations update the cached NAV versus its timestamp. Cash
  payouts, write-downs and supply changes can adjust accounting after a NAV
  publication: sample the actual views, not `latestNav / currentSupply` alone.
- Source availability remains a gate to claiming verified accounting semantics.
  The earlier explorer attempt returned 403 and Sourcify had no v3 match.
  Unsupported generations remain explicit, rather than guessed from a ticker.
- Read `docs/README-hyperevm-hypercore-read-gas.md` and verify that selected
  historical views read cached EVM storage rather than live Core precompiles.
  Do not assume such a dependency exists merely because the protocol uses Core.
  If present, apply existing isolation/unavailability handling; unavailable
  historical Core data must not abort neighbouring vaults or create fake prices.
- Finish the base-class audit before implementation: record whether the shared
  ERC-4626 read machinery is safe to reuse or the adapter must use `VaultBase`,
  including warmup, historical-reader and factory assumptions.

## 2. Discover and classify vaults onchain

- Add `buoy_like` routing and protocol naming in `erc_4626/core.py`, and adapter
  construction in `erc_4626/classification.py`. The feature is a routing marker,
  not a claim that Buoy supports ERC-4626 execution or ERC-7540.
- Enumerate the Router's current and retained legacy factories, then paginate
  `allVaults` or use `vaultsCount` / `vaultAt`. Validate membership and reviewed
  clone implementations. Registry enumeration must work without the API.
- Recover historical factory addresses and deployment blocks from reviewed
  Router/factory history, including retired factories absent from today's
  legacy list. Keep retired vaults as historical records; retirement does not
  prove that historical holdings were worthless or that current exits work.
- Feed addresses into the existing lead database and scan-record path. Add a
  small protocol discovery helper only where existing registry discovery cannot
  express this. Do not bolt a separate scanner or persistence system onto it.
  Registry discovery constructs `ERC4262VaultDetection` records with
  `features={ERC4626Feature.buoy_like}` and verified creation boundaries. Persist
  that classification through the existing discovery state. Classification
  refreshes must revalidate the registry-derived identity rather than losing it
  because a generic ERC-4626 probe does not recognise Buoy.
- Use Hypersync for creation/configuration events, handling both documented
  `VaultCreated` signatures. No JSON-RPC `eth_getLogs`. Reuse cached Hypersync
  timestamps. Do not infer economic flows from ERC-20 fee mints or request events.
  Inflow/outflow metrics remain unknown (null), not zero, until bespoke flow
  accounting is implemented separately; cover this in flow/export tests.
- Avoid a universal probe unless needed for normal candidate discovery. Prefer
  registry-derived identity; if a probe is necessary, choose one reviewed
  no-argument accessor covering the required generations, within the two-probe
  budget. Do not classify all `buoy*` tokens by their name.

## 3. Implement the read adapter and historical reader

Create `eth_defi/erc_4626/vault_protocol/buoy/{__init__,vault}.py` with
`BuoyVault` and a protocol-specific historical reader. Prefer the common
`VaultBase` contract; reuse `ERC4626Vault` read machinery only after auditing
its assumptions and disabling all unsupported inherited behaviour.

- Expose token metadata, denomination, leader identity, fees, published NAV,
  share price, supply, app link and protocol identity through existing accessors.
- Use shared multicall infrastructure for historical reads and the normal
  `create_vault_scan_record()` export path. Audit warmup, reader construction,
  scheduling and all-chain selection so non-standard vaults reach the reader.
- Fix a canonical share-price method per verified generation: prefer
  `pricePerShare() / 10**18` when its source proves that scale. Otherwise use
  `convertToAssets()` for one whole share, converted with the token helpers and
  denomination decimals. Record the choice in the version inventory; a failed
  canonical read cannot silently switch the series to another method. Compare
  other views and net-assets/supply only as block-pinned invariant checks with
  explicit rounding tolerance; warn and reject a conflicting economic snapshot.
- Read performance fees dynamically; document internalised fee-share dilution
  and the native request fee separately. Return unknown for unsupported reads,
  not a hardcoded fee inferred from one example.
- Define `total_assets` as the verified value backing outstanding shares.
  Preserve gross NAV, pending deposits and liabilities in scan-record/metadata
  diagnostics, without adding separate historical raw columns for them. Document
  that this is published accounting, not live HyperCore equity.
- For v3 admission use the policy's whitelist configuration. Keep cap, pause
  and leader-ownership constraints separate from identity restrictions. Review
  v1/v2 independently; absent policy methods and read failures must not become
  permissionless. Unknown risk classification remains unknown.
- No generic liquidity/utilisation estimates or native Hyperliquid vault API
  calls. Buoy's Core account is not a native Hyperliquid leader vault.

## 4. Preserve valuation age through the pipeline

Keep the block read time and NAV publication time separate. Polling a cached
value now proves the contract's current accounting view, not a fresh market
valuation. Store genuine zero valuations; distinguish them from failed reads
and uninitialised, zero-supply vaults.

Historical valuation age is required, not just the latest age: cleaned charts,
backtests and freshness checks must distinguish a publication from repeated
reads of its cached value. The current raw schema has read `timestamp` and
`written_at`, but no general NAV publication timestamp. Add a nullable
`nav_timestamp` only after confirming
there is no suitable existing provenance field. Carry it through
`VaultHistoricalRead`, raw Parquet, cleaned rows, metadata and individual-vault
exports. Other protocols retain null defaults and their existing semantics.

- Leave `timestamp` and `block_number` as the actual sampled chain observation.
  Never pair an old NAV time with current supply/fees and claim it is a historical
  block snapshot. `nav_timestamp` measures the age of its valuation input.
- For Buoy, use publication time for valuation freshness checks as well as the
  genuine RPC observation time. Repeated samples and shared freshness-heartbeat
  retention cannot refresh `nav_timestamp` or clear a stale valuation condition.
  Missing, future or inconsistent publication times cannot certify freshness.
- Explicit publication policy: fresh requires both the read observation and
  NAV publication ages to satisfy the existing shared age limits, with no Buoy
  exemption or new protocol-specific timeout. Stale accounting remains in raw
  history and detail exports with its NAV time/age and stale status, but is
  excluded from current investable rankings and freshness-dependent metrics.
  A quiet but healthy vault may legitimately be stale under this policy; do
  not delete it or invent a heartbeat. Test the exported status and exclusion.
- Audit `vault/historical.py`, `vault/base.py`, `research/wrangle_vault_prices.py`
  and metrics/export freshness consumers together. Use existing age limits;
  document the event-priced series and its limitations. Do not invent an hourly
  NAV schedule or silently turn API quotes into fresh onchain prices.
- Preserve block-aligned reads and real publication changes. Existing sparse
  retention rules may suppress unchanged samples, but an unchanged price with
  a newly published NAV must still retain its newer valuation provenance.
- Resampling uses the last actual row's `nav_timestamp` in each bucket.
  Forward-filled prices retain that price's original publication timestamp,
  never the new bucket time. Null provenance on other protocols must leave
  their existing retention and freshness decisions unchanged.
- Returns describe changes in published share values. Label this basis and
  apply existing sample/freshness requirements to metrics; do not present low
  observed volatility as independently measured intraperiod trading risk.
- A schema addition requires null defaults, a hard failure on migration errors
  and a successful migration test against a copy of production Parquet before
  merge. Never reset existing data to empty on cast/read failure.

## 5. Add offchain metadata in the existing style

Create `eth_defi/erc_4626/vault_protocol/buoy/offchain_metadata.py`, using
Lagoon/IPOR as references rather than introducing a common HTTP framework.

- Provide typed `BuoyVaultMetadata`, `fetch_buoy_vaults_for_chain(...)` and
  `fetch_buoy_vault_metadata(web3, vault_address, ...)` helpers. Use the documented
  public `/app/api/v1` base with optional overrides and explicit HTTP timeouts.
- Parse only descriptions, summaries/taglines, images and public links/display
  labels. Onchain name, leader address and economic fields take precedence;
  an API-provided risk or strategy label is evidence, not our risk classification.
- Key records by chain and normalised vault address. Validate response chain,
  address and pagination. API-only candidates need registry verification before
  enrichment; omission from the API never deletes an onchain vault.
- Fetch metadata lazily and amortise it across vaults. Use a one-day disk and
  expiring process cache, matching Lagoon's refresh behaviour. Include source,
  chain and cache directory in process keys; do not copy an indefinite cache.
- Use file locking and atomic replacement. Keep known-good metadata on transient
  failures, invalid shapes, empty listings or incomplete pagination. Merge failed
  detail reads with old entries. Do not advance successful-fetch timestamps for
  stale fallback; use a bounded failed-refresh cooldown to avoid per-vault bursts.
- A cold API failure yields absent enrichment with a concise warning; onchain
  scanning continues. Corrupt local cache must be surfaced explicitly and must
  not silently overwrite persisted descriptions with an empty result.
- Normalise descriptions and first-sentence plain-text summaries through
  `normalise_vault_description()` / `normalise_vault_short_description()` on fresh
  and cached paths. Preserve manual metadata/notes under existing precedence.
  Do not flag a vault unofficial merely because the API is unavailable.
- No `/quote`, `/performance` or `/allocation` calls in this module or reader.
  No API client in multicall decoding; metadata TTL is unrelated to NAV age.

## 6. Tests and acceptance criteria

Implementation must cover:

1. Registry discovery with API unavailable, current/legacy/retired factories,
   both creation events, deduplication, and rejected unregistered candidates.
2. Exact block-pinned accounting/scales for supported versions: supply, net
   assets, gross NAV, pending deposits, obligations and fee configuration.
   Include published zero, empty vaults, failed reads and unsupported versions.
3. Publication-age propagation through raw, cleaned, scan and individual-vault
   JSON exports; unchanged cached NAV never becomes fresh from repeated polling.
   New publication at unchanged price is retained. Other protocols are unchanged.
4. Metadata pagination, identity validation, chain/case keys, TTL expiry within
   one process, failures/cooldown, atomic cache preservation and text normalisation.
   Demonstrate that malicious/conflicting API economics cannot override RPC data.
5. Public capability stays unsupported and admission preserves public,
   restricted and unknown cases. API failures never erase known metadata.
   Unsupported Router flow accounting exports null rather than zero net flows.
6. At least one minimal real HTTP integration test against the public metadata
   API and real RPC reads via `JSON_RPC_HYPERLIQUID`. Record the command, date
   and redacted provider in the implementation PR comment. No transactions needed.

Use focused tests under `tests/erc_4626/` and existing freshness/export tests;
follow `eth_defi/testing/anvil_fork_pool.py` for any fork tests. All pytest
commands must source `.local-test.env`, use Poetry and a three-minute command
timeout. Copy the ignored environment link file from the main checkout when
needed; do not edit it or invent RPC URLs. Format only changed Python files.

## 7. Publication and historical repair

- Add protocol risk/fee matrix entries, metadata YAML, original/formatted logos
  including `light.png`, protocol feed entry and API/narrative documentation.
  Apply the description-writing guide to public copy and the strategy-tagging
  skill to every covered vault. Check scanner and individual-vault JSON exports.
- Include a new numbered migration for bounded lead/metadata recovery and any
  required price backfill using the onchain reader. Freeze reviewed addresses
  and first-seen boundaries; do not reset reader state or scan the entire chain.
- Preview/apply must follow `.claude/docs/migration-scripts.md`, with the looped
  scanner stopped, lifecycle guard before writer lock, dense timestamp cache
  available, preservation of unrelated history and explicit write postconditions.
  Document production run commands in a Run after merge PR comment.
- Do not import the API's historical price/return series in this change. Do not
  fabricate intraperiod prices between onchain valuation publications.

## Delivery gates

The read-only integration is complete when registered vaults scan without the
Buoy API, onchain accounting and valuation provenance survive normal exports,
optional metadata refreshes safely, supported historical published values are
backfilled without data loss, and focused real/unit tests pass. If accounting
or freshness cannot be verified for a generation, omit its historical reader
through the existing unsupported-reader path and exclude its price-dependent
metrics, retaining catalogue data and any previously verified history. Do not
add a new publication feature flag merely for Buoy. Keep that generation's
price publication unsupported until the missing gate is satisfied;
do not call that partial delivery the completed read-only integration.

This task creates the plan only. It performs no integration, migration,
production action, dependency change or push.

## Plan review

Reviewed on 2026-11-14 with Claude CLI using the exact model
`claude-opus-5-5` (Opus 5.5). The reviewer received the complete numbered plan
and repository context inspected by the primary agent; this was a text-only
plan review, not an independent source-code or deployed-contract audit.

The initial findings led to explicit stale export/ranking rules, canonical
price selection, null flow metrics, timestamp availability by generation,
historical resampling semantics and HyperCore-read verification. It also
clarified classification persistence and the rationale for historical NAV
provenance rather than latest-only metadata.

The updated plan received a completed second review: **No blocking findings.**
Residual risks are source/interface verification, historical NAV/timestamp
consistency, regressions in shared retention/resampling and the production
Parquet migration. Those remain implementation gates, not checks completed
by writing this plan.


## Implementation record

The read-only adapter, official factory discovery, ABI provenance, descriptive
API cache, metadata and logos, historical NAV provenance, freshness policy,
API/narrative documentation and numbered migration are implemented. Live tests
exercise the public API, registry membership and all four verified vault
implementations. A full production Parquet copy passed the nullable schema
migration check; an isolated bounded price-backfill preview also passed.

Migration 101 freezes the 60 reviewed vault identities and creation boundaries.
Its historical end is the earlier of each vault's first saved price and the
saved HyperEVM scanner resume block, so a deployment after the research cutoff
cannot leave a gap. Historical eligibility is checked at that endpoint, and stateless Buoy reads
include it when it falls after the regular hourly grid. Complete empty-supply
accounting may be retained without claiming a bootstrap price; unavailable
observations cannot satisfy that exception. Preview
uses a temporary metadata API cache and preserves the scheduled reader state.
The full production backfill and publication remain operational follow-up work.

An independent implementation review with Claude CLI Opus 5.5 identified
backfill handoff, preview cache isolation and unavailable-observation handling
issues. These were corrected with focused regression coverage, including the
raw cleaner, hourly forward fill and public freshness export.


The completed final implementation review used Claude CLI
`--model claude-opus-5-5` and returned **Approve; no blockers**. It confirmed
that ordinary protocols and stateful sampling retain their existing behaviour.
The final focused regression run passed 276 tests; the opt-in production-copy
schema test had already passed separately against all 21,797,043 source rows.
The isolated real backfill preview was repeated successfully after the endpoint
sampling change, producing two rows (one priced), with no source errors or
scheduled reader-state changes. Public metadata copy was checked against the
repository description writing guide. The full production migration remains
unapplied.

The threshold follow-up sets Buoy's indexing, price-cleaning, polling traction
and ranking minimums to $500, with the small prop trading protocol rationale
next to the shared constant. Existing NAV freshness exclusions still apply.
The focused follow-up regression run passed 258 tests; Claude CLI Opus 5.5
returned no blocking findings. This changes runtime policy and requires no
persisted metadata or reader-state migration.

The fee-completion follow-up documents the HYPE request charge as outside our
read-only indexing and ranking use case, while retaining the depositor cost
disclosure. All standard JSON fee fields and gross/net fee objects are verified
through the scanner-to-export path. The v2/v3 referral component is read
onchain and kept inside the aggregate performance rate; legacy and direct
adapter paths retain the internalised fee mode. The focused regression run
passed 227 tests, including real RPC fee checks for all four generations.
Claude CLI Opus 5.5 approved the follow-up with no blockers. Protocol component
preservation also restores reported values in other protocols' JSON exports;
return calculations are unchanged. The existing pending migration 101 reads
the completed fee schedule when onboarding Buoy; no production state was
modified. Public fee copy passed the description writing guide review.
