# Research: Perp DEX vaults and CEX vault-like features with community/user vault creation

Date: 2026-03-11

## Currently supported native vault protocols

| Protocol | Chain | Permissionless vaults | Status |
|----------|-------|----------------------|--------|
| Hyperliquid | Hypercore (9999) | Yes — anyone can create, 5 max per trader | Full pipeline |
| GRVT | Grvt (325) | No — protocol-approved only (~14 vaults) | Full pipeline |
| Lighter | Lighter (9998) | No — protocol pools only (~200-300) | Full pipeline |
| GMX | Arbitrum | No — GLP is protocol-managed | CCXT adapter |
| Orderly | Multi-chain | Unclear | Partial module |
| Gains Network | Multi-chain | No — gToken vaults are protocol LP | Via ERC-4626 scanner |

---

## Perp DEXes with permissionless user vault creation (NOT supported)

These protocols allow any user/community member to create and manage a vault, similar to Hyperliquid User Vaults.

### Confirmed permissionless vault creation — verified numbers

| Protocol | Chain | Total TVL | Vault count | Depositors | Permissionless | Notes |
|----------|-------|-----------|-------------|------------|---------------|-------|
| **Drift** | Solana | $494M platform, ~$170M in vault strategies | Unknown (multiple operators: Gauntlet, Neutral Trade, Elemental) | Unknown | Yes | Circuit Vaults: $100M TVL. vaults-sdk NPM available. Delegate can only trade, not withdraw. |
| **Paradex** | Starknet appchain | **$53M** (Gigavault: $52.9M) | **~31 active** (1 protocol + 12 VTFs + 18 user) | **5,469** (Gigavault) | Yes ($100 min) | Verified via browser. "Create" button visible. VTFs are leveraged index products. Most user vaults are tiny (<$1K). |
| **Apex Omni** | Multi-chain (zkLink) | **$13.6M** (Insurance Vault: $13.5M) | **~60 user vaults** (6 pages x 10) + 1 protocol vault | **1,247** total | Yes (100 USDT min) | Verified via browser. Nearly identical to Hyperliquid model. Net value / share price tracked. **2026-03-12: Checked user vaults — quality is poor. Most vaults have negligible TVL, low activity, and unimpressive performance. Not worth integrating.** |
| **Bluefin** | Sui | **$65.9M** | **14+ main vaults** + "Other Vaults" section | **40,422** | Curator-managed (via Ember Protocol) — NOT user-permissionless | Verified via browser. Vaults managed by curators (Gamma, Third Eye, MEV Capital, R25, Ember). More like Morpho-style curated vaults. |
| **SynFutures** | Base (expanding) | ~$62M platform TVL | Vault page returned error — feature appears nascent | Unknown | Yes (claimed) | App vault page 404. Landing page shows no vault listing. Feature may be in development. |
| **Aevo** | Ethereum L2 rollup | **$58.6K** (!) | **1 strategy** (Basis Trade) | Unknown | Claimed (Ribbon V3) | Verified via browser. Legacy Ribbon vaults disabled after Dec 2025 exploit (~$2.7M loss). "More strategies coming soon." Effectively dead for now. |
| **SpedX** | Unknown | Unknown | Unknown | Unknown | Yes (claimed) | Could not verify — too niche to find concrete data. |

### Key takeaway from verified data

Only **Paradex** and **Apex Omni** have active, verifiable permissionless user vault ecosystems comparable to Hyperliquid. **Drift** is significant but on Solana (non-EVM). **Bluefin** is curated (not user-permissionless). **Aevo** and **SynFutures** are effectively dead/nascent for vaults.

### Not permissionless or limited

| Protocol | Chain | Notes |
|----------|-------|-------|
| dYdX v4 | dYdX Chain | MegaVault only — single approved operator (Greave Cayman). LP deposits allowed but not manager creation. |
| Vertex | Multi-chain | LP vaults via Elixir/Skate partners only — not user-creatable. |
| RabbitX | Starknet | RLP liquidity pool exists but no user vault creation confirmed. |
| Kwenta/Synthetix | Optimism | Delegated trading exists. Vault products planned for 2026 but not yet live. |
| JOJO | Base | Flexible earn product but no user-managed vault creation confirmed. |
| KiloEx | Multi-chain | Hybrid vault exists (ERC-4626 style) but permissionless creation unclear. |
| LogX | Multi-chain | LLP pool only — no user vault creation found. |
| IntentX | Omnichain | Early stage. Backed vault LP but no user creation confirmed. |
| Pear Protocol | Multi-chain | AI-powered asset management on Hyperliquid. Permissionless platform but vault creation details scarce. |

---

## Centralised exchanges with copy trading features

### Verified numbers

| Exchange | Permissionless? | Lead traders | Copiers/Users | Strategies | Fund model | API |
|----------|----------------|-------------|--------------|------------|-----------|-----|
| **Bitget** | Medium (20%+ ROI or 100 followers) | **200K+** | 110M+ copy trades executed | 305 tokens | Signal-copy + strategy sales | Yes (comprehensive) |
| **Gate.io** | High (300 USDT min) | Unknown | **127K+** active copiers | **742K+** strategies created, 51K copied | Signal-copy, profit-share | Yes (v4) |
| **BingX** | Medium (55% win + 30% profit) | **15K+** elite | **11.5M** users | Unknown | Signal-copy, profit-share | Yes (AI endpoints) |
| **Phemex** | Low ($20K profit or 200% ROI) | **5.5K+** | 30K+ copiers | Unknown | Signal-copy, 1-12% profit-share | Yes |
| **Bybit** | Low (rank or referral) | Unknown | Unknown | 1 per Pro Master | **Share-based** (NAV, 180-day lock) | Limited |
| **Binance** | High (500-1,000 USDT) | Unknown | 250M+ platform users | Unknown | Signal-copy, up to 30% | Yes |
| **OKX** | Low (100K USDT min) | Unknown | Unknown | Unknown | Signal-copy, ~12% | Yes (v5) |
| **KuCoin** | High (no explicit min) | Unknown | **150K+** active copiers | Unknown | Signal-copy, 10%+ | Yes |
| **MEXC** | Medium (experience required) | Unknown | Unknown | Unknown | Signal-copy | Yes |

### Key insight: CEX fund models

- **Almost all CEXes use signal-copying** — followers' funds stay in their own accounts, trades are mirrored proportionally. This is NOT a pooled vault model.
- **Exception: Bybit Copy Trading Pro** — true share-based model with NAV pricing, lock-ups, and redemption windows. Closest to on-chain vaults.
- None of the CEXes use share tokens or on-chain verifiable positions.
- **Bitget** is the most transparent with publicly available statistics and the most comprehensive API.

---

## Summary: best candidates for integration

### Perp DEXes — re-ranked after verification

| Priority | Protocol | Chain | TVL | Vaults | Why |
|----------|----------|-------|-----|--------|-----|
| 1 | **Paradex** | Starknet | $53M | ~31 active | Verified permissionless. Large TVL in Gigavault. Active user vault creation. Documented API. Non-EVM (Starknet). |
| 2 | **Apex Omni** | Multi-chain (zkLink) | $13.6M | ~60 user vaults | Verified permissionless. Nearly identical to Hyperliquid model. zkLink-based. |
| 3 | **Drift** | Solana | $170M+ in vaults | Unknown | Significant TVL. Mature SDK. Non-EVM (Solana). Circuit Vaults: $100M. |
| 4 | **Bluefin** | Sui | $65.9M | 14+ | High TVL and depositors (40K), but curator-managed, NOT user-permissionless. Non-EVM (Sui). |
| — | **Aevo** | Ethereum L2 | $59K | 1 | Effectively dead after Ribbon exploit. Skip. |
| — | **SynFutures** | Base | ~$62M | Vault feature broken | App vault page errors. Feature nascent. Skip for now. |

### CEXes (for website comparison/inclusion)

| Priority | Exchange | Why |
|----------|----------|-----|
| 1 | **Bitget** | 200K+ traders, 110M+ copy trades, best API, most transparent data |
| 2 | **Gate.io** | 742K+ strategies, most permissionless (300 USDT), large dataset |
| 3 | **Bybit Copy Trading Pro** | Only CEX with true share-based vault model (but limited data) |
| 4 | **BingX** | 15K+ elite traders, 11.5M users, AI strategy endpoints |

### Integration challenges

- **Paradex** and **Drift** are non-EVM (Starknet and Solana) — need chain-specific SDKs
- **Apex Omni** runs on zkLink — may need zkLink-specific integration
- **CEXes** require exchange-specific APIs — metrics from centralised leaderboard endpoints, not blockchain data
- Most CEXes don't publish aggregate stats — data scraping from leaderboards may be needed

---

## API analysis for vault data and historical data (2026-03-12)

### DEX vault APIs

| Protocol | Base URL | List vaults | Vault details | Historical data | History limit | Granularity | Auth | Python SDK |
|----------|----------|-------------|---------------|-----------------|---------------|-------------|------|------------|
| **Apex Omni** | `omni.apex.exchange/api/v3/vault/` | `GET /ranking` (568 total, paginated) | `GET /profile?vaultId=` | `GET /fund-net-values?vaultId=` (daily NAV + TVL) | **Full history from inception** (no cap) | Daily (24h at 08:00 UTC) | Public (no auth) | `apexomni` (trading only, no vault methods) |
| **Paradex** | `api.prod.paradex.trade/v1/` | `GET /vaults` (1,537 vaults) | `GET /vaults/summary` (TVL, share price, ROI, depositors, drawdown) | `GET /vaults/history?address=&type=price&resolution=` | **100 data points max** (no pagination, no time range params) | alltime (~5d gaps), 8h, 1h | Public (no auth) | `paradex-py` (no vault methods) |
| **Drift** | `data.api.drift.trade/` | `GET /stats/vaults` (296 vaults) | Pre-computed APYs via `app.drift.trade/api/vaults` | `GET /user/{vaultUser}/snapshots/trading?days=100` | **100 days max** | Daily | Public (no auth) | `driftpy` (has vault module, reads Solana on-chain) |

### DEX API details

#### Apex Omni — best historical data API

**Vault listing** (`GET /api/v3/vault/ranking?page=0&limit=100`):
- Returns paginated vault list (568 total, 131 active with status `VAULT_IN_PROCESS`)
- Fields: `vaultId`, `name`, `desc`, `vaultNetValue` (share price), `tvl`, `share` (total shares), `maxDrawDown`, `pnlRatio`, `status`, `createdTime`, `purchaseFeeRate`, `shareProfitRatio`
- Status values: `VAULT_IN_PROCESS` (active), `VAULT_FINISHED` (closed), `VAULT_INITIAL_FAILED`

**Vault detail** (`GET /api/v3/vault/profile?vaultId={id}`):
- Full metadata including pre-calculated rates, description, owner info

**Historical share price** (`GET /api/v3/vault/fund-net-values?vaultId={id}`):
- Returns daily NAV (net asset value) + TVL from vault inception
- No cap on data points — full history available
- Daily granularity (snapshots at 08:00 UTC)
- Batch endpoint: `GET /api/v3/vault/fund-net-value-batch?vaultIds={id1},{id2},...` for multiple vaults

**Rate limits**: 403 on excessive requests (UID and IP based), no explicit numbers documented.

#### Paradex — documented API, 100-point cap

**Vault listing** (`GET /v1/vaults`):
- Returns all 1,537 registered vaults (284 with TVL > 0, 37 with TVL > $1,000)
- Fields per vault from `/v1/vaults/summary`: `address`, `tvl`, `vtoken_price` (share price), `num_depositors`, `roi_1d`/`roi_7d`/`roi_30d`/`roi_all`, `max_drawdown`, `profit_share`, `status`

**Historical data** (`GET /v1/vaults/history?address={addr}&type={type}&resolution={res}`):
- Types: `price` (share price), `pnl` (profit/loss), `tvl` (total value locked)
- Resolutions: `alltime` (~120h/5d gaps, covers full history), `8h` (~33 days), `1h` (~4 days)
- **Hard cap: 100 data points per request**
- No pagination or time range parameters — cannot get more than 100 points
- Response: `{"results": [{"data": [...], "timestamps": [...]}]}`

**Swagger/OpenAPI**: Available at `api.prod.paradex.trade/swagger/index.html` (requires auth token to access).

**API docs**: [docs.paradex.trade](https://docs.paradex.trade/api/general-information/api-quick-start)

**Rate limits**: 1,500 requests/min per IP.

**WebSocket**: No vault-specific channels. WS channels: account, bbo, fills, funding, markets_summary, order_book, orders, positions, trades, transaction, transfers.

#### Drift — 100-day cap, Python SDK

**Vault listing** (`GET /stats/vaults`):
- Returns 296 vaults with basic metadata
- Fields: profitShare, managementFee, totalShares, netDeposits, totalWithdraws, etc.

**Historical snapshots** (`GET /user/{vaultUser}/snapshots/trading?days=100`):
- Daily TVL/equity snapshots for vault operator accounts
- **Maximum 100 days** of history
- Pre-computed APYs available via `app.drift.trade/api/vaults` (internal endpoint)

**Python SDK** (`driftpy`):
- Has vault module that reads Solana on-chain vault program data directly
- Can be used for real-time vault state, but historical data requires the REST API
- Installation: `pip install driftpy`

**S3 bucket**: Older raw data snapshots available at `drift-historical-data-v2.s3.eu-west-1.amazonaws.com` (deprecated format).

### CEX copy trading APIs

| Exchange | API exists | List traders | Historical PnL | Auth required | Python SDK | Verdict |
|----------|-----------|-------------|----------------|---------------|-----------|---------|
| **Bitget** | Yes (comprehensive) | Yes (`/api/v2/copy/mix-trader/current-track-symbol` + broker V1 endpoints) | Yes (daily, up to 180d via `profitDateGroupList`) | API key required | `python-bitget` (community) | **Best CEX — actionable** |
| **Gate.io** | **No copy trading API** | No | No | N/A | N/A | UI-only, skip |
| **Bybit** | Minimal (order placement only) | No | No | N/A | N/A | Copy Trading Pro has no data API |
| **BingX** | Moderate | No public listing (own account only) | Yes (profit summaries for individual traders) | API key required | None | Limited — no discovery endpoint |

#### Bitget — best CEX API

**Copy trading endpoints** (all require API key + signature):
- `GET /api/v2/copy/mix-trader/current-track-symbol` — trader's current positions
- `GET /api/v2/copy/mix-trader/profit-history-summarys` — profit summaries
- Broker V1 endpoints for third-party data access
- `profitDateGroupList` — daily PnL grouped by date (up to 180 days)

**Limitation**: No fully public endpoint for browsing all traders without auth. Leaderboard data requires scraping or API key.

#### Gate.io, Bybit, BingX — skip

- **Gate.io**: Despite 742K+ strategies, has **zero** copy trading API endpoints. All copy trading is UI-only.
- **Bybit**: Copy Trading Pro (share-based vault model) has no data API whatsoever. Only order placement endpoints for copy traders exist.
- **BingX**: Has trader detail and profit history endpoints, but no public listing/discovery endpoint. All require auth. No Python SDK.

### Integration priority re-ranked by API quality

| Priority | Protocol | Type | Historical data quality | Integration effort | Why |
|----------|----------|------|------------------------|-------------------|-----|
| 1 | **Apex Omni** | DEX | **Excellent** — full history, daily, batch, public | Low (REST only) | Best API. Full NAV history from inception. Batch queries. Nearly identical to Hyperliquid model. |
| 2 | **Paradex** | DEX | **Good** — multi-resolution but 100-point cap | Low-Medium (REST + Starknet) | Documented API. Large TVL ($53M). 100-point cap requires `alltime` resolution for long history. |
| 3 | **Drift** | DEX | **Moderate** — 100-day cap, daily | Medium (REST + Solana SDK) | Significant TVL ($170M+). Python SDK available. 100-day history limit. Non-EVM (Solana). |
| 4 | **Bitget** | CEX | **Good** — daily PnL, 180-day window | Medium (REST + auth) | Only CEX with usable data API. Requires API key. Signal-copy model (not pooled vault). |
