# GMX Lagoon integration security analysis

This document describes the security architecture of the GMX perpetuals integration with Lagoon vaults, focusing on the Guard contract's role in preventing unauthorised fund transfers.

For a tutorial on how to use this integration, see [Lagoon and GMX perpetuals integration](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-gmx.html) or run the example script at `scripts/lagoon/lagoon-gmx-example.py`.

## Overview

The integration allows Lagoon vaults (which use Gnosis Safe) to trade GMX V2 perpetuals while ensuring that:

1. Only whitelisted asset managers can execute trades
2. Funds can only flow to authorised destinations
3. Only approved markets and collateral tokens can be used

## Architecture

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Asset Manager  │────▶│ TradingStrategyModule│────▶│   Gnosis Safe   │
│   (Hot Wallet)  │     │   (Guard Contract)   │     │  (Vault Funds)  │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
                                  │
                                  │ validates
                                  ▼
                        ┌──────────────────────┐
                        │  GMX ExchangeRouter  │
                        │    (multicall)       │
                        └──────────────────────┘
```

## GMX multicall structure

GMX V2 uses a multicall pattern on the ExchangeRouter to batch order operations. The Python module (`eth_defi/gmx/order/base_order.py`) builds these multicalls automatically.

### Inner calls used by Python module

| Inner Call | Source Function | Purpose |
|------------|-----------------|---------|
| `sendWnt(address,uint256)` | `_send_wnt()` at line 888 | Send ETH for keeper execution fee |
| `sendTokens(address,address,uint256)` | `_send_tokens()` at line 872 | Send ERC20 collateral to order vault |
| `createOrder(tuple)` | `_create_order()` at line 857 | Create the GMX position order |

### Multicall patterns

The Python module uses three different multicall patterns depending on the operation:

**Pattern 1: Native token (ETH) collateral - opening position**
```solidity
ExchangeRouter.multicall([
    sendWnt(orderVault, collateral + executionFee),  // Send ETH (collateral + fee)
    createOrder(CreateOrderParams)                   // Create the order
])
```
Built in `_build_multicall_args()` when `is_native=true` and `is_close=false`.

**Pattern 2: ERC20 collateral - opening position**
```solidity
ExchangeRouter.multicall([
    sendWnt(orderVault, executionFee),              // Send ETH for keeper fees
    sendTokens(token, orderVault, amount),          // Send ERC20 collateral
    createOrder(CreateOrderParams)                  // Create the order
])
```
Built in `_build_multicall_args()` when `is_native=false` and `is_close=false`.

**Pattern 3: Closing position**
```solidity
ExchangeRouter.multicall([
    sendWnt(orderVault, executionFee),              // Send ETH for keeper fees only
    createOrder(CreateOrderParams)                  // Create close order
])
```
Built in `_build_multicall_args()` when `is_close=true`.

## Guard validation

The Guard contract (`GuardV0Base.sol`) validates each component of the multicall:

### 1. sendWnt validation

```solidity
function _validate_gmxSendWnt(bytes memory callData, address orderVault) internal pure {
    (address receiver, ) = abi.decode(callData, (address, uint256));
    require(receiver == orderVault, "GMX sendWnt: invalid receiver");
}
```

**Security**: ETH can only be sent to the configured OrderVault, not to arbitrary addresses.

### 2. sendTokens validation

```solidity
function _validate_gmxSendTokens(bytes memory callData, address orderVault) internal view {
    (address token, address receiver, ) = abi.decode(callData, (address, address, uint256));
    require(receiver == orderVault, "GMX sendTokens: invalid receiver");
    require(isAllowedAsset(token), "GMX sendTokens: token not allowed");
}
```

**Security**:
- Tokens can only be sent to the configured OrderVault
- Only whitelisted tokens can be transferred

### 3. createOrder validation

```solidity
function _validate_gmxCreateOrder(bytes memory callData) internal view {
    // Extract addresses from ABI-encoded CreateOrderParams
    // Offsets are fixed based on the nested tuple structure
    assembly {
        let dataPtr := add(callData, 32)
        receiver := mload(add(dataPtr, 0x220))
        cancellationReceiver := mload(add(dataPtr, 0x240))
        market := mload(add(dataPtr, 0x2a0))
        initialCollateralToken := mload(add(dataPtr, 0x2c0))
    }

    require(isAllowedReceiver(receiver), "GMX createOrder: receiver not whitelisted");
    require(isAllowedReceiver(cancellationReceiver), "GMX createOrder: cancellationReceiver not whitelisted");
    require(isAllowedGMXMarket(market), "GMX createOrder: market not allowed");
    require(isAllowedAsset(initialCollateralToken), "GMX createOrder: collateral not allowed");
}
```

**Security**:
- Order receiver must be in `allowedReceivers` (typically only the Safe address)
- Cancellation receiver must also be whitelisted
- Market must be explicitly whitelisted or `anyAsset` enabled
- Collateral token must be whitelisted

## Whitelist configuration

Before using GMX, the vault owner must configure:

```solidity
// 1. Whitelist the GMX router and its associated contracts
guard.whitelistGMX(exchangeRouter, syntheticsRouter, orderVault, "GMX");

// 2. Whitelist the Safe as the only valid receiver for order profits/refunds
guard.allowReceiver(safeAddress, "Safe vault");

// 3. Whitelist specific markets (or use anyAsset)
guard.whitelistGMXMarket(ethUsdMarket, "ETH/USD");
guard.whitelistGMXMarket(btcUsdMarket, "BTC/USD");

// 4. Whitelist collateral tokens
guard.whitelistToken(usdc, "USDC collateral");
guard.whitelistToken(weth, "WETH collateral");
```

## Security properties

### Funds cannot be redirected to attacker addresses

The `allowedReceivers` whitelist ensures that:

1. **Order profits** go to the Safe (via `receiver` field)
2. **Cancelled order refunds** go to the Safe (via `cancellationReceiver` field)
3. **Execution fee refunds** go to the Safe

An attacker cannot create an order with `receiver=attackerAddress` because the Guard will reject it.

### All multicall components are validated

The Guard iterates through every call in the multicall array and validates each one:

```solidity
for (uint256 i = 0; i < calls.length; i++) {
    bytes4 selector = extractSelector(calls[i]);

    if (selector == SEL_GMX_SEND_WNT) {
        _validate_gmxSendWnt(innerCallData, orderVault);
    } else if (selector == SEL_GMX_SEND_TOKENS) {
        _validate_gmxSendTokens(innerCallData, orderVault);
    } else if (selector == SEL_GMX_CREATE_ORDER) {
        _validate_gmxCreateOrder(innerCallData);
    } else {
        revert("GMX: Unknown function in multicall");
    }
}
```

An attacker cannot:
- Hide a malicious call between valid calls
- Use unknown function selectors
- Skip validation by using empty calls

### Owner-only configuration

All whitelist operations require `onlyGuardOwner`:

```solidity
function whitelistGMX(...) external onlyGuardOwner { ... }
function whitelistGMXMarket(...) external onlyGuardOwner { ... }
function allowReceiver(...) public onlyGuardOwner { ... }
function setAnyAssetAllowed(...) external onlyGuardOwner { ... }
```

An attacker cannot whitelist themselves as a receiver or enable dangerous modes.

## Potential attack vectors (mitigated)

### 1. Malicious receiver in order

**Attack**: Set `receiver` to attacker address to steal profits.

**Mitigation**: `isAllowedReceiver(receiver)` check rejects non-whitelisted addresses.

### 2. Malicious cancellation receiver

**Attack**: Set `cancellationReceiver` to attacker address to steal refunds when orders are cancelled.

**Mitigation**: `isAllowedReceiver(cancellationReceiver)` check.

### 3. Hidden sendWnt to attacker

**Attack**: Include `sendWnt(attacker, amount)` in multicall to steal ETH.

**Mitigation**: `_validate_gmxSendWnt` requires receiver == orderVault.

### 4. Hidden sendTokens to attacker

**Attack**: Include `sendTokens(token, attacker, amount)` to steal tokens.

**Mitigation**: `_validate_gmxSendTokens` requires receiver == orderVault.

### 5. Unknown function injection

**Attack**: Include arbitrary function calls in multicall.

**Mitigation**: Unknown selectors cause revert with "GMX: Unknown function in multicall".

### 6. Short calldata to bypass validation

**Attack**: Send calldata shorter than expected to read garbage values.

**Mitigation**: `require(calls[i].length >= 4, "GMX: call too short")` check.

### 7. Batch valid + invalid orders

**Attack**: Include a valid order followed by an invalid order, hoping validation stops early.

**Mitigation**: Loop validates ALL calls; any invalid call reverts the entire transaction.

### 8. anyAsset mode bypass

**Attack**: Use `anyAsset` mode to bypass receiver checks.

**Mitigation**: `anyAsset` only bypasses asset/market checks, NOT receiver checks.

## ABI encoding details

The GMX `CreateOrderParams` structure uses nested dynamic tuples:

```
CreateOrderParams:
├── addresses (tuple)
│   ├── receiver (address)           @ offset 0x220
│   ├── cancellationReceiver         @ offset 0x240
│   ├── callbackContract             @ offset 0x260
│   ├── uiFeeReceiver                @ offset 0x280
│   ├── market                       @ offset 0x2a0
│   ├── initialCollateralToken       @ offset 0x2c0
│   └── swapPath (address[])         @ dynamic
├── numbers (tuple)
│   └── ... (7 uint256 values)
├── orderType (uint8)
├── decreasePositionSwapType (uint8)
├── isLong (bool)
├── shouldUnwrapNativeToken (bool)
├── autoCancel (bool)
├── referralCode (bytes32)
└── uiFeeKeys (bytes32[])
```

The fixed offsets (0x220, 0x240, 0x2a0, 0x2c0) are calculated based on the ABI encoding rules for nested tuples with a dynamic array (swapPath).

## Test coverage

The integration includes comprehensive tests covering all inner calls and validation paths.

### Inner call coverage matrix

| Inner Call | Guard Validation | Unit Test | Integration Test |
|------------|------------------|-----------|------------------|
| `sendWnt(address,uint256)` | `_validate_gmxSendWnt()` | ✅ | ✅ `test_lagoon_wallet_open_long_position` |
| `sendTokens(address,address,uint256)` | `_validate_gmxSendTokens()` | ✅ | ✅ `test_lagoon_wallet_open_short_position` |
| `createOrder(tuple)` | `_validate_gmxCreateOrder()` | ✅ | ✅ Both integration tests |

### Multicall pattern coverage

| Pattern | Description | Integration Test |
|---------|-------------|------------------|
| `[sendWnt, createOrder]` | Long with native ETH collateral | `test_lagoon_wallet_open_long_position` |
| `[sendWnt, sendTokens, createOrder]` | Short with USDC collateral | `test_lagoon_wallet_open_short_position` |
| `[sendWnt, createOrder]` | Close position (fee only) | Covered by position lifecycle tests |

### Guard validation coverage

| Validation | Guard Function | Unit Test | Integration Test |
|------------|----------------|-----------|------------------|
| sendWnt receiver = orderVault | `_validate_gmxSendWnt()` | - | ✅ via real GMX |
| sendTokens receiver = orderVault | `_validate_gmxSendTokens()` | - | ✅ via real GMX |
| sendTokens token is whitelisted | `_validate_gmxSendTokens()` | ✅ `test_gmx_market_whitelisted` | ✅ via real GMX |
| createOrder receiver whitelisted | `_validate_gmxCreateOrder()` | ✅ `test_receiver_whitelisted` | ✅ via real GMX |
| createOrder cancellationReceiver whitelisted | `_validate_gmxCreateOrder()` | ✅ `test_receiver_whitelisted` | ✅ via real GMX |
| createOrder market whitelisted | `_validate_gmxCreateOrder()` | ✅ `test_gmx_market_whitelisted` | ✅ via real GMX |
| createOrder collateral whitelisted | `_validate_gmxCreateOrder()` | ✅ `test_any_asset_allows_non_whitelisted_asset` | ✅ via real GMX |

### Unit tests (`tests/guard/test_guard_gmx_validation.py`)

- Whitelist configuration tests
- Valid multicall acceptance tests
- Invalid receiver rejection tests
- Invalid market/collateral rejection tests
- anyAsset mode tests
- Security/adversarial input tests

### Integration tests (`tests/gmx/lagoon/test_gmx_lagoon_integration.py`)

- End-to-end tests on Arbitrum fork
- Real GMX contract interactions
- Long and short position opening
- Guard validation with actual ABI encoding

## Recommendations

1. **Always whitelist only the Safe address** as a receiver. Never whitelist external addresses.

2. **Use explicit market whitelisting** rather than `anyAsset=true` when possible.

3. **Review the swapPath** if your strategy uses complex swap routes - the current validation does not check swapPath tokens.

4. **Monitor for GMX contract upgrades** that might change the ABI encoding offsets.

5. **Test thoroughly on fork** before deploying to mainnet.

## Files

| File | Description |
|------|-------------|
| `contracts/guard/src/GuardV0Base.sol` | Guard contract with GMX validation |
| `eth_defi/gmx/order/base_order.py` | GMX order building with multicall patterns |
| `eth_defi/gmx/lagoon/wallet.py` | LagoonGMXTradingWallet adapter for vault trading |
| `eth_defi/erc_4626/vault_protocol/lagoon/vault.py` | LagoonVault Python wrapper |
| `tests/guard/test_guard_gmx_validation.py` | Unit tests for Guard GMX validation |
| `tests/gmx/lagoon/test_gmx_lagoon_integration.py` | Integration tests on Arbitrum fork |
