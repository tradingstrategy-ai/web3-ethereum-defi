# Hypercore native vault guard integration

Guard support for depositing USDC from a guarded Safe multisig on HyperEVM (chain 999) into Hypercore native vaults (chain 9999) via the CoreWriter system contract.

> **Use mainnet for testing.** The HyperEVM testnet EVM-to-Core bridge (`CoreDepositWallet.deposit()`) frequently times out or silently fails — deposited USDC gets stuck in EVM escrow indefinitely. Testnet also lacks pre-deployed Safe and Lagoon factory contracts, requiring from-scratch deployment via `forge create`. Mainnet testing is cheap (~0.1 HYPE + ~7 USDC) and uses the same factory infrastructure as production.

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

## Deployment flow

### Overview

```
  Deployer EOA
       │
       ├─ 1. Enable big blocks (evmUserModify via exchange API)
       │      └─ Required for TradingStrategyModuleV0 (~5.4M gas)
       │
       ├─ 2. Deploy Safe (1/1 multisig)
       │      └─ Mainnet: via SafeProxyFactory (small blocks OK)
       │      └─ Testnet: from scratch via forge create (big blocks)
       │
       ├─ 3. Deploy Lagoon vault (ERC-4626)
       │      └─ Mainnet: via OptinProxyFactory (small blocks OK)
       │      └─ Testnet: from scratch via forge create (big blocks)
       │
       ├─ 4. Deploy TradingStrategyModuleV0 (guard)
       │      └─ Always requires big blocks (~5.4M gas)
       │      └─ Whitelists: CoreWriter, CoreDepositWallet, vault address
       │
       └─ 5. Disable big blocks (return to fast ~1s confirmations)
```

The deployment script (`deploy-lagoon-hyperliquid-vault.py`) handles all of these
steps. Set `SIMULATE=true` to test on an Anvil fork without real funds.

## Deposit flow

### Overview

```
                          HyperEVM (chain 999)                       HyperCore (chain 9999)
                    ┌─────────────────────────────┐            ┌──────────────────────────────┐
                    │                             │            │                              │
  Preparation       │  Deployer EOA               │            │                              │
  (first deposit    │    │                        │            │                              │
   only)            │    ├─ transfer USDC to Safe  │            │                              │
                    │    │  (deposit + 2 activation)│           │                              │
                    │    │                        │            │                              │
                    │    └─ activate Safe ────────────────────>│  depositFor(safe, 2 USDC)    │
                    │       via CDW.depositFor()  │            │    └─ creates HyperCore acct │
                    │                             │            │       (~1 USDC creation fee) │
                    │                             │            │                              │
  Phase 1           │  Safe (USDC on EVM)         │            │                              │
  (1 multicall)     │    │                        │            │                              │
                    │    ├─ approve(CDW, amount)   │            │                              │
                    │    │                        │            │                              │
                    │    └─ CDW.deposit(amount)  ─────────────>│  EVM escrow                  │
                    │                             │   bridge   │    │                          │
                    │                             │            │    └─> Safe spot account      │
                    │                             │            │         (USDC arrives ~2-10s) │
                    │                             │            │                              │
                    │         ... wait for escrow to clear ...                                │
                    │                             │            │                              │
  Phase 2           │  Safe (via CoreWriter)      │            │                              │
  (1 multicall)     │    │                        │            │                              │
                    │    ├─ transferUsdClass ─────────────────>│  spot ──> perp               │
                    │    │                        │            │                              │
                    │    └─ vaultTransfer   ──────────────────>│  perp ──> vault              │
                    │                             │            │                              │
                    └─────────────────────────────┘            └──────────────────────────────┘
```

### First deposit preparation

Before the first deposit, the Safe must be funded and activated on HyperCore:

1. **Fund Safe with USDC** — transfer deposit amount + 2 USDC activation overhead from deployer
2. **Activate Safe on HyperCore** — call `CoreDepositWallet.depositFor(safe, 2 USDC, SPOT_DEX)` via the trading strategy module. This bridges 2 USDC and creates the HyperCore account (~1 USDC creation fee). Use `is_account_activated()` to check and `activate_account()` to perform this step.

Subsequent deposits skip activation (the Safe is already known to HyperCore).

### Two-phase deposit

The deposit is split into two phases with an escrow wait between them.
This is the default on live networks (`DEPOSIT_MODE=two_phase`).

**Phase 1** — bridge USDC from EVM to HyperCore spot (1 multicall, 2 `performCall`s):

1. **Approve USDC** to CoreDepositWallet
   - Target: USDC contract
   - Function: `approve(CoreDepositWallet, amount)`
   - Guard: validates approval destination is whitelisted

2. **Bridge USDC to Core** spot account
   - Target: CoreDepositWallet
   - Function: `deposit(amount, SPOT_DEX)`
   - Guard: validates target is allowed CoreDepositWallet

**Wait** — poll `spotClearinghouseState` until `evmEscrows` clears (~2-10 seconds).

**Phase 2** — move USDC into vault (1 multicall, 2 `performCall`s):

3. **Move USDC spot -> perp**
   - Target: CoreWriter
   - Function: `sendRawAction(transferUsdClass(amount, true))`
   - Guard: validates CoreWriter target, action ID 7 allowed

4. **Deposit to vault**
   - Target: CoreWriter
   - Function: `sendRawAction(vaultTransfer(vault, true, amount))`
   - Guard: validates CoreWriter target, action ID 2 allowed, vault address whitelisted

### Minimum deposit amount

Hyperliquid silently rejects `vaultTransfer` deposits below **5 USDC** (`MINIMUM_VAULT_DEPOSIT = 5_000_000` raw). There is no error — the EVM transaction succeeds but the vault position is never created on HyperCore.

## Withdrawal flow

### Overview

```
                          HyperEVM (chain 999)                       HyperCore (chain 9999)
                    ┌─────────────────────────────┐            ┌──────────────────────────────┐
                    │                             │            │                              │
  1 multicall       │  Safe (via CoreWriter)      │            │                              │
  (3 performCalls)  │    │                        │            │                              │
                    │    ├─ vaultTransfer   ──────────────────>│  vault ──> perp              │
                    │    │                        │            │                              │
                    │    ├─ transferUsdClass ─────────────────>│  perp ──> spot               │
                    │    │                        │            │                              │
                    │    └─ spotSend        ──────────────────>│  spot ──> EVM bridge         │
                    │                             │            │    │                          │
                    │  Safe (USDC on EVM) <────────────────────────┘                          │
                    │                             │   bridge   │                              │
                    └─────────────────────────────┘            └──────────────────────────────┘
```

Three `performCall` transactions batched in a single multicall:

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

Both the deposit and withdrawal flows use `TradingStrategyModuleV0.multicall(bytes[])`
to batch multiple `performCall` invocations into a single EVM transaction. The module
inherits `Multicall` from `GuardV0Base`, which uses `delegatecall` to execute each
inner call — preserving `msg.sender` so guard validation works correctly.

When the EVM block finishes execution, all queued CoreWriter actions are processed
sequentially on HyperCore (~47k gas per action). This is implicit batching at the
block level.

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

This means the deposit flow can partially fail. For example, USDC could be bridged to Core (phase 1) but the vault deposit (phase 2) could fail on Core, leaving funds in the Safe's spot or perp account rather than in the vault.

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

- **Contract activation**: smart contract addresses (like Safe multisigs) must be activated on HyperCore before `CoreDepositWallet.deposit()` bridge actions will clear the EVM escrow. Without activation, deposited USDC gets **permanently stuck** in `evmEscrows`.
  - New HyperCore accounts incur a **~1 USDC account creation fee**.
  - The default activation amount is **2 USDC** (`DEFAULT_ACTIVATION_AMOUNT = 2_000_000` raw). Deposits ≤1 USDC to new accounts fail silently.
  - Activation uses `CoreDepositWallet.depositFor(safe, amount, SPOT_DEX)` which bridges USDC and creates the account in one step.
  - Use `is_account_activated()` to check and `activate_account()` to perform activation.
- **Minimum vault deposit**: **5 USDC** (`MINIMUM_VAULT_DEPOSIT = 5_000_000` raw). Hyperliquid silently rejects `vaultTransfer` deposits below this threshold.
- **Bridging fees (Core -> EVM)**: requires HYPE or USDC on HyperCore spot for fees. HYPE is consumed first.
- **Bridging fees (EVM -> Core)**: requires HYPE on HyperEVM for gas.
- The Safe needs its HYPE balance managed to ensure bridging operations can pay fees.

### Flaky RPCs

HyperEVM JSON-RPC endpoints are unreliable — requests randomly fail with connection resets, timeouts, or 5xx errors. Always use `create_multi_provider_web3()` with multiple RPC URLs (space-separated in the `JSON_RPC_HYPERLIQUID` environment variable) so that failed requests are automatically retried against fallback providers:

```python
from eth_defi.provider.multi_provider import create_multi_provider_web3

# Space-separated URLs in env var: "https://rpc1.example.com https://rpc2.example.com"
web3 = create_multi_provider_web3(
    os.environ["JSON_RPC_HYPERLIQUID"],
    default_http_timeout=(3, 500.0),  # (connect, read) timeout in seconds
)
```

The long read timeout (500 s) accommodates big block transactions which take ~1 minute to confirm. Without fallback providers, a single RPC failure during a multi-step deployment or deposit flow can leave operations in a partially completed state.

### Vault lock-up

- User-created vaults: **1 day** lock-up after deposit before withdrawal.
- Protocol vaults (HLP): **4 day** lock-up.
- The `vaultTransfer(withdraw)` action will fail on HyperCore if the lock-up has not expired. The `hyper-evm-lib` library checks the lock via the vault equity precompile before submitting the action.

## Dual-block architecture and contract deployment

HyperEVM produces two types of blocks under a unified, increasing sequence of EVM block numbers.
This has major implications for deploying Lagoon vaults and guard contracts.

### Block types

| Property | Small blocks | Large blocks |
|----------|-------------|-------------|
| Gas limit | ~2–3M | 30M |
| Cadence | Every ~1 second | Every ~1 minute |
| Transactions per block | Multiple | 1 |
| Use case | Normal transactions | Contract deployments, heavy computation |

Two independent mempools source transactions for the two block types.
A transaction is routed to one or the other based on the sender's account-level flag.

### Why this matters for Lagoon deployment

- `TradingStrategyModuleV0` requires **~5.4M gas** to deploy — exceeds the small block limit
- `Vault.sol` (Lagoon implementation) requires **>3M gas** — also exceeds the small block limit
- On HyperEVM **mainnet** (chain 999), Safe and Lagoon factories are already deployed,
  so Safe proxy and vault proxy deployment fits in small blocks — only the
  `TradingStrategyModuleV0` guard module (~5.4M gas) requires big blocks
- On HyperEVM **testnet** (chain 998), there is **no factory** — the script deploys from scratch
  via `forge create`, which hits the small block gas limit and fails

The deployment script failure we encountered:

```
Error: server returned an error response: error code -32603: exceeds block gas limit
```

This occurs because `forge create` submits to the default (small block) mempool.

### How to enable large blocks

Before deploying contracts that exceed the small block gas limit, the deployer address
must opt into the large block mempool. This is a HyperCore-level action, not an EVM transaction.

**Method 1: Hyperliquid Python SDK**

```python
from hyperliquid.exchange import Exchange
from eth_account import Account

account = Account.from_key(private_key)
exchange = Exchange(account, "https://api.hyperliquid.xyz", account_address=account.address)

# Enable large blocks for this address
exchange.use_big_blocks(True)

# ... deploy contracts ...

# Disable large blocks to return to fast confirmation
exchange.use_big_blocks(False)
```

For testnet, use `"https://api.hyperliquid-testnet.xyz"` as the API URL.

Under the hood, this sends an `evmUserModify` action: `{"type": "evmUserModify", "usingBigBlocks": true}`.

**Method 2: Web toggle**

Visit https://hyperevm-block-toggle.vercel.app/, connect the deployer wallet, and sign the enabling transaction.

**Method 3: LayerZero CLI**

```shell
npx @layerzerolabs/hyperliquid-composer set-block --size big --network mainnet --private-key $PRIVATE_KEY
```

**Method 4: `eth_bigBlockGasPrice` RPC**

Query `eth_bigBlockGasPrice` to get the base fee for the next large block, then use that gas price
in the transaction. This routes to the large block mempool without toggling the account flag.

### Prerequisites

- The deployer address must be a known HyperCore user (e.g., by having received Core assets)
- HYPE must be on HyperEVM for gas
- After toggling to large blocks, **all** transactions from that address go to the large block mempool
  until toggled back — including normal transfers, which will take ~1 minute instead of ~1 second

### Checking current block mode

```python
# Check if an address is using large blocks
result = web3.provider.make_request("eth_usingBigBlocks", [address])
```

### Custom HyperEVM JSON-RPC methods

| Method | Purpose |
|--------|---------|
| `eth_bigBlockGasPrice` | Base fee for the next large block |
| `eth_usingBigBlocks` | Whether an address is flagged for large blocks |
| `eth_getSystemTxsByBlockHash` | System transactions from HyperCore by block hash |
| `eth_getSystemTxsByBlockNumber` | System transactions from HyperCore by block number |

### Lagoon factory on mainnet

HyperEVM mainnet (chain 999) has a pre-deployed Lagoon `OptinProxyFactory` at
`0x90beB507A1BA7D64633540cbce615B574224CD84`, registered in `LAGOON_BEACON_PROXY_FACTORIES`.
This means vault deployment uses lightweight proxies and does **not** require from-scratch
deployment. The deploy script explicitly asserts against from-scratch deployment on mainnet.

HyperEVM testnet (chain 998) has **no factory**, so the deploy script deploys the full
Lagoon protocol from scratch using `forge create`.

### Deployment strategy summary

| Scenario | Block type needed | Notes |
|----------|-------------------|-------|
| Mainnet Safe deployment (proxy factory exists) | Small blocks | Proxy deployment is lightweight |
| Mainnet Lagoon vault deployment (factory exists) | Small blocks | Beacon proxy is lightweight |
| Testnet from-scratch deployment | **Large blocks required** | Vault + registry + Safe exceed 3M gas |
| Guard deployment (`TradingStrategyModuleV0`) | **Large blocks required** | ~5.4M gas, always needs big blocks |
| Multicall deposit/withdrawal | Small blocks | Individual calls are <100k gas each |
| Anvil fork (SIMULATE mode) | N/A | Anvil overrides gas limit to 30M |

## Manual testing

A manual test script deploys a Lagoon vault on a HyperEVM Anvil fork and exercises
the full deposit/withdrawal flow:

```shell
source .local-test.env && poetry run python scripts/hyperliquid/deploy-lagoon-hyperliquid-vault.py
```

See the script's docstring for environment variables and account funding instructions.

## Troubleshooting

Use `check-hypercore-user.py` to inspect a HyperCore user's spot balances,
EVM escrows, perpetual account state, and vault positions:

```shell
# Mainnet
ADDRESS=0xfBF2cc6708DC303484b3b8008F1DEcC6d934787a \
    poetry run python scripts/hyperliquid/check-hypercore-user.py

# Testnet
NETWORK=testnet ADDRESS=0xAbc... \
    poetry run python scripts/hyperliquid/check-hypercore-user.py
```

This is useful for diagnosing:

- **Stuck EVM escrows**: USDC bridged via `CoreDepositWallet.deposit()` but not
  yet processed by HyperCore (shows in `EVM escrows` section).
- **Missing vault positions**: deposit EVM transaction succeeded but CoreWriter
  action failed silently on HyperCore (vault positions section is empty).
- **Spot/perp balance mismatches**: USDC arrived on HyperCore spot but the
  `transferUsdClass` or `vaultTransfer` step failed.

## Contract size

See `README-contract-size.md`.

## Further reading

- [hyper-evm-lib](https://github.com/hyperliquid-dev/hyper-evm-lib) -- canonical Solidity library (MIT, maintained by Obsidian Audits)
- [Chase Manning: Hyperliquid Precompiles - An Inconvenient Truth](https://x.com/chase_manning_/status/2014370671514538379)
- [Ambit Labs: Demystifying Precompiles and CoreWriter](https://medium.com/@ambitlabs/demystifying-the-hyperliquid-precompiles-and-corewriter-ef4507eb17ef)
- [HypeRPC: CoreWriter Guide](https://hyperpc.app/blog/hyperliquid-corewriter)
- [Hyperliquid docs: Interacting with HyperCore](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/interacting-with-hypercore)
- [Hyperliquid docs: HyperCore <> HyperEVM transfers](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/hypercore-less-than-greater-than-hyperevm-transfers)
- [Dual-block architecture — Hyperliquid docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/dual-block-architecture)
- [A guide to HyperEVM's dual block architecture — HypeRPC](https://hyperpc.app/blog/hyperevm-dual-block-architecture)
- [How to enable big blocks on HyperEVM — Curve Resources](https://resources.curve.finance/troubleshooting/how-to-enable-big-blocks/)
- [Curve Finance big blocks Python script](https://github.com/curvefi/curve-core/blob/main/scripts/utils/hyperevm_enable_big_blocks.py)
- [Hyperliquid Python SDK `use_big_blocks()` example](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/examples/basic_evm_use_big_blocks.py)
- [HyperEVM JSON-RPC — Hyperliquid docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/json-rpc)
