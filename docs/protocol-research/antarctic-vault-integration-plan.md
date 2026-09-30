# Antarctic vault protocol integration plan

Status: implemented and locally verified; no production state changes or deployment.
Date: 2026-09-30.
Updated: 2026-09-30. Adapter, normal all-chain perpetual DEX onboarding, live Hypersync checks, scoped migration and JSON/Parquet pipeline integration tests are implemented. The original design and Kimi K3 plan-review record follow.

## Objective and recommended approach

Add Antarctic as a recognised vault protocol with AMLP and AHLP on Arbitrum. Provide a special onchain historical reader whose share price changes only when a qualifying manager settlement event occurs. Reuse the existing contextual-history interface, Hypersync prefill, common sparse Parquet writer and metadata exporter. Do not poll the Antarctic API for valuation or manufacture hourly price observations.

The closest working reference is Rysk Premium, a non-ERC-4626 DeFi pool adapter under `eth_defi/erc_4626/vault_protocol/rysk/`. Antarctic should live alongside it at `eth_defi/erc_4626/vault_protocol/antarctic/` and inherit `VaultBase`, rather than pretending to implement ERC-4626. Antarctic is an exchange liquidity product; permissioned minting alone does not make it a legally structured tokenised fund.

This is read-only vault support. Deposit/redemption transaction construction remains unsupported (`get_deposit_manager()` and its public capability return `None`) until the complete asynchronous lifecycle can be implemented and tested. The onboarding skill explicitly allows this declaration for unsupported flows. Do not expose a partial manager or certify the synchronous ERC-4626 manager.

## Supported deployments

Use LP token addresses as stable vault identities; manager addresses are the event sources. Restrict all matches to chain ID 42161.

| Product | Vault/share token | Settlement manager | Application |
| --- | --- | --- | --- |
| AMLP | `0x152f5E6142db867f905a68617dBb6408D7993a4b` | `0x98a6aEE58699e4f4E13D8d8d0800e4e9cbBcf8dD` | `https://www.antarctic.exchange/lp/amlp` |
| AHLP | `0x5fd22dA8315992dbbD82d5AC1087803ff134C2c4` | `0xc5F9d4b9f68CAAA869317Baa09a233b22940bd9f` | `https://www.antarctic.exchange/lp/ahlp` |

Both share tokens have 18 decimals. The denomination token is Arbitrum USDT, `0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9`, with six decimals. Confirm `manager.amlp()` / `manager.ahlp()` and `manager.usdt()` against the reviewed registry when creating metadata. Lowercase registry keys; use `HexAddress` in typed interfaces and `dataclass(slots=True, frozen=True)` for deployment records.

Before implementation, fetch and record the token and manager deployment blocks and timestamps from canonical explorer creation transactions, then verify them through Hypersync. Do not use a guessed deployment block or a current scan date as the first-seen timestamp. Keep token discovery and manager history boundaries separate; the event collector starts at the verified manager deployment boundary. The investigated first settlements were 2025-01-07 for AMLP and 2025-07-15 for AHLP, but these dates are not deployment proofs.

## Evidence and price semantics

The [investigation](antarctic-0x152f5e6142db867f905a68617dbb6408d7993a4b.md) scanned both managers through block 510301932 with Hypersync. It found 932 AMLP settlements in 733 transactions and 48 AHLP settlements in 48 transactions. Each pool had only two settlement transactions in the preceding 30 days and none in seven days. Direct token mints/burns were much more frequent, but supplied no USDT valuation in their associated transaction logs.

The verified [AMLP manager](https://arbiscan.io/address/0x98a6aEE58699e4f4E13D8d8d0800e4e9cbBcf8dD#code) and [AHLP manager](https://arbiscan.io/address/0xc5F9d4b9f68CAAA869317Baa09a233b22940bd9f#code) emit:

```text
AddLiquidity(address account, uint256 usdtAmount, uint256 lpAmount, uint256 TVL)
RemoveLiquidity(address account, uint256 usdtAmount, uint256 lpAmount, uint256 TVL)
```

The Solidity parameter names differ (`amlpAmount` / `ahlpAmount`), while the event signatures have the same types. Decode the verified ABI, not frontend assumptions about indexed fields. The frontend ABI is already known to contain a stale uppercase cooldown getter.

For the initial implementation, the canonical share-price-equivalent series uses **AddLiquidity only**:

```text
price = denomination_token.convert_to_decimals(raw_usdt_amount)
        / share_token.convert_to_decimals(raw_lp_amount)
```

This is a USDT-denominated subscription execution price, published onchain after handler settlement. It is not an independently computed portfolio NAV, a USD oracle price, or a complete investor total-return index. Manager TVL and execution amounts originate with an authorised handler. Mark the existing `PriceSource.smart_contract_event` and explain the handler origin and sparse observation cadence in the product/integration notes.

Persist RemoveLiquidity amounts as redemption execution-price diagnostics, but do not mix them into the canonical series. Its `redeemAmount` is supplied by the handler and the manager does not enforce the same TVL/supply formula used for deposits. A fee, haircut or different settlement convention could create false performance jumps. Redemption-only periods therefore produce no canonical price updates. Expanding the canonical series to redemptions requires a separately verified gross-price calculation and fee treatment; it is not a fallback when deposits are absent.

Reject zero LP amounts and malformed/mismatched events with a contextual hard error. Zero-USDT redemptions may be retained as diagnostic data if the contract permits them; they cannot price the canonical series. Store all raw amounts as decimal strings or sufficiently wide exact integers; never truncate uint256 values to BIGINT or float before price calculation.

Do not treat `PoolDeposit`, `PoolWithdraw`, cancellation events or ERC-20 mint/burn/transfer events as price updates. Staking distributions are outside this first implementation; returns from the price curve exclude separately paid rewards.

## Existing repository extension points

| Existing path | Relevant behaviour | Antarctic change |
| --- | --- | --- |
| `eth_defi/erc_4626/core.py` | Protocol enum, name resolver and activity exemptions | Add `antarctic_like`, protocol name `Antarctic`, and activity exemption |
| `eth_defi/erc_4626/classification.py` | Chain-aware hardcoded classification and adapter factory | Register the two LP tokens and route to `AntarcticVault`; add `share_price_equivalence` |
| `eth_defi/erc_4626/discovery_base.py` | `DEFAULT_HARDCODED_VAULT_LEAD_SOURCES` injects non-standard leads | Add the reviewed two-token lead registry |
| `eth_defi/vault/base.py` | `VaultHistoricalReader.uses_contextual_history` and `fetch_contextual_historical_reads()` | Reuse; add only the small per-reader comparison threshold described below |
| `eth_defi/vault/historical.py` | Splits static/contextual readers and writes common price rows | Reuse; honour the per-reader threshold in its existing comparison helper |
| `eth_defi/vault/scan_all_chains.py` | Prefills Rysk/GMX/etc. context before scanning | Add one Antarctic row selection, context-path assignment and prefill block |
| `scripts/erc-4626/scan-vaults-all-chains.py` | Production CLI calls `scan_all_chains.main()` | Include Antarctic through the existing Arbitrum scan and document its event-only support |
| `eth_defi/vault/post_processing.py` / `top_vaults_json.py` | Common cleaning and `top_vaults_by_chain.json` publication | Exercise the real stages for both Antarctic products; retain existing output schemas |
| `eth_defi/erc_4626/scan.py` | `create_vault_scan_record()` supports `VaultBase` and optional reads | Reuse without a protocol-specific export branch |
| `eth_defi/vault/price_source.py` | Existing `smart_contract_event` source | Reuse unchanged |
| `eth_defi/vault/settlement_data.py` | Stores settlement dates, not amounts or share prices | Do not use it as the valuation store or change its schema |
| `eth_defi/research/vault_metrics.py` | Sparse period calculations and legacy unavailable-period defaults | Add the narrow Antarctic publication handling described below |

Hardcoded recognition needs both classification and lead injection: adding only `HARDCODED_PROTOCOLS` would not discover these LP tokens. Keep manager events out of global ERC-4626 discovery topics because the emitting manager address is not the vault identity. No new universal ABI probe is needed for two reviewed deployments.

## Protocol adapter and contextual reader

Create `constants.py`, `vault.py`, `historical.py`, `historical_context.py`, `tags.py`, `__init__.py` and a protocol README under the Antarctic package.

`AntarcticVault(VaultBase)` should provide ERC-20 identity, share/denomination token metadata, protocol name, product description, application link, strategy tags and `PriceSource.smart_contract_event`. Read total supply from the share token when requested for current metadata. Read the mutable `removeLiquidityCooldown()` through the verified manager ABI. Leave undocumented generic fee values and risk classification unknown (`None`) rather than asserting zero fees or a guaranteed return.

Isolated state methods such as `fetch_share_price()` and `fetch_nav()` must not invent a live price or silently call the API. Initially reject unsupported point-in-time valuation with `NotImplementedError`, following Rysk. `create_vault_scan_record()` already tolerates unsupported optional valuation reads. Metadata can therefore be registered before price context exists. Export caveats using the existing `fetch_scan_record_extra_data()` hook.

`AntarcticHistoricalReader(VaultHistoricalReader)` sets `uses_contextual_history=True`, returns an empty iterator from `construct_multicalls()`, and rejects `process_result()`. Its `fetch_contextual_historical_reads(start_block, end_block, step)` reads local prefetched context and yields only canonical subscription observations with their actual block and naive UTC event timestamp. The `step` parameter is ignored; no hourly grid or forward fill is written to raw Parquet.

Each historical row contains:

- `share_price`: the subscription execution ratio calculated using `Decimal` and token conversion helpers;
- `total_assets`: positive handler-reported event TVL in denomination-token units, clearly documented as reported pre-batch pool valuation; use `None` for a zero pre-batch TVL, retaining the exact zero in source context;
- `total_supply`: `None`, because exact pre-batch supply is not in the event and current supply must not be paired with historical TVL;
- generic fee/open-flow fields: `None` unless separately verified;
- common contextual frequency marker, supplied by the existing writer.

Using event TVL gives the existing metrics builder a historical pool-size value without fabricating `price * current_supply` or requiring an archive-state backfill. A live/current metadata TVL remains unknown until a separately labelled source is provided. The event price becomes observable only at its settlement block; never backdate it to the request.

Both investigated first deposits actually emit `TVL=0`: AMLP at block 292821850 and AHLP at block 357973705. Each deposits 10 USDT for 10 shares, so the price is 1 USDT per share even though the pre-batch pool is empty. Keep that initial price; do not turn the zero TVL into an unknown share price or invent a post-deposit TVL.

The common price filter currently uses a relative 10-basis-point threshold (`DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD = 0.001`). Antarctic's incremental changes can be smaller. Add a default-preserving `share_price_change_threshold: Percent` property to `VaultHistoricalReader`, returning the current default; override it to zero for Antarctic, and pass it to `is_share_price_almost_equal()` in the existing `is_unchanged()` helper. This is the sole new generic reader behaviour. Non-Antarctic readers keep the current threshold and filtering. Exact duplicate prices may still be suppressed in Parquet, while every source event and its freshness remains in context. Code inspection confirmed that `historical.py:672` applies this helper in the contextual loop, and `base.py:118` uses `<= threshold`. Keep calculated prices as `Decimal` through comparison; convert to float only in the existing `VaultHistoricalRead.export()` boundary.

For multiple canonical adds in one transaction, order by block, transaction position and log index. Retain every source event in context. Collapse to the last canonical add per vault/block for the price writer, because common raw rows cannot identify individual log locations; never average account ratios or attach post-batch TVL. Same-batch deposits share the pre-batch pricing formula, with integer rounding differences. A batch containing only redemptions does not emit a canonical price row.

## Hypersync collection and persistence

Add `fetch_and_store_antarctic_history()` with a synchronous public interface, using the required async Hypersync bridge internally. Use the configured client from `configure_hypersync_from_env()` and streams from `open_hypersync_stream()`. Query both manager addresses together, filtered to the two settlement topics; map each manager to its share-token vault identity. Do not use JSON-RPC `eth_getLogs`.

Use the shared `vault-historical-context.duckdb`, via `get_pipeline_data_dir()`, with Antarctic-owned tables. Persist event provenance (chain, vault, manager, block/hash, transaction hash/position, log index, timestamp, event kind, account, raw USDT, raw LP, raw TVL) and a separate successfully scanned range/cursor per manager. Keep hashes/source identities in the cache, without changing the common price Parquet schema.

Deduplicate by `(chain_id, manager_address, transaction_hash, log_index)` using staging and hash joins inside transactions. Do not create PRIMARY KEY/UNIQUE ART indexes. Set `wal_autocheckpoint = '1TB'`, and close all database connections through context managers/finally. Follow the Rysk context store's protocol-local layout but do not copy its per-row existence-query loop into a bulk collector.

Resolve event timestamps with `fetch_exact_block_timestamps_using_hypersync_cached()`, preserving `~/.tradingstrategy/block-timestamp/42161-timestamps.duckdb`. This existing cache-aware API is defined at `eth_defi/hypersync/hypersync_timestamp.py:809` and already used by the Rysk context collector. It avoids bootstrapping hundreds of millions of dense Arbitrum headers merely to timestamp sparse events.

Use inclusive start/exclusive end consistently. Bound reads by the requested scan end and the indexed safe head. Check source block hashes in a bounded replay overlap and replace orphaned Antarctic context rows transactionally. Replay from the last successfully scanned cursor with an overlap, rather than from the last priced event: this avoids rescanning weeks of empty ranges when settlement is infrequent. Advance a cursor only after a complete source range, timestamp resolution and database commit, including successful empty ranges. Failed/partial streams must not mark the range complete or advance the cursor.

Expose progress and log scan bounds, events fetched/inserted/replayed, canonical observations, latest source time and cursor. Retry warnings must be concise without tracebacks; final failures abort that protocol prefill. Do not convert missing credentials, rate limiting or incomplete context into an empty successful price scan.

## Scanner integration and continuation

Antarctic is a new supported perpetual DEX vault protocol in the normal `scripts/erc-4626/scan-vaults-all-chains.py` run. That entrypoint delegates to `eth_defi.vault.scan_all_chains.main()`, and `build_chain_configs()` already enables Arbitrum with `JSON_RPC_ARBITRUM`. Register both LP-token leads and classify them as `Antarctic`; let `scan_vaults_for_chain()` create their metadata and `scan_prices_for_chain()` select their contextual readers. This works on a new database without running an Antarctic-only research script. Existing installations additionally need the address-scoped initial migration described below.

Keep Antarctic under the existing EVM chain scheduler. `build_active_protocols()` schedules non-EVM/native sources such as Hypercore and Lighter; Antarctic does not need another native chain ID, parallel API collector or scheduler item. Add its supported product/source description to the production entrypoint docstring. Test `run_scan_tick()` with Arbitrum due and verify it reaches Antarctic prefill, price writing and post-processing through the same path used in production.

In `scan_prices_for_chain()`, identify Antarctic rows from features, assign the context path, require Hypersync, and prefill only the selected instantiated Antarctic vaults before calling `scan_historical_prices_to_parquet()`. Limit the prefill upper bound to the scan's own end; do not fetch unrelated protocols or chains. Add counts to the existing metrics result. Hardcoded classification plus a feature-level activity exemption ensures zero canonical ERC-4626 deposit counters do not suppress either product elsewhere in the pipeline.

Use existing strategy categories, with evidence for each product: both documented LP pools receive `StrategyTag.liquidity_provider`. AMLP's documented market-making role supports `StrategyTag.market_making` and its perpetual-futures exposure supports `StrategyTag.perpetual_futures`. Review AHLP separately using the strategy skill; a documented hedging role does not prove delta neutrality, arbitrage or its hedge instruments. Test the resulting product-specific tags in JSON. Generic `perp_*` exposure columns remain unknown unless backed by a separate measured source; settlement events do not give long/short positions, counts or concentration. Do not add Arbitrum to `PERP_DEX_NATIVE_CHAIN_IDS`, label all Arbitrum vaults as perpetual DEX products, or export absent exposures as zero. Supporting the exchange's vault prices does not imply a position-data integration.

Prefill must complete before the writer can delete/rewrite an overlap. The existing common scan start uses the chain-wide maximum saved reader block, while contextual readers do not maintain ordinary `BatchCallState`. A first historical Antarctic import therefore requires an address-scoped backfill of both vaults; adding them to a warm chain scan alone would skip their older history.

Normal incremental runs reuse the shared chain continuation point. When a replay discovers/replaces an event before that point, perform a bounded Antarctic-address-only rewrite from the earliest affected block before the normal mixed-chain scan. If replay removes the last canonical event and adds nothing, that bounded rewrite must still delete the orphaned Antarctic price row. Do not move the mixed-chain start backwards or erase/recreate other protocols' reader state. Test a quiet interval and contextual-only scans so empty results do not reset state or wipe old prices.

## Metadata, documentation and migration

Complete onboarding includes `eth_defi/data/vaults/metadata/antarctic.yaml`, feed YAML, risk/fee matrix entries named `Antarctic`, official source logos and the required formatted `light.png`, API stubs/index references and a vault documentation page/index entry. Add `README-Antarctic.md` to the repository README inventory in `CLAUDE.md` (`AGENTS.md` is its symlink). Use the repository's logo and strategy skills during implementation, reviewing both products separately. Unknown AHLP hedging details must remain unknown rather than receiving guessed strategy tags. Follow the metadata standard: describe the products and redemption constraints with primary-source links; keep scanner implementation details in technical documentation. Record the counterintuitive batch input polarity (`isMint == 0` means deposit, 1/2 select redemption routes) and the deliberate per-block price collapse in the protocol README; do not infer investor payment from an LP burn alone.

The [protocol README](../../eth_defi/erc_4626/vault_protocol/antarctic/README-Antarctic.md) is included with this planning update. It explains the exchange, both deployed products, verified onchain data, planned scanner/pipeline support and its limitations. Keep its status explicit until the adapter, registration, pipeline tests and migration have been implemented and checked; update it with real commands/results at that point.

Publication needs a small, explicit Antarctic branch in `calculate_vault_record()` in `eth_defi/research/vault_metrics.py`: existing legacy one-month/three-month fields fall back to zero when their structured period result is unavailable. For Antarctic, retain that structured error and export unavailable legacy returns/CAGR/risk metrics as null rather than measured zero. Keep other protocols' behaviour unchanged. Also verify that daily presentation resampling cannot turn a single real subscription into sufficient measured samples: use canonical source observations to determine sample bounds/counts and observation freshness for Antarctic. Preserve existing successful period calculations where genuine observations support them. Treat volatility/Sharpe computed from stepwise price updates as cadence-sensitive approximations in the public notes. Do not describe the sparse price-only series as a complete investor return including staking income.

Add `scripts/erc-4626/backfill-antarctic-vaults.py`, modelled on the address-scoped Rysk migration. It should default to `DRY_RUN=true`, target exactly the two chain-42161 vaults, refresh their metadata and prefill/replay their event histories, then call the existing writer with an explicit two-address deletion scope. Use environment variables, the parent Poetry environment, progress output, a pipeline lock and atomic state updates. A dry run uses copied data/temporary context and reports planned changes.

Preserve every unrelated vault row, reader-state entry, Parquet row and context table. A failed read/migration is a hard error, never an empty replacement. No price schema migration is proposed. Test against a copy of current production metadata/state/Parquet before production execution. Inspect `docker-compose.yml` before any later production maintenance and use the mounted production state; this plan does not authorise deployment or maintenance.

## Required integration tests

Repository inspection found the real-provider pattern in `tests/erc_4626/vault_protocol/test_rysk_historical_context_integration.py`, common writer/replay coverage in `tests/erc_4626/test_4626_historical_scan.py`, scanner orchestration patterns in `tests/vault/test_scan_all_chains_core3.py`, and JSON/category/sticky-state checks in `tests/erc_4626/test_vault_analysis_sticky_export.py`. There are no Antarctic implementation tests yet. The following tests are implementation deliverables, not claims that existing coverage already proves Antarctic support.

| Proposed test module | Integration boundary | Required evidence |
| --- | --- | --- |
| `tests/erc_4626/vault_protocol/test_antarctic_historical_context_integration.py` | Real Arbitrum Hypersync → verified event decoder → timestamp cache → file-backed context → reader | Fixed settlement ranges for both products, exact source amounts/identity/time, Decimal price, idempotent replay and a quiet range |
| `tests/vault/test_scan_all_chains_antarctic.py` | All-chain scheduling → hardcoded Arbitrum leads → adapter factory → selected-vault prefill → common writer | Both products participate in normal Arbitrum scanning; prefill precedes writing; address filters, missing credentials and failed prefill are handled correctly |
| `tests/erc_4626/vault_protocol/test_antarctic_pipeline_integration.py` | Adapter-generated metadata plus event context → real raw Parquet writer → real cleaner → real vault JSON exporter | Both AMLP and AHLP survive every stage, sparse prices and freshness stay correct, unknown values remain unknown and unrelated data survives |

### Live Hypersync coverage

Create clients with `configure_hypersync_from_env()` and consume logs through `open_hypersync_stream()`; resolve exact timestamps with the cached helper. Guard the live test with `HYPERSYNC_API_KEY` and `JSON_RPC_ARBITRUM`, following Rysk. Use small inclusive-start/exclusive-end block ranges around the reviewed AMLP transaction `0x375492548824f0e2590f176e517f33f6848339b835c68eac57dbb3410353695c` and AHLP transaction `0xfd8f513f15d1b60b0b64b58b72cb086b6708311e2a3081d8b3a5988483397e6c`; commit their exact blocks, hashes, log indices, timestamps and raw amounts as independently checked assertions. Compare Decimal ratios before Parquet float conversion. Include at least one real removal to prove it is persisted as diagnostics without updating the canonical price.

Use temporary context and timestamp-cache directories, close every DuckDB connection and replay the same source range. Assert duplicate ingestion does not multiply observations; a verified event-free range advances the successful cursor without creating prices. The test must call the implemented collector/reader end to end, rather than copying the investigation script or mocking Hypersync. Prefer CI execution when credentials are securely available; apply `skip_hypersync_scan_on_ci` only if the test becomes a multi-minute scan, following repository guidance. If CI cannot run it, the exact focused pytest command below is the guarded manual integration check. Record pass/fail, date and redacted provider in the eventual PR comment. A credential-based skip is not a passing provider test.

### Scanner and publication coverage

The scanner test should exercise `run_scan_tick()` and the real Arbitrum `scan_prices_for_chain()` wiring with temporary paths and bounded ranges. Replace remote source transport with recorded Hypersync responses for deterministic CI coverage, but retain the real decoder, context store, adapter factory, historical reader and writer. Test new-database discovery, warm-chain initial import, selected-address prefill, quiet continuation and failure before writing. Check no Antarctic native/API job is required, both LP tokens appear once, managers are not vaults, and scanning an unrelated Arbitrum vault still works.

Build pipeline fixtures from verified events for both products, with enough genuine observations and reported TVL to satisfy existing publication eligibility; control the clock to the fixture period rather than weakening global thresholds. Before building the fixture, identify the actual cleaner/exporter clock reads and verify the clock override reaches their freshness and eligibility checks. If a small clock-injection seam is needed, keep it behaviour-preserving and test-only in use; do not bypass eligibility or replace metrics calculation. Create metadata using the real `create_vault_scan_record()`/`VaultDatabase` path and production classification. Use the normal scanner/writer to generate `vault-prices-1h.parquet`, call `post_processing.clean_prices()` (which delegates to `generate_cleaned_vault_datasets()`), then call `top_vaults_json.main()` with explicit temporary `data_dir`, metadata, cleaned Parquet and output paths. This is the same JSON builder called by `post_processing.export_top_vaults_json()`; stop before its R2 upload. Keep cleaning, metrics calculation and JSON building real; mock only transport and unrelated enrichment. Do not handcraft final Parquet rows or JSON records as a substitute for this integration test.

Read the files back from disk and assert:

- Raw `vault-prices-1h.parquet`: exactly the two expected chain/address identities plus the unrelated sentinel; actual canonical event blocks/timestamps, correct USDT-per-share values, positive reported pre-batch TVL or null bootstrap assets, null historical supply/unknown fees, unchanged common schema and null unmeasured perp exposure columns. Only qualifying add observations enter the series; sub-10-basis-point changes survive, exact repeats remain in source context, and multiple canonical events in one block collapse deterministically.
- Cleaned `cleaned-vault-prices-1h.parquet`: both products survive activity/TVL cleaning, prices and observation boundaries remain consistent with raw data, and Antarctic handling does not alter the sentinel. Check the existing daily derivative separately: any forward-filled presentation values must not count as additional source observations.
- Published `top_vaults_by_chain.json`: both IDs (`42161-` plus lowercase LP address), protocol `Antarctic`/slug `antarctic`, correct AMLP/AHLP names, application links, denomination, source classification and reviewed strategy tags. Validate the existing strict JSON contract: unknown values become JSON null and no NaN/Infinity leaks. Fees and unsupported transaction capabilities must not become fabricated zero/true values. Check actual exported source/freshness fields and metadata caveats rather than inventing new public keys.
- Publication periods: `last_updated_at`, `last_updated_block`, last price and measured sample bounds/counts come from canonical source observations. One genuine observation with many daily presentation rows still yields unavailable structured periods and null unavailable legacy metrics. A quiet scan neither updates those times nor generates a current price. Stale products follow existing sticky export/category rules with their original observation times, instead of becoming fresh merely because scanning ran.
- Replay/preservation: repeat the scanner and export against the same temporary files, then a quiet interval. No duplicate prices, wiped history or advanced source freshness; unrelated metadata, Parquet rows, reader-state entries and context tables remain intact. In the failure test, compare saved files/cursors before and after prefill failure, allowing normal failure diagnostics but no destructive price rewrite.

Run the same local pipeline stages once using observations fetched by the real Hypersync collector for both products, reusing the guarded live-test setup. This connects successful provider reads to the actual Parquet and JSON outputs; fixture-only pipeline tests and mocked scanner tests do not replace it. Select a bounded historical window with sufficient real observations for publication, and freeze the export clock to that window. Keep all paths under `tmp_path` and disable uploads, social/enrichment scans and production state access.

After implementation, run focused commands with the configured environment, for example:

```bash
source .local-test.env && PYTHONPATH="$PWD" poetry run pytest tests/erc_4626/vault_protocol/test_antarctic_historical_context_integration.py tests/erc_4626/vault_protocol/test_antarctic_pipeline_integration.py tests/vault/test_scan_all_chains_antarctic.py -q
```

Allow at least three minutes per command and split provider/pipeline modules if needed. These paths are now implemented; final verification is recorded below. Do not claim acceptance based on a skipped live test.

## Focused checks and acceptance criteria

1. Classification and discovery: both exact LP addresses classify on Arbitrum, reject another chain and unknown addresses, seed leads with verified deployment dates, instantiate `AntarcticVault`, and bypass generic activity filters. Manager/staking addresses must not become extra vaults.
2. Adapter: token/manager identity, denomination, decimals, links, cooldown, source classification and unknown fee fields; public deposit/redemption capability stays `None`. Current metadata export must succeed without context.
3. Decoding: real event fixtures for both products and kinds; indexed-field layout, exact scaling and positive subscription denominator; requests/cancellations/direct token supply events cannot produce prices. The first actual 10-USDT/10-share deposits must emit price 1 with unknown historical assets and preserve raw TVL zero. A redemption ratio differing from a deposit price must not alter the canonical series.
4. Context: idempotent replay, multiple events per transaction/block, deterministic ordering, successful empty-range cursor advance, interruption before commit, reorg replacement/removal, foreign tables preserved, wide integers and closed file-backed DuckDB. Exercise a production-scale duplicate-ingestion path without ART constraints.
5. Common writer: contextual-only and mixed ERC-4626/Antarctic scans; sub-10-basis-point price changes survive; other readers retain their threshold; no hourly synthetic samples; no history before the first add; duplicate-price source provenance remains available; no accidental pairing with current supply.
6. Continuation/migration: first import into a warm chain, quiet repeat run, same-block replay, late overlap repair including removed-only events, missing credentials, failed prefill, and preservation of unrelated prices/state. Targeted migration must pass the explicit address scope to deletion.
7. Publication: feed the two sparse histories through existing cleaning, lifetime metrics and metadata export. Verify reported TVL remains labelled, low activity does not drop them, `last_updated_at`/sample bounds refer to observations rather than scan time, and the Antarctic legacy metrics null handling agrees with unavailable structured periods. Test a one-observation period surrounded by forward-filled presentation rows: it must remain unavailable. Existing downstream daily forward fills are an analytical presentation of last-known observations, not new measurements; verify they do not extend raw history to the current scan time or present fabricated freshness.
8. Real integration: add credential-guarded Hypersync tests over small fixed ranges containing the reviewed AMLP and AHLP settlement transactions. Assert exact block/timestamp/raw amount/Decimal prices and replay idempotence. Record command, date, pass/fail and redacted provider in any eventual PR comment. The investigation script is evidence, not a substitute for testing the implementation.
9. Fork metadata checks use the shared `anvil_fork_pool`, `ARBITRUM_MIDNIGHT_BLOCK`, matching `xdist_group`, and the canonical instructions in `eth_defi/testing/anvil_fork_pool.py`. No private/latest Anvil forks.

Run only the focused new tests and affected existing contextual-history/classification/migration tests. Prefix pytest with `source .local-test.env && PYTHONPATH="$PWD" poetry run pytest ...`, allow at least three minutes per command, and format touched Python with `poetry run ruff format`. The `PYTHONPATH` prefix deliberately forces imports from this worktree while using the parent's Poetry environment, as prescribed in the repository's agent troubleshooting guidance. Validate metadata and documentation source references; do not build Sphinx locally. Add a dated changelog entry when implementing the feature; push/open a PR only on request.

## Implementation order

1. Verify deployment boundaries and store verified ABIs with canonical-source README; create the two-product registry.
2. Add feature/name/classification/lead registration and the read-only adapter, including both strategy-tag decisions.
3. Implement and test event decoding, context storage, empty-range cursors and replay/reorg handling.
4. Add the contextual reader, minimal threshold hook and all-chain Arbitrum scanner prefill; implement the scanner and real Hypersync integration tests.
5. Finish protocol metadata/logos/feed/API docs, update the existing Antarctic README and add the scoped dry-run migration.
6. Implement and run the raw/cleaned Parquet and vault JSON integration tests for both products, including the live-source pipeline check. Run focused regressions, rehearse against copied production data, and record review/test evidence.

## Review

Kimi K3 reviewed the complete initial plan and targeted repository/verified-contract excerpts on 2026-09-30 using Kimi Code 2.0.2, model alias `kimi-code/k3`. The reviewer returned **approve with minor revisions**, with **no blocking findings**. This was an inline review without tools; repository assumptions were independently checked by the primary agent.

- M1: verified the common contextual branch applies `is_unchanged()` at `historical.py:672`; the threshold hook is needed.
- M2: verified the exact-block cached timestamp helper exists at `hypersync_timestamp.py:809` and is used by Rysk; retain the sparse cache-aware API.
- M3: verified comparison uses `<=`, so threshold zero suppresses exact Decimal duplicates. A direct check returned true for identical prices and false for a one-in-the-last-decimal change. Float conversion remains at Parquet export.
- L1: verified both actual first subscription events have zero TVL; keep price 1, export assets as unknown and retain raw TVL in context.
- L2/L3: added the README inventory entry, input-polarity note and per-block-collapse caveat to the implementation requirements.
- L4: retained and justified the worktree import prefix, which is explicitly supported by repository troubleshooting guidance.

Primary-agent inspection additionally identified the legacy zero-metric fallback and made Antarctic-specific null handling explicit. Residual limitations are handler-reported valuation, sparse subscription-only observations, omitted staking income and loss of intra-block price detail in the common writer. This review preceded implementation and authorised no production rollout.

Kimi K3 then reviewed the updated complete plan in a final blocking-only pass and returned **no blocking findings**. Both review processes completed successfully. Raw review outputs are available locally at `/tmp/antarctic-kimi-k3-review.jsonl` and `/tmp/antarctic-kimi-k3-final-review.jsonl`.

Kimi K3 also reviewed the complete expanded plan and written README on 2026-09-30, including all four follow-up requirements, and returned **no blocking findings**. The CLI completed successfully with a final assistant verdict; the review used the complete current documents supplied inline, without tools. Its output is `/tmp/antarctic-kimi-k3-update-review.jsonl`. The reviewer highlighted fixture-clock/eligibility control and the canonical-observation metric dependency as implementation risks; the clock pre-check is now explicit above. Real-provider execution remains a required guarded check, with recorded-response pipeline coverage for deterministic CI. At this review stage, the work was documentation only; implementation results are recorded below.

## Implementation outcome

Implemented the fixed two-vault Arbitrum registry, verified manager ABIs, chain-scoped classification, discovery leads, read-only adapter, exact source context, sparse canonical subscription reader, default-preserving threshold hook, all-chain prefill and scoped repair. Added metadata, strategy decisions, logo, feed, API/narrative documentation and `README-Antarctic.md`. Unknown metrics use nulls and measured observation counts are preserved through publication.

Implementation refinements:

- The source collector queries selected managers separately, chunked into 10-million-block ranges, so each independent cursor can be completed or retried without broadening scope. Global block log indexes determine ordering; manager provenance is derived from the reviewed LP registry.
- Associated event block headers seed the existing per-chain timestamp cache before the exact cache-aware helper verifies coverage. The initial one-request-per-observation approach exceeded the local token's 30-RPM quota; the final implementation reuses headers and was verified with `HYPERSYNC_RPM=20`.
- Initial history automatically bootstraps each selected pool from its verified manager creation block and writes repairs with explicit LP-address scope. This handles warm chain state without requiring an operator to reset shared state. The separate default-dry-run migration still provides cached metadata repair and full scoped price rewriting.
- Antarctic daily preparation retains canonical event timestamps and counts, leaving curve regularisation to period analytics. This prevents daily forward fills becoming measured samples. Existing protocols retain their existing behaviour and 10-basis-point threshold.
- Reorg coverage is bounded to 512 blocks; deeper historical repairs need operator backfill. Public asynchronous investor transaction flows remain unsupported.

Local checks on 2026-09-30 passed **135 tests**, including the live provider and live-source JSON/Parquet checks. Exact command:

```shell
source .local-test.env && TMPDIR=/tmp/mikko-antarctic-pytest HYPERSYNC_RPM=20 PYTHONPATH="$PWD" timeout 180 poetry run pytest tests/erc_4626/vault_protocol/test_antarctic*.py tests/vault/test_scan_all_chains_antarctic.py tests/erc_4626/vault_protocol/test_rysk.py tests/erc_4626/vault_protocol/test_rysk_migration.py tests/erc_4626/vault_protocol/test_flying_tulip_historical_context.py tests/erc_4626/test_vault_analysis_sticky_export.py tests/vault/test_scan_all_chains_config.py tests/vault/test_historical_format.py tests/research/test_vault_metrics.py -q --tb=short
```

Redacted providers: authenticated Envio Arbitrum Hypersync and the supplied Arbitrum RPC configuration (Goldsky/dRPC/Alchemy). Migration safety was rehearsed on copied integration fixtures with unrelated metadata/price/state/context sentinels; application against production data remains an operator rollout step. No Parquet schema migration is introduced. Python formatting, targeted lint and metadata/logo export checks are performed without building Sphinx. At that stage, Kimi K3 had reviewed only the plan. Subsequent implementation review and PR results are recorded below.


## Manual tooling and chart publication

On 2026-09-30, added `backfill-antarctic-vaults.py` as the operator entrypoint, calling the shared migration implementation. Added `plot-antarctic-vaults.py` with isolated Hypersync context and token/timestamp caches, PNG/HTML plots, CSV observations and a source summary. Committed a live-source chart snapshot through block `510301932`. All 18 focused Antarctic tests passed locally, including authenticated Hypersync checks, migration preservation and chart-source assertions. The exact command was `source .local-test.env && TMPDIR=/tmp/mikko-antarctic-pytest HYPERSYNC_RPM=20 PYTHONPATH="$PWD" timeout 180 poetry run pytest tests/erc_4626/vault_protocol/test_antarctic*.py tests/vault/test_scan_all_chains_antarctic.py -q --tb=short`.


The PR branch was rebased onto current `master` on 2026-09-30. Antarctic rows are split before the newer bulk daily-return fast path, preserving real event counts while other vaults keep their optimised path. The final focused suite passed **172 tests** including live Hypersync, the complete pipeline and existing price-freshness regressions. Ruff and formatting checks passed. Source charts were rendered from the successful full authenticated read; a later cosmetic rerender encountered a transient Hypersync DNS failure and safely aborted before source replacement. The retained complete CSV observations were used to render the final PNGs without another provider scan.


## Simplification and skill audit

On 2026-09-30, removed the duplicate migration entrypoint, replaced recursive/per-vault Antarctic return special cases with one grouped observation path, and centralised unavailable metric defaults. Curator metadata now aliases the existing Antarctic protocol feeder and logo, with source-linked organisation descriptions. Both reviewed pools receive `liquidity_provider`; AMLP additionally receives `market_making` and `perpetual_futures`. AHLP’s specific hedge instruments remain unspecified. Tests check exact exported tags and curator identity for both products, immutable tag lookup and missing-address behaviour.

Corrected research notes that still recommended an API reader, daily-resampling descriptions and the current denomination symbol (`USD₮0`). The protocol, curator and strategy skills were checked against both deployments and their required production strategy-maintenance instructions are included in the PR migration comment. The official Medium feed was manually checked: HTTP 200, valid RSS and ten entries.

The focused Antarctic/curator/metrics/freshness suite passed **178 tests**, including the actual authenticated Hypersync provider and both vaults through Parquet/JSON. CI exposed a deterministic shared-scanner regression in both YieldBasis withholding tests: Antarctic selection assumed every adapter had a `features` attribute. Both failures were reproduced locally and fixed by selecting `AntarcticVault` instances. The shared scanner regression suite then passed **19 tests**; the final tag/pipeline/migration check passed **4 tests**. These are distinct focused runs with overlapping tests, not an additive total.

Kimi Code 2.0.2 performed a grounded read-only implementation and operator-command review using `kimi-code/k3-max`, with Thinking enabled, `default_effort=max` and `KIMI_MODEL_THINKING_EFFORT=max`. It inspected the actual non-empty diff, source, neighbouring pipeline code, tests and all three skills. The initial invocation reached its 15-minute deadline before a verdict; the same session was resumed for the remaining checks and completed successfully with **approve — no blocking issues**. Raw logs are `/tmp/antarctic-kimi-k3-code-review.jsonl` and `/tmp/antarctic-kimi-k3-code-review-final.jsonl`.

The reviewer’s display-name coupling observation was addressed by keying null metric defaults to the reviewed chain/address pairs. Non-blocking observations retained: contextual readers contribute to the live audit’s unknown-conversion count in mixed scans, the confirmed-head clamp applies to the mixed Arbitrum batch, and organisation-name curator matching follows existing repository practice. Handler-reported valuation, sparse subscriptions, omitted staking rewards and 512-block replay remain documented limitations. No production state was changed.
