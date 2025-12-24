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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from freqtrade.enums import CandleType, MarginMode, PriceType, TradingMode
from freqtrade.exceptions import DDosProtection, OperationalException, TemporaryError
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import retrier
from freqtrade.exchange.exchange_types import FtHas, Tickers

from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError
from eth_defi.gmx.ccxt.validation import _timeframe_to_milliseconds
from eth_defi.gmx.core.open_positions import GetOpenPositions

logger = logging.getLogger(__name__)


class Gmx(Exchange):
    """Freqtrade exchange class for GMX protocol.

    This class provides Freqtrade integration for GMX, a decentralized perpetual
    futures exchange. Since GMX is a DEX with unique characteristics, some
    Freqtrade features are not supported.

    Configuration Example::

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
        }
    """

    # Feature flags for GMX futures
    _ft_has: FtHas = {
        # GMX is futures-only, no spot support
        "stoploss_on_exchange": False,  # No stop-loss on exchange (use Freqtrade stop-loss)
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
        "stoploss_order_types": {},  # No stop-loss order types
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

        Args:
            *args: Positional arguments passed to parent Exchange
            **kwargs: Keyword arguments passed to parent Exchange
        """
        super().__init__(*args, **kwargs)

    @property
    def _ccxt_config(self) -> dict:
        """Get CCXT configuration for GMX.

        Returns:
            Configuration dict for CCXT initialization
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

        Args:
            config: Freqtrade configuration dict

        Raises:
            OperationalException: If required config is missing or invalid
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

        Args:
            config: Freqtrade configuration dict containing timerange and pair_whitelist

        Raises:
            InsufficientHistoricalDataError: If timerange exceeds available data
            OperationalException: If data files cannot be read
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

        Args:
            date_str: Date string in format YYYYMMDD or YYYYMMDDHHMMSS

        Returns:
            Unix timestamp in milliseconds

        Raises:
            ValueError: If date_str format is invalid
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

        Args:
            pair: Trading pair (e.g., "ETH/USDC:USDC")
            timeframe: Candle timeframe (e.g., "5m", "1h")
            requested_start: Requested start timestamp (ms)
            datadir: Path to data directory containing feather files

        Raises:
            InsufficientHistoricalDataError: If data is insufficient
            OperationalException: If feather file cannot be read
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

        Args:
            side: Order side ('buy' or 'sell')
            ordertype: Order type ('market', 'limit', etc.)
            leverage: Leverage multiplier
            reduceOnly: Whether this is a reduce-only order
            time_in_force: Time in force (only 'GTC' supported)

        Returns:
            Parameters dict for CCXT order creation
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

        Args:
            pair: Trading pair symbol (e.g., "ETH/USD")
            stake_amount: Stake amount (not used for GMX as leverage is market-specific)

        Returns:
            Maximum leverage as float (e.g., 50.0 for 50x)

        Raises:
            OperationalException: If pair not found or leverage info unavailable
        """
        try:
            # Get market info from CCXT
            market = self.markets.get(pair)

            if not market:
                # If markets not loaded, return default
                logger.warning(f"Market {pair} not found, returning default leverage of 50x")
                return 50.0

            # Get max leverage from market limits
            max_leverage = market.get("limits", {}).get("leverage", {}).get("max")

            if max_leverage and max_leverage > 0:
                return float(max_leverage)

            # Fallback to default GMX leverage
            logger.debug(f"No leverage limit found for {pair}, using default 50x")
            return 50.0

        except Exception as e:
            logger.warning(f"Error getting max leverage for {pair}: {e}, returning default 50x")
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
