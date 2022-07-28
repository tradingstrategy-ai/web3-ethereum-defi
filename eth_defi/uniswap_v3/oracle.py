"""Price oracle implementation for Uniswap v3 pools."""
import datetime
from dataclasses import dataclass
from decimal import Decimal

from web3 import Web3

from eth_defi.abi import get_contract
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import Filter, read_events
from eth_defi.price_oracle.oracle import PriceEntry, PriceOracle, PriceSource
from eth_defi.uniswap_v3.events import decode_swap
from eth_defi.uniswap_v3.pool import PoolDetails, fetch_pool_details


@dataclass
class UniswapV3PriceOracleContext(LogContext):
    """Hold data about tokens in in the pool"""

    pool: PoolDetails

    reverse_token_order: bool


def convert_swap_event_to_price_entry(log: dict) -> PriceEntry:
    """Create a price entry based on eth_getLogs result.

    Called by :py:func:`update_price_oracle_single_thread`.
    """

    context: UniswapV3PriceOracleContext = log["context"]

    # Check our JSON-RPC has not served us something bad
    assert log["address"] == context.pool.address.lower(), f"Got wrong source address for Swap event. Expected pool contract {context.pool.address}, got {log['address']}"

    swap_info: dict = decode_swap(log)
    timestamp = datetime.datetime.utcfromtimestamp(log["timestamp"])

    price = context.pool.convert_price_to_human(
        swap_info["tick"],
        context.reverse_token_order,
    )

    return PriceEntry(
        timestamp=timestamp,
        price=Decimal(price),
        volume=None,  # TODO: figure out the volume
        block_number=swap_info["block_number"],
        source=PriceSource.uniswap_v3_like_pool,
        pool_contract_address=swap_info["pool_contract_address"],
        block_hash=log["blockHash"],
        tx_hash=swap_info["tx_hash"],
    )


def update_price_oracle_single_thread(
    oracle: PriceOracle,
    web3: Web3,
    pool_contract_address: str,
    start_block: int,
    end_block: int,
    reverse_token_order: bool = False,
):
    """Feed price oracle data for a given block range.

    A slow single threaded implementation - suitable for testing.

    Example:

    .. code-block: python

        TODO

    :param oracle:
        Price oracle to update

    :param web3:
        Web3 connection we use to fetch event data from JSON-RPC node

    :param pool_contract_address:
        Pool contract address

    :param start_block:
        First block to include data for

    :param end_block:
        Last block to include data for (inclusive)

    :param reverse_token_order:
        If pair token0 is the quote token to calculate the price.
    """
    Pool = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")

    signatures = Pool.events.Swap.build_filter().topics
    assert len(signatures) == 1

    event_filter = Filter(
        contract_address=pool_contract_address,
        bloom=None,
        topics={
            signatures[0]: Pool.events.Swap,
        },
    )

    pool_details = fetch_pool_details(web3, pool_contract_address)

    # Feed oracle with event data from JSON-RPC node
    for log_result in read_events(
        web3,
        start_block,
        end_block,
        events=[Pool.events.Swap],
        notify=None,
        chunk_size=100,
        filter=event_filter,
        context=UniswapV3PriceOracleContext(pool_details, reverse_token_order),
    ):
        entry = convert_swap_event_to_price_entry(log_result)
        oracle.add_price_entry(entry)
