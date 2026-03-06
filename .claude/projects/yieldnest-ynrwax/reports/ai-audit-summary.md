# YieldNest ynRWAx vault — AI mega-audit summary

## Target

| Property | Value |
|----------|-------|
| Contract | YieldNest RWA MAX (ynRWAx) |
| Proxy | `0x01Ba69727E2860b37bc1a2bd56999c1aFb4C15D8` |
| Implementation | `0xb46D7014C1A29b6A82D8eCDE5aD29d5B09aC7A1b` |
| Chain | Ethereum mainnet |
| Compiler | Solidity 0.8.24 |
| Vault version | 0.4.2 |
| TVL | ~$392,869 |
| Audit date | 2026-03-06 |

## Pipelines used

| # | Skill repo | Findings | Report |
|---|-----------|----------|--------|
| 1 | trailofbits/skills | 16 (2H, 5M, 7L, 4I) | `trailofbits-skills.md` |
| 2 | pashov/skills | 18 (0C, 3H, 10M, 5 below threshold) | `pashov-skills.md` |
| 3 | kadenzipfel/scv-scan | 8 (0C, 1H, 3M, 1L, 2I) | `kadenzipfel-scv-scan.md` |
| 4 | forefy/.context | 11 (0C, 3H, 4M, 4L) | `forefy-context.md` |
| 5 | quillai-network/qs_skills | 17 (0C, 3H, 5M, 9L/I) | `quillai-qs-skills.md` |
| 6 | Archethect/sc-auditor | 6 (0C, 1H, 3M, 2I) | `archethect-sc-auditor.md` |
| 7 | Cyfrin/solskill | 29 (0C, 3H, 6M, 8L, 12I) | `cyfrin-solskill.md` |
| 8 | hackenproof-public/skills | 13 (0C, 0H, 4M, 5L, 4I) | `hackenproof-skills.md` |
| 9 | auditmos/skills | 13 (0C, 3H, 6M, 4L) | `auditmos-skills.md` |
| 10 | Deployment analysis | 7 concerns | `deployment.md` |

## Deduplicated findings (MEDIUM and above)

Findings are sorted by cross-pipeline consensus (number of tools that independently identified the issue), then by severity.

### Deployment and access control issues

| # | Severity | Finding | Pipelines | Addresses |
|---|----------|---------|-----------|-----------|
| D-1 | **HIGH** | **Deployer EOA retains FEE_MANAGER_ROLE.** The deployer `0xa1E340bd1e3ea09B3981164BBB4AfeDdF0e7bA0D` is an EOA that still holds FEE_MANAGER_ROLE, allowing a single private key to change withdrawal fees for all users or set per-user fee overrides. | deployment | `0xa1E3...a0D` |
| D-2 | **MEDIUM** | **Significant signer overlap across all three Safes.** Three EOAs (`0x6A7F...b3`, `0xDD62...0C`, `0x92cf...c8`) appear on all three Safes (Admin 3/5, Processor 2/5, Pauser 2/3). Compromising these three keys gives control over all Safes and cascades to TimelockController. | deployment | Multiple |
| D-3 | **MEDIUM** | **TimelockController admin shared.** The Admin Safe holds DEFAULT_ADMIN_ROLE on the TimelockController, allowing it to grant additional proposer/executor roles without the 24-hour delay. | deployment | `0x0971...8F1` |
| D-4 | **MEDIUM** | **PROCESSOR_ROLE 2-of-5 threshold is low.** The Processor Safe requires only 2-of-5 signatures to execute arbitrary whitelisted calls from the vault's context. | deployment | `0x7e92...Be2c` |

### Code-level findings

| # | Severity | Finding | File : Lines | Pipelines (count) |
|---|----------|---------|-------------|-------------------|
| C-1 | **HIGH** | **Guard silently skips UINT256 parameter validation.** The `Guard.validateCall` loop only validates `ADDRESS`-type parameters. `UINT256` rules configured in `ParamRule` are completely ignored, meaning the processor can pass arbitrary amounts to whitelisted functions. | `Guard.sol:22-28` | trailofbits, pashov, scv-scan, forefy, quillai, archethect, hackenproof, auditmos, cyfrin **(9/9)** |
| C-2 | **HIGH** | **Permissionless `processAccounting()` enables sandwich attacks.** Anyone can call `processAccounting()` to update the cached `totalAssets`, enabling deposit→revalue→redeem sandwich strategies. Combined with donation attacks, this amplifies share price manipulation. | `BaseVault.sol:933-935`, `VaultLib.sol:394-432` | trailofbits, pashov, scv-scan, forefy, quillai, archethect, hackenproof, auditmos, cyfrin **(9/9)** |
| C-3 | **HIGH** | **Public functions with underscore prefix (`_feeOnRaw`, `_feeOnTotal`).** These functions are declared `public` despite the underscore naming convention suggesting internal visibility. They are exposed in the external ABI via IVault, creating a confusing API surface that integrators may incorrectly assume is internal. | `Vault.sol:68-82`, `BaseVault.sol:1021-1029`, `IVault.sol:180-181` | trailofbits, pashov, scv-scan, forefy, quillai, archethect, hackenproof, auditmos, cyfrin **(9/9)** |
| C-4 | **HIGH** | **Hooks contract can mint unbounded shares via `mintShares()`.** The hooks contract (set by `HOOKS_MANAGER_ROLE`) can call `mintShares()` to mint arbitrary shares to any address without updating `totalAssets`, diluting all existing shareholders without limit. | `BaseVault.sol:970-976` | trailofbits, pashov, forefy, quillai, auditmos **(5/9)** |
| C-5 | **HIGH** | **`withdrawAsset` bypasses withdrawal fees.** The `ASSET_WITHDRAWER_ROLE` path through `withdrawAsset()` does not apply any withdrawal fees, unlike the standard `withdraw()` and `redeem()` paths. | `BaseVault.sol:613-629` | trailofbits, pashov, forefy, hackenproof, quillai, auditmos **(6/9)** |
| C-6 | **HIGH** | **Weak first-depositor inflation protection (`+1` virtual offset).** The vault uses `shares.mulDiv(totalAssets + 1, totalSupply + 1)` for share conversion, providing minimal protection against the classic ERC-4626 inflation attack. Industry standard recommends `10^decimalsOffset()` (typically `10^3` to `10^8`). | `VaultLib.sol:285-313` | pashov, forefy, archethect, quillai, auditmos **(5/9)** |
| C-7 | **HIGH** | **Fee-on-transfer token accounting mismatch.** `_deposit()` increments `totalAssets` by the full declared amount before calling `safeTransferFrom`. If a fee-on-transfer token is used as a vault asset, the vault receives fewer tokens than accounted for, inflating share prices. | `BaseVault.sol:535-557` | trailofbits, scv-scan, auditmos, cyfrin **(4/9)** |
| C-8 | **HIGH** | **Provider oracle has no validation.** All asset conversions depend on `IProvider.getRate()` with no staleness check, zero-rate guard, bounds validation, or fallback mechanism. A compromised provider instantly controls all share pricing. | `VaultLib.sol:221-247` | quillai, forefy, auditmos **(3/9)** |
| C-9 | **HIGH** | **No slippage protection on deposits/withdrawals.** None of the `deposit()`, `withdraw()`, `redeem()`, or `mint()` functions accept minimum output parameters (`minSharesOut`/`minAssetsOut`). Combined with the permissionless `processAccounting()`, this enables MEV sandwich attacks. | `BaseVault.sol:280-393` | pashov, auditmos **(2/9)** |
| C-10 | **MEDIUM** | **ETH force-feeding inflates `totalBaseAssets`.** When `countNativeAsset` is true, `computeTotalAssets()` reads `address(this).balance`, which is manipulable via `selfdestruct` (or the `SELFDESTRUCT` replacement) donations, enabling share price inflation. | `VaultLib.sol:374-389` | pashov, scv-scan, quillai, hackenproof, auditmos **(5/9)** |
| C-11 | **MEDIUM** | **CEI violation: `_addTotalAssets` called before `safeTransferFrom`.** In `_deposit()`, the total assets storage is updated before the external token transfer call, violating the Checks-Effects-Interactions pattern. For well-behaved tokens this is protected by `nonReentrant`, but creates a risk window. | `BaseVault.sol:535-557` | trailofbits, pashov, cyfrin, scv-scan **(4/9)** |
| C-12 | **MEDIUM** | **`previewRedeem` fee depends on `_msgSender()`, not share owner.** The fee in `previewRedeem()` is calculated for the caller, not the actual share owner, violating ERC-4626 specification (MUST NOT be dependent on `msg.sender`). Smart contract integrators will get incorrect preview values. | `BaseVault.sol:207-210` | pashov, archethect, quillai **(3/9)** |
| C-13 | **MEDIUM** | **`processor()` lacks `nonReentrant` modifier.** The processor function executes arbitrary external calls from the vault's context but does not have a reentrancy guard, unlike all other state-modifying functions. | `BaseVault.sol:956-963` | quillai, cyfrin, auditmos **(3/9)** |
| C-14 | **MEDIUM** | **Guard parameter decoding incorrect for dynamic types.** The Guard uses fixed 32-byte offsets (`data[4 + i * 32:]`) to decode parameters, which produces incorrect values for functions with dynamic-type parameters (bytes, strings, arrays). The `isArray` field in `ParamRule` is also never checked. | `Guard.sol:24-25` | pashov, hackenproof, archethect **(3/9)** |
| C-15 | **MEDIUM** | **`processor()` not gated by `paused` state.** Six out of seven state-modifying vault functions check `paused`, but `processor()` does not. A compromised `PROCESSOR_ROLE` can move assets during an emergency pause. | `BaseVault.sol:956-963` | quillai **(1/9)** |
| C-16 | **MEDIUM** | **`mintShares()` not gated by `paused` state.** The hooks-only `mintShares()` function can inflate share supply even when the vault is paused for emergencies. | `BaseVault.sol:970-976` | quillai **(1/9)** |
| C-17 | **MEDIUM** | **Incorrect storage slot comment.** `getProcessorStorage()` comment says `keccak256("yieldnest.storage.vault")` but the actual slot value `0x52bb...` does not match `keccak256("yieldnest.storage.vault")`. The vault storage (`getVaultStorage()`) uses the real `keccak256("yieldnest.storage.vault")` hash. | `VaultLib.sol:60-63` | trailofbits, cyfrin, hackenproof **(3/9)** |
| C-18 | **MEDIUM** | **Round-trip conversion drift.** The double conversion path in withdrawals (shares→base→assets→base for `_subTotalAssets`) introduces persistent rounding drift in `totalAssets`, causing a slow leak of accounting precision. | `BaseVault.sol:591`, `VaultLib.sol:285-313` | pashov, auditmos **(2/9)** |

## Positive security observations

Multiple pipelines noted the following strong security practices:

- Correct ERC-4626 rounding directions (floor for user-favourable, ceil for vault-favourable)
- `nonReentrant` on all user-facing deposit/withdrawal functions
- `SafeERC20` used consistently for all token transfers
- `_disableInitializers()` in the implementation constructor prevents re-initialisation
- Granular role-based access control with 11 distinct roles and proper separation of duties
- Vault starts paused on initialisation for safe deployment
- Proxy upgrades protected by 24-hour TimelockController
- Critical management roles (provider, buffer, asset, processor manager) behind timelock
- Deployer EOA properly renounced DEFAULT_ADMIN_ROLE and most other roles
- Custom errors used throughout (no `require` strings)
- Proper use of OpenZeppelin's ERC20PermitUpgradeable (EIP-712)

## Cross-pipeline agreement matrix

Shows how many of the 9 audit pipelines independently flagged each finding:

| Finding | ToB | Pashov | SCV | Forefy | Quill | Archethect | Cyfrin | Hacken | Auditmos |
|---------|:---:|:------:|:---:|:------:|:-----:|:----------:|:------:|:------:|:--------:|
| C-1 Guard skips UINT256 | X | X | X | X | X | X | X | X | X |
| C-2 Permissionless processAccounting | X | X | X | X | X | X | X | X | X |
| C-3 Public underscore functions | X | X | X | X | X | X | X | X | X |
| C-4 Unbounded mintShares | X | X | | X | X | | | | X |
| C-5 withdrawAsset fee bypass | X | X | | X | X | | | X | X |
| C-6 Weak inflation protection | | X | | X | X | X | | | X |
| C-7 Fee-on-transfer mismatch | X | | X | | | | X | | X |
| C-8 No oracle validation | | | | X | X | | | | X |
| C-9 No slippage protection | | X | | | | | | | X |
| C-10 ETH force-feeding | | X | X | | X | | | X | X |
| C-11 CEI violation deposit | X | X | | | | | X | | |
| C-12 previewRedeem caller-dep | | X | | | X | X | | | |
| C-13 processor no nonReentrant | | | | | X | | X | | X |
| C-14 Guard dynamic types | | X | | | | X | | X | |
| C-15 processor ignores paused | | | | | X | | | | |
| C-16 mintShares ignores paused | | | | | X | | | | |
| C-17 Wrong storage comment | X | | | | | | X | X | |
| C-18 Rounding drift | | X | | | | | | | X |
