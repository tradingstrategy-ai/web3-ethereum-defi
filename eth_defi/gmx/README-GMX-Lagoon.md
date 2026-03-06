# GMX Lagoon integration security analysis

Security architecture for GMX perpetuals trading through Lagoon vaults (Gnosis Safe), focusing on the Guard contract's validation of fund flows.

For usage, see [the tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-gmx.html).

## Architecture

```
Asset Manager ──▶ TradingStrategyModule (Guard) ──▶ Gnosis Safe (Vault Funds)
                          │ validates
                          ▼
                  GMX ExchangeRouter (multicall)
```

## GMX multicall patterns

GMX V2 batches order operations via `ExchangeRouter.multicall()`. The Guard validates every inner call.

| Inner call | Purpose |
|------------|---------|
| `sendWnt(address,uint256)` | Send ETH for keeper execution fee |
| `sendTokens(address,address,uint256)` | Send ERC20 collateral to order vault |
| `createOrder(tuple)` | Create the GMX position order |

**Open long (native ETH)**: `[sendWnt(orderVault, collateral+fee), createOrder(...)]`

**Open short (ERC20)**: `[sendWnt(orderVault, fee), sendTokens(token, orderVault, amount), createOrder(...)]`

**Close position**: `[sendWnt(orderVault, fee), createOrder(...)]`

## Opening and closing long and short positions

Detailed Ethereum transaction breakdown for trading with USDC collateral from a Lagoon vault on Arbitrum.

### Pre-requisite: USDC approval (one-time)

Before any trade, the Safe must approve the GMX SyntheticsRouter to spend USDC.

```
Outer transaction:
  from:   asset_manager
  to:     TradingStrategyModuleV0
  call:   performCall(USDC_address, approve(SyntheticsRouter, 2^256-1), 0)
  value:  0
```

Inner effect: Safe calls `USDC.approve(SyntheticsRouter, 2^256-1)`.

See [approvals.py](lagoon/approvals.py) `approve_gmx_collateral_via_vault()`.

### Opening a short (e.g. short ETH/USD with USDC collateral)

One Ethereum transaction wrapping a multicall with **3 inner calls**.

**Outer transaction:**

```
from:   asset_manager
to:     TradingStrategyModuleV0
call:   performCall(ExchangeRouter, <multicall_calldata>, execution_fee)
value:  execution_fee    (if forward_eth=True, else 0 and Safe pays from its own ETH)
```

**Inner multicall** — `ExchangeRouter.multicall(bytes[])` contains:

| # | Call | Selector | Arguments |
|---|------|----------|-----------|
| 1 | `sendWnt` | `0x7d39aaf1` | `(OrderVault, execution_fee_wei)` |
| 2 | `sendTokens` | `0xe6d66ac8` | `(USDC_address, OrderVault, collateral_amount_raw)` |
| 3 | `createOrder` | `0xf59c48eb` | `(CreateOrderParams)` — see below |

Where `collateral_amount_raw = (size_delta_usd / leverage) * 10^6` (USDC has 6 decimals).

**CreateOrderParams for short:**

```
CreateOrderParams {
    addresses: {
        receiver:               Safe_address,
        cancellationReceiver:   Safe_address,
        callbackContract:       0x0,
        uiFeeReceiver:          0x0,
        market:                 ETH_USD_market_address,
        initialCollateralToken: USDC_address,
        swapPath:               [],
    },
    numbers: {
        sizeDeltaUsd:                 size * 10^30,       // e.g. 1000 USD = 1000e30
        initialCollateralDeltaAmount: collateral_raw,     // USDC in 6 decimals
        triggerPrice:                 0,                   // 0 for market orders
        acceptablePrice:              oracle * (1 - slip), // maximise entry price
        executionFee:                 execution_fee_wei,
        callbackGasLimit:             0,
        minOutputAmount:              0,
        validFromTime:                0,
    },
    orderType:               2,      // MarketIncrease
    decreasePositionSwapType: 0,     // NoSwap
    isLong:                  false,  // SHORT
    shouldUnwrapNativeToken: true,
    autoCancel:              false,
    referralCode:            0x0,
    dataList:                [],
}
```

**Token flow:**

```
Safe USDC  ──sendTokens──▶  OrderVault  ──keeper executes──▶  GMX short position (owned by Safe)
Safe ETH   ──sendWnt────▶  OrderVault  ──keeper fee
```

### Opening a long (e.g. long ETH/USD with USDC collateral)

Identical structure — same 3 inner calls. The differences from a short are:

| Field | Short | Long |
|-------|-------|------|
| `isLong` | `false` | `true` |
| `swapPath` | `[]` | `[ETH_USD_market_address]` |
| `acceptablePrice` | `oracle * (1 - slippage)` | `oracle * (1 + slippage)` |

When going long with USDC, GMX needs a `swapPath` to swap USDC → WETH at the market, because the native long collateral is WETH. The swap happens atomically during keeper execution.

**Token flow (long with USDC):**

```
Safe USDC  ──sendTokens──▶  OrderVault  ──keeper: swap USDC→WETH──▶  GMX long position (WETH collateral)
Safe ETH   ──sendWnt────▶  OrderVault  ──keeper fee
```

### Closing a position

One Ethereum transaction with **2 inner calls** (no `sendTokens` — collateral flows back, not in).

**Inner multicall:**

| # | Call | Selector | Arguments |
|---|------|----------|-----------|
| 1 | `sendWnt` | `0x7d39aaf1` | `(OrderVault, execution_fee_wei)` |
| 2 | `createOrder` | `0xf59c48eb` | `(CreateOrderParams)` — see below |

**CreateOrderParams for close:**

```
CreateOrderParams {
    addresses: {
        receiver:               Safe_address,     // collateral + PnL sent here
        cancellationReceiver:   Safe_address,
        market:                 ETH_USD_market_address,
        initialCollateralToken: USDC_address,     // (or WETH for longs)
        swapPath:               [],               // (or [market] for long→USDC)
        ...
    },
    numbers: {
        sizeDeltaUsd:                 position_size * 10^30,   // full close = full position size
        initialCollateralDeltaAmount: collateral_to_withdraw,  // in token decimals
        ...
    },
    orderType:               4,          // MarketDecrease
    isLong:                  true/false,  // matches the position being closed
    ...
}
```

**Token flow (close):**

```
GMX position  ──keeper executes──▶  collateral + PnL returned to Safe (receiver)
Safe ETH      ──sendWnt──────────▶  OrderVault (keeper fee)
```

## Guard validation

The Guard ([GuardV0Base.sol](../../contracts/guard/src/GuardV0Base.sol)) iterates through every call in the multicall and validates each one. Unknown selectors revert the entire transaction.

### sendWnt / sendTokens

- Receiver must equal the configured OrderVault
- Token (for sendTokens) must be whitelisted

### createOrder

Decoded via `abi.decode()` with GMX struct definitions from [IGmxV2.sol](../../contracts/guard/src/lib/IGmxV2.sol):

```solidity
CreateOrderParams memory params = abi.decode(callData, (CreateOrderParams));

require(isAllowedReceiver(params.addresses.receiver));
require(isAllowedReceiver(params.addresses.cancellationReceiver));
require(isAllowedGMXMarket(params.addresses.market));
require(isAllowedAsset(params.addresses.initialCollateralToken));

for (uint256 i = 0; i < params.addresses.swapPath.length; i++) {
    require(isAllowedGMXMarket(params.addresses.swapPath[i]));
}
```

**Validated fields**: receiver, cancellationReceiver, market, collateral token, every swapPath market.

## Whitelist configuration

```solidity
guard.whitelistGMX(exchangeRouter, syntheticsRouter, orderVault, "GMX");
guard.allowReceiver(safeAddress, "Safe vault");
guard.whitelistGMXMarket(ethUsdMarket, "ETH/USD");
guard.whitelistToken(usdc, "USDC collateral");
```

All whitelist operations require `onlyGuardOwner`.

## Execution fee (keeper fee) funding

GMX orders require native ETH execution fees paid to keepers via `sendWnt()`. There are two ways to fund these fees:

### Safe-funded (default)

The Safe holds ETH and pays execution fees from its own balance. The asset manager calls `performCall(target, data, value)` where `value` tells the Safe how much ETH to send, but the asset manager's transaction itself carries no ETH (`msg.value == 0`).

```
Asset Manager ──(gas only)──▶ TradingStrategyModule.performCall(target, data, value)
                                        │
                                        ▼
                                Gnosis Safe ──(value ETH)──▶ GMX ExchangeRouter
```

Simple, but requires someone to top up the Safe with ETH periodically.

### Asset-manager-funded (opt-in, v0.4+)

The asset manager sends ETH with the `performCall` transaction. The module forwards `msg.value` to the Safe via its `receive()` fallback, then the Safe sends `value` ETH to the target as usual. No manual Safe top-up needed.

```
Asset Manager ──(gas + ETH)──▶ TradingStrategyModule.performCall{value: fee}(target, data, value)
                                        │ forwards msg.value to Safe
                                        ▼
                                Gnosis Safe ──(value ETH)──▶ GMX ExchangeRouter
```

Enable this in Python with `forward_eth=True`:

```python
wallet = LagoonGMXTradingWallet(vault, asset_manager, forward_eth=True)
```

The guard does not validate ETH amounts — all targets are governance-approved contracts and ETH only flows to those trusted targets. If `msg.value > value`, the excess stays in the Safe.

## GmxLib validation details

The actual validation logic lives in [GmxLib.sol](../../contracts/guard/src/lib/GmxLib.sol), a library called by `GuardV0Base` when the guard encounters a `multicall()` selector targeting a whitelisted GMX router.

### What GmxLib validates

`GmxLib.validateMulticall()` iterates over every inner call in the multicall payload and dispatches by selector:

| Inner call | Checks |
|------------|--------|
| `sendWnt(receiver, amount)` | `receiver == orderVault` (hardcoded per router during whitelisting) |
| `sendTokens(token, receiver, amount)` | `receiver == orderVault`; token must pass `isAllowedAsset()` (unless `anyAsset`) |
| `createOrder(params)` | See below |
| Unknown selector | **Reverts** — no unrecognised calls allowed |

For `createOrder`, the params struct is ABI-decoded and the following fields are checked:

| Field | Always checked | Bypassed by `anyAsset`? |
|-------|---------------|------------------------|
| `params.addresses.receiver` | Yes | No |
| `params.addresses.cancellationReceiver` | Yes | No |
| `params.addresses.market` | Yes (unless `anyAsset`) | Yes |
| `params.addresses.initialCollateralToken` | Yes (unless `anyAsset`) | Yes |
| `params.addresses.swapPath[*]` (each entry) | Yes (unless `anyAsset`) | Yes |

### What GmxLib does NOT validate

The guard does not check order economics — these are trusted to the asset manager:

- **Order size** (`sizeDeltaUsd`) — no leverage limits enforced
- **Acceptable price** (`acceptablePrice`) — no slippage bounds enforced
- **Execution fee amount** — only the destination (orderVault) is checked
- **Order type and direction** — long/short, market/limit, increase/decrease all allowed
- **Trigger price** (`triggerPrice`) — not checked
- **Callback contract** (`callbackContract`) and **UI fee receiver** — decoded but not validated

### Market whitelisting

Markets are stored in `GmxStorage.allowedMarkets` mapping using diamond storage (slot `keccak256("eth_defi.gmx.v1")`). Functions:

- `whitelistMarket(address, notes)` — sets `allowedMarkets[market] = true`, emits `GMXMarketApproved`
- `removeMarket(address, notes)` — sets to `false`, emits `GMXMarketRemoved`
- `isAllowedMarket(address, anyAsset)` — returns `anyAsset || allowedMarkets[market]`

All called via `GuardV0Base.whitelistGMXMarket()` which requires `onlyGuardOwner`.

## The `anyAsset` flag

`anyAsset` is a boolean on `GuardV0Base` (set via `setAnyAssetAllowed(bool, notes)`, `onlyGuardOwner` only). When enabled, it relaxes asset and market checks across the guard — not just for GMX.

### What `anyAsset = true` bypasses

- **GMX market whitelisting** — `createOrder` accepts any market address, and any swapPath entry
- **GMX collateral token check** — `sendTokens` accepts any ERC-20 token
- **Asset whitelisting** — `isAllowedAsset()` returns `true` for all tokens
- **Uniswap V3 token path validation** — SwapRouter02 accepts any token path

### What `anyAsset = true` does NOT bypass

These checks are **always enforced** regardless of `anyAsset`:

- **Receiver validation** — `createOrder` receiver and cancellationReceiver must be whitelisted
- **OrderVault destination** — `sendWnt` and `sendTokens` can only send to the pre-configured orderVault
- **Router whitelisting** — only whitelisted GMX routers are accepted
- **Sender validation** — only whitelisted asset managers can call `performCall`

### Security implications of `anyAsset = true`

Enabling `anyAsset` is a significant security relaxation. It means the asset manager can:

1. **Trade any GMX market** — not just whitelisted ones (e.g. exotic or low-liquidity markets)
2. **Use any token as collateral** — not just explicitly approved tokens
3. **Route through any swapPath** — intermediate markets are not checked

However, the asset manager **cannot steal funds** because:

- Order proceeds always go to a whitelisted receiver (the Safe)
- Tokens and ETH can only be sent to the pre-configured orderVault
- The asset manager cannot call arbitrary contracts — only whitelisted routers

**Recommendation**: Use explicit market and asset whitelisting (`anyAsset = false`) for production vaults. Only enable `anyAsset` when the vault strategy requires dynamic market access (e.g. multi-market strategies where new markets may be added by GMX governance).

## Attack vectors (mitigated)

| Attack | Mitigation |
|--------|------------|
| Set `receiver` to attacker address | `isAllowedReceiver()` rejects non-whitelisted |
| Set `cancellationReceiver` to attacker | `isAllowedReceiver()` check |
| `sendWnt(attacker, amount)` in multicall | Receiver must equal orderVault |
| `sendTokens(token, attacker, amount)` | Receiver must equal orderVault |
| Inject unknown function in multicall | Unknown selectors revert |
| Short calldata to read garbage | `calls[i].length >= 4` check |
| Batch valid + invalid orders | Loop validates ALL calls; any failure reverts |
| `anyAsset` to bypass receiver checks | `anyAsset` only bypasses asset/market checks, not receiver checks |
| Non-whitelisted market in swapPath | Each swapPath entry checked via `isAllowedGMXMarket()` |

## Recommendations

1. Only whitelist the Safe address as a receiver
2. Use explicit market whitelisting rather than `anyAsset=true` — covers both primary market and swapPath
3. Monitor GMX contract upgrades that might change the `CreateOrderParams` struct layout (structs in [IGmxV2.sol](../../contracts/guard/src/lib/IGmxV2.sol) must match)
4. Test on fork before mainnet

## Testnet (Arbitrum Sepolia)

GMX V2 is deployed on Arbitrum Sepolia (chain ID 421614) with full keeper support, so the complete trading flow (open/close positions, keeper execution) works on testnet.

### Deployment differences

- **No Lagoon factory on Sepolia** — both Gnosis Safe and all Lagoon contracts are deployed from scratch (`from_the_scratch=True, use_forge=True`)
- **No Chainlink price feeds** — USD cost estimates are unavailable on testnet
- **Testnet + simulation is not supported** — Anvil fork mode only works with mainnet

### Getting test tokens

GMX testnet uses its own `MintableToken` contracts — anyone can call `mint(account, amount)` directly.

| Token | Address | Arbiscan |
|-------|---------|----------|
| USDC.SG | `0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773` | [Write Contract](https://sepolia.arbiscan.io/address/0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773#writeContract) |
| USDC | `0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f` | [Write Contract](https://sepolia.arbiscan.io/address/0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f#writeContract) |
| WETH | `0x980B62Da83eFf3D4576C647993b0c1D7faf17c73` | [Write Contract](https://sepolia.arbiscan.io/address/0x980B62Da83eFf3D4576C647993b0c1D7faf17c73#writeContract) |
| BTC | `0xF79cE1Cf38A09D572b021B4C5548b75A14082F12` | [Write Contract](https://sepolia.arbiscan.io/address/0xF79cE1Cf38A09D572b021B4C5548b75A14082F12#writeContract) |

To mint 999 USDC.SG: call `mint(your_address, 999000000)` (6 decimals).

For testnet ETH: use the [LearnWeb3 faucet](https://learnweb3.io/faucets/arbitrum_sepolia/).

### USDC vs USDC.SG gotcha

There are two USDC variants on Arbitrum Sepolia. GMX markets use **USDC.SG** (`0x3253a335...`) as their stablecoin — not the regular USDC (`0x3321Fd36...`). The vault denomination and collateral symbol must match what the market accepts:

- Vault underlying: USDC.SG
- GMX order collateral symbol: `USDC.SG`
- GMX order symbol: `ETH/USDC.SG:USDC.SG`

Using the wrong USDC variant causes `"Not a valid collateral for selected market!"` because the `OrderArgumentParser` validates the resolved token address against the market's `long_token_address` / `short_token_address`.

### Dynamic market resolution

`GMX_POPULAR_MARKETS` only contains mainnet addresses. On testnet, market addresses are fetched dynamically using `fetch_all_gmx_markets(web3)` and matching by `market_symbol == "ETH"`.

### GMX contract addresses

Testnet contract addresses are fetched via `get_contract_addresses("arbitrum_sepolia")` from the hardcoded registry in `eth_defi/gmx/contracts.py` (not fetched from the GMX API like mainnet).

## Test coverage

| Validation | Unit test | Integration test |
|------------|-----------|------------------|
| sendWnt receiver = orderVault | - | via real GMX |
| sendTokens receiver = orderVault | - | via real GMX |
| sendTokens token whitelisted | `test_gmx_market_whitelisted` | via real GMX |
| createOrder receiver whitelisted | `test_receiver_whitelisted` | via real GMX |
| createOrder cancellationReceiver | `test_receiver_whitelisted` | via real GMX |
| createOrder market whitelisted | `test_gmx_market_whitelisted` | via real GMX |
| createOrder collateral whitelisted | `test_any_asset_allows_non_whitelisted_asset` | via real GMX |
| createOrder swapPath whitelisted | `test_security_attack_scenario_non_whitelisted_swap_path` | via real GMX |
| swapPath with anyAsset | `test_any_asset_allows_non_whitelisted_swap_path_market` | - |

## Referral codes

GMX offers a [referral programme](https://docs.gmx.io/docs/referrals/) that discounts position opening and closing fees. The programme has three tiers:

| Tier | Trader discount | Affiliate rebate | Requirements |
|------|----------------|-------------------|--------------|
| 1 | 5% | 5% | Anyone can create |
| 2 | 10% | 10% | 15+ weekly users, $5M+ volume |
| 3 | 10% | 15% ETH/AVAX + 5% esGMX | 30+ weekly users, $25M+ volume |

### How it works on-chain

Referral codes are stored in the [ReferralStorage](https://arbiscan.io/address/0xe6fab3F0c7199b0d34d7FbE83394fc0e0D06e99d) contract (shared between GMX V1 and V2). When `ExchangeRouter.createOrder()` receives a non-zero `referralCode` bytes32 parameter, it calls [ReferralUtils.setTraderReferralCode()](https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/referral/ReferralUtils.sol) which persists the code for the trader account. Once set, the discount applies automatically to all subsequent orders from that account.

Source contracts:
- [ReferralStorage.sol](https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/mock/ReferralStorage.sol) — stores code ownership and trader-code associations
- [ReferralUtils.sol](https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/referral/ReferralUtils.sol) — called by ExchangeRouter during order creation

### Encoding

Referral codes are `bytes32` values: the string is UTF-8 encoded, left-aligned, and right-padded with zeros to 32 bytes. For example, `"tano"` becomes `0x74616e6f000...000` (4 bytes + 28 zero bytes). This matches Solidity's `bytes32("tano")` and ethers.js `formatBytes32String("tano")`.

Use `convert_string_to_bytes32()` from `eth_defi.event_reader.conversion`:

```python
from eth_defi.event_reader.conversion import convert_string_to_bytes32

code = convert_string_to_bytes32("tano")
# b'tano\x00\x00\x00...\x00' (32 bytes)
```

### Usage with Lagoon vault

Pass `referral_code` when creating the `GMXConfig`. All orders (open, close, SL/TP) created through this config will include the referral code:

```python
from eth_defi.gmx.config import GMXConfig
from eth_defi.event_reader.conversion import convert_string_to_bytes32

config = GMXConfig(
    web3=web3,
    user_wallet_address=vault_address,
    referral_code=convert_string_to_bytes32("tano"),
)
wallet = LagoonGMXTradingWallet(vault, asset_manager, forward_eth=True)
```

### Verifying a code on-chain

To check that a referral code is registered and that the encoding is correct, query `ReferralStorage.codeOwners()`:

```python
from eth_defi.event_reader.conversion import convert_string_to_bytes32

referral_storage = web3.eth.contract(
    address="0xe6fab3F0c7199b0d34d7FbE83394fc0e0D06e99d",
    abi=...,  # eth_defi/abi/gmx/ReferralStorage.json
)
code = convert_string_to_bytes32("tano")
owner = referral_storage.functions.codeOwners(code).call()
assert owner != "0x" + "0" * 40  # Code is registered
```

See `tests/gmx/test_referral_code.py` for a complete working example.

## Files

| File | Description |
|------|-------------|
| [TradingStrategyModuleV0.sol](../../contracts/safe-integration/src/TradingStrategyModuleV0.sol) | Safe module with performCall (payable in v0.4+) |
| [GuardV0Base.sol](../../contracts/guard/src/GuardV0Base.sol) | Guard contract with GMX validation |
| [IGmxV2.sol](../../contracts/guard/src/lib/IGmxV2.sol) | GMX struct definitions for `abi.decode()` |
| [base_order.py](order/base_order.py) | GMX order building with multicall patterns |
| [wallet.py](lagoon/wallet.py) | LagoonGMXTradingWallet adapter |
| [test_guard_gmx_validation.py](../../tests/guard/test_guard_gmx_validation.py) | Unit tests for Guard GMX validation |
| [test_gmx_lagoon_integration.py](../../tests/gmx/lagoon/test_gmx_lagoon_integration.py) | Integration tests on Arbitrum fork |
