"""Type definitions for Ostium"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from decimal import Decimal


class OrderType(Enum):
    """Order types supported by Ostium/Gains."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class TradeDirection(Enum):
    """Trade direction (long/short)."""
    LONG = True
    SHORT = False


@dataclass
class TradeParams:
    """Parameters for opening a trade."""
    collateral: Decimal  # USDC amount
    leverage: int  # Leverage multiplier
    pair_id: int  # Asset pair ID (0=BTC, 1=ETH, etc.)
    direction: TradeDirection  # Long or Short
    order_type: OrderType  # Market, Limit, or Stop
    tp_price: Optional[Decimal] = None  # Take profit price
    sl_price: Optional[Decimal] = None  # Stop loss price
    limit_price: Optional[Decimal] = None  # For limit/stop orders


@dataclass
class OstiumContracts:
    """Ostium protocol contract addresses."""
    # Core trading contracts
    trading: str  # Main trading contract (performs trades)
    storage: str  # Stores open trades and orders
    callbacks: str  # Handles trade execution callbacks

    # Token contracts
    usdc: str  # USDC collateral token

    # Oracle contracts
    price_aggregator: Optional[str] = None

    # Additional contracts
    vault: Optional[str] = None
    pool: Optional[str] = None


@dataclass
class ContractCall:
    """Represents a contract call made during trading."""
    contract_address: str
    function_name: str
    function_selector: str  # 4-byte function selector
    parameters: list[tuple]  # List of (param_name, param_type, param_value)
    tx_hash: Optional[str] = None


@dataclass
class GuardValidation:
    """Validation rules for GuardV0 contract."""
    # Whitelisted trading pairs
    allowed_pair_ids: list[int]

    # Leverage limits
    max_leverage: int

    # Collateral limits
    max_collateral_per_trade: Decimal

    # Withdrawal restrictions
    allowed_withdrawal_addresses: list[str]

    # Rate limiting
    max_trades_per_hour: int

    # Contract whitelisting
    allowed_contract_addresses: list[str]
    allowed_function_selectors: list[str]  # 4-byte selectors
    max_total_collateral: Optional[Decimal] = None
    max_trades_per_day: Optional[int] = None


@dataclass
class TradeMetrics:
    """Metrics for an open trade."""
    pair_id: int
    trade_index: int
    collateral: Decimal
    leverage: int
    open_price: Decimal
    is_long: bool
    unrealized_pnl: Decimal
    pnl_percent: Decimal
    funding_fee: Decimal
    rollover_fee: Decimal
    liquidation_price: Decimal
    total_profit: Decimal


@dataclass
class OpenTrade:
    """Represents an open trade on Ostium."""
    pair_id: int
    trade_index: int
    trader: str
    collateral: Decimal
    leverage: int
    open_price: Decimal
    is_long: bool
    tp_price: Optional[Decimal]
    sl_price: Optional[Decimal]
    timestamp: int
