# GMX contract interfaces

All GMX ABIs that describe the deployed contracts belong to **one** release, `GMX_VENDORED_ABI_RELEASE`
(`v2.2.1`) in `eth_defi/gmx/contracts.py`. The pinned contract addresses and
these ABIs must always move together in one commit.

The v2.2.1 deployment JSONs exceed 1 MB. Fetch them from `raw.githubusercontent.com`
(the GitHub contents API returns empty) and re-serialise the `abi` array with
`json.dumps(abi, indent=2)` plus a trailing newline. Error decoding uses the
selector table in `eth_defi/gmx/events.py`, not these files.

The [contract loaders](../../gmx/contracts.py) bind the interfaces to the deployment on the selected chain. The local compiler artefacts also include bytecode for contracts used in fork tests.

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| [Reader.json](Reader.json) | `abi` array of `deployments/arbitrum/Reader.json` at the [`2026-10-23-deployments`](https://github.com/gmx-io/gmx-synthetics/tree/2026-10-23-deployments/deployments/arbitrum) tag of `gmx-io/gmx-synthetics` (v2.2.1); verified [Arbitrum Reader at `0xAda708aFf0f1D784D28cd8Ff4d6D977fF9599e5D`](https://sourcify.dev/server/v2/contract/42161/0xAda708aFf0f1D784D28cd8Ff4d6D977fF9599e5D?fields=all) | Unrecorded | Read positions and market values. |
| [GlvReader.json](GlvReader.json) | `abi` array of `deployments/arbitrum/GlvReader.json` at the [`2026-10-23-deployments`](https://github.com/gmx-io/gmx-synthetics/tree/2026-10-23-deployments/deployments/arbitrum) tag of `gmx-io/gmx-synthetics` (v2.2.1) | Unrecorded | Read GLV vault definitions and constituent markets. |
| [DataStore.json](DataStore.json) | Unrecorded | Unrecorded | Read GMX configuration and market state. |
| [ExchangeRouter.json](ExchangeRouter.json) | `abi` array of `deployments/arbitrum/ExchangeRouter.json` at the [`2026-10-23-deployments`](https://github.com/gmx-io/gmx-synthetics/tree/2026-10-23-deployments/deployments/arbitrum) tag of `gmx-io/gmx-synthetics` (v2.2.1) | Unrecorded | Submit router calls and orders. |
| [Oracle.json](Oracle.json) | `abi` array of `deployments/arbitrum/Oracle.json` at the [`2026-10-23-deployments`](https://github.com/gmx-io/gmx-synthetics/tree/2026-10-23-deployments/deployments/arbitrum) tag of `gmx-io/gmx-synthetics` (v2.2.1) | Unrecorded | Bind the GMX oracle interface. |
| [ReferralStorage.json](ReferralStorage.json) | Unrecorded | Unrecorded | Read referral codes. |
| [RoleStore.json](RoleStore.json) | Unrecorded | Unrecorded | Configure keeper roles on a test fork. |
| [OrderHandler.json](OrderHandler.json) | Minimal Foundry-style stub holding only `executeOrder`; not refreshed for v2.2.1 because that signature did not change | Unrecorded | Execute pending orders on a test fork. |
| [balance.json](balance.json) | Unrecorded | Unrecorded | Read token balances and decimals. |
| [eventemitter.json](eventemitter.json) | Unrecorded | Unrecorded | Decode GMX events. |
| [GmxOrderExecutor.json](GmxOrderExecutor.json) | [Local Solidity source](../../../tests/gmx/forked-env-example/contracts/GmxOrderExecutor.sol) | Compiled locally | Deploy the fork test order executor. |
| [MockOracleProvider.json](MockOracleProvider.json) | [Local Solidity source](../../../tests/gmx/forked-env-example/contracts/mock/MockOracleProvider.sol), Solidity 0.8.20 | Compiled locally | Supply controlled prices in fork tests. |

## Release and rollback rule

Mainnet addresses are resolved through `get_contract_addresses()`. It accepts
only the release the vendored ABIs belong to and raises
`GMXReleaseMismatchError` for any other (including `GMX_CONTRACT_RELEASE=v2.2c`
and live resolution that returns an older known release), because a v2.2c
Reader decoded with the v2.2.1 ABIs silently mis-aligns `Position.numbers`,
`MarketInfo` and `PositionFees`. Older releases stay in `PINNED_CONTRACTS` as
historical data only.

**Rollback = deploy the previous image/tag of this library**, which carries the
matching addresses and ABIs. Do not try to roll back with an environment
variable.

## Chain compatibility

The v2.2.1 `Reader` and `GlvReader` ABIs are vendored for Arbitrum mainnet only.
Arbitrum Sepolia still uses older Reader layouts, and Avalanche has no v2.2.1
contract pin. Until matching per-chain deployments and ABIs are added, these
flows are unsupported and raise `GMXReleaseMismatchError`:

- `get_reader_contract()` and `get_glv_reader_contract()` for Arbitrum Sepolia;
- market loading through `Markets._fetch_onchain_market_addresses()`,
  `Markets.get_available_markets()`, `fetch_all_gmx_markets()`, or the CCXT GMX
  `load_markets()` path on Sepolia;
- the onchain fallback of `GetOpenPositions.get_data()` and CCXT
  `fetch_positions()` when REST and GraphQL have no position data;
- the Reader fallback of CCXT `fetch_order()` when earlier sources do not
  resolve an order;
- `scripts/lagoon/lagoon-gmx-example.py`'s `wait_for_position()` Sepolia path;
- `scripts/gmx/close-testnet-position.py`, which now stops with an explicit
  release mismatch instead of decoding positions against the wrong ABI;
- Avalanche contract resolution, including `get_contract_addresses()`,
  `get_reader_contract()`, and `get_glv_reader_contract()`.

Static Sepolia address inspection remains available through
`NETWORK_CONTRACTS` and `get_contract_addresses("arbitrum_sepolia")`. These
address lookups do not bind or decode a Reader ABI.

When GMX ships a new release: update the ABIs, add the new `PINNED_CONTRACTS`
entry, and bump `GMX_DEFAULT_CONTRACT_RELEASE` and `GMX_VENDORED_ABI_RELEASE`
in one commit.
