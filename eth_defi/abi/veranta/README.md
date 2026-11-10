# Veranta verified interface

- Chain: Base (8453).
- Unified avUSDC proxy: [0x944766f715b51967e56afde5f0aa76ceacc9e7f9](https://basescan.org/address/0x944766f715b51967e56afde5f0aa76ceacc9e7f9).
- Current implementation: [Tranche, 0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89](https://basescan.org/address/0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89#code).
- Implementation at `BASE_MIDNIGHT_BLOCK` (49,030,926): [0xc3769e440e3515ba374372837f76aafdc4fba26d](https://basescan.org/address/0xc3769e440e3515ba374372837f76aafdc4fba26d#code).
- Fetched: 2026-11-04, Etherscan V2 `getsourcecode`, chain ID 8453. `Tranche.json` is the current implementation's unmodified verified ABI array.

Both versions' `src/AvantisTranche.sol` inherit OpenZeppelin ERC-4626 and impose no sender/receiver identity restrictions on deposits. The adapter checks the ERC-1967 implementation slot before declaring permissionless admission; unreviewed upgrades remain unknown. Governance-only configuration and manager-only reserve operations are separate from public deposit admission.

Both reviewed versions use synchronous `deposit` and `redeem`. `balancingFee` returns zero following tranche unification. `collateralHealthFee` delegates to the vault manager; fee arithmetic uses a 100,000 denominator with rounding upwards. `previewRedeem` deducts the fee-inclusive charge. Withdrawals also check utilisation after the payout and fee transfer. The adapter simulates the exact redemption rather than treating `maxRedeem` as proof of execution.

## Direct LP fee source review, 9 November 2026

The unified vault has no separate annual management or investor-profit charge
in its reviewed accounting, mint/burn and transfer paths. Both versions inherit
ordinary OpenZeppelin ERC-4626 accounting, then add explicit entry/exit fees in
`_deposit`, `_withdraw` and their previews. This conclusion comes from the
verified source paths and reward distribution, not solely missing ABI getters.

The manager proxy is
[0xe9fb8c70af1b99f2baaa07aa926fcf3d237348dd](https://basescan.org/address/0xe9fb8c70af1b99f2baaa07aa926fcf3d237348dd#code).
Etherscan V2 `getsourcecode` verified these manager implementations on
2026-11-09:

| Base block | Vault implementation | Manager implementation | Manager source |
| --- | --- | --- | --- |
| 49,030,926 | `0xc3769e440e3515ba374372837f76aafdc4fba26d` | [0xf34e3a38280a7e600cb4aa6d8c2a160d8e16127f](https://basescan.org/address/0xf34e3a38280a7e600cb4aa6d8c2a160d8e16127f#code) | `src/VaultManagerV2.sol` |
| 53,496,246 and 55,297,428 | `0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89` | [0xf0207407beb82902186321e3dd84a7050add275a](https://basescan.org/address/0xf0207407beb82902186321e3dd84a7050add275a#code) | `src/VaultManager.sol` |

The managers' `_distributeRewards` and `distributeRewardsWithThreshold` transfer
the selected LP rewards into the unified tranche without a separate LP
management or performance deduction. The documented trading-revenue split is
upstream of pool income and must not be subtracted again from share-price
returns. Manager `earlyWithdrawFee` and legacy lock-related routines do not
add a charge to the reviewed unified `deposit`/`redeem` path.

Zero direct LP fees do not guarantee that all manager-held income reaches the
pool. The keeper selects reward amounts and PnL proportions and can decrement
`totalRewards`; governance can reset PnL accounting and withdraw buffer funds.
The current `withdrawFromBuffer` excludes `totalRewards` and funding owed from
its available balance, while the historical V2 implementation checks only the
manager's token balance. This discretion is a trust/return risk, not a published
annual management or investor-performance rate.

The adapter reports zero management/performance fees only when the manager
address and exact implementation pair match this review at the requested
block. Unknown wiring or upgrades retain unknown rates; failed reads propagate.
Manager identity verification uses the existing tranche's `vaultManager()`
interface and ERC-1967 storage, so no additional ABI file is needed at runtime.
Any vault or manager upgrade requires a new source review and a review of the
static public fee description alongside the runtime identity allowlist.

At block 55,297,428, `feesOn()` was true, both balancing fees were zero and
`collateralHealthFee()` was 500, or 0.5% nominal. The manager's buffer ratio
was 97 and its five configured fee tiers were 2500, 1500, 500, 250 and 100,
using the same 100,000 denominator. For 1 USDC, `getWithdrawalFeesRaw` returned
5000 raw units and `getWithdrawalFeesTotal` returned 4976; exact previews
preserve the difference between payout-based and fee-inclusive rounding.
The current implementation ABI file remains the verified 2026-11-04 artifact.

## Published withdrawal reference

The user subsequently chose a fixed 0.5% rate for published withdrawal-fee
metadata and estimated net returns. `get_withdraw_fee()` therefore returns
0.005 at every block, including when the execution-fee switch is off. This
supersedes the initial dynamic-rate metadata policy; it does not change the
verified contract facts above. Actual transaction previews, preflight and
receipt analysis keep using the contract's live rate and exact rounding.
At the post-upgrade test block the actual rate is 1.5% while the exported
reference is 0.5%, and the guarded redemption test still receives the true
fee-adjusted payout. Public fee copy links the dynamic fee schedule so readers
can distinguish the reference estimate from their actual withdrawal charge.
