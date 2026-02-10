# Velora (ParaSwap) integration for Lagoon vaults

## Context

We need a Velora DEX aggregator integration matching the existing CowSwap integration pattern. Velora (formerly ParaSwap) aggregates DEX liquidity across chains. The integration enables Lagoon vault asset managers to execute token swaps through Velora's Market API with GuardBase whitelist protection.

**Key difference from CowSwap**: Velora Market API returns raw transaction calldata that executes atomically in one transaction (no offchain order book or polling). This is simpler than CowSwap's presign/post/wait flow.

**API choice**: Market API (not Delta API) because Delta requires EIP-712 signing which is complex for Safe multisig wallets.

---

## Velora API reference (gathered from developers.velora.xyz)

Velora is the rebranded ParaSwap. All API endpoints still use `api.paraswap.io`.

### Velora Market API

**GET `https://api.paraswap.io/prices`** - Get optimal price route

Required query params:
- `srcToken` (string) - Source token address
- `srcDecimals` (int) - Source token decimals
- `destToken` (string) - Destination token address
- `destDecimals` (int) - Destination token decimals
- `amount` (string) - Amount in WEI/raw units (srcToken if SELL, destToken if BUY)

Key optional params:
- `side` - "SELL" (default) or "BUY"
- `network` - Chain ID (default: 1). Supports: 1 (Ethereum), 10 (Optimism), 56 (BSC), 137 (Polygon), 8453 (Base), 42161 (Arbitrum), 43114 (Avalanche), 100 (Gnosis), 146 (Sonic), 130 (Unichain), 3636 (Plasma)
- `userAddress` - Caller wallet address
- `version` - Protocol version (5 or 6.2, default: 5)
- `maxImpact` - Price impact threshold % (default: 15%)
- `partner` - Project name for analytics
- `excludeDEXS` / `includeDEXS` - Filter exchanges

Response (200):
```json
{
  "blockNumber": 12345678,
  "network": 1,
  "srcToken": "0x...",
  "srcDecimals": 18,
  "srcAmount": "1000000000000000000",
  "destToken": "0x...",
  "destDecimals": 6,
  "destAmount": "3500000000",
  "bestRoute": [...],
  "gasCostUSD": "5.93",
  "contractAddress": "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
  "contractMethod": "multiSwap",
  "srcUSD": "3500.00",
  "destUSD": "3500.00"
}
```

Common errors (400):
- "No routes found with enough liquidity"
- "Token not found. Please pass srcDecimals & destDecimals query params"
- "Estimated_loss_greater_than_max_impact"
- "Price Timeout"

---

**POST `https://api.paraswap.io/transactions/:network`** - Build swap transaction

Path params:
- `network` (int) - Chain ID

Query params:
- `ignoreChecks` (bool) - Skip balance/allowance checks (default: false). **Set to true for vault integration.**
- `ignoreGasEstimate` (bool) - Skip gas estimation (default: false). **Set to true for vault integration.**
- `onlyParams` (bool) - Return only contract params (default: false)

Request body:
- `srcToken` (string) - Source token address
- `srcDecimals` (int) - Source token decimals
- `destToken` (string) - Destination token address
- `destDecimals` (int) - Destination token decimals
- `srcAmount` (int) - Source amount with decimals (required if side=SELL)
- `destAmount` (int) - Destination amount with decimals (required if side=BUY)
- `priceRoute` (object) - **Full priceRoute from /prices response**
- `slippage` (int) - Allowed slippage in basis points (e.g., 250 = 2.5%)
- `userAddress` (string) - msg.sender address (**the Safe address for vault integration**)
- `receiver` (string) - Output recipient (optional, defaults to userAddress)
- `partner` (string) - Project name for analytics
- `deadline` (int) - UNIX timestamp validity

Response (200):
```json
{
  "from": "0x...",
  "to": "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
  "value": "0",
  "data": "0x...",
  "gasPrice": "16000000000",
  "chainId": 1
}
```

The `to` field is the Augustus Swapper address. The `data` field is the raw calldata to execute.

---

**GET `https://api.paraswap.io/swap`** - Combined price + tx (simpler but limited)

Same params as /prices plus `slippage`, `userAddress`, etc. Returns both `priceRoute` and `txParams` in one call. **Lower rate limits, no RFQ DEXes, no gas data.** Prefer the two-step flow for production use.

---

### Velora Delta API (NOT used - documented for reference only)

Intent-based protocol similar to CowSwap. Users sign EIP-712 typed data orders which agents fill.

- `GET /quote` - Get delta pricing with market fallback
- `POST /delta/orders/build` - Build EIP-712 order to sign
- `POST /delta/orders` - Submit signed order for auction
- `GET /delta/orders/:orderId` - Track order status

**Not suitable for Safe multisig wallets** - requires EIP-712 signing from the order owner.

---

### Velora contract addresses

**Augustus Swapper v5** (the swap router - receives calldata):

| Chain | Chain ID | Augustus Swapper |
|-------|----------|-----------------|
| Ethereum | 1 | `0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57` |
| Arbitrum | 42161 | `0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57` |
| Polygon | 137 | `0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57` |
| Optimism | 10 | `0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57` |
| Avalanche | 43114 | `0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57` |
| BSC | 56 | `0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57` |
| Base | 8453 | `0x59C7C832e96D2568bea6db468C1aAdcbbDa08A52` |

**TokenTransferProxy** (users approve THIS contract, NOT Augustus):

| Chain | Chain ID | TokenTransferProxy |
|-------|----------|-------------------|
| Ethereum | 1 | `0x216b4b4ba9f3e719726886d34a177484278bfcae` |
| Arbitrum | 42161 | `0x216B4B4Ba9F3e719726886d34a177484278Bfcae` |
| Polygon | 137 | `0x216b4b4ba9f3e719726886d34a177484278bfcae` |
| Optimism | 10 | `0x216B4B4Ba9F3e719726886d34a177484278Bfcae` |
| Avalanche | 43114 | `0x216b4b4ba9f3e719726886d34a177484278bfcae` |
| BSC | 56 | `0x216b4b4ba9f3e719726886d34a177484278bfcae` |
| Base | 8453 | `0x93aAAe79a53759cD164340E4C8766E4Db5331cD7` |

**CRITICAL**: "Allowance should be given to TokenTransferProxy contract and not to AugustusSwapper. FUNDS MAY BE LOST OTHERWISE!"

---

## Existing CowSwap integration (pattern to follow)

### Module structure
```
eth_defi/cow/
  __init__.py
  constants.py    - COWSWAP_SETTLEMENT, COWSWAP_VAULT_RELAYER, API endpoints per chain
  api.py          - CowAPIError exception, get_cowswap_api() helper
  quote.py        - Quote dataclass, fetch_quote() function
  order.py        - GPv2OrderData TypedDict, post_order() function
  status.py       - wait_order_complete(), CowSwapResult dataclass
```

### Lagoon integration
```
eth_defi/erc_4626/vault_protocol/lagoon/cowswap.py
  - approve_cow_swap()          Approve vault relayer
  - presign_cowswap()           Build presigned order call
  - presign_and_broadcast()     Broadcast + extract from event
  - execute_presigned_cowswap_order()  Post to API + wait
  - presign_and_execute_cowswap()      Combined high-level
```

### Smart contracts
- `contracts/guard/src/GuardV0Base.sol` - `allowedCowSwaps` mapping, `whitelistCowSwap()`, `_swapAndValidateCowSwap()`
- `contracts/safe-integration/src/TradingStrategyModuleV0.sol` - `swapAndValidateCowSwap()` entry point
- `contracts/guard/src/lib/SwapCowSwap.sol` - Order creation/signing library

### Deployment
- `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py`:
  - `deploy_automated_lagoon_vault()` has `cowswap: bool = False`
  - Calls `module.functions.whitelistCowSwap(COWSWAP_SETTLEMENT, COWSWAP_VAULT_RELAYER, "Allow CowSwap")`

### Documentation & tutorial
- `docs/source/api/cowswap/index.rst` - API reference with autosummary
- `docs/source/tutorials/lagoon-cowswap.rst` - Tutorial with `literalinclude` of script
- `scripts/lagoon/lagoon-cowswap-example.py` - Full example (308 lines)
- `tests/lagoon/test_lagoon_cowswap.py` - E2E integration tests

### CowSwap flow (for comparison)
1. `fetch_quote()` - POST /api/v1/quote (optional preview)
2. `approve_cow_swap()` - Approve CowSwap vault relayer for token
3. `presign_cowswap()` - Call `swapAndValidateCowSwap()` on module (creates order onchain)
4. `presign_and_broadcast()` - Broadcast presign tx, extract OrderSigned event
5. `post_order()` - POST order to CowSwap offchain API
6. `wait_order_complete()` - Poll /api/v1/orders/{uid}/status until "traded"

---

## GuardBase architecture

`GuardV0Base.sol` (827 lines) uses selector-driven dispatch:
- `_validateCallInternal()` checks: sender whitelisted, (target, selector) pair whitelisted, then dispatches to protocol-specific validators
- Falls through to `revert("Unknown function selector")` for unrecognised selectors
- CowSwap bypasses this via dedicated `swapAndValidateCowSwap()` method

Key whitelists:
- `allowedAssets` - ERC-20 token whitelist
- `allowedSenders` - Asset manager wallets
- `allowedReceivers` - Trade output recipients
- `allowedApprovalDestinations` - Token approval targets
- `allowedCowSwaps` - CowSwap settlement contracts

`TradingStrategyModuleV0.sol` (154 lines) is a Zodiac Module providing:
- `performCall(target, calldata, value)` - Generic call with guard validation
- `swapAndValidateCowSwap(...)` - CowSwap-specific entry point

---

## New files to create

### 1. Core Python module `eth_defi/velora/`

**`eth_defi/velora/__init__.py`** - Package marker

**`eth_defi/velora/constants.py`** - Addresses and API config
- `VELORA_AUGUSTUS_SWAPPER`: dict[int, str] mapping chain_id to Augustus v5 address
- `VELORA_TOKEN_TRANSFER_PROXY`: dict[int, str] mapping chain_id to proxy address
- `VELORA_API_URL = "https://api.paraswap.io"`
- Pattern: `eth_defi/cow/constants.py`

**`eth_defi/velora/api.py`** - API helpers
- `VeloraAPIError(Exception)`
- `get_augustus_swapper(chain_id: int) -> HexAddress`
- `get_token_transfer_proxy(chain_id: int) -> HexAddress`
- Pattern: `eth_defi/cow/api.py`

**`eth_defi/velora/quote.py`** - Price quoting
- `VeloraQuote` dataclass(slots=True, frozen=True) with `buy_token: TokenDetails`, `sell_token: TokenDetails`, `data: dict`
  - Methods: `get_buy_amount() -> Decimal`, `get_sell_amount() -> Decimal`, `get_price() -> Decimal`, `pformat() -> str`
- `fetch_velora_quote(from_: HexAddress, buy_token: TokenDetails, sell_token: TokenDetails, amount_in: Decimal, api_timeout: datetime.timedelta) -> VeloraQuote`
  - Calls `GET https://api.paraswap.io/prices`
  - Query params: `srcToken=sell_token.address`, `destToken=buy_token.address`, `amount=sell_token.convert_to_raw(amount_in)`, `srcDecimals`, `destDecimals`, `side=SELL`, `network=chain_id`, `userAddress=from_`
- Pattern: `eth_defi/cow/quote.py`

**`eth_defi/velora/swap.py`** - Swap transaction building
- `VeloraSwapTransaction` dataclass(slots=True, frozen=True):
  - `buy_token: TokenDetails`, `sell_token: TokenDetails`
  - `amount_in: Decimal`, `min_amount_out: Decimal`
  - `to: HexAddress` (Augustus Swapper)
  - `calldata: HexBytes` (raw Augustus calldata)
  - `value: int` (ETH value, usually 0)
  - `price_route: dict` (raw priceRoute from /prices)
- `VeloraSwapResult` dataclass(slots=True, frozen=True):
  - `tx_hash: HexBytes`
  - `buy_token: TokenDetails`, `sell_token: TokenDetails`
  - `amount_sold: int` (raw), `amount_bought: int` (raw)
- `fetch_velora_swap_transaction(quote: VeloraQuote, user_address: HexAddress, slippage_bps: int = 250, api_timeout: datetime.timedelta) -> VeloraSwapTransaction`
  - Calls `POST https://api.paraswap.io/transactions/<network>`
  - Body: `srcToken`, `destToken`, `srcAmount`, `destAmount` (from quote), `priceRoute` (from quote.data), `slippage=slippage_bps`, `userAddress=user_address`
  - Query: `ignoreChecks=true`, `ignoreGasEstimate=true`
  - Returns `VeloraSwapTransaction` with `to` and `calldata` from API response

### 2. Lagoon integration

**`eth_defi/erc_4626/vault_protocol/lagoon/velora.py`** - Vault swap functions

```python
# Reuse BroadcastCallback pattern from cowswap.py

def approve_velora(vault: LagoonVault, token: TokenDetails, amount: Decimal) -> ContractFunction:
    """Approve Velora TokenTransferProxy to spend tokens on behalf of vault."""
    # token.approve(VELORA_TOKEN_TRANSFER_PROXY[chain_id], amount)
    # return vault.transact_via_trading_strategy_module(func)

def build_velora_swap(
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    augustus_calldata: HexBytes,
) -> ContractFunction:
    """Build swapAndValidateVelora() call on TradingStrategyModuleV0."""
    # augustus = get_augustus_swapper(chain_id)
    # return module.functions.swapAndValidateVelora(
    #     augustus, sell_token.address, buy_token.address,
    #     amount_in_raw, min_amount_out_raw, augustus_calldata
    # )

def execute_velora_swap(
    asset_manager: HotWallet | HexAddress,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    augustus_calldata: HexBytes,
    broadcast_callback: BroadcastCallback = _default_broadcast_callback,
) -> VeloraSwapResult:
    """Execute a Velora swap through the vault."""
    # 1. build_velora_swap()
    # 2. broadcast via callback
    # 3. Read VeloraSwapExecuted event from receipt
    # 4. Return VeloraSwapResult

def approve_and_execute_velora_swap(
    asset_manager_wallet: HotWallet,
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
    broadcast_callback: BroadcastCallback = _default_broadcast_callback,
    slippage_bps: int = 250,
    api_timeout: datetime.timedelta = datetime.timedelta(seconds=30),
) -> VeloraSwapResult:
    """High-level: fetch quote, build tx, approve, execute."""
    # 1. fetch_velora_quote()
    # 2. fetch_velora_swap_transaction(quote, vault.safe_address, slippage_bps)
    # 3. approve_velora(vault, sell_token, amount_in)
    # 4. execute_velora_swap(...)
```

### 3. Smart contract changes

**New: `contracts/guard/src/lib/SwapVelora.sol`**
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

abstract contract SwapVelora {
    event VeloraSwapExecuted(
        uint256 indexed timestamp,
        address indexed augustusSwapper,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        uint256 minAmountOut
    );
}
```

**Modify: `contracts/guard/src/GuardV0Base.sol`**
```solidity
// Add to state variables (~line 98)
mapping(address destination => bool allowed) public allowedVeloraSwappers;

// Add event
event VeloraSwapperApproved(address augustusSwapper, string notes);

// Add whitelist function
function whitelistVelora(
    address augustusSwapper,
    address tokenTransferProxy,
    string calldata notes
) external onlyGuardOwner {
    allowApprovalDestination(tokenTransferProxy, notes);
    allowedVeloraSwappers[augustusSwapper] = true;
    emit VeloraSwapperApproved(augustusSwapper, notes);
}

function isAllowedVeloraSwapper(address swapper) public view returns (bool) {
    return allowedVeloraSwappers[swapper] == true;
}
```

**Modify: `contracts/safe-integration/src/TradingStrategyModuleV0.sol`**
```solidity
// Add after swapAndValidateCowSwap()

function swapAndValidateVelora(
    address augustusSwapper,
    address tokenIn,
    address tokenOut,
    uint256 amountIn,
    uint256 minAmountOut,
    bytes memory augustusCalldata
) public {
    bool success;
    bytes memory response;

    // 1. Validate permissions
    require(isAllowedVeloraSwapper(augustusSwapper), "Velora not enabled");
    require(isAllowedSender(msg.sender), "Sender not asset manager");
    require(isAllowedAsset(tokenIn), "tokenIn not allowed");
    require(isAllowedAsset(tokenOut), "tokenOut not allowed");

    // 2. Record pre-swap balance of output token
    address safeAddress = avatar();
    uint256 preBalance = IERC20(tokenOut).balanceOf(safeAddress);

    // 3. Execute Augustus calldata on Safe
    (success, response) = execAndReturnData(
        augustusSwapper,
        0,
        augustusCalldata,
        Enum.Operation.Call
    );

    if (!success) {
        assembly {
            revert(add(response, 0x20), mload(response))
        }
    }

    // 4. Post-balance slippage check
    uint256 postBalance = IERC20(tokenOut).balanceOf(safeAddress);
    require(
        postBalance >= preBalance + minAmountOut,
        "Insufficient output amount"
    );

    // 5. Emit event
    emit VeloraSwapExecuted(
        block.timestamp,
        augustusSwapper,
        tokenIn,
        tokenOut,
        amountIn,
        postBalance - preBalance,
        minAmountOut
    );
}
```

Bump version: `getTradingStrategyModuleVersion()` returns `"v0.1.4"` (was `"v0.1.3"`)

### 4. Deployment integration

**Modify: `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py`**

In `setup_guard()` - add `velora: bool = False` parameter, add block after CowSwap whitelisting (~line 910):
```python
if velora:
    chain_id = web3.eth.chain_id
    augustus = get_augustus_swapper(chain_id)
    proxy = get_token_transfer_proxy(chain_id)
    logger.info("Whitelisting Velora: Augustus %s, TokenTransferProxy %s", augustus, proxy)
    tx_hash = _broadcast(module.functions.whitelistVelora(augustus, proxy, "Allow Velora"))
    assert_transaction_success_with_explanation(web3, tx_hash)
```

In `deploy_automated_lagoon_vault()` - add `velora: bool = False` parameter, pass through to `setup_guard()`.

Add import: `from eth_defi.velora.api import get_augustus_swapper, get_token_transfer_proxy`

### 5. Documentation

**New: `docs/source/api/velora/index.rst`**
- Pattern: `docs/source/api/cowswap/index.rst`
- Title: "Velora"
- Intro about Velora (formerly ParaSwap) DEX aggregator
- Link to `developers.velora.xyz <https://developers.velora.xyz>`__
- autosummary for: `eth_defi.velora.api`, `eth_defi.velora.constants`, `eth_defi.velora.quote`, `eth_defi.velora.swap`

**Modify: `docs/source/api/index.rst`**
- Add `velora/index` to toctree

**New: `docs/source/tutorials/lagoon-velora.rst`**
- Pattern: `docs/source/tutorials/lagoon-cowswap.rst`
- Title: "Lagoon and Velora integration"
- `literalinclude` of `scripts/lagoon/lagoon-velora-example.py`

**Modify: `docs/source/tutorials/index.rst`**
- Add `lagoon-velora` to toctree

### 6. Example script and tests

**New: `scripts/lagoon/lagoon-velora-example.py`**
- Pattern: `scripts/lagoon/lagoon-cowswap-example.py`
- Deploy vault with `velora=True`, deposit WETH, swap WETH -> USDC.e via Velora on Arbitrum
- Env vars: `JSON_RPC_ARBITRUM`, `PRIVATE_KEY`, `ETHERSCAN_API_KEY`

**New: `tests/lagoon/test_lagoon_velora.py`**
- Pattern: `tests/lagoon/test_lagoon_cowswap.py`
- `test_velora_quote()` - Quote against real Velora API
- `test_lagoon_velora()` - Full E2E: deploy, deposit, settle, approve, swap, verify balances

---

## Execution flow summary

```
Asset Manager (Python)
  |
  +-- 1. fetch_velora_quote()             GET /prices
  +-- 2. fetch_velora_swap_transaction()  POST /transactions/:network
  +-- 3. approve_velora()                 token.approve(TokenTransferProxy)
  |       \-- via performCall() on TradingStrategyModuleV0
  \-- 4. execute_velora_swap()
          \-- swapAndValidateVelora() on TradingStrategyModuleV0
              +-- Validate: sender, Augustus, tokens whitelisted
              +-- Record pre-balance of buy token on Safe
              +-- execAndReturnData(Augustus, calldata) on Safe
              +-- Check post-balance >= pre-balance + minAmountOut
              \-- Emit VeloraSwapExecuted event
```

---

## Implementation order

1. Smart contracts (GuardV0Base.sol, SwapVelora.sol, TradingStrategyModuleV0.sol) + compile ABIs
2. Core Python module (`eth_defi/velora/`)
3. Lagoon integration (`eth_defi/erc_4626/vault_protocol/lagoon/velora.py`)
4. Deployment integration
5. Documentation (API docs, tutorial)
6. Example script + tests

## Verification

- Compile contracts with `forge build` in `contracts/guard/` and `contracts/safe-integration/`
- Run Velora quote test: `source .local-test.env && poetry run pytest tests/lagoon/test_lagoon_velora.py::test_velora_quote -s`
- Run full E2E test: `source .local-test.env && poetry run pytest tests/lagoon/test_lagoon_velora.py::test_lagoon_velora -s`
- Run example script manually: `source .local-test.env && poetry run python scripts/lagoon/lagoon-velora-example.py`
- Build docs: `source .local-test.env && make build-docs`

## File summary

### New files (11)
1. `eth_defi/velora/__init__.py`
2. `eth_defi/velora/constants.py`
3. `eth_defi/velora/api.py`
4. `eth_defi/velora/quote.py`
5. `eth_defi/velora/swap.py`
6. `eth_defi/erc_4626/vault_protocol/lagoon/velora.py`
7. `contracts/guard/src/lib/SwapVelora.sol`
8. `docs/source/api/velora/index.rst`
9. `docs/source/tutorials/lagoon-velora.rst`
10. `scripts/lagoon/lagoon-velora-example.py`
11. `tests/lagoon/test_lagoon_velora.py`

### Modified files (5)
1. `contracts/guard/src/GuardV0Base.sol` - Add Velora whitelisting
2. `contracts/safe-integration/src/TradingStrategyModuleV0.sol` - Add `swapAndValidateVelora()`, bump version
3. `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py` - Add `velora` parameter
4. `docs/source/api/index.rst` - Add velora to toctree
5. `docs/source/tutorials/index.rst` - Add lagoon-velora to toctree
