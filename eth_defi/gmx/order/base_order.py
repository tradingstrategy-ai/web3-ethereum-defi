"""
Library for GMX-based order management including enums, data structures, and base
order implementations. Provides CCXT-compatible interfaces for handling transactions
in GMX decentralized trading.

This module includes:
- Order types and sides
- Swap types for GMX
- Dataclasses for order parameters and transaction results
- A base order class for creating transactions
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Any
from decimal import Decimal
from enum import Enum

from statistics import median

from cchecksum import to_checksum_address
from web3.types import TxParams
from eth_typing import ChecksumAddress

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_exchange_router_contract, NETWORK_TOKENS
from eth_defi.gmx.constants import PRECISION, ORDER_TYPES, DECREASE_POSITION_SWAP_TYPES, GAS_LIMITS
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat


class OrderType(Enum):
    """GMX Order Types mapped to CCXT style."""

    MARKET = "market"
    LIMIT = "limit"
    MARKET_INCREASE = "market_increase"
    MARKET_DECREASE = "market_decrease"
    MARKET_SWAP = "market_swap"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class OrderSide(Enum):
    """Order side."""

    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"


class SwapType(Enum):
    """Position swap types."""

    NO_SWAP = 0
    SWAP_PNL_TOKEN_TO_COLLATERAL_TOKEN = 1
    SWAP_COLLATERAL_TOKEN_TO_PNL_TOKEN = 2


@dataclass
class OrderParams:
    """Order parameters structure.

    :param symbol: Market symbol (e.g., "ETH/USD")
    :type symbol: str
    :param type: Order type
    :type type: OrderType | str
    :param side: Order side (buy/sell, long/short)
    :type side: OrderSide | str
    :param amount: Position size in USD
    :type amount: Decimal | float | int
    :param price: Limit price (if applicable)
    :type price: Optional[Decimal | float]
    :param market_key: Market address
    :type market_key: Optional[str]
    :param collateral_address: Collateral token address
    :type collateral_address: Optional[str]
    :param index_token_address: Index token address
    :type index_token_address: Optional[str]
    :param is_long: Position direction
    :type is_long: bool
    :param slippage_percent: Default 0.5% slippage
    :type slippage_percent: float
    :param swap_path: Swap path for multi-hop swaps
    :type swap_path: list[ChecksumAddress]
    :param execution_fee_buffer: Execution fee buffer multiplier
    :type execution_fee_buffer: float
    :param auto_cancel: Auto cancel orders
    :type auto_cancel: bool
    :param min_output_amount: Minimum output for swaps
    :type min_output_amount: int
    :param max_fee_per_gas: Maximum fee per gas
    :type max_fee_per_gas: Optional[int]
    :param max_priority_fee_per_gas: Maximum priority fee per gas
    :type max_priority_fee_per_gas: Optional[int]
    :param gas_limit: Gas limit for transaction
    :type gas_limit: Optional[int]
    :param client_order_id: Client order ID
    :type client_order_id: Optional[str]
    :param metadata: Additional metadata
    :type metadata: dict[str, Any]
    """

    symbol: str  # Market symbol (e.g., "ETH/USD")
    type: OrderType | str  # Order type
    side: OrderSide | str  # Order side (buy/sell, long/short)
    amount: Decimal | float | int  # Position size in USD
    price: Optional[Decimal | float] = None  # Limit price (if applicable)

    # GMX-specific parameters
    market_key: Optional[str] = None  # Market address
    collateral_address: Optional[str] = None  # Collateral token address
    index_token_address: Optional[str] = None  # Index token address
    is_long: bool = True  # Position direction
    slippage_percent: float = 0.005  # Default 0.5% slippage
    swap_path: list[ChecksumAddress] = field(default_factory=list)

    # Execution parameters
    execution_fee_buffer: float = 1.3  # Execution fee buffer multiplier
    auto_cancel: bool = False  # Auto cancel orders
    min_output_amount: int = 0  # Minimum output for swaps

    # Gas and fee parameters
    max_fee_per_gas: Optional[int] = None
    max_priority_fee_per_gas: Optional[int] = None
    gas_limit: Optional[int] = None

    # Additional metadata
    client_order_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransactionResult:
    """Result of order creation containing unsigned transaction and metadata.

    :param transaction: Unsigned transaction ready for signing
    :type transaction: TxParams
    :param order_type: Order type
    :type order_type: OrderType
    :param symbol: Market symbol
    :type symbol: str
    :param side: Order side
    :type side: OrderSide
    :param amount: Position size
    :type amount: Decimal | float | int
    :param estimated_execution_fee: Estimated execution fee
    :type estimated_execution_fee: int
    :param market_info: Market information
    :type market_info: dict[str, Any]
    :param gas_estimates: Gas estimates
    :type gas_estimates: dict[str, int]
    :param acceptable_price: Acceptable price for execution
    :type acceptable_price: int
    :param mark_price: Current mark price
    :type mark_price: float
    :param slippage_percent: Slippage percentage
    :type slippage_percent: float
    """

    # Unsigned transaction ready for signing
    transaction: TxParams

    # Order metadata
    order_type: OrderType
    symbol: str
    side: OrderSide
    amount: Decimal | float | int
    estimated_execution_fee: int
    market_info: dict[str, Any] = field(default_factory=dict)
    gas_estimates: dict[str, int] = field(default_factory=dict)

    # Additional order details for reference
    acceptable_price: int = 0
    mark_price: float = 0
    slippage_percent: float = 0.005


class BaseOrder:
    """Base GMX Order class migrated from gmx_python_sdk.

    Creates unsigned transactions that can be signed later by the user.
    Compatible with CCXT trading interface patterns for easy migration.
    """

    def __init__(self, config: GMXConfig):
        """Initialize the base order with GMX configuration.

        :param config: GMX configuration containing Web3 instance and chain settings
        :type config: GMXConfig
        """
        self.config = config
        self.web3 = config.web3
        self.chain = config.get_chain()
        self.chain_id = config.web3.eth.chain_id

        # Initialize logger
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self.logger.info(f"Creating order manager for {self.chain}...")

        # Core data providers
        self.markets = Markets(config)
        self.oracle_prices = OraclePrices(self.chain)

        # Contract instances
        self.contract_addresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(self.web3, self.chain)

        # Gas limit mappings for different order types
        self._gas_limits = GAS_LIMITS

        # Order type mappings
        self._order_types = ORDER_TYPES

        # Initialize execution fee buffer
        self.execution_buffer = 1.3  # Anything less than this doesn't

        self.logger.info(f"Initialized order manager for {self.chain}")

    # CCXT-compatible method names for easy migration
    def create_market_buy_order(
        self,
        symbol: str,
        amount: float | int,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> TransactionResult:
        """Create market buy order (CCXT compatible).

        :param symbol: Market symbol
        :type symbol: str
        :param amount: Position size
        :type amount: float | int
        :param price: Limit price (optional)
        :type price: Optional[float]
        :param params: Additional parameters
        :type params: Optional[dict]
        :return: Transaction result
        :rtype: TransactionResult
        """
        order_params = OrderParams(
            symbol=symbol,
            type=OrderType.MARKET_INCREASE,
            side=OrderSide.BUY,
            amount=amount,
            price=price,
            is_long=True,
            **(params or {}),
        )
        return self.create_order(order_params)

    def create_market_sell_order(
        self,
        symbol: str,
        amount: float | int,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> TransactionResult:
        """Create market sell order (CCXT compatible).

        :param symbol: Market symbol
        :type symbol: str
        :param amount: Position size
        :type amount: float | int
        :param price: Limit price (optional)
        :type price: Optional[float]
        :param params: Additional parameters
        :type params: Optional[dict]
        :return: Transaction result
        :rtype: TransactionResult
        """
        order_params = OrderParams(
            symbol=symbol,
            type=OrderType.MARKET_DECREASE,
            side=OrderSide.SELL,
            amount=amount,
            price=price,
            is_long=True,
            **(params or {}),
        )
        return self.create_order(order_params)

    def create_limit_buy_order(
        self,
        symbol: str,
        amount: float | int,
        price: float,
        params: Optional[dict] = None,
    ) -> TransactionResult:
        """Create limit buy order (CCXT compatible).

        :param symbol: Market symbol
        :type symbol: str
        :param amount: Position size
        :type amount: float | int
        :param price: Limit price
        :type price: float
        :param params: Additional parameters
        :type params: Optional[dict]
        :return: Transaction result
        :rtype: TransactionResult
        """
        order_params = OrderParams(
            symbol=symbol,
            type=OrderType.LIMIT,
            side=OrderSide.BUY,
            amount=amount,
            price=price,
            is_long=True,
            **(params or {}),
        )
        return self.create_order(order_params)

    def create_limit_sell_order(
        self,
        symbol: str,
        amount: float | int,
        price: float,
        params: Optional[dict] = None,
    ) -> TransactionResult:
        """Create limit sell order (CCXT compatible).

        :param symbol: Market symbol
        :type symbol: str
        :param amount: Position size
        :type amount: float | int
        :param price: Limit price
        :type price: float
        :param params: Additional parameters
        :type params: Optional[dict]
        :return: Transaction result
        :rtype: TransactionResult
        """
        order_params = OrderParams(
            symbol=symbol,
            type=OrderType.LIMIT,
            side=OrderSide.SELL,
            amount=amount,
            price=price,
            is_long=True,
            **(params or {}),
        )
        return self.create_order(order_params)

    def create_order(self, params: OrderParams) -> TransactionResult:
        """Create an order transaction (main method).

        :param params: Order parameters
        :type params: OrderParams
        :return: TransactionResult with unsigned transaction and metadata
        :rtype: TransactionResult
        """
        self.validate_params(params)

        # Determine order characteristics
        is_open = params.type in [OrderType.MARKET_INCREASE, OrderType.LIMIT]
        is_close = params.type in [OrderType.MARKET_DECREASE]
        is_swap = params.type == OrderType.MARKET_SWAP

        return self._build_order_transaction(params, is_open, is_close, is_swap)

    def validate_params(self, params: OrderParams) -> None:
        """Validate order parameters.

        :param params: Order parameters to validate
        :type params: OrderParams
        :raises ValueError: If parameters are invalid
        """
        if not params.symbol:
            raise ValueError("Symbol is required")

        if not params.type:
            raise ValueError("Order type is required")

        if not params.side:
            raise ValueError("Order side is required")

        if params.amount <= 0:
            raise ValueError("Amount must be positive")

        # Validate market exists
        markets = self.markets.get_available_markets()
        market_found = False
        for market_addr, market_data in markets.items():
            if market_data.get("market_symbol", "").upper() in params.symbol.upper():
                params.market_key = market_addr
                params.index_token_address = market_data.get("index_token_address")
                market_found = True
                break

        if not market_found:
            available_symbols = [data.get("market_symbol", "") for data in markets.values()]
            raise ValueError(f"Invalid market symbol: {params.symbol}. Available: {available_symbols}")

    def _build_order_transaction(
        self,
        params: OrderParams,
        is_open: bool,
        is_close: bool,
        is_swap: bool,
    ) -> TransactionResult:
        """Build the order transaction (core logic from original gmx_python_sdk).

        :param params: Order parameters
        :type params: OrderParams
        :param is_open: Whether this is an opening order
        :type is_open: bool
        :param is_close: Whether this is a closing order
        :type is_close: bool
        :param is_swap: Whether this is a swap order
        :type is_swap: bool
        :return: TransactionResult with transaction and metadata
        :rtype: TransactionResult
        """
        # Get gas limits and execution fee
        gas_limits = self._determine_gas_limits(params.type)

        # Get the current gas price and estimate execution fee
        gas_price = self.web3.eth.gas_price
        execution_fee = int(gas_limits["execution"] * gas_price)
        execution_fee = int(execution_fee * self.execution_buffer)

        # Get market and price data
        markets = self.markets.get_available_markets()
        market_data = markets[params.market_key]
        prices = self.oracle_prices.get_recent_prices()

        # Set default collateral if not specified
        if not params.collateral_address:
            if is_close:
                # For closing, use the market's long token as collateral
                params.collateral_address = market_data["long_token_address"]
            else:
                # For opening, default to USDC
                params.collateral_address = market_data["short_token_address"]  # Usually USDC

        # Calculate prices and slippage
        decimals = market_data["market_metadata"]["decimals"]
        price, acceptable_price, acceptable_price_in_usd = self._get_prices(decimals, prices, params, is_open, is_close, is_swap)

        # Determine order type code
        if is_open:
            order_type = self._order_types["market_increase"]
        elif is_close:
            order_type = self._order_types["market_decrease"]
        elif is_swap:
            order_type = self._order_types["market_swap"]
        else:
            order_type = self._order_types["limit_increase"]

        # Build transaction arguments (following original structure)
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required for transaction building")

        arguments = self._build_order_arguments(params, market_data, execution_fee, order_type, acceptable_price, price if is_open else 0, user_wallet_address)

        # Build multicall transaction
        multicall_args, value_amount = self._build_multicall_args(params, arguments, execution_fee, is_close)

        # Create the transaction
        transaction = self._build_transaction(multicall_args, value_amount, gas_limits["total"])

        return TransactionResult(
            transaction=transaction,
            order_type=params.type if isinstance(params.type, OrderType) else OrderType(params.type),
            symbol=params.symbol,
            side=params.side if isinstance(params.side, OrderSide) else OrderSide(params.side),
            amount=params.amount,
            estimated_execution_fee=execution_fee,
            market_info=market_data,
            gas_estimates=gas_limits,
            acceptable_price=acceptable_price,
            mark_price=price,
            slippage_percent=params.slippage_percent,
        )

    def _determine_gas_limits(self, order_type: OrderType | str) -> dict[str, int]:
        """Determine gas limits for the order type.

        :param order_type: Order type
        :type order_type: OrderType | str
        :return: Gas limits dictionary
        :rtype: dict[str, int]
        """
        if isinstance(order_type, str):
            order_type = OrderType(order_type)

        if order_type == OrderType.MARKET_INCREASE:
            execution_gas = self._gas_limits["increase_order"]
        elif order_type == OrderType.MARKET_DECREASE:
            execution_gas = self._gas_limits["decrease_order"]
        elif order_type == OrderType.MARKET_SWAP:
            execution_gas = self._gas_limits["swap_order"]
        else:
            execution_gas = self._gas_limits["increase_order"]

        return {
            "execution": execution_gas,
            "total": execution_gas + 200000,  # Buffer for multicall overhead
        }

    def _get_prices(
        self,
        decimals: int,
        prices: dict,
        params: OrderParams,
        is_open: bool,
        is_close: bool,
        is_swap: bool,
    ) -> tuple[float, int, float]:
        """Calculate prices with slippage.

        :param decimals: Token decimals
        :type decimals: int
        :param prices: Price data
        :type prices: dict
        :param params: Order parameters
        :type params: OrderParams
        :param is_open: Whether this is an opening order
        :type is_open: bool
        :param is_close: Whether this is a closing order
        :type is_close: bool
        :param is_swap: Whether this is a swap order
        :type is_swap: bool
        :return: Tuple of (price, acceptable_price, acceptable_price_in_usd)
        :rtype: tuple[float, int, float]
        """
        self.logger.info("Getting prices...")

        if params.index_token_address not in prices:
            raise ValueError(f"Price not available for token {params.index_token_address}")

        price_data = prices[params.index_token_address]
        price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])

        # Calculate slippage based on position type and action
        if is_open:
            if params.is_long:
                slippage_price = price + (price * params.slippage_percent)
            else:
                slippage_price = price - (price * params.slippage_percent)
        elif is_close:
            if params.is_long:
                slippage_price = price - (price * params.slippage_percent)
            else:
                slippage_price = price + (price * params.slippage_percent)
        else:
            slippage_price = price

        acceptable_price = int(slippage_price)
        acceptable_price_in_usd = acceptable_price * (10 ** (decimals - PRECISION))  # GMX precision

        self.logger.info(f"Mark Price: ${price * (10 ** (decimals - PRECISION)):.4f}")
        if acceptable_price_in_usd != price * (10 ** (decimals - PRECISION)):
            self.logger.info(f"Acceptable price: ${acceptable_price_in_usd:.4f}")

        return price, acceptable_price, acceptable_price_in_usd

    @staticmethod
    def _build_order_arguments(
        params: OrderParams,
        market_data: dict,
        execution_fee: int,
        order_type: int,
        acceptable_price: int,
        mark_price: float,
        user_wallet_address: str,
    ) -> tuple:
        """Build order arguments tuple (from original structure).

        :param params: Order parameters
        :type params: OrderParams
        :param market_data: Market data
        :type market_data: dict
        :param execution_fee: Execution fee
        :type execution_fee: int
        :param order_type: Order type code
        :type order_type: int
        :param acceptable_price: Acceptable price
        :type acceptable_price: int
        :param mark_price: Mark price
        :type mark_price: float
        :param user_wallet_address: User wallet address
        :type user_wallet_address: str
        :return: Order arguments tuple
        :rtype: tuple
        """

        eth_zero_address = "0x0000000000000000000000000000000000000000"
        referral_code = bytes.fromhex("0000000000000000000000000000000000000000000000000000000000000000")

        # Convert addresses to checksum format
        user_wallet_address = to_checksum_address(user_wallet_address)
        collateral_address = to_checksum_address(params.collateral_address)

        return (
            (
                user_wallet_address,  # receiver
                user_wallet_address,  # cancellation receiver
                eth_zero_address,  # callback contract
                eth_zero_address,  # ui fee receiver
                params.market_key,  # market
                collateral_address,  # initial collateral token
                params.swap_path or [],  # swap path
            ),
            (
                int(params.amount),  # size delta
                int(params.amount) if not params.type == OrderType.MARKET_DECREASE else int(params.amount),  # initial collateral delta
                int(mark_price),  # trigger price
                acceptable_price,  # acceptable price
                execution_fee,  # execution fee
                0,  # callback gas limit
                params.min_output_amount,  # min output amount
                0,  # updated position size
            ),
            order_type,  # order type
            DECREASE_POSITION_SWAP_TYPES["no_swap"],  # decrease position swap type
            params.is_long,  # is long
            True,  # should unwrap native token
            params.auto_cancel,  # auto cancel
            referral_code,  # referral code
        )

    def _build_multicall_args(
        self,
        params: OrderParams,
        arguments: tuple,
        execution_fee: int,
        is_close: bool,
    ) -> tuple[list, int]:
        """Build multicall arguments and determine value amount.

        :param params: Order parameters
        :type params: OrderParams
        :param arguments: Order arguments tuple
        :type arguments: tuple
        :param execution_fee: Execution fee
        :type execution_fee: int
        :param is_close: Whether this is a closing order
        :type is_close: bool
        :return: Tuple of (multicall_args, value_amount)
        :rtype: tuple[list, int]
        """

        value_amount = execution_fee

        # Get native token address from NETWORK_TOKENS
        chain_tokens = NETWORK_TOKENS.get(self.chain.lower())
        if not chain_tokens:
            raise ValueError(f"Unsupported chain for native token lookup: {self.chain}")

        # Get native wrapped token (WETH for arbitrum, WAVAX for avalanche)
        native_token_address = None
        if self.chain.lower() == "arbitrum":
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() == "avalanche":
            native_token_address = chain_tokens.get("WAVAX")

        if not native_token_address:
            raise ValueError(f"Native token not found for chain {self.chain}")

        if params.collateral_address != native_token_address and not is_close:
            # Non-native token: send tokens separately
            multicall_args = [
                self._send_wnt(execution_fee),
                self._send_tokens(params.collateral_address, int(params.amount)),
                self._create_order(arguments),
            ]
        else:
            # Native token or closing position
            if not is_close and params.type != OrderType.MARKET_DECREASE:
                value_amount = int(params.amount) + execution_fee

            multicall_args = [
                self._send_wnt(value_amount),
                self._create_order(arguments),
            ]

        return multicall_args, value_amount

    def _build_transaction(self, multicall_args: list, value_amount: int, gas_limit: int) -> TxParams:
        """Build the final unsigned transaction.

        :param multicall_args: Multicall arguments
        :type multicall_args: list
        :param value_amount: Value amount to send
        :type value_amount: int
        :param gas_limit: Gas limit
        :type gas_limit: int
        :return: Unsigned transaction parameters
        :rtype: TxParams
        """

        # Get gas pricing
        gas_fees = estimate_gas_fees(self.web3)

        transaction: TxParams = {
            "to": self.contract_addresses.exchangerouter,
            "data": encode_abi_compat(self._exchange_router_contract, "multicall", [multicall_args]),
            "value": value_amount,
            "gas": gas_limit,
            "chainId": self.chain_id,
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

        :param arguments: Order arguments
        :type arguments: tuple
        :return: Encoded function call
        :rtype: bytes
        """
        return bytes.fromhex(encode_abi_compat(self._exchange_router_contract, "createOrder", [arguments]))

    def _send_tokens(self, token_address: str, amount: int) -> bytes:
        """Encode sendTokens function call.

        :param token_address: Token address
        :type token_address: str
        :param amount: Token amount
        :type amount: int
        :return: Encoded function call
        :rtype: bytes
        """
        return bytes.fromhex(encode_abi_compat(self._exchange_router_contract, "sendTokens", [token_address, self.contract_addresses.ordervault, amount]))

    def _send_wnt(self, amount: int) -> bytes:
        """Encode sendWnt function call.

        :param amount: Amount to send
        :type amount: int
        :return: Encoded function call
        :rtype: bytes
        """
        return bytes.fromhex(encode_abi_compat(self._exchange_router_contract, "sendWnt", [self.contract_addresses.ordervault, amount]))

    # Additional utility methods for compatibility
    def fetch_markets(self) -> dict[str, dict]:
        """Fetch available markets (CCXT compatible).

        :return: Dictionary of available markets
        :rtype: dict[str, dict]
        """
        markets = self.markets.get_available_markets()

        # Convert to CCXT-like format
        ccxt_markets = {}
        for market_addr, market_data in markets.items():
            symbol = market_data.get("market_symbol", "") + "/USD"
            ccxt_markets[symbol] = {"id": market_addr, "symbol": symbol, "base": market_data.get("market_symbol", ""), "quote": "USD", "active": True, "type": "perpetual", "info": market_data}

        return ccxt_markets

    def fetch_ticker(self, symbol: str) -> dict:
        """Fetch ticker for symbol (CCXT compatible).

        :param symbol: Market symbol
        :type symbol: str
        :return: Ticker data
        :rtype: dict
        :raises ValueError: If market or price data not found
        """
        markets = self.markets.get_available_markets()
        prices = self.oracle_prices.get_recent_prices()

        # Find market by symbol
        market_data = None
        for addr, data in markets.items():
            if data.get("market_symbol", "") + "/USD" == symbol:
                market_data = data
                break

        if not market_data:
            raise ValueError(f"Market {symbol} not found")

        token_address = market_data["index_token_address"]
        if token_address in prices:
            price_data = prices[token_address]
            return {"symbol": symbol, "high": float(price_data.get("maxPriceFull", 0)) / 1e30, "low": float(price_data.get("minPriceFull", 0)) / 1e30, "bid": float(price_data.get("minPriceFull", 0)) / 1e30, "ask": float(price_data.get("maxPriceFull", 0)) / 1e30, "last": median([float(price_data.get("maxPriceFull", 0)), float(price_data.get("minPriceFull", 0))]) / 1e30, "info": price_data}

        raise ValueError(f"Price data not available for {symbol}")
