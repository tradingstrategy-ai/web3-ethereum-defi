# GMX Subsquid GraphQL Integration

This module provides GraphQL-based data access for GMX positions and analytics using the [Subsquid indexer](https://gmx.squids.live/).


**Important**: When closing positions, always use `position_size_usd_raw` from `GetOpenPositions` (contract-based) to ensure exact matching. The GraphQL endpoint is for analytics and viewing, not for trade execution.

## Usage

### Basic Setup

```python
from eth_defi.gmx.graphql.client import GMXSubsquidClient

# Initialize client
client = GMXSubsquidClient(chain="arbitrum")  # or "avalanche" or "arbitrum_sepolia"

# Optional: Use custom endpoint
client = GMXSubsquidClient(custom_endpoint="https://your-endpoint.com/graphql")
```

### Get Open Positions

```python
# Get all open positions for an account
positions = client.get_positions(
    account="0x6fa415E36Ac2a20499956C1CCe8a361a3E419a4D",
    only_open=True,
    limit=100
)

# Process positions
for pos in positions:
    # Raw format (all values are strings with 30 decimals)
    print(f"Position: {pos['market']}")
    print(f"  Size (raw): {pos['sizeInUsd']}")
    print(f"  Direction: {'LONG' if pos['isLong'] else 'SHORT'}")

    # Human-readable format
    formatted = GMXSubsquidClient.format_position(pos)
    print(f"  Size (USD): ${formatted['size_usd']:,.2f}")
    print(f"  Unrealized PnL: ${formatted['unrealized_pnl']:,.2f}")
    print(f"  Leverage: {formatted['leverage']:.2f}x")
```

### Get PnL Summary

```python
# Get PnL summary across different time periods
pnl_summary = client.get_pnl_summary(
    account="0x6fa415E36Ac2a20499956C1CCe8a361a3E419a4D"
)

for period in pnl_summary:
    period_name = period["bucketLabel"]  # "today", "week", "month", "year", "all"
    total_pnl = float(GMXSubsquidClient.from_fixed_point(period["pnlUsd"]))
    wins = period["wins"]
    losses = period["losses"]

    print(f"{period_name.upper()}: ${total_pnl:,.2f} ({wins}W/{losses}L)")
```

### Get Position History

```python
# Get recent position changes
changes = client.get_position_changes(
    account="0x6fa415E36Ac2a20499956C1CCe8a361a3E419a4D",
    limit=50
)

for change in changes:
    size = float(GMXSubsquidClient.from_fixed_point(change["sizeInUsd"]))
    print(f"Position change: ${size:,.2f}")
```

### Get Account Statistics

```python
# Get enhanced account stats with win/loss and capital metrics
stats = client.get_account_stats(
    account="0x6fa415E36Ac2a20499956C1CCe8a361a3E419a4D"
)

if stats:
    volume = float(GMXSubsquidClient.from_fixed_point(stats["volume"]))
    realized_pnl = float(GMXSubsquidClient.from_fixed_point(stats["realizedPnl"]))
    max_capital = float(GMXSubsquidClient.from_fixed_point(stats["maxCapital"]))
    net_capital = float(GMXSubsquidClient.from_fixed_point(stats["netCapital"]))

    print(f"Total Volume: ${volume:,.2f}")
    print(f"Realized PnL: ${realized_pnl:,.2f}")
    print(f"Closed Positions: {stats['closedCount']}")
    print(f"Win/Loss: {stats['wins']}W / {stats['losses']}L")
    print(f"Max Capital: ${max_capital:,.2f}")
    print(f"Net Capital: ${net_capital:,.2f}")
```

### Check if Account is "Large"

```python
# Classify accounts based on trading volume
# Based on GMX interface criteria:
# - Max daily volume > $340,000
# - 14-day volume > $1,800,000
# - All-time volume > $5,800,000
is_large = client.is_large_account(
    account="0x6fa415E36Ac2a20499956C1CCe8a361a3E419a4D"
)

if is_large:
    print("This is a high-volume trading account")
else:
    print("This is a regular trading account")
```

## Working with Raw Values

Values in the GraphQL API use different decimal precisions depending on the field:

```python
# USD values: 30 decimals
raw_size = "8625000000000000000000000000000"  # This is $8.625
size = GMXSubsquidClient.from_fixed_point(raw_size, decimals=30)
print(f"${float(size):.2f}")  # $8.63

# Collateral amounts: Depends on token
# - USDC/USDT: 6 decimals
# - ETH/WETH: 18 decimals
# - WBTC: 8 decimals
# The client automatically detects token decimals

# Entry price: 18 decimals
raw_entry_price = "3941148315941020859138"
entry_price = GMXSubsquidClient.from_fixed_point(raw_entry_price, decimals=18)
print(f"${float(entry_price):.2f}")  # $3941.15

# Leverage: 4 decimals (10000 = 1x leverage)
raw_leverage = "72480"
leverage = GMXSubsquidClient.from_fixed_point(raw_leverage, decimals=4)
print(f"{float(leverage):.2f}x")  # 7.25x
```

### Automatic Decimal Handling

The `format_position()` method automatically handles different decimal precisions:

```python
# Fetch position
positions = client.get_positions(account="0x...", only_open=True, limit=1)
formatted = GMXSubsquidClient.format_position(positions[0])

# All values are already converted to floats with correct decimals
print(f"Collateral: ${formatted['collateral_amount']:,.2f}")  # Automatically detects USDC (6 decimals) vs ETH (18 decimals)
print(f"Entry Price: ${formatted['entry_price']:,.2f}")  # Uses 18 decimals
print(f"Leverage: {formatted['leverage']:.2f}x")  # Uses 4 decimals
print(f"Size: ${formatted['size_usd']:,.2f}")  # Uses 30 decimals
```

## Complete Example

See `tests/gmx/test_graphql_client.py` for a complete working example with rich formatting.

```bash
# Run the demo
python tests/gmx/test_graphql_client.py
```

## Important Notes

### Address Case Sensitivity

**WARNING: The Subsquid GraphQL endpoint is case-sensitive for addresses!**

```python
# This works
positions = client.get_positions("0x6fa415E36Ac2a20499956C1CCe8a361a3E419a4D")

# This returns 0 results
positions = client.get_positions("0x6fa415e36ac2a20499956c1cce8a361a3e419a4d")
```

Always use the checksummed address format.

### When Closing Positions

When closing positions, DO NOT use the GraphQL size values directly. Instead:

1. Fetch position from `GetOpenPositions` (contract-based)
2. Use `position_size_usd_raw` field (exact 30-decimal value from blockchain)
3. Pass this raw value to `close_position()`

```python
from eth_defi.gmx.core import GetOpenPositions
from eth_defi.gmx.trading import GMXTrading

# Get position from contract (source of truth)
positions = position_verifier.get_data(wallet_address)
position_key, position = list(positions.items())[0]

# CRITICAL: Use raw value for exact match
position_size_usd_raw = position["position_size_usd_raw"]

# Close position with exact on-chain value
trading.close_position(
    size_delta_usd=position_size_usd_raw,  # Exact raw value
    # ... other parameters
)
```

## API Endpoints

- **Arbitrum**: https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql
- **Avalanche**: https://gmx.squids.live/gmx-synthetics-avalanche:prod/api/graphql
- **Arbitrum Sepolia**: https://gmx.squids.live/gmx-synthetics-arb-sepolia:prod/api/graphql

## Available Data

### Position Fields

```python
{
    "id": "0x...",
    "positionKey": "0x...",
    "account": "0x...",
    "market": "0x...",
    "collateralToken": "0x...",
    "isLong": True/False,
    "sizeInUsd": "8625000000000000000000000000000",  # 30 decimals
    "collateralAmount": "...",
    "entryPrice": "...",
    "realizedPnl": "...",
    "unrealizedPnl": "...",
    "realizedFees": "...",
    "unrealizedFees": "...",
    "leverage": "...",
    "openedAt": 1234567890
}
```

### PnL Summary Fields

```python
{
    "bucketLabel": "week",  # today, yesterday, week, month, year, all
    "pnlUsd": "...",  # Total PnL
    "realizedPnlUsd": "...",  # Realized PnL
    "unrealizedPnlUsd": "...",  # Unrealized PnL
    "volume": "...",  # Trading volume
    "wins": 10,  # Winning trades
    "losses": 5,  # Losing trades
    "winsLossesRatioBps": "20000",  # Win/loss ratio in basis points
    "usedCapitalUsd": "..."  # Total capital used
}
```

## Error Handling

```python
try:
    positions = client.get_positions(account="0x...")
except requests.HTTPError as e:
    print(f"HTTP error: {e}")
except ValueError as e:
    print(f"GraphQL error: {e}")
```

## Testing

```bash
# Run unit tests
pytest tests/gmx/test_graphql_client.py -v

# Run with real account data
python tests/gmx/test_graphql_client.py
```
