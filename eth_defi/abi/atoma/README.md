# Atoma contract interfaces

These interfaces were retrieved from the verified implementation contracts
using Etherscan API v2 (`chainid=42161`, `module=contract`,
`action=getabi`). The ABI files contain the returned interface array, formatted
as JSON. Load them through `eth_defi.abi.get_deployed_contract()` at the proxy
address. `IAtomaVaultEvents.json` remains the separate discovery interface.

| Interface | Verified implementation | Tested block | Retrieved | SHA-256 of committed file |
| --- | --- | ---: | --- | --- |
| `AtomaVaultFeeSplit.json` | [0x153b97751f8979deea74506ea6b0be3c59dbd527](https://arbiscan.io/address/0x153b97751f8979deea74506ea6b0be3c59dbd527#code) | 520900000 | 2026-11-03 | `8bdec480fa009377d7b87382963998856ca3affec4077013e0621bbb8ce366fb` |
| `AtomaVault.json` | [0x9161be115867c347adfb4c88a80e759920de86dc](https://arbiscan.io/address/0x9161be115867c347adfb4c88a80e759920de86dc#code) | 519150162 | 2026-10-28 | `5c0fa4a5d1838787ac1dc3d7363e5c1e2c5f2e78c086a6539c079f51fc08f209` |
| `AtomaVaultLegacy.json` | [0x9521b08303ae010e85e24fc15d5334a0e506641e](https://arbiscan.io/address/0x9521b08303ae010e85e24fc15d5334a0e506641e#code) | 487039644 | 2026-10-28 | `ae9a916e219358ff5d3ecf40e18a8372ff3c8fc597452b00eaf2880b3d0a9e27` |

Both Arbitrum proxies use the corresponding implementation at these blocks:

- [Atoma Index, `0xcc56410e1a136af0eceb7241c6ae394f4d8b581c`](https://arbiscan.io/address/0xcc56410e1a136af0eceb7241c6ae394f4d8b581c#code).
- [Atoma RWA, `0x1c788e14d8e5b446e3f71b5142e2edabcab36da1`](https://arbiscan.io/address/0x1c788e14d8e5b446e3f71b5142e2edabcab36da1#code).

Both proxies were upgraded from `0x9161be11…` to `0x153b9775…` in one
transaction at block 519510080 (2026-10-29). Execution is restricted to these
two addresses and to the reviewed `AtomaVaultFeeSplit.json` and
`AtomaVault.json` implementations. The legacy interface supports historical
receipt interpretation and canonical-fork readers, not execution
certification. Resolve ERC-1967 storage at the operation block; do not infer a
version from a failed getter.

The verified source is `src/AtomaVault.sol` in each explorer's source bundle.
`WithdrawalClaimed.assets` is **net** USDC in every version. The two reviewed
execution versions retain the exit fee in `accruedWithdrawalFees`; the legacy
version transfers it to the operator during the claim. In the reviewed
versions, deposits lock only newly minted shares, and settled claims are
subject to `shortfallIndexWad` haircuts.
`getEpoch()` returns `(totalShares, settlementNav, settled)`;
`settledUnclaimedAssets()` tracks reserved liabilities. No version permits
ordinary ERC-4626 redemption or user cancellation.

## Fee-split implementation review

The verified sources of `0x9161be11…` and `0x153b9775…` use the same compiler
(solc 0.8.24, 200 optimiser runs, Cancun) and settings. Only `src/AtomaVault.sol`
differs. The interface adds `depositWithReferral`, `feeSplit`, `setFeeSplit`,
the `FeeSplitUpdated`, `FeeSharesDistributed`, `WithdrawalFeesDistributed` and
`DepositReferred` events, and the `InvalidFeeSplit` error. Nothing is removed.

- Performance-fee shares and collected withdrawal fees go to the
  owner-configured fee split, or to `feeRecipient` when no split is configured.
  Both proxies had a four-recipient split at block 520900000.
- `claimWithdrawal()` charges no withdrawal fee to `feeRecipient` or to a
  fee-split recipient. `AtomaVault.fetch_withdrawal_fee_bps()` mirrors this.
- `depositWithReferral()` calls `deposit()` and emits `DepositReferred`. The
  Guard does not allow it; the adapter deposits through `deposit()`.
- `_feeSplits` is appended after `feeRecipient` and `__gap` shrinks from 33 to
  32 slots, so the storage layout is compatible.

`deposit`, `requestWithdrawal`, `settleEpoch`, `claimWithdrawal`, the epoch
schedule, NAV updates and fee rates are otherwise unchanged.

## Accepting a new implementation

Atoma upgrades the proxies about once a month. An unreviewed implementation
blocks exits from held positions as well as new deposits, so review it promptly:

1. Download both verified source bundles through Etherscan API v2
   (`action=getsourcecode`) and compare every source file and the compiler
   settings with the last reviewed implementation.
2. Check the deposit, request, settlement, claim and NAV paths, storage-layout
   compatibility and any new external functions that the Guard could expose.
3. Commit the verified interface here, add its row above, and add it to
   `ATOMA_RECEIPT_INTERFACES` and, if approved for execution,
   `ATOMA_EXECUTION_IMPLEMENTATIONS`.
4. Move the guarded fork tests to a block after the upgrade, update their
   absolute values and RPC cache seed, and keep a request-across-upgrade test.
