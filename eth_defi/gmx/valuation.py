"""GMX account valuation.

Calculate the total equity of a GMX trading account at a specific block,
combining wallet token reserves with unrealised PnL from open perpetual
positions.

- Reserves are read on-chain at the requested ``block_identifier``
- Position data (collateral, size) is read on-chain at the requested
  ``block_identifier``
- Oracle prices use the live GMX signed-prices API (not historical)

Example:

.. code-block:: python

    from eth_defi.provider.multi_provider import create_multi_provider_web3
    from eth_defi.token import fetch_erc20_details
    from eth_defi.gmx.valuation import fetch_gmx_total_equity

    web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
    usdc = fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    equity = fetch_gmx_total_equity(
        web3=web3,
        account="0x...",
        denomination_token=usdc,
        reserve_tokens=[usdc],
        block_identifier=280_000_000,
    )
"""

import logging
from decimal import Decimal

import numpy as np
from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.gmx.constants import PRECISION
from eth_defi.gmx.contracts import (
    get_contract_addresses,
    get_reader_contract,
    get_tokens_metadata_dict,
)
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.token import TokenDetails

logger = logging.getLogger(__name__)


def fetch_gmx_total_equity(
    web3: Web3,
    account: HexAddress | str,
    denomination_token: TokenDetails,
    reserve_tokens: list[TokenDetails],
    block_identifier: BlockIdentifier = "latest",
    chain: str = "arbitrum",
) -> Decimal:
    """Calculate the total equity of a GMX trading account.

    Total equity = wallet reserve balances + GMX position values
    (collateral + unrealised PnL).

    - Reserve balances and position data are read on-chain at the given
      ``block_identifier`` (requires an archive node for historical blocks).
    - Oracle prices are fetched from the live GMX signed-prices API and
      therefore reflect *current* market prices, not prices at the
      historical block.

    :param web3:
        Web3 connection.  Must point to an archive node when querying
        historical blocks.

    :param account:
        Wallet address that holds the reserves and GMX positions.

    :param denomination_token:
        The token in which equity is denominated (e.g. USDC).
        Used to convert collateral amounts to a common unit.

    :param reserve_tokens:
        List of ``TokenDetails`` whose ``balanceOf(account)`` should be
        included in the reserve total.

    :param block_identifier:
        Block number (or ``"latest"``) at which to read on-chain state.

    :param chain:
        GMX chain name (``"arbitrum"``, ``"avalanche"``).

    :return:
        Total equity as a :class:`~decimal.Decimal` in denomination-token
        units (e.g. ``Decimal("15234.56")`` for $15 234.56 USDC).
    """
    account = Web3.to_checksum_address(account)

    # 1. Reserve balances
    reserves_total = Decimal(0)
    for token in reserve_tokens:
        balance = token.fetch_balance_of(account, block_identifier=block_identifier)
        reserves_total += balance
        logger.info(
            "Reserve %s balance for %s: %s",
            token.symbol,
            account,
            balance,
        )

    # 2. GMX positions
    positions_total = _fetch_gmx_positions_value(
        web3=web3,
        account=account,
        denomination_token=denomination_token,
        block_identifier=block_identifier,
        chain=chain,
    )

    total_equity = reserves_total + positions_total
    logger.info(
        "Total equity for %s: reserves=%s, positions=%s, total=%s",
        account,
        reserves_total,
        positions_total,
        total_equity,
    )
    return total_equity


def _fetch_gmx_positions_value(
    web3: Web3,
    account: HexAddress,
    denomination_token: TokenDetails,
    block_identifier: BlockIdentifier,
    chain: str,
) -> Decimal:
    """Read all open GMX positions and calculate their total value.

    Value per position = collateral (in denomination token) + unrealised PnL.

    :return:
        Sum of all position values in denomination-token units.
    """
    reader = get_reader_contract(web3, chain)
    addresses = get_contract_addresses(chain)

    raw_positions = reader.functions.getAccountPositions(addresses.datastore, account, 0, 100).call(block_identifier=block_identifier)

    if not raw_positions:
        logger.info("No open GMX positions for %s", account)
        return Decimal(0)

    # Fetch token metadata and oracle prices once for all positions
    chain_tokens = get_tokens_metadata_dict(chain)
    oracle = OraclePrices(chain=chain)
    oracle_prices = oracle.get_recent_prices()

    positions_total = Decimal(0)

    for raw_position in raw_positions:
        try:
            value = _calculate_position_value(
                raw_position=raw_position,
                denomination_token=denomination_token,
                chain_tokens=chain_tokens,
                oracle_prices=oracle_prices,
            )
            positions_total += value
        except Exception as e:
            logger.warning("Failed to value position: %s", e)
            continue

    return positions_total


def _calculate_position_value(
    raw_position: tuple,
    denomination_token: TokenDetails,
    chain_tokens: dict,
    oracle_prices: dict,
) -> Decimal:
    """Calculate the value of a single GMX position.

    :return:
        Position value (collateral + unrealised PnL) in denomination-token units.
    """
    # Unpack raw position structure:
    # raw_position[0] = Addresses (account, market, collateralToken)
    # raw_position[1] = Numbers (sizeInUsd, sizeInTokens, collateralAmount, ...)
    # raw_position[2] = Flags (isLong,)
    collateral_token_address = raw_position[0][2]
    size_in_usd = raw_position[1][0]  # 30-decimal precision
    size_in_tokens = raw_position[1][1]
    collateral_amount_raw = raw_position[1][2]
    is_long = raw_position[2][0]

    # Get collateral token decimals
    collateral_token_address_cs = Web3.to_checksum_address(collateral_token_address)
    if collateral_token_address_cs not in chain_tokens:
        raise KeyError(f"Collateral token {collateral_token_address_cs} not found in token metadata")
    collateral_decimals = chain_tokens[collateral_token_address_cs]["decimals"]

    # Convert collateral to decimal
    collateral_amount = Decimal(collateral_amount_raw) / Decimal(10**collateral_decimals)

    # If position has no size, just return collateral
    if size_in_usd == 0 or size_in_tokens == 0:
        return collateral_amount

    # Calculate entry price from position data (both at 30-decimal precision)
    # entry_price = sizeInUsd / sizeInTokens, then adjust for token decimals
    # Get index token info from the market
    # For the index token, we need to find it from the market info
    # The market address is raw_position[0][1]
    market_address = Web3.to_checksum_address(raw_position[0][1])

    # Find index token for this market from GMX markets API
    index_token_address = _find_index_token_for_market(market_address, chain_tokens, oracle_prices)

    if index_token_address and index_token_address in chain_tokens:
        index_token_decimals = chain_tokens[index_token_address]["decimals"]
    else:
        # Fallback: assume 18 decimals (ETH, most tokens)
        index_token_decimals = 18

    entry_price = (Decimal(size_in_usd) / Decimal(size_in_tokens)) / Decimal(10 ** (PRECISION - index_token_decimals))

    # Get mark price from oracle
    mark_price = _get_mark_price(
        oracle_prices=oracle_prices,
        index_token_address=index_token_address,
        index_token_decimals=index_token_decimals,
    )

    if mark_price is None:
        # Cannot determine mark price — return just collateral
        logger.warning("No oracle price for index token %s, returning collateral only", index_token_address)
        return collateral_amount

    # Calculate unrealised PnL in USD
    size_in_tokens_decimal = Decimal(size_in_tokens) / Decimal(10**index_token_decimals)

    if is_long:
        pnl_usd = (mark_price - entry_price) * size_in_tokens_decimal
    else:
        pnl_usd = (entry_price - mark_price) * size_in_tokens_decimal

    # Position value = collateral + PnL
    # Both collateral and PnL are in USD terms when collateral is USDC
    position_value = collateral_amount + pnl_usd

    logger.info(
        "Position market=%s is_long=%s collateral=%s entry_price=%s mark_price=%s pnl=%s value=%s",
        market_address,
        is_long,
        collateral_amount,
        entry_price,
        mark_price,
        pnl_usd,
        position_value,
    )

    return position_value


def _find_index_token_for_market(
    market_address: str,
    chain_tokens: dict,
    oracle_prices: dict,
) -> str | None:
    """Try to find the index token address for a GMX market.

    Uses the GMX markets API to resolve market → index token mapping.

    :return:
        Index token address (checksummed) or ``None`` if not found.
    """
    try:
        from eth_defi.gmx.core.markets import Markets

        markets = Markets(chain="arbitrum")
        available = markets.get_available_markets()

        if market_address in available:
            return available[market_address].get("index_token_address")
    except Exception as e:
        logger.warning("Failed to resolve index token for market %s: %s", market_address, e)

    return None


def _get_mark_price(
    oracle_prices: dict,
    index_token_address: str | None,
    index_token_decimals: int,
) -> Decimal | None:
    """Get the current mark price for a token from GMX oracle data.

    :return:
        Mark price as :class:`~decimal.Decimal`, or ``None`` if unavailable.
    """
    if index_token_address is None:
        return None

    # Case-insensitive lookup
    price_data = None
    for addr, data in oracle_prices.items():
        if addr.lower() == index_token_address.lower():
            price_data = data
            break

    if price_data is None:
        return None

    if "maxPriceFull" not in price_data or "minPriceFull" not in price_data:
        return None

    # Mid price from oracle min/max
    mid_price = (Decimal(price_data["maxPriceFull"]) + Decimal(price_data["minPriceFull"])) / Decimal(2)

    # Convert from 30-decimal precision, adjusting for token decimals
    mark_price = mid_price / Decimal(10 ** (PRECISION - index_token_decimals))

    return mark_price
