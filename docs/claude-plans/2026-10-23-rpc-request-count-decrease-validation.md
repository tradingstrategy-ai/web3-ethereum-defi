# RPC request count decrease validation

Date: 2026-10-23 UTC. These are local manual checks, not CI or production measurements. Implementation follows the [trimmed plan](2026-10-23-rpc-request-count-decrease-plan.md). No production scanner deployment, catalogue migration or price backfill was performed.

## Focused regression checks

Python 3.14.6 with the existing parent Poetry environment, importing this worktree. The following passed **132 tests**, including request budgets, reader scheduling, metadata recovery, HyperEVM isolation, timestamps, freshness and existing-row preservation:

```shell
source .local-test.env
timeout 180 poetry run pytest tests/event_reader/test_multicall_batcher.py tests/erc_4626/test_vault_reader_state.py tests/vault/test_rpc_batch.py tests/erc_4626/test_rpc_metadata_recovery.py tests/event_reader/test_hyperevm_multicall_greylist.py tests/event_reader/test_multicall_timestamp_rpc_usage.py tests/vault/test_price_freshness.py tests/vault/test_historical_format.py -q --tb=short
```

Formatting and whitespace checks passed. No Sphinx build was run. Recording-transport tests verify the 1,000-subcall budget of 25 requests at size 40 versus 10 at size 100, worker reuse, explicit lower limits and cold-cache warmup limits.

## Real-provider metadata parity

```shell
source .local-test.env
PYTHONPATH="$PWD" RPC_PARITY_CHECK=true MULTICALL_BATCH_SIZE=100 timeout 180 poetry run python scripts/erc-4626/check-rpc-batch-parity.py
```

All six targets passed fixed-block comparisons of NAV, shares, fees, flags, denomination/share identity, available liquidity and utilisation. Direct and prepared paths used independent temporary token caches. Counts include preparation, warmup and residual direct reads; optional direct probes can record served reverts without failing the metadata result.

| Chain | Source block | Targets | Direct attempts | Prepared attempts |
|---|---:|---:|---:|---:|
| Ethereum | 26,255,058 | Generic ERC-4626, Morpho v1, IPOR | 56 | 25 |
| Arbitrum | 517,522,126 | Morpho v2, IPOR | 36 | 20 |
| Base | 52,983,061 | IPOR | 26 | 23 |

Redacted provider domains were `edge.goldsky.com` for Ethereum/Arbitrum and `lb.drpc.org` for Base. These small cohort results do not establish a fleet-wide saving or validate every configured fallback provider.

## Bounded Arbitrum historical scan

```shell
source .local-test.env
PYTHONPATH="$PWD" RPC_SCAN_CHECK=true START_BLOCK=517392019 END_BLOCK=517521619 MULTICALL_BATCH_SIZE=100 MAX_WORKERS=2 timeout 180 poetry run python scripts/erc-4626/check-rpc-batch-parity.py
PYTHONPATH="$PWD" RPC_SCAN_CHECK=true START_BLOCK=517392019 END_BLOCK=517521619 MULTICALL_BATCH_SIZE=40 MAX_WORKERS=2 timeout 180 poetry run python scripts/erc-4626/check-rpc-batch-parity.py
```

Both commands passed. The end bound is exclusive; the grid contains nine blocks at step 14,400. The real stateful consumer and Parquet writer used temporary output, token cache, timestamp cache and reader state.

| Vault | Retained source block | Share price | Total assets |
|---|---:|---:|---:|
| Morpho v2 `0xbeefff13dd098de415e07f033dae65205b31a894` | 517,392,019 | 1.054326 | 172.04274 |
| IPOR `0xa91267a25939b2b0f046013fbf9597008f7f014b` | 517,392,019 | 1.063584 | 426.62874 |

Rows, numeric values and reader progress matched exactly between sizes. Independent same-block `totalAssets()` and `convertToAssets(one share)` reads agreed. Both vaults followed the daily policy: each received one complete observation over nine sampled blocks. Logs showed 14 scheduled historical subcalls and one feedback flush, with no decoding errors.

Both runs recorded **22 physical RPC attempts**, including preparation and direct verification; the cohort does not fill 100 subcalls. The 100-call run took 2.06 seconds. The 40-call run took 59.43 seconds because Hypersync returned its 15-request rate limit during timestamp collection; the existing bounded backoff waited and recovered. This is not evidence that batch size 100 made historical execution faster. JSON-RPC provider domains were `arb-mainnet.g.alchemy.com`, `edge.goldsky.com` and `lb.drpc.org`.

The initial smoke-check implementation was corrected to read the existing Parquet `chain` column and to validate the reader's `convertToAssets` price instead of the different assets/supply helper. The passing results above were produced after those corrections.

## Real full-batch comparison

```shell
source .local-test.env
PYTHONPATH="$PWD" RPC_BATCH_CHECK=true RPC_CHECK_CHAIN_ID=42161 timeout 180 poetry run python scripts/erc-4626/check-rpc-batch-parity.py
```

Passed on Arbitrum block **517,522,497**, using 26 distinct targets read from the existing local catalogue without changing it. Raw core/optional inputs matched exactly. Size 40 used **8 attempts in 0.25 seconds**; size 100 used **6 attempts in 0.17 seconds**, including connection overhead, with no transport errors. Provider: `edge.goldsky.com`. The command logs the exact target addresses for reproduction. This is a measured 25% reduction for this bounded workload, not the theoretical 60% asymptote.

## Scheduling throughput limit

An offline comparison ran the baseline scheduler from `9d366717a` and the new generator on the same staggered states, holding physical size at 100. The cohort had 100 hourly and 1,000 daily readers, with four methods each. Simulated requests cost three milliseconds and used eight dispatch slots; this models latency rather than running real providers or measuring process startup/serialisation.

| Hourly slots | Baseline modelled requests | Feedback modelled requests | Baseline elapsed | Feedback elapsed |
|---|---:|---:|---:|---:|
| 9 | 114 | 54 | 0.143 s | 0.258 s |
| 50 | 1,711 | 300 | 1.188 s | 1.462 s |

A second run replaced 50 daily readers with permanently unknown-TVL readers. At nine slots, modelled requests fell from 130 to 72 while elapsed time rose from 0.151 to 0.326 seconds; at 50 slots, requests fell from 1,738 to 400 while elapsed rose from 1.181 to 1.746 seconds.

The feedback barrier saves requests but can increase elapsed time by removing cross-block parallelism. A real full-chain duration and production refresh-cycle comparison remain deployment observations. No fixed slowdown threshold, production speedup or global savings percentage is claimed.
