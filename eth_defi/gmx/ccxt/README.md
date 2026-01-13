# GMX CCXT adapter

A [CCXT](https://docs.ccxt.com/)-compatible adapter for [GMX](https://gmx.io/) perpetual futures exchange. This adapter provides familiar CCXT-style methods for market data, position management, and order execution on GMX.

**Note:** For high-level overview and Core Trading API, see the [main GMX README](../README.md).

## Initialisation

### Read-only mode (no wallet)

```python
from eth_defi.gmx.ccxt import GMX

gmx = GMX({
    "rpcUrl": "https://arb1.arbitrum.io/rpc",
})

gmx.load_markets()
```

### Trading mode (with wallet)

```python
from eth_defi.gmx.ccxt import GMX

gmx = GMX({
    "rpcUrl": "https://arb1.arbitrum.io/rpc",
    "privateKey": "0x...",  # Your wallet private key
})

gmx.load_markets()
```

## Configuration options

### Constructor parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rpcUrl` | str | required | Arbitrum RPC endpoint URL |
| `privateKey` | str | optional | Wallet private key (required for trading) |
| `executionBuffer` | float | 2.2 | Gas fee buffer multiplier for order execution |
| `defaultSlippage` | float | 0.003 | Default slippage tolerance (0.3%) |
| `verbose` | bool | false | Enable debug logging |

### Why executionBuffer is needed

GMX orders are executed by keeper bots, not directly by your transaction. The `executionBuffer` multiplies the estimated execution fee to ensure:

1. **Reliable execution** - Keepers require a minimum fee to process orders
2. **Gas price fluctuations** - Buffer covers spikes in network gas prices
3. **Complex orders** - SL/TP bundled orders use higher buffer (2.5x default)

If orders fail with "insufficient execution fee", increase the buffer.

### Options field

Additional options can be set via the `options` field:

```python
gmx = GMX({
    "rpcUrl": "https://arb1.arbitrum.io/rpc",
    "executionBuffer": 2.5,
    "options": {
        "graphql_only": False,
        "rest_api_mode": True,
        "disable_market_cache": False,
    }
})
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `graphql_only` | bool | false | Force GraphQL-only market loading (uses Subsquid) |
| `rest_api_mode` | bool | true | Use REST API for market loading (fastest) |
| `disable_market_cache` | bool | false | Disable disk caching of market data |
| `market_cache_dir` | str | auto | Custom directory for market cache files |

**Note:** REST API mode is fastest (~1-2s), GraphQL is fallback (~1-2s), RPC is slowest but most comprehensive (~90-200s). Testnet (Arbitrum Sepolia) automatically uses RPC mode.

## CCXT methods implemented

### Market data

| Method | Description | Notes |
|--------|-------------|-------|
| `load_markets()` | Load all available markets | Caches results, use `reload=True` to refresh |
| `fetch_markets()` | Fetch fresh market data | Returns list format (not cached) |
| `fetch_ticker(symbol)` | Current price and 24h stats | High/low calculated from OHLCV |
| `fetch_tickers(symbols)` | Batch ticker fetch | Pass `None` for all markets |
| `fetch_ohlcv(symbol, timeframe, since, limit)` | Historical candlestick data | Volume always 0 (GMX limitation) |
| `fetch_trades(symbol, since, limit)` | Recent public trades | Derived from position events |
| `fetch_currencies()` | Token metadata | Decimals, addresses |
| `fetch_time()` | Blockchain timestamp | - |
| `fetch_status()` | API health check | Checks GMX API, Subsquid, Web3 |

### Account and positions

| Method | Description | Notes |
|--------|-------------|-------|
| `fetch_balance()` | Token balances | Wallet token balances |
| `fetch_positions(symbols)` | Open positions | Includes PnL, leverage, liquidation price |
| `fetch_open_orders(symbol)` | Pending orders | Returns positions as order-like structures |
| `fetch_my_trades(symbol, since, limit)` | Trade history | - |

### Trading

| Method | Description | Notes |
|--------|-------------|-------|
| `create_order(symbol, type, side, amount, price, params)` | Generic order creation | Supports all order types |
| `create_market_buy_order(symbol, amount, params)` | Open long position | Use `size_usd` in params |
| `create_market_sell_order(symbol, amount, params)` | Open short position | Use `size_usd` in params |
| `create_limit_buy_order(symbol, amount, price, params)` | Limit long order | Price is trigger price |
| `create_limit_sell_order(symbol, amount, price, params)` | Limit short order | Price is trigger price |
| `set_leverage(leverage, symbol)` | Configure leverage | 1.1x to market max (up to 100x) |

### Derivatives-specific

| Method | Description | Notes |
|--------|-------------|-------|
| `fetch_funding_rate(symbol)` | Current funding rate | Per-second rate |
| `fetch_funding_rate_history(symbol, since, limit)` | Historical funding rates | - |
| `fetch_open_interest(symbol)` | Current open interest | Long + short aggregated |
| `fetch_open_interest_history(symbol, timeframe, since, limit)` | Historical OI | - |
| `fetch_open_interests(symbols)` | Batch OI fetch | - |
| `fetch_leverage_tiers(symbols)` | Leverage tier information | - |

## GMX-specific extensions

Parameters unique to GMX that extend standard CCXT:

| Parameter | Type | Description |
|-----------|------|-------------|
| `size_usd` | float | Position size in USD (alternative to base currency amount) |
| `leverage` | float | Leverage multiplier (1.1x to 100x) |
| `collateral_symbol` | str | Collateral token symbol (e.g., "USDC", "ETH") |
| `execution_buffer` | float | Gas fee multiplier for execution (default 2.2) |
| `slippage_percent` | float | Slippage tolerance (default 0.003 = 0.3%) |
| `stopLoss` | dict | Stop-loss config: `{triggerPrice, triggerPercent, closePercent}` |
| `takeProfit` | dict | Take-profit config: `{triggerPrice, triggerPercent, closePercent}` |
| `reduceOnly` | bool | Close position instead of opening new one |

## Order examples

### Market orders

```python
# Long position
order = gmx.create_market_buy_order(
    "ETH/USDC:USDC",
    0,  # Ignored when size_usd provided
    {
        "size_usd": 1000,
        "leverage": 3.0,
        "collateral_symbol": "USDC",
    },
)

# Short position
order = gmx.create_market_sell_order(
    "BTC/USDC:USDC",
    0,
    {
        "size_usd": 500,
        "leverage": 2.0,
        "collateral_symbol": "USDC",
    },
)

# Close position
order = gmx.create_order(
    "ETH/USDC:USDC",
    "market",
    "sell",  # Opposite of position direction
    0,
    None,
    {"size_usd": 1000, "reduceOnly": True},
)
```

### Limit orders

Execute when market price reaches trigger price. Order remains pending until conditions are met.

- **Long limit:** Trigger price BELOW current price (buy the dip)
- **Short limit:** Trigger price ABOVE current price (sell the rally)

```python
# Limit long - triggers when price drops to $1900
order = gmx.create_limit_buy_order(
    "ETH/USDC:USDC",
    0,
    1900.0,  # Trigger price
    {
        "size_usd": 1000,
        "leverage": 3.0,
        "collateral_symbol": "USDC",
    },
)

# Limit short - triggers when price rises to $4000
order = gmx.create_limit_sell_order(
    "ETH/USDC:USDC",
    0,
    4000.0,  # Trigger price
    {
        "size_usd": 1000,
        "leverage": 2.0,
        "collateral_symbol": "USDC",
    },
)
```

### Stop-loss and take-profit (bundled)

Create position with SL/TP in a single atomic transaction:

```python
# Percentage-based triggers
order = gmx.create_order(
    "ETH/USDC:USDC",
    "market",
    "buy",
    0,
    None,
    {
        "size_usd": 1000,
        "leverage": 3.0,
        "collateral_symbol": "USDC",
        "stopLoss": {"triggerPercent": 0.05},   # 5% below entry
        "takeProfit": {"triggerPercent": 0.10}, # 10% above entry
    },
)

# Absolute price triggers
order = gmx.create_order(
    "ETH/USDC:USDC",
    "market",
    "buy",
    0,
    None,
    {
        "size_usd": 1000,
        "leverage": 3.0,
        "collateral_symbol": "USDC",
        "stopLossPrice": 1850.0,
        "takeProfitPrice": 2200.0,
    },
)
```

### Fetching market data

```python
# Load markets
gmx.load_markets()

# Get current ticker
ticker = gmx.fetch_ticker("ETH/USDC:USDC")
print(f"ETH price: ${ticker['last']:,.2f}")
print(f"24h change: {ticker['percentage']:.2f}%")

# Fetch OHLCV data
ohlcv = gmx.fetch_ohlcv("ETH/USDC:USDC", "1h", limit=100)
for candle in ohlcv[-5:]:
    timestamp, open_, high, low, close, volume = candle
    print(f"{timestamp}: O={open_} H={high} L={low} C={close}")

# Fetch funding rate
funding = gmx.fetch_funding_rate("ETH/USDC:USDC")
print(f"Funding rate: {funding['fundingRate']}")

# Fetch open interest
oi = gmx.fetch_open_interest("ETH/USDC:USDC")
print(f"Open interest: ${oi['openInterestValue']:,.0f}")
```

### Fetching account data

```python
# Get token balances
balance = gmx.fetch_balance()
print(f"USDC balance: {balance['USDC']['free']}")
print(f"ETH balance: {balance['ETH']['free']}")

# Get open positions
positions = gmx.fetch_positions()
for pos in positions:
    print(f"{pos['symbol']}: {pos['side']} {pos['contracts']} @ ${pos['entryPrice']}")
    print(f"  PnL: ${pos['unrealizedPnl']:.2f}")
    print(f"  Liquidation: ${pos['liquidationPrice']:.2f}")
```

## Unsupported CCXT methods

| Method | Reason |
|--------|--------|
| `fetch_order_book()` | GMX uses liquidity pools, not order books |
| `cancel_order()` | GMX orders execute immediately via keepers |
| `fetch_order()` | Orders are transient (limited support for status checking) |

## Limitations

**Note:** These are protocol-level limitations, not implementation gaps:

| Limitation | Description | Documentation |
|------------|-------------|---------------|
| No order book | GMX uses [liquidity pools](https://docs.gmx.io/docs/providing-liquidity), not order books | [Providing Liquidity](https://docs.gmx.io/docs/providing-liquidity) |
| No order cancellation | Executed orders cannot be cancelled (keeper-executed) | [API Contracts](https://docs.gmx.io/docs/api/contracts) |
| No volume data | OHLCV volume always 0 (data source limitation) | [Subsquid GraphQL](https://gmx.squids.live/) |
| OHLCV limit | Historical data limited to ~10,000 candles per request | - |
| Isolated margin only | Cross margin not supported by GMX | [Trading](https://docs.gmx.io/docs/trading) |
| Keeper execution | Orders execute via [keeper network](https://docs.gmx.io/docs/api/contracts), not instantly | [API Contracts](https://docs.gmx.io/docs/api/contracts) |
| 24h stats calculated | Ticker 24h high/low/open are calculated from OHLCV candles, not provided natively by GMX API (unlike centralised exchanges that track these in real-time) | - |

## Symbol format

GMX uses the CCXT unified symbol format for perpetual futures:

```
{BASE}/{QUOTE}:{SETTLE}
```

Examples:
- `ETH/USDC:USDC` - ETH perpetual settled in USDC
- `BTC/USDC:USDC` - BTC perpetual settled in USDC
- `SOL/USDC:USDC` - SOL perpetual settled in USDC

## See also

- [Main GMX README](../README.md) - Overview and Core Trading API
- [GMX Freqtrade Tutorial](https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade) - Complete trading bot example
- [CCXT Documentation](https://docs.ccxt.com/)
