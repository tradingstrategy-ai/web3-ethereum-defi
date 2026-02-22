# Hypercore native vault guard integration

Guard support for depositing USDC from a guarded Safe multisig on HyperEVM (chain 999) into Hypercore native vaults (chain 9999) via the CoreWriter system contract.

## Whitelisted activities

The guard enables three CoreWriter action IDs through `sendRawAction(bytes)`:

| Action ID | Name | Parameters | Purpose |
|-----------|------|-----------|---------|
| 2 | `vaultTransfer` | `(address vault, bool isDeposit, uint64 usd)` | Deposit/withdraw from a Hypercore native vault |
| 6 | `spotSend` | `(address destination, uint64 token, uint64 wei)` | Bridge tokens from Core to EVM (withdrawal step) |
| 7 | `transferUsdClass` | `(uint64 ntl, bool toPerp)` | Move USDC between spot and perp accounts |

Additionally, the CoreDepositWallet is whitelisted for:

| Function | Selector | Purpose |
|----------|----------|---------|
| `deposit(uint256,uint32)` | `0x2b2dfd2c` | Bridge USDC from EVM to Core spot account |

All other CoreWriter action IDs (limit orders, staking, cancel, etc.) are rejected by the guard.

## System addresses

| Contract | Address | Notes |
|----------|---------|-------|
| CoreWriter | `0x3333333333333333333333333333333333333333` | System contract for EVM-to-Core writes |
| CoreDepositWallet (mainnet) | `0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24` | USDC bridging |
| CoreDepositWallet (testnet) | `0x0B80659a4076E9E93C7DbE0f10675A16a3e5C206` | Testnet USDC bridging |
| USDC (mainnet) | `0xb88339CB7199b77E23DB6E890353E22632Ba630f` | HyperEVM native USDC |
| USDC (testnet) | `0x2B3370eE501B4a559b57D449569354196457D8Ab` | Testnet USDC |
| Vault equity precompile | `0x0000000000000000000000000000000000000802` | Read vault equity/lock status |

## Deposit flow

Four `performCall` transactions through TradingStrategyModuleV0:

1. **Approve USDC** to CoreDepositWallet
   - Target: USDC contract
   - Function: `approve(CoreDepositWallet, amount)`
   - Guard: validates approval destination is whitelisted

2. **Bridge USDC to Core** spot account
   - Target: CoreDepositWallet
   - Function: `deposit(amount, SPOT_DEX)`
   - Guard: validates target is allowed CoreDepositWallet

3. **Move USDC spot -> perp**
   - Target: CoreWriter
   - Function: `sendRawAction(transferUsdClass(amount, true))`
   - Guard: validates CoreWriter target, action ID 7 allowed

4. **Deposit to vault**
   - Target: CoreWriter
   - Function: `sendRawAction(vaultTransfer(vault, true, amount))`
   - Guard: validates CoreWriter target, action ID 2 allowed, vault address whitelisted

## Withdrawal flow

Three `performCall` transactions:

1. **Withdraw from vault**
   - Target: CoreWriter
   - Function: `sendRawAction(vaultTransfer(vault, false, amount))`
   - Guard: validates vault address whitelisted

2. **Move USDC perp -> spot**
   - Target: CoreWriter
   - Function: `sendRawAction(transferUsdClass(amount, false))`
   - Guard: validates action ID 7 allowed

3. **Bridge USDC to EVM**
   - Target: CoreWriter
   - Function: `sendRawAction(spotSend(safe_address, USDC_TOKEN, amount))`
   - Guard: validates action ID 6, destination is an allowed receiver

## Multicall batching

Both the deposit and withdrawal flows can be batched into a single EVM transaction
via `TradingStrategyModuleV0.multicall(bytes[])`. The module inherits `Multicall`
from `GuardV0Base`, which uses `delegatecall` to execute each inner call — preserving
`msg.sender` so guard validation works correctly within each batched `performCall`.

When the EVM block finishes execution, all queued CoreWriter actions are processed
sequentially on HyperCore (~47k gas per action). This is implicit batching at the
block level.

Python helpers for building multicall transactions:

```python
from eth_defi.hyperliquid.core_writer import (
    build_hypercore_deposit_multicall,
    build_hypercore_withdraw_multicall,
    get_core_deposit_wallet_contract,
    CORE_DEPOSIT_WALLET_MAINNET,
)

# Single-transaction deposit (4 steps batched)
cdw = get_core_deposit_wallet_contract(web3, CORE_DEPOSIT_WALLET_MAINNET)
fn = build_hypercore_deposit_multicall(
    module=module,
    usdc_contract=usdc_contract,
    core_deposit_wallet=cdw,
    core_writer=core_writer,
    evm_usdc_amount=10_000 * 10**6,
    hypercore_usdc_amount=10_000 * 10**6,
    vault_address="0x...",
)
tx_hash = fn.transact({"from": asset_manager})

# Single-transaction withdrawal (3 steps batched)
fn = build_hypercore_withdraw_multicall(
    module=module,
    core_writer=core_writer,
    hypercore_usdc_amount=10_000 * 10**6,
    vault_address="0x...",
    safe_address=safe.address,
)
tx_hash = fn.transact({"from": asset_manager})
```

## Guard security model

The guard prevents:

- **Unauthorised vaults**: only explicitly whitelisted vault addresses can receive deposits
- **Forbidden action IDs**: limit orders (1), staking (3-5), cancel (10-11), and all other actions are blocked
- **Wrong receivers**: `spotSend` destination must be in `allowedReceivers` (typically the Safe itself)
- **Wrong CoreWriter**: only the whitelisted CoreWriter address is accepted
- **Wrong CoreDepositWallet**: only the whitelisted CoreDepositWallet address is accepted

## Async write behaviour and critical gotchas

Based on [Chase Manning's article](https://x.com/chase_manning_/status/2014370671514538379) (9 months of production experience building on Hypercore) and the [Hyperliquid documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/interacting-with-hypercore).

### Atomic vs async

CoreWriter actions are **not atomic**. When `sendRawAction()` succeeds on HyperEVM, it only means the action was queued. The action is processed by HyperCore validators with a few seconds delay. **An action can succeed on EVM but later fail on HyperCore**, with no revert propagation back to EVM.

This means the 4-step deposit flow can partially fail. For example, USDC could be bridged to Core (step 2) but the vault deposit (step 4) could fail on Core, leaving funds in the Safe's perp account rather than in the vault.

**Mitigation**: validate preconditions before submitting actions. Never assume a CoreWriter action has succeeded. Verify via precompile reads in subsequent blocks.

### Order of events / balance gap

When bridging USDC from EVM to Core, the USDC is taken from the EVM balance **immediately**, but the Core balance is not updated until the **next EVM block**. For a brief period within the same block, funds "disappear" from both balances.

Any precompile read (e.g., vault equity at `0x802`) in the same block as a CoreWriter action returns **stale data**. Precompiles return data from the start of the block.

**Mitigation**: track in-flight amounts; do not rely on precompile reads in the same block as a CoreWriter action. Wait at least one block before reading updated state.

### USDC bridge quirks

- **`dexForwarding` can be disabled**: Circle can disable `dexForwarding` at any time, breaking direct EVM-to-perp bridging. The guard uses the spot-first flow (bridge to spot, then transfer to perp) which is more resilient.
- **Bridge not backed 1:1**: more USDC exists on HyperCore than in the HyperEVM bridge contract. The bridge can **run dry**, causing Core-to-EVM transfers (`spotSend`) to fail with no remediation other than waiting for the bridge to be replenished.
- For vault withdrawals, this means funds could be stuck on HyperCore if the bridge is depleted.

### Fees and activation

- **Contract activation**: smart contracts must be "activated" on HyperCore before CoreWriter actions work. Send 1-2 USDC to the contract's Core address to trigger activation. For a Safe, the Safe address is the Core identity.
- **Bridging fees (Core -> EVM)**: requires HYPE or USDC on HyperCore spot for fees. HYPE is consumed first.
- **Bridging fees (EVM -> Core)**: requires HYPE on HyperEVM for gas.
- The Safe needs its HYPE balance managed to ensure bridging operations can pay fees.

### Historic precompile reads

No RPC provider supports querying precompile views at historic blocks. Only `latest` works. Any blockchain indexer using a backfill architecture will fail on historic precompile queries.

**Schrödinger's balance**: between blocks during a bridge, RPC providers may return 0 for both spot and perp balances. The funds "exist" for EVM transactions in that block but not for external RPC reads.

**Mitigation**: use event logs to detect bridges and adjust reported balances. Do not rely on precompile reads for balance calculations during or immediately after bridge operations.

### Vault lock-up

- User-created vaults: **1 day** lock-up after deposit before withdrawal.
- Protocol vaults (HLP): **4 day** lock-up.
- The `vaultTransfer(withdraw)` action will fail on HyperCore if the lock-up has not expired. The `hyper-evm-lib` library checks the lock via the vault equity precompile before submitting the action.

## Manual testing

A manual test script deploys a Lagoon vault on a HyperEVM Anvil fork and exercises
the full deposit/withdrawal flow:

```shell
source .local-test.env && poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py
```

See the script's docstring for environment variables and account funding instructions.

## Contract size

`TradingStrategyModuleV0` is the critical contract for size — it inherits all guard
logic from `GuardV0Base` and sits at 99.3% of the EIP-170 24,576-byte limit.
See [README-contract-size.md](README-contract-size.md) for contract sizes,
compiler options, and size optimisation techniques.

## Source material

- [hyper-evm-lib](https://github.com/hyperliquid-dev/hyper-evm-lib) -- canonical Solidity library (MIT, maintained by Obsidian Audits)
- [Chase Manning: Hyperliquid Precompiles - An Inconvenient Truth](https://x.com/chase_manning_/status/2014370671514538379)
- [Ambit Labs: Demystifying Precompiles and CoreWriter](https://medium.com/@ambitlabs/demystifying-the-hyperliquid-precompiles-and-corewriter-ef4507eb17ef)
- [HypeRPC: CoreWriter Guide](https://hyperpc.app/blog/hyperliquid-corewriter)
- [Hyperliquid docs: Interacting with HyperCore](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/interacting-with-hypercore)
- [Hyperliquid docs: HyperCore <> HyperEVM transfers](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/hypercore-less-than-greater-than-hyperevm-transfers)
