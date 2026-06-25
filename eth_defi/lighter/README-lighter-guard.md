# Lighter guard integration architecture

Security architecture for depositing into and withdrawing from
[Lighter](https://lighter.xyz) (a non-custodial, zero-fee perpetuals/spot DEX
built as a zk-rollup on Ethereum L1) through an asset-managed Gnosis **Safe**
controlled by `TradingStrategyModuleV0` / `GuardV0`.

This document describes how the on-chain guard whitelisting works. It mirrors
the GMX (`eth_defi/gmx/README-GMX-Lagoon.md`) and Hypercore
(`docs/README-Hypercore-guard.md`) guard integrations.

> Guard scope: **deposit / withdraw only** (the on-chain L1 custody flow).
> Account registration is off-chain (EIP-712 / EIP-1271 Safe signature, no guard
> call). On-book trading (`createOrder`) and trading-key rotation
> (`changePubKey`) happen off-chain / are out of scope for the guard (a follow-up
> may add on-chain order validation to `LighterLib`).

## Why a Safe can use Lighter

Lighter holds all user funds in an L1 smart contract (`ZkLighter`) and keys
accounts by the **Ethereum address** that controls them. That address can be a
smart-contract wallet, so a Safe multisig can custody funds on Lighter:

- **Deposits / withdrawals** are ordinary L1 contract calls the Safe can make
  through the module.
- **Account linking** ties a Lighter account to the Safe address via an
  off-chain Safe signature (EIP-712 / EIP-1271, no gas). The day-to-day L2
  trading key is delegated separately; its on-L1 rotation (`changePubKey`) is
  out of scope for this guard whitelist.
- **Funds stay in the L1 contract** until withdrawn with a valid zk-proof +
  the controlling address, so withdrawals remain Safe-gated.

## Architecture

```
Asset Manager ──▶ TradingStrategyModuleV0 (GuardV0) ──▶ Gnosis Safe (vault funds)
                          │ validateCall()
                          ▼
                  LighterLib.validateCall()  (external lib, DELEGATECALL)
                          │
                          ▼
                  ZkLighter L1 contract (deposit / withdraw / withdrawPendingBalance)
```

- **`GuardV0Base.sol`** holds the generic call-site / token / receiver allow
  lists and the `validateCall()` dispatch. `whitelistLighter()` registers the
  Lighter call sites, the USDC approval destination and the USDC token.
- **`LighterLib.sol`** is an external Forge library (same pattern as `GmxLib` /
  `HypercoreVaultLib`): diamond storage at `keccak256("eth_defi.lighter.v1")`,
  invoked via `DELEGATECALL` so its bytecode does not count against the guard's
  EIP-170 24 KB limit. It keeps protocol-specific allow-sets — allowed
  `ZkLighter` contract(s) and allowed asset indices (USDC) — in its own storage
  (analogous to `GmxLib`'s `allowedRouters` / `allowedMarkets`), and uses
  `IGuardChecks(address(this)).isAllowedReceiver(...)` callbacks for receiver
  checks, exactly like `GmxLib`.
- **Safe** is the fund holder and the only valid recipient of deposits/credits
  and withdrawals — enforced by the guard's `isAllowedReceiver` (set via
  `allowReceiver(safe)`).

## Lighter L1 contract (Ethereum mainnet)

| Item | Value |
|------|-------|
| Proxy (`Proxy`) | `0x3b4d794a66304f130a4db8f2551b0070dfcf5ca7` |
| Implementation (`ZkLighter`) | `0x831ef69bab8af8b1037a4961b8d0674b124e7008` |
| Chain | Ethereum mainnet (chain id 1) |
| Deposit asset | USDC (keyed by `uint16` asset index, see `USDC_ASSET_INDEX()`) |

Lighter keys deposits by **asset index**, not token address. Helpers on the
contract: `USDC_ASSET_INDEX()`, `tokenToAssetIndex(address)`,
`addressToAccountIndex(address)`, `getPendingBalance(address,uint16)`.

## Whitelisted activities

`whitelistLighter(zkLighter, usdc, assetIndex, notes)` registers the call sites
below, plus `allowApprovalDestination(zkLighter)` (for the USDC `approve`) and
the USDC token, and seeds the library's allowed-contract and allowed-asset-index
(USDC) sets. The operator must additionally call `allowReceiver(safe)` so the
Safe is a valid recipient (same requirement as `whitelistGMX`).

| Selector | Function | Guard validation |
|----------|----------|------------------|
| `0x095ea7b3` | `approve(ZkLighter, amount)` on USDC | Approval destination whitelisted |
| `0x8a857083` | `deposit(address _to, uint16 _assetIndex, uint8, uint256)` | `isAllowedReceiver(_to)` (Safe) **and** `_assetIndex` ∈ allowed assets (USDC) |
| `0xd20191bd` | `withdraw(uint48 _accountIndex, uint16 _assetIndex, uint8, uint64)` | `_assetIndex` ∈ allowed assets; moves balance to *pending* (no external recipient) |
| `0x2f25807e` | `withdrawPendingBalance(address _owner, uint16 _assetIndex, uint128)` | `isAllowedReceiver(_owner)` (Safe) **and** `_assetIndex` ∈ allowed assets |

`changePubKey(uint48,uint8,bytes)` (`0x17010c68`) is **not** whitelisted — it is
trading-key rotation, a follow-up that needs an account-index/API-key policy.

## Fund-flow security model

A compromised asset-manager hot wallet must not be able to drain the Safe via
Lighter. Following the `GmxLib` pattern, receiver checks go through the guard's
`isAllowedReceiver` (set by `allowReceiver(safe)`); `LighterLib` adds an
`allowedAssetIndices` (USDC) check from its own storage. The fund-egress vectors
and their checks:

1. **`deposit(_to, _assetIndex, …)`** — `_to` is credited on Lighter. Without a
   check an attacker could credit *their own* account with the Safe's USDC.
   → `require(isAllowedReceiver(_to))` and `require(allowedAssetIndices[_assetIndex])`.
2. **`withdrawPendingBalance(_owner, _assetIndex, …)`** — `_owner` receives the
   released L1 funds. → same two checks on `_owner` and `_assetIndex`.
3. **`withdraw(_accountIndex, _assetIndex, …)`** — **not a fund-egress vector.**
   It only moves an account's balance into that account's *pending balance*; no
   tokens leave `ZkLighter` and there is no recipient parameter. The only L1
   egress is `withdrawPendingBalance` (item 2), receiver-checked to the Safe, so
   funds can only ever be claimed out to a whitelisted Safe. →
   `require(allowedAssetIndices[_assetIndex])` only. `_accountIndex` is not bound
   to the Safe (validateCall has no Safe address). The `ZkLighter` contract
   binds the withdrawal to `msg.sender`, not the supplied `_accountIndex` —
   `withdraw()` sets `masterAccountIndex = validateAndGetAccountIndexFromAddress(msg.sender)`
   (reverting `AdditionalZkLighter_AccountIsNotRegistered()` for an unregistered
   caller). So a compromised asset manager cannot withdraw a foreign account
   through the guard — verified end-to-end by
   `tests/guard/test_guard_lighter_lagoon.py::test_guard_lighter_withdraw_account_index_bound_by_protocol`,
   which asserts the guard permits the call but the protocol reverts with that
   exact error. `approve` is bounded by the approval destination + USDC token.

**`anyAsset` joker.** When the guard runs in "allow all assets" mode
(`anyAsset == true`), the asset-index check is skipped — receiver checks still
run. Same semantics as `GmxLib.isAllowedMarket(market, anyAsset)`. An
`anyAsset` vault may deposit any Lighter asset to the Safe but still cannot
credit/withdraw to a non-Safe receiver. The guard passes its `anyAsset` state
into `LighterLib.validateCall(selector, target, callData, anyAsset)`.

## Deposit flow (one-time approval + deposit)

```
1. approve:  performCall(USDC, approve(ZkLighter, amount), 0)
2. deposit:  performCall(ZkLighter, deposit(safe, USDC_ASSET_INDEX, routeType, amount), 0)
```

Inner effect: the Safe approves USDC to `ZkLighter`, then `ZkLighter` pulls the
USDC and credits the Safe's Lighter account.

## Withdraw flow (request + claim)

```
1. request:  performCall(ZkLighter, withdraw(accountIndex, USDC_ASSET_INDEX, routeType, amount), 0)
   ... wait for the zk-proof to settle on L1 ...
2. claim:    performCall(ZkLighter, withdrawPendingBalance(safe, USDC_ASSET_INDEX, amount), 0)
```

Funds are released on-chain directly to the Safe address.

## Account registration (off-chain — not a guard call)

Linking a Lighter account to the Safe is done **off-chain** by signing a
message with the Safe (EIP-712 / EIP-1271, no gas, no on-chain transaction).
The Safe becomes the L1 root owner of the Lighter account. This step does not
go through the guard.

Day-to-day trading is then done off-chain via a delegated L2 trading key. On
L1, that key is rotated with `changePubKey` — which is **out of scope** for
this guard whitelist (a follow-up needing an account-index/API-key policy). Do
not confuse `changePubKey` (trading-key rotation) with account registration.
(Confirm the exact off-chain linking flow against
[docs.lighter.xyz](https://docs.lighter.xyz).)

## Operator setup (Python)

```python
from eth_defi.lighter.deployment import (
    LighterDeployment,
    setup_lighter_whitelisting,
)

setup_lighter_whitelisting(
    web3=web3,
    module=module,                      # TradingStrategyModuleV0 / GuardV0
    owner=safe_address,
    deployment=LighterDeployment.create_ethereum(),
    safe_address=safe_address,          # allowReceiver(safe)
)
```

`setup_lighter_whitelisting()` reads the USDC asset index from the ZkLighter
contract (`USDC_ASSET_INDEX()`) and calls `whitelistLighter(zkLighter, usdc,
usdcAssetIndex, notes)` + `allowReceiver(safe)` (preferably batched via
`multicall`). It lives in
`eth_defi/lighter/deployment.py` (the production module) and is also called by
the Lagoon vault deployment flow (`eth_defi/erc_4626/vault_protocol/lagoon/
deployment.py`) when a `lighter_deployment` is configured. The Anvil-fork
tests reuse the same function.

## Limitations (current scope)

- **USDC-only.** A single deposit asset (USDC) is whitelisted; its asset index
  is read from `ZkLighter.USDC_ASSET_INDEX()`. Additional assets are not
  supported yet — `whitelistLighter` takes one `assetIndex` per call (each call
  adds to `LighterLib.allowedAssetIndices`), and there is no multi-asset
  convenience helper or multicall batching like
  `setup_hypercore_whitelisting`.
- **Deposit / withdraw only.** On-book trading (`createOrder`) and trading-key
  rotation (`changePubKey`) are out of scope; trading happens off-chain via the
  Lighter L2 API. See the manual tutorial
  `scripts/lagoon/lagoon-lighter-example.py` for the end-to-end lifecycle
  (the off-chain trading steps cannot be simulated on a fork, like GMX keepers).

## Deployment note (library linking)

`LighterLib` is an external library: it must be **deployed and linked** into the
guard/module at deployment time, exactly like `GmxLib` and `HypercoreVaultLib`
(passed via `deploy_contract(..., libraries={"LighterLib": <addr>, ...})`).

## Manual test recipe

On an Anvil **Ethereum mainnet fork** (Lighter is L1):

1. Deploy the guard with `LighterLib` linked; run `setup_lighter_whitelisting`
   (`whitelistLighter(zkLighter, usdc, usdcAssetIndex, notes)` + `allowReceiver(safe)`).
2. Assert `validateCall` accepts `approve`, `deposit(safe, USDC_ASSET_INDEX, …)`,
   `withdraw(accountIndex, USDC_ASSET_INDEX, …)`,
   `withdrawPendingBalance(safe, USDC_ASSET_INDEX, …)`.
3. Assert `validateCall` reverts on `deposit(attacker, …)`,
   `deposit(safe, WRONG_ASSET_INDEX, …)`, `withdrawPendingBalance(attacker, …)`,
   `changePubKey(…)` (not in scope), and any non-whitelisted selector.

## References

- Lighter docs: <https://docs.lighter.xyz>
- `ZkLighter` on Etherscan: <https://etherscan.io/address/0x831ef69bab8af8b1037a4961b8d0674b124e7008>
- Guard core: `contracts/guard/src/GuardV0Base.sol`
- Lighter library: `contracts/guard/src/lib/LighterLib.sol`
- Reference integrations: `eth_defi/gmx/README-GMX-Lagoon.md`,
  `docs/README-Hypercore-guard.md`
