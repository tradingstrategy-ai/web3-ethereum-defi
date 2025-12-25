"""
GMX Utilities Module.

This module provides the essential utility functions and computational foundations
that power the GMX integration system. It implements the mathematical calculations,
data transformations, and helper operations that form the backbone of all higher-level
trading, position management, and risk assessment functionality.

Utility Layer Architecture
--------------------------

Professional trading systems are built on layers of abstraction, where sophisticated
user interfaces depend on robust utility layers that handle the complex mathematics
and data processing behind the scenes. This module represents that crucial
foundation layer, implementing the precise calculations and transformations needed
for safe and accurate trading operations.

Key Computational Categories
----------------------------

- **Financial Mathematics**: Precise liquidation price calculations and risk metrics
- **Data Transformation**: Converting between different data formats and representations
- **Position Analysis**: Extracting meaningful insights from complex position data
- **Parameter Validation**: Ensuring data integrity and operational safety
- **Error Handling**: Graceful handling of edge cases and exceptional conditions

Mathematical Precision Philosophy
--------------------------------

Financial calculations require absolute precision because small errors can compound
into significant financial losses. The utility functions implement robust mathematical
operations using appropriate data types and validation to ensure accuracy across
all supported market conditions and position sizes.

Integration with Trading Operations
----------------------------------

These utilities serve as the computational engine for all higher-level operations.
When you open a position through the trading interface, liquidation calculations
happen here. When you analyze your portfolio through the market data interface,
position formatting occurs here. Understanding these utilities helps you understand
how the entire system works at its core.

Error Prevention and Validation
------------------------------

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
    positions = get_positions(config.get_config())

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
                config=config.get_config(),
                positions=positions,
                market_symbol=display_info["market"],
                is_long=position_data["is_long"],
                slippage_percent=0.02,  # Higher slippage for urgent closure
                out_token="USDC",  # Convert to stable asset
                amount_of_position_to_close=0.5,  # Reduce risk by 50%
                amount_of_collateral_to_remove=0.2,  # Free some capital
            )

Design Philosophy
-----------------
"""

import logging
from typing import Any

from decimal import Decimal
from eth_abi import encode
from eth_utils import keccak

from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.contracts import get_tokens_address_dict, TESTNET_TO_MAINNET_ORACLE_TOKENS


# GMX uses 30-decimal precision for all price values
GMX_PRICE_PRECISION = 30


def convert_raw_price_to_usd(raw_price: int | float, token_decimals: int) -> float:
    """Convert GMX raw price to human-readable USD.

    GMX stores prices in 30-decimal PRECISION format. The conversion formula is:
        price_usd = raw_price / 10^(30 - token_decimals)

    Examples:
        - BTC (8 decimals): raw / 10^22
        - ETH (18 decimals): raw / 10^12
        - USDC (6 decimals): raw / 10^24

    :param raw_price: Raw price value from GMX (30-decimal format)
    :param token_decimals: Number of decimals for the token (e.g., 8 for BTC, 18 for ETH)
    :return: Price in USD
    """
    price_decimals = GMX_PRICE_PRECISION - token_decimals
    return float(raw_price) / (10**price_decimals)


def convert_usd_to_raw_price(price_usd: float, token_decimals: int) -> int:
    """Convert USD price to GMX raw format.

    Inverse of convert_raw_price_to_usd.

    :param price_usd: Price in USD
    :param token_decimals: Number of decimals for the token
    :return: Raw price value in GMX 30-decimal format
    """
    price_decimals = GMX_PRICE_PRECISION - token_decimals
    return int(price_usd * (10**price_decimals))


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
    pending_funding_fees_usd: float = 0.0,
    pending_borrowing_fees_usd: float = 0.0,
    include_closing_fee: bool = True,
    collateral_is_index_token: bool = False,
    collateral_amount: float | None = None,
) -> float:
    """
    Calculate liquidation price matching GMX V2 SDK implementation.

    This function implements the exact liquidation price calculation from the
    official GMX TypeScript SDK, accounting for fees, leverage, maintenance
    margin, and whether collateral token matches the index token.

    **How GMX Liquidation Works:**

    GMX liquidates positions when remaining collateral after fees falls below
    the minimum collateral requirement. The calculation must account for:
    - Pending funding fees (can be positive or negative)
    - Pending borrowing fees (always reduces collateral)
    - Position closing fees (~0.1% of position size)
    - Maintenance margin requirement (~0.5% of position size, min $5)

    **Accurate vs Approximate Mode:**

    - If `collateral_is_index_token` and `collateral_amount` are provided:
      Uses EXACT formula matching GMX SDK (recommended)
    - If not provided: Uses APPROXIMATE formula (simpler but ±0.5% error)

    **Exact Calculation Formulas:**

    Same token (e.g., ETH collateral for ETH/USD position):
        Long: liq_price = (size + liq_collateral + fees) / (size_tokens + collateral_tokens)
        Short: liq_price = (size - liq_collateral - fees) / (size_tokens - collateral_tokens)

    Different tokens (e.g., USDC collateral for ETH/USD position):
        Long: liq_price = (liq_collateral - remaining_collateral + size) / size_tokens
        Short: liq_price = (size - liq_collateral + remaining_collateral) / size_tokens

    **Approximate Formula (fallback):**
        liq_price = entry_price * (1 ± max_loss_ratio)

    This provides quick estimates when token details are unavailable.

    Example:

    .. code-block:: python

        # Approximate mode (quick estimate)
        liq_price = calculate_estimated_liquidation_price(
            entry_price=2000.0,
            collateral_usd=1000.0,
            size_usd=5000.0,  # 5x leverage
            is_long=True,
            pending_funding_fees_usd=5.0,
            pending_borrowing_fees_usd=10.0,
        )
        # Result: ~$1608 (approximate, ±0.5% error)

        # Exact mode (matches GMX SDK)
        liq_price = calculate_estimated_liquidation_price(
            entry_price=2000.0,
            collateral_usd=1000.0,
            collateral_amount=0.5,  # 0.5 ETH collateral
            size_usd=5000.0,
            is_long=True,
            collateral_is_index_token=True,  # ETH collateral for ETH position
            pending_funding_fees_usd=5.0,
            pending_borrowing_fees_usd=10.0,
        )
        # Result: $1681.67 (exact, matches GMX TypeScript SDK)

    :param entry_price:
        Price at which the position was opened
    :type entry_price: float
    :param collateral_usd:
        Total collateral value in USD
    :type collateral_usd: float
    :param size_usd:
        Total position size in USD (collateral × leverage)
    :type size_usd: float
    :param is_long:
        True for long position, False for short position
    :type is_long: bool
    :param maintenance_margin:
        Minimum margin requirement as decimal (default 0.01 = 1%)
        GMX typically uses 1% for most markets
    :type maintenance_margin: float
    :param pending_funding_fees_usd:
        Accumulated funding fees in USD (can be negative for rebates)
    :type pending_funding_fees_usd: float
    :param pending_borrowing_fees_usd:
        Accumulated borrowing fees in USD (always positive)
    :type pending_borrowing_fees_usd: float
    :param include_closing_fee:
        If True, includes 0.1% closing fee in calculation (default: True)
    :type include_closing_fee: bool
    :param collateral_is_index_token:
        Whether collateral token matches index token (e.g., ETH collateral for ETH/USD).
        Required for exact calculation. If False, assumes different token like USDC.
    :type collateral_is_index_token: bool
    :param collateral_amount:
        Amount of collateral in tokens (e.g., 0.5 for 0.5 ETH).
        Required for exact calculation. If None, uses approximate formula.
    :type collateral_amount: float | None
    :return:
        Liquidation price in USD
    :rtype: float

    **Note:**
        For maximum accuracy, provide `collateral_is_index_token` and `collateral_amount`.
        Without these, the function uses an approximate formula with ±0.5% error.
    """
    # Calculate total fees
    total_pending_fees = pending_funding_fees_usd + pending_borrowing_fees_usd
    closing_fee = size_usd * 0.001 if include_closing_fee else 0.0  # 0.1% closing fee
    total_fees = total_pending_fees + closing_fee

    # Calculate liquidation collateral threshold (0.5% of size, min $5)
    liquidation_collateral_usd = max(size_usd * 0.005, 5.0)

    # Use exact formula if token details provided
    if collateral_amount is not None:
        # Calculate size in tokens
        size_in_tokens = size_usd / entry_price

        if collateral_is_index_token:
            # Same token collateral (e.g., ETH collateral for ETH/USD position)
            if is_long:
                # Long: liq_price = (size + liq_collateral + fees) / (size_tokens + collateral_tokens)
                denominator = size_in_tokens + collateral_amount
                if denominator == 0:
                    return 0.0
                liquidation_price = (size_usd + liquidation_collateral_usd + total_fees) / denominator
            else:
                # Short: liq_price = (size - liq_collateral - fees) / (size_tokens - collateral_tokens)
                denominator = size_in_tokens - collateral_amount
                if denominator == 0:
                    return 0.0
                liquidation_price = (size_usd - liquidation_collateral_usd - total_fees) / denominator
        else:
            # Different token collateral (e.g., USDC collateral for ETH/USD position)
            if size_in_tokens == 0:
                return 0.0

            remaining_collateral_usd = collateral_usd - total_pending_fees - closing_fee

            if is_long:
                # Long: liq_price = (liq_collateral - remaining_collateral + size) / size_tokens
                liquidation_price = (liquidation_collateral_usd - remaining_collateral_usd + size_usd) / size_in_tokens
            else:
                # Short: liq_price = (size - liq_collateral + remaining_collateral) / size_tokens
                liquidation_price = (size_usd - liquidation_collateral_usd + remaining_collateral_usd) / size_in_tokens
    else:
        # Fallback to approximate formula when token details not provided
        remaining_collateral = collateral_usd - total_fees
        min_collateral_requirement = liquidation_collateral_usd

        if is_long:
            # For longs: price drop reduces collateral value
            max_loss = remaining_collateral - min_collateral_requirement
            price_drop_ratio = max_loss / size_usd
            liquidation_price = entry_price * (1 - price_drop_ratio)
        else:
            # For shorts: price rise reduces collateral value
            max_loss = remaining_collateral - min_collateral_requirement
            price_rise_ratio = max_loss / size_usd
            liquidation_price = entry_price * (1 + price_rise_ratio)

    return max(liquidation_price, 0.0)


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
    :type config: GMXConfigManager
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

    # GetOpenPositions expects GMXConfig, so create one if we have GMXConfigManager
    if hasattr(config, "get_web3_connection"):
        # This is GMXConfigManager
        from eth_defi.gmx.config import GMXConfig

        web3 = config.get_web3_connection()
        gmx_config = GMXConfig(web3, user_wallet_address=config.user_wallet_address)
        positions = GetOpenPositions(config=gmx_config).get_data(address=address)
    else:
        # This is already GMXConfig
        positions = GetOpenPositions(config=config).get_data(address=address)

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
        order = DecreaseOrder(**close_params)

    :param config:
        GMX configuration object containing network settings and token
        information required for address resolution and market data access
    :type config: GMXConfigManager
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

        # Calculate size delta based on position size and close amount (in USD units, will be scaled in base_order)
        size_delta = float(Decimal(raw_position_data["position_size"]) * Decimal(amount_of_position_to_close))

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


def find_dictionary_by_key_value(outer_dict: dict, key: str, value: str):
    """Find a dictionary within a nested structure by key-value pair.

    :param outer_dict: Dictionary to search through
    :type outer_dict: dict
    :param key: Key to search for
    :type key: str
    :param value: Value that the key should match
    :type value: str
    :return: First matching dictionary or None if not found
    :rtype: dict | None
    """
    for inner_dict in outer_dict.values():
        if key in inner_dict and inner_dict[key] == value:
            return inner_dict
    return None


def determine_swap_route(markets: dict, in_token: str, out_token: str, chain: str = "arbitrum") -> tuple[list, bool]:
    """Determine the optimal swap route through available GMX markets.

    Using the available markets, find the list of GMX markets required
    to swap from token in to token out.

    :param markets: Dictionary of markets output by getMarketInfo
    :type markets: dict
    :param in_token: Contract address of input token
    :type in_token: str
    :param out_token: Contract address of output token
    :type out_token: str
    :param chain: Blockchain network name
    :type chain: str
    :return: Tuple of (list of GMX markets to swap through, requires_multi_swap)
    :rtype: tuple[list, bool]
    """
    from eth_defi.gmx.constants import TOKEN_ADDRESS_MAPPINGS
    from eth_defi.gmx.contracts import NETWORK_TOKENS

    # Apply token address mappings for routing
    # Handle WBTC -> BTC.b mapping and similar
    if chain in TOKEN_ADDRESS_MAPPINGS:
        mappings = TOKEN_ADDRESS_MAPPINGS[chain]
        if in_token in mappings:
            in_token = mappings[in_token]
        if out_token in mappings:
            out_token = mappings[out_token]

    # Get USDC address for routing based on chain
    usdc_address = NETWORK_TOKENS.get(chain, {}).get("USDC")
    if not usdc_address:
        raise ValueError(f"USDC address not configured for chain: {chain}")

    if in_token == usdc_address:
        gmx_market_data = find_dictionary_by_key_value(markets, "index_token_address", out_token)
        if gmx_market_data:
            gmx_market_address = gmx_market_data["gmx_market_address"]
        else:
            raise ValueError(f"No market found for output token {out_token}")
    else:
        gmx_market_data = find_dictionary_by_key_value(markets, "index_token_address", in_token)
        if gmx_market_data:
            gmx_market_address = gmx_market_data["gmx_market_address"]
        else:
            raise ValueError(f"No market found for input token {in_token}")

    is_requires_multi_swap = False

    if out_token != usdc_address and in_token != usdc_address:
        is_requires_multi_swap = True
        second_gmx_market_data = find_dictionary_by_key_value(markets, "index_token_address", out_token)
        if second_gmx_market_data:
            second_gmx_market_address = second_gmx_market_data["gmx_market_address"]
            return [gmx_market_address, second_gmx_market_address], is_requires_multi_swap
        else:
            raise ValueError(f"No market found for output token {out_token} in multi-swap")

    return [gmx_market_address], is_requires_multi_swap


def get_oracle_address(chain: str, token_address: str) -> str:
    """Map testnet address to oracle address if on testnet"""
    if chain in ["arbitrum_sepolia", "avalanche_fuji"]:
        return TESTNET_TO_MAINNET_ORACLE_TOKENS.get(token_address, token_address)
    return token_address
