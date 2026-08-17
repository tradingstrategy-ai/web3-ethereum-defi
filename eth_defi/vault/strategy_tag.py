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
    unknown = "unknown"

    #: Takes directional exposure to one or more markets.
    directional_trading = "directional_trading"

    #: Seeks to offset market exposure while capturing non-directional returns.
    delta_neutral = "delta_neutral"

    #: Trades quantitatively identified pricing patterns.
    statistical_arbitrage = "statistical_arbitrage"

    #: Trades the relative price of two related assets.
    pair_trading = "pair_trading"

    #: Captures funding-rate differences, commonly between perpetual futures markets.
    funding_rate_arbitrage = "funding_rate_arbitrage"

    #: Allocates supplied assets between lending opportunities to improve yield.
    lending_optimisation = "lending_optimisation"

    #: Borrows against supplied collateral to recursively increase lending exposure.
    lending_looping = "lending_looping"

    #: Provides executable liquidity to buyers and sellers.
    market_making = "market_making"

    #: Provides liquidity through an automated market maker.
    market_making_amm = "market_making_amm"

    #: Provides liquidity through a central limit order book.
    market_making_clob = "market_making_clob"

    #: Combines multiple distinct investment strategies.
    multistrategy = "multistrategy"

    #: Trades perpetual futures contracts.
    perpetual_futures = "perpetual_futures"

    #: Provides liquidity through an automated market maker.
    amm = "amm"

    #: Invests in real-world assets.
    rwa = "rwa"

    #: Provides credit backed by real-world assets.
    rwa_credit = "rwa_credit"

    #: Lends against real-world assets.
    rwa_lending = "rwa_lending"

    #: Invests in real-world royalty streams.
    rwa_royalties = "rwa_royalties"

    #: Invests in money-market instruments.
    money_market_fund = "money_market_fund"

    #: Seeks return from the carry of an asset or position.
    carry_trade = "carry_trade"
