"""
GMX Price Sanity Check Module.

This module provides functionality to compare GMX oracle (mark/index) prices against
ticker 'last' prices to detect stale data or price manipulation, especially for low
liquidity tokens.

The price sanity check compares two independent price sources:
- Oracle prices from `/signed_prices/latest` endpoint (used for on-chain execution)
- Ticker prices from `/prices/tickers` endpoint (market ticker data)

When the deviation between these prices exceeds a configurable threshold (default 3%),
the system can take various actions such as logging warnings, using the oracle price,
or raising an exception to prevent potentially problematic trades.

Example:

.. code-block:: python

    from eth_defi.gmx.price_sanity import (
        check_price_sanity,
        PriceSanityCheckConfig,
        PriceSanityAction,
    )
    from eth_defi.gmx.core.oracle import OraclePrices
    from eth_defi.gmx.api import GMXAPI

    # Fetch prices from both sources
    oracle_prices = OraclePrices("arbitrum").get_recent_prices()
    ticker_prices = GMXAPI(chain="arbitrum").get_tickers()

    # Configure sanity check
    config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.03,  # 3%
        action=PriceSanityAction.use_oracle_warn,
    )

    # Perform sanity check
    result = check_price_sanity(
        oracle_price=oracle_prices["0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"],
        ticker_price=ticker_prices[0],  # First ticker
        token_address="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        token_decimals=18,
        config=config,
    )

    # Check result
    if not result.passed:
        print(f"Price deviation: {result.deviation_percent:.2%}")
        print(f"Action taken: {result.action_taken}")
"""

import datetime
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ccxt import ExchangeError

from eth_defi.compat import native_datetime_utc_now
from eth_defi.gmx.constants import PRECISION

logger = logging.getLogger(__name__)


class PriceSanityAction(Enum):
    """Actions to take when price deviation exceeds threshold."""

    #: Use oracle price and log warning
    use_oracle_warn = "use_oracle_warn"

    #: Use ticker price and log warning
    use_ticker_warn = "use_ticker_warn"

    #: Raise PriceSanityException to block the operation
    raise_exception = "raise_exception"


@dataclass(slots=True)
class PriceSanityCheckConfig:
    """Configuration for price sanity checks."""

    #: Whether price sanity checks are enabled
    enabled: bool = True

    #: Deviation threshold as decimal (0.03 = 3%)
    threshold_percent: float = 0.03

    #: Action to take when deviation exceeds threshold
    action: PriceSanityAction = PriceSanityAction.use_oracle_warn


@dataclass(slots=True)
class PriceSanityCheckResult:
    """Result of price sanity check comparison."""

    #: Whether the check passed (deviation within threshold)
    passed: bool

    #: Deviation as decimal (0.045 = 4.5%)
    deviation_percent: float

    #: Oracle price in USD
    oracle_price_usd: float

    #: Ticker price in USD
    ticker_price_usd: float

    #: Action taken based on configuration
    action_taken: PriceSanityAction

    #: Token address that was checked
    token_address: str

    #: Timestamp when check was performed
    timestamp: datetime.datetime

    #: Optional reason for failure (e.g., "ticker_fetch_failed")
    reason: Optional[str] = None


class PriceSanityException(ExchangeError):
    """Raised when price sanity check fails and action is raise_exception.

    This exception is raised when the deviation between oracle and ticker prices
    exceeds the configured threshold and the action is set to raise_exception.

    :param result: The PriceSanityCheckResult containing details about the failure
    """

    def __init__(self, result: PriceSanityCheckResult):
        self.result = result
        message = f"Price sanity check failed for {result.token_address}: deviation {result.deviation_percent:.2%} exceeds threshold. Oracle=${result.oracle_price_usd:.2f}, Ticker=${result.ticker_price_usd:.2f}"
        super().__init__(message)


def get_oracle_price_usd(oracle_data: dict, token_decimals: int) -> float:
    """Extract USD price from oracle data.

    Calculates the median of maxPriceFull and minPriceFull from oracle data
    and converts it to USD based on token decimals.

    :param oracle_data: Oracle price data dictionary with maxPriceFull and minPriceFull
    :param token_decimals: Number of decimals for the token (e.g., 18 for ETH)
    :return: Price in USD
    :raises ValueError: If price data is missing or invalid
    """
    if not oracle_data:
        raise ValueError("Oracle data is empty")

    if "maxPriceFull" not in oracle_data or "minPriceFull" not in oracle_data:
        raise ValueError("Oracle data missing maxPriceFull or minPriceFull")

    max_price = float(oracle_data["maxPriceFull"])
    min_price = float(oracle_data["minPriceFull"])

    if max_price <= 0 or min_price <= 0:
        raise ValueError(f"Invalid oracle prices: max={max_price}, min={min_price}")

    # Calculate median (same logic as base_order.py)
    median_price = (max_price + min_price) / 2

    # Convert from 30-decimal PRECISION format to USD
    price_usd = median_price / (10 ** (PRECISION - token_decimals))

    return price_usd


def get_ticker_price_usd(ticker_data: dict, token_decimals: int) -> float:
    """Extract USD price from ticker data.

    Calculates the midpoint of maxPrice and minPrice from ticker data
    and converts it to USD based on token decimals.

    :param ticker_data: Ticker price data dictionary with maxPrice and minPrice
    :param token_decimals: Number of decimals for the token (e.g., 18 for ETH)
    :return: Price in USD
    :raises ValueError: If price data is missing or invalid
    """
    if not ticker_data:
        raise ValueError("Ticker data is empty")

    if "maxPrice" not in ticker_data or "minPrice" not in ticker_data:
        raise ValueError("Ticker data missing maxPrice or minPrice")

    max_price = float(ticker_data["maxPrice"])
    min_price = float(ticker_data["minPrice"])

    if max_price <= 0 or min_price <= 0:
        raise ValueError(f"Invalid ticker prices: max={max_price}, min={min_price}")

    # Calculate midpoint (same logic as parse_ticker in exchange.py)
    midpoint_price = (max_price + min_price) / 2

    # Convert from 30-decimal PRECISION format to USD
    price_usd = midpoint_price / (10 ** (PRECISION - token_decimals))

    return price_usd


def check_price_sanity(
    oracle_price: dict,
    ticker_price: dict,
    token_address: str,
    token_decimals: int,
    config: PriceSanityCheckConfig,
) -> PriceSanityCheckResult:
    """Compare oracle and ticker prices and determine action.

    This function compares prices from two independent sources (oracle and ticker)
    and calculates the deviation. If the deviation exceeds the configured threshold,
    it takes the specified action (log warning, use oracle, or raise exception).

    The deviation is calculated as:

        deviation = |ticker_price - oracle_price| / |oracle_price|

    :param oracle_price: Oracle price data from OraclePrices.get_recent_prices()
    :param ticker_price: Ticker price data from GMXAPI.get_tickers()
    :param token_address: Token address being checked
    :param token_decimals: Number of decimals for the token
    :param config: Configuration for the sanity check
    :return: PriceSanityCheckResult with comparison details and action taken
    """
    timestamp = native_datetime_utc_now()

    # Validate configuration
    if config.threshold_percent <= 0:
        logger.warning(
            "Invalid threshold_percent %f, using default 0.03 (3%%)",
            config.threshold_percent,
        )
        config.threshold_percent = 0.03

    # Extract prices with error handling
    try:
        oracle_price_usd = get_oracle_price_usd(oracle_price, token_decimals)
    except Exception as e:
        logger.warning(
            "Failed to extract oracle price for %s: %s. Passing sanity check.",
            token_address,
            str(e),
        )
        return PriceSanityCheckResult(
            passed=True,
            deviation_percent=0.0,
            oracle_price_usd=0.0,
            ticker_price_usd=0.0,
            action_taken=config.action,
            token_address=token_address,
            timestamp=timestamp,
            reason="oracle_extraction_failed",
        )

    try:
        ticker_price_usd = get_ticker_price_usd(ticker_price, token_decimals)
    except Exception as e:
        logger.warning(
            "Failed to extract ticker price for %s: %s. Using oracle price only.",
            token_address,
            str(e),
        )
        return PriceSanityCheckResult(
            passed=True,
            deviation_percent=0.0,
            oracle_price_usd=oracle_price_usd,
            ticker_price_usd=0.0,
            action_taken=PriceSanityAction.use_oracle_warn,
            token_address=token_address,
            timestamp=timestamp,
            reason="ticker_extraction_failed",
        )

    # Calculate deviation
    deviation = abs(ticker_price_usd - oracle_price_usd) / abs(oracle_price_usd)

    # Check if within threshold
    passed = deviation <= config.threshold_percent

    if passed:
        logger.debug(
            "Price sanity check passed for %s: oracle=$%.2f, ticker=$%.2f, deviation=%.2f%%",
            token_address,
            oracle_price_usd,
            ticker_price_usd,
            deviation * 100,
        )
        return PriceSanityCheckResult(
            passed=True,
            deviation_percent=deviation,
            oracle_price_usd=oracle_price_usd,
            ticker_price_usd=ticker_price_usd,
            action_taken=config.action,
            token_address=token_address,
            timestamp=timestamp,
        )

    # Deviation exceeds threshold - take action
    logger.warning(
        "Price deviation detected for %s: oracle=$%.2f, ticker=$%.2f, deviation=%.2f%% (threshold=%.2f%%). Action: %s",
        token_address,
        oracle_price_usd,
        ticker_price_usd,
        deviation * 100,
        config.threshold_percent * 100,
        config.action.value,
    )

    result = PriceSanityCheckResult(
        passed=False,
        deviation_percent=deviation,
        oracle_price_usd=oracle_price_usd,
        ticker_price_usd=ticker_price_usd,
        action_taken=config.action,
        token_address=token_address,
        timestamp=timestamp,
        reason="deviation_exceeds_threshold",
    )

    # Raise exception if configured to do so
    if config.action == PriceSanityAction.raise_exception:
        raise PriceSanityException(result)

    return result
