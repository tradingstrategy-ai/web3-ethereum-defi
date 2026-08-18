"""Vault investment strategy classifications."""

import enum


class StrategyTag(str, enum.Enum):
    """Classify the investment strategies used by a vault.

    A vault may have more than one strategy tag.  Tags describe its economic
    strategy rather than its smart-contract implementation, risk level, or
    current trading state.  String values are stable identifiers suitable for
    persisted vault metadata and public data exports.
    """

    #: The vault strategy has not yet been classified.
    #: Example vault: none currently mapped.
    unknown = "unknown"

    #: Takes directional exposure to one or more markets.
    #: Example vault: AllDeFi Quant Directional Strategy (GRVT).
    directional_trading = "directional_trading"

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
    #: Example vault: none currently mapped.
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

    #: Provides liquidity through an automated market maker.
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

    #: Provides liquidity through an automated market maker.
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
    #: Example vault: none currently mapped.
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
