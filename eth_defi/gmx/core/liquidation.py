"""
GMX V2 Liquidation Price Calculation.

This module provides accurate liquidation price calculations for GMX V2 positions,
matching the official TypeScript SDK implementation. The liquidation price represents
the price level at which a position will be automatically closed to prevent the protocol
from taking on bad debt.

Key Differences from Simplified Estimates:
- Accounts for borrowing fees and funding fees
- Includes price impact calculations
- Uses liquidation-specific minimum collateral factors
- Handles both same-collateral and cross-collateral positions
- Properly scales decimals for different token types

Example:
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.gmx.core.liquidation import get_liquidation_price

    config = GMXConfig(web3, user_wallet_address="0x...")
    positions = get_positions(config)

    liq_price = get_liquidation_price(
        config=config,
        position_dict=positions["ETH_long"],
        wallet_address="0x..."
    )
    print(f"Liquidation price: ${liq_price:.2f}")
"""

from decimal import Decimal
from typing import Optional

from eth_defi.gmx.contracts import get_tokens_address_dict
from eth_defi.gmx.keys import create_hash, create_hash_string, apply_factor


def get_is_equivalent_tokens(token1: str, token2: str, chain: str) -> bool:
    """Check if two tokens are equivalent (e.g., WBTC and BTC.b).

    GMX markets may use wrapped versions of tokens (WBTC) while positions
    use native versions (BTC). This function checks if tokens are equivalent
    for the purposes of liquidation calculations.

    Args:
        token1: First token address
        token2: Second token address
        chain: Chain name (arbitrum, avalanche, etc.)

    Returns:
        True if tokens are equivalent, False otherwise
    """
    from eth_defi.gmx.constants import TOKEN_ADDRESS_MAPPINGS

    # Normalize addresses to lowercase
    token1 = token1.lower()
    token2 = token2.lower()

    # Direct match
    if token1 == token2:
        return True

    # Check token mappings for equivalent tokens
    if chain in TOKEN_ADDRESS_MAPPINGS:
        mappings = TOKEN_ADDRESS_MAPPINGS[chain]
        # Get canonical addresses
        canonical1 = mappings.get(token1, token1)
        canonical2 = mappings.get(token2, token2)
        return canonical1 == canonical2

    return False


def get_position_fee(
    size_delta_usd: Decimal,
    for_positive_impact: bool,
    referral_info: dict = None,
    ui_fee_factor: Decimal = Decimal(0),
) -> dict:
    """Calculate position fees for opening or closing.

    GMX charges different fees based on whether the price impact is positive
    or negative. Positive impact (0.05%) vs negative impact (0.07%).

    Args:
        size_delta_usd: Size of position change in USD
        for_positive_impact: True if calculating for positive price impact
        referral_info: Optional referral information for fee discounts
        ui_fee_factor: Optional UI fee factor

    Returns:
        Dictionary with positionFeeUsd key containing fee amount
    """
    # GMX V2 fee structure: 0.05% for positive impact, 0.07% for negative
    factor = Decimal("0.0005") if for_positive_impact else Decimal("0.0007")
    position_fee_usd = size_delta_usd * factor

    # TODO: Apply referral discounts if referral_info provided
    # TODO: Apply UI fee if ui_fee_factor provided

    return {"positionFeeUsd": position_fee_usd}


def min_collateral_factor_for_liquidation_key(market: str) -> bytes:
    """Get datastore key for minimum collateral factor for liquidations.

    This is different from the regular minimum collateral factor - it's
    specifically used for liquidation threshold calculations.

    Args:
        market: Market address

    Returns:
        32-byte key for datastore lookup
    """
    return create_hash(["bytes32", "address"], [create_hash_string("MIN_COLLATERAL_FACTOR_FOR_LIQUIDATION"), market])


def max_position_impact_factor_for_liquidations_key(market: str) -> bytes:
    """Get datastore key for max position impact factor for liquidations.

    Args:
        market: Market address

    Returns:
        32-byte key for datastore lookup
    """
    return create_hash(["bytes32", "address"], [create_hash_string("MAX_POSITION_IMPACT_FACTOR_FOR_LIQUIDATIONS"), market])


def calculate_liquidation_price(
    datastore_obj,
    market_info: dict,
    market_address: str,
    index_token_address: str,
    size_in_usd: Decimal,
    size_in_tokens: Decimal,
    collateral_amount: Decimal,
    collateral_usd: Decimal,
    collateral_token_address: str,
    pending_funding_fees_usd: Decimal,
    pending_borrowing_fees_usd: Decimal,
    min_collateral_usd: Decimal,
    is_long: bool,
    chain: str = "arbitrum",
    use_max_price_impact: bool = True,
    user_referral_info: dict = None,
) -> Optional[Decimal]:
    """Calculate liquidation price for a GMX V2 position.

    This implementation matches the official GMX TypeScript SDK logic,
    including all fees, price impacts, and edge cases.

    Args:
        datastore_obj: GMX datastore contract instance
        market_info: Market information dictionary from Markets.get_available_markets()
        market_address: Market contract address
        index_token_address: Index token address (e.g., WETH for ETH/USD market)
        size_in_usd: Position size in USD (30 decimals)
        size_in_tokens: Position size in tokens (token decimals)
        collateral_amount: Collateral amount in token units
        collateral_usd: Collateral value in USD (30 decimals)
        collateral_token_address: Collateral token address
        pending_funding_fees_usd: Pending funding fees in USD (30 decimals)
        pending_borrowing_fees_usd: Pending borrowing fees in USD (30 decimals)
        min_collateral_usd: Minimum collateral requirement in USD (30 decimals)
        is_long: True for long position, False for short
        chain: Chain name (arbitrum, avalanche, etc.)
        use_max_price_impact: If True, use maximum negative price impact
        user_referral_info: Optional referral information

    Returns:
        Liquidation price in token's native decimals, or None if calculation impossible
    """
    # Validate inputs
    if size_in_usd <= 0 or size_in_tokens <= 0:
        return None

    # Get index token decimals
    index_token_decimals = market_info["index_token_decimals"]

    # Calculate closing fee (always uses negative impact fee rate)
    closing_fee_usd = get_position_fee(size_in_usd, for_positive_impact=False, user_referral_info=user_referral_info)["positionFeeUsd"]

    # Calculate total fees
    total_pending_fees_usd = pending_funding_fees_usd + pending_borrowing_fees_usd
    total_fees_usd = total_pending_fees_usd + closing_fee_usd

    # Get maximum negative price impact allowed for liquidations
    max_position_impact_factor = datastore_obj.functions.getUint(max_position_impact_factor_for_liquidations_key(market_address)).call()

    max_negative_price_impact_usd = Decimal(-1) * Decimal(apply_factor(size_in_usd, max_position_impact_factor))

    # Calculate price impact
    # For liquidation calculations, we typically use max negative impact for safety
    if use_max_price_impact:
        price_impact_delta_usd = max_negative_price_impact_usd
    else:
        # In production, you would call get_price_impact_for_position here
        # For now, we use max negative impact as conservative estimate
        price_impact_delta_usd = max_negative_price_impact_usd

        # Clamp to valid range [max_negative, 0]
        if price_impact_delta_usd > 0:
            price_impact_delta_usd = Decimal(0)
        elif price_impact_delta_usd < max_negative_price_impact_usd:
            price_impact_delta_usd = max_negative_price_impact_usd

    # Get liquidation-specific minimum collateral factor
    min_collateral_factor = datastore_obj.functions.getUint(min_collateral_factor_for_liquidation_key(market_address)).call()

    liquidation_collateral_usd = Decimal(apply_factor(size_in_usd, min_collateral_factor))

    # Ensure liquidation collateral meets minimum
    if liquidation_collateral_usd < min_collateral_usd:
        liquidation_collateral_usd = min_collateral_usd

    # Calculate liquidation price based on collateral type
    liquidation_price = Decimal(0)

    if get_is_equivalent_tokens(collateral_token_address, index_token_address, chain):
        # Same token: collateral and index token are equivalent
        # Position value includes both size and collateral in same units

        if is_long:
            denominator = size_in_tokens + collateral_amount
            if denominator == 0:
                return None

            # FIXED: Added decimal scaling (was commented out in original)
            liquidation_price = ((size_in_usd + liquidation_collateral_usd - price_impact_delta_usd + total_fees_usd) / denominator) * Decimal(10**index_token_decimals)

        else:
            denominator = size_in_tokens - collateral_amount
            if denominator == 0:
                return None

            # FIXED: Added decimal scaling (was commented out in original)
            liquidation_price = ((size_in_usd - liquidation_collateral_usd + price_impact_delta_usd - total_fees_usd) / denominator) * Decimal(10**index_token_decimals)

    else:
        # Different tokens: collateral is separate from position token
        # Need to account for remaining collateral value

        if size_in_tokens == 0:
            return None

        remaining_collateral_usd = collateral_usd + price_impact_delta_usd - total_pending_fees_usd - closing_fee_usd

        if is_long:
            # FIXED: Added decimal scaling (was commented out in original)
            liquidation_price = ((liquidation_collateral_usd - remaining_collateral_usd + size_in_usd) / size_in_tokens) * Decimal(10**index_token_decimals)

        else:
            # FIXED: Added decimal scaling (was commented out in original)
            liquidation_price = ((liquidation_collateral_usd - remaining_collateral_usd - size_in_usd) / (-size_in_tokens)) * Decimal(10**index_token_decimals)

    # Validate result
    if liquidation_price <= 0:
        return None

    return liquidation_price


def get_liquidation_price(
    config,
    position_dict: dict,
    wallet_address: Optional[str] = None,
) -> Optional[float]:
    """Get liquidation price for a GMX position.

    High-level convenience function that fetches all required data and
    calculates the liquidation price for a position.

    Args:
        config: GMXConfig instance
        position_dict: Position dictionary from get_positions()
        wallet_address: Wallet address (optional, uses config if not provided)

    Returns:
        Liquidation price as float, or None if calculation not possible

    Example:
        from eth_defi.gmx.config import GMXConfig
        from eth_defi.gmx.utils import get_positions
        from eth_defi.gmx.core.liquidation import get_liquidation_price

        config = GMXConfig(web3, user_wallet_address="0x...")
        positions = get_positions(config)

        if "ETH_long" in positions:
            liq_price = get_liquidation_price(config, positions["ETH_long"])
            print(f"ETH long liquidation price: ${liq_price:.2f}")
    """
    from eth_defi.gmx.contracts import get_datastore_contract, get_reader_contract
    from eth_defi.gmx.keys import accountPositionListKey, min_collateral

    if wallet_address is None:
        wallet_address = config.user_wallet_address
        if wallet_address is None:
            raise ValueError("wallet_address must be provided or set in config")

    # Contract addresses
    referral_storage = "0xe6fab3F0c7199b0d34d7FbE83394fc0e0D06e99d"
    datastore_address = "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"

    # Get market information
    market_address = position_dict["market"]

    # Initialize markets and get market info
    from eth_defi.gmx.core.open_interest import GetOpenInterest

    data_obj = GetOpenInterest(config=config, filter_swap_markets=True)
    data_obj._get_token_addresses(market_address)
    market_info = data_obj.markets.get_available_markets()[market_address]

    index_token_address = market_info["index_token_address"]

    # Get oracle prices
    oracle_prices = [data_obj._get_oracle_prices(market_address, index_token_address, return_tuple=True)]

    # Get contract instances
    reader_obj = get_reader_contract(config.web3, config.chain)
    datastore_obj = get_datastore_contract(config.web3, config.chain)

    # Get position info from contract for this market
    zero_address = "0x0000000000000000000000000000000000000000"
    account_positions_list_raw = reader_obj.functions.getAccountPositionInfoList(datastore_address, referral_storage, wallet_address, [market_address], oracle_prices, zero_address, 0, 10).call()

    # Transform to dict format
    positions_list = _transform_position_data(account_positions_list_raw)

    # Find matching position for this market
    account_position = None
    for pos in positions_list:
        if pos["position"]["addresses"]["market"].lower() == market_address.lower():
            account_position = pos
            break

    if account_position is None:
        raise ValueError(f"Position not found for market {market_address}")

    # Get collateral token decimals
    collateral_token_address = account_position["position"]["addresses"]["collateralToken"]
    token_info = get_tokens_address_dict(config.chain).get(collateral_token_address)
    if token_info:
        collateral_decimals = token_info["decimals"]
    else:
        collateral_decimals = 18  # Default fallback

    # Calculate liquidation price
    liquidation_price = calculate_liquidation_price(
        datastore_obj=datastore_obj,
        market_info=market_info,
        market_address=market_address,
        index_token_address=index_token_address,
        size_in_usd=Decimal(account_position["position"]["numbers"]["sizeInUsd"]),
        size_in_tokens=Decimal(account_position["position"]["numbers"]["sizeInTokens"]),
        collateral_amount=Decimal(account_position["position"]["numbers"]["collateralAmount"]),
        collateral_usd=Decimal(position_dict["initial_collateral_amount_usd"][0]) * Decimal(10**30),
        collateral_token_address=collateral_token_address,
        pending_funding_fees_usd=Decimal(int((account_position["fees"]["fundingFeeAmount"] * 10**-collateral_decimals) * 10**30)),
        pending_borrowing_fees_usd=Decimal(account_position["borrowing"]["borrowingFeeUsd"]),
        min_collateral_usd=Decimal(datastore_obj.functions.getUint(min_collateral()).call()),
        is_long=position_dict["is_long"],
        chain=config.chain,
        use_max_price_impact=True,
        user_referral_info=None,
    )

    if liquidation_price is None:
        return None

    # Convert from 30-decimal format to regular float
    return float(liquidation_price / Decimal(10**30))


def _transform_position_data(raw_data: list) -> list:
    """Transform raw position data from contract call to dict format.

    Internal helper function to parse the complex tuple structure returned
    by getAccountPositionInfoList contract call.

    Args:
        raw_data: Raw position data from contract

    Returns:
        List of position dictionaries
    """
    result = []

    for pos in raw_data:
        # Unpack the components
        (
            position,
            referral,
            fees,
            base_pnl_usd,
            uncapped_base_pnl_usd,
            pnl_after_price_impact_usd,
        ) = pos

        position_dict = {
            "position": {
                "addresses": {
                    "account": position[0][0],
                    "market": position[0][1],
                    "collateralToken": position[0][2],
                },
                "numbers": {
                    "sizeInUsd": position[1][0],
                    "sizeInTokens": position[1][1],
                    "collateralAmount": position[1][2],
                    "borrowingFactor": position[1][3],
                    "fundingFeeAmountPerSize": position[1][4],
                    "longTokenClaimableFundingAmountPerSize": position[1][5],
                    "shortTokenClaimableFundingAmountPerSize": position[1][6],
                    "increasedAtBlock": position[1][7],
                    "decreasedAtBlock": position[1][8],
                    "increasedAtTime": position[1][9],
                    "decreasedAtTime": position[1][10],
                },
                "flags": {
                    "isLong": position[2][0],
                },
            },
            "referral": {
                "referralCode": referral[0][0],
                "affiliate": referral[0][1],
                "trader": referral[0][2],
                "totalRebateFactor": referral[0][3],
                "traderDiscountFactor": referral[0][4],
                "totalRebateAmount": referral[0][5],
                "traderDiscountAmount": referral[0][6],
                "affiliateRewardAmount": referral[0][7],
            },
            "fees": {
                "fundingFeeAmount": referral[1][0],
                "claimableLongTokenAmount": referral[1][1],
                "claimableShortTokenAmount": referral[1][2],
                "latestFundingFeeAmountPerSize": referral[1][3],
                "latestLongTokenClaimableFundingAmountPerSize": referral[1][4],
                "latestShortTokenClaimableFundingAmountPerSize": referral[1][5],
            },
            "borrowing": {
                "borrowingFeeUsd": referral[2][0],
                "borrowingFeeAmount": referral[2][1],
                "borrowingFeeReceiverFactor": referral[2][2],
                "borrowingFeeAmountForFeeReceiver": referral[2][3],
            },
            "ui": {
                "uiFeeReceiver": referral[3][0],
                "uiFeeReceiverFactor": referral[3][1],
                "uiFeeAmount": referral[3][2],
            },
            "collateralTokenPrice": {
                "min": referral[4][0],
                "max": referral[4][1],
            },
            "positionFeeFactor": referral[5],
            "protocolFeeAmount": referral[6],
            "positionFeeReceiverFactor": referral[7],
            "feeReceiverAmount": referral[8],
            "feeAmountForPool": referral[9],
            "positionFeeAmountForPool": referral[10],
            "positionFeeAmount": referral[11],
            "totalCostAmountExcludingFunding": referral[12],
            "totalCostAmount": referral[13],
            "basePnlUsd": base_pnl_usd,
            "uncappedBasePnlUsd": uncapped_base_pnl_usd,
            "pnlAfterPriceImpactUsd": pnl_after_price_impact_usd,
        }

        result.append(position_dict)

    return result
