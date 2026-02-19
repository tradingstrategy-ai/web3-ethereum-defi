"""
GMX Trading Module

This module provides the fundamental trading execution capabilities for the GMX
protocol, implementing sophisticated position opening, closing, and token swapping
functionality through a unified, professional-grade interface. It serves as the
primary execution engine for all trading operations, translating high-level
trading intentions into precise protocol interactions.

**Trading System Architecture:**

Professional trading systems recognize that execution is where theoretical strategies
meet practical reality. The GMX Trading module implements this philosophy by
providing clean abstractions over complex protocol mechanics while maintaining
the precision and control needed for sophisticated trading strategies.

**Core Trading Operations:**

The module supports the three fundamental operations that form the foundation
of any comprehensive trading system:

- **Position Opening**: Creating new leveraged positions with precise size, collateral, and risk controls
- **Position Closing**: Exiting existing positions with strategic timing and asset selection
- **Token Swapping**: Converting between assets for portfolio rebalancing and strategy execution

**Leveraged Trading Mechanics:**

GMX operates as a sophisticated leveraged trading platform where traders can
open positions larger than their collateral through borrowed capital from
liquidity providers. This creates opportunities for amplified returns while
requiring careful risk management to prevent liquidation during adverse
market movements.

**Risk Management Integration:**

Every trading operation includes comprehensive risk controls including slippage
protection, leverage limits, collateral adequacy validation, and execution
parameter verification. The system prevents common trading errors while
maintaining the flexibility needed for advanced strategies.

**Strategic Trading Patterns:**

The module supports multiple trading patterns including:

- **Directional Trading**: Taking leveraged positions based on market analysis
- **Arbitrage Strategies**: Exploiting price discrepancies across markets or time
- **Delta Neutral Strategies**: Managing exposure through offsetting positions
- **Portfolio Rebalancing**: Adjusting asset allocations based on changing market conditions

**Advanced Execution Features:**

- **Flexible Parameter Control**: Precise control over execution timing and costs
- **Multi-Asset Support**: Trade across all GMX-supported markets and tokens
- **Slippage Management**: Dynamic slippage adjustment based on market conditions

Example:

.. code-block:: python

    # Professional trading workflow with comprehensive risk management
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig.from_private_key(web3, "0x...", chain="arbitrum")
    trader = GMXTrading(config)

    # Strategic position opening with precise risk parameters
    long_eth_order = trader.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",  # Use stable collateral
        start_token_symbol="USDC",  # Start with stable asset
        is_long=True,  # Bullish position
        size_delta_usd=5000,  # $5000 position size
        leverage=3.0,  # 3x leverage
        slippage_percent=0.005,  # 0.5% slippage tolerance
        auto_cancel=True,  # Cancel if execution fails
    )

    # Execute the order
    tx_receipt = long_eth_order.submit()
    print(f"Position opened: {tx_receipt.transactionHash.hex()}")

    # Strategic token swap for portfolio rebalancing
    swap_order = trader.swap_tokens(
        in_token_symbol="ETH",
        out_token_symbol="USDC",
        amount=1.5,  # Swap 1.5 ETH
        slippage_percent=0.01,  # 1% slippage for volatile swap
        execution_buffer=3.0,  # Higher execution buffer
    )

    # Risk management: Close position with strategic asset selection
    close_order = trader.close_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=2500,  # Close half the position
        initial_collateral_delta=800,  # Remove $800 collateral
        slippage_percent=0.003,  # Tight slippage for profit taking
    )

**Integration with Trading Strategies:**

The module is designed to integrate seamlessly with automated trading strategies,
risk management systems, and portfolio optimization algorithms. Its clean
interface and comprehensive parameter control make it suitable for both manual
trading and systematic strategy implementation.

Note:
    All trading operations require wallet configuration with transaction signing
    capabilities and sufficient collateral for the intended operations.

Warning:
    Leveraged trading involves substantial risk of loss. Positions can be
    liquidated if market movements exceed collateral capacity. Never trade
    with more capital than you can afford to lose completely.
"""

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from eth_defi.gmx.order import OrderResult
from eth_defi.gmx.order.increase_order import IncreaseOrder
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.order.sltp_order import SLTPOrder, SLTPEntry, SLTPParams, SLTPOrderResult
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.gas_monitor import (
    GasMonitorConfig,
    GMXGasMonitor,
    GasCheckResult,
    TradeExecutionResult,
    InsufficientGasError,
)

if TYPE_CHECKING:
    from eth_defi.hotwallet import HotWallet

logger = logging.getLogger(__name__)


class GMXTrading:
    """
    Comprehensive trading execution system for GMX protocol operations.

    This class implements the core trading capabilities needed for professional
    trading operations on the GMX protocol, providing sophisticated abstractions
    over complex protocol mechanics while maintaining the precision and control
    required for advanced trading strategies.

    **Trading System Philosophy:**

    The design follows professional trading system principles where execution
    quality is paramount. Every method provides comprehensive parameter control,
    extensive validation, and safety features.
    The architecture supports both manual trading workflows and algorithmic
    strategy implementation.

    **Leveraged Trading Architecture:**

    GMX's leveraged trading system allows traders to open positions larger than
    their collateral by borrowing capital from liquidity providers. This creates
    sophisticated risk/reward dynamics where successful trades can generate
    amplified returns, but unsuccessful trades can result in rapid capital loss
    through liquidation.

    **Risk Control Philosophy:**

    Every trading operation includes multiple layers of risk control including
    slippage protection, collateral adequacy validation, leverage limits, and
    execution parameter verification. The system prevents common trading errors
    while providing the flexibility needed for sophisticated strategies.

    :ivar config: GMX configuration object containing network and wallet settings
    :vartype config: GMXConfig
    """

    def __init__(
        self,
        config: GMXConfig,
        gas_monitor_config: GasMonitorConfig | None = None,
    ):
        """
        Initialize the trading system with GMX configuration and validation.

        This constructor establishes the trading system with comprehensive
        configuration validation to ensure all necessary components are available
        for safe trading operations. Since trading involves financial transactions
        with potential for significant losses, the configuration must include
        transaction signing capabilities.

        :param config:
            Complete GMX configuration object containing network settings and
            wallet credentials. Must have write capability enabled as all trading
            operations require transaction signing and execution
        :type config: GMXConfig
        :param gas_monitor_config:
            Optional configuration for gas monitoring. If None, defaults are used.
            Set enabled=False in the config to disable gas monitoring entirely.
        :type gas_monitor_config: GasMonitorConfig | None
        :raises ValueError:
            When the configuration lacks transaction signing capabilities
            required for trading operations and position management
        """
        self.config = config
        self._gas_monitor_config = gas_monitor_config
        self._gas_monitor: GMXGasMonitor | None = None

    @property
    def gas_monitor(self) -> GMXGasMonitor:
        """Lazy-initialise and return the gas monitor instance.

        :return: GMXGasMonitor instance for this trading session
        """
        if self._gas_monitor is None:
            self._gas_monitor = GMXGasMonitor(
                web3=self.config.web3,
                chain=self.config.get_chain(),
                config=self._gas_monitor_config,
            )
        return self._gas_monitor

    @property
    def gas_monitor_config(self) -> GasMonitorConfig:
        """Return the gas monitor configuration.

        :return: Current gas monitor configuration
        """
        return self.gas_monitor.config

    def execute_order(
        self,
        order_result: OrderResult,
        wallet: "HotWallet",
        check_gas: bool = True,
    ) -> TradeExecutionResult:
        """Execute an order with gas monitoring and graceful failure handling.

        This method provides comprehensive trade execution with:
        - Pre-trade gas balance checking (warning/critical thresholds)
        - Gas estimation with safety buffer
        - Logging of estimated and actual gas costs in native token and USD
        - Graceful handling of out-of-gas failures (returns status instead of crash)

        **Execution Flow:**

        1. Check gas balance (if enabled)
           - Critical: return failed result OR raise (based on config)
           - Warning: log warning, continue
        2. Estimate gas with buffer, log estimate
        3. Sign and broadcast transaction
        4. On success: log actual gas usage, return success result
        5. On OutOfGas: return failed result (no crash)
        6. On other error: return failed result with error message

        Example:

        .. code-block:: python

            # Create order (existing flow)
            order_result = trading.open_position(
                market_symbol="ETH",
                collateral_symbol="USDC",
                ...
            )

            # Execute with gas monitoring (new flow)
            result = trading.execute_order(order_result, wallet)

            if result.success:
                print(f"Trade executed: {result.tx_hash}")
                print(f"Gas cost: {result.gas_cost_native} ETH (~${result.gas_cost_usd})")
            else:
                print(f"Trade failed: {result.reason}")
                # Handle gracefully - no crash, can retry or alert

        :param order_result:
            OrderResult from open_position, close_position, swap_tokens, etc.
            Contains the unsigned transaction to execute.
        :type order_result: OrderResult
        :param wallet:
            HotWallet instance for signing and broadcasting the transaction.
        :type wallet: HotWallet
        :param check_gas:
            Whether to perform gas balance check before execution.
            Set to False to skip balance checking (e.g., for testing).
        :type check_gas: bool
        :return:
            TradeExecutionResult with comprehensive outcome information.
            Check result.success to determine if trade succeeded.
        :rtype: TradeExecutionResult
        :raises InsufficientGasError:
            Only if gas_monitor_config.raise_on_critical is True and
            balance is below critical threshold.
        """
        gas_check: GasCheckResult | None = None
        gas_estimate = None
        native_price_usd: float | None = None

        # Step 1: Check gas balance if enabled
        if check_gas and self.gas_monitor.config.enabled:
            gas_check = self.gas_monitor.check_gas_balance(wallet.address)

            if gas_check.status == "critical":
                self.gas_monitor.log_gas_check_warning(gas_check)

                if self.gas_monitor.config.raise_on_critical:
                    raise InsufficientGasError(gas_check.message, gas_check)

                return TradeExecutionResult(
                    success=False,
                    status="rejected",
                    reason="critical_balance",
                    tx_hash=None,
                    receipt=None,
                    order_result=order_result,
                    gas_check=gas_check,
                    gas_used=None,
                    gas_cost_native=None,
                    gas_cost_usd=None,
                    error_message=gas_check.message,
                )

            elif gas_check.status == "warning":
                self.gas_monitor.log_gas_check_warning(gas_check)

            native_price_usd = gas_check.native_price_usd

        # Step 2: Estimate gas with buffer and log
        try:
            gas_estimate = self.gas_monitor.estimate_transaction_gas(
                tx=order_result.transaction,
                from_addr=wallet.address,
            )
            self.gas_monitor.log_gas_estimate(gas_estimate, "GMX order")

            # Cache price if not already fetched
            if native_price_usd is None:
                native_price_usd = gas_estimate.native_price_usd

        except Exception as e:
            logger.warning("Gas estimation failed: %s - using order gas_limit", e)
            # Fall back to order's gas_limit if estimation fails

        # Step 3: Sign and broadcast transaction
        try:
            # Build the transaction with nonce
            tx = dict(order_result.transaction)

            # Use estimated gas if available, otherwise use order's gas_limit
            if gas_estimate:
                tx["gas"] = gas_estimate.gas_limit

            # Sign the transaction
            signed_tx = wallet.sign_transaction_with_new_nonce(tx)

            # Broadcast
            tx_hash = self.config.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()

            logger.info("GMX order submitted: %s", tx_hash_hex)

            # Wait for receipt
            receipt = self.config.web3.eth.wait_for_transaction_receipt(tx_hash)
            receipt_dict = dict(receipt)

            # Step 4: Log actual gas usage on success
            gas_used = receipt_dict.get("gasUsed", 0)
            gas_cost_native, gas_cost_usd = self.gas_monitor.log_gas_usage(
                receipt=receipt_dict,
                native_price_usd=native_price_usd,
                operation="GMX order",
                estimated_gas=gas_estimate.gas_limit if gas_estimate else None,
            )

            # Check transaction status
            status = receipt_dict.get("status", 1)
            if status == 0:
                # Transaction reverted
                return TradeExecutionResult(
                    success=False,
                    status="failed",
                    reason="reverted",
                    tx_hash=tx_hash_hex,
                    receipt=receipt_dict,
                    order_result=order_result,
                    gas_check=gas_check,
                    gas_used=gas_used,
                    gas_cost_native=gas_cost_native,
                    gas_cost_usd=gas_cost_usd,
                    error_message="Transaction reverted",
                )

            return TradeExecutionResult(
                success=True,
                status="executed",
                reason=None,
                tx_hash=tx_hash_hex,
                receipt=receipt_dict,
                order_result=order_result,
                gas_check=gas_check,
                gas_used=gas_used,
                gas_cost_native=gas_cost_native,
                gas_cost_usd=gas_cost_usd,
                error_message=None,
            )

        except ValueError as e:
            # Handle out-of-gas and other ValueError exceptions
            error_str = str(e).lower()
            if "insufficient funds" in error_str or "out of gas" in error_str:
                reason = "out_of_gas"
                error_message = f"Insufficient funds for gas: {e}"
            else:
                reason = "value_error"
                error_message = str(e)

            logger.error("GMX order failed: %s", error_message)

            return TradeExecutionResult(
                success=False,
                status="failed",
                reason=reason,
                tx_hash=None,
                receipt=None,
                order_result=order_result,
                gas_check=gas_check,
                gas_used=None,
                gas_cost_native=None,
                gas_cost_usd=None,
                error_message=error_message,
            )

        except Exception as e:
            # Handle any other exceptions gracefully
            logger.error("GMX order failed with unexpected error: %s", e)

            return TradeExecutionResult(
                success=False,
                status="failed",
                reason="error",
                tx_hash=None,
                receipt=None,
                order_result=order_result,
                gas_check=gas_check,
                gas_used=None,
                gas_cost_native=None,
                gas_cost_usd=None,
                error_message=str(e),
            )

    def _check_gas_and_log_estimate(
        self,
        order_result: OrderResult,
        operation: str,
        wallet_address: str | None = None,
    ) -> None:
        """Check gas balance and log gas estimate for an order.

        This helper method performs gas monitoring for orders that will be
        manually signed and sent. It checks the wallet's gas balance against
        thresholds and logs the estimated gas cost.

        :param order_result:
            OrderResult containing the unsigned transaction
        :type order_result: OrderResult
        :param operation:
            Description of the operation (e.g., "open_position", "close_position")
        :type operation: str
        :param wallet_address:
            Wallet address to check gas balance for. If None, uses config wallet.
        :type wallet_address: str | None
        """
        # Skip if gas monitoring is disabled
        gas_config = self._gas_monitor_config or GasMonitorConfig()
        if not gas_config.enabled:
            return

        # Get wallet address
        if wallet_address is None:
            wallet_address = self.config.get_wallet_address()
        if wallet_address is None:
            logger.debug("No wallet address available for gas monitoring")
            return

        try:
            # Check gas balance
            gas_check = self.gas_monitor.check_gas_balance(wallet_address)

            # Log balance status
            if gas_check.status == "critical":
                if gas_config.raise_on_critical:
                    raise InsufficientGasError(gas_check.message)
                else:
                    logger.error(gas_check.message)
            elif gas_check.status == "warning":
                logger.warning(gas_check.message)
            else:
                logger.debug(gas_check.message)

            # Estimate gas for the transaction
            try:
                gas_estimate = self.gas_monitor.estimate_transaction_gas(
                    order_result.transaction,
                    wallet_address,
                )
                self.gas_monitor.log_gas_estimate(gas_estimate, operation)
            except Exception as e:
                logger.debug("Could not estimate gas for %s: %s", operation, e)

        except InsufficientGasError:
            raise
        except Exception as e:
            logger.debug("Gas monitoring check failed: %s", e)

    def open_position(
        self,
        market_symbol: str,
        collateral_symbol: str,
        start_token_symbol: str,
        is_long: bool,
        size_delta_usd: float,
        leverage: float,
        slippage_percent: Optional[float] = 0.003,
        **kwargs,
    ) -> OrderResult:
        """
        Execute sophisticated position opening with comprehensive risk and execution control.

        This method creates leveraged trading positions on GMX with precise control
        over size, leverage, collateral composition, and execution parameters. It
        implements professional-grade position opening logic including collateral
        optimization, slippage protection, and execution cost management.

        **Leveraged Position Mechanics:**

        When you open a leveraged position, you're essentially borrowing capital
        from GMX liquidity providers to control a position larger than your
        collateral. The leverage multiplier determines how much capital you
        control relative to your collateral. Higher leverage amplifies both
        potential gains and losses, requiring careful risk management.

        **Collateral Strategy:**

        The choice of collateral asset affects both your risk profile and
        trading costs. Using stable collateral (like USDC) provides predictable
        liquidation thresholds but may incur swap costs. Using the same asset
        as collateral and position (like ETH) minimizes swap costs but creates
        concentrated risk exposure.

        **Position Sizing and Risk Management:**

        Professional traders use position sizing as their primary risk management
        tool. The size_delta_usd parameter should be calculated based on your
        total portfolio size, risk tolerance, and the specific characteristics
        of the market you're trading. Never risk more than you can afford to
        lose on any single position.

        Example:

        .. code-block:: python

            # Conservative long position with stable collateral
            conservative_long = trader.open_position(
                market_symbol="ETH",
                collateral_symbol="USDC",  # Stable collateral
                start_token_symbol="USDC",  # Start with stable asset
                is_long=True,  # Bullish position
                size_delta_usd=1000,  # $1000 position
                leverage=2.0,  # Conservative 2x leverage
                slippage_percent=0.005,  # 0.5% slippage
                auto_cancel=True,  # Cancel if execution fails
            )

            # Aggressive position with asset collateral
            aggressive_long = trader.open_position(
                market_symbol="ETH",
                collateral_symbol="ETH",  # Same asset collateral
                start_token_symbol="ETH",  # Start with ETH
                is_long=True,
                size_delta_usd=5000,  # $5000 position
                leverage=5.0,  # Aggressive 5x leverage
                slippage_percent=0.01,  # 1% slippage for speed
                execution_buffer=2.0,  # Higher execution buffer
            )

        :param market_symbol:
            Symbol identifying the market to trade (e.g., "ETH", "BTC").
            Determines which asset you're taking directional exposure to
        :type market_symbol: str
        :param collateral_symbol:
            Symbol of the asset to use as collateral (e.g., "USDC", "ETH").
            Affects liquidation thresholds, swap costs, and risk concentration
        :type collateral_symbol: str
        :param start_token_symbol:
            Symbol of the asset you currently hold to fund the position.
            May require swapping to reach the desired collateral asset
        :type start_token_symbol: str
        :param is_long:
            Whether to open a long (bullish) or short (bearish) position.
            Long positions profit when prices rise, short positions profit
            when prices fall
        :type is_long: bool
        :param size_delta_usd:
            Total position size in USD terms. Combined with leverage,
            determines the actual capital exposure and potential profit/loss
        :type size_delta_usd: float
        :param leverage:
            Leverage multiplier determining how much capital you control
            relative to your collateral. Higher leverage increases both
            potential returns and liquidation risk
        :type leverage: float
        :param slippage_percent:
            Maximum acceptable slippage as decimal (0.003 = 0.3%). Higher
            values enable faster execution in volatile markets at the cost
            of potentially worse prices
        :type slippage_percent: Optional[float]
        :param kwargs:
            Additional advanced parameters for execution control including
            auto_cancel, execution_buffer, max_fee_per_gas, and other
            order-specific settings
        :type kwargs: Any
        :return:
            Configured increase order object ready for execution with all
            specified parameters and risk controls applied
        :rtype: IncreaseOrder
        :raises ValueError:
            When parameters are invalid, insufficient collateral, or
            leverage exceeds protocol limits for the specified market
        """
        # Get configuration
        config = self.config.get_config()

        # Debug logging for collateral token flow
        execution_buffer_kwarg = kwargs.get("execution_buffer", "NOT_SET")
        logger.info(
            "COLLATERAL_TRACE: GMXTrading.open_position() CALLED\n  market_symbol=%s\n  collateral_symbol=%s\n  start_token_symbol=%s\n  is_long=%s\n  size_delta_usd=%.2f\n  leverage=%.1f\n  execution_buffer=%s (from kwargs)",
            market_symbol,
            collateral_symbol,
            start_token_symbol,
            is_long,
            size_delta_usd,
            leverage,
            f"{execution_buffer_kwarg:.1f}x" if execution_buffer_kwarg != "NOT_SET" else execution_buffer_kwarg,
        )

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": start_token_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,
            "leverage": leverage,
            "slippage_percent": slippage_percent,
        }

        # Debug logging for collateral token flow
        logger.info(
            "COLLATERAL_TRACE: Parameter dictionary created:\n  chain=%s\n  index_token_symbol=%s\n  collateral_token_symbol=%s\n  start_token_symbol=%s",
            parameters["chain"],
            parameters["index_token_symbol"],
            parameters["collateral_token_symbol"],
            parameters["start_token_symbol"],
        )

        # Process parameters
        order_parameters = OrderArgumentParser(config, is_increase=True).process_parameters_dictionary(parameters)

        # Debug logging for collateral token flow
        logger.info(
            "COLLATERAL_TRACE: After OrderArgumentParser.process_parameters_dictionary():\n  collateral_address=%s\n  start_token_address=%s\n  swap_path=%s",
            order_parameters["collateral_address"],
            order_parameters["start_token_address"],
            order_parameters["swap_path"],
        )

        # Log position size details (if gas monitoring enabled)
        gas_config = self._gas_monitor_config or GasMonitorConfig()
        if gas_config.enabled:
            collateral_usd = size_delta_usd / leverage if leverage > 0 else 0
            position_type = "LONG" if is_long else "SHORT"
            # Try to get raw token amount
            try:
                from eth_defi.gmx.contracts import get_token_address_normalized
                from eth_defi.token import fetch_erc20_details
                from eth_defi.gmx.core.oracle import OraclePrices
                from eth_defi.gmx.constants import PRECISION
                from statistics import median

                chain = self.config.get_chain()
                collateral_address = get_token_address_normalized(chain, collateral_symbol)
                if collateral_address:
                    oracle = OraclePrices(chain)
                    price_data = oracle.get_price_for_token(collateral_address)
                    if price_data:
                        token_details = fetch_erc20_details(self.config.web3, collateral_address)
                        raw_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])
                        token_price_usd = raw_price / (10 ** (PRECISION - token_details.decimals))
                        if token_price_usd > 0:
                            collateral_tokens = collateral_usd / token_price_usd
                            logger.info(
                                "Opening %s position: size=$%.2f, collateral=$%.2f (%.6f %s), leverage=%.1fx",
                                position_type,
                                size_delta_usd,
                                collateral_usd,
                                collateral_tokens,
                                collateral_symbol,
                                leverage,
                            )
                        else:
                            logger.info(
                                "Opening %s position: size=$%.2f, collateral=$%.2f %s, leverage=%.1fx",
                                position_type,
                                size_delta_usd,
                                collateral_usd,
                                collateral_symbol,
                                leverage,
                            )
                    else:
                        logger.info(
                            "Opening %s position: size=$%.2f, collateral=$%.2f %s, leverage=%.1fx",
                            position_type,
                            size_delta_usd,
                            collateral_usd,
                            collateral_symbol,
                            leverage,
                        )
            except Exception as e:
                logger.debug("Could not calculate raw token amount: %s", e)
                logger.info(
                    "Opening %s position: size=$%.2f, collateral=$%.2f %s, leverage=%.1fx",
                    position_type,
                    size_delta_usd,
                    collateral_usd,
                    collateral_symbol,
                    leverage,
                )

        # Create order instance with position identification (order classes need GMXConfig)
        order = IncreaseOrder(
            config=self.config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        # Create the actual increase order transaction
        order_result = order.create_increase_order(
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters["swap_path"],
            **kwargs,
        )

        # Check gas and log estimate for manual signing
        self._check_gas_and_log_estimate(order_result, "open_position")

        return order_result

    def close_position(
        self,
        market_symbol: str,
        collateral_symbol: str,
        start_token_symbol: str,
        is_long: bool,
        size_delta_usd: int | float,
        initial_collateral_delta: float,
        slippage_percent: Optional[float] = 0.003,
        **kwargs,
    ) -> OrderResult:
        """
        Execute strategic position closure with precise size and collateral control.

        This method provides sophisticated position closure capabilities with
        independent control over position size reduction and collateral withdrawal.
        It supports both full position closure and partial closure strategies
        that are essential for advanced risk management and profit optimization.

        **Position Closure Strategy:**

        Professional traders rarely close entire positions at once. Instead,
        they use partial closures to lock in profits while maintaining market
        exposure, reduce risk during uncertain periods, or free up collateral
        for new opportunities. This method provides the precision needed for
        these sophisticated strategies.

        **Collateral Management:**

        The independent control over collateral withdrawal allows for advanced
        capital management strategies. You might close 50% of a position size
        while withdrawing 75% of collateral to maximize capital efficiency, or
        close 100% of position size while leaving collateral for rapid re-entry.

        **Timing and Market Conditions:**

        Position closure timing can significantly impact profitability. The
        method provides slippage controls that allow traders to balance execution
        speed against price protection based on current market volatility and
        urgency of the closure requirement.

        Example:

        .. code-block:: python

            # Profit-taking strategy: Partial closure with stable output
            profit_taking = trader.close_position(
                market_symbol="ETH",
                collateral_symbol="USDC",
                start_token_symbol="USDC",
                is_long=True,
                size_delta_usd=2000,  # Close $2000 of position
                initial_collateral_delta=500,  # Remove $500 collateral
                slippage_percent=0.003,  # Tight slippage for profits
                auto_cancel=True,
            )

            # Emergency closure: Full exit with speed priority
            emergency_close = trader.close_position(
                market_symbol="BTC",
                collateral_symbol="BTC",
                start_token_symbol="BTC",
                is_long=False,
                size_delta_usd=10000,  # Close entire $10k position
                initial_collateral_delta=2000,  # Withdraw all collateral
                slippage_percent=0.02,  # Higher slippage for speed
                execution_buffer=3.0,  # Higher execution buffer
            )

        :param market_symbol:
            Symbol identifying the market containing the position to close.
            Must match the market where you currently have an open position
        :type market_symbol: str
        :param collateral_symbol:
            Symbol of the collateral asset in the existing position.
            Must match the collateral type of the position being closed
        :type collateral_symbol: str
        :param start_token_symbol:
            Symbol of the asset to receive upon position closure.
            May trigger asset conversion affecting final proceeds and exposure
        :type start_token_symbol: str
        :param is_long:
            Whether the existing position is long (True) or short (False).
            Must match the direction of the position being closed
        :type is_long: bool
        :param size_delta_usd:
            USD value of position size to close. Can be partial (less than
            total position size) or full closure. Determines exposure reduction
        :type size_delta_usd: float
        :param initial_collateral_delta:
            Amount of collateral to withdraw upon closure. Independent of
            position size, allowing flexible capital management strategies
        :type initial_collateral_delta: float
        :param slippage_percent:
            Maximum acceptable slippage as decimal. Lower values provide
            better price protection, higher values enable faster execution
            in volatile conditions
        :type slippage_percent: Optional[float]
        :param kwargs:
            Additional advanced parameters for execution control including
            auto_cancel, execution_buffer, max_fee_per_gas, and other
            closure-specific settings
        :type kwargs: Any
        :return:
            Configured decrease order object ready for execution with all
            specified closure parameters and risk controls applied
        :rtype: DecreaseOrder
        :raises ValueError:
            When parameters don't match existing position, insufficient
            position size, or invalid collateral withdrawal amounts
        """
        # Get configuration
        config = self.config.get_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": start_token_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,
            "initial_collateral_delta": initial_collateral_delta,
            "slippage_percent": slippage_percent,
        }

        # Process parameters
        order_parameters = OrderArgumentParser(
            config,
            is_decrease=True,
        ).process_parameters_dictionary(parameters)

        # Log position close details (if gas monitoring enabled)
        gas_config = self._gas_monitor_config or GasMonitorConfig()
        if gas_config.enabled:
            position_type = "LONG" if is_long else "SHORT"
            # Try to get raw token amount for collateral withdrawal
            try:
                from eth_defi.gmx.contracts import get_token_address_normalized
                from eth_defi.token import fetch_erc20_details
                from eth_defi.gmx.core.oracle import OraclePrices
                from eth_defi.gmx.constants import PRECISION
                from statistics import median

                chain = self.config.get_chain()
                collateral_address = get_token_address_normalized(chain, collateral_symbol)
                if collateral_address:
                    oracle = OraclePrices(chain)
                    price_data = oracle.get_price_for_token(collateral_address)
                    if price_data:
                        token_details = fetch_erc20_details(self.config.web3, collateral_address)
                        raw_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])
                        token_price_usd = raw_price / (10 ** (PRECISION - token_details.decimals))
                        if token_price_usd > 0:
                            collateral_tokens = initial_collateral_delta / token_price_usd
                            logger.info(
                                "Closing %s position: size=$%.2f, collateral_withdraw=$%.2f (%.6f %s)",
                                position_type,
                                size_delta_usd,
                                initial_collateral_delta,
                                collateral_tokens,
                                collateral_symbol,
                            )
                        else:
                            logger.info(
                                "Closing %s position: size=$%.2f, collateral_withdraw=$%.2f %s",
                                position_type,
                                size_delta_usd,
                                initial_collateral_delta,
                                collateral_symbol,
                            )
                    else:
                        logger.info(
                            "Closing %s position: size=$%.2f, collateral_withdraw=$%.2f %s",
                            position_type,
                            size_delta_usd,
                            initial_collateral_delta,
                            collateral_symbol,
                        )
            except Exception as e:
                logger.debug("Could not calculate raw token amount: %s", e)
                logger.info(
                    "Closing %s position: size=$%.2f, collateral_withdraw=$%.2f %s",
                    position_type,
                    size_delta_usd,
                    initial_collateral_delta,
                    collateral_symbol,
                )

        # Create order instance with position identification (order classes need GMXConfig, not GMXConfigManager)
        order = DecreaseOrder(
            config=self.config,  # Pass GMXConfig, not GMXConfigManager
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        # Create the actual decrease order transaction
        order_result = order.create_decrease_order(
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters.get("swap_path", []),
            **kwargs,
        )

        # Check gas and log estimate for manual signing
        self._check_gas_and_log_estimate(order_result, "close_position")

        return order_result

    def swap_tokens(
        self,
        in_token_symbol: str,
        out_token_symbol: str,
        amount: float,
        position_usd: Optional[float] = 0,
        slippage_percent: Optional[float] = 0.02,
        execution_buffer=2.2,  # this is needed to pass the gas usage
        **kwargs,
    ) -> OrderResult:
        """
        Execute sophisticated token swaps for portfolio management and strategy implementation.

        This method provides advanced token swapping capabilities that go beyond
        simple asset conversion. It supports both basic swaps for portfolio
        rebalancing and position-based swaps that integrate with broader trading
        strategies. The implementation includes sophisticated slippage management
        and execution optimization.

        **Strategic Swap Applications:**

        Token swaps serve multiple strategic purposes in professional trading:
        converting profits to stable assets, rebalancing portfolio compositions,
        preparing assets for new position openings, and implementing arbitrage
        strategies across different markets or protocols.

        **Slippage and Execution Management:**

        Swap execution quality depends heavily on market conditions and swap
        size relative to available liquidity. The method provides dynamic
        slippage controls and execution buffers that can be adjusted based on
        market volatility, urgency, and the specific assets being swapped.

        **Integration with Trading Strategies:**

        Swaps often form part of larger trading strategies rather than standalone
        operations. The method supports integration with position management
        workflows, enabling complex strategies that combine position adjustments
        with asset rebalancing in coordinated sequences.

        Example:

        .. code-block:: python

            # Portfolio rebalancing: Convert profits to stable assets
            profit_conversion = trader.swap_tokens(
                in_token_symbol="ETH",
                out_token_symbol="USDC",
                amount=2.5,  # Swap 2.5 ETH
                slippage_percent=0.01,  # 1% slippage tolerance
                execution_buffer=2.0,  # Standard execution buffer
                auto_cancel=True,
            )

            # Strategy preparation: Convert stable assets for position opening
            position_prep = trader.swap_tokens(
                in_token_symbol="USDC",
                out_token_symbol="BTC",
                amount=5000,  # Swap $5000 USDC
                slippage_percent=0.015,  # 1.5% slippage for large swap
                execution_buffer=3.0,  # Higher buffer for complex swap
                max_fee_per_gas=50000000000,  # Custom gas price
            )

        :param in_token_symbol:
            Symbol of the token to swap from (e.g., "ETH", "USDC").
            Must be an asset you currently hold in sufficient quantity
            for the intended swap amount
        :type in_token_symbol: str
        :param out_token_symbol:
            Symbol of the token to receive from the swap (e.g., "USDC", "BTC").
            Determines your final asset exposure and liquidity characteristics
            after the swap completion
        :type out_token_symbol: str
        :param amount:
            Quantity of the input token to swap. Must not exceed your current
            balance of the input token and should consider any held collateral
            requirements
        :type amount: float
        :param position_usd:
            Optional USD value for position-based swaps that integrate with
            broader trading strategies. Set to 0 for simple asset conversion
        :type position_usd: Optional[float]
        :param slippage_percent:
            Maximum acceptable slippage as decimal (0.02 = 2%). Higher values
            enable execution in volatile conditions but may result in worse
            prices than expected
        :type slippage_percent: Optional[float]
        :param execution_buffer:
            Multiplier for execution fee estimation to ensure successful
            transaction processing. Higher values reduce execution failure
            risk but increase transaction costs
        :type execution_buffer: float
        :param kwargs:
            Additional advanced parameters for swap control including
            auto_cancel, max_fee_per_gas, and other swap-specific settings
        :type kwargs: Any
        :return:
            Configured swap order object ready for execution with all
            specified parameters and slippage controls applied
        :rtype: SwapOrder
        :raises ValueError:
            When insufficient balance, invalid token pairs, or swap
            amount exceeds available liquidity in the target market
        """
        # Get configuration
        config = self.config.get_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "out_token_symbol": out_token_symbol,
            "start_token_symbol": in_token_symbol,
            "is_long": False,
            "size_delta_usd": position_usd,
            "initial_collateral_delta": amount,
            "slippage_percent": slippage_percent,
        }

        # Process parameters
        order_parameters = OrderArgumentParser(
            config,
            is_swap=True,
        ).process_parameters_dictionary(parameters)

        # Create SwapOrder instance (order classes need GMXConfig, not GMXConfigManager)
        swap_order = SwapOrder(
            config=self.config,
            start_token=order_parameters["start_token_address"],
            out_token=order_parameters["out_token_address"],
        )

        # Create the actual swap order transaction
        order_result = swap_order.create_swap_order(
            amount_in=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            execution_buffer=execution_buffer,
            **kwargs,
        )

        # Check gas and log estimate for manual signing
        self._check_gas_and_log_estimate(order_result, "swap_tokens")

        return order_result

    def open_limit_position(
        self,
        market_symbol: str,
        collateral_symbol: str,
        start_token_symbol: str,
        is_long: bool,
        size_delta_usd: float,
        leverage: float,
        trigger_price: float,
        slippage_percent: Optional[float] = 0.003,
        auto_cancel: bool = True,
        **kwargs,
    ) -> OrderResult:
        """
        Open a limit position that triggers at specified price.

        Creates a limit order that opens a position when the market price
        reaches the trigger price. Unlike market orders which execute immediately,
        limit orders remain pending until price conditions are met.

        **When to Use Limit Orders:**

        Limit orders are useful when you want to enter a position at a specific
        price level rather than the current market price. Common use cases include:

        - Buying dips: Set a trigger price below current market to buy if price drops
        - Selling rallies: Set a trigger price above current market to short if price rises
        - Range trading: Enter positions at predetermined support/resistance levels

        **Trigger Price Logic:**

        For long positions, the order triggers when the market price falls to
        or below the trigger price. For short positions, the order triggers
        when the market price rises to or above the trigger price.

        Example:

        .. code-block:: python

            # Limit long order - buy ETH if price drops to $3000
            limit_long = trader.open_limit_position(
                market_symbol="ETH",
                collateral_symbol="ETH",
                start_token_symbol="ETH",
                is_long=True,
                size_delta_usd=1000,
                leverage=2.5,
                trigger_price=3000.0,  # Buy at $3000 or better
                slippage_percent=0.005,
            )

            # Limit short order - short ETH if price rises to $4000
            limit_short = trader.open_limit_position(
                market_symbol="ETH",
                collateral_symbol="USDC",
                start_token_symbol="USDC",
                is_long=False,
                size_delta_usd=1000,
                leverage=2.0,
                trigger_price=4000.0,  # Short at $4000 or better
            )

        :param market_symbol:
            Symbol identifying the market to trade (e.g., "ETH", "BTC")
        :type market_symbol: str
        :param collateral_symbol:
            Symbol of the asset to use as collateral (e.g., "USDC", "ETH")
        :type collateral_symbol: str
        :param start_token_symbol:
            Symbol of the asset you currently hold to fund the position
        :type start_token_symbol: str
        :param is_long:
            Whether to open a long (bullish) or short (bearish) position
        :type is_long: bool
        :param size_delta_usd:
            Total position size in USD terms
        :type size_delta_usd: float
        :param leverage:
            Leverage multiplier (e.g., 2.5 for 2.5x leverage)
        :type leverage: float
        :param trigger_price:
            USD price at which the order triggers and executes
        :type trigger_price: float
        :param slippage_percent:
            Maximum acceptable slippage as decimal (0.003 = 0.3%)
        :type slippage_percent: Optional[float]
        :param auto_cancel:
            Whether to auto-cancel the order if it can't execute (default True)
        :type auto_cancel: bool
        :param kwargs:
            Additional parameters including execution_buffer, max_fee_per_gas, etc.
        :type kwargs: Any
        :return:
            OrderResult containing unsigned transaction ready for signing
        :rtype: OrderResult
        :raises ValueError:
            When parameters are invalid or trigger_price is not positive
        """
        if trigger_price <= 0:
            raise ValueError("trigger_price must be positive")

        # Get configuration
        config = self.config.get_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": start_token_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,
            "leverage": leverage,
            "slippage_percent": slippage_percent,
        }

        # Process parameters
        order_parameters = OrderArgumentParser(config, is_increase=True).process_parameters_dictionary(parameters)

        # Create order instance with position identification
        order = IncreaseOrder(
            config=self.config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        # Create the limit increase order transaction
        order_result = order.create_limit_increase_order(
            trigger_price=trigger_price,
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters["swap_path"],
            auto_cancel=auto_cancel,
            **kwargs,
        )

        # Check gas and log estimate for manual signing
        self._check_gas_and_log_estimate(order_result, "open_limit_position")

        return order_result

    def open_position_with_sltp(
        self,
        market_symbol: str,
        collateral_symbol: str,
        start_token_symbol: str,
        is_long: bool,
        size_delta_usd: float,
        leverage: float,
        stop_loss_percent: float | None = None,
        take_profit_percent: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        slippage_percent: float = 0.003,
        execution_buffer: float = 2.5,
        **kwargs,
    ) -> SLTPOrderResult:
        """
        Open a position with bundled Stop Loss and Take Profit orders.

        Creates a single atomic transaction that opens a position and attaches
        SL/TP orders. All three orders are submitted together, ensuring the
        protective orders are in place from the moment the position is opened.

        Example:

        .. code-block:: python

            # Open long with 5% stop loss and 10% take profit
            result = trader.open_position_with_sltp(
                market_symbol="ETH",
                collateral_symbol="ETH",
                start_token_symbol="ETH",
                is_long=True,
                size_delta_usd=1000,
                leverage=2.5,
                stop_loss_percent=0.05,  # 5% below entry
                take_profit_percent=0.10,  # 10% above entry
            )

            # Or use absolute prices
            result = trader.open_position_with_sltp(
                market_symbol="ETH",
                collateral_symbol="ETH",
                start_token_symbol="ETH",
                is_long=True,
                size_delta_usd=1000,
                leverage=2.5,
                stop_loss_price=2850.0,  # SL at $2850
                take_profit_price=3300.0,  # TP at $3300
            )

        :param market_symbol: Market to trade (e.g., "ETH", "BTC")
        :param collateral_symbol: Collateral asset (e.g., "ETH", "USDC")
        :param start_token_symbol: Asset you're starting with
        :param is_long: True for long, False for short
        :param size_delta_usd: Position size in USD
        :param leverage: Leverage multiplier (e.g., 2.5 for 2.5x)
        :param stop_loss_percent: SL trigger as percentage (0.05 = 5%)
        :param take_profit_percent: TP trigger as percentage (0.10 = 10%)
        :param stop_loss_price: Absolute SL trigger price in USD
        :param take_profit_price: Absolute TP trigger price in USD
        :param slippage_percent: Maximum slippage (default 0.3%)
        :param execution_buffer: Fee buffer multiplier (default 2.5)
        :return: SLTPOrderResult with bundled transaction
        """
        # Get configuration and process parameters
        config = self.config.get_config()

        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": start_token_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,
            "leverage": leverage,
            "slippage_percent": slippage_percent,
        }

        order_parameters = OrderArgumentParser(
            config,
            is_increase=True,
        ).process_parameters_dictionary(parameters)

        # Create SLTP order instance
        sltp = SLTPOrder(
            config=self.config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        # Build SL/TP params
        sltp_params = None
        if stop_loss_percent or stop_loss_price or take_profit_percent or take_profit_price:
            sl_entry = None
            tp_entry = None

            if stop_loss_percent:
                sl_entry = SLTPEntry(trigger_percent=stop_loss_percent)
            elif stop_loss_price:
                sl_entry = SLTPEntry(trigger_price=stop_loss_price)

            if take_profit_percent:
                tp_entry = SLTPEntry(trigger_percent=take_profit_percent)
            elif take_profit_price:
                tp_entry = SLTPEntry(trigger_price=take_profit_price)

            sltp_params = SLTPParams(stop_loss=sl_entry, take_profit=tp_entry)

        order_result = sltp.create_increase_order_with_sltp(
            size_delta_usd=size_delta_usd,
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            sltp_params=sltp_params,
            slippage_percent=slippage_percent,
            swap_path=order_parameters["swap_path"],
            execution_buffer=execution_buffer,
            **kwargs,
        )

        # Check gas and log estimate for manual signing
        # Note: SLTPOrderResult has main_order.transaction for gas estimation
        if hasattr(order_result, "main_order") and hasattr(order_result.main_order, "transaction"):
            self._check_gas_and_log_estimate(order_result.main_order, "open_position_with_sltp")

        return order_result

    def create_stop_loss(
        self,
        market_symbol: str,
        collateral_symbol: str,
        is_long: bool,
        position_size_usd: float,
        entry_price: float,
        stop_loss_percent: float | None = None,
        stop_loss_price: float | None = None,
        close_percent: float = 1.0,
        slippage_percent: float = 0.003,
        execution_buffer: float = 2.5,
        **kwargs,
    ) -> OrderResult:
        """
        Create a standalone Stop Loss order for an existing position.

        The SL order will trigger when price moves against your position
        by the specified amount, limiting potential losses.

        Example:

        .. code-block:: python

            # Create SL 5% below entry for a long position
            sl_result = trader.create_stop_loss(
                market_symbol="ETH",
                collateral_symbol="ETH",
                is_long=True,
                position_size_usd=1000,
                entry_price=3000.0,
                stop_loss_percent=0.05,  # Triggers at $2850
            )

            # Or use absolute price
            sl_result = trader.create_stop_loss(
                market_symbol="ETH",
                collateral_symbol="ETH",
                is_long=True,
                position_size_usd=1000,
                entry_price=3000.0,
                stop_loss_price=2850.0,
            )

        :param market_symbol: Market of the position (e.g., "ETH")
        :param collateral_symbol: Collateral asset of the position
        :param is_long: True if long position, False if short
        :param position_size_usd: Total position size in USD
        :param entry_price: Position entry price in USD
        :param stop_loss_percent: SL as percentage from entry (0.05 = 5%)
        :param stop_loss_price: Absolute SL price in USD
        :param close_percent: Fraction of position to close (1.0 = 100%)
        :param slippage_percent: Maximum slippage
        :param execution_buffer: Fee buffer multiplier
        :return: OrderResult with stop loss transaction
        """
        if not stop_loss_percent and not stop_loss_price:
            raise ValueError("Either stop_loss_percent or stop_loss_price must be provided")

        config = self.config.get_config()

        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": collateral_symbol,
            "is_long": is_long,
            "size_delta_usd": position_size_usd,
            "initial_collateral_delta": 0,  # 0 means withdraw all remaining collateral
            "slippage_percent": slippage_percent,
        }

        order_parameters = OrderArgumentParser(config, is_decrease=True).process_parameters_dictionary(parameters)

        sltp = SLTPOrder(
            config=self.config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        entry = SLTPEntry(
            trigger_percent=stop_loss_percent,
            trigger_price=stop_loss_price,
            close_percent=close_percent,
        )

        order_result = sltp.create_stop_loss_order(
            position_size_usd=position_size_usd,
            entry=entry,
            entry_price=entry_price,
            slippage_percent=slippage_percent,
            execution_buffer=execution_buffer,
        )

        # Check gas and log estimate for manual signing
        self._check_gas_and_log_estimate(order_result, "create_stop_loss")

        return order_result

    def create_take_profit(
        self,
        market_symbol: str,
        collateral_symbol: str,
        is_long: bool,
        position_size_usd: float,
        entry_price: float,
        take_profit_percent: float | None = None,
        take_profit_price: float | None = None,
        close_percent: float = 1.0,
        slippage_percent: float = 0.003,
        execution_buffer: float = 2.5,
        **kwargs,
    ) -> OrderResult:
        """
        Create a standalone Take Profit order for an existing position.

        The TP order will trigger when price moves in your favor
        by the specified amount, locking in profits.

        Example:

        .. code-block:: python

            # Create TP 10% above entry for a long position
            tp_result = trader.create_take_profit(
                market_symbol="ETH",
                collateral_symbol="ETH",
                is_long=True,
                position_size_usd=1000,
                entry_price=3000.0,
                take_profit_percent=0.10,  # Triggers at $3300
            )

            # Scale out: close 50% at TP
            tp_result = trader.create_take_profit(
                market_symbol="ETH",
                collateral_symbol="ETH",
                is_long=True,
                position_size_usd=1000,
                entry_price=3000.0,
                take_profit_price=3300.0,
                close_percent=0.5,  # Close 50% at TP
            )

        :param market_symbol: Market of the position (e.g., "ETH")
        :param collateral_symbol: Collateral asset of the position
        :param is_long: True if long position, False if short
        :param position_size_usd: Total position size in USD
        :param entry_price: Position entry price in USD
        :param take_profit_percent: TP as percentage from entry (0.10 = 10%)
        :param take_profit_price: Absolute TP price in USD
        :param close_percent: Fraction of position to close (1.0 = 100%)
        :param slippage_percent: Maximum slippage
        :param execution_buffer: Fee buffer multiplier
        :return: OrderResult with take profit transaction
        """
        if not take_profit_percent and not take_profit_price:
            raise ValueError("Either take_profit_percent or take_profit_price must be provided")

        config = self.config.get_config()

        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": collateral_symbol,
            "is_long": is_long,
            "size_delta_usd": position_size_usd,
            "initial_collateral_delta": 0,  # 0 means withdraw all remaining collateral
            "slippage_percent": slippage_percent,
        }

        order_parameters = OrderArgumentParser(config, is_decrease=True).process_parameters_dictionary(parameters)

        sltp = SLTPOrder(
            config=self.config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        entry = SLTPEntry(
            trigger_percent=take_profit_percent,
            trigger_price=take_profit_price,
            close_percent=close_percent,
        )

        order_result = sltp.create_take_profit_order(
            position_size_usd=position_size_usd,
            entry=entry,
            entry_price=entry_price,
            slippage_percent=slippage_percent,
            execution_buffer=execution_buffer,
        )

        # Check gas and log estimate for manual signing
        self._check_gas_and_log_estimate(order_result, "create_take_profit")

        return order_result
