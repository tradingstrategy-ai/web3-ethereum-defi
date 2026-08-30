"""Vault investment strategy classifications.

Protocol-local ``STRATEGY_TAGS`` tables use plain lowercase ``str`` keys for
EVM addresses, for example ``"0x1234..."``.  Keeping address literals as
strings avoids noisy ``HexAddress(...)`` constructors while allowing the same
mapping convention to be used for native protocol identifiers.  The lookup
helper normalises adapter-supplied addresses before consulting these tables.
"""

import enum
from collections.abc import Collection, Mapping


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
