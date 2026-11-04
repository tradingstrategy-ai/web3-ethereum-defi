# Avantis verified interface

- Chain: Base (8453).
- Unified avUSDC proxy: [0x944766f715b51967e56afde5f0aa76ceacc9e7f9](https://basescan.org/address/0x944766f715b51967e56afde5f0aa76ceacc9e7f9).
- Current implementation: [Tranche, 0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89](https://basescan.org/address/0xe79184763b27fa8bb0eb2d6af1f0123d43af0e89#code).
- Implementation at `BASE_MIDNIGHT_BLOCK` (49,030,926): [0xc3769e440e3515ba374372837f76aafdc4fba26d](https://basescan.org/address/0xc3769e440e3515ba374372837f76aafdc4fba26d#code).
- Fetched: 2026-11-04, Etherscan V2 `getsourcecode`, chain ID 8453. `Tranche.json` is the current implementation's unmodified verified ABI array.

Both versions' `src/AvantisTranche.sol` inherit OpenZeppelin ERC-4626 and impose no sender/receiver identity restrictions on deposits. The adapter checks the ERC-1967 implementation slot before declaring permissionless admission; unreviewed upgrades remain unknown. Governance-only configuration and manager-only reserve operations are separate from public deposit admission.

Both reviewed versions use synchronous `deposit` and `redeem`. `balancingFee` returns zero following tranche unification. `collateralHealthFee` delegates to the vault manager; fee arithmetic uses a 100,000 denominator with rounding upwards. `previewRedeem` deducts the fee-inclusive charge. Withdrawals also check utilisation after the payout and fee transfer. The adapter simulates the exact redemption rather than treating `maxRedeem` as proof of execution.
