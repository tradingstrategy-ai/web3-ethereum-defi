# Repository guide

See the [project README](../../README.md) for installation and the [development guide](../../docs/source/development.rst) for the local test environment.

## Contributor references

- [Agent CLI commands and review troubleshooting](agent-tricks-and-troubleshooting.md)
- [Grok CLI review commands](grok-cli.md)
- [Numbered database migrations and production recovery](migration-scripts.md)
- [Vault adapters, guard deployment and executor integration](vault-integration.md)
- [Writing public descriptions](description-writing-style.md)
- [How vault notes are selected and published](vault-notes.md)
- [Comparing curator feeds with CuratorWatch](curatorwatch.md)
- [Diagnosing HyperEVM fork timeouts](hyperevm-anvil-fork-timeouts.md)
- [Google Sheets test setup](gspread.md)
- [Birch Hill description sources](../../docs/protocol-research/birch-hill-research.md)
- [Nest AXI description sources](../../docs/protocol-research/nest-axi-research.md)
- [Systemic Strategies L/S Grids description sources](../../docs/protocol-research/systemic-strategies-vault-research.md)

## Scanner operations and maintenance

- [Hypercore native vault guard integration](../../docs/README-Hypercore-guard.md)
- [Atoma execution validation](../../docs/README-atoma-execution.md)
- [Block timestamp storage](../../docs/README-block-timestamp-storage.md)
- [Contract sizes and compiler optimisation](../../docs/README-contract-size.md)
- [Dense block timestamp caches](../../docs/README-dense-timestamps.md)
- [Hypercore permission recovery and DuckDB backups](../../docs/README-hypercore-permission-recovery.md)
- [HyperEVM goldsky eRPC consensus failure](../../docs/README-hyperevm-goldsky-failure.md)
- [HyperEVM HyperCore read gas and `-32003 out of gas` multicall failures](../../docs/README-hyperevm-hypercore-read-gas.md)
- [Hyperliquid vault data limitations](../../docs/README-hyperliquid-vault-limitations.md)
- [Hypersync scan tests — disabled on CI by default](../../docs/README-hypersync-tests.md)
- [Lighter Core pool execution and satellite deployment](../../docs/README-lighter-core-execution.md)
- [Test suite performance](../../docs/README-test-suite-performance.md)
- [Vault description formatting](../../docs/README-vault-description-formatting.md)
- [Vault scan readiness manifest](../../docs/README-vault-scan-manifest.md)
- [Withdrawal capacity warnings](../../docs/README-withdrawal-capacity.md)
- [ERC-4626 vault scripts](../../scripts/erc-4626/README-vault-scripts.md)

## Vault research and reporting

- [Protocol research notes](../../docs/protocol-research/README.md)
- [Antarctic settlement chart snapshot](../../docs/protocol-research/antarctic-charts/README.md)
- [Vault lead migrations](../../eth_defi/erc_4626/README-vault-leads.md)
- [Vault protocol deposit and redemption support](../../eth_defi/erc_4626/README-vault-protocol-support.md)
- [Vault reader states and warmup system](../../eth_defi/erc_4626/vault_protocol/README-reader-states.md)
- [Utilisation and available liquidity metrics for lending vaults](../../eth_defi/erc_4626/vault_protocol/README-utilisation.md)
- [Vault redeemable liquidity vs utilisation](../../eth_defi/erc_4626/vault_protocol/README-vault-redeemable.md)
- [Antarctic vaults](../../eth_defi/erc_4626/vault_protocol/antarctic/README-Antarctic.md)
- [Arcadia vault descriptions](../../eth_defi/erc_4626/vault_protocol/arcadia/README-arcadia.md)
- [Euler vault metadata](../../eth_defi/erc_4626/vault_protocol/euler/README-euler.md)
- [Flying Tulip staking integration](../../eth_defi/erc_4626/vault_protocol/flying_tulip/README-Flying-Tulip.md)
- [Frax vault families](../../eth_defi/erc_4626/vault_protocol/frax/README-Frax.md)
- [Ostium vault V1.5.0 breaking change](../../eth_defi/erc_4626/vault_protocol/gains/README-Ostium.md)
- [gTrade metadata audit](../../eth_defi/erc_4626/vault_protocol/gains/README-gTrade-metadata.md)
- [IPOR curator mapping](../../eth_defi/erc_4626/vault_protocol/ipor/README-IPOR-Curators.md)
- [Morpho metadata requests](../../eth_defi/erc_4626/vault_protocol/morpho/README-morpho-metadata.md)
- [Nova Markets Conviction Pool](../../eth_defi/erc_4626/vault_protocol/nova_markets/README-Nova-Markets.md)
- [Royco vault API](../../eth_defi/erc_4626/vault_protocol/royco/README-Royco.md)
- [Rysk Premium vaults](../../eth_defi/erc_4626/vault_protocol/rysk/README-Rysk-vaults.md)
- [T3tris vault protocol](../../eth_defi/erc_4626/vault_protocol/t3tris/README-t3tris.md)
- [Wildcat lending markets](../../eth_defi/erc_4626/vault_protocol/wildcat/README-Wildcat.md)
- [Asseto tokenised funds](../../eth_defi/tokenised_fund/asseto/README-Asseto.md)
- [The best-performing stablecoin vaults: blog post drafts](../../eth_defi/vault_report/README-best-vaults-news.md)
- [Monthly vault report: blog post outline](../../eth_defi/vault_report/README-blog-post-outline.md)
- [Monthly best-performing stablecoin vaults report](../../eth_defi/vault_report/README-vault-report.md)

## Integrations and scripts

- [GuardV0 — onchain trade validation for asset management](../../contracts/guard/README.md)
- [Web3-Eth-Defi integration contracts](../../contracts/in-house/README.md)
- [Trading Strategy Zodiac-module for Safe multisignature wallets](../../contracts/safe-integration/README.md)
- [Derive session key for vault traders](../../docs/derive-onboarding/README-derive-trader.md)
- [Derive.xyz integration -- implementation summary](../../docs/source/api/derive/README.md)
- [About Aave v3](../../eth_defi/aave_v3/README.md)
- [Contract ABIs in eth_defi](../../eth_defi/abi/README.md)
- [ApeX native vault reader](../../eth_defi/apex/README-apex.md)
- [Circle CCTP V2 integration](../../eth_defi/cctp/README-cctp.md)
- [Core3 projects data API](../../eth_defi/core3/README-core3.md)
- [Currency API historical exchange rate pipeline](../../eth_defi/currency_api/README-currency-api.md)
- [Data directory](../../eth_defi/data/README.md)
- [Vault deposit-status artefact](../../eth_defi/data/deposit-status/README-deposit-status.md)
- [Feed data](../../eth_defi/data/feeds/README.md)
- [Vault protocol metadata](../../eth_defi/data/vaults/README.md)
- [Derive.xyz integration](../../eth_defi/derive/README-derive.md)
- [Enzyme vaults](../../eth_defi/enzyme/README-Enzyme-vaults.md)
- [Webshare proxy](../../eth_defi/event_reader/README-webshares-proxy.md)
- [Feed post collection submodule](../../eth_defi/feed/README-feed.md)
- [GMX Lagoon integration security analysis](../../eth_defi/gmx/README-GMX-Lagoon.md)
- [GMX liquidity-provider vaults](../../eth_defi/gmx/README-GMX-Vaults.md)
- [GMX CCXT adapter for eth_defi](../../eth_defi/gmx/README.md)
- [GMX CCXT adapter](../../eth_defi/gmx/ccxt/README.md)
- [GMX Subsquid GraphQL Integration](../../eth_defi/gmx/graphql/README.md)
- [Hibachi API](../../eth_defi/hibachi/README.md)
- [Lighter guard integration architecture](../../eth_defi/lighter/README-lighter-guard.md)
- [Neutral Trade vault data](../../eth_defi/neutral_trade/README-delta-neutral.md)
- [Pacifica API](../../eth_defi/pacifica/README-pacifica.md)
- [Xerberus vault risk classification](../../eth_defi/xerberus/README-xerberus.md)
- [YieldBasis liquidity-provider vaults](../../eth_defi/yield_basis/README-YieldBasis.md)
- [ApeX native vault metrics](../../scripts/apex/README-apex-vaults.md)
- [Base scripts](../../scripts/base/README.md)
- [Debian Bullseye compatibility](../../scripts/debian-bullseye-compatibility/README.md)
- [Derive v3 native vaults](../../scripts/derive/README-Derive-vaults.md)
- [GRVT native vault metrics pipeline](../../scripts/grvt/README-grvt-vaults.md)
- [Hibachi native vault metrics pipeline](../../scripts/hibachi/README-hibachi-vaults.md)
- [Alternative data sources for Hyperliquid vault historical data](../../scripts/hyperliquid/README-hyperliquid-alternative-data-sources.md)
- [Hyperliquid vault data backfill from S3 archive](../../scripts/hyperliquid/README-hyperliquid-backfill.md)
- [Hyperliquid copy trading and HFT account identification](../../scripts/hyperliquid/README-hyperliquid-copy-trading.md)
- [Hyperliquid trade event history](../../scripts/hyperliquid/README-hyperliquid-trade-events.md)
- [High-frequency Hyperliquid vault data fetcher](../../scripts/hyperliquid/README-hyperliquid-vaults-high-frequency.md)
- [Hyperliquid native vault metrics pipeline](../../scripts/hyperliquid/README-hyperliquid-vaults.md)
- [Infinit migration](../../scripts/infinit/README.md)
- [Lighter native pool metrics pipeline](../../scripts/lighter/README-lighter-vaults.md)
- [Neutral Trade vault collection](../../scripts/neutral-trade/README-neutral-trade-vaults.md)

## Tests and fixtures

- [Fast Anvil fork tests: shared forks, block cache and session deployments](../../eth_defi/testing/README.md)
- [Repository-supplied Anvil fork RPC cache seed](../../eth_defi/testing/rpc_cache_seed/README.md)
- [Vault protocol detection tests](../../tests/erc_4626/vault_protocol/README.md)
- [Antarctic transport fixtures](../../tests/erc_4626/vault_protocol/fixtures/README-antarctic.md)
- [Testing GMX orders](../../tests/gmx/README.md)
- [Guard integration tests](../../tests/guard/README.md)
- [Provider integration tests](../../tests/provider/README.md)
- [JSON-RPC scenario tests](../../tests/rpc/README.md)
- [Historical JSON-RPC provider checks](../../docs/performance/2023-03-provider-checks/results.md)

## Asset sources

See the [ABI guide](../../eth_defi/abi/README.md) for contract sources and how to load an interface. Logo provenance is kept beside the originals for [vaults and curators](../../eth_defi/data/vaults/original_logos/) and [stablecoins](../../eth_defi/data/stablecoins/original_logos/).

## Local development tools

The [Claude settings](../settings.json) configure the default model and tool permissions. Use `git` and `gh` for pull requests.

### Previewing a local web page

To inspect a Svelte page with Claude, start the project’s Vite development server and connect the Playwright MCP server.

```shell
claude mcp add playwright npx @playwright/mcp@latest
```
