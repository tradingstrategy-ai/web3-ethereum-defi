"""GMX exchange subclass for Freqtrade.

This module provides a Freqtrade-compatible exchange class for GMX protocol,
enabling GMX to be used as a trading backend in Freqtrade strategies.

GMX is a decentralized perpetual futures exchange running on Arbitrum and Avalanche.
It uses a unique liquidity pool model instead of traditional order books.

Key Features:
- Perpetual futures trading with up to 100x leverage
- Direct execution against liquidity pools (no order books)
- Immediate order execution (no pending orders)
- Cross and isolated margin modes
- Funding fee mechanics for long/short positions
- Zero-price-impact trades within liquidity limits

Limitations:
- No spot trading (futures only)
- No traditional order book
- No limit orders (all orders execute immediately or revert)
- No order cancellation (orders execute atomically)
- Trading requires Web3 wallet (not API keys)
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exceptions import OperationalException, TemporaryError
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import retrier
from freqtrade.exchange.exchange_types import CcxtOrder, FtHas

from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError
from eth_defi.gmx.core.open_positions import GetOpenPositions

logger = logging.getLogger(__name__)

#: Maximum age for an open GMX order before force-cancelling (milliseconds).
#: GMX market orders execute within seconds via keepers.
#: 10 minutes is generous — if no keeper event after this, something is wrong.
GMX_ORDER_MAX_AGE_MS = 10 * 60 * 1000


class Gmx(Exchange):
    """Freqtrade exchange class for GMX protocol.

    This class provides Freqtrade integration for GMX, a decentralized perpetual
    futures exchange. Since GMX is a DEX with unique characteristics, some
    Freqtrade features are not supported.

    Stop-Loss and Take-Profit Support
    ----------------------------------

    GMX supports both standard Freqtrade SL/TP patterns and advanced bundled orders:

    **Standard Pattern (Default)**

        Freqtrade creates orders separately:

        1. Entry order via ``create_order()`` - opens position
        2. Stop-loss via ``create_stoploss()`` - separate transaction after entry fills
        3. Take-profit via exit signals - bot-managed exits

        This works out of the box with standard Freqtrade strategies when
        ``stoploss_on_exchange=True`` is configured.

    **Advanced Pattern (Bundled Orders)**

        Custom strategies can pass ``stopLoss`` and ``takeProfit`` parameters to
        ``create_order()`` to create all 3 orders atomically in one transaction:

        - Main order (position entry)
        - Stop-loss order (if stopLoss provided)
        - Take-profit order (if takeProfit provided)

        Benefits: Lower gas costs, atomic execution, guaranteed SL/TP placement.

        Example custom strategy::

            def enter_long(self, pair, amount, leverage):
                return self.exchange.create_order(
                    pair=pair,
                    ordertype="market",
                    side="buy",
                    amount=amount,
                    leverage=leverage,
                    stopLoss={"triggerPercent": 0.05},  # 5% SL
                    takeProfit={"triggerPercent": 0.10},  # 10% TP
                )

    Configuration Example
    ---------------------

    Basic configuration::

        {
            "exchange": {
                "name": "gmx",
                "rpc_url": "https://arb1.arbitrum.io/rpc",
                "private_key": "0x...",  # Web3 private key
                "ccxt_config": {},
                "ccxt_async_config": {},
                "pair_whitelist": ["ETH/USD", "BTC/USD"],
            },
            "stake_currency": "USD",
            "trading_mode": "futures",
            "margin_mode": "isolated",
            "order_types": {
                "entry": "market",
                "exit": "market",
                "stoploss": "market",
                "stoploss_on_exchange": True,  # Enable SL on exchange
            },
        }
    """

    # Feature flags for GMX futures
    _ft_has: FtHas = {
        # GMX is futures-only, no spot support
        "stoploss_on_exchange": True,  # GMX supports bundled SL/TP orders
        "order_time_in_force": ["GTC"],  # Only GTC (Good-Till-Cancel) - immediate execution
        "trades_pagination": None,  # No pagination support
        "trades_has_history": True,  # Can fetch historical trades
        "l2_limit_range": None,  # No order book
        "ohlcv_candle_limit": 10000,  # Max candles per request
        "ohlcv_has_history": True,  # Historical OHLCV available
        "mark_ohlcv_price": "index",  # Use index price for mark price
        "mark_ohlcv_timeframe": "1h",  # Default mark price timeframe
        "funding_fee_timeframe": "8h",  # Funding fees every 8 hours
        "ccxt_futures_name": "swap",  # CCXT market type
        "needs_trading_fees": True,  # Trading fees apply
        "order_props_in_contracts": ["amount", "cost", "filled", "remaining"],
        "ws_enabled": False,  # WebSocket not supported yet
    }

    _ft_has_futures: FtHas = {
        "funding_fee_candle_limit": 10000,  # Max funding fee candles
        "stoploss_order_types": {"market": "market"},  # GMX supports market stop-loss
        "order_time_in_force": ["GTC"],  # Only immediate execution
        "tickers_have_price": True,  # Tickers include bid/ask
        "floor_leverage": False,  # Leverage is not floored
        "stop_price_type_field": None,  # No stop price configuration
        "order_props_in_contracts": ["amount", "cost", "filled", "remaining"],
        "stop_price_type_value_mapping": {},  # No stop price types
    }

    # GMX only supports futures with cross/isolated margin
    _supported_trading_mode_margin_pairs: list[tuple[TradingMode, MarginMode]] = [
        (TradingMode.FUTURES, MarginMode.CROSS),
        (TradingMode.FUTURES, MarginMode.ISOLATED),
    ]

    def __init__(self, *args, **kwargs):
        """Initialize GMX exchange.

        :param *args: Positional arguments passed to parent Exchange
        :param **kwargs: Keyword arguments passed to parent Exchange
        """
        super().__init__(*args, **kwargs)

    def fetch_order(self, order_id: str, pair: str, params: dict | None = None) -> CcxtOrder:
        """Fetch order with GMX-specific zombie detection and cancel reason logging.

        Extends the parent ``fetch_order()`` with two GMX-specific behaviours:

        **Zombie order detection:** GMX market orders execute within seconds via
        keepers. If an order is still "open" after :data:`GMX_ORDER_MAX_AGE_MS`
        (default 10 min) with no keeper event, the indexer missed it or something
        went wrong. Force-resolve as cancelled so freqtrade can retry.

        **Cancel reason logging:** When a keeper rejects an order (e.g.
        ``OrderNotFulfillableAtAcceptablePrice``), log the GMX-specific reason
        for easier debugging in freqtrade logs.
        """
        order = super().fetch_order(order_id, pair, params)
        info = order.get("info", {})

        # Zombie order detection: force-cancel orders stuck as "open" beyond max age
        if order.get("status") == "open" and order.get("timestamp") is not None:
            age_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - order["timestamp"]
            if age_ms > GMX_ORDER_MAX_AGE_MS:
                age_seconds = age_ms // 1000
                logger.warning(
                    "GMX zombie order detected: %s for %s has been open for %d seconds. "
                    "Force-resolving as cancelled.",
                    order_id[:18], pair, age_seconds,
                )
                order["status"] = "cancelled"
                order["filled"] = 0.0
                order["remaining"] = order.get("amount")
                order.setdefault("info", {})
                info = order["info"]
                info["gmx_status"] = "zombie_cancelled"
                info["cancel_reason"] = (
                    f"Order open for {age_seconds}s without keeper execution"
                )
                return order

        # Log GMX-specific cancel reasons for debugging
        if order.get("status") in ("cancelled", "canceled", "expired"):
            cancel_reason = (
                info.get("cancellation_reason")
                or info.get("cancel_reason")
            )
            if cancel_reason:
                logger.info(
                    "GMX order %s for %s was %s: %s",
                    order_id[:18],
                    pair,
                    info.get("gmx_status", order["status"]),
                    cancel_reason,
                )

        return order

    @property
    def _ccxt_config(self) -> dict:
        """Get CCXT configuration for GMX.

        :return: Configuration dict for CCXT initialization
        """
        config = {}
        if self.trading_mode == TradingMode.FUTURES:
            config.update(
                {
                    "options": {
                        "defaultType": "swap",  # Use perpetual swaps
                    }
                }
            )
        return config

    def validate_config(self, config):
        """Validate exchange configuration.

        GMX requires Web3 RPC URL and private key instead of API keys.

        :param config: Freqtrade configuration dict
        :raises OperationalException: If required config is missing or invalid
        """
        super().validate_config(config)

        exchange_config = config.get("exchange", {})

        # GMX requires RPC URL
        if "rpc_url" not in exchange_config and "rpcUrl" not in exchange_config.get("ccxt_config", {}):
            raise OperationalException(
                "GMX exchange requires 'rpc_url' in exchange config or 'rpcUrl' in ccxt_config",
            )

        # Trading mode must be futures
        if self.trading_mode != TradingMode.FUTURES:
            raise OperationalException(f"GMX only supports futures trading mode, got: {self.trading_mode}")

        # Margin mode must be set
        if not self.margin_mode:
            raise OperationalException("GMX requires margin_mode to be set (isolated or cross)")

        # Validate timerange for backtesting
        if config.get("runmode") in ["backtest", "hyperopt"]:
            self._validate_backtest_timerange(config)

    def _validate_backtest_timerange(self, config: dict) -> None:
        """Validate that backtest timerange is within available historical data.

        This method checks if the requested timerange in backtesting falls within
        the available data range in cached feather files. Raises an error if data
        is insufficient, preventing wasted computation on invalid backtests.

        :param config: Freqtrade configuration dict containing timerange and pair_whitelist
        :raises InsufficientHistoricalDataError: If timerange exceeds available data
        :raises OperationalException: If data files cannot be read
        """
        # Extract timerange parameter
        timerange_str = config.get("timerange")
        if not timerange_str:
            # No timerange specified, use all available data
            return

        # Parse timerange string (format: "20250101-20251130" or "20250101-")
        timerange_parts = timerange_str.split("-")
        if len(timerange_parts) < 2:
            # Invalid format, let freqtrade handle it
            return

        # Convert start date to timestamp (ms)
        start_str = timerange_parts[0]
        try:
            requested_start = self._parse_timerange_date(start_str)
        except ValueError:
            # Invalid date format, let freqtrade handle it
            return

        # Get pairs and timeframe
        pairs = config.get("exchange", {}).get("pair_whitelist", [])
        timeframe = config.get("timeframe", "5m")

        # Get data directory
        user_data_dir = Path(config.get("user_data_dir", "user_data"))
        datadir_config = config.get("datadir")
        if datadir_config:
            datadir = Path(datadir_config)
        else:
            # Default: user_data/data/<exchange_name>
            datadir = user_data_dir / "data" / self.name

        # Validate each pair
        for pair in pairs:
            self._validate_pair_data(
                pair=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                datadir=datadir,
            )

    def _parse_timerange_date(self, date_str: str) -> int:
        """Parse freqtrade timerange date string to millisecond timestamp.

        :param date_str: Date string in format YYYYMMDD or YYYYMMDDHHMMSS
        :return: Unix timestamp in milliseconds
        :raises ValueError: If date_str format is invalid
        """
        # Parse different formats
        if len(date_str) == 8:  # YYYYMMDD
            dt = datetime.strptime(date_str, "%Y%m%d")
        elif len(date_str) == 14:  # YYYYMMDDHHMMSS
            dt = datetime.strptime(date_str, "%Y%m%d%H%M%S")
        else:
            raise ValueError(f"Invalid timerange date format: {date_str}")

        # Convert to UTC timestamp (ms)
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _validate_pair_data(
        self,
        pair: str,
        timeframe: str,
        requested_start: int,
        datadir: Path,
    ) -> None:
        """Validate single pair's data availability against requested timerange.

        Reads feather file metadata (date column only) and checks if available
        data range covers the requested start date. Validation is date-based,
        meaning any time on the requested date is acceptable.

        :param pair: Trading pair (e.g., "ETH/USDC:USDC")
        :param timeframe: Candle timeframe (e.g., "5m", "1h")
        :param requested_start: Requested start timestamp (ms)
        :param datadir: Path to data directory containing feather files
        :raises InsufficientHistoricalDataError: If data is insufficient
        :raises OperationalException: If feather file cannot be read
        """
        # Convert pair format: "ETH/USDC:USDC" -> "ETH_USDC_USDC"
        pair_filename = pair.replace("/", "_").replace(":", "_")

        # Construct feather file path
        candle_type = "futures"  # GMX only supports futures
        feather_file = datadir / candle_type / f"{pair_filename}-{timeframe}-{candle_type}.feather"

        # Check if file exists
        if not feather_file.exists():
            raise InsufficientHistoricalDataError(
                symbol=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                available_start=None,
                available_end=None,
                candles_received=0,
            )

        # Load feather file metadata (only date column)
        try:
            df = pd.read_feather(feather_file, columns=["date"])
        except Exception as e:
            raise OperationalException(f"Failed to read data file {feather_file}: {e}")

        if len(df) == 0:
            raise InsufficientHistoricalDataError(
                symbol=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                available_start=None,
                available_end=None,
                candles_received=0,
            )

        # Extract available date range
        available_start = int(df["date"].min().timestamp() * 1000)
        available_end = int(df["date"].max().timestamp() * 1000)

        # Compare dates (ignore time) for validation
        # This allows any time on the same date to be acceptable
        requested_date = datetime.fromtimestamp(requested_start / 1000, tz=timezone.utc).date()
        available_start_date = datetime.fromtimestamp(available_start / 1000, tz=timezone.utc).date()

        # Check if data starts on a later date
        if available_start_date > requested_date:
            raise InsufficientHistoricalDataError(
                symbol=pair,
                timeframe=timeframe,
                requested_start=requested_start,
                available_start=available_start,
                available_end=available_end,
                candles_received=len(df),
            )

    def _get_params(
        self,
        side: str,
        ordertype: str,
        leverage: float,
        reduceOnly: bool,
        time_in_force: str = "GTC",
    ) -> dict:
        """Get parameters for order creation.

        :param side: Order side ('buy' or 'sell')
        :param ordertype: Order type ('market', 'limit', etc.)
        :param leverage: Leverage multiplier
        :param reduceOnly: Whether this is a reduce-only order
        :param time_in_force: Time in force (only 'GTC' supported)
        :return: Parameters dict for CCXT order creation
        """
        params = super()._get_params(
            side=side,
            ordertype=ordertype,
            leverage=leverage,
            reduceOnly=reduceOnly,
            time_in_force=time_in_force,
        )

        # GMX-specific parameters
        params["leverage"] = leverage

        return params

    def get_max_leverage(self, pair: str, stake_amount: float | None) -> float:
        """Get maximum leverage for a trading pair on GMX.

        GMX supports different leverage limits per market based on the
        minCollateralFactor. This is already loaded in the market info.

        :param pair: Trading pair symbol (e.g., "ETH/USD")
        :param stake_amount: Stake amount (not used for GMX as leverage is market-specific)
        :return: Maximum leverage as float (e.g., 50.0 for 50x)
        :raises OperationalException: If pair not found or leverage info unavailable
        """
        try:
            # Get market info from CCXT
            market = self.markets.get(pair)

            if not market:
                # If markets not loaded, return default
                logger.warning("Market %s not found, returning default leverage of 50x", pair)
                return 50.0

            # Get max leverage from market limits
            max_leverage = market.get("limits", {}).get("leverage", {}).get("max")

            if max_leverage and max_leverage > 0:
                return float(max_leverage)

            # Fallback to default GMX leverage
            logger.debug("No leverage limit found for %s, using default 50x", pair)
            return 50.0

        except Exception as e:
            logger.warning("Error getting max leverage for %s: %s, returning default 50x", pair, e)
            return 50.0

    def fetch_onchain_positions(self, use_graphql: bool = False) -> dict:
        """Fetch live GMX positions directly from the contracts (or Subsquid when enabled).

        This gives Freqtrade a second, on-chain source of truth to reconcile
        dashboard state after opens/closes. It mirrors the logic used by the
        CCXT adapter so you can verify that positions are really open/closed
        when the UI or logs look suspicious.
        """
        gmx = getattr(self, "_api", None)
        wallet = getattr(gmx, "wallet_address", None)

        if not gmx or not getattr(gmx, "config", None):
            raise OperationalException("GMX CCXT client is not initialized")
        if not wallet:
            raise OperationalException("GMX wallet_address is missing; cannot fetch on-chain positions")

        positions = GetOpenPositions(gmx.config, use_graphql=use_graphql).get_data(wallet)
        logger.info("Fetched %s on-chain GMX positions for wallet %s", len(positions), wallet)
        return positions

    @retrier(retries=0)
    def create_stoploss(
        self,
        pair: str,
        amount: float,
        stop_price: float,
        order_types: dict,
        side: str,
        leverage: float,
    ) -> dict:
        """Create a stop-loss order on GMX.

        GMX supports bundled stop-loss orders that are created atomically with positions.
        This method creates a standalone stop-loss order for existing positions.

        :param pair: Trading pair (e.g., "ETH/USDC:USDC")
        :param amount: Position size in base currency (e.g., BTC for BTC/USD, ETH for ETH/USD)
        :param stop_price: Stop-loss trigger price
        :param order_types: Freqtrade order type configuration
        :param side: Order side ("buy" for closing short, "sell" for closing long)
        :param leverage: Leverage multiplier
        :return: CCXT-compatible order structure
        :raises TemporaryError: If order creation fails temporarily
        :raises DDosProtection: If rate limit exceeded
        """
        logger.debug("*" * 80)
        logger.debug("*** GMX create_stoploss CALLED ***")
        logger.debug(
            "  pair=%s, amount=%.8f, stop_price=%.2f, side=%s, leverage=%.2f",
            pair,
            amount,
            stop_price,
            side,
            leverage,
        )
        logger.debug("  order_types=%s", order_types)
        logger.debug("*" * 80)

        try:
            # Convert amount from base currency to USD
            # Freqtrade passes amount in base currency (BTC/ETH), but GMX expects USD
            ticker = self._api.fetch_ticker(pair)
            current_price = ticker["last"]
            amount_usd = amount * current_price

            logger.debug(
                ">>> Converting stop-loss amount for %s: %.8f (base currency) * %.2f (price) = %.2f USD",
                pair,
                amount,
                current_price,
                amount_usd,
            )

            # GMX uses standalone SL/TP order type
            params = {
                "leverage": leverage,
                "stopLossPrice": stop_price,
            }

            logger.debug("Creating standalone stop-loss order with params: %s", params)

            # Create standalone stop-loss order via CCXT
            order = self._api.create_order(
                symbol=pair,
                type="stop_loss",  # GMX-specific order type
                side=side,
                amount=amount_usd,
                params=params,
            )

            logger.debug("*" * 80)
            logger.debug(
                "✓ Created stop-loss order for %s: price=%.2f, amount=%.2f USD",
                pair,
                stop_price,
                amount_usd,
            )
            logger.debug("*" * 80)
            return order

        except Exception as e:
            logger.error("Failed to create stop-loss for %s: %s", pair, e)
            raise TemporaryError(f"GMX stop-loss creation failed: {e}")

    def stoploss_adjust(self, stop_loss: float, order: dict, side: str) -> bool:
        """Check if stoploss needs adjustment.

        GMX stop-loss orders are immutable once created. To adjust, you must
        cancel the existing order and create a new one.

        :param stop_loss: New stop-loss price
        :param order: Existing stop-loss order
        :param side: Order side
        :return: True if adjustment needed, False otherwise
        """
        # GMX orders are immutable - any change requires cancellation and recreation
        # Since GMX doesn't support order cancellation for executed orders,
        # return False to indicate no adjustment possible
        return False

    @retrier
    def create_order(
        self,
        *,
        pair: str,
        ordertype: str,
        side: str,
        amount: float,
        rate: float | None = None,
        leverage: float = 1.0,
        reduceOnly: bool = False,
        time_in_force: str = "GTC",
        initial_order: bool = True,
        **kwargs,
    ) -> dict:
        """Create order with optional bundled stop-loss and take-profit support.

        GMX supports two order creation patterns:

        **Standard Freqtrade Pattern (separate orders):**
            Used by default when no SL/TP parameters are provided. Freqtrade will:

            1. Call ``create_order()`` to open position (single order)
            2. Call ``create_stoploss()`` after entry fills (separate transaction)
            3. Call ``create_order()`` for exits/take-profit (separate transaction)

            This is the standard Freqtrade flow and works out of the box.

        **Advanced Pattern (bundled orders):**
            When ``stopLoss`` or ``takeProfit`` parameters are provided, GMX creates
            all orders atomically in a single transaction:

            - Main order (entry position)
            - Stop-loss order (if stopLoss provided)
            - Take-profit order (if takeProfit provided)

            This reduces gas costs and ensures atomic execution. Requires custom
            Freqtrade strategies to pass SL/TP parameters.

        Example (standard Freqtrade)::

            # Freqtrade calls this automatically - single entry order
            order = exchange.create_order(
                pair="ETH/USDC:USDC",
                ordertype="market",
                side="buy",
                amount=1000,  # USD
                leverage=3.0,
            )
            # Later, Freqtrade calls create_stoploss() separately

        Example (bundled orders - custom strategy)::

            # Custom strategy can pass SL/TP for bundled order
            order = exchange.create_order(
                pair="ETH/USDC:USDC",
                ordertype="market",
                side="buy",
                amount=1000,
                leverage=3.0,
                stopLoss={"triggerPrice": 1850.0},  # CCXT unified
                takeProfit={"triggerPrice": 2200.0},
            )
            # Creates 3 orders in 1 transaction

        Example (GMX percentage-based triggers)::

            order = exchange.create_order(
                pair="ETH/USDC:USDC",
                ordertype="market",
                side="buy",
                amount=1000,
                leverage=3.0,
                stopLoss={"triggerPercent": 0.05},  # 5% below entry
                takeProfit={"triggerPercent": 0.10},  # 10% above entry
            )

        :param pair: Trading pair (e.g., "ETH/USDC:USDC")
        :param ordertype: Order type ("market", "limit")
        :param side: Order side ("buy" for long, "sell" for short)
        :param amount: Order size in USD
        :param rate: Limit price (not used for GMX market orders)
        :param leverage: Leverage multiplier (1.0 to 100.0)
        :param reduceOnly: Whether this is a reduce-only order
        :param time_in_force: Time in force (only "GTC" supported by GMX)
        :param initial_order: Whether this is an initial order (True) or adjustment (False)
        :param **kwargs: Additional parameters. For bundled orders:

            - ``stopLoss``: Stop-loss configuration (dict or float)

              - Dict: ``{"triggerPrice": 1850.0}`` (CCXT unified)
              - Dict: ``{"triggerPercent": 0.05}`` (GMX extension, 5% below entry)
              - Float: ``1850.0`` (interpreted as triggerPrice)

            - ``takeProfit``: Take-profit configuration (dict or float)

              - Dict: ``{"triggerPrice": 2200.0}`` (CCXT unified)
              - Dict: ``{"triggerPercent": 0.10}`` (GMX extension, 10% above entry)
              - Float: ``2200.0`` (interpreted as triggerPrice)

            - ``stopLossPrice``: Alternative CCXT unified parameter (float)
            - ``takeProfitPrice``: Alternative CCXT unified parameter (float)
            - ``collateral_symbol``: Collateral token (e.g., "USDC")
            - ``slippage_percent``: Slippage tolerance (default: 0.003)

        :return: CCXT-compatible order structure with GMX-specific info
        :raises TemporaryError: If order creation fails temporarily
        :raises OperationalException: If parameters are invalid
        """
        # Enhanced logging with visual separators for workflow visibility
        logger.debug("=" * 80)
        logger.debug("*** GMX FREQTRADE create_order CALLED ***")
        logger.debug(
            "  pair=%s, ordertype=%s, side=%s, amount=%.8f, rate=%s, leverage=%.2f, reduceOnly=%s, time_in_force=%s, initial_order=%s",
            pair,
            ordertype,
            side,
            amount,
            rate,
            leverage,
            reduceOnly,
            time_in_force,
            initial_order,
        )
        if kwargs:
            logger.debug("  kwargs=%s", kwargs)
        logger.debug("=" * 80)

        # Check wallet ETH balance and warn if low (before creating order)
        try:
            if hasattr(self._api, "web3") and hasattr(self._api, "wallet"):
                balance_wei = self._api.web3.eth.get_balance(self._api.wallet.address)
                balance_eth = balance_wei / 1e18

                # Warn if balance is low (< 0.01 ETH)
                if balance_eth < 0.01:
                    logger.warning(
                        "💰 GMX GAS WARNING: Low ETH balance %.6f ETH. Minimum recommended: 0.01 ETH. Top up wallet %s to avoid order failures.",
                        balance_eth,
                        self._api.wallet.address,
                    )
        except Exception:
            # Silently ignore balance check failures (don't block order creation)
            pass

        # Call parent create_order which uses CCXT underneath
        # Note: initial_order is GMX-specific, don't pass to parent Exchange
        logger.debug(">>> Delegating to parent Exchange.create_order() -> GMX CCXT adapter")
        order = super().create_order(
            pair=pair,
            ordertype=ordertype,
            side=side,
            amount=amount,
            rate=rate,
            leverage=leverage,
            reduceOnly=reduceOnly,
            time_in_force=time_in_force,
            **kwargs,
        )

        # Detect "position already closed" synthetic orders from the CCXT adapter.
        # This happens when the bot tries to exit a position that no longer exists
        # on-chain. The CCXT adapter returns a synthetic closed order with
        # info["reason"] == "position_already_closed". The actual close reason
        # (stop-loss, liquidation, manual close) is not available at this layer.
        if order.get("info", {}).get("reason") == "position_already_closed":
            logger.warning(
                "GMX position for %s no longer exists on-chain. "
                "Returning synthetic closed order with exit_reason=%s",
                pair,
                order.get("info", {}).get("exit_reason", "sold_on_exchange"),
            )

        logger.debug("=" * 80)
        logger.debug("*** GMX CCXT adapter RETURNED order ***")
        logger.debug(
            "  id=%s, status=%s, filled=%.8f, remaining=%.8f",
            order.get("id"),
            order.get("status"),
            order.get("filled", 0),
            order.get("remaining", 0),
        )
        logger.debug("  cost=%.2f, average=%.4f", order.get("cost", 0), order.get("average", 0))
        # Log order info for debugging balance/profit issues
        order_info = order.get("info", {})
        if order_info:
            logger.debug("  FREQTRADE_ORDER_TRACE: info=%s", order_info)
        logger.debug("=" * 80)

        return order
