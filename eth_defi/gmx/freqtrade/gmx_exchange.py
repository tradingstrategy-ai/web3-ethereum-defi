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
from typing import Any

from freqtrade.enums import CandleType, MarginMode, PriceType, TradingMode
from freqtrade.exceptions import DDosProtection, OperationalException, TemporaryError
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import retrier
from freqtrade.exchange.exchange_types import FtHas, Tickers

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
        "ohlcv_candle_limit": 1000,  # Max candles per request
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
        "funding_fee_candle_limit": 1000,  # Max funding fee candles
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
            raise OperationalException("GMX exchange requires 'rpc_url' in exchange config or 'rpcUrl' in ccxt_config")

        # Trading mode must be futures
        if self.trading_mode != TradingMode.FUTURES:
            raise OperationalException(f"GMX only supports futures trading mode, got: {self.trading_mode}")

        # Margin mode must be set
        if not self.margin_mode:
            raise OperationalException("GMX requires margin_mode to be set (isolated or cross)")

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
