"""
GMX Base Order Implementation

Base class for GMX order management including enums, data structures, and base
order implementations. Provides transaction building for GMX decentralised trading.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from decimal import Decimal
from enum import Enum
from statistics import median

from eth_utils import to_checksum_address
from web3.types import TxParams

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_exchange_router_contract, NETWORK_TOKENS, get_datastore_contract
from eth_defi.gmx.constants import PRECISION, ORDER_TYPES, DECREASE_POSITION_SWAP_TYPES, GAS_LIMITS, ETH_ZERO_ADDRESS
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat
from eth_defi.gmx.gas_utils import get_gas_limits
from eth_defi.token import fetch_erc20_details


# Module-level constants
ZERO_REFERRAL_CODE = bytes.fromhex("0" * 64)


class OrderType(Enum):
    """GMX Order Types with contract values."""

    SWAP = 0
    SHIFT = 1
    ATOMIC_WITHDRAWAL = 2
    DEPOSIT = 3
    WITHDRAWAL = 4
    ATOMIC_SWAP = 5


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
    execution_buffer: float = 1.3

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
    """

    transaction: TxParams
    execution_fee: int
    acceptable_price: int
    mark_price: float
    gas_limit: int
    estimated_price_impact: Optional[float] = None  # Added price impact


class BaseOrder:
    """Base GMX Order class.

    Creates unsigned transactions that can be signed later by the user.
    Compatible with CCXT trading interface patterns for easy migration.
    """

    def __init__(self, config: GMXConfig):
        """Initialize the base order with GMX configuration.

        :param config: GMX configuration instance
        :type config: GMXConfig
        """
        self._oracle_prices = None
        self._markets = None
        self.config = config
        self.chain = config.get_chain()
        self.web3 = config.web3
        self.chain_id = config.web3.eth.chain_id
        self.contract_addresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(self.web3, self.chain)
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize order type constants
        self._order_types = ORDER_TYPES

        # Initialize gas limits from datastore
        self._initialize_gas_limits()

        self.logger.debug(f"Initialized {self.__class__.__name__} for {self.chain}")

    # New method to initialize gas limits
    def _initialize_gas_limits(self):
        """Load gas limits from GMX datastore contract.

        Falls back to default constants if datastore query fails.
        """
        try:
            datastore = get_datastore_contract(self.web3, self.chain)
            self._gas_limits = get_gas_limits(datastore)
            self.logger.debug("Gas limits loaded from datastore contract")
        except Exception as e:
            self.logger.warning(f"Failed to load gas limits from datastore: {e}")
            # Fallback to default gas limits from constants
            self._gas_limits = GAS_LIMITS.copy()
            self.logger.debug("Using fallback gas limits from constants")

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
    ) -> OrderResult:
        """Build an order transaction.

        Core method that constructs an unsigned transaction for GMX orders.
        This replaces the original SDK's order_builder that submitted transactions.

        :param params: Order parameters
        :param is_open: Whether opening a position
        :param is_close: Whether closing a position
        :param is_swap: Whether performing a swap
        :return: OrderResult with unsigned transaction
        """
        # Determine gas limits (from original determine_gas_limits)
        if is_open:
            order_type = self._order_types["market_increase"]
        elif is_close:
            order_type = self._order_types["market_decrease"]
        elif is_swap:
            order_type = self._order_types["market_swap"]
        else:
            order_type = self._order_types["market_increase"]

        # Get market and price data first (validate market exists before other operations)
        markets = self.markets.get_available_markets()
        prices = self.oracle_prices.get_recent_prices()

        market_data = markets.get(params.market_key)
        if not market_data:
            raise ValueError(f"Market {params.market_key} not found")

        # Calculate prices with slippage (validate prices exist before other operations)
        decimals = market_data["market_metadata"]["decimals"]
        price, acceptable_price, acceptable_price_in_usd = self._get_prices(
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
        execution_fee = int(gas_limits["total"] * gas_price)
        execution_fee = int(execution_fee * params.execution_buffer)

        # Check approval if not closing (after market and price validation)
        if not is_close:
            self._check_for_approval(params)

        # Build order arguments (from original _create_order)
        mark_price = int(price) if is_open else 0
        acceptable_price_val = acceptable_price if not is_swap else 0

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

        # Estimate price impact (optional, may return None)
        price_impact = self._estimate_price_impact(
            params,
            market_data,
            is_open,
            is_close,
            is_swap,
        )

        return OrderResult(
            transaction=transaction,
            execution_fee=execution_fee,
            acceptable_price=acceptable_price_val,
            mark_price=price,
            gas_limit=gas_limits["total"],
            estimated_price_impact=price_impact,
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
    ) -> tuple[float, int, float]:
        """Calculate prices with slippage.

        :param decimals: Token decimals
        :param prices: Oracle prices dictionary
        :param params: Order parameters
        :param is_open: Whether opening a position
        :param is_close: Whether closing a position
        :param is_swap: Whether performing a swap
        :return: Tuple of (price, acceptable_price, acceptable_price_in_usd)
        """
        self.logger.debug("Getting prices...")

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
            slippage_price = 0

        acceptable_price = int(slippage_price)
        acceptable_price_in_usd = acceptable_price * (10 ** (decimals - PRECISION))

        self.logger.debug(f"Mark Price: ${price * (10 ** (decimals - PRECISION)):.4f}")
        if acceptable_price_in_usd != 0:
            self.logger.debug(f"Acceptable price: ${acceptable_price_in_usd:.4f}")

        return price, acceptable_price, acceptable_price_in_usd

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

        # Size delta: position size in USD with 30 decimals of precision
        size_delta_usd = int(Decimal(str(params.size_delta)) * Decimal(10**30))

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

        if self.chain.lower() == "arbitrum":
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() == "avalanche":
            native_token_address = chain_tokens.get("WAVAX")
        else:
            raise ValueError(f"Unsupported chain: {self.chain}")

        # Check if collateral is the native token
        is_native = params.collateral_address.lower() == native_token_address.lower()

        # Get collateral amount from params
        collateral_amount = int(params.initial_collateral_delta_amount)

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

        if self.chain.lower() == "arbitrum":
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() == "avalanche":
            native_token_address = chain_tokens.get("WAVAX")
        else:
            raise ValueError(f"Unsupported chain: {self.chain}")

        # Skip approval check for native token
        if params.collateral_address.lower() == native_token_address.lower():
            self.logger.debug("Native token - no approval needed")
            return

        # Check ERC20 approval
        user_address = self.config.get_wallet_address()
        # Skip approval check if no wallet is configured (for unit tests)
        if not user_address:
            self.logger.debug("No wallet address configured, skipping approval check")
            return

        token_details = fetch_erc20_details(self.web3, params.collateral_address)
        token_contract = token_details.contract

        allowance = token_contract.functions.allowance(
            to_checksum_address(user_address),
            self.contract_addresses.syntheticsrouter,
        ).call()

        required_amount = int(params.initial_collateral_delta_amount)

        if allowance < required_amount:
            raise ValueError(
                f"Insufficient token allowance for {token_details.symbol}. Required: {required_amount / (10**token_details.decimals):.4f}, Current allowance: {allowance / (10**token_details.decimals):.4f}. Please approve tokens first using: token.approve('{self.contract_addresses.syntheticsrouter}', amount)",
            )

        self.logger.debug(
            f"Token approval check passed: {allowance / (10**token_details.decimals):.4f} {token_details.symbol} approved",
        )

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
            from eth_defi.gmx.contracts import get_reader_contract

            reader = get_reader_contract(self.web3, self.chain)
            prices = self.oracle_prices.get_recent_prices()

            # Get index token price
            index_token_address = params.index_token_address
            if index_token_address not in prices:
                return None

            price_data = prices[index_token_address]
            index_token_price = (
                int(price_data["maxPriceFull"]),
                int(price_data["minPriceFull"]),
            )

            # Calculate position size in tokens
            decimals = market_data["market_metadata"]["decimals"]
            median_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])

            size_delta_usd = int(Decimal(str(params.size_delta)) * Decimal(10**PRECISION))
            position_size_in_tokens = int((params.size_delta * (10**PRECISION)) / median_price)

            # Query reader contract for execution price and impact
            result = reader.functions.getExecutionPrice(
                self.contract_addresses.datastore,
                params.market_key,
                index_token_price,
                0,  # positionSizeInUsd (we use sizeDeltaUsd)
                position_size_in_tokens,
                size_delta_usd,
                params.is_long,
            ).call()

            # Result is tuple: (priceImpactUsd, priceImpactDiffUsd, executionPrice)
            price_impact_usd = result[0] / (10**PRECISION)

            return price_impact_usd

        except Exception as e:
            self.logger.warning(f"Could not estimate price impact: {e}")
            return None
