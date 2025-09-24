"""
GMX Order Management Module

This module provides an interface for GMX orders. It creates unsigned transactions that can be signed later by the user.

Return unsigned transactions for maximum flexibility
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from decimal import Decimal
from enum import Enum
import logging

from cchecksum import to_checksum_address
from web3.types import TxParams
from eth_typing import ChecksumAddress
from eth_utils import to_wei

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import NETWORK_CONTRACTS
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.data import GMXMarketData


class OrderType(Enum):
    """GMX Order Types"""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    INCREASE = "increase"
    DECREASE = "decrease"
    SWAP = "swap"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class OrderSide(Enum):
    """Order side"""

    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"


@dataclass
class OrderParams:
    """
    Order parameters structure
    """

    symbol: str  # Market symbol (e.g., "ETH/USD")
    type: OrderType  # Order type
    side: OrderSide  # Order side (buy/sell, long/short)
    amount: Decimal | float | int  # Position size in USD
    price: Optional[Decimal | float] = None  # Limit price (if applicable)

    # GMX-specific parameters
    collateral_token: Optional[str] = None  # Collateral token symbol
    start_token: Optional[str] = None  # Starting token for swaps
    leverage: Optional[Decimal | float] = None  # Leverage multiplier
    slippage_percent: float = 0.005  # Default 0.5% slippage

    # Advanced parameters
    trigger_price: Optional[Decimal | float] = None  # Stop/limit trigger
    reduce_only: bool = False  # Close position only
    post_only: bool = False  # Maker only orders
    time_in_force: str = "GTC"  # Good till cancelled

    # Execution parameters
    execution_fee_buffer: float = 1.2  # Execution fee multiplier
    min_output_amount: Optional[int] = None  # Minimum output for swaps
    swap_path: Optional[list[ChecksumAddress]] = None  # Token swap path

    # Optional metadata
    client_order_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderMetadata:
    """
    Order metadata to accompany the transaction
    Contains GMX-specific information about the order
    """

    order_type: OrderType
    symbol: str
    side: OrderSide
    amount: Decimal | float | int
    estimated_execution_fee: int = 0
    market_info: dict[str, Any] = field(default_factory=dict)
    slippage_percent: float = 0.005
    leverage: Optional[Decimal | float] = None


class BaseOrder:
    """
    Base GMX Order base class

    This class creates unsigned transactions (TxParams) for GMX orders that can be signed
    later by the user using standard Web3/eth-account tools.

    Returns:
        Standard Web3 TxParams that can be signed with eth-account or sent via Web3
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize the base order with GMX configuration

        Args:
            config: GMX configuration containing Web3 instance and chain settings
        """
        self.config = config
        self.web3 = config.web3
        self.chain = config.get_chain().lower()
        self.logger = logging.getLogger(f"{self.__class__.__name__}")

        # Initialize core data providers
        self.markets = Markets(config)
        self.oracle = OraclePrices(config.chain)
        self.data_provider = GMXMarketData(config)

        # Cache for contract addresses and ABIs
        self._contracts: dict[str, Any] = {}
        self._market_cache: dict[str, Any] = {}

        self.logger.info(f"Initialized {self.__class__.__name__} for {self.chain}")

    def get_markets(self) -> dict[str, Any]:
        """
        Get all available markets

        Returns:
            Dictionary of market information keyed by symbol
        """
        if not self._market_cache:
            markets_info = self.markets.get_available_markets()
            # Convert to
            self._market_cache = self._format_markets(markets_info)

        return self._market_cache

    def get_market(self, symbol: str) -> dict[str, Any]:
        """
        Get specific market information

        Args:
            symbol: Market symbol (e.g., "ETH/USD")

        Returns:
            Market information dictionary
        """
        markets = self.get_markets()
        if symbol not in markets:
            raise ValueError(f"Market {symbol} not found. Available markets: {list(markets.keys())}")

        return markets[symbol]

    def get_oracle_prices(self, tokens: Optional[list[str]] = None) -> dict[Any, Any] | dict[str, float]:
        """
        Get current oracle prices for tokens

        Args:
            tokens: List of token symbols. If None, gets all available tokens

        Returns:
            Dictionary of token prices with min/max price information
        """
        try:
            prices = self.oracle.get_recent_prices()
            if tokens:
                # Filter for requested tokens only
                filtered_prices = {}
                for token in tokens:
                    if token in prices:
                        filtered_prices[token] = prices[token]
                return filtered_prices
            return prices
        except Exception as e:
            self.logger.error(f"Failed to get oracle prices: {e}")
            raise

    def estimate_execution_fee(self, order_type: OrderType, gas_limit: int = 2000000) -> int:
        """
        Estimate execution fee for order type

        Args:
            order_type: Type of order to estimate fee for
            gas_limit: Gas limit. Defaults to 2M gas

        Returns:
            Estimated execution fee in Wei
        """
        try:
            # Get the appropriate gas limit for order type
            gas_limit_key = {OrderType.INCREASE: "increase_order", OrderType.DECREASE: "decrease_order", OrderType.SWAP: "swap_order", OrderType.DEPOSIT: "deposit", OrderType.WITHDRAWAL: "withdrawal"}.get(order_type, "increase_order")

            # Get current gas price
            gas_price = self.web3.eth.gas_price

            # Calculate execution fee with buffer
            execution_fee = int(gas_limit * gas_price)

            return execution_fee

        except Exception as e:
            self.logger.warning(f"Failed to estimate execution fee: {e}")
            # Return conservative estimate
            return int(2000000 * self.web3.eth.gas_price)

    def create_order(self, params: OrderParams) -> tuple[TxParams, OrderMetadata]:
        """
        Create an unsigned transaction for the order

        This is the main method that should be overridden by child classes
        to implement specific order logic.

        Args:
            params: Order parameters

        Returns:
            Tuple of (TxParams for Web3, OrderMetadata with additional info)
        """
        raise NotImplementedError("Child classes must implement create_order method")

    def validate_params(self, params: OrderParams) -> None:
        """
        Validate order parameters

        Args:
            params: Order parameters to validate

        Raises:
            ValueError: If parameters are invalid
        """
        # Basic validation
        if not params.symbol:
            raise ValueError("Symbol is required")

        if not params.type:
            raise ValueError("Order type is required")

        if not params.side:
            raise ValueError("Order side is required")

        if params.amount <= 0:
            raise ValueError("Amount must be positive")

        # Validate market exists
        try:
            self.get_market(params.symbol)
        except ValueError:
            raise ValueError(f"Invalid market symbol: {params.symbol}")

        # Type-specific validation
        if params.type in [OrderType.LIMIT, OrderType.STOP_LIMIT]:
            if params.price is None:
                raise ValueError(f"Price is required for {params.type.value} orders")

        if params.type in [OrderType.STOP, OrderType.STOP_LIMIT]:
            if params.trigger_price is None:
                raise ValueError(f"Trigger price is required for {params.type.value} orders")

    def _format_markets(self, gmx_markets: dict[str, Any]) -> dict[str, Any]:
        """
        Convert GMX market format to CCTX like output

        Args:
            gmx_markets: Raw GMX markets data

        Returns:
             markets dictionary
        """
        ccxt_markets = {}

        for market_address, market_data in gmx_markets.items():
            try:
                # Extract token symbols
                index_token = market_data.get("index_token_symbol", "").upper()

                # Create CCXT-like symbol
                symbol = f"{index_token}/USD"

                ccxt_markets[symbol] = {
                    "id": market_address,
                    "symbol": symbol,
                    "base": index_token,
                    "quote": "USD",
                    "baseId": market_data.get("index_token_address"),
                    "quoteId": "USD",
                    "active": True,
                    "type": "perpetual",
                    "linear": True,
                    "settle": "USDC",
                    "contractSize": 1,
                    "precision": {"amount": 8, "price": 2},
                    "limits": {
                        "amount": {"min": 1, "max": None},
                        "price": {"min": None, "max": None},
                        "cost": {"min": 1, "max": None},  # $1 minimum
                    },
                    "info": market_data,  # Keep original GMX data
                }

            except Exception as e:
                self.logger.warning(f"Failed to format market {market_address}: {e}")
                continue

        return ccxt_markets

    def _get_contract_address(self, contract_name: str) -> ChecksumAddress:
        """
        Get contract address for the current chain

        Args:
            contract_name: Name of the contract (e.g., "ExchangeRouter")

        Returns:
            Contract address
        """
        # This should integrate with your existing contract address mapping
        # from eth_defi/gmx/core or config

        chain_contracts = NETWORK_CONTRACTS.get(self.chain)
        if not chain_contracts:
            raise ValueError(f"Unsupported chain: {self.chain}")

        address = chain_contracts.get(contract_name)
        if not address:
            raise ValueError(f"Contract {contract_name} not found for chain {self.chain}")

        return to_checksum_address(address)

    def _build_base_transaction(self, to: ChecksumAddress, data: bytes, value: int = 0, gas_limit: Optional[int] = None) -> TxParams:
        """
        Build a base transaction structure that child classes can extend

        Args:
            to: Contract address to call
            data: Encoded function call data
            value: ETH value to send (default 0)
            gas_limit: Gas limit (estimated if not provided)

        Returns:
            Base TxParams structure
        """
        if gas_limit is None:
            # Estimate gas limit if not provided
            try:
                gas_limit = self.web3.eth.estimate_gas(
                    {
                        "to": to,
                        "data": data,
                        "value": value,
                        "from": self.config.get_wallet_address() if self.config.get_wallet_address() else "0x0000000000000000000000000000000000000000",
                    }
                )
                # Add 20% buffer
                gas_limit = int(gas_limit * 1.2)
            except Exception as e:
                self.logger.warning(f"Gas estimation failed: {e}, using default")
                gas_limit = 2000000  # Conservative default

        tx_params: TxParams = {
            "to": to,
            "data": data,
            "value": value,
            "gas": gas_limit,
        }

        # Add gas pricing (prefer EIP-1559 if supported)
        try:
            # Try to get EIP-1559 gas prices
            latest_block = self.web3.eth.get_block("latest")
            if hasattr(latest_block, "baseFeePerGas") and latest_block.baseFeePerGas is not None:
                # EIP-1559 supported
                base_fee = latest_block.baseFeePerGas
                max_priority_fee = to_wei(2, "gwei")  # 2 gwei priority fee
                max_fee = base_fee * 2 + max_priority_fee  # 2x base fee + priority

                tx_params["maxFeePerGas"] = max_fee
                tx_params["maxPriorityFeePerGas"] = max_priority_fee
            else:
                # Legacy gas pricing
                tx_params["gasPrice"] = self.web3.eth.gas_price
        except Exception as e:
            # Fallback to legacy gas pricing
            self.logger.warning(f"EIP-1559 gas pricing failed: {e}, using legacy")
            tx_params["gasPrice"] = self.web3.eth.gas_price

        return tx_params
