""""Analyse Uniswap v3 TVL (total value locked) and market depth."""
from decimal import Decimal

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.token import TokenDetails
from eth_defi.uniswap_v3.pool import PoolDetails


def fetch_uniswap_v3_pool_tvl(
    pool: PoolDetails,
    quote_token: TokenDetails,
    block_identifier: BlockIdentifier = None,
) -> Decimal:
    """Return the total value locked of the quote token.

    - This gets the amount of quote token lockedin the pool

    .. note ::

        This includes unclaimed fees.

    :param pool:
        Uniswap v3 pool data fully resolved.

        See :py:func:`eth_defi.uniswap_v3.pool.fetch_pool_details`

    :param quote_token:
        Which side of the pool to get.


    :param block_identifier:
        Get the historically locked value.

        You need to have an archive node to query this.

    :return:
        Amount of quote token locked in the pool.

        The US dollar TVL is this value * 2, because for the locked value
        both sides of the pool count, although this is irrelevant for trading

    """
    # No risk here, because we are not sending a transaction
    assert quote_token.address == pool.token0.address or quote_token.address == pool.token1.address
    address = Web3.to_checksum_address(pool.address)
    raw_amount = quote_token.contract.functions.balanceOf(address).call(block_identifier=block_identifier)
    return quote_token.convert_to_decimals(raw_amount)
