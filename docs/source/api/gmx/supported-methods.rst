.. _gmx-supported-methods:

GMX supported CCXT and Freqtrade methods
-----------------------------------------

This page lists all standard `CCXT <https://docs.ccxt.com/>`__ exchange methods and
`Freqtrade <https://www.freqtrade.io/>`__ features, showing which ones are supported
by the GMX adapter. For comparison, see the
`Binance CCXT reference <https://docs.ccxt.com/exchanges/binance>`__.

The GMX adapter class lives in :py:mod:`eth_defi.gmx.ccxt` and extends the base
CCXT ``Exchange`` class. All supported methods work with both Arbitrum One (mainnet)
and Arbitrum Sepolia (testnet).

.. contents:: On this page
   :local:
   :depth: 2

Supported methods overview
==========================

The table below lists every standard CCXT method and its implementation status
in the GMX adapter.

- **Yes** -- fully implemented
- **Emulated** -- implemented using alternative data sources (e.g. OHLCV instead of real-time feed)
- **No** -- not supported; calling will raise ``NotSupported`` or ``NotImplementedError``

Market data
~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Method
     - Status
     - Notes
   * - ``fetch_markets``
     - Yes
     - REST API, GraphQL (Subsquid), or RPC modes
   * - ``fetch_ticker``
     - Yes
     -
   * - ``fetch_tickers``
     - Yes
     - Batch fetch for multiple symbols
   * - ``fetch_ohlcv``
     - Yes
     - 1m, 5m, 15m, 1h, 4h, 1d; up to 10,000 candles
   * - ``fetch_trades``
     - Emulated
     - Derived from position increase/decrease events
   * - ``fetch_currencies``
     - Yes
     - Token metadata (decimals, contract addresses)
   * - ``fetch_time``
     - Yes
     - Returns blockchain timestamp
   * - ``fetch_status``
     - Yes
     - API health check
   * - ``fetch_order_book``
     - No
     - GMX uses liquidity pools, not order books
   * - ``fetch_bids_asks``
     - No
     -
   * - ``fetch_last_prices``
     - No
     -
   * - ``fetch_mark_price``
     - No
     -

Trading
~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Method
     - Status
     - Notes
   * - ``create_order``
     - Yes
     - Market, limit, stop-loss, take-profit, bundled SL/TP
   * - ``create_market_buy_order``
     - Yes
     - Opens long position
   * - ``create_market_sell_order``
     - Yes
     - Opens short position
   * - ``create_limit_order``
     - Yes
     - Behaves as a trigger order on GMX
   * - ``create_market_order_with_cost``
     - No
     - Use ``size_usd`` parameter in ``create_order`` instead
   * - ``edit_order``
     - No
     - Orders are immutable once created
   * - ``cancel_order``
     - No
     - Orders execute immediately via keeper network or revert
   * - ``cancel_all_orders``
     - No
     -
   * - ``fetch_order``
     - Yes
     - Limited; polls keeper execution status
   * - ``fetch_orders``
     - No
     - Use ``fetch_positions`` and ``fetch_my_trades``
   * - ``fetch_open_orders``
     - Yes
     - Returns open positions as order-like structures
   * - ``fetch_closed_orders``
     - No
     - Use ``fetch_my_trades``
   * - ``fetch_canceled_orders``
     - No
     -
   * - ``fetch_order_trades``
     - No
     -

Account and balance
~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Method
     - Status
     - Notes
   * - ``fetch_balance``
     - Yes
     - Wallet token balances; ``used`` field shows 0.0
   * - ``fetch_my_trades``
     - Yes
     - User trade history
   * - ``fetch_positions``
     - Yes
     - Full position detail: PnL, leverage, liquidation price, collateral
   * - ``fetch_ledger``
     - No
     -
   * - ``fetch_trading_fee``
     - No
     -
   * - ``fetch_trading_fees``
     - No
     -
   * - ``calculate_fee``
     - Yes
     -

Derivatives
~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Method
     - Status
     - Notes
   * - ``fetch_funding_rate``
     - Yes
     -
   * - ``fetch_funding_rate_history``
     - Yes
     -
   * - ``fetch_funding_rates``
     - No
     -
   * - ``fetch_funding_history``
     - Yes
     - Returns empty list (GMX does not track historical funding per account)
   * - ``fetch_open_interest``
     - Yes
     -
   * - ``fetch_open_interest_history``
     - Yes
     -
   * - ``fetch_open_interests``
     - Yes
     - Batch fetch for multiple symbols
   * - ``set_leverage``
     - Yes
     - 1.0x to 100x per market
   * - ``fetch_leverage``
     - Yes
     -
   * - ``fetch_leverage_tiers``
     - Yes
     - Requires Subsquid data source
   * - ``fetch_market_leverage_tiers``
     - Yes
     -
   * - ``set_margin_mode``
     - No
     - GMX uses isolated margin only
   * - ``set_position_mode``
     - No
     -
   * - ``add_margin``
     - No
     - Not yet implemented
   * - ``reduce_margin``
     - No
     - Not yet implemented
   * - ``fetch_apy``
     - Yes
     - GMX-specific extension

Transfers and funding
~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Method
     - Status
     - Notes
   * - ``deposit``
     - No
     -
   * - ``withdraw``
     - No
     -
   * - ``transfer``
     - No
     -
   * - ``fetch_deposits``
     - No
     -
   * - ``fetch_withdrawals``
     - No
     -
   * - ``fetch_deposit_address``
     - No
     -
   * - ``fetch_borrow_rate``
     - No
     -
   * - ``fetch_borrow_rates``
     - No
     -

WebSocket (watch methods)
~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Method
     - Status
     - Notes
   * - ``watch_ticker``
     - No
     -
   * - ``watch_tickers``
     - No
     -
   * - ``watch_order_book``
     - No
     -
   * - ``watch_trades``
     - No
     -
   * - ``watch_ohlcv``
     - No
     -
   * - ``watch_orders``
     - No
     -
   * - ``watch_positions``
     - No
     -
   * - ``watch_balance``
     - No
     -

GMX-specific extensions
=======================

These parameters and features are unique to the GMX adapter and not part of the
standard CCXT interface.

Custom order parameters
~~~~~~~~~~~~~~~~~~~~~~~

Pass these in the ``params`` dict when calling ``create_order``:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Parameter
     - Description
   * - ``size_usd``
     - Position size in USD (alternative to base currency amount)
   * - ``leverage``
     - Leverage multiplier (1.0x to 100x)
   * - ``collateral_symbol``
     - Collateral token symbol (e.g. ``"USDC"``, ``"ETH"``)
   * - ``execution_buffer``
     - Gas fee multiplier for execution (default 2.2)
   * - ``slippage_percent``
     - Slippage tolerance (default 0.003 = 0.3%)
   * - ``stopLoss``
     - Stop-loss config: ``{triggerPrice, triggerPercent, closePercent}``
   * - ``takeProfit``
     - Take-profit config: ``{triggerPrice, triggerPercent, closePercent}``
   * - ``reduceOnly``
     - Close position instead of opening new one
   * - ``wait_for_execution``
     - Wait for keeper execution (default ``True``)
   * - ``auto_cancel``
     - Auto-cancel if execution fails (default ``False``)

Bundled orders
~~~~~~~~~~~~~~

GMX supports creating a position with stop-loss and take-profit in a single
atomic transaction. Pass ``stopLoss`` and ``takeProfit`` to ``create_order``
with either percentage-based (``triggerPercent``) or absolute price
(``triggerPrice``) triggers.

Freqtrade integration
=====================

The Freqtrade exchange class lives in :py:mod:`eth_defi.gmx.freqtrade` and provides
the monkeypatch that injects GMX as a supported exchange.

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Feature
     - Status
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
   * - Backtest timerange validation
     - Yes
     - Validates data availability before backtesting
   * - ``get_max_leverage``
     - Yes
     -
   * - ``fetch_onchain_positions``
     - Yes
     - Fetch positions via GraphQL or Web3
   * - Spot trading
     - No
     - Only perpetual futures
   * - WebSocket streaming
     - No
     -
   * - Order modification (``stoploss_adjust``)
     - No
     - Returns ``False``; GMX orders are immutable
   * - Cross margin
     - No
     - GMX uses isolated margin only

Supported timeframes
====================

.. list-table::
   :header-rows: 1
   :widths: 20 30

   * - Timeframe
     - Period
   * - ``1m``
     - 1 minute
   * - ``5m``
     - 5 minutes
   * - ``15m``
     - 15 minutes
   * - ``1h``
     - 1 hour
   * - ``4h``
     - 4 hours
   * - ``1d``
     - 1 day

Known limitations
=================

- **No volume data** -- OHLCV volume is always 0 (GMX API limitation)
- **Calculated 24h stats** -- ticker 24h high/low/open are calculated from OHLCV, not real-time
- **Balance used field** -- shown as 0.0 (not calculated)
- **No order book** -- GMX uses liquidity pools instead of traditional order books
- **No order cancellation** -- orders execute immediately via keeper network or revert
- **Isolated margin only** -- cross margin is not supported by the protocol
- **Keeper execution** -- orders are processed by the keeper network, not instantly
- **Testnet** -- Arbitrum Sepolia uses RPC mode with slower market loading

Async support
=============

A full async implementation is available in :py:mod:`eth_defi.gmx.ccxt.async_support`
with the same method signatures, using ``aiohttp`` for HTTP calls, ``AsyncWeb3`` for
blockchain operations, and async GraphQL for Subsquid queries.
