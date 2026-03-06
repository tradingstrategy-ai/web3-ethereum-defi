# YieldNest ynRWAx vault deployment report

## Overview

| Property | Value |
|----------|-------|
| Vault name | YieldNest RWA MAX |
| Symbol | ynRWAx |
| Decimals | 18 |
| Vault version | 0.4.2 |
| Chain | Ethereum mainnet |
| Deployment block | 22674309 (2025-06-10 12:57:47 UTC) |
| Paused | No |
| Always compute total assets | Yes |
| Hooks contract | Not set (zero address) |
| Buffer strategy | Not set (zero address) |
| Total supply | ~5,141,910 shares |
| Total assets | ~0.000005 (base units) |

The vault has been upgraded once since deployment:
- Block 22674309: initial implementation `0xc1C5B18774d0282949331b719b5EA4A21CbC62C8`
- Block 24518374: upgraded to current implementation `0xb46D7014C1A29b6A82D8eCDE5aD29d5B09aC7A1b`

### Supported assets

| Index | Address | Name | Symbol |
|-------|---------|------|--------|
| 0 (base) | `0xdA7d2025c7f1f1A1d34AB3F4dF01102d0428E574` | Wrapped USDC | WUSDC |
| 1 (default) | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | USD Coin | USDC |
| 2 | `0xF6e1443e3F70724cec8C0a779C7C35A8DcDA928B` | YieldNest USDC Flex Strategy - ynRWAx - SPV1 | ynFlex-USDC-ynRWAx-SPV1 |

## Table 1: Contract addresses

| Name | Address | Type | Source |
|------|---------|------|--------|
| Vault (Proxy) | `0x01Ba69727E2860b37bc1a2bd56999c1aFb4C15D8` | TransparentUpgradeableProxy | OpenZeppelin |
| Vault (Implementation) | `0xb46D7014C1A29b6A82D8eCDE5aD29d5B09aC7A1b` | Vault v0.4.2 | `src/Vault.sol` inheriting `src/src/BaseVault.sol` |
| Vault (Original implementation) | `0xc1C5B18774d0282949331b719b5EA4A21CbC62C8` | Vault (prior version) | Superseded |
| ProxyAdmin | `0x90ae998b7920F7C5eB27d792F710Ac5EaCf5c7DC` | OpenZeppelin ProxyAdmin | `Common.sol` imports |
| TimelockController | `0x0971628c7D3C6009D309165fEDCc47A12e5158F1` | OpenZeppelin TimelockController (86400s / 24h delay) | `Common.sol` imports |
| Provider | `0x5Ecf4465EB7D39EF3F15B6C0a13356c552508788` | Contract (608 bytes) | External |
| StrategyKeeper | `0x68521bE2613785A0E4710caE32D8F3219f05b6D2` | Verified StrategyKeeper v0.1.0 (9434 bytes) | External - monitors vault balances, allocates to strategy |
| FlexStrategyLeverageKeeper | `0x6920C7c9a66EdEa563b1aEcb8CA8097f811CbFc5` | Verified FlexStrategyLeverageKeeper (14232 bytes) | External - harvests yield from vault positions |

## Table 2: Privileged addresses and role assignments

### Vault roles (AccessControlUpgradeable)

All roles are administered by `DEFAULT_ADMIN_ROLE`. Only the holder of `DEFAULT_ADMIN_ROLE` can grant or revoke any role.

| Role | Address | Label | Type | Signer setup |
|------|---------|-------|------|-------------|
| **DEFAULT_ADMIN_ROLE** | `0xfcad670592a3b24869C0b51a6c6FDED4F95D6975` | YieldNest Admin Safe | Gnosis Safe 1.3.0 (EIP-1167 proxy) | **3-of-5** multisig |
| FEE_MANAGER_ROLE | `0xfcad670592a3b24869C0b51a6c6FDED4F95D6975` | YieldNest Admin Safe | Gnosis Safe 1.3.0 (EIP-1167 proxy) | 3-of-5 multisig |
| UNPAUSER_ROLE | `0xfcad670592a3b24869C0b51a6c6FDED4F95D6975` | YieldNest Admin Safe | Gnosis Safe 1.3.0 (EIP-1167 proxy) | 3-of-5 multisig |
| FEE_MANAGER_ROLE | `0xa1E340bd1e3ea09B3981164BBB4AfeDdF0e7bA0D` | YieldNest: Deployer | **EOA** | Single key |
| PROCESSOR_ROLE | `0x7e92AbC00F58Eb325C7fC95Ed52ACdf74584Be2c` | YieldNest Processor Safe | Gnosis Safe 1.3.0 (EIP-1167 proxy) | **2-of-5** multisig |
| PROCESSOR_ROLE | `0x68521bE2613785A0E4710caE32D8F3219f05b6D2` | StrategyKeeper | Verified contract | Automated keeper |
| PAUSER_ROLE | `0xa08F39d30dc865CC11a49b6e5cBd27630D6141C3` | YieldNest Pauser Safe | Gnosis Safe 1.3.0 (EIP-1167 proxy) | **2-of-3** multisig |
| PROVIDER_MANAGER_ROLE | `0x0971628c7D3C6009D309165fEDCc47A12e5158F1` | TimelockController | OpenZeppelin TimelockController | 24-hour delay |
| BUFFER_MANAGER_ROLE | `0x0971628c7D3C6009D309165fEDCc47A12e5158F1` | TimelockController | OpenZeppelin TimelockController | 24-hour delay |
| ASSET_MANAGER_ROLE | `0x0971628c7D3C6009D309165fEDCc47A12e5158F1` | TimelockController | OpenZeppelin TimelockController | 24-hour delay |
| PROCESSOR_MANAGER_ROLE | `0x0971628c7D3C6009D309165fEDCc47A12e5158F1` | TimelockController | OpenZeppelin TimelockController | 24-hour delay |
| ASSET_WITHDRAWER_ROLE | `0x6920C7c9a66EdEa563b1aEcb8CA8097f811CbFc5` | FlexStrategyLeverageKeeper | Verified contract | Automated keeper |
| HOOKS_MANAGER_ROLE | *Not assigned* | -- | -- | -- |

### TimelockController roles (0x0971...8F1)

The TimelockController has a **24-hour minimum delay** (86400 seconds) and controls the ProxyAdmin as well as the vault's manager roles.

| Timelock role | Address | Type |
|--------------|---------|------|
| DEFAULT_ADMIN_ROLE | `0x0971628c7D3C6009D309165fEDCc47A12e5158F1` | Self (TimelockController) |
| DEFAULT_ADMIN_ROLE | `0xfcad670592a3b24869C0b51a6c6FDED4F95D6975` | YieldNest Admin Safe (3/5) |
| PROPOSER_ROLE | `0xfcad670592a3b24869C0b51a6c6FDED4F95D6975` | YieldNest Admin Safe (3/5) |
| EXECUTOR_ROLE | `0xfcad670592a3b24869C0b51a6c6FDED4F95D6975` | YieldNest Admin Safe (3/5) |
| CANCELLER_ROLE | `0xfcad670592a3b24869C0b51a6c6FDED4F95D6975` | YieldNest Admin Safe (3/5) |

### ProxyAdmin ownership

| Property | Address | Type |
|----------|---------|------|
| ProxyAdmin | `0x90ae998b7920F7C5eB27d792F710Ac5EaCf5c7DC` | OpenZeppelin ProxyAdmin |
| ProxyAdmin owner | `0x0971628c7D3C6009D309165fEDCc47A12e5158F1` | TimelockController (24h delay) |

### Safe multisig signer details

**YieldNest Admin Safe** (`0xfcad670592a3b24869C0b51a6c6FDED4F95D6975`) -- 3-of-5 threshold:

| Signer | Type | Also signer on |
|--------|------|---------------|
| `0xE27B5c80DE762cd47f824515f845CB4bec881F88` | EOA | Admin Safe only |
| `0x6A7Ff17e8347e7EAd5856c83299ACb506Cb878b3` | EOA | Admin, Processor, Pauser Safes |
| `0xDD62d882ca6bE24d08D0067A4660d9165eb9F80C` | EOA | Admin, Processor, Pauser Safes |
| `0xF522712DdAb999493D716eD681D8a0fb5C5FdC90` | EOA | Admin, Processor Safes |
| `0x92cfFf81BD9D3ca540d3ee7e7d26A67b47FdB7c8` | EOA | Admin, Processor, Pauser Safes |

**YieldNest Processor Safe** (`0x7e92AbC00F58Eb325C7fC95Ed52ACdf74584Be2c`) -- 2-of-5 threshold:

| Signer | Type |
|--------|------|
| `0x296D28BBBdaFAacc69881005bF1db399Cc1028e3` | EOA |
| `0xF522712DdAb999493D716eD681D8a0fb5C5FdC90` | EOA |
| `0x92cfFf81BD9D3ca540d3ee7e7d26A67b47FdB7c8` | EOA |
| `0xDD62d882ca6bE24d08D0067A4660d9165eb9F80C` | EOA |
| `0x6A7Ff17e8347e7EAd5856c83299ACb506Cb878b3` | EOA |

**YieldNest Pauser Safe** (`0xa08F39d30dc865CC11a49b6e5cBd27630D6141C3`) -- 2-of-3 threshold:

| Signer | Type |
|--------|------|
| `0xDD62d882ca6bE24d08D0067A4660d9165eb9F80C` | EOA |
| `0x92cfFf81BD9D3ca540d3ee7e7d26A67b47FdB7c8` | EOA |
| `0x6A7Ff17e8347e7EAd5856c83299ACb506Cb878b3` | EOA |

## Access control architecture diagram

```
ProxyAdmin (0x90ae...5edc)
  |
  owner --> TimelockController (0x0971...8f1, 24h delay)
               |
               proposer/executor/canceller --> Admin Safe 3/5 (0xfcad...6975)
               admin_role --> Admin Safe 3/5 + self


Vault (0x01Ba...15D8) AccessControl:
  |
  DEFAULT_ADMIN_ROLE --------> Admin Safe 3/5 (0xfcad...6975)
  |   (can grant/revoke all roles)
  |
  +-- FEE_MANAGER_ROLE ------> Admin Safe 3/5 (0xfcad...6975)
  |                             Deployer EOA (0xa1E3...a0D)  [!]
  |
  +-- UNPAUSER_ROLE ----------> Admin Safe 3/5 (0xfcad...6975)
  +-- PAUSER_ROLE ------------> Pauser Safe 2/3 (0xa08F...1C3)
  |
  +-- PROCESSOR_ROLE ---------> Processor Safe 2/5 (0x7e92...Be2c)
  |                             StrategyKeeper contract (0x6852...b6D2)
  |
  +-- PROVIDER_MANAGER_ROLE --> TimelockController (0x0971...8f1)
  +-- BUFFER_MANAGER_ROLE ----> TimelockController (0x0971...8f1)
  +-- ASSET_MANAGER_ROLE -----> TimelockController (0x0971...8f1)
  +-- PROCESSOR_MANAGER_ROLE -> TimelockController (0x0971...8f1)
  |
  +-- ASSET_WITHDRAWER_ROLE --> FlexStrategyLeverageKeeper (0x6920...fDC5)
  +-- HOOKS_MANAGER_ROLE -----> (not assigned)
```

## Role capability summary

| Role | Capabilities |
|------|-------------|
| DEFAULT_ADMIN_ROLE | Grant and revoke all roles |
| FEE_MANAGER_ROLE | Set base withdrawal fee; override per-user withdrawal fees |
| PROCESSOR_ROLE | Execute arbitrary calls to whitelisted targets via `processor()` |
| PAUSER_ROLE | Pause the vault (blocks deposits/withdrawals) |
| UNPAUSER_ROLE | Unpause the vault |
| PROVIDER_MANAGER_ROLE | Set the provider (price oracle / rate provider) |
| BUFFER_MANAGER_ROLE | Set the buffer strategy (withdrawal liquidity source) |
| ASSET_MANAGER_ROLE | Add/update/delete supported assets; toggle `alwaysComputeTotalAssets` |
| PROCESSOR_MANAGER_ROLE | Configure which target+function combinations the PROCESSOR_ROLE can call |
| HOOKS_MANAGER_ROLE | Set the hooks contract (deposit/withdraw/mint/redeem callbacks) |
| ASSET_WITHDRAWER_ROLE | Withdraw specific assets directly from vault holdings (bypasses buffer) |

## Security observations

### Positive findings

1. **Proxy upgrade protected by timelock.** The ProxyAdmin is owned by a TimelockController with a 24-hour delay, preventing immediate malicious upgrades. The timelock is controlled by a 3-of-5 Safe multisig.

2. **Critical configuration roles behind timelock.** PROVIDER_MANAGER, BUFFER_MANAGER, ASSET_MANAGER, and PROCESSOR_MANAGER roles are all assigned to the TimelockController, giving a 24-hour window to detect and react to malicious configuration changes.

3. **DEFAULT_ADMIN_ROLE held by multisig, not EOA.** The deployer EOA (`0xa1E3...a0D`) properly renounced its DEFAULT_ADMIN_ROLE after granting it to the 3/5 Admin Safe.

4. **Deployer renounced most roles.** The deployer revoked its own DEFAULT_ADMIN, PROCESSOR_MANAGER, BUFFER_MANAGER, PROVIDER_MANAGER, ASSET_MANAGER, and UNPAUSER roles during deployment.

5. **Appropriate separation of duties.** Operational roles (PROCESSOR, PAUSER) use lower-threshold Safes (2/5, 2/3) for faster response, while governance roles use the higher-threshold 3/5 Safe or the timelock.

### Concerns

1. **Deployer EOA retains FEE_MANAGER_ROLE.** The deployer EOA `0xa1E340bd1e3ea09B3981164BBB4AfeDdF0e7bA0D` still holds FEE_MANAGER_ROLE. This allows a single private key to change withdrawal fees for all users or grant per-user fee overrides. While the impact is limited to fee manipulation (not fund theft), this role should ideally be revoked from the EOA, leaving it only on the Admin Safe.

2. **PROCESSOR_ROLE is high-privilege.** The PROCESSOR_ROLE can execute arbitrary calls from the vault to any whitelisted target. The whitelisting (managed by PROCESSOR_MANAGER_ROLE behind the timelock) mitigates this, but the 2/5 threshold on the Processor Safe is relatively low. If 2 of the 5 signers are compromised, they can execute any whitelisted operation.

3. **Significant signer overlap across Safes.** Three EOAs (`0x6A7F...b3`, `0xDD62...0C`, `0x92cf...c8`) appear as signers on all three Safes (Admin, Processor, Pauser). Compromising these three keys would give an attacker control over all operational Safes and the Admin Safe simultaneously, which would also cascade to TimelockController control (propose + execute).

4. **TimelockController admin role shared.** Both the TimelockController itself and the Admin Safe hold DEFAULT_ADMIN_ROLE on the TimelockController. This means the Admin Safe can grant additional proposer/executor roles to other addresses without a time delay, potentially bypassing the timelock's protective delay for future operations.

5. **Buffer strategy is zero address.** The buffer is currently set to the zero address, which means `maxWithdraw()` and `maxRedeem()` return 0 for all users. Standard ERC-4626 withdrawals are effectively disabled.

6. **HOOKS_MANAGER_ROLE unassigned.** No address holds HOOKS_MANAGER_ROLE. The hooks contract is currently not set (zero address). If hooks are needed in the future, the DEFAULT_ADMIN_ROLE (Admin Safe) can grant this role to an appropriate address. This is not necessarily a concern but is worth noting for completeness.

7. **ASSET_WITHDRAWER_ROLE on automated keeper.** The FlexStrategyLeverageKeeper contract holds ASSET_WITHDRAWER_ROLE, which allows it to withdraw assets directly from vault holdings and burn shares from any owner. The security of this depends on the keeper contract's own access control, which should be audited separately.
