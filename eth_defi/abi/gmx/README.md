# GMX ABI sources

All GMX ABIs below belong to **one** release, `GMX_VENDORED_ABI_RELEASE`
(`v2.2.1`) in `eth_defi/gmx/contracts.py`. The pinned contract addresses and
these ABIs must always move together in one commit.

- `Reader.json`, `ExchangeRouter.json`, `GlvReader.json` and `Oracle.json` are the
  `abi` arrays of `deployments/arbitrum/<Contract>.json` at the
  `gmx-io/gmx-synthetics` tag
  [`2026-10-23-deployments`](https://github.com/gmx-io/gmx-synthetics/tree/2026-10-23-deployments/deployments/arbitrum),
  re-serialised with `json.dumps(abi, indent=2)` plus a trailing newline. The
  deployment JSONs exceed 1 MB, so fetch them from `raw.githubusercontent.com`
  (the GitHub contents API returns empty). The verified Arbitrum Reader is
  [`0xAda708aFf0f1D784D28cd8Ff4d6D977fF9599e5D`](https://sourcify.dev/server/v2/contract/42161/0xAda708aFf0f1D784D28cd8Ff4d6D977fF9599e5D?fields=all).
- `OrderHandler.json` is a minimal Foundry-style stub holding only
  `executeOrder`, used by the Anvil keeper helper. v2.2.1 did not change that
  signature, and error decoding uses the selector table in
  `eth_defi/gmx/events.py`, not this file, so it was deliberately not refreshed.
- `MockOracleProvider.json` is compiled from
  `tests/gmx/forked-env-example/contracts/mock/MockOracleProvider.sol` with
  Solidity 0.8.20.

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

When GMX ships a new release: update the ABIs, add the new `PINNED_CONTRACTS`
entry, and bump `GMX_DEFAULT_CONTRACT_RELEASE` and `GMX_VENDORED_ABI_RELEASE`
in one commit.
