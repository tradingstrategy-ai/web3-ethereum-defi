# GMX Order Keeper Execution Guide

## Overview

Instead of manually implementing keeper execution logic in Python (handling oracle setup, keeper roles, etc.), you can use the **`GmxOrderExecutor`** contract which delegates to the proven Foundry test contract implementation.

This approach:
- ✅ Avoids reimplementing complex oracle provider setup
- ✅ Uses the exact logic from working Foundry tests
- ✅ Simplifies Python code significantly
- ✅ Ensures consistency with mainnet behavior

## Architecture

```
┌──────────────────────────────────┐
│   Python Test (debug.py)         │
│  - Creates order via SDK         │
│  - Gets order key                │
│  - Calls GmxOrderExecutor        │
└────────────┬──────────────────────┘
             │ orderKey
             ▼
┌──────────────────────────────────────────────────┐
│   GmxOrderExecutor Contract (Solidity)           │
│  - Extends GmxForkHelpers                        │
│  - executeOrderWithOracle()                      │
│  - Handles oracle setup                          │
│  - Calls keeper execution                        │
└────────────┬─────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────┐
│   GMX Protocol Contracts         │
│  - OrderHandler.executeOrder()   │
│  - Updates positions             │
│  - Emits events                  │
└──────────────────────────────────┘
```

## Usage in Python

### 1. Deploy the Contract

```python
from web3 import Web3
from eth_defi.abi import get_contract

# After fork is created
executor_contract_json = {
    "abi": [...],  # Load from artifacts
    "bytecode": "..."
}

# Deploy executor
executor_tx = web3.eth.contract(
    abi=executor_contract_json["abi"]
).constructor().transact()

executor_address = web3.eth.get_transaction_receipt(executor_tx)['contractAddress']
executor = web3.eth.contract(
    address=executor_address,
    abi=executor_contract_json["abi"]
)
```

### 2. Initialize with GMX Contracts

```python
# Initialize executor with GMX contract addresses
executor.functions.initialize(
    exchangeRouter_address,
    orderHandler_address,
    oracle_address,
    reader_address,
    dataStore_address,
    roleStore_address,
    oracleStore_address,
    weth_address,
    usdc_address
).transact({'from': account.address})
```

### 3. Execute Orders

```python
# Create order via SDK (as before)
order_result = increase_order.create_increase_order(...)
order_key = order_result.order_key

# Execute via contract (simple!)
tx_hash = executor.functions.executeOrderWithDefaultPrices(
    order_key,
    user_address
).transact({'from': keeper_address})

# Or with custom prices
tx_hash = executor.functions.executeOrderWithOracle(
    order_key,
    eth_price_usd=3892,
    usdc_price_usd=1,
    executingUser=user_address
).transact({'from': keeper_address})
```

## Contract Methods

### `executeOrderWithOracle(orderKey, ethPriceUsd, usdcPriceUsd, executingUser)`

Execute an order with custom oracle prices.

**Parameters:**
- `orderKey`: The order to execute (bytes32)
- `ethPriceUsd`: ETH price in USD, unscaled (e.g., 3892 for $3,892)
- `usdcPriceUsd`: USDC price in USD (typically 1)
- `executingUser`: User address for position key derivation

**Returns:**
- `positionKey`: The resulting position key (for assertions/verification)

**Example:**
```python
position_key = executor.functions.executeOrderWithOracle(
    order_key,
    3892,  # $3,892 ETH
    1,     # $1 USDC
    user_address
).call()

assert position_key != "0x" + "0" * 64, "Position should exist"
```

### `executeOrderWithDefaultPrices(orderKey, executingUser)`

Execute an order using standard test prices (ETH: $3,892, USDC: $1).

**Parameters:**
- `orderKey`: The order to execute
- `executingUser`: User address for position key derivation

**Returns:**
- `positionKey`: The resulting position key

**Example:**
```python
position_key = executor.functions.executeOrderWithDefaultPrices(
    order_key,
    user_address
).call()
```

### `executeDecreaseOrder(orderKey, ethPriceUsd, usdcPriceUsd)`

Execute a decrease order (closing a position).

**Parameters:**
- `orderKey`: The decrease order to execute
- `ethPriceUsd`: ETH price for oracle
- `usdcPriceUsd`: USDC price for oracle

**Example:**
```python
executor.functions.executeDecreaseOrder(
    close_order_key,
    3892,
    1
).transact({'from': keeper_address})
```

## What the Contract Does Internally

The `GmxOrderExecutor` handles everything that was previously manual:

1. **Oracle Provider Setup**
   - Sets up mock oracle provider bytecode (via `vm.etch`)
   - Provides proper Chainlink Data Streams interface

2. **Keeper Management**
   - Retrieves active keeper from RoleStore
   - Executes orders with keeper privileges (`vm.startPrank`)

3. **Oracle Parameters**
   - Builds `SetPricesParams` with token and provider addresses
   - Uses `GmxArbitrumAddresses.CHAINLINK_DATA_STREAM_PROVIDER`
   - Provides proper array structures for execution

4. **Position Key Derivation**
   - Calculates position key for assertions
   - Accounts for market, collateral, isLong flags

## Advantages Over Manual Implementation

### Before (Manual Python)
```python
# Complex oracle setup
oracle_provider_bytecode = get_mock_oracle_bytecode()
tenderly_set_code(web3, oracle_address, oracle_provider_bytecode)

# Manual keeper lookup
keeper = get_keeper_address_from_role_store()

# Manual oracle params
oracle_params = build_oracle_params(weth, usdc, chainlink_provider)

# Manual execution
orderHandler.executeOrder(order_key, oracle_params)
```

### After (Using Contract)
```python
# One simple call!
executor.functions.executeOrderWithDefaultPrices(
    order_key,
    user_address
).call()
```

## Testing Keeper Execution

```python
def test_keeper_execution():
    # Create order
    order_result = increase_order.create_increase_order(...)
    order_key = order_result.order_key

    # Execute via contract (replaces all manual keeper logic)
    position_key = executor.functions.executeOrderWithDefaultPrices(
        order_key,
        user_address
    ).call()

    # Verify position exists
    assert position_key != "0x" + "0" * 64

    # Verify position state (if needed)
    position = reader.functions.getPosition(
        datastore_address,
        position_key
    ).call()
    assert position['sizeInUsd'] > 0
```

## Integration with debug.py

Replace keeper execution section in `tests/gmx/debug.py` with:

```python
# Initialize executor once
executor.functions.initialize(
    gmx_config.contracts.exchange_router,
    gmx_config.contracts.order_handler,
    gmx_config.contracts.oracle,
    gmx_config.contracts.reader,
    gmx_config.contracts.data_store,
    gmx_config.contracts.role_store,
    gmx_config.contracts.oracle_store,
    gmx_config.chain_tokens.weth,
    gmx_config.chain_tokens.usdc
).transact({'from': deployer})

# Later, execute orders
try:
    position_key = executor.functions.executeOrderWithDefaultPrices(
        order_key,
        user_address
    ).call()
    console.print("✓ Order executed successfully", position_key)
except Exception as e:
    console.print(f"✗ Keeper execution failed: {e}")
```

## Notes

- The contract handles `vm.startPrank` and `vm.stopPrank` internally
- Oracle provider setup matches the Foundry test implementation exactly
- Position keys are returned for easy assertion and verification
- Default prices (ETH: $3,892, USDC: $1) match the reference mainnet order execution

## Deployment

To deploy the contract programmatically from Python:

```python
from web3 import Web3
from eth_account import Account

# Load contract ABI/bytecode
with open("out/GmxOrderExecutor.sol/GmxOrderExecutor.json") as f:
    contract_data = json.load(f)

# Deploy
contract = web3.eth.contract(abi=contract_data["abi"])
tx_hash = contract.constructor().transact({
    'from': deployer_address,
    'gas': 3000000,
    'data': contract_data["bytecode"]["object"]
})

executor_address = web3.eth.get_transaction_receipt(tx_hash)['contractAddress']
```

## Troubleshooting

### "No ORDER_KEEPERs found"
- The keeper address list is empty in RoleStore
- Ensure fork is at a block where keepers exist

### "Oracle provider not set up"
- Check that mock oracle bytecode is being deployed
- Verify `_setupMockOracleProvider()` implementation

### Position key is zero
- Order may have failed to execute
- Check order status in OrderBook
- Verify oracle prices are reasonable
