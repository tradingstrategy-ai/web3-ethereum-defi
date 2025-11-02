# GMX Fork Testing Utilities

This package contains utilities for testing GMX trading on Anvil mainnet forks using pure Python (Approach 2).

## Files

### `fork_helpers.py`

Low-level Anvil fork utilities:
- `set_eth_balance()` - Set native ETH balance on fork
- `set_erc20_balance()` - Set ERC20 token balance via storage manipulation
- `get_active_keeper()` - Query active ORDER_KEEPER from RoleStore
- `impersonate_account()` - Impersonate account for transaction signing
- `stop_impersonating_account()` - Stop impersonating
- `mine_block()` - Mine new blocks
- `set_storage_at()` - Direct storage manipulation
- `get_role_store_contract()` - Get RoleStore contract instance
- `get_order_handler_contract()` - Get OrderHandler contract instance

### `event_parser.py`

Transaction log parsing utilities:
- `extract_order_key_from_receipt()` - Get order key from OrderCreated event
- `extract_position_key_from_receipt()` - Get position key from PositionIncrease event
- `extract_position_decrease_key_from_receipt()` - Get key from PositionDecrease event
- `extract_event_by_signature()` - Extract any event by signature
- `extract_all_events_by_signature()` - Extract all matching events
- `get_event_topic()` - Get event topic hash
- `extract_position_key_from_receipt_generic()` - Robust position key extraction

### `keeper_executor.py`

Keeper simulation utilities:
- `execute_order_as_keeper()` - Execute pending order as keeper (main function)
- `execute_multiple_orders()` - Execute multiple orders sequentially
- `_prepare_default_prices()` - Convert USD prices to GMX format

## Quick Start

```python
from web3 import Web3
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.config import GMXConfig
from tests.gmx.setup_test.fork_helpers import set_eth_balance, set_erc20_balance
from tests.gmx.setup_test.event_parser import extract_order_key_from_receipt
from tests.gmx.setup_test.keeper_executor import execute_order_as_keeper

# Connect to Anvil fork
web3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))

# Set wallet balances on fork
set_eth_balance(web3, wallet_address, 100 * 10**18)
set_erc20_balance(web3, usdc_address, wallet_address, 100_000 * 10**6)

# Create order via Python SDK
config = GMXConfig(web3, user_wallet_address=wallet_address)
trader = GMXTrading(config)
order = trader.open_position(market_symbol="ETH", ...)

# Submit order
signed_tx = wallet.sign_transaction_with_new_nonce(order.transaction)
tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

# Extract order key
order_key = extract_order_key_from_receipt(receipt)

# Execute as keeper (this is the magic!)
exec_receipt = execute_order_as_keeper(
    web3=web3,
    order_key=order_key,
    chain="arbitrum",
    eth_price_usd=3892,
    usdc_price_usd=1,
)
```

## Architecture

Approach 2 breaks down into:

1. **Order Creation** - Python SDK creates unsigned transaction
2. **Order Submission** - User signs and submits to ExchangeRouter
3. **Order Extraction** - Event parser extracts order key from logs
4. **Keeper Execution** - Keeper executor simulates keeper (key part)
5. **Position Verification** - Query positions via SDK data classes

All in pure Python - no Solidity needed!

## Testing

See `tests/gmx/debug.py` for a complete working example showing all steps.

Run it with:
```bash
# Terminal 1
anvil --fork-url https://arb1.arbitrum.io/rpc --fork-block-number 392496384

# Terminal 2
python tests/gmx/debug.py
```

## Documentation

Full guide with API reference: `docs/gmx_fork_testing_guide.md`

## Design Decisions

- **Tests, not SDK**: These utilities live in tests/ folder, not eth_defi/gmx/, because they're testing-specific (Anvil, impersonation, etc.)
- **No emojis**: Clean logging output without Unicode characters
- **Modular design**: Each module is independent and can be used separately
- **Anvil-first**: Built for Anvil, with fallback support for Tenderly
