.. _gmx:

GMX API
-------

This module contains `GMX <https://gmx.io/>`__ perpetual futures protocol support for Python.

GMX is a decentralised perpetual futures exchange on Arbitrum.
This module provides tools for interacting with GMX, including:

- Opening and closing leveraged positions (market, limit, stop-loss, take-profit)
- `CCXT <https://docs.ccxt.com/>`__-compatible adapter for trading bot integration
- `Freqtrade <https://www.freqtrade.io/>`__ exchange integration via monkeypatch
- Token swaps using GMX liquidity pools
- Reading current and historical market data, funding rates, and open interest
- GraphQL-based data access via Subsquid indexer
- Onchain data retrieval (volume, events)

Tutorials and examples
======================

- :ref:`gmx-swap` -- token swap tutorial
- `Complete Freqtrade trading bot example <https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade>`__ -- live trading with GMX and Freqtrade
- `GMX historical data collector <https://github.com/tradingstrategy-ai/gmx-data-collector>`__ -- historical OHLCV and market data tool

Trading with core modules
=========================

You can interact with GMX directly using the core Python modules. These provide low-level
access to GMX smart contracts for opening and closing leveraged positions, reading market data,
and executing token swaps — without requiring any external trading framework.

The CCXT adapter builds on top of these core modules and exposes them through the standard
`CCXT <https://docs.ccxt.com/>`__ exchange interface, making the GMX integration compatible
with any CCXT-based trading bot.

Because the adapter is CCXT-compatible, you can deploy pre-existing
`Freqtrade <https://www.freqtrade.io/>`__ strategies on GMX without modifying
strategy code. A monkeypatch injects GMX as a supported exchange into Freqtrade
without requiring changes to Freqtrade or CCXT source. Use the ``freqtrade-gmx``
wrapper script to start Freqtrade with GMX support enabled.

Key features:

- USD-based position sizing with ``size_usd`` parameter
- Configurable leverage (1.0x to 100x per market)
- Percentage-based stop-loss and take-profit triggers
- Historical OHLCV data for backtesting (up to 10,000 candles)
- Supports Arbitrum One (mainnet) and Arbitrum Sepolia (testnet)

See the `gmx-ccxt-freqtrade repository <https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade>`__
for complete setup instructions, backtesting guides, and a working strategy example.

Supported CCXT and Freqtrade features
======================================

The following tables list which standard CCXT and Freqtrade features are supported
by the GMX adapter.

**Market data**

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - Method
     - Supported
     - Notes
   * - ``fetch_markets``
     - Yes
     -
   * - ``fetch_ticker`` / ``fetch_tickers``
     - Yes
     -
   * - ``fetch_ohlcv``
     - Yes
     - 1m, 5m, 15m, 1h, 4h, 1d; up to 10,000 candles
   * - ``fetch_trades``
     - Yes
     - Derived from position events
   * - ``fetch_currencies``
     - Yes
     -
   * - ``fetch_order_book``
     - No
     - GMX uses liquidity pools, not order books
   * - ``fetch_time`` / ``fetch_status``
     - Yes
     -

**Account and positions**

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - Method
     - Supported
     - Notes
   * - ``fetch_balance``
     - Yes
     -
   * - ``fetch_positions``
     - Yes
     - Includes PnL, leverage, liquidation price
   * - ``fetch_my_trades``
     - Yes
     -
   * - ``fetch_open_orders``
     - Yes
     - Returns open positions as order-like structures
   * - ``fetch_orders`` / ``fetch_closed_orders``
     - No
     - Use ``fetch_positions`` and ``fetch_my_trades``

**Order management**

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - Method
     - Supported
     - Notes
   * - ``create_order``
     - Yes
     - Market, limit, stop-loss, take-profit, bundled SL/TP
   * - ``create_market_buy_order`` / ``create_market_sell_order``
     - Yes
     -
   * - ``create_limit_order``
     - Yes
     - Behaves as a trigger order
   * - ``cancel_order``
     - No
     - Orders execute immediately via keeper network
   * - ``edit_order``
     - No
     - Orders are immutable once created
   * - ``add_margin`` / ``reduce_margin``
     - No
     - Not yet implemented

**Derivatives**

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - Method
     - Supported
     - Notes
   * - ``fetch_funding_rate`` / ``fetch_funding_rate_history``
     - Yes
     -
   * - ``fetch_open_interest`` / ``fetch_open_interest_history``
     - Yes
     -
   * - ``set_leverage`` / ``fetch_leverage``
     - Yes
     - 1.0x to 100x per market
   * - ``fetch_leverage_tiers``
     - Yes
     - Requires Subsquid
   * - ``set_margin_mode``
     - No
     - GMX uses isolated margin only

**Freqtrade integration**

.. list-table::
   :header-rows: 1
   :widths: 40 15 45

   * - Feature
     - Supported
     - Notes
   * - Futures trading mode
     - Yes
     -
   * - Stoploss on exchange
     - Yes
     - Bundled SL/TP in a single transaction
   * - Historical OHLCV for backtesting
     - Yes
     - Up to 10,000 candles per request
   * - Spot trading
     - No
     - Only perpetual futures
   * - WebSocket streaming
     - No
     -
   * - Order modification
     - No
     - Orders are immutable

**Known limitations**

- OHLCV volume is always 0 (GMX API limitation)
- Ticker 24h high/low/open are calculated from OHLCV, not real-time
- Balance ``used`` field is not calculated (shown as 0.0)
- Testnet (Arbitrum Sepolia) uses RPC mode with slower market loading

What is GMX?
============

GMX is a `perpetual future <https://tradingstrategy.ai/glossary/perpetual%20future>`__ ("perp") `DEX <https://tradingstrategy.ai/glossary/DEX>`__ for `EVM <https://tradingstrategy.ai/glossary/EVM>`__ blockchains.

GMX offers dozens of perp trading pairs for popular cryptocurrencies like BTC, ETH and SOL. GMX is so-called pure `onchain <https://tradingstrategy.ai/glossary/onchain>`__ market with high degree of decentralisation. Thus, GMX has high `composability <https://tradingstrategy.ai/glossary/composability>`__ with other `decentralised finance <https://tradingstrategy.ai/glossary/decentralised%20finance>`__ `protocols <https://tradingstrategy.ai/glossary/protocols>`__. This allows users `longing <https://tradingstrategy.ai/glossary/longing>`__ and `shorting <https://tradingstrategy.ai/glossary/shorting>`__ different asset prices with `leverage <https://tradingstrategy.ai/glossary/leverage>`__ onchain.

GMX is one of the oldest pure onchain perpetual future market places still running. GMX mainly operates on Arbitrum, but has expanded to include cross-chain functionality.

GMX has its own pools for `market making <https://tradingstrategy.ai/glossary/market%20making>`__ where pools users can provide liquidity and take the other side of the trade. There are GLV vaults in GMX v2 and older GLP vaults in GMX v1. GLV is the index pool of GMX's markets, rebalancing liquidity to its best-performing GM pools and generating fees from them. As a result, GLV offers liquidity providers a balanced instrument with stable risk-adjusted returns and high capital efficiency.

There are multiple third-party DeFi vaults built on the top of GMX, like Umami's GM vaults following `ERC-4626 <https://tradingstrategy.ai/glossary/ERC-4626>`__ standard.

GMX price formation relies on its multi-asset liquidity pool (GLP) and a "virtual" `AMM <https://tradingstrategy.ai/glossary/AMM>`__ (vAMM) model that uses Chainlink oracles for price data instead of a traditional `order book <https://tradingstrategy.ai/glossary/order%20book>`__. When users trade, the vAMM calculates the price based on the ratio of assets in the GLP pool, and price feeds from Chainlink ensure accurate pricing for fixed-price trades.

Unlike standard AMMs, GMX's vAMM shifts risk to `liquidity providers <https://tradingstrategy.ai/glossary/liquidity%20providers>`__ (LPs) in the GLP pool, with traders paying a fee that is split between the GMX token holders and the GLP LPs.

More information
================

- `GMX documentation <https://docs.gmx.io/>`__
- `GMX Synthetics contracts <https://github.com/gmx-io/gmx-synthetics>`__
- `CCXT documentation <https://docs.ccxt.com/>`__
- `Freqtrade documentation <https://www.freqtrade.io/>`__

.. autosummary::
   :toctree: _autosummary_gmx
   :recursive:

   eth_defi.gmx.api
   eth_defi.gmx.base
   eth_defi.gmx.cache
   eth_defi.gmx.ccxt
   eth_defi.gmx.config
   eth_defi.gmx.constants
   eth_defi.gmx.contracts
   eth_defi.gmx.core
   eth_defi.gmx.data
   eth_defi.gmx.events
   eth_defi.gmx.freqtrade
   eth_defi.gmx.gas_monitor
   eth_defi.gmx.gas_utils
   eth_defi.gmx.graphql
   eth_defi.gmx.keys
   eth_defi.gmx.onchain
   eth_defi.gmx.order
   eth_defi.gmx.order_tracking
   eth_defi.gmx.price_sanity
   eth_defi.gmx.retry
   eth_defi.gmx.synthetic_tokens
   eth_defi.gmx.testing
   eth_defi.gmx.trading
   eth_defi.gmx.types
   eth_defi.gmx.utils
   eth_defi.gmx.verification
