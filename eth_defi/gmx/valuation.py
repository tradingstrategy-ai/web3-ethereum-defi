"""GMX account valuation.

Calculate the total equity of a GMX trading account at a specific block,
combining wallet token reserves with unrealised PnL from open perpetual
positions.

- Reserves are read on-chain at the requested ``block_identifier``
- Position data (collateral, size) is read on-chain at the requested
  ``block_identifier``
- Oracle prices use the live GMX signed-prices API (not historical)
- Designed for USDC-collateralised accounts — collateral and PnL are
  both in USD terms

Example:

.. code-block:: python

    import os
    from eth_defi.provider.multi_provider import create_multi_provider_web3
    from eth_defi.token import fetch_erc20_details
    from eth_defi.gmx.valuation import fetch_gmx_total_equity

    web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
    usdc = fetch_erc20_details(web3, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    # Account 0x1640... has 9 USDC-collateralised GMX positions (mixed long/short)
    # and ~$978K USDC wallet reserves at block 401_729_535.
    result = fetch_gmx_total_equity(
        web3=web3,
        account="0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c",
        reserve_tokens=[usdc],
        block_identifier=401_729_535,
    )
    # result.reserves ≈ Decimal("978163.29")  (deterministic at this block)
    # result.positions ≈ Decimal("600000")    (collateral ~$272K + PnL, oracle-price dependent)
    # result.get_total() ≈ Decimal("1578000") (reserves + positions)
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import BlockIdentifier, HexAddress
from eth_utils import to_checksum_address
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


@dataclass(slots=True)
class GMXEquity:
    """Equity breakdown for a GMX trading account.

    See :py:func:`fetch_gmx_total_equity`.
    """

    #: Sum of ERC-20 reserve token balances (e.g. USDC in the wallet)
    reserves: Decimal

    #: Sum of open GMX position values (collateral + unrealised PnL)
    positions: Decimal

    def get_total(self) -> Decimal:
        """Total equity = reserves + positions."""
        return self.reserves + self.positions


def fetch_gmx_total_equity(
    web3: Web3,
    account: HexAddress | str,
    reserve_tokens: list[TokenDetails],
    block_identifier: BlockIdentifier = "latest",
    chain: str = "arbitrum",
) -> GMXEquity:
    """Calculate the total equity of a GMX trading account.

    Returns a :class:`GMXEquity` dataclass with separate ``reserves``
    and ``positions`` subtotals.  Call :meth:`GMXEquity.get_total` for
    the combined figure.

    Designed for USDC-collateralised accounts where both collateral
    amounts and PnL are in USD terms.  Reserve token balances are
    summed directly — the caller controls which tokens to include.

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

    :param reserve_tokens:
        List of ``TokenDetails`` whose ``balanceOf(account)`` should be
        included in the reserve total.  Must be USD-pegged stablecoins
        (e.g. USDC) — an assertion fires if a non-stablecoin token is
        passed (checked via :meth:`~eth_defi.token.TokenDetails.is_stablecoin_like`).

    :param block_identifier:
        Block number (or ``"latest"``) at which to read on-chain state.

    :param chain:
        GMX chain name (``"arbitrum"``, ``"avalanche"``).

    :return:
        :class:`GMXEquity` with reserves and positions subtotals.
    """
    account = Web3.to_checksum_address(account)

    # 1. Reserve balances
    reserves_total = Decimal(0)
    for token in reserve_tokens:
        assert token.is_stablecoin_like(), f"Reserve token {token.symbol} does not look like a stablecoin. Non-stablecoin reserves would produce mixed-unit totals."
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
        block_identifier=block_identifier,
        chain=chain,
    )

    logger.info(
        "Total equity for %s: reserves=%s, positions=%s, total=%s",
        account,
        reserves_total,
        positions_total,
        reserves_total + positions_total,
    )
    return GMXEquity(reserves=reserves_total, positions=positions_total)


#: wstETH GMX market uses a zero index-token address on-chain;
#: the real wstETH token must be substituted.
#: See :class:`~eth_defi.gmx.core.markets.Markets` for the canonical handling.
_WSTETH_MARKET = to_checksum_address("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5")
_WSTETH_TOKEN = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _fetch_market_index_tokens(
    reader,
    datastore_address: str,
    block_identifier: BlockIdentifier,
) -> dict[str, str]:
    """Build a market_address → index_token_address mapping from on-chain data.

    Calls ``Reader.getMarkets()`` directly — the same RPC call that
    :class:`~eth_defi.gmx.core.markets.Markets` uses internally, but
    without requiring a ``GMXConfig`` object.

    Handles the wstETH special case where the on-chain index token is
    the zero address and must be replaced with the real wstETH token.

    :return:
        Dict mapping checksummed market address to checksummed index token address.
    """
    raw_markets = reader.functions.getMarkets(datastore_address, 0, 115).call(block_identifier=block_identifier)

    market_to_index: dict[str, str] = {}
    for raw_market in raw_markets:
        market_addr = to_checksum_address(raw_market[0])
        index_token = to_checksum_address(raw_market[1])

        # wstETH market has zero index token on-chain — substitute real address
        if index_token == _ZERO_ADDRESS:
            if market_addr == _WSTETH_MARKET:
                index_token = _WSTETH_TOKEN
            else:
                continue

        market_to_index[market_addr] = index_token

    return market_to_index


def _fetch_gmx_positions_value(
    web3: Web3,
    account: HexAddress,
    block_identifier: BlockIdentifier,
    chain: str,
) -> Decimal:
    """Read all open GMX positions and calculate their total value.

    Value per position = collateral + unrealised PnL (both in USD terms).

    :return:
        Sum of all position values.
    """
    reader = get_reader_contract(web3, chain)
    addresses = get_contract_addresses(chain)

    raw_positions = reader.functions.getAccountPositions(addresses.datastore, account, 0, 100).call(block_identifier=block_identifier)

    if not raw_positions:
        logger.info("No open GMX positions for %s", account)
        return Decimal(0)

    # Fetch market→index token mapping, token metadata, and oracle prices once
    market_to_index = _fetch_market_index_tokens(reader, addresses.datastore, block_identifier)
    chain_tokens = get_tokens_metadata_dict(chain)
    oracle = OraclePrices(chain=chain)
    oracle_prices = oracle.get_recent_prices()

    positions_total = Decimal(0)

    for raw_position in raw_positions:
        try:
            value = _calculate_position_value(
                raw_position=raw_position,
                chain_tokens=chain_tokens,
                oracle_prices=oracle_prices,
                market_to_index=market_to_index,
            )
            positions_total += value
        except Exception as e:
            logger.warning("Failed to value position: %s", e)
            continue

    return positions_total


def _calculate_position_value(
    raw_position: tuple,
    chain_tokens: dict,
    oracle_prices: dict,
    market_to_index: dict[str, str],
) -> Decimal:
    """Calculate the value of a single GMX position.

    :return:
        Position value (collateral + unrealised PnL) in USD terms.
    """
    # Unpack raw position structure:
    # raw_position[0] = Addresses (account, market, collateralToken)
    # raw_position[1] = Numbers (sizeInUsd, sizeInTokens, collateralAmount, ...)
    # raw_position[2] = Flags (isLong,)
    market_address = to_checksum_address(raw_position[0][1])
    collateral_token_address = to_checksum_address(raw_position[0][2])
    size_in_usd = raw_position[1][0]  # 30-decimal precision
    size_in_tokens = raw_position[1][1]
    collateral_amount_raw = raw_position[1][2]
    is_long = raw_position[2][0]

    # Get collateral token decimals
    if collateral_token_address not in chain_tokens:
        raise KeyError(f"Collateral token {collateral_token_address} not found in token metadata")
    collateral_decimals = chain_tokens[collateral_token_address]["decimals"]

    # Convert collateral to decimal
    collateral_amount = Decimal(collateral_amount_raw) / Decimal(10**collateral_decimals)

    # If position has no size, just return collateral
    if size_in_usd == 0 or size_in_tokens == 0:
        return collateral_amount

    # Resolve index token from on-chain market data
    index_token_address = market_to_index.get(market_address)
    if index_token_address is None:
        logger.warning("Market %s not found in on-chain markets, returning collateral only", market_address)
        return collateral_amount

    if index_token_address in chain_tokens:
        index_token_decimals = chain_tokens[index_token_address]["decimals"]
    else:
        # Fallback: assume 18 decimals (ETH, most tokens)
        index_token_decimals = 18

    # Calculate entry price (both values at 30-decimal precision)
    entry_price = (Decimal(size_in_usd) / Decimal(size_in_tokens)) / Decimal(10 ** (PRECISION - index_token_decimals))

    # Get mark price from oracle
    mark_price = _get_mark_price(
        oracle_prices=oracle_prices,
        index_token_address=index_token_address,
        index_token_decimals=index_token_decimals,
    )

    if mark_price is None:
        logger.warning("No oracle price for index token %s, returning collateral only", index_token_address)
        return collateral_amount

    # Calculate unrealised PnL in USD
    size_in_tokens_decimal = Decimal(size_in_tokens) / Decimal(10**index_token_decimals)

    if is_long:
        pnl_usd = (mark_price - entry_price) * size_in_tokens_decimal
    else:
        pnl_usd = (entry_price - mark_price) * size_in_tokens_decimal

    position_value = collateral_amount + pnl_usd

    logger.info(
        "Position market=%s is_long=%s collateral=%s entry=%s mark=%s pnl=%s value=%s",
        market_address,
        is_long,
        collateral_amount,
        entry_price,
        mark_price,
        pnl_usd,
        position_value,
    )

    return position_value


def _get_mark_price(
    oracle_prices: dict,
    index_token_address: str,
    index_token_decimals: int,
) -> Decimal | None:
    """Get the current mark price for a token from GMX oracle data.

    :return:
        Mark price as :class:`~decimal.Decimal`, or ``None`` if unavailable.
    """
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
