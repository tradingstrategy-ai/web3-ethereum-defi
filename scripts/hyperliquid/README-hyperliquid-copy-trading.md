# Hyperliquid copy trading and HFT account identification

Research into copy trading platforms, APIs, and methods for identifying profitable
high-frequency trading accounts on Hyperliquid.

## Copy trading platforms and leaderboards

| Platform | URL | Key features |
|----------|-----|-------------|
| **HyperCopy** | `hypercopy.io` | Dedicated HL copy trading. Filters: High WR >75%, Scalpers, Whales, Holders. Leaderboards: Top Earners, Most Copied, Trending. P/L across 1D/1W/1M/ALL timeframes. |
| **Copin.io** | `app.copin.io/hyperliquid` | 2M+ trader profiles. 26-criteria scoring system covering PnL, ROI, win rate, avg volume, trade duration, max drawdown, leverage, profit factor, W/L ratio. Percentile-based ranking. [Methodology docs](https://docs.copin.io/welcome/methodology). |
| **HypurrScan** | `hypurrscan.io` | Explorer/analytics with `/leaderboard` and per-address pages (`/address/{addr}`). Beta, fully client-rendered. |
| **ASXN Hyperscreener** | `hyperscreener.asxn.xyz` | Dashboard for screening Hyperliquid traders. |
| **PvP Trade** | `pvp.trade` / `t.me/pvptrade_bot` | Telegram bot for social trading. Commands: `/long`, `/short`, `/track` (wallet monitoring), `/leaderboards`, `/positions`. Min 10 USDC deposit. Supports HL, Arbitrum, Solana. |
| **Tealstreet** | `tealstreet.io` | Multi-exchange professional trading terminal with Hyperliquid support. Not primarily copy trading. |
| **Hyperliquid native** | `app.hyperliquid.xyz` | Built-in vault copy trading. Leaders manage pooled capital, depositors share PnL. 10% performance fee on user vaults. 5% minimum leader stake. |

### Copin.io scoring criteria (26 metrics)

Copin uses percentile-based ranking across all 2M+ tracked traders. Key metrics include:

- **PnL** — absolute profit/loss
- **ROI** — return on investment
- **Win rate** — proportion of profitable trades
- **Avg volume** — average trade size
- **Trade duration** — average hold time
- **Max drawdown** — worst peak-to-trough decline
- **Leverage** — average leverage used
- **Profit factor** — gross profit / gross loss
- **W/L ratio** — average win size / average loss size
- **Trade frequency** — trades per time period

## Hyperliquid API endpoints for trader analysis

All via `POST https://api.hyperliquid.xyz/info`:

| Endpoint | Returns | Use for HFT detection |
|----------|---------|----------------------|
| `userFillsByTime` | Paginated fills (max 2000/req, 10K total) | Trade frequency, avg hold time, PnL per trade |
| `clearinghouseState` | Positions, leverage, margin, account value | Current exposure, leverage patterns |
| `portfolio` | Account value + PnL history (day/week/month/allTime) | Equity curve, drawdown, Sharpe ratio |
| `historicalOrders` | Up to 2000 recent orders with status | Cancel-to-fill ratio (high = MM/HFT) |
| `userFees` | Daily volume, fee tier | Volume levels (higher tier = more volume) |
| `userRateLimit` | Cumulative volume, request usage | Total lifetime volume |
| `userFunding` | Funding payment history (max 500/req) | Funding PnL component |
| `userTwapSliceFills` | Algorithmic order executions | Detect algorithmic execution patterns |
| `orderStatus` | Individual order details | Order lifecycle analysis |

### WebSocket streams

Via `wss://api.hyperliquid.xyz/ws`:

| Channel | Subscription | Use |
|---------|-------------|-----|
| `trades` | Per coin | Every execution with user addresses — **best for address discovery** |
| `userFills` | Per user address | Real-time fills with PnL |
| `orderUpdates` | Per user address | Order lifecycle (placed → filled/cancelled) |
| `userEvents` | Per user address | Consolidated: fills + funding + liquidations |
| `clearinghouseState` | Per user address | Live positions and margin |

The `trades` channel is particularly valuable: it broadcasts every trade on a given coin
with the trader's address, making it the primary method for discovering active HFT addresses.

## Address discovery methods

Hyperliquid has **no public leaderboard API** and **no address enumeration endpoint**.
The leaderboard at `app.hyperliquid.xyz/leaderboard` uses internal endpoints not in the public API.

### Method 1: WebSocket trades monitoring (recommended)

Connect to the `trades` WebSocket for high-volume coins (ETH, BTC, SOL, etc.),
accumulate address → fill count over rolling time windows, and identify addresses
with abnormally high fill rates.

```python
# Example subscription message
{"method": "subscribe", "subscription": {"type": "trades", "coin": "ETH"}}
```

Each trade message includes the user's address, allowing real-time address discovery.

### Method 2: Vault leaders from stats-data endpoint

The undocumented `stats-data` endpoint returns ~8000+ vault entries with leader addresses:

```
GET https://stats-data.hyperliquid.xyz/Mainnet/vaults
```

This is already integrated in the codebase via `fetch_all_vaults()` in
`eth_defi/hyperliquid/vault.py`.

### Method 3: Third-party platform scraping

- **Copin.io** has the largest database (2M+ profiles) — check for API access
- **HyperCopy** categorises traders (Scalpers, Whales, etc.) — useful for pre-filtered lists
- **HypurrScan** has per-address pages

### Method 4: Leaderboard UI scraping

`app.hyperliquid.xyz/leaderboard` requires browser automation (Playwright/Selenium)
as it's client-rendered with no public API backing it.

## HFT identification metrics

Once addresses are discovered, compute these metrics from API data:

### Trading behaviour signals

| Metric | HFT threshold | Data source | Calculation |
|--------|--------------|-------------|-------------|
| Trades per hour | >10 sustained | `userFillsByTime` | Count fills / time window |
| Avg hold time | <5 minutes | `userFillsByTime` | Time between open and close fills for same coin |
| Cancel-to-fill ratio | >5:1 | `historicalOrders` | Cancelled orders / filled orders |
| Daily volume | >$1M/day | `userFillsByTime` or `userFees` | Sum of fill sizes × prices |
| Fee tier | VIP tier | `userFees` | Higher tier = more volume |
| TWAP usage | Present | `userTwapSliceFills` | Non-empty = algorithmic execution |
| Position flipping | Frequent | `clearinghouseState` over time | Rapid long→short→long on same coin |

### Profitability signals

| Metric | Target | Data source | Calculation |
|--------|--------|-------------|-------------|
| Cumulative PnL | >0 over 30+ days | `portfolio` (allTime) | Direct from API |
| Win rate | >60% | `userFillsByTime` | Profitable round-trips / total round-trips |
| Profit factor | >1.5 | `userFillsByTime` | Gross profit / gross loss |
| Sharpe ratio | >2 | `portfolio` | Daily returns mean / std |
| Max drawdown | <15% | `portfolio` | Worst peak-to-trough from equity curve |
| PnL per trade | Small but consistent | `userFillsByTime` | Avg `closedPnl` per fill — HFT makes many small wins |
| Daily return consistency | Low variance | `portfolio` | Std dev of daily returns — lower = more systematic |

### Composite HFT score

A practical scoring approach:

1. **Frequency score** (0-100): normalise trades/hour against population
2. **Speed score** (0-100): inverse of avg hold time
3. **Volume score** (0-100): normalise daily volume
4. **Profitability score** (0-100): weighted combination of win rate, Sharpe, profit factor
5. **Consistency score** (0-100): inverse of daily return variance

**HFT score** = weighted average of above components. Filter for score > threshold.

## Existing codebase capabilities

The repository already has substantial Hyperliquid infrastructure that can be reused:

| Module | Capability |
|--------|-----------|
| `eth_defi/hyperliquid/trade_history.py` | `fetch_account_trade_history()` — returns `RoundTripTrade` objects with VWAP prices, realised/net PnL, hold times |
| `eth_defi/hyperliquid/trade_history_db.py` | `HyperliquidTradeHistoryDatabase` — DuckDB persistence with incremental sync for fills, funding, ledger events. Handles the 10K fill cap via accumulation over time. |
| `eth_defi/hyperliquid/position.py` | Position reconstruction from fills into `PositionEvent` objects (open/close/increase/decrease) |
| `eth_defi/hyperliquid/api.py` | API client with rate limiting, proxy rotation, retry logic |
| `eth_defi/hyperliquid/session.py` | `HyperliquidSession` — rate-limited HTTP session (1200 weight/min, ~1 req/sec). Proxy rotation with failure tracking. Worker cloning for parallel requests. |
| `eth_defi/hyperliquid/vault.py` | `fetch_all_vaults()` — ~8000 vault addresses from stats-data endpoint |
| `scripts/hyperliquid/sync-trade-history.py` | Incremental trade history sync for whitelisted accounts |
| `scripts/hyperliquid/vault-trade-history.py` | Display trade history + share prices for a single account |

## Key SDKs

- **Python**: [hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) (1,459 stars) — `Info` class with `user_state()`, fill history, etc.
- **Rust**: [hyperliquid-rust-sdk](https://github.com/hyperliquid-dex/hyperliquid-rust-sdk) (430 stars)
- **CCXT**: Hyperliquid supported via CCXT integration (also integrated in this codebase at `eth_defi/gmx/ccxt/`)

## Key limitations

- **10K fill history cap** — `userFillsByTime` only returns the 10K most recent fills per address.
  The existing `HyperliquidTradeHistoryDatabase` works around this with incremental sync over time
  (accumulating fills across multiple sync runs).
- **No address discovery API** — must use WebSocket monitoring, scraping, or third-party platforms.
  There is no endpoint to enumerate or search for addresses.
- **No public leaderboard API** — the leaderboard UI uses internal/undocumented endpoints.
- **Rate limits** — 1200 weight/minute per IP (~1 req/sec for most info endpoints at 20 weight each).
  Scanning thousands of addresses requires proxy rotation (already supported in `HyperliquidSession`).
- **S3 archive partially private** — the public `s3://hyperliquid-archive/` bucket (eu-west-1) has
  `asset_ctxs/` and `market_data/` but `account_values/` is in a private bucket used internally
  by Hyperliquid for `stats.hyperliquid.xyz`.
- **`vaultSummaries` endpoint broken** — documented but returns empty array. Use `stats-data` instead.

## Related documentation

- [README-hyperliquid-vaults.md](README-hyperliquid-vaults.md) — daily metrics pipeline, share price computation
- [README-hyperliquid-trade-events.md](README-hyperliquid-trade-events.md) — trade history DuckDB schema, sync strategy
- [README-hyperliquid-backfill.md](README-hyperliquid-backfill.md) — S3 archive data source
- [README-hyperliquid-alternative-data-sources.md](README-hyperliquid-alternative-data-sources.md) — data source options, API limitations
