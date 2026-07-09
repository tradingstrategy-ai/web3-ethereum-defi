# Robinhood chain support plan

## Goal

Add Robinhood Chain mainnet support to the EVM chain metadata, Hypersync lookup, ERC-4626 vault scanner, and container environment wiring.

Robinhood Chain mainnet facts checked on 2026-07-09:

- Chain ID: `4663` (`0x1237`).
- Testnet chain ID: `46630` (out of scope unless requested separately).
- Native gas token: `ETH`.
- Public RPC: `https://rpc.mainnet.chain.robinhood.com`.
- Public sequencer: `https://sequencer.mainnet.chain.robinhood.com`.
- Public block explorer: `https://robinhoodchain.blockscout.com`.
- Official product page: `https://robinhood.com/us/en/chain/` (returns `200` after Robinhood's locale redirect).
- Official docs: `https://docs.robinhood.com/chain/connecting/`.
- Official help centre: `https://robinhood.com/us/en/support/articles/robinhood-chain-mainnet/`.
- Hypersync endpoint requested by the operator: `https://4663.hypersync.xyz`; `https://4663.hypersync.xyz/height` returned `{"height":5191553}` during planning.
- Direct public RPC smoke check returned `eth_chainId = 0x1237`.
- Alchemy supports Robinhood Chain mainnet and testnet RPC/WebSocket access:
  - Mainnet RPC: `https://robinhood-mainnet.g.alchemy.com/v2/{API_KEY}`
  - Mainnet WebSocket: `wss://robinhood-mainnet.g.alchemy.com/v2/{API_KEY}`
  - Testnet RPC: `https://robinhood-testnet.g.alchemy.com/v2/{API_KEY}`
  - Testnet WebSocket: `wss://robinhood-testnet.g.alchemy.com/v2/{API_KEY}`
- dRPC supports Robinhood Chain mainnet as an Archive/Premium chain with HTTP and WSS access. Robinhood's docs list dRPC as a supported provider; dRPC's chainlist lists Robinhood chain ID `4663` / `0x1237`.
- dRPC endpoint URLs are dashboard/account generated. The existing local dRPC key was verified against `https://lb.drpc.org/ogrpc?network=robinhood-mainnet&dkey=...` and returned `eth_chainId = 0x1237`.

## Codebase review

Relevant code paths already inspected:

- `eth_defi/chain.py`
  - `CHAIN_NAMES` drives display names and `eth_defi.provider.env.get_json_rpc_env()`.
  - Existing accessor functions include `get_chain_name()`, `get_chain_id_by_name()`, `get_block_time()`, and `get_evm_block_time()`.
  - `CHAIN_HOMEPAGES` drives report links and existing entries use the dict shape `{"name": "...", "homepage": "..."}`.
  - `EVM_BLOCK_TIMES` is required by callers such as `eth_defi.erc_4626.profit_and_loss.get_block_time()` and used by historical price logic for chain time estimates.
  - `SEQUENCERS` has L2 sequencer/public RPC routing for chain-specific deployment or receipt-polling behaviour.
  - `POA_MIDDLEWARE_NEEDED_CHAIN_IDS` does not need Robinhood because it is an Arbitrum Nitro L2, not a geth Clique/PoA chain.
- `eth_defi/hypersync/server.py`
  - `HYPERSYNC_SERVES` is the single internal source for `get_hypersync_server()` and `is_hypersync_supported_chain()`.
  - ERC-4626 lead scanning, price timestamp reads, settlement scanning, Mellow scripts, and timestamp prepopulation all depend on this mapping.
- `eth_defi/hypersync/utils.py`
  - `configure_hypersync_from_env()` auto-enables Hypersync only when the chain ID exists in `HYPERSYNC_SERVES` and `HYPERSYNC_API_KEY` is present.
- `eth_defi/erc_4626/lead_scan_core.py`
  - Discovery creates Web3 from `JSON_RPC_URL`, reads `web3.eth.chain_id`, resolves the human chain name, then asks `configure_hypersync_from_env()` for the backend.
- `eth_defi/vault/scan_all_chains.py`
  - `build_chain_configs()` is the source of EVM chains for the production all-chain scanner.
  - The production entrypoint is `scripts/erc-4626/scan-vaults-all-chains.py`.
  - `scan_chain()` reads the RPC URL from the configured `JSON_RPC_*` env var, validates archive-node behaviour, runs vault discovery, optionally scans prices, then settlement scanning later uses the same chain config env vars.
  - `CHAIN_ORDER`, `TEST_CHAINS`, `DISABLE_CHAINS`, and loop scheduling operate by the `ChainConfig.name` string.
- `docker-compose.yml`
  - Both `vault-scanner-oneshot` and `vault-scanner-looped` expose explicit RPC env vars to the container. A new `JSON_RPC_ROBINHOOD` must be added to both services.
- `scripts/erc-4626/README-vault-scripts.md`
  - Documents `TEST_CHAINS`, `DISABLE_CHAINS`, `CHAIN_ORDER`, `SCAN_CYCLES`, Hypersync knobs, and single-chain script usage.
- `scripts/erc-4626/scan-vaults-all-chains.sh`
  - Legacy shell scanner has a hardcoded chain sequence. If still maintained, add Robinhood there as well.
- `scripts/hypersync/prepopulate-timestamps.py`
  - Pulls `build_chain_configs()`, so Robinhood becomes timestamp-prepopulation eligible automatically after adding it to the all-chain config and Hypersync mapping.
- `eth_defi/provider/env.py`
  - No direct edit needed if `CHAIN_NAMES[4663] == "Robinhood"`, because `get_json_rpc_env(4663)` will derive `JSON_RPC_ROBINHOOD` and `read_json_rpc_url(4663)` will read it.

## Implementation steps

1. Update `eth_defi/chain.py`.
   - Add `4663: "Robinhood"` to `CHAIN_NAMES`.
   - Add `4663: {"name": "Robinhood", "homepage": "https://robinhood.com/us/en/chain/"}` to `CHAIN_HOMEPAGES`.
   - Add `4663` to `EVM_BLOCK_TIMES`.
   - Recommended first value: `0.25`, matching Arbitrum-family scanner behaviour already used for Arbitrum. The official docs promise sub-second soft confirmations but do not publish a fixed block-time value; include that caveat in the comment.
   - Blast radius of a wrong static block-time value is time estimation and wait/ETA heuristics; it should not alter exact block ranges or persisted vault rows.
   - Add `4663` to `SEQUENCERS` with:
     - `sequencer`: `https://sequencer.mainnet.chain.robinhood.com`
     - `public_rpc`: `https://rpc.mainnet.chain.robinhood.com`
   - Do not add Robinhood to `POA_MIDDLEWARE_NEEDED_CHAIN_IDS`.

2. Update `eth_defi/hypersync/server.py`.
   - Add `4663: "https://4663.hypersync.xyz"` to `HYPERSYNC_SERVES`.
   - Refresh the "Updated" comment date if this project convention treats it as the manual maintenance date.

3. Update production chain selection in `eth_defi/vault/scan_all_chains.py`.
   - Add `ChainConfig("Robinhood", "JSON_RPC_ROBINHOOD", True)` to `build_chain_configs()`.
   - Place it close to Arbitrum/Base because Robinhood is an Arbitrum L2 and likely uses similar scanner behaviour.
   - Keep `scan_vaults=True`; the scanner should discover ERC-4626 vaults normally unless production data proves the chain should be price-only.

4. Update container environment wiring in `docker-compose.yml`.
   - Add `JSON_RPC_ROBINHOOD: ${JSON_RPC_ROBINHOOD:-}` to both `vault-scanner-oneshot` and `vault-scanner-looped`.
   - Keep it near Arbitrum/Base or the other L2 chain env vars for readability.

5. Update legacy and operator docs.
   - In `scripts/erc-4626/scan-vaults-all-chains.sh`, add a Robinhood block:
     - `export JSON_RPC_URL=$JSON_RPC_ROBINHOOD`
     - `python scripts/erc-4626/scan-vaults.py`
     - optional `scan-prices.py` block controlled by `SCAN_PRICES`.
   - In `scripts/erc-4626/README-vault-scripts.md`, mention `JSON_RPC_ROBINHOOD` in all-chain scanner examples/operator notes where chain-specific env vars are discussed.
   - Add a Robinhood example for single-chain testing:
     - `TEST_CHAINS=Robinhood ...` for `scripts/erc-4626/scan-vaults-all-chains.py`.
     - `LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ROBINHOOD ...` for `scripts/erc-4626/scan-vaults.py`.

6. Add focused tests if implementation goes ahead.
   - Add or extend a lightweight unit test for chain metadata:
     - `get_chain_name(4663) == "Robinhood"`.
     - `get_chain_id_by_name("Robinhood") == 4663`.
     - `get_json_rpc_env(4663) == "JSON_RPC_ROBINHOOD"`.
     - `get_evm_block_time(4663)` is not `None`.
   - Add or extend a Hypersync mapping test:
     - `is_hypersync_supported_chain(4663) is True`.
     - `get_hypersync_server(4663) == "https://4663.hypersync.xyz"`.
   - Add or extend all-chain scanner config test:
     - `build_chain_configs()` contains `ChainConfig("Robinhood", "JSON_RPC_ROBINHOOD", True)`.
   - Avoid live RPC/Hypersync tests in the normal unit suite; keep those as manual smoke checks because they need network and provider availability.

7. Manual verification after code changes.
   - Install deps if needed with the repo command:
     - `poetry install -E data -E test -E docs -E hypersync -E ccxt -E cloudflare_r2 -E duckdb`
   - Ensure `.local-test.env` exists before pytest, copying it from the main checkout if this worktree lacks it.
   - Run focused unit tests:
     - `source .local-test.env && poetry run pytest <focused test files> -q`
     - Candidate files: `tests/test_chain.py`, a new or existing Hypersync server mapping test, and a scanner-config test near existing `eth_defi.vault.scan_all_chains` tests.
   - Run a no-write or narrow live smoke check if `JSON_RPC_ROBINHOOD` and `HYPERSYNC_API_KEY` are available:
     - `source .local-test.env && TEST_CHAINS=Robinhood SCAN_PRICES=false SKIP_POST_PROCESSING=true MAX_WORKERS=4 MAX_CYCLES=1 poetry run python scripts/erc-4626/scan-vaults-all-chains.py`
   - For a direct single-chain backend check:
     - `source .local-test.env && LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ROBINHOOD END_BLOCK=<small_head_block> poetry run python scripts/erc-4626/scan-vaults.py`

## Risks and notes

- Production RPC should not use the rate-limited public RPC. Official docs recommend provider/archive endpoints for production and historical reads.
- `JSON_RPC_ROBINHOOD` should be an archive-capable endpoint in the same space-separated fallback format as other chains. Alchemy and dRPC are both suitable candidates based on current provider documentation.
- Alchemy has a confirmed fixed URL pattern. For dRPC, use an endpoint copied from the dRPC dashboard or a verified `robinhood-mainnet` endpoint.
- The all-chain scanner's `verify_archive_node()` may reject a non-archive public RPC. This is expected and should be documented rather than bypassed.
- `HYPERSYNC_API_KEY` remains required when `SCAN_BACKEND=auto`; adding the Hypersync server only makes auto-detection possible.
- Testnet `46630` is intentionally not included in this plan. Add it later only if the codebase has a concrete testnet use case.
- If Robinhood has compliance-level sequencer filtering, scanner reads should still see canonical included state, but operational notes should avoid assuming every submitted transaction will be accepted by the sequencer.

## Acceptance criteria

- Robinhood displays as `Robinhood` instead of `Unknown chain 4663`.
- `read_json_rpc_url(4663)` resolves `JSON_RPC_ROBINHOOD`.
- `configure_hypersync_from_env()` can choose `https://4663.hypersync.xyz` for a Web3 instance connected to chain `4663`.
- `TEST_CHAINS=Robinhood` selects only the Robinhood `ChainConfig`.
- Docker services pass `JSON_RPC_ROBINHOOD` into the scanner container.
- Legacy `scan-vaults-all-chains.sh` can scan Robinhood when `JSON_RPC_ROBINHOOD` is set.
- Focused unit tests pass.

## Claude review

Initial Claude CLI review completed on 2026-07-09 with no blocking findings. Follow-up edits applied:

- Clarified that the plan verified the actual chain accessor functions and `CHAIN_HOMEPAGES` dict shape.
- Clarified the production `.py` scanner entrypoint versus the legacy `.sh` script.
- Recorded the Robinhood product URL validation.
- Documented the block-time estimate blast radius.
- Avoided asserting exact new test filenames before implementation.

Final blocking-only Claude CLI pass on the updated plan returned: `No blocking findings.`

After adding Alchemy/dRPC RPC provider findings, another blocking-only Claude CLI pass returned: `No blocking findings.` Non-blocking note: explicitly confirm during implementation that `get_json_rpc_env(4663)` derives `JSON_RPC_ROBINHOOD`; this is already included in the focused test list.

Latest Claude CLI review on 2026-07-09 returned: `No blocking findings.` Non-blocking implementation notes:

- Confirm the `EVM_BLOCK_TIMES` entry uses the same integer chain-id key style as existing entries.
- Run the planned `get_json_rpc_env(4663) == "JSON_RPC_ROBINHOOD"` test before relying on the Docker env var wiring.
