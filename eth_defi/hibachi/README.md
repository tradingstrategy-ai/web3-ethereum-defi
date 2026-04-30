# Hibachi API

Reverse-engineered notes on the Hibachi public data API, used to fetch vault metadata and performance.

- Homepage: https://hibachi.xyz
- Vaults page: https://hibachi.xyz/vaults
- Exchange: stablecoin-native FX / crypto perpetuals exchange built on a custom L2

---

## Base URLs

| Base URL | Auth required | Purpose |
|---|---|---|
| `https://data-api.hibachi.xyz` | No | Public vault/market data |
| `https://api.hibachi.xyz` | Yes (JWT cookie) | User account, holdings |

All responses are JSON.

---

## Public endpoints

### `GET /vault/info`

Returns metadata for all vaults. No authentication required.

**Optional query parameters:**

| Param | Type | Description |
|---|---|---|
| `vaultId` | int | Filter to a single vault |

**Example:**

```
GET https://data-api.hibachi.xyz/vault/info
GET https://data-api.hibachi.xyz/vault/info?vaultId=3
```

**Response:** JSON array of vault objects.

```json
[
  {
    "vaultId": 2,
    "symbol": "GAV",
    "shortDescription": "Growi Alpha Vault",
    "description": "This core Hibachi vault, operated by Growi Finance, offers a systematic mean-reversion strategy on crypto perpetual futures...",
    "perSharePrice": "1.030186",
    "30dSharePrice": "0.999923",
    "outstandingShares": "1501004.254809",
    "managementFees": "0.00000000",
    "depositFees": "0.00000000",
    "withdrawalFees": "0.00000000",
    "performanceFees": "0.00000000",
    "marginingAssetId": 1,
    "vaultAssetId": 131073,
    "vaultPubKey": "92f2d3ac73037b5a635b1aef77452b2c847e6e8a...",
    "minUnlockHours": 0,
    "resolutionDecimals": 6,
    "maxDrawdown": "0",
    "sharpeRatio": "0"
  }
]
```

**Field notes:**

| Field | Type | Description |
|---|---|---|
| `vaultId` | int | Unique vault ID |
| `symbol` | str | Short ticker (e.g. `GAV`, `FLP`) |
| `shortDescription` | str | Display name |
| `description` | str | Long description |
| `perSharePrice` | str decimal | Current share price in USDT |
| `30dSharePrice` | str decimal | Share price 30 days ago |
| `outstandingShares` | str decimal | Total shares issued |
| `managementFees` | str decimal | Annual management fee rate (0 = none) |
| `depositFees` | str decimal | Deposit fee rate at vault level (0 = none; platform charges separately) |
| `withdrawalFees` | str decimal | Withdrawal fee rate at vault level |
| `performanceFees` | str decimal | Performance fee rate |
| `marginingAssetId` | int | Collateral asset ID; `1` = USDT |
| `vaultAssetId` | int | Asset ID of the vault share token |
| `vaultPubKey` | str | Vault's on-exchange public key |
| `minUnlockHours` | int | Minimum lockup period in hours (0 = no lockup) |
| `resolutionDecimals` | int | Decimal precision for shares |
| `maxDrawdown` | str decimal | Maximum drawdown (currently reported as `"0"`) |
| `sharpeRatio` | str decimal | Sharpe ratio (currently reported as `"0"`) |

---

### `GET /vault/performance`

Returns daily share price and TVL history for one vault.

**Required query parameters:**

| Param | Type | Description |
|---|---|---|
| `vaultId` | int | Vault ID (2 or 3 as of 2026-04-30) |
| `timeRange` | str | Must be `All` — other values (1d, 7d, 30d, 90d, 1y) return HTTP 400 |

**Example:**

```
GET https://data-api.hibachi.xyz/vault/performance?vaultId=3&timeRange=All
```

**Response:**

```json
{
  "vaultPerformanceIntervals": [
    {
      "interval": "1d",
      "timestamp": 1773226800,
      "perSharePrice": "1.000017",
      "totalValueLocked": "74925.281546"
    },
    ...
  ]
}
```

**Field notes:**

| Field | Type | Description |
|---|---|---|
| `interval` | str | Always `"1d"` (daily snapshots) |
| `timestamp` | int | Unix timestamp (seconds, UTC) |
| `perSharePrice` | str decimal | Share price in USDT at this snapshot |
| `totalValueLocked` | str decimal | TVL in USDT at this snapshot |

---

### `GET /market/inventory`

Returns exchange asset and market inventory. No vault-specific data.

**Example:**

```
GET https://data-api.hibachi.xyz/market/inventory
```

**Response keys:**

| Key | Description |
|---|---|
| `crossChainAssets` | Supported deposit/withdrawal tokens per chain (e.g. USDC on Base, Arbitrum) with exchange rates to USDT |
| `feeConfig` | Platform-level fee config (same as `exchange-info`) |
| `markets` | Available perpetual markets |
| `tradingTiers` | Volume-based trading tier definitions |

**`crossChainAssets` entry:**

```json
{
  "chain": "Base",
  "token": "USDC",
  "exchangeRateFromUSDT": "0.999200",
  "exchangeRateToUSDT": "0.999200",
  "instantWithdrawalLowerLimitInUSDT": "0.054",
  "instantWithdrawalUpperLimitInUSDT": "26035.07"
}
```

---

### `GET /market/exchange-info`

Returns global exchange configuration.

**Example:**

```
GET https://data-api.hibachi.xyz/market/exchange-info
```

**Response keys:**

| Key | Description |
|---|---|
| `feeConfig` | Platform fee configuration (see below) |
| `futureContracts` | List of perpetual futures (symbol, margin rates, min order size) |
| `instantWithdrawalLimit` | Global instant withdrawal limit |
| `maintenanceWindow` | Scheduled maintenance info |
| `maxSlippageLimit` | Max slippage allowed |
| `status` | Exchange status |

**`feeConfig` structure:**

```json
{
  "tradeMakerFeeRate": "0.00000000",
  "tradeTakerFeeRate": "0.00045000",
  "transferFeeRate": "0.00010000",
  "depositFees": "0.006777",
  "withdrawalFees": "0.018073",
  "instantWithdrawalFees": [
    [1000, 0.002],
    [100,  0.004],
    [50,   0.005],
    [20,   0.01],
    [5,    0.02]
  ],
  "instantWithdrawDstPublicKey": "a4fff986badd3..."
}
```

**Fee notes:**

- Maker fee: 0% (zero)
- Taker fee: 0.045%
- Transfer fee: 0.01%
- Deposit fee: ~0.68%
- Withdrawal fee: ~1.81%
- Instant withdrawal: tiered by amount in USDT (threshold = minimum amount for that rate), e.g. >$1000 → 0.2%, >$100 → 0.4%, >$50 → 0.5%, >$20 → 1%, >$5 → 2%

---

## Authenticated endpoints

These require a session cookie / JWT obtained via wallet signature through the web UI.

| Endpoint | Method | Description |
|---|---|---|
| `GET https://api.hibachi.xyz/auth/user` | GET | Current authenticated user info (401 if not logged in) |
| `GET https://api.hibachi.xyz/vault/holdings` | GET | User's vault share balances |
| `GET https://api.hibachi.xyz/vault/pending` | GET | Pending deposit/withdrawal requests |
| `POST https://api.hibachi.xyz/vault/deposit` | POST | Submit a deposit |
| `POST https://api.hibachi.xyz/vault/withdraw` | POST | Submit a withdrawal |

---

## Known vaults (as of 2026-04-30)

| vaultId | symbol | Name | Strategy |
|---|---|---|---|
| 2 | GAV | Growi Alpha Vault | Mean-reversion on crypto perps (long/short), operated by Growi Finance |
| 3 | FLP | Fire Liquidity Provider | Market making across all Hibachi markets, operated by Kappa Lab |

The platform denomination is USDT (`marginingAssetId=1`).

---

## Deriving TVL from `vault/info`

TVL per vault can be approximated as:

```
TVL = outstandingShares × perSharePrice
```

Both fields are string decimals denominated in USDT.

---

## Notes

- The `data-api.hibachi.xyz` domain serves all public read-only data with no rate limiting or API key observed.
- Hibachi is a Next.js app (v9.47.1 Sentry client, `/_next/` chunks).
- No WebSocket feed was observed for vault data; the UI polls REST endpoints.
- `maxDrawdown` and `sharpeRatio` are present in `/vault/info` but currently always `"0"` — not yet computed server-side.
