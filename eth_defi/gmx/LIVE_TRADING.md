# Live trading with GMX and FreqTrade

Live trading is fully supported on GMX decentralised perpetual futures using the CCXT adapter and FreqTrade framework.

## What makes this possible

The GMX CCXT adapter provides:

- **No API keys** - Direct Web3 wallet authentication with GMX smart contracts
- **Full CCXT compatibility** - `fetch_markets`, `fetch_ticker`, `create_order`, and 20+ methods
- **All order types** - Market, limit, stop-loss, take-profit, bundled SL/TP
- **USD-based sizing** - Use `size_usd` parameter for intuitive position sizing
- **Configurable leverage** - 1.0x to 100x per market
- **FreqTrade integration** - Seamless monkeypatch for strategy backtesting and live trading

## Pre-flight checklist

### Wallet setup

- [ ] Dedicated trading wallet created (never use your main wallet)
- [ ] Wallet funded with USDC on Arbitrum
- [ ] Sufficient ETH on Arbitrum for gas fees (minimum 0.01 ETH recommended)
- [ ] Private key securely stored in secrets file
- [ ] Wallet address verified correct in configuration

### Environment setup

- [ ] Python 3.11+ installed
- [ ] Repository cloned with submodules: `git clone --recurse-submodules https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade.git`
- [ ] Virtual environment created and activated: `python -m venv .venv && source .venv/bin/activate`
- [ ] FreqTrade installed from stable branch:
  ```bash
  git clone --branch stable https://github.com/freqtrade/freqtrade.git freqtrade-develop
  pip install -r freqtrade-develop/requirements.txt
  pip install -e freqtrade-develop/
  ```
- [ ] web3-ethereum-defi installed with extras: `pip install -e "deps/web3-ethereum-defi[web3v7,data,ccxt]"`
- [ ] `freqtrade-gmx` wrapper script tested: `./freqtrade-gmx --version`

**Full setup guide:** https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade#install-freqtrade

### Configuration setup

- [ ] RPC URL configured (Arbitrum mainnet)
- [ ] Secrets file created and NOT committed to git
- [ ] `dry_run: false` set for live trading
- [ ] `stake_amount` appropriate for risk tolerance
- [ ] Pair whitelist reviewed and verified
- [ ] Telegram notifications configured (optional but recommended)

### Strategy validation

- [ ] Backtest completed with acceptable results
- [ ] Dry-run mode tested for at least 24 hours
- [ ] Risk parameters reviewed (stoploss, leverage)
- [ ] Entry/exit logic understood

## Available strategies

### Production strategies

#### IchiV2_LS_Live

Dual long/short Ichimoku strategy for production trading.

**Configuration:** `configs/ichiv2_ls_gmx_static.json`

#### ADXMomentum

Trend-following long-only strategy.

#### Simple

Basic RSI strategy for learning and testing.

### Testing/validation strategies

**Warning:** The following strategies are designed for stress testing and validation only. They will lose money if used for trading.

| Strategy | Purpose |
|----------|---------|
| Pingpong | Rapid long entry/exit every minute |
| PingpongShort | Rapid short entry/exit |
| PingpongLS | Long and short positions simultaneously |
| PingpongSL | Stop-loss testing (0.1% stop) |
| PingpongShortSL | Short stop-loss testing |
| PingpongLimit | Limit order lifecycle testing |

Use these only for validating exchange connectivity and order execution.

## Configuration examples

### Production configuration

**Main config (`configs/ichiv2_ls_gmx_static.json`):**

```json
{
    "trading_mode": "futures",
    "margin_mode": "isolated", // GMX doesn't support cross-margins yet
    "max_open_trades": 10, // No. of trades that can be opened at a time
    "stake_currency": "USDC", // GMX supports USDC & major non-synthetics tokens like ETH, BTC etc.
    "stake_amount": 20, // Min. stake amount on GMX is $2
    "dry_run": false,
    "timeframe": "1h", // 1m timeframe is not reccomended as the trading happens on-chain so the signal will be expired before the trade confirms
    "exchange": {
        "name": "gmx",
        "ccxt_config": {
            "enableRateLimit": true,
            "rateLimit": 500,
            "executionBuffer": 3 // Extra buffer so that GMX multicall passes through
        },
        "pair_whitelist": [ 
            "BTC/USDC:USDC", // Basic perp trading pair configuration
            "ETH/USDC:USDC",
            "SOL/USDC:USDC"
        ]
    },
    "order_types": {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": false
    }
}
```

**Secrets config (`configs/ichiv2_ls_gmx_static.secrets.json`):**

```json
{
    "exchange": {
        "ccxt_config": {
            "rpcUrl": "https://your-arbitrum-rpc-endpoint",
            "privateKey": "0x...",
			"address": "0xdead"
        }
    },
	"api_server": {
        "jwt_secret_key": "super-secret-key-12345",
        "username": "secretAdminuser",
        "password": "adminpass123"
    },
    "telegram": {
        "token": "YOUR_BOT_TOKEN",
        "chat_id": "YOUR_CHAT_ID"
    }
}
```

**Important:** Never commit the secrets file to version control.

### Key configuration parameters

| Parameter | Description | Vlues we found optimal while testing |
|-----------|-------------|-------------|
| `dry_run` | Set to `false` for live trading | Start with `true` |
| `stake_amount` | Position size per trade in USDC | Start small (2-30) |
| `max_open_trades` | Maximum concurrent positions | 5-10 |
| `executionBuffer` | Gas buffer for keeper execution | 2.5-3.0 |
| `stoploss_on_exchange` | GMX uses custom stoploss handling | `false` |

### Generating new configurations

Use the configuration generator script to create new configs with a fresh wallet:

```bash
cd /path/to/gmx-ccxt-freqtrade

# Generate config for a new strategy
python scripts/generate_config.py my_strategy_gmx

# Creates:
# - configs/my_strategy_gmx.json (main config)
# - configs/my_strategy_gmx.secrets.json (wallet with new private key & secrects for the freqtrade web interface)
```

This automatically generates a new Ethereum wallet for each configuration, ensuring wallet isolation between strategies.

## Going live step-by-step

### Step 1: Validate environment

```bash
# Create a virtual environment
python -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Verify GMX is registered
./freqtrade-gmx --version

# Check GMX markets are accessible
python -c "
from eth_defi.gmx.ccxt import GMX
gmx = GMX({'rpcUrl': 'https://arb1.arbitrum.io/rpc'})
gmx.load_markets()
print(f'Markets loaded: {len(gmx.markets)}')
"
```

### Step 2: Dry-run validation (24h minimum recommended)

```bash
# Run in dry-run mode first
./freqtrade-gmx trade \
  --config configs/ichiv2_ls_gmx_static.json \
  --config configs/ichiv2_ls_gmx_static.secrets.json \
  --strategy IchiV2_LS_Live \
  --dry-run

# Monitor logs for errors or unexpected behaviour
# Run for at least 24 hours before going live
```

### Step 3: Start live trading

```bash
# IMPORTANT: Verify dry_run: false in config before proceeding

# Start live trading
./freqtrade-gmx trade \
  --config configs/ichiv2_ls_gmx_static.json \
  --config configs/ichiv2_ls_gmx_static.secrets.json \
  --strategy IchiV2_LS_Live

# With logging to file
./freqtrade-gmx trade \
  --config configs/ichiv2_ls_gmx_static.json \
  --config configs/ichiv2_ls_gmx_static.secrets.json \
  --strategy IchiV2_LS_Live \
  --logfile user_data/logs/live_trading.log
```

### Step 4: Monitor via Telegram or API

```bash
# If Telegram is configured, use these commands:
# /status - Current open positions
# /balance - Wallet balance
# /profit - P&L summary
# /daily - Daily profit

# Or use the REST API
curl http://localhost:8080/api/v1/status
```

## Database management

FreqTrade automatically separates dry-run and live trading databases to prevent mixing simulated and real trades:

| Mode | Database file |
|------|---------------|
| Dry-run | `tradesv3.dryrun.sqlite` |
| Live | `tradesv3.sqlite` |

### Explicit database control

```bash
# Specify database explicitly for live trading
./freqtrade-gmx trade \
  --config configs/ichiv2_ls_gmx_static.json \
  --config configs/ichiv2_ls_gmx_static.secrets.json \
  --strategy IchiV2_LS_Live \
  --db-url sqlite:///user_data/tradesv3.live.sqlite
```

**Important:** Always use separate databases when running multiple strategies to avoid conflicts.

## Monitoring and risk management

### Real-time monitoring

#### Telegram bot commands

| Command | Description |
|---------|-------------|
| `/status` | Current open positions |
| `/balance` | Wallet balance |
| `/profit` | P&L summary |
| `/daily` | Daily profit |
| `/performance` | Strategy performance |
| `/stopentry` |  Pause the bot from trading further 

**N.B.**  Always use the `/stopentry` command from telegram or equivalent option using the API. Without doing this and directly stopping the bot from execution will result it database conflict. Which can only be resolved by getting rid of the old data which is not ideal for production.


## How to handle open orders when freqtrade crashes
If the bot crashed for some reason and there are trades that are still open, if you try to run the bot again freqtrade will be stuck in an infinite loop trying to handle that positions. Use the scripts/gmx/gmx_close_all_positions.py script to close all of the positions so that the bot can be started freash again. Run it by passing the configuration file that was used for opening the trades so that the same account(wallet) can be used to close the positions.

```bash
python3 scripts/close_positions.py configs/super.secrets.json
```

#### Log monitoring

```bash
# Tail live logs
tail -f user_data/logs/live_trading.log

# Filter for trades only
tail -f user_data/logs/live_trading.log | grep -E "(ENTRY|EXIT|SIGNAL)"

# using docker
docker compose logs -f <container_name> --tail=200
```

#### Position monitoring script

```bash
# Check all open positions directly via GMX
poetry run python scripts/gmx/gmx_get_open_positions.py
```

### Risk management settings

| Parameter | Conservative | Moderate | Aggressive |
|-----------|--------------|----------|------------|
| `stake_amount` | 2-20 USDC | 20-50 USDC | 50-100 USDC |
| `max_open_trades` | 3 | 5-7 | 10+ |
| `stoploss` | -5% | -10% | -15% |
| `leverage` | 1-2x | 2-5x | 5-10x |

**N.B.**:  These are just optiomal configurations. This is not a trading advice.

### Things To Consider While Testing

- [ ] Check wallet balance (ETH for gas, USDC for trading)
- [ ] Review open positions
- [ ] Check for any error logs
- [ ] Verify RPC endpoint health
- [ ] Review daily P&L

## Docker deployment

For production deployments, use Docker Compose to run the bot as a service. We have a fully functional `docker-compose.yml` file added which can be extended further according to the requirements. Here is an example snippet:

```yaml
# docker-compose.yml
services:
  freqtrade-gmx:
    image: freqtradeorg/freqtrade:stable
    container_name: gmx-live-bot
    restart: unless-stopped
    volumes:
      - ./user_data:/freqtrade/user_data
      - ./configs:/freqtrade/configs
      - ./deps/web3-ethereum-defi:/freqtrade/web3-ethereum-defi
    environment:
      - PYTHONPATH=/freqtrade/web3-ethereum-defi
    command: >
      trade
      --config /freqtrade/configs/ichiv2_ls_gmx_static.json
      --config /freqtrade/configs/ichiv2_ls_gmx_static.secrets.json
      --strategy IchiV2_LS_Live
      --logfile /freqtrade/user_data/logs/live.log
```

```bash
# Start the bot
docker compose up -d

# View logs
docker compose logs -f freqtrade-gmx

# Stop the bot
docker compose down
```

## Running multiple strategies

Run multiple strategies simultaneously using separate wallets and databases:

```bash
# Terminal 1: Run IchiV2_LS_Live
./freqtrade-gmx trade \
  --config configs/ichiv2_ls_gmx_static.json \
  --config configs/ichiv2_ls_gmx_static.secrets.json \
  --strategy IchiV2_LS_Live \
  --db-url sqlite:///user_data/tradesv3_ichiv2.sqlite

# Terminal 2: Run ADXMomentum (use different wallet!)
./freqtrade-gmx trade \
  --config configs/adxmomentum_gmx.json \
  --config configs/adxmomentum_gmx.secrets.json \
  --strategy ADXMomentum \
  --db-url sqlite:///user_data/tradesv3_adx.sqlite
```

**Critical:** Each strategy instance must use:
- A separate database file
- Optionally, a separate API port if using REST API
- Optionally, A separate wallet (different private key) but recommended.

## Emergency procedures

### Immediate position closure

#### Close all positions

```bash
# Use the emergency close script
poetry run python scripts/gmx/gmx_close_all_positions.py

# Or specify a custom slippage (3%)
export JSON_RPC_ARBITRUM="your-rpc-url"
export PRIVATE_KEY="0x..."
poetry run python scripts/gmx/gmx_close_all_positions.py
```

#### Stop the bot

```bash
# Send SIGTERM (graceful shutdown)
pkill -f "freqtrade-gmx"

# Or use Ctrl+C if running in foreground

# Force stop if unresponsive
pkill -9 -f "freqtrade-gmx"
```

#### Stop new entries only

```bash
# Use Telegram command
/stopentry

# Or via API
curl -X POST http://localhost:8080/api/v1/stopentry
```

### Common issues and fixes

| Issue | Symptom | Resolution |
|-------|---------|------------|
| RPC timeout | Orders not executing | Switch to backup RPC URL |
| Insufficient gas | Transaction reverted | Add more ETH to wallet |
| Keeper delay | Orders pending > 2 min | wait(Hardly ever happens. most likely order failed) |
| Execution fee error | Order creation fails | Increase `executionBuffer` |
| Market not found | Symbol error | Run `load_markets()` first |

### Recovery procedures

1. **Bot crash** - Check logs, restart with `./freqtrade-gmx trade`
2. **Stuck position** - Use emergency close script or close via GMX UI
3. **RPC issues** - Switch to backup RPC in secrets config
4. **Wallet drained** - Stop bot immediately, investigate transactions

## See also

- [GMX CCXT adapter documentation](README.md) - Full API reference
- [CCXT method reference](ccxt/README.md) - CCXT-specific methods
- [gmx-ccxt-freqtrade repository](https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade) - Complete trading bot example
- [GMX Protocol Documentation](https://docs.gmx.io/)
- [FreqTrade Documentation](https://www.freqtrade.io/)

## Example scripts

| Script | Purpose |
|--------|---------|
| [`gmx_close_all_positions.py`](../../scripts/gmx/gmx_close_all_positions.py) | Emergency close all positions |
| [`gmx_get_open_positions.py`](../../scripts/gmx/gmx_get_open_positions.py) | Check current positions |
| [`gmx_ccxt_trading.py`](../../scripts/gmx/gmx_ccxt_trading.py) | Balance and trade history |