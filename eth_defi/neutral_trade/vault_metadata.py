"""Reviewed native vault identities and public strategy descriptions.

The partner REST directory exposes manager wallets, not curator names or
editorial descriptions. This address-scoped overlay supplements it using
Neutral Trade's official strategy pages and documentation. It never infers
identity from a shared manager wallet or a vault's display name.

Identity source: https://www.neutral.trade/strategies
Strategy source index: https://docs.neutral.trade/llms.txt
Reviewed on 2026-11-12. Published curator aliases are not legal identities.
"""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class NativeVaultMetadata:
    """Reviewed public copy for one exact Solana vault account."""

    #: Provider display name at review time, for audit and tests only.
    name: str
    #: Canonical curator feeder slug, including published aliases.
    curator_slug: str
    #: Single-sentence, plain-text strategy summary.
    short_description: str
    #: Strategy explanation in Markdown.
    description: str
    #: Official strategy documentation establishing the investment mechanism.
    source_url: str


_DOCS = "https://docs.neutral.trade/for-capital-allocators"
_MARKET = _DOCS + "/usdc-yield/market-neutral"
_DIRECTIONAL = _DOCS + "/usdc-yield/directional"

#: Exact base58 identities from the official site's reviewed strategy registry.
#: The REST directory remains authoritative for visibility and denomination.
NATIVE_VAULT_METADATA: dict[str, NativeVaultMetadata] = {
    "3vZKAcd74bzwNYZmsJndYExkGj6ABQwrUoiMtdNfzLZe": NativeVaultMetadata(
        name="Options Market Making",
        curator_slug="xanthas",
        short_description="The vault sells short-term bitcoin options on OKX and uses offsetting trades to reduce the effect of price changes.",
        description=(
            "#### Vault's trading strategy\n\n"
            "The vault earns payments from selling short-term bitcoin [options](https://tradingstrategy.ai/glossary/options), contracts that give buyers the right to buy or sell at an agreed price. Xanthas's [automated strategy](https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/options-market-making) sells them on [OKX](https://www.okx.com/) when their prices imply larger market swings than its model expects over the next three days.\n\n"
            "The software offsets some bitcoin price risk with [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future) contracts that track prices without expiring. OKX is the only named exchange, but the documentation does not separately identify the venue for these offsetting contracts. Trades stay within preset limits, and [Copper](https://copper.co/) holds assets outside the exchange.\n\n"
            "**Larger price swings than forecast can cause losses**, despite the offsetting positions. Adjusting those positions also costs money, and custody does not remove the risk of disrupted or frozen access to assets."
        ),
        source_url=_MARKET + "/options-market-making",
    ),
    "46gSEJLNTdDFq7DPjH8RbcTmG1gyw4JCKCr1XfuAfYvh": NativeVaultMetadata(
        name="Mid-frequency Market Making",
        curator_slug="adverseguard-alpha",
        short_description="The vault places buy and sell orders for ether price contracts on Binance, seeking trading profits and exchange rebates.",
        description=("#### Vault's trading strategy\n\nThe vault earns the difference between buying and selling prices, plus rebates from [Binance](https://www.binance.com/) for orders that other traders can fill. It trades ether [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts that track ether's price without an expiry date.\n\nAdverseGuard Alpha's [automated quoting model](https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/mid-frequency-market-making) continuously estimates a fair price and adjusts buy and sell orders around it. A second model reduces or pauses orders when it detects signs of a large price move.\n\nPrices can still move against an order after it fills. Trading system failures can also prevent the vault from adjusting its positions in time."),
        source_url=_MARKET + "/mid-frequency-market-making",
    ),
    "4QBTzqUbn1crLg2PDUGt8abJfqHjeuZkjNLjCCit69ui": NativeVaultMetadata(
        name="Multi-Asset Volatility Arbitrage",
        curator_slug="d-research",
        short_description="The vault trades differences in expected price swings across stock, bond and volatility markets.",
        description=(
            "#### Vault's trading strategy\n\n"
            "The vault seeks profits from differences in how markets price future price swings across stocks, bonds and contracts tied to [volatility](https://tradingstrategy.ai/glossary/volatility), the size of market price swings. D Research's [trading models](https://docs.neutral.trade/for-capital-allocators/usdc-yield/rwa/multi-asset-volatility-arbitrage) scan frequently traded instruments, including listed securities linked to digital assets.\n\n"
            "The named venues are [Nasdaq](https://www.nasdaq.com/), [NYSE](https://www.nyse.com/) and [CME](https://www.cmegroup.com/), but the strategy documentation does not map each instrument to its exchange. Position limits, stress tests and daily adjustments constrain risk; the documentation does not explain which trading decisions require human approval.\n\n"
            "**Quarterly withdrawals require advance notice and are capped** across the product. A withdrawal request may therefore remain partly invested, leaving it exposed to further losses."
        ),
        source_url=_DOCS + "/usdc-yield/rwa/multi-asset-volatility-arbitrage",
    ),
    "Aea2VZfX6hw758cxx2WaGm7kqmdVjhbYsLfde5chVG1W": NativeVaultMetadata(
        name="CTA - Momentum LongShort",
        curator_slug="adverseguard-alpha",
        short_description="The vault follows crypto price trends on Binance, taking positions that can benefit from rising or falling prices and adjusting them hourly.",
        description=("#### Vault's trading strategy\n\nThe vault trades frequently traded crypto assets on [Binance](https://www.binance.com/), seeking profits from continuing price trends and changes in how asset prices move relative to one another. It takes positions that benefit from either rising or falling prices.\n\nAdverseGuard Alpha's [automated models](https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/cta-momentum-longshort) estimate fair prices and combine several trading signals, adjusting the portfolio hourly. Software also changes order timing and size to suit the available buyers and sellers, while position limits restrict the amount at risk.\n\nThe strategy can lose money when trends reverse or its models misread market conditions. Holding positions on both sides of the market does not ensure that their gains and losses cancel out."),
        source_url=_DIRECTIONAL + "/cta-momentum-longshort",
    ),
    "C68A4mAhA9EE4rWq9HmFnq3SPcbNmst6qiBWcna5VDHy": NativeVaultMetadata(
        name="HFT Multi-factor",
        curator_slug="vision-research",
        short_description="The vault uses automated models to trade short-term crypto price moves on Binance and earn income from buy and sell orders.",
        description=("#### Vault's trading strategy\n\nThe vault trades crypto assets and [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts that follow asset prices without expiring, on [Binance](https://www.binance.com/). Vision Research's [automated models](https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/hft-multi-factor) combine predictions of continuing price trends with predictions that prices will return towards an average.\n\nMost trades begin as orders waiting for other traders to fill them. This [market making](https://tradingstrategy.ai/glossary/market-making) seeks the gap between buy and sell prices and exchange rebates, alongside profits from predicted price moves.\n\nThe models trade over short periods, but the documentation does not specify a fixed trading or review interval. Positions can exceed the capital deposited, magnifying losses when prices move against the vault or its models fail."),
        source_url=_MARKET + "/hft-multi-factor",
    ),
    "De47QBuMP7xukYBek5r4ScZF4HEk1kHW34ymKASz3DLt": NativeVaultMetadata(
        name="Delta Neutral Arbitrage",
        curator_slug="velox-trading-group",
        short_description="The vault uses offsetting crypto trades to earn from price differences between exchanges, trader payments and buy and sell orders.",
        description=(
            "#### Vault's trading strategy\n\n"
            "The vault places offsetting trades to profit from differences between exchange prices and between asset prices and futures prices. Velox Trading Group's [strategy](https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/delta-neutral-arbitrage) also collects differences in [funding payments](https://tradingstrategy.ai/glossary/funding-rate), recurring payments between traders on opposite sides of price contracts without an expiry date, and earns from offering buy and sell prices on decentralised markets.\n\n"
            "The named venues are [Binance](https://www.binance.com/), [OKX](https://www.okx.com/), [Bybit](https://www.bybit.com/), [Gate](https://www.gate.com/), [Hyperliquid](https://hyperliquid.xyz/) and [Lighter](https://lighter.xyz/). The documentation does not map individual trades to venues or specify which decisions need human approval; positions are monitored continuously.\n\n"
            "[Copper](https://copper.co/) and [Ceffu](https://www.ceffu.com/) hold assets outside centralised exchanges for those positions. Offsetting trades limit exposure to market direction, but **borrowing, failed trades and frozen or inaccessible assets can still cause losses**."
        ),
        source_url=_MARKET + "/delta-neutral-arbitrage",
    ),
    "DzmixPGgru86YkQY4LSEuM1Sfz7ZFPc1wHwSm4fejXbJ": NativeVaultMetadata(
        name="HFT Market Making",
        curator_slug="adverseguard-alpha",
        short_description="The vault updates buy and sell orders many times a second in Binance ether price contracts to earn trading profits and exchange rebates.",
        description=("#### Vault's trading strategy\n\nThe vault places buy and sell orders in ether [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts that track ether's price without expiring, on [Binance](https://www.binance.com/). Income comes from the gap between buying and selling prices and rebates for orders that other traders fill.\n\nAdverseGuard Alpha's [automated system](https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/hft-market-making) reads changes in pending orders and their place in trading queues, then updates its prices many times a second. It aims to keep the amount held between trades small, with assets held through [Ceffu](https://www.ceffu.com/).\n\nAbrupt price moves can leave the vault buying before a fall or selling before a rise. Connection failures can prevent timely changes to orders, and lower exchange rebates can reduce income."),
        source_url=_MARKET + "/hft-market-making",
    ),
    "Ec6uSz5EsffwfXp43pvpz4rf9ttJRFKES4ZSVX7gVVkQ": NativeVaultMetadata(
        name="CTA - Systematic Alpha",
        curator_slug="hyperithm",
        short_description="The vault uses automated models to invest in rising US stock markets and expected price recoveries, usually holding positions for three to five days.",
        description=("#### Vault's trading strategy\n\nThe vault takes positions that gain when US stock prices rise, using [models that follow trends and expected price recoveries](https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/systematic-alpha). Hyperithm's software adjusts the amount invested to the strength of its signals and usually holds positions for three to five days.\n\nThe investment mandate lists stock funds and equity index [futures](https://tradingstrategy.ai/glossary/futures), contracts tied to stock-market indices, on [Nasdaq](https://www.nasdaq.com/). It also names [NYSE](https://www.nyse.com/) and [Binance](https://www.binance.com/) without identifying the instruments traded there.\n\n**The strategy does not take positions designed to profit from falling stock prices.** Some instruments amplify price changes, so losses can be larger than the underlying market move; holding cash reduces exposure but does not protect the invested portion."),
        source_url=_DIRECTIONAL + "/systematic-alpha",
    ),
    "Eo3m78EQcHnwMyHtxaiz1vw4nwHa85RuGE8jLsrELhxj": NativeVaultMetadata(
        name="CTA - Adaptive Alpha",
        curator_slug="atlas-research",
        short_description="The vault trades short-term crypto price moves using automated models that follow trends or anticipate reversals.",
        description=("#### Vault's trading strategy\n\nThe vault trades contracts whose value depends on crypto asset prices, taking positions that can benefit from either rising or falling prices. Atlas Research's [automated models](https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/cta-adaptive-alpha) continuously monitor prices and incoming buy and sell orders, switching between following trends and anticipating reversals.\n\nPrice and time limits determine when positions close, and the system reduces activity when signals weaken. Reaching the daily loss threshold triggers a pause for risk review and model adjustments; it does not guarantee a maximum loss.\n\n**The execution venues are not disclosed** in the strategy documentation. Incorrect signals can leave the vault exposed to losses."),
        source_url=_DIRECTIONAL + "/cta-adaptive-alpha",
    ),
    "G5aMxQTbGWMnYycpfjHpD7Y1muoKBwaB1HtCdpUUcQZp": NativeVaultMetadata(
        name="CTA - Longshort Alpha",
        curator_slug="c-research",
        short_description="The vault uses automated crypto trading models to profit from price trends, reversals and recurring payments between futures traders.",
        description=(
            "#### Vault's trading strategy\n\n"
            "The vault trades crypto [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), contracts that track asset prices without expiring, primarily on [Binance](https://www.binance.com/). C Research's [automated models](https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/cta-longshort-alpha) trade price trends within the day and expected reversals, taking positions that can benefit from rising or falling prices.\n\n"
            "The strategy also seeks differences in [funding payments](https://tradingstrategy.ai/glossary/funding-rate), recurring payments between traders on opposite sides of these contracts. Trading runs continuously within position limits, with C Research able to intervene manually in exceptional circumstances as a safety measure. Any additional execution venues are not named.\n\n"
            "Losses can arise when many traders exit similar positions at once, funding payments change direction or the models misread prices. Offsetting positions reduce some market exposure but do not eliminate it."
        ),
        source_url=_DIRECTIONAL + "/cta-longshort-alpha",
    ),
    "HWjMfYfc7KoPchoEDa5UFyVSpJxY3aV1RAccKvABYb9z": NativeVaultMetadata(
        name="NT Earn",
        curator_slug="neutral-trade",
        short_description="The vault automatically moves deposits between lending markets on Kamino and Jupiter Lend to earn borrower interest.",
        description=("#### Vault's trading strategy\n\nThe vault lends deposits through [Kamino](https://kamino.com/) and [Jupiter Lend](https://jup.ag/lend), earning interest paid by borrowers. Neutral Trade's [allocation software](https://docs.neutral.trade/for-capital-allocators/usdc-yield/nt-earn) ranks eligible markets by interest rates after checking borrowing demand, available capacity and withdrawal risks.\n\nIt moves money when the expected interest advantage covers the transaction cost. The software also reduces positions when too much of a market's money is lent out, leaving less available for withdrawals.\n\nBorrowers failing to repay or faults in the platforms' software can cause losses. Withdrawals can be delayed if too much money remains tied up in loans."),
        source_url=_DOCS + "/usdc-yield/nt-earn",
    ),
    "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ": NativeVaultMetadata(
        name="Neutral Autopilot",
        curator_slug="neutral-trade",
        short_description="The vault spreads deposits across trading strategies that mostly use offsetting positions to limit exposure to rising or falling markets.",
        description=(
            "#### Vault's trading strategy\n\n"
            "The vault divides deposits among trading strategies, with most money in positions designed to reduce dependence on market direction. Neutral Trade's [allocation software](https://docs.neutral.trade/for-capital-allocators/neutral-autopilot) weighs expected returns, risks and how the strategies perform together, then moves money when allocations drift materially from their targets.\n\n"
            "The underlying strategies earn from differences between prices, payments between futures traders, buy and sell orders, and selling [options](https://tradingstrategy.ai/glossary/options) that give buyers the right to trade at an agreed price. **Up to 5% may follow market direction**, taking positions that depend on prices rising or falling.\n\n"
            "Trading venues and asset custody depend on the underlying vaults and change with the allocation. Their individual strategy descriptions identify the known venues; this vault does not have one fixed set of trading venues.\n\n"
            "Spreading money across strategies can reduce dependence on any one of them. It does not prevent losses or guarantee access to money held by exchanges and custodians."
        ),
        source_url=_DOCS + "/neutral-autopilot",
    ),
    "nE1x7KQq2sm3GQrafQUUdBkSPPT52FmiMM9qAS1dgnC": NativeVaultMetadata(
        name="Hyperliquid Funding Arb",
        curator_slug="neutral-trade",
        short_description="The vault pairs crypto holdings with offsetting price contracts on Hyperliquid to collect recurring payments from other traders.",
        description=(
            "#### Vault's trading strategy\n\n"
            "The vault buys crypto assets on [Hyperliquid](https://hyperliquid.xyz/) and opens matching positions in [perpetual futures](https://tradingstrategy.ai/glossary/perpetual-future), price contracts without an expiry date, that gain when those assets fall. This pairing aims to offset price changes while collecting [funding payments](https://tradingstrategy.ai/glossary/funding-rate), recurring payments received when traders betting on rising prices pay those on the other side.\n\n"
            "Neutral Trade's [automated strategy](https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/hyperliquid-funding-arb) adjusts the paired positions as prices and holdings change. It also invests part of the money in Hyperliquid's HLP vault, which earns from offering buy and sell prices, closing traders' positions when their backing funds fall short, and platform fees.\n\n"
            "Funding payments can reverse, turning income into a cost. Both the paired trades and the HLP investment can lose money."
        ),
        source_url=_MARKET + "/hyperliquid-funding-arb",
    ),
}
