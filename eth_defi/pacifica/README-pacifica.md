# Pacifica API

Reverse-engineered notes on the Pacifica public REST and WebSocket APIs, focused
on the data we can extract for the native vault metrics pipeline (vault discovery,
share prices, TVL, PnL) and market data.

- Homepage: https://pacifica.fi
- App: https://app.pacifica.fi
- Docs: https://docs.pacifica.fi (redirects to https://pacifica.gitbook.io/docs)
- Python SDK: https://github.com/pacifica-fi/python-sdk
- Exchange: USDC-denominated perpetual + spot DEX built on **Solana**

Pacifica is a Solana-based perpetuals and spot exchange. As of June 2026 it lists
~70 perpetual markets (and a growing set of USDC-quoted spot markets) with leverage
of 3x–50x. All accounts, vaults and signatures use **Solana base58 addresses and
ed25519 keys** — not EVM addresses. The single settlement and collateral currency
is **USDC**.

## Shared vault-account metrics

Pacifica vault-account metrics are currently unsupported. Parser groundwork
maps `GET /account` equity and one signed notional for every non-zero
`GET /positions` result, valuing `amount` at the same-cycle
`GET /info/prices` mark and using `bid` as long and `ask` as short.

TODO: enable this only after the DuckDB database, native price exporter,
all-chain scheduling and mark/position timestamp-skew validation are
implemented. Until then Pacifica does not enter Parquet or JSON.
Cross-margin, portfolio-margin, isolated-margin, liquidation and order data are
intentionally excluded. The target shared raw-to-cleaned Parquet and JSON
contract is documented in
[`perp-dex-account-metrics.rst`](../../docs/source/vaults/perp-dex-account-metrics.rst).

All facts below were verified against the live mainnet API on 2026-06-26.

---

## Base URLs

| Base URL | Auth required | Purpose |
|---|---|---|
| `https://api.pacifica.fi/api/v1` | No (for reads) | Mainnet REST API |
| `https://test-api.pacifica.fi/api/v1` | No (for reads) | Testnet REST API |
| `wss://ws.pacifica.fi/ws` | No (for public channels) | Mainnet WebSocket |
| `wss://test-ws.pacifica.fi/ws` | No (for public channels) | Testnet WebSocket |

All REST responses share a uniform envelope:

```json
{ "success": true, "data": ..., "error": null, "code": null }
```

`data` is sometimes an array (e.g. `/info/prices`) and sometimes an object
(e.g. `/lake/list` → `{ "lakes": [...] }`). Numeric quantities are returned as
**decimal strings** to preserve precision; timestamps are **Unix milliseconds, UTC**.

### Authentication

All market-data and account-history **read** endpoints used by the metrics pipeline
are **public** — no API key, no signature, no cookie. We confirmed this by reading
arbitrary vault accounts (`/portfolio`, `/account`, `/positions`) anonymously.

Only **write/trading** operations (create/cancel order, deposit/withdraw, create
vault, leverage changes) require a signature. Pacifica uses ed25519 signatures from
the Solana wallet or a delegated **API Agent Key** ("Agent Wallet"). The metrics
pipeline never signs, so this is out of scope here. See
[signing docs](https://pacifica.gitbook.io/docs/api-documentation/api/signing).

---

## Vaults ("lakes")

Pacifica vaults are managed trading pools: depositors contribute USDC and a
designated **manager** trades the pooled capital as a single real margin account.
Internally the API calls a vault a **"lake"** (the REST path is `/lake/...`), while
the UI and narrative docs call it a "vault".

A vault holds **one account balance and one set of positions**, split across two
share classes against the same capital pool:

- **LP shares** — minted/burned when ordinary depositors deposit/withdraw.
- **Manager shares** — minted/burned when the manager deposits/withdraws.

Both classes take pro-rata profit and loss. On profit, the manager additionally
takes a **performance fee** on gains above the **high-water mark (HWM)** before the
remainder is split pro-rata — so manager NAV per share grows faster than LP NAV per
share. Losses are split pro-rata with no HWM adjustment. The HWM moves monotonically
up through trading but resets on deposits/withdrawals to avoid artificial triggering.

See [vault docs](https://pacifica.gitbook.io/docs/vaults/vaults) and
[profit and loss](https://pacifica.gitbook.io/docs/vaults/profit-and-loss).

### `GET /lake/list` — list all vaults

The single endpoint that enumerates every vault. No authentication required.

**Query parameters:**

| Param | Type | Description |
|---|---|---|
| `account` | str (optional) | Solana address; if given, adds the caller's share balance per vault |

**Example:**

```
GET https://api.pacifica.fi/api/v1/lake/list
```

**Response:** `data.lakes` is an array of vault objects.

```json
{
  "success": true,
  "data": {
    "lakes": [
      {
        "address": "5X8BEVZ8kQSNyRyMBNYWaBUCD3a4azTNn1vnYenML35f",
        "creator": "BazVyuxNetFxKAdKrh4bH7Dw47JPhqYsJmQ6T1jNevmm",
        "manager": "BazVyuxNetFxKAdKrh4bH7Dw47JPhqYsJmQ6T1jNevmm",
        "nickname": "Growi HF",
        "lp_shares": "242424.15707558310389348517622",
        "manager_shares": "10",
        "lp_balance": "242234.72805176015375011623420",
        "manager_balance": "15.869393097690901977179193704",
        "last_checked_equity": "242432.10022",
        "high_watermark": "...",
        "created_at": 1782283725359,
        "config": {
          "deposit_cap": "10000",
          "manager_profit_share": "0.2",
          "manager_loss_share": "0",
          "deposit_min_duration_ms": 86400000,
          "manager_min_balance_portion": "0.1"
        }
      }
    ]
  }
}
```

**Field notes:**

| Field | Type | Description |
|---|---|---|
| `address` | str | Vault's Solana account address (the vault's on-chain trading account) |
| `creator` | str | Address that created the vault |
| `manager` | str | Address that trades the vault (often == creator) |
| `nickname` | str | Display name |
| `lp_shares` | str decimal | Total LP shares outstanding |
| `manager_shares` | str decimal | Total manager shares outstanding |
| `lp_balance` | str decimal | USDC value of the LP share class (LP NAV) |
| `manager_balance` | str decimal | USDC value of the manager share class |
| `last_checked_equity` | str decimal | Last computed total vault equity (USDC) |
| `high_watermark` | str decimal | High-water mark for performance fees (USDC) |
| `created_at` | int | Vault creation time (ms) |
| `config` | object | Vault configuration (see below); absent on brand-new/empty vaults |

**`config` fields:**

| Field | Type | Description |
|---|---|---|
| `deposit_cap` | str decimal | Maximum total deposits (USDC); `"0"`/absent = uncapped |
| `manager_profit_share` | str decimal | **Performance fee** as a fraction (e.g. `"0.2"` = 20%) |
| `manager_loss_share` | str decimal | Fraction of losses the manager absorbs beyond pro-rata |
| `deposit_min_duration_ms` | int | LP **lockup** after deposit, in ms (e.g. `86400000` = 1 day) |
| `manager_min_balance_portion` | str decimal | Minimum manager skin-in-the-game as a fraction of the vault |
| `manager_liquidation_balance_portion` | str decimal | Manager balance fraction that triggers a forced wind-down |
| `withdraw_window_s` | int | Length of each withdrawal window (s) |
| `withdraw_duration_s` | int | Cooldown before a withdrawal becomes claimable (s) |

### Deriving vault metrics

| Metric | How |
|---|---|
| **LP share price (NAV)** | `lp_balance / lp_shares` (current snapshot) |
| **Manager share price** | `manager_balance / manager_shares` |
| **TVL** | `last_checked_equity` (≈ `lp_balance + manager_balance`) |
| **Performance fee** | `config.manager_profit_share` |
| **Lockup** | `config.deposit_min_duration_ms` |
| **Denomination** | always USDC |

> `/lake/list` is a **snapshot only** — it carries no time series. For historical
> share prices / TVL use `/portfolio` against the vault `address` (see below), which
> works because each vault is just a regular trading account.

The full list contains many tiny test vaults (`"Testing vault 0 fee"`, zero balance,
etc.), so the pipeline should filter by a minimum TVL the same way the Hyperliquid
pipeline does.

---

## Account / vault history endpoints

Because a vault is an ordinary account, the generic account endpoints accept a vault
`address` and are the source of historical and live state. All are public.

### `GET /portfolio` — account equity history (share price / TVL source)

This is the key endpoint for reconstructing **TVL and share-price history** of a
vault over time.

| Param | Type | Description |
|---|---|---|
| `account` | str (required) | Account / vault address |
| `time_range` | str (required) | One of `1d`, `7d`, `14d`, `30d`, `all` |
| `start_time` | int (optional) | Start (ms) |
| `end_time` | int (optional) | End (ms) |
| `limit` | int (optional) | Max records; **default 100** |

**Example:**

```
GET https://api.pacifica.fi/api/v1/portfolio?account=<vault>&time_range=all&limit=5000
```

**Response:**

```json
{
  "success": true,
  "data": [
    { "account_equity": "10",          "pnl": "0",         "timestamp": 1782212400000 },
    { "account_equity": "242432.10022", "pnl": "59.562428", "timestamp": 1782467820000 }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `account_equity` | str decimal | Account equity (balance + unrealised PnL) at the snapshot, USDC |
| `pnl` | str decimal | **Cumulative** PnL since account/vault creation, USDC |
| `timestamp` | int | Snapshot time (ms) |

**Frequency and history (verified):**

- **Native granularity ≈ 15 minutes** (observed 900 s gaps).
- **History goes back to vault creation** (`time_range=all`; first point is the
  initial deposit, e.g. `account_equity="10"`).
- The endpoint **downsamples to `limit`**: with the default `limit=100` a young
  vault's 285 native points were returned as ~100 points at 43-min gaps; with
  `limit=500` all 285 native points came back at the full 15-min resolution. To get
  native resolution always request a `limit` well above the expected point count.

**Share-price reconstruction (Hyperliquid-style):** `account_equity` is total NAV,
not per-share, and historical share counts are not exposed. Derive per-period flows
exactly as the Hyperliquid pipeline does:

```
pnl_update[i]     = pnl[i] - pnl[i-1]
netflow_update[i] = (account_equity[i] - account_equity[i-1]) - pnl_update[i]
```

then feed `(pnl_update, netflow_update)` into the shared mint/burn share-price logic.
The current `lp_balance / lp_shares` from `/lake/list` anchors the latest share price.

### `GET /account` — current account snapshot

| Param | Type | Description |
|---|---|---|
| `account` | str (required) | Account / vault address |

Returns a single object: `balance`, `account_equity`, `cross_account_equity`,
`available_to_withdraw`, `total_margin_used`, `cross_mmr`, `positions_count`,
`maker_fee`, `taker_fee`, `fee_level`, `spot_balances`, `updated_at`, etc. Useful for
the live TVL/equity of a vault and the account's trading fee tier.

### `GET /positions` — current open positions

| Param | Type | Description |
|---|---|---|
| `account` | str (required) | Account / vault address |

Array of positions: `symbol`, `side` (`bid`/`ask`), `amount`, `entry_price`,
`margin`, `funding`, `isolated`, `liquidation_price`, `created_at`, `updated_at`.
Lets us see what a vault is actually trading.

### Other account endpoints

These exist (see [llms index](https://pacifica.gitbook.io/docs/llms.txt)) and accept
an `account` param; read access is public:

| Endpoint | Returns |
|---|---|
| `GET /account/balance/history` | Balance history (deposits/withdrawals) |
| `GET /trades` (trade history) | Per-account fills |
| `GET /funding/history` (account funding) | Per-account funding payments |
| `GET /positions/history` | Closed positions |

---

## Market-data endpoints

All public. These cover the exchange itself rather than vaults, but are useful for
funding/price context and for the symbol universe.

### `GET /info` — market info (symbol universe)

No parameters. Returns one object per market (70 perp markets observed):

| Field | Description |
|---|---|
| `symbol` | Market symbol (e.g. `BTC`) |
| `tick_size`, `min_tick`, `max_tick` | Price tick configuration |
| `lot_size` | Size increment |
| `max_leverage` | Max leverage (e.g. 50) |
| `isolated_only` | Whether only isolated margin is allowed |
| `min_order_size`, `max_order_size` | Order size bounds (USD) |
| `funding_rate`, `next_funding_rate` | Current / next funding rate |
| `created_at` | Market listing time (ms) |
| `instrument_type` | e.g. `perpetual` |
| `base_asset` | Base asset symbol |

### `GET /info/prices` — live prices for all markets

No parameters. Array, one entry per market:

| Field | Description |
|---|---|
| `symbol` | Market symbol |
| `mark` | Mark price |
| `oracle` | Oracle price |
| `mid` | Mid of best bid/ask |
| `funding` | Funding rate of the past funding epoch (hourly) |
| `next_funding` | Estimated next-epoch funding rate |
| `open_interest` | Open interest (USD) |
| `volume_24h` | 24h volume (USD) |
| `yesterday_price` | Oracle price 24h ago |
| `timestamp` | Snapshot time (ms) |

### `GET /kline` — OHLCV candles

| Param | Type | Description |
|---|---|---|
| `symbol` | str (required) | Market symbol (e.g. `BTC`) |
| `interval` | str (required) | `1m`,`3m`,`5m`,`15m`,`30m`,`1h`,`2h`,`4h`,`8h`,`12h`,`1d` |
| `start_time` | int (required) | Start (ms) |
| `end_time` | int (optional) | End (ms); defaults to now |

Candle objects use short keys: `t` (start ms), `T` (end ms), `s` (symbol),
`i` (interval), `o`/`h`/`l`/`c` (OHLC, decimal strings), `v` (volume), `n` (trade
count). A sibling endpoint returns **mark-price** candles (see the
[mark-price candle docs](https://pacifica.gitbook.io/docs/api-documentation/api/rest-api/markets/get-mark-price-candle-data)).

### `GET /funding_rate/history` — historical funding

| Param | Type | Description |
|---|---|---|
| `symbol` | str (required) | Market symbol |
| `limit` | int (optional) | Records per page; default 100, **max 4000** |
| `cursor` | str (optional) | Pagination cursor |

Response: `data` array of `{ oracle_price, bid_impact_price, ask_impact_price,
funding_rate, next_funding_rate, created_at }`, plus top-level `next_cursor` and
`has_more` for cursor pagination. Funding settles **hourly**. History is effectively
unbounded via cursor paging.

### Other market endpoints

`GET /book` (orderbook), `GET /trades` (recent market trades), `GET /fee`
(fee levels), `GET /loan_pool` (lending/loan pool). See the
[markets docs](https://pacifica.gitbook.io/docs/api-documentation/api/rest-api/markets).

---

## WebSocket

`wss://ws.pacifica.fi/ws`. Subscribe by sending:

```json
{ "method": "subscribe", "params": { "source": "<channel>", "symbol": "BTC" } }
```

Public channels include `prices`, `book` (orderbook), `bbo` (best bid/offer),
`trades`, `candle`, `mark_price_candle`. Account channels (`account_margin`,
`account_leverage`, `account_info`, `account_positions`, `account_order_updates`,
`account_trades`, `account_transfers`) stream live state for a given account/vault.
For the batch metrics pipeline the REST endpoints above are sufficient; WS is only
needed for low-latency / live monitoring.

---

## Fees

| Fee | Value | Notes |
|---|---|---|
| Vault performance fee | per vault, `config.manager_profit_share` (e.g. 20%) | On profit above HWM; **net of share price** (deducted before LP split) |
| Vault manager loss share | per vault, `config.manager_loss_share` | Extra loss the manager absorbs |
| Trading maker fee | ~0.015% (tier 0; `maker_fee` from `/account`) | Volume-tiered |
| Trading taker fee | ~0.04% (tier 0; `taker_fee` from `/account`) | Volume-tiered |
| Vault creation fee | flat USDC fee | Charged on `create_vault` |

Because the performance fee is taken inside the vault before the LP balance is
struck, the **share price derived from `lp_balance / lp_shares` is already net** of
performance fees — analogous to Hyperliquid's `internalised_skimming` mode.

---

## Rate limits

Credit-based, per IP / per API config key
([rate-limit docs](https://pacifica.gitbook.io/docs/api-documentation/api/rate-limits)):

| Caller | Budget |
|---|---|
| Unidentified IP | 125 credits / 60 s |
| Valid API config key | 300 credits / 60 s |
| Fee tier 1–5 | 300 – 6,000 credits / 60 s |
| VIP 1–3 | 20,000 – 40,000 credits / 60 s |

- Standard request = 1 credit; order cancel = 0.5; heavy GETs = 1–12 credits.
- Exhaustion returns **HTTP 429**.
- Every REST response carries quota headers: `r` (remaining), `t` (seconds to
  refresh), `q` (total quota) — **all multiplied by 10** (e.g. `r=1200` → 120.0).
- WebSocket: max 300 connections per IP, max 20 subscriptions per channel per
  connection; quota echoed in an `rl` field.

For bulk scanning of many vaults, throttle to stay within the unidentified-IP budget
or rotate proxies (as the Hyperliquid HF pipeline does).

---

## Summary: what we can extract

| Data | Endpoint | Auth | Frequency / history |
|---|---|---|---|
| Vault list, shares, balances, config, fees | `GET /lake/list` | No | Snapshot only |
| Vault/account equity + cumulative PnL history | `GET /portfolio` | No | ~15 min native, back to creation |
| Live vault equity / TVL / fees | `GET /account` | No | Snapshot |
| Vault open positions | `GET /positions` | No | Snapshot |
| Market universe & specs | `GET /info` | No | Snapshot |
| Live prices, funding, OI, volume | `GET /info/prices` | No | Snapshot (ms) |
| OHLCV candles | `GET /kline` | No | 1m–1d, bounded by `start_time` |
| Historical funding rates | `GET /funding_rate/history` | No | Hourly, paginated to 4000/req |
| Live streams | `wss://ws.pacifica.fi/ws` | No (public channels) | Real-time |

**Denomination:** USDC. **Chain:** Solana (base58 addresses, not EVM). When wiring
Pacifica vaults into the unified ERC-4626 metrics pipeline, allocate a synthetic
chain ID (as Hyperliquid `9999`, Hibachi `9997`, GRVT `325` do) since there is no
EVM JSON-RPC chain ID for Pacifica/Solana.

---

## Notes

- The REST envelope is uniform; always check `success` and read `data`.
- Numbers are decimal strings — parse with `Decimal`, never float, to keep precision
  (some share/balance values carry 20+ significant digits).
- Timestamps are Unix **milliseconds**, UTC.
- "lake" (API path) == "vault" (UI/docs) — same object.
- The vault list is dominated by tiny test vaults; filter by minimum TVL before
  computing metrics.
- `/portfolio` silently downsamples to `limit`; request a high `limit` for native
  15-minute resolution.
