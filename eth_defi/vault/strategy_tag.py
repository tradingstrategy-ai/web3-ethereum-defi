"""Vault investment strategy classifications.

Protocol-local ``STRATEGY_TAGS`` tables use plain lowercase ``str`` keys for
EVM addresses, for example ``"0x1234..."``.  Keeping address literals as
strings avoids noisy ``HexAddress(...)`` constructors while allowing the same
mapping convention to be used for native protocol identifiers.  The lookup
helper normalises adapter-supplied addresses before consulting these tables.
"""

import enum
from collections.abc import Collection, Mapping
from typing import TypedDict


class StrategyTag(str, enum.Enum):
    """Classify the investment strategies used by a vault.

    A vault may have more than one strategy tag. Tags describe its economic
    strategy, instrument, execution venue, or asset exposure rather than its
    smart-contract implementation, risk level, or current trading state.
    String values are stable identifiers suitable for persisted vault metadata
    and public data exports.
    """

    #: Research established that the vault's strategy is unknown.
    #: Example vault: none currently mapped.
    unknown = "unknown"

    #: Takes directional exposure to one or more markets.
    #: Example vault: AllDeFi Quant Directional Strategy (GRVT).
    directional_trading = "directional_trading"

    #: Maintains a fixed long or short exposure to one underlying asset using
    #: leverage. This is direct market exposure, not a trading signal,
    #: selection method, or other trade intelligence.
    #: Example vault: ADA 2x long (Lighter).
    directional_leverage = "directional_leverage"

    #: Takes directional positions that follow sustained market movements.
    #: Example vault: Gen Wealth Algo (Hyperliquid).
    trend_following = "trend_following"

    #: Uses trader judgement to select and manage positions.
    #: Example vault: Rogue Trader Discretionary (GRVT).
    discretionary_trading = "discretionary_trading"

    #: Uses programmed rules to select, execute, or manage positions.
    #: Example vault: Silentist BALANCE (GRVT).
    algorithmic_trading = "algorithmic_trading"

    #: Captures price differences between markets or related instruments.
    #: Example vault: Axis Origin (Upshift).
    arbitrage = "arbitrage"

    #: Seeks to offset market exposure while capturing non-directional returns.
    #: Example vault: Grvt Liquidity Provider (GLP).
    delta_neutral = "delta_neutral"

    #: Trades quantitatively identified pricing patterns.
    #: Example vault: SOL/BTC Neutral (Hyperliquid).
    statistical_arbitrage = "statistical_arbitrage"

    #: Trades against price moves expected to return towards an average.
    #: Example vault: Growi HF (Hyperliquid).
    mean_reversion = "mean_reversion"

    #: Places orders at predefined price intervals to capture market ranges.
    #: Example vault: AI Grid Trading Strategy (GRVT).
    grid_trading = "grid_trading"

    #: Trades the relative price of two related assets.
    #: Example vault: pmalt (Hyperliquid).
    pair_trading = "pair_trading"

    #: Captures funding-rate differences, commonly between perpetual futures markets.
    #: Example vault: Extended and Nado arbitrage (Atoma).
    funding_rate_arbitrage = "funding_rate_arbitrage"

    #: Supplies assets to a lending market to earn interest.
    #: Example vault: Morpho Gauntlet USDC Prime (Morpho).
    lending = "lending"

    #: Allocates supplied assets between lending opportunities to improve yield.
    #: Example vault: TAU Base USDC LO (IPOR Fusion).
    lending_optimisation = "lending_optimisation"

    #: Borrows against supplied collateral to recursively increase lending exposure.
    #: Example vault: Prime HELOC Loop (IPOR Fusion).
    lending_looping = "lending_looping"

    #: Provides executable liquidity to buyers and sellers.
    #: Example vault: KPK USDC LiquidLane (Symbiotic).
    market_making = "market_making"

    #: Supplies capital to a venue or protocol as a liquidity provider.
    #: Example vault: Hyperliquidity Provider (HLP).
    liquidity_provider = "liquidity_provider"

    #: Operates a strategy that quotes or supplies both sides of a market.
    #: Example vault: Grvt Liquidity Provider (GLP).
    market_maker = "market_maker"

    #: Actively makes markets by supplying liquidity through an automated
    #: market maker.
    #: Example vault: gTrade (Gains Network USDC).
    market_making_amm = "market_making_amm"

    #: Provides liquidity through a central limit order book.
    #: Example vault: Pharos Market Maker (PMM) (GRVT).
    market_making_clob = "market_making_clob"

    #: Combines multiple distinct investment strategies.
    #: Example vault: Axis Origin USDx (Upshift).
    multistrategy = "multistrategy"

    #: Tracks a predefined market or asset index in an index-fund style.
    #: Example vault: Mantle Index Four (Securitize).
    index = "index"

    #: Trades perpetual futures contracts.
    #: Example vault: Grvt Liquidity Provider (GLP).
    perpetual_futures = "perpetual_futures"

    #: Uses an automated market maker as the liquidity venue, regardless of
    #: whether the vault itself runs a market-making strategy.
    #: Example vault: gTrade (Gains Network USDC).
    amm = "amm"

    #: Invests in real-world assets.
    #: Example vault: ynRWAx (YieldNest).
    rwa = "rwa"

    #: Provides credit backed by real-world assets.
    #: Example vault: Balanced Bundle (GRVT).
    rwa_credit = "rwa_credit"

    #: Lends against real-world assets.
    #: Example vault: ynRWAx (YieldNest).
    rwa_lending = "rwa_lending"

    #: Invests in real-world royalty streams.
    #: Example vault: ALAR SailOut Royalty (Liquid Royalty).
    rwa_royalties = "rwa_royalties"

    #: Invests in money-market instruments.
    #: Example vault: Spiko EU T-Bills Money Market Fund (Spiko).
    money_market_fund = "money_market_fund"

    #: Invests in early-stage companies or projects in a venture-capital style.
    #: Example vault: BCAP (Securitize).
    venture_funding = "venture_funding"

    #: Seeks return from the carry of an asset or position.
    #: Example vault: Staked USDe (Ethena).
    carry_trade = "carry_trade"


class StrategyTagMetadata(TypedDict):
    """Human-readable presentation metadata for one strategy category."""

    #: Human-readable category label without Markdown links.
    label: str

    #: Exactly two sentences explaining the evidence-based tagging rule.
    description: str


#: Public presentation labels and category rules for every strategy tag.
#:
#: Descriptions deliberately contain exactly two sentences so API consumers can
#: display them consistently without having to truncate prose.
STRATEGY_TAG_METADATA: dict[StrategyTag, StrategyTagMetadata] = {
    StrategyTag.unknown: {
        "label": "Unknown strategy",
        "description": "We use this tag only when research has explicitly established that the vault's strategy is unknown. It does not mean that an unresearched vault has been classified.",
    },
    StrategyTag.directional_trading: {
        "label": "Directional trading",
        "description": "The vault takes [directional exposure](https://tradingstrategy.ai/glossary/directional-strategy) to one or more markets and can benefit or lose from their direction. We apply this tag when the documented strategy intentionally expresses such a market view.",
    },
    StrategyTag.directional_leverage: {
        "label": "Directional leverage",
        "description": "The vault maintains a fixed leveraged long or short position in an underlying asset. We apply this tag when documented leverage creates direct directional exposure rather than reflecting a trading signal or execution method.",
    },
    StrategyTag.trend_following: {
        "label": "Trend following",
        "description": "The vault takes positions designed to follow sustained [market trends](https://tradingstrategy.ai/glossary/trend-following). We apply this tag when trend-following is documented as part of the investment process.",
    },
    StrategyTag.discretionary_trading: {
        "label": "Discretionary trading",
        "description": "The vault uses a manager's [judgement](https://tradingstrategy.ai/glossary/discretionary-investment-management) to select or manage positions. We apply this tag when the strategy documentation identifies discretionary decision-making.",
    },
    StrategyTag.algorithmic_trading: {
        "label": "Algorithmic trading",
        "description": "The vault uses [programmed rules](https://tradingstrategy.ai/glossary/algorithmic-trading) to select, execute, or manage positions. We apply this tag when automation is a documented part of the strategy rather than an implementation detail alone.",
    },
    StrategyTag.arbitrage: {
        "label": "Arbitrage",
        "description": "The vault seeks to capture price differences between markets or related instruments. We apply this tag when the manager or protocol documents arbitrage as a return source.",
    },
    StrategyTag.delta_neutral: {
        "label": "Delta neutral",
        "description": "The vault seeks to offset [directional market exposure](https://tradingstrategy.ai/glossary/delta-neutral) while earning non-directional returns. We apply this tag when the strategy documents a delta-neutral or market-neutral construction.",
    },
    StrategyTag.statistical_arbitrage: {
        "label": "Statistical arbitrage",
        "description": "The vault trades quantitatively identified [pricing patterns](https://tradingstrategy.ai/glossary/statistical-arbitrage) or statistical relationships. We apply this tag when statistical arbitrage is documented rather than inferred from quantitative branding.",
    },
    StrategyTag.mean_reversion: {
        "label": "Mean reversion",
        "description": "The vault trades [price moves](https://tradingstrategy.ai/glossary/mean-reversion) expected to return towards an average or equilibrium. We apply this tag when mean reversion is explicitly described by the strategy.",
    },
    StrategyTag.grid_trading: {
        "label": "Grid trading",
        "description": "The vault places orders at predefined price intervals to trade a market range. We apply this tag when the manager documents a grid-based execution strategy.",
    },
    StrategyTag.pair_trading: {
        "label": "Pair trading",
        "description": "The vault trades the relative price of two related assets rather than a single asset's direction. We apply this tag when the strategy documents paired long and short exposure.",
    },
    StrategyTag.funding_rate_arbitrage: {
        "label": "Funding-rate arbitrage",
        "description": "The vault seeks to capture [funding-rate differences](https://tradingstrategy.ai/glossary/funding-rate), commonly across perpetual futures markets. We apply this tag when funding payments are a documented return source.",
    },
    StrategyTag.lending: {
        "label": "Lending",
        "description": "The vault supplies assets to a [lending market](https://tradingstrategy.ai/glossary/lending-protocol) to earn interest. We apply this tag to documented lending strategies, including protocol-wide defaults where every supported vault is a lender.",
    },
    StrategyTag.lending_optimisation: {
        "label": "Lending optimisation",
        "description": "The vault allocates supplied assets between lending opportunities to improve yield. We apply this tag when active lending allocation or optimisation is documented.",
    },
    StrategyTag.lending_looping: {
        "label": "Lending looping",
        "description": "The vault borrows against supplied collateral to [recursively increase lending exposure](https://tradingstrategy.ai/glossary/recursive-looping). We apply this tag when the documented strategy uses a lending loop rather than simple lending.",
    },
    StrategyTag.market_making: {
        "label": "Market making",
        "description": "The vault provides [executable liquidity](https://tradingstrategy.ai/glossary/market-making) to buyers and sellers. We apply this tag when the vault's documented strategy is to make markets rather than merely invest in a liquidity pool.",
    },
    StrategyTag.liquidity_provider: {
        "label": "Liquidity provider",
        "description": "The vault supplies capital to a venue or protocol as a [liquidity provider](https://tradingstrategy.ai/glossary/liquidity-provider). We apply this tag when the documented role is liquidity provision, including passive provision without active quoting.",
    },
    StrategyTag.market_maker: {
        "label": "Market maker",
        "description": "The vault operates a strategy that quotes or supplies both sides of a market. We apply this tag when the documented activity is active market making.",
    },
    StrategyTag.market_making_amm: {
        "label": "AMM market making",
        "description": "The vault actively makes markets by supplying liquidity through an automated market maker. We apply this tag when the strategy itself performs market making on an AMM.",
    },
    StrategyTag.market_making_clob: {
        "label": "CLOB market making",
        "description": "The vault provides liquidity through a central limit order book. We apply this tag when the strategy documents order-book market making.",
    },
    StrategyTag.multistrategy: {
        "label": "Multi-strategy",
        "description": "The vault combines multiple distinct investment strategies. We apply this tag only when the manager documents more than one strategy as part of the mandate.",
    },
    StrategyTag.index: {
        "label": "Index",
        "description": "The vault tracks a predefined market or asset index in an index-fund style. We apply this tag when the documented objective is index exposure or replication.",
    },
    StrategyTag.perpetual_futures: {
        "label": "Perpetual futures",
        "description": "The vault trades [perpetual futures contracts](https://tradingstrategy.ai/glossary/perpetual-future). We apply this tag to documented perpetual-futures strategies and native perpetual-DEX defaults unless an explicit exception is maintained.",
    },
    StrategyTag.amm: {
        "label": "Automated market maker",
        "description": "The vault uses an [automated market maker](https://tradingstrategy.ai/glossary/amm) as its liquidity venue. We apply this tag for AMM venue exposure even when the vault is not itself an active market-making strategy.",
    },
    StrategyTag.rwa: {
        "label": "Real-world assets",
        "description": "The vault invests in [real-world assets](https://tradingstrategy.ai/glossary/rwa) or their tokenised representations. We apply this tag when the underlying exposure is documented as real-world assets.",
    },
    StrategyTag.rwa_credit: {
        "label": "RWA credit",
        "description": "The vault provides credit backed by real-world assets. We apply this tag when the strategy documents real-world asset collateral or credit exposure.",
    },
    StrategyTag.rwa_lending: {
        "label": "RWA lending",
        "description": "The vault lends against real-world assets. We apply this tag when the documented lending strategy has real-world asset exposure.",
    },
    StrategyTag.rwa_royalties: {
        "label": "RWA royalties",
        "description": "The vault invests in real-world royalty streams. We apply this tag when the documented assets are contractual royalty payments.",
    },
    StrategyTag.money_market_fund: {
        "label": "Money-market fund",
        "description": "The vault invests in money-market instruments. We apply this tag when the documented mandate is a money-market fund or equivalent cash-management product.",
    },
    StrategyTag.venture_funding: {
        "label": "Venture funding",
        "description": "The vault invests in early-stage companies or projects in a venture-capital style. We apply this tag when the documented mandate is venture funding rather than public-market exposure.",
    },
    StrategyTag.carry_trade: {
        "label": "Carry trade",
        "description": "The vault seeks return from the [carry](https://tradingstrategy.ai/glossary/carry-trade) of an asset or position. We apply this tag when carry is a documented return source, including cash-and-carry structures.",
    },
}


def lookup_strategy_tags(
    mapping: Mapping[str, Collection[StrategyTag]],
    address: str,
) -> set[StrategyTag] | None:
    """Look up a copy of the maintained tags for one EVM vault address.

    Maintained EVM mapping keys are plain lowercase strings rather than
    ``HexAddress`` constructor calls. Address inputs are normalised to
    lowercase before lookup. Returning a fresh set prevents scan consumers
    from mutating the protocol's source mapping; an absent key remains
    ``None`` so missing information is not confused with a deliberately empty
    classification.

    Mappings are intentionally keyed by address without a chain ID. An entry
    applies to every supported deployment at that address, so maintainers must
    confirm that same-address deployments use the same strategy before adding
    one. A genuinely chain-specific classification belongs in the protocol
    adapter's hook instead of this shared address mapping.

    :param mapping:
        Protocol-local lowercase string address-to-tag mapping.
    :param address:
        EVM vault address to look up.
    :return:
        A mutable copy of the maintained tags, or ``None`` when no entry exists.
    """
    tags = mapping.get(address.lower())
    return set(tags) if tags is not None else None


def combine_strategy_tags(
    defaults: Collection[StrategyTag],
    mapping: Mapping[str, Collection[StrategyTag]],
    address: str,
) -> set[StrategyTag]:
    """Combine default and address-specific tags for a native vault export.

    Native integrations use protocol-specific identifiers, which may be
    EVM-style addresses or synthetic IDs. The helper normalises those
    identifiers and always returns a fresh set so callers cannot mutate either
    source collection.

    :param defaults:
        Tags that apply to every vault in the native integration.
    :param mapping:
        Lowercase identifier-to-tag mapping.
    :param address:
        Native vault identifier.
    :return:
        Combined mutable tag set.
    """
    return set(defaults) | set(mapping.get(address.lower(), ()))
