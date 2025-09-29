"""
Library for GMX-based order management including enums, data structures, and base
order implementations. Provides transaction building for GMX decentralised trading.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Any
from decimal import Decimal
from enum import Enum
from statistics import median

from eth_utils import to_checksum_address
from web3.types import TxParams

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_exchange_router_contract, NETWORK_TOKENS
from eth_defi.gmx.constants import PRECISION, ORDER_TYPES, DECREASE_POSITION_SWAP_TYPES, GAS_LIMITS
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gas import estimate_gas_fees
from eth_defi.compat import encode_abi_compat
from eth_defi.token import fetch_erc20_details


class OrderType(Enum):
    """GMX Order Types with contract values."""

    MARKET_SWAP = 0
    LIMIT_SWAP = 1
    MARKET_INCREASE = 2
    LIMIT_INCREASE = 3
    MARKET_DECREASE = 4
    LIMIT_DECREASE = 5
    STOP_LOSS_DECREASE = 6
    LIQUIDATION = 7


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


@dataclass
class OrderResult:
    """Result of order creation containing unsigned transaction.

    :param transaction: Unsigned transaction ready for signing
    :param execution_fee: Estimated execution fee in wei
    :param acceptable_price: Acceptable price for execution
    :param mark_price: Current mark price
    :param gas_limit: Gas limit for transaction
    """

    transaction: TxParams
    execution_fee: int
    acceptable_price: int
    mark_price: float
    gas_limit: int


class BaseOrder:
    """Base GMX Order class.

    Creates unsigned transactions that can be signed later by the user.
    Compatible with CCXT trading interface patterns for easy migration.
    """

    def __init__(self, config: GMXConfig):
        """Initialize the base order with GMX configuration.

        :param config: GMX configuration containing Web3 instance and chain settings
        """
        self.config = config
        self.web3 = config.web3
        self.chain = config.get_chain()
        self.chain_id = config.web3.eth.chain_id

        # Initialize logger
        self.logger = logging.getLogger(f"{self.__class__.__name__}")

        self.logger.info(f"Creating order manager for {self.chain}...")

        # Core data providers (same as original)
        self.markets = Markets(config)
        self.oracle_prices = OraclePrices(self.chain)

        # Contract instances
        self.contract_addresses = get_contract_addresses(self.chain)
        self._exchange_router_contract = get_exchange_router_contract(self.web3, self.chain)

        # Gas limits and constants
        self._gas_limits = GAS_LIMITS
        self._order_types = ORDER_TYPES

        self.logger.info(f"Initialized order manager for {self.chain}")

    def create_order(
        self,
        params: OrderParams,
        is_open: bool = False,
        is_close: bool = False,
        is_swap: bool = False,
    ) -> OrderResult:
        """
        Create an order transaction.

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

        # Get execution fee (from original)
        gas_price = self.web3.eth.gas_price
        gas_limits = self._determine_gas_limits(is_open, is_close, is_swap)
        execution_fee = int(gas_limits["total"] * gas_price)
        execution_fee = int(execution_fee * params.execution_buffer)

        # Check approval if not closing
        if not is_close:
            self._check_for_approval(params)

        # Get market and price data (from original)
        markets = self.markets.get_available_markets()
        prices = self.oracle_prices.get_recent_prices()

        market_data = markets.get(params.market_key)
        if not market_data:
            raise ValueError(f"Market {params.market_key} not found")

        # Calculate prices with slippage (from original _get_prices)
        decimals = market_data["market_metadata"]["decimals"]
        price, acceptable_price, acceptable_price_in_usd = self._get_prices(decimals, prices, params, is_open, is_close, is_swap)

        # Build order arguments (from original _create_order)
        mark_price = int(price) if is_open else 0
        acceptable_price_val = acceptable_price if not is_swap else 0

        # For swaps, market address not important
        market_address = params.market_key if not is_swap else "0x0000000000000000000000000000000000000000"

        arguments = self._build_order_arguments(params, execution_fee, order_type, acceptable_price_val, mark_price)

        # Build multicall (from original)
        multicall_args, value_amount = self._build_multicall_args(params, arguments, execution_fee, is_close)

        # Build final transaction (from original _submit_transaction)
        transaction = self._build_transaction(multicall_args, value_amount, gas_limits["total"])

        return OrderResult(
            transaction=transaction,
            execution_fee=execution_fee,
            acceptable_price=acceptable_price_val,
            mark_price=price,
            gas_limit=gas_limits["total"],
        )

    def _determine_gas_limits(self, is_open: bool, is_close: bool, is_swap: bool) -> dict[str, int]:
        """Determine gas limits based on operation type."""
        if is_open:
            execution_gas = self._gas_limits["increase_order"]
        elif is_close:
            execution_gas = self._gas_limits["decrease_order"]
        elif is_swap:
            execution_gas = self._gas_limits["swap_order"]
        else:
            execution_gas = self._gas_limits["increase_order"]

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
        """
        Calculate prices with slippage

        Returns: (price, acceptable_price, acceptable_price_in_usd)
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
            slippage_price = 0

        acceptable_price = int(slippage_price)
        acceptable_price_in_usd = acceptable_price * (10 ** (decimals - PRECISION))

        self.logger.info(f"Mark Price: ${price * (10 ** (decimals - PRECISION)):.4f}")
        if acceptable_price_in_usd != 0:
            self.logger.info(f"Acceptable price: ${acceptable_price_in_usd:.4f}")

        return price, acceptable_price, acceptable_price_in_usd

    def _build_order_arguments(
        self,
        params: OrderParams,
        execution_fee: int,
        order_type: int,
        acceptable_price: int,
        mark_price: int,
    ) -> tuple:
        """
        Build order arguments tuple.

        Critical: This matches the exact structure expected by GMX contracts.
        """
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        eth_zero_address = "0x" + "0" * 40
        referral_code = bytes.fromhex("0" * 64)

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
                0,  # callbackGasLimit
                0,  # minOutputAmount
                0,  # validFromTime
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
        """
        Build multicall arguments.

        Critical: This determines which tokens to send and in what amounts.
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
        """Build the final unsigned transaction."""
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
        """Encode createOrder function call."""
        hex_data = encode_abi_compat(self._exchange_router_contract, "createOrder", [arguments])
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_tokens(self, token_address: str, amount: int) -> bytes:
        """Encode sendTokens function call."""
        hex_data = encode_abi_compat(self._exchange_router_contract, "sendTokens", [token_address, self.contract_addresses.ordervault, amount])
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _send_wnt(self, amount: int) -> bytes:
        """Encode sendWnt function call."""
        hex_data = encode_abi_compat(self._exchange_router_contract, "sendWnt", [self.contract_addresses.ordervault, amount])
        if hex_data.startswith("0x"):
            hex_data = hex_data[2:]
        return bytes.fromhex(hex_data)

    def _check_for_approval(self, params: OrderParams) -> None:
        """Check token approval (from original check_for_approval)."""
        spender = self.contract_addresses.exchangerouter
        collateral_amount = int(params.initial_collateral_delta_amount)

        # Get the native token to check if approval needed
        chain_tokens = NETWORK_TOKENS.get(self.chain.lower())
        if self.chain.lower() == "arbitrum":
            native_token = chain_tokens.get("WETH")
        else:
            native_token = chain_tokens.get("WAVAX")

        # No approval needed for native token
        if params.collateral_address.lower() == native_token.lower():
            return

        # Check ERC20 approval
        user_address = self.config.get_wallet_address()
        token_details = fetch_erc20_details(self.web3, params.collateral_address)

        allowance = token_details.contract.functions.allowance(to_checksum_address(user_address), to_checksum_address(spender)).call()

        if allowance < collateral_amount:
            raise ValueError(f"Insufficient token approval. Need {collateral_amount}, have {allowance}. Please approve {params.collateral_address} for {spender}")

    def check_if_approved(self, spender: str, token_to_approve: str, amount_of_tokens_to_spend: int, approve: bool = True, wallet=None) -> dict:
        """
        Check if tokens are approved and optionally create an approval transaction.

        returns dict instead of raising errors.
        """
        tokens = NETWORK_TOKENS.get(self.chain, {})
        spender_checksum = to_checksum_address(spender)
        token_checksum = to_checksum_address(token_to_approve)

        # Get the user address
        if wallet:
            user_address = wallet.address
        elif hasattr(self.config, "user_wallet_address") and self.config.user_wallet_address:
            user_address = self.config.user_wallet_address
        else:
            raise ValueError("No wallet address available")

        user_checksum = to_checksum_address(user_address)

        # Check if native token
        native_symbols = {"arbitrum": "WETH", "avalanche": "WAVAX"}
        native_symbol = native_symbols.get(self.chain.lower())
        native_token_address = tokens.get(native_symbol) if native_symbol else None
        is_native = native_token_address and token_checksum.lower() == native_token_address.lower()

        if is_native:
            balance_of = self.web3.eth.get_balance(user_checksum)
        else:
            token_details = fetch_erc20_details(self.web3, token_checksum)
            balance_of = token_details.contract.functions.balanceOf(user_checksum).call()

        if balance_of < amount_of_tokens_to_spend:
            raise ValueError(f"Insufficient balance! Have {balance_of}, need {amount_of_tokens_to_spend}")

        if is_native:
            return {"approved": True, "needs_approval": False}

        # Check allowance
        token_details = fetch_erc20_details(self.web3, token_checksum)
        current_allowance = token_details.contract.functions.allowance(user_checksum, spender_checksum).call()

        if current_allowance >= amount_of_tokens_to_spend:
            return {"approved": True, "needs_approval": False}

        if not approve or not wallet:
            return {"approved": False, "needs_approval": True, "message": f"Need approval for {amount_of_tokens_to_spend} tokens"}

        # Build approval transaction
        gas_fees = estimate_gas_fees(self.web3)

        approval_txn = token_details.contract.functions.approve(spender_checksum, amount_of_tokens_to_spend).build_transaction(
            {
                "from": user_checksum,
                "value": 0,
                "chainId": self.web3.eth.chain_id,
                "gas": 100000,
            }
        )

        if gas_fees.max_fee_per_gas is not None:
            approval_txn["maxFeePerGas"] = gas_fees.max_fee_per_gas
            approval_txn["maxPriorityFeePerGas"] = gas_fees.max_priority_fee_per_gas
            if "gasPrice" in approval_txn:
                del approval_txn["gasPrice"]
        else:
            approval_txn["gasPrice"] = gas_fees.legacy_gas_price
            if "maxFeePerGas" in approval_txn:
                del approval_txn["maxFeePerGas"]
            if "maxPriorityFeePerGas" in approval_txn:
                del approval_txn["maxPriorityFeePerGas"]

        signed_approval = wallet.sign_transaction_with_new_nonce(approval_txn)

        return {"approved": False, "needs_approval": True, "approval_transaction": signed_approval, "message": f"Approval transaction created"}
