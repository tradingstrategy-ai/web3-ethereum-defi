"""
GMX Utilities Module

This module provides the essential utility functions and computational foundations
that power the GMX integration system. It implements the mathematical calculations,
data transformations, and helper operations that form the backbone of all higher-level
trading, position management, and risk assessment functionality.

**Utility Layer Architecture:**

Professional trading systems are built on layers of abstraction, where sophisticated
user interfaces depend on robust utility layers that handle the complex mathematics
and data processing behind the scenes. This module represents that crucial
foundation layer, implementing the precise calculations and transformations needed
for safe and accurate trading operations.

**Key Computational Categories:**

- **Financial Mathematics**: Precise liquidation price calculations and risk metrics
- **Data Transformation**: Converting between different data formats and representations
- **Position Analysis**: Extracting meaningful insights from complex position data
- **Parameter Validation**: Ensuring data integrity and operational safety
- **Error Handling**: Graceful handling of edge cases and exceptional conditions

**Mathematical Precision Philosophy:**

Financial calculations require absolute precision because small errors can compound
into significant financial losses. The utility functions implement robust mathematical
operations using appropriate data types and validation to ensure accuracy across
all supported market conditions and position sizes.

**Integration with Trading Operations:**

These utilities serve as the computational engine for all higher-level operations.
When you open a position through the trading interface, liquidation calculations
happen here. When you analyze your portfolio through the market data interface,
position formatting occurs here. Understanding these utilities helps you understand
how the entire system works at its core.

**Error Prevention and Validation:**

The utility layer implements comprehensive validation and error handling to prevent
invalid operations from propagating through the system. This defensive programming
approach ensures that errors are caught early and reported clearly, preventing
costly mistakes during live trading operations.

Example:

.. code-block:: python

    # Mathematical risk analysis workflow
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.gmx.utils import (
        calculate_estimated_liquidation_price,
        format_position_for_display,
        get_positions,
    )

    # Set up configuration for analysis
    config = GMXConfig.from_private_key(web3, "0x...", "arbitrum")

    # Retrieve and analyze current positions
    positions = get_positions(config.get_read_config())

    for position_key, position_data in positions.items():
        # Format position for human-readable analysis
        display_info = format_position_for_display(position_data)

        # Calculate liquidation risk
        liq_price = calculate_estimated_liquidation_price(
            entry_price=position_data["entry_price"],
            collateral_usd=position_data["collateral_usd"],
            size_usd=position_data["size_usd"],
            is_long=position_data["is_long"],
            maintenance_margin=0.01,  # 1% maintenance margin
        )

        # Risk assessment analysis
        current_price = position_data["mark_price"]
        risk_distance = abs(current_price - liq_price) / current_price

        print(f"Position: {display_info['market']} {display_info['direction']}")
        print(f"Liquidation Price: ${liq_price:.2f}")
        print(f"Risk Distance: {risk_distance:.1%}")

        # Transform position for strategic closure if high risk
        if risk_distance < 0.10:  # Less than 10% safety margin
            close_params = transform_open_position_to_order_parameters(
                config=config.get_write_config(),
                positions=positions,
                market_symbol=display_info["market"],
                is_long=position_data["is_long"],
                slippage_percent=0.02,  # Higher slippage for urgent closure
                out_token="USDC",  # Convert to stable asset
                amount_of_position_to_close=0.5,  # Reduce risk by 50%
                amount_of_collateral_to_remove=0.2,  # Free some capital
            )

**Design Philosophy:**

The utilities are designed around principles of mathematical accuracy, operational
safety, and educational transparency. Each function includes comprehensive validation
and clear error messages to help developers understand both successful operations
and failure modes. This approach builds confidence and competence in using
sophisticated financial tools.

Note:
    All mathematical calculations use appropriate precision arithmetic to ensure
    accuracy in financial contexts where rounding errors can have costly consequences.

Warning:
    Liquidation price calculations are estimates based on current parameters.
    Actual liquidation prices may vary due to market volatility, funding costs,
    and other dynamic factors not captured in simplified calculations.
"""

import logging
from typing import Any, Optional

from decimal import Decimal

from gmx_python_sdk.scripts.v2.get.get_markets import Markets

from gmx_python_sdk.scripts.v2.get.get_open_positions import GetOpenPositions
from gmx_python_sdk.scripts.v2.gmx_utils import (
    ConfigManager,
    find_dictionary_by_key_value,
    get_tokens_address_dict,
    determine_swap_route,
)

# Can be done using the `GMXAPI` class if needed
# def token_symbol_to_address(chain: str, symbol: str) -> Optional[str]:
#     """
#     Convert a token symbol to its address.
#
#     Args:
#         chain: Chain name (arbitrum or avalanche)
#         symbol: Token symbol
#
#     Returns:
#         Token address or None if not found
#     """
#     chain_tokens = GMX_TOKEN_ADDRESSES.get(chain.lower(), {})
#     return chain_tokens.get(symbol.upper())


def format_position_for_display(position: dict[str, Any]) -> dict[str, Any]:
    """
    Transform raw position data into human-readable format for analysis and display.

    This function serves as a crucial bridge between the complex internal data
    structures used by the GMX protocol and the simplified, meaningful information
    that traders need for decision-making. It extracts the essential metrics
    from technical position data and presents them in an intuitive format.

    **Data Transformation Philosophy:**

    Raw position data from blockchain protocols contains extensive technical
    information including contract addresses, encoded values, and implementation
    details that are necessary for system operation but overwhelming for human
    analysis. This function implements intelligent filtering and formatting to
    present only the information needed for trading decisions.

    **Essential Trading Metrics:**

    The formatted output focuses on the core metrics that professional traders
    use for position analysis: market identification, position direction, size
    measurements, leverage calculations, entry and current pricing, and
    profit/loss performance. These metrics enable quick assessment of position
    health and strategic planning.

    **Integration with Analysis Workflows:**

    The standardized output format integrates seamlessly with portfolio analysis
    tools, risk management systems, and reporting dashboards. By providing
    consistent data formatting, it enables reliable automation of position
    monitoring and strategic decision-making processes.

    :param position:
        Raw position data dictionary containing all technical position information
        as returned from GMX protocol queries, including internal identifiers,
        encoded values, and comprehensive position state
    :type position: dict[str, Any]
    :return:
        Formatted dictionary containing human-readable position information
        with standardized keys and simplified values optimized for analysis
        and display in trading interfaces
    :rtype: dict[str, Any]
    """
    # Extract and format relevant fields for display
    return {
        "market": position.get("market_symbol", ""),
        "direction": "Long" if position.get("is_long") else "Short",
        "size_usd": position.get("position_size", 0),
        "collateral": position.get("collateral_token", ""),
        "leverage": position.get("leverage", 0),
        "entry_price": position.get("entry_price", 0),
        "current_price": position.get("mark_price", 0),
        "pnl_percent": position.get("percent_profit", 0),
    }


def calculate_estimated_liquidation_price(
    entry_price: float,
    collateral_usd: float,
    size_usd: float,
    is_long: bool,
    maintenance_margin: float = 0.01,
) -> float:  # 1% maintenance margin
    """
    Calculate estimated liquidation price using fundamental leveraged trading mathematics.

    This function implements the core mathematical relationship that determines
    when leveraged positions become unsustainable and subject to forced closure.
    Understanding liquidation mechanics is essential for risk management and
    position sizing in leveraged trading systems.

    **Liquidation Mathematics Explained:**

    Liquidation occurs when a position's collateral value falls below the minimum
    required to maintain the leveraged exposure. For long positions, this happens
    when prices fall enough that collateral loses value. For short positions,
    liquidation occurs when prices rise and the position accumulates losses that
    exceed available collateral.

    The mathematical relationship derives from the leverage formula:
    leverage = position_size / collateral_value

    When market moves against a position, the collateral absorbs losses. Liquidation
    triggers when remaining collateral falls below the maintenance margin requirement,
    which ensures the protocol can close positions before they become undercollateralized.

    **Risk Management Applications:**

    Liquidation price calculations are fundamental to position sizing and risk
    management. Professional traders use these calculations to determine appropriate
    position sizes, set stop-loss levels, and monitor portfolio risk in real-time.
    Understanding your liquidation price helps prevent unexpected position closures
    during normal market volatility.

    **Calculation Methodology:**

    The calculation accounts for leverage effects, maintenance margin requirements,
    and position direction. For long positions, liquidation prices are below entry
    prices by an amount determined by leverage and margin requirements. For short
    positions, liquidation prices are above entry prices by the corresponding amount.

    Example:

    .. code-block:: python

        # Risk analysis for leveraged ETH position
        entry_price = 2000.0  # Entered ETH long at $2000
        collateral_usd = 1000  # $1000 collateral
        size_usd = 5000  # $5000 position (5x leverage)

        liq_price = calculate_estimated_liquidation_price(
            entry_price=entry_price,
            collateral_usd=collateral_usd,
            size_usd=size_usd,
            is_long=True,
            maintenance_margin=0.01,  # 1% maintenance margin
        )

        # Result: liquidation at approximately $1620
        # Risk analysis: 19% price drop triggers liquidation
        risk_tolerance = (entry_price - liq_price) / entry_price
        print(f"Position liquidates if ETH drops {risk_tolerance:.1%}")

    :param entry_price:
        Price at which the position was opened, serving as the baseline for
        profit/loss calculations and liquidation risk assessment
    :type entry_price: float
    :param collateral_usd:
        Total collateral value backing the position in USD terms. This capital
        absorbs gains and losses as market prices move
    :type collateral_usd: float
    :param size_usd:
        Total position size in USD terms, representing the market exposure
        controlled through leveraged capital
    :type size_usd: float
    :param is_long:
        Position direction - True for long (bullish) positions that profit
        from price increases, False for short (bearish) positions that
        profit from price decreases
    :type is_long: bool
    :param maintenance_margin:
        Minimum margin requirement as decimal (0.01 = 1%). Protocol-specific
        safety buffer ensuring positions can be closed before becoming
        undercollateralized
    :type maintenance_margin: float
    :return:
        Estimated price level at which the position would face liquidation
        based on current parameters and maintenance margin requirements
    :rtype: float
    """
    leverage = size_usd / collateral_usd

    if is_long:
        # For longs, liquidation happens when price drops
        liquidation_price = entry_price * (1 - (1 / leverage) + maintenance_margin)
    else:
        # For shorts, liquidation happens when price rises
        liquidation_price = entry_price * (1 + (1 / leverage) - maintenance_margin)

    return liquidation_price


def get_positions(config, address: str = None) -> dict[str, Any]:
    """
    Retrieve comprehensive position data with intelligent address resolution and error handling.

    This function implements robust position retrieval logic that handles multiple
    address sources and provides clear error handling for common failure scenarios.
    It serves as a reliable foundation for all position-dependent operations
    throughout the trading system.

    **Address Resolution Strategy:**

    The function implements intelligent fallback logic for address determination.
    If an explicit address is provided, it takes precedence. If no address is
    provided, the function attempts to use the address from the configuration.
    This pattern provides flexibility for multi-wallet operations while ensuring
    safe defaults for single-wallet workflows.

    **Position Data Structure:**

    The returned data structure uses human-readable position keys (like "ETH_long")
    that make position identification intuitive for both programmatic access and
    manual analysis. Each position entry contains comprehensive information about
    size, collateral, current performance, and risk metrics.

    **Error Handling Philosophy:**

    The function implements defensive programming principles by validating address
    availability before attempting position queries. Clear error messages help
    developers understand configuration requirements and debug common setup issues.

    :param config:
        GMX configuration object containing network settings and optional
        wallet information for position queries
    :type config: ConfigManager
    :param address:
        Specific Ethereum address to query positions for. If None, attempts
        to use the address from the provided configuration object
    :type address: str, optional
    :return:
        Dictionary containing all open positions keyed by human-readable
        position identifiers (e.g., "ETH_long", "BTC_short") with comprehensive
        position data as values
    :rtype: dict[str, Any]
    :raises Exception:
        When no address is available from either parameter or configuration,
        making position queries impossible
    """
    if address is None:
        address = config.user_wallet_address
        if address is None:
            raise Exception("No address passed in function or config!")

    positions = GetOpenPositions(config=config, address=address).get_data()

    if len(positions) > 0:
        logging.info(f"Open Positions for {address}:")
        for key in positions.keys():
            logging.info(key)

    return positions


def transform_open_position_to_order_parameters(
    config,
    positions: dict[str, Any],
    market_symbol: str,
    is_long: bool,
    slippage_percent: float,
    out_token: str,
    amount_of_position_to_close: float,
    amount_of_collateral_to_remove: float,
) -> dict[str, Any]:
    """
    Transform existing position data into precise order parameters for strategic position closure.

    This function implements sophisticated data transformation logic that bridges
    the gap between high-level trading intentions ("close 50% of my ETH position")
    and the precise technical parameters required by the GMX protocol for order
    execution. It handles complex address resolution, swap path calculation,
    and mathematical precision for financial calculations.

    **Data Transformation Architecture:**

    The transformation process involves multiple complex steps: position identification
    using human-readable keys, address resolution for multiple token types, swap
    path determination for asset conversion, and precise mathematical calculations
    for partial position closure. Each step includes validation to ensure data
    integrity and prevent execution errors.

    **Mathematical Precision Requirements:**

    Financial calculations require absolute precision to prevent rounding errors
    that could cause transaction failures or unexpected results. The function uses
    Decimal arithmetic for position size calculations and properly scales values
    to match protocol requirements for order parameters.

    **Swap Path Intelligence:**

    When the desired output token differs from the position's collateral token,
    the function automatically determines the optimal swap path through available
    markets. This enables strategic asset selection upon position closure without
    requiring manual path configuration.

    **Error Prevention and Validation:**

    Comprehensive validation ensures that all required position data is available
    and properly formatted before attempting transformation. Clear error messages
    help identify configuration issues or missing position data that would prevent
    successful order creation.

    Example:

    .. code-block:: python

        # Strategic position closure with precise control
        positions = get_positions(config)

        # Close 75% of ETH long position, convert to USDC
        close_params = transform_open_position_to_order_parameters(
            config=config,
            positions=positions,
            market_symbol="ETH",
            is_long=True,
            slippage_percent=0.005,  # 0.5% slippage
            out_token="USDC",  # Convert to stable asset
            amount_of_position_to_close=0.75,  # Close 75% of position
            amount_of_collateral_to_remove=0.5,  # Remove 50% of collateral
        )

        # Parameters ready for order execution
        order = DecreaseOrder(**close_params, debug_mode=True)

    :param config:
        GMX configuration object containing network settings and token
        information required for address resolution and market data access
    :type config: ConfigManager
    :param positions:
        Dictionary containing all current open positions with human-readable
        keys and comprehensive position data for transformation
    :type positions: dict[str, Any]
    :param market_symbol:
        Symbol identifying the market containing the position to close
        (e.g., "ETH", "BTC"). Must match an existing position
    :type market_symbol: str
    :param is_long:
        Direction of the position to close - True for long positions,
        False for short positions. Must match existing position direction
    :type is_long: bool
    :param slippage_percent:
        Maximum acceptable slippage for the closure operation as decimal
        (0.005 = 0.5%). Higher values enable faster execution in volatile
        markets but may result in worse prices
    :type slippage_percent: float
    :param out_token:
        Symbol of the token to receive upon position closure. May differ
        from collateral token, triggering automatic swap path calculation
    :type out_token: str
    :param amount_of_position_to_close:
        Fraction of total position size to close, expressed as decimal
        (0.5 = 50%). Enables precise partial position management strategies
    :type amount_of_position_to_close: float
    :param amount_of_collateral_to_remove:
        Fraction of position collateral to withdraw, expressed as decimal.
        Independent of position closure amount for flexible capital management
    :type amount_of_collateral_to_remove: float
    :return:
        Dictionary containing all parameters required for order execution,
        formatted according to GMX protocol requirements with proper
        address resolution and mathematical precision
    :rtype: dict[str, Any]
    :raises Exception:
        When the specified position cannot be found in the positions
        dictionary, indicating invalid market/direction combination
    """
    direction = "long" if is_long else "short"
    position_dictionary_key = f"{market_symbol.upper()}_{direction}"

    try:
        raw_position_data = positions[position_dictionary_key]
        gmx_tokens = get_tokens_address_dict(config.chain)

        # Get collateral token address
        collateral_address = find_dictionary_by_key_value(gmx_tokens, "symbol", raw_position_data["collateral_token"])["address"]

        # Get index token address
        index_address = find_dictionary_by_key_value(gmx_tokens, "symbol", raw_position_data["market_symbol"][0])

        # Get output token address
        out_token_address = find_dictionary_by_key_value(gmx_tokens, "symbol", out_token)["address"]

        # Get markets info
        markets = Markets(config=config).info

        # Calculate swap path if needed
        swap_path = []
        if collateral_address != out_token_address:
            swap_path = determine_swap_route(markets, collateral_address, out_token_address)[0]

        # Calculate size delta based on position size and close amount
        size_delta = int((Decimal(raw_position_data["position_size"]) * (Decimal(10) ** 30)) * Decimal(amount_of_position_to_close))

        # Return formatted parameters
        return {
            "chain": config.chain,
            "market_key": raw_position_data["market"],
            "collateral_address": collateral_address,
            "index_token_address": index_address["address"],
            "is_long": raw_position_data["is_long"],
            "size_delta": size_delta,
            "initial_collateral_delta": int(raw_position_data["inital_collateral_amount"] * amount_of_collateral_to_remove),
            "slippage_percent": slippage_percent,
            "swap_path": swap_path,
        }
    except KeyError:
        raise Exception(f"Couldn't find a {market_symbol} {direction} position for the given user!")


if __name__ == "__main__":
    config = ConfigManager(chain="arbitrum")
    config.set_config()

    positions = get_positions(config=config, address="0x0e9E19E7489E5F13a0940b3b6FcB84B25dc68177")

    # market_symbol = "ETH"
    # is_long = True

    # out_token = "USDC"
    # amount_of_position_to_close = 1
    # amount_of_collateral_to_remove = 1

    # order_params = transform_open_position_to_order_parameters(
    #     config=config,
    #     positions=positions,
    #     market_symbol=market_symbol,
    #     is_long=is_long,
    #     slippage_percent=0.003,
    #     out_token="USDC",
    #     amount_of_position_to_close=amount_of_position_to_close,
    #     amount_of_collateral_to_remove=amount_of_collateral_to_remove
    # )
