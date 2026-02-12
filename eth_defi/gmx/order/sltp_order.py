"""
GMX Stop Loss and Take Profit Order Implementation

Provides SL/TP order functionality aligned with the official GMX SDK.
Supports both bundled (atomic open+SL+TP) and standalone approaches.

Key features:
- Percentage-based and absolute trigger prices
- Partial position closes (percentage or USD-based)
- Configurable auto-cancel behavior
- Match SDK slippage handling (SL: no limit, TP: apply slippage)
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from eth_typing import ChecksumAddress
from eth_utils import to_checksum_address
from web3.types import TxParams

from eth_defi.gas import estimate_gas_fees
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import DECREASE_POSITION_SWAP_TYPES, ETH_ZERO_ADDRESS, PRECISION, OrderType
from eth_defi.gmx.contracts import NETWORK_TOKENS, TESTNET_TO_MAINNET_ORACLE_TOKENS
from eth_defi.gmx.execution_buffer import DEFAULT_EXECUTION_BUFFER, DEFAULT_SLTP_EXECUTION_FEE_BUFFER, apply_execution_buffer
from eth_defi.gmx.gas_utils import calculate_execution_fee
from eth_defi.gmx.order.base_order import ZERO_REFERRAL_CODE, BaseOrder, OrderParams, OrderResult

logger = logging.getLogger(__name__)

# Maximum uint256 value for SL acceptable price (short positions)
MAX_UINT256 = 2**256 - 1


@dataclass
class DecreaseAmounts:
    """Core decrease order amounts - mirrors GMX DecreasePositionAmounts.

    Used internally for contract-level decrease order parameters.

    :param size_delta_usd: Position size to close in 30 decimal precision
    :param collateral_delta_amount: Collateral to withdraw (token decimals)
    :param trigger_price: Price that activates the order (30 decimals)
    :param acceptable_price: Worst acceptable execution price (30 decimals)
    :param trigger_order_type: OrderType.LIMIT_DECREASE or OrderType.STOP_LOSS_DECREASE
    :param is_full_close: Whether this closes the entire position
    :param min_output_usd: Minimum output in USD (30 decimals)
    :param decrease_swap_type: 0=NoSwap, 1=SwapPnlToCollateral, 2=SwapCollateralToPnl
    """

    size_delta_usd: int
    collateral_delta_amount: int = 0
    trigger_price: int | None = None
    acceptable_price: int = 0
    trigger_order_type: OrderType | None = None
    is_full_close: bool = False
    min_output_usd: int = 0
    decrease_swap_type: int = 0


@dataclass
class SLTPEntry:
    """User-friendly SL/TP configuration.

    Allows specifying trigger prices as absolute values or percentages,
    and close sizes as percentages or USD amounts.

    Example:
        # 5% stop loss, close 100% of position
        stop_loss = SLTPEntry(trigger_percent=0.05)

        # Absolute $1850 stop, close half
        stop_loss = SLTPEntry(trigger_price=1850, close_percent=0.5)

        # $75000 take profit, close $25000 worth
        take_profit = SLTPEntry(trigger_price=75000, close_size_usd=25000)

    :param trigger_price: Absolute trigger price in USD (specify ONE of price/percent)
    :param trigger_percent: Percentage from entry price (0.05 = 5%)
    :param close_percent: Fraction of position to close (0.5 = 50%, 1.0 = 100%)
    :param close_size_usd: Absolute USD amount to close
    :param auto_cancel: Whether to cancel if primary order fails
    :param decrease_amounts: Internal computed amounts
    """

    trigger_price: float | None = None
    trigger_percent: float | None = None
    close_percent: float = 1.0
    close_size_usd: float | None = None
    auto_cancel: bool = True
    decrease_amounts: DecreaseAmounts | None = None


@dataclass
class SLTPParams:
    """Combined SL/TP parameters for bundled creation.

    :param stop_loss: Stop loss configuration
    :param take_profit: Take profit configuration
    :param execution_fee_buffer: Multiplier for execution fee
    """

    stop_loss: SLTPEntry | None = None
    take_profit: SLTPEntry | None = None
    execution_fee_buffer: float = DEFAULT_SLTP_EXECUTION_FEE_BUFFER  # I know what I'm doing DO NOT REMOVE THIS Plz.


@dataclass
class SLTPOrderResult:
    """Result from creating orders with SL/TP.

    Contains execution details and optional standalone transactions.

    :param transaction: Main bundled transaction (if bundled approach)
    :param total_execution_fee: Sum of all execution fees in wei
    :param main_order_fee: Execution fee for main order
    :param stop_loss_fee: Execution fee for SL order
    :param take_profit_fee: Execution fee for TP order
    :param entry_price: Entry price used for calculations
    :param stop_loss_trigger_price: Computed SL trigger price
    :param take_profit_trigger_price: Computed TP trigger price
    :param stop_loss_transaction: Standalone SL transaction
    :param take_profit_transaction: Standalone TP transaction
    """

    transaction: TxParams | None = None
    total_execution_fee: int = 0
    main_order_fee: int = 0
    stop_loss_fee: int = 0
    take_profit_fee: int = 0
    entry_price: float = 0.0
    stop_loss_trigger_price: float | None = None
    take_profit_trigger_price: float | None = None
    stop_loss_transaction: TxParams | None = None
    take_profit_transaction: TxParams | None = None


def get_trigger_threshold_type(order_type: OrderType, is_long: bool) -> str:
    """Determine if order triggers above or below price.

    Take Profit (LimitDecrease): Long triggers ABOVE, Short triggers BELOW
    Stop Loss (StopLossDecrease): Long triggers BELOW, Short triggers ABOVE

    :param order_type: The order type (LIMIT_DECREASE or STOP_LOSS_DECREASE)
    :param is_long: Whether this is for a long position
    :return: ">" if triggers above price, "<" if triggers below
    """
    if order_type == OrderType.LIMIT_DECREASE:
        return ">" if is_long else "<"
    elif order_type == OrderType.STOP_LOSS_DECREASE:
        return "<" if is_long else ">"
    raise ValueError(f"Unsupported order type for trigger threshold: {order_type}")


def calculate_trigger_price(
    entry_price: float,
    trigger_percent: float,
    is_long: bool,
    order_type: OrderType,
) -> float:
    """Calculate absolute trigger price from percentage.

    For Stop Loss:
        - Long: entry_price * (1 - trigger_percent)  [price goes down]
        - Short: entry_price * (1 + trigger_percent) [price goes up]

    For Take Profit:
        - Long: entry_price * (1 + trigger_percent)  [price goes up]
        - Short: entry_price * (1 - trigger_percent) [price goes down]

    :param entry_price: The position entry price
    :param trigger_percent: Percentage as decimal (0.05 = 5%)
    :param is_long: Whether this is for a long position
    :param order_type: STOP_LOSS_DECREASE or LIMIT_DECREASE
    :return: Calculated trigger price
    """
    if order_type == OrderType.STOP_LOSS_DECREASE:
        if is_long:
            return entry_price * (1 - trigger_percent)
        else:
            return entry_price * (1 + trigger_percent)
    else:  # Take profit (LIMIT_DECREASE)
        if is_long:
            return entry_price * (1 + trigger_percent)
        else:
            return entry_price * (1 - trigger_percent)


def calculate_acceptable_price(
    trigger_price: float,
    is_long: bool,
    order_type: OrderType,
    slippage_percent: float,
    index_token_decimals: int,
) -> int:
    """Calculate acceptable price for contract.

    For Stop Loss: Prioritize execution (0 for longs, MAX_UINT256 for shorts)
    For Take Profit: Apply slippage protection

    Uses Decimal to avoid floating point precision errors.

    :param trigger_price: The trigger price in USD
    :param is_long: Whether this is for a long position
    :param order_type: STOP_LOSS_DECREASE or LIMIT_DECREASE
    :param slippage_percent: Slippage tolerance as decimal (0.003 = 0.3%)
    :param index_token_decimals: Decimals of the index token
    :return: Acceptable price in contract format (30 decimal precision)
    """
    if order_type == OrderType.STOP_LOSS_DECREASE:
        # Stop loss: prioritize execution, no price protection
        return 0 if is_long else MAX_UINT256

    # Take profit: protect price with slippage
    if is_long:
        # Long TP: we're selling, price could go down slightly
        price = trigger_price * (1 - slippage_percent)
    else:
        # Short TP: we're buying back, price could go up slightly
        price = trigger_price * (1 + slippage_percent)

    # Convert to contract format using Decimal to avoid precision errors
    price_decimal = Decimal(str(price))
    multiplier = Decimal(10 ** (PRECISION - index_token_decimals))
    return int(price_decimal * multiplier)


def convert_price_to_contract_format(price: float, index_token_decimals: int) -> int:
    """Convert USD price to GMX contract format (30 decimal precision).

    Uses Decimal to avoid floating point precision errors.

    :param price: Price in USD
    :param index_token_decimals: Decimals of the index token
    :return: Price in contract format
    """
    price_decimal = Decimal(str(price))
    multiplier = Decimal(10 ** (PRECISION - index_token_decimals))
    return int(price_decimal * multiplier)


class SLTPOrder(BaseOrder):
    """Stop Loss and Take Profit order management.

    Provides methods for creating SL/TP orders both bundled with position
    opens and as standalone orders for existing positions.

    Example:
        sltp = SLTPOrder(config, market_key, collateral_address, index_token, is_long=True)

        # Bundled: open + SL + TP in one transaction
        result = sltp.create_increase_order_with_sltp(
            size_delta_usd=10000,
            collateral_amount=1.5,
            sltp_params=SLTPParams(
                stop_loss=SLTPEntry(trigger_percent=0.05),
                take_profit=SLTPEntry(trigger_percent=0.15),
            ),
        )

        # Standalone: add SL to existing position
        result = sltp.create_stop_loss_order(
            position_size_usd=10000,
            entry=SLTPEntry(trigger_price=1850),
            entry_price=2000,
        )
    """

    def __init__(
        self,
        config: GMXConfig,
        market_key: ChecksumAddress,
        collateral_address: ChecksumAddress,
        index_token_address: ChecksumAddress,
        is_long: bool,
    ):
        """Initialize SL/TP order with position identification.

        :param config: GMX configuration instance
        :param market_key: Market contract address
        :param collateral_address: Collateral token address
        :param index_token_address: Index token address
        :param is_long: True for long position, False for short
        """
        super().__init__(config)
        self.market_key = to_checksum_address(market_key)
        self.collateral_address = to_checksum_address(collateral_address)
        self.index_token_address = to_checksum_address(index_token_address)
        self.is_long = is_long

        logger.debug(
            "Initialized SLTPOrder for market %s, %s position",
            self.market_key,
            "LONG" if self.is_long else "SHORT",
        )

    def _get_index_token_decimals(self) -> int:
        """Get decimals for the index token.

        :return: Number of decimals for the index token
        """
        if self._cached_markets is None:
            self._cached_markets = self.markets.get_available_markets()
        market_data = self._cached_markets.get(self.market_key)
        if not market_data:
            raise ValueError(f"Market {self.market_key} not found")
        return market_data["market_metadata"]["decimals"]

    def _get_current_price(self) -> float:
        """Get current price for the index token.

        :return: Current price in USD
        """
        if self._cached_prices is None:
            self._cached_prices = self.oracle_prices.get_recent_prices()

        oracle_address = self.index_token_address
        if self.chain in ["arbitrum_sepolia", "avalanche_fuji"]:
            oracle_address = TESTNET_TO_MAINNET_ORACLE_TOKENS.get(self.index_token_address, self.index_token_address)

        if oracle_address not in self._cached_prices:
            raise ValueError(f"Price not available for token {self.index_token_address}")

        price_data = self._cached_prices[oracle_address]

        price = median(
            [
                float(price_data["maxPriceFull"]),
                float(price_data["minPriceFull"]),
            ]
        )
        decimals = self._get_index_token_decimals()
        return price / (10 ** (PRECISION - decimals))

    def _calculate_execution_fee(self, order_type: str = "decrease_order", oracle_price_count: int = 2) -> int:
        """Calculate execution fee for an order using GMX's formula.

        GMX validates execution fee against:
            adjustedGasLimit = baseGasLimit + (oracleCount * perOracleGas) + applyFactor(estimatedGasLimit, multiplierFactor)
            minExecutionFee = adjustedGasLimit * tx.gasprice

        :param order_type: Type of order for gas estimation
        :param oracle_price_count: Number of oracle prices (typically 2)
        :return: Execution fee in wei
        """
        # Use maxFeePerGas for EIP-1559 transactions since GMX uses tx.gasprice
        # which equals effectiveGasPrice (min of maxFeePerGas and baseFee + priorityFee)
        gas_fees = estimate_gas_fees(self.web3)
        gas_price = gas_fees.max_fee_per_gas if gas_fees.max_fee_per_gas else self.web3.eth.gas_price

        execution_fee = calculate_execution_fee(
            gas_limits=self._gas_limits,
            gas_price=gas_price,
            order_type=order_type,
            oracle_price_count=oracle_price_count,
        )

        logger.info(
            "Execution fee calculation: gas_price=%s, order_type=%s, base_fee=%.6f ETH, gas_limits=%s",
            gas_price,
            order_type,
            execution_fee / 10**18,
            {k: v for k, v in self._gas_limits.items() if "estimated" in k or k == order_type},
        )

        return execution_fee

    def _resolve_trigger_price(
        self,
        entry: SLTPEntry,
        order_type: OrderType,
        entry_price: float | None,
    ) -> float:
        """Resolve trigger price from entry configuration.

        :param entry: SL/TP entry configuration
        :param order_type: STOP_LOSS_DECREASE or LIMIT_DECREASE
        :param entry_price: Entry price (required if using percentage)
        :return: Absolute trigger price in USD
        """
        if entry.trigger_price is not None:
            return entry.trigger_price

        if entry.trigger_percent is not None:
            if entry_price is None:
                raise ValueError("entry_price is required when using trigger_percent")
            return calculate_trigger_price(
                entry_price,
                entry.trigger_percent,
                self.is_long,
                order_type,
            )

        raise ValueError("Either trigger_price or trigger_percent must be specified")

    def _resolve_close_size(
        self,
        entry: SLTPEntry,
        position_size_usd: float,
    ) -> int:
        """Resolve close size from entry configuration.

        Uses Decimal to avoid floating point precision errors when
        multiplying by 10^30.

        :param entry: SL/TP entry configuration
        :param position_size_usd: Total position size in USD
        :return: Size delta in 30 decimal precision
        """
        if entry.close_size_usd is not None:
            close_usd = entry.close_size_usd
        else:
            close_usd = position_size_usd * entry.close_percent

        # Use Decimal to avoid floating point precision errors
        # e.g., 100.0 * 10**30 should be exactly 100000000000000000000000000000000
        # not 100000000000000005366162204393472
        close_usd_decimal = Decimal(str(close_usd))
        precision_multiplier = Decimal(10**PRECISION)
        return int(close_usd_decimal * precision_multiplier)

    def _build_decrease_order_arguments(
        self,
        size_delta_usd: int,
        trigger_price: int,
        acceptable_price: int,
        order_type: OrderType,
        execution_fee: int,
        auto_cancel: bool = True,
    ) -> tuple:
        """Build order arguments for a decrease order.

        :param size_delta_usd: Position size to close (30 decimals)
        :param trigger_price: Trigger price (30 decimals)
        :param acceptable_price: Acceptable execution price (30 decimals)
        :param order_type: OrderType.LIMIT_DECREASE or OrderType.STOP_LOSS_DECREASE
        :param execution_fee: Execution fee in wei
        :param auto_cancel: Whether to auto-cancel on failure
        :return: Order arguments tuple for contract call
        """
        user_wallet_address = self.config.get_wallet_address()
        if not user_wallet_address:
            raise ValueError("User wallet address is required")

        user_checksum = to_checksum_address(user_wallet_address)
        collateral_checksum = to_checksum_address(self.collateral_address)
        market_checksum = to_checksum_address(self.market_key)

        return (
            (
                user_checksum,  # receiver
                user_checksum,  # cancellationReceiver
                ETH_ZERO_ADDRESS,  # callbackContract
                ETH_ZERO_ADDRESS,  # uiFeeReceiver
                market_checksum,  # market
                collateral_checksum,  # initialCollateralToken
                [],  # swapPath (empty for decrease)
            ),
            (
                size_delta_usd,  # sizeDeltaUsd (30 decimals)
                0,  # initialCollateralDeltaAmount
                trigger_price,  # triggerPrice
                acceptable_price,  # acceptablePrice
                execution_fee,  # executionFee
                0,  # callbackGasLimit
                0,  # minOutputAmount
                0,  # validFromTime
            ),
            int(order_type),  # orderType
            DECREASE_POSITION_SWAP_TYPES["no_swap"],  # decreasePositionSwapType
            self.is_long,  # isLong
            True,  # shouldUnwrapNativeToken
            auto_cancel,  # autoCancel
            ZERO_REFERRAL_CODE,  # referralCode
            [],  # dataList
        )

    def create_stop_loss_order(
        self,
        position_size_usd: float,
        entry: SLTPEntry,
        entry_price: float | None = None,
        slippage_percent: float = 0.003,
        execution_buffer: float = DEFAULT_EXECUTION_BUFFER,
    ) -> OrderResult:
        """Create standalone stop loss for existing position.

        :param position_size_usd: Total position size in USD
        :param entry: Stop loss configuration
        :param entry_price: Entry price (required if using trigger_percent)
        :param slippage_percent: Not used for SL (execution prioritized)
        :param execution_buffer: Multiplier for execution fee
        :return: OrderResult with unsigned transaction
        """
        decimals = self._get_index_token_decimals()

        # Resolve trigger price
        trigger_price_usd = self._resolve_trigger_price(
            entry,
            OrderType.STOP_LOSS_DECREASE,
            entry_price,
        )

        # Resolve close size
        size_delta_usd = self._resolve_close_size(entry, position_size_usd)

        # Calculate prices
        trigger_price_contract = convert_price_to_contract_format(
            trigger_price_usd,
            decimals,
        )
        acceptable_price_contract = calculate_acceptable_price(
            trigger_price_usd,
            self.is_long,
            OrderType.STOP_LOSS_DECREASE,
            slippage_percent,
            decimals,
        )

        # Calculate execution fee
        base_fee = self._calculate_execution_fee("decrease_order")
        execution_fee = apply_execution_buffer(base_fee, execution_buffer)
        logger.info(
            "Stop Loss fee: base=%.6f ETH × buffer=%.1f = %.6f ETH",
            base_fee / 10**18,
            execution_buffer,
            execution_fee / 10**18,
        )

        # Build order arguments
        arguments = self._build_decrease_order_arguments(
            size_delta_usd=size_delta_usd,
            trigger_price=trigger_price_contract,
            acceptable_price=acceptable_price_contract,
            order_type=OrderType.STOP_LOSS_DECREASE,
            execution_fee=execution_fee,
            auto_cancel=entry.auto_cancel,
        )

        # Build multicall
        multicall_args = [
            self._send_wnt(execution_fee),
            self._create_order(arguments),
        ]

        # Build transaction
        gas_limit = self._gas_limits.get("decrease_order", 2000000)
        gas_limit += self._gas_limits.get("multicall_base", 200000)
        transaction = self._build_transaction(multicall_args, execution_fee, gas_limit)

        logger.info(
            "Created stop loss order: trigger=$%.2f, size=$%.2f",
            trigger_price_usd,
            position_size_usd * entry.close_percent if entry.close_size_usd is None else entry.close_size_usd,
        )

        return OrderResult(
            transaction=transaction,
            execution_fee=execution_fee,
            acceptable_price=acceptable_price_contract,
            mark_price=trigger_price_usd,
            gas_limit=gas_limit,
        )

    def create_take_profit_order(
        self,
        position_size_usd: float,
        entry: SLTPEntry,
        entry_price: float | None = None,
        slippage_percent: float = 0.003,
        execution_buffer: float = DEFAULT_EXECUTION_BUFFER,
    ) -> OrderResult:
        """Create standalone take profit for existing position.

        :param position_size_usd: Total position size in USD
        :param entry: Take profit configuration
        :param entry_price: Entry price (required if using trigger_percent)
        :param slippage_percent: Slippage tolerance for price protection
        :param execution_buffer: Multiplier for execution fee
        :return: OrderResult with unsigned transaction
        """
        decimals = self._get_index_token_decimals()

        # Resolve trigger price
        trigger_price_usd = self._resolve_trigger_price(
            entry,
            OrderType.LIMIT_DECREASE,
            entry_price,
        )

        # Resolve close size
        size_delta_usd = self._resolve_close_size(entry, position_size_usd)

        # Calculate prices
        trigger_price_contract = convert_price_to_contract_format(
            trigger_price_usd,
            decimals,
        )
        acceptable_price_contract = calculate_acceptable_price(
            trigger_price_usd,
            self.is_long,
            OrderType.LIMIT_DECREASE,
            slippage_percent,
            decimals,
        )

        # Calculate execution fee
        base_fee = self._calculate_execution_fee("decrease_order")
        execution_fee = apply_execution_buffer(base_fee, execution_buffer)
        logger.info(
            "Take Profit fee: base=%.6f ETH × buffer=%.1f = %.6f ETH",
            base_fee / 10**18,
            execution_buffer,
            execution_fee / 10**18,
        )

        # Build order arguments
        arguments = self._build_decrease_order_arguments(
            size_delta_usd=size_delta_usd,
            trigger_price=trigger_price_contract,
            acceptable_price=acceptable_price_contract,
            order_type=OrderType.LIMIT_DECREASE,
            execution_fee=execution_fee,
            auto_cancel=entry.auto_cancel,
        )

        # Build multicall
        multicall_args = [
            self._send_wnt(execution_fee),
            self._create_order(arguments),
        ]

        # Build transaction
        gas_limit = self._gas_limits.get("decrease_order", 2000000)
        gas_limit += self._gas_limits.get("multicall_base", 200000)
        transaction = self._build_transaction(multicall_args, execution_fee, gas_limit)

        logger.info(
            "Created take profit order: trigger=$%.2f, size=$%.2f",
            trigger_price_usd,
            position_size_usd * entry.close_percent if entry.close_size_usd is None else entry.close_size_usd,
        )

        return OrderResult(
            transaction=transaction,
            execution_fee=execution_fee,
            acceptable_price=acceptable_price_contract,
            mark_price=trigger_price_usd,
            gas_limit=gas_limit,
        )

    def create_increase_order_with_sltp(
        self,
        size_delta_usd: float,
        initial_collateral_delta_amount: int | str,
        sltp_params: SLTPParams | None = None,
        slippage_percent: float = 0.003,
        swap_path: list[str] | None = None,
        execution_buffer: float = DEFAULT_EXECUTION_BUFFER,
        auto_cancel: bool = False,
        data_list: list[str] | None = None,
    ) -> SLTPOrderResult:
        """Open position + SL + TP in single atomic transaction.

        Creates a bundled multicall transaction that atomically creates:
        1. The main increase order
        2. Optional stop loss order
        3. Optional take profit order

        :param size_delta_usd: Position size in USD
        :param initial_collateral_delta_amount: Collateral in token's smallest unit
        :param sltp_params: SL/TP configuration
        :param slippage_percent: Slippage tolerance
        :param swap_path: Optional swap routing
        :param execution_buffer: Multiplier for execution fees
        :param auto_cancel: Auto-cancel main order if can't execute
        :param data_list: Additional data for order
        :return: SLTPOrderResult with bundled transaction
        """
        if swap_path is None:
            swap_path = []
        if data_list is None:
            data_list = []

        decimals = self._get_index_token_decimals()
        current_price = self._get_current_price()

        # Calculate main order execution fee
        main_gas_limit = self._gas_limits.get("increase_order", 2500000)
        gas_price = self.web3.eth.gas_price
        main_execution_fee = int(main_gas_limit * gas_price * execution_buffer)

        # Scale size_delta_usd to 30 decimal precision (GMX format)
        # Use Decimal to avoid floating point precision errors
        size_delta_scaled = int(Decimal(str(size_delta_usd)) * Decimal(10**PRECISION))

        # Get main order parameters
        params = OrderParams(
            market_key=self.market_key,
            collateral_address=self.collateral_address,
            index_token_address=self.index_token_address,
            is_long=self.is_long,
            size_delta=size_delta_scaled,
            initial_collateral_delta_amount=str(initial_collateral_delta_amount),
            slippage_percent=slippage_percent,
            swap_path=swap_path,
            execution_buffer=execution_buffer,
            auto_cancel=auto_cancel,
            data_list=data_list,
        )

        # Build main order result to get price info
        main_result = self.order_builder(
            params,
            is_open=True,
            is_close=False,
            is_swap=False,
        )
        entry_price = main_result.mark_price

        # Initialize result
        result = SLTPOrderResult(
            main_order_fee=main_result.execution_fee,
            entry_price=entry_price,
        )

        # Build multicall args starting with main order components
        # Get collateral amount and check if native token
        collateral_amount = int(initial_collateral_delta_amount)
        chain_tokens = NETWORK_TOKENS.get(self.chain.lower())
        if self.chain.lower() in ["arbitrum", "arbitrum_sepolia"]:
            native_token_address = chain_tokens.get("WETH")
        elif self.chain.lower() in ["avalanche", "avalanche_fuji"]:
            native_token_address = chain_tokens.get("WAVAX")
        else:
            raise ValueError(f"Unsupported chain: {self.chain}")

        is_native = self.collateral_address.lower() == native_token_address.lower()

        # Calculate SL/TP fees
        sl_fee = 0
        tp_fee = 0
        sl_trigger_price = None
        tp_trigger_price = None

        if sltp_params:
            if sltp_params.stop_loss:
                sl_fee = self._calculate_execution_fee("decrease_order")
                sl_fee = apply_execution_buffer(sl_fee, execution_buffer * sltp_params.execution_fee_buffer, validate=False)

                sl_trigger_price = self._resolve_trigger_price(
                    sltp_params.stop_loss,
                    OrderType.STOP_LOSS_DECREASE,
                    entry_price,
                )
                result.stop_loss_trigger_price = sl_trigger_price

            if sltp_params.take_profit:
                tp_fee = self._calculate_execution_fee("decrease_order")
                tp_fee = apply_execution_buffer(tp_fee, execution_buffer * sltp_params.execution_fee_buffer, validate=False)

                tp_trigger_price = self._resolve_trigger_price(
                    sltp_params.take_profit,
                    OrderType.LIMIT_DECREASE,
                    entry_price,
                )
                result.take_profit_trigger_price = tp_trigger_price

        result.stop_loss_fee = sl_fee
        result.take_profit_fee = tp_fee
        result.total_execution_fee = main_result.execution_fee + sl_fee + tp_fee

        # Calculate total value to send (for transaction value)
        total_wnt = main_result.execution_fee + sl_fee + tp_fee
        if is_native:
            total_wnt += collateral_amount

        # Build multicall args
        # IMPORTANT: GMX requires sendWnt before EACH createOrder, not just once!
        # Each order's execution fee must be deposited to the vault before creating that order.
        multicall_args: list = []

        # 1. Send WNT for main order (execution fee + collateral if native)
        main_wnt = main_result.execution_fee
        if is_native:
            main_wnt += collateral_amount
        multicall_args.append(self._send_wnt(main_wnt))

        # 2. Send tokens if ERC20 collateral
        if not is_native:
            multicall_args.append(
                self._send_tokens(self.collateral_address, collateral_amount),
            )

        # 3. Main increase order
        main_arguments = self._build_order_arguments(
            params,
            main_result.execution_fee,
            OrderType.MARKET_INCREASE,
            main_result.acceptable_price,
            int(main_result.mark_price * (10 ** (PRECISION - decimals))),
        )
        multicall_args.append(self._create_order(main_arguments))

        # 4. Stop loss order if configured (sendWnt + createOrder)
        if sltp_params and sltp_params.stop_loss and sl_trigger_price:
            # Send WNT for SL order BEFORE creating the order
            multicall_args.append(self._send_wnt(sl_fee))

            sl_size_delta = self._resolve_close_size(
                sltp_params.stop_loss,
                size_delta_usd,
            )
            sl_trigger_contract = convert_price_to_contract_format(
                sl_trigger_price,
                decimals,
            )
            sl_acceptable = calculate_acceptable_price(
                sl_trigger_price,
                self.is_long,
                OrderType.STOP_LOSS_DECREASE,
                slippage_percent,
                decimals,
            )
            sl_arguments = self._build_decrease_order_arguments(
                size_delta_usd=sl_size_delta,
                trigger_price=sl_trigger_contract,
                acceptable_price=sl_acceptable,
                order_type=OrderType.STOP_LOSS_DECREASE,
                execution_fee=sl_fee,
                auto_cancel=sltp_params.stop_loss.auto_cancel,
            )
            multicall_args.append(self._create_order(sl_arguments))

        # 5. Take profit order if configured (sendWnt + createOrder)
        if sltp_params and sltp_params.take_profit and tp_trigger_price:
            # Send WNT for TP order BEFORE creating the order
            multicall_args.append(self._send_wnt(tp_fee))

            tp_size_delta = self._resolve_close_size(
                sltp_params.take_profit,
                size_delta_usd,
            )
            tp_trigger_contract = convert_price_to_contract_format(
                tp_trigger_price,
                decimals,
            )
            tp_acceptable = calculate_acceptable_price(
                tp_trigger_price,
                self.is_long,
                OrderType.LIMIT_DECREASE,
                slippage_percent,
                decimals,
            )
            tp_arguments = self._build_decrease_order_arguments(
                size_delta_usd=tp_size_delta,
                trigger_price=tp_trigger_contract,
                acceptable_price=tp_acceptable,
                order_type=OrderType.LIMIT_DECREASE,
                execution_fee=tp_fee,
                auto_cancel=sltp_params.take_profit.auto_cancel,
            )
            multicall_args.append(self._create_order(tp_arguments))

        # Calculate total gas limit
        total_gas = main_result.gas_limit
        if sl_fee > 0:
            total_gas += self._gas_limits.get("decrease_order", 2000000)
        if tp_fee > 0:
            total_gas += self._gas_limits.get("decrease_order", 2000000)

        # Build final transaction
        transaction = self._build_transaction(multicall_args, total_wnt, total_gas)
        result.transaction = transaction

        logger.info(
            "Created increase order with SL/TP: size=$%.2f, entry=$%.2f, SL=$%s, TP=$%s",
            size_delta_usd,
            entry_price,
            f"{sl_trigger_price:.2f}" if sl_trigger_price else "N/A",
            f"{tp_trigger_price:.2f}" if tp_trigger_price else "N/A",
        )

        return result
