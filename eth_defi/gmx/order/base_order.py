"""
GMX Base Order Implementation

Base class for GMX order management including enums, data structures, and base
order implementations. Provides transaction building for GMX decentralised trading.
"""

import logging
import datetime
from dataclasses import dataclass, field
from typing import Optional
from decimal import Decimal
from statistics import median

from eth_utils import to_checksum_address
from web3.types import TxParams

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_exchange_router_contract, NETWORK_TOKENS, get_datastore_contract, TESTNET_TO_MAINNET_ORACLE_TOKENS, get_reader_contract
from eth_defi.gmx.constants import PRECISION, OrderType, DECREASE_POSITION_SWAP_TYPES, GAS_LIMITS, ETH_ZERO_ADDRESS
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat, native_datetime_utc_now
from eth_defi.gmx.execution_buffer import DEFAULT_EXECUTION_BUFFER, apply_execution_buffer, validate_execution_buffer
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.token import fetch_erc20_details


# Module-level constants and logger
logger = logging.getLogger(__name__)
ZERO_REFERRAL_CODE = bytes.fromhex("0" * 64)

# Price cache for ETH/USD conversion
_eth_price_cache: Optional[tuple[Decimal, datetime.datetime]] = None
_ETH_PRICE_CACHE_SECONDS = 60  # Cache ETH price for 60 seconds


def get_eth_price_usd(config: GMXConfig) -> Optional[Decimal]:
    """Fetch current ETH/USD price from Chainlink with caching.

    This function fetches the current ETH/USD price from Chainlink price feeds
    and caches the result for 60 seconds to avoid rate limiting.

    Supported chains:
    - Arbitrum (chain 42161): Uses 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612

    :param config: GMX configuration containing web3 instance
    :return: ETH price in USD as Decimal, or None if fetching fails
    """
    global _eth_price_cache

    # Check cache
    if _eth_price_cache is not None:
        price, timestamp = _eth_price_cache
        age_seconds = (native_datetime_utc_now() - timestamp).total_seconds()
        if age_seconds < _ETH_PRICE_CACHE_SECONDS:
            logger.debug(
                "Using cached ETH price: $%.2f (cached %.1f seconds ago)",
                price,
                age_seconds,
            )
            return price

    # Fetch fresh price
    try:
        from eth_defi.abi import get_deployed_contract
        from eth_defi.chainlink.round_data import ChainLinkLatestRoundData

        # Get Chainlink aggregator address based on chain
        chain_id = config.web3.eth.chain_id

        # Map chain IDs to Chainlink ETH/USD feed addresses
        # Source: https://docs.chain.link/data-feeds/price-feeds/addresses
        chainlink_feeds = {
            1: "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",  # Ethereum mainnet
            42161: "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",  # Arbitrum One
            10: "0x13e3Ee699D1909E989722E753853AE30b17e08c5",  # Optimism
            8453: "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",  # Base
            137: "0xAB594600376Ec9fD91F8e885dADF0CE036862dE0",  # Polygon
            43114: "0x0A77230d17318075983913bC2145DB16C7366156",  # Avalanche
        }

        aggregator_address = chainlink_feeds.get(chain_id)
        if not aggregator_address:
            logger.debug(
                "Chainlink ETH/USD feed not configured for chain %d. Skipping USD conversion.",
                chain_id,
            )
            return None

        # Fetch price from Chainlink
        aggregator = get_deployed_contract(
            config.web3,
            "ChainlinkAggregatorV2V3Interface.json",
            aggregator_address,
        )
        data = aggregator.functions.latestRoundData().call()
        round_data = ChainLinkLatestRoundData(aggregator, *data)
        price = round_data.price

        # Cache the price (keep as Decimal for precision)
        _eth_price_cache = (price, native_datetime_utc_now())

        logger.debug(
            "Fetched fresh ETH price from Chainlink on chain %d: $%.2f",
            chain_id,
            float(price),
        )

        return price

    except Exception as e:
        logger.debug(
            "Failed to fetch ETH price from Chainlink: %s. USD conversion unavailable.",
            str(e),
        )
        return None


def format_eth_with_usd(eth_amount: float, eth_price_usd: Optional[Decimal]) -> str:
    """Format ETH amount with optional USD value.

    :param eth_amount: Amount in ETH
    :param eth_price_usd: Current ETH price in USD as Decimal, or None to skip
    :return: Formatted string like "0.000096 ETH (~$0.30)" or "0.000096 ETH"
    """
    if eth_price_usd is not None:
        # Convert eth_amount to Decimal for precise calculation
        usd_value = Decimal(str(eth_amount)) * eth_price_usd
        return f"{eth_amount:.6f} ETH (~${float(usd_value):.2f})"
    else:
        return f"{eth_amount:.6f} ETH"


@dataclass
class OrderParams:
    """Order parameters for GMX orders."""

    # Market identification
    market_key: str
    collateral_address: str
    index_token_address: str

    # Position parameters
    is_long: bool
    size_delta: float  # Position size in USD
    initial_collateral_delta_amount: str  # Collateral in token's smallest unit (wei/satoshi)

    # Execution parameters
    slippage_percent: float = 0.005
    swap_path: list[str] = field(default_factory=list)

    # Optional parameters
    max_fee_per_gas: Optional[int] = None
    auto_cancel: bool = False
    execution_buffer: float = DEFAULT_EXECUTION_BUFFER
    # GMX v2.2 new dataList field
    data_list: Optional[list[str]] = field(default_factory=list)

    # Additional optional parameters
    callback_gas_limit: int = 0
    min_output_amount: int = 0
    valid_from_time: int = 0


@dataclass
class OrderResult:
    """Result of order creation containing unsigned transaction.

    :param transaction: Unsigned transaction ready for signing
    :param execution_fee: Estimated execution fee in wei
    :param acceptable_price: Acceptable price for execution
    :param mark_price: Current mark price
    :param gas_limit: Gas limit for transaction
    :param estimated_price_impact: Optional estimated price impact in USD
    :param price_sanity_check: Optional price sanity check result
    """

    transaction: TxParams
    execution_fee: int
    acceptable_price: int
    mark_price: float
    gas_limit: int
    estimated_price_impact: Optional[float] = None  # Added price impact
    price_sanity_check: Optional["PriceSanityCheckResult"] = None  # Price sanity check result


class BaseOrder:
    """Base GMX Order class.

    Creates unsigned transactions that can be signed later by the user.
    Compatible with CCXT trading interface patterns for easy migration.
    """

    def __init__(self, config: GMXConfig, price_sanity_config: "PriceSanityCheckConfig | None" = None):
        """Initialize the base order with GMX configuration.

        :param config: GMX configuration instance
        :type config: GMXConfig
        :param price_sanity_config: Optional configuration for price sanity checks
        :type price_sanity_config: PriceSanityCheckConfig | None
        """
        self._oracle_prices = None
        self._markets = None
        self._cached_prices = None  # Cache for oracle prices
        self._cached_markets = None  # Cache for markets data
        self.config = config
        self.chain = config.get_chain()
        self.web3 = config.web3
        self.chain_id = config.web3.eth.chain_id
        self.contract_addresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(
            self.web3,
            self.chain,
        )

        # Initialize order type constants
        self._order_types = OrderType

        # Initialize gas limits from datastore
        self._initialize_gas_limits()

        # Initialize price sanity config
        if price_sanity_config is None:
            from eth_defi.gmx.price_sanity import PriceSanityCheckConfig

            self._price_sanity_config = PriceSanityCheckConfig()
        else:
            self._price_sanity_config = price_sanity_config

        logger.debug(
            "Initialized %s for %s",
            self.__class__.__name__,
            self.chain,
        )

    # New method to initialize gas limits
    def _initialize_gas_limits(self):
        """Load gas limits from GMX datastore contract.

        Falls back to default constants if datastore query fails.
        """
        try:
            datastore = get_datastore_contract(self.web3, self.chain)
            self._gas_limits = get_gas_limits(datastore)
            logger.debug("Gas limits loaded from datastore contract")
        except Exception as e:
            logger.warning("Failed to load gas limits from datastore: %s", e)
            # Fallback to default gas limits from constants
            self._gas_limits = GAS_LIMITS.copy()
            logger.debug("Using fallback gas limits from constants")

    @property
    def markets(self) -> Markets:
        """Markets instance for retrieving market information.

        Uses cached property pattern for efficiency.
        """
        if self._markets is None:
            self._markets = Markets(self.config)
        return self._markets

    @property
    def oracle_prices(self) -> OraclePrices:
        """Oracle prices instance for retrieving current prices.

        Uses cached property pattern for efficiency.
        """
        if self._oracle_prices is None:
            self._oracle_prices = OraclePrices(self.config.chain)
        return self._oracle_prices

    def refresh_cache(self) -> None:
        """Refresh cached markets and oracle prices data.

        Call this method to force a refresh of the cached data if you need
        the latest market information and prices.
        """
        logger.debug("Refreshing markets and oracle prices cache")
        self._cached_markets = None
        self._cached_prices = None

    def create_order(
        self,
        params: OrderParams,
        is_open: bool = False,
        is_close: bool = False,
        is_swap: bool = False,
    ) -> OrderResult:
        """Create an order (public interface).

        This is the main public method for creating orders.

        :param params: Order parameters
        :param is_open: Whether opening a position
        :param is_close: Whether closing a position
        :param is_swap: Whether performing a swap
        :return: OrderResult with unsigned transaction
        """
        return self.order_builder(params, is_open, is_close, is_swap)

    def order_builder(
        self,
        params: OrderParams,
        is_open: bool = False,
        is_close: bool = False,
        is_swap: bool = False,
        is_limit: bool = False,
        trigger_price: float | None = None,
    ) -> OrderResult:
        """Build an order transaction.

        Core method that constructs an unsigned transaction for GMX orders.
        This replaces the original SDK's order_builder that submitted transactions.

        :param params: Order parameters
        :param is_open: Whether opening a position
        :param is_close: Whether closing a position
        :param is_swap: Whether performing a swap
        :param is_limit: Whether this is a limit order (triggers at specified price)
        :param trigger_price: USD price at which order triggers (required for limit orders)
        :return: OrderResult with unsigned transaction
        """
        # Determine gas limits (from original determine_gas_limits)
        if is_open:
            if is_limit:
                order_type = OrderType.LIMIT_INCREASE
            else:
                order_type = OrderType.MARKET_INCREASE
        elif is_close:
            order_type = OrderType.MARKET_DECREASE
        elif is_swap:
            order_type = OrderType.MARKET_SWAP
        else:
            order_type = OrderType.MARKET_INCREASE

        # Get market and price data first (validate market exists before other operations)
        # Use cached data to avoid repeated expensive API calls
        if self._cached_markets is None:
            self._cached_markets = self.markets.get_available_markets()
            logger.debug("Markets data cached")
        markets = self._cached_markets

        if self._cached_prices is None:
            self._cached_prices = self.oracle_prices.get_recent_prices()
            logger.debug("Oracle prices cached")
        prices = self._cached_prices

        # For swaps, market_key is zero address - use first swap_path market instead
        if is_swap and params.swap_path:
            market_key_for_lookup = params.swap_path[0]
        else:
            market_key_for_lookup = params.market_key

        market_data = markets.get(market_key_for_lookup)
        if not market_data:
            raise ValueError(f"Market {market_key_for_lookup} not found")

        # Calculate prices with slippage (validate prices exist before other operations)
        decimals = market_data["market_metadata"]["decimals"]
        price_usd, raw_price, acceptable_price, acceptable_price_in_usd, sanity_result = self._get_prices(
            decimals,
            prices,
            params,
            is_open,
            is_close,
            is_swap,
        )

        # Get execution fee
        gas_price = self.web3.eth.gas_price
        gas_limits = self._determine_gas_limits(is_open, is_close, is_swap)
        base_execution_fee = int(gas_limits["total"] * gas_price)
        execution_fee = apply_execution_buffer(base_execution_fee, params.execution_buffer)

        # Log execution fee breakdown for user visibility
        execution_fee_eth = execution_fee / 1e18
        base_execution_fee_eth = base_execution_fee / 1e18
        gas_price_gwei = gas_price / 1e9

        # Fetch ETH price for USD conversion
        eth_price_usd = get_eth_price_usd(self.config)

        logger.info(
            "💰 GMX Execution Fee Breakdown:\n  Base execution fee: %d wei (%s)\n  Execution buffer: %.1fx\n  Final execution fee: %d wei (%s) ← Paid to GMX keepers\n  Gas price: %.2f gwei\n  Note: This is separate from Ethereum gas fees",
            base_execution_fee,
            format_eth_with_usd(base_execution_fee_eth, eth_price_usd),
            params.execution_buffer,
            execution_fee,
            format_eth_with_usd(execution_fee_eth, eth_price_usd),
            gas_price_gwei,
        )

        # Check approval if not closing (after market and price validation)
        if not is_close:
            self._check_for_approval(params)

        # Build order arguments (from original _create_order)
        # Use raw_price (in contract format) for mark_price, not the USD price
        mark_price = raw_price if is_open else 0
        acceptable_price_val = acceptable_price if not is_swap else 0

        # For limit orders, override with trigger price-based calculations
        if is_limit and trigger_price is not None:
            # Calculate acceptable price from trigger price instead of mark price
            if params.is_long:
                # Long: willing to buy at slightly higher than trigger
                slippage_price = trigger_price * (1 + params.slippage_percent)
            else:
                # Short: willing to sell at slightly lower than trigger
                slippage_price = trigger_price * (1 - params.slippage_percent)

            acceptable_price_val = int(slippage_price * (10 ** (PRECISION - decimals)))

            # Convert trigger price to contract format for mark_price param (used as triggerPrice)
            mark_price = int(Decimal(str(trigger_price)) * Decimal(10 ** (PRECISION - decimals)))

            logger.debug("Limit order trigger price (USD): $%.4f", trigger_price)
            logger.debug("Limit order acceptable price (contract): %d", acceptable_price_val)
            logger.debug("Limit order trigger price (contract): %d", mark_price)

        arguments = self._build_order_arguments(
            params,
            execution_fee,
            order_type,
            acceptable_price_val,
            mark_price,
        )

        # Build multicall
        multicall_args, value_amount = self._build_multicall_args(
            params,
            arguments,
            execution_fee,
            is_close,
        )

        # Build final transaction (from original _submit_transaction)
        transaction = self._build_transaction(
            multicall_args,
            value_amount,
            gas_limits["total"],
        )

        # Estimate price impact (optional, may return None). Mostly fails on arbitrum
        price_impact = self._estimate_price_impact(
            params,
            market_data,
            is_open,
            is_close,
            is_swap,
        )

        # For limit orders, return trigger_price as the mark_price
        result_mark_price = trigger_price if (is_limit and trigger_price is not None) else price_usd

        # Log total cost summary for user
        estimated_gas_cost_eth = (gas_limits["total"] * gas_price) / 1e18
        total_eth_needed = execution_fee_eth + estimated_gas_cost_eth

        logger.info(
            "📊 Total Transaction Cost Summary:\n  ├─ Execution fee (GMX keepers): %s\n  ├─ Gas fee (Ethereum network): ~%s (estimated)\n  └─ Total ETH needed: ~%s\n  💡 To reduce execution fee, set executionBuffer to 1.5-1.8 in config (currently %.1fx)",
            format_eth_with_usd(execution_fee_eth, eth_price_usd),
            format_eth_with_usd(estimated_gas_cost_eth, eth_price_usd),
            format_eth_with_usd(total_eth_needed, eth_price_usd),
            params.execution_buffer,
        )

        return OrderResult(
            transaction=transaction,
            execution_fee=execution_fee,
            acceptable_price=acceptable_price_val,
            mark_price=result_mark_price,
            gas_limit=gas_limits["total"],
            estimated_price_impact=price_impact,
            price_sanity_check=sanity_result,
        )

    def _determine_gas_limits(self, is_open: bool, is_close: bool, is_swap: bool) -> dict[str, int]:
        """Determine gas limits based on operation type.

        :param is_open: Whether opening a position
        :param is_close: Whether closing a position
        :param is_swap: Whether performing a swap
        :return: Dictionary with execution and total gas limits
        """
        if is_open:
            execution_gas = self._gas_limits.get("increase_order", 2000000)
        elif is_close:
            execution_gas = self._gas_limits.get("decrease_order", 2000000)
        elif is_swap:
            execution_gas = self._gas_limits.get("swap_order", 1500000)
        else:
            execution_gas = self._gas_limits.get("increase_order", 2000000)

        return {
            "execution": execution_gas,
            "total": execution_gas + self._gas_limits.get("multicall_base", 200000),
        }

    def _get_prices(
        self,
        decimals: int,
        prices: dict,
        params: OrderParams,
        is_open: bool,
        is_close: bool,
        is_swap: bool,
    ) -> tuple[float, int, int, float, Optional["PriceSanityCheckResult"]]:
        """Calculate prices with slippage.

        :param decimals: Token decimals
        :param prices: Oracle prices dictionary
        :param params: Order parameters
        :param is_open: Whether opening a position
        :param is_close: Whether closing a position
        :param is_swap: Whether performing a swap
        :return: Tuple of (price_usd, raw_price, acceptable_price, acceptable_price_in_usd, price_sanity_check)
        """
        logger.debug("Getting prices...")

        # Get oracle address (map testnet token to oracle address if needed)
        oracle_address = params.index_token_address
        if self.chain in ["arbitrum_sepolia", "avalanche_fuji"]:
            oracle_address = TESTNET_TO_MAINNET_ORACLE_TOKENS.get(params.index_token_address, params.index_token_address)

        if oracle_address not in prices:
            raise ValueError(
                f"Price not available for token {params.index_token_address} (oracle: {oracle_address})",
            )

        price_data = prices[oracle_address]
        price = median(
            [float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])],
        )

        # Oracle REST API returns prices in 30-decimal PRECISION format
        # Convert to human-readable USD price based on token decimals
        price_usd = price / (10 ** (PRECISION - decimals))  # PRECISION = 30

        # Calculate slippage based on position type and action
        if is_open:
            if params.is_long:
                slippage_price = price_usd + (price_usd * params.slippage_percent)
            else:
                slippage_price = price_usd - (price_usd * params.slippage_percent)
        elif is_close:
            if params.is_long:
                slippage_price = price_usd - (price_usd * params.slippage_percent)
            else:
                slippage_price = price_usd + (price_usd * params.slippage_percent)
        else:
            slippage_price = 0

        # Convert acceptable price back to contract format: raw_price = human_price * (10 ** (30 - token_decimals))
        acceptable_price = int(slippage_price * (10 ** (PRECISION - decimals)))  # Scale to GMX format (30 - token_decimals)

        acceptable_price_in_usd = slippage_price if slippage_price != 0 else 0

        # Use Decimal for raw_price to avoid precision loss
        raw_price = int(Decimal(str(price)))

        logger.debug("Oracle Address: %s", oracle_address)
        logger.debug("Token Decimals: %d", decimals)
        logger.debug("Raw price (contract format): %s", raw_price)
        logger.debug("Mark Price (USD): $%.8f", price_usd)
        if acceptable_price_in_usd != 0:
            logger.debug("Acceptable price (USD): $%.8f", acceptable_price_in_usd)
            logger.debug("Acceptable price (contract format): %d", acceptable_price)

        # Perform price sanity check if enabled and not a swap
        sanity_result = None
        if self._price_sanity_config.enabled and not is_swap:
            try:
                from eth_defi.gmx.price_sanity import (
                    check_price_sanity,
                    PriceSanityAction,
                    PriceSanityException,
                )
                from eth_defi.gmx.api import GMXAPI

                # Fetch ticker prices
                api = GMXAPI(config=None, chain=self.chain)
                all_tickers = api.get_tickers()

                # Find ticker for this token
                ticker = next(
                    (t for t in all_tickers if t.get("tokenAddress", "").lower() == oracle_address.lower()),
                    None,
                )

                if ticker:
                    # Perform sanity check
                    sanity_result = check_price_sanity(
                        oracle_price=price_data,
                        ticker_price=ticker,
                        token_address=oracle_address,
                        token_decimals=decimals,
                        config=self._price_sanity_config,
                    )

                    # Apply action if check failed
                    if not sanity_result.passed:
                        if sanity_result.action_taken == PriceSanityAction.use_ticker_warn:
                            # Use ticker price instead of oracle price
                            price_usd = sanity_result.ticker_price_usd
                            # Recalculate slippage with ticker price
                            if is_open:
                                if params.is_long:
                                    slippage_price = price_usd + (price_usd * params.slippage_percent)
                                else:
                                    slippage_price = price_usd - (price_usd * params.slippage_percent)
                            elif is_close:
                                if params.is_long:
                                    slippage_price = price_usd - (price_usd * params.slippage_percent)
                                else:
                                    slippage_price = price_usd + (price_usd * params.slippage_percent)
                            else:
                                slippage_price = 0

                            acceptable_price = int(slippage_price * (10 ** (PRECISION - decimals)))
                            acceptable_price_in_usd = slippage_price if slippage_price != 0 else 0
                            raw_price = int(Decimal(str(price_usd * (10 ** (PRECISION - decimals)))))

                            logger.info(
                                "Using ticker price $%.2f instead of oracle price $%.2f due to sanity check",
                                price_usd,
                                sanity_result.oracle_price_usd,
                            )
                        # use_oracle_warn and raise_exception are already handled by check_price_sanity

            except PriceSanityException:
                # Re-raise price sanity exceptions
                raise
            except Exception as e:
                # Log but don't fail on sanity check errors
                logger.warning(
                    "Price sanity check failed: %s. Continuing with oracle price.",
                    str(e),
                )

        return price_usd, raw_price, acceptable_price, acceptable_price_in_usd, sanity_result

    def _build_order_arguments(
        self,
        params: OrderParams,
        execution_fee: int,
        order_type: int,
        acceptable_price: int,
        mark_price: int,
    ) -> tuple:
        """Build order arguments tuple.

        This matches the exact structure expected by GMX contracts.

        :param params: Order parameters
        :param execution_fee: Execution fee in wei
        :param order_type: GMX order type constant
        :param acceptable_price: Acceptable execution price
        :param mark_price: Current mark/trigger price
        :return: Tuple of order arguments for contract call
        """
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        # Use module-level constants
        eth_zero_address = ETH_ZERO_ADDRESS
        referral_code = ZERO_REFERRAL_CODE

        user_checksum = to_checksum_address(user_wallet_address)
        collateral_checksum = to_checksum_address(params.collateral_address)
        market_checksum = to_checksum_address(params.market_key)

        # Convert swap_path to checksum addresses
        swap_path_checksum = [to_checksum_address(addr) for addr in params.swap_path]

        # Size delta: already in GMX format (10^30 precision) from OrderArgumentParser
        # DO NOT multiply again - OrderArgumentParser already applied 10^30
        size_delta_usd = int(params.size_delta)

        # Collateral: already in token's smallest unit (from initial_collateral_delta_amount)
        collateral_amount = int(params.initial_collateral_delta_amount)

        return (
            (
                user_checksum,  # receiver
                user_checksum,  # cancellationReceiver
                eth_zero_address,  # callbackContract
                eth_zero_address,  # uiFeeReceiver
                market_checksum,  # market
                collateral_checksum,  # initialCollateralToken
                swap_path_checksum,  # swapPath
            ),
            (
                size_delta_usd,  # sizeDeltaUsd (30 decimals)
                collateral_amount,  # initialCollateralDeltaAmount (token decimals)
                mark_price,  # triggerPrice
                acceptable_price,  # acceptablePrice
                execution_fee,  # executionFee
                params.callback_gas_limit,  # Use param instead of hardcoded 0
                params.min_output_amount,  # Use param instead of hardcoded 0
                params.valid_from_time,  # Use param instead of hardcoded 0
            ),
            order_type,  # orderType
            DECREASE_POSITION_SWAP_TYPES["no_swap"],  # decreasePositionSwapType
            params.is_long,  # isLong
            True,  # shouldUnwrapNativeToken
            params.auto_cancel,  # autoCancel
            referral_code,  # referralCode
            params.data_list,  # dataList
        )

    def _build_multicall_args(
        self,
        params: OrderParams,
        arguments: tuple,
        execution_fee: int,
        is_close: bool,
    ) -> tuple[list, int]:
        """Build multicall arguments.

        This determines which tokens to send and in what amounts.

        :param params: Order parameters
        :param arguments: Order arguments tuple
        :param execution_fee: Execution fee in wei
        :param is_close: Whether this is a close position order
        :return: Tuple of (multicall_args list, value_amount)
        """
        value_amount = execution_fee

        # Get the native token address for this chain
        chain_tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not chain_tokens:
            raise ValueError(f"Unsupported chain: {self.chain}")

        if self.chain.lower() in ["arbitrum", "arbitrum_sepolia"]:
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() in ["avalanche", "avalanche_fuji"]:
            native_token_address = chain_tokens.get("WAVAX")
        else:
            raise ValueError(f"Unsupported chain: {self.chain}")

        # Check if collateral is the native token
        is_native = params.collateral_address.lower() == native_token_address.lower()

        # Get collateral amount from params
        collateral_amount = int(params.initial_collateral_delta_amount)

        # Debug logging for collateral token flow
        logger.info(
            "COLLATERAL_TRACE: BaseOrder._prepare_multicall()\n  collateral_address=%s\n  native_token_address=%s\n  is_native=%s\n  collateral_amount=%d wei\n  execution_fee=%d wei\n  is_close=%s",
            params.collateral_address,
            native_token_address,
            is_native,
            collateral_amount,
            execution_fee,
            is_close,
        )

        if is_native and not is_close:
            # Native token: include collateral in value
            value_amount = collateral_amount + execution_fee
            multicall_args = [
                self._send_wnt(value_amount),
                self._create_order(arguments),
            ]
        elif not is_close:
            # ERC20 token: send tokens separately
            multicall_args = [
                self._send_wnt(execution_fee),
                self._send_tokens(params.collateral_address, collateral_amount),
                self._create_order(arguments),
            ]
        else:
            # Closing position: only send execution fee
            multicall_args = [
                self._send_wnt(value_amount),
                self._create_order(arguments),
            ]

        # Debug logging for collateral token flow
        if is_native and not is_close:
            logger.info(
                "COLLATERAL_TRACE: Transaction value determined:\n  is_native=%s\n  is_close=%s\n  value_amount=%d wei (%.6f ETH)\n  breakdown: collateral=%d wei + execution_fee=%d wei",
                is_native,
                is_close,
                value_amount,
                value_amount / 1e18,
                collateral_amount,
                execution_fee,
            )
        else:
            logger.info(
                "COLLATERAL_TRACE: Transaction value determined:\n  is_native=%s\n  is_close=%s\n  value_amount=%d wei (%.6f ETH)\n  breakdown: execution_fee=%d wei only",
                is_native,
                is_close,
                value_amount,
                value_amount / 1e18,
                execution_fee,
            )

        return multicall_args, value_amount

    def _build_transaction(
        self,
        multicall_args: list,
        value_amount: int,
        gas_limit: int,
    ) -> TxParams:
        """Build the final unsigned transaction.

        :param multicall_args: List of encoded multicall arguments
        :param value_amount: ETH value to send with transaction
        :param gas_limit: Gas limit for transaction
        :return: Unsigned transaction parameters
        """
        user_address = self.config.get_wallet_address()
        if not user_address:
            raise ValueError("User wallet address required")

        nonce = self.web3.eth.get_transaction_count(to_checksum_address(user_address))
        gas_fees = estimate_gas_fees(self.web3)

        transaction: TxParams = {
            "from": to_checksum_address(user_address),
            "to": self.contract_addresses.exchangerouter,
            "data": encode_abi_compat(self._exchange_router_contract, "multicall", [multicall_args]),
            "value": value_amount,
            "gas": gas_limit,
            "chainId": self.chain_id,
            "nonce": nonce,
        }

        # Add EIP-1559 or legacy gas pricing
        if gas_fees.max_fee_per_gas is not None:
            transaction["maxFeePerGas"] = gas_fees.max_fee_per_gas
            transaction["maxPriorityFeePerGas"] = gas_fees.max_priority_fee_per_gas
        else:
            transaction["gasPrice"] = gas_fees.legacy_gas_price

        return transaction

    def _create_order(self, arguments: tuple) -> bytes:
        """Encode createOrder function call.

        :param arguments: Order arguments tuple
        :return: Encoded function call data
        """
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "createOrder",
            [arguments],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_tokens(self, token_address: str, amount: int) -> bytes:
        """Encode sendTokens function call.

        :param token_address: ERC20 token contract address
        :param amount: Amount of tokens to send (in smallest unit)
        :return: Encoded function call data
        """
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "sendTokens",
            [token_address, self.contract_addresses.ordervault, amount],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_wnt(self, amount: int) -> bytes:
        """Encode sendWnt function call.

        :param amount: Amount of native token to send (in wei)
        :return: Encoded function call data
        """
        hex_data = encode_abi_compat(
            self._exchange_router_contract,
            "sendWnt",
            [self.contract_addresses.ordervault, amount],
        )
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _check_for_approval(self, params: OrderParams) -> None:
        """Check token approval (from original check_for_approval).

        Verifies that the user has approved sufficient tokens for the order.
        Skips check for native tokens (WETH/WAVAX).

        :param params: Order parameters
        :raises ValueError: If insufficient token allowance
        """
        # Get the native token address for this chain
        chain_tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not chain_tokens:
            raise ValueError(f"Unsupported chain: {self.chain}")

        if self.chain.lower() in ["arbitrum", "arbitrum_sepolia"]:
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() in ["avalanche", "avalanche_fuji"]:
            native_token_address = chain_tokens.get("WAVAX")
        else:
            raise ValueError(f"Unsupported chain: {self.chain}")

        # Skip approval check for native token
        if params.collateral_address.lower() == native_token_address.lower():
            logger.debug("Native token - no approval needed")
            return

        # Check ERC20 approval
        user_address = self.config.get_wallet_address()
        # Skip approval check if no wallet is configured (for unit tests)
        if not user_address:
            logger.debug("No wallet address configured, skipping approval check")
            return

        token_details = fetch_erc20_details(self.web3, params.collateral_address, chain_id=self.chain_id)
        token_contract = token_details.contract

        allowance = token_contract.functions.allowance(
            to_checksum_address(user_address),
            self.contract_addresses.syntheticsrouter,
        ).call()

        required_amount = int(params.initial_collateral_delta_amount)

        if allowance < required_amount:
            required = required_amount / (10**token_details.decimals)
            current = allowance / (10**token_details.decimals)

            # Just log a warning - don't block transaction creation
            logger.warning(
                "Insufficient token allowance for %s. Required: %.4f, Current allowance: %.4f. User needs to approve tokens using: token.approve('%s', amount) before submitting the transaction.",
                token_details.symbol,
                required,
                current,
                self.contract_addresses.syntheticsrouter,
            )
        else:
            logger.debug("Token approval check passed: %.4f %s approved", allowance / (10**token_details.decimals), token_details.symbol)

    # New method to estimate price impact
    def _estimate_price_impact(
        self,
        params: OrderParams,
        market_data: dict,
        is_open: bool,
        is_close: bool,
        is_swap: bool,
    ) -> Optional[float]:
        """Estimate price impact for the order.

        This is an optional estimation that queries the GMX Reader contract.
        Returns None if estimation fails.

        GMX v2.2 Price Impact Changes:
        - Price impact is stored on position increase (pendingImpactAmount field)
        - Net price impact charged on position decrease = (decrease impact) + (stored impact * order.size / position.size)
        - Full uncapped price impact applied to executionPrice for acceptablePrice validation
        - Positive impact capped by position impact pool only on decrease (not increase)
        - Lendable impact configuration helps when pool insufficient to pay positive impact

        :param params: Order parameters
        :param market_data: Market data dictionary
        :param is_open: Whether opening a position
        :param is_close: Whether closing a position
        :param is_swap: Whether performing a swap
        :return: Estimated price impact in USD, or None if unavailable
        """
        # Skip price impact for swaps (handled differently)
        if is_swap:
            return None

        try:
            reader = get_reader_contract(self.web3, self.chain)
            # Use cached prices instead of fetching again
            prices = self._cached_prices if self._cached_prices is not None else self.oracle_prices.get_recent_prices()

            # Get index token price
            index_token_address = params.index_token_address
            if index_token_address not in prices:
                return None

            price_data = prices[index_token_address]
            index_token_price = (
                int(price_data["maxPriceFull"]),
                int(price_data["minPriceFull"]),
            )

            # Get long and short token prices for MarketUtils.MarketPrices
            long_token_address = market_data["long_token_address"]
            short_token_address = market_data["short_token_address"]

            # Get long token price
            if long_token_address in prices:
                long_price_data = prices[long_token_address]
                long_token_price = (
                    int(long_price_data["maxPriceFull"]),
                    int(long_price_data["minPriceFull"]),
                )
            else:
                # Fallback to index token price if long token price not available
                long_token_price = index_token_price

            # Get short token price
            if short_token_address in prices:
                short_price_data = prices[short_token_address]
                short_token_price = (
                    int(short_price_data["maxPriceFull"]),
                    int(short_price_data["minPriceFull"]),
                )
            else:
                # Fallback to index token price if short token price not available
                short_token_price = index_token_price

            # Build MarketUtils.MarketPrices struct for v2.2
            market_prices = (
                index_token_price,  # indexTokenPrice
                long_token_price,  # longTokenPrice
                short_token_price,  # shortTokenPrice
            )

            # Calculate position size in tokens
            decimals = market_data["market_metadata"]["decimals"]
            median_price = median(
                [float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])],
            )

            # size_delta is already in 10^30 format from OrderArgumentParser
            size_delta_usd = int(params.size_delta)
            position_size_in_tokens = int(params.size_delta / median_price)

            # Query reader contract for execution price and impact (v2.2 format)
            # New signature: getExecutionPrice(dataStore, market, prices, positionSizeInUsd, positionSizeInTokens, sizeDeltaUsd, pendingImpactAmount, isLong)
            result = reader.functions.getExecutionPrice(
                self.contract_addresses.datastore,
                params.market_key,
                market_prices,  # MarketUtils.MarketPrices (v2.2 change)
                0,  # positionSizeInUsd (we use sizeDeltaUsd)
                position_size_in_tokens,
                size_delta_usd,
                0,  # pendingImpactAmount (v2.2 new parameter - 0 for new positions)
                params.is_long,
            ).call()

            # Result is ExecutionPriceResult struct (v2.2):
            # (priceImpactUsd, executionPrice, balanceWasImproved, proportionalPendingImpactUsd, totalImpactUsd, priceImpactDiffUsd)
            price_impact_usd = result[0] / (10**PRECISION)

            return price_impact_usd

        except Exception as e:
            logger.warning("Could not estimate price impact: %s", e)
            return None
