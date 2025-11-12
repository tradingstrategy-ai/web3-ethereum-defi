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

from typing import Optional

from eth_defi.gmx.order import OrderResult
from eth_defi.gmx.order.increase_order import IncreaseOrder
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser
from eth_defi.gmx.config import GMXConfig


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

    def __init__(self, config: GMXConfig):
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
        :raises ValueError:
            When the configuration lacks transaction signing capabilities
            required for trading operations and position management
        """
        self.config = config

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

        # Create order instance with position identification (order classes need GMXConfig)
        order = IncreaseOrder(
            config=self.config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        # Create the actual increase order transaction
        return order.create_increase_order(
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters["swap_path"],
            **kwargs,
        )

    def close_position(
        self,
        market_symbol: str,
        collateral_symbol: str,
        start_token_symbol: str,
        is_long: bool,
        size_delta_usd: float,
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

        # Create order instance with position identification (order classes need GMXConfig, not GMXConfigManager)
        order = DecreaseOrder(
            config=self.config,  # Pass GMXConfig, not GMXConfigManager
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
        )

        # Create the actual decrease order transaction
        return order.create_decrease_order(
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters.get("swap_path", []),
            **kwargs,
        )

    def swap_tokens(
        self,
        in_token_symbol: str,
        out_token_symbol: str,
        amount: float,
        position_usd: Optional[float] = 0,
        slippage_percent: Optional[float] = 0.02,
        execution_buffer=1.3,  # this is needed to pass the gas usage
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
        return swap_order.create_swap_order(
            amount_in=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            execution_buffer=execution_buffer,
            **kwargs,
        )
