"""
GMX Order Management Module

This module provides sophisticated order management and position control functionality
for the GMX protocol, implementing professional-grade trading system capabilities
through a unified, high-level interface. It serves as the command center for all
position lifecycle management, from opening to closing, with comprehensive risk
controls and flexible execution strategies.

**Position Management Philosophy:**

Professional trading systems recognize that opening a position is often the easy
part - the real skill lies in managing that position over time and closing it
optimally. The GMX Order Manager implements this philosophy by providing multiple
pathways for position closure, each designed for different strategic scenarios
and risk management requirements.

**Key Trading System Concepts:**

- **Position Lifecycle Management**: Complete control over position opening, monitoring, and closing
- **Risk Parameter Control**: Precise slippage, size, and collateral management
- **Multi-Strategy Support**: Different closing methods for different trading strategies
- **Flexible Asset Handling**: Choose exactly which assets to receive upon position closure

**Order Execution Strategies:**

The module supports multiple order execution patterns to accommodate different
trading styles and market conditions:

- **Parameter-Driven Orders**: Direct control over all order parameters for algorithmic strategies
- **Key-Based Position Management**: Simplified position closure using position identifiers
- **Partial Position Management**: Precise control over position sizing and collateral removal
- **Multi-Address Support**: Manage positions across different wallet addresses

**Risk Management Integration:**

Every order execution method includes comprehensive risk controls including slippage
protection, position size validation, and collateral management. The system prevents
common trading errors through parameter validation while maintaining the flexibility
needed for sophisticated trading strategies.

**Real-World Trading Scenarios:**

- **Profit Taking**: Close profitable positions while preserving capital for new opportunities
- **Loss Cutting**: Quickly exit losing positions to prevent further capital erosion
- **Portfolio Rebalancing**: Adjust position sizes to maintain desired risk exposures
- **Liquidity Management**: Convert positions to desired assets based on market conditions
- **Emergency Risk Management**: Rapidly close positions during adverse market conditions

Example:

.. code-block:: python

    # Professional position management workflow
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig.from_private_key(web3, "0x...", chain="arbitrum")
    order_manager = GMXOrderManager(config)

    # Step 1: Assess current position portfolio
    positions = order_manager.get_open_positions()
    print(f"Currently managing {len(positions)} positions")

    # Step 2: Strategic position closure using key-based method
    if "ETH_long" in positions:
        # Take 50% profits while keeping position open
        close_order = order_manager.close_position_by_key(
            position_key="ETH_long",
            out_token_symbol="USDC",  # Convert to stable value
            amount_of_position_to_close=0.5,  # Close half the position
            amount_of_collateral_to_remove=0.3,  # Remove some collateral
            slippage_percent=0.005,  # 0.5% slippage tolerance
        )

    # Step 3: Algorithmic position management with precise parameters
    risk_parameters = {
        "chain": "arbitrum",
        "index_token_symbol": "SOL",
        "collateral_token_symbol": "SOL",
        "start_token_symbol": "SOL",
        "is_long": True,
        "size_delta_usd": 1000,  # Close $1000 of position
        "initial_collateral_delta": 50,  # Remove 50 tokens collateral
        "slippage_percent": 0.01,  # 1% slippage for volatile market
    }

    algorithmic_order = order_manager.close_position(
        parameters=risk_parameters,
    )

    # Step 4: Monitor and execute orders
    tx_receipt = algorithmic_order.submit()
    print(f"Position closed: {tx_receipt.transactionHash.hex()}")

**Integration with Trading Strategies:**

The order manager is designed to integrate seamlessly with automated trading
strategies, risk management systems, and portfolio optimization algorithms.
Its flexible parameter structure and comprehensive validation make it suitable
for both manual trading and systematic strategy implementation.

Note:
    All order operations require wallet configuration with transaction signing
    capabilities. Test your strategies thoroughly before live execution.

Warning:
    Position management involves significant financial risk. Improper order
    execution can result in substantial losses. Always validate parameters
    and test strategies thoroughly before live execution.
"""

from typing import Any

from eth_typing import ChecksumAddress as Address

from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.order import OrderResult
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.utils import transform_open_position_to_order_parameters


class GMXOrderManager:
    """
    Comprehensive order management and position control system for GMX protocol trading.

    This class implements the sophisticated order management capabilities needed
    for professional trading operations, providing multiple pathways for position
    lifecycle management with comprehensive risk controls and strategic flexibility.
    The design follows professional trading system patterns where position
    management is treated as equally important as position opening.

    **Trading System Architecture:**

    The order manager operates as the central command system for all position
    modifications, implementing industry-standard risk management patterns while
    maintaining the flexibility needed for diverse trading strategies. It bridges
    the gap between high-level trading intentions and low-level protocol interactions.

    **Position Management Patterns:**

    Professional traders use different position management approaches based on
    market conditions, strategy requirements, and risk tolerance. The order
    manager supports all major patterns:

    - **Systematic Management**: Algorithm-driven position adjustments based on predefined rules
    - **Discretionary Management**: Manual position adjustments based on market analysis
    - **Risk-Based Management**: Position modifications triggered by risk metrics
    - **Opportunity-Based Management**: Position adjustments to capture new market opportunities

    **Risk Control Integration:**

    Every order execution path includes comprehensive validation and risk controls.
    The system prevents common trading errors while maintaining the parameter
    flexibility needed for sophisticated strategies. Risk controls include slippage
    protection, position size validation, collateral adequacy checks, and parameter
    consistency verification.

    :ivar config: GMX configuration object containing network and wallet settings
    :vartype config: GMXConfig
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize the order management system with GMX configuration and validation.

        This constructor establishes the order management system with comprehensive
        configuration validation to ensure all necessary components are available
        for safe order execution. Since order management involves financial
        transactions, the configuration must include transaction signing capabilities.

        :param config:
            Complete GMX configuration object containing network settings and
            wallet credentials. Must have write capability enabled as all order
            operations require transaction signing and execution
        :type config: GMXConfig
        :raises ValueError:
            When the configuration lacks transaction signing capabilities
            required for order execution and position management
        """
        self.config = config

    def get_open_positions(self, address: str | Address | None = None) -> dict[str, Any]:
        """
        Retrieve comprehensive information about all open trading positions for a specified address.

        This method provides the foundation for all position management operations
        by returning detailed information about current positions including sizes,
        collateral amounts, profit/loss status, and risk metrics. The information
        is essential for making informed decisions about position modifications,
        risk management, and strategic adjustments.

        **Position Data Structure:**

        The returned data includes all information needed for sophisticated position
        analysis including entry prices, current values, margin requirements,
        liquidation thresholds, and accumulated funding costs. This comprehensive
        view enables both manual analysis and algorithmic position management.

        :param address:
            Ethereum wallet address to query positions for. If not provided,
            uses the wallet address from the GMX configuration. Must be a valid
            Ethereum address format (0x... or ENS name)
        :type address: str | Address | None
        :return:
            Dictionary containing detailed position information with position
            keys as dictionary keys and comprehensive position data as values,
            including sizes, collateral, PnL, and risk metrics
        :rtype: dict[str, any]
        :raises ValueError:
            When no wallet address is available either from parameter or
            configuration, making position queries impossible
        """
        if address is None:
            address = self.config.get_wallet_address()

        if not address:
            raise ValueError("No wallet address provided")

        config = self.config.get_config()
        return GetOpenPositions(config).get_data(address)

    def close_position(self, parameters: dict) -> OrderResult:
        """
        Execute sophisticated position closure using comprehensive parameter control.

        This method provides the most flexible and powerful approach to position
        management by accepting a complete parameter dictionary that specifies
        every aspect of the order execution. It's designed for algorithmic trading
        strategies and sophisticated manual trading where precise control over
        execution parameters is essential.

        **Parameter-Driven Trading:**

        This approach is particularly valuable for systematic trading strategies
        where order parameters are calculated algorithmically based on market
        conditions, risk metrics, or portfolio optimization algorithms. The
        comprehensive parameter structure ensures that strategies can implement
        sophisticated risk management and execution logic.

        **Advanced Risk Management:**

        The parameter structure supports advanced risk management techniques
        including dynamic slippage adjustment, partial position closure, selective
        collateral removal, and complex asset swap strategies. This enables
        implementation of professional-grade risk management systems.

        Example:

        .. code-block:: python

            # Algorithmic position management with dynamic parameters
            market_volatility = calculate_current_volatility("SOL")
            optimal_slippage = 0.002 + (market_volatility * 0.01)

            risk_parameters = {
                "chain": "arbitrum",
                "index_token_symbol": "SOL",
                "collateral_token_symbol": "SOL",
                "start_token_symbol": "SOL",
                "is_long": True,
                "size_delta_usd": calculate_optimal_size_reduction(),
                "initial_collateral_delta": calculate_collateral_removal(),
                "slippage_percent": optimal_slippage,
            }

            order = order_manager.close_position(
                parameters=risk_parameters,
            )

        :param parameters:
            Comprehensive dictionary containing all order execution parameters.
            Required keys include market identification, position direction,
            size adjustments, and risk controls. See example for complete structure
        :type parameters: dict[str, any]
        :return:
            Configured decrease order object ready for execution with all
            specified parameters and risk controls applied
        :rtype: DecreaseOrder
        :raises ValueError:
            When required parameters are missing, invalid, or inconsistent
            with current position state and market conditions
        """
        # Get configuration
        config = self.config.get_config()

        # Validate required parameters
        required_params = [
            "index_token_symbol",
            "collateral_token_symbol",
            "is_long",
            "size_delta_usd",
        ]
        for param in required_params:
            if param not in parameters:
                raise ValueError(f"Missing required parameter: {param}")

        # Set chain parameter if not provided
        if "chain" not in parameters:
            parameters["chain"] = self.config.get_chain()

        # Process parameters through the OrderArgumentParser
        order_parameters = OrderArgumentParser(
            config,
            is_decrease=True,
        ).process_parameters_dictionary(parameters)

        # Create order instance with position identification
        order = DecreaseOrder(
            config=config,
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
        )

    def close_position_by_key(
        self,
        position_key: str,
        out_token_symbol: str,
        amount_of_position_to_close: float = 1.0,
        amount_of_collateral_to_remove: float = 1.0,
        slippage_percent: float = 0.003,
        address: str | Address | None = None,
    ) -> OrderResult:
        """
        Execute strategic position closure using simplified position identification.

        This method provides an intuitive, high-level interface for position
        management by using human-readable position keys instead of complex
        parameter dictionaries. It's designed for manual trading workflows and
        simplified algorithmic strategies where ease of use is prioritized over
        granular parameter control.

        **Strategic Position Management:**

        The key-based approach excels in scenarios where traders think in terms
        of "close my ETH long position" rather than specific parameter combinations.
        It automatically handles the complex parameter translation while providing
        strategic controls over closure amount, collateral management, and output
        asset selection.

        **Partial Position Strategies:**

        The ability to close partial positions and remove partial collateral
        enables sophisticated position management strategies including profit
        taking, risk reduction, and portfolio rebalancing. These capabilities
        are essential for maintaining optimal risk exposure as market conditions
        evolve.

        **Asset Selection Strategy:**

        The choice of output token affects both immediate liquidity and ongoing
        risk exposure. Selecting stable tokens (USDC) locks in current values,
        while selecting volatile tokens (ETH) maintains price exposure. This
        strategic flexibility supports diverse trading and investment approaches.

        Example:

        .. code-block:: python

            # Strategic profit-taking on successful position
            profit_taking_order = order_manager.close_position_by_key(
                position_key="ETH_long",
                out_token_symbol="USDC",  # Lock in USD value
                amount_of_position_to_close=0.25,  # Take 25% profits
                amount_of_collateral_to_remove=0.1,  # Remove minimal collateral
                slippage_percent=0.005,  # Tight slippage for profits
            )

            # Risk management: Full position closure during market stress
            risk_management_order = order_manager.close_position_by_key(
                position_key="SOL_short",
                out_token_symbol="SOL",  # Maintain asset exposure
                amount_of_position_to_close=1.0,  # Close entire position
                amount_of_collateral_to_remove=1.0,  # Withdraw all collateral
                slippage_percent=0.02,  # Higher slippage for speed
            )

        :param position_key:
            Human-readable identifier for the position to close, formatted as
            "SYMBOL_direction" (e.g., "ETH_long", "BTC_short"). Must match
            an existing position in the wallet's current position portfolio
        :type position_key: str
        :param out_token_symbol:
            Symbol of the token to receive upon position closure (e.g., "USDC",
            "ETH"). Determines final asset exposure and liquidity characteristics
            of the closure proceeds
        :type out_token_symbol: str
        :param amount_of_position_to_close:
            Fraction of the total position size to close, expressed as decimal
            (0.25 = 25%, 1.0 = 100%). Enables precise partial position management
            for strategic position sizing
        :type amount_of_position_to_close: float
        :param amount_of_collateral_to_remove:
            Fraction of position collateral to withdraw, expressed as decimal.
            Independent of position closure amount, allowing flexible collateral
            management strategies
        :type amount_of_collateral_to_remove: float
        :param slippage_percent:
            Maximum acceptable slippage as decimal (0.003 = 0.3%). Higher values
            enable faster execution in volatile markets, lower values provide
            better price protection in stable conditions
        :type slippage_percent: float
        :param address:
            Specific wallet address containing the position to close. If not
            provided, uses the address from GMX configuration
        :type address: str | Address | None
        :return:
            Configured decrease order object ready for execution with all
            strategic parameters applied and risk controls validated
        :rtype: DecreaseOrder
        :raises ValueError:
            When position key is not found, invalid format, or position
            parameters are inconsistent with current market conditions
        """
        # Get configuration
        config = self.config.get_config()

        # Get positions
        if address:
            positions = self.get_open_positions(address)
        else:
            # if address is not passed get the address of the user from config
            positions = self.get_open_positions()

        if position_key not in positions.keys():
            raise ValueError(f"Position with key {position_key} not found")

        # key will be like this `ETH_short`
        # Split the key to get market and direction
        parts = position_key.split("_")
        if len(parts) != 2:
            raise ValueError(f"Invalid position key format: {position_key}")

        market_symbol = parts[0]
        direction = parts[1]
        is_long = direction.lower() == "long"

        # Transform position to order parameters
        order_parameters = transform_open_position_to_order_parameters(
            config=config,
            positions=positions,
            market_symbol=market_symbol,
            is_long=is_long,
            slippage_percent=slippage_percent,
            out_token=out_token_symbol,
            amount_of_position_to_close=amount_of_position_to_close,
            amount_of_collateral_to_remove=amount_of_collateral_to_remove,
        )

        # Create order instance with position identification
        order = DecreaseOrder(
            config=config,
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
        )
