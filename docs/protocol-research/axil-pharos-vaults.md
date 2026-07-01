# Axil Pharos vaults

Research date: 2026-06-30.

## Summary

Axil has at least two Turtle-listed consumer credit vaults on Pharos Mainnet. They are not currently indexed by our vault pipeline because Pharos Mainnet is not configured as a supported EVM scanner chain in this repository, and Envio HyperSync does not currently list Pharos as a supported HyperSync network.

The known Axil vault tokens are:

| Vault | Symbol | Chain | Address | Evidence |
| ----- | ------ | ----- | ------- | -------- |
| Axil Consumer Credit Vault - 6M | VRPCS | Pharos Mainnet | `0xee26bb0989691735c997dfdc49a4a607f75e190b` | [RWA.xyz VRPCS asset page](https://app.rwa.xyz/assets/VRPCS) |
| Axil Consumer Credit Vault - 7D | VRPCW | Pharos Mainnet | `0x1c2bc8b553d9a7e61f7531a3a4bf2162f4569268` | [RWA.xyz VRPCW asset page](https://app.rwa.xyz/assets/VRPCW) |

Pharos Mainnet uses chain id `1672` (`0x688`). See the [dRPC Pharos chain page](https://drpc.org/chainlist/pharos-mainnet-rpc) and [Chainlist Pharos entry](https://chainlist.org/chain/1672).

## Current repository state

The local vault universe generated on 2026-06-28 did not contain Axil, VRPCS, VRPCW, or any Pharos chain entries.

Pharos support is missing from the scanner configuration:

- `eth_defi.chain.CHAIN_NAMES` has no Pharos entry for chain id `1672`.
- `eth_defi.vault.scan_all_chains.build_chain_configs()` has no `Pharos` / `JSON_RPC_PHAROS` scanner entry.
- The current vault discovery path uses Envio HyperSync when available, with JSON-RPC as fallback. Pharos cannot use the HyperSync path unless Envio adds a Pharos HyperSync endpoint.

The repository already contains Axil curator and logo metadata:

- `eth_defi/data/feeds/curators/axil.yaml`
- `eth_defi/data/vaults/original_logos/axil/README.md`
- `eth_defi/data/vaults/formatted_logos/axil/generic.png`

This means the gap is chain and vault indexing support, not Axil brand metadata.

## Provider support

Provider status checked on 2026-06-30:

| Provider | Pharos Mainnet status | Notes |
| -------- | --------------------- | ----- |
| Envio HyperSync | Not listed | [HyperSync supported networks](https://docs.envio.dev/docs/HyperSync/hypersync-supported-networks) does not list Pharos or chain id `1672`. Test endpoints such as `https://1672.hypersync.xyz` return 404. |
| Goldsky | Supported | [Goldsky Pharos docs](https://docs.goldsky.com/chains/pharos) list Pharos Mainnet and Atlantic Testnet. [Goldsky supported networks](https://docs.goldsky.com/chains/supported-networks) cover Mirror datasets for EVM chains, including blocks, logs, enriched transactions, and traces. This is an indexing product, not a drop-in Web3.py JSON-RPC endpoint for the current scanner. |
| dRPC | Supported | [dRPC Pharos Mainnet RPC](https://drpc.org/chainlist/pharos-mainnet-rpc) lists chain id `1672`, archive support, and the HTTPS endpoint `https://pharos.drpc.org`. |

## Missing pieces

To index Axil vaults, the pipeline needs:

1. Add Pharos Mainnet chain metadata:

   - Add `1672: "Pharos"` to `eth_defi.chain.CHAIN_NAMES`.
   - Add a Pharos homepage entry to `eth_defi.chain.CHAIN_HOMEPAGES`.
   - Add a block time estimate to `eth_defi.chain.EVM_BLOCK_TIMES`.

2. Add Pharos to the multi-chain scanner:

   - Add `ChainConfig("Pharos", "JSON_RPC_PHAROS", True)` to `build_chain_configs()`.
   - Configure `JSON_RPC_PHAROS` in production with an archive-capable RPC provider, e.g. dRPC.
   - Force or allow the JSON-RPC discovery backend for Pharos while HyperSync is unavailable.

3. Verify Axil discovery events:

   - Confirm whether the Axil contracts emit standard ERC-4626 `Deposit` and `Withdraw` events or only ERC-7540 async request/claim events.
   - If the standard event pair is not emitted, add protocol-specific discovery events in `eth_defi.erc_4626.discovery_base`.
   - Confirm whether the contracts expose enough ERC-4626/ERC-7540 calls for `probe_vaults()` and price scanning.

4. Add test coverage:

   - Add a focused Pharos/Axil detection test using the two known addresses.
   - Capture expected protocol/features after live probing.
   - Add scanner configuration tests that assert Pharos appears in `CHAIN_NAMES` and `build_chain_configs()`.

5. Validate historical scanning:

   - Run a small Pharos range scan around the first Axil deposit events.
   - Verify metadata rows are created for both VRPCS and VRPCW.
   - Run price scanning for the detected vaults and check that share price, NAV, denomination token, and first-seen timestamps are sane.

## Practical next step

Start with RPC-only Pharos support through `JSON_RPC_PHAROS`, using dRPC or another archive-capable provider. Goldsky can be used for a future dedicated indexing adapter, but the current pipeline expects either HyperSync-compatible event streaming or Web3.py JSON-RPC access.
