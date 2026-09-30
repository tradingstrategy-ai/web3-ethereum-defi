# Antarctic vaults

Implemented on 2026-09-30: read-only AMLP and AHLP vault support on Arbitrum, with Hypersync settlement history, normal all-chain scanner registration, and raw/cleaned Parquet and vault JSON integration. Production deployment is a separate operator action.

## What Antarctic is

[Antarctic Exchange](https://www.antarctic.exchange/) is a perpetual futures exchange. Its [hybrid liquidity model](https://docs.antarctic.exchange/technical-framework/hybird-lp-model) combines smart-contract deposits and redemptions with offchain components. LP settlement can be read from Arbitrum events, but those events do not reconstruct the exchange's trading book or independently establish portfolio NAV.

The exchange documents [two liquidity products](https://docs.antarctic.exchange/antarctic-overview/liquidity-provider-protection): AMLP supplies market-making liquidity, while AHLP supplies hedging liquidity. The [FAQ](https://docs.antarctic.exchange/faq) describes AMLP as a counterparty to traders, so trader gains and losses affect its economics. The reviewed sources do not establish AHLP's precise hedge instruments or justify assuming a delta-neutral strategy. Neither product's share-price curve guarantees positive returns.

## Reviewed deployments

The initial integration covers exactly these two products on Arbitrum One, chain ID `42161`. The LP token is the vault identity; the manager emits settlement events.

| Product | Share token / vault address | Settlement manager | Application |
| --- | --- | --- | --- |
| AMLP | `0x152f5E6142db867f905a68617dBb6408D7993a4b` | `0x98a6aEE58699e4f4E13D8d8d0800e4e9cbBcf8dD` | [AMLP pool](https://www.antarctic.exchange/lp/amlp) |
| AHLP | `0x5fd22dA8315992dbbD82d5AC1087803ff134C2c4` | `0xc5F9d4b9f68CAAA869317Baa09a233b22940bd9f` | [AHLP pool](https://www.antarctic.exchange/lp/ahlp) |

Both share tokens use 18 decimals. Their denomination is Arbitrum USDT, `0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9`, with six decimals. Treat prices and TVL as USDT-denominated values; a USDT/USD conversion is separate.

The verified [AMLP manager source](https://arbiscan.io/address/0x98a6aEE58699e4f4E13D8d8d0800e4e9cbBcf8dD#code) and [AHLP manager source](https://arbiscan.io/address/0xc5F9d4b9f68CAAA869317Baa09a233b22940bd9f#code) provide the settlement interfaces. The LP tokens are not ERC-4626 vaults: investigated `asset()` and `totalAssets()` calls reverted. Their ERC-20 identity and supply remain readable.

## What can be read onchain

The managers emit `AddLiquidity` and `RemoveLiquidity`, containing an account, raw USDT amount, raw LP amount and handler-reported TVL. Logs also identify the emitting manager, transaction, block and log index; Hypersync supplies the corresponding block timestamps. Decode the verified interfaces: the amount parameter is named `amlpAmount` or `ahlpAmount`, while the event types are otherwise shared.

For a settled subscription, calculate:

```text
share price = USDT amount converted using denomination-token decimals
              / LP amount converted using share-token decimals
```

This is a subscription execution price in USDT per share. It becomes observable at the settlement block. An authorised handler supplies the batch valuation inputs; the value is not an independently calculated NAV. Use exact raw integers and Decimal arithmetic until the common Parquet export converts the result to float.

The canonical price series uses **AddLiquidity only**. Persist removals as redemption diagnostics, but keep them out of that series: the manager accepts a handler-supplied redemption amount without enforcing the subscription TVL/supply formula. Combining these ratios could introduce changes caused by settlement conventions, fees or haircuts.

Additional interpretation rules:

- Request and cancellation events describe workflow, not completed price observations. ERC-20 transfers, mints and burns have no valuation by themselves.
- In the reviewed batch implementation, `isMint == 0` selects deposits; `1` and `2` select redemption routes. Do not infer the operation from the variable name.
- The first observed subscription for each product exchanged 10 USDT for 10 shares with reported TVL zero. Preserve price 1 and raw TVL zero, but export historical assets as unknown for those bootstrap observations.
- Positive event TVL is a handler-reported pre-batch value. Do not divide it by today's token supply to reconstruct historical price; historical supply remains unknown in price rows.
- Separate staking distributions are excluded from this price series. Long/short exposure, open positions, concentration, fee breakdowns and independently calculated trading PnL are not supplied by the settlement events.

On 2026-09-30, a Hypersync investigation through block `510301932` found:

| Product | Add events | Remove events | Settlement transactions | Transactions in preceding 30 days | Transactions in preceding 7 days |
| --- | ---: | ---: | ---: | ---: | ---: |
| AMLP | 635 | 297 | 733 | 2 | 0 |
| AHLP | 34 | 14 | 48 | 2 | 0 |

A batch can contain several events. These are historical counts at the investigation cutoff, not a promised publication schedule. The latest observed subscriptions were 2026-09-16 for AMLP and 2026-09-10 for AHLP. Sparse settlements mean a lack of new observations, not proof that economic value stayed constant.

## Repository support

`AntarcticVault` implements `VaultBase`, alongside the repository's other bespoke DeFi pool adapters. It recognises only the two reviewed chain/address pairs, seeds their discovery leads and exposes protocol `Antarctic`, product links, denomination, source caveats and independently reviewed strategy tags.

The Antarctic contextual historical reader consumes local settlement context. The scanner prefetches manager logs with `configure_hypersync_from_env()` and `open_hypersync_stream()`, then resolves exact event timestamps through the cache-aware Hypersync helper. It stores raw logs, canonical subscriptions, redemption diagnostics and successful scan cursors in protocol-specific tables in the shared historical context DuckDB. The store preserves unrelated tables and uses exact storage for uint256 amounts, application-level deduplication and transactional reorg repair.

Support runs through the existing [all-chain scanner](../../../../scripts/erc-4626/scan-vaults-all-chains.py) and its normal Arbitrum schedule. The scanner already enables Arbitrum via `JSON_RPC_ARBITRUM`; Antarctic feature/lead registration and selected-vault context prefill run before the common price writer. It does not need a separate native-chain API collection job. Historical event discovery uses Hypersync, with `HYPERSYNC_API_KEY` configured through the existing environment helpers.

The pipeline is:

```text
Arbitrum manager events → Hypersync → Antarctic historical context
    → Antarctic reader + common writer → vault-prices-1h.parquet
    → existing cleaner → cleaned-vault-prices-1h.parquet
    → existing metrics/JSON builder → top_vaults_by_chain.json
```

Despite the `1h` filename, raw Antarctic prices contain only qualifying event observations. Preserve changes below the common 10-basis-point suppression threshold with an Antarctic-specific zero threshold. Exact repeated prices may be omitted from price Parquet while their source logs remain in context. The common writer has one price per vault/block, so the last canonical event in a block wins; retain every source event in context for auditability.

Use the existing `PriceSource.smart_contract_event` classification. Downstream daily resampling may present the last observed value, but it must not create new measured samples, refresh source timestamps or make an insufficient return period appear available. Unavailable Antarctic period returns and related legacy metrics should be null rather than fabricated zero. Generic perp exposure fields stay unmeasured; onchain LP price support does not imply position visibility.

## Scope and operational limits

The initial adapter is read-only. Public deposit/redemption transaction construction stays unsupported. Investigated managers reported a mutable seven-day removal cooldown; refresh the verified getter for metadata rather than promising immediate redemption or hardcoding a permanent duration. Unknown management/performance fees and technical risk classification remain unknown.

There is no Antarctic API valuation fallback. API chart values can be researched separately, but they must not silently extend the onchain series or turn cumulative returns into annualised APY. Handler-reported values, sparse observations and omitted staking rewards must be visible in the integration notes.

Each selected pool has its own completed-range cursor and bounded overlap replay. On first import, the scanner automatically prefills that pool from its verified manager creation block and repairs only its price rows, even when the shared Arbitrum continuation point is already warm. Successful quiet ranges advance source cursors without inventing prices. Source reads must complete before replacement; pending price repairs survive writer failures. Reorg replay covers the last 512 blocks, so a deeper historical repair requires an explicit operator backfill.

The scoped migration below refreshes existing cached metadata and rehearses a full two-address rewrite. It defaults to dry run and preserves unrelated metadata, prices, context tables and reader state. No common Parquet schema change is required.

## Configuration and migration

Normal scanning requires `JSON_RPC_ARBITRUM`, the existing configured Hypersync endpoint and `HYPERSYNC_API_KEY`. Use `HYPERSYNC_RPM` to match the token's quota; local verification used `20` requests per minute. Manager/token identity, the mutable cooldown and minimum deposit are read using the committed verified ABIs. Historical prices require indexed events rather than archive contract state.

Creation boundaries are recorded in `constants.py`:

| Product | LP creation block | Manager creation block | LP creation time (UTC) |
| --- | ---: | ---: | --- |
| AMLP | 291176730 | 291176746 | 2025-01-02 10:17:16 |
| AHLP | 357654635 | 357654676 | 2025-07-14 15:40:38 |

Rehearse against an existing pipeline directory containing `vault-metadata-db.pickle`. The default `PIPELINE_DATA_DIR` is `~/.tradingstrategy/vaults`. Provide the RPC and Hypersync environment through the operator's existing configuration:

```shell
DRY_RUN=true MAX_WORKERS=4 HYPERSYNC_RPM=20 poetry run python scripts/erc-4626/backfill-antarctic-vaults.py
```

Dry run takes the pipeline writer lock, copies existing prices and context (including any WAL) into a temporary directory on the same volume, and uses private token and timestamp caches. It checks available disk space and exercises the actual decoder, context store, common price writer and metadata classifier. Original pipeline files are unchanged. `DRY_RUN=false` explicitly applies the same two-address operation; reader state is never reset. Unknown dry-run values are rejected.

Before applying in production, inspect `docker-compose.yml`, stop the persistent scanner, retain backups and use the normal mounted pipeline directory. The one-shot service shares persistent state with the looped service: retain the standard home and mounts. The migration must be the only writer to this context while copying or applying. Context acknowledgement follows atomic price writing, and metadata replacement follows successful source reads. Refresh cleaned prices and JSON with the normal post-processing cycle after applying. The older `migrate-antarctic-vaults.py` entrypoint calls the same implementation. Production application has not been performed by this implementation task.

## Integration coverage and local results

On 2026-09-30, the focused suite passed **172 tests**, including:

- Real authenticated Arbitrum Hypersync reads for both reviewed settlement transactions, exact raw amounts, timestamps, Decimal prices and replay.
- A live-source two-product run through the common price writer, cleaner and actual vault JSON builder; every output path is explicitly isolated.
- Recorded-response all-chain Arbitrum scheduler coverage, quiet continuation, missing-source failure and recovery after an atomic price writer failure.
- Raw/cleaned Parquet and strict JSON publication, sparse observations, null unknowns, one-observation metrics and changes below 10 basis points.
- File-backed context replay, reorg replacement/removal, partial-stream failure, wide integers, 10,000-row duplicate ingestion and foreign-table preservation.
- Dry-run byte preservation and scoped migration application on copied pipeline fixtures, including recovery of old source rows behind an advanced cursor.
- Shared `anvil_fork_pool` metadata checks at `ARBITRUM_MIDNIGHT_BLOCK` for both vaults, plus existing Rysk, Flying Tulip, scanner configuration, vault metrics, price freshness and sticky-export regressions.

These are local runs using authenticated Envio Arbitrum Hypersync and configured Arbitrum RPC providers (Goldsky/dRPC/Alchemy); credentials are never recorded in fixtures. Live checks are automatically guarded by the required environment variables. Their bounded source ranges take seconds and do not use the CI skip intended for multi-minute discovery scans.

To run the Antarctic checks locally:

```shell
source .local-test.env && PYTHONPATH="$PWD" HYPERSYNC_RPM=20 timeout 180 poetry run pytest tests/erc_4626/vault_protocol/test_antarctic*.py tests/vault/test_scan_all_chains_antarctic.py -q
```

Set `TMPDIR` to an owned temporary directory if the shared token-cache directory belongs to another local user. Tests stop before R2 uploads. Record the exact live-test command, date and redacted provider in the eventual pull request comment; production rollout remains a separate operator action.

The [integration plan](../../../../docs/protocol-research/antarctic-vault-integration-plan.md) records the design and prior Kimi K3 plan review. The [research record](../../../../docs/protocol-research/antarctic-0x152f5e6142db867f905a68617dbb6408d7993a4b.md) contains source transactions, API findings and detailed event-frequency evidence.

## Render equity and TVL charts

The manual chart script fetches full indexed history into its own directory and renders both products with the production subscription-price reader:

```shell
source .local-test.env && HYPERSYNC_RPM=20 poetry run python scripts/erc-4626/plot-antarctic-vaults.py
```

`OUTPUT_DIR` defaults to `~/.cache/antarctic-charts` and must be outside the shared pipeline directory. It contains a private context, token and timestamp caches, two PNG charts, interactive HTML copies, observed CSV rows and `summary.json`. `END_BLOCK` optionally selects an exclusive historical cutoff; the default is the confirmed RPC head. Plotly/Kaleido needs Chrome; set `BROWSER_PATH` to an existing installation, or install Chrome with `poetry run plotly_get_chrome`.

Equity starts at 100 at each vault's first subscription price. It excludes staking distributions and investor cash flows. TVL is the positive pre-batch value reported in subscription events; bootstrap zero remains unknown. Markers identify actual observations, connecting lines are illustrative, and plots stop at the last subscription rather than the scan date.

The [committed chart snapshot](../../../../docs/protocol-research/antarctic-charts/README.md) was fetched from live Hypersync on 2026-09-30 through block `510301932`. AMLP has 522 canonical subscription blocks and a share-price return of −10.39%; AHLP has 34 blocks and +1.98%. The respective last reported TVLs are 6.25 million and 3.18 million USDT. These are observations at different final settlement dates, not a current valuation.
