# Neutral Trade API-only vault integration research

Research date: 2026-11-12. This is an integration assessment, not an implemented collector.

## Recommendation

Neutral Trade fits the existing API-only native vault pipeline. Collect its vault catalogue, share prices, TVL and performance through REST, persist observations locally, and export them through the shared vault dataset. No Solana or EVM RPC connection is needed by our collector.

Full coverage requires a partner API key. Marketplace and Voltr coverage, product-tier metadata and fee treatment need confirmation before implementation.

## Sources and live checks

The [API, SDK and IDL guide](https://docs.neutral.trade/for-distribution-partners/api-and-sdk-and-idl) links to the [live OpenAPI specification](https://api.neutral.trade/openapi.json), downloaded during this research. The specification describes projection-backed read routes with zero Solana or EVM RPC calls on the read path. The SDK, IDL and transaction builders are unnecessary for this read-only integration.

The following anonymous requests were checked on 2026-11-12:

| Request | Result |
| --- | --- |
| `GET /openapi.json` | HTTP 200; live specification retrieved |
| `GET /public/yields` | HTTP 200; 15 vaults, comprising 14 Solana vaults and one EVM vault |
| `GET /v2/vaults` | HTTP 401; valid API key required |

Authenticated routes were not tested. Their capabilities below are documented contracts, not verified response contents.

## Available data

The following table selects the main read routes relevant to the collector. All paths use `https://api.neutral.trade` as their base URL.

| Endpoint | Data | Access |
| --- | --- | --- |
| `/public/yields` | Vault names, addresses, chains, deposit assets, USD TVL and APY windows | Public |
| `/v2/status` | Projection health and freshness | Partner key |
| `/v2/vaults` | Directory, manager wallet, category, fees, limits, visibility and lifecycle settings | Partner key |
| `/v2/vault/{id}` | Vault configuration and timing information | Partner key |
| `/v2/vault/{id}/share-price` | Current price and paginated historical ticks | Partner key |
| `/v2/vault/{id}/snapshots` | Daily closing prices, shares, TVL, flows and fee aggregates | Partner key |
| `/v2/vault/{id}/metrics` | Returns, Sharpe, drawdown and fee schedule | Partner key |
| `/v2/vault/{id}/share-price-display` | Display history with live, backtest and pre-Neutral phases | Partner key |

Partner keys are supplied through `x-api-key` or `Authorization: Bearer`. Store the key server-side. Respect the key's quota headers and `Retry-After` on HTTP 429.

The [public yield feed](https://api.neutral.trade/public/yields) included Neutral Autopilot, NT Earn, Hyperliquid Funding Arb, market-making strategies, Multi-Asset Volatility Arbitrage, several CTA strategies and Meridian Liquidity Provider. Meridian's chain was `evm:4663` (Robinhood). Thirteen Solana vaults were denominated in USDC, one Solana vault in WSOL, and the EVM vault in USDe (reported as `USDE`). Do not assume a universal USDC denomination.

## Catalogue and Marketplace coverage

The [Marketplace page](https://www.neutral.trade/strategies?tier=marketplace) listed Meridian Liquidity Provider, LOMR and Hachiko during the research. Only Meridian appeared in the public yield response. The page also linked separately to Voltr. Thus the public feed does not establish complete coverage of the requested catalogue.

Neutral Trade's [September and October update](https://www.neutral.trade/blog/september-october-2026-update) distinguishes three groups:

- **Neutral Curated:** full due diligence and a live test with Neutral capital; eligible for Autopilot allocations.
- **Neutral Marketplace:** basic due diligence and KYB, without the live Neutral-capital test or Autopilot allocations.
- **Voltr:** permissionless curator vaults; the curator controls funds, and APY and share price are self-reported.

The authenticated directory is documented to include every ntbundle registry vault and registered EVM vaults, independently of catalogue visibility. Retired legacy Drift vaults are excluded. Only `visible=true` records should be presented publicly; null is not an approval. The documented directory schema has a category but no explicit Curated/Marketplace product-tier field. The metrics contract describes external vaults with a PPS series but no current bundle-state projection; that does not establish complete Voltr coverage.

Confirm with Neutral Trade how to obtain LOMR, Hachiko and all eligible Voltr records and histories, and how to distinguish product tiers programmatically. Do not infer a tier from a vault name or invent missing data.

## Existing repository precedents

These integrations collect native vault data through APIs and feed the shared dataset without requiring an ERC-4626 contract reader:

| Integration | Relevant implementation |
| --- | --- |
| ApeX | [Exporter](../../eth_defi/apex/vault_data_export.py) and [pipeline guide](../../scripts/apex/README-apex-vaults.md): catalogue, retained history, lifecycle handling, DuckDB and unified exports |
| Hibachi | [Exporter](../../eth_defi/hibachi/vault_data_export.py) and [pipeline guide](../../scripts/hibachi/README-hibachi-vaults.md): simple metadata and daily share-price collection |
| GRVT | [Exporter](../../eth_defi/grvt/vault_data_export.py): API identities, manager metadata and explicit fee treatment |
| Derive v3 | [Exporter](../../eth_defi/derive/v3_vault_data_export.py): accounting denomination separated from deposit asset, API price provenance and mainnet-only export |
| Hyperliquid | [Pipeline guide](../../scripts/hyperliquid/README-hyperliquid-vaults.md): established native API collector, with protocol-specific economic-return reconstruction |
| Lighter | [Pipeline guide](../../scripts/lighter/README-lighter-vaults.md): API-only pool collection and shared cleaning/export pipeline |

[Pacifica](../../eth_defi/pacifica/README-pacifica.md) has Solana API research and parser groundwork, but its DuckDB, native price export and scheduling integration are unfinished. It is not a complete collector to copy.

## Proposed implementation

Use the ApeX and Derive patterns for a dedicated `eth_defi/neutral_trade/` package:

```text
Neutral REST API
    -> local DuckDB observations and history
    -> shared VaultDatabase metadata and raw price Parquet
    -> existing cleaning, returns and JSON export
```

- Add a typed HTTP client, catalogue/history collector and exporter, then register the source in the [all-chain orchestrator](../../eth_defi/vault/scan_all_chains.py). A proposed server-side credential name is `NEUTRAL_TRADE_API_KEY`.
- Preserve exact, case-sensitive Solana addresses. The current [address validator](../../eth_defi/utils.py) does not accept bare base58 addresses, so deliberately introduce a supported native identifier or Solana address support. Retain original addresses alongside any synthetic identifiers. EVM records should retain their actual chain/address identities.
- Keep product tier, underlying protocol and verified curator attribution separate. A manager wallet alone does not identify a firm. Do not classify every strategy as perpetual futures: the catalogue also includes options, directional and other strategies.
- Preserve raw integer strings and token decimals before normalisation. EVM projected prices use the asset-decimal scale, not necessarily raw contract precision. Separate asset-denominated prices, USD valuation and deposit assets.
- Use opaque cursors for historical pagination. Merge matching observations and retain unmatched older history; never replace the archive with only the latest response. Use unconstrained DuckDB ingestion tables with application-level deduplication, following the repository's Python 3.14/DuckDB safeguards.
- Preserve source timestamps, `priceAsOf`, legacy markers and unavailable/degraded states. Compare mixed-chain freshness by block time, not slot. Missing holdings, exposure and liquidity data remain unknown, not zero.

## Accounting and overlap risks

**Separate live returns from display history.** The display endpoint includes `backtest`, `pre-neutral` and `live` phases and explicitly marks carried-forward points. Do not pass the entire display series into live vault return calculations. Canonical history also distinguishes event ticks from migrated daily observations; snapshots distinguish final closes from legacy daily means.

**Establish fee semantics before calculating net returns.** The API overview describes public APYs as gross, before performance and management fees, while the endpoint contract notes that EVM sampled prices already reflect protocol fee dilution. The metrics endpoint's after-fee figures use a documented presentation formula, which is not a reconstruction of an individual depositor's high-water-mark fees. External vaults use a zero fee schedule because their feed PPS is already net of venue fees. Preserve source metrics separately and avoid charging fees twice.

**Avoid duplicate EVM records and double-counted capital.** The repository already tracks Neutral Autopilot's Ethereum and Monad Accountable feeders; see the [existing research](neutral-trade-autopilot-2026-10-29.md). Link the Solana master and feeder records, retain canonical chain/address identities and define aggregate TVL treatment. Autopilot also allocates across underlying strategies, so platform-level totals require care beyond summing listed vault TVLs.

Meridian Liquidity Provider on Robinhood (`4663`, `0x24b84023c8e4Da635be228C380C09bfE5271BF9d`) is also already recognised by the repository's Accountable integration. Its existing curator attribution is Kappa Lab; Neutral Trade distributes the vault. Merge by canonical chain/address identity and preserve that attribution rather than exporting a second Neutral-curated record.

**Do not treat visibility as liquidity.** A visible vault may be paused, private, full or awaiting manager settlement. EVM minimum redemptions are in share units, while deposit limits use asset units. Null withdrawal capacity or timing is not a promise of instant redemption.

## Prerequisites and next steps

1. Obtain a partner API key and run a minimal authenticated catalogue, detail and history check end-to-end. Record a dated, redacted result before claiming the integration works.
2. Confirm Marketplace/Voltr catalogue completeness, product-tier fields, history availability and redistribution permissions with Neutral Trade.
3. Check actual price scales and fee semantics for representative USDC, WSOL and EVM vaults, including a legacy/live boundary.
4. Implement the collector and focused coverage for pagination, case-sensitive identities, history preservation, unavailable data and accounting boundaries.

No production state was changed and no collector was implemented during this research.
