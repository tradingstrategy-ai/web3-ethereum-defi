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
| **Apex Omni** | Multi-chain (zkLink) | **$13.6M** (Insurance Vault: $13.5M) | **~60 user vaults** (6 pages x 10) + 1 protocol vault | **1,247** total | Yes (100 USDT min) | Verified via browser. Nearly identical to Hyperliquid model. Net value / share price tracked. |
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
