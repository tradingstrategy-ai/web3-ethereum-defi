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
        short_description="The vault sells short-dated Bitcoin options and hedges price exposure to earn the difference between implied and realised volatility.",
        description=("Xanthas runs a [systematic Bitcoin options strategy](" + _MARKET + "/options-market-making). It sells short-dated [options](https://tradingstrategy.ai/glossary/options) on [OKX](https://www.okx.com/) when their implied volatility exceeds its three-day forecast of realised volatility, and hedges price exposure with perpetual swaps.\n\nTrading runs automatically within preset limits, with off-exchange custody through [Copper](https://copper.co/). **Sharp price moves or volatility changes can cause losses** even when directional exposure is hedged."),
        source_url=_MARKET + "/options-market-making",
    ),
    "46gSEJLNTdDFq7DPjH8RbcTmG1gyw4JCKCr1XfuAfYvh": NativeVaultMetadata(
        name="Mid-frequency Market Making",
        curator_slug="adverseguard-alpha",
        short_description="The vault quotes both sides of Binance's ether perpetual futures market to earn trading spreads and maker rebates.",
        description=("AdverseGuard Alpha's [automated quoting strategy](" + _MARKET + "/mid-frequency-market-making) places buy and sell orders in the ETHUSDT perpetual futures market on [Binance](https://www.binance.com/). A fair-price model adjusts quotes continuously, seeking the difference between buying and selling prices and exchange rebates for providing liquidity.\n\nThe system reduces or pauses quoting when signals indicate an adverse price move. Losses can arise when prices move against filled orders or trading systems fail."),
        source_url=_MARKET + "/mid-frequency-market-making",
    ),
    "4QBTzqUbn1crLg2PDUGt8abJfqHjeuZkjNLjCCit69ui": NativeVaultMetadata(
        name="Multi-Asset Volatility Arbitrage",
        curator_slug="d-research",
        short_description="The vault trades volatility differences across listed equity, fixed-income and volatility markets.",
        description=("D Research's [systematic volatility strategy](" + _DOCS + "/usdc-yield/rwa/multi-asset-volatility-arbitrage) trades liquid equity, fixed-income and volatility instruments. It seeks differences in volatility pricing, including exposure to digital assets through listed securities; the listed venues are [Nasdaq](https://www.nasdaq.com/), [NYSE](https://www.nyse.com/) and [CME](https://www.cmegroup.com/).\n\nPortfolio limits, stress tests and daily position adjustments control exposure. **Redemptions are quarterly**, require advance notice and are subject to a gate, so money may remain invested during market stress."),
        source_url=_DOCS + "/usdc-yield/rwa/multi-asset-volatility-arbitrage",
    ),
    "Aea2VZfX6hw758cxx2WaGm7kqmdVjhbYsLfde5chVG1W": NativeVaultMetadata(
        name="CTA - Momentum LongShort",
        curator_slug="adverseguard-alpha",
        short_description="The vault trades a basket of crypto assets on Binance using systematic long-short momentum signals and hourly rebalancing.",
        description=("AdverseGuard Alpha runs a [directional momentum strategy](" + _DIRECTIONAL + "/cta-momentum-longshort) on [Binance](https://www.binance.com/). Software combines several quantitative signals to buy or sell liquid crypto assets and rebalances the portfolio hourly.\n\nExecution algorithms adjust order timing and size to liquidity, while position limits constrain exposure. The strategy can lose money when trends reverse or its signals fail."),
        source_url=_DIRECTIONAL + "/cta-momentum-longshort",
    ),
    "C68A4mAhA9EE4rWq9HmFnq3SPcbNmst6qiBWcna5VDHy": NativeVaultMetadata(
        name="HFT Multi-factor",
        curator_slug="vision-research",
        short_description="The vault combines high-frequency trading signals with market making in liquid crypto perpetual futures on Binance.",
        description=("Vision Research's [automated trading models](" + _MARKET + "/hft-multi-factor) combine short-horizon price signals with market making on [Binance](https://www.binance.com/). The strategy trades liquid crypto assets and perpetual futures, placing most orders as maker orders to earn trading spreads and exchange rebates.\n\nIts models combine momentum and mean-reversion signals, which trade expected continuations or reversals in prices. Leverage, model errors and adverse price moves can cause losses."),
        source_url=_MARKET + "/hft-multi-factor",
    ),
    "De47QBuMP7xukYBek5r4ScZF4HEk1kHW34ymKASz3DLt": NativeVaultMetadata(
        name="Delta Neutral Arbitrage",
        curator_slug="velox-trading-group",
        short_description="The vault trades exchange price differences, funding spreads and market-making opportunities across centralised and decentralised venues.",
        description=("Velox Trading Group's [arbitrage strategy](" + _MARKET + "/delta-neutral-arbitrage) combines offsetting trades across venues, funding and futures-price differences, and DeFi market making. Its venues are [Binance](https://www.binance.com/), [OKX](https://www.okx.com/), [Bybit](https://www.bybit.com/), [Gate](https://www.gate.com/), [Hyperliquid](https://hyperliquid.xyz/) and [Lighter](https://lighter.xyz/).\n\nCentralised exchange positions use off-exchange custody through [Copper](https://copper.co/) and [Ceffu](https://www.ceffu.com/). Hedging reduces directional exposure, but leverage, counterparties and failures in trading or DeFi systems can still cause losses."),
        source_url=_MARKET + "/delta-neutral-arbitrage",
    ),
    "DzmixPGgru86YkQY4LSEuM1Sfz7ZFPc1wHwSm4fejXbJ": NativeVaultMetadata(
        name="HFT Market Making",
        curator_slug="adverseguard-alpha",
        short_description="The vault uses rapid order-book signals to quote Binance's ether perpetual futures market and earn trading spreads and rebates.",
        description=("AdverseGuard Alpha's [high-frequency quoting system](" + _MARKET + "/hft-market-making) trades ETHUSDT perpetual futures on [Binance](https://www.binance.com/). It reads changes in orders and trading queues, updates quotes many times a second and keeps inventory small.\n\nIncome comes from trading spreads and maker rebates, with custody through [Ceffu](https://www.ceffu.com/). The strategy can lose money during abrupt price moves, connectivity failures or changes to exchange rebates."),
        source_url=_MARKET + "/hft-market-making",
    ),
    "Ec6uSz5EsffwfXp43pvpz4rf9ttJRFKES4ZSVX7gVVkQ": NativeVaultMetadata(
        name="CTA - Systematic Alpha",
        curator_slug="hyperithm",
        short_description="The vault takes long-only US equity exposure using systematic trend-following and mean-reversion models.",
        description=("Hyperithm's [long-only systematic strategy](" + _DIRECTIONAL + "/systematic-alpha) combines models that follow price trends with models that trade expected reversals towards an average. Positions typically last three to five days, and software adjusts invested capital to signal strength.\n\nThe strategy documentation lists [Nasdaq](https://www.nasdaq.com/) and [NYSE](https://www.nyse.com/) for US equity exposure, and [Binance](https://www.binance.com/) without specifying its instruments there. The portfolio remains exposed to falling equity prices even when it holds part of its capital in cash."),
        source_url=_DIRECTIONAL + "/systematic-alpha",
    ),
    "Eo3m78EQcHnwMyHtxaiz1vw4nwHa85RuGE8jLsrELhxj": NativeVaultMetadata(
        name="CTA - Adaptive Alpha",
        curator_slug="atlas-research",
        short_description="The vault trades short-term crypto derivatives signals, switching between trend-following and mean-reversion positions.",
        description=("Atlas Research's [automated directional strategy](" + _DIRECTIONAL + "/cta-adaptive-alpha) takes long and short positions in liquid crypto derivatives. It monitors price and order-flow signals continuously, moving between trend-following and expected price-reversal trades as conditions change.\n\nPrice-based and time-based rules close positions and reduce activity when signals weaken. The strategy's execution venues are not named in its documentation, and directional exposure can produce losses when signals fail."),
        source_url=_DIRECTIONAL + "/cta-adaptive-alpha",
    ),
    "G5aMxQTbGWMnYycpfjHpD7Y1muoKBwaB1HtCdpUUcQZp": NativeVaultMetadata(
        name="CTA - Longshort Alpha",
        curator_slug="c-research",
        short_description="The vault combines systematic long-short crypto perpetual futures positions, funding collection and mean-reversion signals.",
        description=("C Research's [systematic long-short strategy](" + _DIRECTIONAL + "/cta-longshort-alpha) trades crypto perpetual futures primarily on [Binance](https://www.binance.com/). Its models combine intraday momentum, funding payments and expected price reversals, using offsetting positions across a liquid trading universe.\n\nTrading runs continuously with position limits and risk controls. Losses can result when crowded positions unwind, funding changes or the models misread a market move."),
        source_url=_DIRECTIONAL + "/cta-longshort-alpha",
    ),
    "HWjMfYfc7KoPchoEDa5UFyVSpJxY3aV1RAccKvABYb9z": NativeVaultMetadata(
        name="NT Earn",
        curator_slug="neutral-trade",
        short_description="The vault automatically allocates deposits across Solana lending markets to earn borrower interest.",
        description=("Neutral Trade's [automated lending allocator](" + _DOCS + "/usdc-yield/nt-earn) distributes deposits across Solana money markets, including [Kamino](https://kamino.com/) and [Jupiter Lend](https://jup.ag/lend). It ranks eligible markets by interest rates, borrowing demand and available capacity, then rebalances when rate differences justify the transaction cost.\n\nUtilisation checks reduce positions when markets become harder to exit. Borrower losses, smart contract failures or high utilisation can still cause losses or delay withdrawals."),
        source_url=_DOCS + "/usdc-yield/nt-earn",
    ),
    "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ": NativeVaultMetadata(
        name="Neutral Autopilot",
        curator_slug="neutral-trade",
        short_description="The vault allocates across several trading strategies with a market-neutral core and a limited directional allocation.",
        description=("Neutral Trade's [multi-strategy allocator](" + _DOCS + "/neutral-autopilot) spreads deposits across its trading vaults and rebalances as their risk and return characteristics change. The core uses hedged funding, arbitrage, market-making and options strategies, with up to 5% permitted in directional strategies.\n\nExecution and custody depend on each underlying strategy, spanning centralised and decentralised venues. Diversification does not remove losses or counterparty risk in those vaults, and withdrawals follow the weekly redemption cycle."),
        source_url=_DOCS + "/neutral-autopilot",
    ),
    "nE1x7KQq2sm3GQrafQUUdBkSPPT52FmiMM9qAS1dgnC": NativeVaultMetadata(
        name="Hyperliquid Funding Arb",
        curator_slug="neutral-trade",
        short_description="The vault holds offsetting spot and perpetual futures positions on Hyperliquid to collect funding payments.",
        description=("Neutral Trade's [funding strategy](" + _MARKET + "/hyperliquid-funding-arb) buys spot assets and sells matching perpetual futures on [Hyperliquid](https://hyperliquid.xyz/). Software rebalances the offsetting positions to limit directional exposure and receive funding when leveraged traders pay shorts.\n\nA separate allocation to Hyperliquid's HLP vault earns market-making and liquidation income. Funding can turn negative, and both the hedged positions and HLP allocation can lose money."),
        source_url=_MARKET + "/hyperliquid-funding-arb",
    ),
}
