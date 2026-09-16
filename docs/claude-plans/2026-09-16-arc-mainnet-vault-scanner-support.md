# Arc mainnet vault scanner support plan

## Goal

Add Arc mainnet as a supported EVM source for the ERC-4626 vault pipeline. The
initial scope is read-only discovery, metadata refresh, historical share-price
scanning, timestamp caching, and export. It does not include transaction
broadcasting, deployments, bridge operations, or a new protocol adapter unless
the first reviewed Arc vaults require one.

## Confirmed launch context

- [Circle announced Arc public mainnet on 16 September
  2026](https://www.circle.com/fr/pressroom/circle-launches-arc-mainnet-an-economic-operating-system-for-the-internet).
  The announcement describes Arc as an EVM-compatible L1, with USDC gas and
  deterministic sub-second finality.
- [Circle's current connection guide](https://developers.circle.com/stablefx/howtos/connect-wallet-console)
  records Arc mainnet as chain ID `5042` and its HTTPS RPC as
  `https://rpc.mainnet.arc.io`. It lists Arc Testnet separately as `5042002`.
- The launch announcement identifies Aave and Morpho as day-one credit-market
  deployments. Morpho vaults are likely ERC-4626 candidates; this must be
  established by an onchain discovery run rather than assumed from the launch
  list.
- The repository already has only Arc **Testnet** coverage:
  `HYPERSYNC_SERVES[5042002]` and a focused test. Arc mainnet is not yet in
  `CHAIN_NAMES`, `EVM_BLOCK_TIMES`, production scanner configuration, Docker
  environment passthrough, or operator documentation.

## Provider and indexer status (checked 2026-09-16)

- The Arc endpoint derived from the configured Ethereum Goldsky URL by replacing
  its final chain-id path segment (`1` → `5042`) works. It returned
  `eth_chainId = 0x13b2`, a current head, and block 1, so it is suitable for
  initial current-state/discovery testing. Historical state still needs the
  explicit archive-read checks in this plan before it is used for price scans.
- The Arc endpoint derived from the configured Ethereum dRPC URL by replacing
  `network=ethereum` with `network=arc` returns HTTP `403` for chain ID, head,
  and block reads. Do not add it to `JSON_RPC_ARC` until dRPC enables Arc for
  the existing API key or issues an Arc-capable key.
- No usable public Arc mainnet Hypersync stream was confirmed. The conventional
  `arc.hypersync.xyz` and `5042.hypersync.xyz` hosts did not resolve, while
  `arc-mainnet.hypersync.xyz` refused the connection. Envio HyperRPC's
  `arc.rpc.hypersync.xyz` resolves but requires authentication and is an RPC
  service, not evidence of an indexed Hypersync event stream. Therefore Arc
  must remain outside production ERC-4626 discovery and timestamp operations
  until Envio publishes a working mainnet stream and the bounded event test
  passes.

## Constraints and decisions

- Use `JSON_RPC_ARC` for Arc mainnet. Adding `5042: "Arc"` to `CHAIN_NAMES`
  makes `get_json_rpc_env()` and `read_json_rpc_url()` derive this consistently.
- Keep testnet and mainnet separate. Do not repurpose chain ID `5042002` or add
  the testnet to the production all-chain schedule.
- Historical event and timestamp reads require Hypersync. Do not make
  JSON-RPC `eth_getLogs` a production fallback. Arc should not be scheduled
  until Envio publishes and the team verifies a mainnet endpoint. This gate is
  currently unmet.
- USDC is Arc's native gas asset but the scanner is read-only. Do not invent a
  wrapped-native-token address, a sequencer configuration, or transaction
  settings. Add a mainnet USDC contract to `USDC_NATIVE_TOKEN` or stablecoin
  metadata only after its address and ERC-20 behaviour have been verified from
  Circle's official contract documentation and a real mainnet call.
- Deterministic finality does not remove the need for the normal persisted lead
  cursors, reader states, Parquet safeguards, or timestamp cache. It only means
  that a committed Arc block need not be delayed for a multi-block reorg window.

## Implementation steps

1. Establish the production provider and indexer prerequisites.

   - Add `JSON_RPC_ARC` to the operator's secrets as an archive-capable Arc
     endpoint. Confirm `eth_chainId == 5042`, obtain the current height, and
     test historical `eth_call` at representative early blocks before enabling
     price history.
   - Confirm the public Arc mainnet Hypersync URL with Envio's supported-network
     documentation or Envio support. Smoke-test its height and a bounded
     ERC-4626 `Deposit`/`Withdraw` stream. Record the exact URL and verification
     date in the implementation notes; do not infer it from the existing
     `arc-testnet` hostname.
   - Query a short recent block range to measure the observed effective block
     interval. Store a non-zero float in `EVM_BLOCK_TIMES[5042]`; do not assume
     a particular value solely from Arc's sub-second-finality claim, especially
     if block-header timestamps use one-second resolution.
   - Verify the standard Multicall3 deployment and the official mainnet USDC
     ERC-20 address/decimals. This determines whether generic vault reads work
     unchanged and whether USDC-specific metadata is required.

2. Add Arc to reusable chain metadata.

   - In `eth_defi/chain.py`, add Arc mainnet (`5042`) to `CHAIN_NAMES`,
     `CHAIN_HOMEPAGES`, and `EVM_BLOCK_TIMES` using the measured interval.
     Use Arc's official public homepage, not an unverified explorer URL.
   - Add a Foundry cache network-name entry only after an Arc Anvil fork is
     successfully created and the directory name is observed. Arc support for
     scanner reads does not require this entry.
   - Leave `POA_MIDDLEWARE_NEEDED_CHAIN_IDS` and `SEQUENCERS` untouched unless
     an observed Arc historical-header or write-path failure demonstrates a
     need.
   - In `eth_defi/hypersync/server.py`, add the verified mainnet `5042` server
     while retaining the existing `5042002` testnet mapping.

3. Schedule the production scanner and pass its configuration into containers.

   - Add `ChainConfig("Arc", "JSON_RPC_ARC", True)` to
     `eth_defi/vault/scan_all_chains.py::build_chain_configs()`, near the other
     fast EVM networks. This automatically makes Arc eligible for lead
     discovery, price scans, timestamp prepopulation, cache healing, cycle
     controls, and `CHAIN_ORDER` / `TEST_CHAINS` selection.
   - Add `JSON_RPC_ARC: ${JSON_RPC_ARC:-}` to both `vault-scanner-oneshot` and
     `vault-scanner-looped` in `docker-compose.yml`.
   - Add the same chain to the legacy
     `scripts/erc-4626/scan-vaults-all-chains.sh` sequence so that the manual
     and Python orchestration paths do not drift.
   - Update the `scan-vaults.py` and `scan-vaults-all-chains.py` sections of
     `scripts/erc-4626/README-vault-scripts.md`: document `JSON_RPC_ARC`, a
     metadata-only Arc run, and the rule that prices need both archive RPC and
     Hypersync timestamp support. Add Arc to the illustrative `CHAIN_ORDER`
     list.

4. Validate generic ERC-4626 coverage and add only evidence-backed protocol work.

   - First run Arc discovery in an isolated `PIPELINE_DATA_DIR` with
     `SCAN_PRICES=false`; inspect lead count, classification outcome, metadata
     failures, token symbols, and detected Aave/Morpho contracts.
   - Treat `ERC-4626` as the initial eligibility boundary. Aave markets or
     other contracts that are not vault share tokens should not be forced into
     the ERC-4626 pipeline.
   - If Morpho vaults are detected but missing from Morpho's offchain API,
     compare the behaviour with the existing narrow Robinhood bypass. Add an
     Arc-specific bypass only with captured mainnet examples and regression
     tests; do not widen a general missing-record exception pre-emptively.
   - If a day-one protocol exposes a non-standard vault or custom price/fee
     method, make it a separate follow-up integration with reviewed contracts,
     ABI source, classification probe, adapter, and address-scoped migration.

5. Bring Arc into production without endangering existing scanner state.

   - Start with the focused metadata-only all-chain command below. Review its
     lead report and isolated database before adding `JSON_RPC_ARC` to the
     production environment.
   - Prepopulate Arc's dense timestamp cache at
     `~/.tradingstrategy/block-timestamp/5042-timestamps.duckdb` before the
     first historical price run. Because the network is new, a genesis-to-head
     fill should be bounded and observable, but it still needs Hypersync rate
     limits and durable cache writes.
   - Run the initial price scan only after archive-state and timestamp coverage
     pass. Preserve the shared metadata pickle, reader-state pickle, Parquet
     files, and timestamp-cache directory; never reset another chain as part of
     enabling Arc.
   - Enable `SCAN_PRICES=true` in production after the first Arc run has shown
     viable denominated vaults and stable historical Multicall responses.
     Preserve the normal per-chain reader state from then on.

6. Add focused automated and real-integration verification.

   - Extend `tests/test_chain.py` to check the Arc name, ID lookup,
     `JSON_RPC_ARC` derivation/reading, homepage, and measured block-time
     value.
   - Extend `tests/hypersync/test_hypersync_server.py` to assert the verified
     mainnet Arc endpoint separately from the existing testnet assertion.
   - Extend `tests/vault/test_scan_all_chains_config.py` to require
     `ChainConfig("Arc", "JSON_RPC_ARC", True)`.
   - Add a minimal Arc integration test guarded by `JSON_RPC_ARC` and
     `HYPERSYNC_API_KEY`. It must verify chain ID, a successful current-state
     multicall read, and a bounded real Hypersync event/timestamp response; it
     should be opt-in locally or placed in the appropriate slow integration
     workflow so ordinary CI remains deterministic.
   - Once discovered, add at least one fixed, real Arc ERC-4626 vault test. It
     should assert classification and current metadata against the configured
     provider; use a historic fork only after Arc/Anvil compatibility and a
     cacheable fixed block have been demonstrated.

7. Format, test, and document the release.

   - Add the required dated `feat:` entry to `CHANGELOG.md` when this feature
     is implemented.
   - Run `poetry run ruff format` and the three focused configuration test
     modules. Use `.local-test.env` and the prescribed three-minute pytest
     timeout.
   - Record the real integration result in the pull-request comment: command,
     pass/fail outcome, date, and redacted provider identity. Include any
     provider retention or HyperSync availability limitation and the safe retry
     plan rather than repeatedly retrying the same bulk request.

## Suggested verification sequence

```shell
# Isolated discovery first: no shared production state and no price writes.
source .local-test.env && \
PIPELINE_DATA_DIR=/tmp/arc-vault-pipeline \
TEST_CHAINS=Arc \
SCAN_PRICES=false \
SKIP_POST_PROCESSING=true \
MAX_CYCLES=1 \
poetry run python scripts/erc-4626/scan-vaults-all-chains.py

# After the mainnet Hypersync mapping and archive provider have been verified,
# fill only Arc's missing timestamp range.
source .local-test.env && \
CHAIN_FILTER=Arc \
poetry run python scripts/hypersync/prepopulate-timestamps.py

# Focused unit coverage.
source .local-test.env && poetry run pytest \
  tests/test_chain.py \
  tests/hypersync/test_hypersync_server.py \
  tests/vault/test_scan_all_chains_config.py \
  -q
```

The implementation owner should use the repository's configured timeout of
`180000` milliseconds for the pytest command. The price-enabled production run
is deliberately excluded from this plan until the isolated discovery, archive
state, timestamp-cache, and stablecoin-metadata checks have all succeeded.
