# Tempo chain support plan

## Goal

Add Tempo mainnet support to the EVM chain metadata, Hypersync lookup, ERC-4626
vault scanner, and container environment wiring, following the Robinhood Chain
support pattern.

Tempo mainnet facts checked on 2026-07-10:

- Chain ID: `4217` (`0x1079`).
- Testnet chain ID: `42431` for Tempo Testnet (Moderato), out of scope unless
  requested separately.
- Currency: `USD`.
- Public RPC: `https://rpc.tempo.xyz`.
- Public WebSocket RPC: `wss://rpc.tempo.xyz`.
- Public block explorer: `https://explore.tempo.xyz`.
- Official docs: `https://tempo.xyz/developers/docs/quickstart/connection-details`.
- Official homepage: `https://tempo.xyz`.
- Hypersync endpoint: `https://tempo.hypersync.xyz`; Envio also lists
  `https://4217.hypersync.xyz`.
- HyperRPC endpoint: `https://tempo.rpc.hypersync.xyz`; Envio also lists
  `https://4217.rpc.hypersync.xyz`.
- Goldsky supports Tempo mainnet with chain slug `tempo`; existing
  `JSON_RPC_ETHEREUM` Goldsky credentials can be transformed to chain ID `4217`.
- dRPC supports Tempo mainnet; the existing local dRPC credential was verified
  through a derived `network=tempo` endpoint and returned chain ID `4217`.
- A manual ERC-4626 lead scan using `JSON_RPC_TEMPO` and Hypersync scanned
  blocks `1` to `29,335,409`, found `25` vault leads, and classified `24` as
  usable with `1` broken lead.

## Robinhood pull request review

Recent Robinhood support landed through two merged pull requests:

- `#1229 feat: add Robinhood chain support`
  - Added `CHAIN_NAMES`, `CHAIN_HOMEPAGES`, `EVM_BLOCK_TIMES`, and `SEQUENCERS`
    entries in `eth_defi/chain.py`.
  - Added the Hypersync server mapping in `eth_defi/hypersync/server.py`.
  - Added `ChainConfig("Robinhood", "JSON_RPC_ROBINHOOD", True)` to
    `eth_defi/vault/scan_all_chains.py`.
  - Added `JSON_RPC_ROBINHOOD` passthrough to both Docker scanner services.
  - Added Robinhood to the legacy shell scanner and operator documentation.
  - Added focused tests for chain metadata, Hypersync mapping, and scanner
    configuration.
  - Also added Robinhood USDG metadata and stablecoin rate aliases because
    USDG was relevant to Robinhood vaults.
- `#1246 fix: retain Robinhood Morpho vaults during API coverage gap`
  - Added a narrow Robinhood-only Morpho API missing-record bypass.
  - Preserved on-chain-detected Morpho vaults that were legitimate but absent
    from Morpho's public API.
  - Added V1/V2 regression tests and documentation explaining that the bypass is
    temporary.

The Tempo plan should copy the broad support shape from `#1229`, but should not
copy the Morpho bypass from `#1246` unless Tempo vault data shows the same
off-chain API coverage problem.

## Codebase review

Relevant code paths:

- `eth_defi/chain.py`
  - `CHAIN_NAMES` drives display names and
    `eth_defi.provider.env.get_json_rpc_env()`.
  - `CHAIN_HOMEPAGES` drives report links.
  - `EVM_BLOCK_TIMES` is used by historical price and block/time estimation
    code.
  - Existing accessors return `float | None` or `float`, so sub-second block
    times are supported at the metadata API level. Implementation still needs a
    focused audit of scanner call sites for any accidental integer truncation.
  - `SEQUENCERS` is only needed when the chain exposes a separate sequencer or
    official write endpoint. Current Tempo docs expose normal HTTP/WSS RPC
    endpoints, not a separate sequencer endpoint.
  - `POA_MIDDLEWARE_NEEDED_CHAIN_IDS` should not include Tempo unless historical
    RPC reads prove it needs Web3.py extra-data middleware.
- `eth_defi/hypersync/server.py`
  - `HYPERSYNC_SERVES` is the single internal source for
    `get_hypersync_server()` and `is_hypersync_supported_chain()`.
  - Tempo `4217` is already present in the current worktree.
- `eth_defi/hypersync/utils.py`
  - `configure_hypersync_from_env()` can only auto-enable Hypersync when the
    chain ID exists in `HYPERSYNC_SERVES` and `HYPERSYNC_API_KEY` is present.
- `eth_defi/erc_4626/lead_scan_core.py`
  - Discovery creates Web3 from `JSON_RPC_URL`, reads `web3.eth.chain_id`,
    resolves the human chain name, then asks `configure_hypersync_from_env()`
    for the backend.
- `eth_defi/vault/scan_all_chains.py`
  - `build_chain_configs()` is the production source of EVM chains for the
    all-chain scanner.
  - `TEST_CHAINS`, `DISABLE_CHAINS`, `CHAIN_ORDER`, and cycle scheduling operate
    by `ChainConfig.name`.
- `docker-compose.yml`
  - Both `vault-scanner-oneshot` and `vault-scanner-looped` expose explicit RPC
    environment variables to the container.
- `scripts/erc-4626/README-vault-scripts.md`
  - Documents chain-specific scanner usage and focused dry-run patterns.
- `scripts/erc-4626/scan-vaults-all-chains.sh`
  - Legacy shell scanner has a hardcoded chain sequence and still needs explicit
    updates when a new EVM chain is added.
- `scripts/hypersync/prepopulate-timestamps.py`
  - Pulls `build_chain_configs()`, so Tempo becomes timestamp-prepopulation
    eligible after adding it to the all-chain config and Hypersync mapping.
- `eth_defi/provider/env.py`
  - No direct edit needed if `CHAIN_NAMES[4217] == "Tempo"`, because
    `get_json_rpc_env(4217)` derives `JSON_RPC_TEMPO`.
  - `read_json_rpc_url(4217)` then reads the derived environment variable and
    raises a clear `ValueError` when it is missing.

## Implementation steps

1. Update `CHANGELOG.md`.
   - Add one `feat:` entry dated `2026-07-10` because repo rules require a
     changelog entry for feature pull requests.
   - Suggested text: `feat: Add Tempo chain metadata, Hypersync lookup, vault
     scanner scheduling and Docker RPC wiring (2026-07-10)`.

2. Update `eth_defi/chain.py`.
   - Add `4217: "Tempo"` to `CHAIN_NAMES`.
   - Add `4217: {"name": "Tempo", "homepage": "https://tempo.xyz"}` to
     `CHAIN_HOMEPAGES`.
   - Add `4217` to `EVM_BLOCK_TIMES`.
   - Measure the block-time value from recent blocks over `JSON_RPC_TEMPO`
     before committing it. Use a sample of recent consecutive blocks and take a
     robust value such as median timestamp delta, not a single old/new block
     pair, because fast chains can have jitter.
   - Audit call sites that use `EVM_BLOCK_TIMES`, `get_block_time()`, or
     `get_evm_block_time()` in the vault scanner path and confirm sub-second
     floats are not rounded or truncated to zero.
   - Do not add Tempo to `SEQUENCERS` unless official docs expose a separate
     sequencer/write endpoint.
   - Do not add Tempo to `POA_MIDDLEWARE_NEEDED_CHAIN_IDS` unless a concrete
     historical block-read failure proves it is required.

3. Keep and verify the Hypersync server mapping.
   - The current worktree already adds `4217: "https://tempo.hypersync.xyz"` to
     `HYPERSYNC_SERVES`.
   - Use the slug host because it matches the convention used for most existing
     entries in `HYPERSYNC_SERVES`; Robinhood is the exception because that PR
     intentionally used `https://4663.hypersync.xyz`.
   - Re-smoke `https://tempo.hypersync.xyz/height` before implementation
     sign-off, because the previous manual scan validated Tempo Hypersync
     behaviour but the explicit host should be recorded.
   - Keep the updated maintenance date comment.
   - Keep the focused Tempo assertions in `tests/hypersync/test_hypersync_server.py`.

4. Update production chain selection in `eth_defi/vault/scan_all_chains.py`.
   - Add `ChainConfig("Tempo", "JSON_RPC_TEMPO", True)` to
     `build_chain_configs()`.
   - Place Tempo near the other fast EVM chains, e.g. close to Base, Arbitrum,
     and Robinhood.
   - Keep `scan_vaults=True` because the manual scan found valid ERC-4626 leads.
   - No hardcoded Tempo edit is expected in
     `scripts/erc-4626/scan-vaults-all-chains.py`; the Python production
     scanner should pick Tempo up through `build_chain_configs()`.

5. Update container environment wiring in `docker-compose.yml`.
   - Add `JSON_RPC_TEMPO: ${JSON_RPC_TEMPO:-}` to both `vault-scanner-oneshot`
     and `vault-scanner-looped`.
   - Keep it near other EVM RPC environment variables.

6. Update legacy scanner and operator docs.
   - Add a Tempo block to `scripts/erc-4626/scan-vaults-all-chains.sh`:
     `export JSON_RPC_URL=$JSON_RPC_TEMPO`, then `scan-vaults.py`, then the
     optional `scan-prices.py` block.
   - This `.sh` update is separate from the production Python scanner; the
     Python entrypoint is covered by `build_chain_configs()`.
   - In `scripts/erc-4626/README-vault-scripts.md`, document
     `JSON_RPC_TEMPO` for single-chain scanning.
   - Add a focused all-chain dry-run example using `TEST_CHAINS=Tempo`,
     `SCAN_PRICES=false`, and `SKIP_POST_PROCESSING=true`.

7. Add focused tests.
   - Extend `tests/test_chain.py` with:
     - `get_chain_name(4217) == "Tempo"`.
     - `get_chain_id_by_name("Tempo") == 4217`.
     - `get_json_rpc_env(4217) == "JSON_RPC_TEMPO"`.
     - `read_json_rpc_url(4217)` reads `JSON_RPC_TEMPO` when the test patches
       the environment, or add this assertion only if it can be done without
       relying on live secrets.
     - `get_evm_block_time(4217)` equals the measured configured value.
     - `get_chain_homepage(4217) == ("Tempo", "https://tempo.xyz")`.
   - Extend `tests/hypersync/test_hypersync_server.py` with:
     - `is_hypersync_supported_chain(4217) is True`.
     - `get_hypersync_server(4217) == "https://tempo.hypersync.xyz"`.
   - Extend `tests/vault/test_scan_all_chains_config.py` with:
     - `build_chain_configs()` contains
       `ChainConfig("Tempo", "JSON_RPC_TEMPO", True)`.
   - Avoid live RPC/Hypersync assertions in the normal unit suite.

8. Check whether Tempo needs Robinhood-style protocol-specific follow-up.
   - Inspect the `25` Tempo leads from the manual scan, especially Morpho and
     Mellow vaults.
   - If Tempo Morpho vaults are on-chain valid but missing from Morpho's public
     API, add a Tempo-specific temporary bypass with the same narrow shape as
     the Robinhood bypass in `#1246`.
   - Do not widen `MORPHO_API_NOT_FOUND_FLAG_BYPASS_CHAINS` pre-emptively.
   - Add protocol-specific tests only if a real Tempo data-quality issue is
     observed.

9. Decide whether stablecoin metadata is needed.
   - Robinhood needed USDG metadata because USDG was part of the chain's vault
     universe.
   - Tempo uses `USD` as its chain currency and supports stablecoin-style fee
     payment, but this does not automatically imply a new stablecoin metadata
     entry.
   - Add or update stablecoin metadata only if discovered Tempo vaults use a
     token not already covered by `eth_defi/data/stablecoins`.

## Manual verification

Before pytest in this worktree, ensure `.local-test.env` exists. If it is
missing, copy it from the main checkout as described in the worktree environment
instructions in `CLAUDE.md` / `AGENTS.md`.

Confirm the manual scan did not create a commit-relevant data change:

```shell
git status --short
```

The shared vault metadata pickle lives under `~/.tradingstrategy`; it should not
be committed, and implementation verification should avoid further shared-state
rewrites unless deliberately requested.

Run focused unit tests:

```shell
source .local-test.env && poetry run pytest \
  tests/test_chain.py \
  tests/hypersync/test_hypersync_server.py \
  tests/vault/test_scan_all_chains_config.py \
  -q
```

Run a focused all-chain dry run:

```shell
source .local-test.env && \
TEST_CHAINS=Tempo \
SCAN_PRICES=false \
SKIP_POST_PROCESSING=true \
MAX_WORKERS=4 \
MAX_CYCLES=1 \
poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

Check read-only scanner assumptions for Tempo's USD currency model:

- Verify discovery and metadata refresh do not rely on native ETH balance or an
  18-decimal gas token.
- Do not add transaction-sending support without a separate review of Tempo's
  non-standard native gas-token semantics.

Run a direct single-chain scan only if a fresh lead scan is required:

```shell
source .local-test.env && \
LOG_LEVEL=info \
JSON_RPC_URL=$JSON_RPC_TEMPO \
MAX_WORKERS=4 \
HYPERSYNC_CONCURRENCY=1 \
poetry run python scripts/erc-4626/scan-vaults.py
```

## Risks and notes

- `JSON_RPC_TEMPO` should be an archive-capable endpoint in the same
  space-separated fallback format as other `JSON_RPC_*` values.
- Public `https://rpc.tempo.xyz` is useful for smoke checks, but production
  scanner runs should use provider endpoints that can handle historical reads.
- Tempo uses `USD` as its currency and has no normal native gas-token model.
  Read-only scanner paths are expected to be unaffected, but this needs explicit
  verification before merging.
- `HYPERSYNC_API_KEY` remains required when `SCAN_BACKEND=auto`; adding the
  Hypersync server only makes auto-detection possible.
- Testnet `42431` is intentionally excluded from this plan.
- Timestamp prepopulation will pick up Tempo automatically after
  `build_chain_configs()` includes it. Because Tempo has sub-second blocks and a
  large block count, treat any full timestamp prepopulation as an operationally
  meaningful run and avoid triggering it accidentally.
- The manual scan updated the shared vault metadata pickle under
  `~/.tradingstrategy`. Future verification should avoid accidental shared-state
  rewrites unless that is intended.

## Acceptance criteria

- Tempo displays as `Tempo` instead of `Unknown chain 4217`.
- `get_json_rpc_env(4217)` derives `JSON_RPC_TEMPO`.
- `read_json_rpc_url(4217)` reads `JSON_RPC_TEMPO` when the environment variable
  is configured.
- `configure_hypersync_from_env()` can choose `https://tempo.hypersync.xyz` for
  a Web3 instance connected to chain `4217`.
- `TEST_CHAINS=Tempo` selects only the Tempo `ChainConfig`.
- Docker services pass `JSON_RPC_TEMPO` into the scanner container.
- Legacy `scan-vaults-all-chains.sh` can scan Tempo when `JSON_RPC_TEMPO` is set.
- Focused unit tests pass.

## Claude review

Initial Claude CLI plan review completed on 2026-07-10. Findings applied:

- Added the missing feature changelog implementation step.
- Clarified `get_json_rpc_env()` versus `read_json_rpc_url()` expectations.
- Added a block-time measurement method and a sub-second float call-site audit.
- Clarified that the production Python scanner gets Tempo from
  `build_chain_configs()`, while only the legacy shell scanner needs a hardcoded
  Tempo block.
- Aligned the worktree environment note with `CLAUDE.md` / `AGENTS.md`.
- Added explicit Hypersync slug-host verification, native USD gas-model scanner
  checks, timestamp-prepopulation operational notes, and shared metadata pickle
  safeguards.

Final blocking-only Claude CLI pass on 2026-07-10 returned:
`No blocking findings.` Non-blocking implementation note: when adding the
homepage test, match the real `get_chain_homepage()` return shape.
